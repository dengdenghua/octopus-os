"""SMB/CIFS mount backend.

Extracted from ``mount_backend.py`` (god-file reduction). Backed by
``smbprotocol`` (``smbclient``) with graceful fallback when the optional
dependency is missing.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any, ClassVar

from ._mount_backend_errors import BackendUnavailableError
from ._mount_backend_types import DirEntry, FileStat, MountBackend

_logger = logging.getLogger(__name__)


def _smb_mtime(info: Any) -> float:
    """Extract mtime from an smbprotocol FileInfo-like object."""
    for attr in ("last_write_time", "mtime", "st_mtime"):
        val = getattr(info, attr, None)
        if isinstance(val, (int, float)):
            return float(val)
    return 0.0


class SmbMountBackend(MountBackend):
    """SMB/CIFS backend backed by ``smbprotocol`` (``smbclient``).

    Graceful fallback: if ``smbprotocol`` is not installed,
    ``test_connection`` returns ``False`` and other methods raise
    ``BackendUnavailableError`` with the install hint.
    """

    _DEP_HINT: ClassVar[str] = "smbprotocol not installed · pip install smbprotocol"

    def __init__(
        self,
        *,
        host: str,
        share: str,
        username: str | None = None,
        password: str | None = None,
        domain: str | None = None,
        root_path: str = "",
        timeout: int = 30,
    ) -> None:
        if not host:
            raise ValueError("host required")
        if not share:
            raise ValueError("share required")
        self.host = host
        self.share = share.strip("\\/")
        self.username = username
        self.password = password
        self.domain = domain
        self.root_path = root_path or ""
        self.timeout = timeout

    def _ensure_available(self) -> None:
        try:
            import smbclient  # noqa: F401
        except ImportError as e:  # pragma: no cover - exercised via mock
            raise BackendUnavailableError(self._DEP_HINT) from e

    def _unc(self, path: str) -> str:
        # Normalise to backslash-separated components; callers may pass
        # either POSIX-style or Windows-style relative paths.
        rel = path.replace("/", "\\").strip("\\/")
        parts = [f"\\\\{self.host}", self.share]
        root = self.root_path.replace("/", "\\").strip("\\/")
        if root:
            parts.append(root)
        if rel:
            parts.append(rel)
        return "\\".join(parts)

    @staticmethod
    def _rel(unc: str) -> str:
        # Strip the leading \\host\share\[root]\ prefix as best-effort;
        # callers primarily care about the leaf name for display.
        parts = unc.split("\\")
        return "\\".join(parts[3:]) if len(parts) > 3 else parts[-1]

    async def read_file(self, path: str) -> bytes:
        self._ensure_available()
        import smbclient

        unc = self._unc(path)

        def _read() -> bytes:
            with smbclient.open_file(unc, mode="rb") as fh:
                return fh.read()

        return await asyncio.to_thread(_read)

    async def write_file(self, path: str, content: bytes) -> None:
        self._ensure_available()
        import smbclient

        unc = self._unc(path)

        def _write() -> None:
            parent = "\\".join(unc.split("\\")[:-1])
            if parent:
                with contextlib.suppress(Exception):
                    smbclient.mkdir(parent)
            with smbclient.open_file(unc, mode="wb") as fh:
                fh.write(content)

        await asyncio.to_thread(_write)

    async def list_dir(self, path: str, depth: int = 1) -> list[DirEntry]:
        self._ensure_available()
        import smbclient

        unc = self._unc(path)
        return await asyncio.to_thread(self._list_dir_sync, smbclient, unc, depth)

    def _list_dir_sync(self, smbclient: Any, unc: str, depth: int) -> list[DirEntry]:
        entries: list[DirEntry] = []

        def _recurse(current: str, current_depth: int) -> None:
            if current_depth > depth:
                return
            try:
                infos = smbclient.listdir(current)
            except Exception as exc:  # noqa: BLE001
                _logger.warning("smb_backend · listdir failed on %s: %s", current, exc)
                return
            for name in sorted(infos, key=lambda n: n.lower()):
                child = f"{current}\\{name}"
                try:
                    info = smbclient.getinfo(child)
                except Exception:  # noqa: BLE001
                    continue
                is_dir = info.is_directory()
                entries.append(
                    DirEntry(
                        name=name,
                        path=self._rel(child) or name,
                        is_dir=is_dir,
                        size=getattr(info, "file_size", 0) or 0,
                        modified=_smb_mtime(info),
                    )
                )
                if is_dir and current_depth < depth:
                    _recurse(child, current_depth + 1)

        _recurse(unc, 1)
        return entries

    async def stat(self, path: str) -> FileStat:
        self._ensure_available()
        import smbclient

        unc = self._unc(path)
        info = await asyncio.to_thread(smbclient.getinfo, unc)
        return FileStat(
            path=self._rel(unc) or path,
            is_dir=info.is_directory(),
            size=getattr(info, "file_size", 0) or 0,
            modified=_smb_mtime(info),
        )

    async def mkdir(self, path: str) -> None:
        self._ensure_available()
        import smbclient

        unc = self._unc(path)
        await asyncio.to_thread(smbclient.mkdir, unc)

    async def remove(self, path: str) -> None:
        self._ensure_available()
        import smbclient

        unc = self._unc(path)

        def _remove() -> None:
            info = smbclient.getinfo(unc)
            if info.is_directory():
                smbclient.rmdir(unc)
            else:
                smbclient.remove(unc)

        await asyncio.to_thread(_remove)

    async def test_connection(self) -> bool:
        try:
            self._ensure_available()
        except BackendUnavailableError as e:
            _logger.warning("smb_backend · unavailable: %s", e)
            return False
        try:
            import smbclient

            if self.username is not None and self.password is not None:
                await asyncio.to_thread(
                    smbclient.register_session,
                    self.host,
                    username=self.username,
                    password=self.password,
                    domain=self.domain,
                )
            unc = f"\\\\{self.host}\\{self.share}"
            await asyncio.to_thread(smbclient.getinfo, unc)
            return True
        except Exception as e:  # noqa: BLE001 — probe must not raise
            _logger.warning("smb_backend · test_connection failed: %s", e)
            return False


__all__ = ["SmbMountBackend"]
