"""Approval-bound scrub start for Echo-managed Btrfs RAID1 filesystems."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from appliance.native_btrfs import (
    _run,
    _run_mutating,
    btrfs_volume_transaction,
    managed_btrfs_filesystems,
)
from appliance.native_btrfs_health import probe_btrfs_filesystems
from appliance.native_storage_probe import run_readonly
from appliance.omv_protocol import BTRFS_SCRUB_PLAN_SCHEMA, validate_btrfs_scrub_desired

_FSTAB_PATH = Path("/etc/fstab")
_TRANSITION_ATTEMPTS = 21
_TRANSITION_INTERVAL_SECONDS = 0.25


def _canonical_hash(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def _require_tools() -> None:
    missing = [name for name in ("btrfs", "findmnt") if shutil.which(name) is None]
    if missing:
        raise OSError(f"native Btrfs scrub tools are unavailable: {', '.join(missing)}")


def _health_runner(*args: str, timeout: float) -> str:
    return run_readonly(args, timeout=timeout)


def _parse_scrub_status(output: str, *, expected_uuid: str) -> dict[str, Any]:
    uuid_match = re.search(r"^\s*UUID:\s*([0-9a-fA-F-]+)\s*$", output, re.MULTILINE)
    status_match = re.search(r"^\s*Status:\s*([A-Za-z -]+?)\s*$", output, re.MULTILINE)
    if uuid_match is None or uuid_match.group(1).lower() != expected_uuid or status_match is None:
        raise OSError("btrfs scrub status returned an invalid filesystem identity")
    raw_status = status_match.group(1).strip().casefold()
    state = {
        "running": "inProgress",
        "finished": "completed",
        "canceled": "failed",
        "cancelled": "failed",
        "aborted": "failed",
        "interrupted": "failed",
    }.get(raw_status)
    if state is None:
        raise OSError("btrfs scrub status returned an unknown state")
    percent_match = re.search(r"\((\d+(?:\.\d+)?)%\)", output)
    progress = None
    if percent_match is not None:
        progress = max(0, min(100, int(float(percent_match.group(1)))))
    error_summary = re.search(r"^Error summary:\s*(.+?)\s*$", output, re.MULTILINE)
    errors: int | None = None
    if error_summary is not None:
        summary = error_summary.group(1).strip().casefold()
        if summary == "no errors found":
            errors = 0
        else:
            counts = [int(value) for value in re.findall(r"(?:^|\s)[a-z_]+=([0-9]+)", summary)]
            errors = sum(counts) if counts else None
    return {
        "kind": "scrub",
        "state": state,
        "progressPercent": progress,
        "errors": errors,
    }


def _scrub_status(mountpoint: str, filesystem_uuid: str) -> tuple[dict[str, Any], str]:
    completed = _run("btrfs", "scrub", "status", "--raw", mountpoint)
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    combined = f"{stdout}\n{stderr}".casefold()
    if "no stats available" in combined:
        snapshot = {
            "kind": "scrub",
            "state": "idle",
            "progressPercent": None,
            "errors": None,
        }
        return snapshot, _canonical_hash(snapshot)
    if completed.returncode not in {0, 3}:
        detail = stderr.strip() or stdout.strip() or f"exit {completed.returncode}"
        raise OSError(f"btrfs scrub status failed: {detail[:512]}")
    snapshot = _parse_scrub_status(stdout, expected_uuid=filesystem_uuid)
    return snapshot, _canonical_hash(stdout)


def _health_inventory() -> list[dict[str, Any]]:
    probe = probe_btrfs_filesystems(
        expected=True,
        runner=_health_runner,
        checked_at=datetime.now(UTC).isoformat(),
    )
    if not probe.value:
        raise OSError("mounted Btrfs health inventory is unavailable")
    return probe.value


def _exclusive_operation(filesystem_uuid: str) -> str:
    # Replacement reuses this module's scrub parser, so keep the shared sysfs
    # probe lazy to avoid a module-import cycle.
    from appliance.native_btrfs_replace import _sysfs_snapshot

    return str(_sysfs_snapshot(filesystem_uuid)["exclusiveOperation"])


def _maintenance_inventory(*, fstab_path: Path) -> list[dict[str, Any]]:
    _require_tools()
    managed = managed_btrfs_filesystems(fstab_path=fstab_path)
    if not managed:
        return []
    health_by_uuid = {item["uuid"]: item for item in _health_inventory()}
    maintenance: list[dict[str, Any]] = []
    for registered in managed:
        filesystem = health_by_uuid.get(registered["uuid"])
        if filesystem is None or filesystem["mountpoint"] != registered["mountpoint"]:
            raise OSError("an Echo-managed Btrfs filesystem is not mounted at its registered path")
        scan, status_hash = _scrub_status(filesystem["mountpoint"], filesystem["uuid"])
        exclusive_operation = _exclusive_operation(filesystem["uuid"])
        topology_safe = (
            filesystem["missingDevices"] == 0
            and filesystem["readOnly"] is False
            and filesystem["dataProfile"] == "raid1"
            and filesystem["metadataProfile"] == "raid1"
        )
        maintenance.append(
            {
                "filesystem": filesystem,
                "scan": scan,
                "exclusiveOperation": exclusive_operation,
                "canStartScrub": topology_safe
                and exclusive_operation == "none"
                and scan["state"] in {"idle", "completed", "failed"},
                "_statusHash": status_hash,
            }
        )
    return maintenance


def btrfs_scrub_maintenance(*, fstab_path: Path = _FSTAB_PATH) -> list[dict[str, Any]]:
    with btrfs_volume_transaction():
        records = _maintenance_inventory(fstab_path=fstab_path)
        return [
            {key: value for key, value in record.items() if not key.startswith("_")}
            for record in records
        ]


def _build_plan(desired: dict[str, str], *, fstab_path: Path) -> dict[str, Any]:
    matches = [
        record
        for record in _maintenance_inventory(fstab_path=fstab_path)
        if record["filesystem"]["uuid"] == desired["filesystemUuid"]
    ]
    if len(matches) != 1:
        raise ValueError("Echo-managed Btrfs filesystem UUID is unavailable or ambiguous")
    record = matches[0]
    if not record["canStartScrub"]:
        raise ValueError(
            "Btrfs scrub requires a complete writable RAID1 with no active exclusive operation"
        )
    filesystem = record["filesystem"]
    material = {
        "schema": BTRFS_SCRUB_PLAN_SCHEMA,
        "operation": "start",
        "desired": desired,
        "filesystem": filesystem,
        "before": record["scan"],
        "exclusiveOperation": record["exclusiveOperation"],
        "baseRevision": _canonical_hash(
            {
                "filesystem": filesystem,
                "scan": record["scan"],
                "exclusiveOperation": record["exclusiveOperation"],
                "statusHash": record["_statusHash"],
            }
        ),
    }
    return {
        **material,
        "planId": _canonical_hash(material),
        "requiresApproval": True,
        "changes": [{"field": "maintenance", "before": record["scan"]["state"], "after": "scrub"}],
        "safety": {
            "scope": "echoManagedMountedBtrfsRaid1Only",
            "data": "checksummedReplicasMayBeReadAndRepaired",
            "activeMaintenance": "mustBeAbsent",
            "ioLoad": "high",
            "wait": False,
            "readOnly": False,
            "force": False,
            "cancel": False,
            "rollback": "noneAfterScrubAccepted",
        },
        "source": "native",
        "_statusHash": record["_statusHash"],
    }


def plan_btrfs_scrub(
    desired_state: dict[str, Any], *, fstab_path: Path = _FSTAB_PATH
) -> dict[str, Any]:
    desired = validate_btrfs_scrub_desired(dict(desired_state))
    with btrfs_volume_transaction():
        plan = _build_plan(desired, fstab_path=fstab_path)
        plan.pop("_statusHash", None)
        return plan


def _verified_transition(
    mountpoint: str,
    filesystem_uuid: str,
    *,
    previous_status_hash: str,
    attempts: int = _TRANSITION_ATTEMPTS,
    sleeper: Callable[[float], None] = time.sleep,
) -> tuple[dict[str, Any], str] | None:
    if attempts < 1:
        raise ValueError("Btrfs scrub transition attempts must be positive")
    for attempt in range(attempts):
        scan, status_hash = _scrub_status(mountpoint, filesystem_uuid)
        if status_hash != previous_status_hash and scan["state"] in {
            "inProgress",
            "completed",
        }:
            state = (
                "scrubbing"
                if scan["state"] == "inProgress"
                else "completedWithErrors"
                if scan["errors"]
                else "completed"
            )
            return scan, state
        if attempt + 1 < attempts:
            sleeper(_TRANSITION_INTERVAL_SECONDS)
    return None


def apply_btrfs_scrub(
    desired_state: dict[str, Any],
    plan_id: str,
    *,
    fstab_path: Path = _FSTAB_PATH,
) -> dict[str, Any]:
    desired = validate_btrfs_scrub_desired(dict(desired_state))
    with btrfs_volume_transaction():
        plan = _build_plan(desired, fstab_path=fstab_path)
        if plan["planId"] != plan_id:
            raise ValueError("Btrfs scrub plan is stale; preview the change again")
        previous_status_hash = plan.pop("_statusHash")
        mountpoint = plan["filesystem"]["mountpoint"]
        command_error: Exception | None = None
        try:
            _run_mutating("btrfs", "scrub", "start", mountpoint)
        except Exception as exc:
            command_error = exc
        try:
            transition = _verified_transition(
                mountpoint,
                desired["filesystemUuid"],
                previous_status_hash=previous_status_hash,
            )
        except Exception as state_exc:
            if command_error is not None:
                raise OSError(
                    "Btrfs scrub command failed and the resulting filesystem state is unknown"
                ) from state_exc
            raise
        if transition is None:
            if command_error is not None:
                raise command_error
            raise OSError("Btrfs did not report the planned scrub after accepting the command")
        scan, maintenance_state = transition
        return {
            **plan,
            "applied": True,
            "verified": True,
            "scan": scan,
            "maintenanceState": maintenance_state,
        }


__all__ = [
    "apply_btrfs_scrub",
    "btrfs_scrub_maintenance",
    "plan_btrfs_scrub",
]
