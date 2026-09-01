"""End-to-end proof of the arm-block contract (P2 template).

Loads the committed reference arm (``demos/arms/memory_arm``) through the
real PluginHub + ServiceBus path and asserts the whole chain works:

  block.yaml (kind: arm, consumes: [memory])
    → topological load after `memory` is bound
    → registers a skill into the SkillRegistry
    → skill handler resolves the `memory` service via ctx.service_bus
"""

from __future__ import annotations

import sys
from pathlib import Path

from runtime.execution.suckers.registry import SkillRegistry
from runtime.platform.plugins.plugin_hub import PluginHub
from runtime.platform.process.block_manifest import BlockKind, BlockManifest
from runtime.platform.process.composition import build_default_service_bus

DEMO_ARMS = Path(__file__).resolve().parent.parent / "demos" / "arms"
ARM_NAME = "memory_arm"
SKILL_NAME = "memory_arm.recall"


class _FakeJournal:
    def __init__(self) -> None:
        self.events = []

    def write(self, _event) -> None:
        pass

    def read_all(self, *, scope=None):
        return list(self.events)

    def read_by_session(self, _session_id: str):
        return []


def _clean_imports() -> None:
    sys.modules.pop(ARM_NAME, None)


def test_arm_manifest_kind_is_arm():
    manifest = BlockManifest.from_yaml(DEMO_ARMS / ARM_NAME / "plugin.yaml")
    assert manifest.kind == BlockKind.ARM
    assert manifest.consumes == ["memory"]
    assert manifest.provides == ["memory_arm.skills"]


def test_arm_loads_and_registers_skill_via_service_bus():
    _clean_imports()
    try:
        registry = SkillRegistry()
        bus = build_default_service_bus(journal=_FakeJournal())
        hub = PluginHub(
            plugin_dir=DEMO_ARMS,
            skill_registry=registry,
            service_bus=bus,
        )

        loaded = hub.load_all()
        assert ARM_NAME in loaded
        assert bus.has("memory_arm.skills")  # arm's provides bound to the bus

        # The demo skill landed in the shared SkillRegistry.
        assert registry.has(SKILL_NAME)
        skill = registry.get(SKILL_NAME)
        assert skill.cost_profile == "low"

        # The REAL 432-line memory family registered through the same arm.
        for real in ("remember", "recall", "diary_write", "update_soul"):
            assert registry.has(real), f"real memory skill {real} missing"

        # The handler resolves the memory service through the bus (no direct
        # journal import) — this is the arm contract in action.
        output = skill.handler({})
        assert output == "recalled 0 journal events via memory service"
    finally:
        _clean_imports()


def test_real_family_registration_is_idempotent():
    """Loading the family twice (default assembly + arm) must not crash."""
    from runtime.execution.suckers.memory_skills import register_memory_skills

    registry = SkillRegistry()
    register_memory_skills(registry)
    register_memory_skills(registry)  # second load: no duplicate ValueError
    assert registry.has("remember")
    assert registry.has("recall")


def test_arm_is_blocked_without_memory_service():
    """Without a bound `memory` service the arm must not load (honest block)."""
    _clean_imports()
    try:
        bus = build_default_service_bus(journal=None)  # no memory service
        hub = PluginHub(plugin_dir=DEMO_ARMS, service_bus=bus)
        loaded = hub.load_all()
        assert ARM_NAME not in loaded
    finally:
        _clean_imports()

