"""Admin map aggregation over the same filters as report search.

Low zooms get grid clusters snapped on `map_point_3857`; high zooms get raw
points, falling back to clusters when a viewport exceeds `POINTS_CEILING`.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.admin.report_sql import GEOCODE_JOIN
from api.admin.search_filters import (
    STRUCTURED_FILTERS,
    add_semantic_bindings,
    apply_semantic_session_config,
    base_filter_bindings,
    geom_clause,
    preset_for,
    resolve_location_multipolygon,
)
from api.schemas import as_damage_class
from api.schemas.admin_map import MapClusterCell, MapPoint
from api.schemas.admin_search import SearchRequest
from api.schemas.common import LocationOut

# Matches the client's MapLibre clusterMaxZoom.
POINTS_ZOOM = 14.0
# A points-zoom viewport with more matches than this is served as clusters.
POINTS_CEILING = 5000
# Semantic clusters aggregate only the top matches by similarity, so counts are not a full census.
SEMANTIC_CLUSTER_CAP = 50_000

# Web Mercator metres per pixel at zoom 0 for 256px tiles. The client renders
# server bubbles as-is, so _CLUSTER_RADIUS_PX alone sets bubble density.
_MERCATOR_M_PER_PX_Z0 = 156543.03392804097
_CLUSTER_RADIUS_PX = 64.0


def cluster_cell_size_3857(zoom: float) -> float:
    """Edge, in EPSG:3857 units, of a `_CLUSTER_RADIUS_PX`-pixel cell at `zoom`."""
    return _CLUSTER_RADIUS_PX * _MERCATOR_M_PER_PX_Z0 / (2.0**zoom)


# Clusters
#
# The bbox prefilter uses map_point's GiST index; the grid snaps the stored
# map_point_3857. A cell's centroid is the plain mean of its points, transformed
# back to 4326 once per cell.

_CELLS_CTE = """
    cells as (
        select
            st_snaptogrid(p, :cell_size) as cell,
            count(*)                                      as total,
            count(*) filter (where dc = 'minimal')        as minimal,
            count(*) filter (where dc = 'partial')        as partial,
            count(*) filter (where dc = 'complete')       as complete,
            count(distinct p)                             as distinct_point_count,
            avg(st_x(p))                                  as cx,
            avg(st_y(p))                                  as cy,
            -- A one-report cell carries that report's details so the client
            -- can draw it as a clickable pin instead of a "1" bubble.
            (case when count(*) = 1 then min(rid::text) end)     as single_id,
            (case when count(*) = 1 then min(dc) end)            as single_dc,
            (case when count(*) = 1 then min(source) end)        as single_source,
            (case when count(*) = 1 then bool_or(area_only) end) as single_area_only
        from matched
        group by st_snaptogrid(p, :cell_size)
    )
"""

# `{extra}` lets the semantic path add `base_count` for cap detection.
_CELLS_SELECT = """
    select
        st_y(st_transform(st_setsrid(st_makepoint(cx, cy), 3857), 4326)) as lat,
        st_x(st_transform(st_setsrid(st_makepoint(cx, cy), 3857), 4326)) as lng,
        total, minimal, partial, complete, distinct_point_count,
        single_id, single_dc, single_source, single_area_only{extra}
    from cells
"""

_VIEWPORT_INTERSECT = (
    "st_intersects(r.map_point, st_makeenvelope(:west, :south, :east, :north, 4326)::geography)"
)


def _build_cluster_structured_sql(has_geom: bool, geom_is_collection: bool) -> Any:
    geom = geom_clause(geom_is_collection) if has_geom else ""
    return text(
        f"""
    with matched as materialized (
        select
            r.map_point_3857 as p,
            r.damage_class as dc,
            r.id as rid,
            r.map_point_source as source,
            g.area_only as area_only
        from public.reports r
        left join public.buildings b on b.id = r.building_id
        {GEOCODE_JOIN}
        where {STRUCTURED_FILTERS}
          and r.map_point_3857 is not null
          and {_VIEWPORT_INTERSECT}
        {geom}
    ),
    {_CELLS_CTE}
    {_CELLS_SELECT.format(extra="")}
    """
    )


def _build_cluster_semantic_sql(has_geom: bool, geom_is_collection: bool) -> Any:
    geom = geom_clause(geom_is_collection) if has_geom else ""
    # The ANN order-by/limit stays inside a materialized CTE so the planner uses
    # the HNSW index; a GROUP BY around it would seq-scan every embedding. The
    # cap applies to the viewport's top-K. base_count equal to the cap means
    # there may be more matches.
    return text(
        f"""
    with base as materialized (
        select
            r.map_point_3857 as p,
            r.damage_class as dc,
            r.id as rid,
            r.map_point_source as source,
            g.area_only as area_only,
            (e.embedding <=> cast(:qvec as halfvec)) as distance
        from public.reports r
        join public.report_embeddings e on e.report_id = r.id
        left join public.buildings b on b.id = r.building_id
        {GEOCODE_JOIN}
        where {STRUCTURED_FILTERS}
          and r.map_point_3857 is not null
          and {_VIEWPORT_INTERSECT}
          and e.embedding_version = :embedding_version
          and (e.embedding <=> cast(:qvec as halfvec)) <= :max_distance
        {geom}
        order by (e.embedding <=> cast(:qvec as halfvec)) asc
        limit :semantic_cap
    ),
    astats as (
        select avg(distance) as mean_d, stddev_pop(distance) as sd from base
    ),
    matched as (
        -- Anomaly = similarity 2 sigma below the mean, i.e. distance above
        -- mean_d + 2*sd (similarity = 1 - distance).
        select base.p as p, base.dc as dc, base.rid as rid,
               base.source as source, base.area_only as area_only
        from base, astats
        where (not :anomalies_only)
           or (astats.sd is not null and astats.sd > 0
               and base.distance > astats.mean_d + 2 * astats.sd)
    ),
    basecount as (select count(*) as n from base),
    {_CELLS_CTE}
    {_CELLS_SELECT.format(extra=", (select n from basecount) as base_count")}
    """
    )


_COUNT_SEMANTIC_MATCHES_SQL_TMPL = """
    select count(*) as n
    from public.reports r
    join public.report_embeddings e on e.report_id = r.id
    left join public.buildings b on b.id = r.building_id
    {geocode_join}
    where {structured}
      and r.map_point_3857 is not null
      and {viewport}
      and e.embedding_version = :embedding_version
      and (e.embedding <=> cast(:qvec as halfvec)) <= :max_distance
    {geom}
"""


def _viewport_bindings(bbox: tuple[float, float, float, float]) -> dict[str, float]:
    west, south, east, north = bbox
    return {"west": west, "south": south, "east": east, "north": north}


async def fetch_map_clusters(
    session: AsyncSession,
    *,
    crisis_id: uuid.UUID,
    request: SearchRequest,
    bbox: tuple[float, float, float, float],
    zoom: float,
    query_vector: list[float] | None,
    anomalies_only: bool,
) -> tuple[list[MapClusterCell], int, int | None, bool]:
    """Grid-aggregate the filtered set. Returns `(cells, total, total_match_count, capped)`.

    `total` is what is rendered (the capped top-K when semantic).
    `total_match_count` is the uncapped semantic count, set only when `capped`.
    """
    geom_literal = await resolve_location_multipolygon(session, request.location)
    floor, ef_search = preset_for(request.strictness)
    semantic = query_vector is not None

    bindings = base_filter_bindings(crisis_id, request, geom_literal)
    bindings.update(_viewport_bindings(bbox))
    bindings["cell_size"] = cluster_cell_size_3857(zoom)
    has_geom = geom_literal is not None
    geom_is_collection = bindings["geom_is_collection"]

    if not semantic:
        sql = _build_cluster_structured_sql(has_geom, geom_is_collection)
        rows = (await session.execute(sql, bindings)).all()
        cells = [_row_to_cell(r) for r in rows]
        total = sum(c.total for c in cells)
        return cells, total, None, False

    await apply_semantic_session_config(session, ef_search)
    add_semantic_bindings(bindings, query_vector, floor)  # pyright: ignore[reportArgumentType]
    bindings["semantic_cap"] = SEMANTIC_CLUSTER_CAP
    bindings["anomalies_only"] = anomalies_only
    sql = _build_cluster_semantic_sql(has_geom, geom_is_collection)
    rows = (await session.execute(sql, bindings)).all()
    cells = [_row_to_cell(r) for r in rows]
    total = sum(c.total for c in cells)
    base_count = int(rows[0].base_count) if rows else 0
    capped = base_count >= SEMANTIC_CLUSTER_CAP
    total_match_count: int | None = None
    if capped:
        total_match_count = await _count_semantic_matches(
            session, bindings=bindings, has_geom=has_geom, geom_is_collection=geom_is_collection
        )
    return cells, total, total_match_count, capped


async def _count_semantic_matches(
    session: AsyncSession,
    *,
    bindings: dict[str, Any],
    has_geom: bool,
    geom_is_collection: bool,
) -> int:
    """Uncapped semantic match count; only run when the cap was hit."""
    geom = geom_clause(geom_is_collection) if has_geom else ""
    sql = text(
        _COUNT_SEMANTIC_MATCHES_SQL_TMPL.format(
            geocode_join=GEOCODE_JOIN,
            structured=STRUCTURED_FILTERS,
            viewport=_VIEWPORT_INTERSECT,
            geom=geom,
        )
    )
    value = (await session.execute(sql, bindings)).scalar()
    return int(value or 0)


def _row_to_cell(row: Any) -> MapClusterCell:
    single_id = getattr(row, "single_id", None)
    single_dc = getattr(row, "single_dc", None)
    single_source = getattr(row, "single_source", None)
    return MapClusterCell(
        lat=float(row.lat),
        lng=float(row.lng),
        total=int(row.total),
        minimal=int(row.minimal),
        partial=int(row.partial),
        complete=int(row.complete),
        distinct_point_count=int(row.distinct_point_count),
        report_id=uuid.UUID(single_id) if single_id else None,
        damage_class=as_damage_class(single_dc) if single_dc else None,
        location_source=single_source if single_source else None,
        location_area_only=bool(getattr(row, "single_area_only", None)),
    )


# Points
#
# Fetch one row past the ceiling to detect overflow without a full count.

_POINT_PROJECTION = """
        r.id,
        r.damage_class as dc,
        st_y(r.map_point::geometry) as lat,
        st_x(r.map_point::geometry) as lng,
        r.map_point_source as source,
        g.area_only as geocode_area_only,
        r.created_at
"""


def _build_points_structured_sql(has_geom: bool, geom_is_collection: bool) -> Any:
    geom = geom_clause(geom_is_collection) if has_geom else ""
    return text(
        f"""
    select
        {_POINT_PROJECTION}
    from public.reports r
    left join public.buildings b on b.id = r.building_id
    {GEOCODE_JOIN}
    where {STRUCTURED_FILTERS}
      and r.map_point is not null
      and {_VIEWPORT_INTERSECT}
    {geom}
    order by r.created_at desc, r.id desc
    limit :point_limit
    """
    )


def _build_points_semantic_sql(has_geom: bool, geom_is_collection: bool) -> Any:
    geom = geom_clause(geom_is_collection) if has_geom else ""
    return text(
        f"""
    select
        {_POINT_PROJECTION},
        (e.embedding <=> cast(:qvec as halfvec)) as distance
    from public.reports r
    join public.report_embeddings e on e.report_id = r.id
    left join public.buildings b on b.id = r.building_id
    {GEOCODE_JOIN}
    where {STRUCTURED_FILTERS}
      and r.map_point is not null
      and {_VIEWPORT_INTERSECT}
      and e.embedding_version = :embedding_version
      and (e.embedding <=> cast(:qvec as halfvec)) <= :max_distance
    {geom}
    order by (e.embedding <=> cast(:qvec as halfvec)) asc, r.id desc
    limit :point_limit
    """
    )


async def fetch_map_points(
    session: AsyncSession,
    *,
    crisis_id: uuid.UUID,
    request: SearchRequest,
    bbox: tuple[float, float, float, float],
    query_vector: list[float] | None,
    anomalies_only: bool,
) -> tuple[list[MapPoint], bool]:
    """Filtered points in the viewport. Returns `(points, overflowed)`.

    On overflow (more than `POINTS_CEILING`) points is empty and the caller
    falls back to clusters.
    """
    geom_literal = await resolve_location_multipolygon(session, request.location)
    floor, ef_search = preset_for(request.strictness)
    semantic = query_vector is not None

    bindings = base_filter_bindings(crisis_id, request, geom_literal)
    bindings.update(_viewport_bindings(bbox))
    bindings["point_limit"] = POINTS_CEILING + 1
    has_geom = geom_literal is not None
    geom_is_collection = bindings["geom_is_collection"]

    if semantic:
        await apply_semantic_session_config(session, ef_search)
        add_semantic_bindings(bindings, query_vector, floor)  # pyright: ignore[reportArgumentType]
        sql = _build_points_semantic_sql(has_geom, geom_is_collection)
    else:
        sql = _build_points_structured_sql(has_geom, geom_is_collection)

    rows = (await session.execute(sql, bindings)).all()
    if len(rows) > POINTS_CEILING:
        return [], True

    anomaly_ids: set[uuid.UUID] = _semantic_anomaly_ids(rows) if semantic else set()
    points = [_row_to_point(r, is_anomaly=r.id in anomaly_ids) for r in rows]
    # Without a query there is no similarity signal, so anomalies-only is ignored.
    if anomalies_only and semantic:
        points = [p for p in points if p.is_anomaly]
    return points, False


def _semantic_anomaly_ids(rows: Sequence[Any]) -> set[uuid.UUID]:
    """Ids 2 sigma below the mean similarity of the returned points.

    The cluster path computes sigma over the capped match set instead.
    """
    sims = [1.0 - float(r.distance) for r in rows if r.distance is not None]
    if len(sims) < 3:
        return set()
    mean = sum(sims) / len(sims)
    stdev = (sum((s - mean) ** 2 for s in sims) / len(sims)) ** 0.5
    if stdev == 0.0:
        return set()
    threshold = mean - 2 * stdev
    return {r.id for r in rows if r.distance is not None and (1.0 - float(r.distance)) < threshold}


def _row_to_point(row: Any, *, is_anomaly: bool) -> MapPoint:
    return MapPoint(
        id=row.id,
        damage_class=as_damage_class(row.dc),
        map_point=LocationOut(lat=float(row.lat), lng=float(row.lng)),
        location_source=row.source,
        location_area_only=bool(row.geocode_area_only) if row.source == "ai_geocode" else False,
        is_anomaly=is_anomaly,
    )


__all__ = [
    "POINTS_CEILING",
    "POINTS_ZOOM",
    "SEMANTIC_CLUSTER_CAP",
    "cluster_cell_size_3857",
    "fetch_map_clusters",
    "fetch_map_points",
]
