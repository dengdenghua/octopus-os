"""启动器的 HTTP API。

GET  /api/appliance/apps                 应用列表(docker 不可用时优雅降级)
POST /api/appliance/apps/{id}/start
POST /api/appliance/apps/{id}/stop

start/stop 操作 docker.sock(root 等价权限)——P2 在此接入
runtime/safety/approval 审批门;P1 由前端确认对话框把关。
"""

from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException
from starlette.concurrency import run_in_threadpool

from appliance.app_registry.catalog import build_catalog
from appliance.app_registry.docker_client import DockerClient, DockerUnavailable

_CONTAINER_ID = re.compile(r"^[0-9a-f]{12,64}$")


def create_appliance_router(docker: DockerClient | None = None) -> APIRouter:
    client = docker or DockerClient()
    router = APIRouter(prefix="/api/appliance", tags=["appliance"])

    def _validated(container_id: str) -> str:
        if not _CONTAINER_ID.match(container_id):
            raise HTTPException(status_code=422, detail="invalid container id")
        return container_id

    @router.get("/apps")
    async def list_apps() -> dict:
        try:
            containers = await run_in_threadpool(client.list_containers)
        except DockerUnavailable as exc:
            return {"available": False, "apps": [], "error": str(exc)}
        apps = build_catalog(containers)
        return {"available": True, "apps": [a.to_dict() for a in apps], "error": None}

    @router.post("/apps/{container_id}/start")
    async def start_app(container_id: str) -> dict:
        try:
            await run_in_threadpool(client.start, _validated(container_id))
        except DockerUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"ok": True}

    @router.post("/apps/{container_id}/stop")
    async def stop_app(container_id: str) -> dict:
        try:
            await run_in_threadpool(client.stop, _validated(container_id))
        except DockerUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"ok": True}

    return router
