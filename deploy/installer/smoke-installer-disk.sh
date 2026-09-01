#!/usr/bin/env bash
# Exercise the production installer against an ephemeral whole-disk NBD target.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
INSTALLER="$REPO_ROOT/deploy/installer/echo-os-installer"
SOURCE_VERIFIER="$REPO_ROOT/deploy/installer/verify_install_bundle.py"
SOURCE_STREAM_VERIFIER="$REPO_ROOT/deploy/installer/verify_install_stream.py"
SOURCE_KEYRING_VERIFIER="$REPO_ROOT/deploy/installer/verify_public_keyring.py"
SOURCE_DATA_PROTECTION="$REPO_ROOT/deploy/data-protection/echo_data_protection.py"
SOURCE_PCR_PUBLIC_KEY="${ECHO_TPM2_PCR_PUBLIC_KEY:-}"
SOURCE_REPART_DIR="$REPO_ROOT/deploy/installer/repart.d"
MODE="${1:-}"
BUNDLE_INPUT="${2:-}"
KEYRING_INPUT="${3:-${ECHO_INSTALL_KEYRING:-}}"
OUTPUT_INPUT="${4:-}"
SYSTEM_DIR=/usr/lib/echo-os
SMOKE_DIR=""
TARGET_FILE=""
OUTPUT_RAW=""
RECOVERY_KEY_OUTPUT=""
NBD_DEVICE=""
USED_NBD_DEVICE=""
UDEVD_PID=""
UDEVD_LOG=""
ACTIVE_HOLDER_MAPPING=""
STAGED_SYSTEM_DIR=0
STAGED_PCR_PUBLIC_KEY=0
CREATED_NBD_NODES=()

usage() {
  cat >&2 <<EOF
usage:
  $0 plan INSTALL_BUNDLE PUBLIC_KEYRING
  $0 install INSTALL_BUNDLE PUBLIC_KEYRING INSTALLED_RAW_OUTPUT
EOF
}

fail() {
  echo "Echo OS installer disk smoke: $*" >&2
  exit 1
}

cleanup() {
  local exit_code="$?"
  trap - EXIT INT TERM
  if [[ -n "$NBD_DEVICE" ]]; then
    if [[ -n "$ACTIVE_HOLDER_MAPPING" ]]; then
      dmsetup remove "$ACTIVE_HOLDER_MAPPING" >/dev/null 2>&1 || true
      ACTIVE_HOLDER_MAPPING=""
    fi
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
  if [[ "$STAGED_SYSTEM_DIR" -eq 1 ]]; then
    rm -f -- \
      "$SYSTEM_DIR/verify-install-bundle.py" \
      "$SYSTEM_DIR/verify-install-stream.py" \
      "$SYSTEM_DIR/verify-public-keyring.py" \
      "$SYSTEM_DIR/echo-data-protection" \
      "$SYSTEM_DIR/install-keyring.gpg"
    for definition_name in \
      00-esp.conf 10-root.conf 11-root-b.conf \
      20-var.conf 30-swap.conf 40-home.conf; do
      rm -f -- "$SYSTEM_DIR/install-repart.d/$definition_name"
    done
    rmdir -- "$SYSTEM_DIR/install-repart.d" >/dev/null 2>&1 || true
    rmdir -- "$SYSTEM_DIR" >/dev/null 2>&1 || true
  fi
  if [[ "$STAGED_PCR_PUBLIC_KEY" -eq 1 ]]; then
    rm -f -- /usr/lib/systemd/tpm2-pcr-public-key.pem
  fi
  if [[ -n "$TARGET_FILE" ]]; then
    rm -f -- "$TARGET_FILE"
  fi
  if [[ "$exit_code" -ne 0 && -n "$UDEVD_LOG" && -f "$UDEVD_LOG" ]]; then
    echo "Ephemeral udev log follows:" >&2
    tail -120 "$UDEVD_LOG" >&2 || true
  fi
  if [[ -n "$SMOKE_DIR" ]]; then
    rm -f -- "$SMOKE_DIR/partitions.json" "$SMOKE_DIR/udevd.log"
    rmdir -- "$SMOKE_DIR" >/dev/null 2>&1 || true
  fi
  exit "$exit_code"
}
trap cleanup EXIT INT TERM

[[ "$MODE" == plan || "$MODE" == install ]] || { usage; exit 2; }
if [[ "$MODE" == plan ]]; then
  [[ $# -eq 3 && -n "$BUNDLE_INPUT" && -n "$KEYRING_INPUT" ]] || {
    usage
    exit 2
  }
else
  [[ $# -eq 4 && -n "$BUNDLE_INPUT" && -n "$KEYRING_INPUT" && \
     -n "$OUTPUT_INPUT" ]] || {
    usage
    exit 2
  }
fi
[[ "$(uname -s)" == Linux ]] || fail "Linux is required"
[[ "$(id -u)" -eq 0 ]] || fail "root privileges are required"
[[ "${CI:-}" == true || \
   "${ECHO_INSTALLER_DISK_SMOKE:-}" == USE-EPHEMERAL-NBD || \
   ( "$MODE" == plan && \
     "${ECHO_INSTALLER_PLAN_SMOKE:-}" == USE-EPHEMERAL-NBD ) ]] || \
  fail "set ECHO_INSTALLER_DISK_SMOKE=USE-EPHEMERAL-NBD outside CI"
for command_name in \
  awk basename blockdev cat chmod dd dirname dmsetup grep id install kill lsblk \
  mkdir mknod modprobe mktemp mv python3 qemu-nbd realpath rm rmdir \
  sfdisk sha256sum sleep stat tail tr truncate udevadm uname; do
  command -v "$command_name" >/dev/null 2>&1 || \
    fail "test dependency is missing: $command_name"
done
[[ -x "$INSTALLER" && -x "$SOURCE_VERIFIER" && \
   -x "$SOURCE_STREAM_VERIFIER" && -x "$SOURCE_KEYRING_VERIFIER" && \
   -x "$SOURCE_DATA_PROTECTION" && \
   -d "$SOURCE_REPART_DIR" ]] || \
  fail "installer sources are incomplete"
[[ -n "$SOURCE_PCR_PUBLIC_KEY" && -f "$SOURCE_PCR_PUBLIC_KEY" && \
   ! -L "$SOURCE_PCR_PUBLIC_KEY" ]] || \
  fail "ECHO_TPM2_PCR_PUBLIC_KEY must name the CI PCR policy public key"
SOURCE_PCR_PUBLIC_KEY="$(realpath -- "$SOURCE_PCR_PUBLIC_KEY")"
python3 "$SOURCE_DATA_PROTECTION" check-tpm2-public-key \
  "$SOURCE_PCR_PUBLIC_KEY"

[[ -d "$BUNDLE_INPUT" ]] || fail "bundle directory is missing: $BUNDLE_INPUT"
[[ -f "$KEYRING_INPUT" && ! -L "$KEYRING_INPUT" && -s "$KEYRING_INPUT" ]] || \
  fail "public keyring must be a regular non-empty file"
BUNDLE="$(realpath -- "$BUNDLE_INPUT")"
KEYRING="$(realpath -- "$KEYRING_INPUT")"
[[ ! -e "$SYSTEM_DIR" ]] || \
  fail "$SYSTEM_DIR already exists; refusing to replace host installer state"
[[ ! -e /usr/lib/systemd/tpm2-pcr-public-key.pem ]] || \
  fail "host PCR policy path already exists; refusing to replace it"

MACHINE_RECORD="$(python3 "$SOURCE_VERIFIER" --machine "$BUNDLE")"
IFS=$'\t' read -r IMAGE_VERSION _PAYLOAD_NAME UNCOMPRESSED_SIZE UNCOMPRESSED_SHA256 \
  _FACTORY_KEY_NAME _FACTORY_KEY_SHA256 _PCR_POLICY_PUBLIC_KEY_SHA256 \
  <<<"$MACHINE_RECORD"
[[ -n "$IMAGE_VERSION" && "$UNCOMPRESSED_SIZE" =~ ^[1-9][0-9]*$ && \
   "$UNCOMPRESSED_SHA256" =~ ^[0-9a-f]{64}$ ]] || \
  fail "bundle verifier returned an invalid machine record"
MANIFEST_SHA256="$(sha256sum "$BUNDLE/INSTALL-MANIFEST.json" | awk '{print $1}')"
[[ "$MANIFEST_SHA256" =~ ^[0-9a-f]{64}$ ]] || \
  fail "cannot bind the installer manifest identity"

if [[ "$MODE" == install ]]; then
  [[ ! -L "$OUTPUT_INPUT" && ! -e "$OUTPUT_INPUT" ]] || \
    fail "installed output must be a new non-symlink path: $OUTPUT_INPUT"
  OUTPUT_PARENT_INPUT="$(dirname -- "$OUTPUT_INPUT")"
  OUTPUT_BASENAME="$(basename -- "$OUTPUT_INPUT")"
  mkdir -p -- "$OUTPUT_PARENT_INPUT"
  OUTPUT_PARENT="$(realpath -- "$OUTPUT_PARENT_INPUT")"
  OUTPUT_RAW="$OUTPUT_PARENT/$OUTPUT_BASENAME"
  [[ ! -e "$OUTPUT_RAW" ]] || fail "installed output already exists: $OUTPUT_RAW"
  SMOKE_DIR="$(mktemp -d "$OUTPUT_PARENT/.echo-installer-smoke.XXXXXX")"
  if [[ -n "${ECHO_INSTALL_RECOVERY_KEY_OUTPUT:-}" ]]; then
    [[ ! -L "$ECHO_INSTALL_RECOVERY_KEY_OUTPUT" && \
       ! -e "$ECHO_INSTALL_RECOVERY_KEY_OUTPUT" ]] || \
      fail "recovery-key output must be a new non-symlink path"
    RECOVERY_PARENT_INPUT="$(dirname -- "$ECHO_INSTALL_RECOVERY_KEY_OUTPUT")"
    [[ -d "$RECOVERY_PARENT_INPUT" ]] || \
      fail "recovery-key output parent is missing"
    RECOVERY_PARENT="$(realpath -- "$RECOVERY_PARENT_INPUT")"
    RECOVERY_KEY_OUTPUT="$RECOVERY_PARENT/$(basename -- "$ECHO_INSTALL_RECOVERY_KEY_OUTPUT")"
  else
    RECOVERY_KEY_OUTPUT="$OUTPUT_RAW.recovery.key"
    [[ ! -e "$RECOVERY_KEY_OUTPUT" ]] || \
      fail "default recovery-key output already exists: $RECOVERY_KEY_OUTPUT"
  fi
  python3 "$SOURCE_DATA_PROTECTION" generate-recovery-key \
    "$RECOVERY_KEY_OUTPUT" >/dev/null
else
  SMOKE_DIR="$(mktemp -d)"
fi
TARGET_FILE="$SMOKE_DIR/target.raw"

# The production installer intentionally reads only immutable Recovery paths.
# This CI-only harness stages those exact paths, but never overwrites existing
# host state and removes only the known files it created.
install -d -m 0755 "$SYSTEM_DIR/install-repart.d"
STAGED_SYSTEM_DIR=1
install -m 0755 "$SOURCE_VERIFIER" "$SYSTEM_DIR/verify-install-bundle.py"
install -m 0755 "$SOURCE_STREAM_VERIFIER" "$SYSTEM_DIR/verify-install-stream.py"
install -m 0755 "$SOURCE_KEYRING_VERIFIER" "$SYSTEM_DIR/verify-public-keyring.py"
install -m 0755 "$SOURCE_DATA_PROTECTION" "$SYSTEM_DIR/echo-data-protection"
install -m 0644 "$KEYRING" "$SYSTEM_DIR/install-keyring.gpg"
install -m 0644 "$SOURCE_PCR_PUBLIC_KEY" \
  /usr/lib/systemd/tpm2-pcr-public-key.pem
STAGED_PCR_PUBLIC_KEY=1
for definition in "$SOURCE_REPART_DIR"/*.conf; do
  install -m 0644 "$definition" "$SYSTEM_DIR/install-repart.d/${definition##*/}"
done

TARGET_SIZE=$((UNCOMPRESSED_SIZE + 1024 * 1024 * 1024))
truncate -s "$TARGET_SIZE" "$TARGET_FILE"

boundary_digest() {
  {
    dd if="$TARGET_FILE" bs=1M count=1 status=none
    tail -c 1048576 "$TARGET_FILE"
  } | sha256sum | awk '{ print $1 }'
}

BEFORE_STAT="$(stat -c '%s:%b:%Y' "$TARGET_FILE")"
BEFORE_BOUNDARY="$(boundary_digest)"

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

if [[ "$MODE" == install ]] && ! udevadm control --ping >/dev/null 2>&1; then
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
  udevadm control --ping >/dev/null 2>&1 || fail "ephemeral systemd-udevd is not ready"
fi

for device_candidate in /dev/nbd[0-9]*; do
  [[ "$device_candidate" =~ ^/dev/nbd[0-9]+$ && -b "$device_candidate" ]] || continue
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
  fail "NBD test target is not reported as a whole disk"

# Prove that the production plan rejects an unmounted disk which is still in
# use by a device-mapper/LVM/RAID-style holder. The mapping is over only the
# disposable NBD target and is removed before the real plan.
ACTIVE_HOLDER_MAPPING="echo-installer-holder-$$"
TARGET_SECTORS="$(blockdev --getsz "$NBD_DEVICE")"
[[ "$TARGET_SECTORS" =~ ^[1-9][0-9]*$ ]] || \
  fail "unable to size the ephemeral holder test"
dmsetup create "$ACTIVE_HOLDER_MAPPING" \
  --table "0 $TARGET_SECTORS linear $NBD_DEVICE 0"
set +e
HOLDER_PLAN_OUTPUT="$("$INSTALLER" plan "$BUNDLE" "$NBD_DEVICE" 2>&1)"
HOLDER_PLAN_STATUS=$?
set -e
[[ "$HOLDER_PLAN_STATUS" -ne 0 ]] || \
  fail "installer accepted a target with an active block holder"
grep -Fq 'has an active block holder' <<<"$HOLDER_PLAN_OUTPUT" || \
  fail "installer rejected the holder test for the wrong reason"
dmsetup remove "$ACTIVE_HOLDER_MAPPING"
ACTIVE_HOLDER_MAPPING=""
udevadm settle --timeout=30
echo "  holder safety verified: active device-mapper target rejected"

PLAN_OUTPUT="$("$INSTALLER" plan "$BUNDLE" "$NBD_DEVICE")"
printf '%s\n' "$PLAN_OUTPUT"
grep -Fxq "ECHO_INSTALL_BUNDLE_AUTHENTICATED action=plan version=$IMAGE_VERSION manifest=$MANIFEST_SHA256 source=$UNCOMPRESSED_SHA256" \
  <<<"$PLAN_OUTPUT" || fail "plan did not bind its authenticated manifest and source raw"
grep -Eq '^  confirmation: INSTALL-ECHO-OS:nbd[0-9]+:[0-9a-f]{16}$' \
  <<<"$PLAN_OUTPUT" || fail "plan did not emit a bound per-disk confirmation"
grep -Eq '^  device id:    [0-9]+:[0-9]+$' <<<"$PLAN_OUTPUT" || \
  fail "plan did not bind the kernel block-device identity"
grep -Eq '^  wwn:          [A-Za-z0-9._-]+$' <<<"$PLAN_OUTPUT" || \
  fail "plan did not report the durable disk identity"
grep -Eq "^ECHO_INSTALL_PLAN_READY target=$NBD_DEVICE version=$IMAGE_VERSION source=$UNCOMPRESSED_SHA256$" \
  <<<"$PLAN_OUTPUT" || fail "plan readiness marker is missing"
if grep -q '^ECHO_INSTALL_COMPLETE ' <<<"$PLAN_OUTPUT"; then
  fail "read-only plan unexpectedly reported an installation"
fi
CONFIRMATION="$(awk '$1 == "confirmation:" { print $2; exit }' <<<"$PLAN_OUTPUT")"
[[ "$CONFIRMATION" =~ ^INSTALL-ECHO-OS:nbd[0-9]+:[0-9a-f]{16}$ ]] || \
  fail "unable to extract the exact install confirmation"

AFTER_PLAN_STAT="$(stat -c '%s:%b:%Y' "$TARGET_FILE")"
AFTER_PLAN_BOUNDARY="$(boundary_digest)"
[[ "$AFTER_PLAN_STAT" == "$BEFORE_STAT" && \
   "$AFTER_PLAN_BOUNDARY" == "$BEFORE_BOUNDARY" ]] || \
  fail "read-only plan changed the sparse target"

if [[ "$MODE" == plan ]]; then
  qemu-nbd --disconnect "$NBD_DEVICE"
  NBD_DEVICE=""
  udevadm settle --timeout=30
  echo "Echo OS authenticated installer plan smoke OK: version=$IMAGE_VERSION target=ephemeral-nbd writes=0"
  exit 0
fi

INSTALL_OUTPUT="$(
  ECHO_INSTALL_RECOVERY_KEY_FILE="$RECOVERY_KEY_OUTPUT" \
  ECHO_INSTALL_TPM2_DEVICE_KEY="${ECHO_INSTALL_TPM2_DEVICE_KEY:-}" \
    "$INSTALLER" install "$BUNDLE" "$NBD_DEVICE" "$CONFIRMATION"
)"
printf '%s\n' "$INSTALL_OUTPUT"
grep -Fxq "ECHO_INSTALL_BUNDLE_AUTHENTICATED action=install version=$IMAGE_VERSION manifest=$MANIFEST_SHA256 source=$UNCOMPRESSED_SHA256" \
  <<<"$INSTALL_OUTPUT" || fail "install did not bind its authenticated manifest and source raw"
grep -Eq "^ECHO_INSTALL_TARGET_LOCKED target=$NBD_DEVICE device-id=[0-9]+:[0-9]+ identity=stable$" \
  <<<"$INSTALL_OUTPUT" || fail "installer did not prove the locked target identity"
grep -Fxq '  verified: exact uncompressed image bytes by direct post-flush readback' \
  <<<"$INSTALL_OUTPUT" || fail "installer did not prove direct post-flush readback"
grep -Eq "^ECHO_INSTALL_COMPLETE target=$NBD_DEVICE version=$IMAGE_VERSION source=$UNCOMPRESSED_SHA256 home=${NBD_DEVICE}p10 data=luks2-tpm2-signed-pcr11-recovery$" \
  <<<"$INSTALL_OUTPUT" || fail "installer completion marker is missing or inconsistent"

qemu-nbd --disconnect "$NBD_DEVICE"
NBD_DEVICE=""
udevadm settle --timeout=30
PARTITIONS_JSON="$SMOKE_DIR/partitions.json"
sfdisk --json "$TARGET_FILE" >"$PARTITIONS_JSON"
python3 - "$PARTITIONS_JSON" "$IMAGE_VERSION" "$UNCOMPRESSED_SIZE" "$TARGET_SIZE" <<'PY'
import json
import sys

json_path, version, source_size, target_size = sys.argv[1:]
source_size, target_size = int(source_size), int(target_size)
with open(json_path, encoding="utf-8") as stream:
    table = json.load(stream)["partitiontable"]
if table.get("label") != "gpt":
    raise SystemExit("installed target is not GPT")
expected = [
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
]
partitions = table.get("partitions", [])
actual = [item.get("name") for item in partitions]
if actual != expected:
    raise SystemExit(f"installed partition order mismatch: {actual!r}")
sector_size = int(table.get("sectorsize", 0))
if sector_size <= 0:
    raise SystemExit("installed sector size is invalid")
previous_end = 0
for partition in partitions:
    start, size = int(partition.get("start", 0)), int(partition.get("size", 0))
    if start <= 0 or size <= 0 or start < previous_end:
        raise SystemExit(f"installed partition extent is invalid: {partition!r}")
    previous_end = start + size
home = partitions[-1]
home_end_bytes = (int(home["start"]) + int(home["size"])) * sector_size
if home_end_bytes < source_size + 512 * 1024 * 1024:
    raise SystemExit("installed home did not grow beyond the signed source image")
if target_size - home_end_bytes > 16 * 1024 * 1024:
    raise SystemExit("installed home left unexpected trailing disk capacity")
if home_end_bytes > target_size:
    raise SystemExit("installed home extends beyond the target disk")
print(
    "installed GPT verified: "
    f"home_growth_bytes={home_end_bytes - source_size} target_bytes={target_size}"
)
PY

mv -- "$TARGET_FILE" "$OUTPUT_RAW"
TARGET_FILE=""
echo "Echo OS authenticated installer write smoke OK: version=$IMAGE_VERSION output=$OUTPUT_RAW"
echo "Echo OS test recovery key retained privately at: $RECOVERY_KEY_OUTPUT"
