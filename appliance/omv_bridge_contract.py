"""Host-side validation and deterministic plan contract for the OMV bridge."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import uuid
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from typing import Any

from appliance.omv_bridge_errors import OmvBridgeError, OmvBridgeValidationError

MAX_RPC_OUTPUT_BYTES = 4 * 1024 * 1024
MAX_MDSTAT_BYTES = 256 * 1024
MAX_DEVICEFILE_LENGTH = 256
DEFAULT_RPC_TIMEOUT_SECONDS = 20
_DEVICEFILE_PATTERN = re.compile(r"/dev/[A-Za-z0-9._/+:-]+")
_BLOCK_TYPE_PATTERN = re.compile(r"[A-Za-z0-9._+-]{1,32}")
_MDSTAT_ARRAY_PATTERN = re.compile(r"^(md[A-Za-z0-9_.-]+)\s*:\s*(.+)$")
_OMV_UUID_PATTERN = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_PLAN_ID_PATTERN = re.compile(r"[0-9a-f]{64}")
_PORTABLE_SHARE_NAME_PATTERN = re.compile(
    r"(?=.{1,64}\Z)[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9_-])?"
)
_WINDOWS_RESERVED_NAMES = {
    "aux",
    "con",
    "nul",
    "prn",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}
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
MAX_SECRET_RPC_RESPONSE_BYTES = 1024 * 1024
DEFAULT_OMV_ENGINE_SOCKET = "/var/lib/openmediavault/engined.sock"
OMV_CONFIGOBJECT_NEW_UUID = "fa4b1c66-ef79-11e5-87a0-0002b3a176b4"
_QUOTA_UNIT_BYTES = {
    "B": 1,
    "KiB": 1024,
    "MiB": 1024**2,
    "GiB": 1024**3,
    "TiB": 1024**4,
    "PiB": 1024**5,
    "EiB": 1024**6,
}
_SMB_SHARE_FIELDS = {
    "uuid",
    "enable",
    "sharedfolderref",
    "comment",
    "guest",
    "readonly",
    "browseable",
    "recyclebin",
    "recyclemaxsize",
    "recyclemaxage",
    "hidedotfiles",
    "inheritacls",
    "inheritpermissions",
    "easupport",
    "storedosattributes",
    "hostsallow",
    "hostsdeny",
    "audit",
    "timemachine",
    "extraoptions",
}
_NFS_SHARE_FIELDS = {
    "uuid",
    "sharedfolderref",
    "mntentref",
    "client",
    "options",
    "extraoptions",
    "comment",
}
_NFS_MANAGED_EXTRA_OPTIONS = "sync,subtree_check,root_squash"
_PRIVILEGE_TO_PERMS = {
    "inherit": None,
    "none": 0,
    "read": 5,
    "readWrite": 7,
}
_PERMS_TO_PRIVILEGE = {value: key for key, value in _PRIVILEGE_TO_PERMS.items()}
_ALLOWED_RPC_CALLS = {
    ("Config", "applyChanges"),
    ("Config", "isDirty"),
    ("FileSystemMgmt", "enumerateMountedFilesystems"),
    ("FsTab", "get"),
    ("NFS", "getSettings"),
    ("NFS", "getShare"),
    ("NFS", "getShareList"),
    ("NFS", "setShare"),
    ("NFS", "deleteShare"),
    ("Quota", "get"),
    ("Quota", "setByTypeName"),
    ("ShareMgmt", "enumerateSharedFolders"),
    ("ShareMgmt", "get"),
    ("ShareMgmt", "getCandidates"),
    ("ShareMgmt", "getPrivileges"),
    ("ShareMgmt", "set"),
    ("ShareMgmt", "setPrivileges"),
    ("ShareMgmt", "delete"),
    ("SMB", "getSettings"),
    ("SMB", "getShare"),
    ("SMB", "getShareList"),
    ("SMB", "setShare"),
    ("SMB", "deleteShare"),
    ("Smart", "enumerateDevices"),
    ("Smart", "getInformation"),
    ("UserMgmt", "enumerateGroups"),
    ("UserMgmt", "enumerateUsers"),
    ("UserMgmt", "getGroup"),
    ("UserMgmt", "getSettings"),
    ("UserMgmt", "getUser"),
    ("UserMgmt", "setGroup"),
    ("UserMgmt", "deleteGroup"),
    ("UserMgmt", "deleteUser"),
}
_SHARE_LIST_PARAMS = {
    "start": 0,
    "limit": -1,
    "sortfield": "sharedfoldername",
    "sortdir": "ASC",
}
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


RpcRunner = Callable[[str, str, dict[str, Any]], Any]
SecretRpcRunner = Callable[[dict[str, Any]], Any]
TopologyRunner = Callable[[], Any]
MdstatReader = Callable[[], str]


def _safe_text(value: Any, *, maximum: int = 512) -> str:
    if not isinstance(value, str):
        return ""
    return "".join(character for character in value if character >= " ")[:maximum]


def _integer(value: Any, *, minimum: int = 0, maximum: int = 2**63 - 1) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if not minimum <= parsed <= maximum:
        return None
    return parsed


def _boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return value == 1
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "on"}
    return False


def _strict_text(value: Any, *, maximum: int) -> str:
    if not isinstance(value, str) or len(value) > maximum:
        raise OmvBridgeValidationError("OMV SMB text field is invalid")
    if any(character < " " for character in value):
        raise OmvBridgeValidationError("OMV SMB text field contains control characters")
    return value


def _account_name(value: Any, kind: str) -> str:
    if not isinstance(value, str) or _ACCOUNT_NAME_PATTERN.fullmatch(value) is None:
        raise OmvBridgeValidationError(
            f"{kind} name must start with a lowercase letter and use at most 32 lowercase letters, numbers, dash or underscore"
        )
    if value in _PROTECTED_ACCOUNT_NAMES or value.startswith("echo-"):
        raise OmvBridgeValidationError(f"{kind} name is reserved by the NAS")
    return value


def _validated_group_desired(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {"schema", "name", "comment"}:
        raise OmvBridgeValidationError("group desired state has unexpected fields")
    if value.get("schema") != GROUP_DESIRED_SCHEMA:
        raise OmvBridgeValidationError("group desired-state schema is unsupported")
    return {
        "schema": GROUP_DESIRED_SCHEMA,
        "name": _account_name(value.get("name"), "group"),
        "comment": _strict_text(value.get("comment"), maximum=65),
    }


def _validated_user_desired(value: Any) -> dict[str, Any]:
    expected = {"schema", "name", "displayName", "password", "groups"}
    if not isinstance(value, dict) or set(value) != expected:
        raise OmvBridgeValidationError("user desired state has unexpected fields")
    if value.get("schema") != USER_DESIRED_SCHEMA:
        raise OmvBridgeValidationError("user desired-state schema is unsupported")
    name = _account_name(value.get("name"), "user")
    display_name = _strict_text(value.get("displayName"), maximum=65).strip()
    if not display_name:
        raise OmvBridgeValidationError("user display name is required")
    password = value.get("password")
    if (
        not isinstance(password, str)
        or not 12 <= len(password) <= 128
        or any(character < " " for character in password)
        or password.casefold() == name.casefold()
    ):
        raise OmvBridgeValidationError(
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
        raise OmvBridgeValidationError(
            "user password must be a 20-character passphrase or contain three character classes"
        )
    groups = value.get("groups")
    if not isinstance(groups, list) or len(groups) > 32:
        raise OmvBridgeValidationError("user groups must be a bounded list")
    validated_groups = [_account_name(group, "group") for group in groups]
    if validated_groups != sorted(set(validated_groups)):
        raise OmvBridgeValidationError("user groups must be unique and sorted")
    return {
        "schema": USER_DESIRED_SCHEMA,
        "name": name,
        "displayName": display_name,
        "password": password,
        "groups": validated_groups,
    }


def _validated_user_password_desired(value: Any) -> dict[str, str]:
    expected = {"schema", "name", "password"}
    if not isinstance(value, dict) or set(value) != expected:
        raise OmvBridgeValidationError("user password desired state has unexpected fields")
    if value.get("schema") != USER_PASSWORD_DESIRED_SCHEMA:
        raise OmvBridgeValidationError("user password desired-state schema is unsupported")
    name = _account_name(value.get("name"), "user")
    password = value.get("password")
    if (
        not isinstance(password, str)
        or not 12 <= len(password) <= 128
        or any(character < " " for character in password)
        or password.casefold() == name.casefold()
    ):
        raise OmvBridgeValidationError(
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
        raise OmvBridgeValidationError(
            "user password must be a 20-character passphrase or contain three character classes"
        )
    return {
        "schema": USER_PASSWORD_DESIRED_SCHEMA,
        "name": name,
        "password": password,
    }


def _portable_share_name(value: Any) -> str:
    if not isinstance(value, str) or _PORTABLE_SHARE_NAME_PATTERN.fullmatch(value) is None:
        raise OmvBridgeValidationError(
            "shared folder name must be 1-64 portable letters, numbers, dot, dash or underscore"
        )
    if ".." in value:
        raise OmvBridgeValidationError("shared folder name cannot contain dot-dot")
    if value.casefold() in _WINDOWS_RESERVED_NAMES:
        raise OmvBridgeValidationError("shared folder name is reserved by network clients")
    return value


def _validated_shared_folder_desired(value: Any) -> dict[str, Any]:
    expected = {"schema", "mountPointRef", "name", "comment"}
    if not isinstance(value, dict) or set(value) != expected:
        raise OmvBridgeValidationError("shared folder desired state has unexpected fields")
    if value.get("schema") != SHARED_FOLDER_DESIRED_SCHEMA:
        raise OmvBridgeValidationError("shared folder desired-state schema is unsupported")
    mount_point_ref = value.get("mountPointRef")
    if not isinstance(mount_point_ref, str) or _OMV_UUID_PATTERN.fullmatch(mount_point_ref) is None:
        raise OmvBridgeValidationError("shared folder mount point UUID is invalid")
    return {
        "schema": SHARED_FOLDER_DESIRED_SCHEMA,
        "mountPointRef": mount_point_ref.lower(),
        "name": _portable_share_name(value.get("name")),
        "comment": _strict_text(value.get("comment"), maximum=512),
    }


def _validated_share_privilege_desired(value: Any) -> dict[str, str]:
    expected = {
        "schema",
        "sharedFolderRef",
        "principalType",
        "principalName",
        "permission",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise OmvBridgeValidationError("share privilege desired state has unexpected fields")
    if value.get("schema") != SHARE_PRIVILEGE_DESIRED_SCHEMA:
        raise OmvBridgeValidationError("share privilege desired-state schema is unsupported")
    folder_ref = value.get("sharedFolderRef")
    if not isinstance(folder_ref, str) or _OMV_UUID_PATTERN.fullmatch(folder_ref) is None:
        raise OmvBridgeValidationError("share privilege folder UUID is invalid")
    principal_type = value.get("principalType")
    if principal_type not in {"user", "group"}:
        raise OmvBridgeValidationError("share privilege principal type is invalid")
    raw_name = value.get("principalName")
    if (
        not isinstance(raw_name, str)
        or not raw_name
        or len(raw_name) > 255
        or raw_name != raw_name.strip()
        or any(character < " " for character in raw_name)
    ):
        raise OmvBridgeValidationError("share privilege principal name is invalid")
    permission = value.get("permission")
    if permission not in _PRIVILEGE_TO_PERMS:
        raise OmvBridgeValidationError("share privilege permission is invalid")
    return {
        "schema": SHARE_PRIVILEGE_DESIRED_SCHEMA,
        "sharedFolderRef": folder_ref.lower(),
        "principalType": principal_type,
        "principalName": raw_name,
        "permission": permission,
    }


def _validated_mount_point(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise OmvBridgeValidationError("OMV mount point is invalid")
    mount_uuid = value.get("uuid")
    if not isinstance(mount_uuid, str) or _OMV_UUID_PATTERN.fullmatch(mount_uuid) is None:
        raise OmvBridgeValidationError("OMV mount point UUID is invalid")
    directory = _strict_text(value.get("dir"), maximum=4096)
    filesystem_type = _strict_text(value.get("type"), maximum=64)
    if not directory.startswith("/srv/") or directory.endswith("/") or not filesystem_type:
        raise OmvBridgeValidationError("OMV mount point is outside the managed storage root")
    return {
        "uuid": mount_uuid.lower(),
        "dir": directory,
        "type": filesystem_type,
    }


def _validated_shared_folder_config(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise OmvBridgeValidationError("OMV shared folder configuration is invalid")
    share_uuid = value.get("uuid")
    mount_ref = value.get("mntentref")
    if (
        not isinstance(share_uuid, str)
        or _OMV_UUID_PATTERN.fullmatch(share_uuid) is None
        or not isinstance(mount_ref, str)
        or _OMV_UUID_PATTERN.fullmatch(mount_ref) is None
    ):
        raise OmvBridgeValidationError("OMV shared folder identity is invalid")
    name = _portable_share_name(value.get("name"))
    relative_path = _strict_text(value.get("reldirpath"), maximum=4096)
    comment = _strict_text(value.get("comment"), maximum=512)
    return {
        "uuid": share_uuid.lower(),
        "mntentref": mount_ref.lower(),
        "name": name,
        "reldirpath": relative_path,
        "comment": comment,
    }


def _validated_smb_share(value: Any) -> dict[str, Any]:
    """Return the exact OMV setShare shape or fail closed.

    OMV may add display-only fields to getShare responses. They must never be
    reflected into a write request, so this function explicitly reconstructs
    the documented RPC schema.
    """

    if not isinstance(value, dict):
        raise OmvBridgeValidationError("OMV SMB share must be an object")
    required = _SMB_SHARE_FIELDS - {"timemachine"}
    if not required.issubset(value):
        raise OmvBridgeValidationError("OMV SMB share is incomplete")
    result: dict[str, Any] = {}
    for key in ("uuid", "sharedfolderref"):
        item = value.get(key)
        if not isinstance(item, str) or _OMV_UUID_PATTERN.fullmatch(item) is None:
            raise OmvBridgeValidationError("OMV SMB share UUID is invalid")
        result[key] = item.lower()
    for key in (
        "enable",
        "readonly",
        "browseable",
        "recyclebin",
        "hidedotfiles",
        "inheritacls",
        "inheritpermissions",
        "easupport",
        "storedosattributes",
        "audit",
    ):
        item = value.get(key)
        if not isinstance(item, bool):
            raise OmvBridgeValidationError("OMV SMB share flag is invalid")
        result[key] = item
    timemachine = value.get("timemachine", False)
    if not isinstance(timemachine, bool):
        raise OmvBridgeValidationError("OMV SMB Time Machine flag is invalid")
    result["timemachine"] = timemachine
    for key in ("recyclemaxsize", "recyclemaxage"):
        item = value.get(key)
        if isinstance(item, bool) or not isinstance(item, int) or not 0 <= item <= 2**31 - 1:
            raise OmvBridgeValidationError("OMV SMB recycle limit is invalid")
        result[key] = item
    guest = value.get("guest")
    if guest not in {"no", "allow", "only"}:
        raise OmvBridgeValidationError("OMV SMB guest mode is invalid")
    result["guest"] = guest
    for key, maximum in (
        ("comment", 512),
        ("hostsallow", 1024),
        ("hostsdeny", 1024),
        ("extraoptions", 4096),
    ):
        result[key] = _strict_text(value.get(key), maximum=maximum)
    return result


def _validated_smb_desired(value: Any) -> dict[str, Any]:
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
        raise OmvBridgeValidationError("SMB desired state has unexpected fields")
    if value.get("schema") != SMB_DESIRED_SCHEMA:
        raise OmvBridgeValidationError("SMB desired-state schema is unsupported")
    folder_ref = value.get("sharedFolderRef")
    if not isinstance(folder_ref, str) or _OMV_UUID_PATTERN.fullmatch(folder_ref) is None:
        raise OmvBridgeValidationError("SMB shared folder UUID is invalid")
    for key in ("enabled", "readOnly", "browseable", "recycleBin"):
        if not isinstance(value.get(key), bool):
            raise OmvBridgeValidationError(f"SMB desired field {key} must be boolean")
    comment = _strict_text(value.get("comment"), maximum=512)
    return {
        "schema": SMB_DESIRED_SCHEMA,
        "sharedFolderRef": folder_ref.lower(),
        "enabled": value["enabled"],
        "readOnly": value["readOnly"],
        "browseable": value["browseable"],
        "recycleBin": value["recycleBin"],
        "comment": comment,
    }


def _private_network(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 64 or any(c < " " for c in value):
        raise OmvBridgeValidationError("NFS client must be one private network in CIDR form")
    try:
        network = ipaddress.ip_network(value.strip(), strict=True)
    except ValueError as exc:
        raise OmvBridgeValidationError(
            "NFS client must be one canonical private network in CIDR form"
        ) from exc
    allowed = (
        network.subnet_of(ipaddress.ip_network("10.0.0.0/8"))
        or network.subnet_of(ipaddress.ip_network("172.16.0.0/12"))
        or network.subnet_of(ipaddress.ip_network("192.168.0.0/16"))
        if network.version == 4
        else network.subnet_of(ipaddress.ip_network("fc00::/7"))
    )
    if not allowed:
        raise OmvBridgeValidationError("NFS client network must be RFC1918 or IPv6 ULA")
    return network.with_prefixlen


def _validated_nfs_share(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not _NFS_SHARE_FIELDS.issubset(value):
        raise OmvBridgeValidationError("OMV NFS share is incomplete")
    result: dict[str, Any] = {}
    for key in ("uuid", "sharedfolderref", "mntentref"):
        item = value.get(key)
        if not isinstance(item, str) or _OMV_UUID_PATTERN.fullmatch(item) is None:
            raise OmvBridgeValidationError("OMV NFS share UUID is invalid")
        result[key] = item.lower()
    result["client"] = _strict_text(value.get("client"), maximum=512)
    result["options"] = _strict_text(value.get("options"), maximum=64)
    result["extraoptions"] = _strict_text(value.get("extraoptions"), maximum=4096)
    result["comment"] = _strict_text(value.get("comment"), maximum=512)
    return result


def _validated_nfs_desired(value: Any) -> dict[str, Any]:
    expected = {
        "schema",
        "sharedFolderRef",
        "clientCidr",
        "readOnly",
        "comment",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise OmvBridgeValidationError("NFS desired state has unexpected fields")
    if value.get("schema") != NFS_DESIRED_SCHEMA:
        raise OmvBridgeValidationError("NFS desired-state schema is unsupported")
    folder_ref = value.get("sharedFolderRef")
    if not isinstance(folder_ref, str) or _OMV_UUID_PATTERN.fullmatch(folder_ref) is None:
        raise OmvBridgeValidationError("NFS shared folder UUID is invalid")
    if not isinstance(value.get("readOnly"), bool):
        raise OmvBridgeValidationError("NFS desired field readOnly must be boolean")
    return {
        "schema": NFS_DESIRED_SCHEMA,
        "sharedFolderRef": folder_ref.lower(),
        "clientCidr": _private_network(value.get("clientCidr")),
        "readOnly": value["readOnly"],
        "comment": _strict_text(value.get("comment"), maximum=512),
    }


def _validated_quota_desired(value: Any) -> dict[str, Any]:
    expected = {
        "schema",
        "filesystemUuid",
        "subjectType",
        "subjectName",
        "hardLimitBytes",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise OmvBridgeValidationError("quota desired state has unexpected fields")
    if value.get("schema") != QUOTA_DESIRED_SCHEMA:
        raise OmvBridgeValidationError("quota desired-state schema is unsupported")
    filesystem_uuid = value.get("filesystemUuid")
    if not isinstance(filesystem_uuid, str) or _OMV_UUID_PATTERN.fullmatch(filesystem_uuid) is None:
        raise OmvBridgeValidationError("quota filesystem UUID is invalid")
    subject_type = value.get("subjectType")
    if subject_type not in {"user", "group"}:
        raise OmvBridgeValidationError("quota subject type is invalid")
    raw_subject_name = value.get("subjectName")
    if (
        not isinstance(raw_subject_name, str)
        or len(raw_subject_name) > 255
        or any(character < " " for character in raw_subject_name)
    ):
        raise OmvBridgeValidationError("quota subject name is invalid")
    subject_name = raw_subject_name.strip()
    if not subject_name:
        raise OmvBridgeValidationError("quota subject name is invalid")
    hard_limit = value.get("hardLimitBytes")
    if (
        isinstance(hard_limit, bool)
        or not isinstance(hard_limit, int)
        or not 0 <= hard_limit <= MAX_QUOTA_BYTES
        or hard_limit not in {0}
        and (hard_limit < 1024 or hard_limit % 1024 != 0)
    ):
        raise OmvBridgeValidationError(
            "quota hard limit must be zero or a positive multiple of 1024 bytes"
        )
    return {
        "schema": QUOTA_DESIRED_SCHEMA,
        "filesystemUuid": filesystem_uuid.lower(),
        "subjectType": subject_type,
        "subjectName": subject_name,
        "hardLimitBytes": hard_limit,
    }


def _quota_limit_bytes(value: Any, unit: Any) -> int:
    if isinstance(value, bool) or unit not in _QUOTA_UNIT_BYTES:
        raise OmvBridgeError("OMV quota limit is invalid")
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise OmvBridgeError("OMV quota limit is invalid") from exc
    exact = decimal * _QUOTA_UNIT_BYTES[unit]
    if not decimal.is_finite() or decimal < 0 or exact != exact.to_integral_value():
        raise OmvBridgeError("OMV quota limit is invalid")
    result = int(exact)
    if result > MAX_QUOTA_BYTES:
        raise OmvBridgeError("OMV quota limit exceeds the supported range")
    return result


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _privilege_sort_key(value: dict[str, Any]) -> tuple[int, str]:
    return (0 if value["type"] == "user" else 1, value["name"])


def _uuid_from_plan(plan_id: str) -> str:
    raw = bytearray(bytes.fromhex(plan_id[:32]))
    raw[6] = (raw[6] & 0x0F) | 0x40
    raw[8] = (raw[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(raw)))
