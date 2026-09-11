# Echo OS 与本地飞牛 OS 解包能力对比

更新时间：2026-09-09

## 证据边界

飞牛侧只采用本机解包产物，不把营销页面或语言包中的提示直接当成已安装能力：

- 镜像身份与完整清单：`C:\飞牛os\extracted\README.md`
- 架构复盘：`C:\飞牛os\extracted\reports\nas-architecture-notes.md`
- 系统根：`C:\飞牛os\extracted\trimfs`

镜像是飞牛 V1.2.0505：Debian 12.12 根系统、Debian 13.1 安装器、两套定制内核，
`/usr/trim` 约 1.2 GB、99 个私有二进制。语言包能证明产品/UI 设计意图，但只有二进制、
systemd unit、nginx 路由、dpkg 状态或实际配置同时存在时，本表才把它记为基础镜像能力。

## 面向 NAS 的结论

Echo OS 已经不是“套壳文件管理器”：SMB/NFS/Time Machine、ZFS/mdraid/Btrfs、SMART、
UPS、硬配额、受控存储写面、Restic 备份恢复、Docker 应用、Tailscale、家庭账号、审批审计、
TOTP 与无人值守 Webhook/SMTP 告警已经形成一套更保守的控制面。它和飞牛的最大差距，当前不在
RAID 按钮数量，而在正式制品与真实硬件证据，以及云备份、分享链接等产品宽度。轻量 DLNA 与
WebDAV 服务端均已完成 Debian 13 QEMU 的真实运行验证，但尚未取得新 raw 镜像、异机电视/播放器
以及 Windows/macOS WebDAV 客户端证据。

飞牛 WebDAV 判断来自解包实物而非文案：`trimfs/etc/systemd/system/webdav.service` 启动私有
`/usr/trim/bin/webdav`，依赖 `trim_main`、RPC broker 与 sysinfo；`trimfs/usr/trim/etc/webdav.conf`
固定 HTTP 5005、HTTPS 5006、证书/密钥和无限深度。Echo 不能用一个普通 nginx DAV 端点冒充等价能力，
因为那会绕过现有家庭账号和共享 ACL。Echo 现已采用 nginx TLS → 回环 rclone auth proxy → 每账号
OpenSSH chroot/SFTP 的分层实现：认证复用 Echo 家庭账号但不复用 POSIX 密码，目录可见性和写入继续
由实时 POSIX ACL 决定，禁用账号会撤销登录并清除旧 bind mount。网关及刷新单元默认关闭，只有管理员
完成 plan → 独立密码审批 → apply 后才发布，停用会清理所有 unit、watcher 和 bind mount。它已通过
真实 QEMU 启停与方法矩阵，剩余 P1 是装入冻结 raw 后用 Windows/macOS 实体客户端完成证书信任、
映射、锁和大文件互操作验收。

原生 raw 镜像的源码入口现已从任务投影用的轻量扩展切到完整、强制认证的 NAS 控制面；OEM
本机口令只在进程内生成 bcrypt 认证状态，特权存储操作继续经过固定 Unix-socket broker。raw
装配源码现也显式包含与飞牛解包实物一致的 Samba/VFS、NFS、mdadm、SMART、NUT、hdparm、quota
和 WSD 发现栈；SMB/Time Machine 与 NFS 的受管私网规则随共享事务开闭，开机门会把运行态和持久态
逐条绑定到 root-only 状态文件。Raw 装配现在也包含 Debian Docker Engine、每设备 256-bit credential
以及只监听回环、以专用静态系统账号运行的 Hub 控制代理；该账号只有 broker 主组和 Docker socket
补充组、零能力且不继承桌面管理员组，Agent 不接触 `docker.sock`。这仍不等于飞牛式
可安装 NAS 成品：Hub 生命周期现在会从受信目录、完整容器标签、实际端口绑定和当前私网容器 IP
同步 `StrictForwardPorts` rich rules，漂移时关闭全部应用放行。raw OpenZFS 源码装配也已补齐 Debian
`contrib`、精确内核 headers、DKMS/ZED、与 UKI 相同 UEFI `db` 身份的逐模块签名，以及阻断存储 broker
和 boot blessing 的当前内核健康门；Hub 链已在 Debian 13 QEMU 的真实 Docker 26.1.5、
firewalld 2.3.1/nftables 上通过鉴权、端口批准、计划绑定停启、转发恢复及身份漂移关闭，但新 raw 制品
尚未在真实 Secure Boot 内核验证模块加载，正式九应用和物理机仍待验收。

| 领域           | 飞牛解包证据                                                                                         | Echo OS 当前状态                                                                                                                                          | 判断                                                                         |
| -------------- | ---------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| 安装与硬件覆盖 | 定制 d-i、两套定制内核、96 个 firmware deb、Realtek/NVIDIA/it87 OOT 驱动                             | Debian 标准栈、双架构构建与大量实验室合同；正式冻结 ISO/物理机门仍未关闭                                                                                  | 飞牛产品化更成熟；Echo 维护成本更低但硬件覆盖证据不足                        |
| 存储栈         | mdadm、ZFS、Btrfs、LVM、bcache；自研 `trimafs2` 统一命名空间                                         | ZFS/mdraid/Btrfs/EXT4 原生探测与多项 plan/approval/apply；raw 已装配逐内核构建和 Secure-Boot 签名 OpenZFS，不自研文件系统                                 | Echo 方向正确；新 raw 制品仍须验证实际加载，不复制 `trimafs2` 的内核维护负担 |
| 文件协议       | Samba、NFS、`smbftpd`、WebDAV、MiniDLNA 均有系统/私有组件证据                                        | SMB、NFS、Time Machine 已闭环；ReadyMedia DLNA 已通过 QEMU 实测；WebDAV 已通过 TLS、家庭账号、ACL/chroot、完整写方法与禁用撤权 VM 实测；无 NAS 服务端 FTP | DLNA/WebDAV 均待 raw/异机验收；FTP 默认不开放、可做可选组件                  |
| 账号与权限     | `accountsrv`、`trimacl`、定制 Samba/ACL、`trim_tfa`                                                  | 独立家庭账号、实时共享权限、POSIX ACL、硬配额、TOTP、恢复码、限速、会话撤销                                                                               | Echo 安全语义更清晰；飞牛的深度 Samba 补丁不应在无刚需时照搬                 |
| 备份与同步     | `backup_cloud/local/remote/service`、rclone、WebDAV 等组件                                           | 加密 Restic 备份集、跨卷恢复、掉电恢复、状态备份、手机照片/文件同步；除安全发现 USB/NFS/CIFS/rclone 目标外，UI 已能审批创建和撤销 S3 兼容的加密 rclone 挂载 | Echo 恢复完整性与通用 S3 路径更强；云厂商 OAuth 原生接入宽度仍弱             |
| 故障通知       | `notify_service`、`eventlogger_service`，且 CGI 有外部邮件配置/测试接口                              | 浏览器通知之外，后台 Webhook 与 SMTP 邮件各自具备加密配置、审批审计、独立去重、退避重试和测试投递                                                         | 核心通道已补齐；真实公网、企业渠道和长时断网仍待验                           |
| 远程访问       | `trim_open_gateway`、`trim-sharelink`、WebDAV 远程访问文案/路由                                      | Tailscale Serve HTTPS；文件分享支持高熵一次性令牌、到期/次数限制、撤销、ACL 重检和管理员审批；默认不开放公网端口                              | Echo 已补齐安全分享链；飞牛仍在公网网关/多端体验上更宽                              |
| 应用与下载     | `trim_app_center`、qBittorrent、aria2、媒体服务；基础镜像的 `/usr/local/apps` 未包含可断言的可选应用 | 受信 Docker Hub，Raw 镜像内置受限 Docker 控制和私网应用端口同步；真实 Docker/nftables QEMU 生命周期已验，九个正式应用待 raw/物理复验                      | Echo 可审计/回滚设计更强；正式制品、九应用负载和 aria2 式下载中心仍缺        |
| 虚拟化         | 安装 QEMU/libvirt/swtpm 软件包                                                                       | 无 VM 管理控制面                                                                                                                                          | P2；包存在不能证明飞牛基础镜像已有完整 VM UI                                 |
| iSCSI          | 语言包明确提示需从应用中心安装 iSCSI App；基础解包只看到客户端库，不能证明 target 已安装             | 无 iSCSI target 控制面                                                                                                                                    | 双方都不能把它算基础能力；Echo 可列为可选 P2                                 |
| 更新与可观测   | 私有签名源、liveupdate/updatemgr、分层 systemd、eventlogger、统一 core dump                          | A/B、签名、受限 core dump、固定 systemd 健康探针、重启风暴保护及进程外 deadman 已接线；正式签名制品未闭环                                                 | 本机观测面已补强，冻结制品与真实故障注入仍是上线 P0                          |

## 优先级

1. **P0：冻结版本并完成正式 ISO/升级链与物理 NAS 验收。** 包括 x86_64/ARM64、真实
   Windows/macOS/Linux 客户端、SMART/RAID 降级换盘、UPS 断电、24 小时耐久、TLS 与回滚。
2. **P0/P1：把无人值守告警从本地闭环推到真实公网。** 当前 Webhook 与 SMTP 已独立落地；
   下一步是真实接收端、断网数小时后的恢复、DNS 变化和重启去重，再加企业微信/钉钉适配器。
3. **P1：补协议异机验收与备份目标宽度。** 把现有 DLNA、WebDAV 装入冻结 raw，分别在异机
   播放器和 Windows/macOS 客户端验收。备份 UI 已能列出通过独立文件系统校验的预挂载
   USB/NFS/CIFS/rclone 目标，并受控管理 S3 兼容对象存储挂载；HTTPS MinIO 互操作已实测，
   下一步补公网 S3 厂商与 OneDrive/Google Drive/百度网盘等 OAuth 原生适配，不要先做 FTP、VM 或 iSCSI。

预挂载目标发现已经在 Debian 13 x86_64 快照 VM 用真实 ext4、只读 bind、tmpfs 和 `fuse.rclone`
验证：只返回可写 ext4 与 `fuse.rclone`，排除只读/易失挂载，并隐藏块设备和远端源。证据
`_vmtest/external-mount-candidates-runtime.json` 的 SHA-256 为
`7a7d23d6b28717b9572ff3fb75b68bae92d6d686199f2e7e1384197de9b7f0d8`。验证还发现并修复了
正式包闭包只含 `rclone`、缺少 `fuse3/fusermount3` 的问题；mkosi 与离线 ISO 清单现在都显式包含
`fuse3`，制品门要求 `/usr/bin/fusermount3` 为 root:root mode-4755。本次旧 VM 临时安装 Debian
`fuse3 3.17.2-3`，并使用同级飞牛解包的 `rclone v3.67.6-DEV`；因此它证明发现逻辑和依赖合同，
不替代重新构建冻结 raw 后的包存在性验收。

S3 兼容管理面会把访问密钥仅写入 `systemd-creds` 加密凭据，以计划哈希和管理员单次审批绑定
创建/撤销，并在挂载后重新执行独立写入与边界验证。Debian 13 x86_64 VM 已用加密凭据和本地
rclone alias 验证真实 systemd 启停、宿主可见 `fuse.rclone`、候选入选及停止后卸载；证据
`_vmtest/rclone-backup-mount-runtime.json` 的 SHA-256 为
`239799a283c8de344733bf255d48b9f4a3ad9cf66dc342271ae6d8805bbd2ed2`。随后使用官方 SHA-256
校验的 MinIO Linux amd64 服务，在 VM 回环地址建立受信临时 HTTPS，经生产 plan/apply 创建
加密 rclone 挂载，完成 8 MiB Restic 备份、全仓 `--read-data`、服务重启后的恢复与摘要核对，
再经生产撤销链确认卸载、删凭据和清注册表；28 个 S3 对象实际落盘。证据
`_vmtest/s3-backup-interop.json` 的 SHA-256 为
`61103dfd1a11ea8124ca2f87ffd080900403b3e951d5eba8c58f65c8720fd67d`。这关闭了通用 HTTPS S3
协议的本地互操作门，但仍不替代 Cloudflare R2、AWS S3 等公网厂商与长时断网验收。

已有设备的 `nas-maintenance-v4` 升级也已在 Debian 13 快照 VM 从 v3 等价基线实跑：迁移仅补齐
缺失包、模板、wrapper 和安全目录，保持既有 Time Machine/unit 不变，二次规划幂等且不会启用
无凭据实例。证据 `_vmtest/host-migration-v4.json` 的 SHA-256 为
`681b5d416b2173e511d3b697dc175cdc3910dca48d77750c0d23bbcce18b4808`；物理旧机仍需最终验收。
4. **P1：完成崩溃与运行观测的真实环境闭环。** 受限 core dump、固定服务健康探针、
   `start-limit-hit` 检测、`OnFailure` 与五分钟进程外 deadman 已落地；Debian 13 QEMU 已验证在线锁、
   5 次重启失败、独立通知进程、恢复和 timer。仍需冻结候选、公网接收端和真机重启风暴验收。
5. **P2：按用户需求增加 VM、iSCSI target、aria2 下载中心和可选 FTP。** 这些不应阻塞 NAS
   核心可靠性交付。

## 明确不建议照搬

- 不复制 `trimafs2` 自研内核文件系统；继续用 ZFS dataset、bind mount 或 mergerfs。
- 不在没有互操作性证据时维护整套 Samba 私有补丁。
- 不做飞牛式 1.2 GB 单体核心包；保持功能域拆分、独立升级与回滚。
- 不把语言包中的“有这个 App”误报成基础镜像已经安装。
- 不为了功能对齐取消 Echo 现有的计划、一次性审批、写后回读、审计与失败关闭边界。
