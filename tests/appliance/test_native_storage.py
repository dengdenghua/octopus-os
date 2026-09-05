from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from appliance import native_storage
from appliance.native_storage_routes import create_omv_alias_router

MOUNT_POINT_REF = "11111111-2222-4333-8444-555555555555"


def _desired(mount_point_ref: str, **overrides: Any) -> dict[str, Any]:
    return {
        "schema": "echo.omv.shared-folder-desired.v1",
        "mountPointRef": mount_point_ref,
        "name": "Photos",
        "comment": "Family photos",
        **overrides,
    }


@pytest.fixture
def native_volume(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, Path, str]:
    volume = tmp_path / "volume"
    volume.mkdir()
    registry = tmp_path / "state" / "native-shared-folders.json"
    mount_point_ref = native_storage.volume_uuid(str(volume))
    monkeypatch.setattr(native_storage, "_NATIVE_SHARE_REGISTRY", registry)
    monkeypatch.setattr(native_storage, "_NATIVE_DATA_MOUNT_ROOTS", (str(tmp_path),))
    monkeypatch.setattr(
        native_storage,
        "filesystems",
        lambda: [
            {
                "devicefile": "/dev/test0",
                "mountpoint": str(volume),
                "readOnly": False,
            }
        ],
    )
    monkeypatch.setattr(native_storage, "_users_group_gid", lambda: 100)
    monkeypatch.setattr(native_storage, "_configure_shared_folder", lambda _path, _gid: None)
    monkeypatch.setattr(
        native_storage,
        "_verify_shared_folder",
        lambda path, _gid: path.is_dir(),
    )
    return volume, registry, mount_point_ref


def test_native_status_advertises_only_the_available_write_slice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(native_storage.shutil, "which", lambda _binary: "/usr/bin/tool")
    monkeypatch.setattr(native_storage, "_native_quota_tools_available", lambda: True)
    monkeypatch.setattr(native_storage, "_native_nut_usb_driver_available", lambda: True)

    payload = native_storage.status()

    assert payload["readOnly"] is False
    assert payload["capabilities"] == [
        "shared-folder.create.simple.v1",
        "shared-folder.rename.safe.v1",
        "shared-folder.detach.safe.v1",
        "shared-folder.delete.empty.v1",
        "account.group.create.v1",
        "account.user.create.v1",
        "account.user.password.reset.v1",
        "shared-folder.privilege.simple.v1",
        "smb.share.desired.v1",
        "nfs.share.private-network.v1",
        "nfs.share.remove.safe.v1",
        "filesystem.quota.user-group.v1",
        "storage.pool.zfs-mirror.create.v1",
        "storage.array.mdraid1.create.v1",
        "storage.pool.zfs-mirror.replace.blank.v1",
        "storage.pool.zfs.export.safe.v1",
        "storage.pool.zfs.import.echo-root.v1",
        "storage.pool.zfs.scrub.start.v1",
        "power.ups-shutdown-policy.v1",
        "power.ups.local-usb.configure.v1",
        "storage.smart.self-test.start.v1",
        "storage.smart.self-test.schedule.v1",
    ]


def test_native_status_hides_write_slices_without_host_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    available = {"groupadd", "useradd", "chpasswd", "smbpasswd"}
    monkeypatch.setattr(
        native_storage.shutil,
        "which",
        lambda binary: f"/usr/bin/{binary}" if binary in available else None,
    )

    payload = native_storage.status()

    assert payload["capabilities"] == [
        "shared-folder.create.simple.v1",
        "shared-folder.rename.safe.v1",
        "shared-folder.detach.safe.v1",
        "shared-folder.delete.empty.v1",
        "account.group.create.v1",
        "account.user.create.v1",
        "account.user.password.reset.v1",
    ]


def test_native_status_keeps_group_creation_when_only_groupadd_is_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        native_storage.shutil,
        "which",
        lambda binary: "/usr/bin/groupadd" if binary == "groupadd" else None,
    )

    assert native_storage.status()["capabilities"] == [
        "shared-folder.create.simple.v1",
        "shared-folder.rename.safe.v1",
        "shared-folder.detach.safe.v1",
        "shared-folder.delete.empty.v1",
        "account.group.create.v1",
    ]


def test_filesystems_reports_kernel_read_only_mount(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*args: str, **_kwargs: Any) -> str:
        if args[:1] == ("df",):
            return (
                "Filesystem Type 1024-blocks Used Available Capacity Mounted on\n"
                "/dev/test0 ext4 100000 10000 90000 10% /data\n"
            )
        if args[:1] == ("findmnt",):
            return "ro,relatime\n"
        return ""

    monkeypatch.setattr(native_storage, "_run", fake_run)
    monkeypatch.setattr(
        native_storage.shutil,
        "which",
        lambda binary: "/usr/bin/findmnt" if binary == "findmnt" else None,
    )

    entries = native_storage.filesystems()

    assert entries[0]["mountpoint"] == "/data"
    assert entries[0]["readOnly"] is True
    assert entries[0]["supportsQuota"] is False


def test_filesystems_does_not_advertise_system_root_as_quota_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*args: str, **_kwargs: Any) -> str:
        if args[:1] == ("df",):
            return (
                "Filesystem Type 1024-blocks Used Available Capacity Mounted on\n"
                "/dev/root ext4 100000 10000 90000 10% /\n"
                "/dev/data ext4 200000 20000 180000 10% /data\n"
            )
        if args[:1] == ("findmnt",):
            return "rw,relatime\n"
        return ""

    monkeypatch.setattr(native_storage, "_run", fake_run)
    monkeypatch.setattr(native_storage, "_NATIVE_DATA_MOUNT_ROOTS", ("/data",))
    monkeypatch.setattr(native_storage.shutil, "which", lambda _binary: "/usr/bin/tool")

    entries = native_storage.filesystems()

    assert [entry["mountpoint"] for entry in entries] == ["/", "/data"]
    assert entries[0]["supportsQuota"] is False
    assert entries[1]["supportsQuota"] is True


def _mock_native_health_reads(
    monkeypatch: pytest.MonkeyPatch, *, present: bool, passed: bool | None
) -> None:
    import io
    import subprocess

    def read_command(argv, **_kwargs):
        if argv[0] == "lsblk":
            value = {
                "blockdevices": [{"name": "sda", "type": "disk", "size": 2**40}] if present else []
            }
            return subprocess.CompletedProcess(argv, 0, json.dumps(value), "")
        if argv[0] == "df":
            value = "Filesystem Type 1B-blocks Used Available Capacity Mounted on\n"
            if present:
                value += "/dev/sda1 ext4 10000 2000 8000 20% /data\n"
            return subprocess.CompletedProcess(argv, 0, value, "")
        if argv[0] == "smartctl":
            value = {"smart_status": {"passed": passed}} if passed is not None else {}
            return subprocess.CompletedProcess(argv, 0, json.dumps(value), "")
        if argv[0] == "findmnt":
            return subprocess.CompletedProcess(argv, 0, "rw,relatime\n", "")
        raise FileNotFoundError()

    monkeypatch.setattr(native_storage.subprocess, "run", read_command)
    monkeypatch.setattr(
        native_storage.shutil,
        "which",
        lambda binary: "/usr/bin/findmnt" if binary == "findmnt" else None,
    )
    monkeypatch.setattr(
        native_storage,
        "open",
        lambda *_args, **_kwargs: io.StringIO("Personalities : [raid1]\nunused devices: <none>\n"),
        raising=False,
    )


def test_storage_health_does_not_report_healthy_when_all_probes_are_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_native_health_reads(monkeypatch, present=False, passed=None)
    health = native_storage.storage_health()
    assert health["state"] == "unknown"
    assert health["coverage"] == "none"
    assert health["stale"] is True
    assert health["lastSuccessfulAt"] is None
    assert health["summary"] == {"critical": 0, "warning": 0, "total": 0}
    assert health["probeEvidence"][0]["code"] == "empty_inventory"


def test_storage_health_marks_unknown_smart_as_degraded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_native_health_reads(monkeypatch, present=True, passed=None)
    health = native_storage.storage_health()
    assert health["state"] == "degraded"
    assert health["coverage"] == "partial"
    assert health["stale"] is True
    assert health["lastSuccessfulAt"] is None
    assert health["summary"] == {"critical": 0, "warning": 0, "total": 0}
    assert health["probeEvidence"][-1]["code"] == "health_unreported"


def test_storage_health_stays_healthy_only_with_a_positive_smart_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_native_health_reads(monkeypatch, present=True, passed=True)
    health = native_storage.storage_health()
    assert health["state"] == "healthy"
    assert health["coverage"] == "complete"
    assert health["stale"] is False
    assert health["summary"] == {"critical": 0, "warning": 0, "total": 0}


@pytest.mark.skipif(sys.platform == "win32", reason="path assertions are POSIX-specific")
def test_sharing_targets_match_the_frontend_contract_without_host_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        native_storage,
        "filesystems",
        lambda: [
            {
                "devicefile": "/dev/test0",
                "uuid": None,
                "label": "Family",
                "type": "ext4",
                "mountpoint": "/srv/family",
                "sizeBytes": 10_000,
                "availableBytes": 8_000,
                "readOnly": False,
                "supportsAcl": False,
            }
        ],
    )
    monkeypatch.setattr(native_storage, "_etc_entries", lambda _path: [])
    monkeypatch.setattr(native_storage, "_NATIVE_DATA_MOUNT_ROOTS", ("/srv",))
    monkeypatch.setattr(native_storage, "_samba_usershares", lambda: [])
    monkeypatch.setattr(native_storage, "_registry_load", lambda **_kwargs: [])

    target = native_storage.sharing_overview()["sharedFolderTargets"][0]

    assert target == {
        "mountPointRef": native_storage.volume_uuid("/srv/family"),
        "filesystemUuid": native_storage._filesystem_uuid("/dev/test0", "/srv/family"),
        "label": "Family",
        "type": "ext4",
        "sizeBytes": 10_000,
        "availableBytes": 8_000,
        "readOnly": False,
    }
    assert "path" not in target


@pytest.mark.skipif(sys.platform == "win32", reason="path assertions are POSIX-specific")
def test_system_mounts_are_never_offered_as_shared_folder_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entries = [
        {
            "devicefile": "/dev/system",
            "uuid": None,
            "label": "root",
            "type": "ext4",
            "mountpoint": "/",
            "sizeBytes": 10_000,
            "availableBytes": 8_000,
            "readOnly": False,
            "supportsAcl": True,
        },
        {
            "devicefile": "/dev/data",
            "uuid": None,
            "label": "family",
            "type": "zfs",
            "mountpoint": "/data/family",
            "sizeBytes": 20_000,
            "availableBytes": 18_000,
            "readOnly": False,
            "supportsAcl": True,
        },
    ]
    monkeypatch.setattr(native_storage, "filesystems", lambda: entries)
    monkeypatch.setattr(native_storage, "_etc_entries", lambda _path: [])
    monkeypatch.setattr(native_storage, "_samba_usershares", lambda: [])
    monkeypatch.setattr(native_storage, "_registry_load", lambda **_kwargs: [])

    overview = native_storage.sharing_overview()

    assert [target["label"] for target in overview["sharedFolderTargets"]] == ["family"]
    assert native_storage.volume_uuid("/") not in native_storage._writable_targets()
    assert [folder["relativePath"] for folder in overview["sharedFolders"]] == ["/", "/"]
    assert all("/data/family" not in json.dumps(folder) for folder in overview["sharedFolders"])


def test_sharing_overview_skips_corrupt_registered_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    valid_uuid = "11111111-2222-4333-8444-555555555555"
    monkeypatch.setattr(native_storage, "filesystems", lambda: [])
    monkeypatch.setattr(native_storage, "_etc_entries", lambda _path: [])
    monkeypatch.setattr(native_storage, "_samba_usershares", lambda: [])
    monkeypatch.setattr(native_storage, "_native_nfs_shares", lambda: [])
    monkeypatch.setattr(
        native_storage,
        "_registry_load",
        lambda **_kwargs: [
            {
                "uuid": valid_uuid,
                "name": "Private",
                "relativePath": "/srv/private",
                "device": "/dev/test0",
            },
            {
                "uuid": valid_uuid,
                "name": "Family",
                "relativePath": "Family",
                "device": "/dev/test0",
            },
        ],
    )

    overview = native_storage.sharing_overview()

    assert [folder["name"] for folder in overview["sharedFolders"]] == ["Family"]
    assert "/srv/private" not in json.dumps(overview, ensure_ascii=False)


def test_sharing_overview_keeps_the_users_group_and_empty_smb_service_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entries = {
        "/etc/passwd": [
            ["root", "x", "0", "0", "root", "/root"],
            ["mother", "x", "1001", "100", "Mom", "/home/mother"],
        ],
        "/etc/group": [
            ["root", "x", "0", ""],
            ["users", "x", "100", "mother"],
            ["media", "x", "1001", "mother"],
            ["daemon", "x", "999", ""],
        ],
    }
    monkeypatch.setattr(native_storage, "_etc_entries", lambda path: entries[path])
    monkeypatch.setattr(native_storage, "filesystems", lambda: [])
    monkeypatch.setattr(native_storage, "_registry_load", lambda **_kwargs: [])
    monkeypatch.setattr(native_storage, "_samba_usershares", lambda: [])
    monkeypatch.setattr(
        native_storage.shutil,
        "which",
        lambda binary: f"/usr/bin/{binary}" if binary in {"net", "smbd"} else None,
    )

    overview = native_storage.sharing_overview()

    assert overview["smb"] == {"enabled": True, "shares": []}
    assert [group["name"] for group in overview["groups"]] == ["users", "media"]
    assert overview["users"][0]["groups"] == ["users", "media"]


def test_samba_usershare_inventory_parses_samba_key_value_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        native_storage,
        "_run",
        lambda *_args, **_kwargs: (
            "[media]\n"
            "path=/srv/media\n"
            "comment=Family media\n"
            "usershare_acl=BUILTIN\\\\Users:R,\n"
            "guest_ok=n\n"
        ),
    )

    shares = native_storage._samba_usershares()

    assert shares == [
        {
            "uuid": "media",
            "sharedFolderRef": "media",
            "sharedFolderName": "media",
            "enabled": True,
            "readOnly": True,
            "guest": "no",
            "browseable": True,
            "recycleBin": False,
            "comment": "Family media",
        }
    ]


def test_principal_id_rejects_system_accounts_but_allows_nas_users_and_users_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_ids = {"root": 0, "mother": 1001}
    group_ids = {"root": 0, "users": 100, "media": 1001}

    def getpwnam(name: str) -> SimpleNamespace:
        return SimpleNamespace(pw_uid=user_ids[name])

    def getgrnam(name: str) -> SimpleNamespace:
        return SimpleNamespace(gr_gid=group_ids[name])

    monkeypatch.setattr(
        native_storage,
        "_require_posix_accounts",
        lambda: (SimpleNamespace(getgrnam=getgrnam), SimpleNamespace(getpwnam=getpwnam)),
    )

    assert native_storage._principal_id("user", "mother") == 1001
    assert native_storage._principal_id("group", "users") == 100
    with pytest.raises(ValueError, match="not an exposed NAS identity"):
        native_storage._principal_id("user", "root")
    with pytest.raises(ValueError, match="not an exposed NAS identity"):
        native_storage._principal_id("group", "root")


@pytest.mark.parametrize(
    "acl_text",
    ["user:mother:rwx\n", "default:user:mother:rwx\n"],
)
def test_partial_share_acl_fails_closed_until_access_and_default_match(acl_text: str) -> None:
    with pytest.raises(OSError, match="access and default ACL entries are incomplete"):
        native_storage._acl_permission(acl_text, "user", "mother")


def test_shared_folder_create_is_atomic_and_idempotent(
    native_volume: tuple[Path, Path, str],
) -> None:
    volume, registry, mount_point_ref = native_volume
    desired = _desired(mount_point_ref)
    plan = native_storage.plan_shared_folder(desired)

    assert plan["operation"] == "create"
    assert plan["requiresApproval"] is True
    assert plan["target"]["mountPointRef"] == mount_point_ref
    assert plan["target"]["label"] == "volume"
    assert "mountPoint" not in plan["target"]
    assert str(volume) not in json.dumps(plan, ensure_ascii=False)
    applied = native_storage.apply_shared_folder(desired, plan["planId"])

    assert applied["applied"] is True
    assert applied["verified"] is True
    assert (volume / "Photos").is_dir()
    persisted = json.loads(registry.read_text(encoding="utf-8"))
    assert persisted[0]["volumePath"] == str(volume)
    assert "volumePath" not in applied["sharedFolder"]
    assert str(volume) not in json.dumps(applied, ensure_ascii=False)
    assert applied["sharedFolder"]["relativePath"] == "Photos"
    assert plan["shareUuid"] == applied["sharedFolder"]["uuid"]

    repeated_plan = native_storage.plan_shared_folder(desired)
    assert repeated_plan["operation"] == "none"
    assert repeated_plan["planId"] != plan["planId"]
    repeated = native_storage.apply_shared_folder(desired, repeated_plan["planId"])
    assert repeated["applied"] is False
    assert repeated["verified"] is True


def test_shared_folder_comment_update_preserves_directory_and_registry_identity(
    native_volume: tuple[Path, Path, str],
) -> None:
    volume, registry, mount_point_ref = native_volume
    initial = _desired(mount_point_ref)
    created = native_storage.apply_shared_folder(
        initial, native_storage.plan_shared_folder(initial)["planId"]
    )
    updated = {**initial, "comment": "Updated family photos"}

    plan = native_storage.plan_shared_folder(updated)

    assert plan["operation"] == "update"
    assert plan["requiresApproval"] is True
    assert plan["shareUuid"] == created["sharedFolder"]["uuid"]
    assert plan["changes"] == [
        {"field": "comment", "before": "Family photos", "after": "Updated family photos"}
    ]
    assert plan["safety"]["update"] == "commentOnly"

    applied = native_storage.apply_shared_folder(updated, plan["planId"])

    assert applied["applied"] is True
    assert applied["verified"] is True
    assert applied["sharedFolder"]["uuid"] == created["sharedFolder"]["uuid"]
    assert (volume / "Photos").is_dir()
    persisted = json.loads(registry.read_text(encoding="utf-8"))
    assert persisted[0]["comment"] == "Updated family photos"
    assert str(volume) not in json.dumps(applied, ensure_ascii=False)


def test_shared_folder_rename_preserves_data_acl_identity_and_comment_updates(
    native_volume: tuple[Path, Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    volume, registry, mount_point_ref = native_volume
    monkeypatch.setattr(native_storage, "_NATIVE_NFS_EXPORTS", registry.parent / "exports")
    monkeypatch.setattr(native_storage, "_smb_usershare_info", lambda _name: None)
    initial = _desired(mount_point_ref)
    created = native_storage.apply_shared_folder(
        initial, native_storage.plan_shared_folder(initial)["planId"]
    )
    marker = volume / "Photos" / "nested" / "keep.txt"
    marker.parent.mkdir()
    marker.write_text("preserved", encoding="utf-8")
    desired = {
        "schema": "echo.omv.shared-folder-rename-desired.v1",
        "sharedFolderRef": created["sharedFolder"]["uuid"],
        "name": "Family",
    }

    plan = native_storage.plan_shared_folder_rename(desired)

    assert plan["operation"] == "rename"
    assert plan["requiresApproval"] is True
    assert plan["shareUuid"] == created["sharedFolder"]["uuid"]
    assert plan["changes"] == [{"field": "name", "before": "Photos", "after": "Family"}]
    assert plan["safety"] == {
        "filesystem": "sameMountedWritableVolume",
        "data": "preserved",
        "identity": "uuidPreserved",
        "acl": "preservedWithDirectory",
        "dependentShares": "mustBeAbsent",
        "rollback": "directoryAndRegistry",
    }
    assert str(volume) not in json.dumps(plan, ensure_ascii=False)

    applied = native_storage.apply_shared_folder_rename(desired, plan["planId"])

    assert applied["applied"] is True
    assert applied["verified"] is True
    assert applied["dataPreserved"] is True
    assert applied["sharedFolder"]["uuid"] == created["sharedFolder"]["uuid"]
    assert applied["sharedFolder"]["name"] == "Family"
    assert not (volume / "Photos").exists()
    assert (volume / "Family" / "nested" / "keep.txt").read_text(encoding="utf-8") == "preserved"
    persisted = json.loads(registry.read_text(encoding="utf-8"))[0]
    assert persisted["uuid"] == created["sharedFolder"]["uuid"]
    assert persisted["name"] == persisted["relativePath"] == "Family"

    # A rename keeps the UUID stable, and subsequent comment-only updates
    # must continue to address the same registry row.
    comment_update = _desired(
        mount_point_ref,
        name="Family",
        comment="Renamed family archive",
    )
    comment_plan = native_storage.plan_shared_folder(comment_update)
    assert comment_plan["operation"] == "update"
    assert comment_plan["shareUuid"] == created["sharedFolder"]["uuid"]
    comment_result = native_storage.apply_shared_folder(comment_update, comment_plan["planId"])
    assert comment_result["sharedFolder"]["uuid"] == created["sharedFolder"]["uuid"]


@pytest.mark.parametrize("dependency", ["smb", "nfs"])
def test_shared_folder_rename_requires_dependent_shares_to_be_removed(
    native_volume: tuple[Path, Path, str],
    monkeypatch: pytest.MonkeyPatch,
    dependency: str,
) -> None:
    volume, registry, mount_point_ref = native_volume
    monkeypatch.setattr(native_storage, "_NATIVE_NFS_EXPORTS", registry.parent / "exports")
    monkeypatch.setattr(native_storage, "_smb_usershare_info", lambda _name: None)
    created = native_storage.apply_shared_folder(
        _desired(mount_point_ref),
        native_storage.plan_shared_folder(_desired(mount_point_ref))["planId"],
    )
    if dependency == "smb":
        monkeypatch.setattr(native_storage, "_smb_usershare_info", lambda _name: {"path": "x"})
        match = "disable the SMB share"
    else:
        monkeypatch.setattr(
            native_storage,
            "_nfs_exports_load",
            lambda **_kwargs: [
                {
                    "uuid": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                    "sharedFolderRef": created["sharedFolder"]["uuid"],
                    "client": "192.168.1.0/24",
                    "readOnly": False,
                    "comment": "test",
                }
            ],
        )
        match = "remove the NFS rule"
    desired = {
        "schema": "echo.omv.shared-folder-rename-desired.v1",
        "sharedFolderRef": created["sharedFolder"]["uuid"],
        "name": "Family",
    }

    with pytest.raises(ValueError, match=match):
        native_storage.plan_shared_folder_rename(desired)

    assert (volume / "Photos").is_dir()
    assert not (volume / "Family").exists()


def test_shared_folder_rename_rejects_collision_and_rolls_back_registry_failure(
    native_volume: tuple[Path, Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    volume, registry, mount_point_ref = native_volume
    monkeypatch.setattr(native_storage, "_NATIVE_NFS_EXPORTS", registry.parent / "exports")
    monkeypatch.setattr(native_storage, "_smb_usershare_info", lambda _name: None)
    created = native_storage.apply_shared_folder(
        _desired(mount_point_ref),
        native_storage.plan_shared_folder(_desired(mount_point_ref))["planId"],
    )
    desired = {
        "schema": "echo.omv.shared-folder-rename-desired.v1",
        "sharedFolderRef": created["sharedFolder"]["uuid"],
        "name": "Family",
    }
    (volume / "Family").mkdir()
    with pytest.raises(ValueError, match="target already exists"):
        native_storage.plan_shared_folder_rename(desired)
    (volume / "Family").rmdir()

    plan = native_storage.plan_shared_folder_rename(desired)
    original_save = native_storage._registry_save
    calls = 0

    def fail_once(entries: list[dict[str, Any]]) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("simulated registry failure")
        original_save(entries)

    monkeypatch.setattr(native_storage, "_registry_save", fail_once)
    with pytest.raises(OSError, match="simulated registry failure"):
        native_storage.apply_shared_folder_rename(desired, plan["planId"])

    assert calls == 2
    assert (volume / "Photos").is_dir()
    assert not (volume / "Family").exists()
    persisted = json.loads(registry.read_text(encoding="utf-8"))[0]
    assert persisted["name"] == persisted["relativePath"] == "Photos"


def test_zfs_pool_export_dependency_inventory_is_path_scoped_and_sanitized(
    native_volume: tuple[Path, Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    volume, _registry, mount_point_ref = native_volume
    monkeypatch.setattr(native_storage, "_NATIVE_ZFS_MOUNT_ROOT", volume.parent)
    created = native_storage.apply_shared_folder(
        _desired(mount_point_ref),
        native_storage.plan_shared_folder(_desired(mount_point_ref))["planId"],
    )

    dependencies = native_storage._zfs_pool_managed_dependencies(volume.name)

    assert dependencies == [{"uuid": created["sharedFolder"]["uuid"], "name": "Photos"}]
    assert str(volume) not in json.dumps(dependencies)
    assert native_storage._zfs_pool_managed_dependencies("other") == []


def test_exportable_zfs_pool_inventory_hides_pool_with_managed_share(
    native_volume: tuple[Path, Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    volume, _registry, mount_point_ref = native_volume
    monkeypatch.setattr(native_storage, "_NATIVE_ZFS_MOUNT_ROOT", volume.parent)
    native_storage.apply_shared_folder(
        _desired(mount_point_ref),
        native_storage.plan_shared_folder(_desired(mount_point_ref))["planId"],
    )
    pools = [
        {"name": volume.name, "poolGuid": "123", "safeToExport": True},
        {"name": "other", "poolGuid": "456", "safeToExport": True},
    ]
    monkeypatch.setattr(native_storage, "_exportable_zfs_pools", lambda: pools)

    assert native_storage.exportable_zfs_pools() == [pools[1]]


def test_shared_folder_detach_preserves_directory_and_removes_only_registry_entry(
    native_volume: tuple[Path, Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    volume, registry, mount_point_ref = native_volume
    monkeypatch.setattr(native_storage, "_NATIVE_NFS_EXPORTS", registry.parent / "exports")
    monkeypatch.setattr(native_storage, "_smb_usershare_info", lambda _name: None)
    desired = _desired(mount_point_ref)
    created = native_storage.apply_shared_folder(
        desired, native_storage.plan_shared_folder(desired)["planId"]
    )
    detach = {
        "schema": "echo.omv.shared-folder-detach-desired.v1",
        "sharedFolderRef": created["sharedFolder"]["uuid"],
        "preserveData": True,
    }

    plan = native_storage.plan_shared_folder_detach(detach)

    assert plan["operation"] == "remove"
    assert plan["requiresApproval"] is True
    assert plan["safety"] == {
        "data": "preserved",
        "directory": "neverDeleted",
        "dependentShares": "mustBeAbsent",
        "acl": "untouched",
        "rollback": "registryOnly",
        "mount": "notRequiredForDetach",
    }
    applied = native_storage.apply_shared_folder_detach(detach, plan["planId"])

    assert applied["applied"] is True
    assert applied["verified"] is True
    assert applied["dataPreserved"] is True
    assert applied["sharedFolder"]["uuid"] == created["sharedFolder"]["uuid"]
    assert (volume / "Photos").is_dir()
    assert json.loads(registry.read_text(encoding="utf-8")) == []
    with pytest.raises(ValueError, match="does not match any native shared folder"):
        native_storage.plan_shared_folder_detach(detach)


def test_shared_folder_detach_can_unregister_after_volume_is_unmounted(
    native_volume: tuple[Path, Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    volume, registry, mount_point_ref = native_volume
    folder_uuid = "11111111-2222-4333-8444-555555555555"
    folder = _register_folder(volume, registry, mount_point_ref, folder_uuid)
    marker = folder / "keep.txt"
    marker.write_text("preserve detach data", encoding="utf-8")
    monkeypatch.setattr(native_storage, "_NATIVE_NFS_EXPORTS", registry.parent / "exports")
    monkeypatch.setattr(native_storage, "_smb_usershare_info", lambda _name: None)
    monkeypatch.setattr(native_storage, "filesystems", lambda: [])
    detach = {
        "schema": "echo.omv.shared-folder-detach-desired.v1",
        "sharedFolderRef": folder_uuid,
        "preserveData": True,
    }

    plan = native_storage.plan_shared_folder_detach(detach)
    assert plan["sharedFolder"]["status"] == "UNAVAILABLE"
    applied = native_storage.apply_shared_folder_detach(detach, plan["planId"])

    assert applied["applied"] is True
    assert applied["verified"] is True
    assert applied["dataPreserved"] is True
    assert marker.read_text(encoding="utf-8") == "preserve detach data"
    assert json.loads(registry.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("dependency", ["smb", "nfs"])
def test_shared_folder_detach_requires_dependent_shares_to_be_removed(
    native_volume: tuple[Path, Path, str],
    monkeypatch: pytest.MonkeyPatch,
    dependency: str,
) -> None:
    _volume, registry, mount_point_ref = native_volume
    monkeypatch.setattr(native_storage, "_NATIVE_NFS_EXPORTS", registry.parent / "exports")
    monkeypatch.setattr(native_storage, "_smb_usershare_info", lambda _name: None)
    desired = _desired(mount_point_ref)
    created = native_storage.apply_shared_folder(
        desired, native_storage.plan_shared_folder(desired)["planId"]
    )
    if dependency == "smb":
        monkeypatch.setattr(native_storage, "_smb_usershare_info", lambda _name: {"path": "x"})
        match = "disable the SMB share"
    else:
        monkeypatch.setattr(
            native_storage,
            "_nfs_exports_load",
            lambda **_kwargs: [
                {
                    "uuid": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                    "sharedFolderRef": created["sharedFolder"]["uuid"],
                    "client": "192.168.1.0/24",
                    "readOnly": False,
                    "comment": "test",
                }
            ],
        )
        match = "remove the NFS rule"

    detach = {
        "schema": "echo.omv.shared-folder-detach-desired.v1",
        "sharedFolderRef": created["sharedFolder"]["uuid"],
        "preserveData": True,
    }
    with pytest.raises(ValueError, match=match):
        native_storage.plan_shared_folder_detach(detach)
    assert (native_volume[0] / "Photos").is_dir()


def test_shared_folder_detach_rolls_back_registry_on_write_failure(
    native_volume: tuple[Path, Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    volume, registry, mount_point_ref = native_volume
    monkeypatch.setattr(native_storage, "_NATIVE_NFS_EXPORTS", registry.parent / "exports")
    monkeypatch.setattr(native_storage, "_smb_usershare_info", lambda _name: None)
    desired = _desired(mount_point_ref)
    created = native_storage.apply_shared_folder(
        desired, native_storage.plan_shared_folder(desired)["planId"]
    )
    detach = {
        "schema": "echo.omv.shared-folder-detach-desired.v1",
        "sharedFolderRef": created["sharedFolder"]["uuid"],
        "preserveData": True,
    }
    plan = native_storage.plan_shared_folder_detach(detach)
    original_save = native_storage._registry_save
    calls = 0

    def fail_once(entries: list[dict[str, Any]]) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("simulated registry failure")
        original_save(entries)

    monkeypatch.setattr(native_storage, "_registry_save", fail_once)
    with pytest.raises(OSError, match="simulated registry failure"):
        native_storage.apply_shared_folder_detach(detach, plan["planId"])

    assert calls == 2
    assert (
        json.loads(registry.read_text(encoding="utf-8"))[0]["uuid"]
        == created["sharedFolder"]["uuid"]
    )
    assert (volume / "Photos").is_dir()


def test_shared_folder_delete_removes_only_an_empty_registered_directory(
    native_volume: tuple[Path, Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    volume, registry, mount_point_ref = native_volume
    monkeypatch.setattr(native_storage, "_NATIVE_NFS_EXPORTS", registry.parent / "exports")
    monkeypatch.setattr(native_storage, "_smb_usershare_info", lambda _name: None)
    desired = _desired(mount_point_ref)
    created = native_storage.apply_shared_folder(
        desired, native_storage.plan_shared_folder(desired)["planId"]
    )
    delete = {
        "schema": "echo.omv.shared-folder-delete-desired.v1",
        "sharedFolderRef": created["sharedFolder"]["uuid"],
        "emptyOnly": True,
    }

    plan = native_storage.plan_shared_folder_delete(delete)

    assert plan["operation"] == "remove"
    assert plan["requiresApproval"] is True
    assert plan["safety"] == {
        "data": "emptyDirectoryOnly",
        "directory": "deleted",
        "dependentShares": "mustBeAbsent",
        "recursive": "never",
        "mount": "mountedWritableOnly",
        "rollback": "registryAndEmptyDirectory",
    }
    assert str(volume) not in json.dumps(plan, ensure_ascii=False)

    applied = native_storage.apply_shared_folder_delete(delete, plan["planId"])

    assert applied["applied"] is True
    assert applied["verified"] is True
    assert applied["directoryDeleted"] is True
    assert applied["dataDeleted"] is False
    assert not (volume / "Photos").exists()
    assert json.loads(registry.read_text(encoding="utf-8")) == []


def test_shared_folder_delete_rejects_nonempty_directory(
    native_volume: tuple[Path, Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    volume, registry, mount_point_ref = native_volume
    monkeypatch.setattr(native_storage, "_NATIVE_NFS_EXPORTS", registry.parent / "exports")
    monkeypatch.setattr(native_storage, "_smb_usershare_info", lambda _name: None)
    desired = _desired(mount_point_ref)
    created = native_storage.apply_shared_folder(
        desired, native_storage.plan_shared_folder(desired)["planId"]
    )
    (volume / "Photos" / "keep.txt").write_text("keep", encoding="utf-8")
    delete = {
        "schema": "echo.omv.shared-folder-delete-desired.v1",
        "sharedFolderRef": created["sharedFolder"]["uuid"],
        "emptyOnly": True,
    }

    with pytest.raises(ValueError, match="not empty"):
        native_storage.plan_shared_folder_delete(delete)

    assert (volume / "Photos" / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert len(json.loads(registry.read_text(encoding="utf-8"))) == 1


@pytest.mark.parametrize("dependency", ["smb", "nfs"])
def test_shared_folder_delete_requires_dependent_shares_to_be_removed(
    native_volume: tuple[Path, Path, str],
    monkeypatch: pytest.MonkeyPatch,
    dependency: str,
) -> None:
    _volume, registry, mount_point_ref = native_volume
    monkeypatch.setattr(native_storage, "_NATIVE_NFS_EXPORTS", registry.parent / "exports")
    monkeypatch.setattr(native_storage, "_smb_usershare_info", lambda _name: None)
    desired = _desired(mount_point_ref)
    created = native_storage.apply_shared_folder(
        desired, native_storage.plan_shared_folder(desired)["planId"]
    )
    if dependency == "smb":
        monkeypatch.setattr(native_storage, "_smb_usershare_info", lambda _name: {"path": "x"})
        match = "disable the SMB share"
    else:
        monkeypatch.setattr(
            native_storage,
            "_nfs_exports_load",
            lambda **_kwargs: [
                {
                    "uuid": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                    "sharedFolderRef": created["sharedFolder"]["uuid"],
                    "client": "192.168.1.0/24",
                    "readOnly": False,
                    "comment": "test",
                }
            ],
        )
        match = "remove the NFS rule"

    delete = {
        "schema": "echo.omv.shared-folder-delete-desired.v1",
        "sharedFolderRef": created["sharedFolder"]["uuid"],
        "emptyOnly": True,
    }
    with pytest.raises(ValueError, match=match):
        native_storage.plan_shared_folder_delete(delete)
    assert (_volume / "Photos").is_dir()


def test_shared_folder_delete_rolls_back_registry_when_directory_removal_fails(
    native_volume: tuple[Path, Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    volume, registry, mount_point_ref = native_volume
    monkeypatch.setattr(native_storage, "_NATIVE_NFS_EXPORTS", registry.parent / "exports")
    monkeypatch.setattr(native_storage, "_smb_usershare_info", lambda _name: None)
    desired = _desired(mount_point_ref)
    created = native_storage.apply_shared_folder(
        desired, native_storage.plan_shared_folder(desired)["planId"]
    )
    delete = {
        "schema": "echo.omv.shared-folder-delete-desired.v1",
        "sharedFolderRef": created["sharedFolder"]["uuid"],
        "emptyOnly": True,
    }
    plan = native_storage.plan_shared_folder_delete(delete)

    def fail_rmdir(_path: Path) -> None:
        raise OSError("simulated directory removal failure")

    monkeypatch.setattr(Path, "rmdir", fail_rmdir)
    with pytest.raises(OSError, match="simulated directory removal failure"):
        native_storage.apply_shared_folder_delete(delete, plan["planId"])

    assert (volume / "Photos").is_dir()
    assert (
        json.loads(registry.read_text(encoding="utf-8"))[0]["uuid"]
        == created["sharedFolder"]["uuid"]
    )


def test_apply_rejects_a_plan_after_the_target_state_changes(
    native_volume: tuple[Path, Path, str],
) -> None:
    volume, _registry, mount_point_ref = native_volume
    desired = _desired(mount_point_ref)
    plan = native_storage.plan_shared_folder(desired)
    (volume / "Photos").mkdir()

    with pytest.raises(ValueError, match="outside the native registry"):
        native_storage.apply_shared_folder(desired, plan["planId"])


@pytest.mark.parametrize("failure_point", ["permissions", "registry"])
def test_failed_create_removes_its_empty_directory(
    native_volume: tuple[Path, Path, str],
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    volume, registry, mount_point_ref = native_volume
    desired = _desired(mount_point_ref)
    plan = native_storage.plan_shared_folder(desired)

    if failure_point == "permissions":
        monkeypatch.setattr(
            native_storage,
            "_configure_shared_folder",
            lambda _path, _gid: (_ for _ in ()).throw(OSError("permission failure")),
        )
    else:
        monkeypatch.setattr(
            native_storage,
            "_registry_save",
            lambda _entries: (_ for _ in ()).throw(OSError("registry failure")),
        )

    with pytest.raises(OSError):
        native_storage.apply_shared_folder(desired, plan["planId"])

    assert not (volume / "Photos").exists()
    assert not registry.exists()


def test_corrupt_registry_fails_closed(
    native_volume: tuple[Path, Path, str],
) -> None:
    _volume, registry, mount_point_ref = native_volume
    registry.parent.mkdir(parents=True)
    registry.write_text("not-json", encoding="utf-8")

    with pytest.raises(OSError, match="registry is unreadable"):
        native_storage.plan_shared_folder(_desired(mount_point_ref))


def test_concurrent_apply_allows_exactly_one_commit(
    native_volume: tuple[Path, Path, str],
) -> None:
    _volume, registry, mount_point_ref = native_volume
    desired = _desired(mount_point_ref)
    plan = native_storage.plan_shared_folder(desired)

    def apply() -> str:
        try:
            native_storage.apply_shared_folder(desired, plan["planId"])
        except ValueError:
            return "stale"
        return "created"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(pool.map(lambda _index: apply(), range(2)))

    assert outcomes == ["created", "stale"]
    assert len(json.loads(registry.read_text(encoding="utf-8"))) == 1


def test_native_alias_exposes_create_capability_and_stale_plan_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ECHO_APPLIANCE", raising=False)
    current_plan = {
        "planId": "a" * 64,
        "operation": "create",
        "requiresApproval": True,
    }
    monkeypatch.setattr(native_storage, "plan_shared_folder", lambda _desired: current_plan)
    app = FastAPI()
    app.include_router(create_omv_alias_router())
    client = TestClient(app)

    status = client.get("/api/appliance/omv/status")
    response = client.post(
        "/api/appliance/omv/sharing/folders/apply",
        json={"desired": _desired(MOUNT_POINT_REF), "planId": "b" * 64},
    )

    assert status.status_code == 200
    assert "shared-folder.create.simple.v1" in status.json()["capabilities"]
    assert response.status_code == 409


def test_native_alias_binds_comment_update_to_update_approval_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ECHO_APPLIANCE", raising=False)
    plan_id = "a" * 64
    current_plan = {"planId": plan_id, "operation": "update", "requiresApproval": True}
    applied = {**current_plan, "applied": True, "verified": True}
    approval_calls: list[dict[str, Any]] = []
    audit_calls: list[dict[str, Any]] = []

    class Approval:
        def consume(self, **kwargs: Any) -> None:
            approval_calls.append(kwargs)

    class Audit:
        def record(self, **kwargs: Any) -> None:
            audit_calls.append(kwargs)

    monkeypatch.setattr(native_storage, "plan_shared_folder", lambda _desired: current_plan)
    monkeypatch.setattr(
        native_storage,
        "apply_shared_folder",
        lambda _desired, _plan_id: applied,
    )
    app = FastAPI()
    app.include_router(create_omv_alias_router(approval=Approval(), audit=Audit()))
    client = TestClient(app)

    response = client.post(
        "/api/appliance/omv/sharing/folders/apply",
        json={"desired": _desired(MOUNT_POINT_REF), "planId": plan_id},
        headers={"X-Echo-Approval": "approval-token"},
    )

    assert response.status_code == 200
    assert approval_calls[0]["action"] == "omv.shared-folder.update"
    assert {entry["action"] for entry in audit_calls} == {"omv.shared-folder.update"}


def test_native_alias_binds_folder_rename_to_update_approval_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ECHO_APPLIANCE", raising=False)
    plan_id = "e" * 64
    current_plan = {"planId": plan_id, "operation": "rename", "requiresApproval": True}
    applied = {
        **current_plan,
        "applied": True,
        "verified": True,
        "dataPreserved": True,
    }
    approval_calls: list[dict[str, Any]] = []
    audit_calls: list[dict[str, Any]] = []

    class Approval:
        def consume(self, **kwargs: Any) -> None:
            approval_calls.append(kwargs)

    class Audit:
        def record(self, **kwargs: Any) -> None:
            audit_calls.append(kwargs)

    monkeypatch.setattr(native_storage, "plan_shared_folder_rename", lambda _desired: current_plan)
    monkeypatch.setattr(
        native_storage,
        "apply_shared_folder_rename",
        lambda _desired, _plan_id: applied,
    )
    app = FastAPI()
    app.include_router(create_omv_alias_router(approval=Approval(), audit=Audit()))
    client = TestClient(app)

    response = client.post(
        "/api/appliance/omv/sharing/folders/rename/apply",
        json={
            "desired": {
                "schema": "echo.omv.shared-folder-rename-desired.v1",
                "sharedFolderRef": MOUNT_POINT_REF,
                "name": "Family",
            },
            "planId": plan_id,
        },
        headers={"X-Echo-Approval": "approval-token"},
    )

    assert response.status_code == 200
    assert approval_calls[0]["action"] == "omv.shared-folder.update"
    assert {entry["action"] for entry in audit_calls} == {"omv.shared-folder.update"}
    assert audit_calls[0]["metadata"]["dataPreserved"] is True


def test_native_alias_binds_folder_detach_to_data_preserving_approval_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ECHO_APPLIANCE", raising=False)
    plan_id = "a" * 64
    current_plan = {
        "planId": plan_id,
        "operation": "remove",
        "requiresApproval": True,
    }
    applied = {**current_plan, "applied": True, "verified": True, "dataPreserved": True}
    approval_calls: list[dict[str, Any]] = []
    audit_calls: list[dict[str, Any]] = []

    class Approval:
        def consume(self, **kwargs: Any) -> None:
            approval_calls.append(kwargs)

    class Audit:
        def record(self, **kwargs: Any) -> None:
            audit_calls.append(kwargs)

    monkeypatch.setattr(native_storage, "plan_shared_folder_detach", lambda _desired: current_plan)
    monkeypatch.setattr(
        native_storage,
        "apply_shared_folder_detach",
        lambda _desired, _plan_id: applied,
    )
    app = FastAPI()
    app.include_router(create_omv_alias_router(approval=Approval(), audit=Audit()))
    client = TestClient(app)

    response = client.post(
        "/api/appliance/omv/sharing/folders/detach/apply",
        json={
            "desired": {
                "schema": "echo.omv.shared-folder-detach-desired.v1",
                "sharedFolderRef": MOUNT_POINT_REF,
                "preserveData": True,
            },
            "planId": plan_id,
        },
        headers={"X-Echo-Approval": "approval-token"},
    )

    assert response.status_code == 200
    assert approval_calls[0]["action"] == "omv.shared-folder.detach"
    assert {entry["action"] for entry in audit_calls} == {"omv.shared-folder.detach"}


def test_native_alias_binds_empty_folder_delete_to_approval_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ECHO_APPLIANCE", raising=False)
    plan_id = "d" * 64
    current_plan = {
        "planId": plan_id,
        "operation": "remove",
        "requiresApproval": True,
    }
    applied = {
        **current_plan,
        "applied": True,
        "verified": True,
        "directoryDeleted": True,
        "dataDeleted": False,
    }
    approval_calls: list[dict[str, Any]] = []
    audit_calls: list[dict[str, Any]] = []

    class Approval:
        def consume(self, **kwargs: Any) -> None:
            approval_calls.append(kwargs)

    class Audit:
        def record(self, **kwargs: Any) -> None:
            audit_calls.append(kwargs)

    monkeypatch.setattr(native_storage, "plan_shared_folder_delete", lambda _desired: current_plan)
    monkeypatch.setattr(
        native_storage,
        "apply_shared_folder_delete",
        lambda _desired, _plan_id: applied,
    )
    app = FastAPI()
    app.include_router(create_omv_alias_router(approval=Approval(), audit=Audit()))
    client = TestClient(app)

    response = client.post(
        "/api/appliance/omv/sharing/folders/delete/apply",
        json={
            "desired": {
                "schema": "echo.omv.shared-folder-delete-desired.v1",
                "sharedFolderRef": MOUNT_POINT_REF,
                "emptyOnly": True,
            },
            "planId": plan_id,
        },
        headers={"X-Echo-Approval": "approval-token"},
    )

    assert response.status_code == 200
    assert approval_calls[0]["action"] == "omv.shared-folder.delete"
    assert {entry["action"] for entry in audit_calls} == {"omv.shared-folder.delete"}


def test_native_alias_maps_apply_io_failure_to_service_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ECHO_APPLIANCE", raising=False)
    plan_id = "a" * 64
    monkeypatch.setattr(
        native_storage,
        "plan_shared_folder",
        lambda _desired: {
            "planId": plan_id,
            "operation": "create",
            "requiresApproval": True,
        },
    )
    monkeypatch.setattr(
        native_storage,
        "apply_shared_folder",
        lambda _desired, _plan_id: (_ for _ in ()).throw(OSError("disk failure")),
    )
    app = FastAPI()
    app.include_router(create_omv_alias_router())
    client = TestClient(app)

    response = client.post(
        "/api/appliance/omv/sharing/folders/apply",
        json={"desired": _desired(MOUNT_POINT_REF), "planId": plan_id},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "原生存储面暂不可用"}


def test_native_alias_groups_apply_rejects_a_stale_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ECHO_APPLIANCE", raising=False)
    current_plan = {"planId": "a" * 64, "operation": "create", "requiresApproval": True}
    monkeypatch.setattr(native_storage, "plan_group", lambda _desired: current_plan)
    app = FastAPI()
    app.include_router(create_omv_alias_router())
    client = TestClient(app)

    response = client.post(
        "/api/appliance/omv/accounts/groups/apply",
        json={
            "desired": {"schema": "echo.omv.group-desired.v1", "name": "family", "comment": "x"},
            "planId": "b" * 64,
        },
    )

    assert response.status_code == 409


def test_native_alias_binds_zfs_mirror_creation_to_destructive_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ECHO_APPLIANCE", raising=False)
    plan_id = "f" * 64
    current_plan = {"planId": plan_id, "operation": "create", "requiresApproval": True}
    applied = {**current_plan, "applied": True, "verified": True}
    approval_calls: list[dict[str, Any]] = []
    audit_calls: list[dict[str, Any]] = []

    class Approval:
        def consume(self, **kwargs: Any) -> None:
            approval_calls.append(kwargs)

    class Audit:
        def record(self, **kwargs: Any) -> None:
            audit_calls.append(kwargs)

    monkeypatch.setattr(native_storage, "plan_zfs_mirror", lambda _desired: current_plan)
    monkeypatch.setattr(
        native_storage,
        "apply_zfs_mirror",
        lambda _desired, _plan_id: applied,
    )
    app = FastAPI()
    app.include_router(create_omv_alias_router(approval=Approval(), audit=Audit()))
    client = TestClient(app)

    response = client.post(
        "/api/appliance/omv/pools/zfs-mirror/apply",
        json={
            "desired": {
                "schema": "echo.omv.zfs-mirror-desired.v1",
                "name": "family",
                "devices": ["/dev/sdb", "/dev/sdc"],
                "dataLossConfirmed": True,
            },
            "planId": plan_id,
        },
        headers={"X-Echo-Approval": "approval-token"},
    )

    assert response.status_code == 200
    assert approval_calls[0]["action"] == "omv.zfs-mirror.create"
    assert {entry["action"] for entry in audit_calls} == {"omv.zfs-mirror.create"}


def test_native_alias_exposes_only_server_validated_zfs_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ECHO_APPLIANCE", raising=False)
    expected = [
        {
            "devicefile": "/dev/sdb",
            "sizeBytes": 8 * 1024**3,
            "serial": "disk-b",
            "wwn": None,
            "model": "QEMU HARDDISK",
        }
    ]
    monkeypatch.setattr(native_storage, "zfs_mirror_candidates", lambda: expected)
    app = FastAPI()
    app.include_router(create_omv_alias_router())

    response = TestClient(app).get("/api/appliance/omv/pools/zfs-mirror/candidates")

    assert response.status_code == 200
    assert response.json() == {"devices": expected, "readOnly": True, "source": "native"}


def test_native_alias_exposes_only_server_validated_zfs_replacement_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ECHO_APPLIANCE", raising=False)
    expected = [
        {
            "pool": {
                "name": "family",
                "poolGuid": "15451357997522795478",
                "health": "DEGRADED",
                "sizeBytes": 16 * 1024**3,
            },
            "layout": "twoDiskMirror",
            "replaceableMember": {
                "slot": 2,
                "vdevGuid": "2222222222222222222",
                "state": "UNAVAIL",
            },
            "minimumReplacementBytes": 16 * 1024**3,
            "replacementDevices": [],
        }
    ]
    monkeypatch.setattr(
        native_storage,
        "zfs_mirror_replacement_candidates",
        lambda: expected,
    )
    app = FastAPI()
    app.include_router(create_omv_alias_router())

    response = TestClient(app).get("/api/appliance/omv/pools/zfs-mirror/replacement-candidates")

    assert response.status_code == 200
    assert response.json() == {
        "replacements": expected,
        "readOnly": True,
        "source": "native",
    }


def test_native_alias_binds_zfs_mirror_replace_to_exact_approval_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ECHO_APPLIANCE", raising=False)
    plan_id = "9" * 64
    current_plan = {"planId": plan_id, "operation": "replace", "requiresApproval": True}
    applied = {**current_plan, "applied": True, "verified": True, "dataPreserved": True}
    approval_calls: list[dict[str, Any]] = []
    audit_calls: list[dict[str, Any]] = []

    class Approval:
        def consume(self, **kwargs: Any) -> None:
            approval_calls.append(kwargs)

    class Audit:
        def record(self, **kwargs: Any) -> None:
            audit_calls.append(kwargs)

    monkeypatch.setattr(native_storage, "plan_zfs_mirror_replace", lambda _desired: current_plan)
    monkeypatch.setattr(
        native_storage,
        "apply_zfs_mirror_replace",
        lambda _desired, _plan_id: applied,
    )
    app = FastAPI()
    app.include_router(create_omv_alias_router(approval=Approval(), audit=Audit()))

    response = TestClient(app).post(
        "/api/appliance/omv/pools/zfs-mirror/replace/apply",
        json={
            "desired": {
                "schema": "echo.omv.zfs-mirror-replace-desired.v1",
                "name": "family",
                "poolGuid": "15451357997522795478",
                "oldVdevGuid": "2222222222222222222",
                "replacementDevice": "/dev/sdd",
                "dataPreserved": True,
            },
            "planId": plan_id,
        },
        headers={"X-Echo-Approval": "approval-token"},
    )

    assert response.status_code == 200
    assert approval_calls[0]["action"] == "omv.zfs-mirror.replace"
    assert {entry["action"] for entry in audit_calls} == {"omv.zfs-mirror.replace"}


def test_native_alias_exposes_only_safe_zfs_import_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ECHO_APPLIANCE", raising=False)
    expected = [
        {
            "name": "family",
            "poolGuid": "15451357997522795478",
            "state": "ONLINE",
            "layout": "mirror",
            "configHash": "c" * 64,
            "safeToImport": True,
        }
    ]
    monkeypatch.setattr(native_storage, "importable_zfs_pools", lambda: expected)
    app = FastAPI()
    app.include_router(create_omv_alias_router())

    response = TestClient(app).get("/api/appliance/omv/pools/zfs/import-candidates")

    assert response.status_code == 200
    assert response.json() == {"pools": expected, "readOnly": True, "source": "native"}


def test_native_alias_exposes_only_safe_zfs_export_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ECHO_APPLIANCE", raising=False)
    expected = [
        {
            "name": "family",
            "poolGuid": "15451357997522795478",
            "health": "ONLINE",
            "sizeBytes": 16 * 1024**3,
            "rootMountpoint": "/data/family",
            "datasetCount": 1,
            "mountedCount": 1,
            "safeToExport": True,
        }
    ]
    monkeypatch.setattr(native_storage, "exportable_zfs_pools", lambda: expected)
    app = FastAPI()
    app.include_router(create_omv_alias_router())

    response = TestClient(app).get("/api/appliance/omv/pools/zfs")

    assert response.status_code == 200
    assert response.json() == {"pools": expected, "readOnly": True, "source": "native"}


def test_native_alias_exposes_zfs_maintenance_without_raw_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ECHO_APPLIANCE", raising=False)
    expected = [
        {
            "pool": {
                "name": "family",
                "poolGuid": "15451357997522795478",
                "health": "ONLINE",
                "sizeBytes": 16 * 1024**3,
            },
            "rootMountpoint": "/data/family",
            "scan": {
                "kind": "resilver",
                "state": "inProgress",
                "progressPercent": 42.5,
                "errors": None,
                "summaryHash": "a" * 64,
            },
            "canStartScrub": False,
        }
    ]
    monkeypatch.setattr(native_storage, "zfs_pool_maintenance", lambda: expected)
    app = FastAPI()
    app.include_router(create_omv_alias_router())

    response = TestClient(app).get("/api/appliance/omv/pools/zfs/maintenance")

    assert response.status_code == 200
    assert response.json() == {"pools": expected, "readOnly": True, "source": "native"}


def test_native_alias_binds_zfs_scrub_to_exact_approval_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ECHO_APPLIANCE", raising=False)
    plan_id = "f" * 64
    desired = {
        "schema": "echo.omv.zfs-scrub-desired.v1",
        "name": "family",
        "poolGuid": "15451357997522795478",
        "operation": "start",
    }
    current_plan = {"planId": plan_id, "operation": "start", "requiresApproval": True}
    approval_calls: list[dict[str, Any]] = []
    audit_calls: list[dict[str, Any]] = []

    class Approval:
        def consume(self, **kwargs: Any) -> None:
            approval_calls.append(kwargs)

    class Audit:
        def record(self, **kwargs: Any) -> None:
            audit_calls.append(kwargs)

    monkeypatch.setattr(native_storage, "plan_zfs_scrub", lambda _desired: current_plan)
    monkeypatch.setattr(
        native_storage,
        "apply_zfs_scrub",
        lambda _desired, _plan_id: {**current_plan, "applied": True, "verified": True},
    )
    app = FastAPI()
    app.include_router(create_omv_alias_router(approval=Approval(), audit=Audit()))

    response = TestClient(app).post(
        "/api/appliance/omv/pools/zfs/scrub/apply",
        json={"desired": desired, "planId": plan_id},
        headers={"X-Echo-Approval": "approval-token"},
    )

    assert response.status_code == 200
    assert approval_calls[0]["action"] == "omv.zfs.scrub.start"
    assert {entry["action"] for entry in audit_calls} == {"omv.zfs.scrub.start"}


@pytest.mark.parametrize(
    ("operation", "action"),
    [("export", "omv.zfs-pool.export"), ("import", "omv.zfs-pool.import")],
)
def test_native_alias_binds_zfs_lifecycle_to_exact_approval_action(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    action: str,
) -> None:
    monkeypatch.delenv("ECHO_APPLIANCE", raising=False)
    plan_id = "e" * 64
    current_plan = {"planId": plan_id, "operation": operation, "requiresApproval": True}
    applied = {**current_plan, "applied": True, "verified": True, "dataPreserved": True}
    approval_calls: list[dict[str, Any]] = []
    audit_calls: list[dict[str, Any]] = []

    class Approval:
        def consume(self, **kwargs: Any) -> None:
            approval_calls.append(kwargs)

    class Audit:
        def record(self, **kwargs: Any) -> None:
            audit_calls.append(kwargs)

    desired = (
        {
            "schema": "echo.omv.zfs-pool-export-desired.v1",
            "name": "family",
            "poolGuid": "15451357997522795478",
            "dataPreserved": True,
        }
        if operation == "export"
        else {
            "schema": "echo.omv.zfs-pool-import-desired.v1",
            "name": "family",
            "poolGuid": "15451357997522795478",
            "mountPolicy": "echoDataRootOnly",
        }
    )
    plan_name = f"plan_zfs_pool_{operation}"
    apply_name = f"apply_zfs_pool_{operation}"
    monkeypatch.setattr(native_storage, plan_name, lambda _desired: current_plan)
    monkeypatch.setattr(
        native_storage,
        apply_name,
        lambda _desired, _plan_id: applied,
    )
    app = FastAPI()
    app.include_router(create_omv_alias_router(approval=Approval(), audit=Audit()))

    response = TestClient(app).post(
        f"/api/appliance/omv/pools/zfs/{operation}/apply",
        json={"desired": desired, "planId": plan_id},
        headers={"X-Echo-Approval": "approval-token"},
    )

    assert response.status_code == 200
    assert approval_calls[0]["action"] == action
    assert {entry["action"] for entry in audit_calls} == {action}


# --- Write-plane slices: accounts, SMB, quota (subprocess mocked) --------


@pytest.fixture
def posix_accounts(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    created_groups: list[str] = ["users"]
    created_users: list[str] = []
    user_records: dict[str, dict[str, Any]] = {}
    secrets: list[tuple[str, str]] = []
    samba: list[str] = []

    def fake_group_exists(name: str) -> bool:
        return name in created_groups

    def fake_user_exists(name: str) -> bool:
        return name in created_users

    def default_user_record(name: str) -> dict[str, Any]:
        return {
            "name": name,
            "uid": 1001,
            "gid": 1001,
            "comment": "Mom" if name == "mother" else name.title(),
            "home": f"/home/{name}",
            "shell": "/usr/sbin/nologin",
            "groups": ["users"],
        }

    def fake_user_snapshot(name: str) -> dict[str, Any] | None:
        if name not in created_users:
            return None
        record = user_records.setdefault(name, default_user_record(name))
        return record if record["shell"] == "/usr/sbin/nologin" else None

    def fake_run_write(*args: str, timeout: float = 60.0) -> None:
        if args[:1] == ("groupadd",):
            created_groups.append(args[1])
        elif args[:1] == ("useradd",):
            name = args[-1]
            created_users.append(name)
            group_index = args.index("-G") + 1
            groups = sorted(set(args[group_index].split(",")))
            comment = args[args.index("--comment") + 1]
            user_records[name] = {**default_user_record(name), "comment": comment, "groups": groups}
        elif args[:2] == ("userdel",):
            created_users.remove(args[1])
            user_records.pop(args[1], None)
        elif args[:1] == ("zfs",):
            pass
        else:
            raise AssertionError(f"unexpected command {args}")

    def fake_run_write_stdin(*args: str, input_text: str = "", timeout: float = 30.0) -> None:
        if args[:1] == ("chpasswd",):
            name, _, pw = input_text.partition(":")
            secrets.append((name, pw.rstrip("\n")))
        elif args[:3] == ("smbpasswd", "-a", "-s"):
            samba.append(args[3])
        else:
            raise AssertionError(f"unexpected stdin command {args}")

    monkeypatch.setattr(native_storage, "_group_exists", fake_group_exists)
    monkeypatch.setattr(native_storage, "_user_exists", fake_user_exists)
    monkeypatch.setattr(native_storage, "_constrained_user_snapshot", fake_user_snapshot)
    monkeypatch.setattr(native_storage, "_run_write", fake_run_write)
    monkeypatch.setattr(native_storage, "_run_write_stdin", fake_run_write_stdin)
    return {
        "groups": created_groups,
        "users": created_users,
        "records": user_records,
        "secrets": secrets,
        "samba": samba,
    }


def test_group_create_is_idempotent(posix_accounts: dict[str, list[Any]]) -> None:
    desired = {"schema": "echo.omv.group-desired.v1", "name": "family", "comment": "Family share"}
    plan = native_storage.plan_group(desired)

    assert plan["operation"] == "create"
    assert plan["requiresApproval"] is True
    applied = native_storage.apply_group(desired, plan["planId"])
    assert applied["applied"] is True
    assert "family" in posix_accounts["groups"]

    repeat = native_storage.plan_group(desired)
    assert repeat["operation"] == "none"
    none_applied = native_storage.apply_group(desired, repeat["planId"])
    assert none_applied["applied"] is False


def test_user_create_enables_system_and_samba(posix_accounts: dict[str, list[Any]]) -> None:
    desired = {
        "schema": "echo.omv.user-desired.v1",
        "name": "mother",
        "displayName": "Mom",
        "password": "Family-Shared-2026!",
        "groups": [],
    }
    plan = native_storage.plan_user(desired)

    assert plan["operation"] == "create"
    assert plan["desired"]["passwordBound"] is True
    assert "password" not in plan["desired"]

    applied = native_storage.apply_user(desired, plan["planId"])
    assert applied["applied"] is True
    assert "mother" in posix_accounts["users"]
    assert ("mother", "Family-Shared-2026!") in posix_accounts["secrets"]
    assert "mother" in posix_accounts["samba"]
    assert posix_accounts["records"]["mother"]["comment"] == "Mom"
    assert posix_accounts["records"]["mother"]["groups"] == ["users"]


def test_user_create_requires_existing_group(posix_accounts: dict[str, list[Any]]) -> None:
    desired = {
        "schema": "echo.omv.user-desired.v1",
        "name": "kid",
        "displayName": "Kid",
        "password": "Family-Shared-2026!",
        "groups": ["nope"],
    }
    with pytest.raises(ValueError, match="does not exist"):
        native_storage.plan_user(desired)


def test_user_password_reset(posix_accounts: dict[str, Any]) -> None:
    posix_accounts["users"].append("mother")
    desired = {
        "schema": "echo.omv.user-password-desired.v1",
        "name": "mother",
        "password": "New-Family-2026!",
    }
    plan = native_storage.plan_user_password(desired)

    assert plan["operation"] == "resetPassword"
    applied = native_storage.apply_user_password(desired, plan["planId"])
    assert applied["applied"] is True
    assert ("mother", "New-Family-2026!") in posix_accounts["secrets"]


def test_user_password_reset_rejects_unconstrained_account(
    posix_accounts: dict[str, Any],
) -> None:
    posix_accounts["users"].append("service")
    posix_accounts["records"]["service"] = {
        "name": "service",
        "uid": 1002,
        "gid": 1002,
        "comment": "Service",
        "home": "/home/service",
        "shell": "/bin/bash",
        "groups": ["users"],
    }
    desired = {
        "schema": "echo.omv.user-password-desired.v1",
        "name": "service",
        "password": "New-Service-2026!",
    }
    with pytest.raises(ValueError, match="constrained normal NAS user"):
        native_storage.plan_user_password(desired)


def test_smb_share_enable(
    native_volume: tuple[Path, Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    volume, registry, mount_point_ref = native_volume
    folder_uuid = "11111111-2222-4333-8444-555555555555"
    folder = _register_folder(volume, registry, mount_point_ref, folder_uuid)
    calls: list[tuple[Any, ...]] = []
    info_calls = {"n": 0}

    def fake_info(_name: str) -> dict[str, Any] | None:
        info_calls["n"] += 1
        # Plan, then apply's internal re-plan: share not created yet -> None.
        # The post-create verify call sees the (simulated) registered share.
        return None if info_calls["n"] < 3 else {"comment": "Media"}

    monkeypatch.setattr(native_storage, "_group_exists", lambda _name: True)
    monkeypatch.setattr(native_storage, "_smb_usershare_info", fake_info)
    monkeypatch.setattr(native_storage, "_run_write", lambda *a, timeout=60.0: calls.append(a))

    desired = {
        "schema": "echo.omv.smb-share-desired.v1",
        "sharedFolderRef": folder_uuid,
        "enabled": True,
        "readOnly": False,
        "browseable": True,
        "recycleBin": False,
        "comment": "Media",
    }
    plan = native_storage.plan_smb(desired)
    assert plan["operation"] == "create"
    assert plan["sharedFolder"] == {
        "uuid": folder_uuid,
        "name": "Photos",
        "status": "MOUNTED",
    }
    assert plan["shareUuid"] == native_storage._smb_share_uuid(folder_uuid)
    assert "target" not in plan
    assert str(folder) not in json.dumps(plan, ensure_ascii=False)
    applied = native_storage.apply_smb(desired, plan["planId"])
    assert applied["applied"] is True
    assert "path" not in applied["share"]
    assert str(folder) not in json.dumps(applied, ensure_ascii=False)
    assert calls and calls[0][:3] == ("net", "usershare", "add")


def test_smb_share_remove_requires_a_verified_absent_usershare(
    native_volume: tuple[Path, Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    volume, registry, mount_point_ref = native_volume
    folder_uuid = "11111111-2222-4333-8444-555555555555"
    _register_folder(volume, registry, mount_point_ref, folder_uuid)
    calls: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        native_storage,
        "_smb_usershare_info",
        lambda _name: {"comment": "Media", "usershare_acl": "users:f"},
    )
    monkeypatch.setattr(native_storage, "_run_write", lambda *args, **_kwargs: calls.append(args))
    desired = {
        "schema": "echo.omv.smb-share-desired.v1",
        "sharedFolderRef": folder_uuid,
        "enabled": False,
        "readOnly": False,
        "browseable": True,
        "recycleBin": False,
        "comment": "Media",
    }

    plan = native_storage.plan_smb(desired)
    assert plan["operation"] == "remove"
    with pytest.raises(OSError, match="remained after delete"):
        native_storage.apply_smb(desired, plan["planId"])
    assert calls == [("net", "usershare", "delete", "Photos")]


def test_smb_share_remove_can_clean_rule_when_volume_is_unmounted(
    native_volume: tuple[Path, Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    volume, registry, mount_point_ref = native_volume
    folder_uuid = "11111111-2222-4333-8444-555555555555"
    folder = _register_folder(volume, registry, mount_point_ref, folder_uuid)
    calls: list[tuple[Any, ...]] = []
    present = {"value": True}

    monkeypatch.setattr(
        native_storage,
        "_smb_usershare_info",
        lambda _name: (
            {"comment": "Media", "usershare_acl": "users:f"} if present["value"] else None
        ),
    )

    def fake_write(*args: str, **_kwargs: Any) -> None:
        calls.append(args)
        if args == ("net", "usershare", "delete", "Photos"):
            present["value"] = False

    monkeypatch.setattr(native_storage, "_run_write", fake_write)
    monkeypatch.setattr(native_storage, "filesystems", lambda: [])
    desired = {
        "schema": "echo.omv.smb-share-desired.v1",
        "sharedFolderRef": folder_uuid,
        "enabled": False,
        "readOnly": False,
        "browseable": True,
        "recycleBin": False,
        "comment": "Media",
    }

    plan = native_storage.plan_smb(desired)
    assert plan["operation"] == "remove"
    assert plan["sharedFolder"]["status"] == "UNAVAILABLE"
    applied = native_storage.apply_smb(desired, plan["planId"])

    assert applied["applied"] is True
    assert applied["verified"] is True
    assert calls == [("net", "usershare", "delete", "Photos")]
    assert folder.is_dir()


def test_smb_disabled_noop_fails_if_share_appears_during_apply(
    native_volume: tuple[Path, Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    volume, registry, mount_point_ref = native_volume
    folder_uuid = "11111111-2222-4333-8444-555555555555"
    _register_folder(volume, registry, mount_point_ref, folder_uuid)
    info_calls = {"n": 0}

    def fake_info(_name: str) -> dict[str, Any] | None:
        info_calls["n"] += 1
        # The plan and apply re-plan both see no share; the final read-back
        # simulates another actor creating it before the no-op is verified.
        return {"comment": "race", "usershare_acl": "users:f"} if info_calls["n"] >= 3 else None

    monkeypatch.setattr(native_storage, "_smb_usershare_info", fake_info)
    desired = {
        "schema": "echo.omv.smb-share-desired.v1",
        "sharedFolderRef": folder_uuid,
        "enabled": False,
        "readOnly": False,
        "browseable": True,
        "recycleBin": False,
        "comment": "race",
    }

    plan = native_storage.plan_smb(desired)
    assert plan["operation"] == "none"
    with pytest.raises(OSError, match="appeared during apply"):
        native_storage.apply_smb(desired, plan["planId"])
    assert info_calls["n"] == 3


def test_native_smb_rejects_unmanaged_usershare_options() -> None:
    base = {
        "schema": "echo.omv.smb-share-desired.v1",
        "sharedFolderRef": "11111111-2222-4333-8444-555555555555",
        "enabled": True,
        "readOnly": False,
        "browseable": True,
        "recycleBin": False,
        "comment": "Media",
    }
    with pytest.raises(ValueError, match="recycle bin"):
        native_storage.plan_smb({**base, "recycleBin": True})
    with pytest.raises(ValueError, match="discovery"):
        native_storage.plan_smb({**base, "browseable": False})


def test_quota_requires_a_supported_native_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    fs_uuid = "11111111-2222-4333-8444-555555555555"
    monkeypatch.setattr(
        native_storage,
        "filesystems",
        lambda: [
            {
                "uuid": fs_uuid,
                "type": "ext4",
                "devicefile": "/dev/sda1",
                "mountpoint": "/data/family",
            }
        ],
    )
    desired = {
        "schema": "echo.omv.filesystem-quota-desired.v1",
        "filesystemUuid": fs_uuid,
        "subjectType": "user",
        "subjectName": "mother",
        "hardLimitBytes": 1024**3,
    }
    monkeypatch.setattr(native_storage, "_native_quota_tools_available", lambda: False)
    with pytest.raises(ValueError, match="kernel quota tools"):
        native_storage.plan_quota(desired)


def test_quota_plan_on_zfs(monkeypatch: pytest.MonkeyPatch) -> None:
    fs_uuid = "11111111-2222-4333-8444-555555555555"
    monkeypatch.setattr(
        native_storage,
        "filesystems",
        lambda: [
            {
                "uuid": fs_uuid,
                "type": "zfs",
                "devicefile": "tank/share",
                "mountpoint": "/srv/family",
                "label": "share",
                "readOnly": False,
                "supportsQuota": True,
            }
        ],
    )
    monkeypatch.setattr(native_storage, "_principal_id", lambda _kind, _name: 1001)
    quota_value = {"bytes": 0}

    def fake_read(*_args: str, **_kwargs: Any) -> str:
        return "none\n" if quota_value["bytes"] == 0 else f"{quota_value['bytes']}\n"

    monkeypatch.setattr(native_storage, "_run_read_checked", fake_read)
    zfs_calls: list[tuple[Any, ...]] = []

    def fake_write(*args: str, timeout: float = 60.0) -> None:
        zfs_calls.append(args)
        value = args[2].split("=", 1)[1]
        quota_value["bytes"] = 0 if value == "none" else int(value)

    monkeypatch.setattr(native_storage, "_run_write", fake_write)
    desired = {
        "schema": "echo.omv.filesystem-quota-desired.v1",
        "filesystemUuid": fs_uuid,
        "subjectType": "user",
        "subjectName": "mother",
        "hardLimitBytes": 1024**3,
    }
    plan = native_storage.plan_quota(desired)
    assert plan["operation"] == "update"
    assert plan["filesystem"] == {
        "uuid": fs_uuid,
        "label": "share",
        "type": "zfs",
        "readOnly": False,
        "supportsQuota": True,
    }
    assert plan["subject"] == {
        "type": "user",
        "name": "mother",
        "hardLimitBytes": 0,
        "used": "unknown",
    }
    assert "dataset" not in json.dumps(plan, ensure_ascii=False)
    applied = native_storage.apply_quota(desired, plan["planId"])
    assert applied["applied"] is True
    assert zfs_calls and zfs_calls[0][:2] == ("zfs", "set")
    assert "userquota@mother" in zfs_calls[0][2]
    assert applied["quota"] == {
        "filesystemUuid": fs_uuid,
        "subjectType": "user",
        "subjectName": "mother",
        "hardLimitBytes": 1024**3,
    }
    assert "dataset" not in json.dumps(applied, ensure_ascii=False)


def test_quota_plan_and_apply_on_ext4_kernel_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    fs_uuid = "22222222-3333-4444-8555-666666666666"
    mountpoint = "/srv/family"
    monkeypatch.setattr(
        native_storage,
        "filesystems",
        lambda: [
            {
                "uuid": fs_uuid,
                "type": "ext4",
                "mountpoint": mountpoint,
                "label": "family",
                "readOnly": False,
                "supportsQuota": True,
            }
        ],
    )
    monkeypatch.setattr(native_storage, "_native_quota_tools_available", lambda: True)
    monkeypatch.setattr(native_storage, "_mount_read_only", lambda _path: False)
    monkeypatch.setattr(native_storage, "_principal_id", lambda _kind, _name: 1001)
    quota_bytes = {"hard": 4 * 1024}

    def fake_report(*_args: str, **_kwargs: Any) -> str:
        hard_kib = quota_bytes["hard"] // 1024
        return (
            "User,BlockStatus,FileStatus,BlockUsed,BlockSoftLimit,BlockHardLimit,"
            "BlockGrace,FileUsed,FileSoftLimit,FileHardLimit,FileGrace\n"
            f"mother,-,-,1,0,{hard_kib},0,0,0,0,0\n"
        )

    monkeypatch.setattr(native_storage, "_run_read_checked", fake_report)
    setquota_calls: list[tuple[str, ...]] = []

    def fake_setquota(*args: str, **_kwargs: Any) -> None:
        setquota_calls.append(args)
        quota_bytes["hard"] = int(args[4]) * 1024

    monkeypatch.setattr(native_storage, "_run_write", fake_setquota)
    desired = {
        "schema": "echo.omv.filesystem-quota-desired.v1",
        "filesystemUuid": fs_uuid,
        "subjectType": "user",
        "subjectName": "mother",
        "hardLimitBytes": 1024**2,
    }

    plan = native_storage.plan_quota(desired)

    assert plan["operation"] == "update"
    assert plan["filesystem"] == {
        "uuid": fs_uuid,
        "label": "family",
        "type": "ext4",
        "readOnly": False,
        "supportsQuota": True,
    }
    assert plan["subject"] == {
        "type": "user",
        "name": "mother",
        "hardLimitBytes": 4 * 1024,
        "used": "1 KiB",
    }
    assert mountpoint not in json.dumps(plan, ensure_ascii=False)

    applied = native_storage.apply_quota(desired, plan["planId"])

    assert applied["applied"] is True
    assert applied["verified"] is True
    assert setquota_calls == [("setquota", "-u", "mother", "0", "1024", "0", "0", mountpoint)]
    assert applied["quota"] == {
        "filesystemUuid": fs_uuid,
        "subjectType": "user",
        "subjectName": "mother",
        "hardLimitBytes": 1024**2,
    }
    assert mountpoint not in json.dumps(applied, ensure_ascii=False)


def test_quota_rejects_kernel_filesystem_without_mount_quota(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fs_uuid = "33333333-4444-4555-8666-777777777777"
    mountpoint = "/srv/family"
    monkeypatch.setattr(
        native_storage,
        "filesystems",
        lambda: [
            {
                "uuid": fs_uuid,
                "type": "ext4",
                "mountpoint": mountpoint,
                "readOnly": False,
                "supportsQuota": True,
            }
        ],
    )
    monkeypatch.setattr(native_storage, "_native_quota_tools_available", lambda: True)
    monkeypatch.setattr(native_storage, "_mount_read_only", lambda _path: False)
    monkeypatch.setattr(native_storage, "_principal_id", lambda _kind, _name: 1001)
    monkeypatch.setattr(native_storage.shutil, "which", lambda _binary: "/usr/bin/findmnt")
    monkeypatch.setattr(native_storage, "_run", lambda *_args, **_kwargs: "rw,relatime\n")
    desired = {
        "schema": "echo.omv.filesystem-quota-desired.v1",
        "filesystemUuid": fs_uuid,
        "subjectType": "user",
        "subjectName": "mother",
        "hardLimitBytes": 1024**2,
    }
    with pytest.raises(ValueError, match="not enabled"):
        native_storage.plan_quota(desired)


# --- Native privilege and NFS slices ------------------------------------


def _register_folder(volume: Path, registry: Path, mount_point_ref: str, folder_uuid: str) -> Path:
    folder = volume / "Photos"
    folder.mkdir()
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(
        json.dumps(
            [
                {
                    "uuid": folder_uuid,
                    "name": "Photos",
                    "comment": "Family photos",
                    "relativePath": "Photos",
                    "device": "/dev/test0",
                    "volumePath": str(volume),
                    "mountPointRef": mount_point_ref,
                    "createdAt": "2026-09-05T00:00:00+00:00",
                }
            ]
        ),
        encoding="utf-8",
    )
    return folder


def _privilege_desired(folder_uuid: str, permission: str = "readWrite") -> dict[str, Any]:
    return {
        "schema": "echo.omv.share-privilege-desired.v1",
        "sharedFolderRef": folder_uuid,
        "principalType": "user",
        "principalName": "mother",
        "permission": permission,
    }


def _nfs_desired(folder_uuid: str, **overrides: Any) -> dict[str, Any]:
    return {
        "schema": "echo.omv.nfs-share-desired.v1",
        "sharedFolderRef": folder_uuid,
        "clientCidr": "192.168.50.0/24",
        "readOnly": False,
        "comment": "Family LAN",
        **overrides,
    }


def test_share_privilege_plan_and_apply_use_non_recursive_acl(
    native_volume: tuple[Path, Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    volume, registry, mount_point_ref = native_volume
    folder_uuid = "11111111-2222-4333-8444-555555555555"
    folder = _register_folder(volume, registry, mount_point_ref, folder_uuid)
    original_acl = (
        f"# file: {folder}\n# owner: root\n# group: users\n"
        "user::rwx\ngroup::rwx\nmask::rwx\nother::---\n"
    )
    acl = {"text": original_acl}
    calls: list[tuple[str, ...]] = []

    monkeypatch.setattr(native_storage, "_principal_id", lambda _kind, _name: 1001)
    monkeypatch.setattr(native_storage, "_read_acl", lambda _path: acl["text"])

    def fake_write(*args: str, timeout: float = 60.0) -> None:
        calls.append(args)
        acl["text"] = original_acl + "user:mother:rwx\ndefault:user:mother:rwx\n"

    monkeypatch.setattr(native_storage, "_run_write", fake_write)
    desired = _privilege_desired(folder_uuid)
    plan = native_storage.plan_share_privilege(desired)

    assert plan["operation"] == "update"
    assert plan["principal"]["before"] == "inherit"
    applied = native_storage.apply_share_privilege(desired, plan["planId"])

    assert applied["applied"] is True
    assert applied["verified"] is True
    assert calls == [
        (
            "setfacl",
            "-m",
            "u:mother:rwx,d:u:mother:rwx",
            str(folder),
        )
    ]
    assert all("-R" not in call for call in calls)


def test_share_privilege_failure_restores_full_acl_snapshot(
    native_volume: tuple[Path, Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    volume, registry, mount_point_ref = native_volume
    folder_uuid = "11111111-2222-4333-8444-555555555555"
    folder = _register_folder(volume, registry, mount_point_ref, folder_uuid)
    original_acl = f"# file: {folder}\nuser::rwx\ngroup::rwx\nmask::rwx\nother::---\n"
    acl = {"text": original_acl}
    restored: list[str] = []
    monkeypatch.setattr(native_storage, "_principal_id", lambda _kind, _name: 1001)
    monkeypatch.setattr(native_storage, "_read_acl", lambda _path: acl["text"])
    monkeypatch.setattr(native_storage, "_run_write", lambda *a, **_kw: None)

    def restore(*_args: str, input_text: str, timeout: float = 30.0) -> None:
        restored.append(input_text)
        acl["text"] = input_text

    monkeypatch.setattr(native_storage, "_run_write_stdin", restore)
    desired = _privilege_desired(folder_uuid)
    plan = native_storage.plan_share_privilege(desired)

    with pytest.raises(OSError, match="did not persist"):
        native_storage.apply_share_privilege(desired, plan["planId"])

    assert restored == [original_acl]
    assert acl["text"] == original_acl


def test_nfs_apply_writes_only_echo_managed_exports_and_verifies_live_state(
    native_volume: tuple[Path, Path, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    volume, registry, mount_point_ref = native_volume
    folder_uuid = "11111111-2222-4333-8444-555555555555"
    folder = _register_folder(volume, registry, mount_point_ref, folder_uuid)
    exports = tmp_path / "exports.d" / "echo-os.exports"
    exportfs_calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(native_storage, "_NATIVE_NFS_EXPORTS", exports)
    monkeypatch.setattr(
        native_storage, "_run_write", lambda *args, **_kwargs: exportfs_calls.append(args)
    )
    monkeypatch.setattr(
        native_storage,
        "_run_read_checked",
        lambda *_args, **_kwargs: (
            f"{folder} 192.168.50.0/24(rw,sync,no_subtree_check,root_squash,secure)\n"
        ),
    )
    desired = _nfs_desired(folder_uuid)
    plan = native_storage.plan_nfs(desired)

    assert plan["operation"] == "create"
    applied = native_storage.apply_nfs(desired, plan["planId"])

    assert applied["applied"] is True
    assert exportfs_calls == [("exportfs", "-ra")]
    text = exports.read_text(encoding="utf-8")
    assert text.startswith(
        "# Generated by Echo OS. Manual edits are rejected and never merged.\n# echo-os-rule {"
    )
    assert text.endswith(f"{folder} 192.168.50.0/24(rw,sync,no_subtree_check,root_squash,secure)\n")
    assert native_storage._nfs_exports_load(strict=True) == [applied["share"]]

    repeated_plan = native_storage.plan_nfs(desired)
    assert repeated_plan["operation"] == "none"
    repeated = native_storage.apply_nfs(desired, repeated_plan["planId"])
    assert repeated["applied"] is False

    exports.write_text(text + "# out-of-band edit\n", encoding="utf-8")
    with pytest.raises(OSError, match="unrecognized or modified"):
        native_storage.plan_nfs(desired)


def test_nfs_live_verify_failure_rolls_back_managed_exports(
    native_volume: tuple[Path, Path, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    volume, registry, mount_point_ref = native_volume
    folder_uuid = "11111111-2222-4333-8444-555555555555"
    _register_folder(volume, registry, mount_point_ref, folder_uuid)
    exports = tmp_path / "exports.d" / "echo-os.exports"
    monkeypatch.setattr(native_storage, "_NATIVE_NFS_EXPORTS", exports)
    monkeypatch.setattr(native_storage, "_run_write", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(native_storage, "_run_read_checked", lambda *_args, **_kwargs: "")
    desired = _nfs_desired(folder_uuid)
    plan = native_storage.plan_nfs(desired)

    with pytest.raises(OSError, match="absent from the live export table"):
        native_storage.apply_nfs(desired, plan["planId"])

    assert not exports.exists()


def test_nfs_remove_deletes_only_managed_rule_and_preserves_folder_data(
    native_volume: tuple[Path, Path, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    volume, registry, mount_point_ref = native_volume
    folder_uuid = "11111111-2222-4333-8444-555555555555"
    folder = _register_folder(volume, registry, mount_point_ref, folder_uuid)
    exports = tmp_path / "exports.d" / "echo-os.exports"
    live = {"present": False}
    exportfs_calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(native_storage, "_NATIVE_NFS_EXPORTS", exports)

    def fake_write(*args: str, **_kwargs: Any) -> None:
        exportfs_calls.append(args)
        if args == ("exportfs", "-ra"):
            live["present"] = len(exportfs_calls) == 1

    monkeypatch.setattr(native_storage, "_run_write", fake_write)
    monkeypatch.setattr(
        native_storage,
        "_run_read_checked",
        lambda *_args, **_kwargs: (
            f"{folder} 192.168.50.0/24(rw,sync,no_subtree_check,root_squash,secure)\n"
            if live["present"]
            else ""
        ),
    )
    nfs_desired = _nfs_desired(folder_uuid)
    nfs_plan = native_storage.plan_nfs(nfs_desired)
    native_storage.apply_nfs(nfs_desired, nfs_plan["planId"])

    remove_desired = {
        "schema": "echo.omv.nfs-share-remove-desired.v1",
        "sharedFolderRef": folder_uuid,
        "clientCidr": "192.168.50.0/24",
    }
    remove_plan = native_storage.plan_nfs_remove(remove_desired)

    assert remove_plan["operation"] == "remove"
    assert remove_plan["requiresApproval"] is True
    assert remove_plan["safety"]["data"] == "preserved"
    removed = native_storage.apply_nfs_remove(remove_desired, remove_plan["planId"])

    assert removed["applied"] is True
    assert removed["verified"] is True
    assert removed["dataPreserved"] is True
    assert not exports.exists()
    assert not live["present"]
    assert folder.is_dir()
    assert exportfs_calls == [("exportfs", "-ra"), ("exportfs", "-ra")]
    repeated_plan = native_storage.plan_nfs_remove(remove_desired)
    assert repeated_plan["operation"] == "none"
    repeated = native_storage.apply_nfs_remove(remove_desired, repeated_plan["planId"])
    assert repeated["applied"] is False
    assert repeated["verified"] is True


def test_nfs_remove_can_clean_rule_when_volume_is_unmounted(
    native_volume: tuple[Path, Path, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    volume, registry, mount_point_ref = native_volume
    folder_uuid = "11111111-2222-4333-8444-555555555555"
    folder = _register_folder(volume, registry, mount_point_ref, folder_uuid)
    exports = tmp_path / "exports.d" / "echo-os.exports"
    live = {"present": False}
    exportfs_calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(native_storage, "_NATIVE_NFS_EXPORTS", exports)

    def fake_write(*args: str, **_kwargs: Any) -> None:
        if args == ("exportfs", "-ra"):
            exportfs_calls.append(args)
            live["present"] = len(exportfs_calls) == 1

    monkeypatch.setattr(native_storage, "_run_write", fake_write)
    monkeypatch.setattr(
        native_storage,
        "_run_read_checked",
        lambda *_args, **_kwargs: (
            f"{folder} 192.168.50.0/24(rw,sync,no_subtree_check,root_squash,secure)\n"
            if live["present"]
            else ""
        ),
    )
    nfs_desired = _nfs_desired(folder_uuid)
    nfs_plan = native_storage.plan_nfs(nfs_desired)
    native_storage.apply_nfs(nfs_desired, nfs_plan["planId"])

    # The registry and export file remain available while the data mount is
    # gone. Removal must not require a writable target or touch the directory.
    monkeypatch.setattr(
        native_storage,
        "filesystems",
        lambda: [{"mountpoint": str(volume), "readOnly": True}],
    )
    read_only_plan = native_storage.plan_nfs_remove(
        {
            "schema": "echo.omv.nfs-share-remove-desired.v1",
            "sharedFolderRef": folder_uuid,
            "clientCidr": "192.168.50.0/24",
        }
    )
    assert read_only_plan["sharedFolder"]["status"] == "READ_ONLY"
    monkeypatch.setattr(native_storage, "filesystems", lambda: [])
    assert native_storage._native_nfs_shares()[0]["sharedFolderRef"] == folder_uuid
    remove_desired = {
        "schema": "echo.omv.nfs-share-remove-desired.v1",
        "sharedFolderRef": folder_uuid,
        "clientCidr": "192.168.50.0/24",
    }
    remove_plan = native_storage.plan_nfs_remove(remove_desired)

    assert remove_plan["operation"] == "remove"
    assert remove_plan["sharedFolder"]["status"] == "UNAVAILABLE"
    removed = native_storage.apply_nfs_remove(remove_desired, remove_plan["planId"])

    assert removed["applied"] is True
    assert removed["verified"] is True
    assert removed["dataPreserved"] is True
    assert not exports.exists()
    assert not live["present"]
    assert folder.is_dir()
    assert exportfs_calls == [("exportfs", "-ra"), ("exportfs", "-ra")]


def test_nfs_rejects_public_client_network_before_touching_host(
    native_volume: tuple[Path, Path, str],
) -> None:
    _volume, _registry, _mount_point_ref = native_volume
    with pytest.raises(ValueError, match="RFC1918"):
        native_storage.plan_nfs(
            _nfs_desired("11111111-2222-4333-8444-555555555555", clientCidr="8.8.8.0/24")
        )


def test_native_alias_exposes_privilege_and_nfs_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ECHO_APPLIANCE", raising=False)
    privilege_plan = {"planId": "a" * 64, "operation": "none"}
    nfs_plan = {"planId": "b" * 64, "operation": "none"}
    monkeypatch.setattr(native_storage, "plan_share_privilege", lambda _desired: privilege_plan)
    monkeypatch.setattr(native_storage, "plan_nfs", lambda _desired: nfs_plan)
    monkeypatch.setattr(
        native_storage,
        "share_privileges",
        lambda _uuid: [{"type": "user", "id": 1001, "name": "mother", "permission": "read"}],
    )
    app = FastAPI()
    app.include_router(create_omv_alias_router())
    client = TestClient(app)
    folder_uuid = "11111111-2222-4333-8444-555555555555"

    privilege = client.post(
        "/api/appliance/omv/sharing/privileges/plan",
        json=_privilege_desired(folder_uuid, "read"),
    )
    nfs = client.post(
        "/api/appliance/omv/sharing/nfs/plan",
        json=_nfs_desired(folder_uuid),
    )
    inventory = client.get(f"/api/appliance/omv/sharing/{folder_uuid}/privileges")

    assert privilege.status_code == 200
    assert nfs.status_code == 200
    assert inventory.status_code == 200
    assert inventory.json()["privileges"][0]["id"] == 1001


def test_native_alias_exposes_nfs_remove_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ECHO_APPLIANCE", raising=False)
    plan = {"planId": "c" * 64, "operation": "remove", "requiresApproval": True}
    monkeypatch.setattr(native_storage, "plan_nfs_remove", lambda _desired: plan)
    app = FastAPI()
    app.include_router(create_omv_alias_router())
    client = TestClient(app)

    response = client.post(
        "/api/appliance/omv/sharing/nfs/remove/plan",
        json={
            "schema": "echo.omv.nfs-share-remove-desired.v1",
            "sharedFolderRef": "11111111-2222-4333-8444-555555555555",
            "clientCidr": "192.168.50.0/24",
        },
    )

    assert response.status_code == 200
    assert response.json()["operation"] == "remove"
