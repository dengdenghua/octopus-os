"""Authenticated lifecycle API for Agent capabilities shown inside Echo Hub."""

from __future__ import annotations

import re
from typing import Any, Protocol

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from starlette.concurrency import run_in_threadpool

from appliance.agent_api.capabilities import AgentCapabilityApiError
from appliance.approval import (
    HighRiskApprovalService,
    consume_request_approval,
    request_intent_id,
)
from appliance.audit import ApplianceAudit, AuditIntegrityError
from appliance.security import ApplianceAuthenticator, resolve_authenticator


class AgentCapabilityInvoker(Protocol):
    def invoke(
        self,
        operation: str,
        capability_id: str,
        *,
        actor_id: str,
        roles: tuple[str, ...] = (),
        **kwargs: Any,
    ) -> dict[str, Any]: ...


class AgentCapabilityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    capability_id: str = Field(alias="capabilityId", min_length=1, max_length=256)


class AgentCapabilityApplyRequest(AgentCapabilityRequest):
    plan_id: str = Field(alias="planId", pattern=r"^[0-9a-f]{64}$")


class AgentCapabilityAuthorizeRequest(AgentCapabilityApplyRequest):
    permissions: list[str] = Field(default_factory=list, max_length=64)
    activate: bool = True

    @field_validator("permissions")
    @classmethod
    def validate_permissions(cls, permissions: list[str]) -> list[str]:
        if len(set(permissions)) != len(permissions):
            raise ValueError("permissions must be unique")
        if any(permission not in _PUBLIC_PERMISSIONS for permission in permissions):
            raise ValueError("permission is not supported by Echo OS")
        return permissions


class AgentCapabilityConnectRequest(AgentCapabilityRequest):
    tokens: dict[str, SecretStr] | None = None

    @field_validator("tokens")
    @classmethod
    def validate_tokens(cls, tokens: dict[str, SecretStr] | None) -> dict[str, SecretStr] | None:
        if tokens is None:
            return None
        if not 1 <= len(tokens) <= 32:
            raise ValueError("tokens must contain between 1 and 32 entries")
        for key, secret in tokens.items():
            if (
                not 1 <= len(key) <= 128
                or any(ord(character) < 33 or ord(character) == 127 for character in key)
                or not 1 <= len(secret.get_secret_value()) <= 8192
            ):
                raise ValueError("token name or value is invalid")
        return tokens


_PUBLIC_PERMISSIONS = frozenset(
    {
        "account.credentials",
        "content.read",
        "content.write",
        "interaction.user",
        "network.remote",
        "process.local",
    }
)

_PUBLIC_ERRORS = {
    "AUTHENTICATION_REQUIRED": (401, "authentication is required"),
    "DEVICE_OPERATOR_REQUIRED": (403, "device operator permission is required"),
    "CAPABILITY_NOT_FOUND": (404, "capability was not found"),
    "INVALID_CAPABILITY_ID": (422, "capability id is invalid"),
    "INVALID_PLAN_ID": (422, "capability plan id is invalid"),
    "INVALID_PRINCIPAL": (422, "capability principal is invalid"),
    "INVALID_CREDENTIALS": (422, "capability credentials are invalid"),
    "PLAN_STALE": (409, "capability plan has changed; review it again"),
    "INSTALL_BLOCKED": (409, "capability installation is blocked"),
    "CAPABILITY_NOT_INSTALLED": (409, "capability is not installed"),
    "PERMISSION_REVIEW_REQUIRED": (409, "capability permissions require review"),
    "CONNECT_BLOCKED": (409, "capability connection is blocked"),
    "PRINCIPAL_ISOLATION_UNAVAILABLE": (409, "capability principal isolation is unavailable"),
    "ROLLBACK_UNAVAILABLE": (409, "capability rollback is unavailable"),
    "ROLLBACK_REJECTED": (409, "capability rollback was rejected"),
    "AGENT_CAPABILITY_UNAVAILABLE": (503, "Agent capability service is unavailable"),
}

_PLAN_ID = re.compile(r"^[0-9a-f]{64}$")
_CREDENTIAL_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
_CONNECTION_MODES = frozenset(
    {"principal_credentials", "no_credentials", "agent_managed", "unavailable"}
)
_AUTH_MODES = frozenset(
    {"connected-account", "mcp", "oauth", "oneid-token", "server-side", "token"}
)


def _public_text(value: Any, *, maximum: int) -> str | None:
    if not isinstance(value, str) or not 1 <= len(value) <= maximum:
        return None
    if value != value.strip() or any(
        ord(character) < 32 or ord(character) == 127 for character in value
    ):
        return None
    return value


def _public_list(
    value: Any,
    *,
    maximum_items: int = 64,
    maximum_text: int = 256,
    allowlist: frozenset[str] | None = None,
) -> list[str] | None:
    if not isinstance(value, list) or len(value) > maximum_items:
        return None
    projected: list[str] = []
    for item in value:
        text = _public_text(item, maximum=maximum_text)
        if text is None or (allowlist is not None and text not in allowlist):
            return None
        if text not in projected:
            projected.append(text)
    return projected


def _project_plan(operation: str, capability_id: str, value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    ready_field = {
        "install_plan": "can_install",
        "uninstall_plan": "can_uninstall",
        "rollback_plan": "can_rollback",
    }[operation]
    schema = _public_text(value.get("schema"), maximum=96)
    returned_id = _public_text(value.get("capability_id"), maximum=256)
    plan_id = value.get("plan_id")
    permissions = _public_list(value.get("permissions", []), allowlist=_PUBLIC_PERMISSIONS)
    blockers = _public_list(value.get("blockers", []))
    changes = _public_list(value.get("changes", []))
    if (
        schema is None
        or value.get("service_schema") != "echo.capability-service.v1"
        or returned_id != capability_id
        or not isinstance(plan_id, str)
        or _PLAN_ID.fullmatch(plan_id) is None
        or not isinstance(value.get(ready_field), bool)
        or permissions is None
        or blockers is None
        or changes is None
    ):
        return None
    return {
        "schema": schema,
        "service_schema": "echo.capability-service.v1",
        "capability_id": returned_id,
        "plan_id": plan_id,
        ready_field: value[ready_field],
        "permissions": permissions,
        "blockers": blockers,
        "changes": changes,
    }


def _project_connection_profile(capability_id: str, value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    auth_mode = value.get("auth_mode")
    mode = value.get("mode")
    minimum = value.get("minimum_credentials")
    blockers = _public_list(value.get("blockers", []), maximum_items=32)
    raw_fields = value.get("fields")
    if (
        value.get("schema") != "echo.capability-service.v1"
        or value.get("capability_id") != capability_id
        or auth_mode not in _AUTH_MODES
        or mode not in _CONNECTION_MODES
        or not isinstance(value.get("can_connect"), bool)
        or not isinstance(value.get("connected"), bool)
        or not isinstance(minimum, int)
        or isinstance(minimum, bool)
        or not 0 <= minimum <= 32
        or blockers is None
        or not isinstance(raw_fields, list)
        or len(raw_fields) > 32
    ):
        return None
    fields: list[dict[str, Any]] = []
    for field in raw_fields:
        if not isinstance(field, dict):
            return None
        key = field.get("key")
        label = _public_text(field.get("label"), maximum=128)
        label_zh = _public_text(field.get("label_zh"), maximum=128)
        if (
            not isinstance(key, str)
            or _CREDENTIAL_KEY.fullmatch(key) is None
            or label is None
            or label_zh is None
            or field.get("secret") is not True
            or not isinstance(field.get("required"), bool)
        ):
            return None
        fields.append(
            {
                "key": key,
                "label": label,
                "label_zh": label_zh,
                "secret": True,
                "required": field["required"],
            }
        )
    if minimum > len(fields):
        return None
    return {
        "schema": "echo.capability-service.v1",
        "capability_id": capability_id,
        "auth_mode": auth_mode,
        "mode": mode,
        "can_connect": value["can_connect"],
        "connected": value["connected"],
        "minimum_credentials": minimum,
        "fields": fields,
        "blockers": blockers,
    }


def _project_operation_result(
    operation: str,
    capability_id: str,
    value: Any,
) -> dict[str, Any] | None:
    if (
        not isinstance(value, dict)
        or value.get("schema") != "echo.capability-service.v1"
        or value.get("operation") != operation
        or not isinstance(value.get("capability"), dict)
        or not isinstance(value.get("result"), dict)
        or value["capability"].get("id") != capability_id
    ):
        return None
    capability = {"id": capability_id}
    result: dict[str, Any] = {}
    for field in ("installed", "enabled", "connected"):
        capability_value = value["capability"].get(field)
        result_value = value["result"].get(field)
        if isinstance(capability_value, bool):
            capability[field] = capability_value
        if isinstance(result_value, bool):
            result[field] = result_value
    return {
        "schema": "echo.capability-service.v1",
        "operation": operation,
        "capability": capability,
        "result": result,
    }


def _project_status(capability_id: str, value: Any) -> dict[str, Any] | None:
    if (
        not isinstance(value, dict)
        or value.get("schema") != "echo.capability-service.v1"
        or value.get("capability_id") != capability_id
        or not isinstance(value.get("status"), dict)
    ):
        return None
    status = {
        field: value["status"][field]
        for field in ("installed", "enabled", "connected")
        if isinstance(value["status"].get(field), bool)
    }
    return {
        "schema": "echo.capability-service.v1",
        "capability_id": capability_id,
        "status": status,
    }


def _project_agent_result(
    operation: str,
    capability_id: str,
    value: Any,
) -> dict[str, Any]:
    if operation in {"install_plan", "uninstall_plan", "rollback_plan"}:
        projected = _project_plan(operation, capability_id, value)
    elif operation == "connection_profile":
        projected = _project_connection_profile(capability_id, value)
    elif operation == "status":
        projected = _project_status(capability_id, value)
    elif operation in {
        "inspect",
        "install",
        "authorize",
        "disable",
        "connect",
        "disconnect",
        "uninstall",
        "rollback",
    }:
        projected = _project_operation_result(operation, capability_id, value)
    else:
        projected = None
    if projected is None:
        raise AgentCapabilityApiError(
            "INVALID_RUNTIME_RESULT",
            "Agent capability result is invalid",
        )
    return projected


def create_agent_capabilities_router(
    service: AgentCapabilityInvoker,
    *,
    jwt_secret: str | None = None,
    authenticator: ApplianceAuthenticator | None = None,
    approval: HighRiskApprovalService | None = None,
    audit: ApplianceAudit | None = None,
) -> APIRouter:
    auth = resolve_authenticator(jwt_secret=jwt_secret, authenticator=authenticator)
    require_auth = auth.dependency()

    def require_operator(actor: str = Depends(require_auth)) -> str:
        if actor != "local:admin":
            raise HTTPException(status_code=403, detail="device operator permission is required")
        return actor

    router = APIRouter(
        prefix="/api/appliance/agent-capabilities",
        tags=["appliance", "agent-capabilities"],
        dependencies=[Depends(require_auth)],
    )

    async def invoke(
        operation: str,
        capability_id: str,
        *,
        actor: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        try:
            result = await run_in_threadpool(
                service.invoke,
                operation,
                capability_id,
                actor_id=actor,
                roles=("admin",) if actor == "local:admin" else (),
                **kwargs,
            )
            return _project_agent_result(operation, capability_id, result)
        except AgentCapabilityApiError as exc:
            code = (
                exc.code
                if isinstance(exc.code, str) and exc.code in _PUBLIC_ERRORS
                else "AGENT_CAPABILITY_UNAVAILABLE"
            )
            status_code, message = _PUBLIC_ERRORS[code]
            raise HTTPException(
                status_code=status_code,
                detail={"code": code, "message": message},
            ) from exc
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "AGENT_CAPABILITY_UNAVAILABLE",
                    "message": "Agent capability service is unavailable",
                },
            ) from exc

    def record(
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
                raise HTTPException(status_code=503, detail="Agent capability audit unavailable")
            return
        public_metadata = dict(metadata or {})
        intent_id = request_intent_id(request)
        if intent_id:
            public_metadata["intentId"] = intent_id
        try:
            audit.record(
                actor=actor,
                action=action,
                target=target,
                outcome=outcome,
                metadata=public_metadata,
            )
        except (OSError, AuditIntegrityError) as exc:
            raise HTTPException(
                status_code=503, detail="Agent capability audit unavailable"
            ) from exc

    def consume_approval(
        request: Request,
        *,
        actor: str,
        action: str,
        plan_id: str,
    ) -> None:
        if approval is None:
            if auth.required:
                raise HTTPException(status_code=503, detail="Agent capability approval unavailable")
            return
        consume_request_approval(
            request,
            approval,
            actor=actor,
            action=action,
            target=plan_id,
        )

    @router.get("/{capability_id}")
    async def inspect_capability(
        capability_id: str,
        actor: str = Depends(require_auth),
    ) -> dict[str, Any]:
        return await invoke("inspect", capability_id, actor=actor)

    @router.get("/{capability_id}/status")
    async def capability_status(
        capability_id: str,
        actor: str = Depends(require_auth),
    ) -> dict[str, Any]:
        return await invoke("status", capability_id, actor=actor)

    @router.get("/{capability_id}/connection-profile")
    async def capability_connection_profile(
        capability_id: str,
        actor: str = Depends(require_auth),
    ) -> dict[str, Any]:
        return await invoke("connection_profile", capability_id, actor=actor)

    @router.post("/plans/install")
    async def plan_install(
        body: AgentCapabilityRequest,
        actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        return await invoke("install_plan", body.capability_id, actor=actor)

    @router.post("/plans/install/apply")
    async def apply_install(
        body: AgentCapabilityApplyRequest,
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        consume_approval(
            request,
            actor=actor,
            action="agent.capability.install",
            plan_id=body.plan_id,
        )
        record(
            request=request,
            actor=actor,
            action="agent.capability.install",
            target=body.plan_id,
            outcome="attempted",
            metadata={"capabilityId": body.capability_id},
        )
        try:
            result = await invoke(
                "install",
                body.capability_id,
                actor=actor,
                plan_id=body.plan_id,
            )
        except HTTPException:
            record(
                request=request,
                actor=actor,
                action="agent.capability.install",
                target=body.plan_id,
                outcome="failed",
                metadata={"capabilityId": body.capability_id},
            )
            raise
        record(
            request=request,
            actor=actor,
            action="agent.capability.install",
            target=body.plan_id,
            outcome="succeeded",
            metadata={"capabilityId": body.capability_id},
        )
        return result

    @router.post("/plans/authorize")
    async def plan_authorize(
        body: AgentCapabilityRequest,
        actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        return await invoke("install_plan", body.capability_id, actor=actor)

    @router.post("/plans/authorize/apply")
    async def apply_authorize(
        body: AgentCapabilityAuthorizeRequest,
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        consume_approval(
            request,
            actor=actor,
            action="agent.capability.authorize",
            plan_id=body.plan_id,
        )
        record(
            request=request,
            actor=actor,
            action="agent.capability.authorize",
            target=body.plan_id,
            outcome="attempted",
            metadata={
                "permissionCount": len(body.permissions),
                "activate": body.activate,
            },
        )
        try:
            result = await invoke(
                "authorize",
                body.capability_id,
                actor=actor,
                plan_id=body.plan_id,
                permissions=body.permissions,
                activate=body.activate,
            )
        except HTTPException:
            record(
                request=request,
                actor=actor,
                action="agent.capability.authorize",
                target=body.plan_id,
                outcome="failed",
            )
            raise
        record(
            request=request,
            actor=actor,
            action="agent.capability.authorize",
            target=body.plan_id,
            outcome="succeeded",
            metadata={"permissionCount": len(body.permissions), "activate": body.activate},
        )
        return result

    @router.post("/{capability_id}/disable")
    async def disable_capability(
        capability_id: str,
        request: Request,
        actor: str = Depends(require_auth),
    ) -> dict[str, Any]:
        record(
            request=request,
            actor=actor,
            action="agent.capability.disable",
            target=capability_id,
            outcome="attempted",
        )
        try:
            result = await invoke("disable", capability_id, actor=actor)
        except HTTPException:
            record(
                request=request,
                actor=actor,
                action="agent.capability.disable",
                target=capability_id,
                outcome="failed",
            )
            raise
        record(
            request=request,
            actor=actor,
            action="agent.capability.disable",
            target=capability_id,
            outcome="succeeded",
        )
        return result

    @router.post("/connect")
    async def connect_capability(
        body: AgentCapabilityConnectRequest,
        request: Request,
        actor: str = Depends(require_auth),
    ) -> dict[str, Any]:
        tokens = (
            {key: value.get_secret_value() for key, value in body.tokens.items()}
            if body.tokens
            else None
        )
        record(
            request=request,
            actor=actor,
            action="agent.capability.connect",
            target=body.capability_id,
            outcome="attempted",
            metadata={"credentialFieldCount": len(tokens or {})},
        )
        try:
            try:
                result = await invoke(
                    "connect",
                    body.capability_id,
                    actor=actor,
                    tokens=tokens,
                )
            finally:
                if tokens:
                    tokens.clear()
        except HTTPException:
            record(
                request=request,
                actor=actor,
                action="agent.capability.connect",
                target=body.capability_id,
                outcome="failed",
            )
            raise
        record(
            request=request,
            actor=actor,
            action="agent.capability.connect",
            target=body.capability_id,
            outcome="succeeded",
        )
        return result

    @router.post("/{capability_id}/disconnect")
    async def disconnect_capability(
        capability_id: str,
        request: Request,
        actor: str = Depends(require_auth),
    ) -> dict[str, Any]:
        record(
            request=request,
            actor=actor,
            action="agent.capability.disconnect",
            target=capability_id,
            outcome="attempted",
        )
        try:
            result = await invoke("disconnect", capability_id, actor=actor)
        except HTTPException:
            record(
                request=request,
                actor=actor,
                action="agent.capability.disconnect",
                target=capability_id,
                outcome="failed",
            )
            raise
        record(
            request=request,
            actor=actor,
            action="agent.capability.disconnect",
            target=capability_id,
            outcome="succeeded",
        )
        return result

    async def lifecycle_plan(
        operation: str,
        body: AgentCapabilityRequest,
        actor: str,
    ) -> dict[str, Any]:
        return await invoke(f"{operation}_plan", body.capability_id, actor=actor)

    @router.post("/plans/uninstall")
    async def plan_uninstall(
        body: AgentCapabilityRequest,
        actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        return await lifecycle_plan("uninstall", body, actor)

    @router.post("/plans/rollback")
    async def plan_rollback(
        body: AgentCapabilityRequest,
        actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        return await lifecycle_plan("rollback", body, actor)

    async def apply_device_lifecycle(
        operation: str,
        body: AgentCapabilityApplyRequest,
        request: Request,
        actor: str,
    ) -> dict[str, Any]:
        action = f"agent.capability.{operation}"
        consume_approval(request, actor=actor, action=action, plan_id=body.plan_id)
        record(
            request=request,
            actor=actor,
            action=action,
            target=body.plan_id,
            outcome="attempted",
            metadata={"capabilityId": body.capability_id},
        )
        try:
            result = await invoke(
                operation,
                body.capability_id,
                actor=actor,
                plan_id=body.plan_id,
            )
        except HTTPException:
            record(
                request=request,
                actor=actor,
                action=action,
                target=body.plan_id,
                outcome="failed",
                metadata={"capabilityId": body.capability_id},
            )
            raise
        record(
            request=request,
            actor=actor,
            action=action,
            target=body.plan_id,
            outcome="succeeded",
            metadata={"capabilityId": body.capability_id},
        )
        return result

    @router.post("/plans/uninstall/apply")
    async def apply_uninstall(
        body: AgentCapabilityApplyRequest,
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        return await apply_device_lifecycle("uninstall", body, request, actor)

    @router.post("/plans/rollback/apply")
    async def apply_rollback(
        body: AgentCapabilityApplyRequest,
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        return await apply_device_lifecycle("rollback", body, request, actor)

    return router


__all__ = [
    "AgentCapabilityApplyRequest",
    "AgentCapabilityAuthorizeRequest",
    "AgentCapabilityConnectRequest",
    "AgentCapabilityRequest",
    "create_agent_capabilities_router",
]
