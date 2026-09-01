"""Tests for the composition-layer block manifest."""

from __future__ import annotations

import pytest

from runtime.platform.process.block_manifest import (
    BLOCK_MANIFEST_SCHEMA_VERSION,
    ApprovalMode,
    BlockKind,
    BlockManifest,
    SandboxMode,
    SandboxSpec,
)


def _valid() -> dict:
    return {
        "name": "echo.memory",
        "version": "1.0.0",
        "kind": "memory",
        "provides": ["memory"],
        "consumes": ["journal"],
        "emits": ["memory.promoted"],
        "subscribes": ["journal.appended"],
        "capabilities": ["vector_store"],
    }


def test_schema_version_defaults_to_current():
    manifest = BlockManifest.from_dict(_valid())
    assert manifest.schema_version == BLOCK_MANIFEST_SCHEMA_VERSION


def test_newer_schema_version_rejected():
    with pytest.raises(ValueError, match="not supported"):
        BlockManifest.from_dict({**_valid(), "schema_version": 99})


def test_zero_schema_version_rejected():
    with pytest.raises(ValueError):
        BlockManifest.from_dict({**_valid(), "schema_version": 0})


def test_valid_manifest_parses_with_defaults():
    manifest = BlockManifest.from_dict(_valid())
    assert manifest.name == "echo.memory"
    assert manifest.kind == BlockKind.MEMORY
    assert manifest.sandbox == SandboxSpec()
    assert manifest.sandbox.mode == SandboxMode.WORKSPACE_WRITE
    assert manifest.sandbox.approval == ApprovalMode.AUTO
    assert manifest.frontend is None
    assert manifest.dependencies == []


def test_name_must_be_lowercase_slug():
    with pytest.raises(ValueError, match="lowercase slug"):
        BlockManifest.from_dict({**_valid(), "name": "Echo.Memory"})


def test_duplicate_service_entries_rejected():
    with pytest.raises(ValueError, match="duplicate entry"):
        BlockManifest.from_dict({**_valid(), "provides": ["memory", "memory"]})


def test_self_consumption_rejected():
    with pytest.raises(ValueError, match="cannot both provide and consume"):
        BlockManifest.from_dict({**_valid(), "consumes": ["memory"]})


def test_unknown_kind_rejected():
    with pytest.raises(ValueError):
        BlockManifest.from_dict({**_valid(), "kind": "spaceship"})


def test_unknown_fields_forbidden():
    with pytest.raises(ValueError):
        BlockManifest.from_dict({**_valid(), "typo_field": 1})


def test_sandbox_tier_is_constrained():
    with pytest.raises(ValueError):
        BlockManifest.from_dict({**_valid(), "sandbox": {"mode": "root"}})


def test_from_yaml_roundtrip(tmp_path):
    path = tmp_path / "block.yaml"
    path.write_text(
        "name: echo.browser-arm\nkind: arm\nprovides: [browser]\nconsumes: [event_bus]\n",
        encoding="utf-8",
    )
    manifest = BlockManifest.from_yaml(path)
    assert manifest.name == "echo.browser-arm"
    assert manifest.kind == BlockKind.ARM
    assert manifest.provides == ["browser"]


def test_from_yaml_rejects_non_mapping(tmp_path):
    path = tmp_path / "block.yaml"
    path.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a mapping"):
        BlockManifest.from_yaml(path)


def test_from_plugin_manifest_maps_legacy_fields():
    class Legacy:
        name = "legacy_plugin"
        version = "0.3.0"
        description = "d"
        author = "a"
        requires = ["journal"]
        provides = ["widget"]
        subscribes = ["turn.started"]

    manifest = BlockManifest.from_plugin_manifest(Legacy())
    assert manifest.name == "legacy_plugin"
    assert manifest.kind == BlockKind.PLUGIN
    assert manifest.consumes == ["journal"]
    assert manifest.provides == ["widget"]
    assert manifest.subscribes == ["turn.started"]


