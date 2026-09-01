"""NFS mount backend.

Extracted from ``mount_backend.py`` (god-file reduction). Python has no
widely-adopted pure-Python NFS client; deployment mounts the export at OS
level and this backend delegates to :class:`LocalMountBackend`.
"""

from __future__ import annotations

from pathlib import Path

from ._mount_backend_local import LocalMountBackend
from ._mount_backend_types import DirEntry, FileStat, MountBackend


class NfsMountBackend(MountBackend):
    """NFS backend.

    Python has no widely-adopted pure-Python NFS client; the standard
    deployment pattern is to mount the NFS export at OS level (Linux
    ``mount -t nfs`` / macOS mount_nfs) and access it through the local
    VFS. This backend therefore delegates to :class:`LocalMountBackend`
    on the configured ``mount_point``.

    For environments where the OS has NOT already mounted the share,
    ``test_connection`` returns ``False`` and the operator is expected
    to mount it out-of-band.
    """

    def __init__(
        self,
        *,
        mount_point: str | Path,
        host: str | None = None,
        export: str | None = None,
    ) -> None:
        self.mount_point = Path(mount_point).expanduser().resolve()
        self.host = host
        self.export = export
        self._local = LocalMountBackend(self.mount_point)

    async def read_file(self, path: str) -> bytes:
        return await self._local.read_file(path)

    async def write_file(self, path: str, content: bytes) -> None:
        await self._local.write_file(path, content)

    async def list_dir(self, path: str, depth: int = 1) -> list[DirEntry]:
        return await self._local.list_dir(path, depth)

    async def stat(self, path: str) -> FileStat:
        return await self._local.stat(path)

    async def mkdir(self, path: str) -> None:
        await self._local.mkdir(path)

    async def remove(self, path: str) -> None:
        await self._local.remove(path)

    async def test_connection(self) -> bool:
        # Heuristic: if the mount_point is an active NFS mount, the
        # process can stat it (already covered by test_connection) and
        # the directory is non-empty or stat-able. We don't strictly
        # verify the fstype here; the operator is responsible for the
        # mount itself.
        return await self._local.test_connection()


__all__ = ["NfsMountBackend"]
