"""Unit tests for the building-density grid cell math.

The click-time count is `sum(n)` over the cells a bbox covers, so the index
math in `cell_range_for_bbox` is the contract that must line up exactly with
the offline build (which assigns each building to `floor(centre / step)`).
Pure functions, no DB — see `test_buildings_ingest_integration.py` for the
DB-backed paths.
"""

from __future__ import annotations

import math

from api.buildings.density_grid import DENSITY_GRID_STEP_DEG, cell_range_for_bbox


def test_cell_range_is_floor_of_coord_over_step() -> None:
    step = DENSITY_GRID_STEP_DEG
    # A bbox wholly inside one cell collapses to a single (ix, iy).
    x = 50.5
    y = 26.1
    ix0, iy0, ix1, iy1 = cell_range_for_bbox(x, y, x + step / 4, y + step / 4)
    assert ix0 == ix1 == math.floor(x / step)
    assert iy0 == iy1 == math.floor(y / step)


def test_cell_range_is_inclusive_on_both_ends() -> None:
    # Spanning ~3 cells in x and ~2 in y must yield inclusive index ranges so
    # the SQL `between ix0 and ix1` sums every touched cell.
    step = DENSITY_GRID_STEP_DEG
    xmin, ymin = 10.0, 10.0
    xmax, ymax = xmin + step * 2.5, ymin + step * 1.5
    ix0, iy0, ix1, iy1 = cell_range_for_bbox(xmin, ymin, xmax, ymax)
    assert (ix1 - ix0, iy1 - iy0) == (2, 1)


def test_cell_range_handles_negative_coordinates() -> None:
    # Western/southern hemisphere: floor must round toward -inf, not zero, so
    # the range stays ordered (ix0 <= ix1).
    step = DENSITY_GRID_STEP_DEG
    ix0, iy0, ix1, iy1 = cell_range_for_bbox(-0.03, -0.03, -0.01, -0.01)
    assert ix0 <= ix1 and iy0 <= iy1
    assert ix0 == math.floor(-0.03 / step)
    assert ix1 == math.floor(-0.01 / step)
