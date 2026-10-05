"""Approximate building count and duration for an AOI, shown before an ingest.

Counts come from the density grid, falling back to S3 parquet metadata when no
grid is built. Both overcount (bbox, not polygon).
"""

from __future__ import annotations

import asyncio
import logging
import math
import uuid

from shapely import wkb as shapely_wkb
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.buildings.density_grid import cell_range_for_bbox, lookup_density_count
from api.buildings.ingest_job import BUFFER_DEGREES, SIMPLIFY_DEGREES
from api.buildings.overture_building_reader import BuildingsReader
from api.buildings.overture_release_locator import OvertureReleaseLocator
from api.schemas.crises import IngestEstimateResponse, IngestEstimateSeconds

logger = logging.getLogger(__name__)


class EstimateUnavailableError(RuntimeError):
    """The crisis has no usable geometry to estimate against."""


class IngestEstimator:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        reader: BuildingsReader,
        release_locator: OvertureReleaseLocator,
        *,
        download_rows_per_sec: float,
        load_rows_per_sec: float,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._reader = reader
        self._release_locator = release_locator
        self._download_rps = download_rows_per_sec
        self._load_rps = load_rows_per_sec

    async def estimate(self, crisis_id: uuid.UUID) -> IngestEstimateResponse:
        polygon_wkb = await self._buffered_wkb(crisis_id)
        if polygon_wkb is None:
            raise EstimateUnavailableError(
                f"crisis {crisis_id} has no geometry to estimate against"
            )
        return await self._estimate_for_wkb(polygon_wkb)

    async def estimate_geometry(self, geojson: str) -> IngestEstimateResponse:
        """Estimate for an unsaved GeoJSON geometry (crisis create flow)."""
        polygon_wkb = await self._buffered_wkb_from_geojson(geojson)
        if polygon_wkb is None:
            raise EstimateUnavailableError("geometry is empty or not a valid polygon")
        return await self._estimate_for_wkb(polygon_wkb)

    async def _estimate_for_wkb(self, polygon_wkb: bytes) -> IngestEstimateResponse:
        release = self._release_locator.latest()
        count, basis = await self._count(polygon_wkb, release)

        download = math.ceil(count / self._download_rps) if self._download_rps > 0 else 0
        load = math.ceil(count / self._load_rps) if self._load_rps > 0 else 0
        return IngestEstimateResponse(
            approx_building_count=count,
            estimate_basis=basis,
            est_seconds=IngestEstimateSeconds(download=download, load=load, total=download + load),
            note=(
                "Approximate (bounding-box overcount). Most of the time is the "
                "set-based load + incremental spatial-index maintenance."
            ),
        )

    async def _count(self, polygon_wkb: bytes, release: str) -> tuple[int, str]:
        """Building count for the AOI bbox: density grid, else S3 metadata (slow)."""
        xmin, ymin, xmax, ymax = shapely_wkb.loads(polygon_wkb).bounds
        ix0, iy0, ix1, iy1 = cell_range_for_bbox(xmin, ymin, xmax, ymax)
        async with self._sessionmaker() as session:
            grid_count = await lookup_density_count(
                session, ix0=ix0, iy0=iy0, ix1=ix1, iy1=iy1, preferred_release=release
            )
        if grid_count is not None:
            return grid_count, "density_grid"

        logger.info("density grid empty for release %s; falling back to live S3 count", release)
        count = await asyncio.to_thread(self._reader.approx_count, polygon_wkb, release)
        return count, "parquet_bbox_metadata"

    async def _buffered_wkb(self, crisis_id: uuid.UUID) -> bytes | None:
        """The crisis AOI buffered and simplified the same way the ingest does."""
        async with self._sessionmaker() as session:
            row = (
                await session.execute(
                    text(
                        "select case when geometry is null then null "
                        "  else st_asbinary(st_multi(st_simplifypreservetopology("
                        "    st_buffer(geometry::geometry, :buf), :simplify"
                        "  ))) end as wkb "
                        "from public.crises where id = :id"
                    ),
                    {"id": str(crisis_id), "buf": BUFFER_DEGREES, "simplify": SIMPLIFY_DEGREES},
                )
            ).first()
        if row is None or row.wkb is None:
            return None
        return bytes(row.wkb)

    async def _buffered_wkb_from_geojson(self, geojson: str) -> bytes | None:
        """Same as _buffered_wkb for raw GeoJSON; None if empty or rejected by PostGIS."""
        try:
            async with self._sessionmaker() as session:
                row = (
                    await session.execute(
                        text(
                            "select st_asbinary(st_multi(st_simplifypreservetopology("
                            "  st_buffer("
                            "    st_setsrid(st_geomfromgeojson(:g), 4326), :buf"
                            "  ), :simplify))) as wkb"
                        ),
                        {"g": geojson, "buf": BUFFER_DEGREES, "simplify": SIMPLIFY_DEGREES},
                    )
                ).first()
        except DBAPIError:
            return None
        if row is None or row.wkb is None:
            return None
        return bytes(row.wkb)


__all__ = ["EstimateUnavailableError", "IngestEstimator"]
