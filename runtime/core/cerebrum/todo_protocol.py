"""Shared rules for the user-visible task checklist protocol.

The checklist is not a reasoning transcript. It is the execution contract the
UI can show while an agent performs multi-step work.
"""

from __future__ import annotations

import re
from typing import Any

from runtime.core.cerebrum.react_types import ReActStep
from runtime.core.cerebrum.work_mode import SWARM_ALIASES

_FOLLOWUP_EXECUTION_RE = re.compile(
    r"(?:\u7136\u540e|\u63a5\u7740|\u968f\u540e|\u5e76(?:\u4e14)?|\u540c\u65f6)\s*"
    r"(?:\u4fee\u6539|\u5b9e\u73b0|\u4fee\u590d|\u66f4\u65b0|\u521b\u5efa|\u65b0\u589e|\u91cd\u6784|\u6267\u884c|\u8fd0\u884c)|"
    r"\b(?:then|and then|also|and)\s+"
    r"(?:implement|fix|modify|edit|update|create|refactor|run|execute)\b",
    re.IGNORECASE,
)
_CONCISE_RESULT_RE = re.compile(
    r"(?:\u4e00\u53e5|\u4e00\u53e5\u8bdd|\u4e00\u6bb5|\u7b80\u77ed|\u7ed3\u8bba|"
    r"\u53ea\u56de\u7b54|\u4ec5\u56de\u7b54)|"
    r"\b(?:one sentence|brief|concise|short conclusion|only (?:answer|report|return))\b",
    re.IGNORECASE,
)
_READ_ONLY_RE = re.compile(
    r"(?:只读|不要(?:修改|写入|创建|新增)|不(?:修改|写入|创建|新增)|严禁(?:修改|写入|创建|新增))|"
    r"\b(?:read[ -]?only|do not (?:modify|edit|write|create)|without (?:modifying|editing|writing|creating))\b",
    re.IGNORECASE,
)
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_NON_TASK_CHAT_RE = re.compile(
    r"^\s*(?:"
    r"ok|okay|yes|thanks?|thank you|hello|hi|hey|hello everyone|"
    r"嗯+|哦+|噢+|好(?:的|啊)?|行|可以|收到|明白|谢谢|大家好|你好|您好"
    r")\s*[。.!！?？,，~～]*\s*$",
    re.IGNORECASE,
)


def _effective_length(text: str) -> int:
    """Length weighted for CJK density: one CJK char carries roughly the
    information of 2-3 Latin chars, so Chinese prompts hit the long-form
    threshold at ~27 characters instead of 80."""
    return len(text) + 2 * len(_CJK_RE.findall(text))


def _is_narrow_read_only_command(text: str) -> bool:
    """Return whether a turn is one bounded read-only command."""

    return bool(
        len(text) <= 300
        and "\n" not in text
        and re.search(r"\bexec_shell\b", text, re.IGNORECASE)
        and _READ_ONLY_RE.search(text)
        and _CONCISE_RESULT_RE.search(text)
        and not _FOLLOWUP_EXECUTION_RE.search(text)
    )


_ANALYSIS_ONLY_RE = re.compile(
    r"(?:分析|解释|说明|总结|概述|评估|审查|检查|看看|不足|缺点|问题|风险|建议|"
    r"看法|评一下|讲一下|说一下|聊聊|讨论)"
    r"|\b(?:analy[sz]e|explain|summari[sz]e|review|assess|evaluat|"
    r"discuss|opinion|thoughts?|insights?)\b",
    re.IGNORECASE,
)
# Broad-scope targets signal a project-level audit, not a short follow-up.
# "inspect the project and summarize it" looks read-only but is a multi-step
# audit that still warrants a checklist.  Short follow-ups like "解释一下这段代码"
# reference a specific narrow object, not the whole project/codebase.
_BROAD_SCOPE_RE = re.compile(
    r"(?:项目|代码库|架构|整体|全面|系统)"
    r"|\b(?:project|codebase|architecture|workspace|repository|repo|"
    r"system|overall|comprehensive)\b",
    re.IGNORECASE,
)


def _is_read_only_analysis_goal(text: str) -> bool:
    """Return whether a turn is a short read-only analysis/inquiry follow-up.

    Code mode is also the default home for read-only follow-up questions
    ("不足点呢", "解释一下这段代码").  Forcing a checklist for these short
    follow-ups trains the model to manufacture fake todos, which the
    completion guard then has to reject.  Exempt them here so the root
    cause is fixed at the trigger layer instead of patched at the guard
    layer.

    Deliberately narrow: broad read-only audits ("只读审计...形成完整报告")
    and long analysis/report tasks still require a checklist because they
    are multi-step — only short follow-up inquiries with an explicit
    analysis/inquiry cue and no write intent are exempted.  Requiring the
    cue prevents short work directives like "继续优化深度研究" or
    "把登录页改成暗色主题" from being mistaken for read-only analysis.
    """
    # Lazy import to avoid circular dependency: react_goal_analysis imports
    # from todo_protocol at module level.
    from runtime.core.cerebrum.react_goal_analysis import _goal_requests_code_mutation

    # If the goal requests workspace mutation, it is not read-only analysis.
    if _goal_requests_code_mutation(text):
        return False
    # Short follow-up questions (effective length < 80) carrying an explicit
    # analysis/inquiry cue and no follow-up execution intent are read-only:
    # "不足点呢", "解释一下这段代码", "还有什么问题".  The cue requirement is
    # what separates an inquiry from a short work directive.
    return bool(
        _effective_length(text) < 80
        and _ANALYSIS_ONLY_RE.search(text)
        and not _FOLLOWUP_EXECUTION_RE.search(text)
        and not _BROAD_SCOPE_RE.search(text)
    )


def context_mode(user_context: dict[str, Any] | None) -> str:
    """Return the best-effort runtime mode from a thread context."""

    if not isinstance(user_context, dict):
        return ""
    metadata = user_context.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    for key in ("mode", "task_type", "research_mode"):
        value = user_context.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    if isinstance(user_context.get("workspace_path") or metadata.get("workspace_path"), str):
        return "code"
    return ""


def should_require_todo_protocol(
    goal: str,
    user_context: dict[str, Any] | None = None,
) -> bool:
    """Whether the turn has an explicit contract requiring a checklist.

    ``required`` is deliberately *not* inferred from task wording or length.
    Those heuristics are open-ended: a user can express the same continuation
    or analysis intent in infinitely many ways, and one false positive turns a
    progress aid into an execution gate. A narrow, closed set of greetings and
    acknowledgements is exempt because those messages are not tasks at all.

    Ordinary multi-step work still receives the optional checklist guidance
    and the model may call ``todo_write``.  The runtime only enforces it when a
    structured orchestration contract (goal/team/swarm) requires it. Natural
    language never changes this enforcement state.
    """

    text = (goal or "").strip()
    if not text:
        return False
    if _NON_TASK_CHAT_RE.fullmatch(text):
        return False
    mode = context_mode(user_context)
    metadata = user_context.get("metadata") if isinstance(user_context, dict) else None

    todo_policy = user_context.get("todo_policy") if isinstance(user_context, dict) else None
    if todo_policy is None and isinstance(metadata, dict):
        todo_policy = metadata.get("todo_policy")
    if isinstance(todo_policy, str):
        normalized_policy = todo_policy.strip().lower()
        if normalized_policy in {"disabled", "off", "optional"}:
            return False
        if normalized_policy in {"required", "on"}:
            return True
    elif todo_policy is True:
        return True
    elif todo_policy is False:
        return False

    goal_mode = None
    if isinstance(user_context, dict):
        goal_mode = user_context.get("goal_mode") or user_context.get("completion_policy")
    if goal_mode is None and isinstance(metadata, dict):
        goal_mode = metadata.get("goal_mode") or metadata.get("completion_policy")
    if goal_mode is True or (
        isinstance(goal_mode, str) and goal_mode.lower() in {"goal", "goal_mode", "true"}
    ):
        return True

    capability = None
    if isinstance(user_context, dict):
        capability = user_context.get("capability_mode")
    if capability is None and isinstance(metadata, dict):
        capability = metadata.get("capability_mode")
    if isinstance(capability, str) and capability.lower() in SWARM_ALIASES | {"team", "collab"}:
        return True

    return mode in SWARM_ALIASES | {"team"}


def render_todo_protocol_guidance(*, required: bool, mode: str = "") -> str:
    """Render a compact system guidance block for checklist behavior."""

    lead = "TASK CHECKLIST PROTOCOL REQUIRED" if required else "TASK CHECKLIST PROTOCOL AVAILABLE"
    scope = f" for {mode} mode" if mode else ""
    requirement = (
        "For this turn, call `todo_write` before giving the final answer. "
        "For execution-heavy work, create the checklist before substantial "
        "tool work when possible."
        if required
        else "Use `todo_write` when the task becomes multi-step."
    )
    return (
        f"{lead}{scope}:\n"
        f"- {requirement}\n"
        "- The checklist is user-visible progress, not hidden reasoning.\n"
        "- Pass the complete list every time; do not send diffs.\n"
        "- Items must use status `pending`, `in_progress`, or `completed`; "
        "keep at most one `in_progress` item.\n"
        "- Update the checklist when a phase starts, when a phase completes, "
        "and before the final answer after tool work.\n"
        "- Treat the checklist as mutable: when code, documentation, or tool evidence "
        "changes the scope, revise item wording, add/remove/reorder items, and keep stable "
        "IDs for unchanged work instead of preserving an obsolete initial plan.\n"
        "- After a successful workspace write or verification milestone, make the next "
        "action todo_write with the full evidence-backed plan before starting more work.\n"
        "- If blocked, update the checklist to show the blocked/incomplete "
        "item and ask the user for the specific missing input."
    )


def _todo_prewrite_guard(
    actions: list[str],
    steps: list[ReActStep],
    *,
    required: bool,
    visible: bool,
) -> str | None:
    """Compatibility hook; checklist absence never blocks tool execution."""

    return None


def _todo_completion_before_write_guard(
    actions: list[str],
    steps: list[ReActStep],
    *,
    required: bool,
) -> str | None:
    """Compatibility hook; checklist status never blocks a tool call."""

    return None


def _todo_reconciliation_guard(
    actions: list[str],
    steps: list[ReActStep],
    *,
    required: bool,
    visible: bool,
) -> str | None:
    """Compatibility hook; stale plans never pause phase transitions."""

    return None
