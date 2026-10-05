"""Integration tests for `/admin/reports/search/views` — saved filter snapshots.

The saved-views surface is coordinator-scoped: rows carry a
`coordinator_id` that the handler matches against the JWT subject
returned by `require_coordinator`. The local conftest installs a
bypass-coordinator whose UUID is fixed; we lean on that for ownership
assertions.
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

pytestmark = pytest.mark.integration


async def _seed_crisis(engine_url: str) -> uuid.UUID:
    crisis_id = uuid.uuid4()
    suffix = uuid.uuid4().hex[:8]
    engine = create_async_engine(engine_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "insert into public.crises "
                    "  (id, name, status, type, countries) "
                    "values "
                    "  (:id, :n, 'active', 'flood', ARRAY['QA']::text[])"
                ),
                {"id": str(crisis_id), "n": f"Saved-views test {suffix}"},
            )
    finally:
        await engine.dispose()
    return crisis_id


async def _cleanup(engine_url: str, *, crisis_ids: list[uuid.UUID]) -> None:
    engine = create_async_engine(engine_url)
    try:
        async with engine.begin() as conn:
            # The saved_search_views FK to crises is on-delete-cascade so
            # the views go away with the crisis.
            await conn.execute(
                text("delete from public.crises where id = any(:ids)"),
                {"ids": [str(i) for i in crisis_ids]},
            )
    finally:
        await engine.dispose()


def test_saved_views_crud_round_trip() -> None:
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis(settings.database_url))
    try:
        with TestClient(app) as client:
            # Create
            body = {
                "name": "Yesterday's flooding",
                "filter": {
                    "query": "flood",
                    "strictness": "balanced",
                    "limit": 500,
                },
            }
            r = client.post(
                "/admin/reports/search/views",
                params={"crisis_id": str(crisis_id)},
                json=body,
            )
            assert r.status_code == 201, r.text
            created = r.json()
            view_id = created["id"]
            assert created["name"] == body["name"]
            assert created["filter_signature"]
            assert created["filter"]["query"] == "flood"

            # List
            r = client.get(
                "/admin/reports/search/views",
                params={"crisis_id": str(crisis_id)},
            )
            assert r.status_code == 200
            assert any(v["id"] == view_id for v in r.json())

            # Get
            r = client.get(f"/admin/reports/search/views/{view_id}")
            assert r.status_code == 200
            assert r.json()["id"] == view_id

            # Patch (rename + update filter)
            r = client.patch(
                f"/admin/reports/search/views/{view_id}",
                json={
                    "name": "Renamed",
                    "filter": {"query": "fire", "strictness": "strict", "limit": 300},
                },
            )
            assert r.status_code == 200, r.text
            patched = r.json()
            assert patched["name"] == "Renamed"
            assert patched["filter"]["query"] == "fire"
            assert patched["filter_signature"] != created["filter_signature"]

            # Delete
            r = client.delete(f"/admin/reports/search/views/{view_id}")
            assert r.status_code == 204
            r = client.get(f"/admin/reports/search/views/{view_id}")
            assert r.status_code == 404
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))


def test_saved_view_patch_requires_at_least_one_field() -> None:
    settings = get_settings()
    crisis_id = asyncio.run(_seed_crisis(settings.database_url))
    try:
        with TestClient(app) as client:
            create = client.post(
                "/admin/reports/search/views",
                params={"crisis_id": str(crisis_id)},
                json={"name": "stub", "filter": {}},
            )
            view_id = create.json()["id"]
            r = client.patch(f"/admin/reports/search/views/{view_id}", json={})
            assert r.status_code == 400
    finally:
        asyncio.run(_cleanup(settings.database_url, crisis_ids=[crisis_id]))
