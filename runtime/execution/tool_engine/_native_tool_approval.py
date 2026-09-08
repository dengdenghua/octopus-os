"""Exact native-call approval through the server's current session provider."""

from __future__ import annotations

import json
from typing import Any

from runtime.platform.process.session import current_session
from runtime.safety.approval.approval_gate import (
    ApprovalRequest,
    AutoDenyProvider,
    approval_action_for_tool,
)
from runtime.safety.approval.cancellation import current_cancellation_token
from runtime.safety.validation.prompt_injection import (
    injection_gate_already_handled,
    injection_taint_gates,
)

NATIVE_TOOL_APPROVAL_TIMEOUT_S = 120.0


class NativeToolResult(tuple):
    """Keep the two-value public result while carrying trusted control state.

    Control fields are written only by the approval gate or executor adapter,
    never parsed from a handler's output or a model string. Ordinary tuple
    unpacking is unchanged.
    """

    approval_blocked: str
    execution_blocked: str
    effect_receipt: dict[str, Any] | None

    def __new__(
        cls,
        text: str,
        *,
        reason: str = "",
        is_error: bool = True,
        execution_blocked: str = "",
        effect_receipt: dict[str, Any] | None = None,
    ) -> NativeToolResult:
        result = super().__new__(cls, (text, is_error))
        result.approval_blocked = reason
        result.execution_blocked = execution_blocked
        result.effect_receipt = dict(effect_receipt) if effect_receipt is not None else None
        return result


def approve_native_call(call: Any) -> bool | NativeToolResult:
    """Return whether this gate handled approval, or a terminal refusal."""
    token = current_cancellation_token()
    if token.is_cancelled:
        return NativeToolResult("Tool execution was cancelled.", reason="cancelled")
    session = current_session()
    metadata = getattr(session, "metadata", None) or {}
    arguments_text = json.dumps(call.arguments, ensure_ascii=False, sort_keys=True)
    preview = arguments_text[:500]
    risk, action, _policy = approval_action_for_tool(
        call.name, arguments_text, policy=metadata.get("approval_risk_policy")
    )
    if action == "deny":
        return NativeToolResult("Tool execution was denied by policy.", reason="policy_denied")

    # Permission aliases are normalized at the server boundary, but native
    # tool calls can also arrive through legacy/headless loops.  Normalize a
    # second time here so an alias cannot silently acquire a different
    # meaning in this last-mile gate. ``acceptEdits`` means automatic
    # *review*, not unconditional approval: its provider must inspect every
    # elevated call. Only an explicitly authorized ``auto_approve`` flag or
    # the server-owned bypass mode may skip this provider.
    from runtime.safety.approval.permission_modes import canonical_permission_mode

    permission_mode = canonical_permission_mode(metadata.get("permission_mode"))
    automatic = bool(metadata.get("auto_approve")) or permission_mode == "bypassPermissions"
    tainted = (
        injection_taint_gates()
        and not injection_gate_already_handled()
        and risk.level in {"medium", "high", "critical"}
    )
    if tainted:
        automatic = False
        action = "ask"
    if automatic:
        return True
    if action not in {"ask", "confirm"}:
        return False

    provider = metadata.get("_approval_provider") or AutoDenyProvider()
    try:
        decision = provider.request(
            ApprovalRequest(
                thread_id=str(getattr(session, "thread_id", None) or ""),
                tool_name=call.name,
                tool_call_id=call.id,
                args_preview=preview,
                detail=f"{call.name} wants to execute (risk={risk.level}: {risk.reason})",
            ),
            timeout=NATIVE_TOOL_APPROVAL_TIMEOUT_S,
        )
    except Exception:  # noqa: BLE001 - a broken approval transport must fail closed
        return NativeToolResult(
            "Tool approval could not be obtained.", reason="approval_unavailable"
        )
    if token.is_cancelled:
        return NativeToolResult("Tool execution was cancelled.", reason="cancelled")
    if not getattr(decision, "approved", False):
        return NativeToolResult("Tool execution was not approved.", reason="approval_not_granted")
    return True


__all__ = ["NativeToolResult", "approve_native_call"]
