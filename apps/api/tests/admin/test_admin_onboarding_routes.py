"""Integration tests for `/admin/onboarding` — per-coordinator first-run state.

The surface is coordinator-scoped: the single row is keyed on the JWT
subject returned by `require_coordinator`. The local conftest installs a
bypass coordinator with a fixed UUID; we key cleanup off that.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from api.core.config import get_settings
from api.main import app

# Matches the bypass coordinator in tests/conftest.py.
_BYPASS_COORDINATOR_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")


pytestmark = pytest.mark.integration


async def _reset(engine_url: str) -> None:
    engine = create_async_engine(engine_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("delete from public.coordinator_onboarding where coordinator_id = :id"),
                {"id": str(_BYPASS_COORDINATOR_ID)},
            )
    finally:
        await engine.dispose()


def test_onboarding_state_round_trip() -> None:
    settings = get_settings()
    asyncio.run(_reset(settings.database_url))
    try:
        with TestClient(app) as client:
            # No row yet → both milestones false.
            r = client.get("/admin/onboarding")
            assert r.status_code == 200, r.text
            assert r.json() == {
                "walkthrough_completed": False,
                "dashboard_explored": False,
            }

            # Mark the walkthrough done; dashboard stays untouched.
            r = client.patch("/admin/onboarding", json={"walkthrough_completed": True})
            assert r.status_code == 200, r.text
            assert r.json() == {
                "walkthrough_completed": True,
                "dashboard_explored": False,
            }

            # Mark the dashboard explored; walkthrough persists.
            r = client.patch("/admin/onboarding", json={"dashboard_explored": True})
            assert r.status_code == 200, r.text
            assert r.json() == {
                "walkthrough_completed": True,
                "dashboard_explored": True,
            }

            # Re-reading returns the persisted state.
            r = client.get("/admin/onboarding")
            assert r.json() == {
                "walkthrough_completed": True,
                "dashboard_explored": True,
            }
    finally:
        asyncio.run(_reset(settings.database_url))


def test_onboarding_marks_are_monotonic() -> None:
    """Sending `false` (or omitting a field) never clears a set milestone."""
    settings = get_settings()
    asyncio.run(_reset(settings.database_url))
    try:
        with TestClient(app) as client:
            client.patch("/admin/onboarding", json={"walkthrough_completed": True})

            # An explicit false must not undo it (replaying the walkthrough
            # should never reset completion).
            r = client.patch("/admin/onboarding", json={"walkthrough_completed": False})
            assert r.status_code == 200, r.text
            assert r.json()["walkthrough_completed"] is True

            # An empty patch is a harmless no-op.
            r = client.patch("/admin/onboarding", json={})
            assert r.json() == {
                "walkthrough_completed": True,
                "dashboard_explored": False,
            }
    finally:
        asyncio.run(_reset(settings.database_url))
