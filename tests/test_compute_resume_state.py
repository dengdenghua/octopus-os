"""Unit tests for ``_compute_resume_state`` — the pure resume-rebuild helper
extracted out of ``stream_react_loop``'s PHASE 5.

Before the extraction this logic was welded into the loop's ~25-variable
closure and could only be exercised end-to-end. As a standalone pure function
its contract is testable in isolation: load → validate → rebuild loop state,
or ``None`` when there is nothing to resume, or ``ValueError`` when the
checkpoint is unsafe.
"""

import pytest

from runtime.core.cerebrum import checkpoint_integrity, react_resume
from runtime.core.cerebrum.react_context import (
    _restore_messages_from_checkpoint,
    _serialize_messages_for_checkpoint,
)
from runtime.core.cerebrum.react_loop import _compute_resume_state, _ResumeState
from runtime.platform.models.llm import Message


class _OkIntegrity:
    resume_safe = True
    errors: list[str] = []


class _BadIntegrity:
    resume_safe = False
    errors = ["corrupt step snapshot"]


def test_checkpoint_message_roundtrip_preserves_phase() -> None:
    messages = [
        Message(role="assistant", content="still working", phase="commentary"),
        Message(role="assistant", content="done", phase="final_answer"),
    ]

    restored = _restore_messages_from_checkpoint(
        _serialize_messages_for_checkpoint(messages),
    )

    assert [message.phase for message in restored] == ["commentary", "final_answer"]


def _snapshot(**overrides):
    base = {
        "source": "journal",
        "iteration_completed": 3,
        "messages_snapshot": [],
        "steps_snapshot": [],
        "has_final_answer": False,
        "final_answer": "",
        "working_set_snapshot": [],
        "progress_summary": "",
        "current_phase": "",
    }
    base.update(overrides)
    return base


def _call(**overrides):
    kwargs = {
        "base_messages": [],
        "base_working_set": {},
        "base_progress_summary": "",
        "base_current_phase": "understand",
        "max_iterations": 30,
    }
    kwargs.update(overrides)
    return _compute_resume_state(object(), object(), "task-1", **kwargs)


def test_returns_none_when_no_snapshot(monkeypatch):
    monkeypatch.setattr(react_resume, "_load_resume_checkpoint_snapshot", lambda *a, **k: None)
    assert _call() is None


def test_rebuilds_state_from_snapshot(monkeypatch):
    monkeypatch.setattr(
        react_resume,
        "_load_resume_checkpoint_snapshot",
        lambda *a, **k: _snapshot(
            iteration_completed=3,
            progress_summary="halfway",
            current_phase="build",
            steps_snapshot=[
                {"iteration": 1, "thought": "t", "action": "read_file", "observation": "o"}
            ],
            working_set_snapshot=[{"path": "x.py"}],
        ),
    )
    monkeypatch.setattr(
        checkpoint_integrity, "validate_checkpoint_state", lambda *a, **k: _OkIntegrity()
    )
    # Keep the message rehydration a no-op — it has its own tests and needs
    # real Message objects we don't want to fabricate here.
    monkeypatch.setattr(react_resume, "_rehydrate_messages_from_steps", lambda m, s: m)

    out = _call(base_messages=["system"])

    assert isinstance(out, _ResumeState)
    assert out.resume_from_iter == 3
    assert out.progress_summary == "halfway"
    assert out.current_phase == "build"
    assert len(out.steps) == 1
    assert out.steps[0].action == "read_file"
    assert out.working_set == {"x.py": {"path": "x.py"}}
    assert out.terminated_reason == "max_iter"
    assert out.resume_event["type"] == "react_resumed"
    assert out.resume_event["restored_step_count"] == 1
    assert out.resume_event["checkpoint_iteration"] == 3


def test_final_answer_checkpoint_short_circuits_loop(monkeypatch):
    monkeypatch.setattr(
        react_resume,
        "_load_resume_checkpoint_snapshot",
        lambda *a, **k: _snapshot(
            iteration_completed=5, has_final_answer=True, final_answer="done"
        ),
    )
    monkeypatch.setattr(
        checkpoint_integrity, "validate_checkpoint_state", lambda *a, **k: _OkIntegrity()
    )

    out = _call(max_iterations=30)

    assert out.final_answer == "done"
    assert out.terminated_reason == "final_answer"
    # resume_from_iter jumps to max_iterations so the for-loop body never runs.
    assert out.resume_from_iter == 30
    assert out.resume_event["has_final_answer"] is True


def test_unsafe_checkpoint_raises(monkeypatch):
    monkeypatch.setattr(
        react_resume, "_load_resume_checkpoint_snapshot", lambda *a, **k: _snapshot()
    )
    monkeypatch.setattr(
        checkpoint_integrity, "validate_checkpoint_state", lambda *a, **k: _BadIntegrity()
    )

    with pytest.raises(ValueError, match="unsafe checkpoint"):
        _call()

