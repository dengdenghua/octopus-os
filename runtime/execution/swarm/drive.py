"""Reusable entry point to the boids/SignalBus mesh swarm.

``SwarmRuntime`` — the mesh swarm with boids arbitration and Arm-to-Arm
``SignalBus`` chatter — was wired only inside the ``echo run`` CLI, so the
project's most distinctive biomimetic capability was unreachable from any
other surface. This factors the boids + swarm construction into one callable
that any surface (a serve bridge, a skill) can drive, and surfaces the mesh's
live coordination via an ``on_signal`` callback so a streaming caller can show
the Arm-to-Arm chatter as it happens.

The caller owns the ``SignalBus`` and ``ArmPool`` (the arms must be built with
the same bus — that shared bus IS the mesh substrate — and which arms exist is
the caller's decision); this wires boids + ``SwarmRuntime`` + the signal tap
on top.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from runtime.execution.swarm import SwarmRuntime
from runtime.safety.chromatophores import BoidsArbitrator

# A swarm arm running shell / fs-write / GUI in parallel is a different safety
# posture than the read-only demo pool. Default to the project's own danger
# classification (is_dangerous_tool + the "dangerous" affinity, same gate the
# skill-forge uses) so the registry-derived pool stays safe unless a caller
# opts in.
_GENERIC = "generic"


def _is_dangerous(registry: Any, name: str) -> bool:
    from runtime.safety.approval.approval_gate import is_dangerous_tool

    if is_dangerous_tool(name):
        return True
    try:
        skill = registry.get(name)
    except Exception:  # noqa: BLE001 — missing/invalid skills fail elsewhere
        return False
    return "dangerous" in set(getattr(skill, "affinity", None) or [])


def build_arm_pool_from_registry(
    registry: Any,
    runtime: Any,
    *,
    signal_bus: Any = None,
    max_arms: int = 8,
    min_group_size: int = 2,
    include_dangerous: bool = False,
) -> Any:
    """Build a swarm ``ArmPool`` from the *registered* skills, grouped by the
    catalog's own taxonomy — replacing the hardcoded 3-arm demo pool.

    Each catalog group with at least ``min_group_size`` registered skills
    becomes one Arm (affinity = the group, allowed_skills = that group's
    registered skills); the largest groups win up to ``max_arms - 1`` and the
    rest fold into a generic arm. Dangerous skills are excluded unless
    ``include_dangerous`` — a swarm of parallel shell/write/GUI arms is a
    safety decision, not a default.
    """
    from runtime.execution.all_skills import skill_group
    from runtime.execution.arms import Arm, ArmPool
    from runtime.platform.models import ArmId, SkillId

    by_group: dict[str, list[str]] = {}
    for name in registry.all_names():
        if not include_dangerous and _is_dangerous(registry, name):
            continue
        group = skill_group(name) or _GENERIC
        by_group.setdefault(group, []).append(name)

    def _arm(arm_id: str, affinity: str, skills: list[str]) -> Any:
        return Arm(
            arm_id=ArmId(arm_id),
            affinity=[affinity],
            allowed_skills=[SkillId(s) for s in sorted(skills)],
            runtime=runtime,
            signal_bus=signal_bus,
        )

    arms: list[Any] = []
    generic: list[str] = list(by_group.pop(_GENERIC, []))
    ranked = sorted(by_group.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    for group, skills in ranked:
        if len(arms) < max(1, max_arms - 1) and len(skills) >= min_group_size:
            arms.append(_arm(f"{group}_arm", group, skills))
        else:
            generic.extend(skills)
    if generic:
        arms.append(_arm("generic_arm", "general", generic))
    return ArmPool(arms)


def run_swarm(
    graph: Any,
    budget: Any,
    *,
    arm_pool: Any,
    signal_bus: Any,
    journal: Any = None,
    max_workers: int = 4,
    split_strategy: str = "per_node",
    on_signal: Callable[[Any], None] | None = None,
    registry: Any = None,
) -> Any:
    """Drive ``graph`` through the mesh swarm and return its ``SwarmResult``.

    ``arm_pool`` must have been built with ``signal_bus`` so arms and swarm
    share one bus (= the mesh). ``on_signal`` receives every ``SignalEvent``
    the swarm publishes — the live Arm-to-Arm mesh coordination. ``registry``,
    when given, lets the swarm read each skill's declared exclusive resource
    (ADR-010 Phase 2) so e.g. two parallel desktop ops serialise on the screen.
    """
    if on_signal is not None:
        signal_bus.subscribe("*", on_signal)

    skill_resources = None
    if registry is not None:

        def skill_resources(skill_ref: str) -> list[str]:
            try:
                res = registry.get(skill_ref).exclusive_resource
            except Exception:
                return []
            return [res] if res else []

    swarm = SwarmRuntime(
        arm_pool=arm_pool,
        signal_bus=signal_bus,
        boids=BoidsArbitrator(signal_bus=signal_bus),
        journal=journal,
        max_workers=max_workers,
        skill_resources=skill_resources,
    )
    return swarm.run(graph, budget, split_strategy=split_strategy)
