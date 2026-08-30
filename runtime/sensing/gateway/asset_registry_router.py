"""统一资产仓库路由 —— 插件 / 技能 / 角色(WorkBuddy + Codex + 本地 + 内置)归一视图。

数据源:``runtime.platform.assets.asset_registry``(~/.echo/assets/,统一 index.json)。

- GET  /api/assets                统一资产列表(kind/source/search 过滤)+ 汇总
- GET  /api/assets/{kind}/{asset_id}  单个资产详情
- POST /api/assets/sync            重建统一仓库(幂等:聚合所有来源 → 写 index + 快照)

设计:只读浏览统一仓库 + 显式重建,不改变各来源的既有读写路径(兼容层)。
"""

from __future__ import annotations

from typing import Any

try:
    from fastapi import APIRouter, Depends, HTTPException, Query, Request

    FASTAPI_AVAILABLE = True
except Exception:  # pragma: no cover - fastapi optional at import time
    FASTAPI_AVAILABLE = False
    APIRouter = None  # type: ignore[assignment, misc]
    Depends = None  # type: ignore[assignment, misc]
    HTTPException = None  # type: ignore[assignment, misc]
    Query = None  # type: ignore[assignment, misc]
    Request = None  # type: ignore[assignment, misc]

from runtime.sensing._fastapi_guard import require_fastapi


def create_asset_registry_router(
    *,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
) -> Any:
    require_fastapi()

    def _auth_dep(request: Request) -> None:
        if not require_auth:
            return
        from runtime.adapters.web_auth import _resolve_actor

        _resolve_actor(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )

    def _admin_dep(request: Request) -> None:
        from runtime.safety.auth.principal import require_roles

        require_roles(
            request,
            identity_store,
            require_auth,
            ("admin",),
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )

    router = APIRouter(tags=["assets"], dependencies=[Depends(_auth_dep)])

    @router.get("/api/assets")
    def list_unified_assets(
        kind: str | None = None,
        source: str | None = None,
        search: str | None = None,
        limit: int = Query(default=500, ge=1, le=2000),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        from runtime.platform.assets.asset_registry import list_assets, summary

        s = summary()
        if s is None:
            raise HTTPException(404, "统一资产仓库未初始化,请先 POST /api/assets/sync")
        items = list_assets(kind=kind, source=source, search=search)
        return {
            "summary": s,
            "total": len(items),
            "items": items[offset : offset + limit],
            "kind_filter": kind,
            "source_filter": source,
        }

    @router.get("/api/assets/{kind}/{asset_id}")
    def get_unified_asset(kind: str, asset_id: str) -> dict[str, Any]:
        from runtime.platform.assets.asset_registry import get_asset

        item = get_asset(kind, asset_id)
        if item is None:
            raise HTTPException(404, f"asset not found: {kind}/{asset_id}")
        return item

    @router.post("/api/assets/sync", dependencies=[Depends(_admin_dep)])
    def sync_unified_assets() -> dict[str, Any]:
        from runtime.platform.assets.asset_registry import sync_assets

        return sync_assets()

    return router
