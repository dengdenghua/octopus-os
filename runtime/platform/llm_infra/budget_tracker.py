"""Session-level token & USD budget tracker.

The existing ``Budget`` class in ``runtime/platform/models/governance``
is a single-task atomic reserve/commit pair. This module adds a
*session-level* accumulator: one ``TokenBudgetTracker`` sums every
turn's actual token + USD cost for a single session_id, fires warnings
at 80% / 95% of the ceiling, and raises ``BudgetExceeded`` once the
ceiling is hit.

Design
------
* Pure in-memory dict of session_id → ``BudgetState`` guarded by one
  lock. A single daemon with 1000 active sessions uses < 1 MB.
* Separate ceilings for tokens and USD so a cheap-model burst
  doesn't trip the dollar limit first and vice versa.
* Warning thresholds fire **exactly once** each, not on every bump.
* Thread-safe. ``record(session_id, cost)`` is the hot path and takes
  ~2 μs on a modern laptop (single dict lookup + 4 adds + lock).

Usage
-----

    tracker = TokenBudgetTracker(
        tokens_ceiling=500_000,
        usd_ceiling=5.0,
        on_warning=lambda sid, st, pct: log.warn(...),
    )
    tracker.record("sess-1", CostEntry(tokens_in=100, tokens_out=50, usd=0.01))
    state = tracker.get("sess-1")
    # → BudgetState(tokens_used=150, usd_used=0.01, tokens_warn_hit=[], ...)

    # Raises BudgetExceeded when a record would push total above ceiling.
    # ``record`` is NOT atomic-reserve-style — it's a post-fact tally.
    # The caller is responsible for checking ``remaining()`` before
    # launching an expensive operation if pre-flight enforcement is
    # required.

Integration points
------------------
* Beak's ``execute_step`` after Budget.commit — record the actual
  step cost into the session-level tracker.
* Cerebrum's planner loop — check ``remaining_usd(sid)`` before
  starting a new plan iteration.
* SSE observability — expose ``snapshot_all()`` on the journal stream
  so the frontend can draw a per-session cost bar.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from runtime.platform.models import CostEntry

_LOG = logging.getLogger("echo.platform.budget_tracker")

# ── Warning thresholds (as fraction of ceiling) ────────────────
_WARN_THRESHOLDS: tuple[float, ...] = (0.80, 0.95)


class BudgetExceeded(RuntimeError):
    """Raised when a record() call would push usage past the ceiling."""

    def __init__(
        self,
        session_id: str,
        *,
        dimension: str,
        used: float,
        ceiling: float,
    ) -> None:
        super().__init__(
            f"budget exceeded for session {session_id!r}: "
            f"{dimension}={used:.4g} ceiling={ceiling:.4g}"
        )
        self.session_id = session_id
        self.dimension = dimension
        self.used = used
        self.ceiling = ceiling


@dataclass
class BudgetState:
    """Per-session cumulative usage snapshot."""

    session_id: str
    tokens_used: int = 0
    usd_used: float = 0.0
    call_count: int = 0
    # Which warning thresholds have already fired (per dimension).
    tokens_warn_hit: list[float] = field(default_factory=list)
    usd_warn_hit: list[float] = field(default_factory=list)
    tokens_ceiling: int | None = None
    usd_ceiling: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "tokens_used": self.tokens_used,
            "usd_used": self.usd_used,
            "call_count": self.call_count,
            "tokens_ceiling": self.tokens_ceiling,
            "usd_ceiling": self.usd_ceiling,
            "tokens_pct": (self.tokens_used / self.tokens_ceiling if self.tokens_ceiling else None),
            "usd_pct": (self.usd_used / self.usd_ceiling if self.usd_ceiling else None),
        }


class TokenBudgetTracker:
    """Session-level cumulative token + USD budget accumulator.

    Parameters
    ----------
    tokens_ceiling:
        Hard limit on total tokens (tokens_in + tokens_out across all
        turns) for a single session. ``None`` disables the limit.
    usd_ceiling:
        Hard limit on cumulative USD. ``None`` disables.
    on_warning:
        Callback ``(session_id, state, threshold_pct)`` fired once
        per threshold per dimension. Use it to log, emit an SSE event,
        or dispatch a notification.
    on_exceeded:
        Callback ``(session_id, state, dimension)`` fired right before
        ``BudgetExceeded`` is raised. Use it to notify a human / page
        on-call / write an audit event.
    """

    def __init__(
        self,
        *,
        tokens_ceiling: int | None = None,
        usd_ceiling: float | None = None,
        on_warning: Callable[[str, BudgetState, float], None] | None = None,
        on_exceeded: Callable[[str, BudgetState, str], None] | None = None,
    ) -> None:
        self._tokens_ceiling = tokens_ceiling
        self._usd_ceiling = usd_ceiling
        self._on_warning = on_warning
        self._on_exceeded = on_exceeded
        self._states: dict[str, BudgetState] = {}
        self._lock = threading.Lock()

    # ── public API ──────────────────────────────────────────

    def record(
        self,
        session_id: str,
        cost: CostEntry,
        *,
        tokens_ceiling: int | None = None,
        usd_ceiling: float | None = None,
    ) -> BudgetState:
        """Accumulate a cost entry into the session's budget.

        Optional ``tokens_ceiling`` / ``usd_ceiling`` override the
        tracker's global limits for THIS session only (on first record).
        Subsequent records for the same session keep the original
        ceiling — the first writer wins, which matches the typical
        flow where the session's ceiling is set when the session is
        created.

        Returns the updated ``BudgetState`` snapshot.

        Raises ``BudgetExceeded`` AFTER recording if either ceiling
        was crossed. The caller decides whether to abort the next
        operation or continue (post-fact mode).
        """
        if not session_id:
            raise ValueError("session_id required")
        tokens_delta = int(cost.tokens_in or 0) + int(cost.tokens_out or 0)
        usd_delta = float(cost.usd or 0.0)

        with self._lock:
            state = self._states.get(session_id)
            if state is None:
                state = BudgetState(
                    session_id=session_id,
                    tokens_ceiling=tokens_ceiling
                    if tokens_ceiling is not None
                    else self._tokens_ceiling,
                    usd_ceiling=usd_ceiling if usd_ceiling is not None else self._usd_ceiling,
                )
                self._states[session_id] = state

            state.tokens_used += tokens_delta
            state.usd_used += usd_delta
            state.call_count += 1

            # Snapshot for callbacks (avoid holding lock during them).
            fired_warnings: list[tuple[BudgetState, float]] = []
            exceeded: str | None = None

            if state.tokens_ceiling and state.tokens_ceiling > 0:
                pct = state.tokens_used / state.tokens_ceiling
                for threshold in _WARN_THRESHOLDS:
                    if pct >= threshold and threshold not in state.tokens_warn_hit:
                        state.tokens_warn_hit.append(threshold)
                        fired_warnings.append((state, threshold))
                if state.tokens_used >= state.tokens_ceiling:
                    exceeded = "tokens"

            if state.usd_ceiling and state.usd_ceiling > 0:
                pct = state.usd_used / state.usd_ceiling
                for threshold in _WARN_THRESHOLDS:
                    if pct >= threshold and threshold not in state.usd_warn_hit:
                        state.usd_warn_hit.append(threshold)
                        fired_warnings.append((state, threshold))
                if state.usd_used >= state.usd_ceiling and exceeded is None:
                    exceeded = "usd"

            # Make a copy so callbacks can't mutate the live state.
            snapshot = BudgetState(
                session_id=state.session_id,
                tokens_used=state.tokens_used,
                usd_used=state.usd_used,
                call_count=state.call_count,
                tokens_warn_hit=list(state.tokens_warn_hit),
                usd_warn_hit=list(state.usd_warn_hit),
                tokens_ceiling=state.tokens_ceiling,
                usd_ceiling=state.usd_ceiling,
            )

        # Fire callbacks OUTSIDE the lock so slow listeners don't block
        # the hot path.
        for st, thr in fired_warnings:
            try:
                if self._on_warning is not None:
                    self._on_warning(st.session_id, snapshot, thr)
            except Exception as exc:  # noqa: BLE001
                _LOG.warning("budget on_warning callback raised: %s", exc)

        if exceeded is not None:
            ceiling = snapshot.tokens_ceiling if exceeded == "tokens" else snapshot.usd_ceiling
            used: float = snapshot.tokens_used if exceeded == "tokens" else snapshot.usd_used
            try:
                if self._on_exceeded is not None:
                    self._on_exceeded(session_id, snapshot, exceeded)
            except Exception as exc:  # noqa: BLE001
                _LOG.warning("budget on_exceeded callback raised: %s", exc)
            raise BudgetExceeded(
                session_id,
                dimension=exceeded,
                used=used,
                ceiling=float(ceiling or 0),
            )

        return snapshot

    def get(self, session_id: str) -> BudgetState | None:
        """Return a copy of the session's current state, or None."""
        with self._lock:
            state = self._states.get(session_id)
            if state is None:
                return None
            return BudgetState(
                session_id=state.session_id,
                tokens_used=state.tokens_used,
                usd_used=state.usd_used,
                call_count=state.call_count,
                tokens_warn_hit=list(state.tokens_warn_hit),
                usd_warn_hit=list(state.usd_warn_hit),
                tokens_ceiling=state.tokens_ceiling,
                usd_ceiling=state.usd_ceiling,
            )

    def remaining(self, session_id: str) -> dict[str, float | None]:
        """Return remaining budget for a session.

        Shape::

            {"tokens": int | None, "usd": float | None}

        ``None`` means no ceiling was configured for that dimension.
        """
        with self._lock:
            state = self._states.get(session_id)
            if state is None:
                return {
                    "tokens": self._tokens_ceiling,
                    "usd": self._usd_ceiling,
                }
            return {
                "tokens": (
                    max(0, state.tokens_ceiling - state.tokens_used)
                    if state.tokens_ceiling
                    else None
                ),
                "usd": (
                    max(0.0, state.usd_ceiling - state.usd_used) if state.usd_ceiling else None
                ),
            }

    def reset(self, session_id: str | None = None) -> None:
        """Clear a single session or every session (when ``None``)."""
        with self._lock:
            if session_id is None:
                self._states.clear()
            else:
                self._states.pop(session_id, None)

    def snapshot_all(self) -> list[dict[str, Any]]:
        """Return a list of every session's state as dicts."""
        with self._lock:
            return [st.to_dict() for st in self._states.values()]


__all__ = [
    "BudgetExceeded",
    "BudgetState",
    "TokenBudgetTracker",
]
