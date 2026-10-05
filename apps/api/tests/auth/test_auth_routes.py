"""Integration tests for `POST /auth/login`, `/auth/refresh`, `/auth/logout`.

The Supabase Auth client is stubbed via `app.dependency_overrides`. The
refresh store is exercised against a real local Redis (so the rotation +
reuse-detection paths are tested end-to-end). Skipped if Redis is
unreachable.
"""

from __future__ import annotations

import dataclasses
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


class _StubSupabaseAuthClient:
    """Stub for the Supabase Auth proxy. Tests configure the desired
    behaviour by mutating the instance attributes.
    """

    def __init__(self) -> None:
        # If set, `password_login` returns this dict. If `None`, raises.
        self.login_response: dict[str, Any] | None = None
        self.login_error: SupabaseAuthError | None = None
        # `mint_access_token(refresh_token)` returns
        # `(access_token, next_refresh_token)`. Tests overwrite the pair to
        # assert what the route forwards. If `mint_response` is `None`, raises.
        self.mint_response: tuple[str, str] | None = ("stub-access-token", "next-sb-refresh")
        self.mint_error: SupabaseAuthError | None = None
        # Records every refresh-token Supabase was asked to exchange — used
        # to assert the route plumbs the stored Supabase refresh in.
        self.mint_calls: list[str] = []

    async def password_login(self, email: str, password: str) -> dict[str, Any]:
        if self.login_error:
            raise self.login_error
        if self.login_response is None:
            raise SupabaseAuthError(400, "no response configured")
        return self.login_response

    async def mint_access_token(self, refresh_token: str) -> tuple[str, str]:
        self.mint_calls.append(refresh_token)
        if self.mint_error:
            raise self.mint_error
        if self.mint_response is None:
            raise SupabaseAuthError(400, "no response configured")
        return self.mint_response

    async def get_user(self, user_id: uuid.UUID) -> dict[str, Any]:
        return {}

    async def aclose(self) -> None:
        return None


@pytest.fixture
def stack() -> Iterator[tuple[TestClient, _StubSupabaseAuthClient]]:
    """Stand up the app with a stubbed Supabase client.

    The real `RefreshTokenStore` is left in place — it's already backed by
    the local Redis, which we have just verified is reachable.
    """
    stub_sb = _StubSupabaseAuthClient()
    with TestClient(app) as client:
        real_state = app.state.container

        def _override() -> AppState:
            return dataclasses.replace(real_state, supabase_auth_client=stub_sb)

        app.dependency_overrides[get_app_state] = _override
        try:
            yield client, stub_sb
        finally:
            app.dependency_overrides.pop(get_app_state, None)


def _coordinator_user() -> dict[str, Any]:
    return {
        "id": "22222222-2222-4222-8222-222222222222",
        "email": "coord@example.com",
        "app_metadata": {"role": "coordinator"},
    }


def test_login_returns_access_token_and_sets_refresh_cookie(
    stack: tuple[TestClient, _StubSupabaseAuthClient],
) -> None:
    client, sb = stack
    sb.login_response = {
        "access_token": "supabase-issued-access-token",
        "user": _coordinator_user(),
    }
    res = client.post("/auth/login", json={"email": "coord@example.com", "password": "secret"})
    assert res.status_code == 200, res.text

    body = res.json()
    assert body["access_token"] == "supabase-issued-access-token"
    assert body["expires_in"] == 900

    # Cookie set with the configured name.
    settings = get_settings()
    cookie_name = settings.auth_refresh_cookie_name
    set_cookie = res.headers.get("set-cookie", "")
    assert cookie_name in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie.lower() or "samesite=lax" in set_cookie.lower()


def test_login_with_invalid_credentials_returns_401(
    stack: tuple[TestClient, _StubSupabaseAuthClient],
) -> None:
    client, sb = stack
    sb.login_error = SupabaseAuthError(400, "invalid login")
    res = client.post("/auth/login", json={"email": "x@y.z", "password": "wrong"})
    assert res.status_code == 401


def test_login_with_non_coordinator_role_returns_403(
    stack: tuple[TestClient, _StubSupabaseAuthClient],
) -> None:
    client, sb = stack
    user = _coordinator_user()
    user["app_metadata"] = {"role": "citizen"}
    sb.login_response = {"access_token": "token", "user": user}
    res = client.post("/auth/login", json={"email": "c@x.y", "password": "x"})
    assert res.status_code == 403


def test_refresh_with_no_cookie_returns_401(
    stack: tuple[TestClient, _StubSupabaseAuthClient],
) -> None:
    client, _ = stack
    res = client.post("/auth/refresh")
    assert res.status_code == 401


def test_refresh_with_unknown_cookie_returns_401(
    stack: tuple[TestClient, _StubSupabaseAuthClient],
) -> None:
    client, _ = stack
    settings = get_settings()
    res = client.post(
        "/auth/refresh",
        cookies={settings.auth_refresh_cookie_name: "garbage-jti"},
    )
    assert res.status_code == 401


def test_full_login_refresh_rotates_token(
    stack: tuple[TestClient, _StubSupabaseAuthClient],
) -> None:
    client, sb = stack
    sb.login_response = {
        "access_token": "first",
        "refresh_token": "sb-refresh-1",
        "user": _coordinator_user(),
    }
    sb.mint_response = ("second", "sb-refresh-2")

    login_res = client.post("/auth/login", json={"email": "c@x.y", "password": "x"})
    assert login_res.status_code == 200
    settings = get_settings()
    first_cookie = login_res.cookies.get(settings.auth_refresh_cookie_name)
    assert first_cookie

    refresh_res = client.post("/auth/refresh")
    assert refresh_res.status_code == 200, refresh_res.text
    assert refresh_res.json()["access_token"] == "second"
    # The route must have handed Supabase the refresh token bound to the
    # jti — not the jti itself and not the user_id.
    assert sb.mint_calls == ["sb-refresh-1"]
    second_cookie = refresh_res.cookies.get(settings.auth_refresh_cookie_name)
    assert second_cookie
    assert second_cookie != first_cookie

    # A second refresh must use the *next* Supabase refresh (the route
    # attached it to the sibling jti after the first rotate).
    sb.mint_response = ("third", "sb-refresh-3")
    second_refresh = client.post("/auth/refresh")
    assert second_refresh.status_code == 200
    assert second_refresh.json()["access_token"] == "third"
    assert sb.mint_calls == ["sb-refresh-1", "sb-refresh-2"]


def test_refresh_with_no_supabase_refresh_returns_401(
    stack: tuple[TestClient, _StubSupabaseAuthClient],
) -> None:
    """If Supabase's login response had no `refresh_token`, /auth/refresh
    has nothing to forward and must 401 rather than 502."""
    client, sb = stack
    # No `refresh_token` in the login response.
    sb.login_response = {"access_token": "first", "user": _coordinator_user()}
    settings = get_settings()
    login = client.post("/auth/login", json={"email": "c@x.y", "password": "x"})
    assert login.status_code == 200

    refresh_res = client.post("/auth/refresh")
    assert refresh_res.status_code == 401
    # And the cookie is cleared.
    assert settings.auth_refresh_cookie_name in refresh_res.headers.get("set-cookie", "")


def test_refresh_when_supabase_rejects_returns_401(
    stack: tuple[TestClient, _StubSupabaseAuthClient],
) -> None:
    """Supabase's `Invalid Refresh Token` 4xx must translate to 401 (the
    user needs to re-login), not 502."""
    client, sb = stack
    sb.login_response = {
        "access_token": "first",
        "refresh_token": "sb-refresh-1",
        "user": _coordinator_user(),
    }
    sb.mint_error = SupabaseAuthError(400, "Invalid Refresh Token")
    client.post("/auth/login", json={"email": "c@x.y", "password": "x"})

    refresh_res = client.post("/auth/refresh")
    assert refresh_res.status_code == 401


def test_refresh_token_reuse_revokes_family(
    stack: tuple[TestClient, _StubSupabaseAuthClient],
) -> None:
    client, sb = stack
    sb.login_response = {
        "access_token": "first",
        "refresh_token": "sb-refresh-1",
        "user": _coordinator_user(),
    }
    sb.mint_response = ("second", "sb-refresh-2")

    settings = get_settings()
    login_res = client.post("/auth/login", json={"email": "c@x.y", "password": "x"})
    first_cookie = login_res.cookies.get(settings.auth_refresh_cookie_name)
    assert first_cookie

    # First refresh — TestClient updates its cookie jar to the new value.
    refresh1 = client.post("/auth/refresh")
    assert refresh1.status_code == 200
    second_cookie = client.cookies.get(settings.auth_refresh_cookie_name)
    assert second_cookie is not None
    assert second_cookie != first_cookie

    # Reuse: present the FIRST cookie again. The TestClient drops the
    # current cookie via `cookies=...` to force-send the old value.
    reuse_res = client.post(
        "/auth/refresh",
        cookies={settings.auth_refresh_cookie_name: first_cookie},
    )
    assert reuse_res.status_code == 401

    # Family is revoked — the second (legitimate) cookie also fails now.
    aftermath = client.post(
        "/auth/refresh",
        cookies={settings.auth_refresh_cookie_name: second_cookie},
    )
    assert aftermath.status_code == 401


def test_logout_revokes_family_and_clears_cookie(
    stack: tuple[TestClient, _StubSupabaseAuthClient],
) -> None:
    client, sb = stack
    sb.login_response = {"access_token": "first", "user": _coordinator_user()}

    settings = get_settings()
    client.post("/auth/login", json={"email": "c@x.y", "password": "x"})
    cookie = client.cookies.get(settings.auth_refresh_cookie_name)
    assert cookie

    logout = client.post("/auth/logout")
    assert logout.status_code == 204

    # Subsequent refresh with the same cookie value fails.
    res = client.post(
        "/auth/refresh",
        cookies={settings.auth_refresh_cookie_name: cookie},
    )
    assert res.status_code == 401


def test_login_cookie_samesite_none_forces_secure(
    stack: tuple[TestClient, _StubSupabaseAuthClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: when an operator switches `AUTH_REFRESH_COOKIE_SAMESITE`
    to `none` for a cross-site deployment, the cookie must also be `Secure`
    — browsers silently drop `SameSite=None` cookies that lack `Secure`,
    which is exactly the "missing refresh cookie" 401 we saw in dev when
    the PWA and API lived on different hosts. The route forces `Secure` on
    when SameSite=None regardless of `AUTH_REFRESH_COOKIE_SECURE`.
    """
    from api.auth import routes as auth_routes

    client, sb = stack
    sb.login_response = {"access_token": "x", "user": _coordinator_user()}

    settings = get_settings()
    monkeypatch.setattr(settings, "auth_refresh_cookie_samesite", "none")
    monkeypatch.setattr(settings, "auth_refresh_cookie_secure", False)

    res = client.post("/auth/login", json={"email": "c@x.y", "password": "x"})
    assert res.status_code == 200
    set_cookie = res.headers.get("set-cookie", "").lower()
    assert "samesite=none" in set_cookie
    # Browsers reject SameSite=None without Secure; the route promotes it.
    assert "secure" in set_cookie
    # And the helper accepts the value path-by-path (sanity).
    assert auth_routes._samesite_value(settings) == "none"  # pyright: ignore[reportPrivateUsage]


def test_login_cookie_rejects_unknown_samesite(
    stack: tuple[TestClient, _StubSupabaseAuthClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Misspelt env vars must fail loud — a silent fallback to Lax would
    mask a deployment misconfiguration in exactly the cross-site scenario
    where it matters."""
    client, sb = stack
    sb.login_response = {"access_token": "x", "user": _coordinator_user()}
    settings = get_settings()
    monkeypatch.setattr(settings, "auth_refresh_cookie_samesite", "strict-ish")

    # `RuntimeError` propagates out of the route under `raise_server_exceptions
    # =True` (TestClient default) — what matters is that the cookie does NOT
    # get set silently with the bogus value.
    with pytest.raises(RuntimeError, match="AUTH_REFRESH_COOKIE_SAMESITE"):
        client.post("/auth/login", json={"email": "c@x.y", "password": "x"})
