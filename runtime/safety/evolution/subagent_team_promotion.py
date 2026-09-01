"""Promote trusted subagent roles into team-topology proposals.

Subagent fitness is role-level evidence. This module turns strong, reviewed
subagent roles into organization-level ``swap_agent`` proposals without
mutating the topology registry. The existing forge and operator approval path
remain the only place that can apply those proposals.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from runtime.safety.evolution.subagent_fitness import compute_subagent_fitness
from runtime.safety.evolution.subagent_policy import evaluate_agent_policy
from runtime.safety.organization.evolver import Proposal
from runtime.safety.organization.forge import load_registry
from runtime.safety.organization.promotion_lift import compute_topology_promotion_lift
from runtime.safety.organization.topology import Role, TeamTopology

_SCHEMA = "echo.subagent_team_promotion.v1"


def build_subagent_team_promotion_proposals(
    *,
    registry: dict[str, TeamTopology] | None = None,
    review_queue_path: str | Path | None = None,
    subagent_policy_path: str | Path | None = None,
    min_score: float = 0.8,
    min_promoted: int = 1,
    min_confidence: float = 0.6,
    limit: int = 25,
) -> dict[str, Any]:
    """Return topology proposals derived from strong subagent evidence."""
    active_registry = registry if registry is not None else load_registry()
    fitness = compute_subagent_fitness(review_queue_path=review_queue_path)
    roles = fitness.get("roles") if isinstance(fitness, dict) else []
    strong_roles = [
        report
        for report in roles or []
        if _is_promotable_role(
            report,
            min_score=min_score,
            min_promoted=min_promoted,
            min_confidence=min_confidence,
        )
    ]
    proposals: list[Proposal] = []
    skipped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for report in sorted(
        strong_roles,
        key=lambda row: (
            -float(row.get("score") or 0.0),
            -int(row.get("promoted_count") or 0),
            str(row.get("role") or ""),
        ),
    ):
        role = _canonical_role(report.get("role"))
        if role is None:
            skipped.append(
                {
                    "role": str(report.get("role") or ""),
                    "reason": "role is not a canonical topology role",
                }
            )
            continue
        agent_id = str(report.get("role") or "").strip()
        policy = evaluate_agent_policy(
            {str(role): agent_id},
            path=subagent_policy_path,
        )
        if policy.get("blocked"):
            skipped.append(
                {
                    "role": agent_id,
                    "reason": "operator policy retired this subagent",
                    "subagent_policy": policy,
                }
            )
            continue
        for topology in active_registry.values():
            spec = topology.agents.get(role)
            if spec is None or spec.agent_id == agent_id:
                continue
            key = (topology.fingerprint, str(role), agent_id)
            if key in seen:
                continue
            seen.add(key)
            proposals.append(
                _proposal_from_role_report(
                    topology=topology,
                    role=role,
                    old_agent=spec.agent_id,
                    new_agent=agent_id,
                    report=report,
                    subagent_policy=policy,
                )
            )
            if len(proposals) >= max(1, limit):
                break
        if len(proposals) >= max(1, limit):
            break

    return {
        "schema": _SCHEMA,
        "ts": time.time(),
        "source": "subagent_fitness",
        "strong_role_count": len(strong_roles),
        "topology_count": len(active_registry),
        "proposal_count": len(proposals),
        "proposals": [proposal.to_dict() for proposal in proposals],
        "skipped": skipped[:25],
        "thresholds": {
            "min_score": min_score,
            "min_promoted": min_promoted,
            "min_confidence": min_confidence,
        },
    }


def merged_topology_proposals(
    persisted: list[dict[str, Any]],
    *,
    registry: dict[str, TeamTopology] | None = None,
    review_queue_path: str | Path | None = None,
    subagent_policy_path: str | Path | None = None,
    promotion_lift_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge persisted topology proposals with live subagent promotions."""
    active_registry = registry if registry is not None else load_registry()
    live = build_subagent_team_promotion_proposals(
        registry=active_registry,
        review_queue_path=review_queue_path,
        subagent_policy_path=subagent_policy_path,
    )
    lift_report = (
        promotion_lift_report
        if promotion_lift_report is not None
        else compute_topology_promotion_lift(registry=active_registry)
    )
    lift_index = _historical_lift_index(lift_report)
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for raw in [*persisted, *live["proposals"]]:
        if not isinstance(raw, dict):
            continue
        key = _proposal_key(raw)
        if key in seen:
            continue
        seen.add(key)
        rows.append(_ranked_proposal(raw, lift_index=lift_index))
    rows.sort(
        key=lambda row: (
            float(row.get("rank_score") or row.get("confidence") or 0.0),
            float(row.get("confidence") or 0.0),
            str(row.get("rationale") or ""),
        ),
        reverse=True,
    )
    return {
        "schema": "echo.topology_proposals.merged.v1",
        "count": len(rows),
        "persisted_count": len(persisted),
        "subagent_promotion_count": live["proposal_count"],
        "proposals": rows,
        "subagent_promotion": live,
    }


def _is_promotable_role(
    report: dict[str, Any],
    *,
    min_score: float,
    min_promoted: int,
    min_confidence: float,
) -> bool:
    return (
        str(report.get("verdict") or "") == "strong"
        and float(report.get("score") or 0.0) >= min_score
        and int(report.get("promoted_count") or 0) >= min_promoted
        and float(report.get("confidence") or 0.0) >= min_confidence
    )


def _canonical_role(value: Any) -> Role | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return Role(text)
    except ValueError:
        return None


def _proposal_from_role_report(
    *,
    topology: TeamTopology,
    role: Role,
    old_agent: str,
    new_agent: str,
    report: dict[str, Any],
    subagent_policy: dict[str, Any],
) -> Proposal:
    score = float(report.get("score") or 0.0)
    confidence = float(report.get("confidence") or 0.0)
    promoted = int(report.get("promoted_count") or 0)
    evidence_ids = [
        str(value) for value in (report.get("evidence_item_ids") or []) if str(value or "").strip()
    ][:8]
    return Proposal(
        kind="swap_agent",
        base_topology=topology.fingerprint,
        bucket=topology.task_bucket,
        detail={
            "role": str(role),
            "old_agent": old_agent,
            "new_agent": new_agent,
            "source": "subagent_fitness",
            "subagent_score": round(score, 3),
            "subagent_confidence": round(confidence, 3),
            "promoted_count": promoted,
            "evidence_item_ids": evidence_ids,
            "subagent_policy": subagent_policy,
        },
        confidence=round(min(0.95, 0.55 + (score - 0.8) + confidence * 0.2), 3),
        rationale=(
            f"subagent {new_agent} is strong for {role} "
            f"(score {score:.2f}, {promoted} promoted review item(s)); "
            f"swap into {topology.name}"
        ),
    )


def _proposal_key(raw: dict[str, Any]) -> tuple[str, str, str, str]:
    detail = raw.get("detail") if isinstance(raw.get("detail"), dict) else {}
    return (
        str(raw.get("kind") or ""),
        str(raw.get("base_topology") or ""),
        str(detail.get("role") or ""),
        str(detail.get("new_agent") or ""),
    )


def _ranked_proposal(
    raw: dict[str, Any],
    *,
    lift_index: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    proposal = dict(raw)
    detail = dict(proposal.get("detail") or {})
    key = (
        str(detail.get("role") or ""),
        str(detail.get("new_agent") or ""),
    )
    confidence = float(proposal.get("confidence") or 0.0)
    historical = lift_index.get(key)
    adjustment = float(historical.get("rank_adjustment") or 0.0) if historical else 0.0
    rank_score = max(0.0, min(1.0, confidence + adjustment))
    if historical:
        detail["historical_lift"] = historical
    proposal["detail"] = detail
    proposal["rank_score"] = round(rank_score, 3)
    return proposal


def _historical_lift_index(
    report: dict[str, Any] | None,
) -> dict[tuple[str, str], dict[str, Any]]:
    rows = report.get("reports") if isinstance(report, dict) else []
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        detail = (
            row.get("promotion_detail") if isinstance(row.get("promotion_detail"), dict) else {}
        )
        role = str(detail.get("role") or "").strip()
        new_agent = str(detail.get("new_agent") or "").strip()
        if not role or not new_agent:
            continue
        grouped.setdefault((role, new_agent), []).append(row)

    out: dict[tuple[str, str], dict[str, Any]] = {}
    for key, items in grouped.items():
        success_deltas = [
            float((item.get("lift") or {}).get("success_rate_delta"))
            for item in items
            if isinstance((item.get("lift") or {}).get("success_rate_delta"), int | float)
        ]
        quality_deltas = [
            float((item.get("lift") or {}).get("quality_score_delta"))
            for item in items
            if isinstance((item.get("lift") or {}).get("quality_score_delta"), int | float)
        ]
        improved = sum(1 for item in items if item.get("verdict") == "improved")
        regressed = sum(1 for item in items if item.get("verdict") == "regressed")
        avg_success = (
            round(sum(success_deltas) / len(success_deltas), 3) if success_deltas else None
        )
        avg_quality = (
            round(sum(quality_deltas) / len(quality_deltas), 3) if quality_deltas else None
        )
        adjustment = 0.0
        if improved:
            adjustment += min(0.12, 0.04 * improved)
        if regressed:
            adjustment -= min(0.18, 0.06 * regressed)
        if isinstance(avg_success, float):
            adjustment += max(-0.08, min(0.08, avg_success * 0.2))
        if isinstance(avg_quality, float):
            adjustment += max(-0.06, min(0.06, avg_quality * 0.2))
        out[key] = {
            "matched_promotions": len(items),
            "improved_count": improved,
            "regressed_count": regressed,
            "avg_success_rate_delta": avg_success,
            "avg_quality_score_delta": avg_quality,
            "rank_adjustment": round(adjustment, 3),
        }
    return out


__all__ = [
    "build_subagent_team_promotion_proposals",
    "merged_topology_proposals",
]
