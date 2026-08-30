from __future__ import annotations

import base64
import importlib.util
import json
import tarfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from runtime.platform.plugins.cloud_catalog import CloudCatalog
from runtime.platform.plugins.marketplace_package import verify_marketplace_package_trust

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "extensions"
    / "workbuddy-experts"
    / "scripts"
    / "build-cloud-bundles.py"
)


def _load_builder():
    spec = importlib.util.spec_from_file_location("test_build_cloud_bundles", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_plugin_content_builder_signs_codex_and_connector_packages(
    tmp_path: Path, monkeypatch
) -> None:
    builder = _load_builder()
    codex = tmp_path / "codex" / "documents"
    codex_manifest = codex / ".codex-plugin" / "plugin.json"
    codex_manifest.parent.mkdir(parents=True)
    codex_manifest.write_text(
        json.dumps({"name": "documents", "version": "1.0.0"}),
        encoding="utf-8",
    )
    codex_content = codex / "content.txt"
    codex_content.write_text("codex content", encoding="utf-8")
    codex_content.chmod(0o755)
    connector = tmp_path / "connectors" / "documents"
    connector.mkdir(parents=True)
    (connector / "cli.json").write_text('{"command":"documents"}', encoding="utf-8")
    connector_catalog = tmp_path / "connector-catalog.json"
    connector_catalog.write_text(
        json.dumps(
            {
                "connectors": [
                    {
                        "id": "documents",
                        "type": "mcp",
                        "auth_mode": "token",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    cache = tmp_path / "cache"
    cache.mkdir()
    workbenches = tmp_path / "workbenches"
    workbenches.mkdir()
    monkeypatch.setattr(builder, "REPO_CODEX_PLUGINS", codex.parent)
    monkeypatch.setattr(builder, "CODEX_CACHE", cache)
    monkeypatch.setattr(builder, "CONNECTOR_ROOT", connector.parent)
    monkeypatch.setattr(builder, "CONNECTOR_CATALOG", connector_catalog)
    monkeypatch.setattr(builder, "WORKBENCH_ROOT", workbenches)

    private_key = Ed25519PrivateKey.generate()
    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    monkeypatch.setenv(
        "ECHO_PLUGIN_SIGNING_PRIVATE_KEY",
        base64.b64encode(private_bytes).decode("ascii"),
    )
    monkeypatch.setenv("ECHO_PLUGIN_SIGNING_PUBLISHER_ID", "echoai")
    monkeypatch.setenv("ECHO_PLUGIN_SIGNING_KEY_ID", "release-1")
    trust_store = tmp_path / "plugin-publishers.json"
    trust_store.write_text(
        json.dumps(
            {
                "schema": "echo.plugin_publisher_trust_store.v1",
                "publishers": [
                    {
                        "publisher_id": "echoai",
                        "keys": [
                            {
                                "key_id": "release-1",
                                "algorithm": "ed25519",
                                "status": "active",
                                "public_key": base64.b64encode(public_bytes).decode("ascii"),
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "output"
    output.mkdir()

    builder.build_plugins(output)

    runtime_extracted = tmp_path / "runtime-extracted"
    for kind in ("codex", "connector"):
        package = CloudCatalog._extract_member(
            output / "echo-plugins.tar.gz",
            f"plugins/{kind}",
            runtime_extracted / kind,
            "documents",
        )
        assert package is not None
        runtime_trust = verify_marketplace_package_trust(
            package,
            package_kind=kind,
            plugin_id="documents",
            expected_version="1.0.0",
            trust_store_path=trust_store,
            require_trusted=True,
        )
        assert runtime_trust["publisher_verified"] is True
        assert runtime_trust["host_api"] == ">=0.2,<0.3"
    connector_trust = verify_marketplace_package_trust(
        runtime_extracted / "connector" / "documents",
        package_kind="connector",
        plugin_id="documents",
        trust_store_path=trust_store,
        require_trusted=True,
    )
    assert connector_trust["permissions"] == [
        "account.credentials",
        "network.remote",
        "process.local",
    ]
    assert connector_trust["auth_modes"] == ["token"]
    assert (
        runtime_extracted / "codex" / "documents" / "content.txt"
    ).stat().st_mode & 0o777 == 0o755

    extracted = tmp_path / "extracted"
    extracted.mkdir()
    with tarfile.open(output / "echo-plugins.tar.gz", "r:gz") as archive:
        archive.extractall(extracted, filter="data")
    for kind in ("codex", "connector"):
        trust = verify_marketplace_package_trust(
            extracted / "plugins" / kind / "documents",
            package_kind=kind,
            plugin_id="documents",
            expected_version="1.0.0",
            trust_store_path=trust_store,
            require_trusted=True,
        )
        assert trust["publisher_verified"] is True
        assert trust["publisher_id"] == "echoai"
        assert trust["release_summary"].startswith("1.0.0：")

    assert not (codex / ".codex-plugin" / "provenance.json").exists()
    assert not (connector / ".echo-connector").exists()



