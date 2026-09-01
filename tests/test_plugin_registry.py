from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.platform.plugins.codex_discovery import discover_codex_plugins
from runtime.platform.plugins.plugin_registry import (
    REGISTRY_SCHEMA,
    discover_plugin_registry_updates,
    install_registry_plugin,
)
from runtime.sensing.gateway.plugins_router import create_plugins_router


def _plugin(root: Path, version: str) -> Path:
    plugin = root / f"workspace-suite-{version}"
    (plugin / ".codex-plugin").mkdir(parents=True)
    (plugin / "skills" / "workspace").mkdir(parents=True)
    (plugin / ".codex-plugin" / "plugin.json").write_text(
        json.dumps(
            {
                "name": "workspace-suite",
                "version": version,
                "apps": [{"id": "workspace", "name": "Workspace"}],
                "mcpServers": {"workspace": {"type": "http", "url": "https://example.invalid/mcp"}},
                "permissions": ["network:https://example.invalid"],
            }
        ),
        encoding="utf-8",
    )
    (plugin / "skills" / "workspace" / "SKILL.md").write_text(
        "# Workspace\n\nOperate the workspace connector.\n",
        encoding="utf-8",
    )
    return plugin


def _registry(path: Path, source: Path, *, require_signature: bool = False) -> Path:
    candidate = discover_codex_plugins([source.parent])[0]
    digest = candidate["smoke"]["content_provenance"]["digest"]
    path.write_text(
        json.dumps(
            {
                "schema": REGISTRY_SCHEMA,
                "plugins": [
                    {
                        "id": "workspace-suite",
                        "version": candidate["version"],
                        "source_path": str(source),
                        "content_digest": digest,
                        "surfaces": ["apps", "mcp", "skills"],
                        "publisher_signature_required": require_signature,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_registry_discovers_verified_fixture_and_one_click_installs(tmp_path: Path) -> None:
    source = _plugin(tmp_path / "sources", "1.0.0")
    registry = _registry(tmp_path / "registry.json", source)
    managed = tmp_path / "managed"

    report = discover_plugin_registry_updates(
        registry_path=registry,
        plugin_root=managed,
    )

    assert report["ready"] is True
    assert report["install_count"] == 1
    entry = report["plugins"][0]
    assert entry["fixture_verified"] is True
    assert entry["one_click_install"] is True
    assert entry["surfaces"] == ["apps", "mcp", "skills"]

    installed = install_registry_plugin(
        "workspace-suite",
        registry_path=registry,
        plugin_root=managed,
        confirm_install=True,
    )
    assert installed["status"] == "committed"
    assert installed["registry_fixture_digest"] == entry["content_digest"]
    assert (managed / "workspace-suite").is_dir()

    current = discover_plugin_registry_updates(
        registry_path=registry,
        plugin_root=managed,
    )
    assert current["plugins"][0]["status"] == "current"
    assert current["plugins"][0]["one_click_install"] is False


def test_registry_blocks_unsigned_fixture_when_signature_is_required(tmp_path: Path) -> None:
    source = _plugin(tmp_path / "sources", "1.0.0")
    registry = _registry(tmp_path / "registry.json", source, require_signature=True)

    report = discover_plugin_registry_updates(
        registry_path=registry,
        plugin_root=tmp_path / "managed",
    )

    entry = report["plugins"][0]
    assert entry["fixture_verified"] is False
    assert entry["installable"] is False
    assert "trusted publisher signature is required" in entry["blockers"]


def test_registry_router_exposes_update_and_audited_install(tmp_path: Path) -> None:
    source = _plugin(tmp_path / "sources", "1.0.0")
    registry = _registry(tmp_path / "registry.json", source)
    managed = tmp_path / "managed"
    audit = tmp_path / "audit.jsonl"
    app = FastAPI()
    app.include_router(
        create_plugins_router(
            plugin_roots=[managed],
            plugin_registry_path=registry,
            promotion_audit_path=audit,
        )
    )
    client = TestClient(app)

    updates = client.get("/api/plugins/registry/updates")
    assert updates.status_code == 200
    assert updates.json()["plugins"][0]["one_click_install"] is True

    denied = client.post(
        "/api/plugins/registry/install",
        json={"plugin_id": "workspace-suite"},
    )
    assert denied.status_code == 400
    installed = client.post(
        "/api/plugins/registry/install",
        json={"plugin_id": "workspace-suite", "confirm_install": True},
    )
    assert installed.status_code == 200
    assert installed.json()["registry_surfaces"] == ["apps", "mcp", "skills"]
    assert "plugin_registry_install" in audit.read_text(encoding="utf-8")

