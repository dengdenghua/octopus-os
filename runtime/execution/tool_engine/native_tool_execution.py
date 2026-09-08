"""Execute a model-native tool call through the Echo executor boundary."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import replace
from typing import Any
from uuid import uuid4

from runtime.execution.misc.skill_policy import audit_read_only_tool_denial
from runtime.platform.models import ArmId, Budget, BudgetLimits, SkillId, TaskId
from runtime.platform.process.session import current_session, session_scope
from runtime.safety.approval.cancellation import current_cancellation_token
from runtime.safety.validation.prompt_injection import (
    injection_gate_already_handled,
    set_injection_gate_handled,
)

from ._native_tool_approval import NativeToolResult, approve_native_call
from .effect_receipts import EFFECT_RECEIPT_SCHEMA, should_refresh_read_result
from .tool_output_pruner import TOOL_RESULT_PRUNE_ENABLED
from .tool_output_spill import TOOL_RESULT_SPILL_ENABLED
from .tool_protocol import (
    normalize_step_tool_result,
    normalize_tool_call,
    normalize_tool_result,
)

TOOL_OUTPUT_MAX_CHARS = 16_000


def execute_native_tool_call(
    stack: Any,
    call: Any,
    *,
    max_chars: int = TOOL_OUTPUT_MAX_CHARS,
    prune_middle: bool = TOOL_RESULT_PRUNE_ENABLED,
    spill_oversized: bool = TOOL_RESULT_SPILL_ENABLED,
    task_id: TaskId | None = None,
    step_id: int = 0,
    arm_id: ArmId | None = None,
    budget: Budget | None = None,
) -> tuple[str, bool]:
    """Run one native tool request through the normal executor chokepoint.

    Returns model-facing ``(output_text, is_error)`` with the same output
    bounds and policy checks used by the gateway-native loop. Lightweight
    executor doubles retain the historical direct-handler fallback.
    """

    from runtime.safety.privacy import tool_privacy_denial

    executor = getattr(stack, "executor", None)
    if executor is None:
        return ("(executor unavailable)", True)
    try:
        normalized = normalize_tool_call(call, origin="native")
    except ValueError as exc:
        return (f"(invalid tool call: {exc})", True)

    try:
        registry = executor.registry
        if not registry.has(normalized.name):
            return (f"(skill not found: {normalized.name})", True)
        privacy_denial = tool_privacy_denial(registry.get(normalized.name))
        if privacy_denial:
            return NativeToolResult(privacy_denial, execution_blocked="privacy_egress_blocked")
        try:
            if not registry.is_enabled(normalized.name):
                return (f"(skill disabled: {normalized.name})", True)
        except (AttributeError, TypeError, ValueError):
            pass
        skill = registry.get(normalized.name)
    except (AttributeError, TypeError, KeyError) as exc:
        return (f"(registry error: {exc})", True)

    policy_session = current_session()
    audit_denial = audit_read_only_tool_denial(
        normalized.name,
        normalized.arguments,
        context=getattr(policy_session, "metadata", None) or {},
    )
    if audit_denial is not None:
        return (audit_denial, True)

    approval = approve_native_call(normalized)
    if isinstance(approval, NativeToolResult):
        return approval
    # Approval belongs to this exact invocation. Do not modify the shared
    # Session (parallel tools must still obtain their own permission), or set
    # auto_approve for nested native calls. Other executor policy gates remain.
    execution_session = policy_session
    if (
        approval
        and policy_session is not None
        and policy_session.metadata.get("enforce_executor_approval")
    ):
        execution_session = replace(
            policy_session,
            metadata={**policy_session.metadata, "enforce_executor_approval": False},
        )
    previous_handled = injection_gate_already_handled()
    try:
        if approval:
            set_injection_gate_handled(True)
        with session_scope(execution_session) if execution_session is not None else nullcontext():
            if current_cancellation_token().is_cancelled:
                return NativeToolResult("Tool execution was cancelled.", reason="cancelled")
            return _execute_approved_native_call(
                executor,
                skill,
                normalized,
                max_chars=max_chars,
                prune_middle=prune_middle,
                spill_oversized=spill_oversized,
                task_id=task_id,
                step_id=step_id,
                arm_id=arm_id,
                budget=budget,
            )
    finally:
        set_injection_gate_handled(previous_handled)


def _execute_approved_native_call(
    executor: Any,
    skill: Any,
    normalized: Any,
    *,
    max_chars: int,
    prune_middle: bool,
    spill_oversized: bool,
    task_id: TaskId | None,
    step_id: int,
    arm_id: ArmId | None,
    budget: Budget | None,
) -> tuple[str, bool]:
    effect_receipt = None
    if hasattr(executor, "execute_step"):
        try:
            resolved_task_id = task_id or TaskId(uuid4())
            resolved_arm_id = arm_id or ArmId("agentic")
            session = current_session()
            step = executor.execute_step(
                step_id,
                f"agentic:{normalized.id}",
                SkillId(normalized.name),
                dict(normalized.arguments),
                caller="agentic",
                task_id=resolved_task_id,
                arm_id=resolved_arm_id,
                budget=budget
                or Budget(
                    resolved_task_id,
                    BudgetLimits(tokens=100_000, usd=10.0),
                ),
                actor=session.actor if session is not None else None,
            )
            output = step.result.output
            receipt = getattr(step.result, "effect_receipt", None)
            if (
                isinstance(receipt, dict)
                and receipt.get("schema") == EFFECT_RECEIPT_SCHEMA
                and receipt.get("sealed") is True
                and receipt.get("emitted_by") == "tool_executor"
            ):
                effect_receipt = receipt
                # Successful protected reads intentionally bypass cached
                # receipts to re-check today's grant/ACL. A rewritten result
                # or a failed handler never receives that exception.
                refreshed_read = (
                    should_refresh_read_result(skill)
                    and step.result.status == "success"
                    and not receipt.get("reason")
                )
                if receipt.get("state") == "indeterminate" and not refreshed_read:
                    return NativeToolResult(
                        "Tool effects are indeterminate; execution has stopped. "
                        "Check the actual result before deciding whether to retry.",
                        execution_blocked="indeterminate_side_effect",
                        effect_receipt=effect_receipt,
                    )
            if step.result.status != "success":
                result = normalize_step_tool_result(
                    step,
                    origin="native",
                    max_chars=max_chars,
                    prune_middle=prune_middle,
                    spill_oversized=spill_oversized,
                    tool_name=normalized.name,
                )
                reason = step.result.error_type or step.result.status
                return NativeToolResult(
                    result.rendered or f"({reason})", effect_receipt=effect_receipt
                )
        except (RuntimeError, ValueError, TypeError, OSError) as exc:
            # An executor/receipt failure may occur after a handler returned.
            # Do not turn it into an ordinary observation the model can retry.
            return NativeToolResult(
                f"(skill error: {type(exc).__name__}: {exc})",
                execution_blocked="tool_execution_unavailable",
            )
    else:
        try:
            output = skill.handler(**normalized.arguments)
        except TypeError as exc:
            return (f"(TypeError: {exc})", True)
        except (RuntimeError, ValueError, OSError) as exc:
            return (f"(skill error: {type(exc).__name__}: {exc})", True)

    result = normalize_tool_result(
        normalized,
        output,
        origin="native",
        max_chars=max_chars,
        prune_middle=prune_middle,
        spill_oversized=spill_oversized,
        tool_name=normalized.name,
    )
    return NativeToolResult(
        result.rendered, is_error=result.is_error, effect_receipt=effect_receipt
    )


__all__ = ["TOOL_OUTPUT_MAX_CHARS", "execute_native_tool_call"]
