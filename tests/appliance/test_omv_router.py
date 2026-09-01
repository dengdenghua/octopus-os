from __future__ import annotations

import hashlib
import time
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from appliance.approval import APPROVAL_HEADER, HighRiskApprovalService, create_approval_router
from appliance.audit import ApplianceAudit
from appliance.omv_client import OmvUnavailable
from appliance.omv_router import create_omv_router
from runtime.safety.auth.identity import encode_jwt_hs256

JWT_SECRET = "omv-router-test-secret-which-is-not-production"
PASSWORD = "device-admin-password"
PASSWORD_HASH = hashlib.sha256(PASSWORD.encode()).hexdigest()
PLAN_ID = "a" * 64
QUOTA_PLAN_ID = "c" * 64
NFS_PLAN_ID = "e" * 64
FOLDER_PLAN_ID = "9" * 64
PRIVILEGE_PLAN_ID = "7" * 64
GROUP_PLAN_ID = "5" * 64
USER_PLAN_ID = "4" * 64
USER_PASSWORD_PLAN_ID = "6" * 64
SHARE_UUID = "11111111-2222-4333-8444-555555555555"
FILESYSTEM_UUID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
MOUNT_POINT_UUID = "77777777-6666-4555-8444-333333333333"


def _desired(**overrides: Any) -> dict[str, Any]:
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


def _folder_desired(**overrides: Any) -> dict[str, Any]:
    return {
        "schema": "echo.omv.shared-folder-desired.v1",
        "mountPointRef": MOUNT_POINT_UUID,
        "name": "Photos",
        "comment": "Family photos",
        **overrides,
    }


def _privilege_desired(**overrides: Any) -> dict[str, Any]:
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
        "hardLimitBytes": 10 * 1024**3,
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


def _user_password_desired(**overrides: Any) -> dict[str, Any]:
    return {
        "schema": "echo.omv.user-password-desired.v1",
        "name": "mother",
        "password": "Replacement-Family-2026!",
        **overrides,
    }


def _headers() -> dict[str, str]:
    token = encode_jwt_hs256(
        {"sub": "local:admin", "iat": 0, "exp": int(time.time()) + 60},
        secret=JWT_SECRET,
    )
    return {"Authorization": f"Bearer {token}"}


def _member_headers() -> dict[str, str]:
    token = encode_jwt_hs256(
        {"sub": "local:alice", "iat": 0, "exp": int(time.time()) + 60},
        secret=JWT_SECRET,
    )
    return {"Authorization": f"Bearer {token}"}


class _StubOmv:
    configured = True
    admin_url = "https://nas.example.test"

    def __init__(self, *, available: bool = True, control_supported: bool = False) -> None:
        self.available = available
        self.control_supported = control_supported
        self.calls: list[tuple[str, str | None]] = []

    def ping(self) -> bool:
        self.calls.append(("ping", None))
        return self.available

    def supports_smb_control(self) -> bool:
        self.calls.append(("supports_smb_control", None))
        return self.available and self.control_supported

    def filesystems(self) -> list[dict[str, Any]]:
        self.calls.append(("filesystems", None))
        if not self.available:
            raise OmvUnavailable("not connected")
        return [{"devicefile": "/dev/sda1"}]

    def smart(self, devicefile: str) -> dict[str, Any]:
        self.calls.append(("smart", devicefile))
        if not self.available:
            raise OmvUnavailable("not connected")
        return {"devicefile": devicefile, "health": "PASSED"}

    def smart_devices(self) -> list[dict[str, Any]]:
        self.calls.append(("smart_devices", None))
        if not self.available:
            raise OmvUnavailable("not connected")
        return [{"devicefile": "/dev/sda", "health": "GOOD"}]

    def storage_topology(self) -> dict[str, list[dict[str, Any]]]:
        self.calls.append(("storage_topology", None))
        if not self.available:
            raise OmvUnavailable("not connected")
        return {
            "devices": [{"devicefile": "/dev/md0", "type": "raid1"}],
            "arrays": [{"devicefile": "/dev/md0", "status": "healthy"}],
        }

    def sharing_overview(self) -> dict[str, Any]:
        self.calls.append(("sharing_overview", None))
        if not self.available:
            raise OmvUnavailable("not connected")
        return {
            "sharedFolders": [{"uuid": "11111111-2222-4333-8444-555555555555"}],
            "users": [{"name": "alice"}],
            "groups": [{"name": "users"}],
            "smb": {"enabled": True, "shares": []},
            "nfs": {"enabled": False, "shares": []},
        }

    def share_privileges(self, share_uuid: str) -> list[dict[str, Any]]:
        self.calls.append(("share_privileges", share_uuid))
        if not self.available:
            raise OmvUnavailable("not connected")
        return [{"type": "user", "name": "alice", "permission": "readWrite"}]

    def plan_smb_share(self, desired: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("plan_smb_share", desired["sharedFolderRef"]))
        if not self.available:
            raise OmvUnavailable("not connected")
        return {
            "schema": "echo.omv.smb-share-plan.v1",
            "planId": PLAN_ID,
            "baseRevision": "b" * 64,
            "operation": "create",
            "requiresApproval": True,
            "shareUuid": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
            "sharedFolder": {"uuid": SHARE_UUID, "name": "Family", "status": "OK"},
            "desired": desired,
            "changes": [{"field": "comment", "before": None, "after": desired["comment"]}],
            "safety": {"guestAccess": "disabled"},
        }

    def apply_smb_share(self, desired: dict[str, Any], plan_id: str) -> dict[str, Any]:
        self.calls.append(("apply_smb_share", plan_id))
        return {**self.plan_smb_share(desired), "applied": True, "verified": True}


class _QuotaStubOmv(_StubOmv):
    def capabilities(self) -> list[str]:
        self.calls.append(("capabilities", None))
        return ["filesystem.quota.user-group.v1"] if self.available else []

    def plan_filesystem_quota(self, desired: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("plan_filesystem_quota", desired["subjectName"]))
        if not self.available:
            raise OmvUnavailable("not connected")
        return {
            "schema": "echo.omv.filesystem-quota-plan.v1",
            "planId": QUOTA_PLAN_ID,
            "baseRevision": "d" * 64,
            "operation": "update",
            "requiresApproval": True,
            "filesystem": {
                "uuid": FILESYSTEM_UUID,
                "label": "Family",
                "type": "ext4",
                "readOnly": False,
                "supportsQuota": True,
            },
            "subject": {
                "type": desired["subjectType"],
                "name": desired["subjectName"],
                "hardLimitBytes": 0,
                "used": "4 MiB",
            },
            "desired": desired,
            "changes": [
                {
                    "field": "hardLimitBytes",
                    "before": 0,
                    "after": desired["hardLimitBytes"],
                }
            ],
            "safety": {
                "scope": "filesystemUserOrGroup",
                "protocolCoverage": ["local", "SMB", "NFS"],
                "sharedFolderQuota": "notSupportedByOmvQuotaRpc",
                "minimumUnitBytes": 1024,
            },
        }

    def apply_filesystem_quota(
        self,
        desired: dict[str, Any],
        plan_id: str,
    ) -> dict[str, Any]:
        self.calls.append(("apply_filesystem_quota", plan_id))
        return {
            **self.plan_filesystem_quota(desired),
            "applied": True,
            "verified": True,
        }


class _NfsStubOmv(_StubOmv):
    def capabilities(self) -> list[str]:
        self.calls.append(("capabilities", None))
        return ["nfs.share.private-network.v1"] if self.available else []

    def plan_nfs_share(self, desired: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("plan_nfs_share", desired["clientCidr"]))
        return {
            "schema": "echo.omv.nfs-share-plan.v1",
            "planId": NFS_PLAN_ID,
            "baseRevision": "f" * 64,
            "operation": "create",
            "requiresApproval": True,
            "shareUuid": "99999999-8888-4777-8666-555555555555",
            "sharedFolder": {"uuid": SHARE_UUID, "name": "Family", "status": "OK"},
            "desired": desired,
            "changes": [
                {"field": "readOnly", "before": None, "after": desired["readOnly"]},
                {"field": "comment", "before": None, "after": desired["comment"]},
            ],
            "safety": {
                "clientScope": "privateCidrOnly",
                "rootSquash": "required",
                "syncWrites": "required",
                "advancedOptions": "notManaged",
                "delete": "notManaged",
            },
        }

    def apply_nfs_share(self, desired: dict[str, Any], plan_id: str) -> dict[str, Any]:
        self.calls.append(("apply_nfs_share", plan_id))
        return {**self.plan_nfs_share(desired), "applied": True, "verified": True}


class _SharedFolderStubOmv(_StubOmv):
    def capabilities(self) -> list[str]:
        self.calls.append(("capabilities", None))
        return ["shared-folder.create.simple.v1"] if self.available else []

    def plan_shared_folder(self, desired: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("plan_shared_folder", desired["name"]))
        return {
            "schema": "echo.omv.shared-folder-plan.v1",
            "planId": FOLDER_PLAN_ID,
            "baseRevision": "8" * 64,
            "operation": "create",
            "requiresApproval": True,
            "shareUuid": "44444444-3333-4222-8111-000000000000",
            "target": {
                "mountPointRef": MOUNT_POINT_UUID,
                "filesystemUuid": FILESYSTEM_UUID,
                "label": "Family",
                "type": "ext4",
                "sizeBytes": 100 * 1024**3,
                "availableBytes": 80 * 1024**3,
                "readOnly": False,
            },
            "desired": desired,
            "changes": [
                {"field": "name", "before": None, "after": desired["name"]},
                {"field": "comment", "before": None, "after": desired["comment"]},
            ],
            "safety": {
                "filesystem": "existingMountedWritableOnly",
                "relativePath": "derivedFromPortableName",
                "directoryMode": "2770UsersGroup",
                "acl": "notManaged",
                "update": "notManaged",
                "delete": "notManaged",
            },
        }

    def apply_shared_folder(self, desired: dict[str, Any], plan_id: str) -> dict[str, Any]:
        self.calls.append(("apply_shared_folder", plan_id))
        return {**self.plan_shared_folder(desired), "applied": True, "verified": True}


class _SharePrivilegeStubOmv(_StubOmv):
    def capabilities(self) -> list[str]:
        self.calls.append(("capabilities", None))
        return ["shared-folder.privilege.simple.v1"] if self.available else []

    def plan_share_privilege(self, desired: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("plan_share_privilege", desired["principalName"]))
        return {
            "schema": "echo.omv.share-privilege-plan.v1",
            "planId": PRIVILEGE_PLAN_ID,
            "baseRevision": "6" * 64,
            "operation": "update",
            "requiresApproval": True,
            "sharedFolder": {"uuid": SHARE_UUID, "name": "Family", "status": "OK"},
            "principal": {
                "type": desired["principalType"],
                "id": 1000,
                "name": desired["principalName"],
                "before": "inherit",
                "after": desired["permission"],
            },
            "desired": desired,
            "changes": [
                {
                    "field": "permission",
                    "before": "inherit",
                    "after": desired["permission"],
                }
            ],
            "safety": {
                "scope": "sharedFolderConfigPrivilege",
                "principal": "existingOmvUserOrGroup",
                "filesystemAcl": "notModified",
                "recursive": "never",
                "serviceDeploy": "sambaAndRsyncdWhenDirty",
                "delete": "notManaged",
            },
        }

    def apply_share_privilege(self, desired: dict[str, Any], plan_id: str) -> dict[str, Any]:
        self.calls.append(("apply_share_privilege", plan_id))
        return {
            **self.plan_share_privilege(desired),
            "applied": True,
            "verified": True,
            "deployedServices": ["samba"],
        }


class _AccountStubOmv(_StubOmv):
    def __init__(self) -> None:
        super().__init__(available=True)
        self.received_passwords: list[str] = []

    def capabilities(self) -> list[str]:
        self.calls.append(("capabilities", None))
        return [
            "account.group.create.v1",
            "account.user.create.v1",
            "account.user.password.reset.v1",
        ]

    def plan_group(self, desired: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("plan_group", desired["name"]))
        return {
            "schema": "echo.omv.group-plan.v1",
            "planId": GROUP_PLAN_ID,
            "baseRevision": "3" * 64,
            "operation": "create",
            "requiresApproval": True,
            "desired": desired,
            "changes": [
                {"field": "name", "before": None, "after": desired["name"]},
                {"field": "comment", "before": None, "after": desired["comment"]},
            ],
            "safety": {"initialMembers": "empty"},
        }

    def apply_group(self, desired: dict[str, Any], plan_id: str) -> dict[str, Any]:
        self.calls.append(("apply_group", plan_id))
        return {**self.plan_group(desired), "applied": True, "verified": True}

    def plan_user(self, desired: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("plan_user", desired["name"]))
        self.received_passwords.append(desired["password"])
        return {
            "schema": "echo.omv.user-plan.v1",
            "planId": USER_PLAN_ID,
            "baseRevision": "2" * 64,
            "operation": "create",
            "requiresApproval": True,
            "desired": {
                "schema": desired["schema"],
                "name": desired["name"],
                "displayName": desired["displayName"],
                "groups": desired["groups"],
                "passwordBound": True,
            },
            "changes": [
                {"field": "name", "before": None, "after": desired["name"]},
                {
                    "field": "displayName",
                    "before": None,
                    "after": desired["displayName"],
                },
                {"field": "groups", "before": [], "after": desired["groups"]},
            ],
            "safety": {"loginShell": "nologin", "sshKeys": "none"},
        }

    def apply_user(self, desired: dict[str, Any], plan_id: str) -> dict[str, Any]:
        self.calls.append(("apply_user", plan_id))
        return {**self.plan_user(desired), "applied": True, "verified": True}

    def plan_user_password(self, desired: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("plan_user_password", desired["name"]))
        self.received_passwords.append(desired["password"])
        return {
            "schema": "echo.omv.user-password-plan.v1",
            "planId": USER_PASSWORD_PLAN_ID,
            "baseRevision": "6" * 64,
            "operation": "resetPassword",
            "requiresApproval": True,
            "desired": {
                "schema": desired["schema"],
                "name": desired["name"],
                "passwordBound": True,
            },
            "changes": [
                {
                    "field": "password",
                    "before": "currentCredential",
                    "after": "replacementCredential",
                }
            ],
            "safety": {
                "accountFields": "preservedAndVerified",
                "rollback": "notAvailableAfterAcceptedSecretRpc",
            },
        }

    def apply_user_password(self, desired: dict[str, Any], plan_id: str) -> dict[str, Any]:
        self.calls.append(("apply_user_password", plan_id))
        return {
            **self.plan_user_password(desired),
            "applied": True,
            "verified": True,
        }


class _StubMonitor:
    def __init__(self) -> None:
        self.calls = 0

    def snapshot(self) -> dict[str, Any]:
        self.calls += 1
        return {
            "schemaVersion": 1,
            "state": "healthy",
            "stale": False,
            "checkedAt": "2026-08-26T01:00:00Z",
            "lastSuccessfulAt": "2026-08-26T01:00:00Z",
            "intervalSeconds": 300,
            "persistenceHealthy": True,
            "monitoring": True,
            "activeAlerts": [],
            "events": [],
            "summary": {"critical": 0, "warning": 0, "total": 0},
            "readOnly": True,
        }


def _client(stub: _StubOmv, monitor=None, *, approval=None, audit=None) -> TestClient:
    app = FastAPI()
    if approval is not None:
        app.include_router(create_approval_router(approval, jwt_secret=JWT_SECRET))
    app.include_router(
        create_omv_router(
            stub,
            monitor=monitor,
            jwt_secret=JWT_SECRET,
            approval=approval,
            audit=audit,
        )
    )
    return TestClient(app)


def test_omv_routes_require_the_device_login() -> None:
    client = _client(_StubOmv())

    assert client.get("/api/appliance/omv/status").status_code == 401
    assert client.get("/api/appliance/omv/health").status_code == 401
    assert client.get("/api/appliance/omv/filesystems").status_code == 401
    assert client.get("/api/appliance/omv/smart?devicefile=/dev/sda").status_code == 401
    assert client.get("/api/appliance/omv/smart/devices").status_code == 401
    assert client.get("/api/appliance/omv/topology").status_code == 401
    assert client.get("/api/appliance/omv/sharing").status_code == 401
    assert (
        client.post("/api/appliance/omv/accounts/groups/plan", json=_group_desired()).status_code
        == 401
    )
    assert (
        client.post("/api/appliance/omv/accounts/users/plan", json=_user_desired()).status_code
        == 401
    )
    assert (
        client.post("/api/appliance/omv/sharing/folders/plan", json=_folder_desired()).status_code
        == 401
    )
    assert client.post("/api/appliance/omv/sharing/smb/plan", json=_desired()).status_code == 401
    assert client.post("/api/appliance/omv/quota/plan", json=_quota_desired()).status_code == 401
    assert (
        client.post(
            "/api/appliance/omv/quota/apply",
            json={"desired": _quota_desired(), "planId": QUOTA_PLAN_ID},
        ).status_code
        == 401
    )


def test_family_member_cannot_read_or_mutate_the_omv_control_plane() -> None:
    client = _client(_StubOmv())

    assert client.get("/api/appliance/omv/status", headers=_member_headers()).status_code == 403
    assert (
        client.post(
            "/api/appliance/omv/accounts/groups/plan",
            headers=_member_headers(),
            json=_group_desired(),
        ).status_code
        == 403
    )


def test_persistent_health_snapshot_is_authenticated_and_read_only() -> None:
    monitor = _StubMonitor()
    client = _client(_StubOmv(), monitor)

    response = client.get("/api/appliance/omv/health", headers=_headers())
    post = client.post("/api/appliance/omv/health", headers=_headers())

    assert response.status_code == 200
    assert response.json()["state"] == "healthy"
    assert response.json()["monitoring"] is True
    assert response.json()["readOnly"] is True
    assert post.status_code == 405
    assert monitor.calls == 1


def test_status_and_read_paths_are_exposed_without_mutating_routes() -> None:
    stub = _StubOmv()
    client = _client(stub)

    status = client.get("/api/appliance/omv/status", headers=_headers())
    filesystems = client.get("/api/appliance/omv/filesystems", headers=_headers())
    smart = client.get(
        "/api/appliance/omv/smart",
        params={"devicefile": "/dev/sda"},
        headers=_headers(),
    )
    devices = client.get("/api/appliance/omv/smart/devices", headers=_headers())
    topology = client.get("/api/appliance/omv/topology", headers=_headers())
    sharing = client.get("/api/appliance/omv/sharing", headers=_headers())
    privileges = client.get(
        "/api/appliance/omv/sharing/11111111-2222-4333-8444-555555555555/privileges",
        headers=_headers(),
    )
    post = client.post("/api/appliance/omv/filesystems", headers=_headers())

    assert status.json() == {
        "configured": True,
        "available": True,
        "readOnly": True,
        "adminUrl": "https://nas.example.test",
        "capabilities": [],
    }
    assert filesystems.json() == {
        "filesystems": [{"devicefile": "/dev/sda1"}],
        "readOnly": True,
    }
    assert smart.json() == {
        "smart": {"devicefile": "/dev/sda", "health": "PASSED"},
        "readOnly": True,
    }
    assert devices.json() == {
        "devices": [{"devicefile": "/dev/sda", "health": "GOOD"}],
        "readOnly": True,
    }
    assert topology.json() == {
        "devices": [{"devicefile": "/dev/md0", "type": "raid1"}],
        "arrays": [{"devicefile": "/dev/md0", "status": "healthy"}],
        "readOnly": True,
    }
    assert sharing.json()["smb"] == {"enabled": True, "shares": []}
    assert sharing.json()["readOnly"] is True
    assert privileges.json() == {
        "privileges": [{"type": "user", "name": "alice", "permission": "readWrite"}],
        "readOnly": True,
    }
    assert post.status_code == 405
    assert stub.calls == [
        ("ping", None),
        ("supports_smb_control", None),
        ("filesystems", None),
        ("smart", "/dev/sda"),
        ("smart_devices", None),
        ("storage_topology", None),
        ("sharing_overview", None),
        (
            "share_privileges",
            "11111111-2222-4333-8444-555555555555",
        ),
    ]


def test_invalid_device_path_is_rejected_before_the_bridge() -> None:
    stub = _StubOmv()
    response = _client(stub).get(
        "/api/appliance/omv/smart",
        params={"devicefile": "/dev/sda;shutdown"},
        headers=_headers(),
    )

    assert response.status_code == 422
    assert stub.calls == []


def test_unavailable_read_bridge_maps_to_service_unavailable() -> None:
    stub = _StubOmv(available=False)
    client = _client(stub)

    status = client.get("/api/appliance/omv/status", headers=_headers())
    filesystems = client.get("/api/appliance/omv/filesystems", headers=_headers())

    assert status.json() == {
        "configured": True,
        "available": False,
        "readOnly": True,
        "adminUrl": "https://nas.example.test",
        "capabilities": [],
    }
    assert filesystems.status_code == 503
    assert filesystems.json() == {"detail": "OMV read-only integration is unavailable"}


def test_family_account_creation_requires_plan_bound_approval_and_never_audits_password(
    tmp_path,
) -> None:
    audit = ApplianceAudit.from_data_dir(tmp_path, jwt_secret=JWT_SECRET)
    approval = HighRiskApprovalService(
        password_hash=PASSWORD_HASH,
        jwt_secret=JWT_SECRET,
        audit=audit,
        boot_nonce=b"a" * 32,
    )
    stub = _AccountStubOmv()
    client = _client(stub, approval=approval, audit=audit)

    status = client.get("/api/appliance/omv/status", headers=_headers())
    assert status.json()["capabilities"] == [
        "account.group.create.v1",
        "account.user.create.v1",
        "account.user.password.reset.v1",
    ]

    group_plan = client.post(
        "/api/appliance/omv/accounts/groups/plan",
        json=_group_desired(),
        headers=_headers(),
    )
    assert group_plan.status_code == 200
    group_body = {"desired": _group_desired(), "planId": GROUP_PLAN_ID}
    assert (
        client.post(
            "/api/appliance/omv/accounts/groups/apply",
            json=group_body,
            headers=_headers(),
        ).status_code
        == 403
    )
    group_approval = client.post(
        "/api/appliance/approvals",
        json={"action": "omv.group.create", "target": GROUP_PLAN_ID, "password": PASSWORD},
        headers=_headers(),
    )
    assert group_approval.status_code == 200
    group_result = client.post(
        "/api/appliance/omv/accounts/groups/apply",
        json=group_body,
        headers={**_headers(), APPROVAL_HEADER: group_approval.json()["approvalToken"]},
    )
    assert group_result.status_code == 200
    assert group_result.json()["verified"] is True

    user_plan = client.post(
        "/api/appliance/omv/accounts/users/plan",
        json=_user_desired(),
        headers=_headers(),
    )
    assert user_plan.status_code == 200
    assert "Echo-Family-2026!" not in user_plan.text
    assert "password" not in user_plan.json()["desired"]
    user_body = {"desired": _user_desired(), "planId": USER_PLAN_ID}
    assert (
        client.post(
            "/api/appliance/omv/accounts/users/apply",
            json=user_body,
            headers=_headers(),
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/appliance/omv/accounts/users/apply",
            json={**user_body, "planId": "0" * 64},
            headers=_headers(),
        ).status_code
        == 409
    )
    user_approval = client.post(
        "/api/appliance/approvals",
        json={"action": "omv.user.create", "target": USER_PLAN_ID, "password": PASSWORD},
        headers=_headers(),
    )
    assert user_approval.status_code == 200
    user_result = client.post(
        "/api/appliance/omv/accounts/users/apply",
        json=user_body,
        headers={
            **_headers(),
            APPROVAL_HEADER: user_approval.json()["approvalToken"],
            "X-Echo-Intent": "task-family-user-1",
        },
    )
    assert user_result.status_code == 200
    assert user_result.json()["verified"] is True
    assert "Echo-Family-2026!" not in user_result.text

    records = [entry["payload"] for entry in audit.recent(50)]
    assert "Echo-Family-2026!" not in str(records)
    assert all(password == "Echo-Family-2026!" for password in stub.received_passwords)
    assert any(
        record["action"] == "omv.group.create"
        and record["outcome"] == "succeeded"
        and record["metadata"]["name"] == "family"
        for record in records
    )
    assert any(
        record["action"] == "omv.user.create"
        and record["outcome"] == "succeeded"
        and record["metadata"]["name"] == "mother"
        and record["metadata"]["groups"] == ["family"]
        and record["metadata"]["intentId"] == "task-family-user-1"
        and "password" not in str(record["metadata"]).casefold()
        for record in records
    )


def test_family_account_validation_rejects_reserved_names_and_weak_passwords() -> None:
    stub = _AccountStubOmv()
    client = _client(stub)

    reserved_group = client.post(
        "/api/appliance/omv/accounts/groups/plan",
        json=_group_desired(name="root"),
        headers=_headers(),
    )
    weak_password = client.post(
        "/api/appliance/omv/accounts/users/plan",
        json=_user_desired(password="weakpassword"),
        headers=_headers(),
    )
    unsorted_groups = client.post(
        "/api/appliance/omv/accounts/users/plan",
        json=_user_desired(groups=["photos", "family"]),
        headers=_headers(),
    )

    assert reserved_group.status_code == 422
    assert weak_password.status_code == 422
    assert "weakpassword" not in weak_password.text
    assert unsorted_groups.status_code == 422
    assert not any(call[0] in {"plan_group", "plan_user"} for call in stub.calls)


def test_family_password_reset_requires_bound_approval_and_never_audits_secret(
    tmp_path,
) -> None:
    audit = ApplianceAudit.from_data_dir(tmp_path, jwt_secret=JWT_SECRET)
    approval = HighRiskApprovalService(
        password_hash=PASSWORD_HASH,
        jwt_secret=JWT_SECRET,
        audit=audit,
        boot_nonce=b"r" * 32,
    )
    stub = _AccountStubOmv()
    client = _client(stub, approval=approval, audit=audit)
    desired = _user_password_desired()

    preview = client.post(
        "/api/appliance/omv/accounts/users/password/plan",
        json=desired,
        headers=_headers(),
    )
    assert preview.status_code == 200
    assert desired["password"] not in preview.text
    assert preview.json()["desired"]["passwordBound"] is True
    body = {"desired": desired, "planId": USER_PASSWORD_PLAN_ID}
    assert (
        client.post(
            "/api/appliance/omv/accounts/users/password/apply",
            json=body,
            headers=_headers(),
        ).status_code
        == 403
    )
    stale = client.post(
        "/api/appliance/omv/accounts/users/password/apply",
        json={**body, "planId": "0" * 64},
        headers=_headers(),
    )
    assert stale.status_code == 409
    step_up = client.post(
        "/api/appliance/approvals",
        json={
            "action": "omv.user.password.reset",
            "target": USER_PASSWORD_PLAN_ID,
            "password": PASSWORD,
        },
        headers=_headers(),
    )
    assert step_up.status_code == 200
    result = client.post(
        "/api/appliance/omv/accounts/users/password/apply",
        json=body,
        headers={
            **_headers(),
            APPROVAL_HEADER: step_up.json()["approvalToken"],
            "X-Echo-Intent": "task-family-password-1",
        },
    )
    assert result.status_code == 200
    assert result.json()["verified"] is True
    assert desired["password"] not in result.text

    records = [entry["payload"] for entry in audit.recent(50)]
    assert desired["password"] not in str(records)
    assert any(
        record["action"] == "omv.user.password.reset"
        and record["outcome"] == "succeeded"
        and record["metadata"]["name"] == "mother"
        and record["metadata"]["changeFields"] == ["password"]
        and record["metadata"]["intentId"] == "task-family-password-1"
        and "replacement-family" not in str(record["metadata"]).casefold()
        for record in records
    )

    weak = client.post(
        "/api/appliance/omv/accounts/users/password/plan",
        json=_user_password_desired(password="weakpassword"),
        headers=_headers(),
    )
    assert weak.status_code == 422
    assert "weakpassword" not in weak.text


def test_shared_folder_create_requires_plan_bound_password_step_up_and_audits(tmp_path) -> None:
    audit = ApplianceAudit.from_data_dir(tmp_path, jwt_secret=JWT_SECRET)
    approval = HighRiskApprovalService(
        password_hash=PASSWORD_HASH,
        jwt_secret=JWT_SECRET,
        audit=audit,
        boot_nonce=b"f" * 32,
    )
    stub = _SharedFolderStubOmv()
    client = _client(stub, approval=approval, audit=audit)

    status = client.get("/api/appliance/omv/status", headers=_headers())
    plan = client.post(
        "/api/appliance/omv/sharing/folders/plan",
        json=_folder_desired(),
        headers=_headers(),
    )
    assert status.json()["capabilities"] == ["shared-folder.create.simple.v1"]
    assert plan.status_code == 200
    assert plan.json()["planId"] == FOLDER_PLAN_ID
    apply_body = {"desired": _folder_desired(), "planId": FOLDER_PLAN_ID}
    assert (
        client.post(
            "/api/appliance/omv/sharing/folders/apply",
            json=apply_body,
            headers=_headers(),
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/appliance/omv/sharing/folders/apply",
            json={**apply_body, "planId": "0" * 64},
            headers=_headers(),
        ).status_code
        == 409
    )

    wrong = client.post(
        "/api/appliance/approvals",
        json={"action": "omv.smb.apply", "target": FOLDER_PLAN_ID, "password": PASSWORD},
        headers=_headers(),
    )
    assert wrong.status_code == 200
    assert (
        client.post(
            "/api/appliance/omv/sharing/folders/apply",
            json=apply_body,
            headers={**_headers(), APPROVAL_HEADER: wrong.json()["approvalToken"]},
        ).status_code
        == 403
    )

    issued = client.post(
        "/api/appliance/approvals",
        json={
            "action": "omv.shared-folder.create",
            "target": FOLDER_PLAN_ID,
            "password": PASSWORD,
        },
        headers=_headers(),
    )
    assert issued.status_code == 200
    applied = client.post(
        "/api/appliance/omv/sharing/folders/apply",
        json=apply_body,
        headers={
            **_headers(),
            APPROVAL_HEADER: issued.json()["approvalToken"],
            "X-Echo-Intent": "task-folder-1",
        },
    )
    assert applied.status_code == 200
    assert applied.json()["verified"] is True
    records = [entry["payload"] for entry in audit.recent(30)]
    assert any(
        record["action"] == "omv.shared-folder.create"
        and record["outcome"] == "attempted"
        and record["metadata"]["mountPointRef"] == MOUNT_POINT_UUID
        and record["metadata"]["name"] == "Photos"
        and record["metadata"]["intentId"] == "task-folder-1"
        for record in records
    )
    assert any(
        record["action"] == "omv.shared-folder.create" and record["outcome"] == "succeeded"
        for record in records
    )
    assert PASSWORD not in str(records)
    assert ("apply_shared_folder", FOLDER_PLAN_ID) in stub.calls


def test_shared_folder_plan_rejects_paths_reserved_names_and_extra_fields_before_bridge() -> None:
    for desired in (
        {**_folder_desired(), "relativePath": "elsewhere"},
        _folder_desired(name="../escape"),
        _folder_desired(name="a..b"),
        _folder_desired(name="CON"),
    ):
        stub = _SharedFolderStubOmv()
        response = _client(stub).post(
            "/api/appliance/omv/sharing/folders/plan",
            json=desired,
            headers=_headers(),
        )
        assert response.status_code == 422
        assert not any(call[0] == "plan_shared_folder" for call in stub.calls)


def test_share_privilege_apply_requires_plan_bound_password_step_up_and_audits(tmp_path) -> None:
    audit = ApplianceAudit.from_data_dir(tmp_path, jwt_secret=JWT_SECRET)
    approval = HighRiskApprovalService(
        password_hash=PASSWORD_HASH,
        jwt_secret=JWT_SECRET,
        audit=audit,
        boot_nonce=b"p" * 32,
    )
    stub = _SharePrivilegeStubOmv()
    client = _client(stub, approval=approval, audit=audit)

    status = client.get("/api/appliance/omv/status", headers=_headers())
    plan = client.post(
        "/api/appliance/omv/sharing/privileges/plan",
        json=_privilege_desired(),
        headers=_headers(),
    )

    assert status.json()["readOnly"] is False
    assert status.json()["capabilities"] == ["shared-folder.privilege.simple.v1"]
    assert plan.status_code == 200
    assert plan.json()["planId"] == PRIVILEGE_PLAN_ID
    apply_body = {"desired": _privilege_desired(), "planId": PRIVILEGE_PLAN_ID}
    assert (
        client.post(
            "/api/appliance/omv/sharing/privileges/apply",
            json=apply_body,
            headers=_headers(),
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/appliance/omv/sharing/privileges/apply",
            json={**apply_body, "planId": "0" * 64},
            headers=_headers(),
        ).status_code
        == 409
    )

    issued = client.post(
        "/api/appliance/approvals",
        json={
            "action": "omv.share-privilege.apply",
            "target": PRIVILEGE_PLAN_ID,
            "password": PASSWORD,
        },
        headers=_headers(),
    )
    assert issued.status_code == 200
    applied = client.post(
        "/api/appliance/omv/sharing/privileges/apply",
        json=apply_body,
        headers={
            **_headers(),
            APPROVAL_HEADER: issued.json()["approvalToken"],
            "X-Echo-Intent": "task-share-access-1",
        },
    )

    assert applied.status_code == 200
    assert applied.json()["verified"] is True
    records = [entry["payload"] for entry in audit.recent(30)]
    assert any(
        record["action"] == "omv.share-privilege.apply"
        and record["outcome"] == "attempted"
        and record["metadata"]["sharedFolderRef"] == SHARE_UUID
        and record["metadata"]["principalType"] == "user"
        and record["metadata"]["principalName"] == "alice"
        and record["metadata"]["beforePermission"] == "inherit"
        and record["metadata"]["afterPermission"] == "readWrite"
        and record["metadata"]["intentId"] == "task-share-access-1"
        for record in records
    )
    assert any(
        record["action"] == "omv.share-privilege.apply" and record["outcome"] == "succeeded"
        for record in records
    )
    assert PASSWORD not in str(records)
    assert ("apply_share_privilege", PRIVILEGE_PLAN_ID) in stub.calls


def test_share_privilege_plan_rejects_arbitrary_or_malformed_principals_before_bridge() -> None:
    for desired in (
        {**_privilege_desired(), "recursive": True},
        _privilege_desired(principalType="everyone"),
        _privilege_desired(principalName="alice\nroot"),
        _privilege_desired(principalName=" alice"),
        _privilege_desired(permission="admin"),
    ):
        stub = _SharePrivilegeStubOmv()
        response = _client(stub).post(
            "/api/appliance/omv/sharing/privileges/plan",
            json=desired,
            headers=_headers(),
        )
        assert response.status_code == 422
        assert not any(call[0] == "plan_share_privilege" for call in stub.calls)


def test_smb_apply_requires_plan_bound_password_step_up_and_audits(tmp_path) -> None:
    audit = ApplianceAudit.from_data_dir(tmp_path, jwt_secret=JWT_SECRET)
    approval = HighRiskApprovalService(
        password_hash=PASSWORD_HASH,
        jwt_secret=JWT_SECRET,
        audit=audit,
        boot_nonce=b"s" * 32,
    )
    stub = _StubOmv(control_supported=True)
    client = _client(stub, approval=approval, audit=audit)

    status = client.get("/api/appliance/omv/status", headers=_headers())
    plan = client.post(
        "/api/appliance/omv/sharing/smb/plan",
        json=_desired(),
        headers=_headers(),
    )

    assert status.json()["readOnly"] is False
    assert status.json()["capabilities"] == ["smb.share.desired.v1"]
    assert plan.status_code == 200
    assert plan.json()["planId"] == PLAN_ID
    apply_body = {"desired": _desired(), "planId": PLAN_ID}
    assert (
        client.post(
            "/api/appliance/omv/sharing/smb/apply",
            json=apply_body,
            headers=_headers(),
        ).status_code
        == 403
    )

    issued = client.post(
        "/api/appliance/approvals",
        json={"action": "omv.smb.apply", "target": PLAN_ID, "password": PASSWORD},
        headers=_headers(),
    )
    assert issued.status_code == 200
    approval_token = issued.json()["approvalToken"]
    applied = client.post(
        "/api/appliance/omv/sharing/smb/apply",
        json=apply_body,
        headers={
            **_headers(),
            APPROVAL_HEADER: approval_token,
            "X-Echo-Intent": "task-omv-1",
        },
    )

    assert applied.status_code == 200
    assert applied.json()["applied"] is True
    assert (
        client.post(
            "/api/appliance/omv/sharing/smb/apply",
            json=apply_body,
            headers={**_headers(), APPROVAL_HEADER: approval_token},
        ).status_code
        == 403
    )
    records = [entry["payload"] for entry in audit.recent(30)]
    assert any(
        record["action"] == "omv.smb.apply"
        and record["outcome"] == "attempted"
        and record["target"] == PLAN_ID
        and record["metadata"]["intentId"] == "task-omv-1"
        for record in records
    )
    assert any(
        record["action"] == "omv.smb.apply" and record["outcome"] == "succeeded"
        for record in records
    )
    assert ("apply_smb_share", PLAN_ID) in stub.calls


def test_smb_plan_rejects_unknown_fields_before_calling_bridge() -> None:
    stub = _StubOmv(control_supported=True)
    response = _client(stub).post(
        "/api/appliance/omv/sharing/smb/plan",
        json={**_desired(), "guest": "only"},
        headers=_headers(),
    )

    assert response.status_code == 422
    assert not any(call[0] == "plan_smb_share" for call in stub.calls)


def test_nfs_apply_requires_plan_bound_password_step_up_and_audits(tmp_path) -> None:
    audit = ApplianceAudit.from_data_dir(tmp_path, jwt_secret=JWT_SECRET)
    approval = HighRiskApprovalService(
        password_hash=PASSWORD_HASH,
        jwt_secret=JWT_SECRET,
        audit=audit,
        boot_nonce=b"n" * 32,
    )
    stub = _NfsStubOmv()
    client = _client(stub, approval=approval, audit=audit)

    status = client.get("/api/appliance/omv/status", headers=_headers())
    plan = client.post(
        "/api/appliance/omv/sharing/nfs/plan",
        json=_nfs_desired(),
        headers=_headers(),
    )
    assert status.json()["capabilities"] == ["nfs.share.private-network.v1"]
    assert plan.status_code == 200
    apply_body = {"desired": _nfs_desired(), "planId": NFS_PLAN_ID}
    assert (
        client.post(
            "/api/appliance/omv/sharing/nfs/apply",
            json=apply_body,
            headers=_headers(),
        ).status_code
        == 403
    )

    issued = client.post(
        "/api/appliance/approvals",
        json={"action": "omv.nfs.apply", "target": NFS_PLAN_ID, "password": PASSWORD},
        headers=_headers(),
    )
    assert issued.status_code == 200
    applied = client.post(
        "/api/appliance/omv/sharing/nfs/apply",
        json=apply_body,
        headers={
            **_headers(),
            APPROVAL_HEADER: issued.json()["approvalToken"],
            "X-Echo-Intent": "task-nfs-1",
        },
    )
    assert applied.status_code == 200
    assert applied.json()["verified"] is True
    records = [entry["payload"] for entry in audit.recent(30)]
    assert any(
        record["action"] == "omv.nfs.apply"
        and record["outcome"] == "attempted"
        and record["metadata"]["clientCidr"] == "192.168.1.0/24"
        and record["metadata"]["intentId"] == "task-nfs-1"
        for record in records
    )
    assert any(
        record["action"] == "omv.nfs.apply" and record["outcome"] == "succeeded"
        for record in records
    )
    assert ("apply_nfs_share", NFS_PLAN_ID) in stub.calls


def test_nfs_plan_rejects_unknown_fields_before_calling_bridge() -> None:
    stub = _NfsStubOmv()
    response = _client(stub).post(
        "/api/appliance/omv/sharing/nfs/plan",
        json={**_nfs_desired(), "extraoptions": "no_root_squash"},
        headers=_headers(),
    )
    assert response.status_code == 422
    assert not any(call[0] == "plan_nfs_share" for call in stub.calls)


def test_quota_apply_requires_plan_bound_password_step_up_and_audits(tmp_path) -> None:
    audit = ApplianceAudit.from_data_dir(tmp_path, jwt_secret=JWT_SECRET)
    approval = HighRiskApprovalService(
        password_hash=PASSWORD_HASH,
        jwt_secret=JWT_SECRET,
        audit=audit,
        boot_nonce=b"q" * 32,
    )
    stub = _QuotaStubOmv()
    client = _client(stub, approval=approval, audit=audit)

    status = client.get("/api/appliance/omv/status", headers=_headers())
    plan = client.post(
        "/api/appliance/omv/quota/plan",
        json=_quota_desired(),
        headers=_headers(),
    )

    assert status.json()["readOnly"] is False
    assert status.json()["capabilities"] == ["filesystem.quota.user-group.v1"]
    assert plan.status_code == 200
    assert plan.json()["planId"] == QUOTA_PLAN_ID
    apply_body = {"desired": _quota_desired(), "planId": QUOTA_PLAN_ID}
    assert (
        client.post(
            "/api/appliance/omv/quota/apply",
            json=apply_body,
            headers=_headers(),
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/appliance/omv/quota/apply",
            json={**apply_body, "planId": "0" * 64},
            headers=_headers(),
        ).status_code
        == 409
    )

    wrong = client.post(
        "/api/appliance/approvals",
        json={"action": "omv.smb.apply", "target": QUOTA_PLAN_ID, "password": PASSWORD},
        headers=_headers(),
    )
    assert wrong.status_code == 200
    assert (
        client.post(
            "/api/appliance/omv/quota/apply",
            json=apply_body,
            headers={**_headers(), APPROVAL_HEADER: wrong.json()["approvalToken"]},
        ).status_code
        == 403
    )

    issued = client.post(
        "/api/appliance/approvals",
        json={"action": "omv.quota.apply", "target": QUOTA_PLAN_ID, "password": PASSWORD},
        headers=_headers(),
    )
    assert issued.status_code == 200
    approval_token = issued.json()["approvalToken"]
    applied = client.post(
        "/api/appliance/omv/quota/apply",
        json=apply_body,
        headers={
            **_headers(),
            APPROVAL_HEADER: approval_token,
            "X-Echo-Intent": "task-quota-1",
        },
    )

    assert applied.status_code == 200
    assert applied.json()["verified"] is True
    assert (
        client.post(
            "/api/appliance/omv/quota/apply",
            json=apply_body,
            headers={**_headers(), APPROVAL_HEADER: approval_token},
        ).status_code
        == 403
    )
    records = [entry["payload"] for entry in audit.recent(40)]
    assert any(
        record["action"] == "omv.quota.apply"
        and record["outcome"] == "attempted"
        and record["target"] == QUOTA_PLAN_ID
        and record["metadata"]["intentId"] == "task-quota-1"
        for record in records
    )
    assert any(
        record["action"] == "omv.quota.apply" and record["outcome"] == "succeeded"
        for record in records
    )
    assert PASSWORD not in str(records)
    assert ("apply_filesystem_quota", QUOTA_PLAN_ID) in stub.calls


def test_quota_plan_rejects_unknown_fields_invalid_units_and_bool_before_bridge() -> None:
    for desired in (
        {**_quota_desired(), "sharedFolderPath": "/srv/family"},
        _quota_desired(hardLimitBytes=1025),
        _quota_desired(hardLimitBytes=True),
        _quota_desired(filesystemUuid="not-an-omv-filesystem-uuid-value"),
    ):
        stub = _QuotaStubOmv()
        response = _client(stub).post(
            "/api/appliance/omv/quota/plan",
            json=desired,
            headers=_headers(),
        )

        assert response.status_code == 422
        assert not any(call[0] == "plan_filesystem_quota" for call in stub.calls)
