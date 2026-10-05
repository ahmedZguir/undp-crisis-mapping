"""Unit tests for the DB-free per-cell impact merge (`_merge_impact_cells`).

Guards the §10 enrichment that the live analysis tab paints: that displaced
comes from the WorldPop population band where the cell is covered and falls back
to the occupancy band otherwise, that dollars pass through only where LitPop
covered the cell, and that cells with no damage keep the empty-impact defaults.
The SQL that produces the `ImpactCellRow`s is covered DB-backed in
`tests/admin/test_analysis_metrics.py`.
"""

from __future__ import annotations

from api.analysis import data
from api.analysis.builder import _merge_impact_cells  # pyright: ignore[reportPrivateUsage]
from api.analysis.constants import OCCUPANTS_PER_BUILDING
from api.analysis.metrics import MeshCell


def _mesh_cell(ix: int, iy: int) -> MeshCell:
    # The merge keys off the paired CellRow's (ix, iy), not the MeshCell, so the
    # MeshCell's own coords/stats are immaterial here — only that it threads
    # through unchanged except for the impact fields.
    return MeshCell(
        lat=(iy + 0.5) * 0.02,
        lon=(ix + 0.5) * 0.02,
        observed=0,
        expected=0.0,
        smoothed_rate=0.0,
        residual_z=0.0,
        gi_z=0.0,
        blind_spot=False,
    )


def _cell_row(ix: int, iy: int) -> data.CellRow:
    return data.CellRow(
        ix=ix,
        iy=iy,
        lon=(ix + 0.5) * 0.02,
        lat=(iy + 0.5) * 0.02,
        stock=10.0,
        observed=0,
        minimal=0,
        partial=0,
        complete=0,
    )


def test_merge_scales_displaced_and_passes_dollars_through() -> None:
    """A LitPop-covered cell: displaced = count x occupancy band, dollars pass."""
    cells = [_cell_row(1, 1)]
    mesh = [_mesh_cell(1, 1)]
    impact = {
        (1, 1): data.ImpactCellRow(
            ix=1,
            iy=1,
            displacing_buildings=40,
            economic_low_usd=1_000_000.0,
            economic_high_usd=2_500_000.0,
            economic_available=True,
            displaced_low=0.0,
            displaced_high=0.0,
            population_available=False,
        )
    }

    merged = _merge_impact_cells(mesh, cells, impact)

    assert len(merged) == 1
    c = merged[0]
    assert c.affected_buildings == 40
    assert c.displaced_low == round(40 * OCCUPANTS_PER_BUILDING.low)
    assert c.displaced_high == round(40 * OCCUPANTS_PER_BUILDING.high)
    assert c.economic_available is True
    assert c.economic_low_usd == 1_000_000.0
    assert c.economic_high_usd == 2_500_000.0


def test_merge_population_available_uses_worldpop_band() -> None:
    """A population-covered cell: displaced comes from the WorldPop band (rounded),
    not the occupancy fallback; the building count still drives affected_buildings."""
    cells = [_cell_row(3, 4)]
    mesh = [_mesh_cell(3, 4)]
    impact = {
        (3, 4): data.ImpactCellRow(
            ix=3,
            iy=4,
            displacing_buildings=20,
            economic_low_usd=0.0,
            economic_high_usd=0.0,
            economic_available=False,
            displaced_low=18.4,
            displaced_high=37.6,
            population_available=True,
        )
    }

    merged = _merge_impact_cells(mesh, cells, impact)

    c = merged[0]
    assert c.affected_buildings == 20
    # WorldPop band, rounded — not 20 x occupancy
    assert c.displaced_low == 18
    assert c.displaced_high == 38


def test_merge_uncovered_cell_in_covered_crisis_zero_floors() -> None:
    """Once any cell has population, the crisis is on the population path, so a
    damaged cell WorldPop saw no settlement in shows 0 (honest floor) - NOT the
    3-6 fallback. Keeps the map's sum consistent with the zero-floored headline."""
    cells = [_cell_row(1, 1), _cell_row(9, 9)]
    mesh = [_mesh_cell(1, 1), _mesh_cell(9, 9)]
    impact = {
        # covered cell
        (1, 1): data.ImpactCellRow(
            ix=1,
            iy=1,
            displacing_buildings=20,
            economic_low_usd=0.0,
            economic_high_usd=0.0,
            economic_available=False,
            displaced_low=18.4,
            displaced_high=37.6,
            population_available=True,
        ),
        # damaged but no WorldPop settlement → 0 people, must stay 0
        (9, 9): data.ImpactCellRow(
            ix=9,
            iy=9,
            displacing_buildings=15,
            economic_low_usd=0.0,
            economic_high_usd=0.0,
            economic_available=False,
            displaced_low=0.0,
            displaced_high=0.0,
            population_available=False,
        ),
    }

    merged = {(c.lon, c.lat): c for c in _merge_impact_cells(mesh, cells, impact)}
    covered = merged[((1 + 0.5) * 0.02, (1 + 0.5) * 0.02)]
    uncovered = merged[((9 + 0.5) * 0.02, (9 + 0.5) * 0.02)]
    assert (covered.displaced_low, covered.displaced_high) == (18, 38)
    # honest floor, not 15 x occupancy band
    assert uncovered.affected_buildings == 15
    assert (uncovered.displaced_low, uncovered.displaced_high) == (0, 0)


def test_merge_litpop_absent_keeps_displaced_drops_dollars() -> None:
    """Degradation path: no LitPop → economic is None, displaced still computed."""
    cells = [_cell_row(2, 3)]
    mesh = [_mesh_cell(2, 3)]
    impact = {
        (2, 3): data.ImpactCellRow(
            ix=2,
            iy=3,
            displacing_buildings=12,
            economic_low_usd=0.0,
            economic_high_usd=0.0,
            economic_available=False,
            displaced_low=0.0,
            displaced_high=0.0,
            population_available=False,
        )
    }

    merged = _merge_impact_cells(mesh, cells, impact)

    c = merged[0]
    assert c.affected_buildings == 12
    assert c.displaced_low == round(12 * OCCUPANTS_PER_BUILDING.low)
    assert c.displaced_high == round(12 * OCCUPANTS_PER_BUILDING.high)
    assert c.economic_available is False
    assert c.economic_low_usd is None
    assert c.economic_high_usd is None


def test_merge_cell_without_impact_keeps_empty_defaults() -> None:
    """A cell with no damaged buildings is absent from the impact map → defaults."""
    cells = [_cell_row(5, 5)]
    mesh = [_mesh_cell(5, 5)]

    merged = _merge_impact_cells(mesh, cells, {})

    c = merged[0]
    assert c.affected_buildings == 0
    assert c.displaced_low == 0
    assert c.displaced_high == 0
    assert c.economic_available is False
    assert c.economic_low_usd is None
    assert c.economic_high_usd is None


def test_merge_preserves_order_and_count() -> None:
    """Output is 1:1 with the input mesh, in order (the residual roll-up relies
    on the cells/mesh staying aligned)."""
    cells = [_cell_row(i, 0) for i in range(4)]
    mesh = [_mesh_cell(i, 0) for i in range(4)]
    impact = {
        (1, 0): data.ImpactCellRow(
            ix=1,
            iy=0,
            displacing_buildings=3,
            economic_low_usd=0.0,
            economic_high_usd=0.0,
            economic_available=False,
            displaced_low=0.0,
            displaced_high=0.0,
            population_available=False,
        )
    }

    merged = _merge_impact_cells(mesh, cells, impact)

    assert [c.lon for c in merged] == [c.lon for c in mesh]
    assert merged[1].affected_buildings == 3
    assert all(merged[i].affected_buildings == 0 for i in (0, 2, 3))
