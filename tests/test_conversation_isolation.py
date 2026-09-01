"""Implementation note."""

from __future__ import annotations

import threading
from pathlib import Path
from uuid import uuid4

import pytest
from runtime.memory.journal import (
    InMemoryJournal,
    JSONLJournal,
    current_agent_id,
    current_conversation_id,
    journal_context,
)
from runtime.memory.journal.journal import Step, TrajectoryEvent
from runtime.platform.models import (
    ArmId,
    ExecutionResult,
    TaskId,
    ToolCall,
    Trajectory,
    TrajectoryOutcome,
)

# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestJournalContext:
    def test_default_none(self):
        assert current_agent_id() is None
        assert current_conversation_id() is None

    def test_sets_within_block(self):
        with journal_context(agent_id="coder", conversation_id="conv-1"):
            assert current_agent_id() == "coder"
            assert current_conversation_id() == "conv-1"
        # Implementation note.
        assert current_agent_id() is None
        assert current_conversation_id() is None

    def test_nested_blocks_restore(self):
        with journal_context(agent_id="outer", conversation_id="c1"):
            assert current_agent_id() == "outer"
            with journal_context(agent_id="inner", conversation_id="c2"):
                assert current_agent_id() == "inner"
                assert current_conversation_id() == "c2"
            # Implementation note.
            assert current_agent_id() == "outer"
            assert current_conversation_id() == "c1"

    def test_thread_isolation(self):
        """Implementation note."""
        import contextvars

        values = []

        def worker():
            ctx = contextvars.copy_context()

            def _inner():
                with journal_context(agent_id="worker"):
                    values.append(current_agent_id())

            ctx.run(_inner)

        with journal_context(agent_id="main"):
            t = threading.Thread(target=worker)
            t.start()
            t.join()
            # Implementation note.
            assert current_agent_id() == "main"

        assert values == ["worker"]


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


def _mk_step(step_id: int = 0, node_id: str = "n0") -> Step:
    call = ToolCall(caller="test", sucker_id="read_file", args={})
    result = ExecutionResult(
        call_id=call.call_id,
        status="success",
        output={"x": 1},
    )
    return Step(
        step_id=step_id,
        node_id=node_id,
        action=call,
        result=result,
    )


class TestAutoInjection:
    def test_write_step_picks_up_context(self):
        j = InMemoryJournal()
        task_id = TaskId(uuid4())
        arm_id = ArmId("a")

        with journal_context(agent_id="coder", conversation_id="c1"):
            j.write_step(task_id, arm_id, _mk_step())

        events = j.read_all()
        assert len(events) == 1
        assert events[0].agent_id == "coder"
        assert events[0].conversation_id == "c1"

    def test_write_trajectory_picks_up_context(self):
        j = InMemoryJournal()
        traj = Trajectory(
            task_id=TaskId(uuid4()),
            arm_id=ArmId("a"),
            steps=[_mk_step()],
            outcome=TrajectoryOutcome(success=True),
        )
        with journal_context(agent_id="ecommerce_mind", conversation_id="c9"):
            j.write_trajectory(traj)
        events = j.read_all()
        t_event = next(e for e in events if isinstance(e, TrajectoryEvent))
        assert t_event.agent_id == "ecommerce_mind"
        assert t_event.conversation_id == "c9"

    def test_outside_context_leaves_none(self):
        j = InMemoryJournal()
        j.write_step(TaskId(uuid4()), ArmId("a"), _mk_step())
        e = j.read_all()[0]
        assert e.agent_id is None
        assert e.conversation_id is None


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestJournalQueries:
    def _seed(self):
        j = InMemoryJournal()
        # Implementation note.
        scenarios = [
            ("coder", "c1"),
            ("coder", "c1"),
            ("coder", "c2"),
            ("shopify", "c3"),
            ("shopify", "c3"),
        ]
        for aid, cid in scenarios:
            with journal_context(agent_id=aid, conversation_id=cid):
                j.write_step(TaskId(uuid4()), ArmId("a"), _mk_step())
        return j

    def test_read_by_agent(self):
        j = self._seed()
        coder_events = j.read_by_agent("coder")
        assert len(coder_events) == 3
        assert all(e.agent_id == "coder" for e in coder_events)

    def test_read_by_conversation(self):
        j = self._seed()
        c1_events = j.read_by_conversation("c1")
        assert len(c1_events) == 2

    def test_list_conversations_all(self):
        j = self._seed()
        assert set(j.list_conversations()) == {"c1", "c2", "c3"}

    def test_list_conversations_filtered(self):
        j = self._seed()
        coder_convs = j.list_conversations(agent_id="coder")
        assert set(coder_convs) == {"c1", "c2"}

    def test_list_conversations_empty(self):
        """Implementation note."""
        j = InMemoryJournal()
        j.write_step(TaskId(uuid4()), ArmId("a"), _mk_step())
        assert j.list_conversations() == []

    def test_jsonl_journal_also_works(self, tmp_path: Path):
        """Implementation note."""
        j = JSONLJournal(tmp_path / "events.jsonl")
        with journal_context(agent_id="coder", conversation_id="c1"):
            j.write_step(TaskId(uuid4()), ArmId("a"), _mk_step())
        # Implementation note.
        j2 = JSONLJournal(tmp_path / "events.jsonl")
        events = j2.read_all()
        assert len(events) == 1
        assert events[0].agent_id == "coder"
        assert events[0].conversation_id == "c1"


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def _build_stack(tmp_path: Path):
    from runtime.core.cerebrum import StaticPlanner
    from runtime.core.cerebrum.planner import Rule
    from runtime.core.graph_runtime import GraphRuntime
    from runtime.execution.suckers import SkillRegistry
    from runtime.execution.suckers.builtins import register_all
    from runtime.execution.tool_engine import ToolExecutor
    from runtime.platform.models import BudgetSpec, SkillId
    from runtime.safety.auth import TrustEngine

    journal = JSONLJournal(tmp_path / "events.jsonl")
    registry = SkillRegistry()
    register_all(registry)
    executor = ToolExecutor(
        registry=registry,
        immunity=TrustEngine(trusted_sources=["skill://public/*"]),
        journal=journal,
    )
    runtime = GraphRuntime(executor=executor, journal=journal)
    planner = StaticPlanner(
        rules=[
            Rule(
                name="default",
                intent_types=["task"],
                skill_sequence=[SkillId("list_cwd")],
            )
        ],
        default_budget=BudgetSpec(tokens=10_000, usd=0.10),
        fallback_skill=SkillId("list_cwd"),
    )

    class _S:
        pass

    s = _S()
    s.planner = planner
    s.runtime = runtime
    s.registry = registry
    s.journal = journal
    return s


class TestChatAutoConversationId:
    def test_response_carries_conversation_id(self, tmp_path: Path):
        from runtime.sensing.gateway.openai_gateway_router import create_openai_router

        stack = _build_stack(tmp_path)
        app = FastAPI()
        app.include_router(create_openai_router(stack))

        r = TestClient(app).post(
            "/v1/chat/completions",
            json={
                "model": "x",
                "messages": [{"role": "user", "content": "list files"}],
            },
        )
        assert r.status_code == 200
        cid = r.json()["echo"]["conversation_id"]
        assert isinstance(cid, str)
        assert len(cid) > 0

    def test_same_conversation_id_accumulates(self, tmp_path: Path):
        from runtime.sensing.gateway.openai_gateway_router import create_openai_router

        stack = _build_stack(tmp_path)
        app = FastAPI()
        app.include_router(create_openai_router(stack))
        client = TestClient(app)

        # Implementation note.
        r1 = client.post(
            "/v1/chat/completions",
            json={"model": "x", "messages": [{"role": "user", "content": "list files"}]},
        )
        cid = r1.json()["echo"]["conversation_id"]

        # Implementation note.
        r2 = client.post(
            "/v1/chat/completions",
            json={
                "model": "x",
                "messages": [{"role": "user", "content": "list again"}],
                "conversation_id": cid,
            },
        )
        assert r2.json()["echo"]["conversation_id"] == cid

        # Implementation note.
        events = stack.journal.read_by_conversation(cid)
        assert len(events) >= 2  # Implementation note.

    def test_different_conversations_isolated(self, tmp_path: Path):
        from runtime.sensing.gateway.openai_gateway_router import create_openai_router

        stack = _build_stack(tmp_path)
        app = FastAPI()
        app.include_router(create_openai_router(stack))
        client = TestClient(app)

        r1 = client.post(
            "/v1/chat/completions",
            json={
                "model": "x",
                "messages": [{"role": "user", "content": "a"}],
            },
        )
        r2 = client.post(
            "/v1/chat/completions",
            json={
                "model": "x",
                "messages": [{"role": "user", "content": "b"}],
            },
        )
        cid1 = r1.json()["echo"]["conversation_id"]
        cid2 = r2.json()["echo"]["conversation_id"]
        assert cid1 != cid2

        c1_events = stack.journal.read_by_conversation(cid1)
        c2_events = stack.journal.read_by_conversation(cid2)
        # Implementation note.
        assert all(e.conversation_id == cid1 for e in c1_events)
        assert all(e.conversation_id == cid2 for e in c2_events)

    def test_invalid_conversation_id_rejected(self, tmp_path: Path):
        from runtime.sensing.gateway.openai_gateway_router import create_openai_router

        stack = _build_stack(tmp_path)
        app = FastAPI()
        app.include_router(create_openai_router(stack))
        r = TestClient(app).post(
            "/v1/chat/completions",
            json={
                "model": "x",
                "messages": [{"role": "user", "content": "x"}],
                "conversation_id": "",
            },
        )
        assert r.status_code == 400


class TestChatAgentStampsJournal:
    def test_events_tagged_with_agent_id(self, tmp_path: Path):
        from runtime.execution.agents import AgentRegistry, make_general_agent
        from runtime.sensing.gateway.openai_gateway_router import create_openai_router

        stack = _build_stack(tmp_path)
        reg = AgentRegistry()
        from runtime.core.graph_runtime import GraphRuntime

        class _FakeExecutor:
            journal = None

        reg.register(
            make_general_agent(
                GraphRuntime(
                    executor=_FakeExecutor(),
                    journal=None,
                )
            )
        )

        app = FastAPI()
        app.include_router(
            create_openai_router(
                stack,
                agent_registry=reg,
            )
        )
        r = TestClient(app).post(
            "/v1/chat/completions",
            json={
                "model": "x",
                "messages": [{"role": "user", "content": "list"}],
                "agent": "general",
            },
        )
        assert r.status_code == 200

        events = list(stack.journal.read_by_agent("general"))
        assert events, "no events tagged with agent_id=general"


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestConversationEndpoints:
    def _app(self, journal, registry=None):
        from runtime.execution.agents import AgentRegistry
        from runtime.sensing.gateway.agents_router import create_agents_router

        app = FastAPI()
        app.include_router(
            create_agents_router(
                registry=registry or AgentRegistry(),
                journal=journal,
            )
        )
        return app

    def test_list_empty(self, tmp_path: Path):
        j = JSONLJournal(tmp_path / "events.jsonl")
        app = self._app(j)
        r = TestClient(app).get("/api/conversations")
        assert r.status_code == 200
        assert r.json() == []

    def test_list_returns_conversations(self, tmp_path: Path):
        j = JSONLJournal(tmp_path / "events.jsonl")
        with journal_context(agent_id="coder", conversation_id="c1"):
            j.write_step(TaskId(uuid4()), ArmId("a"), _mk_step())
        with journal_context(agent_id="shopify", conversation_id="c2"):
            j.write_step(TaskId(uuid4()), ArmId("a"), _mk_step())

        app = self._app(j)
        data = TestClient(app).get("/api/conversations").json()
        cids = {d["conversation_id"] for d in data}
        assert cids == {"c1", "c2"}
        for d in data:
            assert d["event_count"] >= 1
            assert "first_ts" in d and "last_ts" in d

    def test_filter_by_agent(self, tmp_path: Path):
        j = JSONLJournal(tmp_path / "events.jsonl")
        with journal_context(agent_id="coder", conversation_id="c1"):
            j.write_step(TaskId(uuid4()), ArmId("a"), _mk_step())
        with journal_context(agent_id="shopify", conversation_id="c2"):
            j.write_step(TaskId(uuid4()), ArmId("a"), _mk_step())

        app = self._app(j)
        data = TestClient(app).get("/api/conversations?agent=coder").json()
        assert len(data) == 1
        assert data[0]["conversation_id"] == "c1"

    def test_events_endpoint(self, tmp_path: Path):
        j = JSONLJournal(tmp_path / "events.jsonl")
        with journal_context(agent_id="coder", conversation_id="mycid"):
            j.write_step(TaskId(uuid4()), ArmId("a"), _mk_step())
            j.write_step(TaskId(uuid4()), ArmId("a"), _mk_step(step_id=1, node_id="n1"))

        app = self._app(j)
        r = TestClient(app).get("/api/conversations/mycid/events")
        assert r.status_code == 200
        data = r.json()
        assert data["conversation_id"] == "mycid"
        assert data["total"] == 2
        assert len(data["events"]) == 2
        assert all(e["agent_id"] == "coder" for e in data["events"])

    def test_events_unknown_conversation_404(self, tmp_path: Path):
        j = JSONLJournal(tmp_path / "events.jsonl")
        app = self._app(j)
        r = TestClient(app).get("/api/conversations/ghost/events")
        assert r.status_code == 404
