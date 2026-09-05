"""Native (OpenMediaVault-free) storage plane.

Storage source built exclusively on standard Linux tooling that
already ships with the appliance: ``zpool``, ``lsblk``, ``df`` and
``smartctl``. The kernel — not a third-party NAS panel — is the single source
of truth for device state, so no parallel storage state is ever maintained.

Payloads intentionally match the versioned OMV-compatible API contracts, so
the existing appliance UI works identically with or without OpenMediaVault
installed. The native runtime imports only the protocol definitions; it does
not load the optional bridge client or its HTTP transport:

    /status       -> OmvStatus
    /health       -> OmvHealthSnapshot
    /filesystems  -> OmvFilesystem[]
    /smart        -> OmvSmart
    /smart/devices-> OmvSmartDevice[]
    /topology     -> OmvStorageTopology

Write support is deliberately split into narrow desired/plan/apply slices.
Shared folders are limited to registered directories on mounted NAS volumes;
privileges only touch the selected directory's non-recursive POSIX ACL; NFS
only owns one generated file below ``/etc/exports.d``. Formatting, pool
deletion, recursive permission changes, and arbitrary protocol options remain
outside this module.
"""

from __future__ import annotations

import contextlib
import csv
import hashlib
import io
import json
import os
import posixpath
import re
import shutil
import stat as stat_module
import subprocess
import tempfile
import threading
import uuid as uuid_module
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from appliance.omv_protocol import (
    GROUP_PLAN_SCHEMA,
    NFS_PLAN_SCHEMA,
    QUOTA_PLAN_SCHEMA,
    SHARE_PRIVILEGE_PLAN_SCHEMA,
    SHARED_FOLDER_DESIRED_SCHEMA,
    SMB_PLAN_SCHEMA,
    USER_PASSWORD_PLAN_SCHEMA,
    USER_PLAN_SCHEMA,
    validate_devicefile,
    validate_group_desired,
    validate_nfs_desired,
    validate_quota_desired,
    validate_share_privilege_desired,
    validate_shared_folder_desired,
    validate_smb_desired,
    validate_user_desired,
    validate_user_password_desired,
)

SCHEMA_VERSION = 1

SHARED_FOLDER_PLAN_SCHEMA = "echo.omv.shared-folder-plan.v1"
_NATIVE_SHARE_REGISTRY = Path("/var/lib/echo-os/native-shared-folders.json")
_NATIVE_NFS_EXPORTS = Path("/etc/exports.d/echo-os.exports")
_STORAGE_UUID_NAMESPACE = uuid_module.UUID("6f1e0d5c-3a34-4f5e-9a52-1f65c3b07a11")
_REGISTRY_THREAD_LOCK = threading.RLock()
_NATIVE_DATA_MOUNT_ROOTS = ("/data", "/mnt", "/srv", "/fs", "/volume")
_ZFS_DATASET_PATTERN = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._:-]*(?:/[A-Za-z0-9][A-Za-z0-9._:-]*)*"
)
_NATIVE_KERNEL_QUOTA_FILESYSTEMS = frozenset({"ext2", "ext3", "ext4", "xfs"})
_QUOTA_REPORT_MAX_BYTES = 1024 * 1024

# Filesystem types that carry no persistent user data; they would otherwise
# dominate the list on any Debian host.
_PSEUDO_FS = {
    "tmpfs",
    "devtmpfs",
    "proc",
    "sysfs",
    "cgroup",
    "cgroup2",
    "devpts",
    "securityfs",
    "pstore",
    "bpf",
    "tracefs",
    "debugfs",
    "configfs",
    "fusectl",
    "ramfs",
    "hugetlbfs",
    "mqueue",
    "autofs",
    "binfmt_misc",
    "efivarfs",
    "nsfs",
    "overlay",
}

_ZFS_HEALTH_TO_STATE = {
    "ONLINE": "healthy",
    "DEGRADED": "degraded",
    "FAULTED": "critical",
    "OFFLINE": "critical",
    "UNAVAIL": "critical",
    "REMOVED": "warning",
    "SUSPENDED": "warning",
}

_MD_STATE_MAP = {
    "UU": "healthy",
    "UUU": "healthy",
    "clean": "healthy",
    "active": "healthy",
    "recovering": "recovering",
    "resync": "recovering",
    "resyncing": "recovering",
    "check": "checking",
    "checking": "checking",
    "repair": "checking",
    "degraded": "degraded",
    "inactive": "inactive",
}

_USAGE_WARN_PERCENT = 85
_USAGE_CRITICAL_PERCENT = 95

# Virtual machines expose a 4 KB floppy and QEMU sometimes reports zero-sized
# loop devices; anything smaller than 1 GiB is never a real data disk.
_MIN_DISK_BYTES = 1024**3


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _run(*args: str, timeout: float = 20.0) -> str:
    """Run a read-only command; return stdout, or "" when it is unavailable."""
    try:
        completed = subprocess.run(
            list(args),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout or ""


def _mount_read_only(mountpoint: str) -> bool:
    """Read the kernel's current mount flag without trusting a config file."""
    if shutil.which("findmnt") is None:
        return False
    options = _run("findmnt", "-n", "-o", "OPTIONS", "-T", mountpoint, timeout=10.0)
    tokens = {item.strip().casefold() for item in options.split(",") if item.strip()}
    return "ro" in tokens and "rw" not in tokens


def _native_quota_tools_available() -> bool:
    """Return whether the standard kernel-quota command pair is installed."""
    return all(shutil.which(binary) is not None for binary in ("repquota", "setquota"))


def _native_command_tools_available(*binaries: str) -> bool:
    """Return whether every host command needed by one native write slice exists."""
    return all(shutil.which(binary) is not None for binary in binaries)


def _native_data_mountpoint(value: Any) -> bool:
    """Return whether a mountpoint belongs to an explicitly managed NAS root."""
    if not isinstance(value, str) or not value:
        return False
    # ``df``/``findmnt`` always report POSIX paths, even when their output is
    # exercised by the Windows test suite. Keep that comparison independent of
    # the host running the Python process, while retaining host-path support
    # for isolated native-volume tests.
    if posixpath.isabs(value):
        normalized = posixpath.normpath(value)
        roots = tuple(posixpath.normpath(root) for root in _NATIVE_DATA_MOUNT_ROOTS)
        commonpath = posixpath.commonpath
    elif os.path.isabs(value):
        normalized = os.path.normpath(value)
        roots = tuple(os.path.normpath(root) for root in _NATIVE_DATA_MOUNT_ROOTS)
        commonpath = os.path.commonpath
    else:
        return False
    for normalized_root in roots:
        try:
            if commonpath((normalized, normalized_root)) == normalized_root:
                return True
        except ValueError:
            continue
    return False


def _kernel_quota_enabled(mountpoint: str, subject_type: str) -> bool:
    """Return whether the mounted filesystem advertises the requested quota mode.

    ``repquota`` exits with a generic command error when a supported filesystem
    has the quota package installed but was mounted without quota accounting.
    Checking the kernel mount options first lets the API report that expected
    configuration gap as a validation error while retaining a best-effort
    fallback on hosts without ``findmnt``.
    """
    if shutil.which("findmnt") is None:
        return True
    options = _run("findmnt", "-n", "-o", "OPTIONS", "-T", mountpoint, timeout=10.0)
    if not options:
        return True
    tokens = {item.strip().casefold() for item in options.split(",") if item.strip()}
    if subject_type == "user":
        enabled = any(
            token in {"quota", "usrquota", "uquota"}
            or token.startswith("usrjquota=")
            for token in tokens
        )
    else:
        enabled = any(
            token in {"quota", "grpquota", "gquota"}
            or token.startswith("grpjquota=")
            for token in tokens
        )
    if not enabled:
        raise ValueError(f"kernel {subject_type} quotas are not enabled on this filesystem")
    return True


def _run_json(*args: str, timeout: float = 25.0) -> dict[str, Any]:
    """Run ``smartctl -j`` and keep the JSON even on a non-zero exit.

    smartctl exits non-zero for plenty of benign reasons (a virtio disk simply
    has no SMART table, an old disk lacks a capability) while still printing a
    perfectly usable JSON payload. Throwing the payload away would report every
    such device as "UNKNOWN" for no reason.
    """
    try:
        completed = subprocess.run(
            list(args),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if not completed.stdout:
        return {}
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _percent(used: int | None, total: int | None) -> int | None:
    if not used or not total or total <= 0:
        return None
    return max(0, min(100, int(round(used * 100.0 / total))))


# --------------------------------------------------------------------------- #
# Devices / topology
# --------------------------------------------------------------------------- #


def block_devices() -> list[dict[str, Any]]:
    """Enumerate physical disks via ``lsblk`` (kernel is the authority)."""
    out = _run(
        "lsblk",
        "-J",
        "-b",
        "-o",
        "NAME,SIZE,TYPE,MOUNTPOINT,FSTYPE,MODEL,SERIAL,ROTA,PKNAME",
    )
    if not out:
        return []
    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        return []

    devices: list[dict[str, Any]] = []

    def walk(node: dict[str, Any]) -> None:
        name = node.get("name")
        node_type = node.get("type") or "disk"
        if (
            node_type
            in {"disk", "rom", "part", "lvm", "md", "raid0", "raid1", "raid5", "raid6", "raid10"}
            and node_type == "disk"
            and (_int(node.get("size")) or 0) >= _MIN_DISK_BYTES
        ):
            devices.append(
                {
                    "devicefile": f"/dev/{name}",
                    "type": node_type,
                    "sizeBytes": _int(node.get("size")),
                    "filesystemType": node.get("fstype") or None,
                    "rotational": None
                    if node.get("rota") is None
                    else bool(node.get("rota") == "1" or node.get("rota") is True),
                    "model": (node.get("model") or "").strip() or None,
                    "serial": (node.get("serial") or "").strip() or None,
                    "parentDevicefiles": [],
                }
            )
        for child in node.get("children") or []:
            walk(child)

    for node in payload.get("blockdevices") or []:
        walk(node)
    return devices


def md_arrays() -> list[dict[str, Any]]:
    """Parse ``/proc/mdstat`` into the OMV RAID array shape."""
    try:
        with open("/proc/mdstat", encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError:
        return []

    arrays: list[dict[str, Any]] = []
    for line in text.splitlines():
        match = re.match(r"^md\d+\s*:", line)
        if not match:
            continue
        name = line.split(":", 1)[0].strip()
        body = line.split(":", 1)[1]
        level = "unknown"
        for candidate in ("raid0", "raid1", "raid10", "raid5", "raid6", "linear"):
            if candidate in body:
                level = candidate
                break
        status = "unknown"
        for token, mapped in _MD_STATE_MAP.items():
            if token in body:
                status = mapped
                break
        # "[UU]" / "[U_]" patterns describe member states
        member_block = re.search(r"\[([U_]+)\]", body)
        total = active = None
        if member_block:
            slots = member_block.group(1)
            total = len(slots)
            active = slots.count("U")
            if active < total:
                status = "degraded"
        progress = None
        progress_match = re.search(r"=\s*\S+\s*\((\d+(?:\.\d+)?)%\)", body)
        if progress_match:
            progress = int(float(progress_match.group(1)))
        operation = None
        for token in ("recovery", "resync", "check", "reshape"):
            if token in body:
                operation = token
                break
        arrays.append(
            {
                "devicefile": f"/dev/{name}",
                "level": level,
                "status": status,
                "totalDevices": total,
                "activeDevices": active,
                "operation": operation,
                "operationPercent": progress,
            }
        )
    return arrays


def zfs_pools() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (arrays, alerts) derived from ``zpool list``/``zpool status``."""
    out = _run("zpool", "list", "-H", "-p", "-o", "name,size,alloc,free,health,cap,frag")
    if not out:
        return [], []

    arrays: list[dict[str, Any]] = []
    alerts: list[dict[str, Any]] = []
    for line in out.strip().splitlines():
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        name, size, _alloc, _free, health = parts[0], parts[1], parts[2], parts[3], parts[4]
        level = _zfs_level(name)
        status = _ZFS_HEALTH_TO_STATE.get(health, "unknown")
        arrays.append(
            {
                "devicefile": name,
                "level": level,
                "status": status,
                "totalDevices": None,
                "activeDevices": None,
                "operation": "scrub" if _zfs_scrubbing(name) else None,
                "operationPercent": _zfs_scrub_percent(name),
                "sizeBytes": _int(size),
                "health": health,
                "kind": "zfs",
            }
        )
        if status in {"degraded", "critical", "warning"}:
            severity = "critical" if status == "critical" else "warning"
            alerts.append(
                {
                    "id": f"zfs:{name}",
                    "code": f"zfs.{health.lower()}",
                    "severity": severity,
                    "resource": name,
                    "message": f"ZFS 存储池 {name} 状态: {health}",
                }
            )
    return arrays, alerts


def _zfs_level(pool: str) -> str:
    status = _run("zpool", "status", pool, timeout=10.0)
    match = re.search(
        r"^\s*(\S+)-\d+\s+(ONLINE|DEGRADED|FAULTED|OFFLINE|UNAVAIL)", status, re.MULTILINE
    )
    if match:
        return match.group(1)
    if "raidz2" in status:
        return "raidz2"
    if "raidz1" in status or "raidz" in status:
        return "raidz1"
    if "mirror" in status:
        return "mirror"
    return "zfs"


def _zfs_scrubbing(pool: str) -> bool:
    return "scrub in progress" in _run("zpool", "status", pool, timeout=10.0)


def _zfs_scrub_percent(pool: str) -> int | None:
    match = re.search(
        r"scrub in progress[^\n]*?(\d+(?:\.\d+)?)% done",
        _run("zpool", "status", pool, timeout=10.0),
    )
    if match:
        return int(float(match.group(1)))
    return None


def storage_topology() -> dict[str, Any]:
    """Devices + arrays (mdraid and ZFS) in the OMV topology shape."""
    devices = block_devices()
    arrays = md_arrays()
    zfs, _alerts = zfs_pools()
    arrays.extend(zfs)
    return {"devices": devices, "arrays": arrays, "readOnly": True}


# --------------------------------------------------------------------------- #
# Filesystems
# --------------------------------------------------------------------------- #


def filesystems() -> list[dict[str, Any]]:
    """Mounted filesystems with capacity, in the OMV filesystem shape."""
    out = _run("df", "-B1", "-P", "-T")
    if not out:
        return []
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    quota_tools_available = _native_quota_tools_available()
    for line in out.strip().splitlines()[1:]:
        parts = line.split()
        if len(parts) < 7:
            continue
        devicefile, fstype, size, used, available, _used_percent, mountpoint = (
            parts[0],
            parts[1],
            parts[2],
            parts[3],
            parts[4],
            parts[5],
            " ".join(parts[6:]),
        )
        if fstype in _PSEUDO_FS or devicefile in seen:
            continue
        seen.add(devicefile)
        size_i = _int(size)
        available_i = _int(available)
        if size_i is None or available_i is None:
            continue
        read_only = _mount_read_only(mountpoint)
        supports_quota = _native_data_mountpoint(mountpoint) and (
            fstype == "zfs"
            or fstype in _NATIVE_KERNEL_QUOTA_FILESYSTEMS
            and quota_tools_available
        )
        entries.append(
            {
                "devicefile": devicefile,
                "parentdevicefile": None,
                # Native Linux tooling does not expose an OMV UUID for every
                # mounted dataset (notably ZFS). Keep a stable provider-local
                # identity so quota and shared-folder plans can bind without
                # returning a host path to the browser.
                "uuid": _filesystem_uuid(devicefile, mountpoint),
                "label": mountpoint.split("/")[-1] or mountpoint,
                "type": fstype,
                "mountpoint": mountpoint,
                "sizeBytes": size_i,
                "availableBytes": available_i,
                "usedPercent": _percent(_int(used), size_i),
                "readOnly": read_only,
                "supportsAcl": False,
                # ZFS uses native userquota/groupquota properties. ext2/3/4
                # and XFS use the standard kernel quota tools when installed;
                # the plan path still verifies that quotas are enabled.
                "supportsQuota": supports_quota,
            }
        )
    return entries


# --------------------------------------------------------------------------- #
# SMART
# --------------------------------------------------------------------------- #


def _smart_json(devicefile: str, extra: tuple[str, ...] = ("-H",)) -> dict[str, Any]:
    return _run_json("smartctl", "-j", *extra, devicefile)


def smart_report(devicefile: str) -> dict[str, Any]:
    """Full SMART report for one device (OVM ``OmvSmart`` shape)."""
    payload = _smart_json(devicefile, ("-H", "-A", "-i"))
    passed = (payload.get("smart_status") or {}).get("passed")
    health = "PASSED" if passed is True else "FAILED" if passed is False else "UNKNOWN"
    temperature = (payload.get("temperature") or {}).get("current")
    return {
        "devicefile": devicefile,
        "model": payload.get("model_name") or None,
        "health": health,
        "temperatureC": _int(temperature),
        "powerOnHours": _int((payload.get("power_on_time") or {}).get("hours")),
        "powerCycles": _int(payload.get("power_cycle_count") or 0) or None,
    }


def smart_devices() -> list[dict[str, Any]]:
    """Quick SMART health for every physical disk (parallel, bounded)."""
    known = block_devices()
    disks = [d["devicefile"] for d in known if d.get("devicefile")]
    if not disks:
        return []
    sizes = {d["devicefile"]: d.get("sizeBytes") for d in known}

    def probe(dev: str) -> dict[str, Any]:
        payload = _smart_json(dev, ("-H", "-i"))
        passed = (payload.get("smart_status") or {}).get("passed")
        return {
            "devicefile": dev,
            "model": payload.get("model_name") or None,
            "sizeBytes": sizes.get(dev),
            "health": "PASSED" if passed is True else "FAILED" if passed is False else "UNKNOWN",
            "temperatureC": _int((payload.get("temperature") or {}).get("current")),
        }

    with ThreadPoolExecutor(max_workers=8) as pool:
        return list(pool.map(probe, disks))


# --------------------------------------------------------------------------- #
# Aggregated health
# --------------------------------------------------------------------------- #


def _usage_alerts(fs_entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    for entry in fs_entries:
        used = entry.get("usedPercent")
        if used is None:
            continue
        if used >= _USAGE_CRITICAL_PERCENT:
            severity = "critical"
        elif used >= _USAGE_WARN_PERCENT:
            severity = "warning"
        else:
            continue
        alerts.append(
            {
                "id": f"usage:{entry['devicefile']}",
                "code": "filesystem.usage",
                "severity": severity,
                "resource": entry["mountpoint"],
                "message": f"{entry['mountpoint']} 已用 {used}%",
            }
        )
    return alerts


def storage_health() -> dict[str, Any]:
    """Aggregate native signals into the OmvHealthSnapshot shape."""
    checked_at = _now()
    fs_entries = filesystems()
    _zfs, zfs_alerts = zfs_pools()
    arrays = md_arrays()
    devices = smart_devices()

    alerts: list[dict[str, Any]] = list(zfs_alerts)
    alerts.extend(_usage_alerts(fs_entries))

    for array in arrays:
        if array.get("status") in {"degraded", "critical"}:
            alerts.append(
                {
                    "id": f"md:{array['devicefile']}",
                    "code": "raid.degraded",
                    "severity": "critical" if array["status"] == "critical" else "warning",
                    "resource": array["devicefile"],
                    "message": f"软阵列 {array['devicefile']} 未处于健康状态",
                }
            )
    for device in devices:
        if device.get("health") == "FAILED":
            alerts.append(
                {
                    "id": f"smart:{device['devicefile']}",
                    "code": "smart.failed",
                    "severity": "critical",
                    "resource": device["devicefile"],
                    "message": f"{device['devicefile']} SMART 自检失败,请尽快备份并更换硬盘",
                }
            )

    critical = sum(1 for a in alerts if a["severity"] == "critical")
    warning = sum(1 for a in alerts if a["severity"] == "warning")
    state = "healthy"
    if critical:
        state = "critical"
    elif warning:
        state = "warning"

    now = checked_at
    return {
        "schemaVersion": SCHEMA_VERSION,
        "state": state,
        "stale": False,
        "checkedAt": checked_at,
        "lastSuccessfulAt": now,
        "intervalSeconds": 0,
        "persistenceHealthy": True,
        "monitoring": False,
        "activeAlerts": [
            {**alert, "firstSeenAt": now, "lastSeenAt": now, "occurrences": 1} for alert in alerts
        ],
        "events": [],
        "summary": {"critical": critical, "warning": warning, "total": len(alerts)},
        "readOnly": True,
        "source": "native",
        "pools": len(_zfs),
        "devices": len(devices),
    }


# Write slices the native plane can actually perform on this host.
_NATIVE_WRITE_CAPABILITIES = (
    "shared-folder.create.simple.v1",
    "account.group.create.v1",
    "account.user.create.v1",
    "account.user.password.reset.v1",
    "shared-folder.privilege.simple.v1",
    "smb.share.desired.v1",
    "nfs.share.private-network.v1",
    "filesystem.quota.user-group.v1",
)


def _native_write_capabilities() -> list[str]:
    """Publish only write slices whose host-side prerequisites are present.

    Capability discovery is part of the UI contract: a missing optional host
    package should disable its control instead of advertising a button that
    can only fail later during ``plan``.  The shared-folder slice itself uses
    Python/POSIX primitives and remains available; it still requires a
    mounted writable NAS target at plan time.
    """
    unavailable: set[str] = set()
    if not _native_command_tools_available("groupadd"):
        unavailable.add("account.group.create.v1")
    if not _native_command_tools_available("useradd", "chpasswd", "smbpasswd"):
        unavailable.add("account.user.create.v1")
    if not _native_command_tools_available("chpasswd", "smbpasswd"):
        unavailable.add("account.user.password.reset.v1")
    if not _native_command_tools_available("getfacl", "setfacl"):
        unavailable.add("shared-folder.privilege.simple.v1")
    if not _native_command_tools_available("net", "smbd"):
        unavailable.add("smb.share.desired.v1")
    if not _native_command_tools_available("exportfs"):
        unavailable.add("nfs.share.private-network.v1")
    if not (
        _native_command_tools_available("zfs") or _native_quota_tools_available()
    ):
        unavailable.add("filesystem.quota.user-group.v1")
    return [capability for capability in _NATIVE_WRITE_CAPABILITIES if capability not in unavailable]


def status() -> dict[str, Any]:
    """The native plane is always 'configured' — it needs no external panel."""
    return {
        "configured": True,
        "available": bool(block_devices()),
        "readOnly": False,
        "adminUrl": None,
        "capabilities": _native_write_capabilities(),
        "source": "native",
    }


# --------------------------------------------------------------------------- #
# Sharing inventory (system users/groups + Samba usershares)
# --------------------------------------------------------------------------- #

_SYSTEM_UID_FLOOR = 1000


def _etc_entries(path: str) -> list[list[str]]:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return [line.rstrip("\n").split(":") for line in handle if line.strip()]
    except OSError:
        return []


def _is_visible_nas_user(uid: int) -> bool:
    """Return whether a POSIX uid is safe to expose as a NAS identity."""
    return _SYSTEM_UID_FLOOR <= uid < 65534


def _is_visible_nas_group(name: str, gid: int) -> bool:
    """Keep normal groups visible, plus Debian's shared ``users`` group."""
    return 0 <= gid < 65534 and (gid >= _SYSTEM_UID_FLOOR or name == "users")


def _samba_service_available() -> bool:
    """Report whether the native usershare tool and daemon are installed.

    An empty usershare inventory is a valid initial state.  Using the number
    of existing shares as the service flag makes the first SMB share appear
    disabled and prevents the UI from offering its create action.
    """
    return all(shutil.which(binary) is not None for binary in ("net", "smbd"))


def _samba_usershares() -> list[dict[str, Any]]:
    out = _run("net", "usershare", "info", "--long", timeout=15.0)
    shares: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in (out or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if not line.startswith("[") or not line.endswith("]"):
            if current is not None:
                key, value = _samba_setting(line)
                if not key:
                    continue
                key, value = key.strip(), value.strip()
                if key == "path":
                    current["sharedFolderName"] = value.rstrip("/").split("/")[-1]
                elif key == "comment":
                    current["comment"] = value
                elif key == "usershare_acl":
                    current["readOnly"] = _smb_info_read_only({"usershare_acl": value})
                elif key == "guest_ok":
                    current["guest"] = "yes" if value.casefold() == "y" else "no"
        else:
            current = {
                "uuid": line.strip("[]"),
                "sharedFolderRef": line.strip("[]"),
                "sharedFolderName": "",
                "enabled": True,
                "readOnly": False,
                "guest": "none",
                "browseable": True,
                "recycleBin": False,
                "comment": "",
            }
            shares.append(current)
    return shares


def _samba_setting(line: str) -> tuple[str, str]:
    """Parse both ``key=value`` (Samba's output) and legacy ``key: value``."""
    if "=" in line:
        return line.split("=", 1)
    key, separator, value = line.partition(":")
    return (key, value) if separator else ("", "")


def _nfs_service_available() -> bool:
    return shutil.which("exportfs") is not None


def _native_nfs_shares() -> list[dict[str, Any]]:
    """Return only Echo-managed NFS rules; never infer ownership of other exports."""
    folders = {
        folder_uuid: entry
        for entry in _registry_load()
        if (folder_uuid := _registered_uuid(entry)) is not None
    }
    result: list[dict[str, Any]] = []
    for entry in _nfs_exports_load():
        folder = folders.get(entry.get("sharedFolderRef"))
        if folder is None:
            continue
        result.append(
            {
                "uuid": entry["uuid"],
                "sharedFolderRef": entry["sharedFolderRef"],
                "sharedFolderName": folder.get("name", ""),
                "client": entry["client"],
                "options": "ro" if entry["readOnly"] else "rw",
                "comment": entry["comment"],
            }
        )
    return result


def sharing_overview() -> dict[str, Any]:
    """Inventory straight from the host: getent + mounts + Samba usershares.

    Same top-level shape as the OMV bridge inventory, so the account directory
    (Echo 家庭成员 → 存储身份) and the sharing panels keep working without
    OpenMediaVault. System accounts below uid 1000 are infrastructure and are
    never exposed as NAS identities.
    """
    users: list[dict[str, Any]] = []
    for fields in _etc_entries("/etc/passwd"):
        if len(fields) < 6:
            continue
        name, _pw, uid_s, gid_s, gecos, _home = fields[:6]
        uid, gid = _int(uid_s), _int(gid_s)
        if uid is None or gid is None or not _is_visible_nas_user(uid):
            continue
        users.append(
            {
                "name": name,
                "uid": uid,
                "gid": gid,
                "comment": gecos.split(",")[0],
                "groups": [
                    g[0]
                    for g in _etc_entries("/etc/group")
                    if name in (g[3].split(",") if len(g) > 3 else [])
                ],
            }
        )
    groups = [
        {
            "name": fields[0],
            "gid": _int(fields[2]) or 0,
            "members": fields[3].split(",") if len(fields) > 3 else [],
        }
        for fields in _etc_entries("/etc/group")
        if (
            len(fields) > 3
            and (gid := _int(fields[2])) is not None
            and _is_visible_nas_group(fields[0], gid)
        )
    ]

    fs_entries = filesystems()
    shared_folders = [
        {
            "uuid": volume_uuid(entry["mountpoint"]),
            "name": entry["label"],
            "comment": "",
            # A filesystem root is inventory-only, not an Echo-registered
            # share.  Never expose its host mountpoint through this field.
            "relativePath": "/",
            "device": _public_devicefile(entry.get("devicefile")),
            "status": "MOUNTED",
            "inUse": True,
            "supportsAcl": bool(entry.get("supportsAcl")),
        }
        for entry in fs_entries
    ]
    # 原生写面登记的共享文件夹(2770, users 组)也并入清单。
    for entry in _registry_load():
        folder_uuid = _registered_uuid(entry)
        relative_name = _registered_relative_name(entry)
        if folder_uuid is None or relative_name is None or not isinstance(entry.get("name"), str):
            continue
        shared_folders.append(
            {
                "uuid": folder_uuid,
                "name": entry["name"],
                "comment": entry.get("comment", ""),
                "relativePath": relative_name,
                "device": _public_devicefile(entry.get("device")),
                "status": "MOUNTED",
                "inUse": True,
                "supportsAcl": shutil.which("getfacl") is not None
                and shutil.which("setfacl") is not None,
            }
        )
    shared_folder_targets = [
        {
            "mountPointRef": volume_uuid(entry["mountpoint"]),
            "filesystemUuid": _filesystem_ref(entry),
            "label": entry["label"],
            "type": entry["type"],
            "sizeBytes": entry["sizeBytes"],
            "availableBytes": entry["availableBytes"],
            "readOnly": False,
        }
        for entry in fs_entries
        if _is_native_share_target(entry)
    ]
    smb_available = _samba_service_available()
    smb_shares = _samba_usershares()
    nfs_shares = _native_nfs_shares()
    return {
        "sharedFolders": shared_folders,
        "sharedFolderTargets": shared_folder_targets,
        "users": users,
        "groups": groups,
        "smb": {"enabled": smb_available, "shares": smb_shares},
        "nfs": {"enabled": _nfs_service_available(), "shares": nfs_shares},
        "readOnly": True,
    }


# --------------------------------------------------------------------------- #
# Native write plane: shared folders (first slice)
# --------------------------------------------------------------------------- #


def volume_uuid(mountpoint: str) -> str:
    """Deterministic UUID for a mountpoint (stable across reboots)."""
    return str(uuid_module.uuid5(_STORAGE_UUID_NAMESPACE, f"echo-storage:{mountpoint}"))


def _filesystem_uuid(devicefile: str, mountpoint: str) -> str:
    """Return a stable native identity when the host has no filesystem UUID."""
    return str(
        uuid_module.uuid5(
            _STORAGE_UUID_NAMESPACE,
            f"echo-filesystem:{devicefile}:{mountpoint}",
        )
    )


def _filesystem_ref(entry: dict[str, Any]) -> str:
    """Project a filesystem UUID without exposing its mountpoint."""
    raw_uuid = entry.get("uuid")
    if isinstance(raw_uuid, str) and raw_uuid:
        return raw_uuid.lower()
    return _filesystem_uuid(
        str(entry.get("devicefile") or ""), str(entry.get("mountpoint") or "")
    )


def _public_devicefile(value: Any) -> str:
    """Keep only canonical device or ZFS dataset identifiers in public rows."""
    if not isinstance(value, str) or not value or any(character < " " for character in value):
        return ""
    if os.path.normpath(value) != value or ".." in Path(value).parts:
        return ""
    if value.startswith("/dev/"):
        try:
            return validate_devicefile(value)
        except ValueError:
            return ""
    return value if _ZFS_DATASET_PATTERN.fullmatch(value) else ""


def _registered_uuid(entry: dict[str, Any], *, strict: bool = False) -> str | None:
    raw_uuid = entry.get("uuid")
    try:
        parsed = uuid_module.UUID(raw_uuid) if isinstance(raw_uuid, str) else None
        if parsed is None or str(parsed) != raw_uuid.lower():
            raise ValueError
        return str(parsed)
    except (TypeError, ValueError, AttributeError):
        if strict:
            raise OSError("native shared-folder registry contains an invalid UUID") from None
        return None


def _registered_relative_name(entry: dict[str, Any], *, strict: bool = False) -> str | None:
    """Accept only the single portable component created by the native plane."""
    raw_relative = entry.get("relativePath")
    raw_name = entry.get("name")
    candidate = raw_relative if raw_relative is not None else raw_name
    try:
        if not isinstance(candidate, str):
            raise ValueError
        normalized = validate_shared_folder_desired(
            {
                "schema": SHARED_FOLDER_DESIRED_SCHEMA,
                "mountPointRef": "11111111-2222-4333-8444-555555555555",
                "name": candidate,
                "comment": "",
            }
        )["name"]
        if raw_name is not None and raw_name != normalized:
            raise ValueError
        if raw_relative is not None and raw_relative != normalized:
            raise ValueError
        return normalized
    except (TypeError, ValueError, KeyError):
        if strict:
            raise OSError(
                "native shared-folder registry contains an unsafe relative path"
            ) from None
        return None


def _registry_load(*, strict: bool = False) -> list[dict[str, Any]]:
    try:
        with open(_NATIVE_SHARE_REGISTRY, encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError) as exc:
        if strict:
            raise OSError("native shared-folder registry is unreadable") from exc
        return []
    if not isinstance(payload, list) or any(not isinstance(entry, dict) for entry in payload):
        if strict:
            raise OSError("native shared-folder registry has an invalid shape")
        return []
    return payload


def _registry_save(entries: list[dict[str, Any]]) -> None:
    _NATIVE_SHARE_REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{_NATIVE_SHARE_REGISTRY.name}.",
        suffix=".tmp",
        dir=_NATIVE_SHARE_REGISTRY.parent,
    )
    temporary = Path(temporary_name)
    try:
        _make_private(descriptor, temporary)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            json.dump(entries, handle, ensure_ascii=False, indent=1)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, _NATIVE_SHARE_REGISTRY)
        with contextlib.suppress(OSError):
            directory_fd = os.open(_NATIVE_SHARE_REGISTRY.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def _read_regular_text(path: Path, *, missing_ok: bool = False) -> str | None:
    """Read a managed file without following a final-component symlink."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        if missing_ok:
            return None
        raise
    if not stat_module.S_ISREG(info.st_mode):
        raise OSError(f"managed file is not a regular file: {path}")
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise OSError(f"managed file is unreadable: {path}") from exc


def _atomic_text_save(path: Path, content: str, *, mode: int) -> None:
    """Atomically replace a small root-managed UTF-8 configuration file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        fchmod = getattr(os, "fchmod", None)
        if callable(fchmod):
            fchmod(descriptor, mode)
        else:  # pragma: no cover - exercised by the Windows CI job
            temporary.chmod(mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        with contextlib.suppress(OSError):
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def _restore_managed_text(path: Path, content: str | None, *, mode: int) -> None:
    if content is None:
        with contextlib.suppress(FileNotFoundError):
            path.unlink()
        return
    _atomic_text_save(path, content, mode=mode)


def _make_private(descriptor: int, path: Path) -> None:
    fchmod = getattr(os, "fchmod", None)
    if callable(fchmod):
        fchmod(descriptor, 0o600)
    else:  # pragma: no cover - exercised by the Windows CI job
        path.chmod(0o600)


@contextmanager
def _registry_transaction() -> Iterator[None]:
    """Serialize registry and directory state changes across threads/processes."""
    with _REGISTRY_THREAD_LOCK:
        _NATIVE_SHARE_REGISTRY.parent.mkdir(parents=True, exist_ok=True)
        lock_path = _NATIVE_SHARE_REGISTRY.with_name(f".{_NATIVE_SHARE_REGISTRY.name}.lock")
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(lock_path, flags, 0o600)
        try:
            info = os.fstat(descriptor)
            if not stat_module.S_ISREG(info.st_mode):
                raise OSError("native shared-folder lock is not a regular file")
            _make_private(descriptor, lock_path)
            try:
                import fcntl
            except ImportError:  # pragma: no cover - Windows tests use the thread lock
                fcntl = None  # type: ignore[assignment]
            if fcntl is not None:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            os.close(descriptor)


def _writable_targets() -> dict[str, str]:
    """mountPointRef(uuid) -> dedicated NAS data mount, never the system root."""
    targets: dict[str, str] = {}
    for entry in filesystems():
        if not _is_native_share_target(entry):
            continue
        mountpoint = str(entry["mountpoint"])
        targets[volume_uuid(mountpoint)] = mountpoint
    return targets


def _is_native_share_target(entry: dict[str, Any]) -> bool:
    """Only expose mounts below explicit NAS namespaces as write targets.

    ``df`` always includes ``/``. Treating every writable filesystem as a NAS
    volume would therefore let a fresh install create a share on the system
    partition before a data pool is mounted. The allowed namespaces mirror the
    legacy share manager and still cover ZFS datasets mounted below ``/data``
    or ``/fs`` plus conventional removable/server mounts.
    """
    if entry.get("readOnly"):
        return False
    return _native_data_mountpoint(entry.get("mountpoint"))


def _canonical_hash(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _share_uuid(volume_ref: str, name: str) -> str:
    return str(uuid_module.uuid5(_STORAGE_UUID_NAMESPACE, f"echo-share:{volume_ref}:{name}"))


def _smb_share_uuid(folder_ref: str) -> str:
    """Return the stable public identity for the usershare of one folder."""
    return str(uuid_module.uuid5(_STORAGE_UUID_NAMESPACE, f"echo-smb:{folder_ref}"))


def _target_state(path: Path) -> dict[str, Any]:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {"kind": "absent"}
    return {
        "kind": "directory" if stat_module.S_ISDIR(info.st_mode) else "other",
        "mode": info.st_mode & 0o7777,
        "uid": info.st_uid,
        "gid": info.st_gid,
    }


def _users_group_gid() -> int:
    try:
        import grp
    except ImportError as exc:  # pragma: no cover - native storage is Unix-only
        raise OSError("native shared-folder creation requires a Unix host") from exc
    try:
        return grp.getgrnam("users").gr_gid
    except KeyError:
        try:
            return grp.getgrgid(100).gr_gid
        except KeyError as exc:
            raise OSError("native shared-folder creation requires the users group") from exc


def _configure_shared_folder(path: Path, group_gid: int) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        os.fchown(descriptor, 0, group_gid)
        os.fchmod(descriptor, 0o2770)
    finally:
        os.close(descriptor)


def _verify_shared_folder(path: Path, group_gid: int) -> bool:
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return False
    try:
        info = os.fstat(descriptor)
        return (
            stat_module.S_ISDIR(info.st_mode)
            and (info.st_mode & 0o7777) == 0o2770
            and info.st_uid == 0
            and info.st_gid == group_gid
        )
    finally:
        os.close(descriptor)


def _registry_folder_entry(
    *, volume_ref: str, volume_path: str, name: str, comment: str
) -> dict[str, Any]:
    return {
        "uuid": _share_uuid(volume_ref, name),
        "name": name,
        "comment": comment,
        "relativePath": name,
        "device": next(
            (e["devicefile"] for e in filesystems() if e["mountpoint"] == volume_path),
            volume_ref,
        ),
        "volumePath": volume_path,
        "mountPointRef": volume_ref,
        "createdAt": _now(),
    }


def _public_shared_folder_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Project an internal registry entry into the public sharing contract."""
    folder_uuid = _registered_uuid(entry, strict=True)
    relative_name = _registered_relative_name(entry, strict=True)
    assert folder_uuid is not None
    assert relative_name is not None
    return {
        "uuid": folder_uuid,
        "name": str(entry.get("name") or ""),
        "comment": str(entry.get("comment") or ""),
        "relativePath": relative_name,
        "device": _public_devicefile(entry.get("device")),
        "status": "MOUNTED",
        "inUse": True,
        "supportsAcl": shutil.which("getfacl") is not None
        and shutil.which("setfacl") is not None,
    }


def _shared_folder_target_payload(volume_ref: str, volume_path: str) -> dict[str, Any]:
    """Return the public target shape without exposing the host mount path."""
    filesystem = next(
        (
            entry
            for entry in filesystems()
            if os.path.normpath(str(entry.get("mountpoint") or ""))
            == os.path.normpath(volume_path)
        ),
        {},
    )
    raw_label = filesystem.get("label")
    label = raw_label if isinstance(raw_label, str) else Path(volume_path).name
    if any(character < " " for character in label):
        label = ""
    label = label[:256]
    filesystem_uuid = _filesystem_ref(filesystem) if filesystem else None
    return {
        "mountPointRef": volume_ref,
        "filesystemUuid": filesystem_uuid,
        "label": label,
        "type": str(filesystem.get("type") or "")[:64],
        "sizeBytes": max(0, _int(filesystem.get("sizeBytes")) or 0),
        "availableBytes": max(0, _int(filesystem.get("availableBytes")) or 0),
        "readOnly": False,
    }


def _build_shared_folder_plan(desired: dict[str, Any]) -> dict[str, Any]:
    targets = _writable_targets()
    volume_ref = desired["mountPointRef"]
    volume_path = targets.get(volume_ref)
    if volume_path is None:
        raise ValueError("mountPointRef does not match any mounted writable volume")

    registry = _registry_load(strict=True)
    existing = next(
        (
            entry
            for entry in registry
            if entry.get("mountPointRef") == volume_ref and entry.get("name") == desired["name"]
        ),
        None,
    )
    target_dir = Path(volume_path) / desired["name"]
    target_state = _target_state(target_dir)
    if existing is None and target_state["kind"] != "absent":
        raise ValueError("shared folder target already exists outside the native registry")
    if existing is not None:
        if target_state["kind"] != "directory":
            raise OSError("native shared-folder registry does not match the filesystem")
        if (
            _registered_uuid(existing, strict=True) != _share_uuid(volume_ref, desired["name"])
            or _registered_relative_name(existing, strict=True) != desired["name"]
            or os.path.normpath(str(existing.get("volumePath") or ""))
            != os.path.normpath(volume_path)
        ):
            raise OSError("native shared-folder registry identity does not match the mounted target")
        existing_comment = existing.get("comment")
        if (
            not isinstance(existing_comment, str)
            or len(existing_comment) > 512
            or any(character < " " for character in existing_comment)
        ):
            raise OSError("native shared-folder registry contains an invalid comment")
    else:
        existing_comment = None

    operation = (
        "create"
        if existing is None
        else "update"
        if existing_comment != desired["comment"]
        else "none"
    )
    base_revision = _canonical_hash(
        {
            "volume": volume_ref,
            "registry": registry,
            "target": target_state,
        }
    )
    plan_id = _canonical_hash(
        {
            "schema": SHARED_FOLDER_PLAN_SCHEMA,
            "baseRevision": base_revision,
            "desired": desired,
            "operation": operation,
        }
    )
    changes = (
        [
            {"field": "name", "before": None, "after": desired["name"]},
            {"field": "comment", "before": None, "after": desired["comment"]},
        ]
        if operation == "create"
        else (
            []
            if operation == "none"
            else [
                {
                    "field": "comment",
                    "before": existing_comment,
                    "after": desired["comment"],
                }
            ]
        )
    )
    return {
        "schema": SHARED_FOLDER_PLAN_SCHEMA,
        "planId": plan_id,
        "baseRevision": base_revision,
        "operation": operation,
        "requiresApproval": operation != "none",
        "shareUuid": existing.get("uuid") if existing else _share_uuid(volume_ref, desired["name"]),
        "target": _shared_folder_target_payload(volume_ref, volume_path),
        "desired": desired,
        "changes": changes,
        "safety": {
            "filesystem": "existingMountedWritableOnly",
            "relativePath": "derivedFromPortableName",
            "directoryMode": "2770UsersGroup",
            "acl": "notManaged",
            "update": "commentOnly",
            "delete": "notManaged",
        },
        "source": "native",
    }


def plan_shared_folder(desired_state: dict[str, Any]) -> dict[str, Any]:
    """Preview creation or comment-only update on a native writable volume."""
    desired = validate_shared_folder_desired(dict(desired_state))
    with _registry_transaction():
        return _build_shared_folder_plan(desired)


def apply_shared_folder(desired_state: dict[str, Any], plan_id: str) -> dict[str, Any]:
    """Create a directory or atomically update its registry comment."""
    desired = validate_shared_folder_desired(dict(desired_state))
    with _registry_transaction():
        plan = _build_shared_folder_plan(desired)
        if plan["planId"] != plan_id:
            raise ValueError("shared folder plan is stale; preview the change again")

        group_gid = _users_group_gid()
        volume_path = _writable_targets().get(desired["mountPointRef"])
        if volume_path is None:
            raise ValueError("mountPointRef no longer matches a mounted writable volume")
        target_dir = Path(volume_path) / desired["name"]
        if plan["operation"] == "none":
            if not _verify_shared_folder(target_dir, group_gid):
                raise OSError("registered shared folder has unsafe owner, group, or mode")
            existing = next(
                entry
                for entry in _registry_load(strict=True)
                if entry.get("uuid") == plan["shareUuid"]
            )
            return {
                **plan,
                "applied": False,
                "verified": True,
                "sharedFolder": _public_shared_folder_entry(existing),
            }

        if plan["operation"] == "update":
            if not _verify_shared_folder(target_dir, group_gid):
                raise OSError("registered shared folder has unsafe owner, group, or mode")
            registry = _registry_load(strict=True)
            existing_index = next(
                (
                    index
                    for index, item in enumerate(registry)
                    if item.get("uuid") == plan["shareUuid"]
                ),
                None,
            )
            if existing_index is None:
                raise ValueError("shared folder registry changed during apply; preview again")
            existing = registry[existing_index]
            updated = {**existing, "comment": desired["comment"]}
            original_registry = list(registry)
            registry[existing_index] = updated
            try:
                _registry_save(registry)
                observed_registry = _registry_load(strict=True)
                observed = next(
                    (
                        item
                        for item in observed_registry
                        if item.get("uuid") == plan["shareUuid"]
                    ),
                    None,
                )
                if observed is None or observed.get("comment") != desired["comment"]:
                    raise OSError("shared folder comment write-back verification failed")
            except Exception as exc:
                try:
                    _registry_save(original_registry)
                    if _registry_load(strict=True) != original_registry:
                        raise OSError("shared folder registry rollback was not verified")
                except Exception as rollback_exc:
                    raise OSError(
                        "shared folder comment update failed and registry rollback also failed"
                    ) from rollback_exc
                if isinstance(exc, (OSError, ValueError)):
                    raise
                raise OSError("shared folder comment update failed") from exc
            return {
                **plan,
                "applied": True,
                "verified": True,
                "sharedFolder": _public_shared_folder_entry(updated),
            }

        created = False
        try:
            target_dir.mkdir(mode=0o2770, exist_ok=False)
            created = True
            _configure_shared_folder(target_dir, group_gid)
            if not _verify_shared_folder(target_dir, group_gid):
                raise OSError("shared folder directory was not created with root:users 2770")

            entry = _registry_folder_entry(
                volume_ref=desired["mountPointRef"],
                volume_path=volume_path,
                name=desired["name"],
                comment=desired["comment"],
            )
            registry = _registry_load(strict=True)
            registry = [item for item in registry if item.get("uuid") != entry["uuid"]]
            registry.append(entry)
            _registry_save(registry)
        except Exception:
            if created:
                try:
                    target_dir.rmdir()
                except OSError as rollback_exc:
                    raise OSError(
                        "shared folder creation failed and the empty directory could not be rolled back"
                    ) from rollback_exc
            raise
        return {
            **plan,
            "applied": True,
            "verified": True,
            "sharedFolder": _public_shared_folder_entry(entry),
        }


# ---------------------------------------------------------------------------
# Native write plane — accounts, SMB shares, quota
#
# Mirrors the shared-folder slice: a deterministic plan (operation + planId +
# requiresApproval) that can be previewed, then an apply guarded by the same
# high-risk approval + audit path. The host itself (system users/groups,
# Samba usershares, ZFS datasets) is the source of truth, so accounts need no
# parallel registry — SMB shares are read back from ``net usershare``.
# ---------------------------------------------------------------------------


def _run_write(*args: str, timeout: float = 60.0) -> None:
    """Run a mutating command; raise OSError with stderr on any failure."""
    try:
        completed = subprocess.run(
            list(args), capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise OSError(f"native command failed to start: {' '.join(args)}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip() or f"exit {completed.returncode}"
        raise OSError(f"{args[0]} failed: {detail}")


def _run_write_stdin(*args: str, input_text: str, timeout: float = 30.0) -> None:
    """Run a mutating command that reads a secret from stdin."""
    try:
        completed = subprocess.run(
            list(args),
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise OSError(f"native command failed to start: {' '.join(args)}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip() or f"exit {completed.returncode}"
        raise OSError(f"{args[0]} failed: {detail}")


def _run_read_checked(*args: str, timeout: float = 30.0) -> str:
    """Run a required read-back command and fail closed on missing/invalid state."""
    try:
        completed = subprocess.run(
            list(args), capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise OSError(f"native command failed to start: {' '.join(args)}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip() or f"exit {completed.returncode}"
        raise OSError(f"{args[0]} failed: {detail}")
    return completed.stdout or ""


def _require_posix_accounts() -> tuple[Any, Any]:
    try:
        import grp
        import pwd
    except ImportError as exc:  # pragma: no cover - Unix-only module
        raise OSError("native account management requires a Unix host") from exc
    return grp, pwd


def _group_exists(name: str) -> bool:
    grp, _ = _require_posix_accounts()
    try:
        grp.getgrnam(name)
        return True
    except KeyError:
        return False


def _user_exists(name: str) -> bool:
    _, pwd = _require_posix_accounts()
    try:
        pwd.getpwnam(name)
        return True
    except KeyError:
        return False


def _posix_user_snapshot(name: str) -> dict[str, Any] | None:
    """Read the bounded account fields needed by the native safety contract."""
    grp, pwd = _require_posix_accounts()
    try:
        account = pwd.getpwnam(name)
        groups = sorted(
            {
                entry.gr_name
                for entry in grp.getgrall()
                if name in (entry.gr_mem or ())
            }
        )
    except (KeyError, OSError):
        return None
    return {
        "name": str(account.pw_name),
        "uid": int(account.pw_uid),
        "gid": int(account.pw_gid),
        "comment": str(account.pw_gecos),
        "home": str(account.pw_dir),
        "shell": str(account.pw_shell),
        "groups": groups,
    }


def _constrained_user_snapshot(name: str) -> dict[str, Any] | None:
    """Return an existing user only when it matches the managed NAS contract."""
    snapshot = _posix_user_snapshot(name)
    if snapshot is None:
        return None
    if (
        not _is_visible_nas_user(snapshot["uid"])
        or snapshot["shell"] != "/usr/sbin/nologin"
        or not snapshot["comment"].strip()
    ):
        return None
    try:
        grp, _ = _require_posix_accounts()
        for group_name in snapshot["groups"]:
            group = grp.getgrnam(group_name)
            if not _is_visible_nas_group(group_name, int(group.gr_gid)):
                return None
        home = Path(snapshot["home"])
        if not home.is_absolute():
            return None
        home_info = home.lstat()
        if not stat_module.S_ISDIR(home_info.st_mode) or stat_module.S_ISLNK(home_info.st_mode):
            return None
        # A managed account has no SSH entry point. Reject the whole directory,
        # not only authorized_keys, so a later key filename cannot bypass this
        # contract.
        ssh_dir = home / ".ssh"
        try:
            ssh_info = ssh_dir.lstat()
        except FileNotFoundError:
            pass
        else:
            if stat_module.S_ISLNK(ssh_info.st_mode) or stat_module.S_ISDIR(ssh_info.st_mode):
                return None
            return None
    except (KeyError, OSError, ValueError, TypeError):
        return None
    return snapshot


# --- POSIX group creation -------------------------------------------------


def _build_group_plan(desired: dict[str, Any]) -> dict[str, Any]:
    operation = "none" if _group_exists(desired["name"]) else "create"
    base_revision = _canonical_hash({"group": desired["name"], "exists": operation == "none"})
    plan_id = _canonical_hash(
        {
            "schema": GROUP_PLAN_SCHEMA,
            "baseRevision": base_revision,
            "desired": desired,
            "operation": operation,
        }
    )
    changes = (
        []
        if operation == "none"
        else [
            {"field": "name", "before": None, "after": desired["name"]},
            {"field": "comment", "before": None, "after": desired["comment"]},
        ]
    )
    return {
        "schema": GROUP_PLAN_SCHEMA,
        "planId": plan_id,
        "baseRevision": base_revision,
        "operation": operation,
        "requiresApproval": operation == "create",
        "desired": {k: desired[k] for k in ("schema", "name", "comment")},
        "changes": changes,
        "safety": {
            "kind": "posixSystemGroup",
            "comment": "storedInSystemGroupDatabase",
            "delete": "notManaged",
        },
        "source": "native",
    }


def plan_group(desired_state: dict[str, Any]) -> dict[str, Any]:
    """Preview creation of a POSIX system group as a storage identity."""
    desired = validate_group_desired(dict(desired_state))
    return _build_group_plan(desired)


def apply_group(desired_state: dict[str, Any], plan_id: str) -> dict[str, Any]:
    """Create the POSIX group (``groupadd``)."""
    desired = validate_group_desired(dict(desired_state))
    plan = _build_group_plan(desired)
    if plan["planId"] != plan_id:
        raise ValueError("group plan is stale; preview the change again")
    if plan["operation"] == "none":
        if not _group_exists(desired["name"]):
            raise OSError("planned group no longer exists on the host")
        return {**plan, "applied": False, "verified": True, "group": {"name": desired["name"]}}
    _run_write("groupadd", desired["name"])
    if not _group_exists(desired["name"]):
        raise OSError("groupadd reported success but the group is missing")
    return {**plan, "applied": True, "verified": True, "group": {"name": desired["name"]}}


# --- POSIX user creation / password reset ---------------------------------


def _build_user_plan(desired: dict[str, Any]) -> dict[str, Any]:
    operation = "none" if _user_exists(desired["name"]) else "create"
    if operation == "create" and not _group_exists("users"):
        raise ValueError("native user creation requires the users group")
    base_revision = _canonical_hash({"user": desired["name"], "exists": operation == "none"})
    plan_id = _canonical_hash(
        {
            "schema": USER_PLAN_SCHEMA,
            "baseRevision": base_revision,
            "desired": desired,
            "operation": operation,
        }
    )
    changes = (
        []
        if operation == "none"
        else [
            {"field": "name", "before": None, "after": desired["name"]},
            {"field": "displayName", "before": None, "after": desired["displayName"]},
            {"field": "groups", "before": [], "after": desired["groups"]},
            {"field": "loginShell", "before": None, "after": "/usr/sbin/nologin"},
            {"field": "samba", "before": None, "after": "enabled"},
        ]
    )
    return {
        "schema": USER_PLAN_SCHEMA,
        "planId": plan_id,
        "baseRevision": base_revision,
        "operation": operation,
        "requiresApproval": operation == "create",
        # The password is never echoed back; the plan only proves one was bound.
        "desired": {
            "schema": desired["schema"],
            "name": desired["name"],
            "displayName": desired["displayName"],
            "groups": desired["groups"],
            "passwordBound": True,
        },
        "changes": changes,
        "safety": {
            "kind": "posixNormalUser",
            "loginShell": "nologin",
            "home": "createdUnderHomeRoot",
            "baseGroup": "users",
            "samba": "enabledViaSmbpasswd",
            "password": "hashedAndStoredInShadowAndSamba",
            "sshKeys": "none",
            "rollback": "notAvailableAfterAcceptedSecret",
        },
        "source": "native",
    }


def plan_user(desired_state: dict[str, Any]) -> dict[str, Any]:
    """Preview creation of a POSIX storage user (system + Samba account)."""
    desired = validate_user_desired(dict(desired_state))
    for group in desired["groups"]:
        if not _group_exists(group):
            raise ValueError(f"group '{group}' does not exist on the host")
    if not _user_exists(desired["name"]) and not _group_exists("users"):
        raise ValueError("native user creation requires the users group")
    return _build_user_plan(desired)


def _set_user_secret(name: str, password: str) -> None:
    """Set the system password and enable the Samba account atomically."""
    _run_write_stdin("chpasswd", input_text=f"{name}:{password}\n")
    _run_write_stdin("smbpasswd", "-a", "-s", name, input_text=f"{password}\n{password}\n")


def apply_user(desired_state: dict[str, Any], plan_id: str) -> dict[str, Any]:
    """Create the POSIX user, set the password, and enable Samba access."""
    desired = validate_user_desired(dict(desired_state))
    for group in desired["groups"]:
        if not _group_exists(group):
            raise ValueError(f"group '{group}' does not exist on the host")
    plan = _build_user_plan(desired)
    if plan["planId"] != plan_id:
        raise ValueError("user plan is stale; preview the change again")
    if plan["operation"] == "none":
        if not _user_exists(desired["name"]):
            raise OSError("planned user no longer exists on the host")
        return {**plan, "applied": False, "verified": True, "user": {"name": desired["name"]}}
    extra_args: list[str] = []
    extra_args += ["-G", ",".join(["users", *desired["groups"]])]
    _run_write(
        "useradd",
        "--create-home",
        "--shell", "/usr/sbin/nologin",
        "--comment", desired["displayName"],
        *extra_args,
        desired["name"],
    )
    if not _user_exists(desired["name"]):
        raise OSError("useradd reported success but the user is missing")
    try:
        observed = _constrained_user_snapshot(desired["name"])
        expected_groups = sorted({"users", *desired["groups"]})
        if (
            observed is None
            or observed["name"] != desired["name"]
            or observed["comment"] != desired["displayName"]
            or observed["shell"] != "/usr/sbin/nologin"
            or observed["groups"] != expected_groups
        ):
            raise OSError("useradd did not persist the constrained user settings")
        _set_user_secret(desired["name"], desired["password"])
        if _constrained_user_snapshot(desired["name"]) != observed:
            raise OSError("setting the user secret changed account constraints")
    except OSError:
        # Roll back the half-created account so the host is not left inconsistent.
        with contextlib.suppress(OSError):
            _run_write("userdel", desired["name"])
        raise
    return {**plan, "applied": True, "verified": True, "user": {"name": desired["name"]}}


def _build_user_password_plan(desired: dict[str, Any]) -> dict[str, Any]:
    account = _constrained_user_snapshot(desired["name"])
    if account is None:
        raise ValueError("user is not an existing constrained normal NAS user")
    base_revision = _canonical_hash({"user": account, "passwordReset": True})
    plan_id = _canonical_hash(
        {
            "schema": USER_PASSWORD_PLAN_SCHEMA,
            "baseRevision": base_revision,
            "desired": desired,
            "operation": "resetPassword",
        }
    )
    return {
        "schema": USER_PASSWORD_PLAN_SCHEMA,
        "planId": plan_id,
        "baseRevision": base_revision,
        "operation": "resetPassword",
        "requiresApproval": True,
        "desired": {
            "schema": desired["schema"],
            "name": desired["name"],
            "passwordBound": True,
        },
        "changes": [
            {
                "field": "password",
                "before": "currentCredential",
                "after": "replacementCredential",
            }
        ],
        "safety": {
            "scope": "existingConstrainedNormalUser",
            "password": "hashedAndStoredInShadowAndSamba",
            "accountFields": "preservedAndVerified",
            "loginShell": "nologin",
            "sshKeys": "none",
            "rollback": "notAvailableAfterAcceptedSecret",
        },
        "source": "native",
    }


def plan_user_password(desired_state: dict[str, Any]) -> dict[str, Any]:
    """Preview a password reset for an existing storage user."""
    desired = validate_user_password_desired(dict(desired_state))
    return _build_user_password_plan(desired)


def apply_user_password(desired_state: dict[str, Any], plan_id: str) -> dict[str, Any]:
    """Reset the system and Samba password for an existing user."""
    desired = validate_user_password_desired(dict(desired_state))
    plan = _build_user_password_plan(desired)
    if plan["planId"] != plan_id:
        raise ValueError("password plan is stale; preview the change again")
    before = _constrained_user_snapshot(desired["name"])
    if before is None:
        raise OSError("password target is no longer a constrained normal NAS user")
    _set_user_secret(desired["name"], desired["password"])
    if _constrained_user_snapshot(desired["name"]) != before:
        raise OSError("password reset changed account constraints")
    return {**plan, "applied": True, "verified": True, "user": {"name": desired["name"]}}


# --- POSIX ACL share privileges ------------------------------------------

_ACL_TO_PERMISSION = {"---": "none", "r-x": "read", "rwx": "readWrite"}
_PERMISSION_TO_ACL = {"none": "---", "read": "r-x", "readWrite": "rwx"}


def _native_folder_path(entry: dict[str, Any]) -> Path:
    """Resolve one registered folder without accepting symlinks or stale mounts."""
    volume_path = str(entry.get("volumePath") or "")
    relative_path = _registered_relative_name(entry, strict=True)
    mount_ref = str(entry.get("mountPointRef") or "")
    current_volume = _writable_targets().get(mount_ref)
    if not current_volume or os.path.normpath(current_volume) != os.path.normpath(volume_path):
        raise ValueError("shared folder volume is not mounted as a writable NAS target")
    assert relative_path is not None
    path = Path(volume_path) / relative_path
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise ValueError("shared folder directory does not exist on the host") from exc
    if not stat_module.S_ISDIR(info.st_mode) or stat_module.S_ISLNK(info.st_mode):
        raise OSError("registered shared folder is not a real directory")
    return path


def _principal_id(principal_type: str, name: str) -> int:
    grp, pwd = _require_posix_accounts()
    try:
        if principal_type == "user":
            identifier = int(pwd.getpwnam(name).pw_uid)
            visible = _is_visible_nas_user(identifier)
        else:
            account = grp.getgrnam(name)
            identifier = int(account.gr_gid)
            visible = _is_visible_nas_group(name, identifier)
    except KeyError as exc:
        raise ValueError(f"{principal_type} '{name}' does not exist on the host") from exc
    if not visible:
        raise ValueError(f"{principal_type} '{name}' is not an exposed NAS identity")
    return identifier


def _read_acl(path: Path) -> str:
    return _run_read_checked("getfacl", "--absolute-names", str(path))


def _acl_entries(
    acl_text: str, principal_type: str, principal_name: str
) -> tuple[str | None, str | None]:
    kind = "user" if principal_type == "user" else "group"
    access: str | None = None
    default: str | None = None
    for raw_line in acl_text.splitlines():
        line = raw_line.split("#effective:", 1)[0].strip()
        prefix = "default:"
        is_default = line.startswith(prefix)
        if is_default:
            line = line[len(prefix) :]
        fields = line.split(":")
        if len(fields) != 3 or fields[0] != kind or fields[1] != principal_name:
            continue
        permissions = fields[2]
        if permissions not in _ACL_TO_PERMISSION:
            raise OSError("share privilege uses an ACL shape outside the managed slice")
        if is_default:
            default = permissions
        else:
            access = permissions
    return access, default


def _acl_permission(acl_text: str, principal_type: str, principal_name: str) -> str:
    access, default = _acl_entries(acl_text, principal_type, principal_name)
    if (access is None) != (default is None):
        raise OSError(
            "share access and default ACL entries are incomplete; repair them manually first"
        )
    if access is not None and default is not None and access != default:
        raise OSError("share access and default ACL entries disagree; repair them manually first")
    value = access if access is not None else default
    return "inherit" if value is None else _ACL_TO_PERMISSION[value]


def share_privileges(share_uuid: str) -> list[dict[str, Any]]:
    entry = _resolve_shared_folder(share_uuid)
    path = _native_folder_path(entry)
    acl_text = _read_acl(path)
    overview = sharing_overview()
    privileges = [
        {
            "type": "user",
            "id": user["uid"],
            "name": user["name"],
            "permission": _acl_permission(acl_text, "user", user["name"]),
        }
        for user in overview["users"]
    ]
    privileges.extend(
        {
            "type": "group",
            "id": group["gid"],
            "name": group["name"],
            "permission": _acl_permission(acl_text, "group", group["name"]),
        }
        for group in overview["groups"]
    )
    return privileges


def _share_privilege_context(
    desired: dict[str, Any],
) -> tuple[dict[str, Any], Path, int, str]:
    entry = _resolve_shared_folder(desired["sharedFolderRef"])
    path = _native_folder_path(entry)
    identifier = _principal_id(desired["principalType"], desired["principalName"])
    acl_text = _read_acl(path)
    return entry, path, identifier, acl_text


def _build_share_privilege_plan(desired: dict[str, Any]) -> dict[str, Any]:
    entry, _path, identifier, acl_text = _share_privilege_context(desired)
    current = _acl_permission(acl_text, desired["principalType"], desired["principalName"])
    operation = "none" if current == desired["permission"] else "update"
    base_revision = _canonical_hash(
        {
            "sharedFolder": {
                "uuid": entry["uuid"],
                "name": entry["name"],
                "status": "MOUNTED",
            },
            "acl": acl_text,
        }
    )
    plan_id = _canonical_hash(
        {
            "schema": SHARE_PRIVILEGE_PLAN_SCHEMA,
            "baseRevision": base_revision,
            "desired": desired,
        }
    )
    return {
        "schema": SHARE_PRIVILEGE_PLAN_SCHEMA,
        "planId": plan_id,
        "baseRevision": base_revision,
        "operation": operation,
        "requiresApproval": operation == "update",
        "sharedFolder": {"uuid": entry["uuid"], "name": entry["name"], "status": "MOUNTED"},
        "principal": {
            "type": desired["principalType"],
            "id": identifier,
            "name": desired["principalName"],
            "before": current,
            "after": desired["permission"],
        },
        "desired": desired,
        "changes": (
            []
            if operation == "none"
            else [{"field": "permission", "before": current, "after": desired["permission"]}]
        ),
        "safety": {
            "scope": "registeredSharedFolderRootAcl",
            "principal": "existingPosixUserOrGroup",
            "filesystemAcl": "accessAndDefaultOnly",
            "recursive": "never",
            "rollback": "fullAclSnapshot",
            "delete": "notManaged",
        },
        "source": "native",
    }


def plan_share_privilege(desired_state: dict[str, Any]) -> dict[str, Any]:
    desired = validate_share_privilege_desired(dict(desired_state))
    with _registry_transaction():
        return _build_share_privilege_plan(desired)


def apply_share_privilege(desired_state: dict[str, Any], plan_id: str) -> dict[str, Any]:
    desired = validate_share_privilege_desired(dict(desired_state))
    with _registry_transaction():
        plan = _build_share_privilege_plan(desired)
        if plan["planId"] != plan_id:
            raise ValueError("share privilege plan is stale; preview the change again")
        entry, path, _identifier, original_acl = _share_privilege_context(desired)
        observed_revision = _canonical_hash(
            {
                "sharedFolder": {
                    "uuid": entry["uuid"],
                    "name": entry["name"],
                    "status": "MOUNTED",
                },
                "acl": original_acl,
            }
        )
        if observed_revision != plan["baseRevision"]:
            raise ValueError("share privilege changed during apply; preview again")
        if plan["operation"] == "none":
            return {**plan, "applied": False, "verified": True, "deployedServices": []}

        kind = "u" if desired["principalType"] == "user" else "g"
        access, default = _acl_entries(
            original_acl, desired["principalType"], desired["principalName"]
        )
        try:
            if desired["permission"] == "inherit":
                if access is not None:
                    _run_write("setfacl", "-x", f"{kind}:{desired['principalName']}", str(path))
                if default is not None:
                    _run_write(
                        "setfacl", "-x", f"d:{kind}:{desired['principalName']}", str(path)
                    )
            else:
                acl_mode = _PERMISSION_TO_ACL[desired["permission"]]
                _run_write(
                    "setfacl",
                    "-m",
                    f"{kind}:{desired['principalName']}:{acl_mode},"
                    f"d:{kind}:{desired['principalName']}:{acl_mode}",
                    str(path),
                )
            observed_acl = _read_acl(path)
            observed = _acl_permission(
                observed_acl, desired["principalType"], desired["principalName"]
            )
            if observed != desired["permission"]:
                raise OSError("setfacl did not persist the requested share privilege")
        except Exception as exc:
            try:
                _run_write_stdin("setfacl", "--restore=-", input_text=original_acl)
                if _read_acl(path) != original_acl:
                    raise OSError("ACL rollback was not verified")
            except Exception as rollback_exc:
                raise OSError(
                    "share privilege update failed and ACL rollback also failed; inspect the share"
                ) from rollback_exc
            if isinstance(exc, (OSError, ValueError)):
                raise
            raise OSError("share privilege update failed") from exc
        return {**plan, "applied": True, "verified": True, "deployedServices": []}


# --- NFS exports ----------------------------------------------------------

_NFS_EXPORT_OPTIONS = ("sync", "no_subtree_check", "root_squash", "secure")


def _nfs_uuid(folder_ref: str, client: str) -> str:
    return str(uuid_module.uuid5(_STORAGE_UUID_NAMESPACE, f"echo-nfs:{folder_ref}:{client}"))


def _nfs_rule_path(folder_ref: str) -> tuple[dict[str, Any], Path]:
    entry = _resolve_shared_folder(folder_ref)
    path = _native_folder_path(entry)
    if any(character.isspace() for character in str(path)) or (
        os.name != "nt" and "\\" in str(path)
    ):
        raise ValueError("NFS cannot publish a shared-folder path containing whitespace")
    return entry, path


def _render_nfs_exports(entries: list[dict[str, Any]]) -> str:
    lines = ["# Generated by Echo OS. Manual edits are rejected and never merged."]
    for item in sorted(entries, key=lambda value: (value["sharedFolderRef"], value["client"])):
        _folder, path = _nfs_rule_path(item["sharedFolderRef"])
        access = "ro" if item["readOnly"] else "rw"
        options = ",".join((access, *_NFS_EXPORT_OPTIONS))
        metadata = json.dumps(item, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        lines.append(f"# echo-os-rule {metadata}")
        lines.append(f"{path} {item['client']}({options})")
    return "\n".join(lines) + "\n"


def _validated_nfs_export_entries(entries: list[Any]) -> list[dict[str, Any]]:
    expected = {"uuid", "sharedFolderRef", "client", "readOnly", "comment"}
    validated: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != expected:
            raise OSError("Echo-managed NFS exports contain invalid metadata")
        try:
            normalized = validate_nfs_desired(
                {
                    "schema": "echo.omv.nfs-share-desired.v1",
                    "sharedFolderRef": entry["sharedFolderRef"],
                    "clientCidr": entry["client"],
                    "readOnly": entry["readOnly"],
                    "comment": entry["comment"],
                }
            )
            share_uuid = str(uuid_module.UUID(str(entry["uuid"])))
        except (KeyError, TypeError, ValueError) as exc:
            raise OSError("Echo-managed NFS exports contain invalid metadata") from exc
        if (
            share_uuid != entry["uuid"]
            or normalized["sharedFolderRef"] != entry["sharedFolderRef"]
            or normalized["clientCidr"] != entry["client"]
            or share_uuid != _nfs_uuid(entry["sharedFolderRef"], entry["client"])
        ):
            raise OSError("Echo-managed NFS exports contain non-canonical metadata")
        validated.append(dict(entry))
    identities = [(entry["sharedFolderRef"], entry["client"]) for entry in validated]
    if len(identities) != len(set(identities)):
        raise OSError("Echo-managed NFS exports contain duplicate rules")
    return sorted(validated, key=lambda item: (item["sharedFolderRef"], item["client"]))


def _nfs_exports_load(*, strict: bool = False) -> list[dict[str, Any]]:
    try:
        text = _read_regular_text(_NATIVE_NFS_EXPORTS, missing_ok=True)
        if text is None:
            return []
        prefix = "# echo-os-rule "
        raw_entries = [json.loads(line[len(prefix) :]) for line in text.splitlines() if line.startswith(prefix)]
        entries = _validated_nfs_export_entries(raw_entries)
        if text != _render_nfs_exports(entries):
            raise OSError("Echo-managed NFS exports contain unrecognized or modified rules")
        return entries
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        if strict:
            if isinstance(exc, OSError):
                raise
            raise OSError("Echo-managed NFS exports are unreadable") from exc
        return []


def _build_nfs_plan(desired: dict[str, Any]) -> dict[str, Any]:
    folder, path = _nfs_rule_path(desired["sharedFolderRef"])
    exports = _nfs_exports_load(strict=True)
    exports_text = _read_regular_text(_NATIVE_NFS_EXPORTS, missing_ok=True)
    existing = next(
        (
            item
            for item in exports
            if item["sharedFolderRef"] == desired["sharedFolderRef"]
            and item["client"] == desired["clientCidr"]
        ),
        None,
    )
    current = (
        None
        if existing is None
        else {"readOnly": existing["readOnly"], "comment": existing["comment"]}
    )
    wanted = {"readOnly": desired["readOnly"], "comment": desired["comment"]}
    changes = [
        {
            "field": field,
            "before": None if current is None else current[field],
            "after": after,
        }
        for field, after in wanted.items()
        if current is None or current[field] != after
    ]
    operation = "create" if existing is None else ("update" if changes else "none")
    base_revision = _canonical_hash(
        {
            "folder": {"uuid": folder["uuid"], "name": folder["name"], "path": str(path)},
            "exports": exports_text,
        }
    )
    plan_id = _canonical_hash(
        {"schema": NFS_PLAN_SCHEMA, "baseRevision": base_revision, "desired": desired}
    )
    return {
        "schema": NFS_PLAN_SCHEMA,
        "planId": plan_id,
        "baseRevision": base_revision,
        "operation": operation,
        "requiresApproval": operation != "none",
        "shareUuid": existing["uuid"] if existing else _nfs_uuid(
            desired["sharedFolderRef"], desired["clientCidr"]
        ),
        "sharedFolder": {"uuid": folder["uuid"], "name": folder["name"], "status": "MOUNTED"},
        "desired": desired,
        "changes": changes,
        "safety": {
            "clientScope": "privateCidrOnly",
            "rootSquash": "required",
            "syncWrites": "required",
            "advancedOptions": "notManaged",
            "delete": "notManaged",
        },
        "source": "native",
    }


def plan_nfs(desired_state: dict[str, Any]) -> dict[str, Any]:
    desired = validate_nfs_desired(dict(desired_state))
    with _registry_transaction():
        return _build_nfs_plan(desired)


def _verify_live_nfs(path: Path, desired: dict[str, Any]) -> None:
    output = _run_read_checked("exportfs", "-v")
    match = re.search(
        rf"{re.escape(str(path))}\s+{re.escape(desired['clientCidr'])}\(([^)]*)\)",
        output,
    )
    if match is None:
        raise OSError("NFS rule is absent from the live export table")
    options = set(match.group(1).split(","))
    required = {"ro" if desired["readOnly"] else "rw", *_NFS_EXPORT_OPTIONS}
    if not required.issubset(options):
        raise OSError("live NFS rule does not preserve the required safety options")


def apply_nfs(desired_state: dict[str, Any], plan_id: str) -> dict[str, Any]:
    desired = validate_nfs_desired(dict(desired_state))
    with _registry_transaction():
        plan = _build_nfs_plan(desired)
        if plan["planId"] != plan_id:
            raise ValueError("NFS plan is stale; preview the change again")
        _folder, path = _nfs_rule_path(desired["sharedFolderRef"])
        if plan["operation"] == "none":
            _verify_live_nfs(path, desired)
            return {**plan, "applied": False, "verified": True}

        old_exports = _read_regular_text(_NATIVE_NFS_EXPORTS, missing_ok=True)
        exports = _nfs_exports_load(strict=True)
        replacement = {
            "uuid": plan["shareUuid"],
            "sharedFolderRef": desired["sharedFolderRef"],
            "client": desired["clientCidr"],
            "readOnly": desired["readOnly"],
            "comment": desired["comment"],
        }
        wanted = [
            item
            for item in exports
            if not (
                item["sharedFolderRef"] == desired["sharedFolderRef"]
                and item["client"] == desired["clientCidr"]
            )
        ]
        wanted.append(replacement)
        wanted.sort(key=lambda item: (item["sharedFolderRef"], item["client"]))
        try:
            _atomic_text_save(_NATIVE_NFS_EXPORTS, _render_nfs_exports(wanted), mode=0o644)
            _run_write("exportfs", "-ra")
            if _nfs_exports_load(strict=True) != wanted:
                raise OSError("native NFS export write-back verification failed")
            _verify_live_nfs(path, desired)
        except Exception as exc:
            try:
                _restore_managed_text(_NATIVE_NFS_EXPORTS, old_exports, mode=0o644)
                _run_write("exportfs", "-ra")
                if _read_regular_text(_NATIVE_NFS_EXPORTS, missing_ok=True) != old_exports:
                    raise OSError("NFS exports rollback was not verified")
            except Exception as rollback_exc:
                raise OSError(
                    "NFS update failed and rollback also failed; inspect exports immediately"
                ) from rollback_exc
            if isinstance(exc, (OSError, ValueError)):
                raise
            raise OSError("NFS update failed") from exc
        return {**plan, "applied": True, "verified": True, "share": replacement}


# --- SMB usershare enable / update / remove ------------------------------


def _resolve_shared_folder(reference: str) -> dict[str, Any]:
    registry = _registry_load(strict=True)
    for entry in registry:
        if _registered_uuid(entry) == reference:
            return entry
    raise ValueError("sharedFolderRef does not match any native shared folder")


def _smb_usershare_info(name: str) -> dict[str, Any] | None:
    try:
        completed = subprocess.run(
            ["net", "usershare", "info", name],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0 or not completed.stdout.strip():
        return None
    info: dict[str, Any] = {}
    for line in completed.stdout.splitlines():
        key, value = _samba_setting(line)
        if key:
            info[key.strip().lower()] = value.strip()
    return info


def _smb_info_read_only(info: dict[str, Any]) -> bool:
    """Infer the effective usershare ACL without exposing its raw text."""
    acl = info.get("usershare_acl")
    if not isinstance(acl, str) or not acl.strip():
        return False
    permissions = re.findall(r":([A-Za-z]+)(?:,|$)", acl)
    if not permissions:
        return False
    return all(
        "r" in value.casefold()
        and not {"f", "w"}.intersection(value.casefold())
        for value in permissions
    )


def _build_smb_plan(desired: dict[str, Any]) -> dict[str, Any]:
    desired = validate_smb_desired(dict(desired))
    if desired["browseable"] is not True:
        raise ValueError("native SMB usershare discovery cannot be disabled")
    if desired["recycleBin"] is not False:
        raise ValueError("native SMB usershare does not manage a recycle bin")
    entry = _resolve_shared_folder(desired["sharedFolderRef"])
    path = _native_folder_path(entry)
    if not path.is_dir():
        raise ValueError("shared folder directory does not exist on the host")
    name = entry["name"]
    existing = _smb_usershare_info(name)

    current = (
        None
        if existing is None
        else {
            "enabled": True,
            "readOnly": _smb_info_read_only(existing),
            "browseable": True,
            "recycleBin": False,
            "comment": existing.get("comment", ""),
        }
    )
    wanted = {
        "enabled": desired["enabled"],
        "readOnly": desired["readOnly"],
        "browseable": desired["browseable"],
        "recycleBin": desired["recycleBin"],
        "comment": desired["comment"],
    }
    if existing is None:
        operation = "create" if desired["enabled"] else "none"
    elif not desired["enabled"]:
        operation = "remove"
    else:
        operation = "none" if all(
            current[field] == after for field, after in wanted.items()
        ) else "update"

    base_revision = _canonical_hash(
        {"share": name, "path": str(path), "exists": existing is not None}
    )
    plan_id = _canonical_hash(
        {
            "schema": SMB_PLAN_SCHEMA,
            "baseRevision": base_revision,
            "desired": desired,
            "operation": operation,
        }
    )
    changes = [
        {
            "field": field,
            "before": None if current is None else current[field],
            "after": after,
        }
        for field, after in wanted.items()
        if current is None or current[field] != after
    ]
    return {
        "schema": SMB_PLAN_SCHEMA,
        "planId": plan_id,
        "baseRevision": base_revision,
        "operation": operation,
        "requiresApproval": operation in ("create", "update", "remove"),
        "shareName": name,
        "shareUuid": _smb_share_uuid(desired["sharedFolderRef"]),
        "sharedFolder": {
            "uuid": entry["uuid"],
            "name": entry["name"],
            "status": "MOUNTED",
        },
        "desired": desired,
        "changes": changes,
        "safety": {
            "kind": "sambaUsershare",
            "acl": "guestDeniedByDefault",
            "recycleBin": "notManagedByUsershare",
            "browseable": "notManagedByUsershare",
        },
        "source": "native",
    }


def plan_smb(desired_state: dict[str, Any]) -> dict[str, Any]:
    """Preview enabling (or disabling) a Samba usershare for a native folder."""
    desired = validate_smb_desired(dict(desired_state))
    return _build_smb_plan(desired)


def apply_smb(desired_state: dict[str, Any], plan_id: str) -> dict[str, Any]:
    """Create, update, or remove the Samba usershare via ``net usershare``."""
    desired = validate_smb_desired(dict(desired_state))
    plan = _build_smb_plan(desired)
    if plan["planId"] != plan_id:
        raise ValueError("SMB plan is stale; preview the change again")
    name = plan["shareName"]
    entry = _resolve_shared_folder(desired["sharedFolderRef"])
    path = _native_folder_path(entry)
    operation = plan["operation"]
    if operation == "none":
        if _smb_usershare_info(name) is None and desired["enabled"]:
            raise OSError("planned SMB share is missing on the host")
        return {**plan, "applied": False, "verified": True, "share": {"name": name}}
    if operation == "remove":
        _run_write("net", "usershare", "delete", name)
        if _smb_usershare_info(name) is not None:
            raise OSError("Samba usershare remained after delete")
        return {**plan, "applied": True, "verified": True, "share": {"name": name}}
    # create or update: (re)declare the usershare. Read/write is expressed via
    # the usershare ACL (Samba has no --rw/--ro flag). The storage ``users``
    # group covers every NAS identity, so it is the natural grantee; fall back
    # to Samba's read-only ``Everyone`` default when that group is absent.
    if _group_exists("users"):
        acl = "users:r" if desired["readOnly"] else "users:f"
    else:
        acl = "Everyone:r"
    _run_write(
        "net",
        "usershare",
        "add",
        name,
        path,
        desired["comment"] or name,
        acl,
    )
    observed = _smb_usershare_info(name)
    if observed is None:
        raise OSError("Samba usershare was not registered after net usershare add")
    expected_comment = desired["comment"] or name
    if (
        observed.get("comment") != expected_comment
        or _smb_info_read_only(observed) != desired["readOnly"]
    ):
        raise OSError("Samba usershare did not persist the requested state")
    return {**plan, "applied": True, "verified": True, "share": {"name": name}}


# --- Native filesystem quota ---------------------------------------------


def _validated_zfs_dataset(value: Any) -> str:
    if (
        not isinstance(value, str)
        or len(value) > 255
        or _ZFS_DATASET_PATTERN.fullmatch(value) is None
    ):
        raise OSError("mounted ZFS dataset name is invalid")
    return value


def _quota_property(desired: dict[str, Any]) -> str:
    prefix = "userquota" if desired["subjectType"] == "user" else "groupquota"
    return f"{prefix}@{desired['subjectName']}"


def _read_zfs_quota(dataset: str, desired: dict[str, Any]) -> int:
    """Read one quota in bytes; ``none`` and an unset value mean unlimited."""
    output = _run_read_checked(
        "zfs",
        "get",
        "-H",
        "-p",
        "-o",
        "value",
        _quota_property(desired),
        dataset,
    )
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if len(lines) != 1:
        raise OSError("ZFS quota read-back returned an invalid value")
    value = lines[0]
    if value in {"none", "-"}:
        return 0
    if not value.isdigit():
        raise OSError("ZFS quota read-back returned an invalid value")
    limit = int(value)
    if limit < 0 or limit > 2**63 - 1:
        raise OSError("ZFS quota read-back is outside the supported range")
    return limit


def _validated_quota_mountpoint(value: Any) -> str:
    """Validate a kernel-reported mountpoint before passing it to quota tools."""
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 4096
        or "\x00" in value
        or not posixpath.isabs(value)
    ):
        raise OSError("mounted quota filesystem path is invalid")
    return posixpath.normpath(value)


def _quota_report_bytes(value: str) -> int:
    """Convert repquota's raw KiB value (and compatible unit suffixes) to bytes."""
    text = value.strip()
    if text.casefold() in {"", "-", "none"}:
        return 0
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*([kmgt]?)", text, re.IGNORECASE)
    if match is None:
        raise OSError("kernel quota report contains an invalid numeric value")
    try:
        number = Decimal(match.group(1))
    except (InvalidOperation, ValueError) as exc:
        raise OSError("kernel quota report contains an invalid numeric value") from exc
    multiplier = {
        "": 1024,
        "k": 1024,
        "m": 1024**2,
        "g": 1024**3,
        "t": 1024**4,
    }[match.group(2).casefold()]
    exact = number * multiplier
    if not exact.is_finite() or exact != exact.to_integral_value():
        raise OSError("kernel quota report contains a fractional byte value")
    result = int(exact)
    if result < 0 or result > 2**63 - 1:
        raise OSError("kernel quota report is outside the supported range")
    return result


def _quota_usage_text(value: int) -> str:
    for multiplier, suffix in (
        (1024**4, "TiB"),
        (1024**3, "GiB"),
        (1024**2, "MiB"),
        (1024, "KiB"),
    ):
        if value and value % multiplier == 0:
            return f"{value // multiplier} {suffix}"
    return f"{value} B"


def _read_kernel_quota(mountpoint: str, desired: dict[str, Any]) -> dict[str, Any]:
    """Read one existing user/group limit through the kernel quota report."""
    subject_type = desired["subjectType"]
    report = _run_read_checked(
        "repquota",
        "-v",
        "-O",
        "csv",
        "-u" if subject_type == "user" else "-g",
        mountpoint,
        timeout=60.0,
    )
    if len(report.encode("utf-8", errors="replace")) > _QUOTA_REPORT_MAX_BYTES:
        raise OSError("kernel quota report is too large")
    expected_header = "User" if subject_type == "user" else "Group"
    header_found = False
    matching: list[list[str]] = []
    try:
        rows = csv.reader(io.StringIO(report))
        for row in rows:
            if not row:
                continue
            if not header_found:
                if (
                    len(row) >= 11
                    and row[0].strip() == expected_header
                    and row[1].strip() in {"SpaceStatus", "BlockStatus"}
                ):
                    header_found = True
                continue
            if len(row) < 11:
                continue
            if row[0].strip() == desired["subjectName"]:
                matching.append(row)
    except csv.Error as exc:
        raise OSError("kernel quota report is not valid CSV") from exc
    if not header_found:
        raise OSError("kernel quota report has no recognized header")
    if len(matching) > 1:
        raise OSError("kernel quota subject is not unique")
    if not matching:
        return {
            "type": subject_type,
            "name": desired["subjectName"],
            "hardLimitBytes": 0,
            "used": "0 B",
        }
    row = matching[0]
    return {
        "type": subject_type,
        "name": desired["subjectName"],
        "hardLimitBytes": _quota_report_bytes(row[5]),
        "used": _quota_usage_text(_quota_report_bytes(row[3])),
    }


def _set_kernel_quota(
    mountpoint: str,
    desired: dict[str, Any],
    hard_limit_bytes: int,
) -> None:
    """Set only the block hard limit; inode and soft limits remain unlimited."""
    _run_write(
        "setquota",
        "-u" if desired["subjectType"] == "user" else "-g",
        desired["subjectName"],
        "0",
        str(hard_limit_bytes // 1024),
        "0",
        "0",
        mountpoint,
    )


def _native_quota_context(
    desired: dict[str, Any],
) -> tuple[dict[str, Any], tuple[str, str], dict[str, Any]]:
    filesystem = next(
        (entry for entry in filesystems() if _filesystem_ref(entry) == desired["filesystemUuid"]),
        None,
    )
    if filesystem is None:
        raise ValueError("filesystemUuid does not match any mounted filesystem")
    if filesystem.get("readOnly"):
        raise ValueError("quota filesystem is read-only")
    if not _native_data_mountpoint(filesystem.get("mountpoint")):
        raise ValueError("quota filesystem is outside the managed NAS data roots")
    filesystem_type = str(filesystem.get("type") or "").casefold()
    if filesystem_type == "zfs":
        if filesystem.get("supportsQuota") is False:
            raise ValueError("native quota is not enabled on this ZFS dataset")
        dataset = _validated_zfs_dataset(filesystem.get("devicefile"))
        _principal_id(desired["subjectType"], desired["subjectName"])
        current = {
            "type": desired["subjectType"],
            "name": desired["subjectName"],
            "hardLimitBytes": _read_zfs_quota(dataset, desired),
            "used": "unknown",
        }
        return filesystem, ("zfs", dataset), current
    if filesystem_type in _NATIVE_KERNEL_QUOTA_FILESYSTEMS:
        if not _native_quota_tools_available():
            raise ValueError("native kernel quota tools are not installed on this host")
        mountpoint = _validated_quota_mountpoint(filesystem.get("mountpoint"))
        if _mount_read_only(mountpoint):
            raise ValueError("quota filesystem is read-only")
        _principal_id(desired["subjectType"], desired["subjectName"])
        _kernel_quota_enabled(mountpoint, desired["subjectType"])
        return filesystem, ("kernel", mountpoint), _read_kernel_quota(mountpoint, desired)
    raise ValueError("native quota requires a ZFS dataset or ext2/3/4/XFS kernel quota")


def _public_quota_filesystem(filesystem: dict[str, Any], filesystem_ref: str) -> dict[str, Any]:
    label = filesystem.get("label")
    if not isinstance(label, str) or any(character < " " for character in label):
        label = ""
    return {
        "uuid": filesystem_ref,
        "label": label[:256],
        "type": str(filesystem.get("type") or "")[:64],
        "readOnly": bool(filesystem.get("readOnly")),
        "supportsQuota": True,
    }


def _build_quota_plan(desired: dict[str, Any]) -> dict[str, Any]:
    desired = validate_quota_desired(dict(desired))
    filesystem, backend, current = _native_quota_context(desired)
    backend_kind, backend_target = backend
    filesystem_ref = _filesystem_ref(filesystem)
    changed = current["hardLimitBytes"] != desired["hardLimitBytes"]
    base_revision = _canonical_hash(
        {
            "filesystem": {
                "uuid": filesystem_ref,
                "backend": backend_kind,
                "target": backend_target,
                "type": filesystem.get("type"),
                "readOnly": filesystem.get("readOnly", False),
                "supportsQuota": filesystem.get("supportsQuota", True),
            },
            "subject": current,
        }
    )
    plan_id = _canonical_hash(
        {
            "schema": QUOTA_PLAN_SCHEMA,
            "baseRevision": base_revision,
            "desired": desired,
        }
    )
    changes = (
        [
            {
                "field": "hardLimitBytes",
                "before": current["hardLimitBytes"],
                "after": desired["hardLimitBytes"],
            }
        ]
        if changed
        else []
    )
    return {
        "schema": QUOTA_PLAN_SCHEMA,
        "planId": plan_id,
        "baseRevision": base_revision,
        "operation": "update" if changed else "none",
        "requiresApproval": changed,
        "filesystem": _public_quota_filesystem(filesystem, filesystem_ref),
        "subject": current,
        "desired": desired,
        "changes": changes,
        "safety": {
            "scope": "filesystemUserOrGroup",
            "protocolCoverage": ["local", "SMB", "NFS"],
            "sharedFolderQuota": "notSupportedByOmvQuotaRpc",
            "adapter": "zfs-userquota" if backend_kind == "zfs" else "kernel-setquota",
            "minimumUnitBytes": 1024,
        },
        "source": "native",
    }


def plan_quota(desired_state: dict[str, Any]) -> dict[str, Any]:
    """Preview a user/group quota on a mounted ZFS or kernel-quota filesystem."""
    desired = validate_quota_desired(dict(desired_state))
    return _build_quota_plan(desired)


def apply_quota(desired_state: dict[str, Any], plan_id: str) -> dict[str, Any]:
    """Apply a user/group quota through the filesystem's native quota adapter."""
    desired = validate_quota_desired(dict(desired_state))
    plan = _build_quota_plan(desired)
    if plan["planId"] != plan_id:
        raise ValueError("quota plan is stale; preview the change again")
    if plan["operation"] == "none":
        return {**plan, "applied": False, "verified": True}

    _filesystem, backend, current = _native_quota_context(desired)
    backend_kind, backend_target = backend
    original_limit = current["hardLimitBytes"]
    failure_label = "ZFS quota" if backend_kind == "zfs" else "kernel quota"
    try:
        if backend_kind == "zfs":
            subject = _quota_property(desired)
            limit = "none" if desired["hardLimitBytes"] == 0 else str(desired["hardLimitBytes"])
            _run_write("zfs", "set", f"{subject}={limit}", backend_target)
            observed_limit = _read_zfs_quota(backend_target, desired)
            verification_error = "ZFS quota write-back verification failed"
        else:
            _set_kernel_quota(backend_target, desired, desired["hardLimitBytes"])
            observed_limit = _read_kernel_quota(backend_target, desired)["hardLimitBytes"]
            verification_error = "kernel quota write-back verification failed"
        if observed_limit != desired["hardLimitBytes"]:
            raise OSError(verification_error)
    except Exception as exc:
        try:
            if backend_kind == "zfs":
                rollback = "none" if original_limit == 0 else str(original_limit)
                _run_write("zfs", "set", f"{subject}={rollback}", backend_target)
                restored_limit = _read_zfs_quota(backend_target, desired)
                rollback_error = "ZFS quota rollback was not verified"
            else:
                _set_kernel_quota(backend_target, desired, original_limit)
                restored_limit = _read_kernel_quota(backend_target, desired)["hardLimitBytes"]
                rollback_error = "kernel quota rollback was not verified"
            if restored_limit != original_limit:
                raise OSError(rollback_error)
        except Exception as rollback_exc:
            raise OSError(
                f"{failure_label} update failed and rollback also failed; inspect the filesystem immediately"
            ) from rollback_exc
        if isinstance(exc, (OSError, ValueError)):
            raise
        raise OSError(f"{failure_label} update failed") from exc
    return {
        **plan,
        "applied": True,
        "verified": True,
        "quota": {
            "filesystemUuid": desired["filesystemUuid"],
            "subjectType": desired["subjectType"],
            "subjectName": desired["subjectName"],
            "hardLimitBytes": desired["hardLimitBytes"],
        },
    }


class NativeStorageAuthority:
    """Storage-provider surface used by the account and data-access layers.

    The active native provider is backed by the host itself (system users,
    mounts, and Samba usershares). It keeps the same duck-typed read contract
    as the optional OMV compatibility provider without importing that client.
    """

    configured = True
    admin_url = None

    def ping(self) -> bool:
        return bool(block_devices())

    def sharing_overview(self) -> dict[str, Any]:
        return sharing_overview()

    def share_privileges(self, share_uuid: str) -> list[dict[str, Any]]:
        return share_privileges(share_uuid)


def validated_devicefile(devicefile: str) -> str:
    """Validate a device path before it reaches any subprocess."""
    try:
        return validate_devicefile(devicefile)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc


__all__ = [
    "NativeStorageAuthority",
    "apply_group",
    "apply_nfs",
    "apply_quota",
    "apply_share_privilege",
    "apply_shared_folder",
    "apply_smb",
    "apply_user",
    "apply_user_password",
    "block_devices",
    "filesystems",
    "md_arrays",
    "plan_group",
    "plan_nfs",
    "plan_quota",
    "plan_share_privilege",
    "plan_shared_folder",
    "plan_smb",
    "plan_user",
    "plan_user_password",
    "sharing_overview",
    "share_privileges",
    "smart_devices",
    "smart_report",
    "status",
    "storage_health",
    "storage_topology",
    "validated_devicefile",
    "volume_uuid",
    "zfs_pools",
]
