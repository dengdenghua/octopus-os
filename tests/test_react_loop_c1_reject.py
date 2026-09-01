"""C1 regression: denied / user-rejected actions must be recorded, not dropped.

The approval-deny and user-reject branches in ``stream_react_loop`` used to
``continue`` after only setting a local ``observation``. The rejected action
never entered ``steps`` or ``messages``, so the next LLM call couldn't see the
rejection and re-emitted the same action until ``max_iter`` (livelock), and the
denial was invisible to the step trace. ``_record_rejected_step`` now appends
the step and surfaces the rejection to the model.
"""

from __future__ import annotations

from runtime.core.cerebrum.react_loop import _record_rejected_step
from runtime.core.cerebrum.react_types import ReActStep


def test_rejected_step_recorded_and_surfaced_to_model() -> None:
    step = ReActStep(iteration=1, action='exec_shell({"cmd": "rm -rf /"})')
    steps: list = []
    messages: list = []

    reason = "(工具被风险策略拒绝) 此操作被 approval risk policy 拒绝，请换一种方式或询问用户。"
    _record_rejected_step(steps, messages, step, reason)

    # (b) recorded in the step trace (no longer an audit blind spot)
    assert steps == [step]
    assert step.observation == reason

    # (a) surfaced to the model so it won't re-emit the same action (livelock)
    assert len(messages) == 2
    assert messages[0].role == "assistant"
    assert "exec_shell" in messages[0].content
    assert messages[1].role == "user"
    assert "拒绝" in messages[1].content
    assert "继续下一轮推理" in messages[1].content


def test_each_rejection_appends_its_own_record() -> None:
    steps: list = []
    messages: list = []

    _record_rejected_step(steps, messages, ReActStep(iteration=1, action="a()"), "denied-1")
    _record_rejected_step(steps, messages, ReActStep(iteration=2, action="b()"), "denied-2")

    assert len(steps) == 2
    assert [s.observation for s in steps] == ["denied-1", "denied-2"]
    assert len(messages) == 4  # 2 per rejection (assistant + observation)

