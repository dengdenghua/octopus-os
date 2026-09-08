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
from runtime.memory.journal import JournalRecoveryReadError
from runtime.platform.config.builder import StackProtocol
from runtime.platform.models import ParsedIntent, TaskId
from runtime.safety.validation.prompt_injection import (
    mark_injection_taint,
    reset_injection_taint,
    set_injection_gate_handled,
)

_logger = logging.getLogger(__name__)


class ResumeCheckpointError(ValueError):
    """A requested recovery cannot safely begin; never permission to rerun."""

    def __init__(self, task_id: Any, code: str = "resume_checkpoint_invalid") -> None:
        self.task_id = str(task_id or "")
        self.code = code
        messages = {
            "resume_checkpoint_missing": "未找到可读取的恢复检查点；原任务仍保留，请核对任务记录后重试。",
            "resume_checkpoint_version_unsupported": "该检查点版本不受当前程序支持；请使用兼容版本恢复。",
            "resume_confirmation_unavailable": "未找到匹配的待确认恢复请求；请重新选择原任务的检查点。",
            "resume_task_invalid": "恢复任务身份无效；请重新选择原任务，未开始执行。",
            "resume_checkpoint_unavailable": "暂时无法读取恢复检查点；原任务保持不变，请稍后重试。",
        }
        super().__init__(
            messages.get(
                code, "检查点损坏或不完整，无法安全恢复；请核对已有执行结果，未重新执行任务。"
            )
        )


def _validate_resume_checkpoint_snapshot(
    snapshot: dict[str, Any] | None,
    task_id: Any,
) -> dict[str, Any]:
    from runtime.core.cerebrum.checkpoint_integrity import validate_checkpoint_state

    if snapshot is None:
        raise ResumeCheckpointError(task_id, "resume_checkpoint_missing")
    if not isinstance(snapshot, dict):
        raise ResumeCheckpointError(task_id)
    integrity = validate_checkpoint_state(
        snapshot,
        iteration=snapshot.get("iteration_completed", 0),
    )
    if not integrity.resume_safe:
        code = (
            "resume_checkpoint_version_unsupported"
            if "unsupported_checkpoint_version" in integrity.errors
            else "resume_checkpoint_invalid"
        )
        _logger.warning("resume checkpoint rejected (task %s): %s", task_id, integrity.errors)
        raise ResumeCheckpointError(task_id, code)
    return snapshot


def _require_resume_checkpoint(stack: Any, intent: Any, task_id: Any) -> dict[str, Any]:
    try:
        return _validate_resume_checkpoint_snapshot(
            _load_resume_checkpoint_snapshot(stack, intent, task_id),
            task_id,
        )
    except ResumeCheckpointError:
        raise
    except JournalRecoveryReadError as exc:
        raise ResumeCheckpointError(task_id, "resume_checkpoint_missing") from exc
    except (AttributeError, KeyError, TypeError, ValueError, OSError) as exc:
        raise ResumeCheckpointError(task_id) from exc
    except Exception as exc:  # noqa: BLE001 — provider failure never authorizes a fresh run
        raise ResumeCheckpointError(task_id, "resume_checkpoint_unavailable") from exc


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
    resume_intent = (getattr(intent, "user_context", None) or {}).get("resume_intent")
    if isinstance(resume_intent, dict) and resume_intent.get("checkpoint_id"):
        # An explicit selection identifies one trace checkpoint. Do not replace
        # a missing/corrupt selection with the journal's latest different state.
        return _load_trace_resume_checkpoint_snapshot(intent, resume_task_id)
    journal = getattr(stack, "journal", None)
    if journal is not None:
        from runtime.safety.recovery.tenant_scope import read_recovery_events

        ckpts = [
            e
            for e in read_recovery_events(journal, "react_checkpoint", scope=_resume_scope(intent))
            if str(getattr(e, "task_id", "")) == str(resume_task_id)
        ]
        if ckpts:
            return _checkpoint_snapshot_from_journal_event(ckpts[-1])
    return _load_trace_resume_checkpoint_snapshot(intent, resume_task_id)


def _resume_scope(intent: Any) -> Any:
    from runtime.platform.process.session import current_session
    from runtime.safety.recovery.tenant_scope import (
        trusted_scope_from_session,
        trusted_scope_from_user_context,
    )

    return trusted_scope_from_user_context(
        getattr(intent, "user_context", None)
    ) or trusted_scope_from_session(current_session())


def _checkpoint_snapshot_from_journal_event(event: Any) -> dict[str, Any]:
    return {
        "source": "journal",
        "schema_version": getattr(event, "schema_version", 1),
        "iteration_completed": getattr(event, "iteration_completed", 0),
        "max_iterations": getattr(event, "max_iterations", 0),
        "messages_snapshot": getattr(event, "messages_snapshot", []),
        "steps_snapshot": getattr(event, "steps_snapshot", []),
        "has_final_answer": getattr(event, "has_final_answer", False),
        "final_answer": getattr(event, "final_answer", ""),
        "working_set_snapshot": getattr(event, "working_set_snapshot", []),
        "progress_summary": getattr(event, "progress_summary", ""),
        "current_phase": getattr(event, "current_phase", ""),
    }


def _load_trace_resume_checkpoint_snapshot(
    intent: ParsedIntent,
    resume_task_id: TaskId,
) -> dict[str, Any] | None:
    resume_intent = (intent.user_context or {}).get("resume_intent")
    if not isinstance(resume_intent, dict):
        return None
    checkpoint_id = resume_intent.get("checkpoint_id")
    if type(checkpoint_id) is not int or checkpoint_id <= 0:
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
    scope = _resume_scope(intent)
    checkpoint = (
        trace_store.checkpoint_by_id(checkpoint_id, scope=scope)
        if scope is not None
        else trace_store.checkpoint_by_id(checkpoint_id)
    )
    if not isinstance(checkpoint, dict):
        return None
    if scope is None and (checkpoint.get("tenant_id") or checkpoint.get("owner_actor_id")):
        return None
    if str(checkpoint.get("task_id") or "") != str(resume_task_id):
        return None
    if str(checkpoint.get("checkpoint_type") or "").lower() != "react":
        return None
    return _checkpoint_snapshot_from_trace(checkpoint, resume_task_id)


def _checkpoint_snapshot_from_trace(checkpoint: dict[str, Any], task_id: Any) -> dict[str, Any]:
    state = checkpoint.get("state")
    if not isinstance(state, dict):
        raise ResumeCheckpointError(task_id)
    return {
        "source": "trace_store",
        "schema_version": state.get("schema_version", 1),
        "iteration_completed": state.get("iteration_completed", checkpoint.get("iteration", 0)),
        "max_iterations": state.get("max_iterations", 0),
        "messages_snapshot": state.get("messages_snapshot", []),
        "steps_snapshot": state.get("steps_snapshot", []),
        "has_final_answer": state.get("has_final_answer", False),
        "final_answer": state.get("final_answer", ""),
        "working_set_snapshot": state.get("working_set_snapshot", []),
        "progress_summary": state.get("progress_summary", checkpoint.get("summary") or ""),
        "current_phase": state.get("current_phase", ""),
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
    snapshot: dict[str, Any] | None = None,
) -> _ResumeState:
    """Load + validate a resume checkpoint and rebuild loop state from it.

    No mutation of caller state. Missing or unsafe recovery raises a typed
    rejection; it never authorizes a fresh execution of the original task.
    """
    last = (
        _validate_resume_checkpoint_snapshot(snapshot, resume_task_id)
        if snapshot is not None
        else _require_resume_checkpoint(stack, intent, resume_task_id)
    )
    checkpoint_iteration = last["iteration_completed"]

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
    resume_snapshot: dict[str, Any] | None = None,
) -> _ResumedTurn:
    """Pause registration, taint reset, checkpoint resume, resume grant.

    Moved verbatim from ``react_loop.stream_react_loop`` (PHASE 5).
    ``messages`` is the freshly assembled prompt/message list; a
    successful checkpoint resume replaces it (and the other base
    containers) with the rehydrated snapshots.
    """
    from runtime.core.cerebrum.pause_control import get_pause_controller

    # Rebuild before any active registration, taint reset, grant consumption,
    # or pause clearing. Recovery failure must leave the original task intact.
    _rs = None
    if resume_task_id is not None:
        try:
            _rs = _compute_resume_state(
                stack,
                intent,
                resume_task_id,
                base_messages=messages,
                base_working_set={},
                base_progress_summary="",
                base_current_phase="understand",
                max_iterations=max_iterations,
                snapshot=resume_snapshot,
            )
            if _rs is None:
                raise ResumeCheckpointError(resume_task_id, "resume_checkpoint_missing")
        except ResumeCheckpointError:
            raise
        except (AttributeError, KeyError, TypeError, ValueError, OSError) as exc:
            _logger.warning(
                "resume checkpoint rejected (task %s): %s", resume_task_id, type(exc).__name__
            )
            raise ResumeCheckpointError(resume_task_id) from exc

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
        if final_answer is not None:
            # An unused grant cannot reopen an already-final checkpoint.
            resume_from_iter = max_iterations
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
