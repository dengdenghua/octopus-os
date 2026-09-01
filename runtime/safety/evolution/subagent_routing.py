"""Routing policy that uses subagent fitness before delegated work runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from runtime.safety.evolution.subagent_fitness import compute_subagent_fitness

RouteAction = Literal["allow", "allow_with_warning", "block"]
RiskLevel = Literal["low", "medium", "high", "critical"]


@dataclass(frozen=True)
class SubagentRouteDecision:
    role: str
    action: RouteAction
    reason: str
    risk_level: RiskLevel
    verdict: str = "unknown"
    score: float | None = None
    confidence: float = 0.0
    evidence_item_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "echo.subagent_route_decision.v1",
            "role": self.role,
            "action": self.action,
            "reason": self.reason,
            "risk_level": self.risk_level,
            "verdict": self.verdict,
            "score": self.score,
            "confidence": self.confidence,
            "evidence_item_ids": list(self.evidence_item_ids),
        }


def decide_subagent_route(
    *,
    role: str,
    risk_level: str | None = None,
    review_queue_path: str | Path | None = None,
    subagent_policy_path: str | Path | None = None,
    enabled: bool = True,
) -> SubagentRouteDecision:
    role_id = _clean(role, limit=80)
    risk = _risk_level(risk_level)
    if not enabled:
        return SubagentRouteDecision(
            role=role_id,
            action="allow",
            reason="subagent fitness routing disabled",
            risk_level=risk,
        )
    if not role_id:
        return SubagentRouteDecision(
            role="",
            action="allow",
            reason="no role id to score",
            risk_level=risk,
        )

    policy = _operator_policy(
        role_id,
        review_queue_path=review_queue_path,
        subagent_policy_path=subagent_policy_path,
    )
    if policy is not None and policy.get("status") == "retired":
        return SubagentRouteDecision(
            role=role_id,
            action="block",
            reason=(
                f"{role_id} was retired by operator policy"
                + (f": {policy.get('reason')}" if policy.get("reason") else "")
            ),
            risk_level=risk,
            verdict="operator_retired",
            score=0.0,
            confidence=1.0,
            evidence_item_ids=[
                _clean(item, limit=80)
                for item in (policy.get("evidence_item_ids") or [])
                if _clean(item, limit=80)
            ],
        )
    if policy is not None and policy.get("status") == "watch":
        return SubagentRouteDecision(
            role=role_id,
            action="allow_with_warning",
            reason=(
                f"{role_id} is on the operator watchlist"
                + (f": {policy.get('reason')}" if policy.get("reason") else "")
            ),
            risk_level=risk,
            verdict="operator_watch",
            score=None,
            confidence=1.0,
            evidence_item_ids=[
                _clean(item, limit=80)
                for item in (policy.get("evidence_item_ids") or [])
                if _clean(item, limit=80)
            ],
        )

    report = compute_subagent_fitness(
        review_queue_path=review_queue_path,
        role=role_id,
    )
    roles = report.get("roles") if isinstance(report, dict) else []
    role_report = roles[0] if isinstance(roles, list) and roles else None
    if not isinstance(role_report, dict):
        return SubagentRouteDecision(
            role=role_id,
            action="allow",
            reason="no reviewed fitness samples for this subagent",
            risk_level=risk,
        )

    verdict = _clean(role_report.get("verdict"), limit=80) or "unknown"
    score = _float_or_none(role_report.get("score"))
    confidence = float(role_report.get("confidence") or 0.0)
    evidence = [
        _clean(item, limit=80)
        for item in (role_report.get("evidence_item_ids") or [])
        if _clean(item, limit=80)
    ]

    if verdict == "retire_candidate" and risk in {"high", "critical"}:
        return SubagentRouteDecision(
            role=role_id,
            action="block",
            reason=(f"{role_id} is a retire_candidate and this task is {risk}-risk"),
            risk_level=risk,
            verdict=verdict,
            score=score,
            confidence=confidence,
            evidence_item_ids=evidence,
        )
    if verdict == "retire_candidate":
        return SubagentRouteDecision(
            role=role_id,
            action="allow_with_warning",
            reason=f"{role_id} is a retire_candidate; route only low-risk work",
            risk_level=risk,
            verdict=verdict,
            score=score,
            confidence=confidence,
            evidence_item_ids=evidence,
        )
    if verdict == "watch" and risk in {"high", "critical"}:
        return SubagentRouteDecision(
            role=role_id,
            action="allow_with_warning",
            reason=f"{role_id} is under watch for a {risk}-risk task",
            risk_level=risk,
            verdict=verdict,
            score=score,
            confidence=confidence,
            evidence_item_ids=evidence,
        )
    return SubagentRouteDecision(
        role=role_id,
        action="allow",
        reason=f"{role_id} fitness verdict is {verdict}",
        risk_level=risk,
        verdict=verdict,
        score=score,
        confidence=confidence,
        evidence_item_ids=evidence,
    )


def _risk_level(value: str | None) -> RiskLevel:
    raw = _clean(value, limit=40).lower()
    if raw in {"critical", "p0", "danger"}:
        return "critical"
    if raw in {"high", "p1"}:
        return "high"
    if raw in {"medium", "moderate", "p2"}:
        return "medium"
    return "low"


def _operator_policy(
    role: str,
    *,
    review_queue_path: str | Path | None,
    subagent_policy_path: str | Path | None,
) -> dict[str, Any] | None:
    if subagent_policy_path is None and review_queue_path is not None:
        return None
    try:
        from runtime.safety.evolution.subagent_policy import SubagentPolicyStore

        return SubagentPolicyStore(subagent_policy_path).get(role)
    except Exception:
        return None


def _clean(value: Any, *, limit: int) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text[:limit].rstrip()


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = ["SubagentRouteDecision", "decide_subagent_route"]
