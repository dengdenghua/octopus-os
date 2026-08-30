# Echo OS 文档入口与归属

本仓库同时包含 Echo OS 与已迁入的 Agent 运行时，并保留迁移过程中的历史参考资料。默认以
本页列出的“当前 OS 文档”为准；历史资料中的 sibling 仓库、来源锁和第二 WebUI 流程均已退役。

## 当前 OS 文档

| 文档 | 作用 |
| --- | --- |
| [architecture.md](architecture.md) | 当前设备层架构与进程边界 |
| [AGENT_OS_BOUNDARY.md](AGENT_OS_BOUNDARY.md) | 单仓内 OS 与 Agent 的代码、制品和测试边界 |
| [ECHO_AGENT_INTEGRATION.md](ECHO_AGENT_INTEGRATION.md) | Agent wheel、工作台和运行资源接入 |
| [NAS_DELIVERY_STATUS.md](NAS_DELIVERY_STATUS.md) | NAS 产品交付状态和物理验收门槛 |
| [ECHO_CAPABILITY_CONTRACT.md](ECHO_CAPABILITY_CONTRACT.md) | OS 能力契约 |
| [ECHO_TASK_PROJECTION.md](ECHO_TASK_PROJECTION.md) | Agent 任务到 OS 表面的投影契约 |
| [PROJECT_ANALYSIS_2026-08-28.md](PROJECT_ANALYSIS_2026-08-28.md) | 去 fork 风险快照及整改记录 |

部署、升级、恢复和审计操作以
[deploy/appliance/README.md](../deploy/appliance/README.md) 为准。

## 历史 Agent 参考资料

- 根目录 [CODE_WIKI.md](../CODE_WIKI.md)、`docs/auto/`、部分 `docs/architecture/` 与
  `docs/biomimetic/` 内容可能描述迁入前的独立 `echo-agent`。
- 当前实现判断以本仓库的 `runtime/`、`appliance/` 和统一 bundle 契约为准。

## 测试归属

- `tests/appliance/` 是当前 OS 的权威测试集，也是 `pyproject.toml` 默认收集范围。
- `tests/` 根目录覆盖内建 Agent 运行时，`tests/appliance/` 覆盖设备与交付边界。
- 公开源码门继续隔离运行 OS 运维测试，证明备份、恢复和安装工具不依赖运行时导入。
