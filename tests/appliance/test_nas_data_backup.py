from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest

from deploy.appliance import nas_data_backup as backup
from deploy.appliance import nas_data_backup_support as support


def completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, "")


SHARE_REF = "11111111-2222-4333-8444-555555555555"
FILESYSTEM_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
TARGET_SUBVOLUME_UUID = "12345678-1111-2222-3333-444444444444"
SNAPSHOT_SUBVOLUME_UUID = "87654321-9999-8888-7777-666666666666"


def backup_set_manifest(volume: PurePosixPath | None = None) -> dict[str, object]:
    volume = volume or PurePosixPath("/volume1")
    snapshot_name = "before_backup"
    return {
        "schema": backup.BACKUP_SET_SCHEMA,
        "setId": "99999999-aaaa-4bbb-8ccc-dddddddddddd",
        "createdAt": "2026-09-08T06:00:00+08:00",
        "members": [
            {
                "sharedFolderRef": SHARE_REF,
                "filesystemUuid": FILESYSTEM_UUID,
                "snapshotId": str(
                    uuid.uuid5(
                        backup._BTRFS_SNAPSHOT_NAMESPACE,
                        f"{SHARE_REF}:{snapshot_name}",
                    )
                ),
                "sourceSnapshot": str(volume / ".echo-snapshots" / SHARE_REF / snapshot_name),
                "restoreTarget": str(volume / "photos"),
                "storageKind": "btrfsSubvolume",
            }
        ],
    }


def test_read_only_snapshot_uses_deepest_mount(tmp_path: Path) -> None:
    source = tmp_path / "snapshots" / "daily"
    source.mkdir(parents=True)
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(
        "1 0 0:1 / / rw,relatime - ext4 /dev/root rw\n"
        f"2 1 0:2 / {tmp_path / 'snapshots'} ro,nodev - btrfs /dev/mapper/nas ro\n",
        encoding="utf-8",
    )

    record = backup._require_read_only_snapshot(source, mountinfo)

    assert record["mountpoint"] == str(tmp_path / "snapshots")
    assert record["filesystem"] == "btrfs"
    assert len(record["sourceSha256"]) == 64


def test_backup_lock_uses_an_echo_private_runtime_directory() -> None:
    assert Path("/run/echo-os/nas-data-backup.lock") == backup.LOCK_FILE


def test_restic_uses_the_supported_repository_flag(tmp_path: Path) -> None:
    command = backup._restic_base(tmp_path / "repository", 17)

    assert command[1:3] == ["--repo", str(tmp_path / "repository")]
    assert "--repository" not in command


def test_filesystem_durability_barrier_uses_syncfs(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []

    class Syncfs:
        argtypes = None
        restype = None

        def __call__(self, descriptor: int) -> int:
            calls.append(("syncfs", descriptor))
            return 0

    syncfs = Syncfs()
    monkeypatch.setattr(backup.os, "name", "posix")
    monkeypatch.setattr(
        backup.os,
        "open",
        lambda path, flags: calls.append(("open", (path, flags))) or 41,
    )
    monkeypatch.setattr(backup.os, "close", lambda descriptor: calls.append(("close", descriptor)))
    monkeypatch.setattr(
        backup.ctypes,
        "CDLL",
        lambda _name, *, use_errno: SimpleNamespace(syncfs=syncfs),
    )

    backup._sync_filesystem(Path("/mnt/managed-restore"))

    assert calls[0][0] == "open"
    assert calls[1:] == [("syncfs", 41), ("close", 41)]
    assert syncfs.argtypes == [backup.ctypes.c_int]
    assert syncfs.restype is backup.ctypes.c_int


def test_stale_repository_unlock_never_removes_active_locks(tmp_path: Path, monkeypatch) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    commands: list[list[str]] = []

    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if command[-2:] == ["cat", "config"]:
            assert "--no-lock" in command
            return completed(json.dumps({"id": "d" * 64}))
        return completed()

    monkeypatch.setattr(backup, "_context", lambda **_kwargs: (repository, tmp_path))
    monkeypatch.setattr(backup, "_operation_lock", no_lock)
    monkeypatch.setattr(backup, "_password_memfd", fake_password)

    backup._unlock_stale_repository(
        repository=repository,
        repository_mount=tmp_path,
        deployment_root=tmp_path,
        appliance_env=None,
        password=b"controlled-test-password",
        expected_repository_ids=["d" * 64],
        runner=runner,
    )

    assert len(commands) == 2
    assert commands[0][-2:] == ["cat", "config"]
    assert commands[1][-1] == "unlock"
    assert "--remove-all" not in commands[1]


def test_stale_repository_unlock_rejects_a_different_repository(
    tmp_path: Path, monkeypatch
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    commands: list[list[str]] = []

    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return completed(json.dumps({"id": "d" * 64}))

    monkeypatch.setattr(backup, "_context", lambda **_kwargs: (repository, tmp_path))
    monkeypatch.setattr(backup, "_operation_lock", no_lock)
    monkeypatch.setattr(backup, "_password_memfd", fake_password)

    assert (
        backup._unlock_stale_repository(
            repository=repository,
            repository_mount=tmp_path,
            deployment_root=tmp_path,
            appliance_env=None,
            password=b"controlled-test-password",
            expected_repository_ids=["c" * 64],
            runner=runner,
        )
        is False
    )
    assert len(commands) == 1
    assert commands[0][-2:] == ["cat", "config"]
    assert "--no-lock" in commands[0]


def test_unfinished_restore_inventory_is_bounded_and_repository_bound(
    tmp_path: Path, monkeypatch
) -> None:
    set_id = "11111111-2222-4333-8444-555555555555"
    other_set = "22222222-3333-4444-8555-666666666666"
    (tmp_path / f"{set_id}.json").touch()
    (tmp_path / f"{other_set}.json").touch()
    (tmp_path / ".set-receipt-interrupted").touch()
    receipts = {
        set_id: {
            "schema": "echo.nas-set-restore-receipt.v1",
            "setId": set_id,
            "repositoryId": "d" * 64,
            "snapshotId": "a" * 64,
            "manifestSha256": "b" * 64,
            "planId": "c" * 64,
            "phase": "promoting",
            "members": [],
            "createdAt": "2026-09-08T00:00:00+00:00",
            "updatedAt": "2026-09-08T00:01:00+00:00",
        },
        other_set: {
            "schema": "echo.nas-set-restore-receipt.v1",
            "setId": other_set,
            "repositoryId": "e" * 64,
            "snapshotId": "f" * 64,
            "manifestSha256": "1" * 64,
            "planId": "2" * 64,
            "phase": "verified",
            "members": [],
            "createdAt": "2026-09-08T00:00:00+00:00",
            "updatedAt": "2026-09-08T00:01:00+00:00",
        },
    }
    monkeypatch.setattr(support, "_private_directory", lambda _directory: None)
    monkeypatch.setattr(
        support.BackupSetRestoreReceipts,
        "load",
        lambda self: receipts[self.set_id],
    )

    assert support.BackupSetRestoreReceipts.unfinished_repository_ids(tmp_path) == {"d" * 64}


def test_backup_set_manifest_is_canonical_and_path_redacted(tmp_path: Path) -> None:
    manifest = backup._validate_backup_set_manifest(backup_set_manifest())

    assert manifest["createdAt"] == "2026-09-07T22:00:00.000000Z"
    assert len(manifest["manifestSha256"]) == 64
    assert manifest["members"][0]["sharedFolderRef"] == SHARE_REF


def test_backup_set_manifest_rejects_single_share_disguised_as_whole_nas() -> None:
    value = backup_set_manifest()
    value["members"][0]["restoreTarget"] = "/data/nas"

    with pytest.raises(backup.NasDataBackupError, match="managed snapshot layout"):
        backup._validate_backup_set_manifest(value)


def test_backup_set_manifest_rejects_restic_pattern_metacharacters() -> None:
    value = backup_set_manifest()
    value["members"][0]["sourceSnapshot"] = f"/volume1/.echo-snapshots/{SHARE_REF}/before_*"

    with pytest.raises(backup.NasDataBackupError, match="absolute normalized path"):
        backup._validate_backup_set_manifest(value)


def test_backup_set_manifest_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    with pytest.raises(backup.NasDataBackupError, match="duplicate JSON keys"):
        backup._strict_json(
            b'{"schema":"echo.nas-backup-set.v1","schema":"other"}',
            "NAS backup-set manifest",
        )


@pytest.mark.skipif(os.name == "nt", reason="POSIX ownership and modes require a POSIX host")
def test_backup_set_manifest_loader_requires_private_regular_file(tmp_path: Path) -> None:
    manifest_path = tmp_path / "backup-set.json"
    manifest_path.write_text(json.dumps(backup_set_manifest()), encoding="utf-8")
    manifest_path.chmod(0o600)

    loaded = backup.load_backup_set_manifest(manifest_path, owner=manifest_path.stat().st_uid)
    assert loaded["schema"] == backup.BACKUP_SET_SCHEMA

    manifest_path.chmod(0o644)
    with pytest.raises(backup.NasDataBackupError, match="manifest is unsafe"):
        backup.load_backup_set_manifest(manifest_path, owner=manifest_path.stat().st_uid)


def test_backup_set_plan_binds_btrfs_lineage_without_exposing_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    volume = tmp_path / "volume1"
    source = volume / ".echo-snapshots" / SHARE_REF / "before_backup"
    target = volume / "photos"
    repository = tmp_path / "repository"
    source.mkdir(parents=True)
    target.mkdir()
    repository.mkdir()
    manifest = backup._validate_backup_set_manifest(backup_set_manifest())
    real_safe_directory = backup._safe_directory

    class BoundPath:
        def __init__(self, path: Path, device: int) -> None:
            self.path = path
            self.device = device

        def stat(self):
            metadata = self.path.stat()
            return SimpleNamespace(st_dev=self.device, st_ino=metadata.st_ino)

        def __str__(self) -> str:
            return str(self.path)

    source_bound = BoundPath(source, 101)
    target_bound = BoundPath(target, 102)

    def safe_directory(path: Path, label: str):
        translated = source if label == "managed snapshot source" else target
        real_safe_directory(translated or path, label)
        return source_bound if label == "managed snapshot source" else target_bound

    class Repository:
        def stat(self):
            return SimpleNamespace(st_dev=103, st_ino=9001)

    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        selected = command[-1]
        if command[1:3] == ["subvolume", "show"]:
            if selected == str(source_bound):
                return completed(
                    f"UUID: {SNAPSHOT_SUBVOLUME_UUID}\nParent UUID: {TARGET_SUBVOLUME_UUID}\n"
                )
            return completed(f"UUID: {TARGET_SUBVOLUME_UUID}\nParent UUID: -\n")
        if command[1:4] == ["property", "get", "-ts"]:
            return completed("ro=true\n" if command[-2] == str(source_bound) else "ro=false\n")
        if command[0] == "/usr/bin/findmnt":
            return completed(FILESYSTEM_UUID + "\n")
        raise AssertionError(command)

    monkeypatch.setattr(backup, "_safe_directory", safe_directory)
    monkeypatch.setattr(backup, "_context", lambda **_kwargs: (Repository(), target))
    monkeypatch.setattr(
        backup,
        "_mount_record",
        lambda path: {
            "filesystem": "ext4" if isinstance(path, Repository) else "btrfs",
            "source": "/dev/vdc" if isinstance(path, Repository) else "/dev/vdb",
        },
    )

    plan = backup.plan_backup_set(
        manifest=manifest,
        repository=repository,
        repository_mount=tmp_path,
        deployment_root=tmp_path,
        appliance_env=None,
        runner=runner,
    )

    assert plan["schema"] == backup.BACKUP_SET_PLAN_SCHEMA
    assert plan["memberCount"] == 1
    assert plan["pathsRedacted"] is True
    assert len(plan["planId"]) == 64
    serialized = json.dumps(plan)
    assert "/volume1" not in serialized
    assert str(tmp_path) not in serialized


def test_backup_set_plan_rejects_snapshot_with_wrong_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    manifest = backup._validate_backup_set_manifest(backup_set_manifest())
    monkeypatch.setattr(
        backup,
        "_safe_directory",
        lambda path, _label: source if "echo-snapshots" in str(path) else target,
    )
    monkeypatch.setattr(
        backup,
        "_context",
        lambda **_kwargs: (
            SimpleNamespace(stat=lambda: SimpleNamespace(st_dev=999, st_ino=9002)),
            target,
        ),
    )
    monkeypatch.setattr(
        backup,
        "_mount_record",
        lambda path: {
            "filesystem": "ext4" if not isinstance(path, Path) else "btrfs",
            "source": "/dev/vdc" if not isinstance(path, Path) else "/dev/vdb",
        },
    )
    identities = iter(
        [
            {
                "subvolumeUuid": SNAPSHOT_SUBVOLUME_UUID,
                "parentUuid": "00000000-1111-2222-3333-444444444444",
                "readOnly": True,
                "filesystemUuid": FILESYSTEM_UUID,
            },
            {
                "subvolumeUuid": TARGET_SUBVOLUME_UUID,
                "parentUuid": None,
                "readOnly": False,
                "filesystemUuid": FILESYSTEM_UUID,
            },
        ]
    )
    monkeypatch.setattr(backup, "_btrfs_subvolume_identity", lambda *_args: next(identities))

    with pytest.raises(backup.NasDataBackupError, match="read-only child"):
        backup.plan_backup_set(
            manifest=manifest,
            repository=tmp_path / "repository",
            repository_mount=tmp_path,
            deployment_root=tmp_path,
            appliance_env=None,
        )


def test_authenticated_backup_set_metadata_binds_every_source_path() -> None:
    manifest = backup._validate_backup_set_manifest(backup_set_manifest())
    tags = backup._backup_set_tags(manifest)
    item = {
        "id": "a" * 64,
        "time": "2026-09-08T06:10:00Z",
        "paths": [manifest["members"][0]["sourceSnapshot"]],
        "tags": tags,
    }

    parsed = backup._parse_backup_set_snapshot(item)

    assert parsed["setId"] == manifest["setId"]
    assert parsed["manifestSha256"] == manifest["manifestSha256"]
    assert parsed["members"][0]["sharedFolderRef"] == SHARE_REF
    tampered = {**item, "paths": ["/volume1/.echo-snapshots/other/replaced"]}
    with pytest.raises(backup.NasDataBackupError, match="member mapping"):
        backup._parse_backup_set_snapshot(tampered)


def test_backup_set_consumes_plan_and_verifies_authenticated_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    second_ref = "22222222-3333-4444-8555-666666666666"
    value = backup_set_manifest()
    second_name = "daily_copy"
    value["members"].append(
        {
            "sharedFolderRef": second_ref,
            "filesystemUuid": FILESYSTEM_UUID,
            "snapshotId": str(
                uuid.uuid5(
                    backup._BTRFS_SNAPSHOT_NAMESPACE,
                    f"{second_ref}:{second_name}",
                )
            ),
            "sourceSnapshot": f"/volume1/.echo-snapshots/{second_ref}/{second_name}",
            "restoreTarget": "/volume1/videos",
            "storageKind": "btrfsSubvolume",
        }
    )
    manifest = backup._validate_backup_set_manifest(value)
    repository = tmp_path / "repository"
    repository.mkdir()
    snapshot_id = "b" * 64
    commands: list[list[str]] = []
    snapshot_queries = 0
    plan = {
        "planId": "c" * 64,
        "members": [
            {
                "sharedFolderRef": member["sharedFolderRef"],
                "filesystemUuid": member["filesystemUuid"],
                "snapshotId": member["snapshotId"],
                "snapshotSubvolumeUuid": SNAPSHOT_SUBVOLUME_UUID,
            }
            for member in manifest["members"]
        ],
    }
    indexed = {
        "id": snapshot_id,
        "time": "2026-09-08T06:20:00Z",
        "paths": [member["sourceSnapshot"] for member in manifest["members"]],
        "tags": backup._backup_set_tags(manifest),
    }

    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal snapshot_queries
        commands.append(command)
        if command[-2:] == ["cat", "config"]:
            return completed(json.dumps({"id": "d" * 64}))
        if "snapshots" in command:
            snapshot_queries += 1
            return completed(json.dumps([] if snapshot_queries == 1 else [indexed]))
        if "backup" in command:
            return completed(
                json.dumps({"message_type": "summary", "snapshot_id": snapshot_id}) + "\n"
            )
        if "check" in command:
            return completed()
        raise AssertionError(command)

    monkeypatch.setattr(backup, "plan_backup_set", lambda **_kwargs: plan)
    monkeypatch.setattr(backup, "_context", lambda **_kwargs: (repository, tmp_path))
    monkeypatch.setattr(backup, "_operation_lock", no_lock)
    monkeypatch.setattr(backup, "_password_memfd", fake_password)

    report = backup.backup_set(
        manifest=manifest,
        plan_id=plan["planId"],
        repository=repository,
        repository_mount=tmp_path,
        deployment_root=tmp_path,
        appliance_env=None,
        password=b"controlled-test-password",
        runner=runner,
    )

    assert report["snapshotId"] == snapshot_id
    assert report["memberCount"] == 2
    assert report["fullReadVerified"] is True
    assert report["pathsRedacted"] is True
    assert report["idempotent"] is False
    backup_command = next(command for command in commands if "backup" in command)
    assert all(member["sourceSnapshot"] in backup_command for member in manifest["members"])
    assert backup.BACKUP_SET_TAG in backup_command
    assert str(tmp_path) not in json.dumps(report)


def test_restore_set_plan_binds_empty_targets_and_authenticated_members(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = backup._validate_backup_set_manifest(backup_set_manifest())
    target = tmp_path / "photos"
    target.mkdir()
    snapshot = {
        "id": "e" * 64,
        "time": "2026-09-08T06:20:00Z",
        "paths": [manifest["members"][0]["sourceSnapshot"]],
        "tags": backup._backup_set_tags(manifest),
    }

    class Repository:
        def stat(self):
            return SimpleNamespace(st_dev=991, st_ino=9003)

        def __str__(self) -> str:
            return "/mnt/off-device/repository"

    repository = Repository()

    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[-2:] == ["cat", "config"]:
            return completed(json.dumps({"id": "f" * 64}))
        if "snapshots" in command:
            return completed(json.dumps([snapshot]))
        raise AssertionError(command)

    monkeypatch.setattr(backup, "_context", lambda **_kwargs: (repository, target))
    monkeypatch.setattr(backup, "_operation_lock", no_lock)
    monkeypatch.setattr(backup, "_password_memfd", fake_password)
    monkeypatch.setattr(backup, "_require_empty", lambda *_args: target)
    monkeypatch.setattr(
        backup,
        "_mount_record",
        lambda path: {
            "filesystem": "ext4" if isinstance(path, Repository) else "btrfs",
            "source": "/dev/vdc" if isinstance(path, Repository) else "/dev/vdb",
        },
    )
    monkeypatch.setattr(
        backup,
        "_btrfs_subvolume_identity",
        lambda *_args: {
            "subvolumeUuid": TARGET_SUBVOLUME_UUID,
            "parentUuid": None,
            "readOnly": False,
            "filesystemUuid": FILESYSTEM_UUID,
        },
    )

    plan = backup.plan_restore_set(
        manifest=manifest,
        selector=manifest["setId"],
        repository=tmp_path / "repository",
        repository_mount=tmp_path,
        deployment_root=tmp_path,
        appliance_env=None,
        password=b"controlled-test-password",
        runner=runner,
    )

    assert plan["schema"] == "echo.nas-backup-set-restore-plan.v1"
    assert plan["snapshotId"] == "e" * 64
    assert plan["memberCount"] == 1
    assert plan["confirmation"] == (f"RESTORE ECHO NAS SET {manifest['setId']} SNAPSHOT {'e' * 64}")
    assert plan["pathsRedacted"] is True
    assert "/volume1" not in json.dumps(plan)
    assert str(tmp_path) not in json.dumps(plan)


def test_restore_set_plan_rejects_manifest_not_bound_to_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = backup._validate_backup_set_manifest(backup_set_manifest())
    snapshot = {
        "id": "e" * 64,
        "time": "2026-09-08T06:20:00Z",
        "paths": [manifest["members"][0]["sourceSnapshot"]],
        "tags": backup._backup_set_tags({**manifest, "manifestSha256": "0" * 64}),
    }
    repository = tmp_path / "repository"
    repository.mkdir()

    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[-2:] == ["cat", "config"]:
            return completed(json.dumps({"id": "f" * 64}))
        if "snapshots" in command:
            return completed(json.dumps([snapshot]))
        raise AssertionError(command)

    monkeypatch.setattr(backup, "_context", lambda **_kwargs: (repository, tmp_path))
    monkeypatch.setattr(backup, "_operation_lock", no_lock)
    monkeypatch.setattr(backup, "_password_memfd", fake_password)
    monkeypatch.setattr(
        backup,
        "_mount_record",
        lambda _path: {"filesystem": "ext4", "source": "/dev/vdc"},
    )

    with pytest.raises(backup.NasDataBackupError, match="does not match authenticated"):
        backup.plan_restore_set(
            manifest=manifest,
            selector=manifest["setId"],
            repository=repository,
            repository_mount=tmp_path,
            deployment_root=tmp_path,
            appliance_env=None,
            password=b"controlled-test-password",
            runner=runner,
        )


def test_restore_set_plan_binds_explicit_replacement_filesystem_without_leaking_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = backup._validate_backup_set_manifest(backup_set_manifest())
    target_path = "/volume2/replacement/photos"
    replacement_filesystem = "77777777-8888-4999-8aaa-bbbbbbbbbbbb"
    snapshot = {
        "id": "e" * 64,
        "time": "2026-09-08T06:20:00Z",
        "paths": [manifest["members"][0]["sourceSnapshot"]],
        "tags": backup._backup_set_tags(manifest),
    }

    class Repository:
        def stat(self):
            return SimpleNamespace(st_dev=991, st_ino=9003)

    class Target:
        def stat(self):
            return SimpleNamespace(st_dev=992, st_ino=9004)

    repository = Repository()
    target = Target()

    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[-2:] == ["cat", "config"]:
            return completed(json.dumps({"id": "f" * 64}))
        if "snapshots" in command:
            return completed(json.dumps([snapshot]))
        raise AssertionError(command)

    monkeypatch.setattr(backup, "_context", lambda **_kwargs: (repository, target))
    monkeypatch.setattr(backup, "_operation_lock", no_lock)
    monkeypatch.setattr(backup, "_password_memfd", fake_password)
    monkeypatch.setattr(backup, "_require_empty", lambda *_args: target)
    monkeypatch.setattr(
        backup,
        "_mount_record",
        lambda path: {
            "filesystem": "ext4" if isinstance(path, Repository) else "btrfs",
            "source": "/dev/vdc" if isinstance(path, Repository) else "/dev/vdd",
        },
    )
    monkeypatch.setattr(
        backup,
        "_btrfs_subvolume_identity",
        lambda *_args: {
            "subvolumeUuid": TARGET_SUBVOLUME_UUID,
            "parentUuid": None,
            "readOnly": False,
            "filesystemUuid": replacement_filesystem,
        },
    )

    plan = backup.plan_restore_set(
        manifest=manifest,
        selector=manifest["setId"],
        repository=tmp_path / "repository",
        repository_mount=tmp_path,
        deployment_root=tmp_path,
        appliance_env=None,
        password=b"controlled-test-password",
        restore_targets={
            SHARE_REF: {
                "restoreTarget": target_path,
                "filesystemUuid": replacement_filesystem,
            }
        },
        runner=runner,
    )

    assert plan["members"][0]["filesystemUuid"] == FILESYSTEM_UUID
    assert plan["members"][0]["targetFilesystemUuid"] == replacement_filesystem
    assert plan["members"][0]["remapped"] is True
    assert target_path not in json.dumps(plan)


def test_restore_target_mapping_rejects_incomplete_and_nested_targets() -> None:
    raw = backup_set_manifest()
    raw["members"].append(
        {
            **raw["members"][0],
            "sharedFolderRef": "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff",
            "snapshotId": backup._canonical_uuid(
                "22222222-3333-4444-8555-666666666666", "snapshot ID"
            ),
            "sourceSnapshot": (
                "/volume1/.echo-snapshots/"
                "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff/auto-20260908t061500z"
            ),
            "restoreTarget": "/volume1/videos",
        }
    )
    # Use the deterministic ID expected by the manifest validator.
    raw["members"][1]["snapshotId"] = str(
        uuid.uuid5(
            backup._BTRFS_SNAPSHOT_NAMESPACE,
            "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff:auto-20260908t061500z",
        )
    )
    manifest = backup._validate_backup_set_manifest(raw)
    with pytest.raises(backup.NasDataBackupError, match="incomplete"):
        backup._normalize_restore_targets(
            manifest,
            {
                SHARE_REF: {
                    "restoreTarget": "/replacement/photos",
                    "filesystemUuid": FILESYSTEM_UUID,
                }
            },
        )
    with pytest.raises(backup.NasDataBackupError, match="nested"):
        backup._normalize_restore_targets(
            manifest,
            {
                SHARE_REF: {
                    "restoreTarget": "/replacement/data",
                    "filesystemUuid": FILESYSTEM_UUID,
                },
                "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff": {
                    "restoreTarget": "/replacement/data/videos",
                    "filesystemUuid": FILESYSTEM_UUID,
                },
            },
        )


def test_restore_set_stages_exchanges_verifies_and_resumes_without_rewrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = backup._validate_backup_set_manifest(backup_set_manifest())
    repository = tmp_path / "repository"
    repository.mkdir()
    volume = tmp_path / "volume"
    volume.mkdir()
    target = volume / "photos"
    target.mkdir()
    receipt_root = tmp_path / "receipts"
    snapshot_id = "e" * 64
    plan_id = "f" * 64
    replacement_target = "/volume2/replacement/photos"
    replacement_filesystem = "77777777-8888-4999-8aaa-bbbbbbbbbbbb"
    confirmation = f"RESTORE ECHO NAS SET {manifest['setId']} SNAPSHOT {snapshot_id}"
    selected = backup._parse_backup_set_snapshot(
        {
            "id": snapshot_id,
            "time": "2026-09-08T06:20:00Z",
            "paths": [manifest["members"][0]["sourceSnapshot"]],
            "tags": backup._backup_set_tags(manifest),
        }
    )
    identities = {str(target): TARGET_SUBVOLUME_UUID}
    receipts: dict[str, dict[str, object]] = {}
    restore_calls = 0
    durability_events: list[str] = []
    real_safe_directory = backup._safe_directory
    real_require_empty = backup._require_empty

    class MemoryReceipts:
        def __init__(self, _directory: Path, set_id: str) -> None:
            self.set_id = set_id

        def load(self):
            value = receipts.get(self.set_id)
            return json.loads(json.dumps(value)) if value is not None else None

        def save(self, value: dict[str, object]) -> None:
            receipts[self.set_id] = json.loads(
                json.dumps(
                    {
                        **value,
                        "schema": "echo.nas-set-restore-receipt.v1",
                        "setId": self.set_id,
                    }
                )
            )

    def mapped(path: Path) -> Path:
        return target if str(path).replace("\\", "/") == replacement_target else path

    def safe_directory(path: Path, label: str) -> Path:
        return real_safe_directory(mapped(path), label)

    def require_empty(path: Path, label: str) -> Path:
        return real_require_empty(mapped(path), label)

    def identity(path: Path, _runner=None) -> dict[str, object]:
        return {
            "subvolumeUuid": identities[str(path)],
            "parentUuid": None,
            "readOnly": False,
            "filesystemUuid": replacement_filesystem,
        }

    def create_staging(staging: Path, restored: Path, _runner) -> dict[str, object]:
        restored.mkdir(parents=True)
        identities[str(restored)] = SNAPSHOT_SUBVOLUME_UUID
        return identity(restored)

    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal restore_calls
        if command[-2:] == ["cat", "config"]:
            return completed(json.dumps({"id": "a" * 64}))
        if "snapshots" in command:
            return completed(
                json.dumps(
                    [
                        {
                            "id": selected["id"],
                            "time": selected["time"],
                            "paths": selected["paths"],
                            "tags": backup._backup_set_tags(manifest),
                        }
                    ]
                )
            )
        if "restore" in command:
            restore_calls += 1
            staging = Path(command[command.index("--target") + 1])
            restored = staging.joinpath(
                *PurePosixPath(manifest["members"][0]["sourceSnapshot"]).parts[1:]
            )
            (restored / "photo.bin").write_bytes(b"restored-photo")
            return completed()
        if "check" in command:
            return completed()
        raise AssertionError(command)

    def exchange(left: Path, right: Path) -> None:
        durability_events.append("exchange")
        left_uuid, right_uuid = identities[str(left)], identities[str(right)]
        temporary = left.parent / ".exchange-test"
        left.rename(temporary)
        right.rename(left)
        temporary.rename(right)
        identities[str(left)], identities[str(right)] = right_uuid, left_uuid

    def sync_filesystem(path: Path) -> None:
        durability_events.append("sync-target" if mapped(path) == target else "sync-staging")

    def cleanup(
        staging: Path,
        restored: Path,
        *,
        expected_subvolume_uuid: str,
        runner,
    ) -> None:
        assert identities[str(restored)] == expected_subvolume_uuid
        restored.rmdir()
        identities.pop(str(restored))
        cursor = restored.parent
        while cursor != staging:
            cursor.rmdir()
            cursor = cursor.parent
        staging.rmdir()

    plan = {
        "planId": plan_id,
        "snapshotId": snapshot_id,
        "confirmation": confirmation,
        "members": [
            {
                "sharedFolderRef": SHARE_REF,
                "targetSubvolumeUuid": TARGET_SUBVOLUME_UUID,
            }
        ],
    }
    monkeypatch.setattr(backup, "BackupSetRestoreReceipts", MemoryReceipts)
    monkeypatch.setattr(backup, "_operation_lock", no_lock)
    monkeypatch.setattr(backup, "_context", lambda **_kwargs: (repository, target))
    monkeypatch.setattr(backup, "_plan_restore_set_unlocked", lambda **_kwargs: plan)
    monkeypatch.setattr(backup, "_password_memfd", fake_password)
    monkeypatch.setattr(backup, "_safe_directory", safe_directory)
    monkeypatch.setattr(backup, "_require_empty", require_empty)
    monkeypatch.setattr(backup, "_btrfs_subvolume_identity", identity)
    monkeypatch.setattr(backup, "_create_set_staging_subvolume", create_staging)
    monkeypatch.setattr(backup, "_sync_filesystem", sync_filesystem)
    monkeypatch.setattr(backup, "_remove_set_staging", cleanup)
    monkeypatch.setattr(backup, "_safe_set_staging", lambda staging, _target: staging)
    monkeypatch.setattr(
        backup,
        "_restored_root",
        lambda staging, _source: staging.joinpath(
            *PurePosixPath(manifest["members"][0]["sourceSnapshot"]).parts[1:]
        ),
    )
    monkeypatch.setattr(
        backup,
        "_validate_set_restore_receipt",
        lambda value, **_kwargs: value,
    )

    arguments = {
        "manifest": manifest,
        "selector": "latest",
        "plan_id": plan_id,
        "confirmation": confirmation,
        "repository": repository,
        "repository_mount": tmp_path,
        "deployment_root": tmp_path,
        "appliance_env": None,
        "password": b"controlled-test-password",
        "restore_targets": {
            SHARE_REF: {
                "restoreTarget": replacement_target,
                "filesystemUuid": replacement_filesystem,
            }
        },
        "runner": runner,
        "exchange": exchange,
        "receipt_directory": receipt_root,
    }
    first = backup.restore_set(**arguments)
    second = backup.restore_set(**arguments)

    assert first["phase"] == "verified"
    assert first["contentVerified"] is True
    assert first["recovery"] == "fresh_restore"
    assert second["recovery"] == "resumed"
    assert restore_calls == 1
    assert durability_events == ["sync-staging", "exchange", "sync-target"]
    assert (target / "photo.bin").read_bytes() == b"restored-photo"
    assert receipts[manifest["setId"]]["members"][0]["restoreTarget"] == replacement_target
    assert receipts[manifest["setId"]]["members"][0]["filesystemUuid"] == replacement_filesystem
    assert replacement_target not in json.dumps(first)
    assert str(tmp_path) not in json.dumps(first)


@pytest.mark.parametrize(
    ("phase", "member_state", "expected"),
    [
        ("prepared", "prepared", False),
        ("promoting", "prepared", None),
        ("promoting", "promoted", True),
        ("verified", "verified", True),
    ],
)
def test_restore_set_commit_hint_is_honest_about_exchange_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    member_state: str,
    expected: bool | None,
) -> None:
    class Receipts:
        def __init__(self, _directory: Path, _set_id: str) -> None:
            pass

        def load(self):
            return {"phase": phase, "members": [{"state": member_state}]}

    monkeypatch.setattr(backup, "BackupSetRestoreReceipts", Receipts)

    assert (
        backup._restore_set_commit_hint({"setId": "99999999-aaaa-4bbb-8ccc-dddddddddddd"}, tmp_path)
        is expected
    )


@pytest.mark.skipif(os.name == "nt", reason="POSIX ownership and modes require a POSIX host")
def test_backup_lock_directory_is_created_private(tmp_path: Path) -> None:
    runtime = tmp_path / "run"
    runtime.mkdir(mode=0o755)
    runtime.chmod(0o755)
    lock_directory = runtime / "echo-os"

    assert (
        backup._ensure_lock_directory(lock_directory, trusted_uid=runtime.stat().st_uid)
        == lock_directory
    )
    assert stat.S_IMODE(lock_directory.stat().st_mode) == 0o700

    lock_directory.chmod(0o750)
    with pytest.raises(backup.NasDataBackupError, match="lock directory is unsafe"):
        backup._ensure_lock_directory(lock_directory, trusted_uid=runtime.stat().st_uid)


def test_live_writable_tree_is_not_a_backup_source(tmp_path: Path) -> None:
    source = tmp_path / "nas"
    source.mkdir()
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(
        f"1 0 0:1 / {tmp_path} rw,relatime - ext4 /dev/root rw\n",
        encoding="utf-8",
    )

    with pytest.raises(backup.NasDataBackupError, match="read-only"):
        backup._require_read_only_snapshot(source, mountinfo)


def test_read_only_bind_of_live_ext4_tree_is_not_a_snapshot(tmp_path: Path) -> None:
    live = tmp_path / "live"
    source = tmp_path / "readonly-bind"
    live.mkdir()
    source.mkdir()
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(
        f"1 0 0:1 / {tmp_path} rw,relatime - ext4 /dev/md0 rw\n"
        f"2 1 0:1 /live {source} ro,nodev - ext4 /dev/md0 rw\n",
        encoding="utf-8",
    )

    with pytest.raises(backup.NasDataBackupError, match="independent filesystem snapshot"):
        backup._require_snapshot_independence(source, live, mountinfo)


def test_distinct_read_only_btrfs_subvolume_is_an_independent_snapshot(
    tmp_path: Path,
) -> None:
    live = tmp_path / "live"
    source = tmp_path / "snapshot"
    live.mkdir()
    source.mkdir()
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(
        f"1 0 0:1 / {tmp_path} rw,relatime - ext4 /dev/root rw\n"
        f"2 1 0:2 /@nas {live} rw,nodev - btrfs /dev/mapper/nas rw,subvolid=256\n"
        f"3 1 0:2 /@snapshots/daily {source} ro,nodev - btrfs /dev/mapper/nas ro,subvolid=300\n",
        encoding="utf-8",
    )

    backup._require_snapshot_independence(source, live, mountinfo)


def test_snapshot_selection_is_complete_and_unambiguous() -> None:
    snapshots = [
        {"id": "a" * 64, "path": "/srv/snap-a", "time": "2026-08-26T00:00:00Z"},
        {"id": "b" * 64, "path": "/srv/snap-b", "time": "2026-08-27T00:00:00Z"},
    ]

    assert backup._select_snapshot("latest", snapshots)["id"] == "b" * 64
    assert backup._select_snapshot("a" * 12, snapshots)["id"] == "a" * 64
    with pytest.raises(backup.NasDataBackupError, match="invalid"):
        backup._select_snapshot("not-a-snapshot", snapshots)


def test_latest_snapshot_uses_absolute_time_not_offset_text_order() -> None:
    snapshots = [
        {"id": "a" * 64, "path": "/srv/old", "time": "2026-09-08T23:30:00+08:00"},
        {"id": "b" * 64, "path": "/srv/new", "time": "2026-09-08T16:00:00Z"},
    ]

    assert backup._select_snapshot("latest", snapshots)["id"] == "b" * 64
    with pytest.raises(backup.NasDataBackupError, match="timezone"):
        backup._select_snapshot(
            "latest", [{"id": "c" * 64, "path": "/srv/bad", "time": "2026-09-08T16:00:00"}]
        )


def test_restored_tree_rejects_escaping_links_and_special_files(tmp_path: Path) -> None:
    root = tmp_path / "restored"
    root.mkdir()
    (root / "ok.txt").write_text("ok", encoding="utf-8")
    (root / "escape").symlink_to("../../outside")
    with pytest.raises(backup.NasDataBackupError, match="escaping symlink"):
        backup._tree_safe(root)

    (root / "escape").unlink()
    if not hasattr(os, "mkfifo"):
        pytest.skip("special FIFO files require POSIX; escaping link was checked above")
    os.mkfifo(root / "pipe")
    with pytest.raises(backup.NasDataBackupError, match="special file"):
        backup._tree_safe(root)


def test_restored_root_requires_only_the_authenticated_path(tmp_path: Path) -> None:
    staging = tmp_path / "stage"
    expected = staging / "srv" / "snapshots" / "daily"
    expected.mkdir(parents=True)
    assert backup._restored_root(staging, PurePosixPath("/srv/snapshots/daily")) == expected

    (staging / "unexpected").mkdir()
    with pytest.raises(backup.NasDataBackupError, match="unexpected path hierarchy"):
        backup._restored_root(staging, PurePosixPath("/srv/snapshots/daily"))


@contextmanager
def no_lock():
    yield


@contextmanager
def fake_password(password: bytes):
    with tempfile.TemporaryFile() as secret:
        secret.write(password)
        secret.flush()
        yield secret.fileno()


def test_each_restic_process_reads_the_password_from_the_start() -> None:
    reads: list[bytes] = []

    def runner(_command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        descriptor = kwargs["pass_fds"][0]
        reads.append(os.read(descriptor, 4096))
        return completed()

    with fake_password(b"one-password-for-many-commands") as descriptor:
        backup._restic(["restic", "check"], descriptor, runner)
        backup._restic(["restic", "cat", "config"], descriptor, runner)

    assert reads == [b"one-password-for-many-commands"] * 2


def test_snapshot_list_is_bounded_sorted_and_does_not_expose_source_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    nas = tmp_path / "nas"
    repository.mkdir()
    nas.mkdir()
    private_root = (tmp_path / "private" / "snapshots").resolve()
    snapshots = [
        {
            "id": "a" * 64,
            "paths": [str(private_root / "older")],
            "tags": [backup.TAG],
            "time": "2026-09-08T09:00:00+08:00",
        },
        {
            "id": "b" * 64,
            "paths": [str(private_root / "newer")],
            "tags": [backup.TAG],
            "time": "2026-09-08T02:00:00Z",
        },
    ]

    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[-2:] == ["cat", "config"]:
            return completed(json.dumps({"id": "d" * 64}))
        if "snapshots" in command:
            return completed(json.dumps(snapshots))
        raise AssertionError(command)

    monkeypatch.setattr(backup, "_context", lambda **_kwargs: (repository, nas))
    monkeypatch.setattr(backup, "_operation_lock", no_lock)
    monkeypatch.setattr(backup, "_password_memfd", fake_password)

    report = backup.list_snapshots(
        repository=repository,
        repository_mount=tmp_path,
        deployment_root=tmp_path,
        appliance_env=None,
        password=b"controlled-test-password",
        limit=1,
        runner=runner,
    )

    assert report["snapshotCount"] == 2
    assert report["truncated"] is True
    assert report["snapshots"] == [
        {"snapshotId": "b" * 64, "createdAt": "2026-09-08T02:00:00.000000Z"}
    ]
    assert str(private_root) not in json.dumps(report)
    with pytest.raises(backup.NasDataBackupError, match="between 1"):
        backup.list_snapshots(
            repository=repository,
            repository_mount=tmp_path,
            deployment_root=tmp_path,
            appliance_env=None,
            password=b"controlled-test-password",
            limit=0,
            runner=runner,
        )


def test_backup_set_list_is_bounded_sorted_and_path_redacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    manifest = backup._validate_backup_set_manifest(backup_set_manifest())
    older = {
        "id": "a" * 64,
        "time": "2026-09-08T09:00:00+08:00",
        "paths": [manifest["members"][0]["sourceSnapshot"]],
        "tags": backup._backup_set_tags(manifest),
    }
    newer_manifest = {
        **manifest,
        "setId": "88888888-aaaa-4bbb-8ccc-dddddddddddd",
    }
    newer = {
        "id": "b" * 64,
        "time": "2026-09-08T02:00:00Z",
        "paths": [manifest["members"][0]["sourceSnapshot"]],
        "tags": backup._backup_set_tags(newer_manifest),
    }

    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[-2:] == ["cat", "config"]:
            return completed(json.dumps({"id": "d" * 64}))
        if "snapshots" in command:
            return completed(json.dumps([older, newer]))
        raise AssertionError(command)

    monkeypatch.setattr(backup, "_context", lambda **_kwargs: (repository, tmp_path))
    monkeypatch.setattr(backup, "_operation_lock", no_lock)
    monkeypatch.setattr(backup, "_password_memfd", fake_password)

    report = backup.list_backup_sets(
        repository=repository,
        repository_mount=tmp_path,
        deployment_root=tmp_path,
        appliance_env=None,
        password=b"controlled-test-password",
        limit=1,
        runner=runner,
    )

    assert report["setCount"] == 2
    assert report["truncated"] is True
    assert report["sets"][0]["setId"] == newer_manifest["setId"]
    assert report["sets"][0]["createdAt"] == "2026-09-08T02:00:00.000000Z"
    assert report["sets"][0]["memberCount"] == 1
    assert report["pathsRedacted"] is True
    assert "/volume1" not in json.dumps(report)
    assert "sourcePathSha256" not in json.dumps(report)


@pytest.mark.skipif(os.name == "nt", reason="restic snapshot hierarchy uses POSIX source paths")
def test_restore_full_reads_then_atomically_promotes_empty_nas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    deployment = tmp_path / "deployment"
    deployment.mkdir()
    nas = deployment / "storage"
    nas.mkdir()
    snapshot_id = "c" * 64
    original = Path("/srv/echo-snapshots/nightly")
    commands: list[list[str]] = []

    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if command[-2:] == ["cat", "config"]:
            return completed(json.dumps({"id": "d" * 64}))
        if "snapshots" in command:
            return completed(
                json.dumps(
                    [
                        {
                            "id": snapshot_id,
                            "paths": [str(original)],
                            "tags": [backup.TAG],
                            "time": "2026-08-27T00:00:00Z",
                        }
                    ]
                )
            )
        if "restore" in command:
            target = Path(command[command.index("--target") + 1])
            restored = target.joinpath(*PurePosixPath(str(original)).parts[1:])
            restored.mkdir(parents=True)
            (restored / "family.mov").write_bytes(b"video")
            (restored / "album").mkdir()
            (restored / "album" / "photo.jpg").write_bytes(b"photo")
            return completed()
        if "check" in command:
            return completed()
        raise AssertionError(command)

    def exchange(left: Path, right: Path) -> None:
        temporary = left.parent / ".test-empty-swap"
        left.rename(temporary)
        right.rename(left)
        temporary.rename(right)

    monkeypatch.setattr(backup, "_context", lambda **_kwargs: (repository, nas))
    monkeypatch.setattr(backup, "_operation_lock", no_lock)
    monkeypatch.setattr(backup, "_password_memfd", fake_password)
    # Root ownership/directory fsync are exercised separately on Linux. Keep
    # this process double confined to temporary data on unprivileged CI.
    monkeypatch.setattr(
        support, "_private_directory", lambda path: path.mkdir(mode=0o700, exist_ok=True)
    )
    monkeypatch.setattr(support, "_sync_directory", lambda _path: None)

    report = backup.restore(
        repository=repository,
        repository_mount=tmp_path,
        deployment_root=deployment,
        appliance_env=None,
        selector="latest",
        confirmation=f"RESTORE ECHO NAS {snapshot_id} TO {nas}",
        password=b"a-secure-test-password",
        runner=runner,
        exchange=exchange,
        receipt_directory=tmp_path / "receipts",
    )

    assert report["snapshotId"] == snapshot_id
    assert report["atomicPromotion"] is True
    assert report["fullReadVerified"] is True
    assert report["contentVerified"] is True
    assert (nas / "family.mov").read_bytes() == b"video"
    assert (nas / "album" / "photo.jpg").read_bytes() == b"photo"
    assert len([command for command in commands if "check" in command]) == 2
    assert not list(deployment.glob(".storage.echo-nas-restore-*"))


def test_restore_wrong_confirmation_leaves_empty_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    deployment = tmp_path / "deployment"
    deployment.mkdir()
    nas = deployment / "storage"
    nas.mkdir()
    snapshot_id = "e" * 64

    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[-2:] == ["cat", "config"]:
            return completed(json.dumps({"id": "f" * 64}))
        if "snapshots" in command:
            return completed(
                json.dumps(
                    [
                        {
                            "id": snapshot_id,
                            "paths": [str(tmp_path / "source-snapshot")],
                            "tags": [backup.TAG],
                            "time": "2026-08-27T00:00:00Z",
                        }
                    ]
                )
            )
        if "check" in command:
            return completed()
        raise AssertionError("restore must not start with the wrong confirmation")

    monkeypatch.setattr(backup, "_context", lambda **_kwargs: (repository, nas))
    monkeypatch.setattr(backup, "_operation_lock", no_lock)
    monkeypatch.setattr(backup, "_password_memfd", fake_password)

    with pytest.raises(backup.NasDataBackupError, match="confirmation"):
        backup.restore(
            repository=repository,
            repository_mount=tmp_path,
            deployment_root=deployment,
            appliance_env=None,
            selector="latest",
            confirmation="RESTORE SOMETHING ELSE",
            password=b"a-secure-test-password",
            runner=runner,
            receipt_directory=tmp_path / "receipts",
        )
    assert list(nas.iterdir()) == []


def test_cli_list_forwards_limit_and_emits_versioned_result(tmp_path, monkeypatch, capsys):
    runtime = tmp_path / "restic"
    runtime.write_bytes(b"test runtime marker, never executed")
    monkeypatch.setattr(backup, "RESTIC", runtime)
    monkeypatch.setattr(backup.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(backup.os, "uname", lambda: SimpleNamespace(sysname="Linux"), raising=False)
    monkeypatch.setattr(backup, "_password_from_credential", lambda: b"not-logged-password")
    observed = {}

    def listing(**kwargs):
        observed.update(kwargs)
        return {
            "repositoryId": "d" * 64,
            "snapshotCount": 0,
            "snapshots": [],
            "truncated": False,
            "encrypted": True,
            "verification": "authenticated_index_only",
        }

    monkeypatch.setattr(backup, "list_snapshots", listing)
    assert (
        backup.main(
            [
                "list",
                "--limit",
                "7",
                "--repository",
                str(tmp_path),
                "--repository-mount",
                str(tmp_path),
                "--deployment-root",
                str(tmp_path),
                "--state-root",
                str(tmp_path / "state"),
                "--nas-root",
                str(tmp_path / "nas"),
            ]
        )
        == 0
    )

    output = json.loads(capsys.readouterr().out)
    assert observed["limit"] == 7
    assert observed["password"] == b"not-logged-password"
    assert observed["state_root_override"] == tmp_path / "state"
    assert observed["nas_root_override"] == tmp_path / "nas"
    assert output["kind"] == "echo.nas-data-backup.list"
    assert output["schemaVersion"] == backup.SCHEMA_VERSION
    assert output["snapshots"] == []


def test_cli_plan_set_needs_neither_restic_nor_backup_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest_path = tmp_path / "backup-set.json"
    manifest_path.write_text("{}", encoding="utf-8")
    observed: dict[str, object] = {}
    monkeypatch.setattr(backup.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(backup.os, "uname", lambda: SimpleNamespace(sysname="Linux"), raising=False)
    monkeypatch.setattr(
        backup,
        "load_backup_set_manifest",
        lambda path: {"loadedFrom": path},
    )

    def planning(**kwargs: object) -> dict[str, object]:
        observed.update(kwargs)
        return {
            "schema": backup.BACKUP_SET_PLAN_SCHEMA,
            "planId": "a" * 64,
            "setId": "99999999-aaaa-4bbb-8ccc-dddddddddddd",
            "memberCount": 1,
            "members": [],
            "pathsRedacted": True,
        }

    monkeypatch.setattr(backup, "plan_backup_set", planning)
    monkeypatch.setattr(
        backup,
        "_password_from_credential",
        lambda: pytest.fail("plan-set must not request a repository password"),
    )
    monkeypatch.setattr(backup, "RESTIC", tmp_path / "missing-restic")

    assert (
        backup.main(
            [
                "plan-set",
                "--manifest",
                str(manifest_path),
                "--repository",
                str(tmp_path / "repository"),
                "--repository-mount",
                str(tmp_path),
                "--deployment-root",
                str(tmp_path),
            ]
        )
        == 0
    )

    output = json.loads(capsys.readouterr().out)
    assert observed["manifest"] == {"loadedFrom": manifest_path}
    assert output["kind"] == "echo.nas-data-backup.plan-set"
    assert output["pathsRedacted"] is True
