from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.platform.plugins.plugin_hub import PluginHub
from runtime.sensing.gateway.agent_world_router import create_agent_world_router


def _write_runtime_plugin(root: Path, *, delivery: str = "remote") -> Path:
    plugin = root / "narrative_studio"
    plugin.mkdir(parents=True)
    (plugin / "plugin.yaml").write_text(
        f"name: narrative_studio\nversion: 0.2.0\ndelivery: {delivery}\n",
        encoding="utf-8",
    )
    (plugin / "__init__.py").write_text(
        "from runtime.platform.plugins.plugin_base import ModulePlugin\n"
        "class NarrativeRemotePlugin(ModulePlugin):\n"
        "    name = 'narrative_studio'\n"
        "    version = '0.2.0'\n",
        encoding="utf-8",
    )
    return plugin


def test_remote_only_bundled_source_is_inert_until_external_package_exists(
    tmp_path: Path,
) -> None:
    bundled = tmp_path / "bundled"
    external = tmp_path / "plugins"
    _write_runtime_plugin(bundled)
    hub = PluginHub(plugin_dir=external, bundled_plugin_dir=bundled)

    assert "narrative_studio" not in {item["id"] for item in hub.discover()}
    assert hub.load("narrative_studio") is None

    _write_runtime_plugin(external / "workbench")
    discovered = {item["id"]: item for item in hub.discover()}

    assert discovered["narrative_studio"]["bundled"] is False
    assert discovered["narrative_studio"]["source"] == "external"
    assert hub.load("narrative_studio") is not None
    assert hub.start("narrative_studio") is True
    assert hub.is_started("narrative_studio") is True
    assert hub.unload("narrative_studio") is True
    assert hub.get_plugin("narrative_studio") is None


def test_external_runtime_id_can_differ_from_package_directory_and_disable_persists(
    tmp_path: Path,
) -> None:
    external = tmp_path / "plugins"
    package = external / "workbench" / "paper-trading"
    package.mkdir(parents=True)
    (package / "plugin.yaml").write_text(
        "name: paper_trading\nversion: 0.6.0\ndelivery: remote\n",
        encoding="utf-8",
    )
    (package / "__init__.py").write_text(
        "from runtime.platform.plugins.plugin_base import ModulePlugin\n"
        "class PaperTradingRemotePlugin(ModulePlugin):\n"
        "    name = 'paper_trading'\n"
        "    version = '0.6.0'\n",
        encoding="utf-8",
    )
    hub = PluginHub(plugin_dir=external, bundled_plugin_dir=tmp_path / "bundled")

    enabled = hub.enable_plugin("paper_trading")
    assert enabled["loaded"] is True
    assert enabled["started"] is True
    assert hub.get_plugin("paper_trading").__class__.__module__ == "paper_trading"

    disabled = hub.disable_plugin("paper_trading")
    assert disabled["enabled"] is False
    assert disabled["loaded"] is False
    assert hub.load("paper_trading") is None

    reloaded_hub = PluginHub(plugin_dir=external, bundled_plugin_dir=tmp_path / "bundled")
    discovered = {item["id"]: item for item in reloaded_hub.discover()}
    assert discovered["paper_trading"]["enabled"] is False
    assert reloaded_hub.enable_plugin("paper_trading")["started"] is True


def test_remote_narrative_package_overrides_stale_factory_registration(
    tmp_path: Path,
) -> None:
    bundled = tmp_path / "bundled"
    external = tmp_path / "plugins"
    # The activation store still knows the historical narrative id as a
    # factory seed, while the manifest explicitly marks the package remote.
    _write_runtime_plugin(bundled, delivery="remote")
    _write_runtime_plugin(external / "workbench", delivery="remote")
    hub = PluginHub(plugin_dir=external, bundled_plugin_dir=bundled)

    enabled = hub.enable_plugin("narrative_studio")

    assert enabled["source"] == "external"
    assert enabled["loaded"] is True
    assert enabled["started"] is True
    assert hub.disable_plugin("narrative_studio")["enabled"] is False


class _RemoteCatalog:
    package_dir: Path
    events: list[str]
    install_kwargs: dict[str, Any]

    def __init__(self, _kind: str) -> None:
        pass

    def items(self) -> list[dict[str, Any]]:
        return [
            {
                "id": "workbench_narrative",
                "kind": "workbench",
                "plugin": "narrative_studio",
            }
        ]

    def install_plugin(self, name: str, **kwargs: Any) -> dict[str, Any]:
        self.events.append("package:install")
        type(self).install_kwargs = kwargs
        self.package_dir.mkdir(parents=True, exist_ok=True)
        (self.package_dir / "plugin.yaml").write_text("name: narrative_studio\n", encoding="utf-8")
        return {"installed": True, "path": str(self.package_dir), "plugin_id": name}

    def uninstall_plugin(self, name: str, **_kwargs: Any) -> dict[str, Any]:
        self.events.append("package:uninstall")
        return {"uninstalled": True, "plugin_id": name}

    def set_workbench_enabled(self, name: str, enabled: bool) -> dict[str, Any]:
        self.events.append(f"package:{'enable' if enabled else 'disable'}")
        return {
            "plugin_id": name,
            "installed": True,
            "enabled": enabled,
            "lifecycle_state": "enabled" if enabled else "disabled",
        }


class _RuntimeHub:
    def __init__(self, events: list[str], *, fail_load: bool = False) -> None:
        self.events = events
        self.fail_load = fail_load
        self.loaded = False
        self.started = False

    def load(self, _name: str) -> object | None:
        self.events.append("runtime:load")
        if self.fail_load:
            return None
        self.loaded = True
        return object()

    def start(self, _name: str) -> bool:
        self.events.append("runtime:start")
        self.started = True
        return True

    def get_plugin(self, _name: str) -> object | None:
        return object() if self.loaded else None

    def is_started(self, _name: str) -> bool:
        return self.started

    def unload(self, _name: str) -> bool:
        self.events.append("runtime:unload")
        self.loaded = False
        self.started = False
        return True


def _client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    fail_load: bool = False,
) -> tuple[TestClient, list[str]]:
    from runtime.platform.plugins import cloud_catalog

    events: list[str] = []
    _RemoteCatalog.package_dir = tmp_path / "plugins" / "workbench" / "narrative_studio"
    _RemoteCatalog.events = events
    _RemoteCatalog.install_kwargs = {}
    monkeypatch.setattr(cloud_catalog, "CloudCatalog", _RemoteCatalog)
    app = FastAPI()
    app.state.plugin_hub = _RuntimeHub(events, fail_load=fail_load)
    app.include_router(create_agent_world_router())
    return TestClient(app), events


def test_market_install_hot_loads_backend_and_uninstall_unloads_before_delete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, events = _client(tmp_path, monkeypatch)

    installed = client.post("/api/agent-market/cloud/plugins/workbench_narrative/install")
    removed = client.delete("/api/agent-market/cloud/plugins/workbench_narrative/install")

    assert installed.status_code == 200
    assert installed.json()["restart_required"] is False
    assert installed.json()["loaded"] is True
    assert removed.status_code == 200
    assert events == [
        "package:install",
        "runtime:load",
        "runtime:start",
        "runtime:unload",
        "package:uninstall",
    ]


def test_market_install_forwards_restore_options_and_frontend_activation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, events = _client(tmp_path, monkeypatch)

    installed = client.post(
        "/api/agent-market/cloud/plugins/workbench_narrative/install",
        json={
            "enabled": True,
            "restore_data": True,
            "recovery_id": "recover-1",
        },
    )
    disabled = client.post("/api/agent-market/cloud/plugins/workbench_narrative/disable")
    enabled = client.post("/api/agent-market/cloud/plugins/workbench_narrative/enable")

    assert installed.status_code == 200
    assert _RemoteCatalog.install_kwargs == {
        "plugin_kind": "workbench",
        "enabled": True,
        "restore_data": True,
        "recovery_id": "recover-1",
    }
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False
    assert enabled.status_code == 200
    assert enabled.json()["enabled"] is True
    assert events[-2:] == ["package:disable", "package:enable"]


def test_failed_hot_load_rolls_back_new_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, events = _client(tmp_path, monkeypatch, fail_load=True)

    response = client.post("/api/agent-market/cloud/plugins/workbench_narrative/install")

    assert response.status_code == 400
    assert events == [
        "package:install",
        "runtime:load",
        "runtime:unload",
        "package:uninstall",
    ]

