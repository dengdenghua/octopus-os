"""Tests for realtime cerebrum tool lifecycle — hooks, planning mode, thinking, tool round-trip, errors, thread management."""

from __future__ import annotations

from typing import Any

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
    decode_message,
    encode_message,
)
from tests.realtime_cerebrum._helpers import (
    _LAST_SESSION,
    _LAST_STREAM_ARGS,
    _LAST_STREAM_KWARGS,
)
from tests.realtime_cerebrum._helpers import (
    drive as _drive,
)
from tests.realtime_cerebrum._helpers import (
    set_script as _set_script,
)


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


def test_thinking_delta_is_streamed_as_reasoning(gateway: Any) -> None:
    # Since the streaming-UX work (live thinking typewriter + foldable
    # reasoning rows), provider thinking deltas are surfaced as a
    # ReasoningItem instead of being dropped as private chain-of-thought.
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

    reasoning_deltas = [n for n in out["notifications"] if n.method == "item/reasoning/textDelta"]
    assert len(reasoning_deltas) >= 1
    assert "".join(n.params["delta"] for n in reasoning_deltas) == "step 1\nstep 2"

    turn = out["response"].result["turn"]
    r_items = [it for it in turn["items"] if it["type"] == "reasoning"]
    assert len(r_items) == 1
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

