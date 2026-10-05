"""Running-gate tests for `POST /admin/crises/{id}/ingest_buildings`.

The route is supposed to refuse a re-enqueue when a `running` row for the
same crisis already exists, so a coordinator who double-clicks the button
(or two coordinators racing each other) cannot clobber the in-flight row's
`started_at` and end up with two workers stamping the crisis from scratch.
A `?force=true` query parameter overrides the gate so ops can recover from
a worker that died without writing a terminal `crisis_jobs` row.

These tests pin both halves: the 409 path (and that the enqueuer is *not*
called) and the force path (and that `started_at` advances when the gate
is bypassed).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from api.admin.job_routes import get_building_ingest_enqueuer
from api.core.arq import JobEnqueuer
from api.core.config import get_settings
from api.main import app

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _reset_overture_cache() -> None:  # pyright: ignore[reportUnusedFunction]
    """Clear the Overture polygon resolver cache between tests so a
    `dependency_overrides` swap in one test cannot leak its fake reader
    into a later test."""
    # No-op: get_crisis_polygon_resolver is no longer @lru_cache.


def _seed_crisis() -> uuid.UUID:
    settings = get_settings()
    crisis_id = uuid.uuid4()

    async def _run() -> None:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "insert into public.crises (id, name, status, type) "
                        "values (:id, :n, 'inactive', 'flood')"
                    ),
                    {"id": str(crisis_id), "n": f"Gate test {crisis_id}"},
                )
        finally:
            await engine.dispose()

    asyncio.run(_run())
    return crisis_id


def _seed_running_job(crisis_id: uuid.UUID, started_at: datetime) -> None:
    settings = get_settings()

    async def _run() -> None:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "insert into public.crisis_jobs "
                        "  (crisis_id, job_type, status, error, started_at, ended_at) "
                        "values (:id, 'ingest_buildings', 'running', null, :t, null)"
                    ),
                    {"id": str(crisis_id), "t": started_at},
                )
        finally:
            await engine.dispose()

    asyncio.run(_run())


def _read_job_row(crisis_id: uuid.UUID) -> dict[str, Any] | None:
    settings = get_settings()

    async def _run() -> dict[str, Any] | None:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.connect() as conn:
                row = (
                    await conn.execute(
                        text(
                            "select status, error, started_at, ended_at "
                            "from public.crisis_jobs "
                            "where crisis_id = :id and job_type = 'ingest_buildings'"
                        ),
                        {"id": str(crisis_id)},
                    )
                ).first()
        finally:
            await engine.dispose()
        if row is None:
            return None
        return {
            "status": row.status,
            "error": row.error,
            "started_at": row.started_at,
            "ended_at": row.ended_at,
        }

    return asyncio.run(_run())


def _delete_crisis(crisis_id: uuid.UUID) -> None:
    settings = get_settings()

    async def _run() -> None:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text("delete from public.crises where id = :id"),
                    {"id": str(crisis_id)},
                )
        finally:
            await engine.dispose()

    asyncio.run(_run())


class _FakeEnqueuer:
    def __init__(self) -> None:
        self.calls: list[uuid.UUID] = []
        self.next_job_id = "job-gated-xyz"

    async def enqueue(self, crisis_id: uuid.UUID) -> str:
        self.calls.append(crisis_id)
        return self.next_job_id


def test_post_ingest_buildings_returns_409_when_already_running() -> None:
    """A re-enqueue while a `running` row exists must 409 and not enqueue."""
    fake = _FakeEnqueuer()
    enqueuer: JobEnqueuer = fake
    crisis_id = _seed_crisis()
    started_at = datetime.now(tz=UTC) - timedelta(minutes=2)
    _seed_running_job(crisis_id, started_at)
    app.dependency_overrides[get_building_ingest_enqueuer] = lambda: enqueuer
    try:
        with TestClient(app) as client:
            response = client.post(f"/admin/crises/{crisis_id}/ingest_buildings")

            assert response.status_code == 409, response.text
            body: dict[str, Any] = response.json()
            assert body["detail"] == "ingest already running"
            assert "started_at" in body
            # `started_at` round-trips as ISO-8601; just confirm it parses and is
            # close to what we seeded.
            echoed = datetime.fromisoformat(body["started_at"])
            assert abs((echoed - started_at).total_seconds()) < 1.0
            # Enqueuer must NOT have been called.
            assert fake.calls == []

            # Running row is unchanged — `started_at` did not advance.
            row = _read_job_row(crisis_id)
            assert row is not None
            assert row["status"] == "running"
            assert abs((row["started_at"] - started_at).total_seconds()) < 1.0
    finally:
        app.dependency_overrides.pop(get_building_ingest_enqueuer, None)
        _delete_crisis(crisis_id)


def test_post_ingest_buildings_force_true_overrides_running_gate() -> None:
    """`?force=true` enqueues even over a `running` row and bumps `started_at`."""
    fake = _FakeEnqueuer()
    enqueuer: JobEnqueuer = fake
    crisis_id = _seed_crisis()
    stale_started_at = datetime.now(tz=UTC) - timedelta(hours=2)
    _seed_running_job(crisis_id, stale_started_at)
    app.dependency_overrides[get_building_ingest_enqueuer] = lambda: enqueuer
    try:
        with TestClient(app) as client:
            response = client.post(f"/admin/crises/{crisis_id}/ingest_buildings?force=true")

            assert response.status_code == 202, response.text
            body: dict[str, Any] = response.json()
            assert body == {"job_id": "job-gated-xyz"}
            assert fake.calls == [crisis_id]

            # Running row's `started_at` advanced (the upsert path ran).
            row = _read_job_row(crisis_id)
            assert row is not None
            assert row["status"] == "running"
            assert row["started_at"] > stale_started_at
    finally:
        app.dependency_overrides.pop(get_building_ingest_enqueuer, None)
        _delete_crisis(crisis_id)


def test_post_ingest_buildings_first_enqueue_unaffected_by_gate() -> None:
    """No prior `crisis_jobs` row: gate is a no-op; 202 + running row created."""
    fake = _FakeEnqueuer()
    enqueuer: JobEnqueuer = fake
    crisis_id = _seed_crisis()
    app.dependency_overrides[get_building_ingest_enqueuer] = lambda: enqueuer
    try:
        with TestClient(app) as client:
            response = client.post(f"/admin/crises/{crisis_id}/ingest_buildings")

            assert response.status_code == 202, response.text
            assert response.json() == {"job_id": "job-gated-xyz"}
            assert fake.calls == [crisis_id]

            row = _read_job_row(crisis_id)
            assert row is not None
            assert row["status"] == "running"
            assert row["ended_at"] is None
    finally:
        app.dependency_overrides.pop(get_building_ingest_enqueuer, None)
        _delete_crisis(crisis_id)
