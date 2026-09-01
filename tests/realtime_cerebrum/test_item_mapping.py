"""Tests for realtime cerebrum item mapping — flatten, text_delta, todo_write, team subagent, blocked topology, background tools."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    FastAPI = None  # type: ignore[assignment]
    TestClient = None  # type: ignore[assignment]

from runtime.protocol import (
    JsonRpcRequest,
    JsonRpcResponse,
    decode_message,
    encode_message,
)
from tests.realtime_cerebrum._helpers import (
    drive as _drive,
)
from tests.realtime_cerebrum._helpers import (
    set_script as _set_script,
)


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
    assert "reasoning_content" not in ai["additional_kwargs"]
    assert "public_reasoning_summary" not in ai["additional_kwargs"]
    assert [tool["name"] for tool in ai["tool_calls"]] == ["todo_write"]


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
    # SUNSET: embedded workbenchSnapshot no longer ships on
    # turn/plan/updated by default (dedicated workbench/snapshot below
    # carries the identical frame).
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


def test_blocked_topology_id_falls_back_to_react(
    gateway: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from runtime.safety.evolution.subagent_policy import SubagentPolicyStore
    from runtime.safety.organization import (
        AgentSpec,
        CoordinationProtocol,
        Role,
        TeamTopology,
    )

    client, _ = gateway
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    monkeypatch.setenv("ECHO_DATA_DIR", str(data_dir))
    SubagentPolicyStore(data_dir / "subagent_policy.json").decide(
        "planner_a",
        action="retire",
        reason="operator retired planner_a",
        actor="operator-test",
    )
    topology = TeamTopology(
        name="test_topology",
        protocol=CoordinationProtocol.SEQUENTIAL,
        agents={Role.PLANNER: AgentSpec(agent_id="planner_a")},
    )
    called = {"team_runner": False}

    class FakeTeamRunner:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def run(self, *args: Any, **kwargs: Any) -> Any:
            called["team_runner"] = True
            raise AssertionError("blocked topology should not enter TeamRunner")

    monkeypatch.setattr(
        "runtime.safety.organization.forge.load_registry",
        lambda: {"test_topology": topology},
    )
    monkeypatch.setattr(
        "runtime.safety.organization.team_runner.TeamRunner",
        FakeTeamRunner,
    )
    _set_script(
        [
            {"type": "text_delta", "delta": "fallback react"},
            {"type": "react_completed"},
        ]
    )

    with client.websocket_connect("/api/realtime") as ws:
        out = _drive(
            ws,
            {
                "threadId": "th-blocked-topology",
                "input": [{"type": "text", "text": "run blocked topology"}],
                "approvalPolicy": "never",
                "topologyId": "test_topology",
            },
        )

    assert called["team_runner"] is False
    turn = out["response"].result["turn"]
    agent_items = [it for it in turn["items"] if it["type"] == "agentMessage"]
    assert agent_items[-1]["text"] == "fallback react"
    audit = json.loads((data_dir / "promotion_audit.json").read_text(encoding="utf-8"))
    assert audit["records"][0]["event_type"] == "topology_policy_block"
    assert audit["records"][0]["target"] == "topology_policy"
    assert audit["records"][0]["status"] == "blocked"
    assert audit["records"][0]["artifact"]["topology_id"] == "test_topology"
    assert audit["records"][0]["decision_context"]["turn_id"] == turn["id"]


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

