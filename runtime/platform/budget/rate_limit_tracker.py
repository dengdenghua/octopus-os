from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

_LOG = logging.getLogger("echo.budget.rate_limit")


@dataclass
class RateLimitEntry:
    model: str
    provider: str
    requests_remaining: int | None = None
    tokens_remaining: int | None = None
    reset_at: float | None = None
    observed_at: float = field(default_factory=time.time)


class RateLimitTracker:
    def __init__(self) -> None:
        self._entries: dict[str, RateLimitEntry] = {}
        self._lock = threading.Lock()

    def update(self, entry: RateLimitEntry) -> None:
        key = f"{entry.provider}:{entry.model}"
        with self._lock:
            self._entries[key] = entry

    def update_from_headers(
        self,
        provider: str,
        model: str,
        headers: dict[str, str],
    ) -> RateLimitEntry | None:
        requests_remaining = None
        tokens_remaining = None
        reset_at = None

        for key, value in headers.items():
            kl = key.lower()
            if "remaining" in kl and "request" in kl:
                try:  # noqa: SIM105
                    requests_remaining = int(value)
                except (ValueError, TypeError):  # noqa: BLE001 — coercion chain; invalid header value dropped
                    pass
            elif "remaining" in kl and "token" in kl:
                try:  # noqa: SIM105
                    tokens_remaining = int(value)
                except (ValueError, TypeError):  # noqa: BLE001 — coercion chain; invalid header value dropped
                    pass
            elif "reset" in kl:
                try:  # noqa: SIM105
                    reset_at = float(value)
                except (ValueError, TypeError):  # noqa: BLE001 — coercion chain; invalid header value dropped
                    pass

        if requests_remaining is None and tokens_remaining is None and reset_at is None:
            return None

        entry = RateLimitEntry(
            model=model,
            provider=provider,
            requests_remaining=requests_remaining,
            tokens_remaining=tokens_remaining,
            reset_at=reset_at,
        )
        self.update(entry)
        return entry

    def get(self, provider: str, model: str) -> RateLimitEntry | None:
        key = f"{provider}:{model}"
        with self._lock:
            return self._entries.get(key)

    def is_limited(self, provider: str, model: str) -> bool:
        entry = self.get(provider, model)
        if entry is None:
            return False
        if entry.requests_remaining is not None and entry.requests_remaining <= 0:  # noqa: SIM102
            if entry.reset_at and time.time() < entry.reset_at:
                return True
        return False

    def wait_if_limited(self, provider: str, model: str, max_wait: float = 60.0) -> bool:
        entry = self.get(provider, model)
        if entry is None or entry.reset_at is None:
            return False
        if self.is_limited(provider, model):
            wait = min(entry.reset_at - time.time(), max_wait)
            if wait > 0:
                _LOG.info("rate limited on %s/%s · waiting %.1fs", provider, model, wait)
                time.sleep(wait)
                return True
        return False

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                key: {
                    "requests_remaining": e.requests_remaining,
                    "tokens_remaining": e.tokens_remaining,
                    "reset_at": e.reset_at,
                }
                for key, e in self._entries.items()
            }


__all__ = ["RateLimitEntry", "RateLimitTracker"]
