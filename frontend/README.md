# Echo OS 前端

Vite + React 19 + TypeScript 的 Echo OS 唯一用户界面，同时支持浏览器、Electron 和
原生整机镜像。桌面、系统设置、文件/照片、Hub 以及内建 Agent 工作台共用同一
路由树和构建产物。

## 开发

先按[开发接入](../docs/ECHO_AGENT_INTEGRATION.md#本地开发)安装 Python 虚拟环境和
锁定的前端依赖，再从 `frontend/` 执行：

本仓锁定 `pnpm@10.26.2`。构建脚本许可统一放在 `pnpm-workspace.yaml` 的
`allowBuilds`，保留布尔值；Windows 发布使用 NSIS，未使用的 Squirrel 安装器
`electron-winstaller` 明确禁用其安装脚本。不要用其他版本的包管理器重写此配置。

```bash
node scripts/dev-appliance-backend.mjs --check
pnpm dev:with-agent
```

`dev:with-agent` 先构建工作台，再启动 Agent；确认运行时健康和设备会话接口有效后才启动前端，
随后验证前端代理，全部通过才显示 `Ready`。`dev:full` 使用同一个入口：

- Agent API：`127.0.0.1:8000`；
- Echo OS：`http://127.0.0.1:3000/#/desktop`。

工作台是 OS 内建 React 内容，没有第二个 Agent UI 服务。开发时 `/api/*` 由
Vite 代理到 Agent 后端。

在启动终端输入 `rs` 并回车可重启本次启动的前后台；输入 `quit` 或按 `Ctrl+C` 停止本次进程树。
另一个终端运行 `pnpm dev:status` 可同时检查后端和前端代理；未就绪或源码需要重启时返回非零退出码。
端口冲突会明确报错，不会自动结束占用端口的其他进程，也不会悄悄换端口。
后端启动最多等待 120 秒，前端代理最多等待 30 秒；失败会清理本次启动的服务。
运行中任一服务意外退出会报告错误并停止另一服务，不自动重放任务。
重启期间已打开的页面仍可能显示连接错误；服务就绪后使用“重试连接”，会话过期则重新登录。
该入口用于本地源码开发，不替代生产 systemd、镜像升级或恢复机制。

开发环境可在 `data/echo-appliance-dev/codex-runtime`（或 `ECHO_DEV_DATA_DIR` 下的
`codex-runtime`）单独安装固定版本的 `@openai/codex`。启动器自动读取该目录的
`package.json` 和已安装包，要求版本精确匹配、对应平台可执行文件存在，再同时设置
执行路径与运行版本。`--check` 会显示实际选择；显式 `ECHO_CODEX_EXECUTABLE` 优先。
本机已安装 `0.153.4`，目录受本地数据管理，不随 Git 分发。正式发行包仍使用独立的
版本锁定、完整性及许可证校验流程，开发升级不会改写旧版发行证据。

## 质量门

```bash
pnpm format
pnpm lint
pnpm typecheck
pnpm test
pnpm build
```

生产构建输出 `frontend/dist/`，容器和原生镜像将该目录作为 OS 界面。

## 主要产品面

- `/desktop`：桌面、Dock、窗口和系统入口；
- `/workspace/realtime/:threadId`：内建 Agent 对话与任务执行；
- `/workspace/tasks`：任务投影与中断恢复；
- `/workspace/capabilities`：Agent 能力目录和状态；
- `/workspace/observability`：运行日志与可观测信息；
- `/workspace/storage`：文件智能入口。

OS 业务页不直接依赖 Agent 私有前端实现；它们只通过 OS 维护的窄 API 客户端
消费 Agent 能力。
