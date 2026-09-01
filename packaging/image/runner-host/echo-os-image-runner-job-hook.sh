#!/usr/bin/env bash
# Host-owned pre/post job hook for the dedicated Echo OS image runner.
set -euo pipefail

CLEANUP=/usr/local/libexec/echo-os-image-runner-cleanup.py
HOST_WORK_ROOT=/srv/echo-os-image-runner
EXPECTED_WORKSPACE="$HOST_WORK_ROOT/echo-os/echo-os"
EXPECTED_SCRATCH="$HOST_WORK_ROOT/_temp"

[[ "${CI:-}" == true && "${GITHUB_ACTIONS:-}" == true ]] || {
  echo "Echo OS runner hook requires GitHub Actions job identity" >&2
  exit 1
}
[[ "${GITHUB_WORKSPACE:-}" == "$EXPECTED_WORKSPACE" && "${RUNNER_TEMP:-}" == "$EXPECTED_SCRATCH" ]] || {
  echo "Echo OS runner hook requires the dedicated /srv/echo-os-image-runner layout" >&2
  exit 1
}
[[ -x "$CLEANUP" && ! -L "$CLEANUP" ]] || {
  echo "Echo OS runner hook cleanup executable is unavailable" >&2
  exit 1
}

exec /usr/bin/timeout --foreground --signal=TERM 300s \
  /usr/bin/python3 "$CLEANUP" \
  --workspace "$GITHUB_WORKSPACE" \
  --scratch "$RUNNER_TEMP"
