"""DB-only unit tests for IdempotentReportWriter.

Each test runs against the local Supabase Postgres but wraps its work in an
outer transaction with `join_transaction_mode="create_savepoint"`. The session
the writer uses commits inside a SAVEPOINT; we roll the outer transaction back
at teardown so nothing leaks. Test order does not matter; a flake leaves the
DB exactly as it found it.
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
    """Insert a crisis row in the outer (about-to-rollback) transaction."""
    crisis_id = uuid.uuid4()
    await conn.execute(
        text(
            "insert into public.crises (id, name, status, created_at) "
            "values (:id, :n, 'active', :t)"
        ),
        {
            "id": str(crisis_id),
            "n": f"Test crisis {uuid.uuid4().hex[:8]}",
            "t": datetime.now(UTC) - timedelta(minutes=1),
        },
    )
    yield crisis_id


@pytest_asyncio.fixture
async def rolled_back_writer(
    engine: AsyncEngine,
) -> AsyncIterator[tuple[IdempotentReportWriter, AsyncConnection, uuid.UUID]]:
    """Yields a writer bound to a sessionmaker rooted in a connection-scoped
    transaction that is rolled back at teardown. Sessions opened by the writer
    join via SAVEPOINT, so their commits do not escape."""
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
        # Satisfies the minimum-content gate's location-or-route pair and the
        # `reports_location_or_route` CHECK.
        "location": {"lat": 25.2854, "lng": 51.5310},
    }
    base.update(overrides)
    return ReportSubmitPayload.model_validate(base)


async def test_insert_without_key_produces_a_row(
    rolled_back_writer: tuple[IdempotentReportWriter, AsyncConnection, uuid.UUID],
) -> None:
    writer, conn, crisis_id = rolled_back_writer

    result = await writer.write(
        payload=_payload(crisis_id),
        photo_path="abc/def/abcdef.jpg",
        building_id=None,
    )

    assert result.was_duplicate is False
    assert result.crisis_id == crisis_id
    count = (
        await conn.execute(
            text("select count(*) from public.reports where id = :id"),
            {"id": str(result.id)},
        )
    ).scalar_one()
    assert count == 1


async def test_insert_with_key_populates_client_submission_id(
    rolled_back_writer: tuple[IdempotentReportWriter, AsyncConnection, uuid.UUID],
) -> None:
    writer, conn, crisis_id = rolled_back_writer
    submission_id = uuid.uuid4()

    result = await writer.write(
        payload=_payload(crisis_id, client_submission_id=submission_id),
        photo_path="11/22/1122.jpg",
        building_id=None,
    )

    assert result.was_duplicate is False
    stored = (
        await conn.execute(
            text("select client_submission_id from public.reports where id = :id"),
            {"id": str(result.id)},
        )
    ).scalar_one()
    assert stored == submission_id


async def test_same_key_twice_returns_same_id_and_row_count_stays_one(
    rolled_back_writer: tuple[IdempotentReportWriter, AsyncConnection, uuid.UUID],
) -> None:
    writer, conn, crisis_id = rolled_back_writer
    submission_id = uuid.uuid4()
    payload = _payload(crisis_id, client_submission_id=submission_id)

    first = await writer.write(payload=payload, photo_path="aa/bb/aabb.jpg", building_id=None)
    second = await writer.write(payload=payload, photo_path="cc/dd/ccdd.jpg", building_id=None)

    assert first.was_duplicate is False
    assert second.was_duplicate is True
    assert second.id == first.id
    count = (
        await conn.execute(
            text("select count(*) from public.reports where client_submission_id = :sid"),
            {"sid": str(submission_id)},
        )
    ).scalar_one()
    assert count == 1


async def test_two_distinct_keys_produce_two_rows(
    rolled_back_writer: tuple[IdempotentReportWriter, AsyncConnection, uuid.UUID],
) -> None:
    writer, conn, crisis_id = rolled_back_writer
    a, b = uuid.uuid4(), uuid.uuid4()

    first = await writer.write(
        payload=_payload(crisis_id, client_submission_id=a),
        photo_path="00/11/0011.jpg",
        building_id=None,
    )
    second = await writer.write(
        payload=_payload(crisis_id, client_submission_id=b),
        photo_path="00/11/0011.jpg",
        building_id=None,
    )

    assert first.id != second.id
    assert first.was_duplicate is False
    assert second.was_duplicate is False
    count = (
        await conn.execute(
            text("select count(*) from public.reports where crisis_id = :cid"),
            {"cid": str(crisis_id)},
        )
    ).scalar_one()
    assert count == 2
