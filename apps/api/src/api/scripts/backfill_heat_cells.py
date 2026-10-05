"""One-off backfill: rebuild `public.heat_cells` from `public.reports`.

Usage:

    uv run python -m api.scripts.backfill_heat_cells
    uv run python -m api.scripts.backfill_heat_cells --crisis-id <uuid>

Each crisis is rebuilt in its own transaction (delete then insert), so a
partial run can simply be re-run.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.core.config import get_settings
from api.core.h3 import cell_for_location

logger = logging.getLogger("backfill_heat_cells")


@dataclass(slots=True)
class _Agg:
    report_count: int = 0
    minimal_count: int = 0
    partial_count: int = 0
    complete_count: int = 0
    latest_at: datetime | None = None


_LIST_CRISES_SQL = text("select id from public.crises")

_SELECT_REPORTS_SQL = text(
    """
    select damage_class, created_at,
           st_y(location::geometry) as lat,
           st_x(location::geometry) as lng
      from public.reports
     where crisis_id = :crisis_id
       and location is not null
    """
)

_DELETE_CELLS_SQL = text("delete from public.heat_cells where crisis_id = :crisis_id")

_INSERT_CELL_SQL = text(
    """
    insert into public.heat_cells
        (crisis_id, h3_cell, report_count,
         minimal_count, partial_count, complete_count, latest_at)
    values
        (:crisis_id, :h3_cell, :report_count,
         :minimal_count, :partial_count, :complete_count, :latest_at)
    """
)


async def backfill_crisis(session: AsyncSession, crisis_id: uuid.UUID) -> int:
    rows = (await session.execute(_SELECT_REPORTS_SQL, {"crisis_id": str(crisis_id)})).all()
    cells: dict[int, _Agg] = defaultdict(_Agg)
    for row in rows:
        cell = cell_for_location(row.lat, row.lng)
        agg = cells[cell]
        agg.report_count += 1
        if row.damage_class == "minimal":
            agg.minimal_count += 1
        elif row.damage_class == "partial":
            agg.partial_count += 1
        elif row.damage_class == "complete":
            agg.complete_count += 1
        if agg.latest_at is None or row.created_at > agg.latest_at:
            agg.latest_at = row.created_at

    await session.execute(_DELETE_CELLS_SQL, {"crisis_id": str(crisis_id)})
    for cell, agg in cells.items():
        assert agg.latest_at is not None
        await session.execute(
            _INSERT_CELL_SQL,
            {
                "crisis_id": str(crisis_id),
                "h3_cell": cell,
                "report_count": agg.report_count,
                "minimal_count": agg.minimal_count,
                "partial_count": agg.partial_count,
                "complete_count": agg.complete_count,
                "latest_at": agg.latest_at,
            },
        )
    await session.commit()
    return len(cells)


async def _run(crisis_id: uuid.UUID | None) -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessionmaker() as session:
            if crisis_id is None:
                ids = [r.id for r in (await session.execute(_LIST_CRISES_SQL)).all()]
            else:
                ids = [crisis_id]

        for cid in ids:
            async with sessionmaker() as session:
                cells = await backfill_crisis(session, cid)
            logger.info("backfilled crisis_id=%s cells=%d", cid, cells)
    finally:
        await engine.dispose()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--crisis-id",
        type=uuid.UUID,
        default=None,
        help="Backfill one crisis. Omit to walk every crisis in the DB.",
    )
    args = parser.parse_args()
    asyncio.run(_run(args.crisis_id))


if __name__ == "__main__":
    main()
