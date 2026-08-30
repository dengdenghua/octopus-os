from __future__ import annotations

import logging
import os
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any

_LOG = logging.getLogger("echo.credentials.pool")


@dataclass
class CredentialEntry:
    key_id: str
    secret: str
    provider: str = ""
    weight: float = 1.0
    cooldown_until: float = 0.0
    error_count: int = 0
    last_used: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_available(self) -> bool:
        return time.time() >= self.cooldown_until


class CredentialPool:
    def __init__(self) -> None:
        self._entries: list[CredentialEntry] = []
        self._lock = threading.Lock()

    def add(self, entry: CredentialEntry) -> None:
        with self._lock:
            for existing in self._entries:
                if existing.key_id == entry.key_id:
                    existing.secret = entry.secret
                    existing.weight = entry.weight
                    return
            self._entries.append(entry)

    def add_from_env(self, env_var: str, provider: str = "") -> CredentialEntry | None:
        secret = os.environ.get(env_var)
        if not secret:
            return None
        entry = CredentialEntry(
            key_id=f"env:{env_var}",
            secret=secret,
            provider=provider,
            metadata={"source": "env", "var": env_var},
        )
        self.add(entry)
        return entry

    def get(self, provider: str | None = None) -> CredentialEntry | None:
        with self._lock:
            available = [
                e
                for e in self._entries
                if e.is_available and (provider is None or e.provider == provider)
            ]
            if not available:
                return None
            total_weight = sum(e.weight for e in available)
            if total_weight <= 0:
                return available[0]
            r = random.random() * total_weight
            cumulative = 0.0
            for e in available:
                cumulative += e.weight
                if r <= cumulative:
                    e.last_used = time.time()
                    return e
            available[-1].last_used = time.time()
            return available[-1]

    def report_error(self, key_id: str, cooldown_sec: float = 60.0) -> None:
        with self._lock:
            for e in self._entries:
                if e.key_id == key_id:
                    e.error_count += 1
                    e.cooldown_until = time.time() + cooldown_sec
                    _LOG.info(
                        "credential %s error #%d · cooldown %.0fs",
                        key_id,
                        e.error_count,
                        cooldown_sec,
                    )
                    break

    def report_success(self, key_id: str) -> None:
        with self._lock:
            for e in self._entries:
                if e.key_id == key_id:
                    e.error_count = max(0, e.error_count - 1)
                    break

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "total": len(self._entries),
                "available": sum(1 for e in self._entries if e.is_available),
                "cooldown": sum(1 for e in self._entries if not e.is_available),
                "entries": [
                    {
                        "key_id": e.key_id,
                        "provider": e.provider,
                        "available": e.is_available,
                        "error_count": e.error_count,
                    }
                    for e in self._entries
                ],
            }

    @property
    def size(self) -> int:
        return len(self._entries)


__all__ = ["CredentialEntry", "CredentialPool"]
