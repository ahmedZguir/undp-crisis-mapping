"""Inbound redelivery dedup shared by the channel webhooks."""

from __future__ import annotations


class SeenIds:
    """Bounded LRU of recently-handled inbound message ids.

    In-memory, like ``SessionStore``. The cap is large enough that a normal day
    cannot evict an id the provider may still retry.
    """

    def __init__(self, cap: int = 1024) -> None:
        self._cap = cap
        self._ids: dict[str, None] = {}

    def add_if_new(self, mid: str) -> bool:
        if mid in self._ids:
            self._ids.pop(mid, None)
            self._ids[mid] = None
            return False
        self._ids[mid] = None
        if len(self._ids) > self._cap:
            oldest = next(iter(self._ids))
            self._ids.pop(oldest, None)
        return True
