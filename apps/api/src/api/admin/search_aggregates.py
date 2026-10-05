"""Full-set search stats in one statement."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.admin.report_sql import (
    GEOCODE_JOIN,
)
from api.admin.search_filters import (
    STRUCTURED_FILTERS,
    add_semantic_bindings,
    apply_semantic_session_config,
    base_filter_bindings,
    geom_clause,
    is_geometry_collection,
    preset_for,
    resolve_location_multipolygon,
)
from api.schemas.admin_search import (
    DailyBucket,
    SearchRequest,
    SearchStats,
)

# Full-set stats in one statement. The matched CTE is materialised so the
# filter and ANN scan run once for all three sub-aggregates.


def _matched_cte(*, has_geom: bool, geom_is_collection: bool, semantic: bool) -> str:
    geom = geom_clause(geom_is_collection) if has_geom else ""
    semantic_join = "join public.report_embeddings e on e.report_id = r.id" if semantic else ""
    semantic_filter = (
        """
          and e.embedding_version = :embedding_version
          and (e.embedding <=> cast(:qvec as halfvec)) <= :max_distance
        """
        if semantic
        else ""
    )
    return f"""
    matched as materialized (
        select
            r.damage_class,
            r.debris,
            r.infra_type,
            r.building_id,
            (r.location is not null) as has_location,
            (r.map_point is not null) as has_position,
            (r.location is null and b.centroid is null and g.lat is not null and g.lon is not null)
                as is_geocode,
            r.created_at,
            r.client_id
        from public.reports r
        {semantic_join}
        left join public.buildings b on b.id = r.building_id
        {GEOCODE_JOIN}
        where {STRUCTURED_FILTERS}
        {semantic_filter}
        {geom}
    )
    """


def _build_aggregates_sql(has_geom: bool, geom_is_collection: bool, semantic: bool) -> Any:
    cte = _matched_cte(has_geom=has_geom, geom_is_collection=geom_is_collection, semantic=semantic)
    return text(
        f"""
    with {cte},
    scalars as (
        select
            count(*) as total,
            count(*) filter (where damage_class = 'complete') as sev_complete,
            count(*) filter (where damage_class = 'partial')  as sev_partial,
            count(*) filter (where damage_class = 'minimal')  as sev_minimal,
            count(*) filter (where created_at >= cast(:day_ago  as timestamptz)) as last_24h,
            count(*) filter (where created_at >= cast(:hour_ago as timestamptz)) as last_hour,
            count(*) filter (where debris = 'yes')             as debris_yes,
            count(*) filter (where debris in ('yes', 'no'))    as debris_known,
            count(*) filter (where building_id is not null)    as with_building,
            count(*) filter (where has_location)               as with_gps,
            count(*) filter (where is_geocode)                 as with_geocode,
            count(*) filter (where not has_position)           as unmapped,
            count(distinct client_id)   as unique_devices,
            count(distinct building_id) as buildings_affected
        from matched
    ),
    infra as (
        select coalesce(jsonb_object_agg(t, n), '{{}}'::jsonb) as infra
        from (
            select t, count(*) as n
            from matched, unnest(infra_type) as t
            group by t
        ) s
    ),
    daily as (
        select coalesce(
            jsonb_agg(
                jsonb_build_object(
                    'date', day, 'complete', c, 'partial', p, 'minimal', m
                )
                order by day
            ),
            '[]'::jsonb
        ) as daily
        from (
            select
                date_trunc('day', created_at)::date as day,
                count(*) filter (where damage_class = 'complete') as c,
                count(*) filter (where damage_class = 'partial')  as p,
                count(*) filter (where damage_class = 'minimal')  as m
            from matched
            group by 1
        ) d
    )
    select scalars.*, infra.infra, daily.daily
    from scalars, infra, daily
    """
    )


_BUILDINGS_TOTAL_CRISIS_SQL = text(
    "select coalesce(buildings_ingested_count, 0) as n from public.crises where id = :crisis_id"
)


async def compute_search_aggregates(
    session: AsyncSession,
    *,
    crisis_id: uuid.UUID,
    request: SearchRequest,
    query_vector: list[float] | None,
) -> SearchStats:
    """Aggregates over the full filtered set, used by the KPI ribbon, charts and chat."""
    geom_literal = await resolve_location_multipolygon(session, request.location)
    floor, ef_search = preset_for(request.strictness)
    semantic = query_vector is not None

    if semantic:
        # Same plan as run_search. Skipped without a query: no HNSW scan.
        await apply_semantic_session_config(session, ef_search)

    now = datetime.now(UTC)
    geom_is_collection = is_geometry_collection(geom_literal)
    bindings = base_filter_bindings(crisis_id, request, geom_literal)
    bindings["day_ago"] = now - timedelta(hours=24)
    bindings["hour_ago"] = now - timedelta(hours=1)
    if semantic:
        add_semantic_bindings(bindings, query_vector, floor)  # pyright: ignore[reportArgumentType]

    sql = _build_aggregates_sql(geom_literal is not None, geom_is_collection, semantic)
    row = (await session.execute(sql, bindings)).first()

    buildings_total = await _count_buildings_total(session, crisis_id)

    if row is None:  # the select always yields one row
        return SearchStats(
            total=0,
            severity={"complete": 0, "partial": 0, "minimal": 0},
            last_24h=0,
            last_hour=0,
            top_infra=None,
            top_infra_count=0,
            debris_yes=0,
            debris_known=0,
            with_building=0,
            with_gps=0,
            with_geocode=0,
            unmapped=0,
            buildings_total=buildings_total,
        )

    infra_raw = cast("dict[str, int]", row.infra or {})
    daily_raw = cast("list[dict[str, Any]]", row.daily or [])
    infra_breakdown: dict[str, int] = {str(k): int(v) for k, v in infra_raw.items()}
    daily = [
        DailyBucket(
            date=str(d["date"]),
            complete=int(d["complete"]),
            partial=int(d["partial"]),
            minimal=int(d["minimal"]),
        )
        for d in daily_raw
    ]
    top_infra: str | None = None
    top_infra_count = 0
    if infra_breakdown:
        top_infra, top_infra_count = max(infra_breakdown.items(), key=lambda kv: kv[1])

    return SearchStats(
        total=int(row.total),
        severity={
            "complete": int(row.sev_complete),
            "partial": int(row.sev_partial),
            "minimal": int(row.sev_minimal),
        },
        last_24h=int(row.last_24h),
        last_hour=int(row.last_hour),
        top_infra=top_infra,
        top_infra_count=top_infra_count,
        debris_yes=int(row.debris_yes),
        debris_known=int(row.debris_known),
        with_building=int(row.with_building),
        with_gps=int(row.with_gps),
        with_geocode=int(row.with_geocode),
        unmapped=int(row.unmapped),
        infra_breakdown=infra_breakdown,
        daily=daily,
        unique_devices=int(row.unique_devices),
        buildings_affected=int(row.buildings_affected),
        buildings_total=buildings_total,
    )


async def _count_buildings_total(session: AsyncSession, crisis_id: uuid.UUID) -> int:
    """Crisis-wide building count, not scoped to the location filter.

    A live spatial count over the buildings table takes seconds and this runs
    on every filter change and map pan.
    """
    value = (
        await session.execute(_BUILDINGS_TOTAL_CRISIS_SQL, {"crisis_id": str(crisis_id)})
    ).scalar()
    return int(value or 0)
