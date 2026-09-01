from __future__ import annotations

import random
import threading
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict

RotationStrategy = Literal["round_robin", "least_used", "random"]


class KeyStats(BaseModel):
    model_config = ConfigDict(frozen=True)

    key_fingerprint: str
    total_usd: float = 0.0
    total_calls: int = 0
    exhausted_at: float | None = None
    recovered_at: float | None = None


class PoolReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    total_keys: int
    available_keys: int
    exhausted_keys: int
    total_usd: float
    total_calls: int
    stats: list[KeyStats]


class CredentialPool:
    def __init__(
        self,
        keys: list[str],
        strategy: RotationStrategy = "round_robin",
        cooldown_seconds: float = 60.0,
    ) -> None:
        if not keys:
            raise ValueError("CredentialPool requires at least one key")
        self._keys = list(keys)
        self._strategy = strategy
        self._cooldown = cooldown_seconds
        self._lock = threading.RLock()
        self._index: int = 0
        self._exhausted: dict[str, float] = {}
        self._usage: dict[str, float] = {}
        self._calls: dict[str, int] = {}

    def acquire(self) -> str:
        with self._lock:
            self._recover_expired()
            available = [k for k in self._keys if k not in self._exhausted]
            if not available:
                raise AllKeysExhausted(
                    f"all {len(self._keys)} key(s) exhausted; cooldown={self._cooldown}s"
                )
            if self._strategy == "round_robin":
                return self._acquire_round_robin(available)
            if self._strategy == "least_used":
                return self._acquire_least_used(available)
            return random.choice(available)

    def report_exhausted(self, key: str) -> None:
        with self._lock:
            self._exhausted[key] = time.monotonic()

    def report_usage(self, key: str, cost_usd: float = 0.0) -> None:
        with self._lock:
            self._usage[key] = self._usage.get(key, 0.0) + cost_usd
            self._calls[key] = self._calls.get(key, 0) + 1

    def recover(self, key: str) -> None:
        with self._lock:
            self._exhausted.pop(key, None)

    def report(self) -> PoolReport:
        with self._lock:
            self._recover_expired()
            stats = []
            for k in self._keys:
                stats.append(
                    KeyStats(
                        key_fingerprint=_fingerprint(k),
                        total_usd=self._usage.get(k, 0.0),
                        total_calls=self._calls.get(k, 0),
                        exhausted_at=self._exhausted.get(k),
                    )
                )
            return PoolReport(
                total_keys=len(self._keys),
                available_keys=len(self._keys) - len(self._exhausted),
                exhausted_keys=len(self._exhausted),
                total_usd=sum(self._usage.values()),
                total_calls=sum(self._calls.values()),
                stats=stats,
            )

    @property
    def strategy(self) -> RotationStrategy:
        return self._strategy

    @property
    def size(self) -> int:
        return len(self._keys)

    def _acquire_round_robin(self, available: list[str]) -> str:
        available_set = set(available)
        for _ in range(len(self._keys)):
            key = self._keys[self._index % len(self._keys)]
            self._index += 1
            if key in available_set:
                return key
        return available[0]

    def _acquire_least_used(self, available: list[str]) -> str:
        return min(available, key=lambda k: self._usage.get(k, 0.0))

    def _recover_expired(self) -> None:
        now = time.monotonic()
        expired = [k for k, t in self._exhausted.items() if now - t >= self._cooldown]
        for k in expired:
            del self._exhausted[k]


class AllKeysExhausted(RuntimeError):
    pass


def _fingerprint(key: str) -> str:
    if len(key) <= 8:
        return "***"
    return key[:4] + "..." + key[-4:]
