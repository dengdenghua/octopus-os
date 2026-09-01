#!/usr/bin/env bash
# cage 全屏托起它 → Electron 会话 shell(加载构建好的桌面,连本机后端 :8000)。
# 由 echo-shell.service 经 `cage -- 此脚本` 调起;勿直接当登录 shell 跑。
set -euo pipefail

OS_DIR="${ECHO_OS_DIR:-/opt/echo-os}"
APP_DIR="$OS_DIR/frontend"

export ECHO_NATIVE_SHELL=1
export ECHO_BACKEND_URL="${ECHO_BACKEND_URL:-http://127.0.0.1:8000}"

cd "$APP_DIR"

# Electron on Wayland(cage 提供 Wayland)+ 设备上常见的最小化标志。
# --no-sandbox 仅在内核 userns 受限的精简镜像里需要;真机可去掉。
EXTRA=()
# 调试/远程截图:设 ECHO_ELECTRON_DEBUG_PORT 开 CDP(如 9222),
# 宿主机可 curl http://127.0.0.1:9222/json 看页面,或走 screencap。
if [ -n "${ECHO_ELECTRON_DEBUG_PORT:-}" ]; then
  EXTRA+=(--remote-debugging-port="$ECHO_ELECTRON_DEBUG_PORT")
  # Chromium 137+ 默认拒绝外部 origin 的 CDP WebSocket,截图工具会 403
  # (VM 实测)。调试模式下放开,仅监听 127.0.0.1。
  EXTRA+=(--remote-allow-origins='*')
fi
exec npx --no-install electron electron/main.cjs \
  --ozone-platform-hint=auto \
  --enable-features=UseOzonePlatform \
  --disable-gpu-compositing \
  "${EXTRA[@]}"
