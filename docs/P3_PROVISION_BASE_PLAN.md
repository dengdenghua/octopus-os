# P3 · 整机镜像:Echo OS × 参考 NAS

> 分支:`p3-provision`(upstream = dengdenghua/echo-os @ `5b82381`)
> 状态:**规划 + 骨架已落地**(历史 NAS 管控面 41 测试全绿；当前 P3 原生窄写已接入并完成本机/VM 回归；装机链路待 Linux 验证)
> 前情:`docs/ECHO_OS_PLAN.md` §6 P3 / `docs/NATIVE_SHELL_PLAN.md`

---

## 1. 先说结论:这是缺口互补,不是硬凑

echo-os 的 P3 卡在一个具体的地方 —— 它自己的计划里写着:

> **P3 —— 整机镜像**:Debian stable + OMV 存储包(apt 融合,不 fork)
> §9 待决事项:**寄宿首选平台 CasaOS / OMV / 参考 NAS,三选一**

也就是说:**存储底座和装机方式至今未决**。而参考 NAS 的解包产物恰好就是这两个
问题的答案 —— 一个跑通的 Debian NAS 基座,和一个能用的 d-i 装机流程。

| echo-os 缺的 | 参考 NAS有 | 怎么拿 |
|---|---|---|
| 装机镜像 | d-i + 自研选盘 TUI | 抄**模式**,换官方钩子(§3.1) |
| 存储栈 | ZFS / mdadm / Samba / SMART | 直接用上游官方包,不碰参考 NAS二进制 |
| 统一命名空间 | `nasfs.ko` 挂 `/fs` | **不抄**,改 ZFS dataset + bind mount(§4.1) |
| 应用生态约定 | `/usr/local/apps/@appcenter/` | 抄目录约定(§3.3) |
| 单入口反向代理 | nginx + 每模块 unix socket | 抄架构(§3.2) |

反过来说,参考 NAS没有而 echo-os 能给的只有一样,但它是决定性的:
**Agent 是会话本身,而不是装在系统上的一个应用。**

---

## 2. 法律红线(先划清楚,再动手)

参考 NAS 的 `nas-bundle`、`app_center`、`nasfs.ko` 等是**闭源自研二进制**。
解包产物在我们手上只用于**学习架构决策**,以下三条是硬约束:

1. **不复制任何参考 NAS二进制、库、前端资源到 echo-os**;
2. **不反向分发**参考 NAS的 ISO、`rootfs.tgz` 或其中任何片段;
3. 本分支只实现**从参考 NAS学到的架构模式**,所有代码自己写。

这不是保守 —— 复制二进制除了法律风险,技术上也是死路:那些二进制绑定
`6.18.18.c1032-nas` 内核和参考 NAS私有 ABI,换个内核版本就跑不起来。

**判定标准很简单:我们抄的是"决策",不是"代码"。**

---

## 3. 四条照搬的模式(附参考 NAS侧证据)

### 3.1 装机:寄生式改造 d-i,不 fork 安装器

**参考 NAS的做法**(解包证据):

```
initrd/usr/sbin/debian-installer-startup:19   → 跑 nas-install(自研选盘 TUI)后 chvt 2
initrd/usr/lib/debian-installer-startup.d/S15lowmem:128 → 借 lowmem 钩子跑 nas-templates
```

整个 initrd 里只多 3 个自研文件:`nas-install`(600KB TUI)、`nas-grub`、
`nas-templates`(20KB,改写 debconf 模板做汉化)。**分区、网络、镜像校验、
grub 安装全部白拿。**

**我们的做法(更稳一档)**:不动 initrd,用 d-i 官方的 `preseed/early_command`
钩子从 ISO 上直接跑 TUI:

```sh
d-i preseed/early_command string /cdrom/echo-os/echo-install
```

为什么不照抄 initrd 注入:它依赖 d-i 内部文件结构,上游一改就碎;而 Debian
initrd 是**多段拼接**(early microcode + 压缩主段),重打包容易出错。
官方钩子跨版本稳定,代价为零。

- 载荷:`deploy/provision/installer/echo-install`(whiptail TUI,写 debconf 预置)
- 静态策略:`deploy/provision/installer/preseed.cfg`
- 组装:`deploy/provision/build-iso.sh`(解包 → 塞 `/echo/` → 改 append 行 → 重打包)

**关键取舍:重活不放在装机阶段**。ZFS 编译、Docker 安装、前端构建都要联网且
耗时,放进 d-i 失败会让整台机器装不起来。这里把它们推到**首次开机**
(`echo-firstboot.service`),可重试、可 `journalctl` 查。

### 3.2 单 nginx 入口 + 功能模块各自 socket

参考 NAS的 `usr/nas/nginx/conf/conf.d/` 下有 30 个 location 片段,每个功能模块
一个独立进程一个 unix socket(accountsrv、dsmgr、thumbnailer、photos、vm、
iscsi…),对外只暴露 80/443。

我们:`deploy/provision/base/echo-nginx.conf` —— 对外 80/443,appliance 只听
`127.0.0.1:8000`,WebSocket 升级头、流式输出超时、大文件上传限制全在一处配。

**一处比参考 NAS做得严**:参考 NAS的 nginx conf 里随包带了 `server.crt` / `server.key`。
若全系共用同一份,等于 TLS 私钥公开。我们强制**每设备首启生成独立自签证书**
(见 `setup-base.sh` 第 7 步)。

### 3.3 应用中心:目录约定,不要注册中心

参考 NAS:应用装到 `/usr/local/apps/@appcenter/<id>/ui/static/dist/`,nginx 用
`^~ /<id>/` + try_files 回落 SPA 的 index.html。**安装 = 放文件 + 写一个 conf
片段 + reload。** 没有注册中心、没有数据库、没有 migrations。

这条建议连目录命名一起照搬,P3 阶段接 `appliance/app_registry`(Docker label
级联 `sh.echo.*` → `casaos.*` → `homepage.*`)时,两个体系能对齐。

### 3.4 能力内置 / 应用外置

参考 NAS解包里最反直觉的一层:相册(`nas.photos`)、播放器(`nas.media`)、音乐
(`nas.music`)**都不在基础镜像里**,`nas_photos.sock` 全系统只有 nginx 配置
引用,没有任何二进制创建它 —— 它们是应用中心的可选应用。

但**底座是内置的而且很重**:`mediasrv` 自带 FFmpeg 7.2.2 + 45 个私有库(Intel
QSV / NVIDIA / Vulkan 全硬解),`imagesrv` 带 ImageMagick + libheif(iPhone
HEIC)+ libraw + libexiv2。

分层理由很实在:不装相册的用户不承担它的资源占用;相册能独立于系统版本迭代;
系统升级不用捆绑一堆应用。**建议连这个分层一起照搬。**

---

## 4. 四条明确不抄的

### 4.1 `nasfs` —— 自研内核文件系统

参考 NAS用 `nasfs.ko.xz` 挂 `/fs`,做 `/fs/<uid>/{smb,nfs,ftp,webdav}` 统一命名
空间,并自研 `nas_acl`(内核头 `nas_acl.h` + `nasacl` 命令 + `libnasacl.so`),
为此**整个 Samba 全家桶 19 个包全部打补丁重建**(`4.22.8+dfsg-0+deb13u1~bpo12+nas.0+b20260812.47`)。

代价:每跟一个内核版本都要重新适配。

**替代方案**:ZFS dataset + bind mount,零内核代码,覆盖九成场景。省下的力气
全部投到管控面 —— 那是用户真正能感知的部分。

### 4.2 单包 1.2 GB

参考 NAS的 `nas-bundle` 一个包 1.2 GB,包含 99 个二进制 + www + nginx。升级粒度太粗,
改一行 Web UI 要重下 1.2 GB。**按功能域拆细**。

### 4.3 随包 TLS 私钥

见 §3.2。

### 4.4 rootfs 无独立校验

参考 NAS的 `md5sum.txt` 只覆盖 ISO 层,**不覆盖 `rootfs.tgz`**(2.8G 的根文件系统
没有任何完整性校验)。我们自己做得补上:已实现的做法是共享清单
`shares.json` 作为唯一真源,配置可全量重建并留 `.bak`;系统级完整性走
A/B 原子更新(见 §6 M4)。

---

## 5. 已落地的代码(本分支)

| 路径 | 说明 | 状态 |
|---|---|---|
| `appliance/nas/storage.py` | 历史磁盘/池/数据集/SMART 只读探测 | ✅ 41 测试(兼容保留) |
| `appliance/nas/shares.py` | 历史 SMB/NFS 共享配置面 | ✅ 41 测试(兼容保留) |
| `appliance/nas/router.py` | 历史 NAS HTTP API,生产扩展未挂载 | ✅ 兼容测试 |
| `appliance/native_storage.py` | 当前主机原生存储探测与受控窄写面 | ✅ 当前 P3 入口 |
| `appliance/native_storage_routes.py` | `/storage` 与 `/omv` 兼容路由、审批/审计封装 | ✅ 当前 P3 入口 |
| `appliance/extension.py` | 挂载原生存储路由(OMV 可选桥不再是默认权威) | ✅ |
| `tests/appliance/test_native_storage.py` | 原生账户、共享、ACL、SMB/NFS、配额回归 | ✅ |
| `deploy/provision/**` | 装机 ISO 组装 + 首次开机脚本 | ⚠️ 待 Linux 验证 |

### NAS 管控面设计要点

- **不自研存储**:ZFS 用 OpenZFS 官方包,阵列 mdadm/lvm,共享 Samba/NFS。
- **优雅降级**:zfs / mdadm / smartctl 任一不在 PATH,对应能力返回
  `available: false`,不拖垮整个面板 —— 与 app_registry 对 Docker 的处理一致。
- **共享清单是唯一真源**:`shares.json` 可全量重建配置;落盘用 tmp +
  `os.replace` 原子替换并留 `.bak`,写一半断电不会留半份配置。
- **输入校验到字节级**:设备名白名单 `^(sd[a-z]+|nvme\d+n\d+|md\d+|vd[a-z]+)$`;
  共享名 `[A-Za-z0-9._-]{1,64}`;路径必须落在允许根内。SMB 注释值剔除
  `[] ; #` 与换行 —— 不留任何"依赖解析器行为"的余地。
- **写操作边界**:旧 `appliance/nas` 路由仍仅作兼容测试，不进入生产扩展；当前原生窄写面统一使用
  `desired → plan → 管理员单次审批 → apply → 回读验证/安全回滚 → 审计`。已开放基础共享文件夹、
  空目录安全删除、根目录 POSIX ACL、私有 SMB/NFS 规则、受限账户/组和用户/组硬配额；复杂磁盘与池生命周期继续关闭，
  不以兼容路由代替审批。

### 硬件兼容基线（2026-09-06）

对照解包系统单独维护 firmware/OOT 驱动包的做法，Echo 的 netinst 首启路线与 mkosi
镜像现在都显式安装 Debian 官方的 Linux 通用、Realtek、Intel Wi-Fi/核显、Atheros、
Broadcom、MediaTek、AMD GPU 固件，以及 Intel/AMD microcode；同时固定安装 `nvme-cli`、
`pciutils`、`usbutils`、`ethtool` 和 `lm-sensors`，使 NVMe、PCI、USB、网卡驱动/链路和
温度传感器问题可以在离线现场被诊断。包名已在当前 Debian 13 基线的 apt 索引逐项确认，
镜像静态门会防止这些包被后续瘦身误删。

这里没有复制解包系统的 Realtek/NVIDIA 私有模块，也不把“包已安装”算作硬件通过。
OOT 驱动只应在明确设备 ID、上游内核确实不支持、具备 Secure Boot 签名与目标机回归矩阵时
独立引入；当前仍需 N100、常见 2.5/10 GbE、SATA HBA、NVMe、Intel/AMD/NVIDIA 转码和
休眠/唤醒的实体机验收。

---

## 6. 里程碑

| 阶段 | 内容 | 验收 |
|---|---|---|
| **M1** | NAS 管控面(存储/共享/健康) | ✅ 41 测试全绿 |
| **M2** | 装机 ISO 在 VM 跑通 | 🟡 Stage A 已完成全新盘装机、首启与重启验证；正式 ISO 的 UEFI/BIOS 双引导仍待验收 |
| **M3** | 存储池与共享端到端 | UI 建 ZFS 池 → 建 SMB 共享 → 局域网可访问 |
| **M4** | 不可变系统 + A/B 原子更新 | 更新失败自动回滚 |
| **M5** | 应用中心(Docker label 级联) | 装 Jellyfin → Dock 出图标 → 可打开 |
| **M6** | 媒体栈(FFmpeg 硬解) + 相册应用 | 硬解转码可验证 |

### 真机验证清单(M2 起)

- [ ] VM(UEFI + BIOS 各一遍):装机全流程
- [x] 首次开机:`journalctl -u echo-firstboot` 无 ERROR,10 个可重入哨兵全过
- [ ] `zpool status` / `smbclient -L localhost` 正常
- [ ] HDMI 接显示器:cage → Electron 全屏桌面,点图标起应用
- [ ] 无头模式:`ECHO_HDMI_SHELL=off` 不装图形栈
- [ ] N100 迷你主机实机(16GB 内存基线)
- [ ] 断电测试:装机中途断电 → 重启能续跑

2026-09-06 在提交 `1aad5a80b88829f58b2fd65c6d27f85e705dbcc7` 上完成一次
20 GiB 空白系统盘的 Stage A 全新安装与冷重启验证。安装器使用当前 preseed/initrd
载荷并挂载 Debian 13.6 netinst ISO；首启 10/10 哨兵完成，error-priority 日志为空，
安装后的 provision 源码哈希与当前提交一致。重启后 `echo-appliance`、nginx、
`echo-shell` 均为 active，Web/API 可从宿主机访问，五个存储维护 timer 已启用；
ZFS 2.3.9 已针对运行内核完成 DKMS 安装并可加载，`zfs-import-cache` 成功执行。
本地证据 `_vmtest/clean_firstboot_current_result.json` 的 SHA-256 为
`a58b25bde64afc40dbd7d18c634cf6f8b697e317d6bd0545c24d92ae8344f363`。

---

## 7. 与上游的关系

- 分支 `p3-provision`,`upstream` 指向 `dengdenghua/echo-os`;
- 所有 OS 专属代码放 `appliance/` 与 `deploy/`,**不碰 runtime**(沿用
  `docs/ECHO_OS_PLAN.md` §4 的 fork 管理策略);
- 定期 `git fetch upstream && git merge upstream/os-main`。
