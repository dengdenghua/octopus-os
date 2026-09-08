from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from appliance import (
    btrfs_snapshot_schedule_policy,
    native_btrfs_snapshot,
    native_storage,
    native_time_machine,
)
from appliance import (
    nas_backup_restore_policy as restore_policy,
)
from appliance.native_storage_probe import ReadOutput
from deploy.appliance import nas_data_backup

SET_ID = "11111111-2222-4333-8444-555555555555"
SHARE_REF = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
TARGET_REF = "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff"
FILESYSTEM_UUID = "12345678-1234-4234-8234-123456789abc"
TARGET_FILESYSTEM_UUID = "99999999-9999-4999-8999-999999999999"
SNAPSHOT_ID = "f" * 64
PLAN_ID = "e" * 64
SOURCE = f"/data/pool/.echo-snapshots/{SHARE_REF}/auto-20260908t000000z"
TARGET = Path("/data/pool/photos")
REPLACEMENT_TARGET = Path("/data/new-pool/restored-photos")


def _desired_state() -> dict[str, Any]:
    return {
        "schema": restore_policy.DESIRED_SCHEMA,
        "selector": SET_ID,
        "repository": "/mnt/off-device/repository",
        "repositoryMount": "/mnt/off-device",
        "targets": [
            {
                "sourceSharedFolderRef": SHARE_REF,
                "targetSharedFolderRef": TARGET_REF,
            }
        ],
    }


def _selected() -> tuple[dict[str, Any], dict[str, Any]]:
    btrfs_snapshot_id = native_btrfs_snapshot._snapshot_id(SHARE_REF, "auto-20260908t000000z")
    manifest = nas_data_backup._validate_backup_set_manifest(
        {
            "schema": nas_data_backup.BACKUP_SET_SCHEMA,
            "setId": SET_ID,
            "createdAt": "2026-09-08T00:00:00+00:00",
            "members": [
                {
                    "sharedFolderRef": SHARE_REF,
                    "filesystemUuid": FILESYSTEM_UUID,
                    "snapshotId": btrfs_snapshot_id,
                    "sourceSnapshot": SOURCE,
                    "restoreTarget": TARGET.as_posix(),
                    "storageKind": "btrfsSubvolume",
                }
            ],
        }
    )
    selected = {
        "id": SNAPSHOT_ID,
        "time": "2026-09-08T00:02:00Z",
        "setId": SET_ID,
        "manifestSha256": manifest["manifestSha256"],
        "members": nas_data_backup._manifest_index_members(manifest),
        "paths": [SOURCE],
    }
    return selected, manifest


def _engine_plan(manifest: dict[str, Any]) -> dict[str, Any]:
    member = manifest["members"][0]
    return {
        "schema": "echo.nas-backup-set-restore-plan.v1",
        "planId": PLAN_ID,
        "repositoryId": "d" * 64,
        "snapshotId": SNAPSHOT_ID,
        "setId": SET_ID,
        "manifestSha256": manifest["manifestSha256"],
        "memberCount": 1,
        "members": [
            {
                "sharedFolderRef": SHARE_REF,
                "filesystemUuid": FILESYSTEM_UUID,
                "targetFilesystemUuid": TARGET_FILESYSTEM_UUID,
                "snapshotId": member["snapshotId"],
                "targetSubvolumeUuid": "87654321-4321-4321-8321-cba987654321",
                "remapped": True,
            }
        ],
        "confirmation": f"RESTORE ECHO NAS SET {SET_ID} SNAPSHOT {SNAPSHOT_ID}",
        "pathsRedacted": True,
    }


def _context() -> tuple[dict[str, str], dict[str, Any], bytes, dict[str, Any], dict[str, Any]]:
    selected, manifest = _selected()
    return (
        _desired_state(),
        {
            "repository": "/mnt/off-device/repository",
            "repositoryMount": "/mnt/off-device",
        },
        b"private-password",
        selected,
        {
            "repositoryId": "d" * 64,
            "manifest": manifest,
            "restoreTargets": {
                SHARE_REF: {
                    "restoreTarget": REPLACEMENT_TARGET.as_posix(),
                    "filesystemUuid": TARGET_FILESYSTEM_UUID,
                }
            },
            "targetRefs": {SHARE_REF: TARGET_REF},
        },
    )


def test_reconstructs_authenticated_original_manifest_without_live_source_disk() -> None:
    selected, original = _selected()

    rebuilt = restore_policy._manifest_for_snapshot(
        selected,
        original_target_resolver=lambda _ref, _source: TARGET.as_posix(),
    )

    assert rebuilt == original


def test_original_target_path_comes_from_registry_name_and_authenticated_source(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        native_storage,
        "_registry_load",
        lambda **_kwargs: [
            {
                "uuid": SHARE_REF,
                "name": "photos",
                "relativePath": "photos",
                "storageKind": "btrfsSubvolume",
            }
        ],
    )

    assert restore_policy._original_target_path(SHARE_REF, SOURCE) == TARGET.as_posix()


def test_reconstruction_rejects_wrong_original_metadata_without_disclosing_path() -> None:
    selected, _manifest = _selected()

    with pytest.raises(restore_policy.NasBackupRestorePolicyError) as caught:
        restore_policy._manifest_for_snapshot(
            selected,
            original_target_resolver=lambda _ref, _source: "/data/new-pool/photos",
        )

    assert caught.value.code == "source_metadata_mismatch"
    assert "/data/" not in str(caught.value)


def test_explicit_replacement_target_mapping_preserves_original_manifest() -> None:
    desired = restore_policy._desired(_desired_state())
    _selected_value, manifest = _selected()

    private, public = restore_policy._restore_targets(
        desired,
        manifest,
        target_resolver=lambda _ref: (REPLACEMENT_TARGET, TARGET_FILESYSTEM_UUID),
    )

    assert manifest["members"][0]["filesystemUuid"] == FILESYSTEM_UUID
    assert private[SHARE_REF]["filesystemUuid"] == TARGET_FILESYSTEM_UUID
    assert private[SHARE_REF]["restoreTarget"] == REPLACEMENT_TARGET.as_posix()
    assert public == {SHARE_REF: TARGET_REF}


def test_replacement_target_mapping_must_cover_every_source() -> None:
    desired = _desired_state()
    desired["targets"] = [
        {
            "sourceSharedFolderRef": "cccccccc-dddd-4eee-8fff-aaaaaaaaaaaa",
            "targetSharedFolderRef": TARGET_REF,
        }
    ]
    _selected_value, manifest = _selected()

    with pytest.raises(restore_policy.NasBackupRestorePolicyError) as caught:
        restore_policy._restore_targets(restore_policy._desired(desired), manifest)

    assert caught.value.code == "target_mapping_incomplete"


@pytest.mark.parametrize("selector", ["", "LATEST", "../latest", "abc", "g" * 64])
def test_desired_rejects_invalid_selectors(selector: str) -> None:
    desired = _desired_state()
    desired["selector"] = selector
    with pytest.raises(restore_policy.NasBackupRestorePolicyError) as caught:
        restore_policy._desired(desired)
    assert caught.value.code == "invalid_request"


def test_plan_is_path_redacted_and_binds_exact_snapshot(monkeypatch) -> None:
    _desired, _config, _password, _selected_value, context = _context()
    captured: dict[str, Any] = {}
    monkeypatch.setattr(restore_policy, "_restore_context", lambda _value: _context())
    monkeypatch.setattr(nas_data_backup.BackupSetRestoreReceipts, "load", lambda _self: None)
    monkeypatch.setattr(
        nas_data_backup,
        "plan_restore_set",
        lambda **kwargs: captured.update(kwargs) or _engine_plan(context["manifest"]),
    )

    plan = restore_policy.plan_restore(_desired_state())

    assert plan["schema"] == restore_policy.PLAN_SCHEMA
    assert plan["planId"] == PLAN_ID
    assert plan["safety"]["replacementDiskMappingSupported"] is True
    assert plan["members"][0]["sourceSharedFolderRef"] == SHARE_REF
    assert plan["members"][0]["targetSharedFolderRef"] == TARGET_REF
    assert captured["restore_targets"] == context["restoreTargets"]
    assert plan["safety"]["networkSharesMustBeUnpublished"] is True
    assert "/data/" not in str(plan) and "/mnt/" not in str(plan)


@pytest.mark.parametrize(
    ("phase", "operation", "pending"),
    [("promoting", "resumeRestore", True), ("verified", "verifyRestore", False)],
)
def test_plan_projects_durable_receipt_without_private_paths(
    monkeypatch, phase: str, operation: str, pending: bool
) -> None:
    context = _context()
    engine_plan = _engine_plan(context[4]["manifest"])
    receipt_member = {
        **engine_plan["members"][0],
        "filesystemUuid": TARGET_FILESYSTEM_UUID,
        "repositorySource": SOURCE,
        "restoreTarget": REPLACEMENT_TARGET.as_posix(),
        "preparedSubvolumeUuid": None,
        "staging": None,
        "restored": None,
        "tree": None,
        "state": "pending",
    }
    monkeypatch.setattr(restore_policy, "_restore_context", lambda _value: context)
    monkeypatch.setattr(
        nas_data_backup.BackupSetRestoreReceipts, "load", lambda _self: {"private": True}
    )
    monkeypatch.setattr(
        nas_data_backup,
        "_validate_set_restore_receipt",
        lambda *_args, **_kwargs: {
            "planId": PLAN_ID,
            "repositoryId": "d" * 64,
            "snapshotId": SNAPSHOT_ID,
            "setId": SET_ID,
            "manifestSha256": context[4]["manifest"]["manifestSha256"],
            "phase": phase,
            "members": [receipt_member],
        },
    )
    monkeypatch.setattr(
        nas_data_backup,
        "plan_restore_set",
        lambda **_kwargs: pytest.fail("receipt recovery must not start a new plan"),
    )

    plan = restore_policy.plan_restore(context[0])

    assert plan["operation"] == operation and plan["recoveryPending"] is pending
    assert "/data/" not in str(plan) and "/mnt/" not in str(plan)


def test_apply_rejects_stale_plan_before_engine_restore(monkeypatch) -> None:
    called = False
    monkeypatch.setattr(
        restore_policy,
        "plan_restore",
        lambda _value: _engine_plan(_context()[4]["manifest"]),
    )

    def restore(**_kwargs: Any) -> dict[str, Any]:
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(nas_data_backup, "restore_set", restore)
    with pytest.raises(restore_policy.NasBackupRestorePolicyError) as caught:
        restore_policy.apply_restore(
            _desired_state(),
            "a" * 64,
            f"RESTORE ECHO NAS SET {SET_ID} SNAPSHOT {SNAPSHOT_ID}",
        )
    assert caught.value.code == "stale_plan"
    assert called is False


def test_apply_returns_only_verified_redacted_result(monkeypatch) -> None:
    context = _context()
    plan = restore_policy._public_plan(_engine_plan(context[4]["manifest"]))
    captured: dict[str, Any] = {}
    monkeypatch.setattr(restore_policy, "plan_restore", lambda _value: plan)
    monkeypatch.setattr(restore_policy, "_restore_context", lambda _value: context)
    monkeypatch.setattr(
        nas_data_backup,
        "restore_set",
        lambda **kwargs: (
            captured.update(kwargs)
            or {
                "repositoryId": "d" * 64,
                "snapshotId": SNAPSHOT_ID,
                "setId": SET_ID,
                "manifestSha256": context[4]["manifest"]["manifestSha256"],
                "memberCount": 1,
                "members": [],
                "fullReadVerified": True,
                "contentVerified": True,
                "pathsRedacted": True,
                "phase": "verified",
                "recovery": "fresh_restore",
            }
        ),
    )

    result = restore_policy.apply_restore(
        _desired_state(),
        PLAN_ID,
        plan["confirmation"],
    )

    assert result["verified"] is True and result["pathsRedacted"] is True
    assert result["schema"] == "echo.nas-data-backup-restore-result.v1"
    assert "/data/" not in str(result) and "/mnt/" not in str(result)
    assert captured["restore_targets"] == context[4]["restoreTargets"]


def test_list_restore_sets_remains_path_redacted(monkeypatch) -> None:
    selected, _manifest = _selected()
    monkeypatch.setattr(
        restore_policy,
        "_runtime_config",
        lambda _repository: {
            "repository": "/mnt/off-device/repository",
            "repositoryMount": "/mnt/off-device",
        },
    )
    monkeypatch.setattr(restore_policy, "_credential_password", lambda: b"secret")
    monkeypatch.setattr(
        nas_data_backup.BackupSetRestoreReceipts,
        "unfinished_repository_ids",
        lambda _directory: frozenset(),
    )
    monkeypatch.setattr(
        nas_data_backup,
        "list_backup_sets",
        lambda **_kwargs: {
            "repositoryId": "d" * 64,
            "setCount": 1,
            "sets": [
                {
                    "setId": SET_ID,
                    "snapshotId": selected["id"],
                    "createdAt": selected["time"],
                    "manifestSha256": selected["manifestSha256"],
                    "memberCount": 1,
                    "members": [
                        {
                            "sharedFolderRef": SHARE_REF,
                            "filesystemUuid": FILESYSTEM_UUID,
                            "snapshotId": selected["members"][0]["snapshotId"],
                        }
                    ],
                }
            ],
            "truncated": False,
            "encrypted": True,
            "pathsRedacted": True,
            "verification": "authenticated_index_only",
        },
    )

    listing = restore_policy.list_restore_sets(
        {
            "schema": restore_policy.REPOSITORY_SCHEMA,
            "repository": "/mnt/off-device/repository",
            "repositoryMount": "/mnt/off-device",
        }
    )

    assert listing["setCount"] == 1 and listing["pathsRedacted"] is True
    assert listing["restoreMode"] == "explicit-empty-managed-btrfs-target-mapping"
    assert "/mnt/" not in str(listing)


def test_credential_password_supplies_systemd_creds_dependencies(monkeypatch) -> None:
    encrypted = b"encrypted-credential"
    decrypted = b"correct horse battery staple"
    observed: dict[str, object] = {}

    monkeypatch.setattr(
        restore_policy.credential_policy,
        "_read_private_credential",
        lambda _path, *, trusted_uid: (encrypted, {"trustedUid": trusted_uid}),
    )

    def decrypt(arguments, payload, *, runner, systemd_creds):
        observed.update(
            {
                "arguments": arguments,
                "payload": payload,
                "runner": runner,
                "systemdCreds": systemd_creds,
            }
        )
        return decrypted

    monkeypatch.setattr(restore_policy.credential_policy, "_run_systemd_creds", decrypt)

    assert restore_policy._credential_password() == decrypted
    assert observed == {
        "arguments": [
            "decrypt",
            "--name=echo-nas-backup-password",
            "-",
            "-",
        ],
        "payload": encrypted,
        "runner": restore_policy.subprocess.run,
        "systemdCreds": restore_policy.credential_policy.SYSTEMD_CREDS,
    }


@pytest.mark.parametrize(
    ("receipt", "expected_calls"),
    [
        ({"phase": "promoting", "repositoryId": "d" * 64}, 1),
        ({"phase": "verified", "repositoryId": "d" * 64}, 0),
        (None, 0),
    ],
)
def test_stale_restic_unlock_is_limited_to_unfinished_restore_receipts(
    monkeypatch, receipt, expected_calls: int
) -> None:
    calls: list[dict[str, object]] = []

    class Receipts:
        def __init__(self, _directory: Path, set_id: str) -> None:
            assert set_id == SET_ID

        def load(self):
            return receipt

    monkeypatch.setattr(restore_policy.nas_data_backup, "BackupSetRestoreReceipts", Receipts)
    monkeypatch.setattr(
        restore_policy.nas_data_backup,
        "_unlock_stale_repository",
        lambda **kwargs: calls.append(kwargs) or True,
    )
    config = {
        "repository": "/mnt/off-device/repository",
        "repositoryMount": "/mnt/off-device",
    }

    restore_policy._recover_stale_restore_lock(SET_ID, config, b"secret")

    assert len(calls) == expected_calls
    if calls:
        assert calls[0]["password"] == b"secret"
        assert calls[0]["expected_repository_ids"] == ["d" * 64]


def test_restore_set_listing_recovers_only_a_receipt_bound_repository(
    monkeypatch,
) -> None:
    events: list[object] = []
    repository_id = "d" * 64
    monkeypatch.setattr(
        restore_policy,
        "_runtime_config",
        lambda _repository: {
            "repository": "/mnt/off-device/repository",
            "repositoryMount": "/mnt/off-device",
        },
    )
    monkeypatch.setattr(restore_policy, "_credential_password", lambda: b"secret")
    monkeypatch.setattr(
        nas_data_backup.BackupSetRestoreReceipts,
        "unfinished_repository_ids",
        lambda _directory: frozenset({repository_id}),
    )
    monkeypatch.setattr(
        nas_data_backup,
        "_unlock_stale_repository",
        lambda **kwargs: events.append(("unlock", kwargs["expected_repository_ids"])) or True,
    )
    monkeypatch.setattr(
        nas_data_backup,
        "list_backup_sets",
        lambda **_kwargs: (
            events.append("list")
            or {
                "repositoryId": repository_id,
                "setCount": 0,
                "sets": [],
                "truncated": False,
                "encrypted": True,
                "pathsRedacted": True,
                "verification": "authenticated_index_only",
            }
        ),
    )

    result = restore_policy.list_restore_sets(
        {
            "schema": restore_policy.REPOSITORY_SCHEMA,
            "repository": "/mnt/off-device/repository",
            "repositoryMount": "/mnt/off-device",
        }
    )

    assert result["setCount"] == 0
    assert events == [("unlock", [repository_id]), "list"]


def test_restore_target_inventory_exposes_identity_but_not_path(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "replacement"
    target.mkdir()
    monkeypatch.setattr(
        native_storage,
        "_registry_load",
        lambda **_kwargs: [
            {
                "uuid": TARGET_REF,
                "name": "restored-photos",
                "relativePath": "restored-photos",
                "storageKind": "btrfsSubvolume",
            }
        ],
    )
    monkeypatch.setattr(
        restore_policy,
        "_ensure_target_quiesced",
        lambda _ref: (target, TARGET_FILESYSTEM_UUID),
    )

    listing = restore_policy.list_restore_targets()

    assert listing["targetCount"] == 1
    assert listing["targets"] == [
        {
            "sharedFolderRef": TARGET_REF,
            "name": "restored-photos",
            "filesystemUuid": TARGET_FILESYSTEM_UUID,
            "empty": True,
        }
    ]
    assert str(tmp_path) not in str(listing)


def test_source_digest_mapping_is_exact() -> None:
    selected, _manifest = _selected()
    selected["members"][0]["sourcePathSha256"] = hashlib.sha256(b"/different/source").hexdigest()
    with pytest.raises(restore_policy.NasBackupRestorePolicyError):
        restore_policy._manifest_for_snapshot(
            selected,
            original_target_resolver=lambda _ref, _source: TARGET.as_posix(),
        )


@pytest.mark.parametrize(
    ("smb_inventory", "expected_code"),
    [
        (ReadOutput("", state="error", code="command_failed"), "target_unavailable"),
        (ReadOutput("photos\n"), "share_published"),
    ],
)
def test_target_quiescence_fails_closed_when_smb_inventory_is_untrusted_or_active(
    monkeypatch, smb_inventory: ReadOutput, expected_code: str
) -> None:
    monkeypatch.setattr(
        native_btrfs_snapshot,
        "_resolve_share",
        lambda _ref: ({"name": "photos"}, TARGET, {"readOnly": False}),
    )
    monkeypatch.setattr(native_time_machine, "dependency_for", lambda _ref: None)
    monkeypatch.setattr(native_storage, "_run", lambda *_args, **_kwargs: smb_inventory)
    monkeypatch.setattr(native_storage, "_nfs_exports_load", lambda **_kwargs: [])
    monkeypatch.setattr(
        btrfs_snapshot_schedule_policy,
        "read_policy",
        lambda: (True, {"schemaVersion": 2, "shares": []}),
    )
    monkeypatch.setattr(
        native_btrfs_snapshot,
        "_btrfs_filesystem_uuid",
        lambda _target: FILESYSTEM_UUID,
    )

    with pytest.raises(restore_policy.NasBackupRestorePolicyError) as caught:
        restore_policy._ensure_target_quiesced(SHARE_REF)

    assert caught.value.code == expected_code
