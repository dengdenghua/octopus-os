"""Destructive native storage-pool operations with fail-closed disk binding.

Only one deliberately narrow operation lives here: create a two-disk ZFS
mirror from whole, blank, non-removable disks.  The caller must preview a
deterministic plan and apply that exact plan through the appliance approval
and audit envelope.  Expansion, replacement, destroy, force-import, and
signature wiping are intentionally not implemented.
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

from appliance.omv_protocol import ZFS_MIRROR_PLAN_SCHEMA, validate_zfs_mirror_desired

_MIN_DISK_BYTES = 1024**3
_ZFS_MOUNT_ROOT = Path("/data")
_ZFS_LOCK_PATH = Path("/run/lock/echo-os-zfs-pool.lock")
_ZFS_THREAD_LOCK = threading.RLock()
_WHOLE_DISK_PATTERN = re.compile(
    r"/dev/(?:sd[a-z]+|vd[a-z]+|xvd[a-z]+|nvme\d+n\d+|mmcblk\d+)"
)


def _require_tools() -> None:
    missing = [name for name in ("zpool", "zfs", "lsblk", "wipefs") if shutil.which(name) is None]
    if missing:
        raise OSError(f"native ZFS mirror tools are unavailable: {', '.join(missing)}")


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
            raise ValueError(f"selected disk still has a filesystem or RAID signature: {devicefile}")
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
            "unsupported": ["expand", "replace", "destroy", "forceImport", "signatureWipe"],
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
        if not re.search(rf"^\s*{re.escape(device['devicefile'])}\s+ONLINE\b", status, re.MULTILINE):
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


__all__ = ["apply_zfs_mirror", "plan_zfs_mirror", "zfs_mirror_candidates"]
