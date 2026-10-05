"""OvertureDivisionsReader over the divisions tables loaded by
api.scripts.ingest_overture_divisions.
"""

from __future__ import annotations

import json
from typing import cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.areas.overture_divisions_reader import (
    Area,
    AreaNotFoundError,
    Country,
    ResolvedArea,
    as_float,
    as_str_list,
)

_MAX_SEARCH_LIMIT = 50


class PostgresOvertureDivisionsReader:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker
        # Countries only change on re-ingest; restart the API after one.
        self._countries_cache: list[Country] | None = None

    async def search(self, q: str, country: str | None, limit: int) -> list[Area]:
        """Substring match on names_search_text (primary plus all common names).

        Hits may lack a division_area row; the picker falls back to OSM when
        the geometry lookup 404s.
        """
        clamped_limit = max(1, min(limit, _MAX_SEARCH_LIMIT))
        params: dict[str, object] = {
            "pattern": f"%{q}%",
            "country": country.upper() if country is not None else None,
            "limit": clamped_limit,
        }
        sql = text(
            """
            select
                id,
                coalesce(names_common ->> 'en', names_primary) as name,
                subtype,
                country,
                hierarchy_parents as parents,
                bbox_xmin,
                bbox_ymin,
                bbox_xmax,
                bbox_ymax
            from public.overture_divisions
            where names_search_text ilike :pattern
              and (cast(:country as text) is null or country = :country)
            order by population desc nulls last
            limit :limit
            """
        )

        async with self._sessionmaker() as session:
            result = await session.execute(sql, params)
            rows = result.all()

        return [_row_to_area(row) for row in rows]

    async def list_countries(self) -> list[Country]:
        """Every country, deduped by ISO-2 and sorted by name. Cached per instance."""
        if self._countries_cache is not None:
            return self._countries_cache

        sql = text(
            """
            select id, country, coalesce(names_common ->> 'en', names_primary) as name
            from public.overture_divisions
            where subtype = 'country' and country is not null
            """
        )
        async with self._sessionmaker() as session:
            result = await session.execute(sql)
            rows = result.all()

        by_iso: dict[str, Country] = {}
        for row in rows:
            iso2 = str(row.country).upper()
            by_iso.setdefault(
                iso2,
                Country(id=str(row.id), iso2=iso2, name=str(row.name)),
            )
        self._countries_cache = sorted(by_iso.values(), key=lambda c: c.name)
        return self._countries_cache

    async def read_area_geometry(self, area_id: str) -> ResolvedArea:
        """Unioned MultiPolygon and country for a division id.

        A division can have several area rows (e.g. territorial waters).
        """
        sql = text(
            """
            select
                st_asewkb(st_multi(st_union(geometry::geometry))) as wkb,
                max(country) as country,
                count(*) as n
            from public.overture_division_areas
            where division_id = :id
            """
        )
        async with self._sessionmaker() as session:
            result = await session.execute(sql, {"id": area_id})
            row = result.one()

        # Aggregates always return one row; n distinguishes a miss.
        if row.n == 0 or row.wkb is None:
            raise AreaNotFoundError(f"no area with id {area_id!r}")
        return ResolvedArea(
            wkb=bytes(row.wkb),
            country=str(row.country) if row.country is not None else None,
        )

    async def read_area_geojson(self, area_id: str) -> dict[str, object]:
        """GeoJSON MultiPolygon for one division id, parsed so it is not double-encoded."""
        sql = text(
            """
            select
                st_asgeojson(st_multi(st_union(geometry::geometry))) as geojson,
                count(*) as n
            from public.overture_division_areas
            where division_id = :id
            """
        )
        async with self._sessionmaker() as session:
            result = await session.execute(sql, {"id": area_id})
            row = result.one()

        if row.n == 0 or row.geojson is None:
            raise AreaNotFoundError(f"no area with id {area_id!r}")
        parsed: object = json.loads(str(row.geojson))
        if not isinstance(parsed, dict):
            raise TypeError(f"ST_AsGeoJSON returned non-object: {type(parsed).__name__}")
        return cast(dict[str, object], parsed)

    async def read_countries_union(self, iso2_codes: list[str]) -> bytes | None:
        """Union of the given countries' polygons; None if empty or none matched."""
        if not iso2_codes:
            return None
        codes = [c.upper() for c in iso2_codes]
        sql = text(
            """
            select st_asewkb(st_multi(st_union(geometry::geometry))) as wkb
            from public.overture_division_areas
            where subtype = 'country' and country = any(:codes)
            """
        )
        async with self._sessionmaker() as session:
            result = await session.execute(sql, {"codes": codes})
            row = result.one()

        if row.wkb is None:
            return None
        return bytes(row.wkb)

    async def read_countries_geojson_union(self, iso2_codes: list[str]) -> dict[str, object] | None:
        """GeoJSON union of the given countries; None if empty or none matched."""
        if not iso2_codes:
            return None
        codes = [c.upper() for c in iso2_codes]
        sql = text(
            """
            select st_asgeojson(st_multi(st_union(geometry::geometry))) as geojson
            from public.overture_division_areas
            where subtype = 'country' and country = any(:codes)
            """
        )
        async with self._sessionmaker() as session:
            result = await session.execute(sql, {"codes": codes})
            row = result.one()

        if row.geojson is None:
            return None
        parsed: object = json.loads(str(row.geojson))
        if not isinstance(parsed, dict):
            raise TypeError(f"ST_AsGeoJSON returned non-object: {type(parsed).__name__}")
        return cast(dict[str, object], parsed)


def _row_to_area(row: object) -> Area:
    return Area(
        id=str(row.id),  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
        name=str(row.name),  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
        subtype=str(row.subtype),  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
        country=str(row.country) if row.country is not None else None,  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
        parents=as_str_list(row.parents),  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
        bbox=(
            as_float(row.bbox_xmin),  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
            as_float(row.bbox_ymin),  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
            as_float(row.bbox_xmax),  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
            as_float(row.bbox_ymax),  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
        ),
    )


__all__ = ["PostgresOvertureDivisionsReader"]
