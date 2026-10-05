"""Health-endpoint contract tests.

Locks in the load-bearing claims:

1. `/healthz` is dependency-free — it returns 200 even when the DB or
   Redis singletons are broken. Liveness must not cascade dependency
   failures into "restart the container".
2. `/readyz` fails closed on broken deps. The failure body shape is
   load-bearing for operators: `{"status":"not_ready","failures":[…]}`
   with one entry per failing dep, each `"<dep>:<ExcType>"`.
3. `/readyz` returns 200 `{"status":"ready"}` when both deps are healthy.

The Redis case is exercised by swapping `state.redis_client` for a stub
that raises on `ping()`. The DB case is exercised by swapping
`state.engine` for a stub whose `.connect()` raises. Both go through the
`dependency_overrides[get_app_state]` seam the rest of the suite already
uses.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from api.core.app_state import AppState, get_app_state
from api.main import app


class _BrokenEngine:
    """Stub engine whose `.connect()` raises immediately.

    Mirrors the shape `api.core.health.readyz` consumes — a callable
    `connect()` returning an async context manager. Raising synchronously
    on call is enough to drive the `db:<ExcType>` failure path.
    """

    def connect(self) -> object:  # pragma: no cover — error path
        raise ConnectionError("simulated db outage")


class _BrokenRedis:
    """Stub Redis client whose `ping()` raises immediately."""

    async def ping(self) -> bool:  # pragma: no cover — error path
        raise ConnectionError("simulated redis outage")


@pytest.fixture
def client() -> Iterator[TestClient]:
    """A TestClient that has run the real lifespan (real engine + real
    Redis). Individual tests install per-call overrides on top.
    """
    with TestClient(app) as c:
        yield c


def test_healthz_returns_200_even_with_broken_db(client: TestClient) -> None:
    """Load-bearing: liveness must not cascade dependency failures.

    Even with the DB dependency overridden to raise, `/healthz` must
    return 200. The handler does no I/O so the override is in fact a
    no-op for it — that *is* the property under test.
    """
    real_state: AppState = app.state.container
    broken_state = dataclasses.replace(
        real_state,
        engine=_BrokenEngine(),  # type: ignore[arg-type]
        redis_client=_BrokenRedis(),
    )
    app.dependency_overrides[get_app_state] = lambda: broken_state
    try:
        res = client.get("/healthz")
        assert res.status_code == 200, res.text
        assert res.json() == {"status": "ok"}
    finally:
        app.dependency_overrides.pop(get_app_state, None)


def test_health_alias_returns_200(client: TestClient) -> None:
    """The `/health` backward-compat alias hands off to the same handler.

    Documented in `api.core.health`; this guards the alias against silent
    removal.
    """
    res = client.get("/health")
    assert res.status_code == 200, res.text
    assert res.json() == {"status": "ok"}


def test_readyz_returns_503_when_db_unreachable(client: TestClient) -> None:
    real_state: AppState = app.state.container
    broken_state = dataclasses.replace(
        real_state,
        engine=_BrokenEngine(),  # type: ignore[arg-type]
    )
    app.dependency_overrides[get_app_state] = lambda: broken_state
    try:
        res = client.get("/readyz")
        assert res.status_code == 503, res.text
        body = res.json()
        assert body["status"] == "not_ready"
        # Failure entries are `"<dep>:<ExcType>"`. We only assert the dep
        # prefix because the exact exception type may vary by engine
        # internals (the property under test is "the failure is attributed
        # to the DB").
        assert any(item.startswith("db:") for item in body["failures"]), body
    finally:
        app.dependency_overrides.pop(get_app_state, None)


def test_readyz_returns_503_when_redis_unreachable(client: TestClient) -> None:
    real_state: AppState = app.state.container
    broken_state = dataclasses.replace(
        real_state,
        redis_client=_BrokenRedis(),
    )
    app.dependency_overrides[get_app_state] = lambda: broken_state
    try:
        res = client.get("/readyz")
        assert res.status_code == 503, res.text
        body = res.json()
        assert body["status"] == "not_ready"
        assert any(item.startswith("redis:") for item in body["failures"]), body
    finally:
        app.dependency_overrides.pop(get_app_state, None)


def test_readyz_returns_200_when_both_healthy(client: TestClient) -> None:
    """With no overrides, the real lifespan-built state is used.

    Assumes the local Supabase stack + Redis are up — the same assumption
    the rest of the integration suite makes. If `/readyz` fails here the
    failure body's `failures` list will point at the broken dep.
    """
    res = client.get("/readyz")
    assert res.status_code == 200, res.text
    assert res.json() == {"status": "ready"}
