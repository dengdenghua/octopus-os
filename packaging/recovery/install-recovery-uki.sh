#!/usr/bin/env bash
# Install the independent Recovery UKI into a built disk ESP and refresh checksums.
set -euo pipefail

DISK_IMAGE="${1:-}"
RECOVERY_UKI="${2:-}"
IMAGE_VERSION="${3:-}"
CHECKSUM_FILE="${4:-}"
[[ $# -eq 4 ]] || {
  echo "usage: $0 DISK.raw RECOVERY.efi VERSION SHA256SUMS" >&2
  exit 2
}
[[ "$IMAGE_VERSION" =~ ^[0-9A-Za-z][0-9A-Za-z.+:~_-]*$ ]] || {
  echo "invalid recovery version: $IMAGE_VERSION" >&2
  exit 2
}
for command_name in awk mcopy mdir python3 sfdisk sha256sum stat; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "recovery installer dependency missing: $command_name" >&2
    exit 1
  }
done
[[ -f "$DISK_IMAGE" && -f "$RECOVERY_UKI" && -f "$CHECKSUM_FILE" ]] || {
  echo "disk image, recovery UKI or checksum file is missing" >&2
  exit 1
}

TEMP_DIR="$(mktemp -d)"
PARTITIONS_JSON="$TEMP_DIR/partitions.json"
NEW_CHECKSUMS="$TEMP_DIR/SHA256SUMS"
cleanup() { rm -rf -- "$TEMP_DIR"; }
trap cleanup EXIT INT TERM

sfdisk --json "$DISK_IMAGE" >"$PARTITIONS_JSON"
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
ESP_IMAGE="${DISK_IMAGE}@@$((SECTOR_SIZE * ESP_START))"
RECOVERY_NAME="echo-recovery_${IMAGE_VERSION}.efi"
RECOVERY_SIZE="$(stat -c '%s' "$RECOVERY_UKI")"
ESP_FREE_BYTES="$(
  LC_ALL=C mdir -i "$ESP_IMAGE" :: |
    awk '/bytes free/ { gsub(/[^0-9]/, "", $0); value=$0 } END { print value }'
)"
[[ "$ESP_FREE_BYTES" =~ ^[0-9]+$ ]] || {
  echo "unable to determine free space in the Echo OS ESP" >&2
  exit 1
}
# Leave one MiB for FAT directory/allocation overhead and future metadata writes.
if (( RECOVERY_SIZE + 1024 * 1024 > ESP_FREE_BYTES )); then
  echo "recovery UKI does not fit in the ESP: need $((RECOVERY_SIZE + 1024 * 1024)) bytes, have $ESP_FREE_BYTES" >&2
  exit 1
fi
mcopy -o -i "$ESP_IMAGE" "$RECOVERY_UKI" "::/EFI/Linux/$RECOVERY_NAME"
mdir -i "$ESP_IMAGE" "::/EFI/Linux/$RECOVERY_NAME" >/dev/null
echo "Recovery ESP capacity OK: $RECOVERY_SIZE-byte UKI, $ESP_FREE_BYTES bytes available before install"

CHECKSUM_DIRECTORY="$(cd "$(dirname "$CHECKSUM_FILE")" && pwd)"
while read -r _digest artifact; do
  artifact="${artifact#\*}"
  [[ -n "$artifact" && "$artifact" == "$(basename "$artifact")" ]] || {
    echo "unsafe checksum artifact name: $artifact" >&2
    exit 1
  }
  [[ -f "$CHECKSUM_DIRECTORY/$artifact" ]] || {
    echo "checksummed artifact is missing: $artifact" >&2
    exit 1
  }
  (
    cd "$CHECKSUM_DIRECTORY"
    sha256sum "$artifact"
  ) >>"$NEW_CHECKSUMS"
done <"$CHECKSUM_FILE"
mv -f -- "$NEW_CHECKSUMS" "$CHECKSUM_FILE"
echo "Installed $RECOVERY_NAME into the Echo OS ESP"
