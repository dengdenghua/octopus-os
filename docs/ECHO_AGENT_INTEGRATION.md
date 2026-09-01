# Echo OS 与内建 Agent 接入

Echo Agent 的 Python 运行时、资源和 Codex 打包器已经迁入 Echo OS 仓库。产品只有一个
源码 revision、一个 Python distribution 和一套前端：`runtime/` 负责规划、执行、恢复与
能力服务，`appliance/` 负责设备认证、文件、桌面和系统能力，`frontend/` 提供唯一工作台。

OS 功能对 Agent 的调用仍集中在 [`appliance/agent_api/`](../appliance/agent_api/)；这是单仓
内部的稳定领域边界，避免设备层直接依赖运行时私有实现。完整规则见
[Echo OS ↔ Echo Agent 工程边界](AGENT_OS_BOUNDARY.md)。

## 本地开发

不需要准备 sibling 仓库。在当前仓库执行：

```bash
make install
cd frontend
pnpm dev:with-agent
```

开发命令启动 `127.0.0.1:8000` 的内建 Agent 后端和
`http://localhost:3000/#/desktop` 的唯一前端。工作台、任务、能力和终端日志都是 OS
内部路由；`3001` 第二前端和 `ECHO_AGENT_UI_BASE_URL` 已退役。

## 发布边界

`make agent-bundle` 从当前干净 Git revision 生成三个同源表面：

- `agent-dist/`：统一 `echo-os` wheel、依赖锁和安装清单；
- `agent-resources/`：agents、skills、prompts、protocols 与 teams；
- `agent-codex/`：锁定版本的 Linux Codex 可执行包。

`agent_bundle.py` 将三类制品绑定到同一个 source ID，并在 Docker 构建、原生镜像组装和
启动健康门中重算哈希。开发中的 dirty 工作区只能用 `make agent-bundle-local`；脚本先在
仓库外冻结不可变 QA 快照，并在清单标记 `dirty: true`，不能充当正式发布包。

原生系统中，`echo-agent.service` 是内建运行时的兼容服务名。它以普通 `echo` 用户运行，
仅监听 `127.0.0.1:8000`，可写状态位于加密 `/var/lib/echo-agent`。服务名和状态目录保留
兼容性不代表仍依赖另一个仓库或第二套进程实现。
