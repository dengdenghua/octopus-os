#!/usr/bin/env bash
# Bind root-owned Echo cleanup hooks to one already registered Actions runner.
set -euo pipefail
umask 077

[[ $# -eq 2 ]] || {
  echo "usage: $0 ABSOLUTE_RUNNER_APPLICATION_DIR RUNNER_USER" >&2
  exit 2
}
RUNNER_APPLICATION_DIR="$1"
RUNNER_USER="$2"
HOOK=/usr/local/libexec/echo-os-image-runner-job-hook.sh
CLEANUP=/usr/local/libexec/echo-os-image-runner-cleanup.py
REGISTRATION_VERIFIER=/usr/local/libexec/echo-os-image-runner-registration.py

[[ "$(id -u)" -eq 0 ]] || {
  echo "runner hook configuration requires root" >&2
  exit 1
}
[[ "$RUNNER_APPLICATION_DIR" == /* && "$RUNNER_APPLICATION_DIR" != / && \
   ! -L "$RUNNER_APPLICATION_DIR" && -d "$RUNNER_APPLICATION_DIR" ]] || {
  echo "runner application directory must be absolute, existing and non-symlink" >&2
  exit 2
}
[[ "$RUNNER_APPLICATION_DIR" == /opt/actions-runner ]] || {
  echo "runner application directory must be the dedicated /opt/actions-runner path" >&2
  exit 2
}
CANONICAL_RUNNER_APPLICATION_DIR="$(realpath "$RUNNER_APPLICATION_DIR")"
[[ "$CANONICAL_RUNNER_APPLICATION_DIR" == "$RUNNER_APPLICATION_DIR" ]] || {
  echo "runner application directory must be canonical" >&2
  exit 2
}
[[ "$RUNNER_USER" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || {
  echo "invalid runner service account" >&2
  exit 2
}
id "$RUNNER_USER" >/dev/null 2>&1 || {
  echo "runner service account does not exist: $RUNNER_USER" >&2
  exit 2
}
RUNNER_UID="$(id -u "$RUNNER_USER")"
RUNNER_GID="$(id -g "$RUNNER_USER")"
[[ "$RUNNER_UID" -ne 0 ]] || {
  echo "the Actions runner must not use root as its service account" >&2
  exit 2
}
[[ "$(stat -c '%u' "$RUNNER_APPLICATION_DIR")" == "$RUNNER_UID" ]] || {
  echo "runner application directory must be owned by the service account" >&2
  exit 2
}
for runner_file in .runner .credentials .credentials_rsaparams .service config.sh run.sh; do
  path="$RUNNER_APPLICATION_DIR/$runner_file"
  [[ -f "$path" && ! -L "$path" ]] || {
    echo "registered runner file is unavailable: $runner_file" >&2
    exit 2
  }
done
for installed_file in "$HOOK" "$CLEANUP" "$REGISTRATION_VERIFIER"; do
  [[ -f "$installed_file" && ! -L "$installed_file" && \
     "$(stat -c '%u:%g:%a' "$installed_file")" == 0:0:755 ]] || {
    echo "root-owned runner hook is unavailable: $installed_file" >&2
    exit 1
  }
done

for private_runner_file in .runner .credentials .credentials_rsaparams .service; do
  path="$RUNNER_APPLICATION_DIR/$private_runner_file"
  [[ "$(stat -c '%u' "$path")" == "$RUNNER_UID" ]] || {
    echo "registered runner file must be owned by the service account: $private_runner_file" >&2
    exit 2
  }
  chmod 0600 "$path"
done

ENV_FILE="$RUNNER_APPLICATION_DIR/.env"
if [[ -e "$ENV_FILE" || -L "$ENV_FILE" ]]; then
  [[ -f "$ENV_FILE" && ! -L "$ENV_FILE" && \
     "$(stat -c '%u' "$ENV_FILE")" == "$RUNNER_UID" && \
     "$(stat -c '%s' "$ENV_FILE")" -le 65536 ]] || {
    echo "runner .env is unsafe or oversized" >&2
    exit 1
  }
fi

TEMP_ENV="$(mktemp "$RUNNER_APPLICATION_DIR/.env.echo.XXXXXX")"
cleanup() {
  rm -f -- "$TEMP_ENV"
}
trap cleanup EXIT INT TERM
if [[ -f "$ENV_FILE" ]]; then
  awk '
    $0 !~ /^[[:space:]]*ACTIONS_RUNNER_HOOK_JOB_STARTED[[:space:]]*=/ &&
    $0 !~ /^[[:space:]]*ACTIONS_RUNNER_HOOK_JOB_COMPLETED[[:space:]]*=/ { print }
  ' "$ENV_FILE" >"$TEMP_ENV"
fi
printf '%s\n' \
  "ACTIONS_RUNNER_HOOK_JOB_STARTED=$HOOK" \
  "ACTIONS_RUNNER_HOOK_JOB_COMPLETED=$HOOK" \
  >>"$TEMP_ENV"
chown "$RUNNER_UID:$RUNNER_GID" "$TEMP_ENV"
chmod 0600 "$TEMP_ENV"
mv -fT -- "$TEMP_ENV" "$ENV_FILE"
trap - EXIT INT TERM

[[ "$(grep -Fxc "ACTIONS_RUNNER_HOOK_JOB_STARTED=$HOOK" "$ENV_FILE")" -eq 1 && \
   "$(grep -Fxc "ACTIONS_RUNNER_HOOK_JOB_COMPLETED=$HOOK" "$ENV_FILE")" -eq 1 ]] || {
  echo "runner hook environment did not verify after publication" >&2
  exit 1
}
runuser -u "$RUNNER_USER" -- \
  "$REGISTRATION_VERIFIER" --runner-user "$RUNNER_USER"
echo "ECHO_IMAGE_RUNNER_HOOKS_READY app=$RUNNER_APPLICATION_DIR user=$RUNNER_USER"
echo "Start or restart the Actions runner service before assigning an Echo OS job."
