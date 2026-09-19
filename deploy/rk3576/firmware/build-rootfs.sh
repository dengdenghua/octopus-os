#!/usr/bin/env bash
# RK3576 (tvi3512r) · Echo OS ARM64 系统固件 rootfs 装配。
#
# 与 x86 链路 (deploy/provision/) 完全平行:不改其任何文件、不共享状态。
# 在 Linux x86_64 构建主机以 root 运行;依赖: losetup/mount、
# qemu-user-static + binfmt、SDK 目录树。
#
# 用法:
#   RK_SDK_DIR=/path/rk3576_linux-6.1 \
#   RK_ROOTFS=/path/rootfs.img \
#   ECHO_PAYLOAD=/path/echo-payload \
#   BSP_HEADERS_DEB=/path/kernel-headers.deb \
#   ./deploy/rk3576/firmware/build-rootfs.sh
#
# 幂等: WORKDIR 内每阶段哨兵,重跑跳过已完成部分。
# 打包: 阶段完成后按 README 交回 SDK mkfirmware.sh (RK_AUTO_PACK=1 可代跑)。
set -euo pipefail

FW_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKDIR="${ECHO_FW_WORKDIR:-$FW_DIR/.build-work}"
STATE_DIR="$WORKDIR/sentinels"
MNT="$WORKDIR/rootfs"

: "${RK_SDK_DIR:?set RK_SDK_DIR to the unpacked SDK tree}"
: "${RK_ROOTFS:?set RK_ROOTFS to the vendor Debian rootfs image (ext4)}"
: "${ECHO_PAYLOAD:?set ECHO_PAYLOAD to the payload dir (see firmware README)}"
BSP_HEADERS_DEB="${BSP_HEADERS_DEB:-}"
# 桌面形态(2026-09-12 拍板:HDMI 本地桌面):=0 可裁成 headless 变体
ECHO_FW_DESKTOP="${ECHO_FW_DESKTOP:-1}"
# 内存档位(GB):驱动 resource-tune 阶段的全套参数(ARC/zram/内存上限)
#   4  → 求生档: ARC 512M, zram 3G, 后端 MemoryHigh 1.5G(建议配 ECHO_FW_DESKTOP=0)
#   8  → 全形态下限: ARC 1G, zram 2G, MemoryHigh 3G
#   16 → 舒适档: ARC 2G, zram 4G, MemoryHigh 6G
ECHO_FW_RAM_GB="${ECHO_FW_RAM_GB:-8}"
case "$ECHO_FW_RAM_GB" in
  4)  ZFS_ARC_MAX=$((512*1024*1024));  ZRAM_SIZE="3G"; ECHO_MEM_HIGH="1500M" ;;
  8)  ZFS_ARC_MAX=$((1*1024*1024*1024));   ZRAM_SIZE="2G"; ECHO_MEM_HIGH="3G" ;;
  16) ZFS_ARC_MAX=$((2*1024*1024*1024));   ZRAM_SIZE="4G"; ECHO_MEM_HIGH="6G" ;;
  *) die "ECHO_FW_RAM_GB 只支持 4/8/16(其他档位请显式改脚本,不要静默猜)" ;;
esac

log() { printf '[echo-fw %s] %s\n' "$(date +%H:%M:%S)" "$*"; }
die() { log "✗ $*"; exit 1; }
is_done() { [ -f "$STATE_DIR/$1" ]; }
mark() { touch "$STATE_DIR/$1"; }
skip() { log "  = $1 已完成,跳过"; }

mkdir -p "$STATE_DIR"

# ── preflight ────────────────────────────────────────────────
if ! is_done preflight; then
  log "stage preflight"
  [ "$(id -u)" -eq 0 ] || die "请用 root 运行"
  [ -d "$RK_SDK_DIR/device/rockchip" ] || die "RK_SDK_DIR 不像 SDK 树(缺 device/rockchip)"
  [ -f "$RK_ROOTFS" ] || die "rootfs 镜像不存在: $RK_ROOTFS"
  [ -d "$ECHO_PAYLOAD/wheelhouse" ] || die "载荷缺 wheelhouse/"
  [ -d "$ECHO_PAYLOAD/webui-dist" ] || die "载荷缺 webui-dist/"
  [ -d "$ECHO_PAYLOAD/agent-codex" ] || die "载荷缺 agent-codex/(arm64 musl 切片)"
  [ -x /usr/bin/qemu-aarch64-static ] || die "缺 qemu-user-static: /usr/bin/qemu-aarch64-static"
  command -v losetup >/dev/null || die "缺 losetup"
  grep -q aarch64 /proc/sys/fs/binfmt_misc/qemu-aarch64 2>/dev/null \
    || log "  ! binfmt 未注册 aarch64(apt/dkms chroot 步骤会失败),尝试下方修复"
  # tvi3512r 分区表:具体文件名以 SDK 实际为准,先做宽松定位并留档
  find "$RK_SDK_DIR/device/rockchip" -iname "*parameter*" \
    | grep -i "tvi3512r\|rk3576" | tee "$WORKDIR/parameter-candidates.txt" \
    || die "未找到 tvi3512r/rk3576 parameter.txt,人工确认后继续"
  mark preflight
else
  skip preflight
fi

# ── mount ────────────────────────────────────────────────────
if ! is_done mount; then
  log "stage mount: loop 挂载 rootfs"
  mkdir -p "$MNT"
  mount -o loop "$RK_ROOTFS" "$MNT"
  mount -t proc proc "$MNT/proc"
  mount --bind /sys "$MNT/sys"
  mount --bind /dev "$MNT/dev"
  cp /usr/bin/qemu-aarch64-static "$MNT/usr/bin/"
  mark mount
else
  skip mount
fi
cleanup_mount() {
  mountpoint -q "$MNT/dev"  && umount "$MNT/dev"
  mountpoint -q "$MNT/sys"  && umount "$MNT/sys"
  mountpoint -q "$MNT/proc" && umount "$MNT/proc"
  mountpoint -q "$MNT"      && umount "$MNT"
}
trap cleanup_mount EXIT

chroot_run() { chroot "$MNT" /usr/bin/env DEBIAN_FRONTEND=noninteractive "$@"; }

# ── apt ──────────────────────────────────────────────────────
if ! is_done apt; then
  log "stage apt: 安装 packages-arm64.txt(首版允许在线;量产换离线 deb 仓)"
  chroot_run bash -c 'apt-get update && apt-get install -y --no-install-recommends \
      $(grep -v "^#" '"$FW_DIR"'/packages-arm64.txt | grep .)'
  mark apt
else
  skip apt
fi

# ── sdk-userspace: SDK 用户态加速库(NPU/硬编解码)烘焙 ────────
# RKNN runtime / MPP / RGA 的 deb 由 SDK debian/ 构建产出,清单见
# sdk-userspace.txt;内核态驱动不在此列(随 BSP 内核原样保留)。
if ! is_done sdk-userspace; then
  log "stage sdk-userspace: 烘焙 RKNN/MPP/RGA 用户态库"
  : "${RK_USERSPACE_DEB_DIR:?set RK_USERSPACE_DEB_DIR to SDK-built userspace debs}"
  [ -d "$RK_USERSPACE_DEB_DIR" ] || die "RK_USERSPACE_DEB_DIR 不存在: $RK_USERSPACE_DEB_DIR"
  for f in "$RK_USERSPACE_DEB_DIR"/*.deb; do
    chroot_run dpkg -i "$f" || chroot_run apt-get install -yf   # 补依赖后重试
  done
  # 最低验证:库文件落位(设备节点要上板才有)
  ls "$MNT"/usr/lib/librknnrt* >/dev/null 2>&1 \
    || ls "$MNT"/usr/lib/aarch64-linux-gnu/librknnrt* >/dev/null 2>&1 \
    || die "librknnrt 未落位,检查 rknpu2 runtime deb"
  ls "$MNT"/usr/lib/*/librockchip_mpp* >/dev/null 2>&1 \
    || ls "$MNT"/usr/lib/librockchip_mpp* >/dev/null 2>&1 \
    || die "librockchip_mpp 未落位,检查 mpp deb"
  if [ "$ECHO_FW_DESKTOP" = 1 ]; then
    ls "$MNT"/usr/lib/*/libmali.so* >/dev/null 2>&1 \
      || ls "$MNT"/usr/lib/libmali.so* >/dev/null 2>&1 \
      || die "libmali 未落位(HDMI 桌面需要 Wayland/GLES 变体),检查 GPU deb"
  fi
  mark sdk-userspace
else
  skip sdk-userspace
fi

# ── zfs(构建期编 kmod,首启不再 dkms) ────────────────────────
if ! is_done zfs; then
  log "stage zfs: BSP headers + OpenZFS kmod"
  [ -n "$BSP_HEADERS_DEB" ] && [ -f "$BSP_HEADERS_DEB" ] \
    || die "缺 BSP_HEADERS_DEB —— 这是当前最大风险项,见 README 开放问题"
  chroot_run dpkg -i "$BSP_HEADERS_DEB"
  chroot_run dkms autoinstall
  ls "$MNT"/lib/modules/*/updates/dkms/zfs.ko* >/dev/null 2>&1 \
    || die "zfs kmod 未见产出,检查 dkms 构建日志(chroot 内 /var/lib/dkms)"
  mark zfs
else
  skip zfs
fi

# ── payload ──────────────────────────────────────────────────
if ! is_done payload; then
  log "stage payload: 烘焙 Echo 载荷到 /opt/echo-os"
  chroot_run mkdir -p /opt/echo-os
  # 1) Python 闭包 → 独立 venv(与 x86 同路径,服务单元直接复用)
  chroot_run bash -c 'python3 -m venv /opt/echo-os/.venv \
      && /opt/echo-os/.venv/bin/pip install --no-index --find-links \
           '"$ECHO_PAYLOAD"'/wheelhouse --only-binary=:all: \
           --require-hashes -r '"$ECHO_PAYLOAD"'/wheelhouse/runtime-requirements.lock'
  # 2) 源码树 + 载荷(校验沿用既有管线,不另造标准)
  cp -a "$ECHO_PAYLOAD/tree/." "$MNT/opt/echo-os/"
  mkdir -p "$MNT/opt/echo-os/webui-dist" "$MNT/opt/echo-os/agent-codex"
  cp -a "$ECHO_PAYLOAD/webui-dist/." "$MNT/opt/echo-os/webui-dist/"
  cp -a "$ECHO_PAYLOAD/agent-codex/." "$MNT/opt/echo-os/agent-codex/"
  # 3) 桌面形态: Electron arm64 包(electron-builder --linux dir --arm64 产物)
  if [ "$ECHO_FW_DESKTOP" = 1 ]; then
    if [ -d "$ECHO_PAYLOAD/desktop" ]; then
      mkdir -p "$MNT/opt/echo-os/frontend/release"
      cp -a "$ECHO_PAYLOAD/desktop/." "$MNT/opt/echo-os/frontend/release/"
    else
      die "桌面形态缺载荷 desktop/(electron-builder arm64 产物) —— 或显式 ECHO_FW_DESKTOP=0"
    fi
  fi
  if [ -f "$ECHO_PAYLOAD/agent-bundle.json" ]; then
    cp "$ECHO_PAYLOAD/agent-bundle.json" "$MNT/opt/echo-os/"
    chroot_run python3 /opt/echo-os/deploy/appliance/agent_bundle.py verify \
      --bundle-root /opt/echo-os --manifest /opt/echo-os/agent-bundle.json
  fi
  mark payload
else
  skip payload
fi

# ── overlay ──────────────────────────────────────────────────
if ! is_done overlay; then
  log "stage overlay: systemd 单元 + firstboot"
  cp -a "$FW_DIR/overlay/." "$MNT/"
  # 镜像克隆惯例:烘空 machine-id,首启由 systemd 生成唯一 ID
  : > "$MNT/etc/machine-id"
  chroot_run systemctl enable echo-appliance-firmware.service
  chroot_run systemctl enable echo-firstboot-firmware.service
  if [ "$ECHO_FW_DESKTOP" = 1 ]; then
    chroot_run systemctl set-default graphical.target
    chroot_run systemctl enable sddm.service
  fi
  mark overlay
else
  skip overlay
fi

# ── resource-tune: 按内存档位写死资源参数(构建期定,首启零动作) ─
# 消费方与理由见 README「资源档位」;4G 档 + 桌面形态会大声警告。
if ! is_done resource-tune; then
  log "stage resource-tune: RAM=${ECHO_FW_RAM_GB}G → ARC=$((ZFS_ARC_MAX/1024/1024))M zram=$ZRAM_SIZE MemoryHigh=$ECHO_MEM_HIGH"
  if [ "$ECHO_FW_RAM_GB" -le 4 ] && [ "$ECHO_FW_DESKTOP" = 1 ]; then
    log "  ⚠ 4G 内存 + Electron 桌面 = OOM 高危;要么 ECHO_FW_DESKTOP=0,要么自担风险"
  fi
  # 1) ZFS ARC 上限:ZFS 默认吃一半物理内存,是 NAS 固件最大的可压项
  cat > "$MNT/etc/modprobe.d/echo-zfs.conf" <<EOF
# Echo firmware: ARC cap (ECHO_FW_RAM_GB=${ECHO_FW_RAM_GB})
options zfs zfs_arc_max=${ZFS_ARC_MAX}
EOF
  # 2) zram 压缩交换:LPDDR 带宽够,A72 换 CPU 不亏;低内存 ARM 标配
  cat > "$MNT/etc/systemd/zram-generator.conf" <<EOF
# Echo firmware: compressed swap (ECHO_FW_RAM_GB=${ECHO_FW_RAM_GB})
[zram]
zram-size = ${ZRAM_SIZE}
compression-algorithm = zstd
swap-priority = 100
fs-type = swap
EOF
  # 3) 内核参数:高 swappiness 让 zram 承接匿名页,给 ARC/页缓存腾地方
  cat > "$MNT/etc/sysctl.d/99-echo-tune.conf" <<'EOF'
# Echo firmware: memory posture (zram-backed swap)
vm.swappiness = 100
vm.vfs_cache_pressure = 50
EOF
  # 4) 后端内存软上限:防 agent 栈把桌面/推理挤死(OOM 前先自我收敛)
  mkdir -p "$MNT/etc/systemd/system/echo-appliance-firmware.service.d"
  cat > "$MNT/etc/systemd/system/echo-appliance-firmware.service.d/memory.conf" <<EOF
# Echo firmware: backend memory ceiling (ECHO_FW_RAM_GB=${ECHO_FW_RAM_GB})
[Service]
MemoryHigh=${ECHO_MEM_HIGH}
MemoryMax=4G
EOF
  # 5) eMMC 寿命与容量:日志封顶 + 定期 TRIM(固件是 eMMC 常驻写手)
  mkdir -p "$MNT/etc/systemd/journald.conf.d"
  cat > "$MNT/etc/systemd/journald.conf.d/echo-cap.conf" <<'EOF'
# Echo firmware: eMMC endurance
Compress=yes
SystemMaxUse=64M
MaxRetentionSec=7day
EOF
  chroot_run systemctl enable fstrim.timer
  # 6) 桌宠(Godot 常驻进程):4G 档默认关,别和 ARC/推理抢内存
  if [ "$ECHO_FW_RAM_GB" -le 4 ]; then
    mkdir -p "$MNT/etc/systemd/system/echo-desktop.service.d"
    cat > "$MNT/etc/systemd/system/echo-desktop.service.d/pet.conf" <<'EOF'
# Echo firmware: 4G tier keeps the Godot desktop pet out of memory
[Service]
Environment=ECHO_PET_DISABLED=1
EOF
  fi
  mark resource-tune
else
  skip resource-tune
fi

# ── pack ─────────────────────────────────────────────────────
if ! is_done pack; then
  log "stage pack: 卸载 + 校验,交回 SDK 打包"
  cleanup_mount
  trap - EXIT
  e2fsck -fp "$RK_ROOTFS"
  # rootfs 分区大小须在 parameter.txt 同步调大(见 README 开放问题)
  if [ "${RK_AUTO_PACK:-0}" = 1 ]; then
    (cd "$RK_SDK_DIR" && ./mkfirmware.sh)
    log "  → SDK out/ 下 update.img 就绪,upgrade_tool/RKDevTool 烧写"
  else
    log "  手动: cd $RK_SDK_DIR && ./mkfirmware.sh"
  fi
  mark pack
else
  skip pack
fi

log "✓ 固件 rootfs 装配完成: $RK_ROOTFS"
