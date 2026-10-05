"""Progress and terminal writes for admin-triggered job rows.

Covers tables shaped like `crisis_reports` and `report_exports`: a `status`,
a nullable `phase` and `error`, and `ended_at`. Each write runs in its own
transaction so the PWA poll sees progress as it happens.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.workers.sidecars import execute_write

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class JobRow:
    sessionmaker: async_sessionmaker[AsyncSession]
    # Interpolated into SQL; always a literal from the calling job.
    table: str
    row_id: uuid.UUID
    # Log prefix, e.g. "generate_crisis_report report_id".
    log_label: str

    async def update(self, assignments: str, params: dict[str, Any] | None = None) -> None:
        await execute_write(
            self.sessionmaker,
            f"update public.{self.table} set {assignments} where id = :id",
            {"id": str(self.row_id), **(params or {})},
        )

    async def try_update(self, what: str, assignments: str, params: dict[str, Any]) -> None:
        """Best-effort write; a failure is logged, never raised."""
        try:
            await self.update(assignments, params)
        except Exception:
            logger.warning(
                "%s=%s %s write failed (non-fatal)",
                self.log_label,
                self.row_id,
                what,
                exc_info=True,
            )

    async def set_phase(self, phase: str) -> None:
        await self.try_update("phase", "phase = :phase", {"phase": phase})

    async def mark_succeeded(self, assignments: str, params: dict[str, Any]) -> None:
        await self.update(
            "status = 'succeeded', phase = null, error = null, ended_at = now(), " + assignments,
            params,
        )

    async def safe_mark_failed(self, error: str) -> None:
        """Never raises, so the bookkeeping can't mask the job's own exception."""
        try:
            await self.update(
                "status = 'failed', error = :error, ended_at = now()", {"error": error}
            )
        except BaseException:
            logger.exception("%s=%s terminal-row write failed", self.log_label, self.row_id)
