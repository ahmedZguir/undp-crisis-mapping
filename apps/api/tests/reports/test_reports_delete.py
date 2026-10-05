"""End-to-end tests for `POST /reports/delete`.

Coverage:
  * Happy path — submit N reports, delete by client_id, verify rows are gone
    and `heat_cells` is decremented (cells with 0 contribution removed).
  * No-match — fresh UUID, no reports, returns 200 with exact body shape.
  * Malformed body — empty dict, non-UUID, extra fields → 422.
  * Rate limit — second call from the same IP returns 429.
  * Response-shape invariant — body is EXACTLY `{"status": "ok"}` for both
    match and no-match.
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
                        "n": f"delete test crisis {suffix}",
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
                # Reports first (FK on heat_cells via crisis_id cascades, but
                # reports do not — delete those before the crisis row).
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


def _count_reports_by_client(client_id: uuid.UUID) -> int:
    settings = get_settings()

    async def _run() -> int:
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.connect() as conn:
                count = (
                    await conn.execute(
                        text("select count(*) from public.reports where client_id = :id"),
                        {"id": str(client_id)},
                    )
                ).scalar_one()
                return int(count) if count is not None else 0
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


def test_delete_by_client_removes_reports_and_decrements_heat_cells() -> None:
    """Submit three reports under one client_id, two at the same H3 cell and
    one at a different cell. After delete, the rows are gone, the
    two-report cell is removed (count → 0), and the one-report cell is
    also removed (count → 0). Per-class counters drop accordingly."""
    crisis_id = _seed_crisis()
    client_id = uuid.uuid4()
    # Two probes ~10m apart should land in the same res-9 cell; the
    # second pair of coords intentionally sits in a separate cell.
    lat_a, lng_a = 25.30, 51.50
    lat_b, lng_b = 25.40, 51.60
    cell_a = cell_for_location(lat_a, lng_a)
    cell_b = cell_for_location(lat_b, lng_b)
    assert cell_a != cell_b, "test setup mistake: coords must land in different cells"

    try:
        with TestClient(app) as client:
            _submit_report(
                client,
                crisis_id,
                client_id,
                lat=lat_a,
                lng=lng_a,
                damage_class="minimal",
            )
            _submit_report(
                client,
                crisis_id,
                client_id,
                lat=lat_a,
                lng=lng_a,
                damage_class="partial",
            )
            _submit_report(
                client,
                crisis_id,
                client_id,
                lat=lat_b,
                lng=lng_b,
                damage_class="complete",
            )

            # Sanity: rows are there and cells are populated.
            assert _count_reports_by_client(client_id) == 3
            row_a = _heat_cell_row(crisis_id, cell_a)
            assert row_a is not None and row_a["report_count"] == 2
            assert row_a["minimal_count"] == 1
            assert row_a["partial_count"] == 1
            row_b = _heat_cell_row(crisis_id, cell_b)
            assert row_b is not None and row_b["report_count"] == 1
            assert row_b["complete_count"] == 1

            # Reset rate-limit counter so this test doesn't 429 on its
            # single delete call. The autouse `_reset_rate_limiter`
            # fixture already does this between tests; the explicit
            # reset here is belt-and-braces in case the previous test in
            # the suite ran a /reports/delete burst.
            limiter.reset()

            response = client.post(
                "/reports/delete",
                json={"client_id": str(client_id)},
            )
            assert response.status_code == 200, response.text
            assert response.json() == {"status": "ok"}

            # Rows are gone.
            assert _count_reports_by_client(client_id) == 0

            # Both cells dropped to 0 contribution and were cleaned up.
            assert _heat_cell_row(crisis_id, cell_a) is None
            assert _heat_cell_row(crisis_id, cell_b) is None
    finally:
        _delete_crisis(crisis_id)


def test_delete_decrements_without_removing_when_other_clients_remain() -> None:
    """Two clients submit at the same cell. Deleting one decrements but
    leaves the cell standing with the other client's contribution."""
    crisis_id = _seed_crisis()
    alice = uuid.uuid4()
    bob = uuid.uuid4()
    lat, lng = 25.30, 51.50
    cell = cell_for_location(lat, lng)

    try:
        with TestClient(app) as client:
            _submit_report(client, crisis_id, alice, lat=lat, lng=lng, damage_class="minimal")
            _submit_report(client, crisis_id, alice, lat=lat, lng=lng, damage_class="partial")
            _submit_report(client, crisis_id, bob, lat=lat, lng=lng, damage_class="complete")

            row = _heat_cell_row(crisis_id, cell)
            assert row is not None and row["report_count"] == 3

            limiter.reset()
            response = client.post("/reports/delete", json={"client_id": str(alice)})
            assert response.status_code == 200

            # Alice's rows are gone; Bob's row remains.
            assert _count_reports_by_client(alice) == 0
            assert _count_reports_by_client(bob) == 1

            # Cell still exists with Bob's contribution only.
            after = _heat_cell_row(crisis_id, cell)
            assert after is not None
            assert after["report_count"] == 1
            assert after["minimal_count"] == 0
            assert after["partial_count"] == 0
            assert after["complete_count"] == 1
    finally:
        _delete_crisis(crisis_id)


def test_delete_with_no_matching_client_returns_200_same_shape() -> None:
    """A fresh UUID with zero reports gets the same response as a match.
    This is the always-200 oracle-protection invariant."""
    limiter.reset()
    with TestClient(app) as client:
        client_id = uuid.uuid4()
        response = client.post("/reports/delete", json={"client_id": str(client_id)})
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_delete_malformed_body_returns_422() -> None:
    """Bad bodies are the one acceptable non-200: a malformed shape is
    caller-controlled, not server-state-dependent, so leaking it does
    not give an attacker an oracle."""
    limiter.reset()
    with TestClient(app) as client:
        # Empty body — `client_id` missing.
        r1 = client.post("/reports/delete", json={})
        assert r1.status_code == 422

        # Reset between requests so the rate-limit doesn't 429 these.
        limiter.reset()
        r2 = client.post("/reports/delete", json={"client_id": "not-a-uuid"})
        assert r2.status_code == 422

        # Extra fields rejected by `extra='forbid'` on the schema.
        limiter.reset()
        r3 = client.post(
            "/reports/delete",
            json={"client_id": str(uuid.uuid4()), "extra": "nope"},
        )
        assert r3.status_code == 422


def test_delete_response_body_is_exactly_status_ok_for_match() -> None:
    """Exact-equality body check on a match. Guards against accidentally
    adding `deleted_count` or any other distinguishing field — the
    privacy property depends on byte-identical responses."""
    crisis_id = _seed_crisis()
    client_id = uuid.uuid4()
    try:
        with TestClient(app) as client:
            _submit_report(
                client,
                crisis_id,
                client_id,
                lat=25.30,
                lng=51.50,
                damage_class="minimal",
            )

            limiter.reset()
            response = client.post("/reports/delete", json={"client_id": str(client_id)})
            assert response.status_code == 200
            # Exact equality — keys AND values, no extras.
            assert response.json() == {"status": "ok"}
    finally:
        _delete_crisis(crisis_id)


def test_delete_response_body_is_exactly_status_ok_for_no_match() -> None:
    """Companion to the match test — verifies the no-match path produces
    the same exact body. The privacy property is the parity between
    these two tests."""
    limiter.reset()
    with TestClient(app) as client:
        response = client.post("/reports/delete", json={"client_id": str(uuid.uuid4())})
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_delete_returns_429_after_second_call_from_same_ip() -> None:
    """The endpoint is capped at 1/10minutes per IP. The TestClient
    presents as a single IP (`testclient`), so the second call in a
    quick succession must 429.

    Opts out of the autouse `_reset_rate_limiter` by file name (see
    `conftest.py`); we drive the counter explicitly."""
    limiter.reset()
    with TestClient(app) as client:
        r1 = client.post("/reports/delete", json={"client_id": str(uuid.uuid4())})
        r2 = client.post("/reports/delete", json={"client_id": str(uuid.uuid4())})

    assert r1.status_code == 200, r1.text
    assert r2.status_code == 429, r2.text


def test_delete_succeeds_when_storage_call_raises(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A storage outage MUST NOT block the DB delete from succeeding.
    Stub the deleter to raise; assert rows are gone, response is 200
    with the standard shape, and a warning was logged."""

    class _RaisingDeleter:
        async def delete_photo(self, photo_path: str) -> None:
            raise RuntimeError(f"storage offline (path={photo_path})")

    crisis_id = _seed_crisis()
    client_id = uuid.uuid4()

    try:
        with TestClient(app) as client:
            _submit_report(
                client,
                crisis_id,
                client_id,
                lat=25.30,
                lng=51.50,
                damage_class="minimal",
            )
            assert _count_reports_by_client(client_id) == 1

            # Override just the deletion service with one wired to the
            # raising stub. The sessionmaker comes from the live
            # AppState so the DB delete still happens against the real
            # Supabase Postgres.
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
                        "/reports/delete",
                        json={"client_id": str(client_id)},
                    )
            finally:
                app.dependency_overrides.pop(get_report_deletion_service, None)

            assert response.status_code == 200
            assert response.json() == {"status": "ok"}
            # DB delete stuck despite the storage failure.
            assert _count_reports_by_client(client_id) == 0

            # Warning was logged.
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
