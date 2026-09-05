"""Approval-bound policy storage for the local UPS shutdown guard."""

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
DESIRED_SCHEMA = "echo.ups-shutdown-policy-desired.v1"
DEFAULT_REQUIRED_SAMPLES = 3
MAX_FILE_BYTES = 4096
POLICY_PATH = Path("/etc/echo-os/ups-shutdown.json")
_POLICY_LOCK = threading.RLock()


class UpsShutdownPolicyError(ValueError):
    """A policy or its root-owned persistence boundary is unsafe."""


def validate_policy(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "enabled": False,
            "requiredConsecutiveSamples": DEFAULT_REQUIRED_SAMPLES,
        }
    if set(value) != {"schemaVersion", "enabled", "requiredConsecutiveSamples"}:
        raise UpsShutdownPolicyError("UPS shutdown policy has an unexpected schema")
    enabled = value["enabled"]
    samples = value["requiredConsecutiveSamples"]
    if value["schemaVersion"] != SCHEMA_VERSION or not isinstance(enabled, bool):
        raise UpsShutdownPolicyError("UPS shutdown policy has invalid values")
    if isinstance(samples, bool) or not isinstance(samples, int) or not 2 <= samples <= 12:
        raise UpsShutdownPolicyError("UPS shutdown sample count must be between 2 and 12")
    return {
        "schemaVersion": SCHEMA_VERSION,
        "enabled": enabled,
        "requiredConsecutiveSamples": samples,
    }


def _safe_read(path: Path, *, trusted_uid: int) -> tuple[bool, bytes | None]:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False, None
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise OSError("UPS shutdown policy is not a regular file")
    if metadata.st_uid != trusted_uid or (os.name == "posix" and metadata.st_mode & 0o022):
        raise OSError("UPS shutdown policy has unsafe ownership or mode")
    if metadata.st_size > MAX_FILE_BYTES:
        raise OSError("UPS shutdown policy exceeds the safety limit")
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise OSError("UPS shutdown policy could not be read") from exc
    if len(payload) > MAX_FILE_BYTES:
        raise OSError("UPS shutdown policy exceeds the safety limit")
    return True, payload


def read_policy(
    path: Path = POLICY_PATH,
    *,
    trusted_uid: int = 0,
) -> tuple[bool, dict[str, Any]]:
    exists, payload = _safe_read(path, trusted_uid=trusted_uid)
    if payload is None:
        return False, validate_policy(None)
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise OSError("UPS shutdown policy is invalid JSON") from exc
    if not isinstance(value, dict):
        raise OSError("UPS shutdown policy is not a JSON object")
    try:
        return exists, validate_policy(value)
    except UpsShutdownPolicyError as exc:
        raise OSError(str(exc)) from exc


def policy_status(path: Path = POLICY_PATH, *, trusted_uid: int = 0) -> dict[str, Any]:
    configured, policy = read_policy(path, trusted_uid=trusted_uid)
    return {
        **policy,
        "configured": configured,
        "source": "localPolicy",
        "shutdownTrigger": "FSD or persistent OB+LB",
    }


def _desired(value: Mapping[str, Any]) -> dict[str, Any]:
    if set(value) != {"schema", "enabled", "requiredConsecutiveSamples"}:
        raise UpsShutdownPolicyError("UPS shutdown policy desired state has an unexpected schema")
    if value["schema"] != DESIRED_SCHEMA:
        raise UpsShutdownPolicyError("UPS shutdown policy desired schema is unsupported")
    return validate_policy(
        {
            "schemaVersion": SCHEMA_VERSION,
            "enabled": value["enabled"],
            "requiredConsecutiveSamples": value["requiredConsecutiveSamples"],
        }
    )


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
    if desired == current:
        operation = "none"
    elif desired["enabled"] and not current["enabled"]:
        operation = "enable"
    elif not desired["enabled"] and current["enabled"]:
        operation = "disable"
    else:
        operation = "update"
    binding = {
        "schema": DESIRED_SCHEMA,
        "current": current,
        "desired": desired,
        "configured": configured,
        "operation": operation,
    }
    return {
        **binding,
        "planId": hashlib.sha256(_canonical(binding)).hexdigest(),
        "requiresApproval": operation != "none",
        "shutdownTrigger": "FSD or persistent OB+LB",
    }


def _assert_parent(path: Path, *, trusted_uid: int) -> None:
    try:
        metadata = path.parent.lstat()
    except OSError as exc:
        raise OSError("UPS shutdown policy directory is unavailable") from exc
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise OSError("UPS shutdown policy directory is unsafe")
    if metadata.st_uid != trusted_uid or (os.name == "posix" and metadata.st_mode & 0o022):
        raise OSError("UPS shutdown policy directory has unsafe ownership or mode")


def _atomic_write(path: Path, payload: bytes, *, mode: int = 0o644) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
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
        raise OSError("UPS shutdown policy update requires root")
    with _POLICY_LOCK:
        plan = plan_policy(desired_state, path=path, trusted_uid=trusted_uid)
        if plan["planId"] != plan_id:
            raise UpsShutdownPolicyError("UPS shutdown policy plan is stale; preview again")
        if plan["operation"] == "none":
            return {**plan, "applied": False, "verified": True}
        _assert_parent(path, trusted_uid=trusted_uid)
        existed, previous = _safe_read(path, trusted_uid=trusted_uid)
        desired = plan["desired"]
        payload = _canonical(desired) + b"\n"
        try:
            _atomic_write(path, payload)
            _, verified = read_policy(path, trusted_uid=trusted_uid)
            if verified != desired:
                raise OSError("UPS shutdown policy verification failed")
        except OSError as exc:
            try:
                if existed and previous is not None:
                    _atomic_write(path, previous)
                elif path.exists() and not path.is_symlink():
                    path.unlink()
            except OSError as rollback_exc:
                raise OSError(
                    "UPS shutdown policy update failed and rollback was incomplete"
                ) from rollback_exc
            raise OSError("UPS shutdown policy update failed and was rolled back") from exc
        return {**plan, "applied": True, "verified": True}


__all__ = [
    "DEFAULT_REQUIRED_SAMPLES",
    "DESIRED_SCHEMA",
    "POLICY_PATH",
    "UpsShutdownPolicyError",
    "apply_policy",
    "plan_policy",
    "policy_status",
    "read_policy",
    "validate_policy",
]
