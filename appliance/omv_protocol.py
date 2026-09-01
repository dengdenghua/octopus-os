"""Versioned validation and normalization contract for the Echo OMV bridge."""

from __future__ import annotations

import ipaddress
import re
from typing import Any

MAX_BRIDGE_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_DEVICEFILE_LENGTH = 256
SHARED_FOLDER_DESIRED_SCHEMA = "echo.omv.shared-folder-desired.v1"
SHARED_FOLDER_PLAN_SCHEMA = "echo.omv.shared-folder-plan.v1"
SHARED_FOLDER_CONTROL_CAPABILITY = "shared-folder.create.simple.v1"
SHARE_PRIVILEGE_DESIRED_SCHEMA = "echo.omv.share-privilege-desired.v1"
SHARE_PRIVILEGE_PLAN_SCHEMA = "echo.omv.share-privilege-plan.v1"
SHARE_PRIVILEGE_CONTROL_CAPABILITY = "shared-folder.privilege.simple.v1"
SMB_DESIRED_SCHEMA = "echo.omv.smb-share-desired.v1"
SMB_PLAN_SCHEMA = "echo.omv.smb-share-plan.v1"
SMB_CONTROL_CAPABILITY = "smb.share.desired.v1"
NFS_DESIRED_SCHEMA = "echo.omv.nfs-share-desired.v1"
NFS_PLAN_SCHEMA = "echo.omv.nfs-share-plan.v1"
NFS_CONTROL_CAPABILITY = "nfs.share.private-network.v1"
QUOTA_DESIRED_SCHEMA = "echo.omv.filesystem-quota-desired.v1"
QUOTA_PLAN_SCHEMA = "echo.omv.filesystem-quota-plan.v1"
QUOTA_CONTROL_CAPABILITY = "filesystem.quota.user-group.v1"
GROUP_DESIRED_SCHEMA = "echo.omv.group-desired.v1"
GROUP_PLAN_SCHEMA = "echo.omv.group-plan.v1"
GROUP_CONTROL_CAPABILITY = "account.group.create.v1"
USER_DESIRED_SCHEMA = "echo.omv.user-desired.v1"
USER_PLAN_SCHEMA = "echo.omv.user-plan.v1"
USER_CONTROL_CAPABILITY = "account.user.create.v1"
USER_PASSWORD_DESIRED_SCHEMA = "echo.omv.user-password-desired.v1"  # nosec B105
USER_PASSWORD_PLAN_SCHEMA = "echo.omv.user-password-plan.v1"  # nosec B105
USER_PASSWORD_CONTROL_CAPABILITY = "account.user.password.reset.v1"  # nosec B105
HMAC_SAFETY_CONTRACT = "hmacBoundNeverReturnedOrAudited"
MAX_QUOTA_BYTES = 2**63 - 1
_DEVICEFILE_PATTERN = re.compile(r"/dev/[A-Za-z0-9._/+:-]+")
_OMV_UUID_PATTERN = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_PORTABLE_SHARE_NAME_PATTERN = re.compile(
    r"(?=.{1,64}\Z)[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9_-])?"
)
_ACCOUNT_NAME_PATTERN = re.compile(r"[a-z][a-z0-9_-]{0,31}")
_PROTECTED_ACCOUNT_NAMES = {
    "adm",
    "admin",
    "daemon",
    "docker",
    "echo",
    "echo-omv",
    "nobody",
    "openmediavault",
    "root",
    "ssh",
    "sudo",
    "users",
    "www-data",
}
_WINDOWS_RESERVED_NAMES = {
    "aux",
    "con",
    "nul",
    "prn",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}


class OmvUnavailable(RuntimeError):
    """The optional host bridge is not configured, reachable, or trustworthy."""


class OmvControlRejected(RuntimeError):
    """A valid constrained OMV control request cannot safely proceed."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _bounded_text(value: Any, maximum: int) -> str | None:
    if not isinstance(value, str) or len(value) > maximum:
        return None
    if any(character < " " for character in value):
        return None
    return value


def _optional_text(value: Any, maximum: int) -> str | None:
    if value is None:
        return None
    return _bounded_text(value, maximum)


def _integer(value: Any, *, maximum: int = 2**63 - 1) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        return None
    return value


def validate_devicefile(devicefile: str) -> str:
    if (
        not devicefile
        or len(devicefile) > MAX_DEVICEFILE_LENGTH
        or _DEVICEFILE_PATTERN.fullmatch(devicefile) is None
    ):
        raise ValueError("invalid OMV device path")
    return devicefile


def validate_omv_uuid(value: str) -> str:
    if _OMV_UUID_PATTERN.fullmatch(value) is None:
        raise ValueError("invalid OMV object UUID")
    return value


def validate_account_name(value: Any, kind: str) -> str:
    if not isinstance(value, str) or _ACCOUNT_NAME_PATTERN.fullmatch(value) is None:
        raise ValueError(
            f"{kind} name must start with a lowercase letter and use at most 32 lowercase letters, numbers, dash or underscore"
        )
    if value in _PROTECTED_ACCOUNT_NAMES or value.startswith("echo-"):
        raise ValueError(f"{kind} name is reserved by the NAS")
    return value


def validate_group_desired(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"schema", "name", "comment"}:
        raise ValueError("group desired state has unexpected fields")
    if value.get("schema") != GROUP_DESIRED_SCHEMA:
        raise ValueError("group desired-state schema is unsupported")
    comment = _bounded_text(value.get("comment"), 65)
    if comment is None:
        raise ValueError("group comment is invalid")
    return {
        "schema": GROUP_DESIRED_SCHEMA,
        "name": validate_account_name(value.get("name"), "group"),
        "comment": comment,
    }


def validate_user_desired(value: Any) -> dict[str, Any]:
    expected = {"schema", "name", "displayName", "password", "groups"}
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("user desired state has unexpected fields")
    if value.get("schema") != USER_DESIRED_SCHEMA:
        raise ValueError("user desired-state schema is unsupported")
    name = validate_account_name(value.get("name"), "user")
    display_name = _bounded_text(value.get("displayName"), 65)
    if display_name is None or not display_name.strip():
        raise ValueError("user display name is required")
    display_name = display_name.strip()
    password = value.get("password")
    if (
        not isinstance(password, str)
        or not 12 <= len(password) <= 128
        or any(character < " " for character in password)
        or password.casefold() == name.casefold()
    ):
        raise ValueError(
            "user password must be 12-128 characters, contain no controls, and differ from the account name"
        )
    categories = sum(
        bool(predicate(password))
        for predicate in (
            lambda text: any(character.islower() for character in text),
            lambda text: any(character.isupper() for character in text),
            lambda text: any(character.isdigit() for character in text),
            lambda text: any(not character.isalnum() for character in text),
        )
    )
    if len(password) < 20 and categories < 3:
        raise ValueError(
            "user password must be a 20-character passphrase or contain three character classes"
        )
    groups = value.get("groups")
    if not isinstance(groups, list) or len(groups) > 32:
        raise ValueError("user groups must be a bounded list")
    normalized_groups = [validate_account_name(group, "group") for group in groups]
    if normalized_groups != sorted(set(normalized_groups)):
        raise ValueError("user groups must be unique and sorted")
    return {
        "schema": USER_DESIRED_SCHEMA,
        "name": name,
        "displayName": display_name,
        "password": password,
        "groups": normalized_groups,
    }


def validate_user_password_desired(value: Any) -> dict[str, str]:
    expected = {"schema", "name", "password"}
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("user password desired state has unexpected fields")
    if value.get("schema") != USER_PASSWORD_DESIRED_SCHEMA:
        raise ValueError("user password desired-state schema is unsupported")
    name = validate_account_name(value.get("name"), "user")
    password = value.get("password")
    if (
        not isinstance(password, str)
        or not 12 <= len(password) <= 128
        or any(character < " " for character in password)
        or password.casefold() == name.casefold()
    ):
        raise ValueError(
            "user password must be 12-128 characters, contain no controls, and differ from the account name"
        )
    categories = sum(
        bool(predicate(password))
        for predicate in (
            lambda text: any(character.islower() for character in text),
            lambda text: any(character.isupper() for character in text),
            lambda text: any(character.isdigit() for character in text),
            lambda text: any(not character.isalnum() for character in text),
        )
    )
    if len(password) < 20 and categories < 3:
        raise ValueError(
            "user password must be a 20-character passphrase or contain three character classes"
        )
    return {
        "schema": USER_PASSWORD_DESIRED_SCHEMA,
        "name": name,
        "password": password,
    }


def validate_shared_folder_desired(value: Any) -> dict[str, Any]:
    expected = {"schema", "mountPointRef", "name", "comment"}
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("shared folder desired state has unexpected fields")
    if value.get("schema") != SHARED_FOLDER_DESIRED_SCHEMA:
        raise ValueError("shared folder desired-state schema is unsupported")
    mount_point_ref = value.get("mountPointRef")
    if not isinstance(mount_point_ref, str):
        raise ValueError("shared folder mount point UUID is invalid")
    name = value.get("name")
    if (
        not isinstance(name, str)
        or _PORTABLE_SHARE_NAME_PATTERN.fullmatch(name) is None
        or ".." in name
        or name.casefold() in _WINDOWS_RESERVED_NAMES
    ):
        raise ValueError("shared folder name is not portable across network clients")
    comment = _bounded_text(value.get("comment"), 512)
    if comment is None:
        raise ValueError("shared folder comment is invalid")
    return {
        "schema": SHARED_FOLDER_DESIRED_SCHEMA,
        "mountPointRef": validate_omv_uuid(mount_point_ref).lower(),
        "name": name,
        "comment": comment,
    }


def validate_share_privilege_desired(value: Any) -> dict[str, Any]:
    expected = {
        "schema",
        "sharedFolderRef",
        "principalType",
        "principalName",
        "permission",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("share privilege desired state has unexpected fields")
    if value.get("schema") != SHARE_PRIVILEGE_DESIRED_SCHEMA:
        raise ValueError("share privilege desired-state schema is unsupported")
    folder_ref = value.get("sharedFolderRef")
    if not isinstance(folder_ref, str):
        raise ValueError("share privilege folder UUID is invalid")
    principal_type = value.get("principalType")
    if principal_type not in {"user", "group"}:
        raise ValueError("share privilege principal type is invalid")
    principal_name = _bounded_text(value.get("principalName"), 255)
    if not principal_name or principal_name != principal_name.strip():
        raise ValueError("share privilege principal name is invalid")
    permission = value.get("permission")
    if permission not in {"inherit", "none", "read", "readWrite"}:
        raise ValueError("share privilege permission is invalid")
    return {
        "schema": SHARE_PRIVILEGE_DESIRED_SCHEMA,
        "sharedFolderRef": validate_omv_uuid(folder_ref).lower(),
        "principalType": principal_type,
        "principalName": principal_name,
        "permission": permission,
    }


def validate_smb_desired(value: Any) -> dict[str, Any]:
    expected = {
        "schema",
        "sharedFolderRef",
        "enabled",
        "readOnly",
        "browseable",
        "recycleBin",
        "comment",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("SMB desired state has unexpected fields")
    if value.get("schema") != SMB_DESIRED_SCHEMA:
        raise ValueError("SMB desired-state schema is unsupported")
    folder_ref = value.get("sharedFolderRef")
    if not isinstance(folder_ref, str):
        raise ValueError("SMB shared folder UUID is invalid")
    folder_ref = validate_omv_uuid(folder_ref).lower()
    for key in ("enabled", "readOnly", "browseable", "recycleBin"):
        if not isinstance(value.get(key), bool):
            raise ValueError(f"SMB desired field {key} must be boolean")
    comment = _bounded_text(value.get("comment"), 512)
    if comment is None:
        raise ValueError("SMB comment is invalid")
    return {
        "schema": SMB_DESIRED_SCHEMA,
        "sharedFolderRef": folder_ref,
        "enabled": value["enabled"],
        "readOnly": value["readOnly"],
        "browseable": value["browseable"],
        "recycleBin": value["recycleBin"],
        "comment": comment,
    }


def validate_private_network(value: Any) -> str:
    text = _bounded_text(value, 64)
    if text is None:
        raise ValueError("NFS client must be one private network in CIDR form")
    try:
        network = ipaddress.ip_network(text.strip(), strict=True)
    except ValueError as exc:
        raise ValueError("NFS client must be one canonical private network in CIDR form") from exc
    allowed = (
        network.subnet_of(ipaddress.ip_network("10.0.0.0/8"))
        or network.subnet_of(ipaddress.ip_network("172.16.0.0/12"))
        or network.subnet_of(ipaddress.ip_network("192.168.0.0/16"))
        if network.version == 4
        else network.subnet_of(ipaddress.ip_network("fc00::/7"))
    )
    if not allowed:
        raise ValueError("NFS client network must be RFC1918 or IPv6 ULA")
    return network.with_prefixlen


def validate_nfs_desired(value: Any) -> dict[str, Any]:
    expected = {"schema", "sharedFolderRef", "clientCidr", "readOnly", "comment"}
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("NFS desired state has unexpected fields")
    if value.get("schema") != NFS_DESIRED_SCHEMA:
        raise ValueError("NFS desired-state schema is unsupported")
    folder_ref = value.get("sharedFolderRef")
    if not isinstance(folder_ref, str):
        raise ValueError("NFS shared folder UUID is invalid")
    if not isinstance(value.get("readOnly"), bool):
        raise ValueError("NFS desired field readOnly must be boolean")
    comment = _bounded_text(value.get("comment"), 512)
    if comment is None:
        raise ValueError("NFS comment is invalid")
    return {
        "schema": NFS_DESIRED_SCHEMA,
        "sharedFolderRef": validate_omv_uuid(folder_ref).lower(),
        "clientCidr": validate_private_network(value.get("clientCidr")),
        "readOnly": value["readOnly"],
        "comment": comment,
    }


def validate_quota_desired(value: Any) -> dict[str, Any]:
    expected = {
        "schema",
        "filesystemUuid",
        "subjectType",
        "subjectName",
        "hardLimitBytes",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("quota desired state has unexpected fields")
    if value.get("schema") != QUOTA_DESIRED_SCHEMA:
        raise ValueError("quota desired-state schema is unsupported")
    filesystem_uuid = value.get("filesystemUuid")
    if not isinstance(filesystem_uuid, str):
        raise ValueError("quota filesystem UUID is invalid")
    filesystem_uuid = validate_omv_uuid(filesystem_uuid).lower()
    subject_type = value.get("subjectType")
    if subject_type not in {"user", "group"}:
        raise ValueError("quota subject type is invalid")
    subject_name = _bounded_text(value.get("subjectName"), 255)
    if subject_name is None or not subject_name.strip():
        raise ValueError("quota subject name is invalid")
    subject_name = subject_name.strip()
    hard_limit = value.get("hardLimitBytes")
    if (
        isinstance(hard_limit, bool)
        or not isinstance(hard_limit, int)
        or not 0 <= hard_limit <= MAX_QUOTA_BYTES
        or hard_limit != 0
        and (hard_limit < 1024 or hard_limit % 1024 != 0)
    ):
        raise ValueError("quota hard limit must be zero or a positive multiple of 1024 bytes")
    return {
        "schema": QUOTA_DESIRED_SCHEMA,
        "filesystemUuid": filesystem_uuid,
        "subjectType": subject_type,
        "subjectName": subject_name,
        "hardLimitBytes": hard_limit,
    }
