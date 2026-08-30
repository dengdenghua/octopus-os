#!/usr/bin/env bash
# Verified, transaction-like Echo appliance state restoration for Docker Compose.

set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
STATE_DIR="$SCRIPT_DIR/data"
BACKUP_INPUT="${1:-}"
DOCKER_BIN="${ECHO_DOCKER_BIN:-docker}"
PYTHON_BIN="${ECHO_HOST_PYTHON:-python3}"
RELEASE_ENV="${ECHO_RELEASE_ENV:-$SCRIPT_DIR/echo-release.env}"
APPLIANCE_ENV="${ECHO_APPLIANCE_ENV:-$SCRIPT_DIR/appliance.env}"
WAIT_TIMEOUT="${ECHO_RESTORE_WAIT_TIMEOUT:-180}"

fail() {
  printf 'Echo state restore failed: %s\n' "$*" >&2
  exit 1
}

[[ -n "$BACKUP_INPUT" ]] || fail "usage: restore-state.sh /external/path/echo-state-....echo-backup"
[[ "$WAIT_TIMEOUT" =~ ^[0-9]+$ ]] && ((WAIT_TIMEOUT >= 30 && WAIT_TIMEOUT <= 1800)) ||
  fail "ECHO_RESTORE_WAIT_TIMEOUT must be between 30 and 1800 seconds"
command -v flock >/dev/null 2>&1 || fail "flock is required on the NAS host"
command -v "$PYTHON_BIN" >/dev/null 2>&1 || fail "python3 is required on the NAS host"

MAINTENANCE_LOCK="${ECHO_MAINTENANCE_LOCK:-/run/lock/echo-os-appliance-maintenance.lock}"
[[ "$MAINTENANCE_LOCK" == /* && "$MAINTENANCE_LOCK" != "/" && "$MAINTENANCE_LOCK" != *$'\\n'* ]] ||
  fail "ECHO_MAINTENANCE_LOCK must be a safe absolute path"
maintenance_parent="$(dirname -- "$MAINTENANCE_LOCK")"
[[ -d "$maintenance_parent" && ! -L "$maintenance_parent" ]] ||
  fail "maintenance lock parent is missing or unsafe"
maintenance_parent="$(cd -- "$maintenance_parent" && pwd -P)"
MAINTENANCE_LOCK="$maintenance_parent/$(basename -- "$MAINTENANCE_LOCK")"
if [[ -e "$MAINTENANCE_LOCK" && (! -f "$MAINTENANCE_LOCK" || -L "$MAINTENANCE_LOCK") ]]; then
  fail "maintenance lock file is unsafe"
fi
inherited_maintenance_fd="${ECHO_MAINTENANCE_LOCK_FD:-}"
if [[ "$inherited_maintenance_fd" =~ ^[0-9]+$ ]] &&
  [[ -e "/proc/$$/fd/$inherited_maintenance_fd" ]]; then
  inherited_lock="$(readlink -f "/proc/$$/fd/$inherited_maintenance_fd")"
  [[ "$inherited_lock" == "$MAINTENANCE_LOCK" ]] ||
    fail "inherited maintenance lock does not match ECHO_MAINTENANCE_LOCK"
else
  exec 7>"$MAINTENANCE_LOCK"
  flock -n 7 || fail "another Echo maintenance operation is already running"
  export ECHO_MAINTENANCE_LOCK_FD=7
fi

[[ -f "$BACKUP_INPUT" && ! -L "$BACKUP_INPUT" ]] || fail "backup must be a regular non-symlink file"
BACKUP_DIR="$(cd -- "$(dirname -- "$BACKUP_INPUT")" && pwd -P)"
BACKUP_NAME="$(basename -- "$BACKUP_INPUT")"
[[ "$BACKUP_NAME" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.echo-backup$ ]] ||
  fail "backup filename is unsafe"
[[ "$BACKUP_DIR" != *:* && "$BACKUP_DIR" != *$'\n'* ]] || fail "backup path is unsafe for a bind mount"
BACKUP_PATH="$BACKUP_DIR/$BACKUP_NAME"

STATE_PARENT="$(cd -- "$(dirname -- "$STATE_DIR")" && pwd -P)"
STATE_NAME="$(basename -- "$STATE_DIR")"
[[ "$STATE_NAME" == "data" && "$STATE_DIR" == "$STATE_PARENT/data" ]] ||
  fail "managed state path is not the deployment data directory"
[[ -d "$STATE_DIR" && ! -L "$STATE_DIR" ]] ||
  fail "the deployment data directory must exist and must not be a symlink"
[[ "$BACKUP_PATH" != "$STATE_DIR/"* ]] || fail "backup must be outside the live state directory"
[[ "$STATE_PARENT" != *:* && "$STATE_PARENT" != *$'\n'* ]] ||
  fail "state parent path is unsafe for a bind mount"

credential_file="${ECHO_BACKUP_PASSPHRASE_FILE:-}"
if [[ -z "$credential_file" && -n "${CREDENTIALS_DIRECTORY:-}" ]]; then
  credential_file="$CREDENTIALS_DIRECTORY/echo-backup-passphrase"
fi
if [[ -z "${ECHO_BACKUP_PASSPHRASE:-}" && -n "$credential_file" ]]; then
  [[ -f "$credential_file" && ! -L "$credential_file" ]] ||
    fail "backup passphrase credential is missing or unsafe"
  ECHO_BACKUP_PASSPHRASE="$(<"$credential_file")"
fi
[[ ${#ECHO_BACKUP_PASSPHRASE} -ge 12 ]] ||
  fail "ECHO_BACKUP_PASSPHRASE must contain at least 12 characters"
export ECHO_BACKUP_PASSPHRASE

compose=("$DOCKER_BIN" compose)
if [[ -e "$APPLIANCE_ENV" ]]; then
  [[ -f "$APPLIANCE_ENV" && ! -L "$APPLIANCE_ENV" ]] ||
    fail "appliance environment file is unsafe"
  compose+=(--env-file "$APPLIANCE_ENV")
fi
if [[ -e "$RELEASE_ENV" ]]; then
  [[ -f "$RELEASE_ENV" && ! -L "$RELEASE_ENV" ]] || fail "release environment file is unsafe"
  compose+=(--env-file "$RELEASE_ENV")
fi
compose+=(--project-directory "$SCRIPT_DIR" -f "$SCRIPT_DIR/docker-compose.yml")

exec 9>"$STATE_PARENT/.echo-state-restore.lock"
flock -n 9 || fail "another Echo state restore is already running"

backup_sha256() {
  local result
  if command -v sha256sum >/dev/null 2>&1; then
    result="$(sha256sum -- "$BACKUP_PATH")"
  else
    result="$(shasum -a 256 -- "$BACKUP_PATH")"
  fi
  printf '%s\n' "${result%% *}"
}

backup_sha="$(backup_sha256)"
[[ "$backup_sha" =~ ^[0-9a-f]{64}$ ]] || fail "could not calculate the backup SHA-256"

# Verify authentication and structure before asking for destructive confirmation
# or stopping the live appliance.
"${compose[@]}" run --rm --no-deps --entrypoint python \
  -e ECHO_BACKUP_PASSPHRASE \
  -v "$BACKUP_DIR:/recovery-backup:ro" \
  echo-os -m appliance.state_backup verify "/recovery-backup/$BACKUP_NAME"

required_confirmation="RESTORE sha256:$backup_sha TO $STATE_DIR"
if [[ "${ECHO_RESTORE_CONFIRM:-}" != "$required_confirmation" ]]; then
  printf 'Echo state backup verified. No live state was changed.\n'
  printf 'Backup: %s\n' "$BACKUP_PATH"
  printf 'SHA-256: %s\n' "$backup_sha"
  printf 'Target: %s\n' "$STATE_DIR"
  printf 'To continue, set ECHO_RESTORE_CONFIRM exactly to:\n%s\n' "$required_confirmation"
  exit 2
fi
[[ -f "$BACKUP_PATH" && ! -L "$BACKUP_PATH" ]] || fail "verified backup was replaced"
[[ "$(backup_sha256)" == "$backup_sha" ]] || fail "verified backup changed after confirmation"

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
rollback_dir="$STATE_PARENT/.${STATE_NAME}.echo-rollback-$timestamp"
failed_dir="$STATE_PARENT/.${STATE_NAME}.echo-failed-$timestamp"
[[ ! -e "$rollback_dir" && ! -L "$rollback_dir" ]] || fail "rollback directory already exists"
[[ ! -e "$failed_dir" && ! -L "$failed_dir" ]] || fail "failed-state directory already exists"

was_running=0
stopped_live=0
promoted=0
live_displaced=0
running_services="$("${compose[@]}" ps --status running --services)" ||
  fail "could not determine whether echo-os is running"
while IFS= read -r service; do
  [[ "$service" == "echo-os" ]] && was_running=1
done <<<"$running_services"

transaction_dir="$(mktemp -d "$STATE_PARENT/.${STATE_NAME}.echo-restore.XXXXXX")"
transaction_name="$(basename -- "$transaction_dir")"
restored_dir="$transaction_dir/restored"
restored_container="/state-parent/$transaction_name/restored"

fsync_state_parent() {
  "$PYTHON_BIN" -c 'import os,sys; fd=os.open(sys.argv[1], os.O_RDONLY); os.fsync(fd); os.close(fd)' \
    "$STATE_PARENT"
}

restore_previous_directory() {
  [[ -d "$STATE_DIR" && ! -L "$STATE_DIR" ]] || return 1
  [[ -d "$rollback_dir" && ! -L "$rollback_dir" ]] || return 1
  [[ ! -e "$failed_dir" && ! -L "$failed_dir" ]] || return 1
  mv -- "$STATE_DIR" "$failed_dir"
  if ! mv -- "$rollback_dir" "$STATE_DIR"; then
    mv -- "$failed_dir" "$STATE_DIR" || true
    return 1
  fi
  fsync_state_parent
}

cleanup() {
  local status=$?
  trap - EXIT INT TERM
  if ((status != 0)); then
    if ((live_displaced)); then
      printf 'State promotion was interrupted before the restored directory was installed; restoring the previous directory...\n' >&2
      if [[ ! -e "$STATE_DIR" && ! -L "$STATE_DIR" && -d "$rollback_dir" && ! -L "$rollback_dir" ]] &&
        mv -- "$rollback_dir" "$STATE_DIR"; then
        live_displaced=0
        fsync_state_parent || true
      else
        printf 'CRITICAL: interrupted directory promotion could not be rolled back; inspect %s and %s.\n' \
          "$STATE_DIR" "$rollback_dir" >&2
        status=1
      fi
    fi
    if ((promoted)); then
      printf 'Restored state did not become healthy; rolling back the directory promotion...\n' >&2
      "${compose[@]}" stop --timeout 30 echo-os >/dev/null 2>&1 || true
      if ! restore_previous_directory; then
        printf 'CRITICAL: state directory rollback failed; inspect %s and %s before starting Echo.\n' \
          "$STATE_DIR" "$rollback_dir" >&2
        status=1
      else
        promoted=0
        printf 'Failed restored state preserved at: %s\n' "$failed_dir" >&2
      fi
    fi
    if ((stopped_live && was_running)); then
      if ! "${compose[@]}" up -d --no-build --wait --wait-timeout "$WAIT_TIMEOUT" echo-os; then
        printf 'CRITICAL: previous Echo state was restored but the service did not restart.\n' >&2
        status=1
      fi
    fi
    if [[ -d "$transaction_dir" ]]; then
      printf 'Unpromoted restore staging preserved at: %s\n' "$transaction_dir" >&2
    fi
  fi
  unset ECHO_BACKUP_PASSPHRASE ECHO_RESTORE_CONFIRM
  exit "$status"
}
trap cleanup EXIT INT TERM

if ((was_running)); then
  "${compose[@]}" stop --timeout 60 echo-os
  stopped_live=1
fi

"${compose[@]}" run --rm --no-deps --entrypoint python \
  -e ECHO_BACKUP_PASSPHRASE \
  -v "$BACKUP_DIR:/recovery-backup:ro" \
  -v "$STATE_PARENT:/state-parent" \
  echo-os -m appliance.state_backup restore \
  "/recovery-backup/$BACKUP_NAME" "$restored_container"

# Migrations are allowed only on the disposable restored copy.  Promotion then
# requires the exact current schema, valid credentials and a healthy audit chain.
"${compose[@]}" run --rm --no-deps --entrypoint python \
  -v "$STATE_PARENT:/state-parent" \
  echo-os -m appliance.state_schema --state-dir "$restored_container" --prepare
"${compose[@]}" run --rm --no-deps --entrypoint python \
  -v "$STATE_PARENT:/state-parent" \
  echo-os -m appliance.state_recovery --state-dir "$restored_container"

[[ -f "$BACKUP_PATH" && ! -L "$BACKUP_PATH" ]] || fail "verified backup was replaced"
[[ "$(backup_sha256)" == "$backup_sha" ]] || fail "verified backup changed before promotion"
[[ -d "$STATE_DIR" && ! -L "$STATE_DIR" ]] || fail "live state directory changed during restore"
mv -- "$STATE_DIR" "$rollback_dir"
live_displaced=1
if ! mv -- "$restored_dir" "$STATE_DIR"; then
  fail "could not promote the restored state directory"
fi
live_displaced=0
promoted=1
fsync_state_parent
rmdir -- "$transaction_dir"

"${compose[@]}" up -d --no-build --wait --wait-timeout "$WAIT_TIMEOUT" echo-os
"${compose[@]}" exec -T echo-os python -m appliance.state_recovery --state-dir /data

if ((was_running == 0)); then
  "${compose[@]}" stop --timeout 60 echo-os
fi

promoted=0
printf 'Echo state restore complete.\n'
printf 'Active state: %s\n' "$STATE_DIR"
printf 'Previous state retained for manual rollback: %s\n' "$rollback_dir"
printf 'Backup SHA-256: %s\n' "$backup_sha"
