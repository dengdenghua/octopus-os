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

from appliance import (
    mdraid_check_schedule_policy,
    native_storage,
    nut_device_config,
    smart_schedule_policy,
)
from appliance.native_smart import (
    apply_smart_self_test,
    plan_smart_self_test,
    smart_self_test_status,
)
from appliance.native_ups import ups_status
from appliance.omv_models import (
    BtrfsRaid1ApplyRequest,
    BtrfsRaid1DesiredState,
    BtrfsScrubApplyRequest,
    BtrfsScrubDesiredState,
    Ext4VolumeApplyRequest,
    Ext4VolumeDesiredState,
    GroupApplyRequest,
    GroupDesiredState,
    MdRaid1ApplyRequest,
    MdRaid1DesiredState,
    MdRaid1ReplaceApplyRequest,
    MdRaid1ReplaceDesiredState,
    MdRaidCheckApplyRequest,
    MdRaidCheckDesiredState,
    MdRaidCheckSchedulePolicyApplyRequest,
    MdRaidCheckSchedulePolicyDesiredState,
    NfsApplyRequest,
    NfsDesiredState,
    NfsRemoveApplyRequest,
    NfsRemoveDesiredState,
    NutLocalUpsApplyRequest,
    NutLocalUpsDesiredState,
    QuotaApplyRequest,
    QuotaDesiredState,
    SharedFolderApplyRequest,
    SharedFolderDeleteApplyRequest,
    SharedFolderDeleteDesiredState,
    SharedFolderDesiredState,
    SharedFolderDetachApplyRequest,
    SharedFolderDetachDesiredState,
    SharedFolderRenameApplyRequest,
    SharedFolderRenameDesiredState,
    SharePrivilegeApplyRequest,
    SharePrivilegeDesiredState,
    SmartSchedulePolicyApplyRequest,
    SmartSchedulePolicyDesiredState,
    SmartSelfTestApplyRequest,
    SmartSelfTestDesiredState,
    SmbApplyRequest,
    SmbDesiredState,
    UpsShutdownPolicyApplyRequest,
    UpsShutdownPolicyDesiredState,
    UserApplyRequest,
    UserDesiredState,
    UserPasswordApplyRequest,
    UserPasswordDesiredState,
    ZfsMirrorApplyRequest,
    ZfsMirrorDesiredState,
    ZfsMirrorReplaceApplyRequest,
    ZfsMirrorReplaceDesiredState,
    ZfsPoolExportApplyRequest,
    ZfsPoolExportDesiredState,
    ZfsPoolImportApplyRequest,
    ZfsPoolImportDesiredState,
    ZfsScrubApplyRequest,
    ZfsScrubDesiredState,
)
from appliance.security import ApplianceAuthenticator, resolve_authenticator
from appliance.ups_shutdown_policy import apply_policy, plan_policy, policy_status


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

    @router.get("/power/ups")
    async def power_ups() -> dict[str, Any]:
        return await run_in_threadpool(ups_status)

    @router.get("/power/ups/config")
    async def power_ups_config() -> dict[str, Any]:
        try:
            return await run_in_threadpool(nut_device_config.config_status)
        except OSError as exc:
            raise HTTPException(status_code=503, detail="本机 UPS 配置读取失败") from exc

    @router.get("/power/ups/shutdown-policy")
    async def power_ups_shutdown_policy() -> dict[str, Any]:
        try:
            return await run_in_threadpool(policy_status)
        except OSError as exc:
            raise HTTPException(status_code=503, detail="UPS 自动关机策略读取失败") from exc

    @router.get("/filesystems")
    async def filesystems() -> dict[str, Any]:
        return {
            "filesystems": await run_in_threadpool(native_storage.filesystems),
            "readOnly": True,
        }

    @router.get("/topology")
    async def topology() -> dict[str, Any]:
        return await run_in_threadpool(native_storage.storage_topology)

    @router.get("/pools/zfs-mirror/candidates")
    async def zfs_mirror_candidates() -> dict[str, Any]:
        try:
            devices = await run_in_threadpool(native_storage.zfs_mirror_candidates)
        except OSError as exc:
            raise HTTPException(status_code=503, detail="原生 ZFS 候选磁盘探测不可用") from exc
        return {"devices": devices, "readOnly": True, "source": "native"}

    @router.get("/arrays/mdraid1/candidates")
    async def mdraid1_candidates() -> dict[str, Any]:
        try:
            devices = await run_in_threadpool(native_storage.mdraid1_candidates)
        except OSError as exc:
            raise HTTPException(status_code=503, detail="原生 md RAID1 候选磁盘探测不可用") from exc
        return {"devices": devices, "readOnly": True, "source": "native"}

    @router.get("/arrays/mdraid1/replacement-candidates")
    async def mdraid1_replacement_candidates() -> dict[str, Any]:
        try:
            replacements = await run_in_threadpool(native_storage.mdraid1_replacement_candidates)
        except OSError as exc:
            raise HTTPException(status_code=503, detail="原生 md RAID1 换盘候选探测不可用") from exc
        return {"replacements": replacements, "readOnly": True, "source": "native"}

    @router.get("/arrays/mdraid1/maintenance")
    async def mdraid1_maintenance() -> dict[str, Any]:
        try:
            arrays = await run_in_threadpool(native_storage.mdraid_maintenance)
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=503, detail="原生 md RAID1 校验状态不可用") from exc
        return {"arrays": arrays, "readOnly": True, "source": "native"}

    @router.get("/arrays/mdraid1/check/schedule")
    async def mdraid1_check_schedule() -> dict[str, Any]:
        try:
            return await run_in_threadpool(mdraid_check_schedule_policy.policy_status)
        except OSError as exc:
            raise HTTPException(status_code=503, detail="md RAID1 定时校验策略读取失败") from exc

    @router.get("/volumes/ext4/candidates")
    async def ext4_volume_candidates() -> dict[str, Any]:
        try:
            arrays = await run_in_threadpool(native_storage.ext4_volume_candidates)
        except OSError as exc:
            raise HTTPException(status_code=503, detail="原生 EXT4 候选阵列探测不可用") from exc
        return {"arrays": arrays, "readOnly": True, "source": "native"}

    @router.get("/volumes/btrfs-raid1/candidates")
    async def btrfs_raid1_candidates() -> dict[str, Any]:
        try:
            devices = await run_in_threadpool(native_storage.btrfs_raid1_candidates)
        except OSError as exc:
            raise HTTPException(status_code=503, detail="原生 Btrfs RAID1 候选磁盘探测不可用") from exc
        return {"devices": devices, "readOnly": True, "source": "native"}

    @router.get("/volumes/btrfs-raid1/maintenance")
    async def btrfs_scrub_maintenance() -> dict[str, Any]:
        try:
            filesystems = await run_in_threadpool(native_storage.btrfs_scrub_maintenance)
        except OSError as exc:
            raise HTTPException(status_code=503, detail="原生 Btrfs 维护状态不可用") from exc
        return {"filesystems": filesystems, "readOnly": True, "source": "native"}

    @router.get("/pools/zfs-mirror/replacement-candidates")
    async def zfs_mirror_replacement_candidates() -> dict[str, Any]:
        try:
            replacements = await run_in_threadpool(native_storage.zfs_mirror_replacement_candidates)
        except OSError as exc:
            raise HTTPException(status_code=503, detail="原生 ZFS 换盘候选探测不可用") from exc
        return {"replacements": replacements, "readOnly": True, "source": "native"}

    @router.get("/pools/zfs/import-candidates")
    async def zfs_pool_import_candidates() -> dict[str, Any]:
        try:
            pools = await run_in_threadpool(native_storage.importable_zfs_pools)
        except OSError as exc:
            raise HTTPException(status_code=503, detail="原生 ZFS 导入候选探测不可用") from exc
        return {"pools": pools, "readOnly": True, "source": "native"}

    @router.get("/pools/zfs")
    async def zfs_pools() -> dict[str, Any]:
        try:
            pools = await run_in_threadpool(native_storage.exportable_zfs_pools)
        except OSError as exc:
            raise HTTPException(status_code=503, detail="原生 ZFS 存储池探测不可用") from exc
        return {"pools": pools, "readOnly": True, "source": "native"}

    @router.get("/pools/zfs/maintenance")
    async def zfs_pool_maintenance() -> dict[str, Any]:
        try:
            pools = await run_in_threadpool(native_storage.zfs_pool_maintenance)
        except OSError as exc:
            raise HTTPException(status_code=503, detail="原生 ZFS 维护状态探测不可用") from exc
        return {"pools": pools, "readOnly": True, "source": "native"}

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

    @router.get("/smart/self-test")
    async def smart_self_test(
        devicefile: str = Query(min_length=5, max_length=256),
    ) -> dict[str, Any]:
        try:
            return await run_in_threadpool(smart_self_test_status, devicefile)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail="SMART 自检状态读取失败") from exc

    @router.get("/smart/self-test/schedule")
    async def smart_self_test_schedule() -> dict[str, Any]:
        try:
            return await run_in_threadpool(smart_schedule_policy.policy_status)
        except OSError as exc:
            raise HTTPException(status_code=503, detail="SMART 定时自检策略读取失败") from exc


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
        _consume_approval(request, actor=actor, action=action, target=body.plan_id)
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

    @router.post("/sharing/folders/rename/plan")
    async def plan_shared_folder_rename(
        body: SharedFolderRenameDesiredState,
    ) -> dict[str, Any]:
        try:
            return await run_in_threadpool(
                native_storage.plan_shared_folder_rename,
                body.model_dump(by_alias=True),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail="原生存储面暂不可用") from exc

    @router.post("/sharing/folders/rename/apply")
    async def apply_shared_folder_rename_route(
        body: SharedFolderRenameApplyRequest,
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        return await _apply_write(
            request,
            actor=actor,
            action="omv.shared-folder.update",
            plan_fn=native_storage.plan_shared_folder_rename,
            apply_fn=native_storage.apply_shared_folder_rename,
            desired=body.desired.model_dump(by_alias=True),
            plan_id=body.plan_id,
            metadata={
                "sharedFolderRef": body.desired.shared_folder_ref,
                "name": body.desired.name,
                "dataPreserved": True,
            },
        )

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
            return await run_in_threadpool(native_storage.plan_user, body.model_dump(by_alias=True))
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
            return await run_in_threadpool(native_storage.plan_smb, body.model_dump(by_alias=True))
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
            return await run_in_threadpool(native_storage.plan_nfs, body.model_dump(by_alias=True))
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

    # --- Destructive ZFS mirror creation --------------------------------
    @router.post("/pools/zfs-mirror/plan")
    async def plan_zfs_mirror(body: ZfsMirrorDesiredState) -> dict[str, Any]:
        try:
            return await run_in_threadpool(
                native_storage.plan_zfs_mirror, body.model_dump(by_alias=True)
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail="原生 ZFS 存储池暂不可用") from exc

    @router.post("/pools/zfs-mirror/apply")
    async def apply_zfs_mirror_route(
        body: ZfsMirrorApplyRequest,
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        return await _apply_write(
            request,
            actor=actor,
            action="omv.zfs-mirror.create",
            plan_fn=native_storage.plan_zfs_mirror,
            apply_fn=native_storage.apply_zfs_mirror,
            desired=body.desired.model_dump(by_alias=True),
            plan_id=body.plan_id,
            metadata={"name": body.desired.name, "devices": body.desired.devices},
        )

    # --- Destructive Linux md RAID1 creation ----------------------------
    @router.post("/arrays/mdraid1/plan")
    async def plan_mdraid1(body: MdRaid1DesiredState) -> dict[str, Any]:
        try:
            return await run_in_threadpool(
                native_storage.plan_mdraid1,
                body.model_dump(by_alias=True),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail="原生 md RAID1 暂不可用") from exc

    @router.post("/arrays/mdraid1/apply")
    async def apply_mdraid1_route(
        body: MdRaid1ApplyRequest,
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        return await _apply_write(
            request,
            actor=actor,
            action="omv.mdraid1.create",
            plan_fn=native_storage.plan_mdraid1,
            apply_fn=native_storage.apply_mdraid1,
            desired=body.desired.model_dump(by_alias=True),
            plan_id=body.plan_id,
            metadata={"name": body.desired.name, "devices": body.desired.devices},
        )

    # --- Data-preserving repair of one degraded Echo-managed RAID1 -----
    @router.post("/arrays/mdraid1/replace/plan")
    async def plan_mdraid1_replace(body: MdRaid1ReplaceDesiredState) -> dict[str, Any]:
        try:
            return await run_in_threadpool(
                native_storage.plan_mdraid1_replace,
                body.model_dump(by_alias=True),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail="原生 md RAID1 换盘暂不可用") from exc

    @router.post("/arrays/mdraid1/replace/apply")
    async def apply_mdraid1_replace_route(
        body: MdRaid1ReplaceApplyRequest,
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        return await _apply_write(
            request,
            actor=actor,
            action="omv.mdraid1.replace",
            plan_fn=native_storage.plan_mdraid1_replace,
            apply_fn=native_storage.apply_mdraid1_replace,
            desired=body.desired.model_dump(by_alias=True),
            plan_id=body.plan_id,
            metadata={
                "name": body.desired.name,
                "arrayUuid": body.desired.array_uuid,
                "replacementDevice": body.desired.replacement_device,
                "dataPreserved": True,
            },
        )

    # --- Consistency check of a healthy Echo-managed RAID1 -------------
    @router.post("/arrays/mdraid1/check/plan")
    async def plan_mdraid_check(body: MdRaidCheckDesiredState) -> dict[str, Any]:
        try:
            return await run_in_threadpool(
                native_storage.plan_mdraid_check,
                body.model_dump(by_alias=True),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail="原生 md RAID1 校验暂不可用") from exc

    @router.post("/arrays/mdraid1/check/apply")
    async def apply_mdraid_check_route(
        body: MdRaidCheckApplyRequest,
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        return await _apply_write(
            request,
            actor=actor,
            action="omv.mdraid.check.start",
            plan_fn=native_storage.plan_mdraid_check,
            apply_fn=native_storage.apply_mdraid_check,
            desired=body.desired.model_dump(by_alias=True),
            plan_id=body.plan_id,
            metadata={
                "name": body.desired.name,
                "arrayUuid": body.desired.array_uuid,
                "operation": "start",
                "repair": False,
            },
        )

    @router.post("/arrays/mdraid1/check/schedule/plan")
    async def plan_mdraid_check_schedule(
        body: MdRaidCheckSchedulePolicyDesiredState,
    ) -> dict[str, Any]:
        try:
            return await run_in_threadpool(
                mdraid_check_schedule_policy.plan_policy,
                body.model_dump(by_alias=True),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail="md RAID1 定时校验策略暂不可用") from exc

    @router.post("/arrays/mdraid1/check/schedule/apply")
    async def apply_mdraid_check_schedule(
        body: MdRaidCheckSchedulePolicyApplyRequest,
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        return await _apply_write(
            request,
            actor=actor,
            action="storage.mdraid.check.schedule",
            plan_fn=mdraid_check_schedule_policy.plan_policy,
            apply_fn=mdraid_check_schedule_policy.apply_policy,
            desired=body.desired.model_dump(by_alias=True),
            plan_id=body.plan_id,
            metadata={
                "enabled": body.desired.enabled,
                "operation": "check",
                "explicitRepair": False,
                "scope": "echoManagedHealthyRaid1Only",
                "schedule": "monthlyFirstSundayLocal",
            },
        )

    # --- Destructive EXT4 creation on an Echo-managed md RAID1 ---------
    @router.post("/volumes/ext4/plan")
    async def plan_ext4_volume(body: Ext4VolumeDesiredState) -> dict[str, Any]:
        try:
            return await run_in_threadpool(
                native_storage.plan_ext4_volume,
                body.model_dump(by_alias=True),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail="原生 EXT4 卷暂不可用") from exc

    @router.post("/volumes/ext4/apply")
    async def apply_ext4_volume_route(
        body: Ext4VolumeApplyRequest,
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        return await _apply_write(
            request,
            actor=actor,
            action="omv.ext4-volume.create",
            plan_fn=native_storage.plan_ext4_volume,
            apply_fn=native_storage.apply_ext4_volume,
            desired=body.desired.model_dump(by_alias=True),
            plan_id=body.plan_id,
            metadata={
                "arrayUuid": body.desired.array_uuid,
                "name": body.desired.name,
            },
        )

    # --- Destructive Btrfs RAID1 creation on two blank whole disks -----
    @router.post("/volumes/btrfs-raid1/plan")
    async def plan_btrfs_raid1(body: BtrfsRaid1DesiredState) -> dict[str, Any]:
        try:
            return await run_in_threadpool(
                native_storage.plan_btrfs_raid1,
                body.model_dump(by_alias=True),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail="原生 Btrfs RAID1 卷暂不可用") from exc

    @router.post("/volumes/btrfs-raid1/apply")
    async def apply_btrfs_raid1_route(
        body: BtrfsRaid1ApplyRequest,
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        return await _apply_write(
            request,
            actor=actor,
            action="omv.btrfs-raid1.create",
            plan_fn=native_storage.plan_btrfs_raid1,
            apply_fn=native_storage.apply_btrfs_raid1,
            desired=body.desired.model_dump(by_alias=True),
            plan_id=body.plan_id,
            metadata={
                "name": body.desired.name,
                "devices": body.desired.devices,
                "dataProfile": "raid1",
                "metadataProfile": "raid1",
            },
        )

    @router.post("/volumes/btrfs-raid1/scrub/plan")
    async def plan_btrfs_scrub(body: BtrfsScrubDesiredState) -> dict[str, Any]:
        try:
            return await run_in_threadpool(
                native_storage.plan_btrfs_scrub,
                body.model_dump(by_alias=True),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail="原生 Btrfs scrub 暂不可用") from exc

    @router.post("/volumes/btrfs-raid1/scrub/apply")
    async def apply_btrfs_scrub_route(
        body: BtrfsScrubApplyRequest,
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        return await _apply_write(
            request,
            actor=actor,
            action="omv.btrfs.scrub.start",
            plan_fn=native_storage.plan_btrfs_scrub,
            apply_fn=native_storage.apply_btrfs_scrub,
            desired=body.desired.model_dump(by_alias=True),
            plan_id=body.plan_id,
            metadata={
                "filesystemUuid": body.desired.filesystem_uuid,
                "operation": "start",
                "repairFromRedundantCopy": True,
                "force": False,
            },
        )

    @router.post("/pools/zfs-mirror/replace/plan")
    async def plan_zfs_mirror_replace(body: ZfsMirrorReplaceDesiredState) -> dict[str, Any]:
        try:
            return await run_in_threadpool(
                native_storage.plan_zfs_mirror_replace,
                body.model_dump(by_alias=True),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail="原生 ZFS 镜像换盘暂不可用") from exc

    @router.post("/pools/zfs-mirror/replace/apply")
    async def apply_zfs_mirror_replace_route(
        body: ZfsMirrorReplaceApplyRequest,
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        return await _apply_write(
            request,
            actor=actor,
            action="omv.zfs-mirror.replace",
            plan_fn=native_storage.plan_zfs_mirror_replace,
            apply_fn=native_storage.apply_zfs_mirror_replace,
            desired=body.desired.model_dump(by_alias=True),
            plan_id=body.plan_id,
            metadata={
                "name": body.desired.name,
                "poolGuid": body.desired.pool_guid,
                "oldVdevGuid": body.desired.old_vdev_guid,
                "replacementDevice": body.desired.replacement_device,
                "dataPreserved": True,
            },
        )

    # --- Data-preserving ZFS pool export/import -------------------------
    @router.post("/pools/zfs/export/plan")
    async def plan_zfs_pool_export(body: ZfsPoolExportDesiredState) -> dict[str, Any]:
        try:
            return await run_in_threadpool(
                native_storage.plan_zfs_pool_export, body.model_dump(by_alias=True)
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail="原生 ZFS 存储池暂不可用") from exc

    @router.post("/pools/zfs/export/apply")
    async def apply_zfs_pool_export_route(
        body: ZfsPoolExportApplyRequest,
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        return await _apply_write(
            request,
            actor=actor,
            action="omv.zfs-pool.export",
            plan_fn=native_storage.plan_zfs_pool_export,
            apply_fn=native_storage.apply_zfs_pool_export,
            desired=body.desired.model_dump(by_alias=True),
            plan_id=body.plan_id,
            metadata={
                "name": body.desired.name,
                "poolGuid": body.desired.pool_guid,
                "dataPreserved": True,
            },
        )

    @router.post("/pools/zfs/import/plan")
    async def plan_zfs_pool_import(body: ZfsPoolImportDesiredState) -> dict[str, Any]:
        try:
            return await run_in_threadpool(
                native_storage.plan_zfs_pool_import, body.model_dump(by_alias=True)
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail="原生 ZFS 存储池暂不可用") from exc

    @router.post("/pools/zfs/import/apply")
    async def apply_zfs_pool_import_route(
        body: ZfsPoolImportApplyRequest,
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        return await _apply_write(
            request,
            actor=actor,
            action="omv.zfs-pool.import",
            plan_fn=native_storage.plan_zfs_pool_import,
            apply_fn=native_storage.apply_zfs_pool_import,
            desired=body.desired.model_dump(by_alias=True),
            plan_id=body.plan_id,
            metadata={
                "name": body.desired.name,
                "poolGuid": body.desired.pool_guid,
                "mountPolicy": body.desired.mount_policy,
            },
        )

    # --- ZFS scrub maintenance -----------------------------------------
    @router.post("/pools/zfs/scrub/plan")
    async def plan_zfs_scrub(body: ZfsScrubDesiredState) -> dict[str, Any]:
        try:
            return await run_in_threadpool(
                native_storage.plan_zfs_scrub,
                body.model_dump(by_alias=True),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail="原生 ZFS 校验暂不可用") from exc

    @router.post("/pools/zfs/scrub/apply")
    async def apply_zfs_scrub_route(
        body: ZfsScrubApplyRequest,
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        return await _apply_write(
            request,
            actor=actor,
            action="omv.zfs.scrub.start",
            plan_fn=native_storage.plan_zfs_scrub,
            apply_fn=native_storage.apply_zfs_scrub,
            desired=body.desired.model_dump(by_alias=True),
            plan_id=body.plan_id,
            metadata={
                "name": body.desired.name,
                "poolGuid": body.desired.pool_guid,
                "operation": "start",
            },
        )

    # --- Local UPS low-battery shutdown policy -------------------------
    @router.post("/power/ups/config/plan")
    async def plan_local_ups_config(body: NutLocalUpsDesiredState) -> dict[str, Any]:
        try:
            return await run_in_threadpool(
                nut_device_config.plan_config,
                body.model_dump(by_alias=True),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail="本机 UPS 配置暂不可用") from exc

    @router.post("/power/ups/config/apply")
    async def apply_local_ups_config(
        body: NutLocalUpsApplyRequest,
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        return await _apply_write(
            request,
            actor=actor,
            action="power.ups.local-usb.configure",
            plan_fn=nut_device_config.plan_config,
            apply_fn=nut_device_config.apply_config,
            desired=body.desired.model_dump(by_alias=True),
            plan_id=body.plan_id,
            metadata={
                "enabled": body.desired.enabled,
                "driver": body.desired.driver,
                "port": "auto",
                "server": "loopbackOnly",
                "shutdownOwner": "echo-ups-shutdown-guard",
            },
        )

    @router.post("/power/ups/shutdown-policy/plan")
    async def plan_ups_shutdown_policy(
        body: UpsShutdownPolicyDesiredState,
    ) -> dict[str, Any]:
        try:
            return await run_in_threadpool(
                plan_policy,
                body.model_dump(by_alias=True),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail="UPS 自动关机策略暂不可用") from exc

    @router.post("/power/ups/shutdown-policy/apply")
    async def apply_ups_shutdown_policy(
        body: UpsShutdownPolicyApplyRequest,
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        return await _apply_write(
            request,
            actor=actor,
            action="power.ups-shutdown-policy.set",
            plan_fn=plan_policy,
            apply_fn=apply_policy,
            desired=body.desired.model_dump(by_alias=True),
            plan_id=body.plan_id,
            metadata={
                "enabled": body.desired.enabled,
                "requiredConsecutiveSamples": body.desired.required_consecutive_samples,
                "trigger": "FSD or persistent OB+LB",
            },
        )

    # --- SMART whole-disk self-test -----------------------------------
    @router.post("/smart/self-test/schedule/plan")
    async def plan_smart_self_test_schedule(
        body: SmartSchedulePolicyDesiredState,
    ) -> dict[str, Any]:
        try:
            return await run_in_threadpool(
                smart_schedule_policy.plan_policy,
                body.model_dump(by_alias=True),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail="SMART 定时自检策略暂不可用") from exc

    @router.post("/smart/self-test/schedule/apply")
    async def apply_smart_self_test_schedule(
        body: SmartSchedulePolicyApplyRequest,
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        return await _apply_write(
            request,
            actor=actor,
            action="storage.smart.self-test.schedule",
            plan_fn=smart_schedule_policy.plan_policy,
            apply_fn=smart_schedule_policy.apply_policy,
            desired=body.desired.model_dump(by_alias=True),
            plan_id=body.plan_id,
            metadata={
                "enabled": body.desired.enabled,
                "test": "short",
                "schedule": "weeklySundayLocal",
            },
        )

    @router.post("/smart/self-test/plan")
    async def plan_smart_self_test_route(
        body: SmartSelfTestDesiredState,
    ) -> dict[str, Any]:
        try:
            return await run_in_threadpool(
                plan_smart_self_test,
                body.model_dump(by_alias=True),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail="SMART 自检暂不可用") from exc

    @router.post("/smart/self-test/apply")
    async def apply_smart_self_test_route(
        body: SmartSelfTestApplyRequest,
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        return await _apply_write(
            request,
            actor=actor,
            action="storage.smart.self-test.start",
            plan_fn=plan_smart_self_test,
            apply_fn=apply_smart_self_test,
            desired=body.desired.model_dump(by_alias=True),
            plan_id=body.plan_id,
            metadata={
                "devicefile": body.desired.devicefile,
                "test": body.desired.test,
                "captive": False,
                "abort": False,
            },
        )

    return router


__all__ = [
    "create_native_storage_router",
    "create_omv_alias_router",
    "register_native_storage_routes",
]
