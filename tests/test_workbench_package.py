from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.platform.plugins.publisher_provenance import (
    canonical_publisher_signature_payload,
)
from runtime.platform.plugins.workbench_package import (
    WORKBENCH_SIGNATURE_RELATIVE_PATH,
    WorkbenchPackageStore,
    compute_workbench_content_provenance,
    verify_workbench_package_trust,
)
from runtime.safety.auth import Identity, IdentityStore
from runtime.sensing.gateway.workbench_packages_router import (
    create_workbench_packages_router,
)


def _package(root: Path, plugin_id: str = "narrative_studio") -> Path:
    package = root / plugin_id
    (package / "dist" / "assets").mkdir(parents=True)
    (package / "dist" / "index.html").write_text(
        "<!doctype html><title>Narrative</title>",
        encoding="utf-8",
    )
    (package / "dist" / "assets" / "app.js").write_text(
        "document.body.dataset.ready = 'true';",
        encoding="utf-8",
    )
    (package / "app.json").write_text(
        json.dumps(
            {
                "schema": "echo.workbench_app.v1",
                "id": plugin_id,
                "name": "叙事工坊",
                "description": "候选流水线与正典治理",
                "route": "/workspace/narrative",
                "module_id": "narrative",
                "version": "1.0.0",
                "entry": "dist/index.html",
                "isolation": "iframe",
                "permissions": ["workspace.read", "workspace.write"],
                "runtime_plugin": "narrative_studio",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return package


def test_manifest_and_asset_resolution_are_bounded_to_package(tmp_path: Path) -> None:
    _package(tmp_path)
    store = WorkbenchPackageStore(tmp_path)

    manifest = store.load_manifest("narrative_studio")

    assert manifest.entry == "dist/index.html"
    assert manifest.permissions == ("workspace.read", "workspace.write")
    assert manifest.runtime_plugin == "narrative_studio"
    assert store.asset_path("narrative_studio", "dist/assets/app.js").is_file()
    with pytest.raises(ValueError, match="unsafe workbench asset path"):
        store.asset_path("narrative_studio", "../secret")
    with pytest.raises(ValueError, match="invalid workbench plugin id"):
        store.package_dir("../narrative")


def test_manifest_rejects_identity_mismatch_missing_entry_and_symlink(tmp_path: Path) -> None:
    package = _package(tmp_path)
    manifest_path = package / "app.json"
    raw = json.loads(manifest_path.read_text("utf-8"))
    raw["id"] = "different"
    manifest_path.write_text(json.dumps(raw), encoding="utf-8")
    store = WorkbenchPackageStore(tmp_path)

    with pytest.raises(ValueError, match="manifest id mismatch"):
        store.load_manifest("narrative_studio")

    raw["id"] = "narrative_studio"
    raw["entry"] = "dist/missing.html"
    manifest_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="entry is missing"):
        store.load_manifest("narrative_studio")

    raw["entry"] = "dist/index.html"
    raw["runtime_plugin"] = "paper-trading"
    manifest_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid workbench runtime plugin id"):
        store.load_manifest("narrative_studio")

    raw["runtime_plugin"] = "narrative_studio"
    manifest_path.write_text(json.dumps(raw), encoding="utf-8")
    (package / "dist" / "linked.js").symlink_to(package / "dist" / "assets" / "app.js")
    with pytest.raises(ValueError, match="cannot contain symlinks"):
        store.asset_path("narrative_studio", "dist/linked.js")


def test_router_serves_validated_manifest_and_static_assets(tmp_path: Path) -> None:
    _package(tmp_path)
    app = FastAPI()
    app.include_router(create_workbench_packages_router(WorkbenchPackageStore(tmp_path)))
    client = TestClient(app)

    manifest = client.get("/api/workbench-packages/narrative_studio/manifest")
    assert manifest.status_code == 200
    assert manifest.json()["entry_url"] == (
        "/api/workbench-packages/narrative_studio/assets/dist/index.html"
    )

    page = client.get("/api/workbench-packages/narrative_studio/assets/dist/index.html")
    assert page.status_code == 200
    assert "Narrative" in page.text
    assert page.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'self'" in page.headers["content-security-policy"]

    missing = client.get("/api/workbench-packages/narrative_studio/assets/dist/missing.js")
    assert missing.status_code == 404


def test_router_requires_auth_when_enabled(tmp_path: Path) -> None:
    # Regression for audit 2026-08-28 P1-4: with require_auth on, the router
    # previously served manifests and assets to unauthenticated callers
    # (the _app_auth legacy-prefix allowlist falls through to call_next).
    _package(tmp_path)
    store_id = IdentityStore()
    store_id.add(Identity(actor_id="alice"), api_key_plaintext="sk-alice")
    app = FastAPI()
    app.include_router(
        create_workbench_packages_router(
            WorkbenchPackageStore(tmp_path),
            identity_store=store_id,
            require_auth=True,
        )
    )
    client = TestClient(app)

    assert client.get("/api/workbench-packages/narrative_studio/manifest").status_code == 401
    assert (
        client.get("/api/workbench-packages/narrative_studio/assets/dist/index.html").status_code
        == 401
    )

    ok = client.get(
        "/api/workbench-packages/narrative_studio/manifest",
        headers={"Authorization": "Bearer sk-alice"},
    )
    assert ok.status_code == 200


def test_signed_package_and_installed_digest_reject_tampering(tmp_path: Path) -> None:
    package = _package(tmp_path)
    manifest = WorkbenchPackageStore(tmp_path).load_manifest("narrative_studio")
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    provenance = compute_workbench_content_provenance(package)
    payload = canonical_publisher_signature_payload(
        plugin_id=manifest.id,
        version=manifest.version,
        content_digest=provenance["digest"],
        publisher_id="echoai",
        key_id="release-2026",
    )
    signature_path = package / WORKBENCH_SIGNATURE_RELATIVE_PATH
    signature_path.parent.mkdir(parents=True)
    signature_path.write_text(
        json.dumps(
            {
                "schema": "echo.plugin_publisher_signature.v1",
                "algorithm": "ed25519",
                "plugin_id": manifest.id,
                "version": manifest.version,
                "content_digest": provenance["digest"],
                "publisher_id": "echoai",
                "key_id": "release-2026",
                "signature": base64.b64encode(private_key.sign(payload)).decode("ascii"),
            }
        ),
        encoding="utf-8",
    )
    trust_store = tmp_path / "publishers.json"
    trust_store.write_text(
        json.dumps(
            {
                "schema": "echo.plugin_publisher_trust_store.v1",
                "publishers": [
                    {
                        "publisher_id": "echoai",
                        "keys": [
                            {
                                "key_id": "release-2026",
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

    trust = verify_workbench_package_trust(
        package,
        manifest,
        trust_store_path=trust_store,
        require_trusted=True,
    )
    trust_path = tmp_path / ".lifecycle" / "trust" / "narrative_studio.json"
    trust_path.parent.mkdir(parents=True)
    trust_path.write_text(json.dumps(trust), encoding="utf-8")
    strict_store = WorkbenchPackageStore(tmp_path, require_integrity=True)

    assert strict_store.load_manifest("narrative_studio").id == "narrative_studio"
    (package / "dist" / "assets" / "app.js").write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="integrity check failed"):
        strict_store.load_manifest("narrative_studio")


def test_unsigned_download_requires_trusted_publisher(tmp_path: Path) -> None:
    package = _package(tmp_path)
    manifest = WorkbenchPackageStore(tmp_path).load_manifest("narrative_studio")

    with pytest.raises(ValueError, match="trusted publisher signature is required"):
        verify_workbench_package_trust(package, manifest, require_trusted=True)

