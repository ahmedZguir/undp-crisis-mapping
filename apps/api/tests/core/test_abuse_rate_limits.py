"""Rate-limit regression tests for the slowapi layer.

These tests intentionally do NOT use the autouse `_reset_rate_limiter`
fixture (they opt out by filename, see `conftest.py`) so they can drive
a burst and watch the cap fire. Each test calls `limiter.reset()` at the
top to start from a clean counter, then sends N+1 requests where N is
the per-endpoint cap.

The slowapi storage is the same Redis the production wiring uses (the
`storage_uri` is read off `settings.redis_dsn` at import time). For
hermetic tests, `limiter.reset()` clears the counters; Redis is shared
with the worker but the keys are unambiguous (`LIMITER/...`).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.core.rate_limit import limiter
from api.main import app

FIXTURE = Path(__file__).parent.parent / "fixtures" / "tiny.jpg"


pytestmark = [pytest.mark.redis, pytest.mark.real_rate_limit]


@pytest.fixture
def client() -> Iterator[TestClient]:
    """Fresh limiter counter + a TestClient with the real wired-in app."""
    limiter.reset()
    with TestClient(app) as c:
        yield c
    # Belt-and-braces: another reset on the way out so the next test in a
    # different file doesn't inherit a cap-near-saturation counter.
    limiter.reset()


def test_get_crises_returns_429_after_60_per_minute(client: TestClient) -> None:
    """`GET /crises` is capped at 60/minute keyed by IP (the TestClient
    presents as a single IP). The 61st request in a one-minute burst is
    expected to 429."""
    seen_status: list[int] = []
    for _ in range(65):
        r = client.get("/crises")
        seen_status.append(r.status_code)
        if r.status_code == 429:
            break

    assert 429 in seen_status, f"expected a 429 in a 65-call burst; got {seen_status}"
    # The first call must succeed (the limiter only fires after the cap).
    assert seen_status[0] == 200
    # And at least 50 of the early calls must be 200s — that proves the cap
    # is actually 60ish, not "off" or "1".
    assert sum(1 for s in seen_status if s == 200) >= 50


@pytest.mark.integration
def test_post_reports_returns_429_after_5_per_minute(client: TestClient) -> None:
    """`POST /reports` is capped at 5/minute. The 6th request in a one-minute
    burst with the same `X-Client-Id` is expected to 429.

    We don't seed a crisis — the burst exits with 429 before the route
    needs a valid one (rate-limit middleware runs before the handler).
    The early requests in the burst will 4xx for other reasons (e.g.
    409 for unknown crisis), but the SIXTH must be 429.
    """
    client_id_header = {"X-Client-Id": str(uuid.uuid4())}
    payload = {
        "crisis_id": str(uuid.uuid4()),  # bogus — won't matter
        "damage_class": "minimal",
    }

    statuses: list[int] = []
    for _ in range(7):
        files = {
            "photo": ("tiny.jpg", FIXTURE.read_bytes(), "image/jpeg"),
            "data": (None, json.dumps(payload), "application/json"),
        }
        r = client.post("/reports", files=files, headers=client_id_header)
        statuses.append(r.status_code)

    # Some early calls may 4xx (unknown crisis = 409, or rate-limit hits the
    # in-flight ones too). The contract is: by call #6, we see 429.
    assert 429 in statuses, f"expected a 429 in a 7-call burst; got {statuses}"
    # Last call in the burst must be 429 (we're well past the 5/minute cap).
    assert statuses[-1] == 429


def test_post_auth_login_returns_429_after_10_per_minute(client: TestClient) -> None:
    """`POST /auth/login` is capped at 10/minute. The 11th call in a
    one-minute burst is expected to 429 regardless of credentials."""
    statuses: list[int] = []
    for _ in range(12):
        r = client.post(
            "/auth/login",
            json={"email": "test@example.com", "password": "wrong"},
        )
        statuses.append(r.status_code)
        if r.status_code == 429:
            break

    assert 429 in statuses, f"expected a 429 in a 12-call burst; got {statuses}"
    # At least the first call must NOT be 429 — that would mean the limiter
    # started in a saturated state.
    assert statuses[0] != 429
