#!/usr/bin/env bash
# Directly UEFI-boot the self-contained Recovery UKI and wait for diagnostics.
set -euo pipefail

RECOVERY_DIR="$(cd "$(dirname "$0")" && pwd)"
IMAGE_DIR="$RECOVERY_DIR/../image"
# shellcheck source=packaging/image/secure-boot-options.sh
source "$IMAGE_DIR/secure-boot-options.sh"
RECOVERY_UKI="${1:-}"
IMAGE_VERSION="${ECHO_IMAGE_VERSION:-$(tr -d '[:space:]' <"$RECOVERY_DIR/mkosi.version")}"
BOOT_TIMEOUT_SECONDS="${ECHO_RECOVERY_TIMEOUT_SECONDS:-120}"

[[ "$(uname -s)" == "Linux" ]] || { echo "recovery smoke requires Linux" >&2; exit 1; }
[[ "$IMAGE_VERSION" =~ ^[0-9A-Za-z][0-9A-Za-z.+:~_-]*$ ]] || {
  echo "invalid recovery image version: $IMAGE_VERSION" >&2
  exit 2
}
[[ -n "${ECHO_OS_SOURCE_MANIFEST:-}" && \
   -f "$ECHO_OS_SOURCE_MANIFEST" && ! -L "$ECHO_OS_SOURCE_MANIFEST" ]] || {
  echo "ECHO_OS_SOURCE_MANIFEST is required to authenticate Recovery provenance" >&2
  exit 1
}
SOURCE_RECORD="$(python3 "$IMAGE_DIR/os_source_identity.py" verify \
  --manifest "$ECHO_OS_SOURCE_MANIFEST" --machine)"
IFS=$'\t' read -r OS_SOURCE_REPOSITORY OS_SOURCE_COMMIT \
  OS_SOURCE_TREE OS_SOURCE_MANIFEST_SHA256 <<<"$SOURCE_RECORD"
[[ -n "$OS_SOURCE_REPOSITORY" && "$OS_SOURCE_COMMIT" =~ ^[0-9a-f]{40}$ && \
   "$OS_SOURCE_TREE" =~ ^[0-9a-f]{40}$ && \
   "$OS_SOURCE_MANIFEST_SHA256" =~ ^[0-9a-f]{64}$ ]] || {
  echo "OS source identity verifier returned an invalid Recovery record" >&2
  exit 1
}
[[ "$BOOT_TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]] || {
  echo "ECHO_RECOVERY_TIMEOUT_SECONDS must be a positive integer" >&2
  exit 2
}
if [[ -z "$RECOVERY_UKI" ]]; then
  RECOVERY_UKI="$RECOVERY_DIR/mkosi.output/echo-recovery_${IMAGE_VERSION}.efi"
fi
RECOVERY_UKI="$(realpath "$RECOVERY_UKI")"
[[ -f "$RECOVERY_UKI" ]] || { echo "recovery UKI not found" >&2; exit 1; }
for command_name in grep mkosi python3 setsid; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "recovery smoke dependency missing: $command_name" >&2
    exit 1
  }
done
configure_echo_secure_boot

if [[ -n "${ECHO_RECOVERY_LOG_DIR:-}" ]]; then
  LOG_DIR="$ECHO_RECOVERY_LOG_DIR"
  mkdir -p "$LOG_DIR"
  REMOVE_LOG_DIR=0
else
  LOG_DIR="$(mktemp -d)"
  REMOVE_LOG_DIR=1
fi
BOOT_LOG="$LOG_DIR/echo-recovery-boot.log"
# shellcheck disable=SC2329 # Invoked indirectly by EXIT/INT/TERM traps.
cleanup() {
  local exit_code="$1"
  trap - EXIT INT TERM
  if [[ -n "${VM_PROCESS_GROUP:-}" ]]; then
    kill -TERM -- "-$VM_PROCESS_GROUP" 2>/dev/null || true
    sleep 1
    kill -KILL -- "-$VM_PROCESS_GROUP" 2>/dev/null || true
    wait "$VM_PROCESS_GROUP" 2>/dev/null || true
  fi
  if [[ "$exit_code" -ne 0 ]]; then
    tail -240 "$BOOT_LOG" >&2 || true
  elif [[ "$REMOVE_LOG_DIR" -eq 1 ]]; then
    rm -rf -- "$LOG_DIR"
  fi
  exit "$exit_code"
}
trap 'cleanup $?' EXIT INT TERM

OUTPUT_DIRECTORY="$(dirname "$RECOVERY_UKI")"
OUTPUT_NAME="$(basename "$RECOVERY_UKI" .efi)"
(
  cd "$RECOVERY_DIR"
  exec setsid mkosi \
    --image-version "$IMAGE_VERSION" \
    --output-directory "$OUTPUT_DIRECTORY" \
    --output "$OUTPUT_NAME" \
    "${ECHO_MKOSI_SECURE_BOOT_RUNTIME_ARGS[@]}" \
    vm
) >"$BOOT_LOG" 2>&1 &
VM_PROCESS_GROUP=$!

deadline=$((SECONDS + BOOT_TIMEOUT_SECONDS))
while (( SECONDS < deadline )); do
  if grep -q "ECHO_RECOVERY_READY version=$IMAGE_VERSION os=$OS_SOURCE_COMMIT" "$BOOT_LOG"; then
    echo "Echo Recovery self-contained UKI smoke OK"
    exit 0
  fi
  if ! kill -0 "$VM_PROCESS_GROUP" 2>/dev/null; then
    wait "$VM_PROCESS_GROUP" || true
    echo "Recovery VM exited before diagnostics became ready" >&2
    exit 1
  fi
  sleep 1
done

echo "Recovery readiness marker was not observed within ${BOOT_TIMEOUT_SECONDS}s" >&2
exit 1
