#!/usr/bin/env bash
# Recover a crash-interrupted immutable Echo appliance image switch.

set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
DOCKER_BIN="${ECHO_DOCKER_BIN:-docker}"
PYTHON_BIN="${ECHO_HOST_PYTHON:-python3}"
TRANSACTION_TOOL="$SCRIPT_DIR/upgrade_transaction.py"
TRANSACTION_PATH="${ECHO_UPGRADE_TRANSACTION:-$SCRIPT_DIR/.echo-upgrade-transaction.json}"
RELEASE_ENV="${ECHO_RELEASE_ENV:-$SCRIPT_DIR/echo-release.env}"
APPLIANCE_ENV="${ECHO_APPLIANCE_ENV:-$SCRIPT_DIR/appliance.env}"
WAIT_TIMEOUT="${ECHO_UPGRADE_WAIT_TIMEOUT:-180}"

fail() {
  printf 'Echo upgrade recovery failed: %s\n' "$*" >&2
  exit 1
}

[[ "$WAIT_TIMEOUT" =~ ^[0-9]+$ ]] || fail "ECHO_UPGRADE_WAIT_TIMEOUT must be an integer"
((WAIT_TIMEOUT >= 30 && WAIT_TIMEOUT <= 1800)) ||
  fail "ECHO_UPGRADE_WAIT_TIMEOUT must be between 30 and 1800 seconds"
[[ -x "$TRANSACTION_TOOL" && ! -L "$TRANSACTION_TOOL" ]] ||
  fail "upgrade transaction helper is missing or unsafe"
command -v "$DOCKER_BIN" >/dev/null 2>&1 || fail "Docker is required on the NAS host"
command -v "$PYTHON_BIN" >/dev/null 2>&1 || fail "python3 is required on the NAS host"
command -v flock >/dev/null 2>&1 || fail "flock is required on the NAS host"

[[ "$TRANSACTION_PATH" == /* && "$TRANSACTION_PATH" != "/" &&
   "$TRANSACTION_PATH" != *$'\n'* ]] || fail "upgrade transaction path is unsafe"
if [[ ! -e "$TRANSACTION_PATH" && ! -L "$TRANSACTION_PATH" ]]; then
  printf 'No pending Echo appliance upgrade transaction.\n'
  exit 0
fi
[[ -f "$TRANSACTION_PATH" && ! -L "$TRANSACTION_PATH" ]] ||
  fail "upgrade transaction is unsafe"

MAINTENANCE_LOCK="${ECHO_MAINTENANCE_LOCK:-/run/lock/echo-os-appliance-maintenance.lock}"
[[ "$MAINTENANCE_LOCK" == /* && "$MAINTENANCE_LOCK" != "/" &&
   "$MAINTENANCE_LOCK" != *$'\n'* ]] || fail "maintenance lock path is unsafe"
maintenance_parent="$(dirname -- "$MAINTENANCE_LOCK")"
[[ -d "$maintenance_parent" && ! -L "$maintenance_parent" ]] ||
  fail "maintenance lock parent is missing or unsafe"
maintenance_parent="$(cd -- "$maintenance_parent" && pwd -P)"
MAINTENANCE_LOCK="$maintenance_parent/$(basename -- "$MAINTENANCE_LOCK")"
if [[ -e "$MAINTENANCE_LOCK" && (! -f "$MAINTENANCE_LOCK" || -L "$MAINTENANCE_LOCK") ]]; then
  fail "maintenance lock file is unsafe"
fi
exec 7>"$MAINTENANCE_LOCK"
flock -n 7 || fail "another Echo maintenance operation is already running"
exec 8>"$SCRIPT_DIR/.echo-upgrade.lock"
flock -n 8 || fail "another Echo upgrade or recovery is already running"

recovery="$($PYTHON_BIN "$TRANSACTION_TOOL" recover \
  --journal "$TRANSACTION_PATH" --release-env "$RELEASE_ENV")" ||
  fail "transaction selection could not be restored"
previous_image="$(printf '%s' "$recovery" | "$PYTHON_BIN" -c '
import json, re, sys
value = json.load(sys.stdin)
image = value.get("previousImage")
if not isinstance(image, str) or re.fullmatch(r"[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64}", image) is None:
    raise SystemExit("invalid previous image")
print(image)
')" || fail "transaction recovery returned an invalid previous image"

compose=("$DOCKER_BIN" compose)
if [[ -e "$APPLIANCE_ENV" ]]; then
  [[ -f "$APPLIANCE_ENV" && ! -L "$APPLIANCE_ENV" ]] ||
    fail "appliance environment file is unsafe"
  compose+=(--env-file "$APPLIANCE_ENV")
fi
compose+=(
  --env-file "$RELEASE_ENV"
  --project-directory "$SCRIPT_DIR"
  -f "$SCRIPT_DIR/docker-compose.yml"
)
"${compose[@]}" up -d --no-build --wait --wait-timeout "$WAIT_TIMEOUT" ||
  fail "previous immutable release did not become healthy"

main_image="$($DOCKER_BIN inspect --format '{{.Config.Image}}' echo-os)" ||
  fail "recovered echo-os container cannot be inspected"
proxy_image="$($DOCKER_BIN inspect --format '{{.Config.Image}}' echo-docker-control)" ||
  fail "recovered Docker-control container cannot be inspected"
[[ "$main_image" == "$previous_image" && "$proxy_image" == "$previous_image" ]] ||
  fail "recovered containers do not use the previous immutable image"

"$PYTHON_BIN" "$TRANSACTION_TOOL" finish-recovery \
  --journal "$TRANSACTION_PATH" --release-env "$RELEASE_ENV" >/dev/null ||
  fail "recovered transaction could not be committed"
printf 'Echo appliance upgrade recovery complete: %s\n' "$previous_image"
