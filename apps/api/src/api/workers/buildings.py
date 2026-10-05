from __future__ import annotations

import logging
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.areas import PostgresOvertureDivisionsReader
from api.buildings.buildings_bulk_loader import BuildingsBulkLoader
from api.buildings.crisis_polygon_resolver import CrisisPolygonResolver
from api.buildings.ingest_job import BuildingIngestJob
from api.buildings.ingest_progress import CrisisJobProgress
from api.buildings.overture_building_reader import DuckDBBuildingsReader
from api.buildings.overture_release_locator import (
    OvertureReleaseLocator,
    OvertureTileReleaseLocator,
)
from api.buildings.pmtiles_extractor import (
    PmtilesExtractor,
    SupabasePmtilesUploader,
)
from api.workers.context import sessionmaker_from_ctx
from api.workers.errors import format_terminal_error

logger = logging.getLogger(__name__)


async def ingest_buildings(ctx: dict[str, object], crisis_id: str) -> int:
    sessionmaker = sessionmaker_from_ctx(ctx)
    # Resolve the release once so the whole ingest reads one Overture vintage.
    release = OvertureReleaseLocator().latest()
    job = BuildingIngestJob(
        sessionmaker=sessionmaker,
        reader=DuckDBBuildingsReader(),
        bulk_loader=BuildingsBulkLoader(sessionmaker=sessionmaker),
        progress=CrisisJobProgress(sessionmaker=sessionmaker),
        release_locator=OvertureReleaseLocator(listing=lambda: release),
        pmtiles_extractor=PmtilesExtractor(
            uploader=SupabasePmtilesUploader(),
            tile_release_locator=OvertureTileReleaseLocator(),
        ),
        polygon_resolver=CrisisPolygonResolver(
            reader=PostgresOvertureDivisionsReader(sessionmaker),
        ),
    )
    return await run_ingest_buildings_with_terminal_write(uuid.UUID(crisis_id), job, sessionmaker)


async def run_ingest_buildings_with_terminal_write(
    crisis_id: uuid.UUID,
    job: BuildingIngestJob,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> int:
    """Run the ingest and record the terminal `crisis_jobs` row, then re-raise any failure.

    Catches BaseException because arq's job_timeout cancels with CancelledError;
    missing it would leave the row stuck at 'running' and block re-runs. A failed
    terminal write never masks the original exception.
    """
    try:
        result = await job.run(crisis_id)
    except BaseException as exc:
        logger.exception("ingest_buildings failed crisis_id=%s", crisis_id)
        try:
            await _write_terminal_row(
                sessionmaker, crisis_id, status="failed", error=format_terminal_error(exc)
            )
        except Exception:
            logger.exception("ingest_buildings terminal-row write failed crisis_id=%s", crisis_id)
        raise
    await _write_terminal_row(sessionmaker, crisis_id, status="succeeded", error=None)
    return result


async def _write_terminal_row(
    sessionmaker: async_sessionmaker[AsyncSession],
    crisis_id: uuid.UUID,
    *,
    status: str,
    error: str | None,
) -> None:
    """On success, also activates the crisis in the same transaction if it was waiting on ingest."""
    async with sessionmaker() as session, session.begin():
        await session.execute(
            text(
                "insert into public.crisis_jobs "
                "  (crisis_id, job_type, status, error, started_at, ended_at) "
                "values (:id, 'ingest_buildings', :status, :error, now(), now()) "
                "on conflict (crisis_id, job_type) do update set "
                "  status = :status, error = :error, ended_at = now()"
            ),
            {"id": str(crisis_id), "status": status, "error": error},
        )
        if status == "succeeded":
            flipped = (
                await session.execute(
                    text(
                        "update public.crises "
                        "   set status = 'active', "
                        "       activate_at = null, "
                        "       activate_on_ingest_success = false "
                        " where id = :id "
                        "   and status = 'inactive' "
                        "   and activate_on_ingest_success = true "
                        " returning id"
                    ),
                    {"id": str(crisis_id)},
                )
            ).first()
            if flipped is not None:
                logger.info("ingest-driven activation: crisis %s went live", crisis_id)
