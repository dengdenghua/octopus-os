#!/usr/bin/env bash
# Production boot health: require SDDM and its seat0 greeter/session before blessing.
set -euo pipefail

HEALTH_TIMEOUT_SECONDS="${ECHO_LOGIN_HEALTH_TIMEOUT_SECONDS:-120}"
SOURCE_IDENTITY_COMMAND=/usr/lib/echo-os/echo-os-source-identity
if [[ "${ECHO_OS_SOURCE_IDENTITY_TEST_MODE:-}" == USE-TEST-INPUTS ]]; then
  SOURCE_IDENTITY_COMMAND="${ECHO_OS_SOURCE_IDENTITY_COMMAND:-$SOURCE_IDENTITY_COMMAND}"
fi
IMAGE_VERSION=unknown
if [[ -r /usr/lib/os-release ]]; then
  # shellcheck disable=SC1091 # Generated product identity.
  . /usr/lib/os-release
fi
[[ "$HEALTH_TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]] || {
  echo "ECHO_LOGIN_HEALTH_TIMEOUT_SECONDS must be a positive integer" >&2
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
  if systemctl is-failed --quiet sddm.service; then
    echo "SDDM failed before the login screen became healthy" >&2
    exit 1
  fi
  if systemctl is-active --quiet sddm.service && \
     loginctl list-sessions --no-legend --no-pager |
       awk '($3 == "sddm" || $3 == "echo") && $4 == "seat0" { found=1 } END { exit !found }'; then
    echo "ECHO_LOGIN_READY version=${IMAGE_VERSION:-unknown} os=$OS_SOURCE_COMMIT provider=sddm-x11 seat=seat0"
    exit 0
  fi
  sleep 1
done

echo "SDDM did not establish a seat0 greeter within ${HEALTH_TIMEOUT_SECONDS}s" >&2
exit 1
