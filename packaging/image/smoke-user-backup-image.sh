#!/usr/bin/env bash
# Run the backup/corruption/restore gate on disposable OS and external-disk images.
set -euo pipefail
umask 077

IMAGE_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$IMAGE_DIR/../.." && pwd)"
ENCRYPTED_IMAGE="$IMAGE_DIR/../../deploy/data-protection/echo-encrypted-image"
RECOVERY="$REPO_ROOT/deploy/recovery/echo-recovery"
DATA_PROTECTION="$REPO_ROOT/deploy/data-protection/echo_data_protection.py"
RESTORE_TRANSACTION="$REPO_ROOT/deploy/recovery/echo_restore_transaction.py"
REPART_DEFINITIONS="$REPO_ROOT/deploy/recovery/repart.d"
MACHINE_ID_SERVICE="$REPO_ROOT/deploy/machine-state/echo-machine-identity-health.service"
GUEST_SCRIPT="$IMAGE_DIR/echo-user-backup-ci"
SERVICE_OVERRIDE="$IMAGE_DIR/echo-user-backup-ci.service"
IMAGE_VERSION="${ECHO_IMAGE_VERSION:-$(tr -d '[:space:]' <"$IMAGE_DIR/mkosi.version")}"
SOURCE_IMAGE="${1:-}"

[[ $# -eq 1 ]] || {
  echo "usage: $0 SOURCE.raw" >&2
  exit 2
}
[[ "$(uname -s)" == Linux && "$(id -u)" -eq 0 ]] || {
  echo "user-backup raw acceptance requires a privileged Linux host" >&2
  exit 1
}
for command_name in \
  awk chmod cp grep id kill lsblk mkdir mkfs.ext4 mknod modprobe mktemp \
  openssl python3 qemu-nbd realpath rm sed sleep stat tail tee tr truncate \
  udevadm; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "user-backup raw acceptance dependency missing: $command_name" >&2
    exit 1
  }
done
[[ -f "$SOURCE_IMAGE" && ! -L "$SOURCE_IMAGE" ]] || {
  echo "provisioned Echo OS image is missing" >&2
  exit 1
}
[[ -n "${ECHO_DATA_RECOVERY_KEY:-}" && -f "$ECHO_DATA_RECOVERY_KEY" && \
   ! -L "$ECHO_DATA_RECOVERY_KEY" ]] || {
  echo "ECHO_DATA_RECOVERY_KEY must name the installed-device recovery key" >&2
  exit 1
}
[[ -x "$ENCRYPTED_IMAGE" && -x "$RECOVERY" && -x "$DATA_PROTECTION" && \
   -x "$RESTORE_TRANSACTION" && -d "$REPART_DEFINITIONS" && \
   -f "$MACHINE_ID_SERVICE" && -x "$GUEST_SCRIPT" && \
   -f "$SERVICE_OVERRIDE" ]] || {
  echo "user-backup raw acceptance fixtures are incomplete" >&2
  exit 1
}

SOURCE_IMAGE="$(realpath "$SOURCE_IMAGE")"
RECOVERY_KEY="$(realpath "$ECHO_DATA_RECOVERY_KEY")"
TEMP_DIR="$(mktemp -d)"
WORK_IMAGE="$TEMP_DIR/echo-os-user-backup.raw"
ROLLBACK_IMAGE="$TEMP_DIR/echo-os-user-backup-rollback.raw"
BACKUP_DISK="$TEMP_DIR/echo-backup-disk.raw"
PASSWORD_FILE="$TEMP_DIR/echo-backup-ci-password"
LOG_ROOT="${ECHO_USER_BACKUP_LOG_DIR:-$TEMP_DIR/logs}"
TRANSACTION_LOG="$LOG_ROOT/echo-restore-transaction.log"
TRIAL_LOG_ROOT="$LOG_ROOT/restore-trial-boot"
NBD_DEVICE=""
USED_NBD_DEVICE=""
UDEVD_PID=""
UDEVD_LOG=""
CREATED_NBD_NODES=()

cleanup() {
  local exit_code="$?"
  trap - EXIT INT TERM
  if [[ -n "$NBD_DEVICE" ]]; then
    qemu-nbd --disconnect "$NBD_DEVICE" >/dev/null 2>&1 || true
  fi
  if [[ -n "$UDEVD_PID" ]]; then
    kill -TERM "$UDEVD_PID" >/dev/null 2>&1 || true
    wait "$UDEVD_PID" 2>/dev/null || true
  fi
  if [[ -n "$USED_NBD_DEVICE" ]]; then
    for partition_node in "${USED_NBD_DEVICE}"p[0-9]*; do
      [[ -b "$partition_node" ]] && rm -f -- "$partition_node"
    done
  fi
  for device_node in "${CREATED_NBD_NODES[@]}"; do
    rm -f -- "$device_node"
  done
  if [[ "$exit_code" -ne 0 && -n "$UDEVD_LOG" && -f "$UDEVD_LOG" ]]; then
    echo "Ephemeral udev log follows:" >&2
    tail -120 "$UDEVD_LOG" >&2 || true
  fi
  case "$TEMP_DIR" in
    /tmp/*|/var/tmp/*|/private/tmp/*) rm -rf -- "$TEMP_DIR" ;;
    *) echo "refusing to remove unexpected backup smoke directory: $TEMP_DIR" >&2 ;;
  esac
  exit "$exit_code"
}
trap cleanup EXIT INT TERM
mkdir -p "$LOG_ROOT"
: >"$TRANSACTION_LOG"

start_nbd_runtime() {
  modprobe nbd max_part=16
  local sys_candidate device_candidate device_major device_minor
  for sys_candidate in /sys/class/block/nbd[0-9]*; do
    [[ -r "$sys_candidate/dev" ]] || continue
    device_candidate="/dev/${sys_candidate##*/}"
    if [[ ! -e "$device_candidate" ]]; then
      read -r device_major device_minor < <(tr : ' ' <"$sys_candidate/dev")
      [[ "$device_major" =~ ^[0-9]+$ && "$device_minor" =~ ^[0-9]+$ ]] || {
        echo "invalid NBD device number for $sys_candidate" >&2
        exit 1
      }
      mknod "$device_candidate" b "$device_major" "$device_minor"
      chmod 0600 "$device_candidate"
      CREATED_NBD_NODES+=("$device_candidate")
    fi
  done
  if ! udevadm control --ping >/dev/null 2>&1; then
    local udevd_bin=""
    if command -v systemd-udevd >/dev/null 2>&1; then
      udevd_bin="$(command -v systemd-udevd)"
    elif [[ -x /usr/lib/systemd/systemd-udevd ]]; then
      udevd_bin=/usr/lib/systemd/systemd-udevd
    elif [[ -x /lib/systemd/systemd-udevd ]]; then
      udevd_bin=/lib/systemd/systemd-udevd
    else
      echo "systemd-udevd is required for restore transaction partitions" >&2
      exit 1
    fi
    mkdir -p /run/udev
    UDEVD_LOG="$TEMP_DIR/udevd.log"
    "$udevd_bin" --resolve-names=never --children-max=4 >"$UDEVD_LOG" 2>&1 &
    UDEVD_PID=$!
    for _attempt in {1..30}; do
      udevadm control --ping >/dev/null 2>&1 && break
      kill -0 "$UDEVD_PID" 2>/dev/null || {
        echo "ephemeral systemd-udevd exited" >&2
        exit 1
      }
      sleep 0.1
    done
    udevadm control --ping >/dev/null 2>&1 || {
      echo "ephemeral systemd-udevd is not ready" >&2
      exit 1
    }
  fi
}

attach_nbd() {
  local image="$1" device_candidate
  [[ -z "$NBD_DEVICE" ]] || {
    echo "an NBD restore target is already attached" >&2
    exit 1
  }
  for device_candidate in /dev/nbd[0-9]*; do
    [[ "$device_candidate" =~ ^/dev/nbd[0-9]+$ && \
       -b "$device_candidate" ]] || continue
    if qemu-nbd --connect="$device_candidate" --format=raw \
         --discard=unmap --detect-zeroes=unmap "$image" 2>/dev/null; then
      NBD_DEVICE="$device_candidate"
      USED_NBD_DEVICE="$device_candidate"
      break
    fi
  done
  [[ -n "$NBD_DEVICE" ]] || {
    echo "no free NBD whole-disk device is available" >&2
    exit 1
  }
  udevadm settle --timeout=30
  [[ "$(lsblk -dnro TYPE "$NBD_DEVICE")" == disk ]] || {
    echo "restore target is not reported as a whole disk" >&2
    exit 1
  }
}

detach_nbd() {
  [[ -n "$NBD_DEVICE" ]] || return 0
  qemu-nbd --disconnect "$NBD_DEVICE"
  udevadm settle --timeout=30
  NBD_DEVICE=""
}

run_recovery() {
  ECHO_RECOVERY_SOURCE_SMOKE=USE-SOURCE-RUNTIME \
  ECHO_RECOVERY_REPART_DEFINITIONS="$REPART_DEFINITIONS" \
  ECHO_RECOVERY_DATA_PROTECTION="$DATA_PROTECTION" \
  ECHO_RECOVERY_RESTORE_TRANSACTION="$RESTORE_TRANSACTION" \
  ECHO_RECOVERY_RESTORE_KEY_FILE="$RECOVERY_KEY" \
    "$RECOVERY" "$@"
}

record_output() {
  printf '%s\n' "$1" | tee -a "$TRANSACTION_LOG"
}

transaction_id_from_plan() {
  local plan_output="$1" transaction_id
  transaction_id="$(sed -n \
    's/^ECHO_RESTORE_TRANSACTION_STATUS transaction=\([0-9a-f]*\) phase=planned snapshot=.*/\1/p' \
    <<<"$plan_output")"
  [[ "$transaction_id" =~ ^[0-9a-f]{24}$ ]] || {
    echo "restore plan did not emit one valid transaction identity" >&2
    exit 1
  }
  printf '%s\n' "$transaction_id"
}

verify_backup_state() {
  local image="$1" expected_action="$2" transaction_id="$3"
  local state_file="$TEMP_DIR/state-${expected_action}-${transaction_id}.json"
  "$ENCRYPTED_IMAGE" copy-from \
    "$image" "$IMAGE_VERSION" "$RECOVERY_KEY" \
    /var/lib/echo-os/user-backup-state.json "$state_file" >/dev/null
  python3 - "$state_file" "$expected_action" "$transaction_id" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    state = json.load(stream)
if state.get("action") != sys.argv[2]:
    raise SystemExit("backup state did not record the expected restore action")
if state.get("restore_transaction_id") != sys.argv[3]:
    raise SystemExit("backup state did not bind the expected restore transaction")
if sys.argv[2] == "restore-rolled-back" and not state.get("rejected_agent"):
    raise SystemExit("rollback state did not retain the rejected trial Agent path")
PY
}

cp --reflink=auto --sparse=always "$SOURCE_IMAGE" "$WORK_IMAGE"
truncate -s 536870912 "$BACKUP_DISK"
mkfs.ext4 -q -F -L echo-backup-ci "$BACKUP_DISK"
openssl rand -base64 32 >"$PASSWORD_FILE"
chmod 0600 "$PASSWORD_FILE"

for injected_path in \
  /var/lib/echo-os/echo-user-backup-ci \
  /var/lib/echo-os/echo-backup-ci-password \
  /etc/systemd/system/echo-machine-identity-health.service; do
  "$ENCRYPTED_IMAGE" assert-absent \
    "$WORK_IMAGE" "$IMAGE_VERSION" "$RECOVERY_KEY" "$injected_path"
done
"$ENCRYPTED_IMAGE" copy-to \
  "$WORK_IMAGE" "$IMAGE_VERSION" "$RECOVERY_KEY" \
  "$GUEST_SCRIPT" /var/lib/echo-os/echo-user-backup-ci
"$ENCRYPTED_IMAGE" copy-to \
  "$WORK_IMAGE" "$IMAGE_VERSION" "$RECOVERY_KEY" \
  "$PASSWORD_FILE" /var/lib/echo-os/echo-backup-ci-password
"$ENCRYPTED_IMAGE" copy-to \
  "$WORK_IMAGE" "$IMAGE_VERSION" "$RECOVERY_KEY" \
  "$SERVICE_OVERRIDE" /etc/systemd/system/echo-machine-identity-health.service

ECHO_BOOT_TARGET=backup \
ECHO_BOOT_CI_SESSION=no \
ECHO_BOOT_EPHEMERAL=no \
ECHO_BOOT_EXTRA_DISK_PATH="$BACKUP_DISK" \
ECHO_BOOT_LOG_DIR="$LOG_ROOT" \
ECHO_BOOT_TIMEOUT_SECONDS="${ECHO_USER_BACKUP_TIMEOUT_SECONDS:-360}" \
  "$IMAGE_DIR/smoke-boot-image.sh" "$WORK_IMAGE"

STAGE_PATTERN='^ECHO_USER_BACKUP_STAGE_OK repository=[0-9a-f]{16} snapshot=[0-9a-f]{12} wrong-password=rejected disk-full=rejected corruption=rejected restore=staged metadata=acl,xattr,sparse$'
[[ "$(grep -Ec "$STAGE_PATTERN" "$LOG_ROOT/echo-os-boot.log")" -eq 1 ]] || {
    echo "guest did not emit exactly one complete backup staging marker" >&2
    exit 1
  }
STAGE_MARKER="$(grep -E "$STAGE_PATTERN" "$LOG_ROOT/echo-os-boot.log")"
record_output "$STAGE_MARKER"
REPOSITORY_ID="$(sed -n 's/^ECHO_USER_BACKUP_STAGE_OK repository=\([0-9a-f]*\) snapshot=.*/\1/p' <<<"$STAGE_MARKER")"
SNAPSHOT_ID="$(sed -n 's/^ECHO_USER_BACKUP_STAGE_OK repository=[0-9a-f]* snapshot=\([0-9a-f]*\) wrong-password=.*/\1/p' <<<"$STAGE_MARKER")"
[[ "$REPOSITORY_ID" =~ ^[0-9a-f]{16}$ && "$SNAPSHOT_ID" =~ ^[0-9a-f]{12}$ ]] || {
  echo "guest staging marker identities are invalid" >&2
  exit 1
}

# Return the staged disk to its production boot graph before testing a trial
# boot. The disposable guest command and credential remain outside both the
# rollback branch and the committed branch.
"$ENCRYPTED_IMAGE" copy-to \
  "$WORK_IMAGE" "$IMAGE_VERSION" "$RECOVERY_KEY" \
  "$MACHINE_ID_SERVICE" /etc/systemd/system/echo-machine-identity-health.service
for injected_path in \
  /var/lib/echo-os/echo-user-backup-ci \
  /var/lib/echo-os/echo-backup-ci-password; do
  "$ENCRYPTED_IMAGE" remove \
    "$WORK_IMAGE" "$IMAGE_VERSION" "$RECOVERY_KEY" "$injected_path"
  "$ENCRYPTED_IMAGE" assert-absent \
    "$WORK_IMAGE" "$IMAGE_VERSION" "$RECOVERY_KEY" "$injected_path"
done

cp --reflink=auto --sparse=always "$WORK_IMAGE" "$ROLLBACK_IMAGE"
start_nbd_runtime

# Branch one proves that promotion can be declined and that an accepted trial
# can return to the previous Home and Agent state without deleting trial data.
attach_nbd "$ROLLBACK_IMAGE"
ROLLBACK_PLAN_OUTPUT="$(run_recovery restore-plan "$NBD_DEVICE")"
record_output "$ROLLBACK_PLAN_OUTPUT"
ROLLBACK_TRANSACTION="$(transaction_id_from_plan "$ROLLBACK_PLAN_OUTPUT")"
if WRONG_PROMOTION_OUTPUT="$(
  run_recovery restore-promote "$NBD_DEVICE" \
    PROMOTE-ECHO-RESTORE-000000000000000000000000 2>&1
)"; then
  echo "Recovery accepted a promotion token from a different plan" >&2
  exit 1
fi
record_output "$WRONG_PROMOTION_OUTPUT"
grep -Fq 'restore promotion confirmation does not match the plan' \
  <<<"$WRONG_PROMOTION_OUTPUT" || {
    echo "wrong promotion token did not fail for the expected reason" >&2
    exit 1
  }
ROLLBACK_PROMOTE_OUTPUT="$(
  run_recovery restore-promote "$NBD_DEVICE" \
    "PROMOTE-ECHO-RESTORE-$ROLLBACK_TRANSACTION"
)"
record_output "$ROLLBACK_PROMOTE_OUTPUT"
grep -Fq \
  "ECHO_RESTORE_PROMOTED transaction=$ROLLBACK_TRANSACTION phase=trial snapshot=$SNAPSHOT_ID old-data=retained" \
  <<<"$ROLLBACK_PROMOTE_OUTPUT" || {
    echo "rollback branch did not reach a complete trial promotion" >&2
    exit 1
  }
ROLLBACK_OUTPUT="$(
  run_recovery restore-rollback "$NBD_DEVICE" \
    "ROLLBACK-ECHO-RESTORE-$ROLLBACK_TRANSACTION"
)"
record_output "$ROLLBACK_OUTPUT"
grep -Fq \
  "ECHO_RESTORE_ROLLED_BACK transaction=$ROLLBACK_TRANSACTION old-data=active trial-agent=retained" \
  <<<"$ROLLBACK_OUTPUT" || {
    echo "rollback branch did not restore the old active data" >&2
    exit 1
  }
ROLLBACK_STATUS="$(run_recovery restore-status "$NBD_DEVICE")"
record_output "$ROLLBACK_STATUS"
grep -Fxq 'ECHO_RESTORE_TRANSACTION_STATUS phase=none' <<<"$ROLLBACK_STATUS" || {
  echo "rollback branch retained an active journal" >&2
  exit 1
}
detach_nbd
verify_backup_state "$ROLLBACK_IMAGE" restore-rolled-back "$ROLLBACK_TRANSACTION"

# Branch two promotes the same verified stage, cold-boots the real production
# login graph in trial mode, then explicitly commits and removes only old data.
attach_nbd "$WORK_IMAGE"
COMMIT_PLAN_OUTPUT="$(run_recovery restore-plan "$NBD_DEVICE")"
record_output "$COMMIT_PLAN_OUTPUT"
COMMIT_TRANSACTION="$(transaction_id_from_plan "$COMMIT_PLAN_OUTPUT")"
COMMIT_PROMOTE_OUTPUT="$(
  run_recovery restore-promote "$NBD_DEVICE" \
    "PROMOTE-ECHO-RESTORE-$COMMIT_TRANSACTION"
)"
record_output "$COMMIT_PROMOTE_OUTPUT"
grep -Fq \
  "ECHO_RESTORE_PROMOTED transaction=$COMMIT_TRANSACTION phase=trial snapshot=$SNAPSHOT_ID old-data=retained" \
  <<<"$COMMIT_PROMOTE_OUTPUT" || {
    echo "commit branch did not reach a complete trial promotion" >&2
    exit 1
  }
detach_nbd

ECHO_LOGIN_PROVISION_MODE=existing \
ECHO_LOGIN_LOG_DIR="$TRIAL_LOG_ROOT" \
ECHO_LOGIN_TIMEOUT_SECONDS="${ECHO_USER_RESTORE_TRIAL_TIMEOUT_SECONDS:-240}" \
  "$IMAGE_DIR/smoke-login-image.sh" "$WORK_IMAGE"
TRIAL_MARKER="ECHO_RESTORE_TRANSACTION_READY phase=promoted trial=yes transaction=$COMMIT_TRANSACTION"
[[ "$(grep -Fxc "$TRIAL_MARKER" "$TRIAL_LOG_ROOT/echo-os-boot.log")" -eq 1 ]] || {
  echo "promoted restore did not pass exactly one normal-boot transaction gate" >&2
  exit 1
}
record_output "$TRIAL_MARKER"

attach_nbd "$WORK_IMAGE"
COMMIT_STATUS="$(run_recovery restore-status "$NBD_DEVICE")"
record_output "$COMMIT_STATUS"
grep -Fq \
  "ECHO_RESTORE_TRANSACTION_STATUS transaction=$COMMIT_TRANSACTION phase=promoted snapshot=$SNAPSHOT_ID" \
  <<<"$COMMIT_STATUS" || {
    echo "trial boot did not preserve the promoted transaction" >&2
    exit 1
  }
COMMIT_OUTPUT="$(
  run_recovery restore-commit "$NBD_DEVICE" \
    "COMMIT-ECHO-RESTORE-$COMMIT_TRANSACTION"
)"
record_output "$COMMIT_OUTPUT"
grep -Fq \
  "ECHO_RESTORE_COMMITTED transaction=$COMMIT_TRANSACTION old-data=deleted staging=retained" \
  <<<"$COMMIT_OUTPUT" || {
    echo "explicit restore commit did not complete" >&2
    exit 1
  }
FINAL_STATUS="$(run_recovery restore-status "$NBD_DEVICE")"
record_output "$FINAL_STATUS"
grep -Fxq 'ECHO_RESTORE_TRANSACTION_STATUS phase=none' <<<"$FINAL_STATUS" || {
  echo "committed restore retained an active journal" >&2
  exit 1
}
detach_nbd
verify_backup_state "$WORK_IMAGE" restore-committed "$COMMIT_TRANSACTION"

FINAL_MARKER="ECHO_USER_BACKUP_RAW_OK repository=$REPOSITORY_ID snapshot=$SNAPSHOT_ID transaction=$COMMIT_TRANSACTION wrong-password=rejected disk-full=rejected corruption=rejected restore=promote,rollback,commit trial-boot=ready confirmation=rejected metadata=acl,xattr,sparse"
record_output "$FINAL_MARKER"
[[ "$(grep -Fxc "$FINAL_MARKER" "$TRANSACTION_LOG")" -eq 1 ]] || {
  echo "combined restore transaction log has an ambiguous completion marker" >&2
  exit 1
}
echo "  ✓ a dedicated virtual disk held the encrypted restic repository"
echo "  ✓ wrong credentials and a two-MiB free-space reserve were both rejected"
echo "  ✓ full data verification rejected a deliberately corrupted pack"
echo "  ✓ the repaired repository staged byte-, ACL-, xattr- and sparse-preserving restore data"
echo "  ✓ independent raw branches exercised explicit promotion, rollback and commit"
echo "  ✓ a promoted trial passed the production login and transaction health gates"
echo "Echo OS encrypted user-backup and restore-transaction raw acceptance OK"
