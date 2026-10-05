"""Public crisis reads for `buildings` and `full` visibility modes.

Any gate failure returns 404 so callers cannot learn that a crisis exists in another mode.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from api.core.app_state import AppState, get_app_state, get_storage
from api.core.rate_limit import limit
from api.core.storage import PhotoUrlSigner, StorageClient
from api.crises.public_buildings_service import PublicBuildingsService
from api.crises.public_reports_service import (
    PublicReportsService,
    ReportNotFoundError,
)
from api.crises.service import CrisisNotFoundError
from api.schemas.public_reports import (
    BuildingsFeatureCollection,
    PublicReportDetail,
    PublicReportListResponse,
)

router = APIRouter()

_PUBLIC_CACHE_CONTROL = "public, max-age=15"


def get_public_buildings_service(
    state: Annotated[AppState, Depends(get_app_state)],
) -> PublicBuildingsService:
    return state.public_buildings_service


def get_public_reports_service(
    state: Annotated[AppState, Depends(get_app_state)],
) -> PublicReportsService:
    return state.public_reports_service


def get_photo_url_signer(
    storage: Annotated[StorageClient, Depends(get_storage)],
) -> PhotoUrlSigner:
    return storage


@router.get(
    "/crises/{crisis_id}/public/buildings",
    response_model=BuildingsFeatureCollection,
)
@limit("60/minute")
async def get_public_buildings(
    crisis_id: uuid.UUID,
    request: Request,
    response: Response,
    service: Annotated[PublicBuildingsService, Depends(get_public_buildings_service)],
) -> BuildingsFeatureCollection:
    """GeoJSON pins for a `buildings`-mode crisis: worst damage class and visible report count."""
    # slowapi needs `request` in the signature.
    response.headers["Cache-Control"] = _PUBLIC_CACHE_CONTROL
    try:
        return await service.list_pins(crisis_id)
    except CrisisNotFoundError as exc:
        raise HTTPException(status_code=404, detail="crisis_not_found") from exc


@router.get(
    "/crises/{crisis_id}/public/reports",
    response_model=PublicReportListResponse,
)
@limit("60/minute")
async def list_public_reports(
    crisis_id: uuid.UUID,
    request: Request,
    response: Response,
    service: Annotated[PublicReportsService, Depends(get_public_reports_service)],
    bbox: Annotated[
        str | None,
        Query(description="Bounding box 'west,south,east,north' in WGS84 lon/lat."),
    ] = None,
    cursor: Annotated[
        str | None,
        Query(description="Opaque pagination cursor from a previous response."),
    ] = None,
    limit_param: Annotated[int, Query(alias="limit", ge=1, le=2000)] = 50,
) -> PublicReportListResponse:
    """Public-safe report list for a `full`-mode crisis; bbox and cursor are exclusive."""
    response.headers["Cache-Control"] = _PUBLIC_CACHE_CONTROL
    if bbox is not None and cursor is not None:
        raise HTTPException(status_code=400, detail="bbox and cursor are mutually exclusive")
    try:
        return await service.list_for_crisis(
            crisis_id=crisis_id,
            bbox=bbox,
            cursor=cursor,
            limit=limit_param,
        )
    except CrisisNotFoundError as exc:
        raise HTTPException(status_code=404, detail="crisis_not_found") from exc


@router.get(
    "/public/reports/{report_id}",
    response_model=PublicReportDetail,
)
@limit("60/minute")
async def get_public_report_detail(
    report_id: uuid.UUID,
    request: Request,
    response: Response,
    service: Annotated[PublicReportsService, Depends(get_public_reports_service)],
    signer: Annotated[PhotoUrlSigner, Depends(get_photo_url_signer)],
) -> PublicReportDetail:
    """Public-safe detail for one report, with a signed photo URL."""
    response.headers["Cache-Control"] = _PUBLIC_CACHE_CONTROL
    try:
        return await service.get_detail(report_id, signer=signer)
    except ReportNotFoundError as exc:
        raise HTTPException(status_code=404, detail="report_not_found") from exc
