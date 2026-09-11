#!/usr/bin/env python3
"""Fail closed unless an operations output targets a separate active mount."""

from __future__ import annotations

import argparse
import os
import re
import shlex
import shutil
import stat
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

MAX_ENV_BYTES = 64 * 1024
MAX_MOUNTINFO_BYTES = 4 * 1024 * 1024
MOUNT_ESCAPE = re.compile(r"\\([0-7]{3})")
UNSAFE_FILESYSTEMS = {
    "autofs",
    "cgroup",
    "cgroup2",
    "debugfs",
    "devtmpfs",
    "efivarfs",
    "fusectl",
    "hugetlbfs",
    "mqueue",
    "overlay",
    "proc",
    "pstore",
    "ramfs",
    "securityfs",
    "squashfs",
    "sysfs",
    "tmpfs",
    "tracefs",
}
DEFAULT_CANDIDATE_ROOTS = (Path("/mnt"), Path("/media"), Path("/run/media"))
MAX_CANDIDATE_MOUNTS = 64


class ExternalStorageError(RuntimeError):
    """The destination is not a safe independent operations mount."""


def _unescape_mount_field(value: str) -> str:
    return MOUNT_ESCAPE.sub(lambda match: chr(int(match.group(1), 8)), value)


def _safe_directory(path: Path, label: str) -> Path:
    if not path.is_absolute() or any(part in {".", ".."} for part in path.parts):
        raise ExternalStorageError(f"{label} must be an absolute normalized path")
    cursor = Path(path.anchor)
    for part in path.parts[1:]:
        cursor /= part
        if cursor.is_symlink():
            raise ExternalStorageError(f"{label} must not contain a symbolic link")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ExternalStorageError(f"{label} is unavailable") from exc
    if not resolved.is_dir():
        raise ExternalStorageError(f"{label} is not a directory")
    return resolved


def _read_regular(path: Path, *, maximum: int, label: str) -> str:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ExternalStorageError(f"{label} cannot be opened safely") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or not 0 <= before.st_size <= maximum:
            raise ExternalStorageError(f"{label} is not a bounded regular file")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, maximum - total + 1))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum:
                raise ExternalStorageError(f"{label} exceeds its size limit")
        after = os.fstat(descriptor)
        if (before.st_dev, before.st_ino, before.st_mode) != (
            after.st_dev,
            after.st_ino,
            after.st_mode,
        ):
            raise ExternalStorageError(f"{label} changed while it was being read")
        return b"".join(chunks).decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise ExternalStorageError(f"{label} cannot be read safely") from exc
    finally:
        os.close(descriptor)


def _read_env_value(path: Path | None, key: str) -> str | None:
    if path is None or not path.exists():
        return None
    text = _read_regular(
        path,
        maximum=MAX_ENV_BYTES,
        label="appliance environment file",
    )
    found: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, raw_value = line.split("=", 1)
        if name.strip() != key:
            continue
        if found is not None:
            raise ExternalStorageError(f"appliance environment declares {key} more than once")
        value = raw_value.strip()
        if value[:1] in {'"', "'"}:
            try:
                fields = shlex.split(value, posix=True)
            except ValueError as exc:
                raise ExternalStorageError(f"appliance environment has invalid {key}") from exc
            if len(fields) != 1:
                raise ExternalStorageError(f"appliance environment has invalid {key}")
            value = fields[0]
        if not value or any(marker in value for marker in ("\x00", "\n", "\r", "$", "`")):
            raise ExternalStorageError(f"appliance environment has unsafe {key}")
        found = value
    return found


def _nas_root(deployment_root: Path, appliance_env: Path | None) -> Path:
    configured = os.environ.get("NAS_STORAGE") or _read_env_value(appliance_env, "NAS_STORAGE")
    if configured is None:
        return deployment_root / "storage"
    candidate = Path(configured)
    return candidate if candidate.is_absolute() else deployment_root / candidate


def _mount_rows(mountinfo: Path) -> list[dict[str, Any]]:
    text = _read_regular(mountinfo, maximum=MAX_MOUNTINFO_BYTES, label="mount table")
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        before, separator, after = line.partition(" - ")
        fields = before.split()
        trailing = after.split()
        if not separator or len(fields) < 6 or len(trailing) < 3:
            raise ExternalStorageError("mount table contains a malformed record")
        rows.append(
            {
                "mountpoint": _unescape_mount_field(fields[4]),
                "options": frozenset(fields[5].split(",")),
                "filesystem": trailing[0],
                "source": _unescape_mount_field(trailing[1]),
            }
        )
    return rows


def _mount_entry(
    mountinfo: Path,
    expected: Path,
    *,
    rows: Sequence[dict[str, Any]] | None = None,
) -> tuple[str, str]:
    matches = [
        (str(row["filesystem"]), str(row["source"]))
        for row in (_mount_rows(mountinfo) if rows is None else rows)
        if row["mountpoint"] == str(expected)
    ]
    if not matches:
        raise ExternalStorageError("declared operations mount is not currently mounted")
    return matches[-1]


def _candidate_kind(filesystem: str) -> str:
    normalized = filesystem.casefold()
    if normalized.startswith("fuse.") or normalized in {
        "9p",
        "cifs",
        "nfs",
        "nfs4",
        "smb3",
    }:
        return "remote"
    return "local"


def list_external_storage_mounts(
    *,
    deployment_root: Path,
    appliance_env: Path | None,
    state_root_override: Path | None = None,
    nas_root_override: Path | None = None,
    mountinfo: Path = Path("/proc/self/mountinfo"),
    device_reader: Callable[[Path], int] | None = None,
    disk_usage_reader: Callable[[Path], Any] = shutil.disk_usage,
    candidate_roots: Sequence[Path] = DEFAULT_CANDIDATE_ROOTS,
) -> dict[str, Any]:
    """List writable external mounts that pass the backup safety boundary.

    Discovery is deliberately narrower than verification: only conventional
    removable/remote mount roots are shown, mount sources are never returned,
    and every row must independently pass ``verify_external_storage``.  The
    selected repository directory is still reverified during plan/apply.
    """

    roots = tuple(Path(root) for root in candidate_roots)
    if not roots or any(not root.is_absolute() or root == Path("/") for root in roots):
        raise ExternalStorageError("external storage candidate roots are invalid")

    # Keep the final record for an over-mounted path, matching the kernel's
    # effective view and ``_mount_entry`` above.
    mount_rows = _mount_rows(mountinfo)
    effective: dict[str, dict[str, Any]] = {}
    for row in mount_rows:
        effective[str(row["mountpoint"])] = row

    candidates: list[dict[str, Any]] = []
    valid_count = 0
    for mountpoint_text, row in sorted(effective.items()):
        mountpoint = Path(mountpoint_text)
        if (
            not mountpoint.is_absolute()
            or len(mountpoint_text) > 4096
            or any(ord(character) < 32 or character == "\x7f" for character in mountpoint_text)
            or not any(mountpoint == root or root in mountpoint.parents for root in roots)
            or "rw" not in row["options"]
        ):
            continue
        try:
            verified = verify_external_storage(
                destination=mountpoint,
                mountpoint=mountpoint,
                deployment_root=deployment_root,
                appliance_env=appliance_env,
                state_root_override=state_root_override,
                nas_root_override=nas_root_override,
                mountinfo=mountinfo,
                device_reader=device_reader,
                mount_rows=mount_rows,
            )
            usage = disk_usage_reader(mountpoint)
            total = int(usage.total)
            free = int(usage.free)
            if total < 0 or free < 0 or free > total:
                raise ValueError("invalid filesystem capacity")
        except (ExternalStorageError, OSError, ValueError):
            continue
        valid_count += 1
        if len(candidates) >= MAX_CANDIDATE_MOUNTS:
            continue
        candidates.append(
            {
                "mountpoint": verified["mountpoint"],
                "filesystem": verified["filesystem"],
                "kind": _candidate_kind(verified["filesystem"]),
                "totalBytes": total,
                "freeBytes": free,
                "writable": True,
            }
        )
    return {
        "schema": "echo.external-storage-candidates.v1",
        "candidates": candidates,
        "truncated": valid_count > len(candidates),
        "sourcesRedacted": True,
    }


def verify_external_storage(
    *,
    destination: Path,
    mountpoint: Path,
    deployment_root: Path,
    appliance_env: Path | None,
    state_root_override: Path | None = None,
    nas_root_override: Path | None = None,
    mountinfo: Path = Path("/proc/self/mountinfo"),
    device_reader: Callable[[Path], int] | None = None,
    mount_rows: Sequence[dict[str, Any]] | None = None,
) -> dict[str, str]:
    destination = _safe_directory(destination, "operations destination")
    mountpoint = _safe_directory(mountpoint, "operations mountpoint")
    deployment_root = _safe_directory(deployment_root, "deployment root")
    state_root = _safe_directory(
        deployment_root / "data" if state_root_override is None else state_root_override,
        "device state root",
    )
    configured_nas_root = (
        _nas_root(deployment_root, appliance_env)
        if nas_root_override is None
        else nas_root_override
    )
    nas_root = _safe_directory(configured_nas_root, "NAS data root")
    if mountpoint == Path("/") or not (
        destination == mountpoint or mountpoint in destination.parents
    ):
        raise ExternalStorageError("operations destination is outside its declared non-root mount")
    filesystem, source = _mount_entry(mountinfo, mountpoint, rows=mount_rows)
    if filesystem.casefold() in UNSAFE_FILESYSTEMS:
        raise ExternalStorageError("operations mount uses a volatile or system filesystem")
    read_device = device_reader or (lambda path: path.stat().st_dev)
    try:
        destination_device = read_device(destination)
        if read_device(mountpoint) != destination_device:
            raise ExternalStorageError(
                "operations destination changed filesystem below its mountpoint"
            )
        for protected, label in (
            (deployment_root, "deployment"),
            (state_root, "device state"),
            (nas_root, "NAS data"),
        ):
            if read_device(protected) == destination_device:
                raise ExternalStorageError(
                    f"operations destination shares a filesystem with protected {label} data"
                )
    except OSError as exc:
        raise ExternalStorageError("filesystem identity cannot be inspected safely") from exc
    return {
        "destination": str(destination),
        "mountpoint": str(mountpoint),
        "filesystem": filesystem,
        "source": source,
        "deviceId": str(destination_device),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("verify", choices=("verify",))
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--mountpoint", type=Path, required=True)
    parser.add_argument("--deployment-root", type=Path, required=True)
    parser.add_argument("--appliance-env", type=Path)
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--nas-root", type=Path)
    parser.add_argument("--purpose", choices=("state-backup", "audit-evidence"), required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = verify_external_storage(
            destination=args.destination,
            mountpoint=args.mountpoint,
            deployment_root=args.deployment_root,
            appliance_env=args.appliance_env,
            state_root_override=args.state_root,
            nas_root_override=args.nas_root,
        )
    except ExternalStorageError as exc:
        print(f"Echo external storage verification failed: {exc}", file=sys.stderr)
        return 1
    print(
        "ECHO_EXTERNAL_STORAGE_READY "
        f"purpose={args.purpose} mount={result['mountpoint']} filesystem={result['filesystem']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
