from __future__ import annotations

import json
import socket
import stat
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from appliance.omv_bridge import (
    OMV_CONFIGOBJECT_NEW_UUID,
    LsblkTopologyRunner,
    OmvBridgeConflict,
    OmvBridgeError,
    OmvBridgeValidationError,
    OmvCommandRunner,
    OmvEngineSecretRunner,
    OmvReadOnlyService,
    create_server,
)
from appliance.omv_client import OmvClient, OmvUnavailable

SHARE_UUID = "11111111-2222-4333-8444-555555555555"
FILESYSTEM_UUID = "22222222-3333-4444-8555-666666666666"
SMB_UUID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
NFS_UUID = "99999999-8888-4777-8666-555555555555"
NFS_MOUNT_UUID = "77777777-6666-4555-8444-333333333333"
CREATED_FOLDER_UUID = "44444444-3333-4222-8111-000000000000"


def _smb_desired(**overrides: Any) -> dict[str, Any]:
    return {
        "schema": "echo.omv.smb-share-desired.v1",
        "sharedFolderRef": SHARE_UUID,
        "enabled": True,
        "readOnly": False,
        "browseable": True,
        "recycleBin": True,
        "comment": "Family share",
        **overrides,
    }


def _shared_folder_desired(**overrides: Any) -> dict[str, Any]:
    return {
        "schema": "echo.omv.shared-folder-desired.v1",
        "mountPointRef": NFS_MOUNT_UUID,
        "name": "Photos",
        "comment": "Family photos",
        **overrides,
    }


def _share_privilege_desired(**overrides: Any) -> dict[str, Any]:
    return {
        "schema": "echo.omv.share-privilege-desired.v1",
        "sharedFolderRef": SHARE_UUID,
        "principalType": "user",
        "principalName": "alice",
        "permission": "readWrite",
        **overrides,
    }


def _quota_desired(**overrides: Any) -> dict[str, Any]:
    return {
        "schema": "echo.omv.filesystem-quota-desired.v1",
        "filesystemUuid": FILESYSTEM_UUID,
        "subjectType": "user",
        "subjectName": "alice",
        "hardLimitBytes": 10 * 1024**2,
        **overrides,
    }


def _nfs_desired(**overrides: Any) -> dict[str, Any]:
    return {
        "schema": "echo.omv.nfs-share-desired.v1",
        "sharedFolderRef": SHARE_UUID,
        "clientCidr": "192.168.1.0/24",
        "readOnly": True,
        "comment": "Family NFS",
        **overrides,
    }


def _group_desired(**overrides: Any) -> dict[str, Any]:
    return {
        "schema": "echo.omv.group-desired.v1",
        "name": "family",
        "comment": "Family members",
        **overrides,
    }


def _user_desired(**overrides: Any) -> dict[str, Any]:
    return {
        "schema": "echo.omv.user-desired.v1",
        "name": "mother",
        "displayName": "Mother",
        "password": "Echo-Family-2026!",
        "groups": ["family"],
        **overrides,
    }


def _filesystem_payload() -> list[dict[str, Any]]:
    return [
        {
            "devicefile": "/dev/sda1",
            "parentdevicefile": "/dev/sda",
            "uuid": "volume-uuid",
            "label": "Family",
            "type": "ext4",
            "mountpoint": "/srv/dev-disk-by-uuid-volume-uuid",
            "size": 1_000_000,
            "available": 750_000,
            "percentage": 25,
            "_readonly": True,
            "propposixacl": True,
            "propquota": True,
            "untrustedExtraField": "must not cross the bridge",
        }
    ]


def _topology_payload() -> dict[str, Any]:
    md_volume = {
        "name": "/dev/md0",
        "type": "raid1",
        "size": 1_900_000,
        "fstype": "LVM2_member",
        "rota": "1",
        "children": [
            {
                "name": "/dev/mapper/vg-data",
                "type": "lvm",
                "size": 1_800_000,
                "fstype": "ext4",
                "rota": "1",
                "serial": "must-not-cross-the-bridge",
            }
        ],
    }
    return {
        "blockdevices": [
            {
                "name": "/dev/sda",
                "type": "disk",
                "size": 2_000_000,
                "fstype": None,
                "rota": True,
                "children": [
                    {
                        "name": "/dev/sda1",
                        "type": "part",
                        "size": 1_900_000,
                        "fstype": "linux_raid_member",
                        "rota": True,
                        "children": [md_volume],
                    }
                ],
            },
            {
                "name": "/dev/sdb",
                "type": "disk",
                "size": 2_000_000,
                "fstype": None,
                "rota": True,
                "children": [
                    {
                        "name": "/dev/sdb1",
                        "type": "part",
                        "size": 1_900_000,
                        "fstype": "linux_raid_member",
                        "rota": True,
                        "children": [md_volume],
                    }
                ],
            },
        ]
    }


def _mdstat_payload() -> str:
    return """Personalities : [raid1]\nmd0 : active raid1 sda1[0] sdb1[1](F)\n      1900000 blocks super 1.2 [2/1] [U_]\n\nunused devices: <none>\n"""


class _RecordingRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def __call__(self, service: str, method: str, params: dict[str, Any]) -> Any:
        self.calls.append((service, method, params))
        if service == "FileSystemMgmt":
            return _filesystem_payload()
        if service == "ShareMgmt" and method == "getCandidates":
            return []
        if service == "ShareMgmt" and method == "enumerateSharedFolders":
            return [
                {
                    "uuid": SHARE_UUID,
                    "name": "Family",
                    "comment": "Family files",
                    "reldirpath": "Family/",
                    "device": "/dev/md0",
                    "status": "OK",
                    "_used": True,
                    "absdirpath": "/srv/secret/Family",
                    "mntent": {"posixacl": True, "dir": "/srv/secret"},
                }
            ]
        if service == "ShareMgmt" and method == "getPrivileges":
            return [
                {"type": "user", "id": 1000, "name": "alice", "perms": 7},
                {"type": "group", "id": 100, "name": "users", "perms": 5},
            ]
        if service == "UserMgmt" and method == "enumerateUsers":
            return [
                {
                    "name": "alice",
                    "uid": 1000,
                    "gid": 100,
                    "comment": "Alice",
                    "groups": ["users"],
                    "home": "/home/alice",
                    "sshpubkeys": ["secret-key"],
                }
            ]
        if service == "UserMgmt" and method == "enumerateGroups":
            return [{"name": "users", "gid": 100, "members": ["alice"]}]
        if service in {"SMB", "NFS"} and method == "getSettings":
            return {"enable": True, "password": "must-not-cross"}
        if service == "SMB" and method == "getShareList":
            return {
                "total": 1,
                "data": [
                    {
                        "uuid": SMB_UUID,
                        "sharedfolderref": SHARE_UUID,
                        "sharedfoldername": "Family",
                        "enable": True,
                        "readonly": False,
                        "guest": "no",
                        "browseable": True,
                        "recyclebin": False,
                        "comment": "Home share",
                        "extraoptions": "secret option",
                    }
                ],
            }
        if service == "NFS" and method == "getShareList":
            return {
                "total": 1,
                "data": [
                    {
                        "uuid": NFS_UUID,
                        "sharedfolderref": SHARE_UUID,
                        "sharedfoldername": "Family",
                        "client": "192.168.1.0/24",
                        "options": "rw,sync",
                        "extraoptions": "must-not-cross",
                    }
                ],
            }
        if service == "Smart" and method == "enumerateDevices":
            return [
                {
                    "devicename": "/dev/sda",
                    "canonicaldevicefile": "/dev/sda",
                    "devicefile": "/dev/disk/by-id/ata-Example_secret-serial",
                    "model": "Example Disk",
                    "serialnumber": "secret-serial",
                    "size": "2000000",
                    "temperature": "31",
                    "overallstatus": "GOOD",
                }
            ]
        if service == "Smart":
            return {
                "devicemodel": "Example Disk",
                "serialnumber": "secret-serial",
                "smartoverallhealthselfassessmenttestresult": "PASSED",
                "temperature": 31,
                "poweronhours": 1_234,
                "powercycles": 42,
            }
        raise AssertionError(f"unexpected RPC: {service}.{method}")


class _SharePrivilegeControlRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.permissions: dict[tuple[str, str], int | None] = {
            ("user", "alice"): None,
            ("group", "users"): 5,
        }
        self.dirty = {"samba": False, "rsyncd": False}
        self.dirty_on_set = {"samba", "rsyncd"}
        self.fail_apply_count = 0
        self.fail_set_count = 0
        self.folder_status = "OK"

    def __call__(self, service: str, method: str, params: dict[str, Any]) -> Any:
        self.calls.append((service, method, params))
        if (service, method) == ("Config", "isDirty"):
            return self.dirty[params["modules"][0]]
        if (service, method) == ("Config", "applyChanges"):
            module = params["modules"][0]
            if self.fail_apply_count:
                self.fail_apply_count -= 1
                raise OmvBridgeError("simulated privilege deployment failure")
            self.dirty[module] = False
            return [module]
        if (service, method) == ("ShareMgmt", "enumerateSharedFolders"):
            return [
                {
                    "uuid": SHARE_UUID,
                    "name": "Family",
                    "comment": "Family files",
                    "reldirpath": "Family/",
                    "device": "/dev/md0",
                    "status": self.folder_status,
                    "_used": True,
                    "mntent": {"posixacl": True},
                }
            ]
        if (service, method) == ("ShareMgmt", "getPrivileges"):
            return [
                {
                    "type": "user",
                    "id": 1000,
                    "name": "alice",
                    "perms": self.permissions[("user", "alice")],
                },
                {
                    "type": "group",
                    "id": 100,
                    "name": "users",
                    "perms": self.permissions[("group", "users")],
                },
            ]
        if (service, method) == ("ShareMgmt", "setPrivileges"):
            if self.fail_set_count:
                self.fail_set_count -= 1
                raise OmvBridgeError("simulated privilege write failure")
            configured = {
                (item["type"], item["name"]): item["perms"] for item in params["privileges"]
            }
            if not set(configured).issubset(self.permissions):
                raise OmvBridgeError("unknown privilege principal")
            for identity in self.permissions:
                self.permissions[identity] = configured.get(identity)
            for module in self.dirty_on_set:
                self.dirty[module] = True
            return None
        raise AssertionError(f"unexpected RPC: {service}.{method}")


class _SmbControlRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.share: dict[str, Any] | None = None
        self.dirty = False
        self.fail_apply_count = 0
        self.apply_result = ["samba"]

    def __call__(self, service: str, method: str, params: dict[str, Any]) -> Any:
        self.calls.append((service, method, params))
        if (service, method) == ("Config", "isDirty"):
            return self.dirty
        if (service, method) == ("Config", "applyChanges"):
            if self.fail_apply_count:
                self.fail_apply_count -= 1
                raise OmvBridgeError("simulated SMB deployment failure")
            self.dirty = False
            return list(self.apply_result)
        if (service, method) == ("SMB", "getSettings"):
            return {"enable": True}
        if (service, method) == ("ShareMgmt", "enumerateSharedFolders"):
            return [
                {
                    "uuid": SHARE_UUID,
                    "name": "Family",
                    "comment": "Family files",
                    "reldirpath": "Family/",
                    "device": "/dev/md0",
                    "status": "OK",
                    "_used": True,
                    "mntent": {"posixacl": True},
                }
            ]
        if (service, method) == ("SMB", "getShareList"):
            return {
                "total": int(self.share is not None),
                "data": [] if self.share is None else [dict(self.share)],
            }
        if (service, method) == ("SMB", "getShare"):
            if self.share is None or self.share["uuid"] != params["uuid"]:
                raise OmvBridgeError("share is unavailable")
            return dict(self.share)
        if (service, method) == ("SMB", "setShare"):
            self.share = dict(params)
            if self.share["uuid"] == OMV_CONFIGOBJECT_NEW_UUID:
                self.share["uuid"] = SMB_UUID
            self.dirty = True
            return dict(self.share)
        if (service, method) == ("SMB", "deleteShare"):
            if self.share is None or self.share["uuid"] != params["uuid"]:
                raise OmvBridgeError("share is unavailable")
            self.share = None
            self.dirty = True
            return None
        raise AssertionError(f"unexpected RPC: {service}.{method}")


class _NfsControlRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.share: dict[str, Any] | None = None
        self.dirty = False
        self.fail_apply_count = 0

    def __call__(self, service: str, method: str, params: dict[str, Any]) -> Any:
        self.calls.append((service, method, params))
        if (service, method) == ("Config", "isDirty"):
            return self.dirty
        if (service, method) == ("Config", "applyChanges"):
            if self.fail_apply_count:
                self.fail_apply_count -= 1
                raise OmvBridgeError("simulated NFS deployment failure")
            self.dirty = False
            return ["nfs"]
        if (service, method) == ("NFS", "getSettings"):
            return {"enable": True, "versions": ["4", "4.1", "4.2"]}
        if (service, method) == ("ShareMgmt", "enumerateSharedFolders"):
            return [
                {
                    "uuid": SHARE_UUID,
                    "name": "Family",
                    "comment": "Family files",
                    "reldirpath": "Family/",
                    "device": "/dev/md0",
                    "status": "OK",
                    "_used": True,
                    "mntent": {"posixacl": True},
                }
            ]
        if (service, method) == ("NFS", "getShareList"):
            return {
                "total": int(self.share is not None),
                "data": [] if self.share is None else [dict(self.share)],
            }
        if (service, method) == ("NFS", "getShare"):
            if self.share is None or self.share["uuid"] != params["uuid"]:
                raise OmvBridgeError("share is unavailable")
            return dict(self.share)
        if (service, method) == ("NFS", "setShare"):
            self.share = dict(params)
            if self.share["uuid"] == OMV_CONFIGOBJECT_NEW_UUID:
                self.share["uuid"] = NFS_UUID
                self.share["mntentref"] = NFS_MOUNT_UUID
            self.dirty = True
            return dict(self.share)
        if (service, method) == ("NFS", "deleteShare"):
            if self.share is None or self.share["uuid"] != params["uuid"]:
                raise OmvBridgeError("share is unavailable")
            self.share = None
            self.dirty = True
            return None
        raise AssertionError(f"unexpected RPC: {service}.{method}")


class _QuotaControlRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.dirty = False
        self.limit_kib = 0
        self.fail_apply_count = 0
        self.read_only = False
        self.supports_quota = True

    def __call__(self, service: str, method: str, params: dict[str, Any]) -> Any:
        self.calls.append((service, method, params))
        if (service, method) == ("Config", "isDirty"):
            return self.dirty
        if (service, method) == ("Config", "applyChanges"):
            if self.fail_apply_count:
                self.fail_apply_count -= 1
                raise OmvBridgeError("simulated quota deployment failure")
            self.dirty = False
            return ["quota"]
        if (service, method) == ("FileSystemMgmt", "enumerateMountedFilesystems"):
            return [
                {
                    "devicefile": "/dev/sda1",
                    "parentdevicefile": "/dev/sda",
                    "uuid": FILESYSTEM_UUID,
                    "label": "Family",
                    "type": "ext4",
                    "mountpoint": "/srv/dev-disk-by-uuid-family",
                    "size": 100 * 1024**3,
                    "available": 80 * 1024**3,
                    "percentage": 20,
                    "_readonly": self.read_only,
                    "propposixacl": True,
                    "propquota": self.supports_quota,
                }
            ]
        if (service, method) == ("Quota", "get"):
            return [
                {
                    "type": "user",
                    "name": "alice",
                    "bused": "4 MiB",
                    "bhardlimit": self.limit_kib,
                    "bunit": "KiB",
                },
                {
                    "type": "group",
                    "name": "users",
                    "bused": "8 MiB",
                    "bhardlimit": 0,
                    "bunit": "MiB",
                },
            ]
        if (service, method) == ("Quota", "setByTypeName"):
            assert params["uuid"] == FILESYSTEM_UUID
            assert params["type"] == "user"
            assert params["name"] == "alice"
            assert params["bunit"] == "KiB"
            self.limit_kib = params["bhardlimit"]
            self.dirty = True
            return {"fsuuid": FILESYSTEM_UUID}
        raise AssertionError(f"unexpected RPC: {service}.{method}")


class _SharedFolderControlRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.share: dict[str, Any] | None = None
        self.fail_get_count = 0
        self.fail_delete = False
        self.read_only = False

    def __call__(self, service: str, method: str, params: dict[str, Any]) -> Any:
        self.calls.append((service, method, params))
        if (service, method) == ("FileSystemMgmt", "enumerateMountedFilesystems"):
            return [
                {
                    "devicefile": "/dev/sda1",
                    "parentdevicefile": "/dev/sda",
                    "uuid": FILESYSTEM_UUID,
                    "label": "Family",
                    "type": "ext4",
                    "mountpoint": "/srv/dev-disk-by-uuid-family",
                    "size": 100 * 1024**3,
                    "available": 80 * 1024**3,
                    "percentage": 20,
                    "_readonly": self.read_only,
                    "propposixacl": True,
                    "propquota": True,
                }
            ]
        if (service, method) == ("ShareMgmt", "getCandidates"):
            return [{"uuid": NFS_MOUNT_UUID, "description": "secret raw description"}]
        if (service, method) in {
            ("UserMgmt", "enumerateUsers"),
            ("UserMgmt", "enumerateGroups"),
        }:
            return []
        if service in {"SMB", "NFS"} and method == "getSettings":
            return {"enable": False}
        if service in {"SMB", "NFS"} and method == "getShareList":
            return {"total": 0, "data": []}
        if (service, method) == ("FsTab", "get"):
            assert params == {"uuid": NFS_MOUNT_UUID}
            return {
                "uuid": NFS_MOUNT_UUID,
                "fsname": "/dev/disk/by-uuid/secret",
                "dir": "/srv/dev-disk-by-uuid-family",
                "type": "ext4",
                "opts": "defaults",
            }
        if (service, method) == ("ShareMgmt", "enumerateSharedFolders"):
            if self.share is None:
                return []
            return [
                {
                    **self.share,
                    "status": "OK",
                    "device": "/dev/sda1",
                    "_used": False,
                    "mntent": {"posixacl": True},
                }
            ]
        if (service, method) == ("ShareMgmt", "set"):
            assert params == {
                "uuid": OMV_CONFIGOBJECT_NEW_UUID,
                "name": "Photos",
                "reldirpath": "Photos",
                "comment": "Family photos",
                "mntentref": NFS_MOUNT_UUID,
                "mode": "770",
            }
            self.share = {
                "uuid": CREATED_FOLDER_UUID,
                "name": params["name"],
                "reldirpath": f"{params['reldirpath']}/",
                "comment": params["comment"],
                "mntentref": params["mntentref"],
            }
            return dict(self.share)
        if (service, method) == ("ShareMgmt", "get"):
            if self.fail_get_count:
                self.fail_get_count -= 1
                raise OmvBridgeError("simulated shared folder readback failure")
            if self.share is None or self.share["uuid"] != params["uuid"]:
                raise OmvBridgeError("shared folder is unavailable")
            return {**self.share, "mountpoint": "/srv/dev-disk-by-uuid-family"}
        if (service, method) == ("ShareMgmt", "delete"):
            if self.fail_delete:
                raise OmvBridgeError("simulated shared folder rollback failure")
            assert params == {"uuid": CREATED_FOLDER_UUID, "recursive": False}
            self.share = None
            return {"uuid": CREATED_FOLDER_UUID}
        raise AssertionError(f"unexpected RPC: {service}.{method}")


class _AccountControlRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.groups: dict[str, dict[str, Any]] = {}
        self.users: dict[str, dict[str, Any]] = {}
        self.home_directories_enabled = False
        self.corrupt_group_readback = False
        self.corrupt_user_readback = False

    def __call__(self, service: str, method: str, params: dict[str, Any]) -> Any:
        self.calls.append((service, method, params))
        if (service, method) == ("UserMgmt", "getSettings"):
            return {"enable": self.home_directories_enabled}
        if (service, method) == ("UserMgmt", "enumerateGroups"):
            return [dict(group) for group in self.groups.values()]
        if (service, method) == ("UserMgmt", "enumerateUsers"):
            return [dict(user) for user in self.users.values()]
        if (service, method) == ("UserMgmt", "setGroup"):
            self.groups[params["name"]] = {
                "name": params["name"],
                "gid": 1000 + len(self.groups),
                "comment": params["comment"],
                "members": list(params["members"]),
            }
            return dict(self.groups[params["name"]])
        if (service, method) == ("UserMgmt", "getGroup"):
            group = dict(self.groups[params["name"]])
            if self.corrupt_group_readback:
                group["comment"] = "unexpected"
            return group
        if (service, method) == ("UserMgmt", "deleteGroup"):
            self.groups.pop(params["name"], None)
            return None
        if (service, method) == ("UserMgmt", "getUser"):
            return dict(self.users[params["name"]])
        if (service, method) == ("UserMgmt", "deleteUser"):
            self.users.pop(params["name"], None)
            return None
        raise AssertionError(f"unexpected RPC: {service}.{method}")

    def create_user(self, params: dict[str, Any]) -> None:
        self.users[params["name"]] = {
            "name": params["name"],
            "uid": 2000 + len(self.users),
            "gid": 100,
            "comment": params["comment"],
            "shell": ("/bin/bash" if self.corrupt_user_readback else params["shell"]),
            "groups": ["users", *params["groups"]],
            "email": params["email"],
            "disallowusermod": params["disallowusermod"],
            "sshpubkeys": list(params["sshpubkeys"]),
        }


def test_group_control_creates_verifies_and_rejects_stale_plans() -> None:
    runner = _AccountControlRunner()
    service = OmvReadOnlyService(runner, plan_secret=b"g" * 32)

    plan = service.plan_group(_group_desired())
    assert plan["operation"] == "create"
    assert plan["requiresApproval"] is True
    assert plan["safety"]["initialMembers"] == "empty"

    applied = service.apply_group(_group_desired(), plan["planId"])
    assert applied["applied"] is True
    assert applied["verified"] is True
    assert runner.groups["family"]["members"] == []

    other_runner = _AccountControlRunner()
    other_service = OmvReadOnlyService(other_runner, plan_secret=b"g" * 32)
    stale_plan = other_service.plan_group(_group_desired())
    other_runner.groups["media"] = {
        "name": "media",
        "gid": 1001,
        "comment": "Media",
        "members": [],
    }
    with pytest.raises(OmvBridgeConflict, match="stale"):
        other_service.apply_group(_group_desired(), stale_plan["planId"])
    assert "family" not in other_runner.groups


def test_group_control_rolls_back_failed_verification_and_rejects_reserved_names() -> None:
    runner = _AccountControlRunner()
    runner.corrupt_group_readback = True
    service = OmvReadOnlyService(runner)
    plan = service.plan_group(_group_desired())

    with pytest.raises(OmvBridgeError, match="rolled back"):
        service.apply_group(_group_desired(), plan["planId"])
    assert "family" not in runner.groups

    for name in ("root", "users", "echo-system", "Family"):
        with pytest.raises(OmvBridgeValidationError):
            service.plan_group(_group_desired(name=name))


def test_user_control_binds_password_without_returning_it_and_verifies_creation() -> None:
    runner = _AccountControlRunner()
    runner.groups["family"] = {
        "name": "family",
        "gid": 1000,
        "comment": "Family",
        "members": [],
    }
    captured: list[dict[str, Any]] = []

    def secret_runner(params: dict[str, Any]) -> None:
        captured.append(dict(params))
        runner.create_user(params)

    service = OmvReadOnlyService(
        runner,
        secret_runner=secret_runner,
        plan_secret=b"u" * 32,
    )
    desired = _user_desired()
    plan = service.plan_user(desired)
    serialized = json.dumps(plan, sort_keys=True)

    assert desired["password"] not in serialized
    assert "password" not in plan["desired"]
    assert plan["desired"]["passwordBound"] is True
    assert (
        service.plan_user(_user_desired(password="Another-Family-2026!"))["planId"]
        != plan["planId"]
    )

    applied = service.apply_user(desired, plan["planId"])
    assert applied["applied"] is True
    assert applied["verified"] is True
    assert desired["password"] not in json.dumps(applied, sort_keys=True)
    assert captured == [
        {
            "name": "mother",
            "groups": ["family"],
            "shell": "/usr/sbin/nologin",
            "password": "Echo-Family-2026!",
            "email": "",
            "comment": "Mother",
            "disallowusermod": True,
            "sshpubkeys": [],
        }
    ]


def test_user_control_rejects_unsafe_preconditions_and_rolls_back_mismatch() -> None:
    runner = _AccountControlRunner()
    runner.groups["family"] = {
        "name": "family",
        "gid": 1000,
        "comment": "Family",
        "members": [],
    }
    service = OmvReadOnlyService(runner, secret_runner=runner.create_user)

    runner.home_directories_enabled = True
    with pytest.raises(OmvBridgeConflict, match="home directories"):
        service.plan_user(_user_desired())
    runner.home_directories_enabled = False

    with pytest.raises(OmvBridgeValidationError, match="do not exist"):
        service.plan_user(_user_desired(groups=["missing"]))
    for password in ("short", "abcdefghijkl", "mother"):
        with pytest.raises(OmvBridgeValidationError, match="password"):
            service.plan_user(_user_desired(password=password))

    plan = service.plan_user(_user_desired())
    runner.corrupt_user_readback = True
    with pytest.raises(OmvBridgeError, match="rolled back"):
        service.apply_user(_user_desired(), plan["planId"])
    assert "mother" not in runner.users


def test_user_password_reset_binds_secret_and_preserves_constrained_account() -> None:
    runner = _AccountControlRunner()
    runner.groups["family"] = {
        "name": "family",
        "gid": 1000,
        "comment": "Family",
        "members": ["mother"],
    }
    runner.create_user(
        {
            "name": "mother",
            "groups": ["family"],
            "shell": "/usr/sbin/nologin",
            "password": "Original-Family-2026!",
            "email": "",
            "comment": "Mother",
            "disallowusermod": True,
            "sshpubkeys": [],
        }
    )
    captured: list[dict[str, Any]] = []
    service = OmvReadOnlyService(
        runner,
        secret_runner=lambda params: captured.append(dict(params)),
        plan_secret=b"p" * 32,
    )
    desired = {
        "schema": "echo.omv.user-password-desired.v1",
        "name": "mother",
        "password": "Replacement-Family-2026!",
    }

    plan = service.plan_user_password(desired)
    assert plan["operation"] == "resetPassword"
    assert plan["desired"] == {
        "schema": "echo.omv.user-password-desired.v1",
        "name": "mother",
        "passwordBound": True,
    }
    assert desired["password"] not in json.dumps(plan, sort_keys=True)
    assert (
        service.plan_user_password({**desired, "password": "Another-Family-2026!"})["planId"]
        != plan["planId"]
    )

    result = service.apply_user_password(desired, plan["planId"])
    assert result["applied"] is True
    assert result["verified"] is True
    assert desired["password"] not in json.dumps(result, sort_keys=True)
    assert captured == [
        {
            "name": "mother",
            "groups": ["family"],
            "shell": "/usr/sbin/nologin",
            "password": "Replacement-Family-2026!",
            "email": "",
            "comment": "Mother",
            "disallowusermod": True,
            "sshpubkeys": [],
        }
    ]


def test_user_password_reset_rejects_unconstrained_or_missing_accounts() -> None:
    runner = _AccountControlRunner()
    service = OmvReadOnlyService(runner, secret_runner=lambda _params: None)
    desired = {
        "schema": "echo.omv.user-password-desired.v1",
        "name": "mother",
        "password": "Replacement-Family-2026!",
    }

    with pytest.raises(OmvBridgeConflict, match="does not exist"):
        service.plan_user_password(desired)
    runner.users["mother"] = {
        "name": "mother",
        "uid": 2000,
        "gid": 100,
        "comment": "Mother",
        "shell": "/bin/bash",
        "groups": ["users"],
        "email": "",
        "disallowusermod": True,
        "sshpubkeys": [],
    }
    with pytest.raises(OmvBridgeConflict, match="not an Echo-constrained"):
        service.plan_user_password(desired)


def test_engine_secret_runner_uses_nul_delimited_socket_and_discards_password() -> None:
    # macOS limits AF_UNIX paths to roughly 104 bytes; pytest's tmp_path is
    # deliberately descriptive and can exceed that.
    with tempfile.TemporaryDirectory(prefix="echo-engined-", dir="/tmp") as directory:
        socket_path = Path(directory) / "e.sock"
        _exercise_engine_secret_runner(socket_path)


def _exercise_engine_secret_runner(socket_path: Path) -> None:
    received: list[dict[str, Any]] = []
    ready = threading.Event()

    def serve_once() -> None:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(socket_path))
            listener.listen(1)
            ready.set()
            connection, _ = listener.accept()
            with connection:
                request = bytearray()
                while not request.endswith(b"\0"):
                    request.extend(connection.recv(4096))
                received.append(json.loads(bytes(request[:-1])))
                connection.sendall(b'{"response":{"password":"Echo-Family-2026!"},"error":null}\0')

    thread = threading.Thread(target=serve_once, daemon=True)
    thread.start()
    assert ready.wait(timeout=2)
    runner = OmvEngineSecretRunner(socket_path)
    params = {
        "name": "mother",
        "groups": ["family"],
        "shell": "/usr/sbin/nologin",
        "password": "Echo-Family-2026!",
        "email": "",
        "comment": "Mother",
        "disallowusermod": True,
        "sshpubkeys": [],
    }

    assert runner(params) is None
    thread.join(timeout=2)
    assert received == [
        {
            "service": "UserMgmt",
            "method": "setUser",
            "params": params,
            "context": {"username": "admin", "role": 1},
        }
    ]


def test_unix_socket_account_control_round_trip_never_returns_password() -> None:
    with tempfile.TemporaryDirectory(prefix="echo-omv-account-", dir="/tmp") as directory:
        socket_path = Path(directory) / "omv.sock"
        runner = _AccountControlRunner()
        captured: list[dict[str, Any]] = []

        def secret_runner(params: dict[str, Any]) -> None:
            captured.append(dict(params))
            runner.create_user(params)

        server = create_server(
            socket_path,
            OmvReadOnlyService(
                runner,
                secret_runner=secret_runner,
                plan_secret=b"a" * 32,
            ),
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = OmvClient(str(socket_path))
            assert client.supports_group_control() is True
            assert client.supports_user_control() is True

            group_plan = client.plan_group(_group_desired())
            group_result = client.apply_group(_group_desired(), group_plan["planId"])
            user_plan = client.plan_user(_user_desired())
            user_result = client.apply_user(_user_desired(), user_plan["planId"])

            assert group_result["verified"] is True
            assert user_result["verified"] is True
            assert "Echo-Family-2026!" not in json.dumps(
                {"plan": user_plan, "result": user_result},
                sort_keys=True,
            )
            assert captured[0]["password"] == "Echo-Family-2026!"
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


def test_bridge_uses_only_allowlisted_read_rpc_and_sanitizes_output() -> None:
    runner = _RecordingRunner()
    service = OmvReadOnlyService(runner, _topology_payload, _mdstat_payload)

    filesystems = service.filesystems()
    devices = service.smart_devices()
    smart = service.smart("/dev/sda")
    topology = service.storage_topology()
    sharing = service.sharing_overview()
    privileges = service.share_privileges(SHARE_UUID)

    assert runner.calls == [
        (
            "FileSystemMgmt",
            "enumerateMountedFilesystems",
            {"includeroot": False},
        ),
        ("Smart", "enumerateDevices", {}),
        ("Smart", "enumerateDevices", {}),
        (
            "FileSystemMgmt",
            "enumerateMountedFilesystems",
            {"includeroot": False},
        ),
        ("Smart", "getInformation", {"devicefile": "/dev/sda"}),
        ("ShareMgmt", "enumerateSharedFolders", {}),
        ("UserMgmt", "enumerateUsers", {"detail": "basic"}),
        ("UserMgmt", "enumerateGroups", {}),
        ("SMB", "getSettings", {}),
        ("NFS", "getSettings", {}),
        (
            "SMB",
            "getShareList",
            {
                "start": 0,
                "limit": -1,
                "sortfield": "sharedfoldername",
                "sortdir": "ASC",
            },
        ),
        (
            "NFS",
            "getShareList",
            {
                "start": 0,
                "limit": -1,
                "sortfield": "sharedfoldername",
                "sortdir": "ASC",
            },
        ),
        ("ShareMgmt", "getCandidates", {}),
        (
            "FileSystemMgmt",
            "enumerateMountedFilesystems",
            {"includeroot": False},
        ),
        ("ShareMgmt", "enumerateSharedFolders", {}),
        ("ShareMgmt", "getPrivileges", {"uuid": SHARE_UUID}),
    ]
    assert filesystems == [
        {
            "devicefile": "/dev/sda1",
            "parentdevicefile": "/dev/sda",
            "uuid": "volume-uuid",
            "label": "Family",
            "type": "ext4",
            "mountpoint": "/srv/dev-disk-by-uuid-volume-uuid",
            "sizeBytes": 1_000_000,
            "availableBytes": 750_000,
            "usedPercent": 25,
            "readOnly": True,
            "supportsAcl": True,
            "supportsQuota": True,
        }
    ]
    assert devices == [
        {
            "devicefile": "/dev/sda",
            "model": "Example Disk",
            "sizeBytes": 2_000_000,
            "health": "GOOD",
            "temperatureC": 31,
        }
    ]
    assert "secret-serial" not in json.dumps(devices).lower()
    assert smart == {
        "devicefile": "/dev/sda",
        "model": "Example Disk",
        "health": "PASSED",
        "temperatureC": 31,
        "powerOnHours": 1_234,
        "powerCycles": 42,
    }
    assert "serial" not in json.dumps(smart).lower()
    assert topology["devices"][-2:] == [
        {
            "devicefile": "/dev/sdb",
            "type": "disk",
            "sizeBytes": 2_000_000,
            "filesystemType": None,
            "rotational": True,
            "parentDevicefiles": [],
        },
        {
            "devicefile": "/dev/sdb1",
            "type": "part",
            "sizeBytes": 1_900_000,
            "filesystemType": "linux_raid_member",
            "rotational": True,
            "parentDevicefiles": ["/dev/sdb"],
        },
    ]
    assert next(node for node in topology["devices"] if node["devicefile"] == "/dev/md0")[
        "parentDevicefiles"
    ] == ["/dev/sda1", "/dev/sdb1"]
    assert topology["arrays"] == [
        {
            "devicefile": "/dev/md0",
            "level": "raid1",
            "status": "degraded",
            "totalDevices": 2,
            "activeDevices": 1,
            "operation": None,
            "operationPercent": None,
        }
    ]
    assert "must-not-cross" not in json.dumps(topology).lower()
    assert sharing["sharedFolders"][0] == {
        "uuid": SHARE_UUID,
        "name": "Family",
        "comment": "Family files",
        "relativePath": "Family/",
        "device": "/dev/md0",
        "status": "OK",
        "inUse": True,
        "supportsAcl": True,
    }
    assert sharing["sharedFolderTargets"] == []
    assert sharing["users"] == [
        {
            "name": "alice",
            "uid": 1000,
            "gid": 100,
            "comment": "Alice",
            "groups": ["users"],
        }
    ]
    assert sharing["smb"]["shares"][0]["sharedFolderName"] == "Family"
    assert sharing["smb"]["shares"][0]["recycleBin"] is False
    assert sharing["nfs"]["shares"][0]["client"] == "192.168.1.0/24"
    assert privileges == [
        {"type": "user", "id": 1000, "name": "alice", "permission": "readWrite"},
        {"type": "group", "id": 100, "name": "users", "permission": "read"},
    ]
    assert "secret" not in json.dumps(sharing).lower()


def test_shared_folder_control_creates_verifies_and_becomes_idempotent() -> None:
    runner = _SharedFolderControlRunner()
    service = OmvReadOnlyService(runner)

    plan = service.plan_shared_folder(_shared_folder_desired())
    applied = service.apply_shared_folder(_shared_folder_desired(), plan["planId"])
    repeated = service.plan_shared_folder(_shared_folder_desired())

    assert plan["operation"] == "create"
    assert plan["requiresApproval"] is True
    assert plan["target"] == {
        "mountPointRef": NFS_MOUNT_UUID,
        "filesystemUuid": FILESYSTEM_UUID,
        "label": "Family",
        "type": "ext4",
        "sizeBytes": 100 * 1024**3,
        "availableBytes": 80 * 1024**3,
        "readOnly": False,
    }
    assert plan["safety"] == {
        "filesystem": "existingMountedWritableOnly",
        "relativePath": "derivedFromPortableName",
        "directoryMode": "2770UsersGroup",
        "acl": "notManaged",
        "update": "notManaged",
        "delete": "notManaged",
    }
    assert applied["shareUuid"] == CREATED_FOLDER_UUID
    assert applied["applied"] is True
    assert applied["verified"] is True
    assert repeated["operation"] == "none"
    assert repeated["requiresApproval"] is False
    assert repeated["changes"] == []


@pytest.mark.parametrize("name", ["../escape", "Family Photos", "CON", "name.", "a..b", "a" * 65])
def test_shared_folder_control_rejects_non_portable_names(name: str) -> None:
    runner = _SharedFolderControlRunner()

    with pytest.raises(OmvBridgeValidationError, match="shared folder name"):
        OmvReadOnlyService(runner).plan_shared_folder(_shared_folder_desired(name=name))

    assert runner.calls == []


def test_shared_folder_control_rejects_unknown_read_only_or_conflicting_targets() -> None:
    runner = _SharedFolderControlRunner()
    service = OmvReadOnlyService(runner)

    with pytest.raises(OmvBridgeConflict, match="mounted writable"):
        service.plan_shared_folder(
            _shared_folder_desired(mountPointRef="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")
        )

    runner.read_only = True
    with pytest.raises(OmvBridgeConflict, match="mounted writable"):
        service.plan_shared_folder(_shared_folder_desired())
    runner.read_only = False

    runner.share = {
        "uuid": CREATED_FOLDER_UUID,
        "name": "Photos",
        "reldirpath": "Photos/",
        "comment": "different purpose",
        "mntentref": NFS_MOUNT_UUID,
    }
    with pytest.raises(OmvBridgeConflict, match="different settings"):
        service.plan_shared_folder(_shared_folder_desired())


def test_shared_folder_control_rolls_back_config_without_deleting_directory_contents() -> None:
    runner = _SharedFolderControlRunner()
    runner.fail_get_count = 1
    service = OmvReadOnlyService(runner)
    plan = service.plan_shared_folder(_shared_folder_desired())

    with pytest.raises(OmvBridgeError, match="readback failure"):
        service.apply_shared_folder(_shared_folder_desired(), plan["planId"])

    assert runner.share is None
    assert (
        "ShareMgmt",
        "delete",
        {"uuid": CREATED_FOLDER_UUID, "recursive": False},
    ) in runner.calls


def test_shared_folder_control_reports_failed_rollback_as_critical() -> None:
    runner = _SharedFolderControlRunner()
    runner.fail_get_count = 1
    runner.fail_delete = True
    service = OmvReadOnlyService(runner)
    plan = service.plan_shared_folder(_shared_folder_desired())

    with pytest.raises(OmvBridgeError, match="rollback also failed"):
        service.apply_shared_folder(_shared_folder_desired(), plan["planId"])


def test_share_privilege_control_plans_applies_deploys_and_becomes_idempotent() -> None:
    runner = _SharePrivilegeControlRunner()
    service = OmvReadOnlyService(runner)

    plan = service.plan_share_privilege(_share_privilege_desired())

    assert plan["operation"] == "update"
    assert plan["requiresApproval"] is True
    assert plan["principal"] == {
        "type": "user",
        "id": 1000,
        "name": "alice",
        "before": "inherit",
        "after": "readWrite",
    }
    assert plan["changes"] == [{"field": "permission", "before": "inherit", "after": "readWrite"}]
    assert plan["safety"] == {
        "scope": "sharedFolderConfigPrivilege",
        "principal": "existingOmvUserOrGroup",
        "filesystemAcl": "notModified",
        "recursive": "never",
        "serviceDeploy": "sambaAndRsyncdWhenDirty",
        "delete": "notManaged",
    }

    applied = service.apply_share_privilege(_share_privilege_desired(), plan["planId"])

    assert applied["applied"] is True
    assert applied["verified"] is True
    assert applied["deployedServices"] == ["samba", "rsyncd"]
    assert runner.permissions == {
        ("user", "alice"): 7,
        ("group", "users"): 5,
    }
    set_call = next(
        params
        for service_name, method, params in runner.calls
        if (service_name, method) == ("ShareMgmt", "setPrivileges")
    )
    assert set_call == {
        "uuid": SHARE_UUID,
        "privileges": [
            {"type": "user", "name": "alice", "perms": 7},
            {"type": "group", "name": "users", "perms": 5},
        ],
    }

    repeated = service.plan_share_privilege(_share_privilege_desired())
    assert repeated["operation"] == "none"
    assert repeated["requiresApproval"] is False
    assert repeated["changes"] == []


def test_share_privilege_inherit_removes_only_the_selected_principal() -> None:
    runner = _SharePrivilegeControlRunner()
    runner.permissions[("user", "alice")] = 7
    service = OmvReadOnlyService(runner)
    desired = _share_privilege_desired(permission="inherit")
    plan = service.plan_share_privilege(desired)

    applied = service.apply_share_privilege(desired, plan["planId"])

    assert applied["verified"] is True
    assert runner.permissions == {
        ("user", "alice"): None,
        ("group", "users"): 5,
    }
    set_calls = [
        params
        for service_name, method, params in runner.calls
        if (service_name, method) == ("ShareMgmt", "setPrivileges")
    ]
    assert set_calls[0]["privileges"] == [{"type": "group", "name": "users", "perms": 5}]


def test_share_privilege_control_rejects_unknown_principal_offline_folder_and_dirty_config() -> (
    None
):
    runner = _SharePrivilegeControlRunner()
    service = OmvReadOnlyService(runner)

    with pytest.raises(OmvBridgeValidationError, match="not enumerated"):
        service.plan_share_privilege(_share_privilege_desired(principalName="mallory"))

    runner.folder_status = "MISSING"
    with pytest.raises(OmvBridgeConflict, match="not online"):
        service.plan_share_privilege(_share_privilege_desired())

    runner.folder_status = "OK"
    runner.dirty["rsyncd"] = True
    with pytest.raises(OmvBridgeConflict, match="unapplied rsyncd"):
        service.plan_share_privilege(_share_privilege_desired())


def test_share_privilege_control_rejects_stale_plan_and_rolls_back_deployment_failure() -> None:
    runner = _SharePrivilegeControlRunner()
    service = OmvReadOnlyService(runner)
    plan = service.plan_share_privilege(_share_privilege_desired())

    runner.permissions[("group", "users")] = 0
    with pytest.raises(OmvBridgeConflict, match="stale"):
        service.apply_share_privilege(_share_privilege_desired(), plan["planId"])

    runner.permissions[("group", "users")] = 5
    plan = service.plan_share_privilege(_share_privilege_desired())
    runner.fail_apply_count = 1
    with pytest.raises(OmvBridgeError, match="deployment failure"):
        service.apply_share_privilege(_share_privilege_desired(), plan["planId"])

    assert runner.permissions == {
        ("user", "alice"): None,
        ("group", "users"): 5,
    }
    assert runner.dirty == {"samba": False, "rsyncd": False}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("principalType", "everyone"),
        ("principalName", "alice\nroot"),
        ("principalName", " alice"),
        ("permission", "admin"),
        ("sharedFolderRef", "not-a-uuid"),
    ],
)
def test_share_privilege_control_rejects_invalid_desired_state_before_rpc(
    field: str,
    value: Any,
) -> None:
    runner = _SharePrivilegeControlRunner()

    with pytest.raises(OmvBridgeValidationError, match="share privilege"):
        OmvReadOnlyService(runner).plan_share_privilege(_share_privilege_desired(**{field: value}))

    assert runner.calls == []


def test_smb_control_plans_applies_verifies_and_becomes_idempotent() -> None:
    runner = _SmbControlRunner()
    service = OmvReadOnlyService(runner)

    first = service.plan_smb_share(_smb_desired())
    repeated = service.plan_smb_share(_smb_desired())

    assert first == repeated
    assert first["operation"] == "create"
    assert first["requiresApproval"] is True
    assert [change["field"] for change in first["changes"]] == [
        "enabled",
        "readOnly",
        "browseable",
        "recycleBin",
        "comment",
    ]
    assert runner.share is None

    applied = service.apply_smb_share(_smb_desired(), first["planId"])

    assert applied["applied"] is True
    assert applied["verified"] is True
    assert applied["shareUuid"] == SMB_UUID
    assert runner.share is not None
    assert runner.share["guest"] == "no"
    assert runner.share["hostsallow"] == ""
    assert runner.share["hostsdeny"] == ""
    assert runner.share["extraoptions"] == ""
    assert runner.share["sharedfolderref"] == SHARE_UUID
    create_call = next(
        params
        for service_name, method, params in runner.calls
        if (service_name, method) == ("SMB", "setShare")
    )
    assert create_call["uuid"] == OMV_CONFIGOBJECT_NEW_UUID

    no_change = service.plan_smb_share(_smb_desired())
    no_op = service.apply_smb_share(_smb_desired(), no_change["planId"])

    assert no_change["operation"] == "none"
    assert no_change["requiresApproval"] is False
    assert no_op["applied"] is False
    assert no_op["verified"] is True


def test_smb_control_rejects_stale_or_dirty_plans_before_mutation() -> None:
    runner = _SmbControlRunner()
    service = OmvReadOnlyService(runner)
    plan = service.plan_smb_share(_smb_desired())

    with pytest.raises(OmvBridgeConflict, match="stale"):
        service.apply_smb_share(_smb_desired(comment="changed"), plan["planId"])
    assert runner.share is None

    runner.dirty = True
    with pytest.raises(OmvBridgeConflict, match="unapplied"):
        service.plan_smb_share(_smb_desired())
    assert runner.share is None


def test_smb_control_rolls_back_a_failed_create_deployment() -> None:
    runner = _SmbControlRunner()
    service = OmvReadOnlyService(runner)
    plan = service.plan_smb_share(_smb_desired())
    runner.fail_apply_count = 1

    with pytest.raises(OmvBridgeError, match="deployment failure"):
        service.apply_smb_share(_smb_desired(), plan["planId"])

    assert runner.share is None
    assert runner.dirty is False
    assert ("SMB", "deleteShare", {"uuid": SMB_UUID}) in runner.calls


def test_smb_control_requires_confirmation_that_samba_was_deployed() -> None:
    runner = _SmbControlRunner()
    runner.apply_result = []

    with pytest.raises(OmvBridgeError, match="did not deploy the Samba configuration"):
        OmvReadOnlyService(runner)._apply_smb_config()

    assert runner.calls == [("Config", "applyChanges", {"modules": ["samba"], "force": False})]


def test_nfs_control_plans_applies_verifies_and_becomes_idempotent() -> None:
    runner = _NfsControlRunner()
    service = OmvReadOnlyService(runner)

    first = service.plan_nfs_share(_nfs_desired())
    assert first["operation"] == "create"
    assert first["requiresApproval"] is True
    assert first["safety"] == {
        "clientScope": "privateCidrOnly",
        "rootSquash": "required",
        "syncWrites": "required",
        "advancedOptions": "notManaged",
        "delete": "notManaged",
    }

    applied = service.apply_nfs_share(_nfs_desired(), first["planId"])

    assert applied["shareUuid"] == NFS_UUID
    assert applied["applied"] is True
    assert applied["verified"] is True
    assert runner.share == {
        "uuid": NFS_UUID,
        "sharedfolderref": SHARE_UUID,
        "mntentref": NFS_MOUNT_UUID,
        "client": "192.168.1.0/24",
        "options": "ro",
        "extraoptions": "sync,subtree_check,root_squash",
        "comment": "Family NFS",
    }
    no_change = service.plan_nfs_share(_nfs_desired())
    no_op = service.apply_nfs_share(_nfs_desired(), no_change["planId"])
    assert no_change["operation"] == "none"
    assert no_op["applied"] is False
    assert no_op["verified"] is True


def test_nfs_control_rejects_non_private_or_advanced_existing_rules() -> None:
    service = OmvReadOnlyService(_NfsControlRunner())
    for client in ("*", "8.8.8.0/24", "192.168.1.7/24"):
        with pytest.raises(OmvBridgeValidationError, match="NFS client"):
            service.plan_nfs_share(_nfs_desired(clientCidr=client))

    runner = _NfsControlRunner()
    runner.share = {
        "uuid": NFS_UUID,
        "sharedfolderref": SHARE_UUID,
        "mntentref": NFS_MOUNT_UUID,
        "client": "192.168.1.0/24",
        "options": "rw",
        "extraoptions": "no_root_squash,insecure",
        "comment": "advanced",
    }
    with pytest.raises(OmvBridgeConflict, match="advanced options"):
        OmvReadOnlyService(runner).plan_nfs_share(_nfs_desired())


def test_nfs_control_rolls_back_a_failed_create_deployment() -> None:
    runner = _NfsControlRunner()
    service = OmvReadOnlyService(runner)
    plan = service.plan_nfs_share(_nfs_desired())
    runner.fail_apply_count = 1

    with pytest.raises(OmvBridgeError, match="deployment failure"):
        service.apply_nfs_share(_nfs_desired(), plan["planId"])

    assert runner.share is None
    assert runner.dirty is False
    assert ("NFS", "deleteShare", {"uuid": NFS_UUID}) in runner.calls


def test_quota_control_plans_applies_verifies_and_becomes_idempotent() -> None:
    runner = _QuotaControlRunner()
    service = OmvReadOnlyService(runner)

    first = service.plan_filesystem_quota(_quota_desired())
    repeated = service.plan_filesystem_quota(_quota_desired())

    assert first == repeated
    assert first["operation"] == "update"
    assert first["requiresApproval"] is True
    assert first["subject"] == {
        "type": "user",
        "name": "alice",
        "hardLimitBytes": 0,
        "used": "4 MiB",
    }
    assert first["safety"] == {
        "scope": "filesystemUserOrGroup",
        "protocolCoverage": ["local", "SMB", "NFS"],
        "sharedFolderQuota": "notSupportedByOmvQuotaRpc",
        "minimumUnitBytes": 1024,
    }
    assert runner.limit_kib == 0

    applied = service.apply_filesystem_quota(_quota_desired(), first["planId"])

    assert applied["applied"] is True
    assert applied["verified"] is True
    assert runner.limit_kib == 10 * 1024
    no_change = service.plan_filesystem_quota(_quota_desired())
    no_op = service.apply_filesystem_quota(_quota_desired(), no_change["planId"])
    assert no_change["operation"] == "none"
    assert no_op["applied"] is False
    assert no_op["verified"] is True


def test_quota_control_rejects_stale_dirty_unsupported_and_unknown_targets() -> None:
    runner = _QuotaControlRunner()
    service = OmvReadOnlyService(runner)
    plan = service.plan_filesystem_quota(_quota_desired())

    runner.limit_kib = 1024
    with pytest.raises(OmvBridgeConflict, match="stale"):
        service.apply_filesystem_quota(_quota_desired(), plan["planId"])

    runner.dirty = True
    with pytest.raises(OmvBridgeConflict, match="unapplied"):
        service.plan_filesystem_quota(_quota_desired())
    runner.dirty = False

    runner.supports_quota = False
    with pytest.raises(OmvBridgeConflict, match="not supported"):
        service.plan_filesystem_quota(_quota_desired())
    runner.supports_quota = True
    runner.read_only = True
    with pytest.raises(OmvBridgeConflict, match="read-only"):
        service.plan_filesystem_quota(_quota_desired())
    runner.read_only = False
    with pytest.raises(OmvBridgeValidationError, match="not uniquely"):
        service.plan_filesystem_quota(_quota_desired(subjectName="missing"))


def test_quota_control_rolls_back_a_failed_deployment() -> None:
    runner = _QuotaControlRunner()
    service = OmvReadOnlyService(runner)
    plan = service.plan_filesystem_quota(_quota_desired())
    runner.fail_apply_count = 1

    with pytest.raises(OmvBridgeError, match="deployment failure"):
        service.apply_filesystem_quota(_quota_desired(), plan["planId"])

    assert runner.limit_kib == 0
    assert runner.dirty is False
    quota_sets = [
        params
        for service_name, method, params in runner.calls
        if (service_name, method) == ("Quota", "setByTypeName")
    ]
    assert [item["bhardlimit"] for item in quota_sets] == [10 * 1024, 0]


def test_bridge_rejects_smart_for_a_device_not_backed_by_a_mounted_filesystem() -> None:
    runner = _RecordingRunner()

    with pytest.raises(OmvBridgeError, match="not an enumerated"):
        OmvReadOnlyService(runner).smart("/dev/sdz")

    assert runner.calls == [
        ("Smart", "enumerateDevices", {}),
        (
            "FileSystemMgmt",
            "enumerateMountedFilesystems",
            {"includeroot": False},
        ),
    ]


def test_command_runner_has_fixed_argv_no_shell_and_clean_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / "omv-rpc"
    executable.write_text("placeholder")
    captured: dict[str, Any] = {}

    def _run(command: list[str], **kwargs: Any) -> SimpleNamespace:
        captured.update(command=command, **kwargs)
        return SimpleNamespace(returncode=0, stdout=b'{"ok":true}')

    monkeypatch.setattr("appliance.omv_bridge_runners.subprocess.run", _run)

    result = OmvCommandRunner(executable)(
        "FileSystemMgmt",
        "enumerateMountedFilesystems",
        {"includeroot": False},
    )

    assert result == {"ok": True}
    assert captured["command"] == [
        str(executable),
        "-u",
        "admin",
        "FileSystemMgmt",
        "enumerateMountedFilesystems",
        '{"includeroot":false}',
    ]
    assert "shell" not in captured
    assert captured["cwd"] == "/"
    assert captured["env"] == {
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
    }


def test_command_runner_rejects_a_symlinked_executable(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "real-omv-rpc"
    executable.write_text("placeholder")
    symlink = tmp_path / "omv-rpc"
    symlink.symlink_to(executable)

    with pytest.raises(OmvBridgeError, match="unavailable"):
        OmvCommandRunner(symlink)(
            "Smart",
            "getInformation",
            {"devicefile": "/dev/sda"},
        )


def test_command_runner_rejects_non_allowlisted_rpc_before_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / "omv-rpc"
    executable.write_text("placeholder")
    called = False

    def _run(*_args: Any, **_kwargs: Any) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr("appliance.omv_bridge_runners.subprocess.run", _run)

    with pytest.raises(OmvBridgeError, match="fixed allowlist"):
        OmvCommandRunner(executable)("Config", "revertChanges", {})
    assert called is False


def test_command_runner_allows_only_fixed_quota_parameters(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / "omv-rpc"
    executable.write_text("placeholder")
    captured: list[list[str]] = []

    def _run(command: list[str], **_kwargs: Any) -> SimpleNamespace:
        captured.append(command)
        return SimpleNamespace(returncode=0, stdout=b"{}")

    monkeypatch.setattr("appliance.omv_bridge_runners.subprocess.run", _run)
    runner = OmvCommandRunner(executable)
    params = {
        "uuid": FILESYSTEM_UUID,
        "type": "user",
        "name": "alice",
        "bhardlimit": 10 * 1024,
        "bunit": "KiB",
    }

    assert runner("Quota", "setByTypeName", params) == {}
    assert captured[0][-3:] == [
        "Quota",
        "setByTypeName",
        json.dumps(params, sort_keys=True, separators=(",", ":")),
    ]

    invalid = [
        {**params, "bunit": "GiB"},
        {**params, "bhardlimit": True},
        {**params, "name": "alice\nroot"},
        {**params, "path": "/srv/family"},
    ]
    for candidate in invalid:
        with pytest.raises(OmvBridgeError, match="quota mutation"):
            runner("Quota", "setByTypeName", candidate)


def test_command_runner_allows_only_canonical_share_privilege_parameters(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / "omv-rpc"
    executable.write_text("placeholder")
    captured: list[list[str]] = []

    def _run(command: list[str], **_kwargs: Any) -> SimpleNamespace:
        captured.append(command)
        return SimpleNamespace(returncode=0, stdout=b"null")

    monkeypatch.setattr("appliance.omv_bridge_runners.subprocess.run", _run)
    runner = OmvCommandRunner(executable)
    params = {
        "uuid": SHARE_UUID,
        "privileges": [
            {"type": "user", "name": "alice", "perms": 7},
            {"type": "group", "name": "users", "perms": 5},
        ],
    }

    assert runner("ShareMgmt", "setPrivileges", params) is None
    assert captured[0][-3:] == [
        "ShareMgmt",
        "setPrivileges",
        json.dumps(params, sort_keys=True, separators=(",", ":")),
    ]

    invalid = [
        {**params, "privileges": list(reversed(params["privileges"]))},
        {**params, "privileges": [params["privileges"][0], params["privileges"][0]]},
        {**params, "privileges": [{"type": "user", "name": "alice", "perms": None}]},
        {**params, "privileges": [{"type": "user", "name": "alice", "perms": True}]},
        {**params, "privileges": [{"type": "user", "name": "alice\nroot", "perms": 7}]},
        {**params, "recursive": True},
    ]
    for candidate in invalid:
        with pytest.raises(OmvBridgeError, match="privilege mutation"):
            runner("ShareMgmt", "setPrivileges", candidate)

    assert len(captured) == 1


def test_command_runner_allows_only_single_real_privilege_deploy_modules(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / "omv-rpc"
    executable.write_text("placeholder")
    captured: list[list[str]] = []

    def _run(command: list[str], **_kwargs: Any) -> SimpleNamespace:
        captured.append(command)
        return SimpleNamespace(returncode=0, stdout=b"false")

    monkeypatch.setattr("appliance.omv_bridge_runners.subprocess.run", _run)
    runner = OmvCommandRunner(executable)

    assert runner("Config", "isDirty", {"modules": ["rsyncd"]}) is False
    with pytest.raises(OmvBridgeError, match="dirty-state parameters"):
        runner("Config", "isDirty", {"modules": ["samba", "rsyncd"]})
    with pytest.raises(OmvBridgeError, match="dirty-state parameters"):
        runner("Config", "isDirty", {"modules": ["rsync"]})
    assert len(captured) == 1


def test_command_runner_uses_the_real_omv_samba_module_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / "omv-rpc"
    executable.write_text("placeholder")
    captured: list[list[str]] = []

    def _run(command: list[str], **_kwargs: Any) -> SimpleNamespace:
        captured.append(command)
        return SimpleNamespace(returncode=0, stdout=b"false")

    monkeypatch.setattr("appliance.omv_bridge_runners.subprocess.run", _run)
    runner = OmvCommandRunner(executable)

    assert runner("Config", "isDirty", {"modules": ["samba"]}) is False
    assert json.loads(captured[0][-1]) == {"modules": ["samba"]}
    with pytest.raises(OmvBridgeError, match="dirty-state parameters"):
        runner("Config", "isDirty", {"modules": ["smb"]})
    assert len(captured) == 1


def test_lsblk_runner_uses_fixed_read_only_columns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / "lsblk"
    executable.write_text("placeholder")
    captured: dict[str, Any] = {}

    def _run(command: list[str], **kwargs: Any) -> SimpleNamespace:
        captured.update(command=command, **kwargs)
        return SimpleNamespace(returncode=0, stdout=b'{"blockdevices":[]}')

    monkeypatch.setattr("appliance.omv_bridge_runners.subprocess.run", _run)

    result = LsblkTopologyRunner(executable)()

    assert result == {"blockdevices": []}
    assert captured["command"] == [
        str(executable),
        "--json",
        "--bytes",
        "--paths",
        "--tree",
        "--output",
        "NAME,TYPE,SIZE,FSTYPE,ROTA",
    ]
    assert "shell" not in captured
    assert all(sensitive not in captured["command"][-1] for sensitive in ("UUID", "SERIAL", "WWN"))


def test_unix_socket_bridge_and_echo_client_round_trip() -> None:
    # macOS has a short AF_UNIX path limit, so keep this integration socket out
    # of pytest's deliberately descriptive (and much longer) tmp_path.
    with tempfile.TemporaryDirectory(prefix="echo-omv-", dir="/tmp") as directory:
        socket_path = Path(directory) / "omv.sock"
        server = create_server(
            socket_path,
            OmvReadOnlyService(_RecordingRunner(), _topology_payload, _mdstat_payload),
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = OmvClient(str(socket_path))

            assert stat.S_IMODE(socket_path.stat().st_mode) == 0o660
            assert client.ping() is True
            assert client.filesystems()[0]["label"] == "Family"
            assert client.smart_devices()[0] == {
                "devicefile": "/dev/sda",
                "model": "Example Disk",
                "sizeBytes": 2_000_000,
                "health": "GOOD",
                "temperatureC": 31,
            }
            assert client.smart("/dev/sda")["health"] == "PASSED"
            topology = client.storage_topology()
            assert topology["arrays"][0]["status"] == "degraded"
            assert topology["devices"][2]["devicefile"] == "/dev/md0"
            sharing = client.sharing_overview()
            assert sharing["sharedFolders"][0]["name"] == "Family"
            assert sharing["smb"]["enabled"] is True
            assert client.share_privileges(SHARE_UUID)[0]["permission"] == "readWrite"

            transport = httpx.HTTPTransport(uds=str(socket_path))
            with httpx.Client(transport=transport, base_url="http://echo-omv") as raw:
                assert raw.post("/v1/filesystems").status_code == 405
                assert raw.get("/v1/unknown").status_code == 404
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            socket_path.unlink(missing_ok=True)


def test_unix_socket_shared_folder_control_round_trip_is_fixed_and_bounded() -> None:
    with tempfile.TemporaryDirectory(prefix="echo-omv-folder-", dir="/tmp") as directory:
        socket_path = Path(directory) / "omv.sock"
        runner = _SharedFolderControlRunner()
        server = create_server(socket_path, OmvReadOnlyService(runner))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = OmvClient(str(socket_path))
            overview = client.sharing_overview()
            plan = client.plan_shared_folder(_shared_folder_desired())
            applied = client.apply_shared_folder(_shared_folder_desired(), plan["planId"])

            assert overview["sharedFolderTargets"][0]["mountPointRef"] == NFS_MOUNT_UUID
            assert "secret" not in json.dumps(overview)
            assert plan["operation"] == "create"
            assert applied["shareUuid"] == CREATED_FOLDER_UUID
            assert applied["verified"] is True
            with httpx.Client(
                transport=httpx.HTTPTransport(uds=str(socket_path)),
                base_url="http://echo-omv",
            ) as raw:
                arbitrary = raw.post(
                    "/v1/sharing/folders/plan",
                    json={"desired": {**_shared_folder_desired(), "relativePath": "elsewhere"}},
                )
                stale = raw.post(
                    "/v1/sharing/folders/apply",
                    json={"desired": _shared_folder_desired(), "planId": "0" * 64},
                )
                destructive = raw.post(
                    "/v1/sharing/folders/delete",
                    json={"uuid": CREATED_FOLDER_UUID},
                )
            assert arbitrary.status_code == 422
            assert stale.status_code == 409
            assert destructive.status_code == 405
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


def test_unix_socket_share_privilege_control_round_trip_is_fixed_and_bounded() -> None:
    with tempfile.TemporaryDirectory(prefix="echo-omv-privilege-", dir="/tmp") as directory:
        socket_path = Path(directory) / "omv.sock"
        runner = _SharePrivilegeControlRunner()
        server = create_server(socket_path, OmvReadOnlyService(runner))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = OmvClient(str(socket_path))
            desired = _share_privilege_desired()
            plan = client.plan_share_privilege(desired)
            applied = client.apply_share_privilege(desired, plan["planId"])

            assert plan["principal"]["before"] == "inherit"
            assert plan["principal"]["after"] == "readWrite"
            assert applied["verified"] is True
            assert applied["deployedServices"] == ["samba", "rsyncd"]
            assert client.share_privileges(SHARE_UUID)[0]["permission"] == "readWrite"

            with httpx.Client(
                transport=httpx.HTTPTransport(uds=str(socket_path)),
                base_url="http://echo-omv",
            ) as raw:
                arbitrary_principal = raw.post(
                    "/v1/sharing/privileges/plan",
                    json={"desired": _share_privilege_desired(principalName="root")},
                )
                filesystem_acl = raw.post(
                    "/v1/sharing/privileges/apply-recursive",
                    json={"uuid": SHARE_UUID, "mode": "777"},
                )
            assert arbitrary_principal.status_code == 422
            assert filesystem_acl.status_code == 405
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


def test_unix_socket_smb_control_round_trip_is_fixed_and_bounded() -> None:
    with tempfile.TemporaryDirectory(prefix="echo-omv-control-", dir="/tmp") as directory:
        socket_path = Path(directory) / "omv.sock"
        runner = _SmbControlRunner()
        server = create_server(socket_path, OmvReadOnlyService(runner))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = OmvClient(str(socket_path))
            assert client.capabilities() == [
                "shared-folder.create.simple.v1",
                "shared-folder.privilege.simple.v1",
                "smb.share.desired.v1",
                "nfs.share.private-network.v1",
                "filesystem.quota.user-group.v1",
                "account.group.create.v1",
                "account.user.create.v1",
                "account.user.password.reset.v1",
            ]

            plan = client.plan_smb_share(_smb_desired())
            applied = client.apply_smb_share(_smb_desired(), plan["planId"])

            assert plan["operation"] == "create"
            assert applied["applied"] is True
            assert applied["verified"] is True
            transport = httpx.HTTPTransport(uds=str(socket_path))
            with httpx.Client(transport=transport, base_url="http://echo-omv") as raw:
                unknown = raw.post(
                    "/v1/sharing/smb/plan",
                    json={"desired": {**_smb_desired(), "guest": "only"}},
                )
                stale = raw.post(
                    "/v1/sharing/smb/apply",
                    json={"desired": _smb_desired(), "planId": "0" * 64},
                )
                arbitrary = raw.post("/v1/filesystems", json={"rpc": "anything"})
            assert unknown.status_code == 422
            assert stale.status_code == 409
            assert arbitrary.status_code == 405
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            socket_path.unlink(missing_ok=True)


def test_unix_socket_nfs_control_round_trip_is_fixed_and_bounded() -> None:
    with tempfile.TemporaryDirectory(prefix="echo-omv-nfs-", dir="/tmp") as directory:
        socket_path = Path(directory) / "omv.sock"
        runner = _NfsControlRunner()
        server = create_server(socket_path, OmvReadOnlyService(runner))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = OmvClient(str(socket_path))
            assert client.supports_nfs_control() is True
            plan = client.plan_nfs_share(_nfs_desired())
            applied = client.apply_nfs_share(_nfs_desired(), plan["planId"])

            assert plan["operation"] == "create"
            assert applied["applied"] is True
            assert applied["verified"] is True
            transport = httpx.HTTPTransport(uds=str(socket_path))
            with httpx.Client(transport=transport, base_url="http://echo-omv") as raw:
                public = raw.post(
                    "/v1/sharing/nfs/plan",
                    json={"desired": _nfs_desired(clientCidr="8.8.8.0/24")},
                )
                advanced = raw.post(
                    "/v1/sharing/nfs/plan",
                    json={"desired": {**_nfs_desired(), "extraoptions": "no_root_squash"}},
                )
            assert public.status_code == 422
            assert advanced.status_code == 422
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            socket_path.unlink(missing_ok=True)


def test_unix_socket_quota_control_round_trip_is_fixed_and_bounded() -> None:
    with tempfile.TemporaryDirectory(prefix="echo-omv-quota-", dir="/tmp") as directory:
        socket_path = Path(directory) / "omv.sock"
        runner = _QuotaControlRunner()
        server = create_server(socket_path, OmvReadOnlyService(runner))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = OmvClient(str(socket_path))
            assert client.supports_quota_control() is True
            plan = client.plan_filesystem_quota(_quota_desired())
            applied = client.apply_filesystem_quota(_quota_desired(), plan["planId"])

            assert plan["operation"] == "update"
            assert applied["applied"] is True
            assert applied["verified"] is True
            assert runner.limit_kib == 10 * 1024
            transport = httpx.HTTPTransport(uds=str(socket_path))
            with httpx.Client(transport=transport, base_url="http://echo-omv") as raw:
                unknown = raw.post(
                    "/v1/quota/plan",
                    json={"desired": {**_quota_desired(), "sharedFolderPath": "Family"}},
                )
                stale = raw.post(
                    "/v1/quota/apply",
                    json={
                        "desired": _quota_desired(hardLimitBytes=20 * 1024**2),
                        "planId": "0" * 64,
                    },
                )
                arbitrary = raw.post("/v1/filesystems", json={"service": "Quota"})
            assert unknown.status_code == 422
            assert stale.status_code == 409
            assert arbitrary.status_code == 405
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            socket_path.unlink(missing_ok=True)


def test_echo_client_rejects_a_regular_file_instead_of_a_socket(tmp_path: Path) -> None:
    fake_socket = tmp_path / "omv.sock"
    fake_socket.write_text("not a socket")

    with pytest.raises(OmvUnavailable, match="not a Unix socket"):
        OmvClient(str(fake_socket)).filesystems()
