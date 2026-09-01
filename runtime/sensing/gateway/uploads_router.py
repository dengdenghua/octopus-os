"""
Thread uploads / artifacts router.

Extracted from ``runtime/platform/ui/app.py`` as part of the
app.py-split campaign. Owns the 4 endpoints the UI's message
composer + artifact viewer use:

    POST   /api/threads/{tid}/uploads                     · multipart upload
    GET    /api/threads/{tid}/uploads/list                · list stored files
    DELETE /api/threads/{tid}/uploads/{filename}          · remove one
    GET    /api/threads/{tid}/artifacts/{artifact:path}   · serve content

The router accepts a ``thread_store`` + ``upload_root`` at
construction. Both can be ``None`` · in that case every endpoint
returns 503 rather than AttributeError, matching the pre-split
"demo app without a full stack still boots" contract.

Filename sanitization
---------------------

Uploaded filenames are reduced to their basename before being
joined to the thread's upload dir · otherwise a client could
send ``../../etc/passwd`` and land a file outside their sandbox.
Covered by ``test_filename_path_traversal_stripped``.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import secrets
from pathlib import Path
from typing import Any

try:
    from fastapi import APIRouter, File, HTTPException, Request, UploadFile
    from fastapi.responses import FileResponse, HTMLResponse
    from pydantic import BaseModel

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    APIRouter = None  # type: ignore[assignment, misc]
    File = None  # type: ignore[assignment, misc]
    HTTPException = None  # type: ignore[assignment, misc]
    Request = None  # type: ignore[assignment, misc]
    UploadFile = None  # type: ignore[assignment, misc]
    FileResponse = None  # type: ignore[assignment, misc]
    HTMLResponse = None  # type: ignore[assignment, misc]
    BaseModel = object  # type: ignore[assignment, misc]

from runtime.execution.misc.document_text_extractor import extract_text_from_upload
from runtime.execution.misc.office_fidelity_preview import render_office_fidelity_preview
from runtime.execution.misc.office_preview import render_office_preview
from runtime.platform.process.paths import app_paths
from runtime.platform.runtime_policy.workspaces import WorkspaceManager
from runtime.sensing._fastapi_guard import require_fastapi

MAX_UPLOAD_FILES = 20
MAX_UPLOAD_FILE_BYTES = 50 * 1024 * 1024
_UPLOAD_READ_CHUNK_BYTES = 1024 * 1024
_MAX_UPLOAD_FILENAME_LENGTH = 255

# ═══════════════════════════════════════════════════════════
# Response models
# ═══════════════════════════════════════════════════════════


if FASTAPI_AVAILABLE:

    class UploadFileMetadata(BaseModel):
        filename: str
        size: int
        path: str
        virtual_path: str
        artifact_url: str
        extension: str | None = None
        modified: int
        extracted_text: str | None = None

    class UploadPostResponse(BaseModel):
        success: bool
        files: list[UploadFileMetadata]
        message: str

    class UploadsListResponse(BaseModel):
        files: list[UploadFileMetadata]
        count: int

    class UploadDeleteResponse(BaseModel):
        success: bool
        message: str


# ═══════════════════════════════════════════════════════════
# Factory
# ═══════════════════════════════════════════════════════════


def create_uploads_router(
    *,
    thread_store: Any,
    workspace_root: Path | None = None,
    legacy_upload_root: Path | None = None,
    upload_root: Path | None = None,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
) -> Any:
    """Build the FastAPI router.

    Parameters
    ----------
    thread_store :
        ThreadStateStore (for ``ensure_thread``). When ``None``,
        all endpoints return 503 — lets minimal demo apps boot
        without a full stack.
    upload_root :
        Directory under which ``<thread_id>/`` subdirs hold
        uploads. Defaults to ``app_paths().data_dir/thread_uploads`` when
        ``None``. The factory
        captures this value · passing a different path later
        requires a fresh router.
    """
    require_fastapi(__name__)

    router = APIRouter(tags=["uploads"])
    legacy_root = legacy_upload_root or upload_root
    workspace_manager = WorkspaceManager(workspace_root) if workspace_root else None

    def _has_control_chars(value: str) -> bool:
        return any(ord(ch) < 32 or ord(ch) == 127 for ch in value)

    def _safe_upload_filename(filename: str | None) -> str:
        raw = (filename or "upload.bin").replace("\\", "/")
        safe_name = Path(raw).name.strip()
        if (
            not safe_name
            or safe_name in {".", ".."}
            or "/" in safe_name
            or "\\" in safe_name
            or _has_control_chars(safe_name)
            or len(safe_name.encode("utf-8")) > _MAX_UPLOAD_FILENAME_LENGTH
        ):
            raise HTTPException(400, "invalid upload filename")
        return safe_name

    def _legacy_dir_for_root(root: Path, thread_id: str, *, create: bool) -> Path:
        if (
            not thread_id
            or thread_id in {".", ".."}
            or "/" in thread_id
            or "\\" in thread_id
            or _has_control_chars(thread_id)
        ):
            raise HTTPException(400, "invalid thread_id")
        root_resolved = Path(root).expanduser().resolve()
        candidate = (root_resolved / thread_id).resolve(strict=False)
        with contextlib.suppress(ValueError):
            candidate.relative_to(root_resolved)
            if candidate.exists() and candidate.is_symlink():
                raise HTTPException(409, "upload directory is not a real directory")
            if create:
                candidate.mkdir(parents=True, exist_ok=True)
                if candidate.is_symlink() or not candidate.is_dir():
                    raise HTTPException(409, "upload directory is not a real directory")
            return candidate
        raise HTTPException(400, "invalid thread_id")

    def _ensure_real_upload_dir(path: Path) -> Path:
        if path.exists() and path.is_symlink():
            raise HTTPException(409, "upload directory is not a real directory")
        path.mkdir(parents=True, exist_ok=True)
        if path.is_symlink() or not path.is_dir():
            raise HTTPException(409, "upload directory is not a real directory")
        return path

    def _upload_dir(thread_id: str) -> Path:
        if workspace_manager is not None:
            return _ensure_real_upload_dir(workspace_manager.layout(thread_id).upload)
        root = legacy_root or (app_paths().data_dir / "thread_uploads")
        return _legacy_dir_for_root(root, thread_id, create=True)

    def _legacy_upload_dir(thread_id: str) -> Path | None:
        if legacy_root is None:
            return None
        return _legacy_dir_for_root(legacy_root, thread_id, create=False)

    def _upload_dirs_for_read(thread_id: str) -> list[Path]:
        dirs = [_upload_dir(thread_id)]
        legacy = _legacy_upload_dir(thread_id)
        if legacy is not None and legacy not in dirs:
            dirs.append(legacy)
        return dirs

    def _absolute_artifact_candidate(thread_id: str, raw_path: Path) -> Path | None:
        if not raw_path.exists() or raw_path.is_symlink() or not raw_path.is_file():
            return None
        try:
            resolved = raw_path.resolve()
        except OSError:
            return None
        for upload_dir in _upload_dirs_for_read(thread_id):
            with contextlib.suppress(OSError, ValueError):
                resolved.relative_to(upload_dir.resolve())
                return resolved
        return None

    def _safe_file_candidate(path: Path) -> Path | None:
        if not path.exists() or path.is_symlink() or not path.is_file():
            return None
        return path

    def _write_upload_bytes(target: Path, data: bytes) -> None:
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise HTTPException(409, "upload target is not a real file")
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(target, flags, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
        except OSError as exc:
            raise HTTPException(
                500,
                f"failed to store upload: {exc}",
            ) from exc

    async def _read_upload_limited(upload: UploadFile) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = await upload.read(_UPLOAD_READ_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_UPLOAD_FILE_BYTES:
                raise HTTPException(413, "upload file too large")
            chunks.append(chunk)
        return b"".join(chunks)

    def _metadata(
        thread_id: str,
        file_path: Path,
        *,
        extracted_text: str | None = None,
    ) -> dict[str, Any]:
        rel_name = file_path.name
        stat = file_path.stat()
        return {
            "filename": rel_name,
            "size": stat.st_size,
            "path": str(file_path),
            "virtual_path": str(file_path),
            "artifact_url": (f"/api/threads/{thread_id}/artifacts/{rel_name}"),
            "extension": file_path.suffix.lstrip(".") or None,
            "modified": int(stat.st_mtime),
            "extracted_text": extracted_text,
        }

    def _require_store() -> None:
        if thread_store is None:
            raise HTTPException(503, "thread uploads unavailable")

    def _auth(request: Request) -> str | None:
        if require_auth and identity_store is None:
            raise HTTPException(401, "auth required")
        from runtime.safety.auth.principal import resolve_principal

        principal = resolve_principal(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )
        if principal is not None:
            request.state.upload_principal = principal
        return principal.actor_id if principal is not None else None

    def _tenant(request: Request) -> str | None:
        principal = getattr(getattr(request, "state", None), "upload_principal", None)
        return getattr(principal, "tenant_id", None)

    def _require_thread_access(
        request: Request,
        thread_id: str,
        *,
        allow_create: bool = False,
    ) -> str | None:
        actor = _auth(request)
        tenant = _tenant(request)
        if thread_store is None or not hasattr(thread_store, "get"):
            return actor
        thread = thread_store.get(thread_id)
        if thread is None:
            if allow_create and hasattr(thread_store, "ensure_thread"):
                metadata = (
                    {"owner_actor_id": actor, "tenant_id": tenant or ""}
                    if actor is not None
                    else None
                )
                thread_store.ensure_thread(thread_id, metadata=metadata)
                return actor
            if actor is not None:
                raise HTTPException(404, f"thread not found: {thread_id}")
            return actor
        metadata = thread.get("metadata") if isinstance(thread.get("metadata"), dict) else {}
        owner = metadata.get("owner_actor_id") or metadata.get("actor_id")
        if actor is not None:
            # Legacy threads without an owner cannot be safely attributed to
            # a tenant.  Do not let the first authenticated caller claim or
            # read them; they need an explicit migration/admin path.
            stored_tenant = str(metadata.get("tenant_id") or "").strip()
            if tenant and not tenant.startswith("legacy:") and stored_tenant != tenant:
                raise HTTPException(404, f"thread not found: {thread_id}")
            if tenant and stored_tenant and stored_tenant != tenant:
                raise HTTPException(404, f"thread not found: {thread_id}")
            if not owner or owner != actor:
                raise HTTPException(404, f"thread not found: {thread_id}")
        return actor

    @router.post(
        "/api/threads/{thread_id}/uploads",
        response_model=UploadPostResponse,
    )
    async def api_thread_uploads(
        request: Request,
        thread_id: str,
        files: list[UploadFile] = File(...),  # noqa: B008 — FastAPI dependency pattern
    ) -> dict[str, Any]:
        _require_store()
        _require_thread_access(request, thread_id, allow_create=True)
        upload_dir = _upload_dir(thread_id)
        if not files or len(files) > MAX_UPLOAD_FILES:
            raise HTTPException(413, f"upload file count must be 1-{MAX_UPLOAD_FILES}")
        uploaded: list[dict[str, Any]] = []
        for upload in files:
            # Sanitize: strip any path traversal segments. Relying
            # on Path(...).name gives us the basename only · a
            # malicious "../../evil" collapses to "evil".
            safe_name = _safe_upload_filename(upload.filename)
            target = upload_dir / safe_name
            data = await _read_upload_limited(upload)
            await asyncio.to_thread(_write_upload_bytes, target, data)
            ext = target.suffix.lstrip(".") or None
            # OOXML/PDF parsing is CPU/file-format work. Keep it off FastAPI's
            # event loop so one large deck does not stall realtime traffic.
            extracted = await asyncio.to_thread(extract_text_from_upload, data, ext)
            uploaded.append(_metadata(thread_id, target, extracted_text=extracted))
        return {
            "success": True,
            "files": uploaded,
            "message": f"Uploaded {len(uploaded)} file(s)",
        }

    @router.get(
        "/api/threads/{thread_id}/uploads/list",
        response_model=UploadsListResponse,
    )
    def api_thread_uploads_list(request: Request, thread_id: str) -> dict[str, Any]:
        _require_store()
        _require_thread_access(request, thread_id)
        files: list[dict[str, Any]] = []
        seen: set[str] = set()
        for upload_dir in _upload_dirs_for_read(thread_id):
            if not upload_dir.exists() or not upload_dir.is_dir():
                continue
            for path in sorted(upload_dir.iterdir(), key=lambda p: p.name.lower()):
                if path.is_symlink() or not path.is_file() or path.name in seen:
                    continue
                seen.add(path.name)
                files.append(_metadata(thread_id, path))
        return {"files": files, "count": len(files)}

    @router.delete(
        "/api/threads/{thread_id}/uploads/{filename}",
        response_model=UploadDeleteResponse,
    )
    def api_thread_uploads_delete(
        request: Request,
        thread_id: str,
        filename: str,
    ) -> dict[str, Any]:
        _require_store()
        _require_thread_access(request, thread_id)
        # Sanitize the incoming filename the same way we did on
        # upload · don't trust URL path params more than we'd
        # trust a multipart field.
        safe_name = _safe_upload_filename(filename)
        target = next(
            (
                upload_dir / safe_name
                for upload_dir in _upload_dirs_for_read(thread_id)
                if (upload_dir / safe_name).exists()
                and not (upload_dir / safe_name).is_symlink()
                and (upload_dir / safe_name).is_file()
            ),
            None,
        )
        if target is None:
            raise HTTPException(404, f"upload not found: {filename}")
        try:
            target.unlink()
        except OSError as exc:
            raise HTTPException(
                500,
                f"failed to delete upload: {exc}",
            ) from exc
        return {"success": True, "message": f"Deleted {target.name}"}

    @router.get(
        "/api/threads/{thread_id}/artifacts/{artifact_path:path}",
        # response_model omitted · FileResponse doesn't play with
        # pydantic response models · skip the type mismatch.
    )
    def api_thread_artifact(
        request: Request,
        thread_id: str,
        artifact_path: str,
        download: bool = False,
        office_preview: bool = False,
        office_fidelity_preview: bool = False,
    ) -> Any:
        _require_store()
        _require_thread_access(request, thread_id)
        normalized = Path(artifact_path)
        candidates: list[Path] = []
        if normalized.is_absolute():
            # Legacy clients may have persisted the server-side upload
            # path. Keep that compatibility only when the absolute path
            # still points inside this thread's upload roots.
            absolute = _absolute_artifact_candidate(thread_id, normalized)
            if absolute is not None:
                candidates.append(absolute)
        for upload_dir in _upload_dirs_for_read(thread_id):
            candidates.append(upload_dir / _safe_upload_filename(artifact_path))
        target = next(
            (c for c in candidates if _safe_file_candidate(c) is not None),
            None,
        )
        if target is None:
            raise HTTPException(
                404,
                f"artifact not found: {artifact_path}",
            )
        if office_fidelity_preview:
            fidelity_html = render_office_fidelity_preview(target)
            if fidelity_html is not None:
                return HTMLResponse(
                    fidelity_html,
                    headers={
                        "Cache-Control": "no-store",
                        "Content-Security-Policy": (
                            "default-src 'none'; style-src 'unsafe-inline'; "
                            "img-src data:; object-src 'none'; base-uri 'none'; "
                            "form-action 'none'"
                        ),
                        "X-Echo-Office-Preview": "fidelity",
                        "X-Content-Type-Options": "nosniff",
                    },
                )
        preview_nonce = secrets.token_urlsafe(18) if office_preview else None
        preview_html = (
            render_office_preview(target, script_nonce=preview_nonce) if office_preview else None
        )
        if preview_html is not None and preview_nonce is not None:
            return HTMLResponse(
                preview_html,
                headers={
                    "Cache-Control": "no-store",
                    "Content-Security-Policy": (
                        "default-src 'none'; style-src 'unsafe-inline'; "
                        f"script-src 'nonce-{preview_nonce}'; "
                        "img-src data:; base-uri 'none'; form-action 'none'"
                    ),
                    "X-Content-Type-Options": "nosniff",
                },
            )
        return FileResponse(
            str(target),
            filename=target.name if download else None,
        )

    return router


__all__ = ["create_uploads_router"]
