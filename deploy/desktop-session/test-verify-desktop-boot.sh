#!/usr/bin/env bash
# Portable regression test for the direct-session boot-blessing record.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
VERIFIER="$REPO_ROOT/deploy/desktop-session/verify-desktop-boot.sh"
TEST_ROOT="$(mktemp -d)"
FAKE_BIN="$TEST_ROOT/bin"
READY_FILE="$TEST_ROOT/desktop-ready"
OUTPUT_FILE="$TEST_ROOT/output"
SOURCE_IDENTITY="$TEST_ROOT/source-identity"
OS_COMMIT=dddddddddddddddddddddddddddddddddddddddd

cleanup() {
  rm -rf -- "$TEST_ROOT"
}
trap cleanup EXIT INT TERM

mkdir -p "$FAKE_BIN"
printf '%s\n' \
  '#!/bin/sh' \
  'if [ "$1" = is-active ]; then exit 0; fi' \
  'exit 1' >"$FAKE_BIN/systemctl"
chmod 0755 "$FAKE_BIN/systemctl"
printf '%s\n' '#!/bin/sh' "printf '%s\\n' '$OS_COMMIT'" >"$SOURCE_IDENTITY"
chmod 0755 "$SOURCE_IDENTITY"
export ECHO_OS_SOURCE_IDENTITY_TEST_MODE=USE-TEST-INPUTS
export ECHO_OS_SOURCE_IDENTITY_COMMAND="$SOURCE_IDENTITY"

printf '%s\n' \
  'provider=ewmh-x11 window=0x2a auth=ready power=ready notifications=ready input=ready clipboard=ready accessibility=ready' >"$READY_FILE"
PATH="$FAKE_BIN:$PATH" \
ECHO_DESKTOP_READY_FILE="$READY_FILE" \
ECHO_DESKTOP_HEALTH_TIMEOUT_SECONDS=1 \
  "$VERIFIER" >"$OUTPUT_FILE"
grep -Eq "^ECHO_BOOT_HEALTHY version=.* os=$OS_COMMIT provider=ewmh-x11 window=0x2a auth=ready power=ready notifications=ready input=ready clipboard=ready accessibility=ready$" \
  "$OUTPUT_FILE"

printf '%s\n' \
  'provider=ewmh-x11 window=0x2a auth=ready power=ready notifications=ready clipboard=ready accessibility=ready' >"$READY_FILE"
if PATH="$FAKE_BIN:$PATH" \
   ECHO_DESKTOP_READY_FILE="$READY_FILE" \
   ECHO_DESKTOP_HEALTH_TIMEOUT_SECONDS=1 \
     "$VERIFIER" >"$OUTPUT_FILE" 2>&1; then
  echo "readiness without the input method unexpectedly blessed the boot" >&2
  exit 1
fi
grep -q 'did not become healthy' "$OUTPUT_FILE"

printf '%s\n' \
  'provider=ewmh-x11 window=0x2a auth=ready power=ready input=ready clipboard=ready accessibility=ready' >"$READY_FILE"
if PATH="$FAKE_BIN:$PATH" \
   ECHO_DESKTOP_READY_FILE="$READY_FILE" \
   ECHO_DESKTOP_HEALTH_TIMEOUT_SECONDS=1 \
     "$VERIFIER" >"$OUTPUT_FILE" 2>&1; then
  echo "readiness without native notifications unexpectedly blessed the boot" >&2
  exit 1
fi
grep -q 'did not become healthy' "$OUTPUT_FILE"

printf '%s\n' \
  'provider=ewmh-x11 window=0x2a auth=ready power=ready notifications=ready input=ready accessibility=ready' >"$READY_FILE"
if PATH="$FAKE_BIN:$PATH" \
   ECHO_DESKTOP_READY_FILE="$READY_FILE" \
   ECHO_DESKTOP_HEALTH_TIMEOUT_SECONDS=1 \
     "$VERIFIER" >"$OUTPUT_FILE" 2>&1; then
  echo "readiness without the system clipboard unexpectedly blessed the boot" >&2
  exit 1
fi
grep -q 'did not become healthy' "$OUTPUT_FILE"

printf '%s\n' \
  'provider=ewmh-x11 window=0x2a auth=ready power=ready notifications=ready input=ready clipboard=ready' >"$READY_FILE"
if PATH="$FAKE_BIN:$PATH" \
   ECHO_DESKTOP_READY_FILE="$READY_FILE" \
   ECHO_DESKTOP_HEALTH_TIMEOUT_SECONDS=1 \
     "$VERIFIER" >"$OUTPUT_FILE" 2>&1; then
  echo "readiness without the accessible application tree unexpectedly blessed the boot" >&2
  exit 1
fi
grep -q 'did not become healthy' "$OUTPUT_FILE"

printf '%s\n' 'provider=ewmh-x11 window=0x2a' >"$READY_FILE"
if PATH="$FAKE_BIN:$PATH" \
   ECHO_DESKTOP_READY_FILE="$READY_FILE" \
   ECHO_DESKTOP_HEALTH_TIMEOUT_SECONDS=1 \
     "$VERIFIER" >"$OUTPUT_FILE" 2>&1; then
  echo "legacy window-only readiness unexpectedly blessed the boot" >&2
  exit 1
fi
grep -q 'did not become healthy' "$OUTPUT_FILE"

rm -f -- "$READY_FILE"
ln -s /dev/null "$READY_FILE"
if PATH="$FAKE_BIN:$PATH" \
   ECHO_DESKTOP_READY_FILE="$READY_FILE" \
   ECHO_DESKTOP_HEALTH_TIMEOUT_SECONDS=1 \
     "$VERIFIER" >"$OUTPUT_FILE" 2>&1; then
  echo "symlinked readiness unexpectedly blessed the boot" >&2
  exit 1
fi
grep -q 'did not become healthy' "$OUTPUT_FILE"

echo "Echo OS desktop boot-blessing record tests OK"
