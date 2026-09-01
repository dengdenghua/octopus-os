"""Implementation note."""

from __future__ import annotations

from pathlib import Path

from runtime.core.graph_runtime import GraphRuntime
from runtime.execution.suckers import SkillRegistry
from runtime.execution.suckers.builtins import register_all
from runtime.execution.tool_engine import ToolExecutor
from runtime.memory.journal import (
    InMemoryJournal,
    JSONLJournal,
    NodeStartedEvent,
    TaskCheckpointEvent,
    TaskStartedEvent,
)
from runtime.platform.models import (
    ArmId,
    Budget,
    BudgetLimits,
    BudgetSpec,
    SkillId,
    TaskGraph,
    TaskNode,
)
from runtime.safety.auth import TrustEngine
from runtime.sensing.gateway import StreamingJournal

# ═══════════════════════════════════════════════════════════
# fixtures
# ═══════════════════════════════════════════════════════════


def _make_stack():
    reg = SkillRegistry()
    register_all(reg)
    journal = InMemoryJournal()
    executor = ToolExecutor(
        registry=reg,
        immunity=TrustEngine(trusted_sources=["skill://public/*"]),
        journal=journal,
    )
    return reg, journal, executor


def _graph_with_n_nodes(n: int, skill: str = "list_cwd") -> TaskGraph:
    return TaskGraph(
        nodes=[
            TaskNode(node_id=f"n{i}", skill_ref=SkillId(skill), args_template={"path": "."})
            for i in range(n)
        ],
        edges=[],
        budget=BudgetSpec(tokens=10_000, usd=0.10),
        strategy="progress_test",
        task_type="probe",
    )


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestEventSequence:
    def test_task_started_written_before_first_node(self):
        _, journal, executor = _make_stack()
        rt = GraphRuntime(executor=executor, journal=journal)
        graph = _graph_with_n_nodes(2)
        budget = Budget(task_id=graph.task_id, limits=BudgetLimits(tokens=10_000, usd=0.10))

        rt.run(graph, budget=budget, caller="arms/x", arm_id=ArmId("x"))

        events = journal.read_all()
        # Implementation note.
        assert events[0].event_type == "task_started"
        assert events[0].total_nodes == 2
        assert events[0].strategy == "progress_test"

    def test_node_started_before_each_step(self):
        _, journal, executor = _make_stack()
        rt = GraphRuntime(executor=executor, journal=journal)
        graph = _graph_with_n_nodes(3)
        budget = Budget(task_id=graph.task_id, limits=BudgetLimits(tokens=10_000, usd=0.10))

        rt.run(graph, budget=budget, caller="arms/x", arm_id=ArmId("x"))

        events = journal.read_all()
        # Implementation note.
        relevant = [e for e in events if e.event_type in ("node_started", "step")]
        # Implementation note.
        kinds = [e.event_type for e in relevant]
        assert kinds == [
            "node_started",
            "step",
            "node_started",
            "step",
            "node_started",
            "step",
        ]

    def test_node_started_carries_metadata(self):
        _, journal, executor = _make_stack()
        rt = GraphRuntime(executor=executor, journal=journal)
        graph = _graph_with_n_nodes(2)
        budget = Budget(task_id=graph.task_id, limits=BudgetLimits(tokens=10_000, usd=0.10))
        rt.run(graph, budget=budget, caller="arms/x", arm_id=ArmId("x"))

        node_starts = [e for e in journal.read_all() if isinstance(e, NodeStartedEvent)]
        assert len(node_starts) == 2
        assert node_starts[0].node_index == 0
        assert node_starts[0].node_id == "n0"
        assert node_starts[0].skill_ref == "list_cwd"
        assert node_starts[1].node_index == 1
        assert node_starts[1].node_id == "n1"

    def test_trajectory_last(self):
        _, journal, executor = _make_stack()
        rt = GraphRuntime(executor=executor, journal=journal)
        graph = _graph_with_n_nodes(2)
        budget = Budget(task_id=graph.task_id, limits=BudgetLimits(tokens=10_000, usd=0.10))
        rt.run(graph, budget=budget, caller="arms/x", arm_id=ArmId("x"))

        events = journal.read_all()
        assert events[-1].event_type == "trajectory"


# ═══════════════════════════════════════════════════════════
# Checkpoint
# ═══════════════════════════════════════════════════════════


class TestCheckpoint:
    def test_no_checkpoint_by_default(self):
        _, journal, executor = _make_stack()
        rt = GraphRuntime(executor=executor, journal=journal)  # Implementation note.
        graph = _graph_with_n_nodes(5)
        budget = Budget(task_id=graph.task_id, limits=BudgetLimits(tokens=10_000, usd=0.10))
        rt.run(graph, budget=budget, caller="arms/x", arm_id=ArmId("x"))
        cps = [e for e in journal.read_all() if isinstance(e, TaskCheckpointEvent)]
        assert cps == []

    def test_every_2_nodes_writes_checkpoint(self):
        _, journal, executor = _make_stack()
        rt = GraphRuntime(executor=executor, journal=journal, checkpoint_every_n_nodes=2)
        graph = _graph_with_n_nodes(5)
        budget = Budget(task_id=graph.task_id, limits=BudgetLimits(tokens=10_000, usd=0.10))
        rt.run(graph, budget=budget, caller="arms/x", arm_id=ArmId("x"))

        cps = [e for e in journal.read_all() if isinstance(e, TaskCheckpointEvent)]
        # Implementation note.
        assert len(cps) == 2
        assert cps[0].nodes_completed == 2
        assert cps[0].total_nodes == 5
        assert cps[1].nodes_completed == 4


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestJSONLRoundTrip:
    def test_new_events_persist_and_load(self, tmp_path: Path):
        reg = SkillRegistry()
        register_all(reg)
        path = tmp_path / "events.jsonl"
        j1 = JSONLJournal(path)
        executor = ToolExecutor(
            registry=reg,
            immunity=TrustEngine(trusted_sources=["skill://public/*"]),
            journal=j1,
        )
        rt = GraphRuntime(executor=executor, journal=j1, checkpoint_every_n_nodes=1)
        graph = _graph_with_n_nodes(2)
        budget = Budget(task_id=graph.task_id, limits=BudgetLimits(tokens=10_000, usd=0.10))
        rt.run(graph, budget=budget, caller="arms/x", arm_id=ArmId("x"))

        # Implementation note.
        j2 = JSONLJournal(path)
        types = {e.event_type for e in j2.read_all()}
        assert "task_started" in types
        assert "node_started" in types
        assert "task_checkpoint" in types
        assert "step" in types
        assert "trajectory" in types

        # Implementation note.
        ts = [e for e in j2.read_all() if isinstance(e, TaskStartedEvent)]
        assert len(ts) == 1
        assert ts[0].total_nodes == 2


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestStreamingBroadcast:
    def test_subscribers_receive_progress_events(self):
        reg = SkillRegistry()
        register_all(reg)
        base = InMemoryJournal()
        streaming = StreamingJournal(base)
        executor = ToolExecutor(
            registry=reg,
            immunity=TrustEngine(trusted_sources=["skill://public/*"]),
            journal=streaming,
        )
        rt = GraphRuntime(executor=executor, journal=streaming)

        seen: list = []
        streaming.subscribe(lambda e: seen.append(e))

        graph = _graph_with_n_nodes(2)
        budget = Budget(task_id=graph.task_id, limits=BudgetLimits(tokens=10_000, usd=0.10))
        rt.run(graph, budget=budget, caller="arms/x", arm_id=ArmId("x"))

        # Implementation note.
        types = [e.event_type for e in seen]
        assert "task_started" in types
        assert types.count("node_started") == 2
        assert types.count("step") == 2
        assert "trajectory" in types


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestBackwardCompat:
    def test_read_by_type_works_with_new_types(self):
        _, journal, executor = _make_stack()
        rt = GraphRuntime(executor=executor, journal=journal)
        graph = _graph_with_n_nodes(2)
        budget = Budget(task_id=graph.task_id, limits=BudgetLimits(tokens=10_000, usd=0.10))
        rt.run(graph, budget=budget, caller="arms/x", arm_id=ArmId("x"))

        assert len(journal.read_by_type("task_started")) == 1
        assert len(journal.read_by_type("node_started")) == 2
        assert len(journal.read_by_type("trajectory")) == 1
