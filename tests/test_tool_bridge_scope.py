import json
from types import SimpleNamespace

import runtime.sensing.gateway.tool_bridge as tool_bridge
from runtime.execution.suckers.agent_meta_skills import _todo_write
from runtime.execution.suckers.builtins import _list_cwd
from runtime.execution.suckers.registry import Skill, SkillRegistry
from runtime.execution.tool_engine.executor import ToolExecutor
from runtime.platform.models import ParsedIntent
from runtime.platform.process.session import Session, session_scope
from runtime.safety.auth import TrustEngine
from runtime.sensing.gateway.tool_bridge import (
    _execute_tool_call,
    _reflection_checkpoint_message,
    build_anthropic_tool_specs,
    stream_agentic_fallback,
)
from runtime.sensing.model_router.models import (
    ModelResponse,
    ModelStreamEvent,
    ToolCall,
)


def _agent():
    return SimpleNamespace(
        agent_id="coder",
        capabilities={"code_mode_unlock": True},
        soul="",
    )


def _stack(router=None):
    registry = SkillRegistry()
    registry.register(
        Skill(
            name="list_cwd",
            description="List files in a directory.",
            affinity=["file", "io"],
            trusted_source="skill://public/list_cwd",
            handler=_list_cwd,
        ),
        verify_tests=False,
    )
    return SimpleNamespace(
        executor=ToolExecutor(registry, TrustEngine()),
        planner=SimpleNamespace(router=router, planner_model="mock"),
    )


def _stack_with_todo(router=None):
    registry = SkillRegistry()
    registry.register(
        Skill(
            name="list_cwd",
            description="List files in a directory.",
            affinity=["file", "io"],
            trusted_source="skill://public/list_cwd",
            handler=_list_cwd,
        ),
        verify_tests=False,
    )
    registry.register(
        Skill(
            name="todo_write",
            description="Record the live task checklist.",
            affinity=["meta", "plan", "ui"],
            trusted_source="skill://public/todo_write",
            handler=_todo_write,
        ),
        verify_tests=False,
    )
    return SimpleNamespace(
        executor=ToolExecutor(registry, TrustEngine()),
        planner=SimpleNamespace(router=router, planner_model="mock"),
    )


def _registry_with_task_chain() -> SkillRegistry:
    registry = SkillRegistry()
    for name in (
        "todo_write",
        "deep-research-swarm",
        "deep-research",
        "report-writing",
        "docx",
        "web_search",
    ):
        registry.register(
            Skill(
                name=name,
                description=f"Run {name}.",
                trusted_source=f"skill://public/{name}",
                handler=lambda **_kwargs: {},
            ),
            verify_tests=False,
        )
    return registry


def test_chat_mode_tool_specs_exclude_deep_task_chain():
    specs = build_anthropic_tool_specs(
        _registry_with_task_chain(),
        user_context={"mode": "chat"},
    )
    names = {spec.name for spec in specs}

    assert "todo_write" in names
    assert "web_search" in names
    assert "deep-research-swarm" not in names
    assert "deep-research" not in names
    assert "report-writing" not in names
    assert "docx" not in names


def test_research_mode_tool_specs_keep_deep_task_chain():
    specs = build_anthropic_tool_specs(
        _registry_with_task_chain(),
        user_context={"mode": "swarm"},
    )
    names = {spec.name for spec in specs}

    assert "deep-research-swarm" in names
    assert "report-writing" in names
    assert "docx" in names


def test_goal_activation_preserves_relevant_tools_after_cap():
    registry = SkillRegistry()
    for idx in range(20):
        registry.register(
            Skill(
                name=f"dummy_{idx}",
                description="Dummy tool.",
                trusted_source=f"skill://public/dummy_{idx}",
                handler=lambda **_kwargs: {},
            ),
            verify_tests=False,
        )
    for name in ("web_search", "fetch_url", "deep-research", "query_skill"):
        registry.register(
            Skill(
                name=name,
                description=f"Run {name}.",
                trusted_source=f"skill://public/{name}",
                handler=lambda **_kwargs: {},
            ),
            verify_tests=False,
        )

    specs = build_anthropic_tool_specs(
        registry,
        max_skills=3,
        goal="调研一个值得进入的细分赛道，输出竞品和风险",
    )
    names = {spec.name for spec in specs}

    assert "web_search" in names
    assert "fetch_url" in names
    assert "deep-research" in names
    assert "query_skill" in names


def test_tool_result_allows_medium_outputs_without_truncating():
    registry = SkillRegistry()
    registry.register(
        Skill(
            name="large_result",
            description="Return a medium sized result.",
            trusted_source="skill://public/large_result",
            handler=lambda **_kwargs: "x" * 5000,
        ),
        verify_tests=False,
    )
    stack = SimpleNamespace(
        executor=SimpleNamespace(registry=registry),
        planner=SimpleNamespace(router=None, planner_model="mock"),
    )

    rendered, is_error = _execute_tool_call(
        stack,
        ToolCall(id="tc_1", name="large_result", input={}),
    )

    assert is_error is False
    assert rendered == "x" * 5000


def test_reflection_checkpoint_is_structured_and_todo_limited():
    message = _reflection_checkpoint_message(10, 300)

    assert "<reflection-checkpoint iteration=10" in message
    assert "1. 已完成" in message
    assert "2. 还差" in message
    assert "3. 当前 plan 是否仍然合理" in message
    assert "4. 下一步动作" in message
    assert "本轮只允许思考或调用 `todo_write`" in message


def test_agentic_tool_call_uses_session_workspace_path(tmp_path):
    marker = tmp_path / "ONLY_TARGET.txt"
    marker.write_text("target", encoding="utf-8")
    stack = _stack()
    session = Session(
        agent=_agent(),
        thread_id="thread-1",
        metadata={
            "mode": "code",
            "workspace_path": str(tmp_path),
            "sandbox_mode": "sandbox",
        },
    )

    with session_scope(session):
        output, is_error = _execute_tool_call(
            stack,
            ToolCall(id="tool-1", name="list_cwd", input={"path": "."}),
        )

    assert not is_error
    data = json.loads(output)
    assert data["path"] == str(tmp_path.resolve())
    assert {item["name"] for item in data["items"]} == {"ONLY_TARGET.txt"}


def test_agentic_stream_carries_scope_metadata_into_tool_thread(tmp_path):
    marker = tmp_path / "ONLY_TARGET.txt"
    marker.write_text("target", encoding="utf-8")

    class Router:
        def __init__(self):
            self.calls = 0

        def call_stream(self, _request):
            self.calls += 1
            if self.calls == 1:
                yield ModelStreamEvent(
                    type="tool_use",
                    tool_call=ToolCall(
                        id="tool-1",
                        name="list_cwd",
                        input={"path": "."},
                    ),
                )
                yield ModelStreamEvent(
                    type="done",
                    final=ModelResponse(text="", tool_calls=[]),
                )
                return
            yield ModelStreamEvent(type="text_delta", delta="done")
            yield ModelStreamEvent(
                type="done",
                final=ModelResponse(text="done"),
            )

    intent = ParsedIntent(
        raw="分析项目",
        intent_type="task",
        normalized_goal="分析项目",
        user_context={
            "conversation_id": "thread-1",
            "metadata": {
                "mode": "code",
                "workspace_path": str(tmp_path),
                "sandbox_mode": "sandbox",
            },
        },
    )

    events = list(stream_agentic_fallback(_stack(Router()), intent, _agent()))
    tool_end = next(event for event in events if event[0] == "tool_end")

    assert "ONLY_TARGET.txt" in tool_end[1]["output"]


def test_agentic_stream_asserts_todo_write_capability():
    class Router:
        def __init__(self):
            self.requests = []

        def call_stream(self, request):
            self.requests.append(request)
            yield ModelStreamEvent(type="text_delta", delta="done")
            yield ModelStreamEvent(
                type="done",
                final=ModelResponse(text="done"),
            )

    router = Router()
    intent = ParsedIntent(
        raw="use todo_write",
        intent_type="task",
        normalized_goal="use todo_write",
        user_context={"conversation_id": "thread-1"},
    )

    events = list(stream_agentic_fallback(_stack_with_todo(router), intent, _agent()))

    assert any(event[0] == "done" for event in events)
    first_request = router.requests[0]
    system_text = "\n".join(
        msg.content
        for msg in first_request.messages
        if msg.role == "system" and isinstance(msg.content, str)
    )
    assert "You DO have a `todo_write` tool" in system_text
    assert "Do not say `todo_write` is unavailable" in system_text
    assert any(tool.name == "todo_write" for tool in first_request.tools)


def test_agentic_stream_injects_relevant_memory_hub_records(tmp_path, monkeypatch):
    from runtime.memory import user_store

    monkeypatch.setenv("ECHO_HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    user_store.add_fact(
        "Echo deploys must use blue green rollout.",
        category="ops",
        source="manual",
        scope="project",
        project=str(tmp_path),
    )

    class Router:
        def __init__(self):
            self.requests = []

        def call_stream(self, request):
            self.requests.append(request)
            yield ModelStreamEvent(type="text_delta", delta="done")
            yield ModelStreamEvent(
                type="done",
                final=ModelResponse(text="done"),
            )

    router = Router()
    intent = ParsedIntent(
        raw="Plan Echo rollout",
        intent_type="task",
        normalized_goal="Plan Echo rollout",
        user_context={
            "conversation_id": "thread-1",
            "workspace_path": str(tmp_path),
        },
    )

    events = list(stream_agentic_fallback(_stack(router), intent, _agent()))

    assert any(event[0] == "done" for event in events)
    system_text = "\n".join(
        msg.content
        for msg in router.requests[0].messages
        if msg.role == "system" and isinstance(msg.content, str)
    )
    assert "RELEVANT LONG-TERM MEMORY" in system_text
    assert "blue green rollout" in system_text


def test_agentic_stream_injects_team_memory_hub_records(tmp_path, monkeypatch):
    monkeypatch.setenv("ECHO_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    team_core = tmp_path / "teams" / "Alpha-Team" / "team-core"
    team_core.mkdir(parents=True)
    (team_core / "MEMORY.md").write_text(
        "- Alpha team requires release captain reviews\n",
        encoding="utf-8",
    )

    class Router:
        def __init__(self):
            self.requests = []

        def call_stream(self, request):
            self.requests.append(request)
            yield ModelStreamEvent(type="text_delta", delta="done")
            yield ModelStreamEvent(
                type="done",
                final=ModelResponse(text="done"),
            )

    router = Router()
    intent = ParsedIntent(
        raw="Plan release captain rollout",
        intent_type="task",
        normalized_goal="Plan release captain rollout",
        user_context={
            "conversation_id": "thread-1",
            "workspace_path": str(tmp_path),
            "metadata": {"team_id": "Alpha Team"},
        },
    )

    events = list(stream_agentic_fallback(_stack(router), intent, _agent()))

    assert any(event[0] == "done" for event in events)
    system_text = "\n".join(
        msg.content
        for msg in router.requests[0].messages
        if msg.role == "system" and isinstance(msg.content, str)
    )
    assert "RELEVANT LONG-TERM MEMORY" in system_text
    assert "memory_md:team" in system_text
    assert "release captain reviews" in system_text


def test_agentic_stream_does_not_use_todo_as_completion_gate():
    class Router:
        def __init__(self):
            self.calls = 0
            self.requests = []

        def call_stream(self, request):
            self.calls += 1
            self.requests.append(request)
            if self.calls == 1:
                yield ModelStreamEvent(type="text_delta", delta="premature")
                yield ModelStreamEvent(
                    type="done",
                    final=ModelResponse(text="premature"),
                )
                return
            if self.calls == 2:
                yield ModelStreamEvent(
                    type="tool_use",
                    tool_call=ToolCall(
                        id="todo-1",
                        name="todo_write",
                        input={
                            "todos": [
                                {
                                    "text": "Confirm the task shape",
                                    "status": "completed",
                                },
                                {
                                    "text": "Run the requested checks",
                                    "status": "completed",
                                },
                            ],
                        },
                    ),
                )
                yield ModelStreamEvent(
                    type="done",
                    final=ModelResponse(text="", tool_calls=[]),
                )
                return
            yield ModelStreamEvent(type="text_delta", delta="final")
            yield ModelStreamEvent(
                type="done",
                final=ModelResponse(text="final"),
            )

    router = Router()
    intent = ParsedIntent(
        raw="coordinate the team response",
        intent_type="task",
        normalized_goal="coordinate the team response",
        user_context={
            "conversation_id": "thread-1",
            "metadata": {"mode": "team"},
        },
    )

    events = list(stream_agentic_fallback(_stack_with_todo(router), intent, _agent()))

    assert router.calls == 1
    assert not any(
        event[0] == "tool_start" and event[1]["name"] == "todo_write" for event in events
    )
    assert events[-1] == ("done", "", "premature")


def test_agentic_stream_does_not_require_todo_refresh_after_tools(tmp_path):
    marker = tmp_path / "ONLY_TARGET.txt"
    marker.write_text("target", encoding="utf-8")

    class Router:
        def __init__(self):
            self.calls = 0
            self.requests = []

        def call_stream(self, request):
            self.calls += 1
            self.requests.append(request)
            if self.calls == 1:
                yield ModelStreamEvent(
                    type="tool_use",
                    tool_call=ToolCall(
                        id="todo-1",
                        name="todo_write",
                        input={
                            "todos": [
                                {
                                    "text": "Inspect the workspace",
                                    "status": "in_progress",
                                },
                            ],
                        },
                    ),
                )
                yield ModelStreamEvent(
                    type="done",
                    final=ModelResponse(text="", tool_calls=[]),
                )
                return
            if self.calls == 2:
                yield ModelStreamEvent(
                    type="tool_use",
                    tool_call=ToolCall(
                        id="tool-1",
                        name="list_cwd",
                        input={"path": "."},
                    ),
                )
                yield ModelStreamEvent(
                    type="done",
                    final=ModelResponse(text="", tool_calls=[]),
                )
                return
            if self.calls == 3:
                yield ModelStreamEvent(type="text_delta", delta="premature")
                yield ModelStreamEvent(
                    type="done",
                    final=ModelResponse(text="premature"),
                )
                return
            if self.calls == 4:
                yield ModelStreamEvent(
                    type="tool_use",
                    tool_call=ToolCall(
                        id="todo-2",
                        name="todo_write",
                        input={
                            "todos": [
                                {
                                    "text": "Inspect the workspace",
                                    "status": "completed",
                                },
                            ],
                        },
                    ),
                )
                yield ModelStreamEvent(
                    type="done",
                    final=ModelResponse(text="", tool_calls=[]),
                )
                return
            yield ModelStreamEvent(type="text_delta", delta="final")
            yield ModelStreamEvent(
                type="done",
                final=ModelResponse(text="final"),
            )

    router = Router()
    intent = ParsedIntent(
        raw="inspect the project and summarize it",
        intent_type="task",
        normalized_goal="inspect the project and summarize it",
        user_context={
            "conversation_id": "thread-1",
            "metadata": {
                "mode": "code",
                "workspace_path": str(tmp_path),
                "sandbox_mode": "sandbox",
            },
        },
    )

    events = list(stream_agentic_fallback(_stack_with_todo(router), intent, _agent()))

    assert router.calls == 3
    todo_starts = [
        event for event in events if event[0] == "tool_start" and event[1]["name"] == "todo_write"
    ]
    assert len(todo_starts) == 1
    assert events[-1] == ("done", "", "premature")


def test_agentic_stream_prompts_for_user_decision_at_round_cap(monkeypatch):
    monkeypatch.setattr(tool_bridge, "MAX_TOOL_ROUNDS", 2)

    class Router:
        def __init__(self):
            self.requests = []

        def call_stream(self, request):
            self.requests.append(request)
            if request.tools:
                yield ModelStreamEvent(
                    type="text_delta",
                    delta="I will inspect first. ",
                )
                yield ModelStreamEvent(
                    type="tool_use",
                    tool_call=ToolCall(
                        id=f"tool-{len(self.requests)}",
                        name="list_cwd",
                        input={"path": "."},
                    ),
                )
                yield ModelStreamEvent(
                    type="done",
                    final=ModelResponse(text="", tool_calls=[]),
                )
                return
            yield ModelStreamEvent(
                type="text_delta",
                delta="Reached the work limit. Reply `继续` or `生成报告`.",
            )
            yield ModelStreamEvent(
                type="done",
                final=ModelResponse(
                    text="Reached the work limit. Reply `继续` or `生成报告`.",
                ),
            )

    router = Router()
    intent = ParsedIntent(
        raw="write a research report",
        intent_type="task",
        normalized_goal="write a research report",
        user_context={"conversation_id": "thread-1"},
    )

    events = list(stream_agentic_fallback(_stack(router), intent, _agent()))

    assert len(router.requests) == 3
    assert router.requests[-1].tools == []
    assert any(
        "user decision required" in str(msg.content)
        and "Do not write the final report yet" in str(msg.content)
        for msg in router.requests[-1].messages
        if msg.role == "user"
    )
    assert events[-1] == (
        "done",
        "",
        "Reached the work limit. Reply `继续` or `生成报告`.",
    )
