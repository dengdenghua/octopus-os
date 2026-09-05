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
only owns one generated file below ``/etc/exports.d``. Pool writes are limited
to separately reviewed two-blank-disk ZFS and md RAID1 creators, Echo-layout
export/import, and one-failed-member blank-disk replacement for either mirror. Pool
scrub start is exposed with read-back maintenance state. Pool deletion,
expansion, general replacement, recursive permission changes,
signature wiping, and arbitrary protocol options remain outside this module.
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

from appliance.mdraid_check_schedule_policy import (
    scheduler_installed as _mdraid_scheduler_installed,
)
from appliance.native_btrfs import apply_btrfs_raid1, btrfs_raid1_candidates, plan_btrfs_raid1
from appliance.native_btrfs_health import probe_btrfs_filesystems as _probe_btrfs_filesystems
from appliance.native_ext4 import apply_ext4_volume, ext4_volume_candidates, plan_ext4_volume
from appliance.native_mdraid import apply_mdraid1, mdraid1_candidates, plan_mdraid1
from appliance.native_mdraid_check import (
    apply_mdraid_check,
    mdraid_maintenance,
    plan_mdraid_check,
)
from appliance.native_mdraid_replace import (
    apply_mdraid1_replace,
    mdraid1_replacement_candidates,
    plan_mdraid1_replace,
)
from appliance.native_storage_pool import (
    apply_zfs_mirror,
    apply_zfs_mirror_replace,
    apply_zfs_pool_import,
    apply_zfs_scrub,
    importable_zfs_pools,
    plan_zfs_mirror,
    plan_zfs_mirror_replace,
    plan_zfs_pool_import,
    plan_zfs_scrub,
    zfs_mirror_candidates,
    zfs_mirror_replacement_candidates,
    zfs_pool_maintenance,
    zfs_scan_snapshot,
)
from appliance.native_storage_pool import (
    apply_zfs_pool_export as _apply_zfs_pool_export,
)
from appliance.native_storage_pool import (
    exportable_zfs_pools as _exportable_zfs_pools,
)
from appliance.native_storage_pool import (
    plan_zfs_pool_export as _plan_zfs_pool_export,
)
from appliance.native_storage_probe import (
    Probe,
    evidence,
    health_observation,
    observation,
    parsed,
    run_readonly,
)
from appliance.omv_protocol import (
    GROUP_PLAN_SCHEMA,
    NFS_PLAN_SCHEMA,
    NFS_REMOVE_PLAN_SCHEMA,
    QUOTA_PLAN_SCHEMA,
    SHARE_PRIVILEGE_PLAN_SCHEMA,
    SHARED_FOLDER_DELETE_CONTROL_CAPABILITY,
    SHARED_FOLDER_DELETE_PLAN_SCHEMA,
    SHARED_FOLDER_DESIRED_SCHEMA,
    SHARED_FOLDER_DETACH_PLAN_SCHEMA,
    SHARED_FOLDER_RENAME_CONTROL_CAPABILITY,
    SHARED_FOLDER_RENAME_PLAN_SCHEMA,
    SMB_PLAN_SCHEMA,
    USER_PASSWORD_PLAN_SCHEMA,
    USER_PLAN_SCHEMA,
    validate_devicefile,
    validate_group_desired,
    validate_nfs_desired,
    validate_nfs_remove_desired,
    validate_quota_desired,
    validate_share_privilege_desired,
    validate_shared_folder_delete_desired,
    validate_shared_folder_desired,
    validate_shared_folder_detach_desired,
    validate_shared_folder_rename_desired,
    validate_smb_desired,
    validate_user_desired,
    validate_user_password_desired,
    validate_zfs_pool_export_desired,
)

SCHEMA_VERSION = 1

SHARED_FOLDER_PLAN_SCHEMA = "echo.omv.shared-folder-plan.v1"
_NATIVE_SHARE_REGISTRY = Path("/var/lib/echo-os/native-shared-folders.json")
_NATIVE_NFS_EXPORTS = Path("/etc/exports.d/echo-os.exports")
_NATIVE_ZFS_MOUNT_ROOT = Path("/data")
_STORAGE_UUID_NAMESPACE = uuid_module.UUID("6f1e0d5c-3a34-4f5e-9a52-1f65c3b07a11")
_REGISTRY_THREAD_LOCK = threading.RLock()
_NATIVE_DATA_MOUNT_ROOTS = ("/data", "/mnt", "/srv", "/fs", "/volume")
_ZFS_DATASET_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]*(?:/[A-Za-z0-9][A-Za-z0-9._:-]*)*")
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
    """Return read-only stdout with failure evidence retained on the string."""
    return run_readonly(args, timeout=timeout)


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


def _native_mdraid_check_scheduler_available() -> bool:
    return _mdraid_scheduler_installed()


def _native_nut_usb_driver_available() -> bool:
    return any(
        (root / driver).is_file()
        for root in (Path("/usr/lib/nut"), Path("/lib/nut"))
        for driver in (
            "usbhid-ups",
            "blazer_usb",
            "nutdrv_qx",
            "bcmxcp_usb",
            "richcomm_usb",
            "tripplite_usb",
        )
    )


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
            token in {"quota", "usrquota", "uquota"} or token.startswith("usrjquota=")
            for token in tokens
        )
    else:
        enabled = any(
            token in {"quota", "grpquota", "gquota"} or token.startswith("grpjquota=")
            for token in tokens
        )
    if not enabled:
        raise ValueError(f"kernel {subject_type} quotas are not enabled on this filesystem")
    return True


class _SmartPayload(dict[str, Any]):
    probe_evidence: dict[str, Any]


def _run_json(*args: str, timeout: float = 25.0) -> dict[str, Any]:
    """Preserve usable SMART JSON and distinguish exit-status health bits."""
    output = run_readonly(args, timeout=timeout, smart=True)
    probe_evidence = evidence("smart", _now(), output, target=args[-1])
    try:
        payload = json.loads(output or getattr(output, "partial_stdout", ""))
    except (json.JSONDecodeError, ValueError):
        payload = None
    if not isinstance(payload, dict):
        parsed(probe_evidence, 0, invalid=True)
        payload = {}
    result = _SmartPayload(payload)
    result.probe_evidence = probe_evidence
    return result


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _percent(used: int | None, total: int | None) -> int | None:
    if used is None or not total or total <= 0:
        return None
    return max(0, min(100, int(round(used * 100.0 / total))))


# --------------------------------------------------------------------------- #
# Devices / topology
# --------------------------------------------------------------------------- #


def block_devices() -> list[dict[str, Any]]:
    """Enumerate physical disks via ``lsblk`` (kernel is the authority)."""
    return _probe_block_devices().value


def _probe_block_devices() -> Probe:
    out = _run(
        "lsblk",
        "-J",
        "-b",
        "-o",
        "NAME,SIZE,TYPE,MOUNTPOINT,FSTYPE,MODEL,SERIAL,ROTA,PKNAME",
    )
    probe_evidence = evidence("block-devices", _now(), out)
    try:
        payload = json.loads(out or getattr(out, "partial_stdout", ""))
    except json.JSONDecodeError:
        payload = None
    if not isinstance(payload, dict) or not isinstance(payload.get("blockdevices"), list):
        parsed(probe_evidence, 0, invalid=True)
        return Probe([], probe_evidence)

    devices: list[dict[str, Any]] = []
    invalid = False
    has_btrfs = False
    has_md = False
    has_zfs = False

    def walk(node: Any) -> None:
        nonlocal invalid, has_btrfs, has_md, has_zfs
        if not isinstance(node, dict):
            invalid = True
            return
        name = node.get("name")
        node_type = node.get("type")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.!+-]+", name):
            invalid = True
            return
        if not isinstance(node_type, str) or _int(node.get("size")) is None:
            invalid = True
            return
        has_md = has_md or node_type == "md" or node_type.startswith("raid")
        filesystem_type = node.get("fstype")
        if filesystem_type is not None and not isinstance(filesystem_type, str):
            invalid = True
            filesystem_type = None
        has_zfs = has_zfs or filesystem_type in {"zfs", "zfs_member"}
        has_btrfs = has_btrfs or filesystem_type == "btrfs"
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
                    "filesystemType": filesystem_type or None,
                    "rotational": None
                    if node.get("rota") is None
                    else bool(node.get("rota") == "1" or node.get("rota") is True),
                    "model": str(node.get("model") or "").strip() or None,
                    "serial": str(node.get("serial") or "").strip() or None,
                    "parentDevicefiles": [],
                }
            )
        children = node.get("children") or []
        if not isinstance(children, list):
            invalid = True
            return
        for child in children:
            walk(child)

    for node in payload.get("blockdevices") or []:
        walk(node)
    parsed(probe_evidence, len(devices), invalid=invalid)
    probe_evidence["mdraidPresent"] = has_md
    probe_evidence["zfsPresent"] = has_zfs
    probe_evidence["btrfsPresent"] = has_btrfs
    return Probe(devices, probe_evidence)


def md_arrays() -> list[dict[str, Any]]:
    """Parse ``/proc/mdstat`` into the OMV RAID array shape."""
    return _probe_md_arrays().value


def _probe_md_arrays(*, expected: bool = False) -> Probe:
    probe_evidence = evidence("mdraid", _now(), required=expected)
    try:
        with open("/proc/mdstat", encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except FileNotFoundError:
        probe_evidence.update(
            state="unavailable" if expected else "not-applicable", code="not_present"
        )
        return Probe([], probe_evidence)
    except OSError as exc:
        probe_evidence.update(
            state="error",
            required=True,
            code="permission_denied" if isinstance(exc, PermissionError) else "read_failed",
        )
        return Probe([], probe_evidence)

    arrays: list[dict[str, Any]] = []
    invalid = not bool(re.search(r"^Personalities\s*:", text, re.MULTILINE))
    for match in re.finditer(r"^(md\S+)\s*:\s*(.*(?:\n[ \t]+.*)*)", text, re.MULTILINE):
        name, body = match.groups()
        level = "unknown"
        for candidate in ("raid0", "raid10", "raid1", "raid5", "raid6", "linear"):
            if candidate in body:
                level = candidate
                break
        status = "unknown"
        for token, mapped in _MD_STATE_MAP.items():
            if re.search(rf"\b{re.escape(token)}\b", body):
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
        progress_match = re.search(r"=\s*(\d+(?:\.\d+)?)%", body)
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
        invalid = invalid or status == "unknown"
    parsed(probe_evidence, len(arrays), invalid=invalid)
    if arrays or invalid or expected:
        probe_evidence["required"] = True
    if not arrays and not invalid and not expected:
        probe_evidence.update(state="not-applicable", code="not_present")
    return Probe(arrays, probe_evidence)


def zfs_pools() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (arrays, alerts) derived from ``zpool list``/``zpool status``."""
    return _probe_zfs_pools().value


def _probe_zfs_pools(*, expected: bool = False) -> Probe:
    out = _run("zpool", "list", "-H", "-p", "-o", "name,size,alloc,free,health,cap,frag")
    probe_evidence = evidence("zfs", _now(), out, required=expected)
    raw_output = out or getattr(out, "partial_stdout", "")
    if probe_evidence["state"] != "ok" and not raw_output:
        if probe_evidence.get("code") == "tool_missing" and not expected:
            probe_evidence["state"] = "not-applicable"
        else:
            probe_evidence["required"] = True
        return Probe(([], []), probe_evidence)
    out = raw_output
    if not out:
        probe_evidence.update(
            state="empty" if expected else "not-applicable",
            code="empty_inventory" if expected else "not_present",
        )
        return Probe(([], []), probe_evidence)
    if out.strip() == "no pools available" and not expected:
        probe_evidence.update(state="not-applicable", code="not_present")
        return Probe(([], []), probe_evidence)

    arrays: list[dict[str, Any]] = []
    alerts: list[dict[str, Any]] = []
    invalid = False
    for line in out.strip().splitlines():
        parts = line.split("\t")
        if len(parts) < 5:
            invalid = True
            continue
        name, size, _alloc, _free, health = parts[0], parts[1], parts[2], parts[3], parts[4]
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]*", name) or _int(size) is None:
            invalid = True
            continue
        detail = _run("zpool", "status", name, timeout=10.0)
        if getattr(detail, "state", "ok") != "ok" and probe_evidence["state"] == "ok":
            probe_evidence.update(state="partial", code=getattr(detail, "code", "read_failed"))
        elif not re.search(r"^\s*state:\s*\S+", detail, re.MULTILINE):
            invalid = True
        detail_state = re.search(r"^\s*state:\s*(\S+)", detail, re.MULTILINE)
        if detail_state and detail_state.group(1) != health:
            current_health = detail_state.group(1)
            # Inventory and detail are not an atomic snapshot. Preserve a known
            # fault from either read; a later positive read cannot erase it.
            severity = {"healthy": 0, "warning": 1, "degraded": 2, "critical": 3}
            if severity.get(_ZFS_HEALTH_TO_STATE.get(current_health, "unknown"), -1) > severity.get(
                _ZFS_HEALTH_TO_STATE.get(health, "unknown"), -1
            ):
                health = current_health
            if probe_evidence["state"] == "ok":
                probe_evidence.update(state="partial", code="conflicting_health")
        level = _zfs_level(name, detail)
        status = _ZFS_HEALTH_TO_STATE.get(health, "unknown")
        operation, operation_percent = _zfs_maintenance_operation(name, detail)
        arrays.append(
            {
                "devicefile": name,
                "level": level,
                "status": status,
                "totalDevices": None,
                "activeDevices": None,
                "operation": operation,
                "operationPercent": operation_percent,
                "sizeBytes": _int(size),
                "health": health,
                "kind": "zfs",
            }
        )
        invalid = invalid or status == "unknown"
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
    probe_evidence["required"] = True
    parsed(probe_evidence, len(arrays), invalid=invalid)
    return Probe((arrays, alerts), probe_evidence)


def _zfs_level(pool: str, status: str | None = None) -> str:
    if status is None:
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


def _zfs_maintenance_operation(
    pool: str, status: str | None = None
) -> tuple[str | None, int | None]:
    if status is None:
        status = _run("zpool", "status", pool, timeout=10.0)
    try:
        scan = zfs_scan_snapshot(status)
    except OSError:
        return None, None
    if scan["state"] != "inProgress" or scan["kind"] not in {"scrub", "resilver"}:
        return None, None
    progress = scan["progressPercent"]
    return scan["kind"], int(progress) if progress is not None else None


def storage_topology() -> dict[str, Any]:
    """Devices + arrays (mdraid, Btrfs, and ZFS) in the OMV topology shape."""
    device_probe = _probe_block_devices()
    md_probe = _probe_md_arrays(expected=device_probe.evidence.get("mdraidPresent", False))
    zfs_probe = _probe_zfs_pools(expected=device_probe.evidence.get("zfsPresent", False))
    zfs, _alerts = zfs_probe.value
    btrfs_probe = _probe_btrfs_filesystems(
        expected=device_probe.evidence.get("btrfsPresent", False),
        runner=_run,
        checked_at=_now(),
    )
    return {
        "devices": device_probe.value,
        "arrays": [*md_probe.value, *btrfs_probe.value, *zfs],
        "readOnly": True,
        **observation(
            [device_probe.evidence, md_probe.evidence, btrfs_probe.evidence, zfs_probe.evidence]
        ),
    }


# --------------------------------------------------------------------------- #
# Filesystems
# --------------------------------------------------------------------------- #


def filesystems() -> list[dict[str, Any]]:
    """Mounted filesystems with capacity, in the OMV filesystem shape."""
    return _probe_filesystems().value


def _probe_filesystems() -> Probe:
    out = _run("df", "-B1", "-P", "-T")
    probe_evidence = evidence("filesystems", _now(), out)
    out = out or getattr(out, "partial_stdout", "")
    if not out:
        parsed(probe_evidence, 0, invalid=True)
        return Probe([], probe_evidence)
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    quota_tools_available = _native_quota_tools_available()
    invalid = not out.splitlines()[0].startswith("Filesystem")
    for line in out.strip().splitlines()[1:]:
        parts = line.split()
        if len(parts) < 7:
            invalid = True
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
        used_i = _int(used)
        available_i = _int(available)
        if size_i is None or size_i <= 0 or available_i is None or used_i is None or used_i < 0:
            invalid = True
            continue
        if shutil.which("findmnt") is None:
            mount_known = False
            read_only = False
            if probe_evidence["state"] == "ok":
                probe_evidence.update(state="partial", code="tool_missing")
        else:
            mount_options = _run("findmnt", "-n", "-o", "OPTIONS", "-T", mountpoint, timeout=10.0)
            mount_tokens = {
                item.strip().casefold() for item in mount_options.split(",") if item.strip()
            }
            mount_known = getattr(mount_options, "state", "ok") == "ok" and bool(
                mount_tokens & {"ro", "rw"}
            )
            read_only = "ro" in mount_tokens and "rw" not in mount_tokens
            if not mount_known and probe_evidence["state"] == "ok":
                probe_evidence.update(
                    state="partial", code=getattr(mount_options, "code", None) or "parse_failed"
                )
        supports_quota = _native_data_mountpoint(mountpoint) and (
            fstype == "zfs" or fstype in _NATIVE_KERNEL_QUOTA_FILESYSTEMS and quota_tools_available
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
                "usedPercent": _percent(used_i, size_i),
                "readOnly": read_only,
                "mountOptionsKnown": mount_known,
                "supportsAcl": False,
                # ZFS uses native userquota/groupquota properties. ext2/3/4
                # and XFS use the standard kernel quota tools when installed;
                # the plan path still verifies that quotas are enabled.
                "supportsQuota": supports_quota,
            }
        )
    parsed(probe_evidence, len(entries), invalid=invalid)
    return Probe(entries, probe_evidence)


# --------------------------------------------------------------------------- #
# SMART
# --------------------------------------------------------------------------- #


def _smart_json(devicefile: str, extra: tuple[str, ...] = ("-H",)) -> dict[str, Any]:
    return _run_json("smartctl", "-j", *extra, devicefile)


def smart_report(devicefile: str) -> dict[str, Any]:
    """Full SMART report for one device (OVM ``OmvSmart`` shape)."""
    return _probe_smart_device(devicefile, extra=("-H", "-A", "-i")).value


def _probe_smart_device(
    devicefile: str, *, size_bytes: int | None = None, extra: tuple[str, ...] = ("-H", "-i")
) -> Probe:
    payload = _smart_json(devicefile, extra)
    probe_evidence = dict(
        getattr(payload, "probe_evidence", evidence("smart", _now(), target=devicefile))
    )
    # Malformed nested JSON is an observation failure, never an endpoint 500.
    nested_names = ("smart_status", "smart_support", "temperature", "power_on_time", "smartctl")
    invalid = any(name in payload and not isinstance(payload[name], dict) for name in nested_names)

    def field(name: str, key: str) -> Any:
        value = payload.get(name)
        return value.get(key) if isinstance(value, dict) else None

    passed = field("smart_status", "passed")
    exit_code = probe_evidence.get("exitCode", 0)
    # Negative subprocess codes mean signals, not smartctl health bits.
    exit_code = exit_code if isinstance(exit_code, int) and 0 <= exit_code <= 255 else 0
    # Even when -H passes, bits 4-7 convey threshold/log evidence worth showing.
    health = (
        "FAILED"
        if passed is False or exit_code & 24
        else "WARNING"
        if exit_code & 224
        else "PASSED"
        if passed is True
        else "UNKNOWN"
    )
    if probe_evidence["state"] in {"error", "unavailable"}:
        # A failed command cannot provide a fresh positive health conclusion.
        if health == "PASSED":
            health = "UNKNOWN"
    elif invalid:
        probe_evidence.update(
            state="partial" if passed in (True, False) else "error", code="parse_failed"
        )
    elif passed is not True and passed is not False and not exit_code & 248:
        probe_evidence.update(
            state="unavailable",
            code="unsupported"
            if field("smart_support", "available") is False
            else "health_unreported",
        )
    probe_evidence["count"] = int(
        health != "UNKNOWN" or field("temperature", "current") is not None
    )
    result = {
        "devicefile": devicefile,
        "model": payload.get("model_name") or None,
        "health": health,
        "temperatureC": _int(field("temperature", "current")),
        "powerOnHours": _int(field("power_on_time", "hours")),
        "powerCycles": _int(payload.get("power_cycle_count") or 0) or None,
        **observation([probe_evidence]),
    }
    if size_bytes is not None:
        result["sizeBytes"] = size_bytes
    return Probe(result, probe_evidence)


def smart_devices() -> list[dict[str, Any]]:
    """Quick SMART health for every physical disk (parallel, bounded)."""
    return [probe.value for probe in _probe_smart_devices(block_devices())]


def _probe_smart_devices(known: list[dict[str, Any]]) -> list[Probe]:
    def probe(device: dict[str, Any]) -> Probe:
        return _probe_smart_device(device["devicefile"], size_bytes=device.get("sizeBytes"))

    if not known:
        return []
    with ThreadPoolExecutor(max_workers=8) as pool:
        return list(pool.map(probe, known))


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


def _native_health_alert(
    *, code: str, severity: str, resource: str, message: str, at: str
) -> dict[str, Any]:
    """Build a deterministic alert without claiming a failed probe is healthy."""
    alert_id = hashlib.sha256(f"{code}\0{resource}".encode()).hexdigest()[:24]
    return {
        "id": alert_id,
        "code": code,
        "severity": severity,
        "resource": resource,
        "message": message,
        "firstSeenAt": at,
        "lastSeenAt": at,
        "occurrences": 1,
    }


def storage_health() -> dict[str, Any]:
    """Aggregate current observations without inventing a successful monitor run."""
    checked_at = _now()
    filesystem_probe = _probe_filesystems()
    device_probe = _probe_block_devices()
    fs_entries = filesystem_probe.value
    zfs_probe = _probe_zfs_pools(
        expected=(
            device_probe.evidence.get("zfsPresent", False)
            or any(item.get("type") == "zfs" for item in fs_entries)
        )
    )
    zfs, zfs_alerts = zfs_probe.value
    md_probe = _probe_md_arrays(expected=device_probe.evidence.get("mdraidPresent", False))
    arrays = md_probe.value
    btrfs_probe = _probe_btrfs_filesystems(
        expected=(
            device_probe.evidence.get("btrfsPresent", False)
            or any(item.get("type") == "btrfs" for item in fs_entries)
        ),
        runner=_run,
        checked_at=checked_at,
    )
    btrfs = btrfs_probe.value
    smart_probes = _probe_smart_devices(device_probe.value)
    probes = [
        device_probe.evidence,
        filesystem_probe.evidence,
        md_probe.evidence,
        btrfs_probe.evidence,
        zfs_probe.evidence,
        *(probe.evidence for probe in smart_probes),
    ]
    alerts = [*zfs_alerts, *_usage_alerts(fs_entries)]
    for array in arrays:
        if array.get("status") in {"degraded", "critical", "inactive"}:
            alerts.append(
                _native_health_alert(
                    code="raid.degraded",
                    severity="critical" if array["status"] == "critical" else "warning",
                    resource=array["devicefile"],
                    message=f"软阵列 {array['devicefile']} 未处于健康状态",
                    at=checked_at,
                )
            )
    for filesystem in btrfs:
        if filesystem["status"] in {"degraded", "critical", "warning"}:
            if filesystem["status"] == "degraded":
                code = "btrfs.device.missing"
                message = f"Btrfs 卷 {filesystem['mountpoint']} 缺少成员设备"
            elif filesystem["readOnly"]:
                code = "btrfs.mount.read-only"
                message = f"Btrfs 卷 {filesystem['mountpoint']} 当前为只读挂载"
            else:
                code = "btrfs.device.errors"
                message = (
                    f"Btrfs 卷 {filesystem['mountpoint']} 累计设备或校验错误 "
                    f"{filesystem['deviceErrorCount']} 次"
                )
            alerts.append(
                _native_health_alert(
                    code=code,
                    severity="critical" if filesystem["status"] == "critical" else "warning",
                    resource=filesystem["mountpoint"],
                    message=message,
                    at=checked_at,
                )
            )
    for probe in smart_probes:
        device = probe.value
        if device["health"] in {"FAILED", "WARNING"}:
            failed = device["health"] == "FAILED"
            alerts.append(
                _native_health_alert(
                    code="smart.failed" if failed else "smart.history",
                    severity="critical" if failed else "warning",
                    resource=device["devicefile"],
                    message=(
                        f"{device['devicefile']} SMART 报告当前健康或阈值异常，请检查并保护数据"
                        if failed
                        else f"{device['devicefile']} SMART 报告历史阈值、错误或自检记录，请检查详情"
                    ),
                    at=checked_at,
                )
            )
    return {
        "schemaVersion": SCHEMA_VERSION,
        **health_observation(probes, alerts, checked_at=checked_at),
        "readOnly": True,
        "source": "native",
        "pools": len(zfs) + len(btrfs),
        "arrays": len(arrays) + len(btrfs) + len(zfs),
        "filesystems": len(fs_entries),
        "devices": len(device_probe.value),
    }


# Write slices the native plane can actually perform on this host.
_NATIVE_WRITE_CAPABILITIES = (
    "shared-folder.create.simple.v1",
    SHARED_FOLDER_RENAME_CONTROL_CAPABILITY,
    "shared-folder.detach.safe.v1",
    SHARED_FOLDER_DELETE_CONTROL_CAPABILITY,
    "account.group.create.v1",
    "account.user.create.v1",
    "account.user.password.reset.v1",
    "shared-folder.privilege.simple.v1",
    "smb.share.desired.v1",
    "nfs.share.private-network.v1",
    "nfs.share.remove.safe.v1",
    "filesystem.quota.user-group.v1",
    "storage.pool.zfs-mirror.create.v1",
    "storage.array.mdraid1.create.v1",
    "storage.array.mdraid1.replace-failed.blank.v1",
    "storage.array.mdraid.check.start.v1",
    "storage.array.mdraid.check.schedule.v1",
    "storage.volume.ext4.create-mount.v1",
    "storage.volume.btrfs-raid1.create-mount.v1",
    "storage.pool.zfs-mirror.replace.blank.v1",
    "storage.pool.zfs.export.safe.v1",
    "storage.pool.zfs.import.echo-root.v1",
    "storage.pool.zfs.scrub.start.v1",
    "power.ups-shutdown-policy.v1",
    "power.ups.local-usb.configure.v1",
    "storage.smart.self-test.start.v1",
    "storage.smart.self-test.schedule.v1",
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
        unavailable.add("nfs.share.remove.safe.v1")
    if not (_native_command_tools_available("zfs") or _native_quota_tools_available()):
        unavailable.add("filesystem.quota.user-group.v1")
    if not _native_command_tools_available("zpool", "zfs", "lsblk", "wipefs"):
        unavailable.add("storage.pool.zfs-mirror.create.v1")
        unavailable.add("storage.pool.zfs-mirror.replace.blank.v1")
    if not _native_command_tools_available("mdadm", "lsblk", "wipefs", "update-initramfs"):
        unavailable.add("storage.array.mdraid1.create.v1")
    if not _native_command_tools_available("mdadm", "lsblk", "wipefs"):
        unavailable.add("storage.array.mdraid1.replace-failed.blank.v1")
    if not _native_command_tools_available("mdadm", "lsblk"):
        unavailable.add("storage.array.mdraid.check.start.v1")
    if (
        not _native_command_tools_available("mdadm", "lsblk", "systemctl")
        or not _native_mdraid_check_scheduler_available()
    ):
        unavailable.add("storage.array.mdraid.check.schedule.v1")
    if not _native_command_tools_available(
        "mdadm",
        "blkid",
        "findmnt",
        "mkfs.ext4",
        "mount",
        "systemctl",
        "umount",
        "wipefs",
    ):
        unavailable.add("storage.volume.ext4.create-mount.v1")
    if not _native_command_tools_available(
        "blkid",
        "btrfs",
        "findmnt",
        "lsblk",
        "mkfs.btrfs",
        "mount",
        "systemctl",
        "umount",
        "wipefs",
    ):
        unavailable.add("storage.volume.btrfs-raid1.create-mount.v1")
    if not _native_command_tools_available("zpool", "zfs"):
        unavailable.add("storage.pool.zfs.export.safe.v1")
        unavailable.add("storage.pool.zfs.import.echo-root.v1")
        unavailable.add("storage.pool.zfs.scrub.start.v1")
    if not _native_command_tools_available("upsc", "systemctl"):
        unavailable.add("power.ups-shutdown-policy.v1")
    if (
        not _native_command_tools_available("upsc", "systemctl")
        or not _native_nut_usb_driver_available()
    ):
        unavailable.add("power.ups.local-usb.configure.v1")
    if not _native_command_tools_available("smartctl"):
        unavailable.add("storage.smart.self-test.start.v1")
        unavailable.add("storage.smart.self-test.schedule.v1")
    return [
        capability for capability in _NATIVE_WRITE_CAPABILITIES if capability not in unavailable
    ]


def status() -> dict[str, Any]:
    """The native plane is always 'configured' — it needs no external panel."""
    device_probe = _probe_block_devices()
    observed = observation([device_probe.evidence])
    return {
        "configured": True,
        "readOnly": False,
        "adminUrl": None,
        "capabilities": _native_write_capabilities(),
        "source": "native",
        "devices": len(device_probe.value),
        "state": "ready"
        if observed["coverage"] == "complete"
        else "degraded"
        if observed["available"]
        else "unknown",
        **observed,
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
    # Keep stale rules visible while their data volume is offline so the UI
    # can offer the safe removal flow instead of hiding the dependency.
    for entry in _nfs_exports_load(allow_unmounted=True):
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
                "status": _native_registered_folder_status(entry),
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
    return _filesystem_uuid(str(entry.get("devicefile") or ""), str(entry.get("mountpoint") or ""))


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
    # ``readOnly=False`` is not enough to authorize a write: when findmnt
    # failed the probe layer marks the mount options as unknown.  Treat that
    # explicit uncertainty as non-writable until the kernel reports either
    # ``rw`` or a subsequent inventory refresh succeeds.
    if entry.get("readOnly") or entry.get("mountOptionsKnown") is False:
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
        "supportsAcl": shutil.which("getfacl") is not None and shutil.which("setfacl") is not None,
    }


def _shared_folder_target_payload(volume_ref: str, volume_path: str) -> dict[str, Any]:
    """Return the public target shape without exposing the host mount path."""
    filesystem = next(
        (
            entry
            for entry in filesystems()
            if os.path.normpath(str(entry.get("mountpoint") or "")) == os.path.normpath(volume_path)
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
        _registered_uuid(existing, strict=True)
        if _registered_relative_name(existing, strict=True) != desired["name"] or os.path.normpath(
            str(existing.get("volumePath") or "")
        ) != os.path.normpath(volume_path):
            raise OSError(
                "native shared-folder registry identity does not match the mounted target"
            )
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
                    (item for item in observed_registry if item.get("uuid") == plan["shareUuid"]),
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


def _shared_folder_detach_dependencies(
    entry: dict[str, Any],
) -> tuple[bool, list[dict[str, Any]]]:
    """Return live dependent shares that must be removed before detaching."""
    smb_present = _smb_usershare_info(str(entry["name"])) is not None
    nfs_entries = [
        item
        for item in _nfs_exports_load(strict=True, allow_unmounted=True)
        if item.get("sharedFolderRef") == entry.get("uuid")
    ]
    return smb_present, nfs_entries


def _build_shared_folder_rename_plan(desired: dict[str, Any]) -> dict[str, Any]:
    """Build a same-volume rename plan without changing the share identity."""
    registry = _registry_load(strict=True)
    matches = [entry for entry in registry if _registered_uuid(entry) == desired["sharedFolderRef"]]
    if not matches:
        raise ValueError("sharedFolderRef does not match any native shared folder")
    if len(matches) != 1:
        raise OSError("native shared-folder registry contains duplicate UUIDs")
    entry = matches[0]
    source = _native_folder_path(entry)
    group_gid = _users_group_gid()
    if not _verify_shared_folder(source, group_gid):
        raise OSError("registered shared folder has unsafe owner, group, or mode")

    old_name = _registered_relative_name(entry, strict=True)
    assert old_name is not None
    target = source.parent / desired["name"]
    target_state = _target_state(target)
    if desired["name"] == old_name:
        operation = "none"
    else:
        operation = "rename"
        if target_state["kind"] != "absent":
            raise ValueError("shared folder rename target already exists")

    smb_present, nfs_entries = _shared_folder_detach_dependencies(entry)
    if operation != "none":
        if smb_present:
            raise ValueError("disable the SMB share before renaming this folder")
        if nfs_entries:
            raise ValueError("remove the NFS rule before renaming this folder")

    base_revision = _canonical_hash(
        {
            "registry": registry,
            "folder": {
                "uuid": entry["uuid"],
                "name": old_name,
                "state": _target_state(source),
            },
            "target": {"name": desired["name"], "state": target_state},
            "smb": smb_present,
            "nfs": nfs_entries,
        }
    )
    plan_id = _canonical_hash(
        {
            "schema": SHARED_FOLDER_RENAME_PLAN_SCHEMA,
            "baseRevision": base_revision,
            "desired": desired,
            "operation": operation,
        }
    )
    return {
        "schema": SHARED_FOLDER_RENAME_PLAN_SCHEMA,
        "planId": plan_id,
        "baseRevision": base_revision,
        "operation": operation,
        "requiresApproval": operation != "none",
        "shareUuid": entry["uuid"],
        "sharedFolder": _public_shared_folder_entry(entry),
        "desired": desired,
        "changes": (
            []
            if operation == "none"
            else [{"field": "name", "before": old_name, "after": desired["name"]}]
        ),
        "safety": {
            "filesystem": "sameMountedWritableVolume",
            "data": "preserved",
            "identity": "uuidPreserved",
            "acl": "preservedWithDirectory",
            "dependentShares": "mustBeAbsent",
            "rollback": "directoryAndRegistry",
        },
        "source": "native",
    }


def plan_shared_folder_rename(desired_state: dict[str, Any]) -> dict[str, Any]:
    """Preview a data-preserving rename of one registered shared folder."""
    desired = validate_shared_folder_rename_desired(dict(desired_state))
    with _registry_transaction():
        return _build_shared_folder_rename_plan(desired)


def apply_shared_folder_rename(desired_state: dict[str, Any], plan_id: str) -> dict[str, Any]:
    """Rename one directory and its registry row, rolling both back together."""
    desired = validate_shared_folder_rename_desired(dict(desired_state))
    with _registry_transaction():
        plan = _build_shared_folder_rename_plan(desired)
        if plan["planId"] != plan_id:
            raise ValueError("shared folder rename plan is stale; preview the change again")

        entry = _resolve_shared_folder(desired["sharedFolderRef"])
        source = _native_folder_path(entry)
        if plan["operation"] == "none":
            if not _verify_shared_folder(source, _users_group_gid()):
                raise OSError("registered shared folder has unsafe owner, group, or mode")
            return {
                **plan,
                "applied": False,
                "verified": True,
                "dataPreserved": True,
                "sharedFolder": _public_shared_folder_entry(entry),
            }

        target = source.parent / desired["name"]
        registry = _registry_load(strict=True)
        original_registry = list(registry)
        entry_index = next(
            (
                index
                for index, item in enumerate(registry)
                if _registered_uuid(item) == desired["sharedFolderRef"]
            ),
            None,
        )
        if entry_index is None:
            raise ValueError("shared folder registry changed during apply; preview again")
        updated = {
            **registry[entry_index],
            "name": desired["name"],
            "relativePath": desired["name"],
        }
        registry[entry_index] = updated
        renamed = False
        try:
            source.rename(target)
            renamed = True
            if _target_state(source)["kind"] != "absent" or not _verify_shared_folder(
                target, _users_group_gid()
            ):
                raise OSError("shared folder directory rename was not verified")
            _registry_save(registry)
            observed = _resolve_shared_folder(desired["sharedFolderRef"])
            if (
                _registered_relative_name(observed, strict=True) != desired["name"]
                or _native_folder_path(observed) != target
            ):
                raise OSError("shared folder registry rename was not verified")
        except Exception as exc:
            try:
                if renamed:
                    if _target_state(source)["kind"] != "absent":
                        raise OSError("shared folder rename rollback source is occupied")
                    target.rename(source)
                    if not _verify_shared_folder(source, _users_group_gid()):
                        raise OSError("shared folder directory rename rollback was not verified")
                _registry_save(original_registry)
                if _registry_load(strict=True) != original_registry:
                    raise OSError("shared folder registry rename rollback was not verified")
            except Exception as rollback_exc:
                raise OSError(
                    "shared folder rename failed and rollback also failed; inspect the share"
                ) from rollback_exc
            if isinstance(exc, (OSError, ValueError)):
                raise
            raise OSError("shared folder rename failed") from exc

        return {
            **plan,
            "applied": True,
            "verified": True,
            "dataPreserved": True,
            "sharedFolder": _public_shared_folder_entry(updated),
        }


def _build_shared_folder_detach_plan(desired: dict[str, Any]) -> dict[str, Any]:
    registry = _registry_load(strict=True)
    matches = [entry for entry in registry if _registered_uuid(entry) == desired["sharedFolderRef"]]
    if not matches:
        raise ValueError("sharedFolderRef does not match any native shared folder")
    if len(matches) != 1:
        raise OSError("native shared-folder registry contains duplicate UUIDs")
    entry = matches[0]
    # Resolve and validate registry metadata, but never remove or rename the
    # directory.  Detaching is registry-only, so it remains possible while a
    # data volume is temporarily unavailable.
    path = _native_registered_path(entry)
    folder_status = _native_registered_folder_status(entry)
    smb_present, nfs_entries = _shared_folder_detach_dependencies(entry)
    if smb_present:
        raise ValueError("disable the SMB share before detaching this folder")
    if nfs_entries:
        raise ValueError("remove the NFS rule before detaching this folder")
    base_revision = _canonical_hash(
        {
            "registry": registry,
            "folder": {
                "uuid": entry["uuid"],
                "name": entry["name"],
                "path": str(path),
            },
            "status": folder_status,
            "smb": smb_present,
            "nfs": nfs_entries,
        }
    )
    plan_id = _canonical_hash(
        {
            "schema": SHARED_FOLDER_DETACH_PLAN_SCHEMA,
            "baseRevision": base_revision,
            "desired": desired,
        }
    )
    shared_folder = _public_shared_folder_entry(entry)
    shared_folder["status"] = folder_status
    return {
        "schema": SHARED_FOLDER_DETACH_PLAN_SCHEMA,
        "planId": plan_id,
        "baseRevision": base_revision,
        "operation": "remove",
        "requiresApproval": True,
        "shareUuid": entry["uuid"],
        "sharedFolder": shared_folder,
        "desired": desired,
        "changes": [
            {
                "field": "registration",
                "before": "managed",
                "after": "detached",
            }
        ],
        "safety": {
            "data": "preserved",
            "directory": "neverDeleted",
            "dependentShares": "mustBeAbsent",
            "acl": "untouched",
            "rollback": "registryOnly",
            "mount": "notRequiredForDetach",
        },
        "source": "native",
    }


def plan_shared_folder_detach(desired_state: dict[str, Any]) -> dict[str, Any]:
    """Preview unregistering a native folder while preserving all data."""
    desired = validate_shared_folder_detach_desired(dict(desired_state))
    with _registry_transaction():
        return _build_shared_folder_detach_plan(desired)


def apply_shared_folder_detach(desired_state: dict[str, Any], plan_id: str) -> dict[str, Any]:
    """Remove only the native registry entry; never delete the folder or data."""
    desired = validate_shared_folder_detach_desired(dict(desired_state))
    with _registry_transaction():
        plan = _build_shared_folder_detach_plan(desired)
        if plan["planId"] != plan_id:
            raise ValueError("shared folder detach plan is stale; preview the change again")
        registry = _registry_load(strict=True)
        original_registry = list(registry)
        wanted = [
            entry for entry in registry if _registered_uuid(entry) != desired["sharedFolderRef"]
        ]
        if len(wanted) == len(registry):
            raise ValueError("sharedFolderRef does not match any native shared folder")
        try:
            _registry_save(wanted)
            if any(
                _registered_uuid(entry) == desired["sharedFolderRef"]
                for entry in _registry_load(strict=True)
            ):
                raise OSError("shared folder registry detach was not verified")
        except Exception as exc:
            try:
                _registry_save(original_registry)
                if _registry_load(strict=True) != original_registry:
                    raise OSError("shared folder registry rollback was not verified")
            except Exception as rollback_exc:
                raise OSError(
                    "shared folder detach failed and registry rollback also failed"
                ) from rollback_exc
            if isinstance(exc, (OSError, ValueError)):
                raise
            raise OSError("shared folder detach failed") from exc
        return {
            **plan,
            "applied": True,
            "verified": True,
            "dataPreserved": True,
        }


def _directory_is_empty(path: Path) -> bool:
    """Check one directory without traversing or following its children."""
    try:
        with os.scandir(path) as entries:
            return next(entries, None) is None
    except FileNotFoundError as exc:
        raise ValueError("shared folder directory does not exist on the host") from exc
    except NotADirectoryError as exc:
        raise OSError("registered shared folder is not a directory") from exc
    except OSError as exc:
        raise OSError("shared folder directory cannot be inspected") from exc


def _restore_deleted_shared_folder(path: Path, group_gid: int) -> None:
    """Recreate the known empty 2770/users directory during a failed delete."""
    try:
        path.mkdir(mode=0o2770, exist_ok=False)
        _configure_shared_folder(path, group_gid)
        if not _verify_shared_folder(path, group_gid) or not _directory_is_empty(path):
            raise OSError("restored shared folder directory did not pass verification")
    except Exception:
        with contextlib.suppress(OSError):
            path.rmdir()
        raise


def _build_shared_folder_delete_plan(desired: dict[str, Any]) -> dict[str, Any]:
    """Build a destructive plan that can only remove an empty native folder."""
    registry = _registry_load(strict=True)
    matches = [entry for entry in registry if _registered_uuid(entry) == desired["sharedFolderRef"]]
    if not matches:
        raise ValueError("sharedFolderRef does not match any native shared folder")
    if len(matches) != 1:
        raise OSError("native shared-folder registry contains duplicate UUIDs")
    entry = matches[0]
    path = _native_folder_path(entry)
    group_gid = _users_group_gid()
    if not _verify_shared_folder(path, group_gid):
        raise OSError("registered shared folder has unsafe owner, group, or mode")
    if not _directory_is_empty(path):
        raise ValueError(
            "shared folder directory is not empty; only empty directories can be deleted"
        )

    smb_present, nfs_entries = _shared_folder_detach_dependencies(entry)
    if smb_present:
        raise ValueError("disable the SMB share before deleting this folder")
    if nfs_entries:
        raise ValueError("remove the NFS rule before deleting this folder")

    target_state = _target_state(path)
    base_revision = _canonical_hash(
        {
            "registry": registry,
            "folder": {
                "uuid": entry["uuid"],
                "name": entry["name"],
                "path": str(path),
            },
            "target": target_state,
            "empty": True,
            "smb": smb_present,
            "nfs": nfs_entries,
        }
    )
    plan_id = _canonical_hash(
        {
            "schema": SHARED_FOLDER_DELETE_PLAN_SCHEMA,
            "baseRevision": base_revision,
            "desired": desired,
        }
    )
    shared_folder = _public_shared_folder_entry(entry)
    return {
        "schema": SHARED_FOLDER_DELETE_PLAN_SCHEMA,
        "planId": plan_id,
        "baseRevision": base_revision,
        "operation": "remove",
        "requiresApproval": True,
        "shareUuid": entry["uuid"],
        "sharedFolder": shared_folder,
        "desired": desired,
        "changes": [
            {"field": "directory", "before": "empty", "after": "deleted"},
            {"field": "registration", "before": "managed", "after": "removed"},
        ],
        "safety": {
            "data": "emptyDirectoryOnly",
            "directory": "deleted",
            "dependentShares": "mustBeAbsent",
            "recursive": "never",
            "mount": "mountedWritableOnly",
            "rollback": "registryAndEmptyDirectory",
        },
        "source": "native",
    }


def plan_shared_folder_delete(desired_state: dict[str, Any]) -> dict[str, Any]:
    """Preview deletion of one registered empty shared-folder directory."""
    desired = validate_shared_folder_delete_desired(dict(desired_state))
    with _registry_transaction():
        return _build_shared_folder_delete_plan(desired)


def apply_shared_folder_delete(desired_state: dict[str, Any], plan_id: str) -> dict[str, Any]:
    """Delete only an empty native folder and its registration, with rollback."""
    desired = validate_shared_folder_delete_desired(dict(desired_state))
    with _registry_transaction():
        plan = _build_shared_folder_delete_plan(desired)
        if plan["planId"] != plan_id:
            raise ValueError("shared folder delete plan is stale; preview the change again")

        entry = _resolve_shared_folder(desired["sharedFolderRef"])
        path = _native_folder_path(entry)
        group_gid = _users_group_gid()
        if not _verify_shared_folder(path, group_gid):
            raise OSError("registered shared folder has unsafe owner, group, or mode")
        if not _directory_is_empty(path):
            raise ValueError(
                "shared folder directory is not empty; only empty directories can be deleted"
            )

        registry = _registry_load(strict=True)
        original_registry = list(registry)
        wanted = [item for item in registry if _registered_uuid(item) != desired["sharedFolderRef"]]
        if len(wanted) == len(registry):
            raise ValueError("sharedFolderRef does not match any native shared folder")

        removed_directory = False
        try:
            _registry_save(wanted)
            if any(
                _registered_uuid(item) == desired["sharedFolderRef"]
                for item in _registry_load(strict=True)
            ):
                raise OSError("shared folder registry delete was not verified")
            path.rmdir()
            removed_directory = True
            if _target_state(path)["kind"] != "absent":
                raise OSError("shared folder directory delete was not verified")
        except Exception as exc:
            try:
                if removed_directory:
                    _restore_deleted_shared_folder(path, group_gid)
                _registry_save(original_registry)
                if _registry_load(strict=True) != original_registry:
                    raise OSError("shared folder delete rollback was not verified")
            except Exception as rollback_exc:
                raise OSError(
                    "shared folder delete failed and rollback also failed; inspect the empty share"
                ) from rollback_exc
            if isinstance(exc, (OSError, ValueError)):
                raise
            raise OSError("shared folder delete failed") from exc

        return {
            **plan,
            "applied": True,
            "verified": True,
            "directoryDeleted": True,
            "dataDeleted": False,
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
        groups = sorted({entry.gr_name for entry in grp.getgrall() if name in (entry.gr_mem or ())})
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
        "--shell",
        "/usr/sbin/nologin",
        "--comment",
        desired["displayName"],
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


def _native_registered_folder_status(entry: dict[str, Any]) -> str:
    """Project whether a registered folder is mounted, read-only, or absent."""
    try:
        _native_folder_path(entry)
    except (OSError, ValueError):
        volume_path = str(entry.get("volumePath") or "")
        for filesystem in filesystems():
            if os.path.normpath(str(filesystem.get("mountpoint") or "")) != os.path.normpath(
                volume_path
            ):
                continue
            if filesystem.get("readOnly"):
                return "READ_ONLY"
            break
        return "UNAVAILABLE"
    return "MOUNTED"


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
                    _run_write("setfacl", "-x", f"d:{kind}:{desired['principalName']}", str(path))
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


def _native_registered_path(entry: dict[str, Any]) -> Path:
    """Resolve a registered folder path without requiring its mount to be live.

    This is intentionally a metadata-only resolver.  It is used by protocol
    cleanup paths so an administrator can remove a stale share after a disk
    has gone offline.  It never follows the path or mutates the directory;
    the volume root, mount UUID and single portable folder component are still
    checked before the path can enter a host command.
    """
    volume_path = entry.get("volumePath")
    if not isinstance(volume_path, str) or not os.path.isabs(volume_path):
        raise OSError("native NFS registry contains an invalid volume path")
    if not _native_data_mountpoint(volume_path):
        raise OSError("native NFS registry volume is outside the NAS data roots")
    try:
        mount_ref = str(uuid_module.UUID(str(entry.get("mountPointRef"))))
    except (AttributeError, TypeError, ValueError) as exc:
        raise OSError("native NFS registry contains an invalid mount identity") from exc
    if mount_ref != volume_uuid(volume_path):
        raise OSError("native NFS registry volume identity does not match its path")
    relative_name = _registered_relative_name(entry, strict=True)
    assert relative_name is not None
    return Path(volume_path) / relative_name


def _nfs_registered_path(entry: dict[str, Any]) -> Path:
    path = _native_registered_path(entry)
    if any(character.isspace() for character in str(path)) or (
        os.name != "nt" and "\\" in str(path)
    ):
        raise ValueError("NFS cannot publish a shared-folder path containing whitespace")
    return path


def _nfs_rule_path(folder_ref: str) -> tuple[dict[str, Any], Path]:
    entry = _resolve_shared_folder(folder_ref)
    path = _native_folder_path(entry)
    # Keep the same path-shape guard for the mounted create/update path.
    _nfs_registered_path(entry)
    return entry, path


def _render_nfs_exports(entries: list[dict[str, Any]], *, allow_unmounted: bool = False) -> str:
    lines = ["# Generated by Echo OS. Manual edits are rejected and never merged."]
    for item in sorted(entries, key=lambda value: (value["sharedFolderRef"], value["client"])):
        if allow_unmounted:
            folder = _resolve_shared_folder(item["sharedFolderRef"])
            path = _nfs_registered_path(folder)
        else:
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


def _nfs_exports_load(
    *, strict: bool = False, allow_unmounted: bool = False
) -> list[dict[str, Any]]:
    try:
        text = _read_regular_text(_NATIVE_NFS_EXPORTS, missing_ok=True)
        if text is None:
            return []
        prefix = "# echo-os-rule "
        raw_entries = [
            json.loads(line[len(prefix) :]) for line in text.splitlines() if line.startswith(prefix)
        ]
        entries = _validated_nfs_export_entries(raw_entries)
        rendered = (
            _render_nfs_exports(entries, allow_unmounted=True)
            if allow_unmounted
            else _render_nfs_exports(entries)
        )
        if text != rendered:
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
        "shareUuid": existing["uuid"]
        if existing
        else _nfs_uuid(desired["sharedFolderRef"], desired["clientCidr"]),
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


def _verify_live_nfs_absent(path: Path, client: str) -> None:
    output = _run_read_checked("exportfs", "-v")
    if re.search(rf"{re.escape(str(path))}\s+{re.escape(client)}\([^)]*\)", output):
        raise OSError("NFS rule remained in the live export table after removal")


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


def _nfs_removal_context(folder_ref: str) -> tuple[dict[str, Any], Path, str]:
    """Return a validated path and whether its volume is currently usable.

    Removing an export is metadata cleanup, so it remains available when the
    data volume is temporarily absent or read-only.  A mounted writable
    folder keeps the normal ``MOUNTED`` plan status; any other live-filesystem
    state is reported as ``UNAVAILABLE`` without relaxing registry/path
    validation.
    """
    folder = _resolve_shared_folder(folder_ref)
    path = _nfs_registered_path(folder)
    return folder, path, _native_registered_folder_status(folder)


def _build_nfs_remove_plan(desired: dict[str, Any]) -> dict[str, Any]:
    folder, path, folder_status = _nfs_removal_context(desired["sharedFolderRef"])
    exports = _nfs_exports_load(strict=True, allow_unmounted=True)
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
    operation = "remove" if existing is not None else "none"
    share_uuid = (
        existing["uuid"]
        if existing
        else _nfs_uuid(desired["sharedFolderRef"], desired["clientCidr"])
    )
    base_revision = _canonical_hash(
        {
            "folder": {"uuid": folder["uuid"], "name": folder["name"], "path": str(path)},
            "status": folder_status,
            "exports": exports_text,
        }
    )
    plan_id = _canonical_hash(
        {
            "schema": NFS_REMOVE_PLAN_SCHEMA,
            "baseRevision": base_revision,
            "desired": desired,
        }
    )
    return {
        "schema": NFS_REMOVE_PLAN_SCHEMA,
        "planId": plan_id,
        "baseRevision": base_revision,
        "operation": operation,
        "requiresApproval": operation == "remove",
        "shareUuid": share_uuid,
        "sharedFolder": {
            "uuid": folder["uuid"],
            "name": folder["name"],
            "status": folder_status,
        },
        "desired": desired,
        "changes": (
            [
                {
                    "field": "registration",
                    "before": "managed",
                    "after": "removed",
                }
            ]
            if existing
            else []
        ),
        "safety": {
            "export": "managedRuleOnly",
            "data": "preserved",
            "directory": "neverModified",
            "clientScope": "privateCidrOnly",
            "rollback": "exportsAndLiveTable",
        },
        "source": "native",
    }


def plan_nfs_remove(desired_state: dict[str, Any]) -> dict[str, Any]:
    """Preview removing one Echo-managed NFS export without touching data."""
    desired = validate_nfs_remove_desired(dict(desired_state))
    with _registry_transaction():
        return _build_nfs_remove_plan(desired)


def apply_nfs_remove(desired_state: dict[str, Any], plan_id: str) -> dict[str, Any]:
    """Remove one managed NFS rule and verify it left the live export table."""
    desired = validate_nfs_remove_desired(dict(desired_state))
    with _registry_transaction():
        plan = _build_nfs_remove_plan(desired)
        if plan["planId"] != plan_id:
            raise ValueError("NFS remove plan is stale; preview the change again")
        folder = _resolve_shared_folder(desired["sharedFolderRef"])
        path = _nfs_registered_path(folder)
        if plan["operation"] == "none":
            _verify_live_nfs_absent(path, desired["clientCidr"])
            return {**plan, "applied": False, "verified": True, "dataPreserved": True}
        old_exports = _read_regular_text(_NATIVE_NFS_EXPORTS, missing_ok=True)
        exports = _nfs_exports_load(strict=True, allow_unmounted=True)
        existing = next(
            item
            for item in exports
            if item["sharedFolderRef"] == desired["sharedFolderRef"]
            and item["client"] == desired["clientCidr"]
        )
        wanted = [
            item
            for item in exports
            if not (
                item["sharedFolderRef"] == desired["sharedFolderRef"]
                and item["client"] == desired["clientCidr"]
            )
        ]
        restore_desired = {
            "schema": "echo.omv.nfs-share-desired.v1",
            "sharedFolderRef": existing["sharedFolderRef"],
            "clientCidr": existing["client"],
            "readOnly": existing["readOnly"],
            "comment": existing["comment"],
        }
        try:
            if wanted:
                _atomic_text_save(
                    _NATIVE_NFS_EXPORTS,
                    _render_nfs_exports(wanted, allow_unmounted=True),
                    mode=0o644,
                )
            else:
                _restore_managed_text(_NATIVE_NFS_EXPORTS, None, mode=0o644)
            _run_write("exportfs", "-ra")
            if _nfs_exports_load(strict=True, allow_unmounted=True) != wanted:
                raise OSError("native NFS export removal write-back verification failed")
            _verify_live_nfs_absent(path, desired["clientCidr"])
        except Exception as exc:
            try:
                _restore_managed_text(_NATIVE_NFS_EXPORTS, old_exports, mode=0o644)
                _run_write("exportfs", "-ra")
                if _read_regular_text(_NATIVE_NFS_EXPORTS, missing_ok=True) != old_exports:
                    raise OSError("NFS exports rollback was not verified")
                if plan["sharedFolder"]["status"] == "MOUNTED":
                    _verify_live_nfs(path, restore_desired)
                else:
                    _verify_live_nfs_absent(path, desired["clientCidr"])
            except Exception as rollback_exc:
                raise OSError(
                    "NFS removal failed and rollback also failed; inspect exports immediately"
                ) from rollback_exc
            if isinstance(exc, (OSError, ValueError)):
                raise
            raise OSError("NFS removal failed") from exc
        return {
            **plan,
            "applied": True,
            "verified": True,
            "dataPreserved": True,
            "share": {
                "uuid": existing["uuid"],
                "sharedFolderRef": existing["sharedFolderRef"],
                "clientCidr": existing["client"],
                "removed": True,
            },
        }


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
        "r" in value.casefold() and not {"f", "w"}.intersection(value.casefold())
        for value in permissions
    )


def _smb_info_targets_path(info: dict[str, Any], expected: Path) -> bool:
    """Reject a same-name usershare that points at a different directory.

    Samba's ``net usershare info`` includes an absolute ``path`` field.  A
    missing field is tolerated for older test doubles/servers, but whenever it
    is present it must match the signed native-folder path byte-for-byte after
    normalizing separators; otherwise a disable/update could destroy an
    unrelated share that happens to reuse the folder name.
    """
    raw_path = info.get("path")
    if raw_path is None:
        return True
    if not isinstance(raw_path, str) or not raw_path or any(char < " " for char in raw_path):
        return False
    if not os.path.isabs(raw_path):
        return False
    return os.path.normpath(raw_path) == os.path.normpath(str(expected))


def _build_smb_plan(desired: dict[str, Any]) -> dict[str, Any]:
    desired = validate_smb_desired(dict(desired))
    if desired["browseable"] is not True:
        raise ValueError("native SMB usershare discovery cannot be disabled")
    if desired["recycleBin"] is not False:
        raise ValueError("native SMB usershare does not manage a recycle bin")
    entry = _resolve_shared_folder(desired["sharedFolderRef"])
    # Resolve the path from the signed registry even when the volume is
    # unavailable.  A delete is safe metadata cleanup; only create/update
    # below require a currently mounted writable directory.
    path = _native_registered_path(entry)
    folder_status = _native_registered_folder_status(entry)
    name = entry["name"]
    existing = _smb_usershare_info(name)
    if existing is not None and not _smb_info_targets_path(existing, path):
        raise ValueError("Samba usershare name is already bound to another path")

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
        operation = (
            "none" if all(current[field] == after for field, after in wanted.items()) else "update"
        )

    if operation in {"create", "update"} and folder_status != "MOUNTED":
        raise ValueError("shared folder volume is not mounted as a writable NAS target")

    base_revision = _canonical_hash(
        {
            "share": name,
            "path": str(path),
            "exists": existing is not None,
            "status": folder_status,
        }
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
            "status": folder_status,
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
    expected_path = _native_registered_path(entry)
    operation = plan["operation"]
    if operation == "none":
        observed = _smb_usershare_info(name)
        if observed is not None and not _smb_info_targets_path(observed, expected_path):
            raise OSError("Samba usershare path changed during apply")
        if desired["enabled"]:
            if observed is None:
                raise OSError("planned SMB share is missing on the host")
        elif observed is not None:
            raise OSError("planned SMB share appeared during apply")
        return {**plan, "applied": False, "verified": True, "share": {"name": name}}
    if operation == "remove":
        observed = _smb_usershare_info(name)
        if observed is not None and not _smb_info_targets_path(observed, expected_path):
            raise OSError("Samba usershare path changed during apply")
        _run_write("net", "usershare", "delete", name)
        if _smb_usershare_info(name) is not None:
            raise OSError("Samba usershare remained after delete")
        return {**plan, "applied": True, "verified": True, "share": {"name": name}}
    path = _native_folder_path(entry)
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
    if not _smb_info_targets_path(observed, expected_path):
        raise OSError("Samba usershare points at a different path after update")
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
    if filesystem.get("mountOptionsKnown") is False:
        raise ValueError("quota filesystem mount options are unavailable")
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


def _zfs_pool_managed_dependencies(pool_name: str) -> list[dict[str, str]]:
    """Return managed shares rooted anywhere inside one Echo ZFS pool."""
    pool_root = _NATIVE_ZFS_MOUNT_ROOT / pool_name
    dependencies: list[dict[str, str]] = []
    for entry in _registry_load(strict=True):
        path = _native_registered_path(entry)
        try:
            path.relative_to(pool_root)
        except ValueError:
            continue
        folder_uuid = _registered_uuid(entry, strict=True)
        folder_name = _registered_relative_name(entry, strict=True)
        assert folder_uuid is not None
        assert folder_name is not None
        dependencies.append({"uuid": folder_uuid, "name": folder_name})
    return sorted(dependencies, key=lambda item: (item["name"], item["uuid"]))


def exportable_zfs_pools() -> list[dict[str, Any]]:
    """List Echo-layout pools with no managed share dependency."""
    with _registry_transaction():
        return [
            pool
            for pool in _exportable_zfs_pools()
            if not _zfs_pool_managed_dependencies(pool["name"])
        ]


def plan_zfs_pool_export(desired_state: dict[str, Any]) -> dict[str, Any]:
    """Preview a non-force export after proving managed shares are detached."""
    desired = validate_zfs_pool_export_desired(dict(desired_state))
    with _registry_transaction():
        dependencies = _zfs_pool_managed_dependencies(desired["name"])
        return _plan_zfs_pool_export(desired, dependencies)


def apply_zfs_pool_export(desired_state: dict[str, Any], plan_id: str) -> dict[str, Any]:
    """Export one exact pool while holding the share registry snapshot stable."""
    desired = validate_zfs_pool_export_desired(dict(desired_state))
    with _registry_transaction():
        dependencies = _zfs_pool_managed_dependencies(desired["name"])
        return _apply_zfs_pool_export(desired, plan_id, dependencies)


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
    "apply_btrfs_raid1",
    "apply_group",
    "apply_mdraid1",
    "apply_mdraid1_replace",
    "apply_mdraid_check",
    "apply_ext4_volume",
    "apply_nfs",
    "apply_nfs_remove",
    "apply_quota",
    "apply_share_privilege",
    "apply_shared_folder",
    "apply_shared_folder_delete",
    "apply_shared_folder_rename",
    "apply_smb",
    "apply_user",
    "apply_user_password",
    "apply_zfs_mirror",
    "apply_zfs_mirror_replace",
    "apply_zfs_pool_export",
    "apply_zfs_pool_import",
    "apply_zfs_scrub",
    "block_devices",
    "btrfs_raid1_candidates",
    "filesystems",
    "ext4_volume_candidates",
    "exportable_zfs_pools",
    "md_arrays",
    "mdraid1_candidates",
    "mdraid1_replacement_candidates",
    "mdraid_maintenance",
    "plan_group",
    "plan_btrfs_raid1",
    "plan_ext4_volume",
    "plan_mdraid1",
    "plan_mdraid1_replace",
    "plan_mdraid_check",
    "plan_nfs",
    "plan_nfs_remove",
    "plan_quota",
    "plan_share_privilege",
    "plan_shared_folder",
    "plan_shared_folder_delete",
    "plan_shared_folder_rename",
    "plan_smb",
    "plan_user",
    "plan_user_password",
    "plan_zfs_mirror",
    "plan_zfs_mirror_replace",
    "plan_zfs_pool_export",
    "plan_zfs_pool_import",
    "plan_zfs_scrub",
    "sharing_overview",
    "share_privileges",
    "smart_devices",
    "smart_report",
    "status",
    "storage_health",
    "storage_topology",
    "validated_devicefile",
    "volume_uuid",
    "importable_zfs_pools",
    "zfs_mirror_candidates",
    "zfs_mirror_replacement_candidates",
    "zfs_pool_maintenance",
    "zfs_pools",
]
