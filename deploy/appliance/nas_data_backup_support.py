"""Safe diagnostics, content identities and durable NAS restore receipts."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

ERRORS = {
    "unknown": ("Backup operation did not complete.", "inspect_device_and_retry"),
    "target_not_mounted": (
        "The declared backup filesystem is not mounted.",
        "mount_expected_filesystem",
    ),
    "insufficient_space": (
        "The operation ran out of storage space or quota.",
        "free_space_and_retry",
    ),
    "permission_denied": ("The operation was denied access.", "repair_access_and_retry"),
    "network_unavailable": ("The backup connection failed.", "restore_connection_and_retry"),
    "repository_unavailable": (
        "The backup repository could not be opened.",
        "check_repository_and_mount",
    ),
    "operation_timeout": ("The backup operation timed out.", "inspect_device_and_retry"),
    "runtime_unavailable": ("The backup runtime could not be started.", "repair_backup_runtime"),
    "output_limit": ("The backup runtime returned excessive output.", "inspect_backup_runtime"),
    "content_mismatch": (
        "Restored content does not match the tree verified before promotion.",
        "inspect_restored_data",
    ),
    "source_changed": (
        "The restored tree changed while being verified.",
        "stop_other_writers_and_verify",
    ),
    "unsafe_tree": (
        "The restored tree contains an unsafe or unreadable entry.",
        "inspect_restored_data",
    ),
    "receipt_unavailable": (
        "A protected restore receipt could not be read or saved.",
        "repair_receipt_storage",
    ),
    "receipt_mismatch": (
        "No valid receipt matches this snapshot and restore target.",
        "inspect_existing_target",
    ),
    "target_not_empty": (
        "The restore target is not empty and has no matching receipt.",
        "inspect_existing_target",
    ),
    "confirmation_mismatch": (
        "Restore confirmation does not match the selected snapshot and target.",
        "confirm_exact_snapshot_and_target",
    ),
}


class NasDataBackupError(RuntimeError):
    """A fixed public diagnostic; raw process output is never retained here."""

    def __init__(
        self,
        message: str = "Backup operation did not complete",
        *,
        code: str = "unknown",
        phase: str = "preflight",
        committed: bool | None = False,
        next_step: str | None = None,
    ):
        super().__init__(message)
        self.code = code if code in ERRORS else "unknown"
        self.phase = phase
        self.committed = committed
        self.next_step = next_step

    def public(self) -> dict[str, Any]:
        message, next_step = ERRORS[self.code]
        return {
            "code": self.code,
            "phase": self.phase,
            "message": message,
            "nextStep": self.next_step or next_step,
            "committed": self.committed,
        }


def process_failure(stderr: str, *, phase: str) -> NasDataBackupError:
    evidence = stderr.casefold()
    code = "unknown"
    if any(token in evidence for token in ("no space left on device", "disk quota exceeded")):
        code = "insufficient_space"
    elif any(
        token in evidence
        for token in ("permission denied", "access is denied", "operation not permitted")
    ):
        code = "permission_denied"
    elif any(
        token in evidence
        for token in (
            "network is unreachable",
            "no route to host",
            "connection refused",
            "connection reset by peer",
            "temporary failure in name resolution",
            "tls handshake timeout",
        )
    ):
        code = "network_unavailable"
    elif "no such file or directory" in evidence and any(
        token in evidence for token in ("repository", "config")
    ):
        code = "repository_unavailable"
    return NasDataBackupError(ERRORS[code][0], code=code, phase=phase)


def filesystem_failure(
    error: OSError, *, phase: str, committed: bool | None = False
) -> NasDataBackupError:
    code = {
        errno.ENOSPC: "insufficient_space",
        errno.EDQUOT: "insufficient_space",
        errno.EACCES: "permission_denied",
        errno.EPERM: "permission_denied",
    }.get(error.errno, "unknown")
    return NasDataBackupError(ERRORS[code][0], code=code, phase=phase, committed=committed)


def tree_identity(
    root: Path, *, max_entries: int = 10_000_000, max_depth: int = 512
) -> dict[str, Any]:
    """Hash deterministic paths, entry types, link targets and streamed file bytes."""
    digest = hashlib.sha256(b"echo-nas-tree-v1\n")
    entries = logical_bytes = 0

    def fail_walk(error: OSError) -> None:
        raise error

    for current, directories, files in os.walk(
        root, topdown=True, followlinks=False, onerror=fail_walk
    ):
        directories.sort()
        relative = Path(current).relative_to(root)
        if len(relative.parts) > max_depth:
            raise NasDataBackupError(
                "restored tree exceeds depth limit",
                code="unsafe_tree",
                phase="content_verification",
            )
        for name in sorted([*directories, *files]):
            entries += 1
            if entries > max_entries:
                raise NasDataBackupError(
                    "restored tree exceeds entry limit",
                    code="unsafe_tree",
                    phase="content_verification",
                )
            path = Path(current) / name
            key = path.relative_to(root).as_posix()
            before = path.lstat()
            if stat.S_ISLNK(before.st_mode):
                target = os.readlink(path)
                target_path = PurePosixPath(target)
                parts = list(PurePosixPath(key).parent.parts)
                if target_path.is_absolute() or "\\" in target:
                    raise NasDataBackupError(
                        "unsafe restored link", code="unsafe_tree", phase="content_verification"
                    )
                for part in target_path.parts:
                    if part == "..":
                        if not parts:
                            raise NasDataBackupError(
                                "escaping restored link",
                                code="unsafe_tree",
                                phase="content_verification",
                            )
                        parts.pop()
                    elif part != ".":
                        parts.append(part)
                record = [key, "link", target]
            elif stat.S_ISDIR(before.st_mode):
                record = [key, "directory"]
            elif stat.S_ISREG(before.st_mode):
                flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                descriptor = os.open(path, flags)
                with os.fdopen(descriptor, "rb") as stream:
                    opened = os.fstat(stream.fileno())
                    if (opened.st_dev, opened.st_ino, opened.st_size) != (
                        before.st_dev,
                        before.st_ino,
                        before.st_size,
                    ):
                        raise NasDataBackupError(
                            "restored file changed",
                            code="source_changed",
                            phase="content_verification",
                        )
                    content = hashlib.sha256()
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        content.update(chunk)
                    after = os.fstat(stream.fileno())
                final = path.lstat()
                if (opened.st_size, opened.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or (
                    final.st_dev,
                    final.st_ino,
                    final.st_size,
                    final.st_mtime_ns,
                ) != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns):
                    raise NasDataBackupError(
                        "restored file changed", code="source_changed", phase="content_verification"
                    )
                logical_bytes += before.st_size
                record = [key, "file", before.st_size, content.hexdigest()]
            else:
                raise NasDataBackupError(
                    "special restored file", code="unsafe_tree", phase="content_verification"
                )
            digest.update(
                json.dumps(record, ensure_ascii=True, separators=(",", ":")).encode("ascii") + b"\n"
            )
    return {"entries": entries, "logicalBytes": logical_bytes, "treeSha256": digest.hexdigest()}


def _private_directory(directory: Path) -> None:
    if not directory.is_absolute() or ".." in directory.parts:
        raise NasDataBackupError(code="receipt_unavailable", phase="receipt")
    cursor = Path(directory.anchor)
    for part in directory.parts[1:]:
        cursor /= part
        if not cursor.exists():
            cursor.mkdir(mode=0o700)
        metadata = cursor.lstat()
        # Sticky, root-owned /tmp ancestors protect root-owned entries too.
        unsafe_write = (
            stat.S_IMODE(metadata.st_mode) & 0o022 and not metadata.st_mode & stat.S_ISVTX
        )
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != 0 or unsafe_write:
            raise NasDataBackupError(code="receipt_unavailable", phase="receipt")
    if stat.S_IMODE(directory.stat().st_mode) & 0o077:
        raise NasDataBackupError(code="receipt_unavailable", phase="receipt")


class RestoreReceipts:
    def __init__(self, directory: Path, target: Path):
        self.directory = directory
        self.target = str(target.resolve())
        self.path = directory / (hashlib.sha256(self.target.encode()).hexdigest() + ".json")

    def load(self) -> dict[str, Any] | None:
        _private_directory(self.directory)
        if not self.path.exists() and not self.path.is_symlink():
            return None
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self.path, flags)
            with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
                info = os.fstat(stream.fileno())
                if not _private_file(info) or info.st_size > 8192:
                    raise ValueError("unsafe receipt")
                value = json.load(stream)
            if (
                not isinstance(value, dict)
                or value.get("schema") != "echo.nas-restore-receipt.v1"
                or value.get("target") != self.target
                or not _valid_receipt(value)
            ):
                raise ValueError("invalid receipt")
            return value
        except (OSError, ValueError) as error:
            raise NasDataBackupError(code="receipt_unavailable", phase="receipt") from error

    def save(self, value: dict[str, Any]) -> None:
        _private_directory(self.directory)
        value = {**value, "schema": "echo.nas-restore-receipt.v1", "target": self.target}
        descriptor, temporary_name = tempfile.mkstemp(prefix=".receipt-", dir=self.directory)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(value, stream, ensure_ascii=True, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            _sync_directory(self.directory)
        finally:
            temporary.unlink(missing_ok=True)


class BackupSetRestoreReceipts:
    """Durable private transaction state for a multi-target restore."""

    MAX_BYTES = 1024 * 1024
    MAX_INVENTORY_RECEIPTS = 128
    _RECEIPT_NAME = re.compile(
        r"^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.json$"
    )
    _REPOSITORY_ID = re.compile(r"^[0-9a-f]{16,64}$")
    _RECEIPT_KEYS = {
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
    }

    def __init__(self, directory: Path, set_id: str):
        if re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            set_id,
        ) is None:
            raise NasDataBackupError(code="receipt_unavailable", phase="receipt")
        self.directory = directory
        self.set_id = set_id
        self.path = directory / f"{set_id}.json"

    @staticmethod
    def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate receipt key")
            value[key] = item
        return value

    def load(self) -> dict[str, Any] | None:
        _private_directory(self.directory)
        if not self.path.exists() and not self.path.is_symlink():
            return None
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self.path, flags)
            with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
                info = os.fstat(stream.fileno())
                if not _private_file(info) or not 1 <= info.st_size <= self.MAX_BYTES:
                    raise ValueError("unsafe receipt")
                value = json.load(stream, object_pairs_hook=self._reject_duplicates)
            if (
                not isinstance(value, dict)
                or value.get("schema") != "echo.nas-set-restore-receipt.v1"
                or value.get("setId") != self.set_id
            ):
                raise ValueError("invalid receipt")
            return value
        except (OSError, UnicodeError, ValueError) as error:
            raise NasDataBackupError(code="receipt_unavailable", phase="receipt") from error

    @classmethod
    def unfinished_repository_ids(cls, directory: Path) -> frozenset[str]:
        """Return repository identities authorized by unfinished durable receipts."""

        _private_directory(directory)
        set_ids: list[str] = []
        try:
            for entry in directory.iterdir():
                match = cls._RECEIPT_NAME.fullmatch(entry.name)
                if match is not None:
                    set_ids.append(match.group(1))
        except OSError as error:
            raise NasDataBackupError(code="receipt_unavailable", phase="receipt") from error
        if len(set_ids) > cls.MAX_INVENTORY_RECEIPTS:
            raise NasDataBackupError(code="receipt_unavailable", phase="receipt")

        repository_ids: set[str] = set()
        for set_id in sorted(set_ids):
            receipt = cls(directory, set_id).load()
            if receipt is None:
                continue
            repository_id = receipt.get("repositoryId")
            phase = receipt.get("phase")
            if (
                set(receipt) != cls._RECEIPT_KEYS
                or not isinstance(repository_id, str)
                or cls._REPOSITORY_ID.fullmatch(repository_id) is None
                or phase not in {"preparing", "prepared", "promoting", "verified"}
            ):
                raise NasDataBackupError(code="receipt_unavailable", phase="receipt")
            if phase != "verified":
                repository_ids.add(repository_id)
        return frozenset(repository_ids)

    def save(self, value: dict[str, Any]) -> None:
        _private_directory(self.directory)
        value = {
            **value,
            "schema": "echo.nas-set-restore-receipt.v1",
            "setId": self.set_id,
        }
        payload = json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        if not 1 <= len(payload) <= self.MAX_BYTES:
            raise NasDataBackupError(code="receipt_unavailable", phase="receipt")
        descriptor, temporary_name = tempfile.mkstemp(prefix=".set-receipt-", dir=self.directory)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            _sync_directory(self.directory)
        finally:
            temporary.unlink(missing_ok=True)


def _private_file(info) -> bool:
    return (
        stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not stat.S_IMODE(info.st_mode) & 0o077
    )


def _valid_receipt(value: dict[str, Any]) -> bool:
    tree = value.get("tree")
    return (
        isinstance(value.get("repositoryId"), str)
        and re.fullmatch(r"[0-9a-f]{16,64}", value["repositoryId"]) is not None
        and isinstance(value.get("snapshotId"), str)
        and re.fullmatch(r"[0-9a-f]{64}", value["snapshotId"]) is not None
        and isinstance(value.get("phase"), str)
        and value["phase"] in {"prepared", "promoted", "verified"}
        and isinstance(value.get("atomicPromotion"), bool)
        and isinstance(value.get("cleanupPending"), bool)
        and isinstance(tree, dict)
        and set(tree) == {"entries", "logicalBytes", "treeSha256"}
        and all(type(tree[key]) is int and tree[key] >= 0 for key in ("entries", "logicalBytes"))
        and isinstance(tree["treeSha256"], str)
        and re.fullmatch(r"[0-9a-f]{64}", tree["treeSha256"]) is not None
    )


def _sync_directory(directory: Path) -> None:
    directory_fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
