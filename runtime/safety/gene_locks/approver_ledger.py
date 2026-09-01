from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

# Default window · within this many seconds, approvals count as
# "contemporaneous" for the same operation. 10 min is long enough
# for two human ops to coordinate over chat, short enough that
# stale signatures from last week don't accidentally satisfy
# quorum for a new operation.
DEFAULT_WINDOW_S = 600

# Cap on total remembered approvals · protects against a badly-
# behaved client filling the ledger. 500 entries × ~100 bytes ≈
# 50 KB, trivial for even an RK3588 edge device.
MAX_ENTRIES = 500


@dataclass
class ApprovalRecord:
    """One signature · operator name + what they were approving."""

    approver: str
    kind: str  # MutationKind string
    target: str  # recipe_id or "__global__"
    ts: float

    def matches(self, kind: str, target: str) -> bool:
        return self.kind == kind and self.target == target


@dataclass
class ApproverLedger:
    """Ring buffer of approvals · thread-safe."""

    window_s: float = DEFAULT_WINDOW_S
    max_entries: int = MAX_ENTRIES
    _entries: deque[ApprovalRecord] = field(
        default_factory=lambda: deque(maxlen=MAX_ENTRIES),
    )
    _lock: threading.RLock = field(default_factory=threading.RLock)

    def record(self, approver: str, kind: str, target: str) -> None:
        """Add a new approval · skipped when approver is empty."""
        a = (approver or "").strip()
        if not a:
            return
        with self._lock:
            self._entries.append(
                ApprovalRecord(
                    approver=a,
                    kind=kind,
                    target=target,
                    ts=time.time(),
                )
            )

    def count_distinct(
        self,
        kind: str,
        target: str,
        *,
        now: float | None = None,
    ) -> int:
        """Count distinct approvers who signed THIS (kind, target)
        within the current window. Used by the gate to decide if
        quorum is satisfied."""
        cutoff = (now if now is not None else time.time()) - self.window_s
        with self._lock:
            return len(
                {r.approver for r in self._entries if r.matches(kind, target) and r.ts >= cutoff}
            )

    def distinct_approvers(
        self,
        kind: str,
        target: str,
        *,
        now: float | None = None,
    ) -> list[str]:
        """The actual approver names · for audit / gate warning
        messages. Stable alphabetical order so test output is
        predictable."""
        cutoff = (now if now is not None else time.time()) - self.window_s
        with self._lock:
            names = {
                r.approver for r in self._entries if r.matches(kind, target) and r.ts >= cutoff
            }
        return sorted(names)

    def recent(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """Most-recent-first approvals · feeds the audit endpoint.
        Doesn't filter by window · the audit view wants to see
        stale signatures too ("Alice signed 2 days ago")."""
        with self._lock:
            out = [
                {
                    "approver": r.approver,
                    "kind": r.kind,
                    "target": r.target,
                    "ts": r.ts,
                }
                for r in reversed(self._entries)
            ]
        return out[:limit]

    def clear(self) -> int:
        """Wipe the ledger · returns the number of entries dropped.
        Useful for tests; not exposed via API."""
        with self._lock:
            n = len(self._entries)
            self._entries.clear()
            return n


# Module singleton.
_LEDGER: ApproverLedger | None = None
_LEDGER_LOCK = threading.RLock()


def get_ledger() -> ApproverLedger:
    global _LEDGER
    with _LEDGER_LOCK:
        if _LEDGER is None:
            _LEDGER = ApproverLedger()
        return _LEDGER


__all__ = ["ApprovalRecord", "ApproverLedger", "get_ledger", "DEFAULT_WINDOW_S"]
