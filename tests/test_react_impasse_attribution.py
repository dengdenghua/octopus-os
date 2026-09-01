"""A guard impasse must not blame the tool-call format when nothing ran.

Regression cover for trn_c2fbddce247b4164: the turn produced zero
commandExecution items and zero tool-call-protocol-error injections, yet the
terminal message told the user their tool-call format was not recognised by
the execution layer. That misdiagnosis sends the user to fix a wire format
that was never exercised, and the model repeated the same non-action next turn.
"""

from __future__ import annotations

from runtime.core.cerebrum.react_final_answer_guards import (
    _guard_impasse_actionable_hint,
    _guard_impasse_final_answer,
    _guard_rejection_outcome,
    _trajectory_has_executed_action,
    guard_stall_kind,
)
from runtime.core.cerebrum.react_guards import _HARD_GUARD_LABELS
from runtime.core.cerebrum.react_types import ReActStep


def _step(**kw: object) -> ReActStep:
    kw.setdefault("iteration", 1)
    return ReActStep(**kw)  # type: ignore[arg-type]


def test_no_executed_action_is_an_action_deficit() -> None:
    steps = [_step(thought="I will inspect the file", action="", observation="")]
    assert _trajectory_has_executed_action(steps) is False


def test_failed_execution_still_counts_as_executed() -> None:
    """A failed run proves the wire format worked, so it is not a deficit."""
    steps = [_step(thought="t", action='read_file({"path": "x"})', observation="(tool failed)")]
    assert _trajectory_has_executed_action(steps) is True


def test_placeholder_actions_do_not_count() -> None:
    for placeholder in ("none", "N/A", "na", "  "):
        steps = [_step(thought="t", action=placeholder, observation="")]
        assert _trajectory_has_executed_action(steps) is False, placeholder


def test_action_deficit_hint_does_not_blame_tool_format() -> None:
    steps = [_step(thought="I will check the tests next", action="", observation="")]
    hint = _guard_impasse_actionable_hint("completeness guard", "only announces a future", steps)
    assert "没有真正执行任何工具调用" in hint
    # It must explicitly rule the format out, never assert it as the cause.
    assert "这不是工具格式或权限问题" in hint
    assert "未被执行层识别" not in hint


def test_executed_trajectory_keeps_the_generic_hint() -> None:
    steps = [_step(thought="t", action='read_file({"path": "x"})', observation="ok")]
    hint = _guard_impasse_actionable_hint("completeness guard", "some diagnostic", steps)
    assert "未被执行层识别" in hint


def test_omitting_steps_preserves_legacy_wording() -> None:
    """Call sites that pass no trajectory must behave exactly as before."""
    hint = _guard_impasse_actionable_hint("completeness guard", "some diagnostic")
    assert "未被执行层识别" in hint


def test_specific_hints_win_over_action_deficit() -> None:
    """A workspace-scope diagnosis is more useful than deficit wording."""
    steps = [_step(thought="t", action="", observation="")]
    hint = _guard_impasse_actionable_hint("evidence guard", "path_blocked: /etc/passwd", steps)
    assert "不在当前任务获准的工作区内" in hint
    assert "不代表执行沙箱已开启" in hint


def test_stall_kind_separates_deficit_from_evidence() -> None:
    narrating = [_step(thought="I will inspect it", action="", observation="")]
    acted = [_step(thought="t", action='read_file({"path": "x"})', observation="ok")]
    assert guard_stall_kind(narrating) == "action_deficit"
    assert guard_stall_kind(acted) == "evidence"


def test_hard_guards_keep_their_full_retry_budget_on_a_deficit() -> None:
    """Stall *classification* must not shorten a security guard's budget.

    An earlier iteration of this change tightened the limit for action
    deficits, which also tightened ``secret-leak guard`` and friends. Those
    must stay fail-closed at three regardless of why the loop stalled; the
    deficit is addressed by decode-level forcing, not by terminating sooner.
    """
    hard_label = sorted(_HARD_GUARD_LABELS)[0]
    narrating = [_step(thought="I will inspect it", action="", observation="")]
    state: dict = {}
    outcomes = [_guard_rejection_outcome(state, hard_label, narrating) for _ in range(3)]
    assert outcomes == ["retry", "retry", "hard_stop"]


def test_final_answer_threads_the_trajectory_through() -> None:
    steps = [_step(thought="I will look at it", action="", observation="")]
    answer = _guard_impasse_final_answer("completeness guard", "only announces", steps)
    assert "没有真正执行任何工具调用" in answer
    # The internal guard label must never surface as if it were the answer.
    assert "completeness guard" not in answer

