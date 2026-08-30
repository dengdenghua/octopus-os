# WorkBuddy 连接器 → echo 插件(Fork + 认证编排层)

> **发布源，不是桌面内置市场。** 本目录仅供受保护的市场发布流水线生成并签名
> 独立连接器包；Echo 桌面端只下载签名目录，并在用户选择安装时拉取对应连接器。
> `packaging/desktop/build.yml` 不得把本目录复制进安装包。

> 从 WorkBuddy 5.3.14(腾讯)解包 fork 的**108 个连接器** + echo **认证编排层**实现。
> 源: `~/.workbuddy/connectors-marketplace/`(官方连接器市场)

## 目录结构
```
workbuddy-connectors/
├── README.md                        # 本文件
├── .codebuddy-connector/
│   └── connectors.json              # 官方索引(108 个 + auth_injection_rules)
├── connectors/<id>/                 # 原始连接器
│   ├── cli.json                     # CLI 型: node运行时 / 安装 / auth / status
│   ├── mcp.json                     # MCP 型: mcpServers(url/type/timeout)
│   └── skills/**/SKILL.md           # 捆绑技能(如 feishu 27 个 lark-*)
├── icons/                           # 连接器图标
├── echo-manifest.json            # 规范化索引(转插件后,供注册表/商城消费)
├── INDEX.md                         # 可读索引
└── scripts/
    └── port-connectors-to-echo.py  # 转成插件(packs/<id>/)脚本
```

## 108 个连接器构成
| 类型 | 数量 | 说明 |
|---|---|---|
| 🔌 MCP | 84 | 远程 MCP server(streamableHttp),如 westock-mcp / canva-ai / tencent-docs |
| ⌨️ CLI | 22 | 包一层官方 CLI(含登录/状态命令),如 feishu(lark-cli) / dingtalk(dws) |
| 🧩 skill-only | 2 | 纯捆绑技能 |

认证模式: token(99) / server-side(6) / oneid-token / oauth / mcp。官方带 `auth_injection_rules`
(如 tencent-docs 双 token 自动注入 `Authorization` + `X-Oneid-Access-Token`)。

## echo 认证编排层(已实现)
`runtime/platform/connectors/`:

| 模块 | 作用 |
|---|---|
| `credential_store.py` | AES-256-GCM 加密凭据库(`~/.echo/connectors/`,明文永不落盘) |
| `connector_registry.py` | 连接器定义加载 + 安装(技能→skills, MCP 登记默认禁用)/卸载/启停 |
| `auth_orchestrator.py` | 认证编排:connect(token 存库 / CLI 登录命令)、disconnect、status、**auth 头/环境变量注入** |

### MCP 代理认证注入(已接入)
MCP 客户端在建立 HTTP/streamable-http 连接时,自动为**已安装 + 已启用 + 已连接**的连接器注入
解析出的 auth 头(`Authorization` / `X-Oneid-Access-Token` 等);stdio 型注入环境变量。注入优先级:
**用户手填 header > 连接器 auth > 通用 OAuth 兜底**。

```
runtime/adapters/mcp_client/client.py            # HttpMCPClient._transport() + StdioMCPClient._stdio_env()
runtime/adapters/mcp_client/persistent_client.py # PersistentStdioMCPClient._stdio_parameters()
runtime/platform/connectors/auth_orchestrator.py # mcp_injection_for_server(server_name) 反查连接器
```
连接器 id ≠ MCP server 名(如 `canva-ai` → `canva-mcp`),按 `conn.mcp_servers` 的 key 反查。

网关接口(已挂载): `runtime/sensing/gateway/connector_router.py`
```
GET    /api/connectors                      # 列表(108,含安装/启用状态)
POST   /api/connectors/{id}/install         # 安装
POST   /api/connectors/{id}/connect         # 认证编排(带 tokens / 返回 CLI 命令)
GET    /api/connectors/{id}/status          # 认证状态
GET    /api/connectors/{id}/headers         # 解析出的 auth 注入头
POST   /api/connectors/{id}/disconnect      # 断开并清除凭据
DELETE /api/connectors/{id}/install         # 卸载
```

## 转成我们的插件
```bash
python3 extensions/workbuddy-connectors/scripts/port-connectors-to-echo.py          # manifest + INDEX
python3 extensions/workbuddy-connectors/scripts/port-connectors-to-echo.py --packs  # 生成 packs/<id>/ 插件包
```
每个包: `plugin.json`(type=connector, auth_mode/mcp/cli) + `mcp.json` + `cli.json` +
`connector.json`(认证编排元数据) + `skills/`。

## 与 Codex 插件的关系
Codex/OpenAI 插件(`~/.codex/plugins/cache`)同样有**认证编排** —— 每个插件 `.app.json` 声明
依赖的 `connector_*`(如 google-drive → `connector_5f3c8c41...`、figma、sites),OAuth/token 全在
平台侧管。WorkBuddy 连接器与 Codex connector 是同一套架构:都是「集中连接器注册表 + 外部认证 +
auth 注入 + 捆绑技能」。本仓库的认证编排层把 WorkBuddy 这套落地到 echo 自身上。

## 测试
```
tests/test_connectors.py          # 加密库/注册表/认证编排/网关路由
tests/test_mcp_auth_injection.py  # MCP 代理认证注入(headers/env)7 例
```
前端 `frontend/src/components/store/connector-market-panel.tsx`(Hub → 插件 → 连接器 tab)
调 `/api/connectors` 完成浏览 / 安装 / 连接认证 / 启停。
