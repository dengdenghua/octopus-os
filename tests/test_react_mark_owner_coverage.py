"""Dense coverage for react_loop owner-status mirror (audit Q-05)."""

from __future__ import annotations

from runtime.core.cerebrum import react_loop as rl


class _FakeStore:
    def __init__(self):
        self.busy = []
        self.idle = []

    def mark_thread_busy(self, thread_id):
        self.busy.append(thread_id)

    def mark_thread_idle(self, thread_id):
        self.idle.append(thread_id)


def test_mark_owner_thread_busy_idle(monkeypatch) -> None:
    store = _FakeStore()
    import runtime.execution.subagents.sessions as sess

    monkeypatch.setattr(sess, "get_subagent_session_store", lambda: store)
    rl._mark_subagent_owner_thread("t1", busy=True)
    rl._mark_subagent_owner_thread("t1", busy=False)
    assert store.busy == ["t1"]
    assert store.idle == ["t1"]
    rl._mark_subagent_owner_thread("", busy=True)  # empty -> no-op


def test_mark_owner_thread_best_effort(monkeypatch) -> None:
    import runtime.execution.subagents.sessions as sess

    monkeypatch.setattr(sess, "get_subagent_session_store", lambda: None)
    rl._mark_subagent_owner_thread("t1", busy=True)  # store None -> no-op

    def _boom():
        raise RuntimeError("nope")

    monkeypatch.setattr(sess, "get_subagent_session_store", _boom)
    rl._mark_subagent_owner_thread("t1", busy=True)  # failure swallowed


def test_sandbox_violation_detection() -> None:
    from runtime.core.cerebrum._react_execution_phase6d import (
        _can_escalate_sandbox,
        _looks_like_sandbox_violation,
    )

    assert _looks_like_sandbox_violation("sandbox_violation: /x") is True
    assert _looks_like_sandbox_violation("Sandbox-Violation detected") is True
    assert _looks_like_sandbox_violation("ordinary output") is False
    assert _looks_like_sandbox_violation(None) is False
    assert _can_escalate_sandbox("exec_shell") is True
    assert _can_escalate_sandbox("read_file") is False

