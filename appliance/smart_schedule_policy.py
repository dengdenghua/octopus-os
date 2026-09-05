"""Approval-bound policy for weekly whole-disk SMART short self-tests."""

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
DESIRED_SCHEMA = "echo.smart-self-test-schedule-desired.v1"
POLICY_PATH = Path("/etc/echo-os/smart-self-test-schedule.json")
SCHEDULE = "Sunday 03:30 local time, with up to 30 minutes randomized delay"
MAX_FILE_BYTES = 4096
_POLICY_LOCK = threading.RLock()


class SmartSchedulePolicyError(ValueError):
    """A SMART schedule policy or desired state is invalid."""


def validate_policy(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {"schemaVersion": SCHEMA_VERSION, "enabled": False}
    if set(value) != {"schemaVersion", "enabled"}:
        raise SmartSchedulePolicyError("SMART schedule policy has an unexpected schema")
    if value["schemaVersion"] != SCHEMA_VERSION or not isinstance(value["enabled"], bool):
        raise SmartSchedulePolicyError("SMART schedule policy has invalid values")
    return {"schemaVersion": SCHEMA_VERSION, "enabled": value["enabled"]}


def _safe_read(path: Path, *, trusted_uid: int) -> tuple[bool, bytes | None]:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False, None
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise OSError("SMART schedule policy is not a regular file")
    if metadata.st_uid != trusted_uid or (os.name == "posix" and metadata.st_mode & 0o022):
        raise OSError("SMART schedule policy has unsafe ownership or mode")
    if metadata.st_size > MAX_FILE_BYTES:
        raise OSError("SMART schedule policy exceeds the safety limit")
    payload = path.read_bytes()
    if len(payload) > MAX_FILE_BYTES:
        raise OSError("SMART schedule policy exceeds the safety limit")
    return True, payload


def read_policy(path: Path = POLICY_PATH, *, trusted_uid: int = 0) -> tuple[bool, dict[str, Any]]:
    exists, payload = _safe_read(path, trusted_uid=trusted_uid)
    if payload is None:
        return False, validate_policy(None)
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise OSError("SMART schedule policy is invalid JSON") from exc
    if not isinstance(value, dict):
        raise OSError("SMART schedule policy is not a JSON object")
    try:
        return exists, validate_policy(value)
    except SmartSchedulePolicyError as exc:
        raise OSError(str(exc)) from exc


def policy_status(path: Path = POLICY_PATH, *, trusted_uid: int = 0) -> dict[str, Any]:
    configured, policy = read_policy(path, trusted_uid=trusted_uid)
    return {
        **policy,
        "configured": configured,
        "source": "localPolicy",
        "test": "short",
        "schedule": SCHEDULE,
    }


def _desired(value: Mapping[str, Any]) -> dict[str, Any]:
    if set(value) != {"schema", "enabled"}:
        raise SmartSchedulePolicyError("SMART schedule desired state has an unexpected schema")
    if value["schema"] != DESIRED_SCHEMA or not isinstance(value["enabled"], bool):
        raise SmartSchedulePolicyError("SMART schedule desired state is invalid")
    return {"schemaVersion": SCHEMA_VERSION, "enabled": value["enabled"]}


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def plan_policy(
    desired_state: Mapping[str, Any],
    *,
    path: Path = POLICY_PATH,
    trusted_uid: int = 0,
) -> dict[str, Any]:
    desired = _desired(desired_state)
    configured, current = read_policy(path, trusted_uid=trusted_uid)
    operation = "none" if desired == current else "enable" if desired["enabled"] else "disable"
    binding = {
        "schema": DESIRED_SCHEMA,
        "current": current,
        "desired": desired,
        "configured": configured,
        "operation": operation,
        "test": "short",
        "schedule": SCHEDULE,
    }
    return {
        **binding,
        "planId": hashlib.sha256(_canonical(binding)).hexdigest(),
        "requiresApproval": operation != "none",
    }


def _assert_parent(path: Path, *, trusted_uid: int) -> None:
    metadata = path.parent.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise OSError("SMART schedule policy directory is unsafe")
    if metadata.st_uid != trusted_uid or (os.name == "posix" and metadata.st_mode & 0o022):
        raise OSError("SMART schedule policy directory has unsafe ownership or mode")


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
    trusted_uid: int = 0,
) -> dict[str, Any]:
    if os.name == "posix" and os.geteuid() != trusted_uid:
        raise OSError("SMART schedule policy update requires root")
    with _POLICY_LOCK:
        plan = plan_policy(desired_state, path=path, trusted_uid=trusted_uid)
        if plan["planId"] != plan_id:
            raise SmartSchedulePolicyError("SMART schedule plan is stale; preview again")
        if plan["operation"] == "none":
            return {**plan, "applied": False, "verified": True}
        _assert_parent(path, trusted_uid=trusted_uid)
        existed, previous = _safe_read(path, trusted_uid=trusted_uid)
        payload = _canonical(plan["desired"]) + b"\n"
        try:
            _atomic_write(path, payload)
            _, verified = read_policy(path, trusted_uid=trusted_uid)
            if verified != plan["desired"]:
                raise OSError("SMART schedule policy verification failed")
        except OSError as exc:
            try:
                if existed and previous is not None:
                    _atomic_write(path, previous)
                elif path.exists() and not path.is_symlink():
                    path.unlink()
            except OSError as rollback_exc:
                raise OSError(
                    "SMART schedule update failed and rollback was incomplete"
                ) from rollback_exc
            raise OSError("SMART schedule update failed and was rolled back") from exc
        return {**plan, "applied": True, "verified": True}


__all__ = [
    "DESIRED_SCHEMA",
    "POLICY_PATH",
    "SCHEDULE",
    "SmartSchedulePolicyError",
    "apply_policy",
    "plan_policy",
    "policy_status",
    "read_policy",
]
