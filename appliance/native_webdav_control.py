"""Explicit, approval-bound enablement for the native WebDAV gateway.

WebDAV is a second network publication path for every registered share that a
family identity can read.  It is therefore disabled by default and cannot be
made reachable merely by installing its packages.  This module owns the small
root-managed policy file and the deterministic systemd transition used by the
native storage broker.
"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any

from appliance.omv_protocol import (
    WEBDAV_CONTROL_CAPABILITY,
    WEBDAV_PLAN_SCHEMA,
    validate_webdav_desired,
)

WEBDAV_CAPABILITY = WEBDAV_CONTROL_CAPABILITY
POLICY_SCHEMA = "echo.webdav-control.v1"
POLICY_PATH = Path("/var/lib/echo-os/native-webdav.json")
CORE_SERVICES = (
    "echo-webdav-jails.service",
    "echo-webdav-sshd.service",
    "echo-webdav.service",
)
REFRESH_UNITS = ("echo-webdav-refresh.timer", "echo-webdav-refresh.path")
SERVICE_PATHS = {
    service: (
        Path("/usr/lib/systemd/system") / service,
        Path("/etc/systemd/system") / service,
    )
    for service in (*CORE_SERVICES, *REFRESH_UNITS, "echo-webdav-refresh.service")
}


def _storage() -> Any:
    from appliance import native_storage

    return native_storage


def _read_policy(*, strict: bool = False) -> bool:
    try:
        info = POLICY_PATH.lstat()
    except FileNotFoundError:
        return False
    try:
        if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise OSError("managed WebDAV policy is not a regular file")
        if os.name == "posix":
            if stat.S_IMODE(info.st_mode) & 0o022:
                raise OSError("managed WebDAV policy is writable by group or others")
            if info.st_uid not in {0, os.getuid()}:
                raise OSError("managed WebDAV policy has an unexpected owner")
        raw = POLICY_PATH.read_bytes()
        if len(raw) > 4096:
            raise OSError("managed WebDAV policy is too large")
        payload = json.loads(raw)
        if (
            not isinstance(payload, dict)
            or set(payload) != {"schema", "enabled"}
            or payload.get("schema") != POLICY_SCHEMA
            or not isinstance(payload.get("enabled"), bool)
        ):
            raise OSError("managed WebDAV policy has an invalid shape")
        return payload["enabled"]
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        if strict:
            if isinstance(exc, OSError):
                raise
            raise OSError("managed WebDAV policy is unreadable") from exc
        return False


def publication_enabled(*, strict: bool = True) -> bool:
    """Return the root-managed publication decision for the jail reconciler."""

    return _read_policy(strict=strict)


def _policy_bytes(enabled: bool) -> bytes:
    return (
        json.dumps(
            {"schema": POLICY_SCHEMA, "enabled": enabled},
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _write_policy(enabled: bool) -> None:
    POLICY_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    temporary = POLICY_PATH.with_name(f".{POLICY_PATH.name}.{secrets.token_hex(8)}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(_policy_bytes(enabled))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, POLICY_PATH)
        POLICY_PATH.chmod(0o644)
        with contextlib.suppress(OSError):
            directory = os.open(POLICY_PATH.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def _run(*args: str, timeout: float = 90.0, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            list(args), capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise OSError(f"WebDAV control command failed to start: {' '.join(args)}") from exc
    if check and completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip() or f"exit {completed.returncode}"
        raise OSError(f"{args[0]} failed: {detail}")
    return completed


def _unit_state(unit: str, verb: str) -> bool:
    return _run("systemctl", verb, "--quiet", unit, check=False).returncode == 0


def _service_state() -> dict[str, Any]:
    units = {
        unit: {
            "enabled": _unit_state(unit, "is-enabled"),
            "active": _unit_state(unit, "is-active"),
        }
        for unit in (*CORE_SERVICES, *REFRESH_UNITS)
    }
    return {
        "enabled": all(item["enabled"] for item in units.values()),
        "active": all(units[unit]["active"] for unit in CORE_SERVICES),
        "units": units,
    }


def _registered_shares() -> list[dict[str, Any]]:
    storage = _storage()
    shares: list[dict[str, Any]] = []
    with storage._registry_transaction():  # noqa: SLF001 - same native trust domain
        for entry in storage._registry_load(strict=True):  # noqa: SLF001
            reference = storage._registered_uuid(entry, strict=True)  # noqa: SLF001
            name = storage._registered_relative_name(entry, strict=True)  # noqa: SLF001
            assert reference is not None and name is not None
            shares.append(
                {
                    "sharedFolderRef": reference,
                    "name": name,
                    "status": storage._native_registered_folder_status(entry),  # noqa: SLF001
                }
            )
    return sorted(shares, key=lambda item: (item["name"].casefold(), item["sharedFolderRef"]))


def capability_available() -> bool:
    if any(shutil.which(name) is None for name in ("rclone", "sshd", "systemctl", "mount")):
        return False
    if any(not any(path.is_file() and not path.is_symlink() for path in paths) for paths in SERVICE_PATHS.values()):
        return False
    try:
        _read_policy(strict=True)
    except OSError:
        return False
    return True


def _require_ready() -> None:
    if not capability_available():
        raise OSError("native WebDAV requires rclone, OpenSSH and the hardened Echo services")


def status() -> dict[str, Any]:
    try:
        enabled = _read_policy(strict=True)
        policy_valid = True
    except OSError:
        enabled = False
        policy_valid = False
    service = (
        _service_state()
        if shutil.which("systemctl")
        else {"enabled": False, "active": False, "units": {}}
    )
    try:
        shares = _registered_shares()
    except (OSError, ValueError):
        shares = []
        policy_valid = False
    return {
        "schema": "echo.storage.webdav-status.v1",
        "available": policy_valid and capability_available(),
        "enabled": enabled and service["enabled"],
        "active": enabled and service["active"],
        "endpoint": "/webdav/",
        "certificateEndpoint": "/api/appliance/tls/certificate",
        "publishedShares": shares if enabled else [],
        "registeredShareCount": len(shares),
        "source": "native",
        "tlsRequired": True,
    }


def plan_webdav(desired_state: dict[str, Any]) -> dict[str, Any]:
    desired = validate_webdav_desired(desired_state)
    _require_ready()
    shares = _registered_shares()
    if desired["enabled"] and not any(share["status"] == "MOUNTED" for share in shares):
        raise ValueError("WebDAV requires at least one mounted registered NAS share")
    current = _read_policy(strict=True)
    service = _service_state()
    storage = _storage()
    base_revision = storage._canonical_hash(
        {"policy": current, "service": service, "shares": shares}
    )
    unit_states = service["units"].values()
    any_enabled = any(item["enabled"] for item in unit_states)
    any_active = any(item["active"] for item in service["units"].values())
    converged = (
        current and service["enabled"] and service["active"]
    ) or (not current and not any_enabled and not any_active)
    operation = "none" if current == desired["enabled"] and converged else (
        "enable" if desired["enabled"] else "disable"
    )
    plan_id = storage._canonical_hash(
        {
            "schema": WEBDAV_PLAN_SCHEMA,
            "baseRevision": base_revision,
            "desired": desired,
            "operation": operation,
        }
    )
    return {
        "schema": WEBDAV_PLAN_SCHEMA,
        "planId": plan_id,
        "baseRevision": base_revision,
        "operation": operation,
        "requiresApproval": operation != "none",
        "desired": desired,
        "changes": []
        if operation == "none"
        else [{"field": "enabled", "before": current, "after": desired["enabled"]}],
        "publishedShareCount": len([share for share in shares if share["status"] == "MOUNTED"]),
    }


def _set_runtime(enabled: bool) -> None:
    if enabled:
        _run("systemctl", "enable", "--now", CORE_SERVICES[0])
        _run("systemctl", "start", "echo-webdav-refresh.service")
        for unit in (*CORE_SERVICES[1:], *REFRESH_UNITS):
            _run("systemctl", "enable", "--now", unit)
        state = _service_state()
        if not state["enabled"] or not state["active"]:
            raise OSError("WebDAV services did not reach the enabled and active state")
        return

    _run("systemctl", "start", "echo-webdav-refresh.service")
    _run("systemctl", "disable", "--now", *reversed((*CORE_SERVICES, *REFRESH_UNITS)))
    state = _service_state()
    if state["enabled"] or any(state["units"][unit]["active"] for unit in CORE_SERVICES):
        raise OSError("WebDAV services did not reach the disabled state")


def apply_webdav(desired_state: dict[str, Any], plan_id: str) -> dict[str, Any]:
    desired = validate_webdav_desired(desired_state)
    current_plan = plan_webdav(desired)
    if not isinstance(plan_id, str) or plan_id != current_plan["planId"]:
        raise ValueError("WebDAV plan is stale or does not match the desired state")
    if current_plan["operation"] == "none":
        return {**current_plan, "applied": False, "status": status()}

    previous_exists = POLICY_PATH.exists()
    previous_bytes = POLICY_PATH.read_bytes() if previous_exists else None
    previous_enabled = _read_policy(strict=True)
    try:
        _write_policy(desired["enabled"])
        _set_runtime(desired["enabled"])
        if _read_policy(strict=True) != desired["enabled"]:
            raise OSError("WebDAV policy write-back verification failed")
    except Exception as exc:
        try:
            if previous_exists:
                assert previous_bytes == _policy_bytes(previous_enabled)
                _write_policy(previous_enabled)
            else:
                with contextlib.suppress(FileNotFoundError):
                    POLICY_PATH.unlink()
            _set_runtime(previous_enabled)
        except Exception as rollback_exc:
            raise OSError("WebDAV apply and rollback both failed") from rollback_exc
        if isinstance(exc, (OSError, ValueError)):
            raise
        raise OSError("WebDAV apply failed") from exc
    return {**current_plan, "applied": True, "status": status()}


__all__ = [
    "POLICY_PATH",
    "WEBDAV_CAPABILITY",
    "apply_webdav",
    "capability_available",
    "plan_webdav",
    "publication_enabled",
    "status",
]
