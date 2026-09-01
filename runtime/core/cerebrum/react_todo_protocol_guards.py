"""Todo-protocol and completion-phrase guards.

Extracted from ``react_guards.py`` (Wave 3, cluster 3) so the orchestration
module can stay under the size budget. These guards enforce the visible
``todo_write`` checklist and detect mid-flight completion phrases that
aren't followed by a checklist update.

Leaf-ish module: depends only on re / react_goal_analysis / react_parsing /
react_code_mode_guards / react_types — must never import react_guards.
"""

from __future__ import annotations

import re

from runtime.core.cerebrum.react_code_mode_guards import _has_successful_code_write
from runtime.core.cerebrum.react_goal_analysis import _final_answer_requests_user_help
from runtime.core.cerebrum.react_parsing import (
    _is_code_write_step,
    _latest_todo_items,
    _parse_action,
)
from runtime.core.cerebrum.react_types import ReActStep

# Read-only / evidence-gathering tools.  These never mutate the workspace, so
# a turn that only gathered evidence must not be trapped into a todo_write
# refresh loop after the task is already complete (the original bug).  Real
# work tools (echo, exec_shell, write skills, …) are still treated as residue.
_READ_ONLY_EVIDENCE_TOOLS: frozenset[str] = frozenset(
    {
        "file_stats",
        "glob_files",
        "grep_text",
        "list_cwd",
        "read_file",
        "read_file_range",
        "recall",
        "count_words",
    }
)

# Observations the dispatcher renders when a tool call did NOT succeed
# (see ``_react_execution_dispatch``).  Prefix-matched on the step's
# observation to detect executions that produced no scope-changing work.
_FAILURE_OBSERVATION_PREFIXES: tuple[str, ...] = (
    "(工具失败)",
    "(工具执行异常)",
    "(tool failed)",
)

_TERMINAL_DELIVERY_TODO_RE = re.compile(
    r"(?:"
    r"(?:交付|提交|呈现|输出|给出|发送|汇报).{0,12}(?:报告|结果|结论|答案|总结)|"
    r"(?:报告|结果|结论|答案|总结).{0,12}(?:交付|提交|呈现|输出|发送|汇报)|"
    r"deliver|present|return|send|provide|report\s+(?:back|results?)|"
    r"final\s+(?:answer|report|summary|response)"
    r")",
    re.IGNORECASE,
)


def _is_terminal_delivery_todo(title: str) -> bool:
    """Whether emitting the final answer itself fulfills this checklist row."""

    return bool(_TERMINAL_DELIVERY_TODO_RE.search(title or ""))


def _is_read_only_evidence_tool(name: str) -> bool:
    lowered = name.lower()
    return (
        lowered in _READ_ONLY_EVIDENCE_TOOLS
        or lowered.endswith(":read")
        or lowered.startswith("read_")
        or lowered.startswith("search")
        or lowered.startswith("list_")
        or "web_search" in lowered
    )


def _step_is_failed_execution(step: ReActStep) -> bool:
    """Whether a step's tool executions all failed (no success evidence).

    The parallel dispatch writes per-action receipts (``ok: bool``); the
    single-action path renders failures as ``(工具失败)`` / ``(工具执行异常)`` /
    ``(tool failed)`` prefixed observations (see ``_react_execution_dispatch``).
    Any successful receipt wins — a step where one call succeeded and another
    failed IS scope-changing work.
    """
    if step.action_results:
        return not any(result.get("ok") is True for result in step.action_results)
    obs = (step.observation or "").strip()
    return obs.startswith(_FAILURE_OBSERVATION_PREFIXES)


def _has_tool_work_after_latest_todo(steps: list[ReActStep]) -> bool:
    """Whether real (non-read-only) work happened after the latest checklist update.

    Read-only tools (read_file, web_search, list_cwd, …) are evidence-gathering
    and must not trap an already-complete task into a todo_write loop.  Any
    other tool that actually ran successfully and returned an observation
    (echo, exec_shell, write skills, …) is treated as outstanding work so the
    checklist stays accurate before the turn reports completion.

    FAILED executions are exempt: a step whose every tool call failed produced
    no scope-changing work that a checklist update would need to cover.  Without
    this carve-out, an environmental failure (e.g. a sandboxed exec_shell that
    cannot run) that the model retries a few times re-triggers this veto on
    every retry and spins an already-complete turn into a three-strike guard
    impasse even though nothing ever changed.  Fabrication ("I'm done" right
    after a failed verification) is caught by the verification guards, not
    this stale-checklist veto.
    """

    for step in reversed(steps):
        parsed = _parse_action(step.action)
        if parsed is None:
            continue
        name, _args = parsed
        if name == "todo_write":
            return False
        if _step_is_failed_execution(step):
            # Failed attempts change nothing about the task scope.  Skip
            # instead of treating them as outstanding work.
            continue
        if _is_code_write_step(step):
            return True
        # A non-todo_write, non-code-write tool that actually ran and produced
        # an observation counts as real work unless it is read-only evidence.
        if name.lower() not in {"none", "n/a", ""} and step.observation:
            return not _is_read_only_evidence_tool(name)
    return False


def _todo_protocol_completion_guard(
    steps: list[ReActStep],
    final_answer: str,
    *,
    goal: str = "",
) -> str | None:
    """Reject finals that skip or stale the visible checklist protocol.

    For short read-only analysis follow-ups ("不足点呢") that slipped past
    the trigger-layer exemption — e.g. because goal_mode or team mode
    forced ``todo_protocol_required=True`` before the read-only check —
    the checklist is optional: downgrade from hard reject to silent pass
    so pure inquiry follow-ups are not trapped into three-strike loops.

    This is deliberately a narrow safety net mirroring change ①'s
    ``_is_read_only_analysis_goal`` predicate.  Research, team
    coordination, implementation, and broad audit tasks all still require
    a checklist here; only short inquiry follow-ups with no write intent
    and no executed write tool are exempted.
    """

    if _final_answer_requests_user_help(final_answer):
        return None

    # Safety net mirroring change ①: a short read-only analysis follow-up
    # that the trigger layer could not exempt (goal_mode / team mode force
    # ``required=True`` upstream) should not be hard-blocked.  Writes are
    # the contract the checklist protects; without write intent and without
    # an executed write, the checklist is ceremony for an inquiry turn.
    if goal:
        # Lazy import: todo_protocol imports _has_successful_code_write from
        # this module at module scope, so a top-level import would cycle.
        from runtime.core.cerebrum.todo_protocol import _is_read_only_analysis_goal

        if _is_read_only_analysis_goal(goal) and not _has_successful_code_write(steps):
            return None

    todos = _latest_todo_items(steps)
    if not todos:
        return (
            "This task cannot finish yet: no todo_write checklist is recorded. "
            "Create a complete user-visible checklist before the final answer."
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
    # The final answer is the side effect for a terminal delivery row.  Making
    # the model call todo_write *after* it has already produced that answer is
    # impossible and caused completed research reports to die in a three-strike
    # guard impasse.  Other incomplete work remains a hard veto.
    substantive_final = len(final_answer.strip()) >= 80
    if (
        substantive_final
        and incomplete
        and all(_is_terminal_delivery_todo(title) for title in incomplete)
    ):
        incomplete = []
    if incomplete:
        preview = "; ".join(incomplete[:5])
        if len(incomplete) > 5:
            preview += f"; +{len(incomplete) - 5} more"
        return (
            "This task cannot finish yet: unfinished checklist items remain: "
            f"{preview}. Keep working, update todo_write, or ask the user for "
            "help if blocked."
        )

    if _has_tool_work_after_latest_todo(steps):
        return (
            "This task used tools after the latest todo_write update. Call "
            "todo_write again with the complete list marked accurately before "
            "the final answer."
        )

    return None


# ──────────────────────────────────────────────────────────────────
# In-flight guards — fire DURING the loop, not at Final Answer time.
# ──────────────────────────────────────────────────────────────────

# Phrases that suggest the model believes some unit of work just
# completed. Matched in the latest step's Thought / Observation
# heading. Triggers the "now update todo_write" reminder when the
# next action isn't already todo_write.
#
# Keep tight — false positives waste a turn nudging the model to call
# todo_write when the work isn't actually complete. Each entry should
# be unambiguously "I just finished a thing", not "I'm working on a
# thing".
_COMPLETION_PHRASE_RE = re.compile(
    r"(?:"
    # English: completion sentences
    r"\b(?:done|completed|finished|implemented|fixed|resolved)\b[^.\n]{0,40}"
    r"\b(?:successfully|now|the\s+(?:fix|change|edit|implementation))?|"
    r"\bthat'?s\s+(?:done|all|everything)\b|"
    r"\ball\s+(?:done|tests\s+pass|checks\s+pass)\b|"
    # Chinese: 完成 / 修好了 / 改好了 / 写好了 / 都搞定
    r"已[完成完成搞定修好改好写好]|"
    r"全部完成|都[完成搞定]|"
    r"[完修改写]好了|搞定了"
    r")",
    re.IGNORECASE,
)


def _looks_like_completion_phrase(text: str) -> bool:
    if not text:
        return False
    return bool(_COMPLETION_PHRASE_RE.search(text))


def _completion_phrase_without_todo_guard(
    steps: list[ReActStep],
    *,
    todo_protocol_required: bool,
) -> str | None:
    """Detect "I just finished X" claims that aren't immediately followed
    by a ``todo_write`` update.

    Fires DURING the loop (before the next action runs), not at Final
    Answer time. Goal: catch the model when it narrates a completion
    in its Thought but its actual next planned action is something
    other than updating the visible checklist. Quietly returns None
    when the next action IS ``todo_write`` — that's the desired
    behaviour and shouldn't generate noise.

    Kept as a compatibility hook for older callers.  It is intentionally
    telemetry-only: a checklist can describe progress, but it cannot veto
    a turn or force another model round.
    """
    # A checklist is a projection of execution state, never a gate on the
    # model's prose.  Completion words are especially unreliable across
    # providers (and across languages); treating them as a reason to inject
    # another todo_write round caused the exact empty-loop behaviour users
    # saw.  The authoritative completion decision is made from tool/item
    # receipts at turn finalization.  Keep this function for compatibility
    # with older callers, but make it telemetry-only.
    del steps, todo_protocol_required
    return None


__all__ = [
    "_completion_phrase_without_todo_guard",
    "_has_tool_work_after_latest_todo",
    "_looks_like_completion_phrase",
    "_is_terminal_delivery_todo",
    "_step_is_failed_execution",
    "_todo_protocol_completion_guard",
]
