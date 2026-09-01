#!/usr/bin/env bash
# Portable regression test for source-bound Recovery readiness.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
RECOVERY="$REPO_ROOT/deploy/recovery/echo-recovery"
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
printf '%s\n' '#!/bin/sh' 'exit 0' >"$FAKE_BIN/lsblk"
printf '%s\n' '#!/bin/sh' 'exit 0' >"$FAKE_BIN/bootctl"
printf '%s\n' '#!/bin/sh' "printf '%s\\n' '$OS_COMMIT'" >"$SOURCE_IDENTITY"
chmod 0755 "$FAKE_BIN/lsblk" "$FAKE_BIN/bootctl" "$SOURCE_IDENTITY"

PATH="$FAKE_BIN:$PATH" \
ECHO_RECOVERY_SOURCE_SMOKE=USE-SOURCE-RUNTIME \
ECHO_RECOVERY_SOURCE_IDENTITY_COMMAND="$SOURCE_IDENTITY" \
  "$RECOVERY" status >"$OUTPUT"
grep -Eq "^ECHO_RECOVERY_READY version=.* os=$OS_COMMIT$" "$OUTPUT"

printf '%s\n' '#!/bin/sh' 'exit 1' >"$SOURCE_IDENTITY"
chmod 0755 "$SOURCE_IDENTITY"
if PATH="$FAKE_BIN:$PATH" \
   ECHO_RECOVERY_SOURCE_SMOKE=USE-SOURCE-RUNTIME \
   ECHO_RECOVERY_SOURCE_IDENTITY_COMMAND="$SOURCE_IDENTITY" \
     "$RECOVERY" status >"$OUTPUT" 2>&1; then
  echo "Recovery readiness unexpectedly ignored missing OS provenance" >&2
  exit 1
fi
if grep -q '^ECHO_RECOVERY_READY ' "$OUTPUT"; then
  echo "Recovery emitted readiness without verified OS provenance" >&2
  exit 1
fi

echo "Echo Recovery source-bound readiness tests OK"
