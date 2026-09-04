"""Native (OpenMediaVault-free) storage plane.

Read-only storage source built exclusively on standard Linux tooling that
already ships with the appliance: ``zpool``, ``lsblk``, ``df`` and
``smartctl``. The kernel — not a third-party NAS panel — is the single source
of truth for device state, so no parallel storage state is ever maintained.

Payloads are intentionally isomorphic to the OMV bridge responses
(:class:`appliance.omv_client.OmvClient`) so the existing appliance UI works
identically with or without OpenMediaVault installed:

    /status       -> OmvStatus
    /health       -> OmvHealthSnapshot
    /filesystems  -> OmvFilesystem[]
    /smart        -> OmvSmart
    /smart/devices-> OmvSmartDevice[]
    /topology     -> OmvStorageTopology

Everything here is read-only; writes (format, share, quota) stay out of this
module on purpose.
"""

from __future__ import annotations

import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from appliance.omv_protocol import validate_devicefile

SCHEMA_VERSION = 1

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
    return datetime.now(timezone.utc).isoformat()


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
        "lsblk", "-J", "-b", "-o",
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
        if node_type in {"disk", "rom", "part", "lvm", "md", "raid0", "raid1", "raid5", "raid6", "raid10"}:
            if node_type == "disk" and (_int(node.get("size")) or 0) >= _MIN_DISK_BYTES:
                devices.append(
                    {
                        "devicefile": f"/dev/{name}",
                        "type": node_type,
                        "sizeBytes": _int(node.get("size")),
                        "filesystemType": node.get("fstype") or None,
                        "rotational": None if node.get("rota") is None else bool(node.get("rota") == "1" or node.get("rota") is True),
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
    match = re.search(r"^\s*(\S+)-\d+\s+(ONLINE|DEGRADED|FAULTED|OFFLINE|UNAVAIL)", status, re.MULTILINE)
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
    match = re.search(r"scrub in progress[^\n]*?(\d+(?:\.\d+)?)% done", _run("zpool", "status", pool, timeout=10.0))
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
    for line in out.strip().splitlines()[1:]:
        parts = line.split()
        if len(parts) < 7:
            continue
        devicefile, fstype, size, used, available, used_percent, mountpoint = (
            parts[0], parts[1], parts[2], parts[3], parts[4], parts[5], " ".join(parts[6:])
        )
        if fstype in _PSEUDO_FS or devicefile in seen:
            continue
        seen.add(devicefile)
        size_i = _int(size)
        available_i = _int(available)
        if size_i is None or available_i is None:
            continue
        entries.append(
            {
                "devicefile": devicefile,
                "parentdevicefile": None,
                "uuid": None,
                "label": mountpoint.split("/")[-1] or mountpoint,
                "type": fstype,
                "mountpoint": mountpoint,
                "sizeBytes": size_i,
                "availableBytes": available_i,
                "usedPercent": _percent(_int(used), size_i),
                "readOnly": False,
                "supportsAcl": False,
                "supportsQuota": fstype in {"zfs", "btrfs", "xfs", "ext4"},
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
        "powerCycles": _int((payload.get("power_cycle_count") or 0)) or None,
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
            {**alert, "firstSeenAt": now, "lastSeenAt": now, "occurrences": 1}
            for alert in alerts
        ],
        "events": [],
        "summary": {"critical": critical, "warning": warning, "total": len(alerts)},
        "readOnly": True,
        "source": "native",
        "pools": len(_zfs),
        "devices": len(devices),
    }


def status() -> dict[str, Any]:
    """The native plane is always 'configured' — it needs no external panel."""
    return {
        "configured": True,
        "available": bool(block_devices()),
        "readOnly": True,
        "adminUrl": None,
        "capabilities": [],
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


def _samba_usershares() -> list[dict[str, Any]]:
    out = _run("net", "usershare", "info", "--long", timeout=15.0)
    shares: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in (out or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if not line.startswith("[") or not line.endswith("]"):
            if current is not None and ":" in line:
                key, _, value = line.partition(":")
                key, value = key.strip(), value.strip()
                if key == "path":
                    current["sharedFolderName"] = value.rstrip("/").split("/")[-1]
                elif key == "comment":
                    current["comment"] = value
                elif key == "usershare_acl":
                    current["readOnly"] = "R" in value and "F" not in value
                    current["guest"] = "guest_ok=y" if "Guests" in value or "B" in value else "none"
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
        if uid is None or gid is None or uid < _SYSTEM_UID_FLOOR or uid >= 65534:
            continue
        users.append(
            {
                "name": name,
                "uid": uid,
                "gid": gid,
                "comment": gecos.split(",")[0],
                "groups": [
                    g[0] for g in _etc_entries("/etc/group") if name in (g[3].split(",") if len(g) > 3 else [])
                ],
            }
        )
    groups = [
        {"name": fields[0], "gid": _int(fields[2]) or 0, "members": fields[3].split(",") if len(fields) > 3 else []}
        for fields in _etc_entries("/etc/group")
        if len(fields) > 3 and _SYSTEM_UID_FLOOR <= (_int(fields[2]) or 0) < 65534
    ]

    fs_entries = filesystems()
    shared_folders = [
        {
            "uuid": entry["devicefile"],
            "name": entry["label"],
            "comment": "",
            "relativePath": entry["mountpoint"],
            "device": entry["devicefile"],
            "status": "MOUNTED",
            "inUse": True,
            "supportsAcl": bool(entry.get("supportsAcl")),
        }
        for entry in fs_entries
    ]
    shared_folder_targets = [
        {"mountPointRef": entry["devicefile"], "path": entry["mountpoint"]}
        for entry in fs_entries
    ]
    smb_shares = _samba_usershares()
    return {
        "sharedFolders": shared_folders,
        "sharedFolderTargets": shared_folder_targets,
        "users": users,
        "groups": groups,
        "smb": {"enabled": bool(smb_shares), "shares": smb_shares},
        "nfs": {"enabled": False, "shares": []},
        "readOnly": True,
    }


class NativeStorageAuthority:
    """Drop-in replacement for the OMV client's read surface.

    Accounts directory and the family data-access policy previously treated
    OpenMediaVault as the storage identity authority; this class provides the
    same duck-typed surface backed by the host itself (system users, mounts,
    Samba usershares). Read-only by design.
    """

    configured = True
    admin_url = None

    def ping(self) -> bool:
        return bool(block_devices())

    def sharing_overview(self) -> dict[str, Any]:
        return sharing_overview()

    def share_privileges(self, share_uuid: str) -> list[dict[str, Any]]:
        # 原生语义:本机系统用户/组对挂载的存储卷拥有读写权。
        overview = sharing_overview()
        privileges = [
            {"type": "user", "id": u["name"], "name": u["name"], "permission": "readWrite"}
            for u in overview["users"]
        ]
        privileges.extend(
            {"type": "group", "id": g["name"], "name": g["name"], "permission": "readWrite"}
            for g in overview["groups"]
        )
        return privileges


def validated_devicefile(devicefile: str) -> str:
    """Validate a device path before it reaches any subprocess."""
    try:
        return validate_devicefile(devicefile)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc


__all__ = [
    "NativeStorageAuthority",
    "block_devices",
    "filesystems",
    "md_arrays",
    "sharing_overview",
    "smart_devices",
    "smart_report",
    "status",
    "storage_health",
    "storage_topology",
    "validated_devicefile",
    "zfs_pools",
]
