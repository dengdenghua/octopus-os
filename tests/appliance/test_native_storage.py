from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
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


def test_native_status_advertises_only_the_available_write_slice() -> None:
    payload = native_storage.status()

    assert payload["readOnly"] is False
    assert payload["capabilities"] == [
        "shared-folder.create.simple.v1",
        "account.group.create.v1",
        "account.user.create.v1",
        "account.user.password.reset.v1",
        "smb.share.desired.v1",
        "filesystem.quota.user-group.v1",
    ]


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
        "filesystemUuid": None,
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


def test_shared_folder_create_is_atomic_and_idempotent(
    native_volume: tuple[Path, Path, str],
) -> None:
    volume, registry, mount_point_ref = native_volume
    desired = _desired(mount_point_ref)
    plan = native_storage.plan_shared_folder(desired)

    assert plan["operation"] == "create"
    assert plan["requiresApproval"] is True
    applied = native_storage.apply_shared_folder(desired, plan["planId"])

    assert applied["applied"] is True
    assert applied["verified"] is True
    assert (volume / "Photos").is_dir()
    persisted = json.loads(registry.read_text(encoding="utf-8"))
    assert persisted == [applied["sharedFolder"]]
    assert plan["shareUuid"] == applied["sharedFolder"]["uuid"]

    repeated_plan = native_storage.plan_shared_folder(desired)
    assert repeated_plan["operation"] == "none"
    assert repeated_plan["planId"] != plan["planId"]
    repeated = native_storage.apply_shared_folder(desired, repeated_plan["planId"])
    assert repeated["applied"] is False
    assert repeated["verified"] is True


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


# --- Write-plane slices: accounts, SMB, quota (subprocess mocked) --------


@pytest.fixture
def posix_accounts(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    created_groups: list[str] = []
    created_users: list[str] = []
    secrets: list[tuple[str, str]] = []
    samba: list[str] = []

    def fake_group_exists(name: str) -> bool:
        return name in created_groups

    def fake_user_exists(name: str) -> bool:
        return name in created_users

    def fake_run_write(*args: str, timeout: float = 60.0) -> None:
        if args[:1] == ("groupadd",):
            created_groups.append(args[1])
        elif args[:1] == ("useradd",):
            created_users.append(args[-1])
        elif args[:2] == ("userdel",):
            created_users.remove(args[1])
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
    monkeypatch.setattr(native_storage, "_run_write", fake_run_write)
    monkeypatch.setattr(native_storage, "_run_write_stdin", fake_run_write_stdin)
    return {"groups": created_groups, "users": created_users, "secrets": secrets, "samba": samba}


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


def test_user_password_reset(posix_accounts: dict[str, list[Any]]) -> None:
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


def test_smb_share_enable(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[Any, ...]] = []
    info_calls = {"n": 0}
    folder_uuid = "11111111-2222-4333-8444-555555555555"

    def fake_info(_name: str) -> dict[str, Any] | None:
        info_calls["n"] += 1
        # Plan, then apply's internal re-plan: share not created yet -> None.
        # The post-create verify call sees the (simulated) registered share.
        return None if info_calls["n"] < 3 else {"comment": "Media"}

    monkeypatch.setattr(
        native_storage,
        "_registry_load",
        lambda **_kwargs: [
            {"uuid": folder_uuid, "name": "media", "volumePath": "/srv", "relativePath": "media"}
        ],
    )
    monkeypatch.setattr(native_storage.os.path, "isdir", lambda _path: True)
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
    applied = native_storage.apply_smb(desired, plan["planId"])
    assert applied["applied"] is True
    assert calls and calls[0][:3] == ("net", "usershare", "add")


def test_quota_requires_zfs(monkeypatch: pytest.MonkeyPatch) -> None:
    fs_uuid = "11111111-2222-4333-8444-555555555555"
    monkeypatch.setattr(
        native_storage,
        "filesystems",
        lambda: [{"uuid": fs_uuid, "type": "ext4", "devicefile": "/dev/sda1"}],
    )
    desired = {
        "schema": "echo.omv.filesystem-quota-desired.v1",
        "filesystemUuid": fs_uuid,
        "subjectType": "user",
        "subjectName": "mother",
        "hardLimitBytes": 1024**3,
    }
    with pytest.raises(ValueError, match="ZFS"):
        native_storage.plan_quota(desired)


def test_quota_plan_on_zfs(monkeypatch: pytest.MonkeyPatch) -> None:
    fs_uuid = "11111111-2222-4333-8444-555555555555"
    monkeypatch.setattr(
        native_storage,
        "filesystems",
        lambda: [{"uuid": fs_uuid, "type": "zfs", "devicefile": "tank/share"}],
    )
    zfs_calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(native_storage, "_run_write", lambda *a, timeout=60.0: zfs_calls.append(a))
    desired = {
        "schema": "echo.omv.filesystem-quota-desired.v1",
        "filesystemUuid": fs_uuid,
        "subjectType": "user",
        "subjectName": "mother",
        "hardLimitBytes": 1024**3,
    }
    plan = native_storage.plan_quota(desired)
    assert plan["operation"] == "set"
    applied = native_storage.apply_quota(desired, plan["planId"])
    assert applied["applied"] is True
    assert zfs_calls and zfs_calls[0][:2] == ("zfs", "set")
    assert "userquota@mother" in zfs_calls[0][2]
