#!/usr/bin/env bash
# Portable source-policy test; runtime activation is proven by the raw boot gate.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG="$SCRIPT_DIR/echo-coredump.conf"
HEALTH="$SCRIPT_DIR/echo-crash-health"
UNIT="$SCRIPT_DIR/echo-crash-health.service"

fail() {
  echo "test-echo-coredump-policy: $*" >&2
  exit 1
}

[[ -f "$CONFIG" && -x "$HEALTH" && -f "$UNIT" ]] || \
  fail "crash collection source files are incomplete"
[[ "$(grep -Ec '^[^[:space:]#;].*=' "$CONFIG")" -eq 7 ]] || \
  fail "the coredump policy must contain exactly seven settings"
grep -Fxq '[Coredump]' "$CONFIG" || fail "the coredump section is missing"

for setting in \
  'Storage=external' \
  'Compress=yes' \
  'ProcessSizeMax=512M' \
  'ExternalSizeMax=512M' \
  'MaxUse=1G' \
  'KeepFree=2G' \
  'EnterNamespace=no'; do
  [[ "$(grep -Fxc "$setting" "$CONFIG")" -eq 1 ]] || \
    fail "missing or repeated policy setting: $setting"
done

grep -Fxq 'Requires=systemd-coredump.socket' "$UNIT" || \
  fail "health gate must require the native coredump socket"
grep -Fxq 'RequiresMountsFor=/var' "$UNIT" || \
  fail "health gate must wait for persistent var"
grep -Fxq 'Before=boot-complete.target' "$UNIT" || \
  fail "health gate must run before boot blessing"
grep -Fxq 'RequiredBy=boot-complete.target' "$UNIT" || \
  fail "boot blessing must require crash health"
grep -Fq '/dev/mapper/echo-var' "$HEALTH" || \
  fail "health gate must prove encrypted var backing"
grep -Fq 'systemd-analyze cat-config systemd/coredump.conf' "$HEALTH" || \
  fail "health gate must verify the effective systemd policy"
grep -Fq 'systemctl is-active --quiet "$COREDUMP_SOCKET"' "$HEALTH" || \
  fail "health gate must verify socket activation"
grep -Fq 'ECHO_CRASH_COLLECTION_READY provider=systemd-coredump storage=encrypted-var max-use=1G keep-free=2G' \
  "$HEALTH" || fail "health gate readiness record is missing"
if grep -Eiq 'https?://|curl|wget|upload|telemetry|socket\.send|nc[[:space:]]' \
    "$CONFIG" "$HEALTH" "$UNIT"; then
  fail "local crash collection must not contain an upload or network path"
fi

echo "Echo OS bounded local coredump policy OK"
