"""Implementation note."""

from __future__ import annotations

from uuid import uuid4

from runtime.execution.agents import Agent
from runtime.execution.arms.base import ArmPool, Worker
from runtime.memory.journal import InMemoryJournal, journal_context
from runtime.platform.models import (
    ArmId,
    CostEntry,
    ExecutionResult,
    Step,
    TaskId,
    ToolCall,
    Trajectory,
    TrajectoryOutcome,
)
from runtime.safety.recovery import (
    ConsolidatorConfig,
    MemoryConsolidator,
    filter_memories_for_agent,
)

# ═══════════════════════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════════════════════


def _mk_traj(
    *,
    arm_id: str,
    strategy: str,
    success: bool = True,
) -> Trajectory:
    call = ToolCall(caller="t", sucker_id="read_file", args={})
    step = Step(
        step_id=0,
        node_id="n0",
        action=call,
        result=ExecutionResult(
            call_id=call.call_id,
            status="success",
            output={},
        ),
    )
    return Trajectory(
        task_id=TaskId(uuid4()),
        arm_id=ArmId(arm_id),
        strategy_id=strategy,
        steps=[step],
        outcome=TrajectoryOutcome(
            success=success,
            cost=CostEntry(tokens_in=10, tokens_out=5, usd=0.001),
        ),
    )


def _seed_journal(scenarios):
    """scenarios: list of (agent_id, arm_id, strategy)"""
    j = InMemoryJournal()
    # Implementation note.
    for aid, arm, strat in scenarios:
        for _ in range(2):
            with journal_context(agent_id=aid, conversation_id="c"):
                j.write_trajectory(_mk_traj(arm_id=arm, strategy=strat))
    return j


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestScopeFields:
    def test_default_global_on_vanilla_consolidate(self):
        """Implementation note."""
        j = _seed_journal([("coder", "a1", "s1")])
        c = MemoryConsolidator(journal=j, config=ConsolidatorConfig(min_samples_per_cluster=2))
        report = c.consolidate()
        assert len(report.memories_produced) == 1
        m = report.memories_produced[0]
        assert m.scope == "global"
        assert m.scope_key == ""


# ═══════════════════════════════════════════════════════════
# consolidate_scoped
# ═══════════════════════════════════════════════════════════


class TestScopedConsolidation:
    def test_agent_scope_only_sees_own_trajectories(self):
        j = _seed_journal(
            [
                ("coder", "a1", "s1"),
                ("shopify", "a2", "s2"),
            ]
        )
        c = MemoryConsolidator(journal=j, config=ConsolidatorConfig(min_samples_per_cluster=2))
        report = c.consolidate_scoped(
            agents=["coder"],
            include_global=False,
        )
        # Implementation note.
        assert len(report.memories_produced) == 1
        m = report.memories_produced[0]
        assert m.scope == "agent"
        assert m.scope_key == "coder"
        assert str(m.arm_id) == "a1"

    def test_group_scope_aggregates_members(self):
        j = _seed_journal(
            [
                ("vibe_selling", "a1", "s1"),
                ("ecommerce_mind", "a2", "s2"),
                ("coder", "a3", "s3"),
            ]
        )
        c = MemoryConsolidator(journal=j, config=ConsolidatorConfig(min_samples_per_cluster=2))
        report = c.consolidate_scoped(
            groups={"ecom_team": ["vibe_selling", "ecommerce_mind"]},
            include_global=False,
        )
        # Implementation note.
        assert len(report.memories_produced) == 2
        for m in report.memories_produced:
            assert m.scope == "group"
            assert m.scope_key == "ecom_team"
        # Implementation note.
        arm_ids = {str(m.arm_id) for m in report.memories_produced}
        assert "a3" not in arm_ids

    def test_three_tiers_together(self):
        j = _seed_journal(
            [
                ("coder", "a1", "s1"),
                ("vibe_selling", "a2", "s2"),
            ]
        )
        c = MemoryConsolidator(journal=j, config=ConsolidatorConfig(min_samples_per_cluster=2))
        report = c.consolidate_scoped(
            agents=["coder", "vibe_selling"],
            groups={"all": ["coder", "vibe_selling"]},
            include_global=True,
        )
        scopes = [m.scope for m in report.memories_produced]
        # Implementation note.
        assert "global" in scopes
        assert "agent" in scopes
        assert "group" in scopes
        # Implementation note.
        agent_keys = {m.scope_key for m in report.memories_produced if m.scope == "agent"}
        assert agent_keys == {"coder", "vibe_selling"}
        group_keys = {m.scope_key for m in report.memories_produced if m.scope == "group"}
        assert group_keys == {"all"}

    def test_empty_agents_and_groups(self):
        """Implementation note."""
        j = _seed_journal([("coder", "a1", "s1")])
        c = MemoryConsolidator(journal=j, config=ConsolidatorConfig(min_samples_per_cluster=2))
        report = c.consolidate_scoped(include_global=True)
        assert all(m.scope == "global" for m in report.memories_produced)

    def test_include_global_false_with_no_targets_empty(self):
        j = _seed_journal([("coder", "a1", "s1")])
        c = MemoryConsolidator(journal=j, config=ConsolidatorConfig(min_samples_per_cluster=2))
        report = c.consolidate_scoped(include_global=False)
        assert report.memories_produced == []

    def test_legacy_untagged_trajectories_only_feed_global(self):
        """Implementation note."""
        j = InMemoryJournal()
        # Implementation note.
        for _ in range(2):
            j.write_trajectory(_mk_traj(arm_id="a1", strategy="s1"))

        c = MemoryConsolidator(journal=j, config=ConsolidatorConfig(min_samples_per_cluster=2))
        report = c.consolidate_scoped(
            agents=["coder"],
            include_global=True,
        )
        scopes = [m.scope for m in report.memories_produced]
        assert "global" in scopes
        # Implementation note.
        agent_memories = [m for m in report.memories_produced if m.scope == "agent"]
        assert len(agent_memories) == 0


# ═══════════════════════════════════════════════════════════
# filter_memories_for_agent
# ═══════════════════════════════════════════════════════════


class TestFilter:
    def _build_memories(self):
        j = _seed_journal(
            [
                ("coder", "a1", "s1"),
                ("shopify", "a2", "s2"),
                ("vibe_selling", "a3", "s3"),
                ("ecommerce_mind", "a4", "s4"),
            ]
        )
        c = MemoryConsolidator(journal=j, config=ConsolidatorConfig(min_samples_per_cluster=2))
        return c.consolidate_scoped(
            agents=["coder", "shopify", "vibe_selling", "ecommerce_mind"],
            groups={"ecom_team": ["vibe_selling", "ecommerce_mind"]},
            include_global=True,
        ).memories_produced

    def test_coder_sees_global_and_own(self):
        mems = self._build_memories()
        filtered = filter_memories_for_agent(
            mems,
            agent_id="coder",
            groups=[],
        )
        # Implementation note.
        scopes = {m.scope for m in filtered}
        assert "global" in scopes
        assert "agent" in scopes
        assert "group" not in scopes
        # Implementation note.
        for m in filtered:
            if m.scope == "agent":
                assert m.scope_key == "coder"

    def test_vibe_selling_sees_its_group(self):
        mems = self._build_memories()
        filtered = filter_memories_for_agent(
            mems,
            agent_id="vibe_selling",
            groups=["ecom_team"],
        )
        scopes = {m.scope for m in filtered}
        assert "group" in scopes
        # Implementation note.
        for m in filtered:
            if m.scope == "agent":
                assert m.scope_key == "vibe_selling"

    def test_agent_not_in_any_group(self):
        mems = self._build_memories()
        filtered = filter_memories_for_agent(
            mems,
            agent_id="coder",
            groups=["nonexistent"],
        )
        # Implementation note.
        group_mems = [m for m in filtered if m.scope == "group"]
        assert group_mems == []

    def test_isolation_no_cross_agent_leak(self):
        """Implementation note."""
        mems = self._build_memories()
        filtered = filter_memories_for_agent(
            mems,
            agent_id="coder",
            groups=[],
        )
        # Implementation note.
        agent_keys = {m.scope_key for m in filtered if m.scope == "agent"}
        assert agent_keys == {"coder"}
        assert "shopify" not in agent_keys


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestAgentGroups:
    def _rt(self):
        from runtime.core.graph_runtime import GraphRuntime

        class _FE:
            journal = None

        return GraphRuntime(executor=_FE(), journal=None)

    def test_default_empty_groups(self):
        rt = self._rt()
        arm = Worker(
            arm_id=ArmId("a"),
            affinity=[],
            allowed_skills=[],
            runtime=rt,
        )
        agent = Agent(
            agent_id="x",
            display_name="X",
            description="",
            soul="",
            arms=ArmPool([arm]),
        )
        assert agent.groups == []

    def test_explicit_groups(self):
        rt = self._rt()
        arm = Worker(
            arm_id=ArmId("a"),
            affinity=[],
            allowed_skills=[],
            runtime=rt,
        )
        agent = Agent(
            agent_id="vibe",
            display_name="V",
            description="",
            soul="",
            arms=ArmPool([arm]),
            groups=["ecom_team", "marketing_team"],
        )
        assert agent.groups == ["ecom_team", "marketing_team"]


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestEndToEnd:
    def test_agent_views_filtered_memories(self):
        """Implementation note."""
        j = _seed_journal(
            [
                ("vibe", "a1", "s1"),
                ("mind", "a2", "s2"),
            ]
        )
        c = MemoryConsolidator(journal=j, config=ConsolidatorConfig(min_samples_per_cluster=2))
        report = c.consolidate_scoped(
            agents=["vibe", "mind"],
            groups={"ecom": ["vibe", "mind"]},
            include_global=True,
        )

        vibe_view = filter_memories_for_agent(
            report.memories_produced,
            agent_id="vibe",
            groups=["ecom"],
        )
        mind_view = filter_memories_for_agent(
            report.memories_produced,
            agent_id="mind",
            groups=["ecom"],
        )

        def _summarize(mems):
            return sorted((m.scope, m.scope_key, str(m.arm_id)) for m in mems)

        # Implementation note.
        # Implementation note.
        vibe_s = _summarize(vibe_view)
        assert ("agent", "vibe", "a1") in vibe_s
        assert ("agent", "mind", "a2") not in vibe_s
        # Implementation note.
        group_in_vibe = [x for x in vibe_s if x[0] == "group"]
        assert len(group_in_vibe) == 2

        # Implementation note.
        mind_s = _summarize(mind_view)
        assert ("agent", "mind", "a2") in mind_s
        assert ("agent", "vibe", "a1") not in mind_s
