"""Bulk load of Overture buildings via an unlogged staging table, COPY, and a
set-based upsert into public.buildings.

public.buildings is only ever upserted into; truncate/drop here touch only the
per-crisis table in the ingest schema. Live indexes are never dropped.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable, Sequence
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.buildings.overture_building_reader import BuildingRow

logger = logging.getLogger(__name__)

# The runtime role has no CREATE on public, so scratch tables live here.
_STAGE_SCHEMA = "ingest"

# source and properties are constants supplied by the upsert SELECT.
_STAGE_COLUMNS = (
    "source_id",
    "footprint_wkb",
    "name",
    "building_class",
    "height_m",
    "num_floors",
)


def _stage_table(crisis_id: uuid.UUID) -> str:
    """Per-crisis staging table name; uuid hex is always a safe identifier."""
    return f"buildings_stage_{crisis_id.hex}"


class BuildingsBulkLoader:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def create_stage(self, crisis_id: uuid.UUID) -> None:
        """Create this crisis's unlogged, index-free staging table. Idempotent."""
        table = _stage_table(crisis_id)
        async with self._sessionmaker() as session, session.begin():
            await session.execute(
                text(
                    f"create unlogged table if not exists {_STAGE_SCHEMA}.{table} ("
                    "    source_id      text,"
                    "    footprint_wkb  bytea,"
                    "    name           text,"
                    "    building_class text,"
                    "    height_m       double precision,"
                    "    num_floors     integer"
                    ")"
                )
            )

    async def drop_stage(self, crisis_id: uuid.UUID) -> None:
        table = _stage_table(crisis_id)
        async with self._sessionmaker() as session, session.begin():
            await session.execute(text(f"drop table if exists {_STAGE_SCHEMA}.{table}"))

    async def load_tile(
        self, crisis_id: uuid.UUID, batches: Iterable[Sequence[BuildingRow]]
    ) -> int:
        """COPY one tile into the stage, then upsert into the live table.

        Returns rows affected. Two transactions, so the live table is never
        locked while reading from S3 and a failed run loses at most one tile.
        """
        staged = await self._copy_into_stage(crisis_id, batches)
        if staged == 0:
            return 0
        return await self._upsert_stage_into_live(crisis_id)

    async def _copy_into_stage(
        self, crisis_id: uuid.UUID, batches: Iterable[Sequence[BuildingRow]]
    ) -> int:
        table = _stage_table(crisis_id)
        staged = 0
        async with self._sessionmaker() as session, session.begin():
            await session.execute(text(f"truncate {_STAGE_SCHEMA}.{table}"))
            raw = await (await session.connection()).get_raw_connection()
            # COPY has no SQLAlchemy equivalent; use the underlying asyncpg
            # connection, which shares this transaction.
            apg: Any = raw.driver_connection
            for batch in batches:
                records = [
                    (
                        r.source_id,
                        r.footprint_wkb,
                        r.name,
                        r.building_class,
                        r.height_m,
                        r.num_floors,
                    )
                    for r in batch
                ]
                if not records:
                    continue
                await apg.copy_records_to_table(
                    table,
                    schema_name=_STAGE_SCHEMA,
                    columns=list(_STAGE_COLUMNS),
                    records=records,
                )
                staged += len(records)
        return staged

    async def _upsert_stage_into_live(self, crisis_id: uuid.UUID) -> int:
        """Upsert stage into live; returns affected row count.

        `distinct on (source_id)` is needed because Postgres rejects two
        ON CONFLICT hits on the same row within one statement.
        """
        table = _stage_table(crisis_id)
        async with self._sessionmaker() as session, session.begin():
            # A failed run is simply re-run, so skipping the fsync wait is safe.
            await session.execute(text("set local maintenance_work_mem = '512MB'"))
            await session.execute(text("set local synchronous_commit = off"))
            # rowcount is on CursorResult, not the Result type in the stubs.
            result: Any = await session.execute(
                text(
                    "insert into public.buildings ("
                    "    source, source_id, footprint, name, building_class,"
                    "    height_m, num_floors, properties"
                    ") "
                    "select 'overture', source_id,"
                    "       st_multi(st_geomfromwkb(footprint_wkb, 4326))::geography,"
                    "       name, building_class, height_m, num_floors, '{}'::jsonb "
                    "from ("
                    "    select distinct on (source_id) * "
                    f"   from {_STAGE_SCHEMA}.{table} "
                    "    where source_id is not null and footprint_wkb is not null "
                    "    order by source_id"
                    ") deduped "
                    "on conflict (source, source_id) do update set "
                    "    footprint = excluded.footprint,"
                    "    name = excluded.name,"
                    "    building_class = excluded.building_class,"
                    "    height_m = excluded.height_m,"
                    "    num_floors = excluded.num_floors,"
                    "    properties = excluded.properties,"
                    "    updated_at = now()"
                )
            )
            return int(result.rowcount or 0)


__all__ = ["BuildingsBulkLoader"]
