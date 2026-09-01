"""Workspace manifest API.

This router exposes the per-thread workspace contract used by realtime
code/agent turns. The layout itself lives in ``runtime.platform.runtime_policy.workspaces``;
the API is deliberately read-light and creates the standard directory
structure on first access.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any

try:
    from fastapi import APIRouter, HTTPException, Query, Request
    from fastapi.responses import FileResponse, HTMLResponse
    from pydantic import BaseModel

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    APIRouter = None  # type: ignore[assignment, misc]
    HTTPException = None  # type: ignore[assignment, misc]
    Query = None  # type: ignore[assignment, misc]
    Request = None  # type: ignore[assignment, misc]
    FileResponse = None  # type: ignore[assignment, misc]
    HTMLResponse = None  # type: ignore[assignment, misc]
    BaseModel = object  # type: ignore[assignment, misc]

from runtime.execution.misc.office_fidelity_preview import render_office_fidelity_preview
from runtime.execution.misc.office_preview import render_office_preview
from runtime.platform.runtime_policy.workspaces import WorkspaceManager
from runtime.sensing._fastapi_guard import require_fastapi

if FASTAPI_AVAILABLE:

    class WorkspaceDirEntry(BaseModel):
        key: str
        path: str
        exists: bool

    class WorkspaceInfoResponse(BaseModel):
        thread_id: str
        root: str
        paths: dict[str, str]
        dirs: list[WorkspaceDirEntry]
        manifest: dict[str, Any]

    class WorkspaceOutputEntry(BaseModel):
        name: str
        area: str
        relative_path: str
        path: str
        size: int
        modified: int
        download_url: str

    class WorkspaceOutputsResponse(BaseModel):
        thread_id: str
        area: str
        files: list[WorkspaceOutputEntry]
        count: int

    class WorkspaceOutputWriteRequest(BaseModel):
        content: str
        expected_sha256: str | None = None

    class WorkspaceOutputRestoreRequest(BaseModel):
        revision_id: str
        expected_sha256: str

    class WorkspaceOutputWriteResponse(BaseModel):
        success: bool
        path: str
        bytes: int
        sha256: str
        revision_id: str | None = None


def create_workspaces_router(
    *,
    workspace_root: Path | str,
    thread_store: Any = None,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
) -> Any:
    require_fastapi(__name__)

    manager = WorkspaceManager(Path(workspace_root))
    router = APIRouter(tags=["workspaces"])

    def _auth(request: Request) -> str | None:
        if require_auth and identity_store is None:
            raise HTTPException(401, "auth required")
        from runtime.sensing.gateway.openai_gateway_router import _resolve_actor

        return _resolve_actor(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )

    def _require_thread_access(request: Request, thread_id: str) -> str | None:
        actor = _auth(request)
        if thread_store is None or not hasattr(thread_store, "get"):
            return actor
        thread = thread_store.get(thread_id)
        if thread is None:
            if actor is not None:
                raise HTTPException(404, f"thread not found: {thread_id}")
            return actor
        metadata = thread.get("metadata") or {}
        owner = metadata.get("owner_actor_id")
        if actor is not None and owner and owner != actor:
            raise HTTPException(404, f"thread not found: {thread_id}")
        return actor

    def _info(thread_id: str) -> dict[str, Any]:
        if not thread_id.strip():
            raise HTTPException(400, "thread_id is required")
        layout = manager.layout(thread_id)
        paths = layout.as_dict()
        dir_keys = ("upload", "output", "stages", "final", "deploy", "skills")
        return {
            "thread_id": thread_id,
            "root": str(layout.root),
            "paths": paths,
            "dirs": [
                {
                    "key": key,
                    "path": paths[key],
                    "exists": Path(paths[key]).is_dir(),
                }
                for key in dir_keys
            ],
            "manifest": manager.manifest(thread_id),
        }

    def _area_root(thread_id: str, area: str) -> tuple[str, Path]:
        layout = manager.layout(thread_id)
        normalized = (area or "output").strip().lower()
        roots = {
            "output": layout.output,
            "stages": layout.stages,
            "final": layout.final,
            "deploy": layout.deploy,
            "upload": layout.upload,
        }
        if normalized not in roots:
            raise HTTPException(
                400,
                "area must be one of: output, stages, final, deploy, upload",
            )
        return normalized, roots[normalized]

    def _safe_child(root: Path, rel_path: str) -> Path:
        raw = Path(rel_path)
        if raw.is_absolute() or any(part in {"", ".", ".."} for part in raw.parts):
            raise HTTPException(400, "invalid relative path")
        try:
            target = (root / raw).resolve()
            target.relative_to(root.resolve())
        except (OSError, RuntimeError, ValueError) as exc:
            raise HTTPException(400, "invalid relative path") from exc
        return target

    def _revision_dir(thread_id: str, area: str, artifact_path: str) -> Path:
        area_key, _ = _area_root(thread_id, area)
        root = manager.layout(thread_id).root / ".artifact-revisions" / area_key
        return _safe_child(root, artifact_path)

    def _store_revision(
        thread_id: str,
        area: str,
        artifact_path: str,
        content: bytes,
    ) -> str:
        revision_dir = _revision_dir(thread_id, area, artifact_path)
        revision_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(content).hexdigest()
        revision_id = f"{time.time_ns()}-{digest[:12]}.bak"
        revision = _safe_child(revision_dir, revision_id)
        try:
            revision.write_bytes(content)
            os.chmod(revision, 0o600)
        except OSError as exc:
            revision.unlink(missing_ok=True)
            raise HTTPException(500, f"failed to preserve output revision: {exc}") from exc
        # Keep a bounded local history without ever making a successful save
        # depend on cleanup of an old snapshot.
        try:
            history = sorted(
                (path for path in revision_dir.iterdir() if path.is_file()),
                key=lambda path: path.name,
                reverse=True,
            )
            for stale in history[20:]:
                stale.unlink(missing_ok=True)
        except OSError:
            pass
        return revision_id

    def _expected_digest(current: bytes, expected_sha256: str | None) -> str:
        if expected_sha256 is None:
            raise HTTPException(400, "expected_sha256 is required for visual editing")
        expected = expected_sha256.strip().lower()
        if len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected):
            raise HTTPException(400, "expected_sha256 must be a 64-character hex digest")
        observed = hashlib.sha256(current).hexdigest()
        if not hmac.compare_digest(observed, expected):
            raise HTTPException(
                409,
                {
                    "error": "file_changed",
                    "message": "文件已被 Agent 或其他编辑更新，请重新加载后再保存。",
                    "observed_sha256": observed,
                },
            )
        return observed

    def _output_entries(thread_id: str, area: str, limit: int) -> dict[str, Any]:
        area_key, root = _area_root(thread_id, area)
        files: list[dict[str, Any]] = []
        if root.exists():
            for path in sorted(root.rglob("*"), key=lambda p: str(p).lower()):
                if len(files) >= limit:
                    break
                if not path.is_file():
                    continue
                rel = path.relative_to(root).as_posix()
                suffix = "" if area_key == "output" else f"?area={area_key}"
                files.append(
                    {
                        "name": path.name,
                        "area": area_key,
                        "relative_path": rel,
                        "path": str(path),
                        "size": path.stat().st_size,
                        "modified": int(path.stat().st_mtime),
                        "download_url": (f"/api/workspaces/{thread_id}/outputs/{rel}{suffix}"),
                    }
                )
        return {
            "thread_id": thread_id,
            "area": area_key,
            "files": files,
            "count": len(files),
        }

    @router.get(
        "/api/workspaces/{thread_id}",
        response_model=WorkspaceInfoResponse,
    )
    def api_workspace_info(request: Request, thread_id: str) -> dict[str, Any]:
        _require_thread_access(request, thread_id)
        return _info(thread_id)

    @router.get(
        "/api/threads/{thread_id}/workspace",
        response_model=WorkspaceInfoResponse,
    )
    def api_thread_workspace_info(request: Request, thread_id: str) -> dict[str, Any]:
        _require_thread_access(request, thread_id)
        return _info(thread_id)

    @router.get(
        "/api/workspaces/{thread_id}/outputs",
        response_model=WorkspaceOutputsResponse,
    )
    def api_workspace_outputs(
        request: Request,
        thread_id: str,
        area: str = "output",
        limit: int = Query(500, ge=1, le=2000),  # noqa: B008
    ) -> dict[str, Any]:
        _require_thread_access(request, thread_id)
        return _output_entries(thread_id, area, limit)

    @router.get(
        "/api/threads/{thread_id}/outputs",
        response_model=WorkspaceOutputsResponse,
    )
    def api_thread_workspace_outputs(
        request: Request,
        thread_id: str,
        area: str = "output",
        limit: int = Query(500, ge=1, le=2000),  # noqa: B008
    ) -> dict[str, Any]:
        _require_thread_access(request, thread_id)
        return _output_entries(thread_id, area, limit)

    @router.get("/api/workspaces/{thread_id}/outputs/{artifact_path:path}")
    def api_workspace_output_file(
        request: Request,
        thread_id: str,
        artifact_path: str,
        area: str = "output",
        download: bool = False,
        office_preview: bool = False,
        office_fidelity_preview: bool = False,
    ) -> Any:
        _require_thread_access(request, thread_id)
        _, root = _area_root(thread_id, area)
        target = _safe_child(root, artifact_path)
        if not target.is_file():
            raise HTTPException(404, f"output not found: {artifact_path}")
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
        return FileResponse(str(target), filename=target.name if download else None)

    @router.get("/api/threads/{thread_id}/outputs/{artifact_path:path}")
    def api_thread_workspace_output_file(
        request: Request,
        thread_id: str,
        artifact_path: str,
        area: str = "output",
        download: bool = False,
        office_preview: bool = False,
        office_fidelity_preview: bool = False,
    ) -> Any:
        return api_workspace_output_file(
            request,
            thread_id,
            artifact_path,
            area=area,
            download=download,
            office_preview=office_preview,
            office_fidelity_preview=office_fidelity_preview,
        )

    def _write_output_file(
        request: Request,
        thread_id: str,
        artifact_path: str,
        body: WorkspaceOutputWriteRequest,
        *,
        area: str,
    ) -> dict[str, Any]:
        _require_thread_access(request, thread_id)
        _, root = _area_root(thread_id, area)
        target = _safe_child(root, artifact_path)
        if not target.is_file():
            raise HTTPException(404, f"output not found: {artifact_path}")
        if target.suffix.lower() not in {".html", ".htm"}:
            raise HTTPException(415, "visual editing currently supports HTML outputs only")
        payload = body.content.encode("utf-8")
        if len(payload) > 8 * 1024 * 1024:
            raise HTTPException(413, "HTML output exceeds the 8 MB visual-edit limit")
        try:
            current = target.read_bytes()
        except OSError as exc:
            raise HTTPException(500, f"failed to read output: {exc}") from exc
        _expected_digest(current, body.expected_sha256)
        revision_id = _store_revision(thread_id, area, artifact_path, current)
        temporary = target.with_name(f".{target.name}.{secrets.token_hex(8)}.tmp")
        try:
            temporary.write_bytes(payload)
            os.chmod(temporary, target.stat().st_mode)
            os.replace(temporary, target)
        except OSError as exc:
            raise HTTPException(500, f"failed to write output: {exc}") from exc
        finally:
            temporary.unlink(missing_ok=True)
        return {
            "success": True,
            "path": str(target),
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "revision_id": revision_id,
        }

    def _restore_output_file(
        request: Request,
        thread_id: str,
        artifact_path: str,
        body: WorkspaceOutputRestoreRequest,
        *,
        area: str,
    ) -> dict[str, Any]:
        _require_thread_access(request, thread_id)
        _, root = _area_root(thread_id, area)
        target = _safe_child(root, artifact_path)
        if not target.is_file():
            raise HTTPException(404, f"output not found: {artifact_path}")
        if target.suffix.lower() not in {".html", ".htm"}:
            raise HTTPException(415, "visual editing currently supports HTML outputs only")
        if not re.fullmatch(r"\d+-[0-9a-f]{12}\.bak", body.revision_id):
            raise HTTPException(400, "invalid revision_id")
        revision = _safe_child(_revision_dir(thread_id, area, artifact_path), body.revision_id)
        if not revision.is_file():
            raise HTTPException(404, "output revision not found")
        try:
            current = target.read_bytes()
            restored = revision.read_bytes()
        except OSError as exc:
            raise HTTPException(500, f"failed to read output revision: {exc}") from exc
        _expected_digest(current, body.expected_sha256)
        redo_revision_id = _store_revision(thread_id, area, artifact_path, current)
        temporary = target.with_name(f".{target.name}.{secrets.token_hex(8)}.tmp")
        try:
            temporary.write_bytes(restored)
            os.chmod(temporary, target.stat().st_mode)
            os.replace(temporary, target)
        except OSError as exc:
            raise HTTPException(500, f"failed to restore output: {exc}") from exc
        finally:
            temporary.unlink(missing_ok=True)
        return {
            "success": True,
            "path": str(target),
            "bytes": len(restored),
            "sha256": hashlib.sha256(restored).hexdigest(),
            "revision_id": redo_revision_id,
        }

    @router.put(
        "/api/workspaces/{thread_id}/outputs/{artifact_path:path}",
        response_model=WorkspaceOutputWriteResponse,
    )
    def api_write_workspace_output_file(
        request: Request,
        thread_id: str,
        artifact_path: str,
        body: WorkspaceOutputWriteRequest,
        area: str = "output",
    ) -> dict[str, Any]:
        return _write_output_file(request, thread_id, artifact_path, body, area=area)

    @router.put(
        "/api/threads/{thread_id}/outputs/{artifact_path:path}",
        response_model=WorkspaceOutputWriteResponse,
    )
    def api_write_thread_workspace_output_file(
        request: Request,
        thread_id: str,
        artifact_path: str,
        body: WorkspaceOutputWriteRequest,
        area: str = "output",
    ) -> dict[str, Any]:
        return _write_output_file(request, thread_id, artifact_path, body, area=area)

    @router.post(
        "/api/threads/{thread_id}/output-revisions/{artifact_path:path}",
        response_model=WorkspaceOutputWriteResponse,
    )
    def api_restore_thread_workspace_output_file(
        request: Request,
        thread_id: str,
        artifact_path: str,
        body: WorkspaceOutputRestoreRequest,
        area: str = "output",
    ) -> dict[str, Any]:
        return _restore_output_file(request, thread_id, artifact_path, body, area=area)

    return router


__all__ = ["create_workspaces_router"]
