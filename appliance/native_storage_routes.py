"""Read-only native storage routes (no OpenMediaVault required).

Exposed under ``/api/appliance/storage`` with the same payload shapes as
``/api/appliance/omv`` so the appliance UI can fall back to the native plane
whenever a host has no OMV installed — which is the case for the p3-provision
installer route, which never deployed OMV.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from appliance import native_storage
from appliance.omv_models import (
    SharedFolderApplyRequest,
    SharedFolderDesiredState,
)
from appliance.security import ApplianceAuthenticator, resolve_authenticator


def register_native_storage_routes(router: APIRouter) -> None:
    """Attach native read-only routes to an already-authenticated router."""

    @router.get("/status")
    async def status() -> dict[str, Any]:
        return native_storage.status()

    @router.get("/health")
    async def health() -> dict[str, Any]:
        try:
            return native_storage.storage_health()
        except OSError as exc:  # pragma: no cover - defensive
            raise HTTPException(status_code=503, detail="native storage read failed") from exc

    @router.get("/filesystems")
    async def filesystems() -> dict[str, Any]:
        return {"filesystems": native_storage.filesystems(), "readOnly": True}

    @router.get("/topology")
    async def topology() -> dict[str, Any]:
        return native_storage.storage_topology()

    @router.get("/smart/devices")
    async def smart_devices() -> dict[str, Any]:
        return {"devices": native_storage.smart_devices(), "readOnly": True}

    @router.get("/sharing")
    async def sharing() -> dict[str, Any]:
        return {**native_storage.sharing_overview(), "readOnly": True}

    @router.get("/smart")
    async def smart(
        devicefile: str = Query(min_length=5, max_length=256),
    ) -> dict[str, Any]:
        try:
            validated = native_storage.validated_devicefile(devicefile)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"smart": native_storage.smart_report(validated), "readOnly": True}


def create_native_storage_router(
    *,
    jwt_secret: str | None = None,
    authenticator: ApplianceAuthenticator | None = None,
) -> APIRouter:
    """Standalone router with the same operator guard as the OMV router."""
    auth = resolve_authenticator(jwt_secret=jwt_secret, authenticator=authenticator)
    require_operator = auth.operator_dependency()
    router = APIRouter(
        prefix="/api/appliance/storage",
        tags=["appliance", "storage"],
        dependencies=[Depends(require_operator)],
    )
    register_native_storage_routes(router)
    return router


def create_omv_alias_router(
    *,
    jwt_secret: str | None = None,
    authenticator: ApplianceAuthenticator | None = None,
    approval: Any | None = None,
    audit: Any | None = None,
) -> APIRouter:
    """Serve the legacy ``/api/appliance/omv`` paths from the native plane.

    Read paths answer from the host. ``/sharing/folders/plan|apply`` is the
    first native write slice (directory 2770 users-group + registry). The
    remaining mutation endpoints respond 501 until the native write plane
    covers them — they used to mutate an OMV backend that no longer exists.
    """
    from appliance.approval import consume_request_approval, request_intent_id
    from appliance.audit import AuditIntegrityError

    auth = resolve_authenticator(jwt_secret=jwt_secret, authenticator=authenticator)
    require_operator = auth.operator_dependency()
    router = APIRouter(
        prefix="/api/appliance/omv",
        tags=["appliance", "storage"],
        dependencies=[Depends(require_operator)],
    )
    register_native_storage_routes(router)

    removed = "该存储变更操作的原生写面尚未开通"

    def _consume_approval(request: Request, *, actor: str, action: str, target: str) -> None:
        if approval is None:
            if auth.required:
                raise HTTPException(status_code=503, detail="high-risk approval unavailable")
            return
        consume_request_approval(request, approval, actor=actor, action=action, target=target)

    def _record(
        request: Request,
        *,
        actor: str,
        action: str,
        target: str,
        outcome: str,
        metadata: dict[str, Any],
    ) -> None:
        if audit is None:
            if auth.required:
                raise HTTPException(status_code=503, detail="appliance audit unavailable")
            return
        details = dict(metadata)
        intent_id = request_intent_id(request)
        if intent_id:
            details["intentId"] = intent_id
        try:
            audit.record(
                actor=actor, action=action, target=target, outcome=outcome, metadata=details
            )
        except (OSError, AuditIntegrityError) as exc:
            raise HTTPException(status_code=503, detail="appliance audit unavailable") from exc

    @router.post("/sharing/folders/plan")
    async def plan_shared_folder(body: SharedFolderDesiredState) -> dict[str, Any]:
        try:
            return await run_in_threadpool(
                native_storage.plan_shared_folder, body.model_dump(by_alias=True)
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail="原生存储面暂不可用") from exc

    @router.post("/sharing/folders/apply")
    async def apply_shared_folder(
        body: SharedFolderApplyRequest,
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        desired = body.desired.model_dump(by_alias=True)
        try:
            current_plan = await run_in_threadpool(native_storage.plan_shared_folder, desired)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail="原生存储面暂不可用") from exc
        if current_plan.get("planId") != body.plan_id:
            raise HTTPException(
                status_code=409, detail="shared folder plan is stale; preview again"
            )
        if current_plan.get("operation") == "none":
            try:
                return await run_in_threadpool(
                    native_storage.apply_shared_folder, desired, body.plan_id
                )
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except OSError as exc:
                raise HTTPException(status_code=503, detail="原生存储面暂不可用") from exc

        _consume_approval(
            request, actor=actor, action="omv.shared-folder.create", target=body.plan_id
        )
        metadata = {
            "operation": current_plan.get("operation"),
            "mountPointRef": desired["mountPointRef"],
            "name": desired["name"],
            "source": "native",
        }
        _record(
            request,
            action="omv.shared-folder.create",
            actor=actor,
            target=body.plan_id,
            outcome="attempted",
            metadata=metadata,
        )
        try:
            result = await run_in_threadpool(
                native_storage.apply_shared_folder, desired, body.plan_id
            )
        except ValueError as exc:
            _record(
                request,
                action="omv.shared-folder.create",
                actor=actor,
                target=body.plan_id,
                outcome="failed",
                metadata={**metadata, "errorType": type(exc).__name__},
            )
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except OSError as exc:
            _record(
                request,
                action="omv.shared-folder.create",
                actor=actor,
                target=body.plan_id,
                outcome="failed",
                metadata={**metadata, "errorType": type(exc).__name__},
            )
            raise HTTPException(status_code=503, detail="原生存储面暂不可用") from exc
        _record(
            request,
            action="omv.shared-folder.create",
            actor=actor,
            target=body.plan_id,
            outcome="succeeded",
            metadata=metadata,
        )
        return result

    removed_paths = (
        "/accounts/groups/plan",
        "/accounts/groups/apply",
        "/accounts/users/plan",
        "/accounts/users/apply",
        "/accounts/users/password/plan",
        "/accounts/users/password/apply",
        "/sharing/privileges/plan",
        "/sharing/privileges/apply",
        "/sharing/smb/plan",
        "/sharing/smb/apply",
        "/sharing/nfs/plan",
        "/sharing/nfs/apply",
        "/quota/plan",
        "/quota/apply",
    )

    def _removed() -> dict[str, Any]:
        raise HTTPException(status_code=501, detail=removed)

    for path in removed_paths:
        router.add_api_route(
            path,
            _removed,
            methods=["POST"],
            name=f"removed{path.replace('/', '_')}",
        )

    return router


__all__ = [
    "create_native_storage_router",
    "create_omv_alias_router",
    "register_native_storage_routes",
]
