"""Damage-impact queries: building asset value and displaced-population estimates."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.analysis.constants import LITPOP_GRID_STEP_DEG
from api.analysis.data.geography import resolve_density_release
from api.analysis.data.sql import MAP_POINT
from api.buildings.density_grid import DENSITY_GRID_STEP_DEG


def _impact_ctes(
    damage_ratio: dict[str, tuple[float, float]],
    displacement_fraction: dict[str, tuple[float, float]],
) -> str:
    """CTEs shared by `fetch_impact` and `fetch_impact_cells`, ending at `pop_valued`.

    Binds :cid, :lstep, :dstep and :release. The class constants go in as VALUES
    lists of (class, low, high) so they are applied in SQL.
    """
    ratio_values = ", ".join(f"('{cls}', {lo}, {hi})" for cls, (lo, hi) in damage_ratio.items())
    disp_values = ", ".join(
        f"('{cls}', {lo}, {hi})" for cls, (lo, hi) in displacement_fraction.items()
    )
    return f"""
    with ratios(cls, lo, hi) as (values {ratio_values}),
    disp(cls, lo, hi) as (values {disp_values}),
    damaged as (
        -- one row per damaged BUILDING at its worst reported class
        -- (a building-less report is its own unit), so corroborating
        -- reports can't multiply the estimate
        select distinct on (coalesce(r.building_id::text, r.id::text))
            r.damage_class,
            floor(st_x({MAP_POINT}) / :lstep)::int as ix,
            floor(st_y({MAP_POINT}) / :lstep)::int as iy
        from public.reports r
        where r.crisis_id = :cid
          and r.damage_class in ('minimal','partial','complete')
          and r.map_point is not null
        order by coalesce(r.building_id::text, r.id::text),
            case r.damage_class
                when 'complete' then 3 when 'partial' then 2 else 1
            end desc
    ),
    bounds as (
        select min(ix) as x0, max(ix) as x1,
               min(iy) as y0, max(iy) as y1
        from damaged
    ),
    lp as (
        -- LitPop splits border cells between country datasets, so
        -- aggregate per cell before joining to avoid duplicate rows.
        -- This is a seq scan: fine once per job, not per row.
        select v.ix, v.iy, sum(v.usd) as usd
        from public.litpop_value v, bounds
        where v.ix between bounds.x0 and bounds.x1
          and v.iy between bounds.y0 and bounds.y1
        group by v.ix, v.iy
    ),
    pop as (
        -- population_value lives on the same 0.02° grid as litpop;
        -- same per-cell pre-aggregation and bounded seq scan.
        select v.ix, v.iy, sum(v.people) as people
        from public.population_value v, bounds
        where v.ix between bounds.x0 and bounds.x1
          and v.iy between bounds.y0 and bounds.y1
        group by v.ix, v.iy
    ),
    valued as (
        select d.ix, d.iy, d.damage_class,
            lp.usd / greatest(coalesce(stock.n, 1), 1) as per_building_usd
        from damaged d
        join lp on lp.ix = d.ix and lp.iy = d.iy
        left join public.building_density_grid stock
          on stock.release = :release
         and stock.ix = floor((d.ix + 0.5) * :lstep / :dstep)::int
         and stock.iy = floor((d.iy + 0.5) * :lstep / :dstep)::int
    ),
    pop_valued as (
        -- parallel to `valued` but for population, so the economic
        -- aggregate stays independent of population coverage
        select d.ix, d.iy, d.damage_class,
            pop.people / greatest(coalesce(stock.n, 1), 1) as per_building_people
        from damaged d
        join pop on pop.ix = d.ix and pop.iy = d.iy
        left join public.building_density_grid stock
          on stock.release = :release
         and stock.ix = floor((d.ix + 0.5) * :lstep / :dstep)::int
         and stock.iy = floor((d.iy + 0.5) * :lstep / :dstep)::int
    )"""


@dataclass(frozen=True)
class ImpactRow:
    affected_buildings: int  # distinct damaged buildings (fallback: reports)
    economic_low_usd: float
    economic_high_usd: float
    litpop_cells_hit: int  # >0 means litpop had coverage for this AOI
    # Population-based displaced band, valid only when `population_available`.
    displaced_low: float
    displaced_high: float
    population_available: bool  # WorldPop population grid covered ≥1 damaged cell


async def fetch_impact(
    session: AsyncSession,
    crisis_id: uuid.UUID,
    *,
    displacing_classes: frozenset[str],
    damage_ratio: dict[str, tuple[float, float]],
    displacement_fraction: dict[str, tuple[float, float]],
    release: str | None = None,
) -> ImpactRow:
    """Crisis-wide impact aggregates.

    Each damaged building counts once at its worst class (a building-less report
    is its own unit), so corroborating reports don't multiply the estimate. A
    unit gets its cell's LitPop value and population divided by the cell's
    building stock, times the class ratio. Cell values are summed across country
    datasets because border cells are split between them.
    """
    release = await resolve_density_release(session, release)
    classes = sorted(displacing_classes)

    affected = (
        await session.execute(
            text(
                """
                select
                    count(distinct building_id) filter (where building_id is not null)
                    + count(*) filter (where building_id is null and damage_class = any(:classes))
                    as affected
                from public.reports
                where crisis_id = :cid and damage_class = any(:classes)
                """
            ),
            {"cid": str(crisis_id), "classes": classes},
        )
    ).scalar()

    econ = (
        await session.execute(
            text(
                f"""
                {_impact_ctes(damage_ratio, displacement_fraction)},
                econ as (
                    select
                        coalesce(sum(v.per_building_usd * ratios.lo), 0) as low,
                        coalesce(sum(v.per_building_usd * ratios.hi), 0) as high,
                        count(*) as cells_hit
                    from valued v
                    join ratios on ratios.cls = v.damage_class
                ),
                popagg as (
                    select
                        coalesce(sum(pv.per_building_people * disp.lo), 0) as displaced_low,
                        coalesce(sum(pv.per_building_people * disp.hi), 0) as displaced_high,
                        count(*) as pop_hit
                    from pop_valued pv
                    join disp on disp.cls = pv.damage_class
                )
                select econ.low, econ.high, econ.cells_hit,
                       popagg.displaced_low, popagg.displaced_high, popagg.pop_hit
                from econ, popagg
                """
            ),
            {
                "cid": str(crisis_id),
                "lstep": LITPOP_GRID_STEP_DEG,
                "dstep": DENSITY_GRID_STEP_DEG,
                "release": release,
            },
        )
    ).one()

    return ImpactRow(
        affected_buildings=int(affected or 0),
        economic_low_usd=float(econ.low or 0.0),
        economic_high_usd=float(econ.high or 0.0),
        litpop_cells_hit=int(econ.cells_hit or 0),
        displaced_low=float(econ.displaced_low or 0.0),
        displaced_high=float(econ.displaced_high or 0.0),
        population_available=int(econ.pop_hit or 0) > 0,
    )


@dataclass(frozen=True)
class ImpactCellRow:
    """Per-cell impact on the analysis mesh grid, so (ix, iy) joins onto `MeshCell` directly."""

    ix: int
    iy: int
    displacing_buildings: int  # partial/complete damaged buildings in the cell
    economic_low_usd: float
    economic_high_usd: float
    economic_available: bool  # LitPop had coverage for this cell
    # Population-based displaced band, valid only when `population_available`.
    displaced_low: float
    displaced_high: float
    population_available: bool  # WorldPop population grid covered this cell


async def fetch_impact_cells(
    session: AsyncSession,
    crisis_id: uuid.UUID,
    *,
    displacing_classes: frozenset[str],
    damage_ratio: dict[str, tuple[float, float]],
    displacement_fraction: dict[str, tuple[float, float]],
    release: str | None = None,
) -> list[ImpactCellRow]:
    """Per-cell version of `fetch_impact` for the live impact map.

    The displaced band sums over all damaged buildings, not just `displacing_buildings`.
    """
    release = await resolve_density_release(session, release)
    classes = sorted(displacing_classes)

    rows = (
        await session.execute(
            text(
                f"""
                {_impact_ctes(damage_ratio, displacement_fraction)},
                econ_by_cell as (
                    select v.ix, v.iy,
                        sum(v.per_building_usd * ratios.lo) as econ_low,
                        sum(v.per_building_usd * ratios.hi) as econ_high
                    from valued v
                    join ratios on ratios.cls = v.damage_class
                    group by v.ix, v.iy
                ),
                pop_by_cell as (
                    select pv.ix, pv.iy,
                        sum(pv.per_building_people * disp.lo) as disp_low,
                        sum(pv.per_building_people * disp.hi) as disp_high
                    from pop_valued pv
                    join disp on disp.cls = pv.damage_class
                    group by pv.ix, pv.iy
                ),
                dmg_by_cell as (
                    select ix, iy,
                        count(*) filter (where damage_class = any(:classes))
                            as displacing_buildings
                    from damaged
                    group by ix, iy
                )
                select d.ix, d.iy, d.displacing_buildings,
                    coalesce(e.econ_low, 0)  as econ_low,
                    coalesce(e.econ_high, 0) as econ_high,
                    (e.ix is not null)       as economic_available,
                    coalesce(p.disp_low, 0)  as disp_low,
                    coalesce(p.disp_high, 0) as disp_high,
                    (p.ix is not null)       as population_available
                from dmg_by_cell d
                left join econ_by_cell e on e.ix = d.ix and e.iy = d.iy
                left join pop_by_cell p on p.ix = d.ix and p.iy = d.iy
                """
            ),
            {
                "cid": str(crisis_id),
                "classes": classes,
                "lstep": LITPOP_GRID_STEP_DEG,
                "dstep": DENSITY_GRID_STEP_DEG,
                "release": release,
            },
        )
    ).all()

    return [
        ImpactCellRow(
            ix=int(r.ix),
            iy=int(r.iy),
            displacing_buildings=int(r.displacing_buildings),
            economic_low_usd=float(r.econ_low or 0.0),
            economic_high_usd=float(r.econ_high or 0.0),
            economic_available=bool(r.economic_available),
            displaced_low=float(r.disp_low or 0.0),
            displaced_high=float(r.disp_high or 0.0),
            population_available=bool(r.population_available),
        )
        for r in rows
    ]
