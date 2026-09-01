"""Tests for realtime cerebrum reflection fast path — simple questions, mode bypasses, tool routing."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    FastAPI = None  # type: ignore[assignment]
    TestClient = None  # type: ignore[assignment]

from tests.realtime_cerebrum._helpers import (
    _LAST_STREAM_KWARGS,
)
from tests.realtime_cerebrum._helpers import (
    drive as _drive,
)
from tests.realtime_cerebrum._helpers import (
    set_script as _set_script,
)


def test_simple_question_uses_reflection_fast_path(tmp_path: Path) -> None:
    from fastapi import FastAPI

    from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime
    from runtime.sensing.gateway.realtime_gateway import RealtimeGateway
    from runtime.sensing.model_router.models import ModelResponse, ModelStreamEvent

    class FakeRouter:
        def __init__(self) -> None:
            self.calls = 0

        def call_stream(self, _request: Any) -> Iterator[ModelStreamEvent]:
            self.calls += 1
            yield ModelStreamEvent(type="thinking_delta", delta="quick reflection")
            yield ModelStreamEvent(type="text_delta", delta="4")
            yield ModelStreamEvent(
                type="done",
                final=ModelResponse(text="4", thinking="quick reflection"),
            )

    class FakePlanner:
        planner_model = "fake"

        def __init__(self, router: FakeRouter) -> None:
            self.router = router

    class FakeStack:
        def __init__(self, router: FakeRouter) -> None:
            self.planner = FakePlanner(router)
            self.journal = None

    router = FakeRouter()
    runtime = CerebrumRuntime(
        stack=FakeStack(router),
        agent=None,
        logs_root=str(tmp_path / "threads"),
    )
    gateway = RealtimeGateway(runtime=runtime, approval_timeout=5.0)
    app = FastAPI()
    app.include_router(gateway.router)

    _set_script(
        [
            {"type": "tool_start", "tool_name": "list_cwd", "tool_call_id": "should-not-run"},
            {"type": "react_completed"},
        ]
    )
    with TestClient(app) as client, client.websocket_connect("/api/realtime") as ws:
        out = _drive(
            ws,
            {
                "threadId": "th-fast",
                "input": [{"type": "text", "text": "2+2等于几？"}],
                "approvalPolicy": "never",
                "model": "fake",
            },
        )

    assert router.calls == 1
    turn = out["response"].result["turn"]
    # Thinking is now surfaced as a ReasoningItem (streaming-UX work);
    # the fast path itself is unchanged — one router call, no tools.
    item_types = [it["type"] for it in turn["items"]]
    assert item_types[0] == "userMessage"
    assert item_types[-1] == "agentMessage"
    assert "reasoning" in item_types
    assert turn["items"][-1]["text"] == "4"


def test_reflex_greeting_uses_selected_agent_identity(tmp_path: Path) -> None:
    from fastapi import FastAPI

    from runtime.cli import _build_reflex_router
    from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime
    from runtime.sensing.gateway.realtime_gateway import RealtimeGateway

    class FakePlanner:
        router = object()

    class FakeStack:
        planner = FakePlanner()
        journal = None

    class FakeRegistry:
        agent = SimpleNamespace(agent_id="general", display_name="Eve")

        def has(self, agent_id: str) -> bool:
            return agent_id == self.agent.agent_id

        def get(self, _agent_id: str) -> Any:
            return self.agent

    runtime = CerebrumRuntime(
        stack=FakeStack(),
        agent=None,
        agent_registry=FakeRegistry(),
        logs_root=str(tmp_path / "threads"),
        reflex_router=_build_reflex_router(),
    )
    gateway = RealtimeGateway(runtime=runtime, approval_timeout=5.0)
    app = FastAPI()
    app.include_router(gateway.router)

    with TestClient(app) as client, client.websocket_connect("/api/realtime") as ws:
        out = _drive(
            ws,
            {
                "threadId": "th-agent-greeting",
                "input": [
                    {
                        "type": "text",
                        "text": "你好",
                        "metadata": {"context": {"mode": "chat", "agent_id": "general"}},
                    }
                ],
                "approvalPolicy": "never",
            },
        )

    turn = out["response"].result["turn"]
    assistant_items = [item for item in turn["items"] if item["type"] == "agentMessage"]
    assert assistant_items
    assert "我是 Eve" in assistant_items[-1]["text"]
    assert "我是 Echo" not in assistant_items[-1]["text"]


def test_chat_mode_tool_intent_bypasses_reflection_fast_path(tmp_path: Path) -> None:
    from runtime.protocol.items import TurnParams
    from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime

    class FakePlanner:
        router = object()

    class FakeStack:
        planner = FakePlanner()

    runtime = CerebrumRuntime(
        stack=FakeStack(),
        agent=None,
        logs_root=str(tmp_path / "threads"),
    )
    params = TurnParams.model_validate(
        {
            "threadId": "th-inspiration-tool",
            "input": [
                {
                    "type": "text",
                    "text": "搜索一下 OpenClaw 的官方仓库",
                    "metadata": {"context": {"mode": "chat"}},
                },
            ],
            "approvalPolicy": "never",
        }
    )

    assert (
        runtime._should_use_reflection_fast_path(
            "搜索一下 OpenClaw 的官方仓库",
            params,
        )
        is False
    )


def test_code_mode_never_uses_text_only_reflection_fast_path(tmp_path: Path) -> None:
    from runtime.protocol.items import TurnParams
    from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime

    runtime = CerebrumRuntime(
        stack=SimpleNamespace(planner=SimpleNamespace(router=object())),
        agent=None,
        logs_root=str(tmp_path / "threads"),
    )
    params = TurnParams.model_validate(
        {
            "threadId": "th-code-tools",
            "cwd": str(tmp_path),
            "input": [
                {
                    "type": "text",
                    "text": "fix the failing project tests",
                    "metadata": {
                        "context": {
                            "mode": "code",
                            "capability_mode": "code",
                            "workspace_path": str(tmp_path),
                        }
                    },
                }
            ],
        }
    )

    assert (
        runtime._should_use_reflection_fast_path("fix the failing project tests", params) is False
    )


def test_question_mark_with_resumable_task_bypasses_greeting_fast_path(
    tmp_path: Path,
) -> None:
    from runtime.protocol.items import TurnParams
    from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime

    runtime = CerebrumRuntime(
        stack=SimpleNamespace(planner=SimpleNamespace(router=object())),
        agent=None,
        logs_root=str(tmp_path / "threads"),
    )
    params = TurnParams.model_validate(
        {
            "threadId": "th-paused-question",
            "input": [{"type": "text", "text": "?"}],
            "approvalPolicy": "never",
        }
    )

    assert runtime._should_use_reflection_fast_path("?", params) is True
    assert (
        runtime._should_use_reflection_fast_path(
            "?",
            params,
            has_resumable_task=True,
        )
        is False
    )


def test_react_mode_ambiguous_topic_bypasses_reflection_fast_path(tmp_path: Path) -> None:
    from runtime.protocol.items import TurnParams
    from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime

    class FakePlanner:
        router = object()

    class FakeStack:
        planner = FakePlanner()

    runtime = CerebrumRuntime(
        stack=FakeStack(),
        agent=None,
        logs_root=str(tmp_path / "threads"),
    )
    params = TurnParams.model_validate(
        {
            "threadId": "th-react-topic",
            "input": [
                {
                    "type": "text",
                    "text": "AI 家庭机器人（扫地/陪伴/安防）",
                    "metadata": {"context": {"mode": "react"}},
                },
            ],
            "approvalPolicy": "never",
        }
    )

    assert (
        runtime._should_use_reflection_fast_path(
            "AI 家庭机器人（扫地/陪伴/安防）",
            params,
        )
        is False
    )


def test_default_mode_ambiguous_topic_bypasses_reflection_fast_path(tmp_path: Path) -> None:
    from runtime.protocol.items import TurnParams
    from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime

    class FakePlanner:
        router = object()

    class FakeStack:
        planner = FakePlanner()

    runtime = CerebrumRuntime(
        stack=FakeStack(),
        agent=None,
        logs_root=str(tmp_path / "threads"),
    )
    params = TurnParams.model_validate(
        {
            "threadId": "th-default-topic",
            "input": [{"type": "text", "text": "AI 家庭机器人（扫地/陪伴/安防）"}],
            "approvalPolicy": "never",
        }
    )

    assert (
        runtime._should_use_reflection_fast_path(
            "AI 家庭机器人（扫地/陪伴/安防）",
            params,
        )
        is False
    )


def test_react_mode_contextual_confirm_bypasses_reflection_fast_path(
    tmp_path: Path,
) -> None:
    from runtime.protocol.items import TurnParams
    from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime

    class FakePlanner:
        router = object()

    class FakeStack:
        planner = FakePlanner()

    runtime = CerebrumRuntime(
        stack=FakeStack(),
        agent=None,
        logs_root=str(tmp_path / "threads"),
    )
    params = TurnParams.model_validate(
        {
            "threadId": "th-react-confirm",
            "input": [
                {
                    "type": "text",
                    "text": "好",
                    "metadata": {"context": {"mode": "react"}},
                },
            ],
            "approvalPolicy": "never",
        }
    )

    assert (
        runtime._should_use_reflection_fast_path(
            "好",
            params,
            conversation_messages=[
                {
                    "role": "assistant",
                    "content": "选定后我可以直接启动 deep research。",
                },
            ],
        )
        is False
    )


def test_react_mode_contextual_research_topic_bypasses_reflection_fast_path(
    tmp_path: Path,
) -> None:
    from runtime.protocol.items import TurnParams
    from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime

    class FakePlanner:
        router = object()

    class FakeStack:
        planner = FakePlanner()

    runtime = CerebrumRuntime(
        stack=FakeStack(),
        agent=None,
        logs_root=str(tmp_path / "threads"),
    )
    params = TurnParams.model_validate(
        {
            "threadId": "th-react-research-topic",
            "input": [
                {
                    "type": "text",
                    "text": "AI应用",
                    "metadata": {"context": {"mode": "react"}},
                },
            ],
            "approvalPolicy": "never",
        }
    )

    assert (
        runtime._should_use_reflection_fast_path(
            "AI应用",
            params,
            conversation_messages=[
                {
                    "role": "assistant",
                    "content": (
                        "这个方向很宽泛，我需要一个聚焦点才能给出有价值的调研。\n\n"
                        "给我一个大致方向，我马上开始调研。"
                    ),
                },
            ],
        )
        is False
    )


def test_react_mode_simple_question_still_uses_reflection_fast_path(
    tmp_path: Path,
) -> None:
    from runtime.protocol.items import TurnParams
    from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime

    class FakePlanner:
        router = object()

    class FakeStack:
        planner = FakePlanner()

    runtime = CerebrumRuntime(
        stack=FakeStack(),
        agent=None,
        logs_root=str(tmp_path / "threads"),
    )
    params = TurnParams.model_validate(
        {
            "threadId": "th-react-qa",
            "input": [
                {
                    "type": "text",
                    "text": "2+2等于几？",
                    "metadata": {"context": {"mode": "react"}},
                },
            ],
            "approvalPolicy": "never",
        }
    )

    assert runtime._should_use_reflection_fast_path("2+2等于几？", params) is True


def test_react_mode_explicit_no_tool_reply_uses_reflection_fast_path(
    tmp_path: Path,
) -> None:
    from runtime.protocol.items import TurnParams
    from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime

    class FakePlanner:
        router = object()

    class FakeStack:
        planner = FakePlanner()

    runtime = CerebrumRuntime(
        stack=FakeStack(),
        agent=None,
        logs_root=str(tmp_path / "threads"),
    )
    params = TurnParams.model_validate(
        {
            "threadId": "th-react-no-tools",
            "input": [
                {
                    "type": "text",
                    "text": "普通模式回归：请只用一句话回复收到，不要调用工具。",
                    "metadata": {"context": {"mode": "react"}},
                },
            ],
            "approvalPolicy": "never",
        }
    )

    assert (
        runtime._should_use_reflection_fast_path(
            "普通模式回归：请只用一句话回复收到，不要调用工具。",
            params,
        )
        is True
    )


def test_input_metadata_capability_mode_reaches_react_intent() -> None:
    from runtime.protocol.items import TurnParams
    from runtime.sensing.gateway.realtime_cerebrum import _build_intent

    params = TurnParams.model_validate(
        {
            "threadId": "th-code",
            "input": [
                {
                    "type": "text",
                    "text": "fix the tests",
                    "metadata": {
                        "context": {
                            "mode": "code",
                            "capability_mode": "code",
                            "code_mode": "solo",
                            "permission_mode": "default",
                            "sandbox_mode": "sandbox",
                            "execution_environment": "sandbox",
                        },
                    },
                },
            ],
            "approvalPolicy": "never",
        }
    )

    intent = _build_intent(
        "fix the tests",
        params,
        allow_client_auto_approve=True,
    )

    assert intent.user_context["mode"] == "code"
    assert intent.user_context["capability_mode"] == "code"
    assert intent.user_context["code_mode"] == "solo"
    assert intent.user_context["permission_mode"] == "default"
    assert intent.user_context["sandbox_mode"] == "sandbox"
    assert intent.user_context["auto_approve"] is True


def test_tool_question_keeps_react_path_when_router_exists(tmp_path: Path) -> None:
    from fastapi import FastAPI

    from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime
    from runtime.sensing.gateway.realtime_gateway import RealtimeGateway
    from runtime.sensing.model_router.models import ModelStreamEvent

    class FakeRouter:
        def __init__(self) -> None:
            self.calls = 0

        def call_stream(self, _request: Any) -> Iterator[ModelStreamEvent]:
            self.calls += 1
            raise AssertionError("tool turns must not use reflection fast path")

    class FakePlanner:
        planner_model = "fake"

        def __init__(self, router: FakeRouter) -> None:
            self.router = router

    class FakeStack:
        def __init__(self, router: FakeRouter) -> None:
            self.planner = FakePlanner(router)
            self.journal = None

    router = FakeRouter()
    runtime = CerebrumRuntime(
        stack=FakeStack(router),
        agent=None,
        logs_root=str(tmp_path / "threads"),
    )
    gateway = RealtimeGateway(runtime=runtime, approval_timeout=5.0)
    app = FastAPI()
    app.include_router(gateway.router)

    _set_script(
        [
            {"type": "tool_start", "tool_name": "list_cwd", "tool_call_id": "call-1"},
            {
                "type": "tool_end",
                "tool_name": "list_cwd",
                "tool_call_id": "call-1",
                "status": "success",
                "output_preview": "ok",
            },
            {"type": "react_completed"},
        ]
    )
    with TestClient(app) as client, client.websocket_connect("/api/realtime") as ws:
        out = _drive(
            ws,
            {
                "threadId": "th-tool-router",
                "input": [{"type": "text", "text": "列一下当前目录"}],
                "approvalPolicy": "never",
            },
        )

    assert router.calls == 0
    assert _LAST_STREAM_KWARGS["max_iterations"] == 30
    turn = out["response"].result["turn"]
    cmd_items = [it for it in turn["items"] if it["type"] == "commandExecution"]
    assert cmd_items[0]["command"] == "list_cwd"

