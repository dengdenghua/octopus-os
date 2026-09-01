# Electron 桌面壳

> 2026-06-13 重建。原 `frontend/electron/` 从未进入 git(`git log --all -- frontend/electron`
> 为空),在本地清理中丢失。本次按 `src/types/electron.d.ts` 留存的完整契约重写。

## 运行

```bash
pnpm electron:dev    # 启动 Vite(:3000)并在就绪后拉起 Electron
pnpm electron        # 仅拉起 Electron(假定 dev server 已在运行)
```

后端默认 `http://127.0.0.1:8000`,可用 `ECHO_BACKEND_URL` 覆盖;
前端地址可用 `ELECTRON_START_URL` 覆盖(默认 `http://localhost:3000`)。开发态固定使用
`localhost`，与浏览器入口共用同一登录站点，避免在 `localhost` 与 `127.0.0.1` 之间切换时
丢失 host-only 会话 Cookie。

## 实现状态(对照 electron.d.ts)

| 命名空间                                                                                       | 状态                                                                         |
| ---------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| `app` / `dialog` / `window` / `backend.getBaseURL`                                             | ✅ 完整                                                                      |
| `desktop`(桌面助手:列举/打开/分类移动/批量/撤销/系统信息 + items-changed 监听)                 | ✅ 完整                                                                      |
| `browser`(导航/JS/截图/取文/click/type/hover/scroll/waitFor/pressKey/ariaTree/清站点数据/下载) | ✅ 完整(ariaTree 走 CDP)                                                     |
| `extensions`(list/installFromFolder/setEnabled/remove)                                         | ✅ 基本(Electron loadExtension 的 API 子集限制)                              |
| `on` 八个事件通道                                                                              | ✅ 已接线；独立安装包下载更新后触发 `app:update-downloaded`                  |
| `desktop.installContextMenu`(Windows 右键菜单)                                                 | ⛔ 诚实降级:返回 `{ok:false}`,前端按钮会提示失败                             |
| `backend.restart`（重启镜像内固定的 `echo-agent.service`）                                     | ✅ 仅原生 Linux 会话；systemd/Polkit 授权且不接受 renderer 命令文本          |
| `apps`(枚举/启动 freedesktop 应用) / `windows`(真实 WM 窗口)                                   | ✅ X11/EWMH + KWin UUID/output bridge；Wayland 生产候选已组装、CI/raw 待实跑 |
| `notifications`（标准 Linux 通知历史的 list/close/clear）                                      | ✅ 原生会话私有 socket；浏览器/非 Linux 会话诚实返回无能力                   |
| `nativeGlass`（平台 compositor 玻璃）                                                          | ✅ macOS 26 AppKit；KWin X11 blur region；KWin Wayland 原生 Effect           |

## 已知边界

- 同一套前端有两种交付，不是两个产品分叉：
  - Windows/macOS/Linux 独立安装包使用 `packaging/desktop/build.yml`，随包携带固定版本的
    Agent 后端、Codex、首启配置和只读资源，并通过 Electron 更新通道独立升级。
  - Echo OS 镜像内的 Linux 目录包使用 `frontend/package.json` 的最小构建配置，只携带
    `app.asar` 与 Liquid Glass 视觉资源；它连接系统唯一的 `echo-agent.service`，不携带、
    不启动第二份 Agent/Codex，也不启用 Electron 更新通道。系统升级由 OS 的 A/B 更新链负责。
- Echo OS 目录包独占一个进入只读系统镜像的 `native-shell-profile.json` 身份标记；独立
  安装包明确不携带它。打包后的主进程据此自动进入 native-shell 模式，固定可执行文件名
  `echo-os-desktop` 只用于在标记意外缺失时失败关闭。`ECHO_NATIVE_SHELL`/`ECHO_SHELL_MODE`
  只保留给开发、会话布局和测试，不再是防止系统壳误启动独立 Agent 的唯一条件。
- `packaging/image/verify-native-shell-package.cjs` 对真实 x86-64 目录包做失败关闭检查：
  固定外部资源清单、拒绝 Agent/Codex/更新配置/原生 addon/测试文件，并将 ASAR 内关键
  主进程代码逐字节绑定到当前 checkout。
- 已配置 electron-builder：独立 Linux AppImage、Windows NSIS、macOS DMG/ZIP 都由平台
  发布 workflow 构建；更新元数据、blockmap、签名/公证与来源证明在上传前重新验证。
- Echo OS 会话安装器使用 `electron-builder --linux dir --x64`，避免开机依赖开发服务器。
- `webview` 标签已启用(workspace 内嵌浏览器依赖它)。
- 安全基线:contextIsolation 开、nodeIntegration 关、`window.open` 一律转系统浏览器。
- 目标 C 桌面会话使用 `ECHO_SHELL_MODE=desktop`:Electron 无框最大化但不进入
  kiosk，由 KWin 管理其上方的真实应用窗口。部署见 `deploy/desktop-session/`。
- `native-windows.cjs` 在 X11 继续使用 EWMH；在 `XDG_SESSION_TYPE=wayland`
  下只接受用户私有 socket 中经双重校验的 KWin UUID 快照与固定动作。
  伴随服务与 KWin 脚本见 `deploy/desktop-session/kwin-window-bridge/`。
- `native-liquid-glass.cjs` 在 Wayland 只调用 KWin 上固定的
  `org.echoos.KWin.LiquidGlass1` 接口，最多同步 8 个经过裁剪的圆角区域。KWin 原生 Effect
  负责背景模糊，WebGL 只保留折射/色散；Effect 不可用时自动退回 WebGL/CSS。
- Wayland 候选要求 Electron 在 renderer 真正离线加载后，向固定
  `$XDG_RUNTIME_DIR/echo-os/renderer-ready` 原子写入 mode-`0600` 标记；任意环境路径、
  符号链接或非私有父目录都会被拒绝。该 helper 有独立 Node 安全测试。
- `system-notifications.cjs` 不直接取得系统 D-Bus 名称，只连接会话监管的 Echo
  通知服务。它只接受精确的 `$XDG_RUNTIME_DIR/echo-os/notifications.sock`，并验证
  socket 类型、当前 UID 和私有权限；renderer 只能调用 capabilities、最多 100 条的
  list、uint32 id close 和 clear，不能选择路径、执行命令或读取通知声明的任意图标。
- `agent-service.cjs` 只允许原生 Linux 会话请求重启固定的
  `echo-agent.service`；服务名、systemctl 路径和参数都不由 renderer 提供，普通网页与
  开发模式只会返回无能力，实际授权仍由 systemd 的 Polkit 规则和桌面认证代理决定。
  `systemctl restart` 完成后还必须通过镜像内固定的
  `/usr/lib/echo-os/verify-native-agent-health`，证明运行时、WebUI 和恢复队列仍与镜像
  bundle 同源，之后才向设置页报告成功。
