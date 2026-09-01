"""Fail-closed response normalization for the Echo OMV bridge contract."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from appliance.omv_protocol import (
    GROUP_PLAN_SCHEMA,
    HMAC_SAFETY_CONTRACT,
    MAX_DEVICEFILE_LENGTH,
    MAX_QUOTA_BYTES,
    NFS_PLAN_SCHEMA,
    QUOTA_PLAN_SCHEMA,
    SHARE_PRIVILEGE_PLAN_SCHEMA,
    SHARED_FOLDER_PLAN_SCHEMA,
    SMB_PLAN_SCHEMA,
    USER_DESIRED_SCHEMA,
    USER_PASSWORD_DESIRED_SCHEMA,
    USER_PASSWORD_PLAN_SCHEMA,
    USER_PLAN_SCHEMA,
    OmvUnavailable,
    _bounded_text,
    _integer,
    _optional_text,
    validate_devicefile,
    validate_group_desired,
    validate_nfs_desired,
    validate_omv_uuid,
    validate_quota_desired,
    validate_share_privilege_desired,
    validate_shared_folder_desired,
    validate_smb_desired,
)


def _normalized_admin_url(value: str | None) -> str | None:
    configured = str(value or "").strip()
    if not configured:
        return None
    parsed = urlsplit(configured)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        return None
    return configured.rstrip("/")


def _filesystem(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise OmvUnavailable("OMV bridge returned an invalid filesystem entry")
    devicefile = _bounded_text(item.get("devicefile"), MAX_DEVICEFILE_LENGTH)
    mountpoint = _bounded_text(item.get("mountpoint"), 4096)
    size = _integer(item.get("sizeBytes"))
    available = _integer(item.get("availableBytes"))
    used_percent = item.get("usedPercent")
    if used_percent is not None:
        used_percent = _integer(used_percent, maximum=100)
    if not devicefile or not mountpoint or size is None or available is None:
        raise OmvUnavailable("OMV bridge returned an incomplete filesystem entry")
    if available > size:
        raise OmvUnavailable("OMV bridge returned impossible filesystem capacity")
    if used_percent is None and item.get("usedPercent") is not None:
        raise OmvUnavailable("OMV bridge returned an invalid usage percentage")
    for key in ("readOnly", "supportsAcl", "supportsQuota"):
        if not isinstance(item.get(key), bool):
            raise OmvUnavailable("OMV bridge returned an invalid filesystem flag")
    result = {
        "devicefile": validate_devicefile(devicefile),
        "parentdevicefile": _optional_text(item.get("parentdevicefile"), MAX_DEVICEFILE_LENGTH),
        "uuid": _optional_text(item.get("uuid"), 128),
        "label": _bounded_text(item.get("label"), 256),
        "type": _bounded_text(item.get("type"), 64),
        "mountpoint": mountpoint,
        "sizeBytes": size,
        "availableBytes": available,
        "usedPercent": used_percent,
        "readOnly": item["readOnly"],
        "supportsAcl": item["supportsAcl"],
        "supportsQuota": item["supportsQuota"],
    }
    if result["label"] is None or result["type"] is None:
        raise OmvUnavailable("OMV bridge returned an invalid filesystem label")
    parent = result["parentdevicefile"]
    if parent is not None:
        validate_devicefile(parent)
    return result


def _smart(item: Any, expected_devicefile: str) -> dict[str, Any]:
    if not isinstance(item, dict) or item.get("devicefile") != expected_devicefile:
        raise OmvUnavailable("OMV bridge returned an invalid SMART response")
    model = _bounded_text(item.get("model"), 256)
    health = _bounded_text(item.get("health"), 128)
    if model is None or not health:
        raise OmvUnavailable("OMV bridge returned incomplete SMART information")
    result: dict[str, Any] = {
        "devicefile": expected_devicefile,
        "model": model,
        "health": health,
    }
    for key, maximum in (
        ("temperatureC", 300),
        ("powerOnHours", 2**63 - 1),
        ("powerCycles", 2**63 - 1),
    ):
        value = item.get(key)
        if value is not None:
            value = _integer(value, maximum=maximum)
            if value is None:
                raise OmvUnavailable("OMV bridge returned an invalid SMART counter")
        result[key] = value
    return result


def _smart_device(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise OmvUnavailable("OMV bridge returned an invalid SMART device")
    devicefile = _bounded_text(item.get("devicefile"), MAX_DEVICEFILE_LENGTH)
    model = _bounded_text(item.get("model"), 256)
    health = _bounded_text(item.get("health"), 128)
    size = item.get("sizeBytes")
    temperature = item.get("temperatureC")
    if not devicefile or model is None or not health:
        raise OmvUnavailable("OMV bridge returned an incomplete SMART device")
    validate_devicefile(devicefile)
    if size is not None:
        size = _integer(size)
        if size is None:
            raise OmvUnavailable("OMV bridge returned an invalid disk size")
    if temperature is not None:
        temperature = _integer(temperature, maximum=300)
        if temperature is None:
            raise OmvUnavailable("OMV bridge returned an invalid disk temperature")
    return {
        "devicefile": devicefile,
        "model": model,
        "sizeBytes": size,
        "health": health,
        "temperatureC": temperature,
    }


def _topology_device(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise OmvUnavailable("OMV bridge returned an invalid topology device")
    devicefile = _bounded_text(item.get("devicefile"), MAX_DEVICEFILE_LENGTH)
    block_type = _bounded_text(item.get("type"), 32)
    filesystem_type = _optional_text(item.get("filesystemType"), 64)
    size = item.get("sizeBytes")
    rotational = item.get("rotational")
    parents = item.get("parentDevicefiles")
    if not devicefile or not block_type or not isinstance(parents, list):
        raise OmvUnavailable("OMV bridge returned an incomplete topology device")
    validate_devicefile(devicefile)
    if size is not None:
        size = _integer(size)
        if size is None:
            raise OmvUnavailable("OMV bridge returned an invalid topology size")
    if rotational is not None and not isinstance(rotational, bool):
        raise OmvUnavailable("OMV bridge returned an invalid rotation flag")
    if len(parents) > 32:
        raise OmvUnavailable("OMV bridge returned too many topology parents")
    validated_parents: list[str] = []
    for parent in parents:
        parent_text = _bounded_text(parent, MAX_DEVICEFILE_LENGTH)
        if not parent_text or parent_text == devicefile:
            raise OmvUnavailable("OMV bridge returned an invalid topology parent")
        validated_parents.append(validate_devicefile(parent_text))
    if len(set(validated_parents)) != len(validated_parents):
        raise OmvUnavailable("OMV bridge returned duplicate topology parents")
    return {
        "devicefile": devicefile,
        "type": block_type,
        "sizeBytes": size,
        "filesystemType": filesystem_type,
        "rotational": rotational,
        "parentDevicefiles": validated_parents,
    }


def _raid_array(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise OmvUnavailable("OMV bridge returned an invalid RAID array")
    devicefile = _bounded_text(item.get("devicefile"), MAX_DEVICEFILE_LENGTH)
    level = _bounded_text(item.get("level"), 32)
    status = _bounded_text(item.get("status"), 32)
    operation = _optional_text(item.get("operation"), 32)
    total = item.get("totalDevices")
    active = item.get("activeDevices")
    progress = item.get("operationPercent")
    if not devicefile or not level or not status:
        raise OmvUnavailable("OMV bridge returned an incomplete RAID array")
    validate_devicefile(devicefile)
    for value in (total, active):
        if value is not None and _integer(value, maximum=1024) is None:
            raise OmvUnavailable("OMV bridge returned an invalid RAID member count")
    if (total is None) != (active is None) or (
        total is not None and active is not None and active > total
    ):
        raise OmvUnavailable("OMV bridge returned impossible RAID member counts")
    if progress is not None and _integer(progress, maximum=100) is None:
        raise OmvUnavailable("OMV bridge returned invalid RAID progress")
    return {
        "devicefile": devicefile,
        "level": level,
        "status": status,
        "totalDevices": total,
        "activeDevices": active,
        "operation": operation,
        "operationPercent": progress,
    }


def _shared_folder(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise OmvUnavailable("OMV bridge returned an invalid shared folder")
    share_uuid = _bounded_text(item.get("uuid"), 36)
    name = _bounded_text(item.get("name"), 255)
    comment = _bounded_text(item.get("comment"), 512)
    relative_path = _bounded_text(item.get("relativePath"), 4096)
    device = _bounded_text(item.get("device"), 256)
    status = _bounded_text(item.get("status"), 64)
    if not share_uuid or not name or None in (comment, relative_path, device, status):
        raise OmvUnavailable("OMV bridge returned an incomplete shared folder")
    try:
        validate_omv_uuid(share_uuid)
    except ValueError as exc:
        raise OmvUnavailable("OMV bridge returned an invalid SMB share UUID") from exc
    if not isinstance(item.get("inUse"), bool) or not isinstance(item.get("supportsAcl"), bool):
        raise OmvUnavailable("OMV bridge returned an invalid shared folder flag")
    return {
        "uuid": share_uuid,
        "name": name,
        "comment": comment,
        "relativePath": relative_path,
        "device": device,
        "status": status,
        "inUse": item["inUse"],
        "supportsAcl": item["supportsAcl"],
    }


def _shared_folder_target(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict) or set(item) != {
        "mountPointRef",
        "filesystemUuid",
        "label",
        "type",
        "sizeBytes",
        "availableBytes",
        "readOnly",
    }:
        raise OmvUnavailable("OMV bridge returned an invalid shared folder target")
    mount_ref = _bounded_text(item.get("mountPointRef"), 36)
    filesystem_uuid = _optional_text(item.get("filesystemUuid"), 128)
    label = _bounded_text(item.get("label"), 256)
    filesystem_type = _bounded_text(item.get("type"), 64)
    size = _integer(item.get("sizeBytes"))
    available = _integer(item.get("availableBytes"))
    if (
        not mount_ref
        or label is None
        or not filesystem_type
        or size is None
        or available is None
        or available > size
        or item.get("readOnly") is not False
    ):
        raise OmvUnavailable("OMV bridge returned an incomplete shared folder target")
    try:
        validate_omv_uuid(mount_ref)
    except ValueError as exc:
        raise OmvUnavailable("OMV bridge returned an invalid mount point UUID") from exc
    return {
        "mountPointRef": mount_ref.lower(),
        "filesystemUuid": filesystem_uuid,
        "label": label,
        "type": filesystem_type,
        "sizeBytes": size,
        "availableBytes": available,
        "readOnly": False,
    }


def _sharing_user(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise OmvUnavailable("OMV bridge returned an invalid NAS user")
    name = _bounded_text(item.get("name"), 255)
    comment = _bounded_text(item.get("comment"), 512)
    uid = _integer(item.get("uid"), maximum=2**31 - 1)
    gid = _integer(item.get("gid"), maximum=2**31 - 1)
    groups = item.get("groups")
    if not name or comment is None or uid is None or gid is None or not isinstance(groups, list):
        raise OmvUnavailable("OMV bridge returned an incomplete NAS user")
    if len(groups) > 64:
        raise OmvUnavailable("OMV bridge returned too many groups for a NAS user")
    validated_groups = [_bounded_text(value, 255) for value in groups]
    if any(not value for value in validated_groups):
        raise OmvUnavailable("OMV bridge returned an invalid NAS group name")
    return {
        "name": name,
        "uid": uid,
        "gid": gid,
        "comment": comment,
        "groups": validated_groups,
    }


def _sharing_group(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise OmvUnavailable("OMV bridge returned an invalid NAS group")
    name = _bounded_text(item.get("name"), 255)
    gid = _integer(item.get("gid"), maximum=2**31 - 1)
    members = item.get("members")
    if not name or gid is None or not isinstance(members, list) or len(members) > 1024:
        raise OmvUnavailable("OMV bridge returned an incomplete NAS group")
    validated_members = [_bounded_text(value, 255) for value in members]
    if any(not value for value in validated_members):
        raise OmvUnavailable("OMV bridge returned an invalid NAS group member")
    return {"name": name, "gid": gid, "members": validated_members}


def _protocol_share(item: Any, protocol: str) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise OmvUnavailable(f"OMV bridge returned an invalid {protocol} share")
    share_uuid = _bounded_text(item.get("uuid"), 36)
    folder_ref = _bounded_text(item.get("sharedFolderRef"), 36)
    folder_name = _bounded_text(item.get("sharedFolderName"), 255)
    if not share_uuid or not folder_ref or folder_name is None:
        raise OmvUnavailable(f"OMV bridge returned an incomplete {protocol} share")
    validate_omv_uuid(share_uuid)
    validate_omv_uuid(folder_ref)
    if protocol == "SMB":
        for key in ("enabled", "readOnly", "browseable", "recycleBin"):
            if not isinstance(item.get(key), bool):
                raise OmvUnavailable("OMV bridge returned an invalid SMB flag")
        guest = _bounded_text(item.get("guest"), 32)
        comment = _bounded_text(item.get("comment"), 512)
        if not guest or comment is None:
            raise OmvUnavailable("OMV bridge returned incomplete SMB settings")
        return {
            "uuid": share_uuid,
            "sharedFolderRef": folder_ref,
            "sharedFolderName": folder_name,
            "enabled": item["enabled"],
            "readOnly": item["readOnly"],
            "guest": guest,
            "browseable": item["browseable"],
            "recycleBin": item["recycleBin"],
            "comment": comment,
        }
    client = _bounded_text(item.get("client"), 512)
    options = _bounded_text(item.get("options"), 1024)
    comment = _bounded_text(item.get("comment"), 512)
    if client is None or options is None or comment is None:
        raise OmvUnavailable("OMV bridge returned incomplete NFS settings")
    return {
        "uuid": share_uuid,
        "sharedFolderRef": folder_ref,
        "sharedFolderName": folder_name,
        "client": client,
        "options": options,
        "comment": comment,
    }


def _privilege(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise OmvUnavailable("OMV bridge returned an invalid share privilege")
    role_type = _bounded_text(item.get("type"), 16)
    name = _bounded_text(item.get("name"), 255)
    identifier = _integer(item.get("id"), maximum=2**31 - 1)
    permission = _bounded_text(item.get("permission"), 16)
    if (
        role_type not in {"user", "group"}
        or not name
        or identifier is None
        or permission not in {"inherit", "none", "read", "readWrite"}
    ):
        raise OmvUnavailable("OMV bridge returned an incomplete share privilege")
    return {
        "type": role_type,
        "id": identifier,
        "name": name,
        "permission": permission,
    }


def _account_plan_header(
    value: Any,
    schema: str,
    label: str,
    *,
    operation: str = "create",
) -> tuple[str, str]:
    if not isinstance(value, dict) or value.get("schema") != schema:
        raise OmvUnavailable(f"OMV bridge returned an invalid {label} plan")
    plan_id = _bounded_text(value.get("planId"), 64)
    base_revision = _bounded_text(value.get("baseRevision"), 64)
    if (
        plan_id is None
        or re.fullmatch(r"[0-9a-f]{64}", plan_id) is None
        or base_revision is None
        or re.fullmatch(r"[0-9a-f]{64}", base_revision) is None
        or value.get("operation") != operation
        or value.get("requiresApproval") is not True
        or not isinstance(value.get("applied", False), bool)
        or not isinstance(value.get("verified", True), bool)
    ):
        raise OmvUnavailable(f"OMV bridge returned an incomplete {label} plan")
    allowed = {
        "schema",
        "planId",
        "baseRevision",
        "operation",
        "requiresApproval",
        "desired",
        "changes",
        "safety",
        "applied",
        "verified",
    }
    if not set(value).issubset(allowed):
        raise OmvUnavailable(f"OMV bridge returned unexpected {label} plan fields")
    return plan_id, base_revision


def _group_plan(value: Any, *, expected_desired: dict[str, Any]) -> dict[str, Any]:
    plan_id, base_revision = _account_plan_header(value, GROUP_PLAN_SCHEMA, "group")
    try:
        desired = validate_group_desired(value.get("desired"))
    except ValueError as exc:
        raise OmvUnavailable("OMV bridge returned an invalid group desired state") from exc
    changes = value.get("changes")
    if desired != expected_desired or not isinstance(changes, list) or len(changes) != 2:
        raise OmvUnavailable("OMV bridge returned a mismatched group plan")
    expected_changes = [
        {"field": "name", "before": None, "after": desired["name"]},
        {"field": "comment", "before": None, "after": desired["comment"]},
    ]
    if changes != expected_changes:
        raise OmvUnavailable("OMV bridge returned invalid group plan changes")
    safety = {
        "scope": "newNormalOmvGroup",
        "initialMembers": "empty",
        "systemGroups": "never",
        "update": "notManaged",
        "delete": "rollbackOnlyBeforeUse",
    }
    if value.get("safety") != safety:
        raise OmvUnavailable("OMV bridge returned an invalid group safety contract")
    result = {
        "schema": GROUP_PLAN_SCHEMA,
        "planId": plan_id,
        "baseRevision": base_revision,
        "operation": "create",
        "requiresApproval": True,
        "desired": desired,
        "changes": expected_changes,
        "safety": safety,
    }
    if "applied" in value:
        result["applied"] = value["applied"]
    if "verified" in value:
        result["verified"] = value["verified"]
    return result


def _user_plan(value: Any, *, expected_desired: dict[str, Any]) -> dict[str, Any]:
    plan_id, base_revision = _account_plan_header(value, USER_PLAN_SCHEMA, "user")
    desired = value.get("desired")
    expected_safe_desired = {
        "schema": USER_DESIRED_SCHEMA,
        "name": expected_desired["name"],
        "displayName": expected_desired["displayName"],
        "groups": expected_desired["groups"],
        "passwordBound": True,
    }
    changes = value.get("changes")
    if desired != expected_safe_desired or not isinstance(changes, list) or len(changes) != 3:
        raise OmvUnavailable("OMV bridge returned a mismatched user plan")
    expected_changes = [
        {"field": "name", "before": None, "after": expected_desired["name"]},
        {
            "field": "displayName",
            "before": None,
            "after": expected_desired["displayName"],
        },
        {"field": "groups", "before": [], "after": expected_desired["groups"]},
    ]
    if changes != expected_changes:
        raise OmvUnavailable("OMV bridge returned invalid user plan changes")
    safety = {
        "scope": "newNormalOmvUser",
        "password": HMAC_SAFETY_CONTRACT,
        "loginShell": "nologin",
        "sshKeys": "none",
        "homeDirectory": "automaticHomesMustBeDisabled",
        "systemGroups": "notEnumeratedNotSelectable",
        "update": "notManaged",
        "delete": "rollbackOnlyBeforeUse",
    }
    if value.get("safety") != safety:
        raise OmvUnavailable("OMV bridge returned an invalid user safety contract")
    result = {
        "schema": USER_PLAN_SCHEMA,
        "planId": plan_id,
        "baseRevision": base_revision,
        "operation": "create",
        "requiresApproval": True,
        "desired": expected_safe_desired,
        "changes": expected_changes,
        "safety": safety,
    }
    if "applied" in value:
        result["applied"] = value["applied"]
    if "verified" in value:
        result["verified"] = value["verified"]
    return result


def _user_password_plan(value: Any, *, expected_desired: dict[str, str]) -> dict[str, Any]:
    plan_id, base_revision = _account_plan_header(
        value,
        USER_PASSWORD_PLAN_SCHEMA,
        "user password",
        operation="resetPassword",
    )
    expected_safe_desired = {
        "schema": USER_PASSWORD_DESIRED_SCHEMA,
        "name": expected_desired["name"],
        "passwordBound": True,
    }
    changes = value.get("changes")
    expected_changes = [
        {
            "field": "password",
            "before": "currentCredential",
            "after": "replacementCredential",
        }
    ]
    if value.get("desired") != expected_safe_desired or changes != expected_changes:
        raise OmvUnavailable("OMV bridge returned a mismatched user password plan")
    safety = {
        "scope": "existingConstrainedNormalOmvUser",
        "password": HMAC_SAFETY_CONTRACT,
        "accountFields": "preservedAndVerified",
        "loginShell": "nologin",
        "sshKeys": "none",
        "rollback": "notAvailableAfterAcceptedSecretRpc",
    }
    if value.get("safety") != safety:
        raise OmvUnavailable("OMV bridge returned an invalid user password safety contract")
    result = {
        "schema": USER_PASSWORD_PLAN_SCHEMA,
        "planId": plan_id,
        "baseRevision": base_revision,
        "operation": "resetPassword",
        "requiresApproval": True,
        "desired": expected_safe_desired,
        "changes": expected_changes,
        "safety": safety,
    }
    if "applied" in value:
        result["applied"] = value["applied"]
    if "verified" in value:
        result["verified"] = value["verified"]
    return result


def _share_privilege_plan(value: Any, *, expected_desired: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema") != SHARE_PRIVILEGE_PLAN_SCHEMA:
        raise OmvUnavailable("OMV bridge returned an invalid share privilege plan")
    plan_id = _bounded_text(value.get("planId"), 64)
    base_revision = _bounded_text(value.get("baseRevision"), 64)
    operation = value.get("operation")
    folder = value.get("sharedFolder")
    principal = value.get("principal")
    changes = value.get("changes")
    if (
        plan_id is None
        or re.fullmatch(r"[0-9a-f]{64}", plan_id) is None
        or base_revision is None
        or re.fullmatch(r"[0-9a-f]{64}", base_revision) is None
        or operation not in {"update", "none"}
        or not isinstance(value.get("requiresApproval"), bool)
        or not isinstance(value.get("applied", False), bool)
        or not isinstance(value.get("verified", True), bool)
        or not isinstance(folder, dict)
        or set(folder) != {"uuid", "name", "status"}
        or not isinstance(principal, dict)
        or set(principal) != {"type", "id", "name", "before", "after"}
        or not isinstance(changes, list)
        or len(changes) > 1
    ):
        raise OmvUnavailable("OMV bridge returned an incomplete share privilege plan")
    try:
        desired = validate_share_privilege_desired(value.get("desired"))
        folder_uuid = validate_omv_uuid(folder.get("uuid")).lower()
    except (TypeError, ValueError) as exc:
        raise OmvUnavailable("OMV bridge returned an invalid share privilege identity") from exc
    folder_name = _bounded_text(folder.get("name"), 255)
    folder_status = _bounded_text(folder.get("status"), 64)
    role_type = principal.get("type")
    principal_name = _bounded_text(principal.get("name"), 255)
    identifier = _integer(principal.get("id"), maximum=2**31 - 1)
    before = principal.get("before")
    after = principal.get("after")
    permissions = {"inherit", "none", "read", "readWrite"}
    if (
        desired != expected_desired
        or folder_uuid != desired["sharedFolderRef"]
        or not folder_name
        or not folder_status
        or role_type != desired["principalType"]
        or principal_name != desired["principalName"]
        or identifier is None
        or before not in permissions
        or after != desired["permission"]
    ):
        raise OmvUnavailable("OMV bridge returned a mismatched share privilege plan")
    normalized_changes: list[dict[str, Any]] = []
    for change in changes:
        if (
            not isinstance(change, dict)
            or set(change) != {"field", "before", "after"}
            or change.get("field") != "permission"
            or change.get("before") != before
            or change.get("after") != after
        ):
            raise OmvUnavailable("OMV bridge returned an invalid share privilege change")
        normalized_changes.append(dict(change))
    if value["requiresApproval"] != (operation == "update"):
        raise OmvUnavailable("OMV bridge returned an invalid share privilege approval requirement")
    if (operation == "none") != (before == after and not normalized_changes):
        raise OmvUnavailable("OMV bridge returned an inconsistent share privilege operation")
    if operation == "update" and len(normalized_changes) != 1:
        raise OmvUnavailable("OMV bridge returned an incomplete share privilege change")
    expected_safety = {
        "scope": "sharedFolderConfigPrivilege",
        "principal": "existingOmvUserOrGroup",
        "filesystemAcl": "notModified",
        "recursive": "never",
        "serviceDeploy": "sambaAndRsyncdWhenDirty",
        "delete": "notManaged",
    }
    if value.get("safety") != expected_safety:
        raise OmvUnavailable("OMV bridge returned an invalid share privilege safety contract")
    result = {
        "schema": SHARE_PRIVILEGE_PLAN_SCHEMA,
        "planId": plan_id,
        "baseRevision": base_revision,
        "operation": operation,
        "requiresApproval": value["requiresApproval"],
        "sharedFolder": {"uuid": folder_uuid, "name": folder_name, "status": folder_status},
        "principal": {
            "type": role_type,
            "id": identifier,
            "name": principal_name,
            "before": before,
            "after": after,
        },
        "desired": desired,
        "changes": normalized_changes,
        "safety": expected_safety,
    }
    if "applied" in value:
        result["applied"] = value["applied"]
    if "verified" in value:
        result["verified"] = value["verified"]
    if "deployedServices" in value:
        deployed = value["deployedServices"]
        if not isinstance(deployed, list) or deployed not in (
            [],
            ["samba"],
            ["rsyncd"],
            ["samba", "rsyncd"],
        ):
            raise OmvUnavailable("OMV bridge returned invalid privilege deployment services")
        result["deployedServices"] = deployed
    return result


def _shared_folder_plan(value: Any, *, expected_desired: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema") != SHARED_FOLDER_PLAN_SCHEMA:
        raise OmvUnavailable("OMV bridge returned an invalid shared folder plan")
    plan_id = _bounded_text(value.get("planId"), 64)
    base_revision = _bounded_text(value.get("baseRevision"), 64)
    share_uuid = _bounded_text(value.get("shareUuid"), 36)
    operation = value.get("operation")
    target = value.get("target")
    changes = value.get("changes")
    if (
        plan_id is None
        or re.fullmatch(r"[0-9a-f]{64}", plan_id) is None
        or base_revision is None
        or re.fullmatch(r"[0-9a-f]{64}", base_revision) is None
        or share_uuid is None
        or operation not in {"create", "none"}
        or not isinstance(value.get("requiresApproval"), bool)
        or not isinstance(value.get("applied", False), bool)
        or not isinstance(value.get("verified", True), bool)
        or not isinstance(changes, list)
        or len(changes) > 2
    ):
        raise OmvUnavailable("OMV bridge returned an incomplete shared folder plan")
    try:
        validate_omv_uuid(share_uuid)
        desired = validate_shared_folder_desired(value.get("desired"))
    except ValueError as exc:
        raise OmvUnavailable("OMV bridge returned an invalid shared folder identity") from exc
    normalized_target = _shared_folder_target(target)
    if (
        desired != expected_desired
        or normalized_target["mountPointRef"] != desired["mountPointRef"]
    ):
        raise OmvUnavailable("OMV bridge returned a mismatched shared folder plan")
    normalized_changes: list[dict[str, Any]] = []
    for change in changes:
        if not isinstance(change, dict) or set(change) != {"field", "before", "after"}:
            raise OmvUnavailable("OMV bridge returned an invalid shared folder plan change")
        field = change.get("field")
        if field not in {"name", "comment"} or change.get("before") is not None:
            raise OmvUnavailable("OMV bridge returned an invalid shared folder create change")
        if change.get("after") != desired[field]:
            raise OmvUnavailable("OMV bridge returned a mismatched shared folder create change")
        normalized_changes.append(dict(change))
    if value["requiresApproval"] != (operation == "create"):
        raise OmvUnavailable("OMV bridge returned an invalid shared folder approval requirement")
    if (operation == "none") != (not normalized_changes):
        raise OmvUnavailable("OMV bridge returned an inconsistent shared folder operation")
    expected_safety = {
        "filesystem": "existingMountedWritableOnly",
        "relativePath": "derivedFromPortableName",
        "directoryMode": "2770UsersGroup",
        "acl": "notManaged",
        "update": "notManaged",
        "delete": "notManaged",
    }
    if value.get("safety") != expected_safety:
        raise OmvUnavailable("OMV bridge returned an invalid shared folder safety contract")
    result = {
        "schema": SHARED_FOLDER_PLAN_SCHEMA,
        "planId": plan_id,
        "baseRevision": base_revision,
        "operation": operation,
        "requiresApproval": value["requiresApproval"],
        "shareUuid": share_uuid.lower(),
        "target": normalized_target,
        "desired": desired,
        "changes": normalized_changes,
        "safety": expected_safety,
    }
    if "applied" in value:
        result["applied"] = value["applied"]
    if "verified" in value:
        result["verified"] = value["verified"]
    return result


def _smb_plan(value: Any, *, expected_desired: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema") != SMB_PLAN_SCHEMA:
        raise OmvUnavailable("OMV bridge returned an invalid SMB plan")
    plan_id = _bounded_text(value.get("planId"), 64)
    base_revision = _bounded_text(value.get("baseRevision"), 64)
    share_uuid = _bounded_text(value.get("shareUuid"), 36)
    operation = value.get("operation")
    folder = value.get("sharedFolder")
    changes = value.get("changes")
    safety = value.get("safety")
    if (
        plan_id is None
        or re.fullmatch(r"[0-9a-f]{64}", plan_id) is None
        or base_revision is None
        or re.fullmatch(r"[0-9a-f]{64}", base_revision) is None
        or share_uuid is None
        or operation not in {"create", "update", "none"}
        or not isinstance(value.get("requiresApproval"), bool)
        or not isinstance(value.get("applied", False), bool)
        or not isinstance(value.get("verified", True), bool)
        or not isinstance(folder, dict)
        or not isinstance(changes, list)
        or len(changes) > 5
        or not isinstance(safety, dict)
    ):
        raise OmvUnavailable("OMV bridge returned an incomplete SMB plan")
    validate_omv_uuid(share_uuid)
    folder_uuid = _bounded_text(folder.get("uuid"), 36)
    folder_name = _bounded_text(folder.get("name"), 255)
    folder_status = _bounded_text(folder.get("status"), 64)
    if not folder_uuid or folder_name is None or not folder_status:
        raise OmvUnavailable("OMV bridge returned an invalid SMB plan folder")
    try:
        validate_omv_uuid(folder_uuid)
    except ValueError as exc:
        raise OmvUnavailable("OMV bridge returned an invalid SMB folder UUID") from exc
    try:
        desired = validate_smb_desired(value.get("desired"))
    except ValueError as exc:
        raise OmvUnavailable("OMV bridge returned an invalid SMB desired state") from exc
    if desired != expected_desired or desired["sharedFolderRef"] != folder_uuid.lower():
        raise OmvUnavailable("OMV bridge returned a mismatched SMB plan")
    allowed_fields = {"enabled", "readOnly", "browseable", "recycleBin", "comment"}
    normalized_changes: list[dict[str, Any]] = []
    for change in changes:
        if not isinstance(change, dict) or set(change) != {"field", "before", "after"}:
            raise OmvUnavailable("OMV bridge returned an invalid SMB plan change")
        field = change.get("field")
        if field not in allowed_fields or change.get("after") != desired[field]:
            raise OmvUnavailable("OMV bridge returned a mismatched SMB plan change")
        before = change.get("before")
        if field == "comment":
            if before is not None and _bounded_text(before, 512) is None:
                raise OmvUnavailable("OMV bridge returned an invalid SMB text change")
        elif before is not None and not isinstance(before, bool):
            raise OmvUnavailable("OMV bridge returned an invalid SMB flag change")
        normalized_changes.append(dict(change))
    if value["requiresApproval"] != (operation != "none"):
        raise OmvUnavailable("OMV bridge returned an invalid SMB approval requirement")
    if (operation == "none") != (not normalized_changes):
        raise OmvUnavailable("OMV bridge returned an inconsistent SMB operation")
    expected_safety = {
        "guestAccess": "disabled",
        "advancedOptions": "notManaged",
        "acl": "notManaged",
    }
    if safety != expected_safety:
        raise OmvUnavailable("OMV bridge returned an invalid SMB safety contract")
    result = {
        "schema": SMB_PLAN_SCHEMA,
        "planId": plan_id,
        "baseRevision": base_revision,
        "operation": operation,
        "requiresApproval": value["requiresApproval"],
        "shareUuid": share_uuid.lower(),
        "sharedFolder": {
            "uuid": folder_uuid.lower(),
            "name": folder_name,
            "status": folder_status,
        },
        "desired": desired,
        "changes": normalized_changes,
        "safety": expected_safety,
    }
    if "applied" in value:
        result["applied"] = value["applied"]
    if "verified" in value:
        result["verified"] = value["verified"]
    return result


def _nfs_plan(value: Any, *, expected_desired: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema") != NFS_PLAN_SCHEMA:
        raise OmvUnavailable("OMV bridge returned an invalid NFS plan")
    plan_id = _bounded_text(value.get("planId"), 64)
    base_revision = _bounded_text(value.get("baseRevision"), 64)
    share_uuid = _bounded_text(value.get("shareUuid"), 36)
    operation = value.get("operation")
    folder = value.get("sharedFolder")
    changes = value.get("changes")
    safety = value.get("safety")
    if (
        plan_id is None
        or re.fullmatch(r"[0-9a-f]{64}", plan_id) is None
        or base_revision is None
        or re.fullmatch(r"[0-9a-f]{64}", base_revision) is None
        or share_uuid is None
        or operation not in {"create", "update", "none"}
        or not isinstance(value.get("requiresApproval"), bool)
        or not isinstance(value.get("applied", False), bool)
        or not isinstance(value.get("verified", True), bool)
        or not isinstance(folder, dict)
        or not isinstance(changes, list)
        or len(changes) > 2
        or not isinstance(safety, dict)
    ):
        raise OmvUnavailable("OMV bridge returned an incomplete NFS plan")
    try:
        validate_omv_uuid(share_uuid)
        desired = validate_nfs_desired(value.get("desired"))
        folder_uuid = validate_omv_uuid(str(folder.get("uuid"))).lower()
    except ValueError as exc:
        raise OmvUnavailable("OMV bridge returned an invalid NFS identity") from exc
    folder_name = _bounded_text(folder.get("name"), 255)
    folder_status = _bounded_text(folder.get("status"), 64)
    if (
        desired != expected_desired
        or desired["sharedFolderRef"] != folder_uuid
        or folder_name is None
        or not folder_status
    ):
        raise OmvUnavailable("OMV bridge returned a mismatched NFS plan")
    normalized_changes: list[dict[str, Any]] = []
    for change in changes:
        if not isinstance(change, dict) or set(change) != {"field", "before", "after"}:
            raise OmvUnavailable("OMV bridge returned an invalid NFS plan change")
        field = change.get("field")
        before = change.get("before")
        if field not in {"readOnly", "comment"} or change.get("after") != desired[field]:
            raise OmvUnavailable("OMV bridge returned a mismatched NFS plan change")
        if field == "readOnly":
            if before is not None and not isinstance(before, bool):
                raise OmvUnavailable("OMV bridge returned an invalid NFS flag change")
        elif before is not None and _bounded_text(before, 512) is None:
            raise OmvUnavailable("OMV bridge returned an invalid NFS text change")
        normalized_changes.append(dict(change))
    if value["requiresApproval"] != (operation != "none"):
        raise OmvUnavailable("OMV bridge returned an invalid NFS approval requirement")
    if (operation == "none") != (not normalized_changes):
        raise OmvUnavailable("OMV bridge returned an inconsistent NFS operation")
    expected_safety = {
        "clientScope": "privateCidrOnly",
        "rootSquash": "required",
        "syncWrites": "required",
        "advancedOptions": "notManaged",
        "delete": "notManaged",
    }
    if safety != expected_safety:
        raise OmvUnavailable("OMV bridge returned an invalid NFS safety contract")
    result = {
        "schema": NFS_PLAN_SCHEMA,
        "planId": plan_id,
        "baseRevision": base_revision,
        "operation": operation,
        "requiresApproval": value["requiresApproval"],
        "shareUuid": share_uuid.lower(),
        "sharedFolder": {
            "uuid": folder_uuid,
            "name": folder_name,
            "status": folder_status,
        },
        "desired": desired,
        "changes": normalized_changes,
        "safety": expected_safety,
    }
    if "applied" in value:
        result["applied"] = value["applied"]
    if "verified" in value:
        result["verified"] = value["verified"]
    return result


def _quota_plan(value: Any, *, expected_desired: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema") != QUOTA_PLAN_SCHEMA:
        raise OmvUnavailable("OMV bridge returned an invalid quota plan")
    plan_id = _bounded_text(value.get("planId"), 64)
    base_revision = _bounded_text(value.get("baseRevision"), 64)
    operation = value.get("operation")
    filesystem = value.get("filesystem")
    subject = value.get("subject")
    changes = value.get("changes")
    safety = value.get("safety")
    if (
        plan_id is None
        or re.fullmatch(r"[0-9a-f]{64}", plan_id) is None
        or base_revision is None
        or re.fullmatch(r"[0-9a-f]{64}", base_revision) is None
        or operation not in {"update", "none"}
        or not isinstance(value.get("requiresApproval"), bool)
        or not isinstance(value.get("applied", False), bool)
        or not isinstance(value.get("verified", True), bool)
        or not isinstance(filesystem, dict)
        or set(filesystem) != {"uuid", "label", "type", "readOnly", "supportsQuota"}
        or not isinstance(subject, dict)
        or set(subject) != {"type", "name", "hardLimitBytes", "used"}
        or not isinstance(changes, list)
        or len(changes) > 1
        or not isinstance(safety, dict)
    ):
        raise OmvUnavailable("OMV bridge returned an incomplete quota plan")
    try:
        desired = validate_quota_desired(value.get("desired"))
        filesystem_uuid = validate_omv_uuid(str(filesystem.get("uuid"))).lower()
    except ValueError as exc:
        raise OmvUnavailable("OMV bridge returned an invalid quota identity") from exc
    label = _bounded_text(filesystem.get("label"), 256)
    filesystem_type = _bounded_text(filesystem.get("type"), 64)
    current_limit = _integer(subject.get("hardLimitBytes"), maximum=MAX_QUOTA_BYTES)
    used = _bounded_text(subject.get("used"), 64)
    if (
        desired != expected_desired
        or filesystem_uuid != desired["filesystemUuid"]
        or label is None
        or filesystem_type is None
        or filesystem.get("readOnly") is not False
        or filesystem.get("supportsQuota") is not True
        or subject.get("type") != desired["subjectType"]
        or subject.get("name") != desired["subjectName"]
        or current_limit is None
        or used is None
    ):
        raise OmvUnavailable("OMV bridge returned a mismatched quota plan")
    normalized_changes: list[dict[str, Any]] = []
    for change in changes:
        if (
            not isinstance(change, dict)
            or set(change) != {"field", "before", "after"}
            or change.get("field") != "hardLimitBytes"
            or _integer(change.get("before"), maximum=MAX_QUOTA_BYTES) is None
            or change.get("after") != desired["hardLimitBytes"]
        ):
            raise OmvUnavailable("OMV bridge returned an invalid quota plan change")
        normalized_changes.append(dict(change))
    if value["requiresApproval"] != (operation != "none"):
        raise OmvUnavailable("OMV bridge returned an invalid quota approval requirement")
    if (operation == "none") != (not normalized_changes):
        raise OmvUnavailable("OMV bridge returned an inconsistent quota operation")
    expected_safety = {
        "scope": "filesystemUserOrGroup",
        "protocolCoverage": ["local", "SMB", "NFS"],
        "sharedFolderQuota": "notSupportedByOmvQuotaRpc",
        "minimumUnitBytes": 1024,
    }
    if safety != expected_safety:
        raise OmvUnavailable("OMV bridge returned an invalid quota safety contract")
    result = {
        "schema": QUOTA_PLAN_SCHEMA,
        "planId": plan_id,
        "baseRevision": base_revision,
        "operation": operation,
        "requiresApproval": value["requiresApproval"],
        "filesystem": {
            "uuid": filesystem_uuid,
            "label": label,
            "type": filesystem_type,
            "readOnly": False,
            "supportsQuota": True,
        },
        "subject": {
            "type": desired["subjectType"],
            "name": desired["subjectName"],
            "hardLimitBytes": current_limit,
            "used": used,
        },
        "desired": desired,
        "changes": normalized_changes,
        "safety": expected_safety,
    }
    if "applied" in value:
        result["applied"] = value["applied"]
    if "verified" in value:
        result["verified"] = value["verified"]
    return result
