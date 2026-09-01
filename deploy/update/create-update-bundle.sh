#!/usr/bin/env bash
# Build a signed, compressed Echo OS root + UKI release bundle.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
IMAGE_DIR="$REPO_ROOT/packaging/image"
VERIFY_BUNDLE="$REPO_ROOT/deploy/update/verify-update-bundle.py"
VERIFY_VERITY="$REPO_ROOT/deploy/update/verify-verity-set.py"
VERIFY_KEYRING="$REPO_ROOT/deploy/installer/verify_public_keyring.py"
VERIFY_PCR_POLICY="$REPO_ROOT/deploy/data-protection/verify_uki_pcr_policy.py"
VERIFY_OS_SOURCE="$IMAGE_DIR/os_source_identity.py"
DATA_PROTECTION_TOOL="$REPO_ROOT/deploy/data-protection/echo_data_protection.py"
IMAGE_VERSION="${ECHO_IMAGE_VERSION:-$(tr -d '[:space:]' <"$IMAGE_DIR/mkosi.version")}"
[[ "$IMAGE_VERSION" =~ ^[0-9A-Za-z][0-9A-Za-z.+:~_-]*$ ]] || {
  echo "invalid mkosi.version: $IMAGE_VERSION" >&2
  exit 2
}
OUTPUT_DIR="${1:-}"
SIGNING_KEY="${ECHO_UPDATE_SIGNING_KEY:-}"
TRUST_KEYRING_INPUT="${ECHO_UPDATE_KEYRING:-}"
VERITY_CERTIFICATE_INPUT="${ECHO_SECURE_BOOT_CERTIFICATE:-}"
PCR_POLICY_PUBLIC_KEY_INPUT="${ECHO_TPM2_PCR_PUBLIC_KEY:-}"
OS_SOURCE_MANIFEST_INPUT="${ECHO_OS_SOURCE_MANIFEST:-}"

[[ -n "$OUTPUT_DIR" && $# -eq 1 ]] || {
  echo "usage: ECHO_OS_SOURCE_MANIFEST=SOURCE_IDENTITY ECHO_UPDATE_SIGNING_KEY=FINGERPRINT ECHO_UPDATE_KEYRING=PUBLIC_KEYRING ECHO_SECURE_BOOT_CERTIFICATE=RELEASE_CERT ECHO_TPM2_PCR_PUBLIC_KEY=PCR_PUBLIC_KEY $0 OUTPUT_DIRECTORY" >&2
  exit 2
}
[[ "$(uname -s)" == "Linux" ]] || {
  echo "update bundles require the Linux split-partition artifacts" >&2
  exit 1
}
[[ "$SIGNING_KEY" =~ ^[0-9A-Fa-f]{40,64}$ ]] || {
  echo "ECHO_UPDATE_SIGNING_KEY must be the full production GPG fingerprint" >&2
  exit 1
}
for command_name in gpg openssl sbverify sha256sum veritysetup zstd install realpath; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "bundle dependency missing: $command_name" >&2
    exit 1
  }
done
if command -v ukify >/dev/null 2>&1; then
  UKIFY_BIN="$(command -v ukify)"
elif [[ -x /usr/lib/systemd/ukify ]]; then
  UKIFY_BIN=/usr/lib/systemd/ukify
else
  echo "bundle dependency missing: ukify" >&2
  exit 1
fi
for command_name in \
  basename chmod dirname find gpgv mkdir mktemp mv python3 rm rmdir stat; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "bundle dependency missing: $command_name" >&2
    exit 1
  }
done

[[ -n "$TRUST_KEYRING_INPUT" ]] || {
  echo "ECHO_UPDATE_KEYRING must select the production update trust root" >&2
  exit 1
}
[[ -f "$TRUST_KEYRING_INPUT" && ! -L "$TRUST_KEYRING_INPUT" && \
   -s "$TRUST_KEYRING_INPUT" ]] || {
  echo "ECHO_UPDATE_KEYRING must name a regular non-empty public keyring" >&2
  exit 1
}
TRUST_KEYRING="$(realpath -- "$TRUST_KEYRING_INPUT")"
python3 "$VERIFY_KEYRING" "$TRUST_KEYRING"
[[ -n "$VERITY_CERTIFICATE_INPUT" && -f "$VERITY_CERTIFICATE_INPUT" && \
   ! -L "$VERITY_CERTIFICATE_INPUT" ]] || {
  echo "ECHO_SECURE_BOOT_CERTIFICATE must select the dm-verity release certificate" >&2
  exit 1
}
VERITY_CERTIFICATE="$(realpath -- "$VERITY_CERTIFICATE_INPUT")"
[[ -n "$PCR_POLICY_PUBLIC_KEY_INPUT" && -f "$PCR_POLICY_PUBLIC_KEY_INPUT" && \
   ! -L "$PCR_POLICY_PUBLIC_KEY_INPUT" && -x "$VERIFY_PCR_POLICY" && \
   -x "$DATA_PROTECTION_TOOL" ]] || {
  echo "ECHO_TPM2_PCR_PUBLIC_KEY and the signed-PCR verifiers are required" >&2
  exit 1
}
PCR_POLICY_PUBLIC_KEY="$(realpath -- "$PCR_POLICY_PUBLIC_KEY_INPUT")"
python3 "$DATA_PROTECTION_TOOL" check-tpm2-public-key \
  "$PCR_POLICY_PUBLIC_KEY"
[[ -x "$VERIFY_OS_SOURCE" && -n "$OS_SOURCE_MANIFEST_INPUT" && \
   -f "$OS_SOURCE_MANIFEST_INPUT" && ! -L "$OS_SOURCE_MANIFEST_INPUT" ]] || {
  echo "ECHO_OS_SOURCE_MANIFEST must name the verified clean OS source identity" >&2
  exit 1
}
OS_SOURCE_MANIFEST="$(realpath -- "$OS_SOURCE_MANIFEST_INPUT")"
OS_SOURCE_RECORD="$(
  python3 "$VERIFY_OS_SOURCE" verify --manifest "$OS_SOURCE_MANIFEST" --machine
)"
IFS=$'\t' read -r OS_SOURCE_REPOSITORY OS_SOURCE_COMMIT OS_SOURCE_TREE \
  OS_SOURCE_MANIFEST_SHA256 <<<"$OS_SOURCE_RECORD"
[[ -n "$OS_SOURCE_REPOSITORY" && "$OS_SOURCE_COMMIT" =~ ^[0-9a-f]{40}$ && \
   "$OS_SOURCE_TREE" =~ ^[0-9a-f]{40}$ && \
   "$OS_SOURCE_MANIFEST_SHA256" =~ ^[0-9a-f]{64}$ ]] || {
  echo "OS source identity verifier returned an invalid machine record" >&2
  exit 1
}

UKI_SOURCE="$IMAGE_DIR/mkosi.output/echo-os_${IMAGE_VERSION}.efi"

resolve_partition_artifact() {
  local kind="$1"
  local -a matches=()
  while IFS= read -r -d '' artifact; do
    matches+=("$artifact")
  done < <(find "$IMAGE_DIR/mkosi.output" -maxdepth 1 -type f \
    -name "echo-os_${IMAGE_VERSION}.${kind}.*.raw" -print0)
  [[ "${#matches[@]}" -eq 1 ]] || {
    echo "expected exactly one ${kind} UUID-bearing split artifact, found ${#matches[@]}" >&2
    return 1
  }
  printf '%s\n' "${matches[0]}"
}

ROOT_SOURCE="$(resolve_partition_artifact root)"
VERITY_SOURCE="$(resolve_partition_artifact root-verity)"
VERITY_SIG_SOURCE="$(resolve_partition_artifact root-verity-sig)"
[[ -f "$ROOT_SOURCE" && ! -L "$ROOT_SOURCE" && -s "$ROOT_SOURCE" && \
   -f "$VERITY_SOURCE" && ! -L "$VERITY_SOURCE" && -s "$VERITY_SOURCE" && \
   -f "$VERITY_SIG_SOURCE" && ! -L "$VERITY_SIG_SOURCE" && \
   -s "$VERITY_SIG_SOURCE" && -f "$UKI_SOURCE" && ! -L "$UKI_SOURCE" && \
   -s "$UKI_SOURCE" ]] || {
  echo "version-matched root, verity, verity signature and UKI artifacts are required" >&2
  exit 1
}
python3 "$VERIFY_VERITY" \
  "$ROOT_SOURCE" "$VERITY_SOURCE" "$VERITY_SIG_SOURCE" \
  "$VERITY_CERTIFICATE" --uki "$UKI_SOURCE"
sbverify --cert "$VERITY_CERTIFICATE" "$UKI_SOURCE" >/dev/null

[[ ! -L "$OUTPUT_DIR" ]] || {
  echo "output directory must not be a symlink: $OUTPUT_DIR" >&2
  exit 1
}
OUTPUT_PARENT_INPUT="$(dirname -- "$OUTPUT_DIR")"
OUTPUT_BASENAME="$(basename -- "$OUTPUT_DIR")"
mkdir -p -- "$OUTPUT_PARENT_INPUT"
OUTPUT_PARENT="$(realpath -- "$OUTPUT_PARENT_INPUT")"
OUTPUT_DIR="$OUTPUT_PARENT/$OUTPUT_BASENAME"
if [[ -e "$OUTPUT_DIR" ]]; then
  [[ -d "$OUTPUT_DIR" && -z "$(find "$OUTPUT_DIR" -mindepth 1 -maxdepth 1 -print -quit)" ]] || {
    echo "output directory must not exist or must be empty: $OUTPUT_DIR" >&2
    exit 1
  }
fi

TEMP_DIR="$(mktemp -d "$OUTPUT_PARENT/.${OUTPUT_BASENAME}.tmp.XXXXXX")"
cleanup() { rm -rf -- "$TEMP_DIR"; }
trap cleanup EXIT INT TERM
BUNDLE_BUILD_DIR="$TEMP_DIR/bundle"
mkdir -m 0755 -- "$BUNDLE_BUILD_DIR"
EMBEDDED_PCR_PUBLIC_KEY="$TEMP_DIR/uki-pcr-public-key.pem"
EMBEDDED_PCR_SIGNATURE="$TEMP_DIR/uki-pcr-signature.json"
"$UKIFY_BIN" inspect "$UKI_SOURCE" \
  --section ".pcrpkey:text@$EMBEDDED_PCR_PUBLIC_KEY" \
  --section ".pcrsig:text@$EMBEDDED_PCR_SIGNATURE" >/dev/null
python3 "$VERIFY_PCR_POLICY" \
  "$PCR_POLICY_PUBLIC_KEY" "$EMBEDDED_PCR_PUBLIC_KEY" \
  "$EMBEDDED_PCR_SIGNATURE"
ROOT_TARGET="$BUNDLE_BUILD_DIR/$(basename "$ROOT_SOURCE").zst"
VERITY_TARGET="$BUNDLE_BUILD_DIR/$(basename "$VERITY_SOURCE").zst"
VERITY_SIG_TARGET="$BUNDLE_BUILD_DIR/$(basename "$VERITY_SIG_SOURCE").zst"
UKI_TARGET="$BUNDLE_BUILD_DIR/echo-os_${IMAGE_VERSION}.efi"
OS_SOURCE_TARGET="$BUNDLE_BUILD_DIR/OS-SOURCE-IDENTITY.json"

zstd -T0 -10 --sparse "$ROOT_SOURCE" -o "$ROOT_TARGET"
zstd -T0 -10 --sparse "$VERITY_SOURCE" -o "$VERITY_TARGET"
zstd -T0 -10 --sparse "$VERITY_SIG_SOURCE" -o "$VERITY_SIG_TARGET"
install -m 0444 "$UKI_SOURCE" "$UKI_TARGET"
install -m 0444 "$OS_SOURCE_MANIFEST" "$OS_SOURCE_TARGET"
(
  cd "$BUNDLE_BUILD_DIR"
  sha256sum \
    "$(basename "$ROOT_TARGET")" \
    "$(basename "$VERITY_TARGET")" \
    "$(basename "$VERITY_SIG_TARGET")" \
    "$(basename "$UKI_TARGET")" \
    "$(basename "$OS_SOURCE_TARGET")" >SHA256SUMS
)
gpg --batch --yes --local-user "$SIGNING_KEY" --detach-sign \
  --output "$BUNDLE_BUILD_DIR/SHA256SUMS.gpg" "$BUNDLE_BUILD_DIR/SHA256SUMS"
chmod 0444 \
  "$BUNDLE_BUILD_DIR/SHA256SUMS" \
  "$BUNDLE_BUILD_DIR/SHA256SUMS.gpg" \
  "$ROOT_TARGET" "$VERITY_TARGET" "$VERITY_SIG_TARGET" "$UKI_TARGET" \
  "$OS_SOURCE_TARGET"

# A release is publishable only when the exact public keyring selected for the
# image accepts it and the strict runtime verifier accepts its complete layout.
gpgv --keyring "$TRUST_KEYRING" \
  "$BUNDLE_BUILD_DIR/SHA256SUMS.gpg" "$BUNDLE_BUILD_DIR/SHA256SUMS"
python3 "$VERIFY_BUNDLE" "$BUNDLE_BUILD_DIR"
zstd --test -- "$ROOT_TARGET" "$VERITY_TARGET" "$VERITY_SIG_TARGET"

if [[ -d "$OUTPUT_DIR" ]]; then
  rmdir -- "$OUTPUT_DIR"
fi
mv -- "$BUNDLE_BUILD_DIR" "$OUTPUT_DIR"

echo "Signed Echo OS $IMAGE_VERSION update bundle: $OUTPUT_DIR"
