"""Destructive native storage-pool operations with fail-closed disk binding.

Operations here are deliberately narrow: create a two-disk ZFS mirror from
whole blank disks, export an idle Echo-layout pool without force, or import
one exact exported pool by GUID after a no-mount read-only inspection.  Every
mutation is plan-bound and must pass the appliance approval/audit envelope.
Expansion, healthy-member or multi-vdev replacement, destroy, force-import,
recovery rewind, missing-log import, destroyed-pool import, and signature
wiping remain unsupported.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat as stat_module
import subprocess
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from appliance.omv_protocol import (
    ZFS_MIRROR_PLAN_SCHEMA,
    ZFS_MIRROR_REPLACE_PLAN_SCHEMA,
    ZFS_POOL_EXPORT_PLAN_SCHEMA,
    ZFS_POOL_IMPORT_PLAN_SCHEMA,
    ZFS_SCRUB_PLAN_SCHEMA,
    validate_zfs_mirror_desired,
    validate_zfs_mirror_replace_desired,
    validate_zfs_pool_export_desired,
    validate_zfs_pool_import_desired,
    validate_zfs_scrub_desired,
)

_MIN_DISK_BYTES = 1024**3
_ZFS_MOUNT_ROOT = Path("/data")
_ZFS_LOCK_PATH = Path("/run/lock/echo-os-zfs-pool.lock")
_ZFS_THREAD_LOCK = threading.RLock()
_WHOLE_DISK_PATTERN = re.compile(r"/dev/(?:sd[a-z]+|vd[a-z]+|xvd[a-z]+|nvme\d+n\d+|mmcblk\d+)")
_POOL_NAME_PATTERN = re.compile(r"[a-z][a-z0-9_-]{0,31}")
_POOL_GUID_PATTERN = re.compile(r"[1-9][0-9]{0,19}")
_POOL_HEALTH = frozenset({"ONLINE", "DEGRADED", "FAULTED", "OFFLINE", "REMOVED", "UNAVAIL"})


def _require_tools() -> None:
    missing = [name for name in ("zpool", "zfs", "lsblk", "wipefs") if shutil.which(name) is None]
    if missing:
        raise OSError(f"native ZFS mirror tools are unavailable: {', '.join(missing)}")


def _require_lifecycle_tools() -> None:
    missing = [name for name in ("zpool", "zfs") if shutil.which(name) is None]
    if missing:
        raise OSError(f"native ZFS lifecycle tools are unavailable: {', '.join(missing)}")


def _canonical_hash(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _run_checked(*args: str, timeout: float = 30.0) -> str:
    try:
        completed = subprocess.run(
            list(args),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise OSError(f"native command failed to start: {args[0]}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip() or f"exit {completed.returncode}"
        raise OSError(f"{args[0]} failed: {detail}")
    return completed.stdout or ""


def _run_mutating(*args: str, timeout: float = 120.0) -> None:
    _run_checked(*args, timeout=timeout)


def _mountpoints(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list) and all(item is None or isinstance(item, str) for item in value):
        return [item for item in value if item]
    raise OSError("lsblk returned an invalid mountpoint list")


def _inspect_zfs_mirror_devices(devicefiles: list[str]) -> list[dict[str, Any]]:
    """Return stable identities after proving every requested disk is blank."""
    for devicefile in devicefiles:
        if _WHOLE_DISK_PATTERN.fullmatch(devicefile) is None:
            raise ValueError(f"unsupported whole-disk device: {devicefile}")
    output = _run_checked(
        "lsblk",
        "-J",
        "-b",
        "-p",
        "-o",
        "PATH,TYPE,SIZE,FSTYPE,MOUNTPOINTS,SERIAL,WWN,MODEL,RO,RM,PTTYPE",
        *devicefiles,
    )
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as exc:
        raise OSError("lsblk returned invalid JSON") from exc
    raw_devices = payload.get("blockdevices") if isinstance(payload, dict) else None
    if not isinstance(raw_devices, list):
        raise OSError("lsblk did not return a block-device inventory")
    by_path = {
        entry.get("path"): entry
        for entry in raw_devices
        if isinstance(entry, dict) and isinstance(entry.get("path"), str)
    }
    if set(by_path) != set(devicefiles):
        raise ValueError("one or more selected disks disappeared or resolved ambiguously")

    identities: list[dict[str, Any]] = []
    for devicefile in devicefiles:
        entry = by_path[devicefile]
        size = entry.get("size")
        if isinstance(size, bool):
            size = None
        try:
            size_bytes = int(size)
        except (TypeError, ValueError):
            size_bytes = 0
        if entry.get("type") != "disk" or size_bytes < _MIN_DISK_BYTES:
            raise ValueError(f"selected device is not a usable whole disk: {devicefile}")
        if entry.get("ro") in (True, 1, "1"):
            raise ValueError(f"selected disk is read-only: {devicefile}")
        if entry.get("rm") in (True, 1, "1"):
            raise ValueError(f"removable disks are not accepted for a native pool: {devicefile}")
        if entry.get("fstype") or entry.get("pttype") or entry.get("children"):
            raise ValueError(f"selected disk is not blank: {devicefile}")
        if _mountpoints(entry.get("mountpoints")):
            raise ValueError(f"selected disk is mounted: {devicefile}")
        serial = str(entry.get("serial") or "").strip()
        wwn = str(entry.get("wwn") or "").strip()
        if not serial and not wwn:
            raise ValueError(f"selected disk has no persistent serial or WWN: {devicefile}")
        signatures = _run_checked("wipefs", "--noheadings", "--output", "TYPE", devicefile)
        if signatures.strip():
            raise ValueError(
                f"selected disk still has a filesystem or RAID signature: {devicefile}"
            )
        identities.append(
            {
                "devicefile": devicefile,
                "sizeBytes": size_bytes,
                "serial": serial or None,
                "wwn": wwn or None,
                "model": str(entry.get("model") or "").strip() or None,
            }
        )
    return identities


def zfs_mirror_candidates() -> list[dict[str, Any]]:
    """List only disks that currently pass the same checks used by ``plan``.

    Returning an allow-list instead of every host disk keeps the UI from
    presenting the system disk as a plausible destructive target. Plan/apply
    still repeat all checks and bind identities; this inventory grants no
    authority by itself.
    """
    _require_tools()
    output = _run_checked("lsblk", "-J", "-p", "-o", "PATH,TYPE")
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as exc:
        raise OSError("lsblk returned invalid JSON") from exc
    raw_devices = payload.get("blockdevices") if isinstance(payload, dict) else None
    if not isinstance(raw_devices, list):
        raise OSError("lsblk did not return a block-device inventory")
    candidates: list[dict[str, Any]] = []
    for entry in raw_devices:
        if not isinstance(entry, dict) or entry.get("type") != "disk":
            continue
        devicefile = entry.get("path")
        if not isinstance(devicefile, str) or _WHOLE_DISK_PATTERN.fullmatch(devicefile) is None:
            continue
        try:
            identities = _inspect_zfs_mirror_devices([devicefile])
        except ValueError:
            continue
        candidates.extend(identities)
    return sorted(candidates, key=lambda item: item["devicefile"])


def _existing_zfs_pool_names() -> list[str]:
    output = _run_checked("zpool", "list", "-H", "-o", "name")
    names = [line.strip() for line in output.splitlines() if line.strip()]
    if any(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]*", name) is None for name in names):
        raise OSError("zpool returned an invalid pool name")
    return sorted(names)


def _mount_target_state(pool_name: str) -> dict[str, Any]:
    root = _ZFS_MOUNT_ROOT
    try:
        root_info = root.lstat()
    except FileNotFoundError as exc:
        raise OSError(f"native pool mount root does not exist: {root}") from exc
    if not stat_module.S_ISDIR(root_info.st_mode) or root.is_symlink():
        raise OSError(f"native pool mount root is not a safe directory: {root}")
    target = root / pool_name
    try:
        info = target.lstat()
    except FileNotFoundError:
        return {"path": str(target), "kind": "absent"}
    return {
        "path": str(target),
        "kind": "directory" if stat_module.S_ISDIR(info.st_mode) else "other",
    }


def _build_zfs_mirror_plan(desired: dict[str, Any]) -> dict[str, Any]:
    _require_tools()
    identities = _inspect_zfs_mirror_devices(desired["devices"])
    pools = _existing_zfs_pool_names()
    if desired["name"] in pools:
        raise ValueError("a ZFS pool with this name already exists")
    mount_target = _mount_target_state(desired["name"])
    if mount_target["kind"] != "absent":
        raise ValueError("the derived ZFS mountpoint already exists")
    base_revision = _canonical_hash(
        {"devices": identities, "pools": pools, "mountTarget": mount_target}
    )
    plan_material = {
        "schema": ZFS_MIRROR_PLAN_SCHEMA,
        "baseRevision": base_revision,
        "operation": "create",
        "desired": desired,
        "devices": identities,
        "mountpoint": mount_target["path"],
    }
    return {
        **plan_material,
        "planId": _canonical_hash(plan_material),
        "requiresApproval": True,
        "changes": [
            {
                "operation": "create",
                "resource": "zfsMirrorPool",
                "name": desired["name"],
            }
        ],
        "safety": {
            "destructive": True,
            "dataLossConfirmed": True,
            "layout": "twoDiskMirrorOnly",
            "devices": "wholeBlankNonRemovableWithPersistentIdentity",
            "force": False,
            "rollback": "bestEffortPoolDestroyBeforeHandoff",
            "unsupported": [
                "expand",
                "generalReplace",
                "destroy",
                "forceImport",
                "signatureWipe",
            ],
        },
        "source": "native",
    }


@contextmanager
def _pool_transaction() -> Iterator[None]:
    with _ZFS_THREAD_LOCK:
        _ZFS_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(_ZFS_LOCK_PATH, flags, 0o600)
        try:
            info = os.fstat(descriptor)
            if not stat_module.S_ISREG(info.st_mode):
                raise OSError("native ZFS lock is not a regular file")
            fchmod = getattr(os, "fchmod", None)
            if callable(fchmod):
                fchmod(descriptor, 0o600)
            try:
                import fcntl
            except ImportError:  # pragma: no cover - Windows tests use the thread lock
                fcntl = None  # type: ignore[assignment]
            if fcntl is not None:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            os.close(descriptor)


def _verify_created_pool(plan: dict[str, Any]) -> dict[str, Any]:
    name = plan["desired"]["name"]
    health = _run_checked("zpool", "list", "-H", "-o", "health", name).strip()
    if health != "ONLINE":
        raise OSError(f"new ZFS mirror is not ONLINE: {health or 'unknown'}")
    status = _run_checked("zpool", "status", "-P", "-L", name)
    if "mirror-0" not in status:
        raise OSError("new ZFS pool is not a mirror")
    for device in plan["devices"]:
        if not re.search(
            rf"^\s*{re.escape(device['devicefile'])}\s+ONLINE\b", status, re.MULTILINE
        ):
            raise OSError("new ZFS mirror did not retain every planned disk")
    properties = {
        prop: _run_checked("zfs", "get", "-H", "-o", "value", prop, name).strip()
        for prop in ("mountpoint", "compression", "atime", "xattr", "acltype")
    }
    expected = {
        "mountpoint": plan["mountpoint"],
        "compression": "lz4",
        "atime": "off",
        "xattr": "sa",
        "acltype": "posix",
    }
    if properties != expected:
        raise OSError("new ZFS pool properties failed read-back verification")
    return {"name": name, "health": health, "layout": "mirror", **properties}


def _pool_exists(name: str) -> bool:
    return name in _existing_zfs_pool_names()


def plan_zfs_mirror(desired_state: dict[str, Any]) -> dict[str, Any]:
    desired = validate_zfs_mirror_desired(dict(desired_state))
    with _pool_transaction():
        return _build_zfs_mirror_plan(desired)


def apply_zfs_mirror(desired_state: dict[str, Any], plan_id: str) -> dict[str, Any]:
    desired = validate_zfs_mirror_desired(dict(desired_state))
    with _pool_transaction():
        plan = _build_zfs_mirror_plan(desired)
        if plan["planId"] != plan_id:
            raise ValueError("ZFS mirror plan is stale; preview the change again")
        created = False
        try:
            _run_mutating(
                "zpool",
                "create",
                "-o",
                "ashift=12",
                "-O",
                "compression=lz4",
                "-O",
                "atime=off",
                "-O",
                "xattr=sa",
                "-O",
                "acltype=posixacl",
                "-O",
                f"mountpoint={plan['mountpoint']}",
                desired["name"],
                "mirror",
                *desired["devices"],
            )
            created = True
            pool = _verify_created_pool(plan)
        except Exception as exc:
            pool_exists = created
            if not pool_exists:
                try:
                    pool_exists = _pool_exists(desired["name"])
                except Exception as state_exc:
                    raise OSError(
                        "ZFS mirror creation failed and the resulting pool state is unknown"
                    ) from state_exc
            if pool_exists:
                try:
                    _run_mutating("zpool", "destroy", desired["name"])
                    if _pool_exists(desired["name"]):
                        raise OSError("ZFS pool rollback was not verified")
                except Exception as rollback_exc:
                    raise OSError(
                        "ZFS mirror creation failed and automatic pool rollback also failed"
                    ) from rollback_exc
            if isinstance(exc, (OSError, ValueError)):
                raise
            raise OSError("ZFS mirror creation failed") from exc
        return {**plan, "applied": True, "verified": True, "pool": pool}


def _valid_pool_guid(value: str) -> bool:
    return bool(_POOL_GUID_PATTERN.fullmatch(value) and 0 < int(value) <= 2**64 - 1)


def _imported_pool_snapshots() -> list[dict[str, Any]]:
    """Read stable identities for pools currently imported into the kernel."""
    output = _run_checked("zpool", "list", "-H", "-p", "-o", "name,guid,health,size")
    if not output.strip() or output.strip() == "no pools available":
        return []
    pools: list[dict[str, Any]] = []
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) != 4:
            raise OSError("zpool returned an invalid imported-pool inventory")
        name, guid, health, raw_size = parts
        if (
            _POOL_NAME_PATTERN.fullmatch(name) is None
            or not _valid_pool_guid(guid)
            or health not in _POOL_HEALTH
        ):
            raise OSError("zpool returned an unsafe imported-pool identity")
        try:
            size_bytes = int(raw_size)
        except ValueError as exc:
            raise OSError("zpool returned an invalid imported-pool size") from exc
        if size_bytes < 0:
            raise OSError("zpool returned an invalid imported-pool size")
        pools.append(
            {
                "name": name,
                "poolGuid": guid,
                "health": health,
                "sizeBytes": size_bytes,
            }
        )
    identities = [(pool["name"], pool["poolGuid"]) for pool in pools]
    if len(identities) != len(set(identities)):
        raise OSError("zpool returned duplicate imported-pool identities")
    return sorted(pools, key=lambda pool: (pool["name"], pool["poolGuid"]))


def _parse_importable_pools(output: str) -> list[dict[str, Any]]:
    """Parse the documented C-locale ``zpool import`` summary format."""
    if not output.strip() or "no pools available to import" in output.casefold():
        return []
    starts = list(re.finditer(r"(?m)^\s*pool:\s*(\S+)\s*$", output))
    if not starts:
        raise OSError("zpool import returned an unrecognized inventory")
    candidates: list[dict[str, Any]] = []
    for index, match in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(output)
        block = output[match.start() : end]
        name = match.group(1)
        guid_match = re.search(r"(?m)^\s*id:\s*([0-9]+)\s*$", block)
        state_match = re.search(r"(?m)^\s*state:\s*(\S+)\s*$", block)
        config_match = re.search(r"(?m)^\s*config:\s*$", block)
        if (
            _POOL_NAME_PATTERN.fullmatch(name) is None
            or guid_match is None
            or not _valid_pool_guid(guid_match.group(1))
            or state_match is None
            or state_match.group(1) not in _POOL_HEALTH
            or config_match is None
        ):
            raise OSError("zpool import returned an unsafe pool identity")
        action_is_safe = "can be imported using its name or numeric identifier" in block.casefold()
        config_lines = [
            line.rstrip() for line in block[config_match.end() :].splitlines() if line.strip()
        ]
        if not config_lines:
            raise OSError("zpool import omitted the pool configuration")
        config_text = "\n".join(config_lines)
        layout = (
            "raidz3"
            if re.search(r"(?m)^\s*raidz3(?:-\d+)?\s+", config_text)
            else "raidz2"
            if re.search(r"(?m)^\s*raidz2(?:-\d+)?\s+", config_text)
            else "raidz1"
            if re.search(r"(?m)^\s*raidz(?:1)?(?:-\d+)?\s+", config_text)
            else "mirror"
            if re.search(r"(?m)^\s*mirror(?:-\d+)?\s+", config_text)
            else "stripe"
        )
        candidates.append(
            {
                "name": name,
                "poolGuid": guid_match.group(1),
                "state": state_match.group(1),
                "layout": layout,
                "configHash": _canonical_hash(config_text),
                "safeToImport": action_is_safe and state_match.group(1) == "ONLINE",
            }
        )
    guids = [candidate["poolGuid"] for candidate in candidates]
    if len(guids) != len(set(guids)):
        raise OSError("zpool import returned duplicate pool GUIDs")
    return sorted(candidates, key=lambda pool: (pool["name"], pool["poolGuid"]))


def importable_zfs_pools() -> list[dict[str, Any]]:
    """List exported ONLINE pools that do not require a forced import."""
    _require_lifecycle_tools()
    candidates = _parse_importable_pools(_run_checked("zpool", "import", timeout=60.0))
    return [candidate for candidate in candidates if candidate["safeToImport"]]


def exportable_zfs_pools() -> list[dict[str, Any]]:
    """List imported pools that satisfy the non-force Echo export policy."""
    _require_lifecycle_tools()
    pools: list[dict[str, Any]] = []
    for pool in _imported_pool_snapshots():
        if pool["health"] != "ONLINE":
            continue
        status = _run_checked("zpool", "status", pool["name"])
        if "scrub in progress" in status or "resilver in progress" in status:
            continue
        try:
            policy = _dataset_mount_policy(
                pool["name"],
                _dataset_snapshots(pool["name"]),
                check_targets=False,
            )
        except ValueError:
            continue
        pools.append(
            {
                **pool,
                "rootMountpoint": policy["rootMountpoint"],
                "datasetCount": policy["datasetCount"],
                "mountedCount": policy["mountedCount"],
                "safeToExport": True,
            }
        )
    return pools


_STATUS_ROW_PATTERN = re.compile(
    r"^(?P<indent>[ \t]+)(?P<name>\S+)\s+"
    r"(?P<state>ONLINE|DEGRADED|FAULTED|OFFLINE|REMOVED|UNAVAIL)\s+"
    r"(?P<read>\d+|-)\s+(?P<write>\d+|-)\s+(?P<cksum>\d+|-)"
    r"(?:\s+(?P<detail>.*))?$"
)
_REPLACEABLE_VDEV_STATES = frozenset({"DEGRADED", "FAULTED", "OFFLINE", "REMOVED", "UNAVAIL"})


def _status_config_rows(output: str) -> list[dict[str, Any]]:
    config_match = re.search(r"(?m)^config:\s*$", output)
    errors_match = re.search(r"(?m)^errors:\s*", output)
    if config_match is None or errors_match is None or errors_match.start() <= config_match.end():
        raise OSError("zpool status omitted a bounded configuration table")
    rows: list[dict[str, Any]] = []
    for raw_line in output[config_match.end() : errors_match.start()].splitlines():
        line = raw_line.expandtabs(8).rstrip()
        match = _STATUS_ROW_PATTERN.fullmatch(line)
        if match is None:
            continue
        rows.append(
            {
                "indent": len(match.group("indent")),
                "name": match.group("name"),
                "state": match.group("state"),
                "readErrors": int(match.group("read")) if match.group("read").isdigit() else None,
                "writeErrors": (
                    int(match.group("write")) if match.group("write").isdigit() else None
                ),
                "checksumErrors": (
                    int(match.group("cksum")) if match.group("cksum").isdigit() else None
                ),
                "detail": match.group("detail") or "",
            }
        )
    if not rows:
        raise OSError("zpool status returned no configuration rows")
    return rows


def _zfs_scan_snapshot(output: str) -> dict[str, Any]:
    """Parse only the bounded maintenance facts exposed by ``zpool status``."""
    match = re.search(r"(?m)^\s*scan:\s*(?P<summary>[^\r\n]+)\s*$", output)
    if match is None:
        raise OSError("zpool status omitted the maintenance scan state")
    summary = match.group("summary").strip()
    folded = summary.casefold()
    kind = (
        "scrub"
        if "scrub" in folded
        else "resilver"
        if "resilver" in folded
        else "none"
        if folded == "none requested"
        else "unknown"
    )
    state = (
        "idle"
        if kind == "none"
        else "inProgress"
        if "in progress" in folded
        else "completed"
        if kind in {"scrub", "resilver"}
        and any(marker in folded for marker in ("repaired", "resilvered"))
        else "unknown"
    )
    progress: float | None = None
    if state == "inProgress":
        scan_end = re.search(r"(?m)^(?:config:|errors:)\s*", output[match.end() :])
        block_end = match.end() + scan_end.start() if scan_end else len(output)
        progress_match = re.search(
            r"(?P<percent>\d+(?:\.\d+)?)%\s+done\b",
            output[match.end() : block_end],
            re.IGNORECASE,
        )
        if progress_match is not None:
            parsed = float(progress_match.group("percent"))
            # OpenZFS documents that live-pool churn may push progress above 100%.
            progress = min(parsed, 100.0)
    errors_match = re.search(r"\bwith\s+(?P<errors>\d+)\s+errors?\b", summary, re.IGNORECASE)
    return {
        "kind": kind,
        "state": state,
        "progressPercent": progress,
        "errors": int(errors_match.group("errors")) if errors_match else None,
        "summaryHash": _canonical_hash(summary),
    }


def zfs_scan_snapshot(output: str) -> dict[str, Any]:
    """Public bounded parser shared by read-only topology and write workflows."""
    return _zfs_scan_snapshot(output)


def _device_size_bytes(devicefile: str) -> int:
    if (
        not devicefile.startswith("/dev/")
        or len(devicefile) > 128
        or any(character <= " " for character in devicefile)
    ):
        raise OSError("zpool status returned an unsafe online mirror device path")
    output = _run_checked("lsblk", "-b", "-d", "-n", "-o", "SIZE", devicefile).strip()
    if not output.isdigit() or int(output) < _MIN_DISK_BYTES:
        raise OSError("lsblk returned an invalid online mirror device size")
    return int(output)


def _mirror_replacement_topology(name: str, pool_guid: str) -> dict[str, Any]:
    pool = _pool_snapshot(name, pool_guid)
    if pool["health"] not in {"ONLINE", "DEGRADED"}:
        raise ValueError("ZFS mirror is not healthy enough for an online replacement")
    normal_status = _run_checked("zpool", "status", "-LP", name)
    guid_status = _run_checked("zpool", "status", "-gP", name)
    if any(
        marker in normal_status
        for marker in ("scrub in progress", "resilver in progress", "replacing-")
    ):
        raise ValueError("wait for the active ZFS maintenance operation before replacing a disk")
    if re.search(r"(?m)^errors:\s+No known data errors\s*$", normal_status) is None:
        raise ValueError("ZFS mirror reports known data errors and needs manual recovery review")
    normal_rows = _status_config_rows(normal_status)
    guid_rows = _status_config_rows(guid_status)
    if len(normal_rows) != 4 or len(guid_rows) != 4:
        raise ValueError("only a single two-disk ZFS mirror can use this replacement workflow")
    if [row["indent"] for row in normal_rows] != [row["indent"] for row in guid_rows]:
        raise OSError("zpool status GUID and path topologies do not agree")
    if [row["state"] for row in normal_rows] != [row["state"] for row in guid_rows]:
        raise OSError("zpool status GUID and path health do not agree")
    root, mirror, first, second = normal_rows
    _guid_root, _guid_mirror, guid_first, guid_second = guid_rows
    if (
        root["name"] != name
        or root["state"] != pool["health"]
        or not mirror["name"].startswith("mirror-")
        or not root["indent"] < mirror["indent"] < first["indent"]
        or first["indent"] != second["indent"]
    ):
        raise ValueError("only a single two-disk ZFS mirror can use this replacement workflow")
    members: list[dict[str, Any]] = []
    for index, (normal, guid) in enumerate(((first, guid_first), (second, guid_second)), start=1):
        if not _valid_pool_guid(guid["name"]):
            raise OSError("zpool status did not provide a canonical leaf vdev GUID")
        members.append(
            {
                "slot": index,
                "vdevGuid": guid["name"],
                "state": normal["state"],
            }
        )
    failed = [member for member in members if member["state"] in _REPLACEABLE_VDEV_STATES]
    online = [member for member in members if member["state"] == "ONLINE"]
    if len(failed) != 1 or len(online) != 1:
        raise ValueError(
            "replacement requires exactly one failed mirror member and one ONLINE member"
        )
    online_path = (first, second)[online[0]["slot"] - 1]["name"]
    online_row = (first, second)[online[0]["slot"] - 1]
    if any(online_row[field] != 0 for field in ("readErrors", "writeErrors", "checksumErrors")):
        raise ValueError("the surviving ZFS mirror member has I/O errors")
    minimum_replacement_bytes = _device_size_bytes(online_path)
    datasets = _dataset_snapshots(name)
    policy = _dataset_mount_policy(name, datasets, check_targets=False)
    return {
        "pool": pool,
        "layout": "twoDiskMirror",
        "members": members,
        "replaceableMember": failed[0],
        "minimumReplacementBytes": minimum_replacement_bytes,
        "datasetPolicyHash": policy["policyHash"],
        "topologyHash": _canonical_hash(
            {
                "normalStatus": normal_status,
                "guidStatus": guid_status,
                "datasetPolicyHash": policy["policyHash"],
            }
        ),
    }


def zfs_mirror_replacement_candidates() -> list[dict[str, Any]]:
    """Return degraded two-disk mirrors and blank disks large enough to repair them."""
    _require_tools()
    blank_devices = zfs_mirror_candidates()
    candidates: list[dict[str, Any]] = []
    for pool in _imported_pool_snapshots():
        if pool["health"] not in {"ONLINE", "DEGRADED"}:
            continue
        try:
            topology = _mirror_replacement_topology(pool["name"], pool["poolGuid"])
        except ValueError:
            continue
        compatible = [
            device
            for device in blank_devices
            if device["sizeBytes"] >= topology["minimumReplacementBytes"]
        ]
        candidates.append(
            {
                "pool": topology["pool"],
                "layout": topology["layout"],
                "replaceableMember": topology["replaceableMember"],
                "minimumReplacementBytes": topology["minimumReplacementBytes"],
                "replacementDevices": compatible,
            }
        )
    return candidates


def _build_zfs_mirror_replace_plan(desired: dict[str, Any]) -> dict[str, Any]:
    _require_tools()
    topology = _mirror_replacement_topology(desired["name"], desired["poolGuid"])
    if topology["replaceableMember"]["vdevGuid"] != desired["oldVdevGuid"]:
        raise ValueError("the selected failed ZFS vdev no longer matches this mirror")
    replacement = _inspect_zfs_mirror_devices([desired["replacementDevice"]])[0]
    if replacement["sizeBytes"] < topology["minimumReplacementBytes"]:
        raise ValueError("replacement disk is smaller than the ONLINE mirror member")
    base_revision = _canonical_hash(
        {
            "pool": topology["pool"],
            "topologyHash": topology["topologyHash"],
            "replacement": replacement,
        }
    )
    plan_material = {
        "schema": ZFS_MIRROR_REPLACE_PLAN_SCHEMA,
        "baseRevision": base_revision,
        "operation": "replace",
        "desired": desired,
        "pool": topology["pool"],
        "failedMember": topology["replaceableMember"],
        "replacement": replacement,
        "minimumReplacementBytes": topology["minimumReplacementBytes"],
    }
    return {
        **plan_material,
        "planId": _canonical_hash(plan_material),
        "requiresApproval": True,
        "changes": [
            {
                "field": "mirrorMember",
                "before": topology["replaceableMember"]["vdevGuid"],
                "after": replacement["devicefile"],
            }
        ],
        "safety": {
            "data": "preservedDuringResilver",
            "scope": "singleTwoDiskMirrorOnly",
            "target": "failedLeafVdevGuid",
            "replacement": "wholeBlankNonRemovableWithPersistentIdentity",
            "minimumSize": "onlineSiblingDeviceSize",
            "force": False,
            "sequentialReconstruction": False,
            "wait": False,
            "activeMaintenance": "mustBeAbsent",
            "rollback": "noneAfterReplacementAccepted",
        },
        "source": "native",
    }


def plan_zfs_mirror_replace(desired_state: dict[str, Any]) -> dict[str, Any]:
    desired = validate_zfs_mirror_replace_desired(dict(desired_state))
    with _pool_transaction():
        return _build_zfs_mirror_replace_plan(desired)


def _replacement_path_present(status: str, devicefile: str) -> bool:
    pattern = re.compile(rf"^{re.escape(devicefile)}(?:p?[0-9]+)?$")
    return any(pattern.fullmatch(row["name"]) for row in _status_config_rows(status))


def apply_zfs_mirror_replace(desired_state: dict[str, Any], plan_id: str) -> dict[str, Any]:
    desired = validate_zfs_mirror_replace_desired(dict(desired_state))
    with _pool_transaction():
        plan = _build_zfs_mirror_replace_plan(desired)
        if plan["planId"] != plan_id:
            raise ValueError("ZFS mirror replacement plan is stale; preview the change again")
        try:
            _run_mutating(
                "zpool",
                "replace",
                desired["name"],
                desired["oldVdevGuid"],
                desired["replacementDevice"],
            )
        except Exception:
            try:
                status = _run_checked("zpool", "status", "-LP", desired["name"])
            except Exception as state_exc:
                raise OSError(
                    "ZFS replacement command failed and the resulting pool state is unknown"
                ) from state_exc
            if not _replacement_path_present(status, desired["replacementDevice"]):
                raise
        else:
            status = _run_checked("zpool", "status", "-LP", desired["name"])
        if not _replacement_path_present(status, desired["replacementDevice"]):
            raise OSError("ZFS did not retain the planned replacement disk")
        pool = _pool_snapshot(desired["name"], desired["poolGuid"])
        maintenance = "resilvering" if "resilver in progress" in status else "acceptedOrCompleted"
        return {
            **plan,
            "applied": True,
            "verified": True,
            "dataPreserved": True,
            "maintenanceState": maintenance,
            "pool": pool,
        }


def _pool_snapshot(name: str, guid: str) -> dict[str, Any]:
    matches = [
        pool
        for pool in _imported_pool_snapshots()
        if pool["name"] == name and pool["poolGuid"] == guid
    ]
    if len(matches) != 1:
        raise ValueError("ZFS pool name/GUID does not match one imported pool")
    return matches[0]


def _dataset_snapshots(pool_name: str) -> list[dict[str, str]]:
    output = _run_checked(
        "zfs",
        "list",
        "-H",
        "-p",
        "-t",
        "filesystem",
        "-o",
        "name,mountpoint,canmount,mounted,encryption",
        "-r",
        pool_name,
    )
    datasets: list[dict[str, str]] = []
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) != 5:
            raise OSError("zfs returned an invalid dataset inventory")
        name, mountpoint, canmount, mounted, encryption = parts
        if (
            not (name == pool_name or name.startswith(f"{pool_name}/"))
            or any(character < " " for character in mountpoint)
            or canmount not in {"on", "off", "noauto"}
            or mounted not in {"yes", "no"}
        ):
            raise OSError("zfs returned unsafe dataset metadata")
        datasets.append(
            {
                "name": name,
                "mountpoint": mountpoint,
                "canmount": canmount,
                "mounted": mounted,
                "encryption": encryption,
            }
        )
    if not datasets or sum(dataset["name"] == pool_name for dataset in datasets) != 1:
        raise OSError("ZFS pool root dataset is absent or ambiguous")
    return sorted(datasets, key=lambda dataset: (dataset["name"].count("/"), dataset["name"]))


def _mount_target_is_safe(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return True
    if not stat_module.S_ISDIR(info.st_mode) or path.is_symlink() or os.path.ismount(path):
        return False
    try:
        return next(path.iterdir(), None) is None
    except OSError:
        return False


def _dataset_mount_policy(
    pool_name: str,
    datasets: list[dict[str, str]],
    *,
    check_targets: bool,
) -> dict[str, Any]:
    root = _ZFS_MOUNT_ROOT / pool_name
    root_text = str(root)
    mountable: list[str] = []
    mounted = 0
    for dataset in datasets:
        if dataset["encryption"] != "off":
            raise ValueError("encrypted ZFS datasets require a separate key-management workflow")
        mountpoint = dataset["mountpoint"]
        if dataset["name"] == pool_name and mountpoint != root_text:
            raise ValueError("ZFS pool root mountpoint is outside the Echo data root")
        if mountpoint not in {"none", "legacy", "-"}:
            if not os.path.isabs(mountpoint) or os.path.normpath(mountpoint) != mountpoint:
                raise ValueError("ZFS dataset has a non-canonical mountpoint")
            if mountpoint != root_text and not mountpoint.startswith(f"{root_text}{os.sep}"):
                raise ValueError("ZFS dataset mountpoint is outside the Echo pool root")
            if dataset["canmount"] == "on":
                if check_targets and not _mount_target_is_safe(Path(mountpoint)):
                    raise ValueError("ZFS dataset mountpoint is occupied or unsafe")
                mountable.append(dataset["name"])
        if dataset["mounted"] == "yes":
            mounted += 1
    policy_material = [
        {
            "name": dataset["name"],
            "mountpoint": dataset["mountpoint"],
            "canmount": dataset["canmount"],
            "encryption": dataset["encryption"],
        }
        for dataset in datasets
    ]
    return {
        "rootMountpoint": root_text,
        "datasetCount": len(datasets),
        "mountedCount": mounted,
        "mountableDatasets": mountable,
        "policyHash": _canonical_hash(policy_material),
    }


def zfs_pool_maintenance() -> list[dict[str, Any]]:
    """Return scrub/resilver state for imported pools using the Echo mount policy."""
    _require_lifecycle_tools()
    maintenance: list[dict[str, Any]] = []
    for pool in _imported_pool_snapshots():
        try:
            policy = _dataset_mount_policy(
                pool["name"],
                _dataset_snapshots(pool["name"]),
                check_targets=False,
            )
        except ValueError:
            continue
        status = _run_checked("zpool", "status", "-P", pool["name"])
        scan = _zfs_scan_snapshot(status)
        maintenance.append(
            {
                "pool": pool,
                "rootMountpoint": policy["rootMountpoint"],
                "scan": scan,
                "canStartScrub": pool["health"] == "ONLINE"
                and scan["state"] in {"idle", "completed"},
            }
        )
    return maintenance


def _build_zfs_scrub_plan(desired: dict[str, Any]) -> dict[str, Any]:
    _require_lifecycle_tools()
    pool = _pool_snapshot(desired["name"], desired["poolGuid"])
    if pool["health"] != "ONLINE":
        raise ValueError("only an ONLINE ZFS pool can start a scrub")
    policy = _dataset_mount_policy(
        pool["name"],
        _dataset_snapshots(pool["name"]),
        check_targets=False,
    )
    status = _run_checked("zpool", "status", "-P", pool["name"])
    scan = _zfs_scan_snapshot(status)
    if scan["state"] not in {"idle", "completed"}:
        raise ValueError("wait for the active or unknown ZFS maintenance operation")
    status_hash = _canonical_hash(status)
    base_revision = _canonical_hash(
        {
            "pool": pool,
            "datasetPolicyHash": policy["policyHash"],
            "statusHash": status_hash,
        }
    )
    plan_material = {
        "schema": ZFS_SCRUB_PLAN_SCHEMA,
        "baseRevision": base_revision,
        "operation": "start",
        "desired": desired,
        "pool": pool,
        "before": scan,
    }
    return {
        **plan_material,
        "planId": _canonical_hash(plan_material),
        "requiresApproval": True,
        "changes": [{"field": "maintenance", "before": scan["state"], "after": "scrub"}],
        "safety": {
            "data": "checksummedAndRepairableReplicasMayBeRepaired",
            "poolState": "onlineOnly",
            "mounts": "echoDataRootOnly",
            "activeMaintenance": "mustBeAbsent",
            "ioLoad": "high",
            "wait": False,
            "pause": False,
            "stop": False,
            "rollback": "noneAfterScrubAccepted",
        },
        "source": "native",
        "_statusHash": status_hash,
    }


def plan_zfs_scrub(desired_state: dict[str, Any]) -> dict[str, Any]:
    desired = validate_zfs_scrub_desired(dict(desired_state))
    with _pool_transaction():
        plan = _build_zfs_scrub_plan(desired)
        plan.pop("_statusHash", None)
        return plan


def _verified_scrub_transition(
    status: str, *, previous_status_hash: str
) -> tuple[dict[str, Any], str] | None:
    if _canonical_hash(status) == previous_status_hash:
        return None
    scan = _zfs_scan_snapshot(status)
    if scan["kind"] != "scrub" or scan["state"] not in {"inProgress", "completed"}:
        return None
    return scan, "scrubbing" if scan["state"] == "inProgress" else "completed"


def apply_zfs_scrub(desired_state: dict[str, Any], plan_id: str) -> dict[str, Any]:
    desired = validate_zfs_scrub_desired(dict(desired_state))
    with _pool_transaction():
        plan = _build_zfs_scrub_plan(desired)
        if plan["planId"] != plan_id:
            raise ValueError("ZFS scrub plan is stale; preview the change again")
        previous_status_hash = plan.pop("_statusHash")
        command_error: Exception | None = None
        try:
            _run_mutating("zpool", "scrub", desired["name"])
        except Exception as exc:
            command_error = exc
        try:
            status = _run_checked("zpool", "status", "-P", desired["name"])
            transition = _verified_scrub_transition(
                status,
                previous_status_hash=previous_status_hash,
            )
        except Exception as state_exc:
            if command_error is not None:
                raise OSError(
                    "ZFS scrub command failed and the resulting pool state is unknown"
                ) from state_exc
            raise
        if transition is None:
            if command_error is not None:
                raise command_error
            raise OSError("ZFS did not report the planned scrub after accepting the command")
        scan, maintenance_state = transition
        return {
            **plan,
            "applied": True,
            "verified": True,
            "maintenanceState": maintenance_state,
            "scan": scan,
            "pool": _pool_snapshot(desired["name"], desired["poolGuid"]),
        }


def _build_zfs_pool_export_plan(
    desired: dict[str, Any], managed_dependencies: list[dict[str, str]]
) -> dict[str, Any]:
    _require_lifecycle_tools()
    pool = _pool_snapshot(desired["name"], desired["poolGuid"])
    if pool["health"] != "ONLINE":
        raise ValueError("only an ONLINE ZFS pool can be safely exported")
    if managed_dependencies:
        raise ValueError("detach every Echo shared folder before exporting this ZFS pool")
    status = _run_checked("zpool", "status", pool["name"])
    if "scrub in progress" in status or "resilver in progress" in status:
        raise ValueError("wait for the active ZFS maintenance operation before exporting")
    datasets = _dataset_snapshots(pool["name"])
    policy = _dataset_mount_policy(pool["name"], datasets, check_targets=False)
    dependencies = sorted(
        managed_dependencies, key=lambda item: (item.get("name", ""), item.get("uuid", ""))
    )
    base_revision = _canonical_hash(
        {
            "pool": pool,
            "datasetPolicyHash": policy["policyHash"],
            "mountedCount": policy["mountedCount"],
            "dependencies": dependencies,
            "status": _canonical_hash(status),
        }
    )
    plan_material = {
        "schema": ZFS_POOL_EXPORT_PLAN_SCHEMA,
        "baseRevision": base_revision,
        "operation": "export",
        "desired": desired,
        "pool": pool,
        "datasetCount": policy["datasetCount"],
        "mountedCount": policy["mountedCount"],
    }
    return {
        **plan_material,
        "planId": _canonical_hash(plan_material),
        "requiresApproval": True,
        "changes": [{"field": "availability", "before": "imported", "after": "exported"}],
        "safety": {
            "data": "preserved",
            "force": False,
            "poolState": "onlineOnly",
            "mounts": "echoDataRootOnly",
            "dependentShares": "mustBeDetached",
            "activeMaintenance": "mustBeAbsent",
            "rollback": "notAttemptedAfterConfirmedExport",
        },
        "source": "native",
    }


def plan_zfs_pool_export(
    desired_state: dict[str, Any], managed_dependencies: list[dict[str, str]]
) -> dict[str, Any]:
    desired = validate_zfs_pool_export_desired(dict(desired_state))
    with _pool_transaction():
        return _build_zfs_pool_export_plan(desired, managed_dependencies)


def apply_zfs_pool_export(
    desired_state: dict[str, Any],
    plan_id: str,
    managed_dependencies: list[dict[str, str]],
) -> dict[str, Any]:
    desired = validate_zfs_pool_export_desired(dict(desired_state))
    with _pool_transaction():
        plan = _build_zfs_pool_export_plan(desired, managed_dependencies)
        if plan["planId"] != plan_id:
            raise ValueError("ZFS pool export plan is stale; preview the change again")
        _run_mutating("zpool", "sync", desired["name"])
        _run_mutating("zpool", "export", desired["name"])
        if any(pool["poolGuid"] == desired["poolGuid"] for pool in _imported_pool_snapshots()):
            raise OSError("ZFS pool remained imported after export")
        candidates = importable_zfs_pools()
        if not any(
            candidate["name"] == desired["name"] and candidate["poolGuid"] == desired["poolGuid"]
            for candidate in candidates
        ):
            raise OSError("exported ZFS pool was not rediscovered by GUID")
        return {
            **plan,
            "applied": True,
            "verified": True,
            "dataPreserved": True,
            "pool": {
                **plan["pool"],
                "availability": "exported",
            },
        }


def _build_zfs_pool_import_plan(desired: dict[str, Any]) -> dict[str, Any]:
    _require_lifecycle_tools()
    imported = _imported_pool_snapshots()
    if any(
        pool["name"] == desired["name"] or pool["poolGuid"] == desired["poolGuid"]
        for pool in imported
    ):
        raise ValueError("the requested ZFS pool name or GUID is already imported")
    matches = [
        candidate
        for candidate in importable_zfs_pools()
        if candidate["name"] == desired["name"] and candidate["poolGuid"] == desired["poolGuid"]
    ]
    if len(matches) != 1:
        raise ValueError("ZFS pool name/GUID does not match one safe import candidate")
    candidate = matches[0]
    base_revision = _canonical_hash(
        {
            "candidate": candidate,
            "imported": [{"name": pool["name"], "poolGuid": pool["poolGuid"]} for pool in imported],
        }
    )
    plan_material = {
        "schema": ZFS_POOL_IMPORT_PLAN_SCHEMA,
        "baseRevision": base_revision,
        "operation": "import",
        "desired": desired,
        "candidate": candidate,
    }
    return {
        **plan_material,
        "planId": _canonical_hash(plan_material),
        "requiresApproval": True,
        "changes": [{"field": "availability", "before": "exported", "after": "imported"}],
        "safety": {
            "data": "preserved",
            "identity": "guidBound",
            "force": False,
            "recoveryFlags": False,
            "destroyedPools": False,
            "inspection": "readOnlyNoMountBeforeWritableImport",
            "mounts": "echoDataRootOnly",
            "encryption": "notYetSupported",
            "rollback": "exportOnFailure",
        },
        "source": "native",
    }


def plan_zfs_pool_import(desired_state: dict[str, Any]) -> dict[str, Any]:
    desired = validate_zfs_pool_import_desired(dict(desired_state))
    with _pool_transaction():
        return _build_zfs_pool_import_plan(desired)


def _inspect_imported_pool_policy(
    desired: dict[str, Any], *, check_targets: bool
) -> dict[str, Any]:
    pool = _pool_snapshot(desired["name"], desired["poolGuid"])
    if pool["health"] != "ONLINE":
        raise ValueError("imported ZFS pool is not ONLINE")
    datasets = _dataset_snapshots(pool["name"])
    return {
        "pool": pool,
        "datasets": datasets,
        "policy": _dataset_mount_policy(pool["name"], datasets, check_targets=check_targets),
    }


def apply_zfs_pool_import(desired_state: dict[str, Any], plan_id: str) -> dict[str, Any]:
    desired = validate_zfs_pool_import_desired(dict(desired_state))
    with _pool_transaction():
        plan = _build_zfs_pool_import_plan(desired)
        if plan["planId"] != plan_id:
            raise ValueError("ZFS pool import plan is stale; preview the change again")

        readonly_imported = False
        try:
            try:
                _run_mutating(
                    "zpool",
                    "import",
                    "-N",
                    "-o",
                    "readonly=on",
                    desired["poolGuid"],
                )
            except Exception:
                try:
                    readonly_imported = any(
                        pool["poolGuid"] == desired["poolGuid"]
                        for pool in _imported_pool_snapshots()
                    )
                except Exception as state_exc:
                    raise OSError(
                        "ZFS read-only import failed and the resulting pool state is unknown"
                    ) from state_exc
                raise
            readonly_imported = True
            inspection = _inspect_imported_pool_policy(desired, check_targets=True)
        finally:
            if readonly_imported:
                try:
                    _run_mutating("zpool", "export", desired["name"])
                    if any(
                        pool["poolGuid"] == desired["poolGuid"]
                        for pool in _imported_pool_snapshots()
                    ):
                        raise OSError("read-only inspected pool remained imported")
                except Exception as export_exc:
                    raise OSError(
                        "ZFS read-only import inspection could not restore the exported state"
                    ) from export_exc

        refreshed = _build_zfs_pool_import_plan(desired)
        if refreshed["planId"] != plan_id:
            raise ValueError("ZFS pool import candidate changed during inspection; preview again")

        imported = False
        try:
            _run_mutating("zpool", "import", "-N", desired["poolGuid"])
            imported = True
            current = _inspect_imported_pool_policy(desired, check_targets=True)
            if current["policy"]["policyHash"] != inspection["policy"]["policyHash"]:
                raise ValueError("ZFS dataset mount policy changed during import")
            mountpoints = {
                dataset["name"]: dataset["mountpoint"] for dataset in current["datasets"]
            }
            for dataset in current["policy"]["mountableDatasets"]:
                if not _mount_target_is_safe(Path(mountpoints[dataset])):
                    raise ValueError("ZFS dataset mountpoint became occupied during ordered import")
                _run_mutating("zfs", "mount", dataset)
            verified = _inspect_imported_pool_policy(desired, check_targets=False)
            mounted_by_name = {
                dataset["name"]: dataset["mounted"] for dataset in verified["datasets"]
            }
            if any(
                mounted_by_name.get(dataset) != "yes"
                for dataset in current["policy"]["mountableDatasets"]
            ):
                raise OSError("one or more ZFS datasets failed mount verification")
        except Exception as exc:
            if not imported:
                try:
                    imported = any(
                        pool["poolGuid"] == desired["poolGuid"]
                        for pool in _imported_pool_snapshots()
                    )
                except Exception as state_exc:
                    raise OSError(
                        "ZFS pool import failed and the resulting pool state is unknown"
                    ) from state_exc
            if imported:
                try:
                    _run_mutating("zpool", "export", desired["name"])
                    if any(
                        pool["poolGuid"] == desired["poolGuid"]
                        for pool in _imported_pool_snapshots()
                    ):
                        raise OSError("failed import rollback left the pool imported")
                except Exception as rollback_exc:
                    raise OSError(
                        "ZFS pool import failed and export rollback also failed"
                    ) from rollback_exc
            if isinstance(exc, (OSError, ValueError)):
                raise
            raise OSError("ZFS pool import failed") from exc

        return {
            **plan,
            "applied": True,
            "verified": True,
            "dataPreserved": True,
            "pool": {
                **verified["pool"],
                "availability": "imported",
                "datasetCount": verified["policy"]["datasetCount"],
                "mountedCount": len(current["policy"]["mountableDatasets"]),
            },
        }


__all__ = [
    "apply_zfs_scrub",
    "apply_zfs_mirror_replace",
    "apply_zfs_mirror",
    "apply_zfs_pool_export",
    "apply_zfs_pool_import",
    "exportable_zfs_pools",
    "importable_zfs_pools",
    "plan_zfs_mirror",
    "plan_zfs_mirror_replace",
    "plan_zfs_pool_export",
    "plan_zfs_pool_import",
    "plan_zfs_scrub",
    "zfs_mirror_candidates",
    "zfs_mirror_replacement_candidates",
    "zfs_pool_maintenance",
    "zfs_scan_snapshot",
]
