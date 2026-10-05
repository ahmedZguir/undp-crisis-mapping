"""SQLAlchemy implementation of CrisisLookup, with fetch_by_id behind CrisisRowCache."""

from __future__ import annotations

import json
import uuid
from typing import Any, cast

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.crises.cache import MISSING, CrisisRowCache
from api.crises.service import CrisisRow

_FETCH_BY_ID_SQL = text(
    "select id, name, status, created_at, public_visibility, public_infra_types "
    "from public.crises where id = :id limit 1"
)

_FETCH_FORM_BY_ID_SQL = text(
    "select form_version, form_schema from public.crises where id = :id limit 1"
)


class SqlAlchemyCrisisLookup:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        cache: CrisisRowCache | None = None,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._cache = cache

    async def fetch_by_id(
        self,
        crisis_id: uuid.UUID,
        *,
        _session: AsyncSession | None = None,
    ) -> CrisisRow | None:
        cache_key = str(crisis_id)
        if self._cache is not None:
            cached = await self._cache.get(cache_key)
            if cached is MISSING:
                return None
            if isinstance(cached, CrisisRow):
                return cached

        if _session is not None:
            row = await self._fetch_in_session(_session, crisis_id)
        else:
            async with self._sessionmaker() as session:
                row = await self._fetch_in_session(session, crisis_id)

        if self._cache is not None:
            await self._cache.put(cache_key, row if row is not None else MISSING)
        return row

    @staticmethod
    async def _fetch_in_session(session: AsyncSession, crisis_id: uuid.UUID) -> CrisisRow | None:
        result = await session.execute(_FETCH_BY_ID_SQL, {"id": str(crisis_id)})
        row = result.mappings().first()
        return _row_to_crisis(row) if row is not None else None

    async def fetch_form_by_id(
        self,
        crisis_id: uuid.UUID,
    ) -> tuple[dict[str, object], int] | None:
        async with self._sessionmaker() as session:
            result = await session.execute(_FETCH_FORM_BY_ID_SQL, {"id": str(crisis_id)})
            row = result.first()
        if row is None:
            return None
        schema: Any = row.form_schema
        # JSONB normally decodes to a dict; tolerate a text column / driver
        # that hands back the raw JSON string.
        if not isinstance(schema, dict):
            schema = json.loads(str(schema))
        return cast("dict[str, object]", schema), int(row.form_version)

    async def fetch_active(self) -> list[CrisisRow]:
        async with self._sessionmaker() as session:
            result = await session.execute(
                text(
                    "select id, name, status, created_at, "
                    "       pmtiles_url, overture_release_pinned, "
                    "       public_visibility, public_infra_types, "
                    "       st_asgeojson(geometry::geometry) as geometry "
                    "from public.crises where status = 'active'"
                )
            )
            rows = result.mappings().all()
        return [_row_to_crisis(r) for r in rows]


def _row_to_crisis(m: RowMapping) -> CrisisRow:
    """Columns absent from the query keep their CrisisRow defaults."""
    infra = m["public_infra_types"]
    return CrisisRow(
        id=_as_uuid(m["id"]),
        name=m["name"],
        status=m["status"],
        created_at=m["created_at"],
        pmtiles_url=m.get("pmtiles_url"),
        overture_release_pinned=m.get("overture_release_pinned"),
        geometry=_parse_geojson(m.get("geometry")),
        public_visibility=m["public_visibility"],
        public_infra_types=list(infra) if infra is not None else None,
    )


def _as_uuid(value: object) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _parse_geojson(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    parsed = json.loads(str(value))
    return cast(dict[str, object], parsed)
