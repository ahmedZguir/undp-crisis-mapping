"""Hard deletion of reports with matching heat_cells decrements and photo cleanup."""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from collections.abc import Sequence
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.core.h3 import cell_for_location
from api.core.storage import PhotoStorageDeleter

_logger = logging.getLogger(__name__)


# reports has no h3_cell column, so lat/lng are returned to recompute it.
_DELETE_REPORTS_SQL = text(
    """
    delete from public.reports
     where client_id = :client_id
    returning
        id,
        photo_path,
        crisis_id,
        damage_class,
        st_y(location::geometry) as report_lat,
        st_x(location::geometry) as report_lng
    """
)


# Owner check is in SQL: a wrong client_id and an unknown id both delete nothing.
_DELETE_ONE_REPORT_SQL = text(
    """
    delete from public.reports
     where id        = :report_id
       and client_id = :client_id
    returning
        id,
        photo_path,
        crisis_id,
        damage_class,
        st_y(location::geometry) as report_lat,
        st_x(location::geometry) as report_lng
    """
)


# Inverse of the writer's upsert. latest_at is not rolled back; recomputing it
# from the remaining rows is not worth the cost.
_DECREMENT_HEAT_CELL_SQL = text(
    """
    update public.heat_cells
       set report_count   = report_count   - :total,
           minimal_count  = minimal_count  - :minimal,
           partial_count  = partial_count  - :partial,
           complete_count = complete_count - :complete
     where crisis_id = :crisis_id
       and h3_cell   = :h3_cell
    """
)


# Photos are content-addressed, so several reports can share one photo_path.
_PHOTO_PATH_STILL_REFERENCED_SQL = text(
    """
    select 1
      from public.reports
     where photo_path = :photo_path
     limit 1
    """
)


# Only cells touched by this delete are checked.
_CLEANUP_EMPTY_CELLS_SQL = text(
    """
    delete from public.heat_cells
     where crisis_id = :crisis_id
       and h3_cell   = :h3_cell
       and report_count <= 0
    """
)


# Retention purge. Enrichment sidecars go via ON DELETE CASCADE.
_DELETE_REPORTS_BY_CRISIS_SQL = text(
    """
    delete from public.reports
     where crisis_id = :crisis_id
    returning
        id,
        photo_path,
        crisis_id,
        damage_class,
        st_y(location::geometry) as report_lat,
        st_x(location::geometry) as report_lng
    """
)


# PII-free audit row, one per crisis, written in the delete's transaction.
_INSERT_DISPOSAL_LOG_SQL = text(
    """
    insert into public.data_disposal_log
        (crisis_id, reports_count, photos_count, reason)
    values
        (:crisis_id, :reports_count, :photos_count, :reason)
    """
)


class CitizenReportDeletionService:
    """Hard delete, heat_cells decrement, photo cleanup and disposal log.

    Citizen deletes and the retention purge share `_purge` so the bookkeeping
    cannot diverge.
    """

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        storage: PhotoStorageDeleter,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._storage = storage

    async def delete_by_client(self, client_id: uuid.UUID) -> None:
        """Erase every report for client_id.

        The delete and storage loop run even when nothing matches, to keep
        timing similar for the match and no-match cases.
        """
        await self._purge(_DELETE_REPORTS_SQL, {"client_id": str(client_id)}, reason="manual")

    async def delete_one(self, report_id: uuid.UUID, client_id: uuid.UUID) -> None:
        """Erase one report only if client_id owns it; otherwise a no-op."""
        await self._purge(
            _DELETE_ONE_REPORT_SQL,
            {"report_id": str(report_id), "client_id": str(client_id)},
            reason="manual",
        )

    async def delete_by_crisis(self, crisis_id: uuid.UUID, *, reason: str = "retention") -> int:
        """Hard-delete every report under a crisis. Idempotent; returns rows deleted."""
        return await self._purge(
            _DELETE_REPORTS_BY_CRISIS_SQL, {"crisis_id": str(crisis_id)}, reason=reason
        )

    async def _purge(self, delete_sql: Any, params: dict[str, Any], *, reason: str) -> int:
        """Delete, decrement heat_cells and log in one transaction, then clean up photos.

        Returns the number of reports deleted.
        """
        async with self._sessionmaker() as session:
            rows = (await session.execute(delete_sql, params)).all()
            await self._apply_heat_cell_decrements(session, rows)
            await self._write_disposal_log(session, rows, reason)
            await session.commit()

        await self._cleanup_photos(rows)
        return len(rows)

    async def _write_disposal_log(
        self, session: AsyncSession, rows: Sequence[Any], reason: str
    ) -> None:
        """Write one data_disposal_log row per crisis; a citizen delete can span several."""
        if not rows:
            return
        reports_by_crisis: dict[str, int] = defaultdict(int)
        photos_by_crisis: dict[str, int] = defaultdict(int)
        for row in rows:
            crisis_id_str = str(row.crisis_id)
            reports_by_crisis[crisis_id_str] += 1
            if row.photo_path:
                photos_by_crisis[crisis_id_str] += 1

        for crisis_id_str, reports_count in reports_by_crisis.items():
            await session.execute(
                _INSERT_DISPOSAL_LOG_SQL,
                {
                    "crisis_id": crisis_id_str,
                    "reports_count": reports_count,
                    "photos_count": photos_by_crisis.get(crisis_id_str, 0),
                    "reason": reason,
                },
            )

    async def _apply_heat_cell_decrements(self, session: AsyncSession, rows: Sequence[Any]) -> None:
        buckets: dict[tuple[str, int], _CellDelta] = defaultdict(_CellDelta)
        for row in rows:
            if row.report_lat is None or row.report_lng is None:
                continue
            h3 = cell_for_location(float(row.report_lat), float(row.report_lng))
            buckets[(str(row.crisis_id), h3)].add(row.damage_class)

        for (crisis_id_str, h3_cell), delta in buckets.items():
            params = {
                "crisis_id": crisis_id_str,
                "h3_cell": h3_cell,
                "total": delta.total,
                "minimal": delta.minimal,
                "partial": delta.partial,
                "complete": delta.complete,
            }
            await session.execute(_DECREMENT_HEAT_CELL_SQL, params)
            await session.execute(
                _CLEANUP_EMPTY_CELLS_SQL,
                {"crisis_id": crisis_id_str, "h3_cell": h3_cell},
            )

    async def _cleanup_photos(self, rows: Sequence[Any]) -> None:
        """Delete photo blobs no surviving report references, after commit.

        Runs after the commit so the reference check sees only survivors. Failures
        are logged and leave an orphan blob; the DB delete stands. An upload can
        adopt a blob in the instant its last reference is deleted, leaving one
        report with a broken image.
        """
        paths = {row.photo_path for row in rows if row.photo_path}
        if not paths:
            return

        async with self._sessionmaker() as session:
            for photo_path in paths:
                still_referenced = (
                    await session.execute(
                        _PHOTO_PATH_STILL_REFERENCED_SQL, {"photo_path": photo_path}
                    )
                ).first() is not None
                if still_referenced:
                    continue
                try:
                    await self._storage.delete_photo(photo_path)
                except Exception as exc:
                    _logger.warning(
                        "photo.delete.failed path=%s err=%s",
                        photo_path,
                        exc,
                    )


class _CellDelta:
    """Per (crisis_id, h3_cell) decrement accumulator."""

    __slots__ = ("complete", "minimal", "partial", "total")

    def __init__(self) -> None:
        self.total = 0
        self.minimal = 0
        self.partial = 0
        self.complete = 0

    def add(self, damage_class: str) -> None:
        self.total += 1
        if damage_class == "minimal":
            self.minimal += 1
        elif damage_class == "partial":
            self.partial += 1
        elif damage_class == "complete":
            self.complete += 1
