"""Refresh-store rotation + reuse detection against a real local Redis.

Marked `redis`, so it is skipped when Redis is unreachable. Every test isolates
its state by using a unique Redis key prefix (the random `family_id`/
`jti` returned by `issue`) so there is no global TRUNCATE / FLUSHDB.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from api.auth.models import FamilyRevokedError, ReuseDetectedError, TokenNotFoundError
from api.auth.refresh import RefreshTokenStore
from api.core.config import get_settings

pytestmark = [pytest.mark.redis, pytest.mark.real_auth]


async def _store() -> RefreshTokenStore:
    import redis.asyncio as redis_asyncio

    settings = get_settings()
    client: Any = redis_asyncio.from_url(  # pyright: ignore[reportUnknownMemberType]
        settings.redis_dsn, decode_responses=True
    )
    return RefreshTokenStore(client)


async def test_issue_then_rotate_returns_new_token() -> None:
    store = await _store()
    user_id = uuid.uuid4()
    jti, family_id = await store.issue(user_id)

    new_jti, family_id_returned, user_id_returned, prior_sb = await store.rotate(jti)

    assert new_jti != jti
    assert family_id_returned == family_id
    assert user_id_returned == user_id
    assert prior_sb is None


async def test_issue_with_supabase_refresh_is_returned_on_rotate() -> None:
    """The Supabase refresh token bound to a jti at `issue()` time must
    survive `rotate()` and surface as the `prior_supabase_refresh` slot.
    After `set_supabase_refresh` on the sibling jti, the new value must
    surface on the *next* rotate."""
    store = await _store()
    user_id = uuid.uuid4()
    jti, _ = await store.issue(user_id, supabase_refresh="sb-refresh-A")

    new_jti, _, _, prior_sb = await store.rotate(jti)
    assert prior_sb == "sb-refresh-A"

    # The sibling jti starts WITHOUT a supabase refresh — the route is
    # expected to call `set_supabase_refresh` after Supabase replies.
    await store.set_supabase_refresh(new_jti, "sb-refresh-B")
    rotated_sibling = await store.rotate(new_jti)
    assert rotated_sibling[3] == "sb-refresh-B"


async def test_set_supabase_refresh_on_unknown_jti_raises() -> None:
    store = await _store()
    with pytest.raises(TokenNotFoundError):
        await store.set_supabase_refresh("nonexistent-jti", "anything")


async def test_double_rotate_reuse_detected_and_revokes_family() -> None:
    store = await _store()
    user_id = uuid.uuid4()
    jti, _ = await store.issue(user_id)
    new_jti, _, _, _ = await store.rotate(jti)

    # Second rotate on the ORIGINAL jti is reuse.
    with pytest.raises(ReuseDetectedError):
        await store.rotate(jti)

    # And the family is revoked, so rotating the new jti also fails.
    with pytest.raises(FamilyRevokedError):
        await store.rotate(new_jti)


async def test_rotate_after_revoke_family_returns_revoked() -> None:
    store = await _store()
    user_id = uuid.uuid4()
    jti, family_id = await store.issue(user_id)

    await store.revoke_family(family_id)

    with pytest.raises(FamilyRevokedError):
        await store.rotate(jti)


async def test_unknown_token_returns_not_found() -> None:
    store = await _store()
    with pytest.raises(TokenNotFoundError):
        await store.rotate("nonexistent-jti-value")


async def test_family_for_returns_family_id_for_known_token() -> None:
    store = await _store()
    user_id = uuid.uuid4()
    jti, family_id = await store.issue(user_id)

    assert await store.family_for(jti) == family_id
    assert await store.family_for("unknown") is None
