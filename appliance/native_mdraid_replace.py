"""Plan-bound replacement of one failed Echo-managed md RAID1 member.

The workflow deliberately covers only a degraded two-disk RAID1 with one
fully in-sync survivor and either one kernel-confirmed faulty member or one
missing slot.  It never marks a healthy member faulty, never uses ``--force``,
and only accepts a blank whole-disk replacement with a persistent identity.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any

from appliance.native_mdraid import (
    _MDADM_CONFIG,
    _array_transaction,
    _canonical_hash,
    _managed_entries,
    _parse_export_fields,
    _read_config,
    _run,
    _run_checked,
    _run_mutating,
)
from appliance.native_storage_pool import inspect_blank_whole_disks
from appliance.omv_protocol import (
    MDRAID1_REPLACE_PLAN_SCHEMA,
    validate_mdraid1_replace_desired,
)

_SYS_CLASS_BLOCK = Path("/sys/class/block")
_ARRAY_PATH_PATTERN = re.compile(r"/dev/md/echo-[a-z][a-z0-9_-]{0,26}")
_KERNEL_MD_PATTERN = re.compile(r"md[0-9]+")
_WHOLE_DISK_PATTERN = re.compile(r"/dev/(?:sd[a-z]+|vd[a-z]+|xvd[a-z]+|nvme\d+n\d+|mmcblk\d+)")
_UUID_PATTERN = re.compile(r"[0-9a-f]{8}(?::[0-9a-f]{8}){3}")
_KNOWN_MEMBER_STATES = frozenset(
    {
        "faulty",
        "in_sync",
        "writemostly",
        "blocked",
        "spare",
        "write_error",
        "want_replacement",
        "replacement",
    }
)


def _require_tools() -> None:
    missing = [name for name in ("mdadm", "lsblk", "wipefs") if shutil.which(name) is None]
    if missing:
        raise OSError(f"md RAID1 replacement tools are unavailable: {', '.join(missing)}")


def _target(name: str) -> str:
    value = f"/dev/md/echo-{name}"
    if _ARRAY_PATH_PATTERN.fullmatch(value) is None:
        raise ValueError("md RAID1 target path is invalid")
    return value


def _configured_identity(name: str, array_uuid: str, config_path: Path) -> dict[str, str]:
    config = _read_config(config_path)
    _begin, _end, entries = _managed_entries(config.decode("utf-8") if config else "")
    target = _target(name)
    matches = [entry for entry in entries if entry.split()[1] == target]
    if len(matches) != 1:
        raise ValueError("md RAID1 is not uniquely owned by the Echo managed configuration")
    configured_uuid = matches[0].split()[2].removeprefix("UUID=")
    if configured_uuid != array_uuid:
        raise ValueError("md RAID1 name and UUID do not match the managed configuration")
    return {
        "name": name,
        "devicefile": target,
        "uuid": configured_uuid,
        "configSha256": hashlib.sha256(config or b"").hexdigest(),
    }


def _read_sysfs_text(path: Path) -> str:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise OSError(f"md RAID1 kernel state is unavailable: {path.name}") from exc
    if not payload or len(payload) > 4096 or b"\x00" in payload:
        raise OSError("md RAID1 kernel state is invalid")
    try:
        return payload.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise OSError("md RAID1 kernel state is invalid") from exc


def _member_device(member_dir: Path) -> str:
    fields: dict[str, str] = {}
    for line in _read_sysfs_text(member_dir / "block" / "uevent").splitlines():
        if "=" not in line:
            raise OSError("md RAID1 member identity is invalid")
        key, value = line.split("=", 1)
        if not key or key in fields or len(value) > 512:
            raise OSError("md RAID1 member identity is invalid")
        fields[key] = value
    devicefile = f"/dev/{fields.get('DEVNAME', '')}"
    if _WHOLE_DISK_PATTERN.fullmatch(devicefile) is None:
        raise ValueError("md RAID1 replacement supports whole-disk members only")
    return devicefile


def _sysfs_snapshot(target: str) -> dict[str, Any]:
    lines = [
        line.strip()
        for line in _run_checked("lsblk", "-n", "-d", "-o", "KNAME", target).splitlines()
        if line.strip()
    ]
    if len(lines) != 1 or _KERNEL_MD_PATTERN.fullmatch(lines[0]) is None:
        raise OSError("md RAID1 target did not resolve to one kernel md device")
    md_dir = _SYS_CLASS_BLOCK / lines[0] / "md"
    sync_action = _read_sysfs_text(md_dir / "sync_action")
    component_size_text = _read_sysfs_text(md_dir / "component_size")
    if not component_size_text.isdigit() or int(component_size_text) <= 0:
        raise OSError("md RAID1 component size is invalid")
    members: list[dict[str, Any]] = []
    try:
        member_dirs = sorted(
            entry for entry in md_dir.iterdir() if entry.name.startswith("dev-") and entry.is_dir()
        )
    except OSError as exc:
        raise OSError("md RAID1 kernel members are unavailable") from exc
    for member_dir in member_dirs:
        raw_state = _read_sysfs_text(member_dir / "state")
        states = sorted(token.strip() for token in raw_state.split(",") if token.strip())
        if not states or len(states) != len(set(states)) or not set(states) <= _KNOWN_MEMBER_STATES:
            raise OSError("md RAID1 member state is unsupported")
        slot_text = _read_sysfs_text(member_dir / "slot")
        if slot_text == "none":
            slot: int | None = None
        elif slot_text in {"0", "1"}:
            slot = int(slot_text)
        else:
            raise OSError("md RAID1 member slot is invalid")
        members.append(
            {
                "devicefile": _member_device(member_dir),
                "slot": slot,
                "states": states,
            }
        )
    paths = [member["devicefile"] for member in members]
    occupied_slots = [member["slot"] for member in members if member["slot"] is not None]
    if len(paths) != len(set(paths)) or len(occupied_slots) != len(set(occupied_slots)):
        raise OSError("md RAID1 kernel topology contains duplicate members")
    return {
        "kernelDevice": lines[0],
        "syncAction": sync_action,
        "componentSizeSectors": int(component_size_text),
        "members": members,
    }


def _existing_disk_identity(devicefile: str) -> dict[str, Any]:
    output = _run_checked(
        "lsblk",
        "-J",
        "-b",
        "-d",
        "-p",
        "-o",
        "PATH,TYPE,SIZE,SERIAL,WWN,RO,RM",
        devicefile,
    )
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as exc:
        raise OSError("lsblk returned invalid md RAID1 member identity") from exc
    rows = payload.get("blockdevices") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise OSError("lsblk did not return one md RAID1 member")
    row = rows[0]
    serial = row.get("serial") if isinstance(row.get("serial"), str) else ""
    wwn = row.get("wwn") if isinstance(row.get("wwn"), str) else ""
    size = row.get("size")
    if (
        row.get("path") != devicefile
        or row.get("type") != "disk"
        or isinstance(size, bool)
        or not isinstance(size, int)
        or size < 1024**3
        or row.get("ro") != 0
        or row.get("rm") != 0
        or not (serial.strip() or wwn.strip())
    ):
        raise ValueError("surviving md RAID1 member lacks a safe persistent whole-disk identity")
    return {
        "devicefile": devicefile,
        "sizeBytes": size,
        "serial": serial.strip() or None,
        "wwn": wwn.strip() or None,
    }


def _degraded_topology(name: str, array_uuid: str, config_path: Path) -> dict[str, Any]:
    identity = _configured_identity(name, array_uuid, config_path)
    detail_test = _run("mdadm", "--detail", "--test", identity["devicefile"])
    if detail_test.returncode != 1:
        raise ValueError("md RAID1 must be usable with exactly one failed device")
    fields = _parse_export_fields(
        _run_checked("mdadm", "--detail", "--export", identity["devicefile"])
    )
    if (
        fields.get("MD_LEVEL") != "raid1"
        or fields.get("MD_DEVICES") != "2"
        or fields.get("MD_METADATA") != "1.2"
        or fields.get("MD_DEVNAME") != f"echo-{name}"
        or fields.get("MD_UUID", "").lower() != array_uuid
        or _UUID_PATTERN.fullmatch(array_uuid) is None
        or fields.get("MD_RESHAPE_ACTIVE", "False") != "False"
    ):
        raise ValueError("managed md RAID1 identity or layout is not replaceable")
    kernel = _sysfs_snapshot(identity["devicefile"])
    if kernel["syncAction"] != "idle":
        raise ValueError("md RAID1 already has active recovery or maintenance")
    healthy = [
        member
        for member in kernel["members"]
        if member["states"] == ["in_sync"] and member["slot"] in {0, 1}
    ]
    faulty = [
        member
        for member in kernel["members"]
        if "faulty" in member["states"]
        and "in_sync" not in member["states"]
        and member["slot"] is None
        and set(member["states"]) <= {"faulty", "write_error"}
    ]
    recognized = {member["devicefile"] for member in [*healthy, *faulty]}
    if (
        len(healthy) != 1
        or len(faulty) > 1
        or len(kernel["members"]) not in {1, 2}
        or recognized != {member["devicefile"] for member in kernel["members"]}
    ):
        raise ValueError("replacement requires one in-sync member and one failed or missing member")
    survivor = _existing_disk_identity(healthy[0]["devicefile"])
    if survivor["sizeBytes"] < kernel["componentSizeSectors"] * 512:
        raise OSError("md RAID1 component size exceeds the surviving member")
    missing_slots = sorted({0, 1} - {healthy[0]["slot"]})
    if len(missing_slots) != 1:
        raise ValueError("md RAID1 does not have exactly one vacant RAID1 role")
    return {
        "array": identity,
        "kernelDevice": kernel["kernelDevice"],
        "syncAction": kernel["syncAction"],
        "survivingMember": {**healthy[0], **survivor},
        "failedMember": faulty[0] if faulty else None,
        "missingSlot": missing_slots[0],
        "minimumReplacementBytes": survivor["sizeBytes"],
        "topologyHash": _canonical_hash({"detail": fields, "kernel": kernel, "survivor": survivor}),
    }


def mdraid1_replacement_candidates(*, config_path: Path = _MDADM_CONFIG) -> list[dict[str, Any]]:
    """Return eligible degraded arrays and blank disks large enough to repair them."""
    _require_tools()
    config = _read_config(config_path)
    _begin, _end, entries = _managed_entries(config.decode("utf-8") if config else "")
    blank_devices: list[dict[str, Any]] = []
    output = _run_checked("lsblk", "-J", "-p", "-o", "PATH,TYPE")
    try:
        rows = json.loads(output).get("blockdevices")
    except (AttributeError, json.JSONDecodeError) as exc:
        raise OSError("lsblk returned invalid replacement inventory") from exc
    if not isinstance(rows, list):
        raise OSError("lsblk did not return a replacement inventory")
    for row in rows:
        if not isinstance(row, dict) or row.get("type") != "disk":
            continue
        devicefile = row.get("path")
        if not isinstance(devicefile, str):
            continue
        try:
            blank_devices.extend(inspect_blank_whole_disks([devicefile]))
        except ValueError:
            continue
    replacements: list[dict[str, Any]] = []
    for entry in entries:
        tokens = entry.split()
        name = tokens[1].removeprefix("/dev/md/echo-")
        array_uuid = tokens[2].removeprefix("UUID=")
        try:
            topology = _degraded_topology(name, array_uuid, config_path)
        except ValueError:
            continue
        compatible = [
            device
            for device in blank_devices
            if device["sizeBytes"] >= topology["minimumReplacementBytes"]
        ]
        replacements.append(
            {
                "array": topology["array"],
                "survivingMember": topology["survivingMember"],
                "failedMember": topology["failedMember"],
                "missingSlot": topology["missingSlot"],
                "minimumReplacementBytes": topology["minimumReplacementBytes"],
                "replacementDevices": compatible,
            }
        )
    return replacements


def _build_plan(desired: dict[str, Any], config_path: Path) -> dict[str, Any]:
    _require_tools()
    topology = _degraded_topology(desired["name"], desired["arrayUuid"], config_path)
    replacement = inspect_blank_whole_disks([desired["replacementDevice"]])[0]
    if replacement["sizeBytes"] < topology["minimumReplacementBytes"]:
        raise ValueError("replacement disk is smaller than the surviving RAID1 member")
    if replacement["devicefile"] in {
        topology["survivingMember"]["devicefile"],
        (topology["failedMember"] or {}).get("devicefile"),
    }:
        raise ValueError("replacement disk is already part of the md RAID1")
    base_revision = _canonical_hash(
        {
            "topologyHash": topology["topologyHash"],
            "replacement": replacement,
            "configSha256": topology["array"]["configSha256"],
        }
    )
    material = {
        "schema": MDRAID1_REPLACE_PLAN_SCHEMA,
        "operation": "replaceFailedMember",
        "desired": desired,
        "array": topology["array"],
        "survivingMember": topology["survivingMember"],
        "failedMember": topology["failedMember"],
        "missingSlot": topology["missingSlot"],
        "replacement": replacement,
        "minimumReplacementBytes": topology["minimumReplacementBytes"],
        "baseRevision": base_revision,
    }
    return {
        **material,
        "planId": _canonical_hash(material),
        "requiresApproval": True,
        "changes": [
            {
                "field": f"raidRole{topology['missingSlot']}",
                "before": (topology["failedMember"] or {}).get("devicefile"),
                "after": replacement["devicefile"],
            }
        ],
        "safety": {
            "data": "preservedDuringRecovery",
            "scope": "singleEchoManagedTwoDiskRaid1Only",
            "survivor": "oneKernelConfirmedInSyncMember",
            "failedMember": "kernelFaultyOrMissingOnly",
            "replacement": "wholeBlankNonRemovableWithPersistentIdentity",
            "minimumSize": "survivingMemberDeviceSize",
            "force": False,
            "marksHealthyMemberFaulty": False,
            "waitForRecovery": False,
            "rollback": "noneAfterReplacementAccepted",
        },
        "source": "native",
    }


def plan_mdraid1_replace(
    desired_state: dict[str, Any], *, config_path: Path = _MDADM_CONFIG
) -> dict[str, Any]:
    desired = validate_mdraid1_replace_desired(dict(desired_state))
    with _array_transaction():
        return _build_plan(desired, config_path)


def _replacement_accepted(plan: dict[str, Any]) -> dict[str, Any] | None:
    try:
        kernel = _sysfs_snapshot(plan["array"]["devicefile"])
        fields = _parse_export_fields(
            _run_checked("mdadm", "--detail", "--export", plan["array"]["devicefile"])
        )
    except (OSError, ValueError):
        return None
    if (
        fields.get("MD_UUID", "").lower() != plan["array"]["uuid"]
        or fields.get("MD_LEVEL") != "raid1"
        or fields.get("MD_DEVICES") != "2"
        or kernel["syncAction"] not in {"recover", "idle"}
    ):
        return None
    by_path = {member["devicefile"]: member for member in kernel["members"]}
    survivor = by_path.get(plan["survivingMember"]["devicefile"])
    replacement = by_path.get(plan["replacement"]["devicefile"])
    if (
        len(by_path) != 2
        or survivor is None
        or survivor["states"] != ["in_sync"]
        or replacement is None
        or "faulty" in replacement["states"]
        or "blocked" in replacement["states"]
        or not ({"spare", "in_sync", "replacement"} & set(replacement["states"]))
    ):
        return None
    return {
        "kernelDevice": kernel["kernelDevice"],
        "syncAction": kernel["syncAction"],
        "members": kernel["members"],
    }


def apply_mdraid1_replace(
    desired_state: dict[str, Any],
    plan_id: str,
    *,
    config_path: Path = _MDADM_CONFIG,
) -> dict[str, Any]:
    desired = validate_mdraid1_replace_desired(dict(desired_state))
    with _array_transaction():
        plan = _build_plan(desired, config_path)
        if plan["planId"] != plan_id:
            raise ValueError("md RAID1 replacement plan is stale; preview the change again")
        failed = plan["failedMember"]
        if failed is not None:
            try:
                _run_mutating(
                    "mdadm", plan["array"]["devicefile"], "--remove", failed["devicefile"]
                )
            except OSError:
                current_paths = {
                    member["devicefile"]
                    for member in _sysfs_snapshot(plan["array"]["devicefile"])["members"]
                }
                if failed["devicefile"] in current_paths:
                    raise
        try:
            _run_mutating(
                "mdadm",
                plan["array"]["devicefile"],
                "--add",
                plan["replacement"]["devicefile"],
            )
        except OSError as exc:
            accepted = _replacement_accepted(plan)
            if accepted is None:
                raise OSError(
                    "md RAID1 replacement was not accepted; the array remains degraded"
                ) from exc
        else:
            accepted = _replacement_accepted(plan)
        if accepted is None:
            raise OSError("md RAID1 did not retain the planned replacement disk")
        return {
            **plan,
            "applied": True,
            "verified": True,
            "dataPreserved": True,
            "maintenanceState": (
                "recovering" if accepted["syncAction"] == "recover" else "acceptedOrCompleted"
            ),
            "kernel": accepted,
        }


__all__ = [
    "apply_mdraid1_replace",
    "mdraid1_replacement_candidates",
    "plan_mdraid1_replace",
]
