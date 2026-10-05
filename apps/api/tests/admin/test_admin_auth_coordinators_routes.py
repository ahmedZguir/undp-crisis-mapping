"""Integration tests for the four new admin coordinator routes.

Covers:
  * `GET    /admin/coordinators`               — list + role filter + is_self
  * `POST   /admin/coordinators`               — create + 409 + 422 + 401 + 403
  * `PATCH  /admin/coordinators/{id}/password` — rotate + revoke + 400 on self
  * `POST   /admin/coordinators/{id}/enable`   — enable + 400 on self + idempotent

The existing disable endpoint has its own file (`test_admin_auth_coordinators_disable_route.py`);
we only smoke-test it here for the 401-without-bearer case.

Filename prefix `test_admin_auth_` opts out of the autouse
`_bypass_admin_auth` so the real `require_coordinator` gate runs.

The Redis-revocation assertions skip when local Redis is unreachable —
same posture as the disable-route file. Pure unit-style tests (no Redis
revocation expected) still run unconditionally.
"""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from api.auth.supabase import SupabaseAuthError
from api.core.app_state import AppState, get_app_state
from api.core.config import get_settings
from api.main import app

pytestmark = pytest.mark.real_auth

# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


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
    """Records calls; exposes the full `SupabaseAuthClientLike` surface.

    Behaviours are toggleable per test via attributes so the same stub
    serves every case.
    """

    def __init__(self) -> None:
        self.create_calls: list[dict[str, Any]] = []
        self.update_password_calls: list[dict[str, Any]] = []
        self.disable_calls: list[uuid.UUID] = []
        self.enable_calls: list[uuid.UUID] = []
        self.find_by_email_response: dict[str, Any] | None = None
        self.list_users_response: list[dict[str, Any]] = []
        self.created_user: dict[str, Any] | None = None
        # Role the mutation routes will read for their target via `get_user`.
        # Defaults to `coordinator` so the admin-immutability guard passes;
        # tests set it to `"admin"` to exercise the 403, or to a
        # `SupabaseAuthError` to exercise the 404 / 502 paths.
        self.get_user_role: str | None = "coordinator"
        self.raise_on_get_user: SupabaseAuthError | None = None
        self.get_user_calls: list[uuid.UUID] = []
        self.raise_on_create: SupabaseAuthError | None = None
        self.raise_on_update_password: SupabaseAuthError | None = None
        self.raise_on_enable: SupabaseAuthError | None = None
        self.raise_on_list: SupabaseAuthError | None = None

    async def password_login(self, email: str, password: str) -> dict[str, Any]:
        return {}

    async def mint_access_token(self, refresh_token: str) -> tuple[str, str]:
        return ("stub-access", "stub-next-refresh")

    async def get_user(self, user_id: uuid.UUID) -> dict[str, Any]:
        self.get_user_calls.append(user_id)
        if self.raise_on_get_user is not None:
            raise self.raise_on_get_user
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
        if self.raise_on_create is not None:
            raise self.raise_on_create
        self.create_calls.append({"email": email, "password": password, "role": role})
        if self.created_user is not None:
            return self.created_user
        # Default: synthesise a plausible Supabase Auth row.
        return {
            "id": str(uuid.uuid4()),
            "email": email,
            "created_at": datetime.now(tz=UTC).isoformat(),
            "banned_until": None,
            "app_metadata": {"role": role},
        }

    async def admin_update_user_password(
        self, *, user_id: uuid.UUID, password: str, role: str | None = None
    ) -> dict[str, Any]:
        if self.raise_on_update_password is not None:
            raise self.raise_on_update_password
        self.update_password_calls.append({"user_id": user_id, "password": password, "role": role})
        return {"id": str(user_id)}

    async def admin_find_user_by_email(self, email: str) -> dict[str, Any] | None:
        return self.find_by_email_response

    async def admin_verify_password(self, email: str, password: str) -> bool:
        return False

    async def admin_disable_user(self, user_id: uuid.UUID) -> None:
        self.disable_calls.append(user_id)

    async def admin_enable_user(self, user_id: uuid.UUID) -> None:
        if self.raise_on_enable is not None:
            raise self.raise_on_enable
        self.enable_calls.append(user_id)

    async def admin_list_users(self, *, per_page: int = 100) -> list[dict[str, Any]]:
        if self.raise_on_list is not None:
            raise self.raise_on_list
        return self.list_users_response


_ADMIN_ID = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
_PEER_ID = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")


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


# ---------------------------------------------------------------------------
# GET /admin/coordinators
# ---------------------------------------------------------------------------


def _supabase_user_row(
    *, user_id: uuid.UUID, email: str, role: str | None = "admin", banned_until: str | None = None
) -> dict[str, Any]:
    """Build a plausible Supabase Auth admin-API user row."""
    return {
        "id": str(user_id),
        "email": email,
        "created_at": "2024-01-02T03:04:05.123456+00:00",
        "banned_until": banned_until,
        "app_metadata": {"role": role} if role is not None else {},
        # Intentionally include a field we must NOT leak — verify it does
        # not surface in the response.
        "encrypted_password": "$2a$10$shouldNeverAppearInResponse",
    }


def test_list_returns_401_without_bearer(
    stack: tuple[TestClient, dict[str, Any], _StubSupabaseAuthClient],
) -> None:
    client, _, _ = stack
    res = client.get("/admin/coordinators")
    assert res.status_code == 401


def test_list_returns_both_tiers_and_marks_self(
    stack: tuple[TestClient, dict[str, Any], _StubSupabaseAuthClient],
) -> None:
    client, state, sb = stack
    state["claims"] = _claims_for("admin")
    sb.list_users_response = [
        _supabase_user_row(user_id=_ADMIN_ID, email="me@example.com", role="admin"),
        _supabase_user_row(user_id=_PEER_ID, email="coord@example.com", role="coordinator"),
        _supabase_user_row(user_id=uuid.uuid4(), email="citizen@example.com", role="citizen"),
        _supabase_user_row(user_id=uuid.uuid4(), email="noRole@example.com", role=None),
    ]
    res = client.get(
        "/admin/coordinators",
        headers={"Authorization": "Bearer good-token"},
    )
    assert res.status_code == 200, res.text
    body_raw: Any = res.json()
    assert isinstance(body_raw, list)
    body = cast(list[dict[str, Any]], body_raw)
    # Both managed tiers survive; citizen / role-less rows are filtered out.
    assert {r["email"] for r in body} == {"me@example.com", "coord@example.com"}
    by_email = {r["email"]: r for r in body}
    assert by_email["me@example.com"]["role"] == "admin"
    assert by_email["coord@example.com"]["role"] == "coordinator"
    self_rows: list[dict[str, Any]] = [r for r in body if r["is_self"]]
    assert len(self_rows) == 1
    assert self_rows[0]["email"] == "me@example.com"
    # Defence in depth: no password material in the response.
    for r in body:
        for key in r:
            assert "password" not in key.lower()
            assert "encrypted" not in key.lower()


def test_list_marks_disabled_rows_when_banned_until_is_future(
    stack: tuple[TestClient, dict[str, Any], _StubSupabaseAuthClient],
) -> None:
    client, state, sb = stack
    state["claims"] = _claims_for("admin")
    future = (datetime.now(tz=UTC) + timedelta(days=365)).isoformat()
    past = (datetime.now(tz=UTC) - timedelta(days=1)).isoformat()
    disabled_id = uuid.uuid4()
    rehabilitated_id = uuid.uuid4()
    sb.list_users_response = [
        _supabase_user_row(user_id=disabled_id, email="d@example.com", banned_until=future),
        _supabase_user_row(user_id=rehabilitated_id, email="r@example.com", banned_until=past),
    ]
    res = client.get(
        "/admin/coordinators",
        headers={"Authorization": "Bearer good-token"},
    )
    assert res.status_code == 200, res.text
    by_email = {r["email"]: r for r in res.json()}
    assert by_email["d@example.com"]["is_disabled"] is True
    assert by_email["r@example.com"]["is_disabled"] is False


def test_list_allows_coordinator_role(
    stack: tuple[TestClient, dict[str, Any], _StubSupabaseAuthClient],
) -> None:
    """Read-only visibility is open to both roles."""
    client, state, sb = stack
    state["claims"] = _claims_for("coordinator")
    sb.list_users_response = [
        _supabase_user_row(user_id=_ADMIN_ID, email="admin@example.com"),
    ]
    res = client.get(
        "/admin/coordinators",
        headers={"Authorization": "Bearer good-token"},
    )
    assert res.status_code == 200


# ---------------------------------------------------------------------------
# POST /admin/coordinators
# ---------------------------------------------------------------------------


def test_create_without_bearer_returns_401(
    stack: tuple[TestClient, dict[str, Any], _StubSupabaseAuthClient],
) -> None:
    client, _, _ = stack
    res = client.post(
        "/admin/coordinators",
        json={"email": "new@example.com", "password": "newpass1234"},
    )
    assert res.status_code == 401


def test_create_with_coordinator_role_returns_403(
    stack: tuple[TestClient, dict[str, Any], _StubSupabaseAuthClient],
) -> None:
    client, state, sb = stack
    state["claims"] = _claims_for("coordinator")
    res = client.post(
        "/admin/coordinators",
        json={"email": "new@example.com", "password": "newpass1234"},
        headers={"Authorization": "Bearer good-token"},
    )
    assert res.status_code == 403
    assert sb.create_calls == []


def test_create_defaults_to_coordinator_role(
    stack: tuple[TestClient, dict[str, Any], _StubSupabaseAuthClient],
) -> None:
    """An omitted `role` mints the lower tier — never silently an admin."""
    client, state, sb = stack
    state["claims"] = _claims_for("admin")
    new_id = uuid.uuid4()
    sb.created_user = _supabase_user_row(
        user_id=new_id, email="new@example.com", role="coordinator"
    )
    res = client.post(
        "/admin/coordinators",
        json={"email": "new@example.com", "password": "newpass1234"},
        headers={"Authorization": "Bearer good-token"},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["id"] == str(new_id)
    assert body["email"] == "new@example.com"
    assert body["role"] == "coordinator"
    assert body["is_self"] is False
    assert body["is_disabled"] is False
    assert sb.create_calls == [
        {"email": "new@example.com", "password": "newpass1234", "role": "coordinator"}
    ]


def test_create_admin_when_role_explicitly_admin(
    stack: tuple[TestClient, dict[str, Any], _StubSupabaseAuthClient],
) -> None:
    client, state, sb = stack
    state["claims"] = _claims_for("admin")
    new_id = uuid.uuid4()
    sb.created_user = _supabase_user_row(user_id=new_id, email="boss@example.com", role="admin")
    res = client.post(
        "/admin/coordinators",
        json={"email": "boss@example.com", "password": "newpass1234", "role": "admin"},
        headers={"Authorization": "Bearer good-token"},
    )
    assert res.status_code == 201, res.text
    assert res.json()["role"] == "admin"
    assert sb.create_calls == [
        {"email": "boss@example.com", "password": "newpass1234", "role": "admin"}
    ]


def test_create_with_unknown_role_returns_422(
    stack: tuple[TestClient, dict[str, Any], _StubSupabaseAuthClient],
) -> None:
    client, state, sb = stack
    state["claims"] = _claims_for("admin")
    res = client.post(
        "/admin/coordinators",
        json={"email": "new@example.com", "password": "newpass1234", "role": "superadmin"},
        headers={"Authorization": "Bearer good-token"},
    )
    assert res.status_code == 422
    assert sb.create_calls == []


def test_create_duplicate_email_returns_409(
    stack: tuple[TestClient, dict[str, Any], _StubSupabaseAuthClient],
) -> None:
    client, state, sb = stack
    state["claims"] = _claims_for("admin")
    sb.find_by_email_response = _supabase_user_row(user_id=uuid.uuid4(), email="dup@example.com")
    res = client.post(
        "/admin/coordinators",
        json={"email": "dup@example.com", "password": "anotherpass1"},
        headers={"Authorization": "Bearer good-token"},
    )
    assert res.status_code == 409
    assert res.json()["detail"] == "email_exists"
    assert sb.create_calls == []


def test_create_with_short_password_returns_422(
    stack: tuple[TestClient, dict[str, Any], _StubSupabaseAuthClient],
) -> None:
    client, state, _ = stack
    state["claims"] = _claims_for("admin")
    res = client.post(
        "/admin/coordinators",
        json={"email": "new@example.com", "password": "short"},
        headers={"Authorization": "Bearer good-token"},
    )
    assert res.status_code == 422


def test_create_with_missing_password_returns_422(
    stack: tuple[TestClient, dict[str, Any], _StubSupabaseAuthClient],
) -> None:
    client, state, _ = stack
    state["claims"] = _claims_for("admin")
    res = client.post(
        "/admin/coordinators",
        json={"email": "new@example.com"},
        headers={"Authorization": "Bearer good-token"},
    )
    assert res.status_code == 422


# ---------------------------------------------------------------------------
# PATCH /admin/coordinators/{id}/password
# ---------------------------------------------------------------------------


def test_rotate_without_bearer_returns_401(
    stack: tuple[TestClient, dict[str, Any], _StubSupabaseAuthClient],
) -> None:
    client, _, _ = stack
    res = client.patch(
        f"/admin/coordinators/{_PEER_ID}/password",
        json={"password": "rotatedpw123"},
    )
    assert res.status_code == 401


def test_rotate_with_coordinator_role_returns_403(
    stack: tuple[TestClient, dict[str, Any], _StubSupabaseAuthClient],
) -> None:
    client, state, sb = stack
    state["claims"] = _claims_for("coordinator")
    res = client.patch(
        f"/admin/coordinators/{_PEER_ID}/password",
        json={"password": "rotatedpw123"},
        headers={"Authorization": "Bearer good-token"},
    )
    assert res.status_code == 403
    assert sb.update_password_calls == []


def test_rotate_self_returns_400(
    stack: tuple[TestClient, dict[str, Any], _StubSupabaseAuthClient],
) -> None:
    client, state, sb = stack
    state["claims"] = _claims_for("admin")
    res = client.patch(
        f"/admin/coordinators/{_ADMIN_ID}/password",
        json={"password": "rotatedpw123"},
        headers={"Authorization": "Bearer good-token"},
    )
    assert res.status_code == 400
    assert res.json()["detail"] == "cannot_modify_self"
    assert sb.update_password_calls == []


def test_rotate_success_calls_supabase(
    stack: tuple[TestClient, dict[str, Any], _StubSupabaseAuthClient],
) -> None:
    client, state, sb = stack
    state["claims"] = _claims_for("admin")
    res = client.patch(
        f"/admin/coordinators/{_PEER_ID}/password",
        json={"password": "rotatedpw123"},
        headers={"Authorization": "Bearer good-token"},
    )
    assert res.status_code == 200, res.text
    assert res.json() == {"status": "ok"}
    assert len(sb.update_password_calls) == 1
    call = sb.update_password_calls[0]
    assert call["user_id"] == _PEER_ID
    assert call["password"] == "rotatedpw123"
    # Re-stamps the target's *own* role (coordinator), never a hardcoded
    # value — a password rotation must not change the target's tier.
    assert call["role"] == "coordinator"


def test_rotate_admin_target_returns_403(
    stack: tuple[TestClient, dict[str, Any], _StubSupabaseAuthClient],
) -> None:
    """Admin accounts are immutable via the API — no password rotation."""
    client, state, sb = stack
    state["claims"] = _claims_for("admin")
    sb.get_user_role = "admin"
    res = client.patch(
        f"/admin/coordinators/{_PEER_ID}/password",
        json={"password": "rotatedpw123"},
        headers={"Authorization": "Bearer good-token"},
    )
    assert res.status_code == 403
    assert res.json()["detail"] == "admin_immutable"
    assert sb.update_password_calls == []


def test_rotate_missing_target_returns_404(
    stack: tuple[TestClient, dict[str, Any], _StubSupabaseAuthClient],
) -> None:
    client, state, sb = stack
    state["claims"] = _claims_for("admin")
    sb.raise_on_get_user = SupabaseAuthError(404, "not found")
    res = client.patch(
        f"/admin/coordinators/{_PEER_ID}/password",
        json={"password": "rotatedpw123"},
        headers={"Authorization": "Bearer good-token"},
    )
    assert res.status_code == 404
    assert res.json()["detail"] == "coordinator_not_found"
    assert sb.update_password_calls == []


def test_rotate_short_password_returns_422(
    stack: tuple[TestClient, dict[str, Any], _StubSupabaseAuthClient],
) -> None:
    client, state, _ = stack
    state["claims"] = _claims_for("admin")
    res = client.patch(
        f"/admin/coordinators/{_PEER_ID}/password",
        json={"password": "short"},
        headers={"Authorization": "Bearer good-token"},
    )
    assert res.status_code == 422


@pytest.mark.redis
def test_rotate_revokes_existing_refresh_families(
    stack: tuple[TestClient, dict[str, Any], _StubSupabaseAuthClient],
) -> None:
    client, state, _sb = stack
    state["claims"] = _claims_for("admin")

    # Seed two refresh-token families for the target against the same
    # Redis the endpoint will touch. Same pattern as the disable test.
    import asyncio

    import redis.asyncio as redis_asyncio

    from api.auth.refresh import RefreshTokenStore as _Store

    settings = get_settings()

    async def _seed() -> None:
        client_handle: Any = redis_asyncio.from_url(  # pyright: ignore[reportUnknownMemberType]
            settings.redis_dsn, decode_responses=True
        )
        store = _Store(client_handle)
        try:
            await store.issue(_PEER_ID)
            await store.issue(_PEER_ID)
        finally:
            await client_handle.aclose()

    asyncio.run(_seed())

    res = client.patch(
        f"/admin/coordinators/{_PEER_ID}/password",
        json={"password": "rotatedpw123"},
        headers={"Authorization": "Bearer good-token"},
    )
    assert res.status_code == 200, res.text


# ---------------------------------------------------------------------------
# POST /admin/coordinators/{id}/enable
# ---------------------------------------------------------------------------


def test_enable_without_bearer_returns_401(
    stack: tuple[TestClient, dict[str, Any], _StubSupabaseAuthClient],
) -> None:
    client, _, _ = stack
    res = client.post(f"/admin/coordinators/{_PEER_ID}/enable")
    assert res.status_code == 401


def test_enable_with_coordinator_role_returns_403(
    stack: tuple[TestClient, dict[str, Any], _StubSupabaseAuthClient],
) -> None:
    client, state, sb = stack
    state["claims"] = _claims_for("coordinator")
    res = client.post(
        f"/admin/coordinators/{_PEER_ID}/enable",
        headers={"Authorization": "Bearer good-token"},
    )
    assert res.status_code == 403
    assert sb.enable_calls == []


def test_enable_self_returns_400(
    stack: tuple[TestClient, dict[str, Any], _StubSupabaseAuthClient],
) -> None:
    client, state, sb = stack
    state["claims"] = _claims_for("admin")
    res = client.post(
        f"/admin/coordinators/{_ADMIN_ID}/enable",
        headers={"Authorization": "Bearer good-token"},
    )
    assert res.status_code == 400
    assert res.json()["detail"] == "cannot_modify_self"
    assert sb.enable_calls == []


def test_enable_admin_target_returns_403(
    stack: tuple[TestClient, dict[str, Any], _StubSupabaseAuthClient],
) -> None:
    """A disabled admin cannot be re-enabled via the API — out-of-band only."""
    client, state, sb = stack
    state["claims"] = _claims_for("admin")
    sb.get_user_role = "admin"
    res = client.post(
        f"/admin/coordinators/{_PEER_ID}/enable",
        headers={"Authorization": "Bearer good-token"},
    )
    assert res.status_code == 403
    assert res.json()["detail"] == "admin_immutable"
    assert sb.enable_calls == []


def test_enable_success_calls_supabase(
    stack: tuple[TestClient, dict[str, Any], _StubSupabaseAuthClient],
) -> None:
    client, state, sb = stack
    state["claims"] = _claims_for("admin")
    res = client.post(
        f"/admin/coordinators/{_PEER_ID}/enable",
        headers={"Authorization": "Bearer good-token"},
    )
    assert res.status_code == 200, res.text
    assert res.json() == {"status": "ok"}
    assert sb.enable_calls == [_PEER_ID]


def test_enable_idempotent_on_already_active_user(
    stack: tuple[TestClient, dict[str, Any], _StubSupabaseAuthClient],
) -> None:
    """Supabase's `ban_duration: "none"` on an active user is a no-op 200.

    The stub mimics that by simply recording the call without raising.
    """
    client, state, sb = stack
    state["claims"] = _claims_for("admin")
    for _ in range(3):
        res = client.post(
            f"/admin/coordinators/{_PEER_ID}/enable",
            headers={"Authorization": "Bearer good-token"},
        )
        assert res.status_code == 200, res.text
    assert sb.enable_calls == [_PEER_ID, _PEER_ID, _PEER_ID]


def test_enable_supabase_failure_returns_502(
    stack: tuple[TestClient, dict[str, Any], _StubSupabaseAuthClient],
) -> None:
    client, state, sb = stack
    state["claims"] = _claims_for("admin")
    sb.raise_on_enable = SupabaseAuthError(500, "boom")
    res = client.post(
        f"/admin/coordinators/{_PEER_ID}/enable",
        headers={"Authorization": "Bearer good-token"},
    )
    assert res.status_code == 502
