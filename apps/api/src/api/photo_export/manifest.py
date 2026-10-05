"""CSV and GeoJSON streaming over a view's matched set, for bundles and data exports.

Reuses the whole-crisis export projection so the field set cannot drift.
"""

from __future__ import annotations

import csv
import io
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.admin.export_queries import CSV_COLUMNS, FEATURE_INNER, csv_cell_select, feature_object_sql
from api.photo_export.resolver import build_match_bindings, match_from_where
from api.schemas.admin_search import SearchRequest

_CRS_URN = "urn:ogc:def:crs:OGC:1.3:CRS84"
_SCHEMA_VERSION = 1

# Must match resolver.export_photo_entry_name.
_PHOTO_FILE_SQL = (
    "case when feat.photo_path is null then '' else "
    "'photos/' || feat.id::text || '.' || "
    r"coalesce(nullif(lower(substring(feat.photo_path from '\.([^./]+)$')), ''), 'jpg') "
    "end"
)


class _BinarySink(Protocol):
    def write(self, data: bytes, /) -> Any: ...


def manifest_filename(fmt: str) -> str:
    return "manifest.geojson" if fmt == "geojson" else "manifest.csv"


def _matched_cte(*, semantic: bool, has_geom: bool, geom_is_collection: bool) -> str:
    where = match_from_where(
        semantic=semantic,
        has_geom=has_geom,
        geom_is_collection=geom_is_collection,
        photo_only=False,
    )
    return f"matched as (select r.id {where})"


def _feature_inner_null_filters() -> dict[str, Any]:
    """Null out the projection's own filters; scoping comes from the `matched` CTE."""
    return {
        "date_from": None,
        "date_to": None,
        "west": None,
        "south": None,
        "east": None,
        "north": None,
    }


async def stream_matched_geojson(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    crisis_id: uuid.UUID,
    request: SearchRequest,
    query_vector: list[float] | None,
    metadata: dict[str, Any],
    with_photo_file: bool,
) -> AsyncIterator[bytes]:
    async with sessionmaker() as session:
        bindings, semantic, has_geom, gic = await build_match_bindings(
            session, crisis_id=crisis_id, request=request, query_vector=query_vector
        )
        bindings.update(_feature_inner_null_filters())
        cte = _matched_cte(semantic=semantic, has_geom=has_geom, geom_is_collection=gic)
        extra = f", 'photo_file', {_PHOTO_FILE_SQL}" if with_photo_file else ""
        sql = text(
            f"""
            with {cte}
            select ({feature_object_sql(extra)})::text as feature
            from ({FEATURE_INNER}) feat
            join matched m on m.id = feat.id
            order by feat.created_at desc, feat.id desc
            """
        )
        header = '{"type":"FeatureCollection","metadata":' + json.dumps(
            metadata, ensure_ascii=False
        )
        yield (header + ',"features":[').encode()
        first = True
        result = await session.stream_scalars(sql, bindings)
        async for feature in result:
            chunk: str = feature
            yield (chunk if first else "," + chunk).encode()
            first = False
        yield b"]}"


async def stream_matched_csv(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    crisis_id: uuid.UUID,
    request: SearchRequest,
    query_vector: list[float] | None,
    with_photo_file: bool,
) -> AsyncIterator[bytes]:
    buffer = io.StringIO()
    writer = csv.writer(buffer)

    def drain() -> bytes:
        chunk = buffer.getvalue().encode("utf-8")
        buffer.seek(0)
        buffer.truncate(0)
        return chunk

    columns = [*CSV_COLUMNS, "photo_file"] if with_photo_file else list(CSV_COLUMNS)
    writer.writerow(columns)
    yield b"\xef\xbb\xbf" + drain()

    async with sessionmaker() as session:
        bindings, semantic, has_geom, gic = await build_match_bindings(
            session, crisis_id=crisis_id, request=request, query_vector=query_vector
        )
        bindings.update(_feature_inner_null_filters())
        cte = _matched_cte(semantic=semantic, has_geom=has_geom, geom_is_collection=gic)
        extra = f", {_PHOTO_FILE_SQL} as photo_file" if with_photo_file else ""
        sql = text(
            f"""
            with {cte}
            select {csv_cell_select(extra)}
            from ({FEATURE_INNER}) feat
            join matched m on m.id = feat.id
            order by feat.created_at desc, feat.id desc
            """
        )
        result = await session.stream(sql, bindings)
        async for row in result:
            writer.writerow(row)
            yield drain()


async def write_geojson_manifest(
    dest: _BinarySink,
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
    crisis_id: uuid.UUID,
    request: SearchRequest,
    query_vector: list[float] | None,
    metadata: dict[str, Any],
) -> None:
    async for chunk in stream_matched_geojson(
        sessionmaker,
        crisis_id=crisis_id,
        request=request,
        query_vector=query_vector,
        metadata=metadata,
        with_photo_file=True,
    ):
        dest.write(chunk)


async def write_csv_manifest(
    dest: _BinarySink,
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
    crisis_id: uuid.UUID,
    request: SearchRequest,
    query_vector: list[float] | None,
) -> None:
    async for chunk in stream_matched_csv(
        sessionmaker,
        crisis_id=crisis_id,
        request=request,
        query_vector=query_vector,
        with_photo_file=True,
    ):
        dest.write(chunk)


def build_geojson_metadata(
    *,
    crisis_id: uuid.UUID,
    crisis_name: str | None,
    request: SearchRequest,
    scope: str,
    report_count: int,
    generated_at: str,
) -> dict[str, Any]:
    return {
        "generated_at": generated_at,
        "crisis": {"id": str(crisis_id), "name": crisis_name},
        "scope": scope,
        "filters": request.model_dump(mode="json", exclude_none=True),
        "report_count": report_count,
        "crs": _CRS_URN,
        "schema_version": _SCHEMA_VERSION,
    }


def build_export_manifest_json(
    *,
    export_id: uuid.UUID,
    crisis_id: uuid.UUID,
    crisis_name: str | None,
    created_by_email: str | None,
    generated_at: str,
    scope: str,
    fmt: str,
    request: SearchRequest,
    report_count: int,
    photo_count: int,
) -> bytes:
    """Provenance file for the bundle; the CSV manifest has no metadata slot of its own."""
    payload = {
        "export_id": str(export_id),
        "crisis": {"id": str(crisis_id), "name": crisis_name},
        "exported_by": created_by_email,
        "generated_at": generated_at,
        "scope": scope,
        "manifest_format": fmt,
        "filters": request.model_dump(mode="json", exclude_none=True),
        "report_count": report_count,
        "photo_count": photo_count,
        "crs": _CRS_URN,
        "schema_version": _SCHEMA_VERSION,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


__all__ = [
    "build_export_manifest_json",
    "build_geojson_metadata",
    "manifest_filename",
    "stream_matched_csv",
    "stream_matched_geojson",
    "write_csv_manifest",
    "write_geojson_manifest",
]
