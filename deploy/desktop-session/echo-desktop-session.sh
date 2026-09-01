#!/usr/bin/env bash
# Echo OS target C session: Xorg -> KWin -> interactive Echo desktop shell.
set -euo pipefail

OS_DIR="${ECHO_OS_DIR:-/opt/echo-os}"
APP_DIR="$OS_DIR/frontend"
DEFAULT_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/echo-os"
READY_FILE="${ECHO_DESKTOP_READY_FILE:-$DEFAULT_RUNTIME_DIR/desktop-ready}"
READY_TEMP_FILE="${READY_FILE}.$$"
NATIVE_APP_IPC_READY_FILE="$DEFAULT_RUNTIME_DIR/native-app-ipc-ready"
LOCK_SERVICE=/usr/lib/echo-os/echo-session-lock
KWIN_BRIDGE_SERVICE=/usr/lib/echo-os/echo-kwin-window-bridge
KWIN_BRIDGE_SOCKET="$DEFAULT_RUNTIME_DIR/kwin-window-bridge.sock"
POLKIT_AGENT=/usr/lib/x86_64-linux-gnu/libexec/polkit-kde-authentication-agent-1
POWERDEVIL=/usr/lib/x86_64-linux-gnu/libexec/org_kde_powerdevil
NOTIFICATION_SERVICE=/usr/lib/echo-os/echo-notification-service
NOTIFICATION_SOCKET="$DEFAULT_RUNTIME_DIR/notifications.sock"
INPUT_METHOD=/usr/bin/fcitx5
CLIPBOARD_HOST=/usr/lib/echo-os/echo-clipboard-host
CLIPBOARD_DATABASE="$DEFAULT_RUNTIME_DIR/clipboard/history3.sqlite"
ACCESSIBILITY_BUS=/usr/libexec/at-spi-bus-launcher
ACCESSIBILITY_PROBE=/usr/lib/echo-os/echo-accessibility-smoke.py
CORE_APPS_SESSION_SMOKE=/usr/lib/echo-os/echo-core-apps-session-smoke.py

export ECHO_NATIVE_SHELL=1
export ECHO_SHELL_MODE=desktop
export ECHO_BACKEND_URL="${ECHO_BACKEND_URL:-http://127.0.0.1:8000}"
export XDG_SESSION_TYPE=x11
export XDG_SESSION_DESKTOP=echo
export XDG_CURRENT_DESKTOP=Echo:KDE
export QT_QPA_PLATFORM=xcb
export GTK_IM_MODULE=fcitx
export QT_IM_MODULE=fcitx
export XMODIFIERS=@im=fcitx
export SDL_IM_MODULE=fcitx
export QT_ACCESSIBILITY=1

# Flatpak exports application launchers and icons outside the immutable root's
# ordinary /usr/share tree. Keep both installation scopes visible to the Echo
# launcher; /var and /home survive an A/B root replacement.
XDG_DATA_HOME_EFFECTIVE="${XDG_DATA_HOME:-${HOME:-/home/echo}/.local/share}"
XDG_DATA_DIRS_DEFAULT=/usr/local/share:/usr/share
export XDG_DATA_DIRS="$XDG_DATA_HOME_EFFECTIVE/flatpak/exports/share:/var/lib/flatpak/exports/share:${XDG_DATA_DIRS:-$XDG_DATA_DIRS_DEFAULT}"

# systemd/PAM gives the session a seat but not every minimal image starts a
# user D-Bus. KWin needs one; re-enter exactly once under dbus-run-session.
if [[ -z "${DBUS_SESSION_BUS_ADDRESS:-}" && "${ECHO_DBUS_SESSION:-0}" != "1" ]]; then
  export ECHO_DBUS_SESSION=1
  exec dbus-run-session -- "$0"
fi

# D-Bus-activated applications do not necessarily inherit the shell process
# environment. Publish only the fixed input-module and accessibility variables;
# import them into the systemd user manager in a normal PAM/SDDM session.
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

session_name_has_owner() {
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

cleanup() {
  rm -f -- "$READY_FILE" "$READY_TEMP_FILE" "$NATIVE_APP_IPC_READY_FILE"
  [[ -n "${SHELL_PID:-}" ]] && kill "$SHELL_PID" 2>/dev/null || true
  [[ -n "${LOCK_SERVICE_PID:-}" ]] && kill "$LOCK_SERVICE_PID" 2>/dev/null || true
  [[ -n "${KWIN_PID:-}" ]] && kill "$KWIN_PID" 2>/dev/null || true
  [[ -n "${KWIN_BRIDGE_PID:-}" ]] && kill "$KWIN_BRIDGE_PID" 2>/dev/null || true
  [[ -n "${POLKIT_AGENT_PID:-}" ]] && kill "$POLKIT_AGENT_PID" 2>/dev/null || true
  [[ -n "${POWERDEVIL_PID:-}" ]] && kill "$POWERDEVIL_PID" 2>/dev/null || true
  [[ -n "${NOTIFICATION_SERVICE_PID:-}" ]] && kill "$NOTIFICATION_SERVICE_PID" 2>/dev/null || true
  [[ -n "${INPUT_METHOD_PID:-}" ]] && kill "$INPUT_METHOD_PID" 2>/dev/null || true
  [[ -n "${CLIPBOARD_HOST_PID:-}" ]] && kill "$CLIPBOARD_HOST_PID" 2>/dev/null || true
  [[ -n "${ACCESSIBILITY_BUS_PID:-}" ]] && kill "$ACCESSIBILITY_BUS_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM
for command_name in gdbus python3 stat timeout; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "Echo OS session dependency missing: $command_name" >&2
    exit 1
  }
done
mkdir -p "$DEFAULT_RUNTIME_DIR"
chmod 0700 "$DEFAULT_RUNTIME_DIR"
[[ -d "$DEFAULT_RUNTIME_DIR" && ! -L "$DEFAULT_RUNTIME_DIR" && \
   "$(stat -c '%u:%a' "$DEFAULT_RUNTIME_DIR")" == "$(id -u):700" ]] || {
  echo "Echo OS private runtime directory is unsafe" >&2
  exit 1
}
mkdir -p "$(dirname "$READY_FILE")"
rm -f -- "$READY_FILE" "$READY_TEMP_FILE"

[[ -x "$ACCESSIBILITY_BUS" ]] || {
  echo "Echo OS AT-SPI bus launcher is missing: $ACCESSIBILITY_BUS" >&2
  exit 1
}
if ! session_name_has_owner org.a11y.Bus; then
  "$ACCESSIBILITY_BUS" --launch-immediately &
  ACCESSIBILITY_BUS_PID=$!
fi
for _attempt in $(seq 1 100); do
  if session_name_has_owner org.a11y.Bus && accessibility_address_ready; then
    break
  fi
  if [[ -n "${ACCESSIBILITY_BUS_PID:-}" ]] && \
     ! kill -0 "$ACCESSIBILITY_BUS_PID" 2>/dev/null; then
    wait "$ACCESSIBILITY_BUS_PID" || true
    echo "Echo OS AT-SPI bus exited during X11 startup" >&2
    exit 1
  fi
  sleep 0.1
done
session_name_has_owner org.a11y.Bus && accessibility_address_ready || {
  echo "Echo OS AT-SPI accessibility bus did not become ready" >&2
  exit 1
}
echo "ECHO_ACCESSIBILITY_READY provider=at-spi2 dbus=ready qt=enabled session=x11"

[[ -x "$NOTIFICATION_SERVICE" ]] || {
  echo "Echo OS notification service is missing: $NOTIFICATION_SERVICE" >&2
  exit 1
}
"$NOTIFICATION_SERVICE" --socket "$NOTIFICATION_SOCKET" --session x11 &
NOTIFICATION_SERVICE_PID=$!
for _attempt in $(seq 1 100); do
  if session_name_has_owner org.freedesktop.Notifications && \
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
session_name_has_owner org.freedesktop.Notifications && \
  [[ -S "$NOTIFICATION_SOCKET" && ! -L "$NOTIFICATION_SOCKET" && \
     "$(stat -c '%u:%a' "$NOTIFICATION_SOCKET")" == "$(id -u):600" ]] || {
    echo "Echo OS notification service did not establish its private bridge" >&2
    exit 1
  }
export ECHO_NOTIFICATION_SOCKET="$NOTIFICATION_SOCKET"
echo "ECHO_NOTIFICATION_SERVICE_READY provider=echo-native dbus=ready socket=private session=x11"

[[ -x "$KWIN_BRIDGE_SERVICE" ]] || {
  echo "Echo OS KWin window bridge is missing: $KWIN_BRIDGE_SERVICE" >&2
  exit 1
}
"$KWIN_BRIDGE_SERVICE" --socket "$KWIN_BRIDGE_SOCKET" &
KWIN_BRIDGE_PID=$!
for _attempt in $(seq 1 50); do
  [[ -S "$KWIN_BRIDGE_SOCKET" ]] && break
  if ! kill -0 "$KWIN_BRIDGE_PID" 2>/dev/null; then
    wait "$KWIN_BRIDGE_PID" || true
    echo "Echo OS KWin window bridge exited during startup" >&2
    exit 1
  fi
  sleep 0.1
done
[[ -S "$KWIN_BRIDGE_SOCKET" ]] || {
  echo "Echo OS KWin window bridge socket did not become ready" >&2
  exit 1
}
export ECHO_KWIN_WINDOW_BRIDGE_SOCKET="$KWIN_BRIDGE_SOCKET"

# Echo's top shell publishes bounded glass regions through the KDE blur-behind
# property. Make the compositor effect deterministic before KWin starts; the
# renderer still keeps WebGL/SVG as its fallback when compositing is absent.
KWIN_CONFIG_TOOL="$(command -v kwriteconfig6 || command -v kwriteconfig5 || true)"
[[ -n "$KWIN_CONFIG_TOOL" ]] || {
  echo "KWin configuration tool is missing; native glass cannot be enabled" >&2
  exit 1
}
"$KWIN_CONFIG_TOOL" --file kwinrc --group Plugins --key blurEnabled true
echo "ECHO_KWIN_GLASS_EFFECT_READY provider=kwin-blur region=bounded"

xsetroot -solid "#101826" || true
kwin_x11 --replace &
KWIN_PID=$!

for _attempt in $(seq 1 80); do
  if wmctrl -m >/dev/null 2>&1; then break; fi
  if ! kill -0 "$KWIN_PID" 2>/dev/null; then
    echo "KWin exited before the window-manager contract became ready" >&2
    exit 1
  fi
  sleep 0.1
done
wmctrl -m >/dev/null 2>&1 || {
  echo "KWin window-manager contract did not become ready" >&2
  exit 1
}

[[ -x "$INPUT_METHOD" ]] || {
  echo "Echo OS Fcitx5 input method is missing: $INPUT_METHOD" >&2
  exit 1
}
"$INPUT_METHOD" --replace &
INPUT_METHOD_PID=$!
for _attempt in $(seq 1 100); do
  if session_name_has_owner org.fcitx.Fcitx5; then break; fi
  if ! kill -0 "$INPUT_METHOD_PID" 2>/dev/null; then
    wait "$INPUT_METHOD_PID" || true
    echo "Echo OS Fcitx5 exited during X11 startup" >&2
    exit 1
  fi
  sleep 0.1
done
session_name_has_owner org.fcitx.Fcitx5 || {
  echo "Echo OS Fcitx5 did not register its session service" >&2
  exit 1
}
echo "ECHO_INPUT_METHOD_READY provider=fcitx5 dbus=ready frontend=x11"

[[ -x "$CLIPBOARD_HOST" ]] || {
  echo "Echo OS windowless Klipper host is missing: $CLIPBOARD_HOST" >&2
  exit 1
}
"$CLIPBOARD_HOST" --session x11 --database "$CLIPBOARD_DATABASE" &
CLIPBOARD_HOST_PID=$!
for _attempt in $(seq 1 100); do
  if session_name_has_owner org.kde.klipper && \
     [[ -f "$CLIPBOARD_DATABASE" ]]; then
    break
  fi
  if ! kill -0 "$CLIPBOARD_HOST_PID" 2>/dev/null; then
    wait "$CLIPBOARD_HOST_PID" || true
    echo "Echo OS Klipper host exited during X11 startup" >&2
    exit 1
  fi
  sleep 0.1
done
session_name_has_owner org.kde.klipper && \
  [[ -f "$CLIPBOARD_DATABASE" && ! -L "$CLIPBOARD_DATABASE" && \
     "$(stat -c '%u:%a' "$CLIPBOARD_DATABASE")" == "$(id -u):600" ]] || {
    echo "Echo OS Klipper did not establish a private runtime clipboard" >&2
    exit 1
  }
echo "ECHO_CLIPBOARD_READY provider=klipper-qml dbus=ready storage=runtime-tmpfs persistence=off session=x11"

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
echo "ECHO_AUTH_AGENT_READY provider=polkit-kde session=x11"

command -v gdbus >/dev/null 2>&1 || {
  echo "Echo OS power-management D-Bus probe is missing: gdbus" >&2
  exit 1
}
[[ -x "$POWERDEVIL" ]] || {
  echo "Echo OS PowerDevil runtime is missing: $POWERDEVIL" >&2
  exit 1
}
"$POWERDEVIL" &
POWERDEVIL_PID=$!
for _attempt in $(seq 1 100); do
  if session_name_has_owner org.kde.Solid.PowerManagement && \
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
session_name_has_owner org.kde.Solid.PowerManagement && \
  system_name_has_owner org.freedesktop.UPower && \
  system_name_has_owner net.hadess.PowerProfiles || {
    echo "Echo OS power-management D-Bus chain did not become ready" >&2
    exit 1
  }
echo "ECHO_POWER_MANAGEMENT_READY provider=powerdevil upower=ready profiles=ready session=x11"

# The same KWin script and UUID/action transport is used by the future Wayland
# session. Exercise it under KWin X11 now so it cannot silently rot behind the
# EWMH bring-up provider.
for _attempt in $(seq 1 50); do
  if "$KWIN_BRIDGE_SERVICE" --socket "$KWIN_BRIDGE_SOCKET" --probe \
       >/dev/null 2>&1; then
    break
  fi
  if ! kill -0 "$KWIN_PID" 2>/dev/null || \
     ! kill -0 "$KWIN_BRIDGE_PID" 2>/dev/null; then
    echo "KWin compositor bridge failed before publishing its first snapshot" >&2
    exit 1
  fi
  sleep 0.1
done
"$KWIN_BRIDGE_SERVICE" --socket "$KWIN_BRIDGE_SOCKET" --probe \
  >/dev/null 2>&1 || {
    echo "KWin compositor bridge did not publish a window snapshot" >&2
    exit 1
  }
echo "ECHO_KWIN_COMPOSITOR_BRIDGE_READY provider=kwin-script transport=private-socket"

[[ -x "$LOCK_SERVICE" ]] || {
  echo "Echo OS session lock service is missing: $LOCK_SERVICE" >&2
  exit 1
}
"$LOCK_SERVICE" &
LOCK_SERVICE_PID=$!
sleep 0.2
kill -0 "$LOCK_SERVICE_PID" 2>/dev/null || {
  wait "$LOCK_SERVICE_PID" || true
  echo "Echo OS session lock service exited before the desktop started" >&2
  exit 1
}
export ECHO_LOCK_SCREEN_READY=1
echo "ECHO_LOCK_SERVICE_READY provider=xss-lock pam=echo-lock idle=${ECHO_LOCK_IDLE_SECONDS:-600}"

NATIVE_APP_IPC_TEST=0
NATIVE_APP_IPC_BASELINE_IDS=""
if [[ -n "${CREDENTIALS_DIRECTORY:-}" && \
      -f "$CREDENTIALS_DIRECTORY/echo.os.ci-session" ]]; then
  NATIVE_APP_IPC_TEST=1
  NATIVE_APP_IPC_BASELINE_IDS="$(
    wmctrl -l -x -p 2>/dev/null | \
      awk 'tolower($5) ~ /(^|[.])kcalc([.]|$)/ { print tolower($1) }'
  )"
  rm -f -- "$NATIVE_APP_IPC_READY_FILE"
  export ECHO_NATIVE_APP_SMOKE_ID=org.kde.kcalc
  export ECHO_NATIVE_APP_IPC_READY_FILE="$NATIVE_APP_IPC_READY_FILE"
else
  unset ECHO_NATIVE_APP_SMOKE_ID ECHO_NATIVE_APP_IPC_READY_FILE
fi

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
    # Developer fallback. The installer requires a packaged binary, so a
    # production device does not normally take this branch.
    SHELL_COMMAND=(npx --no-install electron electron/main.cjs)
  fi
fi

"${SHELL_COMMAND[@]}" \
  --class=echo-shell \
  --ozone-platform=x11 \
  --force-renderer-accessibility &
SHELL_PID=$!

# Keep the interactive shell below native applications. Electron also marks it
# skip-taskbar; EWMH here makes the session behavior explicit at the WM layer.
for _attempt in $(seq 1 100); do
  SHELL_WINDOW_ID="$(
    wmctrl -lx | awk 'tolower($0) ~ /echo-shell|echo-os-desktop/ { print $1; exit }'
  )"
  if [[ -n "$SHELL_WINDOW_ID" ]]; then
    wmctrl -ir "$SHELL_WINDOW_ID" -b remove,above,fullscreen || true
    wmctrl -ir "$SHELL_WINDOW_ID" -b add,below,skip_taskbar,skip_pager || true
    break
  fi
  if ! kill -0 "$SHELL_PID" 2>/dev/null; then
    wait "$SHELL_PID"
    exit $?
  fi
  sleep 0.1
done

[[ -n "${SHELL_WINDOW_ID:-}" ]] || {
  echo "Echo Desktop did not register a window with KWin" >&2
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
printf 'provider=ewmh-x11 window=%s auth=ready power=ready notifications=ready input=ready clipboard=ready accessibility=ready\n' \
  "$SHELL_WINDOW_ID" >"$READY_TEMP_FILE"
chmod 0600 "$READY_TEMP_FILE"
mv -f -- "$READY_TEMP_FILE" "$READY_FILE"
echo "ECHO_DESKTOP_READY provider=ewmh-x11 window=$SHELL_WINDOW_ID auth=ready power=ready notifications=ready input=ready clipboard=ready accessibility=ready"

# The credential-gated direct VM session actively requests one real logind lock
# after the renderer window exists. The xss-lock adapter emits a second marker
# only when XSecureLock is actually launched. Production SDDM sessions do not
# receive this credential and lock only on user, idle or sleep events.
if [[ -n "${CREDENTIALS_DIRECTORY:-}" && \
      -f "$CREDENTIALS_DIRECTORY/echo.os.ci-session" ]]; then
  [[ "$NATIVE_APP_IPC_TEST" -eq 1 ]] || {
    echo "native-app IPC test state was not initialized" >&2
    exit 1
  }
  for _attempt in $(seq 1 300); do
    [[ -f "$NATIVE_APP_IPC_READY_FILE" ]] && break
    kill -0 "$SHELL_PID" 2>/dev/null || {
      echo "Echo Desktop exited before completing native-app IPC smoke" >&2
      exit 1
    }
    sleep 0.1
  done
  [[ -f "$NATIVE_APP_IPC_READY_FILE" && ! -L "$NATIVE_APP_IPC_READY_FILE" && \
     "$(stat -c '%u:%a' "$NATIVE_APP_IPC_READY_FILE")" == "$(id -u):600" && \
     "$(<"$NATIVE_APP_IPC_READY_FILE")" == \
       "app=org.kde.kcalc path=preload-ipc-gio result=zero-exit" ]] || {
    echo "Echo Desktop did not publish its private native-app IPC result" >&2
    exit 1
  }
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
    kill -0 "$SHELL_PID" 2>/dev/null || break
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
  [[ -x "$CORE_APPS_SESSION_SMOKE" ]] || {
    echo "Echo OS core-application session smoke is missing" >&2
    exit 1
  }
  ECHO_CORE_APPS_SESSION_TEST=USE-EPHEMERAL-RUNTIME \
    timeout 360s "$CORE_APPS_SESSION_SMOKE" --session x11
  /usr/bin/loginctl lock-session self
fi

# A session without its lock handler is not safe to leave running. Return to
# SDDM (or let the credential service restart) if xss-lock ever exits first.
while kill -0 "$SHELL_PID" 2>/dev/null; do
  if ! kill -0 "$LOCK_SERVICE_PID" 2>/dev/null; then
    wait "$LOCK_SERVICE_PID" || true
    echo "Echo OS lock service failed; terminating the graphical session" >&2
    kill "$SHELL_PID" 2>/dev/null || true
    wait "$SHELL_PID" 2>/dev/null || true
    exit 1
  fi
  if ! kill -0 "$KWIN_BRIDGE_PID" 2>/dev/null; then
    wait "$KWIN_BRIDGE_PID" || true
    echo "Echo OS KWin window bridge failed; terminating the graphical session" >&2
    kill "$SHELL_PID" 2>/dev/null || true
    wait "$SHELL_PID" 2>/dev/null || true
    exit 1
  fi
  if ! kill -0 "$POLKIT_AGENT_PID" 2>/dev/null; then
    wait "$POLKIT_AGENT_PID" || true
    echo "Echo OS PolicyKit authentication agent failed; terminating the graphical session" >&2
    kill "$SHELL_PID" 2>/dev/null || true
    wait "$SHELL_PID" 2>/dev/null || true
    exit 1
  fi
  if ! kill -0 "$POWERDEVIL_PID" 2>/dev/null || \
     ! session_name_has_owner org.kde.Solid.PowerManagement; then
    wait "$POWERDEVIL_PID" || true
    echo "Echo OS power management failed; terminating the graphical session" >&2
    kill "$SHELL_PID" 2>/dev/null || true
    wait "$SHELL_PID" 2>/dev/null || true
    exit 1
  fi
  if ! kill -0 "$NOTIFICATION_SERVICE_PID" 2>/dev/null || \
     ! session_name_has_owner org.freedesktop.Notifications || \
     [[ ! -S "$NOTIFICATION_SOCKET" ]]; then
    wait "$NOTIFICATION_SERVICE_PID" || true
    echo "Echo OS notification service failed; terminating the graphical session" >&2
    kill "$SHELL_PID" 2>/dev/null || true
    wait "$SHELL_PID" 2>/dev/null || true
    exit 1
  fi
  if ! kill -0 "$INPUT_METHOD_PID" 2>/dev/null || \
     ! session_name_has_owner org.fcitx.Fcitx5; then
    wait "$INPUT_METHOD_PID" || true
    echo "Echo OS input method failed; terminating the graphical session" >&2
    kill "$SHELL_PID" 2>/dev/null || true
    wait "$SHELL_PID" 2>/dev/null || true
    exit 1
  fi
  if ! kill -0 "$CLIPBOARD_HOST_PID" 2>/dev/null || \
     ! session_name_has_owner org.kde.klipper || \
     [[ ! -f "$CLIPBOARD_DATABASE" ]]; then
    wait "$CLIPBOARD_HOST_PID" || true
    echo "Echo OS clipboard service failed; terminating the graphical session" >&2
    kill "$SHELL_PID" 2>/dev/null || true
    wait "$SHELL_PID" 2>/dev/null || true
    exit 1
  fi
  if [[ -n "${ACCESSIBILITY_BUS_PID:-}" ]] && \
     ! kill -0 "$ACCESSIBILITY_BUS_PID" 2>/dev/null || \
     ! session_name_has_owner org.a11y.Bus || \
     ! accessibility_address_ready; then
    [[ -z "${ACCESSIBILITY_BUS_PID:-}" ]] || wait "$ACCESSIBILITY_BUS_PID" || true
    echo "Echo OS accessibility bus failed; terminating the graphical session" >&2
    kill "$SHELL_PID" 2>/dev/null || true
    wait "$SHELL_PID" 2>/dev/null || true
    exit 1
  fi
  sleep 1
done
wait "$SHELL_PID"
