"""Heatmap MVT tiles and stats.

The admin tile route has the same shape as the public one (admin_router is mounted
under /admin) but skips the visibility gate, uses k=1, and has a 5-second private
cache so coordinators see new reports on the next pan.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from api.core.app_state import AppState, get_app_state
from api.crises.service import CrisisNotFoundError
from api.heatmap.stats import HeatmapStatsService
from api.heatmap.tiles import HeatmapTileService
from api.schemas.public_reports import CrisisStatsResponse

router = APIRouter()
admin_router = APIRouter()

_TILE_CACHE_CONTROL = "public, max-age=15"
_ADMIN_TILE_CACHE_CONTROL = "private, max-age=5"


def get_tile_service(
    state: Annotated[AppState, Depends(get_app_state)],
) -> HeatmapTileService:
    return state.heatmap_tile_service


def get_stats_service(
    state: Annotated[AppState, Depends(get_app_state)],
) -> HeatmapStatsService:
    return state.heatmap_stats_service


async def _render_tile(
    *,
    crisis_id: uuid.UUID,
    z: int,
    x: int,
    y: int,
    request: Request,
    service: HeatmapTileService,
    admin: bool,
) -> Response:
    try:
        rendered = await service.render_tile(crisis_id, z, x, y, admin=admin)
    except CrisisNotFoundError as exc:
        raise HTTPException(status_code=404, detail="crisis_not_found") from exc

    etag = _weak_etag(rendered.latest_at)
    cache_control = _ADMIN_TILE_CACHE_CONTROL if admin else _TILE_CACHE_CONTROL
    headers = {"ETag": etag, "Cache-Control": cache_control}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(
        content=rendered.body,
        media_type="application/x-protobuf",
        headers=headers,
    )


@router.get(
    "/crises/{crisis_id}/tiles/heat/{z}/{x}/{y}.pbf",
    response_class=Response,
)
async def get_heat_tile(
    crisis_id: uuid.UUID,
    z: int,
    x: int,
    y: int,
    request: Request,
    service: Annotated[HeatmapTileService, Depends(get_tile_service)],
) -> Response:
    return await _render_tile(
        crisis_id=crisis_id,
        z=z,
        x=x,
        y=y,
        request=request,
        service=service,
        admin=False,
    )


@router.get(
    "/crises/{crisis_id}/stats",
    response_model=CrisisStatsResponse,
)
async def get_crisis_stats(
    crisis_id: uuid.UUID,
    service: Annotated[HeatmapStatsService, Depends(get_stats_service)],
) -> CrisisStatsResponse:
    try:
        return await service.compute(crisis_id)
    except CrisisNotFoundError as exc:
        raise HTTPException(status_code=404, detail="crisis_not_found") from exc


@admin_router.get(
    "/crises/{crisis_id}/tiles/heat/{z}/{x}/{y}.pbf",
    response_class=Response,
)
async def get_admin_heat_tile(
    crisis_id: uuid.UUID,
    z: int,
    x: int,
    y: int,
    request: Request,
    service: Annotated[HeatmapTileService, Depends(get_tile_service)],
) -> Response:
    return await _render_tile(
        crisis_id=crisis_id,
        z=z,
        x=x,
        y=y,
        request=request,
        service=service,
        admin=True,
    )


def _weak_etag(latest_at: datetime | None) -> str:
    if latest_at is None:
        return 'W/"empty"'
    return f'W/"{int(latest_at.timestamp())}"'
