"""Authenticated, plan-bound HTTP entry points for NAS document organization."""

from __future__ import annotations

from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from appliance.approval import HighRiskApprovalService, consume_request_approval
from appliance.files.organization import FileOrganizationService, OrganizationError
from appliance.security import ApplianceAuthenticator, resolve_authenticator

PlanId = Annotated[str, Path(pattern=r"^[0-9a-f]{64}$")]
EntryId = Annotated[str, Path(pattern=r"^(?:[0-9a-f]{24}|[0-9a-f]{64})$")]


class _CreatePlanBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(max_length=4096)


class _EmptyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


def create_file_organization_router(
    service: FileOrganizationService,
    *,
    authenticator: ApplianceAuthenticator | None = None,
    jwt_secret: str | None = None,
    approval: HighRiskApprovalService | None = None,
) -> APIRouter:
    auth = resolve_authenticator(jwt_secret=jwt_secret, authenticator=authenticator)
    require_auth = auth.dependency()
    router = APIRouter(prefix="/api/appliance/files/organize/plans", tags=["appliance", "files"])

    def invoke(method: Any, *args: Any) -> Any:
        try:
            return method(*args)
        except OrganizationError as exc:
            raise HTTPException(
                status_code=exc.status, detail={"error": exc.error, "message": exc.message}
            ) from exc

    def approved_apply(request: Request, actor: str, plan_id: str) -> dict[str, Any]:
        # Only the authorized persisted plan can choose the approval action.
        # Neither the request body nor a model-supplied target participates.
        plan = invoke(service.get_plan, actor, plan_id)
        direction = plan.get("direction")
        binding = plan.get("approval")
        if (
            plan.get("schema") != "echo.files.organize.plan.v1"
            or plan.get("planId") != plan_id
            or direction not in ("apply", "undo")
            or plan.get("requiresApproval") is not True
            or not isinstance(binding, dict)
            or binding.get("action") != f"files.organize.{direction}"
            or binding.get("target") != plan_id
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "organization_plan_invalid",
                    "message": "整理计划无法核实，请重新预览",
                },
            )
        if approval is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "organization_approval_unavailable",
                    "message": "操作审批暂不可用，本次执行未开始",
                },
            )
        consume_request_approval(
            request, approval, actor=actor, action=binding["action"], target=plan_id
        )
        # The service rechecks the owner, scope and file state under its lease.
        return invoke(service.apply, actor, plan_id)

    @router.post("")
    async def create_plan(body: _CreatePlanBody, actor: str = Depends(require_auth)):
        return await run_in_threadpool(invoke, service.create_plan, actor, body.path)

    @router.get("")
    async def list_plans(
        path: Annotated[str, Query(max_length=4096)] = "", actor: str = Depends(require_auth)
    ):
        return await run_in_threadpool(invoke, service.list_plans, actor, path)

    @router.get("/{plan_id}")
    async def get_plan(plan_id: PlanId, actor: str = Depends(require_auth)):
        return await run_in_threadpool(invoke, service.get_plan, actor, plan_id)

    @router.get("/{plan_id}/result")
    async def get_result(plan_id: PlanId, actor: str = Depends(require_auth)):
        return await run_in_threadpool(invoke, service.get_result, actor, plan_id)

    @router.get("/{plan_id}/entries/{entry_id}/original")
    async def original(
        plan_id: PlanId, entry_id: EntryId, request: Request, actor: str = Depends(require_auth)
    ):
        if (
            request.query_params
            or request.headers.get("content-length", "0") not in {"", "0"}
            or request.headers.get("transfer-encoding")
        ):
            raise HTTPException(
                status_code=422, detail="original download accepts only plan and entry IDs"
            )
        item = await run_in_threadpool(invoke, service.read_original, actor, plan_id, entry_id)
        return Response(
            content=item.data,
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f"attachment; filename=\"original\"; filename*=UTF-8''{quote(item.filename, safe='')}",
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
                "X-Content-SHA256": item.sha256,
            },
        )

    @router.post("/{plan_id}/apply")
    async def apply_plan(
        plan_id: PlanId, body: _EmptyBody, request: Request, actor: str = Depends(require_auth)
    ):
        return await run_in_threadpool(approved_apply, request, actor, plan_id)

    @router.post("/{plan_id}/undo-plan")
    async def create_undo_plan(
        plan_id: PlanId, body: _EmptyBody, actor: str = Depends(require_auth)
    ):
        return await run_in_threadpool(invoke, service.create_undo_plan, actor, plan_id)

    @router.post("/{plan_id}/cancel")
    async def cancel_plan(plan_id: PlanId, body: _EmptyBody, actor: str = Depends(require_auth)):
        return await run_in_threadpool(invoke, service.cancel, actor, plan_id)

    return router


__all__ = ["create_file_organization_router"]
