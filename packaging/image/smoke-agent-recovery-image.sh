#!/usr/bin/env bash
# Prove a real raw cold boot discovers interrupted Agent work without mutating it.
set -euo pipefail

IMAGE_DIR="$(cd "$(dirname "$0")" && pwd)"
ENCRYPTED_IMAGE="$IMAGE_DIR/../../deploy/data-protection/echo-encrypted-image"
FIXTURE="$IMAGE_DIR/agent-recovery-task-runs.json"
VERIFY_FIXTURE="$IMAGE_DIR/verify-agent-recovery-fixture.py"
IMAGE_VERSION="${ECHO_IMAGE_VERSION:-$(tr -d '[:space:]' <"$IMAGE_DIR/mkosi.version")}"
SOURCE_IMAGE="${1:-$IMAGE_DIR/mkosi.output/echo-os_${IMAGE_VERSION}.raw}"

[[ $# -le 1 ]] || {
  echo "usage: $0 [SOURCE.raw]" >&2
  exit 2
}
[[ "$(uname -s)" == Linux && "$(id -u)" -eq 0 ]] || {
  echo "Agent recovery smoke requires a privileged Linux host" >&2
  exit 1
}
[[ "$IMAGE_VERSION" =~ ^[0-9A-Za-z][0-9A-Za-z.+:~_-]*$ ]] || {
  echo "invalid image version: $IMAGE_VERSION" >&2
  exit 2
}
for command_name in chmod cp grep id mkdir mktemp python3 realpath rm tr uname; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "Agent recovery smoke dependency missing: $command_name" >&2
    exit 1
  }
done
[[ -x "$ENCRYPTED_IMAGE" && -x "$VERIFY_FIXTURE" ]] || {
  echo "Agent recovery image helpers are missing" >&2
  exit 1
}
[[ -f "$FIXTURE" && ! -L "$FIXTURE" ]] || {
  echo "Agent recovery task fixture is missing or redirected" >&2
  exit 1
}
[[ -n "${ECHO_DATA_RECOVERY_KEY:-}" && \
   -f "$ECHO_DATA_RECOVERY_KEY" && ! -L "$ECHO_DATA_RECOVERY_KEY" ]] || {
  echo "ECHO_DATA_RECOVERY_KEY must name the installed device recovery key" >&2
  exit 1
}
[[ -n "${ECHO_SWTPM_STATE_DIR:-}" && \
   -d "$ECHO_SWTPM_STATE_DIR" && ! -L "$ECHO_SWTPM_STATE_DIR" ]] || {
  echo "ECHO_SWTPM_STATE_DIR must retain the installed device TPM identity" >&2
  exit 1
}
[[ -f "$SOURCE_IMAGE" && ! -L "$SOURCE_IMAGE" ]] || {
  echo "finished Echo OS image not found: $SOURCE_IMAGE" >&2
  exit 1
}

SOURCE_IMAGE="$(realpath "$SOURCE_IMAGE")"
RECOVERY_KEY="$(realpath "$ECHO_DATA_RECOVERY_KEY")"
python3 "$VERIFY_FIXTURE" verify "$FIXTURE"

TEMP_DIR="$(mktemp -d)"
RECOVERY_IMAGE="$TEMP_DIR/echo-os-agent-recovery.raw"
TMPFILES_RULE="$TEMP_DIR/99-echo-agent-recovery-ci.conf"
OBSERVED_STORE="$TEMP_DIR/task_runs.after-cold-boot.json"
LOG_ROOT="${ECHO_AGENT_RECOVERY_LOG_DIR:-$TEMP_DIR/logs}"
cleanup() { rm -rf -- "$TEMP_DIR"; }
trap cleanup EXIT INT TERM
mkdir -p "$LOG_ROOT"

cp --reflink=auto --sparse=always "$SOURCE_IMAGE" "$RECOVERY_IMAGE"
printf '%s\n' \
  'd /var/lib/echo-agent 0700 echo echo -' \
  'C /var/lib/echo-agent/task_runs.json 0600 echo echo - /var/lib/echo-agent-recovery-seed.json' \
  >"$TMPFILES_RULE"
chmod 0600 "$TMPFILES_RULE"

# The input disk must be clean. Both fixture files are written only to this
# disposable copy's encrypted persistent state, never to its signed root.
"$ENCRYPTED_IMAGE" assert-absent \
  "$RECOVERY_IMAGE" "$IMAGE_VERSION" "$RECOVERY_KEY" \
  /var/lib/echo-agent/task_runs.json
"$ENCRYPTED_IMAGE" assert-absent \
  "$RECOVERY_IMAGE" "$IMAGE_VERSION" "$RECOVERY_KEY" \
  /var/lib/echo-agent-recovery-seed.json
"$ENCRYPTED_IMAGE" assert-absent \
  "$RECOVERY_IMAGE" "$IMAGE_VERSION" "$RECOVERY_KEY" \
  /etc/tmpfiles.d/99-echo-agent-recovery-ci.conf
"$ENCRYPTED_IMAGE" copy-to \
  "$RECOVERY_IMAGE" "$IMAGE_VERSION" "$RECOVERY_KEY" \
  "$FIXTURE" /var/lib/echo-agent-recovery-seed.json
"$ENCRYPTED_IMAGE" copy-to \
  "$RECOVERY_IMAGE" "$IMAGE_VERSION" "$RECOVERY_KEY" \
  "$TMPFILES_RULE" /etc/tmpfiles.d/99-echo-agent-recovery-ci.conf

ECHO_EXPECT_AGENT_RECOVERY_COUNT=1 \
ECHO_BOOT_EPHEMERAL=no \
ECHO_BOOT_LOG_DIR="$LOG_ROOT" \
ECHO_BOOT_TIMEOUT_SECONDS="${ECHO_AGENT_RECOVERY_TIMEOUT_SECONDS:-240}" \
  "$IMAGE_DIR/smoke-boot-image.sh" "$RECOVERY_IMAGE"

grep -Eq \
  'ECHO_AGENT_READY source=[0-9a-f]{40} endpoint=http://127\.0\.0\.1:8000 recovery=1[[:space:]]*$' \
  "$LOG_ROOT/echo-os-boot.log" || {
    echo "cold boot did not report exactly one interrupted Agent task" >&2
    exit 1
  }
"$ENCRYPTED_IMAGE" copy-from \
  "$RECOVERY_IMAGE" "$IMAGE_VERSION" "$RECOVERY_KEY" \
  /var/lib/echo-agent/task_runs.json "$OBSERVED_STORE"
python3 "$VERIFY_FIXTURE" unchanged "$FIXTURE" "$OBSERVED_STORE"

echo "  ✓ encrypted persistent Agent state survived the simulated power loss"
echo "  ✓ native cold-boot health reported exactly one interrupted task"
echo "  ✓ discovery left the task status, checkpoint and expired lease byte-for-byte unchanged"
echo "Echo OS Agent interrupted-task cold-boot recovery smoke OK"
