#!/usr/bin/env python3
"""Encrypted off-device backup and empty-volume restore for Echo NAS data.

The appliance-state backup intentionally excludes user files.  This command
provides the separate data path: it accepts only a read-only source snapshot,
uses restic's authenticated encrypted repository, performs a full repository
read before and after transfer, and restores only into the configured, empty
NAS root. Promotion is one Linux ``renameat2(RENAME_EXCHANGE)``. Protected
receipts bind independent content hashes to the exact snapshot and target;
``verify-restore`` checks a previous promotion without rewriting user files.
"""

from __future__ import annotations

import argparse
import ctypes
import getpass
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess  # nosec B404
import sys
import tempfile
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

try:
    from deploy.appliance.external_storage import (
        ExternalStorageError,
        _nas_root,
        verify_external_storage,
    )
    from deploy.appliance.nas_data_backup_support import (
        BackupSetRestoreReceipts,
        NasDataBackupError,
        RestoreReceipts,
        filesystem_failure,
        process_failure,
        tree_identity,
    )
except ModuleNotFoundError:
    from external_storage import (  # type: ignore[no-redef]
        ExternalStorageError,
        _nas_root,
        verify_external_storage,
    )
    from nas_data_backup_support import (  # type: ignore[no-redef]
        BackupSetRestoreReceipts,
        NasDataBackupError,
        RestoreReceipts,
        filesystem_failure,
        process_failure,
        tree_identity,
    )


SCHEMA_VERSION = 1
RESTIC = Path("/usr/bin/restic")
TAG = "echo-nas-data-v1"
# Public systemd credential key, never the credential value itself.
PASSWORD_CREDENTIAL = "echo-nas-backup-password"  # nosec B105
LOCK_FILE = Path("/run/echo-os/nas-data-backup.lock")
RESTORE_RECEIPTS = Path("/var/lib/echo-os/nas-restore")
RESTORE_SET_RECEIPTS = Path("/var/lib/echo-os/nas-set-restore")
BTRFS = Path("/usr/bin/btrfs")
BACKUP_SET_SCHEMA = "echo.nas-backup-set.v1"
BACKUP_SET_PLAN_SCHEMA = "echo.nas-backup-set-plan.v1"
BACKUP_SET_TAG = "echo-nas-backup-set-v1"
BACKUP_SET_ID_TAG = "echo-set-id:"
BACKUP_SET_MANIFEST_TAG = "echo-set-manifest:"
BACKUP_SET_MEMBER_TAG = "echo-set-member-v1:"
MAX_BACKUP_SET_BYTES = 256 * 1024
MAX_BACKUP_SET_MEMBERS = 256
_BTRFS_SNAPSHOT_NAMESPACE = uuid.UUID("b12e7b15-6c89-49ec-925f-129781c7bf50")
MAX_PASSWORD_BYTES = 4096
MAX_OUTPUT_BYTES = 16 * 1024 * 1024
MAX_TREE_ENTRIES = 10_000_000
MAX_TREE_DEPTH = 512
MAX_LIST_SNAPSHOTS = 200
SNAPSHOT = re.compile(r"^[0-9a-f]{64}$")
REPOSITORY_ID = re.compile(r"^[0-9a-f]{16,64}$")
BTRFS_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
AT_FDCWD = -100
RENAME_EXCHANGE = 2


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _run(command: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603
        list(command),
        check=False,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=24 * 60 * 60,
        **kwargs,
    )


def _fixed_environment() -> dict[str, str]:
    return {
        "HOME": "/root",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
        "TMPDIR": "/run",
    }


def _safe_directory(path: Path, label: str) -> Path:
    if not path.is_absolute() or any(part in {".", ".."} for part in path.parts):
        raise NasDataBackupError(f"{label} must be an absolute normalized path")
    cursor = Path(path.anchor)
    for part in path.parts[1:]:
        cursor /= part
        if cursor.is_symlink():
            raise NasDataBackupError(f"{label} must not contain a symbolic link")
    try:
        resolved = path.resolve(strict=True)
        metadata = resolved.lstat()
    except OSError as exc:
        raise NasDataBackupError(f"{label} is unavailable") from exc
    if not stat.S_ISDIR(metadata.st_mode):
        raise NasDataBackupError(f"{label} is not a directory")
    return resolved


def _require_empty(path: Path, label: str) -> Path:
    resolved = _safe_directory(path, label)
    if next(resolved.iterdir(), None) is not None:
        raise NasDataBackupError(f"{label} must be empty")
    return resolved


def _private_regular(path: Path, label: str, *, owner: int = 0) -> bytes:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise NasDataBackupError(f"{label} is unavailable") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != owner
            or stat.S_IMODE(metadata.st_mode) & 0o077
            or not 1 <= metadata.st_size <= MAX_PASSWORD_BYTES
        ):
            raise NasDataBackupError(f"{label} is unsafe")
        chunks: list[bytes] = []
        remaining = metadata.st_size
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                raise NasDataBackupError(f"{label} ended while reading")
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) != metadata.st_size:
            raise NasDataBackupError(f"{label} changed while reading")
        return raw
    finally:
        os.close(descriptor)


def _strict_json(raw: bytes, label: str) -> Any:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise NasDataBackupError(f"{label} contains duplicate JSON keys")
            result[key] = value
        return result

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicates)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise NasDataBackupError(f"{label} is not valid UTF-8 JSON") from exc


def _canonical_uuid(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 36:
        raise NasDataBackupError(f"{label} must be a canonical UUID")
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise NasDataBackupError(f"{label} must be a canonical UUID") from exc
    normalized = str(parsed)
    if value != normalized:
        raise NasDataBackupError(f"{label} must be a lowercase canonical UUID")
    return normalized


def _canonical_posix_path(value: Any, label: str) -> PurePosixPath:
    if not isinstance(value, str) or not 1 <= len(value) <= 4096:
        raise NasDataBackupError(f"{label} must be an absolute normalized path")
    if any(token in value for token in ("\0", "\r", "\n", "\\", "*", "?", "[")):
        raise NasDataBackupError(f"{label} must be an absolute normalized path")
    path = PurePosixPath(value)
    if (
        not path.is_absolute()
        or str(path) != value
        or any(part in {"", ".", ".."} for part in path.parts[1:])
    ):
        raise NasDataBackupError(f"{label} must be an absolute normalized path")
    return path


def _validate_backup_set_manifest(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "schema",
        "setId",
        "createdAt",
        "members",
    }:
        raise NasDataBackupError("NAS backup-set manifest has an invalid shape")
    if value["schema"] != BACKUP_SET_SCHEMA:
        raise NasDataBackupError("NAS backup-set manifest schema is unsupported")
    set_id = _canonical_uuid(value["setId"], "backup-set ID")
    created = _snapshot_time(value["createdAt"])
    members = value["members"]
    if not isinstance(members, list) or not 1 <= len(members) <= MAX_BACKUP_SET_MEMBERS:
        raise NasDataBackupError(
            f"NAS backup set must contain 1 to {MAX_BACKUP_SET_MEMBERS} members"
        )
    normalized: list[dict[str, str]] = []
    identities: dict[str, set[str]] = {
        "sharedFolderRef": set(),
        "snapshotId": set(),
        "sourceSnapshot": set(),
        "restoreTarget": set(),
    }
    for raw_member in members:
        if not isinstance(raw_member, dict) or set(raw_member) != {
            "sharedFolderRef",
            "filesystemUuid",
            "snapshotId",
            "sourceSnapshot",
            "restoreTarget",
            "storageKind",
        }:
            raise NasDataBackupError("NAS backup-set member has an invalid shape")
        if raw_member["storageKind"] != "btrfsSubvolume":
            raise NasDataBackupError("NAS backup-set v1 accepts only managed Btrfs share snapshots")
        shared_ref = _canonical_uuid(raw_member["sharedFolderRef"], "shared-folder ref")
        filesystem_uuid = _canonical_uuid(raw_member["filesystemUuid"], "filesystem UUID")
        snapshot_id = _canonical_uuid(raw_member["snapshotId"], "snapshot ID")
        source = _canonical_posix_path(raw_member["sourceSnapshot"], "snapshot source")
        target = _canonical_posix_path(raw_member["restoreTarget"], "restore target")
        if (
            len(source.parts) < 4
            or source.parts[-3] != ".echo-snapshots"
            or source.parts[-2] != shared_ref
            or PurePosixPath(*source.parts[:-3]) != target.parent
        ):
            raise NasDataBackupError(
                "NAS backup-set member does not match the managed snapshot layout"
            )
        expected_snapshot = str(
            uuid.uuid5(_BTRFS_SNAPSHOT_NAMESPACE, f"{shared_ref}:{source.name}")
        )
        if snapshot_id != expected_snapshot:
            raise NasDataBackupError(
                "NAS backup-set snapshot ID does not match its managed snapshot name"
            )
        member = {
            "sharedFolderRef": shared_ref,
            "filesystemUuid": filesystem_uuid,
            "snapshotId": snapshot_id,
            "sourceSnapshot": str(source),
            "restoreTarget": str(target),
            "storageKind": "btrfsSubvolume",
        }
        for field, seen in identities.items():
            identity = member[field]
            if identity in seen:
                raise NasDataBackupError(f"NAS backup-set member has a duplicate {field}")
            seen.add(identity)
        normalized.append(member)
    normalized.sort(key=lambda item: item["sharedFolderRef"])
    targets = [PurePosixPath(item["restoreTarget"]) for item in normalized]
    for index, target in enumerate(targets):
        for other in targets[index + 1 :]:
            if target in other.parents or other in target.parents:
                raise NasDataBackupError("NAS backup-set restore targets must not be nested")
    canonical = {
        "schema": BACKUP_SET_SCHEMA,
        "setId": set_id,
        "createdAt": created.isoformat(timespec="microseconds").replace("+00:00", "Z"),
        "members": normalized,
    }
    digest = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {**canonical, "manifestSha256": digest}


def load_backup_set_manifest(path: Path, *, owner: int = 0) -> dict[str, Any]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise NasDataBackupError("NAS backup-set manifest is unavailable") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != owner
            or stat.S_IMODE(before.st_mode) & 0o077
            or not 1 <= before.st_size <= MAX_BACKUP_SET_BYTES
        ):
            raise NasDataBackupError("NAS backup-set manifest is unsafe")
        raw = bytearray()
        while len(raw) <= MAX_BACKUP_SET_BYTES:
            chunk = os.read(descriptor, min(64 * 1024, MAX_BACKUP_SET_BYTES + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        after = os.fstat(descriptor)
        if len(raw) != before.st_size or (
            before.st_ino,
            before.st_dev,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_ino,
            after.st_dev,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise NasDataBackupError("NAS backup-set manifest changed while reading")
    finally:
        os.close(descriptor)
    return _validate_backup_set_manifest(_strict_json(bytes(raw), "NAS backup-set manifest"))


def _password_from_credential() -> bytes:
    directory = os.environ.get("CREDENTIALS_DIRECTORY")
    if directory:
        root = Path(directory)
        if not root.is_absolute():
            raise NasDataBackupError("systemd credential directory is not absolute")
        try:
            metadata = root.lstat()
        except OSError as exc:
            raise NasDataBackupError("systemd credential directory is unavailable") from exc
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            raise NasDataBackupError("systemd credential directory is unsafe")
        raw = _private_regular(root / PASSWORD_CREDENTIAL, "NAS backup credential")
        value = raw.removesuffix(b"\n")
    else:
        value = getpass.getpass("Echo NAS backup password: ").encode("utf-8")
    if not 12 <= len(value) <= MAX_PASSWORD_BYTES or any(
        token in value for token in (b"\0", b"\r", b"\n")
    ):
        raise NasDataBackupError("NAS backup password must contain 12 to 4096 safe bytes")
    return value


@contextmanager
def _password_memfd(password: bytes) -> Iterator[int]:
    if not hasattr(os, "memfd_create"):
        raise NasDataBackupError("anonymous password transport is unavailable")
    descriptor = os.memfd_create("echo-nas-backup-password", 0)
    try:
        os.fchmod(descriptor, 0o400)
        os.write(descriptor, password)
        os.lseek(descriptor, 0, os.SEEK_SET)
        yield descriptor
    finally:
        os.close(descriptor)


@contextmanager
def _operation_lock() -> Iterator[None]:
    import fcntl

    _ensure_lock_directory(LOCK_FILE.parent)
    flags = os.O_CREAT | os.O_RDWR | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(LOCK_FILE, flags, 0o600)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0:
            raise NasDataBackupError("NAS backup lock is unsafe")
        os.fchmod(descriptor, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise NasDataBackupError("another NAS backup operation is active") from exc
        yield
    finally:
        os.close(descriptor)


def _ensure_lock_directory(path: Path, *, trusted_uid: int = 0) -> Path:
    """Create one private runtime directory below a trusted, non-writable parent."""
    base = _safe_directory(path.parent, "NAS backup runtime directory")
    base_info = base.lstat()
    if base_info.st_uid != trusted_uid or stat.S_IMODE(base_info.st_mode) & 0o022:
        raise NasDataBackupError("NAS backup runtime directory is unsafe")
    with suppress(FileExistsError):
        path.mkdir(mode=0o700)
    try:
        info = path.lstat()
    except OSError as exc:
        raise NasDataBackupError("NAS backup lock directory is unavailable") from exc
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != trusted_uid
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise NasDataBackupError("NAS backup lock directory is unsafe")
    return path


def _mount_record(path: Path, mountinfo: Path = Path("/proc/self/mountinfo")) -> dict[str, Any]:
    try:
        raw = mountinfo.read_bytes()
    except OSError as exc:
        raise NasDataBackupError("kernel mount table is unavailable") from exc
    if not 1 <= len(raw) <= 4 * 1024 * 1024:
        raise NasDataBackupError("kernel mount table is empty or oversized")
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeError as exc:
        raise NasDataBackupError("kernel mount table is malformed") from exc
    matches: list[tuple[int, dict[str, Any]]] = []
    for line in lines:
        before, separator, after = line.partition(" - ")
        fields, trailing = before.split(), after.split()
        if not separator or len(fields) < 6 or len(trailing) < 3:
            raise NasDataBackupError("kernel mount table contains a malformed record")
        mountpoint = Path(
            re.sub(r"\\([0-7]{3})", lambda item: chr(int(item.group(1), 8)), fields[4])
        )
        try:
            path.relative_to(mountpoint)
        except ValueError:
            continue
        matches.append(
            (
                len(mountpoint.parts),
                {
                    "root": fields[3],
                    "mountpoint": str(mountpoint),
                    "options": fields[5].split(","),
                    "filesystem": trailing[0],
                    "source": trailing[1],
                    "superOptions": trailing[2].split(","),
                },
            )
        )
    if not matches:
        raise NasDataBackupError("NAS path is not backed by a visible mount")
    return max(matches, key=lambda item: item[0])[1]


def _require_read_only_snapshot(
    path: Path, mountinfo: Path = Path("/proc/self/mountinfo")
) -> dict[str, str]:
    record = _mount_record(path, mountinfo)
    if "ro" not in record["options"]:
        raise NasDataBackupError("NAS backup source must be a read-only mounted snapshot")
    return {
        "mountpoint": record["mountpoint"],
        "filesystem": record["filesystem"],
        "sourceSha256": hashlib.sha256(str(record["source"]).encode()).hexdigest(),
    }


def _require_snapshot_independence(
    source: Path,
    nas_root: Path,
    mountinfo: Path = Path("/proc/self/mountinfo"),
) -> None:
    if source.stat().st_dev != nas_root.stat().st_dev:
        return
    snapshot = _mount_record(source, mountinfo)
    live = _mount_record(nas_root, mountinfo)
    # Btrfs snapshots legitimately share one block device.  They still have a
    # distinct mounted subvolume root.  A read-only bind mount of the live
    # ext4/xfs tree is not a snapshot and must not be accepted as consistency.
    snapshot_identity = (
        snapshot["root"],
        snapshot["source"],
        tuple(snapshot["superOptions"]),
    )
    live_identity = (
        live["root"],
        live["source"],
        tuple(live["superOptions"]),
    )
    if snapshot["filesystem"] != "btrfs" or snapshot_identity == live_identity:
        raise NasDataBackupError(
            "NAS backup source must be an independent filesystem snapshot, not a read-only bind"
        )


def _inspection_output(command: Sequence[str], label: str, runner: Runner = _run) -> str:
    try:
        completed = runner(command, env=_fixed_environment())
    except (OSError, subprocess.SubprocessError) as exc:
        raise NasDataBackupError(f"{label} could not be inspected") from exc
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    if (
        len(stdout.encode("utf-8", "replace")) > 64 * 1024
        or len(stderr.encode("utf-8", "replace")) > 64 * 1024
    ):
        raise NasDataBackupError(f"{label} inspection output is oversized")
    if completed.returncode != 0:
        raise NasDataBackupError(f"{label} could not be inspected")
    return stdout


def _btrfs_subvolume_identity(path: Path, runner: Runner = _run) -> dict[str, Any]:
    show = _inspection_output(
        [str(BTRFS), "subvolume", "show", str(path)], "Btrfs subvolume", runner
    )
    fields: dict[str, str] = {}
    for raw_line in show.splitlines():
        key, separator, value = raw_line.strip().partition(":")
        if separator and key in {"UUID", "Parent UUID"}:
            fields[key] = value.strip().lower()
    subvolume_uuid = fields.get("UUID", "")
    parent_uuid = fields.get("Parent UUID", "-")
    if BTRFS_UUID.fullmatch(subvolume_uuid) is None or (
        parent_uuid != "-" and BTRFS_UUID.fullmatch(parent_uuid) is None
    ):
        raise NasDataBackupError("Btrfs subvolume identity is invalid")
    property_output = _inspection_output(
        [str(BTRFS), "property", "get", "-ts", str(path), "ro"],
        "Btrfs read-only property",
        runner,
    ).strip()
    if property_output not in {"ro=true", "ro=false"}:
        raise NasDataBackupError("Btrfs read-only property is invalid")
    filesystem_uuid = (
        _inspection_output(
            ["/usr/bin/findmnt", "-n", "-o", "UUID", "-T", str(path)],
            "Btrfs filesystem identity",
            runner,
        )
        .strip()
        .lower()
    )
    if BTRFS_UUID.fullmatch(filesystem_uuid) is None:
        raise NasDataBackupError("Btrfs filesystem identity is invalid")
    return {
        "subvolumeUuid": subvolume_uuid,
        "parentUuid": None if parent_uuid == "-" else parent_uuid,
        "readOnly": property_output == "ro=true",
        "filesystemUuid": filesystem_uuid,
    }


def _btrfs_mutation(command: Sequence[str], runner: Runner = _run) -> None:
    try:
        completed = runner(command, env=_fixed_environment())
    except (OSError, subprocess.SubprocessError) as exc:
        raise NasDataBackupError("Btrfs restore staging could not be changed") from exc
    if (
        len((completed.stdout or "").encode("utf-8", "replace")) > 64 * 1024
        or len((completed.stderr or "").encode("utf-8", "replace")) > 64 * 1024
        or completed.returncode != 0
    ):
        raise NasDataBackupError("Btrfs restore staging could not be changed")


def _restic_base(repository: Path, password_fd: int) -> list[str]:
    return [
        str(RESTIC),
        "--repo",
        str(repository),
        "--password-file",
        f"/proc/self/fd/{password_fd}",
        "--no-cache",
    ]


def _restic(
    arguments: Sequence[str],
    password_fd: int,
    runner: Runner = _run,
    *,
    phase: str | None = None,
) -> subprocess.CompletedProcess[str]:
    phase = phase or (
        "restore_transfer"
        if "restore" in arguments
        else "backup_transfer"
        if "backup" in arguments
        else "repository_check"
    )
    try:
        os.lseek(password_fd, 0, os.SEEK_SET)
        completed = runner(arguments, pass_fds=(password_fd,), env=_fixed_environment())
    except subprocess.TimeoutExpired as exc:
        raise NasDataBackupError(code="operation_timeout", phase=phase) from exc
    except OSError as exc:
        error = filesystem_failure(exc, phase=phase)
        if isinstance(exc, FileNotFoundError):
            error = NasDataBackupError(code="runtime_unavailable", phase=phase)
        raise error from exc
    if (
        len(completed.stdout.encode("utf-8", "replace")) > MAX_OUTPUT_BYTES
        or len(completed.stderr.encode("utf-8", "replace")) > MAX_OUTPUT_BYTES
    ):
        raise NasDataBackupError(code="output_limit", phase=phase)
    if completed.returncode != 0:
        raise process_failure(completed.stderr, phase=phase)
    return completed


def _repository_id(
    repository: Path, password_fd: int, runner: Runner, *, no_lock: bool = False
) -> str:
    arguments = _restic_base(repository, password_fd)
    if no_lock:
        arguments.append("--no-lock")
    completed = _restic([*arguments, "cat", "config"], password_fd, runner)
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise NasDataBackupError("restic repository config is malformed") from exc
    identity = value.get("id") if isinstance(value, dict) else None
    if not isinstance(identity, str) or REPOSITORY_ID.fullmatch(identity) is None:
        raise NasDataBackupError("restic repository identity is invalid")
    return identity


def _snapshot_time(value: Any) -> datetime:
    if not isinstance(value, str) or not value or len(value) > 64:
        raise NasDataBackupError("restic snapshot time is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise NasDataBackupError("restic snapshot time is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise NasDataBackupError("restic snapshot time is missing its timezone")
    return parsed.astimezone(UTC)


def _snapshots(repository: Path, password_fd: int, runner: Runner) -> list[dict[str, Any]]:
    completed = _restic(
        [*_restic_base(repository, password_fd), "snapshots", "--json", "--tag", TAG],
        password_fd,
        runner,
    )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise NasDataBackupError("restic snapshot index is malformed") from exc
    if not isinstance(value, list) or len(value) > 1_000_000:
        raise NasDataBackupError("restic snapshot index is invalid")
    result: list[dict[str, Any]] = []
    for item in value:
        snapshot_id = item.get("id") if isinstance(item, dict) else None
        paths = item.get("paths") if isinstance(item, dict) else None
        tags = item.get("tags") if isinstance(item, dict) else None
        snapshot_time = item.get("time") if isinstance(item, dict) else None
        snapshot_path = Path(paths[0]) if isinstance(paths, list) and len(paths) == 1 else None
        if (
            not isinstance(snapshot_id, str)
            or SNAPSHOT.fullmatch(snapshot_id) is None
            or not isinstance(paths, list)
            or len(paths) != 1
            or not isinstance(paths[0], str)
            or snapshot_path is None
            or not snapshot_path.is_absolute()
            or any(part in {"", ".", ".."} for part in snapshot_path.parts[1:])
            or any(token in paths[0] for token in ("\0", "\r", "\n"))
            or not isinstance(tags, list)
            or TAG not in tags
        ):
            raise NasDataBackupError("restic snapshot identity is invalid")
        _snapshot_time(snapshot_time)
        result.append({"id": snapshot_id, "path": paths[0], "time": snapshot_time})
    return result


def _backup_set_tags(manifest: Mapping[str, Any]) -> list[str]:
    tags = [
        BACKUP_SET_TAG,
        BACKUP_SET_ID_TAG + str(manifest["setId"]),
        BACKUP_SET_MANIFEST_TAG + str(manifest["manifestSha256"]),
    ]
    for member in manifest["members"]:
        path_digest = hashlib.sha256(member["sourceSnapshot"].encode("utf-8")).hexdigest()
        tags.append(
            BACKUP_SET_MEMBER_TAG
            + ":".join(
                (
                    member["sharedFolderRef"],
                    member["filesystemUuid"],
                    member["snapshotId"],
                    path_digest,
                )
            )
        )
    return tags


def _parse_backup_set_snapshot(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise NasDataBackupError("restic backup-set snapshot identity is invalid")
    snapshot_id = item.get("id")
    snapshot_time = item.get("time")
    raw_paths = item.get("paths")
    raw_tags = item.get("tags")
    if (
        not isinstance(snapshot_id, str)
        or SNAPSHOT.fullmatch(snapshot_id) is None
        or not isinstance(raw_paths, list)
        or not 1 <= len(raw_paths) <= MAX_BACKUP_SET_MEMBERS
        or not isinstance(raw_tags, list)
        or len(raw_tags) != len(raw_paths) + 3
        or any(not isinstance(tag, str) or not 1 <= len(tag) <= 256 for tag in raw_tags)
        or len(set(raw_tags)) != len(raw_tags)
        or BACKUP_SET_TAG not in raw_tags
    ):
        raise NasDataBackupError("restic backup-set snapshot identity is invalid")
    _snapshot_time(snapshot_time)
    paths = [_canonical_posix_path(path, "restic backup-set source") for path in raw_paths]
    if len(set(paths)) != len(paths):
        raise NasDataBackupError("restic backup-set snapshot has duplicate sources")
    set_tags = [tag for tag in raw_tags if tag.startswith(BACKUP_SET_ID_TAG)]
    manifest_tags = [tag for tag in raw_tags if tag.startswith(BACKUP_SET_MANIFEST_TAG)]
    member_tags = [tag for tag in raw_tags if tag.startswith(BACKUP_SET_MEMBER_TAG)]
    if len(set_tags) != 1 or len(manifest_tags) != 1 or len(member_tags) != len(paths):
        raise NasDataBackupError("restic backup-set metadata is incomplete")
    set_id = _canonical_uuid(set_tags[0][len(BACKUP_SET_ID_TAG) :], "backup-set ID")
    manifest_digest = manifest_tags[0][len(BACKUP_SET_MANIFEST_TAG) :]
    if SNAPSHOT.fullmatch(manifest_digest) is None:
        raise NasDataBackupError("restic backup-set manifest identity is invalid")
    members: list[dict[str, str]] = []
    for tag in member_tags:
        fields = tag[len(BACKUP_SET_MEMBER_TAG) :].split(":")
        if len(fields) != 4 or SNAPSHOT.fullmatch(fields[3]) is None:
            raise NasDataBackupError("restic backup-set member metadata is invalid")
        members.append(
            {
                "sharedFolderRef": _canonical_uuid(fields[0], "shared-folder ref"),
                "filesystemUuid": _canonical_uuid(fields[1], "filesystem UUID"),
                "snapshotId": _canonical_uuid(fields[2], "snapshot ID"),
                "sourcePathSha256": fields[3],
            }
        )
    if len({member["sharedFolderRef"] for member in members}) != len(members) or {
        member["sourcePathSha256"] for member in members
    } != {hashlib.sha256(str(path).encode("utf-8")).hexdigest() for path in paths}:
        raise NasDataBackupError("restic backup-set member mapping is invalid")
    members.sort(key=lambda member: member["sharedFolderRef"])
    return {
        "id": snapshot_id,
        "time": snapshot_time,
        "setId": set_id,
        "manifestSha256": manifest_digest,
        "members": members,
        "paths": [str(path) for path in paths],
    }


def _backup_set_snapshots(
    repository: Path, password_fd: int, runner: Runner
) -> list[dict[str, Any]]:
    completed = _restic(
        [
            *_restic_base(repository, password_fd),
            "snapshots",
            "--json",
            "--tag",
            BACKUP_SET_TAG,
        ],
        password_fd,
        runner,
    )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise NasDataBackupError("restic backup-set index is malformed") from exc
    if not isinstance(value, list) or len(value) > 1_000_000:
        raise NasDataBackupError("restic backup-set index is invalid")
    return [_parse_backup_set_snapshot(item) for item in value]


def _manifest_index_members(manifest: Mapping[str, Any]) -> list[dict[str, str]]:
    members = [
        {
            "sharedFolderRef": member["sharedFolderRef"],
            "filesystemUuid": member["filesystemUuid"],
            "snapshotId": member["snapshotId"],
            "sourcePathSha256": hashlib.sha256(
                member["sourceSnapshot"].encode("utf-8")
            ).hexdigest(),
        }
        for member in manifest["members"]
    ]
    members.sort(key=lambda member: member["sharedFolderRef"])
    return members


def _select_backup_set(selector: str, snapshots: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not snapshots:
        raise NasDataBackupError("NAS backup repository has no backup sets")
    if selector == "latest":
        selected = max(snapshots, key=lambda item: _snapshot_time(item.get("time")))
    else:
        matches: list[Mapping[str, Any]] = []
        try:
            set_id = _canonical_uuid(selector, "backup-set selector")
        except NasDataBackupError:
            set_id = ""
        if set_id:
            matches = [item for item in snapshots if item.get("setId") == set_id]
        elif re.fullmatch(r"[0-9a-f]{12,64}", selector):
            matches = [item for item in snapshots if str(item.get("id", "")).startswith(selector)]
        else:
            raise NasDataBackupError("backup-set selector is invalid")
        if len(matches) != 1:
            raise NasDataBackupError("backup-set selector is missing or ambiguous")
        selected = matches[0]
    return dict(selected)


def _bind_manifest_to_backup_set(
    manifest: Mapping[str, Any], snapshot: Mapping[str, Any]
) -> list[dict[str, str]]:
    expected = _manifest_index_members(manifest)
    if (
        snapshot.get("setId") != manifest.get("setId")
        or snapshot.get("manifestSha256") != manifest.get("manifestSha256")
        or snapshot.get("members") != expected
    ):
        raise NasDataBackupError("backup-set manifest does not match authenticated repository data")
    paths = snapshot.get("paths")
    if not isinstance(paths, list):
        raise NasDataBackupError("backup-set repository paths are unavailable")
    by_digest = {
        hashlib.sha256(path.encode("utf-8")).hexdigest(): path
        for path in paths
        if isinstance(path, str)
    }
    bound: list[dict[str, str]] = []
    for member in manifest["members"]:
        digest = hashlib.sha256(member["sourceSnapshot"].encode("utf-8")).hexdigest()
        source_path = by_digest.get(digest)
        if source_path != member["sourceSnapshot"]:
            raise NasDataBackupError("backup-set source mapping is not authenticated")
        bound.append({**member, "repositorySource": source_path})
    return bound


def _normalize_restore_targets(
    manifest: Mapping[str, Any],
    restore_targets: Mapping[str, Mapping[str, str]] | None,
) -> dict[str, dict[str, str]]:
    """Validate a private source-share to target mapping.

    The authenticated manifest remains immutable.  A replacement-disk restore
    binds a separate target path/filesystem identity into the restore plan and
    durable receipt instead of pretending the new layout was part of the old
    Restic snapshot.
    """
    members = manifest.get("members")
    if not isinstance(members, list):
        raise NasDataBackupError("NAS backup-set manifest was not validated")
    original = {
        member["sharedFolderRef"]: {
            "restoreTarget": member["restoreTarget"],
            "filesystemUuid": member["filesystemUuid"],
        }
        for member in members
    }
    if restore_targets is None:
        return original
    if not isinstance(restore_targets, Mapping) or set(restore_targets) != set(original):
        raise NasDataBackupError("NAS restore target mapping is incomplete")
    normalized: dict[str, dict[str, str]] = {}
    for reference, raw in restore_targets.items():
        if not isinstance(raw, Mapping) or set(raw) != {"restoreTarget", "filesystemUuid"}:
            raise NasDataBackupError("NAS restore target mapping has an invalid shape")
        path = _canonical_posix_path(raw.get("restoreTarget"), "mapped restore target")
        filesystem_uuid = _canonical_uuid(
            raw.get("filesystemUuid"), "mapped target filesystem UUID"
        )
        normalized[reference] = {
            "restoreTarget": str(path),
            "filesystemUuid": filesystem_uuid,
        }
    paths = [PurePosixPath(item["restoreTarget"]) for item in normalized.values()]
    if len(paths) != len(set(paths)):
        raise NasDataBackupError("NAS restore target mapping contains duplicate targets")
    for index, target in enumerate(paths):
        for other in paths[index + 1 :]:
            if target in other.parents or other in target.parents:
                raise NasDataBackupError("NAS restore target mapping contains nested targets")
    return normalized


def _select_snapshot(selector: str, snapshots: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    if not snapshots:
        raise NasDataBackupError("NAS backup repository has no snapshots")
    if selector == "latest":
        selected = max(snapshots, key=lambda item: _snapshot_time(item.get("time")))
    else:
        if re.fullmatch(r"[0-9a-f]{12,64}", selector) is None:
            raise NasDataBackupError("snapshot selector is invalid")
        matches = [item for item in snapshots if str(item["id"]).startswith(selector)]
        if len(matches) != 1:
            raise NasDataBackupError("snapshot selector is missing or ambiguous")
        selected = matches[0]
    return {"id": str(selected["id"]), "path": str(selected["path"])}


def _tree_safe(root: Path) -> dict[str, int]:
    entries = 0
    total_bytes = 0
    root_resolved = root.resolve(strict=True)
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        relative = Path(current).relative_to(root)
        if len(relative.parts) > MAX_TREE_DEPTH:
            raise NasDataBackupError("restored NAS tree exceeds the depth limit")
        for name in [*directories, *files]:
            entries += 1
            if entries > MAX_TREE_ENTRIES:
                raise NasDataBackupError("restored NAS tree exceeds the entry limit")
            item = Path(current) / name
            metadata = item.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                target = os.readlink(item)
                if os.path.isabs(target):
                    raise NasDataBackupError("restored NAS tree contains an absolute symlink")
                resolved = (item.parent / target).resolve(strict=False)
                try:
                    resolved.relative_to(root_resolved)
                except ValueError as exc:
                    raise NasDataBackupError(
                        "restored NAS tree contains an escaping symlink"
                    ) from exc
            elif stat.S_ISREG(metadata.st_mode):
                total_bytes += metadata.st_size
            elif not stat.S_ISDIR(metadata.st_mode):
                raise NasDataBackupError("restored NAS tree contains a special file")
    return {"entries": entries, "logicalBytes": total_bytes}


def _restored_root(staging: Path, original: Path) -> Path:
    pure = PurePosixPath(str(original))
    expected = staging.joinpath(*pure.parts[1:])
    current = staging
    for part in pure.parts[1:]:
        children = list(current.iterdir())
        if len(children) != 1 or children[0].name != part or children[0].is_symlink():
            raise NasDataBackupError("restic restore contains an unexpected path hierarchy")
        current = children[0]
    if current != expected or not current.is_dir():
        raise NasDataBackupError("restic restore is missing its authenticated NAS root")
    return expected


def _exchange_directories(left: Path, right: Path) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise NasDataBackupError("atomic directory exchange is unavailable on this Linux host")
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    if (
        renameat2(
            AT_FDCWD,
            os.fsencode(left),
            AT_FDCWD,
            os.fsencode(right),
            RENAME_EXCHANGE,
        )
        != 0
    ):
        error = ctypes.get_errno()
        raise NasDataBackupError(f"atomic NAS restore promotion failed with errno {error}")


def _sync_filesystem(path: Path) -> None:
    """Durably flush the filesystem containing ``path`` before receipt advancement."""

    # The executable is Linux/root-only, while unit tests also exercise the
    # transaction model on Windows.  The latter has no syncfs(2) equivalent and
    # never supplies real block-device evidence.
    if os.name != "posix":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(path, flags)
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        syncfs = getattr(libc, "syncfs", None)
        if syncfs is None:
            raise NasDataBackupError("filesystem durability barrier is unavailable")
        syncfs.argtypes = [ctypes.c_int]
        syncfs.restype = ctypes.c_int
        if syncfs(descriptor) != 0:
            error = ctypes.get_errno()
            raise NasDataBackupError(f"filesystem durability barrier failed with errno {error}")
    finally:
        os.close(descriptor)


def _remove_empty_restore_scaffold(staging: Path, exchanged_empty: Path) -> None:
    exchanged_empty.rmdir()
    cursor = exchanged_empty.parent
    while cursor != staging:
        cursor.rmdir()
        cursor = cursor.parent
    staging.rmdir()


def _context(
    *,
    repository: Path,
    repository_mount: Path,
    deployment_root: Path,
    appliance_env: Path | None,
    state_root_override: Path | None = None,
    nas_root_override: Path | None = None,
) -> tuple[Path, Path]:
    deployment = _safe_directory(deployment_root, "Echo appliance deployment")
    configured_nas_root = (
        _nas_root(deployment, appliance_env) if nas_root_override is None else nas_root_override
    )
    nas_root = _safe_directory(configured_nas_root, "configured NAS root")
    repository = _safe_directory(repository, "NAS backup repository")
    try:
        verify_external_storage(
            destination=repository,
            mountpoint=repository_mount,
            deployment_root=deployment,
            appliance_env=appliance_env,
            state_root_override=state_root_override,
            nas_root_override=nas_root,
        )
    except ExternalStorageError as exc:
        code = "target_not_mounted" if "not currently mounted" in str(exc) else "unknown"
        raise NasDataBackupError(str(exc), code=code) from exc
    metadata = repository.lstat()
    if metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) & 0o077:
        raise NasDataBackupError("NAS backup repository must be private and root-owned")
    return repository, nas_root


def _unlock_stale_repository(
    *,
    repository: Path,
    repository_mount: Path,
    deployment_root: Path,
    appliance_env: Path | None,
    state_root_override: Path | None = None,
    nas_root_override: Path | None = None,
    password: bytes,
    expected_repository_ids: Sequence[str],
    runner: Runner = _run,
) -> bool:
    """Remove stale locks only when a durable receipt binds this repository."""

    expected = frozenset(expected_repository_ids)
    if not expected or any(
        not isinstance(identity, str) or REPOSITORY_ID.fullmatch(identity) is None
        for identity in expected
    ):
        raise NasDataBackupError(code="receipt_mismatch", phase="repository_unlock")

    repository, _nas = _context(
        repository=repository,
        repository_mount=repository_mount,
        deployment_root=deployment_root,
        appliance_env=appliance_env,
        state_root_override=state_root_override,
        nas_root_override=nas_root_override,
    )
    with _operation_lock(), _password_memfd(password) as descriptor:
        # Default Restic unlock performs its own stale-owner checks.  Never use
        # --remove-all here because a live process may own another lock.
        if _repository_id(repository, descriptor, runner, no_lock=True) not in expected:
            return False
        arguments = [*_restic_base(repository, descriptor), "unlock"]
        if "--remove-all" in arguments:
            raise NasDataBackupError(code="unsafe_command", phase="repository_unlock")
        _restic(
            arguments,
            descriptor,
            runner,
            phase="repository_unlock",
        )
    return True


def plan_backup_set(
    *,
    manifest: dict[str, Any],
    repository: Path,
    repository_mount: Path,
    deployment_root: Path,
    appliance_env: Path | None,
    state_root_override: Path | None = None,
    nas_root_override: Path | None = None,
    runner: Runner = _run,
) -> dict[str, Any]:
    """Bind one private multi-volume manifest to current Btrfs lineage."""
    repository, _nas = _context(
        repository=repository,
        repository_mount=repository_mount,
        deployment_root=deployment_root,
        appliance_env=appliance_env,
        state_root_override=state_root_override,
        nas_root_override=nas_root_override,
    )
    repository_mount_record = _mount_record(repository)
    manifest_digest = manifest.get("manifestSha256")
    if (
        manifest.get("schema") != BACKUP_SET_SCHEMA
        or not isinstance(manifest_digest, str)
        or SNAPSHOT.fullmatch(manifest_digest) is None
        or not isinstance(manifest.get("members"), list)
    ):
        raise NasDataBackupError("NAS backup-set manifest was not validated")
    bound_members: list[dict[str, str]] = []
    private_binding: list[dict[str, Any]] = []
    for member in manifest["members"]:
        source = _safe_directory(Path(member["sourceSnapshot"]), "managed snapshot source")
        target = _safe_directory(Path(member["restoreTarget"]), "managed restore target")
        source_mount_record = _mount_record(source)
        same_repository_filesystem = source.stat().st_dev == repository.stat().st_dev or (
            source_mount_record["filesystem"],
            source_mount_record["source"],
        ) == (
            repository_mount_record["filesystem"],
            repository_mount_record["source"],
        )
        if same_repository_filesystem:
            raise NasDataBackupError("managed snapshot and backup repository share a filesystem")
        source_identity = _btrfs_subvolume_identity(source, runner)
        target_identity = _btrfs_subvolume_identity(target, runner)
        expected_filesystem = member["filesystemUuid"]
        if (
            source_identity["filesystemUuid"] != expected_filesystem
            or target_identity["filesystemUuid"] != expected_filesystem
        ):
            raise NasDataBackupError("backup-set filesystem identity changed")
        if (
            source_identity["readOnly"] is not True
            or target_identity["readOnly"] is not False
            or source_identity["parentUuid"] != target_identity["subvolumeUuid"]
            or source_identity["subvolumeUuid"] == target_identity["subvolumeUuid"]
        ):
            raise NasDataBackupError(
                "managed snapshot is not a read-only child of its restore target"
            )
        public_member = {
            "sharedFolderRef": member["sharedFolderRef"],
            "filesystemUuid": expected_filesystem,
            "snapshotId": member["snapshotId"],
            "snapshotSubvolumeUuid": source_identity["subvolumeUuid"],
        }
        bound_members.append(public_member)
        private_binding.append(
            {
                **public_member,
                "targetSubvolumeUuid": target_identity["subvolumeUuid"],
                "sourceDevice": source.stat().st_dev,
                "sourceInode": source.stat().st_ino,
                "targetDevice": target.stat().st_dev,
                "targetInode": target.stat().st_ino,
            }
        )
    plan_payload = {
        "schema": BACKUP_SET_PLAN_SCHEMA,
        "setId": manifest["setId"],
        "createdAt": manifest["createdAt"],
        "manifestSha256": manifest_digest,
        "repository": {
            "filesystem": repository_mount_record["filesystem"],
            "sourceSha256": hashlib.sha256(
                repository_mount_record["source"].encode("utf-8")
            ).hexdigest(),
            "device": repository.stat().st_dev,
            "inode": repository.stat().st_ino,
        },
        "members": private_binding,
    }
    plan_id = hashlib.sha256(
        json.dumps(plan_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "schema": BACKUP_SET_PLAN_SCHEMA,
        "planId": plan_id,
        "setId": manifest["setId"],
        "createdAt": manifest["createdAt"],
        "manifestSha256": manifest_digest,
        "memberCount": len(bound_members),
        "members": bound_members,
        "sourceModel": "managed-btrfs-share-snapshots",
        "restoreModel": "staged-per-share-transaction-required",
        "pathsRedacted": True,
    }


def backup_set(
    *,
    manifest: dict[str, Any],
    plan_id: str,
    repository: Path,
    repository_mount: Path,
    deployment_root: Path,
    appliance_env: Path | None,
    state_root_override: Path | None = None,
    nas_root_override: Path | None = None,
    password: bytes,
    runner: Runner = _run,
) -> dict[str, Any]:
    """Back up one preflighted set of immutable managed share snapshots."""
    with _operation_lock():
        current_plan = plan_backup_set(
            manifest=manifest,
            repository=repository,
            repository_mount=repository_mount,
            deployment_root=deployment_root,
            appliance_env=appliance_env,
            state_root_override=state_root_override,
            nas_root_override=nas_root_override,
            runner=runner,
        )
        if plan_id != current_plan["planId"]:
            raise NasDataBackupError("NAS backup-set plan changed; preview again")
        repository, _nas = _context(
            repository=repository,
            repository_mount=repository_mount,
            deployment_root=deployment_root,
            appliance_env=appliance_env,
            state_root_override=state_root_override,
            nas_root_override=nas_root_override,
        )
        with _password_memfd(password) as descriptor:
            repository_id = _repository_id(repository, descriptor, runner)
            indexed = _backup_set_snapshots(repository, descriptor, runner)
            same_set = [item for item in indexed if item["setId"] == manifest["setId"]]
            expected_members = _manifest_index_members(manifest)
            if same_set:
                if (
                    len(same_set) != 1
                    or same_set[0]["manifestSha256"] != manifest["manifestSha256"]
                    or same_set[0]["members"] != expected_members
                ):
                    raise NasDataBackupError("backup-set ID already has different repository data")
                _restic(
                    [*_restic_base(repository, descriptor), "check", "--read-data"],
                    descriptor,
                    runner,
                )
                return {
                    "repositoryId": repository_id,
                    "setId": manifest["setId"],
                    "snapshotId": same_set[0]["id"],
                    "manifestSha256": manifest["manifestSha256"],
                    "memberCount": len(expected_members),
                    "members": current_plan["members"],
                    "encrypted": True,
                    "fullReadVerified": True,
                    "pathsRedacted": True,
                    "idempotent": True,
                }
            tags = _backup_set_tags(manifest)
            command = [
                *_restic_base(repository, descriptor),
                "backup",
                "--json",
                "--one-file-system",
                "--host",
                "echo-nas-set-" + hashlib.sha256(manifest["setId"].encode()).hexdigest()[:16],
            ]
            for tag in tags:
                command.extend(("--tag", tag))
            command.extend(member["sourceSnapshot"] for member in manifest["members"])
            completed = _restic(command, descriptor, runner)
            snapshot_ids: list[str] = []
            for line in completed.stdout.splitlines():
                try:
                    message = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise NasDataBackupError("restic backup-set output is malformed") from exc
                if isinstance(message, dict) and message.get("message_type") == "summary":
                    snapshot_id = message.get("snapshot_id")
                    if isinstance(snapshot_id, str) and SNAPSHOT.fullmatch(snapshot_id):
                        snapshot_ids.append(snapshot_id)
            if len(snapshot_ids) != 1:
                raise NasDataBackupError("restic backup-set did not return one complete snapshot")
            _restic(
                [*_restic_base(repository, descriptor), "check", "--read-data"],
                descriptor,
                runner,
            )
            indexed = _backup_set_snapshots(repository, descriptor, runner)
            matches = [item for item in indexed if item["id"] == snapshot_ids[0]]
            if (
                len(matches) != 1
                or matches[0]["setId"] != manifest["setId"]
                or matches[0]["manifestSha256"] != manifest["manifestSha256"]
                or matches[0]["members"] != expected_members
            ):
                raise NasDataBackupError("authenticated backup-set metadata changed after backup")
            post_plan = plan_backup_set(
                manifest=manifest,
                repository=repository,
                repository_mount=repository_mount,
                deployment_root=deployment_root,
                appliance_env=appliance_env,
                state_root_override=state_root_override,
                nas_root_override=nas_root_override,
                runner=runner,
            )
            if post_plan["planId"] != current_plan["planId"]:
                _restic(
                    [
                        *_restic_base(repository, descriptor),
                        "forget",
                        snapshot_ids[0],
                    ],
                    descriptor,
                    runner,
                )
                raise NasDataBackupError("managed snapshot identity changed during backup")
    return {
        "repositoryId": repository_id,
        "setId": manifest["setId"],
        "snapshotId": snapshot_ids[0],
        "manifestSha256": manifest["manifestSha256"],
        "memberCount": len(expected_members),
        "members": current_plan["members"],
        "encrypted": True,
        "fullReadVerified": True,
        "pathsRedacted": True,
        "idempotent": False,
    }


def _plan_restore_set_unlocked(
    *,
    manifest: dict[str, Any],
    selector: str,
    repository: Path,
    repository_mount: Path,
    deployment_root: Path,
    appliance_env: Path | None,
    state_root_override: Path | None = None,
    nas_root_override: Path | None = None,
    password: bytes,
    restore_targets: Mapping[str, Mapping[str, str]] | None = None,
    runner: Runner = _run,
) -> dict[str, Any]:
    repository, _nas = _context(
        repository=repository,
        repository_mount=repository_mount,
        deployment_root=deployment_root,
        appliance_env=appliance_env,
        state_root_override=state_root_override,
        nas_root_override=nas_root_override,
    )
    repository_mount_record = _mount_record(repository)
    with _password_memfd(password) as descriptor:
        repository_id = _repository_id(repository, descriptor, runner)
        selected = _select_backup_set(
            selector, _backup_set_snapshots(repository, descriptor, runner)
        )
    bound = _bind_manifest_to_backup_set(manifest, selected)
    targets = _normalize_restore_targets(manifest, restore_targets)
    public_members: list[dict[str, str]] = []
    private_members: list[dict[str, Any]] = []
    for member in bound:
        target_binding = targets[member["sharedFolderRef"]]
        target = _require_empty(Path(target_binding["restoreTarget"]), "backup-set restore target")
        target_mount = _mount_record(target)
        if target.stat().st_dev == repository.stat().st_dev or (
            target_mount["filesystem"],
            target_mount["source"],
        ) == (repository_mount_record["filesystem"], repository_mount_record["source"]):
            raise NasDataBackupError("backup-set restore target and repository share a filesystem")
        target_identity = _btrfs_subvolume_identity(target, runner)
        if (
            target_identity["readOnly"] is not False
            or target_identity["filesystemUuid"] != target_binding["filesystemUuid"]
        ):
            raise NasDataBackupError("backup-set restore target identity changed")
        public = {
            "sharedFolderRef": member["sharedFolderRef"],
            "filesystemUuid": member["filesystemUuid"],
            "targetFilesystemUuid": target_binding["filesystemUuid"],
            "snapshotId": member["snapshotId"],
            "targetSubvolumeUuid": target_identity["subvolumeUuid"],
            "remapped": target_binding["restoreTarget"] != member["restoreTarget"],
        }
        public_members.append(public)
        target_stat = target.stat()
        private_members.append(
            {
                **public,
                "repositorySourceSha256": hashlib.sha256(
                    member["repositorySource"].encode("utf-8")
                ).hexdigest(),
                "targetDevice": target_stat.st_dev,
                "targetInode": target_stat.st_ino,
                "targetPathSha256": hashlib.sha256(
                    target_binding["restoreTarget"].encode("utf-8")
                ).hexdigest(),
            }
        )
    payload = {
        "schema": "echo.nas-backup-set-restore-plan.v1",
        "repositoryId": repository_id,
        "snapshotId": selected["id"],
        "setId": selected["setId"],
        "manifestSha256": selected["manifestSha256"],
        "members": private_members,
    }
    plan_id = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    confirmation = f"RESTORE ECHO NAS SET {selected['setId']} SNAPSHOT {selected['id']}"
    return {
        "schema": "echo.nas-backup-set-restore-plan.v1",
        "planId": plan_id,
        "repositoryId": repository_id,
        "snapshotId": selected["id"],
        "setId": selected["setId"],
        "manifestSha256": selected["manifestSha256"],
        "memberCount": len(public_members),
        "members": public_members,
        "confirmation": confirmation,
        "targetPolicy": "all-empty-managed-btrfs-subvolumes",
        "promotion": "per-share-atomic-with-durable-transaction-required",
        "pathsRedacted": True,
    }


def plan_restore_set(
    *,
    manifest: dict[str, Any],
    selector: str,
    repository: Path,
    repository_mount: Path,
    deployment_root: Path,
    appliance_env: Path | None,
    state_root_override: Path | None = None,
    nas_root_override: Path | None = None,
    password: bytes,
    restore_targets: Mapping[str, Mapping[str, str]] | None = None,
    runner: Runner = _run,
) -> dict[str, Any]:
    with _operation_lock():
        return _plan_restore_set_unlocked(
            manifest=manifest,
            selector=selector,
            repository=repository,
            repository_mount=repository_mount,
            deployment_root=deployment_root,
            appliance_env=appliance_env,
            state_root_override=state_root_override,
            nas_root_override=nas_root_override,
            password=password,
            restore_targets=restore_targets,
            runner=runner,
        )


def _validate_set_restore_receipt(
    value: Any,
    *,
    manifest: Mapping[str, Any],
    plan_id: str,
    repository_id: str,
    selected: Mapping[str, Any],
    restore_targets: Mapping[str, Mapping[str, str]] | None = None,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "schema",
        "setId",
        "repositoryId",
        "snapshotId",
        "manifestSha256",
        "planId",
        "phase",
        "members",
        "createdAt",
        "updatedAt",
    }:
        raise NasDataBackupError(code="receipt_unavailable", phase="receipt")
    if (
        value["schema"] != "echo.nas-set-restore-receipt.v1"
        or value["setId"] != manifest["setId"]
        or value["repositoryId"] != repository_id
        or value["snapshotId"] != selected["id"]
        or value["manifestSha256"] != manifest["manifestSha256"]
        or value["planId"] != plan_id
        or value["phase"] not in {"preparing", "prepared", "promoting", "verified"}
        or not isinstance(value["createdAt"], str)
        or not isinstance(value["updatedAt"], str)
        or not isinstance(value["members"], list)
        or len(value["members"]) != len(manifest["members"])
    ):
        raise NasDataBackupError(code="receipt_mismatch", phase="receipt")
    expected = {member["sharedFolderRef"]: member for member in manifest["members"]}
    targets = _normalize_restore_targets(manifest, restore_targets)
    observed_refs: set[str] = set()
    valid_states = {"pending", "restoring", "prepared", "promoted", "verified"}
    for member in value["members"]:
        if not isinstance(member, dict) or set(member) != {
            "sharedFolderRef",
            "filesystemUuid",
            "snapshotId",
            "repositorySource",
            "restoreTarget",
            "targetSubvolumeUuid",
            "preparedSubvolumeUuid",
            "staging",
            "restored",
            "tree",
            "state",
        }:
            raise NasDataBackupError(code="receipt_unavailable", phase="receipt")
        shared_ref = member["sharedFolderRef"]
        wanted = expected.get(shared_ref) if isinstance(shared_ref, str) else None
        if (
            wanted is None
            or shared_ref in observed_refs
            or member["filesystemUuid"] != targets[shared_ref]["filesystemUuid"]
            or member["snapshotId"] != wanted["snapshotId"]
            or member["repositorySource"] != wanted["sourceSnapshot"]
            or member["restoreTarget"] != targets[shared_ref]["restoreTarget"]
            or BTRFS_UUID.fullmatch(str(member["targetSubvolumeUuid"])) is None
            or member["state"] not in valid_states
        ):
            raise NasDataBackupError(code="receipt_mismatch", phase="receipt")
        observed_refs.add(shared_ref)
        for field in ("staging", "restored"):
            if member[field] is not None:
                _canonical_posix_path(member[field], f"restore receipt {field}")
        prepared_uuid = member["preparedSubvolumeUuid"]
        if prepared_uuid is not None and BTRFS_UUID.fullmatch(str(prepared_uuid)) is None:
            raise NasDataBackupError(code="receipt_unavailable", phase="receipt")
        tree = member["tree"]
        if tree is not None and (
            not isinstance(tree, dict)
            or set(tree) != {"entries", "logicalBytes", "treeSha256"}
            or any(
                type(tree.get(key)) is not int or tree[key] < 0
                for key in ("entries", "logicalBytes")
            )
            or not isinstance(tree.get("treeSha256"), str)
            or SNAPSHOT.fullmatch(tree["treeSha256"]) is None
        ):
            raise NasDataBackupError(code="receipt_unavailable", phase="receipt")
        if member["state"] in {"prepared", "promoted", "verified"} and (
            prepared_uuid is None
            or tree is None
            or member["staging"] is None
            or member["restored"] is None
        ):
            raise NasDataBackupError(code="receipt_unavailable", phase="receipt")
    if observed_refs != set(expected):
        raise NasDataBackupError(code="receipt_mismatch", phase="receipt")
    return value


def _allocate_set_staging(target: Path, repository_source: str) -> tuple[Path, Path]:
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.echo-set-restore-", dir=target.parent))
    os.chmod(staging, 0o700)
    restored = staging.joinpath(*PurePosixPath(repository_source).parts[1:])
    return staging, restored


def _create_set_staging_subvolume(staging: Path, restored: Path, runner: Runner) -> dict[str, Any]:
    try:
        restored.parent.mkdir(parents=True, mode=0o700)
        _btrfs_mutation([str(BTRFS), "subvolume", "create", str(restored)], runner)
        identity = _btrfs_subvolume_identity(restored, runner)
        if identity["readOnly"] is not False:
            raise NasDataBackupError("restore staging subvolume is read-only")
        return identity
    except Exception:
        if restored.exists() and not restored.is_symlink():
            with suppress(Exception):
                _btrfs_mutation([str(BTRFS), "subvolume", "delete", str(restored)], runner)
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _safe_set_staging(staging: Path, target: Path) -> Path:
    if staging.parent != target.parent or not staging.name.startswith(
        f".{target.name}.echo-set-restore-"
    ):
        raise NasDataBackupError(code="receipt_unavailable", phase="receipt")
    resolved = _safe_directory(staging, "backup-set restore staging")
    info = resolved.lstat()
    if info.st_uid != 0 or stat.S_IMODE(info.st_mode) & 0o077:
        raise NasDataBackupError(code="receipt_unavailable", phase="receipt")
    return resolved


def _remove_set_staging(
    staging: Path,
    restored: Path,
    *,
    expected_subvolume_uuid: str,
    runner: Runner,
) -> None:
    identity = _btrfs_subvolume_identity(restored, runner)
    if (
        identity["subvolumeUuid"] != expected_subvolume_uuid
        or next(restored.iterdir(), None) is not None
    ):
        raise NasDataBackupError("exchanged restore staging is not the original empty target")
    _btrfs_mutation([str(BTRFS), "subvolume", "delete", str(restored)], runner)
    cursor = restored.parent
    while cursor != staging:
        cursor.rmdir()
        cursor = cursor.parent
    staging.rmdir()


def _discard_incomplete_set_staging(
    staging: Path,
    restored: Path,
    *,
    target: Path,
    target_subvolume_uuid: str,
    runner: Runner,
) -> None:
    _safe_set_staging(staging, target)
    if restored.exists() or restored.is_symlink():
        if restored.is_symlink() or not restored.is_dir():
            raise NasDataBackupError(code="receipt_unavailable", phase="receipt")
        identity = _btrfs_subvolume_identity(restored, runner)
        if identity["subvolumeUuid"] == target_subvolume_uuid:
            raise NasDataBackupError(code="receipt_mismatch", phase="receipt")
        _btrfs_mutation([str(BTRFS), "subvolume", "delete", str(restored)], runner)
    shutil.rmtree(staging)


def _restore_set_impl(
    *,
    manifest: dict[str, Any],
    selector: str,
    plan_id: str,
    confirmation: str,
    repository: Path,
    repository_mount: Path,
    deployment_root: Path,
    appliance_env: Path | None,
    state_root_override: Path | None = None,
    nas_root_override: Path | None = None,
    password: bytes,
    restore_targets: Mapping[str, Mapping[str, str]] | None = None,
    runner: Runner = _run,
    exchange: Callable[[Path, Path], None] = _exchange_directories,
    receipt_directory: Path = RESTORE_SET_RECEIPTS,
) -> dict[str, Any]:
    """Restore an authenticated set into empty managed Btrfs share targets."""
    receipt_root = receipt_directory.resolve(strict=False)
    targets = _normalize_restore_targets(manifest, restore_targets)
    private_targets = [
        Path(item["restoreTarget"]).resolve(strict=False) for item in targets.values()
    ]
    repository_candidate = repository.resolve(strict=False)
    if any(
        protected == receipt_root
        or protected in receipt_root.parents
        or receipt_root in protected.parents
        for protected in [repository_candidate, *private_targets]
    ):
        raise NasDataBackupError(code="receipt_unavailable", phase="preflight")
    resumed = False
    with _operation_lock():
        repository, _nas = _context(
            repository=repository,
            repository_mount=repository_mount,
            deployment_root=deployment_root,
            appliance_env=appliance_env,
            state_root_override=state_root_override,
            nas_root_override=nas_root_override,
        )
        receipts = BackupSetRestoreReceipts(receipt_directory, manifest["setId"])
        receipt = receipts.load()
        if receipt is None:
            plan = _plan_restore_set_unlocked(
                manifest=manifest,
                selector=selector,
                repository=repository,
                repository_mount=repository_mount,
                deployment_root=deployment_root,
                appliance_env=appliance_env,
                state_root_override=state_root_override,
                nas_root_override=nas_root_override,
                password=password,
                restore_targets=restore_targets,
                runner=runner,
            )
            if plan_id != plan["planId"]:
                raise NasDataBackupError("NAS restore-set plan changed; preview again")
            if confirmation != plan["confirmation"]:
                raise NasDataBackupError(code="confirmation_mismatch")
            selected_snapshot_id = plan["snapshotId"]
        else:
            resumed = True
            if receipt.get("planId") != plan_id:
                raise NasDataBackupError(code="receipt_mismatch", phase="receipt")
            selected_snapshot_id = str(receipt.get("snapshotId") or "")
        with _password_memfd(password) as descriptor:
            repository_id = _repository_id(repository, descriptor, runner)
            _restic(
                [*_restic_base(repository, descriptor), "check", "--read-data"],
                descriptor,
                runner,
            )
            selected = _select_backup_set(
                selected_snapshot_id,
                _backup_set_snapshots(repository, descriptor, runner),
            )
            bound = _bind_manifest_to_backup_set(manifest, selected)
            expected_confirmation = (
                f"RESTORE ECHO NAS SET {selected['setId']} SNAPSHOT {selected['id']}"
            )
            if confirmation != expected_confirmation:
                raise NasDataBackupError(code="confirmation_mismatch")
            if receipt is None:
                planned_members = {member["sharedFolderRef"]: member for member in plan["members"]}
                now = datetime.now(UTC).isoformat()
                receipt = {
                    "repositoryId": repository_id,
                    "snapshotId": selected["id"],
                    "manifestSha256": manifest["manifestSha256"],
                    "planId": plan_id,
                    "phase": "preparing",
                    "members": [
                        {
                            "sharedFolderRef": member["sharedFolderRef"],
                            "filesystemUuid": targets[member["sharedFolderRef"]]["filesystemUuid"],
                            "snapshotId": member["snapshotId"],
                            "repositorySource": member["repositorySource"],
                            "restoreTarget": targets[member["sharedFolderRef"]]["restoreTarget"],
                            "targetSubvolumeUuid": planned_members[member["sharedFolderRef"]][
                                "targetSubvolumeUuid"
                            ],
                            "preparedSubvolumeUuid": None,
                            "staging": None,
                            "restored": None,
                            "tree": None,
                            "state": "pending",
                        }
                        for member in bound
                    ],
                    "createdAt": now,
                    "updatedAt": now,
                }
                receipts.save(receipt)
                receipt = receipts.load()
                assert receipt is not None
            receipt = _validate_set_restore_receipt(
                receipt,
                manifest=manifest,
                plan_id=plan_id,
                repository_id=repository_id,
                selected=selected,
                restore_targets=restore_targets,
            )

            def save_receipt(*, phase: str | None = None) -> None:
                if phase is not None:
                    receipt["phase"] = phase
                receipt["updatedAt"] = datetime.now(UTC).isoformat()
                receipts.save(receipt)

            for member in receipt["members"]:
                target = _safe_directory(Path(member["restoreTarget"]), "backup-set restore target")
                target_identity = _btrfs_subvolume_identity(target, runner)
                original_uuid = member["targetSubvolumeUuid"]
                prepared_uuid = member["preparedSubvolumeUuid"]
                state = member["state"]
                if state == "pending":
                    if target_identity["subvolumeUuid"] != original_uuid:
                        raise NasDataBackupError(code="receipt_mismatch", phase="receipt")
                    _require_empty(target, "backup-set restore target")
                    continue
                staging_path = Path(member["staging"])
                restored = Path(member["restored"])
                if not staging_path.exists() and state in {"promoted", "verified"}:
                    if (
                        target_identity["subvolumeUuid"] != prepared_uuid
                        or tree_identity(target) != member["tree"]
                    ):
                        raise NasDataBackupError(code="content_mismatch", phase="verified")
                    if state == "promoted":
                        member["state"] = "verified"
                        save_receipt(phase="promoting")
                    continue
                staging = _safe_set_staging(staging_path, target)
                if state == "restoring":
                    if target_identity["subvolumeUuid"] != original_uuid:
                        raise NasDataBackupError(code="receipt_mismatch", phase="receipt")
                    _require_empty(target, "backup-set restore target")
                    _discard_incomplete_set_staging(
                        staging,
                        restored,
                        target=target,
                        target_subvolume_uuid=original_uuid,
                        runner=runner,
                    )
                    member.update(
                        {
                            "preparedSubvolumeUuid": None,
                            "staging": None,
                            "restored": None,
                            "tree": None,
                            "state": "pending",
                        }
                    )
                    save_receipt(phase="preparing")
                    continue
                if prepared_uuid is None or member["tree"] is None:
                    raise NasDataBackupError(code="receipt_unavailable", phase="receipt")
                restored_identity = _btrfs_subvolume_identity(restored, runner)
                if (
                    target_identity["subvolumeUuid"] == original_uuid
                    and restored_identity["subvolumeUuid"] == prepared_uuid
                ):
                    if state != "prepared" or tree_identity(restored) != member["tree"]:
                        raise NasDataBackupError(code="receipt_mismatch", phase="receipt")
                elif (
                    target_identity["subvolumeUuid"] == prepared_uuid
                    and restored_identity["subvolumeUuid"] == original_uuid
                ):
                    if tree_identity(target) != member["tree"]:
                        raise NasDataBackupError(code="content_mismatch", phase="promoting")
                    member["state"] = "promoted"
                    save_receipt(phase="promoting")
                else:
                    raise NasDataBackupError(code="receipt_mismatch", phase="receipt")

            for member in receipt["members"]:
                if member["state"] != "pending":
                    continue
                target = _require_empty(Path(member["restoreTarget"]), "backup-set restore target")
                target_identity = _btrfs_subvolume_identity(target, runner)
                if target_identity["subvolumeUuid"] != member["targetSubvolumeUuid"]:
                    raise NasDataBackupError(code="receipt_mismatch", phase="receipt")
                staging, restored = _allocate_set_staging(target, member["repositorySource"])
                member.update(
                    {
                        "staging": str(staging),
                        "restored": str(restored),
                        "state": "restoring",
                    }
                )
                try:
                    save_receipt(phase="preparing")
                except Exception:
                    staging.rmdir()
                    raise
                prepared_identity = _create_set_staging_subvolume(staging, restored, runner)
                if (
                    prepared_identity["filesystemUuid"] != member["filesystemUuid"]
                    or prepared_identity["subvolumeUuid"] == member["targetSubvolumeUuid"]
                ):
                    raise NasDataBackupError("restore staging filesystem identity changed")
                _restic(
                    [
                        *_restic_base(repository, descriptor),
                        "restore",
                        selected["id"],
                        "--target",
                        str(staging),
                        "--include",
                        member["repositorySource"],
                        "--overwrite",
                        "never",
                    ],
                    descriptor,
                    runner,
                    phase="restore_transfer",
                )
                restored_root = _restored_root(staging, Path(member["repositorySource"]))
                if restored_root != restored:
                    raise NasDataBackupError("restic restored an unexpected backup-set hierarchy")
                if (
                    _btrfs_subvolume_identity(restored, runner)["subvolumeUuid"]
                    != prepared_identity["subvolumeUuid"]
                ):
                    raise NasDataBackupError("restore staging subvolume identity changed")
                restored_tree = tree_identity(restored)
                _sync_filesystem(restored)
                member.update(
                    {
                        "preparedSubvolumeUuid": prepared_identity["subvolumeUuid"],
                        "tree": restored_tree,
                        "state": "prepared",
                    }
                )
                save_receipt(phase="preparing")

            _restic(
                [*_restic_base(repository, descriptor), "check", "--read-data"],
                descriptor,
                runner,
            )
            save_receipt(phase="prepared")
            save_receipt(phase="promoting")
            for member in receipt["members"]:
                if member["state"] in {"promoted", "verified"}:
                    continue
                target = _require_empty(Path(member["restoreTarget"]), "backup-set restore target")
                restored = _safe_directory(Path(member["restored"]), "prepared backup-set restore")
                if (
                    _btrfs_subvolume_identity(target, runner)["subvolumeUuid"]
                    != member["targetSubvolumeUuid"]
                    or _btrfs_subvolume_identity(restored, runner)["subvolumeUuid"]
                    != member["preparedSubvolumeUuid"]
                    or tree_identity(restored) != member["tree"]
                ):
                    raise NasDataBackupError(code="receipt_mismatch", phase="promoting")
                exchange(target, restored)
                if (
                    _btrfs_subvolume_identity(target, runner)["subvolumeUuid"]
                    != member["preparedSubvolumeUuid"]
                    or tree_identity(target) != member["tree"]
                ):
                    raise NasDataBackupError(code="content_mismatch", phase="promoting")
                _sync_filesystem(target)
                member["state"] = "promoted"
                save_receipt(phase="promoting")

            for member in receipt["members"]:
                target = _safe_directory(
                    Path(member["restoreTarget"]), "restored backup-set target"
                )
                if (
                    _btrfs_subvolume_identity(target, runner)["subvolumeUuid"]
                    != member["preparedSubvolumeUuid"]
                    or tree_identity(target) != member["tree"]
                ):
                    raise NasDataBackupError(code="content_mismatch", phase="verified")
                staging = Path(member["staging"])
                restored = Path(member["restored"])
                if staging.exists():
                    _safe_set_staging(staging, target)
                    _remove_set_staging(
                        staging,
                        restored,
                        expected_subvolume_uuid=member["targetSubvolumeUuid"],
                        runner=runner,
                    )
                member["state"] = "verified"
                save_receipt(phase="promoting")
            save_receipt(phase="verified")
    return {
        "repositoryId": receipt["repositoryId"],
        "snapshotId": receipt["snapshotId"],
        "setId": receipt["setId"],
        "manifestSha256": receipt["manifestSha256"],
        "memberCount": len(receipt["members"]),
        "members": [
            {
                "sharedFolderRef": member["sharedFolderRef"],
                "filesystemUuid": member["filesystemUuid"],
                "snapshotId": member["snapshotId"],
                "contentVerified": member["state"] == "verified",
            }
            for member in receipt["members"]
        ],
        "fullReadVerified": True,
        "contentVerified": all(member["state"] == "verified" for member in receipt["members"]),
        "pathsRedacted": True,
        "phase": receipt["phase"],
        "recovery": "resumed" if resumed else "fresh_restore",
    }


def _restore_set_commit_hint(manifest: Mapping[str, Any], receipt_directory: Path) -> bool | None:
    try:
        receipt = BackupSetRestoreReceipts(receipt_directory, manifest["setId"]).load()
    except (KeyError, NasDataBackupError):
        return False
    if receipt is None or receipt.get("phase") in {"preparing", "prepared"}:
        return False
    if receipt.get("phase") == "verified" or any(
        isinstance(member, dict) and member.get("state") in {"promoted", "verified"}
        for member in receipt.get("members", [])
    ):
        return True
    # The durable phase is written before the first exchange.  With no member
    # update yet, a crash may be on either side of the exchange syscall.
    return None


def restore_set(**kwargs: Any) -> dict[str, Any]:
    """Run or resume restore-set while preserving honest commit diagnostics."""
    manifest = kwargs.get("manifest")
    receipt_directory = kwargs.get("receipt_directory", RESTORE_SET_RECEIPTS)
    try:
        return _restore_set_impl(**kwargs)
    except NasDataBackupError as exc:
        if isinstance(manifest, Mapping) and isinstance(receipt_directory, Path):
            committed = _restore_set_commit_hint(manifest, receipt_directory)
            if committed is not False:
                exc.committed = committed
                exc.phase = "promoting"
                exc.next_step = "resume_restore_set"
        raise
    except OSError as exc:
        committed: bool | None = False
        if isinstance(manifest, Mapping) and isinstance(receipt_directory, Path):
            committed = _restore_set_commit_hint(manifest, receipt_directory)
        error = filesystem_failure(exc, phase="promoting" if committed is not False else "restore")
        if committed is not False:
            error.committed = committed
            error.next_step = "resume_restore_set"
        raise error from exc


def init_repository(
    *,
    repository: Path,
    repository_mount: Path,
    deployment_root: Path,
    appliance_env: Path | None,
    state_root_override: Path | None = None,
    nas_root_override: Path | None = None,
    password: bytes,
    runner: Runner = _run,
) -> dict[str, Any]:
    repository, _nas = _context(
        repository=repository,
        repository_mount=repository_mount,
        deployment_root=deployment_root,
        appliance_env=appliance_env,
        state_root_override=state_root_override,
        nas_root_override=nas_root_override,
    )
    if next(repository.iterdir(), None) is not None:
        raise NasDataBackupError("new NAS backup repository directory must be empty")
    with _operation_lock(), _password_memfd(password) as descriptor:
        _restic(
            [*_restic_base(repository, descriptor), "init", "--repository-version", "2"],
            descriptor,
            runner,
        )
        _restic([*_restic_base(repository, descriptor), "check", "--read-data"], descriptor, runner)
        identity = _repository_id(repository, descriptor, runner)
    return {"repositoryId": identity, "encrypted": True, "fullReadVerified": True}


def backup(
    *,
    repository: Path,
    repository_mount: Path,
    deployment_root: Path,
    appliance_env: Path | None,
    state_root_override: Path | None = None,
    nas_root_override: Path | None = None,
    source_snapshot: Path,
    password: bytes,
    runner: Runner = _run,
    mountinfo: Path = Path("/proc/self/mountinfo"),
) -> dict[str, Any]:
    repository, nas_root = _context(
        repository=repository,
        repository_mount=repository_mount,
        deployment_root=deployment_root,
        appliance_env=appliance_env,
        state_root_override=state_root_override,
        nas_root_override=nas_root_override,
    )
    source = _safe_directory(source_snapshot, "read-only NAS snapshot")
    snapshot_mount = _require_read_only_snapshot(source, mountinfo)
    _require_snapshot_independence(source, nas_root, mountinfo)
    if source.stat().st_dev == repository.stat().st_dev:
        raise NasDataBackupError("NAS snapshot and backup repository share a filesystem")
    host = "echo-nas-" + hashlib.sha256(str(nas_root).encode()).hexdigest()[:16]
    with _operation_lock(), _password_memfd(password) as descriptor:
        identity = _repository_id(repository, descriptor, runner)
        completed = _restic(
            [
                *_restic_base(repository, descriptor),
                "backup",
                "--json",
                "--one-file-system",
                "--host",
                host,
                "--tag",
                TAG,
                str(source),
            ],
            descriptor,
            runner,
        )
        snapshot_ids: list[str] = []
        for line in completed.stdout.splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise NasDataBackupError("restic backup output is malformed") from exc
            if isinstance(item, dict) and item.get("message_type") == "summary":
                snapshot_id = item.get("snapshot_id")
                if isinstance(snapshot_id, str) and SNAPSHOT.fullmatch(snapshot_id):
                    snapshot_ids.append(snapshot_id)
        if len(snapshot_ids) != 1:
            raise NasDataBackupError("restic backup did not return one complete snapshot")
        _restic([*_restic_base(repository, descriptor), "check", "--read-data"], descriptor, runner)
        indexed = _snapshots(repository, descriptor, runner)
        selected = _select_snapshot(snapshot_ids[0], indexed)
        if Path(selected["path"]) != source:
            raise NasDataBackupError("authenticated snapshot path changed during backup")
    return {
        "repositoryId": identity,
        "snapshotId": snapshot_ids[0],
        "source": str(source),
        "restoreTarget": str(nas_root),
        "snapshotMount": snapshot_mount,
        "encrypted": True,
        "fullReadVerified": True,
    }


def check_repository(
    *,
    repository: Path,
    repository_mount: Path,
    deployment_root: Path,
    appliance_env: Path | None,
    state_root_override: Path | None = None,
    nas_root_override: Path | None = None,
    password: bytes,
    runner: Runner = _run,
) -> dict[str, Any]:
    repository, _nas = _context(
        repository=repository,
        repository_mount=repository_mount,
        deployment_root=deployment_root,
        appliance_env=appliance_env,
        state_root_override=state_root_override,
        nas_root_override=nas_root_override,
    )
    with _operation_lock(), _password_memfd(password) as descriptor:
        identity = _repository_id(repository, descriptor, runner)
        _restic([*_restic_base(repository, descriptor), "check", "--read-data"], descriptor, runner)
        snapshots = _snapshots(repository, descriptor, runner)
    return {"repositoryId": identity, "snapshots": len(snapshots), "fullReadVerified": True}


def list_snapshots(
    *,
    repository: Path,
    repository_mount: Path,
    deployment_root: Path,
    appliance_env: Path | None,
    state_root_override: Path | None = None,
    nas_root_override: Path | None = None,
    password: bytes,
    limit: int = 50,
    runner: Runner = _run,
) -> dict[str, Any]:
    """Return a bounded, path-redacted view of the authenticated restic index."""
    if type(limit) is not int or not 1 <= limit <= MAX_LIST_SNAPSHOTS:
        raise NasDataBackupError(f"snapshot list limit must be between 1 and {MAX_LIST_SNAPSHOTS}")
    repository, _nas = _context(
        repository=repository,
        repository_mount=repository_mount,
        deployment_root=deployment_root,
        appliance_env=appliance_env,
        state_root_override=state_root_override,
        nas_root_override=nas_root_override,
    )
    with _operation_lock(), _password_memfd(password) as descriptor:
        identity = _repository_id(repository, descriptor, runner)
        snapshots = _snapshots(repository, descriptor, runner)
    ordered = sorted(
        snapshots,
        key=lambda item: (_snapshot_time(item["time"]), item["id"]),
        reverse=True,
    )
    visible = ordered[:limit]
    return {
        "repositoryId": identity,
        "snapshotCount": len(ordered),
        "snapshots": [
            {
                "snapshotId": item["id"],
                "createdAt": _snapshot_time(item["time"])
                .isoformat(timespec="microseconds")
                .replace("+00:00", "Z"),
            }
            for item in visible
        ],
        "truncated": len(ordered) > len(visible),
        "encrypted": True,
        "verification": "authenticated_index_only",
    }


def list_backup_sets(
    *,
    repository: Path,
    repository_mount: Path,
    deployment_root: Path,
    appliance_env: Path | None,
    state_root_override: Path | None = None,
    nas_root_override: Path | None = None,
    password: bytes,
    limit: int = 50,
    runner: Runner = _run,
) -> dict[str, Any]:
    """Return a bounded public view of authenticated multi-share backup sets."""
    if type(limit) is not int or not 1 <= limit <= MAX_LIST_SNAPSHOTS:
        raise NasDataBackupError(
            f"backup-set list limit must be between 1 and {MAX_LIST_SNAPSHOTS}"
        )
    repository, _nas = _context(
        repository=repository,
        repository_mount=repository_mount,
        deployment_root=deployment_root,
        appliance_env=appliance_env,
        state_root_override=state_root_override,
        nas_root_override=nas_root_override,
    )
    with _operation_lock(), _password_memfd(password) as descriptor:
        repository_id = _repository_id(repository, descriptor, runner)
        snapshots = _backup_set_snapshots(repository, descriptor, runner)
    set_ids = [snapshot["setId"] for snapshot in snapshots]
    if len(set(set_ids)) != len(set_ids):
        raise NasDataBackupError("backup-set repository contains duplicate set IDs")
    ordered = sorted(
        snapshots,
        key=lambda item: (_snapshot_time(item["time"]), item["id"]),
        reverse=True,
    )
    visible = ordered[:limit]
    return {
        "repositoryId": repository_id,
        "setCount": len(ordered),
        "sets": [
            {
                "setId": item["setId"],
                "snapshotId": item["id"],
                "createdAt": _snapshot_time(item["time"])
                .isoformat(timespec="microseconds")
                .replace("+00:00", "Z"),
                "manifestSha256": item["manifestSha256"],
                "memberCount": len(item["members"]),
                "members": [
                    {
                        "sharedFolderRef": member["sharedFolderRef"],
                        "filesystemUuid": member["filesystemUuid"],
                        "snapshotId": member["snapshotId"],
                    }
                    for member in item["members"]
                ],
            }
            for item in visible
        ],
        "truncated": len(ordered) > len(visible),
        "encrypted": True,
        "pathsRedacted": True,
        "verification": "authenticated_index_only",
    }


def restore(
    *,
    repository: Path,
    repository_mount: Path,
    deployment_root: Path,
    appliance_env: Path | None,
    state_root_override: Path | None = None,
    nas_root_override: Path | None = None,
    selector: str,
    confirmation: str,
    password: bytes,
    runner: Runner = _run,
    exchange: Callable[[Path, Path], None] = _exchange_directories,
    receipt_directory: Path | None = None,
    verify_only: bool = False,
) -> dict[str, Any]:
    repository, nas_root = _context(
        repository=repository,
        repository_mount=repository_mount,
        deployment_root=deployment_root,
        appliance_env=appliance_env,
        state_root_override=state_root_override,
        nas_root_override=nas_root_override,
    )
    receipt_root = receipt_directory or RESTORE_RECEIPTS
    if any(
        protected == receipt_root.resolve() or protected in receipt_root.resolve().parents
        for protected in (nas_root, repository)
    ):
        raise NasDataBackupError(code="receipt_unavailable", phase="preflight")
    receipts = RestoreReceipts(receipt_root, nas_root)
    with _operation_lock(), _password_memfd(password) as descriptor:
        identity = _repository_id(repository, descriptor, runner)
        _restic([*_restic_base(repository, descriptor), "check", "--read-data"], descriptor, runner)
        selected = _select_snapshot(selector, _snapshots(repository, descriptor, runner))
        expected_confirmation = f"RESTORE ECHO NAS {selected['id']} TO {nas_root}"
        if confirmation != expected_confirmation:
            raise NasDataBackupError(
                "NAS restore confirmation does not bind the exact snapshot and target",
                code="confirmation_mismatch",
            )
        receipt = receipts.load()
        matches = (
            receipt is not None
            and receipt.get("repositoryId") == identity
            and receipt.get("snapshotId") == selected["id"]
        )
        if verify_only or next(nas_root.iterdir(), None) is not None:
            if not matches:
                raise NasDataBackupError(
                    code="receipt_mismatch" if verify_only else "target_not_empty"
                )
            return _verify_promoted_restore(nas_root, receipts, receipt, recovery=True)
        # A completed empty snapshot is also safe to reverify without rewriting.
        if matches and receipt.get("phase") in {"promoted", "verified"}:
            return _verify_promoted_restore(nas_root, receipts, receipt, recovery=True)
        nas_root = _require_empty(nas_root, "configured NAS restore target")
        staging = Path(
            tempfile.mkdtemp(prefix=f".{nas_root.name}.echo-nas-restore-", dir=nas_root.parent)
        )
        os.chmod(staging, 0o700)
        promoted = False
        phase = "restore_transfer"
        try:
            _restic(
                [
                    *_restic_base(repository, descriptor),
                    "restore",
                    selected["id"],
                    "--target",
                    str(staging),
                    "--overwrite",
                    "never",
                ],
                descriptor,
                runner,
            )
            restored = _restored_root(staging, Path(selected["path"]))
            phase = "content_verification"
            tree = tree_identity(restored, max_entries=MAX_TREE_ENTRIES, max_depth=MAX_TREE_DEPTH)
            # Verify repository reads while the live target is still untouched.
            _restic(
                [*_restic_base(repository, descriptor), "check", "--read-data"], descriptor, runner
            )
            if nas_root.stat().st_dev != restored.stat().st_dev:
                raise NasDataBackupError("NAS restore staging is on another filesystem")
            _require_empty(nas_root, "configured NAS restore target")
            phase = "prepared"
            receipt = {
                "repositoryId": identity,
                "snapshotId": selected["id"],
                "tree": tree,
                "phase": "prepared",
                "atomicPromotion": False,
                "cleanupPending": True,
                "preparedAt": datetime.now(UTC).isoformat(),
            }
            receipts.save(receipt)
            exchange(nas_root, restored)
            promoted = True
            phase = "promoted"
            receipt = {**receipt, "phase": "promoted", "atomicPromotion": True}
            receipts.save(receipt)
            try:
                _remove_empty_restore_scaffold(staging, restored)
                receipt["cleanupPending"] = False
            except OSError:
                receipt["cleanupPending"] = True
            return _verify_promoted_restore(nas_root, receipts, receipt, recovery=False)
        except NasDataBackupError as exc:
            if promoted:
                exc.committed = True
                if exc.phase not in {"promoted_verification", "verified"}:
                    exc.phase = "promoted"
                if exc.code != "content_mismatch":
                    exc.next_step = "verify_restore_without_rewriting"
            raise
        except OSError as exc:
            error = filesystem_failure(exc, phase=phase, committed=promoted)
            if promoted:
                error.next_step = "verify_restore_without_rewriting"
            raise error from exc


def _verify_promoted_restore(
    target: Path,
    receipts: RestoreReceipts,
    receipt: dict[str, Any],
    *,
    recovery: bool,
) -> dict[str, Any]:
    """Compare live content to the pre-promotion staging hash without rewriting it."""
    # A prepared receipt may survive a crash on either side of exchange. Its
    # content hash can prove a match; a mismatch cannot prove no promotion.
    committed = True if receipt.get("phase") in {"promoted", "verified"} else None
    verification_phase = "promoted_verification" if committed else "promotion_unconfirmed"
    try:
        tree = tree_identity(target, max_entries=MAX_TREE_ENTRIES, max_depth=MAX_TREE_DEPTH)
    except OSError as exc:
        error = filesystem_failure(exc, phase=verification_phase, committed=committed)
        error.next_step = "verify_restore_without_rewriting"
        raise error from exc
    except NasDataBackupError as exc:
        exc.committed = committed
        exc.phase = verification_phase
        raise
    if tree != receipt.get("tree"):
        raise NasDataBackupError(
            code="content_mismatch",
            phase=verification_phase,
            committed=committed,
        )
    receipt = {**receipt, "phase": "verified", "verifiedAt": datetime.now(UTC).isoformat()}
    try:
        receipts.save(receipt)
    except NasDataBackupError as exc:
        exc.committed = True
        exc.phase = "verified"
        exc.next_step = "verify_restore_without_rewriting"
        raise
    except OSError as exc:
        error = filesystem_failure(exc, phase="verified", committed=True)
        error.next_step = "verify_restore_without_rewriting"
        raise error from exc
    return {
        "repositoryId": receipt["repositoryId"],
        "snapshotId": receipt["snapshotId"],
        "restoreTarget": str(target),
        **tree,
        "atomicPromotion": receipt.get("atomicPromotion") is True,
        "fullReadVerified": True,
        "contentVerified": True,
        "phase": "verified",
        "recovery": "verified_existing" if recovery else "fresh_restore",
        "cleanupPending": receipt.get("cleanupPending") is True,
    }


def _common(command: argparse.ArgumentParser) -> None:
    command.add_argument("--repository", type=Path, required=True)
    command.add_argument("--repository-mount", type=Path, required=True)
    command.add_argument("--deployment-root", type=Path, required=True)
    command.add_argument("--appliance-env", type=Path)
    command.add_argument("--state-root", type=Path)
    command.add_argument("--nas-root", type=Path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "check"):
        _common(subparsers.add_parser(name))
    listing = subparsers.add_parser("list")
    _common(listing)
    listing.add_argument("--limit", type=int, default=50)
    set_listing = subparsers.add_parser("list-sets")
    _common(set_listing)
    set_listing.add_argument("--limit", type=int, default=50)
    set_plan = subparsers.add_parser("plan-set")
    _common(set_plan)
    set_plan.add_argument("--manifest", type=Path, required=True)
    set_create = subparsers.add_parser("backup-set")
    _common(set_create)
    set_create.add_argument("--manifest", type=Path, required=True)
    set_create.add_argument("--plan-id", required=True)
    set_restore_plan = subparsers.add_parser("plan-restore-set")
    _common(set_restore_plan)
    set_restore_plan.add_argument("--manifest", type=Path, required=True)
    set_restore_plan.add_argument("--snapshot", default="latest")
    set_restore = subparsers.add_parser("restore-set")
    _common(set_restore)
    set_restore.add_argument("--manifest", type=Path, required=True)
    set_restore.add_argument("--snapshot", default="latest")
    set_restore.add_argument("--plan-id", required=True)
    set_restore.add_argument("--confirm", required=True)
    set_restore.add_argument("--receipt-directory", type=Path, default=RESTORE_SET_RECEIPTS)
    create = subparsers.add_parser("backup")
    _common(create)
    create.add_argument("--source-snapshot", type=Path, required=True)
    for name in ("restore", "verify-restore"):
        recover = subparsers.add_parser(name)
        _common(recover)
        recover.add_argument("--snapshot", default="latest")
        recover.add_argument("--confirm", required=True)
        recover.add_argument("--receipt-directory", type=Path, default=RESTORE_RECEIPTS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if os.geteuid() != 0 or os.uname().sysname != "Linux":
        print("Echo NAS data backup requires Linux root", file=sys.stderr)
        return 1
    try:
        if args.command == "plan-set":
            report = plan_backup_set(
                manifest=load_backup_set_manifest(args.manifest),
                repository=args.repository,
                repository_mount=args.repository_mount,
                deployment_root=args.deployment_root,
                appliance_env=args.appliance_env,
                state_root_override=args.state_root,
                nas_root_override=args.nas_root,
            )
        else:
            if not RESTIC.is_file() or RESTIC.is_symlink():
                raise NasDataBackupError(
                    "Debian restic runtime is unavailable", code="runtime_unavailable"
                )
            password = _password_from_credential()
            common = {
                "repository": args.repository,
                "repository_mount": args.repository_mount,
                "deployment_root": args.deployment_root,
                "appliance_env": args.appliance_env,
                "state_root_override": args.state_root,
                "nas_root_override": args.nas_root,
                "password": password,
            }
            if args.command == "init":
                report = init_repository(**common)
            elif args.command == "backup":
                report = backup(**common, source_snapshot=args.source_snapshot)
            elif args.command == "backup-set":
                report = backup_set(
                    **common,
                    manifest=load_backup_set_manifest(args.manifest),
                    plan_id=args.plan_id,
                )
            elif args.command == "plan-restore-set":
                report = plan_restore_set(
                    **common,
                    manifest=load_backup_set_manifest(args.manifest),
                    selector=args.snapshot,
                )
            elif args.command == "restore-set":
                report = restore_set(
                    **common,
                    manifest=load_backup_set_manifest(args.manifest),
                    selector=args.snapshot,
                    plan_id=args.plan_id,
                    confirmation=args.confirm,
                    receipt_directory=args.receipt_directory,
                )
            elif args.command == "check":
                report = check_repository(**common)
            elif args.command == "list":
                report = list_snapshots(**common, limit=args.limit)
            elif args.command == "list-sets":
                report = list_backup_sets(**common, limit=args.limit)
            else:
                report = restore(
                    **common,
                    selector=args.snapshot,
                    confirmation=args.confirm,
                    receipt_directory=args.receipt_directory,
                    verify_only=args.command == "verify-restore",
                )
    except (
        OSError,
        UnicodeError,
        ValueError,
        subprocess.SubprocessError,
        NasDataBackupError,
    ) as exc:
        diagnostic = (
            exc
            if isinstance(exc, NasDataBackupError)
            else (
                filesystem_failure(exc, phase="preflight")
                if isinstance(exc, OSError)
                else NasDataBackupError()
            )
        )
        print(
            json.dumps(
                {
                    "schemaVersion": SCHEMA_VERSION,
                    "kind": "echo.nas-data-backup.failure",
                    "ok": False,
                    **diagnostic.public(),
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    finally:
        if "password" in locals():
            password = b""
    print(
        json.dumps(
            {
                "schemaVersion": SCHEMA_VERSION,
                "kind": f"echo.nas-data-backup.{args.command}",
                "completedAt": datetime.now(UTC).isoformat(),
                **report,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
