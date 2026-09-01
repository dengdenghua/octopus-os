"""PHASE 6g + 6d — loop-tail housekeeping and pre-dispatch guard cluster
for the ReAct loop.

Phase-6g half extracted from ``react_execution.py`` (Wave 2): implements
``_phase_6g_housekeeping`` — plan-exit, checkpoints, message append, length
continuation, and context compression. Phase-6d-guard half extracted from
``_react_execution_phase6d.py`` (Wave 2b): implements
``_phase_6d_pre_dispatch_guards`` — the approval/guard checks that run
*before* a step's action(s) are dispatched. Neither this module nor its
imports form a cycle — it imports from react_* leaf modules and the sibling
``_react_execution_progress`` submodule, never react_loop or react_execution.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable
from typing import Any

from runtime.core.cerebrum._react_execution_progress import (
    _build_progress_summary,
    _detect_phase,
    _update_working_set,
)
from runtime.core.cerebrum.react_context import (
    _compress_context,
    _estimate_messages_tokens,
    _serialize_messages_for_checkpoint,
    context_budget_tokens_for_model,
    context_compaction_message_target_tokens,
)
from runtime.core.cerebrum.react_convergence import (
    constrain_explicit_read_scope,
)
from runtime.core.cerebrum.react_explicit_reads import (
    _bound_explicit_large_reads,
)
from runtime.core.cerebrum.react_final_answer_content_guards import (
    _incomplete_final_answer_guard,
)
from runtime.core.cerebrum.react_guards import (
    _code_semantic_followup_guard,
    _goal_requests_code_mutation,
)
from runtime.core.cerebrum.react_loop_state import (
    _LoopControl,
    _LoopState,
)
from runtime.core.cerebrum.react_parsing import (
    _has_code_verification,
    _is_code_write_step,
    _parse_action,
)
from runtime.core.cerebrum.react_types import (
    REACT_OBSERVATION_FOLLOWUP,
    ReActStep,
)
from runtime.core.cerebrum.todo_protocol import (
    _todo_completion_before_write_guard,
    _todo_prewrite_guard,
    _todo_reconciliation_guard,
)
from runtime.platform.models import Step
from runtime.platform.models.llm import Message

_logger = logging.getLogger(__name__)

# Number of consecutive "blank" iterations (no tool call, no observation,
# no meaningful thought, no final answer) before the model-spin guard pauses
# the turn with a clear reason instead of letting the loop burn through all
# remaining iterations up to the generic near-limit auto-pause.
_SPIN_BAIL_AT = 3
_MAX_AUTO_ITERATION_EXTENSIONS = 2


def _step_has_successful_tool_evidence(step: ReActStep) -> bool:
    """Return whether one step added successful, externally grounded work."""
    if step.action_results:
        return any(result.get("ok") is True for result in step.action_results)
    action = (step.action or "").strip().lower()
    observation = (step.observation or "").strip()
    return bool(action and action not in {"none", "n/a"} and observation and observation != "N/A")


def _is_making_iteration_progress(state: _LoopState) -> bool:
    """Conservatively detect progress before extending a turn in place.

    Successful tool receipts are the strongest available signal. Requiring
    more than one distinct recent action prevents a single repeated search or
    verifier from turning the bounded extension into an unbounded loop.
    """
    if (
        state.consecutive_spin_iterations
        or state.consecutive_same_failed_actions >= 2
        or state.consecutive_same_noop_actions >= 2
    ):
        return False
    recent = state.steps[-5:]
    productive = [step for step in recent if _step_has_successful_tool_evidence(step)]
    if len(productive) < 3:
        return False
    fingerprints = {
        " ".join((step.action or "").split()).casefold()
        for step in productive
        if (step.action or "").strip()
    }
    return len(fingerprints) >= 2


def _auto_extend_iteration_limit(state: _LoopState, max_iterations: int) -> int:
    """Return a bounded extended limit when the recent trajectory is healthy."""
    if state.iteration_extensions_used >= _MAX_AUTO_ITERATION_EXTENSIONS:
        return max_iterations
    if not _is_making_iteration_progress(state):
        return max_iterations
    base_limit = state.iteration_base_limit or max_iterations
    extension = max(10, base_limit // 2)
    extended_limit = max_iterations + extension
    state.iteration_base_limit = base_limit
    state.iteration_limit = extended_limit
    state.iteration_extensions_used += 1
    task_id = str(state.react_task_id or "")
    if task_id:
        with contextlib.suppress(Exception):
            state.pause_controller.update_active_iteration_limit(task_id, extended_limit)
    return extended_limit


def _phase_6g_housekeeping(state: _LoopState, *, i: int, max_iterations: int) -> _LoopControl:
    """Loop-tail housekeeping: plan-exit, checkpoints, msg append, compress.

    Moved verbatim from ``react_loop.py`` (PHASE 6g). Returns ``BREAK``
    when a final answer terminates the turn (Python ``break`` in the
    original), otherwise ``CONTINUE`` so the loop proceeds to the next
    iteration. No yields — plain function, not a generator.
    """
    # Reference-typed aliases — mutations propagate to the main loop.
    steps = state.steps
    final_answer_segments = state.final_answer_segments
    messages = state.messages
    _working_set = state.working_set
    stack = state.stack
    react_task_id = state.react_task_id
    router = state.router
    thread_id = state.thread_id
    resp = state.resp
    step = state.step
    assert step is not None, "phase 6g requires a parsed ReAct step"
    _pause = state.pause_controller
    # Scalar pulls — original local names; pushed back in the finally.
    planning_mode = state.planning_mode
    enable_tools = state.enable_tools
    executor = state.executor
    tools_active = state.tools_active
    maybe_final = state.maybe_final
    final_answer = state.final_answer
    final_answer_emitted = state.final_answer_emitted
    terminated_reason = state.terminated_reason
    effective_model = state.effective_model
    _current_phase = state.current_phase
    _progress_summary = state.progress_summary
    _force_convergence_next = state.force_convergence_next
    _length_limit_should_continue = state.length_limit_should_continue
    _is_code_mode = state.is_code_mode
    _native_mode = state.native_mode
    _observed_read_sequence = state.observed_read_sequence
    _length_limited = state.length_limited
    _final_delta_emitted_this_iteration = state.final_delta_emitted_this_iteration
    text = state.text
    _agent_id_for_pause = state.agent_id_for_pause
    _consecutive_spin_iterations = state.consecutive_spin_iterations
    try:
        # Mid-turn plan exit: model called exit_plan_mode and user approved.
        # Switch from "plan only" to "execute" without ending the turn.
        if planning_mode:
            try:
                from runtime.platform.process.session import current_session as _cs_plan

                _session_obj = _cs_plan()
            except (ImportError, AttributeError):  # noqa: BLE001
                _session_obj = None
            if (
                _session_obj is not None
                and _session_obj.metadata is not None
                and _session_obj.metadata.pop("_plan_mode_exit_approved", False)
            ):
                planning_mode = False
                enable_tools = True
                executor = getattr(stack, "executor", None)
                tools_active = executor is not None
                _logger.info(
                    "plan_mode exited mid-turn; continuing execution in same turn",
                )

        if _is_code_mode and step.action and step.action.lower() not in {"none", "n/a", ""}:
            _update_working_set(_working_set, step, _current_phase)
            _current_phase = _detect_phase(step, _current_phase)
            _progress_summary = _build_progress_summary(steps, _working_set, _current_phase)

        _has_real_observation = bool(step.observation and step.observation != "N/A")
        _has_response_tool_calls = bool(getattr(resp, "tool_calls", None))
        _length_limit_should_continue = _length_limited and not (
            _has_response_tool_calls or _has_real_observation
        )
        _checkpoint_has_final = maybe_final is not None and not _length_limit_should_continue
        if react_task_id is not None and _checkpoint_has_final:
            _ckpt_journal = getattr(stack, "journal", None)
            if _ckpt_journal is not None and hasattr(_ckpt_journal, "write_react_checkpoint"):
                try:
                    from runtime.platform.models import ArmId

                    _ckpt_journal.write_react_checkpoint(
                        react_task_id,
                        arm_id=ArmId("react_arm"),
                        iteration_completed=i + 1,
                        max_iterations=max_iterations,
                        messages_snapshot=_serialize_messages_for_checkpoint(messages),
                        steps_snapshot=[
                            {
                                "iteration": s.iteration,
                                "thought": s.thought,
                                "public_update": s.public_update,
                                "action": s.action,
                                "actions": list(s.actions),
                                "observation": s.observation,
                                "action_results": [dict(result) for result in s.action_results],
                            }
                            for s in steps
                        ],
                        has_final_answer=_checkpoint_has_final,
                        final_answer=maybe_final if _checkpoint_has_final else "",
                        working_set_snapshot=list(_working_set.values()),
                        progress_summary=_progress_summary,
                        current_phase=_current_phase,
                    )
                except (OSError, TypeError):
                    _logger.debug("checkpoint write failed", exc_info=True)
        if maybe_final and _length_limit_should_continue:
            final_answer_segments.append(maybe_final)
            maybe_final = None

        if maybe_final:
            if final_answer_segments:
                final_answer = "".join(final_answer_segments + [maybe_final])
                final_answer_segments.clear()
            else:
                final_answer = maybe_final
            # A guarded long-task answer may have been intentionally buffered
            # until every completion gate passed.  Only suppress the final
            # emitter when this iteration actually yielded answer text.
            final_answer_emitted = _final_delta_emitted_this_iteration
            terminated_reason = "final_answer"
            return _LoopControl.BREAK

        # ── Model-spin guard ─────────────────────────────────────────
        # A step that carries no tool call, no observation, no meaningful
        # thought and no final answer is "blank". When the upstream model
        # degrades into emitting only empty reasoning (spaces/whitespace)
        # it burns through every remaining iteration until the near-limit
        # auto-pause fires. Detect the runaway early and pause with a clear
        # reason so the user can switch models instead of waiting it out.
        #
        # A degraded model (e.g. kimi-k3) often pairs that empty reasoning
        # with a *promise-style* final answer ("我这就开始…支撑结论") that the
        # completeness guard rejects. Such a ``maybe_final`` is not real work
        # and must NOT reset the spin counter — otherwise the spin guard never
        # fires and the model churns to the generic near-limit pause.
        _has_real_obs = bool(
            step.observation and step.observation.strip() and step.observation != "N/A"
        )
        _spin_relevant_final = bool(
            maybe_final and _incomplete_final_answer_guard(maybe_final) is None
        )
        _is_blank_step = (
            not step.actions
            and not _has_real_obs
            and not (step.thought and step.thought.strip())
            and not (text and text.strip())
            and not _spin_relevant_final
        )
        if _is_blank_step:
            _consecutive_spin_iterations += 1
        else:
            _consecutive_spin_iterations = 0
        state.consecutive_spin_iterations = _consecutive_spin_iterations

        if (
            _consecutive_spin_iterations >= _SPIN_BAIL_AT
            and react_task_id is not None
            and not _pause.is_pause_requested(str(react_task_id))
        ):
            # Capability-enhancing spin escalation: instead of pausing a
            # spinning turn immediately, first force a context-compression
            # pass, then attempt a model switch. Only when both fail to break
            # the spin do we pause with a clear reason.
            _spin_stage = state.spin_escalation_stage
            if _spin_stage == 0:
                # Stage 1: force convergence/compression on the next iteration.
                _force_convergence_next = True
                state.spin_escalation_stage = 1
                _consecutive_spin_iterations = 0
                state.consecutive_spin_iterations = 0
                _logger.warning(
                    "react_loop spin-guard stage 1·compress at iter %d · task %s · "
                    "%d consecutive blank steps",
                    i + 1,
                    react_task_id,
                    _consecutive_spin_iterations,
                )
            elif _spin_stage == 1:
                # Stage 2: request a model switch; the main loop consumes the
                # flag before the next LLM call.
                state.spin_model_switch_requested = True
                state.spin_escalation_stage = 2
                _consecutive_spin_iterations = 0
                state.consecutive_spin_iterations = 0
                _logger.warning(
                    "react_loop spin-guard stage 2·switch-model at iter %d · task %s · "
                    "%d consecutive blank steps",
                    i + 1,
                    react_task_id,
                    _consecutive_spin_iterations,
                )
            else:
                # Stage 3: exhausted escalation — pause with a clear reason so
                # the user can intervene instead of burning remaining iterations.
                _logger.warning(
                    "react_loop spin-guard at iter %d · task %s · %d consecutive blank steps",
                    i + 1,
                    react_task_id,
                    _consecutive_spin_iterations,
                )
                _pause.request_pause(
                    task_id=str(react_task_id),
                    reason="model_spinning",
                    requested_by="system",
                    note=(
                        f"模型空转 · 连续 {_consecutive_spin_iterations} 轮未产出有效动作，"
                        f"已自动暂停 · 可切换模型或补充信息后继续"
                    ),
                    thread_id=thread_id or "",
                    agent_id=_agent_id_for_pause,
                )

        if (
            react_task_id is not None
            and max_iterations >= 15
            and (max_iterations - (i + 1)) <= 3
            and not _pause.is_pause_requested(str(react_task_id))
        ):
            remaining = max_iterations - (i + 1)
            _extended_limit = _auto_extend_iteration_limit(state, max_iterations)
            if _extended_limit > max_iterations:
                _logger.info(
                    "react_loop auto-extended at iter %d · task %s · %d -> %d (%d/%d grants)",
                    i + 1,
                    react_task_id,
                    max_iterations,
                    _extended_limit,
                    state.iteration_extensions_used,
                    _MAX_AUTO_ITERATION_EXTENSIONS,
                )
            else:
                _logger.info(
                    "react_loop auto-pause at iter %d · task %s · %d left · "
                    "will checkpoint next loop top",
                    i + 1,
                    react_task_id,
                    remaining,
                )
                _pause.request_pause(
                    task_id=str(react_task_id),
                    reason="iteration_near_limit",
                    requested_by="system",
                    note=(
                        f"自动暂停 · 已跑 {i + 1}/{max_iterations} 轮 · "
                        f"剩余 {remaining} 轮 · 点继续并加预算可接续"
                    ),
                    thread_id=thread_id or "",
                    agent_id=_agent_id_for_pause,
                )

        _assistant_content = text
        if _native_mode and step.action and getattr(resp, "tool_calls", None):
            # A native tool round is one atomic assistant action. Providers
            # sometimes attach answer-like prose to that same response; it is
            # neither a terminal answer nor safe history. Record only the
            # synthesised structured action so later rounds cannot reinforce
            # an unsupported pre-tool completion claim.
            _assistant_content = step.action
        messages.append(Message(role="assistant", content=_assistant_content))
        # Length-limit continuation. When the upstream model truncated
        # its response (finish_reason=="length" / "max_tokens" / etc.)
        # the assistant message we just appended is mid-sentence — the
        # model itself doesn't know it stopped early, so on the NEXT
        # iteration it will either repeat work or give up and write a
        # short summary. Inject a user message asking it to continue
        # exactly where it left off so long-form generation (research
        # reports, code files, plans) can finish across multiple
        # iterations without the user seeing a half-finished doc.
        if _length_limit_should_continue:
            _code_action_recovery = _is_code_mode and not final_answer_segments
            if _code_action_recovery:
                _force_convergence_next = True
                _length_recovery_prompt = (
                    "Your previous code-task response hit the output limit before producing an "
                    "executable action. Do not continue or repeat the prose analysis. Extended "
                    "thinking is disabled for this recovery round. Emit exactly one concrete next "
                    "Action: skill_name({JSON}) now; prefer the required source/test mutation, or "
                    "the smallest targeted verifier if the implementation is already written."
                )
            else:
                _length_recovery_prompt = (
                    "Your previous response was cut off by the output "
                    "length limit. Continue exactly where it stopped — "
                    "do NOT repeat earlier text, do NOT restart the "
                    "report, do NOT switch to writing a summary or "
                    "calling todo_write. Resume from the exact "
                    "character you stopped at and finish every "
                    "remaining section."
                )
            messages.append(
                Message(
                    role="user",
                    content=_length_recovery_prompt,
                )
            )
            _logger.info(
                "react_loop iter %d · finish_reason=length, injecting continue prompt",
                i + 1,
            )
        elif step.observation and step.observation != "N/A":
            # TokenJuice: compress the observation before it enters
            # the message stream so the next LLM round sees a leaner
            # version. The full observation is preserved in
            # step.observation for journal / display / guards. On
            # by default — opt out via ECHO_TOKEN_JUICE=0.
            _obs_for_model = step.observation
            try:
                from runtime.core.cerebrum.token_juicer import (
                    is_enabled as _juice_enabled,
                )
                from runtime.core.cerebrum.token_juicer import (
                    juice as _juice,
                )

                if _observed_read_sequence or _juice_enabled():
                    _juiced, _stats = _juice(
                        step.observation,
                        max_chars=6000,
                    )
                    if _stats.passes:
                        _obs_for_model = _juiced
                        (_logger.info if _observed_read_sequence else _logger.debug)(
                            "token_juice iter %d · %d→%d chars (%.1f%% saved) passes=%s",
                            i + 1,
                            _stats.before,
                            _stats.after,
                            (1 - _stats.ratio) * 100,
                            ",".join(_stats.passes),
                        )
            except (ImportError, ValueError, TypeError):
                _logger.debug("token_juice unavailable", exc_info=True)
            messages.append(
                Message(
                    role="user",
                    content=(f"Observation: {_obs_for_model}\n\n{REACT_OBSERVATION_FOLLOWUP}"),
                )
            )

        # dsh repeat-tool-reminder: fold queued guard notices into the model
        # context right after this step's tool results, before the next LLM
        # round (the deny fast paths flushed theirs via
        # ``_flush_guard_notices``; this drain covers every other exit).
        _guard_notices = getattr(state, "guard_notices", None)
        if _guard_notices:
            for _guard_notice in _guard_notices:
                messages.append(Message(role="user", content=_guard_notice))
            _guard_notices.clear()

        _context_capacity = context_budget_tokens_for_model(effective_model)
        _context_before = _estimate_messages_tokens(messages)
        # The provider's preceding input usage is the only measurement that
        # includes native tool schemas.  A long tool turn can therefore be at
        # 99% upstream pressure while message-only estimation still says 40%.
        _provider_context_tokens = 0
        if react_task_id is not None:
            with contextlib.suppress(Exception):
                _provider_context_tokens = next(
                    (
                        int(task.current_context_tokens or 0)
                        for task in _pause.list_active()
                        if task.task_id == str(react_task_id)
                    ),
                    0,
                )
        # Compact before the request is actually at the provider cliff. At
        # 80% pressure, target a 60% working set and preserve the remaining
        # runway for tool schemas, the next observation and final synthesis.
        _context_target = context_compaction_message_target_tokens(
            _context_before,
            provider_context_tokens=_provider_context_tokens,
            capacity_tokens=_context_capacity,
        )
        messages = _compress_context(
            messages,
            max_tokens=_context_target,
            router=router,
            model=effective_model,
            is_code_mode=_is_code_mode,
            progress_summary=_progress_summary,
            current_phase=_current_phase,
            working_set=_working_set,
        )
        _context_after = _estimate_messages_tokens(messages)
        if _context_after < _context_before:
            _logger.info(
                "react context compacted · iter %d · %d -> %d tokens · "
                "capacity=%d target=%d strategy=%s",
                i + 1,
                _context_before,
                _context_after,
                _context_capacity,
                _context_target,
                "deterministic_code_state" if _is_code_mode else "summary_or_trim",
            )

        with contextlib.suppress(Exception):
            _pause.update_active_iteration(str(react_task_id), i + 1)
        return _LoopControl.CONTINUE
    finally:
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


def _phase_6d_pre_dispatch_guards(
    step: ReActStep,
    steps: list[ReActStep],
    intent: Any,
    i: int,
    *,
    tools_active: bool,
    _effective_wp: Any,
    _read_only_turn: bool,
    _observed_read_sequence: Any,
    _consecutive_same_failed_actions: int,
    _last_failed_action_fingerprint: str,
    _consecutive_same_noop_actions: int,
    _last_noop_action_fingerprint: str,
    _evidence_convergence_active: Any,
    _todo_protocol_required: bool,
    _is_goal_mode: bool,
    _todo_protocol_visible: bool,
    _is_code_mode: bool,
    _green_verification_convergence_active: bool,
    _green_convergence_todo_used: bool,
    maybe_final: Any,
    _force_convergence_next: bool,
    _deduplicate_actions: Callable[..., Any],
    _action_batch_fingerprint: Callable[..., str],
) -> tuple[Any, ...]:
    """Run the pre-dispatch guards and return the updated scalar locals.

    ``step`` is mutated in place. The returned tuple carries, in order:
    ``observation``, ``resolved_name``, ``action_args``, ``beak_step``,
    ``tool_ok``, ``tool_action_requested``, ``maybe_final``,
    ``_force_convergence_next``, ``_green_convergence_todo_used``,
    ``_duplicate_action_count``, ``_explicit_read_scope_note``,
    ``_current_action_fingerprint``, ``_repeated_failure_skipped``,
    ``_repeated_noop_skipped``.
    """
    observation: str | None = step.observation or None
    resolved_name: str | None = None
    action_args: dict[str, Any] | None = None
    beak_step: Step | None = None
    tool_ok = False
    tool_action_requested = (
        tools_active and step.action and step.action.lower() not in {"none", "n/a", ""}
    )
    _duplicate_action_count = 0
    _explicit_read_scope_note = ""
    if tool_action_requested and len(step.actions) > 1:
        step.actions, _duplicate_action_count = _deduplicate_actions(step.actions)
        step.action = "; ".join(step.actions)
        tool_action_requested = bool(step.actions)
    if tool_action_requested:
        _candidate_actions = step.actions or [step.action]
        _candidate_actions = _bound_explicit_large_reads(
            goal=intent.normalized_goal,
            workspace_path=(_effective_wp if isinstance(_effective_wp, str) else None),
            actions=_candidate_actions,
            read_only=_read_only_turn,
        )
        step.actions = _candidate_actions
        step.action = "; ".join(_candidate_actions)
        _scope_constraint = constrain_explicit_read_scope(
            goal=intent.normalized_goal,
            steps=steps,
            actions=_candidate_actions,
            read_only=_read_only_turn,
            enforce_order=_observed_read_sequence,
        )
        if _scope_constraint is not None:
            step.actions = list(_scope_constraint.actions)
            step.action = "; ".join(step.actions)
            _explicit_read_scope_note = _scope_constraint.observation_note()
            tool_action_requested = bool(step.actions)
            if not tool_action_requested:
                observation = _explicit_read_scope_note
                step.observation = observation
                maybe_final = None
    _current_action_fingerprint = ""
    _repeated_failure_skipped = False
    if tool_action_requested:
        _current_action_fingerprint = _action_batch_fingerprint(step.actions or [step.action])
        if (
            _consecutive_same_failed_actions >= 2
            and _current_action_fingerprint == _last_failed_action_fingerprint
        ):
            observation = (
                "[repeated-failing-tool-skipped] The same tool call or ordered tool batch "
                "with identical arguments already failed twice, so the runtime did not "
                "execute it a third time. Treat the prior failure as definitive. Choose a different "
                "action: for a missing file, create it with an allowed write tool; for "
                "invalid arguments, correct them; otherwise use a different evidence source."
            )
            step.observation = observation
            step.action = ""
            step.actions = []
            tool_action_requested = False
            maybe_final = None
            _repeated_failure_skipped = True
    _repeated_noop_skipped = False
    if (
        tool_action_requested
        and not _repeated_failure_skipped
        and _consecutive_same_noop_actions >= 2
        and _current_action_fingerprint == _last_noop_action_fingerprint
    ):
        observation = (
            "[repeated-noop-tool-skipped] The same tool call with identical "
            "arguments already ran twice but produced no effect (ok=True but "
            "empty/zero-count result). The runtime did not execute it a third "
            "time. The arguments are likely under a wrong key — re-read the "
            "tool description and re-issue with the correct parameter names."
        )
        step.observation = observation
        step.action = ""
        step.actions = []
        tool_action_requested = False
        maybe_final = None
        _repeated_noop_skipped = True
    if _evidence_convergence_active is not None and tool_action_requested:
        observation = (
            "The read-only evidence requested by the user is already complete, so "
            "the runtime did not execute this additional tool call. Answer now from "
            "the recorded observations; do not broaden the search or call another tool."
        )
        step.observation = observation
        step.action = ""
        step.actions = []
        tool_action_requested = False
        maybe_final = None
        _force_convergence_next = True
    if tool_action_requested:
        _todo_prewrite_message = _todo_prewrite_guard(
            step.actions or [step.action],
            steps,
            # Keep bounded inspections and one-command probes lightweight.
            # ReAct's plan-first gate applies to genuinely long or explicit
            # goal-mode work; the native tool bridge enforces its own
            # equivalent bootstrap from the shared protocol classifier.
            required=(
                _todo_protocol_required
                and (
                    _is_goal_mode
                    or "\n" in intent.normalized_goal
                    or len(intent.normalized_goal) >= 80
                )
            ),
            visible=_todo_protocol_visible,
        )
        if _todo_prewrite_message:
            observation = _todo_prewrite_message
            step.observation = observation
            step.action = ""
            step.actions = []
            tool_action_requested = False
            maybe_final = None
    if _is_code_mode and tool_action_requested:
        _premature_todo_completion = _todo_completion_before_write_guard(
            step.actions or [step.action],
            steps,
            required=_goal_requests_code_mutation(intent.normalized_goal),
        )
        if _premature_todo_completion:
            observation = _premature_todo_completion
            step.observation = observation
            step.action = ""
            step.actions = []
            tool_action_requested = False
            maybe_final = None
    if tool_action_requested:
        _todo_reconciliation_message = _todo_reconciliation_guard(
            step.actions or [step.action],
            steps,
            required=_todo_protocol_required,
            visible=_todo_protocol_visible,
        )
        if _todo_reconciliation_message:
            observation = _todo_reconciliation_message
            step.observation = observation
            step.action = ""
            step.actions = []
            tool_action_requested = False
            maybe_final = None
    if _is_code_mode and tool_action_requested:
        # A deterministic source-level concurrency defect is stronger
        # evidence than another green/red probe.  Do not let providers
        # evade the repair instruction by cycling through pytest, lint,
        # typecheck, or shell variants.  Reads and actual code writes stay
        # available; a write+verify batch is also allowed because the
        # ordered outcome tracker will evaluate the post-repair checks.
        _semantic_repair = _code_semantic_followup_guard(
            steps,
            is_code_mode=True,
        )
        if _semantic_repair:
            _candidate_steps = [
                ReActStep(iteration=i + 1, action=_candidate)
                for _candidate in (step.actions or [step.action])
            ]
            _candidate_has_write = any(
                _is_code_write_step(_candidate_step) for _candidate_step in _candidate_steps
            )
            _candidate_has_verifier = any(
                _has_code_verification([_candidate_step]) for _candidate_step in _candidate_steps
            )
            if _candidate_has_verifier and not _candidate_has_write:
                observation = (
                    "[semantic-repair-tool-skipped] A deterministic source defect "
                    "is still present in the latest source edit, so the runtime did not "
                    "execute another verifier or shell probe. Repair the source first.\n"
                    + _semantic_repair
                )
                step.observation = observation
                step.action = ""
                step.actions = []
                tool_action_requested = False
                maybe_final = None
                _force_convergence_next = True
    if _green_verification_convergence_active and tool_action_requested:
        _candidate_actions = step.actions or [step.action]
        _candidate_names = []
        for _candidate_action in _candidate_actions:
            _candidate_parsed = _parse_action(_candidate_action)
            if _candidate_parsed is not None:
                _candidate_names.append(_candidate_parsed[0])
        _allow_one_todo = (
            bool(_candidate_names)
            and all(name == "todo_write" for name in _candidate_names)
            and not _green_convergence_todo_used
        )
        if _allow_one_todo:
            _green_convergence_todo_used = True
        else:
            # Two independent green verification rounds after the latest
            # write are sufficient evidence. Re-running read/test/lint or
            # shell probes only burns the turn budget and can turn a valid
            # implementation into a timeout. Suppress those actions while
            # preserving one checklist-finalization opportunity.
            observation = (
                "[redundant-tool-skipped] Two separate verification rounds are already green "
                "and no code changed afterward. This tool call was not executed. Do not call "
                "another tool. Emit `Final Answer:` now with the recorded test/lint evidence."
            )
            step.observation = observation
            step.action = ""
            step.actions = []
            tool_action_requested = False
            maybe_final = None
            _force_convergence_next = True

    return (
        observation,
        resolved_name,
        action_args,
        beak_step,
        tool_ok,
        tool_action_requested,
        maybe_final,
        _force_convergence_next,
        _green_convergence_todo_used,
        _duplicate_action_count,
        _explicit_read_scope_note,
        _current_action_fingerprint,
        _repeated_failure_skipped,
        _repeated_noop_skipped,
    )
