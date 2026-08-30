"""Active single-demo forge (``SkillForge.forge_selected``).

When a person actively demonstrates a process to teach the agent, requiring the
autonomous ``min_hits`` (3) repetitions is absurd — one good demo should mint a
(provisional) skill now, and the existing evolution machinery refines it over
the next few self-runs. Safety is NOT bypassed: a macro over dangerous
primitives still goes to governed quarantine for human approval.
"""

from __future__ import annotations

from uuid import uuid4

from runtime.execution.suckers import Skill, SkillRegistry
from runtime.memory.journal import InMemoryJournal
from runtime.platform.models import (
    ArmId,
    ExecutionResult,
    Step,
    TaskId,
    ToolCall,
    Trajectory,
    TrajectoryOutcome,
)
from runtime.safety.recovery import SkillForge


def _step(idx: int, sucker: str) -> Step:
    call = ToolCall(caller="arms/code_arm", sucker_id=sucker, args={})
    return Step(
        step_id=idx,
        node_id=f"n{idx}",
        action=call,
        result=ExecutionResult(call_id=call.call_id, status="success", output={"ok": True}),
    )


def _traj(
    suckers: list[str],
    *,
    success: bool = True,
    degraded: bool = False,
) -> Trajectory:
    return Trajectory(
        task_id=TaskId(uuid4()),
        arm_id=ArmId("code_arm"),
        steps=[_step(i, s) for i, s in enumerate(suckers)],
        outcome=TrajectoryOutcome(success=success, degraded=degraded),
    )


def _registry(*names: str) -> SkillRegistry:
    r = SkillRegistry()
    for name in names:
        r.register(
            Skill(
                name=name,
                trusted_source=f"skill://public/{name}",
                handler=lambda **kw: {"ok": True},
            ),
            verify_tests=False,
        )
    return r


def test_single_demo_forges_bypassing_min_hits():
    """One 2-step demo mints a skill — the autonomous gate (min_hits=3) wouldn't."""
    reg = _registry("list_cwd", "count_words")
    journal = InMemoryJournal()
    forge = SkillForge(journal, reg)  # default ForgeConfig(min_hits=3)

    demo = _traj(["list_cwd", "count_words"])
    journal.write_trajectory(demo)

    # Autonomous path: a single sample is below min_hits → forges nothing.
    assert forge.propose() == []

    # Active path: the same single demo forges now.
    result = forge.forge_selected([demo])
    assert result.candidates_total == 1
    assert len(result.promoted) == 1
    assert result.quarantined == []
    # The forged skill is registered and reusable.
    assert reg.has(result.promoted[0])


def test_forge_selected_skips_trivial_single_step():
    """A <2-step "macro" is not a reusable skill — no candidate."""
    forge = SkillForge(InMemoryJournal(), _registry("list_cwd"))
    result = forge.forge_selected([_traj(["list_cwd"])])
    assert result.candidates_total == 0
    assert result.promoted == []


def test_forge_selected_skips_degraded_success():
    forge = SkillForge(InMemoryJournal(), _registry("list_cwd", "count_words"))

    result = forge.forge_selected([_traj(["list_cwd", "count_words"], degraded=True)])

    assert result.candidates_total == 0
    assert result.promoted == []


def test_forge_selected_quarantines_dangerous_macro():
    """Single demo over a dangerous primitive is NEVER auto-granted — the immune
    gate routes it to governed quarantine, same safety as the autonomous path."""
    reg = _registry("list_cwd", "exec_shell")  # exec_shell is is_dangerous_tool
    journal = InMemoryJournal()
    forge = SkillForge(journal, reg)

    result = forge.forge_selected([_traj(["list_cwd", "exec_shell"])])

    assert len(result.quarantined) == 1
    assert result.promoted == []
    # Not auto-granted into the registry.
    assert not reg.has(result.quarantined[0])

