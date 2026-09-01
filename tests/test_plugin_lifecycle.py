from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.platform.plugins.plugin_lifecycle import (
    HISTORY_SCHEMA,
    install_local_plugin,
    plugin_lifecycle_history,
    rollback_plugin_transaction,
)
from runtime.sensing.gateway.plugins_router import create_plugins_router


def _plugin(root: Path, version: str, *, release_evidence: bool = False) -> Path:
    plugin = root / f"research-{version}"
    (plugin / ".codex-plugin").mkdir(parents=True)
    (plugin / "skills" / "brief").mkdir(parents=True)
    (plugin / ".codex-plugin" / "plugin.json").write_text(
        json.dumps(
            {
                "name": "research",
                "version": version,
                "interface": {"capabilities": [{"name": "brief", "type": "codex"}]},
            }
        ),
        encoding="utf-8",
    )
    (plugin / "skills" / "brief" / "SKILL.md").write_text(
        f"# Brief {version}\n",
        encoding="utf-8",
    )
    if release_evidence:
        (plugin / "MIGRATION.md").write_text("# Migration\n", encoding="utf-8")
        (plugin / "tests").mkdir()
        (plugin / "tests" / "test_contract.py").write_text(
            "def test_contract():\n    assert True\n",
            encoding="utf-8",
        )
    return plugin


def _installed_version(root: Path) -> str:
    return str(
        json.loads(
            (root / "research" / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
        )["version"]
    )


def test_plugin_lifecycle_installs_upgrades_and_rolls_back(tmp_path: Path) -> None:
    managed = tmp_path / "managed"
    first_source = _plugin(tmp_path / "sources", "1.0.0")
    second_source = _plugin(tmp_path / "sources", "1.1.0", release_evidence=True)

    first = install_local_plugin(
        first_source,
        plugin_root=managed,
        confirm_install=True,
    )
    assert first["operation"] == "install"
    assert first["rollback_available"] is True
    assert _installed_version(managed) == "1.0.0"

    second = install_local_plugin(
        second_source,
        plugin_root=managed,
        confirm_install=True,
    )
    assert second["operation"] == "upgrade"
    assert second["previous_version"] == "1.0.0"
    assert second["migration_ready"] is True
    assert Path(second["backup"]).is_dir()
    assert _installed_version(managed) == "1.1.0"

    rolled_back = rollback_plugin_transaction(
        second["transaction_id"],
        plugin_root=managed,
        confirm_rollback=True,
    )
    assert rolled_back["status"] == "rolled_back"
    assert rolled_back["restored_version"] == "1.0.0"
    assert _installed_version(managed) == "1.0.0"

    history = plugin_lifecycle_history(plugin_root=managed)
    assert history["schema"] == HISTORY_SCHEMA
    assert history["total"] == 3


def test_plugin_lifecycle_rejects_unsafe_or_unready_upgrade(tmp_path: Path) -> None:
    managed = tmp_path / "managed"
    first_source = _plugin(tmp_path / "sources", "1.0.0")
    install_local_plugin(first_source, plugin_root=managed, confirm_install=True)

    unsafe = _plugin(tmp_path / "unsafe", "1.1.0", release_evidence=True)
    (unsafe / "escape").symlink_to(tmp_path / "outside")
    with pytest.raises(ValueError, match="symbolic links"):
        install_local_plugin(unsafe, plugin_root=managed, confirm_install=True)

    no_migration = _plugin(tmp_path / "unready", "1.1.0")
    with pytest.raises(ValueError, match="migration gate failed"):
        install_local_plugin(no_migration, plugin_root=managed, confirm_install=True)
    assert _installed_version(managed) == "1.0.0"


def test_plugin_lifecycle_requires_trusted_publisher_only_for_remote_mode(
    tmp_path: Path,
) -> None:
    source = _plugin(tmp_path / "sources", "1.0.0")

    local = install_local_plugin(
        source,
        plugin_root=tmp_path / "local-managed",
        confirm_install=True,
    )
    assert local["status"] == "committed"

    with pytest.raises(ValueError, match="trusted publisher signature is required"):
        install_local_plugin(
            source,
            plugin_root=tmp_path / "remote-managed",
            confirm_install=True,
            require_trusted_publisher=True,
        )
    assert not (tmp_path / "remote-managed" / "research").exists()


def test_plugin_lifecycle_requires_trusted_publisher_in_production_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _plugin(tmp_path / "sources", "1.0.0")
    managed = tmp_path / "managed"
    monkeypatch.setenv("ECHO_DEPLOYMENT_MODE", "production")

    with pytest.raises(ValueError, match="trusted publisher signature is required"):
        install_local_plugin(
            source,
            plugin_root=managed,
            confirm_install=True,
        )

    assert not (managed / "research").exists()


def test_plugin_lifecycle_router_requires_confirmation_and_audits(tmp_path: Path) -> None:
    managed = tmp_path / "managed"
    source = _plugin(tmp_path / "sources", "1.0.0")
    audit_path = tmp_path / "audit.jsonl"
    app = FastAPI()
    app.include_router(
        create_plugins_router(
            plugin_roots=[managed],
            promotion_audit_path=audit_path,
        )
    )
    client = TestClient(app)

    denied = client.post("/api/plugins/lifecycle/install", json={"source_path": str(source)})
    assert denied.status_code == 400
    installed = client.post(
        "/api/plugins/lifecycle/install",
        json={"source_path": str(source), "confirm_install": True},
    )
    assert installed.status_code == 200
    assert installed.json()["status"] == "committed"
    history = client.get("/api/plugins/lifecycle/history").json()
    assert history["total"] == 1

    rolled_back = client.post(
        "/api/plugins/lifecycle/rollback",
        json={
            "transaction_id": installed.json()["transaction_id"],
            "confirm_rollback": True,
        },
    )
    assert rolled_back.status_code == 200
    assert not (managed / "research").exists()
    audit_text = audit_path.read_text(encoding="utf-8")
    assert "plugin_lifecycle_install" in audit_text
    assert "plugin_lifecycle_rollback" in audit_text

