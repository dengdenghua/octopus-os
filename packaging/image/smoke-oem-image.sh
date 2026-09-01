#!/usr/bin/env bash
# Exercise first-boot OEM provisioning before the production SDDM session.
set -euo pipefail

IMAGE_DIR="$(cd "$(dirname "$0")" && pwd)"
[[ $# -eq 1 || $# -eq 2 ]] || {
  echo "usage: $0 SOURCE.raw [PROVISIONED.raw]" >&2
  exit 2
}
if [[ $# -eq 2 ]]; then
  ECHO_LOGIN_OUTPUT_IMAGE="$2"
  export ECHO_LOGIN_OUTPUT_IMAGE
fi
ECHO_LOGIN_PROVISION_MODE=oem-credential \
  exec "$IMAGE_DIR/smoke-login-image.sh" "$1"
