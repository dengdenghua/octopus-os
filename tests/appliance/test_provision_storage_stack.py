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
