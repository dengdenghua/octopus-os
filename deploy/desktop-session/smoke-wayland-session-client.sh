#!/usr/bin/env bash
# KWin --exit-with-session child: verify outputs and a native Wayland window.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
APP_DIR="$REPO_ROOT/frontend"
BRIDGE=/usr/lib/echo-os/echo-kwin-window-bridge
NATIVE_DRIVER="$REPO_ROOT/deploy/desktop-session/native-window-session-smoke.cjs"
WAYLAND_APP="$REPO_ROOT/deploy/desktop-session/wayland-window-smoke.py"
LOG_DIR="${ECHO_SMOKE_LOG_DIR:?ECHO_SMOKE_LOG_DIR is required}"
BRIDGE_SOCKET="${ECHO_KWIN_WINDOW_BRIDGE_SOCKET:?bridge socket is required}"
INPUT_METHOD=/usr/bin/fcitx5
CLIPBOARD_HOST=/usr/lib/echo-os/echo-clipboard-host
ACCESSIBILITY_BUS=/usr/libexec/at-spi-bus-launcher
ACCESSIBILITY_PROBE="$REPO_ROOT/deploy/desktop-session/echo-accessibility-smoke.py"
CORE_APPS_SESSION_SMOKE="$REPO_ROOT/deploy/core-apps/echo_core_apps_session_smoke.py"
WAYLAND_IPC_WINDOW_HELPER="$REPO_ROOT/deploy/desktop-session/verify_wayland_native_app_ipc.py"

[[ "${XDG_SESSION_TYPE:-}" == wayland ]] || {
  echo "KWin child did not inherit XDG_SESSION_TYPE=wayland" >&2
  exit 1
}
[[ -n "${WAYLAND_DISPLAY:-}" && -S "$XDG_RUNTIME_DIR/$WAYLAND_DISPLAY" ]] || {
  echo "KWin child did not receive a live WAYLAND_DISPLAY" >&2
  exit 1
}
[[ -x "$INPUT_METHOD" && -x "$CLIPBOARD_HOST" && \
   -x "$ACCESSIBILITY_BUS" && -x "$ACCESSIBILITY_PROBE" && \
   -x "$CORE_APPS_SESSION_SMOKE" && -f "$WAYLAND_IPC_WINDOW_HELPER" ]] || {
  echo "Wayland smoke input-method, clipboard or accessibility dependency is missing" >&2
  exit 1
}
for command_name in dbus-update-activation-environment wl-copy wl-paste stat timeout; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "Wayland smoke clipboard dependency is missing: $command_name" >&2
    exit 1
  }
done

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

timeout 10s wayland-info >"$LOG_DIR/wayland-info.log" 2>&1
WAYLAND_OUTPUT_COUNT="$(grep -c "interface: 'wl_output'" "$LOG_DIR/wayland-info.log")"
[[ "$WAYLAND_OUTPUT_COUNT" -eq 2 ]] || {
  echo "expected two wl_output globals, got $WAYLAND_OUTPUT_COUNT" >&2
  exit 1
}

for _attempt in $(seq 1 150); do
  if "$BRIDGE" --socket "$BRIDGE_SOCKET" --probe >/dev/null 2>&1; then
    break
  fi
  sleep 0.1
done
"$BRIDGE" --socket "$BRIDGE_SOCKET" --probe >/dev/null
export QT_QPA_PLATFORM=wayland
/usr/bin/dbus-update-activation-environment \
  WAYLAND_DISPLAY XDG_SESSION_TYPE XDG_CURRENT_DESKTOP QT_QPA_PLATFORM

COMPOSITOR_STATE="$(
  "$BRIDGE" --socket "$BRIDGE_SOCKET" --request list
)"
python3 -c '
import json
import math
import sys

state = json.loads(sys.argv[1])
outputs = state.get("outputs", [])
if len(outputs) != 2:
    raise SystemExit(f"expected two KWin outputs, got {outputs!r}")
if any(not math.isclose(item.get("scale", 0), 1.25) for item in outputs):
    raise SystemExit(f"expected 1.25x output scale, got {outputs!r}")
if any(item.get("width", 0) <= 0 or item.get("height", 0) <= 0 for item in outputs):
    raise SystemExit(f"invalid KWin output geometry: {outputs!r}")
print("  ✓ KWin bridge reported two 1.25x outputs")
' "$COMPOSITOR_STATE"

cleanup_client() {
  [[ -n "${ECHO_PID:-}" ]] && kill "$ECHO_PID" 2>/dev/null || true
  [[ -n "${NATIVE_APP_PID:-}" ]] && kill "$NATIVE_APP_PID" 2>/dev/null || true
  [[ -n "${INPUT_METHOD_PID:-}" ]] && kill "$INPUT_METHOD_PID" 2>/dev/null || true
  [[ -n "${CLIPBOARD_SOURCE_PID:-}" ]] && kill "$CLIPBOARD_SOURCE_PID" 2>/dev/null || true
  [[ -n "${CLIPBOARD_HOST_PID:-}" ]] && kill "$CLIPBOARD_HOST_PID" 2>/dev/null || true
  [[ -n "${ACCESSIBILITY_BUS_PID:-}" ]] && kill "$ACCESSIBILITY_BUS_PID" 2>/dev/null || true
  rm -f -- "${NATIVE_APP_IPC_READY_FILE:-}" "${RENDERER_READY_FILE:-}"
}
trap cleanup_client EXIT
trap 'exit 130' INT TERM

"$ACCESSIBILITY_BUS" --launch-immediately \
  >"$LOG_DIR/accessibility-bus-wayland.log" 2>&1 &
ACCESSIBILITY_BUS_PID=$!
for _attempt in $(seq 1 150); do
  if gdbus call --session \
       --dest org.a11y.Bus \
       --object-path /org/a11y/bus \
       --method org.a11y.Bus.GetAddress >/dev/null 2>&1; then
    break
  fi
  kill -0 "$ACCESSIBILITY_BUS_PID" 2>/dev/null || {
    echo "AT-SPI bus exited before registering its Wayland service" >&2
    exit 1
  }
  sleep 0.1
done
gdbus call --session \
  --dest org.a11y.Bus \
  --object-path /org/a11y/bus \
  --method org.a11y.Bus.GetAddress >/dev/null
echo "  ✓ AT-SPI registered the Wayland accessibility bus"

"$INPUT_METHOD" --replace >"$LOG_DIR/fcitx5-wayland.log" 2>&1 &
INPUT_METHOD_PID=$!
for _attempt in $(seq 1 150); do
  if gdbus call --session \
       --dest org.freedesktop.DBus \
       --object-path /org/freedesktop/DBus \
       --method org.freedesktop.DBus.NameHasOwner org.fcitx.Fcitx5 \
       2>/dev/null | grep -q true; then
    break
  fi
  kill -0 "$INPUT_METHOD_PID" 2>/dev/null || {
    echo "Fcitx5 exited before registering its Wayland session service" >&2
    exit 1
  }
  sleep 0.1
done
gdbus call --session \
  --dest org.freedesktop.DBus \
  --object-path /org/freedesktop/DBus \
  --method org.freedesktop.DBus.NameHasOwner org.fcitx.Fcitx5 |
  grep -q true
echo "  ✓ Fcitx5 registered the multilingual Wayland input-method service"

CLIPBOARD_DATABASE="$XDG_RUNTIME_DIR/echo-os/clipboard/history3.sqlite"
"$CLIPBOARD_HOST" --session wayland --database "$CLIPBOARD_DATABASE" \
  >"$LOG_DIR/clipboard-wayland.log" 2>&1 &
CLIPBOARD_HOST_PID=$!
for _attempt in $(seq 1 150); do
  if gdbus call --session \
       --dest org.freedesktop.DBus \
       --object-path /org/freedesktop/DBus \
       --method org.freedesktop.DBus.NameHasOwner org.kde.klipper \
       2>/dev/null | grep -q true; then
    break
  fi
  kill -0 "$CLIPBOARD_HOST_PID" 2>/dev/null || {
    echo "windowless Klipper host exited before registering on Wayland" >&2
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
  echo "Klipper did not create its private Wayland runtime database" >&2
  exit 1
}

CLIPBOARD_SENTINEL='echo-os-wayland-clipboard-persistence-smoke'
printf '%s' "$CLIPBOARD_SENTINEL" | \
  wl-copy --foreground --paste-once \
    >"$LOG_DIR/clipboard-source-wayland.log" 2>&1 &
CLIPBOARD_SOURCE_PID=$!
for _attempt in $(seq 1 150); do
  if ! kill -0 "$CLIPBOARD_SOURCE_PID" 2>/dev/null; then break; fi
  kill -0 "$CLIPBOARD_HOST_PID" 2>/dev/null || {
    echo "Klipper host exited while capturing the Wayland clipboard" >&2
    exit 1
  }
  sleep 0.1
done
if kill -0 "$CLIPBOARD_SOURCE_PID" 2>/dev/null; then
  echo "Klipper never captured the Wayland clipboard from its source owner" >&2
  exit 1
fi
wait "$CLIPBOARD_SOURCE_PID"
unset CLIPBOARD_SOURCE_PID
CLIPBOARD_VALUE=''
for _attempt in $(seq 1 150); do
  CLIPBOARD_VALUE="$(timeout 1s wl-paste --no-newline 2>/dev/null || true)"
  [[ "$CLIPBOARD_VALUE" == "$CLIPBOARD_SENTINEL" ]] && break
  kill -0 "$CLIPBOARD_HOST_PID" 2>/dev/null || break
  sleep 0.1
done
[[ "$CLIPBOARD_VALUE" == "$CLIPBOARD_SENTINEL" ]] || {
  echo "Wayland clipboard disappeared after the source process exited" >&2
  exit 1
}
unset CLIPBOARD_VALUE
echo "  ✓ Klipper preserved the Wayland clipboard after its source process exited without persistent-home storage"

ECHO_CORE_APPS_SESSION_TEST=USE-EPHEMERAL-RUNTIME \
  timeout 360s "$CORE_APPS_SESSION_SMOKE" --session wayland \
    --bridge-socket "$BRIDGE_SOCKET" \
    >"$LOG_DIR/core-apps-session-wayland.log"
grep -q '^ECHO_CORE_APPS_SESSION_READY session=wayland cases=directory,http,text,pdf,image,archive,audio,terminal,calculator transports=xdg-open,gio-launch windows=native cleanup=closed fixtures=runtime-and-loopback-only$' \
  "$LOG_DIR/core-apps-session-wayland.log"
echo "  ✓ XDG defaults and desktop launch opened nine real Wayland core-application windows and closed them"

# Prove that the packaged Echo renderer/preload path—not only a direct GIO
# fixture—can enumerate and launch one immutable desktop application inside the
# real KWin Wayland session. Snapshot compositor-owned UUIDs before Electron is
# allowed to request KCalc so a pre-existing window cannot satisfy the gate.
NATIVE_APP_IPC_READY_FILE="$XDG_RUNTIME_DIR/echo-os/native-app-ipc-ready"
RENDERER_READY_FILE="$XDG_RUNTIME_DIR/echo-os/renderer-ready"
NATIVE_APP_IPC_BASELINE_IDS="$(
  "$BRIDGE" --socket "$BRIDGE_SOCKET" --request list | \
    python3 "$WAYLAND_IPC_WINDOW_HELPER" baseline
)"
rm -f -- "$NATIVE_APP_IPC_READY_FILE" "$RENDERER_READY_FILE"

WAYLAND_ECHO_ARGUMENTS=(
  --class=echo-shell
  --ozone-platform=wayland
  --force-renderer-accessibility
  --enable-features=UseOzonePlatform
)
if [[ "$(id -u)" -eq 0 ]]; then
  WAYLAND_ECHO_ARGUMENTS+=(--no-sandbox)
fi
OCTOPUS_NATIVE_SHELL=1 \
OCTOPUS_SHELL_MODE=desktop \
OCTOPUS_SMOKE=1 \
OCTOPUS_SMOKE_HOLD_MS=45000 \
OCTOPUS_NATIVE_APP_SMOKE_ID=org.kde.kcalc \
ECHO_NATIVE_APP_IPC_READY_FILE="$NATIVE_APP_IPC_READY_FILE" \
ECHO_RENDERER_READY_FILE="$RENDERER_READY_FILE" \
QT_QPA_PLATFORM=wayland \
"$PACKAGED_BIN" "${WAYLAND_ECHO_ARGUMENTS[@]}" \
  >"$LOG_DIR/echo-desktop-wayland.log" 2>&1 &
ECHO_PID=$!

for _attempt in $(seq 1 300); do
  [[ -f "$NATIVE_APP_IPC_READY_FILE" && -f "$RENDERER_READY_FILE" ]] && break
  kill -0 "$ECHO_PID" 2>/dev/null || {
    echo "packaged Echo Desktop exited before completing its Wayland IPC smoke" >&2
    exit 1
  }
  sleep 0.1
done
[[ -f "$NATIVE_APP_IPC_READY_FILE" && ! -L "$NATIVE_APP_IPC_READY_FILE" && \
   "$(stat -c '%u:%a' "$NATIVE_APP_IPC_READY_FILE")" == "$(id -u):600" && \
   "$(<"$NATIVE_APP_IPC_READY_FILE")" == \
     "app=org.kde.kcalc path=preload-ipc-gio result=zero-exit" ]] || {
  echo "packaged Echo Desktop did not publish its private Wayland native-app IPC result" >&2
  exit 1
}
[[ -f "$RENDERER_READY_FILE" && ! -L "$RENDERER_READY_FILE" && \
   "$(stat -c '%u:%a' "$RENDERER_READY_FILE")" == "$(id -u):600" && \
   "$(<"$RENDERER_READY_FILE")" == \
     "provider=electron-renderer status=ready mode=desktop" ]] || {
  echo "packaged Echo Desktop did not publish trusted Wayland renderer readiness" >&2
  exit 1
}
grep -Fxq \
  'ECHO_NATIVE_APP_IPC_ACCEPTED app=org.kde.kcalc path=preload-ipc-gio result=zero-exit' \
  "$LOG_DIR/echo-desktop-wayland.log"

NATIVE_APP_IPC_WINDOW_ID=""
for _attempt in $(seq 1 300); do
  CURRENT_WINDOWS_JSON="$(
    "$BRIDGE" --socket "$BRIDGE_SOCKET" --request list
  )"
  set +e
  NATIVE_APP_IPC_WINDOW_ID="$(
    printf '%s' "$CURRENT_WINDOWS_JSON" | \
      python3 "$WAYLAND_IPC_WINDOW_HELPER" find \
        --baseline-ids "$NATIVE_APP_IPC_BASELINE_IDS"
  )"
  MATCH_STATUS=$?
  set -e
  [[ "$MATCH_STATUS" -ne 2 ]] || {
    echo "packaged Echo IPC returned an invalid Wayland window set" >&2
    exit 1
  }
  [[ "$MATCH_STATUS" -eq 0 ]] && break
  kill -0 "$ECHO_PID" 2>/dev/null || break
  sleep 0.1
done
[[ "$NATIVE_APP_IPC_WINDOW_ID" =~ ^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$ ]] || {
  echo "packaged Echo IPC returned success without a new Wayland KCalc window" >&2
  exit 1
}

"$BRIDGE" --socket "$BRIDGE_SOCKET" --request close \
  --window-id "$NATIVE_APP_IPC_WINDOW_ID" >/dev/null
for _attempt in $(seq 1 200); do
  CURRENT_WINDOWS_JSON="$(
    "$BRIDGE" --socket "$BRIDGE_SOCKET" --request list
  )"
  set +e
  printf '%s' "$CURRENT_WINDOWS_JSON" | \
    python3 "$WAYLAND_IPC_WINDOW_HELPER" absent \
      --window-id "$NATIVE_APP_IPC_WINDOW_ID"
  ABSENT_STATUS=$?
  set -e
  [[ "$ABSENT_STATUS" -ne 2 ]] || {
    echo "KWin returned invalid state while closing the IPC KCalc window" >&2
    exit 1
  }
  if [[ "$ABSENT_STATUS" -eq 0 ]]; then
    break
  fi
  sleep 0.1
done
printf '%s' "$CURRENT_WINDOWS_JSON" | \
  python3 "$WAYLAND_IPC_WINDOW_HELPER" absent \
    --window-id "$NATIVE_APP_IPC_WINDOW_ID" || {
      echo "KCalc window remained after the acknowledged close" >&2
      exit 1
    }
rm -f -- "$NATIVE_APP_IPC_READY_FILE"
echo "ECHO_NATIVE_APP_IPC_READY session=wayland app=org.kde.kcalc path=preload-ipc-gio result=zero-exit cleanup=closed"

timeout 20s python3 "$ACCESSIBILITY_PROBE" \
  --root-pid "$ECHO_PID" \
  --required-name "Echo OS 桌面" \
  --timeout-seconds 15 \
  >"$LOG_DIR/accessibility-tree-echo-wayland.log"
grep -Fxq 'ECHO_ACCESSIBILITY_TREE_READY provider=at-spi2 application=echo' \
  "$LOG_DIR/accessibility-tree-echo-wayland.log"
echo "  ✓ packaged Echo Desktop traversed preload IPC and exposed its Wayland AT-SPI marker"

if ! wait "$ECHO_PID"; then
  echo "packaged Echo Desktop Wayland smoke process exited unsuccessfully" >&2
  exit 1
fi
unset ECHO_PID
rm -f -- "$RENDERER_READY_FILE"

GDK_BACKEND=wayland python3 "$WAYLAND_APP" \
  >"$LOG_DIR/wayland-app.log" 2>&1 &
NATIVE_APP_PID=$!

KWIN_WINDOW_UUID=""
for _attempt in $(seq 1 150); do
  WINDOWS_JSON="$(
    "$BRIDGE" --socket "$BRIDGE_SOCKET" --request list
  )"
  if KWIN_WINDOW_UUID="$(python3 -c '
import json
import sys

state = json.loads(sys.argv[1])
matches = [
    item
    for item in state.get("windows", [])
    if "Echo Wayland Bridge Smoke" in item.get("title", "")
]
if len(matches) != 1:
    raise SystemExit(1)
window = matches[0]
print(window["id"])
' "$WINDOWS_JSON")"; then
    break
  fi
  kill -0 "$NATIVE_APP_PID" 2>/dev/null || {
    echo "native Wayland test application exited before KWin published it" >&2
    exit 1
  }
  sleep 0.1
done
[[ -n "$KWIN_WINDOW_UUID" ]] || {
  echo "KWin bridge did not publish the native Wayland test window" >&2
  exit 1
}
echo "  ✓ native Wayland client registered as $KWIN_WINDOW_UUID"

timeout 20s python3 "$ACCESSIBILITY_PROBE" \
  --root-pid "$NATIVE_APP_PID" \
  --required-name "Echo Wayland Accessibility Probe" \
  --timeout-seconds 15 \
  >"$LOG_DIR/accessibility-tree-wayland.log"
grep -q 'ECHO_ACCESSIBILITY_TREE_READY provider=at-spi2 application=echo' \
  "$LOG_DIR/accessibility-tree-wayland.log"
echo "  ✓ native Wayland client exposed its fixed AT-SPI marker"

ECHO_SMOKE_NATIVE_WINDOW_ID="$KWIN_WINDOW_UUID" node "$NATIVE_DRIVER"
wait "$NATIVE_APP_PID" || true
unset NATIVE_APP_PID

for _attempt in $(seq 1 150); do
  if gdbus call --session \
       --dest org.freedesktop.ScreenSaver \
       --object-path /ScreenSaver \
       --method org.freedesktop.ScreenSaver.GetActive \
       >/dev/null 2>&1; then
    break
  fi
  sleep 0.1
done
gdbus call --session \
  --dest org.freedesktop.ScreenSaver \
  --object-path /ScreenSaver \
  --method org.freedesktop.ScreenSaver.GetActive \
  >/dev/null
timeout 20s gdbus call --session \
  --dest org.freedesktop.ScreenSaver \
  --object-path /ScreenSaver \
  --method org.freedesktop.ScreenSaver.Lock \
  >"$LOG_DIR/wayland-lock.log" 2>&1
LOCK_STATE="$(gdbus call --session \
  --dest org.freedesktop.ScreenSaver \
  --object-path /ScreenSaver \
  --method org.freedesktop.ScreenSaver.GetActive)"
[[ "$LOCK_STATE" == *true* ]] || {
  echo "KScreenLocker did not enter the locked state: $LOCK_STATE" >&2
  exit 1
}
echo "ECHO_WAYLAND_LOCK_READY provider=kscreenlocker pam=kde"
echo "Wayland session integration smoke OK"
