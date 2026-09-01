#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 /absolute/path/to/Echo.app" >&2
  exit 2
fi

app_path=$1
if [[ "$app_path" != /* || ! -d "$app_path" ]]; then
  echo "packaged app must be an existing absolute path: $app_path" >&2
  exit 1
fi

app_contents="$app_path/Contents"
resources="$app_contents/Resources"
app_executable="$app_contents/MacOS/Echo"
backend="$resources/backend/echo-backend"
codex_root="$resources/codex"
codex="$codex_root/bin/codex"
codex_code_mode_host="$codex_root/bin/codex-code-mode-host"
codex_rg="$codex_root/codex-path/rg"
codex_zsh="$codex_root/codex-resources/zsh/bin/zsh"
manifest="$codex_root/echo-codex-bundle.json"
update_config="$resources/app-update.yml"

for required in \
  "$app_executable" \
  "$backend" \
  "$codex" \
  "$codex_code_mode_host" \
  "$codex_rg" \
  "$codex_zsh" \
  "$manifest" \
  "$update_config" \
  "$resources/config.desktop.yaml"; do
  if [[ ! -f "$required" || -L "$required" ]]; then
    echo "missing or linked packaged file: $required" >&2
    exit 1
  fi
done

python3 - "$update_config" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
entries = {}
for line in path.read_text(encoding="utf-8").splitlines():
    if not line.strip():
        continue
    key, separator, value = line.partition(":")
    if not separator or not key.strip() or key.strip() in entries:
        raise SystemExit("packaged updater config is malformed")
    entries[key.strip()] = value.strip()
expected = {
    "owner": "dengdenghua",
    "repo": "echo-os",
    "provider": "github",
    "updaterCacheDirName": "echo-frontend-updater",
}
if entries != expected:
    raise SystemExit(f"packaged updater config differs from the release channel: {entries!r}")
print("PACKAGED_UPDATE_CHANNEL_OK=github:dengdenghua/echo-os")
PY

ELECTRON_RUN_AS_NODE=1 "$app_executable" -e \
  'const fs=require("fs"),p=require("path"),r=process.argv[1],v=JSON.parse(fs.readFileSync(p.join(r,"package.json"),"utf8")).version;if(v!=="6.8.9"||!fs.statSync(p.join(r,"out/main.js")).isFile())process.exit(2);console.log("PACKAGED_ELECTRON_UPDATER_OK=6.8.9")' \
  "$app_contents/Resources/app.asar/node_modules/electron-updater"

expected_arch=${ECHO_MAC_ARCH:-arm64}
case "$expected_arch" in
  arm64) file_arch="arm64" ;;
  x64) file_arch="x86_64" ;;
  *) echo "unsupported ECHO_MAC_ARCH: $expected_arch" >&2; exit 1 ;;
esac
packaged_executables=(
  "$app_executable"
  "$backend"
  "$codex"
  "$codex_code_mode_host"
  "$codex_rg"
  "$codex_zsh"
)
for executable in "${packaged_executables[@]}"; do
  if ! file "$executable" | grep -Eq "Mach-O .* $file_arch( |$)"; then
    echo "packaged executable has the wrong architecture: $executable" >&2
    file "$executable" >&2
    exit 1
  fi
done

python3 - "$codex_root" "$expected_arch" <<'PY'
import hashlib
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
arch = sys.argv[2]
manifest = json.loads((root / "echo-codex-bundle.json").read_text(encoding="utf-8"))
expected_target = "aarch64-apple-darwin" if arch == "arm64" else "x86_64-apple-darwin"
if manifest.get("schema") != "echo.codex_bundle.v1":
    raise SystemExit("invalid Codex bundle schema")
if manifest.get("package") != "@openai/codex" or manifest.get("version") != "0.149.0":
    raise SystemExit("invalid Codex package identity")
if manifest.get("target") != expected_target:
    raise SystemExit(f"Codex target mismatch: {manifest.get('target')}")
files = manifest.get("files")
if not isinstance(files, dict) or not files:
    raise SystemExit("Codex bundle has no file manifest")
for relative, expected in files.items():
    candidate = root / relative
    if candidate.is_symlink() or not candidate.is_file():
        raise SystemExit(f"missing or linked Codex bundle file: {relative}")
    actual = hashlib.sha256(candidate.read_bytes()).hexdigest()
    if actual != expected:
        raise SystemExit(f"Codex bundle hash mismatch: {relative}")
print(f"PACKAGED_CODEX_HASHES_OK={len(files)}")
PY

"$codex" app-server --help >/dev/null

if [[ -e "$resources/extensions/workbuddy-connectors" ]]; then
  echo "desktop package must not embed the WorkBuddy marketplace snapshot" >&2
  exit 1
fi
echo "PACKAGED_CONNECTOR_MARKETPLACE_ON_DEMAND=1"

if [[ ${ECHO_REQUIRE_SIGNED_MACOS:-0} == 1 ]]; then
  codesign --verify --deep --strict --verbose=4 "$app_path"
  app_signature=$(codesign -dv --verbose=4 "$app_path" 2>&1)
  app_team=$(sed -n 's/^TeamIdentifier=//p' <<<"$app_signature" | head -1)
  app_authority=$(grep -m1 '^Authority=Developer ID Application:' <<<"$app_signature" || true)
  if [[ -z "$app_team" || -z "$app_authority" || "$app_team" == "not set" ]]; then
    echo "packaged application lacks a Developer ID signing identity" >&2
    exit 1
  fi
  for executable in "${packaged_executables[@]:1}"; do
    codesign --verify --strict --verbose=4 "$executable"
    signature=$(codesign -dv --verbose=4 "$executable" 2>&1)
    authority=$(grep -m1 '^Authority=Developer ID Application:' <<<"$signature" || true)
    team=$(sed -n 's/^TeamIdentifier=//p' <<<"$signature" | head -1)
    if [[ -z "$authority" ]]; then
      echo "packaged executable lacks a Developer ID signature: $executable" >&2
      exit 1
    fi
    if [[ "$team" != "$app_team" || "$authority" != "$app_authority" ]]; then
      echo "packaged executable signer differs from the application: $executable" >&2
      exit 1
    fi
  done
  spctl --assess --type execute --verbose=4 "$app_path"
  xcrun stapler validate "$app_path"
fi

script_dir=$(cd "$(dirname "$0")" && pwd -P)
repo_root=$(cd "$script_dir/../.." && pwd -P)
smoke_root=$(mktemp -d)
backend_port=${ECHO_MACOS_SMOKE_PORT:-18766}
config="$smoke_root/config.yaml"
backend_pid=""
app_pid=""

cleanup() {
  if [[ -n "$app_pid" ]]; then
    kill "$app_pid" 2>/dev/null || true
    wait "$app_pid" 2>/dev/null || true
  fi
  if [[ -n "$backend_pid" ]]; then
    kill "$backend_pid" 2>/dev/null || true
    wait "$backend_pid" 2>/dev/null || true
  fi
  # This directory is created immediately above with mktemp and contains only
  # disposable first-launch state. Do not leak tens of megabytes per verifier run.
  find "$smoke_root" -depth -delete 2>/dev/null || true
}
trap cleanup EXIT

node -e \
  'const m=require(process.argv[1]);m.ensureDesktopConfigFile({bundledPath:process.argv[2],targetPath:process.argv[3]});m.ensureDesktopResources({bundledRoot:process.argv[4],targetRoot:process.argv[5]});' \
  "$repo_root/frontend/electron/desktop-config.cjs" \
  "$resources/config.desktop.yaml" \
  "$config" \
  "$resources" \
  "$smoke_root/resources"
# Exercise the packaged bcrypt extension as part of the real login path. The
# hash is a low-cost release-smoke fixture for "release-smoke-password" only.
printf '%s\n' '  users:' '    release-smoke: "bcrypt:$2b$04$KWFHX0cmIsgqSTQ23AnuouwO21q.Yz8ZP017wkIhGLfDU6Yg4ruoW"' >>"$config"

ECHO_DESKTOP=1 \
ECHO_DATA_DIR="$smoke_root/data" \
ECHO_RESOURCES_DIR="$smoke_root/resources" \
ECHO_CODEX_EXECUTABLE="$codex" \
  "$backend" serve --config "$config" --host 127.0.0.1 --port "$backend_port" \
  >"$smoke_root/backend.stdout.log" 2>"$smoke_root/backend.stderr.log" &
backend_pid=$!

ready=0
for _ in {1..180}; do
  if ! kill -0 "$backend_pid" 2>/dev/null; then
    break
  fi
  if curl -fsS "http://127.0.0.1:$backend_port/readyz" \
    >"$smoke_root/ready.json" 2>/dev/null; then
    ready=1
    break
  fi
  sleep 0.5
done
if [[ $ready -ne 1 ]]; then
  sed -n '1,160p' "$smoke_root/backend.stdout.log" >&2
  sed -n '1,240p' "$smoke_root/backend.stderr.log" >&2
  echo "packaged backend did not reach /readyz" >&2
  exit 1
fi
if ! curl -fsS -H "Content-Type: application/json" \
  --data '{"username":"release-smoke","password":"release-smoke-password"}' \
  "http://127.0.0.1:$backend_port/api/auth/local/login" \
  >"$smoke_root/local-auth.json"; then
  echo "packaged local authentication is unavailable" >&2
  exit 1
fi
access_token=$(python3 - "$smoke_root/local-auth.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    token = json.load(handle).get("access_token")
if not isinstance(token, str) or not token:
    raise SystemExit("packaged local authentication did not return an access token")
print(token)
PY
)
if ! curl -fsS -H "Authorization: Bearer $access_token" \
  "http://127.0.0.1:$backend_port/api/plugins/clip-studio/health" \
  >"$smoke_root/clip-studio-health.json"; then
  echo "packaged Clip Studio plugin is unavailable" >&2
  exit 1
fi
python3 - "$smoke_root/clip-studio-health.json" <<'PY'
import json
import pathlib
import sys

payload = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
if payload.get("ok") is not True or payload.get("plugin") != "clip_studio":
    raise SystemExit("packaged Clip Studio health payload is invalid")
PY
kill "$backend_pid" 2>/dev/null || true
wait "$backend_pid" 2>/dev/null || true
backend_pid=""

if [[ ${ECHO_MACOS_APP_LAUNCH_SMOKE:-0} == 1 ]]; then
  app_port=$((backend_port + 1))
  ECHO_BACKEND_URL="http://127.0.0.1:$app_port" \
  ECHO_SMOKE=1 \
  ECHO_SMOKE_HOLD_MS=1000 \
    "$app_executable" --user-data-dir="$smoke_root/user-data" \
    >"$smoke_root/app.stdout.log" 2>"$smoke_root/app.stderr.log" &
  app_pid=$!
  app_ready=0
  app_renderer=0
  for _ in {1..180}; do
    if ! kill -0 "$app_pid" 2>/dev/null; then
      break
    fi
    if curl -fsS "http://127.0.0.1:$app_port/readyz" \
      >"$smoke_root/app-ready.json" 2>/dev/null; then
      app_ready=1
    fi
    if grep -q "SMOKE OK: echo-app://app/" "$smoke_root/app.stdout.log" 2>/dev/null; then
      app_renderer=1
    fi
    if [[ $app_ready -eq 1 && $app_renderer -eq 1 ]]; then
      break
    fi
    sleep 0.5
  done
  for _ in {1..180}; do
    if ! kill -0 "$app_pid" 2>/dev/null; then
      break
    fi
    sleep 0.5
  done
  if kill -0 "$app_pid" 2>/dev/null; then
    sed -n '1,180p' "$smoke_root/app.stdout.log" >&2
    sed -n '1,260p' "$smoke_root/app.stderr.log" >&2
    echo "packaged Electron app did not exit after its bounded smoke hold" >&2
    exit 1
  fi
  set +e
  wait "$app_pid"
  app_status=$?
  set -e
  app_pid=""
  if grep -q "SMOKE OK: echo-app://app/" "$smoke_root/app.stdout.log" 2>/dev/null; then
    app_renderer=1
  fi
  backend_stopped=0
  for _ in {1..40}; do
    if ! curl -fsS "http://127.0.0.1:$app_port/readyz" >/dev/null 2>&1; then
      backend_stopped=1
      break
    fi
    sleep 0.25
  done
  if [[ $app_status -ne 0 || $app_ready -ne 1 || $app_renderer -ne 1 || $backend_stopped -ne 1 ]]; then
    sed -n '1,180p' "$smoke_root/app.stdout.log" >&2
    sed -n '1,260p' "$smoke_root/app.stderr.log" >&2
    echo "packaged Electron first-launch smoke failed (status=$app_status backend=$app_ready renderer=$app_renderer stopped=$backend_stopped)" >&2
    exit 1
  fi
  if [[ ! -f "$smoke_root/user-data/config.yaml" || ! -d "$smoke_root/user-data/resources" ]]; then
    echo "packaged Electron app did not materialize first-launch state" >&2
    exit 1
  fi
fi

echo "ECHO_PACKAGED_MACOS_OK"
