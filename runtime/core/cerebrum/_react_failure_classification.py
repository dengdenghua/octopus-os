"""Classification of failed tool executions for readable failure surfacing.

Turn finalization marks a turn ``failed``, but the terminal UI only receives
the raw, often cryptic tool error text (e.g. a pnpm abort inside a husky
commit-msg hook, a network refusal, a missing binary). That leaves a user
staring at a red turn with no idea *why* the block happened.

This leaf module classifies the latest failed tool execution into a small,
stable taxonomy:

* ``environment`` — the sandbox / host blocked the tool (no-TTY package
  manager, network, permissions, missing binary, timeout, sandbox policy).
* ``git_hook``    — a repository git hook rejected the operation.

Each classification carries ``{kind, code, readable}``; ``readable`` is a
short human sentence the gateway surfaces verbatim as ``turn.outcome_reason``
so the UI can explain the block instead of echoing raw stderr. Unknown
failures return ``None`` and fall back to the existing generic handling.

Leaf module: imports nothing from react_* (stdlib only), so it cannot
participate in an import cycle with the loop body.
"""

from __future__ import annotations

import re
from typing import Any

# (regex, code, readable) — first match wins, so specific signatures must be
# ordered before their broader siblings. ``readable`` is user-facing Chinese
# (matching the guard-reason messages in react_final_answer_guards).
_ENVIRONMENT_PATTERNS: list[tuple[str, str, str]] = [
    # pnpm / package-manager purge confirmation in a no-TTY shell. This is the
    # classic "why did my git commit die" case: a husky commit-msg hook runs
    # ``pnpm exec commitlint`` and pnpm wants to purge the node_modules dir
    # that a different pnpm major installed.
    (
        r"ERR_PNPM_ABORTED_REMOVE_MODULES_DIR_NO_TTY|Aborted removal of modules "
        r"directory due to no TTY",
        "pnpm_modules_purge_no_tty",
        "环境阻塞：pnpm 想在无 TTY 环境下交互确认删除 node_modules，因此中止了。"
        "可在 .npmrc 设置 confirmModulesPurge=false、执行时带 CI=true，"
        "或让钩子直接调用 node_modules/.bin/ 下的可执行文件。",
    ),
    # Network refused / unreachable / blocked by sandbox.
    (
        r"network (?:is )?unreachable|network (?:is|was) (?:unavailable|blocked)|"
        r"name or service not known|no route to host|temporary failure in "
        r"name resolution|econnrefused|econnreset|enetunreach|etimedout|"
        r"connection (?:refused|reset|timed out)|getaddrinfo|"
        r"allow_network[:=] *false|network access.*denied",
        "network_unavailable",
        "环境阻塞：网络不可用或被沙箱拦截，工具无法访问外部资源。"
        "可稍后重试，或在允许网络访问的环境中执行。",
    ),
    # Permission / OS-level denial.
    (
        r"permission denied|eacces|operation not permitted|eperm|"
        r"not (?:authorized|permitted) to",
        "permission_denied",
        "环境阻塞：缺少对该文件、目录或操作的权限。请检查沙箱范围或提升权限后重试。",
    ),
    # Missing command / binary / module.
    (
        r"command not found|no such file or directory[^\n]{0,40}(?:bin/|\.js|\.sh)|"
        r"git_not_found|not found: *git|pnpm is not installed|"
        r"commitlint is not installed|eslint is not installed",
        "tool_not_found",
        "环境阻塞：命令执行失败，依赖的二进制或依赖未安装（如 git / pnpm / commitlint）。"
        "请先安装依赖再重试。",
    ),
    # Sandbox policy rejection.
    (
        r"sandbox|execution_policy|policy.?rejected|blocked by sandbox",
        "sandbox_blocked",
        "环境阻塞：沙箱策略拒绝了该操作。请在允许的范围内重试。",
    ),
    # Timeout.
    (
        r"timed? ?out(?: after| in)?|timeout_?|exceeded.*(?:timeout|deadline)",
        "timeout",
        "环境阻塞：命令执行超时。",
    ),
]


def classify_tool_failure(tool_name: str, detail: str) -> dict[str, Any] | None:
    """Classify a failed tool execution into ``{kind, code, readable}``.

    ``tool_name`` is the beak tool name (e.g. ``git_commit``,
    ``exec_shell``); ``detail`` is the raw redacted failure text. Returns
    ``None`` when nothing matches — callers keep the generic path.
    """
    signal = (detail or "").strip()
    if not signal:
        return None
    normalized = " ".join(signal.split())

    for pattern, code, readable in _ENVIRONMENT_PATTERNS:
        if re.search(pattern, normalized, re.IGNORECASE):
            return {"kind": "environment", "code": code, "readable": readable}

    # A bare git-hook marker with no environmental cause above it: the hook
    # itself (commitlint rule, lint failure) rejected the operation.
    if re.search(
        r"husky|commit-msg script failed|pre-commit(?: hook)? failed", normalized, re.IGNORECASE
    ):
        return {
            "kind": "git_hook",
            "code": "git_hook_rejected",
            "readable": (
                "git 钩子拦截：仓库的 git hook 拒绝了本次提交。请根据钩子输出修正"
                "（如提交信息格式、lint 错误），或按项目规则处理。"
            ),
        }
    return None
