from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from runtime.safety.auth.scope import TenantScope
from runtime.safety.evolution.fitness import (
    FitnessReport,
    compute_fitness,
)

_LOG = logging.getLogger("echo.evolution.strategy")


@dataclass
class StrategyDecision:
    action: str
    confidence: str
    rationale: str
    params: dict[str, Any]


@dataclass
class StrategyConfig:
    evolve_threshold: float = 0.4
    hold_min: float = 0.6
    explore_min: float = 0.8
    regress_window: int = 3
    stable_window: int = 5


class StrategyEngine:
    def __init__(self, config: StrategyConfig | None = None) -> None:
        self.config = config or StrategyConfig()
        self._history: list[FitnessReport] = []

    def decide(self, report: FitnessReport) -> StrategyDecision:
        incoming_scope = _report_scope_key(report)
        if self._history and {_report_scope_key(existing) for existing in self._history} != {
            incoming_scope
        }:
            # Strategy trend is meaningful only within one exact
            # agent+tenant+owner namespace.  Reset rather than mixing a prior
            # tenant into the next tenant's evolution decision.
            self._history = []
        self._history.append(report)
        if len(self._history) > 20:
            self._history = self._history[-20:]

        if report.verdict == "critical":
            return StrategyDecision(
                action="revert",
                confidence="high",
                rationale=f"fitness critical ({report.combined:.2f}) · immediate rollback",
                params={"steps_back": 1},
            )

        if report.combined < self.config.evolve_threshold:
            return StrategyDecision(
                action="evolve",
                confidence="high",
                rationale=f"fitness low ({report.combined:.2f} < {self.config.evolve_threshold}) · evolve needed",
                params={"max_rounds": 1, "dry_run": True},
            )

        if self._is_regressing():
            return StrategyDecision(
                action="evolve",
                confidence="medium",
                rationale="fitness trending down · proactive evolve",
                params={"max_rounds": 1, "dry_run": True},
            )

        if report.combined >= self.config.explore_min and self._is_stable():
            return StrategyDecision(
                action="explore",
                confidence="medium",
                rationale=f"fitness high and stable ({report.combined:.2f}) · safe to explore",
                params={"max_rounds": 2, "dry_run": True},
            )

        return StrategyDecision(
            action="hold",
            confidence="high",
            rationale=f"fitness acceptable ({report.combined:.2f}) · no action needed",
            params={},
        )

    def _is_regressing(self) -> bool:
        if len(self._history) < self.config.regress_window + 1:
            return False
        recent = self._history[-(self.config.regress_window + 1) :]
        older = recent[:-1]
        newer = recent[1:]
        older_avg = sum(r.combined for r in older) / len(older)
        newer_avg = sum(r.combined for r in newer) / len(newer)
        return newer_avg < older_avg - 0.05

    def _is_stable(self) -> bool:
        if len(self._history) < self.config.stable_window:
            return False
        recent = self._history[-self.config.stable_window :]
        scores = [r.combined for r in recent]
        avg = sum(scores) / len(scores)
        variance = sum((s - avg) ** 2 for s in scores) / len(scores)
        return variance < 0.01


def _report_scope_key(report: FitnessReport) -> tuple[str, str, str, str]:
    return (
        report.agent_id,
        report.scope_mode,
        report.tenant_id,
        report.owner_actor_id,
    )


def run_strategy_cycle(
    agent_id: str,
    *,
    scope: TenantScope | None = None,
) -> dict[str, Any]:
    """Decision-only evolution cycle, for external orchestrators.

    Computes fitness for ``agent_id`` and returns the strategy decision as a
    structured dict WITHOUT applying it. This is the public API for external
    schedulers / cron that want to own the action; the in-process daemon that
    actually *acts* on such decisions is ``EvolutionAutoTrigger``
    (``auto_trigger.py``). In-repo callers should prefer the daemon; this
    function exists so a standalone scheduler can drive the same decision
    logic without hosting the daemon thread.
    """
    report = compute_fitness(agent_id, scope=scope)
    engine = StrategyEngine()
    decision = engine.decide(report)
    return {
        "agent_id": agent_id,
        "scope_mode": report.scope_mode,
        "fitness": {
            "combined": report.combined,
            "verdict": report.verdict,
            "l1_score": report.l1.score,
            "l1_trend": report.l1.trend,
        },
        "decision": {
            "action": decision.action,
            "confidence": decision.confidence,
            "rationale": decision.rationale,
            "params": decision.params,
        },
    }


__all__ = [
    "StrategyConfig",
    "StrategyDecision",
    "StrategyEngine",
    "run_strategy_cycle",
]
