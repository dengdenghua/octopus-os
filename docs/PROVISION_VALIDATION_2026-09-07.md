# 装机链核查：2026-09-07

当前结论：完整 NAS 交付尚未通过。此前提交、构建成功或静态测试通过不能替代
当前版本的干净安装、首启、重启及存储客户端验证。NAS 的其他待验收项继续以
`NAS_DELIVERY_STATUS.md` 为索引，其中旧版本证据不代表当前工作树已验收。

## 已重新确认的事实

- Windows 宿主机曾出现 C 盘剩余空间为 0；VM 随后出现 EXT4 I/O 错误。
  这些运行及其产物不能作为成功验收证据。当前已恢复宿主机可用空间。
- 构建 VM 的 `/tmp` 是 **2 GiB tmpfs**，而 `/var/tmp` 位于根分区。
  此前构建器默认在 `/tmp` 暂存；反复清理根分区不会增加该 tmpfs 的容量。
- 构建器现默认使用 `/var/tmp`，仍支持 `TMPDIR`，并在下载前检测实际暂存
  文件系统至少有 6 GiB 可用空间。该检查只是下限，不能检测虚拟磁盘所在宿主机
  的剩余容量；较大载荷仍需另行预留空间。
- Linux 上实际运行构建脚本的四项容量准入测试通过：不足时提前拒绝、容量信息
  非法时拒绝、足够时进入 ISO 检查、未设置 TMPDIR 时使用 `/var/tmp`。
  测试同时检查自定义暂存目录被清理。原有装机静态测试 29 项通过。
- 新下载的 `debian-13.6.0-amd64-netinst.iso` 已与 Debian 官方 HTTPS 发布目录的
  `SHA256SUMS` 核对一致。此处只记录校验和验证，不声称已验证 GPG 发布签名。

## 完整诊断介质发现的新问题

显式使用 `TMPDIR=/mnt/build` 后，`c0a39f5` 的五载荷诊断镜像成功构建，
大小约 1622 MiB，SHA-256 为
`5d3d7b1bd15a341a178a7d6a3c71521d29d118b9509feb8beb6b1c809e8c7596`。
这是复用旧载荷的诊断产物，不是本次工作树的正式发布包。

全量提取并运行 `md5sum --quiet -c md5sum.txt` 后，仅两个条目失败：
`isolinux/isolinux.bin` 和 `isolinux/boot.cat`。构建器在打包前记录了它们的摘要，
随后 xorriso 改写 boot-info-table 并重新生成引导目录，导致最终清单失效。
构建器现排除这两个会被改写的文件，其余普通文件继续纳入校验。

新增 Linux 行为测试使用真实 xorriso 打包、提取小型合成介质，确认两个文件确实
发生变化、修正后的清单通过，并在篡改普通载荷后正确拒绝；该测试不验证启动能力。
Linux 行为测试合计 5 项通过；Windows 为 29 项静态测试通过、5 项 Linux 测试跳过。
诊断 ISO 的 El Torito 目录同时列出 BIOS 和 UEFI 入口，但还没有完成实际启动验收。

随后使用修正后的清单生成逻辑，重封装完整诊断介质并保留引导入口，再次全量提取
后，清单中的全部文件均通过 `md5sum --quiet -c md5sum.txt`。修正后诊断 ISO：
`/mnt/build/echo-os-c0a39f5-checksum-diagnostic.iso`，SHA-256 为
`90802d744b33762f07598093bff976d34e64f524947f8d9bbc9e8c90649a508a`。
这证明完整介质的清单修正有效，不代表重新构建了当前源码或完成了干净安装。

从本次诊断 ISO 提取的安装入口 SHA-256（禁止与旧 VM 测试 initrd 混用）：

- `install.amd/vmlinuz`: `e7667ff961fcf0f872e2618a930454a6362ce58995f386431f60a5169c85f41a`
- `install.amd/initrd.gz`: `a288c7f5da42ae931e68c236aa3a6441864f99d299874b1d24481c834ecec27a`

## 对上一轮结论的修正

### 本轮新增的装机保护与测试门修复

- 发现选盘界面原先只排除零容量磁盘，但预置 EFI/swap/根分区的最小容量和
  为 512 + 2048 + 30000 MB。现将可选系统盘下限设为 32 GiB（约 34.4 GB），
  并在清空确认前及提交 debconf 分区预置前复查所选磁盘容量与候选身份。
  这不是稳定硬件身份/热插拔竞争的完整防护，不能据此声称解决所有换盘竞争。
- Linux 实跑 installer 冒烟测试 31 项通过，覆盖过小/未知容量在清空确认前拒绝、
  恰好达到下限可用、非候选磁盘拒绝；装机合同测试更新后 30 项通过。
  测试使用独立临时目录和交互桩，不操作实际磁盘；新保护尚未嵌入正在运行的诊断 ISO。
- 公开源码测试入口原先因未登记 `test_iso_workspace_admission.py` 与
  `test_provision_storage_stack.py` 在收集前退出；现补齐分类，保留其他会话的已有修改。
- 使用项目 `.venv` 完整测试环境执行公开入口，第一次结果为 2540 passed、105 skipped、
  4 failed。三项写租约测试缺少 `mode=code`，实际被工作区写权限拒绝；另一项回滚测试
  手工注入 token=1，但持久化 leaseCounter 仍为 0，导致准备数据时被完整性校验拒绝。
  修复仅调整测试前置条件：显式声明代码工作模式、使用原子的 `next_lease_token()`
  分配令牌，未放宽生产权限或存储校验。相关测试 77 项通过，完整公开入口第二次运行
  **2544 passed、105 skipped，退出码 0**（466.86 秒）。跳过项包含 Linux/POSIX
  专用用例，不把这项 Windows 结果当作 Linux 全覆盖或正式发布验收。
  原始日志位于 `C:\vmtest\install-diagnostic-20260907\public-source-tests-rerun.log`，
  SHA-256 为 `ac065ed6dbe593798a64a233f75fb981f7c270b94138e7518fbfb07ab9b59e6f`。

随后在同一当前工作树中完成了包含最新 SMB 同名账户保护的完整公开源码门禁：
**2568 passed、105 skipped，退出码 0**（368.63 秒）。日志位于
`C:\vmtest\install-diagnostic-20260907\public-source-tests-final-current2.log`，
SHA-256 为 `7a3c58d277d4d4096f3b2baa6c894556164aba59ec8eceb0dd1a452b7239cff2`。
105 个跳过项是 Windows 宿主缺少 POSIX/Linux 实机语义的明确用例，不能替代 Linux
安装、跨设备客户端或正式制品验收。

### 当前工作树的存储与 Web 安全回归

2026-09-07 重新运行以下测试文件：`test_native_storage.py`、
`test_native_storage_pool.py`、`test_native_storage_observation.py`、
`test_native_storage_write_readiness.py`、`test_web_security.py`，结果为
**220 passed、2 skipped**。FastAPI TestClient 另有一条 httpx 兼容层弃用警告，
不将这组 Windows/mocked 测试当作 Linux 存储或浏览器端到端验收。

代码回读确认旧待办清单已经过时：privileges 与 NFS 的 plan/apply 路由已接入
原生实现和审批写入流程，不再是 501 占位；Web 安全测试已覆盖同协议、同端口的
`localhost`、`127.0.0.1`、`::1` 别名允许，以及跨端口请求继续返回 403。
这些是现有实现的重新核验，本轮未修改该存储或安全代码。完整浏览器审批流程和
当前发布制品上的真实 ACL/NFS 客户端行为仍需独立验收。

### CD APT 选项的含义

提交 `c0a39f5` 增加的 `apt-setup/cdrom/set-first=false` 仅用于跳过额外介质扫描。
已从该 Debian ISO 中提取 `apt-cdrom-setup_0.198_all.udeb`，核对
`usr/lib/apt-setup/generators/40cdrom` 和 `41cdset`：前者仍会注册当前 CD APT 源，
后者才消费该选项。因此“已禁用 CD 源”“已修复 GRUB 卷标问题”均没有依据。
该选项可保留为单介质安装策略，但 GRUB 卡点必须重新验证。

旧的 `initrd-vmtest.gz` 也不能用作当前正式 preseed 的验收入口；后续安装测试
必须从本次 ISO 提取内核/initrd，或直接由该 ISO 引导，并记录来源与哈希。

## 仍需完成

### 当前诊断 VM（2026-09-07，安装完成、首启超时后续跑成功）

- 宿主目录：`C:\vmtest\install-diagnostic-20260907`。已复制修正后的诊断 ISO，
  宿主端 SHA-256 与上述 `90802d74...` 完整摘要一致。
- QEMU q35、4 GiB 内存、4 vCPU、新建 UEFI 变量、新建 40 GiB 空白 qcow2；
  块设备回读确认仅挂该空盘、只读诊断 ISO 与固件，不挂旧系统或数据盘。
- 已直接从 ISO 的 UEFI DVD 入口进入 Echo TUI，完成欢迎、选盘、清空确认、
  主机名和两次管理员密码输入，安装器开始加载分区等组件。没有替换 initrd、
  修改 preseed 或绕过实际 TUI。截图保存在同一宿主目录。
- 为观测安装过程，仅在安装器第二控制台将 `/var/log/syslog` 追踪输出到串口；
  宿主记录为 `serial-install.log`，用于核对以下安装阶段结果，不代替首启/重启验收。
- 本次日志在 09:31:18 UTC 明确记录 `grub-install ran successfully`，并回读
  UEFI `debian` 启动项指向 `\EFI\debian\shimx64.efi`；没有复现此前换盘提示。
  随后从目标盘回读 `/target/var/log/echo-stageA.txt` 得到 `stageA-ok`，`/target/opt`
  下五份载荷均存在：源码 bundle 44,458,577 字节、Web 25,038,865 字节、Python
  34,798,498 字节、Codex 122,855,983 字节、系统包仓 486,368,416 字节。
  安装器已执行完最后的微码/initramfs 配置，于 09:34:29 UTC 正常卸载目标文件系统，
  QEMU 随安装器重启请求按 `-no-reboot` 配置退出，stderr 为空。
  随后启动不挂 ISO、`restrict=on` 限制外网且只显式转发 SSH 的首次开机；
  首启成功与否仍需验证。上述记录只证明该诊断介质完成了本次安装流程。
- 首启已从目标 UEFI `debian` 启动项进入系统，SSH 登录成功，内核为
  `6.12.94+deb13-amd64`，boot ID 为 `f0fc40e6-7779-43e8-a36a-f839fac15a59`。
  `/sys/firmware/efi` 存在，`stageA-ok` 可回读；外部域名解析失败，直接连接
  `1.1.1.1:443` 返回 `Network is unreachable`，确认不只是依赖 DNS 失败模拟断网。
- `echo-firstboot` 已校验本地系统包仓，ZFS 2.3.9 的 DKMS 状态为当前内核
  `installed`，`/sys/module/zfs/version` 回读 `2.3.9-0+deb13u1`，确认模块已加载。
  18:33:59 +08:00 完成存储阶段，随后 Docker 安装完成且服务为 `active`；
  无头模式跳过 NodeSource/npm，源码从介质恢复后进入 Python wheelhouse 安装。
- 第一次首启服务实际从 17:35:46 运行至 18:35:46 +08:00，达到配置的 60 分钟
  上限，被 systemd 终止：`ActiveState=failed`、`Result=timeout`、
  `ExecMainStatus=15`。当时 pip 正在安装本地 wheel；日志记录 2.2 GiB 内存峰值。
  本 VM 使用 QEMU 软件模拟，ZFS 编译耗时较长，但不能据此把超时判作通过，
  也不能推断物理硬件必然成功。**首次开机一次成功验收失败**。
- 当前源码已将 `echo-firstboot.service` 的全局启动时限改为 `infinity`：各网络和
  包管理操作仍保留自身有界重试，阶段哨兵仍负责断点续跑，但低功耗 CPU 不会再因
  总耗时超过一小时而被 systemd 强制终止。该改动尚未进入新的正式 ISO；只有使用
  最终源码重建并完成一次全新首启后，才能关闭本条失败记录。
- 超时后确认服务 cgroup 已清空、无 apt/dpkg/pip 活动进程，`dpkg --audit`
  无输出，Python 载荷仍存在，`echo-py` 和 `services` 完成标记均未生成；
  `apt/storage/docker/node/echo-src` 标记存在。18:36:48 +08:00 在同一 VM
  显式启动既有 firstboot 服务验证断点续跑，未修改服务超时、载荷或完成标记。
  续跑按标记跳过前五个阶段，重新安装 Python 后完成 Web、Codex 和服务配置，
  18:39:52 正常退出：`ActiveState=active`、`SubState=exited`、`Result=success`。
  这证明本次中断位置可手工续跑，不能将其合并为第一次首启成功。
- 随后独立检查后端/nginx/Docker 均为运行状态、后端重启次数为 0、系统无失败单元；
  HTTP 根入口和经 nginx/直连后端的 `/api/health` 均返回 200，健康正文
  `status=ok`、trace store ready、`restartRequired=false`。HTTPS 自签入口在
  显式跳过证书信任验证的探测中返回 200，不代表证书受客户端信任。
  Python `pip check` 无损坏依赖，`codex --version` 为 `codex-cli 0.149.0`。
  健康接口同时显示 `verifiedBundle=false`，不能把该诊断运行当作正式绑定发布。
- 端口检查确认 HTTP 后端仅监听 `127.0.0.1:8000`，但同一进程还监听
  `0.0.0.0:8765`。该端口对应 Tentacle LAN WebSocket；重启后在此诊断制品
  上分别发送缺失令牌和错误令牌的 `device/hello`，均返回错误码 -32099 并以
  1008 关闭连接，没有注册成功。探测从 guest 回环发起，不代表跨设备网络全覆盖，
  也不能声称应用只有 nginx 对外入口。
- 完成上述检查后发出正常重启请求，原 QEMU PID 18464 已退出且 stderr 为空。
  使用同一系统盘、UEFI 变量和受限网络重新启动，不挂 ISO，新的 QEMU PID 为
  3508；SSH 仍为 `127.0.0.1:2223`，监控端口为 4546。
  串口日志独立保存为 `serial-offline-reboot.log`；后续先检查该运行，
  不能因日志暂时不增长而重新开始安装。
- 重启后 boot ID 变为 `08de653a-c5ef-4e97-b1c6-23e982677753`，后端、nginx、
  Docker 自动运行，ZFS 模块自动加载；系统无失败单元，后端 `NRestarts=0`。
  firstboot 因完成标记而 `ConditionResult=no`、inactive，符合预期，不是失败。
  `services` 标记和认证文件 mtime 分别保持 1788777592、1788777625，未重新初始化。
  本次只检查认证文件存在及时间，不声称验证所有配置内容和用户数据持久性。
- 后端服务 18:42:48 启动进程、18:43:57 才完成应用启动并监听，约 69 秒冷启动
  窗口内 nginx API 返回 502；软件模拟环境的时间不能外推到物理机，但页面应有
  就绪提示。随后健康接口正文为 `status=ok`、`restartRequired=false`，根页面 200。
  一次受保护健康接口探测仍在 15 秒内未收到响应；18:45:31 再次检查时无令牌请求
  在 0.035 秒内返回 401，普通健康接口在 0.043 秒内返回 200。保留前次超时记录，
  不把这组基础复查称为长稳或完整浏览器验收。
- 当前源码的 nginx 已把后端尚未监听时的 502/504 转换为带 `Retry-After: 3`、
  `Cache-Control: no-store` 和固定 `appliance_starting` 错误码的 503；后端主动返回的
  503 不会被覆盖。前端认证入口现在只对这一精确错误码自动重试，遵守并钳制
  `Retry-After`，120 秒后转为可手动重试，且在页面卸载时取消等待；普通 503 不会被
  掩盖。登录页会等待认证恢复后再探测登录方式，不会用自身约 7.5 秒的短重试提前
  得出“服务未启用”，启动等待页会显示明确提示。实际 NAS 入口 `/#/desktop` 也已
  改为失败关闭：主认证未完成、appliance 会话探测网络错误、5xx 或畸形 200 时均不
  渲染桌面；只有该兼容接口明确返回 404 时才按非 appliance 开发模式放行。相关
  API/Provider/登录页/路由回归 34 项、TypeScript、ESLint 和生产构建均通过。
  本机 Vite + Chromium 路由拦截另有 3 项通过，验证结构化冷启动 503 自动恢复到设备
  登录、普通 503 不误重试，以及 appliance 状态 503 时桌面保持关闭。该 nginx 配置
  已由本 VM 的 nginx 1.26.3 对空上游实际返回验证，但上述浏览器测试使用本机拦截，
  诊断镜像仍保留旧配置；正式制品仍需复测完整浏览器启动体验。
- 启动日志另有 `clip_studio` 缺少 `av` 的导入错误。当前工作树已改为按功能延迟
  加载可选媒体依赖：缺少 `av` 时插件本身及图片/工程能力仍可加载，视频能力明确
  报告依赖缺失；对应 readiness 回归 17 项已通过。诊断运行使用旧载荷，仍需用最终
  源码重建后验证，不能只凭本地回归消除镜像中的实际功能缺口。
- 日志也确认该诊断介质在安装阶段从 Debian 在线下载了附加依赖。因此即使其后续
  受控断网首启通过，也不能将该介质称为“全离线装机”。当前源码随后新增严格 NAS
  release 模式：要求官方 SHA-256 绑定的本地 Debian ISO，以及同一源码 tree 的 Web、
  Python wheelhouse、原生 Codex 和 system-deb 完整闭包；该模式会清空 d-i 的 tasksel/
  pkgsel 在线附加包，关闭安装镜像源，并让首次开机在任何离线合同字段缺失时失败关闭。
  严格入口现额外在 ISO 内生成确定性的 `release-manifest.json`，绑定基础 ISO、源码
  commit/tree、五份载荷和运行时身份；首启会先校验源码 bundle 摘要，安装后保留清单
  供审计。构建 wrapper 还会重开并提取成品，复核 BIOS/UEFI El Torito 入口、介质
  `md5sum.txt`、全部载荷摘要与 Git bundle/tree 完整性。相关装机合同
  **39 passed、6 skipped**；6 个 Bash 入口通过语法
  检查。6 个跳过项需要 Linux 的 xorriso/cpio 环境，当前还没有从干净工作树构建严格
  ISO 或完成断网安装，因而这里只证明发布门和制品核验器源码已接线，不声称全离线
  制品已验收；manifest 也不替代发布签名。
- 架构复核确认本目录属于 d-i 兼容/诊断路径；生产权威路径是
  `packaging/image` 的 dm-verity/Secure Boot/A-B raw image 与 `deploy/installer`
  的 GPG 签名整盘安装包。当前 Windows 主机重新执行该正式路径的可移植合同：安装包
  验证 **25 passed**，更新验证 **20 passed、4 skipped**，镜像身份/证据/runner 策略
  **62 passed、4 skipped**。8 个跳过项明确依赖 POSIX 所有权、锁或直接 shell；这组
  结果证明格式、摘要、签名输入边界和证据绑定逻辑，不替代 Linux mkosi/QEMU 实跑。
- 构建 VM 已通过 ACPI 正常关机以释放内存；未删除它的磁盘和诊断产物。

### 原生账户 HTTP 写面阻断（同一诊断 VM，2026-09-07）

重启后继续用原安装制品验收共享控制面，未替换源码、认证文件或修改管理员密码。
诊断脚本使用该设备首启生成的原管理员凭据登录，凭据只在进程内消费、不写入
报告。新建的 256 MiB 普通镜像文件通过 loop 挂为独立 ext4 测试卷；未格式化
任何块设备或重分区系统盘。

- 实际 HTTP 创建共享文件夹：无审批 apply 返回 403；密码复核签发审批后
  apply 成功、`verified=true`，目录真实存在。
- 下一步创建存储用户时，`/api/appliance/omv/accounts/users/plan` 对合法随机
  密码返回 422：`user password must be 12-128 characters, contain no controls,
  and differ from the account name`。诊断未继续执行 SMB、ACL、NFS 客户端测试，
  这些项目仍未获得本制品的通过证据。
- 根因也存在于当前源码：`UserDesiredState` / `UserPasswordDesiredState` 使用
  Pydantic `SecretStr`，原生路由直接 `model_dump()` 后交给要求 `str` 的协议
  校验器。旧桥接路由已有 `to_wire()` 转换，原生路由遗漏；创建和重置密码均受影响。
- 本轮仅修复原生路由的四个 plan/apply 入口：在可信内部边界调用既有 `to_wire()`，
  apply 的转换错误继续映射到 422，不放宽密码规则、计划绑定或审批门。
  未修改 `native_storage.py`、密码哈希、OS 命令或秘密审计策略。
- 新增回归保留真实原生校验、规划和应用函数，只模拟 OS 账户子进程及审批/审计
  接收器。修复前创建/重置两条合法 HTTP 流均失败，修复后新增 10 项通过；覆盖
  明文密码仅到内部写入、响应/审计不含密码、过期计划拒绝、无审批不写、非法输入
  plan/apply 都返回 422。原生存储、写面 readiness、旧桥接路由及审批测试合计
  **163 passed、2 skipped**；跳过项为 Windows 上的 POSIX 路径断言。
  这不是 Linux 真实账户写入通过，VM 仍保留原制品以保存失败证据。
- 失败后经独立计划和审批删除唯一空测试共享，确认用户未创建、无 NFS exports，
  卸载 loop 文件系统并移除空挂载点；保留镜像文件与失败报告。应用继续 active，
  `NRestarts=0`。当时临时诊断服务 `echo-diagnostic-sharing` 保持 failed 供检查，
  不与生产服务失败混淆；本轮验证结束后已删除三个临时诊断 unit 并清除 failed
  状态，当前 VM 无 failed unit。

宿主诊断脚本为 `C:\vmtest\install-diagnostic-20260907\native_sharing_probe.py`；
VM 对应脚本 SHA-256 为
`9b571d67d33d6464a5a06a45ef114e5993ea23c6b90008e791610bb787f93810`。
VM 失败与清理报告 `/var/tmp/echo-sharing-diagnostic-20260907/result.json` 的
SHA-256 为 `ce930629cd97f06a1ad5feea02248dcaa88ddde8cf81f25b219c4de184a93a77`。
报告仍为 `passed=false`，不得因后续清理或本地修复改判原镜像成功。

### 修改后 VM 的账户、SMB/NFS 协议验证（2026-09-07）

为继续验证上述修复，本轮在同一隔离 VM 临时部署了明确标记的诊断修复，
**不将它作为原 ISO 或完整当前工作树的验收**。先确认源码和 site-packages 中的
对应文件一致，再分别备份两份路由、两份原生存储实现。测试没有替换认证库。

首先，创建用户和根目录 ACL 经真实 HTTP 计划、未审批 403、密码复核审批、apply
成功，OS/Samba 账户和 access/default ACL 均回读确认。原诊断将共享名和用户名
设为同一值，Samba 拒绝这种命名；后续经审批重命名测试共享，并实际验证密码重置。
该同名限制已前移到当前源码的计划阶段提示；已有旧制品仍会在 Samba 执行阶段报错，
不能报作完整产品体验。Unix-only 的真实运行会查询本机账户；Windows 协议单元测试
仍由 mock 提供账户能力，真实 apply 在缺失 Unix helpers 时失败关闭。

继续用真实 SMB 客户端时发现两处部署/实现问题：

- Debian 默认 usershare 定义目录为 root:sambashare 1770；新 NAS 用户只加入 users，
  无法遍历该目录。Samba 日志明确记录读取测试 usershare 文件时权限不足，
  `runuser` 下的 `stat` 也失败。目录权限控制 usershare 定义的访问与创建，
  参见 [Samba 官方 usershare 说明](https://www.samba.org/samba/docs/4.20/man-html/net.8.html)。
- 原实现的 `users:f` 被解析为 `BUILTIN\\Users`，定义文件实际保存
  `S-1-5-32-545:f`，不是 Linux users 组。仅添加目录只读 ACL 仍不能连接；
  临时同时使用 Linux 组 SID `S-1-22-2-100` 后真实 SMB 读写和摘要通过。
  该 Unix SID 形式亦见 [Samba 开发者说明](https://lists.samba.org/archive/samba/2008-March/139247.html)。
  隔离对照后均恢复原定义与目录 ACL，没有用 Everyone 或添加 sambashare 写权限绕过。
- 同名测试还确认 Samba 会拒绝与本机用户重名的 usershare。当前计划阶段会先拒绝
  启用该共享，避免管理员审批后才收到 503；禁用/移除已有规则仍保持可执行。

源码修复现在按实际 users GID 构造 Unix 组 SID，缺组或非法 GID 失败关闭；
明确 `guest_ok=n`，回读限定唯一目标组和权限，旧错误 ACL 需要重新审批更新。
计划摘要也绑定现有 usershare 配置和目标组身份，防止外部配置改变仍沿用旧计划。
首启新增 `configure_native_samba_usershares`：验证默认路径、root 所有权、无符号链接、
祖先目录不可被组/其他用户写入、注册目录保留 sticky 且无 others 权限及既有 ACL mask 后，
仅添加 users 组 r-x，保留 mask，不授予定义写入权。
自定义 usershare 路径失败关闭，需管理员单独配置；既有已完成首启的设备仍需升级迁移，
不能仅靠已跳过的 storage 阶段自动修复。

上述当前修复在 VM 中的明确身份为：

- `native_storage_routes.py`: `009ec5e372105ac99830b20288f268f3f3bd9c3d83b9361d97d6977059fddccb`
- `native_storage.py`: `601ae04982705eaf3e9ac43dfc687993cd0f75e14757dcc642b572273acc7aba`
- 首启步骤库：`77afd4e57e9ef24e9bb0bcae1ddddb24f4c9b4de87bbd00d6325080f87693909`

之后补强了祖先目录不可写、注册目录 sticky/others 的前置检查；当前步骤库 SHA-256
为 `d3ce06b4cf4e8cf351b5bebaa4c31545c2f4be60c34e1f40d5d121f94863d17b`。
该版本 helper 也在已恢复原 ACL 的 VM 上连续执行两次成功，确认幂等且仍只增加
users:r-x，随后再次还原目录 ACL。前述协议报告绑定的应用模块身份未变化。

实际运行该首启 helper 后，NAS 用户可读取但不可写 usershare 定义目录。随后
19:22:12–19:22:49 +08:00 的协议诊断正常退出，验证了：

- 密码重置、旧 SMB ACL 迁移、SMB/NFS 配置及 ACL 更新均保留未审批 403 和审批后回读。
- SMB3 上传/下载的 SHA-256 一致；错误密码被拒绝；切换只读后写入拒绝、原数据可读。
- 真实 NFSv4 挂载后 root_squash 拒绝 root 写入，普通 NAS 用户可读写；
  切换只读后新挂载拒绝写入且保留读取；根目录 ACL 设为 none 后读取被拒绝。

客户端运行在同一 VM，NFS 使用其私网地址，仍不是 Windows/macOS/Linux 异机互通、
并发、大文件、重启持久性或断网故障验收。SMB 诊断原有单次密码 pipe 出现空读警告，
后改为匿名 memfd 提供可重新打开的密码源；密码未进入 argv、环境值或磁盘文件。
这是测试凭据传递修正，不计作产品修复。

最终经审批移除 NFS/SMB 规则、保数据解除共享登记，卸载测试卷，并移除精确匹配的
测试 OS/Samba 账户（UID 1001、nologin、Diagnostic member），保留家目录和含测试数据
的 256 MiB 镜像。四份应用文件按原 SHA-256 逐一还原，目录 ACL 恢复原值；
19:28:31 后端 active、NRestarts=0、健康接口 200。还原后的 VM 仍有原制品缺陷，
不能把修复临时部署的结果描述为原镜像现已修好。

宿主证据保存在 `C:\vmtest\install-diagnostic-20260907`，下载后摘要与 VM 一致：

- `protocol-fixed-result.json`：`a0387193e796871f30e2081f3b677e2460b493d694ec4467a7606bbdf76f076b`
- `smb-cause-isolation.json`：`8bd4d8b73eae8559dc09157fe5f7bddeb7d763146e43d72650c1cf5db8a5ef7c`
- `cleanup-result.json`：`bf51966dcef4731e3d3627cd439ce85f0b557b037e93763aa6c6fafc41c725b4`

最新相关回归为 **273 passed、2 skipped**（原生存储/SMB 路径保护、readiness、
observation、桥接路由、审批、装机合同），ruff 检查通过。跳过项仍为 Windows 上
的 POSIX 路径断言；首启 helper 的新鲜默认目录成功路径另已在上述 Linux VM 实跑。
随后又把 Samba usershare 与本机账户同名冲突前移到 plan 阶段，并让 capability
预检确认 `users` 组可映射为受管 Unix SID；当前工作树
`appliance/native_storage.py` 的 SHA-256 为
`27d18a1f337ace3bfd12083575ebf47939b8ecd7469b72122097e86923b21f9b`，因此不应把
上方临时部署的模块哈希误当作当前源码快照。

### 后续验收顺序

1. 使用空间正常且无 I/O 错误的构建环境生成介质，完整检查 ISO 内容和引导入口。
2. 从新的介质完成干净空盘安装，核对 GRUB、late_command、首启载荷和账户。
3. 首启在受控断网条件下核对 Codex、Python、Web、系统包与 ZFS 内核模块。
4. 重启后核对服务、HTTP 入口与真实 SMB/NFS 客户端读写及权限拒绝。
5. 发布前从最终干净源码重新生成所有载荷。先前为安装诊断复用旧载荷并改写
   `.echo-source-tree` 的制品不能作为当前版本的正式发布证据。

当前工作树还有其他会话的大量未提交改动；最终交付应以明确冻结的版本及与其
绑定的产物为准，不自动将这些改动纳入本次装机修复提交。
