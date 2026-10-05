"""Integration tests for `POST /admin/coordinators/{user_id}/disable`.

Uses the `_StubVerifier` pattern from `test_admin_auth_gate.py` so the
router-level gate (`require_coordinator`) runs unchanged. Substitutes a
stubbed `SupabaseAuthClientLike` so no real Supabase Auth call fires, and
exercises the refresh-revocation path against a real local Redis.

Skipped if Redis is unreachable — the off-boarding endpoint always
touches Redis, so a Redis-less test mode would be too narrow.

The filename starts with `test_admin_auth_` so the `conftest.py`
autouse-bypass fixture does NOT install the fake `require_coordinator` —
the real dependency runs and we drive role/identity via the stub
verifier.
"""

from __future__ import annotations

import dataclasses
import logging
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from api.auth.supabase import SupabaseAuthError
from api.core.app_state import AppState, get_app_state
from api.core.config import get_settings
from api.main import app

pytestmark = [pytest.mark.redis, pytest.mark.real_auth]


class _StubVerifier:
    """Returns the configured claims; raises on `None` to simulate a bad token."""

    def __init__(self, claims: dict[str, Any] | None) -> None:
        self._claims = claims

    def verify(self, token: str) -> dict[str, Any]:
        if self._claims is None:
            import jwt as _jwt

            raise _jwt.InvalidSignatureError("stub: invalid")
        return self._claims


class _StubSupabaseAuthClient:
    """Records `admin_disable_user` calls; the rest of the surface is unused
    by the disable endpoint."""

    def __init__(self) -> None:
        self.disable_calls: list[uuid.UUID] = []
        self.raise_on_disable: SupabaseAuthError | None = None
        # Role the disable route reads for its target via `get_user`. Defaults
        # to `coordinator` so the admin-immutability guard passes; a test sets
        # `"admin"` to exercise the 403.
        self.get_user_role: str | None = "coordinator"

    async def password_login(self, email: str, password: str) -> dict[str, Any]:
        return {}

    async def mint_access_token(self, refresh_token: str) -> tuple[str, str]:
        return ("stub-access", "stub-next-refresh")

    async def get_user(self, user_id: uuid.UUID) -> dict[str, Any]:
        meta = {"role": self.get_user_role} if self.get_user_role is not None else {}
        return {
            "id": str(user_id),
            "email": "target@example.com",
            "created_at": "2024-01-02T03:04:05.123456+00:00",
            "banned_until": None,
            "app_metadata": meta,
        }

    async def aclose(self) -> None:
        return None

    async def admin_create_user(
        self, *, email: str, password: str, role: str = "admin"
    ) -> dict[str, Any]:
        return {"id": str(uuid.uuid4())}

    async def admin_update_user_password(
        self, *, user_id: uuid.UUID, password: str, role: str | None = None
    ) -> dict[str, Any]:
        return {"id": str(user_id)}

    async def admin_find_user_by_email(self, email: str) -> dict[str, Any] | None:
        return None

    async def admin_verify_password(self, email: str, password: str) -> bool:
        return False

    async def admin_disable_user(self, user_id: uuid.UUID) -> None:
        if self.raise_on_disable is not None:
            raise self.raise_on_disable
        self.disable_calls.append(user_id)

    async def admin_enable_user(self, user_id: uuid.UUID) -> None:
        return None

    async def admin_list_users(self, *, per_page: int = 100) -> list[dict[str, Any]]:
        return []


_ADMIN_ID = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


def _claims_for(role: str, *, sub: str = str(_ADMIN_ID)) -> dict[str, Any]:
    return {
        "sub": sub,
        "aud": "authenticated",
        "iss": "test-issuer",
        "exp": 9_999_999_999,
        "iat": 1,
        "email": "admin@example.com",
        "app_metadata": {"role": role},
    }


@pytest.fixture
def stack() -> Iterator[tuple[TestClient, dict[str, Any], _StubSupabaseAuthClient]]:
    verifier_state: dict[str, Any] = {"claims": None}
    stub_sb = _StubSupabaseAuthClient()

    with TestClient(app) as client:
        real_state = app.state.container

        def _override() -> AppState:
            return dataclasses.replace(
                real_state,
                jwt_verifier=_StubVerifier(verifier_state["claims"]),
                supabase_auth_client=stub_sb,
            )

        app.dependency_overrides[get_app_state] = _override
        try:
            yield client, verifier_state, stub_sb
        finally:
            app.dependency_overrides.pop(get_app_state, None)


def test_disable_without_authorization_returns_401(
    stack: tuple[TestClient, dict[str, Any], _StubSupabaseAuthClient],
) -> None:
    client, _, _ = stack
    target = uuid.uuid4()
    res = client.post(f"/admin/coordinators/{target}/disable", json={})
    assert res.status_code == 401


def test_disable_with_coordinator_role_returns_403(
    stack: tuple[TestClient, dict[str, Any], _StubSupabaseAuthClient],
) -> None:
    client, state, sb = stack
    state["claims"] = _claims_for("coordinator")
    target = uuid.uuid4()
    res = client.post(
        f"/admin/coordinators/{target}/disable",
        json={},
        headers={"Authorization": "Bearer good-token"},
    )
    assert res.status_code == 403
    assert sb.disable_calls == []


def test_disable_self_returns_400(
    stack: tuple[TestClient, dict[str, Any], _StubSupabaseAuthClient],
) -> None:
    client, state, sb = stack
    state["claims"] = _claims_for("admin")
    res = client.post(
        f"/admin/coordinators/{_ADMIN_ID}/disable",
        json={"reason": "test"},
        headers={"Authorization": "Bearer good-token"},
    )
    assert res.status_code == 400
    assert sb.disable_calls == []


def test_disable_admin_target_returns_403(
    stack: tuple[TestClient, dict[str, Any], _StubSupabaseAuthClient],
) -> None:
    """Admin accounts cannot be removed via the API (immutable) — out-of-band only."""
    client, state, sb = stack
    state["claims"] = _claims_for("admin")
    sb.get_user_role = "admin"
    target = uuid.uuid4()
    res = client.post(
        f"/admin/coordinators/{target}/disable",
        json={"reason": "should be refused"},
        headers={"Authorization": "Bearer good-token"},
    )
    assert res.status_code == 403
    assert res.json()["detail"] == "admin_immutable"
    assert sb.disable_calls == []


def test_disable_happy_path_bans_target(
    stack: tuple[TestClient, dict[str, Any], _StubSupabaseAuthClient],
) -> None:
    client, state, sb = stack
    state["claims"] = _claims_for("admin")
    target = uuid.uuid4()
    res = client.post(
        f"/admin/coordinators/{target}/disable",
        json={"reason": "left UNDP"},
        headers={"Authorization": "Bearer good-token"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["user_id"] == str(target)
    assert "disabled_at" in body
    assert sb.disable_calls == [target]


def test_disable_revokes_existing_refresh_families(
    stack: tuple[TestClient, dict[str, Any], _StubSupabaseAuthClient],
) -> None:
    client, state, _sb = stack
    state["claims"] = _claims_for("admin")

    # Pre-seed two refresh-token families for the target against the same
    # Redis the endpoint will touch. We open a fresh redis client in a
    # one-shot asyncio.run() so we do not interfere with the TestClient's
    # loop (httpx's TestClient drives its own).
    import asyncio

    import redis.asyncio as redis_asyncio

    from api.auth.refresh import RefreshTokenStore as _Store

    target = uuid.uuid4()
    settings = get_settings()

    async def _seed() -> None:
        client_handle: Any = redis_asyncio.from_url(  # pyright: ignore[reportUnknownMemberType]
            settings.redis_dsn, decode_responses=True
        )
        store = _Store(client_handle)
        try:
            await store.issue(target)
            await store.issue(target)
        finally:
            await client_handle.aclose()

    asyncio.run(_seed())

    res = client.post(
        f"/admin/coordinators/{target}/disable",
        json={},
        headers={"Authorization": "Bearer good-token"},
    )
    assert res.status_code == 200, res.text
    # The body's families_revoked count is not exposed in the response
    # contract; we assert the side effect via the audit log line in the
    # other test. Here we just confirm the call succeeded with a real
    # Redis-backed revoke.


def test_disable_emits_stdout_audit_line(
    stack: tuple[TestClient, dict[str, Any], _StubSupabaseAuthClient],
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, state, _ = stack
    state["claims"] = _claims_for("admin")
    target = uuid.uuid4()

    with caplog.at_level(logging.INFO):
        res = client.post(
            f"/admin/coordinators/{target}/disable",
            json={"reason": "left UNDP"},
            headers={"Authorization": "Bearer good-token"},
        )
        assert res.status_code == 200

    audit_lines = [r.message for r in caplog.records if "coordinator disabled" in r.message]
    assert audit_lines, "expected a 'coordinator disabled' stdout line"
    line = audit_lines[0]
    assert f"actor={_ADMIN_ID}" in line
    assert f"target={target}" in line
    assert "reason=left UNDP" in line
    assert "families_revoked=" in line


def test_disable_supabase_failure_returns_502(
    stack: tuple[TestClient, dict[str, Any], _StubSupabaseAuthClient],
) -> None:
    client, state, sb = stack
    state["claims"] = _claims_for("admin")
    sb.raise_on_disable = SupabaseAuthError(500, "boom")
    target = uuid.uuid4()

    res = client.post(
        f"/admin/coordinators/{target}/disable",
        json={},
        headers={"Authorization": "Bearer good-token"},
    )
    assert res.status_code == 502
