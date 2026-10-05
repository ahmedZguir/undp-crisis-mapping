"""End-to-end tests for `POST /reports/{report_id}/delete`.

Companion to `test_reports_delete.py` (the bulk wipe). The per-report
variant shares the same heat_cells + photo cleanup helpers; these tests
focus on the soft-auth gate and the always-200 invariant under the
three miss paths (wrong client_id, unknown report_id, internal failure).

Coverage:
  * Happy path — delete one of two reports under the same client_id;
    verify only that row is gone and heat_cells reflects the change.
  * Wrong client_id — caller knows the report_id but not the owning
    client_id; response is 200 same shape and the row stays.
  * Unknown report_id — fresh UUID, no row; response is 200 same shape.
  * Malformed body — empty dict / non-UUID / extra fields → 422.
  * Malformed path — non-UUID `report_id` → 422.
  * Rate limit — 4th call from the same IP within the minute returns 429.
  * Storage-failure tolerance — stubbed deleter raises; DB still deletes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from api.core.config import get_settings
from api.core.h3 import cell_for_location
from api.core.rate_limit import limiter
from api.main import app
from api.reports.deletion import CitizenReportDeletionService
from api.reports.routes import get_report_deletion_service

FIXTURE = Path(__file__).parent.parent / "fixtures" / "tiny.jpg"


pytestmark = pytest.mark.integration


# --- Test infra (crisis seed + row counts) -------------------------------


def _seed_crisis(status: str = "active") -> uuid.UUID:
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
                        "values (:id, :n, :s, :t)"
                    ),
                    {
                        "id": str(crisis_id),
                        "n": f"delete-one test crisis {suffix}",
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


def _report_exists(report_id: uuid.UUID) -> bool:
    settings = get_settings()

    async def _run() -> bool:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.connect() as conn:
                row = (
                    await conn.execute(
                        text("select 1 from public.reports where id = :id"),
                        {"id": str(report_id)},
                    )
                ).first()
                return row is not None
        finally:
            await engine.dispose()

    return asyncio.run(_run())


def _heat_cell_row(crisis_id: uuid.UUID, h3_cell: int) -> dict[str, int] | None:
    settings = get_settings()

    async def _run() -> dict[str, int] | None:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.connect() as conn:
                row = (
                    await conn.execute(
                        text(
                            "select report_count, minimal_count, partial_count, "
                            "complete_count from public.heat_cells "
                            "where crisis_id = :cid and h3_cell = :cell"
                        ),
                        {"cid": str(crisis_id), "cell": h3_cell},
                    )
                ).first()
                if row is None:
                    return None
                return {
                    "report_count": int(row.report_count),
                    "minimal_count": int(row.minimal_count),
                    "partial_count": int(row.partial_count),
                    "complete_count": int(row.complete_count),
                }
        finally:
            await engine.dispose()

    return asyncio.run(_run())


def _submit_report(
    client: TestClient,
    crisis_id: uuid.UUID,
    client_id: uuid.UUID,
    *,
    lat: float,
    lng: float,
    damage_class: str,
) -> uuid.UUID:
    payload = {
        "crisis_id": str(crisis_id),
        "damage_class": damage_class,
        "location": {"lat": lat, "lng": lng},
        "client_id": str(client_id),
    }
    files = {
        "photo": ("tiny.jpg", FIXTURE.read_bytes(), "image/jpeg"),
        "data": (None, json.dumps(payload), "application/json"),
    }
    response = client.post("/reports", files=files)
    assert response.status_code == 200, response.text
    return uuid.UUID(response.json()["id"])


# --- Tests ---------------------------------------------------------------


def test_delete_one_removes_target_and_leaves_sibling() -> None:
    """Two reports under one client_id at distinct cells. Deleting the
    first removes its row and its cell; the second row + its cell are
    untouched."""
    crisis_id = _seed_crisis()
    client_id = uuid.uuid4()
    lat_a, lng_a = 25.30, 51.50
    lat_b, lng_b = 25.40, 51.60
    cell_a = cell_for_location(lat_a, lng_a)
    cell_b = cell_for_location(lat_b, lng_b)
    assert cell_a != cell_b, "test setup: probes must land in different cells"

    try:
        with TestClient(app) as client:
            target = _submit_report(
                client, crisis_id, client_id, lat=lat_a, lng=lng_a, damage_class="minimal"
            )
            sibling = _submit_report(
                client, crisis_id, client_id, lat=lat_b, lng=lng_b, damage_class="complete"
            )

            assert _report_exists(target)
            assert _report_exists(sibling)
            row_a = _heat_cell_row(crisis_id, cell_a)
            assert row_a is not None and row_a["report_count"] == 1
            assert row_a["minimal_count"] == 1
            row_b = _heat_cell_row(crisis_id, cell_b)
            assert row_b is not None and row_b["report_count"] == 1
            assert row_b["complete_count"] == 1

            limiter.reset()
            response = client.post(
                f"/reports/{target}/delete",
                json={"client_id": str(client_id)},
            )
            assert response.status_code == 200, response.text
            assert response.json() == {"status": "ok"}

            assert not _report_exists(target)
            assert _report_exists(sibling)
            assert _heat_cell_row(crisis_id, cell_a) is None
            after_b = _heat_cell_row(crisis_id, cell_b)
            assert after_b is not None and after_b["report_count"] == 1
    finally:
        _delete_crisis(crisis_id)


def test_delete_one_wrong_client_id_is_noop_with_200() -> None:
    """Caller knows the `report_id` but supplies a different `client_id`.
    The DELETE filters on BOTH, so the row stays; the response is the
    standard 200 + `{"status":"ok"}` — same shape as the success path."""
    crisis_id = _seed_crisis()
    owner = uuid.uuid4()
    attacker = uuid.uuid4()
    lat, lng = 25.30, 51.50
    cell = cell_for_location(lat, lng)

    try:
        with TestClient(app) as client:
            target = _submit_report(
                client, crisis_id, owner, lat=lat, lng=lng, damage_class="partial"
            )
            assert _report_exists(target)

            limiter.reset()
            response = client.post(
                f"/reports/{target}/delete",
                json={"client_id": str(attacker)},
            )
            assert response.status_code == 200, response.text
            assert response.json() == {"status": "ok"}

            # Row is still there; heat_cells unchanged.
            assert _report_exists(target)
            row = _heat_cell_row(crisis_id, cell)
            assert row is not None and row["report_count"] == 1
            assert row["partial_count"] == 1
    finally:
        _delete_crisis(crisis_id)


def test_delete_one_unknown_report_id_returns_200_same_shape() -> None:
    """Unknown `report_id` is byte-identical to the wrong-owner and
    success paths — that parity is the privacy property."""
    limiter.reset()
    with TestClient(app) as client:
        response = client.post(
            f"/reports/{uuid.uuid4()}/delete",
            json={"client_id": str(uuid.uuid4())},
        )
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_delete_one_malformed_body_returns_422() -> None:
    """Bad bodies leak no server state — caller-controlled shape errors
    are an acceptable non-200."""
    rid = uuid.uuid4()
    with TestClient(app) as client:
        limiter.reset()
        r1 = client.post(f"/reports/{rid}/delete", json={})
        assert r1.status_code == 422

        limiter.reset()
        r2 = client.post(f"/reports/{rid}/delete", json={"client_id": "not-a-uuid"})
        assert r2.status_code == 422

        limiter.reset()
        r3 = client.post(
            f"/reports/{rid}/delete",
            json={"client_id": str(uuid.uuid4()), "extra": "nope"},
        )
        assert r3.status_code == 422


def test_delete_one_malformed_path_returns_422() -> None:
    """Non-UUID `report_id` is FastAPI coercion territory — 422."""
    limiter.reset()
    with TestClient(app) as client:
        response = client.post(
            "/reports/not-a-uuid/delete",
            json={"client_id": str(uuid.uuid4())},
        )
    assert response.status_code == 422


def test_delete_one_returns_429_after_burst_from_same_ip() -> None:
    """The endpoint is capped at 3/minute per IP. Four calls in a tight
    loop produce three 200s then a 429.

    Opts out of the autouse `_reset_rate_limiter` is NOT needed here —
    we drive the counter explicitly and don't rely on baseline reset
    behaviour."""
    limiter.reset()
    with TestClient(app) as client:
        rid = uuid.uuid4()
        cid = uuid.uuid4()
        codes = [
            client.post(f"/reports/{rid}/delete", json={"client_id": str(cid)}).status_code
            for _ in range(4)
        ]
    assert codes[:3] == [200, 200, 200], codes
    assert codes[3] == 429, codes


def test_delete_one_succeeds_when_storage_call_raises(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A storage outage must not block the DB delete — the row goes,
    the photo orphan is logged at WARNING."""

    class _RaisingDeleter:
        async def delete_photo(self, photo_path: str) -> None:
            raise RuntimeError(f"storage offline (path={photo_path})")

    crisis_id = _seed_crisis()
    client_id = uuid.uuid4()

    try:
        with TestClient(app) as client:
            target = _submit_report(
                client, crisis_id, client_id, lat=25.30, lng=51.50, damage_class="minimal"
            )
            assert _report_exists(target)

            state: Any = app.state.container
            real_service: CitizenReportDeletionService = state.report_deletion_service
            stub_service = CitizenReportDeletionService(
                sessionmaker=real_service._sessionmaker,  # pyright: ignore[reportPrivateUsage]
                storage=_RaisingDeleter(),
            )
            app.dependency_overrides[get_report_deletion_service] = lambda: stub_service
            try:
                limiter.reset()
                with caplog.at_level(logging.WARNING, logger="api.reports.deletion"):
                    response = client.post(
                        f"/reports/{target}/delete",
                        json={"client_id": str(client_id)},
                    )
            finally:
                app.dependency_overrides.pop(get_report_deletion_service, None)

            assert response.status_code == 200
            assert response.json() == {"status": "ok"}
            assert not _report_exists(target)

            warns = [
                r
                for r in caplog.records
                if r.levelno == logging.WARNING and "photo.delete.failed" in r.getMessage()
            ]
            assert warns, (
                f"no photo.delete.failed warning; got {[r.getMessage() for r in caplog.records]}"
            )
    finally:
        _delete_crisis(crisis_id)
