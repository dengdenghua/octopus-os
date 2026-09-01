"""Tests for the ephemeral sub-agent verification gate."""

from __future__ import annotations

from runtime.execution.suckers._ephemeral_verification import (
    is_code_write_tool,
    is_verification_tool,
    verification_gate_nudge,
    written_code_path,
)


def test_code_write_tool_detected():
    assert is_code_write_tool("edit_text_file")
    assert is_code_write_tool("write_text_file")
    assert is_code_write_tool("apply_patch")
    assert not is_code_write_tool("web_search")


def test_written_code_path_only_for_code_files():
    assert written_code_path({"path": "src/app.py"}) == "src/app.py"
    assert written_code_path({"path": "src/App.tsx"}) == "src/App.tsx"
    # non-code files don't trigger the gate
    assert written_code_path({"path": "notes.md"}) is None
    assert written_code_path({"path": "data.json"}) is None
    assert written_code_path({"path": ""}) is None
    assert written_code_path({"other": 1}) is None


def test_verification_tool_detection():
    assert is_verification_tool("run_tests", {})
    assert is_verification_tool("typecheck", {})
    # shell commands inspected for verification keywords
    assert is_verification_tool("bash", {"command": "python -m pytest tests"})
    assert is_verification_tool("exec_shell", {"command": "pnpm typecheck"})
    assert is_verification_tool("shell_command", {"cmd": "npm test"})
    # non-verification shell commands
    assert not is_verification_tool("bash", {"command": "cat src/app.py"})
    assert not is_verification_tool("bash", {"command": "git status"})


def test_gate_fires_when_write_without_followup_verification():
    tools = [
        {"name": "edit_text_file", "input": {"path": "src/app.py"}, "ok": True},
    ]
    nudge = verification_gate_nudge(tools, max_rounds=10, current_round=1)
    assert nudge is not None
    assert "验证" in nudge


def test_gate_silent_when_verification_ran_after_write():
    tools = [
        {"name": "edit_text_file", "input": {"path": "src/app.py"}, "ok": True},
        {"name": "bash", "input": {"command": "python -m pytest tests"}, "ok": True},
    ]
    assert verification_gate_nudge(tools, max_rounds=10, current_round=1) is None


def test_gate_silent_when_write_failed():
    tools = [
        {"name": "edit_text_file", "input": {"path": "src/app.py"}, "ok": False},
    ]
    assert verification_gate_nudge(tools, max_rounds=10, current_round=1) is None


def test_gate_silent_when_only_non_code_files_written():
    tools = [
        {"name": "write_text_file", "input": {"path": "report.md"}, "ok": True},
    ]
    assert verification_gate_nudge(tools, max_rounds=10, current_round=1) is None


def test_gate_silent_on_last_round():
    tools = [
        {"name": "edit_text_file", "input": {"path": "src/app.py"}, "ok": True},
    ]
    assert verification_gate_nudge(tools, max_rounds=5, current_round=4) is None


def test_gate_verification_before_write_still_fires():
    # verify ran first, then a write — no verification after the write
    tools = [
        {"name": "bash", "input": {"command": "pnpm typecheck"}, "ok": True},
        {"name": "edit_text_file", "input": {"path": "src/app.ts"}, "ok": True},
    ]
    assert verification_gate_nudge(tools, max_rounds=10, current_round=1) is not None

