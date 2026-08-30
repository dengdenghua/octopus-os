#!/usr/bin/env bash
# Verify a released Echo OS evidence manifest with public material only.
set -euo pipefail

IMAGE_DIR="$(cd "$(dirname "$0")" && pwd)"
PACKAGED_VERIFY_KEYRING="$IMAGE_DIR/verify_public_keyring.py"
REPOSITORY_VERIFY_KEYRING="$IMAGE_DIR/../../deploy/installer/verify_public_keyring.py"
if [[ -f "$PACKAGED_VERIFY_KEYRING" && ! -L "$PACKAGED_VERIFY_KEYRING" ]]; then
  VERIFY_KEYRING="$PACKAGED_VERIFY_KEYRING"
else
  VERIFY_KEYRING="$REPOSITORY_VERIFY_KEYRING"
fi
MANIFEST_INPUT="${1:-}"
SIGNATURE_INPUT="${2:-}"
KEYRING_INPUT="${3:-}"

usage() {
  echo "usage: $0 EVIDENCE.json EVIDENCE.json.gpg PUBLIC_KEYRING" >&2
}

fail() {
  echo "Echo OS released evidence rejected: $*" >&2
  exit 1
}

[[ $# -eq 3 ]] || { usage; exit 2; }
for command_name in awk gpgv python3 realpath sha256sum stat; do
  command -v "$command_name" >/dev/null 2>&1 || \
    fail "required command is missing: $command_name"
done
[[ -x "$VERIFY_KEYRING" ]] || fail "public-keyring verifier is unavailable"
for input_path in "$MANIFEST_INPUT" "$SIGNATURE_INPUT" "$KEYRING_INPUT"; do
  [[ -f "$input_path" && ! -L "$input_path" ]] || \
    fail "every input must be a regular non-symlink file"
done

MANIFEST="$(realpath -- "$MANIFEST_INPUT")"
SIGNATURE="$(realpath -- "$SIGNATURE_INPUT")"
KEYRING="$(realpath -- "$KEYRING_INPUT")"
[[ "$MANIFEST" != "$SIGNATURE" && "$MANIFEST" != "$KEYRING" && \
   "$SIGNATURE" != "$KEYRING" ]] || \
  fail "manifest, signature and keyring paths must be distinct"

MANIFEST_SIZE="$(stat -c '%s' "$MANIFEST")"
SIGNATURE_SIZE="$(stat -c '%s' "$SIGNATURE")"
[[ "$MANIFEST_SIZE" =~ ^[1-9][0-9]*$ && "$MANIFEST_SIZE" -le 1048576 ]] || \
  fail "evidence manifest must be 1 byte to 1 MiB"
[[ "$SIGNATURE_SIZE" =~ ^[1-9][0-9]*$ && "$SIGNATURE_SIZE" -le 1048576 ]] || \
  fail "detached evidence signature must be 1 byte to 1 MiB"

python3 "$VERIFY_KEYRING" "$KEYRING"
gpgv --keyring "$KEYRING" "$SIGNATURE" "$MANIFEST"

MANIFEST_SHA256="$(sha256sum "$MANIFEST" | awk '{print $1}')"
SIGNATURE_SHA256="$(sha256sum "$SIGNATURE" | awk '{print $1}')"
KEYRING_SHA256="$(sha256sum "$KEYRING" | awk '{print $1}')"
[[ "$MANIFEST_SHA256" =~ ^[0-9a-f]{64}$ && \
   "$SIGNATURE_SHA256" =~ ^[0-9a-f]{64}$ && \
   "$KEYRING_SHA256" =~ ^[0-9a-f]{64}$ ]] || \
  fail "cannot hash the verified evidence inputs"
echo "ECHO_OS_IMAGE_EVIDENCE_SIGNATURE_OK manifest=$MANIFEST_SHA256 signature=$SIGNATURE_SHA256 keyring=$KEYRING_SHA256"
