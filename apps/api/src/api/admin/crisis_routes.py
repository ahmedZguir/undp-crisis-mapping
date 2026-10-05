"""Admin crisis create, update and list.

PATCH is the only writer of `status`; the ingest worker never sets it. SQL and
scheduling rules live in `api.admin.crisis_writer`.
"""

from __future__ import annotations

import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import IntegrityError

from api.admin.crisis_writer import (
    UnknownDivisionError,
    apply_archival_clock,
    apply_scheduling_rules,
    fetch_detail,
    fetch_geometry_geojson,
    fetch_status_and_geometry,
    ingest_job_exists,
    insert_crisis,
    list_admin_crises,
    resolve_countries,
    resolve_create_geometry,
    resolve_patch_geometry,
    row_to_detail,
    update_plain,
    update_with_geometry_invalidation,
)
from api.areas import PostgresOvertureDivisionsReader
from api.buildings.crisis_polygon_resolver import CrisisPolygonResolver
from api.core.app_state import AppState, get_app_state
from api.core.rate_limit import limit
from api.forms.validation import validate_form_schema
from api.schemas.areas import AreaGeometryResponse
from api.schemas.crises import (
    CrisisAdminListItem,
    CrisisCreatedResponse,
    CrisisCreatePayload,
    CrisisPatchPayload,
)

router = APIRouter(prefix="/crises")

# A duplicate crisis name becomes a 409; matching on the index name keeps other
# unique violations from being mislabelled.
_UNIQUE_VIOLATION = "23505"
_CRISIS_NAME_INDEX = "crises_name_uniq"


def _name_conflict(exc: IntegrityError, name: str) -> HTTPException:
    """409 for a duplicate crisis name; re-raise anything else unchanged."""
    sqlstate = getattr(exc.orig, "sqlstate", None)
    if sqlstate == _UNIQUE_VIOLATION and _CRISIS_NAME_INDEX in str(exc.orig):
        return HTTPException(
            status_code=409,
            detail=f'a crisis named "{name}" already exists',
        )
    raise exc


def get_crisis_polygon_resolver(
    state: Annotated[AppState, Depends(get_app_state)],
) -> CrisisPolygonResolver:
    return CrisisPolygonResolver(reader=PostgresOvertureDivisionsReader(state.sessionmaker))


@router.post("", response_model=CrisisCreatedResponse, status_code=201)
@limit("30/minute")
async def create_crisis(
    request: Request,
    payload: CrisisCreatePayload,
    resolver: Annotated[CrisisPolygonResolver, Depends(get_crisis_polygon_resolver)],
    state: Annotated[AppState, Depends(get_app_state)],
) -> CrisisCreatedResponse:
    if payload.form_schema is not None:
        errors = validate_form_schema(payload.form_schema)
        if errors:
            raise HTTPException(status_code=422, detail={"errors": errors})

    try:
        resolved = await resolve_create_geometry(resolver, payload)
    except UnknownDivisionError as exc:
        raise HTTPException(status_code=400, detail=f"unknown division_id: {exc}") from exc

    countries = resolve_countries(payload, resolved)

    try:
        async with state.sessionmaker() as session, session.begin():
            row = await insert_crisis(
                session,
                payload=payload,
                countries=countries,
                geometry_wkb=resolved.wkb,
            )
    except IntegrityError as exc:
        raise _name_conflict(exc, payload.name) from exc

    assert row is not None
    return row_to_detail(row)


@router.patch("/{crisis_id}", response_model=CrisisCreatedResponse)
@limit("30/minute")
async def patch_crisis(
    request: Request,
    crisis_id: uuid.UUID,
    payload: CrisisPatchPayload,
    resolver: Annotated[CrisisPolygonResolver, Depends(get_crisis_polygon_resolver)],
    state: Annotated[AppState, Depends(get_app_state)],
) -> CrisisCreatedResponse:
    """Partial update.

    Writing `geometry` also nulls `pmtiles_url` and `buildings_ingested_at` in the
    same UPDATE so the map never serves stale buildings for a new polygon.
    """
    updates = payload.model_dump(exclude_unset=True)
    updates.pop("geometry", None)  # handled out-of-band below

    # Resolve before opening the transaction. A None geometry clears the polygon
    # and still triggers invalidation.
    geometry_wkb: bytes | None = None
    if "geometry" in payload.model_fields_set and payload.geometry is not None:
        try:
            geometry_wkb = await resolve_patch_geometry(resolver, payload)
        except UnknownDivisionError as exc:
            raise HTTPException(status_code=400, detail=f"unknown division_id: {exc}") from exc

    try:
        async with state.sessionmaker() as session, session.begin():
            current = await fetch_status_and_geometry(session, crisis_id)
            if current is None:
                raise HTTPException(status_code=404, detail="crisis not found")

            try:
                apply_scheduling_rules(
                    payload=payload,
                    updates=updates,
                    current_status=current.status,
                    # Scheduling checks see the geometry as it will be after this PATCH.
                    geometry_after_patch=(
                        payload.geometry is not None
                        if "geometry" in payload.model_fields_set
                        else bool(current.has_geometry)
                    ),
                )
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc

            # Stamp or clear the retention clock on archive transitions.
            apply_archival_clock(
                payload=payload,
                updates=updates,
                current_status=current.status,
            )

            if (
                "activate_on_ingest_success" in payload.model_fields_set
                and payload.activate_on_ingest_success
                and not await ingest_job_exists(session, crisis_id)
            ):
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "activate_on_ingest_success requires an ingest job; "
                        "trigger ingest first via POST /admin/crises/{id}/ingest_buildings."
                    ),
                )

            if "geometry" in payload.model_fields_set:
                row = await update_with_geometry_invalidation(
                    session, crisis_id, updates, geometry_wkb
                )
            elif updates:
                row = await update_plain(session, crisis_id, updates)
            else:
                row = await fetch_detail(session, crisis_id)
    except IntegrityError as exc:
        raise _name_conflict(exc, updates.get("name", "")) from exc

    if row is None:
        raise HTTPException(status_code=404, detail="crisis not found")

    # Make a status change visible to public heatmap gates immediately, not after the cache TTL.
    await state.crisis_row_cache.invalidate(str(crisis_id))

    return row_to_detail(row)


@router.get("/{crisis_id}/geometry", response_model=AreaGeometryResponse)
async def get_crisis_geometry(
    crisis_id: uuid.UUID,
    state: Annotated[AppState, Depends(get_app_state)],
) -> AreaGeometryResponse:
    """Saved crisis polygon as GeoJSON. 404 when `geometry` is NULL."""
    async with state.sessionmaker() as session:
        row = await fetch_geometry_geojson(session, crisis_id)
    if row is None:
        raise HTTPException(status_code=404, detail="crisis not found")
    if row.geojson is None:
        raise HTTPException(status_code=404, detail="crisis has no geometry")
    return AreaGeometryResponse.model_validate(json.loads(str(row.geojson)))


@router.get("", response_model=list[CrisisAdminListItem])
async def list_crises(
    state: Annotated[AppState, Depends(get_app_state)],
) -> list[CrisisAdminListItem]:
    async with state.sessionmaker() as session:
        return await list_admin_crises(session)
