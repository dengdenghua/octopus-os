# Echo OS 与内建 Agent 接入

Echo Agent 的 Python 运行时、资源和 Codex 打包器已经迁入 Echo OS 仓库。产品只有一个
源码 revision、一个 Python distribution 和一套前端：`runtime/` 负责规划、执行、恢复与
能力服务，`appliance/` 负责设备认证、文件、桌面和系统能力，`frontend/` 提供唯一工作台。

OS 功能对 Agent 的调用仍集中在 [`appliance/agent_api/`](../appliance/agent_api/)；这是单仓
内部的稳定领域边界，避免设备层直接依赖运行时私有实现。完整规则见
[Echo OS ↔ Echo Agent 工程边界](AGENT_OS_BOUNDARY.md)。

## 2026-09-06：吸收 D 盘 echo agent 的高价值部分

D 盘项目最值得迁入的不是另一套 UI 或数据库，而是执行边界。当前 OS 已新增一层
host-owned contract：主机负责 `task_id`、线程/租户/用户身份、权限范围、租约和绝对
截止时间；Native 与 Codex 只是可替换的执行引擎。引擎选择、模型选择和 Agent 角色选择
保持分离，续跑只能改变 instruction，不能换掉原任务的权限或资源上限。

这次又把 D 盘项目里最容易被忽略的“审批语义一致性”接到了真实链路：
`default/plan` 仍由用户审批，`acceptEdits/approve-for-me` 在服务端规范化为
`auto_review`，由独立 reviewer 按当前用户意图审查所有越过边界的动作；reviewer 超时、
不可用、拒绝或连续三次拒绝都会 fail-closed。`bypassPermissions/full-access` 只有桌面
配置显式开启 `safety.allow_client_approval_bypass` 后才会把 Codex 的当前线程切到
`approval_policy=never`、`danger-full-access` 和网络开启；sidecar 的状态目录仍明确拒绝，
客户端不能凭 metadata 伪造这条开关。Native ReAct、Codex dynamic tools、CLI 和
Codex App Server 共用同一组模式解析，避免 UI 看到一种模式而后端执行另一种模式。

Codex 的 stdout 事件队列也采用有上限的相邻 delta 合并和 token 用量快照替换。高频输出
不会因为短暂调度抖动把健康任务误判为断连；真正无法消化的洪峰会生成可恢复的
`codex_event_backpressure`，保留已完成步骤并提示继续，而不是把内部异常直接展示给用户。
Windows 控制会话同时保留 `SYSTEMROOT/COMSPEC/PATHEXT/WINDIR` 等必要运行环境，归档
探测遇到短暂文件锁会按“未确认归档”继续服务。

逐项对照后的取舍如下：

| D 盘能力 | 当前 OS 对应实现 | 对本项目的实际价值 | 处理决定 |
| --- | --- | --- | --- |
| `Session` 作为单一上下文根 | `runtime/platform/process/session.py`、`runtime/execution/host_boundary.py` | 让 actor、tenant、线程、权限和租约一起跨线程传递，阻断模型或请求体伪造上下文 | 已接入主执行入口 |
| TaskSupervisor、lease、heartbeat、recovery | `runtime/platform/process/task_supervisor.py`、`runtime/execution/parallel_agents/` | 让 Agent 任务在刷新/重启/并发冲突后仍能说明谁在执行、能否接续 | 已接入；只读恢复不自动重放副作用 |
| effect receipt / artifact handoff | `runtime/execution/tool_engine/effect_receipts.py`、`artifact_contracts.py` | 把“模型说完成”与“provider 已写入、可回读”分开，支持文件和报告交付追踪 | 已接入；领域 provider 仍保留自己的权威 |
| 记忆来源和反馈闭环 | `runtime/memory/semantics.py`、`memory/threads/feedback.py`、`session_search.py` | 让长期记忆、对话搜索和用户评价可追溯，不让模型摘要冒充系统事实 | 已复用现有本地存储 |
| Graph/Swarm 的依赖和资源 claim | `runtime/execution/parallel_agents/`、`runtime/execution/swarm/` | 在多 Agent 并发时表达依赖、互斥资源和部分失败 | 已复用；不另建第二个调度器 |
| 递归子 Agent 的跨 worker 治理账本 | `runtime/execution/subagents/governance.py`、`bridge.py`、`ephemeral_runner.py` | 把进程内并发上限扩展到部署级租约、单轮 token/cost breaker 和幂等用量结算，避免多个 worker 各自放行造成超额 | 已吸收为增量控制面；主 ReAct 与 mini-loop 都回报 provider 用量，账本不可用时默认保留旧兼容路径，可配置 fail-closed |
| Codex readiness / 预检 | `runtime/execution/codex_backend/readiness.py`、`_config_endpoints_codex.py` | 在真正启动进程或消耗模型额度前区分开关、可执行文件、模型、工具和账号缺失；UI 可给出可行动原因 | 已接入模型 profile；只读，不登录、不刷新、不发请求 |
| OKF/repo knowledge substrate | `runtime/memory/hemolymph/repo_context.py`、知识图谱与 Wiki 生成链 | 提高代码/项目资料检索的一致性，减少每次从零扫描 | 已保留现有实现，后续按真实中文/大库数据测量 |
| Capability plane / 多租户市场 | `appliance/capabilities/`、技能/插件市场 | 适合多产品或 SaaS 的版本化发布，不是单机 NAS 首要瓶颈；本仓已有能力定义与授权合同 | 不整套搬入；仅在需要时补来源、release pin 与 readiness |

这张表是架构边界而不是“已完成”清单：每一行仍需以对应入口的回归和真实任务证据为准。
尤其是 `Session`、租约和回执解决的是执行可信度，不能替代相册、文件、Storage 或
备份 provider 对原件、成员权限和恢复结果的确认。

### 对 D 盘源码的逐项审计

2026-09-06 对 `D:\\echo agent` 当前分支做了目录与源码级盘点。下表列的是 D 盘确实存在、
但不能直接等同于“本项目缺失”的模块；“等价实现”表示本仓已有不同路径或不同领域的
实现，“待接入”表示只有在对应产品场景成立并完成安全合同后才值得迁移。

| D 盘源码 | 它实际解决的问题 | 本仓核对结果 | 决策 |
| --- | --- | --- | --- |
| `execution/subagents/candidate_patch.py` | 候选补丁的检查、指纹、备份、应用、回滚与重试幂等 | 本仓有演化/候选 canary 与回执，但没有同形态的代码补丁事务 | P2；先做只读检查和 reviewer 门，再允许代码工作区写入；不把它接到 NAS 原件路径 |
| `execution/subagents/artifact_handoff.py` | 子 Agent 之间传递带哈希、大小、租约和回读条件的产物 | 本仓 `artifact_contracts.py`、`effect_receipts.py` 已吸收同一原则 | 保留本仓合同，不复制 D 盘实现；文件、相册和 Storage 继续由各自 provider 确认 |
| `execution/subagents/isolated_worktree.py` | 为代码任务建立隔离 worktree，避免子 Agent 直接改宿主 checkout | 本仓已有 `worktree_loop.py` 和 host boundary，但整机/跨进程恢复仍待验收 | 仅用于代码项目；不作为 NAS 文件整理的通用写入口 |
| `execution/subagents/execution_context.py` | 从父任务派生受限的子任务 Session、超时和只读父上下文 | 本仓 `Session`、`host_boundary.py`、subagent bridge 已接入 | 已吸收；后续补全所有入口的 host 归属检查 |
| `memory/cowork/context_steward.py` 及 context engines | 给协作成员分配相关上下文、token 预算和成员级可见范围 | 本仓有 cowork 房间/黑板/成员存储，但没有 D 盘同名的统一上下文管家 | P1；多人协作成为主场景后再接，先用成员 grant 和脱敏测试定义合同 |
| `sensing/gateway/collaboration_delivery_outbox.py` | 协作报告的持久 outbox、重试、幂等和崩溃恢复 | 本仓已有 journal、jobs/workflow settlement、项目 outbox 和云边 outbox | 复用既有持久化；不再建第二个 delivery 数据库 |
| `sensing/gateway/a2a_server.py` 与 A2A task store | 让本机 Agent 作为可发现的远端 Agent 接收任务并持久化事件 | 本仓已有 A2A remote registry/client 路由，未提供同等 server 面 | P2；只有在多台 NAS/Agent 互调时接入，必须先有身份、资源 ACL、限流和撤销合同 |
| `safety/sandboxing/_landlock_wrapper.py` | Linux Landlock 系统调用封装，提供硬文件系统边界 | 本仓 `sandbox.py` 已有 Landlock 选择与实现 | 已吸收概念；Windows 软约束与 Linux 真机硬沙箱仍分开验收 |
| `safety/evolution/dual_helix_policy.py` | 根据低置信度/影子结果决定是否触发演化候选 | 本仓 `dual_helix_shadow.py`、canary 与 runtime outcome 已有等价链路 | 已吸收语义；不重复引入第二套演化调度器 |
| `platform/models/provider_errors.py` | 把提供方 HTTP/额度/不可用错误规范化为稳定分类 | `runtime/platform/models/provider_errors.py` 已提供脱敏公共异常，并由模型插件 connect/enable 入口消费 | 已接入；其他模型 provider 仍需按同一合同迁移，不改变原始诊断 |
| `execution/agents/team_patterns.py`、`collaboration_quality.py` | 依据任务识别团队模式并做相关性、证据性、独立性评分 | 本仓有 team/cowork 策略和任务投影，质量评分口径尚未统一 | P2；先定义可解释指标，不能让模型自评分直接改变权限或结果 |

这次盘点说明 D 盘项目的价值主要落在 **Agent runtime control plane**：任务身份、隔离、
产物交付、协作上下文和外部 Agent 互操作。它不能替代本仓的 **appliance/data plane**：
磁盘、照片原件、索引、成员授权、备份和设备恢复。复制 D 盘 UI、第二套 SQLite 或第二个
调度器会制造两个权威，反而削弱项目；正确的接法是以本仓 host/provider/receipt 合同为根，
逐项吸收可验证的边界和失败语义。

D 盘仓库里的 capability-plane 仍标为 proposal，部分多租户、市场和跨产品发布能力是设计
而不是可直接依赖的生产实现；因此本仓只吸收可验证的协议和边界，不把 D 盘代码目录或
配置文件当作运行时依赖。

能力市场不是当前的迁移缺口：本仓的
[`appliance/capabilities/`](../appliance/capabilities/) 已经提供带版本的能力定义、请求
Schema、资源范围、审批和审计合同。D 盘 capability-plane 对本项目的增量价值只在于把
来源、release pin 和运行时 readiness 继续补到同一份清单；在没有实际跨产品发布需求前，
复制它的市场、Graph 或多租户编排会增加第二套发布与授权权威。

云商城目录也遵循同一边界：源码 checkout 缺少可选专家子模块时，`CloudCatalog` 只提供
明确标记的内置 `local_dev/builtin_fallback` 描述，方便模拟器和本地 UI；正式包没有签名
目录或可信缓存时仍 fail-closed。`/api/agent-market/cloud/installed` 将本地已安装投影与
目录可用性拆开返回，因此模型服务或商城离线不会阻塞桌面启动，也不会把未验证包误报为可安装。

D 盘 Agent 对资源治理的另一个可复用价值已经落到本地图片链路：设备相册自己的索引锁
继续属于 appliance，但通用图片/视频技能共享的 CLIP/InsightFace 单例现在由
`runtime/memory/hemolymph/image_semantic_index.py` 的 `inference_slot` 统一限流。默认单并发、
有界等待退避、等待期间可取消；同一设备的不同服务进程通过数据目录下的 OS 锁文件共享槽位，
目录不可用时显式在 readiness 中报告 `processShared=false` 并安全退回进程内限制。超限会返回固定 `image_inference_busy` 并保留旧索引；槽位
只围绕原生推理调用，回读和 SQLite 仍由领域 provider 负责。它吸收的是 D 的控制面边界，
不是把 D 的数据库、第二个调度器或模型实现搬进本仓。

相册任务在同一边界上补了安全暂停/恢复：暂停请求只在索引检查点生效，当前临时事务
回滚、旧快照保留，写锁和推理准入随即释放；恢复重新校验计划和图库指纹后继续使用
原任务身份。这样 D 的资源治理价值落到了真实本地数据库链路，同时仍由 appliance
拥有相册的文件信任、审批、租约和任务 API。

实时 ReAct 和 Codex App Server 入口都在 `TaskExecutionGuard` 取得租约后绑定这份不可变
请求，并通过 `ContextVar` 传给惰性 provider、Codex 动态工具和子 worker。工具开始、完成、
心跳和清理复用同一执行身份；旧的匿名/无租约测试保留兼容路径。这样 D 项目中的“宿主
拥有任务、provider 执行任务”原则落到了两个真实执行引擎，而没有新增第二个调度器。

这条边界也贯通了 Project OS、直接 subagent、team/cowork、auto-parallel、parallel 和
deep-research 的后台 worker。跨线程调度会显式重建或继承 Session；没有当前 host 的调用不会
接受上下文里伪造的 `caller_session`。deep-research 的持久 ResearchJob 同时保存 owner/tenant 和
聚合 `host_task_id`，查询按这两个坐标隔离，重启后仍能从作业记录直达 TaskSupervisor 恢复队列；
standalone parallel/deep-research 批次还会登记一个聚合 TaskSupervisor host record，逐 worker 记录
继续保留各自执行租约。批次终态会结算聚合记录，租约丢失会停止后续调度；进程重启后
`recovery-snapshot` 可以从聚合记录生成脱敏的任务、状态、时间和错误视图，但不伪造模型输出、
事件或新的调度结果，也不会自动重建内存批次。deep-research 还通过
`/api/research/deep/jobs/{job_id}/recovery-snapshot` 暴露同一快照，并先校验研究作业的
owner/tenant 与持久 host 坐标；研究面板在实时批次不可见时会降级展示这份脱敏视图，
不会把它误当作可取消的实时 worker。
作业接口和历史列表同时返回 additive 的 `recovery_required` 标记：它保留工作流原始
`status`，但明确提示“运行中”只是最后一次持久状态，当前需要先查看恢复队列，避免把
旧状态误解为仍有活跃调度器。

通用并行工作台也通过 `/api/agents/parallel/recovery-snapshots` 按当前用户和租户发现
持久批次。进程重启后没有内存 `BatchResult` 时，面板使用快照中的任务、状态和描述生成
只读卡片，并明确显示“持久恢复视图”；它不会提供取消按钮，也不会把缺失的 worker 输出
补成结果。该列表只返回已经通过 host owner/tenant 过滤的脱敏快照，避免用原本的全局 live
status 来发现别人的批次。

`status` 用于当前进程的聚合活动概览，持久恢复列表则专门负责重启后的批次发现；后者只返回
已经通过 host owner/tenant 过滤的脱敏快照。

跨引擎交付还使用统一的 artifact contract：只传递路径、哈希、大小和生产任务 ID，
内容仍留在现有授权工作区；`HandoffRecorder` 只能由 host 注入持久读写回调，模型或传输
JSON 不能伪造回执。后续文件、相册和 NAS provider 仍各自维护领域回执，靠 task/intent
ID 关联，不合并它们的数据库。

相册这条关联现在也有可验证的资源坐标：设备相册和 Agent 的目录图片技能用同一套
部署内 `photo-library` source id；每个设备相册结果再带 `assetId` 和由大小/mtime
计算的 `assetRevision`。Agent 通过设备相册工具返回的 `resourceSource` 保留这份库身份，
同时保留旧的 provider `source="appliance.photos"` 兼容字段。host execution 存在时，
工具还回传服务器生成的 `execution.taskId`/`threadId`，所以任务、图片结果和后续领域
回读可以关联而不会把宿主路径或模型参数当成身份。通用图片搜索和元数据结果在文件
仍位于库内时还附带 path-free 的 `assetReference`，用于后续回读；缺失或越界文件不会
伪造引用。source id 只是本机部署范围的哈希，
数据库仍按设备相册、通用 Agent 图片库和外部 Storage 分开；外部 Storage 的资源级成员
授权仍待真实服务合同和双用户验收，不能由这个 id 代替。

通用 `image_*`、`video_*` 技能现在明确属于工作区图片/视频能力：有 Session 时，执行器会
把 `directory`、`image_path`、`video_path` 和批量图片参数解析并校验到当前读范围，模型给出的
绝对 NAS 路径或其他租户路径会在 handler 前拒绝。设备 NAS 相册必须走 `photos_*`，由该服务
重新取得 `DataAccessPolicy`、成员可见范围和资源回读；没有 Session 的本地 CLI/测试调用仍
保留显式目录契约，不把开发工具误当成设备授权入口。

记忆层也吸收了 D 项目的证据语义。事实现在可以带 `user_statement`、
`user_preference`、`project_knowledge`、`model_summary` 或 `derived_summary` 标签；旧记录
和无法证明来源的导入统一按 `unclassified/unverified` 处理。进入模型上下文时会保留
来源标签、加入“历史记忆只是参考数据”的提示，并 JSON 引用内容，避免一条记忆把自己
伪装成系统指令。记忆永远不能授权动作、证明执行成功或覆盖当前任务；执行结果仍以
Journal、TaskSupervisor 和 provider 回读为准。

递归子 Agent 还吸收了 D 盘治理账本的边界。每个有 `Session.turn_id` 的子调用在共享
SQLite WAL 中取得短期租约；跨 worker 的活动数、单轮 token/cost 累计和 breaker 由同一事务
判断，重复用量事件用 `usage_id` 去重。长调用由后台心跳续租；正常结束会释放租约，超时/取消
会在 worker 真正退出后释放，若线程无法退出则停止续租并让租约自然过期。续租明确失败会取消
子任务，连续的账本抖动也会在有限重试后收紧，避免租约过期后继续递归。
这只是执行额度控制面，不能替代父任务的恢复快照、领域数据库或 provider 回读；未带宿主
turn 身份的历史 raw 调用仍走兼容的进程内限制。

治理账本的结果只通过当前 root thread 已鉴权的 subagent event bus 输出脱敏快照；租约 owner、
内部 lease id 等字段不会进入事件或前端模型。Agent Workbench 时间线会在子 Agent 完成、失败、
超时或取消时显示本轮 token/cost 用量，账本熔断会使用错误态颜色提示。这样运行中的额度状态和
跨 worker 保护可以被操作员观察，但不会新增一个可枚举任意 turn 的全局治理接口。

部署可用以下环境变量调整边界：`ECHO_MAX_ACTIVE_SUBAGENTS_PER_TURN` 控制单轮活动租约，
`ECHO_MAX_SUBAGENT_TOKENS_PER_TURN` 和 `ECHO_MAX_SUBAGENT_COST_USD_PER_TURN` 控制累计
额度，`ECHO_SUBAGENT_LEASE_SECONDS` 控制租约时长，`ECHO_SUBAGENT_GOVERNANCE_DB` 指定
共享账本路径；设置 `ECHO_SUBAGENT_GOVERNANCE_REQUIRED=1` 后账本不可用会拒绝受管 spawn。

这次接入的边界是明确的：它提高了任务身份连续性、续跑安全、跨引擎扩展和记忆防误用
能力，但没有替代现有相册/文件/NAS 数据库，也没有让本地后端自动变成离线模型。仍需把
host boundary 推广到尚未接入的入口，完成跨进程与 Linux/物理恢复验收，并做真实模型质量
和资源基线。

相关实现与回归：

- `runtime/execution/request.py`、`host_boundary.py`、`engines.py`、`artifact_contracts.py`
- `runtime/safety/approval/permission_modes.py`、`guardian_review.py`、
  `frontend/src/core/permissions.ts`：四种权限模式、独立 reviewer 与前后端同名兼容别名
- `runtime/execution/tool_engine/_native_tool_approval.py`、
  `runtime/safety/governance/execution_policy.py`：最后一公里 native/governance gate 也复用
  同一模式解析；`acceptEdits` 不再把写工具名当作无条件批准，缺少 reviewer 时保持拒绝
- `runtime/execution/codex_backend/client.py`、`security.py`、`backend.py`、
  `runtime/sensing/gateway/realtime_turn_input.py`：App Server 配置、沙箱、网络和事件背压
  由服务端统一约束；`frontend/src/core/threads/use-thread-stream-realtime.ts` 只传递可审计上下文
- `echo_runtime/resource_identity.py`（`runtime/platform/resource_identity.py` 保留兼容导出）、
  `appliance/photos/service.py`、`appliance/photo_tools.py`
- `runtime/sensing/gateway/_realtime_react_stream_drive.py`、`realtime_codex_backend.py`
- `runtime/memory/semantics.py` 与 `runtime/memory/runtime_state/hub.py`
- `runtime/execution/parallel_agents/_batch_host.py` 与 `tests/test_parallel_batch_host.py`
- `runtime/execution/codex_backend/readiness.py` 与 `tests/test_codex_readiness.py`
- `runtime/platform/io/sqlite.py` 与 `tests/test_sqlite_lifecycle.py`：短时 SQLite 事务退出时
  自动关闭句柄；不改变领域层常驻连接的所有权。
- `runtime/execution/subagents/governance.py`、`bridge.py`、`ephemeral_runner.py` 与
  `tests/test_subagent_governance.py`、`tests/test_subagent_react_drive.py`：跨 worker 子
  Agent 租约、心跳、单轮额度 breaker 和 provider 用量回报；不改变领域层数据库边界。
- `runtime/execution/subagents/worktree_loop.py`、`runtime/safety/organization/team_runner.py`、
  `runtime/memory/cowork/runtime.py`：工作树、团队并行角色和后台 cowork 线程显式继承或
  建立 host `Session`，避免线程池/守护线程把治理账本、租约和任务归属截断。
- `frontend/src/core/threads/subagent-bus-events.ts`、`frontend/src/components/workspace/live-tool-timeline.tsx`：
  只接收白名单治理快照并在工作台显示 token/cost；前端治理事件映射回归为 **10 passed**。
- `tests/test_execution_boundary.py`、`tests/test_memory_semantics.py`
- `pytest tests/realtime_cerebrum/test_resume.py tests/realtime_cerebrum/test_task_lease_admission.py tests/realtime_cerebrum/test_supervisor_lease_renewal.py tests/test_execution_boundary.py tests/test_task_execution.py -q`：129 passed
- `pytest tests/test_drive_codex_app_server.py tests/test_coder_role_routing.py -q`：31 passed
- `pytest tests/test_memory_semantics.py tests/test_profile_memory.py tests/test_memory_hub.py tests/test_memory_assets.py tests/test_memory_router.py tests/test_user_memory_visibility.py tests/test_openai_gateway.py tests/test_user_preferences.py -q`：75 passed

## 本地开发

不需要准备 sibling 仓库或发布 bundle。使用 Python 3.11+、Node.js 和
`frontend/package.json` 指定的 pnpm 版本，在当前仓库执行。

Linux / macOS：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev,serve,tracing,web,local-auth]'
cd frontend
pnpm install --frozen-lockfile
node scripts/dev-appliance-backend.mjs --check
pnpm dev:with-agent
```

Windows PowerShell：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev,serve,tracing,web,local-auth]'
cd frontend
pnpm install --frozen-lockfile
node scripts/dev-appliance-backend.mjs --check
pnpm dev:with-agent
```

启动器依次使用显式 `ECHO_AGENT_CONFIG`、已有 `config.local.yaml`、
`config.example.yaml`，不会修改配置。若需定制，复制示例为 `config.local.yaml`。
默认示例声明模型配置；模型任务仍需在实际使用前配置提供方，示例文件不保证离线推理。

Python 默认从仓库 `.venv` 查找；已有其他开发环境时将 `ECHO_AGENT_PYTHON` 设为解释器
文件路径。显式配置或 Python 路径无效时直接报错，避免静默使用另一环境。

`--check` 只检查配置/解释器文件选择、端口和可选版本元数据，不启动服务、不创建数据目录、
不读取认证秘密。它不检查 Python 包是否已安装，也不证明后端或模型可用。

源码开发允许缺少 `deploy/appliance/agent-codex/echo-codex-bundle.json`，由运行时处理
开发依赖版本；这不表示 Codex 二进制已打包或验证。已有清单格式错误仍会报告错误，
正式制品验证规则保持独立。

开发入口默认启用本机免密模式，只绑定回环地址；生产部署使用下方发布路径。

开发模型配置首次复制使用私有暂存目录并在完整写入后发布；Windows 会校验当前用户 ACL，
POSIX 使用 `0700`/`0600`。目标已有配置不覆盖；暂存权限设置失败或文件系统不支持硬链接时
复制失败，不退化为暴露中间内容的复制方式。启动的 Python 子进程统一使用 UTF-8。

Windows 本地状态现在使用当前用户私有 ACL 和原生进程锁；POSIX 仍使用原有权限与
`flock`。本机已用隔离数据、静态 planner 和真实 CLI 完成两次启动，验证登录、照片
原图读取、四个设备相册工具注册与重启认证。此证据不包含真实模型调用或 Linux 存储
管理；Windows 当前进程沙箱仍只有软约束。阶段结果见[打磨记录](AGENT_OS_POLISH_STATUS.md)。

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
