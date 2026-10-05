"""Verifies `ReportSubmissionService.submit` calls the enrichment enqueuer
on new writes and skips it on dedup hits.

The service is a thin composition; we drive it with stubs over storage,
crisis lookup, and the enqueuer Protocol so the test exercises only the
control flow we care about — no Supabase, no Redis.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
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

from api.core.arq import JobEnqueuer
from api.core.config import get_settings
from api.crises.lookup import SqlAlchemyCrisisLookup
from api.crises.service import CrisisService
from api.reports.service import ReportSubmissionService
from api.schemas.reports import ReportSubmitPayload

pytestmark = pytest.mark.integration


@dataclass
class _RecordingEnqueuer(JobEnqueuer):
    """Test stub — records every report_id passed to it."""

    seen: list[uuid.UUID] = field(default_factory=list[uuid.UUID])

    async def enqueue(self, entity_id: uuid.UUID) -> str:
        self.seen.append(entity_id)
        return ""


class _StubStorage:
    async def upload_photo(self, content: bytes, content_type: str) -> str:
        _ = content, content_type
        return "stubbed/path.jpg"


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
            "n": f"Service test crisis {uuid.uuid4().hex[:8]}",
            "t": datetime.now(UTC) - timedelta(minutes=1),
        },
    )
    yield crisis_id


@pytest_asyncio.fixture
async def service_harness(
    engine: AsyncEngine,
) -> AsyncIterator[tuple[ReportSubmissionService, _RecordingEnqueuer, uuid.UUID, AsyncConnection]]:
    async with engine.connect() as conn:
        outer = await conn.begin()
        try:
            sessionmaker = async_sessionmaker(
                bind=conn,
                expire_on_commit=False,
                join_transaction_mode="create_savepoint",
            )
            async with _seeded_crisis(conn) as crisis_id:
                crisis_service = CrisisService(
                    lookup=SqlAlchemyCrisisLookup(sessionmaker),
                    reserved_name="Other / Unspecified",
                )
                enqueuer = _RecordingEnqueuer()
                service = ReportSubmissionService(
                    storage=_StubStorage(),
                    crisis_service=crisis_service,
                    sessionmaker=sessionmaker,
                    enrichment_enqueuer=enqueuer,
                )
                yield service, enqueuer, crisis_id, conn
        finally:
            await outer.rollback()


def _tiny_jpeg() -> bytes:
    # Smallest legal JPEG: SOI + EOI. Photo validator only checks size/mime.
    return b"\xff\xd8\xff\xd9"


async def test_submission_enqueues_enrich_report_for_new_row(
    service_harness: tuple[ReportSubmissionService, _RecordingEnqueuer, uuid.UUID, AsyncConnection],
) -> None:
    service, enqueuer, crisis_id, _conn = service_harness
    payload = ReportSubmitPayload.model_validate(
        {
            "crisis_id": crisis_id,
            "damage_class": "minimal",
            "location": {"lat": 25.2854, "lng": 51.5310},
        }
    )

    response = await service.submit(_tiny_jpeg(), "image/jpeg", payload)

    assert enqueuer.seen == [response.id]


async def test_submission_dedup_does_not_re_enqueue(
    service_harness: tuple[ReportSubmissionService, _RecordingEnqueuer, uuid.UUID, AsyncConnection],
) -> None:
    """A second submit with the same `client_submission_id` is a dedup hit
    — the pipeline is already in flight or finalised, so no re-enqueue."""
    service, enqueuer, crisis_id, _conn = service_harness
    submission_id = uuid.uuid4()
    payload = ReportSubmitPayload.model_validate(
        {
            "crisis_id": crisis_id,
            "damage_class": "minimal",
            "client_submission_id": submission_id,
            "location": {"lat": 25.2854, "lng": 51.5310},
        }
    )

    first = await service.submit(_tiny_jpeg(), "image/jpeg", payload)
    second = await service.submit(_tiny_jpeg(), "image/jpeg", payload)

    assert first.id == second.id
    assert enqueuer.seen == [first.id]  # only the first submit enqueued.
