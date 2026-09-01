"""PHASE 6d — action dispatch + observation for the ReAct loop.

Extracted from ``react_execution.py`` (Wave 2). Implements
``_phase_6d_dispatch_and_observe``: resolves the step's action(s), runs
approval/retry/cancel, dispatches single or parallel tool calls, and
projects the resulting observation. Cross-module helpers that would create
import cycles (``_dispatch_parallel_actions``, ``_WRITE_TOOLS``, the
quiet-evidence helpers, the five action-outcome helpers) are injected by the
caller. Imports only react_* leaf modules and sibling
``_react_execution_*`` submodules — never imports react_loop or
react_execution.
"""

from __future__ import annotations

import contextlib
import logging
import re
import time
import uuid
from collections.abc import Callable, Generator
from typing import Any

from runtime.core.cerebrum._react_execution_dispatch import (
    _execute_action_via_beak,
    _run_auto_diagnostics,
)
from runtime.core.cerebrum._react_execution_phase6g import (
    _phase_6d_pre_dispatch_guards,
)
from runtime.core.cerebrum._react_execution_results import (
    _background_task_info_from_observation,
    _is_scoped_artifact_write,
    _tool_event_extras_from_beak_step,
)
from runtime.core.cerebrum.react_context import _prefetch_related_files
from runtime.core.cerebrum.react_convergence import (
    build_direct_answer_directive,
    read_only_evidence_convergence,
)
from runtime.core.cerebrum.react_execution_receipts import (
    _execution_effect_receipt,
    _execution_receipt_trust,
    _retry_safe_effect_receipt,
)
from runtime.core.cerebrum.react_explicit_reads import (
    _narrow_command_direct_answer,
)
from runtime.core.cerebrum.react_final_answer_guards import (
    _record_rejected_step,
)
from runtime.core.cerebrum.react_loop_state import (
    _LoopControl,
    _LoopState,
)
from runtime.core.cerebrum.react_parsing import (
    _has_code_verification,
    _has_successful_verification_observation,
    _is_code_write_step,
    _parse_action,
    _placeholder_observation,
    _summarize_observation,
)
from runtime.core.cerebrum.react_public_updates import (
    _observed_read_fallback_update,
    _safe_public_update,
    _stream_public_evidence_narrative,
)
from runtime.execution.tool_engine.tool_protocol import (
    normalize_tool_lifecycle_event,
    tool_lifecycle_event_to_react_event,
)
from runtime.safety.hooks.tool_edge_hooks import post_write_diagnostic_record
from runtime.safety.validation.prompt_injection import (
    injection_taint_gates,
    is_untrusted_tool,
    mark_injection_taint,
    scan_for_injection,
    set_injection_gate_handled,
    wrap_untrusted_observation,
)

_logger = logging.getLogger(__name__)


def _observe_repeat_guard(state: _LoopState, tool_name: str, arguments: Any) -> None:
    """Advance the advisory repeat-call chain for one attempted call.

    dsh ``repeat-tool-reminder`` observes at post-execute (denied calls flow
    through the same waterfall); our deny fast paths return before the end of
    the phase, so we observe at attempt time instead — a model hammering a
    denied call is exactly the loop worth breaking. Best-effort: a guard
    failure must never break the turn.
    """
    guard = getattr(state, "repeat_guard", None)
    if guard is None:
        return
    try:
        reminder = guard.observe(
            tool_name,
            arguments,
            agent_key=state.thread_id or "default",
        )
    except Exception:  # noqa: BLE001 — advisory; never break the turn
        _logger.debug("repeat-tool guard observe skipped", exc_info=True)
        return
    if reminder:
        state.guard_notices.append(reminder)


def _flush_guard_notices(state: _LoopState, messages: Any) -> None:
    """Append queued guard reminders to the model context immediately.

    Used on the approval-denied fast paths (which skip PHASE 6g), so the
    nudge still lands before the next model call — dsh's post-execute
    ``additionalContexts`` timing. Notices are drained (never duplicated).
    """
    notices = getattr(state, "guard_notices", None)
    if not notices:
        return
    from runtime.platform.models.llm import Message

    for notice in notices:
        messages.append(Message(role="user", content=notice))
    notices.clear()


_SANDBOX_VIOLATION_RE = re.compile(r"sandbox[ _\-]?violation", re.IGNORECASE)
# Tools whose sandbox block is safe to escalate to the human (network/write
# denied for a command). Pure write/edit/delete tools are deliberately NOT
# listed — a blocked write must be re-considered, not auto-re-offered.
_ESCALABLE_TOOLS = frozenset(
    {
        "exec_shell",
        "exec_command",
        "run_command",
        "background_exec",
        "bash",
        "sh",
        "shell",
        "git",
        "git_network",
        "npm",
        "pnpm",
        "yarn",
        "pip",
        "pip3",
        "cargo",
        "go",
        "test",
        "typecheck",
        "lint",
        "verify",
        "verify_skills",
    }
)


def _looks_like_sandbox_violation(observation: str | None) -> bool:
    """Whether a tool observation is a sandbox rejection (post-execution)."""
    return bool(observation and _SANDBOX_VIOLATION_RE.search(observation))


_UNAVAILABLE_APPROVAL_REASONS = (
    "timeout",
    "connection_lost",
    "error:",
    "no interactive approval ui",
)


def _approval_could_not_reach_user(decision: Any) -> bool:
    """Separate an unavailable reviewer from an explicit human decline."""

    if bool(getattr(decision, "approved", False)):
        return False
    reason = str(getattr(decision, "reason", "") or "").strip().lower()
    return any(
        reason == marker or reason.startswith(marker) for marker in _UNAVAILABLE_APPROVAL_REASONS
    )


def _pause_for_unavailable_approval(
    state: _LoopState,
    *,
    iteration: int,
    tool_name: str,
    detail: str,
) -> bool:
    """Request a checkpointed pause instead of turning UI absence into failure."""

    pause_controller = state.pause_controller
    task_id = str(state.react_task_id or "").strip()
    if pause_controller is None or not task_id:
        return False
    pause_controller.request_pause(
        task_id=task_id,
        reason="approval_required",
        requested_by="system",
        note=f"{tool_name}: {detail}",
        thread_id=state.thread_id,
        agent_id=state.agent_id_for_pause,
    )
    # Reserve a boundary for the normal pause guard, including when the
    # blocked call happened on what would otherwise be the final iteration.
    state.iteration_limit = max(state.iteration_limit, iteration + 2)
    return True


def _can_escalate_sandbox(tool_name: str) -> bool:
    """Whether this tool is eligible for the sandbox-escalation prompt."""
    return tool_name in _ESCALABLE_TOOLS


def _latest_human_intent(messages: Any) -> str:
    """Most recent human message text — the only trusted authorization
    evidence for the guardian review (codex policy_template Evidence
    Handling: user messages are trusted content; tool outputs are not)."""
    try:
        for message in reversed(list(messages or [])):
            if getattr(message, "type", None) == "human":
                content = getattr(message, "content", None)
                if isinstance(content, str) and content.strip():
                    return content.strip()[:1000]
                if isinstance(content, list):
                    parts = [
                        part.get("text", "")
                        for part in content
                        if isinstance(part, dict) and part.get("type") == "text"
                    ]
                    text = " ".join(parts).strip()
                    if text:
                        return text[:1000]
    except Exception:  # noqa: BLE001 — intent extraction is best-effort
        pass
    return ""


def _phase_6d_dispatch_and_observe(
    state: _LoopState,
    *,
    i: int,
    dispatch_parallel_actions: Callable[..., Any],
    write_tools: frozenset,
    result_checkpoint_is_meaningful: Callable[..., bool],
    should_accumulate_quiet_evidence: Callable[..., bool],
    quiet_evidence_checkpoint_due: Callable[..., bool],
    action_batch_fingerprint: Callable[..., str],
    deduplicate_actions: Callable[..., Any],
    per_action_outcomes: Callable[..., Any],
    retry_safe_affinity: Callable[..., bool],
    tool_call_succeeded: Callable[..., bool],
    observation_is_noop: Callable[[str], bool],
) -> Generator[dict[str, Any], None, _LoopControl]:
    """Dispatch the step's action(s), run approval/retry/cancel, observe.

    Moved verbatim from ``react_loop.py`` (PHASE 6d). Returns
    ``CONTINUE`` to proceed to PHASE 6e, ``NEXT_ITERATION`` for the
    approval-denied fast paths (Python ``continue`` in the original),
    or ``BREAK`` when the cancel token killed the tool mid-run.
    ``_dispatch_parallel_actions`` / ``_WRITE_TOOLS`` / the three
    quiet-evidence helpers / the five action-outcome helpers are
    injected because their home modules (react_parallel_dispatch,
    react_quiet_evidence, react_action_outcomes) import this one.
    """
    # Injected callables/constants under their original names.
    _dispatch_parallel_actions = dispatch_parallel_actions
    _WRITE_TOOLS = write_tools  # noqa: N806
    _result_checkpoint_is_meaningful = result_checkpoint_is_meaningful
    _should_accumulate_quiet_evidence = should_accumulate_quiet_evidence
    _quiet_evidence_checkpoint_due = quiet_evidence_checkpoint_due
    _action_batch_fingerprint = action_batch_fingerprint
    _deduplicate_actions = deduplicate_actions
    _per_action_outcomes = per_action_outcomes
    _tool_call_succeeded = tool_call_succeeded
    _observation_is_noop = observation_is_noop
    # Reference-typed aliases — mutations propagate to the main loop.
    step = state.step
    assert step is not None, "phase 6d requires a parsed ReAct step"
    steps = state.steps
    executed_beak_steps = state.executed_beak_steps
    messages = state.messages
    _working_set = state.working_set
    stack = state.stack
    react_task_id = state.react_task_id
    executor = state.executor
    agent = state.agent
    intent = state.intent
    router = state.router
    thread_id = state.thread_id
    approval_provider = state.approval_provider
    # Guardian independent review (opt-in): high/critical risk actions get
    # a second opinion from an independent model before escalating to the
    # user. Off by default; budget is per-thread (exhaustion = long-task
    # exemption, no further reviews this turn); failures degrade to the
    # rule engine's conclusion. Built once per phase — the router and
    # user-context config are read here.
    _guardian_reviewer = None
    if intent.user_context.get("guardian_review_enabled", False):
        from runtime.safety.approval.guardian_review import (
            GuardianReviewer,
            GuardianReviewerConfig,
        )

        # Default the review model to the CONVERSATION's own model — the
        # user's chosen model is always available to them; only an explicit
        # override (guardian_review_model) switches to a dedicated reviewer.
        _guardian_model = intent.user_context.get("guardian_review_model")
        _guardian_reviewer = GuardianReviewer(
            router,
            GuardianReviewerConfig(
                enabled=True,
                per_turn_limit=int(intent.user_context.get("guardian_review_per_turn_limit", 3)),
                timeout_s=float(intent.user_context.get("guardian_review_timeout_s", 15.0)),
                guardian_model=(
                    str(_guardian_model).strip()
                    if isinstance(_guardian_model, str) and _guardian_model.strip()
                    else None
                ),
                # The conversation's own model — reference state directly, the
                # local ``effective_model`` scalar pull happens further down.
                default_model=state.effective_model,
            ),
        )
    output_chunk_sink = state.output_chunk_sink
    _metadata = state.metadata
    _effective_wp = state.effective_wp
    # Scalar pulls — original local names; pushed back in the finally.
    tools_active = state.tools_active
    effective_model = state.effective_model
    _current_phase = state.current_phase
    _is_code_mode = state.is_code_mode
    _todo_protocol_required = state.todo_protocol_required
    _todo_protocol_visible = state.todo_protocol_visible
    _read_only_turn = state.read_only_turn
    _is_goal_mode = state.is_goal_mode
    _observed_read_sequence = state.observed_read_sequence
    _ordered_result_handoffs = state.ordered_result_handoffs
    _realtime_public_orientation = state.realtime_public_orientation
    _realtime_public_narrative = state.realtime_public_narrative
    maybe_final = state.maybe_final
    terminated_reason = state.terminated_reason
    _evidence_convergence_active = state.evidence_convergence_active
    _force_convergence_next = state.force_convergence_next
    _consecutive_same_failed_actions = state.consecutive_same_failed_actions
    _last_failed_action_fingerprint = state.last_failed_action_fingerprint
    _consecutive_same_noop_actions = state.consecutive_same_noop_actions
    _last_noop_action_fingerprint = state.last_noop_action_fingerprint
    _green_verification_convergence_active = state.green_verification_convergence_active
    _green_convergence_todo_used = state.green_convergence_todo_used
    _result_handoff_ready = state.result_handoff_ready
    _last_public_update_key = state.last_public_update_key
    _saw_successful_code_write = state.saw_successful_code_write
    _clean_verification_rounds_after_write = state.clean_verification_rounds_after_write
    _quiet_evidence_steps = state.quiet_evidence_steps
    try:
        (
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
        ) = _phase_6d_pre_dispatch_guards(
            step,
            steps,
            intent,
            i,
            tools_active=tools_active,
            _effective_wp=_effective_wp,
            _read_only_turn=_read_only_turn,
            _observed_read_sequence=_observed_read_sequence,
            _consecutive_same_failed_actions=_consecutive_same_failed_actions,
            _last_failed_action_fingerprint=_last_failed_action_fingerprint,
            _consecutive_same_noop_actions=_consecutive_same_noop_actions,
            _last_noop_action_fingerprint=_last_noop_action_fingerprint,
            _evidence_convergence_active=_evidence_convergence_active,
            _todo_protocol_required=_todo_protocol_required,
            _is_goal_mode=_is_goal_mode,
            _todo_protocol_visible=_todo_protocol_visible,
            _is_code_mode=_is_code_mode,
            _green_verification_convergence_active=_green_verification_convergence_active,
            _green_convergence_todo_used=_green_convergence_todo_used,
            maybe_final=maybe_final,
            _force_convergence_next=_force_convergence_next,
            _deduplicate_actions=_deduplicate_actions,
            _action_batch_fingerprint=_action_batch_fingerprint,
        )

        # ``Update:`` is the explicit public checkpoint channel. Emit only
        # after the whole model turn has parsed, immediately before the tool
        # starts, so a partial ``Action:`` can never leak into conversation.
        # De-duplicate retries that repeat the same checkpoint verbatim.
        step.public_update = _safe_public_update(step.public_update)
        _checkpoint_actions = step.actions or [step.action]
        _prior_result_handoff = bool(
            _ordered_result_handoffs and _result_handoff_ready and tool_action_requested
        )
        if _prior_result_handoff:
            # The evidence narrator already gave the user the preceding fact
            # and next decision. Do not repeat a stochastic model paraphrase
            # immediately before the next tool row.
            step.public_update = ""
        if (
            not step.public_update
            and tool_action_requested
            and maybe_final is None
            and _realtime_public_orientation
            # Native tool schemas already require a structured public_update.
            # If a provider omits it, do not spend a second model call trying
            # to classify arbitrary prose as commentary: the real tool row is
            # sufficient activity feedback and private text stays private.
            and not state.native_mode
            and not _prior_result_handoff
        ):
            try:
                _repaired_public_update = yield from _stream_public_evidence_narrative(
                    router,
                    model=effective_model,
                    goal=intent.normalized_goal,
                    step=step,
                    convergence=None,
                    iteration=i + 1,
                    previous_key=_last_public_update_key,
                    pending_action=True,
                )
            except Exception as exc:  # noqa: BLE001 — optional public narration
                _logger.warning("public action orientation repair failed: %s", exc)
                _repaired_public_update = ""
            if _repaired_public_update:
                step.public_update = _repaired_public_update
                _last_public_update_key = (
                    re.sub(r"\s+", " ", _repaired_public_update).strip().casefold()
                )
        _model_supplied_update = bool(step.public_update)
        _public_update_key = re.sub(r"\s+", " ", step.public_update).strip().casefold()
        # Model-supplied ``Update:``/``Progress:`` checkpoints are stripped
        # from the zero-anchor streamed answer lane (react_model_stream), so
        # they must be surfaced here even when this iteration has no tool
        # action — otherwise the checkpoint would be lost entirely. Final
        # answers still suppress commentary so a checkpoint embedded in a
        # Final Answer body is not duplicated against the delivered answer.
        if (
            step.public_update
            and maybe_final is None
            and _public_update_key != _last_public_update_key
        ):
            yield {
                "type": "commentary_delta",
                "delta": step.public_update,
                "progress_source": "model",
                "start_new_segment": True,
                "iteration": i + 1,
            }
            _last_public_update_key = _public_update_key

        if tool_action_requested:
            _result_handoff_ready = False
            observation = None
            step.observation = ""
            maybe_final = None

        # Multi-action fast path: when the model emitted >1 tool call
        # in a single Action: block, dispatch them concurrently and
        # merge observations. Keeps the legacy single-action path
        # below untouched — that branch only runs when there is
        # exactly one action, preserving every existing
        # approval/retry/cancel/background-task behavior.
        _parallel_handled = False
        if tool_action_requested and len(step.actions) > 1:
            _parallel_obs, _parallel_results = yield from _dispatch_parallel_actions(
                step.actions,
                stack=stack,
                executor=executor,
                iteration=i + 1,
                react_task_id=react_task_id,
                agent=agent,
                intent=intent,
                beak_step_sink=executed_beak_steps,
            )
            if _parallel_obs is not None:
                observation = _parallel_obs
                step.observation = _parallel_obs
                step.action_results = _parallel_results
                tool_ok = all(r.get("ok") for r in _parallel_results)
                _parallel_handled = True
                # dsh repeat-tool-reminder: observe each parallel attempt
                # (args re-parsed from the action text; the dispatcher
                # already deduplicated identical calls within the batch).
                if state.repeat_guard is not None:
                    for _r_idx, _r in enumerate(_parallel_results):
                        _r_parsed = _parse_action(step.actions[_r_idx])
                        _r_name = _r.get("tool_name") or (_r_parsed[0] if _r_parsed else "")
                        _r_args = (
                            _r_parsed[1]
                            if _r_parsed is not None and isinstance(_r_parsed[1], dict)
                            else {}
                        )
                        _observe_repeat_guard(state, _r_name, _r_args)

        if not _parallel_handled and not step.observation:
            will_attempt_tool = tool_action_requested
            if will_attempt_tool:
                assert executor is not None
                parsed = _parse_action(step.action)
                resolved_name = parsed[0] if parsed and executor.registry.has(parsed[0]) else None
                if resolved_name is not None:
                    assert parsed is not None
                    call_id = uuid.uuid4().hex[:12]
                    action_args = parsed[1] if isinstance(parsed[1], dict) else {}
                    _input_preview = action_args
                    # dsh repeat-tool-reminder: observe at attempt time so
                    # denied/rejected calls count too (a model hammering a
                    # denied call is exactly the loop worth breaking).
                    _observe_repeat_guard(state, resolved_name, action_args)
                    _tool_started_at = time.monotonic()
                    yield tool_lifecycle_event_to_react_event(
                        normalize_tool_lifecycle_event(
                            "tool_start",
                            {
                                "tool_name": resolved_name,
                                "tool_call_id": call_id,
                                "iteration": i + 1,
                                "input_preview": _input_preview,
                            },
                            origin="react_compat",
                        )
                    )
                    _auto_approve = intent.user_context.get(
                        "auto_approve", False
                    ) or intent.flags.get("auto_approve", False)
                    from runtime.safety.approval.approval_gate import (
                        ApprovalRequest,
                        AutoDenyProvider,
                        approval_action_for_tool,
                    )

                    try:
                        from runtime.platform.process.session import current_session as _cs_ap

                        _sess_ap = _cs_ap()
                        _risk_policy_raw = (
                            (getattr(_sess_ap, "metadata", {}) or {}).get("approval_risk_policy")
                            if _sess_ap is not None
                            else None
                        )
                    except (AttributeError, TypeError):
                        _risk_policy_raw = None
                    _approval_risk, _approval_action, _approval_policy = approval_action_for_tool(
                        resolved_name,
                        str(_input_preview)[:500] if _input_preview else "",
                        policy=_risk_policy_raw,
                    )
                    _scoped_artifact_write = _is_scoped_artifact_write(
                        resolved_name,
                        _input_preview,
                    )
                    _permission_mode_value = str(
                        intent.user_context.get("permission_mode")
                        or _metadata.get("permission_mode")
                        or ""
                    ).lower()
                    _accept_edits_auto_approve = (
                        _permission_mode_value in {"acceptedits", "accept-edits"}
                        and resolved_name in _WRITE_TOOLS
                    )
                    # Injection taint gate (hard): if untrusted content
                    # carrying injection markers entered this turn, a
                    # risky tool can no longer auto-run — force it through
                    # human approval, overriding auto_approve and the
                    # scoped-write / accept-edits fast paths. This is the
                    # escalation from the in-context warning to an actual
                    # stop: a poisoned page can't drive an exec_shell /
                    # write / send behind the user's back. Gate at medium+
                    # so EXFILTRATION (egress tools = medium — the classic
                    # injection payload) is caught, not just destructive
                    # high-risk tools; only pure low-risk reads still
                    # auto-run after taint.
                    if injection_taint_gates() and _approval_risk.level in {
                        "medium",
                        "high",
                        "critical",
                    }:
                        _auto_approve = False
                        _scoped_artifact_write = False
                        _accept_edits_auto_approve = False
                        if _approval_action not in {"ask", "confirm", "deny"}:
                            _approval_action = "ask"
                        _approval_risk = _approval_risk.with_injection_taint()
                    # Guardian independent review (opt-in, after the taint
                    # gate so tainted actions still route to the human): a
                    # guardian deny tightens ask/confirm/allow to deny; an
                    # allow or a failure keeps the rule engine's action.
                    _approval_deny_reason: str | None = None
                    if _guardian_reviewer is not None:
                        from runtime.safety.approval.guardian_review import (
                            decide_with_guardian,
                        )

                        _guardian_user_intent = _latest_human_intent(messages)
                        _guardian_action, _guardian_note = decide_with_guardian(
                            rule_engine_action=_approval_action,
                            rule_engine_risk=_approval_risk.level,
                            rule_engine_categories=_approval_risk.categories,
                            reviewer=_guardian_reviewer,
                            thread_id=thread_id,
                            tool_name=resolved_name,
                            args_preview=(str(_input_preview)[:500] if _input_preview else ""),
                            user_intent=_guardian_user_intent,
                        )
                        if _guardian_action == "deny":
                            _approval_action = "deny"
                            _approval_deny_reason = _guardian_note
                    if (
                        _approval_action == "deny"
                        and not _auto_approve
                        and not _scoped_artifact_write
                    ):
                        yield {
                            "type": "tool_end",
                            "tool_name": resolved_name,
                            "tool_call_id": call_id,
                            "iteration": i + 1,
                            "status": "rejected",
                            "output_preview": (
                                _approval_deny_reason
                                or f"Denied by approval risk policy "
                                f"(risk={_approval_risk.level}: {_approval_risk.reason})"
                            ),
                            "duration_ms": int((time.monotonic() - _tool_started_at) * 1000),
                            "risk": _approval_risk.to_dict(),
                            "approval_action": _approval_action,
                            "approval_policy": _approval_policy.to_dict(),
                        }
                        observation = (
                            "(工具被风险策略拒绝) 此操作被 approval risk policy 拒绝，"
                            "请换一种方式或询问用户。"
                        )
                        _record_rejected_step(steps, messages, step, observation)
                        _flush_guard_notices(state, messages)
                        return _LoopControl.NEXT_ITERATION
                    if (
                        _approval_action in {"ask", "confirm"}
                        and not _auto_approve
                        and not _scoped_artifact_write
                        and not _accept_edits_auto_approve
                    ):
                        _provider = approval_provider or AutoDenyProvider()
                        _approval_detail = (
                            f"{resolved_name} wants to execute "
                            f"(risk={_approval_risk.level}: {_approval_risk.reason})"
                        )
                        yield {
                            "type": "tool_approval_request",
                            "tool_name": resolved_name,
                            "tool_call_id": call_id,
                            "args_preview": str(_input_preview)[:500] if _input_preview else "",
                            "detail": _approval_detail,
                            "risk": _approval_risk.to_dict(),
                            "approval_action": _approval_action,
                            "approval_policy": _approval_policy.to_dict(),
                        }
                        _decision = _provider.request(
                            ApprovalRequest(
                                thread_id=thread_id,
                                tool_name=resolved_name,
                                tool_call_id=call_id,
                                args_preview=str(_input_preview)[:500] if _input_preview else "",
                                detail=_approval_detail,
                            ),
                            timeout=600.0,
                        )
                        if not _decision.approved:
                            if _approval_could_not_reach_user(
                                _decision
                            ) and _pause_for_unavailable_approval(
                                state,
                                iteration=i,
                                tool_name=resolved_name,
                                detail=_approval_detail,
                            ):
                                yield {
                                    "type": "tool_end",
                                    "tool_name": resolved_name,
                                    "tool_call_id": call_id,
                                    "iteration": i + 1,
                                    "status": "waiting_approval",
                                    "output_preview": (
                                        "审批界面暂不可用，任务已保存并暂停，等待用户授权后继续。"
                                    ),
                                    "duration_ms": int(
                                        (time.monotonic() - _tool_started_at) * 1000
                                    ),
                                }
                                return _LoopControl.CONTINUE
                            yield {
                                "type": "tool_end",
                                "tool_name": resolved_name,
                                "tool_call_id": call_id,
                                "iteration": i + 1,
                                "status": "rejected",
                                "output_preview": _decision.reason or "User denied tool execution",
                                "duration_ms": int((time.monotonic() - _tool_started_at) * 1000),
                            }
                            observation = (
                                "(工具被用户拒绝) 用户拒绝了此操作，请换一种方式或询问用户。"
                            )
                            _record_rejected_step(steps, messages, step, observation)
                            _flush_guard_notices(state, messages)
                            return _LoopControl.NEXT_ITERATION
                    if output_chunk_sink is not None:
                        from runtime.core.cerebrum.tool_output_sink import push_sink

                        _bound_call_id = call_id

                        def _local_sink(
                            stream: str,
                            chunk: str,
                            bound_call_id: str = _bound_call_id,
                        ) -> None:
                            output_chunk_sink(bound_call_id, stream, chunk)

                        def _sink_scope() -> Any:
                            return push_sink(_local_sink)
                    else:

                        def _sink_scope() -> Any:
                            return contextlib.nullcontext()

                    # This single-action path ran its own approval gate
                    # (incl. the injection-taint escalation) above, so tell
                    # the executor's chokepoint block this call was reviewed
                    # — otherwise it would double-block an approved tool.
                    with _sink_scope():
                        set_injection_gate_handled(True)
                        try:
                            observation, beak_step = _execute_action_via_beak(
                                stack,
                                step.action,
                                react_task_id=react_task_id,
                                react_step_counter=i + 1,
                                agent=agent,
                                intent=intent,
                            )
                        finally:
                            set_injection_gate_handled(False)
                    if beak_step is not None:
                        executed_beak_steps.append(beak_step)
                    # Tool may have been killed mid-run by the cancel
                    # token. Detect this so we can label the event and
                    # break the loop — skipping the retry and the next
                    # LLM round, which would both waste budget.
                    _ct_post = None
                    try:
                        from runtime.safety.approval.cancellation import (
                            current_cancellation_token,
                        )

                        _ct_post = current_cancellation_token()
                    except (ImportError, AttributeError, TypeError, UnboundLocalError):  # noqa: BLE001 — cancellation subsystem unavailable; post-tool cancel check skipped
                        pass
                    _was_cancelled = bool(_ct_post and _ct_post.is_cancelled)

                    tool_ok = _tool_call_succeeded(observation, beak_step)
                    if _was_cancelled:
                        yield {
                            "type": "tool_end",
                            "tool_name": resolved_name,
                            "tool_call_id": call_id,
                            "iteration": i + 1,
                            "status": "cancelled",
                            "output_preview": "(已取消) 用户中断了此操作。",
                            "duration_ms": int((time.monotonic() - _tool_started_at) * 1000),
                        }
                        terminated_reason = "cancelled"
                        return _LoopControl.BREAK
                    # ── Sandbox-blocked escalation ─────────────────────────
                    # A tool that ran inside the sandbox can be blocked
                    # (network denied / write outside workspace) and come back
                    # as a "sandbox_violation" execution error. That failure
                    # carries no approval prompt by itself. Escalate to the
                    # human: offer to re-run the same command with a relaxed
                    # sandbox (network allowed), and only re-execute after
                    # explicit approval — mirroring Codex's post-block prompt.
                    if (
                        not tool_ok
                        and observation
                        and _looks_like_sandbox_violation(observation)
                        and _can_escalate_sandbox(resolved_name)
                        and not _auto_approve
                    ):
                        _escalation_provider = approval_provider or AutoDenyProvider()
                        _escalation_detail = (
                            f"{resolved_name} 被沙箱拦截（最可能是网络被禁用或写入超出工作区）。"
                            "是否允许以放宽沙箱（允许网络访问）重跑该命令？"
                        )
                        yield {
                            "type": "tool_approval_request",
                            "tool_name": resolved_name,
                            "tool_call_id": call_id,
                            "iteration": i + 1,
                            "args_preview": str(_input_preview)[:500] if _input_preview else "",
                            "detail": _escalation_detail,
                            "risk": {
                                "level": "high",
                                "categories": ["sandbox_escalation"],
                                "reason": "sandbox blocked the command; user may approve relaxed constraints",
                                "requires_approval": True,
                            },
                            "approval_action": "confirm",
                            "approval_policy": _approval_policy.to_dict(),
                            "sandbox_escalation": True,
                        }
                        _escalation_decision = _escalation_provider.request(
                            ApprovalRequest(
                                thread_id=thread_id,
                                tool_name=resolved_name,
                                tool_call_id=call_id,
                                args_preview=(str(_input_preview)[:500] if _input_preview else ""),
                                detail=_escalation_detail,
                            ),
                            timeout=600.0,
                        )
                        if not _escalation_decision.approved:
                            if _approval_could_not_reach_user(
                                _escalation_decision
                            ) and _pause_for_unavailable_approval(
                                state,
                                iteration=i,
                                tool_name=resolved_name,
                                detail=_escalation_detail,
                            ):
                                yield {
                                    "type": "tool_end",
                                    "tool_name": resolved_name,
                                    "tool_call_id": call_id,
                                    "iteration": i + 1,
                                    "status": "waiting_approval",
                                    "output_preview": (
                                        "沙箱放宽审批暂不可用，任务已保存并暂停，等待授权后继续。"
                                    ),
                                    "duration_ms": int(
                                        (time.monotonic() - _tool_started_at) * 1000
                                    ),
                                }
                                return _LoopControl.CONTINUE
                            yield {
                                "type": "tool_end",
                                "tool_name": resolved_name,
                                "tool_call_id": call_id,
                                "iteration": i + 1,
                                "status": "rejected",
                                "output_preview": (
                                    _escalation_decision.reason
                                    or "User declined sandbox escalation (run with network)"
                                ),
                                "duration_ms": int((time.monotonic() - _tool_started_at) * 1000),
                            }
                            observation = (
                                "(工具被沙箱拦截，用户拒绝放宽沙箱重跑；"
                                "请换一种不需要该权限/网络的方式，或询问用户。)"
                            )
                            _record_rejected_step(steps, messages, step, observation)
                            _flush_guard_notices(state, messages)
                            return _LoopControl.NEXT_ITERATION
                        _logger.info(
                            "react_loop iter %d · sandbox escalation approved for %s",
                            i + 1,
                            resolved_name,
                        )
                        with _sink_scope():
                            set_injection_gate_handled(True)
                            try:
                                esc_obs, esc_step = _execute_action_via_beak(
                                    stack,
                                    step.action,
                                    react_task_id=react_task_id,
                                    react_step_counter=i + 1,
                                    agent=agent,
                                    intent=intent,
                                    sandbox_override={
                                        "type": "dangerFullAccess",
                                        "networkAccess": True,
                                    },
                                )
                            finally:
                                set_injection_gate_handled(False)
                        if esc_step is not None:
                            executed_beak_steps.append(esc_step)
                        esc_ok = _tool_call_succeeded(esc_obs, esc_step)
                        if esc_ok:
                            observation = esc_obs
                            beak_step = esc_step
                            tool_ok = True
                        else:
                            observation = (observation or "") + (
                                "\n[已获授权放宽沙箱重跑，但仍失败；请换方法或调整参数]"
                            )
                    if not tool_ok and observation:
                        # C2: affinity/name metadata can be forged by a plugin.
                        # Silent retry therefore requires a server-stamped
                        # receipt proving that the exact canonical handler was
                        # read-only, entered, failed, and is retry-safe.
                        if not _retry_safe_effect_receipt(beak_step):
                            observation = observation + (
                                "\n[未获得可安全重试的服务器执行凭证，已停止自动重放；"
                                "请检查状态后再决定是否重试或换方法]"
                            )
                        else:
                            _logger.info(
                                "react_loop iter %d · tool %s failed, auto-retrying once",
                                i + 1,
                                resolved_name,
                            )
                            with _sink_scope():
                                set_injection_gate_handled(True)
                                try:
                                    retry_obs, retry_step = _execute_action_via_beak(
                                        stack,
                                        step.action,
                                        react_task_id=react_task_id,
                                        react_step_counter=i + 1,
                                        agent=agent,
                                        intent=intent,
                                    )
                                finally:
                                    set_injection_gate_handled(False)
                            if retry_step is not None:
                                executed_beak_steps.append(retry_step)
                            retry_ok = _tool_call_succeeded(retry_obs, retry_step)
                            if retry_ok:
                                observation = retry_obs
                                beak_step = retry_step
                                tool_ok = True
                            else:
                                observation = observation + "\n[自动重试仍失败，请换方法或调整参数]"
                    _background_task = (
                        _background_task_info_from_observation(observation)
                        if tool_ok and resolved_name in {"background_exec", "exec_shell"}
                        else None
                    )
                    if _background_task is not None:
                        yield {
                            "type": "tool_background",
                            "tool_name": resolved_name,
                            "tool_call_id": call_id,
                            "iteration": i + 1,
                            "status": "running",
                            "task_id": _background_task["task_id"],
                            "snapshot": _background_task,
                            "output_preview": (
                                _summarize_observation(observation)
                                if isinstance(observation, str) and observation
                                else observation
                            ),
                            "duration_ms": int((time.monotonic() - _tool_started_at) * 1000),
                        }
                    else:
                        yield tool_lifecycle_event_to_react_event(
                            normalize_tool_lifecycle_event(
                                "tool_end",
                                {
                                    "tool_name": resolved_name,
                                    "tool_call_id": call_id,
                                    "iteration": i + 1,
                                    "status": "success" if tool_ok else "error",
                                    "output_preview": (
                                        _summarize_observation(observation)
                                        if isinstance(observation, str) and observation
                                        else observation
                                    ),
                                    "duration_ms": int(
                                        (time.monotonic() - _tool_started_at) * 1000
                                    ),
                                    **_tool_event_extras_from_beak_step(beak_step, resolved_name),
                                },
                                origin="react_compat",
                            )
                        )
                    # Indirect prompt-injection defense (single-action
                    # path; mirrors _dispatch_parallel_actions): fence an
                    # external tool's output as data before it becomes the
                    # observation the model reads next.
                    if tool_ok and isinstance(observation, str) and observation:
                        _pi_affinity: list[str] | None = None
                        try:
                            if executor.registry.has(resolved_name):
                                _pi_affinity = executor.registry.get(
                                    resolved_name,
                                ).affinity
                        except (KeyError, AttributeError):
                            _pi_affinity = None
                        if is_untrusted_tool(resolved_name, _pi_affinity):
                            _pi_scan = scan_for_injection(observation)
                            observation = wrap_untrusted_observation(
                                observation,
                                source=resolved_name,
                                scan=_pi_scan,
                            )
                            if _pi_scan.flagged:
                                # Taint the turn → force human approval on a
                                # later high-risk tool (read at the gate).
                                mark_injection_taint(_pi_scan.severity)
                                _logger.warning(
                                    "prompt-injection markers in %s output "
                                    "(severity=%s, signals=%s)",
                                    resolved_name,
                                    _pi_scan.severity,
                                    ",".join(_pi_scan.labels),
                                )
                    _trusted_execution, _execution_source = _execution_receipt_trust(beak_step)
                    step.action_results = [
                        {
                            "tool_name": resolved_name,
                            "ok": tool_ok,
                            "observation": observation or "",
                            "duration_ms": int((time.monotonic() - _tool_started_at) * 1000),
                            "call_id": call_id,
                            "trusted_execution": _trusted_execution,
                            "execution_source": _execution_source,
                            "effect_receipt": _execution_effect_receipt(beak_step),
                        }
                    ]
                else:
                    observation, beak_step = _execute_action_via_beak(
                        stack,
                        step.action,
                        react_task_id=react_task_id,
                        react_step_counter=i + 1,
                        agent=agent,
                        intent=intent,
                    )
                    if beak_step is not None:
                        executed_beak_steps.append(beak_step)
            if observation is None:
                observation = _placeholder_observation(step.action)
            step.observation = observation

        _direct_command_answer = _narrow_command_direct_answer(
            goal=intent.normalized_goal,
            step=step,
            beak_step=beak_step,
            resolved_name=resolved_name,
            succeeded=tool_ok,
        )
        if _direct_command_answer is not None:
            maybe_final = _direct_command_answer

        if _duplicate_action_count and step.observation:
            step.observation += (
                "\n\n[duplicate-tools-collapsed] The provider emitted "
                f"{_duplicate_action_count} duplicate call(s) with identical tool arguments "
                "in one model round. The runtime executed each unique call once."
            )
        if (
            _explicit_read_scope_note
            and step.observation
            and _explicit_read_scope_note not in step.observation
        ):
            step.observation += "\n\n" + _explicit_read_scope_note
        if tool_action_requested and _current_action_fingerprint:
            if tool_ok:
                _last_failed_action_fingerprint = ""
                _consecutive_same_failed_actions = 0
            elif _current_action_fingerprint == _last_failed_action_fingerprint:
                _consecutive_same_failed_actions += 1
            else:
                _last_failed_action_fingerprint = _current_action_fingerprint
                _consecutive_same_failed_actions = 1
            # Silent no-op detection: the tool returned ok=True but the
            # observation shows an empty/zero-count result.  This catches
            # the "wrong key" failure mode where the handler swallows the
            # unknown argument and returns a valid-but-empty payload.
            _is_noop = tool_ok and _observation_is_noop(step.observation or "")
            if _is_noop and _current_action_fingerprint == _last_noop_action_fingerprint:
                _consecutive_same_noop_actions += 1
            elif _is_noop:
                _last_noop_action_fingerprint = _current_action_fingerprint
                _consecutive_same_noop_actions = 1
            else:
                _last_noop_action_fingerprint = ""
                _consecutive_same_noop_actions = 0
        elif not _repeated_failure_skipped and not _repeated_noop_skipped and tool_action_requested:
            _last_failed_action_fingerprint = ""
            _consecutive_same_failed_actions = 0
            _last_noop_action_fingerprint = ""
            _consecutive_same_noop_actions = 0

        # Common single/parallel tool outlet. Keep terminal evidence here so
        # a model round that launches lint + tests together is counted exactly
        # like one that launches either verifier alone.
        if _is_code_mode and tool_action_requested:
            _ordered_outcomes = _per_action_outcomes(step, default_ok=tool_ok)
            _last_successful_write_idx = -1
            for _outcome_idx, (_outcome_step, _outcome_ok) in enumerate(_ordered_outcomes):
                if _outcome_ok and _is_code_write_step(_outcome_step):
                    _last_successful_write_idx = _outcome_idx

            if _last_successful_write_idx >= 0:
                _saw_successful_code_write = True
                _clean_verification_rounds_after_write = 0
                _verification_outcomes = _ordered_outcomes[_last_successful_write_idx + 1 :]
            else:
                _verification_outcomes = _ordered_outcomes

            if _saw_successful_code_write:
                for _outcome_step, _outcome_ok in _verification_outcomes:
                    if not _has_code_verification([_outcome_step]):
                        continue
                    if _outcome_ok and _has_successful_verification_observation([_outcome_step]):
                        # Separate verifier calls in one serial multi-action
                        # batch are independent evidence rounds. Counting the
                        # whole batch once caused green code agents to run the
                        # same suite a dozen more times before convergence.
                        _clean_verification_rounds_after_write += 1
                    else:
                        _clean_verification_rounds_after_write = 0

            if (
                _clean_verification_rounds_after_write >= 2
                and not _green_verification_convergence_active
            ):
                _green_verification_convergence_active = True
                _force_convergence_next = True
                step.observation = (step.observation or observation or "") + (
                    "\n\n[green-verification-convergence]\n"
                    "Two clean verifier rounds completed after the latest successful code "
                    "write. The runtime has recorded terminal-quality evidence. Do not run "
                    "another verifier or shell probe. Update todo_write once if needed, then "
                    "emit Final Answer."
                )

        _evidence_convergence_became_active = False
        if _evidence_convergence_active is None and tool_action_requested:
            _evidence_convergence_active = read_only_evidence_convergence(
                goal=intent.normalized_goal,
                steps=steps + [step],
                read_only=_read_only_turn,
            )
            if _evidence_convergence_active is not None:
                _evidence_convergence_became_active = True
                _force_convergence_next = True
                _coverage = ", ".join(_evidence_convergence_active.covered[:6])
                _coverage_note = f" Covered evidence: {_coverage}." if _coverage else ""
                _direct_answer_directive = build_direct_answer_directive(
                    goal=intent.normalized_goal,
                    decision=_evidence_convergence_active,
                    steps=steps + [step],
                )
                step.observation = (step.observation or observation or "") + (
                    "\n\nThe user's requested read-only evidence is complete."
                    + _coverage_note
                    + " The next response must answer directly from these observations. "
                    "Do not call another tool or expand the investigation."
                    + (f"\n\n{_direct_answer_directive}" if _direct_answer_directive else "")
                )

        _meaningful_result_checkpoint = (
            tool_action_requested
            and observation
            and _result_checkpoint_is_meaningful(
                step.actions or [step.action],
                succeeded=tool_ok,
            )
        )
        _observed_result_checkpoint = bool(
            _ordered_result_handoffs
            and tool_action_requested
            and observation
            and (
                tool_ok
                or str(observation).lstrip().startswith("(real tool execution succeeded)")
                or not re.search(
                    r"(?:tool execution failed|file not found|no such file|"
                    r"does not exist|permission denied|读取失败|未找到|不存在)",
                    str(observation),
                    re.IGNORECASE,
                )
            )
        )
        if tool_action_requested and _should_accumulate_quiet_evidence(
            step,
            succeeded=tool_ok,
            observation=observation or "",
        ):
            _quiet_evidence_steps.append(step)
            # Keep prompts bounded when a provider repeatedly inspects new
            # files without producing a checkpoint of its own.
            _quiet_evidence_steps = _quiet_evidence_steps[-4:]
        _quiet_evidence_due = _quiet_evidence_checkpoint_due(_quiet_evidence_steps)
        _model_result_update = ""
        if _observed_result_checkpoint and maybe_final is None:
            # Ordered read tasks need a guaranteed conversational beat between
            # batches. A second model call can be slow, return SKIP, or finish
            # after the next action is already visible. Publish the completed
            # read receipt immediately; it is factual, privacy-safe, and gives
            # the user a stable fact -> next action rhythm.
            _model_result_update = _safe_public_update(
                _observed_read_fallback_update(
                    goal=intent.normalized_goal,
                    step=step,
                )
            )
            if _model_result_update:
                yield {
                    "type": "commentary_delta",
                    "delta": _model_result_update,
                    "progress_source": "runtime",
                    "public_evidence": True,
                    "start_new_segment": True,
                    "iteration": i + 1,
                }
        if (
            (_realtime_public_narrative or _ordered_result_handoffs)
            and maybe_final is None
            and not _model_result_update
            and (not _model_supplied_update or _quiet_evidence_due or _observed_result_checkpoint)
            and (
                _meaningful_result_checkpoint
                or _observed_result_checkpoint
                or _quiet_evidence_due
                or (
                    _evidence_convergence_became_active
                    and _evidence_convergence_active is not None
                    and len(_evidence_convergence_active.covered) > 1
                )
            )
        ):
            try:
                _model_result_update = yield from _stream_public_evidence_narrative(
                    router,
                    model=effective_model,
                    goal=intent.normalized_goal,
                    step=step,
                    convergence=(
                        _evidence_convergence_active
                        if _evidence_convergence_became_active
                        else None
                    ),
                    evidence_steps=(
                        _quiet_evidence_steps if _quiet_evidence_due else steps + [step]
                    ),
                    iteration=i + 1,
                    previous_key=_last_public_update_key,
                    succeeded=tool_ok,
                )
            except Exception as exc:  # noqa: BLE001 — optional public narration
                _logger.warning("public evidence narration failed: %s", exc)
                _model_result_update = ""
        if _quiet_evidence_due:
            # Whether the narrator spoke or the deterministic read receipt was
            # used, this evidence window has been considered. Start a fresh
            # window so long read-only tasks get bounded conversational beats.
            _quiet_evidence_steps = []
        _model_result_update_key = re.sub(r"\s+", " ", _model_result_update).strip().casefold()
        if _model_result_update and _model_result_update_key != _last_public_update_key:
            _last_public_update_key = _model_result_update_key
            if _ordered_result_handoffs:
                _result_handoff_ready = True

        if _is_code_mode and observation and _current_phase in ("execute", "verify"):
            _write_tools = frozenset(
                {
                    "write_text_file",
                    "edit_file",
                    "multi_edit_file",
                    "edit_text_file",
                    "edit_code",
                    "str_replace",
                    "write_file",
                    "create_file",
                }
            )
            if resolved_name in _write_tools and tool_ok:
                _diag_record = post_write_diagnostic_record(
                    resolved_name,
                    action_args or {},
                    action_args or {},
                    workspace_path=_effective_wp if isinstance(_effective_wp, str) else "",
                )
                _diag_status = str(_diag_record.get("status") or "skipped")
                _diag_reason = str(_diag_record.get("reason") or "")
                _diag_target = str(_diag_record.get("target") or "")
                _diag_text = f"{_diag_status}: {_diag_reason}" + (
                    f" · {_diag_target}" if _diag_target else ""
                )
                step.observation = (
                    (step.observation or observation) + "\n\n[写后诊断记录]\n" + _diag_text
                )
                _auto_diag = _run_auto_diagnostics(
                    stack,
                    workspace_path=_effective_wp if isinstance(_effective_wp, str) else None,
                )
                if _auto_diag:
                    step.observation = (
                        (step.observation or observation) + "\n\n[自动诊断结果]\n" + _auto_diag
                    )
                _prefetch = _prefetch_related_files(step.action, _working_set)
                if _prefetch:
                    step.observation = (
                        (step.observation or observation) + "\n\n[关联文件预读]\n" + _prefetch
                    )

        return _LoopControl.CONTINUE
    finally:
        state.maybe_final = maybe_final
        state.terminated_reason = terminated_reason
        state.evidence_convergence_active = _evidence_convergence_active
        state.force_convergence_next = _force_convergence_next
        state.consecutive_same_failed_actions = _consecutive_same_failed_actions
        state.last_failed_action_fingerprint = _last_failed_action_fingerprint
        state.consecutive_same_noop_actions = _consecutive_same_noop_actions
        state.last_noop_action_fingerprint = _last_noop_action_fingerprint
        state.green_verification_convergence_active = _green_verification_convergence_active
        state.green_convergence_todo_used = _green_convergence_todo_used
        state.result_handoff_ready = _result_handoff_ready
        state.last_public_update_key = _last_public_update_key
        state.saw_successful_code_write = _saw_successful_code_write
        state.clean_verification_rounds_after_write = _clean_verification_rounds_after_write
        state.quiet_evidence_steps = _quiet_evidence_steps
