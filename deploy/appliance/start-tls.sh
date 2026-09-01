#!/usr/bin/env bash
set -Eeuo pipefail

deployment_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
tls_directory="$deployment_directory/tls"
docker_bin="${ECHO_DOCKER_BIN:-docker}"
release_env="${ECHO_RELEASE_ENV:-$deployment_directory/echo-release.env}"
appliance_env="${ECHO_APPLIANCE_ENV:-$deployment_directory/appliance.env}"

fail() {
  echo "Echo TLS startup refused: $*" >&2
  exit 1
}

case "${ECHO_TLS_HOST:-}" in
  ""|*://*|*/*|*:*|*','*|*' '*|*'*'*) fail "ECHO_TLS_HOST must be one exact DNS name or IP without scheme, path, port, comma, space or wildcard" ;;
esac

case "${ECHO_TLS_PROXY_IP:-172.30.90.2}" in
  *'*'*|*','*|*' '*) fail "ECHO_TLS_PROXY_IP must be one exact container IP" ;;
esac

validate_port() {
  port_name=$1
  port_value=$2
  [ -n "$port_value" ] || return 0
  case "$port_value" in
    *[!0-9]*) fail "$port_name must be an integer between 1 and 65535" ;;
  esac
  [ "$port_value" -ge 1 ] && [ "$port_value" -le 65535 ] || fail "$port_name must be an integer between 1 and 65535"
}

validate_port ECHO_TLS_HTTP_PORT "${ECHO_TLS_HTTP_PORT:-}"
validate_port ECHO_TLS_HTTPS_PORT "${ECHO_TLS_HTTPS_PORT:-}"

python3 - "${ECHO_TLS_PROXY_IP:-172.30.90.2}" "${ECHO_TLS_SUBNET:-172.30.90.0/24}" "$ECHO_TLS_HOST" <<'PY'
import ipaddress
import re
import sys

try:
    proxy = ipaddress.IPv4Address(sys.argv[1])
    subnet = ipaddress.IPv4Network(sys.argv[2], strict=True)
except ValueError as exc:
    raise SystemExit(f"Echo TLS startup refused: invalid proxy IP/subnet: {exc}")
if proxy not in subnet or proxy in {subnet.network_address, subnet.broadcast_address}:
    raise SystemExit("Echo TLS startup refused: proxy IP must be a usable address inside ECHO_TLS_SUBNET")

host = sys.argv[3]
try:
    ipaddress.IPv4Address(host)
except ValueError:
    label = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
    if len(host) > 253 or "." not in host or any(not label.fullmatch(part) for part in host.split(".")):
        raise SystemExit("Echo TLS startup refused: ECHO_TLS_HOST must be a valid ASCII DNS name or IPv4 address")
PY

"$tls_directory/verify-tls-assets.sh" \
  "$tls_directory/echo.crt" \
  "$tls_directory/echo.key" \
  "$ECHO_TLS_HOST"

export ECHO_BIND_ADDRESS=127.0.0.1
export ECHO_APPLIANCE_TRUSTED_HOSTS=$ECHO_TLS_HOST
export ECHO_APPLIANCE_TRUSTED_ORIGINS=https://$ECHO_TLS_HOST${ECHO_TLS_HTTPS_PORT:+:$ECHO_TLS_HTTPS_PORT}

cd "$deployment_directory"
compose=("$docker_bin" compose)
if [[ -e "$appliance_env" ]]; then
  [[ -f "$appliance_env" && ! -L "$appliance_env" ]] ||
    fail "appliance environment file is unsafe"
  compose+=(--env-file "$appliance_env")
fi
if [[ -e "$release_env" ]]; then
  [[ -f "$release_env" && ! -L "$release_env" ]] ||
    fail "release environment file is unsafe"
  compose+=(--env-file "$release_env")
fi
compose+=(-f docker-compose.yml -f docker-compose.tls.yml)
"${compose[@]}" config --quiet
exec "${compose[@]}" up -d "$@"
