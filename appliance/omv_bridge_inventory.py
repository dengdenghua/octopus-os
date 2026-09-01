"""Sanitized filesystem, SMART, sharing, and topology inventory for OMV."""

from __future__ import annotations

import re
from typing import Any

from appliance.omv_bridge_contract import (
    _BLOCK_TYPE_PATTERN,
    _DEVICEFILE_PATTERN,
    _MDSTAT_ARRAY_PATTERN,
    _OMV_UUID_PATTERN,
    _SHARE_LIST_PARAMS,
    MAX_DEVICEFILE_LENGTH,
    MAX_MDSTAT_BYTES,
    _boolean,
    _integer,
    _safe_text,
    _validated_mount_point,
)
from appliance.omv_bridge_errors import OmvBridgeError


class OmvInventoryMixin:
    """Read-only inventory methods composed into the bridge service facade."""

    def filesystems(self) -> list[dict[str, Any]]:
        payload = self._runner(
            "FileSystemMgmt",
            "enumerateMountedFilesystems",
            {"includeroot": False},
        )
        if not isinstance(payload, list):
            raise OmvBridgeError("OMV filesystem response must be a list")
        result: list[dict[str, Any]] = []
        for item in payload[:1024]:
            if not isinstance(item, dict):
                continue
            size = _integer(item.get("size"))
            available = _integer(item.get("available"))
            percentage = _integer(item.get("percentage"), maximum=100)
            devicefile = _safe_text(item.get("devicefile"), maximum=MAX_DEVICEFILE_LENGTH)
            mountpoint = _safe_text(item.get("mountpoint"), maximum=4096)
            if size is None or available is None or not devicefile or not mountpoint:
                continue
            result.append(
                {
                    "devicefile": devicefile,
                    "parentdevicefile": _safe_text(
                        item.get("parentdevicefile"), maximum=MAX_DEVICEFILE_LENGTH
                    )
                    or None,
                    "uuid": _safe_text(item.get("uuid"), maximum=128) or None,
                    "label": _safe_text(item.get("label"), maximum=256),
                    "type": _safe_text(item.get("type"), maximum=64),
                    "mountpoint": mountpoint,
                    "sizeBytes": size,
                    "availableBytes": available,
                    "usedPercent": percentage,
                    "readOnly": bool(item.get("_readonly", item.get("readonly", False))),
                    "supportsAcl": bool(item.get("propposixacl", False)),
                    "supportsQuota": bool(item.get("propquota", False)),
                }
            )
        return result

    def _smart_device_context(self) -> tuple[set[str], dict[str, dict[str, Any]]]:
        physical = {device["devicefile"]: device for device in self.smart_devices()}
        allowed = set(physical)
        for filesystem in self.filesystems():
            for key in ("devicefile", "parentdevicefile"):
                candidate = filesystem.get(key)
                if isinstance(candidate, str) and _DEVICEFILE_PATTERN.fullmatch(candidate):
                    allowed.add(candidate)
        return allowed, physical

    def smart_devices(self) -> list[dict[str, Any]]:
        payload = self._runner("Smart", "enumerateDevices", {})
        if not isinstance(payload, list):
            raise OmvBridgeError("OMV SMART device response must be a list")
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in payload[:256]:
            if not isinstance(item, dict):
                continue
            # OMV's predictable /dev/disk/by-id path can contain a drive
            # serial number. Only expose its canonical /dev/sdX-style path.
            devicefile = _safe_text(
                item.get("canonicaldevicefile") or item.get("devicename"),
                maximum=MAX_DEVICEFILE_LENGTH,
            )
            if (
                not devicefile
                or devicefile in seen
                or _DEVICEFILE_PATTERN.fullmatch(devicefile) is None
            ):
                continue
            seen.add(devicefile)
            result.append(
                {
                    "devicefile": devicefile,
                    "model": _safe_text(item.get("model"), maximum=256),
                    "sizeBytes": _integer(item.get("size")),
                    "health": _safe_text(item.get("overallstatus"), maximum=128) or "unknown",
                    "temperatureC": _integer(item.get("temperature"), maximum=300),
                }
            )
        return result

    def smart(self, devicefile: str) -> dict[str, Any]:
        if (
            len(devicefile) > MAX_DEVICEFILE_LENGTH
            or _DEVICEFILE_PATTERN.fullmatch(devicefile) is None
        ):
            raise OmvBridgeError("SMART device is not an enumerated filesystem device")
        allowed, enumerated = self._smart_device_context()
        if devicefile not in allowed:
            raise OmvBridgeError("SMART device is not an enumerated filesystem device")
        payload = self._runner("Smart", "getInformation", {"devicefile": devicefile})
        if not isinstance(payload, dict):
            raise OmvBridgeError("OMV SMART response must be an object")
        health = next(
            (
                _safe_text(payload.get(key), maximum=128)
                for key in (
                    "overallstatus",
                    "smartoverallhealthselfassessmenttestresult",
                    "smarthealthstatus",
                    "overallhealth",
                    "smartstatus",
                    "health",
                    "assessment",
                )
                if _safe_text(payload.get(key), maximum=128)
            ),
            str(enumerated.get(devicefile, {}).get("health") or "unknown"),
        )
        return {
            "devicefile": devicefile,
            "model": next(
                (
                    _safe_text(payload.get(key), maximum=256)
                    for key in ("devicemodel", "modelnumber", "product", "model")
                    if _safe_text(payload.get(key), maximum=256)
                ),
                str(enumerated.get(devicefile, {}).get("model") or ""),
            ),
            "health": health,
            "temperatureC": _integer(payload.get("temperature"), maximum=300),
            "powerOnHours": _integer(payload.get("poweronhours")),
            "powerCycles": _integer(payload.get("powercycles")),
        }

    @staticmethod
    def _share_list_data(payload: Any, label: str) -> list[Any]:
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            raise OmvBridgeError(f"OMV {label} response must contain a list")
        return data[:1024]

    def _raw_shared_folders(self) -> list[Any]:
        payload = self._runner("ShareMgmt", "enumerateSharedFolders", {})
        if not isinstance(payload, list):
            raise OmvBridgeError("OMV shared folder response must be a list")
        return payload[:1024]

    def shared_folders(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for item in self._raw_shared_folders():
            if not isinstance(item, dict):
                continue
            share_uuid = _safe_text(item.get("uuid"), maximum=36)
            name = _safe_text(item.get("name"), maximum=255)
            if _OMV_UUID_PATTERN.fullmatch(share_uuid) is None or not name:
                continue
            mount = item.get("mntent") if isinstance(item.get("mntent"), dict) else {}
            result.append(
                {
                    "uuid": share_uuid,
                    "name": name,
                    "comment": _safe_text(item.get("comment"), maximum=512),
                    "relativePath": _safe_text(item.get("reldirpath"), maximum=4096),
                    "device": _safe_text(item.get("device"), maximum=256),
                    "status": _safe_text(item.get("status"), maximum=64) or "unknown",
                    "inUse": _boolean(item.get("_used")),
                    "supportsAcl": _boolean(mount.get("posixacl")),
                }
            )
        return result

    def shared_folder_targets(self) -> list[dict[str, Any]]:
        candidates = self._runner("ShareMgmt", "getCandidates", {})
        if not isinstance(candidates, list):
            raise OmvBridgeError("OMV shared folder target response must be a list")
        filesystems = {item["mountpoint"]: item for item in self.filesystems()}
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in candidates[:128]:
            candidate_uuid = item.get("uuid") if isinstance(item, dict) else None
            if (
                not isinstance(candidate_uuid, str)
                or _OMV_UUID_PATTERN.fullmatch(candidate_uuid) is None
                or candidate_uuid.lower() in seen
            ):
                continue
            mount = _validated_mount_point(
                self._runner("FsTab", "get", {"uuid": candidate_uuid.lower()})
            )
            filesystem = filesystems.get(mount["dir"])
            if filesystem is None or filesystem["readOnly"]:
                continue
            seen.add(mount["uuid"])
            result.append(
                {
                    "mountPointRef": mount["uuid"],
                    "filesystemUuid": filesystem["uuid"],
                    "label": filesystem["label"],
                    "type": filesystem["type"],
                    "sizeBytes": filesystem["sizeBytes"],
                    "availableBytes": filesystem["availableBytes"],
                    "readOnly": False,
                }
            )
        return result

    def sharing_overview(self) -> dict[str, Any]:
        shared_folders = self.shared_folders()

        users_payload = self._runner("UserMgmt", "enumerateUsers", {"detail": "basic"})
        groups_payload = self._runner("UserMgmt", "enumerateGroups", {})
        if not isinstance(users_payload, list) or not isinstance(groups_payload, list):
            raise OmvBridgeError("OMV account inventory response must be a list")

        users: list[dict[str, Any]] = []
        for item in users_payload[:1024]:
            if not isinstance(item, dict):
                continue
            name = _safe_text(item.get("name"), maximum=255)
            uid = _integer(item.get("uid"), maximum=2**31 - 1)
            gid = _integer(item.get("gid"), maximum=2**31 - 1)
            if not name or uid is None or gid is None:
                continue
            raw_groups = item.get("groups")
            groups = (
                [group for value in raw_groups[:64] if (group := _safe_text(value, maximum=255))]
                if isinstance(raw_groups, list)
                else []
            )
            users.append(
                {
                    "name": name,
                    "uid": uid,
                    "gid": gid,
                    "comment": _safe_text(item.get("comment"), maximum=512),
                    "groups": groups,
                }
            )

        groups: list[dict[str, Any]] = []
        for item in groups_payload[:1024]:
            if not isinstance(item, dict):
                continue
            name = _safe_text(item.get("name"), maximum=255)
            gid = _integer(item.get("gid"), maximum=2**31 - 1)
            if not name or gid is None:
                continue
            raw_members = item.get("members")
            members = (
                [
                    member
                    for value in raw_members[:1024]
                    if (member := _safe_text(value, maximum=255))
                ]
                if isinstance(raw_members, list)
                else []
            )
            groups.append({"name": name, "gid": gid, "members": members})

        smb_settings = self._runner("SMB", "getSettings", {})
        nfs_settings = self._runner("NFS", "getSettings", {})
        if not isinstance(smb_settings, dict) or not isinstance(nfs_settings, dict):
            raise OmvBridgeError("OMV sharing settings response must be an object")

        smb_payload = self._runner("SMB", "getShareList", dict(_SHARE_LIST_PARAMS))
        nfs_payload = self._runner("NFS", "getShareList", dict(_SHARE_LIST_PARAMS))
        smb_shares: list[dict[str, Any]] = []
        for item in self._share_list_data(smb_payload, "SMB share list"):
            if not isinstance(item, dict):
                continue
            share_uuid = _safe_text(item.get("uuid"), maximum=36)
            folder_ref = _safe_text(item.get("sharedfolderref"), maximum=36)
            if (
                _OMV_UUID_PATTERN.fullmatch(share_uuid) is None
                or _OMV_UUID_PATTERN.fullmatch(folder_ref) is None
            ):
                continue
            smb_shares.append(
                {
                    "uuid": share_uuid,
                    "sharedFolderRef": folder_ref,
                    "sharedFolderName": _safe_text(item.get("sharedfoldername"), maximum=255),
                    "enabled": _boolean(item.get("enable")),
                    "readOnly": _boolean(item.get("readonly")),
                    "guest": _safe_text(item.get("guest"), maximum=32) or "no",
                    "browseable": _boolean(item.get("browseable")),
                    "recycleBin": _boolean(item.get("recyclebin")),
                    "comment": _safe_text(item.get("comment"), maximum=512),
                }
            )

        nfs_shares: list[dict[str, Any]] = []
        for item in self._share_list_data(nfs_payload, "NFS share list"):
            if not isinstance(item, dict):
                continue
            share_uuid = _safe_text(item.get("uuid"), maximum=36)
            folder_ref = _safe_text(item.get("sharedfolderref"), maximum=36)
            if (
                _OMV_UUID_PATTERN.fullmatch(share_uuid) is None
                or _OMV_UUID_PATTERN.fullmatch(folder_ref) is None
            ):
                continue
            nfs_shares.append(
                {
                    "uuid": share_uuid,
                    "sharedFolderRef": folder_ref,
                    "sharedFolderName": _safe_text(item.get("sharedfoldername"), maximum=255),
                    "client": _safe_text(item.get("client"), maximum=512),
                    "options": _safe_text(item.get("options"), maximum=1024),
                    "comment": _safe_text(item.get("comment"), maximum=512),
                }
            )

        return {
            "sharedFolders": shared_folders,
            "sharedFolderTargets": self.shared_folder_targets(),
            "users": users,
            "groups": groups,
            "smb": {
                "enabled": _boolean(smb_settings.get("enable")),
                "shares": smb_shares,
            },
            "nfs": {
                "enabled": _boolean(nfs_settings.get("enable")),
                "shares": nfs_shares,
            },
        }

    def block_topology(self) -> list[dict[str, Any]]:
        if self._topology_runner is None:
            raise OmvBridgeError("block topology discovery is not configured")
        payload = self._topology_runner()
        roots = payload.get("blockdevices") if isinstance(payload, dict) else None
        if not isinstance(roots, list):
            raise OmvBridgeError("block topology response must contain devices")

        nodes: dict[str, dict[str, Any]] = {}

        def visit(item: Any, parent: str | None, depth: int) -> None:
            if depth > 32 or not isinstance(item, dict):
                return
            devicefile = _safe_text(item.get("name"), maximum=MAX_DEVICEFILE_LENGTH)
            valid_device = bool(devicefile and _DEVICEFILE_PATTERN.fullmatch(devicefile))
            current_parent = parent
            if valid_device:
                block_type = _safe_text(item.get("type"), maximum=32)
                if _BLOCK_TYPE_PATTERN.fullmatch(block_type) is None:
                    block_type = "unknown"
                rotational_value = item.get("rota")
                if isinstance(rotational_value, bool):
                    rotational = rotational_value
                elif rotational_value in (0, 1, "0", "1"):
                    rotational = str(rotational_value) == "1"
                else:
                    rotational = None
                node = nodes.setdefault(
                    devicefile,
                    {
                        "devicefile": devicefile,
                        "type": block_type,
                        "sizeBytes": _integer(item.get("size")),
                        "filesystemType": _safe_text(item.get("fstype"), maximum=64) or None,
                        "rotational": rotational,
                        "parentDevicefiles": set(),
                    },
                )
                if parent and parent != devicefile:
                    node["parentDevicefiles"].add(parent)
                current_parent = devicefile
            children = item.get("children", [])
            if isinstance(children, list):
                for child in children[:1024]:
                    if len(nodes) >= 1024:
                        break
                    visit(child, current_parent, depth + 1)

        for root in roots[:1024]:
            if len(nodes) >= 1024:
                break
            visit(root, None, 0)

        return [
            {
                **node,
                "parentDevicefiles": sorted(node["parentDevicefiles"]),
            }
            for node in nodes.values()
        ]

    def raid_arrays(self) -> list[dict[str, Any]]:
        if self._mdstat_reader is None:
            return []
        content = self._mdstat_reader()
        if not isinstance(content, str) or len(content) > MAX_MDSTAT_BYTES:
            raise OmvBridgeError("software RAID status is invalid")
        lines = content.splitlines()
        result: list[dict[str, Any]] = []
        index = 0
        while index < len(lines) and len(result) < 256:
            match = _MDSTAT_ARRAY_PATTERN.match(lines[index].strip())
            if match is None:
                index += 1
                continue
            name, summary = match.groups()
            detail_lines: list[str] = []
            index += 1
            while index < len(lines):
                line = lines[index]
                if _MDSTAT_ARRAY_PATTERN.match(line.strip()):
                    break
                if not line.strip():
                    index += 1
                    break
                detail_lines.append(line.strip())
                index += 1

            tokens = summary.split()
            state = tokens[0].lower() if tokens else "unknown"
            level = next(
                (token.lower() for token in tokens[1:] if token.lower().startswith("raid")),
                "unknown",
            )
            detail = " ".join(detail_lines)
            counts = re.search(r"\[(\d+)/(\d+)\]\s+\[([U_]+)\]", detail)
            total_devices = int(counts.group(1)) if counts else None
            active_devices = int(counts.group(2)) if counts else None
            member_map = counts.group(3) if counts else ""
            operation_match = re.search(
                r"\b(recovery|resync|reshape|check)\s*=\s*([0-9]+(?:\.[0-9]+)?)%",
                detail,
                re.IGNORECASE,
            )
            operation = operation_match.group(1).lower() if operation_match else None
            operation_percent = (
                min(100, max(0, round(float(operation_match.group(2)))))
                if operation_match
                else None
            )
            if state != "active":
                health = "inactive"
            elif counts and (active_devices != total_devices or "_" in member_map):
                health = "degraded"
            elif operation in {"recovery", "resync", "reshape"}:
                health = "recovering"
            elif operation == "check":
                health = "checking"
            elif counts:
                health = "healthy"
            else:
                health = "unknown"
            result.append(
                {
                    "devicefile": f"/dev/{name}",
                    "level": level,
                    "status": health,
                    "totalDevices": total_devices,
                    "activeDevices": active_devices,
                    "operation": operation,
                    "operationPercent": operation_percent,
                }
            )
        return result

    def storage_topology(self) -> dict[str, Any]:
        return {
            "devices": self.block_topology(),
            "arrays": self.raid_arrays(),
        }


__all__ = ["OmvInventoryMixin"]
