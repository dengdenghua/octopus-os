#!/usr/bin/env bash
# Offline, encrypted Echo audit export to an administrator-provided external mount.

set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
EVIDENCE_DIR="${ECHO_AUDIT_EXPORT_DIR:-}"
EVIDENCE_MOUNTPOINT="${ECHO_AUDIT_EXPORT_MOUNTPOINT:-}"
KEEP_DAYS="${ECHO_AUDIT_KEEP_DAYS:-365}"
KEEP_MINIMUM="${ECHO_AUDIT_KEEP_MINIMUM:-12}"
STOP_TIMEOUT="${ECHO_AUDIT_STOP_TIMEOUT:-60}"
DOCKER_BIN="${ECHO_DOCKER_BIN:-docker}"
RELEASE_ENV="${ECHO_RELEASE_ENV:-$SCRIPT_DIR/echo-release.env}"
APPLIANCE_ENV="${ECHO_APPLIANCE_ENV:-$SCRIPT_DIR/appliance.env}"
PYTHON_BIN="${ECHO_HOST_PYTHON:-python3}"
EXTERNAL_STORAGE_VERIFIER="$SCRIPT_DIR/external_storage.py"

fail() {
  printf 'Echo audit evidence export failed: %s\n' "$*" >&2
  exit 1
}

[[ -n "$EVIDENCE_DIR" ]] ||
  fail "ECHO_AUDIT_EXPORT_DIR must name a pre-mounted external evidence directory"
[[ -n "$EVIDENCE_MOUNTPOINT" ]] ||
  fail "ECHO_AUDIT_EXPORT_MOUNTPOINT must name that external filesystem's mountpoint"
[[ "$KEEP_DAYS" =~ ^[0-9]+$ ]] && ((KEEP_DAYS >= 30 && KEEP_DAYS <= 3650)) ||
  fail "ECHO_AUDIT_KEEP_DAYS must be between 30 and 3650"
[[ "$KEEP_MINIMUM" =~ ^[0-9]+$ ]] && ((KEEP_MINIMUM >= 2 && KEEP_MINIMUM <= 1000)) ||
  fail "ECHO_AUDIT_KEEP_MINIMUM must be between 2 and 1000"
[[ "$STOP_TIMEOUT" =~ ^[0-9]+$ ]] && ((STOP_TIMEOUT >= 10 && STOP_TIMEOUT <= 600)) ||
  fail "ECHO_AUDIT_STOP_TIMEOUT must be between 10 and 600 seconds"

credential_file="${ECHO_AUDIT_EXPORT_PASSPHRASE_FILE:-}"
if [[ -z "$credential_file" && -n "${CREDENTIALS_DIRECTORY:-}" ]]; then
  credential_file="$CREDENTIALS_DIRECTORY/echo-audit-export-passphrase"
fi
if [[ -z "${ECHO_AUDIT_EXPORT_PASSPHRASE:-}" && -n "$credential_file" ]]; then
  [[ -f "$credential_file" && ! -L "$credential_file" ]] ||
    fail "audit export passphrase credential is missing or unsafe"
  ECHO_AUDIT_EXPORT_PASSPHRASE="$(<"$credential_file")"
fi
[[ ${#ECHO_AUDIT_EXPORT_PASSPHRASE} -ge 12 ]] ||
  fail "ECHO_AUDIT_EXPORT_PASSPHRASE must contain at least 12 characters"
export ECHO_AUDIT_EXPORT_PASSPHRASE

[[ -d "$EVIDENCE_DIR" && ! -L "$EVIDENCE_DIR" ]] ||
  fail "external evidence directory must already exist and must not be a symlink"
EVIDENCE_DIR="$(cd -- "$EVIDENCE_DIR" && pwd -P)"
[[ "$EVIDENCE_DIR" != *:* && "$EVIDENCE_DIR" != *$'\n'* ]] ||
  fail "external evidence directory contains unsafe bind-mount characters"
case "$EVIDENCE_DIR/" in
  "$SCRIPT_DIR/"*) fail "external evidence directory must not be inside the deployment tree" ;;
esac

command -v "$PYTHON_BIN" >/dev/null 2>&1 || fail "python3 is required on the NAS host"
[[ -f "$EXTERNAL_STORAGE_VERIFIER" && ! -L "$EXTERNAL_STORAGE_VERIFIER" ]] ||
  fail "external storage verifier is missing or unsafe"
storage_args=(
  verify
  --destination "$EVIDENCE_DIR"
  --mountpoint "$EVIDENCE_MOUNTPOINT"
  --deployment-root "$SCRIPT_DIR"
  --purpose audit-evidence
)
if [[ -e "$APPLIANCE_ENV" ]]; then
  storage_args+=(--appliance-env "$APPLIANCE_ENV")
fi
"$PYTHON_BIN" "$EXTERNAL_STORAGE_VERIFIER" "${storage_args[@]}" ||
  fail "audit destination is not a verified active external filesystem"

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
exec 9>"$EVIDENCE_DIR/.echo-audit-maintenance.lock"
flock -n 9 || fail "another Echo audit evidence job is already running"

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

restart_required=0
cleanup() {
  local status=$?
  trap - EXIT INT TERM
  if ((restart_required)); then
    printf 'Restoring the previously running Echo service...\n' >&2
    if ! "${compose[@]}" start echo-os; then
      printf 'Echo audit evidence export failed: could not restart echo-os\n' >&2
      status=1
    fi
  fi
  unset ECHO_AUDIT_EXPORT_PASSPHRASE
  exit "$status"
}
trap cleanup EXIT INT TERM

was_running=0
running_services="$("${compose[@]}" ps --status running --services)" ||
  fail "could not determine whether echo-os is running"
while IFS= read -r service; do
  [[ "$service" == "echo-os" ]] && was_running=1
done <<<"$running_services"

if ((was_running)); then
  restart_required=1
  "${compose[@]}" stop --timeout "$STOP_TIMEOUT" echo-os
fi

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
evidence_name="echo-audit-$timestamp.echo-audit"
evidence_path="$EVIDENCE_DIR/$evidence_name"

"${compose[@]}" run --rm --no-deps --entrypoint python \
  -e ECHO_AUDIT_EXPORT_PASSPHRASE \
  -v "$EVIDENCE_DIR:/evidence" \
  echo-os -m appliance.audit_evidence export "/evidence/$evidence_name" --state-dir /data

"${compose[@]}" run --rm --no-deps --entrypoint python \
  -e ECHO_AUDIT_EXPORT_PASSPHRASE \
  -v "$EVIDENCE_DIR:/evidence:ro" \
  echo-os -m appliance.audit_evidence verify "/evidence/$evidence_name"

if ((was_running)); then
  "${compose[@]}" start echo-os
  restart_required=0
fi

# Only encrypted evidence generations are aged out.  The live journal is never pruned.
"${compose[@]}" run --rm --no-deps --entrypoint python \
  -e ECHO_AUDIT_EXPORT_PASSPHRASE \
  -v "$EVIDENCE_DIR:/evidence" \
  echo-os -m appliance.audit_evidence prune /evidence \
  --keep-days "$KEEP_DAYS" --keep-minimum "$KEEP_MINIMUM"

printf 'Echo audit evidence export complete: %s\n' "$evidence_path"
