"""Integration tests for GET /admin/crises/{id}/jobs.

Reads the `public.crisis_jobs` rows for one crisis. Seeds rows directly via
SQL — the worker-side write path is exercised in `test_buildings_ingest_integration.py`.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from api.core.config import get_settings
from api.main import app

pytestmark = pytest.mark.integration


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
                    {"id": str(crisis_id), "n": f"Jobs test {crisis_id}"},
                )
        finally:
            await engine.dispose()

    asyncio.run(_run())
    return crisis_id


def _seed_job(
    crisis_id: uuid.UUID,
    job_type: str,
    status: str,
    error: str | None = None,
) -> None:
    settings = get_settings()

    async def _run() -> None:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "insert into public.crisis_jobs "
                        "  (crisis_id, job_type, status, error, started_at, ended_at) "
                        "values (:id, :t, :s, :e, now(), "
                        "  case when :s in ('succeeded', 'failed') "
                        "       then now() else null end)"
                    ),
                    {"id": str(crisis_id), "t": job_type, "s": status, "e": error},
                )
        finally:
            await engine.dispose()

    asyncio.run(_run())


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


def test_get_admin_crisis_jobs_returns_seeded_rows() -> None:
    crisis_id = _seed_crisis()
    _seed_job(crisis_id, "ingest_buildings", "succeeded")

    try:
        with TestClient(app) as client:
            response = client.get(f"/admin/crises/{crisis_id}/jobs")
        assert response.status_code == 200, response.text
        body: list[dict[str, Any]] = response.json()
        assert len(body) == 1
        item = body[0]
        assert item["job_type"] == "ingest_buildings"
        assert item["status"] == "succeeded"
        assert item["error"] is None
        assert item["started_at"] is not None
        assert item["ended_at"] is not None
    finally:
        _delete_crisis(crisis_id)  # cascades crisis_jobs


def test_get_admin_crisis_jobs_returns_empty_for_no_jobs() -> None:
    crisis_id = _seed_crisis()
    try:
        with TestClient(app) as client:
            response = client.get(f"/admin/crises/{crisis_id}/jobs")
        assert response.status_code == 200, response.text
        assert response.json() == []
    finally:
        _delete_crisis(crisis_id)


def test_get_admin_crisis_jobs_surfaces_failed_error_message() -> None:
    crisis_id = _seed_crisis()
    _seed_job(
        crisis_id,
        "ingest_buildings",
        "failed",
        error="boom: redis was unreachable",
    )

    try:
        with TestClient(app) as client:
            body = client.get(f"/admin/crises/{crisis_id}/jobs").json()
        assert len(body) == 1
        assert body[0]["status"] == "failed"
        assert body[0]["error"] == "boom: redis was unreachable"
    finally:
        _delete_crisis(crisis_id)
