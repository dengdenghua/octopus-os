"""One canonical pre-execution governance decision for every tool call.

Echo historically had the right safety primitives but composed them in
several places: task capability switches, the approval risk matrix, global
capability switches, and prompt-injection taint.  This module gives those
checks a shared instruction model and a four-outcome decision contract:

``allow``
    Execute unchanged.
``deny``
    Do not execute; policy made a final rejection.
``hold``
    Do not execute yet; an interactive approval is required.
``rewrite``
    Execute the sanitized arguments and retain evidence of what was removed.

The evaluator deliberately owns no UI or transport concerns.  Its result is
JSON-safe and can be written to traces or rendered in any client.
"""

from __future__ import annotations

import shlex
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal
from urllib.parse import urlsplit

from runtime.platform.observability.redactor import redact_dict
from runtime.platform.process.utils import safe_repr
from runtime.safety.approval.approval_gate import (
    ApprovalRisk,
    ApprovalRiskPolicy,
    assess_approval_risk,
    injection_taint_block,
)
from runtime.safety.validation.prompt_injection import current_injection_taint


class GovernanceOutcome(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    HOLD = "hold"
    REWRITE = "rewrite"


@dataclass(frozen=True, slots=True)
class ExecutionPolicyContext:
    """Session policy inputs that are independent of the tool itself."""

    enforce_approval: bool = False
    auto_approve: bool = False
    bypass_approval: bool = False
    approval_risk_policy: ApprovalRiskPolicy = field(default_factory=ApprovalRiskPolicy)

    @classmethod
    def from_metadata(cls, metadata: Mapping[str, Any] | None) -> ExecutionPolicyContext:
        value = metadata if isinstance(metadata, Mapping) else {}
        permission_mode = str(value.get("permission_mode") or "").strip().lower()
        raw_risk_policy = value.get("approval_risk_policy")
        risk_policy = (
            raw_risk_policy
            if isinstance(raw_risk_policy, ApprovalRiskPolicy)
            else ApprovalRiskPolicy.from_mapping(raw_risk_policy)
        )
        return cls(
            enforce_approval=bool(value.get("enforce_executor_approval")),
            auto_approve=bool(value.get("auto_approve")),
            bypass_approval=permission_mode in {"bypasspermissions", "bypass-permissions"},
            approval_risk_policy=risk_policy,
        )


@dataclass(frozen=True, slots=True)
class ExecutionInstruction:
    """Transport-neutral description of a proposed side effect."""

    instruction_id: str
    tool_name: str
    caller: str
    args_preview: str
    target: str | None
    risk: ApprovalRisk
    taint: str
    reversible: bool
    parent_instruction_id: str | None = None
    references: tuple[str, ...] = ()
    rewritten_fields: tuple[str, ...] = ()
    schema: Literal["echo.execution_instruction.v1"] = "echo.execution_instruction.v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "instruction_id": self.instruction_id,
            "tool_name": self.tool_name,
            "caller": self.caller,
            "args_preview": self.args_preview,
            "target": self.target,
            "risk": self.risk.to_dict(),
            "taint": self.taint,
            "reversible": self.reversible,
            "parent_instruction_id": self.parent_instruction_id,
            "references": list(self.references),
            "rewritten_fields": list(self.rewritten_fields),
        }


@dataclass(frozen=True, slots=True)
class GovernanceDecision:
    outcome: GovernanceOutcome
    instruction: ExecutionInstruction
    gate: str
    reason: str
    approval_action: str = "allow"
    approval_policy: dict[str, str] = field(default_factory=dict)
    schema: Literal["echo.governance_decision.v1"] = "echo.governance_decision.v1"

    @property
    def may_execute(self) -> bool:
        return self.outcome in {GovernanceOutcome.ALLOW, GovernanceOutcome.REWRITE}

    @property
    def requires_approval(self) -> bool:
        return self.outcome is GovernanceOutcome.HOLD

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "outcome": self.outcome.value,
            "gate": self.gate,
            "reason": self.reason,
            "approval_action": self.approval_action,
            "approval_policy": dict(self.approval_policy),
            # Kept at the top level for approval UIs that should not need to
            # understand the full instruction envelope on day one.
            "tool_name": self.instruction.tool_name,
            "risk": self.instruction.risk.to_dict(),
            "args_preview": self.instruction.args_preview,
            "instruction": self.instruction.to_dict(),
        }


def build_execution_instruction(
    *,
    instruction_id: str,
    tool_name: str,
    caller: str,
    args: Mapping[str, Any],
    rewritten_fields: tuple[str, ...] = (),
    parent_instruction_id: str | None = None,
    references: tuple[str, ...] = (),
) -> ExecutionInstruction:
    preview = _args_preview(args)
    return ExecutionInstruction(
        instruction_id=instruction_id,
        tool_name=tool_name,
        caller=caller,
        args_preview=preview,
        target=_target_preview(args),
        risk=assess_approval_risk(tool_name, preview),
        taint=current_injection_taint(),
        reversible=_is_reversible(tool_name),
        parent_instruction_id=parent_instruction_id,
        references=references,
        rewritten_fields=tuple(sorted(set(rewritten_fields))),
    )


def evaluate_execution_policy(
    instruction: ExecutionInstruction,
    *,
    context: ExecutionPolicyContext | None = None,
    task_capability: tuple[bool, str | None] = (True, None),
    capability: tuple[bool, str | None] = (True, None),
) -> GovernanceDecision:
    """Evaluate gates in the executor's established fail-closed order."""

    policy_context = context or ExecutionPolicyContext()

    if not task_capability[0]:
        return _decision(
            GovernanceOutcome.DENY,
            instruction,
            gate="task_capability",
            reason=task_capability[1] or "task capability disabled",
            approval_action="deny",
            policy=policy_context.approval_risk_policy,
        )

    if (
        policy_context.enforce_approval
        and not policy_context.auto_approve
        and not policy_context.bypass_approval
    ):
        action = policy_context.approval_risk_policy.action_for(instruction.risk)
        if action not in {"allow", "audit"}:
            reason = (
                f"approval required before executing {instruction.tool_name} "
                f"(risk={instruction.risk.level}: {instruction.risk.reason}; action={action})"
            )
            return _decision(
                GovernanceOutcome.DENY if action == "deny" else GovernanceOutcome.HOLD,
                instruction,
                gate="approval",
                reason=reason,
                approval_action=action,
                policy=policy_context.approval_risk_policy,
            )

    if not capability[0]:
        return _decision(
            GovernanceOutcome.DENY,
            instruction,
            gate="capability",
            reason=capability[1] or "capability disabled",
            approval_action="deny",
            policy=policy_context.approval_risk_policy,
        )

    taint_reason = injection_taint_block(
        instruction.tool_name,
        instruction.args_preview,
    )
    if taint_reason is not None:
        return _decision(
            GovernanceOutcome.DENY,
            instruction,
            gate="injection_taint",
            reason=taint_reason,
            approval_action="deny",
            policy=policy_context.approval_risk_policy,
        )

    if instruction.rewritten_fields:
        fields = ", ".join(instruction.rewritten_fields)
        return _decision(
            GovernanceOutcome.REWRITE,
            instruction,
            gate="argument_sanitizer",
            reason=f"removed model-controlled privilege fields: {fields}",
            policy=policy_context.approval_risk_policy,
        )

    return _decision(
        GovernanceOutcome.ALLOW,
        instruction,
        gate="policy",
        reason="all pre-execution gates passed",
        policy=policy_context.approval_risk_policy,
    )


def _decision(
    outcome: GovernanceOutcome,
    instruction: ExecutionInstruction,
    *,
    gate: str,
    reason: str,
    approval_action: str = "allow",
    policy: ApprovalRiskPolicy,
) -> GovernanceDecision:
    return GovernanceDecision(
        outcome=outcome,
        instruction=instruction,
        gate=gate,
        reason=reason,
        approval_action=approval_action,
        approval_policy=policy.to_dict(),
    )


def _args_preview(args: Mapping[str, Any]) -> str:
    try:
        sanitized = redact_dict(_redact_sensitive_fields(dict(args)))
        return repr(safe_repr(sanitized))[:500]
    except Exception:  # noqa: BLE001 - audit preview must never break execution
        return "<unavailable>"


def _redact_sensitive_fields(value: Any) -> Any:
    sensitive_fragments = (
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "credential",
        "password",
        "secret",
        "token",
    )
    if isinstance(value, dict):
        redacted: dict[Any, Any] = {}
        for key, child in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            redacted[key] = (
                "[REDACTED:field]"
                if any(fragment in normalized for fragment in sensitive_fragments)
                else _redact_sensitive_fields(child)
            )
        return redacted
    if isinstance(value, list):
        return [_redact_sensitive_fields(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_sensitive_fields(item) for item in value)
    return value


def _target_preview(args: Mapping[str, Any]) -> str | None:
    for key in ("path", "file_path", "filepath"):
        raw = args.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()[:240]

    raw_url = args.get("url")
    if isinstance(raw_url, str) and raw_url.strip():
        try:
            parsed = urlsplit(raw_url.strip())
            if parsed.scheme and parsed.netloc:
                return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"[:240]
        except ValueError:
            return "<invalid-url>"

    command = args.get("command") or args.get("cmd")
    if isinstance(command, str) and command.strip():
        try:
            tokens = shlex.split(command)
        except ValueError:
            tokens = command.split()
        return f"command:{tokens[0]}" if tokens else None
    return None


def _is_reversible(tool_name: str) -> bool:
    name = tool_name.strip().lower()
    irreversible_prefixes = (
        "delete_",
        "send_",
        "email_",
        "slack_",
        "publish_",
        "deploy_",
        "mouse_",
        "keyboard_",
        "computer_execute",
    )
    return not name.startswith(irreversible_prefixes)


__all__ = [
    "ExecutionInstruction",
    "ExecutionPolicyContext",
    "GovernanceDecision",
    "GovernanceOutcome",
    "build_execution_instruction",
    "evaluate_execution_policy",
]
