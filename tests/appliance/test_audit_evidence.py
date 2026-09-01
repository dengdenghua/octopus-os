"""Encrypted audit evidence is portable, pinned, and safely retained."""

from __future__ import annotations

import datetime as dt
import json
import shutil
import stat

import pytest

from appliance.audit import ApplianceAudit
from appliance.audit_evidence import (
    AuditEvidenceError,
    export_evidence,
    prune_evidence_set,
    verify_evidence,
)
from appliance.state_lock import StateDirectoryLock

JWT_SECRET = "Evidence-Secret_123456789012345678901234567890"
PASSPHRASE = b"portable evidence passphrase"


def _state(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "appliance-auth.json").write_text(
        json.dumps({"jwt_secret": JWT_SECRET, "password_hash": "not-exported"})
    )
    audit = ApplianceAudit.from_data_dir(state, jwt_secret=JWT_SECRET)
    audit.record(
        actor="local:admin",
        action="share.delete",
        target="family/private",
        outcome="succeeded",
        metadata={"password": "must-not-export-in-clear"},
    )
    audit.rotate_key(actor="local:admin")
    return state, audit


def test_export_is_encrypted_and_verifies_with_pinned_device_identity(tmp_path) -> None:
    state, audit = _state(tmp_path)
    output = tmp_path / "external" / "echo-audit-20260827T010000Z.echo-audit"

    exported = export_evidence(state, output, passphrase=PASSPHRASE)
    verified = verify_evidence(output, passphrase=PASSPHRASE)
    pinned = verify_evidence(
        output,
        passphrase=PASSPHRASE,
        expected_signing_key_id=verified["signingKeyId"],
    )

    ciphertext = output.read_bytes()
    assert exported["entries"] == 2
    assert exported["encrypted"] is True
    assert verified == pinned
    assert verified["nasUserDataIncluded"] is False
    assert verified["authenticationStoreIncluded"] is False
    assert "appliance-auth.json" not in verified["files"]
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert JWT_SECRET.encode() not in ciphertext
    assert b"family/private" not in ciphertext
    assert audit.verify().ok is True


def test_wrong_passphrase_tampering_and_wrong_identity_fail_closed(tmp_path) -> None:
    state, _audit = _state(tmp_path)
    output = tmp_path / "evidence.echo-audit"
    export_evidence(state, output, passphrase=PASSPHRASE)
    verify_evidence(output, passphrase=PASSPHRASE)

    with pytest.raises(AuditEvidenceError, match="authentication failed"):
        verify_evidence(output, passphrase=b"wrong passphrase long enough")
    with pytest.raises(AuditEvidenceError, match="verification failed"):
        verify_evidence(
            output,
            passphrase=PASSPHRASE,
            expected_signing_key_id="sha256:" + "0" * 64,
        )

    tampered = tmp_path / "tampered.echo-audit"
    payload = bytearray(output.read_bytes())
    payload[-20] ^= 1
    tampered.write_bytes(payload)
    with pytest.raises(AuditEvidenceError, match="authentication failed"):
        verify_evidence(tampered, passphrase=PASSPHRASE)


def test_export_requires_offline_lock_and_external_destination(tmp_path) -> None:
    state, _audit = _state(tmp_path)
    with pytest.raises(AuditEvidenceError, match="outside appliance state"):
        export_evidence(
            state,
            state / "blocked.echo-audit",
            passphrase=PASSPHRASE,
        )

    with (
        StateDirectoryLock.acquire(state, exclusive=True),
        pytest.raises(AuditEvidenceError, match="already in use"),
    ):
        export_evidence(
            state,
            tmp_path / "blocked.echo-audit",
            passphrase=PASSPHRASE,
        )


def test_retention_verifies_newest_and_deletes_only_expired_managed_bundles(tmp_path) -> None:
    state, _audit = _state(tmp_path)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    oldest = evidence / "echo-audit-20260101T000000Z.echo-audit"
    export_evidence(state, oldest, passphrase=PASSPHRASE)
    middle = evidence / "echo-audit-20260201T000000Z.echo-audit"
    newest = evidence / "echo-audit-20260301T000000Z.echo-audit"
    shutil.copyfile(oldest, middle)
    shutil.copyfile(oldest, newest)
    unrelated = evidence / "customer-export.echo-audit"
    unrelated.write_text("leave me")

    report = prune_evidence_set(
        evidence,
        keep_days=30,
        keep_minimum=2,
        passphrase=PASSPHRASE,
        now=dt.datetime(2026, 8, 27, tzinfo=dt.UTC),
    )

    assert report["deleted"] == [oldest.name]
    assert report["kept"] == [middle.name, newest.name]
    assert report["verifiedNewest"] == newest.name
    assert not oldest.exists()
    assert middle.exists() and newest.exists() and unrelated.exists()

    before = middle.read_bytes()
    tampered = bytearray(newest.read_bytes())
    tampered[-1] ^= 1
    newest.write_bytes(tampered)
    extra_old = evidence / "echo-audit-20250101T000000Z.echo-audit"
    extra_old.write_bytes(before)
    with pytest.raises(AuditEvidenceError, match="authentication failed"):
        prune_evidence_set(
            evidence,
            keep_days=30,
            keep_minimum=2,
            passphrase=PASSPHRASE,
            now=dt.datetime(2026, 8, 27, tzinfo=dt.UTC),
        )
    assert extra_old.exists()
