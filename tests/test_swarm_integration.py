"""Implementation note."""

from __future__ import annotations

import time

import pytest
from runtime.core.graph_runtime import GraphRuntime
from runtime.execution.arms import Arm, ArmPool
from runtime.execution.suckers import Skill, SkillRegistry
from runtime.execution.swarm import SwarmResult, SwarmRuntime
from runtime.execution.tool_engine import ToolExecutor
from runtime.memory.journal import InMemoryJournal
from runtime.platform.models import (
    ArmId,
    Budget,
    BudgetLimits,
    BudgetSpec,
    SkillId,
    TaskGraph,
    TaskNode,
    new_id,
    now_utc,
)
from runtime.safety.auth import TrustEngine
from runtime.safety.chromatophores import (
    TOPIC_ARM_BUSY,
    TOPIC_ARM_IDLE,
    BoidsArbitrator,
    ResourceClaim,
    SignalBus,
)

# ═══════════════════════════════════════════════════════════
# Shared fixtures
# ═══════════════════════════════════════════════════════════


@pytest.fixture
def journal():
    return InMemoryJournal()


@pytest.fixture
def registry():
    r = SkillRegistry()

    # Implementation note.
    def _slow_read(**kw):
        time.sleep(0.05)  # 50ms
        return {"read": True, "path": kw.get("path", ".")}

    def _slow_count(**kw):
        time.sleep(0.05)
        return {"words": 42}

    def _slow_hash(**kw):
        time.sleep(0.05)
        return {"hash": "abc123"}

    r.register(
        Skill(
            name="read_file",
            trusted_source="skill://public/read_file",
            handler=_slow_read,
        ),
        verify_tests=False,
    )
    r.register(
        Skill(
            name="count_words",
            trusted_source="skill://public/count_words",
            handler=_slow_count,
        ),
        verify_tests=False,
    )
    r.register(
        Skill(
            name="hash_text",
            trusted_source="skill://public/hash_text",
            handler=_slow_hash,
        ),
        verify_tests=False,
    )
    return r


@pytest.fixture
def runtime(registry, journal):
    executor = ToolExecutor(
        registry=registry,
        immunity=TrustEngine(trusted_sources=["skill://public/*"]),
        journal=journal,
    )
    return GraphRuntime(executor=executor, journal=journal)


@pytest.fixture
def pool(runtime):
    """Implementation note."""
    code_arm = Arm(
        arm_id=ArmId("code_arm"),
        affinity=["code"],
        allowed_skills=[SkillId("read_file")],
        runtime=runtime,
    )
    search_arm = Arm(
        arm_id=ArmId("search_arm"),
        affinity=["search"],
        allowed_skills=[SkillId("count_words")],
        runtime=runtime,
    )
    generic_arm = Arm(
        arm_id=ArmId("generic_arm"),
        affinity=["text"],
        allowed_skills=[SkillId("hash_text"), SkillId("read_file"), SkillId("count_words")],
        runtime=runtime,
    )
    return ArmPool([code_arm, search_arm, generic_arm])


@pytest.fixture
def signal_bus():
    return SignalBus()


@pytest.fixture
def boids(signal_bus):
    return BoidsArbitrator(signal_bus=signal_bus)


def _mk_graph(skills: list[str]) -> TaskGraph:
    nodes = [
        TaskNode(node_id=f"n{i}", skill_ref=SkillId(s), args_template={"path": "."})
        for i, s in enumerate(skills)
    ]
    return TaskGraph(
        nodes=nodes,
        edges=[],
        budget=BudgetSpec(tokens=50_000, usd=0.50),
        task_type="mixed",
    )


# ═══════════════════════════════════════════════════════════
# Integration: Arm + SwarmRuntime + SignalBus + Journal
# ═══════════════════════════════════════════════════════════


class TestArmsAndSwarmIntegration:
    def test_three_arms_run_three_nodes_concurrently(self, pool, signal_bus, boids, journal):
        """Implementation note."""
        graph = _mk_graph(["read_file", "count_words", "hash_text"])
        budget = Budget(task_id=graph.task_id, limits=BudgetLimits(tokens=50_000, usd=0.50))
        swarm = SwarmRuntime(
            arm_pool=pool,
            signal_bus=signal_bus,
            boids=boids,
            journal=journal,
            max_workers=3,
        )

        result = swarm.run(graph=graph, budget=budget, split_strategy="per_node")

        assert isinstance(result, SwarmResult)
        assert result.all_successful, f"failed results: {result.arm_results}"
        assert len(result.arm_results) == 3
        assert result.parallelism_achieved == 3

        # Implementation note.
        # Implementation note.
        assert result.total_wall_ms < 400, (
            f"should be concurrent, but took {result.total_wall_ms}ms"
        )

    def test_signal_bus_gets_busy_idle_events(self, pool, signal_bus, journal):
        """Implementation note."""
        busy_events = []
        idle_events = []
        signal_bus.subscribe(TOPIC_ARM_BUSY, lambda e: busy_events.append(e))
        signal_bus.subscribe(TOPIC_ARM_IDLE, lambda e: idle_events.append(e))

        graph = _mk_graph(["read_file", "count_words"])
        budget = Budget(task_id=graph.task_id, limits=BudgetLimits(tokens=10_000, usd=0.10))
        SwarmRuntime(
            arm_pool=pool,
            signal_bus=signal_bus,
            journal=journal,
        ).run(graph=graph, budget=budget)

        # Implementation note.
        assert len(busy_events) == 2
        assert len(idle_events) == 2

    def test_no_matching_arm_produces_failed_result(self, pool, journal):
        """Implementation note."""
        # Implementation note.
        graph = _mk_graph(["read_file"])
        # Implementation note.
        graph = TaskGraph(
            nodes=[
                TaskNode(
                    node_id="n0",
                    skill_ref=SkillId("never_registered_skill"),
                    args_template={},
                )
            ],
            edges=[],
            budget=graph.budget,
            task_type="mixed",
        )
        budget = Budget(task_id=graph.task_id, limits=BudgetLimits(tokens=1_000, usd=0.01))
        result = SwarmRuntime(arm_pool=pool, journal=journal).run(graph=graph, budget=budget)
        assert not result.all_successful
        assert result.arm_results[0].status == "failed"
        assert "no_arm_matched" in result.arm_results[0].reason


class TestBoidsArbitrationUnderContention:
    def test_multiple_arms_claim_same_resource_single_winner(self, boids, signal_bus):
        """Implementation note."""
        claim_a = ResourceClaim(
            claim_id=new_id(),
            arm_id=ArmId("code_arm"),
            resource_uri="file://shared.lock",
            priority=50,
            ttl_ms=5000,
            claimed_at=now_utc(),
        )
        claim_b = ResourceClaim(
            claim_id=new_id(),
            arm_id=ArmId("search_arm"),
            resource_uri="file://shared.lock",
            priority=30,
            ttl_ms=5000,
            claimed_at=now_utc(),
        )

        verdict_a = boids.arbitrate(claim_a)
        verdict_b = boids.arbitrate(claim_b)

        assert verdict_a == "win"
        assert verdict_b == "lose"  # Implementation note.
        active = boids.active_claims()
        assert len(active) == 1
        assert active[0].arm_id == "code_arm"

    def test_read_only_resource_allows_coexist(self, boids):
        """Implementation note."""
        c1 = ResourceClaim(
            claim_id=new_id(),
            arm_id=ArmId("a1"),
            resource_uri="readonly:docs",
            priority=10,
            ttl_ms=5000,
            claimed_at=now_utc(),
        )
        c2 = ResourceClaim(
            claim_id=new_id(),
            arm_id=ArmId("a2"),
            resource_uri="readonly:docs",
            priority=10,
            ttl_ms=5000,
            claimed_at=now_utc(),
        )
        assert boids.arbitrate(c1) in ("win", "coexist")
        assert boids.arbitrate(c2) == "coexist"

    def test_signal_bus_receives_sucker_grabbed_on_win(self, boids, signal_bus):
        """Implementation note."""
        events = []
        signal_bus.subscribe("sucker.grabbed", lambda e: events.append(e))

        claim = ResourceClaim(
            claim_id=new_id(),
            arm_id=ArmId("winner_arm"),
            resource_uri="file://x.lock",
            priority=50,
            ttl_ms=5000,
            claimed_at=now_utc(),
        )
        boids.arbitrate(claim)
        assert len(events) >= 1


class TestBudgetSharingAcrossArms:
    def test_shared_budget_accumulates(self, pool, journal):
        """Implementation note."""
        graph = _mk_graph(["read_file", "count_words", "hash_text"])
        budget = Budget(task_id=graph.task_id, limits=BudgetLimits(tokens=50_000, usd=0.50))
        result = SwarmRuntime(arm_pool=pool, journal=journal).run(graph=graph, budget=budget)

        # Implementation note.
        assert budget.tokens_spent > 0
        assert budget.usd_spent > 0

        # Implementation note.
        assert result.total_cost_usd >= 0


class TestSwarmFullStackTrajectory:
    def test_trajectory_events_in_journal(self, pool, signal_bus, journal):
        """Implementation note."""
        graph = _mk_graph(["read_file", "count_words"])
        budget = Budget(task_id=graph.task_id, limits=BudgetLimits(tokens=20_000, usd=0.20))
        SwarmRuntime(arm_pool=pool, signal_bus=signal_bus, journal=journal).run(
            graph=graph, budget=budget
        )

        # Implementation note.
        # Implementation note.
        steps = journal.read_by_type("step")
        immunes = journal.read_by_type("immune")
        budgets = journal.read_by_type("budget_commit")
        trajs = journal.read_by_type("trajectory")

        assert len(steps) >= 2  # Implementation note.
        assert len(immunes) >= 2
        assert len(budgets) >= 2
        assert len(trajs) >= 2  # Implementation note.
