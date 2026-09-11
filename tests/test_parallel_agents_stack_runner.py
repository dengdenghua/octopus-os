"""Implementation note."""

from __future__ import annotations

import threading
import time

import pytest

from runtime.core.cerebrum import StaticPlanner
from runtime.core.cerebrum.planner import Rule
from runtime.core.graph_runtime import GraphRuntime
from runtime.execution.agents import Agent, AgentRegistry
from runtime.execution.arms.base import ArmPool
from runtime.execution.arms.presets import make_web_read_arm
from runtime.execution.parallel_agents import (
    DispatchTaskInput,
    ParallelAgentOrchestrator,
    make_stack_subagent_runner,
)
from runtime.execution.suckers import SkillRegistry
from runtime.execution.suckers.builtins import register_all
from runtime.execution.tool_engine import ToolExecutor
from runtime.memory.journal import InMemoryJournal
from runtime.platform.models import BudgetSpec, SkillId, Trajectory, TrajectoryOutcome
from runtime.platform.process.session import current_session
from runtime.safety.auth import TrustEngine

# ═══════════════════════════════════════════════════════════════
# fixtures
# ═══════════════════════════════════════════════════════════════


def _build_stack():
    """Implementation note."""
    journal = InMemoryJournal()
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
            ),
        ],
        default_budget=BudgetSpec(tokens=10_000, usd=0.10),
        fallback_skill=SkillId("list_cwd"),
    )

    class _Stack:
        pass

    s = _Stack()
    s.planner = planner
    s.runtime = runtime
    s.registry = registry
    s.journal = journal
    return s


class _BoomPlanner:
    """Implementation note."""

    def plan(self, intent, **_kw):
        from runtime.core.cerebrum.planner import PlannerError

        raise PlannerError("boom simulated")


def _build_registry():
    runtime = GraphRuntime(
        executor=ToolExecutor(
            registry=SkillRegistry(),
            immunity=TrustEngine(),
            journal=InMemoryJournal(),
        ),
        journal=InMemoryJournal(),
    )
    reg = AgentRegistry()
    reg.register(
        Agent(
            agent_id="scout",
            display_name="Scout",
            description="test agent",
            soul="You are a scout.",
            arms=ArmPool([make_web_read_arm(runtime)]),
            icon="🔭",
            model="test-model",
        )
    )
    return reg


# ═══════════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════════


class TestRunnerHappyPath:
    def test_runs_plan_and_summarizes(self):
        stack = _build_stack()
        runner = make_stack_subagent_runner(stack=stack)
        out = runner(
            "list files",
            subagent_name="general-purpose",
            context={},
            cancel_event=threading.Event(),
        )
        assert isinstance(out, str)
        # Implementation note.
        assert "OK" in out
        assert "list_cwd" in out

    def test_emits_tool_events_from_trajectory(self):
        stack = _build_stack()
        events: list[dict] = []
        runner = make_stack_subagent_runner(stack=stack)
        out = runner(
            "list files",
            subagent_name="general-purpose",
            context={"emit_tool_event": lambda **event: events.append(event)},
            cancel_event=threading.Event(),
        )

        assert "OK" in out
        assert events
        assert events[0]["tool_name"] == "list_cwd"
        assert events[0]["status"] == "completed"
        assert "output_preview" in events[0]

    def test_binds_runtime_session_metadata_and_file_owner(self):
        stack = _build_stack()
        captured: dict = {}

        class _Runtime:
            def run(self, graph, *, budget, caller, arm_id, actor):
                session = current_session()
                captured["metadata"] = session.metadata if session is not None else None
                captured["thread_id"] = session.thread_id if session is not None else None
                captured["actor"] = actor
                return Trajectory(
                    task_id=graph.task_id,
                    arm_id=arm_id,
                    outcome=TrajectoryOutcome(success=True),
                )

        stack.runtime = _Runtime()
        metadata = {"workspace_path": "C:/workspace"}
        runner = make_stack_subagent_runner(stack=stack)
        out = runner(
            "list files",
            subagent_name="general-purpose",
            context={
                "thread_id": "thread-1",
                "file_write_owner": "task-a",
                "runtime_session_metadata": metadata,
            },
            cancel_event=threading.Event(),
        )

        assert "OK" in out
        assert captured["metadata"] is metadata
        assert captured["thread_id"] == "thread-1"
        assert captured["actor"] == "task-a"

    def test_via_orchestrator_end_to_end(self):
        stack = _build_stack()
        orch = ParallelAgentOrchestrator(
            max_concurrency=2,
            task_runner=make_stack_subagent_runner(stack=stack),
        )
        try:
            batch = orch.dispatch(
                [
                    DispatchTaskInput(description="list them", subagent_name="x"),
                    DispatchTaskInput(description="list others", subagent_name="y"),
                ]
            )
            for _ in range(200):
                snap = orch.get_batch(batch.batch_id)
                if snap.status == "completed":
                    break
                time.sleep(0.02)
            snap = orch.get_batch(batch.batch_id)
            assert snap.status == "completed"
            assert snap.completed_tasks == 2
            for r in snap.results:
                assert r.status == "completed"
                assert "list_cwd" in (r.result or "")
        finally:
            orch.shutdown(wait=False)


# ═══════════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════════


class TestPlannerError:
    def test_planner_error_returns_message_not_failed(self):
        """Implementation note."""

        class _S:
            pass

        s = _S()
        s.planner = _BoomPlanner()
        # Implementation note.
        s.runtime = object()

        runner = make_stack_subagent_runner(stack=s)
        out = runner("anything", subagent_name="x", context={})
        assert out.startswith("[planner error]")
        assert "boom simulated" in out


# ═══════════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════════


class TestAgentRegistryLookup:
    def test_unknown_subagent_name_falls_back_to_free_mode(self):
        stack = _build_stack()
        reg = _build_registry()
        runner = make_stack_subagent_runner(
            stack=stack,
            agent_registry=reg,
        )
        # Implementation note.
        out = runner("list", subagent_name="ghost", context={})
        assert "OK" in out

    def test_known_subagent_name_injects_soul_and_model(self):
        """Implementation note."""
        stack = _build_stack()
        real_planner = stack.planner  # Implementation note.
        reg = _build_registry()

        class _SpyPlanner:
            captured: dict = {}

            def plan(self, intent, *, allowed_skills=None, soul=None, model=None):
                _SpyPlanner.captured = {
                    "allowed_skills": allowed_skills,
                    "soul": soul,
                    "model": model,
                }
                return real_planner.plan(
                    intent,
                    allowed_skills=allowed_skills,
                )

        stack.planner = _SpyPlanner()  # type: ignore[assignment]

        runner = make_stack_subagent_runner(
            stack=stack,
            agent_registry=reg,
        )
        runner("list", subagent_name="scout", context={})
        assert _SpyPlanner.captured.get("soul") == "You are a scout."
        assert _SpyPlanner.captured.get("model") == "test-model"

    def test_context_model_overrides_agent_model(self):
        stack = _build_stack()
        real_planner = stack.planner
        reg = _build_registry()

        class _SpyPlanner:
            captured: dict = {}

            def plan(self, intent, *, allowed_skills=None, soul=None, model=None):
                _SpyPlanner.captured = {
                    "model": model,
                    "soul": soul,
                }
                return real_planner.plan(
                    intent,
                    allowed_skills=allowed_skills,
                )

        stack.planner = _SpyPlanner()  # type: ignore[assignment]

        runner = make_stack_subagent_runner(
            stack=stack,
            agent_registry=reg,
        )
        runner(
            "list",
            subagent_name="scout",
            context={"model_name": "kimi-k2.5"},
        )
        assert _SpyPlanner.captured.get("model") == "kimi-k2.5"


# ═══════════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════════


class TestCancelAndBudget:
    def test_cancel_event_preempts_before_plan(self):
        stack = _build_stack()
        runner = make_stack_subagent_runner(stack=stack)
        ev = threading.Event()
        ev.set()
        out = runner("x", subagent_name="y", context={}, cancel_event=ev)
        assert out == ""

    def test_sibling_tasks_get_independent_budgets(self):
        """Implementation note."""
        stack = _build_stack()
        orch = ParallelAgentOrchestrator(
            max_concurrency=4,
            task_runner=make_stack_subagent_runner(
                stack=stack,
                default_tokens=1_000,
                default_usd=0.01,
            ),
        )
        try:
            batch = orch.dispatch([DispatchTaskInput(description=f"list {i}") for i in range(3)])
            for _ in range(200):
                s = orch.get_batch(batch.batch_id)
                if s.status == "completed":
                    break
                time.sleep(0.02)
            s = orch.get_batch(batch.batch_id)
            assert s.completed_tasks == 3
        finally:
            orch.shutdown(wait=False)


# ═══════════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════════


class TestConstruct:
    def test_none_stack_raises(self):
        with pytest.raises(ValueError, match="stack"):
            make_stack_subagent_runner(stack=None)

    def test_missing_planner_raises(self):
        class _Bad:
            planner = None
            runtime = object()

        with pytest.raises(ValueError, match="planner"):
            make_stack_subagent_runner(stack=_Bad())

    def test_missing_runtime_raises(self):
        class _Bad:
            planner = object()
            runtime = None

        with pytest.raises(ValueError, match="runtime"):
            make_stack_subagent_runner(stack=_Bad())


class TestInjectionTaintThreading:
    """The spawning parent's prompt-injection taint must survive the hop into
    the sub-agent: ``_inherited_injection_taint`` in the call context has to be
    copied into the sub-agent intent's ``user_context`` (the allowlist of keys
    threaded through is narrow, so this is an explicit, security-critical
    member). The sub-agent's react loop honors it at start."""

    def test_inherited_taint_lands_in_subagent_user_context(self):
        stack = _build_stack()
        real_planner = stack.planner
        reg = _build_registry()

        class _SpyPlanner:
            captured: dict = {}

            def plan(self, intent, *, allowed_skills=None, soul=None, model=None):
                _SpyPlanner.captured = dict(intent.user_context)
                return real_planner.plan(intent, allowed_skills=allowed_skills)

        stack.planner = _SpyPlanner()  # type: ignore[assignment]
        runner = make_stack_subagent_runner(stack=stack, agent_registry=reg)
        runner(
            "list",
            subagent_name="scout",
            context={"_inherited_injection_taint": "high"},
        )
        assert _SpyPlanner.captured.get("_inherited_injection_taint") == "high"

    def test_clean_context_leaves_no_taint_key(self):
        stack = _build_stack()
        real_planner = stack.planner
        reg = _build_registry()

        class _SpyPlanner:
            captured: dict = {}

            def plan(self, intent, *, allowed_skills=None, soul=None, model=None):
                _SpyPlanner.captured = dict(intent.user_context)
                return real_planner.plan(intent, allowed_skills=allowed_skills)

        stack.planner = _SpyPlanner()  # type: ignore[assignment]
        runner = make_stack_subagent_runner(stack=stack, agent_registry=reg)
        runner("list", subagent_name="scout", context={})
        assert "_inherited_injection_taint" not in _SpyPlanner.captured
