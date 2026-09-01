#!/usr/bin/env bash
# Compatibility entrypoint for older Echo OS bring-up instructions.
# Target C retired the Cage/kiosk installer in favor of the KWin desktop.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
TARGET="$HERE/../desktop-session/setup-desktop-session.sh"

[[ -x "$TARGET" ]] || {
  echo "当前 KWin 桌面安装器缺失或不可执行: $TARGET" >&2
  exit 1
}

echo "提示: Cage kiosk 安装路线已经退役，转入目标 C 的 KWin 通用桌面安装器。" >&2
exec "$TARGET" "$@"
