"""Account and credential mutation routes for the Echo OMV API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from appliance.omv_client import OmvControlRejected, OmvUnavailable
from appliance.omv_models import (
    GroupApplyRequest,
    GroupDesiredState,
    UserApplyRequest,
    UserDesiredState,
    UserPasswordApplyRequest,
    UserPasswordDesiredState,
)
from appliance.omv_route_context import OmvRouteContext


def register_omv_account_routes(router: APIRouter, context: OmvRouteContext) -> None:
    """Attach OMV group, user, and credential routes."""
    omv = context.omv
    require_auth = context.require_auth

    @router.post("/accounts/groups/plan")
    async def plan_group(body: GroupDesiredState) -> dict:
        desired = body.model_dump(by_alias=True)
        try:
            return await run_in_threadpool(omv.plan_group, desired)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OmvControlRejected as exc:
            raise context.control_error(exc) from exc
        except OmvUnavailable as exc:
            raise HTTPException(
                status_code=503,
                detail="OMV group control is unavailable",
            ) from exc

    @router.post("/accounts/groups/apply")
    async def apply_group(
        body: GroupApplyRequest,
        request: Request,
        actor: str = Depends(require_auth),
    ) -> dict:
        desired = body.desired.model_dump(by_alias=True)
        try:
            current_plan = await run_in_threadpool(omv.plan_group, desired)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OmvControlRejected as exc:
            raise context.control_error(exc) from exc
        except OmvUnavailable as exc:
            raise HTTPException(status_code=503, detail="OMV group control is unavailable") from exc
        if current_plan.get("planId") != body.plan_id:
            raise HTTPException(status_code=409, detail="group plan is stale; preview again")
        context.consume_approval(
            request,
            actor=actor,
            action="omv.group.create",
            target=body.plan_id,
        )
        metadata = {
            "operation": "create",
            "name": desired["name"],
            "changeFields": ["name", "comment"],
        }
        context.record(
            request,
            action="omv.group.create",
            actor=actor,
            target=body.plan_id,
            outcome="attempted",
            metadata=metadata,
        )
        try:
            result = await run_in_threadpool(omv.apply_group, desired, body.plan_id)
        except (OmvControlRejected, OmvUnavailable, ValueError) as exc:
            context.record(
                request,
                action="omv.group.create",
                actor=actor,
                target=body.plan_id,
                outcome="failed",
                metadata={**metadata, "errorType": type(exc).__name__},
            )
            if isinstance(exc, OmvControlRejected):
                raise context.control_error(exc) from exc
            if isinstance(exc, ValueError):
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            raise HTTPException(status_code=503, detail="OMV group control is unavailable") from exc
        context.record(
            request,
            action="omv.group.create",
            actor=actor,
            target=body.plan_id,
            outcome="succeeded",
            metadata=metadata,
        )
        return result

    @router.post("/accounts/users/plan")
    async def plan_user(body: UserDesiredState) -> dict:
        try:
            desired = body.to_wire()
            return await run_in_threadpool(omv.plan_user, desired)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OmvControlRejected as exc:
            raise context.control_error(exc) from exc
        except OmvUnavailable as exc:
            raise HTTPException(status_code=503, detail="OMV user control is unavailable") from exc

    @router.post("/accounts/users/apply")
    async def apply_user(
        body: UserApplyRequest,
        request: Request,
        actor: str = Depends(require_auth),
    ) -> dict:
        try:
            desired = body.desired.to_wire()
            current_plan = await run_in_threadpool(omv.plan_user, desired)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OmvControlRejected as exc:
            raise context.control_error(exc) from exc
        except OmvUnavailable as exc:
            raise HTTPException(status_code=503, detail="OMV user control is unavailable") from exc
        if current_plan.get("planId") != body.plan_id:
            raise HTTPException(status_code=409, detail="user plan is stale; preview again")
        context.consume_approval(
            request,
            actor=actor,
            action="omv.user.create",
            target=body.plan_id,
        )
        metadata = {
            "operation": "create",
            "name": desired["name"],
            "displayName": desired["displayName"],
            "groups": list(desired["groups"]),
            "changeFields": ["name", "displayName", "groups"],
        }
        context.record(
            request,
            action="omv.user.create",
            actor=actor,
            target=body.plan_id,
            outcome="attempted",
            metadata=metadata,
        )
        try:
            result = await run_in_threadpool(omv.apply_user, desired, body.plan_id)
        except (OmvControlRejected, OmvUnavailable, ValueError) as exc:
            context.record(
                request,
                action="omv.user.create",
                actor=actor,
                target=body.plan_id,
                outcome="failed",
                metadata={**metadata, "errorType": type(exc).__name__},
            )
            if isinstance(exc, OmvControlRejected):
                raise context.control_error(exc) from exc
            if isinstance(exc, ValueError):
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            raise HTTPException(status_code=503, detail="OMV user control is unavailable") from exc
        context.record(
            request,
            action="omv.user.create",
            actor=actor,
            target=body.plan_id,
            outcome="succeeded",
            metadata=metadata,
        )
        return result

    @router.post("/accounts/users/password/plan")
    async def plan_user_password(body: UserPasswordDesiredState) -> dict:
        try:
            desired = body.to_wire()
            return await run_in_threadpool(omv.plan_user_password, desired)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OmvControlRejected as exc:
            raise context.control_error(exc) from exc
        except OmvUnavailable as exc:
            raise HTTPException(
                status_code=503,
                detail="OMV user password control is unavailable",
            ) from exc

    @router.post("/accounts/users/password/apply")
    async def apply_user_password(
        body: UserPasswordApplyRequest,
        request: Request,
        actor: str = Depends(require_auth),
    ) -> dict:
        try:
            desired = body.desired.to_wire()
            current_plan = await run_in_threadpool(omv.plan_user_password, desired)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OmvControlRejected as exc:
            raise context.control_error(exc) from exc
        except OmvUnavailable as exc:
            raise HTTPException(
                status_code=503,
                detail="OMV user password control is unavailable",
            ) from exc
        if current_plan.get("planId") != body.plan_id:
            raise HTTPException(
                status_code=409, detail="user password plan is stale; preview again"
            )
        context.consume_approval(
            request,
            actor=actor,
            action="omv.user.password.reset",
            target=body.plan_id,
        )
        metadata = {
            "operation": "resetPassword",
            "name": desired["name"],
            "changeFields": ["password"],
            "rollback": "notAvailableAfterAcceptedSecretRpc",
        }
        context.record(
            request,
            action="omv.user.password.reset",
            actor=actor,
            target=body.plan_id,
            outcome="attempted",
            metadata=metadata,
        )
        try:
            result = await run_in_threadpool(
                omv.apply_user_password,
                desired,
                body.plan_id,
            )
        except (OmvControlRejected, OmvUnavailable, ValueError) as exc:
            context.record(
                request,
                action="omv.user.password.reset",
                actor=actor,
                target=body.plan_id,
                outcome="failed",
                metadata={**metadata, "errorType": type(exc).__name__},
            )
            if isinstance(exc, OmvControlRejected):
                raise context.control_error(exc) from exc
            if isinstance(exc, ValueError):
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            raise HTTPException(
                status_code=503,
                detail="OMV user password control is unavailable",
            ) from exc
        context.record(
            request,
            action="omv.user.password.reset",
            actor=actor,
            target=body.plan_id,
            outcome="succeeded",
            metadata=metadata,
        )
        return result


__all__ = ["register_omv_account_routes"]
