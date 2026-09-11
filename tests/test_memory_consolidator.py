"""Implementation note."""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest

from runtime.memory.journal import InMemoryJournal, journal_context
from runtime.platform.models import (
    ArmId,
    CostEntry,
    TaskId,
    Trajectory,
    TrajectoryOutcome,
    now_utc,
)
from runtime.safety.recovery import (
    ConsolidatedMemory,
    ConsolidatorConfig,
    MemoryConsolidator,
    format_memories_for_prompt,
)


def _mk_traj(
    arm: str = "code_arm",
    strategy: str = "default",
    success: bool = True,
    steps: int = 3,
    cost_usd: float = 0.01,
    tokens: int = 300,
    age_hours: float = 0.0,
) -> Trajectory:
    started = now_utc() - timedelta(hours=age_hours + 0.1)
    completed = now_utc() - timedelta(hours=age_hours)
    return Trajectory(
        task_id=TaskId(uuid4()),
        arm_id=ArmId(arm),
        strategy_id=strategy,
        steps=[],  # Implementation note.
        outcome=TrajectoryOutcome(
            success=success,
            cost=CostEntry(tokens_in=tokens // 2, tokens_out=tokens // 2, usd=cost_usd),
        ),
        started_at=started,
        completed_at=completed,
    )


@pytest.fixture
def journal_mixed():
    j = InMemoryJournal()
    # Implementation note.
    for _ in range(5):
        j.write_trajectory(_mk_traj(arm="code_arm", strategy="default", success=True))
    # Implementation note.
    for _ in range(3):
        j.write_trajectory(_mk_traj(arm="code_arm", strategy="default", success=False))
    # Implementation note.
    for _ in range(2):
        j.write_trajectory(
            _mk_traj(arm="text_arm", strategy="llm_plan", success=True, cost_usd=0.05)
        )
    # Implementation note.
    j.write_trajectory(_mk_traj(arm="x", strategy="solo"))
    return j


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestConsolidate:
    def test_clusters_by_arm_strategy(self, journal_mixed):
        report = MemoryConsolidator(journal_mixed).consolidate()
        assert report.trajectories_scanned == 11
        assert report.clusters_formed == 3
        # Implementation note.
        assert len(report.memories_produced) == 2
        keys = {m.pattern_key for m in report.memories_produced}
        assert "code_arm/default" in keys
        assert "text_arm/llm_plan" in keys
        assert "x/solo" not in keys

    def test_success_rate_correct(self, journal_mixed):
        report = MemoryConsolidator(journal_mixed).consolidate()
        main = next(m for m in report.memories_produced if m.pattern_key == "code_arm/default")
        assert main.trajectories_count == 8
        assert main.success_count == 5
        assert abs(main.success_rate - 5 / 8) < 1e-6

    def test_cost_aggregation(self, journal_mixed):
        report = MemoryConsolidator(journal_mixed).consolidate()
        text_arm = next(m for m in report.memories_produced if m.pattern_key == "text_arm/llm_plan")
        # Implementation note.
        assert abs(text_arm.total_cost_usd - 0.10) < 1e-6

    def test_min_samples_filter(self):
        j = InMemoryJournal()
        j.write_trajectory(_mk_traj(arm="a", strategy="s"))  # Implementation note.
        report = MemoryConsolidator(j).consolidate()
        assert report.memories_produced == []

    def test_custom_min_samples(self):
        j = InMemoryJournal()
        j.write_trajectory(_mk_traj(arm="a", strategy="s"))
        report = MemoryConsolidator(
            j, config=ConsolidatorConfig(min_samples_per_cluster=1)
        ).consolidate()
        assert len(report.memories_produced) == 1

    def test_swarm_aggregate_clusters_under_stable_swarm_arm_id(self):
        j = InMemoryJournal()
        for arm in ("arm_a", "arm_b"):
            for _ in range(2):
                j.write_trajectory(_mk_traj(arm=arm, strategy="swarm"))
        report = MemoryConsolidator(j).consolidate()
        keys = {m.pattern_key for m in report.memories_produced}
        assert keys == {"arm_a/swarm", "arm_b/swarm"}

        j.write_trajectory(_mk_traj(arm="swarm", strategy="swarm"))
        j.write_trajectory(_mk_traj(arm="swarm", strategy="swarm"))
        report = MemoryConsolidator(j).consolidate()
        keys = {m.pattern_key for m in report.memories_produced}
        assert "swarm/swarm" in keys

    def test_swarm_aggregate_deduplicates_same_task_for_memory_stats(self):
        j = InMemoryJournal()
        task_id = TaskId(uuid4())

        with journal_context(agent_id="coder", conversation_id="c1"):
            j.write_trajectory(
                _mk_traj(arm="arm_a", strategy="default", cost_usd=0.02).model_copy(
                    update={"task_id": task_id}
                )
            )
            j.write_trajectory(
                _mk_traj(arm="swarm", strategy="swarm", cost_usd=0.02).model_copy(
                    update={"task_id": task_id}
                )
            )

        second_task_id = TaskId(uuid4())
        with journal_context(agent_id="coder", conversation_id="c1"):
            j.write_trajectory(
                _mk_traj(arm="arm_b", strategy="default", cost_usd=0.03).model_copy(
                    update={"task_id": second_task_id}
                )
            )
            j.write_trajectory(
                _mk_traj(arm="swarm", strategy="swarm", cost_usd=0.03).model_copy(
                    update={"task_id": second_task_id}
                )
            )

        report = MemoryConsolidator(j).consolidate()
        swarm = next(m for m in report.memories_produced if m.pattern_key == "swarm/swarm")
        assert swarm.trajectories_count == 2
        assert abs(swarm.total_cost_usd - 0.05) < 1e-6

    def test_swarm_aggregate_dedupe_prefers_latest_same_task(self):
        j = InMemoryJournal()
        task_id = TaskId(uuid4())
        other_task_id = TaskId(uuid4())

        j.write_trajectory(
            _mk_traj(arm="swarm", strategy="swarm", success=False, cost_usd=0.01).model_copy(
                update={"task_id": task_id}
            )
        )
        j.write_trajectory(
            _mk_traj(arm="swarm", strategy="swarm", success=True, cost_usd=0.07).model_copy(
                update={"task_id": task_id}
            )
        )
        j.write_trajectory(
            _mk_traj(arm="swarm", strategy="swarm", success=True, cost_usd=0.03).model_copy(
                update={"task_id": other_task_id}
            )
        )

        report = MemoryConsolidator(j).consolidate()
        swarm = next(m for m in report.memories_produced if m.pattern_key == "swarm/swarm")
        assert swarm.trajectories_count == 2
        assert swarm.success_count == 2
        assert abs(swarm.total_cost_usd - 0.10) < 1e-6


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestTier:
    def test_hot_when_recent(self):
        j = InMemoryJournal()
        for _ in range(2):
            j.write_trajectory(_mk_traj(age_hours=1))
        m = MemoryConsolidator(j).consolidate().memories_produced[0]
        assert m.tier == "hot"

    def test_warm_when_weeks_old(self):
        j = InMemoryJournal()
        for _ in range(2):
            j.write_trajectory(_mk_traj(age_hours=72))
        m = MemoryConsolidator(j).consolidate().memories_produced[0]
        assert m.tier == "warm"

    def test_cold_when_ancient(self):
        j = InMemoryJournal()
        for _ in range(2):
            j.write_trajectory(_mk_traj(age_hours=24 * 60))  # 60 days
        m = MemoryConsolidator(j).consolidate().memories_produced[0]
        assert m.tier == "cold"


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestStableMemoryId:
    def test_same_cluster_same_id_across_runs(self):
        j = InMemoryJournal()
        for _ in range(3):
            j.write_trajectory(_mk_traj())
        a = MemoryConsolidator(j).consolidate().memories_produced[0].memory_id
        b = MemoryConsolidator(j).consolidate().memories_produced[0].memory_id
        assert a == b


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestMaxMemories:
    def test_respects_limit(self):
        j = InMemoryJournal()
        for arm_i in range(10):
            for _ in range(2):
                j.write_trajectory(_mk_traj(arm=f"arm_{arm_i}", strategy=f"s_{arm_i}"))
        report = MemoryConsolidator(j, config=ConsolidatorConfig(max_memories=3)).consolidate()
        assert len(report.memories_produced) == 3


# ═══════════════════════════════════════════════════════════
# format_memories_for_prompt
# ═══════════════════════════════════════════════════════════


class TestFormatForPrompt:
    def test_empty_returns_empty(self):
        assert format_memories_for_prompt([]) == ""

    def test_contains_header(self, journal_mixed):
        memories = MemoryConsolidator(journal_mixed).consolidate().memories_produced
        text = format_memories_for_prompt(memories)
        assert "CONSOLIDATED MEMORIES" in text

    def test_sorts_by_count_times_success(self, journal_mixed):
        memories = MemoryConsolidator(journal_mixed).consolidate().memories_produced
        text = format_memories_for_prompt(memories)
        # Implementation note.
        pos_main = text.find("code_arm/default")
        pos_text = text.find("text_arm/llm_plan")
        assert 0 < pos_main < pos_text

    def test_only_hot_filter(self):
        j = InMemoryJournal()
        # Implementation note.
        for _ in range(2):
            j.write_trajectory(_mk_traj(arm="hot_arm", strategy="s1", age_hours=1))
        for _ in range(2):
            j.write_trajectory(_mk_traj(arm="cold_arm", strategy="s2", age_hours=24 * 60))
        memories = MemoryConsolidator(j).consolidate().memories_produced
        text_all = format_memories_for_prompt(memories, only_hot=False)
        text_hot = format_memories_for_prompt(memories, only_hot=True)
        assert "hot_arm" in text_hot
        assert "cold_arm" in text_all
        assert "cold_arm" not in text_hot

    def test_truncation(self):
        # Implementation note.
        from uuid import uuid4

        memories = [
            ConsolidatedMemory(
                memory_id=uuid4(),
                pattern_key=f"arm_{i}/strategy_{i}",
                arm_id=ArmId(f"arm_{i}"),
                strategy_id=f"strategy_{i}",
                trajectories_count=10,
                success_count=8,
                success_rate=0.8,
                avg_step_count=3.0,
                total_cost_usd=0.5,
                total_tokens=1000,
                first_seen=now_utc(),
                last_seen=now_utc(),
                tier="warm",
            )
            for i in range(30)
        ]
        text = format_memories_for_prompt(memories, max_total_chars=500)
        assert "truncated" in text.lower()


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestReflectionIntegration:
    def test_memory_complements_rule(self, journal_mixed):
        """Implementation note."""
        from runtime.safety.recovery import RuleExtractor

        memories = MemoryConsolidator(journal_mixed).consolidate().memories_produced
        # Run RuleExtractor for its side effect on the journal; its
        # output is not asserted here (covered by its own tests).
        RuleExtractor(journal_mixed).extract()
        # Implementation note.
        assert len(memories) >= 1
        # Implementation note.
        memories2 = MemoryConsolidator(journal_mixed).consolidate().memories_produced
        assert len(memories) == len(memories2)
