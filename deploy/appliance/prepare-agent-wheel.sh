#!/usr/bin/env bash
# Build the unified Echo OS wheel and bind it to one source identity.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
OS_ROOT="$(cd "$HERE/../.." && pwd)"
AGENT_SRC="${ECHO_BUNDLE_SOURCE:-$OS_ROOT}"
if [ -n "${ECHO_AGENT_SRC:-}" ]; then
  AGENT_SRC="$ECHO_AGENT_SRC"
fi
DIST="$HERE/agent-dist"
IDENTITY="${ECHO_AGENT_IDENTITY_FILE:-}"
OWN_IDENTITY=0
STAGE=""
PYTHON="$("$HERE/bundle-python.sh" --print-interpreter)"
LOCK_TOOL="$HERE/dependency_lock.py"
COMMITTED_BUILD_LOCK="$HERE/build-requirements.lock"
COMMITTED_RUNTIME_LOCK="$HERE/runtime-requirements.lock"
COMMITTED_LOCK_METADATA="$HERE/python-dependency-lock.json"

cleanup() {
  cleanup_exit=$?
  if [ -n "$STAGE" ] && [ -d "$STAGE" ]; then
    find "$STAGE" -depth -delete
  fi
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
if ! command -v uv >/dev/null 2>&1; then
  echo "ERROR: pinned uv 0.11.25 is required for a reproducible Agent wheel." >&2
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

STAGE="$(mktemp -d -t echo-agent-dist.XXXXXX)"
STAGE_BUILD_LOCK="$STAGE/build-requirements.lock"
STAGE_RUNTIME_LOCK="$STAGE/runtime-requirements.lock"
STAGE_LOCK_METADATA="$STAGE/python-dependency-lock.json"

if [ "${ECHO_AGENT_ALLOW_DIRTY:-0}" = "1" ]; then
  echo "Resolving ephemeral Python locks for the frozen dirty Agent QA source"
  "$PYTHON" "$LOCK_TOOL" refresh \
    --os-project "$OS_ROOT/pyproject.toml" \
    --agent-project "$AGENT_SRC/pyproject.toml" \
    --build-lock "$STAGE_BUILD_LOCK" \
    --runtime-lock "$STAGE_RUNTIME_LOCK" \
    --metadata "$STAGE_LOCK_METADATA"
else
  echo "Verifying release Python locks against the clean Agent source"
  "$PYTHON" "$LOCK_TOOL" verify \
    --os-project "$OS_ROOT/pyproject.toml" \
    --agent-project "$AGENT_SRC/pyproject.toml" \
    --build-lock "$COMMITTED_BUILD_LOCK" \
    --runtime-lock "$COMMITTED_RUNTIME_LOCK" \
    --metadata "$COMMITTED_LOCK_METADATA"
  cp "$COMMITTED_BUILD_LOCK" "$STAGE_BUILD_LOCK"
  cp "$COMMITTED_RUNTIME_LOCK" "$STAGE_RUNTIME_LOCK"
  cp "$COMMITTED_LOCK_METADATA" "$STAGE_LOCK_METADATA"
fi

echo "Building unified Echo OS wheel: $AGENT_SRC"
uv build --quiet --wheel --out-dir "$STAGE" \
  --build-constraints "$STAGE_BUILD_LOCK" \
  --require-hashes \
  "$AGENT_SRC"

"$PYTHON" "$HERE/agent_bundle.py" record-wheel \
  --agent-src "$AGENT_SRC" \
  --identity "$IDENTITY" \
  --dist "$STAGE"
"$PYTHON" "$HERE/agent_bundle.py" promote-dir \
  --stage "$STAGE" \
  --destination "$DIST"

echo "Prepared verified Echo OS wheel: $DIST"
