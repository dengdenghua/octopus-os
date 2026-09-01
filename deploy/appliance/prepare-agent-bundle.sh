#!/usr/bin/env bash
# Prepare wheel + runtime resources + Codex from one immutable OS snapshot.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
OS_ROOT="$(cd "$HERE/../.." && pwd)"
AGENT_SRC="$OS_ROOT"
IDENTITY="$(mktemp -t echo-agent-source.XXXXXX)"
PYTHON="$("$HERE/bundle-python.sh" --print-interpreter)"
SNAPSHOT_ROOT=""

cleanup() {
  cleanup_exit=$?
  if [ -f "$IDENTITY" ]; then
    find "$IDENTITY" -delete
  fi
  if [ -n "$SNAPSHOT_ROOT" ] && [ -d "$SNAPSHOT_ROOT" ]; then
    find "$SNAPSHOT_ROOT" -depth -delete
  fi
  return "$cleanup_exit"
}
trap cleanup EXIT

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
export SOURCE_DATE_EPOCH="$("$PYTHON" -c \
  'import json,sys; print(json.load(open(sys.argv[1]))["source_date_epoch"])' "$IDENTITY")"

if [ "$("$PYTHON" -c \
  'import json,sys; print(int(json.load(open(sys.argv[1]))["dirty"]))' "$IDENTITY")" = "1" ]; then
  # Dependency installs are intentionally excluded from the frozen source
  # snapshot. Keep the captured checkout as the only trusted lookup root for
  # the pinned Codex npm package.
  export ECHO_AGENT_DEPENDENCY_SRC="$AGENT_SRC"
  SNAPSHOT_ROOT="$(mktemp -d -t echo-agent-source-snapshot.XXXXXX)"
  "$PYTHON" "$HERE/agent_bundle.py" snapshot-source \
    --agent-src "$AGENT_SRC" \
    --identity "$IDENTITY" \
    --destination "$SNAPSHOT_ROOT/source"
  AGENT_SRC="$SNAPSHOT_ROOT/source"
fi

export ECHO_AGENT_DEPENDENCY_SRC="${ECHO_AGENT_DEPENDENCY_SRC:-$AGENT_SRC}"

export ECHO_BUNDLE_SOURCE="$AGENT_SRC"
export ECHO_AGENT_IDENTITY_FILE="$IDENTITY"
"$PYTHON" "$HERE/agent_bundle.py" verify-agent-api --echo-src "$AGENT_SRC"
"$HERE/prepare-agent-wheel.sh"
"$HERE/prepare-agent-resources.sh"
"$HERE/prepare-agent-codex.sh"

"$PYTHON" "$HERE/agent_bundle.py" assemble \
  --bundle-root "$HERE" \
  --identity "$IDENTITY" \
  --output "$HERE/agent-bundle.json"
"$PYTHON" "$HERE/agent_bundle.py" verify \
  --bundle-root "$HERE" \
  --manifest "$HERE/agent-bundle.json"

echo "Agent appliance bundle is complete and internally consistent."
