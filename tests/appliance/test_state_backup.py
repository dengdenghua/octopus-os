"""Encrypted appliance-state backup excludes NAS data and restores safely."""

from __future__ import annotations

import json
import stat

import pytest

from appliance.state_backup import (
    BackupError,
    export_backup,
    prune_backup_set,
    restore_backup,
    verify_backup,
)
from appliance.state_lock import LOCK_FILENAME, StateDirectoryLock, StateLockError
from appliance.state_schema import CURRENT_SCHEMA_VERSION, ensure_state_schema

PASSPHRASE = b"correct horse battery staple"


def _state_tree(tmp_path):
    state = tmp_path / "state"
    nas = state / "nas"
    (state / "memory").mkdir(parents=True)
    nas.mkdir()
    (state / "appliance-auth.json").write_text(
        json.dumps(
            {
                "username": "admin",
                "password_hash": "bcrypt:not-plaintext",
                "jwt_secret": "private-jwt-secret",
                "session_not_before": 0,
            }
        )
    )
    (state / "appliance-auth.json").chmod(0o600)
    (state / "memory" / "facts.json").write_text('{"fact":"remember me"}')
    (state / "omv-health-state.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "state": "healthy",
                "stale": False,
                "checkedAt": "2026-08-26T01:00:00Z",
                "lastSuccessfulAt": "2026-08-26T01:00:00Z",
                "intervalSeconds": 300,
                "persistenceHealthy": True,
                "activeAlerts": [],
                "events": [],
                "summary": {"critical": 0, "warning": 0, "total": 0},
                "readOnly": True,
            }
        )
    )
    (state / "omv-health-state.json").chmod(0o600)
    (nas / "family-photo.jpg").write_bytes(b"NAS-USER-DATA-MUST-NOT-BE-INCLUDED")
    return state, nas


def test_encrypted_round_trip_excludes_nas_user_data(tmp_path) -> None:
    state, nas = _state_tree(tmp_path)
    backup = tmp_path / "backups" / "device.echo-backup"

    exported = export_backup(state, backup, nas_root=nas, passphrase=PASSPHRASE)

    encrypted = backup.read_bytes()
    assert exported["encrypted"] is True
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert b"private-jwt-secret" not in encrypted
    assert b"remember me" not in encrypted
    assert b"NAS-USER-DATA-MUST-NOT-BE-INCLUDED" not in encrypted

    verified = verify_backup(backup, passphrase=PASSPHRASE)
    assert verified["encrypted"] is True
    assert verified["nasUserDataIncluded"] is False
    assert verified["stateSchemaVersion"] == 0
    assert verified["stateCompatible"] is True

    restored = tmp_path / "restored-state"
    report = restore_backup(backup, restored, passphrase=PASSPHRASE)
    assert report["restoredTo"] == str(restored)
    assert json.loads((restored / "appliance-auth.json").read_text())["jwt_secret"] == (
        "private-jwt-secret"
    )
    assert (restored / "memory" / "facts.json").read_text() == '{"fact":"remember me"}'
    assert json.loads((restored / "omv-health-state.json").read_text())["state"] == ("healthy")
    assert stat.S_IMODE((restored / "omv-health-state.json").stat().st_mode) == 0o600
    assert stat.S_IMODE((restored / "appliance-auth.json").stat().st_mode) == 0o600
    assert not (restored / "nas").exists()
    assert not (restored / LOCK_FILENAME).exists()


def test_backup_manifest_carries_explicit_state_schema(tmp_path) -> None:
    state, nas = _state_tree(tmp_path)
    ensure_state_schema(state)
    backup = tmp_path / "versioned.echo-backup"

    exported = export_backup(state, backup, nas_root=nas, passphrase=PASSPHRASE)
    verified = verify_backup(backup, passphrase=PASSPHRASE)

    assert exported["stateSchemaVersion"] == CURRENT_SCHEMA_VERSION
    assert exported["stateCompatible"] is True
    assert verified["stateSchemaVersion"] == CURRENT_SCHEMA_VERSION


def test_wrong_passphrase_tampering_and_overwrite_fail_closed(tmp_path) -> None:
    state, nas = _state_tree(tmp_path)
    backup = tmp_path / "device.echo-backup"
    export_backup(state, backup, nas_root=nas, passphrase=PASSPHRASE)

    with pytest.raises(BackupError, match="authentication failed"):
        verify_backup(backup, passphrase=b"wrong password with enough length")

    tampered = tmp_path / "tampered.echo-backup"
    payload = bytearray(backup.read_bytes())
    payload[-17] ^= 0x01
    tampered.write_bytes(payload)
    with pytest.raises(BackupError, match="authentication failed"):
        verify_backup(tampered, passphrase=PASSPHRASE)

    with pytest.raises(BackupError, match="already exists"):
        export_backup(state, backup, nas_root=nas, passphrase=PASSPHRASE)
    existing_target = tmp_path / "existing"
    existing_target.mkdir()
    with pytest.raises(BackupError, match="must not already exist"):
        restore_backup(backup, existing_target, passphrase=PASSPHRASE)


def test_running_state_lock_blocks_offline_export(tmp_path) -> None:
    state, nas = _state_tree(tmp_path)
    runtime_lock = StateDirectoryLock.acquire(
        state,
        exclusive=True,
        purpose="appliance runtime",
    )
    try:
        with pytest.raises(BackupError, match="already in use"):
            export_backup(
                state,
                tmp_path / "blocked.echo-backup",
                nas_root=nas,
                passphrase=PASSPHRASE,
            )
    finally:
        runtime_lock.release()

    assert export_backup(
        state,
        tmp_path / "offline.echo-backup",
        nas_root=nas,
        passphrase=PASSPHRASE,
    )["encrypted"]


def test_unsafe_state_symlink_and_lock_symlink_are_rejected(tmp_path) -> None:
    state, nas = _state_tree(tmp_path)
    (state / "escape").symlink_to("../outside")
    with pytest.raises(BackupError, match="unsafe archive symlink"):
        export_backup(
            state,
            tmp_path / "unsafe.echo-backup",
            nas_root=nas,
            passphrase=PASSPHRASE,
        )

    (state / "escape").unlink()
    outside = tmp_path / "outside-lock"
    outside.write_text("not a lock")
    lock_path = state / LOCK_FILENAME
    if lock_path.exists():
        lock_path.unlink()
    lock_path.symlink_to(outside)
    with pytest.raises(StateLockError, match="cannot open private state lock"):
        StateDirectoryLock.acquire(state, exclusive=True)


def test_retention_verifies_newest_before_deleting_only_managed_backups(
    tmp_path,
) -> None:
    state, nas = _state_tree(tmp_path)
    backups = tmp_path / "backups"
    backups.mkdir()
    source = backups / "echo-state-20260826T010000Z.echo-backup"
    export_backup(state, source, nas_root=nas, passphrase=PASSPHRASE)
    payload = source.read_bytes()
    middle = backups / "echo-state-20260826T020000Z.echo-backup"
    newest = backups / "echo-state-20260826T030000Z.echo-backup"
    middle.write_bytes(payload)
    newest.write_bytes(payload)
    unrelated = backups / "family-photos.echo-backup"
    unrelated.write_bytes(b"not managed by Echo state retention")

    report = prune_backup_set(backups, keep=2, passphrase=PASSPHRASE)

    assert report["deleted"] == [source.name]
    assert report["verifiedNewest"] == newest.name
    assert not source.exists()
    assert middle.exists() and newest.exists() and unrelated.exists()

    older = backups / "echo-state-20260825T230000Z.echo-backup"
    older.write_bytes(payload)
    with pytest.raises(BackupError, match="authentication failed"):
        prune_backup_set(
            backups,
            keep=2,
            passphrase=b"wrong password with enough length",
        )
    assert older.exists()

    with pytest.raises(BackupError, match="must keep between 2"):
        prune_backup_set(backups, keep=1, passphrase=PASSPHRASE)
