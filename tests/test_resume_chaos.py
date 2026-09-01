"""Chaos tests for the resume/checkpoint recovery path.

These inject deliberately corrupt or hostile checkpoints and assert two things:

1. **The integrity gate rejects them** — ``validate_checkpoint_state`` returns
   ``resume_safe=False`` with a precise error, so a corrupt snapshot can never
   be rehydrated into the loop.
2. **The downgrade is observable** — when resume fails, ``_resume_or_register_turn``
   falls back to a fresh run (``resume_from_iter=0``, ``resume_event=None``) AND
   emits a ``WARNING``-level log so ops can see a resume was attempted+rejected,
   rather than silently proceeding.

This is the "make silent degradation observable" guarantee for the checkpoint
recovery design (see ``docs/design/react-loop-split-plan.md``).
"""

from __future__ import annotations

import logging

import pytest

from runtime.core.cerebrum import checkpoint_integrity, pause_control, react_resume
from runtime.core.cerebrum.react_resume import _resume_or_register_turn

# ─────────────────────────────────────────────────────────────
# 1. Chaos injection matrix — the integrity gate must reject
#    every corrupt shape with a precise error.
# ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "state,error_substr",
    [
        # messages not a list
        ({"messages_snapshot": "not-a-list"}, "messages_snapshot_not_list"),
        # a message with an invalid role
        (
            {"messages_snapshot": [{"role": "admin", "content": "x"}]},
            "message_0_invalid_role",
        ),
        # a step with a non-numeric iteration
        (
            {
                "steps_snapshot": [{"iteration": "abc", "action": "read_file"}],
            },
            "step_0_invalid_iteration",
        ),
        # steps ahead of the checkpoint iteration (clock skew / hijack)
        (
            {"steps_snapshot": [{"iteration": 9, "action": "read_file"}]},
            "steps_ahead_of_checkpoint_iteration",
        ),
        # working_set not a list
        ({"working_set_snapshot": {"nope": 1}}, "working_set_snapshot_not_list"),
        # a non-dict message
        ({"messages_snapshot": [42]}, "message_0_not_object"),
    ],
)
def test_chaos_integrity_gate_rejects_corrupt_shapes(state, error_substr):
    report = checkpoint_integrity.validate_checkpoint_state(state, iteration=2)
    assert report.resume_safe is False
    assert error_substr in report.errors


def test_chaos_integrity_gate_rejects_promotion_attack():
    """A checkpoint whose steps claim a higher iteration than the checkpoint
    itself (e.g. to skip ahead / replay fresh work) must be rejected."""
    report = checkpoint_integrity.validate_checkpoint_state(
        {
            "messages_snapshot": [{"role": "user", "content": "hi"}],
            "steps_snapshot": [{"iteration": 100, "action": "exec_shell"}],
        },
        iteration=3,
    )
    assert report.resume_safe is False
    assert "steps_ahead_of_checkpoint_iteration" in report.errors


# ─────────────────────────────────────────────────────────────
# 2. Fallback behavior — a corrupt checkpoint must not block a
#    fresh run; it degrades to ``resume_from_iter=0``.
# ─────────────────────────────────────────────────────────────


class _FakePause:
    def __init__(self):
        self.registered = None
        self.granted = None
        self.cleared = None

    def register_active(self, *a, **k):
        self.registered = (a, k)

    def consume_grant(self, resume_task_id):
        self.granted = resume_task_id
        return {}

    def clear(self, resume_task_id):
        self.cleared = resume_task_id


class _FakeAgent:
    agent_id = "architect"


def _base_intent(**overrides):
    intent = {"user_context": {}}
    intent["user_context"].update(overrides)
    return type("Intent", (), {"user_context": intent["user_context"]})()


def _resume_or_register(
    *,
    resume_task_id="task-1",
    react_task_id="task-1",
    monkeypatch=None,
    load_impl=None,
):
    """Drive ``_resume_or_register_turn`` with a faked resume checkpoint that
    raises ``ValueError`` (corrupt), asserting the observable fallback."""
    pause = _FakePause()

    monkeypatch.setattr(pause_control, "get_pause_controller", lambda: pause)
    monkeypatch.setattr(
        react_resume,
        "_compute_resume_state",
        load_impl
        if load_impl is not None
        else lambda *a, **k: (_ for _ in ()).throw(ValueError("corrupt")),
    )
    monkeypatch.setattr(react_resume, "reset_injection_taint", lambda: None)
    monkeypatch.setattr(react_resume, "set_injection_gate_handled", lambda _v: None)
    monkeypatch.setattr(react_resume, "mark_injection_taint", lambda _v: None)

    out = _resume_or_register_turn(
        stack=object(),
        intent=_base_intent(),
        agent=_FakeAgent(),
        resume_task_id=resume_task_id,
        react_task_id=react_task_id,
        thread_id="thread-1",
        max_iterations=30,
        active_max_tokens_budget=1000,
        active_max_usd_budget=1.0,
        messages=["system"],
    )
    return out, pause


def test_chaos_corrupt_checkpoint_falls_back_to_fresh_run(monkeypatch, caplog):
    with caplog.at_level(logging.WARNING, logger="runtime.core.cerebrum.react_resume"):
        out, pause = _resume_or_register(
            resume_task_id="task-1",
            monkeypatch=monkeypatch,
        )

    # Fallback = fresh run: no resume, no rehydrated event.
    assert out.resume_from_iter == 0
    assert out.resume_event is None
    assert out.terminated_reason == "max_iter"
    assert out.final_answer is None
    # The pause controller still registered the task (so it can be paused).
    assert pause.registered is not None

    # Observable downgrade: a WARNING named the task + reason.
    assert any(
        "resume checkpoint rejected" in r.message and "task-1" in r.message for r in caplog.records
    )


def test_chaos_corrupt_checkpoint_warns_not_silent(monkeypatch, caplog):
    """The downgrade must be a WARNING (observable), not a silent debug."""
    with caplog.at_level(logging.DEBUG, logger="runtime.core.cerebrum.react_resume"):
        _resume_or_register(resume_task_id="task-9", monkeypatch=monkeypatch)

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings, "expected at least one WARNING for the rejected resume"
    assert any("task-9" in r.message for r in warnings)

