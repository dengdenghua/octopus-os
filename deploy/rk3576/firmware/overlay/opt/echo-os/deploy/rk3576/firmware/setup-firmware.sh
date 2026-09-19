#!/usr/bin/env bash
# Echo OS 首启配置 · RK3576 固件版(ARM64)。
#
# 与 x86 链路 (deploy/provision/base/setup-base.sh) 平行,不共享文件、
# 不修改对方。固件形态下 Python/Web/Codex/系统包已在构建期烘焙,
# 这里只做设备相关三步: identity → storage → services。严格离线。
#
# 幂等: /var/lib/echo-os/firstboot-firmware/<stage> 哨兵,重入续跑。
# 日志: journalctl -u echo-firstboot-firmware
set -euo pipefail

STATE_DIR=/var/lib/echo-os/firstboot-firmware
LOG_TAG="echo-firstboot-firmware"

log() { printf '[%s %s] %s\n' "$LOG_TAG" "$(date +%H:%M:%S)" "$*"; }
is_done() { [ -f "$STATE_DIR/$1" ]; }
mark()    { touch "$STATE_DIR/$1"; }
skip()    { log "  = $1 已完成,跳过"; }

[ "$(id -u)" -eq 0 ] || { log "✗ 请用 root 运行"; exit 1; }
mkdir -p "$STATE_DIR"

# 可选覆盖(与 x86 版同语义): /etc/echo-os/firstboot.env
[ -r /etc/echo-os/firstboot.env ] && . /etc/echo-os/firstboot.env

# ── identity: 设备身份落盘 ───────────────────────────────────
if ! is_done identity; then
  log "stage identity"
  # machine-id 的重置在构建期完成(build-rootfs.sh 烘空 /etc/machine-id,
  # 首启由 systemd-machine-id-setup 生成)——这里只兜底补齐。
  if [ ! -s /etc/machine-id ]; then
    systemd-machine-id-setup
    cp /etc/machine-id /var/lib/dbus/machine-id 2>/dev/null || true
  fi
  mkdir -p /var/lib/echo-os
  # 设备指纹:固件身份 = 镜像 commit + 板卡型号(供 Appliance 侧 machine-state 用)
  printf 'board=%s\nimage_commit=%s\nflashed_at=%s\n' \
    "${ECHO_FW_BOARD:-tvi3512r-rk3576}" \
    "${ECHO_IMAGE_COMMIT:-unknown}" \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    > /var/lib/echo-os/device-firmware.txt
  mark identity
else
  skip identity
fi

# ── storage: zfs kmod + pool 探测/导入(不自动建池) ───────────
# 与 x86 行为一致:建池由 Web 桌面 native-storage broker 引导完成。
if ! is_done storage; then
  log "stage storage"
  modprobe zfs 2>/dev/null || log "  ! zfs 模块加载失败(构建期 kmod 缺失?)"
  # 导入既有 pool(可导入但未导入的);无 pool 是正常首启状态
  if command -v zpool >/dev/null; then
    zpool import -a -N 2>/dev/null || true
    zpool list -Ho name 2>/dev/null | while read -r p; do
      log "  + 已导入 pool: $p"
    done
  else
    log "  ! 缺 zpool,zfsutils-linux 未正确烘焙?"
  fi
  systemctl enable --now udisks2 >/dev/null 2>&1 || true
  mark storage
else
  skip storage
fi

# ── services: 拉起控制面 ─────────────────────────────────────
if ! is_done services; then
  log "stage services"
  systemctl enable --now nginx >/dev/null 2>&1 || log "  ! nginx 未启用"
  systemctl enable --now echo-appliance-firmware.service
  # HDMI 桌面形态:初始判定 + 热插拔升降共用一份逻辑(hdmi-hotplug.sh,
  # 由 udev DRM change 事件实时触发;此处只做首启首评)。
  hotplug=/opt/echo-os/deploy/rk3576/firmware/hdmi-hotplug.sh
  if [ -d /usr/share/wayland-sessions ] && systemctl list-unit-files sddm.service &>/dev/null; then
    if [ -x "$hotplug" ]; then
      "$hotplug" firstboot || log "  ! 桌面状态初始评估失败(热插拔事件仍会重评)"
      while IFS= read -r line; do log "  $line"; done < /var/lib/echo-os/desktop-mode.txt 2>/dev/null || true
    else
      log "  ! 缺 hdmi-hotplug.sh,桌面栈回退常开(内存换可用性)"
      systemctl enable --now sddm >/dev/null 2>&1 || true
    fi
  fi
  if [ -e /dev/mali0 ] || [ -e /dev/mali ]; then
    log "  + GPU: /dev/mali 在位"
  else
    log "  ! GPU: /dev/mali 不在位(内核 mali 驱动未加载?桌面将回落软渲染)"
  fi
  # 数据目录属主交给 appliance 入口自修复(与容器入口同策略)
  mkdir -p /data
  mark services
else
  skip services
fi

log "✓ Echo OS (RK3576 firmware) 就绪"
log "  后端: systemctl status echo-appliance-firmware"
log "  Web : http://$(hostname).local"
