#!/usr/bin/env bash
# Select a Python new enough to run agent_bundle.py, preferring this OS checkout's venv.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
OS_ROOT="$(cd "$HERE/../.." && pwd)"

is_supported() {
  "$1" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' >/dev/null 2>&1
}

PYTHON_BIN="${ECHO_BUNDLE_PYTHON:-}"
if [ -n "$PYTHON_BIN" ] && ! is_supported "$PYTHON_BIN"; then
  echo "ERROR: ECHO_BUNDLE_PYTHON must point to Python 3.11 or newer." >&2
  exit 1
fi
if [ -z "$PYTHON_BIN" ] && [ -x "$OS_ROOT/.venv/bin/python" ] \
  && is_supported "$OS_ROOT/.venv/bin/python"; then
  PYTHON_BIN="$OS_ROOT/.venv/bin/python"
fi
if [ -z "$PYTHON_BIN" ] && command -v python3 >/dev/null 2>&1 \
  && is_supported "$(command -v python3)"; then
  PYTHON_BIN="$(command -v python3)"
fi
if [ -z "$PYTHON_BIN" ] && command -v uv >/dev/null 2>&1; then
  PYTHON_BIN="$(uv python find '>=3.11')"
fi
if [ -z "$PYTHON_BIN" ] || ! is_supported "$PYTHON_BIN"; then
  echo "ERROR: Python 3.11 or newer is required to prepare the Echo Agent bundle." >&2
  exit 1
fi

if [ "${1:-}" = "--print-interpreter" ]; then
  printf '%s\n' "$PYTHON_BIN"
  exit 0
fi
exec "$PYTHON_BIN" "$@"
