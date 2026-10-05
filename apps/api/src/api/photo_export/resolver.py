"""Resolve which reports a photo export covers, using the live search filters.

An empty SearchRequest is the whole-crisis scope; with no spatial predicate it
includes reports that have no geometry.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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
from api.schemas.admin_search import SearchRequest

# Average photo after the PWA re-encode; used only by the disk pre-flight.
EST_PHOTO_BYTES = 250 * 1024


@dataclass(frozen=True, slots=True)
class MatchCounts:
    total: int
    """All matched reports (drives the manifest scope + `report_count`)."""
    with_photo: int
    """Matched reports carrying a photo (the streaming work unit + disk input)."""


@dataclass(frozen=True, slots=True)
class PhotoRow:
    id: uuid.UUID
    photo_path: str


def request_from_filters(filters: dict[str, Any]) -> SearchRequest:
    return SearchRequest.model_validate(filters or {})


def export_photo_entry_name(report_id: uuid.UUID | str, photo_path: str | None) -> str | None:
    """`photos/<report_id>.<ext>`, or None without a photo. Mirrored in SQL by manifest.py."""
    if not photo_path:
        return None
    tail = photo_path.rsplit("/", 1)[-1]
    ext = tail.rsplit(".", 1)[-1].lower() if "." in tail else ""
    return f"photos/{report_id}.{ext or 'jpg'}"


async def build_match_bindings(
    session: AsyncSession,
    *,
    crisis_id: uuid.UUID,
    request: SearchRequest,
    query_vector: list[float] | None,
) -> tuple[dict[str, Any], bool, bool, bool]:
    """Return `(bindings, semantic, has_geom, geom_is_collection)`.

    Call it in the session that runs the query: it sets `set local` HNSW options.
    """
    geom_literal = await resolve_location_multipolygon(session, request.location)
    bindings = base_filter_bindings(crisis_id, request, geom_literal)
    semantic = query_vector is not None
    if semantic:
        floor, ef_search = preset_for(request.strictness)
        add_semantic_bindings(bindings, query_vector, floor)  # pyright: ignore[reportArgumentType]
        await apply_semantic_session_config(session, ef_search)
    return bindings, semantic, geom_literal is not None, bool(bindings["geom_is_collection"])


def match_from_where(
    *, semantic: bool, has_geom: bool, geom_is_collection: bool, photo_only: bool
) -> str:
    semantic_join = "join public.report_embeddings e on e.report_id = r.id" if semantic else ""
    semantic_filter = (
        """
          and e.embedding_version = :embedding_version
          and (e.embedding <=> cast(:qvec as halfvec)) <= :max_distance
        """
        if semantic
        else ""
    )
    geom = geom_clause(geom_is_collection) if has_geom else ""
    photo = "and r.photo_path is not null" if photo_only else ""
    return f"""
        from public.reports r
        {semantic_join}
        left join public.buildings b on b.id = r.building_id
        {GEOCODE_JOIN}
        where {STRUCTURED_FILTERS}
          {photo}
          {semantic_filter}
        {geom}
    """


async def count_match_set(
    session: AsyncSession,
    *,
    crisis_id: uuid.UUID,
    request: SearchRequest,
    query_vector: list[float] | None,
) -> MatchCounts:
    bindings, semantic, has_geom, gic = await build_match_bindings(
        session, crisis_id=crisis_id, request=request, query_vector=query_vector
    )
    where = match_from_where(
        semantic=semantic, has_geom=has_geom, geom_is_collection=gic, photo_only=False
    )
    sql = text(
        f"""
        select count(*) as total,
               count(*) filter (where r.photo_path is not null) as with_photo
        {where}
        """
    )
    row = (await session.execute(sql, bindings)).one()
    return MatchCounts(total=int(row.total), with_photo=int(row.with_photo))


async def stream_photo_rows(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    crisis_id: uuid.UUID,
    request: SearchRequest,
    query_vector: list[float] | None,
) -> AsyncIterator[PhotoRow]:
    """Stream matched photo rows, newest first, through a server-side cursor."""
    async with sessionmaker() as session:
        bindings, semantic, has_geom, gic = await build_match_bindings(
            session, crisis_id=crisis_id, request=request, query_vector=query_vector
        )
        where = match_from_where(
            semantic=semantic, has_geom=has_geom, geom_is_collection=gic, photo_only=True
        )
        sql = text(
            f"""
            select r.id, r.photo_path
            {where}
            order by r.created_at desc, r.id desc
            """
        )
        result = await session.stream(sql, bindings)
        async for row in result:
            yield PhotoRow(id=row.id, photo_path=row.photo_path)


__all__ = [
    "EST_PHOTO_BYTES",
    "MatchCounts",
    "PhotoRow",
    "build_match_bindings",
    "count_match_set",
    "export_photo_entry_name",
    "match_from_where",
    "request_from_filters",
    "stream_photo_rows",
]
