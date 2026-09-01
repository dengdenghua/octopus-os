"""Regression tests for the in-flight guards added in optimisation §15+§18.

The write-verification guard fires during the loop; the legacy completion
phrase hook is retained as telemetry-only compatibility:

* ``_completion_phrase_without_todo_guard`` — detects completion wording
  but never forces a checklist round.

* ``_unverified_write_followup_guard`` — code was written N steps ago
  with no verification (ruff/pytest/tsc/...). Nudge to verify before
  stacking more changes.

Both must be silent when the model is already doing the right thing
— extra nudges burn tokens without value.
"""

from __future__ import annotations

from runtime.core.cerebrum.react_guards import (
    _completion_phrase_without_todo_guard,
    _looks_like_completion_phrase,
    _unverified_write_followup_guard,
)
from runtime.core.cerebrum.react_types import ReActStep


def _step(
    iteration: int,
    *,
    thought: str = "",
    action: str = "",
    observation: str = "",
) -> ReActStep:
    return ReActStep(
        iteration=iteration,
        thought=thought,
        action=action,
        observation=observation,
    )


# ──────────────────────────────────────────────────────────────────
# §15 — completion-phrase nudge
# ──────────────────────────────────────────────────────────────────


class TestCompletionPhraseDetector:
    def test_english_done_triggers(self) -> None:
        assert _looks_like_completion_phrase("That's done!")

    def test_chinese_done_triggers(self) -> None:
        assert _looks_like_completion_phrase("好了，已完成所有 todo")
        assert _looks_like_completion_phrase("修好了，跑完测试就交")
        assert _looks_like_completion_phrase("搞定了，下一项")

    def test_neutral_progress_does_not_trigger(self) -> None:
        # "I'm working on" / "正在处理" — these are NOT completion claims.
        assert not _looks_like_completion_phrase("I'm working on the parser")
        assert not _looks_like_completion_phrase("正在处理第 3 个文件")
        assert not _looks_like_completion_phrase("Reading the test file now")

    def test_empty_safe(self) -> None:
        assert not _looks_like_completion_phrase("")
        assert not _looks_like_completion_phrase("   ")


class TestCompletionPhraseGuard:
    """The compatibility hook stays silent; receipts own completion."""

    def test_no_todo_protocol_no_nudge(self) -> None:
        # Free-form chat doesn't need checklists — guard stays silent.
        steps = [
            _step(1, thought="Done!", action='exec_shell({"cmd": "echo hi"})'),
        ]
        assert (
            _completion_phrase_without_todo_guard(
                steps,
                todo_protocol_required=False,
            )
            is None
        )

    def test_no_existing_todos_no_nudge(self) -> None:
        # First-turn warm-up: model hasn't built a checklist yet.
        # The Final-Answer-time guard catches "no todo at all"; we
        # don't pile on mid-flight.
        steps = [
            _step(1, thought="Done with discovery", action='read_file({"path": "a.py"})'),
        ]
        assert (
            _completion_phrase_without_todo_guard(
                steps,
                todo_protocol_required=True,
            )
            is None
        )

    def test_completion_phrase_after_real_action_is_telemetry_only(self) -> None:
        steps = [
            _step(
                1,
                action='todo_write({"items": [{"content": "Edit foo", "status": "in_progress"}]})',
                observation="ok",
            ),
            _step(
                2,
                thought="改好了，现在交差",
                action='exec_shell({"command": "ls"})',
                observation="foo.py",
            ),
        ]
        assert (
            _completion_phrase_without_todo_guard(
                steps,
                todo_protocol_required=True,
            )
            is None
        )

    def test_completion_phrase_followed_by_todo_write_silent(self) -> None:
        # The model already did the right thing — guard MUST stay quiet.
        steps = [
            _step(
                1,
                action='todo_write({"items": [{"content": "Edit foo", "status": "in_progress"}]})',
                observation="ok",
            ),
            _step(
                2,
                thought="改好了 foo",
                action='todo_write({"items": [{"content": "Edit foo", "status": "completed"}]})',
                observation="ok",
            ),
        ]
        assert (
            _completion_phrase_without_todo_guard(
                steps,
                todo_protocol_required=True,
            )
            is None
        )

    def test_all_todos_completed_silent(self) -> None:
        # Final-Answer guard handles wrap-up; mid-flight guard stops
        # nagging once everything is checked.
        steps = [
            _step(
                1,
                action='todo_write({"items": [{"content": "Edit foo", "status": "completed"}]})',
                observation="ok",
            ),
            _step(2, thought="All done", action="none"),
        ]
        assert (
            _completion_phrase_without_todo_guard(
                steps,
                todo_protocol_required=True,
            )
            is None
        )


# ──────────────────────────────────────────────────────────────────
# §18 — post-write verification nudge
# ──────────────────────────────────────────────────────────────────


class TestUnverifiedWriteGuard:
    def test_non_code_mode_no_nudge(self) -> None:
        steps = [
            _step(1, action='write_text_file({"path": "foo.py", "content": "x"})'),
            _step(2, action="none"),
            _step(3, action="none"),
            _step(4, action="none"),
            _step(5, action="none"),
            _step(6, action="none"),
            _step(7, action="none"),
        ]
        assert _unverified_write_followup_guard(steps, is_code_mode=False) is None

    def test_no_writes_no_nudge(self) -> None:
        steps = [_step(i, action="read_file") for i in range(1, 10)]
        assert _unverified_write_followup_guard(steps, is_code_mode=True) is None

    def test_recent_write_within_window_silent(self) -> None:
        # 3 steps ago — well within tolerance.
        steps = [
            _step(1, action='edit_file({"path": "a.py", "old_string": "x", "new_string": "y"})'),
            _step(2, action="read_file"),
            _step(3, action="read_file"),
            _step(4, action="read_file"),
        ]
        assert _unverified_write_followup_guard(steps, is_code_mode=True) is None

    def test_old_write_no_verification_nudge(self) -> None:
        # 7 steps ago, no verification — nudge fires.
        steps = [
            _step(1, action='edit_file({"path": "a.py", "old_string": "x", "new_string": "y"})'),
        ] + [_step(i, action="read_file") for i in range(2, 9)]
        # NB: read_file IS in the verification set (sanity probe), so
        # this case actually counts as verified — flip to a non-verify
        # action to test the nudge.
        steps = [
            _step(1, action='edit_file({"path": "a.py", "old_string": "x", "new_string": "y"})'),
        ] + [_step(i, action='todo_write({"items": []})') for i in range(2, 9)]
        msg = _unverified_write_followup_guard(steps, is_code_mode=True)
        assert msg is not None
        assert "verification" in msg.lower() or "verify" in msg.lower()

    def test_verification_after_write_silent(self) -> None:
        steps = [
            _step(1, action='edit_file({"path": "a.py", "old_string": "x", "new_string": "y"})'),
            _step(2, action='todo_write({"items": []})'),
            _step(3, action='exec_shell({"command": "pytest tests/test_a.py"})'),
        ] + [_step(i, action='todo_write({"items": []})') for i in range(4, 12)]
        # Write at step 1, exec_shell at step 3 → guard finds the
        # verification regardless of how many bare todo_writes follow.
        assert _unverified_write_followup_guard(steps, is_code_mode=True) is None
