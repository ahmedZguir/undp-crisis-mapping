"""Crisis, coverage, mesh-cell and district queries for the analysis report."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.analysis.data.sql import AOI_DIVISIONS_CTE, CRITICAL_INFRA_TYPES, MAP_POINT
from api.buildings.density_grid import DENSITY_GRID_STEP_DEG, cell_range_for_bbox


@dataclass(frozen=True)
class CrisisRow:
    name: str
    crisis_type: str | None
    countries: list[str]
    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float
    geom: str | None = None  # GeoJSON of the (simplified) AOI polygon, for the boundary outline


@dataclass(frozen=True)
class CoverRow:
    report_count: int
    device_count: int
    building_count: int
    reported_building_count: int
    as_of: datetime | None


@dataclass(frozen=True)
class CellRow:
    """One mesh cell: density-grid stock + reports bucketed into it."""

    ix: int
    iy: int
    lon: float  # centroid lon = (ix + 0.5) * step
    lat: float
    stock: float  # building stock n_i (density-grid count; >=1 floor)
    observed: int
    minimal: int
    partial: int
    complete: int
    debris_yes: int = 0  # reports in the cell flagging blocked access (debris='yes')


@dataclass(frozen=True)
class DistrictRow:
    """Per-division aggregate (or the leftover-cells fallback in the builder)."""

    division_id: str
    name: str
    centroid_lon: float
    centroid_lat: float
    report_count: int
    minimal: int
    partial: int
    complete: int
    debris_yes: int
    debris_known: int
    services_hit: int
    exposure_buildings: int
    geom: str | None = None  # GeoJSON of the (simplified) division polygon, for the report maps


async def fetch_crisis(session: AsyncSession, crisis_id: uuid.UUID) -> CrisisRow | None:
    """Crisis identity + AOI bounding box (from the MultiPolygon envelope)."""
    row = (
        await session.execute(
            text(
                """
                select
                    c.name,
                    c.type as crisis_type,
                    c.countries,
                    st_xmin(box) as min_lon, st_ymin(box) as min_lat,
                    st_xmax(box) as max_lon, st_ymax(box) as max_lat,
                    st_asgeojson(st_simplifypreservetopology(c.geometry::geometry, 0.001)) as geom
                from public.crises c,
                     lateral (select st_envelope(c.geometry::geometry) as box) e
                where c.id = :cid and c.geometry is not null
                """
            ),
            {"cid": str(crisis_id)},
        )
    ).first()
    if row is None:
        return None
    return CrisisRow(
        name=row.name,
        crisis_type=row.crisis_type,
        countries=list(row.countries or []),
        min_lon=float(row.min_lon),
        min_lat=float(row.min_lat),
        max_lon=float(row.max_lon),
        max_lat=float(row.max_lat),
        geom=row.geom,
    )


async def fetch_cover(
    session: AsyncSession, crisis_id: uuid.UUID, *, release: str | None
) -> CoverRow:
    """Cover counts; building stock comes from the density grid over the AOI bbox."""
    rep = (
        await session.execute(
            text(
                """
                select
                    count(*) as report_count,
                    count(distinct client_id) as device_count,
                    count(distinct building_id) as reported_building_count,
                    max(created_at) as as_of
                from public.reports
                where crisis_id = :cid
                """
            ),
            {"cid": str(crisis_id)},
        )
    ).one()

    building_count = await _aoi_building_stock(session, crisis_id, release=release)

    return CoverRow(
        report_count=int(rep.report_count or 0),
        device_count=int(rep.device_count or 0),
        building_count=building_count,
        reported_building_count=int(rep.reported_building_count or 0),
        as_of=rep.as_of,
    )


async def resolve_density_release(session: AsyncSession, preferred: str | None) -> str | None:
    return (
        await session.execute(
            text(
                """
                select case when exists (
                    select 1 from public.building_density_grid where release = :pref
                  ) then :pref
                  else (select max(release) from public.building_density_grid) end
                """
            ),
            {"pref": preferred},
        )
    ).scalar()


async def _aoi_cell_range(
    session: AsyncSession, crisis_id: uuid.UUID
) -> tuple[int, int, int, int] | None:
    """Integer (ix0, iy0, ix1, iy1) cell range covering the AOI bbox.

    Bound as integer params: comparing the int (ix, iy) columns against a
    double `floor(... / step)` expression defeats the `(release, ix, iy)` index
    and forces a full scan.
    """
    row = (
        await session.execute(
            text(
                """
                select st_xmin(box) xmin, st_ymin(box) ymin,
                       st_xmax(box) xmax, st_ymax(box) ymax
                from public.crises c,
                     lateral (select st_envelope(c.geometry::geometry) as box) e
                where c.id = :cid and c.geometry is not null
                """
            ),
            {"cid": str(crisis_id)},
        )
    ).first()
    if row is None:
        return None
    return cell_range_for_bbox(row.xmin, row.ymin, row.xmax, row.ymax)


async def _aoi_building_stock(
    session: AsyncSession, crisis_id: uuid.UUID, *, release: str | None
) -> int:
    """Building stock in the AOI = Σ density-grid n over the bbox cells."""
    release = await resolve_density_release(session, release)
    rng = await _aoi_cell_range(session, crisis_id)
    if release is None or rng is None:
        return 0
    ix0, iy0, ix1, iy1 = rng
    total = (
        await session.execute(
            text(
                """
                select coalesce(sum(n), 0)::bigint
                from public.building_density_grid
                where release = :rel
                  and ix between :ix0 and :ix1
                  and iy between :iy0 and :iy1
                """
            ),
            {"rel": release, "ix0": ix0, "ix1": ix1, "iy0": iy0, "iy1": iy1},
        )
    ).scalar()
    return int(total or 0)


async def fetch_mesh_cells(
    session: AsyncSession, crisis_id: uuid.UUID, *, release: str | None
) -> list[CellRow]:
    """Every density-grid cell in the AOI bbox with the reports bucketed into it.

    Left join from the grid so cells with buildings but no reports survive.
    """
    release = await resolve_density_release(session, release)
    rng = await _aoi_cell_range(session, crisis_id)
    if release is None or rng is None:
        return []
    ix0, iy0, ix1, iy1 = rng

    rows = (
        await session.execute(
            text(
                f"""
                with grid as (
                    select ix, iy, n
                    from public.building_density_grid
                    where release = :rel
                      and ix between :ix0 and :ix1
                      and iy between :iy0 and :iy1
                      and n > 0
                ),
                rep as (
                    select
                        floor(st_x({MAP_POINT}) / :step)::int as ix,
                        floor(st_y({MAP_POINT}) / :step)::int as iy,
                        count(*) as observed,
                        count(*) filter (where r.damage_class = 'minimal') as minimal,
                        count(*) filter (where r.damage_class = 'partial')  as partial,
                        count(*) filter (where r.damage_class = 'complete') as complete,
                        count(*) filter (where r.debris = 'yes') as debris_yes
                    from public.reports r
                    where r.crisis_id = :cid
                      and r.map_point is not null
                    group by 1, 2
                )
                select
                    g.ix, g.iy, g.n,
                    coalesce(rep.observed, 0)   as observed,
                    coalesce(rep.minimal, 0)    as minimal,
                    coalesce(rep.partial, 0)    as partial,
                    coalesce(rep.complete, 0)   as complete,
                    coalesce(rep.debris_yes, 0) as debris_yes
                from grid g
                left join rep on rep.ix = g.ix and rep.iy = g.iy
                """
            ),
            {
                "cid": str(crisis_id),
                "rel": release,
                "step": DENSITY_GRID_STEP_DEG,
                "ix0": ix0,
                "ix1": ix1,
                "iy0": iy0,
                "iy1": iy1,
            },
        )
    ).all()

    step = DENSITY_GRID_STEP_DEG
    return [
        CellRow(
            ix=int(r.ix),
            iy=int(r.iy),
            lon=(int(r.ix) + 0.5) * step,
            lat=(int(r.iy) + 0.5) * step,
            stock=float(r.n),
            observed=int(r.observed),
            minimal=int(r.minimal),
            partial=int(r.partial),
            complete=int(r.complete),
            debris_yes=int(r.debris_yes),
        )
        for r in rows
    ]


async def fetch_districts(
    session: AsyncSession,
    crisis_id: uuid.UUID,
    *,
    subtype: str = "region",
    release: str | None,
) -> list[DistrictRow]:
    """Per-division aggregates, assigning reports to Overture divisions by `ST_Contains`.

    Divisions are pre-filtered to those overlapping the AOI. `subtype` defaults to
    "region" because "county" names are bare zone numbers. Exposure is the
    density-grid stock of cells whose centroid falls in the division.
    """
    release = await resolve_density_release(session, release)
    rng = await _aoi_cell_range(session, crisis_id)
    ix0, iy0, ix1, iy1 = rng if rng else (1, 0, 0, 0)  # empty range, no grid rows
    crit = sorted(CRITICAL_INFRA_TYPES)

    rows = (
        await session.execute(
            text(
                """
                with aoi as (select geometry::geometry g from public.crises where id = :cid),
                divs as (
                    select da.division_id,
                           min(coalesce(d.names_common->>'en', d.names_primary)) as name,
                           st_union(da.geometry::geometry) as geom
                    from public.overture_division_areas da
                    join public.overture_divisions d on d.id = da.division_id
                    cross join aoi
                    where da.subtype = :subtype
                      and da.geometry && aoi.g
                    group by da.division_id
                ),
                pts as (
                    select r.id,
                           r.damage_class, r.debris, r.infra_type,
                           r.map_point::geometry as g
                    from public.reports r
                    where r.crisis_id = :cid
                      and r.map_point is not null
                ),
                assigned as (
                    select divs.division_id, divs.name,
                           st_x(st_centroid(divs.geom)) as clon,
                           st_y(st_centroid(divs.geom)) as clat,
                           p.id, p.damage_class, p.debris, p.infra_type
                    from divs
                    join pts p on st_contains(divs.geom, p.g)
                ),
                agg as (
                    select division_id, name, clon, clat,
                        count(*) as report_count,
                        count(*) filter (where damage_class = 'minimal') as minimal,
                        count(*) filter (where damage_class = 'partial')  as partial,
                        count(*) filter (where damage_class = 'complete') as complete,
                        count(*) filter (where debris = 'yes') as debris_yes,
                        count(*) filter (where debris in ('yes','no')) as debris_known,
                        count(*) filter (
                            where infra_type && cast(:crit as text[])
                              and (damage_class in ('partial','complete') or debris = 'yes')
                        ) as services_hit
                    from assigned
                    group by division_id, name, clon, clat
                ),
                grid_cells as (
                    -- Bound the 12.5M-row grid to the AOI bbox cell range FIRST
                    -- via the int PK (index range scan; comparing the int ix/iy
                    -- to a double `floor(.../step)` would force a full seq scan),
                    -- then point-in-division only those few cells.
                    select gr.ix, gr.iy, gr.n,
                        st_setsrid(
                            st_point((gr.ix + 0.5) * :step, (gr.iy + 0.5) * :step), 4326
                        ) as centre
                    from public.building_density_grid gr
                    where gr.release = :rel
                      and gr.n > 0
                      and gr.ix between :ix0 and :ix1
                      and gr.iy between :iy0 and :iy1
                ),
                exposure as (
                    select divs.division_id,
                        coalesce(sum(gc.n), 0)::bigint as exposure_buildings
                    from divs
                    left join grid_cells gc on st_contains(divs.geom, gc.centre)
                    group by divs.division_id
                )
                select agg.*, coalesce(exposure.exposure_buildings, 0) as exposure_buildings,
                       st_asgeojson(st_simplifypreservetopology(divs.geom, 0.0005)) as geom
                from agg
                left join exposure on exposure.division_id = agg.division_id
                join divs on divs.division_id = agg.division_id
                """
            ),
            {
                "cid": str(crisis_id),
                "subtype": subtype,
                "crit": crit,
                "rel": release,
                "step": DENSITY_GRID_STEP_DEG,
                "ix0": ix0,
                "ix1": ix1,
                "iy0": iy0,
                "iy1": iy1,
            },
        )
    ).all()

    return [
        DistrictRow(
            division_id=str(r.division_id),
            name=r.name,
            centroid_lon=float(r.clon),
            centroid_lat=float(r.clat),
            report_count=int(r.report_count),
            minimal=int(r.minimal),
            partial=int(r.partial),
            complete=int(r.complete),
            debris_yes=int(r.debris_yes),
            debris_known=int(r.debris_known),
            services_hit=int(r.services_hit),
            exposure_buildings=int(r.exposure_buildings),
            geom=r.geom,
        )
        for r in rows
    ]


async def fetch_division_for_cells(
    session: AsyncSession,
    crisis_id: uuid.UUID,
    *,
    subtype: str = "region",
) -> dict[tuple[int, int], str]:
    """Map each mesh cell centroid to a division id; unmatched cells are absent."""
    rows = (
        await session.execute(
            text(
                f"""
                with {AOI_DIVISIONS_CTE},
                cells as (
                    select distinct
                        floor(st_x({MAP_POINT}) / :step)::int as ix,
                        floor(st_y({MAP_POINT}) / :step)::int as iy
                    from public.reports r
                    where r.crisis_id = :cid
                      and r.map_point is not null
                )
                select c.ix, c.iy, divs.division_id
                from cells c
                left join divs
                  on st_contains(
                        divs.geom,
                        st_setsrid(st_point((c.ix + 0.5) * :step, (c.iy + 0.5) * :step), 4326)
                     )
                """
            ),
            {"cid": str(crisis_id), "subtype": subtype, "step": DENSITY_GRID_STEP_DEG},
        )
    ).all()
    out: dict[tuple[int, int], str] = {}
    for r in rows:
        if r.division_id is not None:
            out[(int(r.ix), int(r.iy))] = str(r.division_id)
    return out
