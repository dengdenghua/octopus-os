"""Shared-folder, privilege, SMB, and NFS routes for the Echo OMV API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from appliance.omv_client import OmvControlRejected, OmvUnavailable
from appliance.omv_models import (
    NfsApplyRequest,
    NfsDesiredState,
    SharedFolderApplyRequest,
    SharedFolderDesiredState,
    SharePrivilegeApplyRequest,
    SharePrivilegeDesiredState,
    SmbApplyRequest,
    SmbDesiredState,
)
from appliance.omv_route_context import OmvRouteContext


def register_omv_sharing_routes(router: APIRouter, context: OmvRouteContext) -> None:
    """Attach shared-folder and network-sharing mutation routes."""
    omv = context.omv
    require_auth = context.require_auth

    @router.post("/sharing/folders/plan")
    async def plan_shared_folder(body: SharedFolderDesiredState) -> dict:
        try:
            return await run_in_threadpool(
                omv.plan_shared_folder,
                body.model_dump(by_alias=True),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OmvControlRejected as exc:
            raise context.control_error(exc) from exc
        except OmvUnavailable as exc:
            raise HTTPException(
                status_code=503,
                detail="OMV shared folder control is unavailable",
            ) from exc

    @router.post("/sharing/folders/apply")
    async def apply_shared_folder(
        body: SharedFolderApplyRequest,
        request: Request,
        actor: str = Depends(require_auth),
    ) -> dict:
        desired = body.desired.model_dump(by_alias=True)
        try:
            current_plan = await run_in_threadpool(omv.plan_shared_folder, desired)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OmvControlRejected as exc:
            raise context.control_error(exc) from exc
        except OmvUnavailable as exc:
            raise HTTPException(
                status_code=503,
                detail="OMV shared folder control is unavailable",
            ) from exc
        if current_plan.get("planId") != body.plan_id:
            raise HTTPException(
                status_code=409, detail="shared folder plan is stale; preview again"
            )
        if current_plan.get("operation") == "none":
            try:
                return await run_in_threadpool(
                    omv.apply_shared_folder,
                    desired,
                    body.plan_id,
                )
            except OmvControlRejected as exc:
                raise context.control_error(exc) from exc
            except OmvUnavailable as exc:
                raise HTTPException(
                    status_code=503,
                    detail="OMV shared folder control is unavailable",
                ) from exc

        context.consume_approval(
            request,
            actor=actor,
            action="omv.shared-folder.create",
            target=body.plan_id,
        )
        metadata = {
            "operation": current_plan.get("operation"),
            "mountPointRef": desired["mountPointRef"],
            "name": desired["name"],
            "changeFields": [
                change.get("field")
                for change in current_plan.get("changes", [])
                if isinstance(change, dict)
            ],
        }
        context.record(
            request,
            action="omv.shared-folder.create",
            actor=actor,
            target=body.plan_id,
            outcome="attempted",
            metadata=metadata,
        )
        try:
            result = await run_in_threadpool(
                omv.apply_shared_folder,
                desired,
                body.plan_id,
            )
        except (OmvControlRejected, OmvUnavailable, ValueError) as exc:
            context.record(
                request,
                action="omv.shared-folder.create",
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
                detail="OMV shared folder control is unavailable",
            ) from exc
        context.record(
            request,
            action="omv.shared-folder.create",
            actor=actor,
            target=body.plan_id,
            outcome="succeeded",
            metadata=metadata,
        )
        return result

    @router.post("/sharing/privileges/plan")
    async def plan_share_privilege(body: SharePrivilegeDesiredState) -> dict:
        try:
            return await run_in_threadpool(
                omv.plan_share_privilege,
                body.model_dump(by_alias=True),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OmvControlRejected as exc:
            raise context.control_error(exc) from exc
        except OmvUnavailable as exc:
            raise HTTPException(
                status_code=503,
                detail="OMV share privilege control is unavailable",
            ) from exc

    @router.post("/sharing/privileges/apply")
    async def apply_share_privilege(
        body: SharePrivilegeApplyRequest,
        request: Request,
        actor: str = Depends(require_auth),
    ) -> dict:
        desired = body.desired.model_dump(by_alias=True)
        try:
            current_plan = await run_in_threadpool(omv.plan_share_privilege, desired)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OmvControlRejected as exc:
            raise context.control_error(exc) from exc
        except OmvUnavailable as exc:
            raise HTTPException(
                status_code=503,
                detail="OMV share privilege control is unavailable",
            ) from exc
        if current_plan.get("planId") != body.plan_id:
            raise HTTPException(
                status_code=409,
                detail="share privilege plan is stale; preview again",
            )
        if current_plan.get("operation") == "none":
            try:
                return await run_in_threadpool(
                    omv.apply_share_privilege,
                    desired,
                    body.plan_id,
                )
            except OmvControlRejected as exc:
                raise context.control_error(exc) from exc
            except OmvUnavailable as exc:
                raise HTTPException(
                    status_code=503,
                    detail="OMV share privilege control is unavailable",
                ) from exc

        context.consume_approval(
            request,
            actor=actor,
            action="omv.share-privilege.apply",
            target=body.plan_id,
        )
        principal = current_plan.get("principal", {})
        metadata = {
            "operation": current_plan.get("operation"),
            "sharedFolderRef": desired["sharedFolderRef"],
            "principalType": desired["principalType"],
            "principalName": desired["principalName"],
            "beforePermission": principal.get("before"),
            "afterPermission": principal.get("after"),
            "changeFields": [
                change.get("field")
                for change in current_plan.get("changes", [])
                if isinstance(change, dict)
            ],
        }
        context.record(
            request,
            action="omv.share-privilege.apply",
            actor=actor,
            target=body.plan_id,
            outcome="attempted",
            metadata=metadata,
        )
        try:
            result = await run_in_threadpool(
                omv.apply_share_privilege,
                desired,
                body.plan_id,
            )
        except (OmvControlRejected, OmvUnavailable, ValueError) as exc:
            context.record(
                request,
                action="omv.share-privilege.apply",
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
                detail="OMV share privilege control is unavailable",
            ) from exc
        context.record(
            request,
            action="omv.share-privilege.apply",
            actor=actor,
            target=body.plan_id,
            outcome="succeeded",
            metadata=metadata,
        )
        return result

    @router.post("/sharing/smb/plan")
    async def plan_smb_share(body: SmbDesiredState) -> dict:
        try:
            return await run_in_threadpool(
                omv.plan_smb_share,
                body.model_dump(by_alias=True),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OmvControlRejected as exc:
            raise context.control_error(exc) from exc
        except OmvUnavailable as exc:
            raise HTTPException(status_code=503, detail="OMV SMB control is unavailable") from exc

    @router.post("/sharing/smb/apply")
    async def apply_smb_share(
        body: SmbApplyRequest,
        request: Request,
        actor: str = Depends(require_auth),
    ) -> dict:
        desired = body.desired.model_dump(by_alias=True)
        try:
            current_plan = await run_in_threadpool(omv.plan_smb_share, desired)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OmvControlRejected as exc:
            raise context.control_error(exc) from exc
        except OmvUnavailable as exc:
            raise HTTPException(status_code=503, detail="OMV SMB control is unavailable") from exc
        if current_plan.get("planId") != body.plan_id:
            raise HTTPException(status_code=409, detail="SMB plan is stale; preview again")
        if current_plan.get("operation") == "none":
            try:
                return await run_in_threadpool(omv.apply_smb_share, desired, body.plan_id)
            except OmvControlRejected as exc:
                raise context.control_error(exc) from exc
            except OmvUnavailable as exc:
                raise HTTPException(
                    status_code=503,
                    detail="OMV SMB control is unavailable",
                ) from exc

        context.consume_approval(
            request,
            actor=actor,
            action="omv.smb.apply",
            target=body.plan_id,
        )
        metadata = {
            "operation": current_plan.get("operation"),
            "sharedFolderRef": desired["sharedFolderRef"],
            "changeFields": [
                change.get("field")
                for change in current_plan.get("changes", [])
                if isinstance(change, dict)
            ],
        }
        context.record(
            request,
            action="omv.smb.apply",
            actor=actor,
            target=body.plan_id,
            outcome="attempted",
            metadata=metadata,
        )
        try:
            result = await run_in_threadpool(omv.apply_smb_share, desired, body.plan_id)
        except (OmvControlRejected, OmvUnavailable, ValueError) as exc:
            context.record(
                request,
                action="omv.smb.apply",
                actor=actor,
                target=body.plan_id,
                outcome="failed",
                metadata={**metadata, "errorType": type(exc).__name__},
            )
            if isinstance(exc, OmvControlRejected):
                raise context.control_error(exc) from exc
            if isinstance(exc, ValueError):
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            raise HTTPException(status_code=503, detail="OMV SMB control is unavailable") from exc
        context.record(
            request,
            action="omv.smb.apply",
            actor=actor,
            target=body.plan_id,
            outcome="succeeded",
            metadata=metadata,
        )
        return result

    @router.post("/sharing/nfs/plan")
    async def plan_nfs_share(body: NfsDesiredState) -> dict:
        try:
            return await run_in_threadpool(
                omv.plan_nfs_share,
                body.model_dump(by_alias=True),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OmvControlRejected as exc:
            raise context.control_error(exc) from exc
        except OmvUnavailable as exc:
            raise HTTPException(status_code=503, detail="OMV NFS control is unavailable") from exc

    @router.post("/sharing/nfs/apply")
    async def apply_nfs_share(
        body: NfsApplyRequest,
        request: Request,
        actor: str = Depends(require_auth),
    ) -> dict:
        desired = body.desired.model_dump(by_alias=True)
        try:
            current_plan = await run_in_threadpool(omv.plan_nfs_share, desired)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OmvControlRejected as exc:
            raise context.control_error(exc) from exc
        except OmvUnavailable as exc:
            raise HTTPException(status_code=503, detail="OMV NFS control is unavailable") from exc
        if current_plan.get("planId") != body.plan_id:
            raise HTTPException(status_code=409, detail="NFS plan is stale; preview again")
        if current_plan.get("operation") == "none":
            try:
                return await run_in_threadpool(omv.apply_nfs_share, desired, body.plan_id)
            except OmvControlRejected as exc:
                raise context.control_error(exc) from exc
            except OmvUnavailable as exc:
                raise HTTPException(
                    status_code=503,
                    detail="OMV NFS control is unavailable",
                ) from exc

        context.consume_approval(
            request,
            actor=actor,
            action="omv.nfs.apply",
            target=body.plan_id,
        )
        metadata = {
            "operation": current_plan.get("operation"),
            "sharedFolderRef": desired["sharedFolderRef"],
            "clientCidr": desired["clientCidr"],
            "changeFields": [
                change.get("field")
                for change in current_plan.get("changes", [])
                if isinstance(change, dict)
            ],
        }
        context.record(
            request,
            action="omv.nfs.apply",
            actor=actor,
            target=body.plan_id,
            outcome="attempted",
            metadata=metadata,
        )
        try:
            result = await run_in_threadpool(omv.apply_nfs_share, desired, body.plan_id)
        except (OmvControlRejected, OmvUnavailable, ValueError) as exc:
            context.record(
                request,
                action="omv.nfs.apply",
                actor=actor,
                target=body.plan_id,
                outcome="failed",
                metadata={**metadata, "errorType": type(exc).__name__},
            )
            if isinstance(exc, OmvControlRejected):
                raise context.control_error(exc) from exc
            if isinstance(exc, ValueError):
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            raise HTTPException(status_code=503, detail="OMV NFS control is unavailable") from exc
        context.record(
            request,
            action="omv.nfs.apply",
            actor=actor,
            target=body.plan_id,
            outcome="succeeded",
            metadata=metadata,
        )
        return result


__all__ = ["register_omv_sharing_routes"]
