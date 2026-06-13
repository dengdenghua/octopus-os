# 原生 shell 整机套件(A 路线)· 开机即 Electron agent 桌面

把一台 Debian(VM/真机)变成 octopus-os 设备:**开机 → splash → 自动登录 → cage →
Electron 全屏 agent 桌面**,无浏览器壳、无桌面环境。配套见 [docs/NATIVE_SHELL_PLAN.md](../../docs/NATIVE_SHELL_PLAN.md)。

> 本机(开发 mac)无 Linux/显示,以下只能在 **Debian VM 或真机**验证;脚本已 `bash -n`
> 语法检查,逻辑正确性需你在 VM 跑通后回填。

## 组成

| 文件 | 作用 |
|---|---|
| `setup-native-shell.sh` | 一键装机:装 cage/plymouth/node/docker、建设备用户、同步源码+构建前端、装 systemd 会话 unit、设默认图形 |
| `octopus-shell.service` | systemd 会话:`cage -- launch.sh`,退出即重启 |
| `octopus-shell-launch.sh` | cage 托起它 → `OCTOPUS_NATIVE_SHELL=1 electron`(加载构建好的 dist,连 :8000) |

## 在 VM 上验证(推荐 Debian 12 + 给虚拟 GPU)

```bash
# 1) 拉两个仓库(去 fork 后构建后端需 agent 源码),装机
git clone .../octopus-agent && git clone .../octopus-os && cd octopus-os
sudo ./deploy/native-shell/setup-native-shell.sh

# 2) 起后端(agent/appliance 容器)
./deploy/appliance/prepare-agent-wheel.sh && ./deploy/appliance/prepare-agent-webui.sh
(cd deploy/appliance && docker compose up -d --build)

# 3) 先不重启,手动起一次会话看日志
sudo systemctl start octopus-shell
journalctl -u octopus-shell -b -f
```

### 验证清单 ⭐

- [ ] `systemctl start octopus-shell` → 屏幕进入**全屏 Electron agent 桌面**(极光壁纸 + Dock),无窗框、无浏览器地址栏
- [ ] Dock/启动器能看到(下一步前端接好后)真实已装应用图标
- [ ] 桌面能连后端:对话/文件管理器可用(后端 :8000 起着)
- [ ] `reboot` → 开机自动进桌面(plymouth → 自动登录 → 桌面),全程不需键鼠登录
- [ ] 关掉 Electron(或它崩)→ service 自动重启回桌面

## 常见卡点(按概率)

1. **cage 起不来:`cannot find seat` / `XDG_RUNTIME_DIR not set`**
   → unit 的 `PAMName=login` + `TTYPath=/dev/tty7` 是给 cage 弄 seat 的;确认
   `seatd`/`systemd-logind` 在跑,设备用户在 `seat`/`video`/`input` 组。
2. **黑屏 / GPU**:VM 里加 `--disable-gpu`(改 `octopus-shell-launch.sh` 的 electron 标志),
   或给 VM 开 3D 加速;真机一般不用。
3. **Electron Wayland 不显示**:确认 `--ozone-platform-hint=auto`;Electron 34 支持 Wayland。
4. **精简镜像里 sandbox 报错**:launch.sh 取消注释 `--no-sandbox`(仅受限内核需要)。
5. **没 splash**:plymouth 要内核 cmdline 带 `quiet splash` + `update-initramfs -u`;非关键,可后补。

## v1 vs 硬化(后续)

- **v1(本套件)**:设备上**从源码跑** Electron(带 node_modules + 构建产物),便于快速验证。
- **硬化①**:`electron-builder` 打成单个 AppImage/unpacked,设备不再需要 node/npm,镜像更薄。
- **硬化②**:`debootstrap`/`mkosi` 做成可烧录 `.img`(只读根 + A/B 更新),= 真正的出厂镜像。
- **存储**:整机路线集成 OMV 存储栈(消费不自研),OMV 管理界面用窗口管理器开成桌面应用。

## 不影响其他形态

`OCTOPUS_NATIVE_SHELL` 未开时,Electron 仍是原寄生窗口模式;浏览器网页路线照常。
