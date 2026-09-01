"""Tests for CerebrumRuntime — the react_loop→item/* adapter.

These tests substitute a fake ``stream_react_loop`` to keep the test
surface narrow: we care about the *bridge*, not the planner. A fake
stream yields a deterministic sequence of legacy react events; the
runtime must translate each into the correct ``item/*`` notification and
persist the right JSONL lines.

Also covers the approval round-trip through the WebSocket (integration
with RealtimeGateway), which is where the old global-dict approval_gate
couldn't be tested without monkey-patching.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    FastAPI = None  # type: ignore[assignment]
    TestClient = None  # type: ignore[assignment]

from runtime.protocol import (
    JsonRpcErrorCode,
    JsonRpcRequest,
    JsonRpcResponse,
    Notification,
    decode_message,
    encode_message,
)

pytestmark = pytest.mark.skipif(
    FastAPI is None, reason="fastapi required for realtime gateway tests"
)


@pytest.fixture(autouse=True)
def _patch_react_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the real stream_react_loop with a deterministic fake.

    The fake reads its script from a module-level list so individual
    tests can stage the event sequence they want. This is the cleanest
    seam the runtime exposes — substituting the planner keeps LLM,
    tool, and sandbox machinery out of the test.
    """
    import runtime.core.cerebrum.react_loop as rl

    def fake_stream(*args: Any, **kwargs: Any) -> Iterator[dict[str, Any]]:
        from runtime.platform.process.session import current_session

        _LAST_STREAM_ARGS.clear()
        _LAST_STREAM_ARGS.update({"args": args, "kwargs": kwargs})
        _LAST_STREAM_KWARGS.clear()
        _LAST_STREAM_KWARGS.update(kwargs)
        active_session = current_session()
        _LAST_SESSION.clear()
        _LAST_SESSION.update(
            {
                "thread_id": active_session.thread_id if active_session else None,
                "metadata": dict(active_session.metadata) if active_session else {},
            }
        )
        approval_provider = kwargs.get("approval_provider")
        script = _SCRIPT[:]
        for event in script:
            if event.get("__approve__"):
                # Translate into an approval round-trip using the
                # injected provider (mirrors what the real loop does).
                from runtime.safety.approval.approval_gate import ApprovalRequest

                decision = approval_provider.request(
                    ApprovalRequest(
                        thread_id=kwargs.get("thread_id", ""),
                        tool_name=event.get("tool_name", "x"),
                        tool_call_id=event.get("tool_call_id", "c1"),
                    )
                )
                yield {
                    "type": "tool_end",
                    "tool_name": event.get("tool_name", "x"),
                    "tool_call_id": event.get("tool_call_id", "c1"),
                    "iteration": 1,
                    "status": "success" if decision.approved else "rejected",
                    "output_preview": decision.reason or ("ok" if decision.approved else "denied"),
                    "duration_ms": 1,
                }
                continue
            yield event

    monkeypatch.setattr(rl, "stream_react_loop", fake_stream)


_SCRIPT: list[dict[str, Any]] = []
_LAST_STREAM_ARGS: dict[str, Any] = {}
_LAST_STREAM_KWARGS: dict[str, Any] = {}
_LAST_SESSION: dict[str, Any] = {}


def _set_script(events: list[dict[str, Any]]) -> None:
    _SCRIPT.clear()
    _SCRIPT.extend(events)
    _LAST_STREAM_KWARGS.clear()
    _LAST_SESSION.clear()


def test_flatten_merges_post_final_trace_items_into_delivered_answer() -> None:
    from runtime.protocol import Turn
    from runtime.sensing.gateway.realtime_cerebrum import _flatten_turns_to_messages

    turn = Turn.model_validate(
        {
            "id": "turn-1",
            "threadId": "thread-1",
            "status": "completed",
            "startedAt": "2026-06-01T18:53:24Z",
            "completedAt": "2026-06-01T19:03:00Z",
            "items": [
                {
                    "id": "u1",
                    "type": "userMessage",
                    "status": "completed",
                    "createdAt": "2026-06-01T18:53:24Z",
                    "text": "research a niche market",
                    "attachments": [],
                },
                {
                    "id": "r1",
                    "type": "reasoning",
                    "status": "completed",
                    "createdAt": "2026-06-01T18:53:26Z",
                    "summary": [],
                    "content": "collect initial evidence",
                },
                {
                    "id": "a1",
                    "type": "agentMessage",
                    "status": "completed",
                    "createdAt": "2026-06-01T19:03:00Z",
                    "text": "# Report\n\nOpportunity, competitors, risks, and next steps.",
                },
                {
                    "id": "r2",
                    "type": "reasoning",
                    "status": "completed",
                    "createdAt": "2026-06-01T19:02:50Z",
                    "summary": [],
                    "content": "The todo-protocol guard keeps blocking my final answer.",
                },
                {
                    "id": "c1",
                    "type": "commandExecution",
                    "status": "completed",
                    "createdAt": "2026-06-01T19:02:47Z",
                    "command": "todo_write",
                    "inputPreview": {},
                    "cwd": None,
                    "aggregatedOutput": "ok",
                    "exitCode": 0,
                    "processId": None,
                    "networkAccess": False,
                },
            ],
            "error": None,
        }
    )

    messages, _, _ = _flatten_turns_to_messages([turn])

    assert len(messages) == 2
    ai = messages[1]
    assert ai["content"].startswith("# Report")
    # Raw provider reasoning is never copied into persisted chat history.
    assert "reasoning_content" not in ai["additional_kwargs"]
    assert "public_reasoning_summary" not in ai["additional_kwargs"]
    assert [tool["name"] for tool in ai["tool_calls"]] == ["todo_write"]


@pytest.fixture()
def gateway(tmp_path: Path) -> Any:
    from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime
    from runtime.sensing.gateway.realtime_gateway import RealtimeGateway

    runtime = CerebrumRuntime(
        stack=object(),  # unused by the fake loop
        agent=object(),
        logs_root=str(tmp_path / "threads"),
    )
    gateway = RealtimeGateway(runtime=runtime, approval_timeout=5.0)
    app = FastAPI()
    app.include_router(gateway.router)
    with TestClient(app) as client:
        yield client, tmp_path / "threads"


def _drive(ws: Any, params: dict[str, Any], approve: bool = True) -> dict[str, Any]:
    ws.send_text(encode_message(JsonRpcRequest(id=1, method="turn/start", params=params)))
    notifications: list[Notification] = []
    response: JsonRpcResponse | None = None
    while True:
        msg = decode_message(ws.receive_text())
        if isinstance(msg, JsonRpcRequest):
            ws.send_text(
                encode_message(
                    JsonRpcResponse(
                        id=msg.id, result={"action": "accept" if approve else "decline"}
                    )
                )
            )
            continue
        if isinstance(msg, Notification):
            notifications.append(msg)
            continue
        if isinstance(msg, JsonRpcResponse) and msg.id == 1:
            response = msg
            break
    assert response is not None
    return {"response": response, "notifications": notifications}


def test_text_delta_maps_to_agent_message(gateway: Any) -> None:
    client, _ = gateway
    _set_script(
        [
            {"type": "text_delta", "delta": "hello "},
            {"type": "text_delta", "delta": "world"},
            {"type": "react_completed"},
        ]
    )
    with client.websocket_connect("/api/realtime") as ws:
        out = _drive(
            ws,
            {
                "threadId": "th-text",
                "input": [{"type": "text", "text": "say hi"}],
                "approvalPolicy": "never",
            },
        )

    methods = [n.method for n in out["notifications"]]
    assert "turn/started" in methods
    assert methods.count("item/started") >= 1
    assert "item/agentMessage/delta" in methods

    deltas = [
        n.params["delta"] for n in out["notifications"] if n.method == "item/agentMessage/delta"
    ]
    assert "".join(deltas) == "hello world"

    # Final snapshot carries one completed agentMessage item.
    turn = out["response"].result["turn"]
    agent_items = [it for it in turn["items"] if it["type"] == "agentMessage"]
    assert len(agent_items) == 1
    assert agent_items[0]["text"] == "hello world"
    assert agent_items[0]["status"] == "completed"


def test_todo_write_emits_plan_update_and_resume_snapshot(gateway: Any) -> None:
    client, _ = gateway
    _set_script(
        [
            {
                "type": "tool_start",
                "tool_name": "todo_write",
                "tool_call_id": "todo-1",
                "input_preview": {
                    "items": [
                        {"content": "Inspect context", "status": "completed"},
                        {"content": "Patch realtime protocol", "status": "in_progress"},
                        {"content": "Verify behavior", "status": "pending"},
                    ]
                },
            },
            {
                "type": "tool_end",
                "tool_name": "todo_write",
                "tool_call_id": "todo-1",
                "status": "success",
                "output_preview": "ok",
            },
            {"type": "react_completed"},
        ]
    )
    with client.websocket_connect("/api/realtime") as ws:
        out = _drive(
            ws,
            {
                "threadId": "th_plan_update",
                "input": [{"type": "text", "text": "plan"}],
                "approvalPolicy": "never",
            },
        )
        ws.send_text(
            encode_message(
                JsonRpcRequest(
                    id=2,
                    method="thread/resume",
                    params={"threadId": "th_plan_update"},
                )
            )
        )
        resume: JsonRpcResponse | None = None
        while True:
            msg = decode_message(ws.receive_text())
            if isinstance(msg, JsonRpcResponse) and msg.id == 2:
                resume = msg
                break

    updates = [n for n in out["notifications"] if n.method == "turn/plan/updated"]
    assert updates
    phases = updates[0].params["phases"]
    assert [phase["title"] for phase in phases] == [
        "Inspect context",
        "Patch realtime protocol",
        "Verify behavior",
    ]
    assert phases[1]["status"] == "running"
    assert phases[1]["activeItemId"] == "todo-1"
    assert updates[0].params["workspaceFocus"]["view"] == "trace"
    assert "workbenchSnapshot" not in updates[0].params

    snapshots = [n for n in out["notifications"] if n.method == "workbench/snapshot"]
    assert snapshots
    assert snapshots[0].params["snapshot"]["version"] == 1
    assert snapshots[0].params["snapshot"]["schemaVersion"] == 2
    assert snapshots[0].params["snapshot"]["currentItemId"] == "todo-1"
    assert snapshots[0].params["snapshot"]["workspaceFocus"]["view"] == "trace"
    final_snapshot = snapshots[-1].params["snapshot"]
    assert final_snapshot["version"] == 3
    assert [phase["status"] for phase in final_snapshot["phases"]] == [
        "done",
        "pending",
        "pending",
    ]

    turn = out["response"].result["turn"]
    assert turn["phases"][1]["status"] == "pending"
    assert turn["workspaceFocus"]["itemId"] == "todo-1"
    assert turn["workbenchSnapshot"]["currentPhaseId"] == "todo-phase:1"
    assert turn["workbenchSnapshot"]["version"] == 3
    assert resume is not None and resume.result is not None
    resumed_turn = resume.result["turns"][0]
    assert resumed_turn["phases"][1]["title"] == "Patch realtime protocol"
    assert resumed_turn["workspaceFocus"]["view"] == "trace"
    assert resumed_turn["workbenchSnapshot"]["version"] == 3


def test_team_subagent_lifecycle_maps_to_first_class_item(
    gateway: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runtime.safety.organization import (
        AgentSpec,
        CoordinationProtocol,
        Role,
        TeamTopology,
    )
    from runtime.safety.organization.team_runner import TeamRunResult

    client, _ = gateway
    topology = TeamTopology(
        name="test_topology",
        protocol=CoordinationProtocol.SEQUENTIAL,
        agents={Role.PLANNER: AgentSpec(agent_id="planner_a")},
    )

    class FakeTeamRunner:
        def __init__(self, *args: Any, event_emitter: Any = None, **kwargs: Any) -> None:
            self._emit = event_emitter

        def run(self, topology: Any, text: str, context: dict[str, Any]) -> TeamRunResult:
            assert self._emit is not None
            self._emit(
                {
                    "type": "subagent_spawned",
                    "agent_id": "planner_a",
                    "role": "planner",
                    "codename": "Plan-abc",
                    "avatar": "P",
                }
            )
            self._emit(
                {
                    "type": "subagent_finished",
                    "agent_id": "planner_a",
                    "role": "planner",
                    "codename": "Plan-abc",
                    "avatar": "P",
                    "ok": True,
                    "iteration_count": 2,
                    "files_touched": ["plan.md"],
                    "status": "done",
                }
            )
            return TeamRunResult(
                topology_name=topology.name,
                topology_fingerprint=topology.fingerprint,
                task_bucket=topology.task_bucket,
                success=True,
                final_output="done",
            )

    monkeypatch.setattr(
        "runtime.safety.organization.forge.load_registry",
        lambda: {"test_topology": topology},
    )
    monkeypatch.setattr(
        "runtime.safety.organization.team_runner.TeamRunner",
        FakeTeamRunner,
    )
    monkeypatch.setattr(
        "runtime.safety.organization.performance_log.record_run",
        lambda *args, **kwargs: None,
    )

    with client.websocket_connect("/api/realtime") as ws:
        out = _drive(
            ws,
            {
                "threadId": "th-team-subagent",
                "input": [{"type": "text", "text": "run topology"}],
                "approvalPolicy": "never",
                "topologyId": "test_topology",
            },
        )

    started = [n.params["item"] for n in out["notifications"] if n.method == "item/started"]
    completed = [n.params["item"] for n in out["notifications"] if n.method == "item/completed"]
    sub_started = [item for item in started if item["type"] == "subagent"]
    sub_completed = [item for item in completed if item["type"] == "subagent"]
    assert len(sub_started) == 1
    assert len(sub_completed) == 1
    assert sub_started[0]["subagentId"] == "planner_a"
    assert sub_started[0]["status"] == "inProgress"
    assert sub_completed[0]["id"] == sub_started[0]["id"]
    assert sub_completed[0]["status"] == "completed"
    assert sub_completed[0]["iterationCount"] == 2
    assert sub_completed[0]["filesTouched"] == ["plan.md"]

    turn = out["response"].result["turn"]
    sub_items = [it for it in turn["items"] if it["type"] == "subagent"]
    assert len(sub_items) == 1
    assert sub_items[0]["status"] == "completed"


def test_background_tool_item_completes_after_turn_response(gateway: Any) -> None:
    import sys
    import time

    from runtime.execution.suckers.write_skills import _background_exec
    from runtime.memory.threads.event_log import EventLog
    from runtime.protocol.items import ItemStatus

    client, logs_dir = gateway
    started = _background_exec(
        command=[
            sys.executable,
            "-c",
            (
                "import time; "
                "print('bg-ready', flush=True); "
                "time.sleep(1.0); "
                "print('bg-done', flush=True)"
            ),
        ],
    )
    task_id = started["task_id"]
    _set_script(
        [
            {
                "type": "tool_start",
                "tool_name": "background_exec",
                "tool_call_id": "c_bg",
                "iteration": 1,
                "input_preview": {"command": "python -c ..."},
            },
            {
                "type": "tool_background",
                "tool_name": "background_exec",
                "tool_call_id": "c_bg",
                "iteration": 1,
                "task_id": task_id,
                "snapshot": started,
                "duration_ms": 1,
            },
            {"type": "react_completed"},
        ]
    )

    with client.websocket_connect("/api/realtime") as ws:
        out = _drive(
            ws,
            {
                "threadId": "th-bg",
                "input": [{"type": "text", "text": "start bg"}],
                "approvalPolicy": "never",
            },
        )

        turn = out["response"].result["turn"]
        cmd_items = [it for it in turn["items"] if it["type"] == "commandExecution"]
        assert len(cmd_items) == 1
        assert cmd_items[0]["status"] == "inProgress"
        assert cmd_items[0]["inputPreview"]["background"] is True
        assert cmd_items[0]["inputPreview"]["task_id"] == task_id

        log = EventLog(logs_dir / "th-bg.jsonl")
        deadline = time.time() + 3.0
        final_item: Any | None = None
        while time.time() < deadline:
            turns = log.replay()
            items = [
                it
                for turn_obj in turns
                for it in turn_obj.items
                if getattr(it, "id", None) == "c_bg"
            ]
            if items and getattr(items[-1], "status", None) == ItemStatus.COMPLETED:
                final_item = items[-1]
                break
            time.sleep(0.05)

    assert final_item is not None
    assert "bg-ready" in final_item.aggregated_output
    assert "bg-done" in final_item.aggregated_output


def test_stale_background_watchers_reaped_on_next_turn(tmp_path: Path) -> None:
    """Watchers from a previous turn must be cancelled when a new
    turn starts on the same thread, otherwise long-running shells
    keep streaming output into the prior conversation while the
    user is on a new topic."""
    import sys
    import time

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from runtime.execution.suckers.write_skills import _background_exec
    from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime
    from runtime.sensing.gateway.realtime_gateway import RealtimeGateway

    runtime = CerebrumRuntime(
        stack=object(),
        agent=object(),
        logs_root=str(tmp_path / "threads"),
    )
    rt_gateway = RealtimeGateway(runtime=runtime, approval_timeout=5.0)
    app = FastAPI()
    app.include_router(rt_gateway.router)

    started = _background_exec(
        command=[
            sys.executable,
            "-c",
            "import time; print('boot', flush=True); time.sleep(30.0)",
        ],
    )
    task_id = started["task_id"]
    _set_script(
        [
            {
                "type": "tool_start",
                "tool_name": "background_exec",
                "tool_call_id": "c_bg2",
                "iteration": 1,
                "input_preview": {"command": "python -c ..."},
            },
            {
                "type": "tool_background",
                "tool_name": "background_exec",
                "tool_call_id": "c_bg2",
                "iteration": 1,
                "task_id": task_id,
                "snapshot": started,
                "duration_ms": 1,
            },
            {"type": "react_completed"},
        ]
    )

    with TestClient(app) as client, client.websocket_connect("/api/realtime") as ws:
        _drive(
            ws,
            {
                "threadId": "th-reap",
                "input": [{"type": "text", "text": "first"}],
                "approvalPolicy": "never",
            },
        )
        bucket = runtime._thread_background_tasks.get("th-reap")
        assert bucket and any(not t.done() for t in bucket), (
            "expected at least one running watcher after first turn"
        )

        # Second turn — reaper at start_turn entry must cancel the
        # leftover watcher before the new turn proceeds.
        _set_script(
            [
                {"type": "text_delta", "delta": "second"},
                {"type": "react_completed"},
            ]
        )
        _drive(
            ws,
            {
                "threadId": "th-reap",
                "input": [{"type": "text", "text": "second"}],
                "approvalPolicy": "never",
            },
        )

        # Reap is awaited inside ``start_turn`` so the bucket
        # should be empty (or all done) by the time the second
        # turn returns. Allow a tiny grace window for done-callbacks.
        deadline = time.time() + 1.0
        while time.time() < deadline:
            bucket = runtime._thread_background_tasks.get("th-reap") or []
            if all(t.done() for t in bucket):
                break
            time.sleep(0.05)
        bucket = runtime._thread_background_tasks.get("th-reap") or []
        assert all(t.done() for t in bucket), (
            "reaper failed to cancel stale watchers from prior turn"
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
    assert [it["type"] for it in turn["items"]] == [
        "userMessage",
        "reasoning",
        "agentMessage",
    ]
    assert turn["items"][1]["content"] == "quick reflection"
    assert turn["items"][2]["text"] == "4"


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


def test_realtime_react_binds_thread_artifact_session(tmp_path: Path) -> None:
    from fastapi import FastAPI

    from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime
    from runtime.sensing.gateway.realtime_gateway import RealtimeGateway

    runtime = CerebrumRuntime(
        stack=object(),
        agent=None,
        logs_root=str(tmp_path / "threads"),
        workspace_root=str(tmp_path / "workspaces"),
    )
    gateway = RealtimeGateway(runtime=runtime, approval_timeout=5.0)
    app = FastAPI()
    app.include_router(gateway.router)

    _set_script([{"type": "react_completed"}])
    with TestClient(app) as client, client.websocket_connect("/api/realtime") as ws:
        _drive(
            ws,
            {
                "threadId": "th-artifacts",
                "input": [{"type": "text", "text": "write a report file"}],
                "approvalPolicy": "on-request",
            },
        )

    assert _LAST_SESSION["thread_id"] == "th-artifacts"
    assert Path(_LAST_SESSION["metadata"]["_artifact_output_root"]) == (
        tmp_path / "workspaces" / "th-artifacts" / "output" / "final"
    )


def test_resume_proposal_block_is_parsed_into_sanitized_session_metadata() -> None:
    from runtime.protocol import TurnParams
    from runtime.sensing.gateway.realtime_cerebrum import _build_intent

    resume_text = """
Resume this agent run from the selected durable checkpoint.

<echo_resume_proposal>
{
  "schema": "echo.resume_proposal.v1",
  "checkpoint_id": 7,
  "task_id": "task-1",
  "checkpoint_type": "react",
  "iteration": 3,
  "phase": "implementation",
  "progress": "trace store wired",
  "working_set": ["runtime/memory/trace_store.py"],
  "resume_plan": ["Continue from iteration 4."],
  "raw_state_included": false,
  "raw_message_snapshots_included": false,
  "messages_snapshot": ["message body"]
}
</echo_resume_proposal>
""".strip()
    intent = _build_intent(
        resume_text,
        TurnParams(
            threadId="th-resume-intent",
            input=[{"type": "text", "text": resume_text}],
            approvalPolicy="on-request",
        ),
    )
    resume_intent = intent.user_context["resume_intent"]
    assert resume_intent == {
        "schema": "echo.resume_intent.v1",
        "requires_confirmation": True,
        "source": "resume_proposal_block",
        "checkpoint_id": 7,
        "task_id": "task-1",
        "checkpoint_type": "react",
        "iteration": 3,
        "continue_from_iteration": 4,
        "phase": "implementation",
        "progress": "trace store wired",
        "working_set": ["runtime/memory/trace_store.py"],
        "resume_plan": ["Continue from iteration 4."],
        "safety": {
            "raw_state_included": False,
            "raw_message_snapshots_included": False,
        },
    }
    assert "messages_snapshot" not in resume_intent
    assert "message body" not in str(resume_intent)


def test_resume_proposal_block_prepares_confirmation_without_running_react(
    gateway: Any,
) -> None:
    client, _ = gateway
    _set_script([{"type": "text_delta", "delta": "should not run"}, {"type": "react_completed"}])
    resume_text = """
Resume this agent run from the selected durable checkpoint.

<echo_resume_proposal>
{
  "schema": "echo.resume_proposal.v1",
  "checkpoint_id": 9,
  "task_id": "task-9",
  "checkpoint_type": "react",
  "iteration": 4,
  "phase": "implementation",
  "progress": "private message body must not leak",
  "working_set": ["runtime/memory/trace_store.py"],
  "resume_plan": ["Inspect sanitized checkpoint metadata.", "message body must not leak"],
  "raw_state_included": false,
  "raw_message_snapshots_included": false,
  "messages_snapshot": ["message body"]
}
</echo_resume_proposal>
""".strip()
    with client.websocket_connect("/api/realtime") as ws:
        out = _drive(
            ws,
            {
                "threadId": "th-resume-confirm",
                "input": [{"type": "text", "text": resume_text}],
                "approvalPolicy": "on-request",
            },
        )

    assert _LAST_STREAM_KWARGS == {}
    turn = out["response"].result["turn"]
    assert turn["status"] == "completed"
    agent_items = [it for it in turn["items"] if it["type"] == "agentMessage"]
    assert len(agent_items) == 1
    text = agent_items[0]["text"]
    assert "恢复请求已准备" in text
    assert "checkpoint #9" in text
    assert "需要你明确确认" in text
    assert "建议恢复计划：2 步" in text
    assert "message body" not in text
    assert "messages_snapshot" not in text
    assert "raw_state" not in text


def test_confirmed_resume_intent_runs_react_once_and_is_consumed(gateway: Any) -> None:
    client, _ = gateway
    resume_text = """
Resume this agent run from the selected durable checkpoint.

<echo_resume_proposal>
{
  "schema": "echo.resume_proposal.v1",
  "checkpoint_id": 12,
  "task_id": "task-12",
  "checkpoint_type": "react",
  "iteration": 2,
  "phase": "implementation",
  "progress": "private message body must not leak",
  "working_set": ["runtime/sensing/siphon/realtime_cerebrum.py"],
  "resume_plan": ["Continue from iteration 3."],
  "raw_state_included": false,
  "raw_message_snapshots_included": false,
  "messages_snapshot": ["message body"]
}
</echo_resume_proposal>
""".strip()
    with client.websocket_connect("/api/realtime") as ws:
        _set_script(
            [{"type": "text_delta", "delta": "should not run"}, {"type": "react_completed"}]
        )
        _drive(
            ws,
            {
                "threadId": "th-resume-consume",
                "input": [{"type": "text", "text": resume_text}],
                "approvalPolicy": "on-request",
            },
        )
        assert _LAST_STREAM_KWARGS == {}

        _set_script([{"type": "react_completed"}])
        _drive(
            ws,
            {
                "threadId": "th-resume-consume",
                "input": [{"type": "text", "text": "确认恢复 checkpoint #12"}],
                "approvalPolicy": "on-request",
            },
        )
        assert _LAST_STREAM_KWARGS["thread_id"] == "th-resume-consume"
        resume_intent = _LAST_SESSION["metadata"]["resume_intent"]
        assert resume_intent["checkpoint_id"] == 12
        assert resume_intent["requires_confirmation"] is False
        assert resume_intent["confirmed"] is True
        assert "message body" not in str(resume_intent)

        _set_script([{"type": "react_completed"}])
        _drive(
            ws,
            {
                "threadId": "th-resume-consume",
                "input": [{"type": "text", "text": "继续"}],
                "approvalPolicy": "on-request",
            },
        )
        assert "resume_intent" not in _LAST_SESSION["metadata"]


def test_confirmed_resume_intent_passes_task_id_to_react(gateway: Any) -> None:
    client, _ = gateway
    task_id = str(uuid4())
    resume_text = f"""
Resume this agent run from the selected durable checkpoint.

<echo_resume_proposal>
{{
  "schema": "echo.resume_proposal.v1",
  "checkpoint_id": 33,
  "task_id": "{task_id}",
  "checkpoint_type": "react",
  "iteration": 5,
  "phase": "implementation",
  "resume_plan": ["Continue from iteration 6."],
  "raw_state_included": false,
  "raw_message_snapshots_included": false
}}
</echo_resume_proposal>
""".strip()
    with client.websocket_connect("/api/realtime") as ws:
        _set_script([{"type": "react_completed"}])
        _drive(
            ws,
            {
                "threadId": "th-resume-task-id",
                "input": [{"type": "text", "text": resume_text}],
                "approvalPolicy": "on-request",
            },
        )
        assert _LAST_STREAM_KWARGS == {}

        _set_script([{"type": "react_completed"}])
        _drive(
            ws,
            {
                "threadId": "th-resume-task-id",
                "input": [{"type": "text", "text": "确认恢复 checkpoint #33"}],
                "approvalPolicy": "on-request",
            },
        )

    assert str(_LAST_STREAM_KWARGS["resume_task_id"]) == task_id


def test_react_resumed_emits_thread_status_changed(gateway: Any) -> None:
    client, _ = gateway
    _set_script(
        [
            {
                "type": "react_resumed",
                "task_id": "task-123",
                "checkpoint_iteration": 2,
                "resume_from_iteration": 2,
                "restored_step_count": 1,
                "has_final_answer": False,
                "current_phase": "execute",
            },
            {"type": "react_completed"},
        ]
    )
    with client.websocket_connect("/api/realtime") as ws:
        out = _drive(
            ws,
            {
                "threadId": "th-resumed-status",
                "input": [{"type": "text", "text": "continue"}],
                "approvalPolicy": "never",
            },
        )

    status_events = [n for n in out["notifications"] if n.method == "thread/status/changed"]
    assert status_events
    assert status_events[0].params["status"]["type"] == "resumed"
    assert status_events[0].params["status"]["resumeFromIteration"] == 2


def test_confirmed_resume_intent_survives_runtime_restart_when_trace_store_exists(
    tmp_path: Path,
) -> None:
    from fastapi import FastAPI

    from runtime.memory.diagnostics.trace_store import AgentTraceStore
    from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime
    from runtime.sensing.gateway.realtime_gateway import RealtimeGateway

    trace = AgentTraceStore(tmp_path / "trace.sqlite")
    runtime_a = CerebrumRuntime(
        stack=object(),
        agent=None,
        logs_root=str(tmp_path / "threads-a"),
        trace_store=trace,
    )
    app_a = FastAPI()
    app_a.include_router(RealtimeGateway(runtime=runtime_a, approval_timeout=5.0).router)
    resume_text = """
Resume this agent run from the selected durable checkpoint.

<echo_resume_proposal>
{
  "schema": "echo.resume_proposal.v1",
  "checkpoint_id": 21,
  "task_id": "task-21",
  "checkpoint_type": "react",
  "iteration": 6,
  "phase": "implementation",
  "progress": "private message body must not leak",
  "working_set": ["runtime/memory/trace_store.py"],
  "resume_plan": ["message body must not leak"],
  "raw_state_included": false,
  "raw_message_snapshots_included": false,
  "messages_snapshot": ["message body"]
}
</echo_resume_proposal>
""".strip()
    with TestClient(app_a) as client, client.websocket_connect("/api/realtime") as ws:
        _set_script([{"type": "react_completed"}])
        _drive(
            ws,
            {
                "threadId": "th-resume-restart",
                "input": [{"type": "text", "text": resume_text}],
                "approvalPolicy": "on-request",
            },
        )
    assert trace.latest_pending_resume_request(thread_id="th-resume-restart") is not None

    runtime_b = CerebrumRuntime(
        stack=object(),
        agent=None,
        logs_root=str(tmp_path / "threads-b"),
        trace_store=trace,
    )
    app_b = FastAPI()
    app_b.include_router(RealtimeGateway(runtime=runtime_b, approval_timeout=5.0).router)
    with TestClient(app_b) as client, client.websocket_connect("/api/realtime") as ws:
        _set_script([{"type": "react_completed"}])
        _drive(
            ws,
            {
                "threadId": "th-resume-restart",
                "input": [{"type": "text", "text": "确认恢复 checkpoint #21"}],
                "approvalPolicy": "on-request",
            },
        )

    resume_intent = _LAST_SESSION["metadata"]["resume_intent"]
    assert resume_intent["checkpoint_id"] == 21
    assert resume_intent["confirmed"] is True
    assert "message body" not in str(resume_intent)
    assert trace.latest_pending_resume_request(thread_id="th-resume-restart") is None
    requests = trace.resume_requests(thread_id="th-resume-restart")
    assert requests[0]["status"] == "consumed"


def test_user_prompt_hook_can_rewrite_prompt_before_react_loop(
    gateway: Any,
) -> None:
    client, _ = gateway
    _set_script(
        [
            {"type": "text_delta", "delta": "expanded"},
            {"type": "react_completed"},
        ]
    )
    from runtime.safety.hooks import HookDecision, UserPromptSubmitEvent, register_hook

    @register_hook(UserPromptSubmitEvent)
    def _rewrite(event):
        assert event.prompt_text == "before hook"
        return HookDecision.modify_prompt("after hook")

    with client.websocket_connect("/api/realtime") as ws:
        out = _drive(
            ws,
            {
                "threadId": "th-user-hook",
                "input": [{"type": "text", "text": "before hook"}],
                "approvalPolicy": "on-request",
                "context": {"mode": "deep"},
            },
        )

    assert _LAST_STREAM_ARGS["args"][1].raw == "after hook"
    turn = out["response"].result["turn"]
    assert turn["status"] == "completed"


def test_complex_turn_defaults_to_planning_mode_in_react_loop(
    gateway: Any,
) -> None:
    client, _ = gateway
    _set_script(
        [
            {"type": "text_delta", "delta": "plan only"},
            {"type": "react_completed"},
        ]
    )

    with client.websocket_connect("/api/realtime") as ws:
        out = _drive(
            ws,
            {
                "threadId": "th-default-plan",
                "input": [{"type": "text", "text": "请完整实现这个功能并测试"}],
                "approvalPolicy": "on-request",
            },
        )

    assert _LAST_STREAM_KWARGS["planning_mode"] is True
    turn = out["response"].result["turn"]
    assert turn["params"]["planningMode"] is True


def test_turn_effort_reaches_react_loop(gateway: Any) -> None:
    client, _ = gateway
    _set_script(
        [
            {"type": "text_delta", "delta": "reasoned"},
            {"type": "react_completed"},
        ]
    )

    with client.websocket_connect("/api/realtime") as ws:
        _drive(
            ws,
            {
                "threadId": "th-effort",
                "input": [{"type": "text", "text": "solve this hard bug"}],
                "approvalPolicy": "never",
                "effort": "xhigh",
            },
        )

    assert _LAST_STREAM_KWARGS["reasoning_effort"] == "xhigh"
    assert _LAST_SESSION["metadata"]["reasoning_effort"] == "xhigh"


def test_thinking_delta_maps_to_reasoning(gateway: Any) -> None:
    client, _ = gateway
    _set_script(
        [
            {"type": "thinking_delta", "delta": "step 1\n"},
            {"type": "thinking_delta", "delta": "step 2"},
            {"type": "react_completed"},
        ]
    )
    with client.websocket_connect("/api/realtime") as ws:
        out = _drive(
            ws,
            {
                "threadId": "th-think",
                "input": [{"type": "text", "text": "reason"}],
                "approvalPolicy": "never",
            },
        )

    reasoning_deltas = [
        n.params["delta"] for n in out["notifications"] if n.method == "item/reasoning/textDelta"
    ]
    assert "".join(reasoning_deltas) == "step 1\nstep 2"

    turn = out["response"].result["turn"]
    r_items = [it for it in turn["items"] if it["type"] == "reasoning"]
    assert r_items[0]["content"] == "step 1\nstep 2"


def test_tool_round_trip_with_approval(gateway: Any) -> None:
    client, logs_root = gateway
    _set_script(
        [
            {"type": "text_delta", "delta": "running "},
            {
                "type": "tool_start",
                "tool_name": "exec_shell",
                "tool_call_id": "call-1",
                "iteration": 1,
                "input_preview": "ls",
            },
            {
                "__approve__": True,
                "tool_name": "exec_shell",
                "tool_call_id": "call-1",
            },
            {"type": "text_delta", "delta": "done"},
            {"type": "react_completed"},
        ]
    )
    with client.websocket_connect("/api/realtime") as ws:
        out = _drive(
            ws,
            {
                "threadId": "th-tool",
                "input": [{"type": "text", "text": "do it"}],
                "approvalPolicy": "on-request",
            },
            approve=True,
        )

    methods = [n.method for n in out["notifications"]]
    # Before tool, the prose item should have completed (flush on
    # tool_start) and a commandExecution item should have started.
    assert methods.index("item/completed") < methods.index(
        "item/started", methods.index("item/started") + 1
    )

    turn = out["response"].result["turn"]
    cmd_items = [it for it in turn["items"] if it["type"] == "commandExecution"]
    assert len(cmd_items) == 1
    assert cmd_items[0]["command"] == "exec_shell"
    assert cmd_items[0]["inputPreview"] == "ls"
    assert cmd_items[0]["status"] == "completed"

    # The event log preserves the full sequence.
    log_file = logs_root / "th-tool.jsonl"
    assert log_file.exists()


def test_tool_rejected_propagates(gateway: Any) -> None:
    client, _ = gateway
    _set_script(
        [
            {
                "type": "tool_start",
                "tool_name": "exec_shell",
                "tool_call_id": "call-2",
                "iteration": 1,
            },
            {
                "__approve__": True,
                "tool_name": "exec_shell",
                "tool_call_id": "call-2",
            },
            {"type": "react_completed"},
        ]
    )
    with client.websocket_connect("/api/realtime") as ws:
        out = _drive(
            ws,
            {
                "threadId": "th-reject",
                "input": [{"type": "text", "text": "nope"}],
                "approvalPolicy": "on-request",
            },
            approve=False,
        )

    turn = out["response"].result["turn"]
    cmd_items = [it for it in turn["items"] if it["type"] == "commandExecution"]
    assert cmd_items[0]["status"] == "declined"


def test_react_error_becomes_error_item(gateway: Any) -> None:
    client, _ = gateway
    _set_script(
        [
            {"type": "text_delta", "delta": "partial "},
            {"type": "react_error", "kind": "RuntimeError", "message": "boom", "iteration": 1},
        ]
    )
    with client.websocket_connect("/api/realtime") as ws:
        out = _drive(
            ws,
            {
                "threadId": "th-err",
                "input": [{"type": "text", "text": "go"}],
                "approvalPolicy": "never",
            },
        )

    turn = out["response"].result["turn"]
    err_items = [it for it in turn["items"] if it["type"] == "error"]
    assert err_items and err_items[0]["message"] == "boom"


def test_resume_after_turn_rebuilds_from_disk(gateway: Any) -> None:
    client, _ = gateway
    _set_script([{"type": "text_delta", "delta": "persist me"}, {"type": "react_completed"}])
    with client.websocket_connect("/api/realtime") as ws:
        _drive(
            ws,
            {
                "threadId": "th-resume",
                "input": [{"type": "text", "text": "hi"}],
                "approvalPolicy": "never",
            },
        )
    with client.websocket_connect("/api/realtime") as ws:
        ws.send_text(
            encode_message(
                JsonRpcRequest(id=7, method="thread/resume", params={"threadId": "th-resume"})
            )
        )
        while True:
            msg = decode_message(ws.receive_text())
            if isinstance(msg, JsonRpcResponse) and msg.id == 7:
                break
    assert msg.result is not None
    turns = msg.result["turns"]
    assert len(turns) == 1
    agent = [it for it in turns[0]["items"] if it["type"] == "agentMessage"]
    assert agent and agent[0]["text"] == "persist me"


def test_thread_list_via_cerebrum(gateway: Any) -> None:
    client, _ = gateway
    _set_script([{"type": "text_delta", "delta": "hi"}, {"type": "react_completed"}])
    with client.websocket_connect("/api/realtime") as ws:
        _drive(
            ws,
            {
                "threadId": "th_cb_one",
                "input": [{"type": "text", "text": "hi"}],
                "approvalPolicy": "never",
            },
        )
        _drive(
            ws,
            {
                "threadId": "th_cb_two",
                "input": [{"type": "text", "text": "hi"}],
                "approvalPolicy": "never",
            },
        )
    with client.websocket_connect("/api/realtime") as ws:
        ws.send_text(encode_message(JsonRpcRequest(id=99, method="thread/list", params={})))
        while True:
            msg = decode_message(ws.receive_text())
            if isinstance(msg, JsonRpcResponse) and msg.id == 99:
                break
    assert msg.result is not None
    ids = sorted(t["threadId"] for t in msg.result["threads"])
    assert ids == ["th_cb_one", "th_cb_two"]


def test_thread_archive_via_cerebrum(gateway: Any) -> None:
    client, _ = gateway
    _set_script([{"type": "text_delta", "delta": "hi"}, {"type": "react_completed"}])
    with client.websocket_connect("/api/realtime") as ws:
        _drive(
            ws,
            {
                "threadId": "th_cb_archive",
                "input": [{"type": "text", "text": "hi"}],
                "approvalPolicy": "never",
            },
        )
    with client.websocket_connect("/api/realtime") as ws:
        ws.send_text(
            encode_message(
                JsonRpcRequest(
                    id=77,
                    method="thread/archive",
                    params={"threadId": "th_cb_archive"},
                )
            )
        )
        while True:
            msg = decode_message(ws.receive_text())
            if isinstance(msg, JsonRpcResponse) and msg.id == 77:
                break
    assert msg.result == {"threadId": "th_cb_archive", "archived": True}


def test_thread_archive_blocks_cerebrum_resume(gateway: Any) -> None:
    client, _ = gateway
    _set_script([{"type": "text_delta", "delta": "hi"}, {"type": "react_completed"}])
    with client.websocket_connect("/api/realtime") as ws:
        _drive(
            ws,
            {
                "threadId": "th_cb_archived_resume",
                "input": [{"type": "text", "text": "hi"}],
                "approvalPolicy": "never",
            },
        )
        ws.send_text(
            encode_message(
                JsonRpcRequest(
                    id=78,
                    method="thread/archive",
                    params={"threadId": "th_cb_archived_resume"},
                )
            )
        )
        while True:
            msg = decode_message(ws.receive_text())
            if isinstance(msg, JsonRpcResponse) and msg.id == 78:
                break
        ws.send_text(
            encode_message(
                JsonRpcRequest(
                    id=79,
                    method="thread/resume",
                    params={"threadId": "th_cb_archived_resume"},
                )
            )
        )
        while True:
            msg = decode_message(ws.receive_text())
            if isinstance(msg, JsonRpcResponse) and msg.id == 79:
                break
    assert msg.error is not None
    assert msg.error.code == JsonRpcErrorCode.THREAD_NOT_FOUND


def test_tool_end_with_diff_emits_file_change_item(gateway: Any) -> None:
    """When react_loop emits a ``tool_end`` carrying a unified diff,
    the bridge must promote it to a structured FileChangeItem so the
    UI can render hunk-level controls."""
    client, _ = gateway
    diff = "--- a/src/foo.py\n+++ b/src/foo.py\n@@ -1,3 +1,3 @@\n x\n-old\n+new\n y\n"
    _set_script(
        [
            {
                "type": "tool_start",
                "tool_name": "edit_text_file",
                "tool_call_id": "call-edit",
            },
            {
                "type": "tool_end",
                "tool_name": "edit_text_file",
                "tool_call_id": "call-edit",
                "iteration": 1,
                "status": "success",
                "output_preview": "ok",
                "duration_ms": 1,
                "diff": diff,
                "verification": {
                    "command": "pytest tests/test_foo.py",
                    "kind": "test",
                    "exit_code": 0,
                    "success": True,
                    "stdout_tail": "1 passed",
                },
            },
            {"type": "react_completed"},
        ]
    )
    with client.websocket_connect("/api/realtime") as ws:
        out = _drive(
            ws,
            {
                "threadId": "th_filechange",
                "input": [{"type": "text", "text": "edit"}],
                "approvalPolicy": "never",
            },
        )

    hunk_deltas = [n for n in out["notifications"] if n.method == "item/fileChange/hunkDelta"]
    assert len(hunk_deltas) == 1
    assert hunk_deltas[0].params["path"] == "src/foo.py"
    assert hunk_deltas[0].params["workspaceFocus"]["view"] == "diff"
    assert hunk_deltas[0].params["hunk"]["decision"] == "pending"

    turn = out["response"].result["turn"]
    file_items = [it for it in turn["items"] if it["type"] == "fileChange"]
    assert len(file_items) == 1
    fci = file_items[0]
    assert len(fci["changes"]) == 1
    change = fci["changes"][0]
    assert change["path"] == "src/foo.py"
    assert change["op"] == "update"
    assert len(change["hunks"]) == 1
    hunk = change["hunks"][0]
    assert hunk["oldStart"] == 1 and hunk["newStart"] == 1
    assert hunk["decision"] == "pending"


def test_code_file_change_without_verification_fails_turn(gateway: Any) -> None:
    client, logs_root = gateway
    diff = "--- a/src/foo.py\n+++ b/src/foo.py\n@@ -1,2 +1,2 @@\n-old\n+new\n"
    _set_script(
        [
            {
                "type": "tool_start",
                "tool_name": "edit_file",
                "tool_call_id": "call-edit",
            },
            {
                "type": "tool_end",
                "tool_name": "edit_file",
                "tool_call_id": "call-edit",
                "iteration": 1,
                "status": "success",
                "output_preview": "ok",
                "duration_ms": 1,
                "diff": diff,
            },
            {"type": "react_completed"},
        ]
    )
    with client.websocket_connect("/api/realtime") as ws:
        out = _drive(
            ws,
            {
                "threadId": "th_unverified_code_change",
                "input": [{"type": "text", "text": "edit"}],
                "approvalPolicy": "never",
            },
        )

    turn = out["response"].result["turn"]
    assert turn["status"] == "failed"
    # The bounded agent-verification follow-up reuses this fake script. A
    # provider may also reuse call ids across real rounds; public item ids
    # must remain unique for the entire turn.
    command_ids = [it["id"] for it in turn["items"] if it["type"] == "commandExecution"]
    assert command_ids == ["call-edit", "call-edit#2"]
    verification_items = [it for it in turn["items"] if it["type"] == "verification"]
    assert len(verification_items) == 1
    assert verification_items[0]["kind"] == "manual"
    assert verification_items[0]["status"] == "failed"
    assert verification_items[0]["relatedFiles"] == ["src/foo.py"]

    ledger_path = logs_root.parent / "proposal_ledger.jsonl"
    lines = ledger_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["kind"] == "turn_failure"
    assert entry["proposer"] == "realtime_cerebrum"
    assert entry["metadata"]["failure_source"] == "verification_required"
    assert entry["metadata"]["code_change_paths"] == ["src/foo.py"]
    assert entry["metadata"]["turn_id"] == turn["id"]


def test_code_file_change_with_successful_verification_can_complete(gateway: Any) -> None:
    client, _ = gateway
    diff = "--- a/src/foo.py\n+++ b/src/foo.py\n@@ -1,2 +1,2 @@\n-old\n+new\n"
    _set_script(
        [
            {
                "type": "tool_start",
                "tool_name": "edit_file",
                "tool_call_id": "call-edit",
            },
            {
                "type": "tool_end",
                "tool_name": "edit_file",
                "tool_call_id": "call-edit",
                "iteration": 1,
                "status": "success",
                "output_preview": "ok",
                "duration_ms": 1,
                "diff": diff,
                "verification": {
                    "command": "pytest tests/test_foo.py",
                    "kind": "test",
                    "exit_code": 0,
                    "success": True,
                    "stdout_tail": "1 passed",
                },
            },
            {"type": "react_completed"},
        ]
    )
    with client.websocket_connect("/api/realtime") as ws:
        out = _drive(
            ws,
            {
                "threadId": "th_verified_code_change",
                "input": [{"type": "text", "text": "edit"}],
                "approvalPolicy": "never",
            },
        )

    turn = out["response"].result["turn"]
    assert turn["status"] == "completed"
    file_items = [it for it in turn["items"] if it["type"] == "fileChange"]
    verification_items = [it for it in turn["items"] if it["type"] == "verification"]
    assert len(file_items) == 1
    assert len(verification_items) == 1
    assert verification_items[0]["status"] == "completed"
    assert verification_items[0]["relatedFiles"] == ["src/foo.py"]
    assert verification_items[0]["relatedChangeItemIds"] == [file_items[0]["id"]]


def test_code_file_change_with_failed_verification_fails_turn(gateway: Any) -> None:
    client, logs_root = gateway
    diff = "--- a/src/foo.py\n+++ b/src/foo.py\n@@ -1,2 +1,2 @@\n-old\n+new\n"
    _set_script(
        [
            {
                "type": "tool_start",
                "tool_name": "edit_file",
                "tool_call_id": "call-edit",
            },
            {
                "type": "tool_end",
                "tool_name": "edit_file",
                "tool_call_id": "call-edit",
                "iteration": 1,
                "status": "success",
                "output_preview": "tests failed",
                "duration_ms": 1,
                "diff": diff,
                "verification": {
                    "command": "pytest tests/test_foo.py",
                    "kind": "test",
                    "exit_code": 1,
                    "success": False,
                    "stdout_tail": "1 failed",
                },
            },
            {"type": "react_completed"},
        ]
    )
    with client.websocket_connect("/api/realtime") as ws:
        out = _drive(
            ws,
            {
                "threadId": "th_failed_verified_code_change",
                "input": [{"type": "text", "text": "edit"}],
                "approvalPolicy": "never",
            },
        )

    turn = out["response"].result["turn"]
    assert turn["status"] == "failed"
    verification_items = [it for it in turn["items"] if it["type"] == "verification"]
    assert len(verification_items) == 1
    assert verification_items[0]["status"] == "failed"
    assert verification_items[0]["relatedFiles"] == ["src/foo.py"]

    ledger_path = logs_root.parent / "proposal_ledger.jsonl"
    entries = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines()]
    failures = [entry for entry in entries if entry["kind"] == "turn_failure"]
    assert failures[-1]["metadata"]["failure_source"] == "verification_failed"
    assert failures[-1]["metadata"]["failed_verifications"][0]["command"] == (
        "pytest tests/test_foo.py"
    )


def test_non_code_file_change_without_verification_can_complete(gateway: Any) -> None:
    client, logs_root = gateway
    diff = "--- a/notes.md\n+++ b/notes.md\n@@ -1,2 +1,2 @@\n-old\n+new\n"
    _set_script(
        [
            {
                "type": "tool_start",
                "tool_name": "write_text_file",
                "tool_call_id": "call-write",
            },
            {
                "type": "tool_end",
                "tool_name": "write_text_file",
                "tool_call_id": "call-write",
                "iteration": 1,
                "status": "success",
                "output_preview": "ok",
                "duration_ms": 1,
                "diff": diff,
            },
            {"type": "react_completed"},
        ]
    )
    with client.websocket_connect("/api/realtime") as ws:
        out = _drive(
            ws,
            {
                "threadId": "th_unverified_note_change",
                "input": [{"type": "text", "text": "write notes"}],
                "approvalPolicy": "never",
            },
        )

    turn = out["response"].result["turn"]
    assert turn["status"] == "completed"
    ledger_path = logs_root.parent / "proposal_ledger.jsonl"
    entries = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines()]
    success = [entry for entry in entries if entry["kind"] == "turn_success"]
    assert len(success) == 1
    assert success[0]["metadata"]["goal"] == "write notes"

    turn = out["response"].result["turn"]
    assert turn["status"] == "completed"
    assert not [it for it in turn["items"] if it["type"] == "verification"]


def test_tool_end_with_post_write_diagnostics_emits_verification_item(gateway: Any) -> None:
    client, _ = gateway
    diagnostics = (
        "ok\n\n"
        "[post-write diagnostics]\n"
        "ruff diagnostics (foo.py):\n"
        "E999 SyntaxError: expected ':'\n"
    )
    _set_script(
        [
            {
                "type": "tool_start",
                "tool_name": "write_text_file",
                "tool_call_id": "call-write",
                "input_preview": {"path": "src/foo.py"},
            },
            {
                "type": "tool_end",
                "tool_name": "write_text_file",
                "tool_call_id": "call-write",
                "iteration": 1,
                "status": "success",
                "output_preview": diagnostics,
                "duration_ms": 1,
            },
            {"type": "react_completed"},
        ]
    )
    with client.websocket_connect("/api/realtime") as ws:
        out = _drive(
            ws,
            {
                "threadId": "th_verify",
                "input": [{"type": "text", "text": "write"}],
                "approvalPolicy": "never",
            },
        )

    completed = [n.params["item"] for n in out["notifications"] if n.method == "item/completed"]
    verification_events = [it for it in completed if it["type"] == "verification"]
    assert len(verification_events) == 1
    assert verification_events[0]["kind"] == "diagnostic"
    assert verification_events[0]["status"] == "failed"
    assert verification_events[0]["exitCode"] == 1
    assert verification_events[0]["relatedFiles"] == ["src/foo.py"]

    turn = out["response"].result["turn"]
    verification_items = [it for it in turn["items"] if it["type"] == "verification"]
    assert len(verification_items) == 1
    assert "ruff diagnostics" in verification_items[0]["stdoutTail"]
    assert turn["status"] == "failed"


def test_tool_end_with_explicit_verification_metadata_emits_verification_item(gateway: Any) -> None:
    client, _ = gateway
    _set_script(
        [
            {
                "type": "tool_start",
                "tool_name": "run_tests",
                "tool_call_id": "call-test",
            },
            {
                "type": "tool_end",
                "tool_name": "run_tests",
                "tool_call_id": "call-test",
                "iteration": 1,
                "status": "success",
                "output_preview": "tests failed",
                "duration_ms": 1,
                "verification": {
                    "command": "pnpm test",
                    "kind": "test",
                    "exit_code": 1,
                    "success": False,
                    "stdout_tail": "1 failed",
                },
            },
            {"type": "react_completed"},
        ]
    )
    with client.websocket_connect("/api/realtime") as ws:
        out = _drive(
            ws,
            {
                "threadId": "th_explicit_verify",
                "input": [{"type": "text", "text": "test"}],
                "approvalPolicy": "never",
            },
        )

    turn = out["response"].result["turn"]
    verification_items = [it for it in turn["items"] if it["type"] == "verification"]
    assert len(verification_items) == 1
    assert verification_items[0]["command"] == "pnpm test"
    assert verification_items[0]["kind"] == "test"
    assert verification_items[0]["status"] == "failed"
    assert verification_items[0]["exitCode"] == 1
    assert verification_items[0]["stdoutTail"] == "1 failed"


def test_tool_end_verification_without_success_uses_event_failure(gateway: Any) -> None:
    client, _ = gateway
    _set_script(
        [
            {
                "type": "tool_start",
                "tool_name": "exec_shell",
                "tool_call_id": "call-typecheck",
            },
            {
                "type": "tool_end",
                "tool_name": "exec_shell",
                "tool_call_id": "call-typecheck",
                "iteration": 1,
                "status": "error",
                "output_preview": "command failed",
                "duration_ms": 1,
                "verification": {
                    "command": "npx -y tsc --noEmit",
                    "kind": "typecheck",
                    "stderr_tail": "[WinError 2] file not found",
                },
            },
            {"type": "react_completed", "success": False},
        ]
    )
    with client.websocket_connect("/api/realtime") as ws:
        out = _drive(
            ws,
            {
                "threadId": "th_verify_unknown_failed",
                "input": [{"type": "text", "text": "test"}],
                "approvalPolicy": "never",
            },
        )

    turn = out["response"].result["turn"]
    verification_items = [it for it in turn["items"] if it["type"] == "verification"]
    assert len(verification_items) == 1
    assert verification_items[0]["command"] == "npx -y tsc --noEmit"
    assert verification_items[0]["kind"] == "typecheck"
    assert verification_items[0]["status"] == "failed"
    assert turn["status"] == "failed"


def test_hunk_decide_rejected_reverts_file(gateway: Any, tmp_path: Path) -> None:
    """Client rejecting a hunk reverse-applies its diff to the file."""
    client, _ = gateway
    target = tmp_path / "sample.txt"
    target.write_text("x\nnew\ny\n", encoding="utf-8")
    diff = "--- a/sample.txt\n+++ b/sample.txt\n@@ -1,3 +1,3 @@\n x\n-old\n+new\n y\n"
    with client.websocket_connect("/api/realtime") as ws:
        ws.send_text(
            encode_message(
                JsonRpcRequest(
                    id=42,
                    method="item/fileChange/hunkDecide",
                    params={
                        "threadId": "th",
                        "turnId": "tn",
                        "itemId": "it",
                        "hunkId": "h1",
                        "path": str(target),
                        "decision": "rejected",
                        "diff": diff,
                    },
                )
            )
        )
        while True:
            msg = decode_message(ws.receive_text())
            if isinstance(msg, JsonRpcResponse) and msg.id == 42:
                break
    assert msg.error is None
    assert msg.result["decision"] == "rejected"
    assert target.read_text(encoding="utf-8") == "x\nold\ny\n"


def test_hunk_decide_accepted_does_not_touch_file(gateway: Any, tmp_path: Path) -> None:
    client, _ = gateway
    target = tmp_path / "kept.txt"
    target.write_text("after-edit\n", encoding="utf-8")
    with client.websocket_connect("/api/realtime") as ws:
        ws.send_text(
            encode_message(
                JsonRpcRequest(
                    id=43,
                    method="item/fileChange/hunkDecide",
                    params={
                        "path": str(target),
                        "decision": "accepted",
                    },
                )
            )
        )
        while True:
            msg = decode_message(ws.receive_text())
            if isinstance(msg, JsonRpcResponse) and msg.id == 43:
                break
    assert msg.error is None
    assert msg.result["decision"] == "accepted"
    assert target.read_text(encoding="utf-8") == "after-edit\n"


def test_throughput_event_maps_to_token_usage(gateway: Any) -> None:
    """react_loop emits periodic ``throughput`` events during long
    streams. The bridge must forward them as ``thread/tokenUsage/updated``
    notifications so the UI can show a live tokens-per-second indicator."""
    client, _ = gateway
    _set_script(
        [
            {"type": "text_delta", "delta": "hello "},
            {
                "type": "throughput",
                "chars": 6,
                "elapsed_ms": 500,
                "chars_per_sec": 12.0,
            },
            {"type": "text_delta", "delta": "world"},
            {"type": "react_completed"},
        ]
    )
    with client.websocket_connect("/api/realtime") as ws:
        result = _drive(
            ws,
            params={
                "threadId": "th_tp",
                "input": [{"type": "text", "text": "stream"}],
                "approvalPolicy": "never",
            },
        )
    tp_notes = [n for n in result["notifications"] if n.method == "thread/tokenUsage/updated"]
    assert tp_notes, "expected at least one tokenUsage notification"
    usage = tp_notes[0].params["tokenUsage"]
    assert usage["chars"] == 6
    assert usage["charsPerSec"] == 12.0
    assert usage["elapsedMs"] == 500


def test_turn_interrupt_kills_in_flight_subprocess(
    gateway: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: client sends turn/interrupt while a tool is running
    a long subprocess → stream_run sees cancellation → proc killed →
    tool_end carries status=cancelled → turn.status = cancelled."""
    import sys
    import time

    import runtime.core.cerebrum.react_loop as rl
    from runtime.platform.process.streaming import stream_run

    tool_completed_naturally = {"flag": False}

    def fake_stream_with_real_subprocess(
        *args: Any,
        **kwargs: Any,
    ) -> Iterator[dict[str, Any]]:
        # tool_start lets the bridge create a commandExecution item.
        yield {
            "type": "tool_start",
            "tool_name": "sleep_long",
            "tool_call_id": "c_cancel",
            "iteration": 1,
            "input_preview": {"seconds": 10},
        }
        t0 = time.monotonic()
        # Real subprocess — would sleep 10s if not cancelled.
        r = stream_run(
            [sys.executable, "-c", "import time; time.sleep(10); print('done')"],
            timeout=15,
        )
        elapsed = time.monotonic() - t0
        if r.get("cancelled"):
            yield {
                "type": "tool_end",
                "tool_name": "sleep_long",
                "tool_call_id": "c_cancel",
                "iteration": 1,
                "status": "cancelled",
                "output_preview": "(已取消)",
                "duration_ms": int(elapsed * 1000),
            }
            yield {"type": "react_cancelled", "iteration": 1}
            return
        tool_completed_naturally["flag"] = True  # should NOT happen
        yield {
            "type": "tool_end",
            "tool_name": "sleep_long",
            "tool_call_id": "c_cancel",
            "iteration": 1,
            "status": "success",
            "output_preview": r.get("stdout", ""),
            "duration_ms": int(elapsed * 1000),
        }
        yield {"type": "react_completed"}

    monkeypatch.setattr(rl, "stream_react_loop", fake_stream_with_real_subprocess)

    client, _ = gateway
    t0 = time.monotonic()
    with client.websocket_connect("/api/realtime") as ws:
        ws.send_text(
            encode_message(
                JsonRpcRequest(
                    id=1,
                    method="turn/start",
                    params={
                        "threadId": "th_cancel",
                        "input": [{"type": "text", "text": "sleep please"}],
                        "approvalPolicy": "never",
                    },
                )
            )
        )

        # Send interrupt after we see the tool_start item
        interrupted = False
        final: JsonRpcResponse | None = None
        notifications: list[Notification] = []
        turn_id: str | None = None
        while final is None:
            msg = decode_message(ws.receive_text())
            if isinstance(msg, Notification):
                notifications.append(msg)
                if msg.method == "turn/started":
                    turn_id = msg.params["turn"]["id"]
                if (
                    msg.method == "item/started"
                    and msg.params.get("item", {}).get("type") == "commandExecution"
                    and not interrupted
                ):
                    ws.send_text(
                        encode_message(
                            JsonRpcRequest(
                                id=99,
                                method="turn/interrupt",
                                params={"threadId": "th_cancel", "turnId": turn_id},
                            )
                        )
                    )
                    interrupted = True
                continue
            if isinstance(msg, JsonRpcResponse) and msg.id == 1:
                final = msg
                break
            # ignore the interrupt ack

    elapsed = time.monotonic() - t0
    assert final is not None
    # Must complete in well under the 10s sleep — cancellation must
    # propagate through the async watcher + stream_run kill path.
    assert elapsed < 3.0, f"interrupt took {elapsed:.1f}s, expected < 3s"
    assert tool_completed_naturally["flag"] is False
    assert final.result["turn"]["status"] == "cancelled"


def test_thread_resume_closes_stale_in_progress_turn(tmp_path: Path) -> None:
    from fastapi import FastAPI

    from runtime.memory.threads.event_log import EventLog
    from runtime.protocol.items import ItemStatus, Turn, TurnParams, UserMessageItem
    from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime
    from runtime.sensing.gateway.realtime_gateway import RealtimeGateway

    logs_root = tmp_path / "threads"
    log = EventLog(logs_root / "stale-thread.jsonl")
    turn = Turn(
        threadId="stale-thread",
        params=TurnParams(
            threadId="stale-thread",
            input=[{"type": "text", "text": "will never finish"}],
        ),
    )
    user_item = UserMessageItem(text="will never finish")
    user_item.status = ItemStatus.COMPLETED
    log.thread_started("stale-thread")
    log.turn_started("stale-thread", turn)
    log.item_started("stale-thread", turn.id, user_item)
    log.item_completed("stale-thread", turn.id, user_item)

    runtime = CerebrumRuntime(
        stack=object(),
        agent=object(),
        logs_root=str(logs_root),
    )
    gateway = RealtimeGateway(runtime=runtime, approval_timeout=5.0)
    app = FastAPI()
    app.include_router(gateway.router)

    with TestClient(app) as client, client.websocket_connect("/api/realtime") as ws:
        ws.send_text(
            encode_message(
                JsonRpcRequest(
                    id=1,
                    method="thread/resume",
                    params={"threadId": "stale-thread"},
                )
            )
        )
        msg = decode_message(ws.receive_text())

    assert isinstance(msg, JsonRpcResponse)
    resumed_turn = msg.result["turns"][0]
    assert resumed_turn["status"] == "failed"
    assert resumed_turn["error"]["code"] == "stale_in_progress_turn"

    replayed = log.replay()
    assert replayed[0].status.value == "failed"


@pytest.mark.asyncio()
async def test_hunk_decide_rejects_paths_outside_thread_workspace(tmp_path: Path) -> None:
    from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime
    from runtime.sensing.gateway.realtime_gateway import _RpcError

    outside = tmp_path / "outside.txt"
    outside.write_text("new\n", encoding="utf-8")
    runtime = CerebrumRuntime(
        stack=object(),
        agent=object(),
        logs_root=str(tmp_path / "threads"),
        workspace_root=str(tmp_path / "workspaces"),
    )

    class Emitter:
        async def notify(self, method: str, params: dict[str, Any]) -> None:
            raise AssertionError("no hunk decision should be broadcast for invalid paths")

    diff = "--- a/outside.txt\n+++ b/outside.txt\n@@ -1 +1 @@\n-old\n+new\n"
    with pytest.raises(_RpcError) as exc:
        await runtime._handle_hunk_decide(  # type: ignore[attr-defined]
            {
                "threadId": "thread-a",
                "turnId": "turn-a",
                "itemId": "item-a",
                "hunkId": "hunk-a",
                "path": str(outside),
                "decision": "rejected",
                "diff": diff,
            },
            Emitter(),
        )

    assert exc.value.code == JsonRpcErrorCode.INVALID_PARAMS
    assert outside.read_text(encoding="utf-8") == "new\n"


def test_meta_skill_hint_emitted_when_prompt_matches_pack(tmp_path: Path) -> None:
    """Soft hand-off: when ``match_meta_skill`` finds a strong
    keyword overlap (e.g. the bug-hunt pack's trigger phrase), the
    runtime emits ``turn/metaSkill/hint`` BEFORE ReAct kicks in. The
    ReAct loop continues normally — hint is informational so the
    user gets an answer even if they don't follow the link to the
    catalog page.

    We use the bug-hunt pack's actual trigger words ("安全审计") so
    the test stays grounded in the shipped catalog rather than a
    hand-rolled fixture that could drift from real behavior."""
    from fastapi import FastAPI

    from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime
    from runtime.sensing.gateway.realtime_gateway import RealtimeGateway
    from runtime.sensing.model_router.models import ModelResponse, ModelStreamEvent

    class FakeRouter:
        def call_stream(self, _request: Any) -> Iterator[ModelStreamEvent]:
            yield ModelStreamEvent(type="text_delta", delta="ack")
            yield ModelStreamEvent(type="done", final=ModelResponse(text="ack"))

    class FakePlanner:
        planner_model = "fake"

        def __init__(self, router: FakeRouter) -> None:
            self.router = router

    class FakeStack:
        def __init__(self, router: FakeRouter) -> None:
            self.planner = FakePlanner(router)
            self.journal = None

    runtime = CerebrumRuntime(
        stack=FakeStack(FakeRouter()),
        agent=None,
        logs_root=str(tmp_path / "threads"),
    )
    gateway = RealtimeGateway(runtime=runtime, approval_timeout=5.0)
    app = FastAPI()
    app.include_router(gateway.router)

    _set_script(
        [
            {"type": "react_completed"},
        ]
    )
    with TestClient(app) as client, client.websocket_connect("/api/realtime") as ws:
        out = _drive(
            ws,
            {
                "threadId": "th-meta-hint",
                # Two keyword tokens that overlap bug-hunt's
                # ``when_to_use``: 安全审计 / 渗透 / 漏洞 /...
                "input": [{"type": "text", "text": "帮我做一次安全审计 渗透测试"}],
                "approvalPolicy": "never",
                "model": "fake",
            },
        )

    notifications = out["notifications"]
    hints = [n for n in notifications if n.method == "turn/metaSkill/hint"]
    assert len(hints) == 1, [n.method for n in notifications]
    payload = hints[0].params
    assert payload["threadId"] == "th-meta-hint"
    assert payload["name"] == "bug-hunt"
    assert payload["stepCount"] >= 1
    assert "security" in payload["affinity"]


def test_meta_skill_hint_silent_when_no_match(tmp_path: Path) -> None:
    """Casual prompts that don't match any pack must NOT trigger a
    hint — the chip is reserved for real workflow intent. A plain
    ``2+2等于几`` should pass through with zero meta-skill events."""
    from fastapi import FastAPI

    from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime
    from runtime.sensing.gateway.realtime_gateway import RealtimeGateway
    from runtime.sensing.model_router.models import ModelResponse, ModelStreamEvent

    class FakeRouter:
        def call_stream(self, _request: Any) -> Iterator[ModelStreamEvent]:
            yield ModelStreamEvent(type="text_delta", delta="4")
            yield ModelStreamEvent(type="done", final=ModelResponse(text="4"))

    class FakePlanner:
        planner_model = "fake"

        def __init__(self, router: FakeRouter) -> None:
            self.router = router

    class FakeStack:
        def __init__(self, router: FakeRouter) -> None:
            self.planner = FakePlanner(router)
            self.journal = None

    runtime = CerebrumRuntime(
        stack=FakeStack(FakeRouter()),
        agent=None,
        logs_root=str(tmp_path / "threads"),
    )
    gateway = RealtimeGateway(runtime=runtime, approval_timeout=5.0)
    app = FastAPI()
    app.include_router(gateway.router)

    _set_script([{"type": "react_completed"}])
    with TestClient(app) as client, client.websocket_connect("/api/realtime") as ws:
        out = _drive(
            ws,
            {
                "threadId": "th-no-hint",
                "input": [{"type": "text", "text": "2+2等于几？"}],
                "approvalPolicy": "never",
                "model": "fake",
            },
        )

    hints = [n for n in out["notifications"] if n.method == "turn/metaSkill/hint"]
    assert hints == []


def test_turn_survives_ws_disconnect_and_runs_server_side(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Audit T-01: a dropped WebSocket must NOT cancel a running turn.

    Turns run as server-resident tasks behind a detached emitter, so a
    network switch, lid-close, or transient disconnect cannot destroy a
    long Agent run. The fake producer must finish naturally after the
    requester disconnects, and the resident task must drain on its own.
    """
    import threading
    import time

    import runtime.core.cerebrum.react_loop as rl
    from runtime.safety.approval.cancellation import current_cancellation_token
    from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime
    from runtime.sensing.gateway.realtime_gateway import RealtimeGateway

    observed: dict[str, Any] = {"token_tripped": None}
    first_event_sent = threading.Event()

    def fake_stream(*args: Any, **kwargs: Any) -> Iterator[dict[str, Any]]:
        # Yield one event so the turn is clearly in-flight, then keep the
        # producer alive briefly while polling the cancellation token.
        yield {"type": "text_delta", "delta": "working"}
        first_event_sent.set()
        token = current_cancellation_token()
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            if token.is_cancelled:
                observed["token_tripped"] = True
                return
            time.sleep(0.05)
        observed["token_tripped"] = False

    monkeypatch.setattr(rl, "stream_react_loop", fake_stream)

    runtime = CerebrumRuntime(
        stack=object(),
        agent=object(),
        logs_root=str(tmp_path / "threads"),
    )
    gateway = RealtimeGateway(runtime=runtime, approval_timeout=5.0)
    app = FastAPI()
    app.include_router(gateway.router)

    with TestClient(app) as client:
        # Nested (not combined) on purpose: we exit the inner ws context
        # to simulate a mid-turn disconnect while staying inside client
        # to poll the outcome afterward.
        with client.websocket_connect("/api/realtime") as ws:
            ws.send_text(
                encode_message(
                    JsonRpcRequest(
                        id=1,
                        method="turn/start",
                        params={
                            "threadId": "th-disconnect",
                            "input": [{"type": "text", "text": "go"}],
                            "approvalPolicy": "never",
                        },
                    )
                )
            )
            # Wait until the producer has started streaming, then leave
            # the context → WebSocket disconnects mid-turn.
            assert first_event_sent.wait(timeout=5.0), "producer never started"
        # ws is now closed; the server-resident turn must keep running.

        deadline = time.monotonic() + 5.0
        while observed["token_tripped"] is None and time.monotonic() < deadline:
            time.sleep(0.05)

        assert observed["token_tripped"] is False, (
            "turn was cancelled by the ws disconnect — long turns must "
            "survive connection loss and run on server-side (audit T-01)"
        )

        deadline = time.monotonic() + 10.0
        while gateway._resident_turn_tasks and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not gateway._resident_turn_tasks, (
            "resident turn did not drain after the requester left"
        )
