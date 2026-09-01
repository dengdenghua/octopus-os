#!/usr/bin/env bash
# Stop at the production SDDM greeter and exercise its fixed screen-reader key.
set -euo pipefail

IMAGE_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE_IMAGE="${1:-}"

[[ $# -eq 1 && -f "$SOURCE_IMAGE" && ! -L "$SOURCE_IMAGE" ]] || {
  echo "usage: $0 PROVISIONED.raw" >&2
  exit 2
}

ECHO_BOOT_TARGET=greeter \
ECHO_BOOT_CI_SESSION=no \
ECHO_BOOT_EPHEMERAL=yes \
ECHO_BOOT_TIMEOUT_SECONDS="${ECHO_SDDM_ACCESSIBILITY_TIMEOUT_SECONDS:-180}" \
ECHO_BOOT_LOG_DIR="${ECHO_SDDM_ACCESSIBILITY_LOG_DIR:-}" \
  exec "$IMAGE_DIR/smoke-boot-image.sh" "$SOURCE_IMAGE"
