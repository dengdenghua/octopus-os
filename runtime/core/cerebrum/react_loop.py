from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Generator
from typing import TYPE_CHECKING, Any

from runtime.core.cerebrum._react_loop_reexports import *  # noqa: F403
from runtime.core.cerebrum.pause_control import turn_wall_time_cap_s
from runtime.core.cerebrum.react_action_outcomes import (
    _action_batch_fingerprint,
    _deduplicate_actions,
    _observation_is_noop,
    _per_action_outcomes,
    _retry_safe_affinity,
    _tool_call_succeeded,
)
from runtime.core.cerebrum.react_checkpointing import (
    _auto_checkpoint_and_evaluate_step,
)
from runtime.core.cerebrum.react_convergence import (
    EvidenceConvergence,
)
from runtime.core.cerebrum.react_execution import (
    _build_research_progress_summary,
    _phase_6d_dispatch_and_observe,
    _phase_6g_housekeeping,
)
from runtime.core.cerebrum.react_final_answer_guards import (
    _phase_6e_guards_and_step_emit,
)
from runtime.core.cerebrum.react_in_flight_nudges import (
    _apply_in_flight_nudges,
    _should_terminal_environment_convergence,
)
from runtime.core.cerebrum.react_loop_controls import (
    _cancel_pause_guard,
)
from runtime.core.cerebrum.react_loop_controls import (
    get_react_variant_stats as get_react_variant_stats,
)
from runtime.core.cerebrum.react_loop_state import (
    _LoopControl,
    _LoopState,
)
from runtime.core.cerebrum.react_model_deadlines import (
    _model_iteration_timeout_s,
)
from runtime.core.cerebrum.react_model_stream import (
    _phase_6b_model_stream,
)
from runtime.core.cerebrum.react_parallel_dispatch import (
    _WRITE_TOOLS,
    _dispatch_parallel_actions,
)
from runtime.core.cerebrum.react_phase_6c import (
    _phase_6c_parse_and_guard,
)
from runtime.core.cerebrum.react_prompt_assembly import (
    _assemble_prompt_and_messages,
    _emit_turn_start_events,
    _resolve_turn_bootstrap,
)
from runtime.core.cerebrum.react_public_updates import (
    _stream_public_evidence_narrative,
)
from runtime.core.cerebrum.react_quiet_evidence import (
    _quiet_evidence_checkpoint_due,
    _result_checkpoint_is_meaningful,
    _should_accumulate_quiet_evidence,
)
from runtime.core.cerebrum.react_resume import (
    _resume_or_register_turn,
)
from runtime.core.cerebrum.react_terminal import (
    _finalize_react_turn,
)
from runtime.core.cerebrum.react_types import (
    ReActResult,
    ReActStep,
)
from runtime.platform.config.builder import StackProtocol
from runtime.platform.config.schema import (
    BUDGET_DEFAULT_MAX_TOKENS,
    BUDGET_DEFAULT_MAX_USD,
)
from runtime.platform.models import ParsedIntent, Step, TaskId
from runtime.platform.models.rescue_policy import next_custom_model_fallback
from runtime.safety.approval.approval_gate import ApprovalProvider
from runtime.safety.guards.repeat_tool_reminder import (
    build_repeat_tool_reminder,
)

if TYPE_CHECKING:
    from runtime.execution.agents.base import Agent

_logger = logging.getLogger(__name__)


def _mark_subagent_owner_thread(thread_id: str, *, busy: bool) -> None:
    """Mirror the parent turn's running/idle state to the report lane.

    dsh ``agent.status``: the driver marks the owning agent busy for the
    duration of its turn so a child ``report`` landing mid-turn is queued
    (``delivery='queued'``, dsh ``inject``) instead of being recorded as a
    wakeup that never fires. Best-effort: a missing store or a failed call
    never breaks or delays the turn.
    """
    if not thread_id:
        return
    try:
        from runtime.execution.subagents.sessions import (
            get_subagent_session_store,
        )

        store = get_subagent_session_store()
        if store is None:
            return
        if busy:
            store.mark_thread_busy(thread_id)
        else:
            store.mark_thread_idle(thread_id)
    except Exception:  # noqa: BLE001 — owner-status mirroring is best-effort
        _logger.debug("subagent owner-status mirror failed", exc_info=True)


def stream_react_loop(
    stack: StackProtocol,
    intent: ParsedIntent,
    agent: Agent | None,
    *,
    model: str | None = None,
    max_iterations: int = 30,
    temperature: float = 0.3,
    enable_tools: bool = True,
    resume_task_id: TaskId | None = None,
    thread_id: str = "",
    max_tokens_budget: int = BUDGET_DEFAULT_MAX_TOKENS,
    max_usd_budget: float = BUDGET_DEFAULT_MAX_USD,
    approval_provider: ApprovalProvider | None = None,
    output_chunk_sink: Callable[[str, str, str], None] | None = None,
    step_evaluator: Callable[[dict[str, Any]], Any] | None = None,
    planning_mode: bool = False,
    reasoning_effort: str | None = None,
    steering_drain: Callable[[], list[str]] | None = None,
    on_auto_parallel_batch: Callable[[str], None] | None = None,
) -> Generator[dict[str, Any], None, ReActResult | None]:
    """Drive one parent ReAct turn (see ``_stream_react_loop_impl``).

    Thin lifecycle wrapper: marks the turn's thread busy in the subagent
    report lane while the turn runs (dsh ``agent.status``) and always clears
    it on exit — normal completion, failure, or early generator close.
    """
    _mark_subagent_owner_thread(thread_id, busy=True)
    try:
        return (
            yield from _stream_react_loop_impl(
                stack,
                intent,
                agent,
                model=model,
                max_iterations=max_iterations,
                temperature=temperature,
                enable_tools=enable_tools,
                resume_task_id=resume_task_id,
                thread_id=thread_id,
                max_tokens_budget=max_tokens_budget,
                max_usd_budget=max_usd_budget,
                approval_provider=approval_provider,
                output_chunk_sink=output_chunk_sink,
                step_evaluator=step_evaluator,
                planning_mode=planning_mode,
                reasoning_effort=reasoning_effort,
                steering_drain=steering_drain,
                on_auto_parallel_batch=on_auto_parallel_batch,
            )
        )
    finally:
        _mark_subagent_owner_thread(thread_id, busy=False)


def _stream_react_loop_impl(
    stack: StackProtocol,
    intent: ParsedIntent,
    agent: Agent | None,
    *,
    model: str | None = None,
    max_iterations: int = 30,
    temperature: float = 0.3,
    enable_tools: bool = True,
    resume_task_id: TaskId | None = None,
    thread_id: str = "",
    max_tokens_budget: int = BUDGET_DEFAULT_MAX_TOKENS,
    max_usd_budget: float = BUDGET_DEFAULT_MAX_USD,
    approval_provider: ApprovalProvider | None = None,
    output_chunk_sink: Callable[[str, str, str], None] | None = None,
    step_evaluator: Callable[[dict[str, Any]], Any] | None = None,
    planning_mode: bool = False,
    reasoning_effort: str | None = None,
    steering_drain: Callable[[], list[str]] | None = None,
    on_auto_parallel_batch: Callable[[str], None] | None = None,
) -> Generator[dict[str, Any], None, ReActResult | None]:
    # Orchestration map: bootstrap/prompt → react_prompt_assembly; resume →
    # react_resume; model streaming → react_model_stream; parse →
    # react_phase_6c; dispatch/housekeeping → react_execution; guards →
    # react_final_answer_guards; checkpoint → react_checkpointing; terminal →
    # react_terminal. Phases exchange state through _LoopState/_LoopControl.

    # Config-driven ReAct tunables (budget.model_iteration_timeout_s /
    # budget.convergence_max_tokens). Read off the stack when present; the
    # test _FakeStack carries no config, so both fall back to their defaults.
    _cfg = getattr(stack, "config", None)
    _budget = getattr(_cfg, "budget", None) if _cfg is not None else None
    _model_iteration_timeout_s_config = (
        float(_budget.model_iteration_timeout_s)
        if _budget is not None and getattr(_budget, "model_iteration_timeout_s", None) is not None
        else None
    )
    _convergence_max_tokens = int(
        getattr(_budget, "convergence_max_tokens", 2000) if _budget is not None else 2000
    )

    # ── PHASE 1–2 · turn bootstrap (react_prompt_assembly) ─────────────
    # Router resolution, tool/native-mode gating, and task-id assignment
    # moved verbatim to react_prompt_assembly._resolve_turn_bootstrap.
    _boot = _resolve_turn_bootstrap(
        stack,
        intent,
        agent,
        model=model,
        enable_tools=enable_tools,
        reasoning_effort=reasoning_effort,
        approval_provider=approval_provider,
        resume_task_id=resume_task_id,
    )
    if _boot is None:
        return None
    router = _boot.router
    _reasoning_effort = _boot.reasoning_effort
    _no_tool_turn = _boot.no_tool_turn
    executor = _boot.executor
    tools_active = _boot.tools_active
    effective_model = _boot.effective_model
    _native_mode = _boot.native_mode
    _strict_explicit_reads = _boot.strict_explicit_reads
    _ordered_result_handoffs = _boot.ordered_result_handoffs
    _native_public_update_tool_specs = _boot.native_public_update_tool_specs
    _native_evidence_update_tool_specs = _boot.native_evidence_update_tool_specs
    react_task_id = _boot.react_task_id
    _camouflage_suffix = _boot.camouflage_suffix

    # ── PHASE 3 · system + volatile prompt assembly ────────────────────
    # Establish the turn-level visibility trace before prompt assembly so
    # capability routing / delegation visibility / skill-catalog decisions
    # recorded inside ``_format_skill_catalog`` land on one trace, then
    # export it as an event in PHASE 4.7. Best-effort: never blocks the turn.
    _visibility_token = None
    try:
        from runtime.core.cerebrum._visibility_trace import (
            active_trace,
            new_trace,
            reset_active_trace,
            set_active_trace,
        )

        if active_trace() is None:
            _visibility_token = set_active_trace(new_trace())
    except (
        ImportError
    ):  # pragma: no cover — module is always present  # noqa: BLE001 — visibility token is optional
        pass
    # PHASE 3→4.7 share a turn-scoped ContextVar trace. Wrap them in
    # try/finally so the token is reset even if a consumer closes the
    # generator early (reconnect / test short-circuit), never leaking
    # the trace into a recycled producer thread or the shared test thread.
    try:
        _assembly = _assemble_prompt_and_messages(
            intent=intent,
            agent=agent,
            stack=stack,
            executor=executor,
            approval_provider=approval_provider,
            resume_task_id=resume_task_id,
            planning_mode=planning_mode,
            tools_active=tools_active,
            native_mode=_native_mode,
            no_tool_turn=_no_tool_turn,
            strict_explicit_reads=_strict_explicit_reads,
            camouflage_suffix=_camouflage_suffix,
            max_iterations=max_iterations,
            max_tokens_budget=max_tokens_budget,
            max_usd_budget=max_usd_budget,
        )
        messages = _assembly.messages
        max_iterations = _assembly.max_iterations
        _metadata = _assembly.metadata
        _effective_wp = _assembly.effective_wp
        _is_goal_mode = _assembly.is_goal_mode
        _is_code_mode = _assembly.is_code_mode
        _browser_operation_mode = _assembly.browser_operation_mode
        _todo_protocol_required = _assembly.todo_protocol_required
        _todo_protocol_visible = _assembly.todo_protocol_visible
        _file_inspection_tools_visible = _assembly.file_inspection_tools_visible
        _read_only_turn = _assembly.read_only_turn
        _observed_read_sequence = _assembly.observed_read_sequence
        _final_guard_grounded_source_paths = _assembly.final_guard_grounded_source_paths
        _guard_impasse_state = _assembly.guard_impasse_state
        _budget_auto_pause_enabled = _assembly.budget_auto_pause_enabled
        _budget_pause_threshold = _assembly.budget_pause_threshold
        _realtime_public_orientation_requested = _assembly.realtime_public_orientation_requested
        _grounding_sources = _assembly.grounding_sources
        _prior_grounding_text = _assembly.prior_grounding_text
        _is_swarm_mode = _assembly.is_swarm_mode
        _is_research_mode = _assembly.is_research_mode
        _active_max_tokens_budget = _assembly.active_max_tokens_budget
        _active_max_usd_budget = _assembly.active_max_usd_budget
        _effective_goal = _assembly.effective_goal or intent.normalized_goal
        # Downstream execution and final-answer guards must evaluate the
        # durable contract, not only a short steering follow-up such as “继续”.
        if _effective_goal != intent.normalized_goal:
            intent = intent.model_copy(update={"normalized_goal": _effective_goal})

        # ── PHASE 4/4.5 · start events + auto-delegation ───────────────
        # Moved verbatim to react_prompt_assembly._emit_turn_start_events.
        yield from _emit_turn_start_events(
            react_task_id=react_task_id,
            thread_id=thread_id,
            max_iterations=max_iterations,
            grounding_sources=_grounding_sources,
            tools_active=tools_active,
            planning_mode=planning_mode,
            intent=intent,
            executor=executor,
            stack=stack,
            messages=messages,
            on_auto_parallel_batch=on_auto_parallel_batch,
        )

        # ── PHASE 4.7 · visibility-trace snapshot ──────────────────────
        # Export the decision-point trace collected during prompt assembly
        # (capability routing / delegation visibility / skill catalog) as
        # one snapshot event so the realtime bridge can persist it as an
        # item. Best-effort: a missing or empty trace skips the event and
        # can never block or alter the turn.
        _visibility_trace = None
        try:
            from runtime.core.cerebrum._visibility_trace import active_trace

            _visibility_trace = active_trace()
        except (
            ImportError
        ):  # pragma: no cover — module is always present  # noqa: BLE001 — optional tooling hook
            pass
        if _visibility_trace is None:
            # ``activation`` is normally not bound in this scope; the
            # ContextVar above is the designed channel (react_context_helpers
            # wires ``activation.trace`` into it during PHASE 3). Keep this
            # defensive lookup so a caller that attaches the trace to its
            # activation object directly still wins.
            _activation = locals().get("activation")
            if _activation is not None:
                _visibility_trace = getattr(_activation, "trace", None)
        if _visibility_trace is not None and not _visibility_trace.empty():
            yield {
                "type": "visibility",
                "summary": "本轮能力路由 / 委派 / 技能目录决策",
                "steps": _visibility_trace.export(),
            }
    finally:
        # All trace decision points (capability routing / delegation
        # visibility / skill catalog) run during PHASE 3 prompt assembly, so
        # the turn-level trace is done after this snapshot. Reset it even on
        # early generator close so a recycled producer thread/context never
        # accumulates decisions across turns.
        if _visibility_token is not None:
            reset_active_trace(_visibility_token)

    # ── PHASE 5 · pre-loop state init + checkpoint resume ──────────────
    # Pause registration, taint reset, checkpoint resume, and resume
    # grants moved verbatim to react_resume._resume_or_register_turn.
    # Audit T-05: register the active turn with a mode-graded wall-clock
    # hard cap so the per-iteration guard auto-pauses runaway turns; the
    # resume path carries the same cap forward.
    _wall_time_cap = turn_wall_time_cap_s(
        goal_mode=_is_goal_mode,
        research_mode=_is_research_mode,
        swarm_mode=_is_swarm_mode,
    )
    _rboot = _resume_or_register_turn(
        stack,
        intent,
        agent,
        resume_task_id=resume_task_id,
        react_task_id=react_task_id,
        thread_id=thread_id,
        max_iterations=max_iterations,
        active_max_tokens_budget=_active_max_tokens_budget,
        active_max_usd_budget=_active_max_usd_budget,
        max_wall_time_seconds=_wall_time_cap,
        messages=messages,
    )
    _pause = _rboot.pause_controller
    _agent_id_for_pause = _rboot.agent_id_for_pause
    steps: list[ReActStep] = _rboot.steps
    messages = _rboot.messages
    _working_set: dict[str, dict[str, Any]] = _rboot.working_set
    _progress_summary = _rboot.progress_summary
    _current_phase = _rboot.current_phase
    final_answer: str | None = _rboot.final_answer
    terminated_reason = _rboot.terminated_reason
    react_task_id = _rboot.react_task_id
    resume_from_iter = _rboot.resume_from_iter
    _resume_event: dict[str, Any] | None = _rboot.resume_event
    max_iterations = _rboot.max_iterations
    executed_beak_steps: list[Step] = []
    final_answer_segments: list[str] = []
    final_answer_emitted = False

    # Throughput sampler — chars/sec across all delta yields. We emit a
    # ``throughput`` event every ~500ms so the UI can show a live
    # tokens-per-second indicator without flooding the WebSocket. Chars
    # are a useful proxy: at the cost of being model-dependent, they
    # don't require a tokenizer in the hot path.
    _throughput_started_at = time.monotonic()
    _throughput_chars = 0
    _throughput_last_emit = _throughput_started_at
    _throughput_interval_s = 0.5

    _known_background_tasks: dict[str, dict[str, Any]] = {}

    if _resume_event is not None:
        yield _resume_event

    consecutive_format_violations = 0
    consecutive_llm_errors = 0
    _last_public_update_key = ""
    _result_handoff_ready = False
    _realtime_public_narrative = bool(intent.user_context.get("realtime_public_narrative"))
    _realtime_public_orientation = _realtime_public_orientation_requested
    _quiet_evidence_steps: list[ReActStep] = []
    _force_convergence_next = False
    # Resume must preserve the tools-disabled terminal lane. Recompute it
    # from restored server-owned receipts before the first model request;
    # legacy checkpoints without provenance fail closed and keep tools on.
    _terminal_convergence_active = _should_terminal_environment_convergence(
        steps,
        is_code_mode=_is_code_mode,
    )
    _green_verification_convergence_active = False
    _green_convergence_todo_used = False
    _evidence_convergence_active: EvidenceConvergence | None = None
    # Persistent execution-state evidence. Recomputing green rounds from the
    # whole trajectory is useful as a fallback, but long turns can decorate
    # old observations with recovery nudges. Track clean verifier rounds at
    # the point tools actually finish so a red→fixed→green trajectory reaches
    # a stable terminal state instead of cycling through verify/todo forever.
    _saw_successful_code_write = False
    _clean_verification_rounds_after_write = 0
    _last_failed_action_fingerprint = ""
    _consecutive_same_failed_actions = 0
    _model_timeout_recoveries = 0
    # Allow two consecutive zero-anchor rounds before bailing. The
    # first violation is often a model warming up — it dumps a chunk
    # of plain markdown / JSON before remembering to use the
    # ``Action:`` anchor. Setting this to 1 used to terminate the
    # loop on the very first round, killing tool work that would have
    # happened on round 2. Two rounds tolerates the warmup but still
    # bails fast when the model genuinely cannot follow ReAct format.
    _format_violation_bail_at = 2
    _context_pressure_signaled: bool = False
    # Once-per-turn in-flight nudge: the first environmental tool failure
    # (sandbox / network block) tells the model to pivot to static evidence.
    _env_degradation_signaled: bool = False

    def _append_pending_live_steering() -> int:
        if steering_drain is None:
            return 0
        try:
            pending = steering_drain()
        except Exception:  # noqa: BLE001 — live steering must not break the turn
            _logger.warning("live steering poll failed", exc_info=True)
            return 0
        from runtime.core.cerebrum.live_steering import (
            append_live_steering_messages,
        )

        count = append_live_steering_messages(messages, pending)
        if count:
            _logger.info(
                "react_loop accepted %d priority user follow-up(s) at a safe boundary",
                count,
            )
            # dsh guard semantics: a user interjection changes the context;
            # repetition across it is not a loop. Reset the repeat-call chain.
            _repeat_guard = state.repeat_guard
            if _repeat_guard is not None:
                try:
                    _repeat_guard.reset(
                        agent_key=state.thread_id or "default",
                    )
                except Exception:  # noqa: BLE001 — advisory; never break the turn
                    _logger.debug("repeat-tool guard reset skipped", exc_info=True)
        return count

    from runtime.platform.models.llm import (
        model_supports_thinking as _supports_thinking,
    )

    _resolved_model = effective_model
    if hasattr(router, "_resolve"):
        try:
            _sub = router._resolve(effective_model)
            if _sub is not router:
                _resolved_model = getattr(_sub, "default_model", None) or effective_model
        except (AttributeError, TypeError):  # noqa: BLE001 — subrouter doesn't expose default_model; fall back to effective_model
            pass
    _wants_thinking = _supports_thinking(_resolved_model)
    # Per-iteration ``max_tokens`` ceiling. Non-thinking models used to
    # cap at 2000 tokens, which is fine for a chatty back-and-forth but
    # truncates long-form generation mid-sentence — research reports
    # are typically 4-6k tokens of markdown and were getting cut at
    # ~2k char before the model could reach the conclusion. The model
    # then read the finish_reason as "length" and (without the
    # continuation logic below) decided the task was done, emitting a
    # short summary instead of resuming. 8k is enough for a single
    # report section; the continuation path catches anything longer.
    _max_tokens_per_iter = 4096 if _wants_thinking else 8000
    _attempted_models = {effective_model}
    _model_failovers = 0

    def _switch_react_model(next_model: str) -> None:
        """Retarget later rounds while preserving this turn's evidence."""

        nonlocal effective_model, _max_tokens_per_iter, _resolved_model, _wants_thinking
        effective_model = next_model
        _resolved_model = next_model
        if hasattr(router, "_resolve"):
            try:
                _subrouter = router._resolve(next_model)
                if _subrouter is not router:
                    _resolved_model = getattr(_subrouter, "default_model", None) or next_model
            except (AttributeError, TypeError):  # noqa: BLE001 — optional router introspection
                pass
        _wants_thinking = _supports_thinking(_resolved_model)
        _max_tokens_per_iter = 4096 if _wants_thinking else 8000

    def _try_react_model_failover(reason: str) -> str | None:
        nonlocal _model_failovers
        if _model_failovers >= 1:
            return None
        next_model = next_custom_model_fallback(
            effective_model,
            _attempted_models,
            require_tool_use=_native_mode,
        )
        if not next_model:
            return None
        previous_model = effective_model
        _switch_react_model(next_model)
        _attempted_models.add(next_model)
        _model_failovers += 1
        _logger.warning(
            "react_loop switching model %s -> %s after %s",
            previous_model,
            next_model,
            reason,
        )
        return next_model

    def _try_requested_model_switch(state: _LoopState) -> str | None:
        """Model-switch hooked to the spin guard's escalation stage.

        Uses the same failover closure and one-switch-per-turn budget as the
        LLM-error path, passing a distinct reason so the audit trail shows the
        switch was spin-triggered rather than error-triggered.
        """
        return _try_react_model_failover("model_spinning")

    # Realtime reasoning providers may stream private thinking for minutes
    # before producing ordinary text or a tool call. The UI must not depend on
    # that hidden stream for its first conversational beat. Generate one
    # task-specific, model-authored sentence through the bounded,
    # thinking-disabled narrator before starting the expensive working call.
    # This is deliberately gateway-gated so batch/API callers keep their
    # existing request count and latency.
    if (
        bool((intent.user_context or {}).get("realtime_public_preface"))
        and _realtime_public_orientation
        and tools_active
        and not _no_tool_turn
        and resume_from_iter == 0
        and not steps
    ):
        try:
            _initial_public_update = yield from _stream_public_evidence_narrative(
                router,
                model=effective_model,
                goal=intent.normalized_goal,
                step=ReActStep(iteration=0),
                convergence=None,
                iteration=0,
                previous_key=_last_public_update_key,
                pending_action=True,
            )
        except Exception as exc:  # noqa: BLE001 — optional first-public-beat repair
            _logger.warning("initial public orientation failed: %s", exc)
            _initial_public_update = ""
        if _initial_public_update:
            _last_public_update_key = re.sub(r"\s+", " ", _initial_public_update).strip().casefold()

    # ── _LoopState assembly (Wave 2) ─────────────────────────────────
    # Reference-typed fields share objects with the locals above;
    # constant scalars are snapshotted here and per-iteration scalars
    # are synced at each extracted-phase call site (currently 6c).
    # dsh repeat-tool-reminder: one advisory guard per turn (fresh chain
    # per turn; user follow-ups reset it via _append_pending_live_steering).
    _repeat_guard = build_repeat_tool_reminder(
        getattr(intent, "user_context", None) or None,
    )
    state = _LoopState(
        repeat_guard=_repeat_guard,
        stack=stack,
        goal=_effective_goal,
        executor=executor,
        react_task_id=react_task_id,
        pause_controller=_pause,
        effective_wp=_effective_wp,
        format_violation_bail_at=_format_violation_bail_at,
        final_guard_grounded_source_paths=_final_guard_grounded_source_paths,
        guard_impasse_state=_guard_impasse_state,
        prior_grounding_text=_prior_grounding_text,
        intent=intent,
        agent=agent,
        thread_id=thread_id,
        approval_provider=approval_provider,
        output_chunk_sink=output_chunk_sink,
        router=router,
        metadata=_metadata,
        is_goal_mode=_is_goal_mode,
        observed_read_sequence=_observed_read_sequence,
        ordered_result_handoffs=_ordered_result_handoffs,
        realtime_public_orientation=_realtime_public_orientation,
        realtime_public_narrative=_realtime_public_narrative,
        is_code_mode=_is_code_mode,
        browser_operation_mode=_browser_operation_mode,
        todo_protocol_required=_todo_protocol_required,
        todo_protocol_visible=_todo_protocol_visible,
        file_inspection_tools_visible=_file_inspection_tools_visible,
        read_only_turn=_read_only_turn,
        no_tool_turn=_no_tool_turn,
        steps=steps,
        executed_beak_steps=executed_beak_steps,
        messages=messages,
        working_set=_working_set,
        effective_model=effective_model,
        final_answer_segments=final_answer_segments,
        planning_mode=planning_mode,
        enable_tools=enable_tools,
        current_phase=_current_phase,
        consecutive_same_failed_actions=_consecutive_same_failed_actions,
        last_failed_action_fingerprint=_last_failed_action_fingerprint,
        terminal_convergence_active=_terminal_convergence_active,
        green_verification_convergence_active=_green_verification_convergence_active,
        green_convergence_todo_used=_green_convergence_todo_used,
        result_handoff_ready=_result_handoff_ready,
        last_public_update_key=_last_public_update_key,
        saw_successful_code_write=_saw_successful_code_write,
        clean_verification_rounds_after_write=_clean_verification_rounds_after_write,
        quiet_evidence_steps=_quiet_evidence_steps,
        native_mode=_native_mode,
        throughput_chars=_throughput_chars,
        temperature=temperature,
        max_tokens_per_iter=_max_tokens_per_iter,
        wants_thinking=_wants_thinking,
        reasoning_effort=_reasoning_effort,
        native_evidence_update_tool_specs=_native_evidence_update_tool_specs,
        native_public_update_tool_specs=_native_public_update_tool_specs,
        budget_auto_pause_enabled=_budget_auto_pause_enabled,
        budget_pause_threshold=_budget_pause_threshold,
        agent_id_for_pause=_agent_id_for_pause,
        throughput_started_at=_throughput_started_at,
        throughput_interval_s=_throughput_interval_s,
        throughput_last_emit=_throughput_last_emit,
        consecutive_llm_errors=consecutive_llm_errors,
        iteration_base_limit=max_iterations,
        iteration_limit=max_iterations,
        terminated_reason=terminated_reason,
        final_answer=final_answer,
        final_answer_emitted=final_answer_emitted,
    )

    def _iteration_indices() -> Any:
        iteration = resume_from_iter
        while iteration < max_iterations:
            yield iteration
            iteration += 1

    for i in _iteration_indices():
        # ── PHASE 6a · cancel / pause guard ────────────────────────────
        _guard_terminated_reason = yield from _cancel_pause_guard(
            iteration=i,
            react_task_id=react_task_id,
            max_iterations=max_iterations,
            stack=stack,
            messages=messages,
            steps=steps,
            working_set=_working_set,
            progress_summary=_progress_summary,
            current_phase=_current_phase,
            pause_controller=_pause,
            append_pending_live_steering=_append_pending_live_steering,
        )
        if _guard_terminated_reason is not None:
            terminated_reason = _guard_terminated_reason
            break

        # ── Spin-escalation model switch (capability-enhancing) ────────
        # The spin guard (phase 6g) may request a model switch after a
        # compression pass failed to break a spinning turn. Consume the flag
        # here, before the next LLM call, so the switch takes effect on this
        # iteration rather than waiting for another blank round.
        if state.spin_model_switch_requested:
            state.spin_model_switch_requested = False
            _fallback_model = _try_requested_model_switch(state)
            if _fallback_model is not None:
                _logger.warning(
                    "react_loop spin-escalation model switch → %s before iter %d (task %s)",
                    _fallback_model,
                    i,
                    react_task_id,
                )

        # ── PHASE 6b · LLM call + Final-Answer anchor stream ───────────
        state.native_mode = _native_mode
        state.evidence_convergence_active = _evidence_convergence_active
        state.effective_model = effective_model
        state.force_convergence_next = _force_convergence_next
        state.last_public_update_key = _last_public_update_key
        state.throughput_chars = _throughput_chars
        state.throughput_last_emit = _throughput_last_emit
        # final_stream_started / streamed_final_chars /
        # final_delta_emitted_this_iteration are reset unconditionally
        # inside the phase each iteration — write-only, no sync-in.
        state.terminated_reason = terminated_reason
        state.consecutive_llm_errors = consecutive_llm_errors
        state.model_failovers = _model_failovers

        def _try_failover_for_6b(reason: str) -> str | None:
            # Same closure bridge as _try_failover_for_6c: the loop's
            # failover closure reads ``_native_mode`` and bumps
            # ``_model_failovers`` via nonlocal; mirror both through
            # state so the phase observes the same values the inline
            # code used to see. ``next_custom_model_fallback`` keeps
            # resolving through the react_loop module global.
            nonlocal _native_mode
            _native_mode = state.native_mode
            _fallback = _try_react_model_failover(reason)
            state.model_failovers = _model_failovers  # noqa: B023 — called immediately, same iteration
            return _fallback

        _loop_ctrl = yield from _phase_6b_model_stream(
            state,
            i=i,
            model_iteration_timeout_s=_model_iteration_timeout_s,
            model_iteration_timeout_s_config=_model_iteration_timeout_s_config,
            try_react_model_failover=_try_failover_for_6b,
        )
        _force_convergence_next = state.force_convergence_next
        _last_public_update_key = state.last_public_update_key
        _throughput_chars = state.throughput_chars
        _throughput_last_emit = state.throughput_last_emit
        _final_stream_started = state.final_stream_started
        _streamed_final_chars = state.streamed_final_chars
        _final_delta_emitted_this_iteration = state.final_delta_emitted_this_iteration
        terminated_reason = state.terminated_reason
        consecutive_llm_errors = state.consecutive_llm_errors
        _model_failovers = state.model_failovers
        resp = state.resp
        raw_text = state.raw_text
        _request_has_tool_evidence = state.request_has_tool_evidence
        _iteration_soft_timed_out = state.iteration_soft_timed_out
        _maybe_emit_throughput = state.maybe_emit_throughput
        if _loop_ctrl is _LoopControl.RETURN_NONE:
            return None
        if _loop_ctrl is _LoopControl.BREAK:
            break
        if _loop_ctrl is _LoopControl.NEXT_ITERATION:
            continue

        # ── PHASE 6c · parse step / format-violation check ─────────────
        # Sync per-iteration scalars into state; the phase pulls/pushes
        # them internally and the write-set is synced back below.
        state.native_mode = _native_mode
        state.evidence_convergence_active = _evidence_convergence_active
        state.model_timeout_recoveries = _model_timeout_recoveries
        state.model_failovers = _model_failovers
        state.final_stream_started = _final_stream_started
        state.force_convergence_next = _force_convergence_next
        state.tools_active = tools_active
        state.consecutive_format_violations = consecutive_format_violations
        state.throughput_chars = _throughput_chars
        state.final_answer = final_answer
        state.terminated_reason = terminated_reason
        state.final_answer_emitted = final_answer_emitted
        state.final_delta_emitted_this_iteration = _final_delta_emitted_this_iteration

        def _try_failover_for_6c(reason: str) -> str | None:
            # The failover closure reads the loop's ``_native_mode`` and
            # bumps ``_model_failovers`` via nonlocal; mirror both through
            # state so the phase observes the same values the inline code
            # used to see. ``next_custom_model_fallback`` keeps resolving
            # through the react_loop module global (tests patch it there).
            nonlocal _native_mode
            _native_mode = state.native_mode
            _fallback = _try_react_model_failover(reason)
            state.model_failovers = _model_failovers  # noqa: B023 — called immediately, same iteration
            return _fallback

        _loop_ctrl = yield from _phase_6c_parse_and_guard(
            state,
            resp=resp,
            raw_text=raw_text,
            i=i,
            request_has_tool_evidence=_request_has_tool_evidence,
            iteration_soft_timed_out=_iteration_soft_timed_out,
            try_react_model_failover=_try_failover_for_6c,
            maybe_emit_throughput=_maybe_emit_throughput,
        )
        _native_mode = state.native_mode
        _model_timeout_recoveries = state.model_timeout_recoveries
        _final_stream_started = state.final_stream_started
        _force_convergence_next = state.force_convergence_next
        consecutive_format_violations = state.consecutive_format_violations
        _throughput_chars = state.throughput_chars
        final_answer = state.final_answer
        terminated_reason = state.terminated_reason
        final_answer_emitted = state.final_answer_emitted
        _final_delta_emitted_this_iteration = state.final_delta_emitted_this_iteration
        step = state.step
        maybe_final = state.maybe_final
        text = state.text
        _length_limited = state.length_limited
        _length_limit_should_continue = state.length_limit_should_continue
        if _loop_ctrl is _LoopControl.RETURN_NONE:
            return None
        if _loop_ctrl is _LoopControl.BREAK:
            break
        assert step is not None, "phase 6c must produce a ReAct step before execution"
        # ── PHASE 6d · action dispatch + observation ───────────────────
        state.maybe_final = maybe_final
        state.terminated_reason = terminated_reason
        state.evidence_convergence_active = _evidence_convergence_active
        state.force_convergence_next = _force_convergence_next
        state.tools_active = tools_active
        state.effective_model = effective_model
        state.current_phase = _current_phase
        state.consecutive_same_failed_actions = _consecutive_same_failed_actions
        state.last_failed_action_fingerprint = _last_failed_action_fingerprint
        state.green_verification_convergence_active = _green_verification_convergence_active
        state.green_convergence_todo_used = _green_convergence_todo_used
        state.result_handoff_ready = _result_handoff_ready
        state.last_public_update_key = _last_public_update_key
        state.saw_successful_code_write = _saw_successful_code_write
        state.clean_verification_rounds_after_write = _clean_verification_rounds_after_write
        state.quiet_evidence_steps = _quiet_evidence_steps
        _loop_ctrl = yield from _phase_6d_dispatch_and_observe(
            state,
            i=i,
            dispatch_parallel_actions=_dispatch_parallel_actions,
            write_tools=_WRITE_TOOLS,
            result_checkpoint_is_meaningful=_result_checkpoint_is_meaningful,
            should_accumulate_quiet_evidence=_should_accumulate_quiet_evidence,
            quiet_evidence_checkpoint_due=_quiet_evidence_checkpoint_due,
            action_batch_fingerprint=_action_batch_fingerprint,
            deduplicate_actions=_deduplicate_actions,
            per_action_outcomes=_per_action_outcomes,
            retry_safe_affinity=_retry_safe_affinity,
            tool_call_succeeded=_tool_call_succeeded,
            observation_is_noop=_observation_is_noop,
        )
        maybe_final = state.maybe_final
        terminated_reason = state.terminated_reason
        _evidence_convergence_active = state.evidence_convergence_active
        _force_convergence_next = state.force_convergence_next
        _consecutive_same_failed_actions = state.consecutive_same_failed_actions
        _last_failed_action_fingerprint = state.last_failed_action_fingerprint
        _green_verification_convergence_active = state.green_verification_convergence_active
        _green_convergence_todo_used = state.green_convergence_todo_used
        _result_handoff_ready = state.result_handoff_ready
        _last_public_update_key = state.last_public_update_key
        _saw_successful_code_write = state.saw_successful_code_write
        _clean_verification_rounds_after_write = state.clean_verification_rounds_after_write
        _quiet_evidence_steps = state.quiet_evidence_steps
        if _loop_ctrl is _LoopControl.RETURN_NONE:
            return None
        if _loop_ctrl is _LoopControl.BREAK:
            break
        if _loop_ctrl is _LoopControl.NEXT_ITERATION:
            continue

        # ── PHASE 6e · in-flight nudges ────────────────────────────────
        (
            _context_pressure_signaled,
            _green_verification_convergence_active,
            _force_convergence_next,
            _env_degradation_signaled,
            _terminal_convergence_active,
        ) = _apply_in_flight_nudges(
            steps=steps,
            step=step,
            i=i,
            known_background_tasks=_known_background_tasks,
            todo_protocol_required=_todo_protocol_required,
            todo_protocol_visible=_todo_protocol_visible,
            is_code_mode=_is_code_mode,
            messages=messages,
            effective_model=effective_model,
            context_pressure_signaled=_context_pressure_signaled,
            green_verification_convergence_active=_green_verification_convergence_active,
            force_convergence_next=_force_convergence_next,
            env_degradation_signaled=_env_degradation_signaled,
            terminal_convergence_active=_terminal_convergence_active,
        )

        # ── PHASE 6e · guards + step yield ────────────────────────────
        state.maybe_final = maybe_final
        state.final_stream_started = _final_stream_started
        state.force_convergence_next = _force_convergence_next
        state.terminal_convergence_active = _terminal_convergence_active
        state.final_delta_emitted_this_iteration = _final_delta_emitted_this_iteration
        state.green_verification_convergence_active = _green_verification_convergence_active
        state.green_convergence_todo_used = _green_convergence_todo_used
        state.clean_verification_rounds_after_write = _clean_verification_rounds_after_write
        state.final_answer = final_answer
        state.terminated_reason = terminated_reason
        state.evidence_convergence_active = _evidence_convergence_active
        state.tools_active = tools_active
        state.current_phase = _current_phase
        state.streamed_final_chars = _streamed_final_chars
        state.progress_summary = _progress_summary
        _loop_ctrl = yield from _phase_6e_guards_and_step_emit(
            state,
            i=i,
            append_pending_live_steering=_append_pending_live_steering,
            build_research_progress_summary=_build_research_progress_summary,
        )
        maybe_final = state.maybe_final
        _force_convergence_next = state.force_convergence_next
        _green_verification_convergence_active = state.green_verification_convergence_active
        _green_convergence_todo_used = state.green_convergence_todo_used
        _clean_verification_rounds_after_write = state.clean_verification_rounds_after_write
        final_answer = state.final_answer
        terminated_reason = state.terminated_reason
        _final_delta_emitted_this_iteration = state.final_delta_emitted_this_iteration
        _public_progress_summary = state.public_progress_summary
        if _loop_ctrl is _LoopControl.BREAK:
            break

        # ── PHASE 6f · auto-checkpoint + step evaluator ────────────────
        yield from _auto_checkpoint_and_evaluate_step(
            maybe_final=maybe_final,
            step=step,
            stack=stack,
            react_task_id=react_task_id,
            max_iterations=max_iterations,
            messages=messages,
            steps=steps,
            working_set=_working_set,
            progress_summary=_progress_summary,
            current_phase=_current_phase,
            public_progress_summary=_public_progress_summary,
            step_evaluator=step_evaluator,
            retry_hint_sink=state.guard_notices,
        )

        steps.append(step)

        # ── PHASE 6g · housekeeping (msg append / continue / loop tail)
        # Body moved to react_execution._phase_6g_housekeeping (Wave 2);
        # sync the per-iteration scalars in/out around the call.
        state.planning_mode = planning_mode
        state.enable_tools = enable_tools
        state.executor = executor
        state.tools_active = tools_active
        state.maybe_final = maybe_final
        state.final_answer = final_answer
        state.final_answer_emitted = final_answer_emitted
        state.terminated_reason = terminated_reason
        state.current_phase = _current_phase
        state.progress_summary = _progress_summary
        state.force_convergence_next = _force_convergence_next
        state.length_limit_should_continue = _length_limit_should_continue
        state.messages = messages
        _loop_ctrl = _phase_6g_housekeeping(state, i=i, max_iterations=max_iterations)
        planning_mode = state.planning_mode
        enable_tools = state.enable_tools
        executor = state.executor
        tools_active = state.tools_active
        maybe_final = state.maybe_final
        final_answer = state.final_answer
        final_answer_emitted = state.final_answer_emitted
        terminated_reason = state.terminated_reason
        _current_phase = state.current_phase
        _progress_summary = state.progress_summary
        _force_convergence_next = state.force_convergence_next
        _length_limit_should_continue = state.length_limit_should_continue
        messages = state.messages
        # ``_iteration_indices`` closes over this local, so a productive turn
        # can continue immediately after a bounded extension without creating
        # a new task or waiting for a user-authored "继续" turn.
        max_iterations = max(max_iterations, state.iteration_limit)
        if _loop_ctrl is _LoopControl.BREAK:
            break

    # ── PHASE 7 · post-loop terminal handling ──────────────────────────
    # ── PHASE 8 · finalization + react_completed yield ─────────────────
    return (
        yield from _finalize_react_turn(
            terminated_reason=terminated_reason,
            final_answer=final_answer,
            i=locals().get("i", 0),
            react_task_id=react_task_id,
            pause_controller=_pause,
            messages=messages,
            is_code_mode=_is_code_mode,
            is_research_mode=_is_research_mode,
            is_swarm_mode=_is_swarm_mode,
            effective_model=effective_model,
            router=router,
            steps=steps,
            executed_beak_steps=executed_beak_steps,
            stack=stack,
            todo_protocol_required=_todo_protocol_required,
            todo_protocol_visible=_todo_protocol_visible,
            file_inspection_tools_visible=_file_inspection_tools_visible,
            tools_active=tools_active,
            goal=intent.normalized_goal,
            browser_operation_mode=_browser_operation_mode,
            final_guard_grounded_source_paths=_final_guard_grounded_source_paths,
            prior_grounding_text=_prior_grounding_text,
            final_answer_emitted=final_answer_emitted,
            model_iteration_timeout_s=_model_iteration_timeout_s,
            model_iteration_timeout_s_config=_model_iteration_timeout_s_config,
            convergence_max_tokens=_convergence_max_tokens,
        )
    )


def run_react_loop(
    stack: StackProtocol,
    intent: ParsedIntent,
    agent: Agent | None,
    *,
    model: str | None = None,
    max_iterations: int = 30,
    temperature: float = 0.3,
    enable_tools: bool = True,
    resume_task_id: TaskId | None = None,
    thread_id: str | None = None,
    max_tokens_budget: int = BUDGET_DEFAULT_MAX_TOKENS,
    max_usd_budget: float = BUDGET_DEFAULT_MAX_USD,
    approval_provider: ApprovalProvider | None = None,
    step_evaluator: Callable[[dict[str, Any]], Any] | None = None,
) -> ReActResult | None:
    gen = stream_react_loop(
        stack,
        intent,
        agent,
        model=model,
        max_iterations=max_iterations,
        temperature=temperature,
        enable_tools=enable_tools,
        resume_task_id=resume_task_id,
        thread_id=thread_id or "",
        max_tokens_budget=max_tokens_budget,
        max_usd_budget=max_usd_budget,
        approval_provider=approval_provider,
        step_evaluator=step_evaluator,
    )
    try:
        while True:
            next(gen)
    except StopIteration as stop:
        return stop.value  # type: ignore[no-any-return]
