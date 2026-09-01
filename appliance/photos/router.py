"""Authenticated, read-only photo library and approval-bound index APIs."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool
from starlette.responses import StreamingResponse

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
from appliance.photos.service import PhotoIndexConflict, PhotoLibraryService, PhotoPathError
from appliance.security import ApplianceAuthenticator, resolve_authenticator

_log = logging.getLogger("echo.appliance.photos")


class PhotoSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=120)
    limit: int = Field(default=24, ge=1, le=50)


class PhotoIndexPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    include_faces: bool = Field(default=False, alias="includeFaces")


class PhotoIndexApplyRequest(PhotoIndexPlanRequest):
    plan_id: str = Field(alias="planId", pattern=r"^[0-9a-f]{64}$")


def _parse_byte_range(value: str, size: int) -> tuple[int, int]:
    if size <= 0 or not value.startswith("bytes=") or "," in value:
        raise ValueError("unsupported range")
    raw_start, separator, raw_end = value.removeprefix("bytes=").partition("-")
    if not separator or (not raw_start and not raw_end):
        raise ValueError("invalid range")
    if not raw_start:
        suffix = int(raw_end)
        if suffix <= 0:
            raise ValueError("invalid suffix range")
        return max(0, size - suffix), size - 1
    start = int(raw_start)
    if start < 0 or start >= size:
        raise ValueError("range start outside file")
    end = size - 1 if not raw_end else min(int(raw_end), size - 1)
    if end < start:
        raise ValueError("range end precedes start")
    return start, end


def create_photos_router(
    service: PhotoLibraryService,
    *,
    jwt_secret: str | None = None,
    approval: HighRiskApprovalService | None = None,
    audit: ApplianceAudit | None = None,
    authenticator: ApplianceAuthenticator | None = None,
    data_access: DataAccessPolicy | None = None,
) -> APIRouter:
    auth = resolve_authenticator(jwt_secret=jwt_secret, authenticator=authenticator)
    require_auth = auth.dependency()
    router = APIRouter(
        prefix="/api/appliance/photos",
        tags=["appliance", "photos"],
        dependencies=[Depends(require_auth)],
    )

    async def access_scope(actor: str = Depends(require_auth)) -> DataAccessScope:
        if data_access is None:
            return DataAccessScope.unrestricted(actor)
        try:
            return await run_in_threadpool(data_access.scope_for_actor, actor)
        except DataAccessDenied as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except DataAccessUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    scope_dependency = Depends(access_scope)

    def authorize(scope: DataAccessScope, mode: str, path: str = "") -> None:
        try:
            if mode == "read":
                scope.require_read(path)
            elif mode == "operator":
                scope.require_operator()
            else:  # pragma: no cover - internal programming guard
                raise RuntimeError("unknown photo authorization mode")
        except DataAccessDenied as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    def record(
        *,
        actor: str,
        target: str,
        outcome: str,
        metadata: dict[str, Any],
        intent_id: str | None = None,
    ) -> None:
        if audit is None:
            if auth.required:
                raise HTTPException(status_code=503, detail="photo index audit unavailable")
            return
        values = dict(metadata)
        if intent_id:
            values["intentId"] = intent_id
        try:
            audit.record(
                actor=actor,
                action="photos.index.build",
                target=target,
                outcome=outcome,
                metadata=values,
            )
        except (OSError, AuditIntegrityError) as exc:
            raise HTTPException(status_code=503, detail="photo index audit unavailable") from exc

    @router.get("/library")
    async def library(
        scope: DataAccessScope = scope_dependency,
        offset: int = Query(default=0, ge=0, le=100_000),
        limit: int = Query(default=120, ge=1, le=500),
        search: str | None = Query(default=None, max_length=120),
    ) -> dict[str, Any]:
        return await run_in_threadpool(
            service.library,
            offset=offset,
            limit=limit,
            search=search,
            path_visible=None if scope.operator else scope.visible,
        )

    @router.get("/status")
    async def status(scope: DataAccessScope = scope_dependency) -> dict[str, Any]:
        return await run_in_threadpool(
            service.status,
            path_visible=None if scope.operator else scope.visible,
        )

    @router.get("/thumbnail")
    async def thumbnail(
        request: Request,
        path: str = Query(min_length=1, max_length=2_048),
        size: int = Query(default=320, ge=64, le=512),
        scope: DataAccessScope = scope_dependency,
    ) -> Response:
        authorize(scope, "read", path)
        try:
            payload, media_type, etag = await run_in_threadpool(
                service.thumbnail,
                path,
                size=size,
                if_none_match=request.headers.get("if-none-match"),
            )
        except PhotoPathError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="photo not found") from exc
        headers = {
            "ETag": f'"{etag}"',
            "Cache-Control": "private, max-age=86400",
            "X-Content-Type-Options": "nosniff",
        }
        if payload is None:
            return Response(status_code=304, headers=headers)
        return Response(content=payload, media_type=media_type, headers=headers)

    @router.get("/original", response_class=StreamingResponse)
    async def original(
        request: Request,
        path: str = Query(min_length=1, max_length=2_048),
        scope: DataAccessScope = scope_dependency,
    ) -> Response:
        authorize(scope, "read", path)
        try:
            opened = await run_in_threadpool(service.original, path)
        except PhotoPathError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="photo not found") from exc

        etag = hashlib.sha256(f"{path}\0{opened.size}\0{opened.mtime_ns}".encode()).hexdigest()
        etag_header = f'"{etag}"'
        headers = {
            "Accept-Ranges": "bytes",
            "Cache-Control": "private, max-age=3600",
            "ETag": etag_header,
            "X-Content-Type-Options": "nosniff",
        }
        supplied = request.headers.get("if-none-match", "").strip()
        if supplied in {"*", etag, etag_header, f"W/{etag_header}"}:
            opened.stream.close()
            return Response(status_code=304, headers=headers)

        start, end = 0, opened.size - 1
        range_header = request.headers.get("range")
        if range_header:
            try:
                start, end = _parse_byte_range(range_header, opened.size)
            except ValueError as exc:
                opened.stream.close()
                raise HTTPException(
                    status_code=416,
                    detail="invalid photo byte range",
                    headers={"Content-Range": f"bytes */{opened.size}"},
                ) from exc
        length = max(0, end - start + 1)
        headers["Content-Length"] = str(length)
        status_code = 206 if range_header else 200
        if range_header:
            headers["Content-Range"] = f"bytes {start}-{end}/{opened.size}"

        def chunks() -> Iterator[bytes]:
            remaining = length
            try:
                opened.stream.seek(start)
                while remaining:
                    chunk = opened.stream.read(min(256 * 1024, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk
            finally:
                opened.stream.close()

        return StreamingResponse(
            chunks(),
            status_code=status_code,
            media_type=opened.media_type,
            headers=headers,
        )

    @router.post("/search")
    async def search(
        body: PhotoSearchRequest,
        scope: DataAccessScope = scope_dependency,
    ) -> dict[str, Any]:
        try:
            return await run_in_threadpool(
                service.search,
                body.query,
                limit=body.limit,
                path_visible=None if scope.operator else scope.visible,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post("/plans/index")
    async def plan_index(
        body: PhotoIndexPlanRequest,
        scope: DataAccessScope = scope_dependency,
    ) -> dict[str, Any]:
        authorize(scope, "operator")
        return await run_in_threadpool(service.plan_index, include_faces=body.include_faces)

    @router.post("/plans/index/apply")
    async def apply_index(
        body: PhotoIndexApplyRequest,
        request: Request,
        actor: str = Depends(require_auth),
        scope: DataAccessScope = scope_dependency,
    ) -> dict[str, Any]:
        authorize(scope, "operator")
        current = await run_in_threadpool(
            service.plan_index,
            include_faces=body.include_faces,
        )
        if current["planId"] != body.plan_id:
            raise HTTPException(status_code=409, detail="照片索引计划已变化，请重新确认")
        if not current["ready"]:
            raise HTTPException(
                status_code=409,
                detail={"message": "照片索引暂不可执行", "blockers": current["blockers"]},
            )
        if approval is None:
            if auth.required:
                raise HTTPException(status_code=503, detail="photo index approval unavailable")
        else:
            consume_request_approval(
                request,
                approval,
                actor=actor,
                action="photos.index.build",
                target=body.plan_id,
            )
        intent_id = request_intent_id(request)
        record(
            actor=actor,
            target=body.plan_id,
            outcome="attempted",
            metadata={
                "includeFaces": body.include_faces,
                "imageCount": current["imageCount"],
                "maxFiles": current["maxFiles"],
            },
            intent_id=intent_id,
        )

        def completed(job: dict[str, Any]) -> None:
            try:
                record(
                    actor=actor,
                    target=body.plan_id,
                    outcome="succeeded" if job["state"] == "succeeded" else "failed",
                    metadata={
                        "jobId": job.get("jobId"),
                        "includeFaces": body.include_faces,
                        "indexed": (job.get("result") or {}).get("indexed"),
                        "reason": job.get("error"),
                    },
                    intent_id=intent_id,
                )
            except HTTPException as exc:
                _log.error("photo index completion audit failed: %s", exc.detail)

        try:
            job = service.start_index(
                plan_id=body.plan_id,
                include_faces=body.include_faces,
                on_complete=completed,
            )
        except PhotoIndexConflict as exc:
            record(
                actor=actor,
                target=body.plan_id,
                outcome="failed",
                metadata={"reason": str(exc)},
                intent_id=intent_id,
            )
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"schema": "echo.photos.index-job.v1", "job": job}

    return router


__all__ = [
    "PhotoIndexApplyRequest",
    "PhotoIndexPlanRequest",
    "PhotoSearchRequest",
    "create_photos_router",
]
