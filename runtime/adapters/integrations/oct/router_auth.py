# ruff: noqa: E402 — module-level imports below are intentionally late

from __future__ import annotations

import logging
import time
from typing import Any

try:
    from fastapi import APIRouter, HTTPException, Request, Response
    from pydantic import BaseModel, Field

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    APIRouter = None  # type: ignore[assignment, misc]
    BaseModel = object  # type: ignore[assignment, misc]
    Response = None  # type: ignore[assignment, misc]

from runtime.sensing._fastapi_guard import require_fastapi

from .client import OctClientError, mask_email, post_public
from .config import OctConfig
from .links import OctLink, OctLinkStore

logger = logging.getLogger(__name__)


def actor_from_email(email: str) -> str:
    """从邮箱派生稳定 actor_id。全小写,避免大小写不一致拆成两个身份。"""
    return "oct:" + (email or "").strip().lower()


if FASTAPI_AVAILABLE:

    class EmailSendRequest(BaseModel):
        email: str = Field(..., description="登录邮箱")

    class EmailLoginRequest(BaseModel):
        email: str
        code: str = Field(..., description="邮箱 6 位验证码")

    class EmailSendResponse(BaseModel):
        sent: bool
        ttl_seconds: int = 300
        dev_code: str | None = None  # 仅 mock 模式返回

    class EmailLoginResponse(BaseModel):
        success: bool
        actor_id: str
        access_token: str | None = None
        token_type: str = "Bearer"
        expires_in: int | None = None
        is_new_user: bool = False
        user: dict[str, Any] = Field(default_factory=dict)
        credits: dict[str, Any] = Field(default_factory=dict)


def create_auth_router(
    *,
    config: OctConfig,
    link_store: OctLinkStore,
    identity_store: Any = None,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
    http_client: Any = None,
) -> Any:
    require_fastapi(__name__)

    router = APIRouter(prefix="/api/auth/oct", tags=["oct", "auth"])
    base = config.base_url.rstrip("/")
    # agent 自有会话 JWT 的密钥:config 优先,其次 app 注入。留空则直接复用网关 JWT。
    effective_jwt_secret = config.jwt_secret or jwt_secret
    effective_jwt_issuer = config.jwt_issuer or jwt_issuer

    def _require_enabled() -> None:
        if not config.enabled:
            raise HTTPException(
                status_code=503,
                detail="oct 账号网关未启用 · config.oct.enabled=true",
            )

    def _check_email(email: str) -> str:
        e = (email or "").strip()
        if "@" not in e or e.startswith("@") or e.endswith("@"):
            raise HTTPException(status_code=400, detail="邮箱格式无效")
        return e

    @router.post("/email/send", response_model=EmailSendResponse)
    def email_send(body: EmailSendRequest) -> EmailSendResponse:
        _require_enabled()
        email_addr = _check_email(body.email)
        try:
            upstream = post_public(
                f"{base}/auth/email/send",
                {"email": email_addr},
                timeout=config.request_timeout_seconds,
                http_client=http_client,
            )
        except OctClientError as exc:
            raise HTTPException(status_code=502, detail=f"oct send code failed: {exc}") from exc
        return EmailSendResponse(
            sent=bool(upstream.get("ok", True)),
            ttl_seconds=int(upstream.get("ttlSeconds") or 300),
            dev_code=upstream.get("devCode") if config.mock_mode else None,
        )

    @router.post("/email/login", response_model=EmailLoginResponse)
    def email_login(
        body: EmailLoginRequest,
        request: Request,
        response: Response,
    ) -> EmailLoginResponse:
        _require_enabled()
        email_addr = _check_email(body.email)
        t0 = time.perf_counter()
        try:
            data = post_public(
                f"{base}/auth/email/login",
                {"email": email_addr, "code": body.code},
                timeout=config.request_timeout_seconds,
                http_client=http_client,
            )
        except OctClientError as exc:
            # 网关 4xx(验证码错/过期)透传为 401,其余 502
            status = 401 if (exc.status_code or 0) in (400, 401, 403) else 502
            raise HTTPException(status_code=status, detail=f"oct 登录失败: {exc}") from exc

        gateway_token = data.get("token")
        oct_user_id = data.get("userId")
        if not gateway_token or not oct_user_id:
            raise HTTPException(status_code=502, detail="oct 登录响应缺少 token/userId")
        email = str(data.get("email") or body.email)
        is_new = bool(data.get("isNewUser"))
        actor_id = actor_from_email(email)

        # ─── Identity upsert ─────────────────────────
        created = False
        try:
            if identity_store is not None:
                from runtime.safety.auth.identity import Identity

                existing = identity_store.get(actor_id) if hasattr(identity_store, "get") else None
                if existing is None:
                    identity = Identity(
                        actor_id=actor_id,
                        roles=("user", "oct"),
                        metadata={
                            "provider": "oct",
                            "email": email,
                            "oct_user_id": str(oct_user_id),
                            "created_at": time.time(),
                        },
                    )
                    try:
                        identity_store.add(identity)
                        created = True
                    except ValueError:  # noqa: BLE001 — duplicate silently skipped
                        pass
        except Exception as exc:  # noqa: BLE001
            logger.warning("oct identity upsert failed (non-fatal): %s", exc)

        # ─── 存网关 JWT(后续调网关用)──────────────
        try:
            link_store.put(
                OctLink(
                    echo_user_id=actor_id,
                    oct_user_id=str(oct_user_id),
                    oct_token=str(gateway_token),
                    email=email,
                    linked_at=time.time(),
                    last_synced_at=time.time(),
                ),
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("oct link_store.put failed: %s", exc, exc_info=True)
            raise HTTPException(status_code=500, detail=f"保存登录凭证失败: {exc}") from exc

        # ─── 会话凭证:签 agent 自有 JWT,或直接复用网关 JWT ──
        access_token: str | None = None
        expires_in: int | None = None
        if effective_jwt_secret:
            try:
                from runtime.safety.auth.identity import encode_jwt_hs256

                now = int(time.time())
                claims: dict[str, Any] = {
                    "sub": actor_id,
                    "iat": now,
                    "exp": now + config.jwt_expire_seconds,
                    "provider": "oct",
                    "email": email,
                }
                if effective_jwt_issuer:
                    claims["iss"] = effective_jwt_issuer
                if jwt_audience:
                    claims["aud"] = jwt_audience
                access_token = encode_jwt_hs256(claims, secret=effective_jwt_secret)
                expires_in = config.jwt_expire_seconds
            except Exception as exc:  # noqa: BLE001
                logger.error("oct JWT signing failed: %s", exc, exc_info=True)
                raise HTTPException(status_code=500, detail=f"JWT 签发失败: {exc}") from exc
        else:
            # 未配 agent jwt_secret。schema 在 oct.enabled 时已强制必填,这里仅防御性兜底:
            # 绝不返回网关原始 JWT —— agent 不持网关密钥、全局鉴权门验不过会把用户锁死。
            logger.error("oct.enabled 但缺 jwt_secret · 不签发会话 token(请配置 oct.jwt_secret)")
            access_token = None
            expires_in = None

        if access_token and expires_in:
            from runtime.safety.auth.principal import set_session_cookie

            set_session_cookie(
                response,
                request,
                access_token,
                max_age=expires_in,
            )

        logger.info(
            "oct email_login ok · total_ms=%d created=%s new=%s email=%s",
            int((time.perf_counter() - t0) * 1000),
            created,
            is_new,
            mask_email(email),
        )
        _ = request  # audit hook placeholder
        return EmailLoginResponse(
            success=True,
            actor_id=actor_id,
            access_token=access_token,
            expires_in=expires_in,
            is_new_user=is_new,
            user={"actor_id": actor_id, "email": email, "provider": "oct", "created": created},
        )

    return router
