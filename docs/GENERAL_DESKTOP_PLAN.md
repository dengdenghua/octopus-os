# Echo OS 目标 C：通用桌面系统架构与验收门

> 产品目标：不是 NAS 网页桌面，也不是单应用 kiosk，而是像 macOS / Windows
> 一样，能安装和运行普通本地应用、管理真实窗口、文件、设备与系统生命周期的
> 通用个人桌面系统。

## 架构决定

Echo OS 不从零实现显示协议、输入分发和 GPU 合成。窗口管理与合成由成熟的
KWin 承担，Echo Shell 实现桌面、Dock、菜单栏、启动器和 Agent 入口：

```text
Linux kernel / systemd / logind / udev
                 │
       KWin compositor + window manager
        ├── native Linux application windows
        ├── XWayland compatibility windows
        └── Echo Shell surfaces
             ├── desktop / wallpaper / widgets
             ├── menu bar / Dock / launcher
             └── Agent workbench / web applications
```

KWin 6 的官方脚本接口已经提供窗口堆栈、活动窗口、应用 desktop-file identity、
最小化状态和关闭动作，足够成为 Echo Shell 的窗口真相源：
<https://develop.kde.org/docs/plasma/kwin/api/>。

## 迁移顺序

早期 `deploy/native-shell/` 的 Cage 单应用 kiosk 已退出产品路径。旧安装命令现在只委托
当前 KWin 通用桌面安装器，不再安装 Cage、启用自动占座会话或用删除式同步覆盖设备目录；
旧 unit 只作为 bring-up 证据保留且不进入目标 C 镜像。这样旧文档或自动化不会把已经具备
SDDM/PAM、多窗口、锁屏、电源和通知链的系统降级回单应用外壳。

### C0 · 可运行的真实多窗口桌面（当前实现）

- `deploy/desktop-session/` 启动 Xorg + KWin X11 + Echo Shell，不再由 Cage 独占。
- `electron-builder` 生成 Linux 应用目录/AppImage；装机会话优先运行打包后的
  `echo-os-desktop`，源码 `npx electron` 只保留为开发回退。
- Electron 在 `ECHO_SHELL_MODE=desktop` 下是无框最大化桌面窗口，而不是
  fullscreen/kiosk；KWin 可在其上方合成普通 GUI 应用。
- `electron/native-windows.cjs` 通过 freedesktop EWMH/wmctrl 枚举、聚焦、恢复、
  最小化、关闭窗口；IPC 只接受严格校验的窗口 id，不接受命令文本。
- Dock 轮询真实窗口状态：已运行应用显示状态点，单击聚焦已有窗口，右键可显示、
  最小化或退出。
- `desktop-session-smoke.yml` 在 Debian stable 的隔离 Xvfb + KWin 会话里启动打包
  Shell，并用真实 X11 应用持续验证枚举、聚焦、最小化、恢复和关闭契约。

这一阶段选择 X11 是 bring-up 手段，不是最终产品边界。Debian stable 提供
`kwin-x11`，能在 VM 和通用 PC 上先验证真正的多窗口闭环：
<https://packages.debian.org/stable/kwin-x11>。

### C1 · KWin Wayland 原生桥

- 最小权限 KWin 6 脚本与伴随服务已实现：脚本从
  `Workspace.stackingOrder` 订阅窗口增删、激活、最小化和 identity 变化，
  使用 `Window.internalId` UUID 与 `desktopFileName`。
- KWin 通过 session D-Bus 主动发布经过筛选的快照，并轮询仅含 UUID 的
  `focus`/`minimize`/`close` 白名单动作。伴随服务只向同一会话用户的
  mode-`0600` runtime socket 开放；D-Bus 入口核对 `org.kde.KWin` unique owner，
  且等 KWin 回执后才报告动作成功。
- 快照协议 v2 同时读取 `Workspace.screens`、输出逻辑几何和
  `Output.devicePixelRatio`，daemon 限制输出数、名称、坐标、尺寸、缩放范围与重复
  identity；因此多屏/HiDPI 状态也来自 compositor，而不是 renderer 推测。
- Electron 已按 `XDG_SESSION_TYPE` 选择 `ewmh-x11` 或 `kwin-wayland` provider，
  Wayland 快照还会在主进程再次验证后才交给 renderer。X11 隔离 smoke 已要求
  KWin 脚本发布真实 UUID 快照并执行关闭动作，防止桥在切换前腐化。
- `smoke-wayland-session.sh` 和 CI job 已定义双虚拟输出、1.25× scale、
  `wayland-info` 交叉检查以及 `GDK_BACKEND=wayland` 原生 GTK 窗口的 UUID 生命周期门；
  同一真实 KWin 会话还启动打包 Echo/Ozone Wayland，让 preload `apps.list()`/`apps.launch()`
  穿过 main IPC/GIO 启动 KCalc，要求私有结果、唯一新增非零 PID 的 compositor UUID、
  KWin close 回执、Echo AT-SPI marker 和干净退出。8 项 parser 测试拒绝畸形、旧、零 PID
  或多个窗口证据。
  gate 还调用 KScreenLocker 的 `org.freedesktop.ScreenSaver.Lock`，只有 compositor
  报告真实 locked 状态才成功。
  由于当前工作机是 macOS 且没有 KWin/容器运行时，这一 gate 尚未在本次工作区执行；
  CI/Linux 变绿前只算可执行验收定义，不算 Wayland 运行证据。
- 镜像已加入 `kwin-wayland`、KScreenLocker 和 XWayland，并交付独立的
  `Echo OS (Wayland Candidate)` SDDM 会话。候选链通过 KDE 官方
  `kwin_wayland_wrapper --drm --xwayland` 预分配 socket，等待
  `org.kde.KWinWrapper` 完成 D-Bus/systemd activation environment 同步，再逐项验证
  Wayland socket、XWayland authority、KWin bridge、KScreenLocker PAM 与 renderer
  原子就绪文件；关键服务消失时失败关闭。
- `smoke-login-image.sh` 只接受 `echo.desktop` 与 `echo-wayland.desktop` 两个精确
  session id；镜像 CI 会复制成品 raw、只在副本注入一次性 SDDM autologin，分别从
  Secure-Boot UEFI 冷启动默认 X11 和 Wayland 候选。Wayland 门要求 DRM KWin、
  XWayland、KScreenLocker、UUID bridge、打包 renderer、preload IPC 启动的唯一 KCalc
  UUID、精确 close 与持久设备状态同时就绪。IPC 请求仅存在于副本的加密 `/etc` overlay，
  为 root-owned `0444` 固定内容；它不替换生产 SDDM/PAM 路径、不启用 auto-exit，成品 raw
  也必须证明该请求不存在。
  该 workflow 尚未因本地未提交改动而运行，所以目前是 raw 验收定义，不是结果。
- KWin 脚本已按精确 desktop-file/WM identity 对 Echo 壳设置 `keepBelow`、
  `skipTaskbar`、`skipPager`、`noBorder` 与 `onAllDesktops`，解决普通全屏窗口覆盖层级。
  这仍是生产候选，不是 raw 运行证据；默认 session 保持 `kwin_x11`。
- 最终仍要把 Shell 拆成桌面层、菜单栏层、Dock 层，使用独立 compositor surface/
  layer-shell 明确层级与输入区，而不是永久依赖一个普通最大化窗口和 KWin 规则。

### C2 · 桌面产品能力

- 多显示器、缩放、旋转、热插拔、虚拟桌面和窗口恢复。
- 键鼠、触控板、触屏、输入法、剪贴板、拖放、通知、音频、蓝牙、网络。
- 系统设置、用户与会话、锁屏、权限提示、应用沙箱和文件选择门户。
- 原生安装包与应用商店；桌面文件、MIME、默认应用和协议处理。
- 无障碍树、键盘全操作、高对比度、减少动态效果。

应用安装这一条已经形成首个可验收切片。Echo 镜像显式安装 Flatpak、KDE Discover
Flatpak backend 与 KDE portal；系统安装保存在持久 `/var/lib/flatpak`，用户安装保存在
持久 `/home`，因此不会跟随 A/B root 替换而消失。Echo 应用商店通过 Discover 官方
`--backends flatpak` 白名单启动，通用 PackageKit 商店入口被高优先级 desktop entry
屏蔽，避免把会在换根时丢失的 deb 安装伪装成持久应用。Flathub 定义及公开验签密钥
随签名 root 交付并独立固定 SHA-256，首次启动只做本地 remote 注册，不等待网络。
Flatpak 官方说明系统/用户安装范围及 `.flatpakrepo` 公钥机制：
<https://docs.flatpak.org/en/latest/using-flatpak.html>；导出的 desktop/icon 路径见：
<https://docs.flatpak.org/en/latest/conventions.html>。

Shell 会话显式暴露两个 Flatpak export 目录并选择 KDE portal。启动器现在尊重 XDG
高优先级 `Hidden=true` 屏蔽、识别 Flatpak 来源和 `StartupWMClass`，每十秒及窗口重新
可见时刷新应用列表；真正启动由 `gio launch <desktop-file>` 解析 freedesktop 字段，
renderer 不再间接触发 `/bin/sh -c`。A/B harness 会在 `/var/lib/flatpak` 写入测试状态、
替换 root 后逐字节核对；生产登录 harness 还要求首次目录注册标记。由于本机不是
Linux，这些 raw 镜像路径目前仍属于已定义、未实跑的 CI 验收，而不是整机证据。

离线核心应用也已从“镜像里碰巧有几个程序”变成明确的整机合同。签名 root 固定交付
Dolphin、Konsole、Firefox ESR、Kate、Okular、Gwenview、Ark、Haruna、Spectacle 与
KCalc；镜像关闭 Recommends，因此另外显式交付 Ark 的 Debian 推荐 7zip、bzip2、unar、
unzip、zip 后端。`/etc/xdg/mimeapps.list` 只声明系统默认值，将目录、网页/链接、文本、PDF、图片、
压缩包和常见音视频分别交给对应原生应用，不设置 Added/Removed Associations，用户仍可
在加密 Home 中覆盖自己的默认选择。启动健康门只核对 root-owned 可执行文件、desktop
entry 和 MIME policy，启用 `PrivateDevices=yes`、`PrivateNetwork=yes`、`ProtectHome=yes`，
不会打开文件、启动 GUI 或枚举用户数据。19 项 portable 策略/故障/functional 测试和
五类无副作用 raw readiness gate 已进入整机合同。额外的 opt-in 会话诊断只在隔离 CI 或
VM credential 下创建 runtime-only 目录、文本/PDF/PNG/ZIP/WAV 和临时 loopback HTTP 页面，
经 `xdg-open` 逐一观察 Dolphin/Firefox ESR/Kate/Okular/Gwenview/Ark/Haruna 的真实
X11/Wayland 窗口与 fixture 标题；另外通过生产 `gio launch` 从不可变 root desktop entry 启动
Konsole/KCalc，核对原生应用身份，再通过生产固定动作接口精确关闭九个窗口。X11/Wayland workflow
与 direct installed raw 还从打包 Echo 的真实 preload `apps.list()`/`apps.launch()` 进入
main-process IPC 和有界 GIO，要求零退出、私有原子结果、唯一新增的非零 PID KCalc 窗口及
精确关闭；Wayland 额外绑定 KWin UUID/close 回执和 Echo AT-SPI marker。SDDM Wayland raw
副本还以 root-owned 只读请求穿过同一 preload IPC，同时保留生产登录路径。raw 完成门和
最终签名 evidence 同时绑定九窗口、direct IPC 与 Wayland SDDM IPC 结果，13 项
evidence-binder 测试会分别拒绝缺失的 direct/Wayland IPC marker。当前只证明这些 Linux
gate 的源码、10 项专用 Node、8 项 Wayland evidence 测试与 portable 逻辑，workflow 尚未
实跑；Dolphin UI 双击、远程 HTTP/HTTPS、损坏文件、
广泛编解码/压缩格式、辅助技术、多用户覆盖与更新回滚仍需 Linux raw/真机矩阵。系统应用包及 desktop identity 以 Debian
Trixie 的 [Kate](https://packages.debian.org/trixie/kate)、
[Okular](https://packages.debian.org/trixie/okular)、
[Gwenview](https://packages.debian.org/trixie/gwenview)、
[Ark](https://packages.debian.org/trixie/ark)、
[Haruna](https://packages.debian.org/trixie/haruna)、
[Spectacle](https://packages.debian.org/trixie/kde-spectacle) 和
[KCalc](https://packages.debian.org/trixie/kcalc) 为准。

设备身份也不能属于某一个 root 槽。通用镜像现在强制交付空 `/etc/machine-id`；
自定义 mkosi systemd initrd 在 dm-verity 已挂载只读 sysroot 后、switch-root 前解锁并
保持挂载 `echo-var`，创建或复用 `/var/lib/echo-os/machine-id`，再只读 bind 到活动
root。它同时用加密 `/var/lib/echo-os/etc-overlay` 为只读 vendor `/etc` 提供 upper/work。
var、swap、home 的 GPT 项设置 `NoAuto=yes`，由明确的 `crypttab`/`fstab` 加密映射负责，
避免 `/var` 在每设备 machine-id 生成前被 GPT 自动挂载按构建期 UUID 误识别。
因此 systemd、D-Bus 和桌面在 A/B 两边从启动第一刻看到同一身份，而克隆出的不同
设备不会共享构建机 ID。状态损坏或 `echo-var` 缺失会停止普通启动，用户仍可从独立
Recovery UKI 处理；factory reset 清空 `/var` 后才会有意生成新身份。systemd 对通用
镜像空 machine-id 与初始化顺序的要求见：
<https://www.freedesktop.org/software/systemd/man/latest/machine-id.html>。
健康日志只输出 `systemd-id128 --app-specific` 的不可逆派生值；A/B harness 比对更新
与回滚启动的派生值。initramfs 实际 bind 和跨槽一致性仍等待 Linux raw 运行证据。

网络连接也已从可替换 root 中拆出。NetworkManager 的官方 `[keyfile] path=`
配置把系统 Wi-Fi、802.1X 和 VPN profile 改存到持久
`/var/lib/NetworkManager/system-connections`；准备服务在 NetworkManager、SDDM
和桌面前失败关闭。一次性旧数据迁移只接受 root 所有且模式为 `0600`
或 `0400` 的普通文件，不跟随符号链接、不覆盖已持久文件。A/B harness
会在更新后和回滚后逐字节核对非自动连接的测试 profile；当前本机已通过
迁移/权限/不覆盖单测，真实 Wi-Fi 秘密保留与跨槽重连仍等待 Linux raw 和真机。
NetworkManager 对 `path` 和私有 profile 权限的定义见：
<https://networkmanager.dev/docs/api/latest/NetworkManager.conf.html>。

主机网络边界现在也从“依赖发行版默认”升级为显式系统能力。镜像安装 Debian
firewalld 2、nftables 与 KDE Plasma firewall KCM；新设备默认使用自定义
`echo-public` zone，只允许 DHCPv6 client，不开放 SSH、Agent、任意端口、masquerade、
rich rule 或 intra-zone forwarding。`StrictForwardPorts=yes` 使 Docker/Podman 的 DNAT
端口也必须经过管理员明确授权，renderer 没有直接防火墙 IPC。root-only baseline verifier
固定 nftables backend、table ownership、reload drop policy、IPv6 RPF 和 daemon 停止后保留
规则；默认 zone 可由 KDE 经 firewalld/PolicyKit 授权修改并留在加密 `/etc` overlay。
健康服务被 NetworkManager、SDDM、直接桌面和 boot blessing 共同 require，先核对
firewalld D-Bus、`inet firewalld` nft table、runtime/config zone 一致性；fresh raw 的五类
冷启动门还必须观察精确 `echo-public` deny marker。12 个 portable 测试与 1955 项静态
合同已通过；Linux raw 的真实 nftables、外部端口扫描、IPv4/IPv6、VPN、多网卡、容器端口、
休眠和 A/B 持久化仍需实跑。Debian 13 的正式包见
<https://packages.debian.org/trixie/firewalld> 与
<https://packages.debian.org/trixie/plasma-firewall>；backend、reload、RPF、退出和严格转发
语义来自 firewalld 官方配置接口：
<https://firewalld.org/documentation/man-pages/firewalld.conf.html>。

可移动存储现在使用 Debian UDisks2 的 system D-Bus/PolicyKit 边界，不向 Electron
renderer 暴露块设备、挂载点或任意命令 IPC。Dolphin 只在用户明确打开设备时请求挂载；
启动健康门只调用只读 `udisksctl status`，不会静默挂载、卸载、格式化或断电用户介质。
镜像显式交付 FAT、exFAT、NTFS、ext4、Btrfs、XFS 的检查/创建工具，以及 KDE KIO
MTP 插件和 Solid 设备动作。`echo-removable-storage-health.service` 在 SDDM、直接桌面和
boot blessing 前要求 UDisks2 service、D-Bus owner、PolicyKit/udev 激活文件、Dolphin、
MTP 和全部文件系统工具同时存在且为 root-owned immutable 内容；五类 raw 启动都要求
精确 readiness。portable 6 项故障测试和静态镜像门已通过；真实 USB/SD/光驱、MTP
手机、只读/损坏介质、热拔插、休眠与多文件系统矩阵仍需 Linux raw/真机。Debian
UDisks2 与 KIO extras 的交付接口见 <https://packages.debian.org/trixie/udisks2> 和
<https://packages.debian.org/trixie/kio-extras>。

本地打印也已从 Electron 依赖中偶然存在的 `libcups` 升级为完整系统链。镜像显式安装
CUPS scheduler/client、OpenPrinting PDF/raster filters、KDE Print Manager、
`cups-pk-helper` PolicyKit mechanism、Avahi 和 loopback-only `ipp-usb`。应用走标准
libcups/Qt/GTK 打印接口，renderer 没有添加打印机、提交或取消作业的命令 IPC。签名 root
中的策略只监听 `localhost:631` 与 `/run/cups/cups.sock`，关闭浏览、默认共享和网页管理；
页日志、完成历史与提交作业文件都不保留，driverless USB 只在 loopback 暴露并把 payload
trace 日志降到 error。`/var/spool/cups` 必须位于加密 `/dev/mapper/echo-var`。
策略解析器与启动门共有 16 项 portable 故障测试；SDDM、直接桌面、boot blessing 和五类
raw 冷启动都要求精确 printing readiness。真实 Qt/GTK/Electron 打印、PolicyKit 允许/拒绝、
USB 热插拔、手动 IPP/IPPS、卡纸/缺纸、取消重试、休眠和作业清除仍需 Linux raw/真机。
CUPS 配置语义见 <https://openprinting.github.io/cups/doc/man-cupsd.conf.html>；Debian 交付包见
<https://packages.debian.org/trixie/cups-daemon>、
<https://packages.debian.org/trixie/print-manager>、
<https://packages.debian.org/trixie/cups-pk-helper> 与
<https://packages.debian.org/trixie/ipp-usb>。

文档扫描也已进入原生桌面边界。镜像显式安装 SANE library/backends、`scanimage`、
`sane-airscan` 与 KDE Skanpage；USB 扫描仪走 Debian udev + `scanner` group，driverless
多功能 USB 设备复用 loopback-only `ipp-usb` 的 eSCL 链，LAN eSCL/WSD 只在用户打开
SANE 应用并请求枚举时发现。系统启动健康门启用 `PrivateDevices=yes` 与
`PrivateNetwork=yes`，只验证 backend loader/version，绝不执行 `scanimage -L`、
`sane-find-scanner` 或 `airscan-discover`。`saned.socket` 默认禁用且启用/监听时拒绝 boot
blessing；AirScan 保持 `pretend-local=false`，关闭 console debug、trace 与 payload
hexdump。扫描结果只写入用户在 Skanpage 中选择的位置，没有 Echo 全局 spool 或历史。
7 项策略测试、6 项运行时故障测试和五类 raw readiness gate 已进入整机合同；平板/ADF、
USB、driverless eSCL、LAN eSCL/WSD、多页 PDF/图片、拔线取消、休眠和无载荷日志仍需
Linux raw/真机。Debian 交付接口见 <https://packages.debian.org/trixie/sane-utils>、
<https://packages.debian.org/trixie/libsane1>、
<https://packages.debian.org/trixie/sane-airscan> 与
<https://packages.debian.org/trixie/skanpage>；KDE 能力边界见
<https://apps.kde.org/skanpage/>。

地区设置也已脱离 root 默认值。镜像显式交付完整 console keymap/IANA 时区数据
和首发 10 组 UTF-8 locale；启动前恢复服务将 locale、console/X11 keymap 与
timezone 保存在 root-only 持久 `/var`。OEM 选择只接受 `localectl`/
`timedatectl` 安装后 catalog 中的精确值，后续管理员变更由 path unit 捕获，
更新前再强制同步。A/B harness 会在新 root 与自动回滚后都验证
`zh_CN.UTF-8`/`us`/`Asia/Shanghai` 被实际恢复且持久 JSON 不变。
systemd 的 locale/keymap 与 timezone 设置接口见：
<https://manpages.debian.org/trixie/systemd/localectl.1.en.html> 和
<https://manpages.debian.org/trixie/systemd/timedatectl.1.en.html>。这条仍需 Linux raw 证明
D-Bus 服务激活、SDDM/X11 布局及更新/回滚恢复效果。

X11 会话锁也已从前端登录遮罩层切换为 Linux 会话能力。会话强制启动
`xss-lock`，将 X11 空闲、`loginctl lock-session self` 和 logind 休眠锁事件
交给 `XSecureLock`；专用 `echo-lock` PAM 服务复用系统密码与账户策略。
默认 10 分钟锁屏、15 分钟关显示，休眠时通过 xss-lock 的延迟锁先
启动 locker 再让系统休眠。Echo 菜单的锁定/退出只能从打包的原生 Linux
会话调用固定 `loginctl` 参数，锁管理器退出时会话失败关闭。本机策略测试
和静态镜像契约已覆盖这些路径；真实密码、错误密码、空闲、手动锁定及
休眠/唤醒后输入 grab 仍等待 Linux raw/VM 和真机证据。

Echo 自定义会话不经过完整 Plasma 启动器，所以现在由 X11 与 Wayland 会话自身启动并
监管 PowerDevil，而不是假设 XDG autostart 会代劳。镜像明确包含 UPower 与 Power
Profiles daemon；会话只有在 PowerDevil 会话总线名和两个系统电源后端都就绪后才
发布电源管理标记，任一会话级 PowerDevil 退出都会关闭桌面。源码/镜像门已覆盖，
实际电池状态、合盖、电源键、空闲 DPMS、休眠前锁定、唤醒和不同硬件 power profile
仍必须由 raw 与真机证明。

通知路径也已经从视觉占位升级为会话级系统能力。Echo 自有守护进程取得标准
`org.freedesktop.Notifications` 名称，普通 Linux/KDE/Flatpak 应用和 Agent 的 Electron
通知统一进入最多 100 条的纯文本历史；renderer 只能通过当前用户私有、权限为 `0600`
的固定 socket 执行 list/close/clear，不能选择路径、读取任意图标或触发命令。X11 与
Wayland 都把 D-Bus 名称、socket 和进程存活纳入 desktop-ready 与运行期失败关闭。
本机已经覆盖纯 store、Electron 边界、UI 类型/组件与 boot blessing；Linux D-Bus
集成和安装后 raw 冷启动由 workflow 定义，真实第三方应用、睡眠/重登后的通知行为仍
需要 Linux runner 和真机证据。

多语言输入现在也进入了生产会话，而不是依赖完整 Plasma 的隐式 autostart。镜像明确
安装 Fcitx5、中文拼音、GTK3/GTK4、Qt5/Qt6 frontend、独立配置工具和 KDE System
Settings KCM；X11 与 Wayland 在 compositor 环境就绪后各自启动一个前台受监管的
Fcitx5，并固定导出 `GTK_IM_MODULE`、`QT_IM_MODULE`、`XMODIFIERS` 与
`SDL_IM_MODULE`。只有取得 `org.fcitx.Fcitx5` 名称才发布 desktop-ready，进程或
D-Bus 名称消失会关闭降级会话。Debian CI 的两种 compositor smoke 都会启动真实
Fcitx5；本机只能完成脚本和镜像契约，中文候选窗位置、Electron/GTK/Qt 实际上屏、
快捷键切换、双屏缩放与重登配置持久性仍等待 Linux runner 和真机。

系统剪贴板也已从“依赖桌面环境大概会启动”变成显式会话组件。Plasma 6 没有独立
Klipper 可执行文件，因此 Echo 用无窗口 Qt 宿主加载 Debian 的
`org.kde.plasma.private.clipboard` QML plugin 和 `libklipper6`；X11 与 Wayland 都等待并
持续监管 `org.kde.klipper`。历史最多 20 项，不收集未显式复制的 primary selection，
不跨登录保存；SQLite 路径被固定在 mode-`0700` 的 logind runtime 子目录，宿主不读取或
记录 payload。两种 compositor smoke 都使用会自动退出的真实 source owner，随后由另
一个进程粘贴同一固定 sentinel，因而能区分“D-Bus 名称存在”和“源应用退出后剪贴板
仍可用”。这些 Linux workflow 尚未在当前 macOS 工作区运行；跨 MIME、密码管理器
secret hint、重登清除、第三方应用和真机压力行为仍需实跑。

无障碍基础链路现在也进入生产会话契约。镜像显式包含 AT-SPI bus/registry、PyAT-SPI、
Orca、Speech Dispatcher 和离线 eSpeak NG；X11 与 Wayland 在应用启动前启用 Qt
accessibility，监管 `org.a11y.Bus` 并验证其地址。Electron 强制生成 accessibility tree，
桌面根与 Dock 行为具有固定 accessible name；独立探针只有在该 marker 属于当前 Echo
进程树时才允许 desktop-ready。探针有节点、深度和超时上限，且不输出其余 accessible
内容。Linux X11/Wayland workflow 都会查打包 Electron 树，Wayland 还查原生 GTK 控件，
四个安装后 cold-boot gate 也同时要求 bus 与 tree marker。SDDM 的生产 X11 greeter
进一步启用 Qt AT-SPI bridge，并用 `sddm` 用户的受限 helper grab 固定
`Super+Alt+S`；只有本地 `seat0`、`Class=greeter`、`Remote=no` 的 logind 会话可以触发
固定 Orca 参数，Debian vendor Xsetup/Xstop 仍被保留。专用 raw gate 源码会在 OEM 状态
落盘并移除测试自动登录后停在生产 greeter，经 QMP 虚拟键盘发送组合键，并要求 helper
ready 与 Orca started marker。当前工作区尚未执行 Linux runtime，所以这道 raw gate 的
跑绿结果、Orca 真正发声、键盘/焦点顺序、高对比度与放大、第三方应用语义以及残障用户
验收仍然是明确缺口。

设置页在修改需要重启的 Agent 自动化能力后，也不再调用未实现的桌面 stub。只有原生
Linux 会话能够请求重启固定的 `echo-agent.service`，systemd/Polkit 仍负责管理员授权；
renderer 不能选择 unit、程序或参数。重启完成后必须再次通过镜像内固定健康验证器，证明
运行时来源、API 合同与任务恢复队列仍和镜像 bundle 一致，才能向用户报告成功。
源码边界、失败路径和 Linux x86-64 Electron 成品包含关系已验证，真实 Polkit 提示与服务
重启仍需随 raw 桌面会话执行。

### C3 · 可交付的操作系统

- 可重复构建的整机镜像、UEFI/安全启动策略、安装器、OEM 首启。
- A/B 或等价原子更新、签名、回滚、恢复分区、出厂重置、离线修复。
- 崩溃收集、性能与功耗基线、磁盘压力处理、备份迁移。
- Intel/AMD/NVIDIA、笔记本/台式机、单屏/多屏、休眠唤醒真机矩阵。

崩溃收集与第一层磁盘压力保护已经进入镜像契约。系统使用 Debian 原生
`systemd-coredump`，将压缩 core 只保存在独立 LUKS2 `echo-var` 上；单进程处理和
单文件上限均为 512 MiB，总量上限 1 GiB，并始终保留至少 2 GiB 可用空间。
`EnterNamespace=no`，系统也不交付自动上传、遥测端点或网络发送路径。由于 core 可能
包含密码、令牌和文档片段，导出必须是 root 管理员在用户知情同意后的显式动作。
`echo-crash-health.service` 只有在有效 systemd 配置、root-only 存储、加密 `/var` 与
原生 socket 同时成立时才允许 `boot-complete.target` 完成。源码策略测试已可在本机执行；
真实进程崩溃、限额回收、磁盘逼近 KeepFree 以及 A/B/恢复场景仍需 Linux raw 和真机取证。

用户备份与迁移也有了第一个明确的安全边界。镜像使用 Debian `restic` 的加密仓库，
不自创备份密码学；输入只允许固定的 `/home/echo` 和 `/var/lib/echo-agent`，明确排除
设备 machine-id、密码哈希、NetworkManager 密钥、TPM/LUKS token 和 `/var/lib/echo-os`
设备状态。仓库只能位于 `/mnt/echo-backup` 的独立本地 POSIX 块设备文件系统，密码由
交互式无回显输入或 systemd encrypted credential 提供，再经匿名 `memfd` 传给以 UID
1000 运行的 restic 子进程，既不进入环境变量、命令行或磁盘文件，也不经过 shell。

为避免把活跃 Firefox/SQLite 数据伪装成一致快照，第一版只允许离线备份：本地、远程或
正在关闭的 `echo` 会话都会被拒绝；工具停用 SDDM 与 Agent 后再次检查会话状态，并
拒绝任何仍带 UID 1000 的遗留进程。即使 stop 只完成一半也进入恢复路径，失败和成功都
必须恢复服务并通过 Agent 健康门。完成后取 restic JSON 输出的
精确 snapshot ID，执行全仓库 `check --read-data`，并从认证索引确认该 ID。恢复同样先做
全量校验，只写进新的私有 `.echo-restore-staging`，拒绝覆盖、额外顶层目录、外部 owner、
特殊文件及绝对/逃逸 symlink，从不自动替换正在使用的数据。Recovery promotion 会把精确
staging、仓库/snapshot 和新旧 Home/Agent 摘要绑定到事务 ID，使用两个文件系统内 rename
和 root-only journal；中间状态阻止 Agent、SDDM、直接桌面和 boot blessing。完整 promotion
仅进入保留旧树的试运行，显式 rollback 保留被拒绝的试运行数据，只有显式 commit 删除旧树。
13 个备份策略单测、7 个事务故障/重入单测和镜像契约已经通过；image workflow 还定义了从
已安装且完成 OEM 的一次性 raw 副本冷启动，附加独立
ext4 virtio 盘，创建 ACL/xattr/稀疏/相对 symlink 数据，要求错误 systemd credential
失败，在仓库只余 2 MiB 时要求新数据备份失败且仓库仍一致，再翻转真实 pack 字节并要求
全量检查拒绝，最后修复 pack、staged restore 并逐项比较。随后同一 staging 分成独立 NBD
rollback/commit raw：前者 promote 后回滚，后者 promote 后先从生产 SDDM 冷启动，再提交；
错误 transaction token 必须被拒绝。它尚未在 Linux runner 跑绿，真实外置盘断连、遗失
口令处置与物理设备迁移仍缺，因此还不能称为已交付备份。

整机 workflow 的最后一步现在还定义了统一证据绑定器：它要求安装、Recovery、换 TPM、
factory reset、OEM/SDDM、X11/Wayland、独立备份盘、promoted restore 生产 SDDM 试运行与
Agent 中断恢复连同 dedicated-runner preflight 共 15 组日志的
完成标记各出现且只出现一次，再把镜像版本、干净 Agent commit、GPG 签名安装
manifest/signature SHA-256、构建前干净 OS commit/tree/origin/source-manifest、
安装公钥环/Secure Boot 证书/signed-PCR11 公钥摘要、
manifest 内源 raw 身份、最终安装后整盘 SHA-256/大小，以及
每份完整日志的 SHA-256 汇总成不含正文的 `echo-os-image-evidence.json`。安装 plan/install
日志必须携带同一 manifest 和源 raw SHA-256。构建器还会在主 root 与 Recovery UKI
组装前后重新核对 checkout 未偏离捕获身份，把同一只读 identity/verifier 封入两者；
桌面、生产登录与 Recovery 的启动完成标记必须返回该精确 OS commit，否则统一证据拒绝。
输入有单文件、总量与整盘上限，
符号链接、越界路径、脏 Agent 或任何缺失/重复标记都会失败关闭；CI 仍单独保留原始日志
供人工审阅。schema-3 安装 manifest 已由发布签名直接绑定同一 OS source identity；随后
统一 evidence 再用安装发布身份产生 detached GPG 签名，立即由 public-only 安装公钥环
对原 JSON 反验，并将签名与验证日志一同保留；发布私钥不进入产物。下载者可用
`verify-os-image-evidence-release.sh` 和这三份公开文件重复同一验证。这把一次运行的证据
收束成可认证、可核对的集合，但尚未运行的 binder/signing gate 不能替代 Linux raw 结果。

当前 C3 bring-up 已在 `packaging/image/` 定义固定 Debian snapshot、GPT 分区、
systemd-boot/UKI、产品身份、声明式用户/服务、镜像校验和及 QEMU 冷启动契约。
更新基线进一步预留两套 root/hash/signature dm-verity 三联槽并生成带 UUID 的三份
分区载荷和 UKI；生产构建必须选择外部
更新公钥环，通过目录 extra-tree 嵌入 root、postinst 公钥 packet 审计和成品 raw
逐字节读回。设备端只有在有界 preflight、GPG manifest 验签、精确目录/载荷哈希、
三条 zstd 测试、PKCS#7/UUID/UKI roothash 核对完成后才能调用 systemd-sysupdate；
同一签名哈希集合还必须包含严格的 `OS-SOURCE-IDENTITY.json`，preflight 与完整验签后
必须得到同一 commit/tree/manifest 摘要；root/hash/signature 先写，UKI 最后发布。
A/B 破坏性 raw 门现在也不再绕过产品入口直接调用 sysupdate：它使用显式、仅源码测试
可启用的离线镜像参数调用同一 `echo-os-update apply`，要求 root-only 独占锁、完整认证、
verity/UKI 关系与带 OS 来源的 `ECHO_UPDATE_APPLIED` 全部成功后才冷启动新版；并发更新
在任何写入前失败关闭。由于 sysupdate 对“版本已经安装”也可能成功退出，入口还必须先用
`check-new` 得到唯一候选、要求它与已认证 bundle 版本精确相等，再显式执行
`update VERSION`；同版本/旧版本重放和候选错配都不能进入写入或伪造 applied marker。
生产设备仍只使用固定系统路径，并在 apply 前捕获实时账户和地区状态。
设备端已补上 HTTPS-only 签名通道：默认 timer 只在 AC 电源上有抖动地执行 fetch，
先认证有界 manifest/signature 再下载精确 payload，以 root-only staging + fsync + rename
发布，拒绝 redirect、内容变换、同版本替换和缓存越界。缓存保留当前认证候选及一个历史
版本，显式 apply 持有通道锁并继续进入同一 `check-new`/生产 updater；定时器不会自动
写槽或重启。仓库中的生产 URL 仍只是配置，尚无线上发布可用性证据。
更新信任也有单调持久代际：release root 绑定 keyring digest、trusted/retired full
fingerprints；只有 restore/crash/Agent 和适用的桌面/登录健康门通过后才在 blessing 前
用 pending→active 事务晋级到加密 `/var`。updater/channel 优先选该托管 keyring，因此
旧 root 回滚不能恢复 retired key。轮换强制 old+new bridge 与下一代 new-only+retire-old
两步，拒绝跳代、同代冲突、静默删钥和取消退休。portable 测试已覆盖事务中断和旧 root
选择，但真实双签名 A/B 轮换尚未执行。
发布侧也不再要求运维直接覆盖 stable 目录：public-key-only repository publisher 会把
签名 bundle 复制到同一 web root 文件系统的私有 staging，重新验签、逐项哈希、fsync 并
rename 为不可变 sequence/version 目录，最后才用相对 symlink 原子切换
`stable/x86-64`。序列只允许从 1 开始逐次加 1，同序列替换、同版本重发、倒退、跳号、
并发发布和 symlink 逃逸都会失败；release rename 后、stable 切换前的中断可重试恢复。
9 个 portable 故障测试已覆盖该源码边界。它仍不等于 `updates.echo-age.com` 已部署；
TLS、web server、缓存头、外部监控、生产签名 bundle 和真实设备 fetch 仍是线上验收门。
桌面端现已补上用户可见的更新边界：root 协调器只把状态、阶段、认证版本、manifest 摘要、
时间和数字错误码原子发布到 4 KiB 上限的 root-owned 公共状态文件，不暴露私有缓存路径、
通道 URL 或 stderr；Electron 只读该固定文件。安装按钮只能通过 KDE PolicyKit agent
授权固定、无参数的 `echo-os-update-apply`，再进入同一签名验证和 inactive-slot updater，
renderer 不能选择命令、bundle 路径、版本或 argv。界面明确区分检查、已认证、备用槽写入、
等待重启和失败；不自动安装、不自动重启。portable 状态/协调器/Electron/UI 测试已覆盖
这条源码链，但尚未在 Linux raw 中实际点击 PolicyKit 对话框并完成一次图形化 A/B 更新。
两个 Linux workflow 另有临时真实 GPG 双版本发布、served manifest 反验与倒退拒绝门；
当前 macOS 工作机没有 GPG，这一项仍需 runner 产生运行证据。
A/B 门还在同一 OEM 后 raw 上先做真实中断：PATH shim 只负责启动真正的
`systemd-sysupdate` 独立进程组，并监视 inactive root 精确 GPT 起点的有界字节；一观察到
真实写入就对整个进程组发 SIGKILL。随后必须同时证明 root 样本已经变化、三联标签尚未
发布、新 UKI 不存在、没有虚假 applied marker、旧 UKI/root 仍能冷启动，并让普通生产
入口继续处理这块同一半写盘。成功重试前还要用经过认证的新 UKI 等大文件填满该盘 ESP，
要求生产 updater 明确因 ENOSPC/磁盘满失败、三联标签和新 UKI 仍未发布、旧入口再次
冷启动；只删除有界测试 filler 后，同盘才允许正常完成更新。这对应 systemd v257 明确记录的
“下次调用识别并清除未完成下载”以及 transfer 全部写完后才顺序发布、入口资源最后发布
的语义；workflow 未实际跑绿前仍只是可执行契约。
A/B 完成后还必须把中断 apply/旧版启动、ESP 满 apply/旧版启动、成功生产 apply、
dm-verity 拒绝、新版启动/登录、三次未健康失败、旧版回滚启动/登录及最终状态共 14 个
日志角色，与
统一 Echo 源码、更新 manifest/signature、
公开更新 keyring 和 OEM 后基础整盘哈希绑定为不含日志正文的 evidence JSON；同一更新签名
身份对 JSON 做 detached signature 并立即用 public-only keyring 反验后才上传。
A/B workflow 现在从生产安装器写盘和
真实 OEM 首启后的同一设备 raw 开始，不再在更新后注入另一套账户状态。新 UKI 采用
三次 boot counting，且 Echo Desktop 健康服务成功后才允许 systemd-bless-boot
标记启动。
`packaging/recovery/` 另行构建不依赖 root A/B 的 Recovery UKI，提供默认只读诊断、
完整 dm-verity root 检查、禁止破坏签名的原地修复，以及只重建
`/var`/swap/`/home` 的显式 factory reset。外部 X.509 私钥/证书
可以让 mkosi 签名 systemd-boot、桌面 UKI、Recovery UKI，并让 VM 使用 enforcing
OVMF；仓库不含生产私钥。当前阶段仍只证明这些源码和 CI 契约，真实 Linux 构建、
双版本破坏性回退、恢复冷启动、生产 Secure Boot 密钥生命周期与真机必须分别取证。

整盘安装也不再只是一项缺失能力。release 工具会在签名前核对成品 raw 的 GPT 类型、
标签、顺序和范围，直接对 raw 内 root/hash/signature 与 ESP 主 UKI 做完整验证，并核对
systemd-boot、桌面/Recovery UKI 的 Secure Boot signer 和 signed-PCR11。随后才把
压缩/解压哈希、字节数和版本写入严格 manifest，再由外部 GPG
发布身份生成 detached signature。Recovery 只信任构建时嵌入的公钥环；安装器拒绝
当前 Recovery 盘、安装介质盘、挂载/活动 swap/只读/过小目标，并用磁盘型号、序列号、
容量、版本和镜像摘要生成逐盘确认 token。解压流必须与签名字节数完全一致，写入后
还会从目标重新哈希全部原始镜像字节；随后移动备份 GPT，再只扩展尾部 `echo-home`
分区和 ext4。Linux image workflow 现在定义了签名 bundle 加临时 NBD 的完整写盘门：
先比较 `plan` 前后目标状态，再把逐盘 token 交给生产 `install`，校验完成标记、GPT 顺序
及 home 对新增容量的占用，最后让 Recovery、生产 X11 登录、Wayland 候选和直接桌面
都从同一个安装后 raw Secure-Boot 冷启动。新增 OEM 门通过私有、随机密码的 systemd
credential 执行生产账户/地区持久化代码，只在临时副本加入 SDDM autologin，不再直接
伪造完成标记和密码哈希。该未提交 workflow 尚未在 Linux runner 运行，因此目前增强
的是可执行验收边界，不能表述成已经成功安装、完成首启或启动。

整机任务已经从 PR 的 portable source contract 中分离：PR 在 Debian 容器内运行 runner
policy、统一 evidence binder 单测和静态合同，不接触 KVM/NBD/loop 或发布密钥；可信
push/workflow dispatch 才能进入最长 6 小时的完整 Linux x86-64 任务。两个重型 job 都固定
要求 `self-hosted`、`linux`、`x64`、`echo-os-image` 四个 runner 标签，不再回退到必然缺少
容量/KVM/块设备的 GitHub 托管 runner。构建前 fail-closed preflight 要求至少 4 个
effective CPU、16 GiB effective memory、48 GiB workspace、160 GiB scratch、4 个空闲 loop、
2 个空闲 NBD、真实 KVM 与 x86-64 Secure-Boot firmware；workspace/scratch 同盘时至少需要
208 GiB 空闲。默认临时目录归入被测 scratch；换 TPM、factory reset 和 provisioned 分支
完成取证后立即释放，避免已经通过的约 21 GiB raw 一直占用后续阶段。preflight 私有 JSON、
完整日志和唯一 readiness marker 会被保留并绑定进最终 15 组 evidence。该门目前已有 9 项
portable 单测，但没有 Linux runner 成功记录。

runner 注册前也不再只靠人工清单。无密钥 host configurator 仅在 Debian/Ubuntu x86-64 上
安装 Docker/KVM 用户态依赖，把专用非 root 服务账号加入 `docker`/`kvm`，以 root-owned
module-load/modprobe 配置固定 64 个 loop、16 个 NBD 与每个 NBD 16 个分区，并创建服务账号
独占 mode-`0700` 工作根。随后 host verifier 以该账号实际连接 Docker daemon、读写打开
`/dev/kvm`、核对运行内核 `/lib/modules`、effective CPU/内存及同盘 208 GiB 可用空间，生成
mode-`0600`、不可覆盖的 host evidence 和唯一 `ECHO_IMAGE_RUNNER_HOST_READY` marker。8 项
portable 单测固定 fail-closed 边界。配置脚本不创建账号、不下载或注册 Actions Runner、
不接收 GitHub URL/Token；官方一次性注册仍由操作者在专用单租户机器上完成，并且必须选择
同一工作根和自定义 `echo-os-image` 标签。Docker 组具有宿主 root 等价能力，因此该机器不得
承载不受信任仓库或 pull request。两个重型 job 还在 artifact 上传后的 `always()` 最终步骤调用
同一个有界 cleanup，清除临时签名/恢复密钥、虚拟 TPM、整盘副本、生成的 Agent bundle 和私有
Agent checkout，同时拒绝链接和非 GitHub Actions 调用；当前没有真实 host marker。
官方 runner 注册完成后还必须运行 `configure-linux-image-runner-hooks.sh`，将应用目录外的 root-owned
hook 同时写入 `.env` 的 `ACTIONS_RUNNER_HOOK_JOB_STARTED` 和
`ACTIONS_RUNNER_HOOK_JOB_COMPLETED`。两次同步 hook 各有 300 秒上限：completed 覆盖取消前仍可进入
收尾的任务，started 则在上次 runner/主机异常来不及收尾时阻止残留进入下一任务。修改后须重启
runner 服务并在首次 run 的 setup/complete 日志确认两个 cleanup marker；当前也没有这份日志。

整机、A/B、appliance release 和通用 CI 现在都从当前 Echo checkout 构建内建 Agent，
不再拉取第二个私有仓库，也不需要额外源码凭据。候选汇总门以 job token 验证统一 Echo 来源；
整机、A/B 和通用 CI 的 PR job 不接收发布 secret，
A/B 特权整机任务也已像主镜像任务一样排除 pull request、依赖 portable source contract、固定
选择带 `echo-os-image` 标签的 self-hosted Linux x86 runner，并执行同一资源/KVM preflight。两个
特权 job 及真实 OMV 容器门还必须运行在 `os-main/main` 的非 PR ref；PR/source job 只保留
`contents:read`，OIDC/attestation 写权限下沉到发布 job。手工候选汇总只允许 `os-main`，任意分支
dispatch 不能触碰自托管构建机或拿到有效候选。
checkout 与 bundle build 被拆成两个 step，后者不继承凭据。通用 PR 保留 Python 3.11/3.12
Ruff、OS 安全扫描和 513 项
release/delivery 测试；39 个完全公开执行文件和 16 个 Agent-bound 文件必须精确覆盖当前
全部 55 个 appliance 测试文件，新增或删除文件而未显式分类会直接失败。分类项还必须是
真实非 symlink 文件；测试启动前若已有插件预载 `runtime` 也会失败。导入器继续拒绝后续私有
`runtime` 导入，父级 Agent fixture 不会被加载；安装私有 Agent 的可信 job 继续执行完整
appliance 套件，本机当前为 708 passed、1 skipped。源码交付前检固定发布 13 个精确
check code，发布证据索引测试要求两端集合完全一致，并覆盖真实的 `git_repository` 仓库身份项，
避免各自的合成测试通过但真实绿色前检仍被索引拒绝。
候选协调为 raw/A-B 使用根路径稳定的三件式证据 artifact，允许仓库固定的专用 self-hosted
runner，并让签名 manifest 绑定 runner preflight；OMV/appliance 则继续拒绝 self-hosted OIDC。
最终候选包携带十份真实索引输入、public-only keyring 检查器和确定性离线回放入口，上传前必须
在脱离仓库路径的打包目录中重验 GPG 并逐字节重建统一索引。
workflow-policy portable 测试固定这些单仓边界；当前工作树仍需审查、提交并推送后才能晋级。

本地设备身份也与 Echo/Agent 云身份分离：生产首次启动由 tty1 上的一次性 OEM
程序为固定 UID 1000 账号设置本地显示名、主机名和管理员密码，成功后才允许 SDDM
通过 PAM 显示图形登录并进入 Echo X session。交付配置没有自动登录；VM 使用不写入
镜像的一次性 systemd credential 激活独立的自动桌面健康路径。该策略和输入/原子
标记测试已经通过。另一个成品副本 harness 会关闭 credential、只向临时 raw 注入
测试 OEM 标记、随机临时密码哈希和 SDDM 一次性登录，先把持久 `/var` 中的账号状态
恢复到锁定的新 root，再从真实 SDDM session 验证 KWin/renderer；交互式首启、错误
密码和手动成功登录仍等待真实 raw 镜像取证。

## 当前证据账本

| 能力               | 已有证据                                                                                                                                                                                                                         | 尚缺验收                                                                                            |
| ------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| 桌面与普通应用窗口 | KWin/X11 会话、EWMH 桥、KWin UUID/desktopFileName 脚本桥、私有带回执动作通道、compositor 输出/缩放协议、KDE wrapper/DRM/XWayland 生产候选、compositor 下层规则；双屏 1.25× virtual 与一次性 SDDM raw gate 都要求打包 Echo/Ozone Wayland、preload IPC→KCalc、唯一 UUID、精确 close、Echo/GTK AT-SPI 与 KScreenLocker；raw 请求不绕过生产登录且不进入成品镜像 | 执行并固定 Wayland CI/raw 结果、候选切默认、独立 Shell surfaces、物理多屏/HiDPI/输入法/休眠矩阵     |
| 本地用户与登录     | 一次性 OEM 设置、本地密码、`sudo` 管理员、SDDM/PAM、无生产自动登录；随机私有 systemd credential 执行真实 OEM 代码后进入临时 SDDM session 的安装后 raw gate 源码                                                                  | Linux runner 跑绿；真人交互首启、错误/正确密码、登出/重登和多用户实跑                               |
| 会话锁与休眠前锁定 | X11 的 xss-lock/XSecureLock/PAM，以及 Wayland 候选的 KScreenLocker/PAM/LockOnResume/零 grace 配置；手动/空闲/休眠锁、锁服务失效关会话、无 shell 锁定/登出动作                                                                    | raw 中两种会话的正确/错误密码、手动/空闲/休眠唤醒锁定、显示关闭、实际图形输入 grab 与真机攻击面审计 |
| 电源与电池管理     | 两种生产会话直接监管 PowerDevil，显式安装并探测 UPower 与 Power Profiles D-Bus 后端，冷启动完成门要求三者同时就绪，PowerDevil 丢失即关闭会话                                                                                   | raw/真机验证电池与交流电状态、合盖/电源键、亮度与 DPMS、不同平台 profile、休眠和唤醒                 |
| 系统通知           | 自有 `org.freedesktop.Notifications` daemon、有界纯文本历史、固定 0600 会话 socket、Electron IPC/UI、X11/Wayland 监管与四个安装后冷启动门源码                                                                                   | Linux workflow 跑绿；真实 KDE/Flatpak/Electron 通知、重登、休眠唤醒和压力行为实跑                    |
| 多语言输入         | Fcitx5、中文拼音、GTK3/4 与 Qt5/6 frontend、KDE KCM、X11/Wayland 会话监管、D-Bus 就绪和四个安装后冷启动门源码                                                                                                                  | Linux CI/raw 实际上屏；Electron/GTK/Qt、候选窗、多屏缩放、快捷键、重登持久与真机输入法实跑          |
| 系统剪贴板         | Debian Plasma 6 Klipper QML/libklipper6、无窗口 Qt 宿主、20 项易失历史、固定 0600 runtime 数据库、X11/Wayland 监管、源 owner 退出后跨进程粘贴 gate 与四个安装后冷启动门源码                                                   | Linux workflow/raw 跑绿；文本/HTML/图片/文件 MIME、密码管理器 hint、重登清除、第三方应用与真机压力实跑 |
| 无障碍与读屏       | AT-SPI bus/registry、Qt/Chromium accessibility、固定桌面/Dock accessible name、PID 绑定且无内容日志的独立树探针、Orca + Speech Dispatcher + eSpeak NG、X11/Wayland 与四个安装后门源码；SDDM greeter Qt bridge、受限 `sddm` helper、固定 `Super+Alt+S` 及无自动登录 QMP raw gate 源码 | Linux workflow/raw 跑绿；登录前快捷键实际结果与发声、键盘/焦点、放大/高对比度、第三方应用及残障用户真机验收 |
| 崩溃诊断与磁盘保护 | Debian `systemd-coredump`、加密 `/var`、512 MiB 单 core、1 GiB 总量、2 GiB `KeepFree`、无自动上传、有效配置与 socket 的 boot blessing gate 及四个安装后冷启动门源码                                                        | Linux raw 触发真实 core 并验证回收/磁盘压力/A-B；真机隐私审计、用户同意式导出和性能功耗基线          |
| 用户备份与迁移     | Debian restic 加密仓库、固定用户/Agent allowlist、匿名 memfd 密码、无 shell/无秘密环境、全会话及 UID 1000 遗留进程离线门、精确 snapshot/全仓库 read-data、staged restore；root-only 跨 Home/var 事务、启动 gate、显式 promote/rollback/commit，13 个备份测试与 7 个中断恢复测试；独立 virtio 仓库盘和双 NBD 分支上错误口令/写满/pack 损坏、ACL/xattr/稀疏、回滚、生产 SDDM 试运行和提交的 raw gate 源码 | 让 Linux raw gate 跑绿并审阅统一事务日志；真机外置盘断连、遗失口令处置与物理设备迁移 |
| 应用安装与沙箱     | Flatpak + Discover Flatpak-only 商店、固定 Flathub 公钥定义、持久 `/var`/`home`、KDE portal、无 shell desktop 启动、A/B 状态 sentinel                                                                                            | raw 镜像联网刷新目录、真实安装/授权/启动/卸载第三方应用，并跨更新/回退实跑                          |
| 核心应用与默认关联 | Dolphin/Konsole/Firefox ESR/Kate/Okular/Gwenview/Ark/Haruna/Spectacle/KCalc、严格系统 `mimeapps.list`、root-owned health、19 项 portable 测试；真实 KWin X11/Wayland 及 direct installed raw 的目录/loopback HTTP/五文件 `xdg-open`、Konsole/KCalc `gio launch`、九个原生窗口与精确关闭 gate；X11/Wayland workflow、direct raw 与 disposable SDDM Wayland raw 都从打包 Echo preload `apps.list()`/`apps.launch()` 穿过生产 IPC/GIO，Wayland 绑定唯一 compositor UUID/close 回执和 Echo AT-SPI，raw 结果绑定发布 evidence；Dock IPC 失败显式报错；10 项 Node、8 项 Wayland parser 与 14 项 evidence-binder 测试通过 | 让 Linux workflow/raw gate 实际跑绿；再实跑 Dolphin UI 双击、远程 HTTP/HTTPS、Spectacle 授权/截图、损坏文件与广泛格式/codec、用户覆盖、辅助技术及跨槽回退 |
| 设备身份           | 克隆镜像空 machine-id、systemd initrd 持久 `/var` bind 与加密 `/etc` overlay、启动前健康门、不可逆日志派生值和 A/B 对比 harness                                                                                                  | raw 镜像首次生成、双槽更新/回滚、factory reset 换 ID 与损坏状态进 Recovery 实跑                     |
| 网络连接持久       | NetworkManager 官方 keyfile path、root-only `/var` profile、安全一次性迁移、会话前健康门、A/B 更新/回滚对比 harness                                                                                                              | raw 镜像真实 Wi-Fi/802.1X/VPN 建立、重启、更新、回滚和损坏权限故障实跑                              |
| 主机防火墙         | Debian firewalld 2 + nftables、KDE firewall KCM、自定义 echo-public 默认拒绝 zone、StrictForwardPorts、root-only baseline verifier、NetworkManager/登录/桌面/blessing 前健康门与 12 个故障测试；五类 raw 冷启动要求精确 readiness | Linux raw 确认 nft table/D-Bus/PolicyKit；外部 IPv4/IPv6 扫描、显式共享、VPN/多网卡、容器端口、休眠及更新回滚实跑 |
| 可移动存储         | UDisks2 system D-Bus/PolicyKit、Dolphin/KIO MTP、FAT/exFAT/NTFS/ext4/Btrfs/XFS 工具、无 renderer 块设备 IPC、只读启动健康门、6 个 portable 故障测试及五类 raw readiness gate | Linux raw/真机实跑 USB/SD/光驱/MTP，验证热拔插、只读/损坏/加密介质、休眠唤醒和格式化授权边界         |
| 本地与 USB 打印    | local-only CUPS、PDF/raster filters、KDE Print Manager、cups-pk-helper PolicyKit、loopback ipp-usb、加密 spool、无作业文件/历史保留、16 个 portable 故障测试及五类 raw readiness gate | Linux raw/真机实跑 Qt/GTK/Electron、授权允许/拒绝、USB 与手动 IPP/IPPS、错误恢复、休眠和作业清除；网络自动发现/共享仍关闭 |
| 文档与图片扫描     | SANE/udev USB、KDE Skanpage、loopback ipp-usb eSCL、按需 AirScan eSCL/WSD、默认关闭 saned sharing、无 trace/hexdump/全局 spool、13 个 portable 故障测试及五类 raw readiness gate | Linux raw/真机实跑平板/ADF、USB/driverless/LAN、PDF/图片、拔线取消、权限拒绝、休眠和 payload 日志审计 |
| 地区与键盘         | 编译 locale、console keymap/tzdata、OEM 精确选择、持久 `/var` 恢复/变更捕获、更新前同步和非默认 A/B harness                                                                                                                      | raw 中 localed/timedated、console/SDDM/X11 键盘、时区/DST 及跨槽恢复实跑                            |
| 整机镜像           | 固定 Debian snapshot、GPT/UEFI/UKI、分区与构建解析门；构建前记录统一 Echo commit/tree/origin，schema-3 安装签名直接绑定其 manifest；整机、A/B 与 appliance CI 从同一个干净 revision 构建，并由专用 runner 的 policy 单测和 fail-closed preflight 约束 CPU/内存/存储/KVM/loop/NBD/Secure-Boot firmware；安装、生命周期和冷启动日志必须绑定同一镜像版本、单一源码身份、公开发布信任材料、GPG manifest/signature、源 raw 与安装后整盘 SHA-256；最终 JSON 由同一安装发布身份 detached-sign 并用 public-only keyring 立即反验 | 审查、提交并推送统一 Echo revision；配置满足资源门的 Linux x86 runner，生成 raw、实际 QEMU 冷启动、签名 evidence 及人工审阅 |
| 整盘安装           | 严格签名 bundle、Recovery 公钥信任根、整盘/源介质/挂载/swap/活动 holder 安全门、锁后磁盘身份复核、刷盘后 direct 全量哈希、GPT 修复和尾部 home 扩展；临时 NBD 真写盘并把安装后 raw 交给后续冷启动的 workflow 源码                 | 让未提交 workflow 在 Linux runner 跑绿，再做真实 Recovery 介质、真人 OEM、更新回退及硬件安装        |
| 更新回退           | public-only 更新信任根、HTTPS-only 有界签名通道、fetch-only timer、双版本私有原子缓存、显式 apply/check-new、防重放；健康门后单调 trust generation、old+new bridge、new-only retire-old、断电可恢复持久晋级和旧 root 不复活旧钥的 portable gate；发布侧 public-key-only 验签、连续 sequence、不可变 release rename 与 stable symlink 原子切换/中断重试；四载荷精确 GPG bundle、A/B dm-verity 三联、UKI roothash、三次 boot count、真实 root 首写 SIGKILL、ESP 真空间耗尽、两次旧 UKI 冷启动、同盘清理重试、显式篡改拒绝与桌面健康门；安装 → OEM → 中断 → 空间耗尽 → 新版 SDDM → 三次故障 → 旧版 SDDM 并保持设备状态的 workflow 源码 | 部署并验证真实签名发布端；让未提交 Linux workflow 跑绿；实跑两代更新签名轮换/退休；再做 CDN 缓存、通道不可达、TLS/代理、其他写入阶段断电和真机更新 |
| 离线恢复           | 独立 Recovery UKI、签名 root 只读验证、禁止原地修复、三卷重置与密钥生命周期 NBD/VM harness；双文件系统用户恢复的计划/试运行/回滚/提交事务和 normal-boot fail-closed gate                                                              | 从成品 ESP 实际冷启动并执行故障盘、断电恢复与用户恢复事务演练                                      |
| 可信启动           | 外部密钥签名、systemd-boot/桌面/Recovery PE 验签、UKI roothash、signed-PCR11 与 enforcing OVMF 配置                                                                                                                              | 生产密钥托管、轮换/吊销、OEM 灌装和真机 Secure Boot                                                 |
| 数据保护           | root 登录锁定、Chromium sandbox、dm-verity A/B root；`/var`/swap/`home` 独立 LUKS2、TPM2 signed-PCR11、恢复密钥轮换、换 TPM 重绑和 factory reset 源码 gate                                                                         | Linux QEMU 跑绿；恢复密钥人工保管/轮换演练、睡眠态攻击面、生产 TPM 和真机安全审计                    |

## 完成定义

目标 C 只有同时满足以下验收门才算“真的系统”：

1. 冷启动进入 Echo 登录/桌面，不依赖开发服务器或人工命令。
2. 至少文件管理器、终端、浏览器和第三方 GUI 应用能安装、启动、浮动、缩放、
   聚焦、最小化、恢复、关闭，并在重启后恢复合理状态。
3. 双屏、HiDPI、音频、网络、蓝牙、输入法、剪贴板、通知、无障碍读屏和休眠唤醒在真机通过。
4. 权限边界经过威胁建模；网页/renderer 不能把 IPC 变成任意 shell。
5. 签名更新可升级、失败可回滚、无网络可进入恢复环境。
6. 有可复现镜像和至少一套持续运行的 VM + 真机回归矩阵。

在这些门全部通过之前，状态应报告为“目标 C 推进中”，不能把网页视觉完成度当成
OS 完成度。
