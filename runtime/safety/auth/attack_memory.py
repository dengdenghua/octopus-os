"""Antibody memory — the immunity protocol's Memory tier.

``protocols/immunity.md`` §Memory: sources that have attacked before
are rejected immediately on the next encounter, with hit accounting.
This module implements the minimum honest version of that tier:

* **Crystallization** — every hard rejection of a source is recorded;
  when one source collects ``threshold`` violations inside the sliding
  window, it crystallizes into an :class:`AttackPattern`.
* **Recall** — ``match()`` answers "is this a known attacker?" and
  bumps ``hit_count`` / ``last_hit`` on a hit, exactly the bookkeeping
  the protocol pseudocode describes.
* **Reporting API** — ``record_attack()`` lets external detectors
  (e.g. a future ``is_attack_like`` learn loop, or a human operator)
  upsert a pattern directly without going through the counter.
* **Persistence** — optional JSON file (atomic replace) so antibodies
  survive restarts; in-memory only when no path is given.

What this tier deliberately does NOT do: behavioural anomaly scoring
(z-scores over latency/tokens/args). That is the Adaptive tier and
remains unimplemented — see docs/implementation-status.md.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

_log = logging.getLogger("runtime.safety.auth.attack_memory")


@dataclass
class AttackPattern:
    entity_id: str
    reason: str = ""
    hit_count: int = 0
    first_seen: float = 0.0
    last_hit: float = 0.0


@dataclass
class _ViolationWindow:
    timestamps: list[float] = field(default_factory=list)
    last_reason: str = ""


class AttackMemory:
    """Thread-safe antibody store with sliding-window crystallization.

    Parameters
    ----------
    path :
        JSON persistence file. ``None`` keeps memory process-local.
    threshold :
        Violations inside ``window_s`` before a source crystallizes
        into a pattern. 1 means "one strike".
    window_s :
        Sliding window for violation counting (seconds).
    clock :
        Injectable time source for tests.
    """

    def __init__(
        self,
        path: Path | str | None = None,
        *,
        threshold: int = 3,
        window_s: float = 3600.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._path = Path(path) if path else None
        self._threshold = max(1, int(threshold))
        self._window_s = float(window_s)
        self._clock = clock
        self._lock = threading.Lock()
        self._patterns: dict[str, AttackPattern] = {}
        self._violations: dict[str, _ViolationWindow] = {}
        if self._path is not None:
            self._load()

    # ── Recall ───────────────────────────────────────────────

    def match(self, entity_id: str) -> AttackPattern | None:
        """Known attacker? Bumps hit accounting on a match."""
        with self._lock:
            pattern = self._patterns.get(entity_id)
            if pattern is None:
                return None
            pattern.hit_count += 1
            pattern.last_hit = self._clock()
            self._save_locked()
            return pattern

    # ── Crystallization ──────────────────────────────────────

    def record_violation(self, entity_id: str, reason: str = "") -> bool:
        """Count one violation; crystallize at the threshold.

        Returns True when this call promoted the source to a pattern.
        """
        now = self._clock()
        with self._lock:
            if entity_id in self._patterns:
                return False
            window = self._violations.setdefault(entity_id, _ViolationWindow())
            cutoff = now - self._window_s
            window.timestamps = [t for t in window.timestamps if t >= cutoff]
            window.timestamps.append(now)
            if reason:
                window.last_reason = reason
            if len(window.timestamps) < self._threshold:
                return False
            self._patterns[entity_id] = AttackPattern(
                entity_id=entity_id,
                reason=window.last_reason
                or f"{self._threshold} violations in {int(self._window_s)}s",
                first_seen=window.timestamps[0],
                last_hit=now,
            )
            del self._violations[entity_id]
            self._save_locked()
            _log.warning(
                "attack memory: %s crystallized (%d violations in window)",
                entity_id,
                self._threshold,
            )
            return True

    def record_attack(self, entity_id: str, reason: str) -> None:
        """Direct upsert — for external detectors / operators."""
        now = self._clock()
        with self._lock:
            existing = self._patterns.get(entity_id)
            if existing is not None:
                existing.reason = reason or existing.reason
                existing.last_hit = now
            else:
                self._patterns[entity_id] = AttackPattern(
                    entity_id=entity_id,
                    reason=reason,
                    first_seen=now,
                    last_hit=now,
                )
            # Promoted directly — drop any in-progress violation window
            # so it doesn't linger unbounded.
            self._violations.pop(entity_id, None)
            self._save_locked()

    def forget(self, entity_id: str) -> bool:
        """Operator escape hatch — remove a pattern (false positive)."""
        with self._lock:
            removed = self._patterns.pop(entity_id, None) is not None
            self._violations.pop(entity_id, None)
            if removed:
                self._save_locked()
            return removed

    def snapshot(self) -> list[dict]:
        """Read-only view for observability surfaces."""
        with self._lock:
            return [asdict(p) for p in self._patterns.values()]

    # ── Persistence ──────────────────────────────────────────

    def _load(self) -> None:
        if self._path is None or not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                # Valid JSON but wrong shape (top-level list/null/number):
                # raw.get below would raise AttributeError and escape the
                # except tuple, crashing startup. Treat as corrupt.
                raise ValueError("antibody file is not a JSON object")
            for entry in raw.get("patterns", []):
                if isinstance(entry, dict) and isinstance(entry.get("entity_id"), str):
                    self._patterns[entry["entity_id"]] = AttackPattern(
                        entity_id=entry["entity_id"],
                        reason=str(entry.get("reason", "")),
                        hit_count=int(entry.get("hit_count", 0)),
                        first_seen=float(entry.get("first_seen", 0.0)),
                        last_hit=float(entry.get("last_hit", 0.0)),
                    )
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            # A corrupt antibody file must not take the engine down;
            # starting clean only loses memory, not safety (innate
            # policy still applies).
            _log.warning("attack memory load failed (%s) · starting clean", exc)

    def _save_locked(self) -> None:
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(
                {"patterns": [asdict(p) for p in self._patterns.values()]},
                ensure_ascii=False,
                indent=2,
            )
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, self._path)
        except OSError as exc:
            _log.warning("attack memory save failed: %s", exc)


__all__ = ["AttackMemory", "AttackPattern"]
