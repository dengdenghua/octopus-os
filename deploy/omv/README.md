# 在 OpenMediaVault 上接入 Echo OS

这套接入读取 OMV 已经维护的挂载文件系统、SMART、拓扑、共享和账户脱敏概览，并提供八类
受控写链：创建空的普通用户组、创建受限家庭成员账户、重置仍满足安全约束的家庭成员密码、在一个**已挂载可写 OMV 文件系统**上
按便携名称创建基础共享文件夹，把一个**已有 OMV 共享文件夹**发布为私有 SMB 或受限私网 NFS
共享、调整一个**已有用户或组**在该共享上的服务权限，以及为一个**已有文件系统上的已有用户
或组**设置 OMV 原生硬配额。Echo 不创建阵列、不格式化磁盘、不修改/删除共享文件夹、不修改
文件 ACL，也不代理任意 OMV RPC。OMV 仍是存储与账户权威。

## 安全边界

```text
浏览器（已登录 Echo）
  → Echo /api/appliance/omv/*（登录 JWT）
      用户组创建 / 家庭成员创建或密码重置 / 基础共享文件夹 / 共享用户组权限 / SMB / 私网 NFS / 文件系统用户组配额：
        期望状态 → 预览 → 管理员密码复核 → 单次 planId 审批 → 审计
  → /run/echo-omv/omv.sock（容器内只读挂载、0660）
  → 宿主 echo-omv-bridge
  → 固定只读 RPC + 仅供共享文件夹 / 权限 / SMB / NFS / 配额事务使用的固定 RPC
      FileSystemMgmt.enumerateMountedFilesystems
      Smart.enumerateDevices
      Smart.getInformation
      FsTab.get
      ShareMgmt.enumerateSharedFolders / getCandidates / get / getPrivileges
      ShareMgmt.set / delete（只创建；回滚 delete 固定 recursive=false）
      ShareMgmt.setPrivileges（仅完整的已有 user/group 配置权限表；perms 只允许 0/5/7）
      UserMgmt.enumerateUsers / enumerateGroups
      UserMgmt.getSettings / getGroup / getUser
      UserMgmt.setGroup / deleteGroup（仅空普通组的创建事务与失败回滚）
      UserMgmt.deleteUser（仅本次新用户的失败回滚）
      OMV engined UserMgmt.setUser（仅经固定 Unix socket 创建受限新用户或替换受限成员密码，不进入进程参数）
      SMB.getSettings / getShareList
      NFS.getSettings / getShareList
      SMB.getShare / setShare / deleteShare
      NFS.getShare / setShare / deleteShare
      Quota.get / setByTypeName（仅 user/group、已有文件系统 UUID、KiB 硬限制）
      Config.isDirty / applyChanges（仅单模块 samba/rsyncd/nfs/quota，force=false）
  → 固定列的只读 lsblk + /proc/mdstat
      NAME,TYPE,SIZE,FSTYPE,ROTA（不读取 UUID / SERIAL / WWN）
```

- 没有新 TCP 端口；桥进程启用 `PrivateNetwork=true`，只能使用本机 Unix socket。
- Echo 容器没有 OMV 密码、root shell 或 `/run/openmediavault`；`docker-control` 也不挂桥插口。
- RPC 服务名、方法名和参数形状写死在宿主代码里。只有用户组、家庭成员创建/密码重置、共享文件夹、共享权限、SMB、NFS 和配额各自的 `plan/apply` POST
  路径存在；任意方法名、额外字段、访客访问、hosts allow/deny、Samba extra options、
  任意共享路径或 quota 单位都不能穿过桥。
- 当前 OMV 核心已不提供旧版 `RaidMgmt`/LVM 管理 RPC；桥只用固定参数 `lsblk` 映射物理盘、
  分区、md RAID 和 LVM 父子关系，并从内核 `/proc/mdstat` 读取软件阵列健康。两条路径都
  没有调用方参数，也不执行 shell。
- SMART 只允许查询 OMV 已枚举的物理盘或挂载卷设备/父设备；可泄露序列号的 by-id 路径会转换
  为 canonical `/dev/...`，序列号和原始 SMART 文本不出宿主。
- Echo API 再做一次类型、长度、容量和设备路径校验，并继续使用设备登录 JWT。
- 共享概览不返回用户密码/口令哈希、SSH 公钥、用户 home、共享绝对宿主路径或额外 Samba
  选项；权限查询只接受桥刚刚枚举出的共享 UUID。
- 用户组创建只接受严格的小写普通账户名，拒绝系统/保留名称，只能创建无成员的新组；已有组、
  组更新和日常删除均拒绝。创建计划经过 `omv.group.create:<planId>` 的管理员密码审批，写后
  `getGroup` 回读验证；只有创建事务尚未成功完成时才会回滚删除本次空组。
- 家庭成员创建只接受严格的小写普通账户名、强密码和桥当前枚举出的普通附加组；OMV 自动创建
  home 的全局选项必须关闭。新用户固定 `/usr/sbin/nologin`、无 email、无 SSH key、禁止用户
  自改资料，并由 OMV 同步建立 Samba 账户。密码经请求体送入固定的 OMV engined Unix socket，
  不进入命令行参数；planId 用 HMAC 绑定密码，但计划响应、桥响应、日志、证据和 Echo 审计均不
  返回或持久化密码。审批绑定 `omv.user.create:<planId>`；失败回滚只删除本次尚未交付的新用户。
- 已有成员密码重置只接受仍保持 `/usr/sbin/nologin`、无 email/SSH key、禁止自改资料的普通用户；
  新密码使用独立 `echo.omv.user-password-desired.v1` 和 HMAC planId，经
  `omv.user.password.reset:<planId>` 单次审批后走同一个秘密 socket。写后回读确认 UID/GID、显示名、
  附加组和全部安全属性未漂移。OMV 一旦接受密码便无法由 Echo 恢复旧密码，因此失败不会伪报回滚，
  而是明确标记凭据状态可能不确定并要求重新预览、重试和登录验证。其他账户属性更新与日常删除仍拒绝。
- 共享文件夹创建只接受 `1–64` 位 ASCII 字母、数字、点、短横线和下划线组成的便携名称；拒绝
  首尾点、连续两点和网络客户端保留名。相对目录只能由名称推导，目标只能是 OMV 枚举出的已挂载
  可写文件系统，模式固定提交为 `770`（OMV 落盘为 `2770`/`users`）。它只支持 create/none，
  不开放更新、删除或 ACL；失败回滚固定 `recursive=false`，只撤销配置而不删除目录和数据。
- SMB 写入只接受已有且在线的共享文件夹和已启用的 SMB 服务；OMV 有未应用 SMB 变更时拒绝，
  apply 前重新计算 planId，写后同步部署并回读验证，失败时恢复原规则或删除本次新规则。已有
  访客、主机范围或额外 Samba 配置的复杂规则只允许在 OMV 管理。
- 共享权限写入只接受 OMV `getPrivileges` 唯一枚举出的已有用户或组，以及一个已有在线共享文件夹；
  单次只改变一个对象，但写回完整的已配置权限表并保留其他对象。值只允许继承、禁止、只读、读写，
  映射为 OMV 的省略/`0`/`5`/`7`。写前拒绝未应用的 Samba 或 Rsync 变更，写后只部署被 OMV 标脏的
  固定模块并回读全表，失败恢复原表。审批绑定 `omv.share-privilege.apply:<planId>`；不开放
  `ShareMgmt.setFileACL`、递归标志或任意账户名称；账户创建只能走上面的独立受限计划，不能借
  共享权限接口完成。
- NFS 写入只接受已有且在线、相对路径不含空格的共享文件夹和已启用的 NFS 服务；客户端只能是
  单个 RFC1918 或 IPv6 ULA CIDR，强制 `root_squash,sync,subtree_check`，不接受 `*`、公网、主机名、
  `insecure`、`no_root_squash` 或任意额外导出参数。已有复杂规则和删除操作仍在 OMV 管理；创建或
  更新同样经过 planId、独立 `omv.nfs.apply` 密码审批、同步部署、回读验证和失败回滚。
- 配额写入只接受 OMV 已挂载、可写且报告支持 quota 的文件系统，以及 `Quota.get` 唯一枚举出的
  已有用户或组。硬限制只能是 0（取消限制）或 1024 字节整数倍；写前拒绝 OMV 未应用的 quota
  变更，写后回读验证，失败时恢复并再次验证原限制。审批绑定 `omv.quota.apply:<planId>`，不能
  用 SMB 审批票据代替。
- 这里的 OMV quota 按**文件系统 + 文件所有者用户/组**计量，覆盖本机、SMB 和 NFS 写入；它
  不是共享文件夹、目录或路径级配额。一个账户在同一文件系统其他目录中的文件也会计入。
- 磁盘初始化、阵列修复、共享文件夹修改/删除、密码以外的账户/组更新与日常删除、文件 ACL、
  NFS 删除及复杂 NFS 修改仍在 OMV 完成。
- Echo 默认每 5 分钟读取一次已脱敏视图，检测 SMART 异常、磁盘高温、卷容量和阵列状态；
  活跃告警、首次/最近出现时间及最近 256 次变化写入 `/data/omv-health-state.json`（0600）。
  桥中断时保留上一次成功状态并标记过期，不会把“读不到”误报成“已恢复”。

实现依据是 OMV 官方的 [`omv-rpc` 文档](https://docs.openmediavault.org/en/latest/development/tools/omv_rpc.html)、
官方 [`Smart` RPC 源码](https://github.com/openmediavault/openmediavault/blob/master/deb/openmediavault/usr/share/openmediavault/engined/rpc/smart.inc)、
[`ShareMgmt`](https://github.com/openmediavault/openmediavault/blob/master/deb/openmediavault/usr/share/openmediavault/engined/rpc/sharemgmt.inc)、
[`UserMgmt`](https://github.com/openmediavault/openmediavault/blob/master/deb/openmediavault/usr/share/openmediavault/engined/rpc/usermgmt.inc)、
[`SMB`](https://github.com/openmediavault/openmediavault/blob/master/deb/openmediavault/usr/share/openmediavault/engined/rpc/smb.inc)、
[`Config`](https://github.com/openmediavault/openmediavault/blob/master/deb/openmediavault/usr/share/openmediavault/engined/rpc/config.inc)、
[`Quota`](https://github.com/openmediavault/openmediavault/blob/45858fa94f2da45d584103e3ce469bdc1d6479a3/deb/openmediavault/usr/share/openmediavault/engined/rpc/quota.inc)、
[`NFS`](https://github.com/openmediavault/openmediavault/blob/master/deb/openmediavault/usr/share/openmediavault/engined/rpc/nfs.inc)
和 util-linux 的 [`lsblk` 文档](https://man7.org/linux/man-pages/man8/lsblk.8.html)。

## 安装

第一版宿主支持矩阵只包含 **Debian 13 + OMV 8**，架构为 amd64 或 arm64。安装器从固定、不可由
普通用户改写的 `/usr/lib/os-release` 读取 Debian 身份，并使用 OMV 自身同样采用的固定
`/usr/bin/dpkg-query -W -f=${Version} openmediavault` 查询已安装包版本；不执行 `os-release`
内容，也不使用 PATH 搜索命令。Debian 12、Ubuntu、OMV 7/9、缺失或畸形包版本都会在创建组、
写 unit 或启动服务之前拒绝。宿主以后离开支持矩阵时仍允许受管卸载，避免升级后无法移除桥。

### 推荐路径：OMV 原生插件包

正式交付优先使用 `openmediavault-echo-os` Debian 包。它沿用 OMV 插件的 control 扩展字段、
`restart-engined`/`update-workbench` trigger、Workbench 导航/路由/组件目录和 Debian systemd
helper 生命周期；桥 unit 安装到 `/usr/lib/systemd/system/echo-omv-bridge.service`，代码安装到
`/usr/lib/echo-os/omv-bridge`。包依赖把 OMV 限定在 `>= 8.0` 且 `< 9.0`，`preinst` 会在解包写入
前从可信 `os-release` 和固定 `dpkg-query` 强制检查 Debian 13 + OMV 8，运行验收还会再次检查。
这些门不能替代每个 OMV 8 次版本的真机安装证明。

在 Python 3.12+ 的发布环境中构建和反向验证确定性包：

```bash
python3 deploy/omv/plugin_package.py build --output-directory dist
python3 deploy/omv/plugin_package.py verify \
  dist/openmediavault-echo-os_0.2.0-1_all.deb
dpkg-deb --info dist/openmediavault-echo-os_0.2.0-1_all.deb
dpkg-deb --contents dist/openmediavault-echo-os_0.2.0-1_all.deb
```

版本默认由前端产品版本生成 Debian revision（例如 `0.2.0` 生成 `0.2.0-1`）。构建同时输出
`.deb.sha256` 和 SPDX 2.3 SBOM；CI 会在 Ubuntu 上再用系统 `dpkg-deb` 检查包，并在 main 发布
构建中为 `.deb` 和 SBOM 分别生成 GitHub OIDC/Sigstore 证明。正式安装前必须核对摘要和仓库身份：

```bash
gh attestation verify openmediavault-echo-os_0.2.0-1_all.deb \
  --repo <owner>/<repository>
sudo apt install ./openmediavault-echo-os_0.2.0-1_all.deb
sudo python3 /usr/lib/echo-os/omv-bridge/platform_preflight.py
getent group echo-omv
sudo systemctl status echo-omv-bridge.service
```

包配置在创建 `echo-omv` 组或重启服务之前自动运行同一份只读平台预检。预检再次确认
Debian 13 + OMV 8，要求短主机名不超过 SMB/NetBIOS 的 15 字符，并核对已安装
`40netplan.sh`、网络接口模型和 `/etc/netplan/*.yaml`。失败只输出问题代码和修复方向，不改
主机名、Netplan 或 OMV 文件；修复宿主后重新执行 `apt install --fix-broken`/包配置即可。上面的
显式命令保留完整 JSON，便于保存验收证据。

CI 还会把包放进固定摘要的 Debian 13 容器，离线执行真实 `dpkg` 首装、升级、注入式失败升级与
旧包回装、手动安装冲突、OMV 9 拒绝、离开矩阵后的 remove、重新安装和 purge。该门使用最小 OMV
版本身份和 systemd 命令夹具，证明 Debian 包管理生命周期、父目录/文件落盘和数据保留边界；它
不证明真实 OMV RPC 或服务启动。

2026-08-26 又在隔离的 ARM64 Debian 13 VM 中从 OMV 官方 Synchrony 仓库安装了当时仓库实际发布的
`openmediavault 8.5.6-1` 和 `openmediavault-salt 8.1.0`，再用未绕过依赖的 `apt install` 验证了
原生插件。实际通过项包括 `openmediavault-engined`、Nginx/PHP、Workbench 源文件和生成后的
`navigation-config.json`/`route-config.json`、真实只读 RPC、4 GiB 专用空白盘的 ext4 挂载、私有
SMB 创建/部署/幂等、用户 64 MiB 硬配额、purge 后文件摘要/SMB/配额保留，以及重装后的状态回读。
桥服务为 `root:echo-omv`、socket 为 `0660`，Samba 最终真实监听 445。该记录仍不能替代 x86_64、
物理盘 SMART/md RAID、浏览器内 Workbench 视觉、真实 SMB/NFS 客户端写满和正式签名发布验收。

当前工作区包含独立的 GitHub Actions `Real OMV 8 / x86_64` 门。它只在一次性 x86 runner 的
Debian 13 systemd 容器内运行，仓库只读挂载、不映射端口、不挂载或触碰宿主数据盘，也不创建
阵列；先从带固定 key 摘要的官方签名 Synchrony 仓库安装当时的真实 OMV 8，再安装本次生成的
`.deb`。NFS 验收只在容器 `/tmp` 创建固定 1 GiB 稀疏文件并绑定一个 loop 设备，由真实
`FileSystemMgmt.create` 创建 ext4 并由 OMV 挂载；专用共享文件夹再通过 Echo
`sharing/folders/plan|apply` 创建，随后通过 Echo 权限 `plan/apply` 给真实 `users` 组设置读写权限；
容器销毁后全部消失。门会验证真实 engined/RPC、Workbench 生成文件、预检、systemd 安全属性、
Unix socket、只读桥 API，并通过 Echo NFS `plan/apply`
创建读写规则，核对 `/etc/exports`、内核 `exportfs`、NFS
服务和 TCP 2049，再以 NFSv4 客户端实际挂载和写入。随后创建受限家庭成员和私有 SMB 规则，
通过官方 `smbclient` 的一次性 `PASSWD_FD` 匿名管道完成 SMB3 密码认证、上传和下载；随后经 Echo
重置该成员密码，要求旧密码认证失败、新密码仍能下载同一载荷且账号属性不变。两个密码都不进入
argv、环境值、文件、日志或 evidence。随后把同一 NFS 规则更新为只读，要求旧文件可读、
新写入被内核拒绝，并分别在插件 purge 后和重装后验证 OMV 规则、`users` 组共享权限与文件摘要
保持不变；家庭成员、Samba 账户、SMB 规则和 SMB 载荷也必须在 purge/reinstall 后继续通过真实
认证并保持相同 SHA-256，重装后的权限与 SMB 计划还必须成为无需写入的 no-op。最后上传
`echo-real-omv-x86-evidence.json`。若官方 importer 仍是
`dnsservers`，还会临时加入隔离 Netplan DNS，要求精确阻断且两个上游文件哈希不变；上游修复后
则要求同一配置通过。该工作流合同已经本地测试，但在正式分支第一次产生绿色 evidence artifact
之前，不能把 x86_64 真实 OMV 验收标为完成，更不能替代物理盘测试。

该门位于独立 `.github/workflows/omv-real-x86.yml`，只在 OMV 相关路径面向 `os-main`/`main` 的
PR、分支变更或人工 `workflow_dispatch` 时运行，不把官方仓库的网络波动扩散到所有无关 CI。
审批与全部 `omv_*.py` 控制面路径也在触发集合内，所有第三方 Action 固定到完整提交。上传前还必须由独立
`verify-real-omv-x86-evidence.py` 复核 exact-schema evidence、当前 `.deb` 字节 SHA-256、当前
Git revision、OMV 8、x86_64、运行中 systemd/socket、Netplan 行为、RPC、Workbench、NFS 私网
CIDR/UUID、共享文件夹/共享权限/NFS 各自的 planId、家庭成员/组/密码重置/SMB UUID 与 planId、SMB3 认证、
读写与跨 purge/reinstall 保留文件摘要、权限；额外/重复字段、公网 CIDR、错包、错版本、错
revision、摘要变化、符号链接或任一假成功布尔都会失败。绿色运行把同次生成的 `.deb`、包校验和、
SPDX 2.3、原始 evidence、verifier 报告、离线 verifier 和整个集合的 SHA-256 清单放进同一
`echo-real-omv-x86-evidence` artifact；正式分支成功后还为 `.deb`、SBOM 和证据集合生成 OIDC
attestation。下载并进入 artifact 目录后可再次离线复核：

```bash
sha256sum -c echo-real-omv-x86-artifact-set.sha256
python3 verify-real-omv-x86-evidence.py \
  "$PWD/echo-real-omv-x86-evidence.json" \
  --plugin-package "$PWD/openmediavault-echo-os_0.2.0-1_all.deb" \
  --expected-source-revision '<artifact 对应的 40 位 Git SHA>'
gh attestation verify openmediavault-echo-os_0.2.0-1_all.deb \
  --repo '<owner>/<repository>'
gh attestation verify echo-real-omv-x86-evidence.json \
  --repo '<owner>/<repository>'
```

这次实装还固定了两个不能由测试桩替代的 OMV 8 约束：配置部署模块名是 `samba`，不是 `smb`；
新建 `SMB.setShare` 必须提交 OMV 的新对象标记 UUID，再使用 OMV 返回的真实 UUID 做验证和回滚。
桥要求 `Config.applyChanges` 的结果明确包含 `samba`，不再接受空列表造成的假成功。

官方 8.5.6 安装脚本在导入带 `nameservers` 的 Debian Cloud Netplan 时还触发了上游字段不一致：
`40netplan.sh` 写 `dnsservers`，而同包模型字段为 `dnsnameservers`。本次只在一次性 VM 中做了一行
临时修正后继续验证，没有把 OMV 上游补丁打进 Echo 包。现在 `platform_preflight.py` 会识别
“错误 importer + 新模型 + 活跃 Netplan nameservers”的精确组合并阻断 Echo 安装；若没有活跃
nameservers，只返回潜在风险警告。它不能让已经失败的 OMV 首装倒退恢复，因此 OMV 首装仍必须
选用上游已修版本或经供应商批准、与精确包版本绑定的临时处置；不能由 Echo 静默改写上游文件。

`postinst` 不把 systemd 的“进程已启动”当成安装成功：它最多等待 15 秒，核对 socket 类型、
owner/group、模式和固定 `/health` 响应；任一步失败都会停止服务并让包保持未配置状态，要求修复
宿主后重试或回装上一份已验证包。

安装前必须先通过下文受管安装器的精确确认卸载旧桥。原生包会拒绝接管
`/var/lib/echo-os/omv-host/install-state.json`、手动 unit 或无清单的桥文件，不会覆盖来源不明的
宿主安装。包会创建动态 GID 的 `echo-omv` 系统组；compose 的 `PGID` 必须使用
`getent group echo-omv | cut -d: -f3` 取得的实际数字，不能假定为 1000。

安装后 OMV 的“服务 → Echo OS”会出现原生 Workbench 页面。当前页面只说明桥状态入口、Unix
socket 和受控写范围；真正的状态、家庭成员、SMB 和配额操作仍在 Echo 设置页完成，并继续经过
设备密码、单次 planId 审批、回读验证、回滚和审计。它不是磁盘、阵列或完整账户/共享管理界面。

### 兼容与迁移路径：受管宿主包

宿主不需要保留整个 Echo OS 开发仓库。发布流程会生成一个同时适用于 amd64/arm64 的最小包，
只含安装器、只读平台预检、systemd unit、桥代码和本文，并在包内记录固定文件集合、权限、大小和 SHA-256：

```bash
python3 deploy/omv/host_bundle.py build --output-directory dist
python3 deploy/omv/host_bundle.py verify \
  dist/echo-omv-host-<artifactId>.tar.gz
```

CI 也会构建并反向校验同样的 `echo-omv-host` 产物，附带与包内六个文件哈希一致的 SPDX 2.3
SBOM；main 分支发布构建还会用 GitHub OIDC/Sigstore 生成来源证明和 SBOM 证明。下载后先核对
同目录 `.sha256`，再用发布仓库身份验证证明：

```bash
gh attestation verify echo-omv-host-<artifactId>.tar.gz \
  --repo <owner>/<repository>
```

证明不可用时不能把普通 CI 下载当正式签名发布；`.sha256` 和包内校验只能发现内容变化，不能
单独证明发布者身份。验证后再解压，并进入唯一的 `echo-omv-host-<artifactId>` 根目录执行下面
命令。

先确定 Echo 主容器最终使用的 `PGID`；插口组必须是同一个数字 GID，示例使用 1000。

先运行只读预检。它核对 Debian 13、OMV 8 完整包版本、Linux 架构（amd64/arm64）、
OMV/lsblk/systemd 固定可执行文件、目标路径、现有安装和源码哈希，并输出本次安装专属确认语；
除只读执行 `dpkg-query` 外，不会创建组、写文件或启动服务：

```bash
cd /opt/echo-omv-host-<artifactId>
sudo python3 deploy/omv/echo_omv_host.py plan --gid 1000
```

检查 JSON 中的 `supported:true`、`distribution:debian`、`distributionVersion:13`、
`omvMajor:8`、`omvVersion`、`supportMatrix:debian-13+omv-8`、`platformPreflight.ready:true`、
`platformPreflight.smbHostnameCompatible:true`、`platformPreflight.netplan.compatible:true`、
`action`、`gid`、`bundleId` 和路径，
然后原样复制 `installConfirmation` 执行。
确认语与当前源码哈希和 GID 绑定，下面的值只是格式示例，不能照抄：

```bash
sudo python3 deploy/omv/echo_omv_host.py install --gid 1000 \
  --confirm 'INSTALL ECHO OMV BRIDGE 1000 <本次 bundleId>'
```

安装器只把桥所需的两个 Python 文件复制到 root 管理的
`/usr/lib/echo-os/omv-bridge`，不会让 root 服务直接执行普通用户可修改的仓库目录；systemd unit
位于 `/etc/systemd/system/echo-omv-bridge.service`，安装清单以 0600 保存到
`/var/lib/echo-os/omv-host/install-state.json`。需要时安装器会创建数字 GID 相同的专用
`echo-omv` 组，不会改变已有组。它会先校验 unit、再启动服务并检查健康和 0660 插口；任何一步
失败会恢复升级前的文件、服务状态和本次新建的组。

不要把插口改成 `0666`。检查宿主桥：

```bash
sudo systemctl status echo-omv-bridge.service
sudo stat -c '%A %U:%G %n' /run/echo-omv/omv.sock
sudo curl --unix-socket /run/echo-omv/omv.sock http://localhost/health
```

然后用 OMV override 启动 Echo；`PGID` 要与上面的插口组数字 GID 一致：

```bash
# 这里进入完整 Echo appliance 发布目录，不是最小宿主桥包。
cd /opt/echo-os/deploy/omv
PGID=1000 ECHO_OMV_ADMIN_URL=https://nas.example.com docker compose \
  -f ../appliance/docker-compose.yml \
  -f docker-compose.omv.yml \
  up -d
```

登录 Echo 后可以检查：

```text
GET /api/appliance/omv/status
GET /api/appliance/omv/health
GET /api/appliance/omv/filesystems
GET /api/appliance/omv/smart/devices
GET /api/appliance/omv/smart?devicefile=/dev/sda
GET /api/appliance/omv/topology
GET /api/appliance/omv/sharing
GET /api/appliance/omv/sharing/{shared-folder-uuid}/privileges
POST /api/appliance/omv/accounts/groups/plan
POST /api/appliance/omv/accounts/groups/apply
POST /api/appliance/omv/accounts/users/plan
POST /api/appliance/omv/accounts/users/apply
POST /api/appliance/omv/accounts/users/password/plan
POST /api/appliance/omv/accounts/users/password/apply
POST /api/appliance/omv/sharing/smb/plan
POST /api/appliance/omv/sharing/smb/apply
POST /api/appliance/omv/sharing/folders/plan
POST /api/appliance/omv/sharing/folders/apply
POST /api/appliance/omv/sharing/privileges/plan
POST /api/appliance/omv/sharing/privileges/apply
POST /api/appliance/omv/sharing/nfs/plan
POST /api/appliance/omv/sharing/nfs/apply
POST /api/appliance/omv/quota/plan
POST /api/appliance/omv/quota/apply
```

用户组 `plan` 的 body 是 `echo.omv.group-desired.v1`，只含严格小写的普通组名和备注；只能创建
空组。`apply` 使用 `omv.group.create:<planId>` 单次审批。家庭成员 `plan` 的 body 是
`echo.omv.user-desired.v1`，只含严格小写的新用户名、显示名、强密码和现有普通附加组；OMV 自动
home 必须关闭，成员固定 nologin、无 email/SSH key 且禁止自改资料。`apply` 使用
`omv.user.create:<planId>` 单次审批。密码进入 HMAC 计划但不出现在安全 desired、响应、日志、
进程参数、证据或审计。已有受限成员的密码重置使用 `echo.omv.user-password-desired.v1` 和独立
`omv.user.password.reset:<planId>` 审批，只替换密码并回读验证其他字段；其他更新和日常删除均拒绝。

共享文件夹 `plan` 的 body 是 `echo.omv.shared-folder-desired.v1`，只含目标挂载点 UUID、便携名称和
备注；相对目录严格由名称推导。`apply` 使用独立的
`omv.shared-folder.create:<planId>` 单次审批。该接口没有更新、删除、任意路径或 ACL 参数。

共享权限 `plan` 的 body 是 `echo.omv.share-privilege-desired.v1`，只含共享文件夹 UUID、一个
`user`/`group`、OMV 已枚举的对象名称，以及 `inherit`/`none`/`read`/`readWrite`。`apply` 使用
`omv.share-privilege.apply:<planId>` 单次审批。Echo 不通过该接口开放文件系统 ACL、递归权限或
账户变更，也不会把其他用户或组从当前权限表中丢失。

`plan` 的 body 是 `echo.omv.smb-share-desired.v1`，只含共享文件夹 UUID、启用、只读、可发现、
回收站和备注。`apply` 再提交同一 desired 与预览返回的 64 位 `planId`，并携带绑定
`omv.smb.apply:<planId>` 的 `X-Echo-Approval`。无变化计划不写 OMV，也不消耗高风险审批。

NFS `plan` 的 body 是 `echo.omv.nfs-share-desired.v1`，只含共享文件夹 UUID、一个私网 CIDR、
只读开关和备注；`apply` 使用独立的 `omv.nfs.apply:<planId>` 单次审批。Echo 不开放 NFS 删除或
任意 exports 参数，复杂规则继续交给 OMV Workbench。

配额 `plan` 的 body 是 `echo.omv.filesystem-quota-desired.v1`，只含文件系统 UUID、`user`/`group`、
对象名称和 `hardLimitBytes`。`apply` 提交同一 desired 与预览的 `planId`，并携带绑定
`omv.quota.apply:<planId>` 的单次审批。设置页输入单位为整数 GiB；0 表示取消硬限制。桥实际只
向 OMV 发送精确 KiB，不接受路径、共享文件夹 UUID、软限制或任意额外 quota 参数。

轮询和阈值可通过 compose 环境变量调整；非法值会拒绝启动，而不是静默退回不安全阈值：

| 变量 | 默认 | 有效范围 |
| --- | ---: | ---: |
| `ECHO_OMV_HEALTH_INTERVAL_SECONDS` | 300 | 60–86400 秒 |
| `ECHO_OMV_TEMP_WARNING_C` | 50 | 30–90°C，且低于严重值 |
| `ECHO_OMV_TEMP_CRITICAL_C` | 60 | 31–100°C，且高于提醒值 |
| `ECHO_OMV_CAPACITY_WARNING_PERCENT` | 90 | 50–99%，且低于严重值 |
| `ECHO_OMV_CAPACITY_CRITICAL_PERCENT` | 95 | 51–100%，且高于提醒值 |

Echo 会在设置页保留告警变化并写容器日志，但不擅自配置邮件/推送接收人；外部通知仍应在 OMV
官方通知设置中配置，避免 Echo 持有邮件或消息服务凭据。

在 OMV 宿主完成整栈部署后，使用仓库验收脚本一次核对当前仍是 Debian 13 + OMV 8、插口权限、
容器挂载、鉴权、固定方法和响应脱敏；脚本也会拒绝非 root 所有、模式异常、使用符号链接或仍
指向 `/opt/echo-os` 仓库的宿主桥安装（`--expected-gid` 必须与 `echo-omv` 数字 GID 一致）：

```bash
ECHO_ADMIN_PASSWORD="$ECHO_ADMIN_PASSWORD" python \
  deploy/appliance/verify-running-appliance.py \
  --require-clean-bundle --require-omv --expected-gid 1000
```

结果的 `omv.host_install` 必须包含 `support_matrix:debian-13+omv-8` 和实际 `omv_version`；如果
宿主安装后升级到其他 Debian/OMV 主版本，验收会失败，但仍可使用安装器的精确确认语安全卸载。
验收器默认自动识别 `/usr/lib/systemd/system` 的原生插件 unit 或 `/etc/systemd/system` 的受管
宿主包 unit，并分别输出 `install_mode:nativePluginPackage` 或 `install_mode:managedHostBundle`；
同机出现两份 unit 会拒绝，不能靠搜索顺序掩盖冲突。
上面的默认验收不修改 OMV。要把 SMB 控制从“协议和测试桩通过”提升为真机证据，先指定一个
**已有私有 SMB 规则**的专用测试共享文件夹 UUID，只生成可逆计划：

```bash
ECHO_ADMIN_PASSWORD="$ECHO_ADMIN_PASSWORD" python \
  deploy/appliance/verify-running-appliance.py \
  --require-clean-bundle --require-omv --expected-gid 1000 \
  --omv-smb-test-folder 11111111-2222-4333-8444-555555555555
```

确认 JSON 中 `changeFields` 只有 `comment`、`writeExecuted:false`，再原样复制
`confirmationRequired` 运行第二次：

```bash
ECHO_ADMIN_PASSWORD="$ECHO_ADMIN_PASSWORD" python \
  deploy/appliance/verify-running-appliance.py \
  --require-clean-bundle --require-omv --expected-gid 1000 \
  --omv-smb-test-folder 11111111-2222-4333-8444-555555555555 \
  --require-omv-smb-write \
  --omv-smb-write-confirm \
    'VERIFY ECHO OMV SMB WRITE 11111111-2222-4333-8444-555555555555'
```

该模式只把规则备注临时改成随机验收标记，回读确认后恢复启用、只读、发现、回收站和原备注的
完整快照，并核对两次 `omv.smb.apply` 的 attempted/succeeded 审计及 intentId。精确确认语和
`--require-omv-smb-write` 必须同时存在，缺一不会写。恢复第一次失败
会再走独立紧急恢复；即使紧急恢复成功，验收仍然失败，不能把有故障的部署标成通过。没有精确
确认语时不会执行写操作。不要把家庭唯一共享或承载唯一数据副本的规则用作测试对象。

用户/组文件系统硬配额也有独立的两阶段可逆写门。必须使用专用测试用户或组；探针限制只能比
当前限制更严格，永远不能在验收过程中放宽或取消限制。第一次只生成计划并输出原值：

```bash
ECHO_ADMIN_PASSWORD="$ECHO_ADMIN_PASSWORD" python \
  deploy/appliance/verify-running-appliance.py \
  --require-clean-bundle --require-omv --expected-gid 1000 \
  --omv-quota-test-filesystem aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee \
  --omv-quota-test-subject-type user \
  --omv-quota-test-subject-name echoverify \
  --omv-quota-test-bytes 1073741824
```

确认输出只有 `hardLimitBytes` 差异，`scope` 是 `filesystemUserOrGroup`、`writeExecuted:false`，
并确认 1 GiB 小于当前有限配额（当前值为 0 表示无限制）。然后原样复制 JSON 输出的
`confirmationRequired`，同时加入两个写参数：

```bash
ECHO_ADMIN_PASSWORD="$ECHO_ADMIN_PASSWORD" python \
  deploy/appliance/verify-running-appliance.py \
  --require-clean-bundle --require-omv --expected-gid 1000 \
  --omv-quota-test-filesystem aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee \
  --omv-quota-test-subject-type user \
  --omv-quota-test-subject-name echoverify \
  --omv-quota-test-bytes 1073741824 \
  --require-omv-quota-write \
  --omv-quota-write-confirm \
    'VERIFY ECHO OMV QUOTA WRITE aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee user echoverify FROM 0 TO 1073741824'
```

上面 `FROM 0` 只是示例，必须复制本机预览实际输出，不能手写猜测。确认语绑定文件系统、对象、
原限制和探针限制；当前状态变化后旧确认语自动失效。验收器临时应用更严格限制、回读，再恢复
原值并回读，最后核对两次 `omv.quota.apply` 审计。普通恢复失败会尝试独立紧急恢复，但整次验收
仍失败；紧急恢复也失败时必须立即人工检查该测试对象。这个门证明配置事务，不替代后续从本机、
SMB、NFS 写满配额的底层强制执行测试。

同一台测试 NAS 还应执行 1 GiB 文件门。先确保 NAS 根下已有专用 `verification` 目录并只预览：

```bash
ECHO_ADMIN_PASSWORD="$ECHO_ADMIN_PASSWORD" python \
  deploy/appliance/verify-running-appliance.py \
  --require-clean-bundle --require-omv --expected-gid 1000 \
  --nas-transfer-test-bytes 1073741824 --nas-transfer-test-path verification \
  --nas-transfer-restart-main
```

核对输出后复制其 `confirmationRequired`，再加 `--require-nas-transfer` 和
`--nas-transfer-write-confirm '<精确确认语>'` 执行。确认语会额外包含
`AND RESTART echo-os`；它不修改 OMV 配置，只短暂重启 Echo 主容器，通过文件 API 验证
持久会话恢复、摘要、完整/Range 下载和取消；唯一测试文件最后进入可恢复回收站，绝不自动清空
用户回收站。

未安装桥时，Echo 其余功能照常运行；状态会报告未配置或不可用，数据接口返回 503，不会回退
到直接读取宿主磁盘。

## 升级与卸载

原生插件包使用标准 Debian 生命周期：用经过摘要和证明验证的新 `.deb` 执行
`sudo apt install ./openmediavault-echo-os_<version>_all.deb` 升级；在专用测试机分别验证首次安装、
同版本重装、跨版本升级、失败升级、`apt remove` 和 `apt purge`。删除或 purge 会停止服务并移除
包管理的 unit、桥代码和 Workbench 文件，但有意保留 `echo-omv` 系统组；OMV 阵列、文件系统、
共享、用户权限和 Echo/NAS 数据不属于该包，绝不能被删除。卸载包前先停止使用该 socket 的 Echo
compose，卸载后再确认 `/run/echo-omv` 已消失且 NAS 数据逐项未变。

下面是受管宿主包的兼容升级/卸载流程，不要和原生 `.deb` 混装。

桥代码跟随 Echo OS 镜像/仓库版本。升级仓库后重新运行 `plan`，确认 `action` 为 `upgrade`，
再用它新输出的 `installConfirmation` 运行同一个 `install` 命令。安装器只覆盖清单内且哈希仍
匹配的旧文件；若有人手工改过 unit 或桥代码，它会拒绝覆盖并要求先人工审查。启动新版失败时
会恢复旧版文件和旧服务状态。宿主桥升级成功后，再用不可变镜像摘要运行 appliance 升级流程。

卸载先去掉 OMV compose override，然后从 `sudo ... plan` 输出中复制
`uninstallConfirmation`。卸载器仅移除清单内的 unit 和桥代码；OMV 文件系统、阵列、共享、
权限以及 Echo/NAS 数据都不在删除范围：

```bash
sudo python3 deploy/omv/echo_omv_host.py plan --gid 1000
sudo python3 deploy/omv/echo_omv_host.py uninstall --gid 1000 \
  --confirm 'UNINSTALL ECHO OMV BRIDGE <已安装的 bundleId>'
```

卸载收据写入 `/var/lib/echo-os/omv-host/last-uninstall.json`。若专用组已有明确成员，或系统判定
删除不安全，组会保留并在收据说明原因，不会让卸载半途失败。`/run/echo-omv` 是 systemd 临时
运行目录，服务停止后会自动清理。
