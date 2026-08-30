"""Mount backend · unified filesystem access abstraction.

Provides a single ``MountBackend`` interface with concrete adapters for
six remote/local filesystem protocols:

  * local  → ``LocalMountBackend`` (pathlib + atomic_write_bytes)
  * sftp   → ``SftpMountBackend`` (paramiko SFTPClient)
  * webdav → ``WebdavMountBackend`` (requests + PROPFIND/GET/PUT/MKCOL/DELETE)
  * smb    → ``SmbMountBackend`` (smbprotocol/smbclient, graceful fallback)
  * nfs    → ``NfsMountBackend`` (delegates to LocalMountBackend on OS-mounted share)
  * s3     → ``S3MountBackend`` (boto3, MinIO/AWS/OSS-compatible, graceful fallback)

All public methods are ``async``; blocking IO is wrapped in
``asyncio.to_thread`` so a single event loop can multiplex many mounts.

Optional dependencies (``paramiko`` / ``requests`` / ``smbprotocol`` /
``boto3``) are imported lazily inside the methods that need them.
``test_connection`` returns ``False`` (rather than raising) when the
optional dep is missing, so a registry probe never crashes the host
process. Other methods raise ``BackendUnavailableError`` so the caller
sees a clear actionable message.

Path semantics
--------------

``path`` arguments are interpreted relative to the backend's
``root_path``. Absolute paths (POSIX-leading ``/`` or, for local,
``Path.is_absolute()``) are used as-is and still subject to the
backend's whitelist (local only). Paths returned in ``DirEntry.path``
and ``FileStat.path`` are relative to ``root_path`` so they can be
round-tripped through the same backend.

Implementations and helpers live in the ``_mount_backend_*`` satellite
modules (god-file reduction); this module re-exports the public API surface
unchanged so existing callers and tests keep working.
"""

from __future__ import annotations

from ._mount_backend_errors import BackendError, BackendUnavailableError
from ._mount_backend_local import DEFAULT_IGNORED_DIRS, LocalMountBackend
from ._mount_backend_nfs import NfsMountBackend
from ._mount_backend_registry import MountBackendRegistry, default_registry
from ._mount_backend_s3 import S3MountBackend
from ._mount_backend_sftp import SftpMountBackend
from ._mount_backend_smb import SmbMountBackend
from ._mount_backend_types import DirEntry, FileStat, MountBackend
from ._mount_backend_webdav import WebdavMountBackend

__all__ = [
    "BackendError",
    "BackendUnavailableError",
    "DEFAULT_IGNORED_DIRS",
    "DirEntry",
    "FileStat",
    "LocalMountBackend",
    "MountBackend",
    "MountBackendRegistry",
    "NfsMountBackend",
    "S3MountBackend",
    "SftpMountBackend",
    "SmbMountBackend",
    "WebdavMountBackend",
    "default_registry",
]
