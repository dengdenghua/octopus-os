#!/usr/bin/env bash
# 组装 Echo OS 装机 ISO。
#
# 思路:拿 Debian 13 (trixie) 官方 netinst 做底,往里塞一个 /echo/ 载荷目录,
# 再给上游 initrd 追加只含 preseed + TUI 的独立 cpio.gz 段。我们不修改
# debian-installer 内部脚本；仍通过官方 preseed/early_command 钩子启动 TUI。
# 必须把 preseed 放进 initrd，因为 d-i 读取本地预置文件时 /cdrom 尚未挂载。
#
# 产物:可刻录 U 盘(dd)/ 可挂 VM 的 hybrid ISO。
#
# 用法:
#   ./build-iso.sh [--profile nas|desktop] [--iso <debian-netinst.iso>] [--mirror <url>] [--out <file>]
#   ./build-iso.sh --iso ~/debian-13.1.0-amd64-netinst.iso --mirror https://mirrors.ustc.edu.cn/debian
#
# 依赖:xorriso、cpio、gzip(apt install xorriso cpio gzip)。仅支持 Linux。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

DEBIAN_ISO_URL="${DEBIAN_ISO_URL:-https://cdimage.debian.org/debian-cd/current/amd64/iso-cd/}"
INPUT_ISO=""
MIRROR=""
OUT_ISO="$REPO_ROOT/dist/echo-os.iso"
INSTALL_PROFILE="${ECHO_INSTALL_PROFILE:-nas}"
WORK=""
SNAPSHOT_REF=""

log()  { printf '\033[1;34m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m  ! %s\033[0m\n' "$*" >&2; }
die()  { printf '\033[1;31m  ✗ %s\033[0m\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --iso)    INPUT_ISO="$2"; shift 2 ;;
    --mirror) MIRROR="$2";    shift 2 ;;
    --out)    OUT_ISO="$2";   shift 2 ;;
    --profile) INSTALL_PROFILE="$2"; shift 2 ;;
    --url)    DEBIAN_ISO_URL="$2"; shift 2 ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) die "未知参数: $1" ;;
  esac
done

case "$INSTALL_PROFILE" in
  nas)     HDMI_SHELL="${ECHO_HDMI_SHELL:-off}" ;;
  desktop) HDMI_SHELL="${ECHO_HDMI_SHELL:-on}" ;;
  *) die "未知安装配置: $INSTALL_PROFILE (只接受 nas 或 desktop)" ;;
esac
DESKTOP_MODE="${ECHO_DESKTOP:-cage}"
case "$HDMI_SHELL" in off|on|auto) ;; *) die "ECHO_HDMI_SHELL 只接受 off/on/auto" ;; esac
case "$DESKTOP_MODE" in cage|kwin) ;; *) die "ECHO_DESKTOP 只接受 cage/kwin" ;; esac

[ "$(uname -s)" = "Linux" ] || die "必须在 Linux 上构建(需要 xorriso)"
command -v xorriso >/dev/null 2>&1 || die "缺少 xorriso: sudo apt install xorriso"
command -v cpio >/dev/null 2>&1 || die "缺少 cpio: sudo apt install cpio"
command -v gzip >/dev/null 2>&1 || die "缺少 gzip: sudo apt install gzip"

WORK="$(mktemp -d "${TMPDIR:-/tmp}/echo-iso.XXXXXX")"
cleanup() {
  if [ -n "$SNAPSHOT_REF" ]; then
    git update-ref -d "$SNAPSHOT_REF" 2>/dev/null || true
  fi
  rm -rf "$WORK"
}
trap cleanup EXIT
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
install -m0644 "$SCRIPT_DIR/installer/echo-install.templates" \
  "$PAYLOAD/echo-install.templates"
install -m0644 "$SCRIPT_DIR/installer/preseed.cfg"      "$PAYLOAD/preseed.cfg"
install -m0755 "$SCRIPT_DIR/base/setup-base.sh"         "$PAYLOAD/setup-base.sh"
# provision-lib.sh 必须随引导文件一起投放: setup-base.sh 在第 31 行就 source 它,
# 而它只随 overlay(clone 之后才解包)交付 —— 漏投会让首次开机直接 source 失败。
# self-heal 见 setup-base.sh(从 overlay 包单独取回)。
install -m0644 "$SCRIPT_DIR/base/provision-lib.sh"      "$PAYLOAD/provision-lib.sh"
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

# ── 3a. 生成精确、无需网络的源码快照 bundle ────────────
# 不能把“相对某个 os-main 的文件覆盖包”应用到移动的远端分支上：删除记录
# 会丢失，上游后来新增/修改的文件也会混进成品。这里创建一个无父提交，tree
# 与当前 HEAD 完全相同；bundle 因而只携带这一版所需对象，目标机还能保留
# 正常的 .git 仓库，后续可重新绑定发布远端。
SOURCE_COMMIT="$(git rev-parse HEAD)"
SOURCE_TREE="$(git rev-parse 'HEAD^{tree}')"
SNAPSHOT_REF="refs/echo-image/snapshot-$$"
SOURCE_BUNDLE_REF="$SNAPSHOT_REF"
SNAPSHOT_COMMIT="$({
  printf 'Echo OS installer snapshot\n\nsource-commit: %s\n' "$SOURCE_COMMIT"
} | git -c user.name='Echo OS Installer' \
        -c user.email='installer@echo-os.local' \
        -c commit.gpgSign=false commit-tree "$SOURCE_TREE")"
git update-ref "$SNAPSHOT_REF" "$SNAPSHOT_COMMIT"
git bundle create "$PAYLOAD/echo-source.bundle" "$SNAPSHOT_REF"
git bundle verify "$PAYLOAD/echo-source.bundle" >/dev/null
[ "$(git show -s --format=%T "$SNAPSHOT_COMMIT")" = "$SOURCE_TREE" ] \
  || die "源码快照 tree 校验失败"
git update-ref -d "$SNAPSHOT_REF"
SNAPSHOT_REF=""
log "已嵌入精确源码快照 $SOURCE_COMMIT"

# 允许注入自定义仓库/分支；bundle 是正式 ISO 的首选来源，仓库参数保留给
# 后续更新和不含 bundle 的旧版/VM 测试介质。
cat >"$PAYLOAD/echo-env.sh" <<EOF
# 由 build-iso.sh 生成;首次开机脚本会 source 它
ECHO_OS_REPO="${ECHO_OS_REPO:-https://github.com/dengdenghua/octopus-os.git}"
ECHO_OS_BRANCH="${ECHO_OS_BRANCH:-p3-provision}"
ECHO_OVERLAY="${ECHO_OVERLAY:-/opt/echo-os-overlay.tar.gz}"
ECHO_SOURCE_BUNDLE="/opt/echo-os-source.bundle"
ECHO_SOURCE_BUNDLE_REF="$SOURCE_BUNDLE_REF"
ECHO_SOURCE_TREE="$SOURCE_TREE"
ECHO_IMAGE_COMMIT="$SOURCE_COMMIT"
DEBIAN_MIRROR="${MIRROR:-https://deb.debian.org/debian}"
ECHO_INSTALL_PROFILE="$INSTALL_PROFILE"
ECHO_HDMI_SHELL="$HDMI_SHELL"
ECHO_DESKTOP="$DESKTOP_MODE"
EOF

# ── 3b. 注入最小 initrd 段 ───────────────────────────────
# Linux initramfs 支持连续的压缩 cpio 段；直接追加独立段可保留 Debian 原始
# initrd，不需要解包或 patch 上游启动脚本。late_command 执行时 CD 已挂载，
# 因此完整载荷仍由 /cdrom/echo-os 提供。
INITRD_ROOT="$WORK/initrd-root"
INITRD_SEGMENT="$WORK/echo-initrd-segment.gz"
mkdir -p "$INITRD_ROOT"
sed 's#/cdrom/echo-os/echo-install#/bin/sh /echo-install#' \
  "$PAYLOAD/preseed.cfg" >"$INITRD_ROOT/preseed.cfg"
install -m0755 "$PAYLOAD/echo-install" "$INITRD_ROOT/echo-install"
install -m0644 "$PAYLOAD/echo-install.templates" \
  "$INITRD_ROOT/echo-install.templates"
(
  cd "$INITRD_ROOT"
  find . -print0 | cpio --null -o --format=newc 2>/dev/null | gzip -n -9
) >"$INITRD_SEGMENT"

INITRD_COUNT=0
while IFS= read -r -d '' initrd; do
  cat "$INITRD_SEGMENT" >>"$initrd"
  INITRD_COUNT=$((INITRD_COUNT + 1))
done < <(find "$WORK/iso/install.amd" -type f -name initrd.gz -print0)
[ "$INITRD_COUNT" -gt 0 ] || die "ISO 内未找到 Debian Installer initrd"
log "已向 $INITRD_COUNT 个 installer initrd 追加 preseed/TUI"

# ── 4. 改引导配置:指向我们的 preseed ─────────────────────
log "改写引导参数"
# 光盘上的 preseed 要到介质挂载后才能读取；locale/keyboard 属于更早的
# installer 问题，必须同时放在内核命令行，否则仍会先掉进 Debian 语言页。
APPEND_ARGS="auto=true priority=critical locale=zh_CN.UTF-8 keyboard-configuration/xkb-keymap=us preseed/file=/preseed.cfg"

# Debian Installer 把 `---` 之后的参数留给已安装系统；自动安装参数必须放在
# 分隔符之前才会作用于当前安装器。没有分隔符的自定义条目则退化为行尾追加。
inject_installer_args() {
  local directive="$1" file="$2"
  sed -i -E \
    -e "/preseed\/file=/! { /^[[:space:]]*${directive}[[:space:]].*[[:space:]]---([[:space:]]|$)/ s|[[:space:]]---| $APPEND_ARGS ---|; }" \
    -e "/preseed\/file=/! { /^[[:space:]]*${directive}[[:space:]]/ s|$| $APPEND_ARGS|; }" \
    "$file"
}

# BIOS: isolinux。给所有 append 行注入参数。
for f in "$WORK/iso"/isolinux/*.cfg "$WORK/iso"/isolinux/*.txt; do
  [ -f "$f" ] || continue
  inject_installer_args append "$f"
done

# 上游语音条目自带全局 ontimeout，会在无键盘环境接管启动。NAS 装机盘改为
# 显示 5 秒品牌菜单后确定进入文本安装器，正好承载 echo-install 的 whiptail。
sed -i -E '/^[[:space:]]*ontimeout[[:space:]]/d' "$WORK/iso"/isolinux/*.cfg
sed -i -E \
  -e 's/^timeout[[:space:]].*/timeout 50/' \
  -e '/^include menu.cfg/i ontimeout install' \
  "$WORK/iso/isolinux/isolinux.cfg"
sed -i -E \
  -e 's/menu title .*Debian GNU\/Linux installer menu \(BIOS mode\)/menu title Echo OS installer (BIOS mode)/' \
  "$WORK/iso/isolinux/menu.cfg"
sed -i -E 's/menu label \^Install$/menu label ^Echo OS installer/' \
  "$WORK/iso/isolinux/txt.cfg"

# UEFI: grub。默认第二个文本安装入口，5 秒后启动。
if [ -f "$WORK/iso/boot/grub/grub.cfg" ]; then
  inject_installer_args linux "$WORK/iso/boot/grub/grub.cfg"
  sed -i '1i set timeout=5\nset default=1' "$WORK/iso/boot/grub/grub.cfg"
  sed -i "0,/'Install'/s//'Echo OS installer'/" "$WORK/iso/boot/grub/grub.cfg"
fi

# ── 5. 重算 md5sum.txt ────────────────────────────────────
log "重算 md5sum.txt"
if [ -f "$WORK/iso/md5sum.txt" ]; then
  # Debian ISO 含 `debian -> .` 兼容链接；跟随它会形成目录环并让 set -e
  # 中止构建。校验清单只需要介质上的真实文件，不遍历符号链接。
  ( cd "$WORK/iso" && find . -type f ! -name md5sum.txt -print0 \
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
