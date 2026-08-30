#!/usr/bin/env bash
# Detach-sign one completed Echo OS evidence manifest with the release identity.
set -euo pipefail
umask 077

IMAGE_DIR="$(cd "$(dirname "$0")" && pwd)"
VERIFY_KEYRING="$IMAGE_DIR/../../deploy/installer/verify_public_keyring.py"
VERIFY_RELEASE="$IMAGE_DIR/verify-os-image-evidence-release.sh"
MANIFEST_INPUT="${1:-}"
SIGNATURE_INPUT="${2:-}"
KEYRING_INPUT="${3:-}"
SIGNING_FINGERPRINT="${4:-}"

usage() {
  echo "usage: $0 EVIDENCE.json EVIDENCE.json.gpg PUBLIC_KEYRING FULL_SIGNING_FINGERPRINT" >&2
}

fail() {
  echo "Echo OS evidence signing failed: $*" >&2
  exit 1
}

[[ $# -eq 4 ]] || { usage; exit 2; }
for command_name in awk chmod dirname gpg gpgv id mktemp mv python3 realpath \
  rm rmdir sha256sum stat sync; do
  command -v "$command_name" >/dev/null 2>&1 || \
    fail "required command is missing: $command_name"
done
[[ -x "$VERIFY_KEYRING" ]] || fail "public-keyring verifier is unavailable"
[[ -x "$VERIFY_RELEASE" ]] || fail "released-evidence verifier is unavailable"
[[ "$SIGNING_FINGERPRINT" =~ ^[0-9A-Fa-f]{40,64}$ ]] || \
  fail "signing identity must be one full OpenPGP fingerprint"
[[ -f "$MANIFEST_INPUT" && ! -L "$MANIFEST_INPUT" ]] || \
  fail "evidence manifest must be a regular non-symlink file"
[[ -f "$KEYRING_INPUT" && ! -L "$KEYRING_INPUT" ]] || \
  fail "installer public keyring must be a regular non-symlink file"
[[ ! -e "$SIGNATURE_INPUT" && ! -L "$SIGNATURE_INPUT" ]] || \
  fail "evidence signature output must be a new non-symlink path"

MANIFEST="$(realpath -- "$MANIFEST_INPUT")"
KEYRING="$(realpath -- "$KEYRING_INPUT")"
SIGNATURE_PARENT_INPUT="$(dirname -- "$SIGNATURE_INPUT")"
[[ -d "$SIGNATURE_PARENT_INPUT" && ! -L "$SIGNATURE_PARENT_INPUT" ]] || \
  fail "evidence signature parent must be a real directory"
SIGNATURE_PARENT="$(realpath -- "$SIGNATURE_PARENT_INPUT")"
SIGNATURE_BASENAME="${SIGNATURE_INPUT##*/}"
[[ -n "$SIGNATURE_BASENAME" && "$SIGNATURE_BASENAME" != . && \
   "$SIGNATURE_BASENAME" != .. ]] || \
  fail "evidence signature output must have one ordinary filename"
SIGNATURE="$SIGNATURE_PARENT/$SIGNATURE_BASENAME"
[[ "$(realpath -m -- "$SIGNATURE_INPUT")" == "$SIGNATURE" ]] || \
  fail "evidence signature output must be a normalized path"
[[ ! -e "$SIGNATURE" && ! -L "$SIGNATURE" ]] || \
  fail "resolved evidence signature output must be new"
[[ "$MANIFEST" != "$KEYRING" && "$MANIFEST" != "$SIGNATURE" && \
   "$KEYRING" != "$SIGNATURE" ]] || \
  fail "manifest, keyring and signature paths must be distinct"

MANIFEST_SIZE="$(stat -c '%s' "$MANIFEST")"
[[ "$MANIFEST_SIZE" =~ ^[1-9][0-9]*$ && "$MANIFEST_SIZE" -le 1048576 ]] || \
  fail "evidence manifest must be 1 byte to 1 MiB"
python3 "$VERIFY_KEYRING" "$KEYRING"

TEMP_DIR="$(mktemp -d "$SIGNATURE_PARENT/.echo-evidence-sign.XXXXXX")"
TEMP_SIGNATURE="$TEMP_DIR/${SIGNATURE##*/}"
cleanup() {
  local exit_code="$?"
  trap - EXIT INT TERM
  rm -f -- "$TEMP_SIGNATURE"
  rmdir -- "$TEMP_DIR" >/dev/null 2>&1 || true
  exit "$exit_code"
}
trap cleanup EXIT INT TERM

gpg --batch --yes --local-user "$SIGNING_FINGERPRINT" --detach-sign \
  --output "$TEMP_SIGNATURE" "$MANIFEST"
[[ -f "$TEMP_SIGNATURE" && ! -L "$TEMP_SIGNATURE" ]] || \
  fail "GPG did not create a regular detached signature"
SIGNATURE_SIZE="$(stat -c '%s' "$TEMP_SIGNATURE")"
[[ "$SIGNATURE_SIZE" =~ ^[1-9][0-9]*$ && "$SIGNATURE_SIZE" -le 1048576 ]] || \
  fail "detached evidence signature must be 1 byte to 1 MiB"
gpgv --keyring "$KEYRING" "$TEMP_SIGNATURE" "$MANIFEST"
chmod 0444 "$TEMP_SIGNATURE"
sync -f "$TEMP_SIGNATURE"
mv -- "$TEMP_SIGNATURE" "$SIGNATURE"
sync -f "$SIGNATURE_PARENT"
"$VERIFY_RELEASE" "$MANIFEST" "$SIGNATURE" "$KEYRING"

MANIFEST_SHA256="$(sha256sum "$MANIFEST" | awk '{print $1}')"
SIGNATURE_SHA256="$(sha256sum "$SIGNATURE" | awk '{print $1}')"
[[ "$MANIFEST_SHA256" =~ ^[0-9a-f]{64}$ && \
   "$SIGNATURE_SHA256" =~ ^[0-9a-f]{64}$ ]] || \
  fail "cannot hash the signed evidence outputs"
echo "ECHO_OS_IMAGE_EVIDENCE_SIGNED manifest=$MANIFEST_SHA256 signature=$SIGNATURE_SHA256 signer=${SIGNING_FINGERPRINT^^}"
