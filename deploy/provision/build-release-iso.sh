#!/usr/bin/env bash
# Build the source-bound offline payloads for the compatibility d-i NAS ISO.
# Production releases use packaging/image + deploy/installer and add dm-verity,
# Secure Boot, encrypted mutable partitions, A/B updates and a GPG signature.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

INPUT_ISO=""
INPUT_ISO_SHA256=""
OUT_ISO="$REPO_ROOT/dist/echo-os-release.iso"
DEBIAN_MIRROR=""
SECURITY_MIRROR=""
DOCKER_MIRROR=""

die() { printf '✗ %s\n' "$*" >&2; exit 1; }

while [ "$#" -gt 0 ]; do
  case "$1" in
    --iso) INPUT_ISO="$2"; shift 2 ;;
    --iso-sha256) INPUT_ISO_SHA256="$2"; shift 2 ;;
    --out) OUT_ISO="$2"; shift 2 ;;
    --debian-mirror) DEBIAN_MIRROR="$2"; shift 2 ;;
    --security-mirror) SECURITY_MIRROR="$2"; shift 2 ;;
    --docker-mirror) DOCKER_MIRROR="$2"; shift 2 ;;
    -h|--help)
      cat <<'EOF'
Usage: build-release-iso.sh --iso <debian-netinst.iso> \
  --iso-sha256 <official-sha256> [--out <echo-os.iso>]

Optional build-time mirrors:
  --debian-mirror <url> --security-mirror <url> --docker-mirror <url>

The resulting installer and first boot do not use these mirrors. They are used
only while resolving the source-bound local system package repository.
EOF
      exit 0
      ;;
    *) die "未知参数: $1" ;;
  esac
done

[ "$(uname -s)" = Linux ] && [ "$(uname -m)" = x86_64 ] \
  || die "正式 amd64 NAS 镜像只接受 Linux x86_64 构建机"
[ -r /etc/os-release ] || die "无法确认构建机发行版"
# shellcheck disable=SC1091
. /etc/os-release
[ "${ID:-}" = debian ] && [ "${VERSION_CODENAME:-}" = trixie ] \
  || die "正式镜像必须在 Debian 13 (trixie) 构建"
command -v python3 >/dev/null 2>&1 || die "缺少构建工具: python3"
PYTHON_RUNTIME="$(python3 -c \
  'import platform,sys; print(f"{sys.implementation.cache_tag} {platform.system().lower()} {platform.machine().lower()}")')"
[ "$PYTHON_RUNTIME" = "cpython-313 linux x86_64" ] \
  || die "正式镜像构建机必须提供 Debian 13 CPython 3.13"
command -v sha256sum >/dev/null 2>&1 || die "缺少构建工具: sha256sum"
[ -n "$INPUT_ISO" ] && [ -f "$INPUT_ISO" ] \
  || die "必须用 --iso 指定本地 Debian netinst ISO"
case "$INPUT_ISO_SHA256" in
  *[!0-9a-fA-F]*|'') die "必须提供官方 64 位 --iso-sha256" ;;
esac
[ "${#INPUT_ISO_SHA256}" -eq 64 ] || die "必须提供官方 64 位 --iso-sha256"
ACTUAL_INPUT_ISO_SHA256="$(sha256sum "$INPUT_ISO" | awk '{print $1}')"
[ "$ACTUAL_INPUT_ISO_SHA256" = "${INPUT_ISO_SHA256,,}" ] \
  || die "Debian 基础 ISO SHA-256 不匹配"
[ -z "$(git -C "$REPO_ROOT" status --porcelain --untracked-files=all)" ] \
  || die "正式 release 只能从干净 Git 工作树构建"

"$SCRIPT_DIR/build-web-dist.sh"
"$SCRIPT_DIR/build-python-wheelhouse.sh"
"$SCRIPT_DIR/build-codex-bundle.sh"

SYSTEM_REPO_ARGS=(--iso "$INPUT_ISO")
[ -z "$DEBIAN_MIRROR" ] \
  || SYSTEM_REPO_ARGS+=(--debian-mirror "$DEBIAN_MIRROR")
[ -z "$SECURITY_MIRROR" ] \
  || SYSTEM_REPO_ARGS+=(--security-mirror "$SECURITY_MIRROR")
[ -z "$DOCKER_MIRROR" ] \
  || SYSTEM_REPO_ARGS+=(--docker-mirror "$DOCKER_MIRROR")
"$SCRIPT_DIR/build-system-deb-repo.sh" "${SYSTEM_REPO_ARGS[@]}"

"$SCRIPT_DIR/build-iso.sh" \
  --release \
  --profile nas \
  --iso "$INPUT_ISO" \
  --iso-sha256 "$INPUT_ISO_SHA256" \
  --web-dist "$REPO_ROOT/frontend/dist" \
  --python-wheelhouse "$REPO_ROOT/dist/python-wheelhouse" \
  --codex-bundle "$REPO_ROOT/dist/codex-bundle" \
  --system-deb-repo "$REPO_ROOT/dist/system-debs" \
  --out "$OUT_ISO"

bash "$SCRIPT_DIR/verify-release-iso.sh" \
  --iso "$OUT_ISO" \
  --expected-sha256 "$(awk '{print $1}' "$OUT_ISO.sha256")"
printf 'release-iso=%s\nrelease-sha256=%s\n' \
  "$OUT_ISO" "$(awk '{print $1}' "$OUT_ISO.sha256")"
