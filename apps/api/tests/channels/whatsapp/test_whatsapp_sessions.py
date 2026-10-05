"""Session store: TTL eviction; ``client_submission_id`` is per session, never phone-derived."""

from __future__ import annotations

from datetime import timedelta

import pytest

from api.channels.sessions import InMemorySessionStore, Session


@pytest.mark.asyncio
async def test_client_submission_id_not_derived_from_phone() -> None:
    a = Session(phone_e164="+9665")
    b = Session(phone_e164="+9665")
    assert a.client_submission_id != b.client_submission_id


@pytest.mark.asyncio
async def test_new_session_defaults() -> None:
    s = Session(phone_e164="+9665")
    assert s.state == "active"
    assert s.history == []
    assert s.active_crisis_choices == {}


@pytest.mark.asyncio
async def test_history_is_bounded() -> None:
    from api.channels.sessions import MAX_HISTORY_MSGS, HistoryMsg

    s = Session(phone_e164="+9665")
    for i in range(MAX_HISTORY_MSGS + 10):
        s.append_history(HistoryMsg(role="user", content=f"m{i}"))
    assert len(s.history) == MAX_HISTORY_MSGS
    # Oldest entries dropped.
    assert s.history[0].content == f"m{10}"


@pytest.mark.asyncio
async def test_get_returns_none_when_expired() -> None:
    store = InMemorySessionStore()
    s = Session(phone_e164="+9665")
    # Force expiry in the past.
    s.expires_at = s.created_at - timedelta(seconds=1)
    await store.upsert(s)
    # upsert() also touches expires_at, so reset it after.
    s.expires_at = s.created_at - timedelta(seconds=1)
    assert await store.get("+9665") is None


@pytest.mark.asyncio
async def test_gc_removes_expired() -> None:
    store = InMemorySessionStore()
    fresh = Session(phone_e164="+1")
    stale = Session(phone_e164="+2")
    await store.upsert(fresh)
    await store.upsert(stale)
    stale.expires_at = stale.created_at - timedelta(seconds=1)
    swept = await store.gc()
    assert swept == 1
    assert await store.get("+1") is not None
    assert await store.get("+2") is None


@pytest.mark.asyncio
async def test_upsert_bumps_expiry() -> None:
    store = InMemorySessionStore()
    s = Session(phone_e164="+9665")
    original_expiry = s.expires_at
    await store.upsert(s)
    # touch() runs inside upsert.
    assert s.expires_at >= original_expiry
