"""Appliance 接口共用的 JWT Bearer 鉴权。

复用 runtime/safety/auth 的 HS256 校验;jwt_secret 为 None(未启用认证,
本地开发)时一律放行。app_registry 与 files 路由共用此实现。
"""

from __future__ import annotations

from fastapi import HTTPException, Request


def bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    return None


def is_authenticated(request: Request, jwt_secret: str | None) -> bool:
    if not jwt_secret:
        return True  # 未启用认证(本地开发)
    token = bearer_token(request)
    if not token:
        return False
    from runtime.safety.auth.identity import JWTError, verify_jwt_hs256

    try:
        verify_jwt_hs256(token, secret=jwt_secret)
    except JWTError:
        return False
    return True


def make_auth_dependency(jwt_secret: str | None):
    """返回一个 FastAPI 依赖:未认证则抛 401。"""

    def _require_auth(request: Request) -> None:
        if not is_authenticated(request, jwt_secret):
            raise HTTPException(status_code=401, detail="authentication required")

    return _require_auth
