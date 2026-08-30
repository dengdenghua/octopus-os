# 前端 core/ → 后端 runtime/ 模块映射

全栈开发时的速查表：前端 `@/core/<domain>` 对应的后端 `runtime/<path>`。

| 前端模块 (`src/core/`) | 后端模块 (`runtime/`)                                                | 说明                   |
| ---------------------- | -------------------------------------------------------------------- | ---------------------- |
| `account/`             | `adapters/integrations/local_auth/`, `adapters/integrations/molili/` | 账户认证               |
| `agents/`              | `execution/agents/`, `execution/arms/`                               | 代理与腕足             |
| `api/`                 | `platform/`                                                          | API 客户端 + 类型生成  |
| `arena/`               | `execution/`                                                         | Agent 竞技场           |
| `artifacts/`           | `platform/ui/`                                                       | Workspace 产物加载     |
| `auth/`                | `adapters/integrations/`                                             | 认证 Token 管理        |
| `background/`          | `adapters/scheduler/`                                                | 后台定时任务           |
| `browser/`             | `execution/suckers/browser_skills.py`                                | 浏览器自动化           |
| `channels/`            | `adapters/channels/`                                                 | 多平台消息通道         |
| `config/`              | `platform/config/`                                                   | 运行时配置             |
| `events/`              | `core/nerves/bus.py`                                                 | 事件总线               |
| `i18n/`                | `platform/i18n/`                                                     | 国际化                 |
| `integrations/`        | `adapters/integrations/`                                             | 第三方集成             |
| `mcp/`                 | `adapters/mcp_client/`                                               | Model Context Protocol |
| `memory/`              | `memory/`                                                            | 记忆/Journal/知识图谱  |
| `messages/`            | `protocol/items.py`                                                  | 消息协议               |
| `models/`              | `sensing/eyes/`                                                      | LLM 模型路由           |
| `observability/`       | `adapters/instrumentation/`                                          | 追踪+指标              |
| `parallel-agents/`     | `execution/arms/`, `safety/chromatophores/`                          | 并行执行+Boids 仲裁    |
| `plugins/`             | `execution/suckers/`                                                 | 技能（Skill）插件      |
| `realtime/`            | `protocol/`                                                          | WebSocket JSON-RPC     |
| `regeneration/`        | `safety/regeneration/`                                               | 反思+自进化            |
| `research/`            | `research/deep_research.py`                                          | 深度研究               |
| `settings/`            | `platform/config/`                                                   | 用户设置持久化         |
| `skills/`              | `execution/suckers/`                                                 | 技能注册表             |
| `tasks/`               | `core/cerebrum/`, `execution/`                                       | 任务规划+执行          |
| `teach-repeat/`        | `safety/regeneration/`                                               | 示教→固化工作流        |
| `teams/`               | `execution/arms/`                                                    | 团队协作（多 Agent）   |
| `threads/`             | `platform/ui/`                                                       | 会话线程               |
| `tools/`               | `execution/beak/executor.py`                                         | 工具执行               |
| `uploads/`             | `platform/ui/uploads_router.py`                                      | 文件上传               |

## 协议层镜像

| 前端                   | 后端                         | 说明                |
| ---------------------- | ---------------------------- | ------------------- |
| `realtime/envelope.ts` | `protocol/envelope.py`       | JSON-RPC 信封       |
| `realtime/items.ts`    | `protocol/items.py`          | 消息 Item 联合类型  |
| `realtime/reducer.ts`  | `protocol/events.py`         | 状态 reducer        |
| `api/openapi-types.ts` | `docs/openapi-snapshot.json` | 自动生成的 API 类型 |

## 构造约定

- `client.ts` / `api.ts` → API 客户端（TanStack Query hooks 的底层）
- `hooks.ts` → TanStack Query hooks
- `types.ts` → 领域类型
- `index.ts` → 模块重导出入口
