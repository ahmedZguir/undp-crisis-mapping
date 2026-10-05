"""Integration tests for `POST /admin/crises/{id}/ingest_buildings`.

The route enqueues the Arq `BuildingIngestJob` and returns `202 {job_id}`. We
substitute a fake enqueuer via `app.dependency_overrides` so the test never
needs Redis. The route also upserts a `running` row into `public.crisis_jobs`
in the same DB transaction as the enqueue — these tests pin both the
`202 {job_id}` contract and the same-transaction commit/rollback semantics.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from api.admin.job_routes import get_building_ingest_enqueuer
from api.core.arq import JobEnqueuer
from api.core.config import get_settings
from api.main import app


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
                    {"id": str(crisis_id), "n": f"Ingest test {crisis_id}"},
                )
        finally:
            await engine.dispose()

    asyncio.run(_run())
    return crisis_id


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


def _read_job_row(crisis_id: uuid.UUID) -> dict[str, Any] | None:
    settings = get_settings()

    async def _run() -> dict[str, Any] | None:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.connect() as conn:
                row = (
                    await conn.execute(
                        text(
                            "select status, error, started_at, ended_at, job_type "
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
            "job_type": row.job_type,
        }

    return asyncio.run(_run())


pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _reset_overture_cache() -> None:  # pyright: ignore[reportUnusedFunction]
    """Clear the Overture polygon resolver cache between tests so a
    `dependency_overrides` swap in one test cannot leak its fake reader
    into a later test."""
    # No-op: get_crisis_polygon_resolver is no longer @lru_cache.


class _FakeEnqueuer:
    def __init__(self) -> None:
        self.calls: list[uuid.UUID] = []
        self.next_job_id = "job-abc-123"

    async def enqueue(self, crisis_id: uuid.UUID) -> str:
        self.calls.append(crisis_id)
        return self.next_job_id


class _FailingEnqueuer:
    async def enqueue(self, crisis_id: uuid.UUID) -> str:
        raise RuntimeError("redis unreachable")


def test_post_admin_ingest_buildings_returns_202_with_job_id() -> None:
    fake = _FakeEnqueuer()
    enqueuer: JobEnqueuer = fake
    crisis_id = _seed_crisis()
    app.dependency_overrides[get_building_ingest_enqueuer] = lambda: enqueuer
    try:
        with TestClient(app) as client:
            response = client.post(f"/admin/crises/{crisis_id}/ingest_buildings")
    finally:
        app.dependency_overrides.pop(get_building_ingest_enqueuer, None)

    try:
        assert response.status_code == 202, response.text
        body: dict[str, Any] = response.json()
        assert body == {"job_id": "job-abc-123"}
        assert fake.calls == [crisis_id]
    finally:
        _delete_crisis(crisis_id)


def test_post_admin_ingest_buildings_writes_running_row() -> None:
    enqueuer: JobEnqueuer = _FakeEnqueuer()
    crisis_id = _seed_crisis()
    app.dependency_overrides[get_building_ingest_enqueuer] = lambda: enqueuer
    try:
        with TestClient(app) as client:
            response = client.post(f"/admin/crises/{crisis_id}/ingest_buildings")
            assert response.status_code == 202

            row = _read_job_row(crisis_id)
            assert row is not None
            assert row["status"] == "running"
            assert row["error"] is None
            assert row["ended_at"] is None
            assert row["job_type"] == "ingest_buildings"
    finally:
        app.dependency_overrides.pop(get_building_ingest_enqueuer, None)
        _delete_crisis(crisis_id)


def test_post_admin_ingest_buildings_rolls_back_on_enqueue_failure() -> None:
    enqueuer: JobEnqueuer = _FailingEnqueuer()
    crisis_id = _seed_crisis()
    app.dependency_overrides[get_building_ingest_enqueuer] = lambda: enqueuer
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post(f"/admin/crises/{crisis_id}/ingest_buildings")
            assert response.status_code == 500

            # Transaction rolled back — no row left behind.
            row = _read_job_row(crisis_id)
            assert row is None
    finally:
        app.dependency_overrides.pop(get_building_ingest_enqueuer, None)
        _delete_crisis(crisis_id)
