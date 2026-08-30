#!/usr/bin/env bash
set -euo pipefail

STATE_DIR="$(cd "$(dirname "$0")" && pwd)"
PREPARE="$STATE_DIR/echo-network-state-prepare"
TEST_ROOT="$(mktemp -d)"
cleanup() { rm -rf -- "$TEST_ROOT"; }
trap cleanup EXIT INT TERM

LEGACY="$TEST_ROOT/etc-connections"
PERSISTENT="$TEST_ROOT/var-connections"
MARKERS="$TEST_ROOT/echo-state"
mkdir -p "$LEGACY" "$PERSISTENT"
printf '[connection]\nid=Legacy Wi-Fi\ntype=wifi\n' >"$LEGACY/legacy.nmconnection"
chmod 0600 "$LEGACY/legacy.nmconnection"
printf '[connection]\nid=Ignored\ntype=wifi\n' >"$LEGACY/insecure.nmconnection"
chmod 0644 "$LEGACY/insecure.nmconnection"
ln -s legacy.nmconnection "$LEGACY/symlink.nmconnection"

run_prepare() {
  ECHO_NETWORK_STATE_TESTING=yes \
  ECHO_NETWORK_EXPECTED_OWNER_UID="$(id -u)" \
  ECHO_NETWORK_LEGACY_DIRECTORY="$LEGACY" \
  ECHO_NETWORK_PERSISTENT_DIRECTORY="$PERSISTENT" \
  ECHO_NETWORK_STATE_DIRECTORY="$MARKERS" \
    "$PREPARE"
}

run_prepare
cmp "$LEGACY/legacy.nmconnection" "$PERSISTENT/legacy.nmconnection"
[[ ! -e "$PERSISTENT/insecure.nmconnection" ]]
[[ ! -e "$PERSISTENT/symlink.nmconnection" ]]
grep -qx 'migrated=1' "$MARKERS/network-state-v1"
grep -qx 'ignored=1' "$MARKERS/network-state-v1"

# A completed migration never overwrites the persistent copy with later root
# content. Subsequent service runs only verify that persistent storage exists.
printf '[connection]\nid=Changed Root Copy\ntype=wifi\n' \
  >"$LEGACY/legacy.nmconnection"
chmod 0600 "$LEGACY/legacy.nmconnection"
run_prepare
grep -q '^id=Legacy Wi-Fi$' "$PERSISTENT/legacy.nmconnection"

echo "Echo OS persistent NetworkManager state tests OK"
