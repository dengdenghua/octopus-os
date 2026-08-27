#!/usr/bin/env bash
# Headless real-KWin smoke: two HiDPI virtual outputs and a native Wayland app.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CLIENT_SCRIPT="$REPO_ROOT/deploy/desktop-session/smoke-wayland-session-client.sh"
BRIDGE=/usr/lib/echo-os/echo-kwin-window-bridge

for command_name in dbus-run-session gdbus kwin_wayland wayland-info node python3 timeout; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "Wayland smoke dependency missing: $command_name" >&2
    exit 1
  }
done
[[ -x "$BRIDGE" && -x "$CLIENT_SCRIPT" ]] || {
  echo "Wayland smoke bridge/client is not installed or executable" >&2
  exit 1
}

if [[ -z "${DBUS_SESSION_BUS_ADDRESS:-}" && "${ECHO_WAYLAND_SMOKE_DBUS:-0}" != 1 ]]; then
  export ECHO_WAYLAND_SMOKE_DBUS=1
  exec dbus-run-session -- "$0" "$@"
fi

if [[ -n "${ECHO_SMOKE_LOG_DIR:-}" ]]; then
  LOG_DIR="$ECHO_SMOKE_LOG_DIR"
  mkdir -p "$LOG_DIR"
  REMOVE_LOG_DIR=0
else
  LOG_DIR="$(mktemp -d)"
  REMOVE_LOG_DIR=1
fi
export ECHO_SMOKE_LOG_DIR="$LOG_DIR"
export XDG_RUNTIME_DIR="$LOG_DIR/runtime"
mkdir -p "$XDG_RUNTIME_DIR/echo-os"
chmod 0700 "$XDG_RUNTIME_DIR" "$XDG_RUNTIME_DIR/echo-os"

cleanup() {
  local status="$1"
  trap - EXIT INT TERM
  [[ -n "${KWIN_BRIDGE_PID:-}" ]] && kill "$KWIN_BRIDGE_PID" 2>/dev/null || true
  wait 2>/dev/null || true
  if [[ "$status" -ne 0 ]]; then
    echo "Wayland session smoke failed. Logs:" >&2
    for log_file in "$LOG_DIR"/*.log; do
      [[ -f "$log_file" ]] || continue
      echo "--- ${log_file##*/}" >&2
      tail -160 "$log_file" >&2 || true
    done
  elif [[ "$REMOVE_LOG_DIR" -eq 1 ]]; then
    rm -rf -- "$LOG_DIR"
  fi
  exit "$status"
}
trap 'cleanup $?' EXIT INT TERM

BRIDGE_SOCKET="$XDG_RUNTIME_DIR/echo-os/kwin-window-bridge.sock"
"$BRIDGE" --socket "$BRIDGE_SOCKET" >"$LOG_DIR/kwin-bridge.log" 2>&1 &
KWIN_BRIDGE_PID=$!
for _attempt in $(seq 1 100); do
  [[ -S "$BRIDGE_SOCKET" ]] && break
  kill -0 "$KWIN_BRIDGE_PID" 2>/dev/null || {
    echo "KWin bridge exited before opening its private socket" >&2
    exit 1
  }
  sleep 0.1
done
[[ -S "$BRIDGE_SOCKET" ]] || {
  echo "KWin bridge socket did not become ready" >&2
  exit 1
}

export ECHO_KWIN_WINDOW_BRIDGE_SOCKET="$BRIDGE_SOCKET"
export XDG_SESSION_TYPE=wayland
export XDG_SESSION_DESKTOP=echo
export XDG_CURRENT_DESKTOP=Echo:KDE
export OCTOPUS_NATIVE_SHELL=1
export LIBGL_ALWAYS_SOFTWARE=1
export GTK_IM_MODULE=fcitx
export QT_IM_MODULE=fcitx
export XMODIFIERS=@im=fcitx
export SDL_IM_MODULE=fcitx
export QT_ACCESSIBILITY=1
unset DISPLAY WAYLAND_DISPLAY QT_QPA_PLATFORM

timeout --signal=TERM 480s kwin_wayland \
  --virtual \
  --width 1280 \
  --height 800 \
  --scale 1.25 \
  --output-count 2 \
  --xwayland \
  --no-global-shortcuts \
  --no-kactivities \
  --socket echo-wayland-smoke-0 \
  --exit-with-session "$CLIENT_SCRIPT" \
  >"$LOG_DIR/kwin-wayland.log" 2>&1

grep -q "Wayland session integration smoke OK" "$LOG_DIR/kwin-wayland.log" || {
  echo "KWin exited without the Wayland completion marker" >&2
  exit 1
}
grep -Fxq \
  'ECHO_NATIVE_APP_IPC_READY session=wayland app=org.kde.kcalc path=preload-ipc-gio result=zero-exit cleanup=closed' \
  "$LOG_DIR/kwin-wayland.log" || {
  echo "KWin exited without the packaged Echo Wayland IPC marker" >&2
  exit 1
}
echo "KWin Wayland multi-output/HiDPI/native-window smoke OK"
