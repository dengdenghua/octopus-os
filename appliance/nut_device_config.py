"""Approval-bound configuration for one directly attached local USB UPS.

Echo deliberately configures only the NUT driver and a loopback-only upsd.
Automatic shutdown remains exclusively owned by ``ups_shutdown_guard`` and
its separately approved policy; this module never writes upsmon credentials or
MONITOR directives.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from appliance.native_ups import ups_status

SCHEMA_VERSION = 1
DESIRED_SCHEMA = "echo.nut-local-ups-desired.v1"
UPS_NAME = "echo-ups"
UPS_CONF = Path("/etc/nut/ups.conf")
UPSD_CONF = Path("/etc/nut/upsd.conf")
SYSTEMCTL = Path("/usr/bin/systemctl")
MAX_FILE_BYTES = 256 * 1024
ALLOWED_DRIVERS = (
    "usbhid-ups",
    "blazer_usb",
    "nutdrv_qx",
    "bcmxcp_usb",
    "richcomm_usb",
    "tripplite_usb",
)
_UPS_BEGIN = "# BEGIN ECHO OS MANAGED LOCAL UPS"
_UPS_END = "# END ECHO OS MANAGED LOCAL UPS"
_UPSD_BEGIN = "# BEGIN ECHO OS MANAGED LOOPBACK UPSD"
_UPSD_END = "# END ECHO OS MANAGED LOOPBACK UPSD"
_SECTION = re.compile(r"^\s*\[([^\]\s]+)\]\s*(?:#.*)?$", re.MULTILINE)
_LISTEN = re.compile(r"^\s*LISTEN\s+(\S+)(?:\s+\d+)?\s*(?:#.*)?$", re.MULTILINE)
_DRIVER = re.compile(r"^\s*driver\s*=\s*([A-Za-z0-9_-]+)\s*(?:#.*)?$", re.MULTILINE)
_LOCK = threading.RLock()


class NutDeviceConfigError(ValueError):
    """The requested NUT configuration is invalid or unsafe to own."""


def _safe_read(path: Path, *, trusted_uid: int) -> tuple[bool, bytes, int, int]:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False, b"", 0o640, -1
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise OSError(f"{path.name} is not a regular file")
    if metadata.st_uid != trusted_uid or (os.name == "posix" and metadata.st_mode & 0o022):
        raise OSError(f"{path.name} has unsafe ownership or mode")
    if metadata.st_size > MAX_FILE_BYTES:
        raise OSError(f"{path.name} exceeds the safety limit")
    payload = path.read_bytes()
    if len(payload) > MAX_FILE_BYTES:
        raise OSError(f"{path.name} exceeds the safety limit")
    return True, payload, stat.S_IMODE(metadata.st_mode), metadata.st_gid


def _text(payload: bytes, *, name: str) -> str:
    try:
        value = payload.decode("utf-8")
    except UnicodeError as exc:
        raise OSError(f"{name} is not UTF-8") from exc
    if "\x00" in value:
        raise OSError(f"{name} contains invalid bytes")
    return value


def _managed_block(text: str, begin: str, end: str) -> str | None:
    begin_count = text.count(begin)
    end_count = text.count(end)
    if begin_count == end_count == 0:
        return None
    if begin_count != 1 or end_count != 1:
        raise OSError("NUT configuration contains malformed Echo ownership markers")
    start = text.index(begin)
    end_start = text.index(end)
    if end_start < start:
        raise OSError("NUT configuration contains reversed Echo ownership markers")
    finish = end_start + len(end)
    return text[start:finish]


def _without_block(text: str, begin: str, end: str) -> str:
    block = _managed_block(text, begin, end)
    if block is None:
        return text
    return (text[: text.index(block)] + text[text.index(block) + len(block) :]).strip() + "\n"


def _external_sections(text: str) -> list[str]:
    unmanaged = _without_block(text, _UPS_BEGIN, _UPS_END)
    return sorted({match.group(1) for match in _SECTION.finditer(unmanaged)})


def _unsafe_listeners(text: str) -> list[str]:
    unmanaged = _without_block(text, _UPSD_BEGIN, _UPSD_END)
    return sorted(
        {
            match.group(1)
            for match in _LISTEN.finditer(unmanaged)
            if match.group(1) not in {"127.0.0.1", "::1", "localhost"}
        }
    )


def _current_driver(text: str) -> str | None:
    block = _managed_block(text, _UPS_BEGIN, _UPS_END)
    if block is None:
        return None
    sections = _SECTION.findall(block)
    drivers = _DRIVER.findall(block)
    if sections != [UPS_NAME] or len(drivers) != 1 or drivers[0] not in ALLOWED_DRIVERS:
        raise OSError("Echo-managed UPS block is invalid")
    return drivers[0]


def _snapshot(
    *, ups_path: Path, upsd_path: Path, trusted_uid: int
) -> tuple[dict[str, Any], dict[str, tuple[bool, bytes, int, int]]]:
    ups_file = _safe_read(ups_path, trusted_uid=trusted_uid)
    upsd_file = _safe_read(upsd_path, trusted_uid=trusted_uid)
    ups_text = _text(ups_file[1], name=ups_path.name)
    upsd_text = _text(upsd_file[1], name=upsd_path.name)
    driver = _current_driver(ups_text)
    external_sections = _external_sections(ups_text)
    unsafe_listeners = _unsafe_listeners(upsd_text)
    revision = hashlib.sha256(ups_file[1] + b"\0" + upsd_file[1]).hexdigest()
    return (
        {
            "schemaVersion": SCHEMA_VERSION,
            "configured": driver is not None,
            "enabled": driver is not None,
            "name": UPS_NAME,
            "driver": driver,
            "port": "auto",
            "localOnly": not unsafe_listeners,
            "externallyManaged": bool(external_sections),
            "externalDeviceCount": len(external_sections),
            "allowedDrivers": list(ALLOWED_DRIVERS),
            "baseRevision": revision,
            "shutdownOwner": "echo-ups-shutdown-guard",
        },
        {"ups": ups_file, "upsd": upsd_file},
    )


def config_status(
    *,
    ups_path: Path = UPS_CONF,
    upsd_path: Path = UPSD_CONF,
    trusted_uid: int = 0,
) -> dict[str, Any]:
    snapshot, _files = _snapshot(ups_path=ups_path, upsd_path=upsd_path, trusted_uid=trusted_uid)
    snapshot.pop("baseRevision")
    return snapshot


def _desired(value: Mapping[str, Any]) -> dict[str, Any]:
    if set(value) != {"schema", "enabled", "driver"}:
        raise NutDeviceConfigError("local UPS desired state has an unexpected schema")
    if value["schema"] != DESIRED_SCHEMA or not isinstance(value["enabled"], bool):
        raise NutDeviceConfigError("local UPS desired state is invalid")
    driver = value["driver"]
    if not isinstance(driver, str) or driver not in ALLOWED_DRIVERS:
        raise NutDeviceConfigError("local UPS driver is not allowed")
    return {"schema": DESIRED_SCHEMA, "enabled": value["enabled"], "driver": driver}


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def plan_config(
    desired_state: Mapping[str, Any],
    *,
    ups_path: Path = UPS_CONF,
    upsd_path: Path = UPSD_CONF,
    trusted_uid: int = 0,
) -> dict[str, Any]:
    desired = _desired(desired_state)
    current, _files = _snapshot(ups_path=ups_path, upsd_path=upsd_path, trusted_uid=trusted_uid)
    if current["externallyManaged"]:
        raise NutDeviceConfigError("existing non-Echo UPS sections must be managed outside Echo")
    if not current["localOnly"]:
        raise NutDeviceConfigError("upsd has a non-loopback listener; Echo refuses to extend it")
    same = current["enabled"] == desired["enabled"] and (
        not desired["enabled"] or current["driver"] == desired["driver"]
    )
    operation = "none" if same else "enable" if desired["enabled"] else "disable"
    binding = {
        "schema": DESIRED_SCHEMA,
        "operation": operation,
        "baseRevision": current["baseRevision"],
        "current": {
            "enabled": current["enabled"],
            "driver": current["driver"],
        },
        "desired": desired,
        "safety": {
            "device": "singleLocalUsbUps",
            "port": "auto",
            "server": "loopbackOnly",
            "upsmon": "notConfigured",
            "shutdownOwner": "echo-ups-shutdown-guard",
        },
    }
    return {
        **binding,
        "planId": hashlib.sha256(_canonical(binding)).hexdigest(),
        "requiresApproval": operation != "none",
    }


def _append_block(text: str, block: str) -> str:
    base = text.rstrip()
    return f"{base}\n\n{block}\n" if base else f"{block}\n"


def _render(ups_text: str, upsd_text: str, desired: Mapping[str, Any]) -> tuple[bytes, bytes]:
    clean_ups = _without_block(ups_text, _UPS_BEGIN, _UPS_END)
    clean_upsd = _without_block(upsd_text, _UPSD_BEGIN, _UPSD_END)
    if not desired["enabled"]:
        return clean_ups.encode(), clean_upsd.encode()
    ups_block = (
        f"{_UPS_BEGIN}\n[{UPS_NAME}]\n    driver = {desired['driver']}\n    port = auto\n{_UPS_END}"
    )
    upsd_block = f"{_UPSD_BEGIN}\nLISTEN 127.0.0.1 3493\nLISTEN ::1 3493\n{_UPSD_END}"
    return (
        _append_block(clean_ups, ups_block).encode(),
        _append_block(clean_upsd, upsd_block).encode(),
    )


def _assert_parent(path: Path, *, trusted_uid: int) -> None:
    metadata = path.parent.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise OSError("NUT configuration directory is unsafe")
    if metadata.st_uid != trusted_uid or (os.name == "posix" and metadata.st_mode & 0o022):
        raise OSError("NUT configuration directory has unsafe ownership or mode")


def _atomic_write(path: Path, payload: bytes, *, mode: int, uid: int, gid: int) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        if os.name == "posix" and gid >= 0:
            os.chown(temporary, uid, gid)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _driver_available(driver: str, roots: tuple[Path, ...]) -> bool:
    return any((root / driver).is_file() and not (root / driver).is_symlink() for root in roots)


def _restart(
    *,
    command_runner: Callable[..., subprocess.CompletedProcess[str]],
    systemctl: Path,
) -> None:
    if not systemctl.is_file() or systemctl.is_symlink():
        raise OSError("trusted systemctl is unavailable")
    for arguments in (
        ("enable", "--now", "nut-driver-enumerator.path"),
        ("restart", "nut-driver-enumerator.service"),
        ("enable", "--now", "nut-server.service"),
    ):
        completed = command_runner(
            [str(systemctl), *arguments],
            capture_output=True,
            text=True,
            timeout=45.0,
            check=False,
            env={**os.environ, "LC_ALL": "C"},
        )
        if completed.returncode != 0:
            raise OSError("NUT service reconfiguration failed")


def _device_is_ready(snapshot: Mapping[str, Any]) -> bool:
    devices = snapshot.get("devices")
    return isinstance(devices, list) and any(
        isinstance(device, dict)
        and device.get("name") == UPS_NAME
        and device.get("available") is True
        for device in devices
    )


def _nut_group_id() -> int:
    if os.name != "posix":
        return -1
    try:
        import grp

        return grp.getgrnam("nut").gr_gid
    except (ImportError, KeyError) as exc:
        raise OSError("NUT service group is unavailable") from exc


def apply_config(
    desired_state: Mapping[str, Any],
    plan_id: str,
    *,
    ups_path: Path = UPS_CONF,
    upsd_path: Path = UPSD_CONF,
    trusted_uid: int = 0,
    trusted_gid: int | None = None,
    driver_roots: tuple[Path, ...] = (Path("/usr/lib/nut"), Path("/lib/nut")),
    systemctl: Path = SYSTEMCTL,
    command_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    status_reader: Callable[[], dict[str, Any]] = ups_status,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    if os.name == "posix" and os.geteuid() != trusted_uid:
        raise OSError("local UPS configuration requires root")
    with _LOCK:
        plan = plan_config(
            desired_state,
            ups_path=ups_path,
            upsd_path=upsd_path,
            trusted_uid=trusted_uid,
        )
        if plan["planId"] != plan_id:
            raise NutDeviceConfigError("local UPS plan is stale; preview again")
        if plan["operation"] == "none":
            return {**plan, "applied": False, "verified": True}
        if plan["desired"]["enabled"] and not _driver_available(
            plan["desired"]["driver"], driver_roots
        ):
            raise OSError("selected NUT driver is not installed")
        if trusted_gid is None:
            trusted_gid = _nut_group_id()
        _assert_parent(ups_path, trusted_uid=trusted_uid)
        _assert_parent(upsd_path, trusted_uid=trusted_uid)
        _current, files = _snapshot(ups_path=ups_path, upsd_path=upsd_path, trusted_uid=trusted_uid)
        ups_file, upsd_file = files["ups"], files["upsd"]
        rendered = _render(
            _text(ups_file[1], name=ups_path.name),
            _text(upsd_file[1], name=upsd_path.name),
            plan["desired"],
        )
        try:
            _atomic_write(
                ups_path,
                rendered[0],
                mode=ups_file[2],
                uid=trusted_uid,
                gid=ups_file[3] if ups_file[0] else trusted_gid,
            )
            _atomic_write(
                upsd_path,
                rendered[1],
                mode=upsd_file[2],
                uid=trusted_uid,
                gid=upsd_file[3] if upsd_file[0] else trusted_gid,
            )
            _restart(command_runner=command_runner, systemctl=systemctl)
            verified = config_status(
                ups_path=ups_path, upsd_path=upsd_path, trusted_uid=trusted_uid
            )
            if verified["enabled"] != plan["desired"]["enabled"] or (
                verified["enabled"] and verified["driver"] != plan["desired"]["driver"]
            ):
                raise OSError("local UPS configuration verification failed")
            if verified["enabled"]:
                ready = False
                for attempt in range(5):
                    if _device_is_ready(status_reader()):
                        ready = True
                        break
                    if attempt < 4:
                        sleeper(1.0)
                if not ready:
                    raise OSError("configured local UPS did not become readable")
        except OSError as exc:
            try:
                for path, original in ((ups_path, ups_file), (upsd_path, upsd_file)):
                    if original[0]:
                        _atomic_write(
                            path,
                            original[1],
                            mode=original[2],
                            uid=trusted_uid,
                            gid=original[3],
                        )
                    elif path.exists() and not path.is_symlink():
                        path.unlink()
                _restart(command_runner=command_runner, systemctl=systemctl)
            except OSError as rollback_exc:
                raise OSError(
                    "local UPS update failed and rollback was incomplete"
                ) from rollback_exc
            raise OSError("local UPS update failed and was rolled back") from exc
        return {**plan, "applied": True, "verified": True, "config": verified}


__all__ = [
    "ALLOWED_DRIVERS",
    "DESIRED_SCHEMA",
    "NutDeviceConfigError",
    "apply_config",
    "config_status",
    "plan_config",
]
