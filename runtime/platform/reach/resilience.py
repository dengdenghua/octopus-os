from __future__ import annotations

import os
import time
from collections import defaultdict, deque
from threading import Lock
from urllib.parse import urlsplit


class HostRateLimiter:
    """Thread-safe sliding-window limiter used before outbound platform requests."""

    def __init__(self, requests_per_minute: int = 60) -> None:
        self.requests_per_minute = max(1, requests_per_minute)
        self._calls: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def acquire(self, url: str, *, max_wait_seconds: float = 2.0) -> bool:
        host = urlsplit(url).hostname or "unknown"
        now = time.monotonic()
        with self._lock:
            calls = self._calls[host]
            while calls and calls[0] <= now - 60:
                calls.popleft()
            if len(calls) < self.requests_per_minute:
                calls.append(now)
                return True
            wait = 60 - (now - calls[0])
        if wait > max_wait_seconds:
            return False
        time.sleep(max(0, wait))
        return self.acquire(url, max_wait_seconds=0)


host_rate_limiter = HostRateLimiter(int(os.environ.get("ECHO_REACH_REQUESTS_PER_MINUTE", "60")))
