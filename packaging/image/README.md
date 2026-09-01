# Echo OS 可启动整机镜像（目标 C / C3）

这里定义第一个可重复构建的 Echo OS x86-64 VM 整机镜像。构建链路是：

```text
Debian 13 snapshot + fixed SOURCE_DATE_EPOCH/seed
 + same-revision Echo wheel/resources/Linux Codex
                         ↓
             mkosi + systemd-repart
                         ↓
 GPT: ESP + root/hash/signature A + empty root/hash/signature B
                 + encrypted /var + swap + /home
                         ↓
   systemd-boot + desktop UKI + independent Recovery UKI
                         ↓
     OEM first boot → SDDM/PAM local login
                         ↓
 Xorg/KWin + Echo Desktop + loopback-native Echo Agent
```

## 构建

mkosi 创建磁盘镜像需要 Linux 内核、systemd 254+ 和镜像构建权限。在 Debian 13
构建机或 CI 中执行：

```bash
./deploy/appliance/prepare-agent-bundle.sh
./packaging/image/build-image.sh --prepare-only
```

镜像构建不会从网络临时选择“最新版 Agent”。前置 bundle 从当前 Echo OS revision
冻结 Agent wheel、运行资源和 Linux x86-64 Codex；`build-image.sh` 再按哈希锁为 Debian 13/CPython 3.13
生成 `/opt/echo-agent`。dirty bundle 只允许显式本地 QA，且会被生产镜像 postinst
拒绝。原生服务从同源配置加上镜像 policy 启动：关闭 Tentacle LAN listener、自动
情报源和开机技能市场刷新，因此冷启动只需要本地绑定目录，不依赖网络；运行时唯一
预期的 Agent listener 是 `127.0.0.1:8000`。开发兼容层中的模拟账号、计费和用量
API 不会进入原生系统运行路径。

原生 Agent 清单 v2 还把任务恢复能力列为镜像硬合同：wheel 必须同时提供只读
`GET /api/task-runs/recovery-queue` 和显式
`POST /api/task-runs/{task_id}/resume-execution`。缺少任一路由或响应 schema 时，运行时
组装及 Debian 镜像内导入检查都会失败。每次冷启动的 Agent 健康门只读取持久化恢复
队列并报告数量，不接管租约、不恢复检查点；真正继续执行仍需设备主人在任务空间完成
“接管”与“恢复执行”两次确认。

Linux 镜像 CI 还会在整盘安装产物的临时副本中向加密 `/var` 注入一条带检查点、租约
已经过期的任务，然后执行真实 UEFI 冷启动。启动必须报告 `recovery=1`；关机后测试再
取回 `/var/lib/echo-agent/task_runs.json` 与注入前内容逐字节比对。这样可以同时证明
中断任务跨断电可发现，以及开机健康检查不会替用户接管或自动续跑。该副本、测试规则
和种子任务都不会进入发布镜像。

完整发布构建必须选择外部提供的安装器/更新 GPG 公钥环、一次性 factory-data key、
Secure Boot/verity X.509 身份和独立 PCR-policy 身份；缺少其中任一项都会失败关闭。
两把私钥必须只对构建用户可读：

```bash
gpg --batch --export FULL_RELEASE_GPG_FINGERPRINT \
  >/run/secrets/echo-os-installer-release.gpg
```

只接受这种 binary public export；secret key packet、GnuPG keybox、压缩/不透明 packet
或超过大小上限的输入会在构建 Recovery 前被拒绝。

```bash
install -d -m 0700 /run/release
os_commit="$(git rev-parse HEAD)"
python3 packaging/image/os_source_identity.py capture \
  --repo "$PWD" \
  --expected-commit "$os_commit" \
  --output /run/release/echo-os-source-identity.json

ECHO_INSTALL_KEYRING=/run/secrets/echo-os-installer-release.gpg \
ECHO_UPDATE_KEYRING=/run/secrets/echo-os-update-release.gpg \
ECHO_UPDATE_TRUST_GENERATION=1 \
ECHO_OS_SOURCE_MANIFEST=/run/release/echo-os-source-identity.json \
ECHO_FACTORY_DATA_KEY=/run/secrets/echo-os-factory-data.key \
ECHO_SECURE_BOOT_KEY=/run/secrets/echo-os-db.key \
ECHO_SECURE_BOOT_CERTIFICATE=/run/secrets/echo-os-db.crt \
ECHO_PCR_POLICY_KEY=/run/secrets/echo-os-pcr-policy.key \
ECHO_PCR_POLICY_CERTIFICATE=/run/secrets/echo-os-pcr-policy.crt \
ECHO_TPM2_PCR_PUBLIC_KEY=/run/secrets/echo-os-pcr-policy-public.pem \
  ./packaging/image/build-image.sh
```

其中 `echo-os-source-identity.json` 必须在任何构建生成步骤前，从干净 checkout 捕获到
仓库外部；CI 会把 `$GITHUB_SHA` 作为预期 commit。`build-image.sh` 与独立 Recovery
构建在组装前后都重新核对当前 checkout 仍与该 commit/tree/origin 完全一致。主
dm-verity root 和已签名 Recovery UKI 都嵌入同一只读 identity 与严格 verifier；成品
artifact 检查还会从 root 逐字节读回并与构建输入比较。

这会让 mkosi 同时签名 systemd-boot、桌面 UKI 和独立 Recovery UKI；同一张发布
证书还签名每个 root 的 dm-verity root hash，构建器会在发布任何分区载荷前核对
PKCS#7 签名、证书指纹、roothash 派生分区 UUID 和 UKI 内嵌 roothash。VM 验收会使用
`uefi-secure-boot` OVMF 和仅灌入该证书的自定义变量存储，而不是关闭验签继续启动。
仓库和构建产物目录都不生成或保存生产私钥。

真正组装前，构建脚本先取得 mkosi 的 JSON resolved summary，并由
`verify-mkosi-summary.py` 要求主镜像确实解析成 x86-64 Debian Trixie disk、依赖唯一
自定义 initrd、启用 Secure Boot、dm-verity、signed expected PCR、UKI 和 split
partitions，且使用选定的六个密钥/证书/加密输入。它同时检查 initrd 已解析出
dm-crypt、dm-verity、ext4、overlay 模块和 machine-state 服务，并拒绝 `rw` 或任何
可变 `root=`。因此命令行参数拼写或 mkosi 默认值漂移不会等到成品启动时才暴露。

macOS 可以先生成并验证 Linux Electron payload，但不能生成 GPT 镜像：

```bash
./packaging/image/build-image.sh --prepare-only
```

完整构建产物位于 `packaging/image/mkosi.output/`，包括未压缩 raw 磁盘、UKI、
带 roothash 派生 UUID 的 root、root-verity、root-verity-sig 三个分区载荷、
SHA-256 校验文件以及 JSON/文本软件包 manifest。

从经过 artifact 验证的 raw 生成签名整盘安装包：

```bash
ECHO_INSTALL_KEYRING=/run/secrets/echo-os-installer-release.gpg \
ECHO_INSTALL_SIGNING_KEY=FULL_RELEASE_GPG_FINGERPRINT \
ECHO_OS_SOURCE_MANIFEST=/run/release/echo-os-source-identity.json \
ECHO_FACTORY_DATA_KEY=/run/secrets/echo-os-factory-data.key \
ECHO_SECURE_BOOT_CERTIFICATE=/run/secrets/echo-os-db.crt \
ECHO_TPM2_PCR_PUBLIC_KEY=/run/secrets/echo-os-pcr-policy-public.pem \
  ./deploy/installer/create-install-bundle.sh \
  packaging/image/mkosi.output/install-bundle
```

签名私钥不会进入 raw、Recovery 或安装包。安装包操作与安全边界见
`deploy/installer/README.md`。

## 验收

```bash
./packaging/image/verify-image.sh --static
./packaging/image/verify-image.sh --artifact packaging/image/mkosi.output/echo-os_0.2.0.raw
./packaging/image/smoke-recovery-image.sh packaging/image/mkosi.output/echo-os_0.2.0.raw
./packaging/image/smoke-login-image.sh packaging/image/mkosi.output/echo-os_0.2.0.raw
./packaging/image/smoke-boot-image.sh packaging/image/mkosi.output/echo-os_0.2.0.raw
./packaging/image/smoke-agent-recovery-image.sh packaging/image/mkosi.output/echo-os_0.2.0.raw
```

PR 只运行 Debian 容器中的 portable source contract：Linux runner policy 与统一 evidence
binder 的单元测试，以及完整静态镜像合同；它不会获得特权设备，也不会构建或启动 raw。
可信 push/workflow dispatch 才进入完整 `build-and-boot`。重型镜像和 A/B job 都只匹配
同时带有 `self-hosted`、`linux`、`x64`、`echo-os-image` 标签的专用 runner；不存在 hosted
fallback。fail-closed preflight 会在签名和构建前再次验证实际资源。专用 runner 至少需要
4 个 effective CPU、16 GiB effective memory、48 GiB workspace、160 GiB scratch、4 个空闲
loop、2 个空闲 NBD、可读写 KVM 和声明 x86-64 Secure Boot 的 QEMU firmware；workspace 与
scratch 共用文件系统时，空闲空间要求相加为 208 GiB。workflow 把无显式父目录的临时树
固定到该 scratch，并在换 TPM、factory reset、provisioned 分支完成取证后立即删除对应 raw，
但备份/恢复门仍会同时保留约五份 21 GiB 整盘状态。preflight 会生成 mode 0600 JSON，保留
完整日志，并发出唯一 `ECHO_IMAGE_RUNNER_READY` marker；该 marker 也是最终 evidence 的
必需输入。artifact 上传之后，无论任务成功或失败，最后的有界 cleanup 都会清除 `echo-` scratch
命名空间、生成的 mkosi/Agent bundle、临时签名私钥、恢复密钥、
虚拟 TPM 状态与整盘副本；它拒绝链接目标、非规范根路径、工作区/临时区重叠和非 GitHub Actions
调用，同时保留 runner 自己的非 Echo 临时文件。当前工作区尚未在满足这些条件的 runner 上执行
完整 workflow。

在注册官方 GitHub Actions Runner 之前，先在一台专用、单租户 Debian/Ubuntu x86-64 主机
创建非 root 服务账号和位于大容量文件系统上的空工作目录，然后从仓库根执行：

```bash
sudo ./packaging/image/configure-linux-image-runner-host.sh \
  /srv/echo-os-image-runner echo-runner
```

该脚本安装宿主 Docker/QEMU/kmod 依赖，固定 loop/NBD 模块参数，把既有 `echo-runner` 加入
`docker` 与 `kvm`，并以该用户运行
`verify-linux-image-runner-host.py`。成功时会在 mode-`0700` 工作根写入不可覆盖、mode-`0600`
的 `echo-image-runner-host.json`，并输出唯一 `ECHO_IMAGE_RUNNER_HOST_READY` marker。验收还会
把配置、host evidence 和宿主 cleanup 全部绑定到同一个 `/srv/echo-os-image-runner`；
配置前该目录必须为空，不能拿已有系统或数据目录改所有者。官方 runner 为 container job
把该 work root 映射为 `/__w`，因此容器内 preflight schema 2 另行精确要求
`/__w/echo-os/echo-os` workspace 与 `/__w/_temp` scratch，并把这两个实际路径写入证据。
它同时
拒绝 rootless/远程 Docker、非默认 context、`DOCKER_HOST`/`DOCKER_CONTEXT` 覆盖和非本机
`/var/run/docker.sock`，避免 privileged job 实际落在另一个 daemon。若模块已
用较小参数加载，先保留失败日志，安排重启以应用 `/etc/modules-load.d` 与
`/etc/modprobe.d` 配置，再重新验收；脚本不会卸载正在使用的块设备模块。

之后只使用 GitHub 仓库 Settings 页面当次显示的官方一次性命令注册 Runner；应用目录固定为
`/opt/actions-runner`，工作目录必须仍为 `/srv/echo-os-image-runner`，runner 名使用短的
字母数字/点/下划线/连字符，自定义标签必须包含 `echo-os-image`，不要使用 `--ephemeral` 或
`--disableupdate`。配置脚本不创建服务账号、不下载 Actions Runner，也没有 GitHub URL、注册
Token、`curl`/`wget` 或 runner `config.sh` 入口。注册成功后先用官方 `svc.sh` 安装但不要启动服务：

```bash
cd /opt/actions-runner
sudo ./svc.sh install echo-runner
```

然后把实际解压、注册并安装服务后的应用目录交给 hook 配置器：

```bash
sudo ./packaging/image/configure-linux-image-runner-hooks.sh \
  /opt/actions-runner echo-runner
```

它只接受已含 `.runner`、`.credentials`、`.credentials_rsaparams`、`.service`、`config.sh`、
`run.sh` 且归服务账号所有的规范目录，把四份注册/凭据文件和原子更新的 `.env` 固定为
mode-`0600`，且应用目录固定为 `/opt/actions-runner`。随后由安装在 runner 应用目录之外的
`verify-linux-image-runner-registration.py` 以非 root 服务账号读取官方 `.runner` 数据，要求
`GitHubUrl` 精确指向 `dengdenghua/echo-os`、`WorkFolder` 精确指向已验收 work root、当前
GitHub V2 消息流、非 ephemeral、未禁用官方安全更新，并核对已启用的官方 systemd unit、两个
固定 cleanup hook 与 mode-`0600` host evidence。成功会输出唯一
`ECHO_IMAGE_RUNNER_REGISTRATION_READY` marker。它同时启用 root-owned、应用目录外的
started/completed hook；私钥、整盘和 Echo 构建残留只会在固定 work root 的
`echo-os/echo-os` checkout 和 `_temp/echo-*` 范围内于
任务结束后清理。宿主 hook 只接受 `/srv/echo-os-image-runner` 布局，container job 的显式
最终 cleanup 只接受与之对应的 `/__w` 布局；两套根路径不能混用。异常中断没跑到 completed
hook 时也会在下一任务开始前清理。hook 使用官方默认
`GITHUB_WORKSPACE`/`RUNNER_TEMP`，自带 300 秒 timeout，不接收注册 token。注册验证和 hook 配置
通过后启动服务：

```bash
cd /opt/actions-runner
sudo ./svc.sh start
```

再从持有仓库管理权限的工作站读取 GitHub Actions runners API，确认这台在线 runner 同时拥有
`self-hosted`、`linux`、`x64`、`echo-os-image` 四个标签；不要把管理 Token 留在 runner 主机。
首次任务日志的 `Set up runner` 与 `Complete runner` 还必须确认两个 hook 都输出
`ECHO_IMAGE_RUNNER_CLEANUP_OK`。Docker 组是宿主 root 等价权限，因此这台机器不能承载其他不受信任
仓库，且两个重型 job 都继续排除 pull request。注册后，容器内 9 项 runner policy 会在生成任何
临时签名身份前再次核对真实构建工具、KVM、Secure-Boot firmware、loop/NBD 空闲量和
workspace/scratch 容量。

Agent 已迁入本仓库。整机、A/B 与通用 CI 都从同一个 Echo OS revision 构建。通用 PR
仍运行 Ruff、SAST、依赖审计、
完整 appliance 套件和隔离的运维测试；任何新增、删除、重叠、symlink 或未分类测试都不能静默
掉出覆盖。源码交付前检拥有固定 check-code 合同，并要求统一 revision 是干净且已推送的交付分支。
候选协调不再从混合工作区与 runner 临时目录的大日志 artifact 猜测下载根路径：raw、A/B 各自
上传只含 manifest、detached signature、public keyring 的专用 30 天 artifact。raw/A-B 的 OIDC
验证明确接受仓库固定的专用 self-hosted runner，同时由签名 manifest 绑定 runner preflight；
GitHub-hosted 的 OMV/appliance 仍强制 `--deny-self-hosted-runners`，两类 policy 都进入候选报告与
统一索引。最终 90 天候选包携带十份实际输入和离线回放入口，上传前会在脱离仓库路径的打包目录
重验 GPG 并逐字节重建索引；缺件、额外路径、漏/重复 checksum 或串版都会失败关闭。

Linux 镜像 workflow 已定义一次完整的临时 NBD 写盘：先验证签名、载荷、目标整盘
识别和逐盘确认字符串，并证明 `plan` 没有写目标；再把精确 token 交给生产安装器，
要求写后哈希、GPT 尾部搬移、`echo-home` 分区/文件系统扩容及最终完成标记。随后
Recovery、凭据驱动的真实 OEM 首启、生产 SDDM/X11、SDDM/Wayland 候选和直接桌面
五个 Secure-Boot QEMU 门都以这块安装后 raw 为输入，而不是继续启动构建源 raw。
OEM 门只在一次性副本注入 SDDM autologin；账户、密码哈希、地区状态和完成标记均由
生产 OEM 程序在启动中生成，随机明文密码仅通过 VM systemd credential 传递。当前
工作区尚未在 Linux runner 执行该未提交 workflow，因此这是可执行验收定义，不是
已经成功安装或完成首启的运行证据。

workflow 的最后一步还会运行 `verify-os-image-evidence.py`。只有安装、Recovery、
换 TPM、factory reset、OEM/SDDM、X11/Wayland、独立备份盘、promoted restore 试运行和
Agent 中断任务恢复连同 dedicated-runner preflight 共 15 组日志都各自包含唯一的完整标记时，
才生成 `echo-os-image-evidence.json`。清单绑定
构建前干净的 Echo OS 40 位 commit/tree/origin 与 source-identity 摘要、精确镜像版本、
干净的 40 位 Agent Git commit 与 bundle manifest 摘要、GPG 签名安装
manifest/signature 摘要、安装公钥环、Secure Boot 公共证书、signed-PCR11 公钥摘要、
manifest 声明的源 raw SHA-256/大小、最终安装后整盘 SHA-256/大小，以及每份原始日志的
相对路径、字节数和 SHA-256；schema-3 安装 manifest 必须重复同一 OS 源码身份，安装
plan/install 标记必须重复同一 manifest 与源 raw 身份；所有正常桌面、生产登录和
Recovery 冷启动标记还必须报告与该签名 source identity 精确相同的 OS commit。
它有单文件/总量/整盘上限，不复制日志正文，也不执行日志控制的内容。生成完成后，CI
用安装 bundle 的同一完整发布 fingerprint 对它产生 detached GPG 签名，并立即用已通过
public-only packet 审计的安装公钥环对“签名 + 原 manifest”执行 `gpgv`；JSON、签名、公钥环
和签名验证日志一起保留，私钥只留在 runner 的临时 `GNUPGHOME`。原始日志仍作为独立 CI
产物供审阅。这样一份经过签名的 manifest 只能证明同一次 workflow 的证据集合没有串版、
缺项或上传后无痕替换；在 Linux runner 实际生成并审阅前，它本身仍只是验收代码。
下载产物后可只用公开材料离线复验：

```bash
./packaging/image/verify-os-image-evidence-release.sh \
  echo-os-image-evidence.json \
  echo-os-image-evidence.json.gpg \
  echo-install-keyring.gpg
```

最后一条命令通过 UEFI/QEMU 冷启动镜像，并同时等待一组来自生产链路的标记：

CI 继续用只读串口采集启动日志，同时显式挂载 `virtio-vga`，让 Xorg/KWin 在无
图形窗口的 runner 上仍然拥有真实的虚拟显示设备。

- `ECHO_DESKTOP_READY`：Xorg、KWin 和 Shell 窗口已经建立。
- `ECHO_RENDERER_READY`：打包后的 Electron renderer 已完成离线加载。
- `ECHO_AGENT_READY`：镜像中的 Agent wheel、资源和 Codex 已通过同源校验，
  loopback 服务返回与镜像清单一致的运行时身份和恢复队列。
- `ECHO_MACHINE_ID_READY`：活动 root 与持久 `/var` 使用同一设备身份，日志只包含不可逆派生值。
- `ECHO_NETWORK_STATE_READY`：NetworkManager 的私有连接位置已在持久 `/var` 准备完成。
- `ECHO_REGION_STATE_READY`：locale、keymap 与 timezone 已初始化或从持久 `/var` 恢复。
- `ECHO_KWIN_COMPOSITOR_BRIDGE_READY`：KWin 脚本已发布第一份 compositor
  UUID 窗口快照，私有 socket 可以接收带回执的固定动作。
- `ECHO_LOCK_SERVICE_READY`：`xss-lock` 已经附着到该 X11/logind 会话。
- `ECHO_LOCK_SCREEN_LAUNCHED`：direct-CI 会话的 logind 锁事件已启动
  PAM-backed `XSecureLock` 进程。该标记不声称证明了输入 grab 或密码解锁。
- `ECHO_NOTIFICATION_SERVICE_READY`：会话用户已经取得标准
  `org.freedesktop.Notifications` D-Bus 名称，并建立只对该用户开放的 Echo 通知中心
  socket。四种安装后桌面门都要求该标记。
- `ECHO_INPUT_METHOD_READY`：受会话监管的 Fcitx5 已取得
  `org.fcitx.Fcitx5` 名称；GTK3/GTK4、Qt5/Qt6、XIM/SDL 环境与中文拼音插件随镜像
  交付。X11 和 Wayland 都必须出现该标记，输入法进程退出会结束不完整的会话。
- `ECHO_CLIPBOARD_READY`：无窗口 Qt 宿主已加载 Debian Plasma 6 的 Klipper QML
  模块并取得 `org.kde.klipper`。其 SQLite 历史固定在 `/run/user/<uid>` 的易失
  runtime，权限为 `0600`，不会写入持久 Home；四种安装后桌面门都要求该标记。
- `ECHO_ACCESSIBILITY_READY`：会话监管的 AT-SPI launcher 已取得 `org.a11y.Bus`，
  `GetAddress` 可用并且 Qt accessibility bridge 已启用。
- `ECHO_ACCESSIBILITY_TREE_READY`：独立 Python/AT-SPI 客户端在刚启动的应用 PID 树中
  找到了固定 Echo accessible marker；探针不会记录其余树节点或用户内容。四种安装后
  桌面门同时要求 bus 与 tree 两个标记。
- `ECHO_CRASH_COLLECTION_READY`：`systemd-coredump` socket 已激活，有效配置仍保持
  512 MiB 单 core、1 GiB 总量和 2 GiB `KeepFree`，且 `/var` 确认来自加密映射。
- `ECHO_REMOVABLE_STORAGE_READY`：UDisks2 已取得 system D-Bus 名称，Dolphin/KIO
  MTP、PolicyKit/udev 激活文件和 FAT、exFAT、NTFS、ext4、Btrfs、XFS 工具链都已
  就绪。该门只读取 `udisksctl status`，不会在启动时自动挂载或修改用户介质；greeter
  和四种安装后桌面完成门都要求该标记。
- `ECHO_PRINTING_READY`：local-only CUPS scheduler/socket、KDE Print Manager、
  CUPS PolicyKit helper、PDF/raster filters 和 loopback driverless USB 链已就绪，
  spool 位于加密 `/var`，作业文件和历史保留关闭；greeter 和四种安装后桌面完成门
  都要求该标记。
- `ECHO_SCANNING_READY`：SANE USB backend/udev、KDE Skanpage、loopback ipp-usb eSCL
  和按需 eSCL/WSD 客户端链已就绪，`saned` 网络共享关闭，扫描内容只由用户选择保存位置；
  greeter 和四种安装后桌面完成门都要求该标记。
- `ECHO_CORE_APPS_READY`：Dolphin、Konsole、Firefox ESR、Kate、Okular、Gwenview、Ark、
  Haruna、Spectacle、KCalc、freedesktop opener 和各自 desktop entry 都来自不可变 root，
  且系统 XDG 默认关联通过严格解析；健康门不启动 GUI 或读取用户文件，greeter 和四种
  安装后桌面完成门都要求该标记。
- `ECHO_CORE_APPS_SESSION_READY`：仅在隔离桌面 CI 或带 VM credential 的 direct raw
  会话中，固定 runtime-only 目录、文本/PDF/PNG/ZIP/WAV 和仅监听随机 `127.0.0.1`
  端口的 HTML 页面已通过 `xdg-open` 进入 Dolphin、Firefox ESR、Kate、Okular、Gwenview、
  Ark、Haruna 的七个真实原生窗口；不可变 root 中 Konsole 与 KCalc 的发行版 desktop
  entry 还必须通过生产 `/usr/bin/gio launch` 各启动一个原生窗口。九个窗口都要求正确
  desktop identity 和非零 PID，七个 handler 窗口还绑定 fixture 标题，随后每个精确窗口
  均通过生产固定动作接口关闭。发布 evidence 要求该标记；普通用户会话不会自动运行此
  诊断，诊断也不访问非 loopback 网络。
- `ECHO_NATIVE_APP_IPC_READY`：打包 Echo Desktop 已实际通过 preload 暴露的
  `apps.list()` 枚举固定 `org.kde.kcalc`，再通过 `apps.launch()`、main-process IPC 与
  有界 `/usr/bin/gio launch` 得到零退出；会话随后观察到唯一新增、非零 PID 的 KCalc
  原生窗口并精确关闭；Wayland workflow 还要求打包 Electron 使用 Ozone Wayland、由
  KWin 私有桥提供 canonical UUID 与 close 回执，并复核 Echo 自身 AT-SPI marker。该门
  接受 standalone desktop CI、`/run/credentials/echo-desktop.service` 中固定 systemd
  credential，或一次性 SDDM Wayland 镜像副本中 root-owned `0444` 的固定请求。第三条路径
  不给 SDDM 传 direct-desktop credential、不启用 standalone auto-exit，Shell 与 Electron
  分别核对固定路径、owner、mode、内容和 Wayland session；原始成品镜像没有该文件。请求
  应用和输出路径均不可由 renderer 选择，私有 runtime 结果以原子 `0600` 文件发布。
  direct raw 与 SDDM Wayland 完成门、发布 evidence 都要求各自唯一精确标记；普通用户会话
  不触发。artifact verifier 还会用 factory key 只读打开 `echo-var`、以 `ro,noload` 检查
  `/etc` overlay upper，证明请求也没有藏在加密持久层中。
- `ECHO_BOOT_HEALTHY`：不可变 root 中的 source identity 重新验证成功并报告其 40 位
  OS commit，桌面健康服务才允许 `boot-complete.target` 完成，随后
  `systemd-bless-boot` 才能把本次启动标记为成功。`ECHO_LOGIN_READY` 与
  `ECHO_RECOVERY_READY` 执行同一来源核对。

这些自动桌面标记只由 VM 验收使用。`smoke-boot-image.sh` 通过 mkosi 向虚拟机传入
一次性的 `echo.os.ci-session` systemd credential；只有该 credential 存在时，
`echo-desktop.service` 才能绕过交互登录启动。credential 不会写入 raw、UKI 或
用户密码数据库。

## OEM 首启与本地登录

未配置的生产镜像不会自动进入桌面。`echo-oem-setup.service` 先占用 tty1，让设备
所有者设置显示名、设备名和本地管理员密码；设备名固定为最多 15 个小写字母、数字或内部连字符，
避免 Samba/Windows 发现截断为另一身份；其他输入经过长度、控制字符、DNS 标签及
弱密码校验。固定首发本地账号 `echo`（UID 1000）成功设置密码并加入 `sudo` 后，
程序以原子方式写入权限为 `0600` 的完成标记，随后 SDDM 才能启动。

A/B root 不能拥有设备密码的唯一副本：新 root 自带的 `/etc/shadow` 按设计仍是锁定
模板。OEM 完成时，真实密码哈希另存为 root-only 的
`/var/lib/echo-os/local-account.shadow`；切换到新 root 后，恢复服务会在 SDDM 之前
通过 `chpasswd --encrypted` 的标准输入恢复哈希。`/etc/shadow`、`/etc/passwd` 或
`/etc/hostname` 改变时，path unit 会重新捕获当前状态，因此正常改密码不会在下一次
更新后回退。明文密码不会持久化，也不会进入命令行参数。
持久状态同时记录捕获时的 root 版本：只有版本已经变化，锁定 shadow 才会被视为新
槽的供应商模板；同一 root 上的主动账号锁定不会被服务自动撤销。

SDDM 通过 PAM 验证 Linux 本地密码，默认再执行
`/usr/share/xsessions/echo.desktop`。镜像也发现
`/usr/share/wayland-sessions/echo-wayland.desktop`，但明确标成
`Echo OS (Wayland Candidate)`，只能由用户手动选择；取得 Linux raw/真机证据前不
替换默认 X11 生产会话。
交付配置不包含 `[Autologin]`、测试密码或空密码。生产启动健康链等待 SDDM 在
`seat0` 建立 greeter；VM 自动化则走前述独立 credential 分支。Echo/Agent 的短信、
邮箱或云账号仍是应用身份，不能代替本地 OS 登录，也不会因此取得 `sudo`。

`smoke-login-image.sh` 不修改交付产物：它先做 sparse/reflink 临时副本，只在副本
写入测试用 OEM 完成标记、随机生成的临时密码哈希和 SDDM 一次性自动登录，然后
明确关闭 CI credential 分支。
它必须从 UEFI/systemd-boot 走到 SDDM 的 `seat0`、由 SDDM 启动 Echo session，最终
同时看到 A/B 账号恢复、登录健康、KWin 和 renderer 标记。测试结束后整个副本被删除。
同一脚本只额外接受精确的 `ECHO_LOGIN_SESSION=echo-wayland.desktop`。镜像 workflow
会再创建一个独立副本，从相同 Secure-Boot UEFI 选择候选会话，要求 DRM KWin、
XWayland、KScreenLocker、UUID bridge、renderer 以及持久账号/机器/网络/地区/应用状态
同时就绪。该源代码门在远端 Linux workflow 变绿前不能被写成已通过。

## PAM 锁屏、空闲与退出会话

镜像显式安装 `xss-lock` 和 `xsecurelock`。Echo session 在 KWin 就绪后先启动
`/usr/lib/echo-os/echo-session-lock`，为 X11 设置 10 分钟锁定和 15 分钟关显示，
再把空闲、logind 手动锁定与休眠前锁定都交给固定的
`echo-screen-locker`。该适配器只启动 `/usr/bin/xsecurelock`，并使用
`/etc/pam.d/echo-lock` 进入 Debian `common-auth`/`common-account` 策略。

前端“锁定屏幕”只在打包的原生 Linux 会话且锁协调器活着时可用，
固定调用 `loginctl lock-session self`；“退出登录”固定调用
`loginctl terminate-session self`。两条路径都使用 `execFile` 的固定程序/参数，
不经命令 shell。锁协调器退出时桌面会话失败关闭，不继续暴露一个无法锁定的桌面。

策略单测、镜像静态契约和 direct-CI 启动标记可验证组件包含与调用链；
真实 PAM 正确/错误密码、手动/空闲锁定、休眠唤醒和 X11 安全 grab 仍必须
在 raw 镜像和真机完成。

Wayland 候选不运行 X11 的 xss-lock 适配器。KWin 内建 KScreenLocker，镜像显式
交付其 greeter、vendor PAM service `kde` 和 `/etc/xdg/kscreenlockerrc`：默认 10 分钟
自动锁定、`LockGrace=0`、必须密码解锁并在 resume 后锁定。候选子会话确认
`org.freedesktop.ScreenSaver.GetActive` 可调用后才设置原生锁屏能力，服务消失即关闭
Shell；virtual Wayland gate 还会调用 `Lock` 并要求返回 active=true。它们目前仍是
源码和待运行 Linux gate，不等于正确/错误密码及休眠后的真机解锁证据。

## 原生电源管理

Echo 的自定义 KWin 会话不运行完整 Plasma workspace，因此不会把
`/etc/xdg/autostart/powerdevil.desktop` 当作隐含前提。X11 和 Wayland 生产会话会直接
启动发行版的 `org_kde_powerdevil`，要求它取得
`org.kde.Solid.PowerManagement` 会话总线名，并等待系统总线上的 UPower 与 Power
Profiles daemon。三者就绪后才输出 `ECHO_POWER_MANAGEMENT_READY`；PowerDevil 消失
会令图形会话失败关闭。镜像显式安装 `upower` 和 `power-profiles-daemon`，不会因
`WithRecommends=no` 丢失 PowerDevil 的推荐后端。

这条门证明电源设置具有后台执行者，但不证明某块实体主板/电池/GPU 的 ACPI、合盖、
亮度、DPMS、平台 profile、suspend 或 resume 行为正确；这些仍属于 raw 与真机矩阵。

## 原生通知服务

自定义会话不依赖 Plasma 隐式启动通知 daemon。镜像交付
`/usr/lib/echo-os/echo-notification-service`，X11 与 Wayland 会话都显式监管它；服务
实现标准 `org.freedesktop.Notifications` 的 Notify、CloseNotification、能力和服务器
信息方法。第一版只宣告 `body`，把 FDO body markup 转为有界纯文本，不接受通知内容
作为命令或任意图标路径。最多 100 条会话内历史通过权限为 `0600` 的 Unix socket 提供
给隔离的 Electron main process，renderer 不能选择 socket 路径。便携 store/Node 协议
测试可以在非 Linux 主机运行；D-Bus 集成、成品 cold boot 和实际第三方应用通知仍由
Linux workflow/raw 验收。

## 易失系统剪贴板

镜像显式安装 `plasma-workspace`、`libqt6sql6-sqlite`、
`python3-pyqt6.qtqml`、`xclip` 与 `wl-clipboard`。Plasma 6 的 Klipper 是
`libklipper6` 加 QML plugin，不存在可被会话直接监管的 standalone binary；因此
`echo-clipboard-host` 使用无窗口 `QApplication` 加载官方
`org.kde.plasma.private.clipboard` 模块，并以 `org.kde.klipper` 作为就绪/存活边界，
不需要运行完整 Plasma Shell 或额外 plasmoid 窗口。

`klipperrc` 默认保存最多 20 个显式复制项、保留图片、不同步 X11 primary selection、
不跨登录保留历史。更强的异常退出边界来自 `KLIPPER_DATABASE`：宿主只接受
`$XDG_RUNTIME_DIR/echo-os/clipboard/history3.sqlite`，逐级拒绝符号链接或非当前用户的
目录，并以 `umask 077` 创建数据库。因此关闭会话、重启或 logind 清理 runtime 后不会
留下持久 clipboard 历史；该宿主的配置、cache 和 Klipper debug log 也被限制在同一
易失/无 payload 边界。X11/Wayland CI 还必须证明原 selection owner 退出后，第二个
独立客户端仍能粘贴固定 sentinel；D-Bus 名称存在本身不算通过。

## AT-SPI 与屏幕阅读器

镜像显式安装 Debian `at-spi2-core`、`python3-pyatspi`、Orca、Speech Dispatcher 和
eSpeak NG 离线语音 backend。自定义 Echo 会话不依赖 Plasma/GNOME autostart，而是直接
监管 `/usr/libexec/at-spi-bus-launcher --launch-immediately`，验证
`org.a11y.Bus.GetAddress`，并把 `QT_ACCESSIBILITY=1` 传给 D-Bus/systemd 激活环境。
Electron 以 `--force-renderer-accessibility` 启动；只有独立 AT-SPI 进程在对应 Electron
进程树中找到固定 `Echo OS 桌面` 节点，desktop-ready 才会写入
`accessibility=ready`。Wayland compositor gate 对原生 GTK 控件执行同类检查。

树探针有节点/深度/超时上限，只比较固定 marker，并且不会打印树中的窗口名、文本、
通知或 Agent 内容。Orca 通过 root-owned、无 shell 的
`/usr/local/share/applications/echo-screen-reader.desktop` 暴露给应用启动器，但不默认
开机朗读。

生产 SDDM 使用 X11 greeter。镜像额外安装 `python3-xlib`，通过
`GreeterEnvironment` 打开 Qt accessibility bridge，并由保留 Debian vendor
Xsetup/Xstop 的 root-owned wrapper 管理一个 `sddm` 用户 transient helper。helper 只在
本地 `seat0` 的 logind greeter 存活时 grab `Super+Alt+S`，只启动固定的
`/usr/bin/orca --replace --no-setup --disable splash-window`，配置/cache 全部留在易失的
`/run/user/<sddm-uid>`，不接受 renderer 参数或 shell。镜像 workflow 会把已通过 OEM
首启、且已移除测试 autologin 的磁盘停在生产 greeter，通过独立 QMP socket 发送固定
组合键，并要求 helper ready 与 Orca started 两个日志 marker。当前 macOS 只能完成
单元/静态/成品准备检查，尚未执行该 raw gate；Speech Dispatcher 实际发声、键盘全流程、
焦点语义、高对比度/放大及真实用户验收仍需 Linux raw 与真机执行。

## KWin compositor 窗口桥

镜像现在同时安装 `kwin-x11`、`kwin-wayland` 与 XWayland，并把
`org.echoos.windowbridge` KWin 6 JavaScript 包交付到 `/usr/share/kwin/scripts`。
脚本直接读取 `Workspace.stackingOrder`，用 `Window.internalId` UUID 和
`desktopFileName` 表示 compositor 所有的窗口；协议 v2 还读取
`Workspace.screens`、输出几何和 `Output.devicePixelRatio`，再由 KWin Window API
执行聚焦、最小化和关闭。

`echo-kwin-window-bridge` 核对 `org.kde.KWin` 的 D-Bus unique owner，只接受
KWin 脚本的快照与回执；Electron 只能连接
`$XDG_RUNTIME_DIR/echo-os/kwin-window-bridge.sock` 的 mode-`0600` socket。
快照会在 daemon 和 Electron 两侧重新验证；动作只包含固定 action 与 UUID，
并在 KWin 回执后才返回成功。daemon 或桥意外退出时桌面会话失败关闭。

Electron 已在 `XDG_SESSION_TYPE=wayland` 时选择 `kwin-wayland` provider。
生产候选通过 `kwin_wayland_wrapper --drm --xwayland` 启动，核对 wrapper 同步到
systemd user environment 的 Wayland/XWayland socket 与 authority，并等待 mode-`0600`
renderer readiness 文件。KWin 脚本按精确 Echo identity 强制桌面窗口 keep-below、
无边框、跨桌面且不出现在 taskbar/pager；默认生产启动仍使用 `kwin_x11`。隔离 Xvfb/KWin smoke 会在 X11
下启动相同 KWin 脚本，要求真实 UUID 快照，并通过该 provider 关闭真实窗口。
另一个 Linux gate 会用 KWin virtual backend 建立两个 1.25× 输出，以
`GDK_BACKEND=wayland` 启动原生 Wayland 窗口，并执行同一 UUID 生命周期。当前 Mac
只能验证该 gate 的源码和静态契约；Linux CI 实际变绿前不能算运行证据，也不能代替
候选生产链、独立 Shell surfaces、热插拔、物理多屏/HiDPI 的 raw/真机验收。

同一个 Debian Trixie 镜像现在还会在 mkosi build overlay 中针对镜像自带的 KWin 6 ABI
编译 `org.echoos.liquidglass` 原生 Effect，并安装到
`/usr/lib/x86_64-linux-gnu/qt6/plugins/kwin/effects/plugins/`。Wayland 会话在 KWin 启动前
写入固定启用项，Shell 等待 Effect 的 D-Bus 对象后报告
`ECHO_KWIN_GLASS_EFFECT_READY`；原始 Wayland 冷启动门要求该标记。运行时仍保留 WebGL
回退，因此不支持硬件加速或 Effect 初始化失败不会让用户得到黑屏，只会失去 compositor
原生模糊。

## 离线核心应用与系统默认关联

签名镜像显式安装 Dolphin、Konsole、Firefox ESR、Kate、Okular、Gwenview、Ark、Haruna、
Spectacle、KCalc、`xdg-utils` 与 `desktop-file-utils`。镜像禁用 package Recommends，
所以同时显式安装 Ark 的 Debian-recommended `7zip`、`bzip2`、`unar`、`unzip`、`zip`。
这套基线覆盖文件管理、终端、网页、
文本、PDF/PostScript、图片、压缩包、音视频、截图和计算器；它属于版本化系统 root，
只随签名 A/B 整机更新变化，不冒充用户从应用商店安装的持久第三方软件。

`/etc/xdg/mimeapps.list` 只包含一个 `Default Applications` 段，固定目录、HTTP/HTTPS、
文本/Markdown/CSV/JSON/XML、PDF/PostScript、常见图片、归档和音视频的系统默认处理器。
它不设置 Added/Removed Associations，用户可在加密 Home 中保存个人覆盖。严格 Python
parser 要求配置是 root-owned、非符号链接、不可由 group/world 写入，并逐项核对完整
映射；遗漏、额外键、重复段或处理器漂移都会失败关闭。

`echo-core-apps-health.service` 在 SDDM、direct-CI desktop 与 boot blessing 前运行，使用
`PrivateDevices=yes`、`PrivateNetwork=yes` 和 `ProtectHome=yes`。它检查所有可执行文件和
desktop entry 的所有权/权限，调用 `desktop-file-validate` 和 policy parser，但不会调用
`xdg-open`、`gio launch` 或任何 GUI 应用。7 项 policy、5 项 runtime health 和 7 项
functional-session portable 测试覆盖安全路径、映射漂移、缺失 runtime、可写文件、无意
启动应用、固定 fixture/identity、loopback-only HTTP 和 CI sentinel。五类 raw 启动门要求
无副作用 health；其中 direct raw 还必须逐一用 `xdg-open` 打开 runtime-only 目录、文本、
PDF、PNG、ZIP、WAV 与随机 loopback 端口上的固定 HTTP 页面，观察七个对应原生窗口并通过
生产窗口接口关闭；同一会话还必须通过生产 `gio launch` 启动 Konsole 与 KCalc 的 root-owned
desktop entry，观察并关闭两个精确原生窗口。该九窗口矩阵已接入真实 KWin X11/Wayland
desktop workflow。X11/Wayland workflow 和 direct raw 还让打包 Electron 从 preload
`apps.list()`/`apps.launch()` 穿过生产 IPC 与有界 GIO 再启动 KCalc，校验私有结果、观察
唯一新增窗口并精确关闭；Wayland 使用 compositor-owned UUID 与 KWin close 回执，8 项
解析器测试拒绝畸形、旧、零 PID 或多窗口证据。SDDM Wayland raw 副本还以 root-owned
只读固定请求走同一 preload IPC，但保持生产 SDDM/PAM 会话。raw 完成门和最终发布 evidence
同时绑定 X11 九窗口、direct preload IPC 与 SDDM Wayland preload IPC marker。14 项
evidence-binder 测试分别包含“缺失 runner preflight marker”、“缺失 direct IPC marker”和
“缺失 Wayland IPC marker 必须拒绝”的反例。
上述 Linux workflow/raw 尚未在本机执行；Dolphin UI 双击、
远端 HTTP/HTTPS、损坏输入、广泛 codec/Archive 格式、截图授权、辅助技术、多用户覆盖与
跨槽回滚仍需 Linux raw 和真机实跑。

Echo Desktop 的原生应用 IPC 不再在 `spawn` 后立即返回伪成功。它使用固定参数
`execFile("/usr/bin/gio", ["launch", desktopFile])`，等待有界 helper 退出；只有零退出才返回
`ok=true`。程序缺失、超时、异步失败和非零退出都会以有界错误回到 renderer，Dock 通过
toast 显示，不再出现“点了没反应但内部声称成功”。10 项专用 Node 测试还覆盖固定
list/launch 路径、私有原子 marker、错误/超时不发布、非 canonical 输出、应用 ID 注入、
普通会话跳过、无 credential 请求失败、root Wayland 请求的 session 限制，以及固定
root-owned 只读文件对错误权限、内容和符号链接的拒绝。

Debian Trixie 的实际包名与 desktop identity 见
[Kate](https://packages.debian.org/trixie/kate)、
[Okular](https://packages.debian.org/trixie/okular)、
[Gwenview](https://packages.debian.org/trixie/gwenview)、
[Ark](https://packages.debian.org/trixie/ark)、
[Haruna](https://packages.debian.org/trixie/haruna)、
[Spectacle](https://packages.debian.org/trixie/kde-spectacle)、
[KCalc](https://packages.debian.org/trixie/kcalc) 与
[xdg-utils](https://packages.debian.org/trixie/xdg-utils)。

## 持久应用商店与沙箱

系统 root 由 A/B 镜像整体替换，所以面向用户的应用不能以 `apt install` 写入当前
`/usr`。镜像显式安装 Flatpak、KDE Discover 的 Flatpak backend、KDE portal 和
freedesktop 桌面/MIME 工具。系统级应用与 runtime 位于 `/var/lib/flatpak`，用户级
应用位于 `~/.local/share/flatpak`；对应的 `/var` 和 `/home` 都在 root A/B 之外。

`echo-app-catalog.service` 在 `/var` 挂载后、SDDM 之前尝试执行一次。它从签名 root
中的 `flathub.flatpakrepo` 建立系统 remote：定义文件包含上游公开 GPG key，并由脚本
固定完整文件 SHA-256；这一步不下载 catalog metadata，也不会让无网络阻塞登录。
同名 remote 如果已经指向其他 URL 会失败关闭而不会被覆盖。成功标记写到持久
`/var/lib/echo-os`，因此后续 root 更新不会重复覆盖管理员的应用源决定。

可见的 `Echo 应用商店` desktop entry 只以 Discover 官方的
`--backends flatpak` 参数启动；Debian Discover 自带、可写当前 root 的 PackageKit
通用入口被 `/usr/local/share` 中的高优先级隐藏项屏蔽。系统组件继续只由签名整机
更新交付。Flathub 是第三方应用目录，不等于 Echo OS 信任根；应用提交者身份、权限
声明和沙箱例外仍需要用户审查。

Echo session 把系统与用户 Flatpak export 加入 `XDG_DATA_DIRS`，并用
`XDG_CURRENT_DESKTOP=Echo:KDE` 选择仓库中的 `echo-portals.conf`。Shell 能发现新导出的
desktop/icon，按 `StartupWMClass` 关联窗口，并通过 `gio launch` 启动 desktop file，
不会把它的 `Exec` 字符串交给 shell。目录会定时和窗口重新可见时刷新。

`ab-update-smoke.yml` 先用生产安装器写出基线磁盘，再让真实 OEM 服务完成首启并将
machine-id、账户 marker/密码哈希和地区状态持久化；测试 autologin 从该生命周期 raw
删除后才开始更新。`smoke-ab-update.sh` 只额外写入 Flatpak sentinel 和不自动连接的
NetworkManager profile，root/hash/signature/UKI 更新后逐字节读回全部设备状态，使用现有 OEM 状态
进入新版生产登录，再运行三次失败和旧版 SDDM 回退。当前本机仍只能验证源码、策略
单测和 mkosi 契约，不能把未运行 workflow 冒充成更新或第三方应用运行证据。

## 跨槽设备身份

构建结束会把通用镜像的 `/etc/machine-id` 截断为空，避免所有克隆设备共享构建时
身份；但让 systemd 在每个 root 自己生成 ID 又会导致 A/B 切换时身份漂移。因此
`echo-machine-id` 被编入 mkosi 的 systemd initrd。dm-verity 已经用 UKI 内嵌 roothash
打开并挂载只读 sysroot 后，`echo-machine-state-initrd.service` 解锁 `echo-var`，在
`initrd-root-fs.target` 和 switch-root 放行前原子创建或复用
`/var/lib/echo-os/machine-id`，再将它只读 bind 到新 root 的 `/etc/machine-id`。
同一阶段把持久 `/var/lib/echo-os/etc-overlay` 作为 upper/work 挂到只读 root 的
`/etc`，所以账户、主机名和其他合法系统状态更新不会改写已签名 root。

ID 必须是非零、32 位小写十六进制；损坏状态、缺少 `echo-var`、不安全的 mount target
或 bind 失败都会让普通启动失败关闭，而不是静默生成另一个身份。合法旧 root ID 可以
在迁移时作为首次种子。`echo-var` 必须保持挂载到 switch-root：overlay 的 upper/work
和 machine-id 源文件都依赖它，提前卸载会得到 `EBUSY` 或破坏 overlay。var、swap、home
三个 GPT 项均设置 `NoAuto=yes`，只允许仓库内明确的 `crypttab`/`fstab` 加密映射挂载，
不会让 GPT 自动挂载用构建期 machine-id 抢先接管 `/var`。出厂重置清空 `/var` 后，
下一次普通启动生成新的设备身份，这是有意的所有权边界。

`echo-machine-identity-health.service` 在 SDDM 和 credential 测试桌面之前比较活动与
持久 ID。串口只记录 `systemd-id128 --app-specific` 的不可逆派生值，不泄露原始
machine-id。A/B harness 在临时设备 `/var` 写入随机 ID，换 root 后读回，并比较正常
新版启动与最终回滚启动的派生值。源码检查会验证自定义 initrd 配置、服务依赖、
`x-initrd.attach` 和 overlay 所需组件；当前 macOS 工作区无法执行真正的 initrd
dm-verity/mount/switch-root，因此实际时序仍必须由 Linux raw 冷启动证明。

## 跨槽网络配置

NetworkManager 默认把系统 keyfile 写到可替换 root 上的
`/etc/NetworkManager/system-connections`。镜像使用 NetworkManager 支持的
`[keyfile] path=` 将唯一受支持的可写位置改为
`/var/lib/NetworkManager/system-connections`，目录为 root-only `0700`，profile
保持 `0600`。因此 Wi-Fi、802.1X 和 VPN 配置不会因 root A/B 替换消失。

`echo-network-state-prepare.service` 在 NetworkManager、SDDM 和测试桌面前执行。
首次启动可从旧 `/etc` 位置迁移普通、root 所有、权限严格为
`0600`/`0400` 的文件；符号链接和可被其他用户读取的文件被忽略，同名持久
profile 永不覆盖。NetworkManager 的 systemd drop-in 要求这个准备服务成功；
登录和桌面冷启动 harness 也要求 `ECHO_NETWORK_STATE_READY` 证据。

`smoke-ab-update.sh` 向临时设备的持久 `/var` 写入一个合法但不自动连接的
Ethernet profile，在 root 替换后及最终回滚后分别逐字节比较。本机现已通过
迁移、权限拒绝和不覆盖单测；Linux raw 中真实 NetworkManager 启动、Wi-Fi
重连及跨槽行为仍待实跑。未加密 `/var` 不能阻止离线攻击者读取 profile 内的
密码或私钥，所以它不替代后续全盘加密/TPM 验收门。

主机入站策略由 Debian firewalld 2 的 nftables backend 明确承担，KDE System Settings
通过 `plasma-firewall` 和系统 firewalld/PolicyKit D-Bus 提供管理员入口。fresh image 的
`echo-public` zone 只允许 DHCPv6 client；不默认开放 SSH、Agent、端口、rich rule、
masquerade 或 forwarding。`StrictForwardPorts=yes` 阻止 Docker/Podman 发布端口绕过主机
授权；daemon 停止时保留已加载规则，reload 窗口对新流量采用 DROP。除 DefaultZone 可由
已授权管理员改变外，backend、table owner、RPF 与转发策略都是 boot invariant。
`echo-firewall-health.service` 被 NetworkManager、SDDM、直接桌面和 boot blessing require，
并验证 D-Bus owner、nft table、默认 zone 和 fresh-image runtime surface。portable 12 项
测试及静态镜像门已通过，真实 raw kernel filtering、KDE 提权、外部扫描、VPN/多网卡、
休眠和容器端口行为仍待 Linux/真机。

## 可移动存储与便携设备

镜像显式安装 UDisks2、Dolphin、KIO extras、MTP runtime，以及 FAT、exFAT、NTFS、
ext4、Btrfs、XFS 的检查与创建工具。桌面沿用 UDisks2 的 system D-Bus 和 PolicyKit
授权边界：用户在 Dolphin 中选择设备时才请求按需挂载，Electron renderer 没有原始
块设备、挂载点或任意存储命令 IPC。系统不会为了通过冷启动测试而自动挂载未知介质。

`echo-removable-storage-health.service` requires `udisks2.service`，并在 SDDM、直接桌面
和 boot blessing 前检查 daemon、D-Bus/udev/PolicyKit 激活文件、Dolphin/KIO MTP 与
全部文件系统工具均来自 root-owned immutable image。它只调用 `udisksctl status`，
不会挂载、卸载、格式化或断电介质。portable 测试可在非 Linux 主机故障注入这些边界；
真实 USB/SD/光驱/MTP 手机、热拔插、损坏/只读/加密介质和休眠唤醒仍由 Linux raw 与
真机矩阵验收。Debian 当前接口与文件清单见
<https://packages.debian.org/trixie/udisks2>、
<https://packages.debian.org/trixie/kio-extras>、
<https://packages.debian.org/trixie/exfatprogs> 和
<https://packages.debian.org/trixie/ntfs-3g>。

## 私密本地打印

镜像显式安装 CUPS scheduler/client、OpenPrinting filters、KDE Print Manager、
`cups-pk-helper`、Avahi 和 `ipp-usb`。普通应用走标准 libcups/Qt/GTK 打印接口；
Electron renderer 没有添加打印机、提交或取消作业的存储/命令 IPC。打印机管理由系统
D-Bus/PolicyKit helper 执行，并使用现有 KDE PolicyKit session agent 显示授权界面。

root-owned `cupsd.conf` 只接受 `localhost:631` 与 `/run/cups/cups.sock`，明确关闭
LAN browsing、默认共享和 CUPS Web UI；解析器拒绝 wildcard Port/SSLListen、include、
ServerAlias 与 Allow 覆盖。页日志、完成历史和提交作业文件都不保留，日志固定有界。
`ipp-usb` 只监听 loopback，verbose IPP/HTTP/USB payload trace 关闭；主机防火墙也不开放
CUPS 或 mDNS 入站。已知的远程 IPP/IPPS 地址仍可由用户主动添加，网络自动发现和把本机
变成打印服务器不在默认边界内。

`echo-printing-health.service` requires CUPS socket/scheduler，并在 SDDM、直接桌面和
boot blessing 前只读检查 scheduler API、KDE KCM、PolicyKit mechanism、IPP backends、
PDF/raster filters 和 driverless USB 链。`/var/spool/cups` 必须解析到加密
`/dev/mapper/echo-var`；健康服务启用 `PrivateDevices=yes`，不能访问打印机设备或修改队列。
10 项策略测试、6 项运行时故障测试和静态镜像门已通过；真实 USB/IPP 打印及作业隐私仍
必须由 Linux raw/真机验收。上游边界见
<https://openprinting.github.io/cups/doc/man-cupsd.conf.html>、
<https://openprinting.github.io/cups/doc/security.html>、
<https://packages.debian.org/trixie/print-manager> 与
<https://packages.debian.org/trixie/ipp-usb>。

## 原生文档扫描

镜像显式安装 SANE library/backend、`scanimage`、`sane-airscan` 与 KDE Skanpage。
普通 USB 扫描仪由 Debian libsane udev 规则授予 `scanner` group；driverless 多功能 USB
设备复用 loopback-only `ipp-usb` 并通过 eSCL backend 使用。LAN eSCL/WSD 不在开机时探测，
只在用户打开 Skanpage 或其他 SANE 应用并请求设备枚举时发现。renderer 没有扫描设备、
输出路径或命令 IPC。

root-owned `airscan.conf` 固定 `protocol=auto`、bounded fast WSD discovery 和
`pretend-local=false`，关闭 console debug 与 payload hexdump，并拒绝 trace 目录或固定第三方
scanner endpoint。`saned.socket` 由镜像 preset 禁用；一旦 scanner-sharing listener 被启用
或激活，`echo-scanning-health` 会阻止图形登录和 boot blessing。健康服务自身启用
`PrivateDevices=yes`、`PrivateNetwork=yes`，只调用 `scanimage --version` 验证 loader，绝不
枚举本机 USB 或 LAN 设备。Skanpage 将 PDF/图片写入用户明确选择的位置，系统没有全局扫描
spool 或 Echo 历史。

7 项策略测试、6 项运行时故障测试和静态镜像门已通过；平板/ADF USB、driverless eSCL、
按需 LAN eSCL/WSD、多页 PDF/图片、拔线取消、权限拒绝、休眠和无 payload 日志仍必须由
Linux raw/真机验收。上游接口见
<https://packages.debian.org/trixie/sane-utils>、
<https://packages.debian.org/trixie/libsane1>、
<https://packages.debian.org/trixie/sane-airscan>、
<https://packages.debian.org/trixie/skanpage> 与
<https://apps.kde.org/skanpage/>。

“关于本机”现在承载系统更新状态和安装入口。定时 fetch 仍只认证并缓存；它将有界的
公开状态写入 `/var/lib/echo-os-update/status.json`。Electron 只接受这个固定 root-owned
文件，并且安装 IPC 不接受任何参数，只能启动 PolicyKit 绑定的
`/usr/lib/echo-os/echo-os-update-apply`。管理员授权后仍调用生产
`echo-os-update-channel apply`，因此 manifest/GPG/dm-verity/UKI/check-new/inactive-slot
约束没有图形化旁路。完成后用户必须显式重新启动；Linux raw 中的真实 PolicyKit 点击、
A/B 安装和首次验证启动仍是剩余设备验收门。

## 跨槽地区、键盘与时区

`/etc/locale.conf`、`/etc/vconsole.conf` 和 `/etc/localtime` 都位于可替换 root。
`echo-region-state-restore.service` 在 OEM、SDDM 和 credential 桌面前运行：首次
启动将镜像默认值验证后写入持久 `/var/lib/echo-os/region-state.json`，
以后的 root 则严格验证并通过 `localectl`/`timedatectl` 恢复。状态必须是
root 所有的 `0600` 普通文件，schema 或值不受新镜像支持时失败关闭。

镜像显式安装 `kbd`、`console-data`、Debian console setup 与 `tzdata`，并编译
`en_US`、`en_GB`、`zh_CN`、`zh_TW`、`ja_JP`、`ko_KR`、`de_DE`、`fr_FR`、
`es_ES`、`pt_BR` 的 UTF-8 locale；中性默认仍为 `C.UTF-8`/`us`/`UTC`。
OEM 程序只接受安装后系统 catalog 返回的精确值，所有系统命令都是固定
argv，不经 shell。

`echo-region-state-capture.path` 监听三个活动配置；每次更新在写 inactive
root 前也强制捕获。A/B harness 注入非默认 `zh_CN.UTF-8` + `us` +
`Asia/Shanghai`，要求新 root 和自动回滚的旧 root 都打印精确 readiness 且
JSON 逐字节不变。当前已有 10 项策略单测与镜像静态契约，但真实
systemd-localed/timedated D-Bus 激活、console/X11 效果和跨槽恢复仍等待 Linux raw。

## A/B 更新与启动回退

镜像预留两套同类型 dm-verity 槽。当前版本由
`echo-root-<version>`、`echo-root-<version>-verity` 和
`echo-root-<version>-verity-sig` 三个只读分区组成；另一套 root/hash/signature
分区均以 `_empty` 标记。构建同时输出带 UUID 的三份分区载荷和版本匹配的 UKI。
UKI 只内嵌一个 `roothash=`，不再信任可变 `root=` 参数；root 和 hash 分区 GPT UUID
分别由该 roothash 的前、后 128 位派生，`systemd-sysupdate` 通过 `@u` 将同一 UUID
写回目标 GPT，因此标签、内容、哈希树、签名和启动项不能被任意拼装。

生产构建必须通过 `ECHO_UPDATE_KEYRING` 选择二进制 OpenPGP 公钥环；mkosi 用目录
extra-tree 将它放入 root，postinst 拒绝私钥/opaque packet，成品 raw 还会逐字节读回
比较。设备端 `echo-os-update` 先对 manifest、签名、三份分区载荷和 UKI 做有大小上限的
结构 preflight，再用 root 所有、不可被普通用户修改的公钥环验证 detached signature；
随后严格要求目录只有同一版本的 root、hash tree、verity signature、一个 UKI 和两份
manifest 文件，逐项核对 SHA-256 并测试三条 zstd 流。它还会用内置发布证书核对签名
JSON、PKCS#7 signer、roothash 派生 UUID 和 UKI roothash，之后才调用
`systemd-sysupdate`。`10-root`、`20-root-verity`、`30-root-verity-sig` 先写 backing
partitions，带三次启动计数的 `90-uki` 最后发布启动项。正式 apply 还会在写 inactive
root 前同步当前本地密码哈希、显示名和主机名，并
独立同步 locale、keymap 和 timezone 到持久 `/var`；任一同步失败时更新按设计中止，
避免新版 root 使设备所有者无法登录或回退地区设置。
详情见 `deploy/update/README.md`。

成品 root 还交付 HTTPS-only 的 `echo-os-update-channel`、Debian CA 信任链、
fetch-only systemd service 和默认启用的 timer。轮询先只下载有界 manifest/signature，
由 public-only keyring 验签后才按已认证清单流式下载五份载荷，并以 root-only staging、
fsync 和 rename 原子发布到 `/var/cache/echo-os/updates/<version>`；同版本替换、redirect、
内容编码、越界 URL、超限/空响应与哈希错误都失败关闭。缓存只保留认证候选和一个最近
历史版本，显式 apply 全程持有通道锁。timer 只执行 `fetch`，不会调用 sysupdate、apply
或 reboot；管理员必须明确运行 `echo-os-update-channel apply`，随后仍经过 `check-new`、
候选版本精确匹配与全部生产验证。源码中配置的 echo-age.com URL 尚未做线上可用性验证，
不能据此宣称发布端已经上线。

每个 root 还必须带显式 `ECHO_UPDATE_TRUST_GENERATION` 生成的 canonical trust policy，
绑定实际 keyring SHA-256、当前 primary fingerprints 和累积 retired fingerprints。
只有 restore/crash/Agent/桌面或登录健康门全部通过后，boot blessing 前的 trust service
才把下一代公钥以断电可恢复 pending→active 事务晋级到加密持久 `/var`。生产 updater 和
通道优先使用这份托管公钥，所以 A/B 回滚到旧 root 也不会复活已退休密钥；显式 `/etc`
管理员 override 仍优先。轮换必须是连续两代：旧钥签名的 old+new bridge，然后新钥签名、
new-only 且把 old 列入 retired 的最终代。跳代、同代换 keyring、静默删钥或取消退休都
失败关闭。当前本机只有 portable 事务/回滚测试，仍需真实两版签名 A/B workflow 取证。

release 工具必须同时拿到外部完整签名 fingerprint 和镜像选择的公钥环；它在目标
文件系统临时目录压缩、签名，用该公钥验签并运行同一严格 verifier 后才原子发布。
仓库不包含开发私钥，也不包含可冒充生产信任根的固定测试公钥。CI 身份每次临时
生成；生产签名发布链和密钥托管仍不能因此算完成。

签名 bundle 之后还有独立的 stable 仓库晋级边界。
`deploy/update/publish_update_repository.py` 只接受同一严格 verifier 可解析且被外部
public-only keyring 验签通过的完整目录，先在 web root 同文件系统中逐文件复制、fsync
并以 mode-`0555` 的不可变 sequence/version 目录 rename，再用一个相对 symlink 原子切换
`stable/x86-64`。首次序列必须为 1，之后只允许精确加 1；同序列异字节、同版本替换、
倒退、跳号、并发发布及通道逃逸都会失败，release rename 后而 stable 切换前的中断可由
同一发布重试完成。portable 测试覆盖这些边界，但线上 TLS/web server、缓存策略、监控和
真实签名内容仍要部署后验收，不能把本地仓库发布器当作公网端点已经存在。两个 Linux
workflow 还会用临时真实 GPG 身份连续发布两个版本、由 `gpgv` 复核通道并拒绝倒退；当前
macOS 工作机缺少 GPG，所以这条仍是待 runner 实跑的门，不是本机通过结果。

`ab-update-smoke.yml` 会在隔离 CI 中临时生成安装、更新和 Secure Boot 身份，分别构建
0.2.0 与 0.2.1。它安装并完成 0.2.0 OEM 首启，先对新版三联分区和 UKI 运行完整
`veritysetup verify`，验证签名更新能以相同本地身份进入 0.2.1 SDDM；随后篡改新版
root，必须先捕获 dm-verity 明确拒绝该集合，再连续消耗三次 boot count，最后要求
0.2.0 桌面和 SDDM 以相同设备/账户状态重新健康启动。测试私钥只存在于该临时 runner，
不会进入镜像或构建产物。

## 加密用户备份与迁移 staging

主镜像安装 Debian `restic`，并交付 `/usr/bin/echo-os-backup`。第一版只接受挂载在
`/mnt/echo-backup` 的独立 ext4/XFS/Btrfs/F2FS 块设备文件系统；仓库固定为
`echo-os-user`，数据源固定为 `/home/echo` 与 `/var/lib/echo-agent`。`/etc`、
`/var/lib/echo-os`、NetworkManager 密钥、TPM/LUKS token、设备 machine-id 和密码哈希
从未进入 restic 参数。口令通过匿名 memfd 传入以 UID 1000 运行且环境固定的 restic，
不会出现在 argv、环境、持久临时文件或 shell 中。

`backup` 和 `restore` 都拒绝任何本地/远程/closing 的 echo 会话，停止 SDDM 与
Agent 后再次检查，并拒绝所有仍带 UID 1000 的进程，从而关闭新登录与同用户后台进程
竞态；所有被停止的服务在成功、失败或部分 stop 后都必须恢复，Agent 还要通过镜像内
健康验证器。备份从 JSON summary 绑定准确 snapshot ID，执行完整
`restic check --read-data` 并核对认证索引。`restore` 也在关闭登录入口和全量校验后，
只向新的 `/home/echo/.echo-restore-staging/<time>-<snapshot>` 写入，使用
`--overwrite never`，并拒绝额外根目录、非 UID 1000 owner、特殊文件和越界 symlink；
它不会自动提升或覆盖活动数据。Recovery 中的 `restore-plan` 会把精确 staging、仓库、
snapshot 以及 Home/Agent 新旧树摘要绑定为 24 位事务 ID；只有计划打印的 token 才能
`restore-promote`。跨 `echo-home`/`echo-var` 的每个 rename 边界都有 root-only 原子 journal，
中间状态会阻止 Agent、SDDM、直接桌面和 boot blessing。完整 promotion 只进入试运行，
旧数据保留在 UID 1000 无法遍历的 `0700` root 容器；`restore-rollback` 恢复旧树并保留试运行
改动，只有显式 `restore-commit` 删除旧树。完整操作与 systemd encrypted credential 示例见
`deploy/backup/README.md`。

该 unit 默认不启用，因为它需要管理员先准备独立仓库、加密凭据和离线时间窗。本机已经
执行策略单测与静态镜像检查。image workflow 会复制完成 OEM 的安装后 raw，注入只存在于
该副本的开机 gate，附加独立 ext4 virtio 盘并通过 systemd credential 提供随机测试口令；
guest 创建 ACL、xattr、64 MiB 稀疏文件和相对 symlink，完成备份后翻转 pack 字节，要求
错误 credential 失败、仓库只剩 2 MiB 时备份失败且仓库保持一致、`check --read-data`
拒绝翻转过字节的 pack，再修复并验证 staged restore 的内容与元数据。随后它把同一 staged
整盘复制为两个独立 NBD 分支：先验证错误 promotion token 被拒绝，再分别执行
promote→rollback 与 promote→生产 SDDM 冷启动→commit；统一日志还要求 repository、snapshot
和 transaction ID 全程一致。目前 13 个备份策略测试、7 个事务故障/恢复测试、证据绑定器
测试和静态镜像契约已在本机通过。该 workflow 尚未在 Linux runner 跑绿，真实外置盘断连、
遗失口令处置和物理设备迁移仍是缺口，所以仍是验收定义而非结果。

## 独立恢复环境

`packaging/recovery/` 生成一个把 kernel、initrd、诊断工具和恢复服务封装在一起的
Recovery UKI。主镜像构建会先检查 ESP 剩余容量，再把它安装到 `EFI/Linux/`；
`loader.conf` 继续让 `echo-os_*` 成为默认项，因此恢复环境不会抢占普通启动。

恢复自动路径只读取磁盘、分区和 boot 状态。`check-root` 会从一个未挂载的
`echo-root-<version>` 定位同版本 hash/signature 分区，核对发布证书 PKCS#7 签名、
roothash 派生 UUID，并运行完整 `veritysetup verify`。已签名 root 禁止原地修复，因为
`e2fsck -y` 一类写入会让原签名失效；恢复方式只能是启动另一套已验证槽，或从通过
身份认证的安装/更新包重新部署。`factory-reset` 还要求整盘目标、恰好存在
`echo-var`/`echo-swap`/`echo-home`，以及精确的破坏性确认词。它重新创建三个 LUKS2
分区、轮换恢复密钥并写入 release 授权的 signed-PCR11 TPM2 token。恢复 UKI 不依赖
root A/B 任一槽，但物理恢复控制台仍是高权限入口，必须由 Secure Boot、设备加密和
物理访问策略共同约束。

## 安全与产品边界

- 构建输入固定在 Debian 2026-08-25 snapshot，并保留仓库签名校验；升级镜像依赖
  时必须显式更新 snapshot、版本和 manifest。成品中的 apt source 回到 Debian 官方
  仓库以便镜像构建和受控维护；普通 `apt install`/`apt upgrade` 会只修改当前 root，
  不是受支持的用户应用或签名原子系统更新路径。
- root 登录默认锁定；生产桌面由 SDDM/PAM 登录后的 `echo` 本地账号运行，首次
  设置后该账号用自己的密码执行 `sudo`。Electron Chromium sandbox 在镜像内设置
  为 root 所有的 `4755` helper，不使用 `--no-sandbox`。
- `/var`、swap 和 `/home` 使用独立 LUKS2 Discoverable Partitions，并标记为显式
  factory-reset 候选；恢复命令要求整盘、精确确认词并轮换恢复密钥和 TPM2 token，
  没有自动触发删除它们的普通启动路径。ESP 与 A/B root 为保证 UEFI/UKI 启动和原子
  更新而未加密；A/B root 由 dm-verity 验证完整性，但内容本身不保密，所以这不是
  “整盘所有分区加密”。
- Flatpak/Discover 持久应用层、固定 remote 定义、KDE portal、Shell 动态发现和 A/B
  sentinel 已有源码与静态测试；仍需 Linux raw 上真实联网安装、权限提示、重启、
  升级和卸载取证。
- 通用镜像空 machine-id、systemd initrd 持久 bind/加密 `/etc` overlay、会话前健康门
  与 A/B 派生 ID 对比已经
  定义；仍需 raw 镜像证明 PID 1 前的真实 mount 时序、缺盘失败和 factory reset 换 ID。
- NetworkManager profile 已改写持久 `/var`，具有 root-only 权限、一次性安全迁移、
  启动门和 A/B 更新/回滚 sentinel；仍需 raw/真机验证真实 Wi-Fi、VPN 及秘密保留。
- locale、console/X11 keymap 和 timezone 已有独立持久状态、OEM 选择、变更捕获、
  会话前恢复门和非默认 A/B 对比；仍需 raw 证明实际系统服务与键盘效果。
- X11 会话已组装 xss-lock/XSecureLock/PAM 锁定、空闲/休眠前锁定和
  logind 退出路径；仍需 raw/真机验证密码、输入 grab 及休眠唤醒安全性。
- X11/Wayland 会话已直接监管 PowerDevil，并把 UPower、Power Profiles 和冷启动完成
  门串联；仍需 raw/真机验证电池、交流电、合盖、电源键、DPMS、平台 profile 与休眠。
- KWin UUID/desktopFileName 脚本桥、compositor 输出/缩放协议、mode-`0600` 会话
  socket、双端快照验证和带回执动作已组装；镜像也包含 KWin wrapper/DRM Wayland/
  XWayland/KScreenLocker 候选链、编译型 Liquid Glass Effect 与 compositor 下层规则。
  原生 Effect 的静态合同和 raw 冷启动门已定义，但尚需 Linux KWin 6.3.6 环境完成实际
  编译、加载、帧率及显存证据。双屏 1.25× + 锁屏 gate 已定义
  但本机未执行，默认启动仍是 X11；必须取得 Linux CI、raw Wayland/XWayland 和物理
  多屏证据后才能切换默认 provider。
- A/B dm-verity 三联、签名 bundle 验证、三次启动计数、桌面健康门和破坏性回退 harness 已经
  定义；还必须让真实 Linux runner 完成双版本升级/失败/回退，才能报告整机通过。
- 独立 Recovery UKI、ESP 注入、dm-verity root 验证、禁止原地修复和限定数据重置已经
  实现源码与
  VM/NBD harness。重置 gate 会在副本上检查旧密钥失效、新 signed-PCR11/SRK token、
  不可变分区未变，并用同一虚拟 TPM 冷启动；但还没有本工作区中的 Linux 执行产物，
  因此不能报告恢复启动或重置闭环已实跑。
- Recovery 还定义了无损恢复密钥轮换和 TPM 清除/换主板后的重新绑定。轮换先把新密钥
  建立到全部数据卷再撤销旧密钥；CI 会保持解密后数据逐字节一致，使用第二个独立
  swtpm/SRK 重绑，并只用替换后的 TPM 冷启动。该 gate 同样尚未在本工作区 Linux
  runner 上产生执行证据。
- 默认 bring-up 仍明确 `SecureBoot=no`；只有外部密钥模式才启用签名和 enforcing
  OVMF。一次性 CI 密钥能验证机制，不代表生产私钥托管、OEM 灌装或真机信任链完成。
- OEM 首启、本地密码、无自动登录的 SDDM/PAM 会话与 CI credential 分流已经完成
  源码和策略测试，但仍需在 raw 镜像上分别实跑交互首启与登录失败/成功路径。
- 当前镜像只定义 x86-64 VM bring-up。签名整盘安装器、A/B root dm-verity 三联、
  UKI roothash 绑定与 `/var`、swap、`/home` 的 TPM2/恢复密钥保护已经进入源码 gate；
  尚未在本工作区的 Linux runner 实际完成 dm-verity 冷启动/篡改拒绝 workflow。
  ARM64、生产 TPM/Secure Boot 灌装，以及真机硬件矩阵仍是后续验收门。
