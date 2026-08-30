"""Trajectory regression gate for the single-agent ReAct loop.

The existing replay machinery either *scores recorded runs*
(``trace_store.replay_gate``) or *probes prompt candidates in a sandbox*
(``native_replay_sandbox`` — which, per its own docstring, "does not rerun a
full LLM/tool loop yet"). Neither re-runs the actual loop to check the agent
still *behaves* the same after a code change.

This is that gate. Each golden case is a deterministic trajectory: a scripted
sequence of model turns (so there is no real LLM and no token cost), a set of
registered skills with real handlers, and the tool-call chain + final-answer
the loop is expected to produce. The harness re-runs ``stream_react_loop`` and
asserts the observed chain matches — so a regression in parsing, dispatch,
parallel fan-out, native tool-use, or final-answer handling fails CI loudly.

Add a case here whenever you fix or change loop behaviour; it pins the fix.
"""

from __future__ import annotations

from typing import Any

import pytest

from runtime.execution.suckers import Skill, SkillRegistry
from runtime.execution.tool_engine import ToolExecutor
from runtime.platform.models import ParsedIntent
from runtime.platform.models.llm import ToolCall
from runtime.safety.auth import TrustEngine
from runtime.sensing.model_router.models import (
    CostEntry,
    ModelResponse,
    ModelStreamEvent,
)

# ── deterministic replay router ──────────────────────────────────────


class _Caps:
    def __init__(self, supports_tool_use: bool) -> None:
        self.supports_tool_use = supports_tool_use


class _ReplayRouter:
    """Replays a scripted list of model turns.

    Each turn is either a ``str`` (text protocol) or a
    ``(text, [ToolCall])`` tuple (native tool-use). Deterministic — no real
    model, no tokens — so it isolates the *loop machinery* under test.
    """

    def __init__(
        self,
        turns: list[Any],
        *,
        supports_tool_use: bool = False,
    ) -> None:
        self.turns = turns
        self.calls = 0
        self.capabilities = _Caps(supports_tool_use)
        self.default_model = "test-model"

    def _resp(self) -> ModelResponse:
        turn = self.turns[min(self.calls, len(self.turns) - 1)]
        self.calls += 1
        finish_reason = "stop"
        if isinstance(turn, dict):
            # Advanced turn: lets a case drive finish_reason (e.g. "length"
            # to exercise the truncated-output continuation path).
            text = turn.get("text", "")
            calls = turn.get("tool_calls", [])
            finish_reason = turn.get("finish_reason", "stop")
        elif isinstance(turn, tuple):
            text, calls = turn
        else:
            text, calls = turn, []
        return ModelResponse(
            text=text,
            model="test-model",
            tool_calls=list(calls),
            finish_reason=finish_reason,
            cost=CostEntry(),
        )

    def call(self, _req: Any) -> ModelResponse:
        return self._resp()

    def call_stream(self, _req: Any):
        resp = self._resp()
        if resp.text:
            yield ModelStreamEvent(type="text_delta", delta=resp.text)
        yield ModelStreamEvent(type="done", final=resp)


class _Stack:
    def __init__(self, executor: ToolExecutor, router: _ReplayRouter) -> None:
        self.executor = executor

        class _Planner:
            planner_model = "test-model"

            def __init__(self, r: _ReplayRouter) -> None:
                self.router = r

        self.planner = _Planner(router)


def _echo(path: str = "", **_kw: Any) -> dict[str, Any]:
    return {"path": path, "content": f"[content of {path}]"}


def _fail(**_kw: Any) -> dict[str, Any]:
    raise RuntimeError("boom: simulated tool failure")


def _build_executor(skills: list[tuple[str, Any]]) -> ToolExecutor:
    reg = SkillRegistry()
    for name, handler in skills:
        reg.register(
            Skill(
                name=name,
                description=f"{name} — args: path (string). Returns dict.",
                trusted_source=f"builtin://{name}",
                handler=handler,
            ),
            verify_tests=False,
        )
    return ToolExecutor(
        registry=reg,
        immunity=TrustEngine(
            trusted_sources=["builtin://*"],
            unknown_policy="allow",
        ),
    )


def _run_trajectory(case: dict[str, Any], monkeypatch) -> tuple[list[str], str]:
    if case.get("native"):
        monkeypatch.setenv("ECHO_NATIVE_TOOLUSE", "1")
    else:
        monkeypatch.delenv("ECHO_NATIVE_TOOLUSE", raising=False)

    from runtime.core.cerebrum.react_loop import stream_react_loop

    executor = _build_executor(case.get("skills", []))
    router = _ReplayRouter(
        case["responses"],
        supports_tool_use=bool(case.get("native")),
    )
    stack = _Stack(executor, router)
    intent = ParsedIntent(
        raw=case["intent"],
        intent_type="task",
        normalized_goal=case["intent"],
        user_context={"auto_approve": True},
    )

    events: list[dict[str, Any]] = []
    result = None
    gen = stream_react_loop(
        stack,
        intent,
        agent=None,
        model="test-model",
        max_iterations=case.get("max_iter", 6),
    )
    try:
        while True:
            events.append(next(gen))
    except StopIteration as stop:
        result = stop.value

    tool_chain = [str(e.get("tool_name")) for e in events if e.get("type") == "tool_start"]
    final = (result.final_answer if result is not None else "") or ""
    return tool_chain, final


# ── golden corpus ────────────────────────────────────────────────────

GOLDEN_CASES: list[dict[str, Any]] = [
    {
        "id": "text_single_tool",
        "native": False,
        "skills": [("read_file", _echo)],
        "intent": "读取 a.yaml 并汇报",
        "responses": [
            'Thought: 读取文件\nAction: read_file({"path": "a.yaml"})',
            "Final Answer: 已读取 a.yaml,内容就绪。",
        ],
        "expect_tools": ["read_file"],
        "expect_final": "已读取",
    },
    {
        "id": "native_single_tool",
        "native": True,
        "skills": [("read_file", _echo)],
        "intent": "用原生工具读取 a.yaml",
        "responses": [
            ("", [ToolCall(id="t1", name="read_file", input={"path": "a.yaml"})]),
            ("Final Answer: 原生读取完成。", []),
        ],
        "expect_tools": ["read_file"],
        "expect_final": "原生读取完成",
    },
    {
        "id": "native_parallel_tools",
        "native": True,
        "skills": [("read_file", _echo)],
        "intent": "原生并发读取两个文件",
        "responses": [
            (
                "",
                [
                    ToolCall(id="t1", name="read_file", input={"path": "a"}),
                    ToolCall(id="t2", name="read_file", input={"path": "b"}),
                ],
            ),
            ("Final Answer: 两个文件都读了。", []),
        ],
        "expect_tools": ["read_file", "read_file"],
        "expect_final": "都读了",
    },
    {
        "id": "final_only_no_tools",
        "native": False,
        "skills": [],
        "intent": "打个招呼",
        "responses": ["Final Answer: 你好,我在。"],
        "expect_tools": [],
        "expect_final": "你好",
    },
    {
        # Multi-step sequencing: the second action is chosen after the first
        # tool's observation. Pins read→edit ordering through the dispatch.
        "id": "text_multi_step_sequence",
        "native": False,
        "skills": [("read_file", _echo), ("edit_file", _echo)],
        "intent": "先读 a.py 再改它",
        "responses": [
            'Thought: 先读\nAction: read_file({"path": "a.py"})',
            'Thought: 再改\nAction: edit_file({"path": "a.py"})',
            "Final Answer: 读改完成。",
        ],
        "expect_tools": ["read_file", "edit_file"],
        "expect_final": "读改完成",
    },
    {
        # Error path: the handler raises — the loop must surface an error
        # observation and recover to a final answer, not abort the turn.
        "id": "text_error_observation_recovery",
        "native": False,
        "skills": [("flaky", _fail)],
        "intent": "调用会失败的工具",
        "responses": [
            "Thought: 试一下\nAction: flaky({})",
            "Final Answer: 工具失败,已知悉并改道。",
        ],
        "expect_tools": ["flaky"],
        "expect_final": "已知悉",
    },
    {
        # Graceful degradation: the model names a tool that is not in the
        # registry — no dispatch fires and the loop still reaches a final
        # answer instead of crashing.
        "id": "text_unknown_tool_graceful",
        "native": False,
        "skills": [("read_file", _echo)],
        "intent": "调一个不存在的工具",
        "responses": [
            "Thought: 调\nAction: nonexistent_tool({})",
            "Final Answer: 该工具不存在,改用其它方案。",
        ],
        "expect_tools": [],
        "expect_final": "不存在",
    },
    {
        # Truncated-output continuation: finish_reason="length" with no final
        # must make the loop continue the response rather than bail.
        "id": "length_limit_continuation",
        "native": False,
        "skills": [],
        "intent": "写一份长报告",
        "responses": [
            {"text": "报告第一部分,内容尚未写完", "finish_reason": "length"},
            "Final Answer: 报告完整版,已续写完毕。",
        ],
        "expect_tools": [],
        "expect_final": "报告完整版",
    },
]


@pytest.mark.parametrize("case", GOLDEN_CASES, ids=[c["id"] for c in GOLDEN_CASES])
def test_trajectory_regression(case: dict[str, Any], monkeypatch) -> None:
    tool_chain, final = _run_trajectory(case, monkeypatch)
    assert tool_chain == case["expect_tools"], (
        f"{case['id']}: tool chain drifted — expected {case['expect_tools']}, got {tool_chain}"
    )
    assert case["expect_final"] in final, (
        f"{case['id']}: final answer drifted — {case['expect_final']!r} not in {final!r}"
    )


def test_guard_impasse_converges_on_real_loop(monkeypatch) -> None:
    """An unsatisfiable evidence guard must NOT let the loop burn its whole
    iteration budget and then misreport a pause. This drives the real
    stream_react_loop with a controlled replay router (no model) that keeps
    claiming an implementation is done while never writing a file — the
    exact livelock a live sweep exposed. The loop must terminate at the
    impasse bound (~3 rejections) far short of max_iter, and say so."""
    monkeypatch.delenv("ECHO_NATIVE_TOOLUSE", raising=False)
    from runtime.core.cerebrum.react_loop import stream_react_loop

    executor = _build_executor([("read_file", _echo)])  # a tool exists...
    # ...but the model never calls a write — it just re-asserts completion,
    # which the implementation-write guard rejects every single time.
    router = _ReplayRouter(["Final Answer: 已完成实现并通过验证。"])
    stack = _Stack(executor, router)
    intent = ParsedIntent(
        raw="修复并实现登录功能",
        intent_type="task",
        normalized_goal="修复并实现登录功能",
        user_context={"mode": "code", "auto_approve": True},
    )

    result = None
    gen = stream_react_loop(stack, intent, agent=None, model="test-model", max_iterations=30)
    try:
        while True:
            next(gen)
    except StopIteration as stop:
        result = stop.value

    # Terminated far short of the 30-iteration budget (impasse trips at 3
    # consecutive no-progress rejections; a handful of calls, not 30).
    assert router.calls <= 8, f"loop burned {router.calls} iterations before stopping"
    assert result is not None
    # And it tells the truth — an impasse report, not a "paused" fiction.
    # Wording tracks the shared guard-impasse template
    # (``react_final_answer_guards._guard_impasse_final_answer``).
    assert "已停止重复尝试" in (result.final_answer or "")

