"""Small TTL cache fixture used by the behavioral head-to-head suite."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Generic, TypeVar

K = TypeVar("K")
V = TypeVar("V")


class TTLCache(Generic[K, V]):
    def __init__(
        self,
        ttl_seconds: float,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self._values: dict[K, tuple[float, V]] = {}

    def get_or_load(self, key: K, loader: Callable[[], V]) -> V:
        """Return a live value or load it once.

        The starter intentionally lacks concurrency, expiry, and failure
        handling. Implement this method without changing its public signature.
        """

        value = loader()
        self._values[key] = (self._clock() + self.ttl_seconds, value)
        return value
