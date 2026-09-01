"""Tool-bridge prune wiring — dsh head+marker+tail on the native path."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from runtime.execution.suckers import Skill, SkillRegistry
from runtime.execution.tool_engine import ToolExecutor
from runtime.execution.tool_engine.tool_output_pruner import (
    DEFAULT_PRUNE_HEAD_CHARS,
    DEFAULT_PRUNE_TAIL_CHARS,
    PRUNE_MARKER,
)
from runtime.safety.auth import TrustEngine
from runtime.sensing.gateway import _tool_bridge_exec as _tbe


def _stack_with_long_output(skill_name: str = "long_tool", size: int = 20000) -> Any:
    reg = SkillRegistry()
    reg.register(
        Skill(
            name=skill_name,
            description="Long output.",
            trusted_source=f"skill://public/{skill_name}",
            handler=lambda **_kwargs: "x" * size,
        ),
        verify_tests=False,
    )
    stack = SimpleNamespace()
    stack.executor = ToolExecutor(reg, TrustEngine())
    return stack


def test_bridge_output_uses_prune_by_default() -> None:
    call = {"id": f"tool-{uuid4().hex[:8]}", "name": "long_tool", "input": {}}
    output, is_error = _tbe._execute_tool_call(_stack_with_long_output(), call)
    assert is_error is False
    assert PRUNE_MARKER in output
    assert "x" * DEFAULT_PRUNE_HEAD_CHARS in output
    assert "x" * DEFAULT_PRUNE_TAIL_CHARS in output
    assert len(output) < 20000


def test_bridge_output_head_truncates_when_switch_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_tbe, "TOOL_RESULT_PRUNE_ENABLED", False)
    call = {"id": f"tool-{uuid4().hex[:8]}", "name": "long_tool", "input": {}}
    output, is_error = _tbe._execute_tool_call(_stack_with_long_output(), call)
    assert is_error is False
    assert PRUNE_MARKER not in output
    assert "(truncated," in output


def test_bridge_short_output_untouched() -> None:
    reg = SkillRegistry()
    reg.register(
        Skill(
            name="short_tool",
            description="Short output.",
            trusted_source="skill://public/short_tool",
            handler=lambda **_kwargs: "small result",
        ),
        verify_tests=False,
    )
    stack = SimpleNamespace()
    stack.executor = ToolExecutor(reg, TrustEngine())
    call = {"id": f"tool-{uuid4().hex[:8]}", "name": "short_tool", "input": {}}
    output, is_error = _tbe._execute_tool_call(stack, call)
    assert is_error is False
    assert (
        output == "(real tool execution succeeded) short_tool\nsmall result"
        or "small result" in output
    )

