"""Integration test for GET /crises against a real Postgres.

Seeds two non-reserved crises (one active, one archived) alongside the
migration-seeded reserved row, then asserts the wire shape, ordering, the
archived-absent rule, and the Cache-Control header.
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


def _seed_crises() -> tuple[uuid.UUID, uuid.UUID, datetime, datetime]:
    """Insert one active and one archived non-reserved crisis. Returns ids
    and created_at timestamps so tests can assert ordering."""
    settings = get_settings()
    base = datetime.now(UTC).replace(microsecond=0)
    older_at = base - timedelta(days=2)
    newer_at = base - timedelta(hours=1)
    older_id = uuid.uuid4()
    newer_id = uuid.uuid4()
    archived_id = uuid.uuid4()
    suffix = uuid.uuid4().hex[:8]

    async def _insert() -> None:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "insert into public.crises (id, name, status, created_at) values "
                        "(:id1, :n1, 'active',   :t1), "
                        "(:id2, :n2, 'active',   :t2), "
                        "(:id3, :n3, 'archived', :t3)"
                    ),
                    {
                        "id1": str(older_id),
                        "n1": f"Older crisis {suffix}",
                        "t1": older_at,
                        "id2": str(newer_id),
                        "n2": f"Newer crisis {suffix}",
                        "t2": newer_at,
                        "id3": str(archived_id),
                        "n3": f"Archived crisis {suffix}",
                        "t3": older_at,
                    },
                )
        finally:
            await engine.dispose()

    asyncio.run(_insert())
    return older_id, newer_id, older_at, newer_at


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


def _seed_crisis_with_pmtiles_fields(name: str, pmtiles_url: str, release: str) -> uuid.UUID:
    """Insert one active crisis with `pmtiles_url` and `overture_release_pinned` set."""
    settings = get_settings()
    crisis_id = uuid.uuid4()

    async def _run() -> None:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "insert into public.crises "
                        "(id, name, status, pmtiles_url, overture_release_pinned) "
                        "values (:id, :n, 'active', :url, :rel)"
                    ),
                    {
                        "id": str(crisis_id),
                        "n": name,
                        "url": pmtiles_url,
                        "rel": release,
                    },
                )
        finally:
            await engine.dispose()

    asyncio.run(_run())
    return crisis_id


def test_get_crises_exposes_pmtiles_url_and_release_when_set() -> None:
    """Every row in `GET /crises` carries `pmtiles_url` and
    `overture_release_pinned`. They are null on rows where the ingest hasn't
    run, and populated on rows where it has."""
    name_with = f"With pmtiles {uuid.uuid4().hex[:8]}"
    object_path = "/storage/v1/object/public/crisis-pmtiles/abc.pmtiles"
    # Seed with a deliberately *stale* origin (a host the API is not served at).
    # `GET /crises` rebuilds the origin against the current `supabase_public_url`
    # on read, so a host change never requires re-ingest. See list_crises.
    pmtiles_url = f"https://example.supabase.co{object_path}"
    release = "fixture-2026-04-15.0"
    crisis_id_with = _seed_crisis_with_pmtiles_fields(name_with, pmtiles_url, release)

    older_id, newer_id, _, _ = _seed_crises()
    seeded_ids = [crisis_id_with, older_id, newer_id]

    try:
        with TestClient(app) as client:
            response = client.get("/crises")
        assert response.status_code == 200, response.text
        body: list[dict[str, object]] = response.json()

        # Every row exposes the two new fields.
        for item in body:
            assert "pmtiles_url" in item
            assert "overture_release_pinned" in item

        with_pmtiles = next(item for item in body if item["id"] == str(crisis_id_with))
        # Origin rebuilt to the current public base; stale `example.supabase.co`
        # is dropped, object path preserved. (dev-proxy off in this env.)
        expected = f"{get_settings().supabase_public_url.rstrip('/')}{object_path}"
        assert with_pmtiles["pmtiles_url"] == expected
        assert with_pmtiles["overture_release_pinned"] == release

        without_pmtiles = next(item for item in body if item["id"] == str(newer_id))
        assert without_pmtiles["pmtiles_url"] is None
        assert without_pmtiles["overture_release_pinned"] is None
    finally:
        _delete_seed(seeded_ids)


def test_get_crises_returns_active_with_reserved_last() -> None:
    settings = get_settings()
    older_id, newer_id, _, _ = _seed_crises()
    seeded_ids = [older_id, newer_id]

    try:
        with TestClient(app) as client:
            response = client.get("/crises")

        assert response.status_code == 200, response.text
        assert response.headers["Cache-Control"] == "public, max-age=60"

        body: list[dict[str, str]] = response.json()
        assert isinstance(body, list)
        assert len(body) >= 3  # two seeded actives + reserved row from migration

        for item in body:
            assert set(item.keys()) == {
                "id",
                "name",
                "pmtiles_url",
                "overture_release_pinned",
                "geometry",
                "public_visibility",
            }

        names: list[str] = [item["name"] for item in body]

        # Reserved row pinned last regardless of created_at.
        assert names[-1] == settings.reserved_crisis_name

        # Archived crisis is absent.
        assert not any("Archived crisis" in n for n in names)

        # Newer non-reserved seeded row appears before older seeded row.
        ids: list[uuid.UUID] = [uuid.UUID(item["id"]) for item in body]
        assert ids.index(newer_id) < ids.index(older_id)
    finally:
        _delete_seed(seeded_ids)
