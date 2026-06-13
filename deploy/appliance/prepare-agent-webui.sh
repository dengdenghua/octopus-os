#!/usr/bin/env bash
# 预构建 agent 工作台前端(P2 同机 webui 投喂),产出投喂给 os 镜像。
#
# 为何需要:P2 去 fork 后,os 前端不再打包 agent 工作台;改由 os 后端在 /agent-ui/
# serve 一份**独立构建**的 agent webui(见 docs/P2_FRONTEND_DEFORK_PLAN.md)。
# 关键:必须以 base=/agent-ui/ 构建,assets 落在 /agent-ui/assets/,不与 os 自身
# webui 的 /assets/ 冲突;工作台是 hash 路由,故子路径 + hash 即可。
#
# 用法:在 `docker compose build` 之前与 prepare-agent-wheel.sh 一起跑:
#   ./deploy/appliance/prepare-agent-wheel.sh
#   ./deploy/appliance/prepare-agent-webui.sh
# agent 源码默认取 sibling ../octopus-agent;否则用 OCTOPUS_AGENT_SRC 指定。
# 产物落在 deploy/appliance/agent-webui/(.gitignore),Dockerfile COPY 它并把
# OCTOPUS_AGENT_WEBUI_DIST 指过去。
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
OS_ROOT="$(cd "$HERE/../.." && pwd)"
AGENT_SRC="${OCTOPUS_AGENT_SRC:-$OS_ROOT/../octopus-agent}"
FE="$AGENT_SRC/frontend"
DIST="$HERE/agent-webui"

if [ ! -f "$FE/package.json" ]; then
  echo "✗ 未找到 agent 前端:$FE" >&2
  echo "  设 OCTOPUS_AGENT_SRC 指向 octopus-agent 仓库根目录后重试。" >&2
  exit 1
fi

# 包管理器:优先 pnpm(仓库用它),回退 npm。
if command -v pnpm >/dev/null 2>&1; then PM="pnpm"; else PM="npm"; fi

if [ ! -d "$FE/node_modules" ]; then
  echo "安装 agent 前端依赖($PM)…"
  ( cd "$FE" && $PM install )
fi

rm -rf "$DIST"
echo "构建 agent webui(base=/agent-ui/):$FE → $DIST"
# vite build 支持 --base / --outDir;以子路径构建,产物投喂 os 后端。
( cd "$FE" && $PM exec vite build --base=/agent-ui/ --outDir "$DIST" --emptyOutDir )

if [ ! -f "$DIST/index.html" ]; then
  echo "✗ 构建未产出 index.html" >&2
  exit 1
fi
echo "✓ 完成:$DIST(index.html + assets)"
ls "$DIST" | head
