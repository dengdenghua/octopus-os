#!/usr/bin/env bash
# Portable regression test for source-bound production login readiness.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
VERIFIER="$REPO_ROOT/deploy/oem/verify-login-boot.sh"
TEST_ROOT="$(mktemp -d)"
FAKE_BIN="$TEST_ROOT/bin"
SOURCE_IDENTITY="$TEST_ROOT/source-identity"
OUTPUT="$TEST_ROOT/output"
OS_COMMIT=dddddddddddddddddddddddddddddddddddddddd

cleanup() {
  rm -rf -- "$TEST_ROOT"
}
trap cleanup EXIT INT TERM

mkdir -p "$FAKE_BIN"
printf '%s\n' \
  '#!/bin/sh' \
  'if [ "$1" = is-active ]; then exit 0; fi' \
  'if [ "$1" = is-failed ]; then exit 1; fi' \
  'exit 1' >"$FAKE_BIN/systemctl"
printf '%s\n' \
  '#!/bin/sh' \
  'printf "%s\\n" "7 1000 sddm seat0 tty1"' >"$FAKE_BIN/loginctl"
printf '%s\n' '#!/bin/sh' "printf '%s\\n' '$OS_COMMIT'" >"$SOURCE_IDENTITY"
chmod 0755 "$FAKE_BIN/systemctl" "$FAKE_BIN/loginctl" "$SOURCE_IDENTITY"

PATH="$FAKE_BIN:$PATH" \
ECHO_LOGIN_HEALTH_TIMEOUT_SECONDS=1 \
ECHO_OS_SOURCE_IDENTITY_TEST_MODE=USE-TEST-INPUTS \
ECHO_OS_SOURCE_IDENTITY_COMMAND="$SOURCE_IDENTITY" \
  "$VERIFIER" >"$OUTPUT"
grep -Eq "^ECHO_LOGIN_READY version=.* os=$OS_COMMIT provider=sddm-x11 seat=seat0$" \
  "$OUTPUT"

printf '%s\n' '#!/bin/sh' "printf '%s\\n' 'invalid'" >"$SOURCE_IDENTITY"
chmod 0755 "$SOURCE_IDENTITY"
if PATH="$FAKE_BIN:$PATH" \
   ECHO_LOGIN_HEALTH_TIMEOUT_SECONDS=1 \
   ECHO_OS_SOURCE_IDENTITY_TEST_MODE=USE-TEST-INPUTS \
   ECHO_OS_SOURCE_IDENTITY_COMMAND="$SOURCE_IDENTITY" \
     "$VERIFIER" >"$OUTPUT" 2>&1; then
  echo "login gate unexpectedly accepted invalid OS provenance" >&2
  exit 1
fi
grep -q 'immutable source identity is invalid' "$OUTPUT"

echo "Echo OS source-bound login readiness tests OK"
