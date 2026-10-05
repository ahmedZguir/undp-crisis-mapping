"""Unit tests for `revoke_refresh_families_for_user` against a real local Redis.

Marked `redis`, so it is skipped when Redis is unreachable. Each test isolates state through
the random `family_id`/`jti` returned by `RefreshTokenStore.issue` so no
global FLUSHDB is needed.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from api.auth.models import FamilyRevokedError
from api.auth.refresh import RefreshTokenStore
from api.auth.revocation import revoke_refresh_families_for_user
from api.core.config import get_settings

pytestmark = pytest.mark.redis


async def _store_and_client() -> tuple[RefreshTokenStore, Any]:
    import redis.asyncio as redis_asyncio

    settings = get_settings()
    client: Any = redis_asyncio.from_url(  # pyright: ignore[reportUnknownMemberType]
        settings.redis_dsn, decode_responses=True
    )
    return RefreshTokenStore(client), client


async def test_revoker_revokes_every_family_for_user() -> None:
    store, client = await _store_and_client()
    user_id = uuid.uuid4()
    jti_a, family_a = await store.issue(user_id)
    jti_b, family_b = await store.issue(user_id)
    assert family_a != family_b

    revoked = await revoke_refresh_families_for_user(client, store, user_id)

    assert revoked >= 2
    # Each token's family is now revoked — rotation raises FamilyRevokedError.
    with pytest.raises(FamilyRevokedError):
        await store.rotate(jti_a)
    with pytest.raises(FamilyRevokedError):
        await store.rotate(jti_b)


async def test_revoker_does_not_touch_other_users() -> None:
    store, client = await _store_and_client()
    user_a = uuid.uuid4()
    user_b = uuid.uuid4()
    _jti_a, _ = await store.issue(user_a)
    jti_b, family_b = await store.issue(user_b)

    await revoke_refresh_families_for_user(client, store, user_a)

    # User B's family is untouched: rotation still mints a new token.
    new_jti, returned_family, returned_user, _prior_sb = await store.rotate(jti_b)
    assert returned_family == family_b
    assert returned_user == user_b
    assert new_jti != jti_b


async def test_revoker_returns_zero_for_user_with_no_tokens() -> None:
    store, client = await _store_and_client()
    unknown_user = uuid.uuid4()

    revoked = await revoke_refresh_families_for_user(client, store, unknown_user)

    assert revoked == 0


async def test_revoker_is_idempotent() -> None:
    store, client = await _store_and_client()
    user_id = uuid.uuid4()
    await store.issue(user_id)
    await store.issue(user_id)

    first = await revoke_refresh_families_for_user(client, store, user_id)
    second = await revoke_refresh_families_for_user(client, store, user_id)

    # Re-revoking is idempotent: the second pass sees the same families
    # and revokes them again (no-op effect, same count).
    assert first >= 2
    assert second == first
