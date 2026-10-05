"""Pre-insert of enrichment sidecars by IdempotentReportWriter.

The writer must create the
`report_translations`, `image_captions`, and `report_geocodes` rows in the
*same* transaction as the report. This test pins that behaviour so the
workers can rely on the sidecars existing the instant the report row does.
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
        # Satisfies the minimum-content gate / `reports_location_or_route`
        # CHECK.
        "location": {"lat": 25.2854, "lng": 51.5310},
    }
    base.update(overrides)
    return ReportSubmitPayload.model_validate(base)


async def test_writer_pre_creates_both_sidecar_rows(
    rolled_back_writer: tuple[IdempotentReportWriter, AsyncConnection, uuid.UUID],
) -> None:
    writer, conn, crisis_id = rolled_back_writer

    result = await writer.write(
        payload=_payload(crisis_id, description="Hello"),
        photo_path="aa/bb/aabb.jpg",
        building_id=None,
    )

    translation = (
        await conn.execute(
            text(
                "select description_status, route_description_status "
                "from public.report_translations where report_id = :id"
            ),
            {"id": str(result.id)},
        )
    ).first()
    assert translation is not None
    assert translation.description_status == "pending"
    assert translation.route_description_status == "pending"

    caption = (
        await conn.execute(
            text("select status from public.image_captions where report_id = :id"),
            {"id": str(result.id)},
        )
    ).first()
    assert caption is not None
    assert caption.status == "pending"

    geocode = (
        await conn.execute(
            text("select status, source from public.report_geocodes where report_id = :id"),
            {"id": str(result.id)},
        )
    ).first()
    assert geocode is not None
    assert geocode.status == "pending"
    assert geocode.source == "osm"

    # report_quality sidecar: pre-created with the cheap
    # write-time signals filled; the worker fills the rest. `computed_at` is
    # null until score_report runs.
    quality = (
        await conn.execute(
            text(
                "select client_id, has_photo, has_description, points, "
                "confidence_score, verified, computed_at "
                "from public.report_quality where report_id = :id"
            ),
            {"id": str(result.id)},
        )
    ).first()
    assert quality is not None
    assert quality.has_photo is True
    assert quality.has_description is True
    assert quality.client_id is None  # payload had no client_id
    assert quality.points == 0
    assert quality.verified is False
    assert quality.computed_at is None


async def test_dedup_hit_does_not_double_insert_sidecars(
    rolled_back_writer: tuple[IdempotentReportWriter, AsyncConnection, uuid.UUID],
) -> None:
    """Second submit with same client_submission_id returns the existing row;
    sidecars stay 1:1 (no UNIQUE violation, no second insert)."""
    writer, conn, crisis_id = rolled_back_writer
    submission_id = uuid.uuid4()
    payload = _payload(crisis_id, client_submission_id=submission_id)

    first = await writer.write(payload=payload, photo_path="11/22/1122.jpg", building_id=None)
    second = await writer.write(payload=payload, photo_path="33/44/3344.jpg", building_id=None)
    assert second.was_duplicate is True
    assert second.id == first.id

    translations_count = (
        await conn.execute(
            text("select count(*) from public.report_translations where report_id = :id"),
            {"id": str(first.id)},
        )
    ).scalar_one()
    captions_count = (
        await conn.execute(
            text("select count(*) from public.image_captions where report_id = :id"),
            {"id": str(first.id)},
        )
    ).scalar_one()
    geocodes_count = (
        await conn.execute(
            text("select count(*) from public.report_geocodes where report_id = :id"),
            {"id": str(first.id)},
        )
    ).scalar_one()
    quality_count = (
        await conn.execute(
            text("select count(*) from public.report_quality where report_id = :id"),
            {"id": str(first.id)},
        )
    ).scalar_one()
    assert translations_count == 1
    assert captions_count == 1
    assert geocodes_count == 1
    assert quality_count == 1
