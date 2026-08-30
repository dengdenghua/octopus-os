#!/usr/bin/env bash
# Portable production-entrypoint regression for authenticated offline A/B apply.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
UPDATE_COMMAND="$REPO_ROOT/deploy/update/echo-os-update"
TEST_ROOT="$(mktemp -d)"
FAKE_BIN="$TEST_ROOT/bin"
BUNDLE="$TEST_ROOT/bundle"
DEFINITIONS="$TEST_ROOT/sysupdate.d"
KEYRING="$TEST_ROOT/update-keyring.gpg"
TRUST_TOOL="$TEST_ROOT/select-trust.py"
TRUST_POLICY="$TEST_ROOT/update-trust-policy.json"
TRUST_STATE_ROOT="$TEST_ROOT/managed-trust"
CERTIFICATE="$TEST_ROOT/verity-certificate.pem"
IMAGE="$TEST_ROOT/echo-os.raw"
OUTPUT="$TEST_ROOT/output"
SYSUPDATE_LOG="$TEST_ROOT/sysupdate.log"
GPGV_LOG="$TEST_ROOT/gpgv.log"
COMMIT=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
TREE=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
MANIFEST=cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc

cleanup() {
  rm -rf -- "$TEST_ROOT"
}
trap cleanup EXIT INT TERM

mkdir -p "$FAKE_BIN" "$BUNDLE" "$DEFINITIONS"
printf '%s\n' '#!/bin/sh' 'if [ "$1" = "-u" ]; then echo 0; exit 0; fi' 'exit 1' \
  >"$FAKE_BIN/id"
printf '%s\n' \
  '#!/bin/sh' \
  'case "$2" in' \
  '  %u:%g) echo 0:0 ;;' \
  '  %a) echo 600 ;;' \
  '  %s) echo 128 ;;' \
  '  *) exit 1 ;;' \
  'esac' >"$FAKE_BIN/stat"
printf '%s\n' \
  '#!/bin/sh' \
  'if [ "${ECHO_TEST_FLOCK_FAIL:-}" = 1 ]; then exit 1; fi' \
  'exit 0' >"$FAKE_BIN/flock"
printf '%s\n' \
  '#!/bin/sh' \
  'if [ -n "${ECHO_TEST_GPGV_LOG:-}" ]; then printf "%s\n" "$*" >"$ECHO_TEST_GPGV_LOG"; fi' \
  'exit 0' >"$FAKE_BIN/gpgv"
printf '%s\n' '#!/bin/sh' 'exit 0' >"$FAKE_BIN/zstd"
printf '%s\n' \
  '#!/bin/sh' \
  'command_name=' \
  'for argument do' \
  '  case "$argument" in check-new|update) command_name=$argument ;; esac' \
  'done' \
  'printf "%s:%s\\n" "$command_name" "$*" >>"$ECHO_TEST_SYSUPDATE_LOG"' \
  'if [ "$command_name" = check-new ]; then' \
  '  if [ "${ECHO_TEST_SYSUPDATE_NO_NEW:-}" = 1 ]; then exit 1; fi' \
  '  printf "%s\\n" "${ECHO_TEST_CANDIDATE:-0.2.1}"' \
  '  exit 0' \
  'fi' \
  'if [ "${ECHO_TEST_SYSUPDATE_FAIL:-}" = 1 ]; then exit 42; fi' \
  'exit 0' >"$FAKE_BIN/systemd-sysupdate"
chmod 0755 "$FAKE_BIN"/*

printf '%s\n' \
  '#!/usr/bin/env python3' \
  'print("0.2.1\t'"$COMMIT"'\t'"$TREE"'\t'"$MANIFEST"'")' \
  >"$TEST_ROOT/verify-bundle.py"
printf '%s\n' '#!/usr/bin/env python3' 'raise SystemExit(0)' \
  >"$TEST_ROOT/verify-verity.py"
printf '%s\n' '#!/usr/bin/env python3' 'raise SystemExit(0)' \
  >"$TEST_ROOT/verify-keyring.py"
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
chmod 0755 "$TEST_ROOT"/verify-*.py "$TRUST_TOOL"

for name in \
  echo-os_0.2.1.root.11111111-2222-3333-4444-555555555555.raw.zst \
  echo-os_0.2.1.root-verity.66666666-7777-8888-9999-aaaaaaaaaaaa.raw.zst \
  echo-os_0.2.1.root-verity-sig.bbbbbbbb-cccc-dddd-eeee-ffffffffffff.raw.zst \
  echo-os_0.2.1.efi OS-SOURCE-IDENTITY.json SHA256SUMS SHA256SUMS.gpg; do
  printf '%s\n' fixture >"$BUNDLE/$name"
done
printf '%s\n' fixture >"$KEYRING"
printf '%s\n' fixture-policy >"$TRUST_POLICY"
printf '%s\n' fixture >"$CERTIFICATE"
printf '%s\n' fixture >"$IMAGE"
UPDATE_MANIFEST_SHA256="$(sha256sum "$BUNDLE/SHA256SUMS" | awk '{print $1}')"
UPDATE_SIGNATURE_SHA256="$(sha256sum "$BUNDLE/SHA256SUMS.gpg" | awk '{print $1}')"

export ECHO_UPDATE_TRUST_TOOL="$TRUST_TOOL"
export ECHO_UPDATE_TRUST_POLICY="$TRUST_POLICY"
export ECHO_UPDATE_TRUST_STATE_ROOT="$TRUST_STATE_ROOT"

MANAGED_KEYRING="$TRUST_STATE_ROOT/update-keyring.gpg"
mkdir -p "${MANAGED_KEYRING%/*}"
printf '%s\n' managed-public-keyring >"$MANAGED_KEYRING"
PATH="$FAKE_BIN:$PATH" \
ECHO_TEST_GPGV_LOG="$GPGV_LOG" \
ECHO_UPDATE_SOURCE_SMOKE=USE-SOURCE-RUNTIME \
ECHO_UPDATE_SYSTEM_KEYRING="$KEYRING" \
ECHO_UPDATE_ADMIN_KEYRING="$TEST_ROOT/no-admin-keyring.gpg" \
ECHO_UPDATE_DEFINITIONS="$DEFINITIONS" \
ECHO_UPDATE_VERIFY_BUNDLE="$TEST_ROOT/verify-bundle.py" \
ECHO_UPDATE_VERIFY_VERITY="$TEST_ROOT/verify-verity.py" \
ECHO_UPDATE_VERITY_CERTIFICATE="$CERTIFICATE" \
ECHO_UPDATE_VERIFY_KEYRING="$TEST_ROOT/verify-keyring.py" \
ECHO_UPDATE_IMAGE="$IMAGE" \
ECHO_UPDATE_LOCK="$TEST_ROOT/run/update.lock" \
  "$UPDATE_COMMAND" verify "$BUNDLE" >"$OUTPUT"
grep -Fq -- "--keyring $MANAGED_KEYRING" "$GPGV_LOG" || {
  echo "production updater did not prefer managed rollback-resistant trust" >&2
  exit 1
}
ADMIN_KEYRING="$TEST_ROOT/admin/update-keyring.gpg"
mkdir -p "${ADMIN_KEYRING%/*}"
printf '%s\n' administrator-public-keyring >"$ADMIN_KEYRING"
PATH="$FAKE_BIN:$PATH" \
ECHO_TEST_GPGV_LOG="$GPGV_LOG" \
ECHO_UPDATE_SOURCE_SMOKE=USE-SOURCE-RUNTIME \
ECHO_UPDATE_SYSTEM_KEYRING="$KEYRING" \
ECHO_UPDATE_ADMIN_KEYRING="$ADMIN_KEYRING" \
ECHO_UPDATE_DEFINITIONS="$DEFINITIONS" \
ECHO_UPDATE_VERIFY_BUNDLE="$TEST_ROOT/verify-bundle.py" \
ECHO_UPDATE_VERIFY_VERITY="$TEST_ROOT/verify-verity.py" \
ECHO_UPDATE_VERITY_CERTIFICATE="$CERTIFICATE" \
ECHO_UPDATE_VERIFY_KEYRING="$TEST_ROOT/verify-keyring.py" \
ECHO_UPDATE_IMAGE="$IMAGE" \
ECHO_UPDATE_LOCK="$TEST_ROOT/run/update.lock" \
  "$UPDATE_COMMAND" verify "$BUNDLE" >"$OUTPUT"
grep -Fq -- "--keyring $ADMIN_KEYRING" "$GPGV_LOG" || {
  echo "production updater did not preserve the explicit administrator trust override" >&2
  exit 1
}

: >"$SYSUPDATE_LOG"
PATH="$FAKE_BIN:$PATH" \
ECHO_TEST_SYSUPDATE_LOG="$SYSUPDATE_LOG" \
ECHO_UPDATE_SOURCE_SMOKE=USE-SOURCE-RUNTIME \
ECHO_UPDATE_SYSTEM_KEYRING="$KEYRING" \
ECHO_UPDATE_ADMIN_KEYRING="$TEST_ROOT/no-admin-keyring.gpg" \
ECHO_UPDATE_DEFINITIONS="$DEFINITIONS" \
ECHO_UPDATE_VERIFY_BUNDLE="$TEST_ROOT/verify-bundle.py" \
ECHO_UPDATE_VERIFY_VERITY="$TEST_ROOT/verify-verity.py" \
ECHO_UPDATE_VERITY_CERTIFICATE="$CERTIFICATE" \
ECHO_UPDATE_VERIFY_KEYRING="$TEST_ROOT/verify-keyring.py" \
ECHO_UPDATE_IMAGE="$IMAGE" \
ECHO_UPDATE_LOCK="$TEST_ROOT/run/update.lock" \
  "$UPDATE_COMMAND" apply "$BUNDLE" >"$OUTPUT"

grep -Fqx \
  "ECHO_UPDATE_BUNDLE_AUTHENTICATED version=0.2.1 os=$COMMIT tree=$TREE source-manifest=$MANIFEST manifest=$UPDATE_MANIFEST_SHA256 signature=$UPDATE_SIGNATURE_SHA256" \
  "$OUTPUT"
grep -Fqx \
  "ECHO_UPDATE_CANDIDATE_READY version=0.2.1 source=authenticated-bundle" \
  "$OUTPUT"
grep -Fqx \
  "ECHO_UPDATE_APPLIED version=0.2.1 os=$COMMIT tree=$TREE source-manifest=$MANIFEST manifest=$UPDATE_MANIFEST_SHA256 signature=$UPDATE_SIGNATURE_SHA256 target=inactive-root-uki-last" \
  "$OUTPUT"
grep -Fqx -- \
  "check-new:--image=$(realpath "$IMAGE") --definitions=$DEFINITIONS --transfer-source=$(realpath "$BUNDLE") check-new" \
  "$SYSUPDATE_LOG"
grep -Fqx -- \
  "update:--image=$(realpath "$IMAGE") --definitions=$DEFINITIONS --transfer-source=$(realpath "$BUNDLE") update 0.2.1" \
  "$SYSUPDATE_LOG"

: >"$SYSUPDATE_LOG"
if PATH="$FAKE_BIN:$PATH" \
   ECHO_TEST_SYSUPDATE_LOG="$SYSUPDATE_LOG" \
   ECHO_TEST_SYSUPDATE_FAIL=1 \
   ECHO_UPDATE_SOURCE_SMOKE=USE-SOURCE-RUNTIME \
   ECHO_UPDATE_SYSTEM_KEYRING="$KEYRING" \
   ECHO_UPDATE_ADMIN_KEYRING="$TEST_ROOT/no-admin-keyring.gpg" \
   ECHO_UPDATE_DEFINITIONS="$DEFINITIONS" \
   ECHO_UPDATE_VERIFY_BUNDLE="$TEST_ROOT/verify-bundle.py" \
   ECHO_UPDATE_VERIFY_VERITY="$TEST_ROOT/verify-verity.py" \
   ECHO_UPDATE_VERITY_CERTIFICATE="$CERTIFICATE" \
   ECHO_UPDATE_VERIFY_KEYRING="$TEST_ROOT/verify-keyring.py" \
   ECHO_UPDATE_IMAGE="$IMAGE" \
   ECHO_UPDATE_LOCK="$TEST_ROOT/run/update.lock" \
     "$UPDATE_COMMAND" apply "$BUNDLE" >"$OUTPUT" 2>&1; then
  echo "failed sysupdate unexpectedly produced a successful apply" >&2
  exit 1
fi
if grep -q '^ECHO_UPDATE_APPLIED ' "$OUTPUT"; then
  echo "failed sysupdate unexpectedly emitted an apply marker" >&2
  exit 1
fi
grep -Fqx \
  "ECHO_UPDATE_CANDIDATE_READY version=0.2.1 source=authenticated-bundle" \
  "$OUTPUT"

: >"$SYSUPDATE_LOG"
if PATH="$FAKE_BIN:$PATH" \
   ECHO_TEST_SYSUPDATE_LOG="$SYSUPDATE_LOG" \
   ECHO_TEST_SYSUPDATE_NO_NEW=1 \
   ECHO_UPDATE_SOURCE_SMOKE=USE-SOURCE-RUNTIME \
   ECHO_UPDATE_SYSTEM_KEYRING="$KEYRING" \
   ECHO_UPDATE_ADMIN_KEYRING="$TEST_ROOT/no-admin-keyring.gpg" \
   ECHO_UPDATE_DEFINITIONS="$DEFINITIONS" \
   ECHO_UPDATE_VERIFY_BUNDLE="$TEST_ROOT/verify-bundle.py" \
   ECHO_UPDATE_VERIFY_VERITY="$TEST_ROOT/verify-verity.py" \
   ECHO_UPDATE_VERITY_CERTIFICATE="$CERTIFICATE" \
   ECHO_UPDATE_VERIFY_KEYRING="$TEST_ROOT/verify-keyring.py" \
   ECHO_UPDATE_IMAGE="$IMAGE" \
   ECHO_UPDATE_LOCK="$TEST_ROOT/run/update.lock" \
     "$UPDATE_COMMAND" apply "$BUNDLE" >"$OUTPUT" 2>&1; then
  echo "already-installed bundle unexpectedly produced a successful apply" >&2
  exit 1
fi
grep -q 'authenticated update bundle is not newer' "$OUTPUT"
if grep -Eq '^ECHO_UPDATE_(CANDIDATE_READY|APPLIED) ' "$OUTPUT" || \
   grep -q '^update:' "$SYSUPDATE_LOG"; then
  echo "replayed bundle reached update or emitted a success marker" >&2
  exit 1
fi

: >"$SYSUPDATE_LOG"
if PATH="$FAKE_BIN:$PATH" \
   ECHO_TEST_SYSUPDATE_LOG="$SYSUPDATE_LOG" \
   ECHO_TEST_CANDIDATE=0.2.2 \
   ECHO_UPDATE_SOURCE_SMOKE=USE-SOURCE-RUNTIME \
   ECHO_UPDATE_SYSTEM_KEYRING="$KEYRING" \
   ECHO_UPDATE_ADMIN_KEYRING="$TEST_ROOT/no-admin-keyring.gpg" \
   ECHO_UPDATE_DEFINITIONS="$DEFINITIONS" \
   ECHO_UPDATE_VERIFY_BUNDLE="$TEST_ROOT/verify-bundle.py" \
   ECHO_UPDATE_VERIFY_VERITY="$TEST_ROOT/verify-verity.py" \
   ECHO_UPDATE_VERITY_CERTIFICATE="$CERTIFICATE" \
   ECHO_UPDATE_VERIFY_KEYRING="$TEST_ROOT/verify-keyring.py" \
   ECHO_UPDATE_IMAGE="$IMAGE" \
   ECHO_UPDATE_LOCK="$TEST_ROOT/run/update.lock" \
     "$UPDATE_COMMAND" apply "$BUNDLE" >"$OUTPUT" 2>&1; then
  echo "mismatched sysupdate candidate unexpectedly applied" >&2
  exit 1
fi
grep -q 'candidate does not match the authenticated bundle version' "$OUTPUT"
if grep -Eq '^ECHO_UPDATE_(CANDIDATE_READY|APPLIED) ' "$OUTPUT" || \
   grep -q '^update:' "$SYSUPDATE_LOG"; then
  echo "mismatched candidate reached update or emitted a success marker" >&2
  exit 1
fi

if PATH="$FAKE_BIN:$PATH" \
   ECHO_TEST_FLOCK_FAIL=1 \
   ECHO_UPDATE_SOURCE_SMOKE=USE-SOURCE-RUNTIME \
   ECHO_UPDATE_SYSTEM_KEYRING="$KEYRING" \
   ECHO_UPDATE_ADMIN_KEYRING="$TEST_ROOT/no-admin-keyring.gpg" \
   ECHO_UPDATE_DEFINITIONS="$DEFINITIONS" \
   ECHO_UPDATE_VERIFY_BUNDLE="$TEST_ROOT/verify-bundle.py" \
   ECHO_UPDATE_VERIFY_VERITY="$TEST_ROOT/verify-verity.py" \
   ECHO_UPDATE_VERITY_CERTIFICATE="$CERTIFICATE" \
   ECHO_UPDATE_VERIFY_KEYRING="$TEST_ROOT/verify-keyring.py" \
   ECHO_UPDATE_IMAGE="$IMAGE" \
   ECHO_UPDATE_LOCK="$TEST_ROOT/run/update.lock" \
     "$UPDATE_COMMAND" apply "$BUNDLE" >"$OUTPUT" 2>&1; then
  echo "concurrent update unexpectedly acquired the device lock" >&2
  exit 1
fi
grep -q 'another Echo OS update is already running' "$OUTPUT"

echo "Echo OS production update entrypoint tests OK"
