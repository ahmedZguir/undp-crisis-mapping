"""Unit tests for the in-process TTL cache fronting `CrisisLookup`.

No database. The cache is a pure data structure; we drive `time.monotonic`
via a tight TTL and exercise the four operations the rest of the system
relies on (hit, miss, expiry, invalidate, bounded eviction).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime

import pytest

from api.crises.cache import MISSING, CrisisRowCache
from api.crises.service import CrisisRow


def _row() -> CrisisRow:
    return CrisisRow(
        id=uuid.uuid4(),
        name="Test",
        status="active",
        created_at=datetime.now(UTC),
    )


def test_cache_returns_value_within_ttl() -> None:
    cache = CrisisRowCache(ttl_seconds=1.0)
    row = _row()
    asyncio.run(cache.put("k", row))
    got = asyncio.run(cache.get("k"))
    assert got is row


def test_cache_expires_after_ttl() -> None:
    cache = CrisisRowCache(ttl_seconds=0.05)
    asyncio.run(cache.put("k", _row()))
    time.sleep(0.06)
    assert asyncio.run(cache.get("k")) is None


def test_cache_stores_missing_sentinel() -> None:
    """A negative lookup must round-trip the sentinel so the lookup adapter
    can short-circuit further DB hits."""
    cache = CrisisRowCache(ttl_seconds=1.0)
    asyncio.run(cache.put("k", MISSING))
    assert asyncio.run(cache.get("k")) is MISSING


def test_invalidate_clears_entry() -> None:
    cache = CrisisRowCache(ttl_seconds=1.0)
    asyncio.run(cache.put("k", _row()))
    asyncio.run(cache.invalidate("k"))
    assert asyncio.run(cache.get("k")) is None


def test_cache_bounded_by_maxsize() -> None:
    """Adding `maxsize + 1` distinct keys evicts the least-recently-touched."""
    cache = CrisisRowCache(ttl_seconds=10.0, maxsize=2)
    a, b, c = _row(), _row(), _row()
    asyncio.run(cache.put("a", a))
    asyncio.run(cache.put("b", b))
    # Touch a so it becomes most-recently-used; b should evict next.
    assert asyncio.run(cache.get("a")) is a
    asyncio.run(cache.put("c", c))
    assert asyncio.run(cache.get("a")) is a
    assert asyncio.run(cache.get("b")) is None
    assert asyncio.run(cache.get("c")) is c


@pytest.mark.parametrize("missing_key", ["nonexistent", ""])
def test_get_on_unknown_key_returns_none(missing_key: str) -> None:
    cache = CrisisRowCache()
    assert asyncio.run(cache.get(missing_key)) is None
