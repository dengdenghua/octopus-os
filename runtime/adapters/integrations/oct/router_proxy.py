"""oct LLM 代理路由 · /api/oct/openai/v1/*。

把 OpenAI 兼容的调用透传到 oct 网关 /v1/*(用 OctLink 里的网关 JWT),网关侧计费扣积分。
agent 自身的模型调度走 OctModelRouter(model_router/oct_router.py);本路由供前端/外部直连。
"""

from __future__ import annotations

import logging
from typing import Any

try:
    import httpx  # type: ignore[import-untyped]

    HTTPX_AVAILABLE = True
except ImportError:  # pragma: no cover
    HTTPX_AVAILABLE = False
    httpx = None  # type: ignore[assignment]

try:
    from fastapi import APIRouter, HTTPException, Request
    from fastapi.responses import JSONResponse, StreamingResponse

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    APIRouter = None  # type: ignore[assignment, misc]

from runtime.sensing._fastapi_guard import require_fastapi

from .client import OctClientError, get_public, is_dead_token
from .config import OctConfig
from .links import OctLinkStore

logger = logging.getLogger(__name__)


def create_proxy_router(
    *,
    config: OctConfig,
    link_store: OctLinkStore,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
    http_client: Any = None,
) -> Any:
    require_fastapi(__name__)

    router = APIRouter(prefix="/api/oct/openai/v1", tags=["oct", "llm"])
    base = config.base_url.rstrip("/")

    def _require_enabled() -> None:
        if not config.enabled:
            raise HTTPException(
                status_code=503, detail="oct 账号网关未启用 · config.oct.enabled=true"
            )

    def _actor(request: Request) -> str:
        from runtime.adapters.web_auth import _resolve_actor

        actor = _resolve_actor(
            request,
            identity_store,
            require_auth,
            jwt_secret=config.jwt_secret or jwt_secret,
            jwt_issuer=config.jwt_issuer or jwt_issuer,
            jwt_audience=jwt_audience,
        )
        if not actor:
            raise HTTPException(401, "authentication required")
        return actor

    @router.get("/models")
    def models() -> dict[str, Any]:
        """公开模型列表(网关 GET /v1/models,无需鉴权)。"""
        _require_enabled()
        try:
            return get_public(
                f"{base}/v1/models",
                timeout=config.request_timeout_seconds,
                http_client=http_client,
            )
        except OctClientError as exc:
            raise HTTPException(502, f"oct gateway: {exc}") from exc

    @router.post("/chat/completions")
    async def chat_completions(request: Request) -> Any:
        """透传到网关 /v1/chat/completions(网关计费)。支持流式 SSE。"""
        _require_enabled()
        actor = _actor(request)
        link = link_store.get(actor)
        if link is None:
            raise HTTPException(404, "no oct account linked · POST /api/auth/oct/email/login first")
        body = await request.json()
        stream = bool(body.get("stream"))
        url = f"{base}/v1/chat/completions"
        headers = {"Authorization": f"Bearer {link.oct_token}", "Content-Type": "application/json"}
        client = http_client or (httpx if HTTPX_AVAILABLE else None)
        if client is None:
            raise HTTPException(500, "httpx 未安装")

        if not stream:
            try:
                r = client.post(url, json=body, headers=headers, timeout=config.llm_timeout_seconds)
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(502, f"oct gateway unreachable: {exc}") from exc
            status = getattr(r, "status_code", 0)
            if is_dead_token(status):
                link_store.mark_token_invalid(actor, "TOKEN_EXPIRED")
                raise HTTPException(401, "oct 登录已过期 · 请重新登录")
            return JSONResponse(status_code=status, content=r.json())

        def _stream() -> Any:
            with client.stream(
                "POST", url, json=body, headers=headers, timeout=config.llm_timeout_seconds
            ) as r:
                if is_dead_token(getattr(r, "status_code", 0)):
                    link_store.mark_token_invalid(actor, "TOKEN_EXPIRED")
                    yield b'data: {"error":"oct \\u767b\\u5f55\\u5df2\\u8fc7\\u671f\\uff0c\\u8bf7\\u91cd\\u65b0\\u767b\\u5f55"}\n\n'
                    return
                for chunk in r.iter_bytes():
                    if chunk:
                        yield chunk

        return StreamingResponse(_stream(), media_type="text/event-stream")

    return router
