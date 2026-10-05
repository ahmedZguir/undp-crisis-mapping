"""End-to-end integration tests for `GET /reports?client_id=…`.

Drives the full read path: FastAPI route → CitizenReportsHistoryService
→ Postgres → Supabase signed-URL minting. Requires `supabase start` to be
running locally; configuration is loaded from `.env` via Settings.

Mirrors the live-stack pattern from `test_reports_integration.py`: seed
rows directly with SQL, drive the endpoint via `TestClient`, clean up in
`finally` so a half-passed run leaves no orphan rows.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from api.core.config import get_settings
from api.main import app
from api.reports.history import MAX_HISTORY_ITEMS, CitizenReportsHistoryService
from api.reports.routes import get_reports_history_service

FIXTURE = Path(__file__).parent.parent / "fixtures" / "tiny.jpg"


pytestmark = pytest.mark.integration


# --- helpers -------------------------------------------------------------


def _seed_crisis(name: str, status: str = "active") -> uuid.UUID:
    settings = get_settings()
    crisis_id = uuid.uuid4()

    async def _run() -> None:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "insert into public.crises (id, name, status, created_at) "
                        "values (:id, :n, :s, :t)"
                    ),
                    {
                        "id": str(crisis_id),
                        "n": name,
                        "s": status,
                        "t": datetime.now(UTC) - timedelta(minutes=1),
                    },
                )
        finally:
            await engine.dispose()

    asyncio.run(_run())
    return crisis_id


def _delete_crisis(crisis_id: uuid.UUID) -> None:
    settings = get_settings()

    async def _run() -> None:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text("delete from public.reports where crisis_id = :id"),
                    {"id": str(crisis_id)},
                )
                await conn.execute(
                    text("delete from public.crises where id = :id"),
                    {"id": str(crisis_id)},
                )
        finally:
            await engine.dispose()

    asyncio.run(_run())


def _submit_report(
    client: TestClient,
    crisis_id: uuid.UUID,
    client_id: uuid.UUID,
    *,
    damage_class: str = "partial",
    description: str | None = None,
    location: dict[str, float] | None = None,
) -> uuid.UUID:
    """POST /reports against the live stack; return the new row id.

    Used to populate history rows end-to-end so the test exercises the
    same write path the PWA does (real photo upload, real DB insert).
    """
    payload: dict[str, object] = {
        "crisis_id": str(crisis_id),
        "damage_class": damage_class,
        "client_id": str(client_id),
        # Default GPS fix so the minimum-content gate passes; callers that
        # care about a specific location override it below.
        "location": {"lat": 25.2854, "lng": 51.5310},
    }
    if description is not None:
        payload["description"] = description
    if location is not None:
        payload["location"] = location
    files = {
        "photo": ("tiny.jpg", FIXTURE.read_bytes(), "image/jpeg"),
        "data": (None, json.dumps(payload), "application/json"),
    }
    response = client.post("/reports", files=files)
    assert response.status_code == 200, response.text
    return uuid.UUID(response.json()["id"])


# --- url-signer override -------------------------------------------------


class _FakeSigner:
    """Stand-in `PhotoUrlSigner` that returns a deterministic URL.

    Used by tests that seed report rows directly with bogus `photo_path`
    values (e.g. cap-bulk-insert, archived-crisis label). Supabase
    Storage correctly refuses to sign URLs for missing objects, but
    those tests only care about the SQL behaviour — the real-signer
    coverage lives in `test_history_signed_url_is_fetchable`.
    """

    async def sign_photo_url(self, photo_path: str, ttl_seconds: int) -> str:
        return f"https://fake.test/signed?path={photo_path}&ttl={ttl_seconds}"


def _override_history_service_with_fake_signer() -> None:
    """Swap the history service for one with a fake URL signer.

    Call inside the `with TestClient(app) as client:` block — the
    lifespan must have run so `app.state.container.sessionmaker` is
    available. Use `_clear_history_service_override` to undo.
    """

    def _override() -> CitizenReportsHistoryService:
        state = app.state.container
        return CitizenReportsHistoryService(
            sessionmaker=state.sessionmaker,
            url_signer=_FakeSigner(),
        )

    app.dependency_overrides[get_reports_history_service] = _override


def _clear_history_service_override() -> None:
    app.dependency_overrides.pop(get_reports_history_service, None)


# --- tests ---------------------------------------------------------------


def test_history_returns_only_requested_clients_reports_in_recency_order() -> None:
    crisis_id = _seed_crisis(f"History crisis {uuid.uuid4().hex[:8]}")
    client_a = uuid.uuid4()
    client_b = uuid.uuid4()
    try:
        with TestClient(app) as client:
            id1 = _submit_report(client, crisis_id, client_a, description="first")
            # Defend against same-millisecond `created_at` collisions: the
            # ORDER BY tiebreaker is `id desc`, but UUIDv4 is not time-
            # sortable, so two writes in the same tick could flip ordering.
            time.sleep(0.05)
            id2 = _submit_report(client, crisis_id, client_a, description="second")
            _submit_report(client, crisis_id, client_b, description="other client")

            response = client.get("/reports", params={"client_id": str(client_a)})
            assert response.status_code == 200, response.text
            body = response.json()

            assert body["total"] == 2
            returned_ids = [item["id"] for item in body["items"]]
            # Recency desc — the second submit must come first.
            assert returned_ids == [str(id2), str(id1)]
            for item in body["items"]:
                assert item["photo_url"].startswith("http")
                assert "token=" in item["photo_url"]
    finally:
        _delete_crisis(crisis_id)


def test_history_returns_empty_envelope_for_unknown_client() -> None:
    with TestClient(app) as client:
        response = client.get("/reports", params={"client_id": str(uuid.uuid4())})
        assert response.status_code == 200, response.text
        assert response.json() == {"items": [], "total": 0}


def test_history_joins_crisis_name_and_status_across_crises() -> None:
    active_id = _seed_crisis(f"Active {uuid.uuid4().hex[:8]}", status="active")
    archived_id = _seed_crisis(f"Archived {uuid.uuid4().hex[:8]}", status="archived")
    client_id = uuid.uuid4()
    try:
        with TestClient(app) as client:
            # Seeds both rows directly (POST refuses archived crises with 409),
            # so the photo paths are bogus — use the fake signer.
            _override_history_service_with_fake_signer()
            try:
                asyncio.run(_seed_report_row(active_id, client_id))
                asyncio.run(_seed_report_row(archived_id, client_id))

                response = client.get("/reports", params={"client_id": str(client_id)})
                assert response.status_code == 200, response.text
                items = response.json()["items"]

                assert len(items) == 2
                by_crisis = {item["crisis_id"]: item for item in items}
                assert by_crisis[str(active_id)]["crisis_status"] == "active"
                assert by_crisis[str(archived_id)]["crisis_status"] == "archived"
                assert by_crisis[str(active_id)]["crisis_name"].startswith("Active ")
                assert by_crisis[str(archived_id)]["crisis_name"].startswith("Archived ")
            finally:
                _clear_history_service_override()
    finally:
        _delete_crisis(active_id)
        _delete_crisis(archived_id)


def test_history_caps_at_max_history_items() -> None:
    crisis_id = _seed_crisis(f"Cap crisis {uuid.uuid4().hex[:8]}")
    client_id = uuid.uuid4()
    over_cap = MAX_HISTORY_ITEMS + 3
    try:
        asyncio.run(_seed_n_report_rows(crisis_id, client_id, over_cap))

        with TestClient(app) as client:
            # Bulk-seeded rows have bogus photo paths — use the fake signer
            # to avoid Supabase 404s on each row.
            _override_history_service_with_fake_signer()
            try:
                response = client.get("/reports", params={"client_id": str(client_id)})
                assert response.status_code == 200, response.text
                body = response.json()

                assert body["total"] == MAX_HISTORY_ITEMS
                assert len(body["items"]) == MAX_HISTORY_ITEMS
            finally:
                _clear_history_service_override()
    finally:
        _delete_crisis(crisis_id)


def test_history_round_trips_location_and_other_fields() -> None:
    crisis_id = _seed_crisis(f"Fields crisis {uuid.uuid4().hex[:8]}")
    client_id = uuid.uuid4()
    try:
        with TestClient(app) as client:
            _submit_report(
                client,
                crisis_id,
                client_id,
                description="wall crack",
                location={"lat": 25.2854, "lng": 51.5310},
            )

            response = client.get("/reports", params={"client_id": str(client_id)})
            assert response.status_code == 200, response.text
            item = response.json()["items"][0]

            assert item["description"] == "wall crack"
            assert item["location"] == {"lat": 25.2854, "lng": 51.5310}
            assert item["damage_class"] == "partial"
    finally:
        _delete_crisis(crisis_id)


def test_history_signed_url_is_fetchable() -> None:
    crisis_id = _seed_crisis(f"Photo crisis {uuid.uuid4().hex[:8]}")
    client_id = uuid.uuid4()
    try:
        with TestClient(app) as client:
            _submit_report(client, crisis_id, client_id)
            response = client.get("/reports", params={"client_id": str(client_id)})
            assert response.status_code == 200, response.text
            photo_url = response.json()["items"][0]["photo_url"]

            # The URL is signed for browser fetch — must succeed without
            # any service-role auth header.
            fetched = httpx.get(photo_url, timeout=5.0)
            assert fetched.status_code == 200, fetched.text
            assert fetched.content == FIXTURE.read_bytes()
    finally:
        _delete_crisis(crisis_id)


# --- direct-seed helpers (skip POST when we need archived-crisis rows) ---


async def _seed_report_row(crisis_id: uuid.UUID, client_id: uuid.UUID) -> None:
    """Insert a report row directly. Used for cases the POST path
    refuses (archived crisis) — the read endpoint sees the row either
    way and we want to prove it."""
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "insert into public.reports "
                    "(crisis_id, damage_class, photo_path, client_id, route_description) "
                    "values (:crisis_id, 'minimal', :path, :client_id, 'test directions')"
                ),
                {
                    "crisis_id": str(crisis_id),
                    "path": f"seed/{uuid.uuid4().hex}.jpg",
                    "client_id": str(client_id),
                },
            )
    finally:
        await engine.dispose()


async def _seed_n_report_rows(crisis_id: uuid.UUID, client_id: uuid.UUID, count: int) -> None:
    """Bulk-insert `count` report rows. The cap test needs more rows
    than would be ergonomic to POST one at a time."""
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "insert into public.reports "
                    "(crisis_id, damage_class, photo_path, client_id, route_description) "
                    "select :crisis_id, 'minimal', "
                    "       'seed/' || gen_random_uuid()::text || '.jpg', "
                    "       :client_id, 'test directions' "
                    "from generate_series(1, :n)"
                ),
                {
                    "crisis_id": str(crisis_id),
                    "client_id": str(client_id),
                    "n": count,
                },
            )
    finally:
        await engine.dispose()
