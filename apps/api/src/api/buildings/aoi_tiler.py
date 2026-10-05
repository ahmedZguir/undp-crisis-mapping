"""Split a large AOI into grid-cell tiles so ingest commits and holds rows one tile
at a time.
"""

from __future__ import annotations

import math
from collections.abc import Iterator

from shapely import wkb as shapely_wkb
from shapely.geometry import box

# ~220 km: a small country is one tile, a dense cell stays a bounded working set.
DEFAULT_TILE_DEGREES = 2.0


def tile_polygon(
    polygon_wkb: bytes,
    cell_degrees: float = DEFAULT_TILE_DEGREES,
) -> list[bytes]:
    """Clip the AOI to grid cells, returning WKB per non-empty cell.

    Returns [polygon_wkb] unchanged when the AOI fits in one cell.
    """
    geom = shapely_wkb.loads(polygon_wkb)
    minx, miny, maxx, maxy = geom.bounds

    if (maxx - minx) <= cell_degrees and (maxy - miny) <= cell_degrees:
        return [polygon_wkb]

    tiles: list[bytes] = []
    for x0, y0 in _cell_origins(minx, miny, maxx, maxy, cell_degrees):
        cell = box(x0, y0, min(x0 + cell_degrees, maxx), min(y0 + cell_degrees, maxy))
        clipped = geom.intersection(cell)
        if clipped.is_empty or clipped.area <= 0.0:
            continue
        tiles.append(shapely_wkb.dumps(clipped))  # pyright: ignore[reportUnknownMemberType]
    return tiles


def _cell_origins(
    minx: float,
    miny: float,
    maxx: float,
    maxy: float,
    cell_degrees: float,
) -> Iterator[tuple[float, float]]:
    """Lower-left corner of each cell; integer counts avoid float drift."""
    cols = max(1, math.ceil((maxx - minx) / cell_degrees))
    rows = max(1, math.ceil((maxy - miny) / cell_degrees))
    for col in range(cols):
        for row in range(rows):
            yield minx + col * cell_degrees, miny + row * cell_degrees


__all__ = ["DEFAULT_TILE_DEGREES", "tile_polygon"]
