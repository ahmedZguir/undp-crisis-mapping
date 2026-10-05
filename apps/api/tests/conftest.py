"""Shared pytest fixtures and markers.

Markers:
- `integration`: needs the local Supabase stack; skipped when unreachable.
- `redis`: needs local Redis; skipped when unreachable.
- `real_auth`: keep the real `require_coordinator` gate (no admin bypass).
- `real_rate_limit`: keep slowapi counters between requests.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from collections.abc import Iterator
from typing import Any, cast

import httpx
import pytest
import redis

from api.auth.dependency import require_coordinator
from api.auth.models import Coordinator
from api.core.config import get_settings
from api.core.rate_limit import limiter
from api.main import app


def _supabase_reachable() -> bool:
    try:
        httpx.get(f"{get_settings().supabase_url}/storage/v1/bucket", timeout=2.0)
    except httpx.HTTPError:
        return False
    return True


def _redis_reachable() -> bool:
    try:
        # redis-py is poorly typed upstream, hence Any.
        client: Any = redis.Redis.from_url(  # pyright: ignore[reportUnknownMemberType]
            get_settings().redis_dsn, socket_connect_timeout=2
        )
        client.ping()
    except Exception:
        return False
    return True


_SERVICE_MARKERS = {
    "integration": (
        _supabase_reachable,
        "local Supabase stack is not reachable; run `supabase start`",
    ),
    "redis": (_redis_reachable, "local Redis is not reachable; start the stack"),
}


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "integration: needs the local Supabase stack")
    config.addinivalue_line("markers", "redis: needs local Redis")
    config.addinivalue_line("markers", "real_auth: keep the real require_coordinator gate")
    config.addinivalue_line("markers", "real_rate_limit: keep rate-limit counters between tests")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Probe each service once, and only if some collected test needs it."""
    for marker, (probe, reason) in _SERVICE_MARKERS.items():
        marked = [item for item in items if item.get_closest_marker(marker)]
        if marked and not probe():
            for item in marked:
                item.add_marker(pytest.mark.skip(reason=reason))


def _marked(request: pytest.FixtureRequest, name: str) -> bool:
    # `request.node` is untyped in upstream pytest stubs.
    node: pytest.Item = cast(Any, request).node
    return node.get_closest_marker(name) is not None


def _bypass_coordinator() -> Coordinator:
    return Coordinator(
        id=uuid.UUID("00000000-0000-4000-8000-000000000001"),
        email="test-bypass@example.com",
        role="admin",
    )


@pytest.fixture(autouse=True)
def _capture_api_logs(  # pyright: ignore[reportUnusedFunction]
    caplog: pytest.LogCaptureFixture,
) -> Iterator[None]:
    """Let `caplog` capture `api.*` log records.

    `api/main.py` configures the `api` logger at import time with its own
    stderr handler and `propagate = False` (so the app does not double-emit
    via a root handler in production). `caplog` installs its capture handler
    on the *root* logger and relies on records propagating up to root, so
    with propagation off no `api.*` record ever reaches it and
    `caplog.records` stays empty even though the line is emitted.

    Attaching caplog's handler directly to the `api` logger for the test's
    duration restores capture without touching the production logging
    topology (we do not re-enable `propagate`). The handler is re-read each
    phase because pytest installs a fresh `LogCaptureHandler` per phase.
    """
    api_logger = logging.getLogger("api")
    handler = caplog.handler
    api_logger.addHandler(handler)
    try:
        yield
    finally:
        api_logger.removeHandler(handler)


@pytest.fixture(autouse=True)
def _bypass_admin_auth(  # pyright: ignore[reportUnusedFunction]
    request: pytest.FixtureRequest,
) -> Iterator[None]:
    """Bypass `require_coordinator` unless the test is marked `real_auth`."""
    if _marked(request, "real_auth"):
        yield
        return

    app.dependency_overrides[require_coordinator] = _bypass_coordinator
    try:
        yield
    finally:
        app.dependency_overrides.pop(require_coordinator, None)


@pytest.fixture(autouse=True)
def _reset_rate_limiter(  # pyright: ignore[reportUnusedFunction]
    request: pytest.FixtureRequest,
) -> Iterator[None]:
    """Wipe the slowapi storage between tests.

    All tests share the TestClient host (`ip:testclient`), and `Limiter`
    persists counters across requests until the window rolls over. Without a
    reset, the second `POST /reports` in the suite would 429 because the 5-per
    -minute cap is global to the test client's IP.

    Tests marked `real_rate_limit` skip the reset so they can drive bursts.
    """
    if _marked(request, "real_rate_limit"):
        yield
        return

    # `reset()` can hit Redis; suppress any transport hiccups so a flaky
    # storage doesn't cascade into every test failing on setup.
    with contextlib.suppress(Exception):  # pragma: no cover — defensive
        limiter.reset()
    yield
