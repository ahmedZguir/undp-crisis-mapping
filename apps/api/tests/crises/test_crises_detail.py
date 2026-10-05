"""Integration tests for `GET /crises/{id}` — public detail with form schema
and version, locale-resolved labels, and (id, locale, form_version) cache.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from api.core.config import get_settings
from api.crises.default_form import DEFAULT_FORM_SCHEMA
from api.main import app

pytestmark = pytest.mark.integration


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


def _activate(crisis_id: uuid.UUID) -> None:
    settings = get_settings()

    async def _run() -> None:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text("update public.crises set status='active' where id=:id"),
                    {"id": str(crisis_id)},
                )
        finally:
            await engine.dispose()

    asyncio.run(_run())


def _create_active_crisis(client: TestClient) -> uuid.UUID:
    name = f"Detail test {uuid.uuid4().hex[:8]}"
    resp = client.post("/admin/crises", json={"name": name, "type": "flood"})
    assert resp.status_code == 201, resp.text
    cid = uuid.UUID(resp.json()["id"])
    _activate(cid)
    return cid


def test_get_crisis_detail_includes_form_schema_and_version() -> None:
    with TestClient(app) as client:
        cid = _create_active_crisis(client)
        try:
            resp = client.get(f"/crises/{cid}")
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["id"] == str(cid)
            assert body["form_version"] == 1
            assert body["form_schema"] == DEFAULT_FORM_SCHEMA
        finally:
            _delete_seed([cid])


def test_get_crisis_detail_returns_404_for_unknown_id() -> None:
    with TestClient(app) as client:
        resp = client.get(f"/crises/{uuid.uuid4()}")
    assert resp.status_code == 404


def test_get_crisis_detail_locale_query_overrides_accept_language() -> None:
    """The default schema has no locale maps yet, so labels are pass-through —
    but the resolver code path must be exercised regardless. Both ?locale=ar
    and Accept-Language: ar should produce the same response shape."""
    with TestClient(app) as client:
        cid = _create_active_crisis(client)
        try:
            r1 = client.get(f"/crises/{cid}?locale=ar")
            r2 = client.get(f"/crises/{cid}", headers={"Accept-Language": "ar,en;q=0.5"})
            assert r1.status_code == 200
            assert r2.status_code == 200
            assert r1.json()["form_schema"] == r2.json()["form_schema"]
        finally:
            _delete_seed([cid])


def test_get_crisis_detail_cache_invalidates_when_form_version_bumps() -> None:
    """Bumping form_version changes the cache key; a subsequent GET must
    reflect the new version (no stale read from a previous cache entry)."""
    settings = get_settings()
    with TestClient(app) as client:
        cid = _create_active_crisis(client)
        try:
            first = client.get(f"/crises/{cid}").json()
            assert first["form_version"] == 1

            async def _bump() -> None:
                engine = create_async_engine(settings.database_url)
                try:
                    async with engine.begin() as conn:
                        await conn.execute(
                            text("update public.crises set form_version=2 where id=:id"),
                            {"id": str(cid)},
                        )
                finally:
                    await engine.dispose()

            asyncio.run(_bump())

            second = client.get(f"/crises/{cid}").json()
            assert second["form_version"] == 2
        finally:
            _delete_seed([cid])
