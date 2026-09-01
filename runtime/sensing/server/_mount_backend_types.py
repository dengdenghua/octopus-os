"""Shared data classes and the abstract base for mount backends.

Extracted from ``mount_backend.py`` (god-file reduction). This is a leaf
module (no imports from the other mount_backend submodules) so it can be
imported by every adapter without introducing a circular import cycle.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class DirEntry:
    """A single entry returned by ``list_dir``."""

    name: str
    path: str
    is_dir: bool
    size: int
    modified: float  # unix timestamp


@dataclass
class FileStat:
    """Metadata for a single file or directory."""

    path: str
    is_dir: bool
    size: int
    modified: float
    created: float | None = None


class MountBackend(ABC):
    """Unified filesystem access abstraction layer."""

    @abstractmethod
    async def read_file(self, path: str) -> bytes: ...

    @abstractmethod
    async def write_file(self, path: str, content: bytes) -> None: ...

    @abstractmethod
    async def list_dir(self, path: str, depth: int = 1) -> list[DirEntry]: ...

    @abstractmethod
    async def stat(self, path: str) -> FileStat: ...

    @abstractmethod
    async def mkdir(self, path: str) -> None: ...

    @abstractmethod
    async def remove(self, path: str) -> None: ...

    @abstractmethod
    async def test_connection(self) -> bool: ...


def stat_is_dir(attr: Any) -> bool:
    """Best-effort ``is_dir`` for paramiko SFTPAttributes."""
    mode = getattr(attr, "st_mode", None)
    if mode is None:
        return False
    import stat as _stat

    return _stat.S_ISDIR(mode)


__all__ = ["DirEntry", "FileStat", "MountBackend", "stat_is_dir"]
