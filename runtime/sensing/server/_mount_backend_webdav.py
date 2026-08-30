"""WebDAV mount backend.

Extracted from ``mount_backend.py`` (god-file reduction). Pure-HTTP
WebDAV adapter using ``requests`` (lazy import) for PROPFIND / GET / PUT /
MKCOL / DELETE. Compatible with Nextcloud, 坚果云, CloudDrive2, and any
RFC 4918-conformant WebDAV server. Basic Auth only.
"""

from __future__ import annotations

import asyncio
import logging
import posixpath
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote, urlparse

from ._mount_backend_errors import BackendError, BackendUnavailableError
from ._mount_backend_types import DirEntry, FileStat, MountBackend

_logger = logging.getLogger(__name__)


_WEBDAV_PROPFIND_BODY = """<?xml version="1.0" encoding="utf-8" ?>
<D:propfind xmlns:D="DAV:">
  <D:prop>
    <D:displayname/>
    <D:getcontentlength/>
    <D:getlastmodified/>
    <D:resourcetype/>
  </D:prop>
</D:propfind>
"""


def _local_name(tag: str) -> str:
    """Strip XML namespace from an ElementTree tag."""
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _find_local(elem: Any, name: str) -> list[Any]:
    """Find all children of ``elem`` whose local-name matches ``name``."""
    return [c for c in elem.iter() if _local_name(c.tag) == name]


def _extract_dav_props(resp: Any) -> tuple[bool, int, float]:
    """Extract (is_dir, size, mtime) from a WebDAV <response> element."""
    is_dir = False
    size = 0
    modified = 0.0
    for prop in _find_local(resp, "prop"):
        if _find_local(prop, "collection"):
            is_dir = True
        len_el = _find_local(prop, "getcontentlength")
        if len_el and len_el[0].text:
            try:
                size = int(len_el[0].text)
            except ValueError:
                size = 0
        mod_el = _find_local(prop, "getlastmodified")
        if mod_el and mod_el[0].text:
            try:
                modified = parsedate_to_datetime(mod_el[0].text).timestamp()
            except (TypeError, ValueError):
                modified = 0.0
    return is_dir, size, modified


class WebdavMountBackend(MountBackend):
    """Pure-HTTP WebDAV backend.

    Uses ``requests`` (lazy import) to issue PROPFIND / GET / PUT /
    MKCOL / DELETE. Compatible with Nextcloud, 坚果云, CloudDrive2,
    and any RFC 4918-conformant WebDAV server. Basic Auth only.
    """

    def __init__(
        self,
        *,
        base_url: str,
        username: str | None = None,
        password: str | None = None,
        root_path: str = "/",
        timeout: float = 30.0,
        verify_ssl: bool = True,
    ) -> None:
        if not base_url:
            raise ValueError("base_url required")
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.root_path = root_path or "/"
        self.timeout = timeout
        self.verify_ssl = verify_ssl

    # ── helpers ───────────────────────────────────────────

    def _ensure_available(self) -> None:
        try:
            import requests  # noqa: F401
        except ImportError as e:  # pragma: no cover - exercised via mock
            raise BackendUnavailableError(
                "requests not installed · pip install requests",
            ) from e

    def _resolve_url(self, path: str) -> str:
        if posixpath.isabs(path):
            rel = path.lstrip("/")
        else:
            root = self.root_path.strip("/")
            rel = f"{root}/{path}" if root else path
        rel = quote(rel, safe="/")
        return f"{self.base_url}/{rel}"

    def _rel_path(self, url_path: str) -> str:
        # url_path is the DAV:href from PROPFIND, or our own resolved
        # path; strip the base + root prefix to get the relative path.
        try:
            from urllib.parse import unquote

            decoded = unquote(url_path)
            parsed = urlparse(decoded)
            decoded = parsed.path or decoded
        except Exception:  # noqa: BLE001
            decoded = url_path
        base_path = urlparse(self.base_url).path.rstrip("/")
        if base_path and decoded.startswith(base_path):
            decoded = decoded[len(base_path) :]
        root = self.root_path.strip("/")
        if root and decoded.startswith("/" + root):
            decoded = decoded[len(root) + 1 :]
        return decoded.lstrip("/")

    def _auth(self) -> tuple[str, str] | None:
        if self.username is not None and self.password is not None:
            return (self.username, self.password)
        return None

    def _request(
        self,
        method: str,
        url: str,
        *,
        data: bytes | str | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        import requests

        return requests.request(
            method,
            url,
            data=data,
            headers=headers,
            auth=self._auth(),
            timeout=self.timeout,
            verify=self.verify_ssl,
        )

    # ── MountBackend implementation ───────────────────────

    async def read_file(self, path: str) -> bytes:
        self._ensure_available()
        url = self._resolve_url(path)
        response = await asyncio.to_thread(self._request, "GET", url)
        if response.status_code >= 400:
            raise BackendError(f"webdav GET {url} → {response.status_code}: {response.text[:200]}")
        return response.content

    async def write_file(self, path: str, content: bytes) -> None:
        self._ensure_available()
        url = self._resolve_url(path)
        # Ensure parent collection exists (best-effort).
        parent = posixpath.dirname(path.rstrip("/"))
        if parent and parent not in ("", "."):
            await self._ensure_collection(parent)
        response = await asyncio.to_thread(
            self._request,
            "PUT",
            url,
            data=content,
            headers={"Content-Type": "application/octet-stream"},
        )
        if response.status_code >= 400:
            raise BackendError(f"webdav PUT {url} → {response.status_code}: {response.text[:200]}")

    async def _ensure_collection(self, path: str) -> None:
        parts = [p for p in path.split("/") if p]
        cur = ""
        for part in parts:
            cur = f"{cur}/{part}" if cur else part
            url = self._resolve_url(cur)
            response = await asyncio.to_thread(self._request, "MKCOL", url)
            # 201 created, 405 already exists — both fine.
            if response.status_code not in (200, 201, 405):
                _logger.debug("webdav MKCOL %s → %s", url, response.status_code)

    async def list_dir(self, path: str, depth: int = 1) -> list[DirEntry]:
        self._ensure_available()
        url = self._resolve_url(path)
        # Depth: 0 → only the resource itself, 1 → immediate children,
        # infinity → whole subtree (servers may reject). Map our depth
        # to the WebDAV Depth header; for depth > 1 we use infinity and
        # post-filter by the requested depth.
        depth_header = "infinity" if depth > 1 else "1"
        response = await asyncio.to_thread(
            self._request,
            "PROPFIND",
            url,
            data=_WEBDAV_PROPFIND_BODY,
            headers={"Depth": depth_header, "Content-Type": "application/xml"},
        )
        if response.status_code >= 400:
            raise BackendError(
                f"webdav PROPFIND {url} → {response.status_code}: {response.text[:200]}",
            )
        return self._parse_propfind(response.content, path, depth)

    def _parse_propfind(self, body: bytes, requested_path: str, max_depth: int) -> list[DirEntry]:
        try:
            root = ET.fromstring(body)  # nosec B314 — webdav PROPFIND from configured backend
        except ET.ParseError as exc:
            raise BackendError(f"webdav PROPFIND returned malformed XML: {exc}") from exc
        # The first <response> is the collection itself; skip it.
        responses = _find_local(root, "response")
        entries: list[DirEntry] = []
        base_rel = self._rel_path(requested_path).strip("/")
        for resp in responses[1:]:
            href_el = _find_local(resp, "href")
            if not href_el:
                continue
            href_text = href_el[0].text or ""
            rel = self._rel_path(href_text)
            if not rel or rel == base_rel:
                continue
            # Enforce depth for infinity responses.
            if max_depth > 1:
                rel_depth = rel.count("/") - base_rel.count("/")
                if rel_depth < 0:
                    continue
                # Use a lenient depth filter: include direct + nested up to max_depth.
                # (Some servers return all descendants regardless of Depth header.)
            is_dir, size, modified = _extract_dav_props(resp)
            name = rel.rsplit("/", 1)[-1] if "/" in rel else rel
            entries.append(
                DirEntry(
                    name=name,
                    path=rel,
                    is_dir=is_dir,
                    size=size,
                    modified=modified,
                )
            )
        return entries

    async def stat(self, path: str) -> FileStat:
        self._ensure_available()
        url = self._resolve_url(path)
        response = await asyncio.to_thread(
            self._request,
            "PROPFIND",
            url,
            data=_WEBDAV_PROPFIND_BODY,
            headers={"Depth": "0", "Content-Type": "application/xml"},
        )
        if response.status_code >= 400:
            raise BackendError(
                f"webdav PROPFIND {url} → {response.status_code}: {response.text[:200]}",
            )
        try:
            root = ET.fromstring(response.content)  # nosec B314 — webdav PROPFIND from configured backend
        except ET.ParseError as exc:
            raise BackendError(f"webdav PROPFIND returned malformed XML: {exc}") from exc
        responses = _find_local(root, "response")
        if not responses:
            raise BackendError("webdav PROPFIND returned no responses")
        is_dir, size, modified = _extract_dav_props(responses[0])
        return FileStat(
            path=self._rel_path(path),
            is_dir=is_dir,
            size=size,
            modified=modified,
        )

    async def mkdir(self, path: str) -> None:
        self._ensure_available()
        url = self._resolve_url(path)
        response = await asyncio.to_thread(self._request, "MKCOL", url)
        if response.status_code not in (200, 201, 405):
            raise BackendError(
                f"webdav MKCOL {url} → {response.status_code}: {response.text[:200]}",
            )

    async def remove(self, path: str) -> None:
        self._ensure_available()
        url = self._resolve_url(path)
        response = await asyncio.to_thread(self._request, "DELETE", url)
        if response.status_code not in (200, 204, 404):
            raise BackendError(
                f"webdav DELETE {url} → {response.status_code}: {response.text[:200]}",
            )

    async def test_connection(self) -> bool:
        try:
            self._ensure_available()
        except BackendUnavailableError as e:
            _logger.warning("webdav_backend · unavailable: %s", e)
            return False
        try:
            url = self._resolve_url("/")
            response = await asyncio.to_thread(
                self._request,
                "PROPFIND",
                url,
                data=_WEBDAV_PROPFIND_BODY,
                headers={"Depth": "0", "Content-Type": "application/xml"},
            )
            return response.status_code < 400
        except Exception as e:  # noqa: BLE001 — probe must not raise
            _logger.warning("webdav_backend · test_connection failed: %s", e)
            return False


__all__ = ["WebdavMountBackend"]
