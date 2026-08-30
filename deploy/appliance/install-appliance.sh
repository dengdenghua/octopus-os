#!/usr/bin/env bash
# Install one immutable Echo appliance release from its verified operations bundle.

set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
DOCKER_BIN="${ECHO_DOCKER_BIN:-docker}"
RELEASE_ENV="${ECHO_RELEASE_ENV:-$SCRIPT_DIR/echo-release.env}"
APPLIANCE_ENV="${ECHO_APPLIANCE_ENV:-$SCRIPT_DIR/appliance.env}"
WAIT_TIMEOUT="${ECHO_INSTALL_WAIT_TIMEOUT:-180}"

fail() {
  printf 'Echo appliance installation failed: %s\n' "$*" >&2
  exit 1
}

[[ "$WAIT_TIMEOUT" =~ ^[0-9]+$ ]] || fail "ECHO_INSTALL_WAIT_TIMEOUT must be an integer"
((WAIT_TIMEOUT >= 30 && WAIT_TIMEOUT <= 1800)) ||
  fail "ECHO_INSTALL_WAIT_TIMEOUT must be between 30 and 1800 seconds"
command -v "$DOCKER_BIN" >/dev/null 2>&1 || fail "Docker is required on the NAS host"
"$DOCKER_BIN" compose version >/dev/null 2>&1 || fail "Docker Compose v2 is required"

[[ -f "$RELEASE_ENV" && ! -L "$RELEASE_ENV" ]] ||
  fail "echo-release.env is missing or unsafe"
release_image=""
while IFS= read -r line || [[ -n "$line" ]]; do
  case "$line" in
    "" | \#*) ;;
    ECHO_OS_IMAGE=*)
      [[ -z "$release_image" ]] || fail "echo-release.env declares ECHO_OS_IMAGE more than once"
      release_image="${line#ECHO_OS_IMAGE=}"
      ;;
    *) fail "echo-release.env contains an unsupported setting" ;;
  esac
done <"$RELEASE_ENV"
[[ "$release_image" =~ ^[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64}$ ]] ||
  fail "echo-release.env must select one immutable registry image"

compose=("$DOCKER_BIN" compose)
if [[ -e "$APPLIANCE_ENV" ]]; then
  [[ -f "$APPLIANCE_ENV" && ! -L "$APPLIANCE_ENV" ]] ||
    fail "appliance.env is unsafe"
  if grep -Eq '^[[:space:]]*ECHO_OS_IMAGE[[:space:]]*=' "$APPLIANCE_ENV"; then
    fail "appliance.env must not override ECHO_OS_IMAGE"
  fi
  compose+=(--env-file "$APPLIANCE_ENV")
fi
compose+=(
  --env-file "$RELEASE_ENV"
  --project-directory "$SCRIPT_DIR"
  -f "$SCRIPT_DIR/docker-compose.yml"
)

"${compose[@]}" config --quiet
"$DOCKER_BIN" pull "$release_image"
"${compose[@]}" up -d --no-build --wait --wait-timeout "$WAIT_TIMEOUT"

main_image="$("$DOCKER_BIN" inspect --format '{{.Config.Image}}' echo-os)"
proxy_image="$("$DOCKER_BIN" inspect --format '{{.Config.Image}}' echo-docker-control)"
[[ "$main_image" == "$release_image" && "$proxy_image" == "$release_image" ]] ||
  fail "running containers do not both use the selected immutable image"

printf 'Echo appliance is healthy at the immutable image: %s\n' "$release_image"
