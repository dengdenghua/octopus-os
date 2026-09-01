#!/usr/bin/env bash
# Offline, verified Echo appliance-state backup for a Docker Compose NAS host.

set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
BACKUP_DIR="${ECHO_BACKUP_DIR:-}"
BACKUP_MOUNTPOINT="${ECHO_BACKUP_MOUNTPOINT:-}"
BACKUP_KEEP="${ECHO_BACKUP_KEEP:-7}"
BACKUP_PREFIX="${ECHO_BACKUP_PREFIX:-echo-state}"
STOP_TIMEOUT="${ECHO_BACKUP_STOP_TIMEOUT:-60}"
DOCKER_BIN="${ECHO_DOCKER_BIN:-docker}"
RELEASE_ENV="${ECHO_RELEASE_ENV:-$SCRIPT_DIR/echo-release.env}"
APPLIANCE_ENV="${ECHO_APPLIANCE_ENV:-$SCRIPT_DIR/appliance.env}"
PYTHON_BIN="${ECHO_HOST_PYTHON:-python3}"
EXTERNAL_STORAGE_VERIFIER="$SCRIPT_DIR/external_storage.py"

fail() {
  printf 'Echo state backup failed: %s\n' "$*" >&2
  exit 1
}

[[ "$BACKUP_KEEP" =~ ^[0-9]+$ ]] || fail "ECHO_BACKUP_KEEP must be an integer"
((BACKUP_KEEP >= 2 && BACKUP_KEEP <= 10000)) ||
  fail "ECHO_BACKUP_KEEP must be between 2 and 10000"
[[ "$STOP_TIMEOUT" =~ ^[0-9]+$ ]] ||
  fail "ECHO_BACKUP_STOP_TIMEOUT must be an integer"
((STOP_TIMEOUT >= 10 && STOP_TIMEOUT <= 600)) ||
  fail "ECHO_BACKUP_STOP_TIMEOUT must be between 10 and 600 seconds"
[[ "$BACKUP_PREFIX" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]] ||
  fail "ECHO_BACKUP_PREFIX contains unsafe characters"
[[ -n "$BACKUP_DIR" ]] ||
  fail "ECHO_BACKUP_DIR must name a directory on a pre-mounted external filesystem"
[[ -n "$BACKUP_MOUNTPOINT" ]] ||
  fail "ECHO_BACKUP_MOUNTPOINT must name that external filesystem's mountpoint"

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

[[ -d "$BACKUP_DIR" && ! -L "$BACKUP_DIR" ]] ||
  fail "backup directory must already exist and must not be a symlink"
BACKUP_DIR="$(cd -- "$BACKUP_DIR" && pwd -P)"
[[ "$BACKUP_DIR" != *:* && "$BACKUP_DIR" != *$'\n'* ]] ||
  fail "backup directory contains characters unsafe for a Docker bind mount"

command -v "$PYTHON_BIN" >/dev/null 2>&1 || fail "python3 is required on the NAS host"
[[ -f "$EXTERNAL_STORAGE_VERIFIER" && ! -L "$EXTERNAL_STORAGE_VERIFIER" ]] ||
  fail "external storage verifier is missing or unsafe"
storage_args=(
  verify
  --destination "$BACKUP_DIR"
  --mountpoint "$BACKUP_MOUNTPOINT"
  --deployment-root "$SCRIPT_DIR"
  --purpose state-backup
)
if [[ -e "$APPLIANCE_ENV" ]]; then
  storage_args+=(--appliance-env "$APPLIANCE_ENV")
fi
"$PYTHON_BIN" "$EXTERNAL_STORAGE_VERIFIER" "${storage_args[@]}" ||
  fail "backup destination is not a verified active external filesystem"

command -v flock >/dev/null 2>&1 || fail "flock is required on the NAS host"

MAINTENANCE_LOCK="${ECHO_MAINTENANCE_LOCK:-/run/lock/echo-os-appliance-maintenance.lock}"
[[ "$MAINTENANCE_LOCK" == /* && "$MAINTENANCE_LOCK" != "/" && "$MAINTENANCE_LOCK" != *$'\n'* ]] ||
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
exec 9>"$BACKUP_DIR/.echo-backup-maintenance.lock"
flock -n 9 || fail "another Echo backup job is already running"

compose=("$DOCKER_BIN" compose)
if [[ -e "$APPLIANCE_ENV" ]]; then
  [[ -f "$APPLIANCE_ENV" && ! -L "$APPLIANCE_ENV" ]] ||
    fail "appliance environment file is unsafe"
  compose+=(--env-file "$APPLIANCE_ENV")
fi
if [[ -e "$RELEASE_ENV" ]]; then
  [[ -f "$RELEASE_ENV" && ! -L "$RELEASE_ENV" ]] ||
    fail "release environment file is unsafe"
  compose+=(--env-file "$RELEASE_ENV")
fi
compose+=(
  --project-directory "$SCRIPT_DIR"
  -f "$SCRIPT_DIR/docker-compose.yml"
)

restart_required=0
cleanup() {
  local status=$?
  trap - EXIT INT TERM
  if ((restart_required)); then
    printf 'Restoring the previously running Echo service...\n' >&2
    if ! "${compose[@]}" start echo-os; then
      printf 'Echo state backup failed: could not restart echo-os\n' >&2
      status=1
    fi
  fi
  unset ECHO_BACKUP_PASSPHRASE
  exit "$status"
}
trap cleanup EXIT INT TERM

was_running=0
running_services="$("${compose[@]}" ps --status running --services)" ||
  fail "could not determine whether echo-os is running"
while IFS= read -r service; do
  if [[ "$service" == "echo-os" ]]; then
    was_running=1
    break
  fi
done <<<"$running_services"

if ((was_running)); then
  restart_required=1
  "${compose[@]}" stop --timeout "$STOP_TIMEOUT" echo-os
fi

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_name="$BACKUP_PREFIX-$timestamp.echo-backup"
backup_path="$BACKUP_DIR/$backup_name"

"${compose[@]}" run --rm --no-deps --entrypoint python \
  -e ECHO_BACKUP_PASSPHRASE \
  -v "$BACKUP_DIR:/backup" \
  echo-os -m appliance.state_backup export \
  "/backup/$backup_name" --state-dir /data --nas-root /data/nas

"${compose[@]}" run --rm --no-deps --entrypoint python \
  -e ECHO_BACKUP_PASSPHRASE \
  -v "$BACKUP_DIR:/backup:ro" \
  echo-os -m appliance.state_backup verify "/backup/$backup_name"

if ((was_running)); then
  "${compose[@]}" start echo-os
  restart_required=0
fi

# Retention runs only after the new backup verifies and the original service
# state has been restored. The Python command re-verifies the newest generation
# before deleting any old managed backup.
"${compose[@]}" run --rm --no-deps --entrypoint python \
  -e ECHO_BACKUP_PASSPHRASE \
  -v "$BACKUP_DIR:/backup" \
  echo-os -m appliance.state_backup prune /backup \
  --keep "$BACKUP_KEEP" --prefix "$BACKUP_PREFIX"

printf 'Echo state backup complete: %s\n' "$backup_path"
