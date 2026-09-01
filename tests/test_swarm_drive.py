"""run_swarm — the reusable mesh-swarm entry + its on_signal mesh tap."""

from __future__ import annotations

from typing import Any

from runtime.execution.swarm import drive
from runtime.safety.chromatophores import SignalBus


def test_run_swarm_runs_and_taps_mesh_signals(monkeypatch) -> None:
    captured: list[Any] = []

    class _FakeSwarm:
        def __init__(
            self, *, arm_pool, signal_bus, boids, journal, max_workers, skill_resources=None
        ):
            self._bus = signal_bus
            self.kw = (arm_pool, journal, max_workers)

        def run(self, graph, budget, *, split_strategy):
            # the swarm publishes Arm-to-Arm mesh chatter during a run
            self._bus.publish(
                "arm.handoff",
                {"from": "code_arm", "to": "text_arm"},
                publisher="swarm",
            )
            return f"result:{graph}:{split_strategy}"

    monkeypatch.setattr(drive, "SwarmRuntime", _FakeSwarm)
    bus = SignalBus()
    result = drive.run_swarm(
        "graph-X",
        "budget",
        arm_pool="pool",
        signal_bus=bus,
        max_workers=2,
        split_strategy="topo_layers",
        on_signal=captured.append,
    )
    assert result == "result:graph-X:topo_layers"
    assert len(captured) == 1  # the live mesh signal was tapped
    assert captured[0].topic == "arm.handoff"
    assert captured[0].payload == {"from": "code_arm", "to": "text_arm"}


def test_run_swarm_without_on_signal(monkeypatch) -> None:
    class _FakeSwarm:
        def __init__(self, **_kw):
            pass

        def run(self, graph, budget, *, split_strategy):
            return "ok"

    monkeypatch.setattr(drive, "SwarmRuntime", _FakeSwarm)
    assert drive.run_swarm("g", "b", arm_pool="p", signal_bus=SignalBus()) == "ok"


# ── build_arm_pool_from_registry (real arms, safe-filtered) ───────


def test_build_arm_pool_from_registry_excludes_dangerous_and_groups() -> None:
    from runtime.execution.suckers import Skill, SkillRegistry
    from runtime.execution.swarm.drive import build_arm_pool_from_registry

    reg = SkillRegistry()
    for name in ("read_file", "count_words", "list_cwd", "hash_text"):  # builtin, safe
        reg.register(
            Skill(
                name=name,
                trusted_source=f"skill://public/{name}",
                handler=lambda **kw: {"ok": True},
            ),
            verify_tests=False,
        )
    reg.register(
        Skill(
            name="exec_shell",
            trusted_source="skill://public/exec_shell",
            handler=lambda **kw: {},
            affinity=["shell", "dangerous"],
        ),
        verify_tests=False,
    )

    pool = build_arm_pool_from_registry(reg, runtime=None)
    allowed = {str(s) for arm in pool.all_arms() for s in arm.allowed_skills}
    assert {"read_file", "count_words", "list_cwd", "hash_text"} <= allowed
    assert "exec_shell" not in allowed  # dangerous filtered out by default
    assert len(pool) >= 1


def test_build_arm_pool_from_registry_include_dangerous_opt_in() -> None:
    from runtime.execution.suckers import Skill, SkillRegistry
    from runtime.execution.swarm.drive import build_arm_pool_from_registry

    reg = SkillRegistry()
    reg.register(
        Skill(
            name="exec_shell",
            trusted_source="skill://public/exec_shell",
            handler=lambda **kw: {},
            affinity=["dangerous"],
        ),
        verify_tests=False,
    )
    reg.register(
        Skill(
            name="read_file",
            trusted_source="skill://public/read_file",
            handler=lambda **kw: {"ok": True},
        ),
        verify_tests=False,
    )
    pool = build_arm_pool_from_registry(reg, runtime=None, include_dangerous=True)
    allowed = {str(s) for arm in pool.all_arms() for s in arm.allowed_skills}
    assert "exec_shell" in allowed  # opted in


# ── pick_for: specialist arm beats the atomic bypass (anti-funnel) ──


def test_pick_for_prefers_explicit_arm_over_atomic_bypass() -> None:
    # list_cwd is atomic, so ANY arm can_use it (the universal bypass). The arm
    # that EXPLICITLY lists it must still win — otherwise every atomic-skill
    # node funnels to arm index 0 and the swarm never parallelizes.
    from runtime.execution.arms import Arm, ArmPool
    from runtime.execution.swarm.runtime import _split_per_node
    from runtime.platform.models import (
        ArmId,
        BudgetSpec,
        SkillId,
        TaskGraph,
        TaskNode,
    )

    broad = Arm(  # index 0, does NOT list list_cwd
        arm_id=ArmId("broad_arm"),
        affinity=["x"],
        allowed_skills=[SkillId("some_market_skill")],
        runtime=None,
    )
    fs = Arm(  # the specialist
        arm_id=ArmId("fs_arm"),
        affinity=["fs"],
        allowed_skills=[SkillId("list_cwd")],
        runtime=None,
    )
    pool = ArmPool([broad, fs])
    g = TaskGraph(
        nodes=[TaskNode(node_id="n0", skill_ref=SkillId("list_cwd"), args_template={})],
        edges=[],
        budget=BudgetSpec(tokens=1000, usd=0.1),
        task_type="mixed",
    )
    arm = pool.pick_for(_split_per_node(g)[0])
    assert arm is not None and str(arm.arm_id) == "fs_arm"

