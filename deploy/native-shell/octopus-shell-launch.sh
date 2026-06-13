#!/usr/bin/env bash
# cage 全屏托起它 → Electron 会话 shell(加载构建好的桌面,连本机后端 :8000)。
# 由 octopus-shell.service 经 `cage -- 此脚本` 调起;勿直接当登录 shell 跑。
set -euo pipefail

OS_DIR="${OCTOPUS_OS_DIR:-/opt/octopus-os}"
APP_DIR="$OS_DIR/frontend"

export OCTOPUS_NATIVE_SHELL=1
export OCTOPUS_BACKEND_URL="${OCTOPUS_BACKEND_URL:-http://127.0.0.1:8000}"

cd "$APP_DIR"

# Electron on Wayland(cage 提供 Wayland)+ 设备上常见的最小化标志。
# --no-sandbox 仅在内核 userns 受限的精简镜像里需要;真机可去掉。
exec npx --no-install electron electron/main.cjs \
  --ozone-platform-hint=auto \
  --enable-features=UseOzonePlatform \
  --disable-gpu-compositing
