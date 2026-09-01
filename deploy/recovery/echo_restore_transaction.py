#!/usr/bin/env python3
"""Crash-recoverable promotion of an Echo OS staged user restore.

The restored home tree lives on echo-home while native Agent state lives on
echo-var.  A single filesystem rename therefore cannot promote both trees.
This module uses same-filesystem renames plus a root-only journal on echo-var.
Every intermediate topology is resumable, and normal boot accepts only no
transaction or a completely promoted trial transaction.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import errno
import hashlib
import json
import os
from pathlib import Path
import posixpath
import re
import shutil
import stat
import subprocess
import sys
from typing import Any, Callable, Mapping, Sequence


SCHEMA = 1
BACKUP_SCHEMAS = frozenset({1, 2})
USER_UID = 1000
USER_GID = 1000
RESTORE_DIRECTORY = ".echo-restore-staging"
BACKUP_STATE_RELATIVE = Path("lib/echo-os/user-backup-state.json")
JOURNAL_RELATIVE = Path("lib/echo-os/restore-transaction.json")
CP = "/usr/bin/cp"
MAX_JSON_BYTES = 1024 * 1024
MAX_TREE_ENTRIES = 2_000_000
MAX_TREE_DEPTH = 256
MAX_PATH_BYTES = 4096
MAX_XATTRS = 256
MAX_XATTR_BYTES = 1024 * 1024
SNAPSHOT_PATTERN = re.compile(r"^[0-9a-f]{64}$")
REPOSITORY_PATTERN = re.compile(r"^[0-9a-f]{16,64}$")
STAGING_PATTERN = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$")
TRANSACTION_PATTERN = re.compile(r"^[0-9a-f]{24}$")
PROMOTION_PHASES = frozenset(
    {
        "prepared",
        "home-retired",
        "home-installed",
        "staging-transferred",
        "agent-retired",
        "promoted",
    }
)
ROLLBACK_PHASES = frozenset(
    {
        "rollback-agent-saved",
        "rollback-agent-restored",
        "rollback-staging-returned",
        "rollback-home-saved",
        "rollback-home-restored",
        "rollback-trial-preserved",
    }
)
COMMIT_PHASES = frozenset(
    {
        "commit-authorized",
        "commit-home-removed",
        "commit-old-removed",
    }
)
ALL_PHASES = PROMOTION_PHASES | ROLLBACK_PHASES | COMMIT_PHASES


class RestoreTransactionError(RuntimeError):
    pass


@dataclass(frozen=True)
class RestoreRoots:
    home_root: Path
    var_root: Path
    user_uid: int = USER_UID
    user_gid: int = USER_GID
    state_owner_uid: int = 0

    @property
    def active_home(self) -> Path:
        return self.home_root / "echo"

    @property
    def active_agent(self) -> Path:
        return self.var_root / "lib" / "echo-agent"

    @property
    def backup_state(self) -> Path:
        return self.var_root / BACKUP_STATE_RELATIVE

    @property
    def journal(self) -> Path:
        return self.var_root / JOURNAL_RELATIVE

    def rollback_home_container(self, transaction_id: str) -> Path:
        return self.home_root / f".echo-restore-rollback-home-{transaction_id}"

    def rollback_home(self, transaction_id: str) -> Path:
        return self.rollback_home_container(transaction_id) / "echo"

    def rollback_agent_container(self, transaction_id: str) -> Path:
        return self.var_root / "lib" / f".echo-restore-rollback-agent-{transaction_id}"

    def rollback_agent(self, transaction_id: str) -> Path:
        return self.rollback_agent_container(transaction_id) / "echo-agent"

    def new_agent_container(self, transaction_id: str) -> Path:
        return self.var_root / "lib" / f".echo-restore-new-agent-{transaction_id}"

    def new_agent(self, transaction_id: str) -> Path:
        return self.new_agent_container(transaction_id) / "echo-agent"

    def rejected_agent_container(self, transaction_id: str) -> Path:
        return self.var_root / "lib" / f".echo-restore-rejected-agent-{transaction_id}"

    def rejected_agent(self, transaction_id: str) -> Path:
        return self.rejected_agent_container(transaction_id) / "echo-agent"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_field(digest: Any, label: bytes, value: bytes) -> None:
    digest.update(len(label).to_bytes(4, "big"))
    digest.update(label)
    digest.update(len(value).to_bytes(8, "big"))
    digest.update(value)


def _safe_xattrs(path: Path, *, symlink: bool) -> list[tuple[bytes, bytes]]:
    if symlink:
        return []
    if not hasattr(os, "listxattr") or not hasattr(os, "getxattr"):
        if sys.platform == "linux":
            raise RestoreTransactionError("Linux extended-attribute verification is unavailable")
        # The production Recovery runtime is Linux and fails closed above.
        # This branch permits source-level transaction tests on macOS Python,
        # whose os module may omit the Linux xattr API entirely.
        return []
    try:
        names = os.listxattr(path, follow_symlinks=False)
    except OSError as error:
        if error.errno in {errno.ENOTSUP, errno.EOPNOTSUPP}:
            return []
        raise RestoreTransactionError(f"cannot inspect extended attributes: {path}") from error
    if len(names) > MAX_XATTRS:
        raise RestoreTransactionError("restored entry has too many extended attributes")
    result: list[tuple[bytes, bytes]] = []
    for name in sorted(names, key=os.fsencode):
        try:
            value = os.getxattr(path, name, follow_symlinks=False)
        except OSError as error:
            raise RestoreTransactionError(
                f"cannot read restored extended attribute: {path}"
            ) from error
        if len(value) > MAX_XATTR_BYTES:
            raise RestoreTransactionError("restored extended attribute exceeds the safety bound")
        result.append((os.fsencode(name), value))
    return result


def _validate_link(root: Path, item: Path, relative: Path) -> bytes:
    try:
        target = os.readlink(item)
    except OSError as error:
        raise RestoreTransactionError("cannot inspect restored symlink") from error
    if os.path.isabs(target):
        raise RestoreTransactionError("restored tree contains an absolute symlink")
    lexical = posixpath.normpath(posixpath.join(relative.parent.as_posix(), target))
    if lexical == ".." or lexical.startswith("../"):
        raise RestoreTransactionError("restored tree contains an escaping symlink")
    try:
        resolved = (item.parent / target).resolve(strict=False)
        resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError) as error:
        raise RestoreTransactionError("restored tree contains an escaping symlink") from error
    return os.fsencode(target)


def tree_digest(
    root: Path,
    *,
    expected_uid: int,
    expected_gid: int | None,
    exclude_root_names: frozenset[str] = frozenset(),
) -> str:
    """Validate and hash names, bytes, ownership, modes, links, xattrs and sparsity."""

    try:
        root_metadata = root.lstat()
    except OSError as error:
        raise RestoreTransactionError(f"required restore tree is unavailable: {root}") from error
    if stat.S_ISLNK(root_metadata.st_mode) or not stat.S_ISDIR(root_metadata.st_mode):
        raise RestoreTransactionError(f"restore tree is not a real directory: {root}")
    try:
        resolved_root = root.resolve(strict=True)
    except OSError as error:
        raise RestoreTransactionError(f"restore tree cannot be resolved: {root}") from error

    digest = hashlib.sha256()
    entries = 0
    hardlinks: dict[tuple[int, int], tuple[int, int]] = {}

    def visit(item: Path, relative: Path, depth: int) -> None:
        nonlocal entries
        entries += 1
        if entries > MAX_TREE_ENTRIES:
            raise RestoreTransactionError("restored tree exceeds the entry-count safety bound")
        if depth > MAX_TREE_DEPTH:
            raise RestoreTransactionError("restored tree exceeds the depth safety bound")
        raw_relative = b"." if relative == Path(".") else os.fsencode(relative.as_posix())
        if len(raw_relative) > MAX_PATH_BYTES:
            raise RestoreTransactionError("restored tree contains an overlong path")
        metadata = item.lstat()
        if metadata.st_uid != expected_uid or (
            expected_gid is not None and metadata.st_gid != expected_gid
        ):
            raise RestoreTransactionError("restored tree contains foreign ownership")
        mode = stat.S_IMODE(metadata.st_mode)
        _hash_field(digest, b"path", raw_relative)
        _hash_field(digest, b"mode", f"{mode:o}".encode("ascii"))
        _hash_field(digest, b"uid", str(metadata.st_uid).encode("ascii"))
        _hash_field(digest, b"gid", str(metadata.st_gid).encode("ascii"))

        if stat.S_ISDIR(metadata.st_mode):
            _hash_field(digest, b"type", b"directory")
            for name, value in _safe_xattrs(item, symlink=False):
                _hash_field(digest, b"xattr-name", name)
                _hash_field(digest, b"xattr-value", value)
            try:
                children = sorted(item.iterdir(), key=lambda child: os.fsencode(child.name))
            except OSError as error:
                raise RestoreTransactionError(f"cannot enumerate restore tree: {item}") from error
            for child in children:
                if relative == Path(".") and child.name in exclude_root_names:
                    continue
                child_relative = Path(child.name) if relative == Path(".") else relative / child.name
                visit(child, child_relative, depth + 1)
            return

        if stat.S_ISLNK(metadata.st_mode):
            _hash_field(digest, b"type", b"symlink")
            _hash_field(digest, b"target", _validate_link(resolved_root, item, relative))
            return

        if not stat.S_ISREG(metadata.st_mode):
            raise RestoreTransactionError("restored tree contains a special file")
        hardlink_key = (metadata.st_dev, metadata.st_ino)
        expected_links, observed_links = hardlinks.get(
            hardlink_key, (metadata.st_nlink, 0)
        )
        if expected_links != metadata.st_nlink:
            raise RestoreTransactionError("restored hard-link metadata changed")
        hardlinks[hardlink_key] = (expected_links, observed_links + 1)
        _hash_field(digest, b"type", b"regular")
        _hash_field(digest, b"size", str(metadata.st_size).encode("ascii"))
        _hash_field(digest, b"links", str(metadata.st_nlink).encode("ascii"))
        sparse = metadata.st_size > 0 and metadata.st_blocks * 512 < metadata.st_size
        _hash_field(digest, b"sparse", b"yes" if sparse else b"no")
        for name, value in _safe_xattrs(item, symlink=False):
            _hash_field(digest, b"xattr-name", name)
            _hash_field(digest, b"xattr-value", value)

        flags = os.O_RDONLY | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(item, flags)
        except OSError as error:
            raise RestoreTransactionError(f"cannot read restored file: {item}") from error
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_dev != metadata.st_dev
                or opened.st_ino != metadata.st_ino
                or opened.st_size != metadata.st_size
            ):
                raise RestoreTransactionError("restored file changed during verification")
            content = hashlib.sha256()
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                content.update(chunk)
            verified = os.fstat(descriptor)
            if (
                verified.st_dev != metadata.st_dev
                or verified.st_ino != metadata.st_ino
                or verified.st_size != metadata.st_size
                or verified.st_mtime_ns != metadata.st_mtime_ns
                or verified.st_ctime_ns != metadata.st_ctime_ns
            ):
                raise RestoreTransactionError("restored file changed during verification")
            _hash_field(digest, b"content-sha256", content.hexdigest().encode("ascii"))
        finally:
            os.close(descriptor)

    visit(root, Path("."), 0)
    if any(expected != observed for expected, observed in hardlinks.values()):
        raise RestoreTransactionError("restored tree contains a hard link outside its root")
    return digest.hexdigest()


def read_private_json(path: Path, owner_uid: int) -> dict[str, Any]:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise RestoreTransactionError(f"private state is unavailable: {path}") from error
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != owner_uid
            or stat.S_IMODE(metadata.st_mode) & 0o077
            or metadata.st_size > MAX_JSON_BYTES
        ):
            raise RestoreTransactionError(f"private state is unsafe: {path}")
        chunks: list[bytes] = []
        remaining = MAX_JSON_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        verified = os.fstat(descriptor)
        if (
            verified.st_dev != metadata.st_dev
            or verified.st_ino != metadata.st_ino
            or verified.st_size != metadata.st_size
            or verified.st_mtime_ns != metadata.st_mtime_ns
            or verified.st_ctime_ns != metadata.st_ctime_ns
        ):
            raise RestoreTransactionError("private state changed while it was being read")
    finally:
        os.close(descriptor)
    if len(raw) > MAX_JSON_BYTES or b"\0" in raw:
        raise RestoreTransactionError("private state exceeds its safety bound")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise RestoreTransactionError("private state is malformed") from error
    if not isinstance(payload, dict):
        raise RestoreTransactionError("private state must be a JSON object")
    return payload


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_private_json(path: Path, payload: Mapping[str, Any], owner_uid: int) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent_metadata = path.parent.lstat()
    if (
        stat.S_ISLNK(parent_metadata.st_mode)
        or not stat.S_ISDIR(parent_metadata.st_mode)
        or parent_metadata.st_uid != owner_uid
        or stat.S_IMODE(parent_metadata.st_mode) & 0o077
    ):
        raise RestoreTransactionError("transaction state directory is unsafe")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, 0o600)
    try:
        if owner_uid != os.geteuid():
            os.fchown(descriptor, owner_uid, -1)
        encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
        offset = 0
        while offset < len(encoded):
            written = os.write(descriptor, encoded[offset:])
            if written <= 0:
                raise RestoreTransactionError("cannot write private transaction state")
            offset += written
        os.fsync(descriptor)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    _fsync_directory(path.parent)


def _safe_staging_name(value: object) -> str | None:
    return value if isinstance(value, str) and STAGING_PATTERN.fullmatch(value) else None


def load_staged_restore(roots: RestoreRoots) -> tuple[dict[str, Any], Path, Path, Path]:
    state = read_private_json(roots.backup_state, roots.state_owner_uid)
    state_schema = state.get("schema")
    if (
        not isinstance(state_schema, int)
        or isinstance(state_schema, bool)
        or state_schema not in BACKUP_SCHEMAS
    ):
        raise RestoreTransactionError("backup state schema is unsupported")
    if state.get("action") != "restore-staged" or state.get("verified_full_read") is not True:
        raise RestoreTransactionError("no fully verified staged restore is selected")
    repository_id = state.get("repository_id")
    snapshot_id = state.get("snapshot_id")
    if not isinstance(repository_id, str) or REPOSITORY_PATTERN.fullmatch(repository_id) is None:
        raise RestoreTransactionError("backup repository identity is invalid")
    if not isinstance(snapshot_id, str) or SNAPSHOT_PATTERN.fullmatch(snapshot_id) is None:
        raise RestoreTransactionError("backup snapshot identity is invalid")

    restore_root = roots.active_home / RESTORE_DIRECTORY
    restore_metadata = restore_root.lstat()
    if (
        stat.S_ISLNK(restore_metadata.st_mode)
        or not stat.S_ISDIR(restore_metadata.st_mode)
        or restore_metadata.st_uid != roots.user_uid
        or stat.S_IMODE(restore_metadata.st_mode) != 0o700
    ):
        raise RestoreTransactionError("restore staging root is unsafe")
    staging_name = _safe_staging_name(state.get("staging_name"))
    if staging_name is None:
        candidates = [
            item.name
            for item in restore_root.iterdir()
            if item.name.endswith(f"-{snapshot_id[:12]}")
            and _safe_staging_name(item.name) is not None
            and item.is_dir()
            and not item.is_symlink()
        ]
        if len(candidates) != 1:
            raise RestoreTransactionError("staged restore directory is missing or ambiguous")
        staging_name = candidates[0]
    stage = restore_root / staging_name
    stage_metadata = stage.lstat()
    if (
        stat.S_ISLNK(stage_metadata.st_mode)
        or not stat.S_ISDIR(stage_metadata.st_mode)
        or stage_metadata.st_uid != roots.user_uid
        or stat.S_IMODE(stage_metadata.st_mode) != 0o700
    ):
        raise RestoreTransactionError("selected staged restore is unsafe")
    expected_top = {stage / "home", stage / "var"}
    if set(stage.iterdir()) != expected_top:
        raise RestoreTransactionError("staged restore contains an unexpected top-level path")
    staged_home = stage / "home" / "echo"
    staged_agent = stage / "var" / "lib" / "echo-agent"
    reserved_home_path = staged_home / RESTORE_DIRECTORY
    if reserved_home_path.exists() or reserved_home_path.is_symlink():
        raise RestoreTransactionError("staged Home contains the reserved restore directory")
    return state, stage, staged_home, staged_agent


def _transaction_id(fields: Mapping[str, str]) -> str:
    canonical = json.dumps(fields, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()[:24]


def build_plan(roots: RestoreRoots) -> dict[str, Any]:
    if roots.journal.exists() or roots.journal.is_symlink():
        raise RestoreTransactionError("a restore transaction already exists")
    state, stage, staged_home, staged_agent = load_staged_restore(roots)
    digests = {
        "old_home": tree_digest(
            roots.active_home,
            expected_uid=roots.user_uid,
            expected_gid=None,
            exclude_root_names=frozenset({RESTORE_DIRECTORY}),
        ),
        "old_agent": tree_digest(
            roots.active_agent,
            expected_uid=roots.user_uid,
            expected_gid=None,
        ),
        "new_home": tree_digest(
            staged_home,
            expected_uid=roots.user_uid,
            expected_gid=None,
        ),
        "new_agent": tree_digest(
            staged_agent,
            expected_uid=roots.user_uid,
            expected_gid=None,
        ),
    }
    identity_fields = {
        "repository_id": str(state["repository_id"]),
        "snapshot_id": str(state["snapshot_id"]),
        "staging_name": stage.name,
        **digests,
    }
    transaction_id = _transaction_id(identity_fields)
    for reserved in (
        roots.rollback_home_container(transaction_id),
        roots.rollback_agent_container(transaction_id),
        roots.rejected_agent_container(transaction_id),
    ):
        if reserved.exists() or reserved.is_symlink():
            raise RestoreTransactionError("a reserved restore transaction path already exists")
    return {
        "schema": SCHEMA,
        "transaction_id": transaction_id,
        **identity_fields,
        "phase": "planned",
        "updated_utc": utc_now(),
    }


def validate_journal(payload: dict[str, Any]) -> dict[str, Any]:
    schema = payload.get("schema")
    if not isinstance(schema, int) or isinstance(schema, bool) or schema != SCHEMA:
        raise RestoreTransactionError("restore transaction schema is unsupported")
    transaction_id = payload.get("transaction_id")
    if not isinstance(transaction_id, str) or TRANSACTION_PATTERN.fullmatch(transaction_id) is None:
        raise RestoreTransactionError("restore transaction identity is invalid")
    phase = payload.get("phase")
    if not isinstance(phase, str) or phase not in ALL_PHASES:
        raise RestoreTransactionError("restore transaction phase is invalid")
    required: dict[str, re.Pattern[str]] = {
        "repository_id": REPOSITORY_PATTERN,
        "snapshot_id": SNAPSHOT_PATTERN,
        "staging_name": STAGING_PATTERN,
        "old_home": re.compile(r"^[0-9a-f]{64}$"),
        "old_agent": re.compile(r"^[0-9a-f]{64}$"),
        "new_home": re.compile(r"^[0-9a-f]{64}$"),
        "new_agent": re.compile(r"^[0-9a-f]{64}$"),
    }
    fields: dict[str, str] = {}
    for name, pattern in required.items():
        value = payload.get(name)
        if not isinstance(value, str) or pattern.fullmatch(value) is None:
            raise RestoreTransactionError(f"restore transaction field is invalid: {name}")
        fields[name] = value
    if _transaction_id(fields) != transaction_id:
        raise RestoreTransactionError("restore transaction identity does not match its contents")
    return payload


def load_journal(roots: RestoreRoots) -> dict[str, Any] | None:
    if not roots.journal.exists() and not roots.journal.is_symlink():
        return None
    return validate_journal(read_private_json(roots.journal, roots.state_owner_uid))


def _token(action: str, transaction_id: str) -> str:
    return f"{action}-ECHO-RESTORE-{transaction_id}"


def promotion_token(payload: Mapping[str, Any]) -> str:
    return _token("PROMOTE", str(payload["transaction_id"]))


def rollback_token(payload: Mapping[str, Any]) -> str:
    return _token("ROLLBACK", str(payload["transaction_id"]))


def commit_token(payload: Mapping[str, Any]) -> str:
    return _token("COMMIT", str(payload["transaction_id"]))


def default_copy_tree(source: Path, destination: Path) -> None:
    result = subprocess.run(
        [
            CP,
            "--archive",
            "--reflink=auto",
            "--sparse=always",
            "--",
            str(source),
            str(destination),
        ],
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
    )
    if result.returncode != 0:
        raise RestoreTransactionError(
            f"cannot copy staged Agent state into echo-var (status {result.returncode})"
        )


def validate_storage_layout(roots: RestoreRoots) -> None:
    paths = (
        roots.home_root,
        roots.var_root,
        roots.var_root / "lib",
        roots.backup_state.parent,
    )
    for path in paths:
        try:
            metadata = path.lstat()
        except OSError as error:
            raise RestoreTransactionError(
                f"restore storage layout is unavailable: {path}"
            ) from error
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != roots.state_owner_uid
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            raise RestoreTransactionError(f"restore storage layout is unsafe: {path}")


class RestoreTransaction:
    def __init__(
        self,
        roots: RestoreRoots,
        *,
        copier: Callable[[Path, Path], None] = default_copy_tree,
        hook: Callable[[str], None] | None = None,
        syncer: Callable[[], None] = os.sync,
    ) -> None:
        validate_storage_layout(roots)
        self.roots = roots
        self.copier = copier
        self.hook = hook or (lambda _event: None)
        self.syncer = syncer

    def _checkpoint(self, journal: dict[str, Any], phase: str) -> dict[str, Any]:
        updated = {**journal, "phase": phase, "updated_utc": utc_now()}
        atomic_private_json(self.roots.journal, updated, self.roots.state_owner_uid)
        self.hook(f"checkpoint:{phase}")
        return updated

    def _rename(self, source: Path, destination: Path, event: str) -> None:
        if source.exists() and not source.is_symlink() and not destination.exists() and not destination.is_symlink():
            os.rename(source, destination)
            self.syncer()
            self.hook(event)
            return
        if destination.exists() and not destination.is_symlink() and not source.exists() and not source.is_symlink():
            return
        raise RestoreTransactionError(f"restore transaction topology is inconsistent at {event}")

    def _matches(
        self,
        path: Path,
        expected: str,
        *,
        exclude_restore: bool = False,
    ) -> bool:
        if not path.exists() or path.is_symlink():
            return False
        return tree_digest(
            path,
            expected_uid=self.roots.user_uid,
            expected_gid=None,
            exclude_root_names=(
                frozenset({RESTORE_DIRECTORY}) if exclude_restore else frozenset()
            ),
        ) == expected

    def _paths(self, journal: Mapping[str, Any]) -> dict[str, Path]:
        transaction_id = str(journal["transaction_id"])
        rollback_home = self.roots.rollback_home(transaction_id)
        return {
            "active_home": self.roots.active_home,
            "active_agent": self.roots.active_agent,
            "rollback_home_container": self.roots.rollback_home_container(transaction_id),
            "rollback_home": rollback_home,
            "rollback_agent_container": self.roots.rollback_agent_container(transaction_id),
            "rollback_agent": self.roots.rollback_agent(transaction_id),
            "new_agent_container": self.roots.new_agent_container(transaction_id),
            "new_agent": self.roots.new_agent(transaction_id),
            "rejected_agent_container": self.roots.rejected_agent_container(transaction_id),
            "rejected_agent": self.roots.rejected_agent(transaction_id),
            "old_restore_root": rollback_home / RESTORE_DIRECTORY,
            "active_restore_root": self.roots.active_home / RESTORE_DIRECTORY,
        }

    def _private_container(self, path: Path, event: str) -> None:
        if not path.exists() and not path.is_symlink():
            path.mkdir(mode=0o700)
            if self.roots.state_owner_uid != os.geteuid():
                os.chown(path, self.roots.state_owner_uid, -1)
            os.chmod(path, 0o700)
            self.syncer()
            self.hook(event)
        try:
            metadata = path.lstat()
        except OSError as error:
            raise RestoreTransactionError("restore transaction container is unavailable") from error
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != self.roots.state_owner_uid
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise RestoreTransactionError("restore transaction container is unsafe")

    def _remove_empty_container(self, path: Path, event: str) -> None:
        if path.exists() and not path.is_symlink():
            try:
                path.rmdir()
            except OSError as error:
                raise RestoreTransactionError("restore transaction container is not empty") from error
            self.syncer()
            self.hook(event)
        elif path.is_symlink():
            raise RestoreTransactionError("restore transaction container became a symlink")

    def plan(self) -> dict[str, Any]:
        journal = load_journal(self.roots)
        return journal if journal is not None else build_plan(self.roots)

    def promote(self, confirmation: str) -> dict[str, Any]:
        journal = load_journal(self.roots)
        if journal is None:
            journal = build_plan(self.roots)
            if confirmation != promotion_token(journal):
                raise RestoreTransactionError("restore promotion confirmation does not match the plan")
            paths = self._paths(journal)
            _, _, _, staged_agent = load_staged_restore(self.roots)
            self._private_container(
                paths["new_agent_container"], "agent-copy-container:after-create"
            )
            if paths["new_agent"].exists() or paths["new_agent"].is_symlink():
                if not self._matches(paths["new_agent"], str(journal["new_agent"])):
                    raise RestoreTransactionError("pre-journal Agent copy is not the planned tree")
            else:
                self.copier(staged_agent, paths["new_agent"])
                self.syncer()
                if not self._matches(paths["new_agent"], str(journal["new_agent"])):
                    raise RestoreTransactionError("copied Agent state differs from the staged tree")
                self.hook("agent-copy:after-copy")
            journal = self._checkpoint(journal, "prepared")
        elif confirmation != promotion_token(journal):
            raise RestoreTransactionError("restore promotion confirmation does not match the journal")
        if str(journal["phase"]) not in PROMOTION_PHASES:
            raise RestoreTransactionError("restore transaction is already rolling back or committing")
        return self._resume_promotion(journal)

    def _resume_promotion(self, journal: dict[str, Any]) -> dict[str, Any]:
        paths = self._paths(journal)
        while True:
            phase = str(journal["phase"])
            if phase == "promoted":
                return journal
            if phase == "prepared":
                if paths["active_home"].exists():
                    if not self._matches(
                        paths["active_home"], str(journal["old_home"]), exclude_restore=True
                    ):
                        raise RestoreTransactionError("active Home changed after restore planning")
                self._private_container(
                    paths["rollback_home_container"], "home-retire-container:after-create"
                )
                self._rename(paths["active_home"], paths["rollback_home"], "home-retire:after-rename")
                if not self._matches(
                    paths["rollback_home"], str(journal["old_home"]), exclude_restore=True
                ):
                    raise RestoreTransactionError("retired Home differs from the planned old tree")
                journal = self._checkpoint(journal, "home-retired")
                continue
            if phase == "home-retired":
                staged_home = (
                    paths["old_restore_root"]
                    / str(journal["staging_name"])
                    / "home"
                    / "echo"
                )
                self._rename(staged_home, paths["active_home"], "home-install:after-rename")
                if not self._matches(paths["active_home"], str(journal["new_home"])):
                    raise RestoreTransactionError("installed Home differs from the staged tree")
                journal = self._checkpoint(journal, "home-installed")
                continue
            if phase == "home-installed":
                self._rename(
                    paths["old_restore_root"],
                    paths["active_restore_root"],
                    "staging-transfer:after-rename",
                )
                if not self._matches(
                    paths["active_home"], str(journal["new_home"]), exclude_restore=True
                ):
                    raise RestoreTransactionError("new Home changed while retaining restore staging")
                journal = self._checkpoint(journal, "staging-transferred")
                continue
            if phase == "staging-transferred":
                if paths["active_agent"].exists() and not self._matches(
                    paths["active_agent"], str(journal["old_agent"])
                ):
                    raise RestoreTransactionError("active Agent state changed after restore planning")
                self._private_container(
                    paths["rollback_agent_container"], "agent-retire-container:after-create"
                )
                self._rename(
                    paths["active_agent"], paths["rollback_agent"], "agent-retire:after-rename"
                )
                if not self._matches(paths["rollback_agent"], str(journal["old_agent"])):
                    raise RestoreTransactionError("retired Agent state differs from the old tree")
                journal = self._checkpoint(journal, "agent-retired")
                continue
            if phase == "agent-retired":
                self._rename(paths["new_agent"], paths["active_agent"], "agent-install:after-rename")
                if not self._matches(paths["active_agent"], str(journal["new_agent"])):
                    raise RestoreTransactionError("installed Agent state differs from the staged tree")
                journal = self._checkpoint(journal, "promoted")
                continue
            raise RestoreTransactionError("restore promotion phase is not resumable")

    def rollback(self, confirmation: str) -> dict[str, Any]:
        journal = load_journal(self.roots)
        if journal is None:
            raise RestoreTransactionError("no restore transaction exists")
        if confirmation != rollback_token(journal):
            raise RestoreTransactionError("restore rollback confirmation does not match the journal")
        phase = str(journal["phase"])
        if phase in COMMIT_PHASES:
            raise RestoreTransactionError("old data deletion has started and cannot be rolled back")
        if phase in PROMOTION_PHASES:
            journal = self._resume_promotion(journal)
        paths = self._paths(journal)
        while True:
            phase = str(journal["phase"])
            if phase == "promoted":
                self._rename(
                    paths["active_agent"], paths["new_agent"], "rollback-agent-save:after-rename"
                )
                journal = self._checkpoint(journal, "rollback-agent-saved")
                continue
            if phase == "rollback-agent-saved":
                self._rename(
                    paths["rollback_agent"],
                    paths["active_agent"],
                    "rollback-agent-restore:after-rename",
                )
                journal = self._checkpoint(journal, "rollback-agent-restored")
                continue
            if phase == "rollback-agent-restored":
                self._rename(
                    paths["active_restore_root"],
                    paths["old_restore_root"],
                    "rollback-staging-return:after-rename",
                )
                journal = self._checkpoint(journal, "rollback-staging-returned")
                continue
            if phase == "rollback-staging-returned":
                staged_home = (
                    paths["old_restore_root"]
                    / str(journal["staging_name"])
                    / "home"
                    / "echo"
                )
                self._rename(
                    paths["active_home"], staged_home, "rollback-home-save:after-rename"
                )
                journal = self._checkpoint(journal, "rollback-home-saved")
                continue
            if phase == "rollback-home-saved":
                self._rename(
                    paths["rollback_home"],
                    paths["active_home"],
                    "rollback-home-restore:after-rename",
                )
                journal = self._checkpoint(journal, "rollback-home-restored")
                continue
            if phase == "rollback-home-restored":
                self._remove_empty_container(
                    paths["rollback_home_container"],
                    "rollback-home-container:after-remove",
                )
                self._rename(
                    paths["new_agent_container"],
                    paths["rejected_agent_container"],
                    "rollback-trial-preserve:after-rename",
                )
                journal = self._checkpoint(journal, "rollback-trial-preserved")
                continue
            if phase == "rollback-trial-preserved":
                self._update_backup_action(
                    journal,
                    "restore-rolled-back",
                    rejected_agent=str(paths["rejected_agent"].relative_to(self.roots.var_root)),
                )
                self.hook("rollback-state:after-update")
                self._remove_journal()
                return {**journal, "phase": "rolled-back"}
            raise RestoreTransactionError("restore rollback phase is not resumable")

    def commit(self, confirmation: str) -> dict[str, Any]:
        journal = load_journal(self.roots)
        if journal is None:
            raise RestoreTransactionError("no restore transaction exists")
        if confirmation != commit_token(journal):
            raise RestoreTransactionError("restore commit confirmation does not match the journal")
        phase = str(journal["phase"])
        if phase in ROLLBACK_PHASES:
            raise RestoreTransactionError("restore transaction is already rolling back")
        if phase in PROMOTION_PHASES and phase != "promoted":
            raise RestoreTransactionError("restore must finish promotion before it can be committed")
        paths = self._paths(journal)
        while True:
            phase = str(journal["phase"])
            if phase == "promoted":
                journal = self._checkpoint(journal, "commit-authorized")
                continue
            if phase == "commit-authorized":
                if paths["rollback_home_container"].exists() and not paths[
                    "rollback_home_container"
                ].is_symlink():
                    shutil.rmtree(paths["rollback_home_container"])
                    self.syncer()
                    self.hook("commit-home:after-remove")
                elif paths["rollback_home_container"].is_symlink():
                    raise RestoreTransactionError("rollback Home became a symlink")
                journal = self._checkpoint(journal, "commit-home-removed")
                continue
            if phase == "commit-home-removed":
                for container in (
                    paths["rollback_agent_container"],
                    paths["new_agent_container"],
                ):
                    if container.exists() and not container.is_symlink():
                        shutil.rmtree(container)
                    elif container.is_symlink():
                        raise RestoreTransactionError("rollback Agent state became a symlink")
                self.syncer()
                self.hook("commit-agent:after-remove")
                journal = self._checkpoint(journal, "commit-old-removed")
                continue
            if phase == "commit-old-removed":
                self._update_backup_action(journal, "restore-committed")
                self.hook("commit-state:after-update")
                self._remove_journal()
                return {**journal, "phase": "committed"}
            raise RestoreTransactionError("restore commit phase is not resumable")

    def _update_backup_action(
        self,
        journal: Mapping[str, Any],
        action: str,
        **extra: str,
    ) -> None:
        state = read_private_json(self.roots.backup_state, self.roots.state_owner_uid)
        state_schema = state.get("schema")
        state_action = state.get("action")
        if (
            not isinstance(state_schema, int)
            or isinstance(state_schema, bool)
            or state_schema not in BACKUP_SCHEMAS
            or state_action not in {"restore-staged", action}
            or state.get("snapshot_id") != journal.get("snapshot_id")
            or state.get("repository_id") != journal.get("repository_id")
            or state.get("staging_name") != journal.get("staging_name")
            or state.get("verified_full_read") is not True
        ):
            raise RestoreTransactionError("backup state no longer matches the restore transaction")
        if state_action == action and state.get("restore_transaction_id") != journal.get(
            "transaction_id"
        ):
            raise RestoreTransactionError("completed backup state names another transaction")
        updated = {
            **state,
            "schema": max(int(state.get("schema", 1)), 2),
            "action": action,
            "restore_transaction_id": journal["transaction_id"],
            "updated_utc": utc_now(),
            **extra,
        }
        atomic_private_json(self.roots.backup_state, updated, self.roots.state_owner_uid)

    def _remove_journal(self) -> None:
        self.roots.journal.unlink()
        _fsync_directory(self.roots.journal.parent)

    def health(self) -> str:
        journal = load_journal(self.roots)
        if journal is None:
            return "ECHO_RESTORE_TRANSACTION_READY phase=none trial=no"
        if journal.get("phase") != "promoted":
            raise RestoreTransactionError(
                f"restore transaction requires Recovery: phase={journal.get('phase')}"
            )
        paths = self._paths(journal)
        private_containers = {
            "rollback_home_container",
            "rollback_agent_container",
            "new_agent_container",
        }
        for name in (
            "active_home",
            "rollback_home_container",
            "rollback_home",
            "active_agent",
            "rollback_agent_container",
            "rollback_agent",
            "new_agent_container",
            "active_restore_root",
        ):
            path = paths[name]
            try:
                metadata = path.lstat()
            except OSError as error:
                raise RestoreTransactionError(
                    f"promoted restore is missing required path: {name}"
                ) from error
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise RestoreTransactionError(f"promoted restore path is unsafe: {name}")
            if name in private_containers and (
                metadata.st_uid != self.roots.state_owner_uid
                or stat.S_IMODE(metadata.st_mode) != 0o700
            ):
                raise RestoreTransactionError(
                    f"promoted restore private container is unsafe: {name}"
                )
        restore_metadata = paths["active_restore_root"].lstat()
        if (
            restore_metadata.st_uid != self.roots.user_uid
            or stat.S_IMODE(restore_metadata.st_mode) != 0o700
        ):
            raise RestoreTransactionError("promoted restore staging root is unsafe")
        return (
            "ECHO_RESTORE_TRANSACTION_READY phase=promoted trial=yes "
            f"transaction={journal['transaction_id']}"
        )


def validate_mounted_roots(home_root: Path, var_root: Path) -> RestoreRoots:
    if not home_root.is_absolute() or not var_root.is_absolute():
        raise RestoreTransactionError("restore roots must be absolute")
    if home_root.is_symlink() or var_root.is_symlink():
        raise RestoreTransactionError("restore roots must not be symlinks")
    try:
        home = home_root.resolve(strict=True)
        var = var_root.resolve(strict=True)
    except OSError as error:
        raise RestoreTransactionError("restore roots are unavailable") from error
    if home == var or home.stat().st_dev == var.stat().st_dev:
        raise RestoreTransactionError("echo-home and echo-var must be independent filesystems")
    for path in (home, var):
        metadata = path.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) & 0o022
            or not os.path.ismount(path)
        ):
            raise RestoreTransactionError("restore root is not a private root-owned mount point")
    return RestoreRoots(home, var)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="action", required=True)
    for action in ("plan", "status", "health"):
        command = subparsers.add_parser(action)
        command.add_argument("--home-root", type=Path, required=True)
        command.add_argument("--var-root", type=Path, required=True)
    for action in ("promote", "rollback", "commit"):
        command = subparsers.add_parser(action)
        command.add_argument("--home-root", type=Path, required=True)
        command.add_argument("--var-root", type=Path, required=True)
        command.add_argument("confirmation")
    return parser.parse_args(argv)


def print_status(transaction: RestoreTransaction, payload: Mapping[str, Any]) -> None:
    phase = str(payload["phase"])
    print(
        "ECHO_RESTORE_TRANSACTION_STATUS "
        f"transaction={payload['transaction_id']} phase={phase} "
        f"snapshot={str(payload['snapshot_id'])[:12]}"
    )
    print(f"Promote or resume: {promotion_token(payload)}")
    if phase not in COMMIT_PHASES:
        print(f"Rollback: {rollback_token(payload)}")
    if phase == "promoted" or phase in COMMIT_PHASES:
        print(f"Commit and delete old data: {commit_token(payload)}")


def main(argv: Sequence[str] | None = None) -> int:
    if os.geteuid() != 0 or os.getuid() != 0:
        print("restore transaction management requires root", file=sys.stderr)
        return 1
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        roots = validate_mounted_roots(args.home_root, args.var_root)
        transaction = RestoreTransaction(roots)
        if args.action == "health":
            print(transaction.health())
            return 0
        if args.action == "plan":
            payload = transaction.plan()
            print_status(transaction, payload)
            return 0
        if args.action == "status":
            payload = load_journal(roots)
            if payload is None:
                print("ECHO_RESTORE_TRANSACTION_STATUS phase=none")
            else:
                print_status(transaction, payload)
            return 0
        if args.action == "promote":
            payload = transaction.promote(args.confirmation)
            print(
                "ECHO_RESTORE_PROMOTED "
                f"transaction={payload['transaction_id']} phase=trial "
                f"snapshot={str(payload['snapshot_id'])[:12]} old-data=retained"
            )
            print(f"Commit after validating the normal desktop: {commit_token(payload)}")
            print(f"Rollback from Recovery: {rollback_token(payload)}")
            return 0
        if args.action == "rollback":
            payload = transaction.rollback(args.confirmation)
            print(
                "ECHO_RESTORE_ROLLED_BACK "
                f"transaction={payload['transaction_id']} old-data=active trial-agent=retained"
            )
            return 0
        if args.action == "commit":
            payload = transaction.commit(args.confirmation)
            print(
                "ECHO_RESTORE_COMMITTED "
                f"transaction={payload['transaction_id']} old-data=deleted staging=retained"
            )
            return 0
        raise RestoreTransactionError("unsupported restore transaction action")
    except (OSError, RestoreTransactionError, subprocess.SubprocessError, UnicodeError) as error:
        print(f"Echo restore transaction failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
