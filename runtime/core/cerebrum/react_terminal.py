"""Post-loop terminal handling + finalization for the ReAct loop.

Moved from ``react_loop.py`` (PHASE 7/8): the paused/cancelled terminal
short-circuits, the forced max-iter convergence call, the final-answer
guard re-check, trajectory persistence, camouflage-scheduler outcome
recording, and the closing ``react_completed`` yield + ``ReActResult``
assembly.

``model_iteration_timeout_s`` is injected by the caller (react_loop)
rather than imported so tests that patch
``runtime.core.cerebrum.react_loop._model_iteration_timeout_s`` keep
working through the moved code path.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable, Generator
from typing import Any

# classify_turn_failure 从定义模块直连导入:react_execution 的 re-export
# 只存在于并发会话未提交版本,提交态 ``from react_execution import
# classify_turn_failure`` 会 ImportError(同 b5e2711d 模式)。
from runtime.core.cerebrum._react_execution_results import (
    _has_structured_user_block,
    classify_turn_failure,
)
from runtime.core.cerebrum.completion_decision import decide_completion
from runtime.core.cerebrum.react_execution import (
    _has_unrecovered_beak_failure,
    _persist_react_trajectory,
    _react_completion_receipt,
)
from runtime.core.cerebrum.react_final_answer_guards import (
    _evaluate_final_answer_guards,
    _guard_reason_for_user,
    _looks_like_observation_echo,
    _try_clean_downgrade,
)
from runtime.core.cerebrum.react_goal_analysis import (
    _final_answer_requests_user_help,
)
from runtime.core.cerebrum.react_loop_controls import _emit_assistant_chunk
from runtime.core.cerebrum.react_model_deadlines import (
    _MODEL_STREAM_DEADLINE,
    _collect_model_stream_text_with_deadline,
    _stage_update_timeout_fallback,
)
from runtime.core.cerebrum.react_parsing import (
    _ACTION_RE,
    _extract_final_answer,
    _looks_like_special_tool_envelope,
)
from runtime.core.cerebrum.react_types import (
    ReActResult,
    ReActStep,
    _safe_react_error_message,
)
from runtime.platform.models.llm import Message, ModelRequest
from runtime.platform.models.rescue_policy import note_model_stall

_logger = logging.getLogger(__name__)

# Terminal states that map to a neutral "waiting" (user must act) rather than
# a red failure — these are already preserved by the gateway as paused /
# cancelled and must not be re-disposed.
_WAITING_TERMINAL_REASONS = frozenset({"paused", "cancelled"})


# ``react_completed.disposition`` is retained as a compatibility projection of
# the canonical ``completion_decision.outcome``.
# It extends the raw ``success`` boolean with the one case that is neither a
# green completion nor a red failure: a turn that ended with a genuine hand
# off to the user (tight-marker help request, e.g. "please confirm the API
# key"). Such a turn still had an unresolved tool failure in its trajectory —
# ``success`` stays False so guard enforcement is unchanged — but the UI
# should render it as "needs your input" (amber / waiting), not as a failure.
#
# Value set: ``completed`` | ``paused`` | ``cancelled`` | ``blocked_on_user``
#            | ``failed``
def _turn_disposition(
    *,
    final_answer: str | None,
    terminated_reason: str,
    final_success: bool,
) -> str:
    if terminated_reason in _WAITING_TERMINAL_REASONS:
        return terminated_reason
    if final_success:
        return "completed"
    # A turn that ended with a genuine hand-off to the user (tight-marker
    # help request in the final answer) after an unresolved tool failure is
    # "needs your input", not a hard failure. Gated on a plain ``final_answer``
    # termination: guard impasses and repair-tier warnings already carry their
    # own presentation (guardBlocked / delivered-with-warning) and must not be
    # re-labelled as a user hand-off. ``allow_short_loose=False`` keeps this
    # honest — a short report that merely *mentions* token/权限 is not a
    # hand-off and must still surface as a failure.
    if (
        terminated_reason == "final_answer"
        and final_answer
        and _final_answer_requests_user_help(
            final_answer,
            allow_short_loose=False,
        )
    ):
        return "blocked_on_user"
    return "failed"


def _finalize_react_turn(
    *,
    terminated_reason: str,
    final_answer: str | None,
    i: int,
    react_task_id: Any,
    pause_controller: Any,
    messages: list,
    is_code_mode: bool,
    is_research_mode: bool,
    is_swarm_mode: bool,
    effective_model: str,
    router: Any,
    steps: list,
    executed_beak_steps: list,
    stack: Any,
    todo_protocol_required: bool,
    todo_protocol_visible: bool,
    file_inspection_tools_visible: bool,
    tools_active: bool,
    goal: str,
    browser_operation_mode: bool,
    final_guard_grounded_source_paths: Any,
    prior_grounding_text: str = "",
    final_answer_emitted: bool,
    model_iteration_timeout_s: Callable[[float | None], float],
    model_iteration_timeout_s_config: float | None = None,
    convergence_max_tokens: int = 2000,
) -> Generator[dict[str, Any], None, ReActResult | None]:
    """Run PHASE 7/8 and return the turn's ``ReActResult`` (or None).

    Yields the terminal events (``react_cancelled`` / ``text_delta`` /
    ``react_error`` / ``react_completed``); the caller drives this with
    ``return (yield from _finalize_react_turn(...))``.
    """
    # (paused / cancelled / forced max-iter convergence)
    if terminated_reason == "paused":
        final_answer = (
            "当前进度已暂停并保存，等待继续。你可以补充信息，或点击继续从 checkpoint 接着执行。"
        )

    if terminated_reason == "cancelled":
        # User pressed Stop. Emit a terminal event so the consumer can
        # finalize the turn promptly, then exit without asking the LLM
        # for one more "final answer" round — that would both waste
        # budget and defeat the whole point of cancellation.
        yield {"type": "react_cancelled", "iteration": i + 1}
        # Persist a cancelled trajectory so the cancellation is auditable
        # (tokens/cost spent up to the Stop) and so resume-side gating can
        # see the task reached a terminal state - otherwise a stale
        # auto-checkpoint makes a cancelled task look resumable.
        _persist_react_trajectory(
            stack,
            react_task_id=react_task_id,
            beak_steps=executed_beak_steps,
            success=False,
            disposition="cancelled",
        )
        with contextlib.suppress(Exception):
            pause_controller.clear(str(react_task_id))
            pause_controller.unregister_active(str(react_task_id))
        return None

    if final_answer is None:
        try:
            messages.append(
                Message(
                    role="user",
                    content=(
                        "已达最大迭代次数。当前是 code 模式: 如果仍有未完成 todo、未验证代码改动、"
                        "或存在权限/登录/信息缺失阻塞, 不要宣称完成; "
                        "请明确请求用户协助并列出被阻塞的 todo。"
                        "只有所有 todo completed 且验证通过, 才给 Final Answer。"
                        "回复时用对话语气回应用户, 而不是只列执行结果。"
                        if is_code_mode
                        else (
                            "已达最大迭代次数,请基于以上推理直接给出 Final Answer。"
                            "用对话语气回应用户的问题, 像真人交流一样自然, "
                            "不要只报告执行结果。"
                        )
                    ),
                )
            )
            if is_research_mode and not is_code_mode:
                messages.append(
                    Message(
                        role="user",
                        content=(
                            "研究报告收敛要求：不要继续输出过程模板或「正在整理」。"
                            "请基于已有搜索、浏览和材料证据，直接输出完整 Final Answer。"
                            "Final Answer 必须是一份可阅读报告，至少包含：执行摘要、关键结论、"
                            "分维度分析、对比/推荐、风险与不确定性、下一步建议、来源说明。"
                        ),
                    )
                )
            if is_swarm_mode and not is_code_mode:
                messages.append(
                    Message(
                        role="user",
                        content=(
                            "SWARM convergence requirement: stop generating "
                            "process-only updates. Based on completed todos, "
                            "skill outputs, subagent results, and blackboard "
                            "findings, produce the integrated Final Answer now. "
                            "Include a concise stage summary, final conclusions, "
                            "quality-review notes, and any created file paths. "
                            "If the work is blocked, name the exact blocker and "
                            "the incomplete todo instead of claiming completion."
                        ),
                    )
                )
            convergence_request = ModelRequest(
                model=effective_model,
                messages=messages,
                max_tokens=5000 if (is_research_mode or is_swarm_mode) else convergence_max_tokens,
                temperature=0.2,
            )
            convergence_result = _collect_model_stream_text_with_deadline(
                router,
                convergence_request,
                model_iteration_timeout_s(model_iteration_timeout_s_config),
            )
            if convergence_result is _MODEL_STREAM_DEADLINE:
                final_answer = _stage_update_timeout_fallback(steps)
                terminated_reason = "model_stall"
                note_model_stall(str(effective_model or ""))
                _logger.warning(
                    "react_loop forced convergence stream exceeded deadline; "
                    "preserving public stage conclusions",
                )
            else:
                assert isinstance(convergence_result, tuple)
                text, _convergence_response = convergence_result
            text = "" if final_answer is not None else text.strip()
            convergence_final = _extract_final_answer(text)
            if final_answer is not None:
                pass
            elif convergence_final:
                final_answer = convergence_final
            elif (
                text
                and not _ACTION_RE.search(text)
                and not _looks_like_observation_echo(text)
                and not _looks_like_special_tool_envelope(text)
                and "<tool_call>" not in text
                and "<tool_invocation" not in text
                and "<function=" not in text
            ):
                # Forced convergence is a direct, tools-disabled synthesis
                # call. Several compatible providers obey the content request
                # but omit the literal ``Final Answer:`` label. Treat that
                # plain report exactly like the main loop's zero-anchor chat
                # recovery instead of silently dropping a complete answer.
                final_answer = text
                _logger.info(
                    "react_loop forced convergence salvaged plain final · chars=%d",
                    len(text),
                )
            else:
                _logger.warning(
                    "react_loop 强制收敛未得安全 Final Answer · raw head=%r",
                    text[:200],
                )
                _persist_react_trajectory(
                    stack,
                    react_task_id=react_task_id,
                    beak_steps=executed_beak_steps,
                    success=False,
                    disposition="failed",
                )
                pause_controller.clear(str(react_task_id))
                pause_controller.unregister_active(str(react_task_id))
                return None

            if final_answer and terminated_reason != "model_stall":
                _forced_step = ReActStep(
                    iteration=(steps[-1].iteration + 1) if steps else 1,
                    action="none",
                )
                _guard_hit = _evaluate_final_answer_guards(
                    steps=steps,
                    step=_forced_step,
                    final_answer=final_answer,
                    is_code_mode=is_code_mode,
                    todo_protocol_required=todo_protocol_required,
                    todo_protocol_visible=todo_protocol_visible,
                    file_inspection_tools_visible=file_inspection_tools_visible,
                    tools_active=tools_active,
                    goal=goal,
                    browser_operation_mode=browser_operation_mode,
                    grounded_source_paths=final_guard_grounded_source_paths,
                    model=effective_model,
                    prior_grounding_text=prior_grounding_text,
                )
                if _guard_hit is not None:
                    # The terminal convergence call is the last chance to
                    # deliver.  If its only defect is leaked ReAct protocol,
                    # keep the useful prose and remove the private tool lane
                    # instead of turning a successful turn into a guard
                    # impasse (or asking the model to retry indefinitely).
                    _downgrade = _try_clean_downgrade(final_answer)
                    if _downgrade is not None:
                        final_answer = _downgrade
                        terminated_reason = "final_answer_with_warning"
                        _guard_hit = None

                if _guard_hit is not None:
                    _guard_label, _guard_message = _guard_hit
                    _user_guard_message = _guard_reason_for_user(_guard_label, _guard_message)
                    final_answer = (
                        "我还不能把这个任务标记为完成。\n\n"
                        f"{_user_guard_message}\n\n"
                        "请点击继续让我接着执行, 或提供必要的权限/登录/信息后我再继续。"
                    )
                    terminated_reason = "guard_impasse"
        except (AttributeError, TypeError, ValueError) as exc:
            _logger.warning(
                "react_loop 强制收敛失败 (%s): %s",
                type(exc).__name__,
                _safe_react_error_message(exc),
            )
            _persist_react_trajectory(
                stack,
                react_task_id=react_task_id,
                beak_steps=executed_beak_steps,
                success=False,
                disposition="failed",
            )
            pause_controller.clear(str(react_task_id))
            pause_controller.unregister_active(str(react_task_id))
            # This is the final model attempt after the ordinary recovery
            # retries have been exhausted.  Surface its real, redacted cause
            # so the gateway and evaluation harness can distinguish provider
            # infrastructure failures from missing agent output.
            yield {
                "type": "react_error",
                "kind": type(exc).__name__,
                "message": _safe_react_error_message(exc),
                "iteration": (steps[-1].iteration + 1) if steps else 1,
                "task_id": str(react_task_id) if react_task_id else None,
                "terminal_stage": "forced_convergence",
            }
            return None

    if final_answer and not final_answer_emitted:
        # ── finalization + react_completed yield ─────────────
        _emit_assistant_chunk(
            stack,
            iteration=(steps[-1].iteration + 1) if steps else 1,
            delta=final_answer,
            task_id=react_task_id,
        )
        yield {
            "type": "text_delta",
            "delta": final_answer,
            "iteration": (steps[-1].iteration + 1) if steps else 1,
        }
        final_answer_emitted = True

    unresolved_tool_failure = _has_unrecovered_beak_failure(executed_beak_steps)
    effective_success = not unresolved_tool_failure and terminated_reason != "model_stall"
    structured_blocked_on_user = _has_structured_user_block(executed_beak_steps)
    # Compatibility only for historical/custom tool adapters that predate
    # executor-authored waiting_user tags. New executions use the structured
    # signal above and do not depend on how the model phrases its answer.
    legacy_blocked_on_user = bool(
        not effective_success
        and not structured_blocked_on_user
        and terminated_reason == "final_answer"
        and final_answer
        and _final_answer_requests_user_help(final_answer, allow_short_loose=False)
    )
    completion_decision = decide_completion(
        terminated_reason=terminated_reason,
        effective_success=effective_success,
        blocked_on_user=structured_blocked_on_user or legacy_blocked_on_user,
    )
    final_success = completion_decision.success
    disposition = completion_decision.outcome
    failure = classify_turn_failure(executed_beak_steps)
    _persist_react_trajectory(
        stack,
        react_task_id=react_task_id,
        beak_steps=executed_beak_steps,
        success=effective_success,
        disposition=disposition,
    )
    try:
        from runtime.safety.experiments.scheduler import (
            get_camouflage_scheduler,
        )

        get_camouflage_scheduler().record_outcome(
            str(react_task_id),
            success=final_success,
        )
    except ImportError:
        _logger.debug("camouflage scheduler not available for recording outcome", exc_info=True)
    # Any terminal state except "paused" must clear the pause/pending record —
    # paused means we are genuinely waiting for the user to click Continue,
    # but success / error / model_stall / guard_impasse all mean the turn has
    # ended and no resume will ever come. Leaving a stale record behind makes
    # the sidebar thread glow yellow ("waiting") forever.
    if terminated_reason != "paused":
        pause_controller.clear(str(react_task_id))
    pause_controller.unregister_active(str(react_task_id))
    completion_receipt = _react_completion_receipt(
        final_answer=final_answer,
        terminated_reason=terminated_reason,
        effective_success=effective_success,
        executed_beak_steps=executed_beak_steps,
        completion_decision=completion_decision.to_dict(),
    )
    yield {
        "type": "react_completed",
        "iteration": steps[-1].iteration if steps else 0,
        "terminated_reason": terminated_reason,
        "has_final_answer": bool(final_answer),
        "success": final_success,
        "disposition": disposition,
        "completion_decision": completion_decision.to_dict(),
        "failure": failure,
        "completion_receipt": completion_receipt,
    }
    return ReActResult(
        final_answer=final_answer,
        steps=steps,
        terminated_reason=terminated_reason,
        success=final_success,
        completion_receipt=completion_receipt,
        completion_decision=completion_decision.to_dict(),
    )
