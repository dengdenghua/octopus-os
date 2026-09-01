"""ADR-010 · swarm resource-contention wiring.

The BoidsArbitrator was complete but had no production caller — the swarm
pre-splits subgraphs per_node, so there was no runtime contention to
arbitrate. ADR-010 wires it at the dispatch boundary (`_run_one`) for
assignments that declare ``exclusive_resources``. These pin the claim →
serialise-on-lose → release loop, and that the default (no declared
resources) is a strict no-op so existing flows are unchanged.
"""

from __future__ import annotations

import threading
import time

from runtime.execution.arms.base import ArmPool
from runtime.execution.swarm.runtime import SwarmRuntime
from runtime.platform.models import ArmId
from runtime.safety.chromatophores.boids import BoidsArbitrator, ResourceClaim


def _rt(*, boids: BoidsArbitrator | None) -> SwarmRuntime:
    return SwarmRuntime(arm_pool=ArmPool([]), boids=boids, max_workers=2)


def _holds(rt: SwarmRuntime, uri: str) -> bool:
    return any(c.resource_uri == uri for c in rt._boids.active_claims())  # noqa: SLF001


# --------------------------------------------------------------------------- #
# No-op guarantees (backward compatibility)                                   #
# --------------------------------------------------------------------------- #


def test_no_declared_resources_is_noop():
    rt = _rt(boids=BoidsArbitrator())
    assert rt._claim_resources(ArmId("a"), []) == []  # noqa: SLF001
    assert rt._boids.active_claims() == []  # noqa: SLF001


def test_no_arbitrator_is_noop():
    rt = _rt(boids=None)
    # Even with declared resources, no arbitrator ⇒ nothing claimed.
    assert rt._claim_resources(ArmId("a"), ["device:x"]) == []  # noqa: SLF001


# --------------------------------------------------------------------------- #
# Claim / release round-trip                                                  #
# --------------------------------------------------------------------------- #


def test_claim_then_release_roundtrip():
    rt = _rt(boids=BoidsArbitrator())
    held = rt._claim_resources(ArmId("a"), ["device:desktop"])  # noqa: SLF001
    assert held == ["device:desktop"]
    assert _holds(rt, "device:desktop")
    rt._release_resources(ArmId("a"), held)  # noqa: SLF001
    assert not _holds(rt, "device:desktop")


def test_readonly_resources_coexist():
    rt = _rt(boids=BoidsArbitrator())
    a = rt._claim_resources(ArmId("a"), ["file:/data.csv:read"])  # noqa: SLF001
    b = rt._claim_resources(ArmId("b"), ["file:/data.csv:read"])  # noqa: SLF001
    assert a == ["file:/data.csv:read"]
    assert b == ["file:/data.csv:read"]  # readonly ⇒ both coexist


# --------------------------------------------------------------------------- #
# Serialisation on contention                                                 #
# --------------------------------------------------------------------------- #


def test_claim_waits_until_holder_releases():
    rt = _rt(boids=BoidsArbitrator())
    # Arm A holds the resource up front.
    rt._boids.arbitrate(  # noqa: SLF001
        ResourceClaim(arm_id=ArmId("A"), resource_uri="device:desktop", ttl_ms=600_000)
    )
    released_at: list[float] = []

    def _release_soon() -> None:
        time.sleep(0.1)
        rt._boids.release(ArmId("A"), "device:desktop")  # noqa: SLF001
        released_at.append(time.monotonic())

    worker = threading.Thread(target=_release_soon)
    worker.start()
    start = time.monotonic()
    held = rt._claim_resources(ArmId("B"), ["device:desktop"])  # noqa: SLF001 — blocks
    acquired_at = time.monotonic()
    worker.join()

    assert held == ["device:desktop"]  # B did eventually acquire it
    assert acquired_at - start >= 0.09  # …and only after waiting for the release
    assert released_at and acquired_at >= released_at[0]


def test_contention_timeout_degrades_without_deadlock():
    rt = _rt(boids=BoidsArbitrator())
    rt._CLAIM_TIMEOUT_S = 0.1  # don't make the test wait the full 30s  # noqa: SLF001
    # A holds it and never releases.
    rt._boids.arbitrate(  # noqa: SLF001
        ResourceClaim(arm_id=ArmId("A"), resource_uri="device:desktop", ttl_ms=600_000)
    )
    # B can't acquire within the timeout → degrades: proceeds holding nothing,
    # rather than blocking the pool forever.
    held = rt._claim_resources(ArmId("B"), ["device:desktop"])  # noqa: SLF001
    assert held == []


# --------------------------------------------------------------------------- #
# Phase 2 · skill-declared exclusivity → assignment.exclusive_resources       #
# --------------------------------------------------------------------------- #


def _graph_with_skill(skill_ref: str):
    from runtime.platform.models import BudgetSpec, TaskGraph, TaskNode

    return TaskGraph(
        nodes=[TaskNode(node_id="n1", skill_ref=skill_ref)],
        budget=BudgetSpec(tokens=1000, usd=0.01),
        task_type="test",
    )


def test_with_resources_populates_from_resolver():
    from runtime.execution.swarm.runtime import _split_per_node

    rt = SwarmRuntime(
        arm_pool=ArmPool([]),
        max_workers=2,
        skill_resources=lambda ref: ["device:desktop"] if ref == "mouse_click" else [],
    )
    [assignment] = _split_per_node(_graph_with_skill("mouse_click"))
    out = rt._with_resources(assignment)  # noqa: SLF001
    assert out.exclusive_resources == ["device:desktop"]


def test_with_resources_noop_for_undeclared_skill():
    from runtime.execution.swarm.runtime import _split_per_node

    rt = SwarmRuntime(arm_pool=ArmPool([]), max_workers=2, skill_resources=lambda ref: [])
    [assignment] = _split_per_node(_graph_with_skill("read_file"))
    assert rt._with_resources(assignment).exclusive_resources == []  # noqa: SLF001


def test_split_assignment_has_no_resources_by_default():
    from runtime.execution.swarm.runtime import _split_per_node

    # Without Phase-2 population (no resolver), a freshly split assignment
    # declares nothing — the mechanism is strictly opt-in.
    [assignment] = _split_per_node(_graph_with_skill("mouse_click"))
    assert assignment.exclusive_resources == []


def test_registry_resolver_reads_declared_resource():
    # Mirrors the resolver run_swarm() builds from the registry.
    from runtime.execution.suckers.registry import Skill, SkillRegistry

    reg = SkillRegistry()
    reg.register(
        Skill(
            name="grab_screen",
            trusted_source="builtin://grab_screen",
            handler=lambda **k: {},
            exclusive_resource="device:desktop",
        ),
        verify_tests=False,
    )
    reg.register(
        Skill(
            name="read_file",
            trusted_source="builtin://read_file",
            handler=lambda **k: {},
        ),
        verify_tests=False,
    )

    def resolver(ref: str) -> list[str]:
        try:
            res = reg.get(ref).exclusive_resource
        except Exception:
            return []
        return [res] if res else []

    assert resolver("grab_screen") == ["device:desktop"]
    assert resolver("read_file") == []  # declared None
    assert resolver("nonexistent") == []  # not in registry


def test_computer_control_skills_declare_desktop():
    import pytest

    from runtime.execution.suckers.computer_skills import register_computer_skills
    from runtime.execution.suckers.registry import SkillRegistry

    reg = SkillRegistry()
    if register_computer_skills(reg, verify_tests=False) == 0:
        pytest.skip("pyautogui not installed — computer skills not registered")
    # Control skills drive the single physical screen/mouse/keyboard ⇒ exclusive.
    for name in ("mouse_click", "mouse_move", "keyboard_type", "keyboard_press"):
        assert reg.get(name).exclusive_resource == "device:desktop", name
    # Read-only observers don't contend ⇒ no claim.
    for name in ("screen_capture", "screen_info"):
        assert reg.get(name).exclusive_resource is None, name

