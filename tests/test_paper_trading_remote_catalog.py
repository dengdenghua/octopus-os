from __future__ import annotations

import json
from pathlib import Path

import pytest

from runtime.platform.plugins import cloud_catalog
from runtime.platform.plugins.cloud_catalog import CloudCatalog


def _catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[CloudCatalog, Path]:
    (tmp_path / ".git").mkdir()
    frontend = tmp_path / "extensions" / "workbench-apps" / "paper-trading"
    (frontend / "dist").mkdir(parents=True)
    (frontend / "dist" / "index.html").write_text("<div>paper trading</div>", "utf-8")
    (frontend / "app.json").write_text(
        json.dumps(
            {
                "schema": "echo.workbench_app.v1",
                "id": "paper-trading",
                "name": "Paper Trading",
                "description": "remote full-stack workbench",
                "route": "/workspace/paper-trading",
                "module_id": "paper.trading",
                "version": "1.0.0",
                "entry": "dist/index.html",
                "runtime_plugin": "paper_trading",
                "isolation": "iframe",
                "permissions": ["backend.api"],
                "data_paths": ["paper_trading"],
            }
        ),
        "utf-8",
    )
    backend = tmp_path / "runtime" / "platform" / "plugins" / "bundled" / "paper_trading"
    backend.mkdir(parents=True)
    (backend / "__init__.py").write_text("BACKEND = True\n", "utf-8")
    (backend / "plugin.yaml").write_text(
        "name: paper_trading\nversion: 0.6.0\ndelivery: remote\n", "utf-8"
    )

    plugin_root = tmp_path / "data" / "plugins"
    monkeypatch.setattr(cloud_catalog, "REPO", tmp_path)
    monkeypatch.setattr(CloudCatalog, "PLUGIN_INSTALL_ROOT", plugin_root)
    monkeypatch.setattr(CloudCatalog, "SKILLS_ROOT", tmp_path / "data" / "skills")
    monkeypatch.setattr(CloudCatalog, "CODEX_CACHE_ROOT", tmp_path / "codex-plugins")
    monkeypatch.setattr(
        CloudCatalog,
        "CONNECTOR_STATE_FILE",
        tmp_path / "data" / "connectors" / "state.json",
    )
    catalog = CloudCatalog("plugins", use_remote=False, use_cache=False)
    catalog._store = {"items": []}
    return catalog, plugin_root


def test_paper_trading_installs_frontend_and_runtime_under_distinct_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog, plugin_root = _catalog(tmp_path, monkeypatch)

    installed = catalog.install_plugin("paper-trading", plugin_kind="workbench")
    package = plugin_root / "workbench" / "paper-trading"

    assert installed["runtime_plugin"] == "paper_trading"
    assert (package / "app.json").is_file()
    assert (package / "dist" / "index.html").is_file()
    assert (package / "plugin.yaml").is_file()
    assert (package / "__init__.py").is_file()
    assert catalog.plugin_statuses()["paper-trading"]["runtime_plugin"] == "paper_trading"

    works = tmp_path / "data" / "paper_trading"
    works.mkdir(parents=True)
    (works / "positions.json").write_text("{}", "utf-8")
    removed = catalog.uninstall_plugin("paper-trading", plugin_kind="workbench")

    assert removed["runtime_plugin"] == "paper_trading"
    assert removed["data"]["status"] == "kept"
    assert (works / "positions.json").is_file()
    assert not package.exists()

