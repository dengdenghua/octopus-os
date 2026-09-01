"""Code-mode completion, write, inspection and tool-availability guards.

Extracted from ``react_guards.py`` (Wave 3, cluster 2) so the orchestration
module can stay under the size budget. Each guard returns either ``None``
(let the Final Answer through) or a message explaining why the model must
keep working.

Leaf-ish module: depends only on re / react_goal_analysis / react_parsing /
react_types — must never import react_guards.
"""

from __future__ import annotations

import re

from runtime.core.cerebrum.react_goal_analysis import (
    _explicit_source_paths,
    _explicitly_requested_tool_names,
    _final_answer_requests_user_help,
    _goal_requests_code_mutation,
    _goal_requests_project_inspection,
    _goal_requires_file_content,
    _normalize_evidence_path,
    _path_evidence_matches,
    _successful_read_paths,
    _successful_write_paths,
)
from runtime.core.cerebrum.react_parsing import (
    _has_code_verification,
    _has_test_write,
    _has_verification_requiring_code_write,
    _is_code_write_step,
    _latest_todo_items,
    _parse_action,
)
from runtime.core.cerebrum.react_types import ReActStep


def _final_answer_claims_no_tool_access(final_answer: str) -> bool:
    lowered = (final_answer or "").lower()
    denial_markers = (
        "no tool",
        "no available",
        "not available",
        "cannot access",
        "can't access",
        "cannot read",
        "can't read",
        "unable to access",
        "unable to read",
        "cannot execute",
        "can't execute",
        "do not have access",
        "don't have access",
        "do not have available",
        "don't have available",
        "not have access",
        "没有可用",
        "无可用",
        "没有工具",
        "无法访问",
        "不能访问",
        "无法读取",
        "不能读取",
        "无法执行",
        "不能执行",
        "不能实际执行",
    )
    tool_markers = (
        "tool",
        "list_cwd",
        "read_file",
        "file",
        "workspace",
        "project",
        "工具",
        "文件",
        "项目",
    )
    return any(marker in lowered for marker in denial_markers) and any(
        marker in lowered for marker in tool_markers
    )


def _has_real_react_action(steps: list[ReActStep]) -> bool:
    for step in steps:
        action = (step.action or "").strip().lower()
        if action and action not in {"none", "n/a", "na"}:
            return True
    return False


def _has_successful_tool_observation(
    steps: list[ReActStep],
    *,
    tool_name: str | None = None,
) -> bool:
    for step in steps:
        action = (step.action or "").strip().lower()
        if not action or action in {"none", "n/a", "na"}:
            continue
        parsed = _parse_action(step.action)
        if tool_name is not None and (parsed is None or parsed[0] != tool_name):
            continue
        observation = (step.observation or "").strip()
        if not observation or observation == "N/A":
            continue
        lowered = observation.lower()
        if (
            "未执行观察" in observation
            or "not executed" in lowered
            or "tool-availability guard" in lowered
            or "工具失败" in observation
            or "工具执行异常" in observation
        ):
            continue
        return True
    return False


def _tool_has_execution_receipt(steps: list[ReActStep], tool_name: str) -> bool:
    """Whether the requested tool reached the execution layer.

    A rejected/failed receipt still proves that the model obeyed the request to
    call the tool; the final answer may then accurately report that outcome.
    """

    expected = tool_name.lower()
    for step in steps:
        actions = step.actions or ([step.action] if step.action else [])
        for index, raw_action in enumerate(actions):
            parsed = _parse_action(raw_action)
            if parsed is None or parsed[0].lower() != expected:
                continue
            if index < len(step.action_results):
                return True
            observation = (step.observation or "").strip()
            if observation and observation != "N/A" and "未执行观察" not in observation:
                return True
    return False


def _explicit_tool_request_guard(
    steps: list[ReActStep],
    final_answer: str,
    *,
    goal: str,
) -> str | None:
    """Require execution receipts for concrete tool calls in the user request."""

    del final_answer
    requested = _explicitly_requested_tool_names(goal)
    missing = sorted(name for name in requested if not _tool_has_execution_receipt(steps, name))
    if not missing:
        return None
    return (
        "The user's explicit tool-call requirement is not complete: no execution "
        f"receipt exists for {', '.join(missing)}. Call the requested tool now "
        "with the user's stated arguments, then finish from its actual result. "
        "Do not replace execution with a plan, checklist, or readiness message."
    )


def _has_successful_code_write(steps: list[ReActStep]) -> bool:
    """Return True only for a write tool with a successful execution receipt."""

    for step in steps:
        if not _is_code_write_step(step):
            continue
        if step.action_results:
            if any(result.get("ok") is True for result in step.action_results):
                return True
            continue
        # Older/replayed trajectories predate action receipts.  Preserve
        # compatibility, but still require a non-error observation.
        if _has_successful_tool_observation([step]):
            return True
    return False


def _code_mode_missing_write_guard(
    steps: list[ReActStep],
    final_answer: str,
    *,
    goal: str,
) -> str | None:
    """Reject an implementation final when no real workspace write succeeded."""

    if not _goal_requests_code_mutation(goal):
        return None
    if _final_answer_requests_user_help(final_answer):
        return None
    if _has_successful_code_write(steps):
        return None
    return (
        "Code mode cannot finish this implementation task yet: no successful "
        "file write/edit execution is recorded. Plans, reasoning, todo status, "
        "and remembered results are not workspace changes. Inspect the supplied "
        "workspace, call a real write/edit tool for the requested change, read "
        "the changed files back, and then run an appropriate verifier."
    )


def _final_answer_claims_tool_was_not_executed(final_answer: str) -> bool:
    lowered = (final_answer or "").lower()
    markers = (
        "not actually executed",
        "was not executed",
        "wasn't executed",
        "only recorded",
        "merely recorded",
        "no real tool",
        "no actual tool",
        "no verifiable",
        "not verifiable",
        "未实际执行",
        "没有实际执行",
        "没有真正执行",
        "只是被记录",
        "仅被记录",
        "没有真实执行",
        "没有可验证",
    )
    return any(marker in lowered for marker in markers)


def _code_mode_missing_inspection_tool_guard(
    steps: list[ReActStep],
    final_answer: str,
    *,
    goal: str,
    file_tools_visible: bool,
    grounded_source_paths: frozenset[str] | set[str] = frozenset(),
) -> str | None:
    """Reject project-inspection finals that did not use file evidence."""
    if not file_tools_visible:
        return None
    if not _goal_requests_project_inspection(goal):
        return None
    if _final_answer_requests_user_help(final_answer):
        return None
    requested_paths = _explicit_source_paths(goal)
    if requested_paths:
        observed_paths = {
            _normalize_evidence_path(path)
            for path in grounded_source_paths
            if _normalize_evidence_path(path)
        }
        observed_paths.update(_successful_read_paths(steps))
        observed_paths.update(_successful_write_paths(steps))
        missing_paths = [
            path
            for path in requested_paths
            if not any(_path_evidence_matches(path, observed) for observed in observed_paths)
        ]
        if missing_paths:
            return (
                "Code mode cannot finish this project-inspection task yet: "
                "the user explicitly named source files that are not covered "
                "by successful read_file evidence or exact source grounding: "
                + ", ".join(missing_paths)
                + ". Read every missing file before answering, then base the "
                "comparison only on those observations."
            )
        return None
    if not _has_successful_tool_observation(steps):
        return (
            "Code mode cannot finish this project-inspection task yet: no "
            "successful file tool observation is recorded. Call "
            'list_cwd({"path":"."}) first, then read_file on the smallest '
            "relevant file set."
        )
    if _goal_requires_file_content(goal) and not _has_successful_tool_observation(
        steps,
        tool_name="read_file",
    ):
        return (
            "Code mode cannot finish this project-inspection task yet: the "
            "request asks for file/config evidence, but no successful "
            "read_file observation is recorded. Read at least one relevant "
            "file before producing the report."
        )
    return None


_SOURCE_FRAGMENT_ONLY_RE = re.compile(
    r"^(?:"
    r"(?:export\s+)?(?:const|let|var|type)\s+[A-Za-z_$][\w$]*\s*(?::[^=]+)?=.+"
    r"|[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*\s*(?::[^=]+)?=.+"
    r"|(?:async\s+)?def\s+[A-Za-z_]\w*\s*\([^\n]*\)\s*(?:->[^:]+)?\s*:"
    r"|(?:export\s+)?(?:interface|class|type)\s+[A-Za-z_$][\w$]*(?:\s*[={].*)?"
    r"|return\s+.+"
    r")$",
    re.IGNORECASE,
)


def _code_mode_inspection_answer_fragment_guard(
    final_answer: str,
    *,
    goal: str,
    file_tools_visible: bool,
) -> str | None:
    """Reject a raw source line masquerading as an inspection report.

    Read-only code analysis often ends immediately after a large file result.
    Weak providers occasionally echo the last visible declaration (for
    example ``str = ""``) as plain prose.  The evidence gate proves the files
    were read, but not that the model actually answered the question.  Keep
    genuine concise conclusions valid; only source-shaped, explanation-free
    fragments are rejected.
    """

    if not file_tools_visible or not _goal_requests_project_inspection(goal):
        return None
    if _final_answer_requests_user_help(final_answer):
        return None
    visible = str(final_answer or "").strip()
    visible = re.sub(r"^```[A-Za-z0-9_+-]*\s*|\s*```$", "", visible).strip()
    visible = visible.strip("`").strip().rstrip(";")
    if not visible or "\n" in visible or len(visible) > 180:
        return None
    if not _SOURCE_FRAGMENT_ONLY_RE.fullmatch(visible):
        return None
    return (
        "Code mode cannot finish this project-inspection task with a bare source-code "
        "fragment. Explain what the observed declaration means and answer the user's "
        "actual comparison or architecture question using the completed read evidence."
    )


def _code_mode_false_no_tool_guard(
    steps: list[ReActStep],
    final_answer: str,
    *,
    goal: str,
    tools_active: bool,
) -> str | None:
    """Reject code-mode finals that hallucinate missing file tools."""
    if not tools_active:
        return None
    if not _goal_requests_project_inspection(goal):
        return None
    if _has_real_react_action(steps):
        return None
    if not _final_answer_claims_no_tool_access(final_answer):
        return None
    return (
        "Tools are available in this ReAct session. Do not claim that "
        "project/file tools are unavailable before trying a listed tool. "
        'For this code-mode inspection task, call list_cwd({"path":"."}) '
        "first, then read_file on the smallest relevant file set. If a "
        "specific tool call fails, report that concrete failure."
    )


def _code_mode_false_tool_result_guard(
    steps: list[ReActStep],
    final_answer: str,
    *,
    tools_active: bool,
) -> str | None:
    """Reject finals that deny a successful real tool observation."""
    if not tools_active:
        return None
    if not _has_successful_tool_observation(steps):
        return None
    if not (
        _final_answer_claims_tool_was_not_executed(final_answer)
        or _final_answer_claims_no_tool_access(final_answer)
    ):
        return None
    return (
        "A real tool execution already succeeded in this ReAct session. "
        "Use the Observation data as evidence; do not claim the action was "
        "only recorded, not actually executed, unavailable, or inaccessible. "
        "Continue the task with additional read-only tools or produce a report "
        "grounded in the successful observations."
    )


def _code_mode_completion_guard(
    steps: list[ReActStep],
    final_answer: str,
    *,
    todo_protocol_required: bool = True,
    execution_degraded: bool = False,
) -> str | None:
    """Reject premature code-mode Final Answer attempts."""
    if _final_answer_requests_user_help(final_answer):
        return None

    todos = _latest_todo_items(steps)
    if todo_protocol_required and not todos and len(steps) >= 3:
        return (
            "Code mode cannot finish yet: no todo_write checklist is recorded. "
            "Create a complete todo list, execute it, "
            "and only finish after all items are completed."
        )

    incomplete: list[str] = []
    for item in todos:
        status = str(item.get("status") or "").lower()
        if status != "completed":
            title = str(
                item.get("title")
                or item.get("content")
                or item.get("text")
                or item.get("task")
                or "untitled"
            )
            incomplete.append(title)
    if incomplete:
        preview = "; ".join(incomplete[:5])
        if len(incomplete) > 5:
            preview += f"; +{len(incomplete) - 5} more"
        return (
            "Code mode cannot finish yet: unfinished todos remain: "
            f"{preview}. Keep working, update todo_write, "
            "or explicitly ask the user for help if blocked."
        )

    completed_todo_text = "\n".join(
        str(item.get("title") or item.get("content") or item.get("text") or item.get("task") or "")
        for item in todos
        if str(item.get("status") or "").lower() == "completed"
    )
    claims_persistent_test_write = bool(
        re.search(
            r"(?:create|add|write|新增|创建|添加|编写|写)"
            r".{0,32}(?:tests?/|test_|tests?\b|测试文件|回归测试)",
            completed_todo_text,
            re.IGNORECASE,
        )
    )
    if claims_persistent_test_write and not _has_test_write(steps):
        return (
            "Code mode cannot finish yet: a completed todo claims that a "
            "persistent test/regression file was created, but no test-file "
            "write is recorded in the trajectory. Inline one-off checks do "
            "not satisfy that checklist item. Write the promised tests under "
            "the repository test directory, read them back, and run them."
        )

    # The "files changed but no verification run" veto demands EXECUTED
    # test/typecheck evidence. When the execution environment is degraded
    # (sandbox / network blocks, detected live in the trajectory), that
    # evidence physically cannot exist — skip the veto so the turn can
    # close with static evidence + an explicit limitation note. The
    # todo-protocol branches above are checklist-file contracts and hold
    # regardless of execution health.
    if (
        not execution_degraded
        and _has_verification_requiring_code_write(steps)
        and not _has_code_verification(steps)
    ):
        return (
            "Code mode cannot finish yet: files were changed "
            "but no verification step is recorded. "
            "Run an appropriate test, typecheck, lint, compile, "
            "or clearly ask the user for help if verification is impossible."
        )

    return None


__all__ = [
    "_code_mode_completion_guard",
    "_code_mode_false_no_tool_guard",
    "_code_mode_false_tool_result_guard",
    "_code_mode_inspection_answer_fragment_guard",
    "_code_mode_missing_inspection_tool_guard",
    "_code_mode_missing_write_guard",
    "_explicit_tool_request_guard",
    "_final_answer_claims_no_tool_access",
    "_final_answer_claims_tool_was_not_executed",
    "_has_real_react_action",
    "_has_successful_code_write",
    "_has_successful_tool_observation",
    "_tool_has_execution_receipt",
]
