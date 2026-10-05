"""Deterministic compute core of the analysis report: `build_report_metrics`."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import math
import uuid
from datetime import UTC, datetime

import numpy as np
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.analysis import data
from api.analysis.constants import (
    BLOCKED_DEBRIS_SHARE,
    DAMAGE_RATIO,
    DISPLACEMENT_FRACTION,
    DISPLACING_DAMAGE_CLASSES,
    HOTSPOT_Z_THRESHOLD,
    OCCUPANTS_PER_BUILDING,
    PRIORITY_WEIGHT_DEBRIS,
    PRIORITY_WEIGHT_REPORTS,
    PRIORITY_WEIGHT_SEVERITY,
    RED_COVERAGE_RESIDUAL_Z,
    RED_SERVICES_HIT,
    RED_SEVERE_DAMAGE_SHARE,
    REPORT_POINTS_THRESHOLD,
    SEVERE_DAMAGE_SHARE,
    SEVERITY_POINTS,
)
from api.analysis.metrics import (
    COMPONENT_COVERAGE,
    COMPONENT_DAMAGE,
    COMPONENT_EXPOSURE,
    COMPONENT_REACHABILITY,
    COMPONENT_SERVICES,
    FLAG_BLIND_SPOT,
    FLAG_BLOCKED,
    FLAG_SEVERE,
    AggregateViews,
    BBox,
    CoverageMetrics,
    DamageMix,
    DayCount,
    District,
    HotspotMetrics,
    ImpactEstimates,
    InfraItem,
    InfraMetrics,
    MeshCell,
    ReportMeta,
    ReportMetrics,
    ReportPoint,
)
from api.analysis.spatial import MeshGrid, hot_cold_counts, run_spatial_pipeline

# Per-class damage weight for the blind-spot damage context.
_DAMAGE_SEVERITY_WEIGHT = {"minimal": 0.25, "partial": 0.6, "complete": 1.0}


async def build_report_metrics(session: AsyncSession, crisis_id: uuid.UUID) -> ReportMetrics:
    """Compute `ReportMetrics` for one crisis; safe with few reports or no AOI geometry."""
    crisis = await data.fetch_crisis(session, crisis_id)
    release = await _preferred_release(session, crisis_id)

    crisis_name = crisis.name if crisis else "Unknown crisis"
    crisis_type = crisis.crisis_type if crisis else None
    countries = crisis.countries if crisis else []
    bbox = BBox(crisis.min_lon, crisis.min_lat, crisis.max_lon, crisis.max_lat) if crisis else None
    geometry = json.loads(crisis.geom) if crisis and crisis.geom else None

    cover = await data.fetch_cover(session, crisis_id, release=release)
    as_of = cover.as_of or datetime.now(UTC)

    meta = ReportMeta(
        crisis_id=crisis_id,
        crisis_name=crisis_name,
        crisis_type=crisis_type,
        countries=countries,
        as_of=as_of,
        bbox=bbox,
        geometry=geometry,
        report_count=cover.report_count,
        device_count=cover.device_count,
        building_count=cover.building_count,
        reported_building_count=cover.reported_building_count,
    )

    # Mesh and spatial pipeline
    cells = await data.fetch_mesh_cells(session, crisis_id, release=release)
    # CPU-bound, so run off the event loop.
    _mesh_cells, coverage, hotspots, blind_cell_keys = await asyncio.to_thread(
        build_mesh_metrics, cells
    )

    # Per-cell impact for the live analysis tab; the PDF ignores these fields.
    impact_cell_rows = await data.fetch_impact_cells(
        session,
        crisis_id,
        displacing_classes=DISPLACING_DAMAGE_CLASSES,
        damage_ratio={cls: (rng.low, rng.high) for cls, rng in DAMAGE_RATIO.items()},
        displacement_fraction={
            cls: (rng.low, rng.high) for cls, rng in DISPLACEMENT_FRACTION.items()
        },
        release=release,
    )
    impact_by_cell = {(r.ix, r.iy): r for r in impact_cell_rows}
    coverage = dataclasses.replace(
        coverage, cells=_merge_impact_cells(coverage.cells, cells, impact_by_cell)
    )

    # Districts
    district_rows = await data.fetch_districts(session, crisis_id, release=release)
    cell_division = await data.fetch_division_for_cells(session, crisis_id)
    districts = build_districts(
        district_rows,
        cells=cells,
        cell_division=cell_division,
        residual_by_cell=_residual_by_cell(cells, coverage),
        blind_cell_keys=blind_cell_keys,
    )
    blind_spot_district_ids = sorted({d.id for d in districts if FLAG_BLIND_SPOT in d.flags})
    coverage = CoverageMetrics(
        coverage_pct=meta.coverage_pct,
        observed_total=coverage.observed_total,
        expected_total=coverage.expected_total,
        blind_spot_district_ids=blind_spot_district_ids,
        cells=coverage.cells,
    )

    # Critical infra
    infra = await _build_infra(session, crisis_id)

    # Impact
    impact = await _build_impact(session, crisis_id, release=release)

    # Large crises get SQL rollups instead of raw points; the figures above are
    # the same either way.
    if cover.report_count > REPORT_POINTS_THRESHOLD:
        points: list[ReportPoint] = []
        aggregated: AggregateViews | None = await _build_aggregate_views(session, crisis_id)
    else:
        point_rows = await data.fetch_report_points(session, crisis_id)
        points = [
            ReportPoint(
                lon=p.lon,
                lat=p.lat,
                damage_class=p.damage_class,
                debris=p.debris,
                created_at=p.created_at,
                infra_type=p.infra_type,
                infra_name=p.infra_name,
            )
            for p in point_rows
        ]
        aggregated = None

    crisis_damage = DamageMix(
        minimal=sum(c.minimal for c in cells),
        partial=sum(c.partial for c in cells),
        complete=sum(c.complete for c in cells),
    )

    return ReportMetrics(
        meta=meta,
        damage=crisis_damage,
        priority_districts=districts,
        coverage=coverage,
        hotspots=hotspots,
        infra=infra,
        impact=impact,
        points=points,
        aggregated=aggregated,
    )


async def _build_aggregate_views(session: AsyncSession, crisis_id: uuid.UUID) -> AggregateViews:
    """SQL-aggregated substitutes for the point maps; per-district lists align to `days`."""
    daily_rows = await data.fetch_daily_counts(session, crisis_id)
    daily = [
        DayCount(day=r.day, minimal=r.minimal, partial=r.partial, complete=r.complete)
        for r in daily_rows
    ]
    debris_yes, debris_known = await data.fetch_debris_totals(session, crisis_id)
    division_day_rows = await data.fetch_daily_by_division(session, crisis_id)

    day_index = {r.day: i for i, r in enumerate(daily_rows)}
    daily_by_district: dict[str, list[int]] = {}
    for r in division_day_rows:
        i = day_index.get(r.day)
        if i is None:
            continue
        arr = daily_by_district.setdefault(r.division_id, [0] * len(daily))
        arr[i] += r.total

    return AggregateViews(
        daily=daily,
        daily_by_district=daily_by_district,
        debris_yes=debris_yes,
        debris_known=debris_known,
    )


async def _preferred_release(session: AsyncSession, crisis_id: uuid.UUID) -> str | None:
    return (
        await session.execute(
            text("select overture_release_pinned from public.crises where id = :cid"),
            {"cid": str(crisis_id)},
        )
    ).scalar()


# Mesh, coverage, hotspots


def build_mesh_metrics(
    cells: list[data.CellRow],
) -> tuple[list[MeshCell], CoverageMetrics, HotspotMetrics, set[tuple[int, int]]]:
    """Run the spatial pipeline and pack per-cell results into MeshCells."""
    if not cells:
        empty_cov = CoverageMetrics(0.0, 0, 0.0, [], [])
        empty_hot = HotspotMetrics(0, 0, HOTSPOT_Z_THRESHOLD)
        return [], empty_cov, empty_hot, set()

    mesh = MeshGrid(
        lon=np.array([c.lon for c in cells], dtype=np.float64),
        lat=np.array([c.lat for c in cells], dtype=np.float64),
        stock=np.array([c.stock for c in cells], dtype=np.float64),
        observed=np.array([float(c.observed) for c in cells], dtype=np.float64),
        damage_weight=np.array([_cell_damage_weight(c) for c in cells], dtype=np.float64),
    )
    result = run_spatial_pipeline(mesh)

    mesh_cells = [
        MeshCell(
            lat=cells[i].lat,
            lon=cells[i].lon,
            observed=cells[i].observed,
            expected=float(result.expected[i]),
            smoothed_rate=float(result.smoothed_rate[i]),
            residual_z=float(result.residual_z[i]),
            gi_z=float(result.gi_z[i]),
            blind_spot=bool(result.blind_spot[i]),
            debris_yes=cells[i].debris_yes,
        )
        for i in range(len(cells))
    ]
    hot, cold = hot_cold_counts(result.gi_z)
    hotspots = HotspotMetrics(
        hot_cell_count=hot, cold_cell_count=cold, significance_z=HOTSPOT_Z_THRESHOLD
    )
    coverage = CoverageMetrics(
        coverage_pct=0.0,  # filled by the caller from ReportMeta
        observed_total=int(sum(c.observed for c in cells)),
        expected_total=float(result.expected.sum()),
        blind_spot_district_ids=[],
        cells=mesh_cells,
    )
    blind_keys = {(cells[i].ix, cells[i].iy) for i in range(len(cells)) if result.blind_spot[i]}
    return mesh_cells, coverage, hotspots, blind_keys


def _merge_impact_cells(
    mesh_cells: list[MeshCell],
    cell_rows: list[data.CellRow],
    impact_by_cell: dict[tuple[int, int], data.ImpactCellRow],
) -> list[MeshCell]:
    """Attach per-cell impact onto the mesh cells, joined on `(ix, iy)`.

    `mesh_cells` and `cell_rows` are in lockstep, so they zip 1:1. The displaced
    source is chosen crisis-wide so the map sums to the headline: if any cell has
    WorldPop coverage, uncovered cells show 0 instead of the occupancy band.
    """
    crisis_has_pop = any(imp.population_available for imp in impact_by_cell.values())
    out: list[MeshCell] = []
    for mc, cr in zip(mesh_cells, cell_rows, strict=True):
        imp = impact_by_cell.get((cr.ix, cr.iy))
        if imp is None:
            out.append(mc)
            continue
        if crisis_has_pop:
            displaced_low = round(imp.displaced_low)
            displaced_high = round(imp.displaced_high)
        else:
            displaced_low = round(imp.displacing_buildings * OCCUPANTS_PER_BUILDING.low)
            displaced_high = round(imp.displacing_buildings * OCCUPANTS_PER_BUILDING.high)
        out.append(
            dataclasses.replace(
                mc,
                affected_buildings=imp.displacing_buildings,
                displaced_low=displaced_low,
                displaced_high=displaced_high,
                economic_available=imp.economic_available,
                economic_low_usd=imp.economic_low_usd if imp.economic_available else None,
                economic_high_usd=imp.economic_high_usd if imp.economic_available else None,
            )
        )
    return out


def _cell_damage_weight(c: data.CellRow) -> float:
    return (
        c.minimal * _DAMAGE_SEVERITY_WEIGHT["minimal"]
        + c.partial * _DAMAGE_SEVERITY_WEIGHT["partial"]
        + c.complete * _DAMAGE_SEVERITY_WEIGHT["complete"]
    )


def _residual_by_cell(
    cells: list[data.CellRow], coverage: CoverageMetrics
) -> dict[tuple[int, int], tuple[float, float]]:
    """Map (ix, iy) to (residual_z, stock) for the exposure-weighted district roll-up."""
    out: dict[tuple[int, int], tuple[float, float]] = {}
    for c, mc in zip(cells, coverage.cells, strict=True):
        out[(c.ix, c.iy)] = (mc.residual_z, c.stock)
    return out


# Districts


def build_districts(
    rows: list[data.DistrictRow],
    *,
    cells: list[data.CellRow],
    cell_division: dict[tuple[int, int], str],
    residual_by_cell: dict[tuple[int, int], tuple[float, float]],
    blind_cell_keys: set[tuple[int, int]],
) -> list[District]:
    """Roll cell results onto divisions, then flag, tier and rank them.

    Cells with reports but no division go into one "Unmapped area" fallback.
    """
    # which mesh cells belong to each division id
    cells_by_division: dict[str, list[tuple[int, int]]] = {}
    for (ix, iy), div_id in cell_division.items():
        cells_by_division.setdefault(div_id, []).append((ix, iy))

    districts: list[District] = []
    for r in rows:
        keys = cells_by_division.get(r.division_id, [])
        residual_z = _exposure_weighted_residual(keys, residual_by_cell)
        has_blind = any(k in blind_cell_keys for k in keys)
        districts.append(
            _make_district(
                div_id=r.division_id,
                name=r.name,
                official=True,
                centroid_lat=r.centroid_lat,
                centroid_lon=r.centroid_lon,
                report_count=r.report_count,
                damage=DamageMix(r.minimal, r.partial, r.complete),
                exposure_buildings=r.exposure_buildings,
                debris_yes=r.debris_yes,
                debris_known=r.debris_known,
                services_hit=r.services_hit,
                coverage_residual_z=residual_z,
                has_blind=has_blind,
                geom=json.loads(r.geom) if r.geom else None,
            )
        )

    # leftover cells with reports that no division claimed form one fallback blob
    claimed = set(cell_division.keys())
    leftover = [c for c in cells if (c.ix, c.iy) not in claimed and c.observed > 0]
    if leftover:
        districts.append(_fallback_district(leftover, residual_by_cell, blind_cell_keys))

    _rank(districts)
    return districts


def _exposure_weighted_residual(
    keys: list[tuple[int, int]],
    residual_by_cell: dict[tuple[int, int], tuple[float, float]],
) -> float:
    """Stock-weighted mean of the cells' local residuals (0.0 if no cells)."""
    num = 0.0
    den = 0.0
    for k in keys:
        rz_stock = residual_by_cell.get(k)
        if rz_stock is None:
            continue
        rz, stock = rz_stock
        w = max(stock, 1.0)
        num += rz * w
        den += w
    return num / den if den else 0.0


def _make_district(
    *,
    div_id: str,
    name: str,
    official: bool,
    centroid_lat: float,
    centroid_lon: float,
    report_count: int,
    damage: DamageMix,
    exposure_buildings: int,
    debris_yes: int,
    debris_known: int,
    services_hit: int,
    coverage_residual_z: float,
    has_blind: bool,
    geom: dict[str, object] | None = None,
) -> District:
    debris_blocked_share = debris_yes / debris_known if debris_known else 0.0

    flags: list[str] = []
    if damage.complete > 0 or damage.severe_share >= SEVERE_DAMAGE_SHARE:
        flags.append(FLAG_SEVERE)
    if debris_blocked_share >= BLOCKED_DEBRIS_SHARE:
        flags.append(FLAG_BLOCKED)
    if has_blind:
        flags.append(FLAG_BLIND_SPOT)

    red: list[str] = []
    if damage.severe_share >= RED_SEVERE_DAMAGE_SHARE:
        red.append(COMPONENT_DAMAGE)
    if debris_blocked_share >= BLOCKED_DEBRIS_SHARE:
        red.append(COMPONENT_REACHABILITY)
    if services_hit >= RED_SERVICES_HIT:
        red.append(COMPONENT_SERVICES)
    if coverage_residual_z < RED_COVERAGE_RESIDUAL_Z:
        red.append(COMPONENT_COVERAGE)
    # exposure red needs all districts, so `_rank` fills it in.

    tier = 0 if (FLAG_SEVERE in flags or FLAG_BLOCKED in flags) else 1

    # placement, both in [0, 1]
    # severity: blends severe share and the weight of confirmed total collapse.
    complete_share = damage.complete / damage.total if damage.total else 0.0
    severity_score = min(1.0, 0.5 * damage.severe_share + 0.5 * complete_share)
    # reachability: 1 when nothing blocked, 0 when everything blocked.
    reachability_score = 1.0 - debris_blocked_share

    return District(
        id=div_id,
        name=name,
        official=official,
        centroid_lat=centroid_lat,
        centroid_lon=centroid_lon,
        report_count=report_count,
        damage=damage,
        exposure_buildings=exposure_buildings,
        debris_yes=debris_yes,
        debris_known=debris_known,
        services_hit=services_hit,
        coverage_residual_z=coverage_residual_z,
        flags=flags,
        red_components=red,
        tier=tier,
        rank=0,  # set by _rank
        priority_score=0.0,  # set by _rank
        severity_score=severity_score,
        reachability_score=reachability_score,
        geom=geom,
    )


def _fallback_district(
    leftover: list[data.CellRow],
    residual_by_cell: dict[tuple[int, int], tuple[float, float]],
    blind_cell_keys: set[tuple[int, int]],
) -> District:
    keys = [(c.ix, c.iy) for c in leftover]
    damage = DamageMix(
        minimal=sum(c.minimal for c in leftover),
        partial=sum(c.partial for c in leftover),
        complete=sum(c.complete for c in leftover),
    )
    lat = sum(c.lat for c in leftover) / len(leftover)
    lon = sum(c.lon for c in leftover) / len(leftover)
    return _make_district(
        div_id="area:unmapped",
        name="Unmapped area",
        official=False,
        centroid_lat=lat,
        centroid_lon=lon,
        report_count=sum(c.observed for c in leftover),
        damage=damage,
        exposure_buildings=int(sum(c.stock for c in leftover)),
        debris_yes=0,  # debris is per-report; the fallback blob lacks it
        debris_known=0,
        services_hit=0,
        coverage_residual_z=_exposure_weighted_residual(keys, residual_by_cell),
        has_blind=any(k in blind_cell_keys for k in keys),
    )


def _rank(districts: list[District]) -> None:
    """Assign the exposure red component, then score and rank districts in place.

    `priority_score` is a weighted sum of min-max-normalised report count, debris
    count and severity points. Flags don't affect rank. Ties break on report
    count, then id.
    """
    if not districts:
        return

    stocks = sorted(d.exposure_buildings for d in districts)
    # top-quartile threshold
    idx = max(0, math.ceil(0.75 * len(stocks)) - 1)
    q75 = stocks[idx]

    # min-max denominators; `or 1` keeps a factor at 0 for everyone when no
    # district has any of it (e.g. zero blocked-access reports crisis-wide).
    max_reports = max(d.report_count for d in districts) or 1
    max_debris = max(d.debris_yes for d in districts) or 1
    max_severity = max(_severity_points(d) for d in districts) or 1

    enriched: list[District] = []
    for d in districts:
        red = list(d.red_components)
        if d.exposure_buildings >= q75 and d.exposure_buildings > 0:
            red = sorted({*red, COMPONENT_EXPOSURE}, key=_component_order)
        score = (
            PRIORITY_WEIGHT_REPORTS * (d.report_count / max_reports)
            + PRIORITY_WEIGHT_DEBRIS * (d.debris_yes / max_debris)
            + PRIORITY_WEIGHT_SEVERITY * (_severity_points(d) / max_severity)
        )
        enriched.append(_replace_district(d, red_components=red, priority_score=score))

    enriched.sort(key=lambda d: (-d.priority_score, -d.report_count, d.id))
    districts[:] = [_replace_district(d, rank=i + 1) for i, d in enumerate(enriched)]


def _severity_points(d: District) -> int:
    return (
        SEVERITY_POINTS["minimal"] * d.damage.minimal
        + SEVERITY_POINTS["partial"] * d.damage.partial
        + SEVERITY_POINTS["complete"] * d.damage.complete
    )


_COMPONENT_ORDER = [
    COMPONENT_DAMAGE,
    COMPONENT_EXPOSURE,
    COMPONENT_REACHABILITY,
    COMPONENT_SERVICES,
    COMPONENT_COVERAGE,
]


def _component_order(c: str) -> int:
    return _COMPONENT_ORDER.index(c) if c in _COMPONENT_ORDER else len(_COMPONENT_ORDER)


def _replace_district(d: District, **changes: object) -> District:
    return dataclasses.replace(d, **changes)


# Infra


async def _build_infra(session: AsyncSession, crisis_id: uuid.UUID) -> InfraMetrics:
    rows = await data.fetch_infra(session, crisis_id)
    items = [
        InfraItem(
            report_id=r.report_id,
            infra_type=r.infra_type,
            name=r.name,
            damage_class=r.damage_class,
            debris=r.debris,
            district_name=None,  # district label is a narrative-layer concern
            lat=r.lat,
            lon=r.lon,
        )
        for r in rows
    ]
    by_type: dict[str, int] = {}
    for r in rows:
        by_type[r.infra_type] = by_type.get(r.infra_type, 0) + 1
    return InfraMetrics(items=items, by_type=by_type)


# Impact


async def _build_impact(
    session: AsyncSession, crisis_id: uuid.UUID, *, release: str | None = None
) -> ImpactEstimates:
    ratio = {cls: (rng.low, rng.high) for cls, rng in DAMAGE_RATIO.items()}
    fraction = {cls: (rng.low, rng.high) for cls, rng in DISPLACEMENT_FRACTION.items()}
    row = await data.fetch_impact(
        session,
        crisis_id,
        displacing_classes=DISPLACING_DAMAGE_CLASSES,
        damage_ratio=ratio,
        displacement_fraction=fraction,
        release=release,
    )

    population_available = row.population_available
    if population_available:
        # WorldPop-anchored: people in each damaged building's cell, shared
        # across the cell's building stock, x the per-class displacement fraction.
        displaced_low = round(row.displaced_low)
        displaced_high = round(row.displaced_high)
        notes = [
            "Displaced range is modelled from WorldPop building-constrained "
            "population (residents in each damaged building's grid cell, shared "
            "across that cell's buildings) times a per-damage-class displacement "
            f"fraction (complete {DISPLACEMENT_FRACTION['complete'].low:g}-"
            f"{DISPLACEMENT_FRACTION['complete'].high:g}, partial "
            f"{DISPLACEMENT_FRACTION['partial'].low:g}-"
            f"{DISPLACEMENT_FRACTION['partial'].high:g}, minimal "
            f"{DISPLACEMENT_FRACTION['minimal'].low:g}-"
            f"{DISPLACEMENT_FRACTION['minimal'].high:g}). WorldPop rests on a "
            "pre-war census, so this is a modelled pre-crisis baseline, not a "
            "live count.",
        ]
    else:
        # No population coverage for this AOI: occupancy band over partial/complete only.
        displaced_low = round(row.affected_buildings * OCCUPANTS_PER_BUILDING.low)
        displaced_high = round(row.affected_buildings * OCCUPANTS_PER_BUILDING.high)
        notes = [
            f"Displaced range assumes {OCCUPANTS_PER_BUILDING.low:g}-"
            f"{OCCUPANTS_PER_BUILDING.high:g} occupants per affected building "
            "(partial/complete only); a deliberately wide stand-in for census "
            "household size, used here because the WorldPop population grid is "
            "not loaded for this AOI.",
        ]

    economic_available = row.litpop_cells_hit > 0 and row.economic_high_usd > 0.0
    if economic_available:
        notes.append(
            "Economic range disaggregates LitPop produced-capital value to each "
            "damaged building (cell value / buildings in cell) and applies a "
            "coarse PDNA-style damage ratio per class."
        )
    else:
        notes.append(
            "Economic estimate pending data: the LitPop asset-value grid is not "
            "loaded for this AOI, so no monetary figure is shown."
        )
    notes.append("Both estimates are coarse and defer to authoritative IOM DTM / PDNA assessments.")

    return ImpactEstimates(
        affected_buildings=row.affected_buildings,
        displaced_low=displaced_low,
        displaced_high=displaced_high,
        population_available=population_available,
        economic_available=economic_available,
        economic_low_usd=row.economic_low_usd if economic_available else None,
        economic_high_usd=row.economic_high_usd if economic_available else None,
        notes=notes,
    )


__all__ = ["build_report_metrics"]
