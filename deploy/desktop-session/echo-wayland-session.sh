#!/usr/bin/env bash
# Echo OS production-candidate session: KWin/DRM Wayland -> Echo desktop shell.
set -euo pipefail

OS_DIR="${OCTOPUS_OS_DIR:-/opt/octopus-os}"
SESSION_WORKER="$OS_DIR/deploy/desktop-session/echo-wayland-shell-session.sh"
KWIN_WRAPPER=/usr/bin/kwin_wayland_wrapper
KWIN_BRIDGE_SERVICE=/usr/lib/echo-os/echo-kwin-window-bridge
RUNTIME_ROOT="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
SESSION_RUNTIME="$RUNTIME_ROOT/echo-os"
KWIN_BRIDGE_SOCKET="$SESSION_RUNTIME/kwin-window-bridge.sock"

export OCTOPUS_NATIVE_SHELL=1
export OCTOPUS_SHELL_MODE=desktop
export OCTOPUS_BACKEND_URL="${OCTOPUS_BACKEND_URL:-http://127.0.0.1:8000}"
export XDG_SESSION_TYPE=wayland
export XDG_SESSION_DESKTOP=echo-wayland
export XDG_CURRENT_DESKTOP=Echo:KDE
export QT_QPA_PLATFORM=wayland
export GTK_IM_MODULE=fcitx
export QT_IM_MODULE=fcitx
export XMODIFIERS=@im=fcitx
export SDL_IM_MODULE=fcitx
export QT_ACCESSIBILITY=1

[[ -x "$KWIN_WRAPPER" && -x "$KWIN_BRIDGE_SERVICE" && -x "$SESSION_WORKER" ]] || {
  echo "Echo OS Wayland session executables are incomplete" >&2
  exit 1
}
[[ "$RUNTIME_ROOT" == /* && -d "$RUNTIME_ROOT" && ! -L "$RUNTIME_ROOT" ]] || {
  echo "Echo OS Wayland session requires an absolute, real XDG_RUNTIME_DIR" >&2
  exit 1
}
[[ "$(stat -c '%u' "$RUNTIME_ROOT")" == "$(id -u)" ]] || {
  echo "Echo OS Wayland runtime directory is not owned by the session user" >&2
  exit 1
}

# A normal SDDM session already owns a user bus. The fallback keeps a manual
# bring-up usable without ever sharing bridge state between users.
if [[ -z "${DBUS_SESSION_BUS_ADDRESS:-}" && "${ECHO_WAYLAND_DBUS_SESSION:-0}" != 1 ]]; then
  export ECHO_WAYLAND_DBUS_SESSION=1
  exec dbus-run-session -- "$0" "$@"
fi

[[ -x /usr/bin/dbus-update-activation-environment && \
   -x /usr/bin/systemctl ]] || {
  echo "Echo OS session activation-environment tools are missing" >&2
  exit 1
}
/usr/bin/dbus-update-activation-environment \
  GTK_IM_MODULE QT_IM_MODULE XMODIFIERS SDL_IM_MODULE QT_ACCESSIBILITY
if /usr/bin/systemctl --user show-environment >/dev/null 2>&1; then
  /usr/bin/systemctl --user import-environment \
    GTK_IM_MODULE QT_IM_MODULE XMODIFIERS SDL_IM_MODULE QT_ACCESSIBILITY
fi

install -d -m 0700 "$SESSION_RUNTIME"
[[ ! -L "$SESSION_RUNTIME" && "$(stat -c '%u:%a' "$SESSION_RUNTIME")" == "$(id -u):700" ]] || {
  echo "Echo OS Wayland private runtime directory failed its ownership contract" >&2
  exit 1
}

cleanup() {
  local status="$1"
  trap - EXIT INT TERM
  [[ -n "${KWIN_WRAPPER_PID:-}" ]] && kill "$KWIN_WRAPPER_PID" 2>/dev/null || true
  [[ -n "${KWIN_BRIDGE_PID:-}" ]] && kill "$KWIN_BRIDGE_PID" 2>/dev/null || true
  wait 2>/dev/null || true
  rm -f -- "$KWIN_BRIDGE_SOCKET"
  exit "$status"
}
trap 'cleanup $?' EXIT
trap 'exit 130' INT TERM

"$KWIN_BRIDGE_SERVICE" --socket "$KWIN_BRIDGE_SOCKET" &
KWIN_BRIDGE_PID=$!
for _attempt in $(seq 1 80); do
  [[ -S "$KWIN_BRIDGE_SOCKET" ]] && break
  if ! kill -0 "$KWIN_BRIDGE_PID" 2>/dev/null; then
    wait "$KWIN_BRIDGE_PID" || true
    echo "Echo OS KWin bridge exited before opening its private socket" >&2
    exit 1
  fi
  sleep 0.1
done
[[ -S "$KWIN_BRIDGE_SOCKET" ]] || {
  echo "Echo OS KWin bridge socket did not become ready" >&2
  exit 1
}
export ECHO_KWIN_WINDOW_BRIDGE_SOCKET="$KWIN_BRIDGE_SOCKET"

# The KDE wrapper pre-allocates Wayland/XWayland sockets and synchronizes their
# names into the D-Bus and systemd user activation environments. The worker is
# KWin's --exit-with-session child, so compositor exit cannot strand the shell.
"$KWIN_WRAPPER" \
  --xwayland \
  --drm \
  --locale1 \
  --exit-with-session "$SESSION_WORKER" &
KWIN_WRAPPER_PID=$!

while kill -0 "$KWIN_WRAPPER_PID" 2>/dev/null; do
  if ! kill -0 "$KWIN_BRIDGE_PID" 2>/dev/null; then
    wait "$KWIN_BRIDGE_PID" || true
    echo "Echo OS KWin window bridge failed; terminating the Wayland session" >&2
    kill "$KWIN_WRAPPER_PID" 2>/dev/null || true
    wait "$KWIN_WRAPPER_PID" 2>/dev/null || true
    exit 1
  fi
  sleep 1
done
wait "$KWIN_WRAPPER_PID"
