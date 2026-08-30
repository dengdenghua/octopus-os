# appliance/ — Echo OS 专属层

OS(NAS 桌面)形态的专属代码,与 runtime/ 内核分离以最小化与母体的合并冲突面
(策略见 docs/ECHO_OS_PLAN.md §4)。

## app_registry — 桌面启动器的数据源

Docker 容器 → 启动器应用卡片。

**启用**:`ECHO_APPLIANCE=1 python -m runtime serve …`(不开此开关时母体行为零变化);
依赖装 `pip install -e ".[appliance]"`(或 `uv sync --extra appliance`)。

**API**:
- `GET  /api/appliance/apps` — 应用列表;Docker 不可达时 `{available:false}` 优雅降级
- `POST /api/appliance/apps/{id}/start` / `…/stop`

**容器元数据 label 级联**(自有规范优先,兼容主流生态):

| 字段 | label 优先级 |
|---|---|
| 名称 | `sh.echo.name` → `casaos.name` → `homepage.name` → OCI title → 容器名 |
| 图标 | `sh.echo.icon` → `casaos.icon` → `icon` → `homepage.icon` → unraid |
| Web 入口 | `sh.echo.webui` → `casaos.webui` → `homepage.href` → unraid → 端口启发式(80/443/3000/8080/8096…) |
| 隐藏 | `sh.echo.hide: "1"` |

后端只投影不含凭据和控制字符的 HTTP(S) Web UI label；前端不会信任 label 中的主机名，
而是统一用 `window.location.hostname` 重建入口（只有浏览器知道用户经由哪个地址访问 NAS）。
外部站点、危险协议和畸形端口不会成为启动目标；有安全发布端口时退回端口入口。

**前端**:`frontend/src/appliance/apps.ts`(类型 + fetch + 30s 轮询 hook);
全部可启动应用进入启动台和 Spotlight，Dock 的“本地应用”段只展示前六项，避免应用较多时
撑坏 Dock；第七项以后仍可从应用库或搜索打开。API 不可用时安全降级为空列表。
Hub 的已安装卡以“打开/启动”为主操作；运行中的应用可从同一卡片整组安全重启或停止，卸载降为
次级动作。Hub 启动、停止和重启不再借用公开入口容器的单容器控制：sidecar 先用受信目录复核完整
服务集合，按依赖顺序启动、按反向顺序停止，失败时恢复操作前的运行集合。所有动作均复用管理员
密码复核、一次性审批票据、后台任务账本与审计链，并明确保留配置卷和 NAS 数据。
受信目录为每个应用提供有界版本号；新安装和更新的受管容器同时写入该版本标签。Hub 卡片会显示
当前版本，存在更新时显示“当前 → 目录版本”；旧容器的缺失、畸形或超长标签只显示“版本待识别”。
Hub 还从同一目录投影即时生成“已安装”和“可更新”计数/筛选，不为商城状态再建一份数据库。
每张设备应用卡还可打开单层详情浮板；浮板通过已登录的 `GET /api/appliance/hub/apps/{app_id}`
重新核对当前目录与运行状态，用普通语言说明设备架构、固定公开端口、隔离/局域网发现方式、每个
持久卷的只读/读写范围、更新快照与卸载保留策略，以及不可变镜像、禁止提权和无硬件直通边界。
详情失败只影响该浮板，不会清空商城目录或误把缓存内容当成当前安装计划。
已安装应用的详情还会通过 `docker-control` 的窄化 `echo.hub.runtime.v1` 合同显示健康、受管服务数、
CPU、内存、进程与重启次数。sidecar 先按受信目录核对完整标签、固定容器名和多服务集合，再读取
one-shot stats；重复、伪造或归属不完整时只返回“暂不可读”。原始 inspect/stats、日志、环境变量、
挂载路径、网络地址和应用内容均不进入主进程或浏览器。
同一详情响应还从脱敏运行合同派生 `echo.hub.diagnostics.v1`：只返回 OOM、健康检查失败、重启循环、
异常退出、部分服务停止或不可自动控制等固定故障码、服务 ID、严重度和恢复类型。商城用固定中文说明
展示并提供整组安全重启；诊断合同不接受自由文本、Docker 错误或原始日志。
安装前资源预检使用同一受信合同和实时 Docker/NAS 快照：逐端口标明可用、当前应用占用或冲突，
显示服务数、系统强制的内存/进程/共享内存上限、配置卷和更新快照数量，并只读报告当前 NAS
总量/剩余量。Jellyfin 与 Navidrome 的单容器合同也已补齐固定运行上限，安装器实际把这些上限
写入容器配置。九个应用的受信目录现按 amd64/arm64 绑定不可变 OCI 摘要的去重分层下载字节数
和 blob 数；正式发布会重新查询 registry 并逐项核对，数据过期即停止发布。`docker-control` 只读
观察配置的 Docker 数据根，并先验证它与 Engine 自报路径一致，只向主进程返回总量/余量而不暴露
宿主路径。安装按“下载量三倍或额外 512 MiB，取较大值”保守预留解压与元数据空间；数据根不可
核对、挂载不匹配或余量不足都会阻断计划，Docker 拉取仍执行最终校验。安装密码确认页会再次展示。

**安全注**:生产 compose 由独立 `docker-control` sidecar 持有宿主 socket；Echo/Agent
主进程只可调用通用应用 list/start/stop、按应用 ID 查询的脱敏运行健康，以及“应用 ID + 计划 ID +
目录摘要”的 Hub 安装、更新、卸载、整组启动、整组停止和安全重启入口；代理会
独立加载同一受信目录并重算计划。任意 Docker API 代理、原始 create/exec/delete 均不存在，
Echo 主容器与代理自身也受标签保护、不可被启动器停止。应用启停和物理清空回收站已进入
管理员密码复核、单次 intent 绑定令牌与 Agent HMAC 审计链；链异常时受控修改 fail closed；
管理员密码轮换和全会话退出也进入同一门，旧 JWT、旧密码和旧审批票据立即失效；Hub 安装
还要求不可变镜像、固定端口/卷、计划无漂移及代理侧二次校验；升级和卸载也使用独立计划、
一次性审批与写后状态核对，并保留应用配置和 NAS 数据。

设备控制面固定只接受 `local:admin`：OMV 健康/拓扑/用户共享管理、设备连接、容器启停、Hub
设备应用生命周期以及管理员密码轮换均不会因家庭成员知道管理员复核密码而越权。家庭成员仍可
打开已运行的家庭应用、浏览 Hub、连接自己名下的 Agent 账户凭据，并只看到自己的 Echo 账号记录。
管理员可把已有普通 OMV 用户开通为独立 Echo 登录，也可停用、重新启用或单独重置其 Echo 密码；
每次变更只吊销该成员旧 Cookie/Bearer/WebSocket，会话不会连带踢出其他家庭成员，也不会修改
OMV 密码。

桌面商城的安装、更新、卸载、启动、停止和安全重启共用 Echo 自有的 `hub-operations.sqlite3` 后台
任务账本；它不打开、
附加或复制 Agent 数据库。浏览器提交审批后即可关闭，重新打开仍能看到排队、执行、成功、失败或
进程重启中断状态；同一应用同一时间只允许一个生命周期任务。任务不伪造下载百分比，失败只返回
有界错误码和恢复动作。`docker-control` 通过内部 NDJSON 流只上报枚举阶段、当前/总镜像数和
Docker 实际完成的镜像层数；镜像名、层 ID、registry 原始状态与错误不会进入主进程。多容器更新
还会报告资源创建、停止旧服务、数据卷快照、启动、健康检查、切换与回滚阶段。进度只在计数变化
时持久化，避免把 Docker 高频下载事件变成 SQLite 写放大。安装结果使用由设备认证秘密派生的
AES-GCM 密钥加密；一次性初始凭据不
进入任务列表，经独立领取接口返回一次后会立即从账本密文中擦除。

审计链支持经管理员密码复核的签名密钥轮换，历史 key ID 保留、秘密不落密钥环；
`GET /api/appliance/audit/anchor` 以 Ed25519 签名绑定日志、检查点、密钥环哈希与尾记录。
`python -m appliance.audit_evidence` 可把这些最小证据制成 scrypt + AES-256-GCM 加密包，固定
设备公钥指纹后可在设备外独立复核。宿主编排、外置保留策略与 systemd timer 见部署 README；
保留策略只处理外部加密包，绝不裁剪实时审计链。

设备状态（认证、审计、Agent 记忆/运行状态）的离线备份入口为
`python -m appliance.state_backup`。它使用 AES-256-GCM 加密、运行时独占锁和安全归档校验，
明确排除 `ECHO_NAS_ROOT`；恢复只创建新目录，NAS 用户文件需由存储底座单独备份。
`python -m appliance.state_recovery` 会只读验证暂存恢复的当前 schema、管理员凭据、私有权限、
审计链和设备签名身份；宿主 `restore-state.sh` 在摘要确认后负责原子晋级、启动后复核和失败目录
回滚，旧状态及失败状态均默认保留，不做隐式删除或内容合并。
运行时还会在独占锁内维护 `echo-state-schema.json`：旧式无标记目录按显式 `0 → 1 → 2` 迁移，
状态版本高于当前程序时拒绝启动，防止旧镜像把新数据静默写坏；备份清单携带该 schema 版本。

## hub / photos — 设备应用与本地智能相册

Echo Hub 将设备应用和 Agent 能力分开投影：设备应用来自 OS 自带、大小受限的受信 JSON 目录；
安装计划只接受固定架构、不可变镜像摘要、端口和卷，执行时由 Docker sidecar 再加载同一目录并
重算计划。当前 Jellyfin、Navidrome、Syncthing、Nextcloud、Immich、Open WebUI、qBittorrent、
Paperless-ngx 与 Home Assistant 已具备真实安装合同。Agent 插件与技能不复制数据库或安装器；Echo
后端在自身会话边界内调用 Agent 既有目录抽象，以 `echo.agent-assets.v6` 只读投影目录、生命周期和权限授权状态。
Hub 会区分工作台、插件、连接器与技能，并显示启用、停用、可更新、异常、可回滚及有界恢复点数量；
工作台、普通插件和连接器均投影 Agent 已有的包完整性、发布者签名和 `host_api`
兼容结果；v5 还投影受限的权限、认证方式、包依赖、随包运行依赖与外部连接器。普通包的这些声明与版本说一起纳入
Ed25519 签名；主机不兼容、未知权限或缺失受信依赖时，安装/更新会在切换目录前拒绝。未安装条目的说明标为目录声明，安装且验签后才标为随包已验证。Hub 严格
区分系统内置、发布者已验证、
仅本地完整性验证、目录收录和来源未验证，不把 HTTPS 下载、作者文字或未签名目录说明冒充认证。
普通插件和连接器更新会先在同盘暂存区完成验签，再把包目录、外置技能、技能注册表和安装状态作为
同一代际提交；跨进程锁阻止并发写入，未完成日志会在下次读取商城状态时自动恢复。只有四处状态均
保有一致上一代时，Hub 才会显示可回滚。
所有管理动作仍回到 Agent 自有页面，由 Agent 维护真实状态。适配器只允许标识、名称、说明、来源、
作者、版本和上述枚举状态等显式公共字段，逐字段限制长度、拒绝控制字符并按稳定身份去重；Agent
后续新增的私有路径、配置、原始错误、签名密钥/摘要、事务或数据库字段不会自动暴露给浏览器。
Agent 若需要操作设备应用，也只使用 Echo Capability Contract 暴露的计划与入队能力；整组启动、
停止和重启均先生成无副作用计划，再用绑定 planId 的管理员审批入队。这里复用公开能力边界，不复用
Agent 私有 SQLite 的表、路径或 schema。

照片应用提供以下需登录接口：

- `GET /api/appliance/photos/library|status|thumbnail|original`：只读扫描 NAS、状态、有界 WebP
  缩略图，以及用户点开单张照片后的原图 Range 响应；
- `POST /api/appliance/photos/search`：复用 Agent 本地 CLIP 索引，未建库时退化为文件名搜索；
- `POST /api/appliance/photos/plans/index|plans/index/apply`：确定性预览、管理员密码单次审批后，
  在后台建立固定的 `data/media/image_index.db`。

OS 只把经过路径筛选的相对图片清单交给 Agent 现有
`runtime.memory.hemolymph.image_semantic_index`，不复制模型、向量 schema 或私有 Agent 数据库。
扫描不跟随符号链接，并排除回收站、上传会话和 `.echo-*` 内部目录；索引、缩略图和搜索都不返回
宿主绝对路径。家庭成员的相册列表、状态计数、搜索、缩略图和原图全部先应用同一份实时 OMV
共享权限投影，不能通过分页总数、语义索引或缩略图接口旁路看到未授权图片。第一版只统计重复组
和模糊照片，不提供自动删除、移动或“清理”写入口。

## files / storage center — 本机容量分析

`GET /api/appliance/files/usage` 为桌面“存储中心”提供只读容量投影：物理容量、可上传空间、
照片/视频/音频/文档/归档分类、顶层大目录、回收站、上传预留和已配置共享配额。扫描最多读取
20 万个目录项并缓存 10 秒；`?fresh=true` 可显式重新分析。扫描不跟随符号链接、不读取文件内容、
不返回 NAS 根目录或宿主挂载路径，内部上传会话也不会被算成用户文件。存储中心的磁盘健康、
RAID/LVM、共享、家庭成员与配额页继续复用下方 OMV 真实接口，而不是维护第二套存储状态。

## device link — 手机与端侧设备连接

`GET /api/appliance/device-link` 将 Agent 现有 Tentacle 连接池投影为桌面“设备连接”应用，状态页只
返回已脱敏的设备名称、平台、在线状态、电量和能力数量，不返回配对口令、凭据摘要、UDID 或宿主
路径。原生 Echo 镜像继续默认关闭 Agent 的便利 LAN listener；只有管理员密码复核并消费绑定
`device-link.enable:lan` 的单次审批后，Echo 才在局域网启动同一 Tentacle 协议。

NAS Compose 会发布同号 `ECHO_DEVICE_LINK_PORT`（默认 8765），但没有启用 Device Link 时端口内
没有监听器。容器入口同时强制关闭 Agent personal preset 的共享 Tentacle，避免默认部署永远停在
`agent-shared`、无法单设备撤销和同步。配对地址优先取管理员打开桌面时使用的 RFC1918 IP/局域网
主机名，也可由 `ECHO_DEVICE_LINK_HOST` 明确指定；不会把容器网段、公共域名或 localhost 静默
写进手机深链。

原生模式下，`POST /api/appliance/device-link/pairing-invitations` 创建 5 分钟邀请；邀请首次成功
连接后绑定该设备 ID，持久状态只保存 JWT secret 派生 HMAC 摘要。每台设备可通过审批绑定的
`DELETE /api/appliance/device-link/devices/{id}` 单独撤销并断开。开发/NAS 配置若已由 Agent 启动
Tentacle，Echo 只以 `agent-shared` 兼容模式复用，不谎称具备单设备撤销。当前范围明确是同一局域网；
设备控制 WebSocket 不会经远程网页网关转发。

Echo 管理的邀请还会在深链中提供 `sync` 基址：Tailscale 文件同步在线时下发 Tailnet HTTPS，
否则仅在明确配置 `ECHO_DEVICE_SYNC_PORT` 时下发可信局域网入口。Mobile 因而能在同一次扫码中
配置控制通道和自动备份入口；没有对外 HTTP 入口的原生 loopback 模式、共享 Agent 凭据模式都不会
误发不可达或不可撤销的分设备同步入口。

可选 `docker-compose.remote-access.yml` 使用固定摘要的官方 Tailscale 容器，以 userspace 模式建立
WireGuard 私网并由 Tailscale Serve 为 Echo 网页终止 HTTPS。侧车不发布宿主端口、删除全部 Linux
capabilities，只能反代 `echo-os:8000`；一次性 auth key 通过只读 Compose secret 注入，不进入 Echo
进程、状态 API 或日志。Echo 只轮询固定的侧车健康端点，并在 `remoteAccess` 中分别报告“远程网页”
与“设备控制/文件同步/照片同步”；同步路由安全挂载后，Tailnet HTTPS 才会报告文件/照片同步可用，
Tentacle 设备控制始终保持 LAN-only。未启用 overlay 时仍固定报告未配置。

## device sync — 分设备照片与文件备份

Echo 复用 Device Link 的一机一凭据身份，但不会让“已配对”等同于“可写 NAS”。管理员需要在桌面
按设备分别开启照片或文件备份，对应四种独立的密码审批 action。设备 HTTP 请求使用
`Authorization: EchoDevice`、绑定的设备 ID 与强制 `X-Echo-Sync-Version: 1`；无版本或不兼容版本
在鉴权后返回 `426`，防止升级漂移时静默写错数据。Agent 的共享兼容凭据无法单独识别/撤销设备，
固定拒绝同步。机器合同与可编译的 Android/OkHttp 参考客户端位于 `docs/mobile/`。

同步账本保存在 OS 自有的 `data/sync/device-sync.db`，不打开 Agent 私有 SQLite。账本只记录授权、
资源 ID、相对路径、SHA-256、可恢复会话归属、变化游标和冲突状态；传输字节继续复用 FileManager
现有的大小上限、磁盘保留、共享配额、同目录临时文件、SHA 校验与原子提交。同一资源重复上报会
恢复或跳过；同名异内容固定保留冲突副本。每个上传会话绑定设备，另一台已配对设备也不能续传、
完成或取消。照片提交只使照片扫描缓存失效，不直接改 Agent 语义索引库；索引仍由照片应用受控重建。

## omv_bridge — OpenMediaVault 受限存储接入

OMV 宿主可运行 `python -m appliance.omv_bridge`，通过权限为 `0660` 的 Unix socket 暴露
挂载文件系统、物理盘摘要、单设备 SMART、脱敏块设备拓扑及共享/用户概览。Echo 主进程经
`ECHO_OMV_SOCKET` 接入后提供八条仅设备管理员可用的只读 API：

- `GET /api/appliance/omv/status`
- `GET /api/appliance/omv/health`
- `GET /api/appliance/omv/filesystems`
- `GET /api/appliance/omv/smart/devices`
- `GET /api/appliance/omv/smart?devicefile=/dev/...`
- `GET /api/appliance/omv/topology`
- `GET /api/appliance/omv/sharing`
- `GET /api/appliance/omv/sharing/{uuid}/privileges`

另有用户组创建、家庭成员创建、基础共享文件夹创建、单一已有用户/组共享权限、SMB、私网 NFS
和文件系统用户/组配额七组窄写 API。`POST /api/appliance/omv/accounts/groups/plan|apply`
只能创建严格命名的空普通组；`POST /api/appliance/omv/accounts/users/plan|apply` 只能创建使用强密码、
现有普通附加组、`/usr/sbin/nologin`、无 email/SSH key 且禁止自改资料的家庭成员。两者都必须
先预览，再使用绑定 planId 的设备管理员密码单次审批，写后从 OMV 回读验证，失败时只回滚本次
尚未交付的新增对象。家庭成员密码只在内存中经固定 Unix socket 传给 OMV；不进入进程参数，
也不返回或写入计划、响应、日志和审计。已有账户/组的更新、日常删除和密码重置仍由 OMV 管理。
`POST /api/appliance/omv/sharing/folders/plan|apply` 只允许在 OMV 已挂载可写卷上按便携名称创建
同名相对目录，固定为 `2770`/`users` 组；不接受任意路径，不修改或删除已有文件夹，也不管理 ACL。
创建前必须预览并用设备管理员密码签发绑定 planId 的单次审批；写后回读验证，失败回滚仅移除
OMV 配置且绝不递归删除目录或数据。`POST /api/appliance/omv/sharing/smb/plan` 只计算期望状态差异；
`POST /api/appliance/omv/sharing/privileges/plan|apply` 只允许从 OMV 返回的现有用户/组中选择一个，
把该对象在一个现有在线共享文件夹上的服务权限设为继承、禁止、只读或读写。它提交完整但经过
校验的当前权限表以保留其他对象，写前拒绝未应用的 Samba/Rsync 变更，写后按需部署、回读并在
失败时恢复原表；不会调用文件 ACL 接口、递归改权限、创建账户或删除数据。变更使用独立的
`omv.share-privilege.apply:<planId>` 密码审批和 HMAC 审计。
`POST /api/appliance/omv/sharing/smb/apply` 必须提交相同 desired、未过期 planId、设备管理员
密码签发的单次审批，并写 HMAC 审计链。它只能创建或更新已有 OMV 共享文件夹对应的私有 SMB
规则；不开放 guest、不接收 hosts/extra options，也不修改 ACL。
`POST /api/appliance/omv/sharing/nfs/plan|apply` 只创建或更新一个已有共享文件夹面向单一
RFC1918/IPv6 ULA CIDR 的规则，强制 `root_squash,sync,subtree_check`；通配符、公网、主机名、
高级导出选项和删除继续由 OMV 管理。两者都使用 planId 绑定的独立密码审批并回读验证/回滚。

桥内不接受调用方提供 OMV service/method，只能执行官方 `FileSystemMgmt.enumerateMountedFilesystems`
以及 `Smart.enumerateDevices/getInformation`；SMART 设备还必须来自 OMV 物理盘枚举或已挂载
文件系统的设备/父设备集合。拓扑只执行固定列的只读 `lsblk` 并解析 `/proc/mdstat`，因此能显示
物理盘、分区、md RAID、LVM 关系和软件阵列降级/重建状态，但不提供阵列控制。共享概览只
读取 OMV 共享文件夹、普通用户/组、SMB/NFS 规则及配置权限；除上述受限创建外，账户、文件 ACL
和复杂协议修改仍使用显式配置的 `ECHO_OMV_ADMIN_URL` 打开 OMV 官方管理页。Echo 仅在创建
请求期间瞬时转交新用户密码，不保存、不记录也不回显。可含序列号的
by-id 路径会在宿主转换成 canonical `/dev/...`；序列号、UUID、WWN、原始 SMART 文本和额外
OMV 字段全部在宿主侧丢弃。未配置或不可用时其余 Echo 功能照常运行，不会回退到宿主 shell、
块设备写权限或 OMV 管理凭据。安装和权限说明见 `deploy/omv/README.md`。
Echo 侧健康监测默认每 5 分钟轮询这些已脱敏数据，持久记录活跃告警及最近 256 次出现、变化、
恢复事件；桥不可用时历史告警保持为过期状态，不会被误判为恢复。

## 测试

```bash
uv sync --extra dev --extra serve --extra appliance
.venv/bin/python -m pytest tests/appliance/ -q   # 无需真实 Docker
```
