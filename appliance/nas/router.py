"""NAS 管控面的 HTTP API。

    GET    /api/appliance/nas/status                 各子系统可用性(公开)
    GET    /api/appliance/nas/disks                  磁盘与分区
    GET    /api/appliance/nas/pools                  ZFS 池 + 数据集
    GET    /api/appliance/nas/smart/{device}         单盘 SMART
    GET    /api/appliance/nas/shares                 共享清单
    POST   /api/appliance/nas/shares                 新建/更新共享(写)
    DELETE /api/appliance/nas/shares/{name}          删除共享(写)
    POST   /api/appliance/nas/shares/apply           渲染配置 + reload(写)

鉴权沿用 app_registry 的做法:同一 jwt_secret 校验 Bearer;``jwt_secret`` 为 None
时全部放行,便于本地开发。

**写操作必须过审批门**:POST/DELETE/apply 会改动系统级配置(SMB/NFS),P3 阶段
在此接入 ``runtime/safety/approval``。当前由登录 + 前端二次确认把关,
接入点已在每个写路由上方用 TODO(P3-approval) 标出。
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from appliance.nas.shares import Share, ShareError, ShareManager
from appliance.nas.storage import StorageInspector
from appliance.security import is_authenticated, make_auth_dependency


class ShareIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    path: str = Field(min_length=1)
    protocols: list[str] = Field(default_factory=lambda: ["smb"])
    read_only: bool = False
    guest_ok: bool = False
    comment: str = ""


def _default_share_manager() -> ShareManager:
    root = os.environ.get("OCTOPUS_NAS_ROOT") or os.path.join(
        os.environ.get("OCTOPUS_DATA_DIR", "/data"), "nas"
    )
    return ShareManager(root)


def create_nas_router(
    inspector: StorageInspector | None = None,
    shares: ShareManager | None = None,
    jwt_secret: str | None = None,
) -> APIRouter:
    insp = inspector or StorageInspector()
    mgr = shares or _default_share_manager()
    router = APIRouter(prefix="/api/appliance/nas", tags=["nas"])

    _require_auth = make_auth_dependency(jwt_secret)

    @router.get("/status")
    async def status(request: Request) -> dict[str, Any]:
        """公开:前端据此灰掉不可用的面板。"""
        caps = await run_in_threadpool(insp.capabilities)
        return {
            "authenticated": is_authenticated(request, jwt_secret),
            "capabilities": caps,
            "shares": len(await run_in_threadpool(mgr.list_shares)),
        }

    @router.get("/disks", dependencies=[Depends(_require_auth)])
    async def disks() -> dict[str, Any]:
        try:
            items = await run_in_threadpool(insp.list_disks)
        except Exception as exc:  # noqa: BLE001 - 设备探测异常不应 500 掉整个面板
            return {"available": False, "disks": [], "error": str(exc)}
        return {
            "available": bool(items),
            "disks": [d.to_dict() for d in items],
            "error": None,
        }

    @router.get("/pools", dependencies=[Depends(_require_auth)])
    async def pools(with_datasets: bool = True) -> dict[str, Any]:
        try:
            items = await run_in_threadpool(insp.list_pools, with_datasets)
        except Exception as exc:  # noqa: BLE001
            return {"available": False, "pools": [], "error": str(exc)}
        return {
            "available": True,
            "pools": [p.to_dict() for p in items],
            "error": None,
        }

    @router.get("/smart/{device}", dependencies=[Depends(_require_auth)])
    async def smart(device: str) -> dict[str, Any]:
        # 设备名白名单在 StorageInspector.smart_health 内校验,非法名抛 ValueError。
        try:
            return await run_in_threadpool(insp.smart_health, device)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            return {"available": False, "device": device, "error": str(exc)}

    @router.get("/shares", dependencies=[Depends(_require_auth)])
    async def list_shares() -> dict[str, Any]:
        items = await run_in_threadpool(mgr.list_shares)
        return {"shares": [s.to_dict() for s in items]}

    @router.post("/shares", dependencies=[Depends(_require_auth)])
    async def upsert_share(payload: ShareIn) -> dict[str, Any]:
        # TODO(P3-approval): 接入 runtime/safety/approval 审批门。
        try:
            saved = await run_in_threadpool(
                mgr.add_share,
                Share(
                    name=payload.name,
                    path=payload.path,
                    protocols=payload.protocols,
                    read_only=payload.read_only,
                    guest_ok=payload.guest_ok,
                    comment=payload.comment,
                ),
            )
        except ShareError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"ok": True, "share": saved.to_dict()}

    @router.delete("/shares/{name}", dependencies=[Depends(_require_auth)])
    async def delete_share(name: str) -> dict[str, Any]:
        # TODO(P3-approval): 接入审批门。
        try:
            removed = await run_in_threadpool(mgr.remove_share, name)
        except ShareError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if not removed:
            raise HTTPException(status_code=404, detail="share not found")
        return {"ok": True}

    @router.post("/shares/apply", dependencies=[Depends(_require_auth)])
    async def apply_shares() -> dict[str, Any]:
        """渲染 + 原子落盘 + reload smbd/nfs。"""
        # TODO(P3-approval): 接入审批门 —— 这是唯一会动系统配置的写操作。
        return await run_in_threadpool(mgr.apply)

    return router


__all__ = ["ShareIn", "create_nas_router"]
