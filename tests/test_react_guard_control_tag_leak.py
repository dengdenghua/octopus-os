"""control-tag leak guard — internal control markers must not leak into the
user-visible final answer.

Some model providers (e.g. agnes-2.5-flash) occasionally echo internal control
markers — ``<system-reminder>`` todo lists, ``<system-prompt>`` fragments, or
private tool envelopes — as assistant text instead of keeping them in the
inference scaffolding layer. This guard rejects any final answer containing
these markers and nudges the model to continue working instead.

The rejection is **hard** (not advisory): delivering internal control text as
the user-facing answer is never acceptable, even on pure-research turns where
other protocol guards are relaxed.
"""

from __future__ import annotations

from runtime.core.cerebrum.react_final_answer_content_guards import _control_tag_leak_guard


def test_fires_on_system_reminder_tag() -> None:
    answer = "<system-reminder>\nThis is a reminder that your todo list is currently:\n1. Task one: in_progress\n2. Task two: pending\n</system-reminder>"
    msg = _control_tag_leak_guard(answer)
    assert msg is not None
    assert "internal control tag" in msg
    assert "<system-reminder>" in msg


def test_fires_on_system_prompt_tag() -> None:
    answer = "Here is the answer: <system-prompt>You are a helpful assistant</system-prompt>"
    msg = _control_tag_leak_guard(answer)
    assert msg is not None
    assert "internal control tag" in msg


def test_fires_on_tool_call_envelope() -> None:
    answer = 'The result is: <|tool_calls_start|>search({"q": "test"})<|tool_calls_end|>'
    msg = _control_tag_leak_guard(answer)
    assert msg is not None
    assert "internal control tag" in msg


def test_fires_on_literal_reminder_phrase() -> None:
    # agnes-2.5-flash echoes this exact phrasing (thread t293eeZgYDFq7uWVvfBhi2)
    answer = "This is a reminder that your todo list is currently: 1. Task A: in_progress, 2. Task B: pending"
    msg = _control_tag_leak_guard(answer)
    assert msg is not None
    assert "internal todo-list reminder" in msg


def test_no_fire_on_clean_answer() -> None:
    answer = "根据调研结果，智能睡眠市场规模达到 $2.3B，主要玩家包括 Oura、Whoop 和 Eight Sleep。"
    assert _control_tag_leak_guard(answer) is None


def test_no_fire_on_empty_answer() -> None:
    assert _control_tag_leak_guard("") is None
    assert _control_tag_leak_guard("   ") is None


def test_case_insensitive_tag_detection() -> None:
    answer = "Result: <SYSTEM-REMINDER>pending tasks</SYSTEM-REMINDER>"
    msg = _control_tag_leak_guard(answer)
    assert msg is not None


def test_no_fire_when_tag_mentioned_as_documentation() -> None:
    # A legitimate answer that *explains* the tag (not echoing it as the answer itself)
    # The guard fires on presence, not context — this is acceptable since control tags
    # should NEVER appear in final answers, even as documentation. If a user asks
    # "what is <system-reminder>", the model should explain without quoting the literal tag.
    answer = "The system uses a <system-reminder> tag internally for todo coordination."
    msg = _control_tag_leak_guard(answer)
    # Deliberately fires — even documentation use is rejected. If the user asks about
    # internal tags, the answer should paraphrase without literal quoting.
    assert msg is not None


def test_fires_on_im_start_end_tags() -> None:
    answer = "<|im_start|>assistant\nHere is the answer<|im_end|>"
    msg = _control_tag_leak_guard(answer)
    assert msg is not None


def test_fires_on_think_tags() -> None:
    # agnes-2.5-flash leaks internal reasoning (thread t5Rxjo_eyk8_HO2CGaN-se turn 3)
    answer = '你说得对，我漏掉了内容。\n\n{"command": "cat file.md"}\n```\n</think>\n\n继续执行...'
    msg = _control_tag_leak_guard(answer)
    assert msg is not None
    assert "internal control tag" in msg


def test_fires_on_opening_think_tag() -> None:
    answer = "<think>Let me analyze this...</think>\n\nThe answer is 42."
    msg = _control_tag_leak_guard(answer)
    assert msg is not None


def test_fires_on_original_user_request_envelope() -> None:
    answer = (
        "[original-user-request]\n"
        "Summarize the repository.\n"
        "[/original-user-request]\n"
        "The repository contains..."
    )
    msg = _control_tag_leak_guard(answer)
    assert msg is not None
    assert "internal prompt envelope" in msg
    assert "[original-user-request]" in msg


def test_fires_on_public_evidence_envelope() -> None:
    msg = _control_tag_leak_guard(
        "[just-completed-evidence]Result 1: completed[/just-completed-evidence]"
    )
    assert msg is not None
    assert "internal prompt envelope" in msg

