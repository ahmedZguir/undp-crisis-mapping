"""Bounded in-process LRU map."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable


class LRUCache[K, V]:
    def __init__(self, maxsize: int) -> None:
        self._maxsize = maxsize
        self._items: OrderedDict[K, V] = OrderedDict()

    def get(self, key: K) -> V | None:
        value = self._items.get(key)
        if value is not None:
            self._items.move_to_end(key)
        return value

    def put(self, key: K, value: V) -> None:
        self._items[key] = value
        self._items.move_to_end(key)
        while len(self._items) > self._maxsize:
            self._items.popitem(last=False)

    def evict_where(self, predicate: Callable[[K], bool]) -> None:
        for key in [k for k in self._items if predicate(k)]:
            self._items.pop(key, None)
