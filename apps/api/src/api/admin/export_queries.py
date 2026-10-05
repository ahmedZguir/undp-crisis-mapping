"""Report export as GeoJSON or CSV, streamed from a server-side cursor.

Features are assembled in Postgres. Geometry precedence is submitted pin, then
building centroid, then AI geocode; `location_source` says which was used.
Photo EXIF GPS is a separate `photo_gps` property.
"""

from __future__ import annotations

import csv
import io
import json
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.core.listing import Bbox

# RFC 7946 deprecates the `crs` member; this only labels `metadata`.
_CRS_URN = "urn:ogc:def:crs:OGC:1.3:CRS84"
_SCHEMA_VERSION = 1

# Shared by the count and feature queries. A NULL :west means no bbox, which
# includes unlocated rows; with a bbox they are dropped, as in the list view.
_FILTER_WHERE = """
    where r.crisis_id = :crisis_id
      and (cast(:damage_class as text) is null or r.damage_class = :damage_class)
      and (
          cast(:date_from as timestamptz) is null
          or r.created_at >= cast(:date_from as timestamptz)
      )
      and (
          cast(:date_to as timestamptz) is null
          or r.created_at <= cast(:date_to as timestamptz)
      )
      and (
          cast(:west as double precision) is null
          or (
              -- bbox matches pin/building points only, using map_point's GiST index.
              st_intersects(
                  r.map_point,
                  st_makeenvelope(:west, :south, :east, :north, 4326)::geography
              )
              and r.map_point_source in ('submitted_pin', 'building_centroid')
          )
      )
"""


_COUNT_SQL = text(
    f"""
    select count(*)
    from public.reports r
    left join public.buildings b on b.id = r.building_id
    {_FILTER_WHERE}
    """
)


# One flat row per report with geometry resolved and enrichment joined. The
# GeoJSON, CSV and photo-export queries all wrap this.
_FEATURE_INNER = f"""
    select
        r.id,
        r.crisis_id,
        c.name as crisis_name,
        r.building_id,
        r.client_id,
        r.created_at,
        r.photo_captured_at,
        r.damage_class,
        r.infra_type,
        r.infra_name,
        r.crisis_type_detailed as crisis_type,
        r.debris,
        r.description,
        t.description_lang,
        t.description_en,
        r.route_description,
        t.route_description_en,
        -- AI geocodes store bare lat/lon, so build the point here.
        coalesce(
            r.location::geometry,
            b.centroid::geometry,
            case when g.lat is not null and g.lon is not null
                 then st_setsrid(st_makepoint(g.lon, g.lat), 4326)
                 else null end
        ) as geom,
        case
            when r.location is not null then 'submitted_pin'
            when b.centroid is not null then 'building_centroid'
            when g.lat is not null and g.lon is not null then 'ai_geocode'
            else null
        end as location_source,
        case when r.photo_exif_gps is not null
             then json_build_array(
                      st_x(r.photo_exif_gps::geometry),
                      st_y(r.photo_exif_gps::geometry)
                  )
             else null end as photo_gps,
        (r.photo_path is not null) as has_photo,
        r.photo_path,
        r.generic_answers as survey,
        case when g.lat is not null and g.lon is not null
             then json_build_object(
                      'coordinates', json_build_array(g.lon, g.lat),
                      'confidence', g.confidence,
                      'granularity_tier', g.granularity_tier,
                      'radius_m', g.radius_m,
                      'area_only', g.area_only,
                      'source', g.source
                  )
             else null end as ai_geocode,
        ic.caption as ai_caption,
        ic.relevance_label as ai_relevance
    from public.reports r
    left join public.crises c on c.id = r.crisis_id
    left join public.buildings b on b.id = r.building_id
    left join public.report_translations t on t.report_id = r.id
    left join public.image_captions ic on ic.report_id = r.id
    left join public.report_geocodes g on g.report_id = r.id
    {_FILTER_WHERE}
"""


# GeoJSON Feature for one `feat.*` row, shared with the photo-export manifest.
# `extra_properties` is appended to `properties` and must start with a comma.
def feature_object_sql(extra_properties: str = "") -> str:
    return f"""
        json_build_object(
            'type', 'Feature',
            -- Feature id is the report UUID, a sibling of properties per RFC 7946.
            'id', feat.id,
            'geometry',
                case when feat.geom is not null
                     then st_asgeojson(feat.geom)::json
                     else null end,
            'properties', json_build_object(
                'crisis_id',            feat.crisis_id,
                'crisis_name',          feat.crisis_name,
                'building_id',          feat.building_id,
                'client_id',            feat.client_id,
                'created_at',
                    to_char(feat.created_at at time zone 'UTC',
                            'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'),
                'photo_captured_at',
                    case when feat.photo_captured_at is not null
                         then to_char(feat.photo_captured_at at time zone 'UTC',
                                      'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"')
                         else null end,
                'damage_class',         feat.damage_class,
                'infra_type',           feat.infra_type,
                'infra_name',           feat.infra_name,
                'crisis_type',          feat.crisis_type,
                'debris',               feat.debris,
                'description',          feat.description,
                'description_lang',     feat.description_lang,
                'description_en',       feat.description_en,
                'route_description',    feat.route_description,
                'route_description_en', feat.route_description_en,
                'location_source',      feat.location_source,
                'photo_gps',            feat.photo_gps,
                'has_photo',            feat.has_photo,
                'survey',               feat.survey,
                'ai_geocode',           feat.ai_geocode,
                'ai_caption',           feat.ai_caption,
                'ai_relevance',         feat.ai_relevance{extra_properties}
            )
        )"""


# Serialised to text in Postgres so the generator writes it out without re-encoding.
_FEATURE_SQL = text(
    f"""
    select ({feature_object_sql()})::text as feature
    from ({_FEATURE_INNER}) feat
    order by feat.created_at desc, feat.id desc
    """
)


# Must match the order in csv_cell_select. Nested fields are JSON text cells.
_CSV_COLUMNS = [
    "report_id",
    "crisis_id",
    "crisis_name",
    "building_id",
    "client_id",
    "created_at",
    "photo_captured_at",
    "damage_class",
    "infra_type",
    "infra_name",
    "crisis_type",
    "debris",
    "description",
    "description_lang",
    "description_en",
    "route_description",
    "route_description_en",
    "location_source",
    "longitude",
    "latitude",
    "photo_gps",
    "has_photo",
    "survey",
    "ai_geocode",
    "ai_caption",
    "ai_relevance",
]


# CSV cells for one `feat.*` row, shared with the photo-export manifest. Quoting
# is left to csv.writer. `extra_columns` must start with a comma.
def csv_cell_select(extra_columns: str = "") -> str:
    return f"""
        feat.id as report_id,
        feat.crisis_id,
        feat.crisis_name,
        feat.building_id,
        feat.client_id,
        to_char(feat.created_at at time zone 'UTC',
                'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"') as created_at,
        case when feat.photo_captured_at is not null
             then to_char(feat.photo_captured_at at time zone 'UTC',
                          'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"')
             else null end as photo_captured_at,
        feat.damage_class,
        to_json(feat.infra_type)::text as infra_type,
        feat.infra_name,
        feat.crisis_type,
        feat.debris,
        feat.description,
        feat.description_lang,
        feat.description_en,
        feat.route_description,
        feat.route_description_en,
        feat.location_source,
        st_x(feat.geom) as longitude,
        st_y(feat.geom) as latitude,
        feat.photo_gps::text as photo_gps,
        feat.has_photo,
        feat.survey::text as survey,
        feat.ai_geocode::text as ai_geocode,
        feat.ai_caption,
        feat.ai_relevance{extra_columns}"""


_CSV_SQL = text(
    f"""
    select {csv_cell_select()}
    from ({_FEATURE_INNER}) feat
    order by feat.created_at desc, feat.id desc
    """
)


# Used by api.photo_export.manifest.
FEATURE_INNER = _FEATURE_INNER
CSV_COLUMNS = _CSV_COLUMNS


def _export_params(
    *,
    crisis_id: uuid.UUID,
    damage_class: str | None,
    bbox: Bbox | None,
    date_from: datetime | None,
    date_to: datetime | None,
) -> dict[str, Any]:
    return {
        "crisis_id": str(crisis_id),
        "damage_class": damage_class,
        "date_from": date_from,
        "date_to": date_to,
        "west": bbox.west if bbox is not None else None,
        "south": bbox.south if bbox is not None else None,
        "east": bbox.east if bbox is not None else None,
        "north": bbox.north if bbox is not None else None,
    }


async def fetch_crisis_name(session: AsyncSession, crisis_id: uuid.UUID) -> str | None:
    """None if the crisis doesn't exist, so the route can 404 before streaming."""
    row = (
        await session.execute(
            text("select name from public.crises where id = :id"),
            {"id": str(crisis_id)},
        )
    ).first()
    return row.name if row is not None else None


def _build_metadata(
    *,
    crisis_id: uuid.UUID,
    crisis_name: str,
    damage_class: str | None,
    bbox: Bbox | None,
    date_from: datetime | None,
    date_to: datetime | None,
    report_count: int,
    generated_at: str,
) -> dict[str, Any]:
    return {
        "generated_at": generated_at,
        "crisis": {"id": str(crisis_id), "name": crisis_name},
        "filters": {
            "damage_class": damage_class,
            "bbox": [bbox.west, bbox.south, bbox.east, bbox.north] if bbox is not None else None,
            "date_from": date_from.isoformat() if date_from is not None else None,
            "date_to": date_to.isoformat() if date_to is not None else None,
        },
        "report_count": report_count,
        "crs": _CRS_URN,
        "schema_version": _SCHEMA_VERSION,
    }


async def stream_feature_collection(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    crisis_id: uuid.UUID,
    crisis_name: str,
    damage_class: str | None,
    bbox: Bbox | None,
    date_from: datetime | None,
    date_to: datetime | None,
    generated_at: str,
) -> AsyncIterator[bytes]:
    """Yield a FeatureCollection as UTF-8 chunks, metadata first, one feature per chunk.

    Owns its session for the life of the stream so the count and the cursor
    see the same snapshot.
    """
    params = _export_params(
        crisis_id=crisis_id,
        damage_class=damage_class,
        bbox=bbox,
        date_from=date_from,
        date_to=date_to,
    )
    async with sessionmaker() as session:
        report_count = int(await session.scalar(_COUNT_SQL, params) or 0)
        metadata = _build_metadata(
            crisis_id=crisis_id,
            crisis_name=crisis_name,
            damage_class=damage_class,
            bbox=bbox,
            date_from=date_from,
            date_to=date_to,
            report_count=report_count,
            generated_at=generated_at,
        )
        header = '{"type":"FeatureCollection","metadata":' + json.dumps(
            metadata, ensure_ascii=False
        )
        yield (header + ',"features":[').encode()

        first = True
        result = await session.stream_scalars(_FEATURE_SQL, params)
        async for feature in result:
            chunk: str = feature
            yield (chunk if first else "," + chunk).encode()
            first = False

        yield b"]}"


async def stream_csv(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    crisis_id: uuid.UUID,
    damage_class: str | None,
    bbox: Bbox | None,
    date_from: datetime | None,
    date_to: datetime | None,
) -> AsyncIterator[bytes]:
    """CSV counterpart of `stream_feature_collection`, without the metadata header."""
    params = _export_params(
        crisis_id=crisis_id,
        damage_class=damage_class,
        bbox=bbox,
        date_from=date_from,
        date_to=date_to,
    )
    buffer = io.StringIO()
    writer = csv.writer(buffer)

    def drain() -> bytes:
        chunk = buffer.getvalue().encode("utf-8")
        buffer.seek(0)
        buffer.truncate(0)
        return chunk

    # The BOM makes Excel read the file as UTF-8 (Arabic text); other parsers ignore it.
    writer.writerow(_CSV_COLUMNS)
    yield b"\xef\xbb\xbf" + drain()

    async with sessionmaker() as session:
        result = await session.stream(_CSV_SQL, params)
        async for row in result:
            writer.writerow(row)
            yield drain()
