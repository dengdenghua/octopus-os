from __future__ import annotations

import json
from pathlib import Path

from runtime.platform.connectors.connector_registry import ConnectorRegistry
from runtime.platform.plugins import cloud_catalog
from runtime.platform.plugins.cloud_catalog import CloudCatalog


def _catalog_item(connector_id: str = "remote-demo") -> dict[str, object]:
    return {
        "id": f"wb_{connector_id}",
        "plugin": connector_id,
        "kind": "connector",
        "name": "Remote Demo",
        "name_zh": "云端示例",
        "description": "downloaded only when installed",
        "type": "mcp",
        "auth_mode": "token",
        "version": "2.1.0",
    }


def _registry(tmp_path: Path) -> ConnectorRegistry:
    return ConnectorRegistry(
        installed_root=tmp_path / "plugins" / "connector",
        skills_root=tmp_path / "skills",
        state_file=tmp_path / "connectors" / "state.json",
    )


def test_cloud_catalog_lists_connector_without_bundled_marketplace(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(CloudCatalog, "items", lambda _self: [_catalog_item()])

    rows = _registry(tmp_path).list()

    assert len(rows) == 1
    assert rows[0]["id"] == "remote-demo"
    assert rows[0]["installed"] is False
    assert rows[0]["version"] == "2.1.0"


def test_installed_connector_remains_available_when_catalog_is_offline(
    tmp_path: Path, monkeypatch
) -> None:
    package = tmp_path / "plugins" / "connector" / "offline-demo"
    package.mkdir(parents=True)
    (package / "mcp.json").write_text(
        json.dumps({"mcpServers": {"offline": {"url": "https://example.test/mcp"}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        CloudCatalog,
        "items",
        lambda _self: (_ for _ in ()).throw(RuntimeError("offline")),
    )

    connector = _registry(tmp_path).get("offline-demo")

    assert connector is not None
    assert connector.type == "mcp"
    assert connector.mcp_servers["offline"]["url"] == "https://example.test/mcp"


def test_install_downloads_only_selected_connector(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(CloudCatalog, "items", lambda _self: [_catalog_item()])
    calls: list[tuple[str, str]] = []

    def install_selected(
        _self: CloudCatalog,
        plugin_id: str,
        *,
        plugin_kind: str,
        dest_root: str | Path,
        **_kwargs: object,
    ) -> dict[str, object]:
        calls.append((plugin_id, plugin_kind))
        package = Path(dest_root) / plugin_id
        package.mkdir(parents=True)
        (package / "mcp.json").write_text(
            json.dumps({"mcpServers": {"remote": {"url": "https://example.test/mcp"}}}),
            encoding="utf-8",
        )
        return {"installed": True, "copied_skills": [], "source": "cloud"}

    monkeypatch.setattr(CloudCatalog, "install_plugin", install_selected)

    result = _registry(tmp_path).install("remote-demo")

    assert calls == [("remote-demo", "connector")]
    assert result["installed"] is True
    assert result["mcp_servers"] == ["remote"]


def test_connector_archive_uses_catalog_package_url(tmp_path: Path, monkeypatch) -> None:
    downloads: list[str] = []
    monkeypatch.setattr(cloud_catalog, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(
        cloud_catalog,
        "fetch_public_https_bytes",
        lambda url, **_kwargs: downloads.append(url) or b"one-connector",
    )
    item = {
        **_catalog_item(),
        "download_url": "https://example.test/echo-connector-remote-demo.tar.gz",
    }
    catalog = CloudCatalog("plugins", use_remote=False, use_cache=False)

    first = catalog._package_archive(
        item,
        package_kind="connector",
        package_id="remote-demo",
    )
    second = catalog._package_archive(
        item,
        package_kind="connector",
        package_id="remote-demo",
    )

    assert first == second
    assert first.read_bytes() == b"one-connector"
    assert downloads == [item["download_url"]]
