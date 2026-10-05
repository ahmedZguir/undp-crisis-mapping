"""Typed wrappers over the untyped h3 package. Cells are stored as bigints."""

from __future__ import annotations

from typing import Any, cast

import h3 as _h3_typed  # pyright: ignore[reportMissingTypeStubs]

_h3: Any = _h3_typed

# ~0.1 km² hexagons, ~174 m edge.
STORED_RESOLUTION = 9

# Render res 7 (~5 km²) from this zoom up, res 6 (~36 km²) below it.
_RES7_MIN_ZOOM = 10


def cell_for_location(lat: float, lng: float, resolution: int = STORED_RESOLUTION) -> int:
    return cast(int, _h3.str_to_int(_h3.latlng_to_cell(lat, lng, resolution)))


def cell_parent(cell: int, target_resolution: int) -> int:
    parent = _h3.cell_to_parent(_h3.int_to_str(cell), target_resolution)
    return cast(int, _h3.str_to_int(parent))


def cell_boundary(cell: int) -> list[tuple[float, float]]:
    """Hex ring as (lng, lat) pairs; h3 itself returns (lat, lng)."""
    raw: list[tuple[float, float]] = _h3.cell_to_boundary(_h3.int_to_str(cell))
    return [(lng, lat) for lat, lng in raw]


def cell_centre_in_bbox(cell: int, bbox: tuple[float, float, float, float]) -> bool:
    lat, lng = cast(tuple[float, float], _h3.cell_to_latlng(_h3.int_to_str(cell)))
    min_lng, min_lat, max_lng, max_lat = bbox
    return min_lat <= lat <= max_lat and min_lng <= lng <= max_lng


def cell_to_string(cell: int) -> str:
    return cast(str, _h3.int_to_str(cell))


def zoom_to_resolution(z: int) -> int:
    return 7 if z >= _RES7_MIN_ZOOM else 6
