"""Browser-interaction and mixed-mode completion guards.

Extracted from ``react_guards.py`` (Wave 3, cluster 5) so the orchestration
module can stay under the size budget. These guards require executed UI
evidence (type / click / upload / submit / confirmation) for explicit
browser tasks, and lane evidence for mixed browser-plus-code workflows.

Leaf-ish module: depends only on re / react_guard_types / react_goal_analysis /
react_parsing / react_types — must never import react_guards.
"""

from __future__ import annotations

import re

from runtime.core.cerebrum.react_goal_analysis import _final_answer_requests_user_help
from runtime.core.cerebrum.react_guard_types import GuardContext
from runtime.core.cerebrum.react_parsing import (
    _has_code_verification,
    _has_code_write,
    _parse_action,
)
from runtime.core.cerebrum.react_types import ReActStep


def _browser_goal_required_evidence(goal: str) -> set[str]:
    """Translate an explicit browser task into observable completion facts."""

    lowered = str(goal or "").lower()
    required: set[str] = set()
    if any(
        marker in lowered for marker in ("native select", "select ", "dropdown", "下拉", "选择")
    ):
        required.add("select")
    if any(marker in lowered for marker in ("rich-text", "rich text", "contenteditable", "富文本")):
        required.add("rich_text")
    if any(marker in lowered for marker in ("upload", "上传")):
        required.add("upload")
    if any(marker in lowered for marker in ("submit", "提交")):
        required.add("submit")
    if any(marker in lowered for marker in ("iframe", "confirmation", "confirmed", "确认状态")):
        required.add("confirmation")
    if any(marker in lowered for marker in ("delete", "remove", "删除")):
        required.add("delete")
    if any(marker in lowered for marker in ("create", "edit", "update", "新增", "编辑", "更新")):
        required.update(("type", "click"))
    return required


def _browser_action_evidence(steps: list[ReActStep]) -> tuple[set[str], int]:
    """Collect successful UI actions and post-submit confirmation evidence."""

    evidence: set[str] = set()
    submit_attempts = 0
    submitted = False
    confirmation_markers = (
        "onboarding complete",
        "confirmation.html",
        'id="confirmed"',
        "'confirmed'",
        '"confirmed"',
    )
    for step in steps:
        actions = step.actions or ([step.action] if step.action else [])
        for index, raw_action in enumerate(actions):
            parsed = _parse_action(raw_action)
            if parsed is None:
                continue
            name, args = parsed
            name = name.lower()
            target = " ".join(f"{key} {value}" for key, value in args.items()).lower()
            action_ok = True
            if index < len(step.action_results):
                action_ok = bool(step.action_results[index].get("ok"))
            else:
                observation = (step.observation or "").lower()
                action_ok = not any(
                    marker in observation
                    for marker in ("(工具失败)", "(工具执行异常)", '"error":', "timed_out")
                )

            if name in {"browser_type", "live_browser_type"} and action_ok:
                evidence.add("type")
                if any(marker in target for marker in ("role", "select", "dropdown", "option")):
                    evidence.add("select")
                if any(marker in target for marker in ("bio", "rich", "contenteditable")):
                    evidence.add("rich_text")
            elif name == "browser_upload" and action_ok:
                evidence.add("upload")
            elif name in {"browser_click", "live_browser_click"}:
                if "submit" in target:
                    # Count attempts, not only successful receipts: a click may
                    # mutate the page before a transport error is reported and
                    # must never be automatically repeated for "exactly once".
                    submit_attempts += 1
                    submitted = True
                    if action_ok:
                        evidence.add("submit")
                if action_ok:
                    evidence.add("click")
                    if any(marker in target for marker in ("delete", "remove", "删除")):
                        evidence.add("delete")

        if submitted:
            observation = (step.observation or "").lower()
            if any(marker in observation for marker in confirmation_markers):
                evidence.add("confirmation")
    return evidence, submit_attempts


def _browser_interaction_completion_guard(ctx: GuardContext) -> str | None:
    if not ctx.browser_operation_mode or _final_answer_requests_user_help(ctx.final_answer):
        return None
    required = _browser_goal_required_evidence(ctx.goal)
    if not required:
        return None
    evidence, submit_attempts = _browser_action_evidence(ctx.steps)
    missing = sorted(required - evidence)
    if not missing:
        return None
    labels = {
        "select": "native select interaction",
        "rich_text": "rich-text entry",
        "type": "form entry",
        "upload": "browser_upload receipt",
        "click": "UI click",
        "submit": "successful submit click",
        "delete": "delete click",
        "confirmation": "post-submit iframe confirmation observation",
    }
    missing_text = ", ".join(labels[item] for item in missing)
    once_note = (
        " A submit click was already attempted; do not click Submit again. Observe the current "
        "page with browser_get(wait_ms=300) or browser_state instead."
        if submit_attempts
        else ""
    )
    return (
        "Cannot finish this explicit browser task yet. Missing executed UI evidence: "
        f"{missing_text}.{once_note} Continue with the persistent browser page; for delayed "
        "iframe results, read the child-frame evidence returned in the frames field."
    )


def _mixed_mode_completion_guard(ctx: GuardContext) -> str | None:
    """Require evidence from every lane in explicit browser-plus-code work."""

    if (
        not ctx.browser_operation_mode
        or not ctx.is_code_mode
        or _browser_goal_is_ui_only(ctx.goal)
        or _final_answer_requests_user_help(ctx.final_answer)
    ):
        return None
    lowered = str(ctx.goal or "").lower()
    browser_requested = any(
        marker in lowered
        for marker in ("browser", "browser ui", "web ui", "浏览器", "页面", "界面")
    )
    code_requested = any(
        marker in lowered
        for marker in (
            "source code",
            "codebase",
            "repository",
            "repo",
            "patch",
            "pytest",
            "run tests",
            "源代码",
            "代码仓库",
            "修改代码",
            "运行测试",
        )
    )
    if not (browser_requested and code_requested):
        return None

    missing: list[str] = []
    if not _has_successful_browser_action(ctx.steps):
        missing.append("executed browser reproduction or inspection")
    if not _has_code_write(ctx.steps):
        missing.append("workspace code edit")
    if not _has_code_verification(ctx.steps):
        missing.append("code verification command")
    if not missing:
        return None
    return (
        "Cannot finish this mixed browser-and-code task yet. Missing lane evidence: "
        f"{', '.join(missing)}. Complete each requested lane in the same turn; "
        "do not treat a code-only or browser-only result as completion."
    )


def _has_successful_browser_action(steps: list[ReActStep]) -> bool:
    for step in steps:
        actions = step.actions or ([step.action] if step.action else [])
        for index, raw_action in enumerate(actions):
            parsed = _parse_action(raw_action)
            if parsed is None:
                continue
            name = parsed[0].lower()
            if not (name.startswith("browser_") or name.startswith("live_browser_")):
                continue
            if name in {"browser_close", "live_browser_close"}:
                continue
            if index < len(step.action_results):
                if bool(step.action_results[index].get("ok")):
                    return True
                continue
            observation = (step.observation or "").lower()
            if not any(
                marker in observation
                for marker in ("(工具失败)", "(工具执行异常)", '"error":', "timed_out")
            ):
                return True
    return False


# ── B/C-class invoke wrappers (non-standard signatures) ───────────


def _browser_goal_is_ui_only(goal: str) -> bool:
    """Whether browser mode is operating only on the app under test.

    Browser mode also covers mixed workflows such as "reproduce in the
    browser, then patch the repository".  Those turns still owe normal
    workspace evidence; only explicit UI goals without development language
    receive the browser-specific exemption.
    """

    lowered = (goal or "").lower()
    ui_markers = (
        "browser ui",
        "browser interface",
        "through the ui",
        "using the ui",
        "use the browser",
        "using the browser",
        "in the browser",
        "浏览器界面",
        "浏览器 ui",
        "仅使用 ui",
        "通过 ui",
        "在浏览器中",
    )
    workspace_markers = (
        "source code",
        "codebase",
        "workspace file",
        "project file",
        "implementation",
        "patch the",
        "modify code",
        "edit code",
        "update code",
        "fix the bug",
        "run tests",
        "test suite",
        "typecheck",
        "frontend component",
        "backend module",
        "git diff",
        "commit the",
        "源代码",
        "代码库",
        "代码仓库",
        "项目文件",
        "工作区文件",
        "修改代码",
        "编辑代码",
        "修复 bug",
        "修复缺陷",
        "运行测试",
        "单元测试",
        "提交代码",
    )
    workspace_patterns = (
        # Word boundaries cover punctuation and start/end positions without
        # treating app copy such as "repository settings" as a code task.
        r"\b(?:patch|fix|refactor)\s+(?:the\s+)?(?:repo|repository|codebase)\b",
        r"\b(?:inspect|read|modify|edit|update|change)\b.{0,32}"
        r"\b(?:source code|codebase|workspace files?|project files?|"
        r"frontend|backend|component|module|implementation)\b",
        r"\b(?:repo|repository)\s+(?:code|files?|implementation)\b",
        r"\b(?:run|execute|rerun)\s+(?:the\s+)?"
        r"(?:tests?|test suite|pytest|ruff|eslint|vitest|typecheck|tsc)\b",
        r"\b(?:add|write|update)\s+(?:unit\s+|integration\s+)?tests?\b",
        r"\b(?:pytest|ruff|eslint|vitest|typecheck|git diff)\b",
    )
    has_ui_marker = any(marker in lowered for marker in ui_markers)
    has_workspace_marker = any(marker in lowered for marker in workspace_markers) or any(
        re.search(pattern, lowered) for pattern in workspace_patterns
    )
    return has_ui_marker and not has_workspace_marker


__all__ = [
    "_browser_action_evidence",
    "_browser_goal_is_ui_only",
    "_browser_goal_required_evidence",
    "_browser_interaction_completion_guard",
    "_has_successful_browser_action",
    "_mixed_mode_completion_guard",
]
