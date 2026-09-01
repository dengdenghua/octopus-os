"""Per-thread isolated workspace directories.

Each realtime thread can get its own scratch workspace — a cwd under
``<data_dir>/workspaces/<thread_id>`` where tool calls default to
running. Two concurrent threads can't accidentally write to the
same file, ``git checkout`` in one thread doesn't yank the floor
out from under the other, and the whole workspace can be discarded
in one ``rmtree`` when the thread is archived.

Policy knobs:

* ``allocate(thread_id)``  — idempotent. Returns the Path, creating it
  with a ``.gitignore`` marker on first touch so the user can tell
  at-a-glance which directories are runtime-allocated.
* ``discard(thread_id)``   — best-effort cleanup. Safe to call on a
  non-existent workspace. Refuses to remove anything outside the
  configured root (guards against mistyped ``root``).
* ``resolve_cwd(thread_id, explicit_cwd)`` — helper the runtime uses
  at turn start. If the caller passes an explicit ``cwd`` (power user,
  single-shot script), that wins; otherwise we allocate.

Authenticated runtimes may bind a thread id to a deeper, server-verified
tenant/actor path with ``bind_managed``.  That binding is deliberately kept on
the app-local manager so every consumer (cwd, uploads and artifact outputs)
uses the same layout for the lifetime of the runtime.
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import json
import logging
import os
import secrets
import shutil
import stat
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any

_logger = logging.getLogger(__name__)

MANAGED_WORKSPACE_MARKER = "server-v1"
MANAGED_WORKSPACE_METADATA_KEY = "_workspace_allocation"
MANAGED_WORKSPACE_DELETION_KEY = "_workspace_deletion"
MANAGED_WORKSPACE_DELETION_MARKER = "staged-v1"

# These fields are controlled exclusively by the server in authenticated mode.
# The shared policy lives below both execution and the HTTP gateway so neither
# layer needs to depend on the other to enforce the filesystem boundary.
PROTECTED_WORKSPACE_METADATA_KEYS = frozenset(
    {
        "workspace_path",
        "extra_workspaces",
        "personal_workspace_path",
        "allowed_write_paths",
        "attachment_read_roots",
        "_artifact_output_root",
        "cwd",
        MANAGED_WORKSPACE_METADATA_KEY,
        MANAGED_WORKSPACE_DELETION_KEY,
    }
)

_GITIGNORE_BODY = (
    "# Auto-created by echo runtime — per-thread isolated workspace.\n"
    "# Safe to delete when the thread is archived.\n"
    "*\n"
    "!.gitignore\n"
    "!workspace.json\n"
    "!upload/\n"
    "!output/\n"
    "!deploy/\n"
    "!skills/\n"
)

_MANIFEST_NAME = "workspace.json"
_STANDARD_DIRS = (
    "upload",
    "output",
    "output/stages",
    "output/final",
    "deploy",
    "skills",
)


def _supports_secure_dirfd() -> bool:
    """Whether this runtime can traverse directories without following links."""

    return bool(
        hasattr(os, "O_DIRECTORY")
        and hasattr(os, "O_NOFOLLOW")
        and os.open in os.supports_dir_fd
        and os.mkdir in os.supports_dir_fd
        and os.stat in os.supports_dir_fd
        and os.unlink in os.supports_dir_fd
        and os.rename in os.supports_dir_fd
        and os.link in os.supports_dir_fd
    )


def _directory_open_flags() -> int:
    flags = os.O_RDONLY | int(getattr(os, "O_DIRECTORY", 0))
    flags |= int(getattr(os, "O_NOFOLLOW", 0))
    flags |= int(getattr(os, "O_CLOEXEC", 0))
    return flags


def _file_open_flags() -> int:
    return int(getattr(os, "O_NOFOLLOW", 0)) | int(getattr(os, "O_CLOEXEC", 0))


def _validate_component(component: str) -> None:
    if (
        not component
        or component in {".", ".."}
        or os.sep in component
        or (os.altsep is not None and os.altsep in component)
    ):
        raise ValueError("workspace path contains an unsafe component")


def _write_all(fd: int, payload: bytes) -> None:
    remaining = memoryview(payload)
    while remaining:
        written = os.write(fd, remaining)
        if written <= 0:
            raise OSError("short write while persisting workspace metadata")
        remaining = remaining[written:]


def _best_effort_fsync_directory(fd: int) -> None:
    with contextlib.suppress(OSError):
        os.fsync(fd)


def _temporary_entry_name(name: str) -> str:
    return f".{name}.tmp-{os.getpid()}-{secrets.token_hex(8)}"


def strip_client_workspace_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Return client metadata without server-owned filesystem scope fields."""
    return {
        key: value
        for key, value in metadata.items()
        if key not in PROTECTED_WORKSPACE_METADATA_KEYS
    }


def _scope_segment(value: str) -> str:
    """Produce an opaque, traversal-safe stable path segment."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def managed_workspace_path(
    workspace_root: str | Path,
    *,
    tenant_id: str,
    actor_id: str,
    thread_id: str,
) -> Path:
    """Return the only valid server path for an authenticated thread."""
    root = Path(workspace_root).expanduser().resolve(strict=False)
    thread_segment = Path(thread_id)
    if (
        not thread_id
        or thread_segment.is_absolute()
        or len(thread_segment.parts) != 1
        or thread_segment.name != thread_id
    ):
        raise ValueError("thread id is not a safe workspace path segment")
    candidate = root / _scope_segment(tenant_id) / _scope_segment(actor_id) / thread_id
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("managed workspace path escapes its root") from exc
    resolved = candidate.resolve(strict=False)
    if resolved != candidate:
        # Reject symlinks even when they redirect to another directory still
        # under workspace_root; otherwise one tenant could alias another.
        raise ValueError("managed workspace path contains a symlink")
    return candidate


def managed_workspace_metadata(
    workspace_root: str | Path,
    *,
    tenant_id: str,
    actor_id: str,
    thread_id: str,
) -> dict[str, str]:
    """Build the immutable metadata written after server allocation."""
    path = managed_workspace_path(
        workspace_root,
        tenant_id=tenant_id,
        actor_id=actor_id,
        thread_id=thread_id,
    )
    return {
        "workspace_path": str(path),
        MANAGED_WORKSPACE_METADATA_KEY: MANAGED_WORKSPACE_MARKER,
    }


def verified_managed_workspace(
    workspace_root: str | Path | None,
    *,
    thread_id: str,
    metadata: dict[str, Any],
    allow_deleting: bool = False,
) -> Path | None:
    """Verify and return a server-managed workspace, otherwise ``None``.

    Verification is structural rather than trusting the marker alone: the
    stored path must exactly equal the deterministic path derived from the
    thread's server-owned actor and tenant metadata.  Ordinary consumers are
    denied while deletion is staged; only the deletion transaction opts into
    ``allow_deleting`` to finish or retry cleanup.
    """
    if workspace_root is None:
        return None
    if (
        metadata.get(MANAGED_WORKSPACE_DELETION_KEY) == MANAGED_WORKSPACE_DELETION_MARKER
        and not allow_deleting
    ):
        # Once deletion is staged, ordinary filesystem consumers must not
        # recreate the path while the cleanup transaction is in progress.
        return None
    if metadata.get(MANAGED_WORKSPACE_METADATA_KEY) != MANAGED_WORKSPACE_MARKER:
        return None
    actor_id = metadata.get("owner_actor_id")
    tenant_id = metadata.get("tenant_id")
    stored_path = metadata.get("workspace_path")
    if not isinstance(actor_id, str) or not actor_id:
        return None
    if not isinstance(tenant_id, str) or not tenant_id:
        return None
    if not isinstance(stored_path, str) or not stored_path:
        return None
    try:
        expected = managed_workspace_path(
            workspace_root,
            tenant_id=tenant_id,
            actor_id=actor_id,
            thread_id=thread_id,
        )
        raw_stored = Path(stored_path).expanduser()
        if not raw_stored.is_absolute():
            return None
        # Normalize dot segments without following symlinks, then require the
        # actual resolution to remain identical as a second symlink defense.
        stored = Path(os.path.abspath(raw_stored))
    except (OSError, RuntimeError, ValueError):
        return None
    if stored != expected or stored.resolve(strict=False) != expected:
        return None
    return stored


@dataclass(frozen=True)
class WorkspaceLayout:
    root: Path
    upload: Path
    output: Path
    stages: Path
    final: Path
    deploy: Path
    skills: Path
    manifest: Path

    def as_dict(self) -> dict[str, str]:
        return {
            "root": str(self.root),
            "upload": str(self.upload),
            "output": str(self.output),
            "stages": str(self.stages),
            "final": str(self.final),
            "deploy": str(self.deploy),
            "skills": str(self.skills),
            "manifest": str(self.manifest),
        }


@dataclass(frozen=True)
class WorkspaceManager:
    root: Path
    _managed_paths: dict[str, Path] = field(
        default_factory=dict,
        init=False,
        repr=False,
        compare=False,
    )
    _managed_lock: RLock = field(
        default_factory=RLock,
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        # Resolve once so ``_contains`` comparisons are robust against
        # symlink games and relative paths.
        object.__setattr__(self, "root", Path(self.root).expanduser().resolve())

    def path_for(self, thread_id: str) -> Path:
        """Return a thread workspace path without creating directories."""
        with self._managed_lock:
            managed = self._managed_paths.get(thread_id)
        if managed is not None:
            return managed
        safe = _safe_slug(thread_id)
        return self.root / safe

    def bind_managed(self, thread_id: str, workspace_path: str | Path) -> WorkspaceLayout:
        """Bind ``thread_id`` to a server-verified path below ``root``.

        Realtime execution has several consumers of the workspace manager. A
        one-off cwd override would leave uploads and artifact outputs on the
        legacy ``root/thread`` path, so authenticated allocation is registered
        once and all subsequent ``layout`` calls resolve identically.

        The caller must already have verified actor/tenant ownership. This
        method supplies the final filesystem boundary: it rejects relative
        paths, paths outside the manager root, symlinks and attempts to rebind
        a live thread to a different directory.
        """
        raw = Path(workspace_path).expanduser()
        if not raw.is_absolute():
            raise ValueError("managed workspace path must be absolute")
        normalized = Path(os.path.abspath(raw))
        resolved = raw.resolve(strict=False)
        if resolved != normalized:
            raise ValueError("managed workspace path contains a symlink")
        if not self._contains(normalized):
            raise ValueError("managed workspace path is outside the workspace root")
        with self._managed_lock:
            existing = self._managed_paths.get(thread_id)
            if existing is not None and existing != normalized:
                raise ValueError("thread already has a different managed workspace")
            # Keep the rebind decision and the filesystem allocation in one
            # critical section. A failed secure allocation must never leave a
            # poisoned in-memory binding behind.
            self._ensure_layout(normalized, thread_id)
            self._managed_paths[thread_id] = normalized
        return self._layout_for(normalized)

    def allocate(self, thread_id: str) -> Path:
        """Create (if needed) and return the workspace dir for a thread."""
        path = self.path_for(thread_id)
        # A freshly opened thread triggers several workspace-backed endpoints
        # in parallel (outputs, uploads, project binding). Serialise layout
        # publication within this runtime so they cannot race on the manifest
        # or its standard directories. RLock keeps bind_managed re-entrant.
        with self._managed_lock:
            self._ensure_layout(path, thread_id)
        return path

    def layout(self, thread_id: str) -> WorkspaceLayout:
        """Return the standard workspace layout, creating it if necessary."""
        root = self.allocate(thread_id)
        return self._layout_for(root)

    @staticmethod
    def _layout_for(root: Path) -> WorkspaceLayout:
        return WorkspaceLayout(
            root=root,
            upload=root / "upload",
            output=root / "output",
            stages=root / "output" / "stages",
            final=root / "output" / "final",
            deploy=root / "deploy",
            skills=root / "skills",
            manifest=root / _MANIFEST_NAME,
        )

    def manifest(self, thread_id: str) -> dict[str, Any]:
        """Return the workspace manifest for a thread."""
        layout = self.layout(thread_id)
        try:
            data = json.loads(self._read_manifest_text(layout.root))
        except (OSError, json.JSONDecodeError):
            self._write_manifest(layout.root, thread_id)
            data = json.loads(self._read_manifest_text(layout.root))
        if isinstance(data, dict):
            return data
        self._write_manifest(layout.root, thread_id)
        return json.loads(self._read_manifest_text(layout.root))

    def discard(self, thread_id: str) -> bool:
        """Remove the thread's workspace. Returns True iff something was deleted.

        Refuses to touch anything that resolves outside ``root`` — a
        last line of defence against a slug resolving to ``..`` due to
        a bug or malicious input.
        """
        path = self.path_for(thread_id)
        try:
            resolved = path.resolve()
        except OSError:
            return False
        if not self._contains(resolved):
            _logger.warning(
                "workspace discard refused: %s outside root %s",
                resolved,
                self.root,
            )
            return False
        if not resolved.exists():
            with self._managed_lock:
                self._managed_paths.pop(thread_id, None)
            return False
        shutil.rmtree(resolved, ignore_errors=True)
        removed = not resolved.exists()
        if removed:
            with self._managed_lock:
                self._managed_paths.pop(thread_id, None)
        return removed

    def resolve_cwd(self, thread_id: str, explicit: str | None) -> str:
        """Decide which cwd a turn should use.

        An explicit path from the caller wins (power users, scripts).
        An absent path means "put the thread in its own sandbox".
        """
        if explicit is not None and str(explicit).strip():
            return str(explicit)
        return str(self.allocate(thread_id))

    def _contains(self, candidate: Path) -> bool:
        try:
            candidate.relative_to(self.root)
            return True
        except ValueError:
            return False

    def _normalized_parts(self, path: Path) -> tuple[Path, tuple[str, ...]]:
        normalized = Path(os.path.abspath(path.expanduser()))
        try:
            relative = normalized.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("workspace path is outside the workspace root") from exc
        parts = tuple(relative.parts)
        if not parts:
            raise ValueError("workspace path must be below the workspace root")
        for component in parts:
            _validate_component(component)
        return normalized, parts

    def _open_root_fd(self) -> int:
        # The configured data root is trusted deployment state. The workspace
        # root itself may not be a symlink when opened; O_NOFOLLOW closes the
        # replacement window after mkdir.
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.root, _directory_open_flags())
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                raise ValueError("workspace root contains a symlink") from exc
            raise
        if not stat.S_ISDIR(os.fstat(fd).st_mode):
            os.close(fd)
            raise ValueError("workspace root is not a directory")
        return fd

    def _open_directory_chain(
        self,
        path: Path,
        *,
        create: bool,
    ) -> tuple[int, Path, os.stat_result]:
        normalized, parts = self._normalized_parts(path)
        current_fd = self._open_root_fd()
        try:
            for component in parts:
                if create:
                    with contextlib.suppress(FileExistsError):
                        os.mkdir(component, mode=0o700, dir_fd=current_fd)
                try:
                    next_fd = os.open(
                        component,
                        _directory_open_flags(),
                        dir_fd=current_fd,
                    )
                except OSError as exc:
                    if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                        raise ValueError(
                            "workspace path contains a symlink or non-directory"
                        ) from exc
                    raise
                if not stat.S_ISDIR(os.fstat(next_fd).st_mode):
                    os.close(next_fd)
                    raise ValueError("workspace path component is not a directory")
                os.close(current_fd)
                current_fd = next_fd
            return current_fd, normalized, os.fstat(current_fd)
        except BaseException:
            os.close(current_fd)
            raise

    @staticmethod
    def _validate_regular_entry_at(directory_fd: int, name: str) -> os.stat_result:
        entry = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if not stat.S_ISREG(entry.st_mode):
            raise ValueError(f"workspace metadata entry {name!r} is not a regular file")
        if entry.st_nlink != 1:
            raise ValueError(f"workspace metadata entry {name!r} has multiple hard links")
        return entry

    @classmethod
    def _validate_published_regular_entry_at(
        cls,
        directory_fd: int,
        name: str,
    ) -> os.stat_result:
        """Validate a no-clobber publication without rejecting its tiny link window.

        ``link(temp, target)`` is the portable dirfd-relative no-replace
        primitive. Between that call and the publisher unlinking ``temp``, the
        inode legitimately has two links. A competing process may observe that
        state; retry it briefly, while still rejecting a persistent hard link.
        """
        for attempt in range(12):
            entry = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if not stat.S_ISREG(entry.st_mode) or entry.st_nlink == 1:
                return cls._validate_regular_entry_at(directory_fd, name)
            if attempt < 11:
                time.sleep(0.001)
        return cls._validate_regular_entry_at(directory_fd, name)

    @staticmethod
    def _write_temporary_file_at(directory_fd: int, name: str, payload: bytes) -> str:
        temporary = _temporary_entry_name(name)
        fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | _file_open_flags(),
            0o600,
            dir_fd=directory_fd,
        )
        try:
            _write_all(fd, payload)
            os.fsync(fd)
        finally:
            os.close(fd)
        return temporary

    @classmethod
    def _atomic_replace_file_at(cls, directory_fd: int, name: str, payload: bytes) -> None:
        temporary = cls._write_temporary_file_at(directory_fd, name, payload)
        try:
            # POSIX rename is atomic and replaces the directory entry itself;
            # it never follows a symlink stored at ``name``.
            os.rename(
                temporary,
                name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
            _best_effort_fsync_directory(directory_fd)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temporary, dir_fd=directory_fd)

    @classmethod
    def _create_file_if_missing_at(cls, directory_fd: int, name: str, payload: bytes) -> None:
        temporary = cls._write_temporary_file_at(directory_fd, name, payload)
        try:
            try:
                # Linking a complete temporary file is an atomic no-clobber
                # publish. If another allocator won, validate its entry rather
                # than overwriting it or following a malicious link.
                os.link(
                    temporary,
                    name,
                    src_dir_fd=directory_fd,
                    dst_dir_fd=directory_fd,
                    follow_symlinks=False,
                )
                # End the legitimate two-link publication window before the
                # directory fsync. Leaving this until ``finally`` made normal
                # concurrent readers reject the new manifest as hard-linked.
                os.unlink(temporary, dir_fd=directory_fd)
                temporary = ""
            except FileExistsError:
                cls._validate_published_regular_entry_at(directory_fd, name)
            _best_effort_fsync_directory(directory_fd)
        finally:
            if temporary:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(temporary, dir_fd=directory_fd)

    @staticmethod
    def _ensure_relative_directory_at(directory_fd: int, relative: str) -> None:
        current_fd = os.dup(directory_fd)
        try:
            for component in Path(relative).parts:
                _validate_component(component)
                with contextlib.suppress(FileExistsError):
                    os.mkdir(component, mode=0o700, dir_fd=current_fd)
                try:
                    next_fd = os.open(
                        component,
                        _directory_open_flags(),
                        dir_fd=current_fd,
                    )
                except OSError as exc:
                    if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                        raise ValueError(
                            "workspace layout contains a symlink or non-directory"
                        ) from exc
                    raise
                os.close(current_fd)
                current_fd = next_fd
        finally:
            os.close(current_fd)

    @staticmethod
    def _verify_directory_binding(
        path: Path,
        expected: os.stat_result,
    ) -> None:
        try:
            current = os.lstat(path)
        except FileNotFoundError as exc:
            raise ValueError("workspace directory was rebound during allocation") from exc
        if stat.S_ISLNK(current.st_mode) or not stat.S_ISDIR(current.st_mode):
            raise ValueError("workspace directory was replaced by a symlink or non-directory")
        if (current.st_dev, current.st_ino) != (expected.st_dev, expected.st_ino):
            raise ValueError("workspace directory was rebound during allocation")
        if path.resolve(strict=True) != path:
            raise ValueError("workspace path contains a symlink")

    def _ensure_layout_dirfd(self, path: Path, thread_id: str) -> None:
        directory_fd, normalized, expected = self._open_directory_chain(path, create=True)
        try:
            self._atomic_replace_file_at(
                directory_fd,
                ".gitignore",
                _GITIGNORE_BODY.encode("utf-8"),
            )
            for relative in _STANDARD_DIRS:
                self._ensure_relative_directory_at(directory_fd, relative)
            manifest_payload = self._manifest_text(normalized, thread_id).encode("utf-8")
            try:
                self._validate_regular_entry_at(directory_fd, _MANIFEST_NAME)
            except FileNotFoundError:
                self._create_file_if_missing_at(
                    directory_fd,
                    _MANIFEST_NAME,
                    manifest_payload,
                )
            # Re-open every standard directory from the still-pinned root fd.
            # A replacement between creation steps is rejected before return.
            for relative in _STANDARD_DIRS:
                self._ensure_relative_directory_at(directory_fd, relative)
        finally:
            os.close(directory_fd)
        self._verify_directory_binding(normalized, expected)

    def _fallback_validate_directory(self, path: Path, *, create: bool) -> Path:
        """Portable strict fallback for runtimes without dirfd/O_NOFOLLOW.

        Python does not expose handle-relative directory creation on every
        platform. This fallback repeatedly uses lstat before and after mkdir
        and refuses any reparse/symlink entry. Atomic file replacement still
        prevents final-component link following.
        """

        normalized, parts = self._normalized_parts(path)
        self.root.mkdir(parents=True, exist_ok=True)
        current = self.root
        for component in parts:
            current = current / component
            try:
                info = os.lstat(current)
            except FileNotFoundError:
                if not create:
                    raise
                current.mkdir()
                info = os.lstat(current)
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise ValueError("workspace path contains a symlink or non-directory")
        if normalized.resolve(strict=True) != normalized:
            raise ValueError("workspace path contains a symlink")
        return normalized

    @staticmethod
    def _atomic_replace_path(path: Path, payload: bytes) -> None:
        temporary = path.parent / _temporary_entry_name(path.name)
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            _write_all(fd, payload)
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            os.replace(temporary, path)
        finally:
            with contextlib.suppress(FileNotFoundError):
                temporary.unlink()

    def _ensure_layout_fallback(self, path: Path, thread_id: str) -> None:
        normalized = self._fallback_validate_directory(path, create=True)
        self._atomic_replace_path(
            normalized / ".gitignore",
            _GITIGNORE_BODY.encode("utf-8"),
        )
        for relative in _STANDARD_DIRS:
            self._fallback_validate_directory(normalized / relative, create=True)
        manifest = normalized / _MANIFEST_NAME
        try:
            info = os.lstat(manifest)
        except FileNotFoundError:
            self._atomic_replace_path(
                manifest,
                self._manifest_text(normalized, thread_id).encode("utf-8"),
            )
        else:
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                raise ValueError("workspace manifest is not a regular file")
            if info.st_nlink != 1:
                raise ValueError("workspace manifest has multiple hard links")
        self._fallback_validate_directory(normalized, create=False)

    def _ensure_layout(self, path: Path, thread_id: str) -> None:
        if _supports_secure_dirfd():
            self._ensure_layout_dirfd(path, thread_id)
            return
        self._ensure_layout_fallback(path, thread_id)

    @staticmethod
    def _manifest_text(path: Path, thread_id: str) -> str:
        payload = {
            "schema": "echo.workspace.v1",
            "thread_id": thread_id,
            "slug": path.name,
            "created_at": datetime.now(UTC).isoformat(),
            "dirs": {
                "upload": "upload",
                "output": "output",
                "stages": "output/stages",
                "final": "output/final",
                "deploy": "deploy",
                "skills": "skills",
            },
        }
        return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"

    def _write_manifest(self, path: Path, thread_id: str) -> None:
        payload = self._manifest_text(path, thread_id).encode("utf-8")
        if _supports_secure_dirfd():
            directory_fd, normalized, expected = self._open_directory_chain(path, create=False)
            try:
                self._atomic_replace_file_at(directory_fd, _MANIFEST_NAME, payload)
            finally:
                os.close(directory_fd)
            self._verify_directory_binding(normalized, expected)
            return
        normalized = self._fallback_validate_directory(path, create=False)
        self._atomic_replace_path(normalized / _MANIFEST_NAME, payload)
        self._fallback_validate_directory(normalized, create=False)

    @staticmethod
    def _read_text_from_fd(fd: int) -> str:
        chunks: list[bytes] = []
        while True:
            chunk = os.read(fd, 64 * 1024)
            if not chunk:
                return b"".join(chunks).decode("utf-8")
            chunks.append(chunk)

    def _read_manifest_text(self, path: Path) -> str:
        if _supports_secure_dirfd():
            directory_fd, normalized, expected = self._open_directory_chain(path, create=False)
            try:
                try:
                    fd = os.open(
                        _MANIFEST_NAME,
                        os.O_RDONLY | _file_open_flags(),
                        dir_fd=directory_fd,
                    )
                except OSError as exc:
                    if exc.errno == errno.ELOOP:
                        raise ValueError("workspace manifest is a symlink") from exc
                    raise
                try:
                    info = os.fstat(fd)
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        raise ValueError("workspace manifest is not a private regular file")
                    content = self._read_text_from_fd(fd)
                finally:
                    os.close(fd)
            finally:
                os.close(directory_fd)
            self._verify_directory_binding(normalized, expected)
            return content
        normalized = self._fallback_validate_directory(path, create=False)
        manifest = normalized / _MANIFEST_NAME
        info = os.lstat(manifest)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("workspace manifest is not a private regular file")
        return manifest.read_text(encoding="utf-8")


def _safe_slug(thread_id: str) -> str:
    """Turn an arbitrary thread id into a safe directory name.

    Allow-list approach: keep alphanumerics, dash, underscore, dot. Anything
    else becomes ``_``. An empty slug falls back to ``thread``. No attempt
    to preserve readability — ids are opaque identifiers upstream, and
    logs always show the original id anyway.
    """
    chars = []
    for ch in thread_id or "":
        if ch.isalnum() or ch in "-_.":
            chars.append(ch)
        else:
            chars.append("_")
    slug = "".join(chars).strip("._") or "thread"
    return slug[:64]


__all__ = [
    "MANAGED_WORKSPACE_DELETION_KEY",
    "MANAGED_WORKSPACE_DELETION_MARKER",
    "MANAGED_WORKSPACE_MARKER",
    "MANAGED_WORKSPACE_METADATA_KEY",
    "PROTECTED_WORKSPACE_METADATA_KEYS",
    "WorkspaceLayout",
    "WorkspaceManager",
    "managed_workspace_metadata",
    "managed_workspace_path",
    "strip_client_workspace_metadata",
    "verified_managed_workspace",
]
