#!/usr/bin/env bash
# Re-open a finished strict ISO and verify its identity and embedded payloads.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ISO=""
EXPECTED_SHA256=""
WORK=""

die() { printf '✗ %s\n' "$*" >&2; exit 1; }

while [ "$#" -gt 0 ]; do
  case "$1" in
    --iso) ISO="$2"; shift 2 ;;
    --expected-sha256) EXPECTED_SHA256="$2"; shift 2 ;;
    -h|--help)
      printf 'Usage: %s --iso <echo-release.iso> [--expected-sha256 <digest>]\n' "$0"
      exit 0
      ;;
    *) die "未知参数: $1" ;;
  esac
done

for tool in awk df git grep md5sum mktemp python3 sha256sum stat xorriso; do
  command -v "$tool" >/dev/null 2>&1 || die "缺少验证工具: $tool"
done
[ -n "$ISO" ] && [ -f "$ISO" ] && [ ! -L "$ISO" ] \
  || die "必须用 --iso 指定常规镜像文件"
ISO="$(cd "$(dirname "$ISO")" && pwd -P)/$(basename "$ISO")"
ISO_DIR="$(dirname "$ISO")"
ISO_NAME="$(basename "$ISO")"

if [ -z "$EXPECTED_SHA256" ]; then
  CHECKSUM_FILE="$ISO.sha256"
  [ -f "$CHECKSUM_FILE" ] && [ ! -L "$CHECKSUM_FILE" ] \
    || die "缺少同目录 ISO SHA-256 清单"
  EXPECTED_SHA256="$(awk -v name="$ISO_NAME" '$2 == name {print $1}' "$CHECKSUM_FILE")"
fi
case "$EXPECTED_SHA256" in *[!0-9a-fA-F]*|'') die "期望 ISO SHA-256 无效" ;; esac
[ "${#EXPECTED_SHA256}" -eq 64 ] || die "期望 ISO SHA-256 无效"
EXPECTED_SHA256="${EXPECTED_SHA256,,}"
ACTUAL_SHA256="$(sha256sum "$ISO" | awk '{print $1}')"
[ "$ACTUAL_SHA256" = "$EXPECTED_SHA256" ] || die "ISO SHA-256 不匹配"

WORK="$(mktemp -d "${TMPDIR:-/var/tmp}/echo-release-verify.XXXXXX")"
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT HUP INT TERM
AVAILABLE_KIB="$(LC_ALL=C df -Pk "$WORK" | awk 'END {print $4}')"
ISO_BYTES="$(stat -c %s "$ISO")"
case "$AVAILABLE_KIB:$ISO_BYTES" in
  *[!0-9:]*) die "无法确定验证工作区容量" ;;
esac
REQUIRED_KIB=$((ISO_BYTES / 1024 + 1048576))
[ "$AVAILABLE_KIB" -ge "$REQUIRED_KIB" ] \
  || die "验证工作区空间不足；请设置 TMPDIR 指向更大的磁盘"

BOOT_REPORT="$(xorriso -indev "$ISO" -report_el_torito plain 2>/dev/null)"
printf '%s\n' "$BOOT_REPORT" | grep -Eq 'El Torito boot img.*BIOS.*y' \
  || die "ISO 缺少可引导 BIOS El Torito 入口"
printf '%s\n' "$BOOT_REPORT" | grep -Eq 'El Torito boot img.*UEFI.*y' \
  || die "ISO 缺少可引导 UEFI El Torito 入口"

xorriso -osirrox on -indev "$ISO" -extract / "$WORK/iso-root" >/dev/null 2>&1 \
  || die "无法提取 ISO 文件系统"
[ -f "$WORK/iso-root/md5sum.txt" ] && [ ! -L "$WORK/iso-root/md5sum.txt" ] \
  || die "ISO 缺少常规 md5sum.txt"
(cd "$WORK/iso-root" && md5sum --quiet -c md5sum.txt) \
  || die "ISO 文件系统完整性校验失败"
PAYLOAD_ROOT="$WORK/iso-root/echo"
MANIFEST_REPORT="$(
  python3 "$SCRIPT_DIR/release-manifest.py" verify-directory --root "$PAYLOAD_ROOT"
)"
printf '%s\n' "$MANIFEST_REPORT"
MANIFEST_TREE="$(printf '%s\n' "$MANIFEST_REPORT" \
  | awk '{for (i = 1; i <= NF; i++) if ($i ~ /^source-tree=/) {sub(/^source-tree=/, "", $i); print $i}}')"
[ -n "$MANIFEST_TREE" ] || die "release manifest 未返回源码 tree 身份"

mkdir "$WORK/source-check"
git -C "$WORK/source-check" init -q
git -C "$WORK/source-check" bundle verify "$PAYLOAD_ROOT/echo-source.bundle" >/dev/null 2>&1 \
  || die "ISO 内源码 bundle 无法通过 Git 完整性验证"
mapfile -t BUNDLE_REFS < <(
  git bundle list-heads "$PAYLOAD_ROOT/echo-source.bundle" | awk '{print $2}'
)
[ "${#BUNDLE_REFS[@]}" -eq 1 ] \
  || die "ISO 内源码 bundle 必须且只能发布一个 ref"
case "${BUNDLE_REFS[0]}" in
  refs/echo-image/snapshot-*) ;;
  *) die "ISO 内源码 bundle ref 身份无效" ;;
esac
git -C "$WORK/source-check" fetch -q \
  "$PAYLOAD_ROOT/echo-source.bundle" "${BUNDLE_REFS[0]}" \
  || die "ISO 内源码 bundle 无法提取发布 ref"
BUNDLE_TREE="$(git -C "$WORK/source-check" rev-parse 'FETCH_HEAD^{tree}')"
[ "$BUNDLE_TREE" = "$MANIFEST_TREE" ] \
  || die "ISO 内源码 bundle tree 与 release manifest 不一致"

printf 'release-iso=verified\niso-sha256=%s\n' "$ACTUAL_SHA256"
