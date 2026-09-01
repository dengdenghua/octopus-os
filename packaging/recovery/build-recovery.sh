#!/usr/bin/env bash
# Build the self-contained Echo Recovery x86-64 UKI.
set -euo pipefail

RECOVERY_DIR="$(cd "$(dirname "$0")" && pwd)"
IMAGE_DIR="$RECOVERY_DIR/../image"
VERIFY_KEYRING="$RECOVERY_DIR/../../deploy/installer/verify_public_keyring.py"
OS_SOURCE_VERIFIER="$IMAGE_DIR/os_source_identity.py"
# shellcheck source=packaging/image/secure-boot-options.sh
source "$IMAGE_DIR/secure-boot-options.sh"
IMAGE_VERSION="${ECHO_IMAGE_VERSION:-$(tr -d '[:space:]' <"$RECOVERY_DIR/mkosi.version")}"
[[ "$IMAGE_VERSION" =~ ^[0-9A-Za-z][0-9A-Za-z.+:~_-]*$ ]] || {
  echo "invalid recovery image version: $IMAGE_VERSION" >&2
  exit 2
}
[[ "$(uname -s)" == "Linux" ]] || {
  echo "Echo Recovery UKI requires a Linux build host" >&2
  exit 1
}
for command_name in \
  awk basename dirname grep install mkosi mktemp python3 realpath rm sha256sum \
  tr uname; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "recovery build dependency missing: $command_name" >&2
    exit 1
  }
done
configure_echo_secure_boot
[[ "$ECHO_SECURE_BOOT_CONFIGURED" == yes ]] || {
  echo "signed Secure Boot and PCR policy identities are required for Recovery" >&2
  exit 1
}
[[ -x "$OS_SOURCE_VERIFIER" && -n "${ECHO_OS_SOURCE_MANIFEST:-}" ]] || {
  echo "ECHO_OS_SOURCE_MANIFEST is required for a source-bound Recovery UKI" >&2
  exit 1
}
OS_SOURCE_MANIFEST_INPUT="$ECHO_OS_SOURCE_MANIFEST"
[[ -f "$OS_SOURCE_MANIFEST_INPUT" && ! -L "$OS_SOURCE_MANIFEST_INPUT" ]] || {
  echo "ECHO_OS_SOURCE_MANIFEST must name one regular source identity" >&2
  exit 1
}
python3 "$OS_SOURCE_VERIFIER" verify --manifest "$OS_SOURCE_MANIFEST_INPUT"
OS_SOURCE_MANIFEST="$(realpath -- "$OS_SOURCE_MANIFEST_INPUT")"
python3 "$OS_SOURCE_VERIFIER" verify-repo \
  --repo "$RECOVERY_DIR/../.." --manifest "$OS_SOURCE_MANIFEST"

RECOVERY_KEYRING_TREE="$(mktemp -d)"
RECOVERY_EXTRA_ARGS=(--extra-tree="$RECOVERY_KEYRING_TREE")
cleanup() {
  if [[ -n "$RECOVERY_KEYRING_TREE" ]]; then
    rm -rf -- "$RECOVERY_KEYRING_TREE"
  fi
}
trap cleanup EXIT INT TERM
install -D -m 0644 "$ECHO_TPM2_PCR_PUBLIC_KEY" \
  "$RECOVERY_KEYRING_TREE/usr/lib/systemd/tpm2-pcr-public-key.pem"
install -D -m 0644 "$ECHO_SECURE_BOOT_CERTIFICATE" \
  "$RECOVERY_KEYRING_TREE/usr/lib/echo-os/verity-certificate.pem"
install -D -m 0444 "$OS_SOURCE_MANIFEST" \
  "$RECOVERY_KEYRING_TREE/usr/lib/echo-os/os-source-identity.json"
if [[ -n "${ECHO_INSTALL_KEYRING:-}" ]]; then
  INSTALL_KEYRING="$(realpath -- "$ECHO_INSTALL_KEYRING")"
  [[ -f "$INSTALL_KEYRING" && ! -L "$INSTALL_KEYRING" && -s "$INSTALL_KEYRING" ]] || {
    echo "ECHO_INSTALL_KEYRING must name a regular non-empty public keyring" >&2
    exit 1
  }
  python3 "$VERIFY_KEYRING" "$INSTALL_KEYRING"
  install -D -m 0644 "$INSTALL_KEYRING" \
    "$RECOVERY_KEYRING_TREE/usr/lib/echo-os/install-keyring.gpg"
fi

MKOSI_VERSION="$(mkosi --version | awk 'NR == 1 { print $2 }')"
python3 - "$MKOSI_VERSION" <<'PY'
import re
import sys

match = re.match(r"^(\d+)\.(\d+)", sys.argv[1])
if not match or tuple(map(int, match.groups())) < (25, 3):
    raise SystemExit(f"mkosi 25.3 or newer is required, found {sys.argv[1]}")
PY

(
  cd "$RECOVERY_DIR"
  mkosi --image-version "$IMAGE_VERSION" \
    "${RECOVERY_EXTRA_ARGS[@]}" \
    "${ECHO_MKOSI_SECURE_BOOT_ARGS[@]}" summary >/dev/null
  mkosi --image-version "$IMAGE_VERSION" \
    "${RECOVERY_EXTRA_ARGS[@]}" \
    "${ECHO_MKOSI_SECURE_BOOT_ARGS[@]}" --force build
)

RECOVERY_UKI="$RECOVERY_DIR/mkosi.output/echo-recovery_${IMAGE_VERSION}.efi"
CHECKSUM_FILE="$RECOVERY_DIR/mkosi.output/echo-recovery_${IMAGE_VERSION}.SHA256SUMS"
[[ -f "$RECOVERY_UKI" && -f "$CHECKSUM_FILE" ]] || {
  echo "mkosi did not produce the recovery UKI and checksum" >&2
  exit 1
}
(
  cd "$(dirname "$CHECKSUM_FILE")"
  sha256sum --check "$(basename "$CHECKSUM_FILE")"
)

if command -v ukify >/dev/null 2>&1; then
  UKIFY_BIN="$(command -v ukify)"
elif [[ -x /usr/lib/systemd/ukify ]]; then
  UKIFY_BIN=/usr/lib/systemd/ukify
else
  echo "recovery verifier dependency missing: ukify" >&2
  exit 1
fi
"$UKIFY_BIN" inspect "$RECOVERY_UKI" | grep -q 'ID=echo-recovery'
python3 "$OS_SOURCE_VERIFIER" verify-repo \
  --repo "$RECOVERY_DIR/../.." --manifest "$OS_SOURCE_MANIFEST"
echo "Echo Recovery UKI ready: $RECOVERY_UKI"
