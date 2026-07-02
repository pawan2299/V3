from __future__ import annotations

import hashlib
import re
import time
from collections import OrderedDict
from typing import Any, Optional


class TTLCache:
    """Thread-safe cache with time-to-live expiration."""

    def __init__(self, maxsize: int = 1000, ttl: int = 1800):
        self.maxsize = maxsize
        self.ttl = ttl
        self._cache: OrderedDict[str, tuple[Any, float]] = OrderedDict()

    def get(self, key: str) -> Optional[Any]:
        if key not in self._cache:
            return None
        value, timestamp = self._cache[key]
        if time.time() - timestamp > self.ttl:
            del self._cache[key]
            return None
        self._cache.move_to_end(key)
        return value

    def set(self, key: str, value: Any) -> None:
        current_time = time.time()
        if key in self._cache:
            self._cache.move_to_end(key)
        self._cache[key] = (value, current_time)
        while len(self._cache) > self.maxsize:
            self._cache.popitem(last=False)

    def clear(self) -> None:
        self._cache.clear()

    def stats(self) -> dict:
        return {
            "size": len(self._cache),
            "maxsize": self.maxsize,
            "ttl": self.ttl,
        }


def cache_key(*parts: str) -> str:
    combined = "|".join(str(part) for part in parts)
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()[:32]


def normalize_text(text: str) -> str:
    cleaned = text.lower().strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned
