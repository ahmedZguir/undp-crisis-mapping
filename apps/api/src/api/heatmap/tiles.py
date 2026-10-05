"""Heatmap MVT tiles built from public.heat_cells.

Each render loads every cell of the crisis, filters by tile bbox in Python, rolls up
to the zoom's H3 resolution, and drops parents below k. Work scales with the crisis's
cell count, not the viewport size.
"""

from __future__ import annotations

import asyncio
import logging
import math
import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

import mapbox_vector_tile as _mvt_typed  # pyright: ignore[reportMissingTypeStubs]
from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.core.h3 import (
    cell_boundary,
    cell_centre_in_bbox,
    cell_parent,
    cell_to_string,
    zoom_to_resolution,
)
from api.crises.service import CrisisService

logger = logging.getLogger(__name__)

# Untyped upstream; calls go through an Any alias.
_mvt: Any = _mvt_typed

# Keep aligned with the citizen-facing legend.
_SEVERITY_WEIGHTS = {"minimal": 0.2, "partial": 0.6, "complete": 1.0}

# Above this many cells per crisis the whole-crisis scan gets expensive; log a warning.
_WHOLE_CRISIS_SCAN_LIMIT = 50_000

_SELECT_CELLS_FOR_CRISIS_SQL = text(
    """
    select h3_cell, report_count,
           minimal_count, partial_count, complete_count, latest_at
      from public.heat_cells
     where crisis_id = :crisis_id
    """
)


_SELECT_K_AND_LATEST_SQL = text(
    """
    select c.heatmap_k_anonymity as k,
           (select max(latest_at) from public.heat_cells
             where crisis_id = c.id) as latest_at
      from public.crises c
     where c.id = :crisis_id
    """
)


@dataclass(slots=True)
class RenderedTile:
    """latest_at feeds the route's weak ETag; None when the crisis has no cells."""

    body: bytes
    latest_at: datetime | None


@dataclass(slots=True)
class _CellAgg:
    report_count: int = 0
    minimal_count: int = 0
    partial_count: int = 0
    complete_count: int = 0
    latest_at: datetime | None = None


class HeatmapTileService:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        crisis_service: CrisisService,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._crisis_service = crisis_service
        # Caps in-flight renders so heavy panning cannot starve the rest of the API.
        self._concurrency = asyncio.Semaphore(8)

    async def render_tile(
        self,
        crisis_id: uuid.UUID,
        z: int,
        x: int,
        y: int,
        *,
        admin: bool = False,
    ) -> RenderedTile:
        """admin=True skips the visibility gate and forces k=1."""
        async with self._concurrency:
            return await self._render_tile_inner(crisis_id, z, x, y, admin=admin)

    async def _render_tile_inner(
        self, crisis_id: uuid.UUID, z: int, x: int, y: int, *, admin: bool
    ) -> RenderedTile:
        bbox = _tile_bbox(z, x, y)
        target_res = zoom_to_resolution(z)

        async with self._sessionmaker() as session:
            if admin:
                # Admins see any crisis regardless of status or visibility.
                await self._crisis_service.require_exists(crisis_id, _session=session)
            else:
                # buildings and full modes have their own public surfaces.
                await self._crisis_service.require_public_visible(
                    crisis_id, "aggregate_view", _session=session
                )
            rows = (
                await session.execute(_SELECT_CELLS_FOR_CRISIS_SQL, {"crisis_id": str(crisis_id)})
            ).all()
            k_latest = (
                await session.execute(_SELECT_K_AND_LATEST_SQL, {"crisis_id": str(crisis_id)})
            ).one()

        # The k floor protects citizen privacy and does not apply to coordinators.
        k = 1 if admin else int(k_latest.k)
        latest_at: datetime | None = k_latest.latest_at

        if len(rows) > _WHOLE_CRISIS_SCAN_LIMIT:
            logger.warning(
                "heat.scan.overflow",
                extra={"crisis_id": str(crisis_id), "rows": len(rows)},
            )

        in_bbox = [r for r in rows if cell_centre_in_bbox(int(r.h3_cell), bbox)]
        if not in_bbox:
            return RenderedTile(body=_encode_empty(bbox), latest_at=latest_at)

        rolled = _rollup(in_bbox, target_res)
        features = [
            _feature(parent, agg) for parent, agg in rolled.items() if agg.report_count >= k
        ]
        if not features:
            return RenderedTile(body=_encode_empty(bbox), latest_at=latest_at)
        body = cast(
            bytes,
            _mvt.encode(
                {"name": "heat", "features": features},
                default_options={"quantize_bounds": bbox},
            ),
        )
        return RenderedTile(body=body, latest_at=latest_at)


def _rollup(rows: Sequence[Row[Any]], target_res: int) -> dict[int, _CellAgg]:
    """Group stored-res cells into their target-res parents."""
    out: dict[int, _CellAgg] = defaultdict(_CellAgg)
    for row in rows:
        parent = cell_parent(int(row.h3_cell), target_res)
        agg = out[parent]
        agg.report_count += int(row.report_count)
        agg.minimal_count += int(row.minimal_count)
        agg.partial_count += int(row.partial_count)
        agg.complete_count += int(row.complete_count)
        row_latest: datetime = row.latest_at
        if agg.latest_at is None or row_latest > agg.latest_at:
            agg.latest_at = row_latest
    return out


def _feature(parent_cell: int, agg: _CellAgg) -> dict[str, object]:
    ring = cell_boundary(parent_cell)
    coords = [*ring, ring[0]]
    wkt = "POLYGON((" + ", ".join(f"{lng} {lat}" for lng, lat in coords) + "))"
    weighted_severity = (
        _SEVERITY_WEIGHTS["minimal"] * agg.minimal_count
        + _SEVERITY_WEIGHTS["partial"] * agg.partial_count
        + _SEVERITY_WEIGHTS["complete"] * agg.complete_count
    ) / agg.report_count
    # Hour granularity blunts timing-based reidentification.
    latest_at = agg.latest_at
    assert latest_at is not None
    latest_iso = latest_at.replace(minute=0, second=0, microsecond=0).isoformat()
    return {
        "geometry": wkt,
        "properties": {
            "cell_id": cell_to_string(parent_cell),
            "report_count": agg.report_count,
            "minimal_count": agg.minimal_count,
            "partial_count": agg.partial_count,
            "complete_count": agg.complete_count,
            "weighted_severity": round(weighted_severity, 4),
            "latest_at": latest_iso,
        },
    }


def _encode_empty(bbox: tuple[float, float, float, float]) -> bytes:
    return cast(
        bytes,
        _mvt.encode(
            {"name": "heat", "features": []},
            default_options={"quantize_bounds": bbox},
        ),
    )


def _tile_bbox(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """Web Mercator tile to (min_lng, min_lat, max_lng, max_lat) in WGS84."""
    n = 2.0**z
    min_lng = x / n * 360.0 - 180.0
    max_lng = (x + 1) / n * 360.0 - 180.0
    # Tile y grows southward; reverse so min_lat < max_lat.
    max_lat = _tile_y_to_lat(y, n)
    min_lat = _tile_y_to_lat(y + 1, n)
    return (min_lng, min_lat, max_lng, max_lat)


def _tile_y_to_lat(y: float, n: float) -> float:
    return math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * y / n))))
