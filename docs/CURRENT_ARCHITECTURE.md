# Echo OS 当前架构

核对日期：2026-09-06。本文描述源码结构和运行边界；真机交付程度以
[NAS 交付状态](NAS_DELIVERY_STATUS.md) 的证据为准。

最新逐链核验与适用 revision 见 [深度审计](PROJECT_DEEP_AUDIT_2026-09-05.md)，其中明确区分
历史证据、当前源码、未提交修复和各安装形态。

## 分层

```text
frontend/：React 桌面、Agent 工作台、Task Space、文件管家、照片
  ├─ Agent API / WebSocket → runtime/ 执行内核、记忆、任务与工具
  ├─ appliance API → 已挂载的设备扩展、会话、审批、审计与 provider
  │    ├─ agent_api/ → 内建 runtime（照片算法、任务状态等兼容接口）
  │    ├─ files/、photos/ → 受限数据访问与本地照片索引
  │    └─ native_storage.py、hub/ → 系统工具与受限应用控制
  ├─ /api/storage → runtime 的认证代理 → 可选 echo-storage 服务
  │                                      ↑ Agent storage 搜索工具
  └─ Electron IPC → 原生应用、窗口与系统设置控制
```

OS 与 Agent 使用同一个 `echo-os` Python distribution 和同源制品；不需要 sibling
Agent 仓库或第二套 WebUI。设备功能访问运行时必须通过 `appliance/agent_api/`，不能
读写运行时私有数据库或复制调度器。[工程边界](AGENT_OS_BOUNDARY.md)

## 运行形态与装配差异

| 形态                   | 入口和进程                                                                                                          | 安全与适用范围                                                                                                                                                                                                                                                                                                       |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 本地源码开发           | `frontend/scripts/dev-appliance-backend.mjs` 启动回环 Agent；Vite 提供 UI 并代理 API                                | 默认开发免密，仅用于本机；不是生产部署入口                                                                                                                                                                                                                                                                           |
| Docker appliance       | `deploy/appliance/docker-compose.yml` 与 `appliance/entrypoint.py`，加载 `appliance.extension`                      | 设备会话、单次高风险审批；Docker 控制通过受限代理                                                                                                                                                                                                                                                                    |
| netinst/provision 整机 | `deploy/provision/base/echo-appliance.service` 以 root 运行完整 appliance 扩展，回环后端经 nginx 提供服务           | 可以访问受支持宿主存储控制；权限边界不同于非 root native Agent                                                                                                                                                                                                                                                       |
| mkosi/raw 原生整机     | `deploy/agent/echo-agent.service` 以 echo 用户运行 `appliance/native_entrypoint.py`，加载完整 `appliance.extension` | OEM 口令绑定私有 Web 认证，存储写入经 root broker；核心 NAS 数据栈、共享防火墙、默认关闭且只读挂载的 ReadyMedia DLNA、Docker Engine、每设备 credential、回环降权 Hub 代理、目录/容器绑定的私网端口转发，以及逐内核构建、发布签名和 boot gate 的 OpenZFS 已进入装配源码。DLNA 已通过 Debian 13 QEMU 的 systemd/firewalld/SSDP/媒体流运行验证；候选级 Secure-Boot/ZFS、DLNA raw/异机播放、Docker/nftables、SDDM/PAM、A/B、Recovery、其他异机协议验证仍缺 |

独立 Electron 桌面安装包也有自己的 launcher 与用户状态目录。上述路径使用同仓运行时，
但服务身份、扩展列表与系统能力不同。不能以同一个前端构建判断每种镜像都具备相同后端。

默认 Docker 主进程降权，只挂载状态与 NAS 数据；它执行存储命令时看到容器环境，不能
据此推断宿主磁盘或系统账户可被管理。独立 Electron launcher 默认也没有注入完整
appliance 扩展。原生 Electron 已有 Wi-Fi、蓝牙、电源与更新等系统 IPC，不应因 native
扩展较窄而判定整机没有这些能力。各路径证据见[源码核验](PROJECT_REVIEW_2026-09-05.md)。

默认开发步骤见[开发接入](ECHO_AGENT_INTEGRATION.md)；NAS 安装见
[部署说明](../deploy/appliance/README.md)；原生构建见[镜像说明](../packaging/image/README.md)。

## 权威状态

| 状态                         | 唯一权威                                            |
| ---------------------------- | --------------------------------------------------- |
| 任务、租约、检查点、恢复执行 | Agent 运行时；Task Space 只投影                     |
| 能力是否存在及当前是否允许   | 已挂载 provider 与能力策略；能力发现不等于授权      |
| 高风险操作批准               | appliance 单次审批，绑定操作者、目标、动作与 intent |
| 文件、挂载、账户和服务状态   | 对应 provider 回读实际系统状态                      |
| UI 进度与产物                | 来自执行记录，不由动画或模型的完成声明推断          |

任务接管与恢复执行保持分离，不能因为重启或健康检查而自动恢复副作用。独立并发批次会把
聚合 host task 也登记到 TaskSupervisor；这让进程退出后的过期执行可见，但不等于批次内容
已经跨进程重建。
[任务契约](ECHO_TASK_PROJECTION.md)、[能力契约](ECHO_CAPABILITY_CONTRACT.md)

## 存储与可选能力

项目有三条已接线的本地数据路径：

| 路径                | 持久数据与消费者                                                                                           | 当前边界                                                                                                  |
| ------------------- | ---------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| Agent 状态          | 用户 JSON/Markdown 记忆、线程 JSONL、任务 JSON、工具副作用 SQLite、日志与 trace SQLite                     | 按职责存储。新发行模板选择持久日志；已有显式内存配置不会被覆盖。见[恢复策略](AGENT_PERSISTENCE_POLICY.md) |
| 文件管家/媒体工作台 | UI 和已注册 Agent storage 工具连接同一 echo-storage 服务；UI 经认证代理，服务令牌在后端注入                | 服务源码不在本仓，不能用接口推断其内部数据库或全套成员权限已验证                                          |
| 自有照片引擎        | runtime 的 SQLite 保存图片向量、人脸、元数据、OCR、分类与质量数据；appliance 照片服务经 agent_api 复用算法 | 通用 Agent 图片工具仍有独立索引；本轮新增的设备相册工具使用桌面同一服务、数据库和成员权限                 |

直接 Agent 图片工具已改为按规范化完整目录路径生成库标识，状态遵循 `ECHO_DATA_DIR` /
`ECHO_HOME` 合同。旧 basename 索引无法证明源目录，因此保留旧文件并要求在原目录重新建立，
不自动合并。人物命名保存人脸原型，数字分组编号只属于当前快照。具体回归与未完成项见
[本轮打磨记录](AGENT_OS_POLISH_STATUS.md)。

设备相册和通用 Agent 图片工具现在使用同一套部署内 `photo-library` source id；相册结果
附带由文件观测生成的 `assetId`/`assetRevision`，Agent 设备相册工具在 host execution
下还返回任务与线程坐标。source id 只用于跨界面关联和变更检测，不代表授权，也不暴露
宿主路径；设备相册、通用图片库和外部 `echo-storage` 的数据库仍分开。

相册构建现在按解码内容指纹与实际加载编码器身份复用向量；未知或旧身份保守重算。
后台任务日志保存运行/取消/终态，索引数据库在同一提交中保存精确任务回执。只有取得
写入租约之后，服务才能根据回执恢复遗留任务；无提交证据的中断不推断为成功。
取消为协作请求，当前原生推理调用返回后才检查，提交成功优先于晚到的取消请求。
这套相册任务状态不替代 Agent 通用任务运行时，也不是逐图片检查点续算。

通用任务由 TaskSupervisor 保持权威，Hub 应用操作另有受保护 SQLite 账本及有界 worker，
相册 job 也有领域状态。界面关联这些结果不应复制执行权威；“统一任务体验”不能被理解成
全产品已经只有一种队列或一个数据库。

完整 appliance 扩展在 NAS 文件根可用且取得运行时工具注册器时，注册 `photos_library`、
`photos_search`、`photos_status` 和 `photos_index_plan`。这些工具由 OS 装配并绑定现有
`PhotoLibraryService` 与账户授权实例，不接受模型提供的用户身份、数据库或宿主目录。
计划工具只预览；建立索引仍走照片面板的单次密码审批。通用 `image_*` 工具及外部
`echo-storage` 尚未因此成为同一个媒体库。新接线属于当前未提交工作区，具体安全回归与
恢复路径验证以打磨记录为准。

完整 appliance 的 documents ABI 可用时，文件管理器与可信 Agent 的
`files_organize_plan` / `files_organize_status` 共用 `FileOrganizationService`。
计划由服务端保存；实际移动与撤销分别使用独立单次审批，任务生命周期沿用 TaskSupervisor，
provider 保留逐文件条件与回执。该服务没有借用通用文本 rollback 来撤销文件 rename。
原件读取按计划条目重新核验目录、文件身份、内容和当前访问权限后返回字节，避免只传递旧路径。
当前实施与 Windows/HTTP/浏览器证据分别见[发票整理验证](DOCUMENT_ORGANIZATION_VERIFICATION_2026-09-05.md)，
不能外推为完整自然语言任务或 Linux 发行物已经验收。

NAS 文档预览另已接入固定解析 worker、OS 内存/CPU 限额、增量提取与每设备跨进程准入；
通用 Agent `read_file` 的 PDF 读取也在同一 worker 中执行，页数和显式页范围由 worker 回报并校验，
不再由服务进程直接打开 PDF。其他解析入口仍遵循各自的隔离边界，
超时/取消不使用部分文本，回收未确认时保留槽位。Windows 源码及最小冻结工作进程已联合验证；
Linux 原生、Linux onefile、macOS、阻塞原件 I/O 与其他解析入口仍有明确边界，详见
[解析隔离验证](DOCUMENT_EXTRACTION_VERIFICATION_2026-09-05.md)。这不是全运行时沙箱。

系统能力契约已有发现、策略与执行描述；当前 host 执行边界已在实时 ReAct、Codex、Project OS、
直接 subagent、team task/cowork worker、auto-parallel 的受管父 Session、legacy parallel task、
独立 parallel HTTP 和 deep-research HTTP worker 入口消费。独立批次另有聚合 TaskSupervisor
记录，终态与租约丢失都能留在任务恢复队列；进程重启后 recovery-snapshot 可从聚合记录生成
durable-only 的任务状态视图，`recovery-snapshots` 列表可按 owner/tenant 发现这些视图。对于
快照明确标为 `failed`、`cancelled`、`timed_out` 或未启动 `pending` 的任务，用户确认后可
直接从持久任务规格创建新批次；恢复过程会保留源批次、重新生成任务 ID，并拒绝重复选择。
内存批次重建和仍在运行任务的自动接管仍未实现。仍不能证明所有
OS 能力都贯通为统一的发现、授权、provider 执行及结果验证。不能据此断言 Agent 无法操作系统，
也不能把能力描述文件或已接入入口的局部回归视为全部端到端路径已完成。
deep-research 作业在同一情形下保留原工作流 `status`，并以 `recovery_required` 标出调度器
状态需要人工核查，避免把持久化的 `running` 误读为当前进程仍在执行。

并行工作台现在另有显式 `POST /api/agents/parallel/batch/{batch_id}/resume` 恢复动作：调用者
必须从快照中明确选择可安全重跑的 `failed`、`cancelled`、`timed_out` 或 `pending` 任务，服务端
会创建新的批次并保留源批次及任务映射。持久化为 `running` 的任务因副作用边界未知只能查看，不能
被该接口隐式重放；该动作已用替换 orchestrator 实例的回归覆盖，但仍不等同于跨进程调度器
重建、未知副作用任务接管或整机恢复。

当前原生存储源使用 `lsblk`、`df`、`smartctl`、ZFS/配额和系统工具；保留 OMV
兼容协议与历史桥不表示原生运行时仍依赖 OMV。已有写面限制在共享目录、账户、根目录
ACL、SMB/NFS、部分配额及受限双盘 ZFS mirror 等操作。
最新 HEAD 已有受限故障成员换盘、scrub 与 Echo 布局池导入/导出；复杂扩容、存储池删除
及强制恢复不能因 UI 入口存在而视为支持。本段经 `f9b3dd8` 至 `88f8370` 重新核验，具体
前置条件与限制以 provider 和交付状态为准。

`echo-storage` 索引、Docker 控制、设备连接等依赖有各自可用条件。不可用时应明确报告，
不能将模块文件存在等同于服务可用。Agent 的多机协调、自进化等可选机制也不能直接作为
默认设备配置的产品能力宣传。

## 证据规则

对外能力分别注明：源码已实现、本机测试通过、真实服务集成通过、VM 验证、物理机验证。
每条运行证据需要 revision、制品与环境；不同阶段、硬件和架构的结果不相互替代。

[architecture.md](architecture.md) 保留仿生架构历史；
[guide/architecture.md](guide/architecture.md) 保留运行时工程参考，设备层旧描述与本文冲突时以
当前源码及本文为准。后续路线见[优化方案](AGENT_OS_OPTIMIZATION_PLAN.md)。
