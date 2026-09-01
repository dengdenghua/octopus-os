# 旧 Native Shell 兼容入口

这一目录曾经实现“Cage 全屏托起 Electron、开机直接进入单应用”的 A 路线。它适合早期
bring-up，但不是目标 C 的通用桌面系统：没有正常的 SDDM/PAM 登录、多窗口 KWin、原生
应用生命周期、锁屏、PowerDevil、通知服务及 X11/Wayland 双会话门。

`setup-native-shell.sh` 现在只保留旧命令路径兼容，并直接委托
`deploy/desktop-session/setup-desktop-session.sh`。它不再安装 Cage、不再启用
`echo-shell.service`、不再以 `rsync --delete` 覆盖安装目录，也不会建立旧的自动登录
kiosk。当前安装方式是：

```bash
sudo ./deploy/desktop-session/setup-desktop-session.sh
```

`echo-shell.service` 和 `echo-shell-launch.sh` 仅作为早期 bring-up 设计证据保留，
不会进入目标 C 镜像，也不应在新设备上安装。当前可交付镜像、登录、安装器、A/B、
Recovery 与冷启动验收均以 `packaging/image/`、`deploy/oem/` 和
`deploy/desktop-session/` 为准。
