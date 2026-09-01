"""统一「插件」市场路由 —— 所有外部能力(WorkBuddy MCP 服务 + Codex 插件)统一叫插件。

给前端一套统一「插件市场」:

  GET    /api/capabilities                   统一列表(连接器 + 插件)
  GET    /api/capabilities/{id}              单个能力详情
  POST   /api/capabilities/{id}/install      安装(技能→skills, 连接器 MCP 登记)
  DELETE /api/capabilities/{id}/install      卸载
  POST   /api/capabilities/{id}/enable       启用
  POST   /api/capabilities/{id}/disable      禁用
  GET    /api/capabilities/{id}/status       认证/连接状态
  POST   /api/capabilities/{id}/connect      认证编排(连接器)
  POST   /api/capabilities/{id}/disconnect   断开
  GET    /api/capabilities/{id}/device-flow 恢复 CLI 设备流授权态
  DELETE /api/capabilities/{id}/device-flow 取消 CLI 设备流并回收进程
  GET    /api/capabilities/{id}/headers      认证注入头

统一模型: runtime/platform/capabilities/capability_registry.py
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import mimetypes
from collections.abc import AsyncIterator
from typing import Any

try:
    from fastapi import APIRouter, Depends, HTTPException, Query, Request
    from fastapi.responses import FileResponse

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    APIRouter = None  # type: ignore[assignment, misc]
    Depends = None  # type: ignore[assignment, misc]
    HTTPException = None  # type: ignore[assignment, misc]
    Query = None  # type: ignore[assignment, misc]
    Request = None  # type: ignore[assignment, misc]
    FileResponse = None  # type: ignore[assignment, misc]

from runtime.platform.connectors import oauth_support
from runtime.platform.connectors.auth_orchestrator import RefreshCleanupRequiredError
from runtime.safety.auth.scope import scope_from_request
from runtime.sensing._fastapi_guard import require_fastapi
from runtime.sensing.gateway._device_flow_models import (
    DeviceFlowCancelResponse,
    DeviceFlowResponse,
)


def create_capability_router(
    *,
    registry: Any = None,
    codex_accounts: Any = None,
    model_provider_plugins: Any = None,
    identity_store: Any = None,
    require_auth: bool = False,
    allow_local_user_plugin_lifecycle: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
) -> Any:
    require_fastapi(__name__)

    if registry is None:
        from runtime.platform.capabilities.capability_registry import (
            CapabilityRegistry,
        )

        registry = CapabilityRegistry()

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

    def _can_manage_local_plugin(request: Request) -> bool:
        """Return whether this request may mutate a process-local plugin.

        The local desktop deployment has one trusted workspace and one active
        user, so making a bundled plugin installable is the expected personal
        app lifecycle. Shared/server deployments keep the operator boundary
        because the same registry is process-global for every tenant.
        """

        if not require_auth or allow_local_user_plugin_lifecycle:
            return True
        principal = getattr(getattr(request, "state", None), "principal", None)
        roles = set(getattr(principal, "roles", ()) or ())
        return bool(roles.intersection({"admin", "operator"}))

    def _annotate_lifecycle(item: dict[str, Any], request: Request) -> dict[str, Any]:
        public = dict(item)
        if (
            public.get("source") == "codex_plugin"
            and public.get("is_codex_marketplace") is not True
        ):
            manageable = _can_manage_local_plugin(request)
            public["lifecycle_manageable"] = manageable
            if not manageable:
                public["installable"] = False
        elif public.get("is_codex_marketplace") is True:
            # App Server state is principal-scoped, so any authenticated user
            # can manage their own installation.
            public["lifecycle_manageable"] = True
        return public

    def _require_local_lifecycle(item: dict[str, Any], request: Request) -> None:
        is_user_managed_plugin = item.get("source") == "codex_plugin" or bool(
            item.get("model_provider")
        )
        if is_user_managed_plugin and _can_manage_local_plugin(request):
            return
        _operator_dep(request)

    router = APIRouter(tags=["capabilities"], dependencies=[Depends(_auth_dep)])
    logger = logging.getLogger(__name__)

    def _refresh_cleanup_conflict(exc: RefreshCleanupRequiredError) -> Any:
        return HTTPException(status_code=409, detail=exc.detail)

    def _get(cid: str) -> dict[str, Any]:
        item = registry.get(cid)
        if item is None:
            raise HTTPException(404, f"capability not found: {cid}")
        return registry._public(item)

    async def _get_any(cid: str, request: Request) -> dict[str, Any]:
        item = registry.get(cid)
        if item is not None:
            return _annotate_lifecycle(registry._public(item), request)
        if codex_accounts is not None and cid.startswith("codex-marketplace:"):
            try:
                plugins = await codex_accounts.list_plugins(scope_from_request(request))
            except Exception as exc:  # noqa: BLE001 - mapped to a bounded gateway error
                raise HTTPException(502, "Codex plugin catalog is unavailable") from exc
            match = next((plugin for plugin in plugins if plugin.get("id") == cid), None)
            if match is not None:
                return _annotate_lifecycle(dict(match), request)
        raise HTTPException(404, f"capability not found: {cid}")

    async def _get_lifecycle_item(cid: str, request: Request) -> dict[str, Any]:
        """Resolve a mutable capability without leaking registry membership.

        Known personal/model-provider plugins may be managed by an ordinary
        authenticated user. For an unknown id there is no such ownership
        evidence, so enforce the operator boundary before returning 404.
        """

        try:
            return await _get_any(cid, request)
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            _operator_dep(request)
            raise

    # 需要手动填 token 的判定:既不能跳网页 OAuth,也没有 CLI 设备流。
    def _is_manual_token_only(item: dict[str, Any]) -> bool:
        # Model-provider plugins intentionally use an API Key form.  They are
        # first-class installable adapters, not obscure MCP token fallbacks,
        # so keep them visible in the normal marketplace.
        if item.get("model_provider"):
            return False
        if item.get("auth_mode") not in ("token", "oneid-token", "mcp", "oauth"):
            return False
        if item.get("oauth_supported") is True:
            return False
        if item.get("oauth_provider"):
            return False
        return not item.get("has_cli_auth")

    @router.get("/api/capabilities")
    async def list_capabilities(
        request: Request,
        search: str | None = None,
        source: str | None = Query(default=None, alias="source"),
        ctype: str | None = Query(default=None, alias="type"),
        limit: int = Query(default=500, ge=1, le=1000),
        offset: int = Query(default=0, ge=0),
        include_manual: bool = Query(
            default=False,
            alias="include_manual",
            description="默认隐藏只能手动填 token 的插件;传 true 全部返回。",
        ),
        force_refetch: bool = Query(default=False, alias="force_refetch"),
    ) -> dict[str, Any]:
        items = registry.list()
        if codex_accounts is not None and source != "connector":
            try:
                remote_plugins = await codex_accounts.list_plugins(
                    scope_from_request(request),
                    force_refetch=force_refetch,
                )
                # The App Server catalog owns install state for personal Codex
                # applications.  A checked-out/bundled copy is only an offline
                # source cache; letting it win here routed Browser/Documents/etc.
                # through the process-global legacy registry and made remote
                # uninstall/reinstall unreachable.
                remote_provider_ids = {
                    str(plugin.get("provider_id") or plugin.get("id") or "")
                    for plugin in remote_plugins
                }
                items = [
                    item
                    for item in items
                    if item.get("source") != "codex_plugin"
                    or str(item.get("provider_id") or item.get("id") or "")
                    not in remote_provider_ids
                ]
                items.extend(dict(plugin) for plugin in remote_plugins)
            except Exception as exc:  # noqa: BLE001 - local catalog remains usable offline
                logger.warning(
                    "Codex plugin catalog unavailable; using local capabilities: %s", exc
                )
        if source:
            items = [i for i in items if i.get("source") == source]
        if ctype:
            items = [i for i in items if i.get("type") == ctype]
        if search:
            q = search.lower()
            items = [
                i
                for i in items
                if q in str(i.get("name", "")).lower()
                or q in str(i.get("name_zh", "")).lower()
                or q in str(i.get("description", "")).lower()
                or q in str(i.get("description_zh", "")).lower()
                or q in str(i.get("id", "")).lower()
            ]
        total = len(items)
        # Only project and annotate the requested page. OAuth discovery and
        # public-shape conversion are the expensive parts of this endpoint.
        items = items[offset : offset + limit]
        # 网页 OAuth 授权支持探测:后台并发 + 磁盘缓存(不阻塞列表返回)
        urls: list[str] = []
        for i in items:
            urls.extend(
                str(s.get("url", ""))
                for s in i.get("mcp_servers", [])
                if isinstance(s, dict) and s.get("url")
            )
        oauth_support.prewarm(urls)
        items = [registry._public(i) for i in items]
        items = [oauth_support.annotate(i) for i in items]
        for i in items:
            i.update(_annotate_lifecycle(i, request))
            i["manual_token_only"] = _is_manual_token_only(i)
        if not include_manual:
            # 移除「只能手动填 token」且未安装的插件(已安装的保留以便管理/卸载)
            items = [i for i in items if not i["manual_token_only"] or i.get("installed")]
        return {"capabilities": items, "total": total}

    @router.get("/api/capabilities/{cid}")
    async def capability_detail(cid: str, request: Request) -> dict[str, Any]:
        return await _get_any(cid, request)

    @router.get("/api/capabilities/{cid}/install-plan")
    async def capability_install_plan(cid: str, request: Request) -> dict[str, Any]:
        item = await _get_lifecycle_item(cid, request)
        _require_local_lifecycle(item, request)
        try:
            return await asyncio.to_thread(registry.install_plan, cid)
        except KeyError as exc:
            raise HTTPException(404, f"capability not found: {cid}") from exc
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(409, "无法生成可验证的安装计划") from exc

    @router.get("/api/capabilities/{cid}/icon")
    async def capability_icon(cid: str, request: Request) -> Any:
        item = registry.get(cid)
        if item is not None:
            icon_path = registry.icon_path(cid)
        elif codex_accounts is not None and cid.startswith("codex-marketplace:"):
            try:
                icon_path = await codex_accounts.plugin_icon_path(
                    scope_from_request(request),
                    catalog_id=cid,
                )
            except Exception as exc:  # noqa: BLE001 - do not expose local paths/protocol errors
                raise HTTPException(404, f"capability icon not found: {cid}") from exc
        else:
            raise HTTPException(404, f"capability not found: {cid}")
        if icon_path is None or not icon_path.is_file():
            raise HTTPException(404, f"capability icon not found: {cid}")
        media_type = mimetypes.guess_type(icon_path.name)[0] or "application/octet-stream"
        return FileResponse(
            str(icon_path),
            media_type=media_type,
            headers={"Cache-Control": "public, max-age=86400"},
        )

    @router.post("/api/capabilities/{cid}/install")
    async def capability_install(cid: str, request: Request) -> dict[str, Any]:
        item = await _get_lifecycle_item(cid, request)
        if item.get("is_codex_marketplace") is True:
            if item.get("installable") is False:
                raise HTTPException(409, "Codex marketplace plugin is not installable")
            try:
                return await codex_accounts.install_plugin(
                    scope_from_request(request),
                    catalog_id=cid,
                )
            except Exception as exc:  # noqa: BLE001 - hide protocol internals from clients
                raise HTTPException(502, "Codex plugin installation failed") from exc
        _require_local_lifecycle(item, request)
        try:
            try:
                body = await request.json()
            except Exception:  # noqa: BLE001 - legacy clients may omit a body
                body = {}
            expected_plan_id = body.get("plan_id") if isinstance(body, dict) else None
            if expected_plan_id is not None:
                current_plan = await asyncio.to_thread(registry.install_plan, cid)
                if expected_plan_id != current_plan.get("plan_id"):
                    raise HTTPException(
                        409,
                        {
                            "code": "INSTALL_PLAN_STALE",
                            "message": "插件目录或依赖状态已变化，请重新确认安装计划。",
                        },
                    )
            # Cloud-backed built-ins may download and extract a sizeable
            # first-party archive. Keep that blocking filesystem/network work
            # off the ASGI event loop so the rest of the app stays responsive.
            return await asyncio.to_thread(registry.install, cid)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 - normalize lifecycle failures for the UI
            logger.warning(
                "Local capability installation failed for %s (%s)",
                cid,
                type(exc).__name__,
            )
            raise HTTPException(
                502,
                "插件包下载或安装失败，请稍后重试",
            ) from exc

    @router.delete("/api/capabilities/{cid}/install")
    async def capability_uninstall(cid: str, request: Request) -> dict[str, Any]:
        item = await _get_lifecycle_item(cid, request)
        if item.get("is_codex_marketplace") is True:
            try:
                return await codex_accounts.uninstall_plugin(
                    scope_from_request(request),
                    catalog_id=cid,
                )
            except Exception as exc:  # noqa: BLE001 - hide protocol internals from clients
                raise HTTPException(502, "Codex plugin uninstall failed") from exc
        _require_local_lifecycle(item, request)
        try:
            removed = await asyncio.to_thread(registry.uninstall, cid)
        except RefreshCleanupRequiredError as exc:
            raise _refresh_cleanup_conflict(exc) from exc
        if not removed:
            raise HTTPException(404, f"capability not installed: {cid}")
        if item.get("model_provider") and model_provider_plugins is not None:
            model_provider_plugins.remove(item)
        return {"installed": False, "capability_id": cid}

    @router.post("/api/capabilities/{cid}/enable")
    async def capability_enable(cid: str, request: Request) -> dict[str, Any]:
        item = await _get_lifecycle_item(cid, request)
        if item.get("is_codex_marketplace") is True:
            raise HTTPException(409, "Codex marketplace plugins are enabled during installation")
        _require_local_lifecycle(item, request)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 - an empty body means no grant
            body = {}
        grant_permissions = body.get("grant_permissions") if isinstance(body, dict) else None
        try:
            expected_plan_id = body.get("plan_id") if isinstance(body, dict) else None
            if expected_plan_id is not None:
                current_plan = await asyncio.to_thread(registry.install_plan, cid)
                if expected_plan_id != current_plan.get("plan_id"):
                    raise HTTPException(
                        409,
                        {
                            "code": "INSTALL_PLAN_STALE",
                            "message": "插件版本或依赖状态已变化，请重新确认权限。",
                        },
                    )
            if grant_permissions is not None:
                registry.grant_permissions(cid, grant_permissions)
            if not registry.set_enabled(cid, True):
                raise HTTPException(404, f"capability not installed: {cid}")
        except PermissionError as exc:
            raise HTTPException(
                409,
                {
                    "code": "PERMISSION_REVIEW_REQUIRED",
                    "permissions": list(item.get("permissions") or []),
                    "message": "请确认该插件的全部签名权限后再启用。",
                },
            ) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if item.get("model_provider"):
            if model_provider_plugins is None:
                registry.set_enabled(cid, False, revoke_credentials=False)
                raise HTTPException(503, "模型适配器服务尚未就绪")
            try:
                discovered = await asyncio.to_thread(
                    model_provider_plugins.validate,
                    item,
                    tokens=None,
                )
                configured = model_provider_plugins.configure(
                    item,
                    models=list(discovered.get("models") or []),
                    base_url=str(discovered.get("base_url") or "") or None,
                )
            except Exception as exc:  # noqa: BLE001 - restore the lifecycle state
                registry.set_enabled(cid, False, revoke_credentials=False)
                raise HTTPException(409, str(exc)) from exc
            return {"enabled": True, "capability_id": cid, **configured}
        return {"enabled": True, "capability_id": cid}

    @router.post("/api/capabilities/{cid}/disable")
    async def capability_disable(cid: str, request: Request) -> dict[str, Any]:
        item = await _get_lifecycle_item(cid, request)
        if item.get("is_codex_marketplace") is True:
            raise HTTPException(409, "Codex marketplace plugins are managed by App Server")
        _require_local_lifecycle(item, request)
        if item.get("model_provider") and model_provider_plugins is not None:
            model_provider_plugins.remove(item)
        try:
            disabled = registry.set_enabled(
                cid,
                False,
                revoke_credentials=False if item.get("model_provider") else None,
            )
        except RefreshCleanupRequiredError as exc:
            raise _refresh_cleanup_conflict(exc) from exc
        if not disabled:
            raise HTTPException(404, f"capability not installed: {cid}")
        return {"enabled": False, "capability_id": cid}

    @router.get("/api/capabilities/{cid}/status")
    async def capability_status(cid: str, request: Request) -> dict[str, Any]:
        item = await _get_any(cid, request)
        if item.get("is_codex_marketplace") is True:
            return {
                "connected": bool(item.get("installed") and item.get("enabled")),
                "installed": bool(item.get("installed")),
                "enabled": bool(item.get("enabled")),
                "auth_mode": item.get("auth_mode") or "none",
            }
        return registry.status(cid)

    @router.post(
        "/api/capabilities/{cid}/connect",
        response_model=DeviceFlowResponse,
        response_model_exclude_unset=True,
    )
    async def capability_connect(cid: str, request: Request) -> dict[str, Any]:
        item = await _get_lifecycle_item(cid, request)
        if item.get("is_codex_marketplace") is True:
            return {
                "connected": bool(item.get("installed")),
                "capability_id": cid,
                "message": "Codex 插件由 App Server 管理。",
            }
        _require_local_lifecycle(item, request)
        body: dict[str, Any] = {}
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 — 无 body / 非 JSON 按空处理
            body = {}
        try:
            tokens = body.get("tokens") or None
            if "grant_permissions" in body:
                registry.grant_permissions(cid, body["grant_permissions"])
            registry.require_permissions(cid)
            discovered: dict[str, Any] | None = None
            if item.get("model_provider"):
                if model_provider_plugins is None:
                    raise ValueError("模型适配器服务尚未就绪")
                discovered = await asyncio.to_thread(
                    model_provider_plugins.validate,
                    item,
                    tokens=tokens,
                )
            result = await asyncio.to_thread(
                registry.connect,
                cid,
                tokens=tokens,
                run_cli=bool(body.get("run_cli")),
            )
            if discovered is not None:
                configured = model_provider_plugins.configure(
                    item,
                    models=list(discovered.get("models") or []),
                    base_url=str(discovered.get("base_url") or "") or None,
                )
                registry.set_enabled(cid, True)
                result.update(
                    {
                        "model_provider": configured,
                        "message": f"已接入 {len(configured['models'])} 个模型。",
                    }
                )
            return result
        except RefreshCleanupRequiredError as exc:
            raise _refresh_cleanup_conflict(exc) from exc
        except PermissionError as exc:
            raise HTTPException(
                409,
                {
                    "code": "PERMISSION_REVIEW_REQUIRED",
                    "permissions": list(item.get("permissions") or []),
                    "message": "请确认该插件的全部签名权限后再连接。",
                },
            ) from exc
        except ValueError as exc:
            raise HTTPException(400 if item.get("model_provider") else 409, str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - keep partial setup out of the runtime
            if item.get("model_provider") and model_provider_plugins is not None:
                model_provider_plugins.remove(item)
                with contextlib.suppress(Exception):
                    registry.disconnect(cid)
            raise HTTPException(502, str(exc)) from exc

    @router.post("/api/capabilities/{cid}/disconnect")
    async def capability_disconnect(cid: str, request: Request) -> dict[str, Any]:
        item = await _get_lifecycle_item(cid, request)
        if item.get("is_codex_marketplace") is True:
            raise HTTPException(409, "Codex marketplace plugin connections are app-managed")
        _require_local_lifecycle(item, request)
        if item.get("model_provider") and model_provider_plugins is not None:
            model_provider_plugins.remove(item)
        try:
            return registry.disconnect(cid)
        except RefreshCleanupRequiredError as exc:
            raise _refresh_cleanup_conflict(exc) from exc

    @router.get(
        "/api/capabilities/{cid}/device-flow",
        dependencies=[Depends(_operator_dep)],
        response_model=DeviceFlowResponse,
        response_model_exclude_unset=True,
    )
    def capability_device_flow(cid: str) -> dict[str, Any]:
        item = _get(cid)
        if item.get("source") != "connector":
            raise HTTPException(409, "capability does not support connector device flow")
        try:
            return registry.device_flow_status(cid)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.delete(
        "/api/capabilities/{cid}/device-flow",
        dependencies=[Depends(_operator_dep)],
        response_model=DeviceFlowCancelResponse,
        response_model_exclude_unset=True,
    )
    def capability_device_flow_cancel(
        cid: str,
        expected_flow_id: str = Query(..., min_length=1, max_length=128),
    ) -> dict[str, Any]:
        item = _get(cid)
        if item.get("source") != "connector":
            raise HTTPException(409, "capability does not support connector device flow")
        try:
            return registry.cancel_device_flow(
                cid,
                expected_flow_id=expected_flow_id,
            )
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get(
        "/api/capabilities/{cid}/headers",
        dependencies=[Depends(_operator_dep)],
    )
    def capability_headers(cid: str) -> dict[str, Any]:
        _get(cid)
        try:
            resolved = registry.resolve_headers(cid)
        except PermissionError as exc:
            raise HTTPException(
                409,
                {
                    "code": "PERMISSION_REVIEW_REQUIRED",
                    "message": "插件凭据权限尚未确认。",
                },
            ) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        raw_headers = resolved.get("headers") if isinstance(resolved, dict) else None
        headers = raw_headers if isinstance(raw_headers, dict) else {}
        return {
            "configured": bool(headers),
            "header_names": sorted(str(name) for name in headers),
        }

    return router
