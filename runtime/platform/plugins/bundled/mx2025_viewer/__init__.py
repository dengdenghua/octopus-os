"""MX技术小筑查看器插件 — 本地默认启用、认证部署 fail-closed 的同源代理。"""

from __future__ import annotations

import html
import logging
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from runtime.execution.suckers.registry import Skill
from runtime.platform.plugins.plugin_base import ModulePlugin

from .cloud_sync import MXCloudSyncConnector
from .groups import ConversationGroupStore
from .proxy import secure_upstream_origin

_logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://mx2025.hhhuu.com"
_NOTICE_CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; "
    "form-action 'none'; frame-ancestors 'self'"
)
_VIEWER_CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; "
    "frame-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
)


def _isolated_bridge_origin(value: str) -> str | None:
    """Accept HTTPS bridges and HTTP only on an explicit loopback host."""
    raw = str(value or "").strip()
    if not raw or "\\" in raw or any(ord(ch) < 33 for ch in raw):
        return None
    try:
        parsed = urlsplit(raw)
        hostname = (parsed.hostname or "").rstrip(".").lower()
        port = parsed.port
    except (TypeError, ValueError):
        return None
    loopback = hostname in {"127.0.0.1", "localhost", "::1"}
    if (
        not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or port == 0
        or (parsed.scheme != "https" and not (parsed.scheme == "http" and loopback))
    ):
        return None
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def _isolated_bridge_page(bridge_url: str) -> str:
    safe_url = html.escape(bridge_url, quote=True)
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>萌侠消息中心</title><style>
html,body,iframe{{width:100%;height:100%;margin:0;border:0;background:#f5f5f7}}
iframe{{display:block}}
</style></head><body><iframe src="{safe_url}/viewer" title="萌侠云端同源代理"></iframe></body></html>"""


def _isolated_bridge_headers(bridge_url: str) -> dict[str, str]:
    parsed = urlsplit(bridge_url)
    bridge_origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    return {
        "Cache-Control": "no-store",
        "Content-Security-Policy": (
            "default-src 'none'; style-src 'unsafe-inline'; "
            f"frame-src {bridge_origin}; base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
        ),
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "SAMEORIGIN",
    }


class _GroupPayload(BaseModel):
    name: str | None = None
    group_id: str | None = None


class _SyncCapturePayload(BaseModel):
    messages: list[dict[str, Any]]


class _SyncConfigurePayload(BaseModel):
    cloud_url: str
    pairing_code: str
    device_name: str = "Echo Desktop"


def _explicitly_enabled(
    config: dict[str, Any],
    key: str,
    *,
    default: bool = False,
) -> bool:
    """Accept only boolean ``true`` while allowing a strict boolean default."""
    return config.get(key, default) is True


def _local_env_enabled(name: str) -> bool:
    """Allow an operator-owned local service definition to opt in explicitly."""
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _configured_enabled(
    config: dict[str, Any],
    key: str,
    *,
    env_name: str,
) -> bool:
    """Prefer an explicit strict config value, then env, then default on."""
    if key in config:
        return _explicitly_enabled(config, key)
    if env_name in os.environ:
        return _local_env_enabled(env_name)
    return _explicitly_enabled(config, key, default=True)


def _page_headers(*, viewer: bool = False) -> dict[str, str]:
    return {
        "Cache-Control": "no-store",
        "Content-Security-Policy": _VIEWER_CSP if viewer else _NOTICE_CSP,
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "SAMEORIGIN",
    }


def _notice_page(*, authenticated_host: bool, base_url: str) -> str:
    if authenticated_host:
        title = "MX技术小筑在当前部署中不可用"
        detail = (
            "当前实例已开启身份认证。为避免第三方脚本获得应用同源权限，"
            "同源代理及其网络路由均已安全关闭。"
        )
        action = "请直接访问可信上游，或仅在隔离的单用户本地实例中启用此功能。"
    else:
        title = "MX技术小筑同源代理未开启"
        detail = (
            "本地单用户实例默认启用该代理。当前未挂载，通常是配置被显式关闭"
            "或上游不是有效的 HTTPS 地址。"
        )
        origin = secure_upstream_origin(base_url) or DEFAULT_BASE_URL
        safe_origin = html.escape(origin, quote=True)
        action = (
            "第三方脚本会使用本应用的 origin 权限；生产或多用户部署不得开启。"
            f'也可以<a href="{safe_origin}" target="_blank" rel="noreferrer noopener">'
            "直接打开上游站点</a>。"
        )
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>
body{{font-family:system-ui,sans-serif;margin:0;background:#10131f;color:#e6e9f0}}
.card{{max-width:680px;margin:12vh auto;padding:28px;border:1px solid #39415f;
border-radius:14px;background:#1a1d33;line-height:1.8}}h1{{font-size:22px;margin-top:0}}
p{{color:#aeb7ca}}code{{color:#f0b90b}}a{{color:#f0b90b}}
</style></head><body><main class="card"><h1>{title}</h1>
<p>{detail}</p><p>{action}</p></main></body></html>"""


class MX2025ViewerPlugin(ModulePlugin):
    """MX技术小筑查看器；本地默认启用，认证部署仍 fail closed。"""

    name = "mx2025_viewer"
    display_name = "MX技术小筑"
    version = "0.5.0"
    description = "MX技术小筑网站查看器 — 本地对话分组、自动收起长期不活跃对话"

    def __init__(self) -> None:
        super().__init__()
        self.base_url = DEFAULT_BASE_URL
        self.proxy_origin = False
        self.allow_same_origin_third_party_scripts = False
        self.isolated_bridge_url = ""
        self.inactive_days = 7
        self._authenticated_host = False
        self.groups = ConversationGroupStore()
        self.cloud_sync = MXCloudSyncConnector("~/.echo/data/mx2025_viewer")

    def on_load(self, ctx: Any) -> None:
        cfg = dict(ctx.config or {})
        self.base_url = str(cfg.get("base_url") or DEFAULT_BASE_URL)
        try:
            inactive_days = int(cfg.get("inactive_days", 7))
        except (TypeError, ValueError):
            inactive_days = 7
        self.inactive_days = min(max(inactive_days, 1), 365)
        self.proxy_origin = False
        self.allow_same_origin_third_party_scripts = _configured_enabled(
            cfg,
            "allow_same_origin_third_party_scripts",
            env_name="ECHO_MX2025_ALLOW_SAME_ORIGIN_SCRIPTS",
        )
        requested_bridge = str(
            cfg.get("isolated_bridge_url") or os.environ.get("ECHO_MX2025_ISOLATED_BRIDGE_URL", "")
        ).strip()
        self.isolated_bridge_url = _isolated_bridge_origin(requested_bridge) or ""
        if requested_bridge and not self.isolated_bridge_url:
            _logger.warning("mx2025_viewer isolated bridge ignored (invalid URL)")
        app = getattr(ctx, "fastapi_app", None)
        self._authenticated_host = bool(
            app is not None and getattr(getattr(app, "state", None), "echo_require_auth", False)
        )

        proxy_requested = _configured_enabled(
            cfg,
            "proxy_origin",
            env_name="ECHO_MX2025_PROXY_ORIGIN",
        )
        secure_upstream = bool(secure_upstream_origin(self.base_url))
        self.proxy_origin = (
            proxy_requested
            and self.allow_same_origin_third_party_scripts
            and secure_upstream
            and not self._authenticated_host
        )
        if proxy_requested and not self.proxy_origin:
            reasons = []
            if not self.allow_same_origin_third_party_scripts:
                reasons.append("missing independent risk acceptance")
            if not secure_upstream:
                reasons.append("upstream is not valid HTTPS")
            if self._authenticated_host:
                reasons.append("host authentication is enabled")
            _logger.warning("mx2025_viewer proxy disabled (%s)", "; ".join(reasons))
        super().on_load(ctx)

    def register_skills(self) -> None:
        if self.ctx is None:
            return
        self.ctx.register_skill(
            Skill(
                name="mx2025.recent_messages",
                description=(
                    "读取本机萌侠连接器已采集的最新消息。参数:limit 可选(1-500,默认100),"
                    "query 可选(按老师名或正文搜索),room_id 可选(限定对话)。返回内容含老师/"
                    "对话标题、消息正文、发布时间、消息类型和是否已同步云端。适用于分析老师"
                    "观点、调研纪要、小作文、竞价观点和最近消息。"
                ),
                summary="读取并搜索萌侠本地消息",
                affinity=["stock", "research", "mx2025", "messages"],
                cost_profile="low",
                trusted_source="plugin://mx2025_viewer",
                handler=self._recent_messages_skill,
            )
        )
        self.ctx.register_skill(
            Skill(
                name="mx2025.sync_status",
                description=(
                    "查看萌侠本地连接器是否已连接云端、待同步消息数量和最后同步状态。"
                    "无参数，不返回设备私钥、配对码或访问令牌。"
                ),
                summary="查看萌侠云端同步状态",
                affinity=["mx2025", "sync", "status"],
                cost_profile="low",
                trusted_source="plugin://mx2025_viewer",
                handler=lambda **_kwargs: {"ok": True, **self.cloud_sync.status()},
            )
        )

    def _recent_messages_skill(self, **kwargs: Any) -> dict[str, Any]:
        try:
            limit = int(kwargs.get("limit") or 100)
        except (TypeError, ValueError):
            limit = 100
        messages = self.cloud_sync.recent_messages(
            limit=limit,
            query=str(kwargs.get("query") or ""),
            room_id=str(kwargs.get("room_id") or ""),
        )
        return {"ok": True, "count": len(messages), "messages": messages}

    def register_routes(self) -> None:
        if self.ctx is None or self.ctx.fastapi_app is None:
            return

        app = self.ctx.fastapi_app
        router = APIRouter(prefix=f"/api/plugins/{self.name}", tags=[self.name])

        # Authenticated hosts never mount third-party scripts on the host
        # origin. They may embed a separately isolated loopback/HTTPS bridge,
        # which has no access to Echo account or control-plane routes.
        if self._authenticated_host:

            @router.get("/page", response_class=HTMLResponse)
            def authenticated_notice() -> HTMLResponse:
                if self.isolated_bridge_url:
                    return HTMLResponse(
                        _isolated_bridge_page(self.isolated_bridge_url),
                        headers=_isolated_bridge_headers(self.isolated_bridge_url),
                    )
                return HTMLResponse(
                    _notice_page(authenticated_host=True, base_url=self.base_url),
                    headers=_page_headers(),
                )

            app.include_router(router)
            return

        @router.get("/groups")
        def list_groups() -> dict[str, Any]:
            return self.groups.snapshot()

        @router.post("/groups")
        def create_group(payload: _GroupPayload) -> dict[str, Any]:
            try:
                group = self.groups.create(payload.name)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return {"ok": True, "group": group}

        @router.patch("/groups/{group_id}")
        def rename_group(group_id: str, payload: _GroupPayload) -> dict[str, Any]:
            try:
                group = self.groups.rename(group_id, payload.name)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            if group is None:
                raise HTTPException(status_code=404, detail="分组不存在")
            return {"ok": True, "group": group}

        @router.delete("/groups/{group_id}")
        def delete_group(group_id: str) -> dict[str, Any]:
            if not self.groups.delete(group_id):
                raise HTTPException(status_code=404, detail="分组不存在")
            return {"ok": True}

        @router.put("/group-assignments/{room_id}")
        def assign_group(room_id: str, payload: _GroupPayload) -> dict[str, Any]:
            try:
                assignment = self.groups.assign(room_id, payload.group_id)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc
            return {"ok": True, "assignment": assignment}

        @router.get("/sync/status")
        def sync_status() -> dict[str, Any]:
            return self.cloud_sync.status()

        @router.post("/sync/configure")
        def sync_configure(payload: _SyncConfigurePayload) -> dict[str, Any]:
            try:
                return self.cloud_sync.configure(
                    cloud_url=payload.cloud_url,
                    pairing_code=payload.pairing_code,
                    device_name=payload.device_name,
                )
            except Exception as exc:  # noqa: BLE001 — return a bounded setup error
                raise HTTPException(status_code=400, detail=str(exc)[:300]) from exc

        @router.delete("/sync/config")
        def sync_disconnect() -> dict[str, Any]:
            self.cloud_sync.disconnect()
            return {"ok": True}

        @router.post("/sync/capture")
        def sync_capture(
            payload: _SyncCapturePayload,
            background_tasks: BackgroundTasks,
        ) -> dict[str, Any]:
            if len(payload.messages) > 100:
                raise HTTPException(status_code=400, detail="at most 100 messages per capture")
            accepted: list[dict[str, Any]] = []
            for item in payload.messages:
                source = str(item.get("source") or "")[:40]
                room_id = str(item.get("source_room_id") or "")[:128]
                message_id = str(item.get("source_message_id") or "")[:160]
                content = str(item.get("content") or "")[:50_000]
                if not source or not room_id or not message_id or not content:
                    continue
                accepted.append(
                    {
                        "source": source,
                        "source_room_id": room_id,
                        "source_message_id": message_id,
                        "title": str(item.get("title") or "")[:240],
                        "content": content,
                        "published_at": str(item.get("published_at") or "")[:64] or None,
                        "payload": item.get("payload")
                        if isinstance(item.get("payload"), dict)
                        else {},
                    }
                )
            result = self.cloud_sync.enqueue(accepted)
            if result["queued"]:
                background_tasks.add_task(self.cloud_sync.flush)
            return {"ok": True, "accepted": len(accepted), **result}

        @router.post("/sync/flush")
        def sync_flush() -> dict[str, Any]:
            return self.cloud_sync.flush()

        proxy_mounted = False
        if self.proxy_origin:
            from .proxy import register_origin_proxy

            proxy_mounted = register_origin_proxy(router, base_url=self.base_url)
            self.proxy_origin = proxy_mounted

        @router.get("/page", response_class=HTMLResponse)
        def viewer_page() -> HTMLResponse:
            if not proxy_mounted:
                return HTMLResponse(
                    _notice_page(authenticated_host=False, base_url=self.base_url),
                    headers=_page_headers(),
                )
            page_path = Path(self.ctx.plugin_dir) / "page" / "index.html"
            try:
                content = page_path.read_text(encoding="utf-8")
            except OSError as exc:
                _logger.warning("mx2025_viewer page unavailable (%s)", type(exc).__name__)
                return HTMLResponse(
                    _notice_page(authenticated_host=False, base_url=self.base_url),
                    status_code=503,
                    headers=_page_headers(),
                )
            replacements = {
                "__MX_INACTIVE_DAYS__": str(self.inactive_days),
                "__MX_GROUP_API__": f"/api/plugins/{self.name}",
                "__MX_VIEWER_MODE__": "local",
                "__MX_OPEN_URL__": "origin/?echo_proxy=7#/",
                "__MX_PROXY_PATH__": f"/api/plugins/{self.name}/origin/",
                "__MX_FRAME_SRC__": "origin/?echo_proxy=7#/",
            }
            for marker, value in replacements.items():
                content = content.replace(marker, value)
            return HTMLResponse(content, headers=_page_headers(viewer=True))

        app.include_router(router)


__all__ = ["MX2025ViewerPlugin"]
