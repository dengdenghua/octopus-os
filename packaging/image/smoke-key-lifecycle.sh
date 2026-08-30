#!/usr/bin/env bash
# Exercise recovery-key rotation and replacement-TPM binding on an NBD disk copy.
set -euo pipefail
umask 077

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
RECOVERY="$REPO_ROOT/deploy/recovery/echo-recovery"
DATA_PROTECTION="$REPO_ROOT/deploy/data-protection/echo_data_protection.py"
REPART_DEFINITIONS="$REPO_ROOT/deploy/recovery/repart.d"
SOURCE_INPUT="${1:-}"
OUTPUT_INPUT="${2:-}"
RECOVERY_KEY_OUTPUT_INPUT="${3:-}"
REPLACEMENT_TPM2_DEVICE_KEY_INPUT="${4:-}"
OLD_RECOVERY_KEY_INPUT="${ECHO_DATA_RECOVERY_KEY:-}"
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
usage: $0 INSTALLED_RAW REBOUND_RAW_OUTPUT NEW_RECOVERY_KEY_OUTPUT REPLACEMENT_TPM2B_PUBLIC
EOF
}

fail() {
  echo "Echo OS key-lifecycle smoke: $*" >&2
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
      */.echo-key-lifecycle.*) rm -rf -- "$SMOKE_DIR" ;;
      *) echo "refusing to remove unexpected smoke directory: $SMOKE_DIR" >&2 ;;
    esac
  fi
  exit "$exit_code"
}
trap cleanup EXIT INT TERM

[[ $# -eq 4 && -n "$SOURCE_INPUT" && -n "$OUTPUT_INPUT" && \
   -n "$RECOVERY_KEY_OUTPUT_INPUT" && \
   -n "$REPLACEMENT_TPM2_DEVICE_KEY_INPUT" ]] || { usage; exit 2; }
[[ "$(uname -s)" == Linux ]] || fail "Linux is required"
[[ "$(id -u)" -eq 0 ]] || fail "root privileges are required"
[[ "${CI:-}" == true || \
   "${ECHO_KEY_LIFECYCLE_SMOKE:-}" == USE-EPHEMERAL-NBD ]] || \
  fail "set ECHO_KEY_LIFECYCLE_SMOKE=USE-EPHEMERAL-NBD outside CI"
for command_name in \
  awk basename blkid chmod cmp cp cryptsetup dirname findmnt grep id install \
  kill lsblk mkdir mknod modprobe mktemp mv openssl python3 qemu-nbd \
  realpath rm sha256sum sleep stat sync tail tr udevadm uname; do
  command -v "$command_name" >/dev/null 2>&1 || \
    fail "test dependency is missing: $command_name"
done
[[ -x "$RECOVERY" && -x "$DATA_PROTECTION" && \
   -d "$REPART_DEFINITIONS" ]] || fail "key-lifecycle sources are incomplete"

[[ -f "$SOURCE_INPUT" && ! -L "$SOURCE_INPUT" ]] || \
  fail "installed source must be a non-symlink regular file"
SOURCE_RAW="$(realpath -- "$SOURCE_INPUT")"
for input_name in \
  OLD_RECOVERY_KEY_INPUT PCR_PUBLIC_KEY_INPUT REPLACEMENT_TPM2_DEVICE_KEY_INPUT; do
  input_path="${!input_name}"
  [[ -n "$input_path" && -f "$input_path" && ! -L "$input_path" ]] || \
    fail "$input_name must name a non-symlink regular file"
done
OLD_RECOVERY_KEY="$(realpath -- "$OLD_RECOVERY_KEY_INPUT")"
PCR_PUBLIC_KEY="$(realpath -- "$PCR_PUBLIC_KEY_INPUT")"
REPLACEMENT_TPM2_DEVICE_KEY="$(realpath -- "$REPLACEMENT_TPM2_DEVICE_KEY_INPUT")"
python3 "$DATA_PROTECTION" check-recovery-key "$OLD_RECOVERY_KEY" >/dev/null
python3 "$DATA_PROTECTION" check-tpm2-public-key "$PCR_PUBLIC_KEY" >/dev/null

[[ ! -e "$OUTPUT_INPUT" && ! -L "$OUTPUT_INPUT" ]] || \
  fail "rebound image output must be a new non-symlink path"
OUTPUT_PARENT_INPUT="$(dirname -- "$OUTPUT_INPUT")"
[[ -d "$OUTPUT_PARENT_INPUT" ]] || fail "rebound image output parent is missing"
OUTPUT_PARENT="$(realpath -- "$OUTPUT_PARENT_INPUT")"
OUTPUT_RAW="$OUTPUT_PARENT/$(basename -- "$OUTPUT_INPUT")"

[[ ! -e "$RECOVERY_KEY_OUTPUT_INPUT" && \
   ! -L "$RECOVERY_KEY_OUTPUT_INPUT" ]] || \
  fail "rotated recovery-key output must be a new non-symlink path"
RECOVERY_KEY_PARENT_INPUT="$(dirname -- "$RECOVERY_KEY_OUTPUT_INPUT")"
[[ -d "$RECOVERY_KEY_PARENT_INPUT" ]] || \
  fail "rotated recovery-key output parent is missing"
RECOVERY_KEY_PARENT="$(realpath -- "$RECOVERY_KEY_PARENT_INPUT")"
RECOVERY_KEY_OUTPUT="$RECOVERY_KEY_PARENT/$(basename -- "$RECOVERY_KEY_OUTPUT_INPUT")"
[[ "$OUTPUT_RAW" != "$SOURCE_RAW" && "$OUTPUT_RAW" != "$RECOVERY_KEY_OUTPUT" && \
   "$RECOVERY_KEY_OUTPUT" != "$SOURCE_RAW" ]] || \
  fail "source image, rebound image and recovery-key paths must be distinct"

SMOKE_DIR="$(mktemp -d "$OUTPUT_PARENT/.echo-key-lifecycle.XXXXXX")"
TARGET_FILE="$SMOKE_DIR/rebound.raw"
NEW_RECOVERY_KEY="$SMOKE_DIR/new-recovery.key"
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
  fail "NBD lifecycle target is not reported as a whole disk"

mapfile -t PARTITION_ROWS < <(
  lsblk -nrpo PATH,TYPE,PARTLABEL "$NBD_DEVICE" | awk '$2 == "part"'
)
[[ "${#PARTITION_ROWS[@]}" -eq 10 ]] || \
  fail "installed target must contain exactly ten partitions"
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
      ;;
    *) IMMUTABLE_DEVICES+=("$partition_path") ;;
  esac
done
[[ "${#DATA_DEVICE_BY_LABEL[@]}" -eq 3 && \
   "${#IMMUTABLE_DEVICES[@]}" -eq 7 ]] || \
  fail "target must contain seven immutable and three mutable partitions"

extract_tpm_token() {
  local device="$1"
  local output="$2"
  local metadata="${output}.metadata"
  cryptsetup luksDump --dump-json-metadata "$device" >"$metadata"
  python3 - "$metadata" "$output" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    metadata = json.load(stream)
tokens = metadata.get("tokens")
if not isinstance(tokens, dict):
    raise SystemExit("LUKS2 metadata has no token table")
tpm_tokens = [
    token
    for token in tokens.values()
    if isinstance(token, dict) and token.get("type") == "systemd-tpm2"
]
if len(tpm_tokens) != 1:
    raise SystemExit("volume must contain exactly one systemd-tpm2 token")
with open(sys.argv[2], "w", encoding="utf-8") as stream:
    json.dump(tpm_tokens[0], stream, sort_keys=True, separators=(",", ":"))
PY
}

capture_payload_hashes() {
  local key_file="$1"
  local output="$2"
  : >"$output"
  for expected_label in echo-var echo-swap echo-home; do
    local partition_path mapping_name filesystem_type
    partition_path="${DATA_DEVICE_BY_LABEL[$expected_label]}"
    mapping_name="echo-key-lifecycle-${expected_label#echo-}"
    [[ ! -e "/dev/mapper/$mapping_name" ]] || \
      fail "lifecycle mapping already exists: $mapping_name"
    cryptsetup open --key-file "$key_file" "$partition_path" "$mapping_name"
    OPEN_MAPPINGS+=("$mapping_name")
    udevadm settle --timeout=30
    filesystem_type="$(blkid -s TYPE -o value "/dev/mapper/$mapping_name")"
    case "$expected_label:$filesystem_type" in
      echo-var:ext4|echo-home:ext4|echo-swap:swap) ;;
      *) fail "$expected_label has unexpected filesystem: $filesystem_type" ;;
    esac
    sha256sum "/dev/mapper/$mapping_name" >>"$output"
    cryptsetup close "$mapping_name"
  done
}

IMMUTABLE_BEFORE="$SMOKE_DIR/immutable-before.sha256"
IMMUTABLE_AFTER="$SMOKE_DIR/immutable-after.sha256"
for partition_path in "${IMMUTABLE_DEVICES[@]}"; do
  sha256sum "$partition_path"
done >"$IMMUTABLE_BEFORE"

PAYLOAD_BEFORE="$SMOKE_DIR/payload-before.sha256"
PAYLOAD_AFTER="$SMOKE_DIR/payload-after.sha256"
capture_payload_hashes "$OLD_RECOVERY_KEY" "$PAYLOAD_BEFORE"
declare -A LUKS_UUID_BEFORE=()
for expected_label in echo-var echo-swap echo-home; do
  partition_path="${DATA_DEVICE_BY_LABEL[$expected_label]}"
  cryptsetup isLuks --type luks2 "$partition_path" || \
    fail "$expected_label is not LUKS2 before key rotation"
  LUKS_UUID_BEFORE[$expected_label]="$(cryptsetup luksUUID "$partition_path")"
  extract_tpm_token \
    "$partition_path" "$SMOKE_DIR/${expected_label}.token-before.json"
done

ROTATION_OUTPUT="$(
  ECHO_RECOVERY_SOURCE_SMOKE=USE-SOURCE-RUNTIME \
  ECHO_RECOVERY_REPART_DEFINITIONS="$REPART_DEFINITIONS" \
  ECHO_RECOVERY_DATA_PROTECTION="$DATA_PROTECTION" \
  ECHO_RECOVERY_PCR_POLICY_PUBLIC_KEY="$PCR_PUBLIC_KEY" \
  ECHO_RECOVERY_ROTATE_OLD_KEY_FILE="$OLD_RECOVERY_KEY" \
  ECHO_RECOVERY_ROTATE_NEW_KEY_FILE="$NEW_RECOVERY_KEY" \
    "$RECOVERY" rotate-recovery-key \
      "$NBD_DEVICE" ROTATE-ECHO-RECOVERY-KEY
)"
printf '%s\n' "$ROTATION_OUTPUT"
grep -Fxq \
  "ECHO_RECOVERY_KEY_ROTATION_COMPLETE target=$NBD_DEVICE old=revoked new=verified tpm2=preserved" \
  <<<"$ROTATION_OUTPUT" || fail "production recovery-key rotation marker is missing"

for expected_label in echo-var echo-swap echo-home; do
  partition_path="${DATA_DEVICE_BY_LABEL[$expected_label]}"
  [[ "$(cryptsetup luksUUID "$partition_path")" == \
     "${LUKS_UUID_BEFORE[$expected_label]}" ]] || \
    fail "$expected_label LUKS identity changed during key rotation"
  if cryptsetup open --test-passphrase --key-file "$OLD_RECOVERY_KEY" \
       "$partition_path" >/dev/null 2>&1; then
    fail "old recovery key still unlocks $expected_label after rotation"
  fi
  cryptsetup open --test-passphrase --key-file "$NEW_RECOVERY_KEY" \
    "$partition_path" >/dev/null 2>&1 || \
    fail "rotated recovery key cannot unlock $expected_label"
  extract_tpm_token \
    "$partition_path" "$SMOKE_DIR/${expected_label}.token-rotated.json"
  cmp "$SMOKE_DIR/${expected_label}.token-before.json" \
    "$SMOKE_DIR/${expected_label}.token-rotated.json" || \
    fail "$expected_label TPM2 token changed during recovery-key rotation"
done

REBIND_OUTPUT="$(
  ECHO_RECOVERY_SOURCE_SMOKE=USE-SOURCE-RUNTIME \
  ECHO_RECOVERY_REPART_DEFINITIONS="$REPART_DEFINITIONS" \
  ECHO_RECOVERY_DATA_PROTECTION="$DATA_PROTECTION" \
  ECHO_RECOVERY_PCR_POLICY_PUBLIC_KEY="$PCR_PUBLIC_KEY" \
  ECHO_RECOVERY_REBIND_KEY_FILE="$NEW_RECOVERY_KEY" \
  ECHO_RECOVERY_REBIND_TPM2_DEVICE_KEY="$REPLACEMENT_TPM2_DEVICE_KEY" \
    "$RECOVERY" rebind-tpm2 "$NBD_DEVICE" REBIND-ECHO-TPM2
)"
printf '%s\n' "$REBIND_OUTPUT"
grep -Fxq \
  "ECHO_TPM2_REBIND_COMPLETE target=$NBD_DEVICE data=luks2-tpm2-signed-pcr11-recovery mode=offline-srk" \
  <<<"$REBIND_OUTPUT" || fail "production replacement-TPM rebind marker is missing"

for expected_label in echo-var echo-swap echo-home; do
  partition_path="${DATA_DEVICE_BY_LABEL[$expected_label]}"
  [[ "$(cryptsetup luksUUID "$partition_path")" == \
     "${LUKS_UUID_BEFORE[$expected_label]}" ]] || \
    fail "$expected_label LUKS identity changed during TPM2 rebind"
  cryptsetup open --test-passphrase --key-file "$NEW_RECOVERY_KEY" \
    "$partition_path" >/dev/null 2>&1 || \
    fail "rotated recovery key failed after TPM2 rebind for $expected_label"
  TOKEN_AFTER="$SMOKE_DIR/${expected_label}.token-rebound.json"
  extract_tpm_token "$partition_path" "$TOKEN_AFTER"
  if cmp -s "$SMOKE_DIR/${expected_label}.token-before.json" "$TOKEN_AFTER"; then
    fail "$expected_label TPM2 token did not rotate to the replacement identity"
  fi
  python3 - "$TOKEN_AFTER" "$PCR_PUBLIC_KEY" "$expected_label" <<'PY'
import base64
import binascii
import json
import sys
from pathlib import Path

token_path, public_key_path, label = sys.argv[1:]
with open(token_path, encoding="utf-8") as stream:
    token = json.load(stream)
if token.get("tpm2-pcrs") != []:
    raise SystemExit(f"{label} unexpectedly binds direct PCR values")
if token.get("tpm2_pubkey_pcrs") != [11]:
    raise SystemExit(f"{label} does not bind signed PCR 11")
try:
    embedded_public_key = base64.b64decode(token["tpm2_pubkey"], validate=True)
    serialized_srk = base64.b64decode(token["tpm2_srk"], validate=True)
except (KeyError, TypeError, ValueError, binascii.Error) as error:
    raise SystemExit(f"{label} TPM2 token has invalid key material") from error
if embedded_public_key != Path(public_key_path).read_bytes():
    raise SystemExit(f"{label} TPM2 token uses the wrong PCR policy key")
if len(serialized_srk) < 32:
    raise SystemExit(f"{label} replacement SRK serialization is unexpectedly short")
keyslots = token.get("keyslots")
if not isinstance(keyslots, list) or len(keyslots) != 1:
    raise SystemExit(f"{label} TPM2 token must reference exactly one keyslot")
PY
done

capture_payload_hashes "$NEW_RECOVERY_KEY" "$PAYLOAD_AFTER"
cmp "$PAYLOAD_BEFORE" "$PAYLOAD_AFTER" || \
  fail "recovery-key rotation or TPM2 rebind changed decrypted device data"
for partition_path in "${IMMUTABLE_DEVICES[@]}"; do
  sha256sum "$partition_path"
done >"$IMMUTABLE_AFTER"
cmp "$IMMUTABLE_BEFORE" "$IMMUTABLE_AFTER" || \
  fail "key lifecycle changed ESP or root-slot bytes"

qemu-nbd --disconnect "$NBD_DEVICE"
NBD_DEVICE=""
udevadm settle --timeout=30
SOURCE_STAT_AFTER="$(stat -c '%d:%i:%s:%b:%Y' "$SOURCE_RAW")"
[[ "$SOURCE_STAT_AFTER" == "$SOURCE_STAT_BEFORE" ]] || \
  fail "key-lifecycle smoke changed the installed source image"

mv -- "$NEW_RECOVERY_KEY" "$RECOVERY_KEY_OUTPUT"
NEW_RECOVERY_KEY=""
PUBLISHED_RECOVERY_KEY=1
if ! mv -- "$TARGET_FILE" "$OUTPUT_RAW"; then
  fail "cannot publish the verified replacement-TPM image"
fi
TARGET_FILE=""
PUBLISHED_RECOVERY_KEY=0
echo "ECHO_KEY_LIFECYCLE_SMOKE_OK output=$OUTPUT_RAW old=revoked new=verified tpm2=replacement-srk data=preserved"
echo "Echo OS rotated test recovery key retained privately at: $RECOVERY_KEY_OUTPUT"
