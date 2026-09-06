#!/usr/bin/env bash
# Build the browser payload from this exact Git tree for ISO embedding.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
FRONTEND_DIR="$REPO_ROOT/frontend"

command -v git >/dev/null 2>&1 || { echo "缺少 git" >&2; exit 1; }
command -v pnpm >/dev/null 2>&1 || { echo "缺少 pnpm" >&2; exit 1; }
[ -z "$(git -C "$REPO_ROOT" status --porcelain --untracked-files=all)" ] || {
  echo "Web 发布载荷只能从干净 Git 工作树构建" >&2
  exit 1
}
SOURCE_TREE="$(git -C "$REPO_ROOT" rev-parse 'HEAD^{tree}')"

(
  cd "$FRONTEND_DIR"
  export CI=true
  export ELECTRON_SKIP_BINARY_DOWNLOAD=1
  pnpm install --frozen-lockfile
  pnpm build
)

[ -f "$FRONTEND_DIR/dist/index.html" ] || {
  echo "前端构建未生成 dist/index.html" >&2
  exit 1
}
[ -z "$(git -C "$REPO_ROOT" status --porcelain --untracked-files=all)" ] || {
  echo "前端构建修改了受版本控制的源码" >&2
  exit 1
}
printf '%s\n' "$SOURCE_TREE" >"$FRONTEND_DIR/dist/.echo-source-tree"
printf 'web-dist=%s\nsource-tree=%s\n' "$FRONTEND_DIR/dist" "$SOURCE_TREE"
