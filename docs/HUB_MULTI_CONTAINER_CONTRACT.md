# Echo Hub 多容器应用合同

当前阶段先把多容器应用能做什么、不能做什么固定成机器可校验的合同，再实现执行器。Hub 不接受
Compose 文件，也不允许浏览器或 Agent 自行提交镜像、端口、宿主路径、网络或特权参数。

## 当前已落地

- 目录新增 `echo.hub.bundle-package.v1`，只接受固定 SHA-256 镜像摘要和 amd64/arm64。
- 每个服务必须声明角色、依赖、网络、命名卷、生成式密钥、环境、启动参数和 exec 健康检查。
- 只有 `publicService` 可以发布宿主端口；数据库和缓存只能连接 `internal` 后端网络。
- `*_FILE` 环境变量必须准确指向本服务已挂载的生成式密钥，目录不能放明文密码或插值表达式。
- 应用状态卷必须进入升级快照集合；NAS 用户资料目录长期保留且禁止被应用更新事务覆盖。服务升级
  顺序必须覆盖全部服务并满足依赖拓扑。
- 大版本一次最多跨一级，避免把 Nextcloud 不支持的跨级迁移包装成一键安全更新。
- 合同摘要由规范 JSON 确定生成，可进入计划身份、受管标签和审计链。
- 应用可声明受限系统能力提供者；当前只允许 `lan-discovery`。提供者容器必须运行且健康，调用方不能
  借此提交 host 网络、端口或任意代理配置。

Nextcloud 固定为 PostgreSQL 18.6、Redis 8.10.1、Nextcloud 34.0.3 App 和同版本 Cron 四个服务。
Immich 固定为 Valkey、PostgreSQL、机器学习和 Server 四个服务；照片库只绑定到
`photos/immich`，数据库与模型缓存使用独立应用状态卷。
Open WebUI 固定为 0.11.1 App 与内部 Valkey 两个服务；只发布 App 的 3005 端口，持久密钥通过
只读密钥卷注入，应用数据进入升级快照，遥测默认关闭。模型端点由用户在 Open WebUI 内配置，
目录不会伪装成已经内置模型或算力。
qBittorrent 固定为 LinuxServer 5.2.3 单服务图；Web 管理端只发布 3006，BT 监听固定 6881/TCP+UDP，
下载只写入 `downloads/qbittorrent` 专属 NAS 目录。Echo 首次生成管理员密码，在容器启动前按
qBittorrent 的 PBKDF2-SHA512 格式写入持久配置并只回显一次；密码不依赖启动日志，重启和卸载
重装不会重置。
更新回归使用 LinuxServer 官方 5.2.2 多架构 OCI 索引摘要升级到 5.2.3，成功路径只快照配置卷并
保留密码卷；候选启动失败时恢复配置卷、旧容器名和原运行状态。
Syncthing 固定为官方 2.1.3 多架构单服务图；Web 管理端发布 3007，同步监听固定 22000/TCP+UDP，
只写入 `sync/syncthing`。设备身份、索引和配置保存在独立卷，管理员密码通过标准输入在首次生成配置
时设置并只回显一次。Syncthing 本身保持普通 Docker bridge，不获得 host 网络；独立的
`lan-discovery` 最小权限代理只转发并校验 Syncthing 局域网发现报文。更新回归使用官方 2.1.2 OCI
索引摘要升级到 2.1.3，成功时只快照配置卷，失败时恢复配置、密码、旧容器名和原运行状态。
Paperless-ngx 固定为 3.1.0 App、PostgreSQL 18.6、Valkey 9、Gotenberg 8.34 和 Tika 3.3.1.0 五服务图；
只发布 App 的 3008 端口，数据库、缓存和文档转换服务均留在私有后端网络。应用数据、数据库和缓存
进入联合升级快照；原始文档、消费入口与导出结果只写入
`documents/paperless/{media,consume,export}` 三个 NAS 专属目录。管理员密码只回显一次，数据库密码和
应用签名密钥通过只读密钥卷注入。默认启用简体中文与英文 OCR，并把 worker/thread 数固定在 NAS
可承受的低并发边界。更新回归使用官方 3.0.5 多架构 OCI 索引摘要升级到 3.1.0，候选失败时恢复三
个状态卷、五个旧容器名和原运行状态，NAS 文档目录不进入应用更新或失败清理。
Home Assistant 固定为官方 2026.8.3 多架构单服务图，配置保存在升级快照卷。官方容器建议
`privileged + host network`；Echo 只保留 mDNS/SSDP 所需的 host 网络，强制剥离全部 capability、
禁止提权，不挂 Docker socket、设备、D-Bus 或宿主目录。8123 直接由应用监听，合同拒绝端口重映射、
隐藏 host 服务或带提权 profile 的变体。该安全模式面向普通 LAN 集成，明确不承诺 USB、Bluetooth、
Zigbee 直通。更新回归使用官方 2026.8.2 OCI 索引升级到 2026.8.3，失败时恢复配置卷、旧容器名和原
运行状态。
更新回归使用官方 v0.11.0 多架构 OCI 索引摘要升级到 v0.11.1，验证成功切换只快照应用数据且保留
密钥；候选 App 启动失败时恢复数据卷、旧 cache/app 容器名和原运行状态。该结论目前是本机执行器
证据，仍不替代真实 Docker 主机的镜像拉取与应用登录验收。

## 已开放安装的执行边界

Nextcloud、Immich、Open WebUI、qBittorrent、Syncthing、Paperless-ngx 与 Home Assistant 已在本机合同测试中完成以下执行闭环，并切换为
`available`：

1. 在受控 sidecar 内生成密钥，通过 Docker archive 写入分服务命名卷；密钥值不进入环境、命令、
   标签或计划。管理员初始密码只回显一次，数据库密码随数据持久保留。
2. 创建隔离网络、按依赖顺序启动并等待真实健康状态；任何一步失败都移除本次新容器、网络与卷。
3. 安装、更新、卸载都由 Hub 根据目录重新计算，不接受调用方提供 Docker 配置。
4. 更新前停止旧服务并同时快照合同声明的应用状态卷；候选服务在新的隔离网络验活，失败时恢复
   状态卷、旧容器名和原运行状态。Immich 照片库不进入应用更新快照，也不会在失败清理中删除。
5. 卸载只移除该应用受管的容器和网络，保留数据与密钥卷；重新安装验证密钥存在但不会再次回显。
6. Immich、qBittorrent、Syncthing 与 Paperless-ngx 安装器只从唯一受保护的 `echo-os` 容器取得真实 NAS bind 根目录，并
   用无网络、只读根文件系统的临时容器创建规范化专属目录；拒绝绝对路径、路径逃逸、符号链接和
   伪造提供者。
7. 安装后运行健康只经 `echo.hub.runtime.v1` 返回：sidecar 必须先核对目录服务集合、受管标签、固定
   容器名、计划/包摘要和版本，再聚合健康、CPU、内存、进程与重启次数。原始 inspect/stats、日志、
   环境、挂载和网络地址不得越过 sidecar；归属不完整或重复时失败关闭。

开发机没有 Docker Engine，因此“代码已开放”仍不等于“真机已交付”。正式交付前还必须在真实
amd64/arm64 Docker 主机验证首次登录、重启、更新失败回滚、卸载重装读回和大图库/大文件负载；
物理生命周期 schema 8 还要求两轮安装后九个应用都从已认证详情接口读到全服务健康和真实聚合资源，
且证据只保存有界汇总，不归档原始容器数据；
当前正式物理验收基线已固定 Jellyfin、Navidrome、Syncthing、Nextcloud、Immich、Open WebUI、
qBittorrent、Paperless-ngx、Home Assistant 九个应用；计划和结果 schema 已拒绝旧八应用证据，并要求两台设备验证
Syncthing 局域网自动发现、Paperless 中文/英文 OCR、DOCX/XLSX/PPTX 解析与导出，以及 Home Assistant
mDNS/SSDP 真实发现与 host-LAN 最小权限边界，仍需在 x86/ARM
真机实际执行后才能宣称交付。

## 与 Agent 的边界

Hub 商城通过 Echo 自己的认证接口只读调用 Agent 插件/技能目录抽象，不转发浏览器令牌，也不会
读取、复制或迁移 Agent 私有 SQLite。Agent 继续拥有能力目录和安装状态；设备同步、设备应用状态、
密钥和升级记录属于 Echo 自己的状态域，避免两个产品互相绑死。
