# Echo OS 文档入口与归属

本仓库同时包含 Echo OS 与已迁入的 Agent 运行时，并保留迁移过程中的历史参考资料。默认以
本页列出的“当前 OS 文档”为准；历史资料中的 sibling 仓库、来源锁和第二 WebUI 流程均已退役。

## 当前 OS 文档

| 文档                                                                                                 | 作用                                                          |
| ---------------------------------------------------------------------------------------------------- | ------------------------------------------------------------- |
| [CURRENT_ARCHITECTURE.md](CURRENT_ARCHITECTURE.md)                                                   | 当前设备层架构、运行形态与状态权威                            |
| [PROJECT_REVIEW_2026-09-05.md](PROJECT_REVIEW_2026-09-05.md)                                         | 源码主链核验、原有与新增能力、竞品事实及优化依据              |
| [AGENT_OS_BOUNDARY.md](AGENT_OS_BOUNDARY.md)                                                         | 单仓内 OS 与 Agent 的代码、制品和测试边界                     |
| [ECHO_AGENT_INTEGRATION.md](ECHO_AGENT_INTEGRATION.md)                                               | Agent wheel、工作台和运行资源接入                             |
| [NAS_DELIVERY_STATUS.md](NAS_DELIVERY_STATUS.md)                                                     | NAS 产品交付状态和物理验收门槛                                |
| [AGENT_OS_OPTIMIZATION_PLAN.md](AGENT_OS_OPTIMIZATION_PLAN.md)                                       | Agent OS 优化建议、阶段优先级与验收标准（第一阶段推进中）     |
| [AGENT_OS_POLISH_STATUS.md](AGENT_OS_POLISH_STATUS.md)                                               | 深入审查后的实施记录、回归证据与待完成链路                    |
| [PROJECT_DEEP_AUDIT_2026-09-05.md](PROJECT_DEEP_AUDIT_2026-09-05.md)                                 | 源码调用链、三类照片入口、交付形态、竞品依据与优化优先级      |
| [DOCUMENT_ROLLBACK_VERIFICATION_2026-09-05.md](DOCUMENT_ROLLBACK_VERIFICATION_2026-09-05.md)         | 文本回滚、可信工作区、桌面批次整理及未完成的平台边界          |
| [DOCUMENT_ORGANIZATION_VERIFICATION_2026-09-05.md](DOCUMENT_ORGANIZATION_VERIFICATION_2026-09-05.md) | NAS 发票计划、审批、真实文件移动、原件与撤销的实施及运行证据  |
| [DOCUMENT_EXTRACTION_RESOURCE_PLAN.md](DOCUMENT_EXTRACTION_RESOURCE_PLAN.md)                         | 文档解析隔离、预算、进程回收及当前平台边界                    |
| [DOCUMENT_EXTRACTION_VERIFICATION_2026-09-05.md](DOCUMENT_EXTRACTION_VERIFICATION_2026-09-05.md)     | 解析隔离联合回归、Windows 冻结工作进程及真实 HTTP/浏览器证据  |
| [PROJECT_REASSESSMENT_2026-09-05.md](PROJECT_REASSESSMENT_2026-09-05.md)                             | 重新核实数据、权限和交付链后的项目与竞品评价                  |
| [AGENT_PERSISTENCE_POLICY.md](AGENT_PERSISTENCE_POLICY.md)                                           | 不同安装形态的日志目录、恢复边界及已有配置迁移                |
| [AGENT_PRIVACY_BOUNDARY.md](AGENT_PRIVACY_BOUNDARY.md)                                               | NAS 与 Agent 的统一隐私模式、出网限制、回归验证与部署边界     |
| [LOCAL_MODEL_SETUP.md](LOCAL_MODEL_SETUP.md)                                                         | 本机资源推荐、Ollama 下载验证、默认模型同步与失败恢复         |
| [guide/first-use-and-support.md](guide/first-use-and-support.md)                                     | 首次使用四步流程、常见问题、脱敏诊断包与支持升级边界          |
| [PHOTO_MODEL_RUNTIME.md](PHOTO_MODEL_RUNTIME.md)                                                     | 相册模型配置、加载状态、离线实测与内容指纹                    |
| [STORAGE_AUTHORIZATION_INTEGRATION.md](STORAGE_AUTHORIZATION_INTEGRATION.md)                         | 外部 Storage 服务定位、待实现的双端授权合同及真实服务验收要求 |
| [AGENT_OS_BASELINE_2026-09-05.md](AGENT_OS_BASELINE_2026-09-05.md)                                   | 第一阶段启动修复、本机实测及未通过项                          |
| [ECHO_CAPABILITY_CONTRACT.md](ECHO_CAPABILITY_CONTRACT.md)                                           | OS 能力契约                                                   |
| [ECHO_TASK_PROJECTION.md](ECHO_TASK_PROJECTION.md)                                                   | Agent 任务到 OS 表面的投影契约                                |
| [PROJECT_ANALYSIS_2026-08-28.md](PROJECT_ANALYSIS_2026-08-28.md)                                     | 去 fork 风险快照及整改记录                                    |

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
