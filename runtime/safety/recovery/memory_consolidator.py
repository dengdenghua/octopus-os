from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from runtime.adapters.instrumentation import trace_stage
from runtime.memory.journal import Journal, TrajectoryEvent
from runtime.platform.models import ArmId, Trajectory, now_utc
from runtime.safety.auth.scope import TenantScope
from runtime.safety.recovery.tenant_scope import read_learning_events

Tier = Literal["cold", "warm", "hot"]

MemoryScope = Literal["agent", "group", "global"]


class ConsolidatedMemory(BaseModel):
    model_config = ConfigDict(frozen=True)

    memory_id: UUID = Field(..., description="stable id for the consolidated memory")
    pattern_key: str
    arm_id: ArmId
    strategy_id: str
    trajectories_count: int
    success_count: int
    success_rate: float
    avg_step_count: float
    total_cost_usd: float
    total_tokens: int
    first_seen: datetime
    last_seen: datetime
    tier: Tier = "warm"
    source_trajectory_ids: list[UUID] = Field(default_factory=list)
    scope: MemoryScope = "global"
    scope_key: str = ""


class ConsolidationReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    trajectories_scanned: int
    clusters_formed: int
    memories_produced: list[ConsolidatedMemory] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


@dataclass
class ConsolidatorConfig:
    min_samples_per_cluster: int = 2  # Implementation note.
    hot_tier_recency_hours: int = 24
    cold_tier_age_days: int = 30
    max_memories: int = 50


# ═══════════════════════════════════════════════════════════
# MemoryConsolidator
# ═══════════════════════════════════════════════════════════


class MemoryConsolidator:
    def __init__(
        self,
        journal: Journal,
        config: ConsolidatorConfig | None = None,
        *,
        scope: TenantScope | None = None,
    ) -> None:
        self.journal = journal
        self.config = config or ConsolidatorConfig()
        self.scope = scope

    def consolidate(self) -> ConsolidationReport:
        tagged = self._collect_trajectories()
        return self._consolidate_subset(
            [t for t, _ in tagged],
            scope="global",
            scope_key="",
        )

    def consolidate_scoped(
        self,
        *,
        agents: list[str] | None = None,
        groups: dict[str, list[str]] | None = None,
        include_global: bool = True,
    ) -> ConsolidationReport:
        with trace_stage("regeneration.memory_consolidator.consolidate_scoped"):
            tagged = self._collect_trajectories()
            all_memories: list[ConsolidatedMemory] = []
            scanned = 0
            clusters_total = 0

            if include_global:
                sub = self._consolidate_subset(
                    [t for t, _ in tagged],
                    scope="global",
                    scope_key="",
                )
                all_memories.extend(sub.memories_produced)
                scanned = sub.trajectories_scanned
                clusters_total += sub.clusters_formed

            for agent_id in agents or []:
                subset = [t for t, aid in tagged if aid == agent_id]
                sub = self._consolidate_subset(
                    subset,
                    scope="agent",
                    scope_key=agent_id,
                )
                all_memories.extend(sub.memories_produced)
                clusters_total += sub.clusters_formed

            for group_name, members in (groups or {}).items():
                member_set = set(members)
                subset = [t for t, aid in tagged if aid in member_set]
                sub = self._consolidate_subset(
                    subset,
                    scope="group",
                    scope_key=group_name,
                )
                all_memories.extend(sub.memories_produced)
                clusters_total += sub.clusters_formed

            return ConsolidationReport(
                trajectories_scanned=scanned or len(tagged),
                clusters_formed=clusters_total,
                memories_produced=all_memories,
            )

    def _collect_trajectories(self) -> list[tuple[Trajectory, str | None]]:
        events = read_learning_events(
            self.journal,
            "trajectory",
            scope=self.scope,
        )
        # Carry event timestamp into the bucket so we can pick the
        # NEWEST swarm aggregate when a task was resumed/retried and
        # wrote multiple aggregates under the same ``task_id``. The
        # earlier ``next(...)``-first behavior would let an old
        # failed aggregate permanently shadow the newer final result.
        grouped: dict[
            object,
            list[tuple[int, Trajectory, str | None]],
        ] = defaultdict(list)
        for idx, event in enumerate(events):
            if not isinstance(event, TrajectoryEvent):
                continue
            grouped[event.trajectory.task_id].append(
                (idx, event.trajectory, event.agent_id),
            )

        tagged: list[tuple[Trajectory, str | None]] = []
        for bucket in grouped.values():
            swarm_entries = [
                (idx, traj, agent_id)
                for idx, traj, agent_id in bucket
                if traj.strategy_id == "swarm"
            ]
            if swarm_entries:
                # Max by append index · last write wins. This is more
                # reliable than event.ts for fast retries that can share
                # the same timestamp tick.
                _, traj, agent_id = max(swarm_entries, key=lambda e: e[0])
                tagged.append((traj, agent_id))
            else:
                tagged.extend((traj, agent_id) for _, traj, agent_id in bucket)
        return tagged

    def _consolidate_subset(
        self,
        trajs: list[Trajectory],
        *,
        scope: MemoryScope,
        scope_key: str,
    ) -> ConsolidationReport:
        clusters: dict[tuple[str, str], list[Trajectory]] = defaultdict(list)
        for t in trajs:
            clusters[(str(t.arm_id), t.strategy_id)].append(t)

        memories: list[ConsolidatedMemory] = []
        now = now_utc()
        for (arm_id, strategy), cluster in clusters.items():
            if len(cluster) < self.config.min_samples_per_cluster:
                continue
            memories.append(
                self._summarize_cluster(
                    arm_id,
                    strategy,
                    cluster,
                    now,
                    scope=scope,
                    scope_key=scope_key,
                )
            )

        memories.sort(key=lambda m: -m.trajectories_count)
        memories = memories[: self.config.max_memories]

        return ConsolidationReport(
            trajectories_scanned=len(trajs),
            clusters_formed=len(clusters),
            memories_produced=memories,
        )

    def _summarize_cluster(
        self,
        arm_id: str,
        strategy: str,
        cluster: list[Trajectory],
        now: datetime,
        *,
        scope: MemoryScope = "global",
        scope_key: str = "",
    ) -> ConsolidatedMemory:
        # A degraded completion is useful negative/partial evidence, but it is
        # not a clean positive sample.  Counting it as success lets cancelled
        # or bridge-error native turns inflate memories that later bias the
        # planner, despite SkillForge/WorkflowRewriter rejecting the same run.
        success = sum(1 for t in cluster if t.outcome.success and not t.outcome.degraded)
        total_cost = sum(t.outcome.cost.usd for t in cluster)
        total_tokens = sum(t.outcome.cost.tokens for t in cluster)
        avg_steps = sum(t.step_count for t in cluster) / len(cluster)
        starts = [t.started_at for t in cluster]
        ends = [t.completed_at for t in cluster]
        first_seen = min(starts)
        last_seen = max(ends)

        age_hours = (now - last_seen).total_seconds() / 3600
        if age_hours <= self.config.hot_tier_recency_hours:
            tier: Tier = "hot"
        elif age_hours / 24 <= self.config.cold_tier_age_days:
            tier = "warm"
        else:
            tier = "cold"

        import hashlib

        key_src = (
            f"{arm_id}|{strategy}|{first_seen.isoformat()}|"
            f"{last_seen.isoformat()}|{scope}|{scope_key}"
        )
        mid_bytes = hashlib.blake2b(key_src.encode("utf-8"), digest_size=16).digest()
        memory_id = UUID(bytes=mid_bytes)

        return ConsolidatedMemory(
            memory_id=memory_id,
            pattern_key=f"{arm_id}/{strategy}",
            arm_id=ArmId(arm_id),
            strategy_id=strategy,
            trajectories_count=len(cluster),
            success_count=success,
            success_rate=success / len(cluster),
            avg_step_count=avg_steps,
            total_cost_usd=total_cost,
            total_tokens=total_tokens,
            first_seen=first_seen,
            last_seen=last_seen,
            tier=tier,
            source_trajectory_ids=[t.trajectory_id for t in cluster],
            scope=scope,
            scope_key=scope_key,
        )


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


def filter_memories_for_agent(
    memories: list[ConsolidatedMemory],
    *,
    agent_id: str,
    groups: list[str] | None = None,
) -> list[ConsolidatedMemory]:
    group_set = set(groups or [])
    out: list[ConsolidatedMemory] = []
    for m in memories:
        if (
            m.scope == "global"
            or m.scope == "agent"
            and m.scope_key == agent_id
            or m.scope == "group"
            and m.scope_key in group_set
        ):
            out.append(m)
    return out


def format_memories_for_prompt(
    memories: list[ConsolidatedMemory],
    *,
    header: str = "CONSOLIDATED MEMORIES (past pattern stats):",
    max_total_chars: int = 1500,
    only_hot: bool = False,
) -> str:
    if not memories:
        return ""

    filtered = [m for m in memories if m.tier == "hot"] if only_hot else list(memories)
    if not filtered:
        return ""

    filtered.sort(key=lambda m: -(m.trajectories_count * m.success_rate))

    lines = [header]
    used = len(header)
    for m in filtered:
        line = (
            f"  - [{m.tier.upper()}] {m.pattern_key} · "
            f"{m.trajectories_count} runs · "
            f"{m.success_rate * 100:.0f}% success · "
            f"avg {m.avg_step_count:.1f} steps · "
            f"${m.total_cost_usd:.4f} total"
        )
        if used + len(line) > max_total_chars:
            lines.append(f"  ... ({len(filtered) - (len(lines) - 1)} more memories truncated)")
            break
        lines.append(line)
        used += len(line)
    return "\n".join(lines)
