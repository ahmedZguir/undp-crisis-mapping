"""Integration tests for the heat_cells backfill script.

Seeds reports directly (bypassing the writer's upsert), runs
`backfill_crisis`, and asserts `heat_cells` reflects the seeded reports.
Scoped cleanup only — never touches rows for other crises.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.core.config import get_settings
from api.core.h3 import cell_for_location
from api.scripts.backfill_heat_cells import backfill_crisis

pytestmark = pytest.mark.integration


async def _seed_crisis(engine_url: str) -> uuid.UUID:
    crisis_id = uuid.uuid4()
    engine = create_async_engine(engine_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "insert into public.crises (id, name, status, created_at) "
                    "values (:id, :n, 'active', :t)"
                ),
                {
                    "id": str(crisis_id),
                    "n": f"Backfill test {uuid.uuid4().hex[:8]}",
                    "t": datetime.now(UTC) - timedelta(minutes=1),
                },
            )
    finally:
        await engine.dispose()
    return crisis_id


async def _seed_report(
    engine_url: str,
    *,
    crisis_id: uuid.UUID,
    damage_class: str = "minimal",
    location: tuple[float, float] | None,
    created_at: datetime | None = None,
) -> None:
    location_sql = (
        "st_setsrid(st_makepoint(:lng, :lat), 4326)::geography" if location is not None else "null"
    )
    created_at_sql = "cast(:created_at as timestamptz)" if created_at is not None else "now()"
    params: dict[str, object] = {
        "crisis_id": str(crisis_id),
        "dc": damage_class,
        "photo": f"test/{uuid.uuid4().hex}.jpg",
    }
    if location is not None:
        params["lat"] = location[0]
        params["lng"] = location[1]
    if created_at is not None:
        params["created_at"] = created_at
    engine = create_async_engine(engine_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "insert into public.reports (crisis_id, damage_class, photo_path, "
                    "location, created_at, route_description) values (:crisis_id, :dc, :photo, "
                    # route_description satisfies reports_location_or_route
                    f"{location_sql}, {created_at_sql}, 'test directions')"
                ),
                params,
            )
    finally:
        await engine.dispose()


async def _cleanup(engine_url: str, *, crisis_ids: list[uuid.UUID]) -> None:
    engine = create_async_engine(engine_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("delete from public.heat_cells where crisis_id = any(:ids)"),
                {"ids": [str(i) for i in crisis_ids]},
            )
            await conn.execute(
                text("delete from public.reports where crisis_id = any(:ids)"),
                {"ids": [str(i) for i in crisis_ids]},
            )
            await conn.execute(
                text("delete from public.crises where id = any(:ids)"),
                {"ids": [str(i) for i in crisis_ids]},
            )
    finally:
        await engine.dispose()


async def _read_heat_cells(engine_url: str, crisis_id: uuid.UUID) -> list[dict[str, object]]:
    engine = create_async_engine(engine_url)
    try:
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "select h3_cell, report_count, minimal_count, partial_count, "
                        "       complete_count "
                        "  from public.heat_cells where crisis_id = :cid "
                        " order by h3_cell"
                    ),
                    {"cid": str(crisis_id)},
                )
            ).all()
    finally:
        await engine.dispose()
    return [
        {
            "h3_cell": int(r.h3_cell),
            "report_count": int(r.report_count),
            "minimal_count": int(r.minimal_count),
            "partial_count": int(r.partial_count),
            "complete_count": int(r.complete_count),
        }
        for r in rows
    ]


async def _run_backfill(crisis_id: uuid.UUID) -> int:
    engine = create_async_engine(get_settings().database_url)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessionmaker() as session:
            return await backfill_crisis(session, crisis_id)
    finally:
        await engine.dispose()


# --- Tests ---------------------------------------------------------------


def test_backfill_populates_from_reports() -> None:
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis(settings.database_url))
    asyncio.run(
        _seed_report(
            settings.database_url,
            crisis_id=crisis_id,
            damage_class="partial",
            location=(25.30, 51.500),
        )
    )
    asyncio.run(
        _seed_report(
            settings.database_url,
            crisis_id=crisis_id,
            damage_class="complete",
            location=(25.30, 51.500),
        )
    )
    asyncio.run(
        _seed_report(
            settings.database_url,
            crisis_id=crisis_id,
            damage_class="minimal",
            location=None,  # Skipped — no location.
        )
    )

    try:
        cells_inserted = asyncio.run(_run_backfill(crisis_id))
        assert cells_inserted == 1
        rows = asyncio.run(_read_heat_cells(settings.database_url, crisis_id))
        expected_cell = cell_for_location(25.30, 51.500)
        assert rows == [
            {
                "h3_cell": expected_cell,
                "report_count": 2,
                "minimal_count": 0,
                "partial_count": 1,
                "complete_count": 1,
            }
        ]
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


def test_backfill_is_idempotent() -> None:
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis(settings.database_url))
    asyncio.run(
        _seed_report(
            settings.database_url,
            crisis_id=crisis_id,
            damage_class="partial",
            location=(25.30, 51.500),
        )
    )

    try:
        asyncio.run(_run_backfill(crisis_id))
        first = asyncio.run(_read_heat_cells(settings.database_url, crisis_id))
        asyncio.run(_run_backfill(crisis_id))
        second = asyncio.run(_read_heat_cells(settings.database_url, crisis_id))
        assert first == second
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


def test_backfill_does_not_touch_other_crises() -> None:
    settings = get_settings()
    crisis_a = asyncio.run(_seed_crisis(settings.database_url))
    crisis_b = asyncio.run(_seed_crisis(settings.database_url))
    asyncio.run(
        _seed_report(
            settings.database_url,
            crisis_id=crisis_a,
            damage_class="partial",
            location=(25.30, 51.500),
        )
    )
    asyncio.run(
        _seed_report(
            settings.database_url,
            crisis_id=crisis_b,
            damage_class="complete",
            location=(25.40, 51.600),
        )
    )

    try:
        asyncio.run(_run_backfill(crisis_a))
        rows_a = asyncio.run(_read_heat_cells(settings.database_url, crisis_a))
        rows_b = asyncio.run(_read_heat_cells(settings.database_url, crisis_b))
        assert len(rows_a) == 1
        # crisis_b never ran — no rows.
        assert rows_b == []
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_a, crisis_b]))
