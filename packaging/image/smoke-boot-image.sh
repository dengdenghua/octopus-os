#!/usr/bin/env bash
# Cold-boot the generated UEFI image and wait for native Shell health markers.
set -euo pipefail
umask 077

IMAGE_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=packaging/image/secure-boot-options.sh
source "$IMAGE_DIR/secure-boot-options.sh"
SWTPM_HARNESS="$IMAGE_DIR/../../deploy/data-protection/echo-swtpm-ci"
IMAGE_PATH="${1:-}"
BOOT_TIMEOUT_SECONDS="${ECHO_BOOT_TIMEOUT_SECONDS:-480}"
BOOT_EPHEMERAL="${ECHO_BOOT_EPHEMERAL:-yes}"
BOOT_TARGET="${ECHO_BOOT_TARGET:-desktop}"
BOOT_CI_SESSION="${ECHO_BOOT_CI_SESSION:-yes}"
BOOT_OEM_CREDENTIAL_FILE="${ECHO_BOOT_OEM_CREDENTIAL_FILE:-}"
EXPECTED_AGENT_RECOVERY_COUNT="${ECHO_EXPECT_AGENT_RECOVERY_COUNT:-}"
BOOT_EXTRA_DISK_PATH="${ECHO_BOOT_EXTRA_DISK_PATH:-}"
QMP_KEY_HELPER="$IMAGE_DIR/send-sddm-screen-reader-key.py"
IMAGE_VERSION="${ECHO_IMAGE_VERSION:-$(tr -d '[:space:]' <"$IMAGE_DIR/mkosi.version")}"
[[ "$IMAGE_VERSION" =~ ^[0-9A-Za-z][0-9A-Za-z.+:~_-]*$ ]] || {
  echo "invalid mkosi.version: $IMAGE_VERSION" >&2
  exit 2
}
[[ -n "${ECHO_OS_SOURCE_MANIFEST:-}" && \
   -f "$ECHO_OS_SOURCE_MANIFEST" && ! -L "$ECHO_OS_SOURCE_MANIFEST" ]] || {
  echo "ECHO_OS_SOURCE_MANIFEST is required to authenticate boot provenance" >&2
  exit 1
}
SOURCE_RECORD="$(python3 "$IMAGE_DIR/os_source_identity.py" verify \
  --manifest "$ECHO_OS_SOURCE_MANIFEST" --machine)"
IFS=$'\t' read -r OS_SOURCE_REPOSITORY OS_SOURCE_COMMIT \
  OS_SOURCE_TREE OS_SOURCE_MANIFEST_SHA256 <<<"$SOURCE_RECORD"
[[ -n "$OS_SOURCE_REPOSITORY" && "$OS_SOURCE_COMMIT" =~ ^[0-9a-f]{40}$ && \
   "$OS_SOURCE_TREE" =~ ^[0-9a-f]{40}$ && \
   "$OS_SOURCE_MANIFEST_SHA256" =~ ^[0-9a-f]{64}$ ]] || {
  echo "OS source identity verifier returned an invalid boot record" >&2
  exit 1
}
CONFIGURED_IMAGE_PATH="$IMAGE_DIR/mkosi.output/echo-os_${IMAGE_VERSION}.raw"

[[ "$(uname -s)" == "Linux" ]] || {
  echo "QEMU boot smoke requires Linux" >&2
  exit 1
}
for command_name in basename chmod grep id mkosi mktemp python3 realpath setsid stat; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "boot smoke dependency missing: $command_name" >&2
    exit 1
  }
done
[[ "$BOOT_TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]] || {
  echo "ECHO_BOOT_TIMEOUT_SECONDS must be a positive integer" >&2
  exit 2
}
[[ "$BOOT_EPHEMERAL" == "yes" || "$BOOT_EPHEMERAL" == "no" ]] || {
  echo "ECHO_BOOT_EPHEMERAL must be yes or no" >&2
  exit 2
}
[[ "$BOOT_TARGET" == "desktop" || "$BOOT_TARGET" == "login" || \
   "$BOOT_TARGET" == "oem-login" || \
   "$BOOT_TARGET" == "wayland-login" || "$BOOT_TARGET" == "greeter" || \
   "$BOOT_TARGET" == "recovery" || "$BOOT_TARGET" == "backup" ]] || {
  echo "ECHO_BOOT_TARGET must be desktop, login, oem-login, wayland-login, greeter, recovery or backup" >&2
  exit 2
}
[[ "$BOOT_CI_SESSION" == "yes" || "$BOOT_CI_SESSION" == "no" ]] || {
  echo "ECHO_BOOT_CI_SESSION must be yes or no" >&2
  exit 2
}
[[ -z "$EXPECTED_AGENT_RECOVERY_COUNT" || \
   "$EXPECTED_AGENT_RECOVERY_COUNT" =~ ^(0|[1-9][0-9]*)$ ]] || {
  echo "ECHO_EXPECT_AGENT_RECOVERY_COUNT must be a non-negative integer" >&2
  exit 2
}
configure_echo_secure_boot
CREDENTIAL_ARGS=()
if [[ "$BOOT_CI_SESSION" == "yes" ]]; then
  [[ -z "$BOOT_OEM_CREDENTIAL_FILE" ]] || {
    echo "direct CI and OEM provisioning credentials are mutually exclusive" >&2
    exit 2
  }
  CREDENTIAL_ARGS=(--credential=echo.os.ci-session=1)
elif [[ -n "$BOOT_OEM_CREDENTIAL_FILE" ]]; then
  [[ -f "$BOOT_OEM_CREDENTIAL_FILE" && ! -L "$BOOT_OEM_CREDENTIAL_FILE" ]] || {
    echo "OEM boot credential must be a regular non-symlink file" >&2
    exit 2
  }
  OEM_CREDENTIAL_FILE="$(realpath "$BOOT_OEM_CREDENTIAL_FILE")"
  [[ "$(basename "$OEM_CREDENTIAL_FILE")" == echo.os.oem ]] || {
    echo "OEM boot credential file must be named echo.os.oem" >&2
    exit 2
  }
  OEM_CREDENTIAL_MODE="$(stat -c '%a' "$OEM_CREDENTIAL_FILE")"
  [[ "$OEM_CREDENTIAL_MODE" =~ ^[0-7]{3,4}$ && \
     $((8#$OEM_CREDENTIAL_MODE & 077)) -eq 0 ]] || {
    echo "OEM boot credential must not be accessible to group or other users" >&2
    exit 2
  }
  CREDENTIAL_ARGS+=(--credential "$OEM_CREDENTIAL_FILE")
fi
if [[ "$BOOT_TARGET" == "greeter" ]]; then
  [[ "$BOOT_CI_SESSION" == "no" && -z "$BOOT_OEM_CREDENTIAL_FILE" && \
     -x "$QMP_KEY_HELPER" ]] || {
    echo "greeter smoke requires a provisioned image, no CI credential and the fixed QMP key helper" >&2
    exit 2
  }
fi
if [[ "$BOOT_TARGET" == "backup" ]]; then
  [[ "$BOOT_CI_SESSION" == "no" && -z "$BOOT_OEM_CREDENTIAL_FILE" && \
     -n "$BOOT_EXTRA_DISK_PATH" ]] || {
    echo "backup smoke requires no login credential and one dedicated extra disk" >&2
    exit 2
  }
fi
if [[ -z "$IMAGE_PATH" ]]; then
  IMAGE_PATH="$CONFIGURED_IMAGE_PATH"
fi
[[ -n "$IMAGE_PATH" && -f "$IMAGE_PATH" ]] || {
  echo "Echo OS raw image not found" >&2
  exit 1
}
IMAGE_PATH="$(realpath "$IMAGE_PATH")"
IMAGE_OUTPUT_DIRECTORY="$(dirname "$IMAGE_PATH")"
IMAGE_OUTPUT_NAME="$(basename "$IMAGE_PATH" .raw)"
EXTRA_DISK_QEMU_ARGS=()
if [[ -n "$BOOT_EXTRA_DISK_PATH" ]]; then
  [[ -f "$BOOT_EXTRA_DISK_PATH" && ! -L "$BOOT_EXTRA_DISK_PATH" ]] || {
    echo "extra disk must be a regular non-symlink file" >&2
    exit 2
  }
  BOOT_EXTRA_DISK_PATH="$(realpath "$BOOT_EXTRA_DISK_PATH")"
  [[ "$BOOT_EXTRA_DISK_PATH" =~ ^/[A-Za-z0-9_./-]+$ ]] || {
    echo "extra disk path contains unsupported QEMU syntax" >&2
    exit 2
  }
  EXTRA_DISK_MODE="$(stat -c '%a' "$BOOT_EXTRA_DISK_PATH")"
  [[ "$EXTRA_DISK_MODE" =~ ^[0-7]{3,4}$ && \
     $((8#$EXTRA_DISK_MODE & 077)) -eq 0 && \
     "$(stat -c '%u' "$BOOT_EXTRA_DISK_PATH")" -eq "$(id -u)" ]] || {
    echo "extra disk must be private and owned by the boot-smoke caller" >&2
    exit 2
  }
  (( $(stat -c '%s' "$BOOT_EXTRA_DISK_PATH") >= 67108864 )) || {
    echo "extra disk is too small" >&2
    exit 2
  }
  EXTRA_DISK_QEMU_ARGS=(
    -drive "file=$BOOT_EXTRA_DISK_PATH,format=raw,if=none,id=echo-backup-disk,cache=none"
    -device "virtio-blk-pci,drive=echo-backup-disk,serial=echo-backup-ci"
  )
fi

if [[ -n "${ECHO_BOOT_LOG_DIR:-}" ]]; then
  LOG_DIR="$ECHO_BOOT_LOG_DIR"
  mkdir -p "$LOG_DIR"
  REMOVE_LOG_DIR=0
else
  LOG_DIR="$(mktemp -d)"
  REMOVE_LOG_DIR=1
fi
LOG_DIR="$(realpath "$LOG_DIR")"
BOOT_LOG="$LOG_DIR/echo-os-boot.log"
QMP_SOCKET=""
QMP_RUNTIME_DIR=""
QMP_QEMU_ARGS=()
if [[ "$BOOT_TARGET" == "greeter" ]]; then
  QMP_RUNTIME_DIR="$(mktemp -d)"
  chmod 0700 "$QMP_RUNTIME_DIR"
  QMP_SOCKET="$QMP_RUNTIME_DIR/qmp.sock"
  [[ "${#QMP_SOCKET}" -le 100 ]] || {
    echo "greeter QMP socket path is too long" >&2
    exit 2
  }
  QMP_QEMU_ARGS=(-qmp "unix:$QMP_SOCKET,server=on,wait=off")
fi
SWTPM_RUNTIME_DIR=""
TPM_MKOSI_ARGS=()
TPM_QEMU_ARGS=()

# shellcheck disable=SC2329 # Invoked indirectly by EXIT/INT/TERM traps.
cleanup() {
  local exit_code="$1"
  trap - EXIT INT TERM
  if [[ -n "${VM_PROCESS_GROUP:-}" ]]; then
    kill -TERM -- "-$VM_PROCESS_GROUP" 2>/dev/null || true
    sleep 1
    kill -KILL -- "-$VM_PROCESS_GROUP" 2>/dev/null || true
    wait "$VM_PROCESS_GROUP" 2>/dev/null || true
  fi
  [[ -z "$QMP_SOCKET" ]] || rm -f -- "$QMP_SOCKET"
  [[ -z "$QMP_RUNTIME_DIR" ]] || rmdir -- "$QMP_RUNTIME_DIR" 2>/dev/null || true
  if [[ -n "$SWTPM_RUNTIME_DIR" ]]; then
    "$SWTPM_HARNESS" stop "$SWTPM_RUNTIME_DIR" >/dev/null 2>&1 || true
    rm -rf -- "$SWTPM_RUNTIME_DIR"
  fi
  if [[ "$exit_code" -ne 0 ]]; then
    echo "Echo OS $BOOT_TARGET cold-boot smoke failed; serial log follows:" >&2
    tail -240 "$BOOT_LOG" >&2 || true
  elif [[ "$REMOVE_LOG_DIR" -eq 1 ]]; then
    rm -rf -- "$LOG_DIR"
  fi
  exit "$exit_code"
}
trap 'cleanup $?' EXIT INT TERM

if [[ -n "${ECHO_SWTPM_STATE_DIR:-}" ]]; then
  [[ -x "$SWTPM_HARNESS" && -d "$ECHO_SWTPM_STATE_DIR" && \
     ! -L "$ECHO_SWTPM_STATE_DIR" ]] || {
    echo "persistent swtpm state or its harness is missing" >&2
    exit 1
  }
  SWTPM_STATE_DIR="$(realpath "$ECHO_SWTPM_STATE_DIR")"
  SWTPM_RUNTIME_DIR="$(mktemp -d /run/echo-swtpm-boot.XXXXXX)"
  chmod 0700 "$SWTPM_RUNTIME_DIR"
  SWTPM_CONTROL_SOCKET="$(
    "$SWTPM_HARNESS" start "$SWTPM_STATE_DIR" "$SWTPM_RUNTIME_DIR"
  )"
  [[ "$SWTPM_CONTROL_SOCKET" =~ ^/[A-Za-z0-9._/-]+$ ]] || {
    echo "swtpm control socket path is unsafe for QEMU" >&2
    exit 1
  }
  # mkosi's TPM=auto would create a second, disposable swtpm and duplicate
  # QEMU's tpm0/chrtpm IDs. The raw arguments below are the sole TPM device.
  TPM_MKOSI_ARGS=(--tpm=no)
  TPM_QEMU_ARGS=(
    -chardev "socket,id=chrtpm,path=$SWTPM_CONTROL_SOCKET"
    -tpmdev "emulator,id=tpm0,chardev=chrtpm"
    -device "tpm-tis,tpmdev=tpm0"
  )
fi

echo "Booting $IMAGE_PATH"
(
  cd "$IMAGE_DIR"
  exec setsid mkosi \
    --image-version "$IMAGE_VERSION" \
    --output-directory "$IMAGE_OUTPUT_DIRECTORY" \
    --output "$IMAGE_OUTPUT_NAME" \
    --ephemeral "$BOOT_EPHEMERAL" \
    "${CREDENTIAL_ARGS[@]}" \
    "${ECHO_MKOSI_SECURE_BOOT_RUNTIME_ARGS[@]}" \
    "${TPM_MKOSI_ARGS[@]}" \
    vm \
    "${TPM_QEMU_ARGS[@]}" \
    "${QMP_QEMU_ARGS[@]}" \
    "${EXTRA_DISK_QEMU_ARGS[@]}"
) >"$BOOT_LOG" 2>&1 &
VM_PROCESS_GROUP=$!

deadline=$((SECONDS + BOOT_TIMEOUT_SECONDS))
while (( SECONDS < deadline )); do
  if [[ "$BOOT_TARGET" == "recovery" ]] && \
     grep -q "ECHO_RECOVERY_READY version=$IMAGE_VERSION os=$OS_SOURCE_COMMIT" "$BOOT_LOG"; then
    echo "  ✓ UEFI/systemd-boot selected the Recovery UKI from the finished ESP"
    echo "  ✓ offline recovery diagnostics reached their read-only readiness marker"
    echo "Echo OS installed-Recovery cold-boot smoke OK"
    exit 0
  fi
  if [[ "$BOOT_TARGET" == "backup" ]] && grep -Eq \
    'ECHO_USER_BACKUP_STAGE_OK repository=[0-9a-f]{16} snapshot=[0-9a-f]{12} wrong-password=rejected disk-full=rejected corruption=rejected restore=staged metadata=acl,xattr,sparse' \
    "$BOOT_LOG"; then
    echo "  ✓ encrypted backup completed on the dedicated virtual disk"
    echo "  ✓ wrong credentials and repository exhaustion both failed closed"
    echo "  ✓ full repository verification rejected deliberate data corruption"
    echo "  ✓ repaired data restored only into the private migration staging tree"
    echo "Echo OS encrypted user-backup cold-boot smoke OK"
    exit 0
  fi
  DESKTOP_READY=0
  WAYLAND_DESKTOP_READY=0
  RENDERER_READY=0
  SYSTEM_CONTROLS_READY=0
  AUTH_AGENT_READY=0
  POWER_MANAGEMENT_READY=0
  NOTIFICATION_SERVICE_READY=0
  INPUT_METHOD_READY=0
  CLIPBOARD_READY=0
  ACCESSIBILITY_READY=0
  SDDM_ACCESSIBILITY_ARMED=0
  SDDM_ACCESSIBILITY_READY=0
  SDDM_SCREEN_READER_STARTED=0
  QMP_SCREEN_READER_KEY_SENT=0
  CRASH_COLLECTION_READY=0
  FIREWALL_READY=0
  REMOVABLE_STORAGE_READY=0
  PRINTING_READY=0
  SCANNING_READY=0
  CORE_APPS_READY=0
  CORE_APPS_SESSION_READY=0
  NATIVE_APP_IPC_READY=0
  WAYLAND_NATIVE_APP_IPC_READY=0
  BOOT_HEALTHY=0
  LOGIN_READY=0
  OEM_PROVISIONED=0
  ACCOUNT_READY=0
  APP_CATALOG_READY=0
  MACHINE_ID_READY=0
  NETWORK_STATE_READY=0
  REGION_STATE_READY=0
  KWIN_BRIDGE_READY=0
  WAYLAND_KWIN_BRIDGE_READY=0
  WAYLAND_KWIN_GLASS_EFFECT_READY=0
  LOCK_SERVICE_READY=0
  WAYLAND_LOCK_SERVICE_READY=0
  LOCK_SCREEN_LAUNCHED=0
  AGENT_READY=0
  grep -Eq 'ECHO_DESKTOP_READY provider=ewmh-x11 window=0x[0-9A-Fa-f]+ auth=ready power=ready notifications=ready input=ready clipboard=ready accessibility=ready' "$BOOT_LOG" && DESKTOP_READY=1
  grep -q 'ECHO_DESKTOP_READY provider=kwin-wayland renderer=ready lock=kscreenlocker auth=ready power=ready notifications=ready input=ready clipboard=ready accessibility=ready' "$BOOT_LOG" && WAYLAND_DESKTOP_READY=1
  grep -q 'ECHO_RENDERER_READY' "$BOOT_LOG" && RENDERER_READY=1
  grep -Eq 'ECHO_SYSTEM_CONTROLS_READY provider=linux-native bridge=ready wifi=ready bluetooth=ready audio=ready display=ready battery=(present|absent)' "$BOOT_LOG" && SYSTEM_CONTROLS_READY=1
  grep -Eq 'ECHO_AUTH_AGENT_READY provider=polkit-kde session=(x11|wayland)' "$BOOT_LOG" && AUTH_AGENT_READY=1
  grep -Eq 'ECHO_POWER_MANAGEMENT_READY provider=powerdevil upower=ready profiles=ready session=(x11|wayland)' "$BOOT_LOG" && POWER_MANAGEMENT_READY=1
  grep -Eq 'ECHO_NOTIFICATION_SERVICE_READY provider=echo-native dbus=ready socket=private session=(x11|wayland)' "$BOOT_LOG" && NOTIFICATION_SERVICE_READY=1
  grep -Eq 'ECHO_INPUT_METHOD_READY provider=fcitx5 dbus=ready frontend=(x11|wayland)' "$BOOT_LOG" && INPUT_METHOD_READY=1
  grep -Eq 'ECHO_CLIPBOARD_READY provider=klipper-qml dbus=ready storage=runtime-tmpfs persistence=off session=(x11|wayland)' "$BOOT_LOG" && CLIPBOARD_READY=1
  if grep -Eq 'ECHO_ACCESSIBILITY_READY provider=at-spi2 dbus=ready qt=enabled session=(x11|wayland)' "$BOOT_LOG" && \
     grep -q 'ECHO_ACCESSIBILITY_TREE_READY provider=at-spi2 application=echo' "$BOOT_LOG"; then
    ACCESSIBILITY_READY=1
  fi
  grep -q 'ECHO_SDDM_ACCESSIBILITY_ARMED provider=at-spi2 screen-reader=orca trigger=super-alt-s' "$BOOT_LOG" && SDDM_ACCESSIBILITY_ARMED=1
  grep -q 'ECHO_SDDM_ACCESSIBILITY_READY provider=at-spi2 screen-reader=orca trigger=super-alt-s' "$BOOT_LOG" && SDDM_ACCESSIBILITY_READY=1
  grep -q 'ECHO_SDDM_SCREEN_READER_STARTED provider=orca trigger=super-alt-s' "$BOOT_LOG" && SDDM_SCREEN_READER_STARTED=1
  grep -q 'ECHO_QMP_KEY_SENT chord=super-alt-s' "$BOOT_LOG" && QMP_SCREEN_READER_KEY_SENT=1
  grep -q 'ECHO_CRASH_COLLECTION_READY provider=systemd-coredump storage=encrypted-var max-use=1G keep-free=2G' "$BOOT_LOG" && CRASH_COLLECTION_READY=1
  grep -q 'ECHO_FIREWALL_READY backend=nftables default-zone=echo-public inbound=deny forward=explicit' "$BOOT_LOG" && FIREWALL_READY=1
  grep -q 'ECHO_REMOVABLE_STORAGE_READY provider=udisks2 policy=polkit mount=on-demand filesystems=vfat,exfat,ntfs,ext4,btrfs,xfs portable=mtp' "$BOOT_LOG" && REMOVABLE_STORAGE_READY=1
  grep -q 'ECHO_PRINTING_READY provider=cups transport=local-only auth=polkit driverless=ipp-usb retention=off storage=encrypted-var' "$BOOT_LOG" && PRINTING_READY=1
  grep -q 'ECHO_SCANNING_READY provider=sane frontend=skanpage usb=udev,ipp-usb network=airscan-on-demand sharing=off retention=user-owned' "$BOOT_LOG" && SCANNING_READY=1
  grep -q 'ECHO_CORE_APPS_READY files=dolphin terminal=konsole browser=firefox text=kate documents=okular images=gwenview archives=ark media=haruna capture=spectacle calculator=kcalc defaults=xdg' "$BOOT_LOG" && CORE_APPS_READY=1
  grep -q 'ECHO_CORE_APPS_SESSION_READY session=x11 cases=directory,http,text,pdf,image,archive,audio,terminal,calculator transports=xdg-open,gio-launch windows=native cleanup=closed fixtures=runtime-and-loopback-only' "$BOOT_LOG" && CORE_APPS_SESSION_READY=1
  grep -Fxq 'ECHO_NATIVE_APP_IPC_READY app=org.kde.kcalc path=preload-ipc-gio result=zero-exit cleanup=closed' "$BOOT_LOG" && NATIVE_APP_IPC_READY=1
  grep -Fxq 'ECHO_NATIVE_APP_IPC_READY session=wayland app=org.kde.kcalc path=preload-ipc-gio result=zero-exit cleanup=closed' "$BOOT_LOG" && WAYLAND_NATIVE_APP_IPC_READY=1
  grep -q "ECHO_BOOT_HEALTHY version=$IMAGE_VERSION os=$OS_SOURCE_COMMIT provider=ewmh-x11" "$BOOT_LOG" && BOOT_HEALTHY=1
  grep -q "ECHO_LOGIN_READY version=$IMAGE_VERSION os=$OS_SOURCE_COMMIT provider=sddm-x11 seat=seat0" "$BOOT_LOG" && LOGIN_READY=1
  grep -q 'ECHO_OEM_PROVISIONED account=echo source=system-credential locale=zh_CN.UTF-8 keymap=us timezone=Asia/Shanghai' "$BOOT_LOG" && OEM_PROVISIONED=1
  grep -Eq 'ECHO_ACCOUNT_(RESTORED account=echo source=persistent-var|STATE_READY account=echo source=active-root)' "$BOOT_LOG" && ACCOUNT_READY=1
  grep -q 'ECHO_APP_CATALOG_READY remote=flathub storage=/var/lib/flatpak' "$BOOT_LOG" && APP_CATALOG_READY=1
  grep -Eq 'ECHO_MACHINE_ID_READY derived=[0-9a-f]{32} source=persistent-var' "$BOOT_LOG" && MACHINE_ID_READY=1
  grep -q 'ECHO_NETWORK_STATE_READY storage=/var/lib/NetworkManager/system-connections' "$BOOT_LOG" && NETWORK_STATE_READY=1
  grep -Eq 'ECHO_REGION_STATE_READY locale=[^ ]+ keymap=[^ ]+ timezone=[^ ]+ source=(initialized-root|persistent-var)' "$BOOT_LOG" && REGION_STATE_READY=1
  grep -q 'ECHO_KWIN_COMPOSITOR_BRIDGE_READY provider=kwin-script transport=private-socket' "$BOOT_LOG" && KWIN_BRIDGE_READY=1
  grep -q 'ECHO_KWIN_COMPOSITOR_BRIDGE_READY provider=kwin-wayland transport=private-socket' "$BOOT_LOG" && WAYLAND_KWIN_BRIDGE_READY=1
  grep -q 'ECHO_KWIN_GLASS_EFFECT_READY provider=kwin-wayland-effect region=bounded fallback=webgl' "$BOOT_LOG" && WAYLAND_KWIN_GLASS_EFFECT_READY=1
  grep -q 'ECHO_LOCK_SERVICE_READY provider=xss-lock pam=echo-lock idle=' "$BOOT_LOG" && LOCK_SERVICE_READY=1
  grep -q 'ECHO_LOCK_SERVICE_READY provider=kscreenlocker pam=kde idle=600 resume=locked' "$BOOT_LOG" && WAYLAND_LOCK_SERVICE_READY=1
  grep -q 'ECHO_LOCK_SCREEN_LAUNCHED provider=xsecurelock pam=echo-lock' "$BOOT_LOG" && LOCK_SCREEN_LAUNCHED=1
  AGENT_RECOVERY_COUNT_PATTERN='[0-9]+'
  if [[ -n "$EXPECTED_AGENT_RECOVERY_COUNT" ]]; then
    AGENT_RECOVERY_COUNT_PATTERN="$EXPECTED_AGENT_RECOVERY_COUNT"
  fi
  grep -Eq "ECHO_AGENT_READY source=[0-9a-f]{40} endpoint=http://127\\.0\\.0\\.1:8000 recovery=${AGENT_RECOVERY_COUNT_PATTERN}([[:space:]]|$)" "$BOOT_LOG" && AGENT_READY=1
  if [[ "$BOOT_TARGET" == "greeter" && \
        "$SDDM_ACCESSIBILITY_READY" -eq 1 && \
        "$QMP_SCREEN_READER_KEY_SENT" -eq 0 && -S "$QMP_SOCKET" ]]; then
    "$QMP_KEY_HELPER" "$QMP_SOCKET" >>"$BOOT_LOG" 2>&1 || {
      echo "QEMU could not deliver the fixed SDDM screen-reader shortcut" >&2
      exit 1
    }
    QMP_SCREEN_READER_KEY_SENT=1
  fi
  if [[ "$BOOT_TARGET" == "greeter" && "$LOGIN_READY" -eq 1 && \
        "$ACCOUNT_READY" -eq 1 && "$MACHINE_ID_READY" -eq 1 && \
        "$NETWORK_STATE_READY" -eq 1 && "$REGION_STATE_READY" -eq 1 && \
        "$FIREWALL_READY" -eq 1 && "$REMOVABLE_STORAGE_READY" -eq 1 && \
        "$PRINTING_READY" -eq 1 && "$SCANNING_READY" -eq 1 && \
        "$CORE_APPS_READY" -eq 1 && \
        "$SDDM_ACCESSIBILITY_ARMED" -eq 1 && \
        "$SDDM_ACCESSIBILITY_READY" -eq 1 && \
        "$QMP_SCREEN_READER_KEY_SENT" -eq 1 && \
        "$SDDM_SCREEN_READER_STARTED" -eq 1 ]]; then
    echo "  ✓ production SDDM stopped at its PAM-backed seat0 greeter"
    echo "  ✓ unprivileged helper attached only after the local greeter became ready"
    echo "  ✓ QEMU delivered Super+Alt+S through the virtual keyboard"
    echo "  ✓ the fixed shortcut started the packaged Orca process before login"
    echo "  ✓ UDisks2 and common removable-media formats were ready before login"
    echo "  ✓ private local CUPS and driverless USB printing were ready before login"
    echo "Echo OS SDDM accessibility cold-boot smoke OK"
    exit 0
  fi
  if [[ "$BOOT_TARGET" == "oem-login" && "$OEM_PROVISIONED" -eq 1 && \
        "$LOGIN_READY" -eq 1 && \
        "$APP_CATALOG_READY" -eq 1 && "$MACHINE_ID_READY" -eq 1 && \
        "$NETWORK_STATE_READY" -eq 1 && "$KWIN_BRIDGE_READY" -eq 1 && \
        "$LOCK_SERVICE_READY" -eq 1 && "$AGENT_READY" -eq 1 && \
        "$DESKTOP_READY" -eq 1 && "$RENDERER_READY" -eq 1 && \
        "$SYSTEM_CONTROLS_READY" -eq 1 && "$AUTH_AGENT_READY" -eq 1 && \
        "$POWER_MANAGEMENT_READY" -eq 1 && \
        "$NOTIFICATION_SERVICE_READY" -eq 1 && \
        "$INPUT_METHOD_READY" -eq 1 && \
        "$CLIPBOARD_READY" -eq 1 && \
        "$ACCESSIBILITY_READY" -eq 1 && \
        "$CRASH_COLLECTION_READY" -eq 1 && "$FIREWALL_READY" -eq 1 && \
        "$REMOVABLE_STORAGE_READY" -eq 1 && "$PRINTING_READY" -eq 1 && \
        "$SCANNING_READY" -eq 1 && "$CORE_APPS_READY" -eq 1 ]]; then
    echo "  ✓ VM-only systemd credential exercised the production OEM account path"
    echo "  ✓ OEM locale, keymap and timezone passed the installed system catalogs"
    echo "  ✓ plaintext test password was not embedded in the delivered disk image"
    echo "  ✓ production SDDM started only after first-boot provisioning completed"
    echo "  ✓ SDDM launched the packaged X11 session under the provisioned local user"
    echo "  ✓ PolicyKit authentication agent remained attached to the user session"
    echo "  ✓ PowerDevil, UPower and power profiles became ready in the user session"
    echo "  ✓ Native application notifications reached the Echo session service"
    echo "  ✓ Fcitx5 multilingual input became ready for the X11 session"
    echo "  ✓ Klipper retained a system clipboard only in volatile session storage"
    echo "  ✓ AT-SPI exposed the fixed Echo application marker for assistive technology"
    echo "  ✓ Bounded crash collection is active on encrypted persistent storage"
    echo "  ✓ nftables rejects unsolicited inbound and implicit container forwarding"
    echo "  ✓ Dolphin can request PolicyKit-mediated removable-media mounts through UDisks2"
    echo "  ✓ KDE can administer local CUPS printers through the PolicyKit helper"
    echo "Echo OS first-boot OEM cold-boot smoke OK"
    exit 0
  fi
  if [[ "$BOOT_TARGET" == "login" && "$LOGIN_READY" -eq 1 && \
        "$ACCOUNT_READY" -eq 1 && "$APP_CATALOG_READY" -eq 1 && \
        "$MACHINE_ID_READY" -eq 1 && "$NETWORK_STATE_READY" -eq 1 && \
        "$REGION_STATE_READY" -eq 1 && "$KWIN_BRIDGE_READY" -eq 1 && \
        "$LOCK_SERVICE_READY" -eq 1 && "$AGENT_READY" -eq 1 && \
        "$DESKTOP_READY" -eq 1 && "$RENDERER_READY" -eq 1 && \
        "$SYSTEM_CONTROLS_READY" -eq 1 && "$AUTH_AGENT_READY" -eq 1 && \
        "$POWER_MANAGEMENT_READY" -eq 1 && \
        "$NOTIFICATION_SERVICE_READY" -eq 1 && \
        "$INPUT_METHOD_READY" -eq 1 && \
        "$CLIPBOARD_READY" -eq 1 && \
        "$ACCESSIBILITY_READY" -eq 1 && \
        "$CRASH_COLLECTION_READY" -eq 1 && "$FIREWALL_READY" -eq 1 && \
        "$REMOVABLE_STORAGE_READY" -eq 1 && "$PRINTING_READY" -eq 1 && \
        "$SCANNING_READY" -eq 1 && "$CORE_APPS_READY" -eq 1 ]]; then
    echo "  ✓ persistent local-account state is valid on the selected root"
    echo "  ✓ persistent machine identity was bound before the production session"
    echo "  ✓ private NetworkManager profiles are backed by persistent device state"
    echo "  ✓ locale, keymap and timezone are backed by persistent device state"
    echo "  ✓ KWin published compositor-owned window state through the private bridge"
    echo "  ✓ PAM lock handler is attached to the production graphical session"
    echo "  ✓ pinned sandboxed application catalog provisioned without boot-time download"
    echo "  ✓ production SDDM established its PAM-backed seat0 login path"
    echo "  ✓ SDDM launched the packaged Echo OS session under the local user"
    echo "  ✓ KWin and the packaged Electron renderer became ready"
    echo "  ✓ native Linux hardware-control bridge and its fixed providers became ready"
    echo "  ✓ PolicyKit authentication agent remained attached to the user session"
    echo "  ✓ PowerDevil, UPower and power profiles became ready in the user session"
    echo "  ✓ Native application notifications reached the Echo session service"
    echo "  ✓ Fcitx5 multilingual input became ready for the X11 session"
    echo "  ✓ Klipper retained a system clipboard only in volatile session storage"
    echo "  ✓ AT-SPI exposed the fixed Echo application marker for assistive technology"
    echo "  ✓ Bounded crash collection is active on encrypted persistent storage"
    echo "  ✓ nftables rejects unsolicited inbound and implicit container forwarding"
    echo "  ✓ Dolphin can request PolicyKit-mediated removable-media mounts through UDisks2"
    echo "  ✓ KDE can administer local CUPS printers through the PolicyKit helper"
    echo "Echo OS production-login cold-boot smoke OK"
    exit 0
  fi
  if [[ "$BOOT_TARGET" == "wayland-login" && "$LOGIN_READY" -eq 1 && \
        "$ACCOUNT_READY" -eq 1 && "$APP_CATALOG_READY" -eq 1 && \
        "$MACHINE_ID_READY" -eq 1 && "$NETWORK_STATE_READY" -eq 1 && \
        "$REGION_STATE_READY" -eq 1 && \
        "$WAYLAND_KWIN_BRIDGE_READY" -eq 1 && \
        "$WAYLAND_KWIN_GLASS_EFFECT_READY" -eq 1 && \
        "$WAYLAND_LOCK_SERVICE_READY" -eq 1 && "$AGENT_READY" -eq 1 && \
        "$WAYLAND_DESKTOP_READY" -eq 1 && "$RENDERER_READY" -eq 1 && \
        "$WAYLAND_NATIVE_APP_IPC_READY" -eq 1 && \
        "$SYSTEM_CONTROLS_READY" -eq 1 && "$AUTH_AGENT_READY" -eq 1 && \
        "$POWER_MANAGEMENT_READY" -eq 1 && \
        "$NOTIFICATION_SERVICE_READY" -eq 1 && \
        "$INPUT_METHOD_READY" -eq 1 && \
        "$CLIPBOARD_READY" -eq 1 && \
        "$ACCESSIBILITY_READY" -eq 1 && \
        "$CRASH_COLLECTION_READY" -eq 1 && "$FIREWALL_READY" -eq 1 && \
        "$REMOVABLE_STORAGE_READY" -eq 1 && "$PRINTING_READY" -eq 1 && \
        "$SCANNING_READY" -eq 1 && "$CORE_APPS_READY" -eq 1 ]]; then
    echo "  ✓ disposable SDDM copy selected the packaged Wayland candidate"
    echo "  ✓ KWin acquired the virtual DRM device and retained XWayland compatibility"
    echo "  ✓ KScreenLocker and its system PAM service became session-critical"
    echo "  ✓ compositor UUID bridge and packaged renderer became ready on Wayland"
    echo "  ✓ KWin loaded the bounded native Liquid Glass compositor effect"
    echo "  ✓ packaged Echo preload IPC opened and closed one compositor-observed KCalc window"
    echo "  ✓ native Linux hardware-control bridge and its fixed providers became ready"
    echo "  ✓ PolicyKit authentication agent remained attached to the user session"
    echo "  ✓ PowerDevil, UPower and power profiles became ready in the user session"
    echo "  ✓ Native application notifications reached the Echo session service"
    echo "  ✓ Fcitx5 multilingual input became ready for the Wayland session"
    echo "  ✓ Klipper retained a system clipboard only in volatile session storage"
    echo "  ✓ AT-SPI exposed the fixed Echo application marker for assistive technology"
    echo "  ✓ Bounded crash collection is active on encrypted persistent storage"
    echo "  ✓ nftables rejects unsolicited inbound and implicit container forwarding"
    echo "  ✓ Dolphin can request PolicyKit-mediated removable-media mounts through UDisks2"
    echo "  ✓ KDE can administer local CUPS printers through the PolicyKit helper"
    echo "  ✓ persistent account, machine, network, region and app state survived the candidate session"
    echo "Echo OS Wayland-candidate raw cold-boot smoke OK"
    exit 0
  fi
  if [[ "$BOOT_TARGET" == "desktop" && "$DESKTOP_READY" -eq 1 && \
        "$RENDERER_READY" -eq 1 && "$SYSTEM_CONTROLS_READY" -eq 1 && \
        "$AUTH_AGENT_READY" -eq 1 && "$POWER_MANAGEMENT_READY" -eq 1 && \
        "$NOTIFICATION_SERVICE_READY" -eq 1 && \
        "$INPUT_METHOD_READY" -eq 1 && \
        "$CLIPBOARD_READY" -eq 1 && \
        "$ACCESSIBILITY_READY" -eq 1 && \
        "$CRASH_COLLECTION_READY" -eq 1 && \
        "$FIREWALL_READY" -eq 1 && "$REMOVABLE_STORAGE_READY" -eq 1 && \
        "$PRINTING_READY" -eq 1 && "$SCANNING_READY" -eq 1 && \
        "$CORE_APPS_READY" -eq 1 && \
        "$CORE_APPS_SESSION_READY" -eq 1 && \
        "$NATIVE_APP_IPC_READY" -eq 1 && \
        "$BOOT_HEALTHY" -eq 1 && \
        "$MACHINE_ID_READY" -eq 1 && "$NETWORK_STATE_READY" -eq 1 && \
        "$REGION_STATE_READY" -eq 1 && "$KWIN_BRIDGE_READY" -eq 1 && \
        "$LOCK_SERVICE_READY" -eq 1 && "$AGENT_READY" -eq 1 && \
        "$LOCK_SCREEN_LAUNCHED" -eq 1 ]]; then
    echo "  ✓ UEFI/systemd cold boot reached KWin and Echo Desktop"
    echo "  ✓ persistent machine identity passed its pre-session health gate"
    echo "  ✓ persistent NetworkManager storage passed its pre-session health gate"
    echo "  ✓ persistent locale, keymap and timezone passed their pre-session health gate"
    echo "  ✓ KWin compositor window bridge published its initial UUID snapshot"
    echo "  ✓ logind launched the PAM-backed XSecureLock process"
    echo "  ✓ packaged Electron renderer finished loading"
    echo "  ✓ native Linux hardware-control bridge and its fixed providers became ready"
    echo "  ✓ PolicyKit authentication agent remained attached to the user session"
    echo "  ✓ PowerDevil, UPower and power profiles became ready in the user session"
    echo "  ✓ Native application notifications reached the Echo session service"
    echo "  ✓ Fcitx5 multilingual input became ready for the X11 session"
    echo "  ✓ Klipper retained a system clipboard only in volatile session storage"
    echo "  ✓ AT-SPI exposed the fixed Echo application marker for assistive technology"
    echo "  ✓ Bounded crash collection is active on encrypted persistent storage"
    echo "  ✓ nftables rejects unsolicited inbound and implicit container forwarding"
    echo "  ✓ Dolphin can request PolicyKit-mediated removable-media mounts through UDisks2"
    echo "  ✓ KDE can administer local CUPS printers through the PolicyKit helper"
    echo "  ✓ XDG defaults opened text, PDF, image, archive and audio fixtures in native applications"
    echo "  ✓ packaged Echo preload IPC launched and closed the observed KCalc window through GIO"
    echo "  ✓ image-baked Agent runtime, workbench and Codex source identity became ready"
    echo "  ✓ boot-complete health gate allows systemd-boot to bless this version"
    echo "Echo OS cold-boot smoke OK"
    exit 0
  fi
  if ! kill -0 "$VM_PROCESS_GROUP" 2>/dev/null; then
    wait "$VM_PROCESS_GROUP" || true
    echo "mkosi/QEMU exited before the $BOOT_TARGET target became ready" >&2
    exit 1
  fi
  sleep 1
done

echo "$BOOT_TARGET readiness markers were not observed within ${BOOT_TIMEOUT_SECONDS}s" >&2
exit 1
