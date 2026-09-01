#!/usr/bin/env bash
# 组装 Echo OS 装机 ISO。
#
# 思路:拿 Debian 13 (trixie) 官方 netinst 做底,往里塞一个 /echo/ 载荷目录,
# 改一下引导配置的 append 行,再重新打包。**不动 initrd** —— 这是刻意的。
#
# 为什么不动 initrd:
#   飞牛的做法是把自研 TUI 塞进 initrd,并 patch debian-installer-startup 两行
#   (usr/sbin/debian-installer-startup:19 与 S15lowmem:128)。这很精巧,但依赖
#   d-i 内部文件结构 —— 上游一改就碎。而且 Debian initrd 是多段拼接
#   (early microcode + 压缩主段),重打包容易出错。
#   我们改用 d-i 官方的 preseed/early_command 钩子,从 ISO 上直接跑 TUI,
#   官方支持、跨版本稳定。
#
# 产物:可刻录 U 盘(dd)/ 可挂 VM 的 hybrid ISO。
#
# 用法:
#   ./build-iso.sh [--iso <debian-netinst.iso>] [--mirror <url>] [--out <file>]
#   ./build-iso.sh --iso ~/debian-13.1.0-amd64-netinst.iso --mirror https://mirrors.ustc.edu.cn/debian
#
# 依赖:xorriso(apt install xorriso)。仅支持 Linux。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

DEBIAN_ISO_URL="${DEBIAN_ISO_URL:-https://cdimage.debian.org/debian-cd/current/amd64/iso-cd/}"
INPUT_ISO=""
MIRROR=""
OUT_ISO="$REPO_ROOT/dist/echo-os.iso"
WORK=""

log()  { printf '\033[1;34m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m  ! %s\033[0m\n' "$*" >&2; }
die()  { printf '\033[1;31m  ✗ %s\033[0m\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --iso)    INPUT_ISO="$2"; shift 2 ;;
    --mirror) MIRROR="$2";    shift 2 ;;
    --out)    OUT_ISO="$2";   shift 2 ;;
    --url)    DEBIAN_ISO_URL="$2"; shift 2 ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) die "未知参数: $1" ;;
  esac
done

[ "$(uname -s)" = "Linux" ] || die "必须在 Linux 上构建(需要 xorriso)"
command -v xorriso >/dev/null 2>&1 || die "缺少 xorriso: sudo apt install xorriso"

WORK="$(mktemp -d "${TMPDIR:-/tmp}/echo-iso.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/src" "$WORK/iso"

# ── 1. 取得 Debian netinst ────────────────────────────────
if [ -z "$INPUT_ISO" ]; then
  log "查找最新的 Debian amd64 netinst…"
  ISO_NAME=$(curl -fsSL "$DEBIAN_ISO_URL" \
    | grep -oE 'debian-[0-9.]+-amd64-netinst\.iso' | sort -uV | tail -1)
  [ -n "$ISO_NAME" ] || die "未能从 $DEBIAN_ISO_URL 解析出 ISO 名,请用 --iso 指定"
  INPUT_ISO="$WORK/$ISO_NAME"
  log "下载 $ISO_NAME"
  curl -fL --progress-bar -o "$INPUT_ISO" "$DEBIAN_ISO_URL/$ISO_NAME"
fi
[ -f "$INPUT_ISO" ] || die "ISO 不存在: $INPUT_ISO"

# ── 2. 解包 ───────────────────────────────────────────────
log "解包 $(basename "$INPUT_ISO")"
xorriso -osirrox on -indev "$INPUT_ISO" -extract / "$WORK/iso" 2>/dev/null \
  || die "解包失败"
chmod -R u+w "$WORK/iso"

# ── 3. 放载荷 ─────────────────────────────────────────────
log "放入 /echo/ 载荷"
PAYLOAD="$WORK/iso/echo"
mkdir -p "$PAYLOAD"

install -m0755 "$SCRIPT_DIR/installer/echo-install"  "$PAYLOAD/echo-install"
install -m0644 "$SCRIPT_DIR/installer/preseed.cfg"      "$PAYLOAD/preseed.cfg"
install -m0755 "$SCRIPT_DIR/base/setup-base.sh"         "$PAYLOAD/setup-base.sh"
install -m0644 "$SCRIPT_DIR/echo-firstboot.service"   "$PAYLOAD/echo-firstboot.service"
install -m0644 "$SCRIPT_DIR/99-echo-os"                  "$PAYLOAD/99-echo-os"

# 首次开机要用的服务单元也带上(setup-base.sh 会从 /opt/echo-os 里取,
# 但 d-i 阶段的 late_command 直接读 ISO,两份都放更省心)
mkdir -p "$PAYLOAD/units"
install -m0644 "$SCRIPT_DIR/base/echo-appliance.service" "$PAYLOAD/units/"
install -m0644 "$SCRIPT_DIR/base/echo-nginx.conf"        "$PAYLOAD/units/"

# 镜像源覆盖
if [ -n "$MIRROR" ]; then
  log "覆盖 Debian 镜像源为 $MIRROR"
  sed -i "s#^d-i mirror/http/hostname string .*#d-i mirror/http/hostname string ${MIRROR%/*}#" "$PAYLOAD/preseed.cfg" || true
  # 简单起见:整行替换成用户给的 URL 的 host/directory 拆分
  HOST="${MIRROR#*://}"; HOST="${HOST%%/*}"
  DIR="/${MIRROR#*://}"; DIR="${DIR#*/}"; DIR="/${DIR}"
  sed -i "s#^d-i mirror/http/hostname string .*#d-i mirror/http/hostname string ${HOST}#" "$PAYLOAD/preseed.cfg"
  sed -i "s#^d-i mirror/http/directory string .*#d-i mirror/http/directory string ${DIR%/}#" "$PAYLOAD/preseed.cfg"
  echo "$MIRROR" > "$PAYLOAD/mirror.txt"
fi

# 允许注入自定义仓库/分支
cat >"$PAYLOAD/echo-env.sh" <<EOF
# 由 build-iso.sh 生成;首次开机脚本会 source 它
ECHO_OS_REPO="${ECHO_OS_REPO:-https://github.com/dengdenghua/octopus-os.git}"
ECHO_OS_BRANCH="${ECHO_OS_BRANCH:-p3-fnos}"
DEBIAN_MIRROR="${MIRROR:-https://deb.debian.org/debian}"
EOF

# ── 4. 改引导配置:指向我们的 preseed ─────────────────────
log "改写引导参数"
APPEND_ARGS="auto=true preseed/file=/cdrom/echo-os/preseed.cfg"

# BIOS: isolinux。给所有含 vmlinuz 的 append 行追加参数。
for f in "$WORK/iso"/isolinux/*.cfg "$WORK/iso"/isolinux/*.txt; do
  [ -f "$f" ] || continue
  # 幂等:已带 preseed/file 就跳过
  grep -q 'preseed/file' "$f" && continue
  sed -i "s|^\([[:space:]]*append[[:space:]].*\)$|\1 $APPEND_ARGS|" "$f"
done

# UEFI: grub。给 linux 行追加参数。
if [ -f "$WORK/iso/boot/grub/grub.cfg" ]; then
  grep -q 'preseed/file' "$WORK/iso/boot/grub/grub.cfg" \
    || sed -i "s|^\([[:space:]]*linux[[:space:]].*\)$|\1 $APPEND_ARGS|" "$WORK/iso/boot/grub/grub.cfg"
fi

# ── 5. 重算 md5sum.txt ────────────────────────────────────
log "重算 md5sum.txt"
if [ -f "$WORK/iso/md5sum.txt" ]; then
  ( cd "$WORK/iso" && find . -follow -type f ! -name md5sum.txt -print0 \
      | xargs -0 md5sum > md5sum.txt )
fi

# ── 6. 重新打包 ───────────────────────────────────────────
log "打包 ISO → $OUT_ISO"
mkdir -p "$(dirname "$OUT_ISO")"
rm -f "$OUT_ISO"

MBR=""
for cand in /usr/lib/ISOLINUX/isohdpfx.bin \
            /usr/share/syslinux/isohdpfx.bin \
            "$WORK/iso/isolinux/isohdpfx.bin"; do
  [ -f "$cand" ] && { MBR="$cand"; break; }
done
[ -n "$MBR" ] || die "找不到 isohdpfx.bin(apt install isolinux),无法生成可启动 hybrid ISO"

xorriso -as mkisofs \
  -r -V 'ECHO_OS' -o "$OUT_ISO" \
  -J -joliet-long -cache-inodes \
  -isohybrid-mbr "$MBR" \
  -b isolinux/isolinux.bin -c isolinux/boot.cat \
  -boot-load-size 4 -boot-info-table -no-emul-boot \
  -eltorito-alt-boot -e boot/grub/efi.img \
  -no-emul-boot -isohybrid-gpt-basdat \
  "$WORK/iso" 2>&1 | tail -3

log "✓ 完成:$OUT_ISO  ($(du -h "$OUT_ISO" | cut -f1))"
cat <<EOF

写入 U 盘:
  sudo dd if="$OUT_ISO" of=/dev/sdX bs=4M status=progress && sync

装机流程:
  1. U 盘启动 → 出现 Echo OS 欢迎界面(echo-install)
  2. 选系统盘 → 确认清除 → 主机名 → 管理员密码
  3. 自动完成分区/装 Debian/装 grub → 重启
  4. 首次开机自动跑 setup-base.sh(ZFS/Samba/Docker/echo),约 10~20 分钟
     journalctl -u echo-firstboot -f
  5. 打开 http://<设备IP> 或 http://<主机名>.local
EOF
