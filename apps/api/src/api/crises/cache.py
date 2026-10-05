"""Short-TTL cache for crisis row lookups, which the public heatmap repeats per tile.

Misses are cached too, so requests for a nonexistent crisis hit the DB once per TTL.
The admin PATCH route invalidates entries on update.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from typing import Final

from api.crises.service import CrisisRow


class _Missing:
    """Sentinel for "we looked this up and got nothing"."""

    __slots__ = ()


MISSING: Final[_Missing] = _Missing()

# The MISSING sentinel never escapes the cache; callers receive CrisisRow | None.
_CacheValue = CrisisRow | _Missing


class CrisisRowCache:
    """Async-safe TTL cache. Bounded; LRU-evicts when full."""

    def __init__(self, ttl_seconds: float = 30.0, maxsize: int = 256) -> None:
        self._ttl = ttl_seconds
        self._maxsize = maxsize
        self._store: OrderedDict[str, tuple[float, _CacheValue]] = OrderedDict()
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> _CacheValue | None:
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if expires_at <= time.monotonic():
                self._store.pop(key, None)
                return None
            self._store.move_to_end(key)
            return value

    async def put(self, key: str, value: _CacheValue) -> None:
        async with self._lock:
            self._store[key] = (time.monotonic() + self._ttl, value)
            self._store.move_to_end(key)
            while len(self._store) > self._maxsize:
                self._store.popitem(last=False)

    async def invalidate(self, key: str) -> None:
        async with self._lock:
            self._store.pop(key, None)
