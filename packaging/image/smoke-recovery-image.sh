#!/usr/bin/env bash
# Boot the Recovery UKI through systemd-boot from a temporary copy of the finished disk.
set -euo pipefail

IMAGE_DIR="$(cd "$(dirname "$0")" && pwd)"
IMAGE_VERSION="${ECHO_IMAGE_VERSION:-$(tr -d '[:space:]' <"$IMAGE_DIR/mkosi.version")}"
SOURCE_IMAGE="${1:-$IMAGE_DIR/mkosi.output/echo-os_${IMAGE_VERSION}.raw}"

[[ "$(uname -s)" == "Linux" ]] || {
  echo "installed-Recovery smoke requires Linux" >&2
  exit 1
}
[[ "$IMAGE_VERSION" =~ ^[0-9A-Za-z][0-9A-Za-z.+:~_-]*$ ]] || {
  echo "invalid image version: $IMAGE_VERSION" >&2
  exit 2
}
for command_name in cp grep mcopy mktemp python3 realpath sfdisk; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "installed-Recovery smoke dependency missing: $command_name" >&2
    exit 1
  }
done
SOURCE_IMAGE="$(realpath "$SOURCE_IMAGE")"
[[ -f "$SOURCE_IMAGE" ]] || {
  echo "finished Echo OS image not found: $SOURCE_IMAGE" >&2
  exit 1
}

TEMP_DIR="$(mktemp -d)"
RECOVERY_IMAGE="$TEMP_DIR/echo-os-recovery-test.raw"
PARTITIONS_JSON="$TEMP_DIR/partitions.json"
RECOVERY_LOADER_CONF="$TEMP_DIR/loader.conf"
LOADER_CONF_CHECK="$TEMP_DIR/loader-check.conf"
cleanup() { rm -rf -- "$TEMP_DIR"; }
trap cleanup EXIT INT TERM

# Keep the delivered image and its checksum immutable; only this sparse/reflink
# test copy receives a temporary boot-menu default.
cp --reflink=auto --sparse=always "$SOURCE_IMAGE" "$RECOVERY_IMAGE"
sfdisk --json "$RECOVERY_IMAGE" >"$PARTITIONS_JSON"
read -r SECTOR_SIZE ESP_START < <(
  python3 - "$PARTITIONS_JSON" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    table = json.load(stream)["partitiontable"]
esp = next((p for p in table["partitions"] if p.get("name") == "echo-esp"), None)
if not esp:
    raise SystemExit("echo-esp partition not found")
print(table["sectorsize"], esp["start"])
PY
)
ESP_IMAGE="${RECOVERY_IMAGE}@@$((SECTOR_SIZE * ESP_START))"
printf '%s\n' \
  'default echo-recovery_*' \
  'timeout 0' \
  'console-mode keep' \
  'editor no' \
  'auto-entries yes' \
  'auto-firmware yes' >"$RECOVERY_LOADER_CONF"
mcopy -o -i "$ESP_IMAGE" "$RECOVERY_LOADER_CONF" "::/loader/loader.conf"
mcopy -i "$ESP_IMAGE" "::/loader/loader.conf" "$LOADER_CONF_CHECK"
grep -q '^default echo-recovery_\*$' "$LOADER_CONF_CHECK" || {
  echo "temporary Recovery boot selection did not persist in the ESP" >&2
  exit 1
}

ECHO_BOOT_TARGET=recovery \
ECHO_BOOT_EPHEMERAL=yes \
ECHO_BOOT_TIMEOUT_SECONDS="${ECHO_RECOVERY_TIMEOUT_SECONDS:-120}" \
ECHO_BOOT_LOG_DIR="${ECHO_RECOVERY_LOG_DIR:-$TEMP_DIR/logs}" \
  "$IMAGE_DIR/smoke-boot-image.sh" "$RECOVERY_IMAGE"
