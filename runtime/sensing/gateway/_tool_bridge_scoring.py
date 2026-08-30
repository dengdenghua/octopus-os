"""Per-turn quality scoring + auto-evolution tick helpers.

Extracted from ``tool_bridge.py`` (the Claude-native agentic loop). This
satellite owns the zero-cost heuristic that feeds the SOUL self-evolution
feedback loop: ``_record_score_safe`` writes a best-effort per-turn score
(never raises into the caller), and ``_auto_evolve_tick_safe`` runs the
periodic auto-regression check that auto-reverts a bad lesson.

The parent ``tool_bridge`` module re-exports every name here so existing
importers and tests are unchanged.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import datetime
from threading import Lock
from typing import Any

from runtime.platform.models import ArmId, CostEntry, ParsedIntent, TaskId

from ._tool_bridge_policy import MAX_TOOL_ROUNDS

_logger = logging.getLogger("echo.agentic")
_NATIVE_TRAJECTORY_FINALIZE_LOCK = Lock()


def _one_scope_value(events: Sequence[Any], field: str) -> str | None:
    """Return one exact non-empty envelope value, rejecting mixed scopes."""

    values = {text for event in events if (text := str(getattr(event, field, None) or "").strip())}
    if len(values) > 1:
        raise ValueError(f"native trajectory spans multiple {field} values")
    return next(iter(values), None)


def _persist_native_trajectory_safe(
    *,
    stack: Any,
    agent: Any,
    intent: ParsedIntent,
    task_id: TaskId,
    success: bool,
    disposition: str,
    step_failures: Mapping[int, str] | None = None,
    step_attempts: Mapping[int, Any] | None = None,
    model_cost: CostEntry | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    _prepared_event_cache: dict[str, Any] | None = None,
    _persistence_state: dict[str, bool] | None = None,
) -> bool:
    """Aggregate one native tool-loop turn into a learnable trajectory.

    Native tool calls already cross :class:`ToolExecutor`, which writes an
    exact ``StepEvent`` for every invocation. Historically each invocation
    received a fresh task id, so regeneration could not recover the ordered
    multi-step turn and saw no terminal trajectory. The caller now supplies
    one task id for the whole turn; this helper gathers those executor
    receipts and appends the terminal aggregate.

    The write is best-effort and idempotent for a task id. Learning must never
    make the user-facing response fail, and a resumed/finalized generator must
    not append the same trajectory twice.
    """

    try:
        from runtime.memory.journal import StepEvent, TrajectoryEvent
        from runtime.platform.models import Trajectory, TrajectoryOutcome

        if _persistence_state is not None:
            _persistence_state["durable"] = False

        journal = getattr(stack, "journal", None) or getattr(
            getattr(stack, "executor", None),
            "journal",
            None,
        )
        if (
            journal is None
            or not hasattr(journal, "read_by_task")
            or not hasattr(journal, "write_trajectory_once")
        ):
            return False

        # Personal/confidential turns are useful to the active conversation but
        # must never become process-global regeneration material.
        if getattr(intent, "privacy", "internal") in {"personal", "confidential"}:
            return False

        prepared_event = (
            _prepared_event_cache.get("event") if _prepared_event_cache is not None else None
        )
        if isinstance(prepared_event, TrajectoryEvent):
            inserted = bool(journal.write_trajectory_once(prepared_event))
            if _persistence_state is not None:
                # False is the durable idempotent "already committed" result;
                # transaction/conflict failures raise and are handled below.
                _persistence_state["durable"] = True
            return inserted

        failures = {
            int(key): str(value or "native_tool_error")
            for key, value in (step_failures or {}).items()
        }
        attempts = {int(key): value for key, value in (step_attempts or {}).items()}
        with _NATIVE_TRAJECTORY_FINALIZE_LOCK:
            events = list(journal.read_by_task(task_id))
            step_events = [event for event in events if isinstance(event, StepEvent)]

            step_ids = [event.step.step_id for event in step_events]
            if len(step_ids) != len(set(step_ids)):
                raise ValueError("native trajectory contains duplicate step ids")

            steps = []
            for event in step_events:
                step = event.step
                failure_type = failures.get(step.step_id)
                # Some handlers historically returned ``{ok: false}`` after the
                # executor had already journalled a successful Step. Preserve the
                # bridge's canonical result in the learnable aggregate so the UI,
                # failure miner and SkillForge do not disagree about the attempt.
                if failure_type and (step.success or failure_type == "cancelled"):
                    error_type = (
                        "cancelled"
                        if failure_type == "cancelled"
                        else step.result.error_type or failure_type
                    )
                    stderr_tags = list(step.result.stderr_tags)
                    if failure_type not in stderr_tags:
                        stderr_tags.append(failure_type)
                    result = step.result.model_copy(
                        update={
                            "status": "failed",
                            "error_type": error_type,
                            "stderr_tags": stderr_tags,
                        }
                    )
                    step = step.model_copy(update={"result": result})
                steps.append(step)

            # Registry/preflight denials and execution-before-start
            # cancellations intentionally never cross ToolExecutor, so there
            # is no StepEvent to aggregate.  Preserve each such attempt as a
            # bounded synthetic failed Step in the terminal trajectory rather
            # than dropping an all-negative turn from the audit/learning set.
            from runtime.platform.models import ExecutionResult, SkillId, Step
            from runtime.platform.models import ToolCall as ExecutionToolCall

            recorded_step_ids = {step.step_id for step in steps}
            for step_id, failure_type in failures.items():
                if step_id in recorded_step_ids:
                    continue
                attempt = attempts.get(step_id)
                if attempt is None:
                    continue
                name = str(
                    getattr(attempt, "name", None)
                    or getattr(attempt, "tool", None)
                    or getattr(attempt, "sucker_id", None)
                    or "invalid_native_tool"
                )
                raw_args = getattr(attempt, "input", None)
                if raw_args is None:
                    raw_args = getattr(attempt, "arguments", None)
                if raw_args is None:
                    raw_args = getattr(attempt, "args", None)
                args = dict(raw_args) if isinstance(raw_args, Mapping) else {}
                provider_call_id = str(
                    getattr(attempt, "id", None) or getattr(attempt, "call_id", None) or step_id
                )
                action = ExecutionToolCall(
                    caller="agentic",
                    sucker_id=SkillId(name),
                    args=args,
                )
                steps.append(
                    Step(
                        step_id=step_id,
                        node_id=f"agentic:{provider_call_id}",
                        action=action,
                        result=ExecutionResult(
                            call_id=action.call_id,
                            status="failed",
                            output={"error": failure_type},
                            error_type=failure_type,
                            stderr_tags=["native_bridge_preflight", failure_type],
                            trusted_execution=False,
                            execution_source="native_bridge_preflight",
                        ),
                    )
                )
            if not steps:
                return False
            steps.sort(key=lambda step: step.step_id)

            total_cost = model_cost or CostEntry()
            for step in steps:
                total_cost = total_cost + step.result.cost

            from runtime.platform.process.session import current_session

            session = current_session()
            context = intent.user_context or {}
            session_metadata = getattr(session, "metadata", None) or {}

            # Executor StepEvent envelopes are the authoritative source. The
            # active server-owned Session and intent values are compatibility
            # fallbacks for direct/CLI callers that do not bind journal_context.
            thread_id = (
                _one_scope_value(step_events, "conversation_id")
                or str(
                    getattr(session, "thread_id", None)
                    or getattr(session, "conversation_id", None)
                    or context.get("thread_id")
                    or context.get("conversation_id")
                    or ""
                ).strip()
                or None
            )
            agent_id = (
                _one_scope_value(step_events, "agent_id")
                or str(
                    getattr(session, "agent_id", None) or getattr(agent, "agent_id", None) or ""
                ).strip()
                or None
            )
            tenant_id = (
                _one_scope_value(step_events, "tenant_id")
                or str(session_metadata.get("tenant_id") or context.get("tenant_id") or "").strip()
                or None
            )
            owner_actor_id = (
                _one_scope_value(step_events, "owner_actor_id")
                or str(
                    session_metadata.get("owner_actor_id") or context.get("owner_actor_id") or ""
                ).strip()
                or None
            )
            actor = (
                _one_scope_value(step_events, "actor")
                or str(getattr(session, "actor", None) or owner_actor_id or "").strip()
                or None
            )

            degraded = bool(failures) or any(not step.success for step in steps)
            terminal_disposition = disposition
            if success and degraded and disposition == "completed":
                terminal_disposition = "completed_with_warning"

            arm_id = ArmId("agentic")
            trajectory = Trajectory(
                task_id=task_id,
                thread_id=thread_id,
                arm_id=arm_id,
                strategy_id="native_tool_loop",
                steps=steps,
                outcome=TrajectoryOutcome(
                    success=success,
                    cost=total_cost,
                    degraded=degraded,
                    disposition=terminal_disposition,
                ),
                started_at=started_at or min(step.action.ts for step in steps),
                completed_at=completed_at or max(step.ts for step in steps),
            )
            prepared_event = TrajectoryEvent(
                task_id=task_id,
                arm_id=arm_id,
                actor=actor,
                tenant_id=tenant_id,
                owner_actor_id=owner_actor_id,
                agent_id=agent_id,
                conversation_id=thread_id,
                trajectory=trajectory,
            )
            if _prepared_event_cache is not None:
                _prepared_event_cache["event"] = prepared_event

        # Never hold the process-wide preparation lock across the durable
        # write: StreamingJournal invokes subscribers after commit, and a
        # subscriber is allowed to perform idempotent re-entrant persistence.
        inserted = bool(journal.write_trajectory_once(prepared_event))
        if _persistence_state is not None:
            _persistence_state["durable"] = True
        return inserted
    except Exception:  # noqa: BLE001 — learning telemetry must never break the reply
        _logger.debug("native trajectory persist skipped", exc_info=True)
        return False


def _record_score_safe(
    *,
    agent: Any,
    intent: ParsedIntent,
    has_final_reply: bool,
    tool_error_count: int,
    rounds_used: int,
    duration_ms: int,
    interrupted: bool = False,
) -> None:
    """Best-effort score record · never raises into the caller.

    Uses the heuristic ``score_turn_outcome`` so this function
    itself doesn't make any LLM calls — zero token cost.
    """
    try:
        from runtime.memory.learning.turn_scoring import (
            record_turn_score,
            score_turn_outcome,
        )
        from runtime.platform.process.session import current_session
        from runtime.safety.recovery.tenant_scope import (
            trusted_scope_from_session,
            trusted_scope_from_user_context,
        )

        agent_id = getattr(agent, "agent_id", "") if agent else ""
        if not agent_id:
            return
        score, reason = score_turn_outcome(
            has_final_reply=has_final_reply,
            tool_error_count=tool_error_count,
            rounds_used=rounds_used,
            rounds_max=MAX_TOOL_ROUNDS,
            interrupted=interrupted,
            duration_ms=duration_ms,
        )
        thread_id = (
            getattr(intent, "thread_id", None) or getattr(intent, "conversation_id", None) or ""
        )
        # The private context marker is stamped by the transport boundary.
        # The active Session is the trusted compatibility carrier for OpenAI,
        # CLI and worker paths that do not expose that marker to the intent.
        scope = trusted_scope_from_user_context(intent.user_context)
        if scope is None:
            scope = trusted_scope_from_session(current_session())
        record_turn_score(
            agent_id=agent_id,
            score=score,
            reason=reason,
            rounds=rounds_used,
            duration_ms=duration_ms,
            thread_id=thread_id,
            turn_id=str(getattr(current_session(), "turn_id", None) or ""),
            scope=scope,
        )
        # Auto-evolution tick · every 5 turns run the zero-cost
        # regression heuristic; if it says "regressed" with ≥5
        # post-change samples, auto-revert. This is the "anti-
        # self-harm" feedback loop closing itself: a bad lesson
        # won't persist past 5 bad turns.
        _auto_evolve_tick_safe(agent_id, scope=scope)
    except (ImportError, AttributeError, OSError, ValueError):  # noqa: BLE001 — scoring is observability; failure must never block reply
        # Scoring is observability · a failure must NEVER affect
        # the user's reply. Swallow + move on.
        pass


def _auto_evolve_tick_safe(
    agent_id: str,
    *,
    every: int = 5,
    min_total: int = 15,
    scope: Any = None,
) -> None:
    """Every ``every`` turns, run an auto-regression check.

    Fail-closed: any exception is swallowed · this is behind the
    user's reply and must never affect it. Cost is ~2ms file read
    + the ``analyze_soul_impact`` pure math; only LLM cost if
    ``_auto_regression_check`` escalates (it doesn't — it's
    heuristic-only).

    Args:
        every: how often to tick. Default 5 = every 5 turns.
        min_total: minimum total scores before ticking at all.
            Default 15 = 10 baseline + 5 post-change minimum.
    """
    try:
        from runtime.memory.learning.turn_scoring import read_recent_scores

        scores = read_recent_scores(
            agent_id,
            limit=max(min_total * 2, 40),
            scope=scope,
        )
        if len(scores) < min_total or len(scores) % every != 0:
            return
        # Import lazily so this tick stays optional (skill module
        # can fail to load without breaking the scoring path).
        from pathlib import Path

        # Temporarily set _agent_core_dir since we're outside a
        # Session context (the skill uses it internally).
        import runtime.execution.suckers.memory_skills as _m
        from runtime.execution.suckers.memory_skills import _auto_regression_check

        original = _m._agent_core_dir
        _m._agent_core_dir = lambda: Path("agents") / agent_id / "agent-core"
        try:
            res = _auto_regression_check(
                window=20,
                drop_threshold=0.2,
                min_samples=5,
                dry_run=False,
                _scope=scope,
            )
            action = res.get("action")
            if action == "reverted":
                _logger.info(
                    "auto-evolve tick · agent=%s reverted SOUL (delta=%s)",
                    agent_id,
                    (res.get("analysis") or {}).get("delta"),
                )
        finally:
            _m._agent_core_dir = original
    except (ImportError, AttributeError, OSError):  # noqa: BLE001 — agent_core_dir reset best-effort
        pass
