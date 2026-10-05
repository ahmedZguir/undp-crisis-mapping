"""Crisis photo exports, built by an Arq job.

At most one running export per crisis: an identical re-POST attaches to it, a
different one gets 409. Running rows older than the worker job_timeout are stale.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text

from api.admin.guards import require_crisis
from api.auth.dependency import require_coordinator
from api.auth.models import Coordinator
from api.core.app_state import AppState, get_app_state, get_storage
from api.core.arq import ArqEnqueuer, JobEnqueuer
from api.core.config import get_settings
from api.core.rate_limit import limit
from api.core.storage import ExportPartStore, StorageClient
from api.schemas.report_exports import (
    PhotoExportCreatedResponse,
    PhotoExportCreateRequest,
    PhotoExportDetail,
    PhotoExportListItem,
    PhotoExportPart,
)

router = APIRouter()

# Matches WorkerSettings.job_timeout, so a dead job cannot block a crisis forever.
_RUNNING_STALE_HOURS = 48


def get_photo_export_enqueuer(
    state: Annotated[AppState, Depends(get_app_state)],
) -> JobEnqueuer:
    return ArqEnqueuer(state.arq_pool, "export_crisis_photos")


def get_export_part_store(
    storage: Annotated[StorageClient, Depends(get_storage)],
) -> ExportPartStore:
    return storage


@router.post(
    "/crises/{crisis_id}/photo_exports",
    response_model=PhotoExportCreatedResponse,
    status_code=201,
)
@limit("30/minute")
async def create_photo_export(
    request: Request,
    crisis_id: uuid.UUID,
    body: PhotoExportCreateRequest,
    coordinator: Annotated[Coordinator, Depends(require_coordinator)],
    enqueuer: Annotated[JobEnqueuer, Depends(get_photo_export_enqueuer)],
    state: Annotated[AppState, Depends(get_app_state)],
) -> PhotoExportCreatedResponse:
    filters_payload = body.filters.model_dump(mode="json", exclude_none=True)

    async with state.sessionmaker() as session, session.begin():
        await require_crisis(session, crisis_id)

        running = (
            await session.execute(
                text(
                    "select id, created_at, filters, scope, format "
                    "from public.report_exports "
                    "where crisis_id = :cid and status = 'running' "
                    "  and created_at > now() - make_interval(hours => :stale) "
                    "order by created_at desc limit 1"
                ),
                {"cid": str(crisis_id), "stale": _RUNNING_STALE_HOURS},
            )
        ).first()
        if running is not None:
            same = (
                running.filters == filters_payload
                and running.scope == body.scope
                and running.format == body.format
            )
            if same:
                # Identical re-click joins the in-flight job.
                return PhotoExportCreatedResponse(
                    id=running.id, status="running", created_at=running.created_at, attached=True
                )
            raise HTTPException(
                status_code=409,
                detail="an export for this crisis is already in progress",
            )

        row = (
            await session.execute(
                text(
                    "insert into public.report_exports "
                    "    (crisis_id, created_by, created_by_email, status, filters, scope, format) "
                    "values (:crisis_id, :created_by, :created_by_email, 'running', "
                    "        cast(:filters as jsonb), :scope, :format) "
                    "returning id, status, created_at"
                ),
                {
                    "crisis_id": str(crisis_id),
                    "created_by": str(coordinator.id),
                    "created_by_email": coordinator.email,
                    "filters": _dump_filters(filters_payload),
                    "scope": body.scope,
                    "format": body.format,
                },
            )
        ).one()
        await enqueuer.enqueue(row.id)

    return PhotoExportCreatedResponse(
        id=row.id, status=row.status, created_at=row.created_at, attached=False
    )


@router.get(
    "/crises/{crisis_id}/photo_exports",
    response_model=list[PhotoExportListItem],
)
async def list_photo_exports(
    crisis_id: uuid.UUID,
    state: Annotated[AppState, Depends(get_app_state)],
) -> list[PhotoExportListItem]:
    async with state.sessionmaker() as session:
        rows = (
            await session.execute(
                text(
                    "select id, created_at, created_by, created_by_email, status, phase, "
                    "       scope, format, photo_count, total_bytes, expires_at, parts "
                    "from public.report_exports "
                    "where crisis_id = :id "
                    "order by created_at desc"
                ),
                {"id": str(crisis_id)},
            )
        ).all()

    return [
        PhotoExportListItem(
            id=r.id,
            created_at=r.created_at,
            created_by=r.created_by,
            created_by_email=r.created_by_email,
            status=r.status,
            phase=r.phase,
            scope=r.scope,
            format=r.format,
            photo_count=r.photo_count,
            total_bytes=r.total_bytes,
            expires_at=r.expires_at,
            download_ready=r.status == "succeeded" and bool(r.parts),
        )
        for r in rows
    ]


@router.get(
    "/crises/{crisis_id}/photo_exports/{export_id}",
    response_model=PhotoExportDetail,
)
async def get_photo_export(
    crisis_id: uuid.UUID,
    export_id: uuid.UUID,
    store: Annotated[ExportPartStore, Depends(get_export_part_store)],
    state: Annotated[AppState, Depends(get_app_state)],
) -> PhotoExportDetail:
    async with state.sessionmaker() as session:
        row = (
            await session.execute(
                text(
                    "select re.id, re.crisis_id, re.created_at, re.created_by, "
                    "       re.created_by_email, re.status, re.phase, re.progress_count, "
                    "       re.progress_total, re.error, re.ended_at, re.scope, re.format, "
                    "       re.photo_count, re.total_bytes, re.expires_at, re.parts, "
                    "       c.name as crisis_name "
                    "from public.report_exports re "
                    "left join public.crises c on c.id = re.crisis_id "
                    "where re.id = :id and re.crisis_id = :crisis_id"
                ),
                {"id": str(export_id), "crisis_id": str(crisis_id)},
            )
        ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="export not found")

    parts = await _signed_parts(store, row)
    return PhotoExportDetail(
        id=row.id,
        crisis_id=row.crisis_id,
        created_at=row.created_at,
        created_by=row.created_by,
        created_by_email=row.created_by_email,
        status=row.status,
        phase=row.phase,
        progress_count=row.progress_count,
        progress_total=row.progress_total,
        error=row.error,
        ended_at=row.ended_at,
        scope=row.scope,
        format=row.format,
        photo_count=row.photo_count,
        total_bytes=row.total_bytes,
        expires_at=row.expires_at,
        parts=parts,
    )


def _dump_filters(filters_payload: dict[str, Any]) -> str:
    return json.dumps(filters_payload)


def _bundle_part_filename(
    crisis_name: str | None, dt: datetime | None, part_number: int, total: int
) -> str:
    """Download name for one zip part, e.g. "Gaza 2026-05-01 part 2 of 3.zip".

    Browsers ignore `<a download>` cross-origin, so the name travels in the
    signed URL's download param.
    """
    name = re.sub(r'[/\\:*?"<>|]+', " ", (crisis_name or "crisis export"))
    name = re.sub(r"\s+", " ", name).strip() or "crisis export"
    date = dt.strftime("%Y-%m-%d") if dt is not None else ""
    base = f"{name} {date}".strip()
    if total > 1:
        return f"{base} part {part_number} of {total}.zip"
    return f"{base}.zip"


async def _signed_parts(store: ExportPartStore, row: Any) -> list[PhotoExportPart]:
    """Sign each zip part with a TTL capped to the bundle's remaining life."""
    if row.status != "succeeded" or not row.parts:
        return []
    settings = get_settings()
    ttl = settings.export_signed_url_ttl_seconds
    if row.expires_at is not None:
        expires = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=UTC)
        remaining = int((expires - datetime.now(UTC)).total_seconds())
        if remaining <= 0:
            return []
        ttl = min(ttl, remaining)

    parts_meta: list[dict[str, Any]] = row.parts or []
    crisis_name = getattr(row, "crisis_name", None)
    when = row.ended_at or row.created_at
    total = len(parts_meta)
    signed: list[PhotoExportPart] = []
    for idx, part in enumerate(parts_meta, start=1):
        key = part.get("key")
        if not isinstance(key, str):
            continue
        filename = _bundle_part_filename(crisis_name, when, idx, total)
        url = await store.sign_export_url(key, ttl, download=filename)
        signed.append(
            PhotoExportPart(
                part_number=idx,
                download_url=url,
                bytes=int(part.get("bytes", 0)),
                photo_count=int(part.get("photo_count", 0)),
            )
        )
    return signed
