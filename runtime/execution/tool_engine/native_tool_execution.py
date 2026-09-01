"""Execute a model-native tool call through the Echo executor boundary."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from runtime.execution.misc.skill_policy import audit_read_only_tool_denial
from runtime.platform.models import ArmId, Budget, BudgetLimits, SkillId, TaskId
from runtime.platform.process.session import current_session

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
                return (result.rendered or f"({reason})", True)
        except (RuntimeError, ValueError, TypeError, OSError) as exc:
            return (f"(skill error: {type(exc).__name__}: {exc})", True)
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
    return (result.rendered, result.is_error)


__all__ = ["TOOL_OUTPUT_MAX_CHARS", "execute_native_tool_call"]
