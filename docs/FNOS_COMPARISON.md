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
RAID 按钮数量，而在正式制品与真实硬件证据，以及 WebDAV/DLNA/云备份等产品宽度。

| 领域           | 飞牛解包证据                                                                                         | Echo OS 当前状态                                                                                   | 判断                                                                  |
| -------------- | ---------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| 安装与硬件覆盖 | 定制 d-i、两套定制内核、96 个 firmware deb、Realtek/NVIDIA/it87 OOT 驱动                             | Debian 标准栈、双架构构建与大量实验室合同；正式冻结 ISO/物理机门仍未关闭                           | 飞牛产品化更成熟；Echo 维护成本更低但硬件覆盖证据不足                 |
| 存储栈         | mdadm、ZFS、Btrfs、LVM、bcache；自研 `trimafs2` 统一命名空间                                         | ZFS/mdraid/Btrfs/EXT4 原生探测与多项 plan/approval/apply；不自研文件系统                           | Echo 方向正确；不要复制 `trimafs2` 的内核维护负担                     |
| 文件协议       | Samba、NFS、`smbftpd`、WebDAV、MiniDLNA 均有系统/私有组件证据                                        | SMB、NFS、Time Machine 已闭环；没有 NAS 服务端 FTP/WebDAV/DLNA                                     | WebDAV 与轻量 DLNA 是 P1；FTP 默认不必开放，可做可选组件              |
| 账号与权限     | `accountsrv`、`trimacl`、定制 Samba/ACL、`trim_tfa`                                                  | 独立家庭账号、实时共享权限、POSIX ACL、硬配额、TOTP、恢复码、限速、会话撤销                        | Echo 安全语义更清晰；飞牛的深度 Samba 补丁不应在无刚需时照搬          |
| 备份与同步     | `backup_cloud/local/remote/service`、rclone、WebDAV 等组件                                           | 加密 Restic 备份集、跨卷恢复、掉电恢复、状态备份、手机照片/文件同步                                | Echo 恢复完整性更强；云厂商/远端目标宽度仍弱                          |
| 故障通知       | `notify_service`、`eventlogger_service`，且 CGI 有外部邮件配置/测试接口                              | 浏览器通知之外，后台 Webhook 与 SMTP 邮件各自具备加密配置、审批审计、独立去重、退避重试和测试投递  | 核心通道已补齐；真实公网、企业渠道和长时断网仍待验                    |
| 远程访问       | `trim_open_gateway`、`trim-sharelink`、WebDAV 远程访问文案/路由                                      | Tailscale Serve HTTPS，默认不开放公网端口                                                          | Echo 边界更安全；消费级分享链接体验不如飞牛完整                       |
| 应用与下载     | `trim_app_center`、qBittorrent、aria2、媒体服务；基础镜像的 `/usr/local/apps` 未包含可断言的可选应用 | 受信 Docker Hub，Jellyfin/Immich/Nextcloud/qBittorrent 等九个应用                                  | Echo 可审计/回滚设计更强；真实 Docker 生命周期和 aria2 式下载中心仍缺 |
| 虚拟化         | 安装 QEMU/libvirt/swtpm 软件包                                                                       | 无 VM 管理控制面                                                                                   | P2；包存在不能证明飞牛基础镜像已有完整 VM UI                          |
| iSCSI          | 语言包明确提示需从应用中心安装 iSCSI App；基础解包只看到客户端库，不能证明 target 已安装             | 无 iSCSI target 控制面                                                                             | 双方都不能把它算基础能力；Echo 可列为可选 P2                          |
| 更新与可观测   | 私有签名源、liveupdate/updatemgr、分层 systemd、eventlogger、统一 core dump                          | A/B、签名、受限 core dump、固定 systemd 健康探针、重启风暴保护及外部告警已接线；正式签名制品未闭环 | 本机观测面已补强，冻结制品与进程外存活探针仍是上线 P0                 |

## 优先级

1. **P0：冻结版本并完成正式 ISO/升级链与物理 NAS 验收。** 包括 x86_64/ARM64、真实
   Windows/macOS/Linux 客户端、SMART/RAID 降级换盘、UPS 断电、24 小时耐久、TLS 与回滚。
2. **P0/P1：把无人值守告警从本地闭环推到真实公网。** 当前 Webhook 与 SMTP 已独立落地；
   下一步是真实接收端、断网数小时后的恢复、DNS 变化和重启去重，再加企业微信/钉钉适配器。
3. **P1：补协议与备份目标宽度。** 优先 WebDAV、轻量 DLNA/媒体发现、S3/对象存储或主流云
   备份目标；不要先做 FTP、VM 或 iSCSI。
4. **P1：完成崩溃与运行观测的进程外闭环。** 受限 core dump、固定服务健康探针、
   `start-limit-hit` 检测及告警联动已落地；仍需独立于主后端的存活探针和真机重启风暴验收。
5. **P2：按用户需求增加 VM、iSCSI target、aria2 下载中心和可选 FTP。** 这些不应阻塞 NAS
   核心可靠性交付。

## 明确不建议照搬

- 不复制 `trimafs2` 自研内核文件系统；继续用 ZFS dataset、bind mount 或 mergerfs。
- 不在没有互操作性证据时维护整套 Samba 私有补丁。
- 不做飞牛式 1.2 GB 单体核心包；保持功能域拆分、独立升级与回滚。
- 不把语言包中的“有这个 App”误报成基础镜像已经安装。
- 不为了功能对齐取消 Echo 现有的计划、一次性审批、写后回读、审计与失败关闭边界。
