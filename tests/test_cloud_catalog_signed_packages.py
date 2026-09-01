from __future__ import annotations

import base64
import json
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from runtime.platform.plugins import cloud_catalog
from runtime.platform.plugins.cloud_catalog import CloudCatalog
from runtime.platform.plugins.marketplace_package import (
    CODEX_SIGNATURE_RELATIVE_PATH,
    CONNECTOR_MANIFEST_RELATIVE_PATH,
    CONNECTOR_MANIFEST_SCHEMA,
    CONNECTOR_SIGNATURE_RELATIVE_PATH,
    compute_marketplace_content_provenance,
    load_marketplace_package_manifest,
)
from runtime.platform.plugins.publisher_provenance import (
    canonical_publisher_signature_payload,
)


def _package(
    tmp_path: Path,
    kind: str,
    *,
    signed: bool,
    version: str = "1.0.0",
    content: str = "reviewed package",
    generation: str = "source",
    signing_key: Ed25519PrivateKey | None = None,
    with_skill: bool = False,
    skill_contents: dict[str, str] | None = None,
    requirements: dict[str, object] | None = None,
    plugin_id: str = "documents",
) -> tuple[Path, Path | None]:
    root = tmp_path / generation / kind / plugin_id
    if kind == "codex":
        manifest = root / ".codex-plugin" / "plugin.json"
        signature_path = CODEX_SIGNATURE_RELATIVE_PATH
        manifest_payload = {
            "name": plugin_id,
            "version": version,
            "releaseNotes": f"{version}：受信插件版本。",
        }
        if requirements:
            manifest_payload["echo"] = requirements
    else:
        manifest = root / CONNECTOR_MANIFEST_RELATIVE_PATH
        signature_path = CONNECTOR_SIGNATURE_RELATIVE_PATH
        manifest_payload = {
            "schema": CONNECTOR_MANIFEST_SCHEMA,
            "id": plugin_id,
            "version": version,
            "release_summary": f"{version}：受信连接器版本。",
        }
        if requirements:
            manifest_payload.update(requirements)
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps(manifest_payload), encoding="utf-8")
    (root / "content.txt").write_text(content, encoding="utf-8")
    bundled_skills = skill_contents or ({"helper": "# Helper\n"} if with_skill else {})
    for skill_name, skill_content in bundled_skills.items():
        skill = root / "skills" / skill_name / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text(skill_content, encoding="utf-8")
    if not signed:
        return root, None

    private_key = signing_key or Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    trust_store = tmp_path / f"{generation}-{kind}-publishers.json"
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
    public_manifest = load_marketplace_package_manifest(root, package_kind=kind)
    provenance = compute_marketplace_content_provenance(
        root,
        signature_relative_path=signature_path,
    )
    payload = canonical_publisher_signature_payload(
        plugin_id=public_manifest["name"],
        version=public_manifest["version"],
        content_digest=provenance["digest"],
        publisher_id="echoai",
        key_id="release-1",
    )
    signature = root / signature_path
    signature.parent.mkdir(parents=True, exist_ok=True)
    signature.write_text(
        json.dumps(
            {
                "schema": "echo.plugin_publisher_signature.v1",
                "algorithm": "ed25519",
                "plugin_id": plugin_id,
                "version": version,
                "content_digest": provenance["digest"],
                "publisher_id": "echoai",
                "key_id": "release-1",
                "signature": base64.b64encode(private_key.sign(payload)).decode("ascii"),
            }
        ),
        encoding="utf-8",
    )
    return root, trust_store


def _catalog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    kind: str,
    source: Path,
    trust_store: Path | None,
    version: str = "1.0.0",
) -> CloudCatalog:
    packaged_root = tmp_path / "packaged-agent"
    packaged_root.mkdir(exist_ok=True)
    plugin_root = tmp_path / "installed"
    codex_cache = tmp_path / "codex-cache"
    codex_cache.mkdir(exist_ok=True)
    monkeypatch.setattr(cloud_catalog, "REPO", packaged_root)
    monkeypatch.setattr(CloudCatalog, "PLUGIN_INSTALL_ROOT", plugin_root)
    monkeypatch.setattr(CloudCatalog, "SKILLS_ROOT", tmp_path / "skills")
    monkeypatch.setattr(CloudCatalog, "CODEX_CACHE_ROOT", codex_cache)
    monkeypatch.setattr(
        CloudCatalog,
        "CONNECTOR_STATE_FILE",
        tmp_path / "connectors" / "state.json",
    )
    monkeypatch.setattr(
        CloudCatalog,
        "CAPABILITY_STATE_FILE",
        tmp_path / "capabilities" / "state.json",
    )
    if trust_store is not None:
        monkeypatch.setenv("ECHO_PLUGIN_PUBLISHER_TRUST_STORE", str(trust_store))
    else:
        monkeypatch.setenv("ECHO_PLUGIN_PUBLISHER_TRUST_STORE", str(tmp_path / "missing.json"))
    catalog = CloudCatalog("plugins", use_remote=False, use_cache=False)
    catalog_kind = "plugin" if kind == "codex" else "connector"
    catalog._store = {
        "items": [
            {
                "id": f"{kind}_documents",
                "plugin": "documents",
                "kind": catalog_kind,
                "version": version,
            }
        ]
    }
    monkeypatch.setattr(catalog, "_archive_path", lambda: tmp_path / "pack.tar.gz")

    def extract(_archive: Path, _prefix: str, destination: Path, name: str) -> Path:
        target = destination / name
        shutil.copytree(source, target)
        return target

    monkeypatch.setattr(catalog, "_extract_member", extract)
    return catalog


@pytest.mark.parametrize("kind", ["codex", "connector"])
def test_packaged_agent_installs_only_publisher_verified_packages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    source, trust_store = _package(
        tmp_path,
        kind,
        signed=True,
        requirements={"permissions": ["content.read"]},
    )
    assert trust_store is not None
    catalog = _catalog(
        tmp_path,
        monkeypatch,
        kind=kind,
        source=source,
        trust_store=trust_store,
    )

    result = catalog.install_plugin("documents", plugin_kind=kind)
    status = catalog.plugin_statuses()["documents"]

    assert result["trust"]["publisher_verified"] is True
    # Installation only stages a verified package.  Activation is a separate
    # permission-reviewed transition for every marketplace package kind.
    assert status["lifecycle_state"] == "disabled"
    assert status["permission_review_required"] is True
    assert status["permissions_granted"] == []
    assert status["permission_active"] is False
    assert status["trust"] == {
        "level": "publisher",
        "integrity_verified": True,
        "publisher_verified": True,
        "publisher_id": "echoai",
    }
    assert status["release_summary"].startswith("1.0.0：受信")


@pytest.mark.parametrize("kind", ["codex", "connector"])
def test_packaged_agent_disables_installed_package_after_content_tampering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    source, trust_store = _package(tmp_path, kind, signed=True)
    assert trust_store is not None
    catalog = _catalog(
        tmp_path,
        monkeypatch,
        kind=kind,
        source=source,
        trust_store=trust_store,
    )
    catalog.install_plugin("documents", plugin_kind=kind)
    installed_content = CloudCatalog.PLUGIN_INSTALL_ROOT / kind / "documents" / "content.txt"
    installed_content.write_text("tampered after install", encoding="utf-8")

    status = catalog.plugin_statuses()["documents"]

    assert status["lifecycle_state"] == "broken"
    assert status["enabled"] is False
    assert status["trust"]["publisher_verified"] is False
    assert "signature rejected" in status["error"]


@pytest.mark.parametrize("kind", ["codex", "connector"])
def test_packaged_agent_disables_tampered_external_skill_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    source, trust_store = _package(tmp_path, kind, signed=True, with_skill=True)
    catalog = _catalog(
        tmp_path,
        monkeypatch,
        kind=kind,
        source=source,
        trust_store=trust_store,
    )
    catalog.install_plugin("documents", plugin_kind=kind)
    (CloudCatalog.SKILLS_ROOT / "documents__helper" / "SKILL.md").write_text(
        "tampered projected skill",
        encoding="utf-8",
    )

    status = catalog.plugin_statuses()["documents"]

    assert status["lifecycle_state"] == "broken"
    assert status["enabled"] is False
    assert status["trust"]["publisher_verified"] is False
    assert "projection integrity failed" in status["error"]


@pytest.mark.parametrize("kind", ["codex", "connector"])
def test_packaged_agent_rejects_unsigned_packages_before_install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    source, trust_store = _package(tmp_path, kind, signed=False)
    catalog = _catalog(
        tmp_path,
        monkeypatch,
        kind=kind,
        source=source,
        trust_store=trust_store,
    )

    with pytest.raises(ValueError, match="trusted .* publisher signature is required"):
        catalog.install_plugin("documents", plugin_kind=kind)

    assert not (CloudCatalog.PLUGIN_INSTALL_ROOT / kind / "documents").exists()


@pytest.mark.parametrize("kind", ["codex", "connector"])
def test_packaged_agent_rejects_incompatible_signed_package_before_switch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    source, trust_store = _package(
        tmp_path,
        kind,
        signed=True,
        requirements={"host_api": ">=99,<100"},
    )
    catalog = _catalog(
        tmp_path,
        monkeypatch,
        kind=kind,
        source=source,
        trust_store=trust_store,
    )

    with pytest.raises(ValueError, match="requires host_api >=99,<100"):
        catalog.install_plugin("documents", plugin_kind=kind)

    assert not (CloudCatalog.PLUGIN_INSTALL_ROOT / kind / "documents").exists()


@pytest.mark.parametrize("kind", ["codex", "connector"])
def test_packaged_agent_rejects_missing_signed_dependency_before_switch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    source, trust_store = _package(
        tmp_path,
        kind,
        signed=True,
        requirements={
            "host_api": ">=0.2,<0.3",
            "dependencies": ["base-tools"],
        },
    )
    catalog = _catalog(
        tmp_path,
        monkeypatch,
        kind=kind,
        source=source,
        trust_store=trust_store,
    )

    with pytest.raises(KeyError, match="marketplace dependency is unavailable: base-tools"):
        catalog.install_plugin("documents", plugin_kind=kind)

    assert not (CloudCatalog.PLUGIN_INSTALL_ROOT / kind / "documents").exists()


@pytest.mark.parametrize("kind", ["codex", "connector"])
def test_packaged_agent_installs_verified_dependency_disabled_before_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    signing_key = Ed25519PrivateKey.generate()
    dependency_source, _dependency_trust = _package(
        tmp_path,
        kind,
        signed=True,
        generation="dependency",
        signing_key=signing_key,
        plugin_id="base-tools",
    )
    source, trust_store = _package(
        tmp_path,
        kind,
        signed=True,
        generation="parent",
        signing_key=signing_key,
        requirements={"dependencies": ["base-tools"]},
    )
    assert trust_store is not None
    catalog = _catalog(
        tmp_path,
        monkeypatch,
        kind=kind,
        source=source,
        trust_store=trust_store,
    )
    catalog_kind = "plugin" if kind == "codex" else "connector"
    catalog._store = {
        "items": [
            {
                "id": f"{kind}_documents",
                "plugin": "documents",
                "kind": catalog_kind,
                "version": "1.0.0",
            },
            {
                "id": f"{kind}_base_tools",
                "plugin": "base-tools",
                "kind": catalog_kind,
                "version": "1.0.0",
            },
        ]
    }

    def extract(_archive: Path, _prefix: str, destination: Path, name: str) -> Path:
        target = destination / name
        shutil.copytree(
            dependency_source if name == "base-tools" else source,
            target,
        )
        return target

    monkeypatch.setattr(catalog, "_extract_member", extract)

    result = catalog.install_plugin("documents", plugin_kind=kind)

    assert (CloudCatalog.PLUGIN_INSTALL_ROOT / kind / "base-tools").is_dir()
    assert (CloudCatalog.PLUGIN_INSTALL_ROOT / kind / "documents").is_dir()
    assert result["installed_dependencies"][0]["plugin_id"] == "base-tools"
    statuses = catalog.plugin_statuses()
    assert statuses["base-tools"]["lifecycle_state"] == "disabled"
    assert statuses["documents"]["lifecycle_state"] == "disabled"


@pytest.mark.parametrize("kind", ["codex", "connector"])
def test_parent_failure_rolls_back_new_marketplace_dependencies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    signing_key = Ed25519PrivateKey.generate()
    dependency_source, _dependency_trust = _package(
        tmp_path,
        kind,
        signed=True,
        generation="dependency-rollback",
        signing_key=signing_key,
        plugin_id="base-tools",
    )
    source, trust_store = _package(
        tmp_path,
        kind,
        signed=True,
        generation="parent-rollback",
        signing_key=signing_key,
        requirements={
            "dependencies": ["base-tools"],
            "host_api": ">=99,<100",
        },
    )
    assert trust_store is not None
    catalog = _catalog(
        tmp_path,
        monkeypatch,
        kind=kind,
        source=source,
        trust_store=trust_store,
    )
    catalog_kind = "plugin" if kind == "codex" else "connector"
    catalog._store = {
        "items": [
            {
                "id": f"{kind}_documents",
                "plugin": "documents",
                "kind": catalog_kind,
                "version": "1.0.0",
            },
            {
                "id": f"{kind}_base_tools",
                "plugin": "base-tools",
                "kind": catalog_kind,
                "version": "1.0.0",
            },
        ]
    }

    def extract(_archive: Path, _prefix: str, destination: Path, name: str) -> Path:
        target = destination / name
        shutil.copytree(
            dependency_source if name == "base-tools" else source,
            target,
        )
        return target

    monkeypatch.setattr(catalog, "_extract_member", extract)

    with pytest.raises(ValueError, match="requires host_api >=99,<100"):
        catalog.install_plugin("documents", plugin_kind=kind)

    assert not (CloudCatalog.PLUGIN_INSTALL_ROOT / kind / "documents").exists()
    assert not (CloudCatalog.PLUGIN_INSTALL_ROOT / kind / "base-tools").exists()
    assert catalog._marketplace_permission_store().get("base-tools") is None


@pytest.mark.parametrize("kind", ["codex", "connector"])
def test_marketplace_update_is_atomic_and_can_restore_previous_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    signing_key = Ed25519PrivateKey.generate()
    first_source, first_trust = _package(
        tmp_path,
        kind,
        signed=True,
        version="1.0.0",
        content="first generation",
        generation="first",
        signing_key=signing_key,
    )
    first_catalog = _catalog(
        tmp_path,
        monkeypatch,
        kind=kind,
        source=first_source,
        trust_store=first_trust,
        version="1.0.0",
    )
    first_catalog.install_plugin("documents", plugin_kind=kind)
    permission_store = first_catalog._marketplace_permission_store()
    permission_store.grant("documents", [])
    permission_store.set_active("documents", True)
    # Exercise rollback from an explicitly activated generation. Installation
    # itself now stages every package disabled until its permissions are granted.
    state_file = (
        CloudCatalog.CONNECTOR_STATE_FILE
        if kind == "connector"
        else CloudCatalog.CAPABILITY_STATE_FILE
    )
    state = json.loads(state_file.read_text(encoding="utf-8"))
    state["documents"]["enabled"] = True
    state_file.write_text(json.dumps(state), encoding="utf-8")

    second_source, second_trust = _package(
        tmp_path,
        kind,
        signed=True,
        version="2.0.0",
        content="second generation",
        generation="second",
        signing_key=signing_key,
    )
    second_catalog = _catalog(
        tmp_path,
        monkeypatch,
        kind=kind,
        source=second_source,
        trust_store=second_trust,
        version="2.0.0",
    )
    updated = second_catalog.install_plugin("documents", plugin_kind=kind)
    target = CloudCatalog.PLUGIN_INSTALL_ROOT / kind / "documents" / "content.txt"

    assert updated["operation"] == "update"
    assert updated["rollback_available"] is True
    assert target.read_text(encoding="utf-8") == "second generation"
    assert permission_store.get("documents")["active"] is False

    rolled_back = second_catalog.rollback_plugin(
        "documents",
        plugin_kind=kind,
        transaction_id=updated["transaction_id"],
    )

    assert rolled_back["operation"] == "restored_previous"
    assert target.read_text(encoding="utf-8") == "first generation"
    assert permission_store.get("documents")["active"] is True
    status = second_catalog.plugin_statuses()["documents"]
    assert status["lifecycle_state"] == "update_available"
    assert status["version"] == "1.0.0"


@pytest.mark.parametrize("kind", ["codex", "connector"])
def test_invalid_marketplace_update_never_replaces_last_good_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    first_source, first_trust = _package(
        tmp_path,
        kind,
        signed=True,
        content="last good generation",
        generation="first",
    )
    catalog = _catalog(
        tmp_path,
        monkeypatch,
        kind=kind,
        source=first_source,
        trust_store=first_trust,
    )
    catalog.install_plugin("documents", plugin_kind=kind)

    broken_source, broken_trust = _package(
        tmp_path,
        kind,
        signed=True,
        content="candidate generation",
        generation="broken",
    )
    (broken_source / "content.txt").write_text("tampered candidate", encoding="utf-8")
    broken_catalog = _catalog(
        tmp_path,
        monkeypatch,
        kind=kind,
        source=broken_source,
        trust_store=broken_trust,
    )

    with pytest.raises(ValueError, match="signature rejected"):
        broken_catalog.install_plugin("documents", plugin_kind=kind)

    target = CloudCatalog.PLUGIN_INSTALL_ROOT / kind / "documents" / "content.txt"
    assert target.read_text(encoding="utf-8") == "last good generation"


@pytest.mark.parametrize("kind", ["codex", "connector"])
def test_marketplace_install_restores_everything_when_state_commit_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    source, trust_store = _package(
        tmp_path,
        kind,
        signed=True,
        with_skill=True,
    )
    catalog = _catalog(
        tmp_path,
        monkeypatch,
        kind=kind,
        source=source,
        trust_store=trust_store,
    )
    state_path = (
        CloudCatalog.CAPABILITY_STATE_FILE if kind == "codex" else CloudCatalog.CONNECTOR_STATE_FILE
    )
    original_atomic_write = cloud_catalog.atomic_write_json

    def fail_state_commit(path, payload, **kwargs):
        if Path(path) == state_path:
            raise OSError("simulated state commit failure")
        return original_atomic_write(path, payload, **kwargs)

    monkeypatch.setattr(cloud_catalog, "atomic_write_json", fail_state_commit)

    with pytest.raises(OSError, match="simulated state commit failure"):
        catalog.install_plugin("documents", plugin_kind=kind)

    assert not (CloudCatalog.PLUGIN_INSTALL_ROOT / kind / "documents").exists()
    assert not (CloudCatalog.SKILLS_ROOT / "documents__helper").exists()


@pytest.mark.parametrize("kind", ["codex", "connector"])
def test_marketplace_rollback_restores_package_skills_registry_and_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    signing_key = Ed25519PrivateKey.generate()
    first_source, first_trust = _package(
        tmp_path,
        kind,
        signed=True,
        version="1.0.0",
        content="first package",
        generation="first",
        signing_key=signing_key,
        skill_contents={"helper": "# First helper\n"},
    )
    first_catalog = _catalog(
        tmp_path,
        monkeypatch,
        kind=kind,
        source=first_source,
        trust_store=first_trust,
        version="1.0.0",
    )
    first_catalog.install_plugin("documents", plugin_kind=kind)
    state_path = (
        CloudCatalog.CAPABILITY_STATE_FILE if kind == "codex" else CloudCatalog.CONNECTOR_STATE_FILE
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["documents"].update(enabled=True, account="preserved-account")
    state_path.write_text(json.dumps(state), encoding="utf-8")
    expected_previous_state = dict(state["documents"])

    second_source, second_trust = _package(
        tmp_path,
        kind,
        signed=True,
        version="2.0.0",
        content="second package",
        generation="second",
        signing_key=signing_key,
        skill_contents={
            "helper": "# Second helper\n",
            "extra": "# Extra skill\n",
        },
    )
    second_catalog = _catalog(
        tmp_path,
        monkeypatch,
        kind=kind,
        source=second_source,
        trust_store=second_trust,
        version="2.0.0",
    )
    updated = second_catalog.install_plugin("documents", plugin_kind=kind)

    assert updated["rollback_available"] is True
    projected_status = second_catalog.plugin_statuses()["documents"]
    assert projected_status["rollback_available"] is True
    assert projected_status["transaction_id"] == updated["transaction_id"]
    assert (CloudCatalog.SKILLS_ROOT / "documents__helper" / "SKILL.md").read_text(
        encoding="utf-8"
    ) == "# Second helper\n"
    assert (CloudCatalog.SKILLS_ROOT / "documents__extra" / "SKILL.md").is_file()
    assert json.loads(state_path.read_text(encoding="utf-8"))["documents"]["account"] == (
        "preserved-account"
    )

    second_catalog.rollback_plugin(
        "documents",
        plugin_kind=kind,
        transaction_id=updated["transaction_id"],
    )

    assert (CloudCatalog.PLUGIN_INSTALL_ROOT / kind / "documents" / "content.txt").read_text(
        encoding="utf-8"
    ) == "first package"
    assert (CloudCatalog.SKILLS_ROOT / "documents__helper" / "SKILL.md").read_text(
        encoding="utf-8"
    ) == "# First helper\n"
    assert not (CloudCatalog.SKILLS_ROOT / "documents__extra").exists()
    registry = json.loads((CloudCatalog.SKILLS_ROOT / "registry.json").read_text("utf-8"))
    assert [row["name"] for row in registry if row["name"].startswith("documents__")] == [
        "documents__helper"
    ]
    assert json.loads(state_path.read_text(encoding="utf-8"))["documents"] == (
        expected_previous_state
    )


@pytest.mark.parametrize("kind", ["codex", "connector"])
def test_marketplace_first_install_rollback_removes_all_owned_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    source, trust_store = _package(
        tmp_path,
        kind,
        signed=True,
        with_skill=True,
    )
    catalog = _catalog(
        tmp_path,
        monkeypatch,
        kind=kind,
        source=source,
        trust_store=trust_store,
    )

    installed = catalog.install_plugin("documents", plugin_kind=kind)
    assert installed["rollback_available"] is True
    catalog.rollback_plugin(
        "documents",
        plugin_kind=kind,
        transaction_id=installed["transaction_id"],
    )

    assert not (CloudCatalog.PLUGIN_INSTALL_ROOT / kind / "documents").exists()
    assert not (CloudCatalog.SKILLS_ROOT / "documents__helper").exists()
    registry = json.loads((CloudCatalog.SKILLS_ROOT / "registry.json").read_text("utf-8"))
    assert not any(row["name"].startswith("documents__") for row in registry)
    state_path = (
        CloudCatalog.CAPABILITY_STATE_FILE if kind == "codex" else CloudCatalog.CONNECTOR_STATE_FILE
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert "documents" not in state


def test_marketplace_rollback_rejects_tampered_skill_backup_without_switching(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signing_key = Ed25519PrivateKey.generate()
    first_source, first_trust = _package(
        tmp_path,
        "connector",
        signed=True,
        version="1.0.0",
        content="first package",
        generation="first",
        signing_key=signing_key,
        skill_contents={"helper": "# First helper\n"},
    )
    first_catalog = _catalog(
        tmp_path,
        monkeypatch,
        kind="connector",
        source=first_source,
        trust_store=first_trust,
        version="1.0.0",
    )
    first_catalog.install_plugin("documents", plugin_kind="connector")
    second_source, second_trust = _package(
        tmp_path,
        "connector",
        signed=True,
        version="2.0.0",
        content="second package",
        generation="second",
        signing_key=signing_key,
        skill_contents={"helper": "# Second helper\n"},
    )
    catalog = _catalog(
        tmp_path,
        monkeypatch,
        kind="connector",
        source=second_source,
        trust_store=second_trust,
        version="2.0.0",
    )
    updated = catalog.install_plugin("documents", plugin_kind="connector")
    backup_skill = (
        CloudCatalog.SKILLS_ROOT
        / ".lifecycle"
        / "marketplace"
        / updated["transaction_id"]
        / "backup"
        / "documents__helper"
        / "SKILL.md"
    )
    backup_skill.write_text("tampered backup", encoding="utf-8")

    with pytest.raises(ValueError, match="projection integrity failed"):
        catalog.rollback_plugin(
            "documents",
            plugin_kind="connector",
            transaction_id=updated["transaction_id"],
        )

    assert (CloudCatalog.PLUGIN_INSTALL_ROOT / "connector" / "documents" / "content.txt").read_text(
        "utf-8"
    ) == "second package"
    assert (CloudCatalog.SKILLS_ROOT / "documents__helper" / "SKILL.md").read_text(
        "utf-8"
    ) == "# Second helper\n"


@pytest.mark.parametrize("kind", ["codex", "connector"])
@pytest.mark.parametrize("interrupted_after", ["package", "skills-state"])
def test_marketplace_status_recovers_interrupted_cross_directory_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    interrupted_after: str,
) -> None:
    signing_key = Ed25519PrivateKey.generate()
    first_source, first_trust = _package(
        tmp_path,
        kind,
        signed=True,
        version="1.0.0",
        content="stable package",
        generation="first",
        signing_key=signing_key,
        skill_contents={"helper": "# Stable helper\n"},
    )
    first_catalog = _catalog(
        tmp_path,
        monkeypatch,
        kind=kind,
        source=first_source,
        trust_store=first_trust,
        version="1.0.0",
    )
    first_catalog.install_plugin("documents", plugin_kind=kind)
    state_path = (
        CloudCatalog.CAPABILITY_STATE_FILE if kind == "codex" else CloudCatalog.CONNECTOR_STATE_FILE
    )
    stable_state = json.loads(state_path.read_text(encoding="utf-8"))
    stable_state["documents"]["recovery_marker"] = "stable"
    state_path.write_text(json.dumps(stable_state), encoding="utf-8")

    second_source, second_trust = _package(
        tmp_path,
        kind,
        signed=True,
        version="2.0.0",
        content="interrupted package",
        generation="second",
        signing_key=signing_key,
        skill_contents={"helper": "# Interrupted helper\n"},
    )
    catalog = _catalog(
        tmp_path,
        monkeypatch,
        kind=kind,
        source=second_source,
        trust_store=second_trust,
        version="2.0.0",
    )
    dest = CloudCatalog.PLUGIN_INSTALL_ROOT / kind
    record = catalog._commit_marketplace_package(
        second_source,
        target=dest / "documents",
        dest=dest,
        plugin_id="documents",
        package_kind=kind,
        expected_version="2.0.0",
        require_trusted=True,
    )
    if interrupted_after == "skills-state":
        state_before = catalog._marketplace_state_snapshot(
            "documents",
            plugin_kind=kind,
        )
        record.update(
            {
                "skills": catalog._commit_marketplace_skill_generation(
                    dest / "documents",
                    plugin_id="documents",
                    transaction_id=record["transaction_id"],
                ),
                "state_before": state_before,
                "status": "skills_committed",
            }
        )
        catalog._write_marketplace_transaction(record, dest=dest)
        catalog._commit_marketplace_installed_state(
            "documents",
            plugin_kind=kind,
            previous=state_before,
        )

    assert (dest / "documents" / "content.txt").read_text("utf-8") == ("interrupted package")
    catalog.installed_plugins()

    assert (dest / "documents" / "content.txt").read_text("utf-8") == "stable package"
    assert (CloudCatalog.SKILLS_ROOT / "documents__helper" / "SKILL.md").read_text(
        "utf-8"
    ) == "# Stable helper\n"
    assert (
        json.loads(state_path.read_text(encoding="utf-8"))["documents"]["recovery_marker"]
        == "stable"
    )
    transaction_path = dest / ".lifecycle" / "transactions" / f"{record['transaction_id']}.json"
    assert json.loads(transaction_path.read_text(encoding="utf-8"))["status"] == "aborted"


@pytest.mark.parametrize("skill_phase", ["before-switch", "mid-switch"])
def test_marketplace_status_recovers_partial_skill_directory_switch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    skill_phase: str,
) -> None:
    signing_key = Ed25519PrivateKey.generate()
    first_source, first_trust = _package(
        tmp_path,
        "connector",
        signed=True,
        version="1.0.0",
        content="stable package",
        generation="first",
        signing_key=signing_key,
        skill_contents={"helper": "# Stable helper\n"},
    )
    first_catalog = _catalog(
        tmp_path,
        monkeypatch,
        kind="connector",
        source=first_source,
        trust_store=first_trust,
        version="1.0.0",
    )
    first_catalog.install_plugin("documents", plugin_kind="connector")
    second_source, second_trust = _package(
        tmp_path,
        "connector",
        signed=True,
        version="2.0.0",
        content="interrupted package",
        generation="second",
        signing_key=signing_key,
        skill_contents={
            "helper": "# Interrupted helper\n",
            "extra": "# Extra helper\n",
        },
    )
    catalog = _catalog(
        tmp_path,
        monkeypatch,
        kind="connector",
        source=second_source,
        trust_store=second_trust,
        version="2.0.0",
    )
    dest = CloudCatalog.PLUGIN_INSTALL_ROOT / "connector"
    record = catalog._commit_marketplace_package(
        second_source,
        target=dest / "documents",
        dest=dest,
        plugin_id="documents",
        package_kind="connector",
        expected_version="2.0.0",
        require_trusted=True,
    )
    transaction_id = record["transaction_id"]
    skill_lifecycle = CloudCatalog.SKILLS_ROOT / ".lifecycle" / "marketplace" / transaction_id
    backup = skill_lifecycle / "backup"
    staging = skill_lifecycle / "staging"
    backup.mkdir(parents=True)
    staging.mkdir()
    generation = {
        "schema": "echo.marketplace_skill_generation.v1",
        "skill_ids": ["documents__extra", "documents__helper"],
        "previous_skill_ids": ["documents__helper"],
        "registry_entries": [
            catalog._marketplace_skill_record("documents", "documents__extra"),
            catalog._marketplace_skill_record("documents", "documents__helper"),
        ],
        "previous_registry_entries": [
            catalog._marketplace_skill_record("documents", "documents__helper")
        ],
    }
    cloud_catalog.atomic_write_json(
        skill_lifecycle / "generation.json",
        generation,
        sort_keys=True,
    )
    if skill_phase == "mid-switch":
        (CloudCatalog.SKILLS_ROOT / "documents__helper").replace(backup / "documents__helper")
        shutil.copytree(
            second_source / "skills" / "helper",
            CloudCatalog.SKILLS_ROOT / "documents__helper",
        )
        shutil.copytree(
            second_source / "skills" / "extra",
            staging / "documents__extra",
        )

    catalog.installed_plugins()

    assert (dest / "documents" / "content.txt").read_text("utf-8") == "stable package"
    assert (CloudCatalog.SKILLS_ROOT / "documents__helper" / "SKILL.md").read_text(
        "utf-8"
    ) == "# Stable helper\n"
    assert not (CloudCatalog.SKILLS_ROOT / "documents__extra").exists()


def test_marketplace_installs_serialize_the_shared_package_and_skill_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, trust_store = _package(
        tmp_path,
        "connector",
        signed=True,
        with_skill=True,
    )
    catalog = _catalog(
        tmp_path,
        monkeypatch,
        kind="connector",
        source=source,
        trust_store=trust_store,
    )
    original_extract = catalog._extract_member
    counter_lock = threading.Lock()
    active = 0
    max_active = 0

    def observed_extract(*args, **kwargs):
        nonlocal active, max_active
        with counter_lock:
            active += 1
            max_active = max(max_active, active)
        try:
            time.sleep(0.03)
            return original_extract(*args, **kwargs)
        finally:
            with counter_lock:
                active -= 1

    monkeypatch.setattr(catalog, "_extract_member", observed_extract)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _index: catalog.install_plugin(
                    "documents",
                    plugin_kind="connector",
                ),
                range(2),
            )
        )

    assert max_active == 1
    assert sorted(result["operation"] for result in results) == ["install", "update"]
    assert (CloudCatalog.SKILLS_ROOT / "documents__helper" / "SKILL.md").is_file()


@pytest.mark.parametrize("operation", ["install", "update"])
def test_marketplace_status_recovers_package_switch_before_main_journal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    stable_source, stable_trust = _package(
        tmp_path,
        "connector",
        signed=True,
        content="stable package",
        generation="stable",
    )
    catalog = _catalog(
        tmp_path,
        monkeypatch,
        kind="connector",
        source=stable_source,
        trust_store=stable_trust,
    )
    dest = CloudCatalog.PLUGIN_INSTALL_ROOT / "connector"
    target = dest / "documents"
    if operation == "update":
        catalog.install_plugin("documents", plugin_kind="connector")
    transaction_id = uuid.uuid4().hex
    staging = dest / ".lifecycle" / "staging" / transaction_id
    staging.mkdir(parents=True)
    cloud_catalog.atomic_write_json(
        staging / "intent.json",
        {
            "schema": "echo.marketplace_package_intent.v1",
            "transaction_id": transaction_id,
            "plugin_id": "documents",
            "kind": "connector",
            "operation": operation,
        },
        sort_keys=True,
    )
    if operation == "update":
        backup = dest / ".lifecycle" / "backups" / transaction_id / "documents"
        backup.parent.mkdir(parents=True)
        target.replace(backup)
    shutil.copytree(stable_source, target)
    (target / "content.txt").write_text("orphaned candidate", encoding="utf-8")

    catalog.installed_plugins()

    if operation == "update":
        assert target.is_dir()
        assert target.joinpath("content.txt").read_text("utf-8") == "stable package"
    else:
        assert not target.exists()
    assert not staging.exists()


def test_marketplace_uninstall_invalidates_prior_rollback_generations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, trust_store = _package(
        tmp_path,
        "connector",
        signed=True,
        with_skill=True,
    )
    catalog = _catalog(
        tmp_path,
        monkeypatch,
        kind="connector",
        source=source,
        trust_store=trust_store,
    )
    installed = catalog.install_plugin("documents", plugin_kind="connector")

    catalog.uninstall_plugin("documents", plugin_kind="connector")

    permission = catalog._marketplace_permission_store().get("documents")
    assert permission["installed"] is False
    assert permission["active"] is False
    assert permission["granted"] == []

    transaction_path = (
        CloudCatalog.PLUGIN_INSTALL_ROOT
        / "connector"
        / ".lifecycle"
        / "transactions"
        / f"{installed['transaction_id']}.json"
    )
    assert json.loads(transaction_path.read_text(encoding="utf-8"))["status"] == (
        "invalidated_uninstalled"
    )
    with pytest.raises(ValueError, match="invalid, unavailable, or already consumed"):
        catalog.rollback_plugin(
            "documents",
            plugin_kind="connector",
            transaction_id=installed["transaction_id"],
        )



