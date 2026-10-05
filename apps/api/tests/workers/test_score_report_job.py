"""Integration test for the `score_report` Arq job.

Runs against local Supabase Postgres inside an outer transaction rolled
back at teardown (same pattern as `test_enrichment_jobs.py`). The AI
classifier is left unconfigured, so `damage_agreement` resolves to NULL via
the job's best-effort catch — this test pins the DB writes (quality row +
badge), not the classifier contract.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

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
from api.workers.scoring import score_report

pytestmark = pytest.mark.integration


def _stub_clients() -> object:
    from api.ai.client import AIClients

    # No classifier configured -> classify_damage raises AIClientUnavailable,
    # the job swallows it, damage_agreement stays NULL.
    return AIClients(text=None, embedding=None)


class _NoPhotoDownloader:
    async def download_photo(self, photo_path: str) -> tuple[bytes, str]:
        _ = photo_path
        return b"\xff\xd8\xff\xd9", "image/jpeg"


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
            "n": f"Scoring test crisis {uuid.uuid4().hex[:8]}",
            "t": datetime.now(UTC) - timedelta(minutes=1),
        },
    )
    yield crisis_id


async def _seed_scored_report(
    conn: AsyncConnection,
    crisis_id: uuid.UUID,
    *,
    client_id: uuid.UUID,
) -> uuid.UUID:
    """A photo+description report with a ready caption and a pre-created
    (un-computed) report_quality row, mirroring the live write path."""
    report_id = uuid.uuid4()
    await conn.execute(
        text(
            "insert into public.reports "
            "  (id, crisis_id, damage_class, description, photo_path, location, "
            "   client_id, photo_captured_at) "
            "values (:id, :cid, 'minimal', :desc, :path, cast(:loc as geography), "
            "        :client_id, :captured)"
        ),
        {
            "id": str(report_id),
            "cid": str(crisis_id),
            "desc": "Wall collapsed on the north side.",
            "path": f"aa/bb/{uuid.uuid4().hex}.jpg",
            "loc": "SRID=4326;POINT(36.16 36.20)",
            "client_id": str(client_id),
            "captured": datetime.now(UTC) - timedelta(hours=2),
        },
    )
    await conn.execute(
        text(
            "insert into public.image_captions (report_id, caption, relevance_label, status) "
            "values (:id, 'a damaged building', 'relevant', 'ready')"
        ),
        {"id": str(report_id)},
    )
    await conn.execute(
        text(
            "insert into public.report_quality (report_id, client_id, has_photo, has_description) "
            "values (:id, :client_id, true, true)"
        ),
        {"id": str(report_id), "client_id": str(client_id)},
    )
    return report_id


def _make_ctx(sessionmaker: async_sessionmaker[Any]) -> dict[str, object]:
    return {
        "redis": object(),
        "db_sessionmaker": sessionmaker,
        "ai_clients": _stub_clients(),
        "storage_client": _NoPhotoDownloader(),
        "job_try": 1,
        "max_tries": 1,
    }


async def test_score_report_fills_quality_and_awards_first_badge(
    engine: AsyncEngine,
) -> None:
    async with engine.connect() as conn:
        outer = await conn.begin()
        try:
            sessionmaker = async_sessionmaker(
                bind=conn,
                expire_on_commit=False,
                join_transaction_mode="create_savepoint",
            )
            async with _seeded_crisis(conn) as crisis_id:
                client_id = uuid.uuid4()
                report_id = await _seed_scored_report(conn, crisis_id, client_id=client_id)

                await score_report(_make_ctx(sessionmaker), str(report_id))

                quality = (
                    await conn.execute(
                        text(
                            "select has_photo, has_description, relevance_label, points, "
                            "confidence_score, corroborators_500m, is_duplicate_image, "
                            "computed_at from public.report_quality where report_id = :id"
                        ),
                        {"id": str(report_id)},
                    )
                ).one()
                assert quality.has_photo is True
                assert quality.has_description is True
                assert quality.relevance_label == "relevant"
                # valid + photo + description + relevant = 4 (damage N/A here).
                assert quality.points == 4
                assert 0.0 < float(quality.confidence_score) <= 1.0
                assert quality.corroborators_500m == 0
                assert quality.is_duplicate_image is False
                assert quality.computed_at is not None

                badges = (
                    (
                        await conn.execute(
                            text(
                                "select badge_slug from public.citizen_badges "
                                "where client_id = :cid order by badge_slug"
                            ),
                            {"cid": str(client_id)},
                        )
                    )
                    .scalars()
                    .all()
                )
                assert "first_report" in badges
        finally:
            await outer.rollback()


async def test_score_report_flags_duplicate_image(engine: AsyncEngine) -> None:
    """A second report reusing an existing photo_path scores 0 points, is
    flagged duplicate, and earns no badge progress."""
    async with engine.connect() as conn:
        outer = await conn.begin()
        try:
            sessionmaker = async_sessionmaker(
                bind=conn,
                expire_on_commit=False,
                join_transaction_mode="create_savepoint",
            )
            async with _seeded_crisis(conn) as crisis_id:
                client_id = uuid.uuid4()
                shared_path = f"cc/dd/{uuid.uuid4().hex}.jpg"
                # First report owns the image.
                first = uuid.uuid4()
                await conn.execute(
                    text(
                        "insert into public.reports (id, crisis_id, damage_class, photo_path, "
                        "location, client_id) values "
                        "(:id, :cid, 'minimal', :path, cast(:loc as geography), :client)"
                    ),
                    {
                        "id": str(first),
                        "cid": str(crisis_id),
                        "path": shared_path,
                        "loc": "SRID=4326;POINT(36.16 36.20)",
                        "client": str(client_id),
                    },
                )
                # Second report recycles the same image.
                second = uuid.uuid4()
                await conn.execute(
                    text(
                        "insert into public.reports (id, crisis_id, damage_class, photo_path, "
                        "location, client_id) values "
                        "(:id, :cid, 'minimal', :path, cast(:loc as geography), :client)"
                    ),
                    {
                        "id": str(second),
                        "cid": str(crisis_id),
                        "path": shared_path,
                        "loc": "SRID=4326;POINT(36.16 36.20)",
                        "client": str(client_id),
                    },
                )
                await conn.execute(
                    text(
                        "insert into public.image_captions (report_id, relevance_label, status) "
                        "values (:id, 'relevant', 'ready')"
                    ),
                    {"id": str(second)},
                )
                await conn.execute(
                    text(
                        "insert into public.report_quality "
                        "(report_id, client_id, has_photo, has_description) "
                        "values (:id, :client, true, false)"
                    ),
                    {"id": str(second), "client": str(client_id)},
                )

                await score_report(_make_ctx(sessionmaker), str(second))

                quality = (
                    await conn.execute(
                        text(
                            "select points, is_duplicate_image, confidence_score "
                            "from public.report_quality where report_id = :id"
                        ),
                        {"id": str(second)},
                    )
                ).one()
                assert quality.is_duplicate_image is True
                assert quality.points == 0
                assert float(quality.confidence_score) <= 0.15

                badge_count = (
                    await conn.execute(
                        text("select count(*) from public.citizen_badges where client_id = :cid"),
                        {"cid": str(client_id)},
                    )
                ).scalar_one()
                assert badge_count == 0  # duplicate report earns no badge progress
        finally:
            await outer.rollback()
