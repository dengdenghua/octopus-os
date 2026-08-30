"""Unified trust decisions for tool execution.

This module is an audit layer on top of ``approval_gate``. The existing
approval gate answers "should this tool ask the user?". The trust gateway
adds a stable explanation shape that can be written to trace storage,
including static permission rules, risk category, and final action.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Literal

from runtime.safety.approval.approval_gate import (
    ApprovalDecision,
    ApprovalPolicy,
    ApprovalProvider,
    ApprovalRequest,
    ApprovalRiskAction,
    ApprovalRiskPolicy,
    approval_action_for_tool,
)

TrustDecisionSource = Literal["static_policy", "risk_policy"]


@dataclass(frozen=True, slots=True)
class TrustDecision:
    tool_name: str
    action: ApprovalRiskAction
    source: TrustDecisionSource
    approved: bool | None
    reason: str
    risk: dict[str, object]
    risk_policy: dict[str, str]
    static_decision: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "echo.trust_decision.v1",
            "tool_name": self.tool_name,
            "action": self.action,
            "source": self.source,
            "approved": self.approved,
            "reason": self.reason,
            "risk": self.risk,
            "risk_policy": self.risk_policy,
            "static_decision": self.static_decision,
        }


def evaluate_tool_trust(
    req: ApprovalRequest,
    *,
    static_policy: ApprovalPolicy | None = None,
    risk_policy: ApprovalRiskPolicy | dict[str, str] | None = None,
) -> TrustDecision:
    risk, action, resolved_policy = approval_action_for_tool(
        req.tool_name,
        req.args_preview,
        policy=risk_policy,
    )
    static = static_policy.decide(req) if static_policy is not None else None
    if static is not None:
        return TrustDecision(
            tool_name=req.tool_name,
            action="allow" if static.approved else "deny",
            source="static_policy",
            approved=static.approved,
            reason=static.reason or "",
            risk=risk.to_dict(),
            risk_policy=resolved_policy.to_dict(),
            static_decision="allow" if static.approved else "deny",
        )
    return TrustDecision(
        tool_name=req.tool_name,
        action=action,
        source="risk_policy",
        approved=True if action in {"allow", "audit"} else None,
        reason=risk.reason,
        risk=risk.to_dict(),
        risk_policy=resolved_policy.to_dict(),
    )


def trace_metadata_for_tool(
    req: ApprovalRequest,
    *,
    detail: str = "",
    static_policy: ApprovalPolicy | None = None,
    risk_policy: ApprovalRiskPolicy | dict[str, str] | None = None,
) -> dict[str, object]:
    return {
        "detail": detail or req.detail,
        "trust_gateway": evaluate_tool_trust(
            req,
            static_policy=static_policy,
            risk_policy=risk_policy,
        ).to_dict(),
    }


class TrustGatewayApprovalProvider(ApprovalProvider):
    """Rule-aware approval provider that audits static decisions.

    Static allow/deny rules short-circuit the fallback provider exactly
    like ``RuleBasedProvider``. The difference is observability: every
    static decision can be written to ``AgentTraceStore.approvals`` so
    the TaskRun ledger can explain why an action ran or was refused.
    """

    def __init__(
        self,
        *,
        static_policy: ApprovalPolicy,
        fallback: ApprovalProvider,
        trace_store: Any = None,
        turn_id: str | None = None,
        agent_id: str | None = None,
    ) -> None:
        self._static_policy = static_policy
        self._fallback = fallback
        self._trace_store = trace_store
        self._turn_id = turn_id
        self._agent_id = agent_id

    def request(self, req: ApprovalRequest, *, timeout: float = 120.0) -> ApprovalDecision:
        decision = self._static_policy.decide(req)
        if decision is not None:
            self._record_static_decision(req, decision)
            return decision
        return self._fallback.request(req, timeout=timeout)

    def _record_static_decision(
        self,
        req: ApprovalRequest,
        decision: ApprovalDecision,
    ) -> None:
        if self._trace_store is None:
            return
        try:
            self._trace_store.record_approval(
                thread_id=req.thread_id,
                turn_id=self._turn_id,
                agent_id=self._agent_id,
                tool_name=req.tool_name,
                tool_call_id=req.tool_call_id,
                args_preview=req.args_preview,
                decision="approved" if decision.approved else "rejected",
                reason=decision.reason or "",
                metadata=trace_metadata_for_tool(
                    req,
                    static_policy=self._static_policy,
                ),
            )
        except Exception:
            return


def summarize_trust_denials(
    approvals: list[dict[str, Any]],
    *,
    limit: int = 10,
) -> dict[str, Any]:
    denied: list[dict[str, Any]] = []
    for row in approvals:
        metadata = row.get("metadata")
        trust = metadata.get("trust_gateway") if isinstance(metadata, dict) else None
        action = trust.get("action") if isinstance(trust, dict) else None
        decision = str(row.get("decision") or "")
        if decision != "rejected" and action not in {"deny", "block", "halt"}:
            continue
        tool_name = str(row.get("tool_name") or (trust or {}).get("tool_name") or "")
        denied.append(
            {
                "id": row.get("id"),
                "ts": row.get("requested_at") or row.get("decided_at") or row.get("ts"),
                "thread_id": row.get("thread_id"),
                "turn_id": row.get("turn_id"),
                "agent_id": row.get("agent_id"),
                "tool_name": tool_name,
                "decision": decision or "rejected",
                "action": action or "deny",
                "reason": row.get("reason")
                or ((trust or {}).get("reason") if isinstance(trust, dict) else ""),
                "risk_level": (
                    ((trust.get("risk") or {}).get("level"))
                    if isinstance(trust, dict) and isinstance(trust.get("risk"), dict)
                    else ""
                ),
            }
        )
    by_tool = Counter(item["tool_name"] for item in denied if item["tool_name"])
    by_action = Counter(item["action"] for item in denied if item["action"])
    return {
        "schema": "echo.trust_denial_summary.v1",
        "total": len(denied),
        "by_tool": dict(by_tool),
        "by_action": dict(by_action),
        "recent": denied[-max(1, int(limit)) :],
    }


__all__ = [
    "TrustDecision",
    "TrustDecisionSource",
    "TrustGatewayApprovalProvider",
    "evaluate_tool_trust",
    "summarize_trust_denials",
    "trace_metadata_for_tool",
]
