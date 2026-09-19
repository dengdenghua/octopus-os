"""Fresh-install storage stack must leave a usable ZFS kernel module."""

import hashlib
import os
import re
import subprocess
import sys
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

FIRSTBOOT_SYSTEM_PACKAGES = {
    "ca-certificates",
    "curl",
    "zstd",
    "gnupg",
    "lsb-release",
    "openssl",
    "avahi-daemon",
    "libnss-mdns",
    "openssh-server",
    "rclone",
    "fuse3",
    "sudo",
    "python3",
    "python3-venv",
    "python3-pip",
    "git",
    "nginx",
    "zfsutils-linux",
    "zfs-dkms",
    "samba",
    "samba-common-bin",
    "smbclient",
    "wsdd2",
    "samba-vfs-modules",
    "nfs-kernel-server",
    "acl",
    "quota",
    "e2fsprogs",
    "nut-client",
    "nut-server",
    "smartmontools",
    "hdparm",
    "mdadm",
    "lvm2",
    "btrfs-progs",
    "parted",
    "util-linux",
    "restic",
    *HARDWARE_SUPPORT_PACKAGES,
    "docker-ce",
    "docker-ce-cli",
    "containerd.io",
    "docker-buildx-plugin",
    "docker-compose-plugin",
}


def test_storage_step_builds_zfs_for_the_running_kernel_before_marking_done() -> None:
    provision = (REPOSITORY / "deploy/provision/base/provision-lib.sh").read_text(encoding="utf-8")
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
    provision = (REPOSITORY / "deploy/provision/base/provision-lib.sh").read_text(encoding="utf-8")
    helper = provision.split("ensure_swap() {", 1)[1].split("\n}\n", 1)[0]
    storage = provision.split("step_storage() {", 1)[1].split("\n}\n", 1)[0]

    assert "need_mb=2048" in helper
    assert 'of="$sf.partial"' in helper
    assert helper.index('mkswap -q "$sf.partial"') < helper.index('mv "$sf.partial" "$sf"')
    assert storage.index("ensure_swap") < storage.index("apt-get install")


def test_native_data_roots_are_traversable_by_nas_identities() -> None:
    provision = (REPOSITORY / "deploy/provision/base/provision-lib.sh").read_text(encoding="utf-8")

    assert "install -d -o root -g root -m0755 /data /data/nas /data/apps" in provision
    assert "mkdir -p /data/nas /data/apps" not in provision


def test_samba_registry_grants_read_only_metadata_access_before_storage_is_done() -> None:
    provision = (REPOSITORY / "deploy/provision/base/provision-lib.sh").read_text(encoding="utf-8")
    helper = provision.split("configure_native_samba_usershares() {", 1)[1].split("\n}\n", 1)[0]
    storage = provision.split("step_storage() {", 1)[1].split("\n}\n", 1)[0]
    assert "registry=/var/lib/samba/usershares" in helper
    assert '[ "$configured" = "$registry" ]' in helper
    assert '[ ! -L "$component" ]' in helper
    assert 'stat -c %u "$component"' in helper
    assert "8#$mode & 0022" in helper
    assert "8#$mode & 1007" in helper
    assert "getent group users" in helper
    assert 'setfacl --no-mask -m g:users:r-x "$registry"' in helper
    assert "g:users:rwx" not in helper
    assert "chmod" not in helper and "usermod" not in helper
    assert storage.index("configure_native_samba_usershares") < storage.index("done_mark storage")


def test_time_machine_samba_include_is_global_validated_and_loaded_before_storage_done() -> None:
    provision = (REPOSITORY / "deploy/provision/base/provision-lib.sh").read_text(encoding="utf-8")
    helper = provision.split("configure_native_samba_time_machine() {", 1)[1].split("\n}\n", 1)[0]
    storage = provision.split("step_storage() {", 1)[1].split("\n}\n", 1)[0]
    baseline = (REPOSITORY / "deploy/provision/base/echo-os-time-machine.conf").read_text(
        encoding="utf-8"
    )

    assert "vfs objects = catia fruit streams_xattr" in baseline
    assert "fruit:aapl = yes" in baseline
    assert "fruit:nfs_aces = no" in baseline
    assert "samba-vfs-modules" in FIRSTBOOT_SYSTEM_PACKAGES
    assert "samba-vfs-modules" in storage
    assert "include_state=" in helper
    assert 'section == "global"' in helper
    assert "catia fruit streams_xattr" in helper
    assert 'testparm -s "$smb_config"' in helper
    assert "systemctl reload smbd.service" in helper
    assert storage.index("configure_native_samba_time_machine") < storage.index("done_mark storage")


def test_hardware_packages_are_split_out_of_the_core_storage_transaction() -> None:
    provision = (REPOSITORY / "deploy/provision/base/provision-lib.sh").read_text(encoding="utf-8")
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
    provision = (REPOSITORY / "deploy/provision/base/provision-lib.sh").read_text(encoding="utf-8")
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
    provision = (REPOSITORY / "deploy/provision/base/provision-lib.sh").read_text(encoding="utf-8")

    assert "apt_update() {" in provision
    assert "apt_retry apt-get -o APT::Update::Error-Mode=any update" in provision
    assert provision.count("apt_update") == 3
    assert "apt_retry apt-get update" not in provision


def test_apt_transfers_have_a_bounded_timeout_before_outer_retries() -> None:
    provision = (REPOSITORY / "deploy/provision/base/provision-lib.sh").read_text(encoding="utf-8")
    apt_step = provision.split("step_apt() {", 1)[1].split("\n}\n", 1)[0]

    assert "cat >/etc/apt/apt.conf.d/80echo-network <<'EOF'" in apt_step
    assert 'Acquire::Retries "3";' in apt_step
    assert 'Acquire::http::Timeout "30";' in apt_step
    assert 'Acquire::https::Timeout "30";' in apt_step
    assert apt_step.index("80echo-network") < apt_step.index("apt_update")


def test_apt_step_bootstraps_tools_removed_from_strict_release_pkgsel() -> None:
    provision = (REPOSITORY / "deploy/provision/base/provision-lib.sh").read_text(encoding="utf-8")
    apt_step = provision.split("step_apt() {", 1)[1].split("\n}\n", 1)[0]

    install_at = apt_step.index("apt-get install -y --no-install-recommends")
    done_at = apt_step.index("done_mark apt")
    bootstrap = apt_step[install_at:done_at]
    for package in (
        "openssh-server",
        "rclone",
        "fuse3",
        "sudo",
        "git",
        "zstd",
        "openssl",
        "avahi-daemon",
        "libnss-mdns",
        "python3",
        "python3-venv",
        "python3-pip",
        "nginx",
    ):
        assert package in bootstrap


def test_release_installs_native_quota_backup_and_local_discovery_dependencies() -> None:
    provision = (REPOSITORY / "deploy/provision/base/provision-lib.sh").read_text(encoding="utf-8")
    storage = provision.split("step_storage() {", 1)[1].split("\n}\n", 1)[0]
    backup = provision.split("step_backup_recovery() {", 1)[1].split("\n}\n", 1)[0]
    services = provision.split("step_services() {", 1)[1].split("\n}\n", 1)[0]

    assert "quota e2fsprogs" in storage
    assert "apt-get install -y --no-install-recommends" in backup
    assert "restic" in backup
    assert "systemctl enable --now avahi-daemon.service" in services


def test_windows_network_discovery_is_wsd_only_and_ordered_after_samba() -> None:
    provision = (REPOSITORY / "deploy/provision/base/provision-lib.sh").read_text(encoding="utf-8")
    storage = provision.split("step_storage() {", 1)[1].split("\n}\n", 1)[0]
    services = provision.split("step_services() {", 1)[1].split("\n}\n", 1)[0]
    override = (REPOSITORY / "deploy/provision/base/wsdd2-echo.conf").read_text(encoding="utf-8")

    assert "samba samba-common-bin smbclient wsdd2" in storage
    assert "ExecStart=/usr/sbin/wsdd2 -w" in override
    assert " -l" not in override
    assert storage.index("wsdd2.service.d/echo.conf") < storage.index(
        "samba samba-common-bin smbclient wsdd2"
    )
    assert "wsdd2.service.d/echo.conf" in services
    samba = services.index("systemctl enable --now smbd.service")
    discovery = services.index("systemctl enable --now wsdd2.service")
    appliance = services.index("systemctl enable --now echo-appliance.service")
    assert samba < discovery < appliance
    assert "systemctl is-active --quiet smbd.service" in services
    assert "systemctl is-active --quiet wsdd2.service" in services


def test_both_image_paths_keep_the_nas_hardware_support_baseline() -> None:
    provision = (REPOSITORY / "deploy/provision/base/provision-lib.sh").read_text(encoding="utf-8")
    storage = provision.split("step_storage() {", 1)[1].split("\n}\n", 1)[0]
    mkosi = (REPOSITORY / "packaging/image/mkosi.conf").read_text(encoding="utf-8")
    image_gate = (REPOSITORY / "packaging/image/verify-image.sh").read_text(encoding="utf-8")

    for package in HARDWARE_SUPPORT_PACKAGES:
        assert package in storage
        assert f"        {package}\n" in mkosi
        assert f"'^        {package}$'" in image_gate


def test_vmtest_installer_attaches_and_validates_netinst_iso() -> None:
    launcher = (REPOSITORY / "deploy/provision/vmtest/tools/launch-vm.ps1").read_text(
        encoding="ascii"
    )

    assert "[string]$IsoPath" in launcher
    assert "Test-Path -LiteralPath $IsoPath -PathType Leaf" in launcher
    assert '"-cdrom `"$IsoPath`" "' in launcher


def test_iso_builder_embeds_an_exact_single_commit_source_bundle() -> None:
    builder = (REPOSITORY / "deploy/provision/build-iso.sh").read_text(encoding="utf-8")
    assert 'commit-tree "$SOURCE_TREE"' in builder
    assert 'git bundle create "$PAYLOAD/echo-source.bundle" "$SNAPSHOT_REF"' in builder
    assert 'git bundle verify "$PAYLOAD/echo-source.bundle"' in builder
    assert 'git show -s --format=%T "$SNAPSHOT_COMMIT"' in builder
    assert 'ECHO_SOURCE_TREE="$SOURCE_TREE"' in builder


def test_firstboot_prefers_local_source_bundle_over_remote_clone() -> None:
    provision = (REPOSITORY / "deploy/provision/base/provision-lib.sh").read_text(encoding="utf-8")
    source = provision.split("step_echo_src() {", 1)[1].split("\n}\n", 1)[0]

    assert 'if [ -f "$SOURCE_BUNDLE" ]' in source
    assert 'git -C "$OS_DIR" fetch -q "$SOURCE_BUNDLE" "$SOURCE_REF"' in source
    assert "rev-parse 'HEAD^{tree}'" in source
    assert 'git -C "$OS_DIR" remote add origin "$OS_REPO"' in source
    assert source.index('if [ -f "$SOURCE_BUNDLE" ]') < source.index("git_retry()")


def test_firstboot_deduplicates_only_debian_sources_and_keeps_backup() -> None:
    provision = (REPOSITORY / "deploy/provision/base/provision-lib.sh").read_text(encoding="utf-8")
    apt = provision.split("step_apt() {", 1)[1].split("\n}\n", 1)[0]

    assert "/etc/apt/sources.list.echo-installer" in apt
    assert 'awk -v mirror_host="$mirror_host"' in apt
    assert 'host == "deb.debian.org"' in apt
    assert 'host == "security.debian.org"' in apt
    assert 'mv "$sources_tmp" /etc/apt/sources.list' in apt


def test_formal_installer_persists_source_bundle_and_identity() -> None:
    builder = (REPOSITORY / "deploy/provision/build-iso.sh").read_text(encoding="utf-8")
    preseed = (REPOSITORY / "deploy/provision/installer/preseed.cfg").read_text(encoding="utf-8")

    assert 'ECHO_SOURCE_BUNDLE="/opt/echo-os-source.bundle"' in builder
    assert 'ECHO_SOURCE_BUNDLE_REF="$SOURCE_BUNDLE_REF"' in builder
    assert 'ECHO_IMAGE_COMMIT="$SOURCE_COMMIT"' in builder
    assert (
        'cp "$payload/release-manifest.json" /target/etc/echo-os/release-manifest.json' in preseed
    )
    assert 'cp "$payload/echo-source.bundle" /target/opt/echo-os-source.bundle' in preseed


def test_iso_can_embed_a_verified_prebuilt_web_payload_for_headless_nas() -> None:
    builder = (REPOSITORY / "deploy/provision/build-iso.sh").read_text(encoding="utf-8")
    preseed = (REPOSITORY / "deploy/provision/installer/preseed.cfg").read_text(encoding="utf-8")
    provision = (REPOSITORY / "deploy/provision/base/provision-lib.sh").read_text(encoding="utf-8")

    assert '--web-dist) WEB_DIST="$2"' in builder
    web_builder = (REPOSITORY / "deploy/provision/build-web-dist.sh").read_text(encoding="utf-8")

    assert "pnpm install --frozen-lockfile" in web_builder
    assert "pnpm build" in web_builder
    assert "ELECTRON_SKIP_BINARY_DOWNLOAD=1" in web_builder
    assert web_builder.count("status --porcelain --untracked-files=all") == 2
    assert "rev-parse 'HEAD^{tree}'" in web_builder
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
    assert 'cp "$payload/echo-web-dist.tar.gz" /target/opt/echo-web-dist.tar.gz' in preseed
    assert "use_prebuilt_web() {" in provision
    assert 'actual="$(sha256sum "$bundle"' in provision
    assert 'tar --no-same-owner --no-same-permissions -xzf "$bundle"' in provision
    assert '"$staged/.echo-source-tree"' in provision
    assert '!= "$ECHO_SOURCE_TREE"' not in provision
    assert "跳过 NodeSource/npm" in provision
    assert "无需 Node/npm/pnpm 网络" in provision


def test_iso_can_embed_a_verified_python_wheelhouse_for_offline_firstboot() -> None:
    builder = (REPOSITORY / "deploy/provision/build-iso.sh").read_text(encoding="utf-8")
    wheel_builder = (REPOSITORY / "deploy/provision/build-python-wheelhouse.sh").read_text(
        encoding="utf-8"
    )
    preseed = (REPOSITORY / "deploy/provision/installer/preseed.cfg").read_text(encoding="utf-8")
    provision = (REPOSITORY / "deploy/provision/base/provision-lib.sh").read_text(encoding="utf-8")

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
    assert "cpython-313\\ linux\\ x86_64" in wheel_builder
    assert "cpython-313 linux x86_64" in builder
    assert "rev-parse 'HEAD^{tree}'" in wheel_builder
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
        'cp "$payload/echo-python-wheelhouse.tar.gz" /target/opt/echo-python-wheelhouse.tar.gz'
    ) in preseed
    assert "use_prebuilt_python() {" in provision
    assert 'actual="$(sha256sum "$bundle"' in provision
    assert '"$staged/.echo-source-tree"' in provision
    assert '"$staged/.echo-python-runtime"' in provision
    assert "for required_file in .echo-source-tree .echo-python-runtime SHA256SUMS" in provision
    assert "--no-index --no-cache-dir --only-binary=:all:" in provision
    assert '"${echo_wheels[0]}[$extras]" packaging' in provision
    assert "无需 PyPI 网络" in provision


def test_iso_can_embed_a_verified_native_codex_for_offline_firstboot() -> None:
    builder = (REPOSITORY / "deploy/provision/build-iso.sh").read_text(encoding="utf-8")
    codex_builder = (REPOSITORY / "deploy/provision/build-codex-bundle.sh").read_text(
        encoding="utf-8"
    )
    preseed = (REPOSITORY / "deploy/provision/installer/preseed.cfg").read_text(encoding="utf-8")
    provision = (REPOSITORY / "deploy/provision/base/provision-lib.sh").read_text(encoding="utf-8")
    setup = (REPOSITORY / "deploy/provision/base/setup-base.sh").read_text(encoding="utf-8")

    assert "status --porcelain --untracked-files=all" in codex_builder
    assert "pnpm install --frozen-lockfile" in codex_builder
    assert "prepare-codex-linux.cjs" in codex_builder
    assert "rev-parse 'HEAD^{tree}'" in codex_builder
    assert '"$STAGED/.echo-source-tree"' in codex_builder
    assert '"$STAGED/.echo-codex-runtime"' in codex_builder
    assert "sha256sum -c SHA256SUMS" in codex_builder
    for executable in (
        "bin/codex",
        "bin/codex-code-mode-host",
        "codex-path/rg",
        "codex-resources/zsh/bin/zsh",
        "codex-resources/bwrap",
    ):
        assert executable in codex_builder

    assert '--codex-bundle) CODEX_BUNDLE_DIR="$2"' in builder
    assert 'CODEX_BUNDLE_DIR" = "$EXPECTED_CODEX_BUNDLE' in builder
    assert '"$CODEX_BUNDLE_DIR/.echo-source-tree"' in builder
    assert '"$CODEX_BUNDLE_DIR/.echo-codex-runtime"' in builder
    assert 'ECHO_CODEX_BUNDLE="$CODEX_BUNDLE_TARGET"' in builder
    assert 'ECHO_CODEX_BUNDLE_SHA256="$CODEX_BUNDLE_SHA256"' in builder
    assert 'cp "$payload/echo-codex.tar.gz" /target/opt/echo-codex.tar.gz' in preseed
    assert "validate_codex_bundle()" in provision
    assert "install_codex_bundle()" in provision
    assert 'ln -sfn "$target/bin/codex" /usr/local/bin/codex' in provision
    assert 'npm install -g "@openai/codex@$expected_version"' in provision
    assert "无 Codex 载荷且无 npm" in provision
    assert "step_codex()" in provision
    assert "then step_codex" in setup


def test_system_deb_builder_resolves_a_kernel_bound_empty_state_closure() -> None:
    builder = (REPOSITORY / "deploy/provision/build-system-deb-repo.sh").read_text(encoding="utf-8")
    packages = (REPOSITORY / "deploy/provision/system-packages-nas.txt").read_text(encoding="utf-8")

    assert 'Dir::State::status="$WORK/status"' in builder
    assert 'Dir::Cache="$WORK/cache"' in builder
    assert 'Dir::Cache::archives="$STAGED"' in builder
    assert "--download-only -y --no-install-recommends" in builder
    assert "linux-image-*-amd64_*.deb" in builder
    assert "[^']+)'\\$#\\1#p" in builder
    assert 'PACKAGES+=("linux-headers-$KERNEL_RELEASE")' in builder
    assert "Docker 仓库签名密钥指纹不匹配" in builder
    assert "dpkg-scanpackages --multiversion" in builder
    assert ".echo-system-runtime" in builder
    assert "packages.lock" in builder
    assert "SHA256SUMS" in builder
    for package in (
        "zfs-dkms",
        "samba",
        "samba-vfs-modules",
        "nfs-kernel-server",
        "wsdd2",
        "firmware-realtek",
        "docker-ce",
        "docker-compose-plugin",
    ):
        assert f"{package}\n" in packages


def test_system_deb_manifest_exactly_covers_headless_firstboot_transactions() -> None:
    package_file = REPOSITORY / "deploy/provision/system-packages-nas.txt"
    manifest = {
        line.split("#", 1)[0].strip()
        for line in package_file.read_text(encoding="utf-8").splitlines()
        if line.split("#", 1)[0].strip()
    }

    assert manifest == FIRSTBOOT_SYSTEM_PACKAGES


def test_rclone_mount_runtime_includes_a_privilege_safe_fuse_helper() -> None:
    mkosi = (REPOSITORY / "packaging/image/mkosi.conf").read_text(encoding="utf-8")
    postinst = (REPOSITORY / "packaging/image/mkosi.postinst.chroot").read_text(encoding="utf-8")
    verifier = (REPOSITORY / "packaging/image/verify-image.sh").read_text(encoding="utf-8")

    assert "        rclone\n        fuse3\n" in mkosi
    assert "/usr/bin/rclone /usr/bin/fusermount3" in postinst
    assert "usr/bin/fusermount3 \\" in verifier
    assert '"$IMAGE_MOUNT/usr/bin/fusermount3")" == "0:0:4755"' in verifier


def test_encrypted_backup_remote_mount_is_installed_without_a_private_namespace() -> None:
    provision = (REPOSITORY / "deploy/provision/base/provision-lib.sh").read_text(encoding="utf-8")
    unit = (REPOSITORY / "deploy/appliance/systemd/echo-rclone-backup@.service").read_text(
        encoding="utf-8"
    )
    mkosi = (REPOSITORY / "packaging/image/mkosi.conf").read_text(encoding="utf-8")

    assert "echo-rclone-backup@.service" in provision
    assert "/usr/lib/echo-os/rclone-backup-mount" in provision
    assert "/mnt/echo-backup-remotes" in provision
    assert "LoadCredentialEncrypted=rclone.conf:" in unit
    assert "CapabilityBoundingSet=CAP_SYS_ADMIN" in unit
    assert "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6" in unit
    assert "ProtectSystem=" not in unit
    assert "ProtectHome=" not in unit
    assert "PrivateTmp=" not in unit
    assert "rclone-backup-mount" in mkosi
    assert "echo-rclone-backup@.service" in mkosi


def test_iso_can_embed_a_verified_firstboot_system_deb_repository() -> None:
    builder = (REPOSITORY / "deploy/provision/build-iso.sh").read_text(encoding="utf-8")
    preseed = (REPOSITORY / "deploy/provision/installer/preseed.cfg").read_text(encoding="utf-8")
    provision = (REPOSITORY / "deploy/provision/base/provision-lib.sh").read_text(encoding="utf-8")
    setup = (REPOSITORY / "deploy/provision/base/setup-base.sh").read_text(encoding="utf-8")

    assert "--system-deb-repo" in builder
    assert "系统包仓与 netinst ISO 内核不匹配" in builder
    assert 'ECHO_SYSTEM_DEB_BUNDLE="$SYSTEM_DEB_BUNDLE_TARGET"' in builder
    assert 'ECHO_SYSTEM_DEB_BUNDLE_SHA256="$SYSTEM_DEB_BUNDLE_SHA256"' in builder
    assert 'ECHO_SYSTEM_DEB_REPO_SHA256="$SYSTEM_DEB_REPO_SHA256"' in builder
    assert "echo-system-debs.tar.gz" in preseed
    assert "prepare_system_deb_repo" in setup
    assert "validate_system_deb_repo" in provision
    assert "URIs: file:$SYSTEM_DEB_REPO" in provision
    assert 'Dir::Etc::sourceparts "/dev/null";' in provision
    assert "Docker 使用已验证的首启离线系统包仓" in provision
    assert "已声明的首启离线系统包仓不存在" in provision
    assert "首启离线系统包仓摘要不匹配" in provision
    assert "ECHO_SYSTEM_DEB_REPO_SHA256" in provision


def test_strict_nas_release_requires_every_offline_payload_and_a_pinned_base_iso() -> None:
    builder = (REPOSITORY / "deploy/provision/build-iso.sh").read_text(encoding="utf-8")
    renderer = (REPOSITORY / "deploy/provision/render-release-preseed.sh").read_text(
        encoding="utf-8"
    )
    packages = (REPOSITORY / "deploy/provision/system-packages-nas.txt").read_text(encoding="utf-8")

    assert "--release) RELEASE_MODE=1" in builder
    assert "严格 release 模式当前只支持 nas profile" in builder
    assert "严格 release 模式要求 ECHO_HDMI_SHELL=off" in builder
    for option in (
        "--iso-sha256",
        "--web-dist",
        "--python-wheelhouse",
        "--codex-bundle",
        "--system-deb-repo",
    ):
        assert f"严格 release 模式缺少 {option}" in builder or (
            option == "--iso-sha256" and "必须用 --iso-sha256" in builder
        )
    assert "Debian 基础 ISO SHA-256 不匹配" in builder
    assert 'sh "$SCRIPT_DIR/render-release-preseed.sh"' in builder
    assert "apt-setup/use_mirror boolean false" in renderer
    assert 'print "tasksel tasksel/first multiselect"' in renderer
    assert 'print "d-i pkgsel/include string"' in renderer
    assert 'ECHO_RELEASE_OFFLINE="$RELEASE_MODE"' in builder
    assert 'ECHO_SOURCE_BUNDLE_SHA256="$SOURCE_BUNDLE_SHA256"' in builder
    assert 'release-manifest.py" create' in builder
    assert "printf '%s  %s\\n'" in builder
    assert '"$OUT_ISO.sha256"' in builder
    for package in (
        "openssh-server",
        "rclone",
        "fuse3",
        "sudo",
        "python3",
        "python3-venv",
        "python3-pip",
        "git",
        "nginx",
    ):
        assert f"{package}\n" in packages


def test_strict_release_firstboot_cannot_fall_back_when_contract_fields_are_missing() -> None:
    setup = (REPOSITORY / "deploy/provision/base/setup-base.sh").read_text(encoding="utf-8")

    assert "require_offline_release_contract()" in setup
    assert '[ "${ECHO_RELEASE_OFFLINE:-0}" = 1 ] || return 0' in setup
    for field in (
        "ECHO_SOURCE_BUNDLE",
        "ECHO_SOURCE_BUNDLE_SHA256",
        "ECHO_SOURCE_TREE",
        "ECHO_IMAGE_COMMIT",
        "ECHO_WEB_BUNDLE_SHA256",
        "ECHO_PYTHON_BUNDLE_SHA256",
        "ECHO_CODEX_BUNDLE_SHA256",
        "ECHO_SYSTEM_DEB_BUNDLE_SHA256",
        "ECHO_SYSTEM_DEB_REPO_SHA256",
    ):
        assert field in setup
    assert "for name in ECHO_SOURCE_TREE ECHO_IMAGE_COMMIT" in setup
    assert 'case "${#value}" in 40|64)' in setup
    assert "require_offline_release_contract || exit 1" in setup
    assert setup.index("require_offline_release_contract || exit 1") < setup.index(
        "prepare_system_deb_repo"
    )
    assert '[ "$system_deb_status" -eq 0 ]' in setup


def test_strict_release_network_fallbacks_are_guarded_at_every_call_site() -> None:
    provision = (REPOSITORY / "deploy/provision/base/provision-lib.sh").read_text(encoding="utf-8")

    assert "严格 release 缺少已验证的离线系统包仓" in provision
    assert "严格 release 禁止 Docker 回退公网软件源" in provision
    assert "严格 release 禁止回退 NodeSource/npm" in provision
    assert "严格 release 源码 bundle 不存在或不是常规文件" in provision
    assert "严格 release 源码 bundle 摘要不匹配" in provision
    assert "严格 release 禁止回退 PyPI/uv" in provision
    assert "严格 release 禁止回退 npm/pnpm 构建" in provision
    assert "严格 release 禁止从 npm 安装 Codex" in provision


def test_release_manifest_binds_every_payload_and_rejects_tampering(tmp_path: Path) -> None:
    manifest_tool = REPOSITORY / "deploy/provision/release-manifest.py"
    payload_names = {
        "source-bundle": "echo-source.bundle",
        "web-bundle": "echo-web-dist.tar.gz",
        "python-bundle": "echo-python-wheelhouse.tar.gz",
        "codex-bundle": "echo-codex.tar.gz",
        "system-deb-bundle": "echo-system-debs.tar.gz",
    }
    digests: dict[str, str] = {}
    for label, name in payload_names.items():
        payload = tmp_path / name
        payload.write_bytes(f"{label}\n".encode())
        digests[label] = hashlib.sha256(payload.read_bytes()).hexdigest()
    source_commit = "a" * 40
    source_tree = "b" * 40
    base_iso = "c" * 64
    repository = "d" * 64
    codex_version = "0.149.0"
    create = [
        sys.executable,
        str(manifest_tool),
        "create",
        "--output",
        str(tmp_path / "release-manifest.json"),
        "--source-commit",
        source_commit,
        "--source-tree",
        source_tree,
        "--base-iso-sha256",
        base_iso,
        "--system-deb-repo-sha256",
        repository,
        "--codex-version",
        codex_version,
    ]
    for label, digest in digests.items():
        create.extend((f"--{label}-sha256", digest))
    child_environment = os.environ.copy()
    child_environment["PYTHONIOENCODING"] = "utf-8"
    subprocess.run(
        create,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=child_environment,
    )
    (tmp_path / "preseed.cfg").write_text(
        "d-i apt-setup/use_mirror boolean false\n"
        "d-i clock-setup/ntp boolean false\n"
        "tasksel tasksel/first multiselect\n"
        "d-i pkgsel/include string\n",
        encoding="utf-8",
    )
    environment = {
        "ECHO_SOURCE_BUNDLE_SHA256": digests["source-bundle"],
        "ECHO_SOURCE_TREE": source_tree,
        "ECHO_IMAGE_COMMIT": source_commit,
        "ECHO_WEB_BUNDLE_SHA256": digests["web-bundle"],
        "ECHO_PYTHON_BUNDLE_SHA256": digests["python-bundle"],
        "ECHO_CODEX_BUNDLE_SHA256": digests["codex-bundle"],
        "ECHO_SYSTEM_DEB_BUNDLE_SHA256": digests["system-deb-bundle"],
        "ECHO_SYSTEM_DEB_REPO_SHA256": repository,
        "ECHO_PACKAGED_CODEX_VERSION": codex_version,
        "ECHO_RELEASE_OFFLINE": "1",
        "ECHO_INSTALL_PROFILE": "nas",
        "ECHO_HDMI_SHELL": "off",
    }
    (tmp_path / "echo-env.sh").write_text(
        "".join(f'{name}="{value}"\n' for name, value in environment.items()),
        encoding="utf-8",
    )
    verify = [
        sys.executable,
        str(manifest_tool),
        "verify-directory",
        "--root",
        str(tmp_path),
    ]
    verified = subprocess.run(
        verify,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=child_environment,
    )
    assert "release-manifest=verified" in verified.stdout

    (tmp_path / payload_names["web-bundle"]).write_bytes(b"tampered\n")
    rejected = subprocess.run(
        verify,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=child_environment,
    )
    assert rejected.returncode != 0
    assert "web payload SHA-256" in rejected.stderr


def test_release_wrapper_builds_all_payloads_before_invoking_strict_iso_mode() -> None:
    wrapper = (REPOSITORY / "deploy/provision/build-release-iso.sh").read_text(encoding="utf-8")

    assert "--iso-sha256" in wrapper
    assert "ACTUAL_INPUT_ISO_SHA256" in wrapper
    assert "Debian 基础 ISO SHA-256 不匹配" in wrapper
    assert "正式 release 只能从干净 Git 工作树构建" in wrapper
    assert "Debian 13 (trixie)" in wrapper
    assert 'PYTHON_RUNTIME" = "cpython-313 linux x86_64"' in wrapper
    ordered_commands = (
        "build-web-dist.sh",
        "build-python-wheelhouse.sh",
        "build-codex-bundle.sh",
        "build-system-deb-repo.sh",
        '"$SCRIPT_DIR/build-iso.sh"',
        "verify-release-iso.sh",
    )
    offsets = [wrapper.index(command) for command in ordered_commands]
    assert offsets == sorted(offsets)
    assert "--release" in wrapper
    assert "--profile nas" in wrapper
    assert '"$OUT_ISO.sha256"' in wrapper
    verifier = (REPOSITORY / "deploy/provision/verify-release-iso.sh").read_text(encoding="utf-8")
    assert '-extract / "$WORK/iso-root"' in verifier
    assert "md5sum --quiet -c md5sum.txt" in verifier
    assert 'release-manifest.py" verify-directory' in verifier
    assert 'git -C "$WORK/source-check" bundle verify' in verifier
    assert "bundle list-heads" in verifier
    assert "BUNDLE_TREE" in verifier
    assert "MANIFEST_TREE" in verifier
    assert "El Torito boot img.*BIOS.*y" in verifier
    assert "El Torito boot img.*UEFI.*y" in verifier


def test_native_appliance_service_inherits_the_image_codex_version() -> None:
    service = (REPOSITORY / "deploy/provision/base/echo-appliance.service").read_text(
        encoding="utf-8"
    )

    assert "EnvironmentFile=-/etc/echo-os/firstboot.env" in service
    assert "ExecStart=/opt/echo-os/.venv/bin/echo-agent serve" in service
    assert "Environment=ECHO_REQUIRED_APP_EXTENSIONS=1" in service
    unit_section = service.split("[Service]", 1)[0]
    assert "StartLimitIntervalSec=300s" in unit_section
    assert "StartLimitBurst=5" in unit_section
    assert "OnFailure=echo-appliance-deadman.service" in unit_section


def test_appliance_deadman_is_installed_as_a_hardened_periodic_fallback() -> None:
    provision = (REPOSITORY / "deploy/provision/base/provision-lib.sh").read_text(encoding="utf-8")
    service = (REPOSITORY / "deploy/appliance/systemd/echo-appliance-deadman.service").read_text(
        encoding="utf-8"
    )
    timer = (REPOSITORY / "deploy/appliance/systemd/echo-appliance-deadman.timer").read_text(
        encoding="utf-8"
    )

    assert "echo-appliance-deadman.service" in provision
    assert "echo-appliance-deadman.timer" in provision
    assert "systemctl enable --now echo-appliance-deadman.timer" in provision
    assert "Type=oneshot" in service
    assert "NoNewPrivileges=true" in service
    assert "ProtectSystem=strict" in service
    assert "PrivateDevices=true" in service
    assert "CapabilityBoundingSet=" in service
    assert "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6" in service
    assert "ReadWritePaths=/data" in service
    assert "appliance.nas_alert_deadman --state-dir /data" in service
    assert "OnBootSec=5min" in timer
    assert "OnUnitInactiveSec=5min" in timer
    assert "Persistent=true" in timer


def test_firstboot_does_not_kill_a_slow_offline_install() -> None:
    service = (REPOSITORY / "deploy/provision/echo-firstboot.service").read_text(encoding="utf-8")

    assert "TimeoutStartSec=infinity" in service
    assert "TimeoutStartSec=60min" not in service


def test_iso_checksum_refresh_does_not_follow_the_debian_directory_loop() -> None:
    builder = (REPOSITORY / "deploy/provision/build-iso.sh").read_text(encoding="utf-8")

    assert "find . -type f ! -name md5sum.txt" in builder
    assert "! -path './isolinux/isolinux.bin'" in builder
    assert "! -path './isolinux/boot.cat'" in builder
    assert "find . -follow" not in builder


def test_iso_builder_injects_preseed_before_installer_separator() -> None:
    builder = (REPOSITORY / "deploy/provision/build-iso.sh").read_text(encoding="utf-8")

    assert "inject_installer_args append" in builder
    assert "inject_installer_args linux" in builder
    assert "auto=true priority=critical locale=zh_CN.UTF-8" in builder
    assert "keyboard-configuration/xkb-keymap=us" in builder
    assert "s|[[:space:]]---| $APPEND_ARGS ---|" in builder


def test_iso_boot_menus_default_to_the_text_echo_installer() -> None:
    builder = (REPOSITORY / "deploy/provision/build-iso.sh").read_text(encoding="utf-8")

    assert "ontimeout install" in builder
    assert "timeout 50" in builder
    assert "Echo OS installer (BIOS mode)" in builder
    assert "menu label ^Echo OS installer" in builder
    assert "set timeout=5\\nset default=1" in builder
    assert "sed -i -E '/^[[:space:]]*ontimeout[[:space:]]/d'" in builder


def test_iso_builder_appends_preseed_and_tui_to_every_installer_initrd() -> None:
    builder = (REPOSITORY / "deploy/provision/build-iso.sh").read_text(encoding="utf-8")

    assert "preseed/file=/preseed.cfg" in builder
    assert "preseed/file=/cdrom/echo-os/preseed.cfg" not in builder
    assert "s#/cdrom/echo-os/echo-install#/bin/sh /echo-install#" in builder
    assert "find . -print0 | cpio --null -o --format=newc" in builder
    assert 'find "$WORK/iso/install.amd" -type f -name initrd.gz -print0' in builder
    assert 'cat "$INITRD_SEGMENT" >>"$initrd"' in builder


def test_installer_tui_uses_posix_arguments_and_normalizes_device_paths() -> None:
    installer = (REPOSITORY / "deploy/provision/installer/echo-install").read_text(encoding="utf-8")
    assert installer.startswith("#!/bin/sh\n")
    assert "menu_items=(" not in installer
    assert "menu_items[@]" not in installer
    assert 'set -- "$@" "$dev"' in installer
    assert "dev=${dev#/dev/}" in installer
    assert 'if [ -n "$devices" ]; then' in installer
    assert 'log "disks=$DISKS"' in installer


def test_installer_disk_admission_covers_the_preseed_recipe_before_confirmation() -> None:
    installer = (REPOSITORY / "deploy/provision/installer/echo-install").read_text(encoding="utf-8")
    preseed = (REPOSITORY / "deploy/provision/installer/preseed.cfg").read_text(encoding="utf-8")
    minimum = re.search(r"^MIN_SYSTEM_DISK_KB=(\d+)$", installer, re.MULTILINE)
    assert minimum is not None
    partitions = re.findall(
        r"^\s+(\d+)\s+\d+\s+-?\d+\s+(?:fat32|linux-swap|ext4)\s+\\$",
        preseed,
        re.MULTILINE,
    )
    assert len(partitions) == 3, "Re-evaluate the admission floor when the layout changes"
    # Interpret recipe MB conservatively as MiB and reserve 2 MiB for alignment.
    assert int(minimum.group(1)) >= (sum(map(int, partitions)) + 2) * 1024
    assert installer.count("\nvalidate_selected_disk\n") == 2
    assert installer.index("\nvalidate_selected_disk\n") < installer.index("# 确认(整盘写入")
    assert installer.rindex("\nvalidate_selected_disk\n") < installer.index('cat >>"$ANSWERS"')


def test_installer_uses_the_native_debconf_frontend_without_whiptail() -> None:
    builder = (REPOSITORY / "deploy/provision/build-iso.sh").read_text(encoding="utf-8")
    installer = (REPOSITORY / "deploy/provision/installer/echo-install").read_text(encoding="utf-8")
    preseed = (REPOSITORY / "deploy/provision/installer/preseed.cfg").read_text(encoding="utf-8")
    templates = (REPOSITORY / "deploy/provision/installer/echo-install.templates").read_text(
        encoding="utf-8"
    )

    assert "debconf-loadtemplate echo-os /echo-install.templates" in installer
    assert "db_input critical echo-os/disk" in installer
    assert "db_input critical echo-os/password" in installer
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
    preseed = (REPOSITORY / "deploy/provision/installer/preseed.cfg").read_text(encoding="utf-8")
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
    provision = (REPOSITORY / "deploy/provision/base/provision-lib.sh").read_text(encoding="utf-8")
    recovery = provision.split("step_backup_recovery() {", 1)[1].split("\n}\n", 1)[0]

    assert "systemctl disable --now echo-recovery.service" in recovery
    assert "systemctl reset-failed echo-recovery.service" in recovery
    assert "systemctl enable echo-recovery.service" not in recovery
    assert "systemctl enable echo-user-backup.service" in recovery
    assert "systemctl enable echo-restore-transaction-health.service" in recovery


def test_iso_profiles_default_to_headless_nas_and_persist_firstboot_config() -> None:
    builder = (REPOSITORY / "deploy/provision/build-iso.sh").read_text(encoding="utf-8")
    preseed = (REPOSITORY / "deploy/provision/installer/preseed.cfg").read_text(encoding="utf-8")
    vm_preseed = (REPOSITORY / "deploy/provision/vmtest/preseed-vmtest.cfg").read_text(
        encoding="utf-8"
    )
    vm_env = (REPOSITORY / "deploy/provision/vmtest/echo-env.sh").read_text(encoding="utf-8")

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
    provision = (REPOSITORY / "deploy/provision/base/provision-lib.sh").read_text(encoding="utf-8")
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
