"""Per-release building counts on a coarse lon/lat grid, for fast ingest estimates.

The cell size must match api/scripts/build_density_grid.py; changing it requires
rebuilding the grid.
"""

from __future__ import annotations

import math

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# ~2.2 km at the equator: fine enough for small crises like Bahrain, coarse
# enough that the global grid stays a few million rows.
DENSITY_GRID_STEP_DEG = 0.02


def cell_range_for_bbox(
    xmin: float, ymin: float, xmax: float, ymax: float
) -> tuple[int, int, int, int]:
    """Inclusive cell index range (ix0, iy0, ix1, iy1) covering an AOI bbox."""
    step = DENSITY_GRID_STEP_DEG
    return (
        math.floor(xmin / step),
        math.floor(ymin / step),
        math.floor(xmax / step),
        math.floor(ymax / step),
    )


async def lookup_density_count(
    session: AsyncSession,
    *,
    ix0: int,
    iy0: int,
    ix1: int,
    iy1: int,
    preferred_release: str,
) -> int | None:
    """Sum grid cells covering a bbox; None only if no grid has been built.

    Uses preferred_release if present, else the newest release in the grid.
    The release is resolved first and bound as a constant so the
    (release, ix, iy) primary key serves the range sum.
    """
    release = (
        await session.execute(
            text(
                "select case when exists ("
                "    select 1 from public.building_density_grid where release = :pref"
                "  ) then :pref "
                "  else (select max(release) from public.building_density_grid) end as rel"
            ),
            {"pref": preferred_release},
        )
    ).scalar()
    if release is None:
        return None

    total = (
        await session.execute(
            text(
                "select coalesce(sum(n), 0)::bigint "
                "from public.building_density_grid "
                "where release = :rel "
                "  and ix between :ix0 and :ix1 "
                "  and iy between :iy0 and :iy1"
            ),
            {"rel": release, "ix0": ix0, "ix1": ix1, "iy0": iy0, "iy1": iy1},
        )
    ).scalar()
    return int(total or 0)


__all__ = ["DENSITY_GRID_STEP_DEG", "cell_range_for_bbox", "lookup_density_count"]
