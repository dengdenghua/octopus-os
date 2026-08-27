# Echo Wayland gate

`.github/workflows/echo-wayland-gate.yml` 把 KWin Wayland 合成器验收从整机、存储、
打印和更新流程中单独拆出。push 到 `codex/echo-wayland-gate` 会在 Debian 13 容器中
启动 KWin virtual backend，并创建两个 1.25× 输出。

该门打包真实 Electron main/preload 与生产窗口桥，但使用
`wayland-renderer-smoke.html` 作为无网络、无账号、无业务状态的有界 renderer。
它验证以下链路：

- KWin Wayland 和 XWayland 同时就绪；
- compositor bridge 发布两个输出和真实窗口 UUID；
- Electron 使用 Ozone/Wayland 启动并发布私有 renderer 就绪文件；
- preload→IPC→GIO 启动唯一新增的 KCalc，再由 KWin 精确关闭；
- 原生 GTK 窗口完成聚焦、最小化、恢复和关闭；
- Fcitx5、易失 Klipper、AT-SPI 与 KScreenLocker 的运行状态成立。

它不能替代完整 Echo Desktop UI、SDDM/raw 镜像、实体 GPU、多屏热插拔、休眠唤醒
或真实 PAM 密码验收。
