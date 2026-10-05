"""Integration test for `verify_report` (the DB helper behind
`PATCH /admin/reports/{id}/verify`).

Exercises the real DB behaviour — verify sets the columns + recomputes
confidence with the verified floor + awards the badge; un-verify revokes
the badge only when the client has no other verified report. Runs inside an
outer transaction rolled back at teardown. The HTTP/auth layer is covered
by the admin auth-gate tests; this pins the data effect.
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

from api.admin.report_queries import verify_report
from api.core.config import get_settings
from api.reports.quality import VERIFIED_FLOOR

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
            "n": f"Verify test crisis {uuid.uuid4().hex[:8]}",
            "t": datetime.now(UTC) - timedelta(minutes=1),
        },
    )
    yield crisis_id


async def _seed_report(
    conn: AsyncConnection, crisis_id: uuid.UUID, *, client_id: uuid.UUID
) -> uuid.UUID:
    report_id = uuid.uuid4()
    await conn.execute(
        text(
            "insert into public.reports (id, crisis_id, damage_class, photo_path, "
            "location, client_id) values "
            "(:id, :cid, 'minimal', :path, cast(:loc as geography), :client)"
        ),
        {
            "id": str(report_id),
            "cid": str(crisis_id),
            "path": f"ee/ff/{uuid.uuid4().hex}.jpg",
            "loc": "SRID=4326;POINT(36.16 36.20)",
            "client": str(client_id),
        },
    )
    await conn.execute(
        text(
            "insert into public.report_quality (report_id, client_id, has_photo, has_description) "
            "values (:id, :client, true, false)"
        ),
        {"id": str(report_id), "client": str(client_id)},
    )
    return report_id


async def test_verify_sets_columns_recomputes_and_awards_badge(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        outer = await conn.begin()
        try:
            sessionmaker = async_sessionmaker(
                bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
            )
            async with _seeded_crisis(conn) as crisis_id:
                client_id = uuid.uuid4()
                coordinator_id = uuid.uuid4()
                report_id = await _seed_report(conn, crisis_id, client_id=client_id)

                async with sessionmaker() as session:
                    result = await verify_report(
                        session,
                        report_id=report_id,
                        verified=True,
                        coordinator_id=coordinator_id,
                    )
                assert result is not None
                score, verified, reputation = result
                assert verified is True
                assert score >= VERIFIED_FLOOR
                assert reputation is not None
                assert reputation.total_reports == 1
                assert reputation.verified_count == 1
                assert "verified_by_coordinator" in reputation.badge_slugs

                row = (
                    await conn.execute(
                        text(
                            "select verified, verified_by, verified_at, confidence_score "
                            "from public.report_quality where report_id = :id"
                        ),
                        {"id": str(report_id)},
                    )
                ).one()
                assert row.verified is True
                assert row.verified_by == coordinator_id
                assert row.verified_at is not None
                assert float(row.confidence_score) >= VERIFIED_FLOOR

                badge = (
                    await conn.execute(
                        text(
                            "select 1 from public.citizen_badges "
                            "where client_id = :cid and badge_slug = 'verified_by_coordinator'"
                        ),
                        {"cid": str(client_id)},
                    )
                ).first()
                assert badge is not None
        finally:
            await outer.rollback()


async def test_unverify_revokes_badge_when_no_other_verified(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        outer = await conn.begin()
        try:
            sessionmaker = async_sessionmaker(
                bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
            )
            async with _seeded_crisis(conn) as crisis_id:
                client_id = uuid.uuid4()
                coordinator_id = uuid.uuid4()
                report_id = await _seed_report(conn, crisis_id, client_id=client_id)

                async with sessionmaker() as session:
                    await verify_report(
                        session, report_id=report_id, verified=True, coordinator_id=coordinator_id
                    )
                async with sessionmaker() as session:
                    result = await verify_report(
                        session, report_id=report_id, verified=False, coordinator_id=coordinator_id
                    )
                assert result is not None
                _, verified, _reputation = result
                assert verified is False

                row = (
                    await conn.execute(
                        text(
                            "select verified, verified_by, verified_at "
                            "from public.report_quality where report_id = :id"
                        ),
                        {"id": str(report_id)},
                    )
                ).one()
                assert row.verified is False
                assert row.verified_by is None
                assert row.verified_at is None

                badge = (
                    await conn.execute(
                        text(
                            "select 1 from public.citizen_badges "
                            "where client_id = :cid and badge_slug = 'verified_by_coordinator'"
                        ),
                        {"cid": str(client_id)},
                    )
                ).first()
                assert badge is None  # revoked: client has no remaining verified report
        finally:
            await outer.rollback()


async def test_verify_unknown_report_returns_none(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        outer = await conn.begin()
        try:
            sessionmaker = async_sessionmaker(
                bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
            )
            async with sessionmaker() as session:
                result = await verify_report(
                    session,
                    report_id=uuid.uuid4(),
                    verified=True,
                    coordinator_id=uuid.uuid4(),
                )
            assert result is None
        finally:
            await outer.rollback()
