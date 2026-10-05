"""Idempotent report insert.

A unique violation on client_submission_id returns the existing row with
was_duplicate=True, so client retries succeed without creating a second report.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.core.h3 import cell_for_location
from api.schemas.reports import PhotoMetadata, ReportSubmitPayload


@dataclass(frozen=True, slots=True)
class WrittenReport:
    id: uuid.UUID
    crisis_id: uuid.UUID
    created_at: datetime
    was_duplicate: bool


# map_point and map_point_source are set by DB triggers; do not insert them.
_INSERT_SQL = text(
    """
    insert into public.reports
        (crisis_id, damage_class, description, route_description, photo_path,
         location, client_id,
         infra_type, infra_name,
         crisis_type, crisis_type_detailed, debris, building_id,
         client_submission_id,
         photo_captured_at, photo_exif_gps, photo_exif_extracted_at, photo_exif_meta,
         form_version, generic_answers)
    values
        (:crisis_id, :damage_class, :description, :route_description, :photo_path,
         cast(:location as geography), :client_id,
         cast(:infra_type as text[]), :infra_name,
         :crisis_type, :crisis_type_detailed, :debris, :building_id,
         :client_submission_id,
         :photo_captured_at,
         cast(:photo_exif_gps as geography),
         :photo_exif_extracted_at,
         cast(:photo_exif_meta as jsonb),
         :form_version,
         coalesce(cast(:generic_answers as jsonb), '{}'::jsonb))
    returning id, crisis_id, created_at
    """
)


_FETCH_BY_SUBMISSION_ID_SQL = text(
    """
    select id, crisis_id, created_at
    from public.reports
    where client_submission_id = :client_submission_id
    """
)


# Enrichment sidecar rows are created with the report, so workers only ever UPDATE.
_INSERT_TRANSLATION_SIDECAR_SQL = text(
    "insert into public.report_translations (report_id) "
    "values (:id) "
    "on conflict (report_id) do nothing"
)
_INSERT_CAPTION_SIDECAR_SQL = text(
    "insert into public.image_captions (report_id) values (:id) on conflict (report_id) do nothing"
)
_INSERT_GEOCODE_SIDECAR_SQL = text(
    "insert into public.report_geocodes (report_id) values (:id) on conflict (report_id) do nothing"
)
# client_id is copied onto report_quality so /me/stats avoids joining reports.
_INSERT_QUALITY_SIDECAR_SQL = text(
    "insert into public.report_quality (report_id, client_id, has_photo, has_description) "
    "values (:id, :client_id, :has_photo, :has_description) "
    "on conflict (report_id) do nothing"
)


# Same transaction as the report insert, so reports and heat_cells stay consistent.
_UPSERT_HEAT_CELL_SQL = text(
    """
    insert into public.heat_cells
        (crisis_id, h3_cell, report_count,
         minimal_count, partial_count, complete_count, latest_at)
    values
        (:crisis_id, :h3_cell, 1,
         case when :damage_class = 'minimal'  then 1 else 0 end,
         case when :damage_class = 'partial'  then 1 else 0 end,
         case when :damage_class = 'complete' then 1 else 0 end,
         :created_at)
    on conflict (crisis_id, h3_cell) do update set
        report_count   = public.heat_cells.report_count   + 1,
        minimal_count  = public.heat_cells.minimal_count  + excluded.minimal_count,
        partial_count  = public.heat_cells.partial_count  + excluded.partial_count,
        complete_count = public.heat_cells.complete_count + excluded.complete_count,
        latest_at      = greatest(public.heat_cells.latest_at, excluded.latest_at)
    """
)


# Postgres unique_violation, read from IntegrityError.orig.sqlstate.
_UNIQUE_VIOLATION = "23505"


class IdempotentReportWriter:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def write(
        self,
        *,
        payload: ReportSubmitPayload,
        photo_path: str | None,
        building_id: uuid.UUID | None,
    ) -> WrittenReport:
        params = self._params(payload, photo_path, building_id)
        async with self._sessionmaker() as session:
            try:
                row = (await session.execute(_INSERT_SQL, params)).one()
                if payload.location is not None:
                    await session.execute(
                        _UPSERT_HEAT_CELL_SQL,
                        {
                            "crisis_id": str(row.crisis_id),
                            "h3_cell": cell_for_location(
                                payload.location.lat, payload.location.lng
                            ),
                            "damage_class": payload.damage_class,
                            "created_at": row.created_at,
                        },
                    )
                await session.execute(_INSERT_TRANSLATION_SIDECAR_SQL, {"id": str(row.id)})
                await session.execute(_INSERT_CAPTION_SIDECAR_SQL, {"id": str(row.id)})
                await session.execute(_INSERT_GEOCODE_SIDECAR_SQL, {"id": str(row.id)})
                await session.execute(
                    _INSERT_QUALITY_SIDECAR_SQL,
                    {
                        "id": str(row.id),
                        "client_id": (
                            str(payload.client_id) if payload.client_id is not None else None
                        ),
                        "has_photo": photo_path is not None,
                        "has_description": bool(
                            payload.description and payload.description.strip()
                        ),
                    },
                )
                await session.commit()
                return WrittenReport(
                    id=row.id,
                    crisis_id=row.crisis_id,
                    created_at=row.created_at,
                    was_duplicate=False,
                )
            except IntegrityError as exc:
                if not _is_unique_violation(exc) or payload.client_submission_id is None:
                    raise
                await session.rollback()
                existing = (
                    await session.execute(
                        _FETCH_BY_SUBMISSION_ID_SQL,
                        {"client_submission_id": str(payload.client_submission_id)},
                    )
                ).one()
                return WrittenReport(
                    id=existing.id,
                    crisis_id=existing.crisis_id,
                    created_at=existing.created_at,
                    was_duplicate=True,
                )

    @staticmethod
    def _params(
        payload: ReportSubmitPayload,
        photo_path: str | None,
        building_id: uuid.UUID | None,
    ) -> dict[str, object | None]:
        location_wkt = (
            f"SRID=4326;POINT({payload.location.lng} {payload.location.lat})"
            if payload.location is not None
            else None
        )
        photo_meta = _photo_metadata_params(payload.photo_metadata)
        return {
            "crisis_id": str(payload.crisis_id),
            "damage_class": payload.damage_class,
            "description": payload.description,
            "route_description": payload.route_description,
            "photo_path": photo_path,
            "location": location_wkt,
            "client_id": (str(payload.client_id) if payload.client_id is not None else None),
            "infra_type": payload.infra_type,
            "infra_name": payload.infra_name,
            "crisis_type": payload.crisis_type,
            "crisis_type_detailed": payload.crisis_type_detailed,
            "debris": payload.debris,
            "building_id": str(building_id) if building_id is not None else None,
            "client_submission_id": (
                str(payload.client_submission_id)
                if payload.client_submission_id is not None
                else None
            ),
            "form_version": payload.form_version,
            "generic_answers": (
                json.dumps(payload.generic_answers) if payload.generic_answers is not None else None
            ),
            **photo_meta,
        }


def _photo_metadata_params(
    meta: PhotoMetadata | None,
) -> dict[str, object | None]:
    """Split photo_metadata into its DB columns; extra EXIF fields go to a JSONB blob."""
    if meta is None:
        return {
            "photo_captured_at": None,
            "photo_exif_gps": None,
            "photo_exif_extracted_at": None,
            "photo_exif_meta": None,
        }
    gps_wkt = (
        f"SRID=4326;POINT({meta.gps.longitude} {meta.gps.latitude})"
        if meta.gps is not None
        else None
    )
    extras: dict[str, object] = {}
    if meta.orientation is not None:
        extras["orientation"] = meta.orientation
    if meta.width is not None:
        extras["width"] = meta.width
    if meta.height is not None:
        extras["height"] = meta.height
    if meta.camera is not None:
        camera = {
            k: v
            for k, v in (
                ("make", meta.camera.make),
                ("model", meta.camera.model),
                ("software", meta.camera.software),
            )
            if v is not None
        }
        if camera:
            extras["camera"] = camera
    # GPS accuracy has no column of its own.
    if meta.gps is not None and meta.gps.accuracy is not None:
        extras["gps_accuracy"] = meta.gps.accuracy
    return {
        "photo_captured_at": meta.captured_at,
        "photo_exif_gps": gps_wkt,
        "photo_exif_extracted_at": meta.extracted_at,
        "photo_exif_meta": json.dumps(extras) if extras else None,
    }


def _is_unique_violation(exc: IntegrityError) -> bool:
    sqlstate = getattr(exc.orig, "sqlstate", None)
    return sqlstate == _UNIQUE_VIOLATION
