from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from runtime.adapters.instrumentation import trace_stage
from runtime.memory.journal import Journal, TrajectoryEvent
from runtime.platform.models import SkillId, Trajectory, new_id
from runtime.safety.auth.scope import TenantScope
from runtime.safety.recovery.tenant_scope import read_learning_events

ProposalKind = Literal[
    "remove_degraded_step",
    "lower_rule_priority",
    "propose_new_rule",
    "merge_redundant_adjacent",
]

Severity = Literal["low", "mid", "high"]


class RewriteProposal(BaseModel):
    model_config = ConfigDict(frozen=True)

    proposal_id: UUID = Field(default_factory=new_id)
    kind: ProposalKind
    target_rule_name: str | None = None  # Implementation note.
    target_step_index: int | None = None  # Implementation note.
    suggested_skill_sequence: list[SkillId] = Field(default_factory=list)
    rationale: str
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    severity: Severity = "mid"
    hit_count: int = 0
    supporting_trajectory_ids: list[UUID] = Field(default_factory=list)


class RewriteReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    trajectories_scanned: int
    proposals: list[RewriteProposal] = Field(default_factory=list)

    @property
    def proposals_by_kind(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for p in self.proposals:
            counts[p.kind] = counts.get(p.kind, 0) + 1
        return counts


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


@dataclass
class RewriterConfig:
    min_rule_invocations: int = 3  # Implementation note.
    degraded_step_threshold: float = 0.5  # Implementation note.
    low_rule_success_threshold: float = 0.4  # Implementation note.
    new_sequence_min_hits: int = 3  # Implementation note.
    max_proposals: int = 20


# ═══════════════════════════════════════════════════════════
# WorkflowRewriter
# ═══════════════════════════════════════════════════════════


class WorkflowRewriter:
    def __init__(
        self,
        journal: Journal,
        config: RewriterConfig | None = None,
        *,
        scope: TenantScope | None = None,
    ) -> None:
        self.journal = journal
        self.config = config or RewriterConfig()
        self.scope = scope

    def analyze(self, rules: list[Any] | None = None) -> RewriteReport:
        with trace_stage("regeneration.workflow_rewriter.analyze"):
            trajs = self._collect_trajectories()

            proposals: list[RewriteProposal] = []
            if rules:
                proposals.extend(self._analyze_rules(rules, trajs))
            proposals.extend(self._propose_new_sequences(trajs))
            proposals.extend(self._detect_redundant_adjacent(trajs))

            sev_rank = {"high": 2, "mid": 1, "low": 0}
            proposals.sort(
                key=lambda p: (sev_rank.get(p.severity, 0), p.hit_count),
                reverse=True,
            )
            proposals = proposals[: self.config.max_proposals]

            return RewriteReport(
                trajectories_scanned=len(trajs),
                proposals=proposals,
            )

    def _analyze_rules(self, rules: list[Any], trajs: list[Trajectory]) -> list[RewriteProposal]:
        proposals: list[RewriteProposal] = []

        by_strategy: dict[str, list[Trajectory]] = defaultdict(list)
        for t in trajs:
            by_strategy[t.strategy_id].append(t)

        for rule in rules:
            rule_name = getattr(rule, "name", None)
            if not rule_name:
                continue
            matched = by_strategy.get(rule_name, [])
            if len(matched) < self.config.min_rule_invocations:
                continue

            success = sum(1 for t in matched if t.outcome.success and not t.outcome.degraded)
            rule_success_rate = success / len(matched)

            if rule_success_rate < self.config.low_rule_success_threshold:
                proposals.append(
                    RewriteProposal(
                        kind="lower_rule_priority",
                        target_rule_name=rule_name,
                        rationale=(
                            f"rule {rule_name!r} has low success rate "
                            f"{rule_success_rate:.1%} across {len(matched)} runs; "
                            f"consider lowering priority or rewriting."
                        ),
                        confidence=1.0 - rule_success_rate,
                        severity="high" if rule_success_rate < 0.2 else "mid",
                        hit_count=len(matched),
                        supporting_trajectory_ids=[t.trajectory_id for t in matched],
                    )
                )

            skill_sequence: list[str] = list(getattr(rule, "skill_sequence", []) or [])
            if not skill_sequence:
                continue

            step_success_count: dict[int, int] = defaultdict(int)
            step_total_count: dict[int, int] = defaultdict(int)
            for t in matched:
                for i, step in enumerate(t.steps):
                    step_total_count[i] += 1
                    if step.success:
                        step_success_count[i] += 1

            for idx, total in step_total_count.items():
                if total < self.config.min_rule_invocations:
                    continue
                success_count = step_success_count.get(idx, 0)
                rate = success_count / total
                if rate < self.config.degraded_step_threshold:
                    sucker_name = skill_sequence[idx] if idx < len(skill_sequence) else "?"
                    proposals.append(
                        RewriteProposal(
                            kind="remove_degraded_step",
                            target_rule_name=rule_name,
                            target_step_index=idx,
                            rationale=(
                                f"step {idx} ({sucker_name!r}) in rule {rule_name!r} "
                                f"succeeds only {rate:.1%} of the time ({total} runs); "
                                f"consider removing or replacing."
                            ),
                            confidence=1.0 - rate,
                            severity="high" if rate < 0.2 else "mid",
                            hit_count=total,
                            supporting_trajectory_ids=[t.trajectory_id for t in matched],
                        )
                    )
        return proposals

    def _propose_new_sequences(self, trajs: list[Trajectory]) -> list[RewriteProposal]:
        success_trajs = [
            t for t in trajs if t.outcome.success and not t.outcome.degraded and t.step_count >= 2
        ]
        if not success_trajs:
            return []

        seq_counter: Counter[tuple[str, ...]] = Counter()
        seq_source_ids: dict[tuple[str, ...], list[UUID]] = defaultdict(list)
        for t in success_trajs:
            seq = tuple(str(s.action.sucker_id) for s in t.steps)
            seq_counter[seq] += 1
            seq_source_ids[seq].append(t.trajectory_id)

        proposals: list[RewriteProposal] = []
        for seq, count in seq_counter.most_common():
            if count < self.config.new_sequence_min_hits:
                break  # Implementation note.
            proposals.append(
                RewriteProposal(
                    kind="propose_new_rule",
                    target_rule_name=None,
                    suggested_skill_sequence=[SkillId(s) for s in seq],
                    rationale=(
                        f"sequence {' → '.join(seq)} succeeded {count} times; "
                        f"consider codifying as a new StaticPlanner Rule."
                    ),
                    confidence=min(0.9, 0.5 + count * 0.05),
                    severity="mid" if count >= 5 else "low",
                    hit_count=count,
                    supporting_trajectory_ids=seq_source_ids[seq],
                )
            )
        return proposals

    def _detect_redundant_adjacent(self, trajs: list[Trajectory]) -> list[RewriteProposal]:
        redundant_counter: Counter[tuple[str, str]] = Counter()
        redundant_source: dict[tuple[str, str], list[UUID]] = defaultdict(list)

        for t in trajs:
            for i in range(len(t.steps) - 1):
                a = t.steps[i]
                b = t.steps[i + 1]
                if a.action.sucker_id == b.action.sucker_id and a.action.args == b.action.args:
                    key = (str(a.action.sucker_id), str(sorted(a.action.args.items())))
                    redundant_counter[key] += 1
                    redundant_source[key].append(t.trajectory_id)

        proposals: list[RewriteProposal] = []
        for (sucker, args_key), count in redundant_counter.most_common():
            if count < self.config.min_rule_invocations:
                break
            proposals.append(
                RewriteProposal(
                    kind="merge_redundant_adjacent",
                    target_rule_name=None,
                    rationale=(
                        f"skill {sucker!r} invoked twice adjacently with identical args "
                        f"in {count} trajectories; consider deduplicating."
                    ),
                    confidence=0.7,
                    severity="low",
                    hit_count=count,
                    supporting_trajectory_ids=redundant_source[(sucker, args_key)],
                )
            )
        return proposals

    def _collect_trajectories(self) -> list[Trajectory]:
        events = read_learning_events(
            self.journal,
            "trajectory",
            scope=self.scope,
        )
        trajs = [e.trajectory for e in events if isinstance(e, TrajectoryEvent)]

        # Keep the whole-task swarm aggregate when it exists for a
        # task; otherwise a single swarm execution can contribute both
        # the arm-internal trajectory and the aggregate copy, which
        # inflates sequence hit counts and rewrite proposals.
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
                            int(t.outcome.success),
                            t.completed_at,
                        ),
                    )
                )
                continue
            selected.extend(bucket)
        return selected


# ═══════════════════════════════════════════════════════════
# Prompt formatting helper
# ═══════════════════════════════════════════════════════════


def format_proposals_for_review(
    proposals: list[RewriteProposal],
    *,
    header: str = "WORKFLOW REWRITE PROPOSALS (for admin review):",
    max_total_chars: int = 2500,
) -> str:
    if not proposals:
        return ""
    lines = [header]
    used = len(header)
    for p in proposals:
        tag = f"[{p.severity.upper()}·{p.kind}·{p.hit_count}x]"
        target = f" rule={p.target_rule_name!r}" if p.target_rule_name else ""
        line = f"  {tag}{target} · {p.rationale}"
        if used + len(line) > max_total_chars:
            lines.append(f"  ... ({len(proposals) - (len(lines) - 1)} more truncated)")
            break
        lines.append(line)
        used += len(line)
    return "\n".join(lines)
