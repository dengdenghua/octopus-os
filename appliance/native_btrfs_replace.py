"""Plan-bound replacement of one missing Echo-managed Btrfs RAID1 member.

This intentionally small repair surface accepts only a mounted, writable,
two-device Btrfs filesystem whose data and metadata profiles are both RAID1.
The mounted filesystem and ``btrfs filesystem show`` must identify exactly one
member as missing.  Sysfs is used to reject unsafe maintenance, error, and
survivor states, while tolerating the stale missing flag observed after a live
device loss.  The replacement is one blank whole disk with a stable hardware
identity and is never forced or automatically resized.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from appliance.native_btrfs import (
    _run,
    _run_checked,
    _run_mutating,
    btrfs_volume_transaction,
    managed_btrfs_filesystems,
)
from appliance.native_btrfs_health import probe_btrfs_filesystems
from appliance.native_btrfs_scrub import _scrub_status
from appliance.native_storage_pool import inspect_blank_whole_disks
from appliance.native_storage_probe import run_readonly
from appliance.omv_protocol import BTRFS_REPLACE_PLAN_SCHEMA, validate_btrfs_replace_desired

_FSTAB_PATH = Path("/etc/fstab")
_SYS_FS_BTRFS = Path("/sys/fs/btrfs")
_WHOLE_DISK_PATTERN = re.compile(r"/dev/(?:sd[a-z]+|vd[a-z]+|xvd[a-z]+|nvme\d+n\d+|mmcblk\d+)")
_UUID_PATTERN = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_MAX_SYSFS_BYTES = 4096
_ACCEPTANCE_TIMEOUT_SECONDS = 15.0
_ACCEPTANCE_POLL_SECONDS = 0.1
_ERROR_NAMES = frozenset(
    {"write_errs", "read_errs", "flush_errs", "corruption_errs", "generation_errs"}
)


def _canonical_hash(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def _require_tools() -> None:
    missing = [
        name for name in ("btrfs", "findmnt", "lsblk", "wipefs") if shutil.which(name) is None
    ]
    if missing:
        raise OSError(f"native Btrfs replacement tools are unavailable: {', '.join(missing)}")


def _read_sysfs_text(path: Path) -> str:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise OSError(f"Btrfs kernel state is unavailable: {path.name}") from exc
    if not payload or len(payload) > _MAX_SYSFS_BYTES or b"\x00" in payload:
        raise OSError("Btrfs kernel state is invalid")
    try:
        return payload.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise OSError("Btrfs kernel state is invalid") from exc


def _zero_or_one(path: Path) -> int:
    value = _read_sysfs_text(path)
    if value not in {"0", "1"}:
        raise OSError("Btrfs kernel device state is invalid")
    return int(value)


def _parse_error_stats(value: str) -> dict[str, int]:
    tokens = value.split()
    if len(tokens) != len(_ERROR_NAMES) * 2:
        raise OSError("Btrfs kernel error counters are invalid")
    counters: dict[str, int] = {}
    for index in range(0, len(tokens), 2):
        name, raw_count = tokens[index : index + 2]
        if name not in _ERROR_NAMES or name in counters or not raw_count.isdigit():
            raise OSError("Btrfs kernel error counters are invalid")
        count = int(raw_count)
        if count > 2**63 - 1:
            raise OSError("Btrfs kernel error counter is out of range")
        counters[name] = count
    if set(counters) != _ERROR_NAMES:
        raise OSError("Btrfs kernel error counters are incomplete")
    return dict(sorted(counters.items()))


def _sysfs_snapshot(filesystem_uuid: str, *, sysfs_root: Path = _SYS_FS_BTRFS) -> dict[str, Any]:
    if _UUID_PATTERN.fullmatch(filesystem_uuid) is None:
        raise ValueError("Btrfs filesystem UUID is invalid")
    filesystem_dir = sysfs_root / filesystem_uuid
    exclusive_operation = _read_sysfs_text(filesystem_dir / "exclusive_operation")
    if exclusive_operation not in {
        "none",
        "balance",
        "balance paused",
        "device add",
        "device delete",
        "device replace",
        "resize",
        "swapfile activate",
    }:
        raise OSError("Btrfs exclusive operation state is unknown")
    devinfo = filesystem_dir / "devinfo"
    try:
        member_dirs = sorted(
            (entry for entry in devinfo.iterdir() if entry.is_dir()),
            key=lambda entry: int(entry.name) if entry.name.isdigit() else 2**63,
        )
    except OSError as exc:
        raise OSError("Btrfs kernel member inventory is unavailable") from exc
    if not member_dirs or any(not entry.name.isdigit() for entry in member_dirs):
        raise OSError("Btrfs kernel member inventory is invalid")
    members: list[dict[str, Any]] = []
    for member_dir in member_dirs:
        devid = int(member_dir.name)
        if not 1 <= devid <= 2**32 - 1:
            raise OSError("Btrfs kernel device ID is out of range")
        counters = _parse_error_stats(_read_sysfs_text(member_dir / "error_stats"))
        members.append(
            {
                "devid": devid,
                "missing": bool(_zero_or_one(member_dir / "missing")),
                "replaceTarget": bool(_zero_or_one(member_dir / "replace_target")),
                "writeable": bool(_zero_or_one(member_dir / "writeable")),
                "errorStats": counters,
                "errorCount": sum(counters.values()),
            }
        )
    ids = [member["devid"] for member in members]
    if len(ids) != len(set(ids)):
        raise OSError("Btrfs kernel member inventory contains duplicate device IDs")
    return {"exclusiveOperation": exclusive_operation, "members": members}


def _parse_filesystem_show(output: str, *, expected_uuid: str) -> dict[str, Any]:
    uuid_match = re.search(r"\buuid:\s*([0-9a-fA-F-]+)\s*$", output, re.MULTILINE)
    total_match = re.search(r"^\s*Total devices\s+(\d+)\b", output, re.MULTILINE)
    if (
        uuid_match is None
        or uuid_match.group(1).lower() != expected_uuid
        or total_match is None
        or int(total_match.group(1)) != 2
    ):
        raise OSError("Btrfs filesystem show returned an unsupported identity")
    members: list[dict[str, Any]] = []
    pattern = re.compile(
        r"^\s*devid\s+(\d+)\s+size\s+(\d+)\s+used\s+(\d+)\s+path\s+(.*?)\s*$",
        re.MULTILINE,
    )
    for match in pattern.finditer(output):
        devid, size_bytes, used_bytes = (int(match.group(index)) for index in (1, 2, 3))
        path = " ".join(match.group(4).split())
        missing_source = path.removesuffix(" MISSING") if path.endswith(" MISSING") else None
        is_missing = path in {"missing", "MISSING", "<missing disk> MISSING"} or (
            missing_source is not None and _WHOLE_DISK_PATTERN.fullmatch(missing_source) is not None
        )
        if (
            not 1 <= devid <= 2**32 - 1
            or used_bytes > size_bytes
            or (is_missing and (size_bytes == 0) != (used_bytes == 0))
            or (not is_missing and size_bytes < 1024**3)
            or (not is_missing and _WHOLE_DISK_PATTERN.fullmatch(path) is None)
        ):
            raise OSError("Btrfs filesystem show returned an unsafe member")
        members.append(
            {
                "devid": devid,
                "sizeBytes": size_bytes,
                "usedBytes": used_bytes,
                "devicefile": None if is_missing else path,
                "missing": is_missing,
            }
        )
    if len(members) != 2 or len({member["devid"] for member in members}) != 2:
        raise OSError("Btrfs filesystem show did not retain exactly two members")
    return {"totalDevices": 2, "members": sorted(members, key=lambda item: item["devid"])}


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
        raise OSError("lsblk returned invalid Btrfs member identity") from exc
    rows = payload.get("blockdevices") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise OSError("lsblk did not return one Btrfs member")
    row = rows[0]
    size = row.get("size")
    serial = row.get("serial") if isinstance(row.get("serial"), str) else ""
    wwn = row.get("wwn") if isinstance(row.get("wwn"), str) else ""
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
        raise ValueError("surviving Btrfs member lacks a safe persistent whole-disk identity")
    return {
        "devicefile": devicefile,
        "sizeBytes": size,
        "serial": serial.strip() or None,
        "wwn": wwn.strip() or None,
    }


def _health_runner(*args: str, timeout: float) -> str:
    return run_readonly(args, timeout=timeout)


def _health_inventory() -> list[dict[str, Any]]:
    probe = probe_btrfs_filesystems(
        expected=True,
        runner=_health_runner,
        checked_at=datetime.now(UTC).isoformat(),
    )
    if not probe.value:
        raise OSError("mounted Btrfs health inventory is unavailable")
    return probe.value


def _blank_disk_candidates() -> list[dict[str, Any]]:
    output = _run_checked("lsblk", "-J", "-p", "-o", "PATH,TYPE")
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as exc:
        raise OSError("lsblk returned invalid Btrfs replacement inventory") from exc
    rows = payload.get("blockdevices") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise OSError("lsblk did not return a Btrfs replacement inventory")
    candidates: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or row.get("type") != "disk":
            continue
        devicefile = row.get("path")
        if not isinstance(devicefile, str):
            continue
        try:
            candidates.extend(inspect_blank_whole_disks([devicefile]))
        except ValueError:
            continue
    return sorted(candidates, key=lambda item: item["devicefile"])


def _parse_replace_status(output: str) -> dict[str, Any]:
    normalized = " ".join(output.strip().split())
    lowered = normalized.casefold()
    if not normalized or len(output.encode("utf-8")) > 64 * 1024:
        raise OSError("btrfs replace status returned invalid output")
    if lowered == "never started":
        state = "idle"
    elif re.search(r"\b\d+(?:\.\d+)?%\s+done\b", lowered):
        state = "inProgress"
    elif "finished on" in lowered:
        state = "completed"
    elif "canceled on" in lowered or "cancelled on" in lowered or "failed" in lowered:
        state = "failed"
    else:
        raise OSError("btrfs replace status returned an unknown state")
    percent_match = re.search(r"\b(\d+(?:\.\d+)?)%\s+done\b", lowered)
    progress = None
    if percent_match is not None:
        numeric = float(percent_match.group(1))
        if not 0 <= numeric <= 100:
            raise OSError("btrfs replace status returned invalid progress")
        progress = int(numeric)
    error_counts = [
        int(value) for value in re.findall(r"\b(\d+)\s+(?:write|uncorr\. read) errs\b", lowered)
    ]
    return {
        "kind": "deviceReplace",
        "state": state,
        "progressPercent": progress,
        "errors": sum(error_counts) if error_counts else None,
    }


def _replace_status(mountpoint: str) -> tuple[dict[str, Any], str]:
    completed = _run("btrfs", "replace", "status", "-1", mountpoint)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip() or f"exit {completed.returncode}"
        raise OSError(f"btrfs replace status failed: {detail[:512]}")
    status = _parse_replace_status(completed.stdout)
    return status, _canonical_hash(completed.stdout)


def _degraded_topology(
    filesystem_uuid: str,
    *,
    fstab_path: Path,
    sysfs_root: Path,
) -> dict[str, Any]:
    managed = [
        item
        for item in managed_btrfs_filesystems(fstab_path=fstab_path)
        if item["uuid"] == filesystem_uuid
    ]
    if len(managed) != 1:
        raise ValueError("Btrfs filesystem is not uniquely owned by Echo")
    health = [item for item in _health_inventory() if item["uuid"] == filesystem_uuid]
    if len(health) != 1 or health[0]["mountpoint"] != managed[0]["mountpoint"]:
        raise ValueError("Echo-managed Btrfs filesystem is not mounted at its registered path")
    filesystem = health[0]
    if (
        filesystem["totalDevices"] != 2
        or filesystem["activeDevices"] != 1
        or filesystem["missingDevices"] != 1
        or filesystem["readOnly"] is not False
        or filesystem["dataProfile"] != "raid1"
        or filesystem["metadataProfile"] != "raid1"
    ):
        raise ValueError("replacement requires one missing member in a writable two-device RAID1")
    kernel = _sysfs_snapshot(filesystem_uuid, sysfs_root=sysfs_root)
    if kernel["exclusiveOperation"] != "none" or any(
        member["replaceTarget"] for member in kernel["members"]
    ):
        raise ValueError("Btrfs already has an active exclusive operation")
    if len(kernel["members"]) != 2:
        raise ValueError("Btrfs kernel topology is unsafe")
    layout = _parse_filesystem_show(
        _run_checked("btrfs", "filesystem", "show", "--raw", filesystem["mountpoint"]),
        expected_uuid=filesystem_uuid,
    )
    if {item["devid"] for item in layout["members"]} != {
        item["devid"] for item in kernel["members"]
    }:
        raise OSError("Btrfs command and kernel member inventories disagree")
    layout_missing = [item for item in layout["members"] if item["missing"]]
    layout_survivors = [item for item in layout["members"] if not item["missing"]]
    if len(layout_missing) != 1 or len(layout_survivors) != 1:
        raise ValueError("Btrfs command output does not confirm the missing member")
    kernel_by_devid = {member["devid"]: member for member in kernel["members"]}
    layout_missing_devid = layout_missing[0]["devid"]
    kernel_missing_devids = {member["devid"] for member in kernel["members"] if member["missing"]}
    if kernel_missing_devids not in (set(), {layout_missing_devid}):
        raise ValueError("Btrfs command and kernel missing-member states disagree")
    survivor_kernel = kernel_by_devid[layout_survivors[0]["devid"]]
    if (
        survivor_kernel["missing"]
        or not survivor_kernel["writeable"]
        or survivor_kernel["errorCount"]
    ):
        raise ValueError("Btrfs kernel does not confirm a writable surviving member")
    survivor = {**survivor_kernel, **layout_survivors[0]}
    survivor.update(_existing_disk_identity(layout_survivors[0]["devicefile"]))
    scrub, scrub_hash = _scrub_status(filesystem["mountpoint"], filesystem_uuid)
    if scrub["state"] == "inProgress":
        raise ValueError("Btrfs scrub is already active")
    replace, replace_hash = _replace_status(filesystem["mountpoint"])
    if replace["state"] == "inProgress":
        raise ValueError("Btrfs device replacement is already active")
    return {
        "filesystem": filesystem,
        "missingMember": layout_missing[0],
        "survivingMember": survivor,
        "minimumReplacementBytes": max(layout_missing[0]["sizeBytes"], survivor["sizeBytes"]),
        "scrub": scrub,
        "replace": replace,
        "topologyHash": _canonical_hash(
            {"filesystem": filesystem, "kernel": kernel, "layout": layout, "survivor": survivor}
        ),
        "scrubStatusHash": scrub_hash,
        "replaceStatusHash": replace_hash,
    }


def btrfs_replacement_candidates(
    *, fstab_path: Path = _FSTAB_PATH, sysfs_root: Path = _SYS_FS_BTRFS
) -> list[dict[str, Any]]:
    _require_tools()
    blank_devices = _blank_disk_candidates()
    replacements: list[dict[str, Any]] = []
    for registered in managed_btrfs_filesystems(fstab_path=fstab_path):
        try:
            topology = _degraded_topology(
                registered["uuid"], fstab_path=fstab_path, sysfs_root=sysfs_root
            )
        except ValueError:
            continue
        compatible = [
            disk
            for disk in blank_devices
            if disk["sizeBytes"] >= topology["minimumReplacementBytes"]
            and disk["devicefile"] != topology["survivingMember"]["devicefile"]
        ]
        replacements.append(
            {
                "filesystem": topology["filesystem"],
                "missingMember": topology["missingMember"],
                "survivingMember": topology["survivingMember"],
                "minimumReplacementBytes": topology["minimumReplacementBytes"],
                "replacementDevices": compatible,
            }
        )
    return replacements


def _build_plan(desired: dict[str, Any], *, fstab_path: Path, sysfs_root: Path) -> dict[str, Any]:
    _require_tools()
    topology = _degraded_topology(
        desired["filesystemUuid"], fstab_path=fstab_path, sysfs_root=sysfs_root
    )
    if topology["missingMember"]["devid"] != desired["missingDevid"]:
        raise ValueError("planned Btrfs missing device ID no longer matches the kernel")
    replacement = _blank_disk_candidates()
    replacement = [
        disk for disk in replacement if disk["devicefile"] == desired["replacementDevice"]
    ]
    if len(replacement) != 1:
        raise ValueError("replacement disk is not one currently blank whole-disk candidate")
    replacement_disk = replacement[0]
    if replacement_disk["sizeBytes"] < topology["minimumReplacementBytes"]:
        raise ValueError("replacement disk is smaller than the missing Btrfs member")
    if replacement_disk["devicefile"] == topology["survivingMember"]["devicefile"]:
        raise ValueError("replacement disk is already the surviving Btrfs member")
    material = {
        "schema": BTRFS_REPLACE_PLAN_SCHEMA,
        "operation": "replaceMissingMember",
        "desired": desired,
        "filesystem": topology["filesystem"],
        "missingMember": topology["missingMember"],
        "survivingMember": topology["survivingMember"],
        "replacement": replacement_disk,
        "minimumReplacementBytes": topology["minimumReplacementBytes"],
        "before": topology["replace"],
        "baseRevision": _canonical_hash(
            {
                "topologyHash": topology["topologyHash"],
                "replacement": replacement_disk,
                "scrubStatusHash": topology["scrubStatusHash"],
                "replaceStatusHash": topology["replaceStatusHash"],
            }
        ),
    }
    return {
        **material,
        "planId": _canonical_hash(material),
        "requiresApproval": True,
        "changes": [
            {
                "field": f"device.{desired['missingDevid']}",
                "before": "missing",
                "after": replacement_disk["devicefile"],
            }
        ],
        "safety": {
            "scope": "singleEchoManagedMountedTwoDeviceBtrfsRaid1Only",
            "data": "preservedFromRemainingRaid1Replica",
            "missingMember": "kernelConfirmedNumericDeviceId",
            "replacement": "wholeBlankNonRemovableWithPersistentIdentity",
            "minimumSize": "largerOfRecordedMissingAndSurvivingMemberSize",
            "activeMaintenance": "mustBeAbsent",
            "force": False,
            "readFromSourceOnly": False,
            "foreground": False,
            "autoResize": False,
            "degradedRemount": False,
            "suspendOrFreezeMayCancel": True,
            "rollback": "noneAfterReplacementAccepted",
        },
        "source": "native",
        "_replaceStatusHash": topology["replaceStatusHash"],
    }


def plan_btrfs_replace(
    desired_state: dict[str, Any],
    *,
    fstab_path: Path = _FSTAB_PATH,
    sysfs_root: Path = _SYS_FS_BTRFS,
) -> dict[str, Any]:
    desired = validate_btrfs_replace_desired(dict(desired_state))
    with btrfs_volume_transaction():
        plan = _build_plan(desired, fstab_path=fstab_path, sysfs_root=sysfs_root)
        plan.pop("_replaceStatusHash", None)
        return plan


def _replacement_accepted(
    plan: dict[str, Any], *, previous_status_hash: str, sysfs_root: Path
) -> tuple[dict[str, Any], str] | None:
    status: dict[str, Any] | None = None
    status_hash: str | None = None
    with suppress(OSError):
        status, status_hash = _replace_status(plan["filesystem"]["mountpoint"])
    kernel = _sysfs_snapshot(plan["filesystem"]["uuid"], sysfs_root=sysfs_root)
    targets = [member for member in kernel["members"] if member["replaceTarget"]]
    if (
        status is not None
        and status_hash != previous_status_hash
        and status["state"] == "inProgress"
        and len(targets) == 1
        and kernel["exclusiveOperation"] == "device replace"
    ):
        return status, "replacing"
    if status is None and len(targets) == 1 and kernel["exclusiveOperation"] == "device replace":
        return {
            "kind": "deviceReplace",
            "state": "inProgress",
            "progressPercent": None,
            "errors": None,
        }, "replacing"
    if (
        status is not None
        and status_hash != previous_status_hash
        and status["state"] == "completed"
        and not targets
        and kernel["exclusiveOperation"] == "none"
    ):
        layout = _parse_filesystem_show(
            _run_checked("btrfs", "filesystem", "show", "--raw", plan["filesystem"]["mountpoint"]),
            expected_uuid=plan["filesystem"]["uuid"],
        )
        expected = next(
            (
                member
                for member in layout["members"]
                if member["devid"] == plan["desired"]["missingDevid"]
            ),
            None,
        )
        if (
            expected is not None
            and not expected["missing"]
            and os.path.realpath(expected["devicefile"])
            == os.path.realpath(plan["replacement"]["devicefile"])
        ):
            return status, "completedWithErrors" if status["errors"] else "acceptedOrCompleted"
    return None


def apply_btrfs_replace(
    desired_state: dict[str, Any],
    plan_id: str,
    *,
    fstab_path: Path = _FSTAB_PATH,
    sysfs_root: Path = _SYS_FS_BTRFS,
) -> dict[str, Any]:
    desired = validate_btrfs_replace_desired(dict(desired_state))
    with btrfs_volume_transaction():
        plan = _build_plan(desired, fstab_path=fstab_path, sysfs_root=sysfs_root)
        if plan["planId"] != plan_id:
            raise ValueError("Btrfs replacement plan is stale; preview the change again")
        previous_status_hash = plan.pop("_replaceStatusHash")
        command_error: Exception | None = None
        try:
            _run_mutating(
                "btrfs",
                "replace",
                "start",
                str(desired["missingDevid"]),
                plan["replacement"]["devicefile"],
                plan["filesystem"]["mountpoint"],
            )
        except Exception as exc:
            command_error = exc
        try:
            deadline = time.monotonic() + _ACCEPTANCE_TIMEOUT_SECONDS
            last_state_error: OSError | ValueError | None = None
            while True:
                try:
                    accepted = _replacement_accepted(
                        plan, previous_status_hash=previous_status_hash, sysfs_root=sysfs_root
                    )
                    last_state_error = None
                except (OSError, ValueError) as exc:
                    accepted = None
                    last_state_error = exc
                if accepted is not None:
                    break
                if time.monotonic() >= deadline:
                    if last_state_error is not None:
                        raise last_state_error
                    break
                time.sleep(_ACCEPTANCE_POLL_SECONDS)
        except Exception as state_exc:
            if command_error is not None:
                raise OSError(
                    "Btrfs replacement command failed and the resulting filesystem state is unknown"
                ) from state_exc
            raise
        if accepted is None:
            if command_error is not None:
                raise OSError(
                    "Btrfs replacement was not accepted; the filesystem remains degraded"
                ) from command_error
            raise OSError(
                "Btrfs did not report the planned replacement after accepting the command"
            )
        status, maintenance_state = accepted
        return {
            **plan,
            "applied": True,
            "verified": True,
            "dataPreserved": True,
            "maintenanceState": maintenance_state,
            "replacementStatus": status,
        }


__all__ = [
    "apply_btrfs_replace",
    "btrfs_replacement_candidates",
    "plan_btrfs_replace",
]
