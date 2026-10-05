"""Integration tests for `GET /crises/{id}/stats`.

Seeds reports directly into `public.reports` (with explicit `created_at`) so
the time-window assertions are deterministic. `heat_cells` is populated
directly to control `affected_cells` independently of the writer.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from api.core.config import get_settings
from api.core.h3 import cell_for_location
from api.main import app

pytestmark = pytest.mark.integration


async def _seed_crisis(engine_url: str, *, status: str = "active", k: int = 1) -> uuid.UUID:
    crisis_id = uuid.uuid4()
    engine = create_async_engine(engine_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "insert into public.crises "
                    "  (id, name, status, created_at, heatmap_k_anonymity) "
                    "values (:id, :n, :s, :t, :k)"
                ),
                {
                    "id": str(crisis_id),
                    "n": f"Stats test {uuid.uuid4().hex[:8]}",
                    "s": status,
                    "t": datetime.now(UTC) - timedelta(minutes=1),
                    "k": k,
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
    created_at: datetime | None = None,
    location: tuple[float, float] | None = None,
) -> None:
    engine = create_async_engine(engine_url)
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


async def _seed_cell(
    engine_url: str,
    *,
    crisis_id: uuid.UUID,
    cell: int,
    report_count: int = 1,
) -> None:
    engine = create_async_engine(engine_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "insert into public.heat_cells "
                    "  (crisis_id, h3_cell, report_count, partial_count, latest_at) "
                    "values (:cid, :cell, :rc, :rc, :ts)"
                ),
                {
                    "cid": str(crisis_id),
                    "cell": cell,
                    "rc": report_count,
                    "ts": datetime.now(UTC),
                },
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


# --- Tests ---------------------------------------------------------------


def test_total_and_by_class_match_manual_count() -> None:
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis(settings.database_url))
    asyncio.run(_seed_report(settings.database_url, crisis_id=crisis_id, damage_class="minimal"))
    asyncio.run(_seed_report(settings.database_url, crisis_id=crisis_id, damage_class="minimal"))
    asyncio.run(_seed_report(settings.database_url, crisis_id=crisis_id, damage_class="partial"))
    asyncio.run(_seed_report(settings.database_url, crisis_id=crisis_id, damage_class="complete"))

    try:
        with TestClient(app) as client:
            response = client.get(f"/crises/{crisis_id}/stats")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total_reports"] == 4
        assert body["by_damage_class"] == {"minimal": 2, "partial": 1, "complete": 1}
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


def test_time_windows_filter_correctly() -> None:
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis(settings.database_url))
    now = datetime.now(UTC)
    asyncio.run(
        _seed_report(
            settings.database_url, crisis_id=crisis_id, created_at=now - timedelta(hours=1)
        )
    )
    asyncio.run(
        _seed_report(settings.database_url, crisis_id=crisis_id, created_at=now - timedelta(days=3))
    )
    asyncio.run(
        _seed_report(
            settings.database_url, crisis_id=crisis_id, created_at=now - timedelta(days=30)
        )
    )

    try:
        with TestClient(app) as client:
            body = client.get(f"/crises/{crisis_id}/stats").json()
        assert body["last_24h"] == 1
        assert body["last_7d"] == 2
        assert body["total_reports"] == 3
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


def test_affected_cells_respects_k() -> None:
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis(settings.database_url, k=2))
    cell_a = cell_for_location(25.30, 51.500)
    cell_b = cell_for_location(25.30, 51.501)
    asyncio.run(_seed_cell(settings.database_url, crisis_id=crisis_id, cell=cell_a, report_count=1))
    asyncio.run(_seed_cell(settings.database_url, crisis_id=crisis_id, cell=cell_b, report_count=5))

    try:
        with TestClient(app) as client:
            body = client.get(f"/crises/{crisis_id}/stats").json()
        # Only cell_b clears k=2.
        assert body["affected_cells"] == 1
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


def test_location_less_report_counts_in_total_only() -> None:
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis(settings.database_url))
    asyncio.run(_seed_report(settings.database_url, crisis_id=crisis_id, location=None))
    cell = cell_for_location(25.30, 51.50)
    asyncio.run(_seed_cell(settings.database_url, crisis_id=crisis_id, cell=cell, report_count=1))

    try:
        with TestClient(app) as client:
            body = client.get(f"/crises/{crisis_id}/stats").json()
        assert body["total_reports"] == 1
        assert body["affected_cells"] == 1
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


def test_inactive_crisis_returns_404() -> None:
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis(settings.database_url, status="inactive"))
    try:
        with TestClient(app) as client:
            response = client.get(f"/crises/{crisis_id}/stats")
        assert response.status_code == 404
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


def test_archived_crisis_returns_404() -> None:
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis(settings.database_url, status="archived"))
    try:
        with TestClient(app) as client:
            response = client.get(f"/crises/{crisis_id}/stats")
        assert response.status_code == 404
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))
