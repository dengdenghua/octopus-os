# Echo OS 再评价：从实际调用链判断项目价值

核验日期：2026-09-05。检查时 HEAD 从 `ae4daddf` 推进到 `3c124354622b3980af63ec105d552b0bac25b008`，工作区另有未提交改动。本文描述这些源码的实际组合，不代表同一已发布制品。核心文件哈希与本轮测试摘要保存在 [source-evidence.json](C:/飞牛os/octopus-os/tmp/project-reassessment-2026-09-05/source-evidence.json)。

**同日第二次深入复核：** 当前核对到 `31b5895236b0634ad461c74905ad1d8f2ff22048` 及未提交工作区。补查业务数据库、手机原图链、相册引擎与界面差别、创作插件、实时执行租约和竞品当前 AI 能力；六个定向测试文件 **152 passed / 53.05 秒**，核对的 12 个实现文件前后哈希一致。另用隔离 ASGI 集成复现了“租约拒绝后仍执行”的边界，见第 3、8 节。此处的新证据与文末保留的第一次测试记录分开，不累加为整机完成率。

本次沿启动装配、任务执行、本地数据、相册、权限、存储及系统交付追踪核心链路，并分别交叉复核。没有逐行读完全部源码，没有运行竞品设备，也没有完成当前候选的 Linux/物理机验收。历史审计和测试数量不替代本次证据。

**后续实施：** 第 3 节复现的实时执行所有权问题已有源码修复，并进一步修复审批接续、native 审批缺接线、损坏任务库回退导致旧身份复活，以及 Windows 中断身份读取。下文保留修复前证据，修复后的适用范围另行列出。Linux 文档解析已追加源码与最小冻结服务验证，仍不代表完整 OS 制品通过，见 [解析验证报告](DOCUMENT_EXTRACTION_VERIFICATION_2026-09-05.md)。

**判断：称为“面向个人设备与私有数据的 Agent OS”有实现依据；其产品成熟度应按交付形态和任务链评估。目前不足以定位为已交付的 GPU/多节点算力操作系统。**

## 1. 已有系统结构，以及每层真正负责的事

| 层 | 真实职责 | 直接源码依据 |
| --- | --- | --- |
| 用户入口 | 同一前端提供桌面、工作台、文件、相册、任务空间；Electron 另有原生 IPC | [desktop/page.tsx:99](C:/飞牛os/octopus-os/frontend/src/app/desktop/page.tsx:99)、[main.cjs:756](C:/飞牛os/octopus-os/frontend/electron/main.cjs:756) |
| Agent 执行 | 会话、规划、工具调用、执行策略、任务租约、检查点及恢复 | [TaskSupervisor](C:/飞牛os/octopus-os/runtime/platform/process/task_supervisor.py)、[ToolExecutor](C:/飞牛os/octopus-os/runtime/execution/tool_engine/executor.py) |
| 设备服务 | 账户、共享权限、单次审批、文件操作、相册、Docker 应用及原生存储 | [extension.py:280](C:/飞牛os/octopus-os/appliance/extension.py:280) |
| 数据持久化 | 记忆、线程、任务、执行回执、图片索引和领域操作记录，各自维护权威状态 | [state.py:61](C:/飞牛os/octopus-os/runtime/platform/ui/state.py:61)、[图像索引](C:/飞牛os/octopus-os/runtime/memory/hemolymph/image_semantic_index.py) |
| 系统交付 | Debian 预配置安装、mkosi 镜像、systemd、容器部署、更新和恢复 | [原生 Agent 服务](C:/飞牛os/octopus-os/deploy/agent/echo-agent.service)、[NAS 服务](C:/飞牛os/octopus-os/deploy/provision/base/echo-appliance.service)、[mkosi.conf](C:/飞牛os/octopus-os/packaging/image/mkosi.conf) |

`echo-os` 是同时包含 appliance 与 runtime 的 Python distribution。运行时不是外链聊天页面，设备层也不是只有桌面卡片。另一方面，运行时中的 Kernel 是 Agent 装配概念，Linux 继续负责驱动、内核、进程和文件系统。

项目的架构特点是自己管理“用户目标如何变成执行、如何保存状态、如何确认结果”，再与设备服务结合。这样的控制范围有价值，也意味着团队必须维护跨层的一致性，不能只验收页面或模块。

范围还包括项目与多人/多 Agent 协作、浏览器和计算机控制、移动端桥接、办公及创作插件。ProjectStore 在应用启动时创建，cowork 的事件、黑板、异步工作与协作记录会装配进运行时；它们不是相册缓存的别名。[项目装配](C:/飞牛os/octopus-os/runtime/platform/ui/_app_routers.py:119)、[协作装配](C:/飞牛os/octopus-os/runtime/platform/ui/_app_stack.py:403)

ProjectOS 的执行器在调用项目任务前先做 SQLite `claim_task`，模型 hook 和 cowork 桥接能进入子 Agent；计算机工具和浏览器工具也有真实动作适配，受桌面会话、浏览器桥接及策略约束。这些是已有业务执行体系，不能把 TaskSupervisor 描述成已经替代全部领域调度的唯一权威。[项目领取](C:/飞牛os/octopus-os/runtime/projectos/engine.py:742)、[子 Agent hook](C:/飞牛os/octopus-os/runtime/projectos/llm_hooks.py:260)、[计算机动作](C:/飞牛os/octopus-os/runtime/execution/suckers/computer_skills.py:182)

创作插件也有实际能力：剪辑工坊保存项目与撤销历史，暴露编辑、合成帧及导出工具；ComfyUI 桥接暴露工作流、依赖诊断、入队和结果读取。这让项目具备从私有素材走向内容生产的组合基础。但外部 ComfyUI 服务、编码依赖和完整成片验收必须单独验证，不能由插件名称推断全部可用。剪辑 readiness 本身明确区分依赖存在和 `runtimeVerified`。[剪辑实现](C:/飞牛os/octopus-os/runtime/platform/plugins/bundled/clip_studio/__init__.py:205)、[依赖事实](C:/飞牛os/octopus-os/runtime/platform/plugins/bundled/clip_studio/readiness.py:47)、[ComfyUI 工具注册](C:/飞牛os/octopus-os/runtime/platform/plugins/bundled/comfyui_bridge/__init__.py:46)

## 2. 本地数据库和相册：原本就存在，应准确区分其角色

| 持久数据 | 存储内容及用途 | 不应混淆的边界 |
| --- | --- | --- |
| 用户记忆 | JSON/Markdown 等本地事实与长期记忆，经 MemoryHub 进入上下文 | 不是所有记忆都在同一 SQLite 中 |
| 线程、任务与轨迹 | 线程 JSONL、任务 JSON、执行回执 SQLite；持久 journal 条件下的 trace/checkpoint | 聊天历史、任务状态和副作用完成证据不是同一数据 |
| 图像 SQLite | 向量、人脸、元数据、OCR、质量、感知哈希及分类相关记录 | 原图仍在文件系统；表存在也不意味着所有照片已完成相应识别 |
| 领域操作状态 | Hub 操作、手机同步、相册 job 等各自的本地记录 | 多库不等于重复调度，也不等于全产品已共用一个队列 |
| 外部 echo-storage | 本仓提供服务客户端、代理和文档搜索工具 | 其内部数据库和完整授权实现需要另查服务源码 |

前次表格对业务数据库概括过粗，具体已有以下本地状态。下表路径相对 runtime 的 `app_paths().data_dir`，不保证与 appliance 状态目录在所有安装形态中相同。

| 本地数据库 | 保存的业务对象 | 源码 |
| --- | --- | --- |
| `org.db`、`workspaces.db` | 组织、部门、频道；工作区及成员 | [组织](C:/飞牛os/octopus-os/runtime/workspace/org_store.py:59)、[工作区](C:/飞牛os/octopus-os/runtime/workspace/store.py:44) |
| `projectos/projectos.db` | 项目、里程碑、任务领取、线程绑定、事件 | [ProjectStore](C:/飞牛os/octopus-os/runtime/projectos/store.py:92) |
| `cowork/group_events.db`、`group_blackboard.db` | 群组事件和共享黑板 | [GroupStore](C:/飞牛os/octopus-os/runtime/memory/cowork/group_store.py:249) |
| `cowork/async_work.db`、`collaboration.db` | 异步工作、协作房间、任务与消息 | [异步工作](C:/飞牛os/octopus-os/runtime/memory/cowork/async_work.py:95)、[协作记录](C:/飞牛os/octopus-os/runtime/memory/cowork/collaboration_store.py:450) |
| `control_sessions/control_sessions.db` | 控制会话、动作、证据及事件 | [控制会话](C:/飞牛os/octopus-os/runtime/memory/control_sessions.py:43) |
| `teamroom/room_messages.db`、`team_invitations.db` | 有序消息、邀请与加入申请 | [消息](C:/飞牛os/octopus-os/runtime/memory/cowork/room_messages.py:55)、[邀请](C:/飞牛os/octopus-os/runtime/memory/cowork/_team_invitation_support.py:137) |

由这些对象可知，项目不仅保存“AI 记住了什么”，还保存“谁参与、在哪个项目工作、做到了哪一步、产生什么证据”。但每个领域的状态一致性、恢复和权限仍需各自验收；建了表不等于这些保证自动成立。

2026-09-06 起，认证的 OpenAI Agent 入口会从已验证 Principal 构造 `MemoryViewer`，聚合同租户
记忆分区，并在提示词注入前执行 private/team/restricted/agent 过滤；写入仍只进入当前
actor 分区。`team_ids` 仅接受身份目录的服务端元数据，不能由请求 body/header 指定。该接线
补上了本地记忆“有表但共享边界未生效”的缺口；对无 `tenant_id` 的旧默认事实，只有匹配
`legacy:<actor>` 且 owner 相同的身份保留读取兼容，显式租户不会把旧文件当成共享池。外部
echo-storage 的资源授权仍需其服务共同实现和真实 A/B/共享/deny 验收。

同一查看者合同已继续接入 `MemoryHub`、实时 ReAct/工具桥以及设置页的 memory search/assets/
trace。realtime WebSocket 只把服务端解析的角色和 `team_ids` 写入私有上下文，MemoryHub
聚合同租户分区后过滤；设置页的共享读取走同一过滤器，写入、修改和删除仍固定当前 actor
分区。这样多用户事实在 Agent、实时和管理 UI 的可见集合一致；未取得 echo-storage 服务
实现前，外部文件/资源授权仍不能据此推定。

原有 HEAD 的图像引擎已有 8 张表，并由 builtins 注册两组共 12 个图片相关工具。当前工作区增加的索引身份、人物命名、回执和权限接线，应作为新增工作单列；不能把原有数据库和图片能力说成最近才建设。

最关键的是三条相册路径：

1. **桌面设备相册**：`PhotosPanel → /api/appliance/photos → PhotoLibraryService → agent_api → runtime 图像引擎`。设备索引位于状态目录的 `media/image_index.db`。当前 `photos_*` 工具在 [extension.py:395](C:/飞牛os/octopus-os/appliance/extension.py:395) 绑定同一个服务，沿用设备成员授权。
2. **通用 Agent 图片工具**：复用图像算法，按规范化目录身份选择自己的 `image_libraries/.../index.db`。算法相同不代表默认查询桌面那一库。
3. **外部 Storage 工作台与 `/apps/photos` 媒体页面**：调用 `core/storage/api`，经 `/api/storage/v1/*` 访问可选 echo-storage。见 [媒体页面](C:/飞牛os/octopus-os/frontend/src/app/apps/media/page.tsx:106) 和 [代理装配:62](C:/飞牛os/octopus-os/runtime/platform/ui/_app_collab.py:62)。桌面设备 FileManager 则走 `/api/appliance/files`，与设备相册共用 NAS 根及家庭数据策略，不能把全部文件管理归给外部服务。[桌面 FileManager](C:/飞牛os/octopus-os/frontend/src/app/desktop/page.tsx:2624)、[文件 API](C:/飞牛os/octopus-os/frontend/src/appliance/files.ts:590)

因此用户关于“Agent 里面本来有本地数据库，包括相册”的判断正确。下一步应完善库身份、来源展示、权限和原件引用的一致性，复用现有引擎；没有理由重建一套相册，或者仅为追求“统一”而硬合并数据库。

手机同步也有真实原件链：上传到 NAS 的 `Mobile Uploads/<device>/Photos/...`，提交时核对文件及 SHA256，写入同步记录后失效相册扫描缓存。它并不自动完成 OCR、人脸及语义索引。因此当前链路应表达为“同步原件 → 相册扫描 → 审批建索引 → 检索”，不能把最后两步省略。[同步提交](C:/飞牛os/octopus-os/appliance/sync.py:580)、[相册缓存失效](C:/飞牛os/octopus-os/appliance/sync.py:626)

还要区别**引擎功能**和**设备界面功能**。图像引擎已有 OCR、人脸、类别、质量与重复图片相关算法；当前设备 router 提供浏览、状态、缩略图、原件、搜索、索引计划/执行/取消，尚未把人物纠错、类别训练和 OCR 管理全部做成设备相册流程。这是复用已有能力的接线与交互工作，不是重造算法。[设备相册路由](C:/飞牛os/octopus-os/appliance/photos/router.py:137)、[独立 OCR 调用](C:/飞牛os/octopus-os/runtime/memory/hemolymph/image_semantic_index.py:759)

设备相册的索引任务已有持久状态与提交回执，能区分提交成功和无完成证据的中断；仍不能称为逐图片断点续算。模型准备、CPU/GPU 性能、中文检索质量和大量照片的稳定性需分别测量。“本地保存”也不能自动推出全部模型调用都离线。

规模边界也可以直接从代码确认：设备相册默认扫描上限为 **20,000**，索引上限为 **4,000**；文字搜图取出授权候选向量，在 Python 中逐项算相似度并排序。这是有界、可工作的实现，但不能直接承诺十万张或百万张图库的交互延迟。应先测实际候选规模、内存与冷/热检索耗时，再决定是否需要向量索引或批处理优化。[service.py:43](C:/飞牛os/octopus-os/appliance/photos/service.py:43)、[image_semantic_index.py:351](C:/飞牛os/octopus-os/runtime/memory/hemolymph/image_semantic_index.py:351)

## 3. 两条具体链路体现项目价值，也暴露接线边界

**查照片。** 当前设备 `photos_search` 从可信运行上下文取得用户授权，再查询桌面同一服务；它不让模型自行指定身份或数据库。相关测试覆盖撤权后丢弃结果、重复工具调用重新检查授权，以及未建语义索引时如实降为文件名搜索。[photo_tools.py](C:/飞牛os/octopus-os/appliance/photo_tools.py)、[test_photo_tools.py:189](C:/飞牛os/octopus-os/tests/appliance/test_photo_tools.py:189)

**整理票据并撤销。** 当前未提交实现中，UI 与可信 Agent 的计划/状态工具共用 FileOrganizationService。服务保存计划，执行和撤销分别消费单次审批，任务进入 TaskSupervisor；逐文件回执、冲突和实际结果由 provider 维护。即使移动完成，只要任务、审计或回执尚未完整记录，`finalizationPending` 仍阻止整体报成功。[organization.py:66](C:/飞牛os/octopus-os/appliance/files/organization.py:66)、[organization_router.py:50](C:/飞牛os/octopus-os/appliance/files/organization_router.py:50)、[file_tasks.py:97](C:/飞牛os/octopus-os/appliance/agent_api/file_tasks.py:97)

这说明系统已经在处理文件身份、外部状态变化、部分成功和恢复等实际问题。任务回执及特定操作的反向执行有明确工程价值。但这条专用链路不能证明“任意自然语言任务都能自动规划并安全撤销”，计划/状态工具也不能等同于 Agent 已获任意执行权。

OS Capability API 的 `/decisions` 目前给出策略判断、执行描述和审批要求，真正的副作用仍由具体 provider 路由负责。本次未找到一个把所有 OS 能力接成自然语言执行的通用消费端。能力目录是必要基础，不能用目录条数作为任务完成率。[capabilities/router.py:103](C:/飞牛os/octopus-os/appliance/capabilities/router.py:103)

执行可靠性还要按分支说明：ToolExecutor 此处的持久回执入口要求 `caller == "react_loop"`；自动 ReAct 检查点默认每 10 次迭代写一次，写入异常会被抑制。已有恢复机制，仍不足以保证所有工具后端都防重复、每一步均可靠持久化或断电后自动完整续跑。[executor.py:717](C:/飞牛os/octopus-os/runtime/execution/tool_engine/executor.py:717)、[react_checkpointing.py:28](C:/飞牛os/octopus-os/runtime/core/cerebrum/react_checkpointing.py:28)

第二次沿调用者确认，native 工具分支传的是 `caller="agentic"`，上述回执分支不覆盖它，且 handler 仍可按瞬时错误重试。通用限时 handler 使用线程池，超时会停止等待，不能强杀已经运行的线程；近期专用文档 worker 的退出确认不覆盖这一通用路径。损坏检查点还存在记录警告后转为 fresh run 的分支。故优化需要分别处理“执行所有权”“可能仍在进行的操作”“不确定副作用后的重试”和“恢复变成重跑”，不能简单调高超时或增加检查点频率。[native 调用](C:/飞牛os/octopus-os/runtime/execution/tool_engine/native_tool_execution.py:77)、[重试选择](C:/飞牛os/octopus-os/runtime/execution/tool_engine/executor.py:828)、[限时线程](C:/飞牛os/octopus-os/runtime/execution/tool_engine/_executor_helpers.py:184)、[恢复降级](C:/飞牛os/octopus-os/runtime/core/cerebrum/react_resume.py:498)

**修复前的第二次复核：实时 ReAct 的任务租约尚不是统一的执行前置门。** 当时消费 `react_started` 时才调用 `start_task`，失败仅记录 warning；事件生产与消费并行，后续心跳有失租取消，但不能替代首次操作前的租约确认。对应旧源码哈希保存在以下复现记录，当前实现已改动。

隔离复现中，两个真实 Supervisor 共用临时 JSON，原 holder 保有 600 秒有效租约。另一 holder 收到 `TaskLeaseConflict` 后，真实 ToolExecutor 仍调用测试写入处理器，临时文件落盘，前端协议收到 `turn/completed`，原租约保持不变。仅用固定事件生成器替代模型循环，未使用真实模型或用户文件，也不是整机双进程测试。它证明当前这些层之间缺少强制阻断，不能推广为所有路径必然双执行；专用文件 provider 的单独租约和审批也不能被这一结果否定。[复现脚本](C:/飞牛os/octopus-os/tmp/project-reassessment-2026-09-05/realtime-lease-audit/audit.py)、[顺序与源文件哈希](C:/飞牛os/octopus-os/tmp/project-reassessment-2026-09-05/realtime-lease-audit/result.json)

这项应优先于增加任务面板或另建调度器：复用已有 Supervisor，在启动失败时禁止继续执行，并在副作用前验证任务所有权；需要测试冲突、过期接管、取消和恢复。领域回执继续负责解释实际外部结果，不能让 UI 的 completed 取代它。

### 执行链的后续修复与仍需区分的边界

配置了 Supervisor 的实时 ReAct/native 路径现在先取得持久租约，再推进模型循环。服务器 Session 传递冻结的 holder、token 与随机 incarnation；工具调用前、续租及结算都检查该身份。同一进程重新取得同一任务、甚至删除任务库后计数重新从 1 开始，也不能让旧执行身份重新有效。自动验证的第二轮 driver 复用同一身份但使用新 scope，已关闭的旧 scope 不重新开放。[执行身份](C:/飞牛os/octopus-os/runtime/platform/process/task_execution.py)、[实际调用前检查](C:/飞牛os/octopus-os/runtime/execution/tool_engine/executor.py)、[实时驱动](C:/飞牛os/octopus-os/runtime/sensing/gateway/_realtime_react_stream_drive.py)

持久 HOLD 保留 WAITING_APPROVAL；批准与结算相竞时不覆盖批准结果。审批后的继续执行通过服务器保存、原子消费的一次性许可换发新租约，并检查 owner/thread。native 分支补接实际审批提供器，未获批准不执行，拒绝结果不能被后续模型措辞改成完成；取消也清理待答复请求。[native 审批](C:/飞牛os/octopus-os/runtime/execution/tool_engine/_native_tool_approval.py)、[Supervisor 状态操作](C:/飞牛os/octopus-os/runtime/platform/process/task_supervisor.py)

故障测试还发现，旧逻辑对损坏任务 JSON 自动回退到空库或旧备份，会重发 token 或重新认可旧租约。当前主记录无效时明确拒绝读写，不自动把备份变成执行权威，并保留原件；合法旧版本租约仍可由兼容 API 读取，新执行身份总是重新签发。**这是任务库自动恢复语义的变化。** 显式恢复应先停止旧执行，再以新身份接续；本轮没有实现或验证“在线覆盖任意旧快照继续执行”。[修复前故障证据](C:/飞牛os/octopus-os/tmp/task-lease-execution-audit/corrupt-g0o8rxcu/result.json)、[修复后真实文件验证](C:/飞牛os/octopus-os/tmp/task-lease-execution-audit/corrupt-a0o28mv9/result.json)

这些改动不证明所有执行入口都已统一调度，也不能撤销 handler 已开始的外部 I/O。native 的副作用回执覆盖、线程超时后的不确定结果、损坏检查点转 fresh run、实际模型任务成功率和跨设备恢复仍需单独处理。这里的强制检查范围是受管实时执行及继承其 Session 的工具调用，不替代 ProjectOS 或设备 provider 自己的操作权威。

## 4. 一个需要优先处理的具体数据权限边界

外部 Storage 代理会验证登录，但 `_auth` 不保留 `_resolve_actor` 的返回身份；向上游发送的是服务级 token，query/body 被转发。该代理没有应用设备相册使用的 DataAccessScope，也没有在其转发合同中绑定可信成员范围。[storage_proxy_router.py:45](C:/飞牛os/octopus-os/runtime/sensing/gateway/storage_proxy_router.py:45)、[storage_proxy_router.py:76](C:/飞牛os/octopus-os/runtime/sensing/gateway/storage_proxy_router.py:76)

通用 `search_documents` 同样使用服务 token，`source_ids` 来自工具参数。[storage_skills.py:125](C:/飞牛os/octopus-os/runtime/execution/suckers/storage_skills.py:125)

**可以确认的是：本仓代理层的入口认证，不足以证明外部 Storage 的资源级家庭成员隔离。** 外部服务不在本仓，本次没有证实实际跨用户泄露；也不能把设备相册已经验证的权限保证直接套到外部文件管家。家庭多用户发布前，应先明确服务是单用户专用实例，还是具备可验证成员授权的共享实例，再用两名不同权限用户验证查询、原件与写操作。

这是比“加一个统一数据库”更具体、更优先的整合问题。

## 5. 四种交付形态必须分开评价

| 形态 | 实际装配 | 评价时必须保留的区别 |
| --- | --- | --- |
| Debian netinst NAS | firstboot 安装存储服务、Docker、前后端；root 运行完整 appliance；nginx 对外 | 有宿主存储操作条件，但整个后端的权限边界更大；不自动具有 mkosi 的 A/B 布局 |
| mkosi 原生整机 | Debian trixie、systemd-boot/UKI/verity；普通 echo 用户运行 Agent，加载 native_extension | 扩展复用公共前端、发布配置和任务接口；NAS 全部接口不会自动存在；原生系统控制另经 Electron/helper |
| Docker appliance | 完整 appliance、绑定 NAS 目录、降权主进程及 Docker 控制代理 | 容器命名空间里的磁盘/用户观察不能作为宿主全盘管理证明 |
| 独立 Electron | 桌面程序启动自身冻结后端，或原生会话复用 systemd 后端 | 安装桌面程序不等于安装启动器、分区、磁盘服务和整机更新链 |

直接依据：[netinst 服务:11](C:/飞牛os/octopus-os/deploy/provision/base/echo-appliance.service:11)、[原生服务:11](C:/飞牛os/octopus-os/deploy/agent/echo-agent.service:11)、[native_extension:17](C:/飞牛os/octopus-os/appliance/native_extension.py:17)、[容器配置:130](C:/飞牛os/octopus-os/deploy/appliance/docker-compose.yml:130)、[Electron 启动选择:1610](C:/飞牛os/octopus-os/frontend/electron/main.cjs:1610)。

原生存储已经由 `NativeStorageAuthority` 装配，旧 `/api/appliance/omv/*` 是原生兼容别名，不足以证明运行时仍依赖 OMV。当前原生源码已有共享、账户、SMB/NFS、ACL、配额，以及限定范围的 ext4、ZFS、md RAID1、Btrfs RAID1 与维护动作；不能扩大理解为任意扩容、销毁或损坏池强制恢复。[native_storage.py:1](C:/飞牛os/octopus-os/appliance/native_storage.py:1)

第二次核验的 HEAD 已包含 Btrfs 缺失成员替换控制。因此不能再概括为“没有换盘能力”；但接受替换、校验当前命令状态，与重建结束、重启后数据完整性通过，是不同验收阶段。[实际 replace 调用](C:/飞牛os/octopus-os/appliance/native_btrfs_replace.py:553)、[分阶段 lab](C:/飞牛os/octopus-os/deploy/appliance/btrfs_replacement_lab.py:425)

mkosi 更新确有验签和 sysupdate 实现，默认配置却仍为 `Sign=no`、`SecureBoot=no`；更新签名、dm-verity 和 Secure Boot 是不同保证。脚本存在不能当成当前制品故障矩阵已经通过。[echo-os-update:195](C:/飞牛os/octopus-os/deploy/update/echo-os-update:195)、[mkosi.conf:334](C:/飞牛os/octopus-os/packaging/image/mkosi.conf:334)

备份也分为设备状态、NAS 用户数据及原生用户目录等路径。状态备份明确排除 NAS 原件，因此“数据库恢复成功”不代表“照片和文件均已恢复”。恢复验收必须同时核对原件、索引、身份、密钥、路径映射与服务可读性。[state_backup.py:141](C:/飞牛os/octopus-os/appliance/state_backup.py:141)

## 6. 与飞牛、绿联和 Omarchy 的公平比较

以下是截至核验时的官方公开能力，不是本轮上机性能或可靠性排名。

| 对象 | 已公开的相关能力 | Echo 应比较的重点 |
| --- | --- | --- |
| 飞牛 fnOS | NAS 数据/媒体服务；本地人脸识别、语义搜图、分类、视频增强识别；移动与远程入口；官方 OpenClaw 接入教程 | 手机备份→扫描/索引→检索→纠错→恢复的完整链路。仅有本地 CLIP、数据库或能安装 Agent 不足以构成区别 |
| 绿联 UGOS Pro | 存储、备份、应用、AI 搜索/问答，以及 OpenClaw、Hermes Agent 应用；部分 AI 功能依型号 | 同一用户目标能否贯穿数据与应用，权限是否持续有效，失败后能否回读与恢复。不能说绿联没有 Agent |
| Omarchy | 完整桌面使用流程，按需 Agent CLI、默认 Agent、用量面板、崩溃诊断和本地模型入口 | 安装、日常桌面使用、Agent 配置及维护流程的一致性。不能仅评价成主题或预装工具集合 |
| Echo 当前代码 | 自有 Agent 运行时和持久状态、设备数据服务、原生 NAS 控制、领域审批/回执、多个系统交付入口 | 特点在于能控制任务执行与设备服务之间的关系；是否转化为用户优势仍须真实任务对比 |

绿联 2026-08-21 的模型管理指南还明确描述了语义索引供全局搜索、文件管理与 Uliya 复用，以及模型启停、学习时段和智能整理；部分能力限 iDX 系列。故“本地数据＋自然语言＋跨应用”本身也不足以声称 Echo 独有。Echo 要证明的优势应具体落在授权对象、执行控制、结果回读和失败恢复上。[绿联模型管理指南](https://www.ugnas.com/play-detail/id-139.html)

来源：[飞牛 AI 相册](https://help.fnnas.com/articles/v1/photo/photo-ai)、[飞牛官方 AI 与 OpenClaw 帮助](https://help.fnnas.com/articles/v1/ai)、[UGOS Pro 系统说明与型号限制](https://support.ugnas.com/detail/article/zh-CN/772)、[Omarchy AI 手册](https://omarchy.org/manual/ai/)。这些页面不证明各产品实际成功率、安全性排名或大库性能。

若评估 Echo 作为 NAS 替代品，应优先评估数据生命周期和硬件适配；若评估个人 Agent 工作站，应优先评估任务完成率和可控性。两套评价轴不能用一个模糊“完成度”分数合并。

“算力 Agent OS”若只是指用本地模型完成工作，项目已有相关基础；若指统一 GPU 容量分配、任务配额、节点调度和故障迁移，则尚无足够交付证据。硬件适配建议、模型路由、多 Agent 并发和 Kubernetes YAML 并不等同于上述能力。[hwfit.py:220](C:/飞牛os/octopus-os/runtime/sensing/model_router/hwfit.py:220)、[Kubernetes 部署](C:/飞牛os/octopus-os/deploy/k8s/deployment.yaml)

## 7. 从上述事实推导的优化顺序

1. **明确主要交付形态。** 选择实际要交付的 NAS 或原生桌面基线；继续保留其他形态，但界面按已挂载 provider、服务身份和依赖展示能力。验收：干净安装后看到的功能与实际可用接口一致。
2. **完善数据来源与权限合同。** 给相册、通用图片库和 Storage 资源统一的来源标识、原件引用和授权检查方式；优先解决外部 Storage 的成员边界。保持各库按职责分工。验收：同一人从 UI/Agent 查询得到同一授权集合，撤权后重复查询和原件读取均失效。
3. **执行前检查与领域结果同时可信。** 第 3 节的受管实时执行检查已补强，继续沿现有 TaskSupervisor 关联 provider job、计划、审批、回执和原件；保留 ProjectOS claim、相册/Hub 领域状态的权威，不另建总调度器。验收仍包括：租约冲突后零新增副作用，刷新、重启、部分失败之后同一任务仍能说明实际结果。
4. **用少数完整任务证明产品差异。** 固定“找授权照片并打开原件”“整理票据并撤销”“诊断备份失败并恢复文件”等真实样本。每次同时验文件内容、权限和任务记录，记录模型版本、失败与人工纠偏；不能仅凭工具单测宣称自然语言任务已完成。
5. **把发布证据绑定同一候选。** 单测、真实服务、VM 和物理机结果各自标明版本与配置。优先验证安装、升级中断、换盘、权限撤销、断网、备份恢复和持续运行；通过一个平台不扩大为全部安装形态和架构通过。

## 8. 本轮实际验证与限制

**执行链修复后的联合回归：** 在 `9e35adf2c3eb7cb84a8015bfcd24cc5fa95aaf1c` 加未提交改动上，Windows 源码测试 **892 passed，186.39 秒，零失败/跳过**。所记录的 66 个实现与测试文件前后 SHA256 一致，运行期间 HEAD 未变。范围包含全部 realtime_cerebrum、任务执行身份/监督器/恢复 API、线程锁、ReAct 与 native 桥、子 Agent Session、回执、ProjectOS 和设备任务投影。记录的完整命令、日志、JUnit 和源码哈希见 [结果](C:/飞牛os/octopus-os/tmp/task-lease-verification-d306be6bbe/result.json)。其中 36 项实际 Gateway/模型协议/ToolExecutor 回归以脚本模型替代供应商，真实执行临时文件操作、JSON-RPC 审批和恢复；不代表真实模型成功率，也不是全仓测试或整机验收。

Linux 文档解析另有 **96 passed / 12 个 Windows 专属跳过**，真实最小 onefile 服务父的正常 PDF、超时及父突然退出三场景通过；与上述 Windows 运行分开统计。[Linux 证据](C:/飞牛os/octopus-os/tmp/linux-document-audit/frozen-linux-4wp2nqj1/result.json)。旧 Windows 冻结 EXE 仍是上一版传输源码制品，不将它称为包含最新 Linux 分支的候选。

**第二次复核的新增验证：** `test_photo_tools.py`、`test_image_library_tools.py`、`test_task_supervisor.py`、`test_storage_proxy_router.py`、`test_tool_effect_receipts.py`、`test_projectos.py` 共 **152 passed，53.05 秒，无跳过/失败**。这验证各自现有测试合同，不证明真实模型规划、大图库性能或外部 Storage 服务授权。源文件前后校验和日志保存在 [结果目录](C:/飞牛os/octopus-os/tmp/project-reassessment-followup-07bbb7790a/result.json)。实时租约复现单独记录为已确认的整合问题，没有把“成功复现缺陷”算作产品通过。

以下为第一次复核的历史记录，保留其适用版本与失败，不以新测试覆盖或抹除。

重新运行以下 6 个测试文件：`test_extension.py`、`test_entrypoint.py`、`test_native_agent.py`、`test_photo_tools.py`、`test_file_organization_tools.py`、`test_task_projection.py`。

结果为 **118 passed、5 failed，36.15 秒**。5 个失败均发生在 Windows 执行 Linux 入口测试：4 个在 fixture 设置时遇到 `os.geteuid` 不存在，1 个 POSIX `0600` mode 断言不成立。本轮没有修改或跳过这些测试，也没有把它们解释成 Linux 运行失败或验证通过。

审计转向前已在进行的文档解析工作，仅收口两处已发现的父进程边界修正：未标记截断的结果不得超出文本上限；验证工作进程身份后才发布 limits-ready 元数据。该定向组另有 54 passed、2 个 Linux 跳过；与上述 123 项不是一次全量运行，不相加成整机质量分数。

相册算法全功能、大图库性能、当前候选的真实存储硬件寿命周期、所有交付形态与竞品同机横评仍不在本轮已验证范围。仓库中已有 VM 工作和历史真实服务证据，不能说“完全没在 Linux 跑过”；也不能把没有绑定当前候选的旧截图或日志当成本轮发布验收。

## 9. 2026-09-06 执行链与交付门补充

后续实现已把上文指出的 native 回执缺口收口到当前受管路径：`react_loop` 与 `agentic` 均由
服务器生成封存的效果分类；未知副作用、限时线程超时和损坏 JSONL 恢复均停止自动续跑；同一
native 任务的步骤号由持久序列分配，确认恢复意图时不会用新步骤号掩盖旧未决效果。当前工作区
对应的执行/恢复联合回归为 **878 passed**，效果存储与步骤序列为 **80 passed**，日志与恢复
底层为 **111 passed**；这些数字是源码回归，不是自然语言任务成功率。

前端按项目过滤规则的第二次全量 Vitest 曾为 **447 个文件通过、3262 passed、2 skipped**。
2026-09-06 将 Electron/脚本 Node 测试排除固化到 `frontend/vite.config.ts`，并将 jsdom
默认 worker 上限设为 4（可用 `ECHO_VITEST_MAX_WORKERS` 调整）；按默认配置复测为
**447 个文件、3268 passed、2 skipped，206.80 秒**，脚本测试 **18 passed**，TypeScript、
ESLint、Prettier 和 Vite 生产构建通过。此前账户、文件组织/OMV 和 Mermaid 的 10 项全量等待
失败在单文件及受控 worker 复测中均通过，归因为测试资源争用；这只说明前端回归执行已稳定，
不替代 Linux/真机和真实任务验收。

公开源码门清单已补入四个 Btrfs 功能回归文件；在当前 Windows 开发机执行时，因 `fcntl`、
`grp` 和 Unix socket 专属类型不可导入而停在收集阶段。门禁设计目标是 Linux CI runner，
Windows 上的 POSIX 权限/进程语义失败不计为 Linux 发布证据。故本项目目前是“核心执行安全
链已明显收口、发布与真实任务验收仍未完成”，不能把当前回归数字写成整机完成度。

本轮对 D:\\echo agent 的继续吸收集中在审批和事件可靠性：服务端把
`default/plan/acceptEdits/bypassPermissions` 规范化，`acceptEdits` 使用独立 reviewer，
reviewer 超时或不可用时拒绝；桌面显式开关才允许 Codex 当前线程使用
`approval_policy=never`、`danger-full-access` 和网络，sidecar 状态目录仍为 deny。高频
App Server delta 与 token 快照通过有界队列合并，真实超载会以可恢复的 backpressure 错误
结束并保留已完成步骤。这个增量直接提升“Agent 能否安全地把一次任务做完和接着做”的
可观测性，比复制 D 盘 UI 或第二套数据库更适合当前 OS；仍需真实 Codex 版本、Linux
硬沙箱和长时模型任务验证。
