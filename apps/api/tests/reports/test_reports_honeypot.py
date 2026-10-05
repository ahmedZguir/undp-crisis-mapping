"""Tests for the `message` honeypot field on `POST /reports`.

A naïve form-spamming bot
that scrapes the DOM for input fields and POSTs every one will fill the
`message` field; real callers (the PWA) never do. The route handler's
first action is the short-circuit: log an INFO line, return a real-
shaped success response with a fresh UUID, and do NOT insert a row or
upload a photo.

The response shape must match a real 200 — same Pydantic model, same
keys — so a bot reading `response.json()` cannot detect the trap from
the body.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from api.core.config import get_settings
from api.main import app

FIXTURE = Path(__file__).parent.parent / "fixtures" / "tiny.jpg"


pytestmark = pytest.mark.integration


def _seed_crisis() -> uuid.UUID:
    settings = get_settings()
    crisis_id = uuid.uuid4()
    suffix = uuid.uuid4().hex[:8]

    async def _run() -> None:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "insert into public.crises (id, name, status, created_at) "
                        "values (:id, :n, 'active', :t)"
                    ),
                    {
                        "id": str(crisis_id),
                        "n": f"honeypot test crisis {suffix}",
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


def _count_reports(crisis_id: uuid.UUID) -> int:
    settings = get_settings()

    async def _run() -> int:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.connect() as conn:
                count = (
                    await conn.execute(
                        text("select count(*) from public.reports where crisis_id = :id"),
                        {"id": str(crisis_id)},
                    )
                ).scalar_one()
                return int(count) if count is not None else 0
        finally:
            await engine.dispose()

    return asyncio.run(_run())


def test_honeypot_returns_200_and_does_not_insert(
    caplog: pytest.LogCaptureFixture,
) -> None:
    crisis_id = _seed_crisis()
    try:
        with TestClient(app) as client:
            payload = {
                "crisis_id": str(crisis_id),
                "damage_class": "minimal",
                "description": "real-looking description",
                "client_id": str(uuid.uuid4()),
                "message": "click here to win a prize",  # honeypot trip
            }
            files = {
                "photo": ("tiny.jpg", FIXTURE.read_bytes(), "image/jpeg"),
                "data": (None, json.dumps(payload), "application/json"),
            }

            with caplog.at_level(logging.INFO, logger="api.reports.routes"):
                response = client.post("/reports", files=files)

            # Real-shaped 200 with a UUID and the standard response keys.
            assert response.status_code == 200, response.text
            body = response.json()
            assert {"id", "crisis_id", "created_at", "damage_class"} <= set(body.keys())
            # UUID parses cleanly.
            uuid.UUID(body["id"])
            # `crisis_id` echoes the payload (matches a real success).
            assert body["crisis_id"] == str(crisis_id)
            assert body["damage_class"] == "minimal"

            # No DB row inserted.
            assert _count_reports(crisis_id) == 0

            # INFO log line emitted.
            honeypot_records = [r for r in caplog.records if "honeypot.triggered" in r.getMessage()]
            assert honeypot_records, (
                f"no honeypot.triggered log entry; got {[r.getMessage() for r in caplog.records]}"
            )
    finally:
        _delete_crisis(crisis_id)


def test_honeypot_field_absent_inserts_normally() -> None:
    """A request without the `message` field follows the normal path
    and inserts a row — the honeypot is opt-in via the field."""
    crisis_id = _seed_crisis()
    try:
        with TestClient(app) as client:
            payload = {
                "crisis_id": str(crisis_id),
                "damage_class": "minimal",
                "client_id": str(uuid.uuid4()),
                # Satisfies the minimum-content gate (location OR route).
                "location": {"lat": 25.2854, "lng": 51.5310},
                # no `message` key
            }
            files = {
                "photo": ("tiny.jpg", FIXTURE.read_bytes(), "image/jpeg"),
                "data": (None, json.dumps(payload), "application/json"),
            }
            response = client.post("/reports", files=files)
            assert response.status_code == 200, response.text
            assert _count_reports(crisis_id) == 1
    finally:
        _delete_crisis(crisis_id)


def test_honeypot_empty_string_does_not_trigger() -> None:
    """`message=""` should NOT trigger — it's falsy. Real callers may send
    empty strings for `description`; we want consistent truthy-check
    semantics."""
    crisis_id = _seed_crisis()
    try:
        with TestClient(app) as client:
            payload = {
                "crisis_id": str(crisis_id),
                "damage_class": "minimal",
                "client_id": str(uuid.uuid4()),
                # Satisfies the minimum-content gate (location OR route).
                "location": {"lat": 25.2854, "lng": 51.5310},
                "message": "",
            }
            files = {
                "photo": ("tiny.jpg", FIXTURE.read_bytes(), "image/jpeg"),
                "data": (None, json.dumps(payload), "application/json"),
            }
            response = client.post("/reports", files=files)
            assert response.status_code == 200, response.text
            # The normal path ran — a row was inserted.
            assert _count_reports(crisis_id) == 1
    finally:
        _delete_crisis(crisis_id)
