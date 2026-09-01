#!/usr/bin/env bash
# Isolated target-C integration smoke: Xvfb -> D-Bus -> KWin -> Echo -> native app.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
APP_DIR="$REPO_ROOT/frontend"
VERIFY_SCRIPT="$REPO_ROOT/deploy/desktop-session/verify-desktop-session.sh"
NATIVE_DRIVER="$REPO_ROOT/deploy/desktop-session/native-window-session-smoke.cjs"
KWIN_BRIDGE=/usr/lib/echo-os/echo-kwin-window-bridge
INPUT_METHOD=/usr/bin/fcitx5
CLIPBOARD_HOST=/usr/lib/echo-os/echo-clipboard-host
ACCESSIBILITY_BUS=/usr/libexec/at-spi-bus-launcher
ACCESSIBILITY_PROBE="$REPO_ROOT/deploy/desktop-session/echo-accessibility-smoke.py"
CORE_APPS_SESSION_SMOKE="$REPO_ROOT/deploy/core-apps/echo_core_apps_session_smoke.py"

for command_name in Xvfb xdpyinfo kwin_x11 wmctrl xprop xmessage dbus-run-session dbus-update-activation-environment gdbus node xclip stat timeout; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "desktop smoke dependency missing: $command_name" >&2
    exit 1
  }
done
[[ -x "$KWIN_BRIDGE" && -x "$INPUT_METHOD" && -x "$CLIPBOARD_HOST" && \
   -x "$ACCESSIBILITY_BUS" && -x "$ACCESSIBILITY_PROBE" && \
   -x "$CORE_APPS_SESSION_SMOKE" ]] || {
  echo "desktop smoke bridge, input-method, clipboard or accessibility dependency is missing" >&2
  exit 1
}

# KWin needs a session bus. Re-enter exactly once so the test can also be run
# directly from a tty or CI container without an existing desktop session.
if [[ -z "${DBUS_SESSION_BUS_ADDRESS:-}" && "${ECHO_SMOKE_DBUS_SESSION:-0}" != "1" ]]; then
  export ECHO_SMOKE_DBUS_SESSION=1
  exec dbus-run-session -- "$0" "$@"
fi

PACKAGED_BIN="${ECHO_DESKTOP_EXECUTABLE:-}"
if [[ -z "$PACKAGED_BIN" ]]; then
  PACKAGED_BIN="$(
    find "$APP_DIR/release" -maxdepth 3 -type f -name echo-os-desktop \
      -perm -111 -print -quit 2>/dev/null || true
  )"
fi
[[ -n "$PACKAGED_BIN" && -x "$PACKAGED_BIN" ]] || {
  echo "packaged Echo Desktop executable not found; build electron-builder --linux dir first" >&2
  exit 1
}

if [[ -n "${ECHO_SMOKE_LOG_DIR:-}" ]]; then
  LOG_DIR="$ECHO_SMOKE_LOG_DIR"
  mkdir -p "$LOG_DIR"
  REMOVE_LOG_DIR=0
else
  LOG_DIR="$(mktemp -d)"
  REMOVE_LOG_DIR=1
fi

if [[ -z "${XDG_RUNTIME_DIR:-}" || ! -d "${XDG_RUNTIME_DIR:-}" ]]; then
  XDG_RUNTIME_DIR="$LOG_DIR/runtime"
  mkdir -p "$XDG_RUNTIME_DIR"
  chmod 0700 "$XDG_RUNTIME_DIR"
  export XDG_RUNTIME_DIR
fi

cleanup() {
  local status="$1"
  trap - EXIT INT TERM
  for process_id in "${NATIVE_APP_PID:-}" "${ECHO_PID:-}" \
    "${CLIPBOARD_SOURCE_PID:-}" "${CLIPBOARD_HOST_PID:-}" \
    "${ACCESSIBILITY_BUS_PID:-}" \
    "${INPUT_METHOD_PID:-}" "${KWIN_PID:-}" "${KWIN_BRIDGE_PID:-}" "${XVFB_PID:-}"; do
    [[ -n "$process_id" ]] && kill "$process_id" 2>/dev/null || true
  done
  wait 2>/dev/null || true
  [[ -n "${NATIVE_APP_IPC_READY_FILE:-}" ]] && \
    rm -f -- "$NATIVE_APP_IPC_READY_FILE"
  if [[ "$status" -ne 0 ]]; then
    echo "Desktop session smoke failed. Logs:" >&2
    for log_file in "$LOG_DIR"/*.log; do
      [[ -f "$log_file" ]] || continue
      echo "--- ${log_file##*/}" >&2
      tail -120 "$log_file" >&2 || true
    done
  elif [[ "$REMOVE_LOG_DIR" -eq 1 ]]; then
    rm -rf -- "$LOG_DIR"
  fi
  exit "$status"
}
trap 'cleanup $?' EXIT INT TERM

# Let Xvfb choose an unused display rather than assuming :99 is free.
DISPLAY_FILE="$LOG_DIR/xvfb-display"
Xvfb -displayfd 3 -screen 0 1280x800x24 -nolisten tcp -noreset \
  3>"$DISPLAY_FILE" >"$LOG_DIR/xvfb.log" 2>&1 &
XVFB_PID=$!
for _attempt in $(seq 1 100); do
  [[ -s "$DISPLAY_FILE" ]] && break
  kill -0 "$XVFB_PID" 2>/dev/null || {
    echo "Xvfb exited before allocating a display" >&2
    exit 1
  }
  sleep 0.1
done
[[ -s "$DISPLAY_FILE" ]] || { echo "Xvfb did not allocate a display" >&2; exit 1; }
DISPLAY=":$(tr -d '\r\n' < "$DISPLAY_FILE")"
export DISPLAY
for _attempt in $(seq 1 100); do
  xdpyinfo -display "$DISPLAY" >/dev/null 2>&1 && break
  sleep 0.1
done
xdpyinfo -display "$DISPLAY" >/dev/null 2>&1 || {
  echo "Xvfb display did not become ready: $DISPLAY" >&2
  exit 1
}
echo "✓ isolated X11 display ready: $DISPLAY"

export KWIN_COMPOSE=N
export LIBGL_ALWAYS_SOFTWARE=1
export QT_QPA_PLATFORM=xcb
export XDG_SESSION_TYPE=x11
export XDG_CURRENT_DESKTOP=Echo
export GTK_IM_MODULE=fcitx
export QT_IM_MODULE=fcitx
export XMODIFIERS=@im=fcitx
export SDL_IM_MODULE=fcitx
export QT_ACCESSIBILITY=1
KWIN_BRIDGE_SOCKET="$XDG_RUNTIME_DIR/echo-os/kwin-window-bridge.sock"
"$KWIN_BRIDGE" --socket "$KWIN_BRIDGE_SOCKET" \
  >"$LOG_DIR/kwin-bridge.log" 2>&1 &
KWIN_BRIDGE_PID=$!
for _attempt in $(seq 1 100); do
  [[ -S "$KWIN_BRIDGE_SOCKET" ]] && break
  kill -0 "$KWIN_BRIDGE_PID" 2>/dev/null || {
    echo "KWin bridge exited before opening its private socket" >&2
    exit 1
  }
  sleep 0.1
done
[[ -S "$KWIN_BRIDGE_SOCKET" ]] || {
  echo "KWin bridge socket did not become ready" >&2
  exit 1
}
kwin_x11 --replace >"$LOG_DIR/kwin.log" 2>&1 &
KWIN_PID=$!
for _attempt in $(seq 1 150); do
  if wmctrl -m 2>/dev/null | grep -qi kwin; then break; fi
  kill -0 "$KWIN_PID" 2>/dev/null || {
    echo "KWin exited before claiming the X11 display" >&2
    exit 1
  }
  sleep 0.1
done
wmctrl -m 2>/dev/null | grep -qi kwin || {
  echo "KWin did not claim the X11 display" >&2
  exit 1
}
echo "✓ KWin owns the isolated X11 session"
/usr/bin/dbus-update-activation-environment \
  DISPLAY XDG_SESSION_TYPE XDG_CURRENT_DESKTOP QT_QPA_PLATFORM
for _attempt in $(seq 1 100); do
  "$KWIN_BRIDGE" --socket "$KWIN_BRIDGE_SOCKET" --probe \
    >/dev/null 2>&1 && break
  sleep 0.1
done
"$KWIN_BRIDGE" --socket "$KWIN_BRIDGE_SOCKET" --probe >/dev/null
echo "✓ KWin script published compositor-owned UUID window state"

"$ACCESSIBILITY_BUS" --launch-immediately \
  >"$LOG_DIR/accessibility-bus-x11.log" 2>&1 &
ACCESSIBILITY_BUS_PID=$!
for _attempt in $(seq 1 100); do
  if gdbus call --session \
       --dest org.a11y.Bus \
       --object-path /org/a11y/bus \
       --method org.a11y.Bus.GetAddress >/dev/null 2>&1; then
    break
  fi
  kill -0 "$ACCESSIBILITY_BUS_PID" 2>/dev/null || {
    echo "AT-SPI bus exited before registering its X11 service" >&2
    exit 1
  }
  sleep 0.1
done
gdbus call --session \
  --dest org.a11y.Bus \
  --object-path /org/a11y/bus \
  --method org.a11y.Bus.GetAddress >/dev/null
echo "✓ AT-SPI registered the X11 accessibility bus"

"$INPUT_METHOD" --replace >"$LOG_DIR/fcitx5.log" 2>&1 &
INPUT_METHOD_PID=$!
for _attempt in $(seq 1 100); do
  if gdbus call --session \
       --dest org.freedesktop.DBus \
       --object-path /org/freedesktop/DBus \
       --method org.freedesktop.DBus.NameHasOwner org.fcitx.Fcitx5 \
       2>/dev/null | grep -q true; then
    break
  fi
  kill -0 "$INPUT_METHOD_PID" 2>/dev/null || {
    echo "Fcitx5 exited before registering its X11 session service" >&2
    exit 1
  }
  sleep 0.1
done
gdbus call --session \
  --dest org.freedesktop.DBus \
  --object-path /org/freedesktop/DBus \
  --method org.freedesktop.DBus.NameHasOwner org.fcitx.Fcitx5 |
  grep -q true
echo "✓ Fcitx5 registered the multilingual X11 input-method service"

CLIPBOARD_DATABASE="$XDG_RUNTIME_DIR/echo-os/clipboard/history3.sqlite"
"$CLIPBOARD_HOST" --session x11 --database "$CLIPBOARD_DATABASE" \
  >"$LOG_DIR/clipboard-x11.log" 2>&1 &
CLIPBOARD_HOST_PID=$!
for _attempt in $(seq 1 100); do
  if gdbus call --session \
       --dest org.freedesktop.DBus \
       --object-path /org/freedesktop/DBus \
       --method org.freedesktop.DBus.NameHasOwner org.kde.klipper \
       2>/dev/null | grep -q true; then
    break
  fi
  kill -0 "$CLIPBOARD_HOST_PID" 2>/dev/null || {
    echo "windowless Klipper host exited before registering on X11" >&2
    exit 1
  }
  sleep 0.1
done
gdbus call --session \
  --dest org.freedesktop.DBus \
  --object-path /org/freedesktop/DBus \
  --method org.freedesktop.DBus.NameHasOwner org.kde.klipper |
  grep -q true
[[ -f "$CLIPBOARD_DATABASE" && ! -L "$CLIPBOARD_DATABASE" && \
   "$(stat -c '%u:%a' "$CLIPBOARD_DATABASE")" == "$(id -u):600" ]] || {
  echo "Klipper did not create its private runtime-only database" >&2
  exit 1
}

CLIPBOARD_SENTINEL='echo-os-x11-clipboard-persistence-smoke'
printf '%s' "$CLIPBOARD_SENTINEL" | \
  xclip -selection clipboard -in -loops 1 \
    >"$LOG_DIR/clipboard-source-x11.log" 2>&1 &
CLIPBOARD_SOURCE_PID=$!
for _attempt in $(seq 1 100); do
  if ! kill -0 "$CLIPBOARD_SOURCE_PID" 2>/dev/null; then break; fi
  kill -0 "$CLIPBOARD_HOST_PID" 2>/dev/null || {
    echo "Klipper host exited while capturing the X11 clipboard" >&2
    exit 1
  }
  sleep 0.1
done
if kill -0 "$CLIPBOARD_SOURCE_PID" 2>/dev/null; then
  echo "Klipper never captured the X11 clipboard from its source owner" >&2
  exit 1
fi
wait "$CLIPBOARD_SOURCE_PID"
unset CLIPBOARD_SOURCE_PID
CLIPBOARD_VALUE=''
for _attempt in $(seq 1 100); do
  CLIPBOARD_VALUE="$(timeout 1s xclip -selection clipboard -out 2>/dev/null || true)"
  [[ "$CLIPBOARD_VALUE" == "$CLIPBOARD_SENTINEL" ]] && break
  kill -0 "$CLIPBOARD_HOST_PID" 2>/dev/null || break
  sleep 0.1
done
[[ "$CLIPBOARD_VALUE" == "$CLIPBOARD_SENTINEL" ]] || {
  echo "X11 clipboard disappeared after the source process exited" >&2
  exit 1
}
unset CLIPBOARD_VALUE
echo "✓ Klipper preserved the X11 clipboard after its source process exited without persistent-home storage"

NATIVE_APP_IPC_READY_FILE="$XDG_RUNTIME_DIR/echo-os/native-app-ipc-ready"
NATIVE_APP_IPC_BASELINE_IDS="$(
  wmctrl -l -x -p 2>/dev/null | \
    awk 'tolower($5) ~ /(^|[.])kcalc([.]|$)/ { print tolower($1) }'
)"
rm -f -- "$NATIVE_APP_IPC_READY_FILE"

ECHO_ARGUMENTS=(
  --class=echo-shell
  --ozone-platform=x11
  --force-renderer-accessibility
  --disable-gpu
)
if [[ "$(id -u)" -eq 0 ]]; then ECHO_ARGUMENTS+=(--no-sandbox); fi
ECHO_NATIVE_SHELL=1 \
ECHO_SHELL_MODE=desktop \
ECHO_SMOKE=1 \
ECHO_SMOKE_HOLD_MS=45000 \
ECHO_NATIVE_APP_SMOKE_ID=org.kde.kcalc \
ECHO_NATIVE_APP_IPC_READY_FILE="$NATIVE_APP_IPC_READY_FILE" \
"$PACKAGED_BIN" "${ECHO_ARGUMENTS[@]}" >"$LOG_DIR/echo-desktop.log" 2>&1 &
ECHO_PID=$!

for _attempt in $(seq 1 150); do
  ECHO_WINDOW_ID="$(
    wmctrl -lx 2>/dev/null | awk \
      'tolower($0) ~ /echo-shell|echo-os-desktop|echo desktop/ { print $1; exit }'
  )"
  [[ -n "$ECHO_WINDOW_ID" ]] && break
  kill -0 "$ECHO_PID" 2>/dev/null || {
    echo "packaged Echo Desktop exited before registering a KWin window" >&2
    exit 1
  }
  sleep 0.1
done
[[ -n "${ECHO_WINDOW_ID:-}" ]] || {
  echo "packaged Echo Desktop did not register a KWin window" >&2
  exit 1
}
echo "✓ packaged Echo Desktop registered as $ECHO_WINDOW_ID"

"$VERIFY_SCRIPT" --runtime
for _attempt in $(seq 1 300); do
  grep -q "SMOKE OK:" "$LOG_DIR/echo-desktop.log" && break
  kill -0 "$ECHO_PID" 2>/dev/null || {
    echo "packaged Echo Desktop exited before its renderer finished loading" >&2
    exit 1
  }
  sleep 0.1
done
grep -q "SMOKE OK:" "$LOG_DIR/echo-desktop.log" || {
  echo "packaged Echo Desktop window exists but its renderer did not finish loading" >&2
  exit 1
}
echo "✓ packaged renderer finished loading"

for _attempt in $(seq 1 300); do
  [[ -f "$NATIVE_APP_IPC_READY_FILE" ]] && break
  kill -0 "$ECHO_PID" 2>/dev/null || {
    echo "packaged Echo Desktop exited before completing native-app IPC smoke" >&2
    exit 1
  }
  sleep 0.1
done
[[ -f "$NATIVE_APP_IPC_READY_FILE" && ! -L "$NATIVE_APP_IPC_READY_FILE" && \
   "$(stat -c '%u:%a' "$NATIVE_APP_IPC_READY_FILE")" == "$(id -u):600" && \
   "$(<"$NATIVE_APP_IPC_READY_FILE")" == \
     "app=org.kde.kcalc path=preload-ipc-gio result=zero-exit" ]] || {
  echo "packaged Echo Desktop did not publish its private native-app IPC result" >&2
  exit 1
}
grep -q '^ECHO_NATIVE_APP_IPC_ACCEPTED app=org.kde.kcalc path=preload-ipc-gio result=zero-exit$' \
  "$LOG_DIR/echo-desktop.log"

NATIVE_APP_IPC_WINDOW_ID=""
for _attempt in $(seq 1 300); do
  NATIVE_APP_IPC_WINDOW_ID="$(
    wmctrl -l -x -p 2>/dev/null | awk \
      -v baseline="$NATIVE_APP_IPC_BASELINE_IDS" '
        BEGIN {
          count = split(baseline, ids, "\n")
          for (index = 1; index <= count; index++) seen[tolower(ids[index])] = 1
        }
        tolower($5) ~ /(^|[.])kcalc([.]|$)/ &&
        $3 ~ /^[1-9][0-9]*$/ && !seen[tolower($1)] { print tolower($1) }
      '
  )"
  [[ "$(printf '%s\n' "$NATIVE_APP_IPC_WINDOW_ID" | sed '/^$/d' | wc -l)" -le 1 ]] || {
    echo "native-app IPC smoke opened multiple new KCalc windows" >&2
    exit 1
  }
  [[ -n "$NATIVE_APP_IPC_WINDOW_ID" ]] && break
  kill -0 "$ECHO_PID" 2>/dev/null || break
  sleep 0.1
done
[[ "$NATIVE_APP_IPC_WINDOW_ID" =~ ^0x[0-9a-f]+$ ]] || {
  echo "native-app IPC returned success without a new KCalc window" >&2
  exit 1
}
wmctrl -ic "$NATIVE_APP_IPC_WINDOW_ID"
for _attempt in $(seq 1 200); do
  wmctrl -l 2>/dev/null | awk '{ print tolower($1) }' | \
    grep -Fxq "$NATIVE_APP_IPC_WINDOW_ID" || break
  sleep 0.1
done
wmctrl -l 2>/dev/null | awk '{ print tolower($1) }' | \
  grep -Fxq "$NATIVE_APP_IPC_WINDOW_ID" && {
    echo "native-app IPC KCalc window did not close" >&2
    exit 1
  }
rm -f -- "$NATIVE_APP_IPC_READY_FILE"
echo "ECHO_NATIVE_APP_IPC_READY app=org.kde.kcalc path=preload-ipc-gio result=zero-exit cleanup=closed"

timeout 20s python3 "$ACCESSIBILITY_PROBE" \
  --root-pid "$ECHO_PID" \
  --required-name "Echo OS 桌面" \
  --timeout-seconds 15 \
  >"$LOG_DIR/accessibility-tree-x11.log"
grep -q 'ECHO_ACCESSIBILITY_TREE_READY provider=at-spi2 application=echo' \
  "$LOG_DIR/accessibility-tree-x11.log"
echo "✓ packaged Echo Desktop exposed its fixed X11 AT-SPI marker"

if ! wait "$ECHO_PID"; then
  echo "packaged Echo Desktop smoke process exited unsuccessfully" >&2
  exit 1
fi
unset ECHO_PID

ECHO_CORE_APPS_SESSION_TEST=USE-EPHEMERAL-RUNTIME \
  timeout 360s "$CORE_APPS_SESSION_SMOKE" --session x11 \
    >"$LOG_DIR/core-apps-session-x11.log"
grep -q '^ECHO_CORE_APPS_SESSION_READY session=x11 cases=directory,http,text,pdf,image,archive,audio,terminal,calculator transports=xdg-open,gio-launch windows=native cleanup=closed fixtures=runtime-and-loopback-only$' \
  "$LOG_DIR/core-apps-session-x11.log"
echo "✓ XDG defaults and desktop launch opened nine real X11 core-application windows and closed them"

xmessage \
  -name echo-native-smoke \
  -title "Echo Native Bridge Smoke" \
  -buttons "Dismiss:0" \
  "Echo OS real native window lifecycle" \
  >"$LOG_DIR/native-app.log" 2>&1 &
NATIVE_APP_PID=$!
for _attempt in $(seq 1 100); do
  NATIVE_WINDOW_ID="$(
    wmctrl -l -x -p 2>/dev/null | awk \
      '/Echo Native Bridge Smoke/ { print $1; exit }'
  )"
  [[ -n "$NATIVE_WINDOW_ID" ]] && break
  kill -0 "$NATIVE_APP_PID" 2>/dev/null || {
    echo "native smoke application exited before creating a window" >&2
    exit 1
  }
  sleep 0.1
done
[[ -n "${NATIVE_WINDOW_ID:-}" ]] || {
  echo "native smoke window did not appear in KWin" >&2
  exit 1
}
echo "✓ native test application registered as $NATIVE_WINDOW_ID"

ECHO_SMOKE_NATIVE_WINDOW_ID="$NATIVE_WINDOW_ID" \
ECHO_SMOKE_SKIP_CLOSE=1 \
  node "$NATIVE_DRIVER"

KWIN_WINDOWS_JSON="$(
  "$KWIN_BRIDGE" --socket "$KWIN_BRIDGE_SOCKET" --request list
)"
KWIN_WINDOW_UUID="$(
  python3 -c '
import json
import sys

response = json.loads(sys.argv[1])
matches = [
    item["id"]
    for item in response.get("windows", [])
    if "Echo Native Bridge Smoke" in item.get("title", "")
]
if len(matches) != 1:
    raise SystemExit(f"expected one KWin UUID window, got {matches!r}")
print(matches[0])
' "$KWIN_WINDOWS_JSON"
)"
"$KWIN_BRIDGE" --socket "$KWIN_BRIDGE_SOCKET" --request close \
  --window-id "$KWIN_WINDOW_UUID" >/dev/null
wait "$NATIVE_APP_PID" || true
unset NATIVE_APP_PID
echo "✓ compositor-native KWin UUID provider closed the real window"

echo "Desktop session integration smoke OK"
