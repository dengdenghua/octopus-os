"""Mount backend registry.

Extracted from ``mount_backend.py`` (god-file reduction). Routes
``mount_type`` to the corresponding adapter class and caches per-workspace
instances so repeated ``get_or_create`` calls return the same backend (and
the same underlying SFTP/HTTP connection pool).
"""

from __future__ import annotations

from ._mount_backend_local import LocalMountBackend
from ._mount_backend_nfs import NfsMountBackend
from ._mount_backend_s3 import S3MountBackend
from ._mount_backend_sftp import SftpMountBackend
from ._mount_backend_smb import SmbMountBackend
from ._mount_backend_types import MountBackend
from ._mount_backend_webdav import WebdavMountBackend


class MountBackendRegistry:
    """Routes ``mount_type`` to the corresponding adapter class.

    Per-workspace instances are cached so repeated ``get_or_create``
    calls return the same backend (and the same underlying SFTP/HTTP
    connection pool).
    """

    def __init__(self) -> None:
        self._backends: dict[str, type[MountBackend]] = {}
        self._instances: dict[str, MountBackend] = {}  # workspace_id -> backend

    def register(self, mount_type: str, backend_class: type[MountBackend]) -> None:
        if not mount_type:
            raise ValueError("mount_type required")
        if not issubclass(backend_class, MountBackend):
            raise TypeError(f"backend_class must subclass MountBackend: {backend_class}")
        self._backends[mount_type.lower()] = backend_class

    def is_registered(self, mount_type: str) -> bool:
        return mount_type.lower() in self._backends

    def get_backend(
        self,
        workspace_id: str,
        mount_type: str,
        mount_target: str,
        mount_options: dict,
    ) -> MountBackend:
        cls = self._backends.get(mount_type.lower())
        if cls is None:
            raise KeyError(f"unknown mount_type: {mount_type!r}")
        return self._instantiate(cls, mount_target, mount_options)

    def get_or_create(
        self,
        workspace_id: str,
        mount_type: str,
        mount_target: str,
        mount_options: dict,
    ) -> MountBackend:
        if not workspace_id:
            raise ValueError("workspace_id required")
        cached = self._instances.get(workspace_id)
        if cached is not None:
            return cached
        backend = self.get_backend(workspace_id, mount_type, mount_target, mount_options)
        self._instances[workspace_id] = backend
        return backend

    def invalidate(self, workspace_id: str) -> None:
        self._instances.pop(workspace_id, None)

    @staticmethod
    def _instantiate(
        cls: type[MountBackend],
        mount_target: str,
        mount_options: dict,
    ) -> MountBackend:
        opts = dict(mount_options or {})
        # LocalMountBackend / NfsMountBackend take the root path as the
        # first positional arg; the others use keyword-only connection
        # params. Mount_target is the connection string / path. Use
        # issubclass so subclasses of the local/nfs backends also get
        # the positional-arg treatment.
        if issubclass(cls, LocalMountBackend) or issubclass(cls, NfsMountBackend):
            return cls(mount_target, **opts)
        return cls(**opts)


# Module-level default registry, pre-populated with all six backends.
default_registry: MountBackendRegistry = MountBackendRegistry()
default_registry.register("local", LocalMountBackend)
default_registry.register("sftp", SftpMountBackend)
default_registry.register("webdav", WebdavMountBackend)
default_registry.register("smb", SmbMountBackend)
default_registry.register("nfs", NfsMountBackend)
default_registry.register("s3", S3MountBackend)


__all__ = ["MountBackendRegistry", "default_registry"]
