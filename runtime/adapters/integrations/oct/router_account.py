"""oct 账号管理路由 · /api/account/oct/*。

用存在 OctLink 里的网关 JWT 调 oct 网关(echo.aurest.ai)的 /account 与 /billing 端点,
做积分余额/会员/用量/商品/订单/每日签到。网关 401 → 标记 token 失效。
"""

from __future__ import annotations

import logging
import time
from typing import Any

try:
    from fastapi import APIRouter, HTTPException, Request
    from pydantic import BaseModel, Field

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    APIRouter = None  # type: ignore[assignment, misc]
    BaseModel = object  # type: ignore[assignment, misc]

from runtime.sensing._fastapi_guard import require_fastapi

from .client import OctClientError, get_auth, is_dead_token, post_auth
from .config import OctConfig
from .links import OctLink, OctLinkStore

logger = logging.getLogger(__name__)


if FASTAPI_AVAILABLE:

    class OctLinkResponse(BaseModel):
        oct_user_id: str
        email: str | None = None
        linked_at: float
        last_synced_at: float | None = None
        credits: dict[str, Any] = Field(default_factory=dict)
        token_invalid: bool = False
        token_invalid_reason: str | None = None

    class OrderRequest(BaseModel):
        goods_id: str = Field(..., description="商品 id(网关 goodsId)")
        currency: str = Field(default="CNY", description="CNY | USD")

    class EstimateRequest(BaseModel):
        model: str
        messages: list[dict[str, Any]] = Field(default_factory=list)
        max_tokens: int = 2048


def create_account_router(
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

    router = APIRouter(prefix="/api/account/oct", tags=["oct"])
    base = config.base_url.rstrip("/")
    timeout = config.request_timeout_seconds

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

    def _link_or_404(actor: str) -> OctLink:
        link = link_store.get(actor)
        if link is None:
            raise HTTPException(
                status_code=404,
                detail="no oct account linked · POST /api/auth/oct/email/login first",
            )
        return link

    def _link_to_response(link: OctLink) -> OctLinkResponse:
        return OctLinkResponse(
            oct_user_id=link.oct_user_id,
            email=link.email,
            linked_at=link.linked_at,
            last_synced_at=link.last_synced_at,
            credits=link.credits_snapshot or {},
            token_invalid=bool(link.token_invalid),
            token_invalid_reason=link.token_invalid_reason,
        )

    def _get(actor: str, link: OctLink, path: str, **params: Any) -> dict[str, Any]:
        """带 dead-token 处理的网关 GET。"""
        try:
            return get_auth(
                f"{base}{path}",
                token=link.oct_token,
                timeout=timeout,
                params=params or None,
                http_client=http_client,
            )
        except OctClientError as exc:
            if is_dead_token(exc.status_code):
                link_store.mark_token_invalid(actor, "TOKEN_EXPIRED")
                raise HTTPException(401, "oct 登录已过期 · 请重新登录") from exc
            raise HTTPException(502, f"oct gateway: {exc}") from exc

    def _post(actor: str, link: OctLink, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            return post_auth(
                f"{base}{path}",
                body,
                token=link.oct_token,
                timeout=timeout,
                http_client=http_client,
            )
        except OctClientError as exc:
            if is_dead_token(exc.status_code):
                link_store.mark_token_invalid(actor, "TOKEN_EXPIRED")
                raise HTTPException(401, "oct 登录已过期 · 请重新登录") from exc
            raise HTTPException(502, f"oct gateway: {exc}") from exc

    # ─── 绑定展示 / 刷新 / 解绑 ─────────────────────────

    @router.get("", response_model=OctLinkResponse | None)
    def show(request: Request) -> OctLinkResponse | None:
        _require_enabled()
        actor = _actor(request)
        link = link_store.get(actor)
        # "Not linked" is an ordinary account state, not a missing API
        # resource. Returning JSON null keeps the workspace boot path free of
        # expected 404 noise; mutation and usage routes still fail closed via
        # _link_or_404 when a binding is actually required.
        if link is None:
            return None
        # snapshot 为空时顺手刷一次
        if not link.credits_snapshot:
            return refresh(request)
        return _link_to_response(link)

    @router.post("/refresh", response_model=OctLinkResponse)
    def refresh(request: Request) -> OctLinkResponse:
        _require_enabled()
        actor = _actor(request)
        link = _link_or_404(actor)
        balance = _get(actor, link, "/account/balance")
        membership = _get(actor, link, "/account/membership")
        snapshot: dict[str, Any] = {**(balance or {}), "membership": membership or {}}
        updated = link_store.update_credits(actor, snapshot, now=time.time())
        return _link_to_response(updated or link)

    @router.delete("/link")
    def unlink(request: Request) -> dict[str, bool]:
        _require_enabled()
        actor = _actor(request)
        return {"unlinked": link_store.delete(actor)}

    # ─── 会员 / 用量 / 签到 ─────────────────────────

    @router.get("/membership")
    def membership(request: Request) -> dict[str, Any]:
        _require_enabled()
        actor = _actor(request)
        link = _link_or_404(actor)
        return _get(actor, link, "/account/membership")

    @router.get("/usage")
    def usage(request: Request, page: int = 1, page_size: int = 50) -> dict[str, Any]:
        _require_enabled()
        actor = _actor(request)
        return _get(actor, _link_or_404(actor), "/account/usage", page=page, pageSize=page_size)

    @router.post("/daily-claim")
    def daily_claim(request: Request) -> dict[str, Any]:
        _require_enabled()
        actor = _actor(request)
        link = _link_or_404(actor)
        result = _post(actor, link, "/account/daily-claim", {})
        try:
            refresh(request)
        except Exception as exc:  # noqa: BLE001
            logger.debug("post-claim refresh failed: %s", exc)
        return result

    # ─── 商品 / 预估 / 订单 ─────────────────────────

    @router.get("/goods")
    def goods(request: Request) -> dict[str, Any]:
        _require_enabled()
        actor = _actor(request)
        return _get(actor, _link_or_404(actor), "/billing/goods")

    @router.post("/estimate")
    def estimate(body: EstimateRequest, request: Request) -> dict[str, Any]:
        _require_enabled()
        actor = _actor(request)
        link = _link_or_404(actor)
        return _post(
            actor,
            link,
            "/billing/estimate",
            {
                "model": body.model,
                "messages": body.messages,
                "maxTokens": body.max_tokens,
            },
        )

    @router.post("/orders")
    def create_order(body: OrderRequest, request: Request) -> dict[str, Any]:
        _require_enabled()
        actor = _actor(request)
        link = _link_or_404(actor)
        return _post(
            actor,
            link,
            "/billing/orders",
            {
                "goodsId": body.goods_id,
                "currency": body.currency,
            },
        )

    @router.get("/orders/{order_no}")
    def find_order(order_no: str, request: Request) -> dict[str, Any]:
        _require_enabled()
        actor = _actor(request)
        link = _link_or_404(actor)
        result = _get(actor, link, f"/billing/orders/{order_no}")
        # 支付成功顺手刷余额
        if isinstance(result, dict) and result.get("status") == "PAID":
            try:
                refresh(request)
            except Exception as exc:  # noqa: BLE001
                logger.debug("post-payment refresh failed: %s", exc)
        return result

    return router
