"""Tamper-evident appliance audit chain and authenticated admin API."""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from appliance.approval import APPROVAL_HEADER, HighRiskApprovalService
from appliance.audit import (
    AUDIT_KEY_ID,
    AUDIT_ROTATE_ACTION,
    AUDIT_ROTATE_TARGET,
    ApplianceAudit,
    AuditIntegrityError,
    create_audit_router,
    verify_audit_anchor,
)
from runtime.safety.auth.identity import encode_jwt_hs256

JWT_SECRET = "Audit-Secret_123456789012345678901234567890"


def _bearer() -> dict[str, str]:
    token = encode_jwt_hs256(
        {"sub": "local:admin", "iat": 0, "exp": 9_999_999_999},
        secret=JWT_SECRET,
    )
    return {"Authorization": f"Bearer {token}"}


def test_records_actor_action_result_and_redacts_credentials(tmp_path):
    audit = ApplianceAudit.from_data_dir(tmp_path, jwt_secret=JWT_SECRET)

    entry = audit.record(
        actor="local:admin",
        action="app.start",
        target="a" * 12,
        outcome="succeeded",
        metadata={"requestId": "req-1", "access_token": "must-not-leak"},
    )

    assert entry["payload"]["actor"] == "local:admin"
    assert entry["payload"]["action"] == "app.start"
    assert entry["payload"]["metadata"]["access_token"] == "[redacted]"
    assert "must-not-leak" not in audit.path.read_text(encoding="utf-8")
    assert audit.path.stat().st_mode & 0o777 == 0o600
    assert audit.checkpoint_path.stat().st_mode & 0o777 == 0o600
    assert audit.verify().ok is True


def test_detects_record_mutation(tmp_path):
    audit = ApplianceAudit.from_data_dir(tmp_path, jwt_secret=JWT_SECRET)
    audit.record(actor="local:admin", action="files.mkdir", target="safe", outcome="succeeded")
    raw = audit.path.read_text(encoding="utf-8").replace('"safe"', '"evil"')
    audit.path.write_text(raw, encoding="utf-8")

    report = audit.verify()

    assert report.ok is False
    assert "MAC mismatch" in report.error


def test_refuses_new_mutations_after_runtime_tampering(tmp_path):
    audit = ApplianceAudit.from_data_dir(tmp_path, jwt_secret=JWT_SECRET)
    audit.record(actor="local:admin", action="files.mkdir", target="safe", outcome="succeeded")
    raw = audit.path.read_text(encoding="utf-8").replace('"safe"', '"evil"')
    audit.path.write_text(raw, encoding="utf-8")

    with pytest.raises(AuditIntegrityError):
        audit.record(
            actor="local:admin",
            action="files.mkdir",
            target="must-not-append",
            outcome="attempted",
        )

    assert "must-not-append" not in audit.path.read_text(encoding="utf-8")


def test_signed_checkpoint_detects_tail_truncation(tmp_path):
    audit = ApplianceAudit.from_data_dir(tmp_path, jwt_secret=JWT_SECRET)
    audit.record(actor="local:admin", action="files.mkdir", target="one", outcome="succeeded")
    audit.record(actor="local:admin", action="files.mkdir", target="two", outcome="succeeded")
    lines = audit.path.read_text(encoding="utf-8").splitlines()
    audit.path.write_text(f"{lines[0]}\n", encoding="utf-8")

    report = audit.verify()

    assert report.ok is False
    assert "signed checkpoint" in report.error


def test_detects_checkpoint_tampering(tmp_path):
    audit = ApplianceAudit.from_data_dir(tmp_path, jwt_secret=JWT_SECRET)
    audit.record(actor="local:admin", action="files.mkdir", target="one", outcome="succeeded")
    checkpoint = json.loads(audit.checkpoint_path.read_text(encoding="utf-8"))
    checkpoint["seq"] = 99
    audit.checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")

    report = audit.verify()

    assert report.ok is False
    assert "signature mismatch" in report.error


def test_audit_admin_api_is_authenticated_and_reports_verification(tmp_path):
    audit = ApplianceAudit.from_data_dir(tmp_path, jwt_secret=JWT_SECRET)
    audit.record(actor="local:admin", action="app.stop", target="a" * 12, outcome="succeeded")
    app = FastAPI()
    app.include_router(create_audit_router(audit, jwt_secret=JWT_SECRET))
    client = TestClient(app)

    assert client.get("/api/appliance/audit/verify").status_code == 401
    verified = client.get("/api/appliance/audit/verify", headers=_bearer())
    events = client.get("/api/appliance/audit/events?limit=1", headers=_bearer())

    assert verified.json() == {
        "ok": True,
        "entriesChecked": 1,
        "brokenAt": None,
        "error": "",
    }
    assert events.status_code == 200
    assert events.json()["verification"]["ok"] is True
    assert events.json()["events"][0]["payload"]["actor"] == "local:admin"


def test_rotates_signing_key_without_losing_v1_verification(tmp_path):
    audit = ApplianceAudit.from_data_dir(tmp_path, jwt_secret=JWT_SECRET)
    first = audit.record(
        actor="local:admin",
        action="files.mkdir",
        target="before-rotation",
        outcome="succeeded",
    )

    rotated = audit.rotate_key(actor="local:admin")
    after = audit.record(
        actor="local:admin",
        action="files.mkdir",
        target="after-rotation",
        outcome="succeeded",
    )

    assert first["key_id"] == AUDIT_KEY_ID
    assert rotated["previousKeyId"] == AUDIT_KEY_ID
    assert rotated["activeKeyId"].startswith("echo-appliance-k2-")
    assert rotated["keyCount"] == 2
    assert rotated["secretsPersisted"] is False
    assert after["key_id"] == rotated["activeKeyId"]
    assert audit.verify().ok is True
    assert audit.keyring_path.stat().st_mode & 0o777 == 0o600
    assert JWT_SECRET not in audit.keyring_path.read_text()

    restarted = ApplianceAudit.from_data_dir(tmp_path, jwt_secret=JWT_SECRET)
    assert restarted.verify().entries_checked == 3
    assert restarted.key_status()["activeKeyId"] == rotated["activeKeyId"]


def test_tampered_or_missing_rotated_keyring_fails_closed(tmp_path):
    audit = ApplianceAudit.from_data_dir(tmp_path, jwt_secret=JWT_SECRET)
    audit.record(actor="local:admin", action="files.mkdir", target="one", outcome="succeeded")
    audit.rotate_key(actor="local:admin")
    raw = json.loads(audit.keyring_path.read_text())
    raw["activeKeyId"] = AUDIT_KEY_ID
    audit.keyring_path.write_text(json.dumps(raw))

    report = audit.verify()
    assert report.ok is False
    assert "keyring" in report.error
    with pytest.raises(AuditIntegrityError):
        audit.record(
            actor="local:admin",
            action="files.mkdir",
            target="blocked",
            outcome="attempted",
        )
    with pytest.raises(AuditIntegrityError):
        ApplianceAudit.from_data_dir(tmp_path, jwt_secret=JWT_SECRET)


def test_ed25519_anchor_is_externally_verifiable_and_pinnable(tmp_path):
    audit = ApplianceAudit.from_data_dir(tmp_path, jwt_secret=JWT_SECRET)
    audit.record(actor="local:admin", action="app.stop", target="a" * 12, outcome="succeeded")

    anchor = audit.anchor()
    report = verify_audit_anchor(anchor)
    pinned = verify_audit_anchor(anchor, expected_signing_key_id=report["signingKeyId"])

    assert report["ok"] is True
    assert report["entries"] == 1
    assert report["tailSeq"] == 0
    assert pinned == report
    assert (
        anchor["audit"]["logSha256"]
        == __import__("hashlib").sha256(audit.path.read_bytes()).hexdigest()
    )
    assert (
        ApplianceAudit.from_data_dir(tmp_path, jwt_secret=JWT_SECRET).anchor()["signing"]
        == (anchor["signing"])
    )

    modified = json.loads(json.dumps(anchor))
    modified["audit"]["tailMac"] = "0" * 64
    with pytest.raises(AuditIntegrityError, match="anchor verification"):
        verify_audit_anchor(modified)
    with pytest.raises(AuditIntegrityError, match="anchor verification"):
        verify_audit_anchor(anchor, expected_signing_key_id="sha256:" + "0" * 64)


def test_key_rotation_api_requires_plan_bound_password_step_up(tmp_path):
    from runtime.adapters.integrations.local_auth.config import hash_password

    audit = ApplianceAudit.from_data_dir(tmp_path, jwt_secret=JWT_SECRET)
    approval = HighRiskApprovalService(
        password_hash=hash_password("device-password"),
        jwt_secret=JWT_SECRET,
        audit=audit,
        boot_nonce=b"a" * 32,
    )
    app = FastAPI()
    app.include_router(
        create_audit_router(
            audit,
            jwt_secret=JWT_SECRET,
            approval=approval,
        )
    )
    client = TestClient(app)

    assert client.get("/api/appliance/audit/keys").status_code == 401
    assert client.get("/api/appliance/audit/anchor").status_code == 401
    denied = client.post("/api/appliance/audit/keys/rotate", headers=_bearer())
    assert denied.status_code == 403

    token, _ttl = approval.issue(
        actor="local:admin",
        action=AUDIT_ROTATE_ACTION,
        target=AUDIT_ROTATE_TARGET,
        password="device-password",
        client_ip="testclient",
    )
    rotated = client.post(
        "/api/appliance/audit/keys/rotate",
        headers={**_bearer(), APPROVAL_HEADER: token},
    )

    assert rotated.status_code == 200
    assert rotated.json()["keyCount"] == 2
    assert rotated.json()["rotationEventSeq"] == 3
    assert client.get("/api/appliance/audit/anchor", headers=_bearer()).status_code == 200
