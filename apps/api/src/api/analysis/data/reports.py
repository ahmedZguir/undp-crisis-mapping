"""Report-level queries: infra items, points, and their aggregated substitutes."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.analysis.data.sql import AOI_DIVISIONS_CTE, CRITICAL_INFRA_TYPES, MAP_POINT


@dataclass(frozen=True)
class InfraRow:
    report_id: uuid.UUID
    infra_type: str
    name: str | None
    damage_class: str
    debris: str | None
    lon: float | None
    lat: float | None


async def fetch_infra(session: AsyncSession, crisis_id: uuid.UUID) -> list[InfraRow]:
    """Damaged or non-functional critical-infra reports, one row per (report, infra_type).

    The name falls back from the reporter's `infra_name` to the linked building's name.
    """
    rows = (
        await session.execute(
            text(
                f"""
                select r.id, t.infra_type,
                       coalesce(nullif(r.infra_name, ''), b.name) as infra_name,
                       r.damage_class, r.debris,
                       st_x({MAP_POINT}) as lon,
                       st_y({MAP_POINT}) as lat
                from public.reports r
                cross join lateral unnest(r.infra_type) as t(infra_type)
                left join public.buildings b on b.id = r.building_id
                where r.crisis_id = :cid
                  and t.infra_type = any(:crit)
                  and (r.damage_class in ('partial','complete') or r.debris = 'yes')
                order by
                    case r.damage_class when 'complete' then 0 when 'partial' then 1 else 2 end,
                    r.created_at desc
                """
            ),
            {"cid": str(crisis_id), "crit": sorted(CRITICAL_INFRA_TYPES)},
        )
    ).all()
    return [
        InfraRow(
            report_id=r.id,
            infra_type=r.infra_type,
            name=r.infra_name,
            damage_class=r.damage_class,
            debris=r.debris,
            lon=float(r.lon) if r.lon is not None else None,
            lat=float(r.lat) if r.lat is not None else None,
        )
        for r in rows
    ]


@dataclass(frozen=True)
class ReportPointRow:
    lon: float
    lat: float
    damage_class: str
    debris: str | None
    created_at: datetime
    infra_type: list[str]
    infra_name: str | None


async def fetch_report_points(session: AsyncSession, crisis_id: uuid.UUID) -> list[ReportPointRow]:
    """Located report points for the point maps and daily timeline.

    Only used below REPORT_POINTS_THRESHOLD. Carries nothing that identifies a reporter.
    """
    rows = (
        await session.execute(
            text(
                f"""
                select st_x({MAP_POINT}) as lon, st_y({MAP_POINT}) as lat,
                       r.damage_class, r.debris, r.created_at,
                       r.infra_type, r.infra_name
                from public.reports r
                where r.crisis_id = :cid
                  and r.map_point is not null
                """
            ),
            {"cid": str(crisis_id)},
        )
    ).all()
    return [
        ReportPointRow(
            lon=float(r.lon),
            lat=float(r.lat),
            damage_class=r.damage_class,
            debris=r.debris,
            created_at=r.created_at,
            infra_type=list(r.infra_type) if r.infra_type is not None else [],
            infra_name=r.infra_name,
        )
        for r in rows
    ]


# Aggregated substitutes for `fetch_report_points` above REPORT_POINTS_THRESHOLD.
# They must use the same located-report filter and UTC day so the figures match
# the point path exactly.


@dataclass(frozen=True)
class DailyCountRow:
    day: date
    minimal: int
    partial: int
    complete: int


async def fetch_daily_counts(session: AsyncSession, crisis_id: uuid.UUID) -> list[DailyCountRow]:
    """Located reports per UTC day, split by damage class (the timeline substrate)."""
    rows = (
        await session.execute(
            text(
                """
                select (r.created_at at time zone 'UTC')::date as day,
                    count(*) filter (where r.damage_class = 'minimal')  as minimal,
                    count(*) filter (where r.damage_class = 'partial')  as partial,
                    count(*) filter (where r.damage_class = 'complete') as complete
                from public.reports r
                where r.crisis_id = :cid
                  and r.map_point is not null
                group by 1
                order by 1
                """
            ),
            {"cid": str(crisis_id)},
        )
    ).all()
    return [
        DailyCountRow(
            day=r.day,
            minimal=int(r.minimal),
            partial=int(r.partial),
            complete=int(r.complete),
        )
        for r in rows
    ]


async def fetch_debris_totals(session: AsyncSession, crisis_id: uuid.UUID) -> tuple[int, int]:
    """Crisis-wide (debris_yes, debris_known) over located reports."""
    row = (
        await session.execute(
            text(
                """
                select
                    count(*) filter (where r.debris = 'yes') as yes,
                    count(*) filter (where r.debris in ('yes', 'no')) as known
                from public.reports r
                where r.crisis_id = :cid
                  and r.map_point is not null
                """
            ),
            {"cid": str(crisis_id)},
        )
    ).one()
    return int(row.yes or 0), int(row.known or 0)


@dataclass(frozen=True)
class DivisionDayRow:
    division_id: str
    day: date
    total: int


async def fetch_daily_by_division(
    session: AsyncSession, crisis_id: uuid.UUID, *, subtype: str = "region"
) -> list[DivisionDayRow]:
    """Per-division reports per UTC day, using the same assignment as `fetch_districts`."""
    rows = (
        await session.execute(
            text(
                f"""
                with {AOI_DIVISIONS_CTE},
                pts as (
                    select (r.created_at at time zone 'UTC')::date as day,
                           r.map_point::geometry as g
                    from public.reports r
                    where r.crisis_id = :cid
                      and r.map_point is not null
                )
                select divs.division_id, p.day, count(*) as total
                from divs
                join pts p on st_contains(divs.geom, p.g)
                group by divs.division_id, p.day
                """
            ),
            {"cid": str(crisis_id), "subtype": subtype},
        )
    ).all()
    return [
        DivisionDayRow(division_id=str(r.division_id), day=r.day, total=int(r.total)) for r in rows
    ]
