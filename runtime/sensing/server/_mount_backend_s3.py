"""S3 mount backend.

Extracted from ``mount_backend.py`` (god-file reduction). Uses ``boto3``
(lazy import) with a custom ``endpoint_url`` for MinIO/OSS. S3 has no real
directories: ``mkdir`` creates a zero-byte ``<path>/`` marker, ``list_dir``
synthesises directory entries from common prefixes, and ``remove`` on a
directory deletes all keys under that prefix.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any, ClassVar

from ._mount_backend_errors import BackendUnavailableError
from ._mount_backend_types import DirEntry, FileStat, MountBackend

_logger = logging.getLogger(__name__)


def _to_timestamp(dt: Any) -> float:
    """Convert a datetime (e.g. boto3 LastModified) to unix timestamp."""
    if dt is None:
        return 0.0
    try:
        return dt.timestamp()
    except (AttributeError, ValueError, OSError):
        return 0.0


class S3MountBackend(MountBackend):
    """S3 backend compatible with AWS S3, MinIO, and 阿里云 OSS.

    Uses ``boto3`` (lazy import) with a custom ``endpoint_url`` for
    MinIO/OSS. S3 has no real directories: ``mkdir`` creates a
    zero-byte ``<path>/`` marker, ``list_dir`` synthesises directory
    entries from common prefixes, and ``remove`` on a directory
    deletes all keys under that prefix.
    """

    _DEP_HINT: ClassVar[str] = "boto3 not installed · pip install boto3"

    def __init__(
        self,
        *,
        bucket: str,
        endpoint_url: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        region: str | None = None,
        root_path: str = "",
    ) -> None:
        if not bucket:
            raise ValueError("bucket required")
        self.bucket = bucket
        self.endpoint_url = endpoint_url
        self.access_key = access_key
        self.secret_key = secret_key
        self.region = region
        self.root_path = root_path.strip("/")
        self._client: Any = None
        self._lock = asyncio.Lock()

    def _ensure_available(self) -> None:
        try:
            import boto3  # noqa: F401
        except ImportError as e:  # pragma: no cover - exercised via mock
            raise BackendUnavailableError(self._DEP_HINT) from e

    async def _get_client(self) -> Any:
        async with self._lock:
            if self._client is not None:
                return self._client
            self._ensure_available()
            self._client = await asyncio.to_thread(self._connect_sync)
            return self._client

    def _connect_sync(self) -> Any:
        import boto3

        kwargs: dict[str, Any] = {}
        if self.endpoint_url:
            kwargs["endpoint_url"] = self.endpoint_url
        if self.access_key is not None and self.secret_key is not None:
            kwargs["aws_access_key_id"] = self.access_key
            kwargs["aws_secret_access_key"] = self.secret_key
        if self.region:
            kwargs["region_name"] = self.region
        return boto3.client("s3", **kwargs)

    # ── key resolution ────────────────────────────────────

    def _resolve_key(self, path: str) -> str:
        rel = path.lstrip("/")
        if self.root_path:
            return f"{self.root_path}/{rel}" if rel else self.root_path
        return rel

    def _rel_key(self, key: str) -> str:
        if self.root_path and key.startswith(self.root_path + "/"):
            return key[len(self.root_path) + 1 :]
        return key

    # ── MountBackend implementation ───────────────────────

    async def read_file(self, path: str) -> bytes:
        client = await self._get_client()
        key = self._resolve_key(path)

        def _read() -> bytes:
            response = client.get_object(Bucket=self.bucket, Key=key)
            return response["Body"].read()

        return await asyncio.to_thread(_read)

    async def write_file(self, path: str, content: bytes) -> None:
        client = await self._get_client()
        key = self._resolve_key(path)

        def _write() -> None:
            client.put_object(Bucket=self.bucket, Key=key, Body=content)

        await asyncio.to_thread(_write)

    async def list_dir(self, path: str, depth: int = 1) -> list[DirEntry]:
        client = await self._get_client()
        prefix = self._resolve_key(path)
        if prefix and not prefix.endswith("/"):
            prefix += "/"
        return await asyncio.to_thread(self._list_dir_sync, client, prefix, depth)

    def _list_dir_sync(self, client: Any, prefix: str, depth: int) -> list[DirEntry]:
        entries: list[DirEntry] = []
        paginator = client.get_paginator("list_objects_v2")
        # depth=1: immediate children only (Delimiter="/").
        # depth>1: paginated full recursion + post-filter by depth.
        if depth <= 1:
            pages = paginator.paginate(Bucket=self.bucket, Prefix=prefix, Delimiter="/")
            for page in pages:
                for cp in page.get("CommonPrefixes", []) or []:
                    raw = cp["Prefix"].rstrip("/")
                    name = raw.rsplit("/", 1)[-1]
                    entries.append(
                        DirEntry(
                            name=name,
                            path=self._rel_key(raw),
                            is_dir=True,
                            size=0,
                            modified=0.0,
                        )
                    )
                for obj in page.get("Contents", []) or []:
                    if obj["Key"] == prefix:
                        continue
                    name = obj["Key"].rsplit("/", 1)[-1]
                    if not name:
                        continue
                    entries.append(
                        DirEntry(
                            name=name,
                            path=self._rel_key(obj["Key"]),
                            is_dir=False,
                            size=obj.get("Size", 0),
                            modified=_to_timestamp(obj.get("LastModified")),
                        )
                    )
        else:
            # Full recursion; then prune by depth.
            seen_keys: set[str] = set()
            pages = paginator.paginate(Bucket=self.bucket, Prefix=prefix)
            base_depth = prefix.count("/")
            for page in pages:
                for obj in page.get("Contents", []) or []:
                    key = obj["Key"]
                    if key in seen_keys or key == prefix:
                        continue
                    rel_depth = key.count("/") - base_depth
                    if rel_depth > depth:
                        continue
                    seen_keys.add(key)
                    name = key.rstrip("/").rsplit("/", 1)[-1]
                    # Synthesize intermediate directory entries.
                    parts = key[len(prefix) :].rstrip("/").split("/")[:-1]
                    cur = prefix.rstrip("/")
                    for part in parts:
                        cur = f"{cur}/{part}"
                        if cur in seen_keys:
                            continue
                        seen_keys.add(cur)
                        entries.append(
                            DirEntry(
                                name=part,
                                path=self._rel_key(cur),
                                is_dir=True,
                                size=0,
                                modified=0.0,
                            )
                        )
                    entries.append(
                        DirEntry(
                            name=name,
                            path=self._rel_key(key.rstrip("/")),
                            is_dir=False,
                            size=obj.get("Size", 0),
                            modified=_to_timestamp(obj.get("LastModified")),
                        )
                    )
            entries.sort(key=lambda e: (not e.is_dir, e.path.lower()))
        return entries

    async def stat(self, path: str) -> FileStat:
        client = await self._get_client()
        key = self._resolve_key(path)

        def _stat() -> FileStat:
            # Try as a file first.
            try:
                head = client.head_object(Bucket=self.bucket, Key=key)
                return FileStat(
                    path=self._rel_key(key),
                    is_dir=False,
                    size=head.get("ContentLength", 0),
                    modified=_to_timestamp(head.get("LastModified")),
                )
            except Exception:  # noqa: BLE001 — fall through to dir check
                pass
            # Maybe a "directory" (prefix).
            prefix = key.rstrip("/") + "/"
            response = client.list_objects_v2(
                Bucket=self.bucket,
                Prefix=prefix,
                MaxKeys=1,
            )
            if response.get("KeyCount", 0) > 0:
                return FileStat(
                    path=self._rel_key(key.rstrip("/")),
                    is_dir=True,
                    size=0,
                    modified=0.0,
                )
            raise FileNotFoundError(f"s3 key not found: {key}")

        return await asyncio.to_thread(_stat)

    async def mkdir(self, path: str) -> None:
        client = await self._get_client()
        key = self._resolve_key(path).rstrip("/") + "/"

        def _mkdir() -> None:
            client.put_object(Bucket=self.bucket, Key=key, Body=b"")

        await asyncio.to_thread(_mkdir)

    async def remove(self, path: str) -> None:
        client = await self._get_client()
        key = self._resolve_key(path)

        def _remove() -> None:
            # Try as file first.
            try:
                client.head_object(Bucket=self.bucket, Key=key)
                client.delete_object(Bucket=self.bucket, Key=key)
                return
            except Exception:  # noqa: BLE001 — fall through to prefix delete
                pass
            prefix = key.rstrip("/") + "/"
            paginator = client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                objs = page.get("Contents", []) or []
                if not objs:
                    continue
                client.delete_objects(
                    Bucket=self.bucket,
                    Delete={"Objects": [{"Key": o["Key"]} for o in objs]},
                )
            # Also remove the directory marker itself.
            with contextlib.suppress(Exception):
                client.delete_object(Bucket=self.bucket, Key=prefix)

        await asyncio.to_thread(_remove)

    async def test_connection(self) -> bool:
        try:
            self._ensure_available()
        except BackendUnavailableError as e:
            _logger.warning("s3_backend · unavailable: %s", e)
            return False
        try:
            client = await self._get_client()

            def _probe() -> bool:
                # list_buckets needs ListAllMyBuckets; some MinIO setups
                # don't grant it. list_objects_v2 on the bucket is the
                # more reliable reachability probe.
                client.list_objects_v2(Bucket=self.bucket, MaxKeys=1)
                return True

            return await asyncio.to_thread(_probe)
        except Exception as e:  # noqa: BLE001 — probe must not raise
            _logger.warning("s3_backend · test_connection failed: %s", e)
            return False


__all__ = ["S3MountBackend"]
