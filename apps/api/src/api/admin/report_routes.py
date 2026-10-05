"""Admin report list, detail, verify and GeoJSON/CSV export routes."""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from api.admin.export_queries import (
    fetch_crisis_name,
    stream_csv,
    stream_feature_collection,
)
from api.admin.guards import require_crisis
from api.admin.report_queries import (
    fetch_detail_row,
    fetch_reporter_reputation,
    list_by_bbox,
    list_by_cursor,
    row_to_detail,
    verify_report,
)
from api.ai import AIClientUnavailableError, embed_query
from api.auth.dependency import require_coordinator
from api.auth.models import Coordinator
from api.core.app_state import AppState, get_app_state, get_storage
from api.core.listing import Bbox, parse_bbox
from api.core.storage import (
    DEFAULT_SIGNED_URL_TTL_SECONDS,
    PhotoUrlSigner,
    StorageClient,
)
from api.schemas import VALID_DAMAGE_CLASSES
from api.schemas.admin_reports import (
    AdminReportDetailResponse,
    AdminReportListResponse,
    VerifyReportRequest,
    VerifyReportResponse,
)
from api.schemas.admin_search import SearchRequest
from api.schemas.report_exports import ExportScope

logger = logging.getLogger(__name__)

router = APIRouter()


def get_photo_url_signer(
    storage: Annotated[StorageClient, Depends(get_storage)],
) -> PhotoUrlSigner:
    return storage


@router.get(
    "/crises/{crisis_id}/reports",
    response_model=AdminReportListResponse,
)
async def list_reports(
    crisis_id: uuid.UUID,
    state: Annotated[AppState, Depends(get_app_state)],
    bbox: Annotated[
        str | None,
        Query(description="Bounding box 'west,south,east,north' in WGS84 lon/lat."),
    ] = None,
    cursor: Annotated[
        str | None,
        Query(description="Opaque pagination cursor from a previous response."),
    ] = None,
    damage_class: Annotated[
        str | None,
        Query(description="Optional filter: one of 'minimal', 'partial', 'complete'."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=2000)] = 50,
) -> AdminReportListResponse:
    """Map mode (bbox, unlocated reports excluded) or list mode (cursor, all reports).

    Passing neither returns the first list-mode page.
    """
    if bbox is not None and cursor is not None:
        raise HTTPException(status_code=400, detail="bbox and cursor are mutually exclusive")
    if damage_class is not None and damage_class not in VALID_DAMAGE_CLASSES:
        raise HTTPException(
            status_code=400,
            detail="damage_class must be one of 'minimal', 'partial', 'complete'",
        )

    async with state.sessionmaker() as session:
        await require_crisis(session, crisis_id)

        if bbox is not None:
            return await list_by_bbox(
                session,
                crisis_id=crisis_id,
                bbox=parse_bbox(bbox),
                damage_class=damage_class,
                limit=limit,
            )

        return await list_by_cursor(
            session,
            crisis_id=crisis_id,
            cursor=cursor,
            damage_class=damage_class,
            limit=limit,
        )


@router.get(
    "/reports/{report_id}",
    response_model=AdminReportDetailResponse,
)
async def get_report_detail(
    report_id: uuid.UUID,
    state: Annotated[AppState, Depends(get_app_state)],
    signer: Annotated[PhotoUrlSigner, Depends(get_photo_url_signer)],
) -> AdminReportDetailResponse:
    async with state.sessionmaker() as session:
        row = await fetch_detail_row(session, report_id)
        if row is None:
            raise HTTPException(status_code=404, detail="report not found")
        reporter_stats = await fetch_reporter_reputation(session, row.client_id)

    # Photo is optional; a description-only report has nothing to sign.
    photo_url = (
        await signer.sign_photo_url(row.photo_path, DEFAULT_SIGNED_URL_TTL_SECONDS)
        if row.photo_path is not None
        else None
    )
    return row_to_detail(row, photo_url=photo_url, reporter_stats=reporter_stats)


@router.patch(
    "/reports/{report_id}/verify",
    response_model=VerifyReportResponse,
)
async def patch_report_verify(
    report_id: uuid.UUID,
    body: VerifyReportRequest,
    state: Annotated[AppState, Depends(get_app_state)],
    coordinator: Annotated[Coordinator, Depends(require_coordinator)],
) -> VerifyReportResponse:
    """Toggle verification, recompute confidence and award or revoke the reporter's badge."""
    async with state.sessionmaker() as session:
        result = await verify_report(
            session,
            report_id=report_id,
            verified=body.verified,
            coordinator_id=coordinator.id,
        )
    if result is None:
        raise HTTPException(status_code=404, detail="report not found")
    score, verified, reporter_stats = result
    return VerifyReportResponse(
        report_id=report_id,
        verified=verified,
        confidence_score=score,
        reporter_stats=reporter_stats,
    )


# Export


def _as_utc(value: datetime | None) -> datetime | None:
    """Treat a naive date filter as UTC so results do not depend on the session time zone."""
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


_EXPORT_MEDIA_TYPES = {"geojson": "application/geo+json", "csv": "text/csv; charset=utf-8"}


def _export_response(
    content: AsyncIterator[bytes], *, ext: str, crisis_name: str, crisis_id: uuid.UUID
) -> StreamingResponse:
    """ASCII slug filename plus an RFC 5987 filename* so non-Latin names survive."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", crisis_name).strip("-").lower()
    ascii_name = f"{slug or f'crisis-{crisis_id}'}.{ext}"
    utf8_name = quote(f"{crisis_name}.{ext}")
    disposition = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{utf8_name}"
    return StreamingResponse(
        content,
        media_type=_EXPORT_MEDIA_TYPES[ext],
        headers={"Content-Disposition": disposition},
    )


@dataclass(frozen=True, slots=True)
class _ExportParams:
    crisis_name: str
    bbox: Bbox | None
    damage_class: str | None
    date_from: datetime | None
    date_to: datetime | None


async def _export_params(
    crisis_id: uuid.UUID,
    state: Annotated[AppState, Depends(get_app_state)],
    bbox: Annotated[
        str | None,
        Query(description="Bounding box 'west,south,east,north' in WGS84 lon/lat."),
    ] = None,
    damage_class: Annotated[
        str | None,
        Query(description="Optional filter: one of 'minimal', 'partial', 'complete'."),
    ] = None,
    date_from: Annotated[
        datetime | None,
        Query(description="Inclusive lower bound on report created_at (ISO-8601)."),
    ] = None,
    date_to: Annotated[
        datetime | None,
        Query(description="Inclusive upper bound on report created_at (ISO-8601)."),
    ] = None,
) -> _ExportParams:
    """Validate before streaming starts, so a bad request never sends a partial 200 body."""
    if damage_class is not None and damage_class not in VALID_DAMAGE_CLASSES:
        raise HTTPException(
            status_code=400,
            detail="damage_class must be one of 'minimal', 'partial', 'complete'",
        )
    parsed_bbox = parse_bbox(bbox) if bbox is not None else None

    async with state.sessionmaker() as session:
        crisis_name = await fetch_crisis_name(session, crisis_id)
    if crisis_name is None:
        raise HTTPException(status_code=404, detail="crisis not found")
    return _ExportParams(
        crisis_name=crisis_name,
        bbox=parsed_bbox,
        damage_class=damage_class,
        date_from=_as_utc(date_from),
        date_to=_as_utc(date_to),
    )


@router.get("/crises/{crisis_id}/export.geojson")
async def export_reports_geojson(
    crisis_id: uuid.UUID,
    state: Annotated[AppState, Depends(get_app_state)],
    params: Annotated[_ExportParams, Depends(_export_params)],
) -> StreamingResponse:
    return _export_response(
        stream_feature_collection(
            state.sessionmaker,
            crisis_id=crisis_id,
            crisis_name=params.crisis_name,
            damage_class=params.damage_class,
            bbox=params.bbox,
            date_from=params.date_from,
            date_to=params.date_to,
            generated_at=datetime.now(UTC).isoformat(),
        ),
        ext="geojson",
        crisis_name=params.crisis_name,
        crisis_id=crisis_id,
    )


@router.get("/crises/{crisis_id}/export.csv")
async def export_reports_csv(
    crisis_id: uuid.UUID,
    state: Annotated[AppState, Depends(get_app_state)],
    params: Annotated[_ExportParams, Depends(_export_params)],
) -> StreamingResponse:
    """Same fields as the GeoJSON export; nested fields are JSON text in one cell."""
    return _export_response(
        stream_csv(
            state.sessionmaker,
            crisis_id=crisis_id,
            damage_class=params.damage_class,
            bbox=params.bbox,
            date_from=params.date_from,
            date_to=params.date_to,
        ),
        ext="csv",
        crisis_name=params.crisis_name,
        crisis_id=crisis_id,
    )


# View-aware export: the dashboard's SearchRequest does not fit in query params,
# so these are POSTs. They share the photo-export resolver so both filter
# identically. An empty body exports the whole crisis.


async def _embed_export_query(state: AppState, request: SearchRequest) -> list[float] | None:
    """None when there is no query or the embedding endpoint is down (structured-only)."""
    if not (request.query and request.query.strip()):
        return None
    try:
        result = await embed_query(request.query.strip(), client=state.ai_clients.embedding)
        return result.vector
    except AIClientUnavailableError as exc:
        logger.warning("export.embedding_unavailable: %s", exc)
        return None


@router.post("/crises/{crisis_id}/export.geojson")
async def export_reports_view_geojson(
    crisis_id: uuid.UUID,
    body: SearchRequest,
    state: Annotated[AppState, Depends(get_app_state)],
    scope: Annotated[ExportScope, Query()] = "view",
) -> StreamingResponse:
    # api.photo_export.manifest imports api.admin, so a top-level import cycles.
    from api.photo_export.manifest import build_geojson_metadata, stream_matched_geojson
    from api.photo_export.resolver import count_match_set

    async with state.sessionmaker() as session:
        crisis_name = await fetch_crisis_name(session, crisis_id)
        if crisis_name is None:
            raise HTTPException(status_code=404, detail="crisis not found")
        query_vector = await _embed_export_query(state, body)
        counts = await count_match_set(
            session, crisis_id=crisis_id, request=body, query_vector=query_vector
        )
    metadata = build_geojson_metadata(
        crisis_id=crisis_id,
        crisis_name=crisis_name,
        request=body,
        scope=scope,
        report_count=counts.total,
        generated_at=datetime.now(UTC).isoformat(),
    )
    return _export_response(
        stream_matched_geojson(
            state.sessionmaker,
            crisis_id=crisis_id,
            request=body,
            query_vector=query_vector,
            metadata=metadata,
            with_photo_file=False,
        ),
        ext="geojson",
        crisis_name=crisis_name,
        crisis_id=crisis_id,
    )


@router.post("/crises/{crisis_id}/export.csv")
async def export_reports_view_csv(
    crisis_id: uuid.UUID,
    body: SearchRequest,
    state: Annotated[AppState, Depends(get_app_state)],
    # Accepted for symmetry with the GeoJSON variant; CSV has no metadata slot.
    scope: Annotated[ExportScope, Query()] = "view",
) -> StreamingResponse:
    from api.photo_export.manifest import stream_matched_csv  # local: avoid import cycle

    async with state.sessionmaker() as session:
        crisis_name = await fetch_crisis_name(session, crisis_id)
        if crisis_name is None:
            raise HTTPException(status_code=404, detail="crisis not found")
        query_vector = await _embed_export_query(state, body)
    return _export_response(
        stream_matched_csv(
            state.sessionmaker,
            crisis_id=crisis_id,
            request=body,
            query_vector=query_vector,
            with_photo_file=False,
        ),
        ext="csv",
        crisis_name=crisis_name,
        crisis_id=crisis_id,
    )
