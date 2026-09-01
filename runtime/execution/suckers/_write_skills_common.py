"""Shared helpers & constants for write_skills · extracted from write_skills.py.

Holds the cross-cutting helpers and module constants used by the write / exec /
git / quality handler submodules.  Kept here so the submodules can import them
without circular imports.
"""

from __future__ import annotations

import shlex
import sys
from pathlib import Path
from typing import Any

_DEFAULT_MAX_BYTES = 1 * 1024 * 1024  # 1 MB
# Long commands (build/test/install) routinely exceed 30s; a too-short
# default turned benign tool calls into timeouts that tripped the react
# loop's timeout guards and killed otherwise-fine turns. Model-facing
# tools still accept an explicit timeout_s override.
_DEFAULT_EXEC_TIMEOUT_S = 60.0
_EXEC_OUTPUT_CAP = 200_000
_BACKGROUND_OUTPUT_CAP = 200_000


def _execution_policy_from_result(result: dict[str, Any]) -> dict[str, Any]:
    policy = result.get("execution_policy")
    return dict(policy) if isinstance(policy, dict) else {}


def _error_with_execution_policy(
    error: str,
    result: dict[str, Any],
    **extra: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"error": error, **extra}
    policy = _execution_policy_from_result(result)
    if policy:
        payload["execution_policy"] = policy
    return payload


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ensure_sandbox(
    path: str | Path,
    sandbox_dir: str | Path | None,
) -> tuple[Path, str | None]:
    from runtime.safety.auth.path_guard import check_path

    verdict = check_path(path, sandbox_dir=sandbox_dir)
    if not verdict.allow:
        reason = verdict.reason
        if "escapes_sandbox" in reason:
            reason = (
                f"path_escapes_sandbox: {verdict.resolved} not under {sandbox_dir}. "
                f"该路径不在当前任务获准的工作区内，无法写入。"
                f"这是工作区授权边界，不代表执行沙箱已开启。"
                f"可操作建议：1) 确认路径是否正确；"
                f"2) 切换到 project workspace 模式以扩大工作区范围；"
                f"3) 使用 CLI code 模式（python -m runtime.cli code --cwd <项目根目录>）运行任务。"
            )
        return Path(path), reason
    return Path(verdict.resolved) if verdict.resolved else Path(path), None


def _parse_command(command: str | list[str]) -> tuple[list[str] | None, str | None]:
    if not command:
        return None, "missing command"
    if isinstance(command, str):
        try:
            argv = shlex.split(command, posix=(sys.platform != "win32"))
        except ValueError as e:
            return None, f"shlex_split_failed: {e}"
    elif isinstance(command, list):
        argv = [str(x) for x in command]
    else:
        return None, f"command must be str or list (got {type(command).__name__})"
    if not argv:
        return None, "empty argv after parsing"
    unsupported = [
        token
        for token in argv
        if token in {"&&", "||", ";", "|", "&", ">", ">>", "<", "<<"}
        or token.startswith(("1>", "2>", "1<", "2<"))
    ]
    if unsupported:
        return (
            None,
            "shell operators are not supported (shell=False): "
            f"{unsupported!r}. Pass the working directory via the `cwd` "
            "argument and invoke one command directly; stdout and stderr "
            "are already returned separately, so redirection is unnecessary.",
        )
    return argv, None
