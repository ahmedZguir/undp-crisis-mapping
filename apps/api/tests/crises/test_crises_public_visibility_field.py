"""`GET /crises` surfaces `public_visibility` on every row.

The citizen-facing `CrisisListItem` carries the per-crisis mode so the PWA
can pick a renderer before its first fetch.
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


_MODES = ("none", "aggregate_view", "buildings", "full")


async def _seed(engine_url: str, *, public_visibility: str, suffix: str) -> uuid.UUID:
    crisis_id = uuid.uuid4()
    engine = create_async_engine(engine_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "insert into public.crises "
                    "  (id, name, status, created_at, public_visibility, "
                    "   heatmap_k_anonymity) "
                    "values (:id, :n, 'active', :t, :pv, 1)"
                ),
                {
                    "id": str(crisis_id),
                    "n": f"PV field {public_visibility} {suffix}",
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
                text("delete from public.crises where id = any(:ids)"),
                {"ids": [str(i) for i in crisis_ids]},
            )
    finally:
        await engine.dispose()


def test_get_crises_includes_public_visibility_for_every_mode() -> None:
    """Seed one active crisis per `public_visibility` value and assert the
    public listing surfaces the mode verbatim on each row."""
    settings = get_settings()
    suffix = uuid.uuid4().hex[:6]
    ids: dict[str, uuid.UUID] = {
        mode: asyncio.run(_seed(settings.database_url, public_visibility=mode, suffix=suffix))
        for mode in _MODES
    }
    try:
        with TestClient(app) as client:
            response = client.get("/crises")
        assert response.status_code == 200, response.text
        items = response.json()
        for mode, crisis_id in ids.items():
            row = next((r for r in items if r["id"] == str(crisis_id)), None)
            assert row is not None, f"missing row for {mode}"
            assert "public_visibility" in row, f"row for {mode} omits the field"
            assert row["public_visibility"] == mode
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=list(ids.values())))
