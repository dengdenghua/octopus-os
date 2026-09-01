#!/usr/bin/env bash
# Destructively exercise signed A/B update, healthy activation and failed-boot rollback on a temporary image.
set -euo pipefail
umask 077

IMAGE_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$IMAGE_DIR/../.." && pwd)"
BASE_IMAGE="${1:-}"
BUNDLE_INPUT="${2:-}"
KEYRING_INPUT="${3:-}"
EXPECTED_VERSION="${4:-}"
DEFINITIONS="$REPO_ROOT/deploy/update/sysupdate.d"
VERIFY_BUNDLE="$REPO_ROOT/deploy/update/verify-update-bundle.py"
VERIFY_VERITY="$REPO_ROOT/deploy/update/verify-verity-set.py"
VERIFY_KEYRING="$REPO_ROOT/deploy/installer/verify_public_keyring.py"
UPDATE_COMMAND="$REPO_ROOT/deploy/update/echo-os-update"
INTERRUPT_SYSUPDATE="$IMAGE_DIR/interrupt-sysupdate-after-write.py"
ENCRYPTED_IMAGE="$REPO_ROOT/deploy/data-protection/echo-encrypted-image"
VERIFY_PCR_POLICY="$REPO_ROOT/deploy/data-protection/verify_uki_pcr_policy.py"

[[ $# -eq 4 ]] || {
  echo "usage: $0 BASE.raw SIGNED_BUNDLE KEYRING EXPECTED_VERSION" >&2
  exit 2
}
[[ "$(uname -s)" == "Linux" && "$(id -u)" -eq 0 ]] || {
  echo "A/B update smoke requires a privileged Linux host" >&2
  exit 1
}
[[ "$EXPECTED_VERSION" =~ ^[0-9A-Za-z][0-9A-Za-z.+:~_-]*$ ]] || {
  echo "invalid expected update version: $EXPECTED_VERSION" >&2
  exit 2
}
for command_name in \
  awk cat chmod cmp cp dd grep ln losetup lsblk mcopy mdel mmd mkdir mkosi mktemp mrd \
  modprobe python3 realpath rm sed sfdisk sha256sum sleep stat sync tail tee tr udevadm veritysetup; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "A/B smoke dependency missing: $command_name" >&2
    exit 1
  }
done
[[ -x "$ENCRYPTED_IMAGE" ]] || {
  echo "encrypted-image test harness is missing" >&2
  exit 1
}
[[ -x "$VERIFY_PCR_POLICY" ]] || {
  echo "UKI PCR policy verifier is missing" >&2
  exit 1
}
[[ -x "$VERIFY_VERITY" ]] || {
  echo "dm-verity set verifier is missing" >&2
  exit 1
}
[[ -x "$VERIFY_KEYRING" && -x "$UPDATE_COMMAND" ]] || {
  echo "production update entrypoint or keyring verifier is missing" >&2
  exit 1
}
[[ -x "$INTERRUPT_SYSUPDATE" ]] || {
  echo "real sysupdate interruption helper is missing" >&2
  exit 1
}
[[ -n "${ECHO_SECURE_BOOT_CERTIFICATE:-}" && \
   -f "$ECHO_SECURE_BOOT_CERTIFICATE" && \
   ! -L "$ECHO_SECURE_BOOT_CERTIFICATE" ]] || {
  echo "ECHO_SECURE_BOOT_CERTIFICATE must name the release verity certificate" >&2
  exit 1
}
VERITY_CERTIFICATE="$(realpath "$ECHO_SECURE_BOOT_CERTIFICATE")"
[[ -n "${ECHO_DATA_RECOVERY_KEY:-}" && \
   -f "$ECHO_DATA_RECOVERY_KEY" && ! -L "$ECHO_DATA_RECOVERY_KEY" ]] || {
  echo "ECHO_DATA_RECOVERY_KEY must name the installed device recovery key" >&2
  exit 1
}
RECOVERY_KEY="$(realpath "$ECHO_DATA_RECOVERY_KEY")"

BASE_IMAGE="$(realpath "$BASE_IMAGE")"
BUNDLE="$(realpath "$BUNDLE_INPUT")"
KEYRING="$(realpath "$KEYRING_INPUT")"
[[ -f "$BASE_IMAGE" && -d "$BUNDLE" && -f "$KEYRING" ]] || {
  echo "base image, bundle or keyring is missing" >&2
  exit 1
}

if command -v ukify >/dev/null 2>&1; then
  UKIFY_BIN="$(command -v ukify)"
elif [[ -x /usr/lib/systemd/ukify ]]; then
  UKIFY_BIN=/usr/lib/systemd/ukify
else
  echo "ukify is unavailable" >&2
  exit 1
fi

TEMP_DIR="$(mktemp -d)"
LOG_ROOT="${ECHO_AB_LOG_DIR:-$TEMP_DIR/logs}"
mkdir -p "$LOG_ROOT"
[[ "$LOG_ROOT" == /* && -d "$LOG_ROOT" && ! -L "$LOG_ROOT" ]] || {
  echo "A/B evidence root must be an absolute real directory" >&2
  exit 1
}
chmod 0700 "$LOG_ROOT"
LOG_ROOT="$(realpath "$LOG_ROOT")"
UPDATED_IMAGE="$TEMP_DIR/echo-os-updated.raw"
ROLLBACK_IMAGE="$TEMP_DIR/echo-os-rollback.raw"
BASE_OS_RELEASE="$TEMP_DIR/base-os-release"
PARTITIONS_JSON="$TEMP_DIR/partitions.json"
INTERRUPTED_PARTITIONS_JSON="$TEMP_DIR/interrupted-partitions.json"
UPDATED_UKI="$TEMP_DIR/updated.efi"
UPDATED_PCR_PUBLIC_KEY="$TEMP_DIR/updated-pcr-public-key.pem"
UPDATED_PCR_SIGNATURE="$TEMP_DIR/updated-pcr-signature.json"
APP_STATE_SOURCE="$TEMP_DIR/flatpak-ab-state"
APP_STATE_COPY="$TEMP_DIR/flatpak-ab-state-after-update"
MACHINE_ID_SOURCE="$TEMP_DIR/machine-id"
MACHINE_ID_COPY="$TEMP_DIR/machine-id-after-update"
NETWORK_PROFILE_SOURCE="$TEMP_DIR/echo-ab-persistence.nmconnection"
NETWORK_PROFILE_COPY="$TEMP_DIR/network-profile-after-update"
NETWORK_PROFILE_ROLLBACK_COPY="$TEMP_DIR/network-profile-after-rollback"
REGION_STATE_SOURCE="$TEMP_DIR/region-state.json"
REGION_STATE_COPY="$TEMP_DIR/region-state-after-update.json"
REGION_STATE_ROLLBACK_COPY="$TEMP_DIR/region-state-after-rollback.json"
OEM_MARKER_SOURCE="$TEMP_DIR/oem-complete.json"
OEM_MARKER_COPY="$TEMP_DIR/oem-complete-after-update.json"
OEM_MARKER_ROLLBACK_COPY="$TEMP_DIR/oem-complete-after-rollback.json"
ACCOUNT_SHADOW_SOURCE="$TEMP_DIR/local-account.shadow"
ACCOUNT_SHADOW_COPY="$TEMP_DIR/local-account-after-update.shadow"
ACCOUNT_SHADOW_ROLLBACK_COPY="$TEMP_DIR/local-account-after-rollback.shadow"
UPDATE_APPLY_LOG="$LOG_ROOT/echo-update-apply.log"
INTERRUPTED_UPDATE_LOG="$LOG_ROOT/echo-update-interrupted.log"
ESP_FULL_UPDATE_LOG="$LOG_ROOT/echo-update-esp-full.log"
AB_EVIDENCE_LOG="$LOG_ROOT/echo-ab-update-evidence.log"
DM_VERITY_REJECTION_LOG="$LOG_ROOT/dm-verity-rejection.log"
[[ ! -e "$UPDATE_APPLY_LOG" && ! -L "$UPDATE_APPLY_LOG" && \
   ! -e "$INTERRUPTED_UPDATE_LOG" && ! -L "$INTERRUPTED_UPDATE_LOG" && \
   ! -e "$ESP_FULL_UPDATE_LOG" && ! -L "$ESP_FULL_UPDATE_LOG" && \
   ! -e "$AB_EVIDENCE_LOG" && ! -L "$AB_EVIDENCE_LOG" && \
   ! -e "$DM_VERITY_REJECTION_LOG" && ! -L "$DM_VERITY_REJECTION_LOG" ]] || {
  echo "A/B evidence outputs must be new non-symlink files" >&2
  exit 1
}
cleanup() {
  if [[ -n "${LOOP_DEVICE:-}" ]]; then
    losetup --detach "$LOOP_DEVICE" >/dev/null 2>&1 || true
  fi
  rm -rf -- "$TEMP_DIR"
}
trap cleanup EXIT INT TERM

BASE_VERSION_HINT="${ECHO_BASE_IMAGE_VERSION:-$(
  tr -d '[:space:]' <"$IMAGE_DIR/mkosi.version"
)}"
"$ENCRYPTED_IMAGE" copy-from \
  "$BASE_IMAGE" "$BASE_VERSION_HINT" "$RECOVERY_KEY" \
  /usr/lib/os-release "$BASE_OS_RELEASE"
BASE_VERSION="$(sed -n 's/^IMAGE_VERSION=//p' "$BASE_OS_RELEASE" | tr -d '\"')"
[[ "$BASE_VERSION" == "$BASE_VERSION_HINT" && \
   "$BASE_VERSION" != "$EXPECTED_VERSION" ]] || {
  echo "base and update versions must be different" >&2
  exit 1
}

# The base for this lifecycle test must already have completed the production
# OEM path. Preserve those exact device/account/region values across update and
# rollback instead of injecting replacement identity after installation.
"$ENCRYPTED_IMAGE" copy-from \
  "$BASE_IMAGE" "$BASE_VERSION" "$RECOVERY_KEY" \
  /var/lib/echo-os/machine-id "$MACHINE_ID_SOURCE"
"$ENCRYPTED_IMAGE" copy-from \
  "$BASE_IMAGE" "$BASE_VERSION" "$RECOVERY_KEY" \
  /var/lib/echo-os/region-state.json "$REGION_STATE_SOURCE"
"$ENCRYPTED_IMAGE" copy-from \
  "$BASE_IMAGE" "$BASE_VERSION" "$RECOVERY_KEY" \
  /var/lib/echo-os/oem-complete.json "$OEM_MARKER_SOURCE"
"$ENCRYPTED_IMAGE" copy-from \
  "$BASE_IMAGE" "$BASE_VERSION" "$RECOVERY_KEY" \
  /var/lib/echo-os/local-account.shadow "$ACCOUNT_SHADOW_SOURCE"
grep -Eq '^[0-9a-f]{32}$' "$MACHINE_ID_SOURCE" || {
  echo "provisioned base machine-id is invalid" >&2
  exit 1
}
grep -Eq '^\$[A-Za-z0-9./]+\$[A-Za-z0-9./$=,_-]+$' "$ACCOUNT_SHADOW_SOURCE" || {
  echo "provisioned base password hash is invalid" >&2
  exit 1
}
[[ "$(stat -c '%a' "$REGION_STATE_SOURCE")" == 600 && \
   "$(stat -c '%a' "$OEM_MARKER_SOURCE")" == 600 && \
   "$(stat -c '%a' "$ACCOUNT_SHADOW_SOURCE")" == 600 ]] || {
  echo "provisioned persistent identity state is not private" >&2
  exit 1
}
python3 - "$REGION_STATE_SOURCE" "$OEM_MARKER_SOURCE" "$BASE_VERSION" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    region = json.load(stream)
expected_region = {
    "schema": 1,
    "locale": "zh_CN.UTF-8",
    "keymap": "us",
    "timezone": "Asia/Shanghai",
}
if region != expected_region:
    raise SystemExit(f"provisioned base region is invalid: {region!r}")
with open(sys.argv[2], encoding="utf-8") as stream:
    marker = json.load(stream)
expected_marker_keys = {
    "schema",
    "account",
    "display_name",
    "hostname",
    "completed_unix",
    "root_version",
}
if set(marker) != expected_marker_keys:
    raise SystemExit(f"provisioned base OEM fields are invalid: {sorted(marker)}")
if (
    marker.get("schema") != 2
    or marker.get("account") != "echo"
    or marker.get("display_name") != "Echo CI"
    or marker.get("hostname") != "echo-oem-ci"
    or marker.get("root_version") != sys.argv[3]
    or not isinstance(marker.get("completed_unix"), int)
    or marker["completed_unix"] <= 0
):
    raise SystemExit(f"provisioned base OEM marker is invalid: {marker!r}")
print("provisioned base identity verified")
PY

VERIFIED_RECORD="$(python3 "$VERIFY_BUNDLE" --machine "$BUNDLE")"
IFS=$'\t' read -r VERIFIED_VERSION OS_SOURCE_COMMIT OS_SOURCE_TREE \
  OS_SOURCE_MANIFEST_SHA256 <<<"$VERIFIED_RECORD"
[[ "$VERIFIED_VERSION" == "$EXPECTED_VERSION" ]] || {
  echo "verified bundle version $VERIFIED_VERSION does not match $EXPECTED_VERSION" >&2
  exit 1
}
[[ "$OS_SOURCE_COMMIT" =~ ^[0-9a-f]{40}$ && \
   "$OS_SOURCE_TREE" =~ ^[0-9a-f]{40}$ && \
   "$OS_SOURCE_MANIFEST_SHA256" =~ ^[0-9a-f]{64}$ ]] || {
  echo "verified update bundle has no valid OS source identity" >&2
  exit 1
}
UPDATE_MANIFEST_SHA256="$(sha256sum "$BUNDLE/SHA256SUMS" | awk '{print $1}')"
UPDATE_SIGNATURE_SHA256="$(sha256sum "$BUNDLE/SHA256SUMS.gpg" | awk '{print $1}')"
[[ "$UPDATE_MANIFEST_SHA256" =~ ^[0-9a-f]{64}$ && \
   "$UPDATE_SIGNATURE_SHA256" =~ ^[0-9a-f]{64}$ ]] || {
  echo "verified update manifest or signature cannot be hashed" >&2
  exit 1
}

cp --reflink=auto --sparse=always "$BASE_IMAGE" "$UPDATED_IMAGE"
printf 'echo-flatpak-state base=%s update=%s\n' \
  "$BASE_VERSION" "$EXPECTED_VERSION" >"$APP_STATE_SOURCE"
"$ENCRYPTED_IMAGE" copy-to \
  "$UPDATED_IMAGE" "$BASE_VERSION" "$RECOVERY_KEY" \
  "$APP_STATE_SOURCE" /var/lib/flatpak/echo-os-ab-persistence
printf '%s\n' \
  '[connection]' \
  'id=Echo A/B Persistence Test' \
  'uuid=3f36e1b2-d650-4f56-966f-3d1bc399e02f' \
  'type=ethernet' \
  'autoconnect=false' \
  'interface-name=echo-ab-test' \
  '' \
  '[ethernet]' >"$NETWORK_PROFILE_SOURCE"
chmod 0600 "$NETWORK_PROFILE_SOURCE"
"$ENCRYPTED_IMAGE" copy-to \
  "$UPDATED_IMAGE" "$BASE_VERSION" "$RECOVERY_KEY" \
  "$NETWORK_PROFILE_SOURCE" \
  /var/lib/NetworkManager/system-connections/echo-ab-persistence.nmconnection

# Exercise a real mid-write interruption through the production entrypoint.
# The PATH shim launches the host's real systemd-sysupdate, watches the first
# bounded sample of the inactive root and SIGKILLs its whole process group at
# the first observed byte change.  This proves more than a fixed sleep: the
# command reached the disposable disk, but the UKI publication transfer did
# not run. The capacity-failure and final normal apply below keep using this
# same partially written raw.
sfdisk --json "$UPDATED_IMAGE" >"$INTERRUPTED_PARTITIONS_JSON"
read -r INTERRUPT_SECTOR_SIZE INTERRUPT_ESP_START \
  INTERRUPT_ROOT_START INTERRUPT_ROOT_SECTORS < <(
  python3 - "$INTERRUPTED_PARTITIONS_JSON" "$BASE_VERSION" <<'PY'
import json
import sys

ROOT_X86_64 = "4f68bce3-e8cd-4db1-96e7-fbcaf984b709"
with open(sys.argv[1], encoding="utf-8") as stream:
    table = json.load(stream)["partitiontable"]
parts = table["partitions"]
labels = [item.get("name") for item in parts]
required = {
    "echo-esp",
    f"echo-root-{sys.argv[2]}",
    f"echo-root-{sys.argv[2]}-verity",
    f"echo-root-{sys.argv[2]}-verity-sig",
}
missing = sorted(required.difference(labels))
inactive_roots = [
    item
    for item in parts
    if item.get("name") == "_empty" and item.get("type", "").lower() == ROOT_X86_64
]
esp = [item for item in parts if item.get("name") == "echo-esp"]
if missing or len(inactive_roots) != 1 or len(esp) != 1:
    raise SystemExit(
        f"base A/B slots are not uniquely addressable: missing={missing}, labels={labels}"
    )
root = inactive_roots[0]
print(table["sectorsize"], esp[0]["start"], root["start"], root["size"])
PY
)
INTERRUPT_SAMPLE_SECTORS=128
if (( INTERRUPT_ROOT_SECTORS < INTERRUPT_SAMPLE_SECTORS )); then
  INTERRUPT_SAMPLE_SECTORS="$INTERRUPT_ROOT_SECTORS"
fi
INTERRUPT_BEFORE_SHA256="$(
  dd if="$UPDATED_IMAGE" bs="$INTERRUPT_SECTOR_SIZE" \
    skip="$INTERRUPT_ROOT_START" count="$INTERRUPT_SAMPLE_SECTORS" status=none |
    sha256sum | awk '{print $1}'
)"
[[ "$INTERRUPT_BEFORE_SHA256" =~ ^[0-9a-f]{64}$ ]] || {
  echo "cannot hash the inactive-root interruption sample" >&2
  exit 1
}
if command -v systemd-sysupdate >/dev/null 2>&1; then
  REAL_SYSUPDATE_BIN="$(realpath "$(command -v systemd-sysupdate)")"
elif [[ -x /usr/lib/systemd/systemd-sysupdate ]]; then
  REAL_SYSUPDATE_BIN=/usr/lib/systemd/systemd-sysupdate
else
  echo "systemd-sysupdate is unavailable for the interruption gate" >&2
  exit 1
fi
INTERRUPT_BIN_DIR="$TEMP_DIR/interruption-bin"
mkdir -p "$INTERRUPT_BIN_DIR"
ln -s "$INTERRUPT_SYSUPDATE" "$INTERRUPT_BIN_DIR/systemd-sysupdate"
AUTHENTICATED_MARKER="ECHO_UPDATE_BUNDLE_AUTHENTICATED version=$EXPECTED_VERSION os=$OS_SOURCE_COMMIT tree=$OS_SOURCE_TREE source-manifest=$OS_SOURCE_MANIFEST_SHA256 manifest=$UPDATE_MANIFEST_SHA256 signature=$UPDATE_SIGNATURE_SHA256"
if PATH="$INTERRUPT_BIN_DIR:$PATH" \
   ECHO_REAL_SYSUPDATE_BIN="$REAL_SYSUPDATE_BIN" \
   ECHO_UPDATE_INTERRUPT_IMAGE="$UPDATED_IMAGE" \
   ECHO_UPDATE_INTERRUPT_SECTOR_SIZE="$INTERRUPT_SECTOR_SIZE" \
   ECHO_UPDATE_INTERRUPT_START_SECTOR="$INTERRUPT_ROOT_START" \
   ECHO_UPDATE_INTERRUPT_SECTOR_COUNT="$INTERRUPT_SAMPLE_SECTORS" \
   ECHO_UPDATE_INTERRUPT_BEFORE_SHA256="$INTERRUPT_BEFORE_SHA256" \
   ECHO_UPDATE_SOURCE_SMOKE=USE-SOURCE-RUNTIME \
   ECHO_UPDATE_SYSTEM_KEYRING="$KEYRING" \
   ECHO_UPDATE_ADMIN_KEYRING="$TEMP_DIR/no-admin-update-keyring.gpg" \
   ECHO_UPDATE_DEFINITIONS="$DEFINITIONS" \
   ECHO_UPDATE_VERIFY_BUNDLE="$VERIFY_BUNDLE" \
   ECHO_UPDATE_VERIFY_VERITY="$VERIFY_VERITY" \
   ECHO_UPDATE_VERITY_CERTIFICATE="$VERITY_CERTIFICATE" \
   ECHO_UPDATE_VERIFY_KEYRING="$VERIFY_KEYRING" \
   ECHO_UPDATE_IMAGE="$UPDATED_IMAGE" \
   ECHO_UPDATE_LOCK="$TEMP_DIR/update.lock" \
     "$UPDATE_COMMAND" apply "$BUNDLE" >"$INTERRUPTED_UPDATE_LOG" 2>&1; then
  echo "mid-write systemd-sysupdate unexpectedly completed" >&2
  exit 1
fi
grep -Fqx "$AUTHENTICATED_MARKER" "$INTERRUPTED_UPDATE_LOG"
grep -Fqx \
  "ECHO_UPDATE_CANDIDATE_READY version=$EXPECTED_VERSION source=authenticated-bundle" \
  "$INTERRUPTED_UPDATE_LOG"
grep -Eq '^ECHO_UPDATE_INTERRUPTION_TRIGGERED sample=inactive-root-first-[1-9][0-9]* signal=SIGKILL before=[0-9a-f]{64} after=[0-9a-f]{64}$' \
  "$INTERRUPTED_UPDATE_LOG"
grep -Fqx 'ECHO_UPDATE_INTERRUPTION_OBSERVED result=signal-9' \
  "$INTERRUPTED_UPDATE_LOG"
if grep -q '^ECHO_UPDATE_APPLIED ' "$INTERRUPTED_UPDATE_LOG"; then
  echo "interrupted update emitted a false applied marker" >&2
  exit 1
fi
INTERRUPT_AFTER_SHA256="$(
  dd if="$UPDATED_IMAGE" bs="$INTERRUPT_SECTOR_SIZE" \
    skip="$INTERRUPT_ROOT_START" count="$INTERRUPT_SAMPLE_SECTORS" status=none |
    sha256sum | awk '{print $1}'
)"
[[ "$INTERRUPT_AFTER_SHA256" =~ ^[0-9a-f]{64}$ && \
   "$INTERRUPT_AFTER_SHA256" != "$INTERRUPT_BEFORE_SHA256" ]] || {
  echo "sysupdate interruption did not leave an observable partial root write" >&2
  exit 1
}
for ((wait_count = 0; wait_count < 100; wait_count += 1)); do
  [[ -z "$(losetup -j "$UPDATED_IMAGE")" ]] && break
  sleep 0.05
done
[[ -z "$(losetup -j "$UPDATED_IMAGE")" ]] || {
  echo "interrupted systemd-sysupdate leaked a live loop device" >&2
  exit 1
}
udevadm settle --timeout=30
sfdisk --json "$UPDATED_IMAGE" >"$INTERRUPTED_PARTITIONS_JSON"
python3 - "$INTERRUPTED_PARTITIONS_JSON" "$BASE_VERSION" "$EXPECTED_VERSION" <<'PY'
import json
import sys

ROOT_X86_64 = "4f68bce3-e8cd-4db1-96e7-fbcaf984b709"
with open(sys.argv[1], encoding="utf-8") as stream:
    parts = json.load(stream)["partitiontable"]["partitions"]
labels = [item.get("name") for item in parts]
base_required = {
    f"echo-root-{sys.argv[2]}",
    f"echo-root-{sys.argv[2]}-verity",
    f"echo-root-{sys.argv[2]}-verity-sig",
}
forbidden = {
    f"echo-root-{sys.argv[3]}",
    f"echo-root-{sys.argv[3]}-verity",
    f"echo-root-{sys.argv[3]}-verity-sig",
}
inactive_roots = [
    item
    for item in parts
    if item.get("name") == "_empty" and item.get("type", "").lower() == ROOT_X86_64
]
if not base_required.issubset(labels) or forbidden.intersection(labels) or len(inactive_roots) != 1:
    raise SystemExit(f"interrupted A/B labels published incomplete state: {labels}")
print("interrupted A/B partition publication correctly remained incomplete")
PY
INTERRUPTED_ESP="${UPDATED_IMAGE}@@$((INTERRUPT_ESP_START * INTERRUPT_SECTOR_SIZE))"
BASE_UKI_PROBE="$TEMP_DIR/interrupted-base.efi"
if mcopy -i "$INTERRUPTED_ESP" "::/EFI/Linux/echo-os_${BASE_VERSION}.efi" \
     "$BASE_UKI_PROBE" >/dev/null 2>&1; then
  :
elif mcopy -i "$INTERRUPTED_ESP" "::/EFI/Linux/echo-os_${BASE_VERSION}+3-0.efi" \
       "$BASE_UKI_PROBE" >/dev/null 2>&1; then
  :
else
  echo "interrupted disk lost the bootable base UKI" >&2
  exit 1
fi
if mcopy -i "$INTERRUPTED_ESP" \
     "::/EFI/Linux/echo-os_${EXPECTED_VERSION}+3-0.efi" \
     "$TEMP_DIR/unexpected-interrupted-update.efi" >/dev/null 2>&1; then
  echo "interrupted update published its UKI before all backing resources" >&2
  exit 1
fi
printf '%s\n' \
  "ECHO_UPDATE_INTERRUPTION_CONFIRMED version=$EXPECTED_VERSION inactive-root=changed labels=unpublished uki=unpublished applied-marker=absent" \
  >>"$INTERRUPTED_UPDATE_LOG"
ECHO_IMAGE_VERSION="$BASE_VERSION" \
ECHO_BOOT_EPHEMERAL=yes \
ECHO_BOOT_LOG_DIR="$LOG_ROOT/interrupted-base-boot" \
  "$IMAGE_DIR/smoke-boot-image.sh" "$UPDATED_IMAGE"
echo "  ✓ mid-write SIGKILL leaves the previous UKI/root bootable"

# Keep using the same partially written disk. Exhaust the ESP with copies of
# the authenticated update UKI, retry the production updater, and require an
# actual ENOSPC failure without resource publication. This models a realistic
# late target-capacity failure after the inactive root has already been dirtied.
UPDATE_UKI_PAYLOAD="$BUNDLE/echo-os_${EXPECTED_VERSION}.efi"
[[ -f "$UPDATE_UKI_PAYLOAD" && ! -L "$UPDATE_UKI_PAYLOAD" ]] || {
  echo "authenticated update UKI is unavailable for ESP exhaustion" >&2
  exit 1
}
UPDATE_UKI_SIZE="$(stat -c '%s' "$UPDATE_UKI_PAYLOAD")"
[[ "$UPDATE_UKI_SIZE" =~ ^[1-9][0-9]*$ ]] || {
  echo "authenticated update UKI has an invalid size" >&2
  exit 1
}
ESP_FILL_COUNT=0
ESP_FILL_FAILURE="$TEMP_DIR/esp-fill-failure.log"
LC_ALL=C mmd -i "$INTERRUPTED_ESP" ::/ESPTEST
for ((fill_index = 0; fill_index < 1024; fill_index += 1)); do
  if LC_ALL=C mcopy -i "$INTERRUPTED_ESP" "$UPDATE_UKI_PAYLOAD" \
       "::/ESPTEST/ECHO${fill_index}.BIN" >"$ESP_FILL_FAILURE" 2>&1; then
    ESP_FILL_COUNT=$((ESP_FILL_COUNT + 1))
  else
    break
  fi
done
[[ "$ESP_FILL_COUNT" -gt 0 && "$ESP_FILL_COUNT" -lt 1024 ]] || {
  echo "ESP exhaustion did not reach a bounded capacity failure" >&2
  cat "$ESP_FILL_FAILURE" >&2 || true
  exit 1
}
printf '%s\n' \
  "ECHO_UPDATE_ESP_EXHAUSTED fillers=$ESP_FILL_COUNT filler-bytes=$UPDATE_UKI_SIZE target=esp" \
  >"$ESP_FULL_UPDATE_LOG"
if LC_ALL=C \
   ECHO_UPDATE_SOURCE_SMOKE=USE-SOURCE-RUNTIME \
   ECHO_UPDATE_SYSTEM_KEYRING="$KEYRING" \
   ECHO_UPDATE_ADMIN_KEYRING="$TEMP_DIR/no-admin-update-keyring.gpg" \
   ECHO_UPDATE_DEFINITIONS="$DEFINITIONS" \
   ECHO_UPDATE_VERIFY_BUNDLE="$VERIFY_BUNDLE" \
   ECHO_UPDATE_VERIFY_VERITY="$VERIFY_VERITY" \
   ECHO_UPDATE_VERITY_CERTIFICATE="$VERITY_CERTIFICATE" \
   ECHO_UPDATE_VERIFY_KEYRING="$VERIFY_KEYRING" \
   ECHO_UPDATE_IMAGE="$UPDATED_IMAGE" \
   ECHO_UPDATE_LOCK="$TEMP_DIR/update.lock" \
     "$UPDATE_COMMAND" apply "$BUNDLE" >>"$ESP_FULL_UPDATE_LOG" 2>&1; then
  echo "ESP-exhausted systemd-sysupdate unexpectedly completed" >&2
  exit 1
fi
grep -Fqx "$AUTHENTICATED_MARKER" "$ESP_FULL_UPDATE_LOG"
grep -Fqx \
  "ECHO_UPDATE_CANDIDATE_READY version=$EXPECTED_VERSION source=authenticated-bundle" \
  "$ESP_FULL_UPDATE_LOG"
grep -Eqi 'No space left on device|ENOSPC|Disk full' "$ESP_FULL_UPDATE_LOG" || {
  echo "ESP-exhausted update failed for a reason other than target capacity" >&2
  tail -120 "$ESP_FULL_UPDATE_LOG" >&2
  exit 1
}
if grep -q '^ECHO_UPDATE_APPLIED ' "$ESP_FULL_UPDATE_LOG"; then
  echo "ESP-exhausted update emitted a false applied marker" >&2
  exit 1
fi
for ((wait_count = 0; wait_count < 100; wait_count += 1)); do
  [[ -z "$(losetup -j "$UPDATED_IMAGE")" ]] && break
  sleep 0.05
done
[[ -z "$(losetup -j "$UPDATED_IMAGE")" ]] || {
  echo "ESP-exhausted systemd-sysupdate leaked a live loop device" >&2
  exit 1
}
udevadm settle --timeout=30
sfdisk --json "$UPDATED_IMAGE" >"$INTERRUPTED_PARTITIONS_JSON"
python3 - "$INTERRUPTED_PARTITIONS_JSON" "$BASE_VERSION" "$EXPECTED_VERSION" <<'PY'
import json
import sys

ROOT_X86_64 = "4f68bce3-e8cd-4db1-96e7-fbcaf984b709"
with open(sys.argv[1], encoding="utf-8") as stream:
    parts = json.load(stream)["partitiontable"]["partitions"]
labels = [item.get("name") for item in parts]
base_required = {
    f"echo-root-{sys.argv[2]}",
    f"echo-root-{sys.argv[2]}-verity",
    f"echo-root-{sys.argv[2]}-verity-sig",
}
forbidden = {
    f"echo-root-{sys.argv[3]}",
    f"echo-root-{sys.argv[3]}-verity",
    f"echo-root-{sys.argv[3]}-verity-sig",
}
inactive_roots = [
    item
    for item in parts
    if item.get("name") == "_empty" and item.get("type", "").lower() == ROOT_X86_64
]
if not base_required.issubset(labels) or forbidden.intersection(labels) or len(inactive_roots) != 1:
    raise SystemExit(f"ESP-full A/B failure published incomplete state: {labels}")
print("ESP-full A/B partition publication correctly remained incomplete")
PY
if mcopy -i "$INTERRUPTED_ESP" \
     "::/EFI/Linux/echo-os_${EXPECTED_VERSION}+3-0.efi" \
     "$TEMP_DIR/unexpected-esp-full-update.efi" >/dev/null 2>&1; then
  echo "ESP-exhausted update published its new UKI" >&2
  exit 1
fi
rm -f -- "$BASE_UKI_PROBE"
if mcopy -i "$INTERRUPTED_ESP" "::/EFI/Linux/echo-os_${BASE_VERSION}.efi" \
     "$BASE_UKI_PROBE" >/dev/null 2>&1; then
  :
elif mcopy -i "$INTERRUPTED_ESP" "::/EFI/Linux/echo-os_${BASE_VERSION}+3-0.efi" \
       "$BASE_UKI_PROBE" >/dev/null 2>&1; then
  :
else
  echo "ESP-exhausted disk lost the bootable base UKI" >&2
  exit 1
fi
printf '%s\n' \
  "ECHO_UPDATE_ESP_FULL_CONFIRMED version=$EXPECTED_VERSION labels=unpublished uki=unpublished applied-marker=absent old-boot-entry=present" \
  >>"$ESP_FULL_UPDATE_LOG"
ECHO_IMAGE_VERSION="$BASE_VERSION" \
ECHO_BOOT_EPHEMERAL=yes \
ECHO_BOOT_LOG_DIR="$LOG_ROOT/esp-full-base-boot" \
  "$IMAGE_DIR/smoke-boot-image.sh" "$UPDATED_IMAGE"
echo "  ✓ full ESP rejects update publication and leaves the previous root bootable"
LC_ALL=C mdel -i "$INTERRUPTED_ESP" '::/ESPTEST/ECHO*.BIN'
LC_ALL=C mrd -i "$INTERRUPTED_ESP" ::/ESPTEST
sync -f "$UPDATED_IMAGE"

ECHO_UPDATE_SOURCE_SMOKE=USE-SOURCE-RUNTIME \
ECHO_UPDATE_SYSTEM_KEYRING="$KEYRING" \
ECHO_UPDATE_ADMIN_KEYRING="$TEMP_DIR/no-admin-update-keyring.gpg" \
ECHO_UPDATE_DEFINITIONS="$DEFINITIONS" \
ECHO_UPDATE_VERIFY_BUNDLE="$VERIFY_BUNDLE" \
ECHO_UPDATE_VERIFY_VERITY="$VERIFY_VERITY" \
ECHO_UPDATE_VERITY_CERTIFICATE="$VERITY_CERTIFICATE" \
ECHO_UPDATE_VERIFY_KEYRING="$VERIFY_KEYRING" \
ECHO_UPDATE_IMAGE="$UPDATED_IMAGE" \
ECHO_UPDATE_LOCK="$TEMP_DIR/update.lock" \
  "$UPDATE_COMMAND" apply "$BUNDLE" | tee "$UPDATE_APPLY_LOG"
grep -Fqx \
  "ECHO_UPDATE_CANDIDATE_READY version=$EXPECTED_VERSION source=authenticated-bundle" \
  "$UPDATE_APPLY_LOG"
grep -Fqx \
  "ECHO_UPDATE_APPLIED version=$EXPECTED_VERSION os=$OS_SOURCE_COMMIT tree=$OS_SOURCE_TREE source-manifest=$OS_SOURCE_MANIFEST_SHA256 manifest=$UPDATE_MANIFEST_SHA256 signature=$UPDATE_SIGNATURE_SHA256 target=inactive-root-uki-last" \
  "$UPDATE_APPLY_LOG"
printf '%s\n' \
  "ECHO_UPDATE_INTERRUPTION_RECOVERED version=$EXPECTED_VERSION result=flushed-and-applied" \
  >>"$INTERRUPTED_UPDATE_LOG"
printf '%s\n' \
  "ECHO_UPDATE_ESP_FULL_RECOVERED version=$EXPECTED_VERSION result=fillers-removed-and-applied" \
  >>"$ESP_FULL_UPDATE_LOG"
echo "  ✓ production updater recovered the same interrupted and space-exhausted disk"

"$ENCRYPTED_IMAGE" copy-from \
  "$UPDATED_IMAGE" "$EXPECTED_VERSION" "$RECOVERY_KEY" \
  /var/lib/flatpak/echo-os-ab-persistence "$APP_STATE_COPY"
cmp "$APP_STATE_SOURCE" "$APP_STATE_COPY"
echo "  ✓ system Flatpak state in persistent /var survives root replacement"
"$ENCRYPTED_IMAGE" copy-from \
  "$UPDATED_IMAGE" "$EXPECTED_VERSION" "$RECOVERY_KEY" \
  /var/lib/echo-os/machine-id "$MACHINE_ID_COPY"
cmp "$MACHINE_ID_SOURCE" "$MACHINE_ID_COPY"
echo "  ✓ unique device machine-id state survives root replacement"
"$ENCRYPTED_IMAGE" copy-from \
  "$UPDATED_IMAGE" "$EXPECTED_VERSION" "$RECOVERY_KEY" \
  /var/lib/NetworkManager/system-connections/echo-ab-persistence.nmconnection \
  "$NETWORK_PROFILE_COPY"
cmp "$NETWORK_PROFILE_SOURCE" "$NETWORK_PROFILE_COPY"
echo "  ✓ private NetworkManager profile survives root replacement"
"$ENCRYPTED_IMAGE" copy-from \
  "$UPDATED_IMAGE" "$EXPECTED_VERSION" "$RECOVERY_KEY" \
  /var/lib/echo-os/region-state.json "$REGION_STATE_COPY"
cmp "$REGION_STATE_SOURCE" "$REGION_STATE_COPY"
echo "  ✓ locale, keymap and timezone state survive root replacement"
"$ENCRYPTED_IMAGE" copy-from \
  "$UPDATED_IMAGE" "$EXPECTED_VERSION" "$RECOVERY_KEY" \
  /var/lib/echo-os/oem-complete.json "$OEM_MARKER_COPY"
cmp "$OEM_MARKER_SOURCE" "$OEM_MARKER_COPY"
"$ENCRYPTED_IMAGE" copy-from \
  "$UPDATED_IMAGE" "$EXPECTED_VERSION" "$RECOVERY_KEY" \
  /var/lib/echo-os/local-account.shadow "$ACCOUNT_SHADOW_COPY"
cmp "$ACCOUNT_SHADOW_SOURCE" "$ACCOUNT_SHADOW_COPY"
echo "  ✓ production OEM identity and password hash survive root replacement"

sfdisk --json "$UPDATED_IMAGE" >"$PARTITIONS_JSON"
read -r SECTOR_SIZE ESP_START UPDATE_ROOT_START < <(
  python3 - "$PARTITIONS_JSON" "$BASE_VERSION" "$EXPECTED_VERSION" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    table = json.load(stream)["partitiontable"]
parts = {item.get("name"): item for item in table["partitions"]}
required = [
    "echo-esp",
    f"echo-root-{sys.argv[2]}",
    f"echo-root-{sys.argv[2]}-verity",
    f"echo-root-{sys.argv[2]}-verity-sig",
    f"echo-root-{sys.argv[3]}",
    f"echo-root-{sys.argv[3]}-verity",
    f"echo-root-{sys.argv[3]}-verity-sig",
]
missing = [name for name in required if name not in parts]
if missing or "_empty" in parts:
    raise SystemExit(f"A/B labels are not committed: missing={missing}, labels={sorted(parts)}")
print(table["sectorsize"], parts["echo-esp"]["start"], parts[f"echo-root-{sys.argv[3]}"]["start"])
PY
)
echo "  ✓ sysupdate committed both root/verity/signature slot triplets"

ESP_IMAGE="${UPDATED_IMAGE}@@$((ESP_START * SECTOR_SIZE))"
mcopy -i "$ESP_IMAGE" "::/EFI/Linux/echo-os_${EXPECTED_VERSION}+3-0.efi" "$UPDATED_UKI"
"$UKIFY_BIN" inspect "$UPDATED_UKI" \
  --section ".pcrpkey:text@$UPDATED_PCR_PUBLIC_KEY" \
  --section ".pcrsig:text@$UPDATED_PCR_SIGNATURE" >/dev/null
cmp "$ECHO_TPM2_PCR_PUBLIC_KEY" "$UPDATED_PCR_PUBLIC_KEY"
python3 "$VERIFY_PCR_POLICY" \
  "$ECHO_TPM2_PCR_PUBLIC_KEY" \
  "$UPDATED_PCR_PUBLIC_KEY" \
  "$UPDATED_PCR_SIGNATURE"
echo "  ✓ updated UKI carries the authorized signed-PCR11 policy"

modprobe loop
LOOP_DEVICE="$(losetup --find --show --partscan --read-only "$UPDATED_IMAGE")"
udevadm settle --timeout=30
partition_for_label() {
  local label="$1"
  lsblk -nrpo PATH,PARTLABEL "$LOOP_DEVICE" |
    awk -v wanted="$label" '$2 == wanted { print $1 }'
}
UPDATE_ROOT_PARTITION="$(partition_for_label "echo-root-$EXPECTED_VERSION")"
UPDATE_VERITY_PARTITION="$(partition_for_label "echo-root-$EXPECTED_VERSION-verity")"
UPDATE_VERITY_SIG_PARTITION="$(partition_for_label "echo-root-$EXPECTED_VERSION-verity-sig")"
[[ -b "$UPDATE_ROOT_PARTITION" && -b "$UPDATE_VERITY_PARTITION" && \
   -b "$UPDATE_VERITY_SIG_PARTITION" ]] || {
  echo "updated verity partition triplet is not uniquely addressable" >&2
  exit 1
}
python3 "$VERIFY_VERITY" \
  "$UPDATE_ROOT_PARTITION" "$UPDATE_VERITY_PARTITION" \
  "$UPDATE_VERITY_SIG_PARTITION" "$VERITY_CERTIFICATE" \
  --uki "$UPDATED_UKI"
losetup --detach "$LOOP_DEVICE"
LOOP_DEVICE=""
udevadm settle --timeout=30
echo "  ✓ updated UKI roothash, GPT UUIDs, PKCS#7 signature and hash tree agree"

ECHO_IMAGE_VERSION="$EXPECTED_VERSION" \
ECHO_BOOT_LOG_DIR="$LOG_ROOT/good-boot" \
  "$IMAGE_DIR/smoke-boot-image.sh" "$UPDATED_IMAGE"
grep -q 'ECHO_REGION_STATE_READY locale=zh_CN.UTF-8 keymap=us timezone=Asia/Shanghai source=persistent-var' \
  "$LOG_ROOT/good-boot/echo-os-boot.log"
echo "  ✓ signed update reaches a healthy $EXPECTED_VERSION desktop"

ECHO_IMAGE_VERSION="$EXPECTED_VERSION" \
ECHO_LOGIN_PROVISION_MODE=existing \
ECHO_LOGIN_LOG_DIR="$LOG_ROOT/updated-production-login" \
  "$IMAGE_DIR/smoke-login-image.sh" "$UPDATED_IMAGE"
echo "  ✓ updated root restores persistent local identity before its SDDM session"

# Start again from the unblessed post-update image. Destroy only the inactive
# updated root, prove dm-verity rejects it, then let systemd-boot consume its
# three attempts persistently.
cp --reflink=auto --sparse=always "$UPDATED_IMAGE" "$ROLLBACK_IMAGE"
dd if=/dev/zero of="$ROLLBACK_IMAGE" bs="$SECTOR_SIZE" seek="$UPDATE_ROOT_START" \
  count=8192 conv=notrunc status=none
LOOP_DEVICE="$(losetup --find --show --partscan --read-only "$ROLLBACK_IMAGE")"
udevadm settle --timeout=30
UPDATE_ROOT_PARTITION="$(partition_for_label "echo-root-$EXPECTED_VERSION")"
UPDATE_VERITY_PARTITION="$(partition_for_label "echo-root-$EXPECTED_VERSION-verity")"
UPDATE_VERITY_SIG_PARTITION="$(partition_for_label "echo-root-$EXPECTED_VERSION-verity-sig")"
if python3 "$VERIFY_VERITY" \
     "$UPDATE_ROOT_PARTITION" "$UPDATE_VERITY_PARTITION" \
     "$UPDATE_VERITY_SIG_PARTITION" "$VERITY_CERTIFICATE" \
     --uki "$UPDATED_UKI" >"$DM_VERITY_REJECTION_LOG" 2>&1; then
  echo "corrupted root unexpectedly passed dm-verity verification" >&2
  exit 1
fi
grep -q 'veritysetup rejected the verity set' "$DM_VERITY_REJECTION_LOG" || {
  echo "corrupted root failed for a reason other than dm-verity rejection" >&2
  cat "$DM_VERITY_REJECTION_LOG" >&2
  exit 1
}
losetup --detach "$LOOP_DEVICE"
LOOP_DEVICE=""
udevadm settle --timeout=30
echo "  ✓ dm-verity rejects a byte-corrupted authenticated root"
ROLLBACK_ESP="${ROLLBACK_IMAGE}@@$((ESP_START * SECTOR_SIZE))"

for attempt in 1 2 3; do
  if ECHO_IMAGE_VERSION="$EXPECTED_VERSION" \
       ECHO_BOOT_EPHEMERAL=no \
       ECHO_BOOT_TIMEOUT_SECONDS=45 \
       ECHO_BOOT_LOG_DIR="$LOG_ROOT/failed-boot-$attempt" \
       "$IMAGE_DIR/smoke-boot-image.sh" "$ROLLBACK_IMAGE"; then
    echo "dm-verity-rejected update unexpectedly became healthy on attempt $attempt" >&2
    exit 1
  fi
  tries_left=$((3 - attempt))
  mcopy -i "$ROLLBACK_ESP" \
    "::/EFI/Linux/echo-os_${EXPECTED_VERSION}+${tries_left}-${attempt}.efi" \
    "$TEMP_DIR/failed-counter-$attempt.efi"
  echo "  ✓ dm-verity-rejected update failed boot attempt $attempt/3"
done

ECHO_IMAGE_VERSION="$BASE_VERSION" \
ECHO_BOOT_EPHEMERAL=no \
ECHO_BOOT_LOG_DIR="$LOG_ROOT/rollback-boot" \
  "$IMAGE_DIR/smoke-boot-image.sh" "$ROLLBACK_IMAGE"
"$ENCRYPTED_IMAGE" copy-from \
  "$ROLLBACK_IMAGE" "$BASE_VERSION" "$RECOVERY_KEY" \
  /var/lib/NetworkManager/system-connections/echo-ab-persistence.nmconnection \
  "$NETWORK_PROFILE_ROLLBACK_COPY"
cmp "$NETWORK_PROFILE_SOURCE" "$NETWORK_PROFILE_ROLLBACK_COPY"
echo "  ✓ private NetworkManager profile is stable across A/B rollback"
"$ENCRYPTED_IMAGE" copy-from \
  "$ROLLBACK_IMAGE" "$BASE_VERSION" "$RECOVERY_KEY" \
  /var/lib/echo-os/region-state.json "$REGION_STATE_ROLLBACK_COPY"
cmp "$REGION_STATE_SOURCE" "$REGION_STATE_ROLLBACK_COPY"
grep -q 'ECHO_REGION_STATE_READY locale=zh_CN.UTF-8 keymap=us timezone=Asia/Shanghai source=persistent-var' \
  "$LOG_ROOT/rollback-boot/echo-os-boot.log"
echo "  ✓ locale, keymap and timezone are stable across A/B rollback"
"$ENCRYPTED_IMAGE" copy-from \
  "$ROLLBACK_IMAGE" "$BASE_VERSION" "$RECOVERY_KEY" \
  /var/lib/echo-os/oem-complete.json "$OEM_MARKER_ROLLBACK_COPY"
cmp "$OEM_MARKER_SOURCE" "$OEM_MARKER_ROLLBACK_COPY"
"$ENCRYPTED_IMAGE" copy-from \
  "$ROLLBACK_IMAGE" "$BASE_VERSION" "$RECOVERY_KEY" \
  /var/lib/echo-os/local-account.shadow "$ACCOUNT_SHADOW_ROLLBACK_COPY"
cmp "$ACCOUNT_SHADOW_SOURCE" "$ACCOUNT_SHADOW_ROLLBACK_COPY"
echo "  ✓ production OEM identity and password hash are stable across A/B rollback"
ECHO_IMAGE_VERSION="$BASE_VERSION" \
ECHO_LOGIN_PROVISION_MODE=existing \
ECHO_LOGIN_LOG_DIR="$LOG_ROOT/rollback-production-login" \
  "$IMAGE_DIR/smoke-login-image.sh" "$ROLLBACK_IMAGE"
echo "  ✓ rolled-back root reaches the production SDDM session with the same local identity"
GOOD_DERIVED_ID="$(
  sed -n 's/.*ECHO_MACHINE_ID_READY derived=\([0-9a-f]\{32\}\) source=.*/\1/p' \
    "$LOG_ROOT/good-boot/echo-os-boot.log" | tail -1
)"
ROLLBACK_DERIVED_ID="$(
  sed -n 's/.*ECHO_MACHINE_ID_READY derived=\([0-9a-f]\{32\}\) source=.*/\1/p' \
    "$LOG_ROOT/rollback-boot/echo-os-boot.log" | tail -1
)"
[[ -n "$GOOD_DERIVED_ID" && "$GOOD_DERIVED_ID" == "$ROLLBACK_DERIVED_ID" ]] || {
  echo "machine identity changed across updated and rolled-back roots" >&2
  exit 1
}
echo "  ✓ non-reversible machine identity is stable across A/B rollback"
AB_EVIDENCE_MARKER="ECHO_AB_UPDATE_RAW_OK base=$BASE_VERSION update=$EXPECTED_VERSION os=$OS_SOURCE_COMMIT tree=$OS_SOURCE_TREE source-manifest=$OS_SOURCE_MANIFEST_SHA256 manifest=$UPDATE_MANIFEST_SHA256 signature=$UPDATE_SIGNATURE_SHA256 interruption=mid-write-no-uki-recovered esp-space=exhausted-no-uki-recovered update-boot=healthy corruption=rejected attempts=3 rollback=healthy state=machine,account,network,region,flatpak"
printf '%s\n' "$AB_EVIDENCE_MARKER" | tee "$AB_EVIDENCE_LOG"
chmod 0444 "$AB_EVIDENCE_LOG" "$DM_VERITY_REJECTION_LOG" \
  "$ESP_FULL_UPDATE_LOG" "$INTERRUPTED_UPDATE_LOG" "$UPDATE_APPLY_LOG"
