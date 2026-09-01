#!/usr/bin/env bash
# Create a signed, compressed whole-disk Echo OS installer bundle.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
IMAGE_DIR="$REPO_ROOT/packaging/image"
VERIFY_BUNDLE="$REPO_ROOT/deploy/installer/verify_install_bundle.py"
VERIFY_KEYRING="$REPO_ROOT/deploy/installer/verify_public_keyring.py"
VERIFY_VERITY="$REPO_ROOT/deploy/update/verify-verity-set.py"
VERIFY_PCR_POLICY="$REPO_ROOT/deploy/data-protection/verify_uki_pcr_policy.py"
VERIFY_OS_SOURCE="$IMAGE_DIR/os_source_identity.py"
IMAGE_VERSION="${ECHO_IMAGE_VERSION:-$(tr -d '[:space:]' <"$IMAGE_DIR/mkosi.version")}"
SOURCE_IMAGE="${ECHO_INSTALL_SOURCE_IMAGE:-$IMAGE_DIR/mkosi.output/echo-os_${IMAGE_VERSION}.raw}"
SIGNING_KEY="${ECHO_INSTALL_SIGNING_KEY:-${ECHO_UPDATE_SIGNING_KEY:-}}"
TRUST_KEYRING_INPUT="${ECHO_INSTALL_KEYRING:-}"
FACTORY_KEY_INPUT="${ECHO_FACTORY_DATA_KEY:-}"
PCR_POLICY_PUBLIC_KEY_INPUT="${ECHO_TPM2_PCR_PUBLIC_KEY:-}"
VERITY_CERTIFICATE_INPUT="${ECHO_SECURE_BOOT_CERTIFICATE:-}"
OS_SOURCE_MANIFEST_INPUT="${ECHO_OS_SOURCE_MANIFEST:-}"
DATA_PROTECTION_TOOL="$REPO_ROOT/deploy/data-protection/echo_data_protection.py"
OUTPUT_DIR="${1:-}"

[[ $# -eq 1 && -n "$OUTPUT_DIR" ]] || {
  echo "usage: ECHO_OS_SOURCE_MANIFEST=SOURCE_IDENTITY ECHO_INSTALL_SIGNING_KEY=FULL_FINGERPRINT ECHO_INSTALL_KEYRING=PUBLIC_KEYRING ECHO_FACTORY_DATA_KEY=KEY ECHO_TPM2_PCR_PUBLIC_KEY=RSA_PUBLIC_KEY ECHO_SECURE_BOOT_CERTIFICATE=RELEASE_CERT $0 OUTPUT_DIRECTORY" >&2
  exit 2
}
[[ "$IMAGE_VERSION" =~ ^[0-9A-Za-z][0-9A-Za-z.+:~_-]*$ ]] || {
  echo "invalid Echo OS image version: $IMAGE_VERSION" >&2
  exit 2
}
[[ "$SIGNING_KEY" =~ ^[0-9A-Fa-f]{40,64}$ ]] || {
  echo "ECHO_INSTALL_SIGNING_KEY must be the full release GPG fingerprint" >&2
  exit 1
}
[[ "$(uname -s)" == Linux ]] || {
  echo "whole-disk installer bundles require a Linux release host" >&2
  exit 1
}
[[ "$(id -u)" -eq 0 ]] || {
  echo "whole-disk release validation requires root for read-only loop inspection" >&2
  exit 1
}
for command_name in \
  awk basename blkid chmod cryptsetup dirname find gpg gpgv id install losetup lsblk \
  mcopy mkdir mktemp modprobe mv openssl python3 realpath rm rmdir sbverify \
  sfdisk sha256sum stat tr udevadm uname veritysetup zstd; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "install-bundle dependency missing: $command_name" >&2
    exit 1
  }
done
if command -v ukify >/dev/null 2>&1; then
  UKIFY_BIN="$(command -v ukify)"
elif [[ -x /usr/lib/systemd/ukify ]]; then
  UKIFY_BIN=/usr/lib/systemd/ukify
else
  echo "install-bundle dependency missing: ukify" >&2
  exit 1
fi
[[ -n "$TRUST_KEYRING_INPUT" ]] || {
  echo "ECHO_INSTALL_KEYRING must select the Recovery installer trust root" >&2
  exit 1
}
[[ -f "$TRUST_KEYRING_INPUT" && ! -L "$TRUST_KEYRING_INPUT" && \
   -s "$TRUST_KEYRING_INPUT" ]] || {
  echo "ECHO_INSTALL_KEYRING must name a regular non-empty public keyring" >&2
  exit 1
}
TRUST_KEYRING="$(realpath -- "$TRUST_KEYRING_INPUT")"
python3 "$VERIFY_KEYRING" "$TRUST_KEYRING"
[[ -n "$FACTORY_KEY_INPUT" && -x "$DATA_PROTECTION_TOOL" ]] || {
  echo "ECHO_FACTORY_DATA_KEY and its strict verifier are required" >&2
  exit 1
}
python3 "$DATA_PROTECTION_TOOL" check-factory-key "$FACTORY_KEY_INPUT"
FACTORY_KEY="$(realpath -- "$FACTORY_KEY_INPUT")"
[[ -n "$PCR_POLICY_PUBLIC_KEY_INPUT" ]] || {
  echo "ECHO_TPM2_PCR_PUBLIC_KEY is required for the signed PCR 11 policy" >&2
  exit 1
}
python3 "$DATA_PROTECTION_TOOL" check-tpm2-public-key \
  "$PCR_POLICY_PUBLIC_KEY_INPUT"
PCR_POLICY_PUBLIC_KEY="$(realpath -- "$PCR_POLICY_PUBLIC_KEY_INPUT")"
[[ -x "$VERIFY_PCR_POLICY" ]] || {
  echo "signed-PCR UKI verifier is unavailable" >&2
  exit 1
}
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
[[ -n "$VERITY_CERTIFICATE_INPUT" && -f "$VERITY_CERTIFICATE_INPUT" && \
   ! -L "$VERITY_CERTIFICATE_INPUT" && -x "$VERIFY_VERITY" ]] || {
  echo "ECHO_SECURE_BOOT_CERTIFICATE and the dm-verity verifier are required" >&2
  exit 1
}
VERITY_CERTIFICATE="$(realpath -- "$VERITY_CERTIFICATE_INPUT")"
[[ -f "$SOURCE_IMAGE" && ! -L "$SOURCE_IMAGE" ]] || {
  echo "finished regular Echo OS raw image is required: $SOURCE_IMAGE" >&2
  exit 1
}
SOURCE_IMAGE="$(realpath -- "$SOURCE_IMAGE")"
SOURCE_SIZE="$(stat -c '%s' "$SOURCE_IMAGE")"
[[ "$SOURCE_SIZE" =~ ^[1-9][0-9]*$ ]] && (( SOURCE_SIZE % 512 == 0 )) || {
  echo "raw image size must be a positive 512-byte multiple" >&2
  exit 1
}

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
LOOP_DEVICE=""
cleanup() {
  if [[ -n "$LOOP_DEVICE" ]]; then
    losetup --detach "$LOOP_DEVICE" >/dev/null 2>&1 || true
  fi
  rm -rf -- "$TEMP_DIR"
}
trap cleanup EXIT INT TERM
BUNDLE_BUILD_DIR="$TEMP_DIR/bundle"
mkdir -m 0755 -- "$BUNDLE_BUILD_DIR"
PAYLOAD_NAME="echo-os_${IMAGE_VERSION}.raw.zst"
PAYLOAD="$BUNDLE_BUILD_DIR/$PAYLOAD_NAME"
MANIFEST="$BUNDLE_BUILD_DIR/INSTALL-MANIFEST.json"
SIGNATURE="$BUNDLE_BUILD_DIR/INSTALL-MANIFEST.json.gpg"
FACTORY_KEY_NAME=FACTORY-DATA-KEY
FACTORY_KEY_TARGET="$BUNDLE_BUILD_DIR/$FACTORY_KEY_NAME"

PARTITIONS_JSON="$TEMP_DIR/partitions.json"
sfdisk --json "$SOURCE_IMAGE" >"$PARTITIONS_JSON"
python3 - "$PARTITIONS_JSON" "$IMAGE_VERSION" "$SOURCE_SIZE" <<'PY'
import json
import sys

path, version, source_size = sys.argv[1], sys.argv[2], int(sys.argv[3])
with open(path, encoding="utf-8") as stream:
    table = json.load(stream)["partitiontable"]
if table.get("label") != "gpt":
    raise SystemExit("installer source must contain a GPT partition table")
expected = [
    ("echo-esp", "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"),
    (f"echo-root-{version}", "4f68bce3-e8cd-4db1-96e7-fbcaf984b709"),
    (f"echo-root-{version}-verity", "2c7357ed-ebd2-46d9-aec1-23d437ec2bf5"),
    (f"echo-root-{version}-verity-sig", "41092b05-9fc8-4523-994f-2def0408b176"),
    ("_empty", "4f68bce3-e8cd-4db1-96e7-fbcaf984b709"),
    ("_empty", "2c7357ed-ebd2-46d9-aec1-23d437ec2bf5"),
    ("_empty", "41092b05-9fc8-4523-994f-2def0408b176"),
    ("echo-var", "4d21b016-b534-45c2-a9fb-5c16e091fd2d"),
    ("echo-swap", "0657fd6d-a4ab-43c4-84e5-0933c84b4f4f"),
    ("echo-home", "933ac7e1-2eb4-4f13-b844-0e14e2aef915"),
]
partitions = table.get("partitions", [])
actual = [
    (item.get("name"), str(item.get("type", "")).lower())
    for item in partitions
]
if actual != expected:
    raise SystemExit(f"installer source partition contract mismatch: {actual!r}")
sector_size = int(table.get("sectorsize", 0))
if sector_size <= 0:
    raise SystemExit("installer source sector size is invalid")
for item in partitions:
    start, size = int(item.get("start", 0)), int(item.get("size", 0))
    if start <= 0 or size <= 0 or (start + size) * sector_size > source_size:
        raise SystemExit(f"partition extent exceeds installer image: {item!r}")
PY

modprobe loop
LOOP_DEVICE="$(losetup --find --show --partscan --read-only "$SOURCE_IMAGE")"
udevadm settle --timeout=30

partition_for_label() {
  local label="$1"
  local -a matches=()
  mapfile -t matches < <(
    lsblk -nrpo PATH,PARTLABEL "$LOOP_DEVICE" |
      awk -v wanted="$label" '$2 == wanted { print $1 }'
  )
  [[ "${#matches[@]}" -eq 1 ]] || {
    echo "installer source partition is ambiguous: $label" >&2
    return 1
  }
  printf '%s\n' "${matches[0]}"
}

ROOT_DEVICE="$(partition_for_label "echo-root-${IMAGE_VERSION}")"
VERITY_DEVICE="$(partition_for_label "echo-root-${IMAGE_VERSION}-verity")"
VERITY_SIG_DEVICE="$(partition_for_label "echo-root-${IMAGE_VERSION}-verity-sig")"
read -r SECTOR_SIZE ESP_START < <(
  python3 - "$PARTITIONS_JSON" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    table = json.load(stream)["partitiontable"]
esp = next((item for item in table["partitions"] if item.get("name") == "echo-esp"), None)
if esp is None:
    raise SystemExit("echo-esp partition is missing")
print(table["sectorsize"], esp["start"])
PY
)
ESP_IMAGE="${SOURCE_IMAGE}@@$((SECTOR_SIZE * ESP_START))"
UKI_DIRECTORY="$TEMP_DIR/main-uki"
mkdir -m 0700 "$UKI_DIRECTORY"
mcopy -i "$ESP_IMAGE" "::/EFI/Linux/echo-os_${IMAGE_VERSION}*.efi" \
  "$UKI_DIRECTORY/"
mapfile -d '' -t MAIN_UKIS < <(
  find "$UKI_DIRECTORY" -maxdepth 1 -type f \
    -name "echo-os_${IMAGE_VERSION}*.efi" -print0
)
[[ "${#MAIN_UKIS[@]}" -eq 1 ]] || {
  echo "installer source must contain exactly one version-matched main UKI" >&2
  exit 1
}
RECOVERY_UKI="$UKI_DIRECTORY/echo-recovery_${IMAGE_VERSION}.efi"
SYSTEMD_BOOT="$UKI_DIRECTORY/systemd-bootx64.efi"
mcopy -i "$ESP_IMAGE" \
  "::/EFI/Linux/echo-recovery_${IMAGE_VERSION}.efi" "$RECOVERY_UKI"
mcopy -i "$ESP_IMAGE" \
  "::/EFI/systemd/systemd-bootx64.efi" "$SYSTEMD_BOOT"
python3 "$VERIFY_VERITY" \
  "$ROOT_DEVICE" "$VERITY_DEVICE" "$VERITY_SIG_DEVICE" \
  "$VERITY_CERTIFICATE" --uki "${MAIN_UKIS[0]}"
sbverify --cert "$VERITY_CERTIFICATE" "${MAIN_UKIS[0]}" >/dev/null
sbverify --cert "$VERITY_CERTIFICATE" "$RECOVERY_UKI" >/dev/null
sbverify --cert "$VERITY_CERTIFICATE" "$SYSTEMD_BOOT" >/dev/null
for uki_kind in main recovery; do
  if [[ "$uki_kind" == main ]]; then
    uki_path="${MAIN_UKIS[0]}"
  else
    uki_path="$RECOVERY_UKI"
  fi
  embedded_pcr_key="$UKI_DIRECTORY/${uki_kind}-pcr-public-key.pem"
  embedded_pcr_signature="$UKI_DIRECTORY/${uki_kind}-pcr-signature.json"
  "$UKIFY_BIN" inspect "$uki_path" \
    --section ".pcrpkey:text@$embedded_pcr_key" \
    --section ".pcrsig:text@$embedded_pcr_signature" >/dev/null
  python3 "$VERIFY_PCR_POLICY" \
    "$PCR_POLICY_PUBLIC_KEY" "$embedded_pcr_key" "$embedded_pcr_signature"
done

for protected_label in echo-var echo-swap echo-home; do
  mapfile -t protected_devices < <(
    lsblk -nrpo PATH,PARTLABEL "$LOOP_DEVICE" |
      awk -v wanted="$protected_label" '$2 == wanted { print $1 }'
  )
  [[ "${#protected_devices[@]}" -eq 1 ]] || {
    echo "installer source encrypted partition is ambiguous: $protected_label" >&2
    exit 1
  }
  protected_device="${protected_devices[0]}"
  cryptsetup isLuks --type luks2 "$protected_device" || {
    echo "installer source partition is not LUKS2: $protected_label" >&2
    exit 1
  }
  cryptsetup open --test-passphrase --key-file "$FACTORY_KEY" \
    "$protected_device" || {
      echo "factory data key does not unlock installer source: $protected_label" >&2
      exit 1
    }
done
echo "Installer source dm-verity, Secure Boot and signed-PCR chain is authenticated"
losetup --detach "$LOOP_DEVICE"
LOOP_DEVICE=""

zstd -T0 -10 --sparse "$SOURCE_IMAGE" -o "$PAYLOAD"
install -m 0444 "$FACTORY_KEY" "$FACTORY_KEY_TARGET"
SOURCE_SHA256="$(sha256sum "$SOURCE_IMAGE" | awk '{print $1}')"
PAYLOAD_SHA256="$(sha256sum "$PAYLOAD" | awk '{print $1}')"
FACTORY_KEY_SHA256="$(sha256sum "$FACTORY_KEY_TARGET" | awk '{print $1}')"
PCR_POLICY_PUBLIC_KEY_SHA256="$(
  sha256sum "$PCR_POLICY_PUBLIC_KEY" | awk '{print $1}'
)"
python3 - \
  "$MANIFEST" "$IMAGE_VERSION" "$PAYLOAD_NAME" "$PAYLOAD_SHA256" \
  "$SOURCE_SHA256" "$SOURCE_SIZE" "$FACTORY_KEY_NAME" \
  "$FACTORY_KEY_SHA256" "$PCR_POLICY_PUBLIC_KEY_SHA256" \
  "$OS_SOURCE_REPOSITORY" "$OS_SOURCE_COMMIT" "$OS_SOURCE_TREE" \
  "$OS_SOURCE_MANIFEST_SHA256" <<'PY'
import json
import os
import sys

(
    target,
    version,
    filename,
    compressed_sha256,
    raw_sha256,
    raw_size,
    factory_key_filename,
    factory_key_sha256,
    pcr_policy_public_key_sha256,
    source_repository,
    source_commit,
    source_tree,
    source_manifest_sha256,
) = sys.argv[1:]
manifest = {
    "schema": 3,
    "product": "echo-os",
    "architecture": "x86-64",
    "version": version,
    "source": {
        "repository": source_repository,
        "commit": source_commit,
        "tree": source_tree,
        "manifest_sha256": source_manifest_sha256,
    },
    "payload": {
        "filename": filename,
        "compression": "zstd",
        "sha256": compressed_sha256,
        "uncompressed_sha256": raw_sha256,
        "uncompressed_size": int(raw_size),
    },
    "disk": {
        "partition_table": "gpt",
        "partition_labels": [
            "echo-esp",
            f"echo-root-{version}",
            f"echo-root-{version}-verity",
            f"echo-root-{version}-verity-sig",
            "_empty",
            "_empty",
            "_empty",
            "echo-var",
            "echo-swap",
            "echo-home",
        ],
    },
    "data_protection": {
        "scheme": "luks2-factory-key",
        "factory_key_filename": factory_key_filename,
        "factory_key_sha256": factory_key_sha256,
        "encrypted_partitions": ["echo-var", "echo-swap", "echo-home"],
        "tpm2_policy": {
            "direct_pcrs": [],
            "signed_pcrs": [11],
            "public_key_sha256": pcr_policy_public_key_sha256,
        },
    },
}
temporary = f"{target}.{os.getpid()}"
with open(temporary, "x", encoding="utf-8") as stream:
    json.dump(manifest, stream, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    stream.write("\n")
    stream.flush()
    os.fsync(stream.fileno())
os.replace(temporary, target)
PY
gpg --batch --yes --local-user "$SIGNING_KEY" --detach-sign \
  --output "$SIGNATURE" "$MANIFEST"
chmod 0444 "$PAYLOAD" "$FACTORY_KEY_TARGET" "$MANIFEST" "$SIGNATURE"

# A successfully generated signature is not sufficient for a release: prove
# that the exact public trust root embedded in Recovery accepts it.
gpgv --keyring "$TRUST_KEYRING" "$SIGNATURE" "$MANIFEST"
python3 "$VERIFY_BUNDLE" "$BUNDLE_BUILD_DIR"

# Publish only after compression, signing and all verification have succeeded.
# The staging directory is created in the destination parent, so this rename is
# atomic. An explicitly supplied empty directory is removed immediately before
# the rename; non-empty destinations and symlinks were rejected above.
if [[ -d "$OUTPUT_DIR" ]]; then
  rmdir -- "$OUTPUT_DIR"
fi
mv -- "$BUNDLE_BUILD_DIR" "$OUTPUT_DIR"
echo "Signed Echo OS $IMAGE_VERSION installer bundle: $OUTPUT_DIR"
