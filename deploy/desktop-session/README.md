# Echo OS 通用桌面会话（目标 C bring-up）

这套部署用于替代 Cage 单应用会话。当前默认生产 bring-up 仍是
`Xorg → KWin → Echo Shell`；镜像同时交付可在 SDDM 手动选择的
`KWin/DRM Wayland → Echo Shell` 候选会话。KWin 管理真实 Linux GUI 窗口，
Echo Shell 仍提供桌面、菜单栏、Dock、启动器和 Agent 工作台。候选会话在 Linux
raw/真机门禁变绿前不会替换默认 X11 会话。

## 安装

在 Debian stable VM 或测试机的本仓库中运行：

```bash
sudo ./deploy/desktop-session/setup-desktop-session.sh
sudo systemctl start echo-desktop
```

安装脚本会构建 Vite 前端和 electron-builder Linux 目录包、安装
`echo-desktop.service`，并禁用会抢占同一 seat 的旧 `echo-shell.service`。
开机会话优先运行打包后的 `echo-os-desktop`，不会把 `npx electron` 当成交付形态。
正式镜像阶段会把这些步骤移入可复现镜像构建；当前脚本只负责 C0 bring-up。

## 验证

```bash
journalctl -u echo-desktop -b
wmctrl -m
wmctrl -lx -p
./deploy/desktop-session/verify-desktop-session.sh --runtime
```

源码/打包阶段可先运行 `verify-desktop-session.sh --static`；live contract 必须在
已经启动 KWin + Echo Shell 的 Linux 图形会话中运行。

也可以在没有现成桌面环境的 Linux 主机或 CI 容器里运行完整的隔离验收：

```bash
cd frontend
pnpm build
pnpm exec electron-builder --linux dir --x64
cd ..
./deploy/desktop-session/smoke-desktop-session.sh
```

该脚本自动建立 `Xvfb → D-Bus → KWin` 会话，启动打包后的 Echo Desktop，随后
创建一个真实 X11 应用窗口，并通过生产桥验证枚举、聚焦、最小化、恢复和关闭。
仓库的 `desktop-session-smoke.yml` 会在 Debian stable 容器中执行同一验收；失败
时上传 KWin、Electron 和 Xvfb 日志。

同一 workflow 还定义了独立的 Wayland gate：

```bash
./deploy/desktop-session/smoke-wayland-session.sh
```

它直接启动 KWin 6 `--virtual` backend，创建两个 1.25× 输出，通过
`wayland-info` 和 compositor bridge 双重核对输出，再以 `GDK_BACKEND=wayland`
强制启动无 X11 回退的 GTK 顶层窗口，并通过生产 UUID provider 完成聚焦、最小化、
恢复和关闭，最后通过 `org.freedesktop.ScreenSaver` 要求 KScreenLocker 真正进入
locked 状态。该 gate 必须在安装了 `kwin-wayland`、`wayland-utils`、
`libkscreenlocker6`、Python GI 和 GTK 的 Linux 环境运行；当前 macOS 工作机只能
验证其源码契约，不能把它记作已执行证据。

`echo-wayland-session.sh` 是独立生产候选启动链，不是 virtual smoke 的别名。它由
SDDM 的 `Echo OS (Wayland Candidate)` 条目启动 KDE 官方
`kwin_wayland_wrapper --drm --xwayland`；wrapper 预分配 socket 并把
`WAYLAND_DISPLAY`、`DISPLAY`、`XAUTHORITY` 同步进 D-Bus/systemd user activation
环境。子会话逐项比对这些值、等待原生 Wayland socket、XWayland、KWin UUID bridge、
KScreenLocker greeter/PAM 与 renderer 的 mode-`0600` 原子就绪文件，任一关键服务
退出就关闭会话。`/etc/xdg/kscreenlockerrc` 默认 10 分钟锁定、无 grace period、
恢复后锁定且必须用系统密码解锁。

整机 workflow 还会对已构建 raw 做一次 sparse/reflink 副本，只在副本的加密 `/etc`
overlay 中加入 SDDM autologin 和 root-owned `0444` 固定 KCalc IPC 请求，再自动选择
`echo-wayland.desktop`。生产 SDDM/PAM 路径不会收到 direct-desktop CI credential，桌面也
不会启用 standalone auto-exit smoke。会话与 Electron 独立校验固定请求后，从
Secure-Boot UEFI 冷启动观察 DRM KWin、XWayland、KScreenLocker、UUID bridge、打包
renderer/preload IPC、唯一新增 KCalc UUID、精确关闭和持久设备状态。原始发布镜像没有
该请求文件；该远端门未运行或未变绿时，候选仍不能算真实整机会话证据。

桌面内验证：

1. 从 Dock 启动文件管理器、终端或浏览器。
2. 普通应用窗口应浮在 Echo 桌面上，可由 KWin 移动和缩放。
3. Dock 出现运行状态点；单击图标聚焦已有窗口。
4. 右键运行中的本地应用图标，验证“显示 / 最小化 / 退出”。
5. 打开两个不同原生应用，确认相互聚焦和关闭不会退出桌面会话。

## 真实会话锁

生产 X11 会话使用 `xss-lock` 把 X11 空闲、`loginctl lock-session`
和 logind 休眠前锁定统一交给 `XSecureLock`。默认 10 分钟空闲锁屏，
15 分钟关闭显示输出；休眠路径用 `--transfer-sleep-lock` 持有 logind
延迟锁，启动锁屏进程后再释放。解锁走独立 `/etc/pam.d/echo-lock`，
复用 Debian `common-auth`/`common-account`，不再用 React 登录遮罩层伪装
系统锁屏。

Echo 菜单的“锁定屏幕”和快捷键只会调用白名单的
`loginctl lock-session self`；“退出登录”调用
`loginctl terminate-session self`。网页预览、普通 Electron 开发窗口和锁服务
未就绪的会话都不会暴露锁屏能力；`xss-lock` 意外退出时桌面
会话也会失败关闭。可移植策略测试：

```bash
./deploy/desktop-session/test-echo-session-lock.sh
```

该测试能证明固定命令参数、超时策略、PAM 选择和休眠适配器启动；
它不能代替 raw 镜像中真实密码解锁、错误密码拒绝、强制休眠/唤醒和
图形输入 grab 的实跑。

## KWin compositor 窗口桥

`kwin-window-bridge/` 是随签名 root 交付的 KWin 6 JavaScript 包。它从
`Workspace.stackingOrder` 读取 compositor 管理的窗口，使用
`Window.internalId` UUID 作为身份，并输出 `desktopFileName`、标题、激活和最小化
状态；同一快照还包含 `Workspace.screens` 的输出名称、逻辑几何和
`Output.devicePixelRatio`。聚焦、最小化和关闭由 KWin 脚本直接操作
Window 对象，不需要 `wmctrl`、X11 window id 或渲染端命令文本。

`echo-kwin-window-bridge` 核对 `org.kde.KWin` 的 D-Bus unique owner，只接受 KWin
发布的快照和固定动作回执；
Electron 通过 `$XDG_RUNTIME_DIR/echo-os/kwin-window-bridge.sock` 的 `0600`
用户私有 socket 访问。双方都会再校验 UUID、字段长度、窗口数和动作白名单；
动作只在 KWin 回执后才向 Electron 报告成功。

当 `XDG_SESSION_TYPE=wayland` 且该 socket 存在时，`native-windows.cjs`
选择 `kwin-wayland` provider；X11 bring-up 仍使用现有 EWMH provider。
隔离 KWin/Xvfb smoke 会同时启动这个脚本桥，等待真实 UUID 快照，并让它关闭一个
真实 X11 测试窗口；独立 KWin Wayland smoke 则要求两个 1.25× compositor 输出和
原生 Wayland 窗口完成相同 UUID 生命周期，防止 provider 在切换前成为死代码。Wayland
smoke 还会真正启动 electron-builder 产出的 Echo Desktop，让 preload
`apps.list()`/`apps.launch()` 通过 main-process IPC 和有界 GIO 启动 KCalc；只有私有
`0600` 结果、唯一新增非零 PID 的 compositor UUID、精确 KWin close 回执、Echo 自身
AT-SPI marker 与干净退出全部成立才完成。
同一 KWin 脚本还按精确 app identity 将 Echo 桌面设为 `keepBelow`、无边框、跨虚拟
桌面并跳过 taskbar/pager，使普通原生应用位于桌面玻璃层之上。

便携协议测试：

```bash
python3 deploy/desktop-session/test_echo_kwin_window_bridge.py
python3 deploy/desktop-session/test_verify_wayland_native_app_ipc.py
```

第二组 8 项测试拒绝畸形/超大快照、重复或非 canonical UUID、旧窗口、零 PID、非 KCalc
身份和多个新 KCalc，关闭后也必须确认同一 UUID 已从 compositor 快照消失。

这一阶段表示 Wayland 窗口控制桥和可选择的 DRM 生产候选会话已组装；默认会话仍由
`kwin_x11` 启动。在候选会话完成 Linux CI/raw 与真机运行、`kwin_wayland` 真正成为
默认 compositor、Echo Shell 拆成独立桌面/菜单栏/Dock surface，并完成 XWayland、
多屏和 HiDPI 实跑前，不报告 C1 完成。

## 原生控制中心

控制中心在浏览器预览里仍是无宿主权限的视觉模拟；进入原生 Linux 桌面会话后，状态
和动作改由隔离 preload bridge 提供。Wi-Fi 通过 NetworkManager 的固定 `nmcli` 参数
读取和切换，蓝牙通过 BlueZ `bluetoothctl`，音量通过默认 PipeWire sink 的 `wpctl`，
亮度通过 `brightnessctl`，电池则直接读取 `/sys/class/power_supply`。渲染端不能传入
可执行文件、命令文本或未限定参数，非 Linux/非原生会话返回无能力。

便携边界测试：

```bash
cd frontend
node electron/system-controls.test.cjs
```

该测试证明解析、能力隔离、数值边界和固定参数向量；真实无线电、音频、背光和电池
仍必须由 raw/真机验收证明。所有安装后桌面冷启动 gate 还会等待
`ECHO_SYSTEM_CONTROLS_READY`：它证明打包后的 Electron 主进程确实加载控制桥并找到
四个固定 Linux provider，但不会把无线网络名、蓝牙控制器名或其他设备身份写进日志。

Dock 与控制中心的“系统设置”在原生会话中会解析并启动发行版安装的 KDE System
Settings，而不是跳回 Agent 工作台。镜像显式安装 NetworkManager、BlueDevil、音频、
显示与电源 KCM；X11 和 Wayland 会话都要求 KDE PolicyKit agent 保持存活，冷启动
gate 会等待 `ECHO_AUTH_AGENT_READY`。因此需要管理员授权的系统配置能够显示本机
密码提示，同时网页预览仍不会获得系统设置或提权能力。

自定义 Echo 会话不会经过完整 Plasma 启动器，因此不能依赖 Plasma 的 XDG autostart
隐式拉起电源服务。两个生产会话现在都直接监管发行版的 PowerDevil daemon，并要求
它取得 `org.kde.Solid.PowerManagement` 会话总线名，同时要求系统总线上的 UPower 和
Power Profiles 服务就绪。冷启动 gate 还会等待
`ECHO_POWER_MANAGEMENT_READY`；电源设置、合盖/电源键策略、亮度、空闲和休眠状态因此
有真实后台执行者，而不只是一个能够打开的 KCM 页面。PowerDevil 退出会终止当前图形
会话，避免系统在失去电源/休眠协调后继续伪报健康。

通知中心也不再使用硬编码“系统已就绪”卡片。两个生产会话会直接启动 Echo 的
`org.freedesktop.Notifications` 服务；普通 Linux、KDE、Flatpak 应用以及 Electron 的
Web Notification 都通过标准会话 D-Bus 进入同一份有界历史。服务只接受并保存纯文本，
最多 100 条，不读取通知声明的图标路径，也不宣告尚未实现的 action 能力。Electron
只能连接 `$XDG_RUNTIME_DIR/echo-os/notifications.sock`，套接字必须属于当前会话用户且
权限不宽于 `0600`。`ECHO_NOTIFICATION_SERVICE_READY` 和可信 desktop-ready 文件都是
冷启动完成条件；D-Bus 名称、私有套接字或守护进程消失会令会话失败关闭。浏览器预览
没有该桥时只说明“原生 Linux 会话中启用”，不会伪造系统通知。

## 多语言输入

Echo 镜像显式安装 Fcitx5、中文拼音、GTK3/GTK4、Qt5/Qt6 frontend、独立配置工具和
KDE System Settings KCM。自定义会话不依赖 Plasma autostart：X11 与 Wayland 都启动
一个前台 Fcitx5，并等待 `org.fcitx.Fcitx5` 会话 D-Bus 名称后才发布
`ECHO_INPUT_METHOD_READY`。`GTK_IM_MODULE=fcitx`、`QT_IM_MODULE=fcitx`、
`XMODIFIERS=@im=fcitx` 和 `SDL_IM_MODULE=fcitx` 在 compositor 启动前固定，后续由
Echo Shell 启动的本地应用继承同一输入环境。Fcitx5 进程或 D-Bus 名称消失会结束会话，
避免在中文输入失效时继续报告健康。

Debian 的 Xvfb/KWin smoke 和 KWin virtual-Wayland smoke 都会启动真实 Fcitx5 并确认
名称所有权；整机的 OEM、SDDM/X11、SDDM/Wayland 和 direct desktop 四道门也要求
输入法标记。它们尚不能证明按键合成、候选窗口定位或文本实际上屏；这些仍需用真实
Electron、GTK、Qt 输入框，在 Linux raw 和多屏/缩放真机上执行。

## 系统剪贴板

Plasma 6 不再交付可独立执行的 `/usr/bin/klipper`；`org.kde.klipper.desktop` 的
`Exec=/usr/bin/false`，真正实现位于 `plasma-workspace` 的
`org.kde.plasma.private.clipboard` QML 模块和 `libklipper6`。Echo 不启动完整
Plasma Shell，也不运行会显示额外窗口的 `plasmawindowed`。生产会话改由
`/usr/lib/echo-os/echo-clipboard-host` 建立一个无窗口 `QApplication`，只实例化发行版
Klipper 模块。X11 与 Wayland 都等待 `org.kde.klipper`、私有数据库和宿主 PID 后发布
`ECHO_CLIPBOARD_READY`；任一项消失会结束降级会话。

`/etc/xdg/klipperrc` 把历史限制为 20 项、忽略未显式复制的 X11 primary selection，
保留文本、图片和其他 MIME 数据，并设置 `NoEmptyClipboard=true`，因此复制源应用退出后
Klipper 会接管 selection owner。`KeepClipboardContents=false` 禁止跨登录恢复；宿主还
把 `KLIPPER_DATABASE` 强制固定到
`$XDG_RUNTIME_DIR/echo-os/clipboard/history3.sqlite`（mode `0600`），所以异常退出也不会
把复制内容落到持久 Home。该进程的 `XDG_CONFIG_HOME` 与 `XDG_CACHE_HOME` 也被隔离到
同一 runtime 子树，并强制关闭上游可能包含文本的 Klipper debug category；宿主本身不
读取或记录 clipboard payload。

Linux CI 的 X11 路径用 `xclip -loops 1`，Wayland 路径用
`wl-copy --foreground --paste-once` 创建一次性源 owner；只有该进程因 Klipper 取数而
退出、随后第二个 `xclip`/`wl-paste` 客户端仍取回固定假数据，测试才通过。macOS 本机
只能执行路径/权限策略单测与静态契约；真实多 MIME、超大对象、密码管理器 secret hint、
重登清除和第三方应用互操作仍需 Linux runner、raw 和真机取证。

## 无障碍基础链路

Echo 不把“镜像里装了一个屏幕阅读器”当作无障碍完成。X11 与 Wayland 会话都在图形
应用启动前导出 `QT_ACCESSIBILITY=1`，显式启动并监管 Debian 的
`at-spi-bus-launcher --launch-immediately`，而且只有 `org.a11y.Bus.GetAddress` 真能
返回辅助技术总线地址时才发布 `ECHO_ACCESSIBILITY_READY`。Electron 同时带
`--force-renderer-accessibility` 启动；桌面根和 Dock 动作有稳定 accessible name。

`echo-accessibility-smoke.py` 不序列化或打印 AT-SPI 树，只在最多 10000 个节点、64 层
深度内寻找固定的 `Echo OS 桌面` 标记，并核对该对象属于刚启动的 Electron PID 或其
子进程。生产 desktop-ready、Linux X11 smoke 和四道安装后冷启动门都要求这一真实树
标记。Wayland compositor smoke 还会打开一个原生 GTK 按钮并从另一个进程通过 AT-SPI
找到固定标记，避免只证明 Electron 自己的 DOM。

镜像显式安装 `orca`、Speech Dispatcher 和离线 eSpeak NG backend，并通过应用目录
交付“屏幕阅读器”入口；Orca 不会在每次登录时自动发声。SDDM 的 X11 greeter 也启用
Qt AT-SPI bridge，并用 `Super+Alt+S` 切换读屏。该快捷键 helper 只以 `sddm` 用户运行，
只接受本地 `seat0`、`Class=greeter`、`Remote=no` 的 logind 会话和受限 Xauthority，且只能
执行固定 Orca 参数，不解析 shell。当前 macOS 工作区只执行了探针/helper 单测、脚本/
镜像契约和工作流定义；工作流会在无自动登录 raw 的真实 greeter 上通过 QEMU 虚拟键盘
发送该组合键，并等待 Orca 进程启动标记。它尚未在当前环境执行，也没有登录前真实语音、
键盘/焦点顺序、缩放/高对比度或残障用户验收证据，因此不能称为完整无障碍。

## 当前边界

- C0 使用 KWin X11 + EWMH，便于现阶段 VM 和 PC 验证真实多窗口。
- Wayland 自动化 gate 覆盖虚拟双输出和 1.25× 缩放，但仍是无实体显示器、无输入
  设备的 llvmpipe 验收，不能替代热插拔、物理双屏、休眠唤醒和 GPU 真机矩阵。
- KWin Wayland 候选会话、KScreenLocker 和 compositor-side 桌面下层规则已有源码；
  默认切换、独立 Shell surface、多屏/HiDPI 真机、原子更新和恢复镜像仍属于后续
  验收门；详见 [目标 C 架构](../../docs/GENERAL_DESKTOP_PLAN.md)。
- 这不是完成声明。没有 VM/真机证据时，只能报告源码和可移植测试通过。
