"""Fresh-install storage stack must leave a usable ZFS kernel module."""

from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[2]


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

    assert 'kernel_release="$(uname -r)"' in storage
    assert storage.index(headers) < storage.index(zfs_package)
    assert storage.index(zfs_package) < storage.index(autoinstall)
    assert storage.index(autoinstall) < storage.index(load)
    assert storage.index(load) < storage.index(import_service) < storage.index(done)
    assert f"{import_service} || true" not in storage


def test_vmtest_installer_attaches_and_validates_netinst_iso() -> None:
    launcher = (
        REPOSITORY / "deploy/provision/vmtest/tools/launch-vm.ps1"
    ).read_text(encoding="ascii")

    assert "[string]$IsoPath" in launcher
    assert "Test-Path -LiteralPath $IsoPath -PathType Leaf" in launcher
    assert '"-cdrom `"$IsoPath`" "' in launcher
