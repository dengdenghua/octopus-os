#!/usr/bin/env bash
# Conservative Echo appliance upgrade: immutable image, backup, preflight, rollback.

set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
TARGET_IMAGE="${1:-}"
DOCKER_BIN="${ECHO_DOCKER_BIN:-docker}"
PYTHON_BIN="${ECHO_HOST_PYTHON:-python3}"
RELEASE_ENV="${ECHO_RELEASE_ENV:-$SCRIPT_DIR/echo-release.env}"
APPLIANCE_ENV="${ECHO_APPLIANCE_ENV:-$SCRIPT_DIR/appliance.env}"
TRANSACTION_TOOL="$SCRIPT_DIR/upgrade_transaction.py"
TRANSACTION_PATH="${ECHO_UPGRADE_TRANSACTION:-$SCRIPT_DIR/.echo-upgrade-transaction.json}"
WAIT_TIMEOUT="${ECHO_UPGRADE_WAIT_TIMEOUT:-180}"

fail() {
  printf 'Echo upgrade failed: %s\n' "$*" >&2
  exit 1
}

[[ "$TARGET_IMAGE" =~ ^[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64}$ ]] ||
  fail "target image must be an immutable registry reference ending in @sha256:<64 hex>"
[[ "$WAIT_TIMEOUT" =~ ^[0-9]+$ ]] || fail "ECHO_UPGRADE_WAIT_TIMEOUT must be an integer"
((WAIT_TIMEOUT >= 30 && WAIT_TIMEOUT <= 1800)) ||
  fail "ECHO_UPGRADE_WAIT_TIMEOUT must be between 30 and 1800 seconds"
[[ -n "$RELEASE_ENV" && "$RELEASE_ENV" != "/" && "$RELEASE_ENV" != *$'\n'* ]] ||
  fail "release environment path is unsafe"

command -v flock >/dev/null 2>&1 || fail "flock is required on the NAS host"
command -v "$PYTHON_BIN" >/dev/null 2>&1 || fail "python3 is required on the NAS host"
[[ -x "$TRANSACTION_TOOL" && ! -L "$TRANSACTION_TOOL" ]] ||
  fail "upgrade transaction helper is missing or unsafe"
[[ "$TRANSACTION_PATH" == /* && "$TRANSACTION_PATH" != "/" &&
   "$TRANSACTION_PATH" != *$'\n'* ]] || fail "upgrade transaction path is unsafe"

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
exec 8>"$SCRIPT_DIR/.echo-upgrade.lock"
flock -n 8 || fail "another Echo upgrade is already running"
[[ ! -e "$TRANSACTION_PATH" && ! -L "$TRANSACTION_PATH" ]] ||
  fail "a pending upgrade transaction must be recovered before another upgrade"

release_parent="$(dirname -- "$RELEASE_ENV")"
mkdir -p -- "$release_parent"
release_parent="$(cd -- "$release_parent" && pwd -P)"
RELEASE_ENV="$release_parent/$(basename -- "$RELEASE_ENV")"
if [[ -e "$RELEASE_ENV" && (! -f "$RELEASE_ENV" || -L "$RELEASE_ENV") ]]; then
  fail "release environment file is unsafe"
fi
if [[ -e "$APPLIANCE_ENV" && (! -f "$APPLIANCE_ENV" || -L "$APPLIANCE_ENV") ]]; then
  fail "appliance environment file is unsafe"
fi

had_previous_release=0
if [[ -f "$RELEASE_ENV" ]]; then
  had_previous_release=1
fi

compose_base=(
  "$DOCKER_BIN" compose
)
if [[ -f "$APPLIANCE_ENV" ]]; then
  compose_base+=(--env-file "$APPLIANCE_ENV")
fi
compose_base+=(--project-directory "$SCRIPT_DIR" -f "$SCRIPT_DIR/docker-compose.yml")

previous_running_image="$("$DOCKER_BIN" inspect --format '{{.Config.Image}}' echo-os)" ||
  fail "the currently deployed echo-os container could not be inspected"
[[ "$previous_running_image" =~ ^[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64}$ ]] ||
  fail "the running container is not selected by an immutable digest"
[[ "$previous_running_image" != "$TARGET_IMAGE" ]] ||
  fail "the requested immutable image is already running"
if ((had_previous_release)); then
  declared_image=""
  while IFS= read -r line || [[ -n "$line" ]]; do
    case "$line" in
      "" | \#*) ;;
      ECHO_OS_IMAGE=*)
        [[ -z "$declared_image" ]] || fail "release environment declares ECHO_OS_IMAGE more than once"
        declared_image="${line#ECHO_OS_IMAGE=}"
        ;;
      *) fail "release environment contains an unsupported setting" ;;
    esac
  done <"$RELEASE_ENV"
  [[ -n "$declared_image" ]] || fail "release environment does not declare ECHO_OS_IMAGE"
  [[ "$declared_image" == "$previous_running_image" ]] ||
    fail "running image and release environment disagree"
fi

switched=0
cleanup() {
  local status=$?
  local -a rollback_compose
  local rollback_main=""
  local rollback_proxy=""
  trap - EXIT INT TERM
  if ((status != 0 && switched)); then
    printf 'Upgrade did not become healthy; restoring the previous image selection...\n' >&2
    if ! "$PYTHON_BIN" "$TRANSACTION_TOOL" recover \
      --journal "$TRANSACTION_PATH" --release-env "$RELEASE_ENV" >/dev/null; then
      printf 'Echo upgrade transaction selection could not be recovered.\n' >&2
      status=1
    else
      rollback_compose=("$DOCKER_BIN" compose)
      if [[ -f "$APPLIANCE_ENV" ]]; then
        rollback_compose+=(--env-file "$APPLIANCE_ENV")
      fi
      rollback_compose+=(
        --env-file "$RELEASE_ENV"
        --project-directory "$SCRIPT_DIR"
        -f "$SCRIPT_DIR/docker-compose.yml"
      )
      if "${rollback_compose[@]}" up -d --no-build --wait \
        --wait-timeout "$WAIT_TIMEOUT"; then
        rollback_main="$("$DOCKER_BIN" inspect --format '{{.Config.Image}}' echo-os)" ||
          rollback_main=""
        rollback_proxy="$("$DOCKER_BIN" inspect --format '{{.Config.Image}}' echo-docker-control)" ||
          rollback_proxy=""
      fi
      if [[ "$rollback_main" == "$previous_running_image" &&
         "$rollback_proxy" == "$previous_running_image" ]] &&
        "$PYTHON_BIN" "$TRANSACTION_TOOL" finish-recovery \
          --journal "$TRANSACTION_PATH" --release-env "$RELEASE_ENV" >/dev/null; then
        :
      else
        status=1
      fi
    fi
    if ((status != 0)); then
      printf 'Echo upgrade rollback also failed; keep the verified backup and inspect Docker logs.\n' >&2
    fi
  fi
  exit "$status"
}
trap cleanup EXIT INT TERM

# A target is never pulled or selected until the currently deployed state has a
# fresh authenticated backup and the original service state has been restored.
ECHO_APPLIANCE_ENV="$APPLIANCE_ENV" ECHO_RELEASE_ENV="$RELEASE_ENV" \
  "$SCRIPT_DIR/backup-state.sh"
"$DOCKER_BIN" pull "$TARGET_IMAGE"

schema_report="$({
  ECHO_OS_IMAGE="$TARGET_IMAGE" "${compose_base[@]}" run --rm --no-deps \
    --entrypoint python echo-os -m appliance.state_schema --state-dir /data
})" || fail "target image cannot read the current state schema"

schema_decision="$(printf '%s' "$schema_report" | "$PYTHON_BIN" -c '
import json, sys
report = json.load(sys.stdin)
required = {"compatible", "migrationRequired", "version", "currentRuntimeVersion"}
if not required.issubset(report):
    raise SystemExit("target image returned an incomplete schema report")
if report["compatible"] is not True:
    raise SystemExit("target image is not state-compatible")
print("migration" if report["migrationRequired"] is True else "safe")
')" || fail "target image returned an invalid state schema report"
if [[ "$schema_decision" != "safe" ]]; then
  fail "target requires a state migration; use the reviewed migration runbook instead of automatic upgrade"
fi

previous_release_present=no
if ((had_previous_release)); then
  previous_release_present=yes
fi
"$PYTHON_BIN" "$TRANSACTION_TOOL" begin \
  --journal "$TRANSACTION_PATH" \
  --release-env "$RELEASE_ENV" \
  --previous-image "$previous_running_image" \
  --target-image "$TARGET_IMAGE" \
  --previous-release-present "$previous_release_present" >/dev/null
switched=1
"$PYTHON_BIN" "$TRANSACTION_TOOL" select \
  --journal "$TRANSACTION_PATH" --release-env "$RELEASE_ENV" >/dev/null

compose_target=(
  "$DOCKER_BIN" compose
)
if [[ -f "$APPLIANCE_ENV" ]]; then
  compose_target+=(--env-file "$APPLIANCE_ENV")
fi
compose_target+=(
  --env-file "$RELEASE_ENV"
  --project-directory "$SCRIPT_DIR"
  -f "$SCRIPT_DIR/docker-compose.yml"
)
"${compose_target[@]}" up -d --no-build --wait --wait-timeout "$WAIT_TIMEOUT"

main_image="$("$DOCKER_BIN" inspect --format '{{.Config.Image}}' echo-os)"
proxy_image="$("$DOCKER_BIN" inspect --format '{{.Config.Image}}' echo-docker-control)"
[[ "$main_image" == "$TARGET_IMAGE" && "$proxy_image" == "$TARGET_IMAGE" ]] ||
  fail "running containers do not both use the selected immutable image"

"$PYTHON_BIN" "$TRANSACTION_TOOL" commit \
  --journal "$TRANSACTION_PATH" --release-env "$RELEASE_ENV" >/dev/null
switched=0
printf 'Echo upgrade complete: %s\n' "$TARGET_IMAGE"
