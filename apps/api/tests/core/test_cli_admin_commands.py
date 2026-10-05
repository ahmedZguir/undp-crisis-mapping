"""Tests for the `python -m api.cli admin {create,set-password}` commands.

The CLI builds its own Supabase + Redis clients by default. We use the
underlying `admin_create_async` / `admin_set_password_async` helpers
with stubs injected; the click surface is a thin sync wrapper over those
helpers and is exercised separately via `CliRunner`.

No Redis dependency: the revoker is invoked against the same fake redis
used in `test_admin_lifecycle_bootstrap.py` (an in-memory dict implementing
`set`/`get`/`scan_iter`).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from click.testing import CliRunner

from api.auth.refresh import RefreshTokenStore
from api.auth.supabase import SupabaseAuthError
from api.cli import admin_create_async, admin_set_password_async, cli


class _StubSupabaseAuthClient:
    def __init__(self) -> None:
        self.find_response: dict[str, Any] | None = None
        self.raise_on_find: SupabaseAuthError | None = None
        self.create_calls: list[dict[str, Any]] = []
        self.update_calls: list[dict[str, Any]] = []

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
        new_id = str(uuid.uuid4())
        self.create_calls.append({"email": email, "password": password, "role": role, "id": new_id})
        return {"id": new_id, "email": email}

    async def admin_update_user_password(
        self, *, user_id: uuid.UUID, password: str, role: str | None = None
    ) -> dict[str, Any]:
        self.update_calls.append({"user_id": user_id, "password": password, "role": role})
        return {"id": str(user_id)}

    async def admin_find_user_by_email(self, email: str) -> dict[str, Any] | None:
        if self.raise_on_find is not None:
            raise self.raise_on_find
        return self.find_response

    async def admin_verify_password(self, email: str, password: str) -> bool:
        return False

    async def admin_disable_user(self, user_id: uuid.UUID) -> None:
        return None

    async def admin_enable_user(self, user_id: uuid.UUID) -> None:
        return None

    async def admin_list_users(self, *, per_page: int = 100) -> list[dict[str, Any]]:
        return []


class _FakeRedisClient:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.closed = False

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    def scan_iter(self, match: str | None = None, count: int | None = None) -> AsyncIterator[str]:
        # Real `redis.asyncio.Redis.scan_iter` is a sync method that
        # returns an async iterator. Mirror that shape.
        prefix = match.rstrip("*") if match else ""

        async def _gen() -> AsyncIterator[str]:
            for key in list(self.store.keys()):
                if key.startswith(prefix):
                    yield key

        return _gen()

    async def aclose(self) -> None:
        self.closed = True


# ---- Direct-helper tests (async) ---------------------------------------


async def test_admin_create_new_user_calls_create() -> None:
    sb = _StubSupabaseAuthClient()
    sb.find_response = None  # user does not exist
    redis = _FakeRedisClient()

    exit_code = await admin_create_async(
        "admin@x.y", "secret", supabase_client=sb, redis_client=redis
    )

    assert exit_code == 0
    assert len(sb.create_calls) == 1
    assert sb.create_calls[0]["email"] == "admin@x.y"
    assert sb.create_calls[0]["role"] == "admin"
    assert sb.update_calls == []


async def test_admin_create_existing_user_falls_through_to_update() -> None:
    user_id = uuid.uuid4()
    sb = _StubSupabaseAuthClient()
    sb.find_response = {"id": str(user_id), "email": "admin@x.y"}
    redis = _FakeRedisClient()
    # Seed a refresh family so the revoker has something to revoke.
    store = RefreshTokenStore(redis)
    await store.issue(user_id)

    exit_code = await admin_create_async(
        "admin@x.y", "new-pw", supabase_client=sb, redis_client=redis
    )

    assert exit_code == 0
    assert sb.create_calls == []
    assert len(sb.update_calls) == 1
    assert sb.update_calls[0]["user_id"] == user_id


async def test_admin_set_password_revokes_families() -> None:
    user_id = uuid.uuid4()
    sb = _StubSupabaseAuthClient()
    sb.find_response = {"id": str(user_id), "email": "admin@x.y"}
    redis = _FakeRedisClient()
    store = RefreshTokenStore(redis)
    await store.issue(user_id)
    await store.issue(user_id)

    exit_code = await admin_set_password_async(
        "admin@x.y", "new-pw", supabase_client=sb, redis_client=redis
    )

    assert exit_code == 0
    assert len(sb.update_calls) == 1
    # Two refresh families issued → two revoked entries in the fake.
    revoked_keys = [k for k in redis.store if "family:" in k and ":revoked" in k]
    assert len(revoked_keys) >= 2


async def test_admin_set_password_unknown_user_returns_nonzero(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sb = _StubSupabaseAuthClient()
    sb.find_response = None
    redis = _FakeRedisClient()

    with caplog.at_level(logging.ERROR):
        exit_code = await admin_set_password_async(
            "ghost@x.y", "pw", supabase_client=sb, redis_client=redis
        )

    assert exit_code == 1
    # Email is masked in the log so we don't log full local-part.
    assert any(
        "admin set-password" in r.message and "g***@x.y" in r.message for r in caplog.records
    )
    assert sb.update_calls == []


async def test_admin_create_logs_outcome_and_masked_email(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sb = _StubSupabaseAuthClient()
    sb.find_response = None
    redis = _FakeRedisClient()

    with caplog.at_level(logging.INFO):
        await admin_create_async("jamie@example.com", "pw", supabase_client=sb, redis_client=redis)

    messages = [r.message for r in caplog.records]
    assert any(
        "admin create" in m and "j***@example.com" in m and "outcome=created" in m for m in messages
    ), messages


# ---- click-surface smoke test ------------------------------------------


def test_cli_admin_create_help() -> None:
    """The click surface compiles and exposes the expected subcommands."""
    runner = CliRunner()
    res = runner.invoke(cli, ["--help"])
    assert res.exit_code == 0
    assert "admin" in res.output


def test_cli_admin_subcommands_listed() -> None:
    runner = CliRunner()
    res = runner.invoke(cli, ["admin", "--help"])
    assert res.exit_code == 0
    assert "create" in res.output
    assert "set-password" in res.output
