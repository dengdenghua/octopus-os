"""Boundary tests for the workbench-snapshot terminal-state machinery.

These exercise edge cases the main e2e test in test_realtime_cerebrum.py
glosses over:

- INTERRUPTED status terminal-phase mapping
- FAILED status only marks the *first* non-done phase as error
- _workbench_status fallback when phases is empty
- _current_workbench_phase ordering when multiple statuses coexist
- finalize_workbench skipped when tools still pending (regression guard
  for the suspicious `if self.tools: return` early exit)
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from runtime.protocol import (
    AgentPhaseSnapshot,
    CommandExecutionItem,
    Turn,
    TurnParams,
    TurnStatus,
    WorkspaceFocus,
)
from runtime.sensing.gateway.realtime_cerebrum import (
    _current_workbench_phase,
    _ReactBridgeState,
    _terminal_workbench_phases,
    _workbench_snapshot,
    _workbench_status,
)

# ── _terminal_workbench_phases ─────────────────────────────────────


def _phase(
    *,
    pid: str,
    index: int,
    status: str,
    title: str = "phase",
    active: str | None = None,
) -> AgentPhaseSnapshot:
    return AgentPhaseSnapshot(
        id=pid,
        index=index,
        total=3,
        title=title,
        status=status,  # type: ignore[arg-type]
        activeItemId=active,
    )


def test_terminal_phases_completed_preserves_unfinished_truth() -> None:
    phases = [
        _phase(pid="p1", index=1, status="done"),
        _phase(pid="p2", index=2, status="running", active="cmd-1"),
        _phase(pid="p3", index=3, status="pending"),
    ]
    out = _terminal_workbench_phases(phases, TurnStatus.COMPLETED)
    assert [p.status for p in out] == ["done", "pending", "pending"]
    assert all(p.active_item_id is None for p in out), (
        "all active_item_id must clear on terminal completion"
    )


def test_terminal_phases_failed_marks_only_first_non_done_as_error() -> None:
    phases = [
        _phase(pid="p1", index=1, status="done"),
        _phase(pid="p2", index=2, status="running", active="cmd-1"),
        _phase(pid="p3", index=3, status="pending"),
    ]
    out = _terminal_workbench_phases(phases, TurnStatus.FAILED)
    # done phase preserved, first non-done becomes error, later phases left alone
    assert [p.status for p in out] == ["done", "error", "pending"]
    assert all(p.active_item_id is None for p in out)


def test_terminal_phases_failed_when_already_failed_at_running_keeps_done_only() -> None:
    """When the first phase is already running and we fail, exactly that
    phase becomes error — subsequent phases stay pending, not all error."""
    phases = [
        _phase(pid="p1", index=1, status="running", active="cmd-1"),
        _phase(pid="p2", index=2, status="pending"),
    ]
    out = _terminal_workbench_phases(phases, TurnStatus.FAILED)
    assert [p.status for p in out] == ["error", "pending"]


def test_terminal_phases_interrupted_running_becomes_pending() -> None:
    phases = [
        _phase(pid="p1", index=1, status="done"),
        _phase(pid="p2", index=2, status="running", active="cmd-1"),
    ]
    out = _terminal_workbench_phases(phases, TurnStatus.INTERRUPTED)
    # An interrupt is not an approval request; preserve done and park running.
    assert [p.status for p in out] == ["done", "pending"]
    assert all(p.active_item_id is None for p in out)


def test_terminal_phases_unknown_status_is_passthrough() -> None:
    """In_progress is not a terminal status; helper should return list as-is."""
    phases = [_phase(pid="p1", index=1, status="running")]
    out = _terminal_workbench_phases(phases, TurnStatus.IN_PROGRESS)
    assert out == phases


# ── _workbench_status ──────────────────────────────────────────────


def test_workbench_status_error_takes_precedence() -> None:
    phases = [
        _phase(pid="p1", index=1, status="done"),
        _phase(pid="p2", index=2, status="error"),
        _phase(pid="p3", index=3, status="running"),
    ]
    assert _workbench_status(phases) == "error"


def test_workbench_status_waiting_approval_over_running() -> None:
    phases = [
        _phase(pid="p1", index=1, status="running"),
        _phase(pid="p2", index=2, status="waiting_approval"),
    ]
    assert _workbench_status(phases) == "waiting_approval"


def test_workbench_status_done_only_when_all_done() -> None:
    phases = [
        _phase(pid="p1", index=1, status="done"),
        _phase(pid="p2", index=2, status="done"),
    ]
    assert _workbench_status(phases) == "done"


def test_workbench_status_empty_phases_returns_running_not_pending() -> None:
    """Currently the helper says running for empty phases — pinning that
    behavior so anyone tightening the literal type catches it via test."""
    assert _workbench_status([]) == "running"


# ── _current_workbench_phase ───────────────────────────────────────


def test_current_phase_prefers_running_over_pending() -> None:
    phases = [
        _phase(pid="p1", index=1, status="done"),
        _phase(pid="p2", index=2, status="pending"),
        _phase(pid="p3", index=3, status="running"),
    ]
    current = _current_workbench_phase(phases)
    assert current is not None and current.id == "p3"


def test_current_phase_falls_back_to_last_when_all_done() -> None:
    phases = [
        _phase(pid="p1", index=1, status="done"),
        _phase(pid="p2", index=2, status="done"),
    ]
    current = _current_workbench_phase(phases)
    assert current is not None and current.id == "p2"


def test_current_phase_none_when_empty() -> None:
    assert _current_workbench_phase([]) is None


# ── _workbench_snapshot ────────────────────────────────────────────


def test_snapshot_uses_focus_item_id_over_phase_active_item() -> None:
    phases = [
        _phase(pid="p1", index=1, status="running", active="from-phase"),
    ]
    focus = WorkspaceFocus(itemId="from-focus", view="terminal", title="t")
    snap = _workbench_snapshot(version=1, phases=phases, workspace_focus=focus)
    assert snap.current_item_id == "from-focus", (
        "WorkspaceFocus must win over phase.active_item_id when both are set"
    )


def test_snapshot_falls_back_to_phase_active_item_when_no_focus() -> None:
    phases = [_phase(pid="p1", index=1, status="running", active="cmd-1")]
    snap = _workbench_snapshot(version=2, phases=phases, workspace_focus=None)
    assert snap.current_item_id == "cmd-1"


def test_snapshot_current_item_none_when_neither_set() -> None:
    phases = [_phase(pid="p1", index=1, status="pending")]
    snap = _workbench_snapshot(version=3, phases=phases, workspace_focus=None)
    assert snap.current_item_id is None


# ── finalize_workbench guard ───────────────────────────────────────


class _StubLog:
    def turn_updated(self, *args, **kwargs) -> None:  # noqa: ARG002
        pass


class _StubEmitter:
    def __init__(self) -> None:
        self.notified: list[tuple[str, dict]] = []

    async def notify(self, method, params) -> None:  # noqa: ARG002
        self.notified.append((str(method), params))


def _make_turn() -> Turn:
    return Turn(
        id="turn-1",
        threadId="th-1",
        params=TurnParams(threadId="th-1", input=[{"type": "text", "text": "go"}]),
    )


@pytest.mark.asyncio
async def test_finalize_workbench_no_op_when_phases_empty() -> None:
    state = _ReactBridgeState()
    turn = _make_turn()
    emitter = _StubEmitter()
    await state.finalize_workbench(
        turn,
        _StubLog(),
        emitter,
        terminal_status=TurnStatus.COMPLETED,  # type: ignore[arg-type]
    )
    assert emitter.notified == []
    assert turn.workbench_snapshot is None


@pytest.mark.asyncio
async def test_finalize_workbench_emits_terminal_snapshot_even_with_pending_tools() -> None:
    """REGRESSION GUARD for the previous bug.

    Before this fix, finalize_workbench bailed when ``self.tools`` was
    non-empty (e.g. background tool watchers still attached). That left
    turns ending alongside long-lived watchers stuck at "running" in
    the UI forever.

    Now finalize_workbench should emit a terminal snapshot regardless
    — _terminal_workbench_phases clears every active_item_id so the
    UI stops highlighting watcher-owned items even though the watcher
    process keeps streaming output deltas.
    """
    state = _ReactBridgeState()
    state.phases = [_phase(pid="p1", index=1, status="running", active="cmd-1")]
    state.tools = {
        "cmd-1": CommandExecutionItem(
            id="cmd-1",
            command="sleep 9999",
            cwd=None,
            createdAt=datetime.now(UTC),
        )
    }
    turn = _make_turn()
    emitter = _StubEmitter()

    await state.finalize_workbench(
        turn,
        _StubLog(),  # type: ignore[arg-type]
        emitter,  # type: ignore[arg-type]
        terminal_status=TurnStatus.COMPLETED,
    )

    # Two notifications: turn/plan/updated + workbench/snapshot.
    methods = [m for m, _ in emitter.notified]
    assert "turn/plan/updated" in methods
    assert "workbench/snapshot" in methods
    # Turn now carries a terminal snapshot.
    assert turn.workbench_snapshot is not None
    assert turn.workbench_snapshot.status == "pending"
    # active_item_id cleared on the phase even though the watcher tool
    # is still in self.tools — this is the whole point of the fix.
    assert turn.workbench_snapshot.phases[0].active_item_id is None
    assert turn.workbench_snapshot.phases[0].status == "pending"


@pytest.mark.asyncio
async def test_finalize_workbench_idempotent_when_already_terminal() -> None:
    """When phases are already in terminal shape AND turn already has a
    snapshot, finalize_workbench should not double-emit."""
    state = _ReactBridgeState()
    state.phases = [_phase(pid="p1", index=1, status="done")]
    turn = _make_turn()
    # Pretend a snapshot was already issued earlier in this turn.
    turn.workbench_snapshot = _workbench_snapshot(
        version=5, phases=state.phases, workspace_focus=None
    )
    emitter = _StubEmitter()

    await state.finalize_workbench(
        turn,
        _StubLog(),  # type: ignore[arg-type]
        emitter,  # type: ignore[arg-type]
        terminal_status=TurnStatus.COMPLETED,
    )

    assert emitter.notified == [], "no re-emit when already terminal"
    assert turn.workbench_snapshot.version == 5, "version unchanged"


@pytest.mark.asyncio
async def test_version_resets_per_bridge_state_instance() -> None:
    """Document that workbench_snapshot_version is per-_ReactBridgeState
    (i.e. per-turn), not per-thread. Two state instances both start at 0."""
    s1 = _ReactBridgeState()
    s2 = _ReactBridgeState()
    assert s1.workbench_snapshot_version == 0
    assert s2.workbench_snapshot_version == 0

    # Drive s1's version up — s2 must stay at 0.
    s1.workbench_snapshot_version = 4
    assert s2.workbench_snapshot_version == 0
