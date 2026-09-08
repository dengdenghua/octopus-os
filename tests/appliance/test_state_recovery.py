"""Restored state is fully validated before host directory promotion."""

from __future__ import annotations

import json
import os

import pytest

from appliance.audit import ApplianceAudit
from appliance.nas_alert_delivery import NasAlertDeliveryService, WebhookDesiredState
from appliance.nas_email_alert_delivery import EmailDesiredState, NasEmailAlertDeliveryService
from appliance.state_lock import LOCK_FILENAME
from appliance.state_recovery import StateRecoveryError, inspect_restored_state
from appliance.state_schema import CURRENT_SCHEMA_VERSION, ensure_state_schema
from appliance.totp import AdministratorTotp, totp_code

JWT_SECRET = "Recovery-Secret_123456789012345678901234567890"


def _restored_state(tmp_path):
    state = tmp_path / "restored"
    state.mkdir()
    (state / "appliance-auth.json").write_text(
        json.dumps(
            {
                "username": "admin",
                "password_hash": "sha256:" + "a" * 64,
                "jwt_secret": JWT_SECRET,
                "session_not_before": 42,
            }
        )
    )
    (state / "appliance-auth.json").chmod(0o600)
    ensure_state_schema(state)
    audit = ApplianceAudit.from_data_dir(state, jwt_secret=JWT_SECRET)
    audit.record(
        actor="local:admin",
        action="files.mkdir",
        target="family",
        outcome="succeeded",
    )
    return state


def test_current_auth_and_audit_state_is_ready_for_promotion(tmp_path) -> None:
    state = _restored_state(tmp_path)
    before = {
        entry.relative_to(state): (entry.read_bytes(), entry.stat().st_mtime_ns)
        for entry in state.rglob("*")
        if entry.is_file()
    }

    report = inspect_restored_state(state)

    assert report["ok"] is True
    assert report["schemaVersion"] == CURRENT_SCHEMA_VERSION
    assert report["migrationRequired"] is False
    assert report["administrator"] == "admin"
    assert report["passwordHashKind"] == "legacy-sha256"
    assert report["sessionNotBefore"] == 42
    assert report["auditEntries"] == 1
    assert report["auditSigningKeyId"].startswith("sha256:")
    assert report["nasUserDataIncluded"] is False
    assert report["runtimeLockIncluded"] is False
    assert report["readOnlyInspection"] is True
    assert report["administratorTotpEnabled"] is False
    assert {
        entry.relative_to(state): (entry.read_bytes(), entry.stat().st_mtime_ns)
        for entry in state.rglob("*")
        if entry.is_file()
    } == before


def test_restored_totp_state_is_validated_before_promotion(tmp_path) -> None:
    state = _restored_state(tmp_path)
    now = 1_800_000_000.0
    totp = AdministratorTotp(
        jwt_secret=JWT_SECRET,
        path=state / "appliance-totp.json",
        clock=lambda: now,
    )
    enrollment = totp.begin_enrollment()
    totp.confirm_enrollment(
        enrollment_id=enrollment["enrollmentId"],
        code=totp_code(enrollment["secret"], timestamp=now),
    )

    report = inspect_restored_state(state)
    assert report["administratorTotpEnabled"] is True
    assert report["administratorRecoveryCodesRemaining"] == 8

    payload = json.loads((state / "appliance-totp.json").read_text())
    payload["secret"] = "not-base32"
    (state / "appliance-totp.json").write_text(json.dumps(payload))
    with pytest.raises(StateRecoveryError, match="TOTP state is invalid"):
        inspect_restored_state(state)


def test_restored_alert_delivery_state_is_authenticated_before_promotion(tmp_path) -> None:
    state = _restored_state(tmp_path)
    delivery = NasAlertDeliveryService(state, encryption_secret=JWT_SECRET)
    plan = delivery.plan(
        WebhookDesiredState.model_validate(
            {"enabled": True, "url": "https://hooks.example.com/echo?secret=restored"}
        )
    )
    delivery.apply(plan["planId"])

    report = inspect_restored_state(state)
    assert report["nasAlertDeliveryConfigured"] is True
    assert report["nasAlertDeliveryEnabled"] is True

    payload = bytearray(delivery.path.read_bytes())
    payload[-8] ^= 1
    delivery.path.write_bytes(payload)
    with pytest.raises(StateRecoveryError, match="alert delivery state is invalid"):
        inspect_restored_state(state)


def test_restored_email_alert_state_is_authenticated_before_promotion(tmp_path) -> None:
    state = _restored_state(tmp_path)
    delivery = NasEmailAlertDeliveryService(state, encryption_secret=JWT_SECRET)
    plan = delivery.plan(
        EmailDesiredState.model_validate(
            {
                "enabled": True,
                "smtpHost": "smtp.example.com",
                "smtpPort": 465,
                "username": "echo@example.com",
                "password": "restored-app-password",
                "fromAddress": "echo@example.com",
                "recipient": "owner@example.net",
            }
        )
    )
    delivery.apply(plan["planId"])

    report = inspect_restored_state(state)
    assert report["nasEmailAlertDeliveryConfigured"] is True
    assert report["nasEmailAlertDeliveryEnabled"] is True

    payload = bytearray(delivery.path.read_bytes())
    payload[-8] ^= 1
    delivery.path.write_bytes(payload)
    with pytest.raises(StateRecoveryError, match="email alert state is invalid"):
        inspect_restored_state(state)


def test_older_compatible_state_must_be_migrated_before_promotion(tmp_path) -> None:
    state = tmp_path / "legacy"
    state.mkdir()
    (state / "appliance-auth.json").write_text(
        json.dumps(
            {
                "username": "admin",
                "password_hash": "sha256:" + "b" * 64,
                "jwt_secret": JWT_SECRET,
                "session_not_before": 0,
            }
        )
    )
    (state / "appliance-auth.json").chmod(0o600)

    with pytest.raises(StateRecoveryError, match="complete supported migrations"):
        inspect_restored_state(state)

    allowed = inspect_restored_state(state, require_current=False)
    assert allowed["schemaVersion"] == 0
    assert allowed["migrationRequired"] is True


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda state: (state / "nas").mkdir(), "NAS user data"),
        (lambda state: (state / LOCK_FILENAME).write_text("stale"), "runtime lock"),
        (
            lambda state: (state / "appliance-auth.json").chmod(0o644),
            "authentication schema marker|state file is unsafe",
        ),
    ],
)
def test_nas_lock_and_public_credentials_are_rejected(tmp_path, mutation, message) -> None:
    if os.name == "nt" and "authentication" in message:
        pytest.skip("POSIX mode-bit mutation is unavailable on Windows")
    state = _restored_state(tmp_path)
    mutation(state)

    with pytest.raises(StateRecoveryError, match=message):
        inspect_restored_state(state)


def test_malformed_credentials_and_tampered_audit_fail_closed(tmp_path) -> None:
    state = _restored_state(tmp_path)
    auth_path = state / "appliance-auth.json"
    auth = json.loads(auth_path.read_text())
    auth["password_hash"] = "plaintext-password"
    auth_path.write_text(json.dumps(auth))

    with pytest.raises(StateRecoveryError, match="password hash is invalid"):
        inspect_restored_state(state)

    auth["password_hash"] = "sha256:" + "c" * 64
    auth_path.write_text(json.dumps(auth))
    audit_path = state / "appliance-audit.jsonl"
    audit_path.write_text(audit_path.read_text().replace('"family"', '"other"'))
    with pytest.raises(StateRecoveryError, match="audit trail failed verification"):
        inspect_restored_state(state)


def test_malformed_member_session_floors_fail_before_state_promotion(tmp_path) -> None:
    state = _restored_state(tmp_path)
    auth_path = state / "appliance-auth.json"
    auth = json.loads(auth_path.read_text())
    auth["account_session_not_before"] = {"alice": -1}
    auth_path.write_text(json.dumps(auth))

    with pytest.raises(StateRecoveryError, match="account session revocation epochs"):
        inspect_restored_state(state)


def test_symlinked_audit_or_runtime_owner_metadata_is_rejected(tmp_path) -> None:
    state = _restored_state(tmp_path)
    checkpoint = state / "appliance-audit.jsonl.checkpoint"
    checkpoint_bytes = checkpoint.read_bytes()
    checkpoint.unlink()
    checkpoint.symlink_to("appliance-audit.jsonl")
    with pytest.raises(StateRecoveryError, match="state file is unsafe"):
        inspect_restored_state(state)

    checkpoint.unlink()
    checkpoint.write_bytes(checkpoint_bytes)
    checkpoint.chmod(0o600)
    audit = ApplianceAudit.from_data_dir(state, jwt_secret=JWT_SECRET)
    audit.record(
        actor="local:admin",
        action="files.mkdir",
        target="second",
        outcome="succeeded",
    )
    owner = state / ".echo-runtime-owner"
    owner.symlink_to("appliance-auth.json")
    with pytest.raises(StateRecoveryError, match="state file is unsafe"):
        inspect_restored_state(state)
