"""Approval-bound policy for monthly Echo-managed md RAID1 consistency checks."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
DESIRED_SCHEMA = "echo.mdraid-check-schedule-desired.v1"
POLICY_PATH = Path("/etc/echo-os/mdraid-check-schedule.json")
TIMER_PATH = Path("/etc/systemd/system/echo-mdraid-check.timer")
SCHEDULE = "first Sunday of each month after 00:45 local time, randomized within 24 hours"
MAX_FILE_BYTES = 4096
MAX_TIMER_BYTES = 16 * 1024
_POLICY_LOCK = threading.RLock()


class MdRaidCheckSchedulePolicyError(ValueError):
    """An md RAID1 check schedule policy or desired state is invalid."""


def validate_policy(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {"schemaVersion": SCHEMA_VERSION, "enabled": False}
    if set(value) != {"schemaVersion", "enabled"}:
        raise MdRaidCheckSchedulePolicyError("md RAID1 check policy has an unexpected schema")
    if value["schemaVersion"] != SCHEMA_VERSION or not isinstance(value["enabled"], bool):
        raise MdRaidCheckSchedulePolicyError("md RAID1 check policy has invalid values")
    return {"schemaVersion": SCHEMA_VERSION, "enabled": value["enabled"]}


def _safe_read(path: Path, *, trusted_uid: int) -> tuple[bool, bytes | None]:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False, None
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise OSError("md RAID1 check policy is not a regular file")
    if metadata.st_uid != trusted_uid or (os.name == "posix" and metadata.st_mode & 0o022):
        raise OSError("md RAID1 check policy has unsafe ownership or mode")
    if metadata.st_size > MAX_FILE_BYTES:
        raise OSError("md RAID1 check policy exceeds the safety limit")
    payload = path.read_bytes()
    if len(payload) > MAX_FILE_BYTES:
        raise OSError("md RAID1 check policy exceeds the safety limit")
    return True, payload


def read_policy(path: Path = POLICY_PATH, *, trusted_uid: int = 0) -> tuple[bool, dict[str, Any]]:
    exists, payload = _safe_read(path, trusted_uid=trusted_uid)
    if payload is None:
        return False, validate_policy(None)
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise OSError("md RAID1 check policy is invalid JSON") from exc
    if not isinstance(value, dict):
        raise OSError("md RAID1 check policy is not a JSON object")
    try:
        return exists, validate_policy(value)
    except MdRaidCheckSchedulePolicyError as exc:
        raise OSError(str(exc)) from exc


def scheduler_installed(timer_path: Path = TIMER_PATH, *, trusted_uid: int = 0) -> bool:
    try:
        metadata = timer_path.lstat()
    except OSError:
        return False
    return (
        stat.S_ISREG(metadata.st_mode)
        and not stat.S_ISLNK(metadata.st_mode)
        and 0 < metadata.st_size <= MAX_TIMER_BYTES
        and metadata.st_uid == trusted_uid
        and (os.name != "posix" or metadata.st_mode & 0o022 == 0)
    )


def policy_status(
    path: Path = POLICY_PATH,
    *,
    timer_path: Path = TIMER_PATH,
    trusted_uid: int = 0,
) -> dict[str, Any]:
    configured, policy = read_policy(path, trusted_uid=trusted_uid)
    return {
        **policy,
        "configured": configured,
        "schedulerInstalled": scheduler_installed(timer_path, trusted_uid=trusted_uid),
        "source": "localPolicy",
        "operation": "check",
        "scope": "echoManagedHealthyRaid1Only",
        "schedule": SCHEDULE,
    }


def _desired(value: Mapping[str, Any]) -> dict[str, Any]:
    if set(value) != {"schema", "enabled"}:
        raise MdRaidCheckSchedulePolicyError(
            "md RAID1 check schedule desired state has an unexpected schema"
        )
    if value["schema"] != DESIRED_SCHEMA or not isinstance(value["enabled"], bool):
        raise MdRaidCheckSchedulePolicyError("md RAID1 check schedule desired state is invalid")
    return {"schemaVersion": SCHEMA_VERSION, "enabled": value["enabled"]}


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def plan_policy(
    desired_state: Mapping[str, Any],
    *,
    path: Path = POLICY_PATH,
    timer_path: Path = TIMER_PATH,
    trusted_uid: int = 0,
) -> dict[str, Any]:
    desired = _desired(desired_state)
    installed = scheduler_installed(timer_path, trusted_uid=trusted_uid)
    if desired["enabled"] and not installed:
        raise OSError("md RAID1 check scheduler is not installed")
    configured, current = read_policy(path, trusted_uid=trusted_uid)
    operation = "none" if desired == current else "enable" if desired["enabled"] else "disable"
    binding = {
        "schema": DESIRED_SCHEMA,
        "current": current,
        "desired": desired,
        "configured": configured,
        "schedulerInstalled": installed,
        "operation": operation,
        "checkAction": "check",
        "scope": "echoManagedHealthyRaid1Only",
        "schedule": SCHEDULE,
    }
    return {
        **binding,
        "planId": hashlib.sha256(_canonical(binding)).hexdigest(),
        "requiresApproval": operation != "none",
        "safety": {
            "explicitRepair": False,
            "kernelReadErrorRecovery": "mayOccur",
            "ioLoad": "high",
            "degradedArrays": "skipped",
        },
    }


def _assert_parent(path: Path, *, trusted_uid: int) -> None:
    metadata = path.parent.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise OSError("md RAID1 check policy directory is unsafe")
    if metadata.st_uid != trusted_uid or (os.name == "posix" and metadata.st_mode & 0o022):
        raise OSError("md RAID1 check policy directory has unsafe ownership or mode")


def _atomic_write(path: Path, payload: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
        if os.name == "posix":
            directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def apply_policy(
    desired_state: Mapping[str, Any],
    plan_id: str,
    *,
    path: Path = POLICY_PATH,
    timer_path: Path = TIMER_PATH,
    trusted_uid: int = 0,
) -> dict[str, Any]:
    if os.name == "posix" and os.geteuid() != trusted_uid:
        raise OSError("md RAID1 check schedule update requires root")
    with _POLICY_LOCK:
        plan = plan_policy(
            desired_state,
            path=path,
            timer_path=timer_path,
            trusted_uid=trusted_uid,
        )
        if plan["planId"] != plan_id:
            raise MdRaidCheckSchedulePolicyError(
                "md RAID1 check schedule plan is stale; preview again"
            )
        if plan["operation"] == "none":
            return {**plan, "applied": False, "verified": True}
        _assert_parent(path, trusted_uid=trusted_uid)
        existed, previous = _safe_read(path, trusted_uid=trusted_uid)
        payload = _canonical(plan["desired"]) + b"\n"
        try:
            _atomic_write(path, payload)
            _, verified = read_policy(path, trusted_uid=trusted_uid)
            if verified != plan["desired"]:
                raise OSError("md RAID1 check schedule verification failed")
        except OSError as exc:
            try:
                if existed and previous is not None:
                    _atomic_write(path, previous)
                elif path.exists() and not path.is_symlink():
                    path.unlink()
            except OSError as rollback_exc:
                raise OSError(
                    "md RAID1 check schedule update failed and rollback was incomplete"
                ) from rollback_exc
            raise OSError("md RAID1 check schedule update failed and was rolled back") from exc
        return {**plan, "applied": True, "verified": True}


__all__ = [
    "DESIRED_SCHEMA",
    "POLICY_PATH",
    "SCHEDULE",
    "TIMER_PATH",
    "MdRaidCheckSchedulePolicyError",
    "apply_policy",
    "plan_policy",
    "policy_status",
    "read_policy",
    "scheduler_installed",
]
