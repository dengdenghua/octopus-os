"""Implementation note."""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.core.graph_runtime import GraphRuntime
from runtime.core.nerves import (
    AgentAdded,
    AgentRemoved,
    SkillRegistered,
    TypedEventBus,
)
from runtime.execution.agents import Agent, AgentRegistry
from runtime.execution.arms.base import ArmPool, Worker
from runtime.execution.suckers import Skill, SkillRegistry
from runtime.platform.models import ArmId, SkillId

# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestSkillRegistryBackwardCompat:
    def test_no_bus_register_unchanged(self):
        reg = SkillRegistry()  # Implementation note.
        skill = Skill(
            name="x",
            description="",
            trusted_source="skill://public/x",
            handler=lambda **_kw: {},
        )
        reg.register(skill, verify_tests=False)
        assert reg.has("x")

    def test_registered_still_queryable(self):
        reg = SkillRegistry()
        for n in ["a", "b", "c"]:
            reg.register(
                Skill(
                    name=n,
                    trusted_source=f"skill://public/{n}",
                    handler=lambda **_kw: {},
                ),
                verify_tests=False,
            )
        assert set(reg.all_names()) == {"a", "b", "c"}


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestSkillRegistryPublishes:
    def test_publishes_on_register(self):
        bus = TypedEventBus()
        events: list[SkillRegistered] = []
        bus.subscribe(SkillRegistered, lambda e: events.append(e))

        reg = SkillRegistry(event_bus=bus)
        reg.register(
            Skill(
                name="my_skill",
                description="test",
                trusted_source="skill://public/my_skill",
                handler=lambda **_kw: {},
                affinity=["io"],
            ),
            verify_tests=False,
        )

        assert len(events) == 1
        assert events[0].skill_name == "my_skill"
        assert events[0].trusted_source == "skill://public/my_skill"
        assert events[0].forged is False

    def test_forged_flag_when_affinity_contains_forged(self):
        bus = TypedEventBus()
        events: list[SkillRegistered] = []
        bus.subscribe(SkillRegistered, lambda e: events.append(e))

        reg = SkillRegistry(event_bus=bus)
        reg.register(
            Skill(
                name="forged_xxx",
                trusted_source="skill://forged/abc",
                handler=lambda **_kw: {},
                affinity=["forged"],
            ),
            verify_tests=False,
        )
        assert events[0].forged is True

    def test_bus_exception_does_not_break_register(self):
        """Implementation note."""

        class _BadBus:
            def publish(self, ev):
                raise RuntimeError("bus down")

        reg = SkillRegistry(event_bus=_BadBus())
        reg.register(
            Skill(
                name="x",
                trusted_source="skill://public/x",
                handler=lambda **_kw: {},
            ),
            verify_tests=False,
        )
        # Implementation note.
        assert reg.has("x")


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class _FakeExecutor:
    journal = None


def _rt():
    return GraphRuntime(executor=_FakeExecutor(), journal=None)


def _mk_agent(agent_id: str, display_name: str = "") -> Agent:
    rt = _rt()
    arm = Worker(
        arm_id=ArmId("a"),
        affinity=[],
        allowed_skills=[SkillId("read_file")],
        runtime=rt,
    )
    return Agent(
        agent_id=agent_id,
        display_name=display_name or agent_id,
        description="",
        soul="",
        arms=ArmPool([arm]),
    )


class TestAgentRegistryPublishes:
    def test_publishes_on_register(self):
        bus = TypedEventBus()
        events: list[AgentAdded] = []
        bus.subscribe(AgentAdded, lambda e: events.append(e))

        reg = AgentRegistry(event_bus=bus)
        reg.register(_mk_agent("coder", display_name="Coder"))

        assert len(events) == 1
        assert events[0].agent_id == "coder"
        assert events[0].display_name == "Coder"

    def test_publishes_on_remove(self):
        bus = TypedEventBus()
        removed: list[AgentRemoved] = []
        bus.subscribe(AgentRemoved, lambda e: removed.append(e))

        reg = AgentRegistry(event_bus=bus)
        reg.register(_mk_agent("a"))
        reg.remove("a")

        assert len(removed) == 1
        assert removed[0].agent_id == "a"

    def test_remove_nonexistent_no_event(self):
        bus = TypedEventBus()
        removed: list[AgentRemoved] = []
        bus.subscribe(AgentRemoved, lambda e: removed.append(e))

        reg = AgentRegistry(event_bus=bus)
        reg.remove("ghost")
        assert removed == []

    def test_backward_compat_no_bus(self):
        reg = AgentRegistry()  # Implementation note.
        reg.register(_mk_agent("x"))
        assert reg.has("x")

    def test_bus_failure_does_not_break_register(self):
        class _BadBus:
            def publish(self, ev):
                raise RuntimeError("boom")

        reg = AgentRegistry(event_bus=_BadBus())
        reg.register(_mk_agent("x"))
        assert reg.has("x")


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestForgeTriggersEvent:
    """Implementation note."""

    @pytest.mark.xfail(
        reason=(
            "bugfix demo trail includes exec_shell; SkillForge danger "
            "gate (runtime.safety.approval.approval_gate.is_dangerous_tool) "
            "refuses promotion of forged composites that wrap dangerous "
            "skills. Expected after the danger-gate hardening; pair this "
            "test with test_evolution_demo's xfailed promotion checks."
        ),
        strict=False,
        raises=AssertionError,
    )
    def test_forge_promote_publishes(self, tmp_path: Path):
        """Implementation note."""
        import shutil

        if shutil.which("git") is None:
            pytest.skip("git not on PATH")

        from demos.bugfix_demo import build_bugfix_graph, setup_buggy_project
        from runtime.execution.suckers.builtins import register_all
        from runtime.execution.suckers.write_skills import register_exec_skill
        from runtime.execution.tool_engine import ToolExecutor
        from runtime.memory.journal import JSONLJournal
        from runtime.platform.models import Budget, BudgetLimits
        from runtime.safety.auth import TrustEngine
        from runtime.safety.recovery import ForgeConfig, SkillForge

        bus = TypedEventBus()
        events: list[SkillRegistered] = []
        bus.subscribe(SkillRegistered, lambda e: events.append(e))

        # Implementation note.
        reg = SkillRegistry(event_bus=bus)
        register_all(reg)
        register_exec_skill(reg)

        initial_count = len(events)  # Implementation note.

        # Implementation note.
        journal_path = tmp_path / "events.jsonl"
        journal = JSONLJournal(journal_path)
        executor = ToolExecutor(
            registry=reg,
            immunity=TrustEngine(trusted_sources=["skill://public/*"]),
            journal=journal,
        )
        runtime = GraphRuntime(executor=executor, journal=journal)
        for i in range(3):
            proj = setup_buggy_project(tmp_path / f"proj_{i}")
            g = build_bugfix_graph(proj)
            b = Budget(
                task_id=g.task_id,
                limits=BudgetLimits(tokens=10_000, usd=0.10),
            )
            runtime.run(g, budget=b, caller="test", arm_id=ArmId("t"))

        # Implementation note.
        forge = SkillForge(
            journal=journal,
            registry=reg,
            config=ForgeConfig(min_hits=2, min_success_rate=0.5),
        )
        result = forge.run()

        # Implementation note.
        assert len(result.promoted) >= 1
        new_events = events[initial_count:]
        forged_events = [e for e in new_events if e.forged]
        assert len(forged_events) >= 1
        # Implementation note.
        assert forged_events[0].skill_name in result.promoted
