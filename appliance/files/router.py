"""NAS 文件管理器 HTTP API。

GET  /api/appliance/files/list?path=<rel>     列目录
POST /api/appliance/files/mkdir               新建目录
POST /api/appliance/files/move                移动/重命名
POST /api/appliance/files/trash               删除 → 移入回收站(非物理删除)
GET  /api/appliance/files/trash               列回收站
POST /api/appliance/files/trash/restore       从回收站恢复
POST /api/appliance/files/trash/empty         清空回收站(唯一物理删除路径)

全部需登录(与启动器同一 JWT)。路径越权(..)返回 400。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from appliance.files.manager import FileManager, PathEscape
from appliance.security import make_auth_dependency


class _MkdirBody(BaseModel):
    path: str


class _MoveBody(BaseModel):
    src: str
    dst: str


class _PathBody(BaseModel):
    path: str


class _IdBody(BaseModel):
    id: str


def create_files_router(
    manager: FileManager,
    jwt_secret: str | None = None,
) -> APIRouter:
    router = APIRouter(
        prefix="/api/appliance/files",
        tags=["appliance", "files"],
        dependencies=[Depends(make_auth_dependency(jwt_secret))],
    )

    def _guard(fn, *args):
        try:
            return fn(*args)
        except PathEscape as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"not found: {exc}") from exc
        except FileExistsError as exc:
            raise HTTPException(status_code=409, detail=f"exists: {exc}") from exc
        except (NotADirectoryError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/list")
    async def list_dir(path: str = "") -> dict:
        entries = await run_in_threadpool(_guard, manager.list_dir, path)
        return {"path": path, "entries": [e.to_dict() for e in entries]}

    @router.post("/mkdir")
    async def mkdir(body: _MkdirBody) -> dict:
        entry = await run_in_threadpool(_guard, manager.mkdir, body.path)
        return {"ok": True, "entry": entry.to_dict()}

    @router.post("/move")
    async def move(body: _MoveBody) -> dict:
        entry = await run_in_threadpool(_guard, manager.move, body.src, body.dst)
        return {"ok": True, "entry": entry.to_dict()}

    @router.post("/trash")
    async def trash(body: _PathBody) -> dict:
        record = await run_in_threadpool(_guard, manager.trash, body.path)
        return {"ok": True, "trashed": record}

    @router.get("/trash")
    async def list_trash() -> dict:
        return {"entries": await run_in_threadpool(manager.list_trash)}

    @router.post("/trash/restore")
    async def restore(body: _IdBody) -> dict:
        entry = await run_in_threadpool(_guard, manager.restore, body.id)
        return {"ok": True, "entry": entry.to_dict()}

    @router.post("/trash/empty")
    async def empty_trash() -> dict:
        count = await run_in_threadpool(manager.empty_trash)
        return {"ok": True, "emptied": count}

    return router
