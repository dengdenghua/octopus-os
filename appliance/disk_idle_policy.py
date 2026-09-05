"""Approval-bound standby timer for stable internal ATA/SATA hard disks."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
DESIRED_SCHEMA = "echo.disk-idle-policy-desired.v1"
POLICY_PATH = Path("/etc/echo-os/disk-idle-policy.json")
SERVICE_PATH = Path("/etc/systemd/system/echo-disk-idle.service")
LOCK_PATH = Path("/run/lock/echo-os-disk-idle.lock")
LSBLK = Path("/usr/bin/lsblk")
HDPARM = Path("/usr/sbin/hdparm")
MAX_FILE_BYTES = 4096
MAX_OUTPUT_BYTES = 1024 * 1024
MAX_DISKS = 32
IDLE_CODES = {0: 0, 30: 241, 60: 242, 120: 244, 240: 248}
_DEVICE = re.compile(r"^/dev/[A-Za-z0-9_.!+-]+$")
_TRANSPORTS = frozenset({"ata", "sata"})
_THREAD_LOCK = threading.RLock()


class DiskIdlePolicyError(ValueError):
    """The requested disk standby policy is invalid or stale."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _safe_read(path: Path, *, trusted_uid: int) -> tuple[bool, bytes | None]:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False, None
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise OSError("disk idle policy is not a regular file")
    if metadata.st_uid != trusted_uid or (os.name == "posix" and metadata.st_mode & 0o022):
        raise OSError("disk idle policy has unsafe ownership or mode")
    if metadata.st_size > MAX_FILE_BYTES:
        raise OSError("disk idle policy exceeds the safety limit")
    payload = path.read_bytes()
    if len(payload) > MAX_FILE_BYTES:
        raise OSError("disk idle policy exceeds the safety limit")
    return True, payload


def _policy(value: Mapping[str, Any] | None) -> dict[str, int]:
    if value is None:
        return {"schemaVersion": SCHEMA_VERSION, "idleMinutes": 0}
    if set(value) != {"schemaVersion", "idleMinutes"}:
        raise DiskIdlePolicyError("disk idle policy has an unexpected schema")
    idle_minutes = value["idleMinutes"]
    if (
        value["schemaVersion"] != SCHEMA_VERSION
        or isinstance(idle_minutes, bool)
        or idle_minutes not in IDLE_CODES
    ):
        raise DiskIdlePolicyError("disk idle policy has invalid values")
    return {"schemaVersion": SCHEMA_VERSION, "idleMinutes": idle_minutes}


def read_policy(path: Path = POLICY_PATH, *, trusted_uid: int = 0) -> tuple[bool, dict[str, int]]:
    exists, payload = _safe_read(path, trusted_uid=trusted_uid)
    if payload is None:
        return False, _policy(None)
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise OSError("disk idle policy is invalid JSON") from exc
    if not isinstance(value, dict):
        raise OSError("disk idle policy is not a JSON object")
    try:
        return exists, _policy(value)
    except DiskIdlePolicyError as exc:
        raise OSError(str(exc)) from exc


def _trusted_file(path: Path, *, trusted_uid: int, maximum: int = MAX_FILE_BYTES) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    return (
        stat.S_ISREG(metadata.st_mode)
        and not stat.S_ISLNK(metadata.st_mode)
        and 0 < metadata.st_size <= maximum
        and metadata.st_uid == trusted_uid
        and (os.name != "posix" or metadata.st_mode & 0o022 == 0)
    )


def service_installed(path: Path = SERVICE_PATH, *, trusted_uid: int = 0) -> bool:
    return _trusted_file(path, trusted_uid=trusted_uid, maximum=16 * 1024)


def _lsblk_flag(value: Any) -> bool:
    return value is True or (type(value) is int and value == 1) or value == "1"


def _run_inventory(
    *,
    lsblk: Path,
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> list[dict[str, Any]]:
    if not lsblk.is_file() or lsblk.is_symlink():
        raise OSError("trusted lsblk is unavailable")
    try:
        completed = runner(
            [
                str(lsblk),
                "-J",
                "-b",
                "-d",
                "-o",
                "PATH,TYPE,SIZE,ROTA,RM,TRAN,MODEL,SERIAL,WWN",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=20.0,
            check=False,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        raise OSError("disk idle inventory failed") from exc
    output = completed.stdout or ""
    if completed.returncode != 0 or len(output.encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise OSError("disk idle inventory failed")
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as exc:
        raise OSError("disk idle inventory was not JSON") from exc
    records = payload.get("blockdevices") if isinstance(payload, dict) else None
    if not isinstance(records, list) or len(records) > MAX_DISKS:
        raise OSError("disk idle inventory was malformed or exceeded the safety limit")

    devices: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    seen_identities: set[str] = set()
    for item in records:
        if not isinstance(item, dict):
            continue
        devicefile = item.get("path")
        transport = str(item.get("tran") or "").strip().casefold()
        serial = str(item.get("serial") or "").strip()
        wwn = str(item.get("wwn") or "").strip()
        size = item.get("size")
        rotational = _lsblk_flag(item.get("rota"))
        removable = _lsblk_flag(item.get("rm"))
        if (
            item.get("type") != "disk"
            or not isinstance(devicefile, str)
            or _DEVICE.fullmatch(devicefile) is None
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size <= 0
            or not rotational
            or removable
            or transport not in _TRANSPORTS
            or not (serial or wwn)
        ):
            continue
        identity = {
            "model": str(item.get("model") or "").strip(),
            "serial": serial,
            "sizeBytes": size,
            "transport": transport,
            "wwn": wwn,
        }
        identity_hash = hashlib.sha256(_canonical(identity)).hexdigest()
        if devicefile in seen_paths or identity_hash in seen_identities:
            raise OSError("disk idle inventory contains duplicate device identities")
        seen_paths.add(devicefile)
        seen_identities.add(identity_hash)
        devices.append(
            {
                "devicefile": devicefile,
                "model": identity["model"] or None,
                "sizeBytes": size,
                "transport": transport,
                "identityHash": identity_hash,
            }
        )
    return sorted(devices, key=lambda item: item["devicefile"])


def eligible_devices(
    *,
    lsblk: Path = LSBLK,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> list[dict[str, Any]]:
    return _run_inventory(lsblk=lsblk, runner=runner)


def policy_status(
    path: Path = POLICY_PATH,
    *,
    service_path: Path = SERVICE_PATH,
    lsblk: Path = LSBLK,
    trusted_uid: int = 0,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    configured, current = read_policy(path, trusted_uid=trusted_uid)
    devices = eligible_devices(lsblk=lsblk, runner=runner)
    return {
        **current,
        "enabled": current["idleMinutes"] > 0,
        "configured": configured,
        "serviceInstalled": service_installed(service_path, trusted_uid=trusted_uid),
        "eligibleDevices": devices,
        "eligibleDeviceCount": len(devices),
        "allowedIdleMinutes": list(IDLE_CODES),
        "scope": "stableInternalRotationalAtaSataWholeDisksOnly",
        "hardwareVerification": "commandAcceptanceOnly",
        "source": "localPolicy",
    }


def _desired(value: Mapping[str, Any]) -> dict[str, int]:
    if set(value) != {"schema", "idleMinutes"}:
        raise DiskIdlePolicyError("disk idle desired state has an unexpected schema")
    idle_minutes = value["idleMinutes"]
    if (
        value["schema"] != DESIRED_SCHEMA
        or isinstance(idle_minutes, bool)
        or not isinstance(idle_minutes, int)
        or idle_minutes not in IDLE_CODES
    ):
        raise DiskIdlePolicyError("disk idle desired state is invalid")
    return {"schemaVersion": SCHEMA_VERSION, "idleMinutes": idle_minutes}


def plan_policy(
    desired_state: Mapping[str, Any],
    *,
    path: Path = POLICY_PATH,
    service_path: Path = SERVICE_PATH,
    lsblk: Path = LSBLK,
    hdparm: Path = HDPARM,
    trusted_uid: int = 0,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    desired = _desired(desired_state)
    configured, current = read_policy(path, trusted_uid=trusted_uid)
    operation = "none" if desired == current else "disable" if desired["idleMinutes"] == 0 else "set"
    installed = service_installed(service_path, trusted_uid=trusted_uid)
    devices: list[dict[str, Any]] = []
    if operation != "none":
        if not hdparm.is_file() or hdparm.is_symlink():
            raise OSError("trusted hdparm is unavailable")
        devices = eligible_devices(lsblk=lsblk, runner=runner)
        if desired["idleMinutes"] > 0 and not installed:
            raise OSError("disk idle boot service is not installed")
        if desired["idleMinutes"] > 0 and not devices:
            raise DiskIdlePolicyError("no eligible internal ATA/SATA hard disk is available")
    binding = {
        "schema": DESIRED_SCHEMA,
        "operation": operation,
        "current": current,
        "desired": desired,
        "configured": configured,
        "serviceInstalled": installed,
        "devices": devices,
        "scope": "stableInternalRotationalAtaSataWholeDisksOnly",
        "hardwareVerification": "commandAcceptanceOnly",
    }
    return {
        **binding,
        "planId": hashlib.sha256(_canonical(binding)).hexdigest(),
        "requiresApproval": operation != "none",
        "safety": {
            "minimumIdleMinutes": 30,
            "nvme": "skipped",
            "usbAndRemovable": "skipped",
            "unknownIdentity": "skipped",
            "firmwareMayIgnoreTimer": True,
            "activeIoPreventsStandby": True,
        },
    }


def _assert_parent(path: Path, *, trusted_uid: int) -> None:
    metadata = path.parent.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise OSError("disk idle policy directory is unsafe")
    if metadata.st_uid != trusted_uid or (os.name == "posix" and metadata.st_mode & 0o022):
        raise OSError("disk idle policy directory has unsafe ownership or mode")


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


@contextmanager
def _transaction(lock_path: Path = LOCK_PATH, *, trusted_uid: int = 0) -> Iterator[None]:
    with _THREAD_LOCK:
        if os.name != "posix":
            yield
            return
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(lock_path, flags, 0o600)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != trusted_uid:
                raise OSError("disk idle transaction lock is unsafe")
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            os.close(descriptor)


def _set_timeout(
    devicefile: str,
    idle_minutes: int,
    *,
    hdparm: Path,
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    try:
        completed = runner(
            [str(hdparm), "-S", str(IDLE_CODES[idle_minutes]), devicefile],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20.0,
            check=False,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise OSError("disk rejected standby timer update") from exc
    output_size = len((completed.stdout or "").encode()) + len((completed.stderr or "").encode())
    if completed.returncode != 0 or output_size > 64 * 1024:
        raise OSError("disk rejected standby timer update")


def apply_policy(
    desired_state: Mapping[str, Any],
    plan_id: str,
    *,
    path: Path = POLICY_PATH,
    service_path: Path = SERVICE_PATH,
    lock_path: Path = LOCK_PATH,
    lsblk: Path = LSBLK,
    hdparm: Path = HDPARM,
    trusted_uid: int = 0,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    if os.name == "posix" and os.geteuid() != trusted_uid:
        raise OSError("disk idle policy update requires root")
    with _transaction(lock_path, trusted_uid=trusted_uid):
        plan = plan_policy(
            desired_state,
            path=path,
            service_path=service_path,
            lsblk=lsblk,
            hdparm=hdparm,
            trusted_uid=trusted_uid,
            runner=runner,
        )
        if plan["planId"] != plan_id:
            raise DiskIdlePolicyError("disk idle plan is stale; preview again")
        if plan["operation"] == "none":
            return {**plan, "applied": False, "verified": True, "hardwareUpdated": 0}
        _assert_parent(path, trusted_uid=trusted_uid)
        existed, previous = _safe_read(path, trusted_uid=trusted_uid)
        attempted: list[str] = []
        old_minutes = plan["current"]["idleMinutes"]
        try:
            for device in plan["devices"]:
                attempted.append(device["devicefile"])
                _set_timeout(
                    device["devicefile"],
                    plan["desired"]["idleMinutes"],
                    hdparm=hdparm,
                    runner=runner,
                )
            _atomic_write(path, _canonical(plan["desired"]) + b"\n")
            _, verified = read_policy(path, trusted_uid=trusted_uid)
            if verified != plan["desired"]:
                raise OSError("disk idle policy verification failed")
        except OSError as exc:
            rollback_errors = 0
            for devicefile in reversed(attempted):
                try:
                    _set_timeout(devicefile, old_minutes, hdparm=hdparm, runner=runner)
                except OSError:
                    rollback_errors += 1
            try:
                if existed and previous is not None:
                    _atomic_write(path, previous)
                elif path.exists() and not path.is_symlink():
                    path.unlink()
            except OSError:
                rollback_errors += 1
            if rollback_errors:
                raise OSError("disk idle update failed and rollback was incomplete") from exc
            raise OSError("disk idle update failed and was rolled back") from exc
        return {
            **plan,
            "applied": True,
            "verified": True,
            "hardwareUpdated": len(attempted),
        }


def apply_configured_policy(
    *,
    path: Path = POLICY_PATH,
    lock_path: Path = LOCK_PATH,
    lsblk: Path = LSBLK,
    hdparm: Path = HDPARM,
    trusted_uid: int = 0,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    if os.name == "posix" and os.geteuid() != trusted_uid:
        raise OSError("disk idle boot apply requires root")
    with _transaction(lock_path, trusted_uid=trusted_uid):
        configured, current = read_policy(path, trusted_uid=trusted_uid)
        if not configured or current["idleMinutes"] == 0:
            return {"outcome": "disabled", "updated": 0, "errors": 0}
        if not hdparm.is_file() or hdparm.is_symlink():
            raise OSError("trusted hdparm is unavailable")
        devices = eligible_devices(lsblk=lsblk, runner=runner)
        updated = 0
        errors = 0
        for device in devices:
            try:
                _set_timeout(
                    device["devicefile"],
                    current["idleMinutes"],
                    hdparm=hdparm,
                    runner=runner,
                )
                updated += 1
            except OSError:
                errors += 1
        return {
            "outcome": "completed" if errors == 0 else "completedWithErrors",
            "updated": updated,
            "errors": errors,
        }


__all__ = [
    "DESIRED_SCHEMA",
    "DiskIdlePolicyError",
    "HDPARM",
    "IDLE_CODES",
    "LOCK_PATH",
    "LSBLK",
    "POLICY_PATH",
    "SERVICE_PATH",
    "apply_configured_policy",
    "apply_policy",
    "eligible_devices",
    "plan_policy",
    "policy_status",
    "read_policy",
    "service_installed",
]
