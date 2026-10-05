from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from api.core.config import get_settings
from api.reports.deletion import CitizenReportDeletionService
from api.workers.context import sessionmaker_from_ctx, storage_from_ctx

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from api.core.storage import ExportPartStore

logger = logging.getLogger(__name__)


async def tick_scheduled_activations(ctx: dict[str, object]) -> int:
    """Activate inactive crises whose `activate_at` is due.

    The API's process-local crisis cache is not invalidated; its TTL picks up
    the change.
    """
    sessionmaker = sessionmaker_from_ctx(ctx)
    async with sessionmaker() as session, session.begin():
        rows = (
            await session.execute(
                text(
                    "update public.crises "
                    "   set status = 'active', "
                    "       activate_at = null, "
                    "       activate_on_ingest_success = false "
                    " where status = 'inactive' "
                    "   and activate_at is not null "
                    "   and activate_at <= now() "
                    " returning id"
                ),
            )
        ).all()
    if rows:
        logger.info("scheduled activation: flipped %d crises", len(rows))
    return len(rows)


async def tick_report_retention(ctx: dict[str, object]) -> int:
    """Hard-delete reports under crises archived longer than the retention window.

    The crisis row, geometry, form schema and PDF analyses are kept. Returns the
    number of reports purged.
    """
    window_days = get_settings().report_retention_days
    sessionmaker = sessionmaker_from_ctx(ctx)

    await _sweep_expired_exports(sessionmaker, storage_from_ctx(ctx))
    async with sessionmaker() as session:
        due = (
            (
                await session.execute(
                    text(
                        "select id from public.crises "
                        " where status = 'archived' "
                        "   and archived_at is not null "
                        "   and archived_at <= now() - make_interval(days => :d)"
                    ),
                    {"d": window_days},
                )
            )
            .scalars()
            .all()
        )

    if not due:
        return 0

    service = CitizenReportDeletionService(sessionmaker, storage_from_ctx(ctx))
    purged = 0
    for crisis_id in due:
        purged += await service.delete_by_crisis(crisis_id, reason="retention")
    logger.info("report retention: purged %d reports across %d crises", purged, len(due))
    return purged


async def _sweep_expired_exports(
    sessionmaker: async_sessionmaker[AsyncSession], store: ExportPartStore
) -> int:
    """Delete zip parts of expired exports and mark the rows 'expired'.

    The row is kept as an audit record. If any part delete fails, the row is
    left for the next sweep so blobs are not orphaned.
    """
    from api.core.storage import StorageError

    async with sessionmaker() as session:
        due = (
            await session.execute(
                text(
                    "select id, parts from public.report_exports "
                    " where status = 'succeeded' "
                    "   and expires_at is not null "
                    "   and expires_at <= now()"
                )
            )
        ).all()

    expired = 0
    for row in due:
        parts: list[dict[str, Any]] = row.parts or []
        keys = [p["key"] for p in parts if isinstance(p.get("key"), str)]
        deleted_all = True
        for key in keys:
            try:
                await store.delete_export_part(key)
            except StorageError:
                logger.warning(
                    "export retention: part delete failed export=%s key=%s; will retry next sweep",
                    row.id,
                    key,
                    exc_info=True,
                )
                deleted_all = False
                break
        if not deleted_all:
            continue
        async with sessionmaker() as session, session.begin():
            await session.execute(
                text(
                    "update public.report_exports "
                    "   set status = 'expired', parts = null "
                    " where id = :id and status = 'succeeded'"
                ),
                {"id": str(row.id)},
            )
        expired += 1

    if expired:
        logger.info("export retention: expired %d photo-export bundle(s)", expired)
    return expired
