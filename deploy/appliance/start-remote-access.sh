#!/usr/bin/env bash
set -Eeuo pipefail

deployment_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
docker_bin="${ECHO_DOCKER_BIN:-docker}"
release_env="${ECHO_RELEASE_ENV:-$deployment_directory/echo-release.env}"
appliance_env="${ECHO_APPLIANCE_ENV:-$deployment_directory/appliance.env}"
auth_key_input="${1:-${ECHO_TAILSCALE_AUTHKEY_FILE:-}}"

fail() {
  echo "Echo remote-access startup refused: $*" >&2
  exit 1
}

[[ -n "${ECHO_TAILSCALE_DNS_NAME:-}" ]] ||
  fail "ECHO_TAILSCALE_DNS_NAME is required (for example echo-os.example.ts.net)"
[[ -n "$auth_key_input" ]] ||
  fail "pass the Tailscale auth-key file as the first argument"
[[ "$auth_key_input" == /* ]] || auth_key_input="$PWD/$auth_key_input"
[[ -f "$auth_key_input" && ! -L "$auth_key_input" ]] ||
  fail "the Tailscale auth key must be a regular non-symlink file"

if key_mode=$(stat -f '%Lp' "$auth_key_input" 2>/dev/null); then
  :
else
  key_mode=$(stat -c '%a' "$auth_key_input" 2>/dev/null) ||
    fail "cannot read Tailscale auth-key permissions"
fi
case "$key_mode" in
  400|600) ;;
  *) fail "Tailscale auth-key permissions must be 0400 or 0600 (observed $key_mode)" ;;
esac

python3 - \
  "$ECHO_TAILSCALE_DNS_NAME" \
  "$auth_key_input" \
  "${ECHO_TAILSCALE_PROXY_IP:-172.30.91.2}" \
  "${ECHO_TAILSCALE_SUBNET:-172.30.91.0/24}" \
  "${ECHO_TAILSCALE_HOSTNAME:-echo-os}" <<'PY'
import ipaddress
import os
import re
import sys

hostname = sys.argv[1].rstrip(".").casefold()
label = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
if (
    len(hostname) > 253
    or hostname == "ts.net"
    or not hostname.endswith(".ts.net")
    or any(not label.fullmatch(part) for part in hostname.split("."))
):
    raise SystemExit(
        "Echo remote-access startup refused: ECHO_TAILSCALE_DNS_NAME must be one exact *.ts.net DNS name"
    )

with open(sys.argv[2], "rb") as handle:
    key = handle.read(513)
if not 20 <= len(key) <= 512 or b"\n" in key or b"\r" in key or b"\0" in key:
    raise SystemExit(
        "Echo remote-access startup refused: Tailscale auth-key file has an invalid shape"
    )
if not key.startswith(b"tskey-auth-") or not all(0x21 <= byte <= 0x7E for byte in key):
    raise SystemExit(
        "Echo remote-access startup refused: Tailscale auth-key file has an invalid shape"
    )

try:
    proxy = ipaddress.IPv4Address(sys.argv[3])
    subnet = ipaddress.IPv4Network(sys.argv[4], strict=True)
except ValueError as exc:
    raise SystemExit(f"Echo remote-access startup refused: invalid proxy IP/subnet: {exc}")
if proxy not in subnet or proxy in {subnet.network_address, subnet.broadcast_address}:
    raise SystemExit(
        "Echo remote-access startup refused: proxy IP must be a usable address inside ECHO_TAILSCALE_SUBNET"
    )

requested_hostname = sys.argv[5].casefold()
if not label.fullmatch(requested_hostname):
    raise SystemExit(
        "Echo remote-access startup refused: ECHO_TAILSCALE_HOSTNAME must be one ASCII DNS label"
    )
PY

export ECHO_TAILSCALE_AUTHKEY_FILE
ECHO_TAILSCALE_AUTHKEY_FILE="$(cd -- "$(dirname -- "$auth_key_input")" && pwd -P)/$(basename -- "$auth_key_input")"
export ECHO_TAILSCALE_DNS_NAME

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
compose+=(-f docker-compose.yml -f docker-compose.remote-access.yml)
"${compose[@]}" config --quiet
exec "${compose[@]}" up -d "${@:2}"
