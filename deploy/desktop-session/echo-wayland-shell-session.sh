#!/usr/bin/env bash
# KWin --exit-with-session child for the selectable Echo Wayland candidate.
set -euo pipefail

OS_DIR="${OCTOPUS_OS_DIR:-/opt/octopus-os}"
APP_DIR="$OS_DIR/frontend"
KWIN_BRIDGE_SERVICE=/usr/lib/echo-os/echo-kwin-window-bridge
KWIN_BRIDGE_SOCKET="${ECHO_KWIN_WINDOW_BRIDGE_SOCKET:?KWin bridge socket is required}"
SESSION_RUNTIME="${XDG_RUNTIME_DIR:?XDG_RUNTIME_DIR is required}/echo-os"
READY_FILE="$SESSION_RUNTIME/desktop-ready"
READY_TEMP_FILE="$READY_FILE.$$"
RENDERER_READY_FILE="$SESSION_RUNTIME/renderer-ready"
POLKIT_AGENT=/usr/lib/x86_64-linux-gnu/libexec/polkit-kde-authentication-agent-1
POWERDEVIL=/usr/lib/x86_64-linux-gnu/libexec/org_kde_powerdevil
NOTIFICATION_SERVICE=/usr/lib/echo-os/echo-notification-service
NOTIFICATION_SOCKET="$SESSION_RUNTIME/notifications.sock"
INPUT_METHOD=/usr/bin/fcitx5
CLIPBOARD_HOST=/usr/lib/echo-os/echo-clipboard-host
CLIPBOARD_DATABASE="$SESSION_RUNTIME/clipboard/history3.sqlite"
ACCESSIBILITY_BUS=/usr/libexec/at-spi-bus-launcher
ACCESSIBILITY_PROBE=/usr/lib/echo-os/echo-accessibility-smoke.py
WAYLAND_IPC_REQUEST=/etc/echo-os/wayland-native-app-ipc
WAYLAND_IPC_WINDOW_HELPER=/usr/lib/echo-os/verify-wayland-native-app-ipc.py
NATIVE_APP_IPC_READY_FILE="$SESSION_RUNTIME/native-app-ipc-ready"

export OCTOPUS_NATIVE_SHELL=1
export OCTOPUS_SHELL_MODE=desktop
export XDG_SESSION_TYPE=wayland
export XDG_SESSION_DESKTOP=echo-wayland
export XDG_CURRENT_DESKTOP=Echo:KDE
export QT_QPA_PLATFORM=wayland
export ECHO_RENDERER_READY_FILE="$RENDERER_READY_FILE"
export GTK_IM_MODULE=fcitx
export QT_IM_MODULE=fcitx
export XMODIFIERS=@im=fcitx
export SDL_IM_MODULE=fcitx
export QT_ACCESSIBILITY=1
unset OCTOPUS_SMOKE OCTOPUS_NATIVE_APP_SMOKE_ID \
  ECHO_NATIVE_APP_IPC_READY_FILE

name_has_owner() {
  gdbus call --session \
    --dest org.freedesktop.DBus \
    --object-path /org/freedesktop/DBus \
    --method org.freedesktop.DBus.NameHasOwner "$1" 2>/dev/null |
    grep -q 'true'
}

system_name_has_owner() {
  gdbus call --system \
    --dest org.freedesktop.DBus \
    --object-path /org/freedesktop/DBus \
    --method org.freedesktop.DBus.NameHasOwner "$1" 2>/dev/null |
    grep -q 'true'
}

accessibility_address_ready() {
  gdbus call --session \
    --dest org.a11y.Bus \
    --object-path /org/a11y/bus \
    --method org.a11y.Bus.GetAddress >/dev/null 2>&1
}

user_environment_value() {
  local wanted="$1"
  local line
  while IFS= read -r line; do
    if [[ "$line" == "$wanted="* ]]; then
      printf '%s\n' "${line#*=}"
      return 0
    fi
  done < <(systemctl --user show-environment)
  return 1
}

cleanup() {
  local status="$1"
  trap - EXIT INT TERM
  [[ -n "${SHELL_PID:-}" ]] && kill "$SHELL_PID" 2>/dev/null || true
  [[ -n "${POLKIT_AGENT_PID:-}" ]] && kill "$POLKIT_AGENT_PID" 2>/dev/null || true
  [[ -n "${POWERDEVIL_PID:-}" ]] && kill "$POWERDEVIL_PID" 2>/dev/null || true
  [[ -n "${NOTIFICATION_SERVICE_PID:-}" ]] && kill "$NOTIFICATION_SERVICE_PID" 2>/dev/null || true
  [[ -n "${INPUT_METHOD_PID:-}" ]] && kill "$INPUT_METHOD_PID" 2>/dev/null || true
  [[ -n "${CLIPBOARD_HOST_PID:-}" ]] && kill "$CLIPBOARD_HOST_PID" 2>/dev/null || true
  [[ -n "${ACCESSIBILITY_BUS_PID:-}" ]] && kill "$ACCESSIBILITY_BUS_PID" 2>/dev/null || true
  wait 2>/dev/null || true
  rm -f -- "$READY_FILE" "$READY_TEMP_FILE" "$RENDERER_READY_FILE" \
    "$NATIVE_APP_IPC_READY_FILE"
  exit "$status"
}
trap 'cleanup $?' EXIT
trap 'exit 130' INT TERM

for command_name in dirname gdbus python3 systemctl timeout xdpyinfo stat; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "Echo OS Wayland session dependency missing: $command_name" >&2
    exit 1
  }
done
[[ "${WAYLAND_DISPLAY:-}" =~ ^[A-Za-z0-9._-]{1,128}$ && \
   -S "$XDG_RUNTIME_DIR/$WAYLAND_DISPLAY" && \
   "$(stat -c '%u' "$XDG_RUNTIME_DIR/$WAYLAND_DISPLAY")" == "$(id -u)" ]] || {
  echo "KWin did not provide a private native Wayland socket" >&2
  exit 1
}
[[ "${DISPLAY:-}" =~ ^:[0-9]{1,5}(\.[0-9]+)?$ ]] || {
  echo "KWin wrapper did not provide a valid XWayland DISPLAY" >&2
  exit 1
}
[[ "${XAUTHORITY:-}" == /* && -f "$XAUTHORITY" && ! -L "$XAUTHORITY" && \
   -r "$XAUTHORITY" && "$(stat -c '%u' "$XAUTHORITY")" == "$(id -u)" ]] || {
  echo "KWin wrapper did not provide a private XWayland authority file" >&2
  exit 1
}
[[ -d "$SESSION_RUNTIME" && ! -L "$SESSION_RUNTIME" && \
   "$(stat -c '%u:%a' "$SESSION_RUNTIME")" == "$(id -u):700" ]] || {
  echo "Echo OS Wayland private runtime directory is unsafe" >&2
  exit 1
}
rm -f -- "$READY_FILE" "$READY_TEMP_FILE" "$RENDERER_READY_FILE" \
  "$NATIVE_APP_IPC_READY_FILE"

[[ -x "$ACCESSIBILITY_BUS" ]] || {
  echo "Echo OS AT-SPI bus launcher is missing: $ACCESSIBILITY_BUS" >&2
  exit 1
}
if ! name_has_owner org.a11y.Bus; then
  "$ACCESSIBILITY_BUS" --launch-immediately &
  ACCESSIBILITY_BUS_PID=$!
fi
for _attempt in $(seq 1 150); do
  if name_has_owner org.a11y.Bus && accessibility_address_ready; then
    break
  fi
  if [[ -n "${ACCESSIBILITY_BUS_PID:-}" ]] && \
     ! kill -0 "$ACCESSIBILITY_BUS_PID" 2>/dev/null; then
    wait "$ACCESSIBILITY_BUS_PID" || true
    echo "Echo OS AT-SPI bus exited during Wayland startup" >&2
    exit 1
  fi
  sleep 0.1
done
name_has_owner org.a11y.Bus && accessibility_address_ready || {
  echo "Echo OS AT-SPI accessibility bus did not become ready" >&2
  exit 1
}
echo "ECHO_ACCESSIBILITY_READY provider=at-spi2 dbus=ready qt=enabled session=wayland"

[[ -x "$NOTIFICATION_SERVICE" ]] || {
  echo "Echo OS notification service is missing: $NOTIFICATION_SERVICE" >&2
  exit 1
}
"$NOTIFICATION_SERVICE" --socket "$NOTIFICATION_SOCKET" --session wayland &
NOTIFICATION_SERVICE_PID=$!
for _attempt in $(seq 1 100); do
  if name_has_owner org.freedesktop.Notifications && \
     [[ -S "$NOTIFICATION_SOCKET" ]]; then
    break
  fi
  if ! kill -0 "$NOTIFICATION_SERVICE_PID" 2>/dev/null; then
    wait "$NOTIFICATION_SERVICE_PID" || true
    echo "Echo OS notification service exited during startup" >&2
    exit 1
  fi
  sleep 0.1
done
name_has_owner org.freedesktop.Notifications && \
  [[ -S "$NOTIFICATION_SOCKET" && ! -L "$NOTIFICATION_SOCKET" && \
     "$(stat -c '%u:%a' "$NOTIFICATION_SOCKET")" == "$(id -u):600" ]] || {
    echo "Echo OS notification service did not establish its private bridge" >&2
    exit 1
  }
export ECHO_NOTIFICATION_SOCKET="$NOTIFICATION_SOCKET"
echo "ECHO_NOTIFICATION_SERVICE_READY provider=echo-native dbus=ready socket=private session=wayland"

# org.kde.KWinWrapper is registered only after KDE has synchronized these
# values for D-Bus- and systemd-activated applications. Compare rather than
# eval the environment returned by systemd.
for _attempt in $(seq 1 150); do
  if name_has_owner org.kde.KWinWrapper; then break; fi
  sleep 0.1
done
name_has_owner org.kde.KWinWrapper || {
  echo "KWin wrapper never completed activation-environment synchronization" >&2
  exit 1
}
[[ "$(user_environment_value WAYLAND_DISPLAY)" == "$WAYLAND_DISPLAY" && \
   "$(user_environment_value DISPLAY)" == "$DISPLAY" && \
   "$(user_environment_value XAUTHORITY)" == "$XAUTHORITY" && \
   "$(user_environment_value GTK_IM_MODULE)" == fcitx && \
   "$(user_environment_value QT_IM_MODULE)" == fcitx && \
   "$(user_environment_value XMODIFIERS)" == @im=fcitx && \
   "$(user_environment_value SDL_IM_MODULE)" == fcitx && \
   "$(user_environment_value QT_ACCESSIBILITY)" == 1 ]] || {
  echo "KWin wrapper and systemd user environments disagree" >&2
  exit 1
}

[[ -x "$INPUT_METHOD" ]] || {
  echo "Echo OS Fcitx5 input method is missing: $INPUT_METHOD" >&2
  exit 1
}
"$INPUT_METHOD" --replace &
INPUT_METHOD_PID=$!
for _attempt in $(seq 1 150); do
  if name_has_owner org.fcitx.Fcitx5; then break; fi
  if ! kill -0 "$INPUT_METHOD_PID" 2>/dev/null; then
    wait "$INPUT_METHOD_PID" || true
    echo "Echo OS Fcitx5 exited during Wayland startup" >&2
    exit 1
  fi
  sleep 0.1
done
name_has_owner org.fcitx.Fcitx5 || {
  echo "Echo OS Fcitx5 did not register its Wayland session service" >&2
  exit 1
}
echo "ECHO_INPUT_METHOD_READY provider=fcitx5 dbus=ready frontend=wayland"

[[ -x "$CLIPBOARD_HOST" ]] || {
  echo "Echo OS windowless Klipper host is missing: $CLIPBOARD_HOST" >&2
  exit 1
}
"$CLIPBOARD_HOST" --session wayland --database "$CLIPBOARD_DATABASE" &
CLIPBOARD_HOST_PID=$!
for _attempt in $(seq 1 150); do
  if name_has_owner org.kde.klipper && \
     [[ -f "$CLIPBOARD_DATABASE" ]]; then
    break
  fi
  if ! kill -0 "$CLIPBOARD_HOST_PID" 2>/dev/null; then
    wait "$CLIPBOARD_HOST_PID" || true
    echo "Echo OS Klipper host exited during Wayland startup" >&2
    exit 1
  fi
  sleep 0.1
done
name_has_owner org.kde.klipper && \
  [[ -f "$CLIPBOARD_DATABASE" && ! -L "$CLIPBOARD_DATABASE" && \
     "$(stat -c '%u:%a' "$CLIPBOARD_DATABASE")" == "$(id -u):600" ]] || {
    echo "Echo OS Klipper did not establish a private runtime clipboard" >&2
    exit 1
  }
echo "ECHO_CLIPBOARD_READY provider=klipper-qml dbus=ready storage=runtime-tmpfs persistence=off session=wayland"

for _attempt in $(seq 1 150); do
  if xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then break; fi
  sleep 0.1
done
xdpyinfo -display "$DISPLAY" >/dev/null 2>&1 || {
  echo "XWayland compatibility display did not become ready" >&2
  exit 1
}

[[ -x "$POLKIT_AGENT" ]] || {
  echo "Echo OS PolicyKit authentication agent is missing: $POLKIT_AGENT" >&2
  exit 1
}
"$POLKIT_AGENT" &
POLKIT_AGENT_PID=$!
sleep 0.2
kill -0 "$POLKIT_AGENT_PID" 2>/dev/null || {
  wait "$POLKIT_AGENT_PID" || true
  echo "Echo OS PolicyKit authentication agent exited during startup" >&2
  exit 1
}
echo "ECHO_AUTH_AGENT_READY provider=polkit-kde session=wayland"

[[ -x "$POWERDEVIL" ]] || {
  echo "Echo OS PowerDevil runtime is missing: $POWERDEVIL" >&2
  exit 1
}
"$POWERDEVIL" &
POWERDEVIL_PID=$!
for _attempt in $(seq 1 100); do
  if name_has_owner org.kde.Solid.PowerManagement && \
     system_name_has_owner org.freedesktop.UPower && \
     system_name_has_owner net.hadess.PowerProfiles; then
    break
  fi
  if ! kill -0 "$POWERDEVIL_PID" 2>/dev/null; then
    wait "$POWERDEVIL_PID" || true
    echo "Echo OS PowerDevil exited before its power backends became ready" >&2
    exit 1
  fi
  sleep 0.1
done
name_has_owner org.kde.Solid.PowerManagement && \
  system_name_has_owner org.freedesktop.UPower && \
  system_name_has_owner net.hadess.PowerProfiles || {
    echo "Echo OS power-management D-Bus chain did not become ready" >&2
    exit 1
  }
echo "ECHO_POWER_MANAGEMENT_READY provider=powerdevil upower=ready profiles=ready session=wayland"

for _attempt in $(seq 1 150); do
  if "$KWIN_BRIDGE_SERVICE" --socket "$KWIN_BRIDGE_SOCKET" --probe \
       >/dev/null 2>&1; then
    break
  fi
  sleep 0.1
done
"$KWIN_BRIDGE_SERVICE" --socket "$KWIN_BRIDGE_SOCKET" --probe >/dev/null || {
  echo "KWin compositor bridge did not publish its first Wayland snapshot" >&2
  exit 1
}
echo "ECHO_KWIN_COMPOSITOR_BRIDGE_READY provider=kwin-wayland transport=private-socket"

# A release image contains only the root-owned /etc/echo-os directory. The raw
# SDDM harness may add this one exact request to its disposable encrypted /etc
# overlay. Both this session and Electron validate it independently; normal
# sessions cannot opt themselves into the diagnostic with environment variables.
WAYLAND_NATIVE_APP_IPC_REQUESTED=0
if [[ -e "$WAYLAND_IPC_REQUEST" || -L "$WAYLAND_IPC_REQUEST" ]]; then
  WAYLAND_IPC_REQUEST_PARENT="$(dirname "$WAYLAND_IPC_REQUEST")"
  WAYLAND_IPC_REQUEST_PARENT_MODE="$(stat -c '%a' "$WAYLAND_IPC_REQUEST_PARENT")"
  [[ -d "$WAYLAND_IPC_REQUEST_PARENT" && \
     ! -L "$WAYLAND_IPC_REQUEST_PARENT" && \
     "$(stat -c '%u:%g' "$WAYLAND_IPC_REQUEST_PARENT")" == 0:0 && \
     "$WAYLAND_IPC_REQUEST_PARENT_MODE" =~ ^[0-7]{3,4}$ && \
     $((8#$WAYLAND_IPC_REQUEST_PARENT_MODE & 022)) -eq 0 && \
     -f "$WAYLAND_IPC_REQUEST" && ! -L "$WAYLAND_IPC_REQUEST" && \
     "$(stat -c '%u:%g:%a' "$WAYLAND_IPC_REQUEST")" == 0:0:444 && \
     "$(stat -c '%s' "$WAYLAND_IPC_REQUEST")" -eq 27 && \
     "$(<"$WAYLAND_IPC_REQUEST")" == "schema=1 app=org.kde.kcalc" ]] || {
       echo "Echo OS Wayland native-app IPC request is unsafe or invalid" >&2
       exit 1
     }
  [[ -x "$WAYLAND_IPC_WINDOW_HELPER" ]] || {
    echo "Echo OS Wayland native-app IPC evidence helper is missing" >&2
    exit 1
  }
  NATIVE_APP_IPC_BASELINE_IDS="$(
    "$KWIN_BRIDGE_SERVICE" --socket "$KWIN_BRIDGE_SOCKET" --request list | \
      python3 "$WAYLAND_IPC_WINDOW_HELPER" baseline
  )"
  rm -f -- "$NATIVE_APP_IPC_READY_FILE"
  export OCTOPUS_NATIVE_APP_SMOKE_ID=org.kde.kcalc
  export ECHO_NATIVE_APP_IPC_READY_FILE="$NATIVE_APP_IPC_READY_FILE"
  WAYLAND_NATIVE_APP_IPC_REQUESTED=1
fi

GREETER_CANDIDATES=(/usr/lib/*-linux-gnu/libexec/kscreenlocker_greet)
[[ "${#GREETER_CANDIDATES[@]}" -eq 1 && -x "${GREETER_CANDIDATES[0]}" && \
   -f /usr/lib/pam.d/kde ]] || {
  echo "KScreenLocker greeter or PAM policy is missing" >&2
  exit 1
}
for _attempt in $(seq 1 150); do
  if gdbus call --session \
       --dest org.freedesktop.ScreenSaver \
       --object-path /ScreenSaver \
       --method org.freedesktop.ScreenSaver.GetActive >/dev/null 2>&1; then
    break
  fi
  sleep 0.1
done
gdbus call --session \
  --dest org.freedesktop.ScreenSaver \
  --object-path /ScreenSaver \
  --method org.freedesktop.ScreenSaver.GetActive >/dev/null || {
  echo "KScreenLocker did not register its session lock interface" >&2
  exit 1
}
export ECHO_LOCK_SCREEN_READY=1
echo "ECHO_LOCK_SERVICE_READY provider=kscreenlocker pam=kde idle=600 resume=locked"

# Flatpak applications and icons persist outside either A/B root slot.
XDG_DATA_HOME_EFFECTIVE="${XDG_DATA_HOME:-${HOME:-/home/octopus}/.local/share}"
XDG_DATA_DIRS_DEFAULT=/usr/local/share:/usr/share
export XDG_DATA_DIRS="$XDG_DATA_HOME_EFFECTIVE/flatpak/exports/share:/var/lib/flatpak/exports/share:${XDG_DATA_DIRS:-$XDG_DATA_DIRS_DEFAULT}"

cd "$APP_DIR"
if [[ -n "${ECHO_DESKTOP_EXECUTABLE:-}" ]]; then
  [[ -x "$ECHO_DESKTOP_EXECUTABLE" ]] || {
    echo "ECHO_DESKTOP_EXECUTABLE is not executable: $ECHO_DESKTOP_EXECUTABLE" >&2
    exit 1
  }
  SHELL_COMMAND=("$ECHO_DESKTOP_EXECUTABLE")
else
  PACKAGED_BIN="$(
    find "$APP_DIR/release" -maxdepth 3 -type f -name echo-os-desktop \
      -perm -111 -print -quit 2>/dev/null || true
  )"
  if [[ -n "$PACKAGED_BIN" ]]; then
    SHELL_COMMAND=("$PACKAGED_BIN")
  else
    SHELL_COMMAND=(npx --no-install electron electron/main.cjs)
  fi
fi

"${SHELL_COMMAND[@]}" \
  --class=echo-shell \
  --ozone-platform=wayland \
  --force-renderer-accessibility \
  --enable-features=UseOzonePlatform &
SHELL_PID=$!

for _attempt in $(seq 1 300); do
  [[ -f "$RENDERER_READY_FILE" ]] && break
  if ! kill -0 "$SHELL_PID" 2>/dev/null; then
    wait "$SHELL_PID"
    exit $?
  fi
  sleep 0.1
done
[[ -f "$RENDERER_READY_FILE" && ! -L "$RENDERER_READY_FILE" && \
   "$(stat -c '%u:%a' "$RENDERER_READY_FILE")" == "$(id -u):600" && \
   "$(<"$RENDERER_READY_FILE")" == "provider=electron-renderer status=ready mode=desktop" ]] || {
  echo "Echo Wayland renderer did not publish its trusted readiness file" >&2
  exit 1
}
[[ -x "$ACCESSIBILITY_PROBE" ]] || {
  echo "Echo OS AT-SPI application-tree probe is missing" >&2
  exit 1
}
timeout 20s python3 "$ACCESSIBILITY_PROBE" \
  --root-pid "$SHELL_PID" \
  --required-name "Echo OS 桌面" \
  --timeout-seconds 15
if [[ "$WAYLAND_NATIVE_APP_IPC_REQUESTED" -eq 1 ]]; then
  for _attempt in $(seq 1 300); do
    [[ -f "$NATIVE_APP_IPC_READY_FILE" ]] && break
    kill -0 "$SHELL_PID" 2>/dev/null || {
      echo "Echo Wayland shell exited before completing native-app IPC" >&2
      exit 1
    }
    sleep 0.1
  done
  [[ -f "$NATIVE_APP_IPC_READY_FILE" && \
     ! -L "$NATIVE_APP_IPC_READY_FILE" && \
     "$(stat -c '%u:%a' "$NATIVE_APP_IPC_READY_FILE")" == "$(id -u):600" && \
     "$(<"$NATIVE_APP_IPC_READY_FILE")" == \
       "app=org.kde.kcalc path=preload-ipc-gio result=zero-exit" ]] || {
    echo "Echo Wayland shell did not publish its private native-app IPC result" >&2
    exit 1
  }

  NATIVE_APP_IPC_WINDOW_ID=""
  for _attempt in $(seq 1 300); do
    CURRENT_WINDOWS_JSON="$(
      "$KWIN_BRIDGE_SERVICE" --socket "$KWIN_BRIDGE_SOCKET" --request list
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
      echo "Echo Wayland shell received invalid compositor IPC evidence" >&2
      exit 1
    }
    [[ "$MATCH_STATUS" -eq 0 ]] && break
    kill -0 "$SHELL_PID" 2>/dev/null || break
    sleep 0.1
  done
  [[ "$NATIVE_APP_IPC_WINDOW_ID" =~ ^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$ ]] || {
    echo "Echo Wayland IPC succeeded without a new KCalc window" >&2
    exit 1
  }

  "$KWIN_BRIDGE_SERVICE" --socket "$KWIN_BRIDGE_SOCKET" --request close \
    --window-id "$NATIVE_APP_IPC_WINDOW_ID" >/dev/null
  for _attempt in $(seq 1 200); do
    CURRENT_WINDOWS_JSON="$(
      "$KWIN_BRIDGE_SERVICE" --socket "$KWIN_BRIDGE_SOCKET" --request list
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
    [[ "$ABSENT_STATUS" -eq 0 ]] && break
    sleep 0.1
  done
  printf '%s' "$CURRENT_WINDOWS_JSON" | \
    python3 "$WAYLAND_IPC_WINDOW_HELPER" absent \
      --window-id "$NATIVE_APP_IPC_WINDOW_ID" || {
        echo "KCalc window remained after the acknowledged Wayland close" >&2
        exit 1
      }
  rm -f -- "$NATIVE_APP_IPC_READY_FILE"
  echo "ECHO_NATIVE_APP_IPC_READY session=wayland app=org.kde.kcalc path=preload-ipc-gio result=zero-exit cleanup=closed"
fi
printf 'provider=kwin-wayland renderer=ready lock=kscreenlocker auth=ready power=ready notifications=ready input=ready clipboard=ready accessibility=ready\n' \
  >"$READY_TEMP_FILE"
chmod 0600 "$READY_TEMP_FILE"
mv -f -- "$READY_TEMP_FILE" "$READY_FILE"
echo "ECHO_DESKTOP_READY provider=kwin-wayland renderer=ready lock=kscreenlocker auth=ready power=ready notifications=ready input=ready clipboard=ready accessibility=ready"

while kill -0 "$SHELL_PID" 2>/dev/null; do
  if ! "$KWIN_BRIDGE_SERVICE" --socket "$KWIN_BRIDGE_SOCKET" --probe \
       >/dev/null 2>&1; then
    echo "Echo OS KWin window bridge failed; terminating the Wayland shell" >&2
    kill "$SHELL_PID" 2>/dev/null || true
    wait "$SHELL_PID" 2>/dev/null || true
    exit 1
  fi
  if ! name_has_owner org.freedesktop.ScreenSaver; then
    echo "Echo OS KScreenLocker failed; terminating the Wayland shell" >&2
    kill "$SHELL_PID" 2>/dev/null || true
    wait "$SHELL_PID" 2>/dev/null || true
    exit 1
  fi
  if ! kill -0 "$POLKIT_AGENT_PID" 2>/dev/null; then
    wait "$POLKIT_AGENT_PID" || true
    echo "Echo OS PolicyKit authentication agent failed; terminating the Wayland shell" >&2
    kill "$SHELL_PID" 2>/dev/null || true
    wait "$SHELL_PID" 2>/dev/null || true
    exit 1
  fi
  if ! kill -0 "$POWERDEVIL_PID" 2>/dev/null || \
     ! name_has_owner org.kde.Solid.PowerManagement; then
    wait "$POWERDEVIL_PID" || true
    echo "Echo OS power management failed; terminating the Wayland shell" >&2
    kill "$SHELL_PID" 2>/dev/null || true
    wait "$SHELL_PID" 2>/dev/null || true
    exit 1
  fi
  if ! kill -0 "$NOTIFICATION_SERVICE_PID" 2>/dev/null || \
     ! name_has_owner org.freedesktop.Notifications || \
     [[ ! -S "$NOTIFICATION_SOCKET" ]]; then
    wait "$NOTIFICATION_SERVICE_PID" || true
    echo "Echo OS notification service failed; terminating the Wayland shell" >&2
    kill "$SHELL_PID" 2>/dev/null || true
    wait "$SHELL_PID" 2>/dev/null || true
    exit 1
  fi
  if ! kill -0 "$INPUT_METHOD_PID" 2>/dev/null || \
     ! name_has_owner org.fcitx.Fcitx5; then
    wait "$INPUT_METHOD_PID" || true
    echo "Echo OS input method failed; terminating the Wayland shell" >&2
    kill "$SHELL_PID" 2>/dev/null || true
    wait "$SHELL_PID" 2>/dev/null || true
    exit 1
  fi
  if ! kill -0 "$CLIPBOARD_HOST_PID" 2>/dev/null || \
     ! name_has_owner org.kde.klipper || \
     [[ ! -f "$CLIPBOARD_DATABASE" ]]; then
    wait "$CLIPBOARD_HOST_PID" || true
    echo "Echo OS clipboard service failed; terminating the Wayland shell" >&2
    kill "$SHELL_PID" 2>/dev/null || true
    wait "$SHELL_PID" 2>/dev/null || true
    exit 1
  fi
  if [[ -n "${ACCESSIBILITY_BUS_PID:-}" ]] && \
     ! kill -0 "$ACCESSIBILITY_BUS_PID" 2>/dev/null || \
     ! name_has_owner org.a11y.Bus || \
     ! accessibility_address_ready; then
    [[ -z "${ACCESSIBILITY_BUS_PID:-}" ]] || wait "$ACCESSIBILITY_BUS_PID" || true
    echo "Echo OS accessibility bus failed; terminating the Wayland shell" >&2
    kill "$SHELL_PID" 2>/dev/null || true
    wait "$SHELL_PID" 2>/dev/null || true
    exit 1
  fi
  sleep 1
done
wait "$SHELL_PID"
