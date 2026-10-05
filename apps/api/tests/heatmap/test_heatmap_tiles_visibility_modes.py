"""`GET /crises/{id}/tiles/heat/{z}/{x}/{y}.pbf` mode-specific gate.

The tile endpoint stays `aggregate_view`-only — `buildings` and `full` modes
have their own surfaces. The tile path swaps
`validate_public_visible` for `validate_public_visible_for_mode(
crisis_id, 'aggregate_view')`, so `buildings`, `full`, `none`, and
`inactive` all 404.

Mirrors the seeding pattern in `test_heatmap_tiles_public_visibility.py`.
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


_DOHA_TILE = (15, 21071, 14002)


async def _seed_crisis(
    engine_url: str,
    *,
    public_visibility: str = "aggregate_view",
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
                    "n": f"Tile modes {public_visibility}/{status} {uuid.uuid4().hex[:6]}",
                    "s": status,
                    "t": datetime.now(UTC) - timedelta(minutes=1),
                    "pv": public_visibility,
                },
            )
            # Seed one cell so a 200 response would have features.
            cell = cell_for_location(25.30, 51.50)
            await conn.execute(
                text(
                    "insert into public.heat_cells "
                    "  (crisis_id, h3_cell, report_count, partial_count, latest_at) "
                    "values (:cid, :cell, 1, 1, :ts)"
                ),
                {"cid": str(crisis_id), "cell": cell, "ts": datetime.now(UTC)},
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
                text("delete from public.crises where id = any(:ids)"),
                {"ids": [str(i) for i in crisis_ids]},
            )
    finally:
        await engine.dispose()


def test_aggregate_view_returns_200() -> None:
    """The v1-default `aggregate_view` setting is the ONLY one the tile
    endpoint serves under the mode-specific gate."""
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis(settings.database_url, public_visibility="aggregate_view"))
    try:
        with TestClient(app) as client:
            z, x, y = _DOHA_TILE
            response = client.get(f"/crises/{crisis_id}/tiles/heat/{z}/{x}/{y}.pbf")
        assert response.status_code == 200, response.text
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


@pytest.mark.parametrize("visibility", ["none", "buildings", "full"])
def test_other_visibility_modes_return_404(visibility: str) -> None:
    """`buildings` and `full` 404 because the tile path is bound to
    `aggregate_view` only — they have their own surfaces. `none`
    404s on visibility grounds."""
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis(settings.database_url, public_visibility=visibility))
    try:
        with TestClient(app) as client:
            z, x, y = _DOHA_TILE
            response = client.get(f"/crises/{crisis_id}/tiles/heat/{z}/{x}/{y}.pbf")
        assert response.status_code == 404, response.text
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


@pytest.mark.parametrize("status", ["inactive", "archived"])
def test_inactive_status_returns_404_even_in_aggregate_view(status: str) -> None:
    """A non-active crisis 404s the tile endpoint even when its visibility
    is `aggregate_view`."""
    settings = get_settings()
    crisis_id = asyncio.run(
        _seed_crisis(settings.database_url, public_visibility="aggregate_view", status=status)
    )
    try:
        with TestClient(app) as client:
            z, x, y = _DOHA_TILE
            response = client.get(f"/crises/{crisis_id}/tiles/heat/{z}/{x}/{y}.pbf")
        assert response.status_code == 404, response.text
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


def test_visibility_flip_takes_effect_after_patch() -> None:
    """Switching from `aggregate_view` to `buildings` via PATCH flips the
    tile endpoint from 200 to 404 on the next request — the admin PATCH
    handler invalidates the `CrisisRow` cache so the flip is visible
    within the next request, not 30 s later.

    Regression guard for the mode-helper migration: under the old
    `validate_public_visible` (any-non-aggregate 404s) the flip to
    `buildings` already 404'd the tile; under the new mode-helper the
    flip must STILL 404 because `buildings != aggregate_view`.
    """
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis(settings.database_url, public_visibility="aggregate_view"))
    try:
        with TestClient(app) as client:
            z, x, y = _DOHA_TILE
            first = client.get(f"/crises/{crisis_id}/tiles/heat/{z}/{x}/{y}.pbf")
            assert first.status_code == 200

            patch = client.patch(
                f"/admin/crises/{crisis_id}",
                json={"public_visibility": "buildings"},
            )
            assert patch.status_code == 200, patch.text

            second = client.get(f"/crises/{crisis_id}/tiles/heat/{z}/{x}/{y}.pbf")
            assert second.status_code == 404
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))
