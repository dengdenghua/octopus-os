"""Implementation note."""

from __future__ import annotations

import time
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from runtime.execution.swarm import (
    AgentHandoff,
    SwarmEvent,
    SwarmPhaseReport,
    SwarmPlan,
    SwarmResult,
    SwarmRuntime,
)
from runtime.memory.journal import InMemoryJournal
from runtime.platform.models import (
    ArmAssignment,
    ArmId,
    ArmResult,
    Budget,
    BudgetLimits,
    BudgetSpec,
    CostEntry,
    ExecutionResult,
    SkillId,
    Step,
    TaskGraph,
    TaskId,
    TaskNode,
    ToolCall,
)

# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class FakeArm:
    """Implementation note."""

    def __init__(
        self,
        arm_id: str,
        affinity: list[str] | None = None,
        allowed: list[str] | None = None,
        handler: Any = None,
        delay_ms: int = 0,
        raise_exc: Exception | None = None,
    ) -> None:
        self.arm_id = ArmId(arm_id)
        self.affinity = affinity or []
        self.allowed_skills = allowed or []
        self._handler = handler
        self._delay_ms = delay_ms
        self._raise = raise_exc
        self.handle_count = 0

    def can_handle(self, task: ArmAssignment) -> bool:
        # Implementation note.
        for node in task.subgraph.nodes:
            if node.skill_ref is None:
                return False
            if node.skill_ref not in self.allowed_skills:
                return False
        return True

    def handle(self, task: ArmAssignment, budget: Budget) -> ArmResult:
        self.handle_count += 1
        if self._delay_ms:
            time.sleep(self._delay_ms / 1000.0)
        if self._raise is not None:
            raise self._raise
        if self._handler is not None:
            return self._handler(task, budget)
        # Implementation note.
        cost = CostEntry(tokens_in=10, tokens_out=5, usd=0.0005)
        rid = budget.reserve(cost)
        budget.commit(rid, cost)
        return ArmResult(
            arm_id=self.arm_id,
            task_id=task.subgraph.task_id,
            status="success",
            outputs={"handled_by": str(self.arm_id)},
            cost=cost,
        )


class FakeArmPool:
    """Implementation note."""

    def __init__(self, arms: list[FakeArm]) -> None:
        self._arms = arms

    def pick_for(self, task: ArmAssignment) -> FakeArm | None:
        for a in self._arms:
            if a.can_handle(task):
                return a
        return None

    def all_arms(self) -> list[FakeArm]:
        return list(self._arms)

    def __len__(self) -> int:
        return len(self._arms)


class FakeSignalBus:
    """Implementation note."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any], str]] = []

    def publish(
        self,
        topic: str,
        payload: dict[str, Any],
        publisher: str = "system",
    ) -> None:
        self.events.append((topic, dict(payload), publisher))

    def subscribe(self, topic_pattern: str, handler: Any) -> int:
        return 0


class FakeBoidsArbitrator:
    """Implementation note."""

    def __init__(self) -> None:
        self.arbitrate_calls: list[Any] = []
        self.release_calls: list[tuple[ArmId, str]] = []

    def arbitrate(self, claim: Any) -> str:
        self.arbitrate_calls.append(claim)
        return "coexist"

    def release(self, arm_id: ArmId, resource_uri: str) -> None:
        self.release_calls.append((arm_id, resource_uri))


# ═══════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════


@pytest.fixture
def two_node_graph() -> TaskGraph:
    return TaskGraph(
        nodes=[
            TaskNode(node_id="n1", skill_ref="read_file"),
            TaskNode(node_id="n2", skill_ref="run_test"),
        ],
        budget=BudgetSpec(tokens=10_000, usd=0.10),
        task_type="code_fix",
    )


@pytest.fixture
def three_node_graph() -> TaskGraph:
    return TaskGraph(
        nodes=[
            TaskNode(node_id="n1", skill_ref="read_file"),
            TaskNode(node_id="n2", skill_ref="read_file"),
            TaskNode(node_id="n3", skill_ref="read_file"),
        ],
        budget=BudgetSpec(tokens=10_000, usd=0.10),
        task_type="bulk_read",
    )


@pytest.fixture
def generous_budget() -> Budget:
    return Budget(
        task_id=TaskId(uuid4()),
        limits=BudgetLimits(tokens=100_000, usd=10.0),
    )


# ═══════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════


class TestSwarmRuntimeBasics:
    """Implementation note."""

    def test_single_task_single_arm_success(
        self,
        two_node_graph: TaskGraph,
        generous_budget: Budget,
    ) -> None:
        arm = FakeArm("code_arm", allowed=["read_file", "run_test"])
        pool = FakeArmPool([arm])
        runtime = SwarmRuntime(arm_pool=pool, max_workers=2)

        result = runtime.run(graph=two_node_graph, budget=generous_budget)

        assert isinstance(result, SwarmResult)
        assert result.task_id == two_node_graph.task_id
        assert len(result.arm_results) == 2  # Implementation note.
        assert all(r.status == "success" for r in result.arm_results)
        assert result.all_successful is True
        assert arm.handle_count == 2

    def test_split_strategy_single_dispatches_once(
        self,
        two_node_graph: TaskGraph,
        generous_budget: Budget,
    ) -> None:
        arm = FakeArm("code_arm", allowed=["read_file", "run_test"])
        pool = FakeArmPool([arm])
        runtime = SwarmRuntime(arm_pool=pool, max_workers=2)

        result = runtime.run(
            graph=two_node_graph,
            budget=generous_budget,
            split_strategy="single",
        )

        assert len(result.arm_results) == 1
        assert arm.handle_count == 1

    def test_unknown_split_strategy_raises(
        self,
        two_node_graph: TaskGraph,
        generous_budget: Budget,
    ) -> None:
        arm = FakeArm("code_arm", allowed=["read_file", "run_test"])
        pool = FakeArmPool([arm])
        runtime = SwarmRuntime(arm_pool=pool)

        with pytest.raises(ValueError, match="split_strategy"):
            runtime.run(
                graph=two_node_graph,
                budget=generous_budget,
                split_strategy="galaxy_brain",  # type: ignore[arg-type]
            )

    def test_max_workers_validated(self) -> None:
        pool = FakeArmPool([])
        with pytest.raises(ValueError, match="max_workers"):
            SwarmRuntime(arm_pool=pool, max_workers=0)


class TestSwarmRuntimeConcurrency:
    """Implementation note."""

    def test_parallel_dispatch_wall_time_under_serial_bound(
        self,
        three_node_graph: TaskGraph,
        generous_budget: Budget,
    ) -> None:
        # Implementation note.
        # Implementation note.
        # Implementation note.
        arms = [
            FakeArm("a1", allowed=["read_file"], delay_ms=50),
            FakeArm("a2", allowed=["read_file"], delay_ms=50),
        ]
        pool = FakeArmPool(arms)
        runtime = SwarmRuntime(arm_pool=pool, max_workers=3)

        start = time.perf_counter()
        result = runtime.run(graph=three_node_graph, budget=generous_budget)
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        assert result.all_successful is True
        assert len(result.arm_results) == 3
        # Implementation note.
        assert sum(a.handle_count for a in arms) == 3
        # Implementation note.
        assert elapsed_ms < 200.0, f"expected parallel but took {elapsed_ms:.1f}ms"

    def test_parallelism_achieved_is_min_of_tasks_and_workers(
        self,
        three_node_graph: TaskGraph,
        generous_budget: Budget,
    ) -> None:
        arm = FakeArm("solo", allowed=["read_file"])
        pool = FakeArmPool([arm])
        runtime = SwarmRuntime(arm_pool=pool, max_workers=2)

        result = runtime.run(graph=three_node_graph, budget=generous_budget)
        # Implementation note.
        assert result.parallelism_achieved == 2

    def test_parallelism_zero_when_no_arm_matched(
        self,
        two_node_graph: TaskGraph,
        generous_budget: Budget,
    ) -> None:
        # Implementation note.
        pool = FakeArmPool([])
        runtime = SwarmRuntime(arm_pool=pool)

        result = runtime.run(graph=two_node_graph, budget=generous_budget)
        assert result.parallelism_achieved == 0


class TestSwarmRuntimeFailureIsolation:
    """Implementation note."""

    def test_no_arm_matched_produces_failed_result(
        self,
        two_node_graph: TaskGraph,
        generous_budget: Budget,
    ) -> None:
        arm = FakeArm("wrong_arm", allowed=["write_file"])  # Implementation note.
        pool = FakeArmPool([arm])
        runtime = SwarmRuntime(arm_pool=pool)

        result = runtime.run(graph=two_node_graph, budget=generous_budget)

        assert len(result.arm_results) == 2
        assert all(r.status == "failed" for r in result.arm_results)
        assert all(r.reason == "no_arm_matched" for r in result.arm_results)
        assert result.all_successful is False
        assert arm.handle_count == 0

    def test_arm_exception_captured_as_failed(
        self,
        three_node_graph: TaskGraph,
        generous_budget: Budget,
    ) -> None:
        # Implementation note.
        # Implementation note.
        # Implementation note.
        bad = FakeArm(
            "bad_arm",
            allowed=["read_file"],
            raise_exc=RuntimeError("boom"),
        )
        pool = FakeArmPool([bad])
        runtime = SwarmRuntime(arm_pool=pool, max_workers=3)

        result = runtime.run(graph=three_node_graph, budget=generous_budget)

        assert len(result.arm_results) == 3
        assert all(r.status == "failed" for r in result.arm_results)
        assert all("boom" in r.reason for r in result.arm_results)
        # Implementation note.
        assert result.all_successful is False

    def test_one_arm_fails_others_continue(
        self,
        three_node_graph: TaskGraph,
        generous_budget: Budget,
    ) -> None:
        # Implementation note.
        # Implementation note.
        # Implementation note.
        call_log: list[int] = []

        def flaky_handler(task: ArmAssignment, budget: Budget) -> ArmResult:
            call_log.append(1)
            if len(call_log) >= 3:
                raise RuntimeError("flaky_third_call")
            cost = CostEntry(tokens_in=5, usd=0.0001)
            rid = budget.reserve(cost)
            budget.commit(rid, cost)
            return ArmResult(
                arm_id=ArmId("flaky"),
                task_id=task.subgraph.task_id,
                status="success",
                cost=cost,
            )

        arm = FakeArm(
            "flaky",
            allowed=["read_file"],
            handler=flaky_handler,
        )
        pool = FakeArmPool([arm])
        # Implementation note.
        runtime = SwarmRuntime(arm_pool=pool, max_workers=1)

        result = runtime.run(graph=three_node_graph, budget=generous_budget)

        statuses = sorted(r.status for r in result.arm_results)
        assert statuses == ["failed", "success", "success"]
        # Implementation note.
        assert sum(1 for r in result.arm_results if r.status == "success") == 2


class TestSwarmRuntimeSignalBus:
    """Implementation note."""

    def test_no_signal_bus_does_not_raise(
        self,
        two_node_graph: TaskGraph,
        generous_budget: Budget,
    ) -> None:
        arm = FakeArm("a", allowed=["read_file", "run_test"])
        pool = FakeArmPool([arm])
        runtime = SwarmRuntime(arm_pool=pool, signal_bus=None)

        result = runtime.run(graph=two_node_graph, budget=generous_budget)
        assert result.all_successful is True

    def test_signal_bus_receives_busy_and_idle_events(
        self,
        two_node_graph: TaskGraph,
        generous_budget: Budget,
    ) -> None:
        arm = FakeArm("a", allowed=["read_file", "run_test"])
        pool = FakeArmPool([arm])
        bus = FakeSignalBus()
        runtime = SwarmRuntime(arm_pool=pool, signal_bus=bus, max_workers=2)

        runtime.run(graph=two_node_graph, budget=generous_budget)

        topics = [e[0] for e in bus.events]
        # Implementation note.
        assert topics.count("arm.busy") == 2
        assert topics.count("arm.idle") == 2
        # Implementation note.
        for _topic, payload, publisher in bus.events:
            assert publisher == "swarm"
            assert "arm_id" in payload
            assert "task_id" in payload

    def test_signal_bus_error_does_not_break_run(
        self,
        two_node_graph: TaskGraph,
        generous_budget: Budget,
    ) -> None:
        class BrokenBus:
            def publish(self, *a: Any, **kw: Any) -> None:
                raise RuntimeError("bus_down")

            def subscribe(self, *a: Any, **kw: Any) -> int:
                return 0

        arm = FakeArm("a", allowed=["read_file", "run_test"])
        pool = FakeArmPool([arm])
        runtime = SwarmRuntime(arm_pool=pool, signal_bus=BrokenBus())

        # Implementation note.
        result = runtime.run(graph=two_node_graph, budget=generous_budget)
        assert result.all_successful is True


class TestSwarmRuntimeJournal:
    """Implementation note."""

    def test_journal_records_one_aggregated_trajectory_per_task(
        self,
        two_node_graph: TaskGraph,
        generous_budget: Budget,
    ) -> None:
        """Post-2026-04 contract: SwarmRuntime writes ONE Trajectory
        per task, aggregating steps across all ArmResults.

        Old contract (one-per-ArmResult) produced a Trajectory with
        ``steps=[]`` per arm · useless for SkillForge because it
        filters ``step_count >= 2``. The aggregated form preserves
        enough context for the evolution loop to learn from
        parallel swarm execution. See
        ``tests/test_swarm_to_skill_forge_integration.py`` for the
        full end-to-end regression.
        """
        arm = FakeArm("a", allowed=["read_file", "run_test"])
        pool = FakeArmPool([arm])
        journal = InMemoryJournal()
        runtime = SwarmRuntime(arm_pool=pool, journal=journal)

        runtime.run(graph=two_node_graph, budget=generous_budget)

        traj_events = journal.read_by_type("trajectory")
        assert len(traj_events) == 1
        assert traj_events[0].task_id == two_node_graph.task_id
        assert traj_events[0].trajectory.strategy_id == "swarm"

    def test_journal_none_skips_trajectory_write(
        self,
        two_node_graph: TaskGraph,
        generous_budget: Budget,
    ) -> None:
        arm = FakeArm("a", allowed=["read_file", "run_test"])
        pool = FakeArmPool([arm])
        # Implementation note.
        runtime = SwarmRuntime(arm_pool=pool, journal=None)
        result = runtime.run(graph=two_node_graph, budget=generous_budget)
        assert result.all_successful is True

    def test_aggregated_trajectory_orders_double_digit_nodes_numerically(
        self,
    ) -> None:
        def _step(node_id: str) -> Step:
            call = ToolCall(
                caller="test",
                sucker_id=SkillId(f"skill_{node_id}"),
                args={},
            )
            return Step(
                step_id=0,
                node_id=node_id,
                action=call,
                result=ExecutionResult(
                    call_id=call.call_id,
                    status="success",
                    output={"node": node_id},
                ),
            )

        journal = InMemoryJournal()
        runtime = SwarmRuntime(arm_pool=FakeArmPool([]), journal=journal)
        task_id = TaskId(uuid4())
        results = [
            ArmResult(
                arm_id=ArmId("a"),
                task_id=task_id,
                status="success",
                steps=[_step("n10")],
            ),
            ArmResult(
                arm_id=ArmId("b"),
                task_id=task_id,
                status="success",
                steps=[_step("n2")],
            ),
        ]

        runtime._record_trajectories(task_id, results)

        [traj_event] = journal.read_by_type("trajectory")
        assert [s.node_id for s in traj_event.trajectory.steps] == ["n2", "n10"]


class TestSwarmRuntimeBudgetSharing:
    """Implementation note."""

    def test_shared_budget_accumulates_across_arms(
        self,
        three_node_graph: TaskGraph,
        generous_budget: Budget,
    ) -> None:
        # Implementation note.
        arms = [
            FakeArm("a1", allowed=["read_file"]),
            FakeArm("a2", allowed=["read_file"]),
        ]
        pool = FakeArmPool(arms)
        runtime = SwarmRuntime(arm_pool=pool, max_workers=3)

        before_tokens = generous_budget.tokens_spent
        before_usd = generous_budget.usd_spent

        result = runtime.run(graph=three_node_graph, budget=generous_budget)

        assert result.all_successful is True
        # Implementation note.
        assert generous_budget.tokens_spent - before_tokens == 15 * 3
        assert generous_budget.usd_spent - before_usd == pytest.approx(0.0015)
        # Implementation note.
        assert result.total_cost_usd == pytest.approx(0.0015)


class TestSwarmResultAggregation:
    """Implementation note."""

    def test_all_successful_false_when_any_failed(
        self,
        two_node_graph: TaskGraph,
        generous_budget: Budget,
    ) -> None:
        # Implementation note.
        arm = FakeArm("partial", allowed=["read_file"])
        pool = FakeArmPool([arm])
        runtime = SwarmRuntime(arm_pool=pool)

        result = runtime.run(graph=two_node_graph, budget=generous_budget)

        statuses = {r.status for r in result.arm_results}
        assert "success" in statuses
        assert "failed" in statuses
        assert result.all_successful is False

    def test_all_successful_false_on_empty_results_edge_case(
        self,
        generous_budget: Budget,
    ) -> None:
        # Implementation note.
        graph = TaskGraph(
            nodes=[TaskNode(node_id="n0", skill_ref="anything")],
            budget=BudgetSpec(tokens=1000, usd=0.01),
        )
        pool = FakeArmPool([])
        runtime = SwarmRuntime(arm_pool=pool)
        result = runtime.run(graph=graph, budget=generous_budget)

        assert len(result.arm_results) == 1
        assert result.arm_results[0].status == "failed"
        assert result.all_successful is False

    def test_total_wall_ms_is_positive(
        self,
        two_node_graph: TaskGraph,
        generous_budget: Budget,
    ) -> None:
        arm = FakeArm("a", allowed=["read_file", "run_test"], delay_ms=5)
        pool = FakeArmPool([arm])
        runtime = SwarmRuntime(arm_pool=pool)

        result = runtime.run(graph=two_node_graph, budget=generous_budget)
        assert result.total_wall_ms > 0


class TestSwarmRuntimeResultImmutable:
    """Implementation note."""

    def test_swarm_result_is_frozen(
        self,
        two_node_graph: TaskGraph,
        generous_budget: Budget,
    ) -> None:
        arm = FakeArm("a", allowed=["read_file", "run_test"])
        pool = FakeArmPool([arm])
        runtime = SwarmRuntime(arm_pool=pool)
        result = runtime.run(graph=two_node_graph, budget=generous_budget)

        # Implementation note.
        with pytest.raises(ValidationError):
            result.parallelism_achieved = 999  # type: ignore[misc]


class TestSwarmRuntimeExplainability:
    def test_result_contains_ui_ready_plan_contracts_and_events(
        self,
        two_node_graph: TaskGraph,
        generous_budget: Budget,
    ) -> None:
        arm = FakeArm("code_arm", allowed=["read_file", "run_test"])
        pool = FakeArmPool([arm])
        runtime = SwarmRuntime(arm_pool=pool, max_workers=2)

        result = runtime.run(graph=two_node_graph, budget=generous_budget)

        assert isinstance(result.plan, SwarmPlan)
        assert result.plan.task_id == two_node_graph.task_id
        assert result.plan.strategy == "per_node"
        assert result.plan.max_workers == 2
        assert len(result.plan.phases) == 1
        assert result.plan.phases[0].parallel is True
        assert len(result.plan.contracts) == 2

        first = result.plan.contracts[0]
        assert first.agent_id == "code_arm"
        assert first.node_ids == ["n1"]
        assert first.role == "read_file"
        assert first.owned_scope == ["node:n1"]
        assert first.forbidden_scope == ["node:n2"]
        assert first.success_criteria == [
            "Complete node n1",
            "Return an ArmResult with status=success",
        ]

        event_types = [event.type for event in result.events]
        assert event_types == [
            "plan_created",
            "phase_started",
            "agent_assigned",
            "agent_assigned",
            "agent_started",
            "agent_started",
            "agent_finished",
            "agent_finished",
            "phase_finished",
            "swarm_finished",
        ]
        assert result.events[0].lane == "workflow"
        assert result.events[2].lane == "agent"
        assert result.events[-1].payload["all_successful"] is True

        payload = result.model_dump(mode="json")
        assert payload["plan"]["contracts"][0]["owned_scope"] == ["node:n1"]
        assert payload["events"][-1]["type"] == "swarm_finished"

    def test_result_contains_agent_handoffs_and_phase_reports(
        self,
        two_node_graph: TaskGraph,
        generous_budget: Budget,
    ) -> None:
        def handler(task: ArmAssignment, budget: Budget) -> ArmResult:
            node_id = task.subgraph.nodes[0].node_id
            return ArmResult(
                arm_id=ArmId("code_arm"),
                task_id=task.subgraph.task_id,
                status="success",
                outputs={
                    "summary": f"completed {node_id}",
                    "files": [f"src/{node_id}.py"],
                },
                cost=CostEntry(usd=0.01),
            )

        arm = FakeArm(
            "code_arm",
            allowed=["read_file", "run_test"],
            handler=handler,
        )
        pool = FakeArmPool([arm])
        runtime = SwarmRuntime(arm_pool=pool, max_workers=2)

        result = runtime.run(graph=two_node_graph, budget=generous_budget)

        assert all(isinstance(h, AgentHandoff) for h in result.handoffs)
        assert all(isinstance(r, SwarmPhaseReport) for r in result.phase_reports)
        assert [h.node_ids for h in result.handoffs] == [["n1"], ["n2"]]
        assert result.handoffs[0].summary == "completed n1"
        assert result.handoffs[0].artifacts == ["src/n1.py"]
        assert result.handoffs[0].cost_usd == pytest.approx(0.01)

        [phase] = result.phase_reports
        assert phase.phase_index == 0
        assert phase.status == "success"
        assert phase.assignment_count == 2
        assert phase.succeeded == 2
        assert phase.failed == 0
        assert phase.handoff_count == 2
        assert phase.cost_usd == pytest.approx(0.02)

        payload = result.model_dump(mode="json")
        assert payload["handoffs"][0]["summary"] == "completed n1"
        assert payload["phase_reports"][0]["status"] == "success"

    def test_topo_layers_plan_preserves_workflow_dependencies(
        self,
        generous_budget: Budget,
    ) -> None:
        graph = TaskGraph(
            nodes=[
                TaskNode(node_id="design", skill_ref="read_file"),
                TaskNode(node_id="build", skill_ref="run_test"),
            ],
            edges=[
                {"from_node": "design", "to_node": "build"},
            ],
            budget=BudgetSpec(tokens=10_000, usd=0.10),
            task_type="code_fix",
        )
        arm = FakeArm("code_arm", allowed=["read_file", "run_test"])
        pool = FakeArmPool([arm])
        runtime = SwarmRuntime(arm_pool=pool, max_workers=2)

        result = runtime.run(
            graph=graph,
            budget=generous_budget,
            split_strategy="topo_layers",
        )

        assert [phase.node_ids for phase in result.plan.phases] == [
            ["design"],
            ["build"],
        ]
        assert result.plan.phases[0].parallel is False
        assert result.plan.contracts[1].depends_on == ["design"]
        assert any(
            event.type == "phase_started" and event.payload["phase_index"] == 1
            for event in result.events
        )

    def test_swarm_event_is_frozen(self) -> None:
        event = SwarmEvent(type="plan_created", lane="workflow")

        with pytest.raises(ValidationError):
            event.type = "swarm_finished"  # type: ignore[misc]
