"""PHASE 6c of the ReAct main loop: parse step / format-violation check.

Moved from ``react_loop.py`` (Wave 2). The phase runs as a generator
driven with ``yield from`` and returns a ``_LoopControl`` signal:
``CONTINUE`` to proceed to PHASE 6d, ``BREAK`` to leave the iteration
loop (``terminated_reason`` / ``final_answer`` already on the state),
``RETURN_NONE`` to abort the turn (trajectory persist + pause
unregister already performed). Scalar loop variables are pulled from
``_LoopState`` into same-named locals on entry and pushed back in a
``finally`` so the moved body stays verbatim; reference-typed state
(steps, executed_beak_steps, guard_impasse_state) mutates in place.

``try_react_model_failover`` and ``maybe_emit_throughput`` are injected
by the caller: the former is a react_loop-local closure that must keep
resolving ``next_custom_model_fallback`` through the react_loop module
global (tests patch it there).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Generator
from typing import Any

from runtime.core.cerebrum.react_auto_verify import (
    _try_auto_verification_salvage,
)
from runtime.core.cerebrum.react_convergence import (
    build_direct_answer_directive,
)
from runtime.core.cerebrum.react_execution import (
    _persist_react_trajectory,
)
from runtime.core.cerebrum.react_explicit_reads import (
    _recover_explicit_read_actions,
)
from runtime.core.cerebrum.react_final_answer_guards import (
    _evaluate_final_answer_guards,
    _final_answer_needs_pre_emit_guard,
    _guard_impasse_final_answer,
    _guard_rejection_outcome,
    _guard_repair_feedback,
    _looks_like_observation_echo,
    _try_clean_downgrade,
    _unfinished_implementation_recovery_needed,
)
from runtime.core.cerebrum.react_loop_controls import _emit_assistant_chunk
from runtime.core.cerebrum.react_loop_state import (
    _LoopControl,
    _LoopState,
)
from runtime.core.cerebrum.react_model_deadlines import (
    _finish_reason_is_length_limited,
    _model_stall_handoff_answer,
    _stage_update_timeout_fallback,
)
from runtime.core.cerebrum.react_native import (
    step_from_tool_calls,
)
from runtime.core.cerebrum.react_parsing import (
    _FINAL_RE,
    _is_format_violation,
    _looks_like_special_tool_envelope,
    _looks_like_unfinished_work,
    _parse_reasoning_action_fallback,
    _parse_step,
)
from runtime.core.cerebrum.react_types import (
    _native_tool_calls_missing_required_args,
)
from runtime.platform.models.rescue_policy import note_model_stall

_logger = logging.getLogger(__name__)


def _next_zero_action_rounds(
    current: int,
    *,
    step: Any,
    maybe_final: Any,
    final_answer_emitted: bool,
) -> int:
    """Consecutive count of rounds that neither acted nor concluded.

    Drives ``ModelRequest.require_tool_use`` on the following request. A round
    that emitted an action resets the count even if that action *failed*: the
    model is engaging with the tools rather than narrating around them, which
    is the behaviour the forcing exists to restore. Concluding the turn also
    resets, so a legitimate prose answer is never treated as a deficit.
    """
    if step is not None and (getattr(step, "action", "") or "").strip():
        return 0
    if maybe_final or final_answer_emitted:
        return 0
    return current + 1


def _zero_action_protocol_reminder(step: Any, consecutive_format_violations: int) -> str | None:
    """Corrective observation for an act-less, conclusion-less round.

    Two narration shapes need it:

    * the step published an ``Update:`` line but no ``Action:`` (Kimi K3
      style - stops after the progress line);
    * plain narration with neither anchor (GLM-5.3 style: "我来查一下…"
      prose that never mentions the protocol). The Update-only reminder
      misses this shape, and without any corrective observation the next
      round sees the model's own prose as the last word, repeats it, and
      the two-strike bail ends the turn with zero tool executions while
      the user was promised action.
    """
    if getattr(step, "action", ""):
        return None
    if getattr(step, "observation", None):
        return None
    if getattr(step, "public_update", None):
        return (
            "[protocol-reminder] Your previous turn published an Update but "
            "did not emit an Action tool call. The task is not complete. "
            'Emit exactly one Action: skill_name({"arg": "value"}) now '
            "to make progress - do not write another Update without an Action."
        )
    if consecutive_format_violations:
        return (
            "[protocol-reminder] Your previous reply narrated what you "
            "intend to do but executed nothing - narration is not action. "
            'Emit exactly one Action: skill_name({"arg": "value"}) now, '
            "or, if and only if the task is already complete, a "
            "Final Answer."
        )
    return None


def _phase_6c_parse_and_guard(
    state: _LoopState,
    *,
    resp: Any,
    raw_text: str,
    i: int,
    request_has_tool_evidence: bool,
    iteration_soft_timed_out: bool,
    try_react_model_failover: Callable[[str], str | None],
    maybe_emit_throughput: Callable[[int], dict[str, Any] | None],
) -> Generator[dict[str, Any], None, _LoopControl]:
    """Parse the model response into a step and run the guard machinery."""
    # Reference-typed aliases — mutations propagate to the main loop.
    steps = state.steps
    executed_beak_steps = state.executed_beak_steps
    stack = state.stack
    react_task_id = state.react_task_id
    goal = state.goal
    executor = state.executor
    effective_wp = state.effective_wp
    _guard_impasse_state = state.guard_impasse_state
    _pause = state.pause_controller
    # Scalar pulls — identical names to the original loop body so the
    # moved code stays verbatim; pushed back in the finally below.
    _native_mode = state.native_mode
    _evidence_convergence_active = state.evidence_convergence_active
    _model_timeout_recoveries = state.model_timeout_recoveries
    _final_stream_started = state.final_stream_started
    _force_convergence_next = state.force_convergence_next
    consecutive_format_violations = state.consecutive_format_violations
    _format_violation_bail_at = state.format_violation_bail_at
    _throughput_chars = state.throughput_chars
    final_answer = state.final_answer
    terminated_reason = state.terminated_reason
    final_answer_emitted = state.final_answer_emitted
    _final_delta_emitted_this_iteration = state.final_delta_emitted_this_iteration
    _todo_protocol_required = state.todo_protocol_required
    _todo_protocol_visible = state.todo_protocol_visible
    _is_code_mode = state.is_code_mode
    _browser_operation_mode = state.browser_operation_mode
    _file_inspection_tools_visible = state.file_inspection_tools_visible
    tools_active = state.tools_active
    _read_only_turn = state.read_only_turn
    _no_tool_turn = state.no_tool_turn
    _final_guard_grounded_source_paths = state.final_guard_grounded_source_paths
    # Injected per-iteration inputs under their original names.
    _request_has_tool_evidence = request_has_tool_evidence
    _iteration_soft_timed_out = iteration_soft_timed_out
    _try_react_model_failover = try_react_model_failover
    _maybe_emit_throughput = maybe_emit_throughput
    # Outputs consumed by 6d–6g; assigned unconditionally below.
    step: Any = None
    maybe_final: str | None = None
    text = ""
    _length_limited = False
    _length_limit_should_continue = False
    try:
        text = (resp.text or raw_text or "").strip()
        resp_thinking = (getattr(resp, "thinking", "") or "").strip()
        if _native_mode and resp is not None and getattr(resp, "tool_calls", None):
            # Native tool-use: read the action straight off the structured
            # tool_calls instead of regex-parsing it out of free text. Only
            # falls through to the text parser when the model returned no
            # tool calls (i.e. it produced a final answer).
            step = step_from_tool_calls(
                resp.tool_calls,
                text=resp.text or "",
                thinking=getattr(resp, "thinking", "") or "",
                iteration=i + 1,
                evidence_round=_request_has_tool_evidence,
            )
            maybe_final = None
            _missing_native_args = _native_tool_calls_missing_required_args(resp.tool_calls)
            if _missing_native_args:
                # Some OpenAI-compatible reasoning providers surface a tool
                # name from their private XML envelope but drop its JSON
                # arguments. Executing that call only creates misleading
                # "missing path/command" failures. Fall back to the explicit
                # ReAct wire format for the next round, where the ordinary
                # parser can recover a complete Action payload.
                # Dual-write: the injected failover closure reads the main
                # loop's ``_native_mode`` local mid-call via a wrapper that
                # re-syncs from state, so the flip must land on state now.
                _native_mode = state.native_mode = False
                step.action = ""
                step.actions = []
                step.action_results = []
                step.observation = (
                    "[tool-call-protocol-error] The provider emitted native "
                    "tool call(s) without required JSON arguments: "
                    + ", ".join(_missing_native_args)
                    + ". Nothing was executed. Retry on the next round using "
                    "exactly Action: skill_name({JSON arguments}); include every "
                    "required path, command, code, query, or content field."
                )
        else:
            step, maybe_final = _parse_step(text, iteration=i + 1)
            if not text and resp_thinking:
                reasoning_step = _parse_reasoning_action_fallback(
                    resp_thinking,
                    iteration=i + 1,
                )
                if reasoning_step is not None:
                    step = reasoning_step
                    maybe_final = None
        if _looks_like_special_tool_envelope(text) and not step.actions and not step.action:
            # The provider exposed a private tool sentinel but supplied no
            # structured call.  Make the failure an Observation so the next
            # model round repairs its syntax instead of ending the user turn
            # with raw control tokens and zero executed tools.
            step.observation = (
                "[tool-call-protocol-error] Provider emitted a tool-call envelope "
                "without an executable tool name and JSON arguments. No tool was "
                "executed. Retry now using Action: skill_name({JSON}); do not narrate "
                "the intended call or repeat the private <|tool_calls_*|> markers."
            )
            maybe_final = None
        if (
            _looks_like_observation_echo(text)
            and not step.observation
            and not step.action
            and maybe_final is None
        ):
            step.observation = text
        if (
            _iteration_soft_timed_out
            and maybe_final is None
            and (not step.action or _evidence_convergence_active is not None)
        ):
            # Remember this model as recently stalled BEFORE the failover/break
            # decision so the next turn's rescue no longer re-selects the exact
            # model (or its same-upstream sibling) that just overran its
            # deadline — this is the cross-turn escalation that breaks the
            # "primary stalls → same fallback stalls → fail" loop.
            note_model_stall(str(state.effective_model or ""))
            _model_timeout_recoveries += 1
            # Long tasks legitimately hit slow-but-working provider rounds. A
            # stalled round that finally yields an action/final resets the
            # counter below, so this break only fires after *consecutive* pure
            # stalls (the provider really returned nothing inside its deadline).
            # Scale the tolerance with the turn's iteration budget so a deep
            # task isn't hard-capped after just two slow rounds.
            _stall_break_threshold = max(2, min(6, state.iteration_limit // 5))
            if _model_timeout_recoveries >= _stall_break_threshold:
                if _evidence_convergence_active is not None:
                    # A provider can ignore tools=[] and finish a timed-out
                    # convergence round with another phantom tool call. That
                    # action is unusable once the requested evidence is
                    # complete and must not reset the stall counter. Surface a
                    # truthful handoff as ordinary answer text before the
                    # terminal receipt; emitting react_error first makes the
                    # realtime gateway close the turn and drop that text.
                    final_answer = _stage_update_timeout_fallback(steps)
                    step.observation = (
                        "[model-iteration-timeout] evidence synthesis retry also timed out"
                    )
                    steps.append(step)
                    terminated_reason = "model_stall"
                    return _LoopControl.BREAK
                # Graceful degradation: instead of a hard "react_error" that
                # the gateway treats as a turn failure, surface a friendly
                # handoff as ordinary answer text.  The turn still ends, but
                # the user sees a natural message (like a thoughtful person
                # pausing mid-conversation) rather than a system error banner.
                final_answer = _model_stall_handoff_answer(steps)
                step.observation = "[model-iteration-timeout] convergence retry also timed out"
                steps.append(step)
                terminated_reason = "model_stall"
                return _LoopControl.BREAK
            _fallback_model = None
            if not _final_stream_started:
                _fallback_model = _try_react_model_failover("model stream timeout")
            recovery_update = (
                "当前模型响应过慢，已保留现有结果并切换备用模型继续。"
                if _fallback_model
                else (
                    "这一轮响应超过了单轮时限；已保留前面的有效结果，"
                    "下一轮会减少额外操作，直接收拢阶段结论、必要操作或最终答案。"
                )
            )
            yield {
                "type": "commentary_delta",
                "delta": recovery_update,
                "progress_source": "runtime",
                # This is an operational truth, not generic stage narration.
                # Keep it visible so a slow-provider failover never looks like
                # an unexplained frozen conversation.
                "public_status": True,
                "iteration": i + 1,
            }
            if _fallback_model:
                yield {
                    "type": "react_retry",
                    "kind": "model_failover",
                    "model": _fallback_model,
                    "iteration": i + 1,
                    # Read from state, not a pulled local: the injected
                    # failover wrapper refreshes state.model_failovers
                    # mid-call after the closure's nonlocal increment.
                    "attempt": state.model_failovers,
                }
            step.public_update = recovery_update
            _timeout_recovery_observation = (
                "[model-iteration-timeout] The previous model stream kept producing "
                "private reasoning without a usable Action or Final Answer. Preserve "
                "all completed tool results. A backup model may now be active. "
                "On the next turn, do not deliberate at "
                "length: emit one concrete Update plus the next necessary Action, or "
                "emit the complete Final Answer directly."
            )
            if _evidence_convergence_active is not None:
                _recovery_directive = build_direct_answer_directive(
                    goal=goal,
                    decision=_evidence_convergence_active,
                    steps=steps,
                )
                if _recovery_directive:
                    _timeout_recovery_observation += f"\n\n{_recovery_directive}"
            step.observation = _timeout_recovery_observation
            _force_convergence_next = True
        elif step.action or maybe_final is not None:
            _model_timeout_recoveries = 0
        _finish_reason = (getattr(resp, "finish_reason", "") or "").strip().lower()
        _length_limited = _finish_reason_is_length_limited(_finish_reason)
        _length_limit_should_continue = False
        if (
            maybe_final
            and not _final_stream_started
            and _evidence_convergence_active is None
            and not _final_answer_needs_pre_emit_guard(
                maybe_final,
                is_code_mode=_is_code_mode,
                browser_operation_mode=_browser_operation_mode,
            )
        ):
            # Fall-through emission for routers that don't actually
            # stream (e.g. tests, non-streaming providers): yield the
            # parsed final once. When _final_stream_started is true the
            # user has already seen these tokens live, so skip to avoid
            # duplicate text in the transcript.
            _emit_assistant_chunk(
                stack,
                iteration=i + 1,
                delta=maybe_final,
                task_id=react_task_id,
            )
            yield {
                "type": "text_delta",
                "delta": maybe_final,
                "iteration": i + 1,
            }
            _final_delta_emitted_this_iteration = True

        # Chat-style answer recovery: the model produced plain
        # markdown without any ReAct anchor BUT we already streamed
        # it live via the 120-char early-flush branch in the LLM
        # call loop above. Treat that streamed prose AS the final
        # answer — don't waste a second LLM round to bail. Without
        # this short-circuit, real chat-style replies (mimo's
        # default shape) burn the bail-at budget and emit the same
        # text twice on iteration N+1.
        if (
            _final_stream_started
            and not _length_limited
            and not maybe_final
            and step.action.lower() in {"none", "n/a", ""}
            and not _looks_like_observation_echo(text)
            and not _FINAL_RE.search(text)
            and not _looks_like_unfinished_work(text)
        ):
            _guard_hit = _evaluate_final_answer_guards(
                steps=steps,
                step=step,
                final_answer=text,
                is_code_mode=_is_code_mode,
                todo_protocol_required=_todo_protocol_required,
                todo_protocol_visible=_todo_protocol_visible,
                file_inspection_tools_visible=_file_inspection_tools_visible,
                tools_active=tools_active,
                goal=goal,
                browser_operation_mode=_browser_operation_mode,
                grounded_source_paths=_final_guard_grounded_source_paths,
                model=state.effective_model,
                prior_grounding_text=state.prior_grounding_text,
                categories=(
                    None
                    if (_browser_operation_mode or _is_code_mode)
                    else frozenset({"security", "protocol", "research"})
                ),
            )
            if _guard_hit is not None:
                # Solution-A: a guard rejection that is purely a leaked ReAct
                # protocol block (Thought/Action/Observation leaked into the
                # answer) is downgraded to a one-shot cleaned delivery rather
                # than retried in a loop. The model usually already did the
                # work (tools ran); only the answer markup was dirty.
                _downgrade = _try_clean_downgrade(text)
                if _downgrade is not None:
                    final_answer = _downgrade
                    terminated_reason = "final_answer_with_warning"
                    steps.append(step)
                    return _LoopControl.BREAK
                _guard_label, _guard_message = _guard_hit
                _guard_outcome = _guard_rejection_outcome(_guard_impasse_state, _guard_label, steps)
                if _guard_outcome == "hard_stop":
                    _auto_verify_step = _try_auto_verification_salvage(
                        _guard_label,
                        steps,
                        iteration=i + 1,
                        cwd=effective_wp if isinstance(effective_wp, str) else None,
                    )
                    if _auto_verify_step is not None:
                        step.thought = _auto_verify_step.thought
                        step.public_update = _auto_verify_step.public_update
                        step.action = _auto_verify_step.action
                        step.actions = _auto_verify_step.actions
                        _final_stream_started = False
                        maybe_final = None
                        return _LoopControl.CONTINUE
                    # Same loop-level bound as the main guard site: the
                    # chat-flush path rejects and continues too, so an
                    # unsatisfiable guard here would livelock identically.
                    _logger.warning(
                        "react_loop guard impasse (chat-flush) · %s repeatedly rejected "
                        "with no intervening tool execution — terminating",
                        _guard_label,
                    )
                    final_answer = _guard_impasse_final_answer(_guard_label, _guard_message, steps)
                    terminated_reason = "guard_impasse"
                    steps.append(step)
                    return _LoopControl.BREAK
                _final_stream_started = False
                step.observation = (
                    (((step.observation or "") + "\n\n") if step.observation else "")
                    + f"[{_guard_label}]\n"
                    + _guard_repair_feedback(_guard_label, _guard_message, steps)
                )
                maybe_final = None
            else:
                final_answer = text
                terminated_reason = "final_answer"
                final_answer_emitted = True
                steps.append(step)
                return _LoopControl.BREAK

        if maybe_final is None and not step.action and not step.observation:
            _recovered_read_actions = _recover_explicit_read_actions(
                goal=goal,
                model_text=step.thought or text,
                workspace_path=(effective_wp if isinstance(effective_wp, str) else None),
                steps=steps,
                executor=executor,
                read_only=_read_only_turn,
            )
            if _recovered_read_actions:
                step.actions = _recovered_read_actions
                step.action = "; ".join(_recovered_read_actions)
                if not step.thought:
                    step.thought = text
                consecutive_format_violations = 0

        if _is_format_violation(step, maybe_final):
            # Length-limited generation gets a free pass on the
            # zero-anchor format violation. The model didn't emit a
            # final answer because it ran out of tokens mid-sentence,
            # not because it broke the protocol — the continuation
            # branch below will inject a "Continue exactly where it
            # stopped" nudge and the next iteration will finish.
            _is_length_truncated = _finish_reason_is_length_limited(
                getattr(resp, "finish_reason", "")
            )
            if _is_length_truncated:
                # Surface the partial text so the user sees streaming
                # progress; don't count it against bail-at.
                if text and not maybe_final and not _final_stream_started:
                    _emit_assistant_chunk(
                        stack,
                        iteration=i + 1,
                        delta=text,
                        task_id=react_task_id,
                    )
                    yield {
                        "type": "text_delta",
                        "delta": text,
                        "iteration": i + 1,
                    }
                consecutive_format_violations = 0
            elif _unfinished_implementation_recovery_needed(
                text,
                goal,
                is_code_mode=_is_code_mode,
            ):
                # Free-form implementation diagnosis is not a final answer.
                # Providers sometimes narrate the exact remaining defect but
                # omit the ReAct Action anchor; the old two-strike fallback
                # terminated at that point and left knowingly broken code.
                # Preserve the diagnosis as an observation and make the next
                # round a bounded, no-extended-thinking convergence attempt.
                consecutive_format_violations = 0
                _final_stream_started = False
                step.observation = (
                    "[unfinished-work-recovery] Your previous prose explicitly says work remains. "
                    "Do not restate the diagnosis. Execute the next necessary tool call now using "
                    "Action: skill_name({JSON}); after focused verification passes, emit Final Answer."
                )
                _force_convergence_next = True
                yield {
                    "type": "commentary_delta",
                    "delta": "检测到尚未完成的实现诊断；已保留结论，下一轮直接执行修复。",
                    "progress_source": "runtime",
                    "iteration": i + 1,
                }
            else:
                consecutive_format_violations += 1
                _plain_answer_can_finish = bool(
                    text
                    and not maybe_final
                    and (
                        _no_tool_turn
                        or i > 0
                        or executed_beak_steps
                        or any(
                            prior_step.action_results
                            or (prior_step.action and prior_step.observation)
                            for prior_step in steps
                        )
                    )
                )
                _logger.warning(
                    "react_loop iter %d · LLM produced zero ReAct anchors "
                    "(consec=%d/%d) · raw head=%r",
                    i + 1,
                    consecutive_format_violations,
                    _format_violation_bail_at,
                    text[:200],
                )
                if (
                    consecutive_format_violations >= _format_violation_bail_at
                    or _plain_answer_can_finish
                ):
                    # Salvage the model's raw output as the final reply.
                    # Without this yield the gateway records a turn that
                    # produced no text → frontend renders the stream as
                    # "本次回复已中断" even though the model spoke. This
                    # is the most common shape of zero-anchor: a research
                    # / chat-style answer in plain markdown without
                    # ``Final Answer:`` prefix. Treat it as the answer
                    # rather than silently discarding it.
                    # If the chat-style early-flush branch above already
                    # streamed this text live, skip the duplicate yield —
                    # otherwise the user sees the answer twice.
                    _guard_hit = None
                    if text and not maybe_final:
                        _guard_hit = _evaluate_final_answer_guards(
                            steps=steps,
                            step=step,
                            final_answer=text,
                            is_code_mode=_is_code_mode,
                            todo_protocol_required=_todo_protocol_required,
                            todo_protocol_visible=_todo_protocol_visible,
                            file_inspection_tools_visible=_file_inspection_tools_visible,
                            tools_active=tools_active,
                            goal=goal,
                            browser_operation_mode=_browser_operation_mode,
                            grounded_source_paths=_final_guard_grounded_source_paths,
                            model=state.effective_model,
                            prior_grounding_text=state.prior_grounding_text,
                            categories=(
                                None
                                if (_browser_operation_mode or _is_code_mode)
                                else frozenset({"security", "protocol", "research"})
                            ),
                        )
                    if _guard_hit is not None:
                        # Solution-A: leaked-protocol rejection → one-shot
                        # cleaned delivery instead of a retry loop.
                        _downgrade = _try_clean_downgrade(text)
                        if _downgrade is not None:
                            final_answer = _downgrade
                            terminated_reason = "final_answer_with_warning"
                            # The raw candidate was withheld by the protocol
                            # pre-emit guard.  Keep this false so PHASE 7 emits
                            # the cleaned answer exactly once.
                            final_answer_emitted = False
                            steps.append(step)
                            return _LoopControl.BREAK
                        _guard_label, _guard_message = _guard_hit
                        _guard_outcome = _guard_rejection_outcome(
                            _guard_impasse_state, _guard_label, steps
                        )
                        if _guard_outcome == "hard_stop":
                            _auto_verify_step = _try_auto_verification_salvage(
                                _guard_label,
                                steps,
                                iteration=i + 1,
                                cwd=effective_wp if isinstance(effective_wp, str) else None,
                            )
                            if _auto_verify_step is not None:
                                step.thought = _auto_verify_step.thought
                                step.public_update = _auto_verify_step.public_update
                                step.action = _auto_verify_step.action
                                step.actions = _auto_verify_step.actions
                                _final_stream_started = False
                                maybe_final = None
                                return _LoopControl.CONTINUE
                            _logger.warning(
                                "react_loop guard impasse (plain-answer recovery) · "
                                "%s repeatedly rejected with no intervening tool execution — "
                                "terminating",
                                _guard_label,
                            )
                            final_answer = _guard_impasse_final_answer(
                                _guard_label,
                                _guard_message,
                                steps,
                            )
                            terminated_reason = "guard_impasse"
                            steps.append(step)
                            return _LoopControl.BREAK
                        consecutive_format_violations = 0
                        step.observation = (
                            (((step.observation or "") + "\n\n") if step.observation else "")
                            + f"[{_guard_label}]\n"
                            + _guard_repair_feedback(_guard_label, _guard_message, steps)
                        )
                    if _guard_hit is not None:
                        consecutive_format_violations = 0
                        maybe_final = None
                    elif text and not maybe_final:
                        # Guarded plain prose is a valid final answer even when
                        # the provider omitted the literal ReAct label. The old
                        # path surfaced the text and then returned ``None``, so
                        # the gateway still marked a visibly complete reply as
                        # interrupted. Finish the turn normally instead.
                        if not _final_stream_started:
                            _emit_assistant_chunk(
                                stack,
                                iteration=i + 1,
                                delta=text,
                                task_id=react_task_id,
                            )
                            yield {
                                "type": "text_delta",
                                "delta": text,
                                "iteration": i + 1,
                            }
                        final_answer = text
                        final_answer_emitted = True
                        terminated_reason = "final_answer"
                        steps.append(step)
                        return _LoopControl.BREAK
                    else:
                        _persist_react_trajectory(
                            stack,
                            react_task_id=react_task_id,
                            beak_steps=executed_beak_steps,
                            success=False,
                            disposition="failed",
                        )
                        _pause.unregister_active(str(react_task_id))
                        return _LoopControl.RETURN_NONE
        else:
            consecutive_format_violations = 0

        # Some reasoning models (e.g. Kimi K3) emit an ``Update:`` progress
        # line but stop before issuing the required ``Action:`` tool call —
        # they narrate intent without executing. Inject a compact system
        # nudge so the next round emits the actual tool call instead of
        # repeating another Update-only turn until max_iterations.
        if not step.action and not maybe_final:
            _reminder = _zero_action_protocol_reminder(step, consecutive_format_violations)
            if _reminder:
                step.observation = _reminder

        if resp_thinking and not step.thought:
            step.thought = resp_thinking

        _throughput_chars += len(text)
        _tp = _maybe_emit_throughput(_throughput_chars)
        if _tp is not None:
            yield _tp
        return _LoopControl.CONTINUE
    finally:
        state.zero_action_rounds = _next_zero_action_rounds(
            state.zero_action_rounds,
            step=step,
            maybe_final=maybe_final,
            final_answer_emitted=final_answer_emitted,
        )
        state.native_mode = _native_mode
        state.model_timeout_recoveries = _model_timeout_recoveries
        state.final_stream_started = _final_stream_started
        state.force_convergence_next = _force_convergence_next
        state.consecutive_format_violations = consecutive_format_violations
        state.throughput_chars = _throughput_chars
        state.final_answer = final_answer
        state.terminated_reason = terminated_reason
        state.final_answer_emitted = final_answer_emitted
        state.final_delta_emitted_this_iteration = _final_delta_emitted_this_iteration
        state.step = step
        state.maybe_final = maybe_final
        state.text = text
        state.length_limited = _length_limited
        state.length_limit_should_continue = _length_limit_should_continue
