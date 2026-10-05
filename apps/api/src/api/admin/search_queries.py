"""Report search SQL: structured filters plus optional thresholded ANN.

The row query carries no count(*) over () window: it would materialise the
whole matched set before LIMIT. The exact total comes from
compute_search_aggregates.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.admin.report_sql import (
    GEOCODE_JOIN,
    GEOCODE_PROJECTION,
    LOCATION_SOURCE_SQL,
    geocode_meta,
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
from api.schemas import as_damage_class
from api.schemas.admin_search import (
    SearchHit,
    SearchRequest,
)
from api.schemas.common import location_or_none


async def run_search(
    session: AsyncSession,
    *,
    crisis_id: uuid.UUID,
    request: SearchRequest,
    query_vector: list[float] | None,
) -> list[SearchHit]:
    """Matched rows, capped at request.limit; query_vector is None without a query."""
    geom_literal = await resolve_location_multipolygon(session, request.location)
    floor, ef_search = preset_for(request.strictness)

    await apply_semantic_session_config(session, ef_search)

    bindings = base_filter_bindings(crisis_id, request, geom_literal)
    bindings["limit"] = request.limit

    if query_vector is None:
        sql = _build_structured_sql(geom_literal is not None, bindings["geom_is_collection"])
        result = await session.execute(sql, bindings)
    else:
        add_semantic_bindings(bindings, query_vector, floor)
        sql = _build_semantic_sql(geom_literal is not None, bindings["geom_is_collection"])
        result = await session.execute(sql, bindings)

    rows = result.all()
    has_sim = query_vector is not None
    hits = [_row_to_hit(r, has_similarity=has_sim) for r in rows[: request.limit]]
    _flag_anomalies(hits)
    return hits


def _build_structured_sql(has_geom: bool, geom_is_collection: bool) -> Any:
    geom = geom_clause(geom_is_collection) if has_geom else ""
    return text(
        f"""
    with candidates as (
        select
            r.id, r.damage_class, r.description, r.route_description,
            r.infra_type, r.infra_name, r.debris,
            r.building_id, b.name as building_name,
            r.photo_path, r.created_at,
            st_y(r.location::geometry) as report_lat,
            st_x(r.location::geometry) as report_lng,
            st_y(b.centroid::geometry) as building_lat,
            st_x(b.centroid::geometry) as building_lng,
            {GEOCODE_PROJECTION}
            {LOCATION_SOURCE_SQL} as location_source,
            t.description_en
        from public.reports r
        left join public.buildings b on b.id = r.building_id
        left join public.report_translations t on t.report_id = r.id
        {GEOCODE_JOIN}
        where {STRUCTURED_FILTERS}
        {geom}
    )
    select *
    from candidates
    order by created_at desc, id desc
    limit :limit
    """
    )


def _build_semantic_sql(has_geom: bool, geom_is_collection: bool) -> Any:
    geom = geom_clause(geom_is_collection) if has_geom else ""
    return text(
        f"""
    with candidates as (
        select
            r.id, r.damage_class, r.description, r.route_description,
            r.infra_type, r.infra_name, r.debris,
            r.building_id, b.name as building_name,
            r.photo_path, r.created_at,
            st_y(r.location::geometry) as report_lat,
            st_x(r.location::geometry) as report_lng,
            st_y(b.centroid::geometry) as building_lat,
            st_x(b.centroid::geometry) as building_lng,
            {GEOCODE_PROJECTION}
            {LOCATION_SOURCE_SQL} as location_source,
            t.description_en,
            (e.embedding <=> cast(:qvec as halfvec)) as distance
        from public.reports r
        join public.report_embeddings e on e.report_id = r.id
        left join public.buildings b on b.id = r.building_id
        left join public.report_translations t on t.report_id = r.id
        {GEOCODE_JOIN}
        where {STRUCTURED_FILTERS}
          and e.embedding_version = :embedding_version
          and (e.embedding <=> cast(:qvec as halfvec)) <= :max_distance
        {geom}
    )
    select *, (1.0 - distance) as similarity
    from candidates
    order by distance asc, id desc
    limit :limit
    """
    )


# Seeded random sampling for the summary. The seed comes from the filter
# signature, so regenerating a summary reads the same sample.


def seed_from_signature(signature: str) -> float:
    """Seed in [-1, 1] for setseed."""
    return int(signature[:8], 16) / 0xFFFFFFFF * 2.0 - 1.0


def _build_sample_ids_sql(has_geom: bool, geom_is_collection: bool, semantic: bool) -> Any:
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
    select r.id
    from public.reports r
    {semantic_join}
    left join public.buildings b on b.id = r.building_id
    {GEOCODE_JOIN}
    where {STRUCTURED_FILTERS}
    {semantic_filter}
    {geom}
    order by random()
    limit :sample_size
    """
    )


async def sample_report_ids(
    session: AsyncSession,
    *,
    crisis_id: uuid.UUID,
    request: SearchRequest,
    query_vector: list[float] | None,
    sample_size: int,
    seed: float,
) -> list[uuid.UUID]:
    """Up to sample_size ids from the full filtered set, reproducible for a given seed."""
    geom_literal = await resolve_location_multipolygon(session, request.location)
    floor, ef_search = preset_for(request.strictness)
    semantic = query_vector is not None
    geom_is_collection = is_geometry_collection(geom_literal)

    if semantic:
        await apply_semantic_session_config(session, ef_search)

    bindings = base_filter_bindings(crisis_id, request, geom_literal)
    bindings["sample_size"] = sample_size
    if semantic:
        add_semantic_bindings(bindings, query_vector, floor)  # pyright: ignore[reportArgumentType]

    await session.execute(text("select setseed(:seed)"), {"seed": seed})
    sql = _build_sample_ids_sql(geom_literal is not None, geom_is_collection, semantic)
    result = await session.execute(sql, bindings)
    return [row.id for row in result.all()]


def _flag_anomalies(hits: list[SearchHit]) -> None:
    """Flag semantic hits more than two sigma below the mean similarity, in place."""
    sims = [h.similarity for h in hits if h.similarity is not None]
    if len(sims) < 3:
        return
    mean = sum(sims) / len(sims)
    variance = sum((s - mean) ** 2 for s in sims) / len(sims)
    stdev = variance**0.5
    if stdev == 0.0:
        return
    threshold = mean - 2 * stdev
    for h in hits:
        if h.similarity is not None and h.similarity < threshold:
            h.is_anomaly = True


def _row_to_hit(row: Any, *, has_similarity: bool) -> SearchHit:
    location = location_or_none(row.report_lat, row.report_lng)
    building_centroid = location_or_none(row.building_lat, row.building_lng)
    geocode = location_or_none(getattr(row, "geocode_lat", None), getattr(row, "geocode_lon", None))
    source = getattr(row, "location_source", None)
    radius_m, area_only, confidence = geocode_meta(row, source)
    return SearchHit(
        id=row.id,
        damage_class=as_damage_class(row.damage_class),
        description=row.description,
        description_en=getattr(row, "description_en", None),
        infra_type=list(row.infra_type) if row.infra_type is not None else None,
        infra_name=row.infra_name,
        route_description=getattr(row, "route_description", None),
        debris=_debris_to_yesno(row.debris),
        building_id=row.building_id,
        building_name=row.building_name,
        location=location,
        map_point=location or building_centroid or geocode,
        location_source=source,
        location_radius_m=radius_m,
        location_area_only=area_only,
        location_confidence=confidence,
        photo_path=row.photo_path,
        created_at=row.created_at,
        similarity=float(row.similarity) if has_similarity else None,
    )


def _debris_to_yesno(value: Any) -> bool | None:
    # 'unknown' and NULL both map to None.
    if value == "yes":
        return True
    if value == "no":
        return False
    return None

    if isinstance(value, int | float):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# Hit details for chat and summary


_HITS_BY_IDS_SQL = text(
    """
    select
        r.id, r.damage_class, r.description, r.infra_type, r.infra_name,
        r.debris, r.building_id, b.name as building_name,
        r.photo_path, r.created_at,
        st_y(r.location::geometry) as report_lat,
        st_x(r.location::geometry) as report_lng,
        st_y(b.centroid::geometry) as building_lat,
        st_x(b.centroid::geometry) as building_lng,
        t.description_en,
        c.caption
    from public.reports r
    left join public.buildings b on b.id = r.building_id
    left join public.report_translations t on t.report_id = r.id
    left join public.image_captions c on c.report_id = r.id
    where r.id = any(:ids)
    """
)


async def fetch_hits_by_ids(
    session: AsyncSession, ids: list[uuid.UUID]
) -> dict[uuid.UUID, dict[str, Any]]:
    """Keyed by id so callers keep their own (ranked) order."""
    if not ids:
        return {}
    result = await session.execute(_HITS_BY_IDS_SQL, {"ids": [str(i) for i in ids]})
    out: dict[uuid.UUID, dict[str, Any]] = {}
    for r in result.all():
        out[r.id] = {
            "id": r.id,
            "damage_class": r.damage_class,
            "description": r.description,
            "description_en": r.description_en,
            "caption": r.caption,
            "infra_type": list(r.infra_type) if r.infra_type is not None else None,
            "infra_name": r.infra_name,
            "debris": r.debris,
            "building_id": r.building_id,
            "building_name": r.building_name,
            "photo_path": r.photo_path,
            "created_at": r.created_at,
            "report_lat": r.report_lat,
            "report_lng": r.report_lng,
            "building_lat": r.building_lat,
            "building_lng": r.building_lng,
        }
    return out
