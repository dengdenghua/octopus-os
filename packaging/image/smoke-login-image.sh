#!/usr/bin/env bash
# Exercise an allowlisted SDDM session on a temporary image without changing the deliverable.
set -euo pipefail

IMAGE_DIR="$(cd "$(dirname "$0")" && pwd)"
ENCRYPTED_IMAGE="$IMAGE_DIR/../../deploy/data-protection/echo-encrypted-image"
IMAGE_VERSION="${ECHO_IMAGE_VERSION:-$(tr -d '[:space:]' <"$IMAGE_DIR/mkosi.version")}"
SOURCE_IMAGE="${1:-$IMAGE_DIR/mkosi.output/echo-os_${IMAGE_VERSION}.raw}"
LOGIN_SESSION="${ECHO_LOGIN_SESSION:-echo.desktop}"
PROVISION_MODE="${ECHO_LOGIN_PROVISION_MODE:-restore}"
OUTPUT_INPUT="${ECHO_LOGIN_OUTPUT_IMAGE:-}"
case "$LOGIN_SESSION" in
  echo.desktop) BOOT_TARGET=login ;;
  echo-wayland.desktop) BOOT_TARGET=wayland-login ;;
  *)
    echo "unsupported disposable SDDM session: $LOGIN_SESSION" >&2
    exit 2
    ;;
esac
case "$PROVISION_MODE" in
  restore) ;;
  existing) ;;
  oem-credential)
    [[ "$LOGIN_SESSION" == echo.desktop ]] || {
      echo "OEM credential smoke supports only the default production X11 session" >&2
      exit 2
    }
    BOOT_TARGET=oem-login
    ;;
  *)
    echo "ECHO_LOGIN_PROVISION_MODE must be restore, existing or oem-credential" >&2
    exit 2
    ;;
esac

[[ "$(uname -s)" == "Linux" ]] || {
  echo "production-login smoke requires Linux" >&2
  exit 1
}
[[ "$(id -u)" -eq 0 ]] || {
  echo "production-login smoke requires root to modify its disposable disk copy" >&2
  exit 1
}
[[ "$IMAGE_VERSION" =~ ^[0-9A-Za-z][0-9A-Za-z.+:~_-]*$ ]] || {
  echo "invalid image version: $IMAGE_VERSION" >&2
  exit 2
}
for command_name in \
  basename cp dirname grep mktemp mv openssl python3 realpath rm stat; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "production-login smoke dependency missing: $command_name" >&2
    exit 1
  }
done
[[ -x "$ENCRYPTED_IMAGE" ]] || {
  echo "encrypted-image test harness is missing" >&2
  exit 1
}
[[ -n "${ECHO_DATA_RECOVERY_KEY:-}" && \
   -f "$ECHO_DATA_RECOVERY_KEY" && ! -L "$ECHO_DATA_RECOVERY_KEY" ]] || {
  echo "ECHO_DATA_RECOVERY_KEY must name the installed device recovery key" >&2
  exit 1
}
RECOVERY_KEY="$(realpath "$ECHO_DATA_RECOVERY_KEY")"
SOURCE_IMAGE="$(realpath "$SOURCE_IMAGE")"
[[ -f "$SOURCE_IMAGE" ]] || {
  echo "finished Echo OS image not found: $SOURCE_IMAGE" >&2
  exit 1
}

OUTPUT_RAW=""
if [[ -n "$OUTPUT_INPUT" ]]; then
  [[ "$PROVISION_MODE" == oem-credential ]] || {
    echo "only OEM credential mode may publish a provisioned image" >&2
    exit 2
  }
  [[ ! -L "$OUTPUT_INPUT" && ! -e "$OUTPUT_INPUT" ]] || {
    echo "provisioned image output must be a new non-symlink path" >&2
    exit 2
  }
  OUTPUT_PARENT_INPUT="$(dirname "$OUTPUT_INPUT")"
  [[ -d "$OUTPUT_PARENT_INPUT" ]] || {
    echo "provisioned image output parent is missing: $OUTPUT_PARENT_INPUT" >&2
    exit 2
  }
  OUTPUT_PARENT="$(realpath "$OUTPUT_PARENT_INPUT")"
  OUTPUT_RAW="$OUTPUT_PARENT/$(basename "$OUTPUT_INPUT")"
  [[ ! -e "$OUTPUT_RAW" ]] || {
    echo "provisioned image output already exists: $OUTPUT_RAW" >&2
    exit 2
  }
  TEMP_DIR="$(mktemp -d "$OUTPUT_PARENT/.echo-oem-provision.XXXXXX")"
else
  TEMP_DIR="$(mktemp -d)"
fi
BOOT_EPHEMERAL=yes
[[ -z "$OUTPUT_RAW" ]] || BOOT_EPHEMERAL=no
LOGIN_IMAGE="$TEMP_DIR/echo-os-login-test.raw"
OEM_MARKER="$TEMP_DIR/oem-complete.json"
ACCOUNT_SHADOW="$TEMP_DIR/local-account.shadow"
TEST_AUTOLOGIN="$TEMP_DIR/99-echo-ci-autologin.conf"
WAYLAND_NATIVE_APP_IPC_REQUEST="$TEMP_DIR/wayland-native-app-ipc"
OEM_CREDENTIAL="$TEMP_DIR/echo.os.oem"
OEM_CREDENTIAL_INPUT=""
cleanup() { rm -rf -- "$TEMP_DIR"; }
trap cleanup EXIT INT TERM

# Only the disposable test copy receives this autologin file. The artifact
# verifier separately proves that the delivered image has no production
# autologin. OEM mode provisions all identity state through a VM-only systemd
# credential; restore mode injects the older-root state needed for A/B restore.
cp --reflink=auto --sparse=always "$SOURCE_IMAGE" "$LOGIN_IMAGE"
if [[ "$PROVISION_MODE" == restore ]]; then
  printf '%s\n' \
    '{"schema":2,"account":"echo","display_name":"Echo CI","hostname":"echo-os","completed_unix":1,"root_version":"pre-test-root","test_only":true}' \
    >"$OEM_MARKER"
  RANDOM_PASSWORD="$(openssl rand -hex 24)"
  PASSWORD_HASH="$(openssl passwd -6 -stdin <<<"$RANDOM_PASSWORD")"
  unset RANDOM_PASSWORD
  [[ "$PASSWORD_HASH" == \$6\$* ]] || {
    echo "unable to generate a disposable SHA-512 account hash" >&2
    exit 1
  }
  printf '%s\n' "$PASSWORD_HASH" >"$ACCOUNT_SHADOW"
  unset PASSWORD_HASH
  chmod 0600 "$OEM_MARKER" "$ACCOUNT_SHADOW"
elif [[ "$PROVISION_MODE" == oem-credential ]]; then
  umask 077
  OEM_PASSWORD="vM7!$(openssl rand -hex 24)#Qa"
  printf '%s\n' \
    "{\"schema\":1,\"display_name\":\"Echo CI\",\"hostname\":\"echo-oem-ci\",\"password\":\"$OEM_PASSWORD\",\"locale\":\"zh_CN.UTF-8\",\"keymap\":\"us\",\"timezone\":\"Asia/Shanghai\"}" \
    >"$OEM_CREDENTIAL"
  unset OEM_PASSWORD
  chmod 0600 "$OEM_CREDENTIAL"
  OEM_CREDENTIAL_INPUT="$OEM_CREDENTIAL"
fi
printf '%s\n' \
  '[Autologin]' \
  'User=echo' \
  "Session=$LOGIN_SESSION" \
  'Relogin=false' >"$TEST_AUTOLOGIN"
if [[ "$PROVISION_MODE" == restore ]]; then
  "$ENCRYPTED_IMAGE" copy-to "$LOGIN_IMAGE" "$IMAGE_VERSION" "$RECOVERY_KEY" \
    "$OEM_MARKER" /var/lib/echo-os/oem-complete.json
  "$ENCRYPTED_IMAGE" copy-to "$LOGIN_IMAGE" "$IMAGE_VERSION" "$RECOVERY_KEY" \
    "$ACCOUNT_SHADOW" /var/lib/echo-os/local-account.shadow
fi
"$ENCRYPTED_IMAGE" copy-to "$LOGIN_IMAGE" "$IMAGE_VERSION" "$RECOVERY_KEY" \
  "$TEST_AUTOLOGIN" /etc/sddm.conf.d/99-echo-ci-autologin.conf
if [[ "$LOGIN_SESSION" == echo-wayland.desktop ]]; then
  [[ -z "$OUTPUT_RAW" ]] || {
    echo "Wayland IPC smoke request may be added only to a disposable image" >&2
    exit 1
  }
  printf '%s\n' 'schema=1 app=org.kde.kcalc' \
    >"$WAYLAND_NATIVE_APP_IPC_REQUEST"
  # The request contains no secret. It is root-owned and read-only so the SDDM
  # user can validate it but cannot create, replace or modify it.
  chmod 0444 "$WAYLAND_NATIVE_APP_IPC_REQUEST"
  "$ENCRYPTED_IMAGE" copy-to "$LOGIN_IMAGE" "$IMAGE_VERSION" "$RECOVERY_KEY" \
    "$WAYLAND_NATIVE_APP_IPC_REQUEST" /etc/echo-os/wayland-native-app-ipc
fi

ECHO_BOOT_TARGET="$BOOT_TARGET" \
ECHO_BOOT_CI_SESSION=no \
ECHO_BOOT_OEM_CREDENTIAL_FILE="$OEM_CREDENTIAL_INPUT" \
ECHO_BOOT_EPHEMERAL="$BOOT_EPHEMERAL" \
ECHO_BOOT_TIMEOUT_SECONDS="${ECHO_LOGIN_TIMEOUT_SECONDS:-180}" \
ECHO_BOOT_LOG_DIR="${ECHO_LOGIN_LOG_DIR:-$TEMP_DIR/logs}" \
  "$IMAGE_DIR/smoke-boot-image.sh" "$LOGIN_IMAGE"

if [[ -n "$OUTPUT_RAW" ]]; then
  "$ENCRYPTED_IMAGE" remove "$LOGIN_IMAGE" "$IMAGE_VERSION" "$RECOVERY_KEY" \
    /etc/sddm.conf.d/99-echo-ci-autologin.conf
  "$ENCRYPTED_IMAGE" assert-absent \
    "$LOGIN_IMAGE" "$IMAGE_VERSION" "$RECOVERY_KEY" \
    /etc/sddm.conf.d/99-echo-ci-autologin.conf
  "$ENCRYPTED_IMAGE" copy-from \
    "$LOGIN_IMAGE" "$IMAGE_VERSION" "$RECOVERY_KEY" \
    /var/lib/echo-os/oem-complete.json "$OEM_MARKER"
  "$ENCRYPTED_IMAGE" copy-from \
    "$LOGIN_IMAGE" "$IMAGE_VERSION" "$RECOVERY_KEY" \
    /var/lib/echo-os/local-account.shadow "$ACCOUNT_SHADOW"
  python3 - "$OEM_MARKER" "$IMAGE_VERSION" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    marker = json.load(stream)
expected_keys = {
    "schema",
    "account",
    "display_name",
    "hostname",
    "completed_unix",
    "root_version",
}
if set(marker) != expected_keys:
    raise SystemExit(f"provisioned OEM marker fields are invalid: {sorted(marker)}")
if marker.get("schema") != 2 or marker.get("account") != "echo":
    raise SystemExit("provisioned OEM marker identity is invalid")
if marker.get("display_name") != "Echo CI" or marker.get("hostname") != "echo-oem-ci":
    raise SystemExit("provisioned OEM identity values are invalid")
if marker.get("root_version") != sys.argv[2]:
    raise SystemExit("provisioned OEM marker has the wrong root version")
if not isinstance(marker.get("completed_unix"), int) or marker["completed_unix"] <= 0:
    raise SystemExit("provisioned OEM completion timestamp is invalid")
print("provisioned OEM marker verified")
PY
  [[ "$(stat -c '%a' "$OEM_MARKER")" == 600 && \
     "$(stat -c '%a' "$ACCOUNT_SHADOW")" == 600 ]] || {
    echo "provisioned OEM state is not private" >&2
    exit 1
  }
  grep -Eq '^\$[A-Za-z0-9./]+\$[A-Za-z0-9./$=,_-]+$' "$ACCOUNT_SHADOW" || {
    echo "provisioned local password hash is invalid" >&2
    exit 1
  }
  mv -- "$LOGIN_IMAGE" "$OUTPUT_RAW"
  echo "Echo OS provisioned first-use image ready: $OUTPUT_RAW"
fi
