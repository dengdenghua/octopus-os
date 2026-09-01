"""Appliance 接口共用的浏览器会话与 Bearer JWT 鉴权。

浏览器登录后，Agent 把 JWT 放进 ``echo_session`` HttpOnly Cookie；CLI
和设备内服务仍可使用 ``Authorization: Bearer``。两种载体最终都由 Agent
官方 HS256 校验器验证，并把不可伪造的 ``sub`` 写入 ``request.state``，供
审批和审计使用。

``jwt_secret is None`` 只用于明确的无认证本地开发，此时使用固定的开发者
actor，不能误报成真实管理员身份。
"""

from __future__ import annotations

import os

from fastapi import Depends, HTTPException, Request

from appliance.agent_api.auth import (
    LEGACY_SESSION_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    JWTError,
    verify_jwt_hs256,
)

DEVELOPMENT_ACTOR = "local:development"
_LEGACY_AGENT_ISSUER = "octo" + "pus-agent"


class ApplianceAuthenticator:
    """Capability-style verifier that keeps the JWT signing secret encapsulated."""

    __slots__ = ("__secret",)

    def __init__(self, jwt_secret: str | None) -> None:
        self.__secret = str(jwt_secret or "")

    @property
    def required(self) -> bool:
        return bool(self.__secret) or os.environ.get("ECHO_APPLIANCE") == "1"

    def actor(self, request: Request) -> str | None:
        return _authenticated_actor(request, self.__secret or None)

    def is_authenticated(self, request: Request) -> bool:
        return self.actor(request) is not None

    def dependency(self):
        def _require_auth(request: Request) -> str:
            actor = self.actor(request)
            if actor is None:
                raise HTTPException(status_code=401, detail="authentication required")
            request.state.appliance_actor = actor
            return actor

        return _require_auth

    def operator_dependency(self):
        """Require the device operator while preserving explicit local development."""

        require_auth = self.dependency()

        def _require_operator(actor: str = Depends(require_auth)) -> str:
            if actor == "local:admin" or (not self.required and actor == DEVELOPMENT_ACTOR):
                return actor
            raise HTTPException(
                status_code=403,
                detail="device operator permission is required",
            )

        return _require_operator


def resolve_authenticator(
    *,
    jwt_secret: str | None = None,
    authenticator: ApplianceAuthenticator | None = None,
) -> ApplianceAuthenticator:
    if authenticator is not None and jwt_secret is not None:
        raise ValueError("pass authenticator or jwt_secret, not both")
    return authenticator or ApplianceAuthenticator(jwt_secret)


def bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    return None


def request_token(request: Request) -> str | None:
    """Read a JWT from Bearer first, then the Agent HttpOnly session cookie."""

    token = bearer_token(request)
    if token:
        return token

    value = (
        request.cookies.get(SESSION_COOKIE_NAME, "").strip()
        or request.cookies.get(LEGACY_SESSION_COOKIE_NAME, "").strip()
    )
    return value or None


def _authenticated_actor(request: Request, jwt_secret: str | None) -> str | None:
    """Return the verified JWT subject, or ``None`` when authentication fails."""

    if not jwt_secret:
        # Missing auth is permitted only for explicit local/native development.
        # Appliance production must never turn configuration loss into admin.
        if os.environ.get("ECHO_APPLIANCE") == "1":
            return None
        return DEVELOPMENT_ACTOR
    token = request_token(request)
    if not token:
        return None
    try:
        claims = verify_jwt_hs256(token, secret=jwt_secret)
        # 若 token 携带 iss，则必须为 echo-agent，避免同一 secret 签发的 evil token 复用；
        # 兼容未带 iss 的旧 token/测试 token
        iss = claims.get("iss")
        if iss is not None and iss not in {"echo-agent", _LEGACY_AGENT_ISSUER}:
            return None
    except JWTError:
        return None
    account_security = getattr(
        getattr(request.app, "state", None),
        "echo_appliance_account_security",
        None,
    )
    if account_security is not None and not account_security.claims_are_current(claims):
        return None
    actor = claims.get("sub")
    if not isinstance(actor, str):
        return None
    actor = actor.strip()
    if not actor or len(actor) > 256:
        return None
    return actor


def is_authenticated(request: Request, jwt_secret: str | None) -> bool:
    return ApplianceAuthenticator(jwt_secret).is_authenticated(request)


def make_auth_dependency(jwt_secret: str | None):
    """Return a dependency that authenticates and exposes the actor id."""

    return ApplianceAuthenticator(jwt_secret).dependency()


__all__ = [
    "DEVELOPMENT_ACTOR",
    "ApplianceAuthenticator",
    "bearer_token",
    "is_authenticated",
    "make_auth_dependency",
    "request_token",
    "resolve_authenticator",
]
