"""服务商直连 OAuth App 配置 —— 为不暴露 ``.well-known`` 元数据、但支持
OAuth App 的服务商提供网页登录(WorkBuddy 的 ``server-side`` 连接器就是靠
它平台自己注册的 OAuth App 做到的)。

我们本地做同样的事:用户在自己的服务商账号下创建一个 OAuth App(client_id +
client_secret),凭据加密存到本地,连接时走 服务商授权页 → 回调 → 换 token →
注入 ``Authorization: Bearer``。endpoints 都是公开固定值,无需 ``.well-known``
探测;token 交换走标准 ``authorization_code`` + ``client_secret``(GitHub 等
不支持 PKCE)。

新增服务商只需在 ``PROVIDERS`` 加一行(authorize/token 端点是公开已知的)。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProviderOAuth:
    id: str
    name: str
    authorize_url: str
    token_url: str
    scopes: tuple[str, ...] = ()
    docs_url: str = ""
    requires_client_secret: bool = True


PROVIDERS: dict[str, ProviderOAuth] = {
    "github": ProviderOAuth(
        id="github",
        name="GitHub",
        authorize_url="https://github.com/login/oauth/authorize",
        token_url="https://github.com/login/oauth/access_token",
        scopes=("repo", "user"),
        docs_url="https://docs.github.com/apps/oauth-apps/building-oauth-apps/creating-an-oauth-app",
    ),
    "gitlab": ProviderOAuth(
        id="gitlab",
        name="GitLab",
        authorize_url="https://gitlab.com/oauth/authorize",
        token_url="https://gitlab.com/oauth/token",
        scopes=("api", "read_user"),
        docs_url="https://docs.gitlab.com/ee/integration/oauth_provider.html",
    ),
}


def get_provider(provider_id: str | None) -> ProviderOAuth | None:
    if not provider_id:
        return None
    return PROVIDERS.get(str(provider_id).strip().lower())


def get_provider_for_capability(item: dict[str, Any]) -> ProviderOAuth | None:
    """从 capability item 解析服务商 id(provider_id 优先,其次 id / source)。"""
    for key in ("provider_id", "id", "source"):
        prov = get_provider(str(item.get(key) or ""))
        if prov is not None:
            return prov
    return None


__all__ = ["PROVIDERS", "ProviderOAuth", "get_provider", "get_provider_for_capability"]
