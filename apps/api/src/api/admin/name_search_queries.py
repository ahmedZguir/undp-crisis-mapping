"""Name autocompletes for the search-filter location pickers (pg_trgm ranked)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas import LocationOut
from api.schemas.admin_search import BuildingSearchHit, DivisionSearchHit

_MAX_LIMIT = 50

# Below pg_trgm's 0.3 default: building names are short and abbreviations
# are common ("Al Falah" vs "Falah").
_TRGM_THRESHOLD = 0.15


# Buildings


# Buildings with at least one report in the crisis; ties go to the most
# reported building. infra_type comes from the latest report.
_BUILDINGS_BY_REPORTS_SQL = text(
    """
    with crisis_reports as (
        select r.id, r.building_id, r.infra_type, r.created_at
          from public.reports r
         where r.crisis_id = :crisis_id
           and r.building_id is not null
    ),
    per_building as (
        select
            building_id,
            count(*)::int as n_reports
          from crisis_reports
         group by building_id
    ),
    latest_per_building as (
        select distinct on (building_id)
               building_id,
               infra_type as latest_infra_type
          from crisis_reports
         order by building_id, created_at desc
    )
    select
        b.id,
        b.name,
        st_y(b.centroid::geometry) as centroid_lat,
        st_x(b.centroid::geometry) as centroid_lng,
        l.latest_infra_type,
        p.n_reports,
        similarity(b.name, :q) as sim
      from per_building p
      join public.buildings b on b.id = p.building_id
      left join latest_per_building l on l.building_id = p.building_id
     where b.name is not null
       and (similarity(b.name, :q) >= :threshold or b.name ilike :pattern)
     order by sim desc, p.n_reports desc, b.name asc
     limit :limit
    """
)


# Fallback for a crisis with no reports yet. Clipping to the envelope keeps
# the scan cheap.
_BUILDINGS_BY_CRISIS_BBOX_SQL = text(
    """
    with crisis_bbox as (
        select st_envelope(c.geometry::geometry) as bbox
          from public.crises c
         where c.id = :crisis_id
    )
    select
        b.id,
        b.name,
        st_y(b.centroid::geometry) as centroid_lat,
        st_x(b.centroid::geometry) as centroid_lng,
        null::text[] as latest_infra_type,
        0::int as n_reports,
        similarity(b.name, :q) as sim
      from public.buildings b
     cross join crisis_bbox
     where b.name is not null
       and crisis_bbox.bbox is not null
       and st_intersects(b.centroid::geometry, crisis_bbox.bbox)
       and (similarity(b.name, :q) >= :threshold or b.name ilike :pattern)
     order by sim desc, b.name asc
     limit :limit
    """
)


async def search_buildings_by_name(
    session: AsyncSession,
    *,
    crisis_id: uuid.UUID,
    q: str,
    limit: int,
) -> list[BuildingSearchHit]:
    """Prefer reported buildings in the crisis; fall back to its bbox when none match."""
    clamped = max(1, min(limit, _MAX_LIMIT))
    params: dict[str, Any] = {
        "crisis_id": str(crisis_id),
        "q": q,
        "pattern": f"%{q}%",
        "threshold": _TRGM_THRESHOLD,
        "limit": clamped,
    }

    result = await session.execute(_BUILDINGS_BY_REPORTS_SQL, params)
    rows = result.all()
    if not rows:
        result = await session.execute(_BUILDINGS_BY_CRISIS_BBOX_SQL, params)
        rows = result.all()
    return [_row_to_building_hit(r) for r in rows]


def _row_to_building_hit(row: Any) -> BuildingSearchHit:
    return BuildingSearchHit(
        id=row.id,
        name=row.name,
        infra_type=list(row.latest_infra_type) if row.latest_infra_type is not None else None,
        centroid=LocationOut(lat=float(row.centroid_lat), lng=float(row.centroid_lng)),
        n_reports=int(row.n_reports),
    )


# Divisions


# ILIKE only: adding a similarity() predicate makes the planner skip the
# trigram index and seq-scan the table.
_DIVISIONS_SQL = text(
    """
    select
        id,
        coalesce(names_common ->> 'en', names_primary) as name,
        subtype,
        country,
        st_y(point_geometry::geometry) as centroid_lat,
        st_x(point_geometry::geometry) as centroid_lng
      from public.overture_divisions
     where (cast(:country as text) is null or country = :country)
       and names_search_text ilike :pattern
       and point_geometry is not null
     order by population desc nulls last
     limit :limit
    """
)


async def search_divisions_by_name(
    session: AsyncSession,
    *,
    country_code: str | None,
    q: str,
    limit: int,
) -> list[DivisionSearchHit]:
    """Fuzzy-match divisions by name; optionally narrow by ISO-2 country."""
    clamped = max(1, min(limit, _MAX_LIMIT))
    params: dict[str, Any] = {
        "pattern": f"%{q}%",
        "country": country_code.upper() if country_code else None,
        "limit": clamped,
    }
    result = await session.execute(_DIVISIONS_SQL, params)
    return [_row_to_division_hit(r) for r in result.all()]


def _row_to_division_hit(row: Any) -> DivisionSearchHit:
    return DivisionSearchHit(
        id=str(row.id),
        name=str(row.name),
        admin_level=str(row.subtype),
        country_code=str(row.country) if row.country is not None else None,
        centroid=LocationOut(lat=float(row.centroid_lat), lng=float(row.centroid_lng)),
    )


__all__ = [
    "search_buildings_by_name",
    "search_divisions_by_name",
]
