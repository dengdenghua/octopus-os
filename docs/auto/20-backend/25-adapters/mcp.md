# Adapters · MCP

> MCP 客户端 + Trust store · ADR-007 治理 · 未审批 server 的工具拒注册。

**Source**: `runtime/adapters/mcp_client/`

## Exports

- `HTTP_AVAILABLE`
- `HttpMCPClient`
- `MCPClient`
- `MCPClientError`
- `MCPInvocationResult`
- `MCPServerConfig`
- `MCPTool`
- `MCPTrustStore`
- `MockMCPClient`
- `PersistentStdioMCPClient`
- `STDIO_AVAILABLE`
- `StdioMCPClient`
- `TrustEntry`
- `close_all_persistent_clients`
- `get_trust_store`
- `register_mcp_tools_as_skills`
- `reset_trust_store_for_tests`

## Modules

| Module | Summary |
| --- | --- |
| `bridge.py` | — |
| `client.py` | — |
| `oauth.py` | MCP OAuth 2.0 (PKCE) client — authorize-on-enable for remote MCP servers. |
| `oauth_discovery.py` | OAuth metadata discovery + dynamic client registration for MCP (step 2). |
| `oauth_providers.py` | 服务商直连 OAuth App 配置 —— 为不暴露 ``.well-known`` 元数据、但支持 OAuth App 的服务商提供网页登录(WorkBuddy 的 ``server-side`` 连接器就是靠 它平台自己注册的 OAuth App 做到的)。 |
| `persistent_client.py` | — |
| `trust.py` | — |
| `types.py` | — |

## Who imports this

**7** file(s) reference this package:

- **`runtime/cli_mcp.py/`** · 1 file(s)
  - `runtime/cli_mcp.py`
- **`runtime/cli_serve.py/`** · 1 file(s)
  - `runtime/cli_serve.py`
- **`runtime/platform/`** · 4 file(s)
  - `runtime/platform/capabilities/capability_registry.py`
  - `runtime/platform/config/builder.py`
  - `runtime/platform/connectors/oauth_support.py`
  - `runtime/platform/ui/health_router.py`
- **`runtime/sensing/`** · 1 file(s)
  - `runtime/sensing/gateway/mcp_router.py`

