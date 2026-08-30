from __future__ import annotations

import hashlib
import random
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from runtime.adapters.instrumentation import trace_stage


@dataclass
class Variant:
    name: str
    payload: Any
    weight: float = 1.0

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Variant.name must be non-empty")
        if self.weight <= 0:
            raise ValueError(f"Variant.weight must be > 0 (got {self.weight})")


@dataclass
class VariantStats:
    assignments: int = 0
    successes: int = 0
    failures: int = 0

    def record(self, *, success: bool) -> None:
        if success:
            self.successes += 1
        else:
            self.failures += 1

    @property
    def success_rate(self) -> float:
        total = self.successes + self.failures
        return self.successes / total if total > 0 else 0.0


class ABSplitter:
    def __init__(
        self,
        variants: Iterable[Variant],
        *,
        seed: int | None = None,
    ) -> None:
        self._variants: list[Variant] = list(variants)
        if not self._variants:
            raise ValueError("ABSplitter requires at least one variant")
        names = [v.name for v in self._variants]
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate variant names: {names}")

        self._total_weight: float = sum(v.weight for v in self._variants)
        self._rng = random.Random(seed) if seed is not None else random.Random()
        self.stats: dict[str, VariantStats] = {v.name: VariantStats() for v in self._variants}
        self._lock = threading.Lock()
        self._cum_weights: list[float] = []
        acc = 0.0
        for v in self._variants:
            acc += v.weight
            self._cum_weights.append(acc)

    @property
    def names(self) -> list[str]:
        return [v.name for v in self._variants]

    def get(self, name: str) -> Variant:
        for v in self._variants:
            if v.name == name:
                return v
        raise KeyError(f"no variant named {name!r}")

    def next_variant(self) -> Variant:
        with trace_stage("camouflage.assign") as span:
            r = self._rng.uniform(0, self._total_weight)
            v = self._pick_by_cum(r)
            self._record_assignment(v.name)
            span.set_attribute("echo.camouflage.variant", v.name)
            span.set_attribute("echo.camouflage.mode", "random")
            return v

    def assign_for(self, key: str | bytes) -> Variant:
        key_bytes = key.encode("utf-8") if isinstance(key, str) else bytes(key)
        digest = hashlib.blake2b(key_bytes, digest_size=8).digest()
        hash_int = int.from_bytes(digest, "big")
        r = (hash_int / 2**64) * self._total_weight
        v = self._pick_by_cum(r)
        with trace_stage("camouflage.assign") as span:
            self._record_assignment(v.name)
            span.set_attribute("echo.camouflage.variant", v.name)
            span.set_attribute("echo.camouflage.mode", "sticky")
            span.set_attribute("echo.camouflage.key_len", len(key_bytes))
        return v

    def record_outcome(self, variant_name: str, *, success: bool) -> None:
        with self._lock:
            stats = self.stats.get(variant_name)
            if stats is None:
                raise KeyError(f"unknown variant {variant_name!r}")
            stats.record(success=success)

    def _pick_by_cum(self, r: float) -> Variant:
        for v, cum in zip(self._variants, self._cum_weights, strict=False):
            if r < cum:
                return v
        return self._variants[-1]

    def _record_assignment(self, name: str) -> None:
        with self._lock:
            self.stats[name].assignments += 1
