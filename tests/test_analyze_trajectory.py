"""normalized_tool_name must read both runner dialects — the flat echo
shape and the nested codex ``item`` shape — so cross-system analysis never
silently reports an empty tool name (the bug this module exists to fix)."""

from __future__ import annotations

from benchmarks.analyze_trajectory import normalized_tool_name, summarize_trajectory


def _start(payload: dict) -> dict:
    return {"kind": "tool_start", "payload": payload}


def test_flat_echo_tool_name() -> None:
    assert normalized_tool_name(_start({"tool_name": "browser_navigate"})) == ("browser_navigate")


def test_codex_command_execution_wrapper() -> None:
    step = _start(
        {"tool_name": "command_execution", "item": {"type": "command_execution", "command": "ls"}}
    )
    assert normalized_tool_name(step) == "command_execution"


def test_echo_command_execution_wrapper_exposes_skill_name() -> None:
    step = {
        "kind": "tool_start",
        "payload": {
            "tool_name": "command_execution",
            "item": {"type": "commandExecution", "command": "browser_navigate"},
        },
    }

    assert normalized_tool_name(step) == "browser_navigate"


def test_codex_mcp_tool_call_uses_item_name() -> None:
    step = _start({"item": {"type": "mcp_tool_call", "tool_name": "browser.click"}})
    assert normalized_tool_name(step) == "browser.click"


def test_codex_mcp_without_name_falls_back_to_type() -> None:
    step = _start({"item": {"type": "mcp_tool_call"}})
    assert normalized_tool_name(step) == "mcp_tool_call"


def test_never_returns_empty_string() -> None:
    # The precise failure that motivated this module: a codex tool_start
    # whose name lives on the item must never read back as "".
    assert normalized_tool_name(_start({"item": {"type": "function_call"}})) != ""
    assert normalized_tool_name({"kind": "text_delta", "payload": {}}) is None


def test_summarize_counts_subagents_and_mcp() -> None:
    traj = {
        "steps": [
            _start({"item": {"type": "mcp_tool_call", "tool_name": "browser.click"}}),
            _start({"tool_name": "call_agent_parallel"}),
            {"kind": "text_delta", "payload": {"delta": "hi"}},
        ]
    }
    s = summarize_trajectory(traj)
    assert s["used_mcp"] is True
    assert s["subagent_starts"] == 1
    assert s["tools"]["browser.click"] == 1


