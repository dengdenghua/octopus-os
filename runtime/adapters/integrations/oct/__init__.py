"""oct 账号网关集成 · 把母本账号体系统一到 echo 自己的网关(echo.aurest.ai)。

提供邮箱验证码登录、积分/会员/用量/商品/订单与 LLM 中转计费。
三个 router 由 create_oct_routers 装配,挂载在 runtime/platform/ui/app.py(gated config.oct.enabled)。
"""

from .client import OctClientError
from .config import OctConfig, load_oct_config_from_dict
from .links import OctLink, OctLinkStore
from .router_account import create_account_router
from .router_auth import create_auth_router
from .router_proxy import create_proxy_router


def create_oct_routers(
    *,
    config: OctConfig,
    link_store: OctLinkStore,
    identity_store: object | None = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
) -> tuple[object, object, object]:
    auth_router = create_auth_router(
        config=config,
        link_store=link_store,
        identity_store=identity_store,
        jwt_secret=jwt_secret,
        jwt_issuer=jwt_issuer,
        jwt_audience=jwt_audience,
    )
    account_router = create_account_router(
        config=config,
        link_store=link_store,
        identity_store=identity_store,
        require_auth=require_auth,
        jwt_secret=jwt_secret,
        jwt_issuer=jwt_issuer,
        jwt_audience=jwt_audience,
    )
    proxy_router = create_proxy_router(
        config=config,
        link_store=link_store,
        identity_store=identity_store,
        require_auth=require_auth,
        jwt_secret=jwt_secret,
        jwt_issuer=jwt_issuer,
        jwt_audience=jwt_audience,
    )
    return auth_router, account_router, proxy_router


__all__ = [
    "OctClientError",
    "OctConfig",
    "OctLink",
    "OctLinkStore",
    "create_account_router",
    "create_auth_router",
    "create_oct_routers",
    "create_proxy_router",
    "load_oct_config_from_dict",
]
