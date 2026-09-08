"""Authorized binary snapshots for workspace originals, separate from uploads."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import secrets
import stat
import tempfile
from dataclasses import replace
from pathlib import Path, PureWindowsPath
from typing import Any
from urllib.parse import quote

from fastapi import HTTPException, Request
from fastapi.responses import Response

from runtime.execution.misc.office_fidelity_preview import render_office_fidelity_preview
from runtime.execution.misc.office_preview import render_office_preview
from runtime.safety.auth.scope import scope_from_principal
from runtime.workspace.crypto import WorkspaceCryptoError

from ._fs_router_helpers import (
    _assert_in_scope,
    _assert_local_request_scope,
    _check_acl,
    _FsContext,
    _parse_workspace_path,
    _remote_backend_for,
    _resolve_remote_workspace,
    _scope_roots,
)

# MountBackend.read_file currently returns an entire byte string. Bound both
# local and remote snapshots; Range selects bytes from that same snapshot.
_MAX_CONTENT_BYTES = 128 * 1024 * 1024
_MIME_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".avif": "image/avif",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".ico": "image/x-icon",
    ".txt": "text/plain",
    ".md": "text/plain",
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".doc": "application/msword",
    ".xls": "application/vnd.ms-excel",
    ".ppt": "application/vnd.ms-powerpoint",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}
_PREVIEW_SUFFIXES = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".csv", ".tsv"}


def _check_size(size: int) -> None:
    if size > _MAX_CONTENT_BYTES:
        raise HTTPException(413, {"error": "file_too_large", "max_bytes": _MAX_CONTENT_BYTES})


def _read_local_snapshot(path: Path) -> bytes:
    """Read one regular file handle, never a later FileResponse path reopen."""
    before = path.lstat()
    if stat.S_ISDIR(before.st_mode):
        raise IsADirectoryError(path)
    if not stat.S_ISREG(before.st_mode) or getattr(before, "st_file_attributes", 0) & 0x400:
        raise PermissionError("file is not a regular file")
    _check_size(before.st_size)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or not os.path.samestat(before, opened):
            raise PermissionError("file changed while opening")
        raw = stream.read(_MAX_CONTENT_BYTES + 1)
        after = os.fstat(stream.fileno())
    _check_size(len(raw))
    current = path.lstat()
    if (
        not os.path.samestat(opened, current)
        or not stat.S_ISREG(current.st_mode)
        or getattr(current, "st_file_attributes", 0) & 0x400
        or (opened.st_size, opened.st_mtime_ns) != (after.st_size, after.st_mtime_ns)
        or len(raw) != after.st_size
    ):
        raise HTTPException(409, {"error": "file_changed", "message": "文件已更新，请重新打开。"})
    return raw


def _remote_context(ctx: _FsContext, request: Request) -> _FsContext:
    principal = getattr(request.state, "principal", None)
    if principal is None or ctx.workspace_store is None:
        return ctx
    # Match the existing workspace API's tenant view, in addition to its
    # member-role check. Never trust a caller-supplied actor or tenant.
    return replace(
        ctx,
        workspace_store=ctx.workspace_store.with_scope(
            scope_from_principal(
                principal,
                allow_cross_tenant=bool(principal.roles.intersection({"admin", "operator"})),
            )
        ),
    )


async def _read_remote(ctx: _FsContext, request: Request, workspace_id: str, path: str) -> bytes:
    if ctx.workspace_store is None:
        raise HTTPException(503, {"error": "workspace_store_not_configured"})
    scoped = _remote_context(ctx, request)
    ws = _resolve_remote_workspace(scoped, workspace_id, request=request)
    if ws is None:
        raise HTTPException(404, {"error": "workspace_not_found"})
    _check_acl(scoped, request, ws.id, write=False)
    backend = _remote_backend_for(scoped, ws)
    if backend is None:
        raise HTTPException(503, {"error": "mount_backend_unavailable"})
    try:
        info = await backend.stat(path)
        if info.is_dir:
            raise FileNotFoundError(path)
        _check_size(info.size)
        raw = await backend.read_file(path)
        if not isinstance(raw, (bytes, bytearray)):
            raise HTTPException(502, {"error": "invalid_backend_content"})
        _check_size(len(raw))
        # A membership change during the remote read must take effect before
        # returning its bytes. This never switches to a local-path fallback.
        _check_acl(scoped, request, ws.id, write=False)
        return bytes(raw)
    except WorkspaceCryptoError as exc:
        raise HTTPException(503, detail=exc.to_detail()) from None
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
        raise HTTPException(404, {"error": "file_not_found"}) from None
    except PermissionError:
        raise HTTPException(403, {"error": "file_read_denied"}) from None
    except HTTPException:
        raise
    except Exception:
        # Backend exception messages may include connection credentials.
        raise HTTPException(503, {"error": "workspace_read_unavailable"}) from None


def _preview(raw: bytes, filename: str, *, fidelity: bool, office: bool) -> Response | None:
    if Path(filename).suffix.lower() not in _PREVIEW_SUFFIXES:
        return None
    # Render the authorized bytes, not an original path that a converter can
    # reopen after the scope check. The temporary original is always removed.
    with tempfile.TemporaryDirectory(prefix="echo-fs-preview-") as directory:
        target = Path(directory) / filename
        target.write_bytes(raw)
        html = render_office_fidelity_preview(target) if fidelity else None
        nonce = None
        headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
        if html is not None:
            headers["X-Echo-Office-Preview"] = "fidelity"
        elif office:
            nonce = secrets.token_urlsafe(18)
            html = render_office_preview(target, script_nonce=nonce)
        if html is None:
            return None
        script = f"script-src 'nonce-{nonce}'; " if nonce else ""
        headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'unsafe-inline'; "
            f"{script}img-src data:; object-src 'none'; base-uri 'none'; form-action 'none'"
        )
        return Response(html, media_type="text/html", headers=headers)


def _content_response(request: Request, raw: bytes, filename: str, *, download: bool) -> Response:
    size = len(raw)
    etag = '"' + hashlib.sha256(raw).hexdigest() + '"'
    mime = _MIME_TYPES.get(Path(filename).suffix.lower(), "application/octet-stream")
    disposition = "attachment" if download or mime == "application/octet-stream" else "inline"
    headers = {
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "sandbox; default-src 'none'; frame-ancestors 'self'",
        "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(filename, safe='')}",
        "Content-Type": mime,
        "Content-Length": str(size),
        "Accept-Ranges": "bytes",
        "ETag": etag,
    }
    status_code = 200
    range_header = request.headers.get("range") if request.method == "GET" else None
    if range_header and request.headers.get("if-range", etag) == etag:
        match = (
            re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
            if len(range_header) < 256
            else None
        )
        valid = match is not None and bool(match[1] or match[2]) and size > 0
        start = end = 0
        if valid and match is not None:
            start = int(match[1]) if match[1] else max(0, size - int(match[2]))
            end = min(size - 1, int(match[2])) if match[1] and match[2] else size - 1
            valid = start <= end and (bool(match[1]) or int(match[2]) > 0)
        if not valid:
            return Response(
                status_code=416,
                headers={
                    **headers,
                    "Content-Range": f"bytes */{size}",
                    "Content-Length": "0",
                },
            )
        raw = raw[start : end + 1]
        status_code = 206
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        headers["Content-Length"] = str(len(raw))
    return Response(
        b"" if request.method == "HEAD" else raw, status_code=status_code, headers=headers
    )


def register_content_endpoint(router: Any, ctx: _FsContext) -> None:
    @router.get("/api/fs/content", operation_id="api_fs_content_get")
    @router.head("/api/fs/content", operation_id="api_fs_content_head")
    async def api_fs_content(
        request: Request,
        path: str,
        thread_id: str | None = None,
        workspace_path: str | None = None,
        workspace_id: str | None = None,
        download: bool = False,
        office_preview: bool = False,
        office_fidelity_preview: bool = False,
    ) -> Response:
        if not path or any(ord(ch) < 32 or ord(ch) == 127 for ch in path):
            raise HTTPException(400, "invalid path")
        prefix_id, rel_path = _parse_workspace_path(path)
        if workspace_id and prefix_id and workspace_id != prefix_id:
            raise HTTPException(400, "workspace_id does not match path")
        selected_workspace = workspace_id or prefix_id
        if selected_workspace:
            raw = await _read_remote(ctx, request, selected_workspace, rel_path)
        else:
            _assert_local_request_scope(
                ctx,
                request,
                thread_id=thread_id,
                workspace_path=workspace_path,
            )
            candidate = Path(path).expanduser()
            if not candidate.is_absolute():
                if PureWindowsPath(path).is_absolute():
                    raise HTTPException(400, "path belongs to a different operating system")
                roots = _scope_roots(ctx, thread_id=thread_id, workspace_path=workspace_path)
                if not roots:
                    raise HTTPException(400, "relative path requires a workspace scope")
                candidate = (
                    Path(workspace_path).expanduser() if workspace_path else roots[0]
                ) / candidate
            target = _assert_in_scope(
                ctx,
                candidate,
                thread_id=thread_id,
                workspace_path=workspace_path,
            )
            try:
                raw = await asyncio.to_thread(_read_local_snapshot, target)
            except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
                raise HTTPException(404, {"error": "file_not_found"}) from None
            except PermissionError:
                raise HTTPException(403, {"error": "file_read_denied"}) from None
            except OSError:
                raise HTTPException(503, {"error": "file_read_unavailable"}) from None
            _assert_local_request_scope(
                ctx,
                request,
                thread_id=thread_id,
                workspace_path=workspace_path,
            )
            if (
                _assert_in_scope(
                    ctx,
                    candidate,
                    thread_id=thread_id,
                    workspace_path=workspace_path,
                )
                != target
            ):
                raise HTTPException(409, {"error": "file_changed"})
        filename = rel_path.replace("\\", "/").rsplit("/", 1)[-1] or "download"
        if (
            not download
            and (office_preview or office_fidelity_preview)
            and request.method != "HEAD"
        ):
            preview = await asyncio.to_thread(
                _preview,
                raw,
                filename,
                fidelity=office_fidelity_preview,
                office=office_preview,
            )
            # Conversion can take longer than the binary read itself.
            # Recheck both the HTML and the unsupported-preview/raw fallback.
            if selected_workspace:
                _check_acl(
                    _remote_context(ctx, request),
                    request,
                    selected_workspace,
                    write=False,
                )
            else:
                _assert_local_request_scope(
                    ctx,
                    request,
                    thread_id=thread_id,
                    workspace_path=workspace_path,
                )
                _assert_in_scope(
                    ctx,
                    candidate,
                    thread_id=thread_id,
                    workspace_path=workspace_path,
                )
            if preview is not None:
                return preview
        return _content_response(request, raw, filename, download=download)
