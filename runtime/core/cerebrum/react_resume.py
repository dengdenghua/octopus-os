"""Resume/checkpoint-rebuild helpers for the ReAct loop.

Extracted from ``react_loop.py`` (Wave 1 of the split documented in
``docs/design/react-loop-split-plan.md``). Loads a resume checkpoint from the
journal or trace store, validates it, and rebuilds the loop state — messages,
steps, working set, phase — as a pure, unit-testable function. Distinct from
``react_checkpointing`` (which writes/mirrors checkpoints) and ``resume_cli``
(which renders the operator-facing resume surface).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from runtime.core.cerebrum.react_checkpointing import _rehydrate_messages_from_steps
from runtime.core.cerebrum.react_context import (
    _compress_context,
    _restore_messages_from_checkpoint,
    context_budget_tokens_for_model,
)
from runtime.core.cerebrum.react_types import ReActStep
from runtime.platform.config.builder import StackProtocol
from runtime.platform.models import ParsedIntent, TaskId
from runtime.safety.validation.prompt_injection import (
    mark_injection_taint,
    reset_injection_taint,
    set_injection_gate_handled,
)

_logger = logging.getLogger(__name__)


def _build_resume_context_prompt(resume_intent: Any) -> str:
    if not isinstance(resume_intent, dict):
        return ""
    if resume_intent.get("confirmed") is not True:
        return ""
    lines = [
        "<resume-context>",
        "This is a sanitized checkpoint recovery summary, not a new user instruction.",
        f"- checkpoint_id: {_resume_context_text(resume_intent.get('checkpoint_id'), 80)}",
        f"- task_id: {_resume_context_text(resume_intent.get('task_id'), 120)}",
        f"- checkpoint_type: {_resume_context_text(resume_intent.get('checkpoint_type'), 80)}",
        f"- iteration: {_resume_context_text(resume_intent.get('iteration'), 32)}",
        f"- continue_from_iteration: {_resume_context_text(resume_intent.get('continue_from_iteration'), 32)}",
    ]
    phase = _resume_context_text(resume_intent.get("phase"), 120)
    if phase:
        lines.append(f"- phase: {phase}")
    working_set = [
        _resume_context_text(path, 180)
        for path in resume_intent.get("working_set", [])
        if isinstance(path, str) and path.strip()
    ][:8]
    if working_set:
        lines.append("- working_set:")
        lines.extend(f"  - {path}" for path in working_set)
    recent = _resume_context_recent_tools(resume_intent.get("recent_tool_calls"))
    if recent:
        lines.append("- recent_tool_calls:")
        lines.extend(recent)
    lines.append("</resume-context>")
    return "\n".join(lines)


def _resume_context_recent_tools(value: Any) -> list[str]:
    items = value if isinstance(value, list) else []
    lines: list[str] = []
    for item in items[:6]:
        if not isinstance(item, dict):
            continue
        tool = _resume_context_text(item.get("tool"), 80)
        if not tool:
            continue
        iteration = _resume_context_text(item.get("iteration"), 32)
        input_preview = _resume_context_text(item.get("input_preview"), 180)
        observation_preview = _resume_context_text(item.get("observation_preview"), 220)
        line = f"  - iter {iteration or '?'} tool={tool}"
        if input_preview:
            line += f" input={input_preview}"
        if observation_preview:
            line += f" observation={observation_preview}"
        lines.append(line)
    return lines


def _resume_context_text(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _carry_prior_spend(stack: Any, resume_task_id: Any) -> tuple[int, float]:
    """Sum historical token/cost spend for a task from the journal.

    Run AFTER ``_resume_or_register_turn`` registers the task but guards the
    carry so a missing journal degrades to (0, 0.0) instead of failing resume.
    """
    if resume_task_id is None:
        return 0, 0.0
    journal = getattr(stack, "journal", None)
    if journal is None or not hasattr(journal, "read_by_task"):
        return 0, 0.0
    try:
        events = journal.read_by_task(str(resume_task_id))
    except (AttributeError, TypeError, ValueError):  # noqa: BLE001
        return 0, 0.0
    total_tokens = 0
    total_cost = 0.0
    for event in events:
        if getattr(event, "event_type", "") != "token_usage":
            continue
        total_tokens += max(0, int(getattr(event, "input_tokens", 0) or 0))
        total_tokens += max(0, int(getattr(event, "output_tokens", 0) or 0))
        total_cost += max(0.0, float(getattr(event, "cost_usd", 0.0) or 0.0))
    return total_tokens, total_cost


def _resume_model_name(stack: Any, intent: Any) -> str:
    """Best-effort model identity for pre-call resume compaction."""

    user_context = getattr(intent, "user_context", None) or {}
    for key in ("execution_model", "selected_model", "model"):
        value = user_context.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    planner = getattr(getattr(stack, "config", None), "planner", None)
    return str(getattr(planner, "model", "") or "")


def _load_resume_checkpoint_snapshot(
    stack: StackProtocol,
    intent: ParsedIntent,
    resume_task_id: TaskId,
) -> dict[str, Any] | None:
    journal = getattr(stack, "journal", None)
    if journal is not None:
        ckpts = [
            e
            for e in journal.read_by_type("react_checkpoint")
            if str(getattr(e, "task_id", "")) == str(resume_task_id)
        ]
        if ckpts:
            return _checkpoint_snapshot_from_journal_event(ckpts[-1])
    return _load_trace_resume_checkpoint_snapshot(intent, resume_task_id)


def _checkpoint_snapshot_from_journal_event(event: Any) -> dict[str, Any]:
    return {
        "source": "journal",
        "iteration_completed": int(getattr(event, "iteration_completed", 0) or 0),
        "max_iterations": int(getattr(event, "max_iterations", 0) or 0),
        "messages_snapshot": getattr(event, "messages_snapshot", []) or [],
        "steps_snapshot": getattr(event, "steps_snapshot", []) or [],
        "has_final_answer": bool(getattr(event, "has_final_answer", False)),
        "final_answer": str(getattr(event, "final_answer", "") or ""),
        "working_set_snapshot": getattr(event, "working_set_snapshot", []) or [],
        "progress_summary": str(getattr(event, "progress_summary", "") or ""),
        "current_phase": str(getattr(event, "current_phase", "") or ""),
    }


def _load_trace_resume_checkpoint_snapshot(
    intent: ParsedIntent,
    resume_task_id: TaskId,
) -> dict[str, Any] | None:
    resume_intent = (intent.user_context or {}).get("resume_intent")
    if not isinstance(resume_intent, dict):
        return None
    checkpoint_id = resume_intent.get("checkpoint_id")
    if not isinstance(checkpoint_id, int) or checkpoint_id <= 0:
        return None
    try:
        from runtime.platform.process.session import current_session

        session = current_session()
    except (ImportError, AttributeError):
        session = None
    metadata = getattr(session, "metadata", None) if session is not None else None
    trace_store = metadata.get("_trace_store") if isinstance(metadata, dict) else None
    if trace_store is None or not hasattr(trace_store, "checkpoint_by_id"):
        return None
    checkpoint = trace_store.checkpoint_by_id(checkpoint_id)
    if not isinstance(checkpoint, dict):
        return None
    if str(checkpoint.get("task_id") or "") != str(resume_task_id):
        return None
    if str(checkpoint.get("checkpoint_type") or "").lower() != "react":
        return None
    _state_raw = checkpoint.get("state")
    state: dict[str, Any] = _state_raw if isinstance(_state_raw, dict) else {}
    return {
        "source": "trace_store",
        "iteration_completed": int(
            state.get("iteration_completed")
            or checkpoint.get("iteration")
            or resume_intent.get("iteration")
            or 0
        ),
        "max_iterations": int(state.get("max_iterations") or 0),
        "messages_snapshot": state.get("messages_snapshot")
        if isinstance(state.get("messages_snapshot"), list)
        else [],
        "steps_snapshot": state.get("steps_snapshot")
        if isinstance(state.get("steps_snapshot"), list)
        else [],
        "has_final_answer": bool(state.get("has_final_answer") is True),
        "final_answer": str(state.get("final_answer") or ""),
        "working_set_snapshot": state.get("working_set_snapshot")
        if isinstance(state.get("working_set_snapshot"), list)
        else [],
        "progress_summary": str(state.get("progress_summary") or checkpoint.get("summary") or ""),
        "current_phase": str(state.get("current_phase") or ""),
    }


@dataclass
class _ResumeState:
    """Loop state rebuilt from a resume checkpoint. Aggregating the ~9 values
    PHASE 5 used to assign inline lets the rebuild live in a pure, unit-testable
    function (``_compute_resume_state``) instead of being welded into the loop's
    closure."""

    resume_from_iter: int
    messages: list[Any]
    steps: list[ReActStep]
    working_set: dict[str, dict[str, Any]]
    progress_summary: str
    current_phase: str
    final_answer: str | None
    terminated_reason: str
    resume_event: dict[str, Any]


def _compute_resume_state(
    stack: StackProtocol,
    intent: ParsedIntent,
    resume_task_id: TaskId,
    *,
    base_messages: list[Any],
    base_working_set: dict[str, dict[str, Any]],
    base_progress_summary: str,
    base_current_phase: str,
    max_iterations: int,
) -> _ResumeState | None:
    """Load + validate a resume checkpoint and rebuild loop state from it.

    Pure except for logging: no ``yield``, no mutation of caller state. Returns
    ``None`` when there is nothing to resume (the caller keeps its defaults).
    Raises ``ValueError`` on an unsafe checkpoint — the caller catches it (along
    with the AttributeError/KeyError/TypeError a malformed snapshot can raise)
    and falls back to a fresh run.
    """
    last = _load_resume_checkpoint_snapshot(stack, intent, resume_task_id)
    if last is None:
        return None

    from runtime.core.cerebrum.checkpoint_integrity import validate_checkpoint_state

    checkpoint_iteration = int(last["iteration_completed"] or 0)
    integrity = validate_checkpoint_state(
        {
            "messages_snapshot": last["messages_snapshot"],
            "steps_snapshot": last["steps_snapshot"],
            "working_set_snapshot": last["working_set_snapshot"],
            "progress_summary": last["progress_summary"],
            "current_phase": last["current_phase"],
        },
        iteration=checkpoint_iteration,
    )
    if not integrity.resume_safe:
        _logger.warning(
            "react_loop resume checkpoint rejected (task %s): %s",
            resume_task_id,
            ", ".join(integrity.errors),
        )
        raise ValueError("unsafe checkpoint")

    resume_from_iter = checkpoint_iteration
    messages = base_messages
    steps: list[ReActStep] = []
    working_set = base_working_set
    progress_summary = base_progress_summary
    current_phase = base_current_phase
    final_answer: str | None = None
    terminated_reason = "max_iter"

    if last["messages_snapshot"]:
        messages = _restore_messages_from_checkpoint(last["messages_snapshot"])
    if last["steps_snapshot"]:
        steps = [
            ReActStep(
                iteration=s.get("iteration", 0),
                thought=s.get("thought", ""),
                public_update=s.get("public_update", ""),
                action=s.get("action", ""),
                actions=[str(action) for action in s.get("actions", []) if isinstance(action, str)]
                if isinstance(s.get("actions", []), list)
                else [],
                observation=s.get("observation", ""),
                action_results=[
                    dict(result)
                    for result in s.get("action_results", [])
                    if isinstance(result, dict)
                ]
                if isinstance(s.get("action_results", []), list)
                else [],
            )
            for s in last["steps_snapshot"]
            if isinstance(s, dict)
        ]
        messages = _rehydrate_messages_from_steps(messages, steps)
    if last["working_set_snapshot"]:
        working_set = {
            f["path"]: f
            for f in last["working_set_snapshot"]
            if isinstance(f, dict) and f.get("path")
        }
    if last["progress_summary"]:
        progress_summary = last["progress_summary"]
    if last["current_phase"]:
        current_phase = last["current_phase"]

    # A periodic checkpoint intentionally keeps full raw step receipts for
    # audit/recovery. Rehydration can therefore be much larger than the prompt
    # snapshot itself. Compact before the very first resumed model call instead
    # of waiting until the end of another iteration (which may never fit).
    resume_model = _resume_model_name(stack, intent)
    resume_is_code_mode = bool(working_set) or any(
        any(
            marker in str(step.action or "")
            for marker in (
                "read_file",
                "edit_file",
                "apply_patch",
                "write_text_file",
                "exec_shell",
            )
        )
        for step in steps
    )
    messages = _compress_context(
        messages,
        max_tokens=context_budget_tokens_for_model(resume_model),
        model=resume_model,
        is_code_mode=resume_is_code_mode,
        progress_summary=progress_summary,
        current_phase=current_phase,
        working_set=working_set,
    )
    if last["has_final_answer"] and last["final_answer"]:
        final_answer = str(last["final_answer"])
        terminated_reason = "final_answer"
        resume_from_iter = max_iterations

    resume_event = {
        "type": "react_resumed",
        "task_id": str(resume_task_id),
        "checkpoint_iteration": checkpoint_iteration,
        "resume_from_iteration": resume_from_iter,
        "restored_step_count": len(steps),
        "has_final_answer": bool(final_answer),
        "current_phase": current_phase,
        "progress_summary": progress_summary,
        "checkpoint_source": last.get("source"),
    }
    _logger.info(
        "react_loop resuming from iteration %d (task %s, source=%s)",
        resume_from_iter,
        resume_task_id,
        last.get("source"),
    )
    return _ResumeState(
        resume_from_iter=resume_from_iter,
        messages=messages,
        steps=steps,
        working_set=working_set,
        progress_summary=progress_summary,
        current_phase=current_phase,
        final_answer=final_answer,
        terminated_reason=terminated_reason,
        resume_event=resume_event,
    )


@dataclass
class _ResumedTurn:
    """Products of the PHASE 5 pre-loop registration + resume step."""

    pause_controller: Any
    agent_id_for_pause: str
    steps: list
    messages: list
    working_set: dict
    progress_summary: str
    current_phase: str
    final_answer: str | None
    terminated_reason: str
    react_task_id: Any
    resume_from_iter: int
    resume_event: dict | None
    max_iterations: int


def _resume_or_register_turn(
    stack: Any,
    intent: Any,
    agent: Any,
    *,
    resume_task_id: Any,
    react_task_id: Any,
    thread_id: str,
    max_iterations: int,
    active_max_tokens_budget: Any,
    active_max_usd_budget: Any,
    max_wall_time_seconds: float = 0.0,
    messages: list,
) -> _ResumedTurn:
    """Pause registration, taint reset, checkpoint resume, resume grant.

    Moved verbatim from ``react_loop.stream_react_loop`` (PHASE 5).
    ``messages`` is the freshly assembled prompt/message list; a
    successful checkpoint resume replaces it (and the other base
    containers) with the rehydrated snapshots.
    """
    from runtime.core.cerebrum.pause_control import get_pause_controller

    _pause = get_pause_controller()
    _agent_id_for_pause = str(getattr(agent, "agent_id", "") or "")
    # Resume a paused long task with its historical spend carried over so the
    # cumulative budget stays accurate instead of restarting from zero.
    _carry_tokens, _carry_cost = _carry_prior_spend(stack, resume_task_id)
    _pause.register_active(
        str(react_task_id),
        thread_id=thread_id or "",
        agent_id=_agent_id_for_pause,
        max_iterations=max_iterations,
        max_tokens=active_max_tokens_budget,
        max_usd=active_max_usd_budget,
        max_wall_time_seconds=max_wall_time_seconds,
        carry_tokens=_carry_tokens,
        carry_cost_usd=_carry_cost,
    )
    steps: list[ReActStep] = []
    # Clear any prompt-injection taint from a prior turn in this context,
    # then INHERIT the spawning parent's taint when this loop is a subagent
    # spun up in a fresh thread/context (the taint contextvar doesn't cross
    # the thread-pool boundary, so the parent passes it explicitly via the
    # intent). Without this, delegating a risky action to a subagent would
    # wash the taint clean.
    reset_injection_taint()
    # Also clear the gate-handled flag. It is a per-thread contextvar that the
    # single-action approval gate sets True around execute() to tell the
    # executor chokepoint "this call was already reviewed". When a subagent is
    # spawned INLINE in the parent's thread (call_subagent with the default
    # timeout_seconds=None), it would otherwise inherit the parent's True and
    # the subagent's OWN risky tools (e.g. via its parallel path) would skip
    # the chokepoint without any approval round. A fresh loop has reviewed
    # nothing yet, so reset it like the taint.
    set_injection_gate_handled(False)
    _inherited_taint = intent.user_context.get("_inherited_injection_taint")
    if isinstance(_inherited_taint, str) and _inherited_taint not in ("", "none"):
        mark_injection_taint(_inherited_taint)
    final_answer: str | None = None
    terminated_reason = "max_iter"
    resume_from_iter = 0
    _working_set: dict[str, dict[str, Any]] = {}
    _progress_summary = ""
    _current_phase = "understand"
    _resume_event: dict[str, Any] | None = None

    if resume_task_id is not None:
        try:
            _rs = _compute_resume_state(
                stack,
                intent,
                resume_task_id,
                base_messages=messages,
                base_working_set=_working_set,
                base_progress_summary=_progress_summary,
                base_current_phase=_current_phase,
                max_iterations=max_iterations,
            )
            if _rs is not None:
                resume_from_iter = _rs.resume_from_iter
                messages = _rs.messages
                steps = _rs.steps
                _working_set = _rs.working_set
                _progress_summary = _rs.progress_summary
                _current_phase = _rs.current_phase
                final_answer = _rs.final_answer
                terminated_reason = _rs.terminated_reason
                react_task_id = resume_task_id
                _resume_event = _rs.resume_event
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            # Explicitly observable downgrade: a rejected/corrupt resume
            # checkpoint must surface as a warning (with the task id + reason)
            # instead of silently falling back to a fresh run. The caller
            # still proceeds with a fresh run, but operators can see that a
            # resume was attempted and rejected.
            _logger.warning(
                "resume checkpoint rejected (task %s) — falling back to fresh run: %s",
                resume_task_id,
                type(exc).__name__,
            )

    if resume_task_id is not None:
        _grant = _pause.consume_grant(str(resume_task_id))
        _extra_iters = int(_grant.get("extra_iterations") or 0)
        _extra_tokens = int(_grant.get("extra_tokens") or 0)
        _extra_usd = float(_grant.get("extra_usd") or 0.0)
        if _extra_iters > 0:
            max_iterations = max_iterations + _extra_iters
            _pause.update_active_iteration_limit(str(resume_task_id), max_iterations)
            _logger.info(
                "react_loop resume grant: +%d iterations for task %s (new max=%d)",
                _extra_iters,
                resume_task_id,
                max_iterations,
            )
        if _extra_tokens > 0 or _extra_usd > 0:
            _updated_limits = _pause.extend_active_limits(
                str(resume_task_id),
                extra_tokens=_extra_tokens,
                extra_usd=_extra_usd,
            )
            _logger.info(
                "react_loop resume grant: +%d cumulative tokens, +$%.3f for task %s "
                "(new max tokens=%s, usd=%s)",
                _extra_tokens,
                _extra_usd,
                resume_task_id,
                getattr(_updated_limits, "max_tokens", "?"),
                getattr(_updated_limits, "max_usd", "?"),
            )
        _pause.clear(str(resume_task_id))
    return _ResumedTurn(
        pause_controller=_pause,
        agent_id_for_pause=_agent_id_for_pause,
        steps=steps,
        messages=messages,
        working_set=_working_set,
        progress_summary=_progress_summary,
        current_phase=_current_phase,
        final_answer=final_answer,
        terminated_reason=terminated_reason,
        react_task_id=react_task_id,
        resume_from_iter=resume_from_iter,
        resume_event=_resume_event,
        max_iterations=max_iterations,
    )
