"""Native storage routes (no OpenMediaVault required).

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
    GroupApplyRequest,
    GroupDesiredState,
    NfsApplyRequest,
    NfsDesiredState,
    NfsRemoveApplyRequest,
    NfsRemoveDesiredState,
    QuotaApplyRequest,
    QuotaDesiredState,
    SharedFolderApplyRequest,
    SharedFolderDeleteApplyRequest,
    SharedFolderDeleteDesiredState,
    SharedFolderDesiredState,
    SharedFolderDetachApplyRequest,
    SharedFolderDetachDesiredState,
    SharePrivilegeApplyRequest,
    SharePrivilegeDesiredState,
    SmbApplyRequest,
    SmbDesiredState,
    UserApplyRequest,
    UserDesiredState,
    UserPasswordApplyRequest,
    UserPasswordDesiredState,
)
from appliance.security import ApplianceAuthenticator, resolve_authenticator


def register_native_storage_routes(router: APIRouter) -> None:
    """Attach native read-only routes to an already-authenticated router."""

    @router.get("/status")
    async def status() -> dict[str, Any]:
        return await run_in_threadpool(native_storage.status)

    @router.get("/health")
    async def health() -> dict[str, Any]:
        try:
            return await run_in_threadpool(native_storage.storage_health)
        except OSError as exc:  # pragma: no cover - defensive
            raise HTTPException(status_code=503, detail="native storage read failed") from exc

    @router.get("/filesystems")
    async def filesystems() -> dict[str, Any]:
        return {
            "filesystems": await run_in_threadpool(native_storage.filesystems),
            "readOnly": True,
        }

    @router.get("/topology")
    async def topology() -> dict[str, Any]:
        return await run_in_threadpool(native_storage.storage_topology)

    @router.get("/smart/devices")
    async def smart_devices() -> dict[str, Any]:
        return {"devices": await run_in_threadpool(native_storage.smart_devices), "readOnly": True}

    @router.get("/sharing")
    async def sharing() -> dict[str, Any]:
        return {**await run_in_threadpool(native_storage.sharing_overview), "readOnly": True}

    @router.get("/sharing/{share_uuid}/privileges")
    async def share_privileges(share_uuid: str) -> dict[str, Any]:
        try:
            privileges = await run_in_threadpool(native_storage.share_privileges, share_uuid)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail="原生共享权限读取失败") from exc
        return {"privileges": privileges, "readOnly": True}

    @router.get("/smart")
    async def smart(
        devicefile: str = Query(min_length=5, max_length=256),
    ) -> dict[str, Any]:
        try:
            validated = native_storage.validated_devicefile(devicefile)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "smart": await run_in_threadpool(native_storage.smart_report, validated),
            "readOnly": True,
        }


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

    Read paths answer from the host. Mutation routes expose only implemented,
    narrow desired/plan/apply slices and retain the OMV-compatible URL and
    approval contract used by the appliance UI.
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

        action = (
            "omv.shared-folder.update"
            if current_plan.get("operation") == "update"
            else "omv.shared-folder.create"
        )
        _consume_approval(
            request, actor=actor, action=action, target=body.plan_id
        )
        metadata = {
            "operation": current_plan.get("operation"),
            "mountPointRef": desired["mountPointRef"],
            "name": desired["name"],
            "source": "native",
        }
        _record(
            request,
            action=action,
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
                action=action,
                actor=actor,
                target=body.plan_id,
                outcome="failed",
                metadata={**metadata, "errorType": type(exc).__name__},
            )
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except OSError as exc:
            _record(
                request,
                action=action,
                actor=actor,
                target=body.plan_id,
                outcome="failed",
                metadata={**metadata, "errorType": type(exc).__name__},
            )
            raise HTTPException(status_code=503, detail="原生存储面暂不可用") from exc
        _record(
            request,
            action=action,
            actor=actor,
            target=body.plan_id,
            outcome="succeeded",
            metadata=metadata,
        )
        return result

    @router.post("/sharing/folders/detach/plan")
    async def plan_shared_folder_detach(
        body: SharedFolderDetachDesiredState,
    ) -> dict[str, Any]:
        try:
            return await run_in_threadpool(
                native_storage.plan_shared_folder_detach,
                body.model_dump(by_alias=True),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail="原生存储面暂不可用") from exc

    @router.post("/sharing/folders/detach/apply")
    async def apply_shared_folder_detach_route(
        body: SharedFolderDetachApplyRequest,
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        return await _apply_write(
            request,
            actor=actor,
            action="omv.shared-folder.detach",
            plan_fn=native_storage.plan_shared_folder_detach,
            apply_fn=native_storage.apply_shared_folder_detach,
            desired=body.desired.model_dump(by_alias=True),
            plan_id=body.plan_id,
            metadata={
                "sharedFolderRef": body.desired.shared_folder_ref,
                "preserveData": body.desired.preserve_data,
            },
        )

    @router.post("/sharing/folders/delete/plan")
    async def plan_shared_folder_delete(
        body: SharedFolderDeleteDesiredState,
    ) -> dict[str, Any]:
        try:
            return await run_in_threadpool(
                native_storage.plan_shared_folder_delete,
                body.model_dump(by_alias=True),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail="原生存储面暂不可用") from exc

    @router.post("/sharing/folders/delete/apply")
    async def apply_shared_folder_delete_route(
        body: SharedFolderDeleteApplyRequest,
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        return await _apply_write(
            request,
            actor=actor,
            action="omv.shared-folder.delete",
            plan_fn=native_storage.plan_shared_folder_delete,
            apply_fn=native_storage.apply_shared_folder_delete,
            desired=body.desired.model_dump(by_alias=True),
            plan_id=body.plan_id,
            metadata={
                "sharedFolderRef": body.desired.shared_folder_ref,
                "emptyOnly": body.desired.empty_only,
            },
        )

    async def _apply_write(
        request: Request,
        *,
        actor: str,
        action: str,
        plan_fn: Any,
        apply_fn: Any,
        desired: dict[str, Any],
        plan_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Shared stall/approval/audit envelope for every native write slice."""
        try:
            current_plan = await run_in_threadpool(plan_fn, desired)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail="原生存储面暂不可用") from exc
        if current_plan.get("planId") != plan_id:
            raise HTTPException(status_code=409, detail=f"{action} plan is stale; preview again")
        if current_plan.get("operation") in ("none",):
            try:
                return await run_in_threadpool(apply_fn, desired, plan_id)
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except OSError as exc:
                raise HTTPException(status_code=503, detail="原生存储面暂不可用") from exc
        _consume_approval(request, actor=actor, action=action, target=plan_id)
        audit_metadata = {
            "operation": current_plan.get("operation"),
            "source": "native",
            **(metadata or {}),
        }
        _record(
            request,
            action=action,
            actor=actor,
            target=plan_id,
            outcome="attempted",
            metadata=audit_metadata,
        )
        try:
            result = await run_in_threadpool(apply_fn, desired, plan_id)
        except ValueError as exc:
            _record(
                request,
                action=action,
                actor=actor,
                target=plan_id,
                outcome="failed",
                metadata={**audit_metadata, "errorType": type(exc).__name__},
            )
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except OSError as exc:
            _record(
                request,
                action=action,
                actor=actor,
                target=plan_id,
                outcome="failed",
                metadata={**audit_metadata, "errorType": type(exc).__name__},
            )
            raise HTTPException(status_code=503, detail="原生存储面暂不可用") from exc
        _record(
            request,
            action=action,
            actor=actor,
            target=plan_id,
            outcome="succeeded",
            metadata=audit_metadata,
        )
        return result

    # --- Group creation --------------------------------------------------
    @router.post("/accounts/groups/plan")
    async def plan_group(body: GroupDesiredState) -> dict[str, Any]:
        try:
            return await run_in_threadpool(
                native_storage.plan_group, body.model_dump(by_alias=True)
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail="原生存储面暂不可用") from exc

    @router.post("/accounts/groups/apply")
    async def apply_group_route(
        body: GroupApplyRequest,
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        return await _apply_write(
            request,
            actor=actor,
            action="omv.group.create",
            plan_fn=native_storage.plan_group,
            apply_fn=native_storage.apply_group,
            desired=body.desired.model_dump(by_alias=True),
            plan_id=body.plan_id,
        )

    # --- User creation ---------------------------------------------------
    @router.post("/accounts/users/plan")
    async def plan_user(body: UserDesiredState) -> dict[str, Any]:
        try:
            return await run_in_threadpool(
                native_storage.plan_user, body.model_dump(by_alias=True)
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail="原生存储面暂不可用") from exc

    @router.post("/accounts/users/apply")
    async def apply_user_route(
        body: UserApplyRequest,
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        return await _apply_write(
            request,
            actor=actor,
            action="omv.user.create",
            plan_fn=native_storage.plan_user,
            apply_fn=native_storage.apply_user,
            desired=body.desired.model_dump(by_alias=True),
            plan_id=body.plan_id,
        )

    # --- User password reset --------------------------------------------
    @router.post("/accounts/users/password/plan")
    async def plan_user_password(body: UserPasswordDesiredState) -> dict[str, Any]:
        try:
            return await run_in_threadpool(
                native_storage.plan_user_password, body.model_dump(by_alias=True)
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail="原生存储面暂不可用") from exc

    @router.post("/accounts/users/password/apply")
    async def apply_user_password_route(
        body: UserPasswordApplyRequest,
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        return await _apply_write(
            request,
            actor=actor,
            action="omv.user.password.reset",
            plan_fn=native_storage.plan_user_password,
            apply_fn=native_storage.apply_user_password,
            desired=body.desired.model_dump(by_alias=True),
            plan_id=body.plan_id,
        )

    # --- Shared-folder POSIX ACL ---------------------------------------
    @router.post("/sharing/privileges/plan")
    async def plan_share_privilege(body: SharePrivilegeDesiredState) -> dict[str, Any]:
        try:
            return await run_in_threadpool(
                native_storage.plan_share_privilege, body.model_dump(by_alias=True)
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail="原生存储面暂不可用") from exc

    @router.post("/sharing/privileges/apply")
    async def apply_share_privilege_route(
        body: SharePrivilegeApplyRequest,
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        return await _apply_write(
            request,
            actor=actor,
            action="omv.share-privilege.apply",
            plan_fn=native_storage.plan_share_privilege,
            apply_fn=native_storage.apply_share_privilege,
            desired=body.desired.model_dump(by_alias=True),
            plan_id=body.plan_id,
            metadata={
                "sharedFolderRef": body.desired.shared_folder_ref,
                "principalType": body.desired.principal_type,
                "principalName": body.desired.principal_name,
                "permission": body.desired.permission,
            },
        )

    # --- SMB usershare ---------------------------------------------------
    @router.post("/sharing/smb/plan")
    async def plan_smb(body: SmbDesiredState) -> dict[str, Any]:
        try:
            return await run_in_threadpool(
                native_storage.plan_smb, body.model_dump(by_alias=True)
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail="原生存储面暂不可用") from exc

    @router.post("/sharing/smb/apply")
    async def apply_smb_route(
        body: SmbApplyRequest,
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        return await _apply_write(
            request,
            actor=actor,
            action="omv.smb.share",
            plan_fn=native_storage.plan_smb,
            apply_fn=native_storage.apply_smb,
            desired=body.desired.model_dump(by_alias=True),
            plan_id=body.plan_id,
        )

    # --- NFS private-network export ------------------------------------
    @router.post("/sharing/nfs/plan")
    async def plan_nfs(body: NfsDesiredState) -> dict[str, Any]:
        try:
            return await run_in_threadpool(
                native_storage.plan_nfs, body.model_dump(by_alias=True)
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail="原生存储面暂不可用") from exc

    @router.post("/sharing/nfs/apply")
    async def apply_nfs_route(
        body: NfsApplyRequest,
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        return await _apply_write(
            request,
            actor=actor,
            action="omv.nfs.apply",
            plan_fn=native_storage.plan_nfs,
            apply_fn=native_storage.apply_nfs,
            desired=body.desired.model_dump(by_alias=True),
            plan_id=body.plan_id,
            metadata={
                "sharedFolderRef": body.desired.shared_folder_ref,
                "clientCidr": body.desired.client_cidr,
                "readOnly": body.desired.read_only,
            },
        )

    @router.post("/sharing/nfs/remove/plan")
    async def plan_nfs_remove(
        body: NfsRemoveDesiredState,
    ) -> dict[str, Any]:
        try:
            return await run_in_threadpool(
                native_storage.plan_nfs_remove,
                body.model_dump(by_alias=True),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail="原生存储面暂不可用") from exc

    @router.post("/sharing/nfs/remove/apply")
    async def apply_nfs_remove_route(
        body: NfsRemoveApplyRequest,
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        return await _apply_write(
            request,
            actor=actor,
            action="omv.nfs.remove",
            plan_fn=native_storage.plan_nfs_remove,
            apply_fn=native_storage.apply_nfs_remove,
            desired=body.desired.model_dump(by_alias=True),
            plan_id=body.plan_id,
            metadata={
                "sharedFolderRef": body.desired.shared_folder_ref,
                "clientCidr": body.desired.client_cidr,
            },
        )

    # --- ZFS quota -------------------------------------------------------
    @router.post("/quota/plan")
    async def plan_quota(body: QuotaDesiredState) -> dict[str, Any]:
        try:
            return await run_in_threadpool(
                native_storage.plan_quota, body.model_dump(by_alias=True)
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail="原生存储面暂不可用") from exc

    @router.post("/quota/apply")
    async def apply_quota_route(
        body: QuotaApplyRequest,
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        return await _apply_write(
            request,
            actor=actor,
            action="omv.quota.set",
            plan_fn=native_storage.plan_quota,
            apply_fn=native_storage.apply_quota,
            desired=body.desired.model_dump(by_alias=True),
            plan_id=body.plan_id,
        )

    return router


__all__ = [
    "create_native_storage_router",
    "create_omv_alias_router",
    "register_native_storage_routes",
]
