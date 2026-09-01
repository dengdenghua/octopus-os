"""Narrow Echo-side client for the host OpenMediaVault bridge."""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from typing import Any

import httpx

from appliance.omv_protocol import (
    GROUP_CONTROL_CAPABILITY,
    GROUP_DESIRED_SCHEMA,
    MAX_BRIDGE_RESPONSE_BYTES,
    NFS_CONTROL_CAPABILITY,
    NFS_DESIRED_SCHEMA,
    QUOTA_CONTROL_CAPABILITY,
    QUOTA_DESIRED_SCHEMA,
    SHARE_PRIVILEGE_CONTROL_CAPABILITY,
    SHARE_PRIVILEGE_DESIRED_SCHEMA,
    SHARED_FOLDER_CONTROL_CAPABILITY,
    SHARED_FOLDER_DESIRED_SCHEMA,
    SMB_CONTROL_CAPABILITY,
    SMB_DESIRED_SCHEMA,
    USER_CONTROL_CAPABILITY,
    USER_DESIRED_SCHEMA,
    USER_PASSWORD_CONTROL_CAPABILITY,
    USER_PASSWORD_DESIRED_SCHEMA,
    OmvControlRejected,
    OmvUnavailable,
    validate_account_name,
    validate_devicefile,
    validate_group_desired,
    validate_nfs_desired,
    validate_omv_uuid,
    validate_quota_desired,
    validate_share_privilege_desired,
    validate_shared_folder_desired,
    validate_smb_desired,
    validate_user_desired,
    validate_user_password_desired,
)
from appliance.omv_response import (
    _filesystem,
    _group_plan,
    _nfs_plan,
    _normalized_admin_url,
    _privilege,
    _protocol_share,
    _quota_plan,
    _raid_array,
    _share_privilege_plan,
    _shared_folder,
    _shared_folder_plan,
    _shared_folder_target,
    _sharing_group,
    _sharing_user,
    _smart,
    _smart_device,
    _smb_plan,
    _topology_device,
    _user_password_plan,
    _user_plan,
)


class OmvClient:
    """Use fixed bridge endpoints through one explicitly configured UDS."""

    def __init__(
        self,
        socket_path: str | None = None,
        *,
        timeout: float = 5.0,
        admin_url: str | None = None,
    ) -> None:
        configured = (
            socket_path if socket_path is not None else os.environ.get("ECHO_OMV_SOCKET", "")
        )
        self._socket_path = str(configured).strip()
        self._timeout = timeout
        self._admin_url = _normalized_admin_url(
            admin_url if admin_url is not None else os.environ.get("ECHO_OMV_ADMIN_URL")
        )

    @property
    def configured(self) -> bool:
        return bool(self._socket_path)

    @property
    def admin_url(self) -> str | None:
        return self._admin_url

    def _checked_socket(self) -> str:
        if not self.configured:
            raise OmvUnavailable("OMV integration is not configured")
        path = Path(self._socket_path)
        if not path.is_absolute() or path.is_symlink():
            raise OmvUnavailable("OMV bridge socket path is invalid")
        try:
            info = path.stat()
        except OSError as exc:
            raise OmvUnavailable("OMV constrained bridge is unavailable") from exc
        if not stat.S_ISSOCK(info.st_mode):
            raise OmvUnavailable("OMV bridge path is not a Unix socket")
        return self._socket_path

    def _request(
        self,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        socket_path = self._checked_socket()
        transport = httpx.HTTPTransport(uds=socket_path, retries=0)
        try:
            with httpx.Client(
                transport=transport,
                base_url="http://echo-omv",
                timeout=self._timeout,
            ) as client:
                if json_body is None:
                    response = client.get(path, params=params)
                else:
                    response = client.post(path, json=json_body)
        except httpx.HTTPError as exc:
            raise OmvUnavailable("OMV bridge is unreachable") from exc
        if response.status_code != 200:
            detail = "OMV control request was rejected"
            try:
                error = response.json().get("error")
                if isinstance(error, str) and 0 < len(error) <= 512:
                    detail = error
            except (AttributeError, ValueError):
                pass
            if response.status_code in {409, 422}:
                raise OmvControlRejected(response.status_code, detail)
            raise OmvUnavailable(f"OMV bridge returned HTTP {response.status_code}")
        content_length = response.headers.get("content-length", "")
        if content_length.isdecimal() and int(content_length) > MAX_BRIDGE_RESPONSE_BYTES:
            raise OmvUnavailable("OMV bridge response exceeded the safety limit")
        if len(response.content) > MAX_BRIDGE_RESPONSE_BYTES:
            raise OmvUnavailable("OMV bridge response exceeded the safety limit")
        try:
            payload = response.json()
        except ValueError as exc:
            raise OmvUnavailable("OMV bridge returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise OmvUnavailable("OMV bridge returned an invalid response")
        return payload

    def ping(self) -> bool:
        try:
            payload = self._request("/health")
        except OmvUnavailable:
            return False
        return payload == {"ok": True}

    def capabilities(self) -> list[str]:
        payload = self._request("/v1/capabilities")
        entries = payload.get("capabilities")
        if (
            not isinstance(entries, list)
            or len(entries) > 16
            or any(not isinstance(item, str) or len(item) > 128 for item in entries)
        ):
            raise OmvUnavailable("OMV bridge returned invalid capabilities")
        return entries

    def supports_smb_control(self) -> bool:
        try:
            return SMB_CONTROL_CAPABILITY in self.capabilities()
        except OmvUnavailable:
            return False

    def supports_shared_folder_control(self) -> bool:
        try:
            return SHARED_FOLDER_CONTROL_CAPABILITY in self.capabilities()
        except OmvUnavailable:
            return False

    def supports_share_privilege_control(self) -> bool:
        try:
            return SHARE_PRIVILEGE_CONTROL_CAPABILITY in self.capabilities()
        except OmvUnavailable:
            return False

    def supports_nfs_control(self) -> bool:
        try:
            return NFS_CONTROL_CAPABILITY in self.capabilities()
        except OmvUnavailable:
            return False

    def supports_quota_control(self) -> bool:
        try:
            return QUOTA_CONTROL_CAPABILITY in self.capabilities()
        except OmvUnavailable:
            return False

    def supports_group_control(self) -> bool:
        try:
            return GROUP_CONTROL_CAPABILITY in self.capabilities()
        except OmvUnavailable:
            return False

    def supports_user_control(self) -> bool:
        try:
            return USER_CONTROL_CAPABILITY in self.capabilities()
        except OmvUnavailable:
            return False

    def supports_user_password_control(self) -> bool:
        try:
            return USER_PASSWORD_CONTROL_CAPABILITY in self.capabilities()
        except OmvUnavailable:
            return False

    def filesystems(self) -> list[dict[str, Any]]:
        payload = self._request("/v1/filesystems")
        entries = payload.get("filesystems")
        if not isinstance(entries, list) or len(entries) > 1024:
            raise OmvUnavailable("OMV bridge returned an invalid filesystem list")
        return [_filesystem(item) for item in entries]

    def smart(self, devicefile: str) -> dict[str, Any]:
        validated = validate_devicefile(devicefile)
        payload = self._request("/v1/smart", params={"devicefile": validated})
        return _smart(payload.get("smart"), validated)

    def smart_devices(self) -> list[dict[str, Any]]:
        payload = self._request("/v1/smart/devices")
        entries = payload.get("devices")
        if not isinstance(entries, list) or len(entries) > 256:
            raise OmvUnavailable("OMV bridge returned an invalid SMART device list")
        return [_smart_device(item) for item in entries]

    def storage_topology(self) -> dict[str, list[dict[str, Any]]]:
        payload = self._request("/v1/storage-topology")
        devices = payload.get("devices")
        arrays = payload.get("arrays")
        if not isinstance(devices, list) or len(devices) > 1024:
            raise OmvUnavailable("OMV bridge returned an invalid topology list")
        if not isinstance(arrays, list) or len(arrays) > 256:
            raise OmvUnavailable("OMV bridge returned an invalid RAID list")
        return {
            "devices": [_topology_device(item) for item in devices],
            "arrays": [_raid_array(item) for item in arrays],
        }

    def sharing_overview(self) -> dict[str, Any]:
        payload = self._request("/v1/sharing")
        shared_folders = payload.get("sharedFolders")
        shared_folder_targets = payload.get("sharedFolderTargets")
        users = payload.get("users")
        groups = payload.get("groups")
        smb = payload.get("smb")
        nfs = payload.get("nfs")
        if (
            not isinstance(shared_folders, list)
            or len(shared_folders) > 1024
            or not isinstance(shared_folder_targets, list)
            or len(shared_folder_targets) > 128
            or not isinstance(users, list)
            or len(users) > 1024
            or not isinstance(groups, list)
            or len(groups) > 1024
        ):
            raise OmvUnavailable("OMV bridge returned an invalid sharing inventory")
        for service, name in ((smb, "SMB"), (nfs, "NFS")):
            if (
                not isinstance(service, dict)
                or not isinstance(service.get("enabled"), bool)
                or not isinstance(service.get("shares"), list)
                or len(service["shares"]) > 1024
            ):
                raise OmvUnavailable(f"OMV bridge returned an invalid {name} service")
        return {
            "sharedFolders": [_shared_folder(item) for item in shared_folders],
            "sharedFolderTargets": [_shared_folder_target(item) for item in shared_folder_targets],
            "users": [_sharing_user(item) for item in users],
            "groups": [_sharing_group(item) for item in groups],
            "smb": {
                "enabled": smb["enabled"],
                "shares": [_protocol_share(item, "SMB") for item in smb["shares"]],
            },
            "nfs": {
                "enabled": nfs["enabled"],
                "shares": [_protocol_share(item, "NFS") for item in nfs["shares"]],
            },
        }

    def share_privileges(self, share_uuid: str) -> list[dict[str, Any]]:
        validated = validate_omv_uuid(share_uuid)
        payload = self._request("/v1/sharing/privileges", params={"uuid": validated})
        entries = payload.get("privileges")
        if not isinstance(entries, list) or len(entries) > 2048:
            raise OmvUnavailable("OMV bridge returned an invalid privilege list")
        return [_privilege(item) for item in entries]

    def plan_group(self, desired_state: dict[str, Any]) -> dict[str, Any]:
        desired = validate_group_desired(desired_state)
        payload = self._request(
            "/v1/accounts/groups/plan",
            json_body={"desired": desired},
        )
        return _group_plan(payload, expected_desired=desired)

    def apply_group(
        self,
        desired_state: dict[str, Any],
        plan_id: str,
    ) -> dict[str, Any]:
        desired = validate_group_desired(desired_state)
        if re.fullmatch(r"[0-9a-f]{64}", plan_id) is None:
            raise ValueError("group plan ID is invalid")
        payload = self._request(
            "/v1/accounts/groups/apply",
            json_body={"desired": desired, "planId": plan_id},
        )
        result = _group_plan(payload, expected_desired=desired)
        if result.get("applied") is not True or result.get("verified") is not True:
            raise OmvUnavailable("OMV bridge returned an unverified group apply result")
        return result

    def plan_user(self, desired_state: dict[str, Any]) -> dict[str, Any]:
        desired = validate_user_desired(desired_state)
        payload = self._request(
            "/v1/accounts/users/plan",
            json_body={"desired": desired},
        )
        return _user_plan(payload, expected_desired=desired)

    def apply_user(
        self,
        desired_state: dict[str, Any],
        plan_id: str,
    ) -> dict[str, Any]:
        desired = validate_user_desired(desired_state)
        if re.fullmatch(r"[0-9a-f]{64}", plan_id) is None:
            raise ValueError("user plan ID is invalid")
        payload = self._request(
            "/v1/accounts/users/apply",
            json_body={"desired": desired, "planId": plan_id},
        )
        result = _user_plan(payload, expected_desired=desired)
        if result.get("applied") is not True or result.get("verified") is not True:
            raise OmvUnavailable("OMV bridge returned an unverified user apply result")
        return result

    def plan_user_password(self, desired_state: dict[str, Any]) -> dict[str, Any]:
        desired = validate_user_password_desired(desired_state)
        payload = self._request(
            "/v1/accounts/users/password/plan",
            json_body={"desired": desired},
        )
        return _user_password_plan(payload, expected_desired=desired)

    def apply_user_password(
        self,
        desired_state: dict[str, Any],
        plan_id: str,
    ) -> dict[str, Any]:
        desired = validate_user_password_desired(desired_state)
        if re.fullmatch(r"[0-9a-f]{64}", plan_id) is None:
            raise ValueError("user password plan ID is invalid")
        payload = self._request(
            "/v1/accounts/users/password/apply",
            json_body={"desired": desired, "planId": plan_id},
        )
        result = _user_password_plan(payload, expected_desired=desired)
        if result.get("applied") is not True or result.get("verified") is not True:
            raise OmvUnavailable("OMV bridge returned an unverified user password result")
        return result

    def plan_share_privilege(self, desired_state: dict[str, Any]) -> dict[str, Any]:
        desired = validate_share_privilege_desired(desired_state)
        payload = self._request(
            "/v1/sharing/privileges/plan",
            json_body={"desired": desired},
        )
        return _share_privilege_plan(payload, expected_desired=desired)

    def apply_share_privilege(
        self,
        desired_state: dict[str, Any],
        plan_id: str,
    ) -> dict[str, Any]:
        desired = validate_share_privilege_desired(desired_state)
        if re.fullmatch(r"[0-9a-f]{64}", plan_id) is None:
            raise ValueError("share privilege plan ID is invalid")
        payload = self._request(
            "/v1/sharing/privileges/apply",
            json_body={"desired": desired, "planId": plan_id},
        )
        result = _share_privilege_plan(payload, expected_desired=desired)
        if (
            not isinstance(result.get("applied"), bool)
            or result.get("verified") is not True
            or not isinstance(result.get("deployedServices"), list)
        ):
            raise OmvUnavailable("OMV bridge returned an unverified share privilege apply result")
        return result

    def plan_shared_folder(self, desired_state: dict[str, Any]) -> dict[str, Any]:
        desired = validate_shared_folder_desired(desired_state)
        payload = self._request(
            "/v1/sharing/folders/plan",
            json_body={"desired": desired},
        )
        return _shared_folder_plan(payload, expected_desired=desired)

    def apply_shared_folder(
        self,
        desired_state: dict[str, Any],
        plan_id: str,
    ) -> dict[str, Any]:
        desired = validate_shared_folder_desired(desired_state)
        if re.fullmatch(r"[0-9a-f]{64}", plan_id) is None:
            raise ValueError("shared folder plan ID is invalid")
        payload = self._request(
            "/v1/sharing/folders/apply",
            json_body={"desired": desired, "planId": plan_id},
        )
        result = _shared_folder_plan(payload, expected_desired=desired)
        if not isinstance(result.get("applied"), bool) or result.get("verified") is not True:
            raise OmvUnavailable("OMV bridge returned an unverified shared folder apply result")
        return result

    def plan_smb_share(self, desired_state: dict[str, Any]) -> dict[str, Any]:
        desired = validate_smb_desired(desired_state)
        payload = self._request(
            "/v1/sharing/smb/plan",
            json_body={"desired": desired},
        )
        return _smb_plan(payload, expected_desired=desired)

    def apply_smb_share(
        self,
        desired_state: dict[str, Any],
        plan_id: str,
    ) -> dict[str, Any]:
        desired = validate_smb_desired(desired_state)
        if re.fullmatch(r"[0-9a-f]{64}", plan_id) is None:
            raise ValueError("SMB plan ID is invalid")
        payload = self._request(
            "/v1/sharing/smb/apply",
            json_body={"desired": desired, "planId": plan_id},
        )
        result = _smb_plan(payload, expected_desired=desired)
        if not isinstance(result.get("applied"), bool) or result.get("verified") is not True:
            raise OmvUnavailable("OMV bridge returned an unverified SMB apply result")
        return result

    def plan_nfs_share(self, desired_state: dict[str, Any]) -> dict[str, Any]:
        desired = validate_nfs_desired(desired_state)
        payload = self._request(
            "/v1/sharing/nfs/plan",
            json_body={"desired": desired},
        )
        return _nfs_plan(payload, expected_desired=desired)

    def apply_nfs_share(
        self,
        desired_state: dict[str, Any],
        plan_id: str,
    ) -> dict[str, Any]:
        desired = validate_nfs_desired(desired_state)
        if re.fullmatch(r"[0-9a-f]{64}", plan_id) is None:
            raise ValueError("NFS plan ID is invalid")
        payload = self._request(
            "/v1/sharing/nfs/apply",
            json_body={"desired": desired, "planId": plan_id},
        )
        result = _nfs_plan(payload, expected_desired=desired)
        if not isinstance(result.get("applied"), bool) or result.get("verified") is not True:
            raise OmvUnavailable("OMV bridge returned an unverified NFS apply result")
        return result

    def plan_filesystem_quota(self, desired_state: dict[str, Any]) -> dict[str, Any]:
        desired = validate_quota_desired(desired_state)
        payload = self._request(
            "/v1/quota/plan",
            json_body={"desired": desired},
        )
        return _quota_plan(payload, expected_desired=desired)

    def apply_filesystem_quota(
        self,
        desired_state: dict[str, Any],
        plan_id: str,
    ) -> dict[str, Any]:
        desired = validate_quota_desired(desired_state)
        if re.fullmatch(r"[0-9a-f]{64}", plan_id) is None:
            raise ValueError("quota plan ID is invalid")
        payload = self._request(
            "/v1/quota/apply",
            json_body={"desired": desired, "planId": plan_id},
        )
        result = _quota_plan(payload, expected_desired=desired)
        if not isinstance(result.get("applied"), bool) or result.get("verified") is not True:
            raise OmvUnavailable("OMV bridge returned an unverified quota apply result")
        return result


__all__ = [
    "GROUP_CONTROL_CAPABILITY",
    "GROUP_DESIRED_SCHEMA",
    "NFS_CONTROL_CAPABILITY",
    "NFS_DESIRED_SCHEMA",
    "OmvClient",
    "OmvControlRejected",
    "OmvUnavailable",
    "QUOTA_CONTROL_CAPABILITY",
    "QUOTA_DESIRED_SCHEMA",
    "SHARED_FOLDER_CONTROL_CAPABILITY",
    "SHARED_FOLDER_DESIRED_SCHEMA",
    "SHARE_PRIVILEGE_CONTROL_CAPABILITY",
    "SHARE_PRIVILEGE_DESIRED_SCHEMA",
    "SMB_CONTROL_CAPABILITY",
    "SMB_DESIRED_SCHEMA",
    "USER_CONTROL_CAPABILITY",
    "USER_DESIRED_SCHEMA",
    "USER_PASSWORD_CONTROL_CAPABILITY",
    "USER_PASSWORD_DESIRED_SCHEMA",
    "validate_account_name",
    "validate_devicefile",
    "validate_group_desired",
    "validate_nfs_desired",
    "validate_omv_uuid",
    "validate_quota_desired",
    "validate_shared_folder_desired",
    "validate_share_privilege_desired",
    "validate_smb_desired",
    "validate_user_desired",
    "validate_user_password_desired",
]
