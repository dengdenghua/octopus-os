#!/usr/bin/env bash
# Build the pinned native Codex CLI payload for offline firstboot installation.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
OUT_DIR="${1:-$REPO_ROOT/dist/codex-bundle}"
PREPARE_SCRIPT="$REPO_ROOT/extras/desktop/prepare-codex-linux.cjs"
PREPARED_DIR="$REPO_ROOT/extras/desktop/build/codex"

die() { printf '✗ %s\n' "$*" >&2; exit 1; }

for tool in git node pnpm sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || die "缺少构建工具: $tool"
done
[ "$(uname -s)" = Linux ] && [ "$(uname -m)" = x86_64 ] \
  || die "正式 Codex 载荷只接受 Linux x86_64 构建机"
[ -f "$PREPARE_SCRIPT" ] || die "Codex 准备脚本不存在"
[ -z "$(git -C "$REPO_ROOT" status --porcelain --untracked-files=all)" ] \
  || die "Codex 发布载荷只能从干净 Git 工作树构建"

SOURCE_TREE="$(git -C "$REPO_ROOT" rev-parse 'HEAD^{tree}')"
CODEX_VERSION="$(sed -nE \
  's/^[[:space:]]*"@openai\/codex":[[:space:]]*"([^"]+)",?[[:space:]]*$/\1/p' \
  "$REPO_ROOT/frontend/package.json" | head -1)"
case "$CODEX_VERSION" in
  ""|*[!0-9A-Za-z.+-]*) die "无法解析 @openai/codex 版本" ;;
esac

(
  cd "$REPO_ROOT/frontend"
  export CI=true
  export ELECTRON_SKIP_BINARY_DOWNLOAD=1
  pnpm install --frozen-lockfile
)
node "$PREPARE_SCRIPT"
[ -d "$PREPARED_DIR" ] || die "Codex 准备脚本未生成载荷"

OUT_PARENT="$(dirname "$OUT_DIR")"
mkdir -p "$OUT_PARENT"
OUT_PARENT="$(cd "$OUT_PARENT" && pwd -P)"
OUT_DIR="$OUT_PARENT/$(basename "$OUT_DIR")"
STAGED="$(mktemp -d "$OUT_PARENT/.codex-bundle.XXXXXX")"
BACKUP=""
cleanup() {
  rm -rf "$STAGED"
  if [ -n "$BACKUP" ] && [ -e "$BACKUP" ] && [ ! -e "$OUT_DIR" ]; then
    mv "$BACKUP" "$OUT_DIR"
  fi
}
trap cleanup EXIT

cp -a "$PREPARED_DIR/." "$STAGED/"
if find "$STAGED" \( -type l -o \( ! -type f -a ! -type d \) \) \
    -print -quit | grep -q .; then
  die "Codex 载荷只能包含常规文件和目录"
fi
for executable in \
  bin/codex bin/codex-code-mode-host codex-path/rg \
  codex-resources/zsh/bin/zsh codex-resources/bwrap; do
  [ -x "$STAGED/$executable" ] || die "Codex 载荷缺少可执行文件: $executable"
done
[ "$($STAGED/bin/codex --version)" = "codex-cli $CODEX_VERSION" ] \
  || die "Codex 二进制版本与 package.json 不一致"
printf '%s\n' "$SOURCE_TREE" >"$STAGED/.echo-source-tree"
printf 'codex-%s linux x86_64\n' "$CODEX_VERSION" >"$STAGED/.echo-codex-runtime"
(
  cd "$STAGED"
  find . -type f ! -name SHA256SUMS -printf '%P\0' \
    | sort -z | xargs -0 sha256sum >SHA256SUMS
  sha256sum -c SHA256SUMS >/dev/null
)

[ -z "$(git -C "$REPO_ROOT" status --porcelain --untracked-files=all)" ] \
  || die "Codex 载荷构建修改了受版本控制的源码"
if [ -e "$OUT_DIR" ]; then
  BACKUP="$OUT_PARENT/.codex-bundle.backup.$$"
  [ ! -e "$BACKUP" ] || die "临时备份路径已存在: $BACKUP"
  mv "$OUT_DIR" "$BACKUP"
fi
mv "$STAGED" "$OUT_DIR"
STAGED=""
if [ -n "$BACKUP" ]; then
  rm -rf "$BACKUP"
  BACKUP=""
fi
printf 'codex-bundle=%s\nsource-tree=%s\nruntime=codex-%s linux x86_64\n' \
  "$OUT_DIR" "$SOURCE_TREE" "$CODEX_VERSION"
