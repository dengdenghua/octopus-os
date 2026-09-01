"""Filesystem quota mutation routes for the Echo OMV API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from appliance.omv_client import OmvControlRejected, OmvUnavailable
from appliance.omv_models import QuotaApplyRequest, QuotaDesiredState
from appliance.omv_route_context import OmvRouteContext


def register_omv_quota_routes(router: APIRouter, context: OmvRouteContext) -> None:
    """Attach filesystem quota planning and application routes."""
    omv = context.omv
    require_auth = context.require_auth

    @router.post("/quota/plan")
    async def plan_filesystem_quota(body: QuotaDesiredState) -> dict:
        try:
            return await run_in_threadpool(
                omv.plan_filesystem_quota,
                body.model_dump(by_alias=True),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OmvControlRejected as exc:
            raise context.control_error(exc) from exc
        except OmvUnavailable as exc:
            raise HTTPException(status_code=503, detail="OMV quota control is unavailable") from exc

    @router.post("/quota/apply")
    async def apply_filesystem_quota(
        body: QuotaApplyRequest,
        request: Request,
        actor: str = Depends(require_auth),
    ) -> dict:
        desired = body.desired.model_dump(by_alias=True)
        try:
            current_plan = await run_in_threadpool(omv.plan_filesystem_quota, desired)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OmvControlRejected as exc:
            raise context.control_error(exc) from exc
        except OmvUnavailable as exc:
            raise HTTPException(status_code=503, detail="OMV quota control is unavailable") from exc
        if current_plan.get("planId") != body.plan_id:
            raise HTTPException(status_code=409, detail="quota plan is stale; preview again")
        if current_plan.get("operation") == "none":
            try:
                return await run_in_threadpool(
                    omv.apply_filesystem_quota,
                    desired,
                    body.plan_id,
                )
            except OmvControlRejected as exc:
                raise context.control_error(exc) from exc
            except OmvUnavailable as exc:
                raise HTTPException(
                    status_code=503,
                    detail="OMV quota control is unavailable",
                ) from exc

        context.consume_approval(
            request,
            actor=actor,
            action="omv.quota.apply",
            target=body.plan_id,
        )
        subject = current_plan.get("subject", {})
        metadata = {
            "filesystemUuid": desired["filesystemUuid"],
            "subjectType": desired["subjectType"],
            "subjectName": desired["subjectName"],
            "beforeBytes": subject.get("hardLimitBytes"),
            "afterBytes": desired["hardLimitBytes"],
        }
        context.record(
            request,
            action="omv.quota.apply",
            actor=actor,
            target=body.plan_id,
            outcome="attempted",
            metadata=metadata,
        )
        try:
            result = await run_in_threadpool(
                omv.apply_filesystem_quota,
                desired,
                body.plan_id,
            )
        except (OmvControlRejected, OmvUnavailable, ValueError) as exc:
            context.record(
                request,
                action="omv.quota.apply",
                actor=actor,
                target=body.plan_id,
                outcome="failed",
                metadata={**metadata, "errorType": type(exc).__name__},
            )
            if isinstance(exc, OmvControlRejected):
                raise context.control_error(exc) from exc
            if isinstance(exc, ValueError):
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            raise HTTPException(status_code=503, detail="OMV quota control is unavailable") from exc
        context.record(
            request,
            action="omv.quota.apply",
            actor=actor,
            target=body.plan_id,
            outcome="succeeded",
            metadata=metadata,
        )
        return result


__all__ = ["register_omv_quota_routes"]
