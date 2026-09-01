"""Unit tests for small pure helpers extracted out of ``stream_react_loop``.

``_finish_reason_is_length_limited`` (PHASE 6c) and ``_tool_call_succeeded``
(PHASE 6d) used to be inlined and duplicated inside the loop body; pulling them
out makes their contracts testable in isolation.
"""

from types import SimpleNamespace

import pytest

from runtime.core.cerebrum import react_action_outcomes
from runtime.core.cerebrum.react_loop import (
    _explicit_no_tool_goal,
    _finish_reason_is_length_limited,
    _has_unrecovered_beak_failure,
    _tool_call_succeeded,
)


@pytest.mark.parametrize(
    "goal",
    [
        "Do not use tools; answer directly.",
        "Reply without any tools.",
        "不要使用工具，只回答结果。",
        "直接回复，不用工具。",
    ],
)
def test_explicit_no_tool_goal(goal):
    assert _explicit_no_tool_goal(goal) is True


@pytest.mark.parametrize(
    "goal",
    [
        "Use the browser tool to verify this.",
        "只读分析两个文件，不要修改。",
        "直接回答后继续执行测试。",
    ],
)
def test_non_no_tool_goal(goal):
    assert _explicit_no_tool_goal(goal) is False


@pytest.mark.parametrize(
    "reason",
    [
        "length",
        "max_tokens",
        "max_output_tokens",
        "output_limit",
        "token_limit",
        "LENGTH",
        "  Max_Tokens  ",
    ],
)
def test_length_limited_finish_reasons(reason):
    assert _finish_reason_is_length_limited(reason) is True


@pytest.mark.parametrize("reason", ["stop", "end_turn", "", None, "tool_use"])
def test_non_length_limited_finish_reasons(reason):
    assert _finish_reason_is_length_limited(reason) is False


def test_tool_success_plain_observation():
    assert _tool_call_succeeded("all good", None) is True


def test_tool_success_none_observation():
    assert _tool_call_succeeded(None, None) is True


@pytest.mark.parametrize("obs", ["(工具失败) boom", "(工具执行异常) trace"])
def test_tool_failure_prefixed_observation(obs):
    assert _tool_call_succeeded(obs, None) is False


def test_beak_step_verdict_overrides_observation(monkeypatch):
    # A successful beak step wins even over a failure-prefixed observation.
    monkeypatch.setattr(react_action_outcomes, "_beak_step_effective_success", lambda s: True)
    assert _tool_call_succeeded("(工具失败) boom", object()) is True
    # A failed beak step overrides a clean-looking observation.
    monkeypatch.setattr(react_action_outcomes, "_beak_step_effective_success", lambda s: False)
    assert _tool_call_succeeded("looks fine", object()) is False


def _beak_step(name: str, *, status: str = "success") -> SimpleNamespace:
    return SimpleNamespace(
        action=SimpleNamespace(name=name),
        result=SimpleNamespace(status=status, output={}),
    )


def test_substantive_success_recovers_an_earlier_tool_failure():
    steps = [
        _beak_step("browser_navigate", status="failed"),
        _beak_step("exec_shell"),
    ]

    assert not _has_unrecovered_beak_failure(steps)


def test_bookkeeping_success_does_not_hide_an_unrecovered_tool_failure():
    steps = [
        _beak_step("browser_navigate", status="failed"),
        _beak_step("todo_write"),
    ]

    assert _has_unrecovered_beak_failure(steps)


# ─── Audit T-17: deadline closes the underlying model stream ────────────────


def test_model_deadline_closes_underlying_stream() -> None:
    """When the inactivity deadline fires, the pump's underlying stream must
    be closed so the provider connection aborts instead of lingering."""
    import threading
    import time

    from runtime.core.cerebrum.react_model_deadlines import (
        _MODEL_STREAM_DEADLINE,
        _iter_model_stream_with_deadline,
    )

    closed = threading.Event()

    class _FakeRouter:
        def call_stream(self, request):
            def gen():
                try:
                    while True:
                        time.sleep(0.01)
                        yield {"delta": "x"}
                finally:
                    closed.set()

            return gen()

    events = list(
        _iter_model_stream_with_deadline(
            _FakeRouter(),
            object(),
            timeout_s=0.15,
            visible_started=lambda: None,
            any_activity_counts=False,
        )
    )
    assert closed.wait(1), "underlying stream was not closed after the deadline"
    # The deadline marker is yielded before the generator returns.
    assert events and events[-1] is _MODEL_STREAM_DEADLINE


# ─── Audit T-14: default checkpoint interval is throttled ───────────────────


def test_default_checkpoint_interval_is_throttled(monkeypatch) -> None:
    """The shipped default writes a full snapshot every 10 iterations, not
    every iteration — an order of magnitude less disk I/O for long turns."""
    from runtime.core.cerebrum import react_checkpointing as rc

    monkeypatch.delenv("ECHO_CHECKPOINT_EVERY_N", raising=False)
    interval = rc._checkpoint_interval()
    assert interval == 10
    # The cadence helper fires on multiples of the interval.
    assert rc._should_auto_checkpoint(10, interval) is True
    assert rc._should_auto_checkpoint(5, interval) is False


# ─── Audit Q-05: react_model_stream pure helpers ─────────────────────────────


def test_stream_answer_body_extracts_final() -> None:
    from runtime.core.cerebrum.react_model_stream import _stream_answer_body

    assert _stream_answer_body("plain prose") == "plain prose"
    assert "the answer" in _stream_answer_body("Thought: x\nFinal Answer: the answer")


def test_safe_stream_end_holds_protocol_leader() -> None:
    from runtime.core.cerebrum.react_model_stream import _safe_stream_end

    assert _safe_stream_end("") == 0
    # Plain prose is released fully.
    assert _safe_stream_end("hello world") == len("hello world")
    # A possible "Action:" leader is held back until the parser sees it whole.
    assert _safe_stream_end("let's think\nAct") < len("let's think\nAct")
    assert _safe_stream_end("let's think\nAction: echo") < len("let's think\nAction: echo")


def test_stream_has_protocol_detection() -> None:
    from runtime.core.cerebrum.react_model_stream import _stream_has_protocol

    assert _stream_has_protocol("Action: echo hi") is True
    assert _stream_has_protocol("ordinary answer text") is False


def test_ambient_subagent_session_id_best_effort() -> None:
    from runtime.core.cerebrum.react_model_stream import _ambient_subagent_session_id

    # No sub-agent ambient set in this test thread -> "" (never raises).
    assert _ambient_subagent_session_id() == ""

