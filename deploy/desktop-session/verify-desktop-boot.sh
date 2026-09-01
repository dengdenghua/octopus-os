#!/usr/bin/env bash
# Gate systemd boot blessing on the actual KWin + Echo Desktop session.
set -euo pipefail

READY_FILE="${ECHO_DESKTOP_READY_FILE:-/run/echo-os/desktop-ready}"
HEALTH_TIMEOUT_SECONDS="${ECHO_DESKTOP_HEALTH_TIMEOUT_SECONDS:-120}"
SOURCE_IDENTITY_COMMAND=/usr/lib/echo-os/echo-os-source-identity
if [[ "${ECHO_OS_SOURCE_IDENTITY_TEST_MODE:-}" == USE-TEST-INPUTS ]]; then
  SOURCE_IDENTITY_COMMAND="${ECHO_OS_SOURCE_IDENTITY_COMMAND:-$SOURCE_IDENTITY_COMMAND}"
fi
IMAGE_VERSION=unknown
if [[ -r /usr/lib/os-release ]]; then
  # shellcheck disable=SC1091 # The product os-release is generated inside the image.
  . /usr/lib/os-release
fi

[[ "$HEALTH_TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]] || {
  echo "ECHO_DESKTOP_HEALTH_TIMEOUT_SECONDS must be a positive integer" >&2
  exit 2
}
[[ "$SOURCE_IDENTITY_COMMAND" == /* && -x "$SOURCE_IDENTITY_COMMAND" && \
   ! -L "$SOURCE_IDENTITY_COMMAND" ]] || {
  echo "Echo OS immutable source identity reader is unavailable" >&2
  exit 1
}
OS_SOURCE_COMMIT="$("$SOURCE_IDENTITY_COMMAND" --commit)"
[[ "$OS_SOURCE_COMMIT" =~ ^[0-9a-f]{40}$ ]] || {
  echo "Echo OS immutable source identity is invalid" >&2
  exit 1
}

deadline=$((SECONDS + HEALTH_TIMEOUT_SECONDS))
while (( SECONDS < deadline )); do
  if ! systemctl is-active --quiet echo-desktop.service; then
    echo "Echo Desktop service stopped before boot could be blessed" >&2
    exit 1
  fi
  if [[ -f "$READY_FILE" && ! -L "$READY_FILE" ]] && \
       grep -Eq '^provider=ewmh-x11 window=0x[0-9A-Fa-f]+ auth=ready power=ready notifications=ready input=ready clipboard=ready accessibility=ready$' \
       "$READY_FILE"; then
    echo "ECHO_BOOT_HEALTHY version=${IMAGE_VERSION:-unknown} os=$OS_SOURCE_COMMIT $(<"$READY_FILE")"
    exit 0
  fi
  sleep 1
done

echo "Echo Desktop did not become healthy within ${HEALTH_TIMEOUT_SECONDS}s" >&2
exit 1
