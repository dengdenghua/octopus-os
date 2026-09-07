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


def test_apt_index_failures_enter_the_existing_retry_loop() -> None:
    provision = (
        REPOSITORY / "deploy/provision/base/provision-lib.sh"
    ).read_text(encoding="utf-8")

    assert "apt_update() {" in provision
    assert "apt_retry apt-get -o APT::Update::Error-Mode=any update" in provision
    assert provision.count("apt_update") == 3
    assert "apt_retry apt-get update" not in provision


def test_apt_transfers_have_a_bounded_timeout_before_outer_retries() -> None:
    provision = (
        REPOSITORY / "deploy/provision/base/provision-lib.sh"
    ).read_text(encoding="utf-8")
    apt_step = provision.split("step_apt() {", 1)[1].split("\n}\n", 1)[0]

    assert "cat >/etc/apt/apt.conf.d/80echo-network <<'EOF'" in apt_step
    assert 'Acquire::Retries "3";' in apt_step
    assert 'Acquire::http::Timeout "30";' in apt_step
    assert 'Acquire::https::Timeout "30";' in apt_step
    assert apt_step.index("80echo-network") < apt_step.index("apt_update")


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


def test_iso_builder_embeds_an_exact_single_commit_source_bundle() -> None:
    builder = (REPOSITORY / "deploy/provision/build-iso.sh").read_text(
        encoding="utf-8"
    )

    assert "commit-tree \"$SOURCE_TREE\"" in builder
    assert 'git bundle create "$PAYLOAD/echo-source.bundle" "$SNAPSHOT_REF"' in builder
    assert 'git bundle verify "$PAYLOAD/echo-source.bundle"' in builder
    assert 'git show -s --format=%T "$SNAPSHOT_COMMIT"' in builder
    assert 'ECHO_SOURCE_TREE="$SOURCE_TREE"' in builder


def test_firstboot_prefers_local_source_bundle_over_remote_clone() -> None:
    provision = (
        REPOSITORY / "deploy/provision/base/provision-lib.sh"
    ).read_text(encoding="utf-8")
    source = provision.split("step_echo_src() {", 1)[1].split("\n}\n", 1)[0]

    assert 'if [ -f "$SOURCE_BUNDLE" ]' in source
    assert 'git -C "$OS_DIR" fetch -q "$SOURCE_BUNDLE" "$SOURCE_REF"' in source
    assert "rev-parse 'HEAD^{tree}'" in source
    assert 'git -C "$OS_DIR" remote add origin "$OS_REPO"' in source
    assert source.index('if [ -f "$SOURCE_BUNDLE" ]') < source.index("git_retry()")


def test_firstboot_deduplicates_only_debian_sources_and_keeps_backup() -> None:
    provision = (
        REPOSITORY / "deploy/provision/base/provision-lib.sh"
    ).read_text(encoding="utf-8")
    apt = provision.split("step_apt() {", 1)[1].split("\n}\n", 1)[0]

    assert "/etc/apt/sources.list.echo-installer" in apt
    assert 'awk -v mirror_host="$mirror_host"' in apt
    assert 'host == "deb.debian.org"' in apt
    assert 'host == "security.debian.org"' in apt
    assert 'mv "$sources_tmp" /etc/apt/sources.list' in apt


def test_formal_installer_persists_source_bundle_and_identity() -> None:
    builder = (REPOSITORY / "deploy/provision/build-iso.sh").read_text(
        encoding="utf-8"
    )
    preseed = (REPOSITORY / "deploy/provision/installer/preseed.cfg").read_text(
        encoding="utf-8"
    )

    assert 'ECHO_SOURCE_BUNDLE="/opt/echo-os-source.bundle"' in builder
    assert 'ECHO_SOURCE_BUNDLE_REF="$SOURCE_BUNDLE_REF"' in builder
    assert 'ECHO_IMAGE_COMMIT="$SOURCE_COMMIT"' in builder
    assert 'cp "$payload/echo-source.bundle" /target/opt/echo-os-source.bundle' in preseed


def test_iso_can_embed_a_verified_prebuilt_web_payload_for_headless_nas() -> None:
    builder = (REPOSITORY / "deploy/provision/build-iso.sh").read_text(
        encoding="utf-8"
    )
    preseed = (REPOSITORY / "deploy/provision/installer/preseed.cfg").read_text(
        encoding="utf-8"
    )
    provision = (
        REPOSITORY / "deploy/provision/base/provision-lib.sh"
    ).read_text(encoding="utf-8")

    assert '--web-dist) WEB_DIST="$2"' in builder
    web_builder = (
        REPOSITORY / "deploy/provision/build-web-dist.sh"
    ).read_text(encoding="utf-8")

    assert "pnpm install --frozen-lockfile" in web_builder
    assert "pnpm build" in web_builder
    assert "ELECTRON_SKIP_BINARY_DOWNLOAD=1" in web_builder
    assert web_builder.count("status --porcelain --untracked-files=all") == 2
    assert 'rev-parse \'HEAD^{tree}\'' in web_builder
    assert 'dist/.echo-source-tree"' in web_builder
    assert 'WEB_DIST" = "$EXPECTED_WEB_DIST' in builder
    assert "status --porcelain --untracked-files=all" in builder
    assert '"$WEB_DIST/.echo-source-tree"' in builder
    assert '!= "$SOURCE_TREE"' not in builder
    assert "tar --sort=name --mtime='@0' --owner=0 --group=0" in builder
    assert "--mode='u+rwX,go+rX,go-w'" in builder
    assert 'WEB_BUNDLE_SHA256="$(sha256sum "$WEB_BUNDLE"' in builder
    assert 'ECHO_WEB_BUNDLE="$WEB_BUNDLE_TARGET"' in builder
    assert 'ECHO_WEB_BUNDLE_SHA256="$WEB_BUNDLE_SHA256"' in builder
    assert (
        'cp "$payload/echo-web-dist.tar.gz" /target/opt/echo-web-dist.tar.gz'
        in preseed
    )
    assert "use_prebuilt_web() {" in provision
    assert 'actual="$(sha256sum "$bundle"' in provision
    assert 'tar --no-same-owner --no-same-permissions -xzf "$bundle"' in provision
    assert '"$staged/.echo-source-tree"' in provision
    assert '!= "$ECHO_SOURCE_TREE"' not in provision
    assert "跳过 NodeSource/npm" in provision
    assert "无需 Node/npm/pnpm 网络" in provision


def test_iso_can_embed_a_verified_python_wheelhouse_for_offline_firstboot() -> None:
    builder = (REPOSITORY / "deploy/provision/build-iso.sh").read_text(
        encoding="utf-8"
    )
    wheel_builder = (
        REPOSITORY / "deploy/provision/build-python-wheelhouse.sh"
    ).read_text(encoding="utf-8")
    preseed = (REPOSITORY / "deploy/provision/installer/preseed.cfg").read_text(
        encoding="utf-8"
    )
    provision = (
        REPOSITORY / "deploy/provision/base/provision-lib.sh"
    ).read_text(encoding="utf-8")

    assert '--python-wheelhouse) PYTHON_WHEELHOUSE="$2"' in builder
    assert "status --porcelain --untracked-files=all" in wheel_builder
    assert "--only-binary=:all:" in wheel_builder
    assert '--only-binary=:all: "uv==0.11.25"' in wheel_builder
    assert "--frozen --no-dev --no-emit-project" in wheel_builder
    assert "--extra serve --extra web --extra appliance --extra minimal" in wheel_builder
    assert "--require-hashes" in wheel_builder
    assert '--requirement "$LOCKED_REQUIREMENTS"' in wheel_builder
    assert '"packaging==$PACKAGING_VERSION"' in wheel_builder
    assert '--no-deps --wheel-dir "$STAGED" "$REPO_ROOT"' in wheel_builder
    assert "cpython-3??\\ linux\\ x86_64" in wheel_builder
    assert 'rev-parse \'HEAD^{tree}\'' in wheel_builder
    assert '"$STAGED/.echo-source-tree"' in wheel_builder
    assert '"$STAGED/.echo-python-runtime"' in wheel_builder
    assert "sha256sum -c SHA256SUMS" in wheel_builder
    assert 'PYTHON_WHEELHOUSE" = "$EXPECTED_PYTHON_WHEELHOUSE' in builder
    assert '"$PYTHON_WHEELHOUSE/.echo-source-tree"' in builder
    assert "sha256sum -c SHA256SUMS" in builder
    assert "tar --sort=name --mtime='@0' --owner=0 --group=0" in builder
    assert 'ECHO_PYTHON_BUNDLE="$PYTHON_BUNDLE_TARGET"' in builder
    assert 'ECHO_PYTHON_BUNDLE_SHA256="$PYTHON_BUNDLE_SHA256"' in builder
    assert '"$REPO_ROOT/frontend/package.json"' in builder
    assert "| head -1" in builder
    assert 'ECHO_PACKAGED_CODEX_VERSION="$CODEX_PACKAGE_VERSION"' in builder
    assert (
        'cp "$payload/echo-python-wheelhouse.tar.gz" '
        "/target/opt/echo-python-wheelhouse.tar.gz"
    ) in preseed
    assert "use_prebuilt_python() {" in provision
    assert 'actual="$(sha256sum "$bundle"' in provision
    assert '"$staged/.echo-source-tree"' in provision
    assert '"$staged/.echo-python-runtime"' in provision
    assert "for required_file in .echo-source-tree .echo-python-runtime SHA256SUMS" in provision
    assert "--no-index --no-cache-dir --only-binary=:all:" in provision
    assert '"${echo_wheels[0]}[$extras]" packaging' in provision
    assert "无需 PyPI 网络" in provision


def test_native_appliance_service_inherits_the_image_codex_version() -> None:
    service = (
        REPOSITORY / "deploy/provision/base/echo-appliance.service"
    ).read_text(encoding="utf-8")

    assert "EnvironmentFile=-/etc/echo-os/firstboot.env" in service
    assert "ExecStart=/opt/echo-os/.venv/bin/echo-agent serve" in service


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


def test_normal_nas_boot_does_not_enable_the_offline_recovery_unit() -> None:
    provision = (
        REPOSITORY / "deploy/provision/base/provision-lib.sh"
    ).read_text(encoding="utf-8")
    recovery = provision.split("step_backup_recovery() {", 1)[1].split("\n}\n", 1)[0]

    assert "systemctl disable --now echo-recovery.service" in recovery
    assert "systemctl reset-failed echo-recovery.service" in recovery
    assert "systemctl enable echo-recovery.service" not in recovery
    assert "systemctl enable echo-user-backup.service" in recovery
    assert "systemctl enable echo-restore-transaction-health.service" in recovery


def test_iso_profiles_default_to_headless_nas_and_persist_firstboot_config() -> None:
    builder = (REPOSITORY / "deploy/provision/build-iso.sh").read_text(
        encoding="utf-8"
    )
    preseed = (REPOSITORY / "deploy/provision/installer/preseed.cfg").read_text(
        encoding="utf-8"
    )
    vm_preseed = (
        REPOSITORY / "deploy/provision/vmtest/preseed-vmtest.cfg"
    ).read_text(encoding="utf-8")
    vm_env = (REPOSITORY / "deploy/provision/vmtest/echo-env.sh").read_text(
        encoding="utf-8"
    )

    assert 'INSTALL_PROFILE="${ECHO_INSTALL_PROFILE:-nas}"' in builder
    assert '--profile) INSTALL_PROFILE="$2"' in builder
    assert 'nas)     HDMI_SHELL="${ECHO_HDMI_SHELL:-off}"' in builder
    assert 'desktop) HDMI_SHELL="${ECHO_HDMI_SHELL:-on}"' in builder
    assert 'ECHO_INSTALL_PROFILE="$INSTALL_PROFILE"' in builder
    assert 'ECHO_HDMI_SHELL="$HDMI_SHELL"' in builder
    assert 'cp "$payload/echo-env.sh" /target/etc/echo-os/firstboot.env' in preseed
    assert "in-target chmod 0644 /etc/echo-os/firstboot.env" in preseed
    assert "cp /echo-vmtest/echo-env.sh /target/etc/echo-os/firstboot.env" in vm_preseed
    assert 'ECHO_INSTALL_PROFILE="nas"' in vm_env
    assert 'ECHO_HDMI_SHELL="off"' in vm_env


def test_headless_profile_skips_electron_runtime_and_graphical_shell() -> None:
    provision = (
        REPOSITORY / "deploy/provision/base/provision-lib.sh"
    ).read_text(encoding="utf-8")
    web = provision.split("step_echo_web() {", 1)[1].split("\n}\n", 1)[0]
    shell = provision.split("step_shell() {", 1)[1].split("\n}\n", 1)[0]

    assert 'HDMI_MODE="${ECHO_HDMI_SHELL:-off}"' in web
    assert 'if [ "$HDMI_MODE" = "off" ]' in web
    assert '[ "$HDMI_MODE" = "auto" ] && ! ls /dev/dri/card*' in web
    assert "export ELECTRON_SKIP_BINARY_DOWNLOAD=1" in web
    assert 'HDMI_MODE="${ECHO_HDMI_SHELL:-off}"' in shell
    assert 'if [ "$HDMI_MODE" = "off" ]' in shell
    assert 'elif [ "$HDMI_MODE" = "auto" ] && ! ls /dev/dri/card*' in shell
    assert "echo-shell.service echo-desktop.service" in shell
    assert shell.count("systemctl set-default multi-user.target") == 2
    assert '[ "$DESKTOP_MODE" = "cage" ]' not in shell
