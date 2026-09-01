#!/usr/bin/env bash
# Assemble the verified unified Echo runtime, resources and Codex for Debian 13.
set -euo pipefail

IMAGE_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$IMAGE_DIR/../.." && pwd)"
BUNDLE_ROOT="$REPO_ROOT/deploy/appliance"
OUTPUT_ROOT="$IMAGE_DIR/mkosi.agent-runtime"
VERIFY="$IMAGE_DIR/verify-native-agent-runtime.py"
BUNDLE_PYTHON="$BUNDLE_ROOT/bundle-python.sh"
AGENT_BUNDLE_TOOL="$BUNDLE_ROOT/agent_bundle.py"
STAGE=""

cleanup() {
  cleanup_exit=$?
  if [[ -n "$STAGE" && -d "$STAGE" ]]; then
    find "$STAGE" -depth -delete
  fi
  return "$cleanup_exit"
}
trap cleanup EXIT INT TERM

for command_name in cp find file mv uv; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "native Agent build dependency missing: $command_name" >&2
    exit 1
  }
done
[[ -x "$VERIFY" && -x "$BUNDLE_PYTHON" && -f "$AGENT_BUNDLE_TOOL" ]] || {
  echo "native Agent verification tools are incomplete" >&2
  exit 1
}
PYTHON="$($BUNDLE_PYTHON --print-interpreter)"
"$PYTHON" "$AGENT_BUNDLE_TOOL" verify \
  --bundle-root "$BUNDLE_ROOT" \
  --manifest "$BUNDLE_ROOT/agent-bundle.json"

"$PYTHON" - "$BUNDLE_ROOT/agent-bundle.json" <<'PY'
import json, os, sys
bundle = json.load(open(sys.argv[1], encoding="utf-8"))
source = bundle.get("source") or {}
if source.get("dirty") and os.environ.get("ECHO_AGENT_ALLOW_DIRTY") != "1":
    raise SystemExit("dirty Agent bundle requires explicit ECHO_AGENT_ALLOW_DIRTY=1")
if not source.get("packaged_codex_version") or not isinstance(bundle.get("codex"), dict):
    raise SystemExit("selected Agent bundle has no source-bound Codex engine")
PY

STAGE="$(mktemp -d "$IMAGE_DIR/.mkosi.agent-runtime.XXXXXX")"
RUNTIME_ROOT="$STAGE/opt/echo-agent"
SITE_PACKAGES="$RUNTIME_ROOT/site-packages"
install -d -m 0755 "$SITE_PACKAGES"

echo "== Materialize the hash-locked CPython 3.13 / Linux x86-64 closure =="
UV_NO_CONFIG=1 uv pip sync "$BUNDLE_ROOT/agent-dist/runtime-requirements.lock" \
  --target "$SITE_PACKAGES" \
  --python-version 3.13 \
  --python-platform x86_64-unknown-linux-gnu \
  --require-hashes \
  --only-binary :all: \
  --no-config

WHEEL="$($PYTHON - "$BUNDLE_ROOT/agent-bundle.json" "$BUNDLE_ROOT/agent-dist" <<'PY'
import json, pathlib, sys
manifest = json.load(open(sys.argv[1], encoding="utf-8"))
name = manifest.get("wheel", {}).get("filename")
path = pathlib.Path(sys.argv[2]) / str(name or "")
if not path.is_file():
    raise SystemExit("verified Agent wheel is missing")
print(path.resolve())
PY
)"
UV_NO_CONFIG=1 uv pip install "$WHEEL" \
  --target "$SITE_PACKAGES" \
  --python-version 3.13 \
  --python-platform x86_64-unknown-linux-gnu \
  --no-deps \
  --no-config

# The unified wheel already contains appliance, runtime, tools and echo_runtime.
find "$SITE_PACKAGES" -type d -name __pycache__ -depth -exec find {} -depth -delete \;
find "$SITE_PACKAGES" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete

cp -a "$BUNDLE_ROOT/agent-resources" "$RUNTIME_ROOT/resources"
cp -a "$BUNDLE_ROOT/agent-codex" "$RUNTIME_ROOT/codex"
cp "$REPO_ROOT/deploy/agent/echo-agent-native.yaml" "$RUNTIME_ROOT/native-config.yaml"
cp "$BUNDLE_ROOT/agent-bundle.json" "$RUNTIME_ROOT/agent-bundle.json"
cp "$BUNDLE_ROOT/agent-dist/runtime-requirements.lock" \
  "$RUNTIME_ROOT/runtime-requirements.lock"
cp "$BUNDLE_ROOT/agent-dist/python-dependency-lock.json" \
  "$RUNTIME_ROOT/python-dependency-lock.json"

find "$RUNTIME_ROOT" -type d -exec chmod go-w {} +
find "$RUNTIME_ROOT" -type f -exec chmod go-w {} +
"$PYTHON" "$VERIFY" --write-manifest "$RUNTIME_ROOT"
"$PYTHON" "$VERIFY" "$RUNTIME_ROOT"

EXPECTED_PARENT="$(cd "$IMAGE_DIR" && pwd)"
[[ "$(dirname "$OUTPUT_ROOT")" == "$EXPECTED_PARENT" && \
   "$(basename "$OUTPUT_ROOT")" == "mkosi.agent-runtime" ]] || {
  echo "refusing to promote a native Agent tree outside packaging/image" >&2
  exit 1
}
if [[ -e "$OUTPUT_ROOT" || -L "$OUTPUT_ROOT" ]]; then
  find "$OUTPUT_ROOT" -depth -delete
fi
mv "$STAGE" "$OUTPUT_ROOT"
STAGE=""
echo "Echo OS native Agent runtime ready: $OUTPUT_ROOT/opt/echo-agent"
