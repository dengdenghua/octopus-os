"""Plan-bound consistency checks for healthy Echo-managed md RAID1 arrays.

Only the kernel's ``check`` action is exposed.  The workflow never explicitly
starts ``repair``, never changes membership, and refuses degraded arrays or an
array with another recovery/maintenance action already in progress.  Linux may
still repair a media read error while servicing the scan.
"""

from __future__ import annotations

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
from appliance.native_mdraid_replace import (
    _configured_identity,
    _read_sysfs_text,
    _sysfs_snapshot,
)
from appliance.omv_protocol import MDRAID_CHECK_PLAN_SCHEMA, validate_mdraid_check_desired

_SYS_CLASS_BLOCK = Path("/sys/class/block")
_UUID_PATTERN = re.compile(r"[0-9a-f]{8}(?::[0-9a-f]{8}){3}")
_SYNC_COMPLETED_PATTERN = re.compile(r"(?P<done>\d+)\s*/\s*(?P<total>\d+)")
_SYNC_ACTIONS = frozenset({"idle", "frozen", "resync", "recover", "check", "repair", "reshape"})


def _require_tools() -> None:
    missing = [name for name in ("mdadm", "lsblk") if shutil.which(name) is None]
    if missing:
        raise OSError(f"md RAID1 consistency tools are unavailable: {', '.join(missing)}")


def _progress(sync_completed: str) -> float | None:
    if sync_completed == "none":
        return None
    match = _SYNC_COMPLETED_PATTERN.fullmatch(sync_completed)
    if match is None:
        raise OSError("md RAID1 consistency progress is invalid")
    done = int(match.group("done"))
    total = int(match.group("total"))
    if total <= 0 or done < 0:
        raise OSError("md RAID1 consistency progress is invalid")
    return min(round(done * 100 / total, 2), 100.0)


def _maintenance_snapshot(
    name: str,
    array_uuid: str,
    *,
    config_path: Path,
) -> dict[str, Any]:
    identity = _configured_identity(name, array_uuid, config_path)
    detail_test = _run("mdadm", "--detail", "--test", identity["devicefile"])
    if detail_test.returncode not in {0, 1}:
        raise OSError("mdadm could not determine the managed RAID1 health")
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
        raise ValueError("managed md RAID1 identity or layout cannot be checked")

    kernel = _sysfs_snapshot(identity["devicefile"])
    action = kernel["syncAction"]
    if action not in _SYNC_ACTIONS:
        raise OSError("md RAID1 reported an unsupported maintenance action")
    md_root = _SYS_CLASS_BLOCK / kernel["kernelDevice"] / "md"
    degraded_text = _read_sysfs_text(md_root / "degraded")
    mismatch_text = _read_sysfs_text(md_root / "mismatch_cnt")
    completed_text = _read_sysfs_text(md_root / "sync_completed")
    if not degraded_text.isdigit() or int(degraded_text) not in {0, 1, 2}:
        raise OSError("md RAID1 degraded count is invalid")
    if not mismatch_text.isdigit() or int(mismatch_text) < 0:
        raise OSError("md RAID1 mismatch count is invalid")

    members = kernel["members"]
    healthy_members = [
        member for member in members if member["states"] == ["in_sync"] and member["slot"] in {0, 1}
    ]
    healthy = (
        detail_test.returncode == 0
        and int(degraded_text) == 0
        and len(members) == 2
        and len(healthy_members) == 2
        and {member["slot"] for member in healthy_members} == {0, 1}
    )
    snapshot_material = {
        "array": identity,
        "kernelDevice": kernel["kernelDevice"],
        "members": members,
        "healthy": healthy,
        "degradedDevices": int(degraded_text),
        "action": action,
        "progressPercent": _progress(completed_text),
        "mismatchCount": int(mismatch_text),
    }
    return {
        **snapshot_material,
        "stateHash": _canonical_hash(snapshot_material),
        "canStartCheck": healthy and action == "idle",
    }


def mdraid_maintenance(*, config_path: Path = _MDADM_CONFIG) -> list[dict[str, Any]]:
    """Return bounded consistency state for every assembled Echo-managed RAID1."""
    _require_tools()
    config = _read_config(config_path)
    _begin, _end, entries = _managed_entries(config.decode("utf-8") if config else "")
    result: list[dict[str, Any]] = []
    for entry in entries:
        tokens = entry.split()
        target = tokens[1]
        result.append(
            _maintenance_snapshot(
                target.removeprefix("/dev/md/echo-"),
                tokens[2].removeprefix("UUID="),
                config_path=config_path,
            )
        )
    return result


def _build_plan(desired: dict[str, Any], *, config_path: Path) -> dict[str, Any]:
    _require_tools()
    before = _maintenance_snapshot(
        desired["name"],
        desired["arrayUuid"],
        config_path=config_path,
    )
    if not before["healthy"]:
        raise ValueError("only a healthy two-member Echo RAID1 can start a consistency check")
    if before["action"] != "idle":
        raise ValueError("wait for the active md RAID1 recovery or maintenance operation")
    plan_material = {
        "schema": MDRAID_CHECK_PLAN_SCHEMA,
        "baseRevision": before["stateHash"],
        "operation": "start",
        "desired": desired,
        "array": before["array"],
        "before": {
            key: before[key]
            for key in (
                "healthy",
                "degradedDevices",
                "action",
                "progressPercent",
                "mismatchCount",
                "stateHash",
            )
        },
    }
    return {
        **plan_material,
        "planId": _canonical_hash(plan_material),
        "requiresApproval": True,
        "changes": [{"field": "maintenance", "before": "idle", "after": "check"}],
        "safety": {
            "data": "redundancyConsistencyScan",
            "scope": "echoManagedHealthyRaid1Only",
            "explicitRepair": False,
            "kernelReadErrorRecovery": "mayOccur",
            "membershipChanges": False,
            "activeMaintenance": "mustBeAbsent",
            "ioLoad": "high",
            "wait": False,
            "stop": False,
            "rollback": "noneAfterCheckAccepted",
        },
        "source": "native",
    }


def plan_mdraid_check(
    desired_state: dict[str, Any],
    *,
    config_path: Path = _MDADM_CONFIG,
) -> dict[str, Any]:
    desired = validate_mdraid_check_desired(dict(desired_state))
    with _array_transaction():
        return _build_plan(desired, config_path=config_path)


def apply_mdraid_check(
    desired_state: dict[str, Any],
    plan_id: str,
    *,
    config_path: Path = _MDADM_CONFIG,
) -> dict[str, Any]:
    desired = validate_mdraid_check_desired(dict(desired_state))
    with _array_transaction():
        plan = _build_plan(desired, config_path=config_path)
        if plan["planId"] != plan_id:
            raise ValueError("md RAID1 consistency plan is stale; preview the change again")
        command_error: Exception | None = None
        try:
            _run_mutating("mdadm", "--action=check", plan["array"]["devicefile"])
        except Exception as exc:
            command_error = exc
        try:
            current = _maintenance_snapshot(
                desired["name"],
                desired["arrayUuid"],
                config_path=config_path,
            )
        except Exception as state_exc:
            if command_error is not None:
                raise OSError(
                    "md RAID1 check command failed and the resulting array state is unknown"
                ) from state_exc
            raise
        transitioned = current["action"] == "check" or current["stateHash"] != plan["baseRevision"]
        if not transitioned:
            if command_error is not None:
                raise command_error
            raise OSError("md RAID1 did not report the planned consistency check")
        if current["action"] not in {"check", "idle"}:
            raise OSError("md RAID1 entered an unexpected maintenance action")
        return {
            **plan,
            "applied": True,
            "verified": True,
            "maintenanceState": "checking" if current["action"] == "check" else "completed",
            "current": current,
        }


__all__ = ["apply_mdraid_check", "mdraid_maintenance", "plan_mdraid_check"]
