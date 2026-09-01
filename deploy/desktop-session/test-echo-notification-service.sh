#!/usr/bin/env bash
# Focused Linux integration test for D-Bus Notify -> private history socket.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SERVICE="$REPO_ROOT/deploy/desktop-session/echo-notification-service"
TEST_RUNTIME="$(mktemp -d)"
SERVICE_LOG="$TEST_RUNTIME/service.log"
chmod 0700 "$TEST_RUNTIME"

cleanup() {
  rm -rf -- "$TEST_RUNTIME"
}
trap cleanup EXIT INT TERM

ECHO_TEST_SERVICE="$SERVICE" \
ECHO_TEST_LOG="$SERVICE_LOG" \
XDG_RUNTIME_DIR="$TEST_RUNTIME" \
dbus-run-session -- bash <<'SESSION'
set -euo pipefail

PRIVATE_DIR="$XDG_RUNTIME_DIR/echo-os"
SOCKET_PATH="$PRIVATE_DIR/notifications.sock"
mkdir -p "$PRIVATE_DIR"
chmod 0700 "$PRIVATE_DIR"
"$ECHO_TEST_SERVICE" --socket "$SOCKET_PATH" --session x11 >"$ECHO_TEST_LOG" 2>&1 &
SERVICE_PID=$!

cleanup_session() {
  kill "$SERVICE_PID" 2>/dev/null || true
  wait "$SERVICE_PID" 2>/dev/null || true
}
trap cleanup_session EXIT INT TERM

for _attempt in $(seq 1 100); do
  if [[ -S "$SOCKET_PATH" ]] && \
     gdbus call --session \
       --dest org.freedesktop.DBus \
       --object-path /org/freedesktop/DBus \
       --method org.freedesktop.DBus.NameHasOwner \
       org.freedesktop.Notifications 2>/dev/null | grep -q true; then
    break
  fi
  kill -0 "$SERVICE_PID"
  sleep 0.05
done

[[ -S "$SOCKET_PATH" && ! -L "$SOCKET_PATH" && \
   "$(stat -c '%u:%a' "$SOCKET_PATH")" == "$(id -u):600" ]]
grep -q 'ECHO_NOTIFICATION_SERVICE_READY provider=echo-native dbus=ready socket=private session=x11' \
  "$ECHO_TEST_LOG"

gdbus call --session \
  --dest org.freedesktop.Notifications \
  --object-path /org/freedesktop/Notifications \
  --method org.freedesktop.Notifications.Notify \
  'Test App' 0 '' 'Build complete' '<b>Safe body</b><br>line two' \
  '[]' '{}' 0 | grep -q 'uint32 1'

python3 - "$SOCKET_PATH" <<'PY'
import json
import socket
import sys


def request(payload: dict[str, object]) -> dict[str, object]:
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(2)
    client.connect(sys.argv[1])
    client.sendall(json.dumps(payload).encode() + b"\n")
    response = b""
    while b"\n" not in response:
        response += client.recv(65536)
    client.close()
    return json.loads(response.split(b"\n", 1)[0])


listed = request({"op": "list"})
assert listed["ok"] is True
assert listed["notifications"] == [
    {
        "id": 1,
        "appName": "Test App",
        "summary": "Build complete",
        "body": "Safe body\nline two",
        "createdAt": listed["notifications"][0]["createdAt"],
        "updatedAt": listed["notifications"][0]["updatedAt"],
    }
]
assert request({"op": "close", "id": 1}) == {"ok": True, "id": 1}
assert request({"op": "list"})["notifications"] == []
PY
SESSION

echo "Echo native notification D-Bus/socket integration tests OK"
