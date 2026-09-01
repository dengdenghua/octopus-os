#!/usr/bin/env bash
# Build the reproducible Echo OS x86-64 UEFI VM image.
set -euo pipefail

IMAGE_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$IMAGE_DIR/../.." && pwd)"
FRONTEND_DIR="$REPO_ROOT/frontend"
RECOVERY_DIR="$REPO_ROOT/packaging/recovery"
NATIVE_AGENT_PREPARE="$IMAGE_DIR/prepare-native-agent-runtime.sh"
NATIVE_SHELL_VERIFY="$IMAGE_DIR/verify-native-shell-package.cjs"
# shellcheck source=packaging/image/secure-boot-options.sh
source "$IMAGE_DIR/secure-boot-options.sh"
MODE="${1:---build}"
IMAGE_VERSION="${ECHO_IMAGE_VERSION:-$(tr -d '[:space:]' <"$IMAGE_DIR/mkosi.version")}"
[[ "$IMAGE_VERSION" =~ ^[0-9A-Za-z][0-9A-Za-z.+:~_-]*$ ]] || {
  echo "invalid mkosi.version: $IMAGE_VERSION" >&2
  exit 2
}

case "$MODE" in
  --build | --prepare-only | --skip-frontend) ;;
  *)
    echo "usage: $0 [--build|--prepare-only|--skip-frontend]" >&2
    exit 2
    ;;
esac

if [[ "$MODE" != "--skip-frontend" ]]; then
  command -v pnpm >/dev/null 2>&1 || {
    echo "pnpm is required to build the locked Echo Desktop payload" >&2
    exit 1
  }
  echo "== Build locked Linux desktop payload =="
  (
    cd "$FRONTEND_DIR"
    pnpm install --frozen-lockfile
    pnpm build
    pnpm exec electron-builder --linux dir --x64
  )
fi

DESKTOP_BINARY="$FRONTEND_DIR/release/linux-unpacked/echo-os-desktop"
CHROME_SANDBOX="$FRONTEND_DIR/release/linux-unpacked/chrome-sandbox"
DESKTOP_RESOURCES="$FRONTEND_DIR/release/linux-unpacked/resources"
# electron-builder derives app-update.yml from package repository metadata
# even for this non-publishing directory target. Echo OS updates atomically at
# the image layer, so erase only that exact generated file and then reject any
# remaining standalone update/Agent resources below.
find "$DESKTOP_RESOURCES/app-update.yml" -type f -delete 2>/dev/null || true
[[ -x "$DESKTOP_BINARY" && -f "$CHROME_SANDBOX" ]] || {
  echo "Linux x64 Electron directory package is incomplete" >&2
  exit 1
}
[[ -f "$DESKTOP_RESOURCES/app.asar" ]] || {
  echo "Linux x64 Electron shell archive is missing" >&2
  exit 1
}
for forbidden_resource in \
  app-update.yml app.asar.unpacked/native config.desktop.yaml backend codex agents prompts protocols resources extensions skills.lock.json; do
  [[ ! -e "$DESKTOP_RESOURCES/$forbidden_resource" ]] || {
    echo "native OS shell contains standalone desktop resource: $forbidden_resource" >&2
    exit 1
  }
done
node "$NATIVE_SHELL_VERIFY" "$(cd "$FRONTEND_DIR/release/linux-unpacked" && pwd)"
if command -v file >/dev/null 2>&1 && \
   ! file "$DESKTOP_BINARY" | grep -q 'ELF 64-bit.*x86-64'; then
  echo "Echo Desktop payload is not a Linux x86-64 executable" >&2
  exit 1
fi

[[ -x "$NATIVE_AGENT_PREPARE" ]] || {
  echo "native Agent runtime preparation tool is missing" >&2
  exit 1
}
echo "== Materialize locked native Agent runtime =="
"$NATIVE_AGENT_PREPARE"

"$IMAGE_DIR/verify-image.sh" --static
if [[ "$MODE" == "--prepare-only" ]]; then
  echo "Image payload and static contract OK"
  exit 0
fi

[[ "$(uname -s)" == "Linux" ]] || {
  echo "mkosi disk images require a Linux host; payload preparation is complete" >&2
  exit 1
}
command -v mkosi >/dev/null 2>&1 || {
  echo "mkosi 25.3 or newer is required" >&2
  exit 1
}
[[ -n "${ECHO_INSTALL_KEYRING:-}" ]] || {
  echo "ECHO_INSTALL_KEYRING is required for an install-capable production/release image" >&2
  exit 1
}
[[ -n "${ECHO_UPDATE_KEYRING:-}" ]] || {
  echo "ECHO_UPDATE_KEYRING is required for an update-capable production/release image" >&2
  exit 1
}
UPDATE_TRUST_GENERATION="${ECHO_UPDATE_TRUST_GENERATION:-}"
[[ "$UPDATE_TRUST_GENERATION" =~ ^[1-9][0-9]*$ ]] || {
  echo "ECHO_UPDATE_TRUST_GENERATION must be a positive monotonic integer" >&2
  exit 1
}
[[ -n "${ECHO_FACTORY_DATA_KEY:-}" ]] || {
  echo "ECHO_FACTORY_DATA_KEY is required for encrypted mutable partitions" >&2
  exit 1
}
FACTORY_DATA_KEY_INPUT="$ECHO_FACTORY_DATA_KEY"
DATA_PROTECTION_TOOL="$REPO_ROOT/deploy/data-protection/echo_data_protection.py"
MKOSI_SUMMARY_VERIFIER="$IMAGE_DIR/verify-mkosi-summary.py"
OS_SOURCE_VERIFIER="$IMAGE_DIR/os_source_identity.py"
[[ -x "$DATA_PROTECTION_TOOL" ]] || {
  echo "data-protection key verifier is missing" >&2
  exit 1
}
[[ -x "$MKOSI_SUMMARY_VERIFIER" ]] || {
  echo "resolved mkosi release-policy verifier is missing" >&2
  exit 1
}
[[ -x "$OS_SOURCE_VERIFIER" && -n "${ECHO_OS_SOURCE_MANIFEST:-}" ]] || {
  echo "ECHO_OS_SOURCE_MANIFEST is required for a source-bound release image" >&2
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
  --repo "$REPO_ROOT" --manifest "$OS_SOURCE_MANIFEST"
python3 "$DATA_PROTECTION_TOOL" check-factory-key "$FACTORY_DATA_KEY_INPUT"
FACTORY_DATA_KEY="$(realpath -- "$FACTORY_DATA_KEY_INPUT")"
UPDATE_KEYRING_INPUT="$ECHO_UPDATE_KEYRING"
KEYRING_VERIFIER="$REPO_ROOT/deploy/installer/verify_public_keyring.py"
UPDATE_TRUST_TOOL="$REPO_ROOT/deploy/update/echo_update_trust.py"
[[ -f "$UPDATE_KEYRING_INPUT" && ! -L "$UPDATE_KEYRING_INPUT" && \
   -s "$UPDATE_KEYRING_INPUT" && -x "$KEYRING_VERIFIER" && \
   -x "$UPDATE_TRUST_TOOL" ]] || {
  echo "ECHO_UPDATE_KEYRING must be a regular public OpenPGP keyring" >&2
  exit 1
}
python3 "$KEYRING_VERIFIER" "$UPDATE_KEYRING_INPUT"
for command_name in cmp gpg install mktemp python3 realpath rm systemd-dissect; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "update trust-root build dependency missing: $command_name" >&2
    exit 1
  }
done
UPDATE_KEYRING_TREE="$(mktemp -d)"
MKOSI_SUMMARY_JSON="$(mktemp)"
cleanup_build_inputs() {
  rm -f -- "$MKOSI_SUMMARY_JSON"
  rm -rf -- "$UPDATE_KEYRING_TREE"
}
trap cleanup_build_inputs EXIT INT TERM
install -d -m 0755 "$UPDATE_KEYRING_TREE/usr/lib/echo-os"
install -m 0444 "$OS_SOURCE_MANIFEST" \
  "$UPDATE_KEYRING_TREE/usr/lib/echo-os/os-source-identity.json"
install -m 0644 "$UPDATE_KEYRING_INPUT" \
  "$UPDATE_KEYRING_TREE/usr/lib/echo-os/update-keyring.gpg"
UPDATE_RETIRED_ARGS=()
UPDATE_RETIRED_RAW="${ECHO_UPDATE_RETIRED_FINGERPRINTS:-}"
UPDATE_RETIRED_RAW="${UPDATE_RETIRED_RAW//,/ }"
read -r -a UPDATE_RETIRED_FINGERPRINTS <<<"$UPDATE_RETIRED_RAW"
for retired_fingerprint in "${UPDATE_RETIRED_FINGERPRINTS[@]}"; do
  [[ -n "$retired_fingerprint" ]] && \
    UPDATE_RETIRED_ARGS+=(--retired-fingerprint "$retired_fingerprint")
done
python3 "$UPDATE_TRUST_TOOL" create-policy \
  --keyring "$UPDATE_KEYRING_INPUT" \
  --generation "$UPDATE_TRUST_GENERATION" \
  --gpg "$(command -v gpg)" \
  --verifier "$KEYRING_VERIFIER" \
  --output "$UPDATE_KEYRING_TREE/usr/lib/echo-os/update-trust-policy.json" \
  "${UPDATE_RETIRED_ARGS[@]}"
configure_echo_secure_boot
[[ "$ECHO_SECURE_BOOT_CONFIGURED" == yes ]] || {
  echo "signed Secure Boot and PCR policy identities are required for release images" >&2
  exit 1
}
install -d -m 0755 "$UPDATE_KEYRING_TREE/usr/lib/systemd"
install -m 0644 "$ECHO_TPM2_PCR_PUBLIC_KEY" \
  "$UPDATE_KEYRING_TREE/usr/lib/systemd/tpm2-pcr-public-key.pem"
install -m 0644 "$ECHO_SECURE_BOOT_CERTIFICATE" \
  "$UPDATE_KEYRING_TREE/usr/lib/echo-os/verity-certificate.pem"

MKOSI_VERSION="$(mkosi --version | awk 'NR == 1 { print $2 }')"
python3 - "$MKOSI_VERSION" <<'PY'
import re
import sys

match = re.match(r"^(\d+)\.(\d+)", sys.argv[1])
if not match:
    raise SystemExit(f"unable to parse mkosi version: {sys.argv[1]}")
version = tuple(int(part) for part in match.groups())
if version < (25, 3):
    raise SystemExit(f"mkosi 25.3 or newer is required, found {sys.argv[1]}")
PY

echo "== Build Echo OS GPT/UEFI image with mkosi $MKOSI_VERSION =="
ECHO_IMAGE_VERSION="$IMAGE_VERSION" "$RECOVERY_DIR/build-recovery.sh"
MKOSI_VERSION_ARGS=()
if [[ -n "${ECHO_IMAGE_VERSION:-}" ]]; then
  MKOSI_VERSION_ARGS=(--image-version "$IMAGE_VERSION")
fi
(
  cd "$IMAGE_DIR"
  mkosi --json "${MKOSI_VERSION_ARGS[@]}" "${ECHO_MKOSI_SECURE_BOOT_ARGS[@]}" \
    --passphrase="$FACTORY_DATA_KEY" \
    --extra-tree="$UPDATE_KEYRING_TREE" summary >"$MKOSI_SUMMARY_JSON"
  python3 "$MKOSI_SUMMARY_VERIFIER" \
    "$MKOSI_SUMMARY_JSON" "$IMAGE_VERSION" \
    "$ECHO_SECURE_BOOT_KEY" "$ECHO_SECURE_BOOT_CERTIFICATE" \
    "$ECHO_PCR_POLICY_KEY" "$ECHO_PCR_POLICY_CERTIFICATE" \
    "$FACTORY_DATA_KEY" "$UPDATE_KEYRING_TREE"
  mkosi "${MKOSI_VERSION_ARGS[@]}" "${ECHO_MKOSI_SECURE_BOOT_ARGS[@]}" \
    --passphrase="$FACTORY_DATA_KEY" \
    --extra-tree="$UPDATE_KEYRING_TREE" --force build
)

IMAGE_PATH="$IMAGE_DIR/mkosi.output/echo-os_${IMAGE_VERSION}.raw"
[[ -f "$IMAGE_PATH" ]] || {
  echo "mkosi did not produce an Echo OS raw disk image" >&2
  exit 1
}
resolve_partition_artifact() {
  local kind="$1"
  local -a matches=()
  while IFS= read -r -d '' artifact; do
    matches+=("$artifact")
  done < <(find "$IMAGE_DIR/mkosi.output" -maxdepth 1 -type f \
    -name "echo-os_${IMAGE_VERSION}.${kind}.*.raw" -print0)
  [[ "${#matches[@]}" -eq 1 ]] || {
    echo "mkosi must produce exactly one ${kind} UUID-bearing split artifact" >&2
    return 1
  }
  printf '%s\n' "${matches[0]}"
}
ROOT_PAYLOAD="$(resolve_partition_artifact root)"
VERITY_PAYLOAD="$(resolve_partition_artifact root-verity)"
VERITY_SIG_PAYLOAD="$(resolve_partition_artifact root-verity-sig)"
UKI_PAYLOAD="$IMAGE_DIR/mkosi.output/echo-os_${IMAGE_VERSION}.efi"
[[ -s "$ROOT_PAYLOAD" && -s "$VERITY_PAYLOAD" && \
   -s "$VERITY_SIG_PAYLOAD" && -s "$UKI_PAYLOAD" ]] || {
  echo "mkosi did not produce the complete root verity artifact triplet" >&2
  exit 1
}
python3 "$REPO_ROOT/deploy/update/verify-verity-set.py" \
  "$ROOT_PAYLOAD" "$VERITY_PAYLOAD" "$VERITY_SIG_PAYLOAD" \
  "$ECHO_SECURE_BOOT_CERTIFICATE" --uki "$UKI_PAYLOAD"
RECOVERY_UKI="$RECOVERY_DIR/mkosi.output/echo-recovery_${IMAGE_VERSION}.efi"
CHECKSUM_FILE="$IMAGE_DIR/mkosi.output/echo-os_${IMAGE_VERSION}.SHA256SUMS"
"$RECOVERY_DIR/install-recovery-uki.sh" \
  "$IMAGE_PATH" "$RECOVERY_UKI" "$IMAGE_VERSION" "$CHECKSUM_FILE"
EMBEDDED_UPDATE_KEYRING="$UPDATE_KEYRING_TREE/embedded-update-keyring.gpg"
systemd-dissect --read-only --fsck=no --copy-from "$ROOT_PAYLOAD" \
  /usr/lib/echo-os/update-keyring.gpg "$EMBEDDED_UPDATE_KEYRING"
cmp "$UPDATE_KEYRING_INPUT" "$EMBEDDED_UPDATE_KEYRING"
EMBEDDED_UPDATE_TRUST_POLICY="$UPDATE_KEYRING_TREE/embedded-update-trust-policy.json"
systemd-dissect --read-only --fsck=no --copy-from "$ROOT_PAYLOAD" \
  /usr/lib/echo-os/update-trust-policy.json "$EMBEDDED_UPDATE_TRUST_POLICY"
cmp "$UPDATE_KEYRING_TREE/usr/lib/echo-os/update-trust-policy.json" \
  "$EMBEDDED_UPDATE_TRUST_POLICY"
EMBEDDED_PCR_PUBLIC_KEY="$UPDATE_KEYRING_TREE/embedded-tpm2-pcr-public-key.pem"
systemd-dissect --read-only --fsck=no --copy-from "$ROOT_PAYLOAD" \
  /usr/lib/systemd/tpm2-pcr-public-key.pem "$EMBEDDED_PCR_PUBLIC_KEY"
cmp "$ECHO_TPM2_PCR_PUBLIC_KEY" "$EMBEDDED_PCR_PUBLIC_KEY"
EMBEDDED_VERITY_CERTIFICATE="$UPDATE_KEYRING_TREE/embedded-verity-certificate.pem"
systemd-dissect --read-only --fsck=no --copy-from "$ROOT_PAYLOAD" \
  /usr/lib/echo-os/verity-certificate.pem "$EMBEDDED_VERITY_CERTIFICATE"
cmp "$ECHO_SECURE_BOOT_CERTIFICATE" "$EMBEDDED_VERITY_CERTIFICATE"
EMBEDDED_OS_SOURCE_MANIFEST="$UPDATE_KEYRING_TREE/embedded-os-source-identity.json"
systemd-dissect --read-only --fsck=no --copy-from "$ROOT_PAYLOAD" \
  /usr/lib/echo-os/os-source-identity.json "$EMBEDDED_OS_SOURCE_MANIFEST"
cmp "$OS_SOURCE_MANIFEST" "$EMBEDDED_OS_SOURCE_MANIFEST"
python3 "$OS_SOURCE_VERIFIER" verify --manifest "$EMBEDDED_OS_SOURCE_MANIFEST"
python3 "$OS_SOURCE_VERIFIER" verify-repo \
  --repo "$REPO_ROOT" --manifest "$OS_SOURCE_MANIFEST"
"$IMAGE_DIR/verify-image.sh" --artifact "$IMAGE_PATH"
python3 "$OS_SOURCE_VERIFIER" verify-repo \
  --repo "$REPO_ROOT" --manifest "$OS_SOURCE_MANIFEST"
echo "Echo OS image ready: $IMAGE_PATH"
