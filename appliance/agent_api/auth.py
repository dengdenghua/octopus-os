"""Agent authentication compatibility surface consumed by Echo OS."""

from runtime.adapters.integrations.local_auth import create_local_auth_router
from runtime.adapters.integrations.local_auth.config import (
    LocalAuthConfig,
    hash_password,
    verify_password,
)
from runtime.safety.auth.identity import JWTError, verify_jwt_hs256
from runtime.safety.auth.principal import (
    LEGACY_SESSION_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    clear_session_cookie,
)

__all__ = [
    "JWTError",
    "LocalAuthConfig",
    "SESSION_COOKIE_NAME",
    "LEGACY_SESSION_COOKIE_NAME",
    "clear_session_cookie",
    "create_local_auth_router",
    "hash_password",
    "verify_jwt_hs256",
    "verify_password",
]
