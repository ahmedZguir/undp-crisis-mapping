"""Unit tests for `aoi_tiler.tile_polygon` — pure geometry, no DB / no I/O."""

from __future__ import annotations

from shapely import wkb as shapely_wkb
from shapely.geometry import box
from shapely.ops import unary_union

from api.buildings.aoi_tiler import tile_polygon


def _wkb(geom) -> bytes:  # type: ignore[no-untyped-def]
    return shapely_wkb.dumps(geom)  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]


def test_small_polygon_returns_single_tile_unchanged() -> None:
    # A 1x1 degree AOI fits inside the default 2 degree cell -> returned as-is.
    poly = box(0.0, 0.0, 1.0, 1.0)
    tiles = tile_polygon(_wkb(poly))
    assert len(tiles) == 1
    assert tiles[0] == _wkb(poly)


def test_large_polygon_splits_into_grid() -> None:
    # A 5x5 degree AOI at a 2 degree cell -> a 3x3 grid (ceil(5/2)=3 per axis).
    poly = box(0.0, 0.0, 5.0, 5.0)
    tiles = tile_polygon(_wkb(poly), cell_degrees=2.0)
    assert len(tiles) == 9


def test_tiles_cover_the_polygon_without_loss() -> None:
    # The union of the clipped tiles must reconstruct the original area.
    poly = box(0.0, 0.0, 5.0, 5.0)
    tiles = tile_polygon(_wkb(poly), cell_degrees=2.0)
    reunion = unary_union([shapely_wkb.loads(t) for t in tiles])
    assert reunion.equals(poly)


def test_cells_outside_the_polygon_are_dropped() -> None:
    # An L-shaped AOI leaves some grid cells empty; those must not be emitted.
    ell = unary_union([box(0.0, 0.0, 4.0, 1.0), box(0.0, 0.0, 1.0, 4.0)])
    tiles = tile_polygon(_wkb(ell), cell_degrees=2.0)
    # Full 2x2 grid would be 4 cells; the top-right cell (2..4, 2..4) is empty.
    assert len(tiles) == 3
    reunion = unary_union([shapely_wkb.loads(t) for t in tiles])
    assert reunion.equals(ell)
