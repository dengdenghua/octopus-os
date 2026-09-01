#!/usr/bin/env bash
# Portable regression for the privileged signed-channel coordinator.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CHANNEL_COMMAND="$REPO_ROOT/deploy/update/echo-os-update-channel"
TEST_ROOT="$(mktemp -d)"
FAKE_BIN="$TEST_ROOT/bin"
SYSTEM_CONFIG="$TEST_ROOT/update-channel"
SYSTEM_KEYRING="$TEST_ROOT/update-keyring.gpg"
TRUST_TOOL="$TEST_ROOT/select-trust.py"
TRUST_POLICY="$TEST_ROOT/update-trust-policy.json"
TRUST_STATE_ROOT="$TEST_ROOT/managed-trust"
STATUS_TOOL="$REPO_ROOT/deploy/update/echo_update_status.py"
STATUS_ROOT="$TEST_ROOT/status"
VERIFY_KEYRING="$TEST_ROOT/verify-keyring.py"
VERIFY_BUNDLE="$TEST_ROOT/verify-bundle.py"
CHANNEL_CLIENT="$TEST_ROOT/channel-client.py"
UPDATE_COMMAND="$TEST_ROOT/echo-os-update"
GPGV="$TEST_ROOT/gpgv"
CACHE_ROOT="$TEST_ROOT/cache/updates"
FETCH_LOCK="$TEST_ROOT/run/fetch.lock"
OUTPUT="$TEST_ROOT/output"
UPDATE_LOG="$TEST_ROOT/update.log"
KEYRING_LOG="$TEST_ROOT/keyring.log"
MANIFEST=dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd

cleanup() {
  rm -rf -- "$TEST_ROOT"
}
trap cleanup EXIT INT TERM

mkdir -p "$FAKE_BIN"
printf '%s\n' '#!/bin/sh' 'if [ "$1" = "-u" ]; then echo 0; exit 0; fi' 'exit 1' \
  >"$FAKE_BIN/id"
printf '%s\n' \
  '#!/bin/sh' \
  'case "$2" in' \
  '  %u:%g) echo 0:0 ;;' \
  '  %a) echo 600 ;;' \
  '  *) exit 1 ;;' \
  'esac' >"$FAKE_BIN/stat"
printf '%s\n' \
  '#!/bin/sh' \
  'if [ "${ECHO_TEST_FLOCK_FAIL:-}" = 1 ]; then exit 1; fi' \
  'exit 0' >"$FAKE_BIN/flock"
chmod 0755 "$FAKE_BIN"/*

printf '%s\n' 'https://updates.example.test/echo-os/stable/x86-64' >"$SYSTEM_CONFIG"
printf '%s\n' public-keyring >"$SYSTEM_KEYRING"
printf '%s\n' '#!/usr/bin/env python3' 'raise SystemExit(0)' >"$VERIFY_KEYRING"
printf '%s\n' '#!/usr/bin/env python3' 'raise SystemExit(0)' >"$VERIFY_BUNDLE"
printf '%s\n' \
  '#!/usr/bin/env python3' \
  'import pathlib, sys' \
  'system = pathlib.Path(sys.argv[sys.argv.index("--system-keyring") + 1])' \
  'state = pathlib.Path(sys.argv[sys.argv.index("--state-root") + 1])' \
  'managed = state / "update-keyring.gpg"' \
  'selected = managed if managed.is_file() else system' \
  'source = "managed" if selected == managed else "system"' \
  'generation = "3" if source == "managed" else "1"' \
  'print(generation, selected, "e" * 64, source, sep="\t")' \
  >"$TRUST_TOOL"
printf '%s\n' \
  '#!/usr/bin/env python3' \
  'import os, pathlib, sys' \
  'cache = pathlib.Path(sys.argv[sys.argv.index("--cache-root") + 1])' \
  'bundle = cache / "0.2.1"' \
  'bundle.mkdir(parents=True, exist_ok=True)' \
  'if os.environ.get("ECHO_TEST_KEYRING_LOG"):' \
  '    pathlib.Path(os.environ["ECHO_TEST_KEYRING_LOG"]).write_text(sys.argv[sys.argv.index("--keyring") + 1])' \
  'target = pathlib.Path(os.environ.get("ECHO_TEST_CHANNEL_ESCAPE", str(bundle)))' \
  'print("0.2.1", target, os.environ["ECHO_TEST_MANIFEST"], sep="\t")' \
  >"$CHANNEL_CLIENT"
printf '%s\n' \
  '#!/bin/sh' \
  'printf "%s\n" "$*" >"$ECHO_TEST_UPDATE_LOG"' \
  'exit 0' >"$UPDATE_COMMAND"
printf '%s\n' '#!/bin/sh' 'exit 0' >"$GPGV"
chmod 0755 \
  "$VERIFY_KEYRING" "$VERIFY_BUNDLE" "$TRUST_TOOL" "$CHANNEL_CLIENT" \
  "$UPDATE_COMMAND" "$GPGV"
printf '%s\n' fixture-policy >"$TRUST_POLICY"

channel_env=(
  PATH="$FAKE_BIN:$PATH"
  ECHO_TEST_UPDATE_LOG="$UPDATE_LOG"
  ECHO_TEST_MANIFEST="$MANIFEST"
  ECHO_UPDATE_CHANNEL_SOURCE_TEST=USE-SOURCE-RUNTIME
  ECHO_UPDATE_CHANNEL_CLIENT="$CHANNEL_CLIENT"
  ECHO_UPDATE_CHANNEL_SYSTEM_CONFIG="$SYSTEM_CONFIG"
  ECHO_UPDATE_CHANNEL_ADMIN_CONFIG="$TEST_ROOT/no-admin-channel"
  ECHO_UPDATE_CHANNEL_SYSTEM_KEYRING="$SYSTEM_KEYRING"
  ECHO_UPDATE_CHANNEL_ADMIN_KEYRING="$TEST_ROOT/no-admin-keyring"
  ECHO_UPDATE_CHANNEL_TRUST_TOOL="$TRUST_TOOL"
  ECHO_UPDATE_CHANNEL_TRUST_POLICY="$TRUST_POLICY"
  ECHO_UPDATE_CHANNEL_TRUST_STATE_ROOT="$TRUST_STATE_ROOT"
  ECHO_UPDATE_CHANNEL_STATUS_TOOL="$STATUS_TOOL"
  ECHO_UPDATE_CHANNEL_STATUS_ROOT="$STATUS_ROOT"
  ECHO_UPDATE_CHANNEL_VERIFY_KEYRING="$VERIFY_KEYRING"
  ECHO_UPDATE_CHANNEL_VERIFY_BUNDLE="$VERIFY_BUNDLE"
  ECHO_UPDATE_CHANNEL_UPDATE_COMMAND="$UPDATE_COMMAND"
  ECHO_UPDATE_CHANNEL_CACHE_ROOT="$CACHE_ROOT"
  ECHO_UPDATE_CHANNEL_LOCK="$FETCH_LOCK"
  ECHO_UPDATE_CHANNEL_GPGV="$GPGV"
)

env "${channel_env[@]}" "$CHANNEL_COMMAND" fetch >"$OUTPUT"
grep -Fqx \
  "ECHO_UPDATE_CHANNEL_FETCHED version=0.2.1 manifest=$MANIFEST cache=verified" \
  "$OUTPUT"
grep -Fqx 'Echo OS update 0.2.1 is authenticated in the private cache' "$OUTPUT"
python3 - "$STATUS_ROOT/status.json" <<'PY'
import json, pathlib, sys
record = json.loads(pathlib.Path(sys.argv[1]).read_text())
assert record["state"] == "ready", record
assert record["phase"] == "fetch", record
assert record["version"] == "0.2.1", record
assert record["manifestSha256"] == "d" * 64, record
PY
[[ ! -e "$UPDATE_LOG" ]] || {
  echo "fetch-only channel operation unexpectedly invoked apply" >&2
  exit 1
}

env "${channel_env[@]}" "$CHANNEL_COMMAND" apply >"$OUTPUT"
grep -Fqx "apply $(realpath "$CACHE_ROOT/0.2.1")" "$UPDATE_LOG"
python3 - "$STATUS_ROOT/status.json" <<'PY'
import json, pathlib, sys
record = json.loads(pathlib.Path(sys.argv[1]).read_text())
assert record["state"] == "reboot-required", record
assert record["phase"] == "apply", record
assert record["version"] == "0.2.1", record
PY

MANAGED_KEYRING="$TRUST_STATE_ROOT/update-keyring.gpg"
mkdir -p "${MANAGED_KEYRING%/*}"
printf '%s\n' managed-public-keyring >"$MANAGED_KEYRING"
env "${channel_env[@]}" \
  ECHO_TEST_KEYRING_LOG="$KEYRING_LOG" \
  "$CHANNEL_COMMAND" fetch >"$OUTPUT"
grep -Fqx "$MANAGED_KEYRING" "$KEYRING_LOG" || {
  echo "channel fetch did not prefer managed rollback-resistant trust" >&2
  exit 1
}
ADMIN_KEYRING="$TEST_ROOT/admin/update-keyring.gpg"
mkdir -p "${ADMIN_KEYRING%/*}"
printf '%s\n' administrator-public-keyring >"$ADMIN_KEYRING"
env "${channel_env[@]}" \
  ECHO_UPDATE_CHANNEL_ADMIN_KEYRING="$ADMIN_KEYRING" \
  ECHO_TEST_KEYRING_LOG="$KEYRING_LOG" \
  "$CHANNEL_COMMAND" fetch >"$OUTPUT"
grep -Fqx "$ADMIN_KEYRING" "$KEYRING_LOG" || {
  echo "channel fetch did not preserve the explicit administrator trust override" >&2
  exit 1
}

if env "${channel_env[@]}" ECHO_TEST_CHANNEL_ESCAPE="$TEST_ROOT/outside" \
     "$CHANNEL_COMMAND" fetch >"$OUTPUT" 2>&1; then
  echo "channel client unexpectedly escaped its private cache" >&2
  exit 1
fi
grep -q 'escaped its private version cache' "$OUTPUT"
python3 - "$STATUS_ROOT/status.json" <<'PY'
import json, pathlib, sys
record = json.loads(pathlib.Path(sys.argv[1]).read_text())
assert record["state"] == "failed", record
assert record["phase"] == "fetch", record
assert isinstance(record["errorCode"], int), record
assert "error" not in record, record
PY

STATUS_BEFORE_CONTENT="$(cat "$STATUS_ROOT/status.json")"
if env "${channel_env[@]}" ECHO_TEST_FLOCK_FAIL=1 \
     "$CHANNEL_COMMAND" fetch >"$OUTPUT" 2>&1; then
  echo "concurrent channel fetch unexpectedly acquired its lock" >&2
  exit 1
fi
grep -q 'another Echo OS channel fetch is already running' "$OUTPUT"
[[ "$(cat "$STATUS_ROOT/status.json")" == "$STATUS_BEFORE_CONTENT" ]] || {
  echo "a rejected concurrent fetch unexpectedly changed public status" >&2
  exit 1
}

echo "Echo OS update-channel coordinator tests OK"
