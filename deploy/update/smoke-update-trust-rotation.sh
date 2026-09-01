#!/usr/bin/env bash
# Exercise a real old+new -> new-only update-signing trust transition.
set -euo pipefail
umask 077

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TRUST_TOOL="$REPO_ROOT/deploy/update/echo_update_trust.py"
VERIFY_KEYRING="$REPO_ROOT/deploy/installer/verify_public_keyring.py"
TEST_ROOT="$(mktemp -d)"
GNUPGHOME="$TEST_ROOT/gnupg"
STATE_ROOT="$TEST_ROOT/state"
MANIFEST="$TEST_ROOT/SHA256SUMS"
OLD_SIGNATURE="$TEST_ROOT/SHA256SUMS.old.gpg"
NEW_SIGNATURE="$TEST_ROOT/SHA256SUMS.new.gpg"
OLD_KEYRING="$TEST_ROOT/update-old.gpg"
BRIDGE_KEYRING="$TEST_ROOT/update-old-new.gpg"
NEW_KEYRING="$TEST_ROOT/update-new.gpg"
POLICY_1="$TEST_ROOT/policy-1.json"
POLICY_2="$TEST_ROOT/policy-2.json"
POLICY_3="$TEST_ROOT/policy-3.json"
OLD_UID='Echo OS rotation old <old-update@example.invalid>'
NEW_UID='Echo OS rotation new <new-update@example.invalid>'

cleanup() {
  rm -rf -- "$TEST_ROOT"
}
trap cleanup EXIT INT TERM

for command_name in awk chmod dirname gpg gpgv mkdir mktemp python3 rm tr; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "update trust rotation dependency missing: $command_name" >&2
    exit 1
  }
done
[[ -x "$TRUST_TOOL" && -x "$VERIFY_KEYRING" ]] || {
  echo "update trust rotation source runtime is incomplete" >&2
  exit 1
}

mkdir -m 0700 "$GNUPGHOME"
export GNUPGHOME
gpg --batch --passphrase '' --quick-generate-key "$OLD_UID" ed25519 sign 1d
gpg --batch --passphrase '' --quick-generate-key "$NEW_UID" ed25519 sign 1d
OLD_FINGERPRINT="$(
  gpg --batch --with-colons --list-secret-keys "$OLD_UID" |
    awk -F: '$1 == "fpr" { print $10; exit }'
)"
NEW_FINGERPRINT="$(
  gpg --batch --with-colons --list-secret-keys "$NEW_UID" |
    awk -F: '$1 == "fpr" { print $10; exit }'
)"
[[ "$OLD_FINGERPRINT" =~ ^[0-9A-Fa-f]{40,64}$ && \
   "$NEW_FINGERPRINT" =~ ^[0-9A-Fa-f]{40,64}$ && \
   "$OLD_FINGERPRINT" != "$NEW_FINGERPRINT" ]] || {
  echo "isolated rotation identities have invalid fingerprints" >&2
  exit 1
}
OLD_FINGERPRINT="$(printf '%s' "$OLD_FINGERPRINT" | tr '[:lower:]' '[:upper:]')"
NEW_FINGERPRINT="$(printf '%s' "$NEW_FINGERPRINT" | tr '[:lower:]' '[:upper:]')"

gpg --batch --export "$OLD_FINGERPRINT" >"$OLD_KEYRING"
gpg --batch --export "$OLD_FINGERPRINT" "$NEW_FINGERPRINT" >"$BRIDGE_KEYRING"
gpg --batch --export "$NEW_FINGERPRINT" >"$NEW_KEYRING"
chmod 0444 "$OLD_KEYRING" "$BRIDGE_KEYRING" "$NEW_KEYRING"

python3 "$TRUST_TOOL" create-policy \
  --keyring "$OLD_KEYRING" --generation 1 \
  --gpg "$(command -v gpg)" --verifier "$VERIFY_KEYRING" --output "$POLICY_1"
python3 "$TRUST_TOOL" create-policy \
  --keyring "$BRIDGE_KEYRING" --generation 2 \
  --gpg "$(command -v gpg)" --verifier "$VERIFY_KEYRING" --output "$POLICY_2"
python3 "$TRUST_TOOL" create-policy \
  --keyring "$NEW_KEYRING" --generation 3 \
  --retired-fingerprint "$OLD_FINGERPRINT" \
  --gpg "$(command -v gpg)" --verifier "$VERIFY_KEYRING" --output "$POLICY_3"

printf '%s\n' 'rotation-manifest' >"$MANIFEST"
gpg --batch --yes --local-user "$OLD_FINGERPRINT" --detach-sign \
  --output "$OLD_SIGNATURE" "$MANIFEST"
gpg --batch --yes --local-user "$NEW_FINGERPRINT" --detach-sign \
  --output "$NEW_SIGNATURE" "$MANIFEST"

trust_runtime=(
  ECHO_UPDATE_TRUST_SOURCE_TEST=USE-SOURCE-RUNTIME
)
env "${trust_runtime[@]}" python3 "$TRUST_TOOL" promote \
  --system-policy "$POLICY_1" --system-keyring "$OLD_KEYRING" \
  --state-root "$STATE_ROOT" --verifier "$VERIFY_KEYRING"
gpgv --keyring "$STATE_ROOT/update-keyring.gpg" "$OLD_SIGNATURE" "$MANIFEST" \
  >/dev/null 2>&1

env "${trust_runtime[@]}" python3 "$TRUST_TOOL" promote \
  --system-policy "$POLICY_2" --system-keyring "$BRIDGE_KEYRING" \
  --state-root "$STATE_ROOT" --verifier "$VERIFY_KEYRING"
gpgv --keyring "$STATE_ROOT/update-keyring.gpg" "$OLD_SIGNATURE" "$MANIFEST" \
  >/dev/null 2>&1
gpgv --keyring "$STATE_ROOT/update-keyring.gpg" "$NEW_SIGNATURE" "$MANIFEST" \
  >/dev/null 2>&1

env "${trust_runtime[@]}" python3 "$TRUST_TOOL" promote \
  --system-policy "$POLICY_3" --system-keyring "$NEW_KEYRING" \
  --state-root "$STATE_ROOT" --verifier "$VERIFY_KEYRING"
if gpgv --keyring "$STATE_ROOT/update-keyring.gpg" "$OLD_SIGNATURE" "$MANIFEST" \
     >/dev/null 2>&1; then
  echo "retired update signing key still authenticated a manifest" >&2
  exit 1
fi
gpgv --keyring "$STATE_ROOT/update-keyring.gpg" "$NEW_SIGNATURE" "$MANIFEST" \
  >/dev/null 2>&1

# Re-select while presenting the generation-1 immutable root. Persistent trust
# must stay at generation 3 and must still reject the old signature.
SELECTION="$(
  env "${trust_runtime[@]}" python3 "$TRUST_TOOL" select \
    --system-policy "$POLICY_1" --system-keyring "$OLD_KEYRING" \
    --state-root "$STATE_ROOT" --verifier "$VERIFY_KEYRING" --machine
)"
IFS=$'\t' read -r SELECTED_GENERATION SELECTED_KEYRING SELECTED_SHA SELECTED_SOURCE \
  <<<"$SELECTION"
[[ "$SELECTED_GENERATION" == 3 && "$SELECTED_KEYRING" == "$STATE_ROOT/update-keyring.gpg" && \
   "$SELECTED_SHA" =~ ^[0-9a-f]{64}$ && "$SELECTED_SOURCE" == managed ]] || {
  echo "old-root rollback did not retain generation-3 managed trust" >&2
  exit 1
}
if gpgv --keyring "$SELECTED_KEYRING" "$OLD_SIGNATURE" "$MANIFEST" >/dev/null 2>&1; then
  echo "old-root rollback resurrected the retired signing key" >&2
  exit 1
fi
gpgv --keyring "$SELECTED_KEYRING" "$NEW_SIGNATURE" "$MANIFEST" >/dev/null 2>&1

echo "ECHO_UPDATE_TRUST_ROTATION_OK bridge=old+new final=new-only old=retired rollback=retained generation=3"
