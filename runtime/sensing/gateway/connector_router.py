"""连接器网关路由 — 浏览/安装/认证编排/启停。

对齐 WorkBuddy 连接器商城与 Codex connector_* 体系,给前端一套「连接器市场」:

  GET    /api/connectors                      连接器列表(含安装/启用状态)
  GET    /api/connectors/{id}                 单个连接器详情
  POST   /api/connectors/{id}/install         安装(技能→skills, MCP 登记,默认禁用)
  DELETE /api/connectors/{id}/install         卸载
  POST   /api/connectors/{id}/enable          启用 MCP(需已连接)
  POST   /api/connectors/{id}/disable         禁用
  GET    /api/connectors/{id}/status          认证状态
  POST   /api/connectors/{id}/connect         认证编排(带 tokens / 起设备流 / 返回 CLI 命令)
  POST   /api/connectors/{id}/disconnect      断开并清除凭据
  GET    /api/connectors/{id}/device-flow     查询进行中的官网授权(verification_uri/user_code)
  DELETE /api/connectors/{id}/device-flow     取消官网授权(终止后台 CLI 登录进程)
  GET    /api/connectors/{id}/headers         解析出的 auth 注入头(供 MCP 代理用)

后端实现: runtime/platform/connectors/{credential_store,connector_registry,auth_orchestrator}
数据源:  extensions/workbuddy-connectors/(WorkBuddy 108 连接器 fork)
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

try:
    from fastapi import APIRouter, Depends, HTTPException, Query, Request

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    APIRouter = None  # type: ignore[assignment, misc]
    Depends = None  # type: ignore[assignment, misc]
    HTTPException = None  # type: ignore[assignment, misc]
    Query = None  # type: ignore[assignment, misc]
    Request = None  # type: ignore[assignment, misc]

from runtime.platform.connectors.auth_orchestrator import RefreshCleanupRequiredError
from runtime.safety.auth.scope import scope_from_request
from runtime.sensing._fastapi_guard import require_fastapi
from runtime.sensing.gateway._device_flow_models import (
    DeviceFlowCancelResponse,
    DeviceFlowResponse,
)


def create_connector_router(
    *,
    registry: Any = None,
    orchestrator: Any = None,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
    auth_injection_rules: list[dict[str, Any]] | None = None,
) -> Any:
    require_fastapi(__name__)

    if registry is None:
        from runtime.platform.connectors.connector_registry import ConnectorRegistry

        registry = ConnectorRegistry()
    if orchestrator is None:
        from runtime.platform.connectors.auth_orchestrator import AuthOrchestrator

        orchestrator = AuthOrchestrator(auth_injection_rules=auth_injection_rules)

    async def _auth_dep(request: Request) -> AsyncIterator[None]:
        from runtime.adapters.web_auth import _resolve_actor
        from runtime.platform.capabilities.tenant_context import (
            use_capability_scope,
        )

        _resolve_actor(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )
        with use_capability_scope(scope_from_request(request)):
            yield

    def _operator_dep(request: Request) -> None:
        from runtime.safety.auth.principal import require_roles

        require_roles(
            request,
            identity_store,
            require_auth,
            ("admin", "operator"),
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )

    router = APIRouter(tags=["connectors"], dependencies=[Depends(_auth_dep)])

    def _refresh_cleanup_conflict(exc: RefreshCleanupRequiredError) -> Any:
        return HTTPException(status_code=409, detail=exc.detail)

    def _get_connector(cid: str) -> Any:
        conn = registry.get(cid)
        if conn is None:
            raise HTTPException(404, f"connector not found: {cid}")
        return conn

    @router.get("/api/connectors")
    def list_connectors(
        search: str | None = None,
        ctype: str | None = Query(default=None, alias="type"),
        limit: int = Query(default=500, ge=1, le=1000),
    ) -> dict[str, Any]:
        conns = registry.list()
        if ctype:
            conns = [c for c in conns if c["type"] == ctype]
        if search:
            q = search.lower()
            conns = [
                c
                for c in conns
                if q in c["name"].lower()
                or q in c["name_zh"].lower()
                or q in c["description_zh"].lower()
                or q in c["id"].lower()
            ]
        return {"connectors": conns[:limit], "total": len(conns)}

    @router.get("/api/connectors/{connector_id}")
    def connector_detail(connector_id: str) -> dict[str, Any]:
        conn = _get_connector(connector_id)
        state = registry._state().get(connector_id) or {}
        return {
            **conn.to_dict(
                installed=bool(state.get("installed")), enabled=bool(state.get("enabled"))
            ),
            "mcp_config": conn.mcp_servers,
            "cli": conn.cli,
            "skills_dir": str(conn.skills_dir) if conn.skills_dir else None,
            "examples_zh": conn.examples_zh,
        }

    @router.post(
        "/api/connectors/{connector_id}/install",
        dependencies=[Depends(_operator_dep)],
    )
    def connector_install(connector_id: str) -> dict[str, Any]:
        conn = _get_connector(connector_id)
        return orchestrator.run_connector_lifecycle(
            conn,
            lambda: registry.install(connector_id),
        )

    @router.delete(
        "/api/connectors/{connector_id}/install",
        dependencies=[Depends(_operator_dep)],
    )
    def connector_uninstall(connector_id: str) -> dict[str, Any]:
        conn = _get_connector(connector_id)
        try:
            removed = orchestrator.run_connector_lifecycle(
                conn,
                lambda: registry.uninstall(connector_id),
                cancel_device_flow=True,
            )
        except RefreshCleanupRequiredError as exc:
            raise _refresh_cleanup_conflict(exc) from exc
        if not removed:
            raise HTTPException(404, f"connector not installed: {connector_id}")
        return {"installed": False, "connector_id": connector_id}

    @router.post(
        "/api/connectors/{connector_id}/enable",
        dependencies=[Depends(_operator_dep)],
    )
    async def connector_enable(connector_id: str, request: Request) -> dict[str, Any]:
        conn = _get_connector(connector_id)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 - an empty body means no grant supplied
            body = {}
        try:
            if "grant_permissions" in body:
                registry.grant_permissions(connector_id, body["grant_permissions"])
            enabled = await asyncio.to_thread(
                orchestrator.run_connector_lifecycle,
                conn,
                lambda: registry.set_enabled(connector_id, True),
            )
        except PermissionError as exc:
            raise HTTPException(
                409,
                {
                    "code": "PERMISSION_REVIEW_REQUIRED",
                    "message": str(exc),
                },
            ) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if not enabled:
            raise HTTPException(404, f"connector not installed: {connector_id}")
        return {"enabled": True, "connector_id": connector_id}

    @router.post(
        "/api/connectors/{connector_id}/disable",
        dependencies=[Depends(_operator_dep)],
    )
    def connector_disable(connector_id: str) -> dict[str, Any]:
        conn = _get_connector(connector_id)
        try:
            disabled = orchestrator.run_connector_lifecycle(
                conn,
                lambda: registry.set_enabled(connector_id, False),
                cancel_device_flow=True,
            )
        except RefreshCleanupRequiredError as exc:
            raise _refresh_cleanup_conflict(exc) from exc
        if not disabled:
            raise HTTPException(404, f"connector not installed: {connector_id}")
        return {"enabled": False, "connector_id": connector_id}

    @router.get("/api/connectors/{connector_id}/status")
    def connector_status(connector_id: str) -> dict[str, Any]:
        conn = _get_connector(connector_id)
        return orchestrator.status(conn)

    @router.post(
        "/api/connectors/{connector_id}/connect",
        dependencies=[Depends(_operator_dep)],
        response_model=DeviceFlowResponse,
        response_model_exclude_unset=True,
    )
    async def connector_connect(connector_id: str, request: Request) -> dict[str, Any]:
        conn = _get_connector(connector_id)
        body: dict[str, Any] = {}
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 — 无 body / 非 JSON 一律按空处理
            body = {}
        tokens = body.get("tokens") or None
        run_cli = bool(body.get("run_cli"))

        def connect_installed() -> dict[str, Any]:
            if connector_id not in registry.installed_ids():
                raise ValueError(f"connector not installed: {connector_id}")
            if "grant_permissions" in body:
                registry.grant_permissions(connector_id, body["grant_permissions"])
            registry.require_permissions(connector_id)
            return orchestrator.connect(
                conn,
                tokens=tokens,
                run_cli=run_cli,
            )

        try:
            return await asyncio.to_thread(
                orchestrator.run_connector_lifecycle,
                conn,
                connect_installed,
            )
        except RefreshCleanupRequiredError as exc:
            raise _refresh_cleanup_conflict(exc) from exc
        except PermissionError as exc:
            raise HTTPException(
                409,
                {
                    "code": "PERMISSION_REVIEW_REQUIRED",
                    "message": str(exc),
                },
            ) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post(
        "/api/connectors/{connector_id}/disconnect",
        dependencies=[Depends(_operator_dep)],
    )
    def connector_disconnect(connector_id: str) -> dict[str, Any]:
        conn = _get_connector(connector_id)
        try:
            return orchestrator.disconnect(conn)
        except RefreshCleanupRequiredError as exc:
            raise _refresh_cleanup_conflict(exc) from exc

    @router.get(
        "/api/connectors/{connector_id}/device-flow",
        dependencies=[Depends(_operator_dep)],
        response_model=DeviceFlowResponse,
        response_model_exclude_unset=True,
    )
    def connector_device_flow(connector_id: str) -> dict[str, Any]:
        """查询进行中的设备流授权(verification_uri / user_code / 剩余时效)。

        前端弹窗被刷新/重开后靠这个接口恢复授权态,不必重新起一次 CLI 登录。
        """
        conn = _get_connector(connector_id)
        return orchestrator.device_flow_status(conn)

    @router.delete(
        "/api/connectors/{connector_id}/device-flow",
        dependencies=[Depends(_operator_dep)],
        response_model=DeviceFlowCancelResponse,
        response_model_exclude_unset=True,
    )
    def connector_device_flow_cancel(
        connector_id: str,
        expected_flow_id: str = Query(..., min_length=1, max_length=128),
    ) -> dict[str, Any]:
        """取消指定代际的设备流，迟到的旧弹窗不影响新授权。"""
        conn = _get_connector(connector_id)
        return orchestrator.cancel_device_flow(
            conn,
            expected_flow_id=expected_flow_id,
        )

    @router.get(
        "/api/connectors/{connector_id}/headers",
        dependencies=[Depends(_operator_dep)],
    )
    def connector_headers(connector_id: str) -> dict[str, Any]:
        conn = _get_connector(connector_id)
        try:
            registry.require_permissions(connector_id, ["account.credentials"])
        except PermissionError as exc:
            raise HTTPException(
                409,
                {
                    "code": "PERMISSION_REVIEW_REQUIRED",
                    "message": str(exc),
                },
            ) from exc
        headers = orchestrator.resolve_headers(conn)
        return {
            "configured": bool(headers),
            "header_names": sorted(str(name) for name in headers),
        }

    return router
