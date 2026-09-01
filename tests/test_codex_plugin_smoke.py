from __future__ import annotations

import base64
import json
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.platform.plugins.codex_discovery import discover_codex_plugins
from runtime.platform.plugins.publisher_provenance import (
    canonical_publisher_signature_payload,
)
from runtime.safety.auth import Identity, IdentityStore
from runtime.safety.evolution.plugin_migration_readiness import (
    compute_plugin_migration_readiness,
)
from runtime.sensing.gateway.plugins_router import create_plugins_router


def test_codex_plugin_discovery_includes_smoke_metadata(tmp_path: Path) -> None:
    plugin_dir = _write_plugin(tmp_path)

    plugins = discover_codex_plugins([tmp_path])

    assert [plugin["id"] for plugin in plugins] == ["research"]
    smoke = plugins[0]["smoke"]
    assert smoke["schema"] == "echo.codex_plugin_smoke.v1"
    assert smoke["ok"] is True
    assert smoke["surfaces"]["skills"] is True
    assert smoke["surfaces"]["mcp"] is True
    assert smoke["trust"]["level"] == "local_review_required"
    assert smoke["content_provenance"]["schema"] == ("echo.plugin_content_provenance.v1")
    assert smoke["content_provenance"]["complete"] is True
    assert len(smoke["content_provenance"]["digest"]) == 64
    assert smoke["content_provenance"]["file_count"] == 3
    assert smoke["permission_resolution"]["status"] == "review_required"
    assert smoke["permission_resolution"]["permissions"] == [
        "mcp:execute:review_required",
        "ui:metadata:local",
    ]
    assert plugin_dir.name == "research"


def test_plugin_content_provenance_changes_with_runtime_content(tmp_path: Path) -> None:
    plugin_dir = _write_plugin(tmp_path)
    first = discover_codex_plugins([tmp_path])[0]["smoke"]["content_provenance"]

    (plugin_dir / "skills" / "brief" / "SKILL.md").write_text(
        "# Brief\n\nChanged instructions.\n",
        encoding="utf-8",
    )
    second = discover_codex_plugins([tmp_path])[0]["smoke"]["content_provenance"]

    assert first["digest"] != second["digest"]
    assert first["file_count"] == second["file_count"] == 3


def test_plugin_content_provenance_rejects_symlink_escape(tmp_path: Path) -> None:
    plugin_dir = _write_plugin(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    (plugin_dir / "outside-link.txt").symlink_to(outside)

    smoke = discover_codex_plugins([tmp_path])[0]["smoke"]
    provenance = smoke["content_provenance"]

    assert provenance["complete"] is False
    assert provenance["digest"] == ""
    assert any("symlink file excluded" in issue for issue in provenance["issues"])
    assert any("symlink file excluded" in warning for warning in smoke["warnings"])


def test_plugin_publisher_signature_verifies_against_operator_trust_store(
    tmp_path: Path,
) -> None:
    plugin_dir = _write_plugin(tmp_path, explicit_permissions=True)
    trust_store = _sign_plugin(plugin_dir)

    plugin = discover_codex_plugins(
        [tmp_path],
        publisher_trust_store_path=trust_store,
    )[0]
    smoke = plugin["smoke"]
    publisher = smoke["publisher_provenance"]

    assert smoke["ok"] is True
    assert smoke["trust"]["level"] == "publisher_verified"
    assert smoke["trust"]["signed"] is True
    assert smoke["content_provenance"]["signed"] is True
    assert smoke["content_provenance"]["file_count"] == 3
    assert publisher["schema"] == "echo.plugin_publisher_provenance.v1"
    assert publisher["status"] == "verified"
    assert publisher["verified"] is True
    assert publisher["trusted"] is True
    assert publisher["publisher_id"] == "acme"
    assert publisher["key_id"] == "release-2026"
    assert len(publisher["signature_digest"]) == 64


def test_plugin_publisher_signature_blocks_runtime_tampering(tmp_path: Path) -> None:
    plugin_dir = _write_plugin(tmp_path, explicit_permissions=True)
    trust_store = _sign_plugin(plugin_dir)
    (plugin_dir / "skills" / "brief" / "SKILL.md").write_text(
        "# Brief\n\nTampered after signing.\n",
        encoding="utf-8",
    )

    smoke = discover_codex_plugins(
        [tmp_path],
        publisher_trust_store_path=trust_store,
    )[0]["smoke"]

    assert smoke["ok"] is False
    assert smoke["publisher_provenance"]["status"] == "tampered"
    assert smoke["publisher_provenance"]["verified"] is False
    assert any("content digest does not match" in issue for issue in smoke["issues"])


def test_plugin_publisher_signature_blocks_revoked_key(tmp_path: Path) -> None:
    plugin_dir = _write_plugin(tmp_path, explicit_permissions=True)
    trust_store = _sign_plugin(plugin_dir, key_status="revoked")

    smoke = discover_codex_plugins(
        [tmp_path],
        publisher_trust_store_path=trust_store,
    )[0]["smoke"]

    assert smoke["ok"] is False
    assert smoke["publisher_provenance"]["status"] == "revoked"
    assert any("key is not active" in issue for issue in smoke["issues"])


def test_plugin_smoke_summary_reports_publisher_provenance(tmp_path: Path) -> None:
    plugin_dir = _write_plugin(tmp_path, explicit_permissions=True)
    trust_store = _sign_plugin(plugin_dir)
    app = FastAPI()
    app.include_router(
        create_plugins_router(
            plugin_roots=[tmp_path],
            publisher_trust_store_path=trust_store,
        )
    )
    client = TestClient(app)

    data = client.get("/api/plugins/smoke-summary").json()

    assert data["publisher_verified_count"] == 1
    assert data["unsigned_count"] == 0
    assert data["invalid_signature_count"] == 0
    assert data["publisher_provenance"][0]["status"] == "verified"
    assert data["review_required_count"] == 0


def test_publisher_trust_endpoints_rotate_revoke_and_audit(tmp_path: Path) -> None:
    plugin_dir = _write_plugin(tmp_path, explicit_permissions=True)
    trust_store = _sign_plugin(plugin_dir)
    audit_path = tmp_path / "promotion-audit.json"
    new_private_key = Ed25519PrivateKey.generate()
    new_public_key = new_private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    app = FastAPI()
    app.include_router(
        create_plugins_router(
            plugin_roots=[tmp_path],
            publisher_trust_store_path=trust_store,
            promotion_audit_path=audit_path,
        )
    )
    client = TestClient(app)

    initial = client.get("/api/plugins/publisher-trust")
    assert initial.status_code == 200
    assert initial.json()["active_key_count"] == 1
    assert initial.json()["publishers"][0]["keys"][0]["public_key_fingerprint"].startswith(
        "sha256:"
    )
    assert "public_key" not in initial.json()["publishers"][0]["keys"][0]

    rejected = client.post(
        "/api/plugins/publisher-trust/rotate",
        json={"publisher_id": "acme"},
    )
    assert rejected.status_code == 400

    rotated = client.post(
        "/api/plugins/publisher-trust/rotate",
        json={
            "publisher_id": "acme",
            "previous_key_id": "release-2026",
            "new_key_id": "release-2026-q3",
            "new_public_key": base64.b64encode(new_public_key).decode("ascii"),
            "reason": "quarterly rotation",
            "confirm_rotation": True,
        },
    )
    assert rotated.status_code == 200
    assert rotated.json()["status"] == "rotated"
    keys = rotated.json()["trust"]["publishers"][0]["keys"]
    assert [(key["key_id"], key["status"]) for key in keys] == [
        ("release-2026", "retired"),
        ("release-2026-q3", "active"),
    ]

    revoked = client.post(
        "/api/plugins/publisher-trust/revoke",
        json={
            "publisher_id": "acme",
            "key_id": "release-2026-q3",
            "reason": "key compromise drill",
            "confirm_revocation": True,
        },
    )
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"
    assert revoked.json()["trust"]["active_key_count"] == 0

    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert [row["event_type"] for row in audit["records"]] == [
        "plugin_publisher_key_rotation",
        "plugin_publisher_key_revocation",
    ]
    assert "new_public_key" not in json.dumps(audit)


def test_codex_plugin_smoke_endpoint(tmp_path: Path) -> None:
    _write_plugin(tmp_path)
    app = FastAPI()
    app.include_router(create_plugins_router(plugin_roots=[tmp_path]))
    client = TestClient(app)

    response = client.get("/api/plugins/research/smoke")

    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_codex_plugin_smoke_summary_endpoint(tmp_path: Path) -> None:
    _write_plugin(tmp_path)
    plugin_dir = tmp_path / "empty"
    (plugin_dir / ".codex-plugin").mkdir(parents=True)
    (plugin_dir / ".codex-plugin" / "plugin.json").write_text(
        json.dumps({"name": "empty", "version": "0.1.0"}),
        encoding="utf-8",
    )
    app = FastAPI()
    app.include_router(create_plugins_router(plugin_roots=[tmp_path]))
    client = TestClient(app)

    response = client.get("/api/plugins/smoke-summary")

    assert response.status_code == 200
    data = response.json()
    assert data["schema"] == "echo.codex_plugin_smoke_summary.v1"
    assert data["total"] == 2
    assert data["ok_count"] == 1
    assert data["failed_count"] == 1
    assert data["review_required_count"] == 2
    assert data["warning_count"] == 1
    assert data["failed"][0]["plugin_id"] == "empty"
    assert data["warnings"][0]["plugin_id"] == "research"
    assert data["permission_resolutions"][0]["schema"] == (
        "echo.codex_plugin_permission_resolution.v1"
    )
    assert data["compatibility"]["schema"] == "echo.codex_plugin_compatibility.v1"
    assert data["compatibility"]["verdict"] == "fail"
    assert data["compatibility"]["surface_totals"]["skills"] == 1
    assert data["compatibility"]["surface_totals"]["mcp"] == 1
    assert data["migration_readiness"]["schema"] == ("echo.plugin_migration_readiness.v1")
    assert data["migration_readiness"]["total"] == 2
    assert data["migration_readiness"]["ready"] is False
    assert data["migration_readiness"]["blocked_count"] == 2
    assert any(
        item["id"] == "no_smoke_failures" and item["passed"] is False
        for item in data["compatibility"]["requirements"]
    )


def test_codex_plugin_smoke_summary_marks_review_compatible_set(tmp_path: Path) -> None:
    _write_plugin(tmp_path)
    app = FastAPI()
    app.include_router(create_plugins_router(plugin_roots=[tmp_path]))
    client = TestClient(app)

    response = client.get("/api/plugins/smoke-summary")

    assert response.status_code == 200
    data = response.json()
    assert data["failed_count"] == 0
    assert data["compatibility"]["verdict"] == "review"
    assert data["compatibility"]["passed"] == data["compatibility"]["total"]
    assert data["compatibility"]["next_actions"] == [
        "Resolve inferred plugin permission defaults or mark accepted risk.",
        "Resolve plugin warnings or mark accepted risk.",
    ]
    assert data["permission_rule_drafts"]["schema"] == ("echo.plugin_permission_rule_drafts.v1")
    assert data["permission_rule_drafts"]["total"] == 2
    assert data["permission_rule_drafts"]["verified"] == 2
    assert data["migration_readiness"]["ready"] is False
    assert data["migration_readiness"]["blocked_count"] == 1


def test_plugin_migration_readiness_endpoint_requires_contract_artifacts(
    tmp_path: Path,
) -> None:
    _write_plugin(tmp_path)
    app = FastAPI()
    app.include_router(create_plugins_router(plugin_roots=[tmp_path]))
    client = TestClient(app)

    response = client.get("/api/plugins/migration-readiness")

    assert response.status_code == 200
    data = response.json()
    assert data["schema"] == "echo.plugin_migration_readiness.v1"
    assert data["total"] == 1
    assert data["ready"] is False
    assert data["ready_count"] == 0
    assert data["blocked_count"] == 1
    plugin = data["plugins"][0]
    assert plugin["schema"] == "echo.plugin_migration_contract.v1"
    assert plugin["migration_contract"]["schema"] == ("echo.plugin_migration_contract.v1")
    assert "plugin migration notes are missing" in plugin["blockers"]
    assert data["next_actions"] == [
        "Add migration notes for research.",
    ]


def test_plugin_migration_readiness_endpoint_marks_release_ready_plugin(
    tmp_path: Path,
) -> None:
    _write_plugin(tmp_path, include_migration_contract=True)
    app = FastAPI()
    app.include_router(create_plugins_router(plugin_roots=[tmp_path]))
    client = TestClient(app)

    response = client.get("/api/plugins/migration-readiness")

    assert response.status_code == 200
    data = response.json()
    assert data["schema"] == "echo.plugin_migration_readiness.v1"
    assert data["score"] == 1.0
    assert data["ready"] is True
    assert data["ready_count"] == 1
    assert data["blocked_count"] == 0
    assert data["plugins"][0]["ready"] is True
    assert data["plugins"][0]["blockers"] == []
    assert data["plugins"][0]["migration_contract"]["migration_notes_present"] is True
    assert data["plugins"][0]["migration_contract"]["regression_tests_present"] is True
    provenance = data["plugins"][0]["migration_contract"]["content_provenance"]
    assert provenance["complete"] is True
    assert len(provenance["digest"]) == 64
    assert provenance["signed"] is False


def test_plugin_migration_readiness_accepts_central_contract_matrix(
    tmp_path: Path,
) -> None:
    _write_plugin(tmp_path)
    docs = tmp_path / "docs/guide"
    docs.mkdir(parents=True)
    (docs / "plugin-migration-matrix.md").write_text(
        "| Plugin | Evidence |\n| --- | --- |\n| `research` | central migration contract |\n",
        encoding="utf-8",
    )
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    for name in (
        "test_codex_plugin_smoke.py",
        "test_app_meta_endpoints.py",
        "test_apps_router.py",
    ):
        (tests_dir / name).write_text("def test_plugin():\n    assert True\n", encoding="utf-8")

    report = compute_plugin_migration_readiness(
        plugins=discover_codex_plugins([tmp_path]),
        root=tmp_path,
    )

    assert report["ready"] is True
    assert report["ready_count"] == 1
    assert report["central_contract"]["covered_count"] == 1
    contract = report["plugins"][0]["migration_contract"]
    assert contract["central_migration_covered"] is True
    assert contract["central_regression_tests_present"] is True
    assert contract["migration_notes_present"] is False


def test_plugin_permission_rule_drafts_endpoint_and_install(tmp_path: Path) -> None:
    from runtime.safety.approval.approval_policy_store import load_policy

    _write_plugin(tmp_path)
    approval_policy_path = tmp_path / "permissions.json"
    audit_path = tmp_path / "promotion_audit.json"
    app = FastAPI()
    app.include_router(
        create_plugins_router(
            plugin_roots=[tmp_path],
            approval_policy_path=approval_policy_path,
            promotion_audit_path=audit_path,
        )
    )
    client = TestClient(app)

    drafts_response = client.get("/api/plugins/permission-rule-drafts")
    drafts = drafts_response.json()
    draft = next(
        item
        for item in drafts["drafts"]
        if item["signed_payload"]["rule"]["tool"] == "mcp__research__*"
    )
    missing_confirm = client.post(
        "/api/plugins/permission-rule-drafts/install",
        json={"draft_id": draft["draft_id"]},
    )
    installed = client.post(
        "/api/plugins/permission-rule-drafts/install",
        json={"draft_id": draft["draft_id"], "confirm_install": True},
    )
    policy = load_policy(approval_policy_path)

    assert drafts_response.status_code == 200
    assert drafts["schema"] == "echo.plugin_permission_rule_drafts.v1"
    assert drafts["total"] == 2
    assert drafts["verified"] == 2
    assert missing_confirm.status_code == 400
    assert missing_confirm.json()["detail"] == "confirm_install=true is required"
    assert installed.status_code == 200
    assert installed.json()["installed"] is True
    assert installed.json()["source_kind"] == "plugin_permission_review"
    assert len(policy.rules) == 1
    assert policy.rules[0].effect == "deny"
    assert policy.rules[0].tool == "mcp__research__*"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert audit["records"][0]["event_type"] == "plugin_permission_rule_install"
    assert audit["records"][0]["target"] == "approval_policy"


def test_codex_plugin_smoke_summary_guides_empty_ecosystem(tmp_path: Path) -> None:
    app = FastAPI()
    app.include_router(create_plugins_router(plugin_roots=[tmp_path]))
    client = TestClient(app)

    response = client.get("/api/plugins/smoke-summary")

    assert response.status_code == 200
    data = response.json()
    assert data["compatibility"]["verdict"] == "fail"
    assert data["compatibility"]["next_actions"] == [
        "Install or enable at least one local Codex-compatible plugin.",
        "Expose at least one plugin capability, skill, app, MCP server, or command.",
    ]


def test_codex_plugin_smoke_flags_empty_surface(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "empty"
    (plugin_dir / ".codex-plugin").mkdir(parents=True)
    (plugin_dir / ".codex-plugin" / "plugin.json").write_text(
        json.dumps({"name": "empty", "version": "0.1.0"}),
        encoding="utf-8",
    )

    plugins = discover_codex_plugins([tmp_path])

    smoke = plugins[0]["smoke"]
    assert smoke["ok"] is False
    assert any("no capabilities" in issue for issue in smoke["issues"])


def test_plugins_router_requires_auth_when_enabled(tmp_path: Path) -> None:
    _write_plugin(tmp_path)
    store = IdentityStore()
    store.add(Identity(actor_id="alice"), api_key_plaintext="sk-alice")
    app = FastAPI()
    app.include_router(
        create_plugins_router(
            plugin_roots=[tmp_path],
            identity_store=store,
            require_auth=True,
        )
    )
    client = TestClient(app)

    assert client.get("/api/plugins").status_code == 401
    assert (
        client.get(
            "/api/plugins",
            headers={"Authorization": "Bearer sk-alice"},
        ).status_code
        == 200
    )


def test_plugin_assets_are_public_read_only_when_auth_enabled(tmp_path: Path) -> None:
    plugin_dir = _write_plugin(tmp_path)
    (plugin_dir / "assets").mkdir()
    (plugin_dir / "assets" / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\nlogo")
    (plugin_dir / "assets" / "icon.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
        encoding="utf-8",
    )
    (plugin_dir / "assets" / "private.png").write_bytes(b"\x89PNG\r\n\x1a\nprivate")
    (plugin_dir / "assets" / "config.json").write_text('{"token":"secret"}', encoding="utf-8")
    (plugin_dir / ".env").write_text("API_TOKEN=secret", encoding="utf-8")
    (plugin_dir / "source.py").write_text("SECRET = 'source'", encoding="utf-8")
    manifest_path = plugin_dir / ".codex-plugin" / "plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["interface"].update({"logo": "assets/logo.png", "composerIcon": "assets/icon.svg"})
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    store = IdentityStore()
    store.add(Identity(actor_id="alice"), api_key_plaintext="sk-alice")
    app = FastAPI()
    app.include_router(
        create_plugins_router(
            plugin_roots=[tmp_path],
            identity_store=store,
            require_auth=True,
        )
    )
    client = TestClient(app)

    assert client.get("/api/plugins").status_code == 401
    logo = client.get("/api/plugins/research/assets/assets/logo.png")
    assert logo.status_code == 200
    assert logo.headers["x-content-type-options"] == "nosniff"
    icon = client.get("/api/plugins/research/assets/assets/icon.svg")
    assert icon.status_code == 200
    assert icon.headers["content-security-policy"] == "default-src 'none'; sandbox"
    assert client.get("/api/plugins/research/assets/assets/private.png").status_code == 401
    assert client.get("/api/plugins/research/assets/.env").status_code == 401
    assert client.get("/api/plugins/research/assets/source.py").status_code == 401

    # A malicious manifest cannot relabel configuration or source as public UI
    # artwork, and removing a prior declaration revokes its anonymous access.
    manifest["interface"].update({"logo": "source.py", "composerIcon": ".env"})
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert client.get("/api/plugins/research/assets/source.py").status_code == 401
    assert client.get("/api/plugins/research/assets/.env").status_code == 401
    assert client.get("/api/plugins/research/assets/assets/logo.png").status_code == 401

    authenticated = {"Authorization": "Bearer sk-alice"}
    assert (
        client.get(
            "/api/plugins/research/assets/assets/private.png",
            headers=authenticated,
        ).status_code
        == 200
    )
    assert (
        client.get(
            "/api/plugins/research/assets/source.py",
            headers=authenticated,
        ).status_code
        == 404
    )
    assert (
        client.get(
            "/api/plugins/research/assets/.env",
            headers=authenticated,
        ).status_code
        == 404
    )
    assert (
        client.get(
            "/api/plugins/research/assets/assets/config.json",
            headers=authenticated,
        ).status_code
        == 404
    )
    assert (
        client.get(
            "/api/plugins/research/assets/.codex-plugin/plugin.json",
            headers=authenticated,
        ).status_code
        == 404
    )


def _write_plugin(
    root: Path,
    *,
    include_migration_contract: bool = False,
    explicit_permissions: bool = False,
) -> Path:
    plugin_dir = root / "research"
    (plugin_dir / ".codex-plugin").mkdir(parents=True)
    (plugin_dir / "skills" / "brief").mkdir(parents=True)
    manifest = {
        "name": "research",
        "version": "0.1.0",
        "interface": {
            "displayName": "Research",
            "capabilities": [{"name": "brief", "type": "codex"}],
        },
    }
    if explicit_permissions:
        manifest["permissions"] = ["mcp:execute"]
    (plugin_dir / ".codex-plugin" / "plugin.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    (plugin_dir / "skills" / "brief" / "SKILL.md").write_text(
        "# Brief\n",
        encoding="utf-8",
    )
    (plugin_dir / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"research": {"command": "node"}}}),
        encoding="utf-8",
    )
    if include_migration_contract:
        (plugin_dir / "MIGRATION.md").write_text(
            "# Migration\n\nRelease checklist and compatibility notes.\n",
            encoding="utf-8",
        )
        (plugin_dir / "tests").mkdir()
        (plugin_dir / "tests" / "test_plugin.py").write_text(
            "def test_plugin_contract():\n    assert True\n",
            encoding="utf-8",
        )
    return plugin_dir


def _sign_plugin(plugin_dir: Path, *, key_status: str = "active") -> Path:
    plugin = discover_codex_plugins([plugin_dir.parent])[0]
    content_digest = plugin["smoke"]["content_provenance"]["digest"]
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    payload = canonical_publisher_signature_payload(
        plugin_id="research",
        version="0.1.0",
        content_digest=content_digest,
        publisher_id="acme",
        key_id="release-2026",
    )
    envelope = {
        "schema": "echo.plugin_publisher_signature.v1",
        "algorithm": "ed25519",
        "plugin_id": "research",
        "version": "0.1.0",
        "content_digest": content_digest,
        "publisher_id": "acme",
        "key_id": "release-2026",
        "signature": base64.b64encode(private_key.sign(payload)).decode("ascii"),
    }
    (plugin_dir / ".codex-plugin" / "provenance.json").write_text(
        json.dumps(envelope),
        encoding="utf-8",
    )
    trust_store_path = plugin_dir.parent / "publisher-trust.json"
    trust_store_path.write_text(
        json.dumps(
            {
                "schema": "echo.plugin_publisher_trust_store.v1",
                "publishers": [
                    {
                        "publisher_id": "acme",
                        "keys": [
                            {
                                "key_id": "release-2026",
                                "algorithm": "ed25519",
                                "status": key_status,
                                "public_key": base64.b64encode(public_key).decode("ascii"),
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return trust_store_path


