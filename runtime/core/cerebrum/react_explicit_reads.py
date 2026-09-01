"""Explicit-read goal predicates and bounded read recovery.

Split out of ``react_loop.py`` (Wave 1 of the react-loop split · see
``docs/design/react-loop-split-plan.md``). Pure move — no behaviour
change. ``react_loop`` re-exports every name so existing imports and
monkeypatch targets keep working.

These helpers cover the "user explicitly named files / asked read-only /
asked tool-free" family:

* goal predicates — ``_explicit_read_only_goal`` /
  ``_explicit_observed_read_sequence`` / ``_explicit_no_tool_goal``
* bounded direct answers and read recovery —
  ``_narrow_command_direct_answer`` / ``_recover_explicit_read_actions``
  / ``_bound_explicit_large_reads``
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from runtime.core.cerebrum.react_guards import _explicit_source_paths
from runtime.core.cerebrum.react_parsing import _parse_action
from runtime.core.cerebrum.react_types import ReActStep
from runtime.core.cerebrum.todo_protocol import _is_narrow_read_only_command
from runtime.platform.models import Step

_EXPLICIT_READ_RECOVERY_PATH_RE = re.compile(
    r"(?<![\w./-])(?:\.{0,2}/)?(?:[A-Za-z0-9_@.-]+/)*[A-Za-z0-9_@.-]+\."
    r"(?:py|tsx|ts|jsx|json|js|ya?ml|toml|md|css|html|go|rs)\b",
    re.IGNORECASE,
)
_FUTURE_READ_INTENT_RE = re.compile(
    r"\b(?:read|open|inspect)\b|(?:读取|查看|打开)",
    re.IGNORECASE,
)
_STRUCTURED_READ_SUFFIXES = {
    ".csv",
    ".docx",
    ".gif",
    ".ipynb",
    ".jpeg",
    ".jpg",
    ".pdf",
    ".png",
    ".pptx",
    ".tsv",
    ".webp",
    ".xlsx",
}
_MAX_UNRANGED_TEXT_LINES = 2000


def _text_file_needs_range(path: Path) -> bool:
    """Return whether an ordinary text read should start with a slice.

    The byte threshold catches normal large source files cheaply.  The bounded
    line probe also catches generated/config files that exceed the reader's
    2,000-line contract while remaining smaller than 100 KiB.
    """

    if path.suffix.lower() in _STRUCTURED_READ_SUFFIXES:
        return False
    try:
        if path.stat().st_size > 100 * 1024:
            return True
        with path.open("r", encoding="utf-8") as handle:
            return any(
                line_number > _MAX_UNRANGED_TEXT_LINES
                for line_number, _line in enumerate(handle, start=1)
            )
    except (OSError, UnicodeDecodeError):
        return False


def _narrow_command_direct_answer(
    *,
    goal: str,
    step: ReActStep,
    beak_step: Step | None,
    resolved_name: str | None,
    succeeded: bool,
) -> str | None:
    """Return trustworthy stdout for a bounded one-command result turn.

    The model already chose the command and the executor already produced a
    structured receipt. When the user explicitly asked for only that output,
    a second model round adds latency and can pull unrelated conversation
    history into an otherwise exact answer. Keep the shortcut deliberately
    narrow; every broader task continues through normal model synthesis.
    """

    if not succeeded or resolved_name != "exec_shell" or not _is_narrow_read_only_command(goal):
        return None
    actions = step.actions or ([step.action] if step.action else [])
    if len(actions) != 1 or beak_step is None:
        return None
    parsed = _parse_action(actions[0])
    if parsed is None:
        return None
    _name, args = parsed
    if args.get("run_in_background") is True or args.get("background") is True:
        return None
    result = getattr(beak_step, "result", None)
    output = getattr(result, "output", None)
    if not isinstance(output, dict):
        return None
    if output.get("success") is False:
        return None
    exit_code = output.get("exit_code")
    if isinstance(exit_code, int) and exit_code != 0:
        return None
    stdout = output.get("stdout")
    if not isinstance(stdout, str):
        return None
    answer = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", stdout).strip()
    if not answer or "\x00" in answer or len(answer) > 2000:
        return None
    return answer


def _recover_explicit_read_actions(
    *,
    goal: str,
    model_text: str,
    workspace_path: str | None,
    steps: list[ReActStep],
    executor: Any,
    read_only: bool,
) -> list[str]:
    """Recover bounded reads when prose announces them but omits tool calls.

    This is intentionally limited to user-named, workspace-contained files in
    an explicit read-only turn. It repairs a common weak-provider failure mode
    without inferring writes, commands, searches, or any path the user did not
    provide. Oversized source files start with a bounded first slice so the
    ordinary reader does not reject the call before the model can refine it.
    """

    if (
        not read_only
        or not workspace_path
        or not _FUTURE_READ_INTENT_RE.search(model_text or "")
        or executor is None
    ):
        return []
    registry = getattr(executor, "registry", None)
    if registry is None or not registry.has("read_file"):
        return []
    requested = list(
        dict.fromkeys(
            match.group(0).replace("\\", "/").lstrip("./")
            for match in _EXPLICIT_READ_RECOVERY_PATH_RE.finditer(goal or "")
        )
    )
    if not requested or len(requested) > 6:
        return []
    from runtime.core.cerebrum.react_guards import _successful_read_paths

    already_read = _successful_read_paths(steps)
    try:
        root = Path(workspace_path).expanduser().resolve()
    except (OSError, RuntimeError):
        return []
    actions: list[str] = []
    for relative in requested:
        normalized = relative.strip("/").lower()
        if normalized in already_read:
            continue
        try:
            candidate = (root / relative).resolve()
            if not candidate.is_relative_to(root) or not candidate.is_file():
                continue
            size = candidate.stat().st_size
        except (OSError, RuntimeError):
            continue
        args: dict[str, Any] = {"path": relative}
        if size > 100 * 1024:
            args.update({"offset": 0, "limit": 400})
        actions.append(f"read_file({json.dumps(args, ensure_ascii=False)})")
    return actions


def _bound_explicit_large_reads(
    *,
    goal: str,
    workspace_path: str | None,
    actions: list[str],
    read_only: bool,
) -> list[str]:
    """Add a first slice to oversized workspace text reads before dispatch.

    This originally covered only explicitly named files in read-only turns.
    That left ordinary code tasks to execute a guaranteed-to-fail unbounded
    read, then spend another model round discovering the same pagination
    contract.  Apply the bound to every workspace-contained ``read_file``;
    explicit read scope is still enforced separately by its own guard.
    """

    del goal, read_only  # retained in the public signature for compatibility
    if not workspace_path or not actions:
        return actions
    try:
        root = Path(workspace_path).expanduser().resolve()
    except (OSError, RuntimeError):
        return actions
    bounded: list[str] = []
    for action in actions:
        parsed = _parse_action(action)
        if parsed is None:
            bounded.append(action)
            continue
        name, args = parsed
        path = args.get("path") if isinstance(args, dict) else None
        if name != "read_file" or not isinstance(path, str) or "offset" in args or "limit" in args:
            bounded.append(action)
            continue
        try:
            candidate = (root / path).resolve()
            oversized = candidate.is_relative_to(root) and candidate.is_file()
            oversized = oversized and _text_file_needs_range(candidate)
        except (OSError, RuntimeError):
            oversized = False
        if not oversized:
            bounded.append(action)
            continue
        ranged_args = dict(args)
        ranged_args.update({"offset": 0, "limit": 400})
        bounded.append(f"read_file({json.dumps(ranged_args, ensure_ascii=False)})")
    return bounded


def _explicit_read_only_goal(value: str | None) -> bool:
    """Whether the current user turn explicitly forbids workspace mutation."""
    text = str(value or "").lower()
    return bool(
        re.search(r"\bread[- ]only\b", text)
        or re.search(
            r"\b(?:do\s+not|don't|must\s+not|never)\s+"
            r"(?:modify|change|edit|write|create|update|add|remove|delete|patch)",
            text,
        )
        or re.search(r"\bwithout\s+(?:modifying|changing|editing|writing|creating)", text)
        or re.search(
            r"(?:只读|(?:不要|严禁|禁止|不得|不可|不允许|"
            r"不(?=修改|改动|更改|编辑|写入|创建|新增|添加|删除|提交))\s*"
            r"(?:修改|改动|更改|编辑|写入|创建|新增|添加|删除|提交))",
            text,
        )
    )


def _explicit_observed_read_sequence(value: str | None) -> bool:
    """Whether the user requires visible, ordered evidence-gathering beats."""

    text = str(value or "")
    if not _explicit_source_paths(text):
        return False
    return bool(
        re.search(r"(?:每批|逐批)[^。；;\n]{0,40}(?:证据|读取|阅读|核对|检查)", text)
        or re.search(r"(?:依次|逐个)\s*(?:并行)?\s*(?:读取|阅读|核对|检查)", text)
        or re.search(
            r"按[^。；;\n]{0,40}顺序[^。\n]{0,1000}先[^。\n]{0,1000}(?:再|然后|最后)",
            text,
        )
        or re.search(
            r"\b(?:after\s+each\s+batch|read\s+in\s+(?:this\s+)?order|"
            r"first\b[^\n]{0,1000}\bthen\b)",
            text,
            re.IGNORECASE,
        )
    )


def _explicit_no_tool_goal(value: str | None) -> bool:
    """Whether the user explicitly requires a direct, tool-free reply."""
    text = str(value or "").lower()
    return bool(
        re.search(
            r"\b(?:do\s+not|don't|must\s+not|never)\s+"
            r"(?:use|call|invoke|run)\s+(?:any\s+)?tools?\b",
            text,
        )
        or re.search(r"\b(?:answer|reply|respond)\s+without\s+(?:any\s+)?tools?\b", text)
        or re.search(
            r"(?:不要|别|禁止|不得|无需|不用|不需要)\s*"
            r"(?:使用|调用|执行|运行)?\s*(?:任何|任意)?\s*(?:工具|tool)",
            text,
        )
        or re.search(r"(?:直接|仅|只)\s*(?:回答|回复).{0,12}(?:不用|不要|无需)\s*(?:工具)?", text)
    )
