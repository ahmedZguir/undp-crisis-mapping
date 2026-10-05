"""`GET /crises/{id}/stats` widened-gate behaviour.

The stats endpoint returns 200 for any live mode (`aggregate_view`, `buildings`,
`full`) and 404 only for `none` (plus the existing `status != 'active'`
404). Aggregate counts are strictly less specific than what `buildings`
and `full` modes already expose, so the stricter old gate was a UX
regression.

Supersedes `test_heatmap_stats_public_visibility.py` (the old
aggregate-view-only matrix). The non-active matrix is still checked
here so the active-only gate stays guarded.
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
from api.main import app

pytestmark = pytest.mark.integration


async def _seed_crisis(
    engine_url: str,
    *,
    public_visibility: str,
    status: str = "active",
) -> uuid.UUID:
    crisis_id = uuid.uuid4()
    engine = create_async_engine(engine_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "insert into public.crises "
                    "  (id, name, status, created_at, public_visibility, "
                    "   heatmap_k_anonymity) "
                    "values (:id, :n, :s, :t, :pv, 1)"
                ),
                {
                    "id": str(crisis_id),
                    "n": f"Stats modes {public_visibility}/{status} {uuid.uuid4().hex[:6]}",
                    "s": status,
                    "t": datetime.now(UTC) - timedelta(minutes=1),
                    "pv": public_visibility,
                },
            )
    finally:
        await engine.dispose()
    return crisis_id


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


@pytest.mark.parametrize("visibility", ["aggregate_view", "buildings", "full"])
def test_live_visibility_modes_return_200(visibility: str) -> None:
    """`aggregate_view`, `buildings`, and `full` all pass the widened gate."""
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis(settings.database_url, public_visibility=visibility))
    try:
        with TestClient(app) as client:
            response = client.get(f"/crises/{crisis_id}/stats")
        assert response.status_code == 200, response.text
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


def test_none_visibility_returns_404() -> None:
    """`none` is the only visibility that 404s the stats endpoint."""
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis(settings.database_url, public_visibility="none"))
    try:
        with TestClient(app) as client:
            response = client.get(f"/crises/{crisis_id}/stats")
        assert response.status_code == 404, response.text
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


@pytest.mark.parametrize("visibility", ["none", "aggregate_view", "buildings", "full"])
@pytest.mark.parametrize("status", ["inactive", "archived"])
def test_non_active_status_returns_404_regardless_of_visibility(
    visibility: str, status: str
) -> None:
    """`status != 'active'` always 404s, even with a live visibility mode."""
    settings = get_settings()
    crisis_id = asyncio.run(
        _seed_crisis(settings.database_url, public_visibility=visibility, status=status)
    )
    try:
        with TestClient(app) as client:
            response = client.get(f"/crises/{crisis_id}/stats")
        assert response.status_code == 404, response.text
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))
