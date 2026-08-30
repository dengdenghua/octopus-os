from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from runtime.execution.tool_engine.tool_protocol import (
    NormalizedToolCall,
    NormalizedToolLifecycleEvent,
    NormalizedToolResult,
    normalize_step_tool_result,
    normalize_task_node_tool_call,
    normalize_tool_call,
    normalize_tool_lifecycle_event,
    normalize_tool_result,
    output_signals_error,
    render_tool_output,
    tool_lifecycle_event_to_react_event,
    tool_lifecycle_event_to_trace_payload,
)
from runtime.platform.models import ExecutionResult, SkillId, Step, TaskNode, ToolCall


def test_normalize_native_tool_call_object():
    call = SimpleNamespace(
        id="tool-1",
        name="read_file",
        input={"path": "README.md"},
    )

    normalized = normalize_tool_call(call, origin="native")

    assert normalized == NormalizedToolCall(
        id="tool-1",
        name="read_file",
        arguments={"path": "README.md"},
        origin="native",
    )


def test_normalize_dict_tool_call_accepts_compat_keys():
    normalized = normalize_tool_call(
        {
            "tool_use_id": "call-1",
            "tool": "write_text_file",
            "arguments": {"path": "out.txt", "content": "ok"},
        },
        origin="react_compat",
    )

    assert normalized.id == "call-1"
    assert normalized.name == "write_text_file"
    assert normalized.arguments == {"path": "out.txt", "content": "ok"}
    assert normalized.origin == "react_compat"


def test_normalize_missing_name_fails_closed():
    with pytest.raises(ValueError, match="missing name"):
        normalize_tool_call({"id": "call-1", "input": {}})


def test_normalize_task_node_tool_call_marks_planner_origin():
    node = TaskNode(node_id="n1", skill_ref=SkillId("edit_file"))

    normalized = normalize_task_node_tool_call(
        node,
        {"path": "a.txt"},
        node_index=1,
    )

    assert normalized == NormalizedToolCall(
        id="n1",
        name="edit_file",
        arguments={"path": "a.txt"},
        origin="planner_compat",
    )


def test_normalize_task_node_tool_call_requires_skill_ref():
    node = TaskNode(node_id="n1", skill_ref=None)

    with pytest.raises(ValueError, match="missing skill_ref"):
        normalize_task_node_tool_call(node, {}, node_index=1)


def test_normalize_tool_result_renders_and_flags_semantic_error():
    result = normalize_tool_result(
        {"id": "call-1", "name": "search", "input": {}},
        {"ok": False, "error": "not found"},
        origin="native",
    )

    assert result == NormalizedToolResult(
        id="call-1",
        name="search",
        output={"ok": False, "error": "not found"},
        rendered='{"ok": false, "error": "not found"}',
        is_error=True,
        status="success",
        error_type=None,
        origin="native",
    )


def test_render_tool_output_applies_bound():
    rendered = render_tool_output("abcdef", max_chars=3)

    assert rendered == "abc\n\n...(truncated, 3 more chars)"


def test_output_signals_error_conventions():
    assert output_signals_error({"ok": False})
    assert output_signals_error({"success": False})
    assert output_signals_error({"exit_code": 2})
    assert output_signals_error({"error": "boom"})
    assert output_signals_error({"status": "failed"})
    assert not output_signals_error({"success": True, "exit_code": 0})
    assert not output_signals_error({"ok": True, "error": "ignored"})
    assert not output_signals_error("error as plain text")


def test_normalize_step_tool_result_uses_execution_step_shape():
    step = Step(
        step_id=0,
        node_id="n0",
        action=ToolCall(
            caller="planner",
            sucker_id=SkillId("read_file"),
            args={"path": "README.md"},
        ),
        result=ExecutionResult(
            call_id=uuid4(),
            status="failed",
            output={"error": "missing"},
            error_type="FileNotFound",
        ),
    )

    result = normalize_step_tool_result(
        step,
        origin="planner_compat",
        max_chars=100,
    )

    assert result.name == "read_file"
    assert result.output == {"error": "missing"}
    assert result.is_error is True
    assert result.status == "failed"
    assert result.error_type == "FileNotFound"
    assert result.origin == "planner_compat"


def test_normalize_step_tool_result_accepts_fallback_call():
    step = SimpleNamespace(
        result=SimpleNamespace(status="success", output={"ok": True}),
    )

    result = normalize_step_tool_result(
        step,
        origin="react_compat",
        fallback_call={"id": "react:1", "name": "echo", "arguments": {}},
    )

    assert result.id == "react:1"
    assert result.name == "echo"
    assert result.rendered == '{"ok": true}'
    assert result.is_error is False


def test_normalize_tool_lifecycle_event_accepts_native_shape():
    event = normalize_tool_lifecycle_event(
        "tool_end",
        {
            "id": "call-1",
            "name": "read_file",
            "output": "not found",
            "is_error": True,
            "iteration": 2,
            "parallel": True,
        },
        origin="native",
    )

    assert event == NormalizedToolLifecycleEvent(
        kind="tool_end",
        id="call-1",
        name="read_file",
        iteration=2,
        input=None,
        output="not found",
        status=None,
        is_error=True,
        duration_ms=None,
        origin="native",
        extras={"parallel": True},
    )


def test_normalize_tool_lifecycle_event_accepts_trace_compat_aliases():
    event = normalize_tool_lifecycle_event(
        "tool_start",
        {
            "call_id": "call-2",
            "tool": "exec_shell",
            "args_preview": "pytest tests/test_x.py",
        },
        origin="react_compat",
    )

    assert event.id == "call-2"
    assert event.name == "exec_shell"
    assert event.input == "pytest tests/test_x.py"
    assert event.extras == {}


def test_tool_lifecycle_event_renders_react_shape():
    event = normalize_tool_lifecycle_event(
        "tool_start",
        {
            "tool_call_id": "call-1",
            "tool_name": "read_file",
            "input_preview": {"path": "README.md"},
            "iteration": 1,
        },
        origin="react_compat",
    )

    assert tool_lifecycle_event_to_react_event(event) == {
        "type": "tool_start",
        "tool_call_id": "call-1",
        "tool_name": "read_file",
        "iteration": 1,
        "input_preview": {"path": "README.md"},
    }


def test_tool_lifecycle_event_preserves_operator_safe_effect_signal():
    signal = {
        "effect_key": "effect:v1:abc",
        "call_id": "call-1",
        "state": "indeterminate",
        "reason": "outcome unknown",
        "fencing_token": 3,
    }
    event = normalize_tool_lifecycle_event(
        "tool_end",
        {
            "tool_call_id": "call-1",
            "tool_name": "write_file",
            "status": "error",
            "output_preview": "outcome unknown",
            "effect_receipt": signal,
        },
        origin="react_compat",
    )

    assert event.extras["effect_receipt"] == signal
    assert tool_lifecycle_event_to_react_event(event)["effect_receipt"] == signal


def test_tool_lifecycle_event_renders_trace_payload_with_aliases():
    event = normalize_tool_lifecycle_event(
        "tool_end",
        {
            "tool_call_id": "call-1",
            "tool_name": "exec_shell",
            "status": "error",
            "output_preview": "failed",
            "iteration": 2,
            "duration_ms": 15,
            "verification": {"kind": "test"},
        },
        origin="react_compat",
    )

    assert tool_lifecycle_event_to_trace_payload(event) == {
        "id": "call-1",
        "name": "exec_shell",
        "tool_call_id": "call-1",
        "tool": "exec_shell",
        "origin": "react_compat",
        "iteration": 2,
        "status": "error",
        "is_error": True,
        "output": "failed",
        "output_preview": "failed",
        "duration_ms": 15,
        "verification": {"kind": "test"},
    }

