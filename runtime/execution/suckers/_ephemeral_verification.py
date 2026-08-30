"""Verification gate for ephemeral sub-agents.

The main conversation refuses to end a code-writing turn without verification
(the "代码改动需要先完成验证" guard). Ephemeral sub-agents (implementer /
debugger / coder roles) should get the same discipline: if they wrote or
edited code and did NOT run a verification tool afterwards, we inject a nudge
asking them to run tests / lint / typecheck / build before giving a final
answer — instead of letting them conclude with unverified code.
"""

from __future__ import annotations

import re
from typing import Any

# Tools that mutate code. Matches the bridge's ``_subagent_write_tools`` plus
# the common ephemeral registry names for code edits.
_CODE_WRITE_TOOLS: frozenset[str] = frozenset(
    {
        "write_text_file",
        "append_text_file",
        "edit_text_file",
        "edit_file",
        "multi_edit_file",
        "propose_patch",
        "create_file",
        "write_file",
        "edit_code",
        "str_replace",
        "apply_patch",
        "patch",
    }
)

# Dedicated verification-ish tool names (non-shell).
_VERIFY_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "run_tests",
        "run_checks",
        "lint_check",
        "typecheck",
        "verify",
        "pytest",
        "test",
        "build",
    }
)

# Shell tools whose command text is inspected for verification keywords.
_SHELL_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "bash",
        "exec_shell",
        "shell_command",
        "run_command",
        "run_shell",
    }
)

# Command fragments that indicate a real verification step (not just "run
# something"). Kept deliberately broad but specific enough to avoid treating
# e.g. ``bash "cat x.py"`` as verification.
_VERIFY_CMD_RE = re.compile(
    r"(?:"
    r"pytest|typecheck|tsc(?:\s|$)|eslint|lint|vitest|jest|"
    r"npm(?:\s+run)?\s+(test|build)|pnpm\s+(test|typecheck|build|check)|"
    r"go\s+test|cargo\s+(test|build|check)|mvn\s+(test|verify)|"
    r"gradle\s+(test|build|check)|make\s+(test|check)|"
    r"tox|nox|uv\s+run\s+pytest|python\s+-m\s+pytest|"
    r"run_tests|run_checks|lint_check|verify"
    r")",
    re.IGNORECASE,
)

_CODE_EXT_RE = re.compile(
    r"\.(?:py|pyw|js|mjs|cjs|jsx|ts|mts|cts|tsx|go|rs|java|c|cc|cpp|h|hpp|cs|rb|php|sh|zsh|bash|vue|svelte|sql|kt|kts|swift|scala|dart|ex|exs|erl|lua|r|pl)$",
    re.IGNORECASE,
)


def is_code_write_tool(name: str) -> bool:
    return name in _CODE_WRITE_TOOLS


def written_code_path(tool_input: Any) -> str | None:
    """Return the code-file path written by a write tool, else ``None``.

    A write only triggers the gate when it actually touches a code file, so a
    sub-agent producing a Markdown/JSON report is not forced to run tests.
    """
    if not isinstance(tool_input, dict):
        return None
    path = tool_input.get("path") or tool_input.get("file_path")
    if not isinstance(path, str) or not path.strip():
        return None
    if _CODE_EXT_RE.search(path):
        return path
    return None


def is_verification_tool(name: str, tool_input: Any) -> bool:
    """True when a tool call counts as a verification step."""
    if name in _VERIFY_TOOL_NAMES:
        return True
    if name in _SHELL_TOOL_NAMES:
        command = ""
        if isinstance(tool_input, dict):
            command = str(
                tool_input.get("command") or tool_input.get("cmd") or tool_input.get("script") or ""
            )
        return bool(_VERIFY_CMD_RE.search(command))
    return False


def verification_gate_nudge(
    executed_tools: list[dict[str, Any]],
    *,
    max_rounds: int | None,
    current_round: int,
) -> str | None:
    """Return a verification nudge message, or ``None`` when no gate applies.

    Fires when the sub-agent wrote a code file and the last executed tool was
    NOT a verification step (a verification ran before the write, but nothing
    after it). Skips when the round budget is exhausted so we never deadlock
    the run — the agent concludes with whatever it has.
    """
    if max_rounds is not None and current_round + 1 >= max_rounds:
        return None

    last_write_idx: int | None = None
    last_verify_idx: int | None = None
    for idx, call in enumerate(executed_tools):
        name = str(call.get("name") or "")
        # Only a SUCCESSFUL code write counts: a failed write changed nothing,
        # so there is nothing to verify (and forcing one would be noise).
        if call.get("ok") and is_code_write_tool(name) and written_code_path(call.get("input")):
            last_write_idx = idx
        if is_verification_tool(name, call.get("input")):
            last_verify_idx = idx

    if last_write_idx is None:
        return None
    if last_verify_idx is not None and last_verify_idx > last_write_idx:
        return None

    return (
        "你修改了代码文件，但最近一次工具调用之后没有运行验证。"
        "请运行测试 / lint / typecheck / 构建（例如 python -m pytest、"
        "pnpm typecheck）确认改动可正常工作，然后再给出最终答案。"
        "不要在没有验证的情况下直接结束。"
    )


__all__ = [
    "is_code_write_tool",
    "is_verification_tool",
    "verification_gate_nudge",
    "written_code_path",
]
