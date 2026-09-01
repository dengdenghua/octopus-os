"""Realtime turn validation, dispatch, resume handling, and finalization."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import TYPE_CHECKING, Any, cast

from runtime.execution.tool_engine.session_reference_uri import (
    SUPPORTED_SESSION_REFERENCE_SCHEMES,
)
from runtime.platform.models.primitives import now_utc
from runtime.protocol import (
    ErrorItem,
    ItemStatus,
    ServerMethod,
    Turn,
    TurnParams,
    TurnStatus,
    VerificationItem,
)
from runtime.safety.approval.approval_gate import ApprovalProvider
from runtime.sensing.gateway._realtime_cerebrum_project_os import _is_project_os_command
from runtime.sensing.gateway._realtime_turn_lifecycle_helpers import (
    _background_task_is_verification,
    _inject_cowork_turn_plan,
    _persist_cowork_user_message,
    _resolve_cowork_responder_agent,
    _turn_has_observable_output,
)
from runtime.sensing.gateway._realtime_turn_lifecycle_resume import (
    _consume_confirmed_resume_intent,
    _consume_paused_task_resume_intent,
    _record_pending_resume_intent,
    _resume_checkpoint_metadata,
)

# Re-exported helper names reachable from the old module-level surface.
__all__ = [
    "_consume_confirmed_resume_intent",
    "_consume_paused_task_resume_intent",
    "_record_pending_resume_intent",
]
from runtime.sensing.gateway.realtime_approval import GatewayApprovalProvider
from runtime.sensing.gateway.realtime_gateway import EventEmitter
from runtime.sensing.gateway.realtime_thread_history import (
    _conversation_messages_for_react,
)
from runtime.sensing.gateway.realtime_turn_input import (
    _build_intent,
    _extract_codex_composer_mode,
    _input_attachments,
    _input_metadata,
    _join_text,
    _resume_confirmation_text,
    _should_default_planning_mode,
    _should_default_topology,
    _turn_mode,
)
from runtime.sensing.gateway.realtime_turn_outcome import (
    _code_change_paths,
    _file_change_item_ids,
    _turn_has_failed_code_verification,
    _turn_has_unverified_code_changes,
    _turn_verification_environment_blocked,
    _verification_plan_for_code_paths,
    _verification_plan_stdout_tail,
)

if TYPE_CHECKING:
    from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime

_logger = logging.getLogger(__name__)

# Bounded window (seconds) the turn verifier waits for an in-flight
# background verification task (background_exec) before closing the turn.
# The model may hand verification to a background task and return while it
# is still running; failing with "verification required" then would blame
# the model for work that is genuinely still finishing.
_BACKGROUND_VERIFY_WAIT_S = 180.0

# A parked-report disk scan should usually finish from the OS cache in a few
# milliseconds.  Give it a small foreground budget so reports are available to
# the first model round when cheap, then let the strongly tracked worker finish
# in the background instead of turning a cold/contended store into 12–42 s of
# model-start latency.
_PENDING_REPORT_STARTUP_BUDGET_S = 1.0

# A Stop can arrive immediately after the client sees ``turn/started``.  The
# pending-report scan is intentionally allowed a one-second warm-start window,
# but that foreground budget must never become a one-second cancellation
# floor.  Poll the gateway's durable interrupt registry while the report task
# runs so the lifecycle can close before any model/tool driver is constructed.
_STARTUP_INTERRUPT_POLL_S = 0.05

# Bounded number of agent-driven verification rounds the turn verifier runs
# when code changed with no recorded verification evidence and the
# auto-verifier could not produce any (sandbox didn't allow it, or no
# allowlisted command fit). Each round asks the agent to run the recommended
# commands itself and fix; one round is enough to close the loop without
# burning budget on a model that keeps failing.
_AGENT_VERIFY_ROUND_LIMIT = 1

# Item markers that are one half of a deliberate pair: the spawn tile is
# never completed, its sibling ``__subagent_finished__`` carries the outcome.
# Sweeping these would emit a completion the frontend reads as a second,
# phantom result for the same lane.
_UNPAIRED_ITEM_MARKERS = frozenset({"__subagent_spawned__"})


def _item_outlives_turn(item: Any) -> bool:
    """True when this item is *meant* to still be running at turn end.

    ``background_exec`` hands work to a task that deliberately outlives the
    turn; a watcher completes the item minutes later. Sweeping it would
    overwrite a real result that is still coming.
    """
    preview = getattr(item, "input_preview", None) or getattr(item, "inputPreview", None)
    if isinstance(preview, dict) and preview.get("background") is True:
        return True
    return bool(getattr(item, "task_id", None))


def _close_turn(
    log: Any,
    thread_id: str,
    turn: Any,
    *,
    error: dict[str, Any] | None = None,
) -> None:
    """Terminate any still-running item, then log the turn as completed.

    Every path out of a turn goes through here so no path can forget. An item
    left at ``inProgress`` spins in the UI forever: in thread
    teD7hPf9dkGOExwO0dIiBE an interrupted turn stranded three ``bb_read``
    calls and one ``call_agent_parallel``, and the interrupt path logged
    ``turn_completed`` without touching them.

    The sweep runs *before* ``turn_completed`` so the journal keeps its
    chronology -- a completion appended after the turn closed would replay out
    of order.
    """
    # Settle governed canary evidence before writing the terminal journal
    # record. Completed and failed turns are gradable; user-driven pause,
    # cancellation, and interruption are explicitly discarded.
    try:
        from runtime.safety.evolution.runtime_outcomes import (
            settle_runtime_candidate_outcomes,
        )

        candidate_success: bool | None
        if getattr(turn, "status", None) is TurnStatus.COMPLETED:
            candidate_success = True
        elif getattr(turn, "status", None) is TurnStatus.FAILED:
            turn_error = turn.error if isinstance(getattr(turn, "error", None), dict) else None
            candidate_success = (
                None if turn_error and turn_error.get("disposition") == "blocked_on_user" else False
            )
        else:
            candidate_success = None
        settle_runtime_candidate_outcomes(
            str(getattr(turn, "id", "") or ""),
            success=candidate_success,
        )
    except Exception:  # noqa: BLE001 — evolution telemetry must not break the turn
        _logger.debug("candidate outcome settlement skipped", exc_info=True)

    for item in getattr(turn, "items", None) or []:
        if getattr(item, "status", None) is not ItemStatus.IN_PROGRESS:
            continue
        if str(getattr(item, "tool", "") or "") in _UNPAIRED_ITEM_MARKERS:
            continue
        if _item_outlives_turn(item):
            continue
        item.status = ItemStatus.FAILED
        # Say why it stopped rather than leaving a bare failure: the turn's own
        # outcome is the cause, and without it the item looks like the command
        # itself broke. Item models carry a message in different fields and
        # ``CommandExecutionItem`` -- the type that actually leaked here -- has
        # no ``error`` at all, so route by declared field. Assigning to a field
        # a pydantic model does not declare raises ValueError, not
        # AttributeError, so ask the model rather than catching.
        detail = f"turn ended while this was still running ({turn.status.value})"
        fields = getattr(type(item), "model_fields", {})
        if "error" in fields and not getattr(item, "error", None):
            item.error = detail
        elif "aggregated_output" in fields:
            existing = getattr(item, "aggregated_output", None) or ""
            item.aggregated_output = f"{existing}\n[{detail}]".lstrip()
        with contextlib.suppress(Exception):
            log.item_completed(thread_id, turn.id, item)
    log.turn_completed(thread_id, turn.id, turn.status, error)

    # dsh ``Stop``: fired when a full agent turn completes, successfully or
    # not (notification-type hook — post-hoc audit / metrics). Every path
    # out of a turn funnels through this helper, so a single dispatch here
    # covers normal, error, cancel, and interrupt endings. Best-effort:
    # hook failures must never break the turn lifecycle.
    try:
        from runtime.platform.process.session import current_session
        from runtime.safety.hooks.runner import dispatch_stop

        dispatch_stop(
            thread_id=thread_id,
            success=getattr(turn, "status", None) is TurnStatus.COMPLETED,
            step_count=len(getattr(turn, "items", None) or []),
            session=current_session(),
        )
    except Exception:  # noqa: BLE001 — notification is best-effort
        _logger.debug("dispatch_stop skipped", exc_info=True)


def _finish_startup_interrupt(
    runtime: CerebrumRuntime,
    turn: Turn,
    log: Any,
    emitter: EventEmitter,
    *,
    intent: Any | None,
) -> bool:
    """Close an explicit Stop before an execution driver owns the turn.

    ReAct, topology, reflection, and partner drivers each have their own
    cancellation boundary. Startup work happens before any of those drivers
    exist, so relying on their pollers lets an already-acknowledged Stop drift
    through report discovery, intent construction, and agent resolution.
    Preserve the durable user-message anchor, then terminate the turn here.
    """

    if not emitter.is_turn_interrupted(turn.id):
        return False
    runtime._set_turn_steering_accepting(turn, False)
    turn.status = TurnStatus.CANCELLED
    turn.outcome_reason = "user_cancelled"
    if not turn.interrupt_reason:
        with contextlib.suppress(Exception):
            turn.interrupt_reason = emitter.get_interrupt_reason(turn.id)
    log.turn_updated(
        turn.thread_id,
        turn.id,
        objective_id=turn.objective_id,
        task_id=turn.task_id,
        outcome_reason=turn.outcome_reason,
    )
    _close_turn(log, turn.thread_id, turn)
    runtime._snapshot_to_thread_store(turn.thread_id, log, intent)
    return True


def _resolve_session_reference_mentions(
    text: str,
    thread_id: str,
) -> tuple[str, str | None]:
    """Resolve session aliases and canonical Echo mentions in a user prompt.

    Both current ``echo-session:`` references and historical
    ``dsh-session:`` references are accepted.

    Returns ``(clean_text, frame)`` — mention tokens replaced by their
    readable labels plus the rendered referenced-sessions frame when any
    mention resolved (``None`` otherwise). Best-effort: a missing store,
    read failure, or budget error leaves the prompt untouched so mention
    resolution can never break a turn.
    """
    if not text:
        return text, None
    if (
        "@session:" not in text
        and "@subagent:" not in text
        and not any(scheme in text for scheme in SUPPORTED_SESSION_REFERENCE_SCHEMES)
    ):
        return text, None
    try:
        from runtime.execution.subagents.sessions import (
            get_subagent_session_store,
        )

        store = get_subagent_session_store()
        if store is None:
            return text, None
        resolved = store.resolve_session_mentions(
            text,
            target_id=thread_id,
            strip_mentions=True,
        )
        clean = resolved.content
        if isinstance(clean, str) and clean.strip():
            text = clean
        frame: str | None = None
        if resolved.additional_context is not None:
            content = resolved.additional_context.get("content")
            if isinstance(content, list) and content and isinstance(content[0], dict):
                frame_text = content[0].get("text")
                if isinstance(frame_text, str) and frame_text.strip():
                    frame = frame_text
        return text, frame
    except Exception:  # noqa: BLE001 — mention resolution is best-effort
        _logger.debug("session-reference mention resolution skipped", exc_info=True)
        return text, None


async def _surface_pending_subagent_reports(
    thread_id: str,
) -> tuple[int, int]:
    """Claim parked subagent reports without blocking realtime delivery.

    Durable session discovery can parse every session JSON while holding the
    store's main lock.  It used to run before ``turn_started`` and therefore
    made an unrelated cold scan or lock holder look like a failed Send.  Run
    the exact same claim/inject/ack sequence in a worker after the user anchor
    is visible.  ``inject_report_into_thread`` is explicitly worker-safe: it
    crosses into the active turn through the steering registry.

    Returns ``(pending_count, injected_count)`` for startup diagnostics.
    A report is still acknowledged only after successful injection, preserving
    the existing at-least-once durable / exactly-once steering boundary.
    """

    def _surface() -> tuple[int, int]:
        from runtime.execution.subagents.sessions import (
            get_subagent_session_store,
            inject_report_into_thread,
        )

        store = get_subagent_session_store()
        if store is None:
            return 0, 0
        pending = store.pending_thread_reports(thread_id)
        injected = 0
        for session_id, index, report in pending:
            if inject_report_into_thread(thread_id, report.content):
                store.mark_reports_delivered(session_id, up_to_index=index)
                injected += 1
        return len(pending), injected

    try:
        return await asyncio.to_thread(_surface)
    except Exception:  # noqa: BLE001 — report surfacing is best-effort
        _logger.debug("pending subagent report surfacing skipped", exc_info=True)
        return 0, 0


async def _refill_subagent_wake_budget(thread_id: str) -> None:
    """Best-effort human-turn wake-budget refill on the store worker lane."""

    def _refill() -> None:
        from runtime.execution.subagents.sessions import get_subagent_session_store

        store = get_subagent_session_store()
        if store is not None:
            store.refill_thread_wake_budget(thread_id)

    try:
        await asyncio.to_thread(_refill)
    except Exception:  # noqa: BLE001 — budget refill is best-effort
        _logger.debug("subagent wake-budget refill skipped", exc_info=True)


def _schedule_subagent_wake_budget_refill(
    runtime: CerebrumRuntime,
    thread_id: str,
) -> None:
    registry = getattr(runtime, "_pending_subagent_refill_tasks", None)
    if not isinstance(registry, dict):
        registry = {}
        runtime._pending_subagent_refill_tasks = registry
    existing = registry.get(thread_id)
    if existing is not None and not existing.done():
        return
    task = asyncio.create_task(
        _refill_subagent_wake_budget(thread_id),
        name=f"subagent-wake-refill:{thread_id}",
    )
    registry[thread_id] = task

    def _discard(done: asyncio.Task[None]) -> None:
        if registry.get(thread_id) is done:
            registry.pop(thread_id, None)

    task.add_done_callback(_discard)


def _schedule_pending_subagent_reports(
    runtime: CerebrumRuntime,
    *,
    thread_id: str,
    turn_id: str,
) -> asyncio.Task[tuple[int, int]]:
    """Return the one live report-surfacing task for ``thread_id``."""

    registry = getattr(runtime, "_pending_subagent_report_tasks", None)
    if not isinstance(registry, dict):
        registry = {}
        runtime._pending_subagent_report_tasks = registry
    existing = registry.get(thread_id)
    if existing is not None and not existing.done():
        return existing

    worker_started_at = time.perf_counter()
    task = asyncio.create_task(
        _surface_pending_subagent_reports(thread_id),
        name=f"pending-subagent-reports:{thread_id}",
    )
    registry[thread_id] = task

    def _complete(done: asyncio.Task[tuple[int, int]]) -> None:
        if registry.get(thread_id) is done:
            registry.pop(thread_id, None)
        if done.cancelled():
            _logger.debug(
                "pending subagent report worker cancelled thread_id=%s turn_id=%s",
                thread_id,
                turn_id,
            )
            return
        try:
            pending_count, injected_count = done.result()
        except Exception:  # noqa: BLE001 — diagnostics cannot break task cleanup
            _logger.debug("pending subagent report worker failed", exc_info=True)
            return
        _logger.info(
            "realtime pending reports worker completed thread_id=%s turn_id=%s "
            "pending_count=%d injected_count=%d worker_ms=%.3f",
            thread_id,
            turn_id,
            pending_count,
            injected_count,
            (time.perf_counter() - worker_started_at) * 1000,
        )

    task.add_done_callback(_complete)
    return task


async def _start_turn(
    runtime: CerebrumRuntime,
    params: dict[str, Any],
    emitter: EventEmitter,
) -> Turn:
    """Start and drive one realtime turn through its terminal state."""
    # ── PHASE 1 · validation + slash/topology/model routing ────────
    validated = TurnParams.model_validate(params)
    thread_id = runtime._require_thread_id(validated.thread_id)
    # dsh ``agent/inbox/claimed``: a HUMAN-initiated turn refills every
    # subagent wake budget owned by this thread.  The actual store call is
    # deliberately deferred until after ``turn_started`` + the user anchor:
    # it shares the durable-session lock with cold disk discovery and must not
    # hold the visible Send acknowledgement hostage.  An auto-woken parent
    # still never refills the budget.
    _input_meta = _input_metadata(validated)
    _auto_wake = bool(
        isinstance(_input_meta.get("context"), dict)
        and bool(_input_meta["context"].get("auto_wake"))
    )
    text = _join_text(validated.input)
    stripped_text, marker_mode = _extract_codex_composer_mode(text)
    if marker_mode is not None:
        text = stripped_text
        patched_input: list[dict[str, Any]] = []
        marker_applied = False
        for block in validated.input:
            if (
                not marker_applied
                and isinstance(block, dict)
                and block.get("type") == "text"
                and isinstance(block.get("text"), str)
            ):
                next_block = dict(block)
                next_block["text"] = stripped_text
                metadata = dict(next_block.get("metadata") or {})
                context = dict(metadata.get("context") or {})
                context.setdefault("workflow_mode", marker_mode)
                context.setdefault("completion_policy", marker_mode)
                context.setdefault("mode_preset", f"{marker_mode}.mode")
                context.setdefault("workflow_preset", f"{marker_mode}.mode")
                if marker_mode == "goal":
                    context.setdefault("goal_mode", True)
                metadata["context"] = context
                next_block["metadata"] = metadata
                patched_input.append(next_block)
                marker_applied = True
                continue
            patched_input.append(block)
        validated = validated.model_copy(
            update={
                "input": patched_input,
                **({"planning_mode": True} if marker_mode in {"plan", "spec"} else {}),
            },
        )
    if text:
        from runtime.sensing.gateway.slash_command_expansion import (
            maybe_expand_slash_command,
        )

        text = maybe_expand_slash_command(text)
    if _should_default_planning_mode(text, validated):
        validated = validated.model_copy(update={"planning_mode": True})
    # Auto-dispatch to a built-in topology when the user message
    # clearly matches one of the multi-agent categories. Single-
    # agent ReAct stays the default; this only fires for
    # "调研 / 代码评审 / 重构 / 调试"-shaped messages without
    # an explicit topology_id.
    _auto_topology = _should_default_topology(text, validated)
    if _auto_topology is not None:
        validated = validated.model_copy(update={"topology_id": _auto_topology})
        _logger.info(
            "auto-dispatch to topology %r based on user message",
            _auto_topology,
        )

    # Smart model routing — auto-route trivial / simple turns to
    # the cheap tier. Complex / research / topology / code-mode
    # turns stay on the user's primary. Explicit ``model`` pins
    # bypass this entirely.
    try:
        from runtime.core.cerebrum.todo_protocol import (
            should_require_todo_protocol,
        )
        from runtime.core.cerebrum.turn_complexity import (
            estimate_turn_complexity,
            select_model_for_complexity,
        )
        from runtime.sensing.gateway.realtime_turn_routing import (
            looks_like_tool_intent,
        )

        _meta = _input_metadata(validated)
        _user_ctx_for_complexity = (
            _meta.get("context") if isinstance(_meta.get("context"), dict) else _meta
        )
        _mode_str = (
            _user_ctx_for_complexity.get("mode")
            if isinstance(_user_ctx_for_complexity, dict)
            else None
        ) or ""
        _capability_mode_str = (
            _user_ctx_for_complexity.get("capability_mode")
            if isinstance(_user_ctx_for_complexity, dict)
            else None
        ) or ""
        _external_model_owner = (
            str(_user_ctx_for_complexity.get("execution_engine") or "").strip().lower()
            if isinstance(_user_ctx_for_complexity, dict)
            else ""
        ) == "codex"
        _is_code_mode_for_routing = bool(_mode_str == "code" or _capability_mode_str)
        _verdict = estimate_turn_complexity(
            text,
            has_explicit_model=bool(
                "model" in getattr(validated, "model_fields_set", set()) and validated.model
            ),
            has_topology=bool(getattr(validated, "topology_id", None)),
            is_code_mode=_is_code_mode_for_routing,
            is_swarm_mode=str(_mode_str).lower() in {"swarm", "swarms"},
            is_research_mode=str(_mode_str).lower() in {"deep", "deep_research", "research"},
            is_goal_mode=bool(getattr(validated, "planning_mode", False)),
            looks_tool_intent=looks_like_tool_intent(text),
            requires_todo_protocol=should_require_todo_protocol(
                text,
                _user_ctx_for_complexity,
            ),
        )
        # AI mode override (Marvis-style efficiency / privacy).
        # Privacy mode pins every turn to ``local`` regardless of
        # complexity so no data leaves the box. Efficiency is a
        # pass-through.
        try:
            from runtime.core.cerebrum.ai_mode import apply_ai_mode_override

            _verdict = apply_ai_mode_override(_verdict)
        except ImportError:  # noqa: BLE001 — ai mode is optional
            pass
        if _external_model_owner:
            # Codex owns its effective model through the principal-scoped
            # model profile. Realtime still estimates complexity for its own
            # lifecycle policy, but must not overwrite or report a model that
            # will never execute.
            _routed_model, _route_reason = None, "external_engine:codex"
        else:
            _routed_model, _route_reason = select_model_for_complexity(
                _verdict,
                user_model=validated.model,
                is_code_mode=_is_code_mode_for_routing,
            )
        if _routed_model:
            validated = validated.model_copy(update={"model": _routed_model})
            _logger.info(
                "smart routing: %s → %s (%s)",
                text[:60].replace("\n", " "),
                _routed_model,
                _route_reason,
            )
    except Exception as exc:  # noqa: BLE001 — smart routing is best-effort; never block a turn
        _logger.debug("smart routing skipped: %s", exc, exc_info=True)

    # ── PHASE 2 · thread setup + turn registration ─────────────────
    # Sweep any background command watchers left running from a
    # previous turn on this thread. They're allowed to outlive
    # their own turn (long shells finishing after the LLM said
    # done) but mustn't bleed into the brand-new conversation
    # the user just started.
    with contextlib.suppress(Exception):
        await runtime._reap_stale_background_tasks(thread_id)

    log = await runtime._ensure_thread(thread_id, emitter)
    runtime._require_thread_owner(
        log,
        getattr(emitter, "actor_id", None),
        access="write",
    )

    turn = Turn(thread_id=thread_id, params=validated)
    turn_created_at = time.perf_counter()
    # Every turn has an objective coordinate from its first emitted snapshot.
    # ReAct replaces this provisional id with its durable task id as soon as
    # ``react_started`` arrives; direct/reflection turns keep the turn id.
    turn.objective_id = turn.id
    # Bound before the try so the escape handler at the bottom can
    # attach the intent when the crash happens after PHASE 4 built it
    # (and pass None for earlier failures — both recorders accept it).
    intent = None
    # Register the turn id with the connection's interrupt
    # registry before emitting turn/started. This closes the race
    # where a client's turn/interrupt (matched by id, not sequence)
    # arrives before our first poll.
    emitter.register_turn(turn.id)
    emitter_registered_at = time.perf_counter()
    runtime._register_active_turn(turn, log)
    active_turn_registered_at = time.perf_counter()
    try:
        evt = log.turn_started(thread_id, turn)
        turn_started_persisted_at = time.perf_counter()
        runtime._active_turn_ids.add(turn.id)
        await emitter.notify(
            ServerMethod.TURN_STARTED,
            {
                "threadId": thread_id,
                "turn": turn.model_dump(by_alias=True, mode="json"),
                "eventId": evt.event_id,
            },
        )
        turn_started_visible_at = time.perf_counter()
        _logger.info(
            "realtime turn startup timing thread_id=%s turn_id=%s "
            "emitter_register_ms=%.3f active_register_ms=%.3f "
            "turn_started_persist_ms=%.3f turn_started_notify_ms=%.3f "
            "created_to_turn_started_ms=%.3f created_to_visible_ms=%.3f",
            thread_id,
            turn.id,
            (emitter_registered_at - turn_created_at) * 1000,
            (active_turn_registered_at - emitter_registered_at) * 1000,
            (turn_started_persisted_at - active_turn_registered_at) * 1000,
            (turn_started_visible_at - turn_started_persisted_at) * 1000,
            (turn_started_persisted_at - turn_created_at) * 1000,
            (turn_started_visible_at - turn_created_at) * 1000,
        )
        runtime._record_task_run_started(turn, text=text, params=validated)

        # ── PHASE 3 · prompt hooks + user message anchor ───────────
        from runtime.platform.process.session import current_session
        from runtime.safety.hooks.runner import (
            dispatch_session_start,
            dispatch_user_prompt,
        )

        # dsh ``SessionStart``: fired once per bound turn session, before
        # the prompt hook, so per-user context hooks load first. Best-effort
        # by contract — a failing hook degrades to pass_through and never
        # raises into the turn.
        dispatch_session_start(thread_id=thread_id, session=current_session())

        prompt_decision = dispatch_user_prompt(
            prompt_text=text,
            thread_id=thread_id,
            session=current_session(),
        )
        if prompt_decision.cancelled:
            err = ErrorItem(message=prompt_decision.reason or "prompt rejected")
            turn.items.append(err)
            await runtime._emit_item_started(turn, log, emitter, err)
            err.status = ItemStatus.FAILED
            await runtime._emit_item_completed(turn, log, emitter, err)
            turn.status = TurnStatus.FAILED
            _close_turn(log, thread_id, turn)
            # ``intent`` isn't built yet on the prompt-rejected path;
            # the snapshot helper accepts None so the legacy thread
            # store still records the failed turn for the sidebar.
            runtime._record_failed_turn_proposal(
                turn,
                intent=None,
                failure_source="prompt_rejected",
            )
            runtime._snapshot_to_thread_store(thread_id, log, None)
            return turn
        if prompt_decision.modified_prompt is not None:
            text = prompt_decision.modified_prompt
        if not text:
            err = ErrorItem(message="empty input")
            turn.items.append(err)
            await runtime._emit_item_started(turn, log, emitter, err)
            await runtime._emit_item_completed(turn, log, emitter, err)
            turn.status = TurnStatus.FAILED
            _close_turn(log, thread_id, turn)
            runtime._record_failed_turn_proposal(
                turn,
                intent=None,
                failure_source="empty_input",
            )
            return turn

        # ── PHASE 3.5 · Echo session-reference mention resolution ──
        # Resolve @session: / @subagent: / canonical echo-session: mentions
        # (plus historical dsh-session: references)
        # into a read-only referenced-sessions frame. The frame is injected
        # into the model's user context (never the sidebar text); any
        # failure degrades to the raw prompt.
        text, session_reference_frame = _resolve_session_reference_mentions(
            text,
            thread_id,
        )

        # Record the user's message as a first-class turn item so
        # ``_flatten_turns_to_messages`` and the realtime adapter
        # both see a HumanMessage anchor. Without this the sidebar
        # title falls back to empty and the chat history starts
        # with the AI's reply only.
        user_item = None
        user_message_visible_at: float | None = None
        try:
            from runtime.protocol import UserMessageItem

            attachments = _input_attachments(validated.input)
            if validated.user_item_id is None:
                user_item = UserMessageItem(text=text, attachments=attachments)
            else:
                user_item = UserMessageItem(
                    id=validated.user_item_id,
                    text=text,
                    attachments=attachments,
                )
            turn.items.append(user_item)
            await runtime._emit_item_started(turn, log, emitter, user_item)
            user_item.status = ItemStatus.COMPLETED
            await runtime._emit_item_completed(turn, log, emitter, user_item)
            user_message_visible_at = time.perf_counter()
            _logger.info(
                "realtime user message timing thread_id=%s turn_id=%s item_id=%s "
                "turn_started_to_user_visible_ms=%.3f created_to_user_visible_ms=%.3f",
                thread_id,
                turn.id,
                user_item.id,
                (user_message_visible_at - turn_started_visible_at) * 1000,
                (user_message_visible_at - turn_created_at) * 1000,
            )
        except Exception:  # noqa: BLE001
            # Non-fatal: react loop still runs without the anchor.
            _logger.debug("user-message anchor skipped", exc_info=True)

        # ``turn/interrupt`` may race the prompt hooks or user-item emission.
        # Keep the user's message as the durable conversation anchor, but do
        # not start report discovery, model routing, or any tool-capable driver
        # after the Stop has already been acknowledged.
        if _finish_startup_interrupt(
            runtime,
            turn,
            log,
            emitter,
            intent=None,
        ):
            return turn

        # dsh inject-at-next-wake: claim every parked report owned by this
        # thread only after the user's Send is durably visible.  The worker
        # hop also keeps a cold all-session scan from blocking the realtime
        # event loop.  Warm scans still inject before history replay; a scan
        # that exceeds the budget keeps running and reaches the live steering
        # queue at the next safe model/tool boundary.  If the turn has already
        # ended, injection returns false and the worker deliberately does not
        # ack, leaving the durable report for the next turn.
        pending_reports_started_at = time.perf_counter()
        if not _auto_wake:
            _schedule_subagent_wake_budget_refill(runtime, thread_id)
        pending_reports_task = _schedule_pending_subagent_reports(
            runtime,
            thread_id=thread_id,
            turn_id=turn.id,
        )
        reports_deadline = asyncio.get_running_loop().time() + _PENDING_REPORT_STARTUP_BUDGET_S
        reports_ready = pending_reports_task.done()
        while not reports_ready and not emitter.is_turn_interrupted(turn.id):
            remaining = reports_deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            completed_report_tasks, _ = await asyncio.wait(
                {pending_reports_task},
                timeout=min(_STARTUP_INTERRUPT_POLL_S, remaining),
            )
            reports_ready = pending_reports_task in completed_report_tasks
        pending_count: int | None = None
        injected_count: int | None = None
        if reports_ready:
            pending_count, injected_count = pending_reports_task.result()
        pending_reports_finished_at = time.perf_counter()
        _logger.info(
            "realtime pending reports timing thread_id=%s turn_id=%s "
            "pending_count=%s injected_count=%s deferred=%s "
            "pending_reports_ms=%.3f user_visible_to_reports_ready_ms=%.3f",
            thread_id,
            turn.id,
            pending_count if pending_count is not None else "pending",
            injected_count if injected_count is not None else "pending",
            str(not reports_ready).lower(),
            (pending_reports_finished_at - pending_reports_started_at) * 1000,
            (pending_reports_finished_at - (user_message_visible_at or turn_started_visible_at))
            * 1000,
        )
        if _finish_startup_interrupt(
            runtime,
            turn,
            log,
            emitter,
            intent=None,
        ):
            return turn

        # ── PHASE 4 · intent build + resume check ──────────────────
        conversation_messages: list[dict[str, Any]] = []
        with contextlib.suppress(Exception):
            conversation_messages = _conversation_messages_for_react(log.replay())

        intent = _build_intent(
            text,
            validated,
            workspaces=runtime._workspaces,
            thread_store=runtime._thread_store,
            allow_client_auto_approve=runtime._allow_client_auto_approve,
            allow_local_workspace_access=runtime._allow_local_workspace_access,
            conversation_messages=conversation_messages,
        )
        resolved_execution_workspace = intent.user_context.get("cwd")
        turn.execution_workspace_path = (
            resolved_execution_workspace.strip()
            if isinstance(resolved_execution_workspace, str)
            and resolved_execution_workspace.strip()
            else None
        )
        # Make a newly-created thread queryable as soon as its authenticated
        # user anchor and execution context exist. Previously the derived HTTP
        # thread store was written only at terminal completion, so workbench
        # and sidebar queries saw several seconds of transient 404s while the
        # live turn was already streaming successfully.
        runtime._snapshot_to_thread_store(thread_id, log, intent)
        if session_reference_frame is not None:
            intent = intent.model_copy(
                update={
                    "user_context": {
                        **intent.user_context,
                        "session_reference_context": session_reference_frame,
                    }
                }
            )
        _inject_cowork_turn_plan(
            runtime,
            thread_id=thread_id,
            text=text,
            intent=intent,
        )
        if user_item is not None:
            _persist_cowork_user_message(
                runtime,
                thread_id=thread_id,
                text=text,
                item_id=user_item.id,
                actor_id=getattr(emitter, "actor_id", None),
                intent=intent,
            )
        explicit_project_command = _is_project_os_command(text)
        if (intent.user_context or {}).get(
            "cowork_waiting_for_mention"
        ) and not explicit_project_command:
            # A durable chat room accepts ordinary human conversation without
            # manufacturing an assistant response.  Close this as a successful
            # user-only turn before approval/model routing; the canonical room
            # mirror above keeps Project actions and reconnect replay available.
            runtime._set_turn_steering_accepting(turn, False)
            turn.status = TurnStatus.COMPLETED
            turn.outcome_reason = "cowork_waiting_for_mention"
            log.turn_updated(
                thread_id,
                turn.id,
                objective_id=turn.objective_id,
                task_id=turn.task_id,
                outcome_reason=turn.outcome_reason,
            )
            _close_turn(log, thread_id, turn)
            runtime._snapshot_to_thread_store(thread_id, log, intent)
            return turn
        confirmed_resume_intent = await runtime._consume_confirmed_resume_intent(thread_id, text)
        if confirmed_resume_intent is None:
            confirmed_resume_intent = await runtime._consume_paused_task_resume_intent(
                thread_id,
                text,
            )
        if confirmed_resume_intent is not None:
            intent.user_context["resume_intent"] = confirmed_resume_intent
        else:
            # Preserve enough durable context for status probes/amendments
            # without restoring raw model messages.  This prevents a paused
            # task followed by "?" or "怎么了" from becoming a context-free
            # greeting while keeping a new objective isolated from old drafts.
            from runtime.core.cerebrum.pause_control import get_pause_controller

            paused_requests = sorted(
                (
                    request
                    for request in get_pause_controller().list_paused()
                    if request.thread_id == thread_id
                ),
                key=lambda request: request.requested_at,
                reverse=True,
            )[:5]
            paused_contexts: list[dict[str, Any]] = []
            for paused_request in paused_requests:
                checkpoint = _resume_checkpoint_metadata(runtime, paused_request.task_id) or {}
                paused_contexts.append(
                    {
                        "task_id": paused_request.task_id,
                        "objective_id": paused_request.task_id,
                        "reason": paused_request.reason,
                        "note": paused_request.note,
                        "iteration": checkpoint.get("iteration", 0),
                        "phase": checkpoint.get("phase", ""),
                        "working_set": checkpoint.get("working_set", []),
                        "checkpoint_id": checkpoint.get("checkpoint_id", 0),
                        "resumable": True,
                    }
                )
            if paused_contexts:
                intent.user_context["paused_tasks_context"] = paused_contexts
                if len(paused_contexts) == 1:
                    intent.user_context["paused_task_context"] = paused_contexts[0]
        resume_intent = intent.user_context.get("resume_intent")
        if isinstance(resume_intent, dict) and resume_intent.get("requires_confirmation") is True:
            await runtime._record_pending_resume_intent(thread_id, resume_intent)
            await runtime._emit_agent_message(
                turn,
                log,
                emitter,
                _resume_confirmation_text(resume_intent),
            )
            turn.status = TurnStatus.COMPLETED
            _close_turn(log, thread_id, turn)
            await runtime._maybe_compact(thread_id, log, emitter)
            runtime._snapshot_to_thread_store(thread_id, log, intent)
            return turn

        # ── PHASE 5 · execution dispatch (topology/fast/react) ─────
        loop = asyncio.get_running_loop()
        gateway_provider = GatewayApprovalProvider(
            emitter,
            loop,
            thread_id=thread_id,
            turn_id=turn.id,
            trace_store=runtime._trace_store,
        )
        provider: ApprovalProvider = runtime._wrap_with_policy(gateway_provider)
        agent = runtime._resolve_agent(validated)
        agent = _resolve_cowork_responder_agent(
            runtime,
            intent=intent,
            fallback=agent,
        )
        # Agent resolution and resume plumbing can yield to async stores. A
        # Stop accepted during that window still belongs to startup and must
        # close before dispatch selects any model/tool-capable driver.
        if _finish_startup_interrupt(
            runtime,
            turn,
            log,
            emitter,
            intent=intent,
        ):
            return turn
        turn_driver = "react"

        try:
            topology_id = getattr(validated, "topology_id", None)
            # Mode-level guard: single-agent modes MUST NOT route
            # through ``_drive_team_topology`` even if a leftover
            # ``topology_id`` slipped through (e.g. settings
            # persisted from a prior swarm turn, an old front-end
            # build, or ``auto-dispatch`` on a stale runtime). The
            # explicit user-facing mode is the source of truth:
            #   chat / react / deep  → single-agent ReAct
            #   swarm                → swarm topology
            # Anything that lands in the first bucket here gets
            # its topology cleared so the swarm path stays
            # unreachable from the Agent / Inspiration modes.
            _mode_str = (_turn_mode(validated) or "").lower()
            if topology_id and _mode_str in {"chat", "react", "deep"}:
                _logger.info(
                    "ignoring topology_id %r in single-agent mode %r",
                    topology_id,
                    _mode_str,
                )
                topology_id = None
                validated = validated.model_copy(
                    update={"topology_id": None},
                )

            # 能力包 / Meta-Skill soft hand-off: if the user's text
            # strongly matches one of the curated workflow packs,
            # surface a hint so the user can switch to the catalog
            # page. ReAct still runs — the hint is informational,
            # not a redirect, until the graph runtime is wired
            # through the realtime gateway.
            try:
                from runtime.memory.skills_lib.meta_skill import match_meta_skill

                _matched = match_meta_skill(text)
            except Exception:  # noqa: BLE001
                _logger.debug("meta-skill match failed", exc_info=True)
                _matched = None
            if _matched is not None:
                await emitter.notify(
                    ServerMethod.TURN_META_SKILL_HINT,
                    {
                        "threadId": thread_id,
                        "turnId": turn.id,
                        "name": _matched.name,
                        "description": _matched.description,
                        "kind": _matched.kind,
                        "affinity": list(_matched.affinity),
                        "stepCount": len(_matched.steps),
                    },
                )

            if explicit_project_command:
                # Project is a capability attached to the thread, not a fourth
                # response strategy. Only an explicit command enters Project
                # OS; ordinary group messages follow chat/cluster/swarm above.
                turn_driver = "project_os"
                await runtime._drive_project_os(
                    turn,
                    log,
                    emitter,
                    intent,
                    thread_id=thread_id,
                    text=text,
                )
            elif str((intent.user_context or {}).get("serve_mesh") or "").strip() == "1" or (
                bool((intent.user_context or {}).get("cowork_is_multi"))
                and len((intent.user_context or {}).get("cowork_responders") or []) > 1
            ):
                # 蜂群 / 冒泡: the user picked the leaderless group mode. Fan the
                # message out to every member agent in parallel — each chimes in
                # with its own persona bubble ("boss speaks, everyone replies").
                # No topology_id needed; degrades to single-agent if <2 members.
                turn_driver = "group_fanout"
                await runtime._drive_group_fanout(
                    turn,
                    log,
                    emitter,
                    intent,
                    text=text,
                )
            elif topology_id:
                # Explicit topology / 集群: orchestrated team — _drive_swarm_mesh
                # auto-picks the boids/SignalBus parallel mesh vs the sequential
                # TeamRunner by the planned graph's shape.
                # An explicit per-turn model must govern the whole team, not
                # only the parent turn.  TeamRunner passes ``model_name`` to
                # every ephemeral role; leaving it absent silently falls back
                # to role defaults/cheap models that may use a different,
                # unavailable provider.  Auto/default selections retain the
                # topology's normal heterogeneous routing.
                _team_model = str(getattr(validated, "model", None) or "").strip()
                if _team_model and _team_model.lower() not in {"auto", "default"}:
                    intent = intent.model_copy(
                        update={
                            "user_context": {
                                **(intent.user_context or {}),
                                "model_name": _team_model,
                            }
                        }
                    )
                turn_driver = "swarm_mesh"
                await runtime._drive_swarm_mesh(
                    turn,
                    log,
                    emitter,
                    intent,
                    text=text,
                    topology_id=topology_id,
                )
            elif runtime._is_codex_app_server_partner(agent):
                # Group/topology routing wins first: selecting Coder as one
                # roster member must not turn the whole room into a single
                # Coder turn. Concrete member dispatch still reaches this same
                # backend through the persistent standard-role runner.
                turn_driver = "codex_app_server"
                await runtime._drive_codex_app_server(
                    turn,
                    log,
                    emitter,
                    intent,
                    agent,
                    provider,
                    text=text,
                )
            elif runtime._should_use_reflection_fast_path(
                text,
                validated,
                conversation_messages=cast("list[dict[str, object]] | None", conversation_messages),
                thread_id=thread_id,
            ):
                turn_driver = "reflection_fast_path"
                await runtime._drive_reflection_fast_path(
                    turn,
                    log,
                    emitter,
                    intent,
                    agent,
                    model=validated.model,
                )
            else:
                turn_driver = "react"
                await runtime._drive_react(
                    turn,
                    log,
                    emitter,
                    intent,
                    provider,
                    agent,
                    model=validated.model,
                )
        except Exception as exc:
            _logger.exception("CerebrumRuntime: turn driver crashed: %s", turn_driver)
            turn.execution_engine = "codex" if turn_driver == "codex_app_server" else "echo"
            context = intent.user_context if isinstance(intent.user_context, dict) else {}
            err = ErrorItem(
                message=str(exc) or exc.__class__.__name__,
                error_info={
                    "code": "turn_driver_exception",
                    "driver": turn_driver,
                    "exception_type": exc.__class__.__name__,
                    "cowork_mode": context.get("cowork_mode"),
                    "topology_id": topology_id or "",
                },
            )
            turn.items.append(err)
            await runtime._emit_item_started(turn, log, emitter, err)
            await runtime._emit_item_completed(turn, log, emitter, err)
            turn.status = TurnStatus.FAILED
            _close_turn(log, thread_id, turn)
            runtime._record_failed_turn_proposal(
                turn,
                intent=intent,
                failure_source=f"{turn_driver}_exception",
            )
            runtime._snapshot_to_thread_store(thread_id, log, intent)
            return turn

        turn.execution_engine = "codex" if turn_driver == "codex_app_server" else "echo"

        # Native tool turns consume steering between model rounds. Other
        # drivers (reflection, topology, Project OS) may finish one atomic
        # pass without such a boundary; hand any message that arrived during
        # that pass to the normal agent loop before finalizing the same turn.
        while turn.status not in {
            TurnStatus.PAUSED,
            TurnStatus.CANCELLED,
            TurnStatus.INTERRUPTED,
            TurnStatus.FAILED,
        }:
            # Close intake before the last durable drain. Any steering RPC
            # acknowledged before this lease update is already in the log and
            # must be consumed; any later RPC is rejected instead of being
            # accepted after the final answer can no longer change.
            runtime._set_turn_steering_accepting(turn, False)
            late_steering = runtime._drain_turn_steering(turn.id)
            if not late_steering:
                break
            runtime._set_turn_steering_accepting(turn, True)
            correction = "\n\n".join(late_steering)
            steering_context = dict(intent.user_context or {})
            steering_context["live_steering"] = True
            steering_intent = intent.model_copy(
                update={
                    "raw": correction,
                    "normalized_goal": correction,
                    "user_context": steering_context,
                }
            )
            if turn_driver == "codex_app_server":
                # The App Server driver consumes most steering live.  A
                # message that races its terminal event is still continued on
                # the same durable inner Codex thread, never switched to a
                # second planner against the same workspace.
                await runtime._drive_codex_app_server(
                    turn,
                    log,
                    emitter,
                    steering_intent,
                    agent,
                    provider,
                    text=correction,
                )
            else:
                turn_driver = "react"
                turn.execution_engine = "echo"
                await runtime._drive_react(
                    turn,
                    log,
                    emitter,
                    steering_intent,
                    provider,
                    agent,
                    model=validated.model,
                )

        # ── PHASE 6 · status finalization + snapshot ───────────────
        if turn.status in {
            TurnStatus.PAUSED,
            TurnStatus.CANCELLED,
            TurnStatus.INTERRUPTED,
        }:
            # Preserve the concrete terminal/waiting outcome.  In particular,
            # a resumable checkpoint must never be flattened back to completed
            # or to a generic transport interruption.
            _close_turn(log, thread_id, turn)
            runtime._snapshot_to_thread_store(thread_id, log, intent)
            return turn
        if turn.status == TurnStatus.FAILED:
            _close_turn(log, thread_id, turn)
            # A "blocked_on_user" disposition is a genuine hand-off, not a
            # failure — do not feed it to the failure-sampling evolution
            # ledger as a turn_failure sample.
            _turn_error = turn.error if isinstance(turn.error, dict) else None
            if not (_turn_error and _turn_error.get("disposition") == "blocked_on_user"):
                runtime._record_failed_turn_proposal(
                    turn,
                    intent=intent,
                    failure_source="react_failed",
                )
            runtime._snapshot_to_thread_store(thread_id, log, intent)
            return turn

        repair_attempt = 0
        requested_sandbox = params.get("sandboxPolicy")
        repair_limit = (
            2
            if turn_driver == "react"
            and isinstance(requested_sandbox, dict)
            and str(requested_sandbox.get("type") or "") == "workspaceWrite"
            and isinstance(validated.sandbox_policy, dict)
            and str(validated.sandbox_policy.get("type") or "") == "workspaceWrite"
            else 0
        )
        if _turn_has_failed_code_verification(turn):
            failed_items = [
                item
                for item in turn.items
                if isinstance(item, VerificationItem) and item.status == ItemStatus.FAILED
            ]
            # ── Environment-blocked degrade ──────────────────────
            # All failed verification items are environment-blocked
            # (missing tool / no network): don't burn repair attempts or
            # hard-fail the turn on an environment problem. Surface a
            # manual-confirmation item and complete.
            if not repair_limit and _turn_verification_environment_blocked(turn):
                degrade_item = VerificationItem(
                    command="manual verification required (environment blocked)",
                    kind="manual",
                    status=ItemStatus.COMPLETED,
                    exit_code=None,
                    summary=(
                        "代码已修改，但验证命令因环境受限未能执行 "
                        "(工具缺失/网络不可用)。请人工运行下列推荐命令确认后继续。"
                    ),
                    stdout_tail=_verification_plan_stdout_tail(
                        _verification_plan_for_code_paths(_code_change_paths(turn), intent)
                    ),
                    stderr_tail=None,
                    related_files=_code_change_paths(turn),
                    related_change_item_ids=_file_change_item_ids(turn),
                )
                turn.items.append(degrade_item)
                await runtime._emit_item_started(turn, log, emitter, degrade_item)
                await runtime._emit_item_completed(turn, log, emitter, degrade_item)
                turn.status = TurnStatus.COMPLETED
                _close_turn(log, thread_id, turn)
                runtime._snapshot_to_thread_store(thread_id, log, intent)
                return turn
            if repair_limit:
                from runtime.safety.evolution.auto_verifier import (
                    build_verification_repair_request,
                )

                verification_plan = _verification_plan_for_code_paths(
                    _code_change_paths(turn),
                    intent,
                )
                repair_attempt = 1
                repair_request = build_verification_repair_request(
                    verification_plan,
                    failed_items,
                    attempt=repair_attempt,
                    max_attempts=repair_limit,
                )
                with contextlib.suppress(Exception):
                    from runtime.safety.evolution.auto_verifier_metrics import (
                        record_auto_verifier_repair_attempt,
                    )

                    record_auto_verifier_repair_attempt(
                        attempt=repair_attempt,
                        max_attempts=repair_limit,
                        status="requested",
                        failed_commands=[item.command for item in failed_items],
                    )
                repair_context = dict(intent.user_context or {})
                repair_context["verification_repair"] = repair_request
                repair_intent = intent.model_copy(
                    update={
                        "raw": repair_request["prompt"],
                        "normalized_goal": repair_request["prompt"],
                        "user_context": repair_context,
                    }
                )
                await runtime._drive_react(
                    turn,
                    log,
                    emitter,
                    repair_intent,
                    provider,
                    agent,
                    model=validated.model,
                )
                if turn.status == TurnStatus.INTERRUPTED:
                    _close_turn(log, thread_id, turn)
                    runtime._snapshot_to_thread_store(thread_id, log, intent)
                    return turn
                if turn.status == TurnStatus.FAILED:
                    _close_turn(log, thread_id, turn)
                    runtime._record_failed_turn_proposal(
                        turn,
                        intent=intent,
                        failure_source="verification_repair_failed",
                    )
                    runtime._snapshot_to_thread_store(thread_id, log, intent)
                    return turn
            else:
                turn.status = TurnStatus.FAILED
                _close_turn(log, thread_id, turn)
                runtime._record_failed_turn_proposal(
                    turn,
                    intent=intent,
                    failure_source="verification_failed",
                )
                runtime._snapshot_to_thread_store(thread_id, log, intent)
                return turn

        if _turn_has_unverified_code_changes(turn):
            # ── Await in-flight background verification ─────────────
            # The model may have handed verification to a background task
            # (background_exec) that is still running when the loop
            # returned. Failing with "verification required" while those
            # tasks are in flight would blame the model for work that is
            # genuinely still finishing. Wait a bounded window for their
            # outcome; if one is still running afterwards, close the turn
            # as completed-with-background (the verifier keeps running)
            # instead of a spurious verification-required failure.
            pending_bg_tasks = [
                task
                for task in runtime._thread_background_tasks.get(thread_id, [])
                if not task.done()
            ]
            # Only bypass the gate when a pending task is plausibly the
            # delegated verification itself. An unrelated watcher / dev
            # server / poller still running is not a reason to silently
            # green unverified code — in that case fall through to the
            # normal verification gate below.
            if any(_background_task_is_verification(task.get_name()) for task in pending_bg_tasks):
                await asyncio.wait(
                    pending_bg_tasks,
                    timeout=_BACKGROUND_VERIFY_WAIT_S,
                )
                if any(not task.done() for task in pending_bg_tasks):
                    turn.status = TurnStatus.COMPLETED
                    turn.outcome_reason = "completed_with_background"
                    _close_turn(log, thread_id, turn)
                    runtime._snapshot_to_thread_store(thread_id, log, intent)
                    return turn

            code_change_paths = _code_change_paths(turn)
            verification_plan = _verification_plan_for_code_paths(
                code_change_paths,
                intent,
            )
            auto_items: list[VerificationItem] = []
            while True:
                try:
                    from runtime.safety.evolution.auto_verifier import (
                        build_verification_repair_request,
                        run_verification_plan,
                    )

                    auto_items = run_verification_plan(
                        verification_plan,
                        sandbox_policy=validated.sandbox_policy,
                    )
                except Exception:  # noqa: BLE001 - auto verification is best-effort
                    auto_items = []
                for auto_item in auto_items:
                    turn.items.append(auto_item)
                    await runtime._emit_item_started(turn, log, emitter, auto_item)
                    await runtime._emit_item_completed(turn, log, emitter, auto_item)
                if repair_attempt:
                    with contextlib.suppress(Exception):
                        from runtime.safety.evolution.auto_verifier_metrics import (
                            record_auto_verifier_repair_attempt,
                        )

                        record_auto_verifier_repair_attempt(
                            attempt=repair_attempt,
                            max_attempts=repair_limit,
                            status=(
                                "passed"
                                if auto_items
                                and all(item.status == ItemStatus.COMPLETED for item in auto_items)
                                else "failed"
                                if auto_items
                                else "no_evidence"
                            ),
                            failed_commands=[],
                            fresh_evidence_commands=[item.command for item in auto_items],
                        )
                if all(item.status == ItemStatus.COMPLETED for item in auto_items):
                    if not auto_items:
                        break
                    turn.status = TurnStatus.COMPLETED
                    _close_turn(log, thread_id, turn)
                    runtime._record_successful_turn_example(turn, intent=intent)
                    await runtime._maybe_compact(thread_id, log, emitter)
                    runtime._snapshot_to_thread_store(thread_id, log, intent)
                    return turn
                if not auto_items or repair_attempt >= repair_limit:
                    break

                repair_attempt += 1
                repair_request = build_verification_repair_request(
                    verification_plan,
                    auto_items,
                    attempt=repair_attempt,
                    max_attempts=repair_limit,
                )
                with contextlib.suppress(Exception):
                    from runtime.safety.evolution.auto_verifier_metrics import (
                        record_auto_verifier_repair_attempt,
                    )

                    record_auto_verifier_repair_attempt(
                        attempt=repair_attempt,
                        max_attempts=repair_limit,
                        status="requested",
                        failed_commands=[item.command for item in auto_items],
                    )
                repair_context = dict(intent.user_context or {})
                repair_context["verification_repair"] = repair_request
                repair_intent = intent.model_copy(
                    update={
                        "raw": repair_request["prompt"],
                        "normalized_goal": repair_request["prompt"],
                        "user_context": repair_context,
                    }
                )
                await runtime._drive_react(
                    turn,
                    log,
                    emitter,
                    repair_intent,
                    provider,
                    agent,
                    model=validated.model,
                )
                if turn.status == TurnStatus.INTERRUPTED:
                    _close_turn(log, thread_id, turn)
                    runtime._snapshot_to_thread_store(thread_id, log, intent)
                    return turn
                verification_plan = _verification_plan_for_code_paths(
                    _code_change_paths(turn),
                    intent,
                )

            if not auto_items:
                # ── Agent-driven verification loop-back ────────────
                # Auto-verification produced no evidence (sandbox didn't
                # allow it, or no allowlisted command fit) and the agent
                # ended without recording a verification step. Instead of
                # hard-ending with a manual "verification required" error,
                # give the agent a bounded round to run the recommended
                # commands itself, fix anything that fails, and report.
                from runtime.safety.evolution.auto_verifier import (
                    build_agent_verification_request,
                )

                agent_verify_attempt = 0
                while agent_verify_attempt < _AGENT_VERIFY_ROUND_LIMIT:
                    if not _turn_has_unverified_code_changes(turn):
                        break
                    agent_verify_attempt += 1
                    verify_request = build_agent_verification_request(
                        verification_plan,
                        attempt=agent_verify_attempt,
                    )
                    verify_context = dict(intent.user_context or {})
                    verify_context["verification_repair"] = verify_request
                    verify_intent = intent.model_copy(
                        update={
                            "raw": verify_request["prompt"],
                            "normalized_goal": verify_request["prompt"],
                            "user_context": verify_context,
                        }
                    )
                    await runtime._drive_react(
                        turn,
                        log,
                        emitter,
                        verify_intent,
                        provider,
                        agent,
                        model=validated.model,
                    )
                    if turn.status in {
                        TurnStatus.INTERRUPTED,
                        TurnStatus.CANCELLED,
                        TurnStatus.PAUSED,
                        TurnStatus.FAILED,
                    }:
                        _close_turn(log, thread_id, turn)
                        runtime._snapshot_to_thread_store(thread_id, log, intent)
                        return turn
                if not _turn_has_unverified_code_changes(turn):
                    turn.status = TurnStatus.COMPLETED
                    _close_turn(log, thread_id, turn)
                    runtime._record_successful_turn_example(turn, intent=intent)
                    await runtime._maybe_compact(thread_id, log, emitter)
                    runtime._snapshot_to_thread_store(thread_id, log, intent)
                    return turn

            if auto_items:
                # ── Environment-blocked degrade ──────────────────────
                # The verifier could not run at all (missing tool /
                # no network / corepack download failure), so a hard
                # failure would blame the code for an environment
                # problem. Degrade to a manual-confirmation turn: the
                # change is preserved, the UI shows the exact command
                # the user must run to close the loop.
                if _turn_verification_environment_blocked(turn):
                    degrade_item = VerificationItem(
                        command="manual verification required (environment blocked)",
                        kind="manual",
                        status=ItemStatus.COMPLETED,
                        exit_code=None,
                        summary=(
                            "代码已修改，但验证命令因环境受限未能执行 "
                            "(工具缺失/网络不可用)。请人工运行下列推荐命令确认后继续。"
                        ),
                        stdout_tail=_verification_plan_stdout_tail(verification_plan),
                        stderr_tail=None,
                        related_files=code_change_paths,
                        related_change_item_ids=_file_change_item_ids(turn),
                    )
                    turn.items.append(degrade_item)
                    await runtime._emit_item_started(turn, log, emitter, degrade_item)
                    await runtime._emit_item_completed(turn, log, emitter, degrade_item)
                    turn.status = TurnStatus.COMPLETED
                    _close_turn(log, thread_id, turn)
                    runtime._snapshot_to_thread_store(thread_id, log, intent)
                    return turn

                turn.status = TurnStatus.FAILED
                _close_turn(log, thread_id, turn)
                runtime._record_failed_turn_proposal(
                    turn,
                    intent=intent,
                    failure_source="verification_failed",
                )
                runtime._snapshot_to_thread_store(thread_id, log, intent)
                return turn

            verification_item = VerificationItem(
                command="verification required",
                kind="manual",
                status=ItemStatus.FAILED,
                exit_code=None,
                summary=(
                    "Code changes were produced but no verification step "
                    "was recorded before final answer."
                ),
                stdout_tail=_verification_plan_stdout_tail(verification_plan),
                stderr_tail=None,
                related_files=code_change_paths,
                related_change_item_ids=_file_change_item_ids(turn),
            )
            turn.items.append(verification_item)
            await runtime._emit_item_started(turn, log, emitter, verification_item)
            await runtime._emit_item_completed(turn, log, emitter, verification_item)
            # ── Environment-blocked degrade ──────────────────────
            # The agent changed code but could not run ANY verifier
            # because the environment itself is broken (missing tool /
            # no network / corepack shim download failure). Keep the
            # change visible and ask the user to confirm manually instead
            # of failing the turn on an environment problem.
            if _turn_verification_environment_blocked(turn):
                turn.status = TurnStatus.COMPLETED
                _close_turn(log, thread_id, turn)
                runtime._snapshot_to_thread_store(thread_id, log, intent)
                return turn
            turn.status = TurnStatus.FAILED
            _close_turn(log, thread_id, turn)
            runtime._record_failed_turn_proposal(
                turn,
                intent=intent,
                failure_source="verification_required",
            )
            runtime._snapshot_to_thread_store(thread_id, log, intent)
            return turn

        if not _turn_has_observable_output(turn):
            err = ErrorItem(
                message=(
                    "模型执行结束但没有返回任何可见输出。请重试，或切换到其他可用模型后再试。"
                ),
                error_info={
                    "code": "empty_model_output",
                    "model": validated.model,
                },
            )
            turn.items.append(err)
            await runtime._emit_item_started(turn, log, emitter, err)
            await runtime._emit_item_completed(turn, log, emitter, err)
            turn.status = TurnStatus.FAILED
            _close_turn(log, thread_id, turn)
            runtime._record_failed_turn_proposal(
                turn,
                intent=intent,
                failure_source="empty_model_output",
            )
            runtime._snapshot_to_thread_store(thread_id, log, intent)
            return turn

        background_tasks = runtime._thread_background_tasks.get(thread_id, [])
        if any(not task.done() for task in background_tasks):
            turn.outcome_reason = "completed_with_background"
            log.turn_updated(
                thread_id,
                turn.id,
                objective_id=turn.objective_id,
                task_id=turn.task_id,
                outcome_reason=turn.outcome_reason,
            )
        turn.status = TurnStatus.COMPLETED
        _close_turn(log, thread_id, turn)
        runtime._record_successful_turn_example(turn, intent=intent)
        await runtime._maybe_compact(thread_id, log, emitter)
        runtime._snapshot_to_thread_store(thread_id, log, intent)
        return turn
    except asyncio.CancelledError:
        # Connection teardown cancels the turn task mid-flight
        # (gateway ``_serve`` finally). CancelledError bypasses both
        # the driver ``except Exception`` above and the gateway's
        # error wrapping, so without this handler the journal keeps an
        # IN_PROGRESS turn that only the next thread/resume can reap
        # and the client never sees a terminal event. Finalize
        # best-effort, then always re-raise — cancellation must
        # propagate.
        if turn.status == TurnStatus.IN_PROGRESS:
            turn.status = TurnStatus.INTERRUPTED
            with contextlib.suppress(Exception):
                _close_turn(log, thread_id, turn)
        if turn.completed_at is None:
            turn.completed_at = now_utc()
        # Record the concrete interrupt reason so the frontend can
        # tell the user *why* the turn stopped, not just *that* it did.
        if not turn.interrupt_reason:
            reason = "连接断开或后端重启"
            with contextlib.suppress(Exception):
                emitter_reason = emitter.get_interrupt_reason(turn.id)
                if emitter_reason:
                    reason = emitter_reason
            turn.interrupt_reason = reason
        # The connection is usually already dead here; send failures
        # must not mask the cancellation. Notify the current connection.
        with contextlib.suppress(Exception):
            await emitter.notify(
                ServerMethod.TURN_INTERRUPTED,
                {
                    "threadId": thread_id,
                    "turnId": turn.id,
                    "reason": turn.interrupt_reason,
                },
            )
        with contextlib.suppress(Exception):
            await emitter.notify(
                ServerMethod.TURN_COMPLETED,
                {
                    "threadId": thread_id,
                    "turn": turn.model_dump(by_alias=True, mode="json"),
                },
            )
        # TODO(P1): Fan out terminal events to sibling connections (same
        # thread_id) matching the normal completion path in
        # realtime_gateway.py:716-720. Without this, sibling tabs see the turn
        # stuck in inProgress until their next thread/resume. Requires passing
        # the gateway instance to _start_turn() or adding a broadcast callback
        # to EventEmitter protocol.
        raise
    except Exception as exc:
        # Failures between turn/started and the driver try (intent
        # build, policy wrap, agent resolve) — or in finalization —
        # escape to the gateway, which only sends a turnId-less error
        # notification: the client would keep this turn inProgress for
        # the rest of the session. Emit the terminal snapshot first,
        # then re-raise so the gateway error path runs unchanged.
        if turn.status == TurnStatus.IN_PROGRESS:
            turn.status = TurnStatus.FAILED
            with contextlib.suppress(Exception):
                _close_turn(
                    log,
                    thread_id,
                    turn,
                    error={"message": str(exc) or exc.__class__.__name__},
                )
            # Mirror the driver-crash handler's bypass records: the
            # evolution store learns the failure and the failed turn
            # stays visible in the sidebar's legacy thread store.
            with contextlib.suppress(Exception):
                runtime._record_failed_turn_proposal(
                    turn,
                    intent=intent,
                    failure_source="turn_lifecycle_exception",
                )
        if turn.completed_at is None:
            turn.completed_at = now_utc()
        with contextlib.suppress(Exception):
            runtime._snapshot_to_thread_store(thread_id, log, intent)
        with contextlib.suppress(Exception):
            await emitter.notify(
                ServerMethod.TURN_COMPLETED,
                {
                    "threadId": thread_id,
                    "turn": turn.model_dump(by_alias=True, mode="json"),
                },
            )
        raise
    finally:
        # Journal replay stamps completedAt from the turn_completed
        # event ts (event_log.py); mirror that on the live snapshot the
        # gateway serializes right after this returns, or clients see
        # completedAt=null until the next resume.
        if turn.status != TurnStatus.IN_PROGRESS and turn.completed_at is None:
            turn.completed_at = now_utc()
        runtime._record_task_run_finished(turn)
        runtime._active_turn_ids.discard(turn.id)
        runtime._unregister_active_turn(turn.id)
        emitter.unregister_turn(turn.id)
