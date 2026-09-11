from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .config import LocalAuthConfig
    from .router import create_local_auth_router

__all__ = ["LocalAuthConfig", "create_local_auth_router"]


def __getattr__(name: str) -> Any:
    if name == "LocalAuthConfig":
        from .config import LocalAuthConfig

        return LocalAuthConfig
    if name == "create_local_auth_router":
        from .router import create_local_auth_router

        return create_local_auth_router
    raise AttributeError(name)
