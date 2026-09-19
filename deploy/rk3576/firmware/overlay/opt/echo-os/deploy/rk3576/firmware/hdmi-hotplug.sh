#!/usr/bin/env bash
# HDMI 热插拔 → 桌面栈实时升降(Echo OS RK3576 固件)。
#
# 由 udev 的 DRM change 事件经 echo-desktop-hotplug.service 触发;
# 首启 services 阶段也调用一次做初始判定。桌面栈是"进程"不是
# "显示器":不拔就关,1.5-2.5G 内存照付——本脚本保证栈状态跟随
# 物理显示器实时对齐。
#
# 决策落 /var/lib/echo-os/desktop-mode.txt(桌面当前状态唯一事实源)。
# 覆盖:
#   ECHO_FW_FORCE_DESKTOP=1  强制开桌面(无视显示器状态)
#   ECHO_FW_HOTPLUG_KEEP=1   拔线不关桌面(保留会话,放弃内存回收)
set -euo pipefail

log() { printf '[echo-desktop-hotplug %s] %s\n' "$(date +%H:%M:%S)" "$*"; }
OUT=/var/lib/echo-os/desktop-mode.txt
mkdir -p /var/lib/echo-os

# 注意:必须锚定行首——"disconnected" 包含 "connected" 子串,裸 grep 必误判
detect() {
  ls /sys/class/drm/*/status >/dev/null 2>&1 \
    && grep -qs '^connected' /sys/class/drm/*/status
}

if [ "${ECHO_FW_FORCE_DESKTOP:-0}" = 1 ]; then
  mode=forced
elif detect; then
  mode=display-connected
else
  mode=no-display
fi

case "$mode" in
  forced | display-connected)
    systemctl enable --now sddm >/dev/null 2>&1 \
      || log "  ! sddm 启动失败(显示栈缺件?mali 状态见 journalctl -b)"
    log "+ 显示器已连接:桌面栈启动($mode)"
    ;;
  no-display)
    if [ "${ECHO_FW_HOTPLUG_KEEP:-0}" = 1 ]; then
      log "= 无显示器,但 ECHO_FW_HOTPLUG_KEEP=1:保留桌面栈不回收"
      mode="no-display-kept"
    elif systemctl is-active --quiet sddm 2>/dev/null; then
      systemctl disable --now sddm >/dev/null 2>&1 || true
      log "- 显示器已断开:桌面栈停止(回收 ~1.5-2.5G;Web 桌面不受影响)"
    else
      log "= 无显示器:桌面栈保持停止"
    fi
    ;;
esac

printf 'desktop_mode=%s\nevent=%s\n' "$mode" "${1:-unknown}" > "$OUT"
