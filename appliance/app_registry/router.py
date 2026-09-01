"""启动器的 HTTP API。

GET  /api/appliance/auth/status          公开:是否需要登录 / 当前是否已登录
GET  /api/appliance/apps                  应用列表(需登录)
POST /api/appliance/apps/{id}/start       (需登录)
POST /api/appliance/apps/{id}/stop        (需登录)

鉴权:appliance 单用户模式下,登录走 runtime 现成的 /api/auth/local/login,
签发 HS256 JWT;这里用同一 jwt_secret 校验 Bearer token 保护应用接口。
jwt_secret 为 None(未启用认证)时全部放行,便于无认证的本地开发。

生产 compose 中 start/stop 只经 docker-control 窄代理，不再把原始 socket 交给
Echo/Agent 进程；每次启停还需要与 actor/action/container 绑定的单次审批令牌，
并在 Agent 官方 HMAC 审计链中记录尝试与结果。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from appliance.app_registry.catalog import build_catalog
from appliance.app_registry.docker_client import (
    DockerClient,
    DockerControlDenied,
    DockerUnavailable,
)
from appliance.approval import (
    HighRiskApprovalService,
    consume_request_approval,
    request_intent_id,
)
from appliance.audit import ApplianceAudit, AuditIntegrityError
from appliance.identifiers import is_container_id
from appliance.security import ApplianceAuthenticator, resolve_authenticator


def create_appliance_router(
    docker: DockerClient | None = None,
    jwt_secret: str | None = None,
    approval: HighRiskApprovalService | None = None,
    audit: ApplianceAudit | None = None,
    *,
    authenticator: ApplianceAuthenticator | None = None,
) -> APIRouter:
    client = docker or DockerClient()
    router = APIRouter(prefix="/api/appliance", tags=["appliance"])
    auth = resolve_authenticator(jwt_secret=jwt_secret, authenticator=authenticator)
    _require_auth = auth.dependency()
    _require_operator = auth.operator_dependency()

    def _validated(container_id: str) -> str:
        if not is_container_id(container_id):
            raise HTTPException(status_code=422, detail="invalid container id")
        return container_id

    def _record(
        *,
        request: Request,
        actor: str,
        action: str,
        target: str,
        outcome: str,
        metadata: dict | None = None,
    ) -> None:
        if audit is None:
            if auth.required:
                raise HTTPException(status_code=503, detail="appliance audit unavailable")
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
            raise HTTPException(status_code=503, detail="appliance audit unavailable") from exc

    def _authorize_control(
        request: Request,
        *,
        actor: str,
        action: str,
        target: str,
    ) -> None:
        if approval is None:
            if auth.required:
                raise HTTPException(status_code=503, detail="high-risk approval unavailable")
        else:
            consume_request_approval(
                request,
                approval,
                actor=actor,
                action=action,
                target=target,
            )
        _record(
            request=request,
            actor=actor,
            action=action,
            target=target,
            outcome="attempted",
        )

    @router.get("/auth/status")
    async def auth_status(request: Request) -> dict:
        # 公开:前端据此决定是否弹登录。
        actor = auth.actor(request)
        return {
            "authRequired": auth.required,
            "authenticated": actor is not None,
            "role": (
                "operator"
                if actor == "local:admin" or (not auth.required and actor == "local:development")
                else "member"
                if actor is not None
                else None
            ),
        }

    @router.get("/apps", dependencies=[Depends(_require_auth)])
    async def list_apps() -> dict:
        try:
            containers = await run_in_threadpool(client.list_containers)
        except DockerUnavailable:
            return {
                "available": False,
                "apps": [],
                "error": "Docker control is unavailable",
            }
        apps = build_catalog(containers)
        return {"available": True, "apps": [a.to_dict() for a in apps], "error": None}

    @router.post("/apps/{container_id}/start")
    async def start_app(
        container_id: str,
        request: Request,
        actor: str = Depends(_require_operator),
    ) -> dict:
        target = _validated(container_id)
        action = "app.start"
        _authorize_control(request, actor=actor, action=action, target=target)
        try:
            await run_in_threadpool(client.start, target)
        except DockerControlDenied as exc:
            _record(
                request=request,
                actor=actor,
                action=action,
                target=target,
                outcome="failed",
                metadata={"reason": "docker control denied"},
            )
            raise HTTPException(
                status_code=403, detail="application control is not allowed"
            ) from exc
        except DockerUnavailable as exc:
            _record(
                request=request,
                actor=actor,
                action=action,
                target=target,
                outcome="failed",
                metadata={"reason": "docker control unavailable"},
            )
            raise HTTPException(
                status_code=503, detail="application control is unavailable"
            ) from exc
        _record(
            request=request,
            actor=actor,
            action=action,
            target=target,
            outcome="succeeded",
        )
        return {"ok": True}

    @router.post("/apps/{container_id}/stop")
    async def stop_app(
        container_id: str,
        request: Request,
        actor: str = Depends(_require_operator),
    ) -> dict:
        target = _validated(container_id)
        action = "app.stop"
        _authorize_control(request, actor=actor, action=action, target=target)
        try:
            await run_in_threadpool(client.stop, target)
        except DockerControlDenied as exc:
            _record(
                request=request,
                actor=actor,
                action=action,
                target=target,
                outcome="failed",
                metadata={"reason": "docker control denied"},
            )
            raise HTTPException(
                status_code=403, detail="application control is not allowed"
            ) from exc
        except DockerUnavailable as exc:
            _record(
                request=request,
                actor=actor,
                action=action,
                target=target,
                outcome="failed",
                metadata={"reason": "docker control unavailable"},
            )
            raise HTTPException(
                status_code=503, detail="application control is unavailable"
            ) from exc
        _record(
            request=request,
            actor=actor,
            action=action,
            target=target,
            outcome="succeeded",
        )
        return {"ok": True}

    return router
