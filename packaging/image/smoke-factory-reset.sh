#!/usr/bin/env bash
# Exercise the production Recovery factory reset against a disposable NBD copy.
set -euo pipefail
umask 077

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
RECOVERY="$REPO_ROOT/deploy/recovery/echo-recovery"
DATA_PROTECTION="$REPO_ROOT/deploy/data-protection/echo_data_protection.py"
REPART_DEFINITIONS="$REPO_ROOT/deploy/recovery/repart.d"
SOURCE_INPUT="${1:-}"
OUTPUT_INPUT="${2:-}"
RECOVERY_KEY_OUTPUT_INPUT="${3:-}"
OLD_RECOVERY_KEY_INPUT="${ECHO_DATA_RECOVERY_KEY:-}"
FACTORY_KEY_INPUT="${ECHO_FACTORY_DATA_KEY:-}"
TPM2_DEVICE_KEY_INPUT="${ECHO_INSTALL_TPM2_DEVICE_KEY:-}"
PCR_PUBLIC_KEY_INPUT="${ECHO_TPM2_PCR_PUBLIC_KEY:-}"
SMOKE_DIR=""
TARGET_FILE=""
NEW_RECOVERY_KEY=""
OUTPUT_RAW=""
RECOVERY_KEY_OUTPUT=""
NBD_DEVICE=""
USED_NBD_DEVICE=""
UDEVD_PID=""
UDEVD_LOG=""
PUBLISHED_RECOVERY_KEY=0
CREATED_NBD_NODES=()
OPEN_MAPPINGS=()

usage() {
  cat >&2 <<EOF
usage: $0 INSTALLED_RAW RESET_RAW_OUTPUT NEW_RECOVERY_KEY_OUTPUT
EOF
}

fail() {
  echo "Echo OS factory-reset smoke: $*" >&2
  exit 1
}

cleanup() {
  local exit_code="$?"
  trap - EXIT INT TERM
  for mapping_name in "${OPEN_MAPPINGS[@]}"; do
    cryptsetup close "$mapping_name" >/dev/null 2>&1 || true
  done
  if [[ -n "$NBD_DEVICE" ]]; then
    qemu-nbd --disconnect "$NBD_DEVICE" >/dev/null 2>&1 || true
  fi
  if [[ -n "$UDEVD_PID" ]]; then
    kill -TERM "$UDEVD_PID" >/dev/null 2>&1 || true
    wait "$UDEVD_PID" 2>/dev/null || true
  fi
  if [[ -n "$USED_NBD_DEVICE" ]]; then
    for partition_node in "${USED_NBD_DEVICE}"p[0-9]*; do
      [[ -b "$partition_node" ]] && rm -f -- "$partition_node"
    done
  fi
  for device_node in "${CREATED_NBD_NODES[@]}"; do
    rm -f -- "$device_node"
  done
  if [[ "$exit_code" -ne 0 && "$PUBLISHED_RECOVERY_KEY" -eq 1 && \
        -n "$RECOVERY_KEY_OUTPUT" ]]; then
    rm -f -- "$RECOVERY_KEY_OUTPUT"
  fi
  if [[ "$exit_code" -ne 0 && -n "$UDEVD_LOG" && -f "$UDEVD_LOG" ]]; then
    echo "Ephemeral udev log follows:" >&2
    tail -120 "$UDEVD_LOG" >&2 || true
  fi
  if [[ -n "$SMOKE_DIR" && -d "$SMOKE_DIR" ]]; then
    case "$SMOKE_DIR" in
      */.echo-factory-reset.*) rm -rf -- "$SMOKE_DIR" ;;
      *) echo "refusing to remove unexpected smoke directory: $SMOKE_DIR" >&2 ;;
    esac
  fi
  exit "$exit_code"
}
trap cleanup EXIT INT TERM

[[ $# -eq 3 && -n "$SOURCE_INPUT" && -n "$OUTPUT_INPUT" && \
   -n "$RECOVERY_KEY_OUTPUT_INPUT" ]] || { usage; exit 2; }
[[ "$(uname -s)" == Linux ]] || fail "Linux is required"
[[ "$(id -u)" -eq 0 ]] || fail "root privileges are required"
[[ "${CI:-}" == true || \
   "${ECHO_FACTORY_RESET_SMOKE:-}" == USE-EPHEMERAL-NBD ]] || \
  fail "set ECHO_FACTORY_RESET_SMOKE=USE-EPHEMERAL-NBD outside CI"
for command_name in \
  awk basename blkid chmod cmp cp cryptsetup dirname grep id kill lsblk \
  mkdir mknod modprobe mktemp mv python3 qemu-nbd realpath rm sha256sum \
  sleep stat systemd-repart tail udevadm uname; do
  command -v "$command_name" >/dev/null 2>&1 || \
    fail "test dependency is missing: $command_name"
done
[[ -x "$RECOVERY" && -x "$DATA_PROTECTION" && \
   -d "$REPART_DEFINITIONS" ]] || fail "factory-reset sources are incomplete"

[[ -f "$SOURCE_INPUT" && ! -L "$SOURCE_INPUT" ]] || \
  fail "installed source must be a non-symlink regular file"
SOURCE_RAW="$(realpath -- "$SOURCE_INPUT")"
for secret_name in \
  OLD_RECOVERY_KEY_INPUT FACTORY_KEY_INPUT TPM2_DEVICE_KEY_INPUT \
  PCR_PUBLIC_KEY_INPUT; do
  secret_path="${!secret_name}"
  [[ -n "$secret_path" && -f "$secret_path" && ! -L "$secret_path" ]] || \
    fail "$secret_name must name a non-symlink regular file"
done
OLD_RECOVERY_KEY="$(realpath -- "$OLD_RECOVERY_KEY_INPUT")"
FACTORY_KEY="$(realpath -- "$FACTORY_KEY_INPUT")"
TPM2_DEVICE_KEY="$(realpath -- "$TPM2_DEVICE_KEY_INPUT")"
PCR_PUBLIC_KEY="$(realpath -- "$PCR_PUBLIC_KEY_INPUT")"
python3 "$DATA_PROTECTION" check-recovery-key "$OLD_RECOVERY_KEY" >/dev/null
python3 "$DATA_PROTECTION" check-factory-key "$FACTORY_KEY" >/dev/null
python3 "$DATA_PROTECTION" check-tpm2-public-key "$PCR_PUBLIC_KEY" >/dev/null

[[ ! -e "$OUTPUT_INPUT" && ! -L "$OUTPUT_INPUT" ]] || \
  fail "reset image output must be a new non-symlink path"
OUTPUT_PARENT_INPUT="$(dirname -- "$OUTPUT_INPUT")"
[[ -d "$OUTPUT_PARENT_INPUT" ]] || fail "reset image output parent is missing"
OUTPUT_PARENT="$(realpath -- "$OUTPUT_PARENT_INPUT")"
OUTPUT_RAW="$OUTPUT_PARENT/$(basename -- "$OUTPUT_INPUT")"

[[ ! -e "$RECOVERY_KEY_OUTPUT_INPUT" && \
   ! -L "$RECOVERY_KEY_OUTPUT_INPUT" ]] || \
  fail "new recovery-key output must be a new non-symlink path"
RECOVERY_KEY_PARENT_INPUT="$(dirname -- "$RECOVERY_KEY_OUTPUT_INPUT")"
[[ -d "$RECOVERY_KEY_PARENT_INPUT" ]] || \
  fail "new recovery-key output parent is missing"
RECOVERY_KEY_PARENT="$(realpath -- "$RECOVERY_KEY_PARENT_INPUT")"
RECOVERY_KEY_OUTPUT="$RECOVERY_KEY_PARENT/$(basename -- "$RECOVERY_KEY_OUTPUT_INPUT")"
[[ "$OUTPUT_RAW" != "$SOURCE_RAW" && "$OUTPUT_RAW" != "$RECOVERY_KEY_OUTPUT" && \
   "$RECOVERY_KEY_OUTPUT" != "$SOURCE_RAW" ]] || \
  fail "source image, reset image and recovery-key paths must be distinct"

SMOKE_DIR="$(mktemp -d "$OUTPUT_PARENT/.echo-factory-reset.XXXXXX")"
TARGET_FILE="$SMOKE_DIR/reset.raw"
NEW_RECOVERY_KEY="$SMOKE_DIR/recovery.key"
cp --reflink=auto --sparse=always -- "$SOURCE_RAW" "$TARGET_FILE"
python3 "$DATA_PROTECTION" generate-recovery-key "$NEW_RECOVERY_KEY" >/dev/null
SOURCE_STAT_BEFORE="$(stat -c '%d:%i:%s:%b:%Y' "$SOURCE_RAW")"

modprobe nbd max_part=16
for sys_candidate in /sys/class/block/nbd[0-9]*; do
  [[ -r "$sys_candidate/dev" ]] || continue
  device_candidate="/dev/${sys_candidate##*/}"
  if [[ ! -e "$device_candidate" ]]; then
    read -r device_major device_minor < <(tr : ' ' <"$sys_candidate/dev")
    [[ "$device_major" =~ ^[0-9]+$ && "$device_minor" =~ ^[0-9]+$ ]] || \
      fail "invalid NBD device number for $sys_candidate"
    mknod "$device_candidate" b "$device_major" "$device_minor"
    chmod 0600 "$device_candidate"
    CREATED_NBD_NODES+=("$device_candidate")
  fi
done

if ! udevadm control --ping >/dev/null 2>&1; then
  if command -v systemd-udevd >/dev/null 2>&1; then
    UDEVD_BIN="$(command -v systemd-udevd)"
  elif [[ -x /usr/lib/systemd/systemd-udevd ]]; then
    UDEVD_BIN=/usr/lib/systemd/systemd-udevd
  elif [[ -x /lib/systemd/systemd-udevd ]]; then
    UDEVD_BIN=/lib/systemd/systemd-udevd
  else
    fail "systemd-udevd is required for partition device nodes"
  fi
  mkdir -p /run/udev
  UDEVD_LOG="$SMOKE_DIR/udevd.log"
  "$UDEVD_BIN" --resolve-names=never --children-max=4 >"$UDEVD_LOG" 2>&1 &
  UDEVD_PID=$!
  for _attempt in {1..30}; do
    udevadm control --ping >/dev/null 2>&1 && break
    kill -0 "$UDEVD_PID" 2>/dev/null || fail "ephemeral systemd-udevd exited"
    sleep 0.1
  done
  udevadm control --ping >/dev/null 2>&1 || \
    fail "ephemeral systemd-udevd is not ready"
fi

for device_candidate in /dev/nbd[0-9]*; do
  [[ "$device_candidate" =~ ^/dev/nbd[0-9]+$ && \
     -b "$device_candidate" ]] || continue
  if qemu-nbd \
       --connect="$device_candidate" \
       --format=raw \
       --discard=unmap \
       --detect-zeroes=unmap \
       "$TARGET_FILE" 2>/dev/null; then
    NBD_DEVICE="$device_candidate"
    USED_NBD_DEVICE="$device_candidate"
    break
  fi
done
[[ -n "$NBD_DEVICE" ]] || fail "no free NBD whole-disk device is available"
udevadm settle --timeout=30
[[ "$(lsblk -dnro TYPE "$NBD_DEVICE")" == disk ]] || \
  fail "NBD reset target is not reported as a whole disk"

mapfile -t PARTITION_ROWS < <(
  lsblk -nrpo PATH,TYPE,PARTLABEL "$NBD_DEVICE" | awk '$2 == "part"'
)
[[ "${#PARTITION_ROWS[@]}" -eq 10 ]] || \
  fail "installed target must contain exactly ten partitions"
DATA_DEVICES=()
IMMUTABLE_DEVICES=()
declare -A DATA_DEVICE_BY_LABEL=()
for row in "${PARTITION_ROWS[@]}"; do
  read -r partition_path partition_type partition_label <<<"$row"
  [[ "$partition_type" == part ]] || fail "unexpected non-partition row"
  case "$partition_label" in
    echo-var|echo-swap|echo-home)
      [[ -z "${DATA_DEVICE_BY_LABEL[$partition_label]:-}" ]] || \
        fail "duplicate mutable partition label: $partition_label"
      DATA_DEVICE_BY_LABEL[$partition_label]="$partition_path"
      DATA_DEVICES+=("$partition_path")
      ;;
    *) IMMUTABLE_DEVICES+=("$partition_path") ;;
  esac
done
[[ "${#DATA_DEVICES[@]}" -eq 3 && "${#IMMUTABLE_DEVICES[@]}" -eq 7 ]] || \
  fail "target must contain seven immutable and three mutable partitions"
for expected_label in echo-var echo-swap echo-home; do
  [[ -n "${DATA_DEVICE_BY_LABEL[$expected_label]:-}" ]] || \
    fail "mutable partition is missing: $expected_label"
done

IMMUTABLE_BEFORE="$SMOKE_DIR/immutable-before.sha256"
IMMUTABLE_AFTER="$SMOKE_DIR/immutable-after.sha256"
for partition_path in "${IMMUTABLE_DEVICES[@]}"; do
  sha256sum "$partition_path"
done >"$IMMUTABLE_BEFORE"
declare -A OLD_LUKS_UUID=()
for expected_label in echo-var echo-swap echo-home; do
  partition_path="${DATA_DEVICE_BY_LABEL[$expected_label]}"
  cryptsetup isLuks --type luks2 "$partition_path" || \
    fail "$expected_label was not LUKS2 before reset"
  cryptsetup open --test-passphrase --key-file "$OLD_RECOVERY_KEY" \
    "$partition_path" >/dev/null 2>&1 || \
    fail "old recovery key did not unlock $expected_label before reset"
  if cryptsetup open --test-passphrase --key-file "$FACTORY_KEY" \
       "$partition_path" >/dev/null 2>&1; then
    fail "factory key still unlocked $expected_label before reset"
  fi
  OLD_LUKS_UUID[$expected_label]="$(cryptsetup luksUUID "$partition_path")"
  [[ -n "${OLD_LUKS_UUID[$expected_label]}" ]] || \
    fail "$expected_label had no LUKS UUID before reset"
done

RESET_OUTPUT="$(
  ECHO_RECOVERY_SOURCE_SMOKE=USE-SOURCE-RUNTIME \
  ECHO_RECOVERY_REPART_DEFINITIONS="$REPART_DEFINITIONS" \
  ECHO_RECOVERY_DATA_PROTECTION="$DATA_PROTECTION" \
  ECHO_RECOVERY_PCR_POLICY_PUBLIC_KEY="$PCR_PUBLIC_KEY" \
  ECHO_FACTORY_RESET_RECOVERY_KEY_FILE="$NEW_RECOVERY_KEY" \
  ECHO_FACTORY_RESET_TPM2_DEVICE_KEY="$TPM2_DEVICE_KEY" \
    "$RECOVERY" factory-reset "$NBD_DEVICE" ERASE-ECHO-DATA
)"
printf '%s\n' "$RESET_OUTPUT"
grep -Fxq \
  "ECHO_FACTORY_RESET_COMPLETE target=$NBD_DEVICE data=luks2-tpm2-signed-pcr11-recovery" \
  <<<"$RESET_OUTPUT" || fail "production factory-reset completion marker is missing"
udevadm settle --timeout=30

for expected_label in echo-var echo-swap echo-home; do
  partition_path="${DATA_DEVICE_BY_LABEL[$expected_label]}"
  cryptsetup isLuks --type luks2 "$partition_path" || \
    fail "$expected_label is not LUKS2 after reset"
  NEW_LUKS_UUID="$(cryptsetup luksUUID "$partition_path")"
  [[ -n "$NEW_LUKS_UUID" && \
     "$NEW_LUKS_UUID" != "${OLD_LUKS_UUID[$expected_label]}" ]] || \
    fail "$expected_label LUKS identity did not rotate"
  cryptsetup open --test-passphrase --key-file "$NEW_RECOVERY_KEY" \
    "$partition_path" >/dev/null 2>&1 || \
    fail "new recovery key cannot unlock $expected_label"
  if cryptsetup open --test-passphrase --key-file "$OLD_RECOVERY_KEY" \
       "$partition_path" >/dev/null 2>&1; then
    fail "old recovery key still unlocks $expected_label"
  fi
  if cryptsetup open --test-passphrase --key-file "$FACTORY_KEY" \
       "$partition_path" >/dev/null 2>&1; then
    fail "factory key unlocks $expected_label after reset"
  fi

  TOKEN_JSON="$SMOKE_DIR/${expected_label}.tokens.json"
  cryptsetup luksDump --dump-json-metadata "$partition_path" >"$TOKEN_JSON"
  python3 - "$TOKEN_JSON" "$expected_label" "$PCR_PUBLIC_KEY" <<'PY'
import base64
import binascii
import json
import sys
from pathlib import Path

path, label, release_public_key_path = sys.argv[1:]
with open(path, encoding="utf-8") as stream:
    metadata = json.load(stream)
tokens = metadata.get("tokens")
if not isinstance(tokens, dict):
    raise SystemExit(f"{label} has no LUKS2 token table")
tpm_tokens = [
    token
    for token in tokens.values()
    if isinstance(token, dict) and token.get("type") == "systemd-tpm2"
]
if len(tpm_tokens) != 1:
    raise SystemExit(f"{label} must contain exactly one systemd-tpm2 token")
token = tpm_tokens[0]
if token.get("tpm2-pcrs") != []:
    raise SystemExit(f"{label} unexpectedly binds direct PCR values")
if token.get("tpm2_pubkey_pcrs") != [11]:
    raise SystemExit(f"{label} does not bind the signed PCR 11 policy")
encoded_public_key = token.get("tpm2_pubkey")
if not isinstance(encoded_public_key, str) or not encoded_public_key:
    raise SystemExit(f"{label} has no signed-PCR public key in its TPM2 token")
try:
    embedded_public_key = base64.b64decode(encoded_public_key, validate=True)
except (ValueError, binascii.Error) as error:
    raise SystemExit(f"{label} has an invalid base64 signed-PCR key") from error
if embedded_public_key != Path(release_public_key_path).read_bytes():
    raise SystemExit(f"{label} TPM2 token uses the wrong signed-PCR public key")
encoded_srk = token.get("tpm2_srk")
if not isinstance(encoded_srk, str) or not encoded_srk:
    raise SystemExit(f"{label} has no serialized per-device SRK in its TPM2 token")
try:
    serialized_srk = base64.b64decode(encoded_srk, validate=True)
except (ValueError, binascii.Error) as error:
    raise SystemExit(f"{label} has invalid base64 per-device SRK data") from error
if len(serialized_srk) < 32:
    raise SystemExit(f"{label} serialized per-device SRK is unexpectedly short")
keyslots = token.get("keyslots")
if not isinstance(keyslots, list) or len(keyslots) != 1:
    raise SystemExit(f"{label} TPM2 token must reference exactly one keyslot")
PY

  mapping_name="echo-factory-smoke-${expected_label#echo-}"
  cryptsetup open --key-file "$NEW_RECOVERY_KEY" \
    "$partition_path" "$mapping_name"
  OPEN_MAPPINGS+=("$mapping_name")
  udevadm settle --timeout=30
  FILESYSTEM_TYPE="$(blkid -s TYPE -o value "/dev/mapper/$mapping_name")"
  case "$expected_label:$FILESYSTEM_TYPE" in
    echo-var:ext4|echo-home:ext4|echo-swap:swap) ;;
    *) fail "$expected_label has unexpected reset filesystem: $FILESYSTEM_TYPE" ;;
  esac
  cryptsetup close "$mapping_name"
done

for partition_path in "${IMMUTABLE_DEVICES[@]}"; do
  sha256sum "$partition_path"
done >"$IMMUTABLE_AFTER"
cmp "$IMMUTABLE_BEFORE" "$IMMUTABLE_AFTER" || \
  fail "factory reset changed ESP or root-slot bytes"

qemu-nbd --disconnect "$NBD_DEVICE"
NBD_DEVICE=""
udevadm settle --timeout=30
SOURCE_STAT_AFTER="$(stat -c '%d:%i:%s:%b:%Y' "$SOURCE_RAW")"
[[ "$SOURCE_STAT_AFTER" == "$SOURCE_STAT_BEFORE" ]] || \
  fail "factory-reset smoke changed the installed source image"

mv -- "$NEW_RECOVERY_KEY" "$RECOVERY_KEY_OUTPUT"
NEW_RECOVERY_KEY=""
PUBLISHED_RECOVERY_KEY=1
if ! mv -- "$TARGET_FILE" "$OUTPUT_RAW"; then
  fail "cannot publish the verified reset image"
fi
TARGET_FILE=""
PUBLISHED_RECOVERY_KEY=0
echo "ECHO_FACTORY_RESET_SMOKE_OK output=$OUTPUT_RAW volumes=var,swap,home old-recovery=revoked factory=absent tpm2=offline-srk"
echo "Echo OS test factory-reset recovery key retained privately at: $RECOVERY_KEY_OUTPUT"
