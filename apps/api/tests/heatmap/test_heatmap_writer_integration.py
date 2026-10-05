"""DB-only integration tests for the writer's `heat_cells` upsert.

Same fixture pattern as `test_idempotent_report_writer.py`: every test runs
in an outer transaction with `join_transaction_mode="create_savepoint"` and
rolls it back at teardown, so no rows leak into the live `reports` /
`heat_cells` tables.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)

from api.core.config import get_settings
from api.core.h3 import cell_for_location
from api.reports.writer import IdempotentReportWriter
from api.schemas.reports import ReportSubmitPayload

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(get_settings().database_url)
    try:
        yield eng
    finally:
        await eng.dispose()


@asynccontextmanager
async def _seeded_crisis(conn: AsyncConnection) -> AsyncGenerator[uuid.UUID]:
    crisis_id = uuid.uuid4()
    await conn.execute(
        text(
            "insert into public.crises (id, name, status, created_at) "
            "values (:id, :n, 'active', :t)"
        ),
        {
            "id": str(crisis_id),
            "n": f"Heatmap writer test {uuid.uuid4().hex[:8]}",
            "t": datetime.now(UTC) - timedelta(minutes=1),
        },
    )
    yield crisis_id


@pytest_asyncio.fixture
async def rolled_back_writer(
    engine: AsyncEngine,
) -> AsyncIterator[tuple[IdempotentReportWriter, AsyncConnection, uuid.UUID]]:
    async with engine.connect() as conn:
        outer = await conn.begin()
        try:
            sessionmaker = async_sessionmaker(
                bind=conn,
                expire_on_commit=False,
                join_transaction_mode="create_savepoint",
            )
            writer = IdempotentReportWriter(sessionmaker=sessionmaker)
            async with _seeded_crisis(conn) as crisis_id:
                yield writer, conn, crisis_id
        finally:
            await outer.rollback()


def _payload(crisis_id: uuid.UUID, **overrides: object) -> ReportSubmitPayload:
    base: dict[str, object] = {
        "crisis_id": crisis_id,
        "damage_class": "minimal",
        "location": {"lat": 25.30, "lng": 51.50},
        # route_description satisfies reports_location_or_route even when a
        # test overrides location=None.
        "route_description": "test directions",
    }
    base.update(overrides)
    return ReportSubmitPayload.model_validate(base)


async def _heat_row(
    conn: AsyncConnection, crisis_id: uuid.UUID, h3_cell: int
) -> dict[str, int] | None:
    row = (
        await conn.execute(
            text(
                "select report_count, minimal_count, partial_count, complete_count "
                "  from public.heat_cells "
                " where crisis_id = :cid and h3_cell = :cell"
            ),
            {"cid": str(crisis_id), "cell": h3_cell},
        )
    ).first()
    if row is None:
        return None
    return {
        "report_count": int(row.report_count),
        "minimal_count": int(row.minimal_count),
        "partial_count": int(row.partial_count),
        "complete_count": int(row.complete_count),
    }


async def test_single_report_creates_one_cell(
    rolled_back_writer: tuple[IdempotentReportWriter, AsyncConnection, uuid.UUID],
) -> None:
    writer, conn, crisis_id = rolled_back_writer

    await writer.write(
        payload=_payload(crisis_id, damage_class="partial"),
        photo_path="a/b/c.jpg",
        building_id=None,
    )

    cell = cell_for_location(25.30, 51.50)
    row = await _heat_row(conn, crisis_id, cell)
    assert row is not None
    assert row["report_count"] == 1
    assert row["minimal_count"] == 0
    assert row["partial_count"] == 1
    assert row["complete_count"] == 0


async def test_two_reports_same_cell_accumulate(
    rolled_back_writer: tuple[IdempotentReportWriter, AsyncConnection, uuid.UUID],
) -> None:
    writer, conn, crisis_id = rolled_back_writer

    await writer.write(
        payload=_payload(crisis_id, damage_class="minimal"),
        photo_path="a/b/c.jpg",
        building_id=None,
    )
    await writer.write(
        payload=_payload(crisis_id, damage_class="complete"),
        photo_path="a/b/d.jpg",
        building_id=None,
    )

    cell = cell_for_location(25.30, 51.50)
    row = await _heat_row(conn, crisis_id, cell)
    assert row is not None
    assert row["report_count"] == 2
    assert row["minimal_count"] == 1
    assert row["complete_count"] == 1


async def test_report_without_location_skips_heat_cells(
    rolled_back_writer: tuple[IdempotentReportWriter, AsyncConnection, uuid.UUID],
) -> None:
    writer, conn, crisis_id = rolled_back_writer

    await writer.write(
        payload=_payload(crisis_id, location=None),
        photo_path="a/b/c.jpg",
        building_id=None,
    )

    count = (
        await conn.execute(
            text("select count(*) from public.heat_cells where crisis_id = :cid"),
            {"cid": str(crisis_id)},
        )
    ).scalar_one()
    assert count == 0

    reports = (
        await conn.execute(
            text("select count(*) from public.reports where crisis_id = :cid"),
            {"cid": str(crisis_id)},
        )
    ).scalar_one()
    assert reports == 1


async def test_idempotent_retry_does_not_double_count(
    rolled_back_writer: tuple[IdempotentReportWriter, AsyncConnection, uuid.UUID],
) -> None:
    writer, conn, crisis_id = rolled_back_writer
    submission_id = uuid.uuid4()
    payload = _payload(crisis_id, client_submission_id=submission_id)

    first = await writer.write(payload=payload, photo_path="aa.jpg", building_id=None)
    second = await writer.write(payload=payload, photo_path="bb.jpg", building_id=None)

    assert first.was_duplicate is False
    assert second.was_duplicate is True
    assert second.id == first.id

    cell = cell_for_location(25.30, 51.50)
    row = await _heat_row(conn, crisis_id, cell)
    assert row is not None
    # Retry path must not bump the cell — the first write already counted.
    assert row["report_count"] == 1
