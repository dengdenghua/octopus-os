#!/usr/bin/env python3
"""Encrypted off-device backup and empty-volume restore for Echo NAS data.

The appliance-state backup intentionally excludes user files.  This command
provides the separate data path: it accepts only a read-only source snapshot,
uses restic's authenticated encrypted repository, performs a full repository
read before and after backup/restore, and restores only into the configured,
empty NAS root.  Promotion is one Linux ``renameat2(RENAME_EXCHANGE)`` so a
power loss cannot expose a half-copied live tree.
"""

from __future__ import annotations

import argparse
import ctypes
import getpass
import hashlib
import json
import os
import re
import stat
import subprocess  # nosec B404
import sys
import tempfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

try:
    from deploy.appliance.external_storage import (
        ExternalStorageError,
        _nas_root,
        verify_external_storage,
    )
except ModuleNotFoundError:
    from external_storage import (  # type: ignore[no-redef]
        ExternalStorageError,
        _nas_root,
        verify_external_storage,
    )


SCHEMA_VERSION = 1
RESTIC = Path("/usr/bin/restic")
TAG = "echo-nas-data-v1"
# Public systemd credential key, never the credential value itself.
PASSWORD_CREDENTIAL = "echo-nas-backup-password"  # nosec B105
LOCK_FILE = Path("/run/lock/echo-nas-data-backup.lock")
MAX_PASSWORD_BYTES = 4096
MAX_OUTPUT_BYTES = 16 * 1024 * 1024
MAX_TREE_ENTRIES = 10_000_000
MAX_TREE_DEPTH = 512
SNAPSHOT = re.compile(r"^[0-9a-f]{64}$")
REPOSITORY_ID = re.compile(r"^[0-9a-f]{16,64}$")
AT_FDCWD = -100
RENAME_EXCHANGE = 2


class NasDataBackupError(RuntimeError):
    """The NAS backup or recovery operation is unsafe or incomplete."""


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

    parent = _safe_directory(LOCK_FILE.parent, "NAS backup lock directory")
    if parent.stat().st_uid != 0 or stat.S_IMODE(parent.stat().st_mode) & 0o022:
        raise NasDataBackupError("NAS backup lock directory is unsafe")
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


def _restic_base(repository: Path, password_fd: int) -> list[str]:
    return [
        str(RESTIC),
        "--repository",
        str(repository),
        "--password-file",
        f"/proc/self/fd/{password_fd}",
        "--no-cache",
    ]


def _restic(
    arguments: Sequence[str], password_fd: int, runner: Runner = _run
) -> subprocess.CompletedProcess[str]:
    completed = runner(
        arguments,
        pass_fds=(password_fd,),
        env=_fixed_environment(),
    )
    if (
        completed.returncode != 0
        or len(completed.stdout.encode("utf-8", "replace")) > MAX_OUTPUT_BYTES
        or len(completed.stderr.encode("utf-8", "replace")) > MAX_OUTPUT_BYTES
    ):
        raise NasDataBackupError(f"restic failed safely with status {completed.returncode}")
    return completed


def _repository_id(repository: Path, password_fd: int, runner: Runner) -> str:
    completed = _restic(
        [*_restic_base(repository, password_fd), "cat", "config"], password_fd, runner
    )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise NasDataBackupError("restic repository config is malformed") from exc
    identity = value.get("id") if isinstance(value, dict) else None
    if not isinstance(identity, str) or REPOSITORY_ID.fullmatch(identity) is None:
        raise NasDataBackupError("restic repository identity is invalid")
    return identity


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
        result.append({"id": snapshot_id, "path": paths[0], "time": str(item.get("time", ""))})
    return result


def _select_snapshot(selector: str, snapshots: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    if not snapshots:
        raise NasDataBackupError("NAS backup repository has no snapshots")
    if selector == "latest":
        selected = max(snapshots, key=lambda item: str(item["time"]))
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
            nas_root_override=nas_root,
        )
    except ExternalStorageError as exc:
        raise NasDataBackupError(str(exc)) from exc
    metadata = repository.lstat()
    if metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) & 0o077:
        raise NasDataBackupError("NAS backup repository must be private and root-owned")
    return repository, nas_root


def init_repository(
    *,
    repository: Path,
    repository_mount: Path,
    deployment_root: Path,
    appliance_env: Path | None,
    password: bytes,
    runner: Runner = _run,
) -> dict[str, Any]:
    repository, _nas = _context(
        repository=repository,
        repository_mount=repository_mount,
        deployment_root=deployment_root,
        appliance_env=appliance_env,
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
    password: bytes,
    runner: Runner = _run,
) -> dict[str, Any]:
    repository, _nas = _context(
        repository=repository,
        repository_mount=repository_mount,
        deployment_root=deployment_root,
        appliance_env=appliance_env,
    )
    with _operation_lock(), _password_memfd(password) as descriptor:
        identity = _repository_id(repository, descriptor, runner)
        _restic([*_restic_base(repository, descriptor), "check", "--read-data"], descriptor, runner)
        snapshots = _snapshots(repository, descriptor, runner)
    return {"repositoryId": identity, "snapshots": len(snapshots), "fullReadVerified": True}


def restore(
    *,
    repository: Path,
    repository_mount: Path,
    deployment_root: Path,
    appliance_env: Path | None,
    selector: str,
    confirmation: str,
    password: bytes,
    runner: Runner = _run,
    exchange: Callable[[Path, Path], None] = _exchange_directories,
) -> dict[str, Any]:
    repository, nas_root = _context(
        repository=repository,
        repository_mount=repository_mount,
        deployment_root=deployment_root,
        appliance_env=appliance_env,
    )
    nas_root = _require_empty(nas_root, "configured NAS restore target")
    with _operation_lock(), _password_memfd(password) as descriptor:
        identity = _repository_id(repository, descriptor, runner)
        _restic([*_restic_base(repository, descriptor), "check", "--read-data"], descriptor, runner)
        selected = _select_snapshot(selector, _snapshots(repository, descriptor, runner))
        expected_confirmation = f"RESTORE ECHO NAS {selected['id']} TO {nas_root}"
        if confirmation != expected_confirmation:
            raise NasDataBackupError(
                "NAS restore confirmation does not bind the exact snapshot and empty target"
            )
        staging = Path(
            tempfile.mkdtemp(prefix=f".{nas_root.name}.echo-nas-restore-", dir=nas_root.parent)
        )
        os.chmod(staging, 0o700)
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
            tree = _tree_safe(restored)
            if nas_root.stat().st_dev != restored.stat().st_dev:
                raise NasDataBackupError("NAS restore staging is on another filesystem")
            exchange(nas_root, restored)
            _remove_empty_restore_scaffold(staging, restored)
            promoted_tree = _tree_safe(nas_root)
            if promoted_tree != tree:
                raise NasDataBackupError("promoted NAS tree differs from authenticated staging")
        except BaseException:
            # Before exchange the empty live target remains untouched.  After a
            # successful exchange the live target is already a complete tree;
            # leftover private staging is preserved for operator inspection.
            raise
        _restic([*_restic_base(repository, descriptor), "check", "--read-data"], descriptor, runner)
    return {
        "repositoryId": identity,
        "snapshotId": selected["id"],
        "restoreTarget": str(nas_root),
        "entries": promoted_tree["entries"],
        "logicalBytes": promoted_tree["logicalBytes"],
        "atomicPromotion": True,
        "fullReadVerified": True,
    }


def _common(command: argparse.ArgumentParser) -> None:
    command.add_argument("--repository", type=Path, required=True)
    command.add_argument("--repository-mount", type=Path, required=True)
    command.add_argument("--deployment-root", type=Path, required=True)
    command.add_argument("--appliance-env", type=Path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "check"):
        _common(subparsers.add_parser(name))
    create = subparsers.add_parser("backup")
    _common(create)
    create.add_argument("--source-snapshot", type=Path, required=True)
    recover = subparsers.add_parser("restore")
    _common(recover)
    recover.add_argument("--snapshot", default="latest")
    recover.add_argument("--confirm", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if os.geteuid() != 0 or os.uname().sysname != "Linux":
        print("Echo NAS data backup requires Linux root", file=sys.stderr)
        return 1
    try:
        if not RESTIC.is_file() or RESTIC.is_symlink():
            raise NasDataBackupError("Debian restic runtime is unavailable")
        password = _password_from_credential()
        common = {
            "repository": args.repository,
            "repository_mount": args.repository_mount,
            "deployment_root": args.deployment_root,
            "appliance_env": args.appliance_env,
            "password": password,
        }
        if args.command == "init":
            report = init_repository(**common)
        elif args.command == "backup":
            report = backup(**common, source_snapshot=args.source_snapshot)
        elif args.command == "check":
            report = check_repository(**common)
        else:
            report = restore(
                **common,
                selector=args.snapshot,
                confirmation=args.confirm,
            )
    except (
        OSError,
        UnicodeError,
        ValueError,
        subprocess.SubprocessError,
        NasDataBackupError,
    ) as exc:
        print(f"Echo NAS data backup failed: {exc}", file=sys.stderr)
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
