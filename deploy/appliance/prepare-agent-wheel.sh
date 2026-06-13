#!/usr/bin/env bash
# 预构建 octopus-agent wheel,投喂给 Docker 构建(P1 去 fork:os 不再 fork runtime/)。
#
# 为何需要:os 的 Dockerfile 不再 COPY 本地 runtime/,改装 pinned agent 依赖。
# agent 仓库私有,Docker 内 `pip install git+...` 需 token;这里改为构建前在宿主
# 把 agent wheel 备好放进 agent-dist/,Dockerfile 用 --find-links 本地安装,免 token。
#
# 用法:在 `docker compose build`(或 docker build)之前运行本脚本一次:
#   ./deploy/appliance/prepare-agent-wheel.sh
# agent 源码默认取 sibling 路径 ../octopus-agent;否则用 OCTOPUS_AGENT_SRC 指定:
#   OCTOPUS_AGENT_SRC=/path/to/octopus-agent ./deploy/appliance/prepare-agent-wheel.sh
#
# 版本钉:产出的 wheel 即当前 agent 工作树版本。要钉特定提交,先在 agent 仓库
# `git checkout <sha>` 再跑本脚本(对齐 pyproject 里 @fedd0c5 那一钉)。
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
OS_ROOT="$(cd "$HERE/../.." && pwd)"
AGENT_SRC="${OCTOPUS_AGENT_SRC:-$OS_ROOT/../octopus-agent}"
DIST="$HERE/agent-dist"

if [ ! -f "$AGENT_SRC/pyproject.toml" ]; then
  echo "✗ 未找到 agent 源码:$AGENT_SRC" >&2
  echo "  设 OCTOPUS_AGENT_SRC 指向 octopus-agent 仓库根目录后重试。" >&2
  exit 1
fi

rm -rf "$DIST" && mkdir -p "$DIST"
echo "构建 agent wheel:$AGENT_SRC → $DIST"

if command -v uv >/dev/null 2>&1; then
  uv build --wheel --out-dir "$DIST" "$AGENT_SRC"
elif python -m build --version >/dev/null 2>&1; then
  python -m build --wheel --outdir "$DIST" "$AGENT_SRC"
else
  python -m pip wheel --no-deps --wheel-dir "$DIST" "$AGENT_SRC"
fi

echo "✓ 完成:"
ls -1 "$DIST"/*.whl
