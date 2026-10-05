"""Load Overture's divisions theme into public.overture_divisions and
public.overture_division_areas. Each table is truncated and reloaded in its own
transaction.

    uv run python -m api.scripts.ingest_overture_divisions [--release 2026-04-15.0] [--yes]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import asyncpg  # pyright: ignore[reportMissingTypeStubs]
import duckdb

from api.buildings.overture_release_locator import OvertureReleaseLocator
from api.core.config import get_settings

logger = logging.getLogger("ingest_overture_divisions")

_BATCH_ROWS = 25_000

# The bucket has no "latest" alias; release must be a dated release.
# division holds names, hierarchies and a point; division_area holds the polygon.
OVERTURE_DIVISION_URL_TEMPLATE = (
    "s3://overturemaps-us-west-2/release/{release}/theme=divisions/type=division/*"
)
OVERTURE_DIVISION_AREA_URL_TEMPLATE = (
    "s3://overturemaps-us-west-2/release/{release}/theme=divisions/type=division_area/*"
)


@dataclass(frozen=True)
class _IngestArgs:
    release: str
    division_parquet_url: str
    division_area_parquet_url: str
    dsn: str
    yes: bool


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    args = _parse_args()
    if not args.yes and not _confirm_truncate():
        logger.error("aborted: rerun with --yes to truncate and reload the divisions tables")
        return 1
    asyncio.run(_run(args))
    return 0


def _confirm_truncate() -> bool:
    prompt = (
        "This truncates public.overture_divisions (cascade) and "
        "public.overture_division_areas. Continue? [y/N] "
    )
    try:
        return input(prompt).strip().lower() in {"y", "yes"}
    except EOFError:
        return False


def _parse_args() -> _IngestArgs:
    parser = argparse.ArgumentParser(
        description="Materialise Overture divisions data into Postgres."
    )
    parser.add_argument(
        "--release",
        default=None,
        help=(
            "Overture release pin (e.g. 2026-04-15.0). Defaults to "
            "OvertureReleaseLocator().latest()."
        ),
    )
    parser.add_argument(
        "--division-parquet-url",
        default=None,
        help="Override the S3 URL for the `division` parquet (for tests).",
    )
    parser.add_argument(
        "--division-area-parquet-url",
        default=None,
        help="Override the S3 URL for the `division_area` parquet (for tests).",
    )
    parser.add_argument(
        "--dsn",
        default=None,
        help=(
            "Postgres DSN. Defaults to Settings.database_url with the +asyncpg driver tag stripped."
        ),
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the confirmation prompt before truncating the divisions tables.",
    )
    ns = parser.parse_args()

    release = ns.release or OvertureReleaseLocator().latest()
    division_url = ns.division_parquet_url or OVERTURE_DIVISION_URL_TEMPLATE.format(release=release)
    division_area_url = ns.division_area_parquet_url or OVERTURE_DIVISION_AREA_URL_TEMPLATE.format(
        release=release
    )
    dsn = ns.dsn or _dsn_from_settings()
    return _IngestArgs(
        release=release,
        division_parquet_url=division_url,
        division_area_parquet_url=division_area_url,
        dsn=dsn,
        yes=bool(ns.yes),
    )


def _dsn_from_settings() -> str:
    raw = get_settings().database_url
    # asyncpg rejects the SQLAlchemy +asyncpg driver tag.
    return raw.replace("postgresql+asyncpg://", "postgresql://", 1)


async def _run(args: _IngestArgs) -> None:
    logger.info(
        "starting ingest release=%s division=%s division_area=%s",
        args.release,
        args.division_parquet_url,
        args.division_area_parquet_url,
    )
    started = time.monotonic()
    duck_con = _open_duckdb()
    try:
        pg_con: Any = await asyncpg.connect(args.dsn)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        try:
            divisions_count = await _ingest_divisions(
                duck_con, pg_con, args.division_parquet_url, args.release
            )
            areas_count = await _ingest_division_areas(
                duck_con, pg_con, args.division_area_parquet_url, args.release
            )
        finally:
            await pg_con.close()  # pyright: ignore[reportUnknownMemberType]
    finally:
        duck_con.close()
    logger.info(
        "ingest done release=%s divisions=%d division_areas=%d elapsed_s=%.1f",
        args.release,
        divisions_count,
        areas_count,
        time.monotonic() - started,
    )


def _open_duckdb() -> duckdb.DuckDBPyConnection:
    """In-memory DuckDB with spatial and httpfs; long HTTP timeout for large parquets."""
    con = duckdb.connect(":memory:")
    con.execute("install spatial")
    con.execute("load spatial")
    try:
        con.execute("install httpfs")
        con.execute("load httpfs")
        con.execute("set http_timeout = 300000")
        con.execute("set http_retries = 5")
        con.execute("set http_retry_wait_ms = 2000")
    except duckdb.Error:  # local fixtures don't need httpfs
        pass
    return con


async def _ingest_divisions(
    duck_con: duckdb.DuckDBPyConnection,
    pg_con: Any,
    parquet_url: str,
    release: str,
) -> int:
    """Load public.overture_divisions.

    names_search_text is the primary name plus every common name, so one
    trigram index covers cross-language typeahead. hierarchy_parents is the
    first ancestry path's names.
    """
    started = time.monotonic()
    logger.info("reading divisions parquet url=%s", parquet_url)
    cursor = duck_con.execute(
        f"""
        select
            id,
            subtype,
            country,
            names.primary as names_primary,
            to_json(names.common) as names_common_json,
            population,
            bbox.xmin as bbox_xmin,
            bbox.ymin as bbox_ymin,
            bbox.xmax as bbox_xmax,
            bbox.ymax as bbox_ymax,
            coalesce(
                list_transform(hierarchies[1], h -> h.name),
                cast([] as varchar[])
            ) as hierarchy_parents,
            case when geometry is null then null
                 else st_aswkb(geometry::geometry) end as point_wkb
        from read_parquet('{parquet_url}')
        """
    )

    async with pg_con.transaction():
        await pg_con.execute("truncate public.overture_divisions cascade")
        # asyncpg cannot encode geography, so COPY WKB into staging and convert.
        await pg_con.execute(
            """
            create temp table _staging_overture_divisions (
                id text,
                subtype text,
                country text,
                names_primary text,
                names_common jsonb,
                names_search_text text,
                population bigint,
                bbox_xmin double precision,
                bbox_ymin double precision,
                bbox_xmax double precision,
                bbox_ymax double precision,
                hierarchy_parents text[],
                point_wkb bytea,
                release text
            ) on commit drop
            """
        )

        total = 0

        async def _records() -> AsyncIterator[tuple[object, ...]]:
            nonlocal total
            while True:
                batch = cursor.fetchmany(_BATCH_ROWS)
                if not batch:
                    return
                for row in batch:
                    common_json = row[4]
                    parsed: Any = json.loads(common_json) if common_json else {}
                    if isinstance(parsed, dict):
                        common_dict: dict[str, Any] = parsed  # pyright: ignore[reportUnknownVariableType]
                    else:
                        common_dict = {}
                    # Overture occasionally has non-string values in the map.
                    search_text_parts: list[str] = [str(row[3])]
                    for value in common_dict.values():
                        if value:
                            search_text_parts.append(str(value))
                    names_search_text = " ".join(search_text_parts)
                    point_wkb = row[11]
                    yield (
                        row[0],  # id
                        row[1],  # subtype
                        row[2],  # country (may be None)
                        row[3],  # names_primary
                        json.dumps(common_dict),  # names_common
                        names_search_text,
                        row[5],  # population (may be None)
                        row[6],
                        row[7],
                        row[8],
                        row[9],
                        list(row[10]) if row[10] is not None else [],
                        bytes(point_wkb) if point_wkb is not None else None,
                        release,
                    )
                    total += 1

        # copy_records_to_table does not take an async iterator; buffer per batch.
        batch_buffer: list[tuple[object, ...]] = []
        async for record in _records():
            batch_buffer.append(record)
            if len(batch_buffer) >= _BATCH_ROWS:
                await pg_con.copy_records_to_table(
                    "_staging_overture_divisions",
                    records=batch_buffer,
                    columns=[
                        "id",
                        "subtype",
                        "country",
                        "names_primary",
                        "names_common",
                        "names_search_text",
                        "population",
                        "bbox_xmin",
                        "bbox_ymin",
                        "bbox_xmax",
                        "bbox_ymax",
                        "hierarchy_parents",
                        "point_wkb",
                        "release",
                    ],
                )
                batch_buffer.clear()
        if batch_buffer:
            await pg_con.copy_records_to_table(
                "_staging_overture_divisions",
                records=batch_buffer,
                columns=[
                    "id",
                    "subtype",
                    "country",
                    "names_primary",
                    "names_common",
                    "names_search_text",
                    "population",
                    "bbox_xmin",
                    "bbox_ymin",
                    "bbox_xmax",
                    "bbox_ymax",
                    "hierarchy_parents",
                    "point_wkb",
                    "release",
                ],
            )

        await pg_con.execute(
            """
            insert into public.overture_divisions (
                id, subtype, country, names_primary, names_common,
                names_search_text, population, bbox_xmin, bbox_ymin,
                bbox_xmax, bbox_ymax, hierarchy_parents,
                point_geometry, release
            )
            select id, subtype, country, names_primary, names_common,
                   names_search_text, population, bbox_xmin, bbox_ymin,
                   bbox_xmax, bbox_ymax, hierarchy_parents,
                   case when point_wkb is null then null
                        else st_geomfromwkb(point_wkb, 4326)::geography end,
                   release
            from _staging_overture_divisions
            """
        )

    elapsed = time.monotonic() - started
    logger.info(
        "ingest_divisions done rows=%d elapsed_s=%.1f release=%s",
        total,
        elapsed,
        release,
    )
    return total


async def _ingest_division_areas(
    duck_con: duckdb.DuckDBPyConnection,
    pg_con: Any,
    parquet_url: str,
    release: str,
) -> int:
    """Load public.overture_division_areas."""
    started = time.monotonic()
    logger.info("reading division_areas parquet url=%s", parquet_url)
    cursor = duck_con.execute(
        f"""
        select
            id,
            division_id,
            subtype,
            country,
            st_aswkb(geometry) as geometry_wkb
        from read_parquet('{parquet_url}')
        """
    )

    async with pg_con.transaction():
        await pg_con.execute("truncate public.overture_division_areas")
        await pg_con.execute(
            """
            create temp table _staging_overture_division_areas (
                id text,
                division_id text,
                subtype text,
                country text,
                geometry_wkb bytea,
                release text
            ) on commit drop
            """
        )

        total = 0
        batch_buffer: list[tuple[object, ...]] = []
        while True:
            batch = cursor.fetchmany(_BATCH_ROWS)
            if not batch:
                break
            for row in batch:
                geom_wkb = row[4]
                if geom_wkb is None:
                    # geometry is NOT NULL; skip malformed rows.
                    continue
                batch_buffer.append(
                    (
                        row[0],
                        row[1],
                        row[2],
                        row[3],
                        bytes(geom_wkb),
                        release,
                    )
                )
                total += 1
            if len(batch_buffer) >= _BATCH_ROWS:
                await pg_con.copy_records_to_table(
                    "_staging_overture_division_areas",
                    records=batch_buffer,
                    columns=[
                        "id",
                        "division_id",
                        "subtype",
                        "country",
                        "geometry_wkb",
                        "release",
                    ],
                )
                batch_buffer.clear()
        if batch_buffer:
            await pg_con.copy_records_to_table(
                "_staging_overture_division_areas",
                records=batch_buffer,
                columns=[
                    "id",
                    "division_id",
                    "subtype",
                    "country",
                    "geometry_wkb",
                    "release",
                ],
            )

        # Some areas reference divisions that were not loaded; the FK would
        # reject them, so drop them.
        await pg_con.execute(
            """
            insert into public.overture_division_areas (
                id, division_id, subtype, country, geometry, release
            )
            select s.id, s.division_id, s.subtype, s.country,
                   st_multi(st_geomfromwkb(s.geometry_wkb, 4326))::geography,
                   s.release
            from _staging_overture_division_areas s
            where exists (
                select 1 from public.overture_divisions d where d.id = s.division_id
            )
            """
        )

    elapsed = time.monotonic() - started
    logger.info(
        "ingest_division_areas done rows=%d elapsed_s=%.1f release=%s",
        total,
        elapsed,
        release,
    )
    return total


if __name__ == "__main__":
    sys.exit(main())
