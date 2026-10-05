"""SQL and write helpers for admin crisis CRUD. FastAPI-free."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.areas import AreaNotFoundError
from api.buildings.crisis_polygon_resolver import (
    CrisisPolygonResolver,
    ResolvedGeometry,
)
from api.crises.default_form import (
    DEFAULT_FORM_SCHEMA,
    DEFAULT_FORM_VERSION,
)
from api.schemas.crises import (
    CrisisAdminListItem,
    CrisisCreatedResponse,
    CrisisCreatePayload,
    CrisisPatchPayload,
    check_scheduling_inputs,
)

# Shared by every INSERT/UPDATE/SELECT that returns the detail shape.
DETAIL_COLUMNS = (
    "id, name, status, created_at, "
    "geometry is not null as has_geometry, "
    "type, countries, started_at, ended_at, "
    "activate_at, activate_on_ingest_success, "
    "public_visibility, heatmap_k_anonymity, public_infra_types"
)


class UnknownDivisionError(Exception):
    """Unknown `division_id`; the route maps it to 400."""

    def __init__(self, original: AreaNotFoundError) -> None:
        super().__init__(str(original))
        self.original = original


async def resolve_create_geometry(
    resolver: CrisisPolygonResolver, payload: CrisisCreatePayload
) -> ResolvedGeometry:
    try:
        return await resolver.resolve(geometry=payload.geometry, countries=payload.countries)
    except AreaNotFoundError as exc:
        raise UnknownDivisionError(exc) from exc


async def resolve_patch_geometry(
    resolver: CrisisPolygonResolver, payload: CrisisPatchPayload
) -> bytes | None:
    """Resolve the patch geometry to WKB; None when the caller clears the polygon."""
    if payload.geometry is None:
        return None
    try:
        resolved = await resolver.resolve(geometry=payload.geometry, countries=None)
    except AreaNotFoundError as exc:
        raise UnknownDivisionError(exc) from exc
    return resolved.wkb


def resolve_countries(payload: CrisisCreatePayload, resolved: ResolvedGeometry) -> list[str]:
    """Explicit `countries` (even `[]`) wins; otherwise seed from the resolved divisions."""
    if payload.countries is not None:
        return payload.countries
    return resolved.auto_seed_countries


async def insert_crisis(
    session: AsyncSession,
    *,
    payload: CrisisCreatePayload,
    countries: list[str],
    geometry_wkb: bytes | None,
) -> Any:
    """Insert a crisis plus its initial form version; return the detail-shaped row."""
    params: dict[str, object] = {
        "name": payload.name,
        "status": payload.status,
        "type": payload.type,
        "countries": countries,
        "started_at": payload.started_at,
        "ended_at": payload.ended_at,
        "activate_at": payload.activate_at,
        "activate_on_ingest_success": payload.activate_on_ingest_success,
        "public_visibility": payload.public_visibility,
        "heatmap_k_anonymity": payload.heatmap_k_anonymity,
        "public_infra_types": payload.public_infra_types,
        "form_version": DEFAULT_FORM_VERSION,
        "form_schema": json.dumps(
            payload.form_schema if payload.form_schema is not None else DEFAULT_FORM_SCHEMA
        ),
    }
    returning = f"returning {DETAIL_COLUMNS}"

    if geometry_wkb is None:
        row = (
            await session.execute(
                text(
                    "insert into public.crises "
                    "  (name, status, type, countries, started_at, ended_at, "
                    "   activate_at, activate_on_ingest_success, "
                    "   public_visibility, heatmap_k_anonymity, public_infra_types, "
                    "   form_version, form_schema) "
                    "values (:name, :status, :type, :countries, "
                    "        :started_at, :ended_at, "
                    "        :activate_at, :activate_on_ingest_success, "
                    "        :public_visibility, :heatmap_k_anonymity, "
                    "        cast(:public_infra_types as text[]), "
                    "        :form_version, cast(:form_schema as jsonb)) " + returning
                ),
                params,
            )
        ).first()
    else:
        row = (
            await session.execute(
                text(
                    "insert into public.crises "
                    "  (name, status, geometry, type, countries, "
                    "   started_at, ended_at, "
                    "   activate_at, activate_on_ingest_success, "
                    "   public_visibility, heatmap_k_anonymity, public_infra_types, "
                    "   form_version, form_schema) "
                    "values (:name, :status, "
                    "  st_multi(st_geomfromwkb(:wkb, 4326))::geography, "
                    "  :type, :countries, :started_at, :ended_at, "
                    "  :activate_at, :activate_on_ingest_success, "
                    "  :public_visibility, :heatmap_k_anonymity, "
                    "  cast(:public_infra_types as text[]), "
                    "  :form_version, cast(:form_schema as jsonb)) " + returning
                ),
                {**params, "wkb": geometry_wkb},
            )
        ).first()

    assert row is not None
    await session.execute(
        text(
            "insert into public.crisis_form_versions (crisis_id, version, schema) "
            "values (:crisis_id, :version, cast(:schema as jsonb))"
        ),
        {
            "crisis_id": str(row.id),
            "version": DEFAULT_FORM_VERSION,
            "schema": json.dumps(DEFAULT_FORM_SCHEMA),
        },
    )
    return row


def apply_scheduling_rules(
    *,
    payload: CrisisPatchPayload,
    updates: dict[str, Any],
    current_status: str,
    geometry_after_patch: bool,
) -> None:
    """Validate and normalise scheduling fields on PATCH, mutating `updates`.

    Scheduling is checked against the persisted status when the payload omits
    `status`. A manual status change clears any pending schedule unless the same
    payload replaces it.
    """
    set_fields = payload.model_fields_set
    schedule_in_payload = "activate_at" in set_fields or "activate_on_ingest_success" in set_fields

    if schedule_in_payload:
        effective_status: str = (
            payload.status
            if payload.status is not None and "status" in set_fields
            else current_status
        )
        check_scheduling_inputs(
            activate_at=payload.activate_at,
            activate_on_ingest_success=payload.activate_on_ingest_success,
            target_status=effective_status,  # pyright: ignore[reportArgumentType]
            geometry_present=geometry_after_patch,
        )
        # The two schedule modes are exclusive (crises_activation_mode_chk); reset
        # the one the payload didn't mention.
        if (
            "activate_at" in set_fields
            and payload.activate_at is not None
            and "activate_on_ingest_success" not in set_fields
        ):
            updates["activate_on_ingest_success"] = False
        if (
            "activate_on_ingest_success" in set_fields
            and payload.activate_on_ingest_success
            and "activate_at" not in set_fields
        ):
            updates["activate_at"] = None

    if "status" in set_fields:
        new_status = payload.status
        if new_status != "inactive":
            updates["activate_at"] = None
            updates["activate_on_ingest_success"] = False
        elif not schedule_in_payload:
            # Drop a stale schedule left over from before a round-trip through active.
            updates["activate_at"] = None
            updates["activate_on_ingest_success"] = False


def apply_archival_clock(
    *,
    payload: CrisisPatchPayload,
    updates: dict[str, Any],
    current_status: str,
) -> None:
    """Set `archived_at` on entering archived and clear it on leaving, mutating `updates`.

    Re-archiving keeps the original timestamp so the retention window doesn't
    restart. This is the only writer of `archived_at`; clients cannot set it.
    """
    if "status" not in payload.model_fields_set or payload.status is None:
        return
    new_status = payload.status
    if new_status == "archived" and current_status != "archived":
        updates["archived_at"] = datetime.now(UTC)
    elif new_status != "archived" and current_status == "archived":
        updates["archived_at"] = None


_TEXT_ARRAY_COLUMNS = frozenset({"public_infra_types"})


def _placeholder_for(column: str) -> str:
    # Postgres can't infer the type of a text[] bind param from asyncpg; cast explicitly.
    if column in _TEXT_ARRAY_COLUMNS:
        return f"cast(:{column} as text[])"
    return f":{column}"


async def update_with_geometry_invalidation(
    session: AsyncSession,
    crisis_id: uuid.UUID,
    other_updates: dict[str, Any],
    geometry_wkb: bytes | None,
) -> Any:
    """Rewrite `geometry` and invalidate the building tiles in one UPDATE.

    A separate statement would leave a window where the map serves stale
    buildings for the new polygon.
    """
    geom_expr = (
        "st_multi(st_geomfromwkb(:geom_wkb, 4326))::geography"
        if geometry_wkb is not None
        else "NULL"
    )
    invalidation = (
        "pmtiles_url = NULL, buildings_ingested_at = NULL, buildings_ingested_count = NULL"
    )

    other_assignments = ", ".join(f"{col} = {_placeholder_for(col)}" for col in other_updates)
    head = f"geometry = {geom_expr}, {invalidation}"
    set_clause = f"{head}, {other_assignments}" if other_updates else head

    params: dict[str, Any] = {**other_updates, "id": str(crisis_id)}
    if geometry_wkb is not None:
        params["geom_wkb"] = geometry_wkb

    return (
        await session.execute(
            text(
                f"update public.crises set {set_clause} where id = :id returning {DETAIL_COLUMNS}"
            ),
            params,
        )
    ).first()


async def update_plain(session: AsyncSession, crisis_id: uuid.UUID, updates: dict[str, Any]) -> Any:
    """UPDATE for non-geometry fields. `updates` must be non-empty."""
    assignments = ", ".join(f"{col} = {_placeholder_for(col)}" for col in updates)
    params: dict[str, Any] = {**updates, "id": str(crisis_id)}
    return (
        await session.execute(
            text(
                f"update public.crises set {assignments} where id = :id returning {DETAIL_COLUMNS}"
            ),
            params,
        )
    ).first()


async def list_admin_crises(session: AsyncSession) -> list[CrisisAdminListItem]:
    """All crises, newest first, including the tile and release columns."""
    result = await session.execute(
        text(
            "select id, name, status, type, countries, "
            "       started_at, ended_at, created_at, "
            "       geometry is not null as has_geometry, "
            "       st_xmin(geometry::geometry) as bbox_w, "
            "       st_ymin(geometry::geometry) as bbox_s, "
            "       st_xmax(geometry::geometry) as bbox_e, "
            "       st_ymax(geometry::geometry) as bbox_n, "
            "       pmtiles_url, overture_release_pinned, "
            "       buildings_ingested_at, buildings_ingested_count, "
            "       activate_at, activate_on_ingest_success, "
            "       public_visibility, heatmap_k_anonymity, public_infra_types "
            "from public.crises "
            "order by created_at desc"
        )
    )
    rows = result.all()

    return [
        CrisisAdminListItem(
            id=_as_uuid(r.id),
            name=r.name,
            status=r.status,
            type=r.type,
            countries=list(r.countries),
            started_at=r.started_at,
            ended_at=r.ended_at,
            created_at=r.created_at,
            has_geometry=bool(r.has_geometry),
            bbox=(
                (float(r.bbox_w), float(r.bbox_s), float(r.bbox_e), float(r.bbox_n))
                if r.bbox_w is not None
                else None
            ),
            pmtiles_url=r.pmtiles_url,
            overture_release_pinned=r.overture_release_pinned,
            buildings_ingested_at=r.buildings_ingested_at,
            buildings_ingested_count=(
                int(r.buildings_ingested_count) if r.buildings_ingested_count is not None else None
            ),
            activate_at=r.activate_at,
            activate_on_ingest_success=bool(r.activate_on_ingest_success),
            public_visibility=r.public_visibility,
            heatmap_k_anonymity=int(r.heatmap_k_anonymity),
            public_infra_types=(
                list(r.public_infra_types) if r.public_infra_types is not None else None
            ),
        )
        for r in rows
    ]


async def fetch_detail(session: AsyncSession, crisis_id: uuid.UUID) -> Any:
    return (
        await session.execute(
            text(f"select {DETAIL_COLUMNS} from public.crises where id = :id"),
            {"id": str(crisis_id)},
        )
    ).first()


async def fetch_geometry_geojson(session: AsyncSession, crisis_id: uuid.UUID) -> Any:
    """None when the crisis is missing; `row.geojson` is NULL when it has no geometry."""
    return (
        await session.execute(
            text(
                "select st_asgeojson(geometry::geometry) as geojson "
                "from public.crises where id = :id"
            ),
            {"id": str(crisis_id)},
        )
    ).first()


async def fetch_status_and_geometry(session: AsyncSession, crisis_id: uuid.UUID) -> Any:
    return (
        await session.execute(
            text(
                "select status, "
                "       geometry is not null as has_geometry "
                "from public.crises where id = :id"
            ),
            {"id": str(crisis_id)},
        )
    ).first()


async def ingest_job_exists(session: AsyncSession, crisis_id: uuid.UUID) -> bool:
    """True if an ingest job was ever queued, in any state.

    A failed job counts: activate_on_ingest_success fires on the next success.
    """
    row = (
        await session.execute(
            text(
                "select 1 from public.crisis_jobs "
                "where crisis_id = :id and job_type = 'ingest_buildings'"
            ),
            {"id": str(crisis_id)},
        )
    ).first()
    return row is not None


def row_to_detail(row: Any) -> CrisisCreatedResponse:
    return CrisisCreatedResponse(
        id=_as_uuid(row.id),
        name=row.name,
        status=row.status,
        created_at=row.created_at,
        has_geometry=bool(row.has_geometry),
        type=row.type,
        countries=list(row.countries),
        started_at=row.started_at,
        ended_at=row.ended_at,
        activate_at=row.activate_at,
        activate_on_ingest_success=bool(row.activate_on_ingest_success),
        public_visibility=row.public_visibility,
        heatmap_k_anonymity=int(row.heatmap_k_anonymity),
        public_infra_types=(
            list(row.public_infra_types) if row.public_infra_types is not None else None
        ),
    )


def _as_uuid(value: object) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
