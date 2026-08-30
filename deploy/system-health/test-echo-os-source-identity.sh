#!/usr/bin/env bash
# Portable regression tests for the immutable runtime source-identity reader.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
HELPER="$REPO_ROOT/deploy/system-health/echo-os-source-identity"
VERIFIER="$REPO_ROOT/packaging/image/os_source_identity.py"
TEST_ROOT="$(mktemp -d)"
MANIFEST="$TEST_ROOT/os-source-identity.json"
OUTPUT="$TEST_ROOT/output"
COMMIT=dddddddddddddddddddddddddddddddddddddddd

cleanup() {
  rm -rf -- "$TEST_ROOT"
}
trap cleanup EXIT INT TERM

printf '%s\n' \
  '{"commit":"dddddddddddddddddddddddddddddddddddddddd","commit_time":"2024-01-01T00:00:00+00:00","dirty":false,"kind":"echo-os-source-identity","repository":"https://github.com/example/echo-os.git","schema":1,"source_date_epoch":1704067200,"tree":"eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"}' \
  >"$MANIFEST"

ECHO_OS_SOURCE_IDENTITY_TEST_MODE=USE-TEST-INPUTS \
ECHO_OS_SOURCE_IDENTITY_VERIFIER="$VERIFIER" \
ECHO_OS_SOURCE_IDENTITY_MANIFEST="$MANIFEST" \
  "$HELPER" --machine >"$OUTPUT"
grep -Eq "^https://github.com/example/echo-os.git[[:space:]]+$COMMIT[[:space:]]+e{40}[[:space:]]+[0-9a-f]{64}$" \
  "$OUTPUT"
[[ "$(ECHO_OS_SOURCE_IDENTITY_TEST_MODE=USE-TEST-INPUTS \
       ECHO_OS_SOURCE_IDENTITY_VERIFIER="$VERIFIER" \
       ECHO_OS_SOURCE_IDENTITY_MANIFEST="$MANIFEST" \
       "$HELPER" --commit)" == "$COMMIT" ]]

ln -s "$MANIFEST" "$TEST_ROOT/redirected.json"
if ECHO_OS_SOURCE_IDENTITY_TEST_MODE=USE-TEST-INPUTS \
   ECHO_OS_SOURCE_IDENTITY_VERIFIER="$VERIFIER" \
   ECHO_OS_SOURCE_IDENTITY_MANIFEST="$TEST_ROOT/redirected.json" \
     "$HELPER" --commit >"$OUTPUT" 2>&1; then
  echo "symlinked OS source identity unexpectedly passed" >&2
  exit 1
fi

printf '%s\n' '{"dirty":true}' >"$MANIFEST"
if ECHO_OS_SOURCE_IDENTITY_TEST_MODE=USE-TEST-INPUTS \
   ECHO_OS_SOURCE_IDENTITY_VERIFIER="$VERIFIER" \
   ECHO_OS_SOURCE_IDENTITY_MANIFEST="$MANIFEST" \
     "$HELPER" --commit >"$OUTPUT" 2>&1; then
  echo "malformed OS source identity unexpectedly passed" >&2
  exit 1
fi

echo "Echo OS immutable source-identity reader tests OK"
