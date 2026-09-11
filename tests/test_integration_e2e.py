"""Implementation note."""

from __future__ import annotations

import pytest

from runtime.core.cerebrum import StaticPlanner
from runtime.core.cerebrum.planner import Rule
from runtime.execution.suckers import Skill, SkillRegistry
from runtime.execution.tool_engine import ToolExecutor
from runtime.memory.journal import InMemoryJournal
from runtime.platform.models import (
    ArmId,
    Budget,
    BudgetLimits,
    BudgetSpec,
    ParsedIntent,
    SkillId,
    Step,
    Trajectory,
    TrajectoryOutcome,
)
from runtime.safety.auth import TrustEngine


@pytest.fixture
def full_stack():
    """Implementation note."""

    # Implementation note.
    registry = SkillRegistry()

    calls_made: list[str] = []

    def _read_file(**kw):
        calls_made.append("read_file")
        return f"<contents of intent: {kw.get('intent_goal', '')}>"

    def _edit_code(**kw):
        calls_made.append("edit_code")
        return "<patch>"

    def _run_pytest(**kw):
        calls_made.append("run_pytest")
        return {"passed": 10, "failed": 0}

    for name, handler in [
        ("read_file", _read_file),
        ("edit_code", _edit_code),
        ("run_pytest", _run_pytest),
    ]:
        registry.register(
            Skill(
                name=name,
                trusted_source=f"skill://public/{name}",
                affinity=["code"],
                handler=handler,
            )
        )

    # Implementation note.
    planner = StaticPlanner(
        rules=[
            Rule(
                name="code_fix_basic",
                intent_types=["debug"],
                skill_sequence=[
                    SkillId("read_file"),
                    SkillId("edit_code"),
                    SkillId("run_pytest"),
                ],
            )
        ],
        default_budget=BudgetSpec(tokens=50_000, usd=0.50),
    )

    immunity = TrustEngine(trusted_sources=["skill://public/*"])
    journal = InMemoryJournal()
    executor = ToolExecutor(registry, immunity, journal)

    return {
        "planner": planner,
        "executor": executor,
        "journal": journal,
        "calls_made": calls_made,
    }


class TestEndToEnd:
    def test_intent_to_trajectory(self, full_stack):
        """Implementation note."""

        # Implementation note.
        intent = ParsedIntent(
            raw="fix failing pytest in tests/test_foo.py",
            intent_type="debug",
            normalized_goal="fix failing pytest in tests/test_foo.py",
        )

        # 2. PLAN
        graph = full_stack["planner"].plan(intent)
        assert len(graph.nodes) == 3

        # Implementation note.
        budget = Budget(
            task_id=graph.task_id,
            limits=BudgetLimits(tokens=graph.budget.tokens, usd=graph.budget.usd),
        )

        # Implementation note.
        executor = full_stack["executor"]
        steps: list[Step] = []
        for i, node in enumerate(graph.nodes):
            step = executor.execute_step(
                step_id=i,
                node_id=node.node_id,
                sucker_id=node.skill_ref,
                args={"intent_goal": intent.normalized_goal},
                caller="arms/code_arm",
                task_id=graph.task_id,
                arm_id=ArmId("code_arm"),
                budget=budget,
            )
            steps.append(step)

        # Implementation note.
        assert all(s.success for s in steps)
        assert full_stack["calls_made"] == ["read_file", "edit_code", "run_pytest"]

        # Implementation note.
        traj = Trajectory(
            task_id=graph.task_id,
            arm_id=ArmId("code_arm"),
            steps=steps,
            outcome=TrajectoryOutcome(success=True),
        )
        full_stack["journal"].write_trajectory(traj)

        # Implementation note.
        journal = full_stack["journal"]
        assert len(journal.read_by_type("step")) == 3
        assert len(journal.read_by_type("immune")) == 3
        assert len(journal.read_by_type("budget_commit")) == 3
        assert len(journal.read_by_type("trajectory")) == 1

        # Implementation note.
        assert budget.tokens_spent > 0
        assert budget.utilization < 1.0


class TestBudgetCutoffMidFlow:
    def test_budget_exhausted_midway_circuit_breaks(self, full_stack):
        """Implementation note."""
        intent = ParsedIntent(
            raw="fix failing test", intent_type="debug", normalized_goal="fix failing test"
        )
        graph = full_stack["planner"].plan(intent)

        # Implementation note.
        budget = Budget(
            task_id=graph.task_id,
            limits=BudgetLimits(tokens=300, usd=0.01),
        )

        executor = full_stack["executor"]
        step0 = executor.execute_step(
            step_id=0,
            node_id="n0",
            sucker_id=graph.nodes[0].skill_ref,
            args={"intent_goal": intent.normalized_goal},
            caller="arms/code_arm",
            task_id=graph.task_id,
            arm_id=ArmId("code_arm"),
            budget=budget,
        )
        assert step0.success

        step1 = executor.execute_step(
            step_id=1,
            node_id="n1",
            sucker_id=graph.nodes[1].skill_ref,
            args={"intent_goal": intent.normalized_goal},
            caller="arms/code_arm",
            task_id=graph.task_id,
            arm_id=ArmId("code_arm"),
            budget=budget,
        )
        assert step1.result.status == "circuit_broken"
        assert budget.status == "exceeded"
