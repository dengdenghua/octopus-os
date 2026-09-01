from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from runtime.memory.learning.turn_scoring import (
    analyze_soul_impact,
    read_recent_scores,
)
from runtime.platform.io import read_json_with_backup
from runtime.platform.process.paths import app_paths
from runtime.safety.auth.scope import TenantScope, tenant_scoped_path
from runtime.safety.evolution.governance_audit import promotion_audit_signals

_LOG = logging.getLogger("echo.evolution.fitness")


def _publish_fitness_event(report: FitnessReport) -> None:
    from runtime.platform.process.eventbus import FitnessComputed, publish_event

    publish_event(
        FitnessComputed(
            event_type="fitness.computed",
            agent_id=report.agent_id,
            combined_score=report.combined,
            verdict=report.verdict,
            trend=report.l1.trend,
            tenant_id=report.tenant_id,
            owner_actor_id=report.owner_actor_id,
            scope_mode=report.scope_mode,
        ),
        logger=_LOG,
    )


@dataclass
class FitnessConfig:
    window: int = 20
    l1_weight: float = 0.4
    l2_weight: float = 0.6
    l2_model: str | None = None
    l2_max_tokens: int = 600
    promotion_audit_path: str | None = None
    promotion_audit_window: int = 20
    governance_max_penalty: float = 0.25
    governance_blocked_override_weight: float = 0.18
    governance_gate_failed_weight: float = 0.08
    governance_override_weight: float = 0.05
    governance_failed_apply_weight: float = 0.08


@dataclass
class L1Fitness:
    score: float
    trend: str
    success_rate: float
    avg_rounds: float
    soul_impact: dict[str, Any]


@dataclass
class L2Fitness:
    score: float
    dominant_failure: str | None
    action: str
    confidence: str
    raw: dict[str, Any] | None


@dataclass
class GovernanceFitness:
    score: float
    penalty: float
    audit_total: int
    recent_total: int
    override_count: int
    gate_failed_count: int
    gate_blocked_override_count: int
    failed_apply_count: int
    reasons: list[str]


@dataclass
class FitnessReport:
    agent_id: str
    ts: str
    l1: L1Fitness
    l2: L2Fitness | None
    combined: float
    verdict: str
    governance: GovernanceFitness | None = None
    tenant_id: str = ""
    owner_actor_id: str = ""
    scope_mode: str = "legacy"


_L2_SYSTEM = """You are a fitness evaluator for an AI agent. Given recent turn
scores and a heuristic pre-analysis, produce a concise fitness judgment.
Output ONLY a JSON envelope.

Schema:
```json
{
  "fitness_score": 0.0-1.0,
  "dominant_failure": "<tag>" | null,
  "action": "evolve" | "revert" | "hold" | "explore",
  "confidence": "low" | "medium" | "high",
  "rationale": "<1-2 sentences>"
}
```"""


def _promotion_audit_paths(base: Path, scope: TenantScope | None) -> list[Path]:
    """Return exact governance partitions for one fitness calculation."""

    if scope is None:
        return [base]
    if not scope.allow_cross_tenant:
        return [tenant_scoped_path(base, scope)]
    out = [base]
    tenants_dir = base.parent / "tenants"
    try:
        partitions = sorted(tenants_dir.iterdir())
    except OSError:
        partitions = []
    for partition in partitions:
        # ``tenant_scoped_path`` uses the first 32 lowercase hex characters
        # of SHA-256.  Ignore anything else and never follow file symlinks.
        if len(partition.name) != 32 or any(ch not in "0123456789abcdef" for ch in partition.name):
            continue
        candidate = partition / base.name
        if candidate.is_file() and not candidate.is_symlink():
            out.append(candidate)
    return out


def compute_l1(
    agent_id: str,
    *,
    window: int = 20,
    scope: TenantScope | None = None,
) -> L1Fitness:
    scores = read_recent_scores(agent_id, limit=window, scope=scope)
    if not scores:
        return L1Fitness(
            score=0.5,
            trend="stable",
            success_rate=0.0,
            avg_rounds=0.0,
            soul_impact={},
        )

    success_rate = sum(1 for s in scores if s.score >= 1.0) / len(scores)
    avg_rounds = sum(s.rounds for s in scores) / max(1, len(scores))

    if len(scores) >= 4:
        first_half = scores[: len(scores) // 2]
        second_half = scores[len(scores) // 2 :]
        avg_first = sum(s.score for s in first_half) / len(first_half)
        avg_second = sum(s.score for s in second_half) / len(second_half)
        delta = avg_second - avg_first
        if delta > 0.1:
            trend = "improving"
        elif delta < -0.1:
            trend = "regressing"
        else:
            trend = "stable"
    else:
        trend = "stable"

    raw_score = sum(s.score for s in scores) / len(scores)
    soul_impact = analyze_soul_impact(agent_id, window=window, scope=scope)

    return L1Fitness(
        score=round(raw_score, 3),
        trend=trend,
        success_rate=round(success_rate, 3),
        avg_rounds=round(avg_rounds, 1),
        soul_impact=soul_impact,
    )


def compute_l2(
    agent_id: str,
    l1: L1Fitness,
    *,
    model: str | None = None,
    window: int = 20,
    scope: TenantScope | None = None,
) -> L2Fitness | None:
    from runtime.platform.process.service_provider import get_provider

    router = get_provider().get("evolve_router")
    if router is None:
        _LOG.debug("evolve_router not wired · skip L2 fitness")
        return None

    from runtime.platform.llm_infra.llm_caller import LLMCaller

    caller = LLMCaller("evolve_router", "evolve_default_model")
    scores = read_recent_scores(agent_id, limit=window, scope=scope)

    rows = "\n".join(
        f"  - {s.ts} score={s.score} reason={s.reason} rounds={s.rounds}" for s in scores[:15]
    )
    user_msg = (
        f"AGENT: {agent_id}\n"
        f"L1 heuristic: score={l1.score} trend={l1.trend} "
        f"success_rate={l1.success_rate}\n"
        f"Soul impact: {json.dumps(l1.soul_impact, ensure_ascii=False)[:500]}\n\n"
        f"Recent turns:\n{rows}\n\n"
        "Produce your JSON fitness envelope."
    )

    parsed, meta = caller.call_json(
        system=_L2_SYSTEM,
        user=user_msg,
        model=model,
        max_tokens=400,
        temperature=0.2,
    )

    if parsed is None:
        return L2Fitness(
            score=l1.score,
            dominant_failure=None,
            action="hold",
            confidence="low",
            raw=meta,
        )

    return L2Fitness(
        score=float(parsed.get("fitness_score", l1.score)),
        dominant_failure=parsed.get("dominant_failure"),
        action=parsed.get("action", "hold"),
        confidence=parsed.get("confidence", "low"),
        raw=parsed,
    )


def compute_governance_fitness(
    *,
    agent_id: str | None = None,
    audit_path: str | Path | None = None,
    window: int = 20,
    max_penalty: float = 0.25,
    blocked_override_weight: float = 0.18,
    gate_failed_weight: float = 0.08,
    override_weight: float = 0.05,
    failed_apply_weight: float = 0.08,
    scope: TenantScope | None = None,
) -> GovernanceFitness:
    base = Path(audit_path) if audit_path is not None else app_paths().promotion_audit_path
    paths = _promotion_audit_paths(base, scope)
    rows: list[dict[str, Any]] = []
    for path in paths:
        raw = read_json_with_backup(path, default=None)
        records = raw.get("records") if isinstance(raw, dict) else []
        if isinstance(records, list):
            rows.extend(row for row in records if isinstance(row, dict))
    if agent_id:
        wanted_agent = str(agent_id)
        rows = [row for row in rows if str(row.get("agent_id") or "") == wanted_agent]
    recent = rows[-max(0, window) :] if window > 0 else rows
    if not recent:
        return GovernanceFitness(
            score=1.0,
            penalty=0.0,
            audit_total=len(rows),
            recent_total=0,
            override_count=0,
            gate_failed_count=0,
            gate_blocked_override_count=0,
            failed_apply_count=0,
            reasons=[],
        )

    override_count = 0
    gate_failed_count = 0
    gate_blocked_override_count = 0
    failed_apply_count = 0
    for row in recent:
        signals = promotion_audit_signals(row)
        if signals["failed_apply"]:
            failed_apply_count += 1
        if signals["gate_failed"]:
            gate_failed_count += 1
        if signals["override"]:
            override_count += 1
            if signals["gate_blocked_override"]:
                gate_blocked_override_count += 1

    total = len(recent)
    override_rate = override_count / total
    gate_failed_rate = gate_failed_count / total
    blocked_override_rate = gate_blocked_override_count / total
    failed_apply_rate = failed_apply_count / total
    penalty = min(
        max(0.0, max_penalty),
        blocked_override_rate * blocked_override_weight
        + gate_failed_rate * gate_failed_weight
        + override_rate * override_weight
        + failed_apply_rate * failed_apply_weight,
    )
    reasons: list[str] = []
    if gate_blocked_override_count:
        reasons.append(f"{gate_blocked_override_count} blocked replay override(s)")
    if gate_failed_count:
        reasons.append(f"{gate_failed_count} replay gate failure(s)")
    if failed_apply_count:
        reasons.append(f"{failed_apply_count} failed promotion apply attempt(s)")
    if override_count and not gate_blocked_override_count:
        reasons.append(f"{override_count} replay override(s)")

    return GovernanceFitness(
        score=round(max(0.0, 1.0 - penalty), 3),
        penalty=round(penalty, 3),
        audit_total=len(rows),
        recent_total=total,
        override_count=override_count,
        gate_failed_count=gate_failed_count,
        gate_blocked_override_count=gate_blocked_override_count,
        failed_apply_count=failed_apply_count,
        reasons=reasons,
    )


def compute_fitness(
    agent_id: str,
    config: FitnessConfig | None = None,
    *,
    publish_event: bool = True,
    scope: TenantScope | None = None,
) -> FitnessReport:
    config = config or FitnessConfig()
    l1 = compute_l1(agent_id, window=config.window, scope=scope)
    l2 = compute_l2(
        agent_id,
        l1,
        model=config.l2_model,
        window=config.window,
        scope=scope,
    )

    if l2 is not None:
        combined = round(
            l1.score * config.l1_weight + l2.score * config.l2_weight,
            3,
        )
    else:
        combined = l1.score

    governance = compute_governance_fitness(
        agent_id=agent_id,
        audit_path=config.promotion_audit_path,
        window=config.promotion_audit_window,
        max_penalty=config.governance_max_penalty,
        blocked_override_weight=config.governance_blocked_override_weight,
        gate_failed_weight=config.governance_gate_failed_weight,
        override_weight=config.governance_override_weight,
        failed_apply_weight=config.governance_failed_apply_weight,
        scope=scope,
    )
    combined = round(max(0.0, combined - governance.penalty), 3)

    if combined >= 0.8:
        verdict = "healthy"
    elif combined >= 0.5:
        verdict = "degraded"
    elif combined >= 0.3:
        verdict = "unhealthy"
    else:
        verdict = "critical"

    report = FitnessReport(
        agent_id=agent_id,
        ts=datetime.now().isoformat(timespec="seconds"),
        l1=l1,
        l2=l2,
        combined=combined,
        verdict=verdict,
        governance=governance,
        tenant_id=scope.tenant_id if scope is not None and not scope.allow_cross_tenant else "",
        owner_actor_id=scope.actor_id if scope is not None and not scope.allow_cross_tenant else "",
        scope_mode=(
            "cross_tenant"
            if scope is not None and scope.allow_cross_tenant
            else "tenant"
            if scope is not None
            else "legacy"
        ),
    )

    if publish_event:
        _publish_fitness_event(report)

    return report


__all__ = [
    "FitnessConfig",
    "FitnessReport",
    "GovernanceFitness",
    "L1Fitness",
    "L2Fitness",
    "compute_fitness",
    "compute_governance_fitness",
    "compute_l1",
    "compute_l2",
]
