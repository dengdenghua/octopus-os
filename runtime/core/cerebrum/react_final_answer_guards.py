"""Final-answer guard plumbing for the ReAct loop.

Extracted from ``react_loop.py`` (Wave 1 of the split documented in
``docs/design/react-loop-split-plan.md``). Everything here decides whether a
candidate final answer may stream to the user, records rejected steps, and
produces the terminal wording when the loop deadlocks against a guard.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Callable, Generator
from typing import Any

from runtime.core.cerebrum.react_convergence import evidence_answer_conflicts_with_goal
from runtime.core.cerebrum.react_explicit_reads import _explicit_read_only_goal
from runtime.core.cerebrum.react_guards import (
    _goal_requests_code_mutation,
    _incomplete_final_answer_guard,
)
from runtime.core.cerebrum.react_loop_controls import (
    _disabled_guard_labels,
    _emit_assistant_chunk,
    _guard_hit_recorder,
)
from runtime.core.cerebrum.react_loop_state import _LoopControl, _LoopState
from runtime.core.cerebrum.react_parsing import (
    _detect_destructive_calls_in_payload,
    _detect_dynamic_exec_in_payload,
    _detect_secrets_in_payload,
    _detect_shell_injection_in_payload,
    _detect_unsafe_deser_in_payload,
    _final_answer_claims_verification,
    _has_code_verification,
    _looks_like_unfinished_work,
    _parse_action,
    _strip_react_protocol_blocks,
)

# Imported from the defining module directly: react_guards' re-export of
# ``_step_is_failed_execution`` exists only in an uncommitted refactor, so at
# the committed tip ``from react_guards import _step_is_failed_execution``
# raised ImportError and broke 24 test modules at collection.
from runtime.core.cerebrum.react_todo_protocol_guards import _step_is_failed_execution
from runtime.core.cerebrum.react_types import REACT_OBSERVATION_FOLLOWUP, ReActStep

_logger = logging.getLogger(__name__)


def _unfinished_implementation_recovery_needed(
    text: str,
    goal: str,
    *,
    is_code_mode: bool,
) -> bool:
    """Limit implementation recovery to turns that actually mutate code.

    Research evidence often describes bugs, expected behavior, or proposed
    fixes. Those phrases can look like unfinished implementation work even
    though the user's requested work is already complete.
    """

    if not _looks_like_unfinished_work(text):
        return False
    if _goal_requests_code_mutation(goal):
        return True
    goal_text = str(goal or "").lower()
    non_implementation_turn = _explicit_read_only_goal(goal) or bool(
        re.search(
            r"(?:网页调研|调研|官方来源|网页|来源)|"
            r"\b(?:web research|research|official source|source|https?://)\b",
            goal_text,
        )
    )
    return not non_implementation_turn


def _record_rejected_step(
    steps: list,
    messages: list,
    step: Any,
    observation: str,
) -> None:
    """Record a denied / user-rejected action instead of silently dropping it.

    The approval-deny and user-reject branches used to ``continue`` after only
    setting a local ``observation``, so the rejected action never entered
    ``steps`` or ``messages``. That (a) livelocked the loop — the next LLM call
    could not see the rejection and re-emitted the same action until
    ``max_iter`` — and (b) left security-relevant denials invisible to the step
    trace. Append the step and surface the rejection to the model (assistant
    action + observation) so it adapts on the next turn."""
    from runtime.platform.models.llm import Message

    step.observation = observation
    steps.append(step)
    messages.append(Message(role="assistant", content=step.action))
    messages.append(
        Message(
            role="user",
            content=f"Observation: {observation}\n\n{REACT_OBSERVATION_FOLLOWUP}",
        )
    )


_CODE_FENCE_RE = re.compile(r"```[^\n]*\n.*?```", re.DOTALL)


def _strip_code_fences(text: str) -> str:
    """Remove fenced code blocks (```...```) from a candidate answer.

    Code deliverables present ``eval``/``exec``/``__import__``/``compile``
    inside markdown fences. Those are display-only tokens, not runtime calls
    the agent is about to run, so the pre-emit guard must not buffer the
    whole stream on them — the terminal guard in ``_evaluate_final_answer_guards``
    still vets the full text for genuinely dangerous execution.
    """
    return _CODE_FENCE_RE.sub(" ", text or "")


def _looks_like_observation_echo(text: str) -> bool:
    """True when model prose is leaked tool/protocol text, not an answer."""
    stripped = (text or "").lstrip()
    if not stripped:
        return False
    head = stripped[:800].lower()
    return (
        head.startswith("observation:")
        or head.startswith("[1/")
        or head.startswith("<tool_invocation")
        or head.startswith("<tool_call")
        or head.startswith("<function")
        or "(real tool execution succeeded)" in head
        or "[system guard]" in head
        or bool(
            re.search(r"(?im)^(?:user|model|assistant|system):\s*", head)
            and re.search(r"(?im)^(?:thought|action|observation|update):\s*", head)
        )
        # ReAct protocol blocks leaked into the answer channel. The model
        # occasionally writes ``Thought: ...`` / ``Action: name({...})`` as
        # answer text instead of routing them through the tool-call channel —
        # the tools then never execute and the user sees raw protocol. A
        # standalone ``Action: name({...})`` call shape is unambiguous (it
        # is never legitimate prose); ``Thought:`` alone could be a quote,
        # so only flag it when an Action block is also present.
        or bool(_REACT_ACTION_CALL_RE.search(stripped))
        or bool(_REACT_THOUGHT_LINE_RE.search(stripped) and _REACT_ACTION_CALL_RE.search(stripped))
        # A few providers omit the Action label entirely and return only the
        # function-call expression.  Treat a standalone known-tool call as
        # protocol text so it is retried/parsed, never shown as a final reply.
        or bool(_STANDALONE_INLINE_TOOL_CALL_RE.fullmatch(stripped))
    )


# A ReAct Action block written as prose: ``Action:\n    name({...})`` or
# ``Action: name({...})``. Anchored on the tool-call shape (name + JSON
# parens) so a legitimate mention like "the Action field" is not flagged.
_REACT_ACTION_CALL_RE = re.compile(
    r"(?im)Action\s*:\s*\n?\s*\w+\s*\(\s*\{.*?\}\s*\)",
    re.DOTALL,
)
# A ``Thought:`` line at the start of a line. Used only as a corroboration
# signal alongside an Action block above, never on its own.
_REACT_THOUGHT_LINE_RE = re.compile(r"(?im)^\s*Thought\s*:\s*")


def _final_answer_needs_pre_emit_guard(
    text: str,
    *,
    is_code_mode: bool,
    browser_operation_mode: bool = False,
) -> bool:
    """Whether user-visible final text must be buffered until guards pass.

    Only genuinely dangerous executable content must force full buffering.
    ``is_code_mode`` no longer forces buffering by itself: in the realtime
    workbench ``is_code`` is effectively always true because a workspace is
    mounted (see ``work_mode.resolve_work_mode``), so treating it as a hard
    gate made every final report render wholesale instead of streaming.
    Markdown code fences (`` ``` ``) are likewise not a reason to buffer —
    a report that quotes code would otherwise never stream. The terminal
    guard in ``_evaluate_final_answer_guards`` still re-evaluates the full
    text, so mid-stream preview of non-executable prose is safe.
    """
    if browser_operation_mode:
        return True
    body = text or ""
    if not body:
        return False
    # A plain chat-style stream can itself be a preparatory placeholder
    # ("I will search...", "我先检查...").  Buffer that shape until the
    # iteration ends so the completeness guard can reject it without first
    # leaking the placeholder into the visible answer channel.  As soon as
    # the same response contains a concrete conclusion this predicate clears
    # and normal token streaming resumes.
    if _incomplete_final_answer_guard(body) is not None:
        return True
    # Verification-success prose is evidence-sensitive even in a tools=[]
    # terminal synthesis round. Buffer it until the hard false-verification
    # guard sees the complete candidate and trusted trajectory receipts.
    if is_code_mode and _final_answer_claims_verification(body):
        return True
    lower = body.lower()
    # In code mode the answer *is* the deliverable: the model is presenting
    # code that routinely contains subprocess/os.system/eval/exec or shell
    # command text. Buffering on those tokens would freeze the stream the
    # moment the first one appears, then dump the whole report at once —
    # exactly the "choppy" code streaming users see. Displaying code is safe
    # (the terminal guard in _evaluate_final_answer_guards still vets any
    # real tool execution), so in code mode only genuinely dangerous explicit
    # exec (eval/exec/__import__/compile of a payload) and secret leakage
    # force buffering. Keyword presence and shell-injection / unsafe-deser /
    # destructive-call patterns — which are normal in code deliverables —
    # only force buffering outside code mode.
    if not is_code_mode and (
        "subprocess" in lower
        or "os.system" in lower
        or "os.popen" in lower
        or "pickle." in lower
        or "marshal." in lower
        or "yaml.load" in lower
        or "eval(" in lower
        or "exec(" in lower
        or "__import__(" in lower
        or "rm -rf" in lower
    ):
        return True
    # Dynamic-exec token detection must ignore fenced code blocks: in code
    # mode the deliverable routinely contains eval/exec/__import__/compile
    # inside fences, which are display-only. Only a dynamic-exec call in the
    # surrounding prose (i.e. the model proposing to actually run it) should
    # buffer the stream. Secrets are still checked on the full text so a
    # leaked key inside a code block is caught before it streams.
    if _detect_secrets_in_payload(body):
        return True
    if _detect_dynamic_exec_in_payload(_strip_code_fences(body)):
        return True
    return bool(
        not is_code_mode
        and (
            _detect_shell_injection_in_payload(body)
            or _detect_unsafe_deser_in_payload(body)
            or _detect_destructive_calls_in_payload(body)
        )
    )


def _evaluate_final_answer_guards(
    *,
    steps: list[ReActStep],
    step: ReActStep,
    final_answer: str,
    is_code_mode: bool,
    todo_protocol_required: bool,
    todo_protocol_visible: bool,
    file_inspection_tools_visible: bool,
    tools_active: bool,
    goal: str,
    browser_operation_mode: bool = False,
    grounded_source_paths: frozenset[str] = frozenset(),
    categories: frozenset[str] | set[str] | None = None,
    model: str = "",
    prior_grounding_text: str = "",
) -> tuple[str, str] | None:
    """Run the final-answer guard registry for regular and salvage paths."""
    from runtime.core.cerebrum.react_guards import (
        GuardContext,
        evaluate_guards,
    )

    candidate_digest = hashlib.sha256(final_answer.encode("utf-8", errors="ignore")).hexdigest()[
        :16
    ]
    all_steps = steps + [step]
    return evaluate_guards(
        GuardContext(
            steps=all_steps,
            final_answer=final_answer,
            is_code_mode=is_code_mode,
            todo_protocol_required=todo_protocol_required,
            todo_protocol_visible=todo_protocol_visible,
            file_inspection_tools_visible=file_inspection_tools_visible,
            tools_active=tools_active,
            goal=goal,
            browser_operation_mode=browser_operation_mode,
            grounded_source_paths=grounded_source_paths,
            model=model,
            prior_grounding_text=prior_grounding_text,
            execution_degraded=_trajectory_execution_degraded(all_steps),
        ),
        recorder=_guard_hit_recorder(
            dedupe_key=f"{id(steps)}:{step.iteration}:{candidate_digest}",
            goal=goal,
            iteration=step.iteration,
            metadata={
                "candidate_digest": candidate_digest,
                "step_count": len(steps) + 1,
                "model": model,
            },
        ),
        disabled_labels=_disabled_guard_labels(),
        categories=categories,
    )


def _note_guard_impasse(
    state: dict,
    label: str,
    steps: list,
    *,
    rejection_limit: int = 3,
) -> bool:
    """Track repeated same-guard rejections; True when the loop is stuck.

    A guard pushing back is healthy — the model does more work and returns
    with evidence. It stops being healthy when the SAME guard rejects the
    final answer again and again while the trajectory gains no new
    action-bearing steps: the model either cannot produce the demanded
    evidence or (worse) its attempts to comply never execute — e.g. its
    tool calls arrive in a format the parser drops. Left unbounded, that
    burns the whole iteration budget and then terminates through the
    auto-pause path, whose "paused — continue from checkpoint" wording
    misreports what actually happened. Three no-progress rejections in a
    row is the bound: real evidence-gathering always grows the step list.

    FAILED executions do not count toward progress: an environmental
    failure (sandbox/network) that the model retries adds a step but no
    evidence, so counting it would silently reset the counter and let the
    same guard reject forever. A genuinely successful new action still
    resets the counter.
    """
    progress = sum(
        1
        for s in steps
        if (
            (getattr(s, "action", "") or "").strip().lower() not in {"", "none", "n/a", "na"}
            and not _step_is_failed_execution(s)
        )
        or bool(getattr(s, "action_results", None))
    )
    if state.get("label") == label and state.get("progress") == progress:
        state["count"] = state.get("count", 0) + 1
    else:
        state.update(label=label, progress=progress, count=1)
    return state["count"] >= rejection_limit


def guard_stall_kind(steps: list[ReActStep]) -> str:
    """Classify *why* a guard keeps rejecting: ``action_deficit`` vs ``evidence``.

    The two demand opposite responses and the loop previously conflated them:

    * ``evidence`` — the model is running tools but cannot obtain what the
      guard wants. Text feedback is useful here; it names the missing piece.
    * ``action_deficit`` — the model executed nothing at all. Text feedback is
      provably useless: in trn_c2fbddce247b4164 the same guard rejected three
      rephrasings of "I'll inspect X next" while zero tools ran. What breaks
      that loop is the decode-level ``require_tool_use`` forcing, so this case
      should be given fewer rephrasing attempts before terminating honestly.
    """
    return "evidence" if _trajectory_has_executed_action(steps) else "action_deficit"


def _guard_rejection_outcome(state: dict, label: str, steps: list) -> str:
    """Return ``retry`` or ``hard_stop`` for a repeated rejection.

    A rejected candidate can never become successful merely because retrying
    stalled.  Protocol-only contamination is handled separately by
    ``_try_clean_downgrade``; every remaining guard represents missing truth,
    evidence, or requested work and must fail closed.
    """
    from runtime.core.cerebrum.react_guards import guard_disposition

    disposition = guard_disposition(label)
    limit = 3 if disposition == "hard" else 2
    if not _note_guard_impasse(state, label, steps, rejection_limit=limit):
        return "retry"
    return "hard_stop"


def _trajectory_has_successful_tool_evidence(steps: list[ReActStep]) -> bool:
    """Whether a prior step produced usable evidence for final synthesis.

    The completeness guard often rejects a progress promise *after* the
    requested reads/tests already succeeded.  Treating that rejection like
    missing evidence tells the model to run another tool, which creates the
    familiar ``I will inspect ...`` -> guard -> inspect-again loop.  Structured
    receipts are authoritative when present; older trajectories fall back to
    a non-failed action with a real observation.
    """

    for prior in steps:
        results = getattr(prior, "action_results", None) or []
        if any(isinstance(result, dict) and result.get("ok") is True for result in results):
            return True
        action = (getattr(prior, "action", "") or "").strip().lower()
        observation = (getattr(prior, "observation", "") or "").strip()
        if (
            action
            and action not in {"none", "n/a", "na"}
            and observation
            and observation != "N/A"
            and not _step_is_failed_execution(prior)
        ):
            return True
    return False


def _guard_repair_feedback(label: str, message: str, steps: list[ReActStep]) -> str:
    """Build the next-round instruction without causing redundant tool work."""

    if label == "final-answer completeness guard" and _trajectory_has_successful_tool_evidence(
        steps
    ):
        return (
            "The candidate was a progress promise, not a final answer. Successful tool evidence "
            "already exists in the recorded Observations. Do not call another tool, repeat an "
            "inspection, or announce future work. Synthesize the complete Final Answer now from "
            "the existing evidence: lead with the concrete result, include the material findings "
            "and verification outcome, and mention only genuine remaining limitations."
        )
    # Every other rejection: the repair instruction is internal loop machinery,
    # not user content. The model must never quote/acknowledge it in the
    # user-facing answer — a leaked "收到 grounding 检查…" prefix is exactly
    # what surfaced as a broken-layout report (thread txhjBkLKtmrjdfdJp0FQhN).
    return (
        f"{message}\n\n"
        "This feedback is internal loop machinery, not content for the user. "
        "Do NOT quote, translate, summarize, or acknowledge it in your "
        "user-facing Final Answer. Output the corrected final answer directly "
        "and nothing else."
    )


# Markers that distinguish an ENVIRONMENTAL tool failure (sandbox/network
# denial the model cannot fix by retrying the same tool) from a logic error
# it could. Matched case-insensitively against the observation text.
_ENVIRONMENTAL_FAILURE_MARKERS: tuple[str, ...] = (
    "(工具执行异常)",
    "operation not permitted",
    "eperm",
    "sandbox_apply",
    "sandbox",
    "connectionerror",
    "connection error",
    "connect timeout",
    "network access",
    "network request",
)


def _trusted_execution_failure(
    action: str,
    observation: str,
    *,
    trusted_execution: bool,
) -> bool:
    """Classify only trusted execution receipts, never arbitrary tool text."""

    if trusted_execution is not True:
        return False
    parsed = _parse_action(action or "")
    if parsed is None:
        return False
    lowered = (observation or "").lower()
    if any(marker in lowered for marker in _ENVIRONMENTAL_FAILURE_MARKERS):
        return True

    # Missing modules/binaries only imply an environment gap when the
    # trusted action is itself a verifier. A file, webpage, or MCP result may
    # quote the same words but cannot flip runtime state through this path.
    verifier_step = ReActStep(iteration=0, action=action, actions=[action])
    if not _has_code_verification([verifier_step]):
        return False
    from runtime.execution.suckers.verify_skills import classify_environment_gap

    if classify_environment_gap(observation):
        return True
    return bool(
        ("error_type=file_not_found" in lowered or '"error_type": "file_not_found"' in lowered)
        and ("no such file or directory" in lowered or "[errno 2]" in lowered)
    )


def _step_environmental_failure_count(step) -> int:
    """Count failed actions without letting a successful sibling erase one."""

    actions = list(getattr(step, "actions", None) or [])
    if not actions:
        action = str(getattr(step, "action", "") or "")
        actions = [action] if action else []
    results = list(getattr(step, "action_results", None) or [])
    if results:
        count = 0
        for index, result in enumerate(results):
            if result.get("ok") is not False or index >= len(actions):
                continue
            if _trusted_execution_failure(
                actions[index],
                str(result.get("observation") or ""),
                trusted_execution=result.get("trusted_execution") is True,
            ):
                count += 1
        return count
    # Legacy/replayed steps without server-owned receipt provenance cannot
    # establish execution trust. Fail closed instead of inferring it from a
    # model-controlled tool name or arbitrary observation text.
    return 0


def _step_is_environmental_failure(step) -> bool:
    """Whether a failed step's cause is environmental rather than a logic
    error the model could fix by retrying. Successful receipts win."""
    return _step_environmental_failure_count(step) > 0


def _environmental_failure_count(steps: list) -> int:
    return sum(_step_environmental_failure_count(step) for step in steps or [])


# How many environmental failures mark the environment itself as degraded.
# One EPERM can be transient (or the model probing whether a tool runs);
# two or more mean execution is genuinely blocked, so run-based evidence
# guards must stop vetoing the turn.
_EXECUTION_DEGRADED_THRESHOLD = 2


def _trajectory_execution_degraded(steps: list) -> bool:
    """Whether the execution environment is degraded.

    Two independent signals, OR'd:

    * startup canary — ``env_health`` probed a sandboxed command at serve
      boot and it could not run (the whole process session is degraded);
    * live trajectory — ≥2 steps that failed environmentally (sandbox /
      network / OS-permission denials the model cannot fix by retrying).

    Either means the run-based guards — which demand executed test or
    typecheck evidence — can never be satisfied, so evaluate_guards
    downgrades them to advisory instead of three-striking the turn.
    """
    from runtime.core.cerebrum.env_health import execution_canary_degraded

    if execution_canary_degraded():
        return True
    count = _environmental_failure_count(steps)
    return count >= _EXECUTION_DEGRADED_THRESHOLD


def _guard_soft_landing_answer(
    candidate: str,
    label: str,
    *,
    steps: list | None = None,
) -> str:
    """Return the useful candidate without exposing internal guard policy.

    Guard labels, retry counters and evidence-gate diagnostics are runtime
    implementation details.  They belong in structured telemetry, never in
    the assistant's conversational answer.  A repair-tier guard is bounded:
    after its retry budget is exhausted we deliver the cleaned candidate and
    let the structured completion receipt carry any degraded-environment
    details.

    ``label`` and ``steps`` intentionally remain in the signature so existing
    callers and telemetry hooks do not need a second compatibility path.
    """
    del label, steps
    return _clean_protocol_leak(candidate or "")


def _clean_protocol_leak(text: str) -> str:
    """Strip ReAct protocol blocks AND inline tool-call JSON from answer
    prose so a leaked-protocol candidate becomes a clean deliverable. Used
    by the soft-landing path and by ``_try_clean_downgrade``."""
    return _strip_inline_tool_calls(_strip_react_protocol_blocks(text or ""))


def _try_clean_downgrade(final_answer: str) -> str | None:
    """For a guard rejection that is purely a leaked-protocol answer, return
    the cleaned body when it still carries real content (so the caller can
    deliver it once instead of looping on retries). Returns None otherwise —
    callers keep their normal retry / hard-stop / soft-land logic.

    This is the Solution-A escape hatch: a model that writes
    ``Thought:``/``Action: name({...})`` into the answer channel has usually
    already done the work (the Action was parsed and executed upstream); only
    the answer markup was dirty. Rather than reject-and-retry until the loop
    deadlocks or the turn is interrupted, deliver the scrubbed answer once.
    """
    if not _looks_like_observation_echo(final_answer):
        return None
    cleaned = _clean_protocol_leak(final_answer)
    # Do not use a prose-length threshold here: concise but complete answers
    # such as "已完成。" or "OK" are valid deliveries. Require actual word/CJK
    # content after cleaning; punctuation-only or protocol-only candidates
    # stay on the normal retry path.
    substantive = re.sub(r"[`*_>#\-\s]", "", cleaned)
    return cleaned if re.search(r"[\w\u3400-\u9fff]", substantive) else None


# Tool names the model may write into answer prose as an inline JSON call
# (``todo_write({"items": [...]})``) instead of emitting a structured tool
# call. When that leaks into the final answer, strip the whole call block so
# the user never sees raw tool protocol in the transcript.
_INLINE_TOOL_CALL_NAMES = (
    "todo_write",
    "todo_update",
    "exec_shell",
    "shell_command",
    "run_command",
    "web_search",
    "fetch_url",
    "web_fetch",
    "read_file",
    "read_text_file",
    "glob_files",
    "find_files",
    "grep_text",
    "list_cwd",
    "search_capabilities",
    "query_skill",
    "browser_open",
    "browser_get_content",
    "artifact",
    "present_files",
    "apply_patch",
    "write_file",
    "write_text_file",
    "edit_file",
    "edit_text_file",
    "str_replace",
    "run_tests",
    "lint_check",
)
_INLINE_TOOL_CALL_RE = re.compile(
    rf"(?<![\w.])(?:{'|'.join(_INLINE_TOOL_CALL_NAMES)})\s*\(",
    re.IGNORECASE,
)
_STANDALONE_INLINE_TOOL_CALL_RE = re.compile(
    rf"^\s*(?:{'|'.join(_INLINE_TOOL_CALL_NAMES)})\s*\(\s*\{{.*\}}\s*\)\s*$",
    re.IGNORECASE | re.DOTALL,
)


def _strip_inline_tool_calls(text: str) -> str:
    """Remove ``tool_name({...})`` blocks that the model wrote into answer
    prose instead of emitting as structured tool calls. A balanced-brace scan
    consumes the whole JSON object (including nested braces) plus the closing
    paren, so surrounding narration survives intact."""
    if not text:
        return text
    parts: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        m = _INLINE_TOOL_CALL_RE.search(text, i)
        if not m:
            parts.append(text[i:])
            break
        parts.append(text[i : m.start()])
        open_idx = text.index("(", m.end() - 1)
        depth = 0
        j = open_idx
        while j < n:
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
                if depth == 0:
                    j += 1
                    while j < n and text[j] in " \t":
                        j += 1
                    if j < n and text[j] == ")":
                        j += 1
                    if j < n and text[j] == "\n":
                        j += 1
                    i = j
                    break
            j += 1
        else:
            # Unbalanced — keep the matched prefix and advance past it.
            parts.append(text[m.start() : m.end()])
            i = m.end()
    return "".join(parts).strip()


def _guard_impasse_final_answer(
    label: str,
    message: str,
    steps: list[ReActStep] | None = None,
) -> str:
    """The honest terminal answer for a guard impasse — shared by every
    in-loop guard-rejection site so the wording (and the truth it tells)
    can't drift between them.

    ``steps`` lets the hint distinguish an *action deficit* (the model never
    executed anything) from *evidence starvation* (it ran tools but could not
    obtain what the guard demanded).  Optional so existing call sites keep
    working; passing it produces a materially more accurate diagnosis.
    """
    user_reason = _guard_reason_for_user(label, message)
    actionable = _guard_impasse_actionable_hint(label, message, steps)
    # ``label`` is useful for logs/metrics, but exposing names such as
    # ``todo-protocol guard`` makes an internal policy failure look like an
    # assistant answer.  Keep the user-facing result factual and actionable.
    del label
    return (
        "这轮任务没有完成。我已停止重复尝试，并保留了当前进度。\n\n"
        f"原因：\n{user_reason}\n\n"
        f"{actionable}"
    )


def _guard_impasse_actionable_hint(
    label: str,
    message: str,
    steps: list[ReActStep] | None = None,
) -> str:
    """Scene-specific actionable advice appended to the impasse message.

    Instead of a single generic "check and retry", give the user a
    concrete next step based on which guard fired and what the
    diagnostic says.
    """
    msg_lower = (message or "").lower()
    if "path_blocked" in msg_lower or "escapes_sandbox" in msg_lower:
        return (
            "如何解决：该路径不在当前任务获准的工作区内；这不代表执行沙箱已开启。\n"
            "1) 确认路径是否正确；\n"
            "2) 切换到 project workspace 模式以扩大工作区范围；\n"
            "3) 使用 CLI code 模式（python -m runtime.cli code --cwd <项目根目录>）运行任务。"
        )
    if "tool" in msg_lower and ("not registered" in msg_lower or "未注册" in msg_lower):
        return (
            "如何解决：所需工具未注册或被配置关闭。\n"
            "1) 检查 config.local.yaml 中对应的 enable_* 开关；\n"
            "2) 确认工具名称拼写正确；\n"
            "3) 重启后端使配置生效。"
        )
    if "inspection" in label.lower() or "evidence" in label.lower():
        return (
            "如何解决：需要先收集执行证据再给出结论。\n"
            "1) 点击继续，让我先读取相关文件或运行验证命令；\n"
            "2) 提供必要的权限/登录/信息后我再继续。"
        )
    # The fallback used to assert that the tool-call format "was not
    # recognised by the execution layer".  When the trajectory contains no
    # executed action at all, that claim is simply false — nothing was
    # rejected because nothing was ever emitted (observed in
    # trn_c2fbddce247b4164: zero commandExecution items and zero
    # tool-call-protocol-error injections, yet the user was told their tool
    # format failed).  Misdiagnosing an action deficit as a parse failure
    # sends the user to fix a wire format that was never broken.
    if steps is not None and not _trajectory_has_executed_action(steps):
        return (
            "本轮我只输出了说明文字，没有真正执行任何工具调用，"
            "因此没有可用于收尾的证据。这不是工具格式或权限问题。\n"
            "如何解决：\n"
            "1) 点击继续，我会直接从具体操作开始，而不是先复述计划；\n"
            "2) 如果目标包含修改代码，请明确说出要改的文件或行为，"
            "我会先落地改动再验证。"
        )
    return (
        "这通常意味着模型输出的工具调用格式未被执行层识别,"
        "或任务所需的能力/权限当前不可用。\n"
        "如何解决：\n"
        "1) 点击继续重试，或补充必要的信息/权限；\n"
        "2) 检查上面的原因后重新描述任务；\n"
        "3) 如仍失败，尝试切换到 CLI code 模式运行。"
    )


def _trajectory_has_executed_action(steps: list[ReActStep]) -> bool:
    """Whether any step actually reached the execution layer.

    Distinct from ``_trajectory_has_successful_tool_evidence``: a *failed*
    execution still counts here, because the wire format demonstrably worked.
    Only a trajectory with no executed action at all is an action deficit.
    """
    for step in steps:
        if getattr(step, "action_results", None):
            return True
        action = (getattr(step, "action", "") or "").strip().lower()
        if action and action not in {"none", "n/a", "na"}:
            return True
    return False


def _guard_reason_for_user(label: str, message: str) -> str:
    """Avoid reflecting rejected security payloads into the transcript.

    Security guard diagnostics intentionally name the exact dangerous token
    or credential shape for the model's next repair attempt.  Reusing that
    diagnostic verbatim in a terminal user message can re-expose the content
    that the guard just prevented from streaming.
    """
    if label in {
        "secret-leak guard",
        "destructive-call guard",
        "dynamic-exec guard",
        "shell-injection guard",
        "unsafe-deser guard",
    }:
        return "安全检查拒绝了候选答复；具体片段已隐藏，避免再次暴露。"
    return message


def _phase_6e_guards_and_step_emit(
    state: _LoopState,
    *,
    i: int,
    append_pending_live_steering: Callable[[], int],
    build_research_progress_summary: Callable[[list[ReActStep]], str],
) -> Generator[dict, None, _LoopControl]:
    """PHASE 6e guard state machine + step completion emit.

    Moved verbatim from ``react_loop.py`` (PHASE 6e, second half): the
    evidence-answer conflict repair, the live-steering finalization
    deferral, final-answer guard evaluation with the three-strike
    impasse breaker, the deferred ``text_delta`` emit, and the
    ``react_step_complete`` event. Returns ``BREAK`` when a guard hits
    the impasse limit (``state`` then carries ``final_answer`` /
    ``terminated_reason``); ``CONTINUE`` otherwise.
    ``_append_pending_live_steering`` is a react_loop closure and
    ``_build_research_progress_summary`` lives in react_execution (which
    imports this module), so both are injected.
    """
    # Injected callables under their original names.
    _append_pending_live_steering = append_pending_live_steering
    _build_research_progress_summary = build_research_progress_summary
    # Reference-typed aliases — mutations propagate to the main loop.
    intent = state.intent
    steps = state.steps
    step = state.step
    assert step is not None, "phase 6e requires a parsed ReAct step"
    react_task_id = state.react_task_id
    _working_set = state.working_set
    _guard_impasse_state = state.guard_impasse_state
    _final_guard_grounded_source_paths = state.final_guard_grounded_source_paths
    # Scalar mailbox — pulled in, pushed back in the finally below.
    maybe_final = state.maybe_final
    final_answer = state.final_answer
    terminated_reason = state.terminated_reason
    _evidence_convergence_active = state.evidence_convergence_active
    _force_convergence_next = state.force_convergence_next
    _final_stream_started = state.final_stream_started
    _final_delta_emitted_this_iteration = state.final_delta_emitted_this_iteration
    _todo_protocol_required = state.todo_protocol_required
    _todo_protocol_visible = state.todo_protocol_visible
    _is_code_mode = state.is_code_mode
    _browser_operation_mode = state.browser_operation_mode
    _file_inspection_tools_visible = state.file_inspection_tools_visible
    tools_active = state.tools_active
    _green_verification_convergence_active = state.green_verification_convergence_active
    _green_convergence_todo_used = state.green_convergence_todo_used
    _clean_verification_rounds_after_write = state.clean_verification_rounds_after_write
    _streamed_final_chars = state.streamed_final_chars
    _current_phase = state.current_phase
    _progress_summary = state.progress_summary
    _public_progress_summary = state.public_progress_summary
    try:
        if (
            maybe_final
            and _evidence_convergence_active is not None
            and evidence_answer_conflicts_with_goal(
                goal=intent.normalized_goal,
                answer=maybe_final,
            )
        ):
            # Bounded evidence exists, so an idle/greeting answer claiming
            # there was no task is objectively contradictory. Keep it out of
            # the answer stream and retry with the original request attached.
            step.observation = (
                (((step.observation or "") + "\n\n") if step.observation else "")
                + "[evidence-answer-conflict]\n"
                + "The proposed answer falsely denied the active user request or the "
                + "completed evidence. Discard it and answer the original request "
                + "directly from the bounded evidence already supplied."
            )
            maybe_final = None
            _force_convergence_next = True

        # Close the race where a follow-up arrives while the model is composing
        # what would otherwise be the terminal answer. Keep that answer as
        # conversation history, then give the latest user message the next
        # model round instead of finalizing over it.
        if maybe_final and _append_pending_live_steering():
            maybe_final = None
            _logger.info(
                "react_loop deferred finalization for a priority user follow-up",
            )

        if maybe_final:
            _deferred_final_emit = not _final_stream_started and (
                _evidence_convergence_active is not None
                or _final_answer_needs_pre_emit_guard(
                    maybe_final,
                    is_code_mode=_is_code_mode,
                    browser_operation_mode=_browser_operation_mode,
                )
            )
            _guard_hit = _evaluate_final_answer_guards(
                steps=steps,
                step=step,
                final_answer=maybe_final,
                is_code_mode=_is_code_mode,
                todo_protocol_required=_todo_protocol_required,
                todo_protocol_visible=_todo_protocol_visible,
                file_inspection_tools_visible=_file_inspection_tools_visible,
                tools_active=tools_active,
                goal=intent.normalized_goal,
                browser_operation_mode=_browser_operation_mode,
                grounded_source_paths=_final_guard_grounded_source_paths,
                prior_grounding_text=state.prior_grounding_text,
            )
            if _guard_hit is not None:
                # Solution-A: a guard rejection that is purely a leaked ReAct
                # protocol block is downgraded to a one-shot cleaned delivery
                # rather than retried in a loop. The model usually already did
                # the work (tools ran); only the answer markup was dirty.
                _downgrade = _try_clean_downgrade(maybe_final)
                if _downgrade is not None:
                    final_answer = _downgrade
                    terminated_reason = "final_answer_with_warning"
                    steps.append(step)
                    return _LoopControl.BREAK
                _guard_label, _guard_message = _guard_hit
                _guard_outcome = _guard_rejection_outcome(_guard_impasse_state, _guard_label, steps)
                if _guard_outcome == "hard_stop":
                    # Same guard, repeated rejections, zero new action-bearing
                    # steps in between: pushing back again only burns the
                    # remaining budget and ends in the auto-pause path's
                    # misleading "paused" report. Terminate with the truth.
                    _logger.warning(
                        "react_loop guard impasse · %s rejected the final answer "
                        "3x with no intervening tool execution — terminating "
                        "explicitly instead of burning the iteration budget",
                        _guard_label,
                    )
                    final_answer = _guard_impasse_final_answer(_guard_label, _guard_message, steps)
                    terminated_reason = "guard_impasse"
                    steps.append(step)
                    return _LoopControl.BREAK
                maybe_final = None
                # A completion guard may discover a semantic defect even
                # after two superficially green verifier rounds. Re-open the
                # tool path so the model can perform the demanded repair;
                # otherwise the convergence gate would suppress every fix
                # and turn a useful guard into an impasse. The todo protocol
                # is different: terminal evidence is still valid and the
                # convergence state already allows exactly one checklist
                # update. Clearing it here caused green agents to resume an
                # unbounded test/lint cycle after that update.
                if _guard_label != "todo-protocol guard" and not (
                    _guard_label == "final-answer completeness guard"
                    and _trajectory_has_successful_tool_evidence(steps)
                ):
                    _green_verification_convergence_active = False
                    _green_convergence_todo_used = False
                    _clean_verification_rounds_after_write = 0
                    _force_convergence_next = False
                step.observation = (
                    (((step.observation or "") + "\n\n") if step.observation else "")
                    + f"[{_guard_label}]\n"
                    + _guard_repair_feedback(_guard_label, _guard_message, steps)
                )
            elif _deferred_final_emit:
                _delta = (
                    maybe_final[_streamed_final_chars:] if _streamed_final_chars else maybe_final
                )
                _emit_assistant_chunk(
                    state.stack,
                    iteration=i + 1,
                    delta=_delta,
                    task_id=react_task_id,
                )
                yield {
                    "type": "text_delta",
                    "delta": _delta,
                    "iteration": i + 1,
                }
                _final_delta_emitted_this_iteration = True

        _public_progress_summary = (
            _progress_summary if _is_code_mode else _build_research_progress_summary(steps + [step])
        )

        yield {
            "type": "react_step_complete",
            "iteration": step.iteration,
            "thought": step.thought,
            "public_update": step.public_update,
            "action": step.action,
            "observation": step.observation,
            "task_id": str(react_task_id),
            "current_phase": _current_phase if _is_code_mode else None,
            "working_set": list(_working_set.values()) if _is_code_mode else None,
            "progress_summary": _public_progress_summary,
        }
        return _LoopControl.CONTINUE
    finally:
        state.maybe_final = maybe_final
        state.force_convergence_next = _force_convergence_next
        state.green_verification_convergence_active = _green_verification_convergence_active
        state.green_convergence_todo_used = _green_convergence_todo_used
        state.clean_verification_rounds_after_write = _clean_verification_rounds_after_write
        state.final_answer = final_answer
        state.terminated_reason = terminated_reason
        state.final_delta_emitted_this_iteration = _final_delta_emitted_this_iteration
        state.public_progress_summary = _public_progress_summary
