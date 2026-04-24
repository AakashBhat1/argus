from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Generic, TypeVar

from project.settings import QUERY_CACHE_ENABLED, QUERY_CACHE_MAX_ITEMS, QUERY_CACHE_TTL_SECONDS


T = TypeVar("T")


class QueryCache(Generic[T]):
    def __init__(self, ttl_seconds: int = QUERY_CACHE_TTL_SECONDS, max_items: int = QUERY_CACHE_MAX_ITEMS) -> None:
        self._ttl_seconds = max(1, int(ttl_seconds))
        self._max_items = max(1, int(max_items))
        self._enabled = QUERY_CACHE_ENABLED
        self._entries: OrderedDict[str, tuple[float, T]] = OrderedDict()
        self._lock = threading.RLock()

    def get(self, key: str) -> T | None:
        if not self._enabled:
            return None
        now = time.time()
        with self._lock:
            payload = self._entries.get(key)
            if payload is None:
                return None
            expires_at, value = payload
            if expires_at < now:
                self._entries.pop(key, None)
                return None
            self._entries.move_to_end(key)
            return value

    def set(self, key: str, value: T) -> None:
        if not self._enabled:
            return
        expires_at = time.time() + self._ttl_seconds
        with self._lock:
            self._entries[key] = (expires_at, value)
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_items:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
