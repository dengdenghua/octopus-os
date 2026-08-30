"""SFTP mount backend.

Extracted from ``mount_backend.py`` (god-file reduction). Backed by
``paramiko.SFTPClient``; connection parameters mirror
:class:`runtime.sensing.server.ssh.SshBackend`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import posixpath
from pathlib import Path
from typing import Any

from runtime.sensing.server._ssh_security import paramiko_disabled_algorithms

from ._mount_backend_errors import BackendUnavailableError
from ._mount_backend_types import DirEntry, FileStat, MountBackend, stat_is_dir

_logger = logging.getLogger(__name__)


class SftpMountBackend(MountBackend):
    """SFTP backend backed by ``paramiko.SFTPClient``.

    Connection parameters mirror :class:`runtime.sensing.server.ssh.SshBackend`
    (``host`` / ``user`` / ``port`` / ``identity_file`` / ``password``)
    so the same mount_options shape works for both.
    """

    def __init__(
        self,
        *,
        host: str,
        user: str | None = None,
        port: int = 22,
        identity_file: str | Path | None = None,
        password: str | None = None,
        root_path: str = "/",
        connect_timeout: int = 10,
        strict_host_key_checking: bool = False,
        known_hosts_file: str | Path | None = None,
    ) -> None:
        if not host:
            raise ValueError("host required")
        if port <= 0 or port > 65535:
            raise ValueError(f"port out of range: {port}")
        self.host = host
        self.user = user
        self.port = port
        self.identity_file = Path(identity_file) if identity_file else None
        self.password = password
        self.root_path = root_path or "/"
        self.connect_timeout = connect_timeout
        self.strict_host_key_checking = strict_host_key_checking
        self.known_hosts_file = Path(known_hosts_file) if known_hosts_file else None
        self._sftp: Any = None
        self._client: Any = None  # underlying SSHClient
        self._lock = asyncio.Lock()

    # ── connection management ─────────────────────────────

    def _ensure_available(self) -> None:
        try:
            import paramiko  # noqa: F401
        except ImportError as e:  # pragma: no cover - exercised via mock
            raise BackendUnavailableError(
                "paramiko not installed · pip install paramiko",
            ) from e

    async def _ensure_connected(self) -> Any:
        async with self._lock:
            if self._sftp is not None:
                # Cheap liveness probe; if the channel is dead we drop
                # the cached handle and reconnect below.
                try:
                    await asyncio.to_thread(self._sftp.stat, ".")
                    return self._sftp
                except Exception:  # noqa: BLE001 — reconnect path
                    _logger.info("sftp_backend · stale connection, reconnecting")
                    self._close_sync()
            self._ensure_available()
            await asyncio.to_thread(self._connect_sync)
            return self._sftp

    def _connect_sync(self) -> None:
        import paramiko

        client = paramiko.SSHClient()
        if self.strict_host_key_checking:
            if self.known_hosts_file is not None:
                client.load_host_keys(str(self.known_hosts_file))
            else:
                client.load_system_host_keys()
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
        else:
            if self.known_hosts_file is not None:
                client.load_host_keys(str(self.known_hosts_file))
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())  # nosec B507
        connect_kwargs: dict[str, Any] = {
            "hostname": self.host,
            "port": self.port,
            "timeout": self.connect_timeout,
            "allow_agent": False,
            "look_for_keys": False,
            "disabled_algorithms": paramiko_disabled_algorithms(),
        }
        if self.user:
            connect_kwargs["username"] = self.user
        if self.identity_file:
            connect_kwargs["key_filename"] = str(self.identity_file)
        if self.password is not None:
            connect_kwargs["password"] = self.password
        client.connect(**connect_kwargs)
        self._client = client
        self._sftp = client.open_sftp()

    def _close_sync(self) -> None:
        if self._sftp is not None:
            with contextlib.suppress(Exception):
                self._sftp.close()
        if self._client is not None:
            with contextlib.suppress(Exception):
                self._client.close()
        self._sftp = None
        self._client = None

    # ── path resolution ───────────────────────────────────

    def _resolve(self, path: str) -> str:
        if posixpath.isabs(path):
            return posixpath.normpath(path)
        return posixpath.normpath(posixpath.join(self.root_path, path))

    def _rel_path(self, p: str) -> str:
        root = self.root_path.rstrip("/")
        if root and p.startswith(root + "/"):
            return p[len(root) + 1 :]
        if p == root:
            return ""
        return p.lstrip("/")

    # ── MountBackend implementation ───────────────────────

    async def read_file(self, path: str) -> bytes:
        sftp = await self._ensure_connected()
        remote = self._resolve(path)

        def _read() -> bytes:
            with sftp.open(remote, "rb") as fh:
                return fh.read()

        return await asyncio.to_thread(_read)

    async def write_file(self, path: str, content: bytes) -> None:
        sftp = await self._ensure_connected()
        remote = self._resolve(path)
        # Write to a sibling temp file then rename — best-effort
        # atomicity, mirroring local atomic_write_bytes semantics.
        tmp = remote + f".echo-tmp-{os.getpid()}-{id(content) & 0xFFFFFFFF}"

        def _write() -> None:
            parent = posixpath.dirname(remote)
            if parent:
                with contextlib.suppress(Exception):
                    sftp.mkdir(parent)
            with sftp.open(tmp, "wb") as fh:
                fh.write(content)
            if hasattr(sftp, "posix_rename"):
                sftp.posix_rename(tmp, remote)
            else:
                sftp.rename(tmp, remote)

        try:
            await asyncio.to_thread(_write)
        except Exception:
            with contextlib.suppress(Exception):
                sftp.remove(tmp)
            raise

    async def list_dir(self, path: str, depth: int = 1) -> list[DirEntry]:
        sftp = await self._ensure_connected()
        remote = self._resolve(path)
        return await asyncio.to_thread(self._list_dir_sync, sftp, remote, depth)

    def _list_dir_sync(self, sftp: Any, remote: str, depth: int) -> list[DirEntry]:
        entries: list[DirEntry] = []

        def _recurse(current: str, current_depth: int) -> None:
            if current_depth > depth:
                return
            try:
                attrs = sftp.listdir_attr(current)
            except OSError as exc:
                _logger.warning("sftp_backend · listdir failed on %s: %s", current, exc)
                return
            attrs.sort(key=lambda a: (not stat_is_dir(a), a.filename.lower()))
            for attr in attrs:
                child_path = posixpath.join(current, attr.filename)
                is_dir = stat_is_dir(attr)
                entries.append(
                    DirEntry(
                        name=attr.filename,
                        path=self._rel_path(child_path),
                        is_dir=is_dir,
                        size=getattr(attr, "st_size", 0) or 0,
                        modified=getattr(attr, "st_mtime", 0.0) or 0.0,
                    )
                )
                if is_dir and current_depth < depth:
                    _recurse(child_path, current_depth + 1)

        _recurse(remote, 1)
        return entries

    async def stat(self, path: str) -> FileStat:
        sftp = await self._ensure_connected()
        remote = self._resolve(path)
        attr = await asyncio.to_thread(sftp.stat, remote)
        return FileStat(
            path=self._rel_path(remote),
            is_dir=stat_is_dir(attr),
            size=getattr(attr, "st_size", 0) or 0,
            modified=getattr(attr, "st_mtime", 0.0) or 0.0,
        )

    async def mkdir(self, path: str) -> None:
        sftp = await self._ensure_connected()
        remote = self._resolve(path)

        def _mkdir() -> None:
            # Build parents like mkdir -p.
            parts = [p for p in remote.split("/") if p]
            cur = "/" if remote.startswith("/") else ""
            for part in parts:
                cur = posixpath.join(cur, part) if cur else part
                with contextlib.suppress(Exception):
                    sftp.mkdir(cur)

        await asyncio.to_thread(_mkdir)

    async def remove(self, path: str) -> None:
        sftp = await self._ensure_connected()
        remote = self._resolve(path)

        def _remove() -> None:
            try:
                sftp.remove(remote)
                return
            except OSError:  # noqa: BLE001 — SFTP uses OSError for the file-vs-directory probe
                pass
            # Directory: recurse + rmdir.
            self._rmtree_sync(sftp, remote)

        await asyncio.to_thread(_remove)

    def _rmtree_sync(self, sftp: Any, remote: str) -> None:
        for attr in sftp.listdir_attr(remote):
            child = posixpath.join(remote, attr.filename)
            if stat_is_dir(attr):
                self._rmtree_sync(sftp, child)
            else:
                with contextlib.suppress(Exception):
                    sftp.remove(child)
        sftp.rmdir(remote)

    async def test_connection(self) -> bool:
        try:
            self._ensure_available()
            await self._ensure_connected()
            return True
        except BackendUnavailableError as e:
            _logger.warning("sftp_backend · unavailable: %s", e)
            return False
        except Exception as e:  # noqa: BLE001 — connection probe should not raise
            _logger.warning("sftp_backend · test_connection failed: %s", e)
            return False


__all__ = ["SftpMountBackend"]
