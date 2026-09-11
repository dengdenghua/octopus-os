"""Authenticated management API for native NAS data backup scheduling."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from starlette.concurrency import run_in_threadpool

from appliance import nas_backup_credential_policy as credential_policy
from appliance import nas_backup_remote_policy as remote_policy
from appliance import nas_backup_restore_policy as restore_policy
from appliance import nas_backup_schedule_policy as policy
from appliance.approval import consume_request_approval, request_intent_id
from appliance.audit import AuditIntegrityError
from appliance.security import ApplianceAuthenticator, resolve_authenticator
from deploy.appliance import external_storage
from deploy.appliance import nas_data_backup_schedule_runner as schedule_runner

ACTION = "storage.nas-backup.schedule"
CREDENTIAL_ACTION = "storage.nas-backup.credential.provision"
CREDENTIAL_ROTATION_ACTION = "storage.nas-backup.credential.rotate"
RESTORE_ACTION = "storage.nas-backup.restore"
REMOTE_ACTION = "storage.nas-backup.remote.configure"


class NasBackupCredentialDesiredState(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: Literal["echo.nas-data-backup-credential-desired.v1"] = Field(
        default=credential_policy.DESIRED_SCHEMA,
        alias="schema",
    )
    mode: Literal["initialize", "connect"]
    repository: str = Field(min_length=2, max_length=4096)
    repository_mount: str = Field(
        min_length=2,
        max_length=4096,
        alias="repositoryMount",
    )
    password: SecretStr = Field(min_length=12, max_length=4096)

    def to_wire(self) -> dict[str, Any]:
        return {
            "schema": self.schema_name,
            "mode": self.mode,
            "repository": self.repository,
            "repositoryMount": self.repository_mount,
            "password": self.password.get_secret_value(),
        }


class NasBackupCredentialApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    desired: NasBackupCredentialDesiredState
    plan_id: str = Field(pattern=r"^[0-9a-f]{64}$", alias="planId")


class NasBackupCredentialRotationDesiredState(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: Literal["echo.nas-data-backup-credential-rotation-desired.v1"] = Field(
        default=credential_policy.ROTATION_DESIRED_SCHEMA,
        alias="schema",
    )
    repository: str = Field(min_length=2, max_length=4096)
    repository_mount: str = Field(
        min_length=2,
        max_length=4096,
        alias="repositoryMount",
    )
    current_password: SecretStr = Field(
        min_length=12,
        max_length=4096,
        alias="currentPassword",
    )
    new_password: SecretStr = Field(
        min_length=12,
        max_length=4096,
        alias="newPassword",
    )

    def to_wire(self) -> dict[str, Any]:
        return {
            "schema": self.schema_name,
            "repository": self.repository,
            "repositoryMount": self.repository_mount,
            "currentPassword": self.current_password.get_secret_value(),
            "newPassword": self.new_password.get_secret_value(),
        }


class NasBackupCredentialRotationApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    desired: NasBackupCredentialRotationDesiredState
    plan_id: str = Field(pattern=r"^[0-9a-f]{64}$", alias="planId")


class NasBackupScheduleDesiredState(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: Literal["echo.nas-data-backup-schedule.v1"] = Field(
        default=policy.DESIRED_SCHEMA,
        alias="schema",
    )
    enabled: bool = Field(strict=True)
    repository: str | None = Field(default=None, min_length=2, max_length=4096)
    repository_mount: str | None = Field(
        default=None,
        min_length=2,
        max_length=4096,
        alias="repositoryMount",
    )

    @model_validator(mode="after")
    def validate_desired(self) -> NasBackupScheduleDesiredState:
        try:
            policy._desired(self.model_dump(by_alias=True))
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        return self


class NasBackupScheduleApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    desired: NasBackupScheduleDesiredState
    plan_id: str = Field(pattern=r"^[0-9a-f]{64}$", alias="planId")


class NasBackupS3RemoteCreateDesiredState(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: Literal["echo.nas-backup-remote-desired.v1"] = Field(
        default=remote_policy.DESIRED_SCHEMA,
        alias="schema",
    )
    operation: Literal["create"]
    remote_id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,31}$", alias="remoteId")
    label: str = Field(min_length=1, max_length=96)
    endpoint: str = Field(min_length=8, max_length=2048)
    region: str = Field(min_length=1, max_length=63)
    bucket: str = Field(min_length=3, max_length=63)
    prefix: str = Field(default="", max_length=1024)
    access_key_id: SecretStr = Field(min_length=3, max_length=256, alias="accessKeyId")
    secret_access_key: SecretStr = Field(
        min_length=8,
        max_length=4096,
        alias="secretAccessKey",
    )

    def to_wire(self) -> dict[str, Any]:
        return {
            "schema": self.schema_name,
            "operation": self.operation,
            "remoteId": self.remote_id,
            "label": self.label,
            "endpoint": self.endpoint,
            "region": self.region,
            "bucket": self.bucket,
            "prefix": self.prefix,
            "accessKeyId": self.access_key_id.get_secret_value(),
            "secretAccessKey": self.secret_access_key.get_secret_value(),
        }


class NasBackupRemoteRemoveDesiredState(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: Literal["echo.nas-backup-remote-desired.v1"] = Field(
        default=remote_policy.DESIRED_SCHEMA,
        alias="schema",
    )
    operation: Literal["remove"]
    remote_id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,31}$", alias="remoteId")

    def to_wire(self) -> dict[str, Any]:
        return {
            "schema": self.schema_name,
            "operation": self.operation,
            "remoteId": self.remote_id,
        }


NasBackupRemoteDesiredState = Annotated[
    NasBackupS3RemoteCreateDesiredState | NasBackupRemoteRemoveDesiredState,
    Field(discriminator="operation"),
]


class NasBackupRemoteApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    desired: NasBackupRemoteDesiredState
    plan_id: str = Field(pattern=r"^[0-9a-f]{64}$", alias="planId")


class NasBackupRestoreRepositoryState(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: Literal["echo.nas-data-backup-restore-repository.v1"] = Field(
        default=restore_policy.REPOSITORY_SCHEMA,
        alias="schema",
    )
    repository: str = Field(min_length=2, max_length=4096)
    repository_mount: str = Field(
        min_length=2,
        max_length=4096,
        alias="repositoryMount",
    )

    @model_validator(mode="after")
    def validate_repository(self) -> NasBackupRestoreRepositoryState:
        try:
            restore_policy._repository(self.model_dump(by_alias=True))
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        return self


class NasBackupRestoreTargetBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    source_shared_folder_ref: str = Field(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
        alias="sourceSharedFolderRef",
    )
    target_shared_folder_ref: str = Field(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
        alias="targetSharedFolderRef",
    )


class NasBackupRestoreDesiredState(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: Literal["echo.nas-data-backup-restore-desired.v2"] = Field(
        default=restore_policy.DESIRED_SCHEMA,
        alias="schema",
    )
    selector: str = Field(min_length=1, max_length=64)
    repository: str = Field(min_length=2, max_length=4096)
    repository_mount: str = Field(
        min_length=2,
        max_length=4096,
        alias="repositoryMount",
    )
    targets: list[NasBackupRestoreTargetBinding] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def validate_desired(self) -> NasBackupRestoreDesiredState:
        try:
            restore_policy._desired(self.model_dump(by_alias=True))
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        return self


class NasBackupRestoreApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    desired: NasBackupRestoreDesiredState
    plan_id: str = Field(pattern=r"^[0-9a-f]{64}$", alias="planId")
    confirmation: str = Field(min_length=1, max_length=512)


def create_nas_backup_router(
    *,
    jwt_secret: str | None = None,
    authenticator: ApplianceAuthenticator | None = None,
    approval: Any | None = None,
    audit: Any | None = None,
    credential_binding_key: bytes | None = None,
) -> APIRouter:
    auth = resolve_authenticator(jwt_secret=jwt_secret, authenticator=authenticator)
    require_operator = auth.operator_dependency()
    binding_key = credential_binding_key or credential_policy.DEFAULT_BINDING_KEY
    router = APIRouter(
        prefix="/api/appliance/storage/backups",
        tags=["appliance", "storage", "backup"],
        dependencies=[Depends(require_operator)],
    )

    def record(
        request: Request,
        *,
        actor: str,
        action: str = ACTION,
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
                actor=actor,
                action=action,
                target=target,
                outcome=outcome,
                metadata=details,
            )
        except (OSError, AuditIntegrityError) as exc:
            raise HTTPException(status_code=503, detail="appliance audit unavailable") from exc

    @router.get("/schedule")
    async def schedule_status(
        actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        try:
            recovery_pending = await run_in_threadpool(credential_policy.rotation_recovery_pending)
        except OSError as exc:
            raise HTTPException(status_code=503, detail="NAS 备份恢复收据不安全") from exc
        recovery: dict[str, Any] | None = None
        if recovery_pending and not (audit is None and auth.required):
            try:
                recovery = await run_in_threadpool(credential_policy.recover_rotation)
            except (OSError, RuntimeError, ValueError):
                recovery_pending = True
            else:
                recovery_pending = False
                if recovery.get("recovered") and audit is not None:
                    try:
                        audit.record(
                            actor=actor,
                            action=CREDENTIAL_ROTATION_ACTION,
                            target=recovery["planId"],
                            outcome="recovered",
                            metadata={
                                "direction": recovery["direction"],
                                "removedKeyCount": recovery["removedKeyCount"],
                                "pathsRedacted": True,
                                "secretRedacted": True,
                                "source": "native",
                            },
                        )
                    except (OSError, AuditIntegrityError) as exc:
                        raise HTTPException(
                            status_code=503, detail="appliance audit unavailable"
                        ) from exc
        try:
            status = await run_in_threadpool(policy.policy_status)
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=503, detail="NAS 备份状态读取失败") from exc
        return {
            **status,
            "credentialRotationRecoveryPending": recovery_pending,
        }

    @router.get("/repository-candidates")
    async def repository_candidates() -> dict[str, Any]:
        try:
            return await run_in_threadpool(
                external_storage.list_external_storage_mounts,
                deployment_root=schedule_runner.DEPLOYMENT_ROOT,
                appliance_env=schedule_runner.APPLIANCE_ENV,
                state_root_override=schedule_runner.STATE_ROOT,
                nas_root_override=schedule_runner.NAS_ROOT,
            )
        except (external_storage.ExternalStorageError, OSError) as exc:
            raise HTTPException(
                status_code=503,
                detail="外置或远端备份挂载列表暂不可用",
            ) from exc

    @router.get("/remotes")
    async def backup_remotes() -> dict[str, Any]:
        try:
            return await run_in_threadpool(remote_policy.list_remotes)
        except (OSError, ValueError) as exc:
            raise HTTPException(
                status_code=503,
                detail="S3 兼容备份远端状态暂不可用",
            ) from exc

    @router.post("/remotes/plan")
    async def plan_backup_remote(
        body: NasBackupRemoteDesiredState,
    ) -> dict[str, Any]:
        try:
            return await run_in_threadpool(
                remote_policy.plan_remote,
                body.to_wire(),
                binding_key=binding_key,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(
                status_code=503,
                detail="S3 兼容备份远端暂不可配置",
            ) from exc

    @router.post("/remotes/apply")
    async def apply_backup_remote(
        body: NasBackupRemoteApplyRequest,
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        desired = body.desired.to_wire()
        try:
            current = await run_in_threadpool(
                remote_policy.plan_remote,
                desired,
                binding_key=binding_key,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(
                status_code=503,
                detail="S3 兼容备份远端暂不可配置",
            ) from exc
        if current.get("planId") != body.plan_id:
            raise HTTPException(
                status_code=409,
                detail="NAS backup remote plan is stale; preview again",
            )
        if approval is None:
            if auth.required:
                raise HTTPException(status_code=503, detail="high-risk approval unavailable")
        else:
            consume_request_approval(
                request,
                approval,
                actor=actor,
                action=REMOTE_ACTION,
                target=body.plan_id,
            )
        metadata = {
            "operation": current["operation"],
            "remoteId": current["desired"]["remoteId"],
            "kind": "s3",
            "pathsRedacted": True,
            "secretRedacted": True,
            "source": "native",
        }
        record(
            request,
            actor=actor,
            action=REMOTE_ACTION,
            target=body.plan_id,
            outcome="attempted",
            metadata=metadata,
        )
        try:
            result = await run_in_threadpool(
                remote_policy.apply_remote,
                desired,
                body.plan_id,
                binding_key=binding_key,
            )
        except ValueError as exc:
            record(
                request,
                actor=actor,
                action=REMOTE_ACTION,
                target=body.plan_id,
                outcome="failed",
                metadata={**metadata, "errorType": type(exc).__name__},
            )
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except OSError as exc:
            record(
                request,
                actor=actor,
                action=REMOTE_ACTION,
                target=body.plan_id,
                outcome="failed",
                metadata={**metadata, "errorType": type(exc).__name__},
            )
            raise HTTPException(
                status_code=503,
                detail="S3 兼容备份远端配置失败；已尝试恢复原状态",
            ) from exc
        record(
            request,
            actor=actor,
            action=REMOTE_ACTION,
            target=body.plan_id,
            outcome="succeeded",
            metadata=metadata,
        )
        return result

    @router.post("/restore/sets")
    async def restore_sets(
        body: NasBackupRestoreRepositoryState,
        limit: int = Query(default=restore_policy.MAX_VISIBLE_SETS, ge=1, le=50),
    ) -> dict[str, Any]:
        try:
            return await run_in_threadpool(
                restore_policy.list_restore_sets,
                body.model_dump(by_alias=True),
                limit=limit,
            )
        except restore_policy.NasBackupRestorePolicyError as exc:
            status_code = (
                409
                if exc.code
                in {
                    "writer_active",
                    "credential_recovery_pending",
                }
                else 503
            )
            raise HTTPException(status_code=status_code, detail="NAS 备份恢复列表暂不可用") from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail="NAS 备份恢复列表暂不可用") from exc

    @router.get("/restore/targets")
    async def restore_targets() -> dict[str, Any]:
        try:
            return await run_in_threadpool(restore_policy.list_restore_targets)
        except restore_policy.NasBackupRestorePolicyError as exc:
            raise HTTPException(status_code=503, detail="NAS 恢复目标列表暂不可用") from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail="NAS 恢复目标列表暂不可用") from exc

    @router.post("/restore/plan")
    async def plan_restore(body: NasBackupRestoreDesiredState) -> dict[str, Any]:
        try:
            return await run_in_threadpool(
                restore_policy.plan_restore, body.model_dump(by_alias=True)
            )
        except restore_policy.NasBackupRestorePolicyError as exc:
            status_code = 422 if exc.code == "invalid_request" else 409
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail="NAS 备份恢复预览失败") from exc

    @router.post("/restore/apply")
    async def apply_restore(
        body: NasBackupRestoreApplyRequest,
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        desired = body.desired.model_dump(by_alias=True)
        try:
            current = await run_in_threadpool(restore_policy.plan_restore, desired)
        except restore_policy.NasBackupRestorePolicyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail="NAS 备份恢复预览失败") from exc
        if current.get("planId") != body.plan_id:
            raise HTTPException(
                status_code=409,
                detail="NAS backup restore plan is stale; preview again",
            )
        if current.get("confirmation") != body.confirmation:
            raise HTTPException(
                status_code=409,
                detail="NAS backup restore confirmation does not match the plan",
            )
        if approval is None:
            if auth.required:
                raise HTTPException(status_code=503, detail="high-risk approval unavailable")
        else:
            consume_request_approval(
                request,
                approval,
                actor=actor,
                action=RESTORE_ACTION,
                target=body.plan_id,
            )
        metadata = {
            "operation": current["operation"],
            "setId": current["setId"],
            "snapshotId": current["snapshotId"],
            "memberCount": current["memberCount"],
            "remappedMemberCount": sum(
                1 for member in current["members"] if member.get("remapped") is True
            ),
            "recoveryPending": current["recoveryPending"],
            "pathsRedacted": True,
            "source": "native",
        }
        record(
            request,
            actor=actor,
            action=RESTORE_ACTION,
            target=body.plan_id,
            outcome="attempted",
            metadata=metadata,
        )
        try:
            result = await run_in_threadpool(
                restore_policy.apply_restore,
                desired,
                body.plan_id,
                body.confirmation,
            )
        except restore_policy.NasBackupRestorePolicyError as exc:
            record(
                request,
                actor=actor,
                action=RESTORE_ACTION,
                target=body.plan_id,
                outcome="failed",
                metadata={
                    **metadata,
                    "errorCode": exc.code,
                    "errorType": type(exc).__name__,
                },
            )
            status_code = (
                409
                if exc.code
                in {
                    "stale_plan",
                    "confirmation_mismatch",
                    "receipt_mismatch",
                    "target_not_empty",
                }
                else 503
            )
            raise HTTPException(
                status_code=status_code,
                detail="NAS 数据恢复未完成；请保持目标离线并重试同一计划",
            ) from exc
        except OSError as exc:
            record(
                request,
                actor=actor,
                action=RESTORE_ACTION,
                target=body.plan_id,
                outcome="failed",
                metadata={**metadata, "errorType": type(exc).__name__},
            )
            raise HTTPException(status_code=503, detail="NAS 数据恢复验证失败") from exc
        record(
            request,
            actor=actor,
            action=RESTORE_ACTION,
            target=body.plan_id,
            outcome="succeeded",
            metadata=metadata,
        )
        return result

    @router.post("/credential/plan")
    async def plan_credential(
        body: NasBackupCredentialDesiredState,
    ) -> dict[str, Any]:
        try:
            return await run_in_threadpool(
                credential_policy.plan_credential,
                body.to_wire(),
                binding_key=binding_key,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail="NAS 备份凭据暂不可配置") from exc

    @router.post("/credential/apply")
    async def apply_credential(
        body: NasBackupCredentialApplyRequest,
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        desired = body.desired.to_wire()
        try:
            current = await run_in_threadpool(
                credential_policy.plan_credential,
                desired,
                binding_key=binding_key,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail="NAS 备份凭据暂不可配置") from exc
        if current.get("planId") != body.plan_id:
            raise HTTPException(
                status_code=409,
                detail="NAS backup credential plan is stale; preview again",
            )
        if approval is None:
            if auth.required:
                raise HTTPException(status_code=503, detail="high-risk approval unavailable")
        else:
            consume_request_approval(
                request,
                approval,
                actor=actor,
                action=CREDENTIAL_ACTION,
                target=body.plan_id,
            )
        metadata = {
            "operation": current["operation"],
            "repositoryMode": desired["mode"],
            "pathsRedacted": True,
            "secretRedacted": True,
            "source": "native",
        }
        intent_id = request_intent_id(request)
        if intent_id:
            metadata["intentId"] = intent_id
        if audit is None:
            if auth.required:
                raise HTTPException(status_code=503, detail="appliance audit unavailable")
        else:
            try:
                audit.record(
                    actor=actor,
                    action=CREDENTIAL_ACTION,
                    target=body.plan_id,
                    outcome="attempted",
                    metadata=metadata,
                )
            except (OSError, AuditIntegrityError) as exc:
                raise HTTPException(status_code=503, detail="appliance audit unavailable") from exc
        try:
            result = await run_in_threadpool(
                credential_policy.apply_credential,
                desired,
                body.plan_id,
                binding_key=binding_key,
            )
        except ValueError as exc:
            outcome = "failed"
            status_code = 409
            detail = str(exc)
            error = exc
        except OSError as exc:
            outcome = "failed"
            status_code = 503
            detail = "NAS 备份凭据配置失败"
            error = exc
        else:
            outcome = "succeeded"
            status_code = 200
            detail = ""
            error = None
        if audit is not None:
            try:
                audit.record(
                    actor=actor,
                    action=CREDENTIAL_ACTION,
                    target=body.plan_id,
                    outcome=outcome,
                    metadata=(
                        metadata
                        if error is None
                        else {**metadata, "errorType": type(error).__name__}
                    ),
                )
            except (OSError, AuditIntegrityError) as exc:
                raise HTTPException(status_code=503, detail="appliance audit unavailable") from exc
        if error is not None:
            raise HTTPException(status_code=status_code, detail=detail) from error
        return result

    @router.post("/credential/rotation/plan")
    async def plan_credential_rotation(
        body: NasBackupCredentialRotationDesiredState,
    ) -> dict[str, Any]:
        try:
            return await run_in_threadpool(
                credential_policy.plan_rotation,
                body.to_wire(),
                binding_key=binding_key,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail="NAS 备份凭据暂不可轮换") from exc

    @router.post("/credential/rotation/apply")
    async def apply_credential_rotation(
        body: NasBackupCredentialRotationApplyRequest,
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        desired = body.desired.to_wire()
        try:
            current = await run_in_threadpool(
                credential_policy.plan_rotation,
                desired,
                binding_key=binding_key,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail="NAS 备份凭据暂不可轮换") from exc
        if current.get("planId") != body.plan_id:
            raise HTTPException(
                status_code=409,
                detail="NAS backup credential rotation plan is stale; preview again",
            )
        if approval is None:
            if auth.required:
                raise HTTPException(status_code=503, detail="high-risk approval unavailable")
        else:
            consume_request_approval(
                request,
                approval,
                actor=actor,
                action=CREDENTIAL_ROTATION_ACTION,
                target=body.plan_id,
            )
        metadata = {
            "operation": current["operation"],
            "repositoryMode": "rotate",
            "pathsRedacted": True,
            "secretRedacted": True,
            "source": "native",
        }
        intent_id = request_intent_id(request)
        if intent_id:
            metadata["intentId"] = intent_id
        if audit is None:
            if auth.required:
                raise HTTPException(status_code=503, detail="appliance audit unavailable")
        else:
            try:
                audit.record(
                    actor=actor,
                    action=CREDENTIAL_ROTATION_ACTION,
                    target=body.plan_id,
                    outcome="attempted",
                    metadata=metadata,
                )
            except (OSError, AuditIntegrityError) as exc:
                raise HTTPException(status_code=503, detail="appliance audit unavailable") from exc
        try:
            result = await run_in_threadpool(
                credential_policy.apply_rotation,
                desired,
                body.plan_id,
                binding_key=binding_key,
            )
        except ValueError as exc:
            outcome = "failed"
            status_code = 409
            detail = str(exc)
            error = exc
        except OSError as exc:
            outcome = "failed"
            status_code = 503
            detail = "NAS 备份凭据轮换失败"
            error = exc
        else:
            outcome = "succeeded"
            status_code = 200
            detail = ""
            error = None
        if audit is not None:
            try:
                audit.record(
                    actor=actor,
                    action=CREDENTIAL_ROTATION_ACTION,
                    target=body.plan_id,
                    outcome=outcome,
                    metadata=(
                        metadata
                        if error is None
                        else {**metadata, "errorType": type(error).__name__}
                    ),
                )
            except (OSError, AuditIntegrityError) as exc:
                raise HTTPException(status_code=503, detail="appliance audit unavailable") from exc
        if error is not None:
            raise HTTPException(status_code=status_code, detail=detail) from error
        return result

    @router.post("/schedule/plan")
    async def plan_schedule(body: NasBackupScheduleDesiredState) -> dict[str, Any]:
        try:
            return await run_in_threadpool(policy.plan_policy, body.model_dump(by_alias=True))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail="NAS 备份配置暂不可用") from exc

    @router.post("/schedule/apply")
    async def apply_schedule(
        body: NasBackupScheduleApplyRequest,
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        desired = body.desired.model_dump(by_alias=True)
        try:
            current = await run_in_threadpool(policy.plan_policy, desired)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail="NAS 备份配置暂不可用") from exc
        if current.get("planId") != body.plan_id:
            raise HTTPException(
                status_code=409,
                detail="NAS backup schedule plan is stale; preview again",
            )
        if current.get("operation") == "none":
            try:
                return await run_in_threadpool(policy.apply_policy, desired, body.plan_id)
            except (OSError, ValueError) as exc:
                raise HTTPException(status_code=503, detail="NAS 备份配置暂不可用") from exc
        if approval is None:
            if auth.required:
                raise HTTPException(status_code=503, detail="high-risk approval unavailable")
        else:
            consume_request_approval(
                request,
                approval,
                actor=actor,
                action=ACTION,
                target=body.plan_id,
            )
        metadata = {
            "operation": current["operation"],
            "enabled": desired["enabled"],
            "repositoryConfigured": desired["repository"] is not None,
            "pathsRedacted": True,
            "source": "native",
        }
        record(
            request,
            actor=actor,
            target=body.plan_id,
            outcome="attempted",
            metadata=metadata,
        )
        try:
            result = await run_in_threadpool(policy.apply_policy, desired, body.plan_id)
        except ValueError as exc:
            record(
                request,
                actor=actor,
                target=body.plan_id,
                outcome="failed",
                metadata={**metadata, "errorType": type(exc).__name__},
            )
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except OSError as exc:
            record(
                request,
                actor=actor,
                target=body.plan_id,
                outcome="failed",
                metadata={**metadata, "errorType": type(exc).__name__},
            )
            raise HTTPException(status_code=503, detail="NAS 备份配置应用失败") from exc
        record(
            request,
            actor=actor,
            target=body.plan_id,
            outcome="succeeded",
            metadata=metadata,
        )
        return result

    return router


__all__ = [
    "ACTION",
    "CREDENTIAL_ACTION",
    "CREDENTIAL_ROTATION_ACTION",
    "NasBackupCredentialApplyRequest",
    "NasBackupCredentialDesiredState",
    "NasBackupRestoreApplyRequest",
    "NasBackupRestoreDesiredState",
    "NasBackupRestoreRepositoryState",
    "NasBackupRestoreTargetBinding",
    "NasBackupScheduleApplyRequest",
    "NasBackupScheduleDesiredState",
    "NasBackupRemoteApplyRequest",
    "NasBackupRemoteDesiredState",
    "REMOTE_ACTION",
    "RESTORE_ACTION",
    "create_nas_backup_router",
]
