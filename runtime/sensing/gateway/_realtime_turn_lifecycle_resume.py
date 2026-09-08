"""Resume-intent persistence for the realtime turn lifecycle.

Unit: the pending resume-intent store — recording a pending resume
intent (``_record_pending_resume_intent``) and consuming a confirmed
resume intent (``_consume_confirmed_resume_intent``).

Split out of ``realtime_turn_lifecycle.py`` so that orchestrator stays
under the god-file line budget.
"""

from __future__ import annotations

import contextlib
import re
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from uuid import UUID

from runtime.core.cerebrum.react_resume import (
    ResumeCheckpointError,
    _checkpoint_snapshot_from_journal_event,
    _checkpoint_snapshot_from_trace,
    _validate_resume_checkpoint_snapshot,
)
from runtime.sensing.gateway.realtime_turn_input import (
    _execution_resume_intent,
    _parse_resume_confirmation,
    _safe_int,
)

if TYPE_CHECKING:
    from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime


_PLAIN_RESUME_RE = re.compile(
    r"^\s*(?:继续(?:吧|任务|执行)?|接着(?:做)?|然后呢|往下(?:做)?|恢复|resume|continue|[?？]{1,4})\s*[。.!！]?\s*$",
    re.IGNORECASE,
)
_DEFAULT_RESUME_ITERATION_GRANT = 15
_DEFAULT_RESUME_TOKEN_GRANT = 100_000


async def _record_pending_resume_intent(
    runtime: CerebrumRuntime,
    thread_id: str,
    resume_intent: dict[str, Any],
) -> None:
    async with runtime._resume_intents_lock:
        runtime._pending_resume_intents[thread_id] = dict(resume_intent)
    if runtime._trace_store is None:
        return
    with contextlib.suppress(Exception):
        runtime._trace_store.record_resume_request(
            thread_id=thread_id,
            checkpoint_id=int(resume_intent.get("checkpoint_id") or 0),
            task_id=resume_intent.get("task_id"),
            status="pending",
            intent=resume_intent,
        )


async def _consume_confirmed_resume_intent(
    runtime: CerebrumRuntime,
    thread_id: str,
    text: str,
    *,
    scope: Any = None,
) -> dict[str, Any] | None:
    checkpoint_id = _parse_resume_confirmation(text)
    if checkpoint_id is None:
        return None
    async with runtime._resume_intents_lock:
        pending = runtime._pending_resume_intents.get(thread_id)
        pending_request_id: int | None = None
        if not isinstance(pending, dict) and runtime._trace_store is not None:
            with contextlib.suppress(Exception):
                request = runtime._trace_store.latest_pending_resume_request(thread_id=thread_id)
                if isinstance(request, dict):
                    pending = request.get("intent")
                    pending_request_id = _safe_int(request.get("id"))
        if not isinstance(pending, dict):
            raise ResumeCheckpointError("", "resume_confirmation_unavailable")
        if _safe_int(pending.get("checkpoint_id")) != checkpoint_id:
            raise ResumeCheckpointError(pending.get("task_id"), "resume_confirmation_unavailable")
        _validate_realtime_resume(runtime, pending, thread_id=thread_id, scope=scope)
        if runtime._trace_store is not None:
            try:
                confirmed = runtime._trace_store.confirm_resume_request(
                    thread_id=thread_id,
                    checkpoint_id=checkpoint_id,
                    confirmation_text=f"确认恢复 checkpoint #{checkpoint_id}",
                )
                if not isinstance(confirmed, dict):
                    raise ResumeCheckpointError(pending.get("task_id"), "resume_confirmation_unavailable")
                pending_request_id = _safe_int(confirmed.get("id")) or pending_request_id
                if pending_request_id is None or not runtime._trace_store.consume_resume_request(pending_request_id):
                    raise ResumeCheckpointError(pending.get("task_id"), "resume_confirmation_unavailable")
            except ResumeCheckpointError:
                raise
            except Exception as exc:
                raise ResumeCheckpointError(pending.get("task_id"), "resume_checkpoint_unavailable") from exc
        runtime._pending_resume_intents.pop(thread_id, None)
    return _execution_resume_intent(pending, checkpoint_id)


def _validate_realtime_resume(
    runtime: CerebrumRuntime,
    resume_intent: dict[str, Any],
    *,
    thread_id: str,
    scope: Any = None,
) -> None:
    """Validate the selected durable state before dispatch or consuming pause state."""
    task_id = str(resume_intent.get("task_id") or "")
    try:
        UUID(task_id)
    except (ValueError, TypeError) as exc:
        raise ResumeCheckpointError(task_id, "resume_task_invalid") from exc
    if resume_intent.get("checkpoint_type") != "react":
        raise ResumeCheckpointError(task_id, "resume_task_invalid")
    checkpoint_id = resume_intent.get("checkpoint_id", 0)
    if type(checkpoint_id) is not int or checkpoint_id < 0:
        raise ResumeCheckpointError(task_id)
    try:
        if checkpoint_id:
            store = runtime._trace_store
            checkpoint = (
                store.checkpoint_by_id(checkpoint_id, scope=scope) if store is not None else None
            )
            if (
                not isinstance(checkpoint, dict)
                or str(checkpoint.get("task_id") or "") != task_id
                or checkpoint.get("checkpoint_type") != "react"
                or (checkpoint.get("thread_id") and checkpoint["thread_id"] != thread_id)
                or (
                    scope is None
                    and (checkpoint.get("tenant_id") or checkpoint.get("owner_actor_id"))
                )
            ):
                raise ResumeCheckpointError(task_id, "resume_checkpoint_missing")
            snapshot = _checkpoint_snapshot_from_trace(checkpoint, task_id)
        else:
            from runtime.core.cerebrum.react_resume import _load_resume_checkpoint_snapshot
            from runtime.safety.recovery.tenant_scope import (
                AUTHORITATIVE_SCOPE_CONTEXT_KEY,
                authoritative_scope_context,
            )

            snapshot = _load_resume_checkpoint_snapshot(
                runtime._stack,
                SimpleNamespace(
                    user_context={
                        AUTHORITATIVE_SCOPE_CONTEXT_KEY: authoritative_scope_context(scope)
                    }
                    if scope is not None
                    else {}
                ),
                task_id,
            )
        _validate_resume_checkpoint_snapshot(snapshot, task_id)
    except ResumeCheckpointError:
        raise
    except (AttributeError, KeyError, TypeError, ValueError, OSError) as exc:
        raise ResumeCheckpointError(task_id) from exc
    except Exception as exc:  # noqa: BLE001 — a storage error cannot become a new task
        raise ResumeCheckpointError(task_id, "resume_checkpoint_unavailable") from exc


def _latest_paused_task_for_thread(thread_id: str) -> Any | None:
    """Return the newest durable pause for one realtime thread.

    A thread normally has one resumable task. Older builds accidentally
    created a fresh task for every ``继续`` message, though, so production
    state can contain several paused task ids for the same thread. Selecting
    by ``requested_at`` makes the handoff deterministic and resumes the most
    recent checkpoint instead of an arbitrary dict insertion.
    """

    from runtime.core.cerebrum.pause_control import get_pause_controller

    controller = get_pause_controller()
    candidates = [request for request in controller.list_paused() if request.thread_id == thread_id]
    if not candidates:
        return None
    return max(candidates, key=lambda request: request.requested_at)


def _unambiguous_paused_task_for_thread(thread_id: str) -> Any | None:
    """Return the only resumable objective, never guess among manual pauses."""

    from runtime.core.cerebrum.pause_control import get_pause_controller

    candidates = [
        request
        for request in get_pause_controller().list_paused()
        if request.thread_id == thread_id
    ]
    if len(candidates) == 1:
        return candidates[0]
    return None


def _resume_checkpoint_metadata(
    runtime: CerebrumRuntime,
    task_id: str,
    *,
    strict: bool = False,
    scope: Any = None,
) -> dict[str, Any] | None:
    """Read only the sanitized fields needed to resume a ReAct checkpoint."""

    try:
        from runtime.platform.process.session import current_session
        from runtime.safety.recovery.tenant_scope import trusted_scope_from_session

        effective_scope = scope or trusted_scope_from_session(current_session())
        trace_store = runtime._trace_store
        checkpoint = None
        if trace_store is not None and hasattr(trace_store, "latest_checkpoint"):
            checkpoint = trace_store.latest_checkpoint(
                task_id=task_id,
                checkpoint_type="react",
                scope=effective_scope,
            )
        if isinstance(checkpoint, dict):
            snapshot = _checkpoint_snapshot_from_trace(checkpoint, task_id)
            checkpoint_id = _safe_int(checkpoint.get("id")) or 0
        else:
            journal = getattr(runtime._stack, "journal", None)
            if journal is None or not hasattr(journal, "read_by_type"):
                return None
            from runtime.safety.recovery.tenant_scope import read_recovery_events

            checkpoints = [
                event
                for event in read_recovery_events(
                    journal,
                    "react_checkpoint",
                    scope=effective_scope,
                )
                if str(getattr(event, "task_id", "") or "") == task_id
            ]
            if not checkpoints:
                return None
            snapshot = _checkpoint_snapshot_from_journal_event(checkpoints[-1])
            checkpoint_id = 0
        _validate_resume_checkpoint_snapshot(snapshot, task_id)
        return {
            "checkpoint_id": checkpoint_id,
            "iteration": snapshot["iteration_completed"],
            "phase": snapshot["current_phase"],
            "working_set": [
                str(item.get("path"))
                for item in snapshot["working_set_snapshot"]
                if isinstance(item, dict) and item.get("path")
            ][:32],
        }
    except Exception as exc:  # noqa: BLE001 — status stays readable; execution uses strict=True
        # A corrupt latest trace is not permission to advertise or select an
        # older journal checkpoint. Status remains readable but non-resumable.
        if strict:
            if isinstance(exc, ResumeCheckpointError):
                raise
            raise ResumeCheckpointError(task_id, "resume_checkpoint_unavailable") from exc
        return None


async def _consume_paused_task_resume_intent(
    runtime: CerebrumRuntime,
    thread_id: str,
    text: str,
    *,
    scope: Any = None,
) -> dict[str, Any] | None:
    """Turn a strict short Continue message into a durable task resume.

    Resume-proposal confirmations keep their existing explicit checkpoint
    flow. This path is only for a task already paused by the runtime (or
    selected through the paused-task banner), so ordinary mentions of the
    word "继续" inside a longer instruction cannot hijack a new turn.
    """

    if _PLAIN_RESUME_RE.fullmatch(text or "") is None:
        return None

    from runtime.core.cerebrum.pause_control import get_pause_controller

    controller = get_pause_controller()
    task_id = controller.consume_pending_resume(thread_id)
    selected_from_banner = bool(task_id)
    pause_request = controller.get_request(task_id) if task_id else None
    if pause_request is not None and pause_request.thread_id != thread_id:
        raise ResumeCheckpointError(task_id, "resume_task_invalid")

    if not task_id:
        pause_request = _unambiguous_paused_task_for_thread(thread_id)
        task_id = pause_request.task_id if pause_request is not None else None
    if not task_id:
        return None

    try:
        checkpoint = _resume_checkpoint_metadata(runtime, task_id, strict=True)
    except ResumeCheckpointError:
        if selected_from_banner:
            controller.set_pending_resume(thread_id, task_id)
        raise
    if checkpoint is None:
        # A banner click can race the loop's checkpoint write. Preserve the
        # explicit handoff instead of consuming it irreversibly.
        if selected_from_banner:
            controller.set_pending_resume(thread_id, task_id)
        raise ResumeCheckpointError(task_id, "resume_checkpoint_missing")

    try:
        _validate_realtime_resume(
            runtime,
            {
                "task_id": task_id,
                "checkpoint_type": "react",
                "checkpoint_id": checkpoint["checkpoint_id"],
            },
            thread_id=thread_id,
            scope=scope,
        )
    except ResumeCheckpointError:
        if selected_from_banner:
            controller.set_pending_resume(thread_id, task_id)
        raise

    if not selected_from_banner:
        pause_reason = getattr(pause_request, "reason", "")
        if pause_reason == "iteration_near_limit":
            controller.set_grant(
                task_id,
                extra_iterations=_DEFAULT_RESUME_ITERATION_GRANT,
            )
        elif pause_reason == "budget_near_limit":
            # A plain Continue is permission to extend cumulative processing
            # runway, not permission to raise a monetary limit. If cost was the
            # actual limiting dimension this token grant is harmless and the
            # explicit USD approval path remains required.
            controller.set_grant(
                task_id,
                extra_tokens=_DEFAULT_RESUME_TOKEN_GRANT,
            )

    # Clean up duplicate system auto-pauses left by the old "继续 creates a
    # new task" bug. User-requested pauses remain independent.
    for stale in controller.list_paused():
        if (
            stale.thread_id == thread_id
            and stale.task_id != task_id
            and stale.requested_by == "system"
            and stale.reason in {"iteration_near_limit", "budget_near_limit", "model_spinning"}
        ):
            controller.clear(stale.task_id)

    iteration = _safe_int(checkpoint.get("iteration"))
    return {
        "schema": "echo.resume_intent.v1",
        "requires_confirmation": False,
        "confirmed": True,
        "source": "paused_task_continue",
        "checkpoint_id": _safe_int(checkpoint.get("checkpoint_id")) or 0,
        "task_id": task_id,
        "checkpoint_type": "react",
        "iteration": iteration,
        "continue_from_iteration": iteration,
        "phase": str(checkpoint.get("phase") or ""),
        "working_set": list(checkpoint.get("working_set") or []),
        "resume_plan": [],
        "safety": {
            "raw_state_included": False,
            "raw_message_snapshots_included": False,
        },
        "confirmation_text": text.strip(),
    }
