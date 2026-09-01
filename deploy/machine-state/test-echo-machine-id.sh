#!/usr/bin/env bash
set -euo pipefail

STATE_DIR="$(cd "$(dirname "$0")" && pwd)"
MACHINE_ID_SCRIPT="$STATE_DIR/echo-machine-id"
TEST_ROOT="$(mktemp -d)"
cleanup() { rm -rf -- "$TEST_ROOT"; }
trap cleanup EXIT INT TERM

RANDOM_SOURCE="$TEST_ROOT/random-uuid"
STATE_FILE="$TEST_ROOT/state/machine-id"
printf '12345678-90ab-4cde-8f01-234567890abc\n' >"$RANDOM_SOURCE"
ECHO_MACHINE_ID_TESTING=yes \
ECHO_MACHINE_ID_RANDOM_SOURCE="$RANDOM_SOURCE" \
  "$MACHINE_ID_SCRIPT" --provision "$STATE_FILE"
grep -qx '1234567890ab4cde8f01234567890abc' "$STATE_FILE"
ECHO_MACHINE_ID_TESTING=yes "$MACHINE_ID_SCRIPT" --validate "$STATE_FILE"

# Provisioning is idempotent and never changes an established device identity.
printf 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee\n' >"$RANDOM_SOURCE"
ECHO_MACHINE_ID_TESTING=yes \
ECHO_MACHINE_ID_RANDOM_SOURCE="$RANDOM_SOURCE" \
  "$MACHINE_ID_SCRIPT" --provision "$STATE_FILE"
grep -qx '1234567890ab4cde8f01234567890abc' "$STATE_FILE"

for invalid_id in \
  00000000000000000000000000000000 \
  1234 \
  1234567890ab4cde8f01234567890abz; do
  invalid_file="$TEST_ROOT/invalid-$invalid_id"
  printf '%s\n' "$invalid_id" >"$invalid_file"
  if ECHO_MACHINE_ID_TESTING=yes \
       "$MACHINE_ID_SCRIPT" --validate "$invalid_file" 2>/dev/null; then
    echo "invalid machine-id was accepted: $invalid_id" >&2
    exit 1
  fi
done

echo "Echo OS persistent machine-id policy tests OK"
