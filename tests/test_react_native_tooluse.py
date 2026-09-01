"""Native tool-use path for the single-agent ReAct loop.

Covers the gate, the tool_calls→step synthesis, the Phase-1 prompt trim,
and the end-to-end loop wiring: when native mode is active the loop must
pass ``tools=`` to the model and drive itself off the structured
``tool_calls`` instead of regex-parsing the action out of text. When the
flag is off (the default) the loop must behave byte-identically — no tools
passed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from runtime.core.cerebrum.react_native import (
    build_loop_tool_specs,
    native_tool_use_active,
    native_tool_use_flag_enabled,
    require_public_update_on_tool_specs,
    step_from_tool_calls,
    trim_text_protocol_for_native,
)
from runtime.core.cerebrum.react_parsing import _latest_todo_items, _parse_action
from runtime.execution.suckers import Skill, SkillRegistry
from runtime.execution.suckers._write_skills_exec import _exec_shell
from runtime.execution.tool_engine import ToolExecutor
from runtime.platform.models import ParsedIntent
from runtime.platform.models.llm import ToolCall, ToolSpec
from runtime.safety.approval.cancellation import CancellationSource, scoped_cancellation
from runtime.safety.auth import TrustEngine
from runtime.sensing.model_router.models import (
    CostEntry,
    ModelResponse,
    ModelStreamEvent,
)

# ── unit: gate ───────────────────────────────────────────────────────


def test_flag_on_by_default(monkeypatch) -> None:
    # Default ON (validated against a live API). Only an explicit falsy
    # value forces the text protocol.
    monkeypatch.delenv("ECHO_NATIVE_TOOLUSE", raising=False)
    assert native_tool_use_flag_enabled() is True
    monkeypatch.setenv("ECHO_NATIVE_TOOLUSE", "0")
    assert native_tool_use_flag_enabled() is False


def test_gate_requires_capability_and_respects_escape_hatch(monkeypatch) -> None:
    class _Caps:
        supports_tool_use = True

    class _Router:
        capabilities = _Caps()

    monkeypatch.delenv("ECHO_NATIVE_TOOLUSE", raising=False)
    assert native_tool_use_active(_Router(), "m") is True  # default on + capable
    monkeypatch.setenv("ECHO_NATIVE_TOOLUSE", "0")
    assert native_tool_use_active(_Router(), "m") is False  # escape hatch forces off
    monkeypatch.setenv("ECHO_NATIVE_TOOLUSE", "1")
    assert native_tool_use_active(_Router(), "m") is True

    class _NoCap:
        pass

    monkeypatch.delenv("ECHO_NATIVE_TOOLUSE", raising=False)
    assert native_tool_use_active(_NoCap(), "m") is False  # capability gate still holds


def test_gate_resolves_dispatch_subrouter(monkeypatch) -> None:
    monkeypatch.setenv("ECHO_NATIVE_TOOLUSE", "1")

    class _Caps:
        supports_tool_use = True

    class _Sub:
        capabilities = _Caps()

    class _Dispatch:
        def _resolve(self, _model: str) -> Any:
            return _Sub()

    assert native_tool_use_active(_Dispatch(), "claude") is True


def test_browser_surface_keeps_late_registered_browser_tools_in_native_specs() -> None:
    registry = SkillRegistry()
    for index in range(60):
        registry.register(
            Skill(
                name=f"dummy_{index}",
                trusted_source=f"skill://test/dummy_{index}",
                handler=lambda **_kw: {},
            ),
            verify_tests=False,
        )
    for name in ("browser_navigate", "browser_state", "browser_type", "browser_click"):
        registry.register(
            Skill(
                name=name,
                affinity=["browser"],
                trusted_source=f"skill://test/{name}",
                handler=lambda **_kw: {},
            ),
            verify_tests=False,
        )

    specs = build_loop_tool_specs(
        SimpleNamespace(registry=registry),
        goal="operate the UI",
        user_context={"browser_surface": "browser", "runtime_surfaces": ["browser"]},
    )

    names = {spec.name for spec in specs}
    assert {"browser_navigate", "browser_state", "browser_type", "browser_click"} <= names


def test_strict_explicit_reads_remove_unrelated_native_tool_schemas() -> None:
    registry = SkillRegistry()
    for name in (
        "read_file",
        "read_file_range",
        "grep_text",
        "bb_read",
        "edit_file",
        "exec_shell",
        "git_commit",
        "browser_navigate",
        "web_search",
    ):
        registry.register(
            Skill(
                name=name,
                trusted_source=f"skill://test/{name}",
                handler=lambda **_kw: {},
            ),
            verify_tests=False,
        )

    specs = build_loop_tool_specs(
        SimpleNamespace(registry=registry),
        goal="只读比较 src/a.py 与 src/b.ts，不要修改文件。",
        user_context={"mode": "code"},
        strict_explicit_reads=True,
    )

    assert [spec.name for spec in specs] == [
        "grep_text",
        "read_file",
        "read_file_range",
    ]


# ── unit: tool_calls → step synthesis ────────────────────────────────


def test_step_from_tool_calls_does_not_guess_public_update_from_provider_text() -> None:
    step = step_from_tool_calls(
        [ToolCall(id="a", name="read_file", input={"path": "x.py"})],
        text="reading",
        iteration=2,
    )
    assert step.actions == ['read_file({"path": "x.py"})']
    assert step.action == 'read_file({"path": "x.py"})'
    assert step.thought == ""
    assert step.public_update == ""


def test_native_tool_schema_requires_model_authored_public_update() -> None:
    original = ToolSpec(
        name="read_file",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    )

    augmented = require_public_update_on_tool_specs([original])[0]

    assert augmented.input_schema["required"] == ["path", "public_update"]
    assert augmented.input_schema["properties"]["public_update"]["type"] == "string"
    assert augmented.input_schema["properties"]["public_update"]["maxLength"] == 420
    assert "public_update" not in original.input_schema["properties"]


def test_native_evidence_round_requires_fact_and_next_action_separately() -> None:
    original = ToolSpec(
        name="read_file",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    )

    augmented = require_public_update_on_tool_specs(
        [original],
        evidence_round=True,
    )[0]

    assert augmented.input_schema["required"] == [
        "path",
        "confirmed_fact",
        "next_action",
    ]
    assert "actual finding" in augmented.input_schema["properties"]["confirmed_fact"]["description"]
    assert "public_update" not in augmented.input_schema["properties"]


def test_structured_public_update_is_displayed_but_not_sent_to_tool() -> None:
    step = step_from_tool_calls(
        [
            ToolCall(
                id="a",
                name="read_file",
                input={
                    "path": "x.py",
                    "public_update": "我先核对 x.py 的实际定义，再给出结论。",
                },
            )
        ],
        iteration=1,
    )

    assert step.public_update == "我先核对 x.py 的实际定义，再给出结论。"
    assert step.action == 'read_file({"path": "x.py"})'


def test_structured_public_update_beats_incidental_provider_text() -> None:
    step = step_from_tool_calls(
        [
            ToolCall(
                id="a",
                name="read_file",
                input={
                    "path": "items.py",
                    "public_update": "我先核对协议定义，确认事件生命周期。",
                },
            )
        ],
        text=(
            "Optional[float] = None\n"
            "def __post_init__(self):\n"
            "    raise ValueError('provider context echo')"
        ),
        iteration=1,
    )

    assert step.public_update == "我先核对协议定义，确认事件生命周期。"
    assert "Optional[float]" not in step.public_update


def test_structured_evidence_update_precedes_generic_text_and_is_not_dispatched() -> None:
    step = step_from_tool_calls(
        [
            ToolCall(
                id="a",
                name="read_file",
                input={
                    "path": "frontend.ts",
                    "confirmed_fact": "后端 Item 采用 started 到 completed 的统一生命周期。",
                    "next_action": "接着核对前端是否按 itemId 归并",
                },
            )
        ],
        text="正在读取下一批文件。",
        iteration=2,
    )

    assert step.public_update == (
        "后端 Item 采用 started 到 completed 的统一生命周期；接着核对前端是否按 itemId 归并"
    )
    assert step.action == 'read_file({"path": "frontend.ts"})'


def test_evidence_round_drops_generic_provider_text_without_structured_fact() -> None:
    step = step_from_tool_calls(
        [ToolCall(id="a", name="read_file", input={"path": "frontend.ts"})],
        text="接下来读取另一个文件。",
        iteration=2,
        evidence_round=True,
    )

    assert step.public_update == ""
    assert step.action == 'read_file({"path": "frontend.ts"})'


def test_explicit_update_in_reasoning_channel_is_recovered_without_leaking_thought() -> None:
    step = step_from_tool_calls(
        [ToolCall(id="a", name="read_file", input={"path": "frontend.ts"})],
        text="",
        thinking=(
            "这里是不能公开的内部分析。\n\n"
            "Update: 第二批确认适配层按 Item 身份保持引用稳定；接着核对滚动容器。\n\n"
            'Action:\nread_file({"path":"frontend.ts"})'
        ),
        iteration=3,
    )

    assert step.public_update == ("第二批确认适配层按 Item 身份保持引用稳定；接着核对滚动容器。")
    assert "内部分析" not in step.public_update


def test_step_from_tool_calls_parallel() -> None:
    step = step_from_tool_calls(
        [
            ToolCall(id="a", name="read_file", input={"path": "x.py"}),
            ToolCall(id="b", name="web_search", input={"q": "echo"}),
        ],
        iteration=1,
    )
    assert len(step.actions) == 2
    assert step.action.count(";") == 1  # joined for the parallel dispatch


def test_step_from_tool_calls_skips_nameless() -> None:
    step = step_from_tool_calls(
        [ToolCall(id="a", name="", input={})],
        iteration=1,
    )
    assert step.actions == []


def test_step_from_tool_calls_todo_write_is_introspectable() -> None:
    step = step_from_tool_calls(
        [
            ToolCall(
                id="a",
                name="todo_write",
                input={"items": [{"content": "Fix bug", "status": "completed"}]},
            )
        ],
        iteration=1,
    )
    items = _latest_todo_items([step])
    assert len(items) == 1
    assert items[0]["content"] == "Fix bug"
    assert items[0]["status"] == "completed"


def test_latest_todo_items_finds_todo_write_in_parallel_native_calls() -> None:
    step = step_from_tool_calls(
        [
            ToolCall(id="a", name="read_file", input={"path": "x.py"}),
            ToolCall(
                id="b",
                name="todo_write",
                input={
                    "items": [
                        {"content": "Read x.py", "status": "completed"},
                        {"content": "Update y.py", "status": "in_progress"},
                    ]
                },
            ),
        ],
        iteration=1,
    )
    # Joined action is not parseable as a single action, but individual
    # actions in step.actions must still be introspected.
    assert _parse_action(step.action) is None
    items = _latest_todo_items([step])
    assert len(items) == 2
    assert items[0]["content"] == "Read x.py"
    assert items[1]["content"] == "Update y.py"


# ── unit: Phase-1 prompt trim ────────────────────────────────────────


def test_trim_text_protocol_drops_scaffold() -> None:
    prompt = (
        "你是助手。\nThought: 当前思考\nAction: skill({})\n"
        "Observation: <由系统填入>\n后续政策说明。"
    )
    trimmed = trim_text_protocol_for_native(prompt)
    assert "Action: skill" not in trimmed
    assert "你是助手。" in trimmed
    assert "后续政策说明。" in trimmed
    assert "原生工具调用" in trimmed


def test_trim_is_noop_when_anchors_absent() -> None:
    prompt = "无任何 ReAct 锚点的提示。"
    assert trim_text_protocol_for_native(prompt) == prompt


# ── integration: loop wiring ─────────────────────────────────────────


@dataclass
class _Reg:
    # has → False keeps the dispatch off the executor entirely: we only
    # assert the native wiring (tools passed + tool_calls consumed), not
    # real skill execution (covered by the existing dispatch tests).
    def has(self, _name: str) -> bool:
        return False

    def is_enabled(self, _name: str) -> bool:
        return True

    def iter_skills(self) -> list[Any]:
        return []

    def iter_agents(self) -> list[Any]:
        return []


@dataclass
class _Exec:
    registry: Any = field(default_factory=_Reg)
    agent_registry: Any = field(default_factory=_Reg)


class _Caps:
    supports_tool_use = True


class _Router:
    """Turn-scripted router. Each turn is ``(text, tool_calls)``."""

    def __init__(self, turns: list[tuple[str, list[ToolCall]]]) -> None:
        self.turns = turns
        self.calls = 0
        self.requests: list[Any] = []
        self.capabilities = _Caps()

    def call(self, req: Any) -> ModelResponse:
        self.requests.append(req)
        text, calls = self.turns[min(self.calls, len(self.turns) - 1)]
        self.calls += 1
        return ModelResponse(
            text=text,
            model="test-model",
            tool_calls=list(calls),
            finish_reason="stop",
            cost=CostEntry(),
        )

    def call_stream(self, req: Any):
        resp = self.call(req)
        if resp.text:
            yield ModelStreamEvent(type="text_delta", delta=resp.text)
        yield ModelStreamEvent(type="done", final=resp)


class _Planner:
    def __init__(self, router: _Router) -> None:
        self.router = router
        self.planner_model = "test-model"


class _Stack:
    def __init__(self, router: _Router) -> None:
        self.planner = _Planner(router)
        self.executor = _Exec()


def _intent(goal: str = "读取配置文件") -> ParsedIntent:
    return ParsedIntent(
        raw=goal,
        intent_type="task",
        normalized_goal=goal,
        user_context={},
    )


def test_native_mode_passes_tools_and_consumes_tool_calls() -> None:
    from runtime.core.cerebrum.react_loop import run_react_loop

    router = _Router(
        [
            ("", [ToolCall(id="t1", name="read_file", input={"path": "config.yaml"})]),
            ("Final Answer: 已读取配置。", []),
        ]
    )
    fake_spec = ToolSpec(name="read_file", description="read a file")
    with (
        patch(
            "runtime.core.cerebrum.react_native.native_tool_use_active",
            return_value=True,
        ),
        patch(
            "runtime.core.cerebrum.react_native.build_loop_tool_specs",
            return_value=[fake_spec],
        ),
    ):
        result = run_react_loop(
            _Stack(router),
            _intent(),
            agent=None,
            max_iterations=5,
        )

    # Native turn passed a non-empty tools list to the model.
    assert any(getattr(r, "tools", None) for r in router.requests), "native mode must pass tools="
    # The loop consumed the tool_calls (turn 1) and continued to the final
    # answer (turn 2) — i.e. it drove off the structured calls, not text.
    assert router.calls >= 2
    # The empty-prose tool-use turn was recorded in history as the
    # synthesised action, not an (API-invalid) empty assistant message.
    turn2_messages = router.requests[1].messages
    assert any(
        getattr(m, "role", "") == "assistant" and "read_file" in str(getattr(m, "content", ""))
        for m in turn2_messages
    ), "turn-1 tool call should appear in the assistant history"
    assert result is not None
    assert "已读取配置" in (result.final_answer or "")


def test_native_mixed_text_and_tool_call_is_atomic_and_not_replayed_as_answer() -> None:
    from runtime.core.cerebrum.react_loop import stream_react_loop

    unsupported = "Final Answer: 修复完成，13/13 tests passed。"
    public_update = "我先读取配置文件，确认磁盘上的实际内容。"
    router = _Router(
        [
            (
                unsupported,
                [
                    ToolCall(
                        id="t1",
                        name="read_file",
                        input={"path": "config.yaml", "public_update": public_update},
                    )
                ],
            ),
            ("Final Answer: 已依据读取结果完成核对。", []),
        ]
    )
    fake_spec = ToolSpec(
        name="read_file",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    )
    intent = _intent("读取 config.yaml 后给出结论")
    intent.user_context.update(
        {
            "realtime_public_orientation": True,
            "realtime_public_narrative": True,
        }
    )
    registry = SkillRegistry()
    registry.register(
        Skill(
            name="read_file",
            description="Read a fixture file.",
            trusted_source="builtin://read_file",
            handler=lambda **_kwargs: {"content": "fixture"},
            affinity=["files"],
        ),
        verify_tests=False,
    )
    stack = _Stack(router)
    stack.executor = _Exec(registry=registry)

    with (
        patch(
            "runtime.core.cerebrum.react_native.native_tool_use_active",
            return_value=True,
        ),
        patch(
            "runtime.core.cerebrum.react_native.build_loop_tool_specs",
            return_value=[fake_spec],
        ),
        patch(
            "runtime.core.cerebrum._react_execution_phase6d._execute_action_via_beak",
            return_value=("fixture", None),
        ),
    ):
        stream = stream_react_loop(stack, intent, agent=None, max_iterations=3)
        events: list[dict[str, Any]] = []
        while True:
            try:
                events.append(next(stream))
            except StopIteration as stop:
                result = stop.value
                break

    visible_answer = "".join(
        str(event.get("delta") or "") for event in events if event.get("type") == "text_delta"
    )
    assert unsupported not in visible_answer
    assert "13/13 tests passed" not in visible_answer
    assert "已依据读取结果完成核对" in visible_answer
    assert any(
        event.get("type") == "commentary_delta" and event.get("delta") == public_update
        for event in events
    )
    assert any(event.get("type") == "tool_start" for event in events)
    assert any(event.get("type") == "tool_end" for event in events)
    second_request_assistant_text = "\n".join(
        str(getattr(message, "content", "") or "")
        for message in router.requests[1].messages
        if getattr(message, "role", "") == "assistant"
    )
    assert "read_file" in second_request_assistant_text
    assert "13/13 tests passed" not in second_request_assistant_text
    assert result is not None and result.final_answer == "已依据读取结果完成核对。"


def test_native_cancel_before_done_discards_pending_answer_lane() -> None:
    from runtime.core.cerebrum.react_loop import stream_react_loop

    source = CancellationSource()

    class _CancelBeforeDoneRouter(_Router):
        def __init__(self) -> None:
            super().__init__([])

        def call_stream(self, req: Any):
            self.requests.append(req)
            yield ModelStreamEvent(
                type="text_delta",
                delta="Final Answer: 13/13 tests passed.",
            )
            source.cancel(reason="user stopped")
            return

    router = _CancelBeforeDoneRouter()
    fake_spec = ToolSpec(name="read_file", description="Read a file.")
    with (
        patch("runtime.core.cerebrum.react_native.native_tool_use_active", return_value=True),
        patch(
            "runtime.core.cerebrum.react_native.build_loop_tool_specs",
            return_value=[fake_spec],
        ),
        scoped_cancellation(source.token),
    ):
        stream = stream_react_loop(_Stack(router), _intent(), agent=None, max_iterations=3)
        events: list[dict[str, Any]] = []
        while True:
            try:
                events.append(next(stream))
            except StopIteration as stopped:
                result = stopped.value
                break

    visible_answer = "".join(
        str(event.get("delta") or "") for event in events if event.get("type") == "text_delta"
    )
    assert visible_answer == ""
    assert any(event.get("type") == "react_cancelled" for event in events)
    assert not any(event.get("type") == "tool_start" for event in events)
    assert result is None


def test_native_eof_without_done_discards_pending_answer_lane() -> None:
    from runtime.core.cerebrum.react_loop import stream_react_loop

    class _EofWithoutDoneRouter(_Router):
        def __init__(self) -> None:
            super().__init__([])

        def call_stream(self, req: Any):
            self.requests.append(req)
            yield ModelStreamEvent(
                type="text_delta",
                delta="Final Answer: 修复完成，13/13 tests passed。",
            )
            return

    router = _EofWithoutDoneRouter()
    fake_spec = ToolSpec(name="read_file", description="Read a file.")
    with (
        patch("runtime.core.cerebrum.react_native.native_tool_use_active", return_value=True),
        patch(
            "runtime.core.cerebrum.react_native.build_loop_tool_specs",
            return_value=[fake_spec],
        ),
        patch(
            "runtime.core.cerebrum.react_loop.next_custom_model_fallback",
            return_value=None,
        ) as fallback,
    ):
        stream = stream_react_loop(_Stack(router), _intent(), agent=None, max_iterations=3)
        events: list[dict[str, Any]] = []
        while True:
            try:
                events.append(next(stream))
            except StopIteration as stopped:
                result = stopped.value
                break

    visible_answer = "".join(
        str(event.get("delta") or "") for event in events if event.get("type") == "text_delta"
    )
    assert visible_answer == ""
    assert not any(event.get("type") == "tool_start" for event in events)
    assert any(
        event.get("type") == "react_error"
        and "terminal done event" in str(event.get("message") or "")
        for event in events
    )
    fallback.assert_called_once()
    assert result is None


def test_native_done_without_final_discards_pending_answer_lane() -> None:
    from runtime.core.cerebrum.react_loop import stream_react_loop

    class _DoneWithoutFinalRouter(_Router):
        def __init__(self) -> None:
            super().__init__([])

        def call_stream(self, req: Any):
            self.requests.append(req)
            yield ModelStreamEvent(
                type="text_delta",
                delta="Final Answer: 修复完成，13/13 tests passed。",
            )
            yield ModelStreamEvent(type="done")

    router = _DoneWithoutFinalRouter()
    fake_spec = ToolSpec(name="read_file", description="Read a file.")
    with (
        patch("runtime.core.cerebrum.react_native.native_tool_use_active", return_value=True),
        patch(
            "runtime.core.cerebrum.react_native.build_loop_tool_specs",
            return_value=[fake_spec],
        ),
        patch(
            "runtime.core.cerebrum.react_loop.next_custom_model_fallback",
            return_value=None,
        ) as fallback,
    ):
        stream = stream_react_loop(_Stack(router), _intent(), agent=None, max_iterations=3)
        events: list[dict[str, Any]] = []
        while True:
            try:
                events.append(next(stream))
            except StopIteration as stopped:
                result = stopped.value
                break

    visible_answer = "".join(
        str(event.get("delta") or "") for event in events if event.get("type") == "text_delta"
    )
    assert visible_answer == ""
    assert not any(event.get("type") == "tool_start" for event in events)
    assert any(
        event.get("type") == "react_error"
        and "lacked its final response" in str(event.get("message") or "")
        for event in events
    )
    fallback.assert_called_once()
    assert result is None


def test_native_environment_gap_convergence_disables_tools_on_terminal_round() -> None:
    from runtime.core.cerebrum.react_loop import stream_react_loop

    router = _Router(
        [
            (
                "",
                [
                    ToolCall(
                        id="write",
                        name="write_text_file",
                        input={"path": "runtime/foo.py", "content": "value = 1\n"},
                    )
                ],
            ),
            (
                "",
                [
                    ToolCall(
                        id="pytest",
                        name="exec_shell",
                        input={"command": "pytest-echo-missing --version"},
                    )
                ],
            ),
            (
                "",
                [
                    ToolCall(
                        id="ruff",
                        name="exec_shell",
                        input={"command": "ruff-echo-missing check runtime/foo.py"},
                    )
                ],
            ),
            ("Final Answer: All tests passed.", []),
            ("Final Answer: 实现已写入；pytest 与 ruff 因环境缺少依赖未能运行。", []),
        ]
    )
    registry = SkillRegistry()
    registry.register(
        Skill(
            name="write_text_file",
            trusted_source="builtin://write_text_file",
            handler=lambda **_kwargs: {"success": True, "bytes_written": 10},
            affinity=["files"],
        ),
        verify_tests=False,
    )

    registry.register(
        Skill(
            name="exec_shell",
            trusted_source="skill://public/exec_shell",
            handler=_exec_shell,
            affinity=["shell", "exec", "dangerous"],
        ),
        verify_tests=False,
    )
    stack = _Stack(router)
    stack.executor = ToolExecutor(
        registry=registry,
        immunity=TrustEngine(trusted_sources=["builtin://*"], unknown_policy="allow"),
    )
    intent = _intent("修改 runtime/foo.py 并运行 pytest 与 ruff 验证")
    intent.user_context.update({"mode": "code", "auto_approve": True})
    specs = [
        ToolSpec(name="write_text_file", description="Write a file."),
        ToolSpec(name="exec_shell", description="Run a verifier."),
    ]

    with (
        patch(
            "runtime.core.cerebrum.react_native.native_tool_use_active",
            return_value=True,
        ),
        patch(
            "runtime.core.cerebrum.react_native.build_loop_tool_specs",
            return_value=specs,
        ),
    ):
        stream = stream_react_loop(stack, intent, agent=None, max_iterations=6)
        events: list[dict[str, Any]] = []
        while True:
            try:
                events.append(next(stream))
            except StopIteration as stop:
                result = stop.value
                break

    assert len(router.requests) == 5, [
        (len(request.tools), request.require_tool_use) for request in router.requests
    ]
    assert all(request.tools for request in router.requests[:3])
    assert all(request.tools == [] for request in router.requests[3:])
    assert all(request.require_tool_use is False for request in router.requests[3:])
    assert any("environment-verification-convergence" in step.observation for step in result.steps)
    verifier_receipts = [
        receipt
        for step in result.steps
        for receipt in step.action_results
        if receipt.get("tool_name") == "exec_shell"
    ]
    assert len(verifier_receipts) == 2
    assert all(receipt.get("trusted_execution") is True for receipt in verifier_receipts)
    assert all(
        receipt.get("execution_source") == "canonical_builtin" for receipt in verifier_receipts
    )
    assert all(receipt.get("ok") is False for receipt in verifier_receipts)
    visible_answer = "".join(
        str(event.get("delta") or "") for event in events if event.get("type") == "text_delta"
    )
    assert "pytest 与 ruff 因环境缺少依赖未能运行" in visible_answer
    assert "All tests passed" not in visible_answer
    assert "passed" not in (result.final_answer or "").lower()


def test_every_native_tool_round_requires_a_fresh_public_update() -> None:
    from runtime.core.cerebrum.react_loop import stream_react_loop

    router = _Router(
        [
            (
                "",
                [
                    ToolCall(
                        id="t1",
                        name="read_file",
                        input={
                            "path": "backend.py",
                            "public_update": "我先核对后端事件定义，确认时间线的源字段。",
                        },
                    )
                ],
            ),
            (
                "",
                [
                    ToolCall(
                        id="t2",
                        name="read_file",
                        input={
                            "path": "frontend.ts",
                            "confirmed_fact": "后端字段已经确认。",
                            "next_action": "我再核对前端映射，确定两端是否逐项对应。",
                        },
                    )
                ],
            ),
            ("Final Answer: 两端字段逐项对应。", []),
        ]
    )
    fake_spec = ToolSpec(
        name="read_file",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    )
    intent = _intent("只读比较后端事件与前端映射")
    intent.user_context.update(
        {
            "realtime_public_narrative": True,
            "realtime_public_orientation": True,
        }
    )

    with (
        patch(
            "runtime.core.cerebrum.react_native.native_tool_use_active",
            return_value=True,
        ),
        patch(
            "runtime.core.cerebrum.react_native.build_loop_tool_specs",
            return_value=[fake_spec],
        ),
    ):
        stream = stream_react_loop(
            _Stack(router),
            intent,
            agent=None,
            max_iterations=5,
        )
        events: list[dict[str, Any]] = []
        while True:
            try:
                events.append(next(stream))
            except StopIteration as stop:
                result = stop.value
                break

    tool_requests = [request for request in router.requests if request.tools]
    assert len(tool_requests) >= 2
    assert "public_update" in tool_requests[0].tools[0].input_schema["required"]
    assert tool_requests[1].tools[0].input_schema["required"] == [
        "path",
        "confirmed_fact",
        "next_action",
    ]
    assert result is not None
    assert [step.public_update for step in result.steps[:2]] == [
        "我先核对后端事件定义，确认时间线的源字段。",
        "后端字段已经确认；我再核对前端映射，确定两端是否逐项对应。",
    ]
    model_updates = [
        event
        for event in events
        if event.get("type") == "commentary_delta" and event.get("progress_source") == "model"
    ]
    assert [event["delta"] for event in model_updates] == [
        "我先核对后端事件定义，确认时间线的源字段。",
        "后端字段已经确认；我再核对前端映射，确定两端是否逐项对应。",
    ]


def test_observed_read_sequence_requires_public_updates_without_ui_flags() -> None:
    from runtime.core.cerebrum.react_loop import run_react_loop

    router = _Router(
        [
            (
                "",
                [
                    ToolCall(
                        id="t1",
                        name="read_file",
                        input={
                            "path": "backend.py",
                            "public_update": "我先核对两个指定文件的实际定义。",
                        },
                    ),
                    ToolCall(
                        id="t2",
                        name="read_file",
                        input={"path": "frontend.ts"},
                    ),
                ],
            ),
            ("Final Answer: 两个文件已经按要求核对。", []),
        ]
    )
    fake_spec = ToolSpec(
        name="read_file",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    )
    intent = _intent(
        "只读按证据顺序先并行读取 backend.py 与 frontend.ts；每批证据后自然告诉我确认了什么。"
    )

    with (
        patch(
            "runtime.core.cerebrum.react_native.native_tool_use_active",
            return_value=True,
        ),
        patch(
            "runtime.core.cerebrum.react_native.build_loop_tool_specs",
            return_value=[fake_spec],
        ),
    ):
        result = run_react_loop(_Stack(router), intent, agent=None, max_iterations=3)

    first_request = router.requests[0]
    assert first_request.tools
    assert "public_update" in first_request.tools[0].input_schema["required"]
    assert result is not None and result.success


def test_native_provider_omission_does_not_promote_tool_round_text() -> None:
    from runtime.core.cerebrum.react_loop import stream_react_loop

    orientation = "我先核对配置文件的实际内容，确认最终结论所需的依据。"
    router = _Router(
        [
            (
                "",
                [
                    ToolCall(
                        id="t1",
                        name="read_file",
                        input={"path": "config.yaml"},
                    )
                ],
            ),
            (orientation, []),
            ("Final Answer: 配置依据已经确认。", []),
        ]
    )
    fake_spec = ToolSpec(
        name="read_file",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    )
    intent = _intent("只读核对 config.yaml 并说明结论")
    intent.user_context.update(
        {
            "realtime_public_narrative": True,
            "realtime_public_orientation": True,
        }
    )

    with (
        patch(
            "runtime.core.cerebrum.react_native.native_tool_use_active",
            return_value=True,
        ),
        patch(
            "runtime.core.cerebrum.react_native.build_loop_tool_specs",
            return_value=[fake_spec],
        ),
    ):
        stream = stream_react_loop(_Stack(router), intent, agent=None, max_iterations=4)
        events: list[dict[str, Any]] = []
        while True:
            try:
                events.append(next(stream))
            except StopIteration as stop:
                result = stop.value
                break

    assert result is not None and result.final_answer == "配置依据已经确认。"
    assert result.steps[0].public_update == ""
    assert len(router.requests) == 3
    model_updates = [
        event["delta"]
        for event in events
        if event.get("type") == "commentary_delta" and event.get("progress_source") == "model"
    ]
    # The explicit post-tool evidence narrator remains public. What is gone is
    # the unsafe fallback that promoted arbitrary text attached to the native
    # tool call itself.
    assert model_updates == [orientation]


def test_escape_hatch_forces_text_mode(monkeypatch) -> None:
    from runtime.core.cerebrum.react_loop import run_react_loop

    # Capable router, but ECHO_NATIVE_TOOLUSE=0 forces the text protocol:
    # no native tools= must be passed even though the model could do them.
    monkeypatch.setenv("ECHO_NATIVE_TOOLUSE", "0")
    router = _Router([("Final Answer: 你好。", [])])
    result = run_react_loop(_Stack(router), _intent("你好"), agent=None)
    assert router.requests
    assert all(not getattr(r, "tools", None) for r in router.requests), (
        "forced text mode must not pass tools="
    )
    assert result is not None

