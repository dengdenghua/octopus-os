#!/usr/bin/env bash
# Build the pinned Linux x86-64 Codex engine from the frozen Echo OS snapshot.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
OS_ROOT="$(cd "$HERE/../.." && pwd)"
AGENT_SRC="${ECHO_BUNDLE_SOURCE:-$OS_ROOT}"
if [ -n "${ECHO_AGENT_SRC:-}" ]; then
  AGENT_SRC="$ECHO_AGENT_SRC"
fi
DIST="$HERE/agent-codex"
DEPENDENCY_SRC="${ECHO_AGENT_DEPENDENCY_SRC:-$AGENT_SRC}"
IDENTITY="${ECHO_AGENT_IDENTITY_FILE:-}"
OWN_IDENTITY=0
STAGE=""
PYTHON="$($HERE/bundle-python.sh --print-interpreter)"

cleanup() {
  cleanup_exit=$?
  if [[ -n "$STAGE" && -d "$STAGE" ]]; then
    find "$STAGE" -depth -delete
  fi
  if [[ "$OWN_IDENTITY" == "1" && -n "$IDENTITY" && -f "$IDENTITY" ]]; then
    find "$IDENTITY" -delete
  fi
  return "$cleanup_exit"
}
trap cleanup EXIT INT TERM

PREPARE="$AGENT_SRC/extras/desktop/prepare-codex-linux.cjs"
[[ -f "$PREPARE" ]] || {
  echo "ERROR: Echo OS source has no Linux Codex packager: $PREPARE" >&2
  exit 1
}
command -v node >/dev/null 2>&1 || {
  echo "ERROR: Node.js is required to prepare the pinned Codex engine" >&2
  exit 1
}

WRAPPER_PACKAGE="$DEPENDENCY_SRC/frontend/node_modules/@openai/codex/package.json"
if [[ ! -f "$WRAPPER_PACKAGE" ]]; then
  command -v pnpm >/dev/null 2>&1 || {
    echo "ERROR: pnpm is required to install the locked Codex package" >&2
    exit 1
  }
  echo "Installing locked Agent frontend dependencies for the Codex package"
  pnpm --dir "$DEPENDENCY_SRC/frontend" install --frozen-lockfile --ignore-scripts
fi
[[ -f "$WRAPPER_PACKAGE" ]] || {
  echo "ERROR: locked @openai/codex package is unavailable: $WRAPPER_PACKAGE" >&2
  exit 1
}

if [[ -z "$IDENTITY" ]]; then
  IDENTITY="$(mktemp -t echo-agent-source.XXXXXX)"
  OWN_IDENTITY=1
  CAPTURE_ARGS=(capture-source --agent-src "$AGENT_SRC" --output "$IDENTITY")
  if [[ "${ECHO_AGENT_ALLOW_DIRTY:-0}" == "1" ]]; then
    CAPTURE_ARGS+=(--allow-dirty)
  fi
  "$PYTHON" "$HERE/agent_bundle.py" "${CAPTURE_ARGS[@]}"
fi
export SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-$($PYTHON -c \
  'import json,sys; print(json.load(open(sys.argv[1]))["source_date_epoch"])' "$IDENTITY")}"

echo "Building pinned Echo Codex engine for Linux x86-64"
NODE_PATH="$DEPENDENCY_SRC/frontend/node_modules${NODE_PATH:+:$NODE_PATH}" \
  ECHO_LINUX_ARCH=x64 node "$PREPARE"
SOURCE="$AGENT_SRC/extras/desktop/build/codex"
[[ -f "$SOURCE/echo-codex-bundle.json" && -x "$SOURCE/bin/codex" ]] || {
  echo "ERROR: Agent Codex packager produced an incomplete tree" >&2
  exit 1
}

STAGE="$(mktemp -d -t echo-agent-codex.XXXXXX)"
cp -a "$SOURCE/." "$STAGE/"
"$PYTHON" "$HERE/agent_bundle.py" record-codex \
  --agent-src "$AGENT_SRC" \
  --identity "$IDENTITY" \
  --dist "$STAGE"
"$PYTHON" "$HERE/agent_bundle.py" promote-dir \
  --stage "$STAGE" \
  --destination "$DIST"

echo "Prepared verified Echo Agent Codex engine: $DIST"
