from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from runtime.adapters.instrumentation import trace_stage
from runtime.memory.journal import Journal, TrajectoryEvent
from runtime.platform.models import SkillId, Trajectory
from runtime.safety.auth.scope import TenantScope
from runtime.safety.recovery.tenant_scope import read_learning_events

# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


Severity = Literal["low", "mid", "high"]


class LearnedRule(BaseModel):
    model_config = ConfigDict(frozen=True)

    rule_id: str = Field(..., min_length=1)
    sucker_id: SkillId
    error_signature: str  # Implementation note.
    pattern: str  # Implementation note.
    mitigation: str  # Implementation note.
    hit_count: int = Field(..., ge=1)
    severity: Severity = "mid"
    source_trajectory_ids: list[str] = Field(default_factory=list)


class RuleExtractionReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    trajectories_scanned: int
    failure_count: int
    clusters_formed: int
    rules_produced: list[LearnedRule] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


@dataclass
class ExtractorConfig:
    min_hits: int = 3  # Implementation note.
    severity_thresholds: dict[Severity, int] = field(
        default_factory=lambda: {"low": 3, "mid": 5, "high": 10}
    )
    max_rules_per_run: int = 30  # Implementation note.
    include_partial_as_failure: bool = False


# ═══════════════════════════════════════════════════════════
# RuleExtractor
# ═══════════════════════════════════════════════════════════


class RuleExtractor:
    def __init__(
        self,
        journal: Journal,
        config: ExtractorConfig | None = None,
        *,
        scope: TenantScope | None = None,
    ) -> None:
        self.journal = journal
        self.config = config or ExtractorConfig()
        self.scope = scope

    def extract(self) -> RuleExtractionReport:
        with trace_stage("regeneration.rule_extractor.extract"):
            all_trajs = self._all_trajectories()
            failures = [t for t in all_trajs if self._is_failed(t)]

            clusters: dict[tuple[str, str], list[tuple[Trajectory, int]]] = defaultdict(list)
            for traj in failures:
                for i, step in enumerate(traj.steps):
                    if step.success:
                        continue
                    sig = _error_signature(step)
                    if sig is None:
                        continue
                    key = (str(step.action.sucker_id), sig)
                    clusters[key].append((traj, i))

            rules: list[LearnedRule] = []
            for (sucker_id, error_sig), hits in clusters.items():
                if len(hits) < self.config.min_hits:
                    continue
                rule = self._derive_rule(sucker_id, error_sig, hits)
                rules.append(rule)

            rules.sort(key=lambda r: (_sev_rank(r.severity), r.hit_count), reverse=True)
            rules = rules[: self.config.max_rules_per_run]

            return RuleExtractionReport(
                trajectories_scanned=len(all_trajs),
                failure_count=len(failures),
                clusters_formed=len(clusters),
                rules_produced=rules,
            )

    def _all_trajectories(self) -> list[Trajectory]:
        events = read_learning_events(
            self.journal,
            "trajectory",
            scope=self.scope,
        )
        trajs = [e.trajectory for e in events if isinstance(e, TrajectoryEvent)]

        # A swarm task may emit both per-arm trajectories and one
        # aggregated ``strategy_id="swarm"`` trajectory for the same
        # task_id. When the aggregate exists, it is the whole-task
        # sample we want; keeping both copies inflates failure hit
        # counts and can overproduce learned rules.
        by_task: dict[str, list[Trajectory]] = defaultdict(list)
        order: list[str] = []
        for traj in trajs:
            key = str(traj.task_id)
            if key not in by_task:
                order.append(key)
            by_task[key].append(traj)

        selected: list[Trajectory] = []
        for key in order:
            bucket = by_task[key]
            swarm_bucket = [t for t in bucket if t.strategy_id == "swarm"]
            if swarm_bucket:
                selected.append(
                    max(
                        swarm_bucket,
                        key=lambda t: (
                            t.step_count,
                            int(not t.outcome.success),
                            t.completed_at,
                        ),
                    )
                )
                continue
            selected.extend(bucket)
        return selected

    def _is_failed(self, traj: Trajectory) -> bool:
        if not traj.outcome.success:
            return True
        if self.config.include_partial_as_failure:
            return any(not s.success for s in traj.steps)
        return False

    def _derive_rule(
        self,
        sucker_id: str,
        error_sig: str,
        hits: list[tuple[Trajectory, int]],
    ) -> LearnedRule:
        hit_count = len(hits)
        severity = self._severity_for(hit_count)

        first_traj, first_idx = hits[0]
        first_step = first_traj.steps[first_idx]
        args_keys = list(first_step.action.args.keys())

        pattern = (
            f"When calling '{sucker_id}'"
            + (f" with args={args_keys}" if args_keys else "")
            + f", failure signature '{error_sig}' seen {hit_count} times"
        )
        mitigation = _mitigation_for(error_sig, sucker_id, args_keys)

        rule_id = f"RULE_{sucker_id}_{_slug(error_sig)}_{hit_count}"
        return LearnedRule(
            rule_id=rule_id,
            sucker_id=SkillId(sucker_id),
            error_signature=error_sig,
            pattern=pattern,
            mitigation=mitigation,
            hit_count=hit_count,
            severity=severity,
            source_trajectory_ids=[str(t.trajectory_id) for t, _ in hits],
        )

    def _severity_for(self, hit_count: int) -> Severity:
        thresholds = self.config.severity_thresholds
        if hit_count >= thresholds.get("high", 10):
            return "high"
        if hit_count >= thresholds.get("mid", 5):
            return "mid"
        return "low"


# ═══════════════════════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════════════════════


def _error_signature(step) -> str | None:
    result = step.result
    if result.status == "success":
        return None
    sig = result.status
    if result.error_type:
        sig = f"{sig}:{result.error_type}"
    return sig


def _mitigation_for(error_sig: str, sucker_id: str, args_keys: list[str]) -> str:
    if "timeout" in error_sig.lower():
        return (
            f"Skill '{sucker_id}' timed out; consider setting tighter deadlines upstream "
            f"or splitting into smaller tasks."
        )
    if "sandbox_violation" in error_sig.lower():
        return (
            f"Skill '{sucker_id}' hit sandbox violation; verify args "
            f"({', '.join(args_keys) or 'N/A'}) don't escape mantle boundaries."
        )
    if "immune_reject" in error_sig.lower():
        return (
            f"Skill '{sucker_id}' was rejected by immunity; check trusted_source signature "
            f"or caller identity."
        )
    if "circuit_broken" in error_sig.lower():
        return (
            f"Skill '{sucker_id}' circuit-broken (budget / loop); allocate more budget "
            f"or break task into sub-steps."
        )
    return (
        f"Skill '{sucker_id}' failed with '{error_sig}'; "
        f"consider alternative skill or validate inputs."
    )


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in text)[:24]


_SEVERITY_RANK = {"low": 0, "mid": 1, "high": 2}


def _sev_rank(sev: Severity) -> int:
    return _SEVERITY_RANK.get(sev, 0)


# ═══════════════════════════════════════════════════════════
# Prompt injection helper
# ═══════════════════════════════════════════════════════════


def format_rules_for_prompt(
    rules: list[LearnedRule],
    *,
    header: str = "LEARNED MITIGATIONS (from past failures):",
    max_total_chars: int = 2000,
) -> str:
    if not rules:
        return ""

    sorted_rules = sorted(rules, key=lambda r: (_sev_rank(r.severity), r.hit_count), reverse=True)

    lines = [header]
    used = len(header)
    for r in sorted_rules:
        line = f"  - [{r.severity.upper()}·{r.hit_count}x] {r.pattern} → {r.mitigation}"
        if used + len(line) > max_total_chars:
            lines.append(f"  ... ({len(sorted_rules) - (len(lines) - 1)} more rules truncated)")
            break
        lines.append(line)
        used += len(line)
    return "\n".join(lines)
