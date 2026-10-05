"""Per-building report aggregation for the admin buildings map layer."""

from __future__ import annotations

import json
import uuid
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
from api.core.listing import Bbox
from api.schemas import as_damage_class
from api.schemas.admin_buildings import (
    AdminBuildingFeature,
    AdminBuildingProperties,
    AdminBuildingsFeatureCollection,
    MultiPolygonGeometry,
    PolygonGeometry,
)
from api.schemas.admin_search import SearchRequest

# All report filtering happens in crisis_reports, so "latest damage class" is the
# latest matching report and only buildings with matching reports appear.
# DISTINCT ON picks the newest report per building.


def _build_building_stats_sql(*, has_geom: bool, geom_is_collection: bool, semantic: bool) -> Any:
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
    return text(
        f"""
    with crisis_reports as (
        select r.building_id, r.damage_class, r.created_at
          from public.reports r
          {semantic_join}
          left join public.buildings b on b.id = r.building_id
          {GEOCODE_JOIN}
         where {STRUCTURED_FILTERS}
           and r.building_id is not null
           {semantic_filter}
        {geom}
    ),
    latest_per_building as (
        select distinct on (building_id)
               building_id, damage_class as latest_damage_class, created_at as latest_at
          from crisis_reports
         order by building_id, created_at desc
    ),
    per_building as (
        select
            r.building_id,
            count(*)::int                                              as report_count,
            count(*) filter (where r.damage_class = 'minimal')::int    as minimal_count,
            count(*) filter (where r.damage_class = 'partial')::int    as partial_count,
            count(*) filter (where r.damage_class = 'complete')::int   as complete_count
          from crisis_reports r
         group by r.building_id
    )
    select
        b.id,
        b.name,
        st_asgeojson(b.footprint::geometry)::text                  as footprint_geojson,
        st_y(b.centroid::geometry)                                 as centroid_lat,
        st_x(b.centroid::geometry)                                 as centroid_lng,
        p.report_count,
        p.minimal_count,
        p.partial_count,
        p.complete_count,
        l.latest_damage_class,
        l.latest_at
      from per_building p
      join public.buildings b on b.id = p.building_id
      join latest_per_building l on l.building_id = p.building_id
     where st_intersects(
              b.footprint,
              st_makeenvelope(:west, :south, :east, :north, 4326)::geography
           )
     order by l.latest_at desc
     limit :fetch_limit
    """
    )


async def list_building_stats(
    session: AsyncSession,
    *,
    crisis_id: uuid.UUID,
    request: SearchRequest,
    bbox: Bbox,
    query_vector: list[float] | None,
    limit: int,
) -> AdminBuildingsFeatureCollection:
    """Filtered per-building stats inside `bbox` as a FeatureCollection.

    Fetches `limit + 1` rows to set `truncated` without a count query.
    """
    geom_literal = await resolve_location_multipolygon(session, request.location)
    floor, ef_search = preset_for(request.strictness)
    semantic = query_vector is not None

    if semantic:
        await apply_semantic_session_config(session, ef_search)

    bindings = base_filter_bindings(crisis_id, request, geom_literal)
    bindings.update(
        {
            "west": bbox.west,
            "south": bbox.south,
            "east": bbox.east,
            "north": bbox.north,
            "fetch_limit": limit + 1,
        }
    )
    if semantic:
        add_semantic_bindings(bindings, query_vector, floor)  # pyright: ignore[reportArgumentType]

    sql = _build_building_stats_sql(
        has_geom=geom_literal is not None,
        geom_is_collection=bindings["geom_is_collection"],
        semantic=semantic,
    )
    result = await session.execute(sql, bindings)
    rows = result.all()
    truncated = len(rows) > limit
    page = rows[:limit]
    return AdminBuildingsFeatureCollection(
        features=[_row_to_feature(r) for r in page],
        truncated=truncated,
    )


def _row_to_feature(row: Any) -> AdminBuildingFeature:
    geometry_dict = json.loads(row.footprint_geojson)
    geometry_type = geometry_dict.get("type")
    if geometry_type == "MultiPolygon":
        geometry: PolygonGeometry | MultiPolygonGeometry = MultiPolygonGeometry(
            coordinates=geometry_dict["coordinates"]
        )
    elif geometry_type == "Polygon":
        geometry = PolygonGeometry(coordinates=geometry_dict["coordinates"])
    else:
        # footprint is geography(MultiPolygon); fail loudly if that ever changes.
        raise ValueError(f"unexpected building geometry type: {geometry_type!r}")
    return AdminBuildingFeature(
        geometry=geometry,
        properties=AdminBuildingProperties(
            building_id=str(row.id),
            name=row.name,
            report_count=int(row.report_count),
            latest_damage_class=as_damage_class(row.latest_damage_class),
            latest_at=row.latest_at,
            minimal_count=int(row.minimal_count),
            partial_count=int(row.partial_count),
            complete_count=int(row.complete_count),
            centroid_lat=float(row.centroid_lat),
            centroid_lng=float(row.centroid_lng),
        ),
    )
