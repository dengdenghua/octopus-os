"""Tests for model-authored public commentary.

Missing ``Update:`` checkpoints intentionally stay silent instead of
manufacturing repeated runtime prose. The frontend activity pulse and concrete
tool rows keep the turn visibly alive until a truthful model-authored update is
available.
"""

from __future__ import annotations

from runtime.core.cerebrum.react_loop import (
    stream_react_loop,
)
from tests.test_react_loop import (
    _build_stack_with_executor,
    _drain,
    _intent,
    _ScriptedRouter,
)


def test_missing_public_update_does_not_manufacture_commentary() -> None:
    """A missing checkpoint yields tool activity without canned assistant prose."""
    router = _ScriptedRouter(
        [
            'Thought: inspect source\nAction: echo({"text": "evidence"})',
            "Final Answer: evidence verified",
        ]
    )
    stack = _build_stack_with_executor(router)
    intent = _intent("inspect source")
    intent.user_context["mode"] = "react"

    events, result = _drain(stream_react_loop(stack, intent, agent=None, max_iterations=3))

    assert result is not None and result.final_answer == "evidence verified"
    commentary = [event for event in events if event["type"] == "commentary_delta"]
    assert commentary == []
    assert result.steps[0].public_update == ""


def test_model_supplied_update_is_not_replaced_by_runtime_fallback() -> None:
    """When the model DOES supply ``Update:``, the runtime fallback must not fire."""
    router = _ScriptedRouter(
        [
            (
                "Thought: inspect source\n"
                "Update: 已定位到证据源，下一步核对内容。\n"
                'Action: echo({"text": "evidence"})'
            ),
            "Final Answer: evidence verified",
        ]
    )
    stack = _build_stack_with_executor(router)
    intent = _intent("inspect source")
    intent.user_context["mode"] = "react"

    events, result = _drain(stream_react_loop(stack, intent, agent=None, max_iterations=3))

    assert result is not None and result.final_answer == "evidence verified"
    commentary = [event for event in events if event["type"] == "commentary_delta"]
    assert len(commentary) == 1
    # Model-supplied updates carry progress_source="model", not "runtime".
    assert commentary[0]["progress_source"] == "model"
    assert commentary[0]["delta"] == "已定位到证据源，下一步核对内容。"


def test_zero_anchor_update_not_leaked_into_answer_and_deduped_as_commentary() -> None:
    """Zero-anchor ``Update:`` checkpoint must not duplicate into the answer.

    Regression for thread txhjBkLKtmrjdfdJp0FQhN: the model wrote plain prose
    (no ``Final Answer:`` anchor) whose ``Update:`` paragraph streamed into the
    answer lane AND was re-emitted as a separate commentary message — the same
    sentence appeared twice. The checkpoint should appear exactly once, as
    commentary, while the intro stays in the answer.
    """
    from runtime.core.cerebrum.react_loop import stream_react_loop
    from tests.test_react_loop import (
        _build_stack_with_executor,
        _ChunkedCapturingRouter,
        _drain,
        _intent,
    )

    intro = (
        "我来帮你查这三组数据：智能床垫全球体量、传统床/床品全球体量、"
        "温度影响睡眠的科学依据，最后汇总成带来源的清单。"
    )
    update = (
        "我会分三路并行查证：智能床垫全球市场规模、传统床/床品全球市场规模、"
        "温度影响睡眠的科学依据，然后汇总成带来源的数据清单。"
    )
    action = 'Action: echo({"text": "search evidence"})'
    router = _ChunkedCapturingRouter(
        [
            f"{intro}\n\nUpdate: {update}\n\n{action}",
            "Final Answer: 三组数据已查证，来源如下。",
        ],
        chunks_by_call={
            1: [f"{intro}\n\n", f"Update: {update}\n\n", action],
        },
    )
    stack = _build_stack_with_executor(router)
    intent = _intent("查三组数据并给出来源")
    intent.user_context["mode"] = "react"

    events, result = _drain(stream_react_loop(stack, intent, agent=None, max_iterations=3))

    assert result is not None and result.final_answer == "三组数据已查证，来源如下。"
    answer = "".join(event["delta"] for event in events if event["type"] == "text_delta")
    # The generic future-intent opener is now buffered as a placeholder (the
    # completeness guard treats "我来帮你查…" as a plan, not a delivered
    # answer), so it must NOT appear in the answer lane at all — the user only
    # ever sees the real final answer.
    assert intro not in answer
    assert update not in answer
    assert "Update:" not in answer
    commentary = [
        event["delta"]
        for event in events
        if event["type"] == "commentary_delta" and event.get("progress_source") == "model"
    ]
    # ...but the concrete checkpoint must still be surfaced once as commentary.
    assert update in commentary
    all_visible = answer + "".join(commentary)
    assert all_visible.count(update) == 1

