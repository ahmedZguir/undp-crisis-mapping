"""Integration tests for GET /admin/crises.

The admin list returns every crisis regardless of status, ordered by
`created_at` desc. Distinct from the citizen-facing `GET /crises` which
filters to `status='active'`.
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


def _seed(name: str, status: str, *, country: str = "QA") -> uuid.UUID:
    settings = get_settings()
    crisis_id = uuid.uuid4()

    async def _run() -> None:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "insert into public.crises "
                        "  (id, name, status, type, countries, started_at, ended_at) "
                        "values "
                        "  (:id, :n, :s, 'flood', ARRAY[:c]::text[], "
                        "   '2026-04-01T00:00:00+00:00', null)"
                    ),
                    {"id": str(crisis_id), "n": name, "s": status, "c": country},
                )
        finally:
            await engine.dispose()

    asyncio.run(_run())
    return crisis_id


def _delete_seed(ids: list[uuid.UUID]) -> None:
    settings = get_settings()

    async def _run() -> None:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text("delete from public.crises where id = any(:ids)"),
                    {"ids": [str(i) for i in ids]},
                )
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_get_admin_crises_returns_all_statuses_with_metadata() -> None:
    suffix = uuid.uuid4().hex[:8]
    inactive_id = _seed(f"Inactive {suffix}", "inactive")
    active_id = _seed(f"Active {suffix}", "active")
    archived_id = _seed(f"Archived {suffix}", "archived")
    seeded = [inactive_id, active_id, archived_id]

    try:
        with TestClient(app) as client:
            response = client.get("/admin/crises")
        assert response.status_code == 200, response.text
        body: list[dict[str, object]] = response.json()
        by_id = {item["id"]: item for item in body}

        for crisis_id in seeded:
            assert str(crisis_id) in by_id, f"{crisis_id} missing from admin list"

        for crisis_id in seeded:
            item = by_id[str(crisis_id)]
            for key in (
                "id",
                "name",
                "status",
                "type",
                "countries",
                "started_at",
                "ended_at",
                "created_at",
                "has_geometry",
                "pmtiles_url",
                "overture_release_pinned",
                "buildings_ingested_at",
                "buildings_ingested_count",
            ):
                assert key in item, f"{key} missing from item {crisis_id}"
            assert item["type"] == "flood"
            assert item["countries"] == ["QA"]
            # Fresh seeds have never been ingested.
            assert item["buildings_ingested_at"] is None
            assert item["buildings_ingested_count"] is None

        assert by_id[str(inactive_id)]["status"] == "inactive"
        assert by_id[str(active_id)]["status"] == "active"
        assert by_id[str(archived_id)]["status"] == "archived"
    finally:
        _delete_seed(seeded)


def _seed_with_count(name: str, count: int) -> uuid.UUID:
    """Seed an `active` crisis row with `buildings_ingested_at` + `count` already
    stamped — the shape `BuildingIngestJob` leaves behind on success.
    """
    settings = get_settings()
    crisis_id = uuid.uuid4()

    async def _run() -> None:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "insert into public.crises "
                        "  (id, name, status, type, countries, "
                        "   buildings_ingested_at, buildings_ingested_count) "
                        "values (:id, :n, 'active', 'flood', "
                        "        ARRAY['QA']::text[], now(), :c)"
                    ),
                    {"id": str(crisis_id), "n": name, "c": count},
                )
        finally:
            await engine.dispose()

    asyncio.run(_run())
    return crisis_id


def test_get_admin_crises_surfaces_buildings_ingested_count() -> None:
    """A crisis with a stamped count must round-trip through the admin list."""
    name = f"Stamped {uuid.uuid4().hex[:8]}"
    crisis_id = _seed_with_count(name, count=4321)

    try:
        with TestClient(app) as client:
            body: list[dict[str, object]] = client.get("/admin/crises").json()
        item = next(c for c in body if c["id"] == str(crisis_id))
        assert item["buildings_ingested_count"] == 4321
        assert item["buildings_ingested_at"] is not None
    finally:
        _delete_seed([crisis_id])


def test_get_admin_crises_orders_by_created_at_desc() -> None:
    suffix = uuid.uuid4().hex[:8]
    older_id = _seed(f"Older {suffix}", "active")
    # Tiny sleep would be unreliable; rely on insertion order producing
    # distinct created_at via clock_timestamp.
    newer_id = _seed(f"Newer {suffix}", "inactive")

    try:
        with TestClient(app) as client:
            body = client.get("/admin/crises").json()
        ids = [item["id"] for item in body]
        assert ids.index(str(newer_id)) < ids.index(str(older_id))
    finally:
        _delete_seed([older_id, newer_id])
