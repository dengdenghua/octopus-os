"""Local filesystem mount backend.

Extracted from ``mount_backend.py`` (god-file reduction). Wraps
:class:`runtime.sensing.server.local.LocalBackend` for read/write whitelist
checks and ``atomic_write_bytes`` for crash-safe writes.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from pathlib import Path

from runtime.platform.io.atomic import atomic_write_bytes

from ._mount_backend_types import DirEntry, FileStat, MountBackend
from .local import LocalBackend

_logger = logging.getLogger(__name__)


# Subset of fs_router.TREE_IGNORED_DIRS that the mount layer filters
# from list_dir by default. Kept small and conservative so remote
# mounts don't surprise the caller by hiding real directories.
DEFAULT_IGNORED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        "node_modules",
        ".echo",
        "logs",
    }
)


class LocalMountBackend(MountBackend):
    """Local filesystem backend with path whitelist enforcement.

    Wraps :class:`runtime.sensing.server.local.LocalBackend` for
    read/write whitelist checks. Writes go through
    :func:`runtime.platform.io.atomic.atomic_write_bytes` so a crash
    mid-write never leaves a half-written file.
    """

    def __init__(
        self,
        root_path: str | Path,
        *,
        allowed_read_roots: list[Path] | None = None,
        allowed_write_roots: list[Path] | None = None,
        ignored_dirs: frozenset[str] | None = None,
    ) -> None:
        self.root_path = Path(root_path).expanduser().resolve()
        if not self.root_path.exists():
            raise FileNotFoundError(f"root_path does not exist: {self.root_path}")
        # Default the sandbox to root_path so callers can't escape
        # the mount without explicitly widening the whitelist.
        read_roots = (
            [self.root_path]
            if allowed_read_roots is None
            else [Path(p).resolve() for p in allowed_read_roots]
        )
        write_roots = (
            [self.root_path]
            if allowed_write_roots is None
            else [Path(p).resolve() for p in allowed_write_roots]
        )
        self._guard = LocalBackend(
            allowed_read_roots=read_roots,
            allowed_write_roots=write_roots,
        )
        self.ignored_dirs = ignored_dirs if ignored_dirs is not None else DEFAULT_IGNORED_DIRS

    # ── path resolution ───────────────────────────────────

    def _resolve(self, path: str) -> Path:
        p = Path(path).expanduser()
        return p.resolve() if p.is_absolute() else (self.root_path / p).resolve()

    def _check_read(self, path: Path) -> Path:
        if not self._guard.allows_read(path):
            raise PermissionError(f"backend denied read: {path}")
        return path

    def _check_write(self, path: Path) -> Path:
        if not self._guard.allows_write(path):
            raise PermissionError(f"backend denied write: {path}")
        return path

    def _rel_path(self, p: Path) -> str:
        try:
            return p.relative_to(self.root_path).as_posix()
        except ValueError:
            return str(p)

    # ── MountBackend implementation ───────────────────────

    async def read_file(self, path: str) -> bytes:
        resolved = self._check_read(self._resolve(path))
        return await asyncio.to_thread(resolved.read_bytes)

    async def write_file(self, path: str, content: bytes) -> None:
        resolved = self._check_write(self._resolve(path))
        await asyncio.to_thread(atomic_write_bytes, resolved, content)

    async def list_dir(self, path: str, depth: int = 1) -> list[DirEntry]:
        resolved = self._check_read(self._resolve(path))
        if not resolved.exists():
            raise FileNotFoundError(f"path not found: {resolved}")
        if not resolved.is_dir():
            raise NotADirectoryError(f"not a directory: {resolved}")
        return await asyncio.to_thread(self._list_dir_sync, resolved, depth)

    def _list_dir_sync(self, root: Path, depth: int) -> list[DirEntry]:
        entries: list[DirEntry] = []

        def _recurse(current: Path, current_depth: int) -> None:
            if current_depth > depth:
                return
            try:
                with os.scandir(current) as it:
                    children = sorted(it, key=lambda e: (not e.is_dir(), e.name.lower()))
            except OSError as exc:
                _logger.warning("local_mount · scandir failed on %s: %s", current, exc)
                return
            for entry in children:
                try:
                    is_dir = entry.is_dir()
                    name = entry.name
                    if is_dir and name in self.ignored_dirs:
                        continue
                    info = entry.stat(follow_symlinks=False)
                    entries.append(
                        DirEntry(
                            name=name,
                            path=self._rel_path(Path(entry.path)),
                            is_dir=is_dir,
                            size=info.st_size,
                            modified=info.st_mtime,
                        )
                    )
                    if is_dir and current_depth < depth:
                        _recurse(Path(entry.path), current_depth + 1)
                except OSError as exc:
                    _logger.debug("local_mount · skip %s: %s", entry.path, exc)

        _recurse(root, 1)
        return entries

    async def stat(self, path: str) -> FileStat:
        resolved = self._check_read(self._resolve(path))
        if not resolved.exists():
            raise FileNotFoundError(f"path not found: {resolved}")
        info = await asyncio.to_thread(resolved.stat)
        return FileStat(
            path=self._rel_path(resolved),
            is_dir=info.st_mode & 0o170000 == 0o040000,
            size=info.st_size,
            modified=info.st_mtime,
            created=info.st_ctime,
        )

    async def mkdir(self, path: str) -> None:
        resolved = self._check_write(self._resolve(path))
        await asyncio.to_thread(lambda: resolved.mkdir(parents=True, exist_ok=True))

    async def remove(self, path: str) -> None:
        resolved = self._check_write(self._resolve(path))
        if not resolved.exists():
            raise FileNotFoundError(f"path not found: {resolved}")

        def _remove() -> None:
            if resolved.is_dir() and not resolved.is_symlink():
                shutil.rmtree(resolved)
            else:
                resolved.unlink()

        await asyncio.to_thread(_remove)

    async def test_connection(self) -> bool:
        return await asyncio.to_thread(
            lambda: self.root_path.exists() and os.access(self.root_path, os.R_OK)
        )


__all__ = ["DEFAULT_IGNORED_DIRS", "LocalMountBackend"]
