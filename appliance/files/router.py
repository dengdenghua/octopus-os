"""NAS 文件管理器 HTTP API。

GET  /api/appliance/files/list?path=<rel>     列目录
POST /api/appliance/files/upload              上传文件(流式临时文件→原子落盘)
POST /api/appliance/files/upload/preflight    上传容量/大小预检
POST /api/appliance/files/upload/sessions     创建可恢复分块上传
PUT  /api/appliance/files/upload/sessions/:id/chunk  顺序追加分块
POST /api/appliance/files/upload/sessions/:id/complete 校验并原子提交
DELETE /api/appliance/files/upload/sessions/:id 取消并清理临时数据
GET  /api/appliance/files/download?path=<rel> 下载文件(支持 Range)
POST /api/appliance/files/mkdir               新建目录
POST /api/appliance/files/move                移动/重命名
POST /api/appliance/files/copy                复制文件/目录(默认不覆盖)
POST /api/appliance/files/trash               删除 → 移入回收站(非物理删除)
GET  /api/appliance/files/trash               列回收站
POST /api/appliance/files/trash/restore       从回收站恢复
POST /api/appliance/files/trash/empty         清空回收站(唯一物理删除路径)

全部需登录(与启动器同一 JWT)。路径越权(..)返回 400。
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse

from appliance.approval import (
    HighRiskApprovalService,
    consume_request_approval,
    request_intent_id,
)
from appliance.audit import ApplianceAudit, AuditIntegrityError
from appliance.data_access import (
    DataAccessDenied,
    DataAccessPolicy,
    DataAccessScope,
    DataAccessUnavailable,
)
from appliance.files.manager import (
    DEFAULT_UPLOAD_CHUNK_BYTES,
    FileManager,
    InsufficientStorage,
    PathEscape,
    ShareQuotaExceeded,
    UploadHashMismatch,
    UploadOffsetMismatch,
    UploadSessionLimit,
    UploadTooLarge,
)
from appliance.security import ApplianceAuthenticator, resolve_authenticator


class _MkdirBody(BaseModel):
    path: str


class _MoveBody(BaseModel):
    src: str
    dst: str


class _CopyBody(BaseModel):
    src: str
    dst: str


class _PathBody(BaseModel):
    path: str


class _IdBody(BaseModel):
    id: str


class _UploadPreflightBody(BaseModel):
    path: str = ""
    filename: str
    size: int
    overwrite: bool = False


class _UploadSessionBody(_UploadPreflightBody):
    sha256: str | None = None
    fingerprint: str | None = None


_SHA256_PATTERN = re.compile(r"[0-9a-fA-F]{64}")


def create_files_router(
    manager: FileManager,
    jwt_secret: str | None = None,
    approval: HighRiskApprovalService | None = None,
    audit: ApplianceAudit | None = None,
    authenticator: ApplianceAuthenticator | None = None,
    data_access: DataAccessPolicy | None = None,
) -> APIRouter:
    auth = resolve_authenticator(jwt_secret=jwt_secret, authenticator=authenticator)
    require_auth = auth.dependency()
    router = APIRouter(
        prefix="/api/appliance/files",
        tags=["appliance", "files"],
        dependencies=[Depends(require_auth)],
    )

    def _guard(fn, *args):
        try:
            return fn(*args)
        except PathEscape as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"not found: {exc}") from exc
        except FileExistsError as exc:
            raise HTTPException(status_code=409, detail=f"exists: {exc}") from exc
        except UploadTooLarge as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except UploadOffsetMismatch as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": str(exc),
                    "uploadedBytes": exc.expected_offset,
                },
            ) from exc
        except UploadHashMismatch as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except UploadSessionLimit as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        except ShareQuotaExceeded as exc:
            raise HTTPException(
                status_code=507,
                detail={"message": str(exc), **exc.report},
            ) from exc
        except InsufficientStorage as exc:
            raise HTTPException(status_code=507, detail=str(exc)) from exc
        except (NotADirectoryError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    def _actor(request: Request) -> str:
        actor = getattr(request.state, "appliance_actor", "")
        return str(actor or "local:development")[:256]

    async def _scope(actor: str = Depends(require_auth)) -> DataAccessScope:
        if data_access is None:
            return DataAccessScope.unrestricted(actor)
        try:
            return await run_in_threadpool(data_access.scope_for_actor, actor)
        except DataAccessDenied as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except DataAccessUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    scope_dependency = Depends(_scope)

    def _target_path(directory: str, filename: str) -> str:
        return "/".join(part for part in (directory.strip("/"), filename) if part)

    def _authorize(scope: DataAccessScope, mode: str, path: str = "") -> None:
        try:
            if mode == "list":
                scope.require_list(path)
            elif mode == "read":
                scope.require_read(path)
            elif mode == "write":
                scope.require_write(path)
            elif mode == "operator":
                scope.require_operator()
            else:  # pragma: no cover - internal programming guard
                raise RuntimeError("unknown data authorization mode")
        except DataAccessDenied as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    def _record(
        request: Request,
        *,
        action: str,
        target: str,
        outcome: str,
        metadata: dict | None = None,
    ) -> None:
        if audit is None:
            if auth.required:
                raise HTTPException(status_code=503, detail="appliance audit unavailable")
            return
        audit_metadata = dict(metadata or {})
        intent_id = request_intent_id(request)
        if intent_id:
            audit_metadata["intentId"] = intent_id
        try:
            audit.record(
                actor=_actor(request),
                action=action,
                target=target,
                outcome=outcome,
                metadata=audit_metadata,
            )
        except (OSError, AuditIntegrityError) as exc:
            raise HTTPException(status_code=503, detail="appliance audit unavailable") from exc

    def _failure(request: Request, *, action: str, target: str, exc: Exception) -> None:
        metadata: dict[str, object] = {"errorType": type(exc).__name__}
        if isinstance(exc, HTTPException):
            metadata["statusCode"] = exc.status_code
        _record(
            request,
            action=action,
            target=target,
            outcome="failed",
            metadata=metadata,
        )

    @router.get("/list")
    async def list_dir(
        scope: DataAccessScope = scope_dependency,
        path: str = "",
    ) -> dict:
        _authorize(scope, "list", path)
        entries = await run_in_threadpool(_guard, manager.list_dir, path)
        return {
            "path": path,
            "entries": [e.to_dict() for e in entries if scope.visible(e.path)],
        }

    @router.get("/usage")
    async def storage_usage(
        scope: DataAccessScope = scope_dependency,
        fresh: bool = False,
    ) -> dict:
        return await run_in_threadpool(
            manager.storage_usage,
            fresh=fresh,
            path_visible=None if scope.operator else scope.visible,
        )

    @router.post("/upload/preflight")
    async def upload_preflight(
        body: _UploadPreflightBody,
        scope: DataAccessScope = scope_dependency,
    ) -> dict:
        _authorize(scope, "write", _target_path(body.path, body.filename))
        report = await run_in_threadpool(
            _guard,
            manager.preflight_upload,
            body.path,
            body.filename,
            body.size,
            body.overwrite,
        )
        return {"ok": True, **report}

    @router.post("/upload")
    async def upload(
        request: Request,
        file: Annotated[UploadFile, File()],
        scope: DataAccessScope = scope_dependency,
        path: Annotated[str, Form()] = "",
        overwrite: Annotated[bool, Form()] = False,
        size: Annotated[int | None, Form()] = None,
        sha256: Annotated[str | None, Form()] = None,
    ) -> dict:
        filename = file.filename or ""
        target_label = _target_path(path, filename)
        _authorize(scope, "write", target_label)
        action = "files.upload"
        _record(request, action=action, target=target_label, outcome="attempted")
        temp = None
        committed = False
        written = 0
        digest = hashlib.sha256()
        try:
            expected_digest = (sha256 or "").strip().lower()
            if expected_digest and _SHA256_PATTERN.fullmatch(expected_digest) is None:
                raise HTTPException(status_code=400, detail="invalid expected SHA-256")
            temp, target = await run_in_threadpool(
                _guard,
                manager.prepare_upload,
                path,
                filename,
                overwrite,
                size,
            )
            with temp.open("xb") as output:
                while chunk := await file.read(1024 * 1024):
                    await run_in_threadpool(
                        _guard,
                        manager.assert_upload_chunk,
                        temp,
                        written,
                        len(chunk),
                    )
                    await run_in_threadpool(output.write, chunk)
                    digest.update(chunk)
                    written += len(chunk)
                await run_in_threadpool(output.flush)
                await run_in_threadpool(os.fsync, output.fileno())
            if size is not None and written != size:
                raise HTTPException(
                    status_code=400, detail="upload size does not match declaration"
                )
            actual_digest = digest.hexdigest()
            if expected_digest and actual_digest != expected_digest:
                raise HTTPException(status_code=422, detail="upload SHA-256 does not match")
            entry = await run_in_threadpool(
                _guard,
                manager.finalize_upload,
                temp,
                target,
                overwrite,
            )
            committed = True
        except HTTPException as exc:
            _failure(request, action=action, target=target_label, exc=exc)
            raise
        except OSError as exc:
            mapped = HTTPException(status_code=507, detail="upload could not be stored")
            _failure(request, action=action, target=target_label, exc=mapped)
            raise mapped from exc
        finally:
            if temp is not None and not committed:
                with contextlib.suppress(OSError, PathEscape):
                    await run_in_threadpool(manager.discard_upload, temp)
            await file.close()
        _record(
            request,
            action=action,
            target=entry.path,
            outcome="succeeded",
            metadata={
                "size": entry.size,
                "overwrite": overwrite,
                "hashAlgorithm": "sha256",
                "hashVerified": bool(expected_digest),
            },
        )
        return {
            "ok": True,
            "entry": entry.to_dict(),
            "sha256": actual_digest,
            "hashVerified": bool(expected_digest),
        }

    @router.post("/upload/sessions")
    async def create_upload_session(
        body: _UploadSessionBody,
        request: Request,
        scope: DataAccessScope = scope_dependency,
    ) -> dict:
        target_label = _target_path(body.path, body.filename)
        _authorize(scope, "write", target_label)
        action = "files.upload.session.create"
        _record(request, action=action, target=target_label, outcome="attempted")
        try:
            session = await run_in_threadpool(
                _guard,
                manager.create_upload_session,
                body.path,
                body.filename,
                body.size,
                body.sha256,
                body.overwrite,
                body.fingerprint,
            )
        except Exception as exc:
            _failure(request, action=action, target=target_label, exc=exc)
            raise
        _record(
            request,
            action=action,
            target=target_label,
            outcome="succeeded",
            metadata={
                "sessionId": session["sessionId"],
                "size": body.size,
                "overwrite": body.overwrite,
                "hashExpected": bool(body.sha256),
            },
        )
        return {"ok": True, **session}

    @router.get("/upload/sessions/{session_id}")
    async def get_upload_session(
        session_id: str,
        scope: DataAccessScope = scope_dependency,
    ) -> dict:
        session = await run_in_threadpool(_guard, manager.get_upload_session, session_id)
        _authorize(scope, "write", str(session["target"]))
        return {"ok": True, **session}

    @router.put("/upload/sessions/{session_id}/chunk")
    async def append_upload_session_chunk(
        session_id: str,
        request: Request,
        scope: DataAccessScope = scope_dependency,
    ) -> dict:
        action = "files.upload.session.chunk"
        target_label = f"session:{session_id}"
        try:
            existing = await run_in_threadpool(_guard, manager.get_upload_session, session_id)
            _authorize(scope, "write", str(existing["target"]))
            raw_offset = request.headers.get("upload-offset", "")
            chunk_digest = request.headers.get("upload-chunk-sha256", "").strip().lower()
            if _SHA256_PATTERN.fullmatch(chunk_digest) is None:
                raise HTTPException(status_code=400, detail="invalid Upload-Chunk-SHA256")
            try:
                offset = int(raw_offset)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="invalid Upload-Offset") from exc
            if offset < 0:
                raise HTTPException(status_code=400, detail="invalid Upload-Offset")
            declared_length = request.headers.get("content-length")
            if declared_length:
                try:
                    if int(declared_length) > DEFAULT_UPLOAD_CHUNK_BYTES:
                        raise HTTPException(status_code=413, detail="upload chunk is too large")
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail="invalid Content-Length") from exc
            chunk = bytearray()
            async for part in request.stream():
                chunk.extend(part)
                if len(chunk) > DEFAULT_UPLOAD_CHUNK_BYTES:
                    raise HTTPException(status_code=413, detail="upload chunk is too large")
            if hashlib.sha256(chunk).hexdigest() != chunk_digest:
                raise HTTPException(status_code=422, detail="upload chunk SHA-256 does not match")
            session = await run_in_threadpool(
                _guard,
                manager.append_upload_session_chunk,
                session_id,
                offset,
                bytes(chunk),
            )
        except Exception as exc:
            _failure(request, action=action, target=target_label, exc=exc)
            raise
        return {"ok": True, **session}

    @router.post("/upload/sessions/{session_id}/complete")
    async def complete_upload_session(
        session_id: str,
        request: Request,
        scope: DataAccessScope = scope_dependency,
    ) -> dict:
        action = "files.upload.session.complete"
        target_label = f"session:{session_id}"
        _record(request, action=action, target=target_label, outcome="attempted")
        try:
            existing = await run_in_threadpool(_guard, manager.get_upload_session, session_id)
            _authorize(scope, "write", str(existing["target"]))
            entry, digest, hash_verified = await run_in_threadpool(
                _guard,
                manager.complete_upload_session,
                session_id,
            )
        except Exception as exc:
            _failure(request, action=action, target=target_label, exc=exc)
            raise
        _record(
            request,
            action=action,
            target=entry.path,
            outcome="succeeded",
            metadata={
                "sessionId": session_id,
                "size": entry.size,
                "hashAlgorithm": "sha256",
                "hashVerified": hash_verified,
            },
        )
        return {
            "ok": True,
            "entry": entry.to_dict(),
            "sha256": digest,
            "hashVerified": hash_verified,
        }

    @router.delete("/upload/sessions/{session_id}")
    async def cancel_upload_session(
        session_id: str,
        request: Request,
        scope: DataAccessScope = scope_dependency,
    ) -> dict:
        action = "files.upload.session.cancel"
        target_label = f"session:{session_id}"
        _record(request, action=action, target=target_label, outcome="attempted")
        try:
            existing = await run_in_threadpool(_guard, manager.get_upload_session, session_id)
            _authorize(scope, "write", str(existing["target"]))
            result = await run_in_threadpool(
                _guard,
                manager.cancel_upload_session,
                session_id,
            )
        except Exception as exc:
            _failure(request, action=action, target=target_label, exc=exc)
            raise
        _record(request, action=action, target=target_label, outcome="succeeded")
        return {"ok": True, **result}

    @router.get("/download", response_class=FileResponse)
    async def download(
        path: str,
        scope: DataAccessScope = scope_dependency,
    ) -> FileResponse:
        _authorize(scope, "read", path)
        target = await run_in_threadpool(_guard, manager.file_for_download, path)
        return FileResponse(target, filename=target.name)

    @router.post("/mkdir")
    async def mkdir(
        body: _MkdirBody,
        request: Request,
        scope: DataAccessScope = scope_dependency,
    ) -> dict:
        _authorize(scope, "write", body.path)
        action = "files.mkdir"
        _record(request, action=action, target=body.path, outcome="attempted")
        try:
            entry = await run_in_threadpool(_guard, manager.mkdir, body.path)
        except Exception as exc:
            _failure(request, action=action, target=body.path, exc=exc)
            raise
        _record(request, action=action, target=entry.path, outcome="succeeded")
        return {"ok": True, "entry": entry.to_dict()}

    @router.post("/move")
    async def move(
        body: _MoveBody,
        request: Request,
        scope: DataAccessScope = scope_dependency,
    ) -> dict:
        _authorize(scope, "write", body.src)
        _authorize(scope, "write", body.dst)
        action = "files.move"
        target = f"{body.src} -> {body.dst}"
        _record(request, action=action, target=target, outcome="attempted")
        try:
            entry = await run_in_threadpool(_guard, manager.move, body.src, body.dst)
        except Exception as exc:
            _failure(request, action=action, target=target, exc=exc)
            raise
        _record(request, action=action, target=target, outcome="succeeded")
        return {"ok": True, "entry": entry.to_dict()}

    @router.post("/copy")
    async def copy(
        body: _CopyBody,
        request: Request,
        scope: DataAccessScope = scope_dependency,
    ) -> dict:
        _authorize(scope, "read", body.src)
        _authorize(scope, "write", body.dst)
        action = "files.copy"
        target = f"{body.src} -> {body.dst}"
        _record(request, action=action, target=target, outcome="attempted")
        try:
            entry = await run_in_threadpool(_guard, manager.copy, body.src, body.dst)
        except Exception as exc:
            _failure(request, action=action, target=target, exc=exc)
            raise
        _record(request, action=action, target=target, outcome="succeeded")
        return {"ok": True, "entry": entry.to_dict()}

    @router.post("/trash")
    async def trash(
        body: _PathBody,
        request: Request,
        scope: DataAccessScope = scope_dependency,
    ) -> dict:
        _authorize(scope, "write", body.path)
        action = "files.trash"
        _record(request, action=action, target=body.path, outcome="attempted")
        try:
            record = await run_in_threadpool(_guard, manager.trash, body.path)
        except Exception as exc:
            _failure(request, action=action, target=body.path, exc=exc)
            raise
        _record(request, action=action, target=body.path, outcome="succeeded")
        return {"ok": True, "trashed": record}

    @router.get("/trash")
    async def list_trash(
        scope: DataAccessScope = scope_dependency,
    ) -> dict:
        entries = await run_in_threadpool(manager.list_trash)
        return {"entries": [entry for entry in entries if scope.can_read(str(entry["original"]))]}

    @router.post("/trash/restore")
    async def restore(
        body: _IdBody,
        request: Request,
        scope: DataAccessScope = scope_dependency,
    ) -> dict:
        action = "files.trash.restore"
        _record(request, action=action, target=body.id, outcome="attempted")
        try:
            entries = await run_in_threadpool(manager.list_trash)
            record = next((entry for entry in entries if entry["id"] == body.id), None)
            if record is None or not scope.can_write(str(record["original"])):
                raise HTTPException(status_code=404, detail="trash entry not found")
            entry = await run_in_threadpool(_guard, manager.restore, body.id)
        except Exception as exc:
            _failure(request, action=action, target=body.id, exc=exc)
            raise
        _record(
            request,
            action=action,
            target=body.id,
            outcome="succeeded",
            metadata={"restoredPath": entry.path},
        )
        return {"ok": True, "entry": entry.to_dict()}

    @router.post("/trash/empty")
    async def empty_trash(
        request: Request,
        scope: DataAccessScope = scope_dependency,
    ) -> dict:
        _authorize(scope, "operator")
        actor = _actor(request)
        action = "files.trash.empty"
        target = "recycle-bin"
        if approval is None:
            if auth.required:
                raise HTTPException(status_code=503, detail="high-risk approval unavailable")
        else:
            consume_request_approval(
                request,
                approval,
                actor=actor,
                action=action,
                target=target,
            )
        _record(request, action=action, target=target, outcome="attempted")
        try:
            count = await run_in_threadpool(manager.empty_trash)
        except Exception as exc:
            _failure(request, action=action, target=target, exc=exc)
            raise
        _record(
            request,
            action=action,
            target=target,
            outcome="succeeded",
            metadata={"emptied": count},
        )
        return {"ok": True, "emptied": count}

    return router
