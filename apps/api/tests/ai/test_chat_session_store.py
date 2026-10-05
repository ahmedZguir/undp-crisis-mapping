"""Unit tests for the in-memory chat-session store.

The store now uses `asyncio.Lock` rather than `threading.Lock`; the API
handlers `await` `put`/`get` so a contended store yields the event loop
instead of blocking it. These tests pin the contract.
"""

from __future__ import annotations

import asyncio
import time
import uuid

import pytest

from api.ai.chat import SESSION_TTL_SECONDS, ChatSession, SessionStore


def _make_session() -> ChatSession:
    return ChatSession(
        session_id=uuid.uuid4().hex,
        crisis_id=uuid.uuid4(),
        filter_signature="sig",
        k_ids=[],
    )


@pytest.mark.asyncio
async def test_put_then_get_returns_same_session() -> None:
    store = SessionStore()
    session = _make_session()
    await store.put(session)
    fetched = await store.get(session.session_id)
    assert fetched is session


@pytest.mark.asyncio
async def test_expired_sessions_are_garbage_collected() -> None:
    """`_gc_locked` runs on every put/get; stale rows disappear."""
    store = SessionStore()
    session = _make_session()
    session.last_touched = time.time() - SESSION_TTL_SECONDS - 1
    # Bypass `put` (it would refresh `last_touched`) and stash the
    # session directly.
    store._sessions[session.session_id] = session  # pyright: ignore[reportPrivateUsage]
    other = _make_session()
    await store.put(other)
    assert await store.get(session.session_id) is None
    assert await store.get(other.session_id) is other


@pytest.mark.asyncio
async def test_concurrent_puts_do_not_corrupt_store() -> None:
    """A burst of concurrent `put`s lands every session in the store."""
    store = SessionStore()
    sessions = [_make_session() for _ in range(100)]
    await asyncio.gather(*(store.put(s) for s in sessions))
    for s in sessions:
        assert await store.get(s.session_id) is s
