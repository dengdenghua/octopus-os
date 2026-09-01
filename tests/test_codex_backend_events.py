from __future__ import annotations

from runtime.execution.codex_backend.events import (
    CodexEventState,
    translate_notification,
)
from runtime.execution.codex_backend.types import Notification


def _notification(method: str, **params: object) -> Notification:
    return Notification(method=method, params=params)  # type: ignore[arg-type]


def test_streamed_agent_message_is_not_duplicated_on_completion() -> None:
    state = CodexEventState()

    assert translate_notification(
        _notification(
            "item/agentMessage/delta",
            threadId="thread-1",
            turnId="turn-1",
            itemId="message-1",
            delta="hello",
        ),
        state,
    ) == [{"type": "text_delta", "delta": "hello"}]
    assert translate_notification(
        _notification(
            "item/completed",
            threadId="thread-1",
            turnId="turn-1",
            item={"id": "message-1", "type": "agentMessage", "text": "hello"},
        ),
        state,
    ) == [{"type": "react_step_complete"}]


def test_completed_agent_message_recovers_when_delta_was_not_observed() -> None:
    state = CodexEventState()

    assert translate_notification(
        _notification(
            "item/completed",
            threadId="thread-1",
            turnId="turn-1",
            item={"id": "message-1", "type": "agentMessage", "text": "final answer"},
        ),
        state,
    ) == [
        {"type": "text_delta", "delta": "final answer"},
        {"type": "react_step_complete"},
    ]


def test_agent_message_phase_routes_commentary_from_first_delta() -> None:
    state = CodexEventState()

    assert (
        translate_notification(
            _notification(
                "item/started",
                threadId="thread-1",
                turnId="turn-1",
                item={
                    "id": "message-1",
                    "type": "agentMessage",
                    "text": "",
                    "phase": "commentary",
                },
            ),
            state,
        )
        == []
    )
    assert (
        translate_notification(
            _notification(
                "item/agentMessage/delta",
                threadId="thread-1",
                turnId="turn-1",
                itemId="message-1",
                delta="",
            ),
            state,
        )
        == []
    )
    assert translate_notification(
        _notification(
            "item/agentMessage/delta",
            threadId="thread-1",
            turnId="turn-1",
            itemId="message-1",
            delta="先检查入口",
        ),
        state,
    ) == [
        {
            "type": "commentary_delta",
            "delta": "先检查入口",
            "public_status": True,
            "start_new_segment": True,
        }
    ]
    assert translate_notification(
        _notification(
            "item/agentMessage/delta",
            threadId="thread-1",
            turnId="turn-1",
            itemId="message-1",
            delta="，再调用工具。",
        ),
        state,
    ) == [
        {
            "type": "commentary_delta",
            "delta": "，再调用工具。",
            "public_status": True,
            "start_new_segment": False,
        }
    ]
    assert translate_notification(
        _notification(
            "item/completed",
            threadId="thread-1",
            turnId="turn-1",
            item={
                "id": "message-1",
                "type": "agentMessage",
                "text": "先检查入口，再调用工具。",
                "phase": "commentary",
            },
        ),
        state,
    ) == [{"type": "react_step_complete"}]


def test_agent_message_phase_keeps_final_answer_in_answer_lane() -> None:
    state = CodexEventState()

    assert (
        translate_notification(
            _notification(
                "item/started",
                threadId="thread-1",
                turnId="turn-1",
                item={
                    "id": "message-final",
                    "type": "agentMessage",
                    "text": "",
                    "phase": "final_answer",
                },
            ),
            state,
        )
        == []
    )
    assert translate_notification(
        _notification(
            "item/agentMessage/delta",
            threadId="thread-1",
            turnId="turn-1",
            itemId="message-final",
            delta="最终结论",
        ),
        state,
    ) == [{"type": "text_delta", "delta": "最终结论"}]


def test_completed_commentary_recovers_phase_when_start_and_delta_were_missed() -> None:
    state = CodexEventState()

    assert translate_notification(
        _notification(
            "item/completed",
            threadId="thread-1",
            turnId="turn-1",
            item={
                "id": "message-1",
                "type": "agentMessage",
                "text": "继续核对证据。",
                "phase": "commentary",
            },
        ),
        state,
    ) == [
        {
            "type": "commentary_delta",
            "delta": "继续核对证据。",
            "public_status": True,
            "start_new_segment": True,
        },
        {"type": "react_step_complete"},
    ]


def test_command_execution_projects_to_native_tool_lifecycle() -> None:
    state = CodexEventState()
    started = _notification(
        "item/started",
        threadId="thread-1",
        turnId="turn-1",
        item={
            "id": "command-1",
            "type": "commandExecution",
            "command": "pytest -q",
            "cwd": "/workspace",
            "commandActions": [],
            "status": "inProgress",
        },
    )
    completed = _notification(
        "item/completed",
        threadId="thread-1",
        turnId="turn-1",
        item={
            "id": "command-1",
            "type": "commandExecution",
            "command": "pytest -q",
            "cwd": "/workspace",
            "commandActions": [],
            "status": "completed",
            "aggregatedOutput": "12 passed",
            "exitCode": 0,
            "durationMs": 125,
        },
    )

    assert translate_notification(started, state) == [
        {
            "type": "tool_start",
            "tool_name": "exec_shell",
            "tool_call_id": "command-1",
            "input_preview": {
                "command": "pytest -q",
                "cwd": "/workspace",
                "actions": [],
                "process_id": "",
            },
            "public_description": "",
        }
    ]
    assert translate_notification(completed, state) == [
        {
            "type": "tool_end",
            "tool_name": "exec_shell",
            "tool_call_id": "command-1",
            "status": "success",
            "output_preview": "12 passed",
            "duration_ms": 125,
            "exit_code": 0,
        }
    ]


def test_file_change_completion_preserves_paths_diffs_and_operations() -> None:
    state = CodexEventState()

    events = translate_notification(
        _notification(
            "item/completed",
            threadId="thread-1",
            turnId="turn-1",
            item={
                "id": "patch-1",
                "type": "fileChange",
                "status": "completed",
                "changes": [
                    {
                        "path": "/workspace/new.py",
                        "kind": {"type": "add"},
                        "diff": "+print('ok')",
                    },
                    {
                        "path": "/workspace/old.py",
                        "kind": {"type": "delete"},
                        "diff": "-pass",
                    },
                ],
            },
        ),
        state,
    )

    assert events[0]["type"] == "tool_start"
    assert events[1]["type"] == "tool_end"
    assert events[1]["file_changes"] == [
        {"path": "/workspace/new.py", "op": "create", "diff": "+print('ok')"},
        {"path": "/workspace/old.py", "op": "delete", "diff": "-pass"},
    ]


def test_turn_and_usage_notifications_preserve_terminal_semantics() -> None:
    state = CodexEventState()

    assert translate_notification(
        _notification(
            "thread/tokenUsage/updated",
            threadId="thread-1",
            turnId="turn-1",
            tokenUsage={"total": {"totalTokens": 42}},
        ),
        state,
    ) == [{"type": "throughput", "usage": {"total": {"totalTokens": 42}}}]
    assert translate_notification(
        _notification(
            "turn/completed",
            threadId="thread-1",
            turn={"id": "turn-1", "status": "interrupted"},
        ),
        state,
    ) == [{"type": "react_cancelled", "reason": "interrupted"}]


def test_unknown_notification_is_ignored_at_protocol_boundary() -> None:
    assert (
        translate_notification(
            _notification("account/rateLimits/updated", opaque={"secret": "not exposed"}),
            CodexEventState(),
        )
        == []
    )


def test_retryable_error_is_status_only_but_terminal_error_fails() -> None:
    state = CodexEventState()

    assert translate_notification(
        _notification(
            "error",
            threadId="thread-1",
            turnId="turn-1",
            message="temporary upstream reset",
            willRetry=True,
        ),
        state,
    ) == [
        {
            "type": "commentary_delta",
            "delta": "当前模型服务遇到暂时性错误，正在自动重试。",
            "public_status": True,
            "start_new_segment": True,
        }
    ]
    assert translate_notification(
        _notification(
            "error",
            threadId="thread-1",
            turnId="turn-1",
            message="permanent failure",
            willRetry=False,
        ),
        state,
    ) == [
        {
            "type": "react_error",
            "kind": "codex_app_server_error",
            "message": "permanent failure",
        }
    ]

