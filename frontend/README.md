# Echo OS 前端

Vite + React 19 + TypeScript 的 Echo OS 唯一用户界面，同时支持浏览器、Electron 和
原生整机镜像。桌面、系统设置、文件/照片、Hub 以及内建 Agent 工作台共用同一
路由树和构建产物。

## 开发

```bash
corepack enable
make frontend-install
cd frontend && pnpm dev:with-agent
```

`dev:with-agent` 同时启动 Agent 后端和 Echo OS 前端：

- Agent API：`127.0.0.1:8000`；
- Echo OS：`http://127.0.0.1:3000/#/desktop`。

工作台是 OS 内建 React 内容，没有第二个 Agent UI 服务。开发时 `/api/*` 由
Vite 代理到 Agent 后端。

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
