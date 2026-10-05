"""Unit tests for the DB-free builder helpers (`api.analysis.builder`).

These guard the §3 flag/tier/rank logic and the §5 cell→district roll-up using
small synthetic `DistrictRow` / `CellRow` fixtures — no database, no spatial
library. The spatial math itself is covered in `test_spatial.py`.
"""

from __future__ import annotations

from api.analysis import data
from api.analysis.builder import build_districts, build_mesh_metrics
from api.analysis.constants import RED_COVERAGE_RESIDUAL_Z
from api.analysis.metrics import (
    COMPONENT_COVERAGE,
    COMPONENT_DAMAGE,
    COMPONENT_SERVICES,
    FLAG_BLIND_SPOT,
    FLAG_BLOCKED,
    FLAG_SEVERE,
    District,
)


def _district_row(
    div_id: str,
    *,
    report_count: int = 0,
    minimal: int = 0,
    partial: int = 0,
    complete: int = 0,
    debris_yes: int = 0,
    debris_known: int = 0,
    services_hit: int = 0,
    exposure_buildings: int = 0,
) -> data.DistrictRow:
    return data.DistrictRow(
        division_id=div_id,
        name=div_id,
        centroid_lon=0.0,
        centroid_lat=0.0,
        report_count=report_count,
        minimal=minimal,
        partial=partial,
        complete=complete,
        debris_yes=debris_yes,
        debris_known=debris_known,
        services_hit=services_hit,
        exposure_buildings=exposure_buildings,
    )


def _districts(
    rows: list[data.DistrictRow],
    *,
    cells: list[data.CellRow] | None = None,
    cell_division: dict[tuple[int, int], str] | None = None,
    residual_by_cell: dict[tuple[int, int], tuple[float, float]] | None = None,
    blind_cell_keys: set[tuple[int, int]] | None = None,
) -> list[District]:
    return build_districts(
        rows,
        cells=cells or [],
        cell_division=cell_division or {},
        residual_by_cell=residual_by_cell or {},
        blind_cell_keys=blind_cell_keys or set(),
    )


def test_complete_damage_flags_severe() -> None:
    rows = [_district_row("d1", report_count=10, minimal=9, complete=1)]
    d = _districts(rows)[0]
    assert FLAG_SEVERE in d.flags  # any complete present
    assert COMPONENT_DAMAGE not in d.red_components  # severe_share only 0.1


def test_high_severe_share_flags_severe_and_red_damage() -> None:
    rows = [_district_row("d1", report_count=10, minimal=4, partial=6)]
    d = _districts(rows)[0]
    assert FLAG_SEVERE in d.flags  # severe_share 0.6 >= 0.40
    assert COMPONENT_DAMAGE in d.red_components  # >= 0.25


def test_blocked_flag_on_debris_share() -> None:
    rows = [_district_row("d1", report_count=10, minimal=10, debris_yes=4, debris_known=10)]
    d = _districts(rows)[0]
    assert FLAG_BLOCKED in d.flags  # 0.4 >= 0.30
    assert FLAG_SEVERE not in d.flags


def test_services_hit_is_red() -> None:
    rows = [_district_row("d1", report_count=5, minimal=5, services_hit=2)]
    d = _districts(rows)[0]
    assert COMPONENT_SERVICES in d.red_components


def test_coverage_residual_red_and_rollup() -> None:
    # one division with a single very-under-reported cell
    rows = [_district_row("d1", report_count=1, minimal=1)]
    cell_division = {(0, 0): "d1"}
    residual_by_cell = {(0, 0): (RED_COVERAGE_RESIDUAL_Z - 1.0, 100.0)}
    d = _districts(rows, cell_division=cell_division, residual_by_cell=residual_by_cell)[0]
    assert d.coverage_residual_z < RED_COVERAGE_RESIDUAL_Z
    assert COMPONENT_COVERAGE in d.red_components


def test_blind_spot_flag_when_cell_flagged() -> None:
    rows = [_district_row("d1", report_count=1, minimal=1)]
    cell_division = {(0, 0): "d1"}
    d = _districts(rows, cell_division=cell_division, blind_cell_keys={(0, 0)})[0]
    assert FLAG_BLIND_SPOT in d.flags


def test_volume_leads_ranking_flags_are_display_only() -> None:
    # Ranking is a weighted blended score led by report volume.
    # The severe/blocked flags are still computed and shown, but no longer pull
    # rank — so a large, calm district now outranks a tiny severe one.
    rows = [
        _district_row("calm", report_count=20, minimal=20),
        _district_row("severe", report_count=3, partial=3),  # severe_share 1.0
    ]
    districts = _districts(rows)
    by_id = {d.id: d for d in districts}
    assert by_id["severe"].tier == 0  # flag still computed
    assert by_id["calm"].tier == 1
    assert by_id["calm"].rank < by_id["severe"].rank
    assert by_id["calm"].priority_score > by_id["severe"].priority_score
    assert districts[0].id == "calm" and districts[0].rank == 1


def test_blind_spot_does_not_outrank_confirmed_damage() -> None:
    # A blind spot changes a different decision; it must not be tier 0.
    rows = [_district_row("d1", report_count=1, minimal=1)]
    cell_division = {(0, 0): "d1"}
    d = _districts(rows, cell_division=cell_division, blind_cell_keys={(0, 0)})[0]
    assert FLAG_BLIND_SPOT in d.flags
    assert d.tier == 1  # not the hard-flag top tier


def test_volume_outweighs_a_single_blocked_factor() -> None:
    # "a" is the only one with blocked access, but "b" has 10x the reports and
    # severity points; with report volume leading the weights, "b" still ranks
    # first. (Replaces the old red-component tiebreak, which no longer exists.)
    rows = [
        _district_row("a", report_count=5, partial=5, debris_yes=5, debris_known=5),
        _district_row("b", report_count=50, partial=50),
    ]
    districts = _districts(rows)
    by_id = {d.id: d for d in districts}
    assert by_id["a"].tier == 0 and by_id["b"].tier == 0  # both still flagged
    assert by_id["b"].rank < by_id["a"].rank
    assert by_id["b"].priority_score > by_id["a"].priority_score


def test_debris_lifts_an_otherwise_equal_district() -> None:
    # Identical volume and damage; the district with blocked access scores higher
    # because the debris factor is non-zero only for it.
    rows = [
        _district_row("blocked", report_count=10, partial=10, debris_yes=10, debris_known=10),
        _district_row("clear", report_count=10, partial=10),
    ]
    districts = _districts(rows)
    by_id = {d.id: d for d in districts}
    assert by_id["blocked"].rank < by_id["clear"].rank
    assert by_id["blocked"].priority_score > by_id["clear"].priority_score


def test_severity_points_order_equal_volume_districts() -> None:
    # Same report count, different severity → the more severe district ranks
    # higher via the severity-points factor (complete=3, partial=2, minimal=1).
    rows = [
        _district_row("worse", report_count=10, complete=10),
        _district_row("milder", report_count=10, minimal=10),
    ]
    districts = _districts(rows)
    by_id = {d.id: d for d in districts}
    assert by_id["worse"].rank < by_id["milder"].rank
    assert by_id["worse"].priority_score > by_id["milder"].priority_score


def test_leftover_cells_become_unmapped_fallback() -> None:
    cells = [
        data.CellRow(
            ix=9, iy=9, lon=9.5, lat=9.5, stock=20.0, observed=3, minimal=1, partial=2, complete=0
        )
    ]
    districts = _districts([], cells=cells, residual_by_cell={(9, 9): (0.0, 20.0)})
    assert any(not d.official and d.id == "area:unmapped" for d in districts)
    fb = next(d for d in districts if d.id == "area:unmapped")
    assert fb.report_count == 3
    assert fb.damage.partial == 2


def test_mesh_metrics_empty() -> None:
    mesh_cells, coverage, hotspots, blind = build_mesh_metrics([])
    assert mesh_cells == []
    assert coverage.observed_total == 0
    assert hotspots.hot_cell_count == 0
    assert blind == set()


def test_mesh_metrics_packs_cells() -> None:
    cells = [
        data.CellRow(
            ix=i,
            iy=0,
            lon=float(i),
            lat=0.0,
            stock=30.0,
            observed=o,
            minimal=o,
            partial=0,
            complete=0,
        )
        for i, o in enumerate([0, 5, 5, 5, 5])
    ]
    mesh_cells, coverage, _hot, _blind = build_mesh_metrics(cells)
    assert len(mesh_cells) == 5
    assert coverage.observed_total == 20
    # the silent cell amid active neighbours has the most-negative residual
    assert mesh_cells[0].residual_z == min(mc.residual_z for mc in mesh_cells)
