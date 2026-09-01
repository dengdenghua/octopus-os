"""Shared mutable state for the computer-automation router family.

Split out of the former ~1994-line computer_router.py, which held this as
five separate local variables closed over by ~50 nested functions inside
one create_computer_router() factory. Bundling them into one dataclass
lets every sibling module (computer_lease.py, computer_control_session.py,
computer_replay_evidence.py, computer_runtime_readiness.py) take a single
explicit `state` parameter instead of relying on closure capture — the
same behavior, but each helper is now a plain, independently testable
module-level function.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from runtime.memory.control_sessions import ControlSessionStore
from runtime.sensing.gateway.waiting_escalation import WaitingEscalationWatchdog

# Shared with computer_control_session.py's _cleanup_pending — lives here
# (not in computer_router.py) so both can import it without a circular
# dependency between the main router module and its sibling.
_PENDING_TTL_SECONDS = 90


@dataclass
class ComputerRouterState:
    pending: dict[str, dict[str, Any]] = field(default_factory=dict)
    lease: dict[str, Any] = field(default_factory=dict)
    activity: list[dict[str, Any]] = field(default_factory=list)
    appshots: dict[str, dict[str, Any]] = field(default_factory=dict)
    screenshot_root: Path = field(
        default_factory=lambda: Path("data/computer_automation/screenshots").resolve()
    )
    control_sessions: ControlSessionStore = field(default_factory=ControlSessionStore)
    # Optional side-channel: waiting_user escalation watchdog. When set,
    # computer_control_session records waiting_user entries / resolutions so a
    # stalled operator approval can be pushed out-of-band (e.g. IM phone
    # notification). Pure side-effect; None keeps existing behavior.
    escalation: WaitingEscalationWatchdog | None = field(default=None, compare=False, repr=False)
    # Serializes lease claim/release. The router's endpoints are sync ``def``,
    # so FastAPI runs them in a threadpool and concurrent requests race on the
    # ``lease`` dict's check-then-act. Reentrant so the lease helpers can nest
    # (_claim_lease → _cleanup_lease/_public_lease) under one acquisition.
    lease_lock: threading.RLock = field(default_factory=threading.RLock, compare=False, repr=False)
    appshot_lock: threading.RLock = field(
        default_factory=threading.RLock,
        compare=False,
        repr=False,
    )


__all__ = ["ComputerRouterState", "_PENDING_TTL_SECONDS"]
