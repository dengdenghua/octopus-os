"""Low-level filesystem primitives for the Codex sidecar security boundary.

This module has no knowledge of App Server requests or execution-session
state.  It owns only opaque path derivation, private file creation and reads,
and marker-guarded cleanup.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import stat
from collections.abc import Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import cast

_MARKER_FILE = ".echo-codex-sidecar.json"
_MARKER_KIND = "echo-codex-sidecar/v1"


class CodexSecurityError(RuntimeError):
    """Raised when a sidecar would start outside the security contract."""


def _opaque_id(namespace: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CodexSecurityError(f"{namespace}_id must be a non-empty string")
    if len(value.encode("utf-8")) > 4096:
        raise CodexSecurityError(f"{namespace}_id is too long")
    payload = f"echo-codex-sidecar\0{namespace}\0{value}".encode()
    return hashlib.sha256(payload).hexdigest()


def _prepare_state_root(path: Path) -> Path:
    if path.parent == path:
        raise CodexSecurityError("filesystem root cannot be used as Codex sidecar state_root")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink():
        raise CodexSecurityError("Codex sidecar state_root cannot be a symlink")
    resolved = path.resolve(strict=True)
    _lock_down_directory(resolved)
    return resolved


def _validate_workspace(
    workspace: Path,
    *,
    allowed_roots: Sequence[Path],
    state_root: Path,
) -> Path:
    candidate = Path(workspace).expanduser()
    if not candidate.is_absolute():
        raise CodexSecurityError("Codex workspace must be absolute")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise CodexSecurityError(f"Codex workspace does not exist: {candidate}") from exc
    if not resolved.is_dir():
        raise CodexSecurityError(f"Codex workspace is not a directory: {resolved}")

    canonical_roots: list[Path] = []
    for root in allowed_roots:
        try:
            canonical = root.resolve(strict=True)
        except OSError as exc:
            raise CodexSecurityError(f"allowed workspace root does not exist: {root}") from exc
        if not canonical.is_dir():
            raise CodexSecurityError(f"allowed workspace root is not a directory: {canonical}")
        if canonical.parent == canonical:
            raise CodexSecurityError("filesystem root cannot be an allowed workspace root")
        canonical_roots.append(canonical)

    if not any(_is_within(resolved, root) for root in canonical_roots):
        raise CodexSecurityError(f"Codex workspace escapes the configured roots: {resolved}")
    if _is_within(resolved, state_root) or _is_within(state_root, resolved):
        raise CodexSecurityError("Codex workspace and sidecar state_root must not overlap")
    return resolved


def _is_within(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def _ensure_private_directory(path: Path, *, root: Path) -> None:
    if not _is_within(path, root):
        raise CodexSecurityError(f"refusing to create a sidecar directory outside {root}")
    relative = path.relative_to(root)
    current = root
    for part in relative.parts:
        current = current / part
        with suppress(FileExistsError):
            current.mkdir(mode=0o700)
        if current.is_symlink():
            raise CodexSecurityError(f"sidecar path component cannot be a symlink: {current}")
        _lock_down_directory(current)


def _lock_down_directory(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise CodexSecurityError(f"cannot inspect sidecar directory: {path}") from exc
    if not stat.S_ISDIR(metadata.st_mode):
        raise CodexSecurityError(f"sidecar path is not a directory: {path}")
    if os.name == "posix" and metadata.st_uid != os.geteuid():
        raise CodexSecurityError(f"sidecar directory is not owned by the service user: {path}")
    try:
        path.chmod(0o700)
    except OSError as exc:
        raise CodexSecurityError(f"cannot apply private permissions to {path}") from exc


def _atomic_write_private(path: Path, data: bytes) -> None:
    try:
        existing = path.lstat()
    except FileNotFoundError:
        existing = None
    except OSError as exc:
        raise CodexSecurityError(f"cannot inspect sidecar file: {path}") from exc
    if existing is not None and stat.S_ISLNK(existing.st_mode):
        raise CodexSecurityError(f"refusing to replace sidecar symlink: {path}")

    temporary = path.parent / f".{path.name}.{secrets.token_hex(12)}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(temporary, flags, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = -1
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            path.chmod(0o600)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    except OSError as exc:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)
        raise CodexSecurityError(f"cannot write private sidecar file: {path}") from exc


def _write_marker(marker_path: Path, details: Mapping[str, str]) -> None:
    payload = {"schema": _MARKER_KIND, **dict(details)}
    _atomic_write_private(
        marker_path,
        (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8"),
    )


def _read_owned_private_file(path: Path, *, max_bytes: int) -> bytes | None:
    try:
        before = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise CodexSecurityError(f"cannot inspect private Codex file: {path}") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise CodexSecurityError(f"private Codex file must be regular and non-symlink: {path}")
    if os.name == "posix":
        if before.st_uid != os.geteuid():
            raise CodexSecurityError(f"private Codex file is not owned by the service user: {path}")
        if stat.S_IMODE(before.st_mode) & 0o077:
            raise CodexSecurityError(f"private Codex file must use owner-only permissions: {path}")
    if before.st_size > max_bytes:
        raise CodexSecurityError(f"private Codex file exceeds {max_bytes} bytes: {path}")

    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise CodexSecurityError(f"cannot open private Codex file: {path}") from exc
    try:
        after = os.fstat(descriptor)
        if not stat.S_ISREG(after.st_mode) or (after.st_dev, after.st_ino) != (
            before.st_dev,
            before.st_ino,
        ):
            raise CodexSecurityError(f"private Codex file changed during validation: {path}")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            data = handle.read(max_bytes + 1)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(data) > max_bytes:
        raise CodexSecurityError(f"private Codex file exceeds {max_bytes} bytes: {path}")
    return data


def _remove_marked_tree(
    path: Path,
    *,
    root: Path,
    marker_path: Path,
    expected_kind: str,
) -> None:
    if root.parent == root or marker_path == root or not _is_within(marker_path, root):
        raise CodexSecurityError("refusing to use a cleanup marker outside sidecar state_root")
    expected_scope = root / ("scratch" if expected_kind == "scratch" else "realms")
    if path == expected_scope or not _is_within(path, expected_scope):
        raise CodexSecurityError(f"refusing to clean an invalid {expected_kind} path: {path}")
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        marker_path.unlink(missing_ok=True)
        return
    except OSError as exc:
        raise CodexSecurityError(f"cannot inspect sidecar cleanup path: {path}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise CodexSecurityError(f"refusing to clean unsafe sidecar path: {path}")
    resolved = path.resolve(strict=True)
    if resolved == root or not _is_within(resolved, root):
        raise CodexSecurityError(f"refusing to clean outside sidecar state_root: {resolved}")
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CodexSecurityError(f"refusing to clean unmarked sidecar tree: {resolved}") from exc
    if (
        marker.get("schema") != _MARKER_KIND
        or marker.get("kind") != expected_kind
        or marker.get("path") != str(resolved)
    ):
        raise CodexSecurityError(f"refusing to clean sidecar tree with invalid marker: {resolved}")
    shutil.rmtree(resolved)
    if not _is_within(marker_path, resolved):
        marker_path.unlink(missing_ok=True)


def _prune_empty_parents(start: Path, *, stop: Path) -> None:
    current = start
    while current != stop and _is_within(current, stop):
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def _non_null_items(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    return {str(key): item for key, item in value.items() if item is not None}


def _mapping_at(
    value: Mapping[str, object], key: str, errors: list[str]
) -> Mapping[str, object] | None:
    nested = value.get(key)
    if not isinstance(nested, Mapping):
        errors.append(f"{key} must be an object")
        return None
    return cast(Mapping[str, object], nested)


def _expect_value(
    value: Mapping[str, object],
    key: str,
    expected: object,
    errors: list[str],
    *,
    prefix: str = "",
) -> None:
    actual = value.get(key)
    if actual != expected:
        errors.append(f"{prefix}{key} must be {expected!r}, got {actual!r}")
