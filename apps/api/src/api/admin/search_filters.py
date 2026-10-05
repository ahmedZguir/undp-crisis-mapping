"""Filter assembly shared by search, aggregates, map, buildings and photo export."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.ai import EMBEDDING_VERSION, halfvec
from api.schemas.admin_search import (
    LocationFilter,
    SearchRequest,
    Strictness,
)

# (minimum cosine similarity, hnsw.ef_search). Floors are uncalibrated
# placeholders.

_STRICTNESS_PRESETS: dict[Strictness, tuple[float, int]] = {
    "loose": (0.40, 200),
    "balanced": (0.45, 80),
    "strict": (0.50, 40),
}


def preset_for(strictness: Strictness) -> tuple[float, int]:
    return _STRICTNESS_PRESETS[strictness]


# Stable hash of the normalised filter: cache key for summaries and the scope
# id that summary and chat calls echo back.


def filter_signature(crisis_id: uuid.UUID, request: SearchRequest) -> str:
    payload = {
        "crisis_id": str(crisis_id),
        "time_from": request.time_window.from_.isoformat()
        if request.time_window and request.time_window.from_
        else None,
        "time_to": request.time_window.to.isoformat()
        if request.time_window and request.time_window.to
        else None,
        "location": _location_signature(request.location),
        "query": request.query or None,
        "strictness": request.strictness,
        "infra_types": sorted(request.infra_types) if request.infra_types else None,
        "debris": request.debris,
        "location_kind": request.location_kind,
        "damage_class": request.damage_class,
        "building_id": str(request.building_id) if request.building_id else None,
        "limit": request.limit,
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def _location_signature(loc: LocationFilter | None) -> dict[str, Any] | None:
    if loc is None:
        return None
    return {
        "division_ids": sorted(loc.division_ids) if loc.division_ids else None,
        "polygon": loc.polygon,
        "bbox": list(loc.bbox) if loc.bbox else None,
    }


# Location geometry


async def resolve_location_multipolygon(
    session: AsyncSession, location: LocationFilter | None
) -> str | None:
    """GeoJSON for the location filter: one geometry, or a JSON array of several.

    Divisions, drawn polygon and bbox are combined; None when none are set.
    """
    if location is None:
        return None
    pieces: list[str] = []

    if location.division_ids:
        result = await session.execute(
            text(
                "select st_asgeojson(st_union(geometry::geometry))::text as g "
                "from public.overture_division_areas "
                "where division_id = any(:ids)"
            ),
            {"ids": location.division_ids},
        )
        row = result.first()
        if row is not None and row.g:
            pieces.append(row.g)

    if location.polygon:
        # PostGIS rejects malformed GeoJSON at the cast.
        pieces.append(json.dumps(location.polygon))

    if location.bbox:
        w, s, e, n = location.bbox
        pieces.append(
            json.dumps(
                {
                    "type": "Polygon",
                    "coordinates": [
                        [[w, s], [e, s], [e, n], [w, n], [w, s]],
                    ],
                }
            )
        )

    if not pieces:
        return None
    return _union_geojson(pieces)


def _union_geojson(geojson_pieces: list[str]) -> str:
    """One piece as-is, several as a JSON array that the SQL collects."""
    if len(geojson_pieces) == 1:
        return geojson_pieces[0]
    return json.dumps([json.loads(p) for p in geojson_pieces])


# SQL builders


STRUCTURED_FILTERS = """
        r.crisis_id = :crisis_id
        and (cast(:from_ts as timestamptz) is null or r.created_at >= cast(:from_ts as timestamptz))
        and (cast(:to_ts   as timestamptz) is null or r.created_at <= cast(:to_ts   as timestamptz))
        and (cast(:damage_class as text) is null or r.damage_class = cast(:damage_class as text))
        and (cast(:infra_types as text[]) is null or r.infra_type && cast(:infra_types as text[]))
        and (
            cast(:debris as text) is null
            or r.debris = cast(:debris as text)
        )
        and (
            :location_kind = 'any'
            or (
                :location_kind = 'exact'
                and (r.location is not null or b.centroid is not null)
            )
            or (
                :location_kind = 'ai_geocode'
                and r.location is null
                and b.centroid is null
                and g.lat is not null
                and g.lon is not null
            )
        )
        and (cast(:building_id as uuid) is null or r.building_id = cast(:building_id as uuid))
"""


def geom_clause(geom_is_collection: bool) -> str:
    if geom_is_collection:
        # Several pieces arrive as a JSON array of GeoJSON geometries.
        return """
        and st_intersects(
            r.map_point,
            (
                select st_collect(st_geomfromgeojson(elem::text))
                from jsonb_array_elements(cast(:geom_literal as jsonb)) as elem
            )::geography
        )
        """
    return """
        and st_intersects(
            r.map_point,
            st_geomfromgeojson(cast(:geom_literal as text))::geography
        )
    """


# Filter assembly shared by search rows, aggregates, sampling, map_queries and
# building_queries.


def base_filter_bindings(
    crisis_id: uuid.UUID, request: SearchRequest, geom_literal: str | None
) -> dict[str, Any]:
    """Structured-filter bindings; callers add semantic and per-query extras."""
    return {
        "crisis_id": str(crisis_id),
        "from_ts": request.time_window.from_ if request.time_window else None,
        "to_ts": request.time_window.to if request.time_window else None,
        "infra_types": request.infra_types if request.infra_types else None,
        "damage_class": (None if request.damage_class == "any" else request.damage_class),
        "debris": _debris_predicate(request.debris),
        "location_kind": request.location_kind,
        "building_id": str(request.building_id) if request.building_id else None,
        "geom_literal": geom_literal,
        "geom_is_collection": is_geometry_collection(geom_literal),
    }


def add_semantic_bindings(
    bindings: dict[str, Any], query_vector: list[float], floor: float
) -> None:
    bindings["qvec"] = halfvec.literal(query_vector)  # pyright: ignore[reportArgumentType]
    bindings["max_distance"] = 1.0 - floor
    bindings["embedding_version"] = EMBEDDING_VERSION


async def apply_semantic_session_config(session: AsyncSession, ef_search: int) -> None:
    """Transaction-local HNSW settings; iterative_scan avoids misses under selective filters."""
    await session.execute(
        text("select set_config('hnsw.ef_search', :ef, true)"),
        {"ef": str(ef_search)},
    )
    await session.execute(text("set local hnsw.iterative_scan = 'strict_order'"))


def _debris_predicate(value: str) -> str | None:
    # reports.debris is text ('yes' | 'no' | 'unknown' | NULL), not a bool.
    if value in ("yes", "no"):
        return value
    return None


def is_geometry_collection(geom_literal: str | None) -> bool:
    if geom_literal is None:
        return False
    return geom_literal.lstrip().startswith("[")
