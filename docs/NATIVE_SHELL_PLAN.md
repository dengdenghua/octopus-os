# 原生 shell(A 路线)· Electron 当会话 shell

> 目标:让 octopus-os 成为**真正的设备 OS**——开机即进原生 agent 桌面(无浏览器壳、
> 无底层桌面环境),而不是"浏览器里的网页桌面"。选定 **A 路线:Electron 当会话**
> (复用现有 React 桌面,主进程拿真实系统手),而非自研 Wayland 合成器(B,留待要
> 合成真实原生程序窗口时)。

## 1. 为什么 A 而非 B(NAS 设备语境)

| | A · Electron 会话 | B · 自研合成器 |
|---|---|---|
| React 桌面复用 | 100%(`IS_NATIVE_DESKTOP` 已留口) | 改造成 layer-shell 层 |
| 管理/起停/图标/文件 所有本地应用 | ✅ | ✅ |
| Docker 网页应用开成桌面窗口 | ✅(BrowserWindow/iframe) | ✅ |
| **合成真实原生 GUI 程序窗口** | ❌(切全屏可用,不浮动) | ✅ |
| 工作量 | 周级 | 月级(系统工程) |

NAS/agent 设备上"应用"几乎全是 Docker 网页服务 → A 在体验上无可感差距,工作量小一个量级。真要合成原生程序再上 B,届时 React 桌面可平移成 layer-shell。

## 2. 分层(整机镜像)

```
Debian 薄镜像
 ├─ plymouth                  开机 splash(品牌)              [镜像层]
 ├─ cage(极简 Wayland 合成器)  仅把 Electron 全屏托起          [镜像层]
 ├─ systemd 自动登录 → Electron 会话                          [镜像层]
 └─ Electron = 桌面 shell
     ├─ 渲染:现有 React 桌面(壁纸/Dock/窗口/文件管理器)
     └─ 主进程(Node)= 系统手:枚举/起停本地应用、文件、docker、Agent 系统之手
后端 + 应用仍 Docker:agent/appliance 容器 + 第三方 NAS 应用容器
```

`OCTOPUS_NATIVE_SHELL=1`(或 `OCTOPUS_SHELL_MODE=session`)→ Electron 窗口
fullscreen + frame:false + kiosk 独占屏幕。

## 3. 能力边界(已对用户确认)

- ✅ **管理所有本地应用**:扫 `/usr/share/applications` 等 XDG 目录的 `.desktop`
  (Name/Icon/Exec/Categories)枚举 + `spawn` 启动;Docker 应用仍走后端 app_registry,
  Dock 合并显示。
- ✅ **文件管理**:Electron 主进程直连 Node `fs`(比网页版经后端更强)。
- ✅ **注册图标显示**:`.desktop` 的 `Icon=` + freedesktop 图标主题解析出真实图标;
  Docker 从容器 label;自定义清单可注册/排序/分组。
- ❌ **原生 GUI 程序窗口浮动进桌面**:这要 B;A 下可全屏切过去 + 热键切回(类平板/TV)。

## 4. 进度

### 已完成(本机可验证部分)
- `frontend/electron/system-shell.cjs`:系统手层——`.desktop` 解析、Exec 字段码清洗、
  可启动判定、图标主题解析、`listApplications()`、`launchApplication()`、IPC 注册。
- `frontend/electron/system-shell.test.cjs`:纯函数 5 测试过(`node` 直跑);mac 上
  `listApplications()` 安全返回 `[]`(无 XDG 目录,不崩)。
- `electron/main.cjs`:require 系统手 + 注册 IPC + `NATIVE_SHELL` 会话 shell 窗口
  (fullscreen/frameless/kiosk)。
- `electron/preload.cjs`:暴露 `window.octopus.apps.{list,launch}`。
- `src/types/electron.d.ts`:`NativeApp` 类型 + `apps` 契约。

### 待做
- **Dock/启动器渲染真实应用**(前端):`window.octopus.apps.list()` + Docker app_registry
  合并;图标显示(主进程加 `apps:icon` 返回 dataURL 或自定义 file 协议)。Linux 上验证。
- **整机镜像**(镜像层,需 Linux/VM):`debootstrap` 薄 Debian + plymouth 主题 + cage +
  自动登录 systemd unit + Electron 打包(electron-builder)+ docker 随机启动 agent/appliance。
- **VM 验证**:开机 → 自动登录 → 全屏 agent 桌面 → 点图标起应用 → 管文件。
- **真机 HDMI**:接显示器/电视实测。
- **真实多窗口**(增强):Docker 网页应用从 iframe 升级为独立 `BrowserWindow`。
- **配套**:OS 级 Agent 权限闸(Agent 动文件/起应用要授权;配第一攻击面"网页→Agent")。

## 5. 与现有路线的关系

- **寄生路线**(Electron 透明叠加,母体):不受影响——`NATIVE_SHELL` 未开时 main.cjs
  仍是原窗口模式。
- **网页路线**(浏览器看 os 桌面):仍可用;原生 shell 是它的"装进设备"形态。
