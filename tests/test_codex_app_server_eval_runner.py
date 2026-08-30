from __future__ import annotations

from benchmarks.codex_app_server_runner import (
    _app_server_message_to_eval,
    _codex_child_environment,
)
from benchmarks.run_behavioral_suite import _codex_approval_responder


def test_app_server_maps_desktop_tool_and_agent_events() -> None:
    emitted: set[str] = set()
    assert _app_server_message_to_eval(
        {
            "method": "item/agentMessage/delta",
            "params": {"itemId": "answer-1", "delta": "done"},
        },
        emitted_agent_items=emitted,
    ) == [{"kind": "text_delta", "delta": "done"}]
    assert emitted == {"answer-1"}

    assert _app_server_message_to_eval(
        {
            "method": "item/started",
            "params": {
                "item": {
                    "id": "browser-1",
                    "type": "dynamicToolCall",
                    "tool": "browser_navigate",
                }
            },
        },
        emitted_agent_items=emitted,
    ) == [
        {
            "kind": "tool_start",
            "tool_name": "browser_navigate",
            "item": {
                "id": "browser-1",
                "type": "dynamicToolCall",
                "tool": "browser_navigate",
            },
        }
    ]

    subagent = _app_server_message_to_eval(
        {
            "method": "item/started",
            "params": {
                "item": {
                    "id": "agent-1",
                    "type": "collabAgentToolCall",
                    "tool": "spawn_agent",
                }
            },
        },
        emitted_agent_items=emitted,
    )
    assert subagent[0]["kind"] == "tool_start"
    assert subagent[0]["tool_name"] == "subagent"

    completed_activity = _app_server_message_to_eval(
        {
            "method": "item/completed",
            "params": {
                "item": {
                    "id": "activity-1",
                    "type": "subAgentActivity",
                    "agentPath": "/root/worker",
                    "agentThreadId": "thread-1",
                    "kind": "started",
                }
            },
        },
        emitted_agent_items=emitted,
    )
    assert completed_activity[0]["kind"] == "tool_start"


def test_successful_transport_fallback_is_warning_not_failed_trial() -> None:
    events = _app_server_message_to_eval(
        {
            "method": "error",
            "params": {
                "message": "Reconnecting... 2/5",
                "additionalDetails": "response stream disconnected: tls handshake eof",
            },
        },
        emitted_agent_items=set(),
    )
    assert events == [{"kind": "transport_warning", "message": "Reconnecting... 2/5"}]


def test_codex_denial_responder_is_selective() -> None:
    responder = _codex_approval_responder("security.denied-destructive-action")
    assert responder is not None
    assert responder(
        "item/commandExecution/requestApproval",
        {"command": "sqlite3 data.db < migration.sql"},
    ) == {"decision": "decline"}
    assert responder(
        "item/commandExecution/requestApproval",
        {"command": "python verify_safe_plan.py"},
    ) == {"decision": "accept"}
    assert responder("item/fileChange/requestApproval", {}) == {"decision": "accept"}
    assert _codex_approval_responder("coding.path-boundary") is None


def test_codex_child_environment_removes_all_echo_state(monkeypatch) -> None:
    monkeypatch.setenv("ECHO_API_TOKEN", "system-under-test-token")
    monkeypatch.setenv("ECHO_EVAL_LOCAL_PASSWORD", "system-under-test-password")
    monkeypatch.setenv("ECHO_EVAL_CONFIG", "/private/echo/config.yaml")
    monkeypatch.setenv("echo_future_secret", "future-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "provider-key-used-by-echo")
    monkeypatch.setenv("GH_TOKEN", "runner-token")
    monkeypatch.setenv("CODEX_HOME", "/signed-in/codex-profile")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    child = _codex_child_environment()

    assert all(not key.upper().startswith("ECHO_") for key in child)
    assert "ANTHROPIC_API_KEY" not in child
    assert "GH_TOKEN" not in child
    assert child["CODEX_HOME"] == "/signed-in/codex-profile"
    assert child["PATH"] == "/usr/bin:/bin"
    assert child["NO_COLOR"] == "1"


