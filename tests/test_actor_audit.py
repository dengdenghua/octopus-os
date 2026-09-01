"""Implementation note."""

from __future__ import annotations

from uuid import uuid4

from runtime.core.graph_runtime import GraphRuntime
from runtime.execution.suckers import SkillRegistry
from runtime.execution.suckers.builtins import register_all
from runtime.execution.tool_engine import ToolExecutor
from runtime.memory.journal import (
    InMemoryJournal,
    TaskStartedEvent,
    TrajectoryEvent,
)
from runtime.platform.models import (
    ArmId,
    Budget,
    BudgetLimits,
    BudgetSpec,
    SkillId,
    TaskGraph,
    TaskId,
    TaskNode,
)
from runtime.safety.auth import TrustEngine

# ═══════════════════════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════════════════════


def _stack():
    reg = SkillRegistry()
    register_all(reg)
    journal = InMemoryJournal()
    executor = ToolExecutor(
        registry=reg,
        immunity=TrustEngine(trusted_sources=["skill://public/*"]),
        journal=journal,
    )
    return journal, executor


def _graph(n: int = 1) -> TaskGraph:
    return TaskGraph(
        nodes=[
            TaskNode(
                node_id=f"n{i}",
                skill_ref=SkillId("list_cwd"),
                args_template={"path": "."},
            )
            for i in range(n)
        ],
        edges=[],
        budget=BudgetSpec(tokens=10_000, usd=0.10),
    )


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestActorField:
    def test_actor_default_none(self):
        """Implementation note."""
        from runtime.memory.journal.journal import BudgetEvent

        ev = BudgetEvent(event_type="budget_commit")
        assert ev.actor is None

    def test_actor_preserves_on_write(self):
        j = InMemoryJournal()
        tid = TaskId(uuid4())
        j.write_task_started(
            tid,
            arm_id=ArmId("a"),
            actor="user_alice",
            total_nodes=2,
            strategy="s",
        )
        events = j.read_all()
        assert events[0].actor == "user_alice"

    def test_write_step_actor(self):
        from runtime.platform.models import (
            ExecutionResult,
            Step,
            ToolCall,
        )

        j = InMemoryJournal()
        tid = TaskId(uuid4())
        call = ToolCall(caller="arms/x", sucker_id="list_cwd", args={})
        step = Step(
            step_id=0,
            node_id="n0",
            action=call,
            result=ExecutionResult(call_id=call.call_id, status="success"),
        )
        j.write_step(tid, ArmId("x"), step, actor="alice")
        evs = j.read_by_type("step")
        assert evs[0].actor == "alice"


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestReadByActor:
    def test_filters_correctly(self):
        j = InMemoryJournal()
        for actor_name in ("alice", "bob", "alice", "carol", "bob"):
            tid = TaskId(uuid4())
            j.write_task_started(
                tid,
                arm_id=ArmId("a"),
                actor=actor_name,
                total_nodes=1,
                strategy="s",
            )
        alice_events = j.read_by_actor("alice")
        bob_events = j.read_by_actor("bob")
        carol_events = j.read_by_actor("carol")
        assert len(alice_events) == 2
        assert len(bob_events) == 2
        assert len(carol_events) == 1
        # Implementation note.
        assert j.read_by_actor("dave") == []

    def test_none_actor_not_matched_by_string(self):
        """Implementation note."""
        from runtime.platform.models import (
            ExecutionResult,
            Step,
            ToolCall,
        )

        j = InMemoryJournal()
        tid = TaskId(uuid4())
        call = ToolCall(caller="arms/x", sucker_id="list_cwd", args={})
        step = Step(
            step_id=0,
            node_id="n0",
            action=call,
            result=ExecutionResult(call_id=call.call_id, status="success"),
        )
        j.write_step(tid, ArmId("x"), step)  # Implementation note.
        assert j.read_by_actor("alice") == []


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestExecutorPassesActor:
    def test_all_events_carry_actor(self):
        journal, executor = _stack()
        tid = TaskId(uuid4())
        budget = Budget(task_id=tid, limits=BudgetLimits(tokens=1000, usd=0.01))
        executor.execute_step(
            step_id=0,
            node_id="n0",
            sucker_id=SkillId("list_cwd"),
            args={"path": "."},
            caller="arms/x",
            task_id=tid,
            arm_id=ArmId("x"),
            budget=budget,
            actor="user_zelda",
        )
        # Implementation note.
        events = journal.read_all()
        assert len(events) >= 3
        for ev in events:
            assert ev.actor == "user_zelda", f"{ev.event_type} missing actor"

    def test_actor_none_stays_none(self):
        journal, executor = _stack()
        tid = TaskId(uuid4())
        budget = Budget(task_id=tid, limits=BudgetLimits(tokens=1000, usd=0.01))
        executor.execute_step(
            step_id=0,
            node_id="n0",
            sucker_id=SkillId("list_cwd"),
            args={"path": "."},
            caller="arms/x",
            task_id=tid,
            arm_id=ArmId("x"),
            budget=budget,
        )
        for ev in journal.read_all():
            assert ev.actor is None


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestRuntimePassesActor:
    def test_actor_propagates_all_events(self):
        journal, executor = _stack()
        rt = GraphRuntime(executor=executor, journal=journal)
        graph = _graph(2)
        budget = Budget(task_id=graph.task_id, limits=BudgetLimits(tokens=10_000, usd=0.10))
        rt.run(
            graph,
            budget=budget,
            caller="arms/x",
            arm_id=ArmId("x"),
            actor="user_probe",
        )

        # Implementation note.
        events = journal.read_all()
        assert any(isinstance(e, TaskStartedEvent) for e in events)
        assert any(isinstance(e, TrajectoryEvent) for e in events)
        for ev in events:
            assert ev.actor == "user_probe", f"{ev.event_type} missing actor={ev.actor}"

    def test_runtime_checkpoint_includes_actor(self):
        journal, executor = _stack()
        rt = GraphRuntime(
            executor=executor,
            journal=journal,
            checkpoint_every_n_nodes=1,
        )
        graph = _graph(2)
        budget = Budget(task_id=graph.task_id, limits=BudgetLimits(tokens=10_000, usd=0.10))
        rt.run(
            graph,
            budget=budget,
            caller="arms/x",
            arm_id=ArmId("x"),
            actor="alice",
        )
        checkpoints = journal.read_by_type("task_checkpoint")
        assert len(checkpoints) >= 1
        for cp in checkpoints:
            assert cp.actor == "alice"


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestMultiActorIsolation:
    def test_two_actors_audited_separately(self):
        journal, executor = _stack()
        rt = GraphRuntime(executor=executor, journal=journal)

        g1 = _graph(1)
        g2 = _graph(1)
        budget1 = Budget(task_id=g1.task_id, limits=BudgetLimits(tokens=1000, usd=0.01))
        budget2 = Budget(task_id=g2.task_id, limits=BudgetLimits(tokens=1000, usd=0.01))

        rt.run(g1, budget=budget1, caller="arms/x", arm_id=ArmId("x"), actor="alice")
        rt.run(g2, budget=budget2, caller="arms/x", arm_id=ArmId("x"), actor="bob")

        alice_events = journal.read_by_actor("alice")
        bob_events = journal.read_by_actor("bob")

        # Implementation note.
        alice_tids = {e.task_id for e in alice_events if e.task_id}
        bob_tids = {e.task_id for e in bob_events if e.task_id}
        assert g1.task_id in alice_tids
        assert g1.task_id not in bob_tids
        assert g2.task_id in bob_tids
        assert g2.task_id not in alice_tids


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestJSONLRoundtrip:
    def test_actor_preserved_through_jsonl(self, tmp_path):
        from runtime.memory.journal import JSONLJournal

        path = tmp_path / "events.jsonl"
        j1 = JSONLJournal(path)
        tid = TaskId(uuid4())
        j1.write_task_started(
            tid,
            arm_id=ArmId("a"),
            actor="persistent_user",
            total_nodes=1,
            strategy="s",
        )

        # Implementation note.
        j2 = JSONLJournal(path)
        events = j2.read_by_actor("persistent_user")
        assert len(events) == 1
        assert events[0].actor == "persistent_user"
