#!/usr/bin/env bash
# Export deployment resources from the same Echo OS source as the wheel.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
OS_ROOT="$(cd "$HERE/../.." && pwd)"
AGENT_SRC="${ECHO_BUNDLE_SOURCE:-$OS_ROOT}"
if [ -n "${ECHO_AGENT_SRC:-}" ]; then
  AGENT_SRC="$ECHO_AGENT_SRC"
fi
DIST="$HERE/agent-resources"
IDENTITY="${ECHO_AGENT_IDENTITY_FILE:-}"
OWN_IDENTITY=0
PYTHON="$("$HERE/bundle-python.sh" --print-interpreter)"

cleanup() {
  cleanup_exit=$?
  if [ "$OWN_IDENTITY" = "1" ] && [ -n "$IDENTITY" ] && [ -f "$IDENTITY" ]; then
    find "$IDENTITY" -delete
  fi
  return "$cleanup_exit"
}
trap cleanup EXIT

if [ ! -f "$AGENT_SRC/pyproject.toml" ]; then
  echo "ERROR: Echo OS source not found: $AGENT_SRC" >&2
  exit 1
fi

if [ -z "$IDENTITY" ]; then
  IDENTITY="$(mktemp -t echo-agent-source.XXXXXX)"
  OWN_IDENTITY=1
  if [ "${ECHO_AGENT_ALLOW_DIRTY:-0}" = "1" ]; then
    "$PYTHON" "$HERE/agent_bundle.py" capture-source \
      --agent-src "$AGENT_SRC" \
      --output "$IDENTITY" \
      --allow-dirty
  else
    "$PYTHON" "$HERE/agent_bundle.py" capture-source \
      --agent-src "$AGENT_SRC" \
      --output "$IDENTITY"
  fi
fi
export SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-$("$PYTHON" -c \
  'import json,sys; print(json.load(open(sys.argv[1]))["source_date_epoch"])' "$IDENTITY")}"

"$PYTHON" "$HERE/agent_bundle.py" export-resources \
  --agent-src "$AGENT_SRC" \
  --identity "$IDENTITY" \
  --dist "$DIST"

echo "Prepared verified Echo Agent runtime resources: $DIST"
