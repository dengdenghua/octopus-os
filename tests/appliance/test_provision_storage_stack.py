"""Fresh-install storage stack must leave a usable ZFS kernel module."""

from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[2]

HARDWARE_SUPPORT_PACKAGES = {
    "firmware-linux-free",
    "firmware-linux-nonfree",
    "firmware-misc-nonfree",
    "firmware-realtek",
    "firmware-iwlwifi",
    "firmware-atheros",
    "firmware-brcm80211",
    "firmware-mediatek",
    "firmware-amd-graphics",
    "firmware-intel-graphics",
    "intel-microcode",
    "amd64-microcode",
    "nvme-cli",
    "pciutils",
    "usbutils",
    "ethtool",
    "lm-sensors",
}


def test_storage_step_builds_zfs_for_the_running_kernel_before_marking_done() -> None:
    provision = (
        REPOSITORY / "deploy/provision/base/provision-lib.sh"
    ).read_text(encoding="utf-8")
    storage = provision.split("step_storage() {", 1)[1].split("\n}\n", 1)[0]

    headers = '"linux-headers-${kernel_release}"'
    zfs_package = "zfsutils-linux zfs-dkms"
    autoinstall = 'dkms autoinstall -k "$kernel_release"'
    load = "modprobe zfs"
    import_service = "systemctl enable --now zfs-import-cache"
    done = "done_mark storage"

    assert storage.index("ensure_swap") < storage.index(headers)
    assert 'kernel_release="$(uname -r)"' in storage
    assert storage.index(headers) < storage.index(zfs_package)
    assert storage.index(zfs_package) < storage.index(autoinstall)
    assert storage.index(autoinstall) < storage.index(load)
    assert storage.index(load) < storage.index(import_service) < storage.index(done)
    assert f"{import_service} || true" not in storage


def test_low_memory_swap_is_ready_before_the_storage_package_transaction() -> None:
    provision = (
        REPOSITORY / "deploy/provision/base/provision-lib.sh"
    ).read_text(encoding="utf-8")
    helper = provision.split("ensure_swap() {", 1)[1].split("\n}\n", 1)[0]
    storage = provision.split("step_storage() {", 1)[1].split("\n}\n", 1)[0]

    assert "need_mb=2048" in helper
    assert 'of="$sf.partial"' in helper
    assert helper.index('mkswap -q "$sf.partial"') < helper.index('mv "$sf.partial" "$sf"')
    assert storage.index("ensure_swap") < storage.index("apt-get install")


def test_hardware_packages_are_split_out_of_the_core_storage_transaction() -> None:
    provision = (
        REPOSITORY / "deploy/provision/base/provision-lib.sh"
    ).read_text(encoding="utf-8")
    storage = provision.split("step_storage() {", 1)[1].split("\n}\n", 1)[0]

    assert storage.count("apt-get install -y --no-install-recommends") == 3
    assert storage.index("zfsutils-linux zfs-dkms") < storage.index(
        "firmware-linux-free firmware-linux-nonfree"
    )
    assert storage.index("firmware-amd-graphics firmware-intel-graphics") < storage.index(
        "firmware-realtek firmware-iwlwifi"
    )
    assert storage.index("intel-microcode amd64-microcode") < storage.index(
        "nvme-cli pciutils usbutils ethtool lm-sensors"
    )


def test_every_apt_operation_has_bounded_firstboot_retries() -> None:
    provision = (
        REPOSITORY / "deploy/provision/base/provision-lib.sh"
    ).read_text(encoding="utf-8")
    helper = provision.split("apt_retry() {", 1)[1].split("\n}\n", 1)[0]
    apt_lines = [
        line.strip()
        for line in provision.splitlines()
        if "apt-get " in line and not line.lstrip().startswith("#")
    ]

    assert "max_attempts=4" in helper
    assert "delay_seconds=$((delay_seconds * 2))" in helper
    assert apt_lines
    assert all(line.startswith("apt_retry ") for line in apt_lines)


def test_both_image_paths_keep_the_nas_hardware_support_baseline() -> None:
    provision = (
        REPOSITORY / "deploy/provision/base/provision-lib.sh"
    ).read_text(encoding="utf-8")
    storage = provision.split("step_storage() {", 1)[1].split("\n}\n", 1)[0]
    mkosi = (REPOSITORY / "packaging/image/mkosi.conf").read_text(encoding="utf-8")
    image_gate = (REPOSITORY / "packaging/image/verify-image.sh").read_text(
        encoding="utf-8"
    )

    for package in HARDWARE_SUPPORT_PACKAGES:
        assert package in storage
        assert f"        {package}\n" in mkosi
        assert f"'^        {package}$'" in image_gate


def test_vmtest_installer_attaches_and_validates_netinst_iso() -> None:
    launcher = (
        REPOSITORY / "deploy/provision/vmtest/tools/launch-vm.ps1"
    ).read_text(encoding="ascii")

    assert "[string]$IsoPath" in launcher
    assert "Test-Path -LiteralPath $IsoPath -PathType Leaf" in launcher
    assert '"-cdrom `"$IsoPath`" "' in launcher


def test_iso_builder_writes_overlay_before_the_pathspec_separator() -> None:
    builder = (REPOSITORY / "deploy/provision/build-iso.sh").read_text(
        encoding="utf-8"
    )

    assert (
        'git archive -o "$PAYLOAD/echo-overlay.tar.gz" HEAD -- "${OVERLAY_FILES[@]}"'
        in builder
    )
    assert 'install -m0644 "$PAYLOAD/echo-overlay.tar.gz"' not in builder


def test_iso_overlay_excludes_deleted_paths_and_preserves_spaces() -> None:
    builder = (REPOSITORY / "deploy/provision/build-iso.sh").read_text(
        encoding="utf-8"
    )

    assert "mapfile -d '' -t OVERLAY_FILES" in builder
    assert "--diff-filter=ACMRTUXB --name-only -z" in builder
    assert '"${OVERLAY_FILES[@]}"' in builder


def test_iso_checksum_refresh_does_not_follow_the_debian_directory_loop() -> None:
    builder = (REPOSITORY / "deploy/provision/build-iso.sh").read_text(
        encoding="utf-8"
    )

    assert "find . -type f ! -name md5sum.txt -print0" in builder
    assert "find . -follow" not in builder


def test_iso_builder_injects_preseed_before_installer_separator() -> None:
    builder = (REPOSITORY / "deploy/provision/build-iso.sh").read_text(
        encoding="utf-8"
    )

    assert "inject_installer_args append" in builder
    assert "inject_installer_args linux" in builder
    assert "auto=true priority=critical locale=zh_CN.UTF-8" in builder
    assert "keyboard-configuration/xkb-keymap=us" in builder
    assert "s|[[:space:]]---| $APPEND_ARGS ---|" in builder


def test_iso_boot_menus_default_to_the_text_echo_installer() -> None:
    builder = (REPOSITORY / "deploy/provision/build-iso.sh").read_text(
        encoding="utf-8"
    )

    assert "ontimeout install" in builder
    assert "timeout 50" in builder
    assert "Echo OS installer (BIOS mode)" in builder
    assert "menu label ^Echo OS installer" in builder
    assert "set timeout=5\\nset default=1" in builder
    assert "sed -i -E '/^[[:space:]]*ontimeout[[:space:]]/d'" in builder


def test_iso_builder_appends_preseed_and_tui_to_every_installer_initrd() -> None:
    builder = (REPOSITORY / "deploy/provision/build-iso.sh").read_text(
        encoding="utf-8"
    )

    assert "preseed/file=/preseed.cfg" in builder
    assert "preseed/file=/cdrom/echo-os/preseed.cfg" not in builder
    assert "s#/cdrom/echo-os/echo-install#/bin/sh /echo-install#" in builder
    assert 'find . -print0 | cpio --null -o --format=newc' in builder
    assert 'find "$WORK/iso/install.amd" -type f -name initrd.gz -print0' in builder
    assert 'cat "$INITRD_SEGMENT" >>"$initrd"' in builder


def test_installer_tui_uses_posix_arguments_and_normalizes_device_paths() -> None:
    installer = (REPOSITORY / "deploy/provision/installer/echo-install").read_text(
        encoding="utf-8"
    )
    assert installer.startswith("#!/bin/sh\n")
    assert "menu_items=(" not in installer
    assert "menu_items[@]" not in installer
    assert 'set -- "$@" "$dev"' in installer
    assert "dev=${dev#/dev/}" in installer
    assert 'if [ -n "$devices" ]; then' in installer
    assert 'log "disks=$DISKS"' in installer


def test_installer_uses_the_native_debconf_frontend_without_whiptail() -> None:
    builder = (REPOSITORY / "deploy/provision/build-iso.sh").read_text(
        encoding="utf-8"
    )
    installer = (REPOSITORY / "deploy/provision/installer/echo-install").read_text(
        encoding="utf-8"
    )
    preseed = (REPOSITORY / "deploy/provision/installer/preseed.cfg").read_text(
        encoding="utf-8"
    )
    templates = (
        REPOSITORY / "deploy/provision/installer/echo-install.templates"
    ).read_text(encoding="utf-8")

    assert 'debconf-loadtemplate echo-os /echo-install.templates' in installer
    assert 'db_input critical echo-os/disk' in installer
    assert 'db_input critical echo-os/password' in installer
    assert 'if [ "$USE_WHIPTAIL" -eq 1 ] && command -v debconf-set-selections' in installer
    assert "db_register debian-installer/dummy" in installer
    assert 'set_answer partman-auto/disk "/dev/$SYSDEV"' in installer
    assert 'set_answer netcfg/get_hostname "$HOSTNAME"' in installer
    assert 'set_answer passwd/user-password "$PW1"' in installer
    assert "d-i passwd/user-password-crypted" not in preseed
    assert '[ "$USE_WHIPTAIL" -eq 1 ] || db_stop' in installer
    assert "DONE=${ECHO_INSTALL_DONE:-/tmp/echo-install.completed}" in installer
    assert 'if [ -f "$DONE" ]; then' in installer
    assert ': >"$DONE"' in installer
    assert 'log "already-complete"' in installer
    assert 'log "answers-applied"' in installer
    assert '"$INITRD_ROOT/echo-install.templates"' in builder
    assert "Template: echo-os/disk" in templates
    assert "Type: password" in templates


def test_installer_late_command_finds_iso_payload_and_fails_atomically() -> None:
    preseed = (REPOSITORY / "deploy/provision/installer/preseed.cfg").read_text(
        encoding="utf-8"
    )
    late_command = preseed.split("d-i preseed/late_command string", 1)[1]

    assert "set -e" in late_command
    assert "/media/cdrom/echo" in late_command
    assert "/cdrom/echo" in late_command
    assert 'if [ -z "$payload" ]' in late_command
    assert 'cp "$payload/setup-base.sh"' in late_command
    assert 'cp "$payload/echo-firstboot.service"' in late_command
    assert "in-target systemctl enable echo-firstboot.service" in late_command
    assert "echo stageA-ok > /target/var/log/echo-stageA.txt" in late_command
    assert "/cdrom/echo-os/setup-base.sh" not in late_command
    assert "echo-os-overlay.tar.gz 2>/dev/null || true" not in late_command
