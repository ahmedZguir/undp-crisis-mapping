"""Overture building ingest for one crisis: resolve the AOI, tile it, bulk-load
each tile, then stamp the crisis. Row stamps commit before the PMTiles bake so a
bake failure does not invalidate loaded rows; the next run retries the bake.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.buildings.aoi_tiler import DEFAULT_TILE_DEGREES, tile_polygon
from api.buildings.buildings_bulk_loader import BuildingsBulkLoader
from api.buildings.crisis_polygon_resolver import CrisisPolygonResolver
from api.buildings.ingest_progress import CrisisJobProgress
from api.buildings.overture_building_reader import BuildingsReader
from api.buildings.overture_release_locator import OvertureReleaseLocator
from api.buildings.pmtiles_extractor import (
    PmtilesExtractor,
    PmtilesExtractorError,
)

logger = logging.getLogger(__name__)

# ~100 m at the equator: absorbs GPS jitter for reports at the AOI edge.
BUFFER_DEGREES = 100.0 / 111_320.0

# ~1 km. DuckDB st_intersects cost scales with vertex count; preserve-topology
# simplification keeps a small AOI from collapsing to empty.
SIMPLIFY_DEGREES = 0.01

_PHASE_LOADING = "loading"


class SkippedNoGeometryError(RuntimeError):
    """The crisis has no geometry and no countries that resolve to a polygon.

    Expected for the reserved Other / Unspecified crisis; the worker logs and skips.
    """


class CrisisNotFoundError(LookupError):
    """No crisis row with the supplied id."""


class BuildingIngestJob:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        reader: BuildingsReader,
        bulk_loader: BuildingsBulkLoader,
        release_locator: OvertureReleaseLocator,
        pmtiles_extractor: PmtilesExtractor | None = None,
        pmtiles_target_path: Path | None = None,
        polygon_resolver: CrisisPolygonResolver | None = None,
        progress: CrisisJobProgress | None = None,
        tile_degrees: float = DEFAULT_TILE_DEGREES,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._reader = reader
        self._bulk_loader = bulk_loader
        self._release_locator = release_locator
        self._pmtiles_extractor = pmtiles_extractor
        self._pmtiles_target_path = pmtiles_target_path
        self._polygon_resolver = polygon_resolver
        self._progress = progress
        self._tile_degrees = tile_degrees

    async def run(self, crisis_id: uuid.UUID) -> int:
        """Ingest buildings for one crisis; returns total rows upserted."""
        logger.info("building-ingest loading polygon crisis_id=%s", crisis_id)
        polygon_wkb = await self._load_buffered_polygon_wkb(crisis_id)

        release = self._release_locator.latest()
        tiles = tile_polygon(polygon_wkb, self._tile_degrees)
        logger.info(
            "building-ingest start crisis_id=%s release=%s tiles=%s",
            crisis_id,
            release,
            len(tiles),
        )

        # Best-effort denominator for the admin progress bar.
        total_estimate = await self._approx_total(polygon_wkb, release)
        await self._report_progress(crisis_id, count=0, total=total_estimate)

        total = await self._load_tiles(crisis_id, tiles, release)

        # Stamp before the slow, failure-prone PMTiles bake so a bake failure
        # does not invalidate the rows already loaded.
        logger.info("building-ingest stamping release crisis_id=%s", crisis_id)
        await self._stamp_release_and_ingested_at(crisis_id, release, total)

        pmtiles_url = await self._maybe_extract_pmtiles(crisis_id, polygon_wkb)
        if pmtiles_url is not None:
            logger.info("building-ingest stamping pmtiles_url crisis_id=%s", crisis_id)
            await self._stamp_pmtiles_url(crisis_id, pmtiles_url)
        logger.info(
            "building-ingest done crisis_id=%s release=%s rows=%s pmtiles=%s",
            crisis_id,
            release,
            total,
            "set" if pmtiles_url else "skipped",
        )
        return total

    async def _load_tiles(self, crisis_id: uuid.UUID, tiles: list[bytes], release: str) -> int:
        await self._bulk_loader.create_stage(crisis_id)
        total = 0
        for tile_n, tile_wkb in enumerate(tiles, start=1):
            n = await self._bulk_loader.load_tile(
                crisis_id, self._reader.read(polygon_wkb=tile_wkb, release=release)
            )
            total += n
            logger.info(
                "building-ingest tile crisis_id=%s tile=%s/%s upserted=%s total=%s",
                crisis_id,
                tile_n,
                len(tiles),
                n,
                total,
            )
            await self._report_progress(crisis_id, count=total)
        # Success path only: cleanup on failure would race cancellation, and the
        # next run reuses a leftover stage.
        await self._bulk_loader.drop_stage(crisis_id)
        return total

    async def _approx_total(self, polygon_wkb: bytes, release: str) -> int | None:
        """Approximate building count from reader metadata, or None if unavailable."""
        approx_count = getattr(self._reader, "approx_count", None)
        if approx_count is None:
            return None
        try:
            return await asyncio.to_thread(approx_count, polygon_wkb, release)
        except Exception:
            logger.warning("building-ingest progress-total estimate failed", exc_info=True)
            return None

    async def _report_progress(
        self, crisis_id: uuid.UUID, *, count: int, total: int | None = None
    ) -> None:
        if self._progress is None:
            return
        await self._progress.update(crisis_id, phase=_PHASE_LOADING, count=count, total=total)

    async def _maybe_extract_pmtiles(
        self,
        crisis_id: uuid.UUID,
        polygon_wkb: bytes,
    ) -> str | None:
        """Bake and upload PMTiles if configured; returns the URL or None on failure.

        The extractor resolves its own tile release, since tile bakes lag the
        parquet releases.
        """
        if self._pmtiles_extractor is None:
            return None
        target_path = (
            self._pmtiles_target_path or Path(tempfile.gettempdir()) / f"crisis-{crisis_id}.pmtiles"
        )
        logger.info(
            "building-ingest pmtiles-extract start crisis_id=%s target=%s",
            crisis_id,
            target_path,
        )
        try:
            # extract is sync and can run for minutes; keep the loop responsive.
            public_url, tile_release = await asyncio.to_thread(
                self._pmtiles_extractor.extract,
                polygon_wkb=polygon_wkb,
                target_path=target_path,
            )
            logger.info(
                "building-ingest pmtiles-extract done crisis_id=%s tile_release=%s url=%s",
                crisis_id,
                tile_release,
                public_url,
            )
            return public_url
        except PmtilesExtractorError:
            logger.exception(
                "building-ingest pmtiles-extract failed crisis_id=%s",
                crisis_id,
            )
            return None

    async def _load_buffered_polygon_wkb(self, crisis_id: uuid.UUID) -> bytes:
        row = await self._fetch_geom_row(crisis_id)
        if row is None:
            raise CrisisNotFoundError(str(crisis_id))
        if row.buffered_wkb is not None:
            return bytes(row.buffered_wkb)

        countries = list(row.countries or [])
        if not countries:
            raise SkippedNoGeometryError(f"crisis {crisis_id} has no geometry; skipping ingest")
        if self._polygon_resolver is None:
            raise SkippedNoGeometryError(
                f"crisis {crisis_id} has no geometry and no polygon resolver is "
                "configured to resolve countries"
            )

        logger.info(
            "building-ingest resolving polygon from countries crisis_id=%s countries=%s",
            crisis_id,
            countries,
        )
        # wkb is None when none of the codes match a country polygon.
        resolved = await self._polygon_resolver.resolve(geometry=None, countries=countries)
        if resolved.wkb is None:
            raise SkippedNoGeometryError(
                f"crisis {crisis_id} has countries {countries} but no country "
                "polygons were found for them"
            )
        polygon_wkb = resolved.wkb
        await self._stamp_geometry(crisis_id, polygon_wkb)

        # Re-fetch so Postgres applies the same buffer and simplification.
        row = await self._fetch_geom_row(crisis_id)
        assert row is not None and row.buffered_wkb is not None
        return bytes(row.buffered_wkb)

    async def _fetch_geom_row(self, crisis_id: uuid.UUID) -> Any:
        async with self._sessionmaker() as session:
            return (
                await session.execute(
                    text(
                        "select "
                        "  countries, "
                        "  case when geometry is null then null "
                        "    else st_asbinary("
                        "      st_multi(st_simplifypreservetopology("
                        "        st_buffer(geometry::geometry, :buf), :simplify"
                        "      ))"
                        "    ) end as buffered_wkb "
                        "from public.crises where id = :id"
                    ),
                    {
                        "id": str(crisis_id),
                        "buf": BUFFER_DEGREES,
                        "simplify": SIMPLIFY_DEGREES,
                    },
                )
            ).first()

    async def _stamp_geometry(self, crisis_id: uuid.UUID, polygon_wkb: bytes) -> None:
        async with self._sessionmaker() as session, session.begin():
            await session.execute(
                text(
                    "update public.crises "
                    "set geometry = "
                    "  st_multi(st_geomfromwkb(:wkb, 4326))::geography "
                    "where id = :id"
                ),
                {"id": str(crisis_id), "wkb": polygon_wkb},
            )

    async def _stamp_release_and_ingested_at(
        self, crisis_id: uuid.UUID, release: str, count: int
    ) -> None:
        async with self._sessionmaker() as session, session.begin():
            await session.execute(
                text(
                    "update public.crises "
                    "set overture_release_pinned = :release, "
                    "    buildings_ingested_at = now(), "
                    "    buildings_ingested_count = :count "
                    "where id = :id"
                ),
                {"id": str(crisis_id), "release": release, "count": count},
            )

    async def _stamp_pmtiles_url(self, crisis_id: uuid.UUID, pmtiles_url: str) -> None:
        async with self._sessionmaker() as session, session.begin():
            await session.execute(
                text("update public.crises set pmtiles_url = :url where id = :id"),
                {"id": str(crisis_id), "url": pmtiles_url},
            )


__all__ = [
    "BUFFER_DEGREES",
    "BuildingIngestJob",
    "CrisisNotFoundError",
    "SkippedNoGeometryError",
]
