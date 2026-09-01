"""End-to-end event-ordering conformance for the TTFT fixes.

Locks the combined event timeline of a full research-style task against
the six TTFT changes (2026-07-28), so the ongoing react_loop PHASE
refactors trip immediately if the streaming order regresses:

- ReAct text protocol: Thought prose streams as ``thinking_delta`` while
  the Final Answer is still buffered (``97d02ecdb``) — thinking must
  precede every tool row, and the long final answer must flow
  progressively, not dump.
- Native tool protocol: post-tool rounds stream text live
  (``eb3843bbb``) — the final synthesis flows before ``stats``/``done``.

Fake-provider simulations only; no wall-clock assertions (order and
content are the contract, not timing luck).
"""

from __future__ import annotations

from runtime.core.cerebrum.react_loop import stream_react_loop
from runtime.platform.models import ParsedIntent
from runtime.sensing.gateway.tool_bridge import stream_agentic_fallback
from runtime.sensing.model_router.models import (
    ModelResponse,
    ModelStreamEvent,
    ToolCall,
)
from tests.test_react_loop import (
    _build_stack_with_executor,
    _ChunkedCapturingRouter,
    _drain,
)
from tests.test_react_loop import (
    _intent as _react_intent,
)
from tests.test_tool_bridge_scope import _agent, _stack


def _kinds(events: list[dict]) -> list[str]:
    return [e["type"] for e in events]


# ── ReAct text protocol: full research-task timeline ──────────

THOUGHT_1 = "先梳理问题的关键维度，确定需要搜索的证据类型，再动手调用工具"
THOUGHT_2 = "第一个来源已经有结论了，现在需要交叉验证第二个来源是否一致"
ANSWER = "综合两个来源的证据，最终结论如下：首先……其次……" * 6


def test_react_research_task_event_ordering() -> None:
    router = _ChunkedCapturingRouter(
        [
            f"Thought: {THOUGHT_1}\n" + 'Action: echo({"text": "来源A证据"})',
            f"Thought: {THOUGHT_2}\n" + 'Action: echo({"text": "来源B证据"})',
            f"Final Answer: {ANSWER}",
        ],
        chunks_by_call={
            # Provider-sized pieces: Thought decodes well before Action.
            1: [f"Thought: {THOUGHT_1}", "\nAction: echo", '({"text": "来源A证据"})'],
            3: ["Final Answer: " + ANSWER[:30], ANSWER[30:90], ANSWER[90:]],
        },
    )
    stack = _build_stack_with_executor(router)

    events, result = _drain(
        # Neutral goal: research keywords (调研/研究/分析/流程…) arm the
        # evidence-convergence pass and research-report guards, which spend
        # extra model calls and would exhaust the 3-call script.
        stream_react_loop(stack, _react_intent("echo 串联演示"), agent=None, max_iterations=5)
    )

    assert result is not None and result.success
    assert result.final_answer == ANSWER
    kinds = _kinds(events)
    assert kinds[0] == "react_started"

    # 1. Every tool row is preceded by its Thought (TTFT fix #1).
    tool_starts = [i for i, k in enumerate(kinds) if k == "tool_start"]
    assert len(tool_starts) == 2
    thinking_at = [i for i, k in enumerate(kinds) if k == "thinking_delta"]
    assert thinking_at, "Thought should stream as thinking_delta"
    for ts in tool_starts:
        assert any(t < ts for t in thinking_at), "tool_start without preceding thinking"

    # 2. Thinking content is exactly the two Thoughts, in order, and
    #    carries no Action markup or tool JSON.
    thinking_text = "".join(e["delta"] for e in events if e["type"] == "thinking_delta")
    assert thinking_text == THOUGHT_1 + THOUGHT_2
    assert "Action" not in thinking_text and "echo({" not in thinking_text

    # 3. The second Thought streams BETWEEN the two tool executions.
    tool_ends = [i for i, k in enumerate(kinds) if k == "tool_end"]
    second_thought_at = max(
        i
        for i, e in enumerate(events)
        if e["type"] == "thinking_delta" and THOUGHT_2[:10] in e["delta"]
    )
    assert tool_ends[0] < second_thought_at < tool_starts[1]

    # 4. The long final answer flows progressively (anchor streaming) —
    #    more than one text_delta — and completes the turn.
    text_deltas = [e["delta"] for e in events if e["type"] == "text_delta"]
    assert "".join(text_deltas) == ANSWER
    assert len(text_deltas) >= 2, "final answer should stream, not dump"
    assert kinds[-1] == "react_completed"
    assert kinds.index("text_delta") > tool_ends[-1]


# ── Native tool protocol: post-tool synthesis streams live ─────


def _native_intent(goal: str, tmp_path) -> ParsedIntent:
    return ParsedIntent(
        raw=goal,
        intent_type="task",
        normalized_goal=goal,
        user_context={
            "conversation_id": "ttft-ordering-test",
            "metadata": {"mode": "code", "workspace_path": str(tmp_path)},
        },
    )


def test_native_research_task_event_ordering(tmp_path) -> None:
    synthesis = "基于收集到的全部证据，综合结论展开如下，逐条说明依据。" * 3

    class Router:
        def __init__(self):
            self.calls = 0

        def call_stream(self, _request):
            self.calls += 1
            if self.calls == 1:
                yield ModelStreamEvent(
                    type="tool_use",
                    tool_call=ToolCall(id="t1", name="list_cwd", input={"path": "."}),
                )
                yield ModelStreamEvent(type="done", final=ModelResponse(text=""))
                return
            # Final synthesis round: arrives in provider-sized pieces.
            for piece in (synthesis[:20], synthesis[20:70], synthesis[70:]):
                yield ModelStreamEvent(type="text_delta", delta=piece)
            yield ModelStreamEvent(type="done", final=ModelResponse(text=synthesis))

    events = list(
        stream_agentic_fallback(_stack(Router()), _native_intent("分析项目", tmp_path), _agent())
    )
    kinds = [event[0] for event in events]
    texts = [str(delta) for kind, delta, _ in events if kind == "text"]

    # Content invariant: exactly the synthesis, in order.
    assert "".join(texts) == synthesis
    # TTFT fix #4: the post-tool synthesis streams live (slice + tail
    # flush), after the last tool row and before stats/done.
    assert len(texts) >= 2, "synthesis should stream, not dump at round end"
    last_tool_end = max(i for i, k in enumerate(kinds) if k == "tool_end")
    first_text = kinds.index("text")
    assert first_text > last_tool_end
    assert first_text < kinds.index("stats") < kinds.index("done")

