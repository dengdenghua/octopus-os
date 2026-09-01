"""Tests for incremental Thought streaming (TTFT fix).

Before the Final Answer anchor exists, the ReAct text protocol buffers all
LLM output so Thought/Action markup cannot leak into the visible answer —
which used to hide the Thought prose until the whole tool loop ended. The
loop now pulls Thought spans out of the growing buffer and streams them as
``thinking_delta`` (collapsible reasoning block), so tool-heavy turns show
signs of life after the first decoded tokens instead of after the last tool.
"""

from __future__ import annotations

from typing import Any

from runtime.core.cerebrum.react_loop import stream_react_loop
from runtime.core.cerebrum.react_parsing import (
    THOUGHT_STREAM_TAIL_MARGIN,
    extract_streamable_thought,
)
from tests.test_react_loop import (
    _build_stack_with_executor,
    _ChunkedCapturingRouter,
    _drain,
    _FakeStack,
    _intent,
    _ScriptedRouter,
)


def _extract_all(chunks: list[str]) -> str:
    """Feed chunks through the extractor incrementally, concatenating output."""
    out: list[str] = []
    cursor = 0
    in_thought = False
    joined = ""
    for chunk in chunks:
        joined += chunk
        text, cursor, in_thought = extract_streamable_thought(joined, cursor, in_thought)
        out.append(text)
    return "".join(out)


# ── extract_streamable_thought (pure) ─────────────────────────


def test_complete_thought_in_single_chunk() -> None:
    text, cursor, in_thought = extract_streamable_thought(
        "Thought: 先查资料再下结论\nAction: none", 0, False
    )
    assert text == "先查资料再下结论"
    assert in_thought is False
    assert cursor == len("Thought: 先查资料再下结论")


def test_unterminated_thought_respects_tail_margin() -> None:
    short = "Thought: 分析中"
    text, cursor, in_thought = extract_streamable_thought(short, 0, False)
    assert text == ""  # everything is within the tail margin
    assert in_thought is True

    grown = short + "x" * (THOUGHT_STREAM_TAIL_MARGIN + 10)
    text, cursor, in_thought = extract_streamable_thought(grown, cursor, in_thought)
    assert text == "分析中" + "x" * 10
    assert in_thought is True


def test_terminator_split_across_chunks_never_leaks() -> None:
    out = _extract_all(["Thought: 分析\nAct", "ion: none\nObservation: done"])
    assert out == "分析"
    assert "Act" not in out and "ion" not in out


def test_action_block_and_tool_envelope_never_emitted() -> None:
    out = _extract_all(
        [
            "Thought: 需要搜索\n",
            'Action: search({"query": "x"})\n',
            "Observation: found it\n",
            "Thought: 再看第二个来源\n",
            "<tool_call>{...}</tool_call>",
        ]
    )
    # Only the two Thought spans, in order — nothing else. (The span
    # before an XML envelope keeps its trailing newline; the reasoning
    # surface strips it when rendering.)
    assert out == "需要搜索再看第二个来源\n"


def test_multiple_thought_segments_skip_intervening_action() -> None:
    out = _extract_all(["Thought: aaa\nAction: none\nThought: bbb\nFinal Answer: ccc"])
    assert out == "aaabbb"


def test_no_marker_emits_nothing() -> None:
    assert _extract_all(["# 标题\n\n普通 markdown 回答，没有锚点。"]) == ""


def test_resume_inside_open_segment() -> None:
    out = _extract_all(
        [
            "Thought: " + "a" * 60,
            "b" * 60 + "\nAction: none",
        ]
    )
    assert out == "a" * 60 + "b" * 60


def test_final_answer_body_marker_is_region_bound() -> None:
    """A 'Thought:' quoted inside the answer must not surface as reasoning.

    Region bounding happens at the call site (react_loop slices the buffer
    at the anchor), so emulate that contract here: extraction only ever
    sees the pre-anchor prefix.
    """
    joined = "Thought: 真思考\nAction: none\n\nFinal Answer: 教训是 Thought: 要转义"
    anchor_at = joined.index("Final Answer:")
    out = _extract_all([joined[:anchor_at]])
    assert out == "真思考"
    assert "要转义" not in out


# ── loop-level ────────────────────────────────────────────────


def test_thought_streams_before_tool_execution() -> None:
    thought = "需要先搜索证据，再交叉验证两个来源的结论是否一致，然后才动手写答案"
    router = _ChunkedCapturingRouter(
        [
            f"Thought: {thought}\n" + 'Action: echo({"text": "证据A"})',
            "Final Answer: 调研完成",
        ],
        chunks_by_call={
            # First call arrives in provider-sized pieces: the Thought
            # decodes well before the Action line.
            1: [f"Thought: {thought}", "\nAction: echo", '({"text": "证据A"})'],
        },
    )
    stack = _build_stack_with_executor(router)

    # Neutral goal: research-flavoured intents ("调研…") activate the
    # evidence-convergence subsystem, which makes extra model calls its
    # own tests cover — here we isolate the streaming path.
    events, result = _drain(
        stream_react_loop(stack, _intent("echo 一下"), agent=None, max_iterations=3)
    )

    assert result is not None and result.final_answer == "调研完成"
    thinking = [e for e in events if e["type"] == "thinking_delta"]
    assert thinking, "Thought should stream as thinking_delta before the anchor"
    # The full Thought prose arrives, in order, with no Action markup.
    assert "".join(e["delta"] for e in thinking) == thought
    # TTFT: at least one thinking chunk lands before the tool starts.
    event_types = [e["type"] for e in events]
    assert event_types.index("thinking_delta") < event_types.index("tool_start")


def test_late_split_action_never_leaks_from_final_answer_stream() -> None:
    """A model may start with valid prose and append a text-protocol Action.

    The marker is deliberately split at ``Act``/``ion``.  The prose remains
    progressive, the tool executes once, and no part of the private call is
    exposed as an answer delta.
    """

    first = 'Final Answer: 阶段结论：当前文件可以正常读取。\nAction: echo({"text": "once"})'
    router = _ChunkedCapturingRouter(
        [first, "Final Answer: 已核对完成。"],
        chunks_by_call={
            1: [
                "Final Answer: 阶段结论：当前文件可以正常读取。",
                "\nAct",
                'ion: echo({"text": "once"})',
            ],
        },
    )
    stack = _build_stack_with_executor(router)

    events, result = _drain(
        stream_react_loop(stack, _intent("核对一次"), agent=None, max_iterations=3)
    )

    assert result is not None and result.final_answer == "已核对完成。"
    visible = "".join(
        str(event.get("delta") or "") for event in events if event.get("type") == "text_delta"
    )
    assert "阶段结论：当前文件可以正常读取" in visible
    assert "Action:" not in visible
    assert "echo(" not in visible
    tool_starts = [
        event
        for event in events
        if event.get("type") == "tool_start" and event.get("tool_name") == "echo"
    ]
    assert len(tool_starts) == 1


def test_visible_todo_does_not_buffer_safe_final_answer() -> None:
    """A checklist is UI coordination state, not a streaming safety gate.

    The old implementation buffered every final whenever todo_write was
    visible, making long task answers arrive as one late burst. A completed
    checklist must keep normal provider-sized answer deltas progressive.
    """

    answer = "任务结果已经整理完成，下面给出完整说明和可复核的最终结论。"
    router = _ChunkedCapturingRouter(
        [
            (
                "Thought: record progress\n"
                'Action: todo_write({"todos":[{"title":"整理结果","status":"completed"}]})'
            ),
            f"Final Answer: {answer}",
        ],
        chunks_by_call={
            2: [
                "Final Answer: 任务结果已经整理完成，",
                "下面给出完整说明和",
                "可复核的最终结论。",
            ]
        },
    )
    intent = _intent("协调并整理一份完整结果")
    intent.user_context["mode"] = "team"

    events, result = _drain(
        stream_react_loop(
            _build_stack_with_executor(router),
            intent,
            agent=None,
            max_iterations=3,
        )
    )

    assert result is not None and result.final_answer == answer
    deltas = [event["delta"] for event in events if event["type"] == "text_delta"]
    assert "".join(deltas) == answer
    assert len(deltas) >= 2
    assert deltas[0] != answer


def test_native_thinking_suppresses_text_thought_extraction() -> None:
    """Providers with a native thinking channel must not get the text
    Thought re-streamed on top (the two would duplicate)."""

    class _NativeThinkingRouter(_ScriptedRouter):
        def call_stream(self, req: Any):
            from runtime.sensing.model_router.models import (
                CostEntry,
                ModelResponse,
                ModelStreamEvent,
            )

            resp = self.call(req)
            yield ModelStreamEvent(type="thinking_delta", delta="native 思考。")
            for piece in ("Thought: 文本思考\n", "Action: none\n", "\nFinal Answer: 好了"):
                yield ModelStreamEvent(type="text_delta", delta=piece)
            yield ModelStreamEvent(
                type="done",
                final=ModelResponse(
                    text=resp.text,
                    model="test-model",
                    input_tokens=0,
                    output_tokens=0,
                    finish_reason="stop",
                    cost=CostEntry(),
                ),
            )

    router = _NativeThinkingRouter(["Thought: 文本思考\nAction: none\n\nFinal Answer: 好了"])
    events, result = _drain(
        stream_react_loop(_FakeStack(router), _intent("测试"), agent=None, max_iterations=2)
    )

    assert result is not None and result.final_answer == "好了"
    thinking = [e["delta"] for e in events if e["type"] == "thinking_delta"]
    assert thinking == ["native 思考。"]

