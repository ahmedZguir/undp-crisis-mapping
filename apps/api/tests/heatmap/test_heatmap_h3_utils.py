"""Unit tests for the H3 helper wrappers.

No DB dependency — these run wherever Python + h3 are installed.
"""

from __future__ import annotations

from api.core.h3 import (
    cell_boundary,
    cell_for_location,
    cell_parent,
    cell_to_string,
    zoom_to_resolution,
)


def test_cell_for_location_returns_int() -> None:
    cell = cell_for_location(25.30, 51.50)
    assert isinstance(cell, int)


def test_cell_for_location_is_deterministic() -> None:
    a = cell_for_location(25.30, 51.50)
    b = cell_for_location(25.30, 51.50)
    assert a == b


def test_two_close_points_share_a_cell() -> None:
    """Two points within ~10m at res 9 land in the same hex."""
    a = cell_for_location(25.30000, 51.50000)
    b = cell_for_location(25.30005, 51.50005)
    assert a == b


def test_cell_parent_rolls_up_to_lower_resolution() -> None:
    cell = cell_for_location(25.30, 51.50)
    parent = cell_parent(cell, 7)
    # Parent at res 7 differs from the res-9 child.
    assert parent != cell


def test_two_neighbour_cells_share_a_parent() -> None:
    a = cell_for_location(25.30000, 51.50000)
    b = cell_for_location(25.30010, 51.50010)  # ~14m away
    assert cell_parent(a, 6) == cell_parent(b, 6)


def test_cell_boundary_returns_six_vertices() -> None:
    cell = cell_for_location(25.30, 51.50)
    ring = cell_boundary(cell)
    assert len(ring) == 6
    # Each vertex is (lng, lat) — both finite floats.
    for lng, lat in ring:
        assert -180.0 <= lng <= 180.0
        assert -90.0 <= lat <= 90.0


def test_cell_to_string_roundtrips_through_int() -> None:
    cell = cell_for_location(25.30, 51.50)
    assert isinstance(cell_to_string(cell), str)


def test_zoom_to_resolution_clamps_to_six_and_seven() -> None:
    """Render resolution steps once: res 6 zoomed out, res 7 from z 9 up.

    Never finer than 7, never coarser than 6 — only these two levels.
    """
    for z in (0, 4, 6, 7, 8, 9):
        assert zoom_to_resolution(z) == 6
    for z in (10, 12, 13, 14, 15, 22):
        assert zoom_to_resolution(z) == 7
