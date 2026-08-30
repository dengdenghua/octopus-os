"""Authenticated Echo Hub catalog and install-plan API."""

from __future__ import annotations

import re
from typing import Any, Protocol

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from appliance.app_registry.docker_client import (
    DockerClient,
    DockerConflict,
    DockerControlDenied,
    DockerUnavailable,
)
from appliance.approval import (
    HighRiskApprovalService,
    consume_request_approval,
    request_intent_id,
)
from appliance.audit import ApplianceAudit, AuditIntegrityError
from appliance.hub.catalog import HubCatalog
from appliance.hub.operations import (
    HubOperationConflict,
    HubOperationCredentialsUnavailable,
    HubOperationService,
    HubOperationUnavailable,
)
from appliance.hub.service import HubService
from appliance.security import ApplianceAuthenticator, resolve_authenticator

_APP_ID = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")


class HubInstallPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    app_id: str = Field(alias="appId", min_length=1, max_length=64)


class HubInstallApplyRequest(HubInstallPlanRequest):
    plan_id: str = Field(alias="planId", pattern=r"^[0-9a-f]{64}$")


class HubInstallExecutor(Protocol):
    def install_hub_app(
        self,
        app_id: str,
        *,
        plan_id: str,
        catalog_digest: str,
    ) -> dict[str, Any]: ...

    def uninstall_hub_app(
        self,
        app_id: str,
        *,
        plan_id: str,
        catalog_digest: str,
    ) -> dict[str, Any]: ...

    def update_hub_app(
        self,
        app_id: str,
        *,
        plan_id: str,
        catalog_digest: str,
    ) -> dict[str, Any]: ...

    def start_hub_app(
        self,
        app_id: str,
        *,
        plan_id: str,
        catalog_digest: str,
    ) -> dict[str, Any]: ...

    def stop_hub_app(
        self,
        app_id: str,
        *,
        plan_id: str,
        catalog_digest: str,
    ) -> dict[str, Any]: ...

    def restart_hub_app(
        self,
        app_id: str,
        *,
        plan_id: str,
        catalog_digest: str,
    ) -> dict[str, Any]: ...


def create_hub_router(
    service: HubService | None = None,
    *,
    installer: HubInstallExecutor | None = None,
    jwt_secret: str | None = None,
    approval: HighRiskApprovalService | None = None,
    audit: ApplianceAudit | None = None,
    operations: HubOperationService | None = None,
    authenticator: ApplianceAuthenticator | None = None,
) -> APIRouter:
    docker_client = DockerClient()
    hub = service or HubService(HubCatalog.load(), docker=docker_client)
    install_executor = installer or docker_client
    auth = resolve_authenticator(jwt_secret=jwt_secret, authenticator=authenticator)
    require_auth = auth.dependency()
    require_operator = auth.operator_dependency()
    router = APIRouter(
        prefix="/api/appliance/hub",
        tags=["appliance", "hub"],
        dependencies=[Depends(require_auth)],
    )

    def _record(
        *,
        request: Request,
        actor: str,
        action: str,
        target: str,
        outcome: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if audit is None:
            if auth.required:
                raise HTTPException(status_code=503, detail="Hub audit unavailable")
            return
        audit_metadata = dict(metadata or {})
        intent_id = request_intent_id(request)
        if intent_id:
            audit_metadata["intentId"] = intent_id
        try:
            audit.record(
                actor=actor,
                action=action,
                target=target,
                outcome=outcome,
                metadata=audit_metadata,
            )
        except (OSError, AuditIntegrityError) as exc:
            raise HTTPException(status_code=503, detail="Hub audit unavailable") from exc

    @router.get("/catalog")
    async def list_catalog(
        search: str | None = Query(default=None, max_length=120),
        category: str | None = Query(default=None, max_length=32),
    ) -> dict:
        return hub.list_catalog(search=search, category=category)

    @router.get("/apps/{app_id}")
    async def app_detail(app_id: str) -> dict:
        if _APP_ID.fullmatch(app_id) is None:
            raise HTTPException(status_code=422, detail="invalid hub app id")
        try:
            return await run_in_threadpool(hub.app_detail, app_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="hub app not found") from exc

    @router.post("/plans/install")
    async def plan_install(
        body: HubInstallPlanRequest,
        _actor: str = Depends(require_operator),
    ) -> dict:
        if _APP_ID.fullmatch(body.app_id) is None:
            raise HTTPException(status_code=422, detail="invalid hub app id")
        try:
            return hub.plan_install(body.app_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="hub app not found") from exc

    @router.post("/plans/install/apply")
    async def apply_install(
        body: HubInstallApplyRequest,
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict:
        if _APP_ID.fullmatch(body.app_id) is None:
            raise HTTPException(status_code=422, detail="invalid hub app id")
        try:
            current_plan = hub.plan_install(body.app_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="hub app not found") from exc
        if current_plan["planId"] != body.plan_id:
            raise HTTPException(status_code=409, detail="Hub install plan changed; review again")
        if not current_plan["ready"]:
            raise HTTPException(
                status_code=409,
                detail={"message": "Hub install is blocked", "blockers": current_plan["blockers"]},
            )
        if approval is None:
            if auth.required:
                raise HTTPException(status_code=503, detail="Hub approval unavailable")
        else:
            consume_request_approval(
                request,
                approval,
                actor=actor,
                action="hub.app.install",
                target=body.plan_id,
            )
        _record(
            request=request,
            actor=actor,
            action="hub.app.install",
            target=body.plan_id,
            outcome="attempted",
            metadata={"appId": body.app_id, "catalogDigest": hub.catalog.digest},
        )
        try:
            result = await run_in_threadpool(
                install_executor.install_hub_app,
                body.app_id,
                plan_id=body.plan_id,
                catalog_digest=hub.catalog.digest,
            )
        except DockerControlDenied as exc:
            _record(
                request=request,
                actor=actor,
                action="hub.app.install",
                target=body.plan_id,
                outcome="failed",
                metadata={"appId": body.app_id, "reason": "installer denied"},
            )
            raise HTTPException(
                status_code=403,
                detail="Hub application control is not allowed",
            ) from exc
        except DockerConflict as exc:
            _record(
                request=request,
                actor=actor,
                action="hub.app.install",
                target=body.plan_id,
                outcome="failed",
                metadata={"appId": body.app_id, "reason": "installer plan conflict"},
            )
            raise HTTPException(
                status_code=409,
                detail="Hub install state changed; review a new plan",
            ) from exc
        except DockerUnavailable as exc:
            _record(
                request=request,
                actor=actor,
                action="hub.app.install",
                target=body.plan_id,
                outcome="failed",
                metadata={"appId": body.app_id, "reason": "installer unavailable"},
            )
            raise HTTPException(
                status_code=503,
                detail="Hub application control is unavailable",
            ) from exc
        _record(
            request=request,
            actor=actor,
            action="hub.app.install",
            target=body.plan_id,
            outcome="succeeded",
            metadata={
                "appId": body.app_id,
                "containerId": result.get("containerId"),
                "catalogDigest": hub.catalog.digest,
            },
        )
        return result

    @router.post("/plans/update")
    async def plan_update(
        body: HubInstallPlanRequest,
        _actor: str = Depends(require_operator),
    ) -> dict:
        if _APP_ID.fullmatch(body.app_id) is None:
            raise HTTPException(status_code=422, detail="invalid hub app id")
        try:
            return hub.plan_update(body.app_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="hub app not found") from exc

    @router.post("/plans/update/apply")
    async def apply_update(
        body: HubInstallApplyRequest,
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict:
        if _APP_ID.fullmatch(body.app_id) is None:
            raise HTTPException(status_code=422, detail="invalid hub app id")
        try:
            current_plan = hub.plan_update(body.app_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="hub app not found") from exc
        if current_plan["planId"] != body.plan_id:
            raise HTTPException(status_code=409, detail="Hub update plan changed; review again")
        if not current_plan["ready"]:
            raise HTTPException(
                status_code=409,
                detail={"message": "Hub update is blocked", "blockers": current_plan["blockers"]},
            )
        if approval is None:
            if auth.required:
                raise HTTPException(status_code=503, detail="Hub approval unavailable")
        else:
            consume_request_approval(
                request,
                approval,
                actor=actor,
                action="hub.app.update",
                target=body.plan_id,
            )
        _record(
            request=request,
            actor=actor,
            action="hub.app.update",
            target=body.plan_id,
            outcome="attempted",
            metadata={"appId": body.app_id, "catalogDigest": hub.catalog.digest},
        )
        try:
            result = await run_in_threadpool(
                install_executor.update_hub_app,
                body.app_id,
                plan_id=body.plan_id,
                catalog_digest=hub.catalog.digest,
            )
        except DockerControlDenied as exc:
            _record(
                request=request,
                actor=actor,
                action="hub.app.update",
                target=body.plan_id,
                outcome="failed",
                metadata={"appId": body.app_id, "reason": "updater denied"},
            )
            raise HTTPException(
                status_code=403,
                detail="Hub application control is not allowed",
            ) from exc
        except DockerConflict as exc:
            _record(
                request=request,
                actor=actor,
                action="hub.app.update",
                target=body.plan_id,
                outcome="failed",
                metadata={"appId": body.app_id, "reason": "updater plan conflict"},
            )
            raise HTTPException(
                status_code=409,
                detail="Hub update state changed; review a new plan",
            ) from exc
        except DockerUnavailable as exc:
            _record(
                request=request,
                actor=actor,
                action="hub.app.update",
                target=body.plan_id,
                outcome="failed",
                metadata={"appId": body.app_id, "reason": "updater unavailable"},
            )
            raise HTTPException(
                status_code=503,
                detail="Hub application control is unavailable",
            ) from exc
        _record(
            request=request,
            actor=actor,
            action="hub.app.update",
            target=body.plan_id,
            outcome="succeeded",
            metadata={
                "appId": body.app_id,
                "containerId": result.get("containerId"),
                "previousContainerId": result.get("previousContainerId"),
                "catalogDigest": hub.catalog.digest,
                "dataVolumesRetained": True,
            },
        )
        return result

    @router.post("/plans/uninstall")
    async def plan_uninstall(
        body: HubInstallPlanRequest,
        _actor: str = Depends(require_operator),
    ) -> dict:
        if _APP_ID.fullmatch(body.app_id) is None:
            raise HTTPException(status_code=422, detail="invalid hub app id")
        try:
            return hub.plan_uninstall(body.app_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="hub app not found") from exc

    def _control_plan(operation: str, app_id: str) -> dict[str, Any]:
        if _APP_ID.fullmatch(app_id) is None:
            raise HTTPException(status_code=422, detail="invalid hub app id")
        try:
            return {
                "start": hub.plan_start,
                "stop": hub.plan_stop,
                "restart": hub.plan_restart,
            }[operation](app_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="hub app not found") from exc

    @router.post("/plans/start")
    async def plan_start(
        body: HubInstallPlanRequest,
        _actor: str = Depends(require_operator),
    ) -> dict:
        return _control_plan("start", body.app_id)

    @router.post("/plans/stop")
    async def plan_stop(
        body: HubInstallPlanRequest,
        _actor: str = Depends(require_operator),
    ) -> dict:
        return _control_plan("stop", body.app_id)

    @router.post("/plans/restart")
    async def plan_restart(
        body: HubInstallPlanRequest,
        _actor: str = Depends(require_operator),
    ) -> dict:
        return _control_plan("restart", body.app_id)

    @router.post("/plans/uninstall/apply")
    async def apply_uninstall(
        body: HubInstallApplyRequest,
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict:
        if _APP_ID.fullmatch(body.app_id) is None:
            raise HTTPException(status_code=422, detail="invalid hub app id")
        try:
            current_plan = hub.plan_uninstall(body.app_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="hub app not found") from exc
        if current_plan["planId"] != body.plan_id:
            raise HTTPException(status_code=409, detail="Hub uninstall plan changed; review again")
        if not current_plan["ready"]:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "Hub uninstall is blocked",
                    "blockers": current_plan["blockers"],
                },
            )
        if approval is None:
            if auth.required:
                raise HTTPException(status_code=503, detail="Hub approval unavailable")
        else:
            consume_request_approval(
                request,
                approval,
                actor=actor,
                action="hub.app.uninstall",
                target=body.plan_id,
            )
        _record(
            request=request,
            actor=actor,
            action="hub.app.uninstall",
            target=body.plan_id,
            outcome="attempted",
            metadata={"appId": body.app_id, "catalogDigest": hub.catalog.digest},
        )
        try:
            result = await run_in_threadpool(
                install_executor.uninstall_hub_app,
                body.app_id,
                plan_id=body.plan_id,
                catalog_digest=hub.catalog.digest,
            )
        except DockerControlDenied as exc:
            _record(
                request=request,
                actor=actor,
                action="hub.app.uninstall",
                target=body.plan_id,
                outcome="failed",
                metadata={"appId": body.app_id, "reason": "uninstaller denied"},
            )
            raise HTTPException(
                status_code=403,
                detail="Hub application control is not allowed",
            ) from exc
        except DockerConflict as exc:
            _record(
                request=request,
                actor=actor,
                action="hub.app.uninstall",
                target=body.plan_id,
                outcome="failed",
                metadata={"appId": body.app_id, "reason": "uninstaller plan conflict"},
            )
            raise HTTPException(
                status_code=409,
                detail="Hub uninstall state changed; review a new plan",
            ) from exc
        except DockerUnavailable as exc:
            _record(
                request=request,
                actor=actor,
                action="hub.app.uninstall",
                target=body.plan_id,
                outcome="failed",
                metadata={"appId": body.app_id, "reason": "uninstaller unavailable"},
            )
            raise HTTPException(
                status_code=503,
                detail="Hub application control is unavailable",
            ) from exc
        _record(
            request=request,
            actor=actor,
            action="hub.app.uninstall",
            target=body.plan_id,
            outcome="succeeded",
            metadata={
                "appId": body.app_id,
                "containerId": result.get("containerId"),
                "catalogDigest": hub.catalog.digest,
                "dataVolumesRetained": True,
            },
        )
        return result

    def _queue_operation(
        *,
        operation: str,
        body: HubInstallApplyRequest,
        request: Request,
        actor: str,
    ) -> dict[str, Any]:
        if operations is None:
            raise HTTPException(status_code=503, detail="Hub background operations unavailable")
        if _APP_ID.fullmatch(body.app_id) is None:
            raise HTTPException(status_code=422, detail="invalid hub app id")
        plan_builder = {
            "install": hub.plan_install,
            "update": hub.plan_update,
            "uninstall": hub.plan_uninstall,
            "start": hub.plan_start,
            "stop": hub.plan_stop,
            "restart": hub.plan_restart,
        }[operation]
        try:
            current_plan = plan_builder(body.app_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="hub app not found") from exc
        if current_plan["planId"] != body.plan_id:
            raise HTTPException(
                status_code=409,
                detail=f"Hub {operation} plan changed; review again",
            )
        if not current_plan["ready"]:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": f"Hub {operation} is blocked",
                    "blockers": current_plan["blockers"],
                },
            )
        action = f"hub.app.{operation}"
        if approval is None:
            if auth.required:
                raise HTTPException(status_code=503, detail="Hub approval unavailable")
        else:
            consume_request_approval(
                request,
                approval,
                actor=actor,
                action=action,
                target=body.plan_id,
            )
        _record(
            request=request,
            actor=actor,
            action=action,
            target=body.plan_id,
            outcome="attempted",
            metadata={"appId": body.app_id, "catalogDigest": hub.catalog.digest},
        )
        try:
            return operations.submit(
                action=operation,  # type: ignore[arg-type]
                app_id=body.app_id,
                plan_id=body.plan_id,
                catalog_digest=hub.catalog.digest,
                actor=actor,
                intent_id=request_intent_id(request),
            )
        except HubOperationConflict as exc:
            _record(
                request=request,
                actor=actor,
                action=action,
                target=body.plan_id,
                outcome="failed",
                metadata={"appId": body.app_id, "reason": "operation already active"},
            )
            raise HTTPException(
                status_code=409,
                detail="This app already has an active Hub operation",
            ) from exc
        except HubOperationUnavailable as exc:
            _record(
                request=request,
                actor=actor,
                action=action,
                target=body.plan_id,
                outcome="failed",
                metadata={"appId": body.app_id, "reason": "operation queue full"},
            )
            raise HTTPException(
                status_code=503,
                detail="Hub background operation queue is unavailable",
            ) from exc

    @router.post("/plans/install/queue", status_code=202)
    async def queue_install(
        body: HubInstallApplyRequest,
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict:
        return _queue_operation(operation="install", body=body, request=request, actor=actor)

    @router.post("/plans/update/queue", status_code=202)
    async def queue_update(
        body: HubInstallApplyRequest,
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict:
        return _queue_operation(operation="update", body=body, request=request, actor=actor)

    @router.post("/plans/uninstall/queue", status_code=202)
    async def queue_uninstall(
        body: HubInstallApplyRequest,
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict:
        return _queue_operation(operation="uninstall", body=body, request=request, actor=actor)

    @router.post("/plans/start/queue", status_code=202)
    async def queue_start(
        body: HubInstallApplyRequest,
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict:
        return _queue_operation(operation="start", body=body, request=request, actor=actor)

    @router.post("/plans/stop/queue", status_code=202)
    async def queue_stop(
        body: HubInstallApplyRequest,
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict:
        return _queue_operation(operation="stop", body=body, request=request, actor=actor)

    @router.post("/plans/restart/queue", status_code=202)
    async def queue_restart(
        body: HubInstallApplyRequest,
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict:
        return _queue_operation(operation="restart", body=body, request=request, actor=actor)

    @router.get("/operations")
    async def list_operations(
        app_id: str | None = Query(default=None, alias="appId", max_length=64),
        limit: int = Query(default=20, ge=1, le=50),
        _actor: str = Depends(require_operator),
    ) -> dict:
        if operations is None:
            raise HTTPException(status_code=503, detail="Hub background operations unavailable")
        if app_id is not None and _APP_ID.fullmatch(app_id) is None:
            raise HTTPException(status_code=422, detail="invalid hub app id")
        return operations.list(app_id=app_id, limit=limit)

    @router.get("/operations/{operation_id}")
    async def operation_detail(
        operation_id: str,
        _actor: str = Depends(require_operator),
    ) -> dict:
        if operations is None:
            raise HTTPException(status_code=503, detail="Hub background operations unavailable")
        try:
            return operations.get(operation_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Hub operation not found") from exc

    @router.post("/operations/{operation_id}/credentials/claim")
    async def claim_operation_credentials(
        operation_id: str,
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict:
        if operations is None:
            raise HTTPException(status_code=503, detail="Hub background operations unavailable")
        try:
            operation = operations.get(operation_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Hub operation not found") from exc
        _record(
            request=request,
            actor=actor,
            action="hub.app.credentials.claim",
            target=operation_id,
            outcome="attempted",
            metadata={"appId": operation["appId"]},
        )
        try:
            return operations.claim_credentials(operation_id)
        except HubOperationCredentialsUnavailable as exc:
            raise HTTPException(
                status_code=409,
                detail="One-time Hub credentials are unavailable",
            ) from exc

    return router


__all__ = ["HubInstallApplyRequest", "HubInstallPlanRequest", "create_hub_router"]
