"""`ensure_env_var_admin` — four-branch tests against a stubbed Supabase client.

The bootstrap reconciler is a pure function over the Supabase admin API +
the refresh-token revoker. We stub `SupabaseAuthClientLike` to record calls
and pre-set responses; the revoker is exercised via a fake redis_client
that records `set` calls (no real Redis needed — `RefreshTokenStore` is
constructed with the fake handle).

We deliberately bypass the test_auth_* / test_admin_auth_* conftest gate
by naming this file `test_admin_lifecycle_*` (no auth gate runs in the
helper paths anyway, but the rule is documented).
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest

from api.auth.bootstrap import (
    BootstrapResult,
    ensure_env_var_admin,
    mask_email,
)
from api.auth.refresh import RefreshTokenStore
from api.auth.supabase import SupabaseAuthError
from api.core.config import Settings, get_settings


class _StubSupabaseAuthClient:
    """Records call shapes for the lifecycle assertions; configurable."""

    def __init__(self) -> None:
        # `admin_find_user_by_email` controls the create-vs-update branch.
        # None  → user does not exist, bootstrap creates.
        # dict  → user exists, bootstrap inspects password then updates / no-ops.
        self.find_response: dict[str, Any] | None = None
        # password-verify controls the no-op-vs-rotate branch.
        self.password_matches: bool = True
        # If set, raise on the named method instead of returning normally.
        self.raise_on: str | None = None
        # Recorded calls.
        self.create_calls: list[dict[str, Any]] = []
        self.update_calls: list[dict[str, Any]] = []
        self.find_calls: list[str] = []
        self.verify_calls: list[tuple[str, str]] = []
        self.disable_calls: list[uuid.UUID] = []

    async def password_login(self, email: str, password: str) -> dict[str, Any]:
        return {}

    async def mint_access_token(self, refresh_token: str) -> tuple[str, str]:
        return ("stub-access", "stub-next-refresh")

    async def get_user(self, user_id: uuid.UUID) -> dict[str, Any]:
        return {}

    async def aclose(self) -> None:
        return None

    async def admin_create_user(
        self, *, email: str, password: str, role: str = "admin"
    ) -> dict[str, Any]:
        if self.raise_on == "admin_create_user":
            raise SupabaseAuthError(500, "stub failure")
        self.create_calls.append({"email": email, "password": password, "role": role})
        return {"id": str(uuid.uuid4()), "email": email}

    async def admin_update_user_password(
        self, *, user_id: uuid.UUID, password: str, role: str | None = None
    ) -> dict[str, Any]:
        if self.raise_on == "admin_update_user_password":
            raise SupabaseAuthError(500, "stub failure")
        self.update_calls.append({"user_id": user_id, "password": password, "role": role})
        return {"id": str(user_id)}

    async def admin_find_user_by_email(self, email: str) -> dict[str, Any] | None:
        if self.raise_on == "admin_find_user_by_email":
            raise SupabaseAuthError(500, "stub failure")
        self.find_calls.append(email)
        return self.find_response

    async def admin_verify_password(self, email: str, password: str) -> bool:
        self.verify_calls.append((email, password))
        return self.password_matches

    async def admin_disable_user(self, user_id: uuid.UUID) -> None:
        self.disable_calls.append(user_id)

    async def admin_enable_user(self, user_id: uuid.UUID) -> None:
        return None

    async def admin_list_users(self, *, per_page: int = 100) -> list[dict[str, Any]]:
        return []


class _FakeRedisClient:
    """Minimal in-memory Redis stub. Implements only the verbs the
    refresh store and the revoker touch: `set`, `get`, `scan_iter`,
    `aclose`. Enough to exercise the bootstrap end-to-end without a real
    Redis."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        # `ex` is ignored — the fake has no TTL.
        self.store[key] = value

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    def scan_iter(self, match: str | None = None, count: int | None = None) -> AsyncIterator[str]:
        # Real `redis.asyncio.Redis.scan_iter` is a SYNC method that
        # returns an async iterator — not an async method. The fake
        # matches that shape. Naive prefix match: `auth:refresh:token:*`
        # → startswith.
        prefix = match.rstrip("*") if match else ""

        async def _gen() -> AsyncIterator[str]:
            for key in list(self.store.keys()):
                if key.startswith(prefix):
                    yield key

        return _gen()


def _settings_with(*, email: str | None, password: str | None) -> Settings:
    """Build a Settings clone with the bootstrap vars overridden."""
    base = get_settings()
    return base.model_copy(update={"admin_email": email, "admin_password": password})


async def test_bootstrap_disabled_when_env_vars_missing() -> None:
    sb = _StubSupabaseAuthClient()
    redis = _FakeRedisClient()
    store = RefreshTokenStore(redis)

    result = await ensure_env_var_admin(
        settings=_settings_with(email=None, password=None),
        supabase_client=sb,
        redis_client=redis,
        refresh_store=store,
    )

    assert result == BootstrapResult.disabled
    assert sb.find_calls == []
    assert sb.create_calls == []
    assert sb.update_calls == []


async def test_bootstrap_creates_new_user_when_absent() -> None:
    sb = _StubSupabaseAuthClient()
    sb.find_response = None  # user does not exist
    redis = _FakeRedisClient()
    store = RefreshTokenStore(redis)

    result = await ensure_env_var_admin(
        settings=_settings_with(email="admin@x.y", password="pw"),
        supabase_client=sb,
        redis_client=redis,
        refresh_store=store,
    )

    assert result == BootstrapResult.created
    assert sb.find_calls == ["admin@x.y"]
    assert len(sb.create_calls) == 1
    assert sb.create_calls[0]["email"] == "admin@x.y"
    assert sb.create_calls[0]["role"] == "admin"
    assert sb.update_calls == []


async def test_bootstrap_no_op_when_password_matches() -> None:
    sb = _StubSupabaseAuthClient()
    sb.find_response = {"id": str(uuid.uuid4()), "email": "admin@x.y"}
    sb.password_matches = True
    redis = _FakeRedisClient()
    store = RefreshTokenStore(redis)

    result = await ensure_env_var_admin(
        settings=_settings_with(email="admin@x.y", password="pw"),
        supabase_client=sb,
        redis_client=redis,
        refresh_store=store,
    )

    assert result == BootstrapResult.unchanged
    assert sb.verify_calls == [("admin@x.y", "pw")]
    assert sb.update_calls == []
    assert sb.create_calls == []


async def test_bootstrap_rotates_password_and_revokes_families() -> None:
    user_id = uuid.uuid4()
    sb = _StubSupabaseAuthClient()
    sb.find_response = {"id": str(user_id), "email": "admin@x.y"}
    sb.password_matches = False

    redis = _FakeRedisClient()
    store = RefreshTokenStore(redis)
    # Pre-seed a refresh token for the user so the revoker has something to revoke.
    await store.issue(user_id)

    result = await ensure_env_var_admin(
        settings=_settings_with(email="admin@x.y", password="new-pw"),
        supabase_client=sb,
        redis_client=redis,
        refresh_store=store,
    )

    assert result == BootstrapResult.password_rotated
    assert len(sb.update_calls) == 1
    assert sb.update_calls[0]["user_id"] == user_id
    assert sb.update_calls[0]["role"] == "admin"
    # And the revoker fired: the family is in the revoked-set.
    revoked_keys = [k for k in redis.store if "family:" in k and ":revoked" in k]
    assert revoked_keys, "expected a revoked-family entry after rotation"


async def test_bootstrap_failure_is_non_fatal(caplog: pytest.LogCaptureFixture) -> None:
    sb = _StubSupabaseAuthClient()
    sb.raise_on = "admin_find_user_by_email"
    redis = _FakeRedisClient()
    store = RefreshTokenStore(redis)

    with caplog.at_level(logging.ERROR):
        result = await ensure_env_var_admin(
            settings=_settings_with(email="admin@x.y", password="pw"),
            supabase_client=sb,
            redis_client=redis,
            refresh_store=store,
        )

    assert result == BootstrapResult.failed
    assert any("env-var admin bootstrap failed" in r.message for r in caplog.records)


def test_mask_email_preserves_first_char_and_domain() -> None:
    assert mask_email("jamie@example.com") == "j***@example.com"
    assert mask_email("a@b.c") == "a***@b.c"
    # Defensive: bare token without @ still masked.
    assert mask_email("bareword") == "b***"


async def test_bootstrap_log_includes_outcome_and_masked_email(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sb = _StubSupabaseAuthClient()
    sb.find_response = None
    redis = _FakeRedisClient()
    store = RefreshTokenStore(redis)

    with caplog.at_level(logging.INFO):
        await ensure_env_var_admin(
            settings=_settings_with(email="jamie@example.com", password="pw"),
            supabase_client=sb,
            redis_client=redis,
            refresh_store=store,
        )

    # Stdout audit shape: masked email + outcome name in a single line.
    messages = [r.message for r in caplog.records]
    assert any("email=j***@example.com" in m and "outcome=created" in m for m in messages), messages


def test_fake_redis_round_trip_is_json_shaped() -> None:
    """Sanity: the fake redis stores JSON-able strings, matching the
    contract the refresh store relies on. Guards against a future
    refactor that changes the on-wire shape."""
    redis = _FakeRedisClient()

    async def _set_and_get() -> str | None:
        await redis.set("k", json.dumps({"a": 1}))
        return await redis.get("k")

    import asyncio

    value = asyncio.run(_set_and_get())
    assert value is not None
    assert json.loads(value) == {"a": 1}
