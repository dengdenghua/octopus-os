from __future__ import annotations

import json
from pathlib import Path

import pytest

from runtime.platform.plugins import cloud_catalog
from runtime.platform.plugins.cloud_catalog import CloudCatalog


def _catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[CloudCatalog, Path]:
    (tmp_path / ".git").mkdir()
    source = tmp_path / "extensions" / "workbench-apps" / "narrative_studio"
    (source / "dist").mkdir(parents=True)
    (source / "dist" / "index.html").write_text("<div>remote narrative</div>", "utf-8")
    (source / "app.json").write_text(
        json.dumps(
            {
                "schema": "echo.workbench_app.v1",
                "id": "narrative_studio",
                "name": "Narrative Studio",
                "description": "remote full-stack workbench",
                "route": "/workspace/narrative",
                "module_id": "narrative",
                "version": "0.2.0",
                "release_summary": "0.2.0：支持角色、世界观与剧情分支协作。",
                "host_api": ">=0.2,<0.3",
                "dependencies": [],
                "entry": "dist/index.html",
                "runtime_plugin": "narrative_studio",
                "isolation": "iframe",
                "permissions": ["backend.api"],
                "data_paths": ["narrative-studio"],
            }
        ),
        "utf-8",
    )
    backend = tmp_path / "runtime" / "platform" / "plugins" / "bundled" / "narrative_studio"
    backend.mkdir(parents=True)
    (backend / "__init__.py").write_text("BACKEND = True\n", "utf-8")
    (backend / "plugin.yaml").write_text(
        "name: narrative_studio\nversion: 0.2.0\ndelivery: remote\n", "utf-8"
    )

    plugin_root = tmp_path / "data" / "plugins"
    codex_root = tmp_path / "codex-plugins"
    codex_root.mkdir()
    monkeypatch.setattr(cloud_catalog, "REPO", tmp_path)
    monkeypatch.setattr(CloudCatalog, "PLUGIN_INSTALL_ROOT", plugin_root)
    monkeypatch.setattr(CloudCatalog, "SKILLS_ROOT", tmp_path / "data" / "skills")
    monkeypatch.setattr(CloudCatalog, "CODEX_CACHE_ROOT", codex_root)
    monkeypatch.setattr(
        CloudCatalog,
        "CONNECTOR_STATE_FILE",
        tmp_path / "data" / "connectors" / "state.json",
    )
    catalog = CloudCatalog("plugins", use_remote=False, use_cache=False)
    catalog._store = {"items": []}
    return catalog, plugin_root


def _add_design_dependency(tmp_path: Path, *, dependencies: list[str] | None = None) -> None:
    source = tmp_path / "extensions" / "workbench-apps" / "design"
    (source / "dist").mkdir(parents=True)
    (source / "dist" / "index.html").write_text("<div>remote design</div>", "utf-8")
    (source / "app.json").write_text(
        json.dumps(
            {
                "schema": "echo.workbench_app.v1",
                "id": "design",
                "name": "Design",
                "description": "shared design dependency",
                "route": "/workspace/design",
                "module_id": "design",
                "version": "1.0.0",
                "host_api": ">=0.2,<0.3",
                "dependencies": dependencies or [],
                "entry": "dist/index.html",
                "isolation": "iframe",
                "permissions": [],
                "data_paths": [],
            }
        ),
        "utf-8",
    )


def _set_narrative_dependencies(tmp_path: Path, dependencies: list[str]) -> None:
    manifest_path = tmp_path / "extensions" / "workbench-apps" / "narrative_studio" / "app.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    manifest["dependencies"] = dependencies
    manifest_path.write_text(json.dumps(manifest), "utf-8")


def test_cloud_catalog_exposes_narrative_as_removable_remote_workbench(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog, _plugin_root = _catalog(tmp_path, monkeypatch)

    item = next(entry for entry in catalog.items() if entry["id"] == "workbench_narrative")

    assert item["plugin"] == "narrative_studio"
    assert item["kind"] == "workbench"
    assert "factory_seed" not in item
    assert item["removable"] is True
    assert item["data_policies"] == ["keep", "trash"]
    assert catalog.is_factory_plugin("narrative_studio") is False

    status = catalog.plugin_statuses()["narrative_studio"]
    assert status["lifecycle_state"] == "available"
    assert status["installed"] is False
    assert status["source"] == "cloud"
    assert status["trust"] == {
        "level": "catalog",
        "integrity_verified": False,
        "publisher_verified": False,
    }
    assert status["compatibility"] == {
        "status": "not_checked",
        "host_api": None,
    }
    assert status["release_summary"].startswith("0.2.0：")


def test_official_narrative_descriptor_overrides_catalog_collision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog, _plugin_root = _catalog(tmp_path, monkeypatch)
    catalog._store = {
        "items": [
            {"id": "workbench_narrative", "plugin": "shadow-package", "kind": "plugin"},
            {
                "id": "workbench_narrative",
                "plugin": "second-shadow",
                "kind": "connector",
            },
        ]
    }

    matches = [item for item in catalog.items() if item["id"] == "workbench_narrative"]

    assert len(matches) == 1
    assert matches[0]["plugin"] == "narrative_studio"
    assert matches[0]["kind"] == "workbench"


def test_remote_install_materializes_frontend_and_backend_and_uninstall_keeps_works(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog, plugin_root = _catalog(tmp_path, monkeypatch)
    works = tmp_path / "data" / "narrative-studio"
    works.mkdir(parents=True)
    (works / "draft.json").write_text('{"title":"keep me"}', encoding="utf-8")

    installed = catalog.install_plugin("narrative_studio", plugin_kind="workbench")
    package = plugin_root / "workbench" / "narrative_studio"

    assert installed["installed"] is True
    assert installed["source"] == "cloud"
    assert installed["restart_required"] is False
    assert (package / "app.json").is_file()
    assert (package / "dist" / "index.html").is_file()
    assert (package / "plugin.yaml").is_file()
    assert (package / "__init__.py").is_file()
    status = catalog.plugin_statuses()["narrative_studio"]
    assert status["lifecycle_state"] == "enabled"
    assert status["trust"] == {
        "level": "local_integrity",
        "integrity_verified": True,
        "publisher_verified": False,
    }
    assert status["compatibility"] == {
        "status": "compatible",
        "host_api": ">=0.2,<0.3",
    }
    assert status["release_summary"] == "0.2.0：支持角色、世界观与剧情分支协作。"

    removed = catalog.uninstall_plugin("narrative_studio", plugin_kind="workbench")

    assert removed["uninstalled"] is True
    assert removed["data"]["status"] == "kept"
    assert (works / "draft.json").exists()
    assert not package.exists()
    assert catalog.plugin_statuses()["narrative_studio"]["lifecycle_state"] == "available"


def test_remote_trash_is_confirmed_recoverable_and_reinstall_can_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog, _plugin_root = _catalog(tmp_path, monkeypatch)
    catalog.install_plugin("narrative_studio", plugin_kind="workbench")
    works = tmp_path / "data" / "narrative-studio"
    works.mkdir(parents=True)
    (works / "chapter.json").write_text('{"body":"once"}', encoding="utf-8")

    with pytest.raises(ValueError, match="confirm_data_move"):
        catalog.uninstall_plugin("narrative_studio", plugin_kind="workbench", data_policy="trash")

    removed = catalog.uninstall_plugin(
        "narrative_studio",
        plugin_kind="workbench",
        data_policy="trash",
        confirm_data_move=True,
    )
    recovery_id = removed["data"]["recovery_id"]
    assert not works.exists()
    assert removed["data"]["status"] == "trashed"
    assert removed["recoveries"][0]["recovery_id"] == recovery_id

    restored = catalog.install_plugin(
        "narrative_studio",
        plugin_kind="workbench",
        restore_data=True,
        recovery_id=recovery_id,
    )

    assert restored["data"]["status"] == "restored"
    assert (works / "chapter.json").exists()
    assert restored["recoveries"] == []


def test_installed_package_with_missing_entry_is_reported_broken(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog, plugin_root = _catalog(tmp_path, monkeypatch)
    catalog.install_plugin("narrative_studio", plugin_kind="workbench")
    (plugin_root / "workbench" / "narrative_studio" / "dist" / "index.html").unlink()

    status = catalog.plugin_statuses()["narrative_studio"]

    assert status["installed"] is True
    assert status["enabled"] is False
    assert status["lifecycle_state"] == "broken"
    assert "entry is missing" in status["error"]
    assert status["trust"]["level"] == "unverified"
    assert status["compatibility"]["status"] == "not_checked"


def test_installed_package_incompatible_with_current_host_is_reported_broken(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog, _plugin_root = _catalog(tmp_path, monkeypatch)
    catalog.install_plugin("narrative_studio", plugin_kind="workbench")
    monkeypatch.setattr(cloud_catalog, "__version__", "0.3.0")

    status = catalog.plugin_statuses()["narrative_studio"]

    assert status["installed"] is True
    assert status["enabled"] is False
    assert status["lifecycle_state"] == "broken"
    assert status["trust"]["level"] == "local_integrity"
    assert status["trust"]["integrity_verified"] is True
    assert status["compatibility"] == {
        "status": "incompatible",
        "host_api": ">=0.2,<0.3",
    }
    assert "requires host_api" in status["error"]


def test_frontend_workbench_disable_is_durable_and_reinstall_reenables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog, _plugin_root = _catalog(tmp_path, monkeypatch)
    catalog.install_plugin("narrative_studio", plugin_kind="workbench")

    disabled = catalog.set_workbench_enabled("narrative_studio", False)
    assert disabled["lifecycle_state"] == "disabled"
    assert catalog.plugin_statuses()["narrative_studio"]["enabled"] is False

    reloaded = CloudCatalog("plugins", use_remote=False, use_cache=False)
    reloaded._store = {"items": []}
    assert reloaded.plugin_statuses()["narrative_studio"]["lifecycle_state"] == "disabled"

    reloaded.install_plugin("narrative_studio", plugin_kind="workbench")
    status = reloaded.plugin_statuses()["narrative_studio"]
    assert status["enabled"] is True
    assert status["rollback_available"] is True
    assert status["rollback_operation"] == "update"


def test_workbench_dependencies_install_first_and_block_unsafe_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog, plugin_root = _catalog(tmp_path, monkeypatch)
    _add_design_dependency(tmp_path)
    _set_narrative_dependencies(tmp_path, ["design"])

    installed = catalog.install_plugin("narrative_studio", plugin_kind="workbench")

    assert (plugin_root / "workbench" / "design" / "app.json").is_file()
    assert installed["installed_dependencies"][0]["plugin_id"] == "design"
    with pytest.raises(ValueError, match="required by installed packages: narrative_studio"):
        catalog.set_workbench_enabled("design", False)
    with pytest.raises(ValueError, match="required by installed packages: narrative_studio"):
        catalog.uninstall_plugin("design", plugin_kind="workbench")

    catalog.uninstall_plugin("narrative_studio", plugin_kind="workbench")
    assert catalog.set_workbench_enabled("design", False)["enabled"] is False


def test_cyclic_workbench_dependencies_leave_no_partial_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog, plugin_root = _catalog(tmp_path, monkeypatch)
    _add_design_dependency(tmp_path, dependencies=["narrative_studio"])
    _set_narrative_dependencies(tmp_path, ["design"])

    with pytest.raises(ValueError, match="cyclic workbench dependency"):
        catalog.install_plugin("narrative_studio", plugin_kind="workbench")

    assert not (plugin_root / "workbench" / "design").exists()
    assert not (plugin_root / "workbench" / "narrative_studio").exists()


def test_restore_refuses_to_overwrite_new_live_works(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog, _plugin_root = _catalog(tmp_path, monkeypatch)
    catalog.install_plugin("narrative_studio", plugin_kind="workbench")
    works = tmp_path / "data" / "narrative-studio"
    works.mkdir(parents=True)
    (works / "old.json").write_text("old", encoding="utf-8")
    removed = catalog.uninstall_plugin(
        "narrative_studio",
        plugin_kind="workbench",
        data_policy="trash",
        confirm_data_move=True,
    )
    recovery_id = removed["data"]["recovery_id"]
    works.mkdir(parents=True)
    (works / "new.json").write_text("new", encoding="utf-8")

    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        catalog.install_plugin(
            "narrative_studio",
            plugin_kind="workbench",
            restore_data=True,
            recovery_id=recovery_id,
        )

    assert (works / "new.json").read_text("utf-8") == "new"
    assert catalog.plugin_statuses()["narrative_studio"]["installed"] is False


def test_workbench_update_is_atomic_and_can_restore_previous_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog, plugin_root = _catalog(tmp_path, monkeypatch)
    source_page = (
        tmp_path / "extensions" / "workbench-apps" / "narrative_studio" / "dist" / "index.html"
    )
    first = catalog.install_plugin("narrative_studio", plugin_kind="workbench")
    target_page = plugin_root / "workbench" / "narrative_studio" / "dist" / "index.html"
    assert first["operation"] == "install"
    assert "remote narrative" in target_page.read_text("utf-8")

    source_page.write_text("<div>updated narrative</div>", "utf-8")
    updated = catalog.install_plugin("narrative_studio", plugin_kind="workbench")

    assert updated["operation"] == "update"
    assert updated["rollback_available"] is True
    assert "updated narrative" in target_page.read_text("utf-8")

    rolled_back = catalog.rollback_plugin(
        "narrative_studio",
        transaction_id=updated["transaction_id"],
    )

    assert rolled_back["operation"] == "restored_previous"
    assert "remote narrative" in target_page.read_text("utf-8")


def test_invalid_update_never_replaces_last_good_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog, plugin_root = _catalog(tmp_path, monkeypatch)
    catalog.install_plugin("narrative_studio", plugin_kind="workbench")
    target_page = plugin_root / "workbench" / "narrative_studio" / "dist" / "index.html"
    source_page = (
        tmp_path / "extensions" / "workbench-apps" / "narrative_studio" / "dist" / "index.html"
    )
    source_page.unlink()

    with pytest.raises(FileNotFoundError, match="entry is missing"):
        catalog.install_plugin("narrative_studio", plugin_kind="workbench")

    assert "remote narrative" in target_page.read_text("utf-8")
    assert catalog.plugin_statuses()["narrative_studio"]["lifecycle_state"] == "enabled"

