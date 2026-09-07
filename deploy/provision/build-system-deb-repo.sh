#!/usr/bin/env bash
# Build a self-contained flat APT repository for headless NAS firstboot.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PACKAGE_LIST="$SCRIPT_DIR/system-packages-nas.txt"
INPUT_ISO=""
OUT_DIR="$REPO_ROOT/dist/system-debs"
DEBIAN_MIRROR="${DEBIAN_MIRROR:-https://deb.debian.org/debian}"
SECURITY_MIRROR="${SECURITY_MIRROR:-https://security.debian.org/debian-security}"
DOCKER_MIRROR="${DOCKER_MIRROR:-https://download.docker.com/linux/debian}"
DOCKER_KEY_URL="${DOCKER_KEY_URL:-https://download.docker.com/linux/debian/gpg}"
DOCKER_KEY_FINGERPRINT=9DC858229FC7DD38854AE2D88D81803C0EBFCD88

die() { printf '✗ %s\n' "$*" >&2; exit 1; }

while [ "$#" -gt 0 ]; do
  case "$1" in
    --iso) INPUT_ISO="$2"; shift 2 ;;
    --out) OUT_DIR="$2"; shift 2 ;;
    --debian-mirror) DEBIAN_MIRROR="$2"; shift 2 ;;
    --security-mirror) SECURITY_MIRROR="$2"; shift 2 ;;
    --docker-mirror) DOCKER_MIRROR="$2"; shift 2 ;;
    -h|--help)
      printf 'Usage: %s --iso <debian-netinst.iso> [--out <directory>]\n' "$0"
      exit 0
      ;;
    *) die "未知参数: $1" ;;
  esac
done

[ -n "$INPUT_ISO" ] || die "必须用 --iso 指定 Debian netinst ISO"
[ -f "$INPUT_ISO" ] || die "ISO 不存在: $INPUT_ISO"
[ -f "$PACKAGE_LIST" ] || die "系统包清单不存在: $PACKAGE_LIST"
[ "$(uname -s)" = Linux ] || die "必须在 Linux amd64 上构建"
for tool in apt-get curl dpkg-deb dpkg-scanpackages git gpg gzip sha256sum xorriso; do
  command -v "$tool" >/dev/null 2>&1 || die "缺少构建工具: $tool"
done
[ "$(dpkg --print-architecture)" = amd64 ] || die "正式系统包仓只接受 amd64 构建机"
[ -z "$(git -C "$REPO_ROOT" status --porcelain --untracked-files=all)" ] \
  || die "系统包发布载荷只能从干净 Git 工作树构建"

SOURCE_TREE="$(git -C "$REPO_ROOT" rev-parse 'HEAD^{tree}')"
mapfile -t KERNEL_DEBS < <(
  xorriso -indev "$INPUT_ISO" \
    -find /pool/main/l/linux-signed-amd64 -type f \
    -name 'linux-image-*-amd64_*.deb' -exec lsdl 2>/dev/null \
    | sed -nE "s#^.*'(/pool/[^']+)'\$#\1#p"
)
[ "${#KERNEL_DEBS[@]}" -eq 1 ] \
  || die "netinst ISO 必须且只能包含一个 amd64 内核包"
KERNEL_RELEASE="$(basename "${KERNEL_DEBS[0]}" \
  | sed -nE 's/^linux-image-([^_]+)_[^_]+_amd64\.deb$/\1/p')"
case "$KERNEL_RELEASE" in
  [0-9]*-amd64) ;;
  *) die "无法从 netinst ISO 解析目标内核版本" ;;
esac

mapfile -t PACKAGES < <(sed -E 's/[[:space:]]*#.*$//; /^[[:space:]]*$/d' "$PACKAGE_LIST")
[ "${#PACKAGES[@]}" -gt 0 ] || die "系统包清单为空"
PACKAGES+=("linux-headers-$KERNEL_RELEASE")
if [ "$(printf '%s\n' "${PACKAGES[@]}" | sort -u | wc -l)" -ne "${#PACKAGES[@]}" ]; then
  die "系统包清单包含重复项"
fi

OUT_PARENT="$(dirname "$OUT_DIR")"
mkdir -p "$OUT_PARENT"
OUT_PARENT="$(cd "$OUT_PARENT" && pwd -P)"
OUT_DIR="$OUT_PARENT/$(basename "$OUT_DIR")"
STAGED="$(mktemp -d "$OUT_PARENT/.system-debs.XXXXXX")"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/echo-system-debs.XXXXXX")"
BACKUP=""
cleanup() {
  rm -rf "$WORK" "$STAGED"
  if [ -n "$BACKUP" ] && [ -e "$BACKUP" ] && [ ! -e "$OUT_DIR" ]; then
    mv "$BACKUP" "$OUT_DIR"
  fi
}
trap cleanup EXIT

mkdir -p "$WORK/lists/partial" "$WORK/cache" "$STAGED/partial"
touch "$WORK/status"
curl -fsSL --retry 3 --connect-timeout 30 --max-time 120 \
  "$DOCKER_KEY_URL" -o "$WORK/docker.asc"
mapfile -t DOCKER_FINGERPRINTS < <(
  gpg --batch --show-keys --with-colons "$WORK/docker.asc" 2>/dev/null \
    | awk -F: '$1 == "fpr" {print $10}'
)
printf '%s\n' "${DOCKER_FINGERPRINTS[@]}" | grep -qxF "$DOCKER_KEY_FINGERPRINT" \
  || die "Docker 仓库签名密钥指纹不匹配"

cat >"$WORK/sources.list" <<EOF
deb [arch=amd64] $DEBIAN_MIRROR trixie main contrib non-free non-free-firmware
deb [arch=amd64] $DEBIAN_MIRROR trixie-updates main contrib non-free non-free-firmware
deb [arch=amd64] $SECURITY_MIRROR trixie-security main contrib non-free non-free-firmware
deb [arch=amd64 signed-by=$WORK/docker.asc] $DOCKER_MIRROR trixie stable
EOF

APT_OPTIONS=(
  -o Debug::NoLocking=1
  -o APT::Architecture=amd64
  -o Dir::Etc::sourcelist="$WORK/sources.list"
  -o Dir::Etc::sourceparts=-
  -o Dir::State::lists="$WORK/lists"
  -o Dir::State::status="$WORK/status"
  -o Dir::Cache="$WORK/cache"
  -o Dir::Cache::archives="$STAGED"
  -o Acquire::Retries=3
  -o Acquire::http::Timeout=30
  -o Acquire::https::Timeout=30
)
apt-get "${APT_OPTIONS[@]}" -o APT::Update::Error-Mode=any update
apt-get "${APT_OPTIONS[@]}" --download-only -y --no-install-recommends \
  install "${PACKAGES[@]}"
rm -rf "$STAGED/partial" "$STAGED/lock"

mapfile -t DEBS < <(find "$STAGED" -maxdepth 1 -type f -name '*.deb' -printf '%f\n' | sort)
[ "${#DEBS[@]}" -gt "${#PACKAGES[@]}" ] \
  || die "系统包仓没有解析出完整依赖闭包"
for deb in "${DEBS[@]}"; do
  architecture="$(dpkg-deb -f "$STAGED/$deb" Architecture)"
  case "$architecture" in amd64|all) ;; *) die "不支持的 deb 架构: $deb ($architecture)" ;; esac
done

(
  cd "$STAGED"
  dpkg-scanpackages --multiversion . /dev/null >Packages
  gzip -n -9 -c Packages >Packages.gz
  : >packages.lock
  for deb in "${DEBS[@]}"; do
    printf '%s\t%s\t%s\t%s\t%s\n' \
      "$(dpkg-deb -f "$deb" Package)" \
      "$(dpkg-deb -f "$deb" Version)" \
      "$(dpkg-deb -f "$deb" Architecture)" \
      "$(sha256sum "$deb" | awk '{print $1}')" \
      "$deb" >>packages.lock
  done
  sort -o packages.lock packages.lock
  printf '%s\n' "$SOURCE_TREE" >.echo-source-tree
  printf 'debian-trixie amd64 kernel-%s\n' "$KERNEL_RELEASE" >.echo-system-runtime
  find . -maxdepth 1 -type f ! -name SHA256SUMS -printf '%P\0' \
    | sort -z | xargs -0 sha256sum >SHA256SUMS
  sha256sum -c SHA256SUMS
)

[ -z "$(git -C "$REPO_ROOT" status --porcelain --untracked-files=all)" ] \
  || die "系统包仓构建修改了受版本控制的源码"
if [ -e "$OUT_DIR" ]; then
  BACKUP="$OUT_PARENT/.system-debs.backup.$$"
  [ ! -e "$BACKUP" ] || die "临时备份路径已存在: $BACKUP"
  mv "$OUT_DIR" "$BACKUP"
fi
mv "$STAGED" "$OUT_DIR"
STAGED=""
if [ -n "$BACKUP" ]; then
  rm -rf "$BACKUP"
  BACKUP=""
fi
printf 'system-deb-repo=%s\nsource-tree=%s\nkernel=%s\ndeb-count=%s\n' \
  "$OUT_DIR" "$SOURCE_TREE" "$KERNEL_RELEASE" "${#DEBS[@]}"
