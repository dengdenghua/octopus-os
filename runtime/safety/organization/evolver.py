"""TopologyEvolver — proposes new team topologies from the perf log.

The evolver is a *static analyser* in MVP — no LLM calls. It reads the
performance JSONL, groups runs by ``task_bucket``, computes per-topology
success metrics, and emits structured mutation proposals.

Three mutation kinds covered:

  * ``swap_agent`` — within a winning topology, swap the agent at a
    specific role for one that wins more often *as that role* in the
    same bucket. (Cross-pollination between high-performing recipes.)

  * ``switch_protocol`` — when a sequential topology is consistently
    losing on quality but winning on speed, propose flipping it to
    ``evaluator_optimizer`` so the team self-corrects.

  * ``adjust_quality_threshold`` — for evaluator_optimizer topologies
    that consistently retry to the max iteration and still pass, the
    threshold may be too high; for those that pass at iteration 1
    every time, it may be too low.

Proposals go to ``data/topology_proposals.json`` for the forge / UI
to consume. Nothing is applied here.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from runtime.platform.io import atomic_write_json
from runtime.platform.process.paths import app_paths

from . import performance_log
from .topology import (
    AgentSpec,
    CoordinationProtocol,
    Role,
    TeamTopology,
)

_logger = logging.getLogger("echo.organization.evolver")


@dataclass
class TopologyStats:
    fingerprint: str
    name: str
    bucket: str
    runs: int = 0
    successes: int = 0
    avg_score: float = 0.0
    avg_iterations: float = 0.0
    avg_duration_ms: float = 0.0

    @property
    def success_rate(self) -> float:
        return self.successes / self.runs if self.runs else 0.0


@dataclass
class Proposal:
    kind: str
    base_topology: str  # fingerprint
    bucket: str
    detail: dict[str, Any]
    confidence: float  # 0..1
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "base_topology": self.base_topology,
            "bucket": self.bucket,
            "detail": self.detail,
            "confidence": self.confidence,
            "rationale": self.rationale,
        }


@dataclass
class EvolverReport:
    proposals: list[Proposal] = field(default_factory=list)
    buckets_analysed: int = 0
    topologies_seen: int = 0
    runs_seen: int = 0


# ── Stats aggregation ────────────────────────────────────────


def _aggregate_stats(rows: list[dict[str, Any]]) -> dict[str, TopologyStats]:
    by_fp: dict[str, TopologyStats] = {}
    for r in rows:
        fp = str(r.get("fingerprint") or "")
        if not fp:
            continue
        stats = by_fp.get(fp)
        if stats is None:
            stats = TopologyStats(
                fingerprint=fp,
                name=str(r.get("topology") or fp),
                bucket=str(r.get("task_bucket") or "default"),
            )
            by_fp[fp] = stats
        stats.runs += 1
        if r.get("success"):
            stats.successes += 1
        score = r.get("quality_score")
        if isinstance(score, (int, float)):
            # Running mean.
            stats.avg_score += (float(score) - stats.avg_score) / stats.runs
        stats.avg_iterations += (
            float(r.get("iterations") or 1) - stats.avg_iterations
        ) / stats.runs
        stats.avg_duration_ms += (
            float(r.get("total_duration_ms") or 0.0) - stats.avg_duration_ms
        ) / stats.runs
    return by_fp


def _bucket_winner(stats_in_bucket: list[TopologyStats]) -> TopologyStats | None:
    """Pick the topology with the best success_rate (tie-break: avg_score)."""
    qualified = [s for s in stats_in_bucket if s.runs >= 3]
    if not qualified:
        return None
    qualified.sort(
        key=lambda s: (s.success_rate, s.avg_score, -s.avg_duration_ms),
        reverse=True,
    )
    return qualified[0]


# ── Proposal builders ────────────────────────────────────────


def _propose_threshold_adjustments(
    bucket: str,
    stats_in_bucket: list[TopologyStats],
    registry: dict[str, TeamTopology],
) -> list[Proposal]:
    out: list[Proposal] = []
    for s in stats_in_bucket:
        topology = registry.get(s.fingerprint)
        if topology is None:
            continue
        if topology.protocol != CoordinationProtocol.EVALUATOR_OPTIMIZER:
            continue
        if s.runs < 5:
            continue
        # Always hitting max iterations → threshold is too high.
        if s.avg_iterations >= topology.max_iterations - 0.5 and s.success_rate >= 0.6:
            new_threshold = max(0.1, topology.quality_threshold - 0.1)
            if new_threshold != topology.quality_threshold:
                out.append(
                    Proposal(
                        kind="adjust_quality_threshold",
                        base_topology=s.fingerprint,
                        bucket=bucket,
                        detail={
                            "old_threshold": topology.quality_threshold,
                            "new_threshold": new_threshold,
                        },
                        confidence=min(0.9, 0.4 + 0.1 * s.runs / 10),
                        rationale=(
                            f"avg_iterations {s.avg_iterations:.1f}/"
                            f"{topology.max_iterations} — threshold too strict"
                        ),
                    )
                )
            continue
        # Always passing on iteration 1 → threshold could be tighter.
        if s.avg_iterations <= 1.2 and s.success_rate >= 0.9:
            new_threshold = min(0.95, topology.quality_threshold + 0.1)
            if new_threshold != topology.quality_threshold:
                out.append(
                    Proposal(
                        kind="adjust_quality_threshold",
                        base_topology=s.fingerprint,
                        bucket=bucket,
                        detail={
                            "old_threshold": topology.quality_threshold,
                            "new_threshold": new_threshold,
                        },
                        confidence=min(0.8, 0.3 + 0.1 * s.runs / 10),
                        rationale=(
                            f"avg_iterations {s.avg_iterations:.1f} — generator "
                            f"never challenged; tighten threshold"
                        ),
                    )
                )
    return out


def _propose_protocol_switch(
    bucket: str,
    stats_in_bucket: list[TopologyStats],
    registry: dict[str, TeamTopology],
) -> list[Proposal]:
    out: list[Proposal] = []
    has_evaluator_winner = any(
        registry.get(s.fingerprint)
        and registry[s.fingerprint].protocol == CoordinationProtocol.EVALUATOR_OPTIMIZER
        and s.success_rate >= 0.7
        for s in stats_in_bucket
    )
    if not has_evaluator_winner:
        return out
    for s in stats_in_bucket:
        topology = registry.get(s.fingerprint)
        if topology is None:
            continue
        if topology.protocol != CoordinationProtocol.SEQUENTIAL:
            continue
        if s.runs < 5:
            continue
        # Sequential losing on quality (avg_score < 0.5) but a sibling
        # evaluator_optimizer is winning in the same bucket.
        if s.avg_score and s.avg_score < 0.5 and s.success_rate < 0.5:
            out.append(
                Proposal(
                    kind="switch_protocol",
                    base_topology=s.fingerprint,
                    bucket=bucket,
                    detail={
                        "from": "sequential",
                        "to": "evaluator_optimizer",
                    },
                    confidence=min(0.85, 0.4 + 0.1 * s.runs / 10),
                    rationale=(
                        f"sequential success_rate {s.success_rate:.0%} / "
                        f"avg_score {s.avg_score:.2f} — sibling "
                        f"evaluator_optimizer wins this bucket"
                    ),
                )
            )
    return out


def _propose_agent_swaps(
    bucket: str,
    stats_in_bucket: list[TopologyStats],
    registry: dict[str, TeamTopology],
) -> list[Proposal]:
    """Find the agent that wins most often *as a given role* in this
    bucket; suggest swapping losing topologies' agent for that one.
    """
    # Per-role winner: agent_id → (wins, runs)
    role_winners: dict[Role, dict[str, dict[str, int]]] = defaultdict(
        lambda: defaultdict(lambda: {"wins": 0, "runs": 0}),
    )
    for s in stats_in_bucket:
        topology = registry.get(s.fingerprint)
        if topology is None:
            continue
        for role, spec in topology.agents.items():
            stats = role_winners[role][spec.agent_id]
            stats["runs"] += s.runs
            stats["wins"] += s.successes

    out: list[Proposal] = []
    for s in stats_in_bucket:
        topology = registry.get(s.fingerprint)
        if topology is None or s.runs < 3:
            continue
        if s.success_rate >= 0.7:
            continue
        for role, spec in topology.agents.items():
            candidates = role_winners[role]
            best_agent: str | None = None
            best_rate = 0.0
            for cand_id, m in candidates.items():
                if cand_id == spec.agent_id:
                    continue
                if m["runs"] < 5:
                    continue
                rate = m["wins"] / m["runs"]
                if rate > best_rate + 0.15:  # need ≥15pp lift
                    best_rate = rate
                    best_agent = cand_id
            if best_agent:
                out.append(
                    Proposal(
                        kind="swap_agent",
                        base_topology=s.fingerprint,
                        bucket=bucket,
                        detail={
                            "role": str(role),
                            "old_agent": spec.agent_id,
                            "new_agent": best_agent,
                            "expected_lift": round(best_rate - s.success_rate, 2),
                        },
                        confidence=min(0.8, 0.3 + 0.1 * s.runs / 10),
                        rationale=(
                            f"role {role}: agent {best_agent} wins "
                            f"{best_rate:.0%} vs current {s.success_rate:.0%}"
                        ),
                    )
                )
                break  # one swap per topology per tick
    return out


# ── Driver ───────────────────────────────────────────────────


class TopologyEvolver:
    """Run one evolution tick over the performance log."""

    def __init__(
        self,
        *,
        log_path: Path | str | None = None,
        proposals_path: Path | str | None = None,
        registry: dict[str, TeamTopology] | None = None,
    ) -> None:
        self._log_path = Path(log_path) if log_path else None
        self._registry: dict[str, TeamTopology] = registry or {}
        if proposals_path:
            self._proposals_path = Path(proposals_path)
        else:
            try:
                self._proposals_path = app_paths().data_dir / "topology_proposals.json"
            except (AttributeError, OSError, TypeError):
                self._proposals_path = Path("data") / "topology_proposals.json"

    def analyse(
        self,
        *,
        min_runs_per_bucket: int = 3,
    ) -> EvolverReport:
        rows = performance_log.read_runs(path=self._log_path)
        stats = _aggregate_stats(rows)

        by_bucket: dict[str, list[TopologyStats]] = defaultdict(list)
        for s in stats.values():
            by_bucket[s.bucket].append(s)

        report = EvolverReport(
            runs_seen=len(rows),
            topologies_seen=len(stats),
        )
        for bucket, in_bucket in by_bucket.items():
            if sum(s.runs for s in in_bucket) < min_runs_per_bucket:
                continue
            report.buckets_analysed += 1
            winner = _bucket_winner(in_bucket)
            if winner is None:
                continue
            report.proposals.extend(
                _propose_threshold_adjustments(bucket, in_bucket, self._registry),
            )
            report.proposals.extend(
                _propose_protocol_switch(bucket, in_bucket, self._registry),
            )
            report.proposals.extend(
                _propose_agent_swaps(bucket, in_bucket, self._registry),
            )
        return report

    def tick(self) -> EvolverReport:
        """Run analyse + persist proposals to disk. Returns the report.

        Gated by ``MutationKind.EVOLVE_TOPOLOGY``: a proposal write is
        a mutation in its own right (it's a candidate that downstream
        forge tooling may auto-promote).
        """
        try:
            from runtime.safety.gene_locks import (
                LockViolation,
                MutationKind,
                gate_mutation,
            )

            gate_mutation(
                kind=MutationKind.EVOLVE_TOPOLOGY,
                target="topology_proposals",
                autonomous=True,
            )
        except LockViolation as lv:
            _logger.info("evolve_topology gated: %s", lv)
            return EvolverReport()
        except (ImportError, AttributeError, OSError):  # noqa: BLE001 — gene_locks unavailable; proceed
            pass

        report = self.analyse()
        payload = {
            "ts": time.time(),
            "buckets_analysed": report.buckets_analysed,
            "topologies_seen": report.topologies_seen,
            "runs_seen": report.runs_seen,
            "proposals": [p.to_dict() for p in report.proposals],
        }
        try:
            atomic_write_json(self._proposals_path, payload)
        except OSError as exc:
            _logger.warning("proposal write failed: %s", exc)
        return report


__all__ = [
    "EvolverReport",
    "Proposal",
    "TopologyEvolver",
    "TopologyStats",
]


# Suppress unused-import linter on AgentSpec / TeamTopology — they're
# part of the module's public type surface even though only used in
# annotations within the registry argument.
_ = AgentSpec
