"""Tests for realtime cerebrum resume — thread artifacts, resume proposals, confirmation flow, runtime restart."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    FastAPI = None  # type: ignore[assignment]
    TestClient = None  # type: ignore[assignment]

from tests.realtime_cerebrum._helpers import (
    _LAST_SESSION,
    _LAST_STREAM_KWARGS,
)
from tests.realtime_cerebrum._helpers import (
    drive as _drive,
)
from tests.realtime_cerebrum._helpers import (
    set_script as _set_script,
)


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


def test_resume_proposal_block_preserves_sanitized_tool_context() -> None:
    from runtime.protocol import TurnParams
    from runtime.sensing.gateway.realtime_cerebrum import _build_intent

    resume_text = """
Resume this agent run from the selected durable checkpoint.

<echo_resume_proposal>
{
  "schema": "echo.resume_proposal.v1",
  "checkpoint_id": 8,
  "task_id": "task-tools",
  "checkpoint_type": "react",
  "iteration": 4,
  "phase": "verify",
  "recent_tool_calls": [
    {
      "iteration": 3,
      "tool": "exec_shell",
      "input_preview": "pytest tests/test_x.py -q",
      "observation_preview": "failed: assertion message body"
    }
  ],
  "raw_state_included": false,
  "raw_message_snapshots_included": false
}
</echo_resume_proposal>
""".strip()
    intent = _build_intent(
        resume_text,
        TurnParams(
            threadId="th-resume-tool-context",
            input=[{"type": "text", "text": resume_text}],
            approvalPolicy="on-request",
        ),
    )

    resume_intent = intent.user_context["resume_intent"]
    assert resume_intent["recent_tool_calls"] == [
        {
            "iteration": 3,
            "tool": "exec_shell",
            "input_preview": "pytest tests/test_x.py -q",
            "observation_preview": "failed: assertion message body",
        }
    ]
    assert resume_intent["safety"]["raw_state_included"] is False
    assert "messages_snapshot" not in str(resume_intent)


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
  "recent_tool_calls": [
    {
      "iteration": 2,
      "tool": "read_file",
      "input_preview": "{\\"path\\": \\"runtime/sensing/siphon/realtime_cerebrum.py\\"}",
      "observation_preview": "read file"
    }
  ],
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
        assert resume_intent["recent_tool_calls"][0]["tool"] == "read_file"
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


def test_plain_continue_resumes_latest_paused_task_and_adds_iteration_budget(
    tmp_path: Path,
) -> None:
    from fastapi import FastAPI

    from runtime.core.cerebrum.pause_control import get_pause_controller
    from runtime.memory.diagnostics.trace_store import AgentTraceStore
    from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime
    from runtime.sensing.gateway.realtime_gateway import RealtimeGateway

    thread_id = "th-plain-continue"
    old_task_id = str(uuid4())
    task_id = str(uuid4())
    trace = AgentTraceStore(tmp_path / "trace.sqlite")
    checkpoint_id = trace.record_checkpoint(
        task_id=task_id,
        thread_id=thread_id,
        checkpoint_type="react",
        iteration=27,
        state={
            "iteration_completed": 27,
            "max_iterations": 30,
            "messages_snapshot": [],
            "steps_snapshot": [],
            "working_set_snapshot": [{"path": "runtime/example.py"}],
            "progress_summary": "implementation in progress",
            "current_phase": "implementation",
        },
    )
    controller = get_pause_controller()
    for paused_task_id in (old_task_id, task_id):
        controller.request_pause(
            paused_task_id,
            reason="iteration_near_limit",
            requested_by="system",
            thread_id=thread_id,
        )
        controller.mark_paused(paused_task_id)

    runtime = CerebrumRuntime(
        stack=object(),
        agent=None,
        logs_root=str(tmp_path / "threads"),
        trace_store=trace,
    )
    app = FastAPI()
    app.include_router(RealtimeGateway(runtime=runtime, approval_timeout=5.0).router)
    try:
        with TestClient(app) as client, client.websocket_connect("/api/realtime") as ws:
            _set_script([{"type": "react_completed"}])
            _drive(
                ws,
                {
                    "threadId": thread_id,
                    "input": [{"type": "text", "text": "继续"}],
                    "approvalPolicy": "on-request",
                },
            )

        assert str(_LAST_STREAM_KWARGS["resume_task_id"]) == task_id
        resume_intent = _LAST_SESSION["metadata"]["resume_intent"]
        assert resume_intent["source"] == "paused_task_continue"
        assert resume_intent["checkpoint_id"] == checkpoint_id
        assert resume_intent["iteration"] == 27
        assert resume_intent["working_set"] == ["runtime/example.py"]
        assert controller.consume_grant(task_id)["extra_iterations"] == 15
        assert controller.get_request(old_task_id) is None
    finally:
        controller.clear(old_task_id)
        controller.clear(task_id)
        trace.close()


def test_short_question_resumes_single_budget_paused_task_with_token_runway(
    tmp_path: Path,
) -> None:
    from fastapi import FastAPI

    from runtime.core.cerebrum.pause_control import get_pause_controller
    from runtime.memory.diagnostics.trace_store import AgentTraceStore
    from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime
    from runtime.sensing.gateway.realtime_gateway import RealtimeGateway

    thread_id = "th-budget-question-continue"
    task_id = str(uuid4())
    trace = AgentTraceStore(tmp_path / "trace.sqlite")
    trace.record_checkpoint(
        task_id=task_id,
        thread_id=thread_id,
        checkpoint_type="react",
        iteration=6,
        state={"iteration_completed": 6, "current_phase": "verify"},
    )
    controller = get_pause_controller()
    controller.request_pause(
        task_id,
        reason="budget_near_limit",
        requested_by="system",
        thread_id=thread_id,
    )
    controller.mark_paused(task_id)
    runtime = CerebrumRuntime(
        stack=object(),
        agent=None,
        logs_root=str(tmp_path / "threads"),
        trace_store=trace,
    )
    app = FastAPI()
    app.include_router(RealtimeGateway(runtime=runtime, approval_timeout=5.0).router)
    try:
        with TestClient(app) as client, client.websocket_connect("/api/realtime") as ws:
            _set_script([{"type": "react_completed"}])
            _drive(
                ws,
                {
                    "threadId": thread_id,
                    "input": [{"type": "text", "text": "？"}],
                    "approvalPolicy": "on-request",
                },
            )

        assert str(_LAST_STREAM_KWARGS["resume_task_id"]) == task_id
        grant = controller.consume_grant(task_id)
        assert grant["extra_tokens"] == 100_000
        assert grant["extra_usd"] == 0.0
    finally:
        controller.clear(task_id)
        trace.close()


def test_longer_continue_instruction_does_not_hijack_a_paused_task(
    tmp_path: Path,
) -> None:
    from fastapi import FastAPI

    from runtime.core.cerebrum.pause_control import get_pause_controller
    from runtime.memory.diagnostics.trace_store import AgentTraceStore
    from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime
    from runtime.sensing.gateway.realtime_gateway import RealtimeGateway

    thread_id = "th-continue-new-instruction"
    task_id = str(uuid4())
    trace = AgentTraceStore(tmp_path / "trace.sqlite")
    trace.record_checkpoint(
        task_id=task_id,
        thread_id=thread_id,
        checkpoint_type="react",
        iteration=3,
        state={"iteration_completed": 3},
    )
    controller = get_pause_controller()
    controller.request_pause(
        task_id,
        reason="iteration_near_limit",
        requested_by="system",
        thread_id=thread_id,
    )
    controller.mark_paused(task_id)

    runtime = CerebrumRuntime(
        stack=object(),
        agent=None,
        logs_root=str(tmp_path / "threads"),
        trace_store=trace,
    )
    app = FastAPI()
    app.include_router(RealtimeGateway(runtime=runtime, approval_timeout=5.0).router)
    try:
        with TestClient(app) as client, client.websocket_connect("/api/realtime") as ws:
            _set_script([{"type": "react_completed"}])
            _drive(
                ws,
                {
                    "threadId": thread_id,
                    "input": [{"type": "text", "text": "继续分析另一个新需求"}],
                    "approvalPolicy": "on-request",
                },
            )

        assert _LAST_STREAM_KWARGS["resume_task_id"] is None
        assert "resume_intent" not in _LAST_SESSION["metadata"]
        assert controller.is_paused(task_id)
    finally:
        controller.clear(task_id)
        trace.close()


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

