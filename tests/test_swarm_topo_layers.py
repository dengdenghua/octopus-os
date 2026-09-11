"""Implementation note."""

from __future__ import annotations

import threading
import time
from uuid import uuid4

import pytest

from runtime.execution.swarm import SwarmRuntime
from runtime.execution.swarm.runtime import _split_topo_layers
from runtime.platform.models import (
    ArmAssignment,
    ArmId,
    ArmResult,
    Budget,
    BudgetLimits,
    BudgetSpec,
    CostEntry,
    TaskGraph,
    TaskId,
    TaskNode,
    WorkflowEdge,
)

# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class _TimingArm:
    """Implementation note."""

    _concurrent = 0
    _peak = 0
    _lock = threading.Lock()

    def __init__(self, arm_id: str, delay_ms: int = 30) -> None:
        self.arm_id = ArmId(arm_id)
        self.affinity = []
        self.allowed_skills = ["any"]
        self._delay_ms = delay_ms
        self.timeline: list[tuple[str, float, float]] = []

    def can_handle(self, task: ArmAssignment) -> bool:
        return True

    def handle(self, task: ArmAssignment, budget: Budget) -> ArmResult:
        node_id = task.subgraph.nodes[0].node_id
        start = time.monotonic()
        with _TimingArm._lock:
            _TimingArm._concurrent += 1
            _TimingArm._peak = max(_TimingArm._peak, _TimingArm._concurrent)
        try:
            time.sleep(self._delay_ms / 1000.0)
        finally:
            with _TimingArm._lock:
                _TimingArm._concurrent -= 1
        end = time.monotonic()
        self.timeline.append((node_id, start, end))
        cost = CostEntry(tokens_in=1, tokens_out=1, usd=0.0)
        rid = budget.reserve(cost)
        budget.commit(rid, cost)
        return ArmResult(
            arm_id=self.arm_id,
            task_id=task.subgraph.task_id,
            status="success",
            outputs={"node": node_id},
            cost=cost,
        )


class _SharedPool:
    """Implementation note."""

    def __init__(self, arm: _TimingArm) -> None:
        self._arm = arm

    def pick_for(self, task: ArmAssignment) -> _TimingArm:
        return self._arm


@pytest.fixture(autouse=True)
def _reset_counters():
    _TimingArm._concurrent = 0
    _TimingArm._peak = 0
    yield


@pytest.fixture
def generous_budget() -> Budget:
    return Budget(
        task_id=TaskId(uuid4()),
        limits=BudgetLimits(tokens=100_000, usd=10.0),
    )


def _node(nid: str) -> TaskNode:
    return TaskNode(node_id=nid, skill_ref="any")


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestSplitTopoLayers:
    def test_linear_chain_gives_single_node_layers(self):
        g = TaskGraph(
            nodes=[_node("a"), _node("b"), _node("c")],
            edges=[
                WorkflowEdge(from_node="a", to_node="b"),
                WorkflowEdge(from_node="b", to_node="c"),
            ],
            budget=BudgetSpec(tokens=1000, usd=0.1),
        )
        layers = _split_topo_layers(g)
        assert len(layers) == 3
        # Implementation note.
        assert [
            layers[0][0].subgraph.nodes[0].node_id,
            layers[1][0].subgraph.nodes[0].node_id,
            layers[2][0].subgraph.nodes[0].node_id,
        ] == ["a", "b", "c"]

    def test_diamond_has_two_middle_nodes_in_same_layer(self):
        g = TaskGraph(
            nodes=[_node("a"), _node("b"), _node("c"), _node("d")],
            edges=[
                WorkflowEdge(from_node="a", to_node="b"),
                WorkflowEdge(from_node="a", to_node="c"),
                WorkflowEdge(from_node="b", to_node="d"),
                WorkflowEdge(from_node="c", to_node="d"),
            ],
            budget=BudgetSpec(tokens=1000, usd=0.1),
        )
        layers = _split_topo_layers(g)
        assert len(layers) == 3
        assert {a.subgraph.nodes[0].node_id for a in layers[0]} == {"a"}
        assert {a.subgraph.nodes[0].node_id for a in layers[1]} == {"b", "c"}
        assert {a.subgraph.nodes[0].node_id for a in layers[2]} == {"d"}

    def test_no_edges_single_layer_all_nodes(self):
        g = TaskGraph(
            nodes=[_node("a"), _node("b"), _node("c")],
            budget=BudgetSpec(tokens=1000, usd=0.1),
        )
        layers = _split_topo_layers(g)
        assert len(layers) == 1
        assert len(layers[0]) == 3

    def test_cycle_raises(self):
        # TaskGraph now validates at construction time (pydantic model
        # validator) instead of deferring to _split_topo_layers. Assert
        # the construction itself raises, not the later split call.
        with pytest.raises(ValueError, match="cycle"):
            TaskGraph(
                nodes=[_node("a"), _node("b")],
                edges=[
                    WorkflowEdge(from_node="a", to_node="b"),
                    WorkflowEdge(from_node="b", to_node="a"),
                ],
                budget=BudgetSpec(tokens=1000, usd=0.1),
            )

    def test_unknown_node_in_edge_raises(self):
        # Same as above — edge validity is checked at TaskGraph construction.
        with pytest.raises(ValueError, match="unknown node|not found"):
            TaskGraph(
                nodes=[_node("a")],
                edges=[WorkflowEdge(from_node="a", to_node="ghost")],
                budget=BudgetSpec(tokens=1000, usd=0.1),
            )


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestTopoLayersExecution:
    def test_linear_chain_runs_sequentially(self, generous_budget):
        """Implementation note."""
        arm = _TimingArm("timing", delay_ms=30)
        pool = _SharedPool(arm)
        runtime = SwarmRuntime(arm_pool=pool, max_workers=4)

        g = TaskGraph(
            nodes=[_node("a"), _node("b"), _node("c")],
            edges=[
                WorkflowEdge(from_node="a", to_node="b"),
                WorkflowEdge(from_node="b", to_node="c"),
            ],
            budget=BudgetSpec(tokens=1000, usd=0.1),
        )
        result = runtime.run(
            graph=g,
            budget=generous_budget,
            split_strategy="topo_layers",
        )
        assert result.all_successful
        assert len(result.arm_results) == 3
        assert _TimingArm._peak == 1  # Implementation note.

        # Implementation note.
        timeline = sorted(arm.timeline, key=lambda t: t[1])
        assert [t[0] for t in timeline] == ["a", "b", "c"]

    def test_diamond_middle_layer_runs_concurrent(self, generous_budget):
        """Implementation note."""
        arm = _TimingArm("timing", delay_ms=50)
        pool = _SharedPool(arm)
        runtime = SwarmRuntime(arm_pool=pool, max_workers=4)

        g = TaskGraph(
            nodes=[_node("a"), _node("b"), _node("c"), _node("d")],
            edges=[
                WorkflowEdge(from_node="a", to_node="b"),
                WorkflowEdge(from_node="a", to_node="c"),
                WorkflowEdge(from_node="b", to_node="d"),
                WorkflowEdge(from_node="c", to_node="d"),
            ],
            budget=BudgetSpec(tokens=1000, usd=0.1),
        )
        result = runtime.run(
            graph=g,
            budget=generous_budget,
            split_strategy="topo_layers",
        )
        assert result.all_successful
        assert len(result.arm_results) == 4
        # Implementation note.
        assert _TimingArm._peak >= 2
        assert result.parallelism_achieved >= 2

        # Implementation note.
        by_node = {nid: (s, e) for nid, s, e in arm.timeline}
        d_start = by_node["d"][0]
        assert d_start >= by_node["b"][1], "d started before b finished"
        assert d_start >= by_node["c"][1], "d started before c finished"


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestPerNodeEdgeGuard:
    def test_per_node_rejects_graph_with_edges(self, generous_budget):
        arm = _TimingArm("x")
        runtime = SwarmRuntime(arm_pool=_SharedPool(arm))
        g = TaskGraph(
            nodes=[_node("a"), _node("b")],
            edges=[WorkflowEdge(from_node="a", to_node="b")],
            budget=BudgetSpec(tokens=1000, usd=0.1),
        )
        with pytest.raises(ValueError, match="per_node requires edgeless"):
            runtime.run(
                graph=g,
                budget=generous_budget,
                split_strategy="per_node",
            )

    def test_per_node_still_works_without_edges(self, generous_budget):
        arm = _TimingArm("x", delay_ms=5)
        runtime = SwarmRuntime(arm_pool=_SharedPool(arm))
        g = TaskGraph(
            nodes=[_node("a"), _node("b")],
            budget=BudgetSpec(tokens=1000, usd=0.1),
        )
        result = runtime.run(
            graph=g,
            budget=generous_budget,
            split_strategy="per_node",
        )
        assert result.all_successful
        assert len(result.arm_results) == 2
