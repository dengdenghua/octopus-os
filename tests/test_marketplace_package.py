from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from runtime.platform.plugins.marketplace_package import (
    CODEX_SIGNATURE_RELATIVE_PATH,
    CONNECTOR_MANIFEST_RELATIVE_PATH,
    CONNECTOR_MANIFEST_SCHEMA,
    CONNECTOR_SIGNATURE_RELATIVE_PATH,
    compute_marketplace_content_provenance,
    load_marketplace_package_manifest,
    verify_marketplace_package_trust,
)
from runtime.platform.plugins.publisher_provenance import (
    canonical_publisher_signature_payload,
)


def _package(tmp_path: Path, kind: str) -> tuple[Path, Path]:
    root = tmp_path / kind
    if kind == "codex":
        manifest_path = root / ".codex-plugin" / "plugin.json"
        signature_path = CODEX_SIGNATURE_RELATIVE_PATH
        payload = {"name": "documents", "version": "1.2.3"}
    else:
        manifest_path = root / CONNECTOR_MANIFEST_RELATIVE_PATH
        signature_path = CONNECTOR_SIGNATURE_RELATIVE_PATH
        payload = {
            "schema": CONNECTOR_MANIFEST_SCHEMA,
            "id": "documents",
            "version": "1.2.3",
        }
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    (root / "content.txt").write_text("trusted content", encoding="utf-8")
    return root, signature_path


def _sign(root: Path, kind: str, signature_path: Path, tmp_path: Path) -> Path:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    trust_store = tmp_path / f"{kind}-publishers.json"
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
                                "public_key": base64.b64encode(public_key).decode("ascii"),
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    manifest = load_marketplace_package_manifest(root, package_kind=kind)
    provenance = compute_marketplace_content_provenance(
        root,
        signature_relative_path=signature_path,
    )
    signature = private_key.sign(
        canonical_publisher_signature_payload(
            plugin_id=manifest["name"],
            version=manifest["version"],
            content_digest=provenance["digest"],
            publisher_id="echoai",
            key_id="release-1",
        )
    )
    envelope = {
        "schema": "echo.plugin_publisher_signature.v1",
        "algorithm": "ed25519",
        "plugin_id": manifest["name"],
        "version": manifest["version"],
        "content_digest": provenance["digest"],
        "publisher_id": "echoai",
        "key_id": "release-1",
        "signature": base64.b64encode(signature).decode("ascii"),
    }
    target = root / signature_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(envelope), encoding="utf-8")
    return trust_store


@pytest.mark.parametrize("kind", ["codex", "connector"])
def test_marketplace_package_verifies_signed_content(tmp_path: Path, kind: str) -> None:
    root, signature_path = _package(tmp_path, kind)
    trust_store = _sign(root, kind, signature_path, tmp_path)

    trust = verify_marketplace_package_trust(
        root,
        package_kind=kind,
        plugin_id="documents",
        expected_version="1.2.3",
        trust_store_path=trust_store,
        require_trusted=True,
    )

    assert trust["schema"] == "echo.marketplace_package_trust.v1"
    assert trust["publisher_verified"] is True
    assert trust["publisher_id"] == "echoai"
    assert len(trust["content_digest"]) == 64


@pytest.mark.parametrize("kind", ["codex", "connector"])
def test_marketplace_package_rejects_tampering(tmp_path: Path, kind: str) -> None:
    root, signature_path = _package(tmp_path, kind)
    trust_store = _sign(root, kind, signature_path, tmp_path)
    (root / "content.txt").write_text("tampered", encoding="utf-8")

    with pytest.raises(ValueError, match="signature rejected"):
        verify_marketplace_package_trust(
            root,
            package_kind=kind,
            plugin_id="documents",
            expected_version="1.2.3",
            trust_store_path=trust_store,
            require_trusted=True,
        )


def test_marketplace_package_requires_release_signature(tmp_path: Path) -> None:
    root, _signature_path = _package(tmp_path, "connector")

    with pytest.raises(ValueError, match="trusted connector publisher signature is required"):
        verify_marketplace_package_trust(
            root,
            package_kind="connector",
            plugin_id="documents",
            expected_version="1.2.3",
            trust_store_path=tmp_path / "missing.json",
            require_trusted=True,
        )


def test_marketplace_package_rejects_identity_and_version_mismatch(tmp_path: Path) -> None:
    root, _signature_path = _package(tmp_path, "codex")

    with pytest.raises(ValueError, match="identity mismatch"):
        verify_marketplace_package_trust(
            root,
            package_kind="codex",
            plugin_id="browser",
        )
    with pytest.raises(ValueError, match="version mismatch"):
        verify_marketplace_package_trust(
            root,
            package_kind="codex",
            plugin_id="documents",
            expected_version="9.9.9",
        )


def test_marketplace_package_rejects_symlinked_content(tmp_path: Path) -> None:
    root, _signature_path = _package(tmp_path, "connector")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    (root / "link.txt").symlink_to(outside)

    with pytest.raises(ValueError, match="content provenance is incomplete"):
        verify_marketplace_package_trust(
            root,
            package_kind="connector",
            plugin_id="documents",
        )


@pytest.mark.parametrize("mutation", ["ignored-directory", "file-mode"])
def test_marketplace_signature_covers_all_directories_and_file_modes(
    tmp_path: Path,
    mutation: str,
) -> None:
    root, signature_path = _package(tmp_path, "codex")
    trust_store = _sign(root, "codex", signature_path, tmp_path)
    if mutation == "ignored-directory":
        injected = root / "node_modules" / "injected.js"
        injected.parent.mkdir()
        injected.write_text("malicious content", encoding="utf-8")
    else:
        (root / "content.txt").chmod(0o755)

    with pytest.raises(ValueError, match="signature rejected"):
        verify_marketplace_package_trust(
            root,
            package_kind="codex",
            plugin_id="documents",
            expected_version="1.2.3",
            trust_store_path=trust_store,
            require_trusted=True,
        )


@pytest.mark.parametrize("release_summary", ["x" * 1_001, ["not", "text"], "bad\nline"])
def test_marketplace_package_rejects_invalid_release_summary(
    tmp_path: Path,
    release_summary: object,
) -> None:
    root, _signature_path = _package(tmp_path, "codex")
    manifest_path = root / ".codex-plugin" / "plugin.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["releaseNotes"] = release_summary
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="release summary is invalid"):
        load_marketplace_package_manifest(root, package_kind="codex")


@pytest.mark.parametrize(
    ("kind", "field", "value", "message"),
    [
        ("codex", "host_api", "definitely not a specifier", "host_api is invalid"),
        ("codex", "permissions", ["host.root"], "permissions are invalid"),
        ("connector", "auth_modes", ["password-in-plain-text"], "auth_modes are invalid"),
        ("connector", "dependencies", ["bad/dependency"], "dependencies .* invalid"),
        ("connector", "runtime_dependencies", ["bad\nfile"], "runtime_dependencies is invalid"),
    ],
)
def test_marketplace_package_rejects_unsafe_signed_requirements(
    tmp_path: Path,
    kind: str,
    field: str,
    value: object,
    message: str,
) -> None:
    root, _signature_path = _package(tmp_path, kind)
    path = (
        root / ".codex-plugin" / "plugin.json"
        if kind == "codex"
        else root / CONNECTOR_MANIFEST_RELATIVE_PATH
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    target = payload.setdefault("echo", {}) if kind == "codex" else payload
    target[field] = value
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_marketplace_package_manifest(root, package_kind=kind)


@pytest.mark.parametrize("kind", ["codex", "connector"])
def test_marketplace_package_projects_signed_requirements(tmp_path: Path, kind: str) -> None:
    root, _signature_path = _package(tmp_path, kind)
    path = (
        root / ".codex-plugin" / "plugin.json"
        if kind == "codex"
        else root / CONNECTOR_MANIFEST_RELATIVE_PATH
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    target = payload.setdefault("echo", {}) if kind == "codex" else payload
    target.update(
        {
            "host_api": ">=0.2,<0.3",
            "permissions": ["content.read", "network.remote"],
            "auth_modes": ["oauth"],
            "dependencies": ["base-tools"],
            "runtime_dependencies": ["renderer.whl"],
        }
    )
    path.write_text(json.dumps(payload), encoding="utf-8")

    manifest = load_marketplace_package_manifest(root, package_kind=kind)

    assert manifest["host_api"] == ">=0.2,<0.3"
    assert manifest["permissions"] == ["content.read", "network.remote"]
    assert manifest["auth_modes"] == ["oauth"]
    assert manifest["dependencies"] == ["base-tools"]
    assert manifest["runtime_dependencies"] == ["renderer.whl"]

