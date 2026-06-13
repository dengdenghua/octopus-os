"""启动器的 HTTP API。

GET  /api/appliance/auth/status          公开:是否需要登录 / 当前是否已登录
GET  /api/appliance/apps                  应用列表(需登录)
POST /api/appliance/apps/{id}/start       (需登录)
POST /api/appliance/apps/{id}/stop        (需登录)

鉴权:appliance 单用户模式下,登录走 runtime 现成的 /api/auth/local/login,
签发 HS256 JWT;这里用同一 jwt_secret 校验 Bearer token 保护应用接口。
jwt_secret 为 None(未启用认证)时全部放行,便于无认证的本地开发。

start/stop 操作 docker.sock(root 等价权限),P2 在此接入
runtime/safety/approval 审批门;P1 由登录 + 前端确认对话框把关。
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from appliance.app_registry.catalog import build_catalog
from appliance.app_registry.docker_client import DockerClient, DockerUnavailable

_CONTAINER_ID = re.compile(r"^[0-9a-f]{12,64}$")


def _bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    return None


def _is_authenticated(request: Request, jwt_secret: str | None) -> bool:
    if not jwt_secret:
        return True  # 未启用认证(本地开发)
    token = _bearer_token(request)
    if not token:
        return False
    from runtime.safety.auth.identity import JWTError, verify_jwt_hs256

    try:
        verify_jwt_hs256(token, secret=jwt_secret)
    except JWTError:
        return False
    return True


def create_appliance_router(
    docker: DockerClient | None = None,
    jwt_secret: str | None = None,
) -> APIRouter:
    client = docker or DockerClient()
    router = APIRouter(prefix="/api/appliance", tags=["appliance"])

    def _require_auth(request: Request) -> None:
        if not _is_authenticated(request, jwt_secret):
            raise HTTPException(status_code=401, detail="authentication required")

    def _validated(container_id: str) -> str:
        if not _CONTAINER_ID.match(container_id):
            raise HTTPException(status_code=422, detail="invalid container id")
        return container_id

    @router.get("/auth/status")
    async def auth_status(request: Request) -> dict:
        # 公开:前端据此决定是否弹登录。
        return {
            "authRequired": jwt_secret is not None,
            "authenticated": _is_authenticated(request, jwt_secret),
        }

    @router.get("/apps", dependencies=[Depends(_require_auth)])
    async def list_apps() -> dict:
        try:
            containers = await run_in_threadpool(client.list_containers)
        except DockerUnavailable as exc:
            return {"available": False, "apps": [], "error": str(exc)}
        apps = build_catalog(containers)
        return {"available": True, "apps": [a.to_dict() for a in apps], "error": None}

    @router.post("/apps/{container_id}/start", dependencies=[Depends(_require_auth)])
    async def start_app(container_id: str) -> dict:
        try:
            await run_in_threadpool(client.start, _validated(container_id))
        except DockerUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"ok": True}

    @router.post("/apps/{container_id}/stop", dependencies=[Depends(_require_auth)])
    async def stop_app(container_id: str) -> dict:
        try:
            await run_in_threadpool(client.stop, _validated(container_id))
        except DockerUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"ok": True}

    return router
