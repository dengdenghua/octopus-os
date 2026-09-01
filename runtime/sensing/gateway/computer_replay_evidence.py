"""Replay-evidence summary for the computer-automation router family.

Split out of the former ~1994-line computer_router.py. A single small
function, but it's a cross-cutting dependency of both computer_lease.py
(conflict-error responses include a replay-evidence hint) and
computer_runtime_readiness.py (readiness capability rows) — pulled into
its own module so those two don't have to import from each other (which
would be circular: lease needs replay evidence for its error responses,
and control-session bookkeeping needs lease's ``_public_lease`` for
``_record_activity``'s default lease snapshot).
"""

from __future__ import annotations

from typing import Any

from runtime.safety.replay.browser_desktop_replay import computer_activity_replay_identity

from .computer_router_state import ComputerRouterState


def _computer_replay_evidence(state: ComputerRouterState, *, limit: int = 100) -> dict[str, Any]:
    items = [item for item in state.activity[-limit:] if isinstance(item, dict)]
    identity = computer_activity_replay_identity(
        items=items,
        pending_count=len(state.pending),
    )
    return {
        "schema": "echo.computer_replay_evidence_hint.v1",
        "case_id": identity["case_id"],
        "fingerprint": identity["fingerprint"],
        "replay_ready": bool(items),
        "replay_case_url": "/api/computer/activity/replay-case",
        "queue_url": "/api/computer/activity/replay-case/queue",
        "queue_body": {"limit": limit},
    }


__all__: list[str] = []
