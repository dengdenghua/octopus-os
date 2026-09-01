# Adapters · Integrations

> Local auth 路由 · 各家第三方集成的 router proxy。

**Source**: `runtime/adapters/integrations/`

## Modules

| Module | Summary |
| --- | --- |
| `local_auth/config.py` | — |
| `local_auth/router.py` | — |
| `oct/client.py` | oct 账号网关 HTTP 客户端 helpers。 |
| `oct/config.py` | oct 账号网关集成配置。 |
| `oct/links.py` | oct 账号绑定存储:agent actor → oct 网关 JWT + 积分快照。 |
| `oct/router_account.py` | oct 账号管理路由 · /api/account/oct/*。 |
| `oct/router_auth.py` | — |
| `oct/router_proxy.py` | oct LLM 代理路由 · /api/oct/openai/v1/*。 |

## Who imports this

**1** file(s) reference this package:

- **`runtime/platform/`** · 1 file(s)
  - `runtime/platform/ui/_app_auth_routers.py`

