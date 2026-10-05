"""Best-effort ingest progress writes, each in its own transaction so they never
interact with the ingest's upserts.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)

_UPDATE_PROGRESS_SQL = text(
    """
    update public.crisis_jobs
       set phase = :phase,
           progress_count = :count,
           progress_total = coalesce(:total, progress_total)
     where crisis_id = :id
       and job_type = 'ingest_buildings'
    """
)


class CrisisJobProgress:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def update(
        self,
        crisis_id: uuid.UUID,
        *,
        phase: str,
        count: int,
        total: int | None = None,
    ) -> None:
        """Set the running job's phase and counters. total=None keeps the
        previous total. Failures are logged and swallowed."""
        try:
            async with self._sessionmaker() as session, session.begin():
                await session.execute(
                    _UPDATE_PROGRESS_SQL,
                    {"id": str(crisis_id), "phase": phase, "count": count, "total": total},
                )
        except Exception:
            logger.warning(
                "building-ingest progress write failed crisis_id=%s (non-fatal)",
                crisis_id,
                exc_info=True,
            )


__all__ = ["CrisisJobProgress"]
