"""Authenticated share management and anonymous, token-only file delivery."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool
from starlette.responses import StreamingResponse

from appliance.approval import HighRiskApprovalService, consume_request_approval
from appliance.audit import ApplianceAudit, AuditIntegrityError
from appliance.data_access import DataAccessDenied, DataAccessUnavailable
from appliance.file_shares import FileShareError, FileShareService
from appliance.files.manager import PathEscape
from appliance.security import ApplianceAuthenticator, resolve_authenticator

ShareId = Annotated[str, Path(pattern=r"^[0-9a-f]{24}$")]
ShareToken = Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{40,64}$")]


class CreateShareBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    path: str = Field(min_length=1, max_length=4096)
    ttl_seconds: int = Field(default=7 * 24 * 3600, alias="ttlSeconds")
    max_downloads: int = Field(default=100, alias="maxDownloads")


class ApplyCreateShareBody(CreateShareBody):
    plan_id: str = Field(alias="planId", pattern=r"^[0-9a-f]{64}$")


class ApplyPlanBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    plan_id: str = Field(alias="planId", pattern=r"^[0-9a-f]{64}$")


def _parse_range(value: str, size: int) -> tuple[int, int]:
    if size <= 0 or not value.startswith("bytes=") or "," in value:
        raise ValueError("unsupported range")
    raw_start, separator, raw_end = value.removeprefix("bytes=").partition("-")
    if not separator or (not raw_start and not raw_end):
        raise ValueError("invalid range")
    if not raw_start:
        suffix = int(raw_end)
        if suffix <= 0:
            raise ValueError("invalid range")
        return max(0, size - suffix), size - 1
    start = int(raw_start)
    if start < 0 or start >= size:
        raise ValueError("range outside file")
    end = size - 1 if not raw_end else min(int(raw_end), size - 1)
    if end < start:
        raise ValueError("invalid range")
    return start, end


def create_file_share_router(
    service: FileShareService,
    *,
    authenticator: ApplianceAuthenticator | None = None,
    jwt_secret: str | None = None,
    approval: HighRiskApprovalService | None = None,
    audit: ApplianceAudit | None = None,
) -> APIRouter:
    auth = resolve_authenticator(jwt_secret=jwt_secret, authenticator=authenticator)
    require_auth = auth.dependency()
    router = APIRouter(tags=["appliance", "file-shares"])

    def invoke(method: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return method(*args, **kwargs)
        except FileShareError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.message) from exc
        except DataAccessDenied as exc:
            raise HTTPException(status_code=403, detail="data path is not authorized") from exc
        except DataAccessUnavailable as exc:
            raise HTTPException(
                status_code=503, detail="data access authority unavailable"
            ) from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail="share state unavailable") from exc
        except PathEscape as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="file not found") from exc

    def record(*, actor: str, action: str, target: str, outcome: str) -> None:
        if audit is None:
            if auth.required:
                raise HTTPException(status_code=503, detail="appliance audit unavailable")
            return
        try:
            audit.record(actor=actor, action=action, target=target, outcome=outcome)
        except (OSError, AuditIntegrityError) as exc:
            raise HTTPException(status_code=503, detail="appliance audit unavailable") from exc

    @router.get("/api/appliance/file-shares")
    async def list_shares(actor: str = Depends(require_auth)) -> dict[str, Any]:
        return {
            "schema": "echo.file-shares.list.v1",
            "shares": await run_in_threadpool(invoke, service.list, actor),
        }

    @router.post("/api/appliance/file-shares/plans")
    async def plan_create(
        body: CreateShareBody, actor: str = Depends(require_auth)
    ) -> dict[str, Any]:
        return await run_in_threadpool(
            invoke,
            service.plan_create,
            actor,
            body.path,
            ttl_seconds=body.ttl_seconds,
            max_downloads=body.max_downloads,
        )

    @router.post("/api/appliance/file-shares/apply")
    async def apply_create(
        body: ApplyCreateShareBody,
        request: Request,
        actor: str = Depends(require_auth),
    ) -> dict[str, Any]:
        plan = await run_in_threadpool(
            invoke,
            service.plan_create,
            actor,
            body.path,
            ttl_seconds=body.ttl_seconds,
            max_downloads=body.max_downloads,
        )
        if approval is None:
            raise HTTPException(status_code=503, detail="high-risk approval unavailable")
        consume_request_approval(
            request,
            approval,
            actor=actor,
            action="files.share.create",
            target=plan["planId"],
        )
        result = await run_in_threadpool(
            invoke,
            service.create,
            actor,
            body.path,
            ttl_seconds=body.ttl_seconds,
            max_downloads=body.max_downloads,
            plan_id=body.plan_id,
        )
        share_id = result["share"]["id"]
        record(actor=actor, action="files.share.create", target=share_id, outcome="succeeded")
        return {
            "schema": "echo.file-share.created.v1",
            "share": result["share"],
            "url": f"/api/public/file-shares/{result['token']}",
        }

    @router.post("/api/appliance/file-shares/{share_id}/revoke-plan")
    async def plan_revoke(share_id: ShareId, actor: str = Depends(require_auth)) -> dict[str, Any]:
        return await run_in_threadpool(invoke, service.plan_revoke, actor, share_id)

    @router.post("/api/appliance/file-shares/{share_id}/revoke")
    async def revoke(
        share_id: ShareId,
        body: ApplyPlanBody,
        request: Request,
        actor: str = Depends(require_auth),
    ) -> dict[str, Any]:
        plan = await run_in_threadpool(invoke, service.plan_revoke, actor, share_id)
        if approval is None:
            raise HTTPException(status_code=503, detail="high-risk approval unavailable")
        consume_request_approval(
            request,
            approval,
            actor=actor,
            action="files.share.revoke",
            target=plan["planId"],
        )
        result = await run_in_threadpool(
            invoke, service.revoke, actor, share_id, plan_id=body.plan_id
        )
        record(actor=actor, action="files.share.revoke", target=share_id, outcome="succeeded")
        return result

    @router.get("/api/public/file-shares/{token}", response_class=StreamingResponse)
    async def download(token: ShareToken, request: Request) -> Response:
        # This is intentionally the only unauthenticated route. It accepts no
        # query-selected path and its 256-bit capability is stored HMAC-only.
        try:
            opened = await run_in_threadpool(service.redeem, token)
        except DataAccessUnavailable as exc:
            raise HTTPException(
                status_code=503, detail="share service temporarily unavailable"
            ) from exc
        except (DataAccessDenied, FileShareError, PathEscape, FileNotFoundError):
            # Do not reveal whether a guessed capability was valid but later
            # revoked by ACL changes, expired, or replaced on disk.
            raise HTTPException(status_code=404, detail="share not found") from None
        except OSError as exc:
            raise HTTPException(
                status_code=503, detail="share service temporarily unavailable"
            ) from exc
        etag = hashlib.sha256(
            f"{opened.share_id}\0{opened.size}\0{opened.mtime_ns}".encode()
        ).hexdigest()
        headers = {
            "Accept-Ranges": "bytes",
            "Cache-Control": "private, no-store",
            "Content-Disposition": f"attachment; filename=download; filename*=UTF-8''{quote(opened.filename, safe='')}",
            "ETag": f'"{etag}"',
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        }
        start, end = 0, opened.size - 1
        supplied_range = request.headers.get("range")
        if supplied_range:
            try:
                start, end = _parse_range(supplied_range, opened.size)
            except (ValueError, OverflowError):
                opened.stream.close()
                raise HTTPException(
                    status_code=416,
                    detail="invalid byte range",
                    headers={"Content-Range": f"bytes */{opened.size}"},
                ) from None
        length = max(0, end - start + 1)
        headers["Content-Length"] = str(length)
        status_code = 206 if supplied_range else 200
        if supplied_range:
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

        try:
            record(
                actor="public:file-share",
                action="files.share.download",
                target=opened.share_id,
                outcome="opened",
            )
        except Exception:
            opened.stream.close()
            raise
        return StreamingResponse(
            chunks(), status_code=status_code, media_type=opened.media_type, headers=headers
        )

    return router


__all__ = ["create_file_share_router"]
