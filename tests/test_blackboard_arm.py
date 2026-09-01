"""End-to-end proof that the arm template generalizes (reference arm #2)."""

from __future__ import annotations

import sys
from pathlib import Path

from runtime.execution.suckers.registry import SkillRegistry
from runtime.platform.plugins.plugin_hub import PluginHub
from runtime.platform.process.block_manifest import BlockKind, BlockManifest
from runtime.platform.process.composition import build_default_service_bus

DEMO_ARMS = Path(__file__).resolve().parent.parent / "demos" / "arms"
ARM_NAME = "blackboard_arm"


def _clean() -> None:
    sys.modules.pop(ARM_NAME, None)


def test_manifest_is_arm_with_no_consumes():
    manifest = BlockManifest.from_yaml(DEMO_ARMS / ARM_NAME / "plugin.yaml")
    assert manifest.kind == BlockKind.ARM
    assert manifest.consumes == []  # dependency-free arm shape
    assert manifest.provides == ["blackboard_arm.skills"]


def test_arm_registers_blackboard_family_without_memory():
    _clean()
    try:
        registry = SkillRegistry()
        bus = build_default_service_bus(journal=None)  # no memory bound
        hub = PluginHub(
            plugin_dir=DEMO_ARMS,
            skill_registry=registry,
            service_bus=bus,
        )
        assert hub.load_all() == [ARM_NAME]
        for name in ("bb_write", "bb_read", "bb_keys"):
            assert registry.has(name), f"blackboard skill {name} missing"
        assert bus.has("blackboard_arm.skills")
    finally:
        _clean()

