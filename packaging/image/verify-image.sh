#!/usr/bin/env bash
# Verify source-level and built-artifact contracts for the target-C image.
set -euo pipefail

IMAGE_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$IMAGE_DIR/../.." && pwd)"
MODE="${1:---static}"
FAILURES=0

pass() { echo "  ✓ $*"; }
fail() { echo "  ✗ $*" >&2; FAILURES=$((FAILURES + 1)); }

require_file() {
  if [[ -f "$1" ]]; then pass "exists ${1#"$REPO_ROOT"/}"; else fail "missing $1"; fi
}

require_executable() {
  if [[ -x "$1" ]]; then pass "executable ${1#"$REPO_ROOT"/}"; else fail "not executable $1"; fi
}

require_pattern() {
  local file_path="$1"
  local pattern="$2"
  local description="$3"
  if grep -Eq -- "$pattern" "$file_path"; then pass "$description"; else fail "$description"; fi
}

forbid_pattern() {
  local file_path="$1"
  local pattern="$2"
  local description="$3"
  if grep -Eq -- "$pattern" "$file_path"; then fail "$description"; else pass "$description"; fi
}

echo "Echo OS image source contract"
require_file "$IMAGE_DIR/mkosi.conf"
require_file "$IMAGE_DIR/mkosi.version"
require_file "$IMAGE_DIR/mkosi.seed"
require_executable "$IMAGE_DIR/mkosi.build"
require_executable "$IMAGE_DIR/mkosi.postinst.chroot"
require_executable "$IMAGE_DIR/build-image.sh"
require_executable "$IMAGE_DIR/prepare-native-agent-runtime.sh"
require_executable "$IMAGE_DIR/verify-native-shell-package.cjs"
require_file "$IMAGE_DIR/verify-native-shell-package.test.cjs"
require_executable "$IMAGE_DIR/verify-native-agent-runtime.py"
require_pattern "$IMAGE_DIR/.gitignore" '^mkosi\.agent-runtime/$' "generated native Agent runtime cannot enter OS source commits"
require_pattern "$IMAGE_DIR/.gitignore" '^\.mkosi\.agent-runtime\.\*/$' "failed native Agent staging directories cannot enter OS source commits"
require_executable "$IMAGE_DIR/verify-mkosi-summary.py"
require_executable "$IMAGE_DIR/test_verify_mkosi_summary.py"
require_executable "$IMAGE_DIR/smoke-boot-image.sh"
require_executable "$IMAGE_DIR/smoke-agent-recovery-image.sh"
require_executable "$IMAGE_DIR/verify-agent-recovery-fixture.py"
require_executable "$IMAGE_DIR/test_verify_agent_recovery_fixture.py"
require_file "$IMAGE_DIR/agent-recovery-task-runs.json"
require_executable "$IMAGE_DIR/verify-os-image-evidence.py"
require_executable "$IMAGE_DIR/test_verify_os_image_evidence.py"
require_executable "$IMAGE_DIR/verify-linux-image-runner.py"
require_executable "$IMAGE_DIR/test_verify_linux_image_runner.py"
require_executable "$IMAGE_DIR/verify-linux-image-runner-host.py"
require_executable "$IMAGE_DIR/test_verify_linux_image_runner_host.py"
require_executable "$IMAGE_DIR/verify-linux-image-runner-registration.py"
require_executable "$IMAGE_DIR/test_verify_linux_image_runner_registration.py"
require_executable "$IMAGE_DIR/configure-linux-image-runner-host.sh"
require_executable "$IMAGE_DIR/cleanup-linux-image-runner.py"
require_executable "$IMAGE_DIR/test_cleanup_linux_image_runner.py"
require_executable "$IMAGE_DIR/configure-linux-image-runner-hooks.sh"
require_executable "$IMAGE_DIR/runner-host/echo-os-image-runner-job-hook.sh"
require_file "$IMAGE_DIR/runner-host/echo-os-image-runner.modules.conf"
require_file "$IMAGE_DIR/runner-host/echo-os-image-runner.modprobe.conf"
require_executable "$IMAGE_DIR/verify-ab-update-evidence.py"
require_executable "$IMAGE_DIR/test_verify_ab_update_evidence.py"
require_executable "$IMAGE_DIR/interrupt-sysupdate-after-write.py"
require_executable "$IMAGE_DIR/test_interrupt_sysupdate_after_write.py"
require_executable "$IMAGE_DIR/os_source_identity.py"
require_executable "$IMAGE_DIR/test_os_source_identity.py"
require_executable "$REPO_ROOT/deploy/appliance/test_unified_echo_workflow_policy.py"
require_executable "$REPO_ROOT/deploy/appliance/run_public_source_tests.py"
require_file "$REPO_ROOT/appliance/agent_assets.py"
require_file "$REPO_ROOT/tests/appliance/test_agent_assets.py"
require_pattern "$REPO_ROOT/appliance/agent_assets.py" '^_PUBLIC_FIELDS:' "Agent capability adapter exposes only an explicit public field contract"
require_pattern "$REPO_ROOT/tests/appliance/test_agent_assets.py" '^def test_agent_assets_projects_only_bounded_public_fields_and_deduplicates\(' "Agent capability adapter rejects private fields and duplicate entries"
require_executable "$REPO_ROOT/deploy/appliance/protocol_interoperability_lab.py"
require_file "$REPO_ROOT/tests/appliance/test_protocol_interoperability_lab.py"
require_executable "$REPO_ROOT/deploy/appliance/lan_discovery_functional_lab.py"
require_file "$REPO_ROOT/tests/appliance/test_lan_discovery_functional_lab.py"
require_pattern "$REPO_ROOT/deploy/appliance/lan_discovery_functional_lab.py" '^def _verify_local_tool\(' "LAN lab self-verifies candidate-bound executable bytes"
require_pattern "$REPO_ROOT/tests/appliance/test_lan_discovery_functional_lab.py" '^def test_verify_rejects_stale_future_or_cross_window_probe\(' "LAN lab rejects stale and cross-window probe evidence"
require_executable "$REPO_ROOT/deploy/appliance/storage_recovery_lab.py"
require_file "$REPO_ROOT/tests/appliance/test_storage_recovery_lab.py"
require_executable "$REPO_ROOT/deploy/appliance/device_endurance_lab.py"
require_file "$REPO_ROOT/tests/appliance/test_device_endurance_lab.py"
require_executable "$REPO_ROOT/deploy/appliance/nas_data_backup.py"
require_file "$REPO_ROOT/tests/appliance/test_nas_data_backup.py"
require_executable "$REPO_ROOT/deploy/appliance/bare_metal_recovery_lab.py"
require_file "$REPO_ROOT/tests/appliance/test_bare_metal_recovery_lab.py"
require_executable "$REPO_ROOT/deploy/appliance/power_state_recovery_lab.py"
require_file "$REPO_ROOT/tests/appliance/test_power_state_recovery_lab.py"
require_executable "$REPO_ROOT/deploy/appliance/recover-appliance-upgrade.sh"
require_executable "$REPO_ROOT/deploy/appliance/upgrade_transaction.py"
require_file "$REPO_ROOT/tests/appliance/test_upgrade_transaction.py"
require_file "$REPO_ROOT/deploy/appliance/systemd/echo-appliance-upgrade-recovery.service.example"
require_executable "$REPO_ROOT/deploy/appliance/delivery_source_preflight.py"
require_executable "$REPO_ROOT/deploy/appliance/release_candidate_preflight.py"
require_executable "$REPO_ROOT/deploy/appliance/release_evidence_index.py"
require_executable "$REPO_ROOT/deploy/appliance/verify-release-candidate-bundle.sh"
require_executable "$REPO_ROOT/deploy/system-health/echo-os-source-identity"
require_executable "$REPO_ROOT/deploy/system-health/test-echo-os-source-identity.sh"
require_file "$REPO_ROOT/deploy/network-security/firewalld.conf"
require_file "$REPO_ROOT/deploy/network-security/echo-public.xml"
require_executable "$REPO_ROOT/deploy/network-security/echo_firewall_policy.py"
require_executable "$REPO_ROOT/deploy/network-security/test_echo_firewall_policy.py"
require_executable "$REPO_ROOT/deploy/network-security/echo-firewall-health"
require_file "$REPO_ROOT/deploy/network-security/echo-firewall-health.service"
require_executable "$REPO_ROOT/deploy/network-security/test_echo_firewall_health.py"
require_file "$REPO_ROOT/deploy/network-security/README.md"
require_executable "$REPO_ROOT/deploy/removable-storage/echo-removable-storage-health"
require_file "$REPO_ROOT/deploy/removable-storage/echo-removable-storage-health.service"
require_executable "$REPO_ROOT/deploy/removable-storage/test_echo_removable_storage_health.py"
require_file "$REPO_ROOT/deploy/removable-storage/README.md"
require_file "$REPO_ROOT/deploy/printing/cupsd.conf"
require_file "$REPO_ROOT/deploy/printing/ipp-usb.conf"
require_executable "$REPO_ROOT/deploy/printing/echo_printing_policy.py"
require_executable "$REPO_ROOT/deploy/printing/test_echo_printing_policy.py"
require_executable "$REPO_ROOT/deploy/printing/echo-printing-health"
require_file "$REPO_ROOT/deploy/printing/echo-printing-health.service"
require_executable "$REPO_ROOT/deploy/printing/test_echo_printing_health.py"
require_file "$REPO_ROOT/deploy/printing/README.md"
require_file "$REPO_ROOT/deploy/scanning/airscan.conf"
require_executable "$REPO_ROOT/deploy/scanning/echo_scanning_policy.py"
require_executable "$REPO_ROOT/deploy/scanning/test_echo_scanning_policy.py"
require_executable "$REPO_ROOT/deploy/scanning/echo-scanning-health"
require_file "$REPO_ROOT/deploy/scanning/echo-scanning-health.service"
require_executable "$REPO_ROOT/deploy/scanning/test_echo_scanning_health.py"
require_file "$REPO_ROOT/deploy/scanning/README.md"
require_file "$REPO_ROOT/deploy/core-apps/mimeapps.list"
require_executable "$REPO_ROOT/deploy/core-apps/echo_core_apps_policy.py"
require_executable "$REPO_ROOT/deploy/core-apps/test_echo_core_apps_policy.py"
require_executable "$REPO_ROOT/deploy/core-apps/echo-core-apps-health"
require_file "$REPO_ROOT/deploy/core-apps/echo-core-apps-health.service"
require_executable "$REPO_ROOT/deploy/core-apps/test_echo_core_apps_health.py"
require_executable "$REPO_ROOT/deploy/core-apps/echo_core_apps_session_smoke.py"
require_executable "$REPO_ROOT/deploy/core-apps/test_echo_core_apps_session_smoke.py"
require_file "$REPO_ROOT/deploy/core-apps/README.md"
require_executable "$IMAGE_DIR/sign-os-image-evidence.sh"
require_executable "$IMAGE_DIR/verify-os-image-evidence-release.sh"
require_executable "$IMAGE_DIR/smoke-login-image.sh"
require_executable "$IMAGE_DIR/smoke-oem-image.sh"
require_executable "$IMAGE_DIR/smoke-sddm-accessibility-image.sh"
require_executable "$IMAGE_DIR/smoke-user-backup-image.sh"
require_executable "$IMAGE_DIR/echo-user-backup-ci"
require_file "$IMAGE_DIR/echo-user-backup-ci.service"
require_executable "$IMAGE_DIR/send-sddm-screen-reader-key.py"
require_executable "$IMAGE_DIR/test_send_sddm_screen_reader_key.py"
require_executable "$IMAGE_DIR/smoke-recovery-image.sh"
require_executable "$IMAGE_DIR/smoke-factory-reset.sh"
require_executable "$IMAGE_DIR/smoke-key-lifecycle.sh"
require_executable "$IMAGE_DIR/smoke-ab-update.sh"
require_executable "$REPO_ROOT/packaging/recovery/build-recovery.sh"
require_executable "$REPO_ROOT/packaging/recovery/smoke-recovery-uki.sh"
require_executable "$REPO_ROOT/packaging/recovery/install-recovery-uki.sh"
require_executable "$REPO_ROOT/deploy/native-shell/setup-native-shell.sh"
require_file "$IMAGE_DIR/secure-boot-options.sh"
require_executable "$REPO_ROOT/deploy/recovery/echo-recovery"
require_executable "$REPO_ROOT/deploy/recovery/test-echo-recovery-source-identity.sh"
require_executable "$REPO_ROOT/deploy/installer/echo-os-installer"
require_executable "$REPO_ROOT/deploy/installer/create-install-bundle.sh"
require_executable "$REPO_ROOT/deploy/installer/smoke-installer-disk.sh"
require_executable "$REPO_ROOT/deploy/installer/smoke-installer-install.sh"
require_executable "$REPO_ROOT/deploy/installer/smoke-installer-plan.sh"
require_executable "$REPO_ROOT/deploy/installer/verify_install_bundle.py"
require_executable "$REPO_ROOT/deploy/installer/test_verify_install_bundle.py"
require_executable "$REPO_ROOT/deploy/installer/verify_install_stream.py"
require_executable "$REPO_ROOT/deploy/installer/test_verify_install_stream.py"
require_executable "$REPO_ROOT/deploy/installer/verify_public_keyring.py"
require_executable "$REPO_ROOT/deploy/installer/test_verify_public_keyring.py"
require_executable "$REPO_ROOT/deploy/oem/echo_oem_setup.py"
require_executable "$REPO_ROOT/deploy/oem/test_echo_oem_setup.py"
require_executable "$REPO_ROOT/deploy/oem/verify-login-boot.sh"
require_executable "$REPO_ROOT/deploy/oem/test-verify-login-boot.sh"
require_executable "$REPO_ROOT/deploy/oem/echo-sddm-accessibility"
require_executable "$REPO_ROOT/deploy/oem/test_echo_sddm_accessibility.py"
require_executable "$REPO_ROOT/deploy/oem/echo-sddm-xsetup"
require_executable "$REPO_ROOT/deploy/oem/echo-sddm-xstop"
require_file "$REPO_ROOT/deploy/oem/echo-local-account.service"
require_file "$REPO_ROOT/deploy/oem/echo-account-capture.service"
require_file "$REPO_ROOT/deploy/oem/echo-account-capture.path"
require_file "$REPO_ROOT/deploy/agent/echo-agent.service"
require_file "$REPO_ROOT/deploy/agent/echo-agent-health.service"
require_file "$REPO_ROOT/deploy/agent/echo-agent-native.yaml"
require_executable "$REPO_ROOT/deploy/agent/verify-native-agent-health"
require_executable "$REPO_ROOT/deploy/backup/echo-user-backup"
require_executable "$REPO_ROOT/deploy/backup/test_echo_user_backup.py"
require_file "$REPO_ROOT/deploy/backup/echo-user-backup.service"
require_file "$REPO_ROOT/deploy/backup/README.md"
require_executable "$REPO_ROOT/deploy/recovery/echo_restore_transaction.py"
require_executable "$REPO_ROOT/deploy/recovery/test_echo_restore_transaction.py"
require_file "$REPO_ROOT/deploy/backup/echo-restore-transaction-health.service"
require_file "$REPO_ROOT/appliance/native_extension.py"
require_file "$REPO_ROOT/appliance/native_entrypoint.py"
require_executable "$REPO_ROOT/deploy/apps/echo-app-catalog"
require_executable "$REPO_ROOT/deploy/apps/test-echo-app-catalog.sh"
require_file "$REPO_ROOT/deploy/apps/echo-app-catalog.service"
require_file "$REPO_ROOT/deploy/apps/echo-app-store.desktop"
require_file "$REPO_ROOT/deploy/apps/org.kde.discover.desktop"
require_file "$REPO_ROOT/deploy/apps/echo-portals.conf"
require_file "$REPO_ROOT/deploy/apps/flathub.flatpakrepo"
require_executable "$REPO_ROOT/deploy/machine-state/echo-machine-id"
require_executable "$REPO_ROOT/deploy/machine-state/verify-machine-identity.sh"
require_executable "$REPO_ROOT/deploy/machine-state/test-echo-machine-id.sh"
require_file "$REPO_ROOT/deploy/machine-state/echo-machine-identity-health.service"
require_executable "$REPO_ROOT/deploy/machine-state/echo-network-state-prepare"
require_executable "$REPO_ROOT/deploy/machine-state/test-echo-network-state.sh"
require_file "$REPO_ROOT/deploy/machine-state/echo-network-state-prepare.service"
require_file "$REPO_ROOT/deploy/machine-state/20-echo-persistent-connections.conf"
require_file "$REPO_ROOT/deploy/machine-state/NetworkManager.service.d/10-echo-persistent-state.conf"
require_executable "$REPO_ROOT/deploy/machine-state/echo-region-state"
require_executable "$REPO_ROOT/deploy/machine-state/test_echo_region_state.py"
require_file "$REPO_ROOT/deploy/machine-state/echo-region-state-restore.service"
require_file "$REPO_ROOT/deploy/machine-state/echo-region-state-capture.service"
require_file "$REPO_ROOT/deploy/machine-state/echo-region-state-capture.path"
require_file "$REPO_ROOT/deploy/machine-state/locale.gen"
require_executable "$REPO_ROOT/deploy/desktop-session/echo-session-lock"
require_executable "$REPO_ROOT/deploy/desktop-session/echo-screen-locker"
require_executable "$REPO_ROOT/deploy/desktop-session/test-echo-session-lock.sh"
require_executable "$REPO_ROOT/deploy/desktop-session/test-verify-desktop-boot.sh"
require_file "$REPO_ROOT/deploy/desktop-session/echo-lock.pam"
require_executable "$REPO_ROOT/deploy/desktop-session/echo-kwin-window-bridge"
require_executable "$REPO_ROOT/deploy/desktop-session/test_echo_kwin_window_bridge.py"
require_file "$REPO_ROOT/deploy/desktop-session/verify_wayland_native_app_ipc.py"
require_file "$REPO_ROOT/deploy/desktop-session/test_verify_wayland_native_app_ipc.py"
require_executable "$REPO_ROOT/deploy/desktop-session/echo-notification-service"
require_file "$REPO_ROOT/deploy/desktop-session/echo_notification_store.py"
require_executable "$REPO_ROOT/deploy/desktop-session/test_echo_notification_store.py"
require_executable "$REPO_ROOT/deploy/desktop-session/test-echo-notification-service.sh"
require_executable "$REPO_ROOT/deploy/desktop-session/echo-clipboard-host"
require_file "$REPO_ROOT/deploy/desktop-session/klipperrc"
require_executable "$REPO_ROOT/deploy/desktop-session/test_echo_clipboard_host.py"
require_executable "$REPO_ROOT/deploy/desktop-session/echo-accessibility-smoke.py"
require_executable "$REPO_ROOT/deploy/desktop-session/test_echo_accessibility_smoke.py"
require_file "$REPO_ROOT/deploy/desktop-session/echo-screen-reader.desktop"
require_file "$REPO_ROOT/deploy/system-health/echo-coredump.conf"
require_executable "$REPO_ROOT/deploy/system-health/echo-crash-health"
require_file "$REPO_ROOT/deploy/system-health/echo-crash-health.service"
require_executable "$REPO_ROOT/deploy/system-health/test-echo-coredump-policy.sh"
require_file "$REPO_ROOT/deploy/system-health/README.md"
require_executable "$REPO_ROOT/deploy/desktop-session/echo-wayland-session.sh"
require_executable "$REPO_ROOT/deploy/desktop-session/echo-wayland-shell-session.sh"
require_executable "$REPO_ROOT/deploy/desktop-session/smoke-wayland-session.sh"
require_executable "$REPO_ROOT/deploy/desktop-session/smoke-wayland-session-client.sh"
require_executable "$REPO_ROOT/deploy/desktop-session/wayland-window-smoke.py"
require_file "$REPO_ROOT/deploy/desktop-session/kwin-window-bridge/metadata.json"
require_file "$REPO_ROOT/deploy/desktop-session/kwin-window-bridge/contents/code/main.js"
require_file "$REPO_ROOT/deploy/desktop-session/kwin-liquid-glass-effect/CMakeLists.txt"
require_file "$REPO_ROOT/deploy/desktop-session/kwin-liquid-glass-effect/src/main.cpp"
require_file "$REPO_ROOT/deploy/desktop-session/kwin-liquid-glass-effect/src/metadata.json"
require_file "$REPO_ROOT/deploy/desktop-session/kwin-liquid-glass-effect/src/echo_liquid_glass_effect.h"
require_file "$REPO_ROOT/deploy/desktop-session/kwin-liquid-glass-effect/src/echo_liquid_glass_effect.cpp"
require_file "$REPO_ROOT/deploy/oem/echo-wayland.desktop"
require_file "$REPO_ROOT/deploy/oem/kscreenlockerrc"
require_file "$REPO_ROOT/frontend/electron/renderer-readiness.cjs"
require_file "$REPO_ROOT/frontend/electron/renderer-readiness.node-test.cjs"
require_file "$REPO_ROOT/frontend/electron/native-app-ipc-smoke.cjs"
require_file "$REPO_ROOT/frontend/electron/native-app-ipc-smoke.node-test.cjs"
require_file "$REPO_ROOT/frontend/electron/system-controls.cjs"
require_file "$REPO_ROOT/frontend/electron/system-controls.test.cjs"
require_file "$REPO_ROOT/frontend/electron/system-update.cjs"
require_file "$REPO_ROOT/frontend/electron/system-update.test.cjs"
require_file "$REPO_ROOT/frontend/electron/agent-service.cjs"
require_file "$REPO_ROOT/frontend/electron/agent-service.test.cjs"
require_executable "$REPO_ROOT/deploy/desktop-session/verify-desktop-boot.sh"
require_executable "$REPO_ROOT/deploy/update/echo-os-update"
require_executable "$REPO_ROOT/deploy/update/test-echo-os-update.sh"
require_executable "$REPO_ROOT/deploy/update/echo-os-update-channel"
require_executable "$REPO_ROOT/deploy/update/echo-os-update-apply"
require_executable "$REPO_ROOT/deploy/update/echo_update_channel.py"
require_executable "$REPO_ROOT/deploy/update/echo_update_status.py"
require_executable "$REPO_ROOT/deploy/update/test-echo-os-update-channel.sh"
require_executable "$REPO_ROOT/deploy/update/test_echo_update_channel.py"
require_executable "$REPO_ROOT/deploy/update/echo_update_trust.py"
require_executable "$REPO_ROOT/deploy/update/test_echo_update_trust.py"
require_executable "$REPO_ROOT/deploy/update/smoke-update-trust-rotation.sh"
require_executable "$REPO_ROOT/deploy/update/smoke-update-repository-publication.sh"
require_file "$REPO_ROOT/deploy/update/update-channel"
require_file "$REPO_ROOT/deploy/update/org.echoos.update.policy"
require_file "$REPO_ROOT/deploy/update/echo-os-update-fetch.service"
require_file "$REPO_ROOT/deploy/update/echo-os-update-fetch.timer"
require_file "$REPO_ROOT/deploy/update/echo-update-trust-promote.service"
require_executable "$REPO_ROOT/deploy/update/verify-update-bundle.py"
require_executable "$REPO_ROOT/deploy/update/verify-verity-set.py"
require_executable "$REPO_ROOT/deploy/update/test_verify_update_bundle.py"
require_executable "$REPO_ROOT/deploy/update/test_verify_verity_set.py"
require_executable "$REPO_ROOT/deploy/update/create-update-bundle.sh"
require_executable "$REPO_ROOT/deploy/update/publish_update_repository.py"
require_file "$REPO_ROOT/deploy/update/test_publish_update_repository.py"
require_executable "$REPO_ROOT/deploy/data-protection/echo_data_protection.py"
require_executable "$REPO_ROOT/deploy/data-protection/test_echo_data_protection.py"
require_file "$REPO_ROOT/deploy/data-protection/README.md"
require_executable "$REPO_ROOT/deploy/data-protection/echo-swtpm-ci"
require_executable "$REPO_ROOT/deploy/data-protection/echo-encrypted-image"
require_executable "$REPO_ROOT/deploy/data-protection/verify_uki_pcr_policy.py"
require_executable "$REPO_ROOT/deploy/data-protection/test_verify_uki_pcr_policy.py"
require_pattern "$IMAGE_DIR/build-image.sh" '^DESKTOP_BINARY="\$FRONTEND_DIR/release/linux-unpacked/echo-os-desktop"$' "image assembly selects the generated Linux desktop payload"
require_pattern "$IMAGE_DIR/build-image.sh" '\[\[ -x "\$DESKTOP_BINARY" && -f "\$CHROME_SANDBOX" \]\]' "image assembly rejects an incomplete generated desktop payload"
require_pattern "$IMAGE_DIR/build-image.sh" '^DESKTOP_RESOURCES="\$FRONTEND_DIR/release/linux-unpacked/resources"$' "image assembly identifies the native shell resource boundary"
require_pattern "$IMAGE_DIR/build-image.sh" '^find "\$DESKTOP_RESOURCES/app-update\.yml" -type f -delete' "native shell strips electron-builder's standalone update channel"
require_pattern "$IMAGE_DIR/build-image.sh" 'native OS shell contains standalone desktop resource' "image assembly rejects standalone Agent and updater resources in the native shell"
require_pattern "$IMAGE_DIR/build-image.sh" '^node "\$NATIVE_SHELL_VERIFY" ' "image assembly verifies the generated ELF and ASAR contents"
require_pattern "$IMAGE_DIR/verify-native-shell-package.cjs" '^  "native-shell-profile\.json",$' "native shell verifier fixes the identity marker in its external resource inventory"
require_pattern "$IMAGE_DIR/verify-native-shell-package.cjs" 'ASAR source differs from checkout' "native shell verifier binds packaged main-process code to the checkout"
require_pattern "$IMAGE_DIR/verify-native-shell-package.cjs" 'little-endian Linux x86-64 ELF' "native shell verifier rejects the wrong target architecture"
require_file "$IMAGE_DIR/native-shell-profile.json"
require_pattern "$IMAGE_DIR/native-shell-profile.json" '^\{ "schema": "echo\.native_shell_profile\.v1" \}$' "packaged native shell has a fixed artifact identity"
require_pattern "$REPO_ROOT/frontend/electron/shell-profile.cjs" '^const NATIVE_SHELL_PROFILE = "native-shell-profile\.json";$' "main process reads the packaged native shell identity"
require_pattern "$REPO_ROOT/frontend/electron/main.cjs" '^  PACKAGED_NATIVE_SHELL \|\|$' "packaged native shell behavior does not depend only on session environment"
require_pattern "$REPO_ROOT/frontend/electron/main.cjs" '^      if \(!NATIVE_SHELL\) \{$' "native shell skips standalone Agent resource materialization"
require_pattern "$REPO_ROOT/frontend/electron/main.cjs" '\(app\.isPackaged && !NATIVE_SHELL\) \|\| SMOKE_TEST_BACKEND' "native shell never starts the standalone bundled Agent"
require_pattern "$REPO_ROOT/frontend/electron/main.cjs" '^    if \(NATIVE_SHELL \|\| !app\.isPackaged\) \{$' "native shell restarts only the system Agent service"

require_pattern "$IMAGE_DIR/mkosi.conf" '^Distribution=debian$' "Debian base is explicit"
require_pattern "$IMAGE_DIR/mkosi.conf" '^MinimumVersion=25\.3$' "mkosi contract rejects older builders"
forbid_pattern "$IMAGE_DIR/mkosi.conf" '^ImageVersion=' "mkosi.version is the single release-version source"
require_pattern "$IMAGE_DIR/mkosi.conf" '^Release=trixie$' "Debian stable release is pinned"
require_pattern "$IMAGE_DIR/mkosi.conf" '^LocalMirror=https://snapshot\.debian\.org/archive/debian/20260825T000000Z$' "build package snapshot is immutable"
require_pattern "$IMAGE_DIR/mkosi.conf" '^Mirror=https://deb\.debian\.org/debian$' "runtime package source remains Debian official"
require_pattern "$IMAGE_DIR/mkosi.conf" '^Architecture=x86-64$' "first hardware contract is x86-64"
require_pattern "$IMAGE_DIR/mkosi.conf" '^Format=disk$' "output is a GPT disk image"
require_pattern "$IMAGE_DIR/mkosi.conf" '^Bootable=yes$' "image is required to be bootable"
require_pattern "$IMAGE_DIR/mkosi.conf" '^Bootloader=systemd-boot$' "UEFI bootloader is systemd-boot"
require_pattern "$IMAGE_DIR/mkosi.conf" '^UnifiedKernelImages=yes$' "UKI generation is mandatory"
require_pattern "$IMAGE_DIR/mkosi.conf" '^UnifiedKernelImageFormat=echo-os_%v\+&c$' "UKIs carry version and boot-attempt counters"
require_pattern "$IMAGE_DIR/mkosi.conf" '^SplitArtifacts=uki,partitions$' "root and UKI update payloads are split"
require_pattern "$IMAGE_DIR/mkosi.conf" '^Initrds=%O/initrd$' "main UKI uses the customized systemd initrd"
forbid_pattern "$IMAGE_DIR/mkosi.conf" '^        root=' "UKI root selection cannot bypass its embedded roothash"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        ro$' "verified root is mounted read-only"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        systemd\.verity_root_options=panic-on-corruption$' "runtime fails closed on dm-verity corruption"
require_pattern "$IMAGE_DIR/mkosi.conf" '^SourceDateEpoch=1787616000$' "file timestamps are clamped"
require_pattern "$IMAGE_DIR/mkosi.conf" '^Checksum=yes$' "artifact checksums are mandatory"
require_pattern "$IMAGE_DIR/mkosi.conf" '^SecureBoot=no$' "unsigned bring-up state is explicit"
require_pattern "$IMAGE_DIR/mkosi.conf" '^QemuArgs=-device virtio-vga$' "headless boot still exposes a virtual GPU to Xorg"
require_pattern "$IMAGE_DIR/mkosi.conf" '^RootPassword=hashed:!$' "root login is locked"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        sddm$' "PAM-backed display manager is installed"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        sddm-theme-breeze$' "graphical login theme is explicit"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        dolphin$' "file manager is installed"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        konsole$' "terminal is installed"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        firefox-esr$' "browser is installed"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        kate$' "offline text and structured-data editing is installed"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        okular$' "PDF and document viewing is installed"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        gwenview$' "native image viewing is installed"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        ark$' "native archive inspection is installed"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        haruna$' "native audio and video playback is installed"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        kde-spectacle$' "native screenshot capture is installed"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        kcalc$' "native calculator is installed"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        xdg-utils$' "freedesktop default-handler tools are installed"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        desktop-file-utils$' "desktop-file validation and MIME database tools are installed"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        7zip$' "Ark receives its recommended 7-Zip backend"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        bzip2$' "Ark receives its recommended bzip2 backend"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        unar$' "Ark receives its recommended multi-format unarchiver"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        unzip$' "Ark receives its recommended ZIP extraction backend"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        zip$' "Ark receives its recommended ZIP creation backend"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        udisks2$' "UDisks2 provides PolicyKit-mediated removable-media operations"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        dosfstools$' "FAT removable media can be checked and created"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        exfatprogs$' "exFAT removable media can be checked and created"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        ntfs-3g$' "NTFS removable media has a userspace read-write implementation"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        e2fsprogs$' "ext4 removable media can be checked and created"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        btrfs-progs$' "Btrfs removable media can be checked and created"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        xfsprogs$' "XFS removable media can be checked and created"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        kio-extras$' "Dolphin receives KDE removable-device and MTP integration"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        libmtp-runtime$' "portable MTP devices have the userspace runtime"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        eject$' "optical and removable-media eject tooling is installed"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        usbutils$' "USB hardware can be inspected locally"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        cups-daemon$' "CUPS provides the native local print scheduler"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        cups-client$' "native applications receive CUPS queue clients"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        cups-common$' "CUPS ships its common MIME and policy data"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        cups-filters$' "modern print jobs receive the OpenPrinting filter chain"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        cups-filters-core-drivers$' "driverless IPP jobs receive PDF and raster filters"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        cups-pk-helper$' "printer administration uses the system PolicyKit helper"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        print-manager$' "KDE System Settings exposes printer and queue management"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        ipp-usb$' "driverless IPP-over-USB printers are supported"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        avahi-daemon$' "loopback driverless USB discovery has its DNS-SD provider"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        libsane1$' "native applications receive the SANE scanner library and USB rules"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        libsane-common$' "native scanner backends receive their configuration catalog"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        sane-utils$' "scanimage provides a native scanner diagnostic frontend"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        sane-airscan$' "driverless eSCL and WSD scanners use the AirScan backend"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        skanpage$' "KDE provides a multi-page graphical scanning surface"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        network-manager$' "NetworkManager and nmcli back the real Wi-Fi control"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        nftables$' "nftables is the host packet-filter backend"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        firewalld$' "firewalld provides a policy-managed host firewall"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        plasma-firewall$' "KDE System Settings exposes an authorized firewall surface"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        bluez$' "BlueZ backs the real Bluetooth control"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        wireplumber$' "WirePlumber and wpctl back the real audio control"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        brightnessctl$' "brightnessctl backs the real display control"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" 'for control_tool in /usr/bin/nmcli /usr/bin/bluetoothctl /usr/bin/wpctl /usr/bin/brightnessctl; do' "image assembly rejects an incomplete native Control Center runtime"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        systemsettings$' "KDE System Settings is installed as the native settings surface"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        plasma-nm$' "native settings can select and configure NetworkManager connections"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        bluedevil$' "native settings can pair and manage Bluetooth devices"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        plasma-pa$' "native settings can manage PipeWire audio devices"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        kscreen$' "native settings can configure displays"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        powerdevil$' "native settings can configure power policy"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        upower$' "battery and line-power state has a system D-Bus provider"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        power-profiles-daemon$' "platform power profiles have a system D-Bus provider"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" 'kcm_networkmanagement\.desktop' "image assembly rejects an incomplete native settings module set"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" 'org_kde_powerdevil' "image assembly rejects a missing PowerDevil runtime"
require_pattern "$IMAGE_DIR/mkosi.extra/usr/lib/sysusers.d/echo-os.conf" '^m echo audio$' "desktop user belongs to the audio device group"
require_pattern "$IMAGE_DIR/mkosi.extra/usr/lib/sysusers.d/echo-os.conf" '^m echo input$' "desktop user can use brightness-controlled LED input devices"
require_pattern "$IMAGE_DIR/mkosi.extra/usr/lib/sysusers.d/echo-os.conf" '^m echo video$' "desktop user can use brightness-controlled backlight devices"
require_pattern "$IMAGE_DIR/mkosi.extra/usr/lib/sysusers.d/echo-os.conf" '^m echo scanner$' "desktop user receives the distribution scanner-device authorization"
require_pattern "$REPO_ROOT/deploy/desktop-session/setup-desktop-session.sh" 'systemsettings plasma-nm bluedevil plasma-pa kscreen powerdevil' "manual desktop installation includes the native settings module set"
require_pattern "$REPO_ROOT/deploy/desktop-session/setup-desktop-session.sh" '^  upower power-profiles-daemon \\' "manual desktop installation includes both system power providers"
require_pattern "$REPO_ROOT/deploy/desktop-session/setup-desktop-session.sh" '^  udisks2 dosfstools exfatprogs ntfs-3g e2fsprogs btrfs-progs xfsprogs \\' "manual desktop installation includes the removable filesystem stack"
require_pattern "$REPO_ROOT/deploy/desktop-session/setup-desktop-session.sh" '^  kio-extras libmtp-runtime eject usbutils \\' "manual desktop installation includes Dolphin portable-device integration"
require_pattern "$REPO_ROOT/deploy/desktop-session/setup-desktop-session.sh" 'deploy/removable-storage/echo-removable-storage-health' "manual desktop installation installs the removable-storage health gate"
require_pattern "$REPO_ROOT/deploy/desktop-session/setup-desktop-session.sh" 'systemctl enable echo-removable-storage-health\.service' "manual desktop installation enables the removable-storage health gate"
require_pattern "$REPO_ROOT/deploy/desktop-session/setup-desktop-session.sh" '^  cups-daemon cups-client cups-common cups-filters cups-filters-core-drivers \\' "manual desktop installation includes the CUPS/filter chain"
require_pattern "$REPO_ROOT/deploy/desktop-session/setup-desktop-session.sh" '^  cups-pk-helper print-manager ipp-usb avahi-daemon \\' "manual desktop installation includes KDE, PolicyKit and driverless USB printing"
require_pattern "$REPO_ROOT/deploy/desktop-session/setup-desktop-session.sh" 'deploy/printing/echo-printing-health' "manual desktop installation installs the printing health gate"
require_pattern "$REPO_ROOT/deploy/desktop-session/setup-desktop-session.sh" 'systemctl enable cups\.service' "manual desktop installation enables the local scheduler"
require_pattern "$REPO_ROOT/deploy/desktop-session/setup-desktop-session.sh" 'systemctl enable echo-printing-health\.service' "manual desktop installation enables the printing health gate"
require_pattern "$REPO_ROOT/deploy/desktop-session/setup-desktop-session.sh" '^  libsane1 libsane-common sane-utils sane-airscan skanpage \\' "manual desktop installation includes native USB and driverless scanning"
require_pattern "$REPO_ROOT/deploy/desktop-session/setup-desktop-session.sh" 'deploy/scanning/echo-scanning-health' "manual desktop installation installs the scanning health gate"
require_pattern "$REPO_ROOT/deploy/desktop-session/setup-desktop-session.sh" 'systemctl disable --now saned\.socket' "manual desktop installation disables scanner sharing"
require_pattern "$REPO_ROOT/deploy/desktop-session/setup-desktop-session.sh" 'systemctl enable echo-scanning-health\.service' "manual desktop installation enables the scanning health gate"
require_pattern "$REPO_ROOT/deploy/desktop-session/setup-desktop-session.sh" '^  dolphin konsole firefox-esr kate okular gwenview ark haruna kde-spectacle kcalc \\' "manual desktop installation includes the offline core application set"
require_pattern "$REPO_ROOT/deploy/desktop-session/setup-desktop-session.sh" '^  xdg-utils desktop-file-utils 7zip bzip2 unar unzip zip \\' "manual desktop installation includes default-handler validation and Ark backends"
require_pattern "$REPO_ROOT/deploy/desktop-session/setup-desktop-session.sh" 'deploy/core-apps/echo-core-apps-health' "manual desktop installation installs the core-app health gate"
require_pattern "$REPO_ROOT/deploy/desktop-session/setup-desktop-session.sh" 'deploy/core-apps/echo_core_apps_session_smoke\.py' "manual desktop installation carries the functional core-app session diagnostic"
require_pattern "$REPO_ROOT/deploy/desktop-session/setup-desktop-session.sh" 'systemctl enable echo-core-apps-health\.service' "manual desktop installation enables the core-app health gate"
require_pattern "$REPO_ROOT/deploy/desktop-session/setup-desktop-session.sh" '^for group_name in audio video input render scanner docker tty; do$' "manual desktop user receives the same hardware groups as the image user"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        systemd-container$' "systemd-sysupdate runtime is installed"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        gpgv$' "detached update signatures can be verified"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        ca-certificates$' "HTTPS update fetches use the distribution CA trust store"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        cryptsetup-bin$' "LUKS2 device data can be inspected and unlocked"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        systemd-cryptsetup$' "systemd TPM2 LUKS tokens are available"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        systemd-coredump$' "native local crash collection is installed"
forbid_pattern "$IMAGE_DIR/mkosi.conf" 'initramfs-tools|cryptsetup-initramfs' "the main root does not build an unused legacy initramfs"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        flatpak$' "sandboxed persistent application runtime is installed"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        plasma-discover$' "graphical application center is installed"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        plasma-discover-backend-flatpak$' "application center has the Flatpak backend"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        libglib2\.0-bin$' "desktop files can be launched without a command shell"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        kbd$' "installed console keymaps can be selected"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        console-data$' "the full console keymap catalog is installed"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        console-setup-linux$' "Linux console keymap data is installed"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        keyboard-configuration$' "Debian keyboard configuration data is installed"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        tzdata$' "IANA timezone data is explicit"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        xss-lock$' "logind/X11 lock coordinator is installed"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        xsecurelock$' "PAM-backed X11 screen locker is installed"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        kwin-wayland$' "KWin Wayland compositor is installed alongside the X11 bring-up provider"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        libkscreenlocker6$' "KWin Wayland ships its PAM-backed screen locker"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        xwayland$' "Wayland session retains X11 application compatibility"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        fcitx5$' "multilingual input-method framework is installed"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        fcitx5-chinese-addons$' "Chinese Pinyin input support is installed"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        fcitx5-frontend-gtk3$' "GTK3 applications receive Fcitx5 input"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        fcitx5-frontend-gtk4$' "GTK4 applications receive Fcitx5 input"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        fcitx5-frontend-qt5$' "Qt5 applications receive Fcitx5 input"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        fcitx5-frontend-qt6$' "Qt6 applications receive Fcitx5 input"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        fcitx5-config-qt$' "standalone Fcitx5 configuration is installed"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        kde-config-fcitx5$' "KDE System Settings exposes Fcitx5 configuration"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        plasma-workspace$' "Plasma 6 provides the supported Klipper implementation"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        libqt6sql6-sqlite$' "Klipper has its required SQLite driver"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        python3-pyqt6\.qtqml$' "windowless Klipper host has QtQml bindings"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        xclip$' "X11 clipboard interoperability client is installed"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        wl-clipboard$' "Wayland clipboard interoperability clients are installed"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        at-spi2-core$' "AT-SPI accessibility bus and registry are installed"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        python3-pyatspi$' "accessibility-tree gate has the official Python binding"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        python3-xlib$' "SDDM shortcut helper has the official X11 binding"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        orca$' "screen-reader application is installed"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        speech-dispatcher$' "screen reader has the speech dispatcher"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        speech-dispatcher-espeak-ng$' "speech dispatcher has an offline voice backend"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        espeak-ng$' "offline text-to-speech engine is installed"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        python3-dbus$' "KWin bridge has the official Python D-Bus binding"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        python3-gi$' "KWin bridge has the GLib event-loop binding"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        kwin-dev$' "native glass effect builds against the image's KWin ABI"
require_pattern "$IMAGE_DIR/mkosi.conf" '^BuildScripts=mkosi\.build$' "mkosi compiles the compositor effect in its build overlay"
require_pattern "$IMAGE_DIR/mkosi.conf" '^BuildSources=%D/\.\./\.\.:echo-os$' "mkosi exposes the fixed project source root to the effect build"
require_pattern "$IMAGE_DIR/mkosi.build" 'cmake --build "\$EFFECT_BUILD" --parallel' "native glass effect is compiled during image assembly"
require_pattern "$IMAGE_DIR/mkosi.build" '^DESTDIR=' "native glass artifact is installed through mkosi's image staging root"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        restic$' "Debian restic provides the encrypted backup format"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        acl$' "backup acceptance can verify POSIX ACL metadata"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        attr$' "backup acceptance can verify extended attributes"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        util-linux$' "backup lifecycle has fixed block, mount and privilege-drop tools"
require_pattern "$IMAGE_DIR/mkosi.conf" '^        %D/mkosi\.agent-runtime:/$' "verified native Agent tree is copied into the image"
require_pattern "$IMAGE_DIR/mkosi.conf" 'echo-agent\.service:/usr/lib/systemd/system/echo-agent\.service$' "native Agent service is copied into the image"
require_pattern "$IMAGE_DIR/mkosi.conf" 'echo-agent-health\.service:/usr/lib/systemd/system/echo-agent-health\.service$' "native Agent boot gate is copied into the image"
require_pattern "$IMAGE_DIR/mkosi.conf" 'verify-native-agent-runtime\.py:/usr/lib/echo-os/verify-native-agent-runtime\.py$' "native Agent provenance verifier is copied into the image"
require_pattern "$IMAGE_DIR/mkosi.conf" 'echo-session-lock:/usr/lib/echo-os/echo-session-lock$' "session lock coordinator is copied into the image"
require_pattern "$IMAGE_DIR/mkosi.conf" 'echo-screen-locker:/usr/lib/echo-os/echo-screen-locker$' "sleep-aware lock adapter is copied into the image"
require_pattern "$IMAGE_DIR/mkosi.conf" 'echo-lock\.pam:/etc/pam\.d/echo-lock$' "dedicated lock PAM policy is copied into the image"
require_pattern "$IMAGE_DIR/mkosi.conf" 'echo-kwin-window-bridge:/usr/lib/echo-os/echo-kwin-window-bridge$' "session-private compositor bridge is copied into the image"
require_pattern "$IMAGE_DIR/mkosi.conf" 'echo-notification-service:/usr/lib/echo-os/echo-notification-service$' "native notification D-Bus service is copied into the image"
require_pattern "$IMAGE_DIR/mkosi.conf" 'echo_notification_store\.py:/usr/lib/echo-os/echo_notification_store\.py$' "bounded notification history module is copied into the image"
require_pattern "$IMAGE_DIR/mkosi.conf" 'echo-clipboard-host:/usr/lib/echo-os/echo-clipboard-host$' "windowless Klipper host is copied into the image"
require_pattern "$IMAGE_DIR/mkosi.conf" 'klipperrc:/etc/xdg/klipperrc$' "clipboard privacy defaults are copied into the image"
require_pattern "$IMAGE_DIR/mkosi.conf" 'echo-accessibility-smoke\.py:/usr/lib/echo-os/echo-accessibility-smoke\.py$' "privacy-bounded AT-SPI tree probe is copied into the image"
require_pattern "$IMAGE_DIR/mkosi.conf" 'echo-screen-reader\.desktop:/usr/local/share/applications/echo-screen-reader\.desktop$' "Orca is exposed through the application catalog"
require_pattern "$IMAGE_DIR/mkosi.conf" 'echo-sddm-accessibility:/usr/lib/echo-os/echo-sddm-accessibility$' "greeter-only screen-reader shortcut helper is copied into the image"
require_pattern "$IMAGE_DIR/mkosi.conf" 'echo-sddm-xsetup:/usr/lib/echo-os/echo-sddm-xsetup$' "SDDM display setup wrapper is copied into the image"
require_pattern "$IMAGE_DIR/mkosi.conf" 'echo-sddm-xstop:/usr/lib/echo-os/echo-sddm-xstop$' "SDDM display cleanup wrapper is copied into the image"
require_pattern "$IMAGE_DIR/mkosi.conf" 'echo-coredump\.conf:/etc/systemd/coredump\.conf\.d/60-echo-os\.conf$' "bounded coredump policy is copied into the image"
require_pattern "$IMAGE_DIR/mkosi.conf" 'echo-crash-health:/usr/lib/echo-os/echo-crash-health$' "crash-collection health verifier is copied into the image"
require_pattern "$IMAGE_DIR/mkosi.conf" 'echo-crash-health\.service:/usr/lib/systemd/system/echo-crash-health\.service$' "crash-collection boot gate is copied into the image"
require_pattern "$IMAGE_DIR/mkosi.conf" 'README\.md:/usr/share/doc/echo-os/crash-collection\.md$' "crash-collection privacy policy is copied into the image"
require_pattern "$IMAGE_DIR/mkosi.conf" 'echo-removable-storage-health:/usr/lib/echo-os/echo-removable-storage-health$' "removable-storage health verifier is copied into the image"
require_pattern "$IMAGE_DIR/mkosi.conf" 'echo-removable-storage-health\.service:/usr/lib/systemd/system/echo-removable-storage-health\.service$' "removable-storage boot gate is copied into the image"
require_pattern "$IMAGE_DIR/mkosi.conf" 'README\.md:/usr/share/doc/echo-os/removable-storage\.md$' "removable-storage policy is copied into the image"
require_pattern "$IMAGE_DIR/mkosi.conf" 'cupsd\.conf:/etc/cups/cupsd\.conf$' "local-only CUPS policy is copied into the image"
require_pattern "$IMAGE_DIR/mkosi.conf" 'ipp-usb\.conf:/etc/ipp-usb/ipp-usb\.conf$' "loopback driverless USB policy is copied into the image"
require_pattern "$IMAGE_DIR/mkosi.conf" 'echo_printing_policy\.py:/usr/lib/echo-os/echo-printing-policy\.py$' "printing policy verifier is copied into the image"
require_pattern "$IMAGE_DIR/mkosi.conf" 'echo-printing-health:/usr/lib/echo-os/echo-printing-health$' "printing runtime health verifier is copied into the image"
require_pattern "$IMAGE_DIR/mkosi.conf" 'echo-printing-health\.service:/usr/lib/systemd/system/echo-printing-health\.service$' "printing boot gate is copied into the image"
require_pattern "$IMAGE_DIR/mkosi.conf" 'README\.md:/usr/share/doc/echo-os/printing\.md$' "printing privacy policy is copied into the image"
require_pattern "$IMAGE_DIR/mkosi.conf" 'airscan\.conf:/etc/sane\.d/airscan\.conf$' "fixed on-demand AirScan policy is copied into the image"
require_pattern "$IMAGE_DIR/mkosi.conf" 'echo_scanning_policy\.py:/usr/lib/echo-os/echo-scanning-policy\.py$' "scanning policy verifier is copied into the image"
require_pattern "$IMAGE_DIR/mkosi.conf" 'echo-scanning-health:/usr/lib/echo-os/echo-scanning-health$' "scanning runtime health verifier is copied into the image"
require_pattern "$IMAGE_DIR/mkosi.conf" 'echo-scanning-health\.service:/usr/lib/systemd/system/echo-scanning-health\.service$' "scanning boot gate is copied into the image"
require_pattern "$IMAGE_DIR/mkosi.conf" 'README\.md:/usr/share/doc/echo-os/scanning\.md$' "scanning privacy policy is copied into the image"
require_pattern "$IMAGE_DIR/mkosi.conf" 'mimeapps\.list:/etc/xdg/mimeapps\.list$' "signed-root XDG defaults are copied into the image"
require_pattern "$IMAGE_DIR/mkosi.conf" 'echo_core_apps_policy\.py:/usr/lib/echo-os/echo-core-apps-policy\.py$' "core-app policy verifier is copied into the image"
require_pattern "$IMAGE_DIR/mkosi.conf" 'echo-core-apps-health:/usr/lib/echo-os/echo-core-apps-health$' "core-app runtime health verifier is copied into the image"
require_pattern "$IMAGE_DIR/mkosi.conf" 'echo_core_apps_session_smoke\.py:/usr/lib/echo-os/echo-core-apps-session-smoke\.py$' "functional XDG core-app session diagnostic is copied into the image"
require_pattern "$IMAGE_DIR/mkosi.conf" 'echo-core-apps-health\.service:/usr/lib/systemd/system/echo-core-apps-health\.service$' "core-app boot gate is copied into the image"
require_pattern "$IMAGE_DIR/mkosi.conf" 'README\.md:/usr/share/doc/echo-os/core-apps\.md$' "core application policy is copied into the image"
require_pattern "$IMAGE_DIR/mkosi.conf" 'echo-user-backup:/usr/bin/echo-os-backup$' "offline backup coordinator is copied into the image"
require_pattern "$IMAGE_DIR/mkosi.conf" 'echo-user-backup\.service:/usr/lib/systemd/system/echo-user-backup\.service$' "credential-backed backup unit is copied into the image"
require_pattern "$IMAGE_DIR/mkosi.conf" 'echo_restore_transaction\.py:/usr/lib/echo-os/echo-restore-transaction\.py$' "restore transaction verifier is copied into the main image"
require_pattern "$IMAGE_DIR/mkosi.conf" 'echo-restore-transaction-health\.service:/usr/lib/systemd/system/echo-restore-transaction-health\.service$' "normal boot receives the restore transaction gate"
require_pattern "$REPO_ROOT/packaging/recovery/mkosi.conf" 'echo_restore_transaction\.py:/usr/lib/echo-os/echo-restore-transaction\.py$' "independent Recovery ships the restore transaction engine"
require_pattern "$IMAGE_DIR/mkosi.conf" 'README\.md:/usr/share/doc/echo-os/user-backup\.md$' "backup and migration boundary is copied into the image"
require_pattern "$IMAGE_DIR/mkosi.conf" 'kwin-window-bridge:/usr/share/kwin/scripts/org\.echoos\.windowbridge$' "KWin script package is copied into the image"
require_pattern "$IMAGE_DIR/mkosi.conf" 'echo_data_protection\.py:/usr/lib/echo-os/echo-data-protection$' "fixed-volume data-protection enrollment ships in the main image"
require_pattern "$IMAGE_DIR/mkosi.conf" 'echo-os-update-channel:/usr/bin/echo-os-update-channel$' "signed-channel coordinator is copied into the image"
require_pattern "$IMAGE_DIR/mkosi.conf" 'echo-os-update-apply:/usr/lib/echo-os/echo-os-update-apply$' "fixed graphical update helper is copied into the image"
require_pattern "$IMAGE_DIR/mkosi.conf" 'echo_update_channel\.py:/usr/lib/echo-os/echo_update_channel\.py$' "bounded HTTPS channel client is copied into the image"
require_pattern "$IMAGE_DIR/mkosi.conf" 'echo_update_status\.py:/usr/lib/echo-os/echo_update_status\.py$' "bounded public update state is copied into the image"
require_pattern "$IMAGE_DIR/mkosi.conf" 'echo_update_trust\.py:/usr/lib/echo-os/echo_update_trust\.py$' "monotonic update-trust engine is copied into the image"
require_pattern "$IMAGE_DIR/mkosi.conf" 'update-channel:/usr/lib/echo-os/update-channel$' "default production channel is copied into the immutable root"
require_pattern "$IMAGE_DIR/mkosi.conf" 'echo-os-update-fetch\.service:/usr/lib/systemd/system/echo-os-update-fetch\.service$' "fetch-only update service is copied into the image"
require_pattern "$IMAGE_DIR/mkosi.conf" 'echo-os-update-fetch\.timer:/usr/lib/systemd/system/echo-os-update-fetch\.timer$' "periodic update timer is copied into the image"
require_pattern "$IMAGE_DIR/mkosi.conf" 'echo-update-trust-promote\.service:/usr/lib/systemd/system/echo-update-trust-promote\.service$' "healthy-boot trust promotion is copied into the image"
require_pattern "$IMAGE_DIR/mkosi.conf" 'org\.echoos\.update\.policy:/usr/share/polkit-1/actions/org\.echoos\.update\.policy$' "graphical update PolicyKit action is copied into the image"
require_file "$REPO_ROOT/packaging/recovery/mkosi.conf"
require_pattern "$REPO_ROOT/packaging/recovery/mkosi.conf" '^Format=uki$' "recovery is a self-contained UKI"
require_pattern "$REPO_ROOT/packaging/recovery/mkosi.conf" '^MakeInitrd=yes$' "recovery userspace lives in its UKI initrd"
require_pattern "$REPO_ROOT/packaging/recovery/mkosi.conf" '^Autologin=yes$' "physical recovery console is available offline"
require_pattern "$REPO_ROOT/packaging/recovery/mkosi.conf" '^        systemd-cryptsetup$' "Recovery can enroll and unlock systemd TPM2 LUKS tokens"
require_pattern "$REPO_ROOT/packaging/recovery/mkosi.conf" 'echo_data_protection\.py:/usr/lib/echo-os/echo-data-protection$' "Recovery ships the fixed-volume enrollment transaction"
require_pattern "$REPO_ROOT/packaging/recovery/mkosi.conf" '^SecureBoot=no$' "unsigned recovery bring-up state is explicit"
if cmp -s "$IMAGE_DIR/mkosi.version" "$REPO_ROOT/packaging/recovery/mkosi.version"; then
  pass "desktop and recovery release versions match"
else
  fail "desktop and recovery mkosi.version files diverge"
fi

for partition_name in \
  00-esp 10-root 11-root-b 12-root-verity-sig 13-root-b \
  14-root-verity-b 15-root-verity-sig-b 20-var 30-swap 40-home; do
  require_file "$IMAGE_DIR/mkosi.repart/$partition_name.conf"
done
require_pattern "$IMAGE_DIR/mkosi.repart/00-esp.conf" '^Type=esp$' "ESP partition is defined"
require_pattern "$IMAGE_DIR/mkosi.repart/10-root.conf" '^Type=root$' "discoverable root partition is defined"
require_pattern "$IMAGE_DIR/mkosi.repart/10-root.conf" '^Label=echo-root-%A$' "active root slot carries its image version"
require_pattern "$IMAGE_DIR/mkosi.repart/10-root.conf" '^Verity=data$' "active root is dm-verity data"
require_pattern "$IMAGE_DIR/mkosi.repart/10-root.conf" '^ReadOnly=yes$' "active root GPT slot is read-only"
require_pattern "$IMAGE_DIR/mkosi.repart/10-root.conf" '^SplitName=root\.%U$' "active root payload carries its roothash-derived UUID"
require_pattern "$IMAGE_DIR/mkosi.repart/11-root-b.conf" '^Type=root-verity$' "active root hash tree is defined"
require_pattern "$IMAGE_DIR/mkosi.repart/11-root-b.conf" '^Verity=hash$' "active root hash tree is generated"
require_pattern "$IMAGE_DIR/mkosi.repart/11-root-b.conf" '^SplitName=root-verity\.%U$' "hash-tree payload carries its roothash-derived UUID"
require_pattern "$IMAGE_DIR/mkosi.repart/12-root-verity-sig.conf" '^Type=root-verity-sig$' "active verity signature partition is defined"
require_pattern "$IMAGE_DIR/mkosi.repart/12-root-verity-sig.conf" '^Verity=signature$' "root hash is signed into its signature partition"
require_pattern "$IMAGE_DIR/mkosi.repart/12-root-verity-sig.conf" '^SizeMaxBytes=4M$' "active signature partition fits systemd's embedded-signature limit"
for inactive_partition in 13-root-b 14-root-verity-b 15-root-verity-sig-b; do
  require_pattern "$IMAGE_DIR/mkosi.repart/$inactive_partition.conf" '^Label=_empty$' "inactive A/B member $inactive_partition is reserved"
  require_pattern "$IMAGE_DIR/mkosi.repart/$inactive_partition.conf" '^ReadOnly=yes$' "inactive A/B member $inactive_partition is read-only"
done
require_pattern "$IMAGE_DIR/mkosi.repart/15-root-verity-sig-b.conf" '^SizeMaxBytes=4M$' "inactive signature partition fits systemd's embedded-signature limit"
for mutable_partition in 20-var 30-swap 40-home; do
  require_pattern "$IMAGE_DIR/mkosi.repart/$mutable_partition.conf" '^NoAuto=yes$' "$mutable_partition is mounted only through the explicit encrypted-data policy"
  require_pattern "$REPO_ROOT/deploy/installer/repart.d/$mutable_partition.conf" '^NoAuto=yes$' "installed $mutable_partition preserves the explicit encrypted-data mount policy"
  require_pattern "$REPO_ROOT/deploy/recovery/repart.d/$mutable_partition.conf" '^NoAuto=yes$' "factory-reset $mutable_partition preserves the explicit encrypted-data mount policy"
done
for installer_signature_partition in 12-root-verity-sig 15-root-verity-sig-b; do
  require_pattern "$REPO_ROOT/deploy/installer/repart.d/$installer_signature_partition.conf" '^SizeMinBytes=4M$' "installed $installer_signature_partition has systemd-compatible minimum size"
  require_pattern "$REPO_ROOT/deploy/installer/repart.d/$installer_signature_partition.conf" '^SizeMaxBytes=4M$' "installed $installer_signature_partition stays within systemd's signature limit"
done
require_file "$IMAGE_DIR/mkosi.images/initrd/mkosi.conf"
require_file "$IMAGE_DIR/mkosi.images/initrd/mkosi.extra/usr/lib/systemd/system/echo-machine-state-initrd.service"
require_file "$IMAGE_DIR/mkosi.images/initrd/mkosi.extra/usr/lib/systemd/system/initrd-root-fs.target.d/echo-machine-state.conf"
require_pattern "$IMAGE_DIR/mkosi.images/initrd/mkosi.conf" '^Include=mkosi-initrd$' "custom initrd is systemd based"
for initrd_package in systemd-cryptsetup cryptsetup-bin kmod util-linux; do
  require_pattern "$IMAGE_DIR/mkosi.images/initrd/mkosi.conf" "^        ${initrd_package}$" "custom initrd includes ${initrd_package}"
done
require_pattern "$IMAGE_DIR/mkosi.images/initrd/mkosi.conf" 'echo-machine-id:/usr/lib/echo-os/echo-machine-id$' "custom initrd contains the machine-state helper"
require_pattern "$IMAGE_DIR/mkosi.images/initrd/mkosi.extra/usr/lib/systemd/system/echo-machine-state-initrd.service" '^Requires=sysroot\.mount systemd-cryptsetup@echo\\x2dvar\.service$' "initrd waits for verified root and encrypted var"
require_pattern "$IMAGE_DIR/mkosi.images/initrd/mkosi.extra/usr/lib/systemd/system/echo-machine-state-initrd.service" '^Before=initrd-root-fs\.target initrd-switch-root\.target$' "encrypted machine state is attached before switch-root"
require_pattern "$IMAGE_DIR/mkosi.images/initrd/mkosi.extra/usr/lib/systemd/system/echo-machine-state-initrd.service" '^Environment=rootmnt=/sysroot$' "machine-state helper receives the verified sysroot"
require_pattern "$IMAGE_DIR/mkosi.images/initrd/mkosi.extra/usr/lib/systemd/system/initrd-root-fs.target.d/echo-machine-state.conf" '^Wants=echo-machine-state-initrd\.service$' "initrd root target pulls in machine-state attachment"
require_pattern "$IMAGE_DIR/mkosi.repart/20-var.conf" '^Type=var$' "persistent var partition is defined"
require_pattern "$IMAGE_DIR/mkosi.repart/30-swap.conf" '^Type=swap$' "fixed swap precedes elastic user data"
require_pattern "$IMAGE_DIR/mkosi.repart/40-home.conf" '^Type=home$' "persistent home partition is the final partition"
for protected_partition in 20-var 30-swap 40-home; do
  require_pattern "$IMAGE_DIR/mkosi.repart/$protected_partition.conf" \
    '^Encrypt=key-file$' "factory image encrypts $protected_partition as LUKS2"
  require_pattern "$REPO_ROOT/deploy/installer/repart.d/$protected_partition.conf" \
    '^Encrypt=key-file$' "installed-disk growth preserves encrypted $protected_partition"
done
require_pattern "$IMAGE_DIR/mkosi.repart/40-home.conf" '^GrowFileSystem=yes$' "installed home filesystem is marked for growth"
forbid_pattern "$IMAGE_DIR/mkosi.repart/40-home.conf" '^SizeMaxBytes=' "home can consume remaining installed-disk capacity"
require_pattern "$IMAGE_DIR/mkosi.extra/usr/lib/systemd/system-preset/80-echo-os.preset" '^enable echo-desktop\.service$' "credential-gated VM desktop unit is enabled"
require_pattern "$IMAGE_DIR/mkosi.extra/usr/lib/systemd/system-preset/80-echo-os.preset" '^enable echo-agent\.service$' "native Agent runtime is enabled"
require_pattern "$IMAGE_DIR/mkosi.extra/usr/lib/systemd/system-preset/80-echo-os.preset" '^enable echo-agent-health\.service$' "native Agent health gates boot completion"
require_pattern "$IMAGE_DIR/mkosi.extra/usr/lib/systemd/system-preset/80-echo-os.preset" '^enable echo-desktop-health\.service$' "credential-gated VM desktop health is enabled"
require_pattern "$IMAGE_DIR/mkosi.extra/usr/lib/systemd/system-preset/80-echo-os.preset" '^enable echo-crash-health\.service$' "bounded crash collection gates boot completion"
require_pattern "$IMAGE_DIR/mkosi.extra/usr/lib/systemd/system-preset/80-echo-os.preset" '^enable firewalld\.service$' "host packet filtering starts on every boot"
require_pattern "$IMAGE_DIR/mkosi.extra/usr/lib/systemd/system-preset/80-echo-os.preset" '^enable echo-firewall-health\.service$' "firewall policy gates networking and login"
require_pattern "$IMAGE_DIR/mkosi.extra/usr/lib/systemd/system-preset/80-echo-os.preset" '^enable echo-removable-storage-health\.service$' "removable storage gates login and boot completion"
require_pattern "$IMAGE_DIR/mkosi.extra/usr/lib/systemd/system-preset/80-echo-os.preset" '^enable cups\.service$' "the local CUPS scheduler is enabled"
require_pattern "$IMAGE_DIR/mkosi.extra/usr/lib/systemd/system-preset/80-echo-os.preset" '^enable echo-printing-health\.service$' "private local printing gates login and boot completion"
require_pattern "$IMAGE_DIR/mkosi.extra/usr/lib/systemd/system-preset/80-echo-os.preset" '^disable saned\.socket$' "scanner sharing remains disabled by default"
require_pattern "$IMAGE_DIR/mkosi.extra/usr/lib/systemd/system-preset/80-echo-os.preset" '^enable echo-scanning-health\.service$' "native scanning gates login and boot completion"
require_pattern "$IMAGE_DIR/mkosi.extra/usr/lib/systemd/system-preset/80-echo-os.preset" '^enable echo-core-apps-health\.service$' "core desktop applications gate login and boot completion"
require_pattern "$IMAGE_DIR/mkosi.extra/usr/lib/systemd/system-preset/80-echo-os.preset" '^enable echo-oem-setup\.service$' "first-boot local account setup is enabled"
require_pattern "$IMAGE_DIR/mkosi.extra/usr/lib/systemd/system-preset/80-echo-os.preset" '^enable echo-local-account\.service$' "A/B account restoration is enabled"
require_pattern "$IMAGE_DIR/mkosi.extra/usr/lib/systemd/system-preset/80-echo-os.preset" '^enable echo-account-capture\.path$' "local identity changes are persisted for the next root"
require_pattern "$IMAGE_DIR/mkosi.extra/usr/lib/systemd/system-preset/80-echo-os.preset" '^enable echo-login-health\.service$' "production login health gates boot completion"
require_pattern "$IMAGE_DIR/mkosi.extra/usr/lib/systemd/system-preset/80-echo-os.preset" '^enable echo-app-catalog\.service$' "sandboxed application catalog is provisioned on first boot"
require_pattern "$IMAGE_DIR/mkosi.extra/usr/lib/systemd/system-preset/80-echo-os.preset" '^enable echo-machine-identity-health\.service$' "persistent machine identity is verified on every boot"
require_pattern "$IMAGE_DIR/mkosi.extra/usr/lib/systemd/system-preset/80-echo-os.preset" '^enable echo-network-state-prepare\.service$' "persistent NetworkManager storage is prepared on every boot"
require_pattern "$IMAGE_DIR/mkosi.extra/usr/lib/systemd/system-preset/80-echo-os.preset" '^enable echo-region-state-restore\.service$' "regional state is restored on every boot"
require_pattern "$IMAGE_DIR/mkosi.extra/usr/lib/systemd/system-preset/80-echo-os.preset" '^enable echo-region-state-capture\.path$' "regional changes are captured for the next root"
require_pattern "$IMAGE_DIR/mkosi.extra/usr/lib/systemd/system-preset/80-echo-os.preset" '^enable echo-update-trust-promote\.service$' "healthy roots promote monotonic update trust"
require_pattern "$IMAGE_DIR/mkosi.extra/usr/lib/systemd/system-preset/80-echo-os.preset" '^enable echo-os-update-fetch\.timer$' "signed update polling is enabled"
forbid_pattern "$IMAGE_DIR/mkosi.extra/usr/lib/systemd/system-preset/80-echo-os.preset" '^enable echo-os-update-fetch\.service$' "the fetch worker is timer-triggered, not independently boot-enabled"
require_pattern "$IMAGE_DIR/mkosi.extra/usr/lib/systemd/system-preset/80-echo-os.preset" '^enable sddm\.service$' "production graphical login is enabled"
require_pattern "$IMAGE_DIR/mkosi.extra/boot/loader/loader.conf" '^default echo-os_\*$' "normal OS remains the default boot entry"
require_pattern "$IMAGE_DIR/mkosi.extra/boot/loader/loader.conf" '^editor no$' "boot-menu kernel command line editing is disabled"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" 'chmod 4755.*CHROME_SANDBOX' "Chromium sandbox helper gets production permissions"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" '^/usr/bin/python3 "\$AGENT_RUNTIME_VERIFIER" "\$AGENT_ROOT" --import-runtime$' "image build imports and verifies the native Agent under Debian Python"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" 'release image requires a clean, commit-addressed Agent source' "release image rejects dirty Agent source snapshots"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" '^AGENT_SOURCE_ID=\$AGENT_SOURCE_ID$' "image contract records the exact Agent commit"
require_pattern "$IMAGE_DIR/build-image.sh" '^"\$NATIVE_AGENT_PREPARE"$' "every image build materializes the locked native Agent tree"
require_pattern "$IMAGE_DIR/prepare-native-agent-runtime.sh" '^  --python-version 3\.13 \\$' "native Agent dependencies target Debian 13 Python"
require_pattern "$IMAGE_DIR/prepare-native-agent-runtime.sh" '^  --python-platform x86_64-unknown-linux-gnu \\$' "native Agent dependencies target Linux x86-64"
require_pattern "$IMAGE_DIR/prepare-native-agent-runtime.sh" '^  --require-hashes \\$' "native Agent dependency installation is hash locked"
require_pattern "$IMAGE_DIR/prepare-native-agent-runtime.sh" '^  --only-binary :all: \\$' "native Agent cross-target closure cannot build host-native extensions"
require_pattern "$IMAGE_DIR/verify-native-agent-runtime.py" '^SCHEMA_VERSION = 4$' "native Agent manifest binds recovery and verified runtime identity contracts"
require_pattern "$IMAGE_DIR/verify-native-agent-runtime.py" '^RECOVERY_QUEUE_PATH = "/api/task-runs/recovery-queue"$' "native Agent image requires the persisted recovery queue"
require_pattern "$IMAGE_DIR/verify-native-agent-runtime.py" '^RESUME_EXECUTION_PATH = "/api/task-runs/\{task_id\}/resume-execution"$' "native Agent image requires checkpoint execution resume"
require_pattern "$IMAGE_DIR/verify-native-agent-runtime.py" '^HEALTH_PATH = "/api/health"$' "native Agent image requires a versioned runtime identity endpoint"
require_pattern "$REPO_ROOT/appliance/native_entrypoint.py" '^    os\.environ\["ECHO_RUNTIME_BUNDLE_VERIFIED"\] = "1"$' "native launcher asserts runtime identity only after bundle verification"
require_pattern "$REPO_ROOT/deploy/agent/echo-agent.service" '^Environment=ECHO_NATIVE_OS=1$' "Agent uses the minimal native OS extension"
require_pattern "$REPO_ROOT/deploy/agent/echo-agent.service" '^Environment=ECHO_PROMPT_SKILL_REFRESH_DEADLINE_S=0$' "Agent boot uses its bundled skill catalog without a startup network refresh"
require_pattern "$REPO_ROOT/deploy/agent/echo-agent.service" '^Environment=ECHO_DISABLE_STUB_API=1$' "native Agent never exposes simulated compatibility account or billing APIs"
require_pattern "$REPO_ROOT/deploy/agent/echo-agent.service" '^Environment=ECHO_CODEX_EXECUTABLE=/opt/echo-agent/codex/bin/codex$' "Agent executes the source-bound Linux Codex binary"
require_pattern "$REPO_ROOT/deploy/agent/echo-agent.service" '^Environment=ECHO_NATIVE_AGENT_CONFIG=/opt/echo-agent/native-config\.yaml$' "Agent starts with the source-bound configuration plus OS policy"
require_pattern "$REPO_ROOT/deploy/agent/echo-agent.service" '^Environment=HOME=/home/echo$' "native Agent resolves user tools and Codex state in the local account home"
require_pattern "$REPO_ROOT/deploy/agent/echo-agent.service" '^ReadWritePaths=/home/echo$' "Agent can operate on the encrypted user workspace"
require_pattern "$REPO_ROOT/deploy/agent/echo-agent-native.yaml" '^  enabled: false$' "native Agent policy disables the extra LAN mobile listener"
require_pattern "$REPO_ROOT/deploy/agent/echo-agent-native.yaml" '^intel_sources: \[\]$' "native Agent boot performs no unsolicited feed refresh"
require_pattern "$REPO_ROOT/deploy/agent/echo-agent.service" '^ExecStart=/usr/bin/python3 -m appliance\.native_entrypoint$' "Agent starts through its fail-closed native entrypoint"
require_pattern "$REPO_ROOT/deploy/agent/echo-agent.service" '^ProtectSystem=strict$' "Agent runtime cannot mutate the verified root"
forbid_pattern "$REPO_ROOT/deploy/agent/echo-agent.service" 'network-online\.target' "Agent boot is not gated on network availability"
require_pattern "$REPO_ROOT/deploy/agent/echo-agent-health.service" '^RequiredBy=boot-complete\.target$' "Agent readiness is a boot-blessing requirement"
require_pattern "$REPO_ROOT/deploy/agent/verify-native-agent-health" '^BASE_URL = "http://127\.0\.0\.1:8000"$' "Agent health probe is fixed to loopback"
require_pattern "$REPO_ROOT/deploy/agent/verify-native-agent-health" 'recovery-queue\?limit=200' "cold boot reads the persisted Agent recovery queue"
require_pattern "$REPO_ROOT/deploy/agent/verify-native-agent-health" 'runtime\.get\("sourceId"\) != source\.get\("source_id"\)' "cold boot binds the live Agent health identity to the image manifest"
forbid_pattern "$REPO_ROOT/deploy/agent/verify-native-agent-health" 'resume-execution|/takeover' "cold boot never mutates or resumes Agent tasks"
require_pattern "$REPO_ROOT/deploy/backup/echo-user-backup" '^REPOSITORY = BACKUP_MOUNT / "echo-os-user"$' "backup repository is fixed below the dedicated mount"
require_pattern "$REPO_ROOT/deploy/backup/echo-user-backup" '^USER_HOME = Path\("/home/echo"\)$' "backup source is fixed to the local user home"
require_pattern "$REPO_ROOT/deploy/backup/echo-user-backup" '^AGENT_STATE = Path\("/var/lib/echo-agent"\)$' "backup source includes native Agent state"
require_pattern "$REPO_ROOT/deploy/backup/echo-user-backup" 'os\.memfd_create\("echo-backup-password", 0\)' "backup password uses anonymous kernel memory"
require_pattern "$REPO_ROOT/deploy/backup/echo-user-backup" '"check", "--read-data"' "backup and restore perform a full repository data read"
require_pattern "$REPO_ROOT/deploy/backup/echo-user-backup" '^def snapshot_from_backup_output\(' "backup records the exact completed snapshot"
require_pattern "$REPO_ROOT/deploy/backup/echo-user-backup" '^SCHEMA = 2$' "backup state records the exact restore staging identity"
require_pattern "$REPO_ROOT/deploy/backup/echo-user-backup" 'staging_name=target\.name' "staged restore binds its exact directory into private state"
require_pattern "$REPO_ROOT/deploy/backup/echo-user-backup" '^def stopped_login_manager\(' "offline data operations close the graphical login race"
require_pattern "$REPO_ROOT/deploy/backup/echo-user-backup" '^def ensure_no_user_processes\(' "offline data operations reject lingering same-user processes"
require_pattern "$REPO_ROOT/deploy/backup/echo-user-backup" 'with stopped_login_manager\(\), stopped_agent\(\):' "backup and restore quiesce both graphical login and the native Agent"
require_pattern "$REPO_ROOT/deploy/backup/test_echo_user_backup.py" '^    def test_agent_is_restarted_when_stop_reports_a_partial_failure\(' "backup tests service recovery after a partial stop failure"
require_pattern "$REPO_ROOT/deploy/backup/test_echo_user_backup.py" '^    def test_login_manager_is_restarted_after_operation_failure\(' "backup tests SDDM recovery after an operation failure"
require_pattern "$REPO_ROOT/deploy/backup/echo-user-backup" '"--overwrite",[[:space:]]*$' "staged restore declares an overwrite policy"
require_pattern "$REPO_ROOT/deploy/backup/echo-user-backup" '^[[:space:]]*"never",$' "staged restore never replaces existing files"
forbid_pattern "$REPO_ROOT/deploy/backup/echo-user-backup" 'shell=True|/bin/sh|sh -c|/etc/shadow|NetworkManager/system-connections|systemd/tpm2' "backup never evaluates a shell or adds credential stores to its source vector"
require_pattern "$REPO_ROOT/deploy/backup/echo-user-backup.service" '^LoadCredentialEncrypted=echo-backup-password$' "scheduled backup accepts only a systemd encrypted credential"
require_pattern "$REPO_ROOT/deploy/backup/echo-user-backup.service" '^ProtectSystem=strict$' "scheduled backup keeps the signed system root read-only"
forbid_pattern "$REPO_ROOT/deploy/backup/echo-user-backup.service" '^\[Install\]|WantedBy=' "backup service is not implicitly enabled without a repository and credential"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'test_echo_user_backup\.py' "image CI tests backup and staged-restore policy"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'test_echo_restore_transaction\.py' "image CI fault-tests restore promotion, rollback and commit"
require_pattern "$REPO_ROOT/deploy/recovery/echo_restore_transaction.py" '^MAX_TREE_ENTRIES = 2_000_000$' "restore promotion bounds its recursive inventory"
require_pattern "$REPO_ROOT/deploy/recovery/echo_restore_transaction.py" '^MAX_TREE_DEPTH = 256$' "restore promotion bounds recursive path depth"
require_pattern "$REPO_ROOT/deploy/recovery/echo_restore_transaction.py" 'content-sha256' "restore promotion binds regular-file bytes"
require_pattern "$REPO_ROOT/deploy/recovery/echo_restore_transaction.py" 'hard link outside its root' "restore promotion rejects externally mutable hard links"
require_pattern "$REPO_ROOT/deploy/recovery/echo_restore_transaction.py" 'os\.listxattr' "restore promotion binds ACL and extended-attribute storage"
require_pattern "$REPO_ROOT/deploy/recovery/echo_restore_transaction.py" 'metadata\.st_blocks \* 512 < metadata\.st_size' "restore promotion preserves sparse-file semantics"
require_pattern "$REPO_ROOT/deploy/recovery/echo_restore_transaction.py" '"--archive",' "cross-filesystem Agent preparation preserves metadata"
require_pattern "$REPO_ROOT/deploy/recovery/echo_restore_transaction.py" '"--reflink=auto",' "Agent preparation uses copy-on-write when the filesystem supports it"
require_pattern "$REPO_ROOT/deploy/recovery/echo_restore_transaction.py" '"--sparse=always",' "Agent preparation preserves sparse files across filesystems"
require_pattern "$REPO_ROOT/deploy/recovery/echo_restore_transaction.py" '^    def _checkpoint\(' "every restore transition has an atomic private journal checkpoint"
require_pattern "$REPO_ROOT/deploy/recovery/echo_restore_transaction.py" '"commit-authorized"' "old data deletion starts only after a journaled explicit commit"
forbid_pattern "$REPO_ROOT/deploy/recovery/echo_restore_transaction.py" 'shell=True|/bin/sh|sh -c|eval\(|exec\(' "restore metadata and journal content are never executed"
require_pattern "$REPO_ROOT/deploy/backup/echo-restore-transaction-health.service" '^Before=echo-agent\.service sddm\.service echo-desktop\.service boot-complete\.target$' "incomplete migration blocks Agent, login, direct desktop and boot blessing"
require_pattern "$REPO_ROOT/deploy/backup/echo-restore-transaction-health.service" '^RequiredBy=echo-agent\.service sddm\.service echo-desktop\.service boot-complete\.target$' "normal boot requires the restore transaction gate"
require_pattern "$REPO_ROOT/deploy/backup/echo-restore-transaction-health.service" '^After=local-fs\.target systemd-tmpfiles-setup\.service$' "restore health waits for its private state directory"
require_pattern "$IMAGE_DIR/mkosi.extra/usr/lib/systemd/system-preset/80-echo-os.preset" '^enable echo-restore-transaction-health\.service$' "restore transaction boot gating is enabled"
require_pattern "$REPO_ROOT/deploy/recovery/echo-recovery" 'restore-plan WHOLE_DISK' "Recovery exposes a read-only restore plan"
require_pattern "$REPO_ROOT/deploy/recovery/echo-recovery" 'PROMOTE-ECHO-RESTORE-' "Recovery promotion requires a plan-bound token"
require_pattern "$REPO_ROOT/deploy/recovery/echo-recovery" 'ROLLBACK-ECHO-RESTORE-' "Recovery rollback requires a transaction-bound token"
require_pattern "$REPO_ROOT/deploy/recovery/echo-recovery" 'COMMIT-ECHO-RESTORE-' "Recovery commit requires a transaction-bound destructive token"
require_pattern "$REPO_ROOT/deploy/recovery/echo-recovery" '^    options=ro,noload,nodev,nosuid,noexec$' "restore planning mounts mutable volumes read-only without journal replay"
require_pattern "$REPO_ROOT/deploy/recovery/echo-recovery" '^    options=rw,nodev,nosuid,noexec$' "restore writes mount data volumes without executable content"
require_pattern "$REPO_ROOT/deploy/recovery/echo-recovery" 'ECHO_RECOVERY_RESTORE_KEY_FILE:-' "Recovery has one explicit noninteractive restore-key input"
require_pattern "$REPO_ROOT/deploy/recovery/echo-recovery" 'noninteractive restore keys require the source-smoke sentinel' "noninteractive restore credentials remain CI-only"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-desktop-health.service" '^Requires=echo-desktop\.service echo-agent-health\.service$' "VM desktop blessing waits for the current Agent"
require_pattern "$REPO_ROOT/deploy/oem/echo-login-health.service" '^Requires=sddm\.service echo-agent-health\.service$' "production login blessing waits for the current Agent"
require_pattern "$IMAGE_DIR/mkosi.extra/usr/lib/tmpfiles.d/echo-os.conf" '^d /var/lib/echo-agent 0700 echo echo -$' "Agent state lives on encrypted persistent var"
require_pattern "$IMAGE_DIR/mkosi.extra/usr/lib/tmpfiles.d/echo-os.conf" '^d /mnt/echo-backup 0755 root root -$' "external backup has one fixed mount point"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" 'install -d -m 0755 -o root -g root /var/lib/flatpak' "finished image pre-creates persistent Flatpak storage"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" '/var/lib/NetworkManager/system-connections' "finished image pre-creates private persistent NetworkManager storage"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" '^locale-gen$' "curated first-release locales are compiled into the image"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" '^: >/etc/machine-id$' "cloned images contain no shared machine identity"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" 'chmod 0755 "\$SESSION_LOCK" "\$SCREEN_LOCKER"' "screen-lock helpers get executable production permissions"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" 'chmod 0644 "\$LOCK_PAM"' "lock PAM policy gets system policy permissions"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" 'chmod 0755 "\$KWIN_WINDOW_BRIDGE"' "compositor bridge daemon gets executable production permissions"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" 'KWIN_LIQUID_GLASS_PLUGIN=/usr/lib/x86_64-linux-gnu/qt6/plugins/kwin/effects/plugins/org\.echoos\.liquidglass\.so' "image assembly requires the compiled KWin Liquid Glass effect"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" '"\$KWIN_LIQUID_GLASS_PLUGIN"' "native KWin effect receives immutable image permissions"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" 'chmod 0755 "\$NOTIFICATION_SERVICE"' "notification daemon gets executable production permissions"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" 'chmod 0644 "\$NOTIFICATION_STORE"' "notification history module is immutable to the session user"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" 'CLIPBOARD_QML_PLUGIN=/usr/lib/x86_64-linux-gnu/qt6/qml/org/kde/plasma/private/clipboard/libklipperplugin\.so' "image assembly requires the official Plasma Klipper QML plugin"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" 'CLIPBOARD_SQLITE_DRIVER=/usr/lib/x86_64-linux-gnu/qt6/plugins/sqldrivers/libqsqlite\.so' "image assembly requires Klipper's SQLite driver"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" 'chmod 0755 "\$CLIPBOARD_HOST"' "clipboard host gets executable production permissions"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" 'chmod 0644 "\$CLIPBOARD_CONFIG"' "clipboard privacy defaults are immutable to the session user"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" 'from PyQt6\.QtQml import QQmlApplicationEngine' "image build imports the windowless Klipper host bindings"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" 'ACCESSIBILITY_BUS=/usr/libexec/at-spi-bus-launcher' "image assembly requires the AT-SPI session launcher"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" 'import pyatspi; assert pyatspi.Registry' "image build imports the AT-SPI application-tree binding"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" 'from Xlib import X, XK, display' "image build imports the SDDM shortcut X11 binding"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" 'chmod 0755 "\$ACCESSIBILITY_PROBE"' "AT-SPI application-tree probe gets executable production permissions"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" 'chmod 0644 "\$SCREEN_READER_DESKTOP"' "screen-reader launcher is immutable to the session user"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" 'chmod 0755 "\$SDDM_ACCESSIBILITY" "\$SDDM_XSETUP" "\$SDDM_XSTOP"' "greeter accessibility helpers get executable production permissions"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" 'chmod 0755 "\$BACKUP_COMMAND"' "backup coordinator gets executable production permissions"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" 'chmod 0644 "\$BACKUP_SERVICE" "\$BACKUP_DOCUMENTATION"' "backup policy files are root-owned immutable image content"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" '/usr/bin/getfacl && -x /usr/bin/setfacl' "image assembly requires ACL inspection and mutation tools"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" '/usr/bin/getfattr && -x /usr/bin/setfattr' "image assembly requires extended-attribute inspection and mutation tools"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" 'INPUT_METHOD_KCM=/usr/lib/x86_64-linux-gnu/qt6/plugins/plasma/kcms/systemsettings/kcm_fcitx5\.so' "image assembly requires the native input-method settings module"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" 'INPUT_METHOD_PINYIN=/usr/share/fcitx5/inputmethod/pinyin\.conf' "image assembly requires Chinese Pinyin input data"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" "^grep -Fxq '0=pinyin'" "image assembly requires Pinyin as the zh_CN default input method"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" 'chmod 0644 "\$COREDUMP_CONFIG" "\$CRASH_HEALTH_SERVICE"' "crash policy and boot gate are immutable to unprivileged users"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" 'chmod 0755 "\$CRASH_HEALTH"' "crash-collection verifier gets executable production permissions"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" 'install -d -m 0755 -o root -g root /var/lib/systemd/coredump' "finished image pre-creates root-owned local coredump storage"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" 'UDISKS_DAEMON=/usr/libexec/udisks2/udisksd' "image assembly requires the UDisks2 daemon"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" 'org\.freedesktop\.UDisks2\.service' "image assembly requires the UDisks2 D-Bus activation contract"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" '/usr/sbin/mkfs\.vfat.*fsck\.vfat' "image assembly requires FAT creation and repair tools"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" '/usr/sbin/mkfs\.exfat.*fsck\.exfat' "image assembly requires exFAT creation and repair tools"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" '/usr/bin/ntfs-3g.*mkfs\.ntfs' "image assembly requires NTFS read-write and creation tools"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" '/usr/sbin/mkfs\.btrfs.*usr/bin/btrfs' "image assembly requires Btrfs creation and inspection tools"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" '/usr/sbin/mkfs\.xfs.*xfs_repair' "image assembly requires XFS creation and repair tools"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" 'KIO_MTP=/usr/lib/x86_64-linux-gnu/qt6/plugins/kf6/kio/mtp\.so' "image assembly requires Dolphin MTP integration"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" 'chmod 0755 "\$REMOVABLE_STORAGE_HEALTH"' "removable-storage verifier gets executable production permissions"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" 'chmod 0644 "\$REMOVABLE_STORAGE_SERVICE" "\$REMOVABLE_STORAGE_DOCUMENTATION"' "removable-storage policy files are immutable image content"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" 'PRINTING_CONFIG=/etc/cups/cupsd\.conf' "image assembly uses the fixed local-only CUPS policy"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" 'IPP_USB_CONFIG=/etc/ipp-usb/ipp-usb\.conf' "image assembly uses the fixed loopback USB policy"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" '/usr/lib/cups/backend/ipp.*backend/ipps' "image assembly requires both driverless IPP backends"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" '/usr/lib/cups/filter/pdftopdf.*pdftoraster' "image assembly requires the PDF/raster print filter chain"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" 'CUPS_PK_HELPER=/usr/libexec/cups-pk-helper-mechanism' "image assembly requires the CUPS PolicyKit mechanism"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" 'PRINTING_KCM=/usr/lib/x86_64-linux-gnu/qt6/plugins/plasma/kcms/systemsettings/kcm_printer_manager\.so' "image assembly requires the KDE printer KCM"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" 'chmod 0755 "\$PRINTING_POLICY" "\$PRINTING_HEALTH"' "printing verifiers get executable production permissions"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" 'chmod 0644 "\$PRINTING_CONFIG" "\$IPP_USB_CONFIG"' "printing policies are immutable image content"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" '/usr/sbin/cupsd -t -c "\$PRINTING_CONFIG"' "image assembly asks CUPS to parse the shipped policy"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" 'install -d -m 0710 -o root -g lp /var/spool/cups' "finished image prepares a private CUPS spool on var"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" 'SCANNING_CONFIG=/etc/sane\.d/airscan\.conf' "image assembly uses the fixed on-demand AirScan policy"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" 'AIRSCAN_BACKEND=/usr/lib/x86_64-linux-gnu/sane/libsane-airscan\.so\.1' "image assembly requires the AirScan SANE backend"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" '/usr/bin/scanimage.*usr/bin/sane-find-scanner' "image assembly requires the native SANE clients"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" '/usr/bin/airscan-discover.*usr/bin/skanpage' "image assembly requires driverless discovery and the KDE scanner UI"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" 'grep -Fxq airscan /etc/sane\.d/dll\.d/airscan' "image assembly verifies AirScan backend registration"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" 'getent group scanner' "image assembly requires the USB scanner authorization group"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" 'chmod 0755 "\$SCANNING_POLICY" "\$SCANNING_HEALTH"' "scanning verifiers get executable production permissions"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" 'chmod 0644 "\$SCANNING_CONFIG" "\$SCANNING_SERVICE" "\$SCANNING_DOCUMENTATION"' "scanning policy files are immutable image content"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" 'CORE_APPS_CONFIG=/etc/xdg/mimeapps\.list' "image assembly uses the signed-root XDG defaults"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" '^CORE_APP_EXECUTABLES=\($' "image assembly checks the complete core application executable set"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" '^CORE_APP_DESKTOPS=\($' "image assembly checks the complete core application desktop set"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" '/usr/bin/desktop-file-validate "\$desktop_file"' "image assembly validates every core application desktop entry"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" 'CORE_APPS_SESSION_SMOKE=/usr/lib/echo-os/echo-core-apps-session-smoke\.py' "image assembly tracks the functional core-app session diagnostic"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" 'chmod 0755 "\$CORE_APPS_POLICY" "\$CORE_APPS_HEALTH" "\$CORE_APPS_SESSION_SMOKE"' "core-app verifiers get executable production permissions"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" 'chmod 0644 "\$CORE_APPS_CONFIG" "\$CORE_APPS_SERVICE" "\$CORE_APPS_DOCUMENTATION"' "core-app policy files are immutable image content"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" 'chown root:root "\$SESSION_LOCK" "\$SCREEN_LOCKER" "\$KWIN_WINDOW_BRIDGE"' "security-sensitive session helpers are root-owned"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" 'chmod 0644 "\$KWIN_BRIDGE_METADATA" "\$KWIN_BRIDGE_SCRIPT"' "KWin script package is immutable to the session user"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" "python3 -c 'import dbus; from gi.repository import GLib'" "image build imports the KWin bridge runtime bindings"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" "python3 -c 'import echo_notification_store'" "image build imports the native notification history module"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" 'systemd-cryptenroll' "finished image requires the TPM2 enrollment runtime"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" 'chmod 0755 "\$DATA_PROTECTION"' "data-protection enrollment is executable and root-owned"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" '"\$KEYRING_VERIFIER" "\$UPDATE_CHANNEL_COMMAND" "\$UPDATE_APPLY_HELPER"' "channel coordinator and fixed apply helper get executable production permissions"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" '"\$UPDATE_TRUST_TOOL" verify-system' "finished root binds its trust policy to the embedded keyring"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" '/var/cache/echo-os/updates' "finished image pre-creates the private authenticated-update cache"
require_pattern "$REPO_ROOT/deploy/data-protection/echo_data_protection.py" '^PARTITIONS = \("echo-var", "echo-swap", "echo-home"\)$' "data protection can target only the fixed mutable partitions"
require_pattern "$REPO_ROOT/deploy/data-protection/echo_data_protection.py" '"--tpm2-pcrs="' "TPM enrollment avoids a version-specific direct PCR binding"
require_pattern "$REPO_ROOT/deploy/data-protection/echo_data_protection.py" 'f"--tpm2-public-key-pcrs=\{SIGNED_PCRS\}"' "TPM enrollment uses the signed PCR policy"
require_pattern "$REPO_ROOT/deploy/data-protection/echo_data_protection.py" '^SIGNED_PCRS = "11"$' "signed PCR 11 binds unlock to an authorized UKI"
forbid_pattern "$REPO_ROOT/deploy/data-protection/echo_data_protection.py" 'tpm2-pcrs=7|tpm2=pcr7' "data protection never binds an installed device to one PCR 7 value"
require_pattern "$REPO_ROOT/deploy/data-protection/echo_data_protection.py" '"--wipe-slot=tpm2"' "TPM2 re-enrollment replaces stale TPM tokens"
require_pattern "$REPO_ROOT/deploy/data-protection/echo_data_protection.py" '"luksRemoveKey"' "factory credentials are removed after production enrollment"
forbid_pattern "$REPO_ROOT/deploy/data-protection/echo_data_protection.py" 'shell=True|os\.system|subprocess\.(call|run)\([^\n]*shell' "data-protection commands never evaluate secret or device text in a shell"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'test_echo_data_protection\.py' "image CI tests the per-device enrollment transaction"
require_pattern "$REPO_ROOT/.github/workflows/ab-update-smoke.yml" 'test_echo_data_protection\.py' "A/B CI tests the per-device enrollment transaction"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'test_verify_uki_pcr_policy\.py' "image CI tests the signed-PCR UKI verifier"
require_pattern "$REPO_ROOT/.github/workflows/ab-update-smoke.yml" 'test_verify_uki_pcr_policy\.py' "A/B CI tests the signed-PCR UKI verifier"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" "unittest discover -s deploy/update -p '\*test\*\.py'" "image CI discovers every update and dm-verity policy test"
require_pattern "$REPO_ROOT/.github/workflows/ab-update-smoke.yml" "unittest discover -s deploy/update -p '\*test\*\.py'" "A/B CI discovers every update and dm-verity policy test"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'initramfs-tools-core' "image CI can inspect the selected custom initrd"
require_pattern "$REPO_ROOT/.github/workflows/ab-update-smoke.yml" 'initramfs-tools-core' "A/B CI can inspect the selected custom initrd"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'prepare-agent-bundle\.sh' "image CI builds the unified Echo bundle before mkosi"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'delivery_source_preflight\.py' "image CI verifies the unified source identity"
forbid_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'ECHO_AGENT_READ_TOKEN|checkout-source|verify-source-lock|\.\./echo-agent' "image CI cannot depend on a second Agent repository"
require_pattern "$REPO_ROOT/.github/workflows/ab-update-smoke.yml" 'prepare-agent-bundle\.sh' "A/B CI builds the unified Echo bundle before both roots"
require_pattern "$REPO_ROOT/.github/workflows/ab-update-smoke.yml" 'delivery_source_preflight\.py' "A/B CI verifies the unified source identity"
require_pattern "$REPO_ROOT/.github/workflows/ab-update-smoke.yml" '^  source-contract:$' "A/B pull requests retain a portable source-contract job"
require_pattern "$REPO_ROOT/.github/workflows/ab-update-smoke.yml" '^      github\.event_name != '\''pull_request'\'' &&$' "privileged A/B execution excludes untrusted pull requests"
require_pattern "$REPO_ROOT/.github/workflows/ab-update-smoke.yml" '^      \(github\.ref == '\''refs/heads/os-main'\'' \|\| github\.ref == '\''refs/heads/main'\''\)$' "privileged A/B execution accepts only trusted delivery branches"
require_pattern "$REPO_ROOT/.github/workflows/ab-update-smoke.yml" '^    runs-on: \[self-hosted, linux, x64, echo-os-image\]$' "trusted A/B runs require the dedicated self-hosted image runner"
forbid_pattern "$REPO_ROOT/.github/workflows/ab-update-smoke.yml" 'vars\.ECHO_OS_IMAGE_RUNNER' "trusted A/B runs cannot fall back to a hosted runner"
require_pattern "$REPO_ROOT/.github/workflows/ab-update-smoke.yml" 'test_verify_linux_image_runner_host\.py' "A/B pull requests test the runner host contract"
require_pattern "$REPO_ROOT/.github/workflows/ab-update-smoke.yml" '^      TMPDIR: /__w/_temp$' "A/B image temporaries stay on the measured container scratch"
require_pattern "$REPO_ROOT/.github/workflows/ab-update-smoke.yml" 'verify-linux-image-runner\.py' "A/B CI fails before building on an undersized runner"
require_pattern "$REPO_ROOT/.github/workflows/ab-update-smoke.yml" 'echo-image-runner-preflight\.log' "A/B CI retains runner-preflight evidence"
forbid_pattern "$REPO_ROOT/.github/workflows/ab-update-smoke.yml" 'ECHO_AGENT_READ_TOKEN|checkout-source|verify-source-lock|\.\./echo-agent' "A/B CI cannot depend on a second Agent repository"
forbid_pattern "$REPO_ROOT/.github/workflows/appliance-release.yml" 'ECHO_AGENT_READ_TOKEN|checkout-source|verify-source-lock|\.\./echo-agent' "appliance release cannot depend on a second Agent repository"
require_pattern "$REPO_ROOT/.github/workflows/ci.yml" '^  public-source-contract:$' "general pull requests retain a unified source contract"
require_pattern "$REPO_ROOT/.github/workflows/ci.yml" 'run_public_source_tests\.py' "general PR CI runs the OS-owned release and delivery test slice"
require_pattern "$REPO_ROOT/.github/workflows/ci.yml" 'test_unified_echo_workflow_policy\.py' "general CI tests the unified Echo workflow policy"
require_pattern "$REPO_ROOT/deploy/appliance/run_public_source_tests.py" '^EMBEDDED_RUNTIME_TESTS = \($' "public CI explicitly classifies embedded runtime coverage"
require_pattern "$REPO_ROOT/deploy/appliance/run_public_source_tests.py" '^    if discovered != classified:$' "public CI rejects every unclassified or stale appliance test file"
require_pattern "$REPO_ROOT/deploy/appliance/run_public_source_tests.py" '^        if not \(REPO_ROOT / relative\)\.is_file\(\) or \(REPO_ROOT / relative\)\.is_symlink\(\)$' "public CI accepts only real non-symlink classified test files"
require_pattern "$REPO_ROOT/deploy/appliance/operations_bundle.py" 'protocolInteroperabilityLab' "operations bundle publishes the real-client SMB/NFS lab entrypoint"
require_pattern "$REPO_ROOT/deploy/appliance/operations_bundle.py" 'nasDataBackup' "operations bundle publishes encrypted NAS data backup and empty-volume restore"
require_pattern "$REPO_ROOT/deploy/appliance/nas_data_backup.py" '^RENAME_EXCHANGE = 2$' "NAS data restore promotes one complete tree atomically"
require_pattern "$REPO_ROOT/deploy/appliance/nas_data_backup.py" '"check", "--read-data"' "NAS data backup authenticates repository contents with a full read"
require_pattern "$REPO_ROOT/deploy/appliance/nas_data_backup.py" 'NAS backup source must be a read-only mounted snapshot' "NAS data backup rejects a live writable source"
require_pattern "$REPO_ROOT/deploy/appliance/operations_bundle.py" 'upgradeRecovery' "operations bundle publishes crash-interrupted upgrade recovery"
require_pattern "$REPO_ROOT/deploy/appliance/operations_bundle.py" 'powerStateRecoveryLab' "operations bundle publishes the physical power/state recovery lab"
require_pattern "$REPO_ROOT/deploy/appliance/operations_bundle.py" 'bareMetalRecoveryLab' "operations bundle publishes the destructive bare-metal recovery lab"
require_pattern "$REPO_ROOT/deploy/appliance/operations_bundle.py" 'paperlessFunctionalLab' "operations bundle publishes the Paperless OCR and Office functional lab"
require_pattern "$REPO_ROOT/deploy/appliance/operations_bundle.py" 'lanDiscoveryFunctionalLab' "operations bundle publishes the real LAN discovery functional lab"
require_pattern "$REPO_ROOT/deploy/appliance/physical_acceptance.py" 'PAPERLESS_FUNCTIONAL_RESULT_NAME' "device gates require candidate-bound Paperless functional evidence"
require_pattern "$REPO_ROOT/deploy/appliance/hub_lifecycle_lab.py" 'private-paperless-secret-output' "Hub lifecycle safely hands Paperless first-install credentials to the functional lab"
require_pattern "$REPO_ROOT/deploy/appliance/paperless_functional_lab.py" 'password-file' "Paperless functional lab consumes the private candidate-bound credential file"
require_pattern "$REPO_ROOT/tests/appliance/test_physical_acceptance_capture.py" '^def test_device_manifest_rejects_rehashed_but_forged_paperless_result\(' "device manifest rejects forged Paperless OCR and Office evidence"
require_pattern "$REPO_ROOT/tests/appliance/test_physical_acceptance.py" '^def test_device_gate_rejects_rehashed_lan_probe_that_differs_from_result\(' "device gate rejects rehashed LAN probes that differ from the combined result"
require_pattern "$REPO_ROOT/deploy/appliance/bare_metal_recovery_lab.py" '^PHASES = \($' "bare-metal recovery requires an ordered multi-boot physical lifecycle"
require_pattern "$REPO_ROOT/deploy/appliance/physical_acceptance.py" 'BARE_METAL_LIFECYCLE_NAME' "physical acceptance requires machine-generated bare-metal recovery evidence"
require_pattern "$REPO_ROOT/deploy/appliance/physical_acceptance_capture.py" 'subparsers\.add_parser\("bare-metal-result"\)' "physical capture derives G6 checks from fixed bare-metal phase logs"
require_pattern "$REPO_ROOT/tests/appliance/test_physical_acceptance_capture.py" '^def test_bare_metal_result_and_manifest_bind_all_eight_destructive_phases\(' "G6 capture and final manifest are tested as one eight-phase chain"
require_pattern "$REPO_ROOT/tests/appliance/test_physical_acceptance_capture.py" '^def test_bare_metal_result_rejects_a_forged_final_nas_canary\(' "G6 rejects forged restored NAS evidence"
require_pattern "$REPO_ROOT/deploy/appliance/physical_acceptance.py" 'POWER_STATE_LIFECYCLE_NAME' "physical acceptance requires machine-generated power/state lifecycle evidence"
require_pattern "$REPO_ROOT/deploy/appliance/upgrade-appliance.sh" 'upgrade_transaction\.py' "appliance upgrades use a durable selection transaction"
require_pattern "$REPO_ROOT/deploy/appliance/upgrade-appliance.sh" 'finish-recovery' "failed upgrades commit recovery only after the previous release is healthy"
require_pattern "$REPO_ROOT/deploy/appliance/operations_systemd.py" 'RECOVERY_SERVICE_NAME = "echo-appliance-upgrade-recovery\.service"' "operations systemd manages boot-time upgrade recovery"
require_pattern "$REPO_ROOT/deploy/appliance/operations_systemd.py" 'ENABLED_UNIT_NAMES = \(RECOVERY_SERVICE_NAME, \*TIMER_NAMES\)' "upgrade recovery and both operations timers share transactional enablement"
require_pattern "$REPO_ROOT/deploy/appliance/systemd/echo-appliance-upgrade-recovery.service.example" '^ConditionPathExists=/opt/echo-os/deploy/appliance/\.echo-upgrade-transaction\.json$' "upgrade recovery runs only for a durable pending transaction"
require_pattern "$REPO_ROOT/deploy/appliance/physical_acceptance.py" 'PROTOCOL_INTEROPERABILITY_LIFECYCLE_NAME' "physical acceptance requires machine-generated protocol lifecycle evidence"
require_pattern "$REPO_ROOT/deploy/appliance/physical_acceptance_capture.py" '"schemaVersion": 17' "six-gate plans advertise every machine-bound physical lifecycle contract"
require_pattern "$REPO_ROOT/deploy/appliance/delivery_source_preflight.py" '^PREFLIGHT_CHECK_CODES = \($' "delivery preflight publishes an explicit check-code contract"
require_pattern "$REPO_ROOT/deploy/appliance/delivery_source_preflight.py" '^    if emitted_codes != PREFLIGHT_CHECK_CODES:$' "delivery preflight rejects drift from its published check-code contract"
require_pattern "$REPO_ROOT/deploy/appliance/delivery_source_preflight.py" '"runtime/__init__\.py"' "delivery preflight requires the embedded Agent runtime"
forbid_pattern "$REPO_ROOT/.github/workflows/delivery-release-candidate.yml" 'ECHO_AGENT_READ_TOKEN|checkout-source|verify-source-lock|\.\./echo-agent' "release-candidate verification uses the unified Echo source"
require_pattern "$REPO_ROOT/deploy/appliance/release_evidence_index.py" '^    "git_repository",$' "release evidence accepts the repository-identity preflight check"
require_pattern "$REPO_ROOT/tests/appliance/test_release_evidence_index.py" '^def test_release_index_accepts_the_exact_source_preflight_check_contract\(\) -> None:$' "release evidence tests the exact delivery-preflight check contract"
forbid_pattern "$REPO_ROOT/.github/workflows/ci.yml" 'ECHO_AGENT_READ_TOKEN|checkout-source|verify-source-lock|\.\./echo-agent' "general CI cannot depend on a second Agent repository"
require_pattern "$REPO_ROOT/deploy/appliance/prepare-agent-bundle.sh" '^AGENT_SRC="\$OS_ROOT"$' "bundle capture starts from the current Echo repository"
require_pattern "$REPO_ROOT/deploy/appliance/prepare-agent-bundle.sh" '^export ECHO_BUNDLE_SOURCE="\$AGENT_SRC"$' "bundle sub-builds consume the frozen current-repository snapshot"
forbid_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'ECHO_AGENT_ALLOW_DIRTY' "whole-disk release CI cannot opt into a dirty Agent snapshot"
forbid_pattern "$REPO_ROOT/.github/workflows/ab-update-smoke.yml" 'ECHO_AGENT_ALLOW_DIRTY' "A/B release CI cannot opt into a dirty Agent snapshot"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'version: "0\.11\.25"' "image CI pins the Agent dependency resolver"
require_pattern "$REPO_ROOT/.github/workflows/ab-update-smoke.yml" 'version: "0\.11\.25"' "A/B CI pins the Agent dependency resolver"
forbid_pattern "$REPO_ROOT/deploy/desktop-session/echo-desktop.service" 'docker\.service|network-online\.target' "desktop boot is not gated on Docker or network availability"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-desktop.service" '^ConditionCredential=echo\.os\.ci-session$' "direct system desktop is restricted to VM credentials"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-desktop.service" '^LoadCredential=echo\.os\.ci-session$' "direct VM session receives its lock-test credential"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-desktop.service" '^Requires=echo-machine-identity-health\.service echo-network-state-prepare\.service echo-region-state-restore\.service$' "credential desktop requires stable machine, network and regional state"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-desktop-health.service" '^ConditionCredential=echo\.os\.ci-session$' "desktop blessing path is restricted to VM credentials"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-desktop-session.sh" 'ECHO_DESKTOP_READY' "native Shell emits a window-ready marker"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-desktop-session.sh" '^export XDG_CURRENT_DESKTOP=Echo:KDE$' "Echo selects its explicit KDE portal backend"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-desktop-session.sh" '/var/lib/flatpak/exports/share' "system Flatpak launchers are visible to the shell"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-desktop-session.sh" 'flatpak/exports/share:' "per-user Flatpak launchers are visible to the shell"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-desktop-session.sh" '^LOCK_SERVICE=/usr/lib/echo-os/echo-session-lock$' "production session uses the packaged lock coordinator"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-desktop-session.sh" '^export ECHO_LOCK_SCREEN_READY=1$' "native system actions open only after the lock coordinator is alive"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-desktop-session.sh" '/usr/bin/loginctl lock-session self' "credential-gated boot smoke requests a real logind lock"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-desktop-session.sh" 'Echo OS lock service failed; terminating the graphical session' "desktop fails closed if its lock coordinator exits"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-desktop-session.sh" '^KWIN_BRIDGE_SERVICE=/usr/lib/echo-os/echo-kwin-window-bridge$' "production session uses the packaged compositor bridge"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-desktop-session.sh" 'ECHO_KWIN_COMPOSITOR_BRIDGE_READY provider=kwin-script transport=private-socket' "session waits for KWin to publish its first UUID snapshot"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-desktop-session.sh" 'Echo OS KWin window bridge failed; terminating the graphical session' "desktop fails closed if its compositor bridge exits"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-session-lock" '^  XSS_LOCK_BIN=/usr/bin/xss-lock$' "production idle coordinator path is fixed"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-session-lock" '^"\$XSET_BIN" s "\$IDLE_SECONDS" "\$IDLE_SECONDS"$' "X11 idle timeout is configured"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-session-lock" 'XSECURELOCK_PAM_SERVICE=echo-lock' "screen locker selects the dedicated PAM policy"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-session-lock" '^exec "\$XSS_LOCK_BIN" --transfer-sleep-lock -- "\$SCREEN_LOCKER"$' "idle and sleep locks are coordinated through xss-lock"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-screen-locker" '^  XSECURELOCK_BIN=/usr/bin/xsecurelock$' "production PAM locker path is fixed"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-screen-locker" 'XSS_SLEEP_LOCK_FD' "locker delays sleep only while establishing the lock"
forbid_pattern "$REPO_ROOT/deploy/desktop-session/echo-session-lock" '/bin/sh|sh -c|eval ' "lock coordinator never evaluates command text"
forbid_pattern "$REPO_ROOT/deploy/desktop-session/echo-screen-locker" '/bin/sh|sh -c|eval ' "lock adapter never evaluates command text"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-lock.pam" '^@include common-auth$' "lock screen authenticates through the system PAM stack"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-lock.pam" '^@include common-account$' "lock screen applies system PAM account policy"
require_pattern "$REPO_ROOT/deploy/desktop-session/kwin-window-bridge/metadata.json" '"EnabledByDefault": true' "KWin loads the signed-root bridge package without mutable user setup"
require_pattern "$REPO_ROOT/deploy/desktop-session/kwin-liquid-glass-effect/CMakeLists.txt" 'INSTALL_NAMESPACE "kwin/effects/plugins"' "Liquid Glass installs through KWin's native effect namespace"
require_pattern "$REPO_ROOT/deploy/desktop-session/kwin-liquid-glass-effect/CMakeLists.txt" 'KWin::kwin' "Liquid Glass links only through KWin's exported compositor ABI"
require_pattern "$REPO_ROOT/deploy/desktop-session/kwin-liquid-glass-effect/src/metadata.json" '"Id": "org\.echoos\.liquidglass"' "native glass effect has one fixed plugin identity"
require_pattern "$REPO_ROOT/deploy/desktop-session/kwin-liquid-glass-effect/src/metadata.json" '"EnabledByDefault": true' "native glass effect is enabled by immutable package metadata"
require_pattern "$REPO_ROOT/deploy/desktop-session/kwin-liquid-glass-effect/src/echo_liquid_glass_effect.h" 'Q_CLASSINFO\("D-Bus Interface", "org\.echoos\.KWin\.LiquidGlass1"\)' "native glass control surface uses one fixed D-Bus interface"
require_pattern "$REPO_ROOT/deploy/desktop-session/kwin-liquid-glass-effect/src/echo_liquid_glass_effect.h" 'm_iterationCount = 2' "native glass blur cost has a fixed two-iteration bound"
require_pattern "$REPO_ROOT/deploy/desktop-session/kwin-liquid-glass-effect/src/echo_liquid_glass_effect.cpp" 'surfaces\.size\(\) > s_maxSurfaceCount' "native effect rejects unbounded surface lists"
require_pattern "$REPO_ROOT/deploy/desktop-session/kwin-liquid-glass-effect/src/echo_liquid_glass_effect.cpp" 'boundingArea > s_maxBlurBoundingArea' "native effect bounds aggregate compositor allocation"
require_pattern "$REPO_ROOT/deploy/desktop-session/kwin-liquid-glass-effect/src/echo_liquid_glass_effect.cpp" 'QPainterPath' "native effect creates rounded compositor regions"
require_pattern "$REPO_ROOT/deploy/desktop-session/kwin-liquid-glass-effect/src/echo_liquid_glass_effect.cpp" 'QDBusConnection::ExportScriptableSlots' "KWin exports only the declared glass control slots"
forbid_pattern "$REPO_ROOT/deploy/desktop-session/kwin-liquid-glass-effect/src/echo_liquid_glass_effect.cpp" 'QProcess|system\(|exec\(' "native effect has no process-launch surface"
require_pattern "$REPO_ROOT/deploy/desktop-session/kwin-window-bridge/contents/code/main.js" 'workspace\.stackingOrder' "KWin compositor stacking order is the Wayland window truth source"
require_pattern "$REPO_ROOT/deploy/desktop-session/kwin-window-bridge/contents/code/main.js" 'window\.internalId' "Wayland windows use KWin UUID identity"
require_pattern "$REPO_ROOT/deploy/desktop-session/kwin-window-bridge/contents/code/main.js" 'window\.desktopFileName' "Wayland application identity comes from KWin"
require_pattern "$REPO_ROOT/deploy/desktop-session/kwin-window-bridge/contents/code/main.js" 'window\.keepBelow = true' "KWin keeps the Echo desktop below ordinary applications"
require_pattern "$REPO_ROOT/deploy/desktop-session/kwin-window-bridge/contents/code/main.js" 'window\.onAllDesktops = true' "KWin owns the Echo shell desktop scope"
require_pattern "$REPO_ROOT/deploy/desktop-session/kwin-window-bridge/contents/code/main.js" 'workspace\.screens' "KWin publishes compositor-owned output topology"
require_pattern "$REPO_ROOT/deploy/desktop-session/kwin-window-bridge/contents/code/main.js" 'output\.devicePixelRatio' "KWin publishes compositor-owned output scale"
require_pattern "$REPO_ROOT/deploy/desktop-session/kwin-window-bridge/contents/code/main.js" 'window\.closeWindow\(\)' "KWin itself executes close actions"
require_pattern "$REPO_ROOT/deploy/desktop-session/kwin-window-bridge/contents/code/main.js" 'new QTimer\(\)' "KWin polls acknowledged actions without a command shell"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-kwin-window-bridge" '^VALID_ACTIONS = frozenset\(\{"focus", "minimize", "close"\}\)$' "compositor bridge actions are a fixed whitelist"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-kwin-window-bridge" 'os\.chmod\(socket_path, 0o600\)' "renderer-facing bridge socket is private to the session user"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-kwin-window-bridge" 'get_name_owner\("org\.kde\.KWin"\)' "D-Bus snapshots and acknowledgements are accepted only from KWin"
forbid_pattern "$REPO_ROOT/deploy/desktop-session/echo-kwin-window-bridge" 'subprocess|shell=True|os\.system' "compositor bridge never starts a command shell"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-notification-service" '^BUS_NAME = "org\.freedesktop\.Notifications"$' "Echo owns the standard native notification interface"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-notification-service" 'os\.chmod\(socket_path, 0o600\)' "notification UI bridge is private to the login user"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-notification-service" 'return \["body"\]' "notification daemon advertises only implemented FDO capabilities"
forbid_pattern "$REPO_ROOT/deploy/desktop-session/echo-notification-service" 'subprocess|shell=True|os\.system' "notification daemon never launches or evaluates commands"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo_notification_store.py" '^MAX_NOTIFICATIONS = 100$' "notification history has a fixed memory bound"
require_pattern "$REPO_ROOT/deploy/system-health/echo-coredump.conf" '^Storage=external$' "process cores use bounded external storage"
require_pattern "$REPO_ROOT/deploy/system-health/echo-coredump.conf" '^Compress=yes$' "local process cores are compressed"
require_pattern "$REPO_ROOT/deploy/system-health/echo-coredump.conf" '^ProcessSizeMax=512M$' "per-process crash handling is bounded"
require_pattern "$REPO_ROOT/deploy/system-health/echo-coredump.conf" '^ExternalSizeMax=512M$' "each stored process core is bounded"
require_pattern "$REPO_ROOT/deploy/system-health/echo-coredump.conf" '^MaxUse=1G$' "aggregate process-core storage is bounded"
require_pattern "$REPO_ROOT/deploy/system-health/echo-coredump.conf" '^KeepFree=2G$' "crash collection preserves a fixed free-space reserve"
require_pattern "$REPO_ROOT/deploy/system-health/echo-coredump.conf" '^EnterNamespace=no$' "crash collection does not enter an application namespace"
require_pattern "$REPO_ROOT/deploy/system-health/echo-crash-health.service" '^Requires=systemd-coredump\.socket$' "crash health requires the native coredump socket"
require_pattern "$REPO_ROOT/deploy/system-health/echo-crash-health.service" '^RequiresMountsFor=/var$' "crash health waits for persistent var"
require_pattern "$REPO_ROOT/deploy/system-health/echo-crash-health.service" '^Before=boot-complete\.target$' "crash health runs before boot blessing"
require_pattern "$REPO_ROOT/deploy/system-health/echo-crash-health.service" '^RequiredBy=boot-complete\.target$' "boot completion requires bounded crash collection"
require_pattern "$REPO_ROOT/deploy/system-health/echo-crash-health" '/dev/mapper/echo-var' "crash health proves that coredumps use encrypted var"
require_pattern "$REPO_ROOT/deploy/system-health/echo-crash-health" 'systemd-analyze cat-config systemd/coredump\.conf' "crash health validates the effective systemd policy"
require_pattern "$REPO_ROOT/deploy/system-health/echo-crash-health" 'systemctl is-active --quiet "\$COREDUMP_SOCKET"' "crash health validates native socket activation"
require_pattern "$REPO_ROOT/deploy/system-health/echo-crash-health" 'ECHO_CRASH_COLLECTION_READY provider=systemd-coredump storage=encrypted-var max-use=1G keep-free=2G' "crash health publishes a privacy-safe readiness record"
forbid_pattern "$REPO_ROOT/deploy/system-health/echo-crash-health" 'https?://|curl|wget|upload|telemetry|nc[[:space:]]' "crash health has no network or automatic upload path"
require_pattern "$REPO_ROOT/deploy/desktop-session/setup-desktop-session.sh" 'python3-dbus python3-gi systemd-coredump' "manual desktop installation includes the native coredump provider"
require_pattern "$REPO_ROOT/deploy/desktop-session/setup-desktop-session.sh" 'echo-crash-health\.service' "manual desktop installation enables the crash health gate"
require_pattern "$REPO_ROOT/frontend/electron/system-notifications.cjs" 'expected = path\.join\(runtimeDir, "echo-os", "notifications\.sock"\)' "Electron accepts only the session-private notification socket"
forbid_pattern "$REPO_ROOT/frontend/electron/system-notifications.cjs" 'exec\(|spawn\(|/bin/sh|sh -c' "notification renderer bridge never invokes a command shell"
require_pattern "$REPO_ROOT/frontend/src/app/desktop/page.tsx" 'notifications=\{nativeNotifications\}' "notification center renders the native session history"
require_pattern "$REPO_ROOT/.github/workflows/desktop-session-smoke.yml" 'system-notifications\.test\.cjs' "desktop CI tests the private Electron notification protocol"
require_pattern "$REPO_ROOT/.github/workflows/desktop-session-smoke.yml" 'test_echo_notification_store\.py' "desktop CI tests bounded notification history"
require_pattern "$REPO_ROOT/.github/workflows/desktop-session-smoke.yml" 'test-echo-notification-service\.sh' "desktop CI exercises the native D-Bus notification service"
require_pattern "$REPO_ROOT/.github/workflows/desktop-session-smoke.yml" 'test-echo-coredump-policy\.sh' "desktop CI tests bounded local crash collection"
require_pattern "$REPO_ROOT/deploy/network-security/firewalld.conf" '^DefaultZone=echo-public$' "fresh systems start in the closed Echo public zone"
require_pattern "$REPO_ROOT/deploy/network-security/firewalld.conf" '^CleanupOnExit=no$' "firewall rules remain fail-safe if the daemon stops"
require_pattern "$REPO_ROOT/deploy/network-security/firewalld.conf" '^FirewallBackend=nftables$' "firewalld cannot fall back to the deprecated iptables backend"
require_pattern "$REPO_ROOT/deploy/network-security/firewalld.conf" '^ReloadPolicy=INPUT:DROP,FORWARD:DROP,OUTPUT:DROP$' "firewall reload drops new traffic instead of opening a transition window"
require_pattern "$REPO_ROOT/deploy/network-security/firewalld.conf" '^StrictForwardPorts=yes$' "container DNAT cannot implicitly bypass the host firewall"
require_pattern "$REPO_ROOT/deploy/network-security/firewalld.conf" '^NftablesTableOwner=yes$' "firewalld owns its nftables policy table exclusively"
require_pattern "$REPO_ROOT/deploy/network-security/echo-public.xml" '<service name="dhcpv6-client"/>' "vendor public zone allows only DHCPv6 client traffic"
forbid_pattern "$REPO_ROOT/deploy/network-security/echo-public.xml" '<port|<forward|<masquerade|<rule|name="ssh"|name="echo-agent"' "vendor public zone opens no service, port, forwarding or rich-rule surface"
require_pattern "$REPO_ROOT/deploy/network-security/echo_firewall_policy.py" '^CONFIG_INVARIANTS = \{' "firewall policy has one strict signed-root invariant set"
require_pattern "$REPO_ROOT/deploy/network-security/echo_firewall_policy.py" 'runtime permits an authorized default-zone change' "runtime policy distinguishes an authorized zone selection from immutable security invariants"
require_pattern "$REPO_ROOT/deploy/network-security/echo_firewall_policy.py" 'document type or entity' "vendor zone parsing rejects XML entity expansion"
require_pattern "$REPO_ROOT/deploy/network-security/echo-firewall-health" 'ECHO_FIREWALL_SOURCE_TEST.*USE-SOURCE-RUNTIME' "firewall runtime overrides require an explicit source-test sentinel"
require_pattern "$REPO_ROOT/deploy/network-security/echo-firewall-health" 'org\.fedoraproject\.FirewallD1' "firewall health requires the real system D-Bus owner"
require_pattern "$REPO_ROOT/deploy/network-security/echo-firewall-health" 'list table inet firewalld' "firewall health proves the nftables table is loaded"
require_pattern "$REPO_ROOT/deploy/network-security/echo-firewall-health" 'ECHO_FIREWALL_READY backend=nftables default-zone=%s inbound=%s forward=explicit' "firewall health emits a bounded readiness record"
require_pattern "$REPO_ROOT/deploy/network-security/echo-firewall-health.service" '^Requires=firewalld\.service$' "firewall health fails with its policy daemon"
require_pattern "$REPO_ROOT/deploy/network-security/echo-firewall-health.service" '^Before=NetworkManager\.service sddm\.service echo-desktop\.service boot-complete\.target$' "firewall policy loads before network, login, desktop and blessing"
require_pattern "$REPO_ROOT/deploy/network-security/echo-firewall-health.service" '^RequiredBy=NetworkManager\.service sddm\.service echo-desktop\.service boot-complete\.target$' "network, login, desktop and blessing require firewall health"
require_pattern "$REPO_ROOT/deploy/network-security/echo-firewall-health.service" '^CapabilityBoundingSet=CAP_NET_ADMIN$' "firewall health retains only the capability needed to inspect nftables"
require_pattern "$REPO_ROOT/deploy/network-security/test_echo_firewall_health.py" 'test_vendor_zone_rejects_open_service_port_protocol_or_rich_rule' "firewall coordinator tests reject runtime exposure"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'test_echo_firewall_policy\.py' "image CI tests the signed firewall baseline"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'test_echo_firewall_health\.py' "image CI tests the firewall runtime coordinator"
require_pattern "$REPO_ROOT/.github/workflows/desktop-session-smoke.yml" 'test_echo_firewall_policy\.py' "desktop CI tests the signed firewall baseline"
require_pattern "$REPO_ROOT/.github/workflows/ab-update-smoke.yml" 'test_echo_firewall_policy\.py' "A/B CI tests the signed firewall baseline before lifecycle execution"
require_pattern "$REPO_ROOT/deploy/removable-storage/echo-removable-storage-health" 'ECHO_REMOVABLE_STORAGE_SOURCE_TEST.*USE-SOURCE-RUNTIME' "removable-storage overrides require an explicit source-test sentinel"
require_pattern "$REPO_ROOT/deploy/removable-storage/echo-removable-storage-health" 'org\.freedesktop\.UDisks2' "removable-storage health requires the real system D-Bus owner"
require_pattern "$REPO_ROOT/deploy/removable-storage/echo-removable-storage-health" '"\$UDISKSCTL" status' "removable-storage health uses the read-only UDisks2 status API"
forbid_pattern "$REPO_ROOT/deploy/removable-storage/echo-removable-storage-health" 'udisksctl[[:space:]]+(mount|unmount|power-off|delete|format|loop-setup)' "boot health never mounts, unmounts, formats or detaches user media"
require_pattern "$REPO_ROOT/deploy/removable-storage/echo-removable-storage-health" 'ECHO_REMOVABLE_STORAGE_READY provider=udisks2 policy=polkit mount=on-demand filesystems=vfat,exfat,ntfs,ext4,btrfs,xfs portable=mtp' "removable-storage health emits one bounded readiness record"
require_pattern "$REPO_ROOT/deploy/removable-storage/echo-removable-storage-health.service" '^Requires=udisks2\.service$' "removable-storage health fails with its policy daemon"
require_pattern "$REPO_ROOT/deploy/removable-storage/echo-removable-storage-health.service" '^Before=sddm\.service echo-desktop\.service boot-complete\.target$' "removable storage is verified before login, desktop and blessing"
require_pattern "$REPO_ROOT/deploy/removable-storage/echo-removable-storage-health.service" '^RequiredBy=sddm\.service echo-desktop\.service boot-complete\.target$' "login, desktop and blessing require removable-storage health"
require_pattern "$REPO_ROOT/deploy/removable-storage/echo-removable-storage-health.service" '^PrivateDevices=yes$' "removable-storage health cannot mutate raw block devices"
require_pattern "$REPO_ROOT/.github/workflows/desktop-session-smoke.yml" 'deploy/removable-storage/\*\*' "desktop CI is triggered by removable-storage changes"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'deploy/removable-storage/\*\*' "image CI is triggered by removable-storage changes"
require_pattern "$REPO_ROOT/.github/workflows/ab-update-smoke.yml" 'deploy/removable-storage/\*\*' "A/B CI is triggered by removable-storage changes"
require_pattern "$REPO_ROOT/.github/workflows/desktop-session-smoke.yml" 'test_echo_removable_storage_health\.py' "desktop CI tests removable-storage policy"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'test_echo_removable_storage_health\.py' "image CI tests removable-storage policy"
require_pattern "$REPO_ROOT/.github/workflows/ab-update-smoke.yml" 'test_echo_removable_storage_health\.py' "A/B CI tests removable-storage policy"
require_pattern "$REPO_ROOT/deploy/printing/cupsd.conf" '^Listen localhost:631$' "CUPS listens only on the loopback IPP endpoint"
require_pattern "$REPO_ROOT/deploy/printing/cupsd.conf" '^Listen /run/cups/cups\.sock$' "CUPS exposes the standard local domain socket"
forbid_pattern "$REPO_ROOT/deploy/printing/cupsd.conf" '^(Port|SSLListen|ServerAlias|Include|Allow)[[:space:]]' "CUPS policy contains no wildcard listener, include or remote allow rule"
require_pattern "$REPO_ROOT/deploy/printing/cupsd.conf" '^Browsing No$' "automatic LAN printer browsing is disabled"
require_pattern "$REPO_ROOT/deploy/printing/cupsd.conf" '^DefaultShared No$' "new printers are not shared"
require_pattern "$REPO_ROOT/deploy/printing/cupsd.conf" '^WebInterface No$' "CUPS web administration is disabled"
require_pattern "$REPO_ROOT/deploy/printing/cupsd.conf" '^PreserveJobFiles No$' "submitted print payloads are not retained"
require_pattern "$REPO_ROOT/deploy/printing/cupsd.conf" '^PreserveJobHistory No$' "completed print history is not retained"
require_pattern "$REPO_ROOT/deploy/printing/ipp-usb.conf" '^interface = loopback$' "driverless USB proxy is loopback-only"
require_pattern "$REPO_ROOT/deploy/printing/ipp-usb.conf" '^(device-log|main-log|console-log) = error$' "driverless USB logging excludes payload traces"
require_pattern "$REPO_ROOT/deploy/printing/echo_printing_policy.py" '^SOURCE_TEST_SENTINEL = "USE-SOURCE-RUNTIME"$' "printing policy fixes one source-test sentinel value"
require_pattern "$REPO_ROOT/deploy/printing/echo_printing_policy.py" 'os\.environ\.get\("ECHO_PRINTING_SOURCE_TEST"\)' "printing policy overrides require the source-test sentinel"
require_pattern "$REPO_ROOT/deploy/printing/echo_printing_policy.py" 'listeners != \["localhost:631", "/run/cups/cups\.sock"\]' "printing policy parser enforces the exact listener pair"
require_pattern "$REPO_ROOT/deploy/printing/echo-printing-health" '"\$LPSTAT" -r' "printing health uses the read-only scheduler status API"
require_pattern "$REPO_ROOT/deploy/printing/echo-printing-health" '/dev/mapper/echo-var' "printing health requires encrypted spool storage"
forbid_pattern "$REPO_ROOT/deploy/printing/echo-printing-health" 'lpadmin[[:space:]]|[[:space:]]lp[[:space:]]|cancel[[:space:]]' "boot health never adds a printer, submits or cancels a job"
require_pattern "$REPO_ROOT/deploy/printing/echo-printing-health" 'ECHO_PRINTING_READY provider=cups transport=local-only auth=polkit driverless=ipp-usb retention=off storage=encrypted-var' "printing health emits one bounded readiness record"
require_pattern "$REPO_ROOT/deploy/printing/echo-printing-health.service" '^Requires=cups\.socket cups\.service$' "printing health fails with its scheduler"
require_pattern "$REPO_ROOT/deploy/printing/echo-printing-health.service" '^RequiresMountsFor=/var/spool/cups$' "printing health is bound to the persistent spool"
require_pattern "$REPO_ROOT/deploy/printing/echo-printing-health.service" '^Before=sddm\.service echo-desktop\.service boot-complete\.target$' "printing is verified before login, desktop and blessing"
require_pattern "$REPO_ROOT/deploy/printing/echo-printing-health.service" '^RequiredBy=sddm\.service echo-desktop\.service boot-complete\.target$' "login, desktop and blessing require printing health"
require_pattern "$REPO_ROOT/deploy/printing/echo-printing-health.service" '^PrivateDevices=yes$' "printing health cannot access raw printer devices"
require_pattern "$REPO_ROOT/.github/workflows/desktop-session-smoke.yml" 'deploy/printing/\*\*' "desktop CI is triggered by printing changes"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'deploy/printing/\*\*' "image CI is triggered by printing changes"
require_pattern "$REPO_ROOT/.github/workflows/ab-update-smoke.yml" 'deploy/printing/\*\*' "A/B CI is triggered by printing changes"
require_pattern "$REPO_ROOT/.github/workflows/desktop-session-smoke.yml" 'test_echo_printing_policy\.py' "desktop CI tests printing policy"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'test_echo_printing_health\.py' "image CI tests printing runtime health"
require_pattern "$REPO_ROOT/.github/workflows/ab-update-smoke.yml" 'test_echo_printing_health\.py' "A/B CI tests printing runtime health"
require_pattern "$REPO_ROOT/deploy/scanning/airscan.conf" '^discovery = enable$' "scanner discovery is available only when a SANE client enumerates devices"
require_pattern "$REPO_ROOT/deploy/scanning/airscan.conf" '^protocol = auto$' "driverless scanners negotiate eSCL or WSD"
require_pattern "$REPO_ROOT/deploy/scanning/airscan.conf" '^ws-discovery = fast$' "on-demand WSD discovery remains bounded"
require_pattern "$REPO_ROOT/deploy/scanning/airscan.conf" '^pretend-local = false$' "remote scanners cannot be re-exported as local devices"
require_pattern "$REPO_ROOT/deploy/scanning/airscan.conf" '^enable = false$' "AirScan console debugging is disabled"
require_pattern "$REPO_ROOT/deploy/scanning/airscan.conf" '^hexdump = false$' "scanner protocol payload hexdumps are disabled"
forbid_pattern "$REPO_ROOT/deploy/scanning/airscan.conf" '^[[:space:]]*trace[[:space:]]*=' "scanner payload traces have no configured destination"
require_pattern "$REPO_ROOT/deploy/scanning/echo_scanning_policy.py" '^SOURCE_TEST_SENTINEL = "USE-SOURCE-RUNTIME"$' "scanning policy fixes one source-test sentinel value"
require_pattern "$REPO_ROOT/deploy/scanning/echo_scanning_policy.py" 'os\.environ\.get\("ECHO_SCANNING_SOURCE_TEST"\)' "scanning policy overrides require the source-test sentinel"
require_pattern "$REPO_ROOT/deploy/scanning/echo-scanning-health" '"\$SCANIMAGE" --version' "scanning health loads SANE without enumerating a device"
forbid_pattern "$REPO_ROOT/deploy/scanning/echo-scanning-health" '"\$SCANIMAGE"[[:space:]]+(-L|--list-devices)' "boot health never enumerates attached or network scanners"
require_pattern "$REPO_ROOT/deploy/scanning/echo-scanning-health" 'is-enabled saned\.socket' "scanning health verifies that network scanner sharing is disabled"
require_pattern "$REPO_ROOT/deploy/scanning/echo-scanning-health" 'is-active --quiet saned\.socket' "scanning health rejects an active network scanner listener"
require_pattern "$REPO_ROOT/deploy/scanning/echo-scanning-health" 'ECHO_SCANNING_READY provider=sane frontend=skanpage usb=udev,ipp-usb network=airscan-on-demand sharing=off retention=user-owned' "scanning health emits one bounded readiness record"
require_pattern "$REPO_ROOT/deploy/scanning/echo-scanning-health.service" '^PrivateDevices=yes$' "scanning boot health cannot open USB scanner devices"
require_pattern "$REPO_ROOT/deploy/scanning/echo-scanning-health.service" '^PrivateNetwork=yes$' "scanning boot health cannot discover LAN scanners"
require_pattern "$REPO_ROOT/deploy/scanning/echo-scanning-health.service" '^Before=sddm\.service echo-desktop\.service boot-complete\.target$' "scanning is verified before login, desktop and blessing"
require_pattern "$REPO_ROOT/deploy/scanning/echo-scanning-health.service" '^RequiredBy=sddm\.service echo-desktop\.service boot-complete\.target$' "login, desktop and blessing require scanning health"
require_pattern "$REPO_ROOT/.github/workflows/desktop-session-smoke.yml" 'deploy/scanning/\*\*' "desktop CI is triggered by scanning changes"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'deploy/scanning/\*\*' "image CI is triggered by scanning changes"
require_pattern "$REPO_ROOT/.github/workflows/ab-update-smoke.yml" 'deploy/scanning/\*\*' "A/B CI is triggered by scanning changes"
require_pattern "$REPO_ROOT/.github/workflows/desktop-session-smoke.yml" 'test_echo_scanning_policy\.py' "desktop CI tests scanning policy"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'test_echo_scanning_health\.py' "image CI tests scanning runtime health"
require_pattern "$REPO_ROOT/.github/workflows/ab-update-smoke.yml" 'test_echo_scanning_health\.py' "A/B CI tests scanning runtime health"
require_pattern "$REPO_ROOT/deploy/core-apps/mimeapps.list" '^inode/directory=org\.kde\.dolphin\.desktop;$' "directories open in the native file manager"
require_pattern "$REPO_ROOT/deploy/core-apps/mimeapps.list" '^x-scheme-handler/https=firefox-esr\.desktop;$' "HTTPS links open in the native browser"
require_pattern "$REPO_ROOT/deploy/core-apps/mimeapps.list" '^application/pdf=org\.kde\.okular\.desktop;$' "PDF documents open in the native document viewer"
require_pattern "$REPO_ROOT/deploy/core-apps/mimeapps.list" '^image/png=org\.kde\.gwenview\.desktop;$' "PNG images open in the native image viewer"
require_pattern "$REPO_ROOT/deploy/core-apps/mimeapps.list" '^application/zip=org\.kde\.ark\.desktop;$' "ZIP archives open in the native archive manager"
require_pattern "$REPO_ROOT/deploy/core-apps/mimeapps.list" '^video/mp4=org\.kde\.haruna\.desktop;$' "MP4 media opens in the native media player"
forbid_pattern "$REPO_ROOT/deploy/core-apps/mimeapps.list" '^\[(Added|Removed) Associations\]$' "system defaults do not shadow or remove user-selected application associations"
require_pattern "$REPO_ROOT/deploy/core-apps/echo_core_apps_policy.py" '^SOURCE_TEST_SENTINEL = "USE-SOURCE-RUNTIME"$' "core-app policy fixes one source-test sentinel value"
require_pattern "$REPO_ROOT/deploy/core-apps/echo_core_apps_policy.py" 'os\.environ\.get\("ECHO_CORE_APPS_SOURCE_TEST"\)' "core-app policy overrides require the source-test sentinel"
require_pattern "$REPO_ROOT/deploy/core-apps/echo-core-apps-health" '^"\$PYTHON" "\$POLICY" >/dev/null' "core-app health validates the signed-root MIME policy"
require_pattern "$REPO_ROOT/deploy/core-apps/echo-core-apps-health" '^  "\$DESKTOP_FILE_VALIDATE" "\$desktop_file" >/dev/null' "core-app health validates installed desktop entries without launching them"
require_pattern "$REPO_ROOT/deploy/core-apps/echo-core-apps-health" 'ECHO_CORE_APPS_READY files=dolphin terminal=konsole browser=firefox text=kate documents=okular images=gwenview archives=ark media=haruna capture=spectacle calculator=kcalc defaults=xdg' "core-app health emits one bounded readiness record"
require_pattern "$REPO_ROOT/deploy/core-apps/echo-core-apps-health.service" '^PrivateDevices=yes$' "core-app boot health cannot access hardware devices"
require_pattern "$REPO_ROOT/deploy/core-apps/echo-core-apps-health.service" '^PrivateNetwork=yes$' "core-app boot health cannot use the network"
require_pattern "$REPO_ROOT/deploy/core-apps/echo-core-apps-health.service" '^ProtectHome=yes$' "core-app boot health cannot inspect user files"
require_pattern "$REPO_ROOT/deploy/core-apps/echo-core-apps-health.service" '^Before=sddm\.service echo-desktop\.service boot-complete\.target$' "core apps are verified before login, desktop and blessing"
require_pattern "$REPO_ROOT/deploy/core-apps/echo-core-apps-health.service" '^RequiredBy=sddm\.service echo-desktop\.service boot-complete\.target$' "login, desktop and blessing require core-app health"
require_pattern "$REPO_ROOT/.github/workflows/desktop-session-smoke.yml" 'deploy/core-apps/\*\*' "desktop CI is triggered by core-app changes"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'deploy/core-apps/\*\*' "image CI is triggered by core-app changes"
require_pattern "$REPO_ROOT/.github/workflows/ab-update-smoke.yml" 'deploy/core-apps/\*\*' "A/B CI is triggered by core-app changes"
require_pattern "$REPO_ROOT/.github/workflows/desktop-session-smoke.yml" 'test_echo_core_apps_policy\.py' "desktop CI tests core-app policy"
require_pattern "$REPO_ROOT/.github/workflows/desktop-session-smoke.yml" 'test_echo_core_apps_session_smoke\.py' "desktop CI tests the functional core-app session diagnostic"
require_pattern "$REPO_ROOT/.github/workflows/echo-wayland-gate.yml" 'python3 deploy/core-apps/test_echo_core_apps_session_smoke\.py' "isolated Wayland CI tests its compositor-specific core-app dependency boundary"
require_pattern "$REPO_ROOT/.github/workflows/echo-wayland-gate.yml" 'python3-pyatspi python3-dbus python3-gi' "isolated Wayland CI installs the accessibility and compositor D-Bus bindings"
require_pattern "$REPO_ROOT/.github/workflows/echo-wayland-gate.yml" 'build-essential cmake extra-cmake-modules kwin-dev' "isolated Wayland CI installs the native effect toolchain"
require_pattern "$REPO_ROOT/.github/workflows/echo-wayland-gate.yml" '-S deploy/desktop-session/kwin-liquid-glass-effect' "isolated Wayland CI compiles the native glass effect against Debian KWin"
require_pattern "$REPO_ROOT/.github/workflows/desktop-session-smoke.yml" '-S deploy/desktop-session/kwin-liquid-glass-effect' "desktop CI compiles the native glass effect before its Wayland smoke"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'test_echo_core_apps_health\.py' "image CI tests core-app runtime health"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'test_echo_core_apps_session_smoke\.py' "image CI tests the functional core-app session diagnostic"
require_pattern "$REPO_ROOT/.github/workflows/ab-update-smoke.yml" 'test_echo_core_apps_health\.py' "A/B CI tests core-app runtime health"
require_pattern "$REPO_ROOT/.github/workflows/ab-update-smoke.yml" 'test_echo_core_apps_session_smoke\.py' "A/B CI tests the functional core-app session diagnostic"
require_pattern "$REPO_ROOT/.github/workflows/desktop-session-smoke.yml" 'dolphin konsole firefox-esr kate okular gwenview ark haruna kde-spectacle kcalc' "desktop CI installs the real offline application set"
require_pattern "$REPO_ROOT/.github/workflows/desktop-session-smoke.yml" 'xdg-utils desktop-file-utils shared-mime-info 7zip bzip2 unar unzip zip' "desktop CI installs XDG resolution and Ark format backends"
require_pattern "$REPO_ROOT/.github/workflows/desktop-session-smoke.yml" 'dbus-daemon dbus-x11' "desktop CI can publish compositor variables to activated core applications"
require_pattern "$REPO_ROOT/deploy/core-apps/mimeapps.list" '^audio/vnd\.wave=org\.kde\.haruna\.desktop;$' "the standard WAV alias opens in the native media player"
require_pattern "$REPO_ROOT/deploy/core-apps/echo_core_apps_session_smoke.py" '^SOURCE_TEST_SENTINEL = "USE-EPHEMERAL-RUNTIME"$' "functional core-app launch requires an explicit CI sentinel"
for core_app_desktop in \
  org.kde.dolphin.desktop \
  firefox-esr.desktop \
  org.kde.kate.desktop \
  org.kde.okular.desktop \
  org.kde.gwenview.desktop \
  org.kde.ark.desktop \
  org.kde.haruna.desktop \
  org.kde.konsole.desktop \
  org.kde.kcalc.desktop; do
  require_pattern "$REPO_ROOT/deploy/core-apps/echo_core_apps_session_smoke.py" "$core_app_desktop" "functional core-app session covers $core_app_desktop"
done
require_pattern "$REPO_ROOT/deploy/core-apps/echo_core_apps_session_smoke.py" '^XDG_OPEN = Path\("/usr/bin/xdg-open"\)$' "functional core-app session enters through the system XDG opener"
require_pattern "$REPO_ROOT/deploy/core-apps/echo_core_apps_session_smoke.py" '^GIO = Path\("/usr/bin/gio"\)$' "functional desktop launch enters through GLib's production launcher"
require_pattern "$REPO_ROOT/deploy/core-apps/echo_core_apps_session_smoke.py" '^def required_session_executables\(session: str\) -> tuple\[Path, \.\.\.\]:$' "functional core-app dependencies are selected by compositor type"
require_pattern "$REPO_ROOT/deploy/core-apps/echo_core_apps_session_smoke.py" '^        return \(\*common, WMCTRL\)$' "X11 core-app diagnostics require the X11 window controller"
require_pattern "$REPO_ROOT/deploy/core-apps/echo_core_apps_session_smoke.py" '^        return \(\*common, KWIN_BRIDGE\)$' "Wayland core-app diagnostics require only the compositor-native bridge"
forbid_pattern "$REPO_ROOT/deploy/core-apps/echo_core_apps_session_smoke.py" '^    for executable in \(XDG_MIME, XDG_OPEN, GIO, DESKTOP_FILE_VALIDATE, WMCTRL, ZIP\):$' "Wayland core-app diagnostics do not inherit an unconditional X11 dependency"
require_pattern "$REPO_ROOT/deploy/core-apps/echo_core_apps_session_smoke.py" 'ThreadingHTTPServer\(\("127\.0\.0\.1", 0\)' "functional browser smoke uses an ephemeral IPv4 loopback endpoint"
forbid_pattern "$REPO_ROOT/deploy/core-apps/echo_core_apps_session_smoke.py" '0\.0\.0\.0|\[::\]' "functional browser smoke never exposes its fixture on a non-loopback listener"
require_pattern "$REPO_ROOT/deploy/core-apps/echo_core_apps_session_smoke.py" '\["query", "default", detected_mime\]' "functional core-app session resolves the detected MIME through system defaults"
require_pattern "$REPO_ROOT/deploy/core-apps/echo_core_apps_session_smoke.py" 'case\.filename\.lower\(\).*window\.get\("title"' "functional core-app session binds each native window to its fixed fixture name"
require_pattern "$REPO_ROOT/deploy/core-apps/echo_core_apps_session_smoke.py" 'provider\.close\(window_id\)' "functional core-app session closes the exact observed window"
require_pattern "$REPO_ROOT/deploy/core-apps/echo_core_apps_session_smoke.py" 'if window_id in opened_window_ids:' "functional core-app cleanup is limited to windows observed by this diagnostic"
require_pattern "$REPO_ROOT/deploy/core-apps/echo_core_apps_session_smoke.py" 'launch_arguments = \["launch", str\(safe_desktop_entry\(case\)\)\]' "functional terminal and calculator use fixed gio launch arguments"
require_pattern "$REPO_ROOT/deploy/core-apps/echo_core_apps_session_smoke.py" 'metadata\.st_uid != 0' "functional desktop launch accepts only immutable root-owned entries"
require_pattern "$REPO_ROOT/deploy/core-apps/echo_core_apps_session_smoke.py" 'tempfile\.mkdtemp\(prefix="core-apps-session-", dir=runtime\)' "functional fixtures stay below the private session runtime"
require_pattern "$REPO_ROOT/deploy/core-apps/echo_core_apps_session_smoke.py" 'ECHO_CORE_APPS_SESSION_READY session=' "functional core-app session emits one bounded readiness record"
forbid_pattern "$REPO_ROOT/deploy/core-apps/echo_core_apps_session_smoke.py" 'shell=True|/bin/sh|sh -c|eval\(' "functional core-app launch never evaluates a command shell"
require_pattern "$REPO_ROOT/deploy/desktop-session/smoke-desktop-session.sh" 'ECHO_CORE_APPS_SESSION_READY session=x11' "X11 CI requires nine real XDG/desktop-launched core application windows"
require_pattern "$REPO_ROOT/deploy/desktop-session/smoke-wayland-session-client.sh" 'ECHO_CORE_APPS_SESSION_READY session=wayland' "Wayland CI requires nine real XDG/desktop-launched core application windows"
require_pattern "$REPO_ROOT/.github/workflows/desktop-session-smoke.yml" 'test_verify_wayland_native_app_ipc\.py' "desktop CI unit-tests Wayland packaged-IPC window evidence"
require_pattern "$REPO_ROOT/deploy/desktop-session/verify_wayland_native_app_ipc.py" '^MAX_WINDOWS = 4096$' "Wayland IPC evidence bounds compositor window state"
require_pattern "$REPO_ROOT/deploy/desktop-session/verify_wayland_native_app_ipc.py" 'window\.pid > 0 and is_kcalc\(window\)' "Wayland IPC evidence requires a positive-PID KCalc identity"
require_pattern "$REPO_ROOT/deploy/desktop-session/verify_wayland_native_app_ipc.py" 'multiple new KCalc windows appeared' "Wayland IPC evidence rejects ambiguous KCalc windows"
require_pattern "$REPO_ROOT/deploy/desktop-session/smoke-wayland-session-client.sh" '^ECHO_NATIVE_APP_SMOKE_ID=org\.kde\.kcalc \\$' "Wayland CI requests only KCalc through packaged Echo IPC"
require_pattern "$REPO_ROOT/deploy/desktop-session/smoke-wayland-session-client.sh" '^  --ozone-platform=wayland$' "Wayland CI runs the packaged Echo renderer on the compositor-native backend"
require_pattern "$REPO_ROOT/deploy/desktop-session/smoke-wayland-session-client.sh" '^  WAYLAND_ECHO_ARGUMENTS\+=\(--no-sandbox\)$' "root-only container smoke explicitly selects Electron's CI sandbox exception"
require_pattern "$REPO_ROOT/deploy/desktop-session/smoke-wayland-session-client.sh" 'python3 "\$WAYLAND_IPC_WINDOW_HELPER" find' "Wayland CI validates KCalc against compositor-owned UUID evidence"
require_pattern "$REPO_ROOT/deploy/desktop-session/smoke-wayland-session-client.sh" '^"\$BRIDGE" --socket "\$BRIDGE_SOCKET" --request close \\$' "Wayland CI closes the exact IPC-launched KCalc window through KWin"
require_pattern "$REPO_ROOT/deploy/desktop-session/smoke-wayland-session-client.sh" '^echo "ECHO_NATIVE_APP_IPC_READY session=wayland app=org\.kde\.kcalc path=preload-ipc-gio result=zero-exit cleanup=closed"$' "Wayland CI emits the complete packaged preload-to-KWin result"
require_pattern "$REPO_ROOT/deploy/desktop-session/smoke-wayland-session.sh" 'ECHO_NATIVE_APP_IPC_READY session=wayland app=org\.kde\.kcalc path=preload-ipc-gio result=zero-exit cleanup=closed' "the outer KWin workflow requires the packaged Wayland IPC marker"
require_pattern "$REPO_ROOT/deploy/desktop-session/smoke-desktop-session.sh" 'dbus-update-activation-environment.*' "X11 CI publishes its allocated display to activated core applications"
require_pattern "$REPO_ROOT/deploy/desktop-session/smoke-wayland-session-client.sh" 'dbus-update-activation-environment.*' "Wayland CI publishes its compositor socket to activated core applications"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-desktop-session.sh" 'ECHO_CORE_APPS_SESSION_TEST=USE-EPHEMERAL-RUNTIME' "direct raw desktop runs the functional app matrix only under its VM credential"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-desktop-session.sh" 'timeout 360s "\$CORE_APPS_SESSION_SMOKE" --session x11' "direct raw desktop bounds the functional app matrix"
require_pattern "$IMAGE_DIR/verify-os-image-evidence.py" 'ECHO_CORE_APPS_SESSION_READY session=x11' "signed release evidence binds the functional core-app result"
require_pattern "$REPO_ROOT/.github/workflows/desktop-session-smoke.yml" 'fcitx5-chinese-addons' "desktop CI installs and exercises Chinese input support"
require_pattern "$REPO_ROOT/.github/workflows/desktop-session-smoke.yml" 'plasma-workspace libqt6sql6-sqlite python3-pyqt6\.qtqml xclip wl-clipboard' "desktop CI installs the real X11 and Wayland clipboard chain"
require_pattern "$REPO_ROOT/.github/workflows/desktop-session-smoke.yml" 'test_echo_clipboard_host\.py' "desktop CI tests the clipboard runtime-storage boundary"
require_pattern "$REPO_ROOT/.github/workflows/desktop-session-smoke.yml" 'at-spi2-core python3-pyatspi python3-xlib orca speech-dispatcher' "desktop CI installs the real session and greeter accessibility chain"
require_pattern "$REPO_ROOT/.github/workflows/desktop-session-smoke.yml" 'test_echo_accessibility_smoke\.py' "desktop CI tests the privacy-bounded tree probe"
require_pattern "$REPO_ROOT/.github/workflows/desktop-session-smoke.yml" 'test_echo_sddm_accessibility\.py' "desktop CI tests the bounded greeter shortcut policy"
require_pattern "$REPO_ROOT/deploy/desktop-session/smoke-desktop-session.sh" 'Fcitx5 registered the multilingual X11 input-method service' "X11 CI observes the real Fcitx5 session service"
require_pattern "$REPO_ROOT/deploy/desktop-session/smoke-wayland-session-client.sh" 'Fcitx5 registered the multilingual Wayland input-method service' "Wayland CI observes the real Fcitx5 session service"
require_pattern "$REPO_ROOT/deploy/desktop-session/setup-desktop-session.sh" 'fcitx5 fcitx5-chinese-addons fcitx5-frontend-gtk3 fcitx5-frontend-gtk4' "manual desktop installation includes Fcitx5 and Chinese input"
require_pattern "$REPO_ROOT/deploy/desktop-session/setup-desktop-session.sh" 'plasma-workspace libqt6sql6-sqlite python3-pyqt6\.qtqml xclip wl-clipboard' "manual desktop installation includes the real Klipper chain"
require_pattern "$REPO_ROOT/deploy/desktop-session/setup-desktop-session.sh" 'at-spi2-core python3-pyatspi orca speech-dispatcher' "manual desktop installation includes AT-SPI, Orca and speech"
require_pattern "$REPO_ROOT/frontend/electron/system-shell.cjs" 'execFileImpl\(' "desktop files are launched through GLib with observable completion"
forbid_pattern "$REPO_ROOT/frontend/electron/system-shell.cjs" 'execFileImpl\("/bin/sh"|shell: true|\["-c",' "application launch never evaluates desktop-file text in a shell"
require_pattern "$REPO_ROOT/frontend/electron/system-shell.cjs" 'timeout: LAUNCH_TIMEOUT_MS' "desktop application launch has a fixed completion timeout"
require_pattern "$REPO_ROOT/frontend/electron/system-shell.cjs" 'resolve\(\{ ok: false, error: launchErrorMessage\(error, stderr\) \}\)' "gio asynchronous and non-zero failures reach the renderer"
require_pattern "$REPO_ROOT/frontend/src/appliance/apps-native.ts" 'launchNativeApplication\(window\.echo\?\.apps, appId\)' "Dock launch awaits the production Electron IPC result"
require_pattern "$REPO_ROOT/frontend/src/app/desktop/page.tsx" 'onLaunchError: reportNativeAppLaunchError' "Dock launch failures are surfaced to the user"
require_pattern "$REPO_ROOT/frontend/src/appliance/apps.ts" 'export function applianceAppsForLibrary' "all launchable Hub applications have an unbounded library projection"
require_pattern "$REPO_ROOT/frontend/src/appliance/apps.ts" 'export function applianceAppsForDock' "the Hub application Dock projection is independently bounded"
require_pattern "$REPO_ROOT/frontend/src/appliance/apps.ts" '!isLocalAppHostname\(parsed\.hostname\)' "container Web UI labels cannot navigate the browser to an external host"
require_pattern "$REPO_ROOT/frontend/src/app/desktop/page.tsx" '\.\.\.libraryApplianceApps\.map' "Launchpad and Spotlight retain Hub applications beyond the Dock limit"
require_pattern "$REPO_ROOT/frontend/src/appliance/apps.test.ts" 'keeps every launchable Hub app in the library while bounding the Dock' "the ninth Hub application remains discoverable outside the Dock"
require_pattern "$REPO_ROOT/appliance/app_registry/catalog.py" 'parsed\.scheme\.casefold\(\) not in \{"http", "https"\}' "the backend rejects unsafe container Web UI label protocols"
require_pattern "$REPO_ROOT/frontend/src/appliance/hub-panel.tsx" 'onOpenDeviceApp\?: \(app: HubApp\) => void' "installed Hub cards expose the primary application-open action"
require_pattern "$REPO_ROOT/frontend/src/appliance/hub-panel.tsx" 'onStop: \(app: HubApp\) => void' "running Hub cards expose a protected stop action"
require_pattern "$REPO_ROOT/frontend/src/app/desktop/page.tsx" 'await stopApplianceApp\(app\.id, approval\.approvalToken\)' "Hub stop consumes the app-bound one-shot approval"
require_pattern "$REPO_ROOT/frontend/src/appliance/hub-panel.test.tsx" 'keeps open primary and routes stop through an all-service safety plan' "Hub installed-card lifecycle actions remain discoverable"
require_pattern "$REPO_ROOT/appliance/hub/catalog.py" '_VERSION = re\.compile' "Hub catalog versions follow a bounded explicit schema"
require_pattern "$REPO_ROOT/appliance/hub/docker_installer.py" '"sh\.echo\.hub\.version": app\.version' "single-container Hub installs persist their trusted catalog version"
require_pattern "$REPO_ROOT/appliance/hub/bundle_installer.py" '"sh\.echo\.hub\.version": app\.version' "multi-service Hub installs persist their trusted catalog version"
require_pattern "$REPO_ROOT/frontend/src/appliance/hub-panel.tsx" '`v\$\{installedVersion\} → v\$\{app\.version\}`' "Hub cards distinguish installed and available versions"
require_pattern "$REPO_ROOT/tests/appliance/test_hub.py" 'test_installed_version_projection_rejects_malformed_labels' "untrusted container version labels do not reach the Hub UI"
require_pattern "$REPO_ROOT/frontend/src/appliance/hub-panel.tsx" 'app\.installation\.installed\)\.length' "Hub derives the installed view from the live catalog projection"
require_pattern "$REPO_ROOT/frontend/src/appliance/hub-panel.tsx" 'app\.updateAvailable\)\.length' "Hub derives the update view from the live catalog projection"
require_pattern "$REPO_ROOT/frontend/src/appliance/hub-panel.test.tsx" 'groups installed applications and available updates without another data store' "Hub installed and update filters remain projection-only"
require_pattern "$REPO_ROOT/frontend/src/appliance/hub.ts" 'export async function fetchHubAppDetail' "Hub application details use the authenticated current-state endpoint"
require_pattern "$REPO_ROOT/frontend/src/appliance/hub.ts" 'body\.app\.id !== appId' "Hub application details reject a mismatched response identity"
require_pattern "$REPO_ROOT/frontend/src/appliance/hub-panel.tsx" 'title="端口与网络"' "Hub details explain the fixed public network scope"
require_pattern "$REPO_ROOT/frontend/src/appliance/hub-panel.tsx" 'title="存储权限"' "Hub details explain read-only and writable storage scope"
require_pattern "$REPO_ROOT/frontend/src/appliance/hub-panel.test.tsx" 'explains fixed ports, storage access and retention in one detail sheet' "Hub detail scope remains visible without nested card chrome"
require_pattern "$REPO_ROOT/appliance/hub/catalog.py" 'class HubContainerRuntime' "single-container Hub packages declare bounded runtime resources"
require_pattern "$REPO_ROOT/appliance/hub/catalog.py" 'IMAGE_STORAGE_SCHEMA = "echo\.hub\.image-storage\.v1"' "Hub catalog binds per-architecture OCI storage attestations"
require_pattern "$REPO_ROOT/appliance/hub/docker_installer.py" '"Memory": package\.runtime\.memory_mib \* 1024 \* 1024' "single-container Hub installs enforce their catalog memory ceiling"
require_pattern "$REPO_ROOT/appliance/hub/service.py" 'RESOURCE_PREFLIGHT_SCHEMA = "echo\.hub\.resource-preflight\.v1"' "Hub exposes one versioned resource-preflight contract"
require_pattern "$REPO_ROOT/appliance/hub/service.py" 'usage = shutil\.disk_usage\(self\._nas_root\)' "Hub resource preflight observes the configured NAS filesystem capacity"
require_pattern "$REPO_ROOT/appliance/docker_proxy.py" 'client\.docker_root_dir\(\) != expected_root' "Docker capacity is accepted only from the engine-bound data-root mount"
require_pattern "$REPO_ROOT/deploy/appliance/docker-compose.yml" '\$\{ECHO_DOCKER_DATA_ROOT:-/var/lib/docker\}:/run/echo-host/docker-data:ro' "Docker control receives one configurable read-only data-root observer"
require_pattern "$REPO_ROOT/frontend/src/appliance/hub-panel.tsx" 'function installResourceSummary' "Hub approval summarizes the current resource preflight"
require_pattern "$REPO_ROOT/frontend/src/appliance/hub-panel.tsx" '下载量来自受信 OCI 清单' "Hub approval distinguishes attested download bytes from conservative extraction reserve"
require_pattern "$REPO_ROOT/deploy/appliance/hub_oci_storage.py" 'def verify_catalog_storage' "release tooling recomputes OCI layer bytes from immutable digests"
require_pattern "$REPO_ROOT/.github/workflows/appliance-release.yml" 'python deploy/appliance/hub_oci_storage\.py' "appliance publication fails on stale Hub OCI storage metadata"
require_pattern "$REPO_ROOT/deploy/appliance/verify-running-appliance.py" 'def _assert_hub_resource_preflight' "running-appliance evidence checks authenticated Hub capacity and limits"
require_pattern "$REPO_ROOT/tests/appliance/test_hub.py" 'test_resource_preflight_reports_real_nas_capacity_without_exposing_path' "Hub capacity projection remains real and path-redacted"
require_pattern "$REPO_ROOT/tests/appliance/test_hub.py" 'test_image_storage_preflight_fails_closed_and_changes_plan_identity' "Hub blocks installation when Docker capacity is missing or insufficient"
require_pattern "$REPO_ROOT/frontend/src/appliance/hub-panel.test.tsx" 'names the exact occupied port instead of showing a generic install failure' "Hub identifies the concrete conflicting port"
require_pattern "$REPO_ROOT/frontend/package.json" 'node electron/native-app-ipc-smoke\.node-test\.cjs' "the normal Electron test command includes the native-app IPC diagnostic"
require_pattern "$REPO_ROOT/.github/workflows/desktop-session-smoke.yml" 'node electron/native-app-ipc-smoke\.node-test\.cjs' "desktop CI unit-tests the native-app IPC diagnostic"
require_pattern "$REPO_ROOT/frontend/electron/native-app-ipc-smoke.cjs" '^const SMOKE_APP_ID = "org\.kde\.kcalc";$' "native-app IPC diagnostic fixes the application identity"
require_pattern "$REPO_ROOT/frontend/electron/native-app-ipc-smoke.cjs" 'const apps = await bridge\.list\(\);' "native-app IPC diagnostic enters through the preload list bridge"
require_pattern "$REPO_ROOT/frontend/electron/native-app-ipc-smoke.cjs" 'return bridge\.launch\("org\.kde\.kcalc"\);' "native-app IPC diagnostic enters through the preload launch bridge"
require_pattern "$REPO_ROOT/frontend/electron/native-app-ipc-smoke.cjs" 'credentialDirectory !== "/run/credentials/echo-desktop\.service"' "production native-app IPC diagnostic accepts only the fixed systemd service credential directory"
require_pattern "$REPO_ROOT/frontend/electron/native-app-ipc-smoke.cjs" '^const ROOT_WAYLAND_REQUEST_PATH = "/etc/echo-os/wayland-native-app-ipc";$' "Wayland SDDM IPC authorization uses one fixed root request path"
require_pattern "$REPO_ROOT/frontend/electron/native-app-ipc-smoke.cjs" 'fs\.constants\.O_NOFOLLOW' "Wayland SDDM IPC request is opened without following symlinks"
require_pattern "$REPO_ROOT/frontend/electron/native-app-ipc-smoke.cjs" 'environment\.XDG_SESSION_TYPE === "wayland"' "root request can authorize only a Wayland desktop session"
require_pattern "$REPO_ROOT/frontend/electron/native-app-ipc-smoke.cjs" 'permissions !== 0o444' "Wayland SDDM IPC request must be root-owned read-only data"
require_pattern "$REPO_ROOT/frontend/electron/native-app-ipc-smoke.cjs" '^  const expectedPath = path\.join\($' "native-app IPC readiness derives from the session runtime root"
require_pattern "$REPO_ROOT/frontend/electron/native-app-ipc-smoke.cjs" '^    "native-app-ipc-ready",$' "native-app IPC readiness uses the fixed filename"
require_pattern "$REPO_ROOT/frontend/electron/native-app-ipc-smoke.cjs" '^  if \(configuredPath !== expectedPath\) \{$' "native-app IPC readiness rejects any non-canonical output path"
require_pattern "$REPO_ROOT/frontend/electron/native-app-ipc-smoke.cjs" 'mode: 0o600' "native-app IPC readiness is written privately"
require_pattern "$REPO_ROOT/frontend/electron/native-app-ipc-smoke.cjs" 'fs\.renameSync\(temporaryPath, expectedPath\)' "native-app IPC readiness is published atomically"
forbid_pattern "$REPO_ROOT/frontend/electron/native-app-ipc-smoke.cjs" 'child_process|exec\(|spawn\(|shell: true' "native-app IPC diagnostic cannot bypass the production preload bridge"
require_pattern "$REPO_ROOT/frontend/electron/main.cjs" 'nativeAppIpcSmoke\.runNativeAppIpcSmoke' "packaged Echo invokes the native-app IPC diagnostic after renderer load"
require_pattern "$REPO_ROOT/deploy/desktop-session/smoke-desktop-session.sh" '^ECHO_NATIVE_APP_SMOKE_ID=org\.kde\.kcalc \\$' "X11 CI requests only KCalc through packaged Echo IPC"
require_pattern "$REPO_ROOT/deploy/desktop-session/smoke-desktop-session.sh" '^echo "ECHO_NATIVE_APP_IPC_READY app=org\.kde\.kcalc path=preload-ipc-gio result=zero-exit cleanup=closed"$' "X11 CI observes and closes the preload-launched KCalc window"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-desktop-session.sh" '^  export ECHO_NATIVE_APP_SMOKE_ID=org\.kde\.kcalc$' "direct raw requests the fixed IPC diagnostic only in its credential branch"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-desktop-session.sh" '^  echo "ECHO_NATIVE_APP_IPC_READY app=org\.kde\.kcalc path=preload-ipc-gio result=zero-exit cleanup=closed"$' "direct raw publishes the observed and closed KCalc IPC result"
require_pattern "$IMAGE_DIR/verify-os-image-evidence.py" 'ECHO_NATIVE_APP_IPC_READY app=org\\\.kde\\\.kcalc path=preload-ipc-gio result=zero-exit cleanup=closed' "signed release evidence binds the packaged preload IPC result"
require_pattern "$IMAGE_DIR/verify-os-image-evidence.py" 'ECHO_NATIVE_APP_IPC_READY session=wayland app=org\\\.kde\\\.kcalc path=preload-ipc-gio result=zero-exit cleanup=closed' "signed release evidence binds the SDDM Wayland preload IPC result"
require_pattern "$IMAGE_DIR/verify-os-image-evidence.py" 'ECHO_IMAGE_RUNNER_READY arch=x86_64' "signed release evidence binds the dedicated runner preflight"
require_pattern "$IMAGE_DIR/mkosi.conf" 'verify_wayland_native_app_ipc\.py:/usr/lib/echo-os/verify-wayland-native-app-ipc\.py' "image packages the bounded Wayland compositor evidence helper"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-wayland-shell-session.sh" '^WAYLAND_IPC_REQUEST=/etc/echo-os/wayland-native-app-ipc$' "installed Wayland session accepts only the fixed root request"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-wayland-shell-session.sh" '^  echo "ECHO_NATIVE_APP_IPC_READY session=wayland app=org\.kde\.kcalc path=preload-ipc-gio result=zero-exit cleanup=closed"$' "installed Wayland session emits compositor-observed packaged IPC evidence"
forbid_pattern "$REPO_ROOT/deploy/desktop-session/echo-wayland-shell-session.sh" '^export ECHO_SMOKE=1$|^ECHO_SMOKE=1 ' "production Wayland SDDM session never enables the standalone auto-exit smoke mode"
require_pattern "$REPO_ROOT/frontend/src/appliance/apps-native.ts" '"systemsettings"' "desktop recognizes KDE System Settings by its freedesktop id"
require_pattern "$REPO_ROOT/frontend/src/app/desktop/page.tsx" 'findNativeSystemSettingsApp\(apps\)' "System Settings Dock action resolves the installed native application"
require_pattern "$REPO_ROOT/frontend/src/app/desktop/page.tsx" 'nativeAppsApi\.launch\(settingsApp\.id\)' "System Settings Dock action launches the enumerated desktop file"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-desktop-session.sh" 'ECHO_AUTH_AGENT_READY provider=polkit-kde session=x11' "X11 session requires an interactive PolicyKit authentication agent"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-wayland-shell-session.sh" 'ECHO_AUTH_AGENT_READY provider=polkit-kde session=wayland' "Wayland session requires an interactive PolicyKit authentication agent"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-desktop-session.sh" 'ECHO_POWER_MANAGEMENT_READY provider=powerdevil upower=ready profiles=ready session=x11' "X11 requires a complete native power-management chain"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-wayland-shell-session.sh" 'ECHO_POWER_MANAGEMENT_READY provider=powerdevil upower=ready profiles=ready session=wayland' "Wayland requires a complete native power-management chain"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-desktop-session.sh" 'ECHO_NOTIFICATION_SERVICE_READY provider=echo-native dbus=ready socket=private session=x11' "X11 requires the standard native notification service"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-wayland-shell-session.sh" 'ECHO_NOTIFICATION_SERVICE_READY provider=echo-native dbus=ready socket=private session=wayland' "Wayland requires the standard native notification service"
for input_environment in \
  'GTK_IM_MODULE=fcitx' \
  'QT_IM_MODULE=fcitx' \
  'XMODIFIERS=@im=fcitx' \
  'SDL_IM_MODULE=fcitx'; do
  require_pattern "$REPO_ROOT/deploy/desktop-session/echo-desktop-session.sh" "^export ${input_environment}$" "X11 exports ${input_environment} for native applications"
  require_pattern "$REPO_ROOT/deploy/desktop-session/echo-wayland-session.sh" "^export ${input_environment}$" "Wayland exports ${input_environment} before compositor startup"
done
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-desktop-session.sh" '^INPUT_METHOD=/usr/bin/fcitx5$' "X11 input-method executable is fixed"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-wayland-shell-session.sh" '^INPUT_METHOD=/usr/bin/fcitx5$' "Wayland input-method executable is fixed"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-desktop-session.sh" '^"\$INPUT_METHOD" --replace &$' "X11 supervises one foreground Fcitx5 instance"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-wayland-shell-session.sh" '^"\$INPUT_METHOD" --replace &$' "Wayland supervises one foreground Fcitx5 instance"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-desktop-session.sh" '^/usr/bin/dbus-update-activation-environment \\$' "X11 publishes fixed input variables to D-Bus activated applications"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-wayland-session.sh" '^/usr/bin/dbus-update-activation-environment \\$' "Wayland publishes fixed input variables before compositor startup"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-wayland-shell-session.sh" 'user_environment_value GTK_IM_MODULE' "Wayland verifies systemd-activated applications inherit Fcitx5"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-desktop-session.sh" 'ECHO_INPUT_METHOD_READY provider=fcitx5 dbus=ready frontend=x11' "X11 requires the Fcitx5 D-Bus service"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-wayland-shell-session.sh" 'ECHO_INPUT_METHOD_READY provider=fcitx5 dbus=ready frontend=wayland' "Wayland requires the Fcitx5 D-Bus service"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-desktop-session.sh" 'Echo OS input method failed; terminating the graphical session' "X11 fails closed if its input method disappears"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-wayland-shell-session.sh" 'Echo OS input method failed; terminating the Wayland shell' "Wayland fails closed if its input method disappears"
forbid_pattern "$REPO_ROOT/deploy/desktop-session/echo-desktop-session.sh" 'im-config|fcitx5 -d' "X11 uses one supervised foreground Fcitx5 process"
forbid_pattern "$REPO_ROOT/deploy/desktop-session/echo-wayland-shell-session.sh" 'im-config|fcitx5 -d' "Wayland uses one supervised foreground Fcitx5 process"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-clipboard-host" 'org\.kde\.plasma\.private\.clipboard 0\.1' "windowless host loads the supported Plasma 6 Klipper QML module"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-clipboard-host" 'database must be exactly' "clipboard database is pinned below the logind runtime directory"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-clipboard-host" '^    os\.environ\["KLIPPER_DATABASE"\] = str\(database\)$' "Klipper cannot select persistent home storage"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-clipboard-host" '^    os\.environ\["XDG_CONFIG_HOME"\] = str\(config_home\)$' "Klipper configuration writes stay in volatile session storage"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-clipboard-host" '^    os\.environ\["XDG_CACHE_HOME"\] = str\(cache_home\)$' "Klipper cache writes stay in volatile session storage"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-clipboard-host" '^    os\.environ\["XDG_CONFIG_DIRS"\] = "/etc/xdg"$' "Klipper always reads the signed-root privacy defaults"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-clipboard-host" 'org\.kde\.klipper\.debug=false' "Klipper payload-bearing debug logs are forcibly disabled"
forbid_pattern "$REPO_ROOT/deploy/desktop-session/echo-clipboard-host" 'mimeData|clipboardContents|getClipboardContents|stdout.*clipboard' "clipboard host never reads or logs user clipboard payloads"
require_pattern "$REPO_ROOT/deploy/desktop-session/klipperrc" '^KeepClipboardContents=false$' "clipboard history is not retained across login sessions"
require_pattern "$REPO_ROOT/deploy/desktop-session/klipperrc" '^NoEmptyClipboard=true$' "clipboard survives the source application exiting"
require_pattern "$REPO_ROOT/deploy/desktop-session/klipperrc" '^IgnoreSelection=true$' "X11 primary selection is not silently added to history"
require_pattern "$REPO_ROOT/deploy/desktop-session/klipperrc" '^IgnoreImages=false$' "explicit image copies remain general-purpose clipboard data"
require_pattern "$REPO_ROOT/deploy/desktop-session/klipperrc" '^MaxClipItems=20$' "volatile clipboard history is bounded"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-desktop-session.sh" 'ECHO_CLIPBOARD_READY provider=klipper-qml dbus=ready storage=runtime-tmpfs persistence=off session=x11' "X11 requires the supervised Klipper service"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-wayland-shell-session.sh" 'ECHO_CLIPBOARD_READY provider=klipper-qml dbus=ready storage=runtime-tmpfs persistence=off session=wayland' "Wayland requires the supervised Klipper service"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-desktop-session.sh" 'clipboard service failed; terminating the graphical session' "X11 fails closed if its clipboard manager disappears"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-wayland-shell-session.sh" 'clipboard service failed; terminating the Wayland shell' "Wayland fails closed if its clipboard manager disappears"
require_pattern "$REPO_ROOT/deploy/desktop-session/smoke-desktop-session.sh" 'X11 clipboard disappeared after the source process exited' "X11 CI proves clipboard ownership survives its source process"
require_pattern "$REPO_ROOT/deploy/desktop-session/smoke-wayland-session-client.sh" 'Wayland clipboard disappeared after the source process exited' "Wayland CI proves clipboard ownership survives its source process"
for accessibility_session in \
  echo-desktop-session.sh \
  echo-wayland-session.sh \
  echo-wayland-shell-session.sh; do
  require_pattern "$REPO_ROOT/deploy/desktop-session/$accessibility_session" '^export QT_ACCESSIBILITY=1$' "$accessibility_session enables the Qt AT-SPI bridge"
done
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-desktop-session.sh" '^ACCESSIBILITY_BUS=/usr/libexec/at-spi-bus-launcher$' "X11 accessibility bus executable is fixed"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-wayland-shell-session.sh" '^ACCESSIBILITY_BUS=/usr/libexec/at-spi-bus-launcher$' "Wayland accessibility bus executable is fixed"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-desktop-session.sh" 'ECHO_ACCESSIBILITY_READY provider=at-spi2 dbus=ready qt=enabled session=x11' "X11 requires a working AT-SPI address"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-wayland-shell-session.sh" 'ECHO_ACCESSIBILITY_READY provider=at-spi2 dbus=ready qt=enabled session=wayland' "Wayland requires a working AT-SPI address"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-desktop-session.sh" '--force-renderer-accessibility' "X11 forces Chromium to expose its accessibility tree"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-wayland-shell-session.sh" '--force-renderer-accessibility' "Wayland forces Chromium to expose its accessibility tree"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-desktop-session.sh" 'required-name "Echo OS 桌面"' "X11 readiness probes the fixed Echo marker"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-wayland-shell-session.sh" 'required-name "Echo OS 桌面"' "Wayland readiness probes the fixed Echo marker"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-desktop-session.sh" 'accessibility bus failed; terminating the graphical session' "X11 fails closed if AT-SPI disappears"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-wayland-shell-session.sh" 'accessibility bus failed; terminating the Wayland shell' "Wayland fails closed if AT-SPI disappears"
require_pattern "$REPO_ROOT/deploy/desktop-session/smoke-desktop-session.sh" 'packaged Echo Desktop exposed its fixed X11 AT-SPI marker' "X11 CI reads the packaged Electron AT-SPI tree"
require_pattern "$REPO_ROOT/deploy/desktop-session/smoke-wayland-session-client.sh" 'native Wayland client exposed its fixed AT-SPI marker' "Wayland CI reads a native GTK AT-SPI tree"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-accessibility-smoke.py" '^MAX_ACCESSIBLE_NODES = 10_000$' "AT-SPI traversal is bounded"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-accessibility-smoke.py" 'process_belongs_to' "AT-SPI marker is tied to the launched application process"
forbid_pattern "$REPO_ROOT/deploy/desktop-session/echo-accessibility-smoke.py" 'print\([^\n]*(accessible\.name|_safe_name|required_name)' "AT-SPI probe never logs application tree content"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-screen-reader.desktop" '^Exec=/usr/bin/orca$' "screen-reader launcher invokes the fixed Orca binary without a shell"
require_pattern "$REPO_ROOT/frontend/src/app/desktop/page.tsx" 'aria-label="Echo OS 桌面"' "desktop root has a stable accessible name"
require_pattern "$REPO_ROOT/frontend/src/appliance/dock.tsx" 'aria-label=\{title\}' "Dock actions expose their titles to assistive technology"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-desktop-session.sh" 'window=\$SHELL_WINDOW_ID auth=ready power=ready notifications=ready input=ready clipboard=ready accessibility=ready' "X11 trusted readiness records authorization, power, notification, input, clipboard and accessibility dependencies"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-wayland-shell-session.sh" 'lock=kscreenlocker auth=ready power=ready notifications=ready input=ready clipboard=ready accessibility=ready' "Wayland trusted readiness records authorization, power, notification, input, clipboard and accessibility dependencies"
require_pattern "$REPO_ROOT/deploy/desktop-session/verify-desktop-boot.sh" 'auth=ready power=ready notifications=ready input=ready clipboard=ready accessibility=ready\$' "VM boot blessing requires the complete session dependency record"
require_pattern "$REPO_ROOT/deploy/desktop-session/verify-desktop-boot.sh" '! -L "\$READY_FILE"' "VM boot blessing rejects redirected readiness records"
require_pattern "$REPO_ROOT/deploy/desktop-session/test-verify-desktop-boot.sh" 'legacy window-only readiness unexpectedly blessed' "boot-blessing regression rejects incomplete legacy readiness"
require_pattern "$REPO_ROOT/deploy/desktop-session/test-verify-desktop-boot.sh" 'readiness without native notifications unexpectedly blessed' "boot blessing rejects a missing notification service"
require_pattern "$REPO_ROOT/deploy/desktop-session/test-verify-desktop-boot.sh" 'readiness without the input method unexpectedly blessed' "boot blessing rejects a missing input method"
require_pattern "$REPO_ROOT/deploy/desktop-session/test-verify-desktop-boot.sh" 'readiness without the system clipboard unexpectedly blessed' "boot blessing rejects a missing system clipboard"
require_pattern "$REPO_ROOT/deploy/desktop-session/test-verify-desktop-boot.sh" 'readiness without the accessible application tree unexpectedly blessed' "boot blessing rejects a missing accessibility tree"
require_pattern "$REPO_ROOT/deploy/desktop-session/test-verify-desktop-boot.sh" 'symlinked readiness unexpectedly blessed' "boot-blessing regression rejects symlinked readiness"
require_pattern "$REPO_ROOT/.github/workflows/desktop-session-smoke.yml" 'test-verify-desktop-boot\.sh' "desktop CI runs the boot-blessing regression"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-desktop-session.sh" 'power management failed; terminating the graphical session' "X11 fails closed if PowerDevil leaves the session"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-wayland-shell-session.sh" 'power management failed; terminating the Wayland shell' "Wayland fails closed if PowerDevil leaves the session"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-desktop-session.sh" 'notification service failed; terminating the graphical session' "X11 fails closed if its notification service leaves"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-wayland-shell-session.sh" 'notification service failed; terminating the Wayland shell' "Wayland fails closed if its notification service leaves"
require_pattern "$REPO_ROOT/frontend/electron/system-actions.cjs" 'lock: \{ tool: "loginctl", args: \["lock-session", "self"\] \}' "Lock Screen maps to the caller's real logind session"
require_pattern "$REPO_ROOT/frontend/electron/system-actions.cjs" 'logout: \{ tool: "loginctl", args: \["terminate-session", "self"\] \}' "Log Out terminates the caller's real logind session"
forbid_pattern "$REPO_ROOT/frontend/electron/system-actions.cjs" 'exec\(|spawn\(|/bin/sh|sh -c' "session and power actions never invoke a command shell"
require_pattern "$REPO_ROOT/frontend/electron/agent-service.cjs" '\["restart", AGENT_SERVICE\]' "Agent restart maps to the fixed image-baked systemd unit"
require_pattern "$REPO_ROOT/frontend/electron/agent-service.cjs" 'platform === "linux" && nativeShell' "Agent service control exists only in the native Linux session"
require_pattern "$REPO_ROOT/frontend/electron/agent-service.cjs" 'const HEALTH_VERIFIER = "/usr/lib/echo-os/verify-native-agent-health";' "Agent restart success is bound to the image-baked health verifier"
require_pattern "$REPO_ROOT/frontend/electron/agent-service.cjs" 'execFileImpl\(' "Agent restart invokes fixed executables with argument vectors"
forbid_pattern "$REPO_ROOT/frontend/electron/agent-service.cjs" 'exec\(|spawn\(|/bin/sh|sh -c' "Agent service control never invokes a command shell"
require_pattern "$REPO_ROOT/frontend/electron/main.cjs" 'agentService\.restartAgentService\(\{ nativeShell: NATIVE_SHELL \}\)' "Electron backend restart uses the native Agent systemd boundary"
require_pattern "$REPO_ROOT/.github/workflows/desktop-session-smoke.yml" 'agent-service\.test\.cjs' "desktop CI tests the fixed Agent restart boundary"
require_pattern "$REPO_ROOT/deploy/native-shell/setup-native-shell.sh" 'exec "\$TARGET" "\$@"' "legacy native-shell installer delegates to the target-C KWin installer"
forbid_pattern "$REPO_ROOT/deploy/native-shell/setup-native-shell.sh" 'cage|rsync|echo-shell\.service|get\.docker\.com' "legacy installer cannot reinstall the Cage kiosk path"
require_pattern "$REPO_ROOT/frontend/electron/system-controls.cjs" 'const sessionShell = nativeShell && platform === "linux";' "hardware controls exist only in the native Linux session"
require_pattern "$REPO_ROOT/frontend/electron/system-controls.cjs" '\["radio", "wifi", value \? "on" : "off"\]' "Wi-Fi control maps to a fixed NetworkManager argument vector"
require_pattern "$REPO_ROOT/frontend/electron/system-controls.cjs" '\["power", value \? "on" : "off"\]' "Bluetooth control maps to a fixed BlueZ argument vector"
require_pattern "$REPO_ROOT/frontend/electron/system-controls.cjs" '\["set-volume", "@DEFAULT_AUDIO_SINK@", `\$\{value\}%`\]' "audio control maps to the default PipeWire sink with a bounded percentage"
require_pattern "$REPO_ROOT/frontend/electron/system-controls.cjs" '\["-q", "set", `\$\{value\}%`\]' "display control maps to a bounded backlight percentage"
forbid_pattern "$REPO_ROOT/frontend/electron/system-controls.cjs" 'exec\(|spawn\(|/bin/sh|sh -c' "hardware controls never evaluate renderer command text"
require_pattern "$REPO_ROOT/frontend/electron/system-controls.cjs" 'ECHO_SYSTEM_CONTROLS_READY' "native control bridge emits a privacy-safe readiness marker"
require_pattern "$REPO_ROOT/frontend/src/app/desktop/page.tsx" 'systemControls=\{systemControls\}' "desktop control center receives real Linux hardware state"
require_pattern "$REPO_ROOT/frontend/src/app/desktop/page.tsx" 'const system = window\.echo\?\.system;' "Lock Screen resolves the isolated native system bridge"
require_pattern "$REPO_ROOT/frontend/src/app/desktop/page.tsx" 'await system\.runAction\("lock"\)' "Lock Screen calls the real native session action"
require_pattern "$REPO_ROOT/frontend/electron/native-windows.cjs" 'provider: "kwin-wayland"' "Electron exposes the compositor-native Wayland provider"
require_pattern "$REPO_ROOT/frontend/electron/native-windows.cjs" 'normalizeKWinWindowId\(windowId\)' "renderer Wayland actions accept only canonical KWin UUIDs"
require_pattern "$REPO_ROOT/frontend/electron/native-windows.cjs" 'method: "action", action, windowId: id' "Electron sends only fixed window actions over the private bridge"
forbid_pattern "$REPO_ROOT/frontend/electron/native-windows.cjs" 'exec\(|spawn\(|/bin/sh|sh -c' "window providers never evaluate renderer command text"
require_pattern "$REPO_ROOT/deploy/desktop-session/smoke-desktop-session.sh" 'KWin script published compositor-owned UUID window state' "isolated KWin smoke waits for real script state"
require_pattern "$REPO_ROOT/deploy/desktop-session/smoke-desktop-session.sh" '--request close' "isolated KWin smoke exercises a compositor-owned UUID action"
require_pattern "$REPO_ROOT/deploy/desktop-session/smoke-wayland-session.sh" '^  --output-count 2 \\$' "Wayland smoke creates two compositor outputs"
require_pattern "$REPO_ROOT/deploy/desktop-session/smoke-wayland-session.sh" '^  --scale 1\.25 \\$' "Wayland smoke exercises fractional HiDPI"
require_pattern "$REPO_ROOT/deploy/desktop-session/smoke-wayland-session.sh" 'org\.echoos\.liquidglassEnabled true' "Wayland smoke explicitly enables the native effect under software rendering"
require_pattern "$REPO_ROOT/deploy/desktop-session/smoke-wayland-session-client.sh" 'LiquidGlass1\.SyncSurfaces|\$KWIN_LIQUID_GLASS_INTERFACE\.SyncSurfaces' "Wayland smoke synchronizes one bounded native glass region"
require_pattern "$REPO_ROOT/deploy/desktop-session/smoke-wayland-session-client.sh" 'ECHO_KWIN_GLASS_EFFECT_READY provider=kwin-wayland-effect region=bounded cleanup=cleared' "Wayland smoke verifies native effect cleanup after Electron exits"
require_pattern "$REPO_ROOT/deploy/desktop-session/smoke-wayland-session-client.sh" '^GDK_BACKEND=wayland python3' "Wayland smoke forces a native Wayland client without X11 fallback"
require_pattern "$REPO_ROOT/deploy/desktop-session/smoke-wayland-session-client.sh" 'ECHO_SMOKE_NATIVE_WINDOW_ID=.*node' "Wayland smoke exercises the production UUID action bridge"
require_pattern "$REPO_ROOT/deploy/desktop-session/smoke-wayland-session-client.sh" 'org\.freedesktop\.ScreenSaver\.Lock' "Wayland smoke requests a real compositor lock"
require_pattern "$REPO_ROOT/deploy/desktop-session/smoke-wayland-session-client.sh" 'ECHO_WAYLAND_LOCK_READY provider=kscreenlocker pam=kde' "Wayland smoke requires KScreenLocker to establish the lock"
require_pattern "$REPO_ROOT/frontend/electron/main.cjs" 'ECHO_RENDERER_READY' "Electron emits a renderer-ready marker"
require_pattern "$REPO_ROOT/frontend/electron/main.cjs" 'formatSystemControlsReadyMarker' "Electron probes the native control bridge after renderer startup"
require_pattern "$REPO_ROOT/frontend/electron/renderer-readiness.cjs" 'configuredPath !== expectedPath' "Electron rejects renderer-controlled readiness paths"
require_pattern "$REPO_ROOT/frontend/electron/renderer-readiness.cjs" 'provider=electron-renderer status=ready mode=desktop' "Electron atomically publishes renderer readiness"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-wayland-session.sh" '^KWIN_WRAPPER=/usr/bin/kwin_wayland_wrapper$' "candidate uses KDE's socket-preserving KWin wrapper"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-wayland-session.sh" 'org\.echoos\.liquidglassEnabled true' "Wayland session enables the packaged native glass effect before KWin starts"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-wayland-session.sh" '^  --drm \\$' "candidate selects the production DRM backend"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-wayland-session.sh" '^  --xwayland \\$' "candidate retains XWayland compatibility"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-wayland-shell-session.sh" 'org\.kde\.KWinWrapper' "candidate waits for activation-environment synchronization"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-wayland-shell-session.sh" 'systemctl --user show-environment' "candidate verifies systemd's synchronized display environment"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-wayland-shell-session.sh" 'org\.freedesktop\.ScreenSaver\.GetActive' "candidate requires KScreenLocker readiness"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-wayland-shell-session.sh" 'ECHO_DESKTOP_READY provider=kwin-wayland' "candidate emits compositor-native readiness"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-wayland-shell-session.sh" 'ECHO_KWIN_GLASS_EFFECT_READY provider=kwin-wayland-effect region=bounded fallback=webgl' "candidate reports the native effect and its bounded fallback"
require_pattern "$REPO_ROOT/frontend/electron/native-liquid-glass.cjs" 'backend: "kwin-wayland-effect"' "Electron selects the native Wayland compositor backend"
require_pattern "$REPO_ROOT/frontend/electron/native-liquid-glass.cjs" 'org\.echoos\.KWin\.LiquidGlass1' "Electron calls only the fixed native effect interface"
require_pattern "$REPO_ROOT/frontend/electron/native-liquid-glass.cjs" 'kwinWaylandEffectPayload' "Electron strips renderer metadata before compositor synchronization"
require_pattern "$REPO_ROOT/deploy/oem/echo-wayland.desktop" '^Name=Echo OS \(Wayland Candidate\)$' "SDDM labels Wayland as a non-default candidate"
require_pattern "$REPO_ROOT/deploy/oem/echo-wayland.desktop" '^Exec=/opt/echo-os/deploy/desktop-session/echo-wayland-session\.sh$' "SDDM can launch the packaged Wayland candidate"
require_pattern "$REPO_ROOT/deploy/oem/sddm.conf" '^SessionDir=/usr/share/wayland-sessions$' "SDDM discovers selectable Wayland sessions"
require_pattern "$REPO_ROOT/deploy/oem/sddm.conf" '^GreeterEnvironment=QT_ACCESSIBILITY=1,ACCESSIBILITY_ENABLED=1,NO_AT_BRIDGE=0$' "SDDM greeter enables the Qt AT-SPI bridge"
require_pattern "$REPO_ROOT/deploy/oem/sddm.conf" '^DisplayCommand=/usr/lib/echo-os/echo-sddm-xsetup$' "SDDM arms the X11 greeter accessibility shortcut"
require_pattern "$REPO_ROOT/deploy/oem/sddm.conf" '^DisplayStopCommand=/usr/lib/echo-os/echo-sddm-xstop$' "SDDM tears down its greeter-only helper"
require_pattern "$REPO_ROOT/deploy/oem/echo-sddm-accessibility" '^ORCA_COMMAND = \($' "greeter helper uses a fixed Orca argument vector"
require_pattern "$REPO_ROOT/deploy/oem/echo-sddm-accessibility" 'wanted_modifiers = X\.Mod1Mask \| X\.Mod4Mask' "greeter helper grabs only Super+Alt+S"
require_pattern "$REPO_ROOT/deploy/oem/echo-sddm-accessibility" 'properties\.get\("Class"\) == "greeter"' "shortcut helper requires a logind greeter session"
require_pattern "$REPO_ROOT/deploy/oem/echo-sddm-accessibility" 'properties\.get\("Seat"\) == "seat0"' "shortcut helper is restricted to the local primary seat"
require_pattern "$REPO_ROOT/deploy/oem/echo-sddm-accessibility" 'properties\.get\("Remote"\) == "no"' "shortcut helper rejects remote sessions"
require_pattern "$REPO_ROOT/deploy/oem/echo-sddm-accessibility" '^def wait_for_greeter_runtime\(' "shortcut readiness includes the private user D-Bus runtime"
require_pattern "$REPO_ROOT/deploy/oem/echo-sddm-accessibility" 'except \(OSError, RuntimeError\):' "temporary Orca runtime failures cannot terminate the shortcut listener"
forbid_pattern "$REPO_ROOT/deploy/oem/echo-sddm-accessibility" 'shell=True|/bin/sh|sh -c' "greeter shortcut never evaluates a command shell"
require_pattern "$REPO_ROOT/deploy/oem/echo-sddm-xsetup" '^VENDOR_XSETUP=/usr/share/sddm/scripts/Xsetup$' "Echo preserves Debian's SDDM display setup"
require_pattern "$REPO_ROOT/deploy/oem/echo-sddm-xsetup" '^  --uid=sddm \\$' "SDDM shortcut helper runs as the unprivileged greeter"
require_pattern "$REPO_ROOT/deploy/oem/echo-sddm-xsetup" '^  --property=NoNewPrivileges=yes \\$' "greeter helper cannot acquire new privileges"
require_pattern "$REPO_ROOT/deploy/oem/echo-sddm-xstop" '^VENDOR_XSTOP=/usr/share/sddm/scripts/Xstop$' "Echo preserves Debian's SDDM display cleanup"
require_pattern "$IMAGE_DIR/send-sddm-screen-reader-key.py" '^SCREEN_READER_KEYS = \($' "raw greeter gate sends a fixed key chord"
require_pattern "$IMAGE_DIR/send-sddm-screen-reader-key.py" '"data": "meta_l"' "raw greeter gate includes the Super modifier"
require_pattern "$IMAGE_DIR/send-sddm-screen-reader-key.py" '"execute": "send-key"' "raw greeter gate uses the documented QMP keyboard command"
require_pattern "$IMAGE_DIR/send-sddm-screen-reader-key.py" 'stat\.S_ISSOCK' "raw greeter gate accepts only a real QMP Unix socket"
require_pattern "$IMAGE_DIR/send-sddm-screen-reader-key.py" 'metadata\.st_uid != os\.getuid\(\)' "raw greeter gate accepts only its own QMP socket"
require_pattern "$IMAGE_DIR/send-sddm-screen-reader-key.py" 'metadata\.st_mode & 0o077' "raw greeter gate rejects a shared QMP control socket"
forbid_pattern "$IMAGE_DIR/send-sddm-screen-reader-key.py" 'shell=True|subprocess|/bin/sh|sh -c' "QMP shortcut injector has no command-execution path"
require_pattern "$IMAGE_DIR/smoke-boot-image.sh" '^umask 077$' "raw boot logs and QMP control start private by default"
require_pattern "$IMAGE_DIR/smoke-sddm-accessibility-image.sh" '^ECHO_BOOT_TARGET=greeter \\$' "dedicated cold boot stops at the production greeter"
require_pattern "$IMAGE_DIR/smoke-sddm-accessibility-image.sh" '^ECHO_BOOT_CI_SESSION=no \\$' "greeter cold boot does not bypass SDDM"
require_pattern "$IMAGE_DIR/smoke-boot-image.sh" 'ECHO_SDDM_ACCESSIBILITY_READY provider=at-spi2 screen-reader=orca trigger=super-alt-s' "raw gate waits until the greeter shortcut is attached"
require_pattern "$IMAGE_DIR/smoke-boot-image.sh" 'ECHO_SDDM_SCREEN_READER_STARTED provider=orca trigger=super-alt-s' "raw gate observes Orca start before login"
require_pattern "$IMAGE_DIR/smoke-boot-image.sh" 'ECHO_QMP_KEY_SENT chord=super-alt-s' "raw gate records delivery of the virtual keyboard chord"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'test_send_sddm_screen_reader_key\.py' "image CI tests the fixed QMP keyboard protocol"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'echo-os-provisioned\.raw' "image CI preserves OEM state for the no-autologin greeter gate"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'smoke-sddm-accessibility-image\.sh' "image CI boots the accessible production greeter"
require_pattern "$IMAGE_DIR/smoke-boot-image.sh" '^BOOT_EXTRA_DISK_PATH="\$\{ECHO_BOOT_EXTRA_DISK_PATH:-\}"$' "raw harness accepts an explicit dedicated test disk"
require_pattern "$IMAGE_DIR/smoke-boot-image.sh" 'serial=echo-backup-ci' "raw harness gives the backup disk one fixed guest identity"
require_pattern "$IMAGE_DIR/smoke-boot-image.sh" 'ECHO_USER_BACKUP_STAGE_OK repository=' "guest boot waits for the complete backup staging marker"
require_pattern "$IMAGE_DIR/smoke-user-backup-image.sh" '^ECHO_BOOT_TARGET=backup \\$' "backup wrapper selects only the dedicated raw target"
require_pattern "$IMAGE_DIR/smoke-user-backup-image.sh" '^ECHO_BOOT_CI_SESSION=no \\$' "backup raw gate starts before any test desktop login"
require_pattern "$IMAGE_DIR/smoke-user-backup-image.sh" '^ECHO_BOOT_EPHEMERAL=no \\$' "backup raw gate persists the verified staging result"
require_pattern "$IMAGE_DIR/smoke-user-backup-image.sh" 'mkfs\.ext4 -q -F -L echo-backup-ci' "backup raw gate creates an independent ext4 repository disk"
require_pattern "$IMAGE_DIR/smoke-user-backup-image.sh" 'openssl rand -base64 32' "backup raw gate creates an ephemeral random repository password"
forbid_pattern "$IMAGE_DIR/smoke-user-backup-image.sh" 'echo.*PASSWORD|cat.*PASSWORD' "backup raw gate never writes its repository password to logs"
require_pattern "$IMAGE_DIR/smoke-user-backup-image.sh" '^  modprobe nbd max_part=16$' "restore transaction branches use a disposable whole-disk NBD target"
require_pattern "$IMAGE_DIR/smoke-user-backup-image.sh" 'cp --reflink=auto --sparse=always "\$WORK_IMAGE" "\$ROLLBACK_IMAGE"' "rollback and commit operate on independent staged raw copies"
require_pattern "$IMAGE_DIR/smoke-user-backup-image.sh" 'PROMOTE-ECHO-RESTORE-000000000000000000000000' "raw Recovery rejects a promotion token from a different plan"
require_pattern "$IMAGE_DIR/smoke-user-backup-image.sh" 'run_recovery restore-promote' "raw Recovery performs explicit restore promotion"
require_pattern "$IMAGE_DIR/smoke-user-backup-image.sh" 'run_recovery restore-rollback' "raw Recovery performs explicit restore rollback"
require_pattern "$IMAGE_DIR/smoke-user-backup-image.sh" 'run_recovery restore-commit' "raw Recovery performs explicit restore commit"
require_pattern "$IMAGE_DIR/smoke-user-backup-image.sh" '^ECHO_LOGIN_PROVISION_MODE=existing \\$' "promoted restore cold-boots the existing production account"
require_pattern "$IMAGE_DIR/smoke-user-backup-image.sh" 'restore=promote,rollback,commit trial-boot=ready confirmation=rejected' "raw backup evidence closes the two-branch restore transaction"
require_pattern "$IMAGE_DIR/echo-user-backup-ci.service" '^Before=echo-agent\.service sddm\.service echo-desktop\.service$' "backup gate runs before Agent and graphical login"
require_pattern "$IMAGE_DIR/echo-user-backup-ci.service" '^LoadCredential=echo-backup-password:/var/lib/echo-os/echo-backup-ci-password$' "guest test imports its password as a systemd credential"
require_pattern "$IMAGE_DIR/echo-user-backup-ci" '^if /usr/bin/echo-os-backup check; then$' "guest test requires corrupted repository verification to fail"
require_pattern "$IMAGE_DIR/echo-user-backup-ci" '^if env CREDENTIALS_DIRECTORY="\$WRONG_CREDENTIAL_DIR" \\$' "guest test requires a wrong repository password to fail"
require_pattern "$IMAGE_DIR/echo-user-backup-ci" 'available - reserve' "guest test constrains the repository to a two-MiB free-space reserve"
require_pattern "$IMAGE_DIR/echo-user-backup-ci" '^if /usr/bin/echo-os-backup backup; then$' "guest test requires repository exhaustion to fail"
require_pattern "$IMAGE_DIR/echo-user-backup-ci" 'ECHO_USER_BACKUP_DISK_FULL_REJECTED repository=consistent-after-failure' "guest test rechecks repository integrity after disk exhaustion"
require_pattern "$IMAGE_DIR/echo-user-backup-ci" 'getfacl --absolute-names --omit-header' "guest test compares restored POSIX ACLs"
require_pattern "$IMAGE_DIR/echo-user-backup-ci" 'getfattr --only-values -n user\.echo\.acceptance' "guest test compares restored extended attributes"
require_pattern "$IMAGE_DIR/echo-user-backup-ci" 'sparse extent expanded into dense data' "guest test rejects loss of sparse-file structure"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'smoke-user-backup-image\.sh' "image CI executes the external-disk backup lifecycle"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'echo-user-backup-boot' "image CI retains the backup acceptance serial log"
forbid_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'echo-backup-ci-password|echo-backup-disk\.raw' "image CI never uploads the backup password or repository disk"
require_pattern "$IMAGE_DIR/verify-os-image-evidence.py" 'echo-user-backup-boot/echo-restore-transaction\.log' "release evidence binds the complete restore transaction log"
require_pattern "$IMAGE_DIR/verify-os-image-evidence.py" '^def verify_user_backup_flow\(' "release evidence relates backup, rollback, trial and commit identities"
require_pattern "$IMAGE_DIR/verify-os-image-evidence.py" 'echo-user-backup-boot/restore-trial-boot/echo-os-boot\.log' "release evidence hashes the promoted production-login trial log"
require_pattern "$IMAGE_DIR/verify-os-image-evidence.py" 'promoted trial boot does not match the backup restore transaction' "release evidence binds the trial boot to its restore transaction"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'test_verify_os_image_evidence\.py' "image CI tests the final evidence binder"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'verify-os-image-evidence\.py' "image CI binds all install and boot logs after the last gate"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'echo-os-image-evidence\.json' "image CI retains the bounded evidence manifest"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" '^          name: echo-os-x86-64-release-evidence$' "image CI publishes a root-stable release evidence artifact"
require_pattern "$REPO_ROOT/.github/workflows/ab-update-smoke.yml" '^          name: echo-os-ab-update-release-evidence$' "A/B CI publishes a root-stable release evidence artifact"
require_pattern "$REPO_ROOT/.github/workflows/delivery-release-candidate.yml" '^          name: echo-os-x86-64-release-evidence$' "release coordination downloads the dedicated raw-image evidence artifact"
require_pattern "$REPO_ROOT/.github/workflows/delivery-release-candidate.yml" '^          name: echo-os-ab-update-release-evidence$' "release coordination downloads the dedicated A/B evidence artifact"
require_pattern "$REPO_ROOT/deploy/appliance/release_candidate_preflight.py" '^        if bool\(RUN_SPECS\[run_name\]\["denySelfHosted"\]\):$' "candidate provenance rejects self-hosted attestations only for hosted workflows"
require_pattern "$REPO_ROOT/deploy/appliance/release_candidate_preflight.py" '^            "runnerPolicy": RUN_SPECS\[run_name\]\["runnerPolicy"\],$' "candidate provenance records the reviewed runner policy"
require_pattern "$REPO_ROOT/deploy/appliance/release_evidence_index.py" '^CANDIDATE_RUNNER_POLICIES = \{$' "release evidence validates workflow-specific runner policies"
require_pattern "$REPO_ROOT/packaging/image/verify-ab-update-evidence.py" '^SCHEMA = 3$' "A/B evidence schema binds dedicated-runner identity and native operations services"
require_pattern "$REPO_ROOT/.github/workflows/ab-update-smoke.yml" '^            --runner-preflight "\$RUNNER_TEMP/echo-image-runner-preflight\.log" \\$' "A/B evidence binds the dedicated runner preflight"
require_pattern "$REPO_ROOT/deploy/appliance/release_evidence_index.py" '^            "runner_preflight",$' "release evidence requires the signed A/B runner preflight digest"
require_pattern "$REPO_ROOT/deploy/appliance/release_evidence_index.py" '^        Path\(__file__\)\.resolve\(\)\.with_name\("verify-os-image-evidence-release\.sh"\),$' "packaged release evidence finds its sibling signature verifier"
require_pattern "$REPO_ROOT/.github/workflows/delivery-release-candidate.yml" '^          mkdir -p dist/release-candidate/inputs$' "release coordination packages every offline replay input"
require_pattern "$REPO_ROOT/.github/workflows/delivery-release-candidate.yml" '^      - name: Replay the packaged candidate without repository source$' "release coordination replays the detached audit bundle before attestation"
require_pattern "$REPO_ROOT/.github/workflows/delivery-release-candidate.yml" '^          cp deploy/installer/verify_public_keyring\.py \\$' "release coordination packages the public-only keyring verifier"
require_pattern "$REPO_ROOT/deploy/appliance/verify-release-candidate-bundle.sh" '^echo "ECHO_DELIVERY_CANDIDATE_OFFLINE_OK index=\$index_sha256"$' "offline candidate replay emits one digest-bound success marker"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'os_source_identity\.py capture' "image CI records OS provenance before source-generating build steps"
require_pattern "$REPO_ROOT/.github/workflows/ab-update-smoke.yml" 'os_source_identity\.py capture' "A/B CI records the clean OS provenance used by its installer"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'test_os_source_identity\.py' "image CI tests clean OS source capture"
require_pattern "$REPO_ROOT/.github/workflows/ab-update-smoke.yml" 'test_os_source_identity\.py' "A/B CI tests clean OS source capture"
require_pattern "$IMAGE_DIR/os_source_identity.py" 'status", "--porcelain=v1", "--untracked-files=all", "-z"' "OS release identity rejects tracked and untracked source changes"
require_pattern "$IMAGE_DIR/os_source_identity.py" 'output must remain outside its Git tree' "OS source capture cannot dirty the tree it attests"
require_pattern "$IMAGE_DIR/os_source_identity.py" 'OS checkout is \{commit\}, but workflow expected \{expected_commit\}' "OS source capture binds the workflow trigger commit"
require_pattern "$IMAGE_DIR/os_source_identity.py" '^def verify_repository\(' "release build can recheck its checkout against the captured identity"
require_pattern "$IMAGE_DIR/build-image.sh" 'verify-repo.*' "main release build rechecks captured source around image construction"
require_pattern "$REPO_ROOT/packaging/recovery/build-recovery.sh" 'verify-repo.*' "Recovery build rechecks captured source around UKI construction"
forbid_pattern "$IMAGE_DIR/os_source_identity.py" 'shell=True|os\.system|eval\(|exec\(' "OS source identity never executes repository-controlled shell text"
require_pattern "$REPO_ROOT/deploy/system-health/echo-os-source-identity" '/usr/lib/echo-os/os-source-identity\.json' "running roots read one immutable embedded OS source identity"
require_pattern "$REPO_ROOT/deploy/system-health/echo-os-source-identity" '"\$VERIFIER" verify --manifest "\$MANIFEST" --machine' "runtime source reader revalidates the embedded identity"
require_pattern "$REPO_ROOT/deploy/system-health/test-echo-os-source-identity.sh" 'symlinked OS source identity unexpectedly passed' "runtime source reader rejects redirected provenance"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'test-echo-os-source-identity\.sh' "image CI tests the immutable runtime source reader"
require_pattern "$REPO_ROOT/.github/workflows/ab-update-smoke.yml" 'test-echo-os-source-identity\.sh' "A/B CI tests the immutable runtime source reader"
require_pattern "$IMAGE_DIR/mkosi.conf" 'os_source_identity\.py:/usr/lib/echo-os/os-source-identity\.py' "main immutable root embeds the source verifier"
require_pattern "$REPO_ROOT/packaging/recovery/mkosi.conf" 'os_source_identity\.py:/usr/lib/echo-os/os-source-identity\.py' "Recovery UKI embeds the source verifier"
require_pattern "$IMAGE_DIR/build-image.sh" 'UPDATE_KEYRING_TREE/usr/lib/echo-os/os-source-identity\.json' "main build injects its captured clean OS source identity"
require_pattern "$REPO_ROOT/packaging/recovery/build-recovery.sh" 'RECOVERY_KEYRING_TREE/usr/lib/echo-os/os-source-identity\.json' "Recovery build injects the same captured OS source identity"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" '"\$OS_SOURCE_VERIFIER" verify --manifest "\$OS_SOURCE_MANIFEST"' "main root validates embedded OS provenance before sealing"
require_pattern "$REPO_ROOT/packaging/recovery/mkosi.postinst.chroot" 'os-source-identity\.py verify' "Recovery validates embedded OS provenance before UKI signing"
require_pattern "$IMAGE_DIR/verify-os-image-evidence.py" '^def load_os_source_identity\(' "release evidence parses one bounded clean OS source identity"
require_pattern "$IMAGE_DIR/verify-os-image-evidence.py" '^def verify_os_source_binding\(' "release evidence relates OS provenance to the signed install bundle"
require_pattern "$IMAGE_DIR/verify-os-image-evidence.py" 'authenticated install manifest does not match the OS source identity' "release evidence rejects a source/install identity mismatch"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" '^          --os-source-manifest "\$ECHO_OS_SOURCE_MANIFEST"$' "image CI supplies the pre-build OS source identity to final evidence"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" '^            \$\{\{ runner\.temp \}\}/echo-os-source-identity\.json$' "release artifacts retain the signed evidence source input"
require_pattern "$IMAGE_DIR/sign-os-image-evidence.sh" 'python3 "\$VERIFY_KEYRING" "\$KEYRING"' "evidence signing rejects non-public release keyrings"
require_pattern "$IMAGE_DIR/sign-os-image-evidence.sh" '^gpg --batch --yes --local-user "\$SIGNING_FINGERPRINT" --detach-sign \\' "evidence manifest receives a detached release signature"
require_pattern "$IMAGE_DIR/sign-os-image-evidence.sh" '^gpgv --keyring "\$KEYRING" "\$TEMP_SIGNATURE" "\$MANIFEST"$' "evidence signature is verified against the exact manifest before publication"
require_pattern "$IMAGE_DIR/sign-os-image-evidence.sh" '^\[\[ ! -e "\$SIGNATURE" && ! -L "\$SIGNATURE" \]\] \|\| \\' "evidence signing never overwrites a resolved output"
require_pattern "$IMAGE_DIR/sign-os-image-evidence.sh" '^"\$VERIFY_RELEASE" "\$MANIFEST" "\$SIGNATURE" "\$KEYRING"$' "published evidence is reverified through the reviewer entrypoint"
forbid_pattern "$IMAGE_DIR/sign-os-image-evidence.sh" 'secret-keyring|private-key|--passphrase|pinentry-mode loopback' "evidence signing never accepts or exports private key material"
require_pattern "$IMAGE_DIR/verify-os-image-evidence-release.sh" 'python3 "\$VERIFY_KEYRING" "\$KEYRING"' "offline evidence review rejects non-public keyrings"
require_pattern "$IMAGE_DIR/verify-os-image-evidence-release.sh" '^gpgv --keyring "\$KEYRING" "\$SIGNATURE" "\$MANIFEST"$' "offline evidence review authenticates the exact downloaded pair"
require_pattern "$IMAGE_DIR/verify-os-image-evidence-release.sh" 'ECHO_OS_IMAGE_EVIDENCE_SIGNATURE_OK manifest=' "offline evidence review emits hash-bound success evidence"
forbid_pattern "$IMAGE_DIR/verify-os-image-evidence-release.sh" 'gpg --|secret-keyring|private-key|--passphrase' "offline evidence review needs no signing or private material"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'sign-os-image-evidence\.sh' "image CI release-signs the completed evidence manifest"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" '^            \$\{\{ runner\.temp \}\}/echo-os-image-evidence\.json\.gpg$' "release artifacts retain the detached evidence signature"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" '^            \$\{\{ runner\.temp \}\}/echo-os-image-evidence-signing\.log$' "release artifacts retain immediate signature-verification evidence"
require_pattern "$IMAGE_DIR/verify-os-image-evidence.py" '^MAX_LOG_BYTES = 32 \* 1024 \* 1024$' "individual release logs have a strict size bound"
require_pattern "$IMAGE_DIR/verify-os-image-evidence.py" '^MAX_TOTAL_LOG_BYTES = 256 \* 1024 \* 1024$' "combined release evidence has a strict size bound"
require_pattern "$IMAGE_DIR/verify-os-image-evidence.py" '^MAX_INSTALLED_IMAGE_BYTES = 128 \* 1024\*\*3$' "installed whole-disk evidence has a strict size bound"
require_pattern "$IMAGE_DIR/verify-os-image-evidence.py" 'dirty is not False' "release evidence rejects a dirty Agent source"
require_pattern "$IMAGE_DIR/verify-os-image-evidence.py" '^def load_install_identity\(' "release evidence parses one exact install manifest and signature"
require_pattern "$IMAGE_DIR/verify-os-image-evidence.py" '^def hash_installed_image\(' "release evidence hashes the final installed whole disk"
require_pattern "$IMAGE_DIR/verify-os-image-evidence.py" 'installed disk is smaller than its signed source image' "release evidence relates installed and source raw sizes"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" '^          --install-manifest ' "image CI binds the authenticated install manifest"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" '^          --install-signature ' "image CI binds the detached install signature"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" '^          --install-keyring "\$ECHO_INSTALL_KEYRING"$' "image CI binds the installer public trust root"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" '^          --secure-boot-certificate "\$ECHO_SECURE_BOOT_CERTIFICATE"$' "image CI binds the Secure Boot public certificate"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" '^          --pcr-policy-public-key "\$ECHO_TPM2_PCR_PUBLIC_KEY"$' "image CI binds the signed-PCR11 public key"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" '^          --installed-image "\$RUNNER_TEMP/echo-os-installed\.raw"$' "image CI hashes the same installed raw used by cold-boot gates"
require_pattern "$IMAGE_DIR/verify-os-image-evidence.py" '^def hash_public_trust_input\(' "release evidence hashes bounded public trust inputs"
require_pattern "$IMAGE_DIR/verify-os-image-evidence.py" 'PCR policy public key does not match the authenticated install manifest' "release evidence relates its PCR key to the install contract"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" '^            \$\{\{ runner\.temp \}\}/echo-install-keyring\.gpg$' "release artifacts retain the public installer trust root"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" '^            \$\{\{ env\.ECHO_SECURE_BOOT_CERTIFICATE \}\}$' "release artifacts retain the public Secure Boot certificate"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" '^            \$\{\{ env\.ECHO_TPM2_PCR_PUBLIC_KEY \}\}$' "release artifacts retain the public PCR policy key"
require_pattern "$IMAGE_DIR/verify-os-image-evidence.py" 'count != 1' "every required completion marker must be unique"
require_pattern "$IMAGE_DIR/verify-os-image-evidence.py" 'hashlib\.sha256\(raw\)\.hexdigest\(\)' "evidence manifest binds every complete input log"
forbid_pattern "$IMAGE_DIR/verify-os-image-evidence.py" 'subprocess|shell=True|eval\(|exec\(' "evidence binding never executes log-controlled content"
require_pattern "$REPO_ROOT/deploy/oem/kscreenlockerrc" '^RequirePassword=true$' "Wayland lock requires system credentials"
require_pattern "$REPO_ROOT/deploy/oem/kscreenlockerrc" '^LockOnResume=true$' "Wayland session locks across suspend/resume"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-desktop.service" '^RuntimeDirectory=echo-os$' "desktop readiness state lives in volatile runtime storage"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-desktop-health.service" '^Before=boot-complete\.target$' "desktop health runs before boot blessing"
require_pattern "$REPO_ROOT/deploy/desktop-session/echo-desktop-health.service" '^RequiredBy=boot-complete\.target$' "boot completion requires desktop health"
require_pattern "$REPO_ROOT/deploy/desktop-session/verify-desktop-boot.sh" 'ECHO_BOOT_HEALTHY' "health gate emits a cold-boot marker"
require_pattern "$REPO_ROOT/deploy/desktop-session/verify-desktop-boot.sh" 'version=\$\{IMAGE_VERSION:-unknown\}' "boot health reports the running image version"
require_pattern "$REPO_ROOT/deploy/desktop-session/verify-desktop-boot.sh" 'os=\$OS_SOURCE_COMMIT' "desktop boot health reports verified immutable OS provenance"
require_pattern "$REPO_ROOT/deploy/oem/verify-login-boot.sh" 'os=\$OS_SOURCE_COMMIT' "login health reports verified immutable OS provenance"
require_pattern "$REPO_ROOT/deploy/oem/test-verify-login-boot.sh" 'login gate unexpectedly accepted invalid OS provenance' "login source-provenance regression fails closed"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'test-verify-login-boot\.sh' "image CI tests source-bound login readiness"
require_pattern "$REPO_ROOT/deploy/recovery/echo-recovery" 'os=\$SOURCE_COMMIT' "Recovery readiness reports verified immutable OS provenance"
require_pattern "$REPO_ROOT/deploy/recovery/test-echo-recovery-source-identity.sh" 'Recovery readiness unexpectedly ignored missing OS provenance' "Recovery readiness fails closed without immutable provenance"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'test-echo-recovery-source-identity\.sh' "image CI tests source-bound Recovery readiness"
require_pattern "$IMAGE_DIR/verify-os-image-evidence.py" 'os=\{escaped_os_commit\}' "release evidence requires cold boots from the signed OS source"
require_pattern "$REPO_ROOT/deploy/update/echo-os-update" 'gpgv --keyring' "update manifest signature is mandatory"
require_pattern "$REPO_ROOT/deploy/update/create-update-bundle.sh" 'ECHO_OS_SOURCE_MANIFEST must name the verified clean OS source identity' "update signing requires pre-build clean OS provenance"
require_pattern "$REPO_ROOT/deploy/update/create-update-bundle.sh" 'OS-SOURCE-IDENTITY\.json' "update bundle publishes the retained OS provenance input"
require_pattern "$REPO_ROOT/deploy/update/create-update-bundle.sh" '"\$\(basename "\$OS_SOURCE_TARGET"\)" >SHA256SUMS' "update signature hash set covers the OS source identity"
require_pattern "$REPO_ROOT/deploy/update/verify-update-bundle.py" '^SOURCE_IDENTITY_NAME = "OS-SOURCE-IDENTITY\.json"$' "update verifier requires the signed OS provenance file"
require_pattern "$REPO_ROOT/deploy/update/verify-update-bundle.py" '^def load_source_identity\(' "update verifier strictly parses its bounded OS provenance"
require_pattern "$REPO_ROOT/deploy/update/verify-update-bundle.py" 'REQUIRED_KINDS = \{"root", "root-verity", "root-verity-sig", "efi", "source"\}' "update bundle exact set includes OS provenance"
require_pattern "$REPO_ROOT/deploy/update/echo-os-update" 'PREFLIGHT_RECORD=.*--preflight --machine' "update preflight records version and OS provenance before authentication"
require_pattern "$REPO_ROOT/deploy/update/echo-os-update" 'AUTHENTICATED_RECORD=.*--machine' "authenticated update verification repeats the provenance record"
require_pattern "$REPO_ROOT/deploy/update/echo-os-update" 'ECHO_UPDATE_BUNDLE_AUTHENTICATED version=' "update runtime emits source-bound authentication evidence"
require_pattern "$REPO_ROOT/deploy/update/echo-os-update" 'flock -n 9' "update runtime serializes verify/apply operations per device"
require_pattern "$REPO_ROOT/deploy/update/echo-os-update" 'another Echo OS update is already running' "concurrent update attempts fail closed"
require_pattern "$REPO_ROOT/deploy/update/echo-os-update" 'ECHO_UPDATE_SOURCE_SMOKE.*USE-SOURCE-RUNTIME' "offline raw overrides require an explicit source-runtime sentinel"
require_pattern "$REPO_ROOT/deploy/update/echo-os-update" 'ECHO_UPDATE_APPLIED version=' "production update entrypoint emits source-bound apply evidence"
require_pattern "$REPO_ROOT/deploy/update/echo-os-update" 'check-new' "production updater rejects replay before writing"
require_pattern "$REPO_ROOT/deploy/update/echo-os-update" 'CANDIDATE_VERSION.*==.*VERSION' "sysupdate candidate must equal the authenticated bundle version"
require_pattern "$REPO_ROOT/deploy/update/echo-os-update" 'update "\$VERSION"' "production updater pins apply to the authenticated candidate"
require_pattern "$REPO_ROOT/deploy/update/echo-os-update" 'ECHO_UPDATE_CANDIDATE_READY' "production evidence distinguishes candidate acceptance from apply"
require_pattern "$REPO_ROOT/deploy/update/echo-os-update" 'manifest=\$UPDATE_MANIFEST_SHA256 signature=\$UPDATE_SIGNATURE_SHA256' "production update evidence binds the exact signed bundle pair"
require_pattern "$REPO_ROOT/deploy/update/test-echo-os-update.sh" 'concurrent update unexpectedly acquired the device lock' "update entrypoint regression covers the exclusive device lock"
require_pattern "$REPO_ROOT/deploy/update/test-echo-os-update.sh" 'failed sysupdate unexpectedly emitted an apply marker' "update entrypoint never reports an interrupted apply as complete"
require_pattern "$REPO_ROOT/deploy/update/test-echo-os-update.sh" 'already-installed bundle unexpectedly produced a successful apply' "update entrypoint regression rejects same-version replay"
require_pattern "$REPO_ROOT/deploy/update/test-echo-os-update.sh" 'mismatched sysupdate candidate unexpectedly applied' "update entrypoint regression rejects candidate/bundle mismatch"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'test-echo-os-update\.sh' "image CI tests the production update entrypoint"
require_pattern "$REPO_ROOT/.github/workflows/ab-update-smoke.yml" 'test-echo-os-update\.sh' "A/B CI tests the production update entrypoint"
require_pattern "$REPO_ROOT/deploy/update/update-channel" '^https://[^/?#[:space:]]+/[^?#[:space:]]+$' "built-in update source is a credential-free HTTPS directory"
require_pattern "$REPO_ROOT/deploy/update/echo_update_channel.py" 'class RejectRedirects\(HTTPRedirectHandler\):' "channel client refuses HTTP redirects"
require_pattern "$REPO_ROOT/deploy/update/echo_update_channel.py" 'context\.minimum_version = ssl\.TLSVersion\.TLSv1_2' "channel TLS has an explicit minimum version"
require_pattern "$REPO_ROOT/deploy/update/echo_update_channel.py" 'os\.O_CREAT \| os\.O_EXCL \| os\.O_WRONLY' "channel cache downloads cannot overwrite an existing path"
require_pattern "$REPO_ROOT/deploy/update/echo_update_channel.py" 'signature_verifier\(gpgv, keyring, signature, manifest\)' "channel authenticates its manifest before payload selection"
require_pattern "$REPO_ROOT/deploy/update/echo_update_channel.py" 'verifier\.PAYLOAD_LIMITS\[kind\]' "every channel payload has a verifier-owned download bound"
require_pattern "$REPO_ROOT/deploy/update/echo_update_channel.py" 'total != int\(length_header\)' "channel rejects truncated declared responses"
require_pattern "$REPO_ROOT/deploy/update/echo_update_channel.py" 'os\.rename\(staging, target\)' "verified channel bundles publish atomically"
require_pattern "$REPO_ROOT/deploy/update/echo_update_channel.py" 'attempted to replace an immutable cached version' "same-version channel replacement fails closed"
require_pattern "$REPO_ROOT/deploy/update/echo_update_channel.py" '^MAX_CACHED_BUNDLES = 2$' "authenticated update cache has a fixed two-version bound"
require_pattern "$REPO_ROOT/deploy/update/echo_update_channel.py" 'vacuum_cache\(cache, target\)' "successful fetch and reuse enforce cache retention"
require_pattern "$REPO_ROOT/deploy/update/echo_update_channel.py" 'clean_abandoned_staging\(cache\)' "a serialized retry removes interrupted private staging"
require_pattern "$REPO_ROOT/deploy/update/echo-os-update-channel" '^SYSTEM_CHANNEL=/usr/lib/echo-os/update-channel$' "installed coordinator uses the immutable channel by default"
require_pattern "$REPO_ROOT/deploy/update/echo-os-update-channel" '^CACHE_ROOT=/var/cache/echo-os/updates$' "authenticated bundles use one private persistent cache"
require_pattern "$REPO_ROOT/deploy/update/echo-os-update-channel" 'flock -n 8' "channel fetches are serialized"
require_pattern "$REPO_ROOT/deploy/update/echo-os-update-channel" 'ECHO_UPDATE_CHANNEL_FETCHED version=' "channel fetch emits an authenticated cache record"
require_pattern "$REPO_ROOT/deploy/update/echo-os-update-channel" '"\$UPDATE_COMMAND" apply "\$BUNDLE_PATH"' "explicit channel apply delegates to the production updater"
require_pattern "$REPO_ROOT/deploy/update/echo-os-update-channel" 'scheduled fetch cannot vacuum this authenticated bundle during an apply' "explicit apply inherits the exclusive cache lock"
require_pattern "$REPO_ROOT/deploy/update/echo-os-update-channel" 'write_status --state checking --phase fetch' "signed channel publishes a coarse checking state"
require_pattern "$REPO_ROOT/deploy/update/echo-os-update-channel" '--state reboot-required' "successful inactive-slot installation publishes the reboot boundary"
require_pattern "$REPO_ROOT/deploy/update/echo-os-update-fetch.service" '^ExecStart=/usr/bin/echo-os-update-channel fetch$' "periodic worker only fetches and authenticates"
require_pattern "$REPO_ROOT/deploy/update/echo-os-update-fetch.service" '^StateDirectory=echo-os-update$' "periodic worker owns the public update-status directory"
require_pattern "$REPO_ROOT/deploy/update/echo-os-update-fetch.service" '^ProtectSystem=strict$' "channel worker has a read-only system image"
require_pattern "$REPO_ROOT/deploy/update/echo-os-update-fetch.service" '^ReadWritePaths=/var/cache/echo-os /run/echo-os-update-channel$' "channel worker can write only its cache and runtime lock"
forbid_pattern "$REPO_ROOT/deploy/update/echo-os-update-fetch.service" ' apply|reboot|systemd-sysupdate' "periodic channel work cannot apply or reboot"
require_pattern "$REPO_ROOT/deploy/update/echo-os-update-fetch.timer" '^Persistent=true$' "missed channel checks resume after boot"
require_pattern "$REPO_ROOT/deploy/update/test-echo-os-update-channel.sh" 'fetch-only channel operation unexpectedly invoked apply' "coordinator regression proves polling never applies"
require_pattern "$REPO_ROOT/deploy/update/test-echo-os-update-channel.sh" 'a rejected concurrent fetch unexpectedly changed public status' "rejected checks cannot clobber the visible update state"
require_pattern "$REPO_ROOT/deploy/update/echo_update_status.py" '^MAX_STATUS_BYTES = 4096$' "desktop update state has a fixed public size bound"
require_pattern "$REPO_ROOT/deploy/update/echo_update_status.py" 'not stat\.S_ISREG\(before\.st_mode\)' "desktop update state rejects non-regular files"
require_pattern "$REPO_ROOT/deploy/update/org.echoos.update.policy" '<allow_active>auth_admin_keep</allow_active>' "system update installation requires an active administrator authorization"
require_pattern "$REPO_ROOT/deploy/update/org.echoos.update.policy" '<annotate key="org\.freedesktop\.policykit\.exec\.path">/bin/bash</annotate>' "PolicyKit binds the script action to a fixed interpreter"
require_pattern "$REPO_ROOT/deploy/update/org.echoos.update.policy" '<annotate key="org\.freedesktop\.policykit\.exec\.argv1">/usr/lib/echo-os/echo-os-update-apply</annotate>' "PolicyKit binds authorization to the exact helper argv1"
forbid_pattern "$REPO_ROOT/deploy/update/org.echoos.update.policy" 'exec\.allow_gui' "non-graphical update helper receives no GUI environment exemption"
require_pattern "$REPO_ROOT/deploy/update/echo-os-update-apply" '^#!/bin/bash$' "privileged graphical update helper uses a fixed interpreter"
require_pattern "$REPO_ROOT/deploy/update/echo-os-update-apply" '^exec /usr/bin/echo-os-update-channel apply$' "privileged graphical helper has one fixed no-argument action"
require_pattern "$REPO_ROOT/frontend/electron/system-update.cjs" '\["--disable-internal-agent", applyInterpreter, applyHelper\]' "Electron invokes only the fixed interpreter and graphical update helper"
forbid_pattern "$REPO_ROOT/frontend/electron/system-update.cjs" 'shell: *true|/bin/sh|-c,' "system update bridge cannot evaluate renderer text in a shell"
require_pattern "$REPO_ROOT/deploy/update/test_echo_update_channel.py" 'test_signature_failure_fetches_no_payload_and_cleans_staging' "channel regression proves untrusted manifests fetch no payload"
require_pattern "$REPO_ROOT/deploy/update/test_echo_update_channel.py" 'test_cache_retains_target_and_only_one_previous_version' "channel regression proves two-version retention"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'test-echo-os-update-channel\.sh' "image CI tests the privileged channel coordinator"
require_pattern "$REPO_ROOT/.github/workflows/ab-update-smoke.yml" 'test-echo-os-update-channel\.sh' "A/B CI tests the privileged channel coordinator"
require_pattern "$REPO_ROOT/.github/workflows/desktop-session-smoke.yml" 'node electron/system-update\.test\.cjs' "desktop CI tests the fixed update IPC boundary"
require_pattern "$REPO_ROOT/deploy/update/echo_update_trust.py" '^POLICY_KEYS = \{' "update trust policy has an exact schema"
require_pattern "$REPO_ROOT/deploy/update/echo_update_trust.py" 'candidate_generation != previous_generation \+ 1' "trust generations cannot skip a required bridge"
require_pattern "$REPO_ROOT/deploy/update/echo_update_trust.py" 'removed update fingerprints must be explicitly retired' "removed signing identities require explicit retirement"
require_pattern "$REPO_ROOT/deploy/update/echo_update_trust.py" 'a retired update fingerprint cannot become unretired' "retired signing identities cannot be restored"
require_pattern "$REPO_ROOT/deploy/update/echo_update_trust.py" 'os\.replace\(paths\["pending_keyring"\], paths\["keyring"\]\)' "managed trust promotes its keyring atomically"
require_pattern "$REPO_ROOT/deploy/update/echo_update_trust.py" 'recover_pending\(state_root, verifier' "trust promotion resumes its interrupted transaction"
require_pattern "$REPO_ROOT/deploy/update/echo_update_trust.py" 'fcntl\.LOCK_EX \| fcntl\.LOCK_NB' "trust promotion is serialized"
require_pattern "$REPO_ROOT/deploy/update/echo_update_trust.py" 'clean_interrupted_temporaries\(state_root' "trust retries remove only bounded atomic-write leftovers"
require_pattern "$REPO_ROOT/deploy/update/echo_update_trust.py" 'system_generation < current_generation' "root rollback retains the newer persistent trust generation"
require_pattern "$REPO_ROOT/deploy/update/echo_update_trust.py" 'ECHO_UPDATE_TRUST_SOURCE_TEST.*USE-SOURCE-RUNTIME' "portable trust overrides require an explicit source-runtime sentinel"
require_pattern "$REPO_ROOT/deploy/update/echo_update_trust.py" '^STATE_ROOT = Path\("/var/lib/echo-os/update-trust"\)$' "managed trust lives on encrypted persistent var"
require_pattern "$REPO_ROOT/deploy/update/echo-os-update" 'python3 "\$TRUST_TOOL" select' "production updater validates managed policy before selecting its keyring"
require_pattern "$REPO_ROOT/deploy/update/echo-os-update-channel" 'python3 "\$TRUST_TOOL" select' "channel validates managed policy before selecting its keyring"
require_pattern "$REPO_ROOT/deploy/update/echo-os-update" '"\$TRUST_SOURCE" == system \|\| "\$TRUST_SOURCE" == managed' "updater accepts only system or managed selector sources"
require_pattern "$REPO_ROOT/deploy/update/echo-os-update-channel" '"\$TRUST_SOURCE" == system \|\| "\$TRUST_SOURCE" == managed' "channel accepts only system or managed selector sources"
require_pattern "$REPO_ROOT/deploy/update/test_echo_update_trust.py" 'test_bridge_then_retirement_survives_root_rollback' "trust regression proves old root rollback cannot resurrect a retired key"
require_pattern "$REPO_ROOT/deploy/update/test_echo_update_trust.py" 'test_policy_only_recovery_finishes_after_keyring_rename' "trust regression covers power loss between keyring and policy publication"
require_pattern "$REPO_ROOT/deploy/update/smoke-update-trust-rotation.sh" 'ECHO_UPDATE_TRUST_ROTATION_OK bridge=old\+new final=new-only old=retired rollback=retained generation=3' "real OpenPGP gate proves bridge, retirement and rollback trust"
require_pattern "$REPO_ROOT/deploy/update/test-echo-os-update.sh" 'production updater did not prefer managed rollback-resistant trust' "updater regression enforces managed-keyring priority"
require_pattern "$REPO_ROOT/deploy/update/test-echo-os-update-channel.sh" 'channel fetch did not prefer managed rollback-resistant trust' "channel regression enforces managed-keyring priority"
require_pattern "$REPO_ROOT/deploy/update/echo-update-trust-promote.service" '^Requires=echo-restore-transaction-health\.service echo-crash-health\.service$' "trust promotion waits for storage and crash health"
require_pattern "$REPO_ROOT/deploy/update/echo-update-trust-promote.service" '^Requires=echo-agent-health\.service echo-desktop-health\.service echo-login-health\.service$' "trust promotion requires Agent and the applicable desktop/login health path"
require_pattern "$REPO_ROOT/deploy/update/echo-update-trust-promote.service" '^Before=boot-complete\.target$' "trust promotion completes before boot blessing"
require_pattern "$REPO_ROOT/deploy/update/echo-update-trust-promote.service" '^ReadWritePaths=/var/lib/echo-os$' "trust promotion can write only persistent Echo state"
require_pattern "$IMAGE_DIR/build-image.sh" 'ECHO_UPDATE_TRUST_GENERATION must be a positive monotonic integer' "release builds require an explicit trust generation"
require_pattern "$IMAGE_DIR/build-image.sh" '"\$UPDATE_TRUST_TOOL" create-policy' "release builds derive the exact trusted fingerprint set"
require_pattern "$IMAGE_DIR/build-image.sh" '/usr/lib/echo-os/update-trust-policy\.json' "release roots embed and read back their trust policy"
require_pattern "$IMAGE_DIR/verify-image.sh" 'echo_update_trust\.py" verify-system' "artifact verification revalidates embedded policy and keyring"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'ECHO_UPDATE_TRUST_GENERATION=1' "image CI starts from an explicit trust generation"
require_pattern "$REPO_ROOT/.github/workflows/ab-update-smoke.yml" 'ECHO_UPDATE_TRUST_GENERATION=1' "A/B CI starts from an explicit trust generation"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'smoke-update-trust-rotation\.sh' "image CI runs real update-key rotation cryptography"
require_pattern "$REPO_ROOT/.github/workflows/ab-update-smoke.yml" 'smoke-update-trust-rotation\.sh' "A/B CI runs real update-key rotation cryptography"
require_pattern "$IMAGE_DIR/smoke-ab-update.sh" '"\$VERIFY_BUNDLE" --machine "\$BUNDLE"' "A/B raw gate reads authenticated OS provenance"
require_pattern "$IMAGE_DIR/smoke-ab-update.sh" '"\$UPDATE_COMMAND" apply "\$BUNDLE"' "A/B raw gate applies through the production update entrypoint"
require_pattern "$IMAGE_DIR/smoke-ab-update.sh" 'ECHO_UPDATE_APPLIED version=' "A/B raw gate retains source-bound production apply evidence"
forbid_pattern "$IMAGE_DIR/smoke-ab-update.sh" '"\$SYSUPDATE_BIN"' "A/B raw gate never bypasses the production update entrypoint"
require_pattern "$IMAGE_DIR/smoke-ab-update.sh" 'ECHO_AB_UPDATE_RAW_OK base=' "A/B raw completion evidence binds update provenance and rollback state"
require_pattern "$IMAGE_DIR/smoke-ab-update.sh" 'AB_EVIDENCE_LOG="\$LOG_ROOT/echo-ab-update-evidence\.log"' "A/B raw completion marker is retained with uploaded logs"
require_pattern "$IMAGE_DIR/smoke-ab-update.sh" 'DM_VERITY_REJECTION_LOG="\$LOG_ROOT/dm-verity-rejection\.log"' "A/B artifacts retain the explicit dm-verity corruption rejection"
require_pattern "$IMAGE_DIR/verify-ab-update-evidence.py" '^def verify_logs\(' "A/B release evidence verifies and hashes the complete lifecycle log set"
require_pattern "$IMAGE_DIR/verify-ab-update-evidence.py" 'A/B evidence contains a forbidden marker' "A/B evidence rejects false success in interrupted and failed boots"
require_pattern "$IMAGE_DIR/verify-ab-update-evidence.py" 'update bundle source identity differs from the build identity' "A/B evidence binds update and build provenance"
require_pattern "$IMAGE_DIR/verify-ab-update-evidence.py" 'update_signature_sha256' "A/B evidence binds the exact update manifest and detached signature"
require_pattern "$IMAGE_DIR/verify-ab-update-evidence.py" '"interrupted_apply"' "A/B evidence binds the authenticated mid-write SIGKILL"
require_pattern "$IMAGE_DIR/verify-ab-update-evidence.py" '"interrupted_boot"' "A/B evidence binds the healthy old-root boot after interruption"
require_pattern "$IMAGE_DIR/verify-ab-update-evidence.py" '"esp_full_apply"' "A/B evidence binds the authenticated ESP capacity failure"
require_pattern "$IMAGE_DIR/verify-ab-update-evidence.py" '"esp_full_boot"' "A/B evidence binds the healthy old-root boot after ESP exhaustion"
require_pattern "$REPO_ROOT/.github/workflows/ab-update-smoke.yml" 'test_verify_ab_update_evidence\.py' "A/B CI tests its lifecycle evidence binder"
require_pattern "$REPO_ROOT/.github/workflows/ab-update-smoke.yml" 'test_interrupt_sysupdate_after_write\.py' "A/B CI tests its deterministic real-write interruption helper"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'test_interrupt_sysupdate_after_write\.py' "image CI tests its deterministic real-write interruption helper"
require_pattern "$REPO_ROOT/.github/workflows/ab-update-smoke.yml" 'verify-ab-update-evidence\.py' "A/B CI binds the completed lifecycle evidence"
require_pattern "$REPO_ROOT/.github/workflows/ab-update-smoke.yml" 'echo-ab-update-evidence\.json\.gpg' "A/B CI retains the detached evidence signature"
require_pattern "$REPO_ROOT/.github/workflows/ab-update-smoke.yml" 'ECHO_UPDATE_SIGNING_KEY' "A/B evidence is signed by the authenticated update identity"
require_pattern "$REPO_ROOT/deploy/update/echo-os-update" 'python3 "\$VERIFY_KEYRING" "\$KEYRING"' "update trust roots reject private OpenPGP material"
require_pattern "$REPO_ROOT/deploy/update/echo-os-update" '"\$VERIFY_BUNDLE" --preflight' "update input is structurally bounded before signature verification"
require_pattern "$REPO_ROOT/deploy/update/echo-os-update" 'require_root_immutable "\$bundle_entry"' "update payloads cannot be raced by non-root writers"
require_pattern "$REPO_ROOT/deploy/update/echo-os-update" 'zstd --test -- "\$\{partition_payloads\[@\]\}"' "all authenticated verity partition payloads must be valid zstd streams"
forbid_pattern "$REPO_ROOT/deploy/update/echo-os-update" '/bin/sh|sh -c|eval ' "update client never evaluates bundle or version text in a command shell"
require_pattern "$REPO_ROOT/deploy/update/verify-update-bundle.py" '^MAX_MANIFEST_SIZE = 64 \* 1024$' "update manifests have a strict pre-authentication size cap"
require_pattern "$REPO_ROOT/deploy/update/verify-update-bundle.py" '^MAX_SIGNATURE_SIZE = 1024 \* 1024$' "update signatures have a strict pre-authentication size cap"
require_pattern "$REPO_ROOT/deploy/update/verify-update-bundle.py" 'actual_names != expected_names' "update bundles reject unsigned extra files"
require_pattern "$REPO_ROOT/deploy/update/verify-update-bundle.py" 'hash_payloads=not args\.preflight' "update verifier separates bounded preflight from authenticated hashing"
require_pattern "$REPO_ROOT/deploy/update/verify-verity-set.py" '^MAX_SIGNATURE_BYTES = 4 \* 1024 \* 1024$' "verity signature parsing matches systemd's four-MiB limit"
require_pattern "$REPO_ROOT/deploy/update/verify-verity-set.py" 'if separator and any\(padding\):' "verity signature partitions reject hidden non-zero trailing data"
require_pattern "$REPO_ROOT/deploy/update/echo-os-update" 'systemd-sysupdate' "authenticated bundles use systemd-sysupdate"
require_pattern "$REPO_ROOT/deploy/update/echo-os-update" 'python3 .*ACCOUNT_STATE_TOOL.* --capture' "A/B apply captures the latest local identity before replacing root"
require_pattern "$REPO_ROOT/deploy/update/sysupdate.d/10-root.transfer" '^ProtectVersion=%A$' "running root version is protected"
require_pattern "$REPO_ROOT/deploy/update/sysupdate.d/10-root.transfer" '^MatchPattern=echo-root-@v$' "root target uses versioned A/B labels"
require_pattern "$REPO_ROOT/deploy/update/sysupdate.d/10-root.transfer" '^MatchPattern=echo-os_@v\.root\.@u\.raw\.zst$' "root update propagates its roothash-derived GPT UUID"
for transfer in 10-root 20-root-verity 30-root-verity-sig; do
  require_file "$REPO_ROOT/deploy/update/sysupdate.d/$transfer.transfer"
  require_pattern "$REPO_ROOT/deploy/update/sysupdate.d/$transfer.transfer" '^ReadOnly=yes$' "$transfer target remains immutable"
done
require_pattern "$REPO_ROOT/deploy/update/sysupdate.d/20-root-verity.transfer" '^MatchPattern=echo-os_@v\.root-verity\.@u\.raw\.zst$' "hash-tree update propagates its roothash-derived GPT UUID"
require_pattern "$REPO_ROOT/deploy/update/sysupdate.d/30-root-verity-sig.transfer" '^MatchPattern=echo-os_@v\.root-verity-sig\.@u\.raw\.zst$' "signature update carries an explicit GPT UUID"
require_pattern "$REPO_ROOT/deploy/update/sysupdate.d/90-uki.transfer" '^MatchPattern=echo-os_@v\+@l-@d\.efi$' "new UKI starts under boot counting"
require_pattern "$REPO_ROOT/deploy/update/create-update-bundle.sh" 'ECHO_UPDATE_SIGNING_KEY' "release bundles require an external signing-key fingerprint"
require_pattern "$REPO_ROOT/deploy/update/create-update-bundle.sh" 'ECHO_UPDATE_KEYRING' "release bundles require the image-selected update trust root"
require_pattern "$REPO_ROOT/deploy/update/create-update-bundle.sh" 'python3 "\$VERIFY_VERITY"' "release tooling proves the root/hash/signature/UKI set before signing"
require_pattern "$REPO_ROOT/deploy/update/create-update-bundle.sh" 'gpgv --keyring "\$TRUST_KEYRING"' "release tooling proves the selected trust root accepts the update"
require_pattern "$REPO_ROOT/deploy/update/create-update-bundle.sh" 'sbverify --cert "\$VERITY_CERTIFICATE" "\$UKI_SOURCE"' "update release tooling authenticates the UKI Secure Boot signer"
require_pattern "$REPO_ROOT/deploy/update/create-update-bundle.sh" 'python3 "\$VERIFY_PCR_POLICY"' "update release tooling authenticates the UKI signed-PCR11 policy"
require_pattern "$REPO_ROOT/deploy/update/create-update-bundle.sh" 'python3 "\$VERIFY_BUNDLE" "\$BUNDLE_BUILD_DIR"' "release tooling verifies the staged update before publication"
require_pattern "$REPO_ROOT/deploy/update/create-update-bundle.sh" '^mv -- "\$BUNDLE_BUILD_DIR" "\$OUTPUT_DIR"$' "release tooling atomically publishes the complete update bundle"
forbid_pattern "$REPO_ROOT/deploy/update/create-update-bundle.sh" 'BEGIN PGP PRIVATE KEY|BEGIN PRIVATE KEY' "release tooling contains no private key material"
require_pattern "$REPO_ROOT/deploy/update/publish_update_repository.py" '^GPGV_PATH = Path\("/usr/bin/gpgv"\)$' "repository publication uses one fixed system signature verifier"
require_pattern "$REPO_ROOT/deploy/update/publish_update_repository.py" 'verify_public_keyring_bytes\(data\)' "repository publication rejects secret or opaque keyrings"
require_pattern "$REPO_ROOT/deploy/update/publish_update_repository.py" 'stable publication must advance by exactly one sequence' "stable publication cannot skip or roll back its monotonic sequence"
require_pattern "$REPO_ROOT/deploy/update/publish_update_repository.py" 'immutable release sequence/version already has different bytes' "published release identities cannot be replaced"
require_pattern "$REPO_ROOT/deploy/update/publish_update_repository.py" '^def cleanup_abandoned_staging\(' "interrupted repository staging is bounded and recoverable"
require_pattern "$REPO_ROOT/deploy/update/publish_update_repository.py" '^def atomic_switch_channel\(' "stable channel publication has one atomic pointer switch"
require_pattern "$REPO_ROOT/deploy/update/publish_update_repository.py" 'os\.replace\(staging, destination\)' "complete authenticated release is renamed before channel exposure"
require_pattern "$REPO_ROOT/deploy/update/publish_update_repository.py" 'os\.replace\(temporary, channel\)' "stable channel switches atomically after release durability"
require_pattern "$REPO_ROOT/deploy/update/publish_update_repository.py" 'fsync_directory\(channel\.parent\)' "stable channel rename is made durable"
require_pattern "$REPO_ROOT/deploy/update/test_publish_update_repository.py" 'test_retry_recovers_release_rename_before_channel_switch' "repository publication retries the post-release pre-channel crash boundary"
require_pattern "$REPO_ROOT/deploy/update/smoke-update-repository-publication.sh" 'gpgv --keyring "\$KEYRING"' "Linux repository smoke re-verifies the served manifest with real GPG"
require_pattern "$REPO_ROOT/deploy/update/smoke-update-repository-publication.sh" 'ECHO_UPDATE_REPOSITORY_GPG_OK sequence=2 version=0\.2\.2 rollback=rejected' "Linux repository smoke proves two publications and rollback rejection"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" "unittest discover -s deploy/update -p '\*test\*\.py'" "image CI exercises repository publication policy"
require_pattern "$REPO_ROOT/.github/workflows/ab-update-smoke.yml" "unittest discover -s deploy/update -p '\*test\*\.py'" "A/B CI exercises repository publication policy"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'smoke-update-repository-publication\.sh' "image CI exercises real-GPG repository publication"
require_pattern "$REPO_ROOT/.github/workflows/ab-update-smoke.yml" 'smoke-update-repository-publication\.sh' "A/B CI exercises real-GPG repository publication"
forbid_pattern "$REPO_ROOT/deploy/update/publish_update_repository.py" 'BEGIN PGP PRIVATE KEY|BEGIN PRIVATE KEY' "repository publisher contains no private key material"
require_pattern "$IMAGE_DIR/smoke-ab-update.sh" 'ECHO_BOOT_EPHEMERAL=no' "rollback smoke persists failed boot counters"
require_pattern "$IMAGE_DIR/interrupt-sysupdate-after-write.py" 'os\.killpg\(process\.pid, signal\.SIGKILL\)' "interruption gate kills the real sysupdate process group"
require_pattern "$IMAGE_DIR/interrupt-sysupdate-after-write.py" 'if "update" not in argv' "interruption shim passes check-new through without inventing a write"
require_pattern "$IMAGE_DIR/interrupt-sysupdate-after-write.py" '^def sample_sha256\(' "interruption gate observes an actual inactive-root byte range"
require_pattern "$IMAGE_DIR/smoke-ab-update.sh" 'PATH="\$INTERRUPT_BIN_DIR:\$PATH"' "mid-write gate still enters through the production updater"
require_pattern "$IMAGE_DIR/smoke-ab-update.sh" 'ECHO_UPDATE_INTERRUPT_BEFORE_SHA256=' "mid-write gate binds the pre-write inactive-root sample"
require_pattern "$IMAGE_DIR/smoke-ab-update.sh" 'interrupted update emitted a false applied marker' "mid-write failure cannot claim update success"
require_pattern "$IMAGE_DIR/smoke-ab-update.sh" 'interrupted-base-boot' "mid-write gate cold-boots the unchanged base entry"
require_pattern "$IMAGE_DIR/smoke-ab-update.sh" 'result=flushed-and-applied' "a normal retry must recover the same partially written disk"
require_pattern "$IMAGE_DIR/smoke-ab-update.sh" 'ESP_FILL_COUNT=.*ESP_FILL_COUNT' "ESP exhaustion is reached with a bounded filler loop"
require_pattern "$IMAGE_DIR/smoke-ab-update.sh" 'No space left on device\|ENOSPC\|Disk full' "ESP failure must be caused by target capacity"
require_pattern "$IMAGE_DIR/smoke-ab-update.sh" 'ESP-exhausted update emitted a false applied marker' "ESP-full failure cannot claim update success"
require_pattern "$IMAGE_DIR/smoke-ab-update.sh" 'esp-full-base-boot' "ESP-full gate cold-boots the unchanged base entry"
require_pattern "$IMAGE_DIR/smoke-ab-update.sh" 'mmd -i .*::/ESPTEST' "ESP exhaustion uses one dedicated filler directory"
require_pattern "$IMAGE_DIR/smoke-ab-update.sh" "LC_ALL=C mdel -i .*ESPTEST/ECHO.*BIN" "ESP recovery deletes only its bounded filler namespace"
require_pattern "$IMAGE_DIR/smoke-ab-update.sh" 'mrd -i .*::/ESPTEST' "ESP recovery removes its empty dedicated filler directory"
require_pattern "$IMAGE_DIR/smoke-ab-update.sh" 'sync -f "\$UPDATED_IMAGE"' "ESP cleanup is durable before same-disk retry"
require_pattern "$IMAGE_DIR/smoke-ab-update.sh" 'fillers-removed-and-applied' "the same ESP-full disk must recover after bounded cleanup"
require_pattern "$IMAGE_DIR/smoke-ab-update.sh" 'count=8192 conv=notrunc' "rollback smoke corrupts only the temporary updated root"
require_pattern "$IMAGE_DIR/smoke-ab-update.sh" 'updated-production-login' "A/B smoke verifies persistent local identity on the updated root"
require_pattern "$IMAGE_DIR/smoke-ab-update.sh" '/var/lib/flatpak/echo-os-ab-persistence' "A/B smoke verifies persistent system application state"
require_pattern "$IMAGE_DIR/smoke-ab-update.sh" '/var/lib/echo-os/machine-id' "A/B smoke carries one device identity across roots"
require_pattern "$IMAGE_DIR/smoke-ab-update.sh" '/var/lib/NetworkManager/system-connections/echo-ab-persistence\.nmconnection' "A/B smoke carries a private NetworkManager profile across roots"
require_pattern "$IMAGE_DIR/smoke-ab-update.sh" 'NETWORK_PROFILE_SOURCE.*NETWORK_PROFILE_ROLLBACK_COPY' "A/B smoke compares private network state after rollback"
require_pattern "$IMAGE_DIR/smoke-ab-update.sh" '/var/lib/echo-os/region-state\.json' "A/B smoke carries locale, keymap and timezone across roots"
require_pattern "$IMAGE_DIR/smoke-ab-update.sh" 'REGION_STATE_SOURCE.*REGION_STATE_ROLLBACK_COPY' "A/B smoke compares regional state after rollback"
require_pattern "$IMAGE_DIR/smoke-ab-update.sh" 'locale=zh_CN\.UTF-8 keymap=us timezone=Asia/Shanghai source=persistent-var' "A/B boots activate a non-default persisted region"
require_pattern "$IMAGE_DIR/smoke-ab-update.sh" 'GOOD_DERIVED_ID.*ROLLBACK_DERIVED_ID' "A/B smoke compares non-reversible identity across rollback"
require_pattern "$IMAGE_DIR/smoke-ab-update.sh" '/var/lib/echo-os/oem-complete\.json' "A/B smoke starts from production OEM completion state"
require_pattern "$IMAGE_DIR/smoke-ab-update.sh" 'OEM_MARKER_SOURCE.*OEM_MARKER_ROLLBACK_COPY' "A/B smoke compares OEM identity state after rollback"
require_pattern "$IMAGE_DIR/smoke-ab-update.sh" 'ACCOUNT_SHADOW_SOURCE.*ACCOUNT_SHADOW_ROLLBACK_COPY' "A/B smoke compares the private password hash after rollback"
require_pattern "$IMAGE_DIR/smoke-ab-update.sh" '^ECHO_LOGIN_PROVISION_MODE=existing \\$' "A/B smoke never reinjects account state after OEM first use"
require_file "$REPO_ROOT/.github/workflows/ab-update-smoke.yml"
require_pattern "$REPO_ROOT/.github/workflows/ab-update-smoke.yml" 'options: --privileged --volume /lib/modules:/lib/modules:ro' "A/B CI exposes the host NBD module tree for disposable installation"
require_pattern "$REPO_ROOT/.github/workflows/ab-update-smoke.yml" 'smoke-installer-install\.sh' "A/B lifecycle starts from the production whole-disk installer"
require_pattern "$REPO_ROOT/.github/workflows/ab-update-smoke.yml" 'smoke-oem-image\.sh' "A/B lifecycle completes production OEM first use"
require_pattern "$REPO_ROOT/.github/workflows/ab-update-smoke.yml" 'echo-os-provisioned-base\.raw' "A/B update starts from the installed and provisioned device"
require_pattern "$IMAGE_DIR/build-image.sh" 'ECHO_UPDATE_KEYRING is required' "production images cannot ship without an update trust root"
require_pattern "$IMAGE_DIR/build-image.sh" 'UPDATE_KEYRING_TREE/usr/lib/echo-os/update-keyring\.gpg' "mkosi receives the update keyring through a directory tree"
require_pattern "$IMAGE_DIR/build-image.sh" '^    --extra-tree="\$UPDATE_KEYRING_TREE" --force build$' "main image build injects the selected update trust tree"
require_pattern "$IMAGE_DIR/build-image.sh" 'cmp "\$UPDATE_KEYRING_INPUT" "\$EMBEDDED_UPDATE_KEYRING"' "finished raw contains the exact selected update trust root"
require_pattern "$IMAGE_DIR/mkosi.postinst.chroot" '"\$KEYRING_VERIFIER" "\$UPDATE_KEYRING"' "finished root validates its embedded public update keyring"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'ECHO_UPDATE_KEYRING=' "image CI provisions an isolated update trust root before building"
require_pattern "$REPO_ROOT/.github/workflows/ab-update-smoke.yml" 'ECHO_UPDATE_KEYRING=' "A/B CI signs with the trust root embedded in both roots"
require_pattern "$REPO_ROOT/deploy/recovery/echo-recovery.service" '^ExecStart=/usr/bin/echo-recovery status$' "automatic recovery startup is read-only"
forbid_pattern "$REPO_ROOT/deploy/recovery/echo-recovery" 'e2fsck -f[pny]' "Recovery never mutates or fsck-replays a signed root"
require_pattern "$REPO_ROOT/deploy/recovery/echo-recovery" 'signed root repair is forbidden' "Recovery refuses in-place signed-root repair"
require_pattern "$REPO_ROOT/deploy/recovery/echo-recovery" 'python3 "\$VERITY_VERIFIER"' "Recovery authenticates and verifies the complete root hash tree"
require_pattern "$REPO_ROOT/deploy/recovery/echo-recovery" 'ERASE-ECHO-DATA' "factory reset requires an explicit destructive confirmation"
require_pattern "$REPO_ROOT/deploy/recovery/echo-recovery" 'ROTATE-ECHO-RECOVERY-KEY' "recovery-key rotation requires an explicit confirmation phrase"
require_pattern "$REPO_ROOT/deploy/recovery/echo-recovery" 'REBIND-ECHO-TPM2' "replacement-TPM binding requires an explicit confirmation phrase"
require_pattern "$REPO_ROOT/deploy/recovery/echo-recovery" 'ECHO_RECOVERY_READY' "recovery emits a boot readiness marker"
require_pattern "$REPO_ROOT/deploy/recovery/echo-recovery" 'ECHO_RECOVERY_SOURCE_SMOKE.*USE-SOURCE-RUNTIME' "source Recovery runtime overrides require an explicit CI sentinel"
for recovery_partition in 20-var 30-swap 40-home; do
  require_file "$REPO_ROOT/deploy/recovery/repart.d/$recovery_partition.conf"
  require_pattern "$REPO_ROOT/deploy/recovery/repart.d/$recovery_partition.conf" \
    '^Encrypt=key-file$' "factory reset encrypts $recovery_partition"
  require_pattern "$REPO_ROOT/deploy/recovery/repart.d/$recovery_partition.conf" \
    '^FactoryReset=yes$' "factory reset explicitly rotates $recovery_partition"
done
require_pattern "$REPO_ROOT/deploy/recovery/echo-recovery" 'echo-var.*echo-swap.*echo-home' "factory reset requires all three mutable partitions"
require_pattern "$REPO_ROOT/deploy/recovery/echo-recovery" '^      --key-file="\$RECOVERY_KEY" \\' "factory reset creates LUKS2 directly with the durable recovery key"
require_pattern "$REPO_ROOT/deploy/recovery/echo-recovery" '"\$DATA_PROTECTION" enroll-recovery' "factory reset enrolls signed-PCR TPM2 after durable recovery access exists"
require_pattern "$REPO_ROOT/deploy/recovery/echo-recovery" 'ECHO_FACTORY_RESET_COMPLETE.*luks2-tpm2-signed-pcr11-recovery' "factory reset reports only the complete encrypted policy"
require_pattern "$REPO_ROOT/deploy/data-protection/echo_data_protection.py" 'def rotate_recovery' "data protection implements recovery-key rotation"
require_pattern "$REPO_ROOT/deploy/data-protection/echo_data_protection.py" 'def rebind_tpm2' "data protection implements replacement-TPM binding"
require_pattern "$REPO_ROOT/deploy/data-protection/echo_data_protection.py" '\(CRYPTENROLL, str\(device\), "--wipe-slot=tpm2"\)' "replacement-TPM binding explicitly removes same-policy stale tokens first"
require_pattern "$REPO_ROOT/deploy/recovery/echo-recovery" '"\$DATA_PROTECTION" rebind-tpm2' "Recovery invokes the dedicated replacement-TPM transaction"
require_pattern "$REPO_ROOT/deploy/data-protection/echo_data_protection.py" 'establish the new key everywhere before revoking the old' "recovery-key rotation documents its no-lockout transaction"
require_pattern "$REPO_ROOT/deploy/data-protection/echo_data_protection.py" 'ECHO_DATA_RECOVERY_ROTATED' "recovery-key core has a distinct completion marker"
require_pattern "$REPO_ROOT/deploy/data-protection/echo_data_protection.py" 'old=revoked new=verified tpm2=preserved' "recovery-key core reports only complete revocation"
require_pattern "$REPO_ROOT/deploy/recovery/echo-recovery" 'ECHO_RECOVERY_KEY_ROTATION_COMPLETE.*old=revoked.*new=verified.*tpm2=preserved' "Recovery reports complete key rotation"
require_pattern "$REPO_ROOT/deploy/recovery/echo-recovery" 'ECHO_TPM2_REBIND_COMPLETE.*signed-pcr11-recovery' "Recovery reports complete replacement-TPM binding"
require_pattern "$IMAGE_DIR/smoke-key-lifecycle.sh" '^modprobe nbd max_part=16$' "key-lifecycle smoke uses a disposable whole-disk NBD copy"
require_pattern "$IMAGE_DIR/smoke-key-lifecycle.sh" 'TPM2 token changed during recovery-key rotation' "key-lifecycle smoke requires rotation to preserve TPM tokens"
require_pattern "$IMAGE_DIR/smoke-key-lifecycle.sh" 'old recovery key still unlocks.*after rotation' "key-lifecycle smoke proves old recovery access is revoked"
require_pattern "$IMAGE_DIR/smoke-key-lifecycle.sh" 'tpm2_pubkey_pcrs.*\[11\]' "replacement-TPM smoke inspects the signed PCR 11 policy"
require_pattern "$IMAGE_DIR/smoke-key-lifecycle.sh" 'recovery-key rotation or TPM2 rebind changed decrypted device data' "key-lifecycle smoke proves decrypted data remains byte-identical"
require_pattern "$IMAGE_DIR/smoke-key-lifecycle.sh" 'key lifecycle changed ESP or root-slot bytes' "key-lifecycle smoke proves immutable partitions remain byte-identical"
require_pattern "$IMAGE_DIR/smoke-key-lifecycle.sh" 'ECHO_KEY_LIFECYCLE_SMOKE_OK.*tpm2=replacement-srk.*data=preserved' "key-lifecycle smoke emits a complete evidence marker"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'Create an isolated replacement TPM identity' "image CI creates an independent replacement TPM identity"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'cmp -s "\$ECHO_INSTALL_TPM2_DEVICE_KEY" "\$replacement_swtpm_srk"' "image CI rejects a replacement TPM with the original SRK"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'smoke-key-lifecycle\.sh' "image CI executes recovery-key rotation and TPM replacement"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'ECHO_SWTPM_STATE_DIR="\$ECHO_REPLACEMENT_SWTPM_STATE_DIR"' "post-rebind boot attaches only the replacement TPM state"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'echo-replacement-tpm-boot' "image CI retains replacement-TPM boot evidence"
require_pattern "$IMAGE_DIR/smoke-factory-reset.sh" '^modprobe nbd max_part=16$' "factory-reset smoke uses a disposable whole-disk NBD copy"
require_pattern "$IMAGE_DIR/smoke-factory-reset.sh" 'old recovery key did not unlock.*before reset' "factory-reset smoke proves the pre-reset recovery credential"
require_pattern "$IMAGE_DIR/smoke-factory-reset.sh" 'old recovery key still unlocks' "factory-reset smoke requires the retired recovery credential to fail"
require_pattern "$IMAGE_DIR/smoke-factory-reset.sh" 'factory key unlocks.*after reset' "factory-reset smoke rejects the shared factory credential after reset"
require_pattern "$IMAGE_DIR/smoke-factory-reset.sh" 'tpm2_pubkey_pcrs.*\[11\]' "factory-reset smoke inspects the signed PCR 11 token policy"
require_pattern "$IMAGE_DIR/smoke-factory-reset.sh" 'tpm2_srk' "factory-reset smoke requires the serialized per-device SRK"
require_pattern "$IMAGE_DIR/smoke-factory-reset.sh" 'cmp "\$IMMUTABLE_BEFORE" "\$IMMUTABLE_AFTER"' "factory-reset smoke proves ESP and root-slot bytes were preserved"
require_pattern "$IMAGE_DIR/smoke-factory-reset.sh" '^mv -- "\$NEW_RECOVERY_KEY" "\$RECOVERY_KEY_OUTPUT"$' "factory-reset smoke publishes the new recovery key only after verification"
require_pattern "$IMAGE_DIR/smoke-factory-reset.sh" '^if ! mv -- "\$TARGET_FILE" "\$OUTPUT_RAW"; then$' "factory-reset smoke publishes the reset image only after verification"
require_pattern "$IMAGE_DIR/smoke-factory-reset.sh" '^ECHO_FACTORY_RESET_SMOKE_OK|echo "ECHO_FACTORY_RESET_SMOKE_OK' "factory-reset smoke emits an evidence marker"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'smoke-factory-reset\.sh' "image CI executes the production factory-reset lifecycle"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'echo-os-factory-reset\.raw' "image CI boots the reset disk copy"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'echo-factory-reset-boot' "image CI retains the post-reset TPM-unlock boot log"
require_pattern "$REPO_ROOT/packaging/recovery/mkosi.conf" '^        gdisk$' "recovery can relocate a copied GPT backup header"
require_pattern "$REPO_ROOT/packaging/recovery/mkosi.conf" '^        gnupg$' "recovery includes release-signature verification tools"
require_pattern "$REPO_ROOT/packaging/recovery/mkosi.conf" '^        zstd$' "recovery can stream compressed installer payloads"
require_pattern "$REPO_ROOT/packaging/recovery/mkosi.conf" '^        openssl$' "Recovery can validate its signed-PCR RSA public key"
require_pattern "$REPO_ROOT/packaging/recovery/mkosi.conf" '^        mawk$' "recovery explicitly includes the installer's awk runtime"
require_pattern "$REPO_ROOT/packaging/recovery/mkosi.conf" '^        sed$' "recovery explicitly includes the installer's text runtime"
require_pattern "$REPO_ROOT/packaging/recovery/mkosi.conf" '^        udev$' "recovery explicitly includes installer device settling"
require_pattern "$REPO_ROOT/packaging/recovery/mkosi.conf" 'echo-os-installer:/usr/bin/echo-os-installer' "recovery ships the whole-disk installer"
require_pattern "$REPO_ROOT/packaging/recovery/mkosi.conf" 'verify_install_stream\.py:/usr/lib/echo-os/verify-install-stream\.py' "recovery ships the exact-byte stream verifier"
require_pattern "$REPO_ROOT/packaging/recovery/mkosi.conf" 'verify_public_keyring\.py:/usr/lib/echo-os/verify-public-keyring\.py' "recovery ships the public-only trust-root verifier"
require_pattern "$REPO_ROOT/packaging/recovery/build-recovery.sh" 'install-keyring\.gpg' "recovery embeds the release-selected installer trust root"
require_pattern "$REPO_ROOT/packaging/recovery/build-recovery.sh" 'RECOVERY_KEYRING_TREE/usr/lib/echo-os/install-keyring\.gpg' "mkosi receives the installer keyring through a directory tree"
require_pattern "$REPO_ROOT/packaging/recovery/build-recovery.sh" 'RECOVERY_KEYRING_TREE/usr/lib/systemd/tpm2-pcr-public-key\.pem' "Recovery embeds the signed-PCR trust root"
require_pattern "$REPO_ROOT/packaging/recovery/build-recovery.sh" '^RECOVERY_EXTRA_ARGS=\(--extra-tree="\$RECOVERY_KEYRING_TREE"\)$' "mkosi extra-tree uses its documented directory input"
require_pattern "$REPO_ROOT/packaging/recovery/mkosi.postinst.chroot" 'verify-public-keyring\.py' "finished Recovery rechecks its embedded public trust root"
require_pattern "$IMAGE_DIR/build-image.sh" 'ECHO_INSTALL_KEYRING is required' "production images cannot ship a trustless installer recovery"
require_pattern "$IMAGE_DIR/build-image.sh" 'ECHO_SECURE_BOOT_CONFIGURED.*yes' "production images require Secure Boot plus signed PCR policy"
require_pattern "$IMAGE_DIR/build-image.sh" 'mkosi --json' "release build records the fully resolved mkosi configuration"
require_pattern "$IMAGE_DIR/build-image.sh" 'python3 "\$MKOSI_SUMMARY_VERIFIER"' "release build rejects a downgraded resolved mkosi policy"
require_pattern "$IMAGE_DIR/verify-mkosi-summary.py" 'main\.get\("Verity"\), "enabled"' "resolved release policy requires dm-verity"
require_pattern "$IMAGE_DIR/verify-mkosi-summary.py" 'main\.get\("SecureBoot"\), True' "resolved release policy requires Secure Boot"
require_pattern "$IMAGE_DIR/verify-mkosi-summary.py" 'main\.get\("SignExpectedPcr"\), "enabled"' "resolved release policy requires signed expected PCRs"
require_pattern "$IMAGE_DIR/verify-mkosi-summary.py" 'item\.startswith\("root="\)' "resolved release policy forbids a mutable root selector"
require_pattern "$IMAGE_DIR/build-image.sh" 'tpm2-pcr-public-key\.pem' "main and Recovery roots receive the signed-PCR public key"
require_pattern "$REPO_ROOT/deploy/installer/create-install-bundle.sh" 'gpg .*--detach-sign' "install manifest receives a detached release signature"
require_pattern "$REPO_ROOT/deploy/installer/create-install-bundle.sh" 'ECHO_OS_SOURCE_MANIFEST must name the verified clean OS source identity' "installer signing requires pre-build clean OS provenance"
require_pattern "$REPO_ROOT/deploy/installer/create-install-bundle.sh" '"schema": 3' "signed installer manifest uses the OS-source-bound schema"
require_pattern "$REPO_ROOT/deploy/installer/create-install-bundle.sh" '"manifest_sha256": source_manifest_sha256' "signed installer manifest binds the retained OS source identity"
require_pattern "$REPO_ROOT/deploy/installer/create-install-bundle.sh" 'installer source partition contract mismatch' "release tooling validates GPT labels and types before signing"
require_pattern "$REPO_ROOT/deploy/installer/create-install-bundle.sh" 'verify-verity-set\.py' "installer release tooling authenticates the raw root integrity set"
require_pattern "$REPO_ROOT/deploy/installer/create-install-bundle.sh" 'mcopy -i "\$ESP_IMAGE"' "installer release tooling verifies the UKI actually stored in the raw ESP"
require_pattern "$REPO_ROOT/deploy/installer/create-install-bundle.sh" '"\$ROOT_DEVICE" "\$VERITY_DEVICE" "\$VERITY_SIG_DEVICE"' "installer signing verifies the raw block devices rather than detached stand-ins"
require_pattern "$REPO_ROOT/deploy/installer/create-install-bundle.sh" 'sbverify --cert "\$VERITY_CERTIFICATE" "\${MAIN_UKIS\[0\]}"' "installer release tooling verifies the desktop UKI Secure Boot signer"
require_pattern "$REPO_ROOT/deploy/installer/create-install-bundle.sh" 'sbverify --cert "\$VERITY_CERTIFICATE" "\$RECOVERY_UKI"' "installer release tooling verifies the Recovery UKI Secure Boot signer"
require_pattern "$REPO_ROOT/deploy/installer/create-install-bundle.sh" 'sbverify --cert "\$VERITY_CERTIFICATE" "\$SYSTEMD_BOOT"' "installer release tooling verifies the bootloader Secure Boot signer"
require_pattern "$REPO_ROOT/deploy/installer/create-install-bundle.sh" 'python3 "\$VERIFY_PCR_POLICY"' "installer release tooling verifies desktop and Recovery signed-PCR11 policies"
require_pattern "$REPO_ROOT/deploy/installer/create-install-bundle.sh" 'output directory must not be a symlink' "release tooling rejects redirected output paths"
require_pattern "$REPO_ROOT/deploy/installer/create-install-bundle.sh" 'python3 "\$VERIFY_BUNDLE" "\$BUNDLE_BUILD_DIR"' "release tooling verifies the staged bundle before publication"
require_pattern "$REPO_ROOT/deploy/installer/create-install-bundle.sh" 'gpgv --keyring "\$TRUST_KEYRING"' "release tooling proves Recovery trusts the installer signature"
require_pattern "$REPO_ROOT/deploy/installer/create-install-bundle.sh" 'python3 "\$VERIFY_KEYRING" "\$TRUST_KEYRING"' "release tooling rejects private material in the Recovery keyring"
require_pattern "$REPO_ROOT/deploy/installer/create-install-bundle.sh" '^mv -- "\$BUNDLE_BUILD_DIR" "\$OUTPUT_DIR"$' "release tooling atomically publishes only a complete bundle"
forbid_pattern "$REPO_ROOT/deploy/installer/create-install-bundle.sh" 'BEGIN PGP PRIVATE KEY|BEGIN PRIVATE KEY' "installer release tooling contains no private key material"
forbid_pattern "$REPO_ROOT/deploy/installer/create-install-bundle.sh" '<\(gpg' "release tooling verifies signatures from a regular public keyring"
require_pattern "$REPO_ROOT/deploy/installer/verify_install_bundle.py" 'actual_names != expected_names' "installer bundle rejects unsigned extra artifacts"
require_pattern "$REPO_ROOT/deploy/installer/verify_install_bundle.py" 'manifest\["schema"\] != 3' "installer accepts only the OS-source-bound manifest schema"
require_pattern "$REPO_ROOT/deploy/installer/verify_install_bundle.py" 'installer OS source identity is invalid' "installer rejects mutable or credentialed OS source identities"
require_pattern "$REPO_ROOT/deploy/installer/verify_install_bundle.py" 'payload_path\.is_symlink' "installer bundle rejects symlink payloads"
require_pattern "$REPO_ROOT/deploy/installer/verify_install_bundle.py" '^MAX_UNCOMPRESSED_SIZE = 64 \* 1024\*\*4$' "installer manifests have a bounded whole-disk size"
require_pattern "$REPO_ROOT/deploy/installer/verify_install_bundle.py" 'compressed payload size is inconsistent' "installer bundles bound compressed-media resource use"
require_pattern "$REPO_ROOT/deploy/installer/verify_install_bundle.py" '"direct_pcrs", "signed_pcrs", "public_key_sha256"' "signed manifest fixes the complete TPM2 policy contract"
require_pattern "$REPO_ROOT/deploy/installer/verify_install_bundle.py" 'signed_pcrs.*!= \[11\]' "installer accepts only vendor-signed PCR 11"
require_pattern "$REPO_ROOT/deploy/installer/verify_install_stream.py" 'source\.read\(1\) != b""' "installer stream rejects bytes beyond the signed size"
require_pattern "$REPO_ROOT/deploy/installer/verify_install_stream.py" 'decompressed image is truncated' "installer stream rejects bytes missing from the signed size"
require_pattern "$REPO_ROOT/deploy/installer/verify_public_keyring.py" '^SECRET_PACKET_TAGS = \{5, 7\}$' "installer trust roots reject OpenPGP secret key packets"
require_pattern "$REPO_ROOT/deploy/installer/verify_public_keyring.py" '^ALLOWED_PUBLIC_KEYRING_TAGS = \{2, 6, 12, 13, 14, 17\}$' "installer trust roots reject opaque OpenPGP packet types"
require_pattern "$REPO_ROOT/deploy/installer/echo-os-installer" 'gpgv --keyring' "Recovery authenticates the manifest against its signed-root keyring"
require_pattern "$REPO_ROOT/deploy/installer/echo-os-installer" 'python3 "\$VERIFY_KEYRING" "\$KEYRING"' "Recovery refuses a keyring containing private material"
require_pattern "$REPO_ROOT/deploy/installer/echo-os-installer" 'SIGNATURE_SIZE.*-le 1048576' "Recovery bounds signature input before invoking gpgv"
require_pattern "$REPO_ROOT/deploy/installer/echo-os-installer" '^  id install lsblk mktemp python3 readlink realpath resize2fs rm sed seq sgdisk \\' "installer explicitly checks every non-builtin text/device runtime"
require_pattern "$REPO_ROOT/deploy/installer/echo-os-installer" 'target or a child device is mounted' "installer refuses mounted targets"
require_pattern "$REPO_ROOT/deploy/installer/echo-os-installer" '/sys/class/block/.*holders' "installer inspects the kernel holder graph"
require_pattern "$REPO_ROOT/deploy/installer/echo-os-installer" 'has an active block holder' "installer refuses active dm-crypt, LVM and RAID targets"
require_pattern "$REPO_ROOT/deploy/installer/echo-os-installer" 'disk containing the installer bundle' "installer refuses its own source media"
require_pattern "$REPO_ROOT/deploy/installer/echo-os-installer" 'disk backing the running recovery root' "installer refuses the running recovery disk"
require_pattern "$REPO_ROOT/deploy/installer/echo-os-installer" 'INSTALL-ECHO-OS:' "installer requires a per-disk confirmation token"
require_pattern "$REPO_ROOT/deploy/installer/echo-os-installer" '^target_identity_record\(\) \{$' "installer records the exact block-device identity"
require_pattern "$REPO_ROOT/deploy/installer/echo-os-installer" 'LOCKED_TARGET_IDENTITY="\$\(target_identity_record\)"' "installer re-reads device identity after taking the disk lock"
require_pattern "$REPO_ROOT/deploy/installer/echo-os-installer" 'target identity changed after confirmation' "installer fails closed when the confirmed target is replaced"
require_pattern "$REPO_ROOT/deploy/installer/echo-os-installer" '^blockdev --flushbufs "\$TARGET"$' "installer flushes buffered target data before verification"
require_pattern "$REPO_ROOT/deploy/installer/echo-os-installer" 'iflag=direct,fullblock,count_bytes' "installer verifies the exact signed byte count with direct block I/O"
forbid_pattern "$REPO_ROOT/deploy/installer/echo-os-installer" 'head -c "\$UNCOMPRESSED_SIZE" "\$TARGET"' "installer never mistakes a buffered page-cache hash for physical readback"
require_pattern "$REPO_ROOT/deploy/installer/echo-os-installer" 'post-write SHA-256 mismatch' "installer verifies all decompressed bytes after writing"
require_pattern "$REPO_ROOT/deploy/installer/echo-os-installer" 'python3 "\$VERIFY_STREAM" "\$UNCOMPRESSED_SIZE" \|' "installer writes only an exact signed-length decompressed stream"
require_pattern "$REPO_ROOT/deploy/installer/echo-os-installer" 'installer bundle and Recovery signed-PCR public keys do not match' "installer authenticates the exact TPM2 policy key before enrollment"
require_pattern "$REPO_ROOT/deploy/installer/echo-os-installer" 'TPM2_ENROLLMENT_ARGS=\(--tpm2-public-key' "installer always supplies the authenticated signed-PCR key"
require_pattern "$REPO_ROOT/deploy/installer/echo-os-installer" '^sgdisk -e "\$TARGET"$' "installer moves the backup GPT to the physical disk end"
require_pattern "$REPO_ROOT/deploy/installer/echo-os-installer" 'systemd-repart \\' "installer expands the signed layout incrementally"
require_pattern "$REPO_ROOT/deploy/installer/echo-os-installer" '^resize2fs "\$HOME_MAPPING"$' "installer grows the encrypted home filesystem mapping"
forbid_pattern "$REPO_ROOT/deploy/installer/echo-os-installer" '/bin/sh|sh -c|eval ' "installer never evaluates bundle or device text in a command shell"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'Create the authenticated installer bundle' "image CI emits a signed installable artifact"
require_pattern "$REPO_ROOT/deploy/installer/smoke-installer-plan.sh" '^exec "\$SCRIPT_DIR/smoke-installer-disk\.sh" plan "\$@"$' "legacy plan smoke delegates to the shared disk harness"
require_pattern "$REPO_ROOT/deploy/installer/smoke-installer-install.sh" '^exec "\$SCRIPT_DIR/smoke-installer-disk\.sh" install "\$@"$' "write smoke delegates to the shared disk harness"
require_pattern "$REPO_ROOT/deploy/installer/smoke-installer-disk.sh" 'PLAN_OUTPUT=.*"\$INSTALLER" plan ' "disk smoke authenticates and plans before any installation"
require_pattern "$REPO_ROOT/deploy/installer/smoke-installer-disk.sh" 'BEFORE_STAT=.*stat' "disk smoke records the untouched target state"
require_pattern "$REPO_ROOT/deploy/installer/smoke-installer-disk.sh" 'AFTER_PLAN_STAT=.*stat' "disk smoke proves planning left its target unchanged"
require_pattern "$REPO_ROOT/deploy/installer/smoke-installer-disk.sh" 'refusing to replace host installer state' "disk smoke never overwrites an installed host trust root"
require_pattern "$REPO_ROOT/deploy/installer/smoke-installer-disk.sh" '^modprobe nbd max_part=16$' "disk smoke uses an isolated whole-disk NBD target"
require_pattern "$REPO_ROOT/deploy/installer/smoke-installer-disk.sh" '^dmsetup create "\$ACTIVE_HOLDER_MAPPING" \\' "disk smoke constructs an active holder over only the disposable target"
require_pattern "$REPO_ROOT/deploy/installer/smoke-installer-disk.sh" 'installer accepted a target with an active block holder' "disk smoke requires the production installer to reject active holders"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'cryptsetup-bin dmsetup systemd-cryptsetup' "image CI installs the active-holder test runtime explicitly"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'e2fsprogs fdisk gdisk zstd' "image CI installs the production installer GPT runtime explicitly"
require_pattern "$REPO_ROOT/.github/workflows/ab-update-smoke.yml" 'e2fsprogs fdisk gdisk zstd' "A/B CI installs the production installer GPT runtime explicitly"
require_pattern "$REPO_ROOT/deploy/installer/smoke-installer-disk.sh" '^       --discard=unmap \\$' "NBD write smoke preserves sparse zero ranges"
require_pattern "$REPO_ROOT/deploy/installer/smoke-installer-disk.sh" '^       --detect-zeroes=unmap \\$' "NBD write smoke maps explicit zero writes back to sparse holes"
require_pattern "$REPO_ROOT/deploy/installer/smoke-installer-disk.sh" '"\$INSTALLER" install "\$BUNDLE" "\$NBD_DEVICE" "\$CONFIRMATION"' "disk smoke invokes the production destructive install action"
require_pattern "$REPO_ROOT/deploy/installer/smoke-installer-disk.sh" '^grep -Eq "\^ECHO_INSTALL_COMPLETE ' "disk smoke requires the production completion marker"
require_pattern "$REPO_ROOT/deploy/installer/echo-os-installer" 'ECHO_INSTALL_BUNDLE_AUTHENTICATED action=\$ACTION version=\$IMAGE_VERSION manifest=\$MANIFEST_SHA256 source=\$UNCOMPRESSED_SHA256' "installer logs the exact authenticated manifest and source raw"
require_pattern "$REPO_ROOT/deploy/installer/echo-os-installer" 'ECHO_INSTALL_COMPLETE target=\$TARGET version=\$IMAGE_VERSION source=\$UNCOMPRESSED_SHA256' "installer completion retains the source raw identity"
require_pattern "$REPO_ROOT/deploy/installer/smoke-installer-disk.sh" 'ECHO_INSTALL_TARGET_LOCKED' "disk smoke requires locked-target identity evidence"
require_pattern "$REPO_ROOT/deploy/installer/smoke-installer-disk.sh" 'direct post-flush readback' "disk smoke requires physical readback evidence"
require_pattern "$REPO_ROOT/deploy/installer/smoke-installer-disk.sh" 'installed home did not grow beyond the signed source image' "disk smoke requires home growth beyond the signed image"
require_pattern "$REPO_ROOT/deploy/installer/smoke-installer-disk.sh" 'installed home left unexpected trailing disk capacity' "disk smoke requires home to consume the target tail"
require_pattern "$REPO_ROOT/deploy/installer/smoke-installer-disk.sh" '^mv -- "\$TARGET_FILE" "\$OUTPUT_RAW"$' "installed raw is published atomically only after verification"
forbid_pattern "$REPO_ROOT/deploy/installer/smoke-installer-disk.sh" '/bin/sh|sh -c|eval ' "disk smoke never evaluates bundle, output or device text in a command shell"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'Install onto an ephemeral whole disk' "image CI executes a real disposable whole-disk install"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'smoke-installer-install\.sh' "image CI uses the production installer write harness"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'smoke-recovery-image\.sh' "image CI retains the independent Recovery boot gate"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'smoke-login-image\.sh' "image CI retains production SDDM boot gates"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'smoke-boot-image\.sh' "image CI retains the direct desktop boot gate"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'smoke-agent-recovery-image\.sh' "image CI cold-boots one persisted interrupted Agent task"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'test_verify_agent_recovery_fixture\.py' "image CI rejects unsafe or mutable recovery fixtures before building"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" '"\$RUNNER_TEMP/echo-os-installed\.raw"' "all post-install boot gates receive the installed whole-disk raw"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'options: --privileged --volume /lib/modules:/lib/modules:ro' "image CI exposes only the host module tree needed for NBD"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'qemu-utils' "image CI installs the NBD userspace client"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'swtpm swtpm-tools tpm2-tools libtss2-tcti-swtpm0t64' "image CI installs a real virtual TPM stack"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'echo-swtpm-ci initialize' "image CI creates one persistent virtual TPM identity"
require_pattern "$REPO_ROOT/.github/workflows/ab-update-smoke.yml" 'echo-swtpm-ci initialize' "A/B CI preserves one virtual TPM across update boots"
require_pattern "$REPO_ROOT/deploy/data-protection/echo-swtpm-ci" 'sensitivedataorigin\|userwithauth\|noda' "virtual TPM SRK uses the systemd-compatible storage template"
require_pattern "$REPO_ROOT/deploy/data-protection/echo-swtpm-ci" '^      systemd-analyze srk >' "systemd itself exports the virtual TPM SRK"
require_pattern "$REPO_ROOT/deploy/data-protection/echo-swtpm-ci" '^    cmp "\$TOOLS_SRK_OUTPUT" "\$SRK_OUTPUT"' "virtual TPM SRK exports agree byte for byte"
require_pattern "$IMAGE_DIR/smoke-boot-image.sh" 'tpm-tis,tpmdev=tpm0' "every QEMU boot attaches the persistent TPM2 device"
require_pattern "$REPO_ROOT/deploy/installer/smoke-installer-disk.sh" 'ECHO_INSTALL_TPM2_DEVICE_KEY' "installer smoke enrolls against that virtual TPM SRK"
require_pattern "$IMAGE_DIR/smoke-login-image.sh" 'ECHO_DATA_RECOVERY_KEY' "host test mutations require the private recovery key"
require_pattern "$IMAGE_DIR/smoke-agent-recovery-image.sh" 'ECHO_DATA_RECOVERY_KEY' "Agent recovery smoke opens encrypted state only with the private recovery key"
require_pattern "$IMAGE_DIR/smoke-agent-recovery-image.sh" '^cp --reflink=auto --sparse=always' "Agent recovery smoke mutates only a disposable raw copy"
require_pattern "$IMAGE_DIR/smoke-agent-recovery-image.sh" '^ECHO_EXPECT_AGENT_RECOVERY_COUNT=1 \\$' "Agent recovery boot requires exactly one discovered task"
require_pattern "$IMAGE_DIR/smoke-agent-recovery-image.sh" '^ECHO_BOOT_EPHEMERAL=no \\$' "Agent recovery evidence persists long enough for post-boot inspection"
require_pattern "$IMAGE_DIR/smoke-agent-recovery-image.sh" 'verify "\$FIXTURE"' "Agent recovery fixture is validated before disk injection"
require_pattern "$IMAGE_DIR/smoke-agent-recovery-image.sh" 'unchanged "\$FIXTURE" "\$OBSERVED_STORE"' "post-boot Agent task state must be byte-for-byte unchanged"
forbid_pattern "$IMAGE_DIR/smoke-agent-recovery-image.sh" '/api/task-runs/.*/(takeover|resume-execution)|curl |urllib' "cold-boot recovery smoke cannot call a mutating Agent API"
require_pattern "$IMAGE_DIR/smoke-ab-update.sh" '"\$ENCRYPTED_IMAGE" copy-' "A/B persistence checks open encrypted var explicitly"
forbid_pattern "$REPO_ROOT/.github/workflows/os-image.yml" '^            .*echo-installed-recovery\.key$' "image artifacts never upload the test recovery key"
forbid_pattern "$REPO_ROOT/.github/workflows/os-image.yml" '^            .*echo-factory-reset-recovery\.key$' "image artifacts never upload the rotated factory-reset recovery key"
forbid_pattern "$REPO_ROOT/.github/workflows/os-image.yml" '^            .*echo-os-factory-reset\.raw$' "image artifacts never upload the credential-gated reset disk copy"
forbid_pattern "$REPO_ROOT/.github/workflows/os-image.yml" '^            .*echo-rotated-recovery\.key$' "image artifacts never upload the rotated lifecycle recovery key"
forbid_pattern "$REPO_ROOT/.github/workflows/os-image.yml" '^            .*echo-os-replacement-tpm\.raw$' "image artifacts never upload the replacement-TPM disk copy"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'test_verify_install_stream\.py' "image CI runs exact-byte installer stream tests"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'test_verify_public_keyring\.py' "image CI tests that Recovery trust roots remain public-only"
require_pattern "$REPO_ROOT/deploy/oem/echo-oem-setup.service" '^ConditionPathExists=!/var/lib/echo-os/oem-complete\.json$' "OEM setup is one-time state"
require_pattern "$REPO_ROOT/deploy/oem/echo-oem-setup.service" '^ConditionCredential=!echo\.os\.ci-session$' "VM credential bypasses interactive OEM setup"
require_pattern "$REPO_ROOT/deploy/oem/echo-oem-setup.service" '^LoadCredential=echo\.os\.oem$' "OEM service imports only its named system credential"
require_pattern "$REPO_ROOT/deploy/oem/echo_oem_setup.py" '^MAX_OEM_CREDENTIAL_SIZE = 8192$' "OEM provisioning credential has a strict size bound"
require_pattern "$REPO_ROOT/deploy/oem/echo_oem_setup.py" '^MAX_DEVICE_NAME_LENGTH = 15$' "OEM device identity stays compatible with SMB discovery"
require_pattern "$REPO_ROOT/deploy/oem/echo_oem_setup.py" 'os\.O_NOFOLLOW' "OEM provisioning never follows a credential symlink"
require_pattern "$REPO_ROOT/deploy/oem/echo_oem_setup.py" 'OEM credential has unexpected or missing fields' "OEM provisioning accepts only the exact credential schema"
require_pattern "$REPO_ROOT/deploy/oem/echo_oem_setup.py" '"--configure-values"' "credential provisioning validates regional values through the production tool"
require_pattern "$REPO_ROOT/deploy/oem/echo_oem_setup.py" 'ECHO_OEM_PROVISIONED account=' "OEM completion emits an auditable non-secret readiness marker"
forbid_pattern "$REPO_ROOT/deploy/oem/echo_oem_setup.py" 'shell=True|/bin/sh|sh -c|--password' "OEM setup never passes credentials through a command shell or password argument"
require_pattern "$REPO_ROOT/deploy/machine-state/echo-region-state" '^def configure_values\(' "OEM regional provisioning uses a noninteractive fixed-argument entrypoint"
require_pattern "$REPO_ROOT/deploy/machine-state/echo-region-state" 'readiness\(region, "oem-credential"\)' "OEM regional provisioning emits a distinct readiness marker"
require_pattern "$IMAGE_DIR/smoke-oem-image.sh" '^ECHO_LOGIN_PROVISION_MODE=oem-credential \\$' "OEM cold-boot smoke selects credential-backed first use"
require_pattern "$IMAGE_DIR/smoke-oem-image.sh" '^  ECHO_LOGIN_OUTPUT_IMAGE="\$2"$' "OEM smoke can publish a provisioned lifecycle image"
require_pattern "$IMAGE_DIR/smoke-login-image.sh" '^ECHO_BOOT_OEM_CREDENTIAL_FILE="\$OEM_CREDENTIAL_INPUT" \\$' "OEM credential is passed only as an ephemeral VM input"
require_pattern "$IMAGE_DIR/smoke-login-image.sh" '"\$ENCRYPTED_IMAGE" remove "\$LOGIN_IMAGE"' "provisioned image cleanup opens only the disposable encrypted lifecycle copy"
require_pattern "$IMAGE_DIR/smoke-login-image.sh" '"\$ENCRYPTED_IMAGE" assert-absent' "published OEM image verifies removal of its test autologin policy"
require_pattern "$IMAGE_DIR/smoke-login-image.sh" '^  mv -- "\$LOGIN_IMAGE" "\$OUTPUT_RAW"$' "provisioned image is published only after state and cleanup checks"
require_pattern "$IMAGE_DIR/smoke-boot-image.sh" 'CREDENTIAL_ARGS\+=\(--credential "\$OEM_CREDENTIAL_FILE"\)' "mkosi receives the OEM credential from a private host file"
require_pattern "$IMAGE_DIR/smoke-boot-image.sh" 'Echo OS first-boot OEM cold-boot smoke OK' "OEM boot gate requires provisioning followed by the production session"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'Secure-Boot OEM first-use from the installed disk' "image CI exercises first-use provisioning on the installed raw"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'smoke-oem-image\.sh' "image CI invokes the OEM cold-boot gate"
require_pattern "$REPO_ROOT/deploy/oem/sddm.service.d/10-echo-oem.conf" '^Requires=echo-machine-identity-health\.service echo-network-state-prepare\.service echo-region-state-restore\.service echo-oem-setup\.service echo-local-account\.service$' "graphical login requires stable device state, OEM setup and account restoration"
require_pattern "$REPO_ROOT/deploy/oem/sddm.service.d/10-echo-oem.conf" '^Wants=echo-app-catalog\.service$' "graphical login orders non-blocking application catalog setup"
require_pattern "$REPO_ROOT/deploy/oem/sddm.service.d/10-echo-oem.conf" '^ConditionCredential=!echo\.os\.ci-session$' "production display manager excludes CI sessions"
require_pattern "$REPO_ROOT/deploy/oem/echo.desktop" '^Exec=/opt/echo-os/deploy/desktop-session/echo-desktop-session\.sh$' "SDDM launches the packaged Echo session"
require_pattern "$REPO_ROOT/deploy/oem/echo-wayland.desktop" '^TryExec=/opt/echo-os/deploy/desktop-session/echo-wayland-session\.sh$' "SDDM exposes Wayland only when its packaged launcher exists"
forbid_pattern "$REPO_ROOT/deploy/oem/sddm.conf" '^\[Autologin\]|^User=echo$' "production SDDM configuration contains no autologin"
require_pattern "$REPO_ROOT/deploy/oem/echo-login-health.service" '^ConditionCredential=!echo\.os\.ci-session$' "production login has a separate health path"
require_pattern "$REPO_ROOT/deploy/oem/verify-login-boot.sh" 'ECHO_LOGIN_READY' "login health emits an auditable readiness marker"
require_pattern "$REPO_ROOT/deploy/oem/echo_oem_setup.py" '\["/usr/sbin/chpasswd"\]' "OEM password is delegated to the system account database"
require_pattern "$REPO_ROOT/deploy/oem/echo_oem_setup.py" '"--groups", "sudo"' "provisioned local user becomes a password-protected administrator"
require_pattern "$REPO_ROOT/deploy/oem/echo_oem_setup.py" 'SHADOW_STATE = STATE_DIRECTORY / "local-account\.shadow"' "password hash survives root replacement in private persistent state"
require_pattern "$REPO_ROOT/deploy/oem/echo_oem_setup.py" '\["/usr/sbin/chpasswd", "--encrypted"\]' "new A/B root restores only the stored password hash"
require_pattern "$REPO_ROOT/deploy/oem/echo_oem_setup.py" 'marker\["root_version"\] == current_version' "same-root account locks are never auto-reversed"
require_pattern "$REPO_ROOT/deploy/oem/echo_oem_setup.py" 'REGION_STATE_TOOL.*--configure' "OEM first boot configures locale, keymap and timezone"
require_pattern "$REPO_ROOT/deploy/oem/echo-oem-setup.service" '^Requires=echo-region-state-restore\.service$' "OEM configuration starts from initialized regional state"
require_pattern "$REPO_ROOT/deploy/oem/echo-account-capture.path" '^PathChanged=/etc/shadow$' "local password changes refresh persistent state"
require_pattern "$REPO_ROOT/deploy/oem/echo-account-capture.path" '^PathChanged=/etc/hostname$' "local hostname changes refresh persistent state"
forbid_pattern "$REPO_ROOT/deploy/oem/echo_oem_setup.py" 'shell=True' "OEM setup never invokes a command shell"
require_pattern "$REPO_ROOT/deploy/apps/echo-app-catalog.service" '^RequiresMountsFor=/var/lib/flatpak /var/lib/echo-os$' "application catalog state is bound to persistent storage"
require_pattern "$REPO_ROOT/deploy/apps/echo-app-catalog.service" '^ConditionPathExists=!/var/lib/echo-os/app-catalog-provisioned$' "default catalog is provisioned only once per device"
require_pattern "$REPO_ROOT/deploy/apps/echo-app-catalog" '^REMOTE_DEFINITION_SHA256=.*3371dd250e61d9e1633630073fefda153cd4426f72f4afa0c3373ae2e8fea03a' "Flathub repository definition is checksum-pinned"
require_pattern "$REPO_ROOT/deploy/apps/echo-app-catalog" 'remote-add --system --if-not-exists --from' "Flathub trust is installed from the local definition"
require_pattern "$REPO_ROOT/deploy/apps/flathub.flatpakrepo" '^Url=https://dl\.flathub\.org/repo/$' "Flathub catalog uses the official HTTPS repository"
require_pattern "$REPO_ROOT/deploy/apps/flathub.flatpakrepo" '^GPGKey=.+$' "Flathub catalog ships its public verification key"
forbid_pattern "$REPO_ROOT/deploy/apps/flathub.flatpakrepo" 'GPGVerify=false|NoGPGVerify=true' "application catalog never disables signature verification"
require_pattern "$REPO_ROOT/deploy/apps/echo-app-store.desktop" '^Exec=/usr/bin/plasma-discover --backends flatpak %U$' "Echo App Store whitelists only the persistent Flatpak backend"
require_pattern "$REPO_ROOT/deploy/apps/org.kde.discover.desktop" '^Hidden=true$' "generic PackageKit-capable Discover launcher is masked"
require_pattern "$REPO_ROOT/deploy/apps/echo-portals.conf" '^default=kde$' "sandboxed applications use the KDE portal implementation"
require_pattern "$REPO_ROOT/deploy/machine-state/echo-machine-id" '^var_device=.*echo-var' "systemd initrd identity is sourced from the persistent var partition"
require_pattern "$REPO_ROOT/deploy/machine-state/echo-machine-id" 'cryptsetup open --type luks2 --tries 3' "systemd initrd unlocks encrypted var before binding machine identity"
require_pattern "$REPO_ROOT/deploy/machine-state/echo-machine-id" 'unlocking encrypted device state through TPM2 or recovery key' "systemd initrd exposes the TPM2-then-recovery unlock path"
require_pattern "$REPO_ROOT/deploy/machine-state/echo-machine-id" 'mount --bind .*state_file.*root_machine_id' "persistent identity is bound before PID 1 starts"
require_pattern "$REPO_ROOT/deploy/machine-state/echo-machine-id" 'upperdir=\$etc_upper,workdir=\$etc_work' "mutable /etc is an overlay whose upper layer lives on encrypted var"
require_pattern "$REPO_ROOT/deploy/machine-state/echo-machine-id" 'remount,bind,ro.*etc_lower' "the versioned vendor /etc remains a read-only lower layer"
require_pattern "$REPO_ROOT/deploy/machine-state/echo-machine-id" '^mount "\$var_device" "\$var_mount"' "encrypted var is mounted inside the verified sysroot"
forbid_pattern "$REPO_ROOT/deploy/machine-state/echo-machine-id" 'umount "\$var_mount"' "the active /etc overlay cannot lose its encrypted backing filesystem"
require_pattern "$REPO_ROOT/deploy/machine-state/echo-machine-id" '00000000000000000000000000000000' "all-zero machine identities are rejected"
require_pattern "$IMAGE_DIR/mkosi.extra/etc/crypttab" '^echo-var PARTLABEL=echo-var none tpm2-device=auto,token-timeout=10s,tries=3,x-initrd\.attach$' "crypttab maps encrypted var in the systemd initrd"
require_pattern "$REPO_ROOT/deploy/data-protection/echo-encrypted-image" 'mount -o ro,noload "\$ROOT_PARTITION"' "offline lifecycle tooling cannot replay or mutate the signed root"
forbid_pattern "$IMAGE_DIR/build-image.sh" 'systemd-dissect --copy-from' "build-time root inspection cannot mount the signed payload writable"
forbid_pattern "$IMAGE_DIR/verify-image.sh" '^(if )?systemd-dissect --copy-from' "artifact root inspection cannot mount the signed payload writable"
require_pattern "$REPO_ROOT/deploy/data-protection/echo-encrypted-image" 'upperdir=\$ETC_OVERLAY/upper,workdir=\$ETC_OVERLAY/work' "offline /etc changes use the encrypted persistent overlay"
require_pattern "$IMAGE_DIR/mkosi.extra/etc/crypttab" '^echo-home PARTLABEL=echo-home none tpm2-device=auto,token-timeout=10s,tries=3$' "crypttab maps encrypted home through the TPM2 token"
require_pattern "$IMAGE_DIR/mkosi.extra/etc/crypttab" '^echo-swap PARTLABEL=echo-swap none tpm2-device=auto,token-timeout=10s,tries=3$' "crypttab maps encrypted swap through the TPM2 token"
require_pattern "$IMAGE_DIR/mkosi.extra/etc/fstab" '^/dev/mapper/echo-var /var ext4 defaults,x-systemd.device-timeout=30s 0 2$' "fstab mounts decrypted var"
require_pattern "$IMAGE_DIR/mkosi.extra/etc/fstab" '^/dev/mapper/echo-home /home ext4 defaults,x-systemd.device-timeout=30s 0 2$' "fstab mounts decrypted home"
require_pattern "$IMAGE_DIR/mkosi.extra/etc/fstab" '^/dev/mapper/echo-swap none swap sw,x-systemd.device-timeout=30s 0 0$' "fstab activates decrypted swap"
require_pattern "$REPO_ROOT/deploy/machine-state/verify-machine-identity.sh" 'systemd-id128 machine-id --app-specific=' "health logs only a non-reversible machine identity"
forbid_pattern "$REPO_ROOT/deploy/machine-state/verify-machine-identity.sh" 'ECHO_MACHINE_ID_READY.*active_id|ECHO_MACHINE_ID_READY.*persistent_id' "health logs never expose the raw machine-id"
require_pattern "$REPO_ROOT/deploy/machine-state/20-echo-persistent-connections.conf" '^path=/var/lib/NetworkManager/system-connections$' "NetworkManager reads and writes profiles on persistent var"
require_pattern "$REPO_ROOT/deploy/machine-state/echo-network-state-prepare.service" '^RequiresMountsFor=/var/lib/NetworkManager /var/lib/echo-os$' "network-state preparation is bound to persistent storage"
require_pattern "$REPO_ROOT/deploy/machine-state/NetworkManager.service.d/10-echo-persistent-state.conf" '^Requires=echo-network-state-prepare\.service$' "NetworkManager fails closed if persistent state preparation fails"
require_pattern "$REPO_ROOT/deploy/machine-state/echo-network-state-prepare" 'find .* -type f -print0' "legacy migration considers regular profiles only"
require_pattern "$REPO_ROOT/deploy/machine-state/echo-network-state-prepare" 'file_mode.*600.*file_mode.*400' "legacy migration accepts only private profile modes"
# shellcheck disable=SC2016 # This is a regex for source text, not an expansion.
require_pattern "$REPO_ROOT/deploy/machine-state/echo-network-state-prepare" '\[\[ -e "\$destination" \]\] && continue' "legacy migration never overwrites persistent profiles"
require_pattern "$REPO_ROOT/deploy/machine-state/echo-network-state-prepare" 'ECHO_NETWORK_STATE_READY storage=/var/lib/NetworkManager/system-connections' "network-state preparation emits an auditable readiness marker"
require_pattern "$REPO_ROOT/deploy/machine-state/echo-region-state" 'localectl", "list-locales"' "region choices come from installed system locale data"
require_pattern "$REPO_ROOT/deploy/machine-state/echo-region-state" 'localectl", "list-keymaps"' "region choices come from installed system keymaps"
require_pattern "$REPO_ROOT/deploy/machine-state/echo-region-state" 'timedatectl", "list-timezones"' "region choices come from installed timezone data"
require_pattern "$REPO_ROOT/deploy/machine-state/echo-region-state" 'localectl", "set-locale"' "locale is applied through the systemd locale interface"
require_pattern "$REPO_ROOT/deploy/machine-state/echo-region-state" 'localectl", "set-keymap"' "console and X11 keymap are applied through systemd"
require_pattern "$REPO_ROOT/deploy/machine-state/echo-region-state" 'timedatectl", "set-timezone"' "timezone is applied through systemd"
forbid_pattern "$REPO_ROOT/deploy/machine-state/echo-region-state" 'shell=True|/bin/sh|sh -c' "regional settings never invoke a command shell"
require_pattern "$REPO_ROOT/deploy/machine-state/echo-region-state-restore.service" '^RequiresMountsFor=/var/lib/echo-os$' "regional restore is bound to persistent storage"
require_pattern "$REPO_ROOT/deploy/machine-state/echo-region-state-capture.path" '^After=local-fs\.target echo-oem-setup\.service$' "regional watcher cannot capture a partial OEM transaction"
require_pattern "$REPO_ROOT/deploy/machine-state/echo-region-state-capture.path" '^PathChanged=/etc/locale\.conf$' "locale changes refresh persistent state"
require_pattern "$REPO_ROOT/deploy/machine-state/echo-region-state-capture.path" '^PathChanged=/etc/vconsole\.conf$' "keymap changes refresh persistent state"
require_pattern "$REPO_ROOT/deploy/machine-state/echo-region-state-capture.path" '^PathChanged=/etc/localtime$' "timezone changes refresh persistent state"
require_pattern "$REPO_ROOT/deploy/update/echo-os-update" 'REGION_STATE_TOOL.*--capture' "A/B apply captures current regional state immediately before root replacement"
for locale_definition in en_US en_GB zh_CN zh_TW ja_JP ko_KR de_DE fr_FR es_ES pt_BR; do
  require_pattern "$REPO_ROOT/deploy/machine-state/locale.gen" "^${locale_definition}\\.UTF-8 UTF-8$" "locale ${locale_definition}.UTF-8 is compiled"
done
if command -v sha256sum >/dev/null 2>&1; then
  FLATHUB_DEFINITION_SHA256="$(sha256sum "$REPO_ROOT/deploy/apps/flathub.flatpakrepo" | awk '{print $1}')"
else
  FLATHUB_DEFINITION_SHA256="$(shasum -a 256 "$REPO_ROOT/deploy/apps/flathub.flatpakrepo" | awk '{print $1}')"
fi
if [[ "$FLATHUB_DEFINITION_SHA256" == 3371dd250e61d9e1633630073fefda153cd4426f72f4afa0c3373ae2e8fea03a ]]; then
  pass "bundled Flathub definition matches its independent SHA-256 pin"
else
  fail "bundled Flathub definition does not match its SHA-256 pin"
fi
require_pattern "$IMAGE_DIR/smoke-boot-image.sh" 'CREDENTIAL_ARGS=\(--credential=echo\.os\.ci-session=1\)' "cold-boot CI can use an ephemeral system credential"
require_pattern "$IMAGE_DIR/smoke-boot-image.sh" 'ECHO_MACHINE_ID_READY derived=\[0-9a-f\]\{32\}' "desktop and login smokes require stable machine identity"
require_pattern "$IMAGE_DIR/smoke-boot-image.sh" 'ECHO_NETWORK_STATE_READY storage=/var/lib/NetworkManager/system-connections' "desktop and login smokes require persistent network state"
require_pattern "$IMAGE_DIR/smoke-boot-image.sh" 'ECHO_REGION_STATE_READY locale=' "desktop and login smokes require regional state"
require_pattern "$IMAGE_DIR/smoke-boot-image.sh" 'ECHO_LOCK_SERVICE_READY provider=xss-lock pam=echo-lock' "desktop and login smokes require the lock coordinator"
require_pattern "$IMAGE_DIR/smoke-boot-image.sh" 'ECHO_LOCK_SCREEN_LAUNCHED provider=xsecurelock pam=echo-lock' "direct desktop smoke launches the PAM locker through logind"
require_pattern "$IMAGE_DIR/smoke-boot-image.sh" 'ECHO_AGENT_READY source=\[0-9a-f\]\{40\}.*endpoint=http://127.*8000 recovery=' "every installed desktop smoke requires the image-baked Agent and its recovery count"
require_pattern "$IMAGE_DIR/smoke-boot-image.sh" 'ECHO_EXPECT_AGENT_RECOVERY_COUNT' "raw boot harness can require an exact interrupted-task count"
require_pattern "$IMAGE_DIR/smoke-boot-image.sh" 'ECHO_SYSTEM_CONTROLS_READY provider=linux-native bridge=ready wifi=ready bluetooth=ready audio=ready display=ready battery=' "every installed desktop smoke requires the native Linux control bridge"
require_pattern "$IMAGE_DIR/smoke-boot-image.sh" 'ECHO_AUTH_AGENT_READY provider=polkit-kde session=' "every installed desktop smoke requires an interactive authorization agent"
require_pattern "$IMAGE_DIR/smoke-boot-image.sh" 'ECHO_POWER_MANAGEMENT_READY provider=powerdevil upower=ready profiles=ready session=' "every installed desktop smoke observes the native power-management chain"
POWER_MANAGEMENT_GATE_COUNT="$(grep -c '"\$POWER_MANAGEMENT_READY" -eq 1' "$IMAGE_DIR/smoke-boot-image.sh")"
[[ "$POWER_MANAGEMENT_GATE_COUNT" -eq 4 ]] || {
  echo "all four installed desktop completion gates must require power management" >&2
  exit 1
}
pass "all installed desktop completion gates require native power management"
require_pattern "$IMAGE_DIR/smoke-boot-image.sh" 'ECHO_NOTIFICATION_SERVICE_READY provider=echo-native dbus=ready socket=private session=' "every installed desktop smoke observes the native notification service"
NOTIFICATION_GATE_COUNT="$(grep -c '"\$NOTIFICATION_SERVICE_READY" -eq 1' "$IMAGE_DIR/smoke-boot-image.sh")"
[[ "$NOTIFICATION_GATE_COUNT" -eq 4 ]] || {
  echo "all four installed desktop completion gates must require native notifications" >&2
  exit 1
}
pass "all installed desktop completion gates require native notifications"
require_pattern "$IMAGE_DIR/smoke-boot-image.sh" 'ECHO_INPUT_METHOD_READY provider=fcitx5 dbus=ready frontend=' "every installed desktop smoke observes the multilingual input method"
INPUT_METHOD_GATE_COUNT="$(grep -c '"\$INPUT_METHOD_READY" -eq 1' "$IMAGE_DIR/smoke-boot-image.sh")"
[[ "$INPUT_METHOD_GATE_COUNT" -eq 4 ]] || {
  echo "all four installed desktop completion gates must require multilingual input" >&2
  exit 1
}
pass "all installed desktop completion gates require multilingual input"
require_pattern "$IMAGE_DIR/smoke-boot-image.sh" 'ECHO_CLIPBOARD_READY provider=klipper-qml dbus=ready storage=runtime-tmpfs persistence=off session=' "every installed desktop smoke observes the system clipboard manager"
CLIPBOARD_GATE_COUNT="$(grep -c '"\$CLIPBOARD_READY" -eq 1' "$IMAGE_DIR/smoke-boot-image.sh")"
[[ "$CLIPBOARD_GATE_COUNT" -eq 4 ]] || {
  echo "all four installed desktop completion gates must require the system clipboard" >&2
  exit 1
}
pass "all installed desktop completion gates require the system clipboard"
require_pattern "$IMAGE_DIR/smoke-boot-image.sh" 'ECHO_ACCESSIBILITY_READY provider=at-spi2 dbus=ready qt=enabled session=' "every installed desktop smoke observes the AT-SPI session bus"
require_pattern "$IMAGE_DIR/smoke-boot-image.sh" 'ECHO_ACCESSIBILITY_TREE_READY provider=at-spi2 application=echo' "every installed desktop smoke observes the fixed Echo accessible tree marker"
ACCESSIBILITY_GATE_COUNT="$(grep -c '"\$ACCESSIBILITY_READY" -eq 1' "$IMAGE_DIR/smoke-boot-image.sh")"
[[ "$ACCESSIBILITY_GATE_COUNT" -eq 4 ]] || {
  echo "all four installed desktop completion gates must require accessibility" >&2
  exit 1
}
pass "all installed desktop completion gates require the AT-SPI application tree"
require_pattern "$IMAGE_DIR/smoke-boot-image.sh" 'ECHO_CRASH_COLLECTION_READY provider=systemd-coredump storage=encrypted-var max-use=1G keep-free=2G' "every installed desktop smoke observes bounded encrypted crash storage"
CRASH_COLLECTION_GATE_COUNT="$(grep -c '"\$CRASH_COLLECTION_READY" -eq 1' "$IMAGE_DIR/smoke-boot-image.sh")"
[[ "$CRASH_COLLECTION_GATE_COUNT" -eq 4 ]] || {
  echo "all four installed desktop completion gates must require bounded crash collection" >&2
  exit 1
}
pass "all installed desktop completion gates require bounded encrypted crash collection"
require_pattern "$IMAGE_DIR/smoke-boot-image.sh" 'ECHO_FIREWALL_READY backend=nftables default-zone=echo-public inbound=deny forward=explicit' "every normal raw boot observes the closed host firewall"
FIREWALL_GATE_COUNT="$(grep -c '"\$FIREWALL_READY" -eq 1' "$IMAGE_DIR/smoke-boot-image.sh")"
[[ "$FIREWALL_GATE_COUNT" -eq 5 ]] || {
  echo "greeter and all four installed desktop completion gates must require host firewall health" >&2
  exit 1
}
pass "greeter and all installed desktop completion gates require nftables/firewalld health"
require_pattern "$IMAGE_DIR/smoke-boot-image.sh" 'ECHO_REMOVABLE_STORAGE_READY provider=udisks2 policy=polkit mount=on-demand filesystems=vfat,exfat,ntfs,ext4,btrfs,xfs portable=mtp' "every normal raw boot observes the removable-storage stack"
REMOVABLE_STORAGE_GATE_COUNT="$(grep -c '"\$REMOVABLE_STORAGE_READY" -eq 1' "$IMAGE_DIR/smoke-boot-image.sh")"
[[ "$REMOVABLE_STORAGE_GATE_COUNT" -eq 5 ]] || {
  echo "greeter and all four installed desktop completion gates must require removable-storage health" >&2
  exit 1
}
pass "greeter and all installed desktop completion gates require UDisks2 removable-storage health"
require_pattern "$IMAGE_DIR/smoke-boot-image.sh" 'ECHO_PRINTING_READY provider=cups transport=local-only auth=polkit driverless=ipp-usb retention=off storage=encrypted-var' "every normal raw boot observes the private local printing stack"
PRINTING_GATE_COUNT="$(grep -c '"\$PRINTING_READY" -eq 1' "$IMAGE_DIR/smoke-boot-image.sh")"
[[ "$PRINTING_GATE_COUNT" -eq 5 ]] || {
  echo "greeter and all four installed desktop completion gates must require printing health" >&2
  exit 1
}
pass "greeter and all installed desktop completion gates require private local printing health"
require_pattern "$IMAGE_DIR/smoke-boot-image.sh" 'ECHO_SCANNING_READY provider=sane frontend=skanpage usb=udev,ipp-usb network=airscan-on-demand sharing=off retention=user-owned' "every normal raw boot observes the native scanning stack"
SCANNING_GATE_COUNT="$(grep -c '"\$SCANNING_READY" -eq 1' "$IMAGE_DIR/smoke-boot-image.sh")"
[[ "$SCANNING_GATE_COUNT" -eq 5 ]] || {
  echo "greeter and all four installed desktop completion gates must require scanning health" >&2
  exit 1
}
pass "greeter and all installed desktop completion gates require native scanning health"
require_pattern "$IMAGE_DIR/smoke-boot-image.sh" 'ECHO_CORE_APPS_READY files=dolphin terminal=konsole browser=firefox text=kate documents=okular images=gwenview archives=ark media=haruna capture=spectacle calculator=kcalc defaults=xdg' "every normal raw boot observes the offline core application set and XDG defaults"
CORE_APPS_GATE_COUNT="$(grep -c '"\$CORE_APPS_READY" -eq 1' "$IMAGE_DIR/smoke-boot-image.sh")"
[[ "$CORE_APPS_GATE_COUNT" -eq 5 ]] || {
  echo "greeter and all four installed desktop completion gates must require core-app health" >&2
  exit 1
}
pass "greeter and all installed desktop completion gates require core application health"
require_pattern "$IMAGE_DIR/smoke-boot-image.sh" 'ECHO_CORE_APPS_SESSION_READY session=x11 cases=directory,http,text,pdf,image,archive,audio,terminal,calculator transports=xdg-open,gio-launch windows=native cleanup=closed fixtures=runtime-and-loopback-only' "direct installed raw boot observes nine XDG/desktop-launched native application windows"
CORE_APPS_SESSION_GATE_COUNT="$(grep -c '"\$CORE_APPS_SESSION_READY" -eq 1' "$IMAGE_DIR/smoke-boot-image.sh")"
[[ "$CORE_APPS_SESSION_GATE_COUNT" -eq 1 ]] || {
  echo "the direct installed desktop gate must require exactly one functional core-app session result" >&2
  exit 1
}
pass "direct installed desktop gate requires real XDG core-application windows"
require_pattern "$IMAGE_DIR/smoke-boot-image.sh" 'grep -Fxq '\''ECHO_NATIVE_APP_IPC_READY app=org\.kde\.kcalc path=preload-ipc-gio result=zero-exit cleanup=closed'\''' "direct installed raw boot observes the exact packaged preload IPC result"
NATIVE_APP_IPC_GATE_COUNT="$(grep -c '"\$NATIVE_APP_IPC_READY" -eq 1' "$IMAGE_DIR/smoke-boot-image.sh")"
[[ "$NATIVE_APP_IPC_GATE_COUNT" -eq 1 ]] || {
  echo "the direct installed desktop gate must require exactly one native-app IPC result" >&2
  exit 1
}
pass "direct installed desktop gate requires the packaged preload-to-GIO application path"
require_pattern "$IMAGE_DIR/smoke-boot-image.sh" 'ECHO_KWIN_COMPOSITOR_BRIDGE_READY provider=kwin-script transport=private-socket' "desktop and login smokes require KWin compositor UUID state"
require_pattern "$IMAGE_DIR/smoke-boot-image.sh" 'ECHO_KWIN_GLASS_EFFECT_READY provider=kwin-wayland-effect region=bounded fallback=webgl' "Wayland raw boot requires the packaged native Liquid Glass effect"
require_pattern "$IMAGE_DIR/smoke-boot-image.sh" '"\$WAYLAND_KWIN_GLASS_EFFECT_READY" -eq 1' "Wayland completion is gated on native effect registration"
require_pattern "$IMAGE_DIR/smoke-login-image.sh" '^cp --reflink=auto --sparse=always' "production-login smoke modifies only a temporary image copy"
require_pattern "$IMAGE_DIR/smoke-login-image.sh" '^ECHO_BOOT_CI_SESSION=no' "production-login smoke exercises SDDM without the CI session bypass"
require_pattern "$IMAGE_DIR/smoke-login-image.sh" '^  echo\.desktop\) BOOT_TARGET=login ;;$' "raw login smoke retains the default X11 production session"
require_pattern "$IMAGE_DIR/smoke-login-image.sh" '^  echo-wayland\.desktop\) BOOT_TARGET=wayland-login ;;$' "raw login smoke can select only the packaged Wayland candidate"
require_pattern "$IMAGE_DIR/smoke-login-image.sh" '/etc/sddm\.conf\.d/99-echo-ci-autologin\.conf' "test-only SDDM autologin is injected into the temporary image"
require_pattern "$IMAGE_DIR/smoke-login-image.sh" '"\$WAYLAND_NATIVE_APP_IPC_REQUEST" /etc/echo-os/wayland-native-app-ipc' "Wayland IPC request is injected only through the disposable encrypted image copy"
require_pattern "$IMAGE_DIR/smoke-login-image.sh" '^  chmod 0444 "\$WAYLAND_NATIVE_APP_IPC_REQUEST"$' "disposable Wayland IPC request is root-owned read-only configuration"
require_pattern "$IMAGE_DIR/smoke-login-image.sh" '^  \[\[ -z "\$OUTPUT_RAW" \]\] \|\| \{$' "Wayland IPC request cannot enter a publishable provisioned image"
require_pattern "$IMAGE_DIR/smoke-login-image.sh" '/var/lib/echo-os/local-account\.shadow' "production-login smoke exercises A/B account restoration"
require_pattern "$IMAGE_DIR/smoke-boot-image.sh" 'ECHO_DESKTOP_READY provider=kwin-wayland renderer=ready lock=kscreenlocker auth=ready power=ready notifications=ready input=ready clipboard=ready accessibility=ready' "raw boot harness requires Wayland renderer, lock, authorization, power, notification, input, clipboard and accessibility readiness"
require_pattern "$IMAGE_DIR/smoke-boot-image.sh" 'grep -Fxq '\''ECHO_NATIVE_APP_IPC_READY session=wayland app=org\.kde\.kcalc path=preload-ipc-gio result=zero-exit cleanup=closed'\''' "raw Wayland boot observes the exact packaged preload IPC result"
WAYLAND_NATIVE_APP_IPC_GATE_COUNT="$(grep -c '"\$WAYLAND_NATIVE_APP_IPC_READY" -eq 1' "$IMAGE_DIR/smoke-boot-image.sh")"
[[ "$WAYLAND_NATIVE_APP_IPC_GATE_COUNT" -eq 1 ]] || {
  echo "the raw Wayland login gate must require exactly one native-app IPC result" >&2
  exit 1
}
pass "raw Wayland SDDM gate requires the packaged preload-to-KWin application path"
require_pattern "$IMAGE_DIR/smoke-boot-image.sh" 'Echo OS Wayland-candidate raw cold-boot smoke OK' "raw boot harness has a distinct Wayland completion gate"
require_pattern "$IMAGE_DIR/verify-image.sh" '^cryptsetup open --readonly --type luks2 --key-file' "artifact verifier opens encrypted var read-only"
require_pattern "$IMAGE_DIR/verify-image.sh" '^mount -o ro,noload "/dev/mapper/\$VAR_MAPPING_NAME"' "artifact verifier inspects encrypted var without journal replay"
require_pattern "$IMAGE_DIR/verify-image.sh" 'etc-overlay/upper/echo-os/wayland-native-app-ipc' "artifact verifier excludes the disposable request from encrypted /etc state"
require_pattern "$IMAGE_DIR/smoke-boot-image.sh" '^    -chardev "socket,id=chrtpm,path=' "boot smoke attaches the persistent external swtpm identity"
require_pattern "$IMAGE_DIR/smoke-boot-image.sh" '^  TPM_MKOSI_ARGS=\(--tpm=no\)$' "persistent TPM boot disables mkosi's disposable TPM"
require_pattern "$IMAGE_DIR/smoke-boot-image.sh" '^    vm \\' "raw QEMU device arguments follow the mkosi vm verb"
require_pattern "$IMAGE_DIR/smoke-ab-update.sh" '\.pcrpkey:text@\$UPDATED_PCR_PUBLIC_KEY' "A/B smoke extracts the updated UKI PCR public key"
require_pattern "$IMAGE_DIR/smoke-ab-update.sh" 'verify_uki_pcr_policy\.py' "A/B smoke validates the updated signed-PCR11 policy"
require_pattern "$IMAGE_DIR/smoke-ab-update.sh" '^cmp "\$ECHO_TPM2_PCR_PUBLIC_KEY" "\$UPDATED_PCR_PUBLIC_KEY"$' "A/B smoke binds the update UKI to the release PCR identity"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'ECHO_LOGIN_SESSION: echo-wayland\.desktop' "image CI boots the candidate from a disposable SDDM copy"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" '^  source-contract:$' "pull requests retain a portable image source-contract job"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" '^          python3 packaging/image/test_verify_os_image_evidence\.py$' "pull requests exercise the signed-evidence binder"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" '^      github\.event_name != '\''pull_request'\'' &&$' "privileged whole-image execution excludes untrusted pull requests"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" '^      \(github\.ref == '\''refs/heads/os-main'\'' \|\| github\.ref == '\''refs/heads/main'\''\)$' "privileged whole-image execution accepts only trusted delivery branches"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" '^    runs-on: \[self-hosted, linux, x64, echo-os-image\]$' "trusted image runs require the dedicated self-hosted image runner"
forbid_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'vars\.ECHO_OS_IMAGE_RUNNER' "trusted image runs cannot fall back to a hosted runner"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'test_verify_linux_image_runner_host\.py' "image pull requests test the runner host contract"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" '^    timeout-minutes: 360$' "full install and cold-boot acceptance has a six-hour ceiling"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" '^      TMPDIR: /__w/_temp$' "bulk image temporaries stay on the measured container scratch filesystem"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'verify-linux-image-runner\.py' "image CI fails before signing/building on an undersized runner"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'echo-image-runner-preflight\.log' "image CI retains runner-preflight evidence"
require_pattern "$IMAGE_DIR/verify-linux-image-runner.py" '^WORKSPACE_REQUIRED_BYTES = 48 \* GIB$' "runner reserves workspace capacity for raw, UKI and installer artifacts"
require_pattern "$IMAGE_DIR/verify-linux-image-runner.py" '^SCRATCH_REQUIRED_BYTES = 160 \* GIB$' "runner reserves scratch capacity for concurrent whole-disk lifecycle branches"
require_pattern "$IMAGE_DIR/verify-linux-image-runner.py" 'aggregate\["required"\] \+= item\.required_bytes' "runner sums capacity when workspace and scratch share a filesystem"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" '^        run: rm -- "\$RUNNER_TEMP/echo-os-replacement-tpm\.raw"$' "image CI releases the verified replacement-TPM disk branch"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" '^        run: rm -- "\$RUNNER_TEMP/echo-os-factory-reset\.raw"$' "image CI releases the verified factory-reset disk branch"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" '^        run: rm -- "\$RUNNER_TEMP/echo-os-provisioned\.raw"$' "image CI releases the verified provisioned disk branch"
require_pattern "$IMAGE_DIR/verify-linux-image-runner.py" '^def _kvm_device_ready\(\) -> bool:$' "runner opens the real KVM character device"
require_pattern "$IMAGE_DIR/verify-linux-image-runner.py" '"secure-boot" not in features' "runner requires an x86-64 Secure-Boot firmware descriptor"
require_pattern "$IMAGE_DIR/verify-linux-image-runner.py" '^MIN_FREE_LOOP_DEVICES = 4$' "runner reserves loop devices for image inspection"
require_pattern "$IMAGE_DIR/verify-linux-image-runner.py" '^MIN_FREE_NBD_DEVICES = 2$' "runner reserves NBD devices for whole-disk installation and restore"
require_pattern "$IMAGE_DIR/verify-linux-image-runner.py" '^SCHEMA = 2$' "runner preflight evidence records its bounded path contract"
require_pattern "$IMAGE_DIR/verify-linux-image-runner.py" '^CONTAINER_WORK_ROOT = Path\("/__w"\)$' "in-job runner preflight is bound to the official container work mount"
require_pattern "$IMAGE_DIR/verify-linux-image-runner.py" '^EXPECTED_WORKSPACE = CONTAINER_WORK_ROOT / "echo-os" / "echo-os"$' "in-job runner preflight requires the translated checkout layout"
require_pattern "$IMAGE_DIR/verify-linux-image-runner.py" '^EXPECTED_SCRATCH = CONTAINER_WORK_ROOT / "_temp"$' "in-job runner preflight requires the translated temporary directory"
require_pattern "$IMAGE_DIR/verify-linux-image-runner-host.py" '^MIN_WORK_ROOT_FREE_BYTES = 208 \* GIB$' "runner host reserves the combined workspace and scratch capacity"
require_pattern "$IMAGE_DIR/verify-linux-image-runner-host.py" '^MIN_LOOP_DEVICES = 64$' "runner host loads enough loop devices before registration"
require_pattern "$IMAGE_DIR/verify-linux-image-runner-host.py" '^MIN_NBD_DEVICES = 16$' "runner host loads enough NBD devices before registration"
require_pattern "$IMAGE_DIR/verify-linux-image-runner-host.py" '^RUNNER_WORK_ROOT = "/srv/echo-os-image-runner"$' "runner host evidence is bound to the dedicated work root"
require_pattern "$IMAGE_DIR/verify-linux-image-runner-host.py" 'docker", "version", "--format"' "runner service user must reach the Docker server"
require_pattern "$IMAGE_DIR/verify-linux-image-runner-host.py" 'docker", "context", "show"' "runner host requires the default Docker context"
require_pattern "$IMAGE_DIR/verify-linux-image-runner-host.py" '"DOCKER_HOST", "DOCKER_CONTEXT"' "runner host rejects Docker daemon environment overrides"
require_pattern "$IMAGE_DIR/verify-linux-image-runner-host.py" 'stat\.S_ISSOCK\(metadata\.st_mode\)' "runner host requires the local Docker Unix socket"
require_pattern "$IMAGE_DIR/verify-linux-image-runner-host.py" 'option == "name=rootless"' "runner host rejects rootless Docker for privileged device jobs"
require_pattern "$IMAGE_DIR/verify-linux-image-runner-host.py" '^        "ECHO_IMAGE_RUNNER_HOST_READY arch=x86_64 "$' "runner host emits one bounded readiness marker"
require_pattern "$IMAGE_DIR/configure-linux-image-runner-host.sh" '^\[\[ "\$WORK_ROOT" == /srv/echo-os-image-runner \]\] \|\| \{$' "runner host configuration is limited to its dedicated work root"
require_pattern "$IMAGE_DIR/configure-linux-image-runner-host.sh" 'find "\$WORK_ROOT" -mindepth 1 -maxdepth 1 -print -quit' "runner host refuses to repurpose a populated directory"
require_pattern "$IMAGE_DIR/configure-linux-image-runner-host.sh" '^usermod -aG docker,kvm "\$RUNNER_USER"$' "runner host grants only the required container and virtualization groups"
require_pattern "$IMAGE_DIR/configure-linux-image-runner-host.sh" '^modprobe loop max_loop=64$' "runner host activates the loop capacity immediately"
require_pattern "$IMAGE_DIR/configure-linux-image-runner-host.sh" '^modprobe nbd nbds_max=16 max_part=16$' "runner host activates the NBD capacity immediately"
forbid_pattern "$IMAGE_DIR/configure-linux-image-runner-host.sh" 'RUNNER_TOKEN|--token|curl |wget |config\.sh' "runner host configuration has no registration credential or runner-download path"
require_pattern "$IMAGE_DIR/configure-linux-image-runner-host.sh" 'echo-os-image-runner-cleanup\.py' "runner host installs a root-owned cleanup implementation"
require_pattern "$IMAGE_DIR/configure-linux-image-runner-host.sh" 'echo-os-image-runner-job-hook\.sh' "runner host installs the pre/post job hook outside the runner application"
require_pattern "$IMAGE_DIR/configure-linux-image-runner-host.sh" 'echo-os-image-runner-registration\.py' "runner host installs the registration verifier outside the runner application"
require_pattern "$IMAGE_DIR/configure-linux-image-runner-hooks.sh" '^\[\[ "\$RUNNER_APPLICATION_DIR" == /opt/actions-runner \]\] \|\| \{$' "runner hook configuration is limited to the dedicated application directory"
require_pattern "$IMAGE_DIR/configure-linux-image-runner-hooks.sh" '^for runner_file in \.runner \.credentials \.credentials_rsaparams \.service config\.sh run\.sh; do$' "runner hooks require one complete official registration and installed service"
require_pattern "$IMAGE_DIR/configure-linux-image-runner-hooks.sh" '^  chmod 0600 "\$path"$' "runner registration and credential files are private before service start"
require_pattern "$IMAGE_DIR/configure-linux-image-runner-hooks.sh" 'echo-os-image-runner-registration\.py' "hook configuration verifies repository, work root and systemd service registration"
require_pattern "$IMAGE_DIR/configure-linux-image-runner-hooks.sh" '^  "ACTIONS_RUNNER_HOOK_JOB_STARTED=\$HOOK" \\$' "registered runner enables the pre-job cleanup hook"
require_pattern "$IMAGE_DIR/configure-linux-image-runner-hooks.sh" '^  "ACTIONS_RUNNER_HOOK_JOB_COMPLETED=\$HOOK" \\$' "registered runner enables the post-job cleanup hook"
require_pattern "$IMAGE_DIR/configure-linux-image-runner-hooks.sh" '^chmod 0600 "\$TEMP_ENV"$' "runner hook environment remains private"
require_pattern "$IMAGE_DIR/runner-host/echo-os-image-runner-job-hook.sh" '^HOST_WORK_ROOT=/srv/echo-os-image-runner$' "runner hook is bound to the dedicated host work root"
require_pattern "$IMAGE_DIR/runner-host/echo-os-image-runner-job-hook.sh" '^EXPECTED_WORKSPACE="\$HOST_WORK_ROOT/echo-os/echo-os"$' "runner hook requires the official host checkout layout"
require_pattern "$IMAGE_DIR/runner-host/echo-os-image-runner-job-hook.sh" '^EXPECTED_SCRATCH="\$HOST_WORK_ROOT/_temp"$' "runner hook requires the official host scratch layout"
require_pattern "$IMAGE_DIR/runner-host/echo-os-image-runner-job-hook.sh" '^exec /usr/bin/timeout --foreground --signal=TERM 300s \\$' "runner hooks have an explicit five-minute ceiling"
forbid_pattern "$IMAGE_DIR/configure-linux-image-runner-hooks.sh" 'RUNNER_TOKEN|--token|curl |wget ' "runner hook configuration has no registration credential or download path"
forbid_pattern "$IMAGE_DIR/runner-host/echo-os-image-runner-job-hook.sh" 'RUNNER_TOKEN|--token|curl |wget |rm -rf' "runner hook delegates only to the bounded cleanup implementation"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" 'cleanup-linux-image-runner\.py' "image CI always invokes bounded runner cleanup"
require_pattern "$REPO_ROOT/.github/workflows/ab-update-smoke.yml" 'cleanup-linux-image-runner\.py' "A/B CI always invokes bounded runner cleanup"
require_pattern "$REPO_ROOT/.github/workflows/os-image.yml" '^      - name: Remove generated bundle and whole-disk temporaries$' "image CI cleanup remains an explicit final step"
require_pattern "$REPO_ROOT/.github/workflows/ab-update-smoke.yml" '^      - name: Remove generated bundle and whole-disk temporaries$' "A/B CI cleanup remains an explicit final step"
require_pattern "$IMAGE_DIR/cleanup-linux-image-runner.py" '^SCRATCH_PREFIX = "echo-"$' "runner cleanup is limited to the Echo scratch namespace"
require_pattern "$IMAGE_DIR/cleanup-linux-image-runner.py" '^RUNNER_WORK_ROOTS = \($' "runner cleanup publishes an explicit host/container root contract"
require_pattern "$IMAGE_DIR/cleanup-linux-image-runner.py" '^    Path\("/srv/echo-os-image-runner"\),$' "runner hook cleanup is rooted in the dedicated host workspace"
require_pattern "$IMAGE_DIR/cleanup-linux-image-runner.py" '^    Path\("/__w"\),$' "in-container cleanup is rooted in the official work mount"
require_pattern "$IMAGE_DIR/cleanup-linux-image-runner.py" '^    expected_workspace = work_root / REPOSITORY_NAME / REPOSITORY_NAME$' "runner cleanup accepts only the primary OS checkout layout"
require_pattern "$IMAGE_DIR/cleanup-linux-image-runner.py" '^    expected_scratch = work_root / RUNNER_SCRATCH_NAME$' "runner cleanup accepts only the runner-owned scratch directory"
require_pattern "$IMAGE_DIR/cleanup-linux-image-runner.py" '^    if os\.environ\.get\("CI"\) != "true" or os\.environ\.get\("GITHUB_ACTIONS"\) != "true":$' "runner cleanup requires GitHub Actions identity"
forbid_pattern "$IMAGE_DIR/cleanup-linux-image-runner.py" 'shell=True|os\.system|subprocess|Path\("/"\).*rmtree' "runner cleanup has no shell or broad root deletion path"
require_pattern "$IMAGE_DIR/runner-host/echo-os-image-runner.modules.conf" '^loop$' "runner host loads the loop module at boot"
require_pattern "$IMAGE_DIR/runner-host/echo-os-image-runner.modules.conf" '^nbd$' "runner host loads the NBD module at boot"
require_pattern "$IMAGE_DIR/runner-host/echo-os-image-runner.modprobe.conf" '^options loop max_loop=64$' "runner host persists loop capacity across reboot"
require_pattern "$IMAGE_DIR/runner-host/echo-os-image-runner.modprobe.conf" '^options nbd nbds_max=16 max_part=16$' "runner host persists NBD capacity across reboot"
require_pattern "$IMAGE_DIR/verify-linux-image-runner-registration.py" '^EXPECTED_REPOSITORY = "dengdenghua/echo-os"$' "registered image runner is scoped to the reviewed GitHub repository"
require_pattern "$IMAGE_DIR/verify-linux-image-runner-registration.py" '^RUNNER_WORK_ROOT = Path\("/srv/echo-os-image-runner"\)$' "registered image runner retains the verified host work root"
require_pattern "$IMAGE_DIR/verify-linux-image-runner-registration.py" 'settings\.get\("WorkFolder"\) != str\(work_root\)' "registration fails closed on an incorrect official runner work folder"
require_pattern "$IMAGE_DIR/verify-linux-image-runner-registration.py" 'ECHO_IMAGE_RUNNER_REGISTRATION_READY' "registration emits one bounded local readiness marker"
require_pattern "$IMAGE_DIR/build-image.sh" 'install-recovery-uki\.sh' "main image build installs the independent recovery UKI"
require_pattern "$IMAGE_DIR/smoke-recovery-image.sh" "'default echo-recovery_\*'" "installed-Recovery smoke selects the ESP entry only on a test copy"
require_pattern "$IMAGE_DIR/smoke-recovery-image.sh" '^ECHO_BOOT_TARGET=recovery' "installed-Recovery smoke boots through the disk-image harness"
require_pattern "$REPO_ROOT/packaging/recovery/install-recovery-uki.sh" 'ESP_FREE_BYTES' "recovery injection checks ESP capacity before copying"
require_pattern "$IMAGE_DIR/secure-boot-options.sh" '^    --secure-boot=yes$' "external keys can enable mkosi Secure Boot signing"
require_pattern "$IMAGE_DIR/secure-boot-options.sh" '^    --verity=yes$' "release builds require dm-verity generation"
require_pattern "$IMAGE_DIR/secure-boot-options.sh" '^    --verity-key="\$key_path"$' "verity root hashes use the Secure Boot release identity"
require_pattern "$IMAGE_DIR/secure-boot-options.sh" '^    --sign-expected-pcr=yes$' "every release UKI receives an expected-PCR signature"
require_pattern "$IMAGE_DIR/secure-boot-options.sh" '^    --sign-expected-pcr-key=' "PCR signatures use an explicit separate private key"
require_pattern "$IMAGE_DIR/secure-boot-options.sh" 'TPM2 PCR public key does not match the PCR policy private key' "build rejects a mismatched TPM2 policy identity"
require_pattern "$IMAGE_DIR/secure-boot-options.sh" '^    --firmware=uefi-secure-boot$' "signed VM tests request enforcing UEFI firmware"
forbid_pattern "$IMAGE_DIR/secure-boot-options.sh" 'BEGIN (RSA |EC )?PRIVATE KEY' "Secure Boot helper contains no private key material"
require_file "$IMAGE_DIR/mkosi.extra/etc/kernel/tries"
require_pattern "$IMAGE_DIR/mkosi.extra/etc/kernel/tries" '^3$' "initial and updated UKIs get three boot attempts"

if [[ "$FAILURES" -ne 0 ]]; then exit 1; fi
if [[ "$MODE" == "--static" ]]; then
  echo "Image source contract OK"
  exit 0
fi
if [[ "$MODE" != "--artifact" || $# -ne 2 ]]; then
  echo "usage: $0 [--static|--artifact IMAGE.raw]" >&2
  exit 2
fi

IMAGE_PATH="$2"
[[ "$(uname -s)" == "Linux" ]] || {
  echo "artifact verification requires Linux systemd-dissect" >&2
  exit 1
}
[[ -f "$IMAGE_PATH" ]] || { echo "image not found: $IMAGE_PATH" >&2; exit 1; }
for command_name in \
  cmp cryptsetup find losetup lsblk lsinitramfs mcopy modprobe mount openssl python3 realpath sfdisk sha256sum stat umount \
  systemd-analyze systemd-dissect udevadm veritysetup; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "artifact verifier dependency missing: $command_name" >&2
    exit 1
  }
done
[[ -n "${ECHO_SECURE_BOOT_CERTIFICATE:-}" ]] || {
  echo "ECHO_SECURE_BOOT_CERTIFICATE is required for verity artifact verification" >&2
  exit 1
}
[[ -n "${ECHO_OS_SOURCE_MANIFEST:-}" && \
   -f "$ECHO_OS_SOURCE_MANIFEST" && ! -L "$ECHO_OS_SOURCE_MANIFEST" ]] || {
  echo "ECHO_OS_SOURCE_MANIFEST is required for source-bound artifact verification" >&2
  exit 1
}
python3 "$IMAGE_DIR/os_source_identity.py" verify \
  --manifest "$ECHO_OS_SOURCE_MANIFEST"
ECHO_OS_SOURCE_MANIFEST="$(realpath "$ECHO_OS_SOURCE_MANIFEST")"
command -v sbverify >/dev/null 2>&1 || {
  echo "signed artifact verifier dependency missing: sbverify" >&2
  exit 1
}
ECHO_SECURE_BOOT_CERTIFICATE="$(realpath "$ECHO_SECURE_BOOT_CERTIFICATE")"
[[ -f "$ECHO_SECURE_BOOT_CERTIFICATE" && ! -L "$ECHO_SECURE_BOOT_CERTIFICATE" ]] || {
  echo "Secure Boot certificate not found: $ECHO_SECURE_BOOT_CERTIFICATE" >&2
  exit 1
}

echo "Echo OS image artifact contract"
systemd-dissect --validate "$IMAGE_PATH" >/dev/null
pass "systemd-dissect accepts the Discoverable Disk Image"

VERIFY_TEMP_DIR="$(mktemp -d)"
PARTITION_JSON="$VERIFY_TEMP_DIR/partitions.json"
OS_RELEASE_COPY="$VERIFY_TEMP_DIR/os-release"
OS_SOURCE_MANIFEST_COPY="$VERIFY_TEMP_DIR/os-source-identity.json"
SHADOW_COPY="$VERIFY_TEMP_DIR/shadow"
SDDM_CONFIG_COPY="$VERIFY_TEMP_DIR/sddm.conf"
SESSION_COPY="$VERIFY_TEMP_DIR/echo.desktop"
WAYLAND_SESSION_COPY="$VERIFY_TEMP_DIR/echo-wayland.desktop"
KSCREENLOCKER_CONFIG_COPY="$VERIFY_TEMP_DIR/kscreenlockerrc"
MACHINE_ID_TEMPLATE_COPY="$VERIFY_TEMP_DIR/machine-id-template"
APP_STORE_COPY="$VERIFY_TEMP_DIR/echo-app-store.desktop"
DISCOVER_MASK_COPY="$VERIFY_TEMP_DIR/org.kde.discover.desktop"
PORTAL_CONFIG_COPY="$VERIFY_TEMP_DIR/echo-portals.conf"
FLATHUB_DEFINITION_COPY="$VERIFY_TEMP_DIR/flathub.flatpakrepo"
NETWORKMANAGER_CONFIG_COPY="$VERIFY_TEMP_DIR/20-echo-persistent-connections.conf"
LOCALE_GEN_COPY="$VERIFY_TEMP_DIR/locale.gen"
LOCK_PAM_COPY="$VERIFY_TEMP_DIR/echo-lock.pam"
PCR_POLICY_PUBLIC_KEY_COPY="$VERIFY_TEMP_DIR/tpm2-pcr-public-key.pem"
VERITY_CERTIFICATE_COPY="$VERIFY_TEMP_DIR/verity-certificate.pem"
UNEXPECTED_OEM_MARKER="$VERIFY_TEMP_DIR/oem-complete.json"
UNEXPECTED_SHADOW_STATE="$VERIFY_TEMP_DIR/local-account.shadow"
UNEXPECTED_APP_MARKER="$VERIFY_TEMP_DIR/app-catalog-provisioned"
UNEXPECTED_MACHINE_ID="$VERIFY_TEMP_DIR/persistent-machine-id"
UNEXPECTED_NETWORK_MARKER="$VERIFY_TEMP_DIR/network-state-v1"
UNEXPECTED_REGION_STATE="$VERIFY_TEMP_DIR/region-state.json"
IMAGE_MOUNT=""
LOOP_DEVICE=""
VAR_MOUNT=""
VAR_MAPPING_NAME=""
VAR_MOUNTED=0
IMAGE_VERSION="${ECHO_IMAGE_VERSION:-$(tr -d '[:space:]' <"$IMAGE_DIR/mkosi.version")}"
resolve_partition_artifact() {
  local kind="$1"
  local -a matches=()
  while IFS= read -r -d '' artifact; do
    matches+=("$artifact")
  done < <(find "$(dirname "$IMAGE_PATH")" -maxdepth 1 -type f \
    -name "echo-os_${IMAGE_VERSION}.${kind}.*.raw" -print0)
  [[ "${#matches[@]}" -eq 1 ]] || {
    echo "expected exactly one UUID-bearing $kind split artifact" >&2
    return 1
  }
  printf '%s\n' "${matches[0]}"
}
ROOT_PAYLOAD="$(resolve_partition_artifact root)"
VERITY_PAYLOAD="$(resolve_partition_artifact root-verity)"
VERITY_SIG_PAYLOAD="$(resolve_partition_artifact root-verity-sig)"
UKI_PAYLOAD="$(dirname "$IMAGE_PATH")/echo-os_${IMAGE_VERSION}.efi"
[[ -s "$ROOT_PAYLOAD" && -s "$VERITY_PAYLOAD" && \
   -s "$VERITY_SIG_PAYLOAD" && -s "$UKI_PAYLOAD" ]] || {
  echo "split root/verity/signature/UKI artifact set is incomplete" >&2
  exit 1
}
[[ -n "${ECHO_FACTORY_DATA_KEY:-}" ]] || {
  echo "ECHO_FACTORY_DATA_KEY is required to verify encrypted image data" >&2
  exit 1
}
FACTORY_DATA_KEY="$(realpath "$ECHO_FACTORY_DATA_KEY")"
python3 "$REPO_ROOT/deploy/data-protection/echo_data_protection.py" \
  check-factory-key "$FACTORY_DATA_KEY"
[[ -n "${ECHO_TPM2_PCR_PUBLIC_KEY:-}" ]] || {
  echo "ECHO_TPM2_PCR_PUBLIC_KEY is required to verify signed-PCR artifacts" >&2
  exit 1
}
PCR_POLICY_PUBLIC_KEY="$(realpath "$ECHO_TPM2_PCR_PUBLIC_KEY")"
python3 "$REPO_ROOT/deploy/data-protection/echo_data_protection.py" \
  check-tpm2-public-key "$PCR_POLICY_PUBLIC_KEY"
cleanup() {
  if [[ -n "$IMAGE_MOUNT" ]]; then
    systemd-dissect --umount "$IMAGE_MOUNT" >/dev/null 2>&1 || true
  fi
  if [[ "$VAR_MOUNTED" -eq 1 ]]; then
    umount "$VAR_MOUNT" >/dev/null 2>&1 || true
  fi
  if [[ -n "$VAR_MAPPING_NAME" ]]; then
    cryptsetup close "$VAR_MAPPING_NAME" >/dev/null 2>&1 || true
  fi
  if [[ -n "$LOOP_DEVICE" ]]; then
    losetup --detach "$LOOP_DEVICE" >/dev/null 2>&1 || true
  fi
  rm -rf -- "$VERIFY_TEMP_DIR"
}
trap cleanup EXIT INT TERM
sfdisk --json "$IMAGE_PATH" >"$PARTITION_JSON"
python3 - "$PARTITION_JSON" "$IMAGE_VERSION" \
  "$ROOT_PAYLOAD" "$VERITY_PAYLOAD" "$VERITY_SIG_PAYLOAD" <<'PY'
import json
import re
import sys

version = sys.argv[2]
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
with open(sys.argv[1], encoding="utf-8") as stream:
    table = json.load(stream)["partitiontable"]
partitions = table.get("partitions", [])
actual = [(item.get("name"), str(item.get("type", "")).lower()) for item in partitions]
if actual != expected:
    raise SystemExit(f"partition contract mismatch: expected={expected}, actual={actual}")
by_label = {item.get("name"): item for item in partitions if item.get("name") != "_empty"}
for label, artifact in [
    (f"echo-root-{version}", sys.argv[3]),
    (f"echo-root-{version}-verity", sys.argv[4]),
    (f"echo-root-{version}-verity-sig", sys.argv[5]),
]:
    match = re.search(r"\.([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})\.raw$", artifact)
    if not match or str(by_label[label].get("uuid", "")).lower() != match.group(1):
        raise SystemExit(f"split artifact UUID does not match GPT member: {label}")
print("  ✓ exact ten-partition GPT layout and split-artifact UUIDs match")
PY

python3 "$REPO_ROOT/deploy/update/verify-verity-set.py" \
  "$ROOT_PAYLOAD" "$VERITY_PAYLOAD" "$VERITY_SIG_PAYLOAD" \
  "$ECHO_SECURE_BOOT_CERTIFICATE" --uki "$UKI_PAYLOAD"
pass "release certificate, UKI roothash, GPT UUIDs and dm-verity tree agree"

modprobe loop
LOOP_DEVICE="$(losetup --find --show --partscan --read-only "$IMAGE_PATH")"
udevadm settle --timeout=30
VAR_PARTITION=""
for protected_label in echo-var echo-swap echo-home; do
  mapfile -t protected_devices < <(
    lsblk -nrpo PATH,PARTLABEL "$LOOP_DEVICE" |
      awk -v wanted="$protected_label" '$2 == wanted { print $1 }'
  )
  [[ "${#protected_devices[@]}" -eq 1 ]] || {
    echo "encrypted partition lookup is ambiguous: $protected_label" >&2
    exit 1
  }
  protected_device="${protected_devices[0]}"
  [[ "$protected_label" != echo-var ]] || VAR_PARTITION="$protected_device"
  cryptsetup isLuks --type luks2 "$protected_device" || {
    echo "$protected_label is not a LUKS2 volume" >&2
    exit 1
  }
  cryptsetup open --test-passphrase --key-file "$FACTORY_DATA_KEY" \
    "$protected_device" || {
      echo "factory data key does not unlock $protected_label" >&2
      exit 1
    }
done
pass "var, swap and home are LUKS2 volumes unlocked by only the install-time factory key"

[[ -n "$VAR_PARTITION" ]] || {
  echo "encrypted var partition was not resolved" >&2
  exit 1
}
VAR_MAPPING_NAME="echo-artifact-var-${LOOP_DEVICE##*/}-$$"
cryptsetup open --readonly --type luks2 --key-file "$FACTORY_DATA_KEY" \
  "$VAR_PARTITION" "$VAR_MAPPING_NAME"
VAR_MOUNT="$VERIFY_TEMP_DIR/encrypted-var"
mkdir -m 0700 "$VAR_MOUNT"
mount -o ro,noload "/dev/mapper/$VAR_MAPPING_NAME" "$VAR_MOUNT"
VAR_MOUNTED=1
UNEXPECTED_WAYLAND_IPC_REQUEST="$VAR_MOUNT/lib/echo-os/etc-overlay/upper/echo-os/wayland-native-app-ipc"
[[ ! -e "$UNEXPECTED_WAYLAND_IPC_REQUEST" && \
   ! -L "$UNEXPECTED_WAYLAND_IPC_REQUEST" ]] || {
  echo "artifact encrypted /etc overlay contains a disposable Wayland IPC request" >&2
  exit 1
}
umount "$VAR_MOUNT"
VAR_MOUNTED=0
cryptsetup close "$VAR_MAPPING_NAME"
VAR_MAPPING_NAME=""
pass "encrypted artifact overlay excludes the disposable Wayland IPC request"

systemd-dissect --read-only --fsck=no --copy-from \
  "$ROOT_PAYLOAD" /usr/lib/os-release "$OS_RELEASE_COPY"
grep -q '^ID=echo-os$' "$OS_RELEASE_COPY"
grep -q "^IMAGE_VERSION=$IMAGE_VERSION$" "$OS_RELEASE_COPY"
pass "mounted artifact reports Echo OS product identity"

systemd-dissect --read-only --fsck=no --copy-from \
  "$ROOT_PAYLOAD" /usr/lib/echo-os/os-source-identity.json \
  "$OS_SOURCE_MANIFEST_COPY"
cmp "$ECHO_OS_SOURCE_MANIFEST" "$OS_SOURCE_MANIFEST_COPY"
python3 "$IMAGE_DIR/os_source_identity.py" verify \
  --manifest "$OS_SOURCE_MANIFEST_COPY"
pass "immutable root contains the exact clean OS source identity selected by the build"

systemd-dissect --read-only --fsck=no --copy-from "$ROOT_PAYLOAD" /etc/shadow "$SHADOW_COPY"
systemd-dissect --read-only --fsck=no --copy-from \
  "$ROOT_PAYLOAD" /etc/sddm.conf.d/10-echo-os.conf "$SDDM_CONFIG_COPY"
systemd-dissect --read-only --fsck=no --copy-from \
  "$ROOT_PAYLOAD" /usr/share/xsessions/echo.desktop "$SESSION_COPY"
systemd-dissect --read-only --fsck=no --copy-from "$ROOT_PAYLOAD" \
  /usr/share/wayland-sessions/echo-wayland.desktop "$WAYLAND_SESSION_COPY"
systemd-dissect --read-only --fsck=no --copy-from "$ROOT_PAYLOAD" \
  /etc/xdg/kscreenlockerrc "$KSCREENLOCKER_CONFIG_COPY"
systemd-dissect --read-only --fsck=no --copy-from \
  "$ROOT_PAYLOAD" /etc/machine-id "$MACHINE_ID_TEMPLATE_COPY"
systemd-dissect --read-only --fsck=no --copy-from "$ROOT_PAYLOAD" \
  /usr/local/share/applications/echo-app-store.desktop "$APP_STORE_COPY"
systemd-dissect --read-only --fsck=no --copy-from "$ROOT_PAYLOAD" \
  /usr/local/share/applications/org.kde.discover.desktop "$DISCOVER_MASK_COPY"
systemd-dissect --read-only --fsck=no --copy-from "$ROOT_PAYLOAD" \
  /usr/share/xdg-desktop-portal/echo-portals.conf "$PORTAL_CONFIG_COPY"
systemd-dissect --read-only --fsck=no --copy-from "$ROOT_PAYLOAD" \
  /usr/share/echo-os/remotes/flathub.flatpakrepo "$FLATHUB_DEFINITION_COPY"
systemd-dissect --read-only --fsck=no --copy-from "$ROOT_PAYLOAD" \
  /etc/NetworkManager/conf.d/20-echo-persistent-connections.conf \
  "$NETWORKMANAGER_CONFIG_COPY"
systemd-dissect --read-only --fsck=no --copy-from \
  "$ROOT_PAYLOAD" /etc/locale.gen "$LOCALE_GEN_COPY"
systemd-dissect --read-only --fsck=no --copy-from \
  "$ROOT_PAYLOAD" /etc/pam.d/echo-lock "$LOCK_PAM_COPY"
systemd-dissect --read-only --fsck=no --copy-from "$ROOT_PAYLOAD" \
  /usr/lib/systemd/tpm2-pcr-public-key.pem "$PCR_POLICY_PUBLIC_KEY_COPY"
systemd-dissect --read-only --fsck=no --copy-from "$ROOT_PAYLOAD" \
  /usr/lib/echo-os/verity-certificate.pem "$VERITY_CERTIFICATE_COPY"
cmp "$PCR_POLICY_PUBLIC_KEY" "$PCR_POLICY_PUBLIC_KEY_COPY"
cmp "$ECHO_SECURE_BOOT_CERTIFICATE" "$VERITY_CERTIFICATE_COPY"
grep -Eq '^echo:!.*:' "$SHADOW_COPY"
if grep -Eq '^\[Autologin\]|^User=echo$' "$SDDM_CONFIG_COPY"; then
  echo "fresh image unexpectedly enables SDDM autologin" >&2
  exit 1
fi
grep -Fxq \
  'GreeterEnvironment=QT_ACCESSIBILITY=1,ACCESSIBILITY_ENABLED=1,NO_AT_BRIDGE=0' \
  "$SDDM_CONFIG_COPY"
grep -Fxq 'DisplayCommand=/usr/lib/echo-os/echo-sddm-xsetup' "$SDDM_CONFIG_COPY"
grep -Fxq 'DisplayStopCommand=/usr/lib/echo-os/echo-sddm-xstop' "$SDDM_CONFIG_COPY"
grep -q '^Exec=/opt/echo-os/deploy/desktop-session/echo-desktop-session\.sh$' "$SESSION_COPY"
grep -q '^Name=Echo OS (Wayland Candidate)$' "$WAYLAND_SESSION_COPY"
grep -q '^Exec=/opt/echo-os/deploy/desktop-session/echo-wayland-session\.sh$' \
  "$WAYLAND_SESSION_COPY"
grep -q '^RequirePassword=true$' "$KSCREENLOCKER_CONFIG_COPY"
grep -q '^LockOnResume=true$' "$KSCREENLOCKER_CONFIG_COPY"
[[ ! -s "$MACHINE_ID_TEMPLATE_COPY" ]] || {
  echo "generic image unexpectedly contains a cloned machine-id" >&2
  exit 1
}
grep -q '^Exec=/usr/bin/plasma-discover --backends flatpak %U$' "$APP_STORE_COPY"
grep -q '^Hidden=true$' "$DISCOVER_MASK_COPY"
grep -q '^default=kde$' "$PORTAL_CONFIG_COPY"
grep -q '^\[keyfile\]$' "$NETWORKMANAGER_CONFIG_COPY"
grep -q '^path=/var/lib/NetworkManager/system-connections$' "$NETWORKMANAGER_CONFIG_COPY"
grep -q '^zh_CN.UTF-8 UTF-8$' "$LOCALE_GEN_COPY"
grep -q '^en_US.UTF-8 UTF-8$' "$LOCALE_GEN_COPY"
grep -q '^@include common-auth$' "$LOCK_PAM_COPY"
grep -q '^@include common-account$' "$LOCK_PAM_COPY"
echo '3371dd250e61d9e1633630073fefda153cd4426f72f4afa0c3373ae2e8fea03a  flathub.flatpakrepo' |
  (cd "$VERIFY_TEMP_DIR" && sha256sum --check -)
if systemd-dissect --read-only --fsck=no --copy-from "$ROOT_PAYLOAD" /var/lib/echo-os/oem-complete.json \
     "$UNEXPECTED_OEM_MARKER" >/dev/null 2>&1; then
  echo "fresh image unexpectedly contains an OEM completion marker" >&2
  exit 1
fi
if systemd-dissect --read-only --fsck=no --copy-from "$ROOT_PAYLOAD" /var/lib/echo-os/local-account.shadow \
     "$UNEXPECTED_SHADOW_STATE" >/dev/null 2>&1; then
  echo "fresh image unexpectedly contains a reusable local-account secret" >&2
  exit 1
fi
if systemd-dissect --read-only --fsck=no --copy-from "$ROOT_PAYLOAD" /var/lib/echo-os/app-catalog-provisioned \
     "$UNEXPECTED_APP_MARKER" >/dev/null 2>&1; then
  echo "fresh image unexpectedly contains a forged application-catalog marker" >&2
  exit 1
fi
if systemd-dissect --read-only --fsck=no --copy-from "$ROOT_PAYLOAD" /var/lib/echo-os/machine-id \
     "$UNEXPECTED_MACHINE_ID" >/dev/null 2>&1; then
  echo "fresh image unexpectedly contains a per-device machine-id" >&2
  exit 1
fi
if systemd-dissect --read-only --fsck=no --copy-from "$ROOT_PAYLOAD" /var/lib/echo-os/network-state-v1 \
     "$UNEXPECTED_NETWORK_MARKER" >/dev/null 2>&1; then
  echo "fresh image unexpectedly contains a forged NetworkManager migration marker" >&2
  exit 1
fi
if systemd-dissect --read-only --fsck=no --copy-from "$ROOT_PAYLOAD" /var/lib/echo-os/region-state.json \
     "$UNEXPECTED_REGION_STATE" >/dev/null 2>&1; then
  echo "fresh image unexpectedly contains one device's regional state" >&2
  exit 1
fi
pass "fresh artifact has locked identity, no autologin, pinned app/network trust, curated locales and no forged device state"
pass "artifact embeds the exact release-selected signed-PCR public key"
pass "artifact embeds the exact release-selected dm-verity certificate"

IMAGE_MOUNT="$VERIFY_TEMP_DIR/root"
mkdir -p "$IMAGE_MOUNT"
systemd-dissect --read-only --fsck=no --mount "$ROOT_PAYLOAD" "$IMAGE_MOUNT"
for session_executable in \
  usr/bin/xss-lock \
  usr/bin/xsecurelock \
  usr/bin/kwin_wayland \
  usr/bin/kwin_wayland_wrapper \
  usr/bin/Xwayland \
  usr/bin/systemsettings \
  usr/bin/fcitx5 \
  usr/bin/fcitx5-remote \
  usr/bin/fcitx5-config-qt \
  usr/bin/xclip \
  usr/bin/wl-copy \
  usr/bin/wl-paste \
  usr/bin/orca \
  usr/bin/spd-say \
  usr/bin/espeak-ng \
  usr/bin/restic \
  usr/bin/findmnt \
  usr/bin/echo-os-backup \
  usr/libexec/at-spi-bus-launcher \
  usr/libexec/at-spi2-registryd \
  usr/lib/x86_64-linux-gnu/libexec/org_kde_powerdevil \
  opt/echo-os/deploy/desktop-session/echo-wayland-session.sh \
  opt/echo-os/deploy/desktop-session/echo-wayland-shell-session.sh \
  opt/echo-agent/codex/bin/codex \
  usr/lib/echo-os/echo-session-lock \
  usr/lib/echo-os/echo-screen-locker \
  usr/lib/echo-os/echo-kwin-window-bridge \
  usr/lib/echo-os/echo-notification-service \
  usr/lib/echo-os/echo-clipboard-host \
  usr/lib/echo-os/echo-accessibility-smoke.py \
  usr/lib/echo-os/verify-wayland-native-app-ipc.py \
  usr/lib/echo-os/echo-sddm-accessibility \
  usr/lib/echo-os/echo-sddm-xsetup \
  usr/lib/echo-os/echo-sddm-xstop \
  usr/lib/systemd/systemd-coredump \
  usr/lib/echo-os/echo-crash-health \
  usr/lib/echo-os/verify-native-agent-health \
  usr/lib/echo-os/verify-native-agent-runtime.py; do
  [[ -x "$IMAGE_MOUNT/$session_executable" ]] || {
    echo "desktop session executable is missing from artifact: /$session_executable" >&2
    exit 1
  }
done
[[ -d "$IMAGE_MOUNT/etc/echo-os" && ! -L "$IMAGE_MOUNT/etc/echo-os" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/etc/echo-os")" == "0:0:755" && \
   ! -e "$IMAGE_MOUNT/etc/echo-os/wayland-native-app-ipc" && \
   ! -L "$IMAGE_MOUNT/etc/echo-os/wayland-native-app-ipc" ]] || {
  echo "artifact must contain a safe admin directory without a baked Wayland IPC request" >&2
  exit 1
}
pass "artifact excludes the disposable Wayland native-app IPC request"
for backup_runtime in \
  usr/lib/systemd/system/echo-user-backup.service \
  usr/share/doc/echo-os/user-backup.md; do
  [[ -f "$IMAGE_MOUNT/$backup_runtime" ]] || {
    echo "encrypted backup runtime is missing from artifact: /$backup_runtime" >&2
    exit 1
  }
done
if find "$IMAGE_MOUNT/etc/systemd/system" -type l -lname '*echo-user-backup.service' \
     -print -quit | grep -q .; then
  echo "artifact unexpectedly enables backup without a repository credential" >&2
  exit 1
fi
for input_runtime in \
  usr/lib/x86_64-linux-gnu/gtk-3.0/3.0.0/immodules/im-fcitx5.so \
  usr/lib/x86_64-linux-gnu/gtk-4.0/4.0.0/immodules/libim-fcitx5.so \
  usr/lib/x86_64-linux-gnu/qt5/plugins/platforminputcontexts/libfcitx5platforminputcontextplugin.so \
  usr/lib/x86_64-linux-gnu/qt6/plugins/platforminputcontexts/libfcitx5platforminputcontextplugin.so \
  usr/lib/x86_64-linux-gnu/qt6/plugins/plasma/kcms/systemsettings/kcm_fcitx5.so \
  usr/share/fcitx5/inputmethod/pinyin.conf \
  usr/share/fcitx5/default/zh_CN \
  usr/share/applications/kcm_fcitx5.desktop; do
  [[ -f "$IMAGE_MOUNT/$input_runtime" ]] || {
    echo "multilingual input-method runtime is missing from artifact: /$input_runtime" >&2
    exit 1
  }
done
grep -Fxq '0=pinyin' "$IMAGE_MOUNT/usr/share/fcitx5/default/zh_CN" || {
  echo "artifact does not select Pinyin for a new zh_CN Fcitx5 profile" >&2
  exit 1
}
for clipboard_runtime in \
  etc/xdg/klipperrc \
  usr/lib/x86_64-linux-gnu/libklipper.so.6 \
  usr/lib/x86_64-linux-gnu/qt6/qml/org/kde/plasma/private/clipboard/libklipperplugin.so \
  usr/lib/x86_64-linux-gnu/qt6/plugins/sqldrivers/libqsqlite.so; do
  [[ -f "$IMAGE_MOUNT/$clipboard_runtime" ]] || {
    echo "system clipboard runtime is missing from artifact: /$clipboard_runtime" >&2
    exit 1
  }
done
grep -Fxq 'KeepClipboardContents=false' "$IMAGE_MOUNT/etc/xdg/klipperrc" && \
  grep -Fxq 'NoEmptyClipboard=true' "$IMAGE_MOUNT/etc/xdg/klipperrc" && \
  grep -Fxq 'MaxClipItems=20' "$IMAGE_MOUNT/etc/xdg/klipperrc" || {
    echo "artifact does not contain the volatile bounded clipboard policy" >&2
    exit 1
  }
for crash_runtime in \
  etc/systemd/coredump.conf.d/60-echo-os.conf \
  usr/lib/systemd/system/systemd-coredump.socket \
  usr/lib/systemd/system/echo-crash-health.service \
  usr/share/doc/echo-os/crash-collection.md; do
  [[ -f "$IMAGE_MOUNT/$crash_runtime" ]] || {
    echo "bounded crash-collection runtime is missing from artifact: /$crash_runtime" >&2
    exit 1
  }
done
[[ -x "$IMAGE_MOUNT/usr/lib/x86_64-linux-gnu/libexec/polkit-kde-authentication-agent-1" ]] || {
  echo "PolicyKit authentication agent is missing from artifact" >&2
  exit 1
}
for accessibility_runtime in \
  etc/environment.d/90qt-a11y.conf \
  usr/lib/systemd/user/at-spi-dbus-bus.service \
  usr/lib/python3/dist-packages/Xlib/__init__.py \
  usr/local/share/applications/echo-screen-reader.desktop; do
  [[ -f "$IMAGE_MOUNT/$accessibility_runtime" ]] || {
    echo "accessibility runtime is missing from artifact: /$accessibility_runtime" >&2
    exit 1
  }
done
grep -Fxq 'QT_ACCESSIBILITY=1' \
  "$IMAGE_MOUNT/etc/environment.d/90qt-a11y.conf" || {
  echo "artifact does not enable the Qt accessibility bridge" >&2
  exit 1
}
grep -Fxq 'Exec=/usr/bin/orca' \
  "$IMAGE_MOUNT/usr/local/share/applications/echo-screen-reader.desktop" || {
  echo "artifact screen-reader launcher does not use the fixed Orca binary" >&2
  exit 1
}
for power_runtime in \
  usr/lib/systemd/user/plasma-powerdevil.service \
  usr/lib/systemd/system/upower.service \
  usr/lib/systemd/system/power-profiles-daemon.service \
  usr/share/dbus-1/system-services/org.freedesktop.UPower.service \
  usr/share/dbus-1/system-services/net.hadess.PowerProfiles.service; do
  [[ -f "$IMAGE_MOUNT/$power_runtime" ]] || {
    echo "native power-management runtime is missing from artifact: /$power_runtime" >&2
    exit 1
  }
done
for control_tool in usr/bin/nmcli usr/bin/bluetoothctl usr/bin/wpctl usr/bin/brightnessctl; do
  [[ -x "$IMAGE_MOUNT/$control_tool" ]] || {
    echo "native Control Center executable is missing from artifact: /$control_tool" >&2
    exit 1
  }
done
for settings_module in \
  kcm_networkmanagement.desktop \
  kcm_bluetooth.desktop \
  kcm_pulseaudio.desktop \
  kcm_kscreen.desktop \
  kcm_powerdevilprofilesconfig.desktop \
  kcm_firewall.desktop; do
  [[ -f "$IMAGE_MOUNT/usr/share/applications/$settings_module" ]] || {
    echo "native System Settings module is missing from artifact: $settings_module" >&2
    exit 1
  }
done
for removable_storage_executable in \
  usr/bin/udisksctl \
  usr/libexec/udisks2/udisksd \
  usr/sbin/mkfs.vfat \
  usr/sbin/fsck.vfat \
  usr/sbin/mkfs.exfat \
  usr/sbin/fsck.exfat \
  usr/bin/ntfs-3g \
  usr/sbin/mkfs.ntfs \
  usr/sbin/mkfs.ext4 \
  usr/sbin/fsck.ext4 \
  usr/sbin/mkfs.btrfs \
  usr/bin/btrfs \
  usr/sbin/mkfs.xfs \
  usr/sbin/xfs_repair \
  usr/bin/dolphin \
  usr/lib/echo-os/echo-removable-storage-health; do
  [[ -x "$IMAGE_MOUNT/$removable_storage_executable" ]] || {
    echo "removable-storage executable is missing from artifact: /$removable_storage_executable" >&2
    exit 1
  }
done
for removable_storage_runtime in \
  usr/lib/systemd/system/udisks2.service \
  usr/lib/udev/rules.d/80-udisks2.rules \
  usr/share/dbus-1/system-services/org.freedesktop.UDisks2.service \
  usr/share/dbus-1/system.d/org.freedesktop.UDisks2.conf \
  usr/share/polkit-1/actions/org.freedesktop.UDisks2.policy \
  usr/lib/x86_64-linux-gnu/qt6/plugins/kf6/kio/mtp.so \
  usr/share/applications/org.kde.dolphin.desktop \
  usr/share/solid/actions/solid_mtp.desktop \
  usr/lib/systemd/system/echo-removable-storage-health.service \
  usr/share/doc/echo-os/removable-storage.md; do
  [[ -f "$IMAGE_MOUNT/$removable_storage_runtime" ]] || {
    echo "removable-storage integration is missing from artifact: /$removable_storage_runtime" >&2
    exit 1
  }
done
for printing_executable in \
  usr/sbin/cupsd \
  usr/bin/lp \
  usr/bin/lpstat \
  usr/bin/cancel \
  usr/sbin/lpadmin \
  usr/lib/cups/backend/ipp \
  usr/lib/cups/backend/ipps \
  usr/lib/cups/filter/pdftopdf \
  usr/lib/cups/filter/pdftoraster \
  usr/libexec/cups-pk-helper-mechanism \
  usr/sbin/ipp-usb \
  usr/sbin/avahi-daemon \
  usr/bin/kde-add-printer \
  usr/bin/configure-printer \
  usr/bin/kde-print-queue \
  usr/lib/echo-os/echo-printing-policy.py \
  usr/lib/echo-os/echo-printing-health; do
  [[ -x "$IMAGE_MOUNT/$printing_executable" ]] || {
    echo "printing executable is missing from artifact: /$printing_executable" >&2
    exit 1
  }
done
for printing_runtime in \
  etc/cups/cupsd.conf \
  etc/ipp-usb/ipp-usb.conf \
  usr/lib/systemd/system/cups.service \
  usr/lib/systemd/system/cups.socket \
  usr/lib/systemd/system/cups.path \
  usr/lib/systemd/system/ipp-usb.service \
  usr/lib/udev/rules.d/71-ipp-usb.rules \
  usr/lib/systemd/system/avahi-daemon.service \
  usr/lib/systemd/system/avahi-daemon.socket \
  usr/share/dbus-1/system-services/org.freedesktop.Avahi.service \
  usr/share/dbus-1/system.d/avahi-dbus.conf \
  etc/dbus-1/system.d/org.opensuse.CupsPkHelper.Mechanism.conf \
  usr/share/dbus-1/system-services/org.opensuse.CupsPkHelper.Mechanism.service \
  usr/share/polkit-1/actions/org.opensuse.cupspkhelper.mechanism.policy \
  usr/lib/x86_64-linux-gnu/qt6/plugins/kf6/kded/printmanager.so \
  usr/lib/x86_64-linux-gnu/qt6/plugins/plasma/kcms/systemsettings/kcm_printer_manager.so \
  usr/share/applications/kcm_printer_manager.desktop \
  usr/share/applications/org.kde.kde-add-printer.desktop \
  usr/lib/systemd/system/echo-printing-health.service \
  usr/share/doc/echo-os/printing.md; do
  [[ -f "$IMAGE_MOUNT/$printing_runtime" ]] || {
    echo "printing integration is missing from artifact: /$printing_runtime" >&2
    exit 1
  }
done
cmp "$REPO_ROOT/deploy/printing/cupsd.conf" "$IMAGE_MOUNT/etc/cups/cupsd.conf"
cmp "$REPO_ROOT/deploy/printing/ipp-usb.conf" "$IMAGE_MOUNT/etc/ipp-usb/ipp-usb.conf"
for scanning_executable in \
  usr/bin/scanimage \
  usr/bin/sane-find-scanner \
  usr/bin/airscan-discover \
  usr/bin/skanpage \
  usr/lib/echo-os/echo-scanning-policy.py \
  usr/lib/echo-os/echo-scanning-health; do
  [[ -x "$IMAGE_MOUNT/$scanning_executable" ]] || {
    echo "scanning executable is missing from artifact: /$scanning_executable" >&2
    exit 1
  }
done
for scanning_runtime in \
  etc/sane.d/airscan.conf \
  etc/sane.d/dll.conf \
  etc/sane.d/dll.d/airscan \
  usr/lib/x86_64-linux-gnu/libsane.so.1 \
  usr/lib/x86_64-linux-gnu/sane/libsane-airscan.so.1 \
  usr/lib/udev/rules.d/60-libsane1.rules \
  usr/lib/udev/rules.d/99-libsane1.rules \
  usr/share/applications/org.kde.skanpage.desktop \
  usr/lib/systemd/system/saned.service \
  usr/lib/systemd/system/saned.socket \
  usr/lib/systemd/system/saned@.service \
  usr/lib/systemd/system/echo-scanning-health.service \
  usr/share/doc/echo-os/scanning.md; do
  [[ -f "$IMAGE_MOUNT/$scanning_runtime" ]] || {
    echo "scanning integration is missing from artifact: /$scanning_runtime" >&2
    exit 1
  }
done
cmp "$REPO_ROOT/deploy/scanning/airscan.conf" "$IMAGE_MOUNT/etc/sane.d/airscan.conf"
SANED_ENABLEMENT="$(systemctl --root="$IMAGE_MOUNT" is-enabled saned.socket 2>/dev/null || true)"
[[ "$SANED_ENABLEMENT" == disabled || "$SANED_ENABLEMENT" == masked ]] || {
  echo "artifact unexpectedly enables saned scanner sharing: $SANED_ENABLEMENT" >&2
  exit 1
}
for core_app_executable in \
  usr/bin/xdg-mime \
  usr/bin/xdg-open \
  usr/bin/gio \
  usr/bin/desktop-file-validate \
  usr/bin/dolphin \
  usr/bin/konsole \
  usr/bin/firefox-esr \
  usr/bin/kate \
  usr/bin/okular \
  usr/bin/gwenview \
  usr/bin/ark \
  usr/bin/haruna \
  usr/bin/spectacle \
  usr/bin/kcalc \
  usr/bin/7z \
  usr/bin/bzip2 \
  usr/bin/unar \
  usr/bin/unzip \
  usr/bin/zip \
  usr/lib/echo-os/echo-core-apps-policy.py \
  usr/lib/echo-os/echo-core-apps-health \
  usr/lib/echo-os/echo-core-apps-session-smoke.py; do
  [[ -x "$IMAGE_MOUNT/$core_app_executable" ]] || {
    echo "core application executable is missing from artifact: /$core_app_executable" >&2
    exit 1
  }
done
for core_app_runtime in \
  etc/xdg/mimeapps.list \
  usr/share/applications/org.kde.dolphin.desktop \
  usr/share/applications/org.kde.konsole.desktop \
  usr/share/applications/firefox-esr.desktop \
  usr/share/applications/org.kde.kate.desktop \
  usr/share/applications/org.kde.okular.desktop \
  usr/share/applications/org.kde.gwenview.desktop \
  usr/share/applications/org.kde.ark.desktop \
  usr/share/applications/org.kde.haruna.desktop \
  usr/share/applications/org.kde.spectacle.desktop \
  usr/share/applications/org.kde.kcalc.desktop \
  usr/lib/systemd/system/echo-core-apps-health.service \
  usr/share/doc/echo-os/core-apps.md; do
  [[ -f "$IMAGE_MOUNT/$core_app_runtime" ]] || {
    echo "core application integration is missing from artifact: /$core_app_runtime" >&2
    exit 1
  }
done
cmp "$REPO_ROOT/deploy/core-apps/mimeapps.list" "$IMAGE_MOUNT/etc/xdg/mimeapps.list"
KSCREENLOCKER_GREETERS=("$IMAGE_MOUNT"/usr/lib/*-linux-gnu/libexec/kscreenlocker_greet)
[[ "${#KSCREENLOCKER_GREETERS[@]}" -eq 1 && \
   -x "${KSCREENLOCKER_GREETERS[0]}" && \
   -f "$IMAGE_MOUNT/usr/lib/pam.d/kde" ]] || {
  echo "KScreenLocker greeter/PAM chain is missing from artifact" >&2
  exit 1
}
[[ -f "$IMAGE_MOUNT/usr/share/kwin/scripts/org.echoos.windowbridge/metadata.json" && \
   -f "$IMAGE_MOUNT/usr/share/kwin/scripts/org.echoos.windowbridge/contents/code/main.js" ]] || {
  echo "KWin compositor script package is missing from artifact" >&2
  exit 1
}
[[ -f "$IMAGE_MOUNT/usr/lib/x86_64-linux-gnu/qt6/plugins/kwin/effects/plugins/org.echoos.liquidglass.so" ]] || {
  echo "KWin Liquid Glass effect is missing from artifact" >&2
  exit 1
}
python3 "$IMAGE_MOUNT/usr/lib/echo-os/verify-native-agent-runtime.py" \
  "$IMAGE_MOUNT/opt/echo-agent"
python3 - \
  "$IMAGE_MOUNT/opt/echo-agent/native-runtime.json" \
  "$IMAGE_MOUNT/opt/echo-agent/agent-bundle.json" \
  "$IMAGE_MOUNT/usr/lib/echo-os/image-contract" <<'PY'
import json
import re
import sys

runtime = json.load(open(sys.argv[1], encoding="utf-8"))
bundle = json.load(open(sys.argv[2], encoding="utf-8"))
contract = dict(
    line.rstrip("\n").split("=", 1)
    for line in open(sys.argv[3], encoding="utf-8")
    if "=" in line
)
source = runtime.get("source")
if not isinstance(source, dict) or source != bundle.get("source"):
    raise SystemExit("native Agent manifests do not share one source identity")
source_id = str(source.get("source_id") or "")
if source.get("dirty") or re.fullmatch(r"[0-9a-f]{40}", source_id) is None:
    raise SystemExit("release artifact contains a dirty or non-commit Agent source")
if contract.get("AGENT_SERVICE") != "echo-agent.service":
    raise SystemExit("image contract does not select the native Agent service")
if contract.get("AGENT_SOURCE_ID") != source_id:
    raise SystemExit("image contract Agent source differs from the immutable runtime")
if runtime.get("python_version") != "3.13" or runtime.get("platform") != "linux/amd64":
    raise SystemExit("native Agent runtime targets the wrong OS ABI")
if runtime.get("codex", {}).get("target") != "x86_64-unknown-linux-musl":
    raise SystemExit("native Agent runtime contains the wrong Codex target")
print(f"  ✓ native Agent source, Python ABI and Codex target agree: {source_id}")
PY
[[ "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/usr/lib/echo-os/echo-kwin-window-bridge")" == "0:0:755" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/usr/lib/echo-os/echo-notification-service")" == "0:0:755" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/usr/lib/echo-os/echo_notification_store.py")" == "0:0:644" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/usr/lib/echo-os/echo-clipboard-host")" == "0:0:755" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/etc/xdg/klipperrc")" == "0:0:644" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/usr/lib/echo-os/echo-accessibility-smoke.py")" == "0:0:755" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/usr/local/share/applications/echo-screen-reader.desktop")" == "0:0:644" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/usr/lib/echo-os/echo-sddm-accessibility")" == "0:0:755" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/usr/lib/echo-os/echo-sddm-xsetup")" == "0:0:755" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/usr/lib/echo-os/echo-sddm-xstop")" == "0:0:755" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/etc/sddm.conf.d/10-echo-os.conf")" == "0:0:644" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/etc/systemd/coredump.conf.d/60-echo-os.conf")" == "0:0:644" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/usr/lib/echo-os/echo-crash-health")" == "0:0:755" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/usr/lib/systemd/system/echo-crash-health.service")" == "0:0:644" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/etc/firewalld/firewalld.conf")" == "0:0:644" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/usr/lib/firewalld/zones/echo-public.xml")" == "0:0:644" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/usr/lib/echo-os/echo-firewall-policy.py")" == "0:0:755" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/usr/lib/echo-os/echo-firewall-health")" == "0:0:755" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/usr/lib/systemd/system/echo-firewall-health.service")" == "0:0:644" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/usr/share/doc/echo-os/host-firewall.md")" == "0:0:644" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/usr/lib/echo-os/echo-removable-storage-health")" == "0:0:755" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/usr/lib/systemd/system/echo-removable-storage-health.service")" == "0:0:644" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/usr/share/doc/echo-os/removable-storage.md")" == "0:0:644" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/etc/cups/cupsd.conf")" == "0:0:644" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/etc/ipp-usb/ipp-usb.conf")" == "0:0:644" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/usr/lib/echo-os/echo-printing-policy.py")" == "0:0:755" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/usr/lib/echo-os/echo-printing-health")" == "0:0:755" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/usr/lib/systemd/system/echo-printing-health.service")" == "0:0:644" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/usr/share/doc/echo-os/printing.md")" == "0:0:644" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/etc/sane.d/airscan.conf")" == "0:0:644" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/usr/lib/echo-os/echo-scanning-policy.py")" == "0:0:755" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/usr/lib/echo-os/echo-scanning-health")" == "0:0:755" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/usr/lib/systemd/system/echo-scanning-health.service")" == "0:0:644" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/usr/share/doc/echo-os/scanning.md")" == "0:0:644" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/etc/xdg/mimeapps.list")" == "0:0:644" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/usr/lib/echo-os/echo-core-apps-policy.py")" == "0:0:755" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/usr/lib/echo-os/echo-core-apps-health")" == "0:0:755" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/usr/lib/echo-os/echo-core-apps-session-smoke.py")" == "0:0:755" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/usr/lib/echo-os/verify-wayland-native-app-ipc.py")" == "0:0:755" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/usr/lib/systemd/system/echo-core-apps-health.service")" == "0:0:644" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/usr/share/doc/echo-os/core-apps.md")" == "0:0:644" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/opt/echo-os/deploy/desktop-session/echo-wayland-session.sh")" == "0:0:755" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/opt/echo-os/deploy/desktop-session/echo-wayland-shell-session.sh")" == "0:0:755" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/usr/share/wayland-sessions/echo-wayland.desktop")" == "0:0:644" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/etc/xdg/kscreenlockerrc")" == "0:0:644" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/usr/share/kwin/scripts/org.echoos.windowbridge/metadata.json")" == "0:0:644" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/usr/share/kwin/scripts/org.echoos.windowbridge/contents/code/main.js")" == "0:0:644" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/usr/lib/x86_64-linux-gnu/qt6/plugins/kwin/effects/plugins/org.echoos.liquidglass.so")" == "0:0:644" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/etc/pam.d/echo-lock")" == "0:0:644" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/opt/echo-agent/native-runtime.json")" == "0:0:644" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/opt/echo-agent/codex/bin/codex")" == "0:0:755" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/usr/lib/systemd/system/echo-agent.service")" == "0:0:644" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/usr/lib/echo-os/verify-native-agent-health")" == "0:0:755" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/usr/bin/echo-os-backup")" == "0:0:755" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/usr/lib/systemd/system/echo-user-backup.service")" == "0:0:644" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/usr/share/doc/echo-os/user-backup.md")" == "0:0:644" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/usr/bin/echo-os-update-channel")" == "0:0:755" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/usr/lib/echo-os/echo-os-update-apply")" == "0:0:755" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/usr/lib/echo-os/echo_update_channel.py")" == "0:0:755" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/usr/lib/echo-os/echo_update_status.py")" == "0:0:755" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/usr/lib/echo-os/echo_update_trust.py")" == "0:0:755" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/usr/lib/echo-os/update-trust-policy.json")" == "0:0:644" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/usr/lib/echo-os/update-channel")" == "0:0:644" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/usr/lib/systemd/system/echo-os-update-fetch.service")" == "0:0:644" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/usr/lib/systemd/system/echo-os-update-fetch.timer")" == "0:0:644" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/usr/lib/systemd/system/echo-update-trust-promote.service")" == "0:0:644" && \
   "$(stat -c '%u:%g:%a' "$IMAGE_MOUNT/usr/share/polkit-1/actions/org.echoos.update.policy")" == "0:0:644" ]] || {
  echo "desktop, firewall, storage, printing, scanning, core-app, crash collection or Agent files have unsafe artifact ownership/mode" >&2
  exit 1
}
python3 - \
  "$IMAGE_MOUNT/usr/lib/echo-os/echo-firewall-policy.py" \
  "$IMAGE_MOUNT/etc/firewalld/firewalld.conf" \
  "$IMAGE_MOUNT/usr/lib/firewalld/zones/echo-public.xml" <<'PY'
import importlib.util
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("artifact_firewall_policy", sys.argv[1])
if spec is None or spec.loader is None:
    raise SystemExit("cannot import installed firewall policy verifier")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
zone = module.verify_policy(
    Path(sys.argv[2]),
    Path(sys.argv[3]),
    expected_uid=0,
    require_vendor_default=True,
)
if zone != "echo-public":
    raise SystemExit("installed firewall policy has the wrong default zone")
print("  ✓ installed firewall baseline is strict, owned and parseable")
PY
ECHO_PRINTING_SOURCE_TEST=USE-SOURCE-RUNTIME \
  python3 "$IMAGE_MOUNT/usr/lib/echo-os/echo-printing-policy.py" \
    --cups-config "$IMAGE_MOUNT/etc/cups/cupsd.conf" \
    --ipp-usb-config "$IMAGE_MOUNT/etc/ipp-usb/ipp-usb.conf"
ECHO_SCANNING_SOURCE_TEST=USE-SOURCE-RUNTIME \
  python3 "$IMAGE_MOUNT/usr/lib/echo-os/echo-scanning-policy.py" \
    --airscan-config "$IMAGE_MOUNT/etc/sane.d/airscan.conf"
ECHO_CORE_APPS_SOURCE_TEST=USE-SOURCE-RUNTIME \
  python3 "$IMAGE_MOUNT/usr/lib/echo-os/echo-core-apps-policy.py" \
    --mimeapps "$IMAGE_MOUNT/etc/xdg/mimeapps.list"
python3 "$IMAGE_MOUNT/usr/lib/echo-os/echo_update_trust.py" verify-system \
  --system-policy "$IMAGE_MOUNT/usr/lib/echo-os/update-trust-policy.json" \
  --system-keyring "$IMAGE_MOUNT/usr/lib/echo-os/update-keyring.gpg" \
  --verifier "$IMAGE_MOUNT/usr/lib/echo-os/verify-public-keyring.py"
systemd-analyze --root="$IMAGE_MOUNT" verify \
  echo-agent.service \
  echo-agent-health.service \
  echo-oem-setup.service \
  echo-local-account.service \
  echo-account-capture.service \
  echo-account-capture.path \
  echo-login-health.service \
  echo-app-catalog.service \
  echo-machine-identity-health.service \
  echo-network-state-prepare.service \
  NetworkManager.service \
  echo-region-state-restore.service \
  echo-region-state-capture.service \
  echo-region-state-capture.path \
  echo-desktop.service \
  echo-desktop-health.service \
  systemd-coredump.socket \
  echo-crash-health.service \
  firewalld.service \
  echo-firewall-health.service \
  udisks2.service \
  echo-removable-storage-health.service \
  cups.socket \
  cups.path \
  cups.service \
  ipp-usb.service \
  avahi-daemon.socket \
  avahi-daemon.service \
  echo-printing-health.service \
  saned.socket \
  saned.service \
  echo-scanning-health.service \
  echo-core-apps-health.service \
  echo-user-backup.service \
  echo-os-update-fetch.service \
  echo-os-update-fetch.timer \
  echo-update-trust-promote.service
systemd-dissect --umount "$IMAGE_MOUNT"
IMAGE_MOUNT=""
pass "artifact contains native Agent, encrypted backup, PAM lock, multilingual input, volatile system clipboard, AT-SPI/Orca, bounded crash, host-firewall, removable-storage, private-printing, native-scanning, offline core-app and functional XDG session chains; systemd verifies Agent, backup, OEM, login, application, machine-state, network, firewall, storage, printing, scanning, core-app, crash and desktop dependencies"

pass "version-matched root, verity, signature and UKI update artifacts exist"

if command -v ukify >/dev/null 2>&1; then
  UKIFY_BIN="$(command -v ukify)"
elif [[ -x /usr/lib/systemd/ukify ]]; then
  UKIFY_BIN=/usr/lib/systemd/ukify
else
  echo "UKI artifact verifier dependency missing: ukify" >&2
  exit 1
fi
MAIN_UKI_INSPECT="$VERIFY_TEMP_DIR/main-uki.inspect"
MAIN_INITRD="$VERIFY_TEMP_DIR/main-initrd"
MAIN_INITRD_CONTENTS="$VERIFY_TEMP_DIR/main-initrd.contents"
MAIN_PCR_PUBLIC_KEY="$VERIFY_TEMP_DIR/main-pcr-public-key.pem"
MAIN_PCR_SIGNATURE="$VERIFY_TEMP_DIR/main-pcr-signature.json"
"$UKIFY_BIN" inspect "$UKI_PAYLOAD" >"$MAIN_UKI_INSPECT"
grep -Eq 'roothash=[0-9a-f]{64}' "$MAIN_UKI_INSPECT"
if grep -Eq '(^|[[:space:]])root=' "$MAIN_UKI_INSPECT"; then
  echo "signed UKI contains a mutable root= selector" >&2
  exit 1
fi
grep -q '\.pcrsig' "$MAIN_UKI_INSPECT"
grep -q '\.pcrpkey' "$MAIN_UKI_INSPECT"
"$UKIFY_BIN" inspect "$UKI_PAYLOAD" \
  --section ".initrd:binary@$MAIN_INITRD" \
  --section ".pcrpkey:text@$MAIN_PCR_PUBLIC_KEY" \
  --section ".pcrsig:text@$MAIN_PCR_SIGNATURE" >/dev/null
lsinitramfs "$MAIN_INITRD" >"$MAIN_INITRD_CONTENTS"
for initrd_member in \
  usr/lib/echo-os/echo-machine-id \
  usr/lib/systemd/system/echo-machine-state-initrd.service \
  usr/lib/systemd/system/initrd-root-fs.target.d/echo-machine-state.conf \
  etc/crypttab; do
  grep -qx "$initrd_member" "$MAIN_INITRD_CONTENTS" || {
    echo "custom systemd initrd member is missing: /$initrd_member" >&2
    exit 1
  }
done
grep -Eq 'cryptsetup/libcryptsetup-token-systemd-tpm2\.so$' \
  "$MAIN_INITRD_CONTENTS" || {
    echo "custom systemd initrd lacks the TPM2 cryptsetup token" >&2
    exit 1
  }
grep -Eq 'kernel/fs/overlayfs/overlay\.ko(\.[a-z0-9]+)?$' \
  "$MAIN_INITRD_CONTENTS" || {
    echo "custom systemd initrd lacks overlayfs" >&2
    exit 1
  }
grep -Eq 'kernel/drivers/md/dm-verity\.ko(\.[a-z0-9]+)?$' \
  "$MAIN_INITRD_CONTENTS" || {
    echo "custom systemd initrd lacks dm-verity" >&2
    exit 1
  }
python3 "$REPO_ROOT/deploy/data-protection/verify_uki_pcr_policy.py" \
  "$PCR_POLICY_PUBLIC_KEY" "$MAIN_PCR_PUBLIC_KEY" "$MAIN_PCR_SIGNATURE"
pass "UKI binds dm-verity root, embeds the custom encrypted-state initrd and carries the authorized signed-PCR11 policy"

read -r SECTOR_SIZE ESP_START < <(
  python3 - "$PARTITION_JSON" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    table = json.load(stream)["partitiontable"]
esp = next((p for p in table["partitions"] if p.get("name") == "echo-esp"), None)
if not esp:
    raise SystemExit("echo-esp partition not found")
print(table["sectorsize"], esp["start"])
PY
)
ESP_IMAGE="${IMAGE_PATH}@@$((SECTOR_SIZE * ESP_START))"
RECOVERY_COPY="$VERIFY_TEMP_DIR/echo-recovery.efi"
LOADER_CONF_COPY="$VERIFY_TEMP_DIR/loader.conf"
SYSTEMD_BOOT_COPY="$VERIFY_TEMP_DIR/systemd-bootx64.efi"
mcopy -i "$ESP_IMAGE" "::/EFI/Linux/echo-recovery_${IMAGE_VERSION}.efi" "$RECOVERY_COPY"
mcopy -i "$ESP_IMAGE" "::/loader/loader.conf" "$LOADER_CONF_COPY"
RECOVERY_UKI_INSPECT="$VERIFY_TEMP_DIR/recovery-uki.inspect"
RECOVERY_PCR_PUBLIC_KEY="$VERIFY_TEMP_DIR/recovery-pcr-public-key.pem"
RECOVERY_PCR_SIGNATURE="$VERIFY_TEMP_DIR/recovery-pcr-signature.json"
"$UKIFY_BIN" inspect "$RECOVERY_COPY" >"$RECOVERY_UKI_INSPECT"
grep -q 'ID=echo-recovery' "$RECOVERY_UKI_INSPECT"
grep -q '\.pcrsig' "$RECOVERY_UKI_INSPECT"
grep -q '\.pcrpkey' "$RECOVERY_UKI_INSPECT"
"$UKIFY_BIN" inspect "$RECOVERY_COPY" \
  --section ".pcrpkey:text@$RECOVERY_PCR_PUBLIC_KEY" \
  --section ".pcrsig:text@$RECOVERY_PCR_SIGNATURE" >/dev/null
python3 "$REPO_ROOT/deploy/data-protection/verify_uki_pcr_policy.py" \
  "$PCR_POLICY_PUBLIC_KEY" "$RECOVERY_PCR_PUBLIC_KEY" \
  "$RECOVERY_PCR_SIGNATURE"
grep -q '^default echo-os_\*$' "$LOADER_CONF_COPY"
grep -q '^editor no$' "$LOADER_CONF_COPY"
pass "ESP contains authorized signed-PCR11 Recovery without changing the normal default"

mcopy -i "$ESP_IMAGE" "::/EFI/systemd/systemd-bootx64.efi" "$SYSTEMD_BOOT_COPY"
sbverify --cert "$ECHO_SECURE_BOOT_CERTIFICATE" "$UKI_PAYLOAD" >/dev/null
sbverify --cert "$ECHO_SECURE_BOOT_CERTIFICATE" "$RECOVERY_COPY" >/dev/null
sbverify --cert "$ECHO_SECURE_BOOT_CERTIFICATE" "$SYSTEMD_BOOT_COPY" >/dev/null
pass "systemd-boot, desktop UKI and Recovery UKI carry the configured Secure Boot signature"

CHECKSUM_FILE="${IMAGE_PATH%.raw}.SHA256SUMS"
[[ -f "$CHECKSUM_FILE" ]] || { echo "SHA256SUMS artifact is missing" >&2; exit 1; }
(
  cd "$(dirname "$CHECKSUM_FILE")"
  sha256sum --check "$(basename "$CHECKSUM_FILE")"
)
pass "generated artifact checksum verifies"

find "$(dirname "$IMAGE_PATH")" -maxdepth 1 -type f -name '*.manifest' -print -quit | grep -q . || {
  echo "JSON package manifest is missing" >&2
  exit 1
}
find "$(dirname "$IMAGE_PATH")" -maxdepth 1 -type f -name '*.changelog' -print -quit | grep -q . || {
  echo "human-readable package changelog is missing" >&2
  exit 1
}
pass "package manifests exist"
echo "Image artifact contract OK"
