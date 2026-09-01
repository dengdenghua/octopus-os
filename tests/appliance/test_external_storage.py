"""Operations output must use a distinct, active, non-volatile filesystem."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from deploy.appliance.external_storage import (
    ExternalStorageError,
    verify_external_storage,
)


def _mount_record(mountpoint: Path, filesystem: str = "ext4") -> str:
    encoded = str(mountpoint).replace(" ", r"\040")
    return f"36 25 8:17 / {encoded} rw,relatime - {filesystem} /dev/external rw\n"


def _layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("NAS_STORAGE", raising=False)
    deployment = tmp_path / "deployment"
    state = deployment / "data"
    nas = deployment / "storage"
    mountpoint = tmp_path / "external media"
    destination = mountpoint / "echo"
    for directory in (state, nas, destination):
        directory.mkdir(parents=True)
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(_mount_record(mountpoint))
    devices = {
        deployment.resolve(): 10,
        state.resolve(): 10,
        nas.resolve(): 11,
        mountpoint.resolve(): 20,
        destination.resolve(): 20,
    }
    return deployment, mountpoint, destination, mountinfo, devices


def _verify(
    deployment: Path,
    mountpoint: Path,
    destination: Path,
    mountinfo: Path,
    devices: dict[Path, int],
    *,
    appliance_env: Path | None = None,
):
    return verify_external_storage(
        destination=destination,
        mountpoint=mountpoint,
        deployment_root=deployment,
        appliance_env=appliance_env,
        mountinfo=mountinfo,
        device_reader=lambda path: devices[path.resolve()],
    )


@pytest.mark.parametrize("filesystem", ["ext4", "nfs4", "cifs"])
def test_accepts_active_distinct_external_or_remote_filesystem(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    filesystem: str,
) -> None:
    deployment, mountpoint, destination, mountinfo, devices = _layout(tmp_path, monkeypatch)
    mountinfo.write_text(_mount_record(mountpoint, filesystem))

    result = _verify(deployment, mountpoint, destination, mountinfo, devices)

    assert result == {
        "destination": str(destination.resolve()),
        "mountpoint": str(mountpoint.resolve()),
        "filesystem": filesystem,
        "source": "/dev/external",
        "deviceId": "20",
    }


def test_rejects_unmounted_destination_fallback(tmp_path: Path, monkeypatch) -> None:
    deployment, mountpoint, destination, mountinfo, devices = _layout(tmp_path, monkeypatch)
    mountinfo.write_text("24 1 8:1 / / rw - ext4 /dev/root rw\n")

    with pytest.raises(ExternalStorageError, match="not currently mounted"):
        _verify(deployment, mountpoint, destination, mountinfo, devices)


@pytest.mark.parametrize("filesystem", ["tmpfs", "overlay", "proc", "squashfs"])
def test_rejects_volatile_or_system_filesystems(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    filesystem: str,
) -> None:
    deployment, mountpoint, destination, mountinfo, devices = _layout(tmp_path, monkeypatch)
    mountinfo.write_text(_mount_record(mountpoint, filesystem))

    with pytest.raises(ExternalStorageError, match="volatile or system"):
        _verify(deployment, mountpoint, destination, mountinfo, devices)


@pytest.mark.parametrize(
    ("protected", "message"),
    (("deployment", "deployment"), ("state", "device state"), ("nas", "NAS data")),
)
def test_rejects_filesystem_shared_with_protected_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    protected: str,
    message: str,
) -> None:
    deployment, mountpoint, destination, mountinfo, devices = _layout(tmp_path, monkeypatch)
    paths = {
        "deployment": deployment,
        "state": deployment / "data",
        "nas": deployment / "storage",
    }
    devices[paths[protected].resolve()] = devices[destination.resolve()]

    with pytest.raises(ExternalStorageError, match=message):
        _verify(deployment, mountpoint, destination, mountinfo, devices)


def test_rejects_nested_filesystem_drift_below_mountpoint(tmp_path: Path, monkeypatch) -> None:
    deployment, mountpoint, destination, mountinfo, devices = _layout(tmp_path, monkeypatch)
    devices[destination.resolve()] = 21

    with pytest.raises(ExternalStorageError, match="changed filesystem"):
        _verify(deployment, mountpoint, destination, mountinfo, devices)


def test_rejects_destination_outside_mount_and_symlink_components(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployment, mountpoint, destination, mountinfo, devices = _layout(tmp_path, monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir()
    devices[outside.resolve()] = 20
    with pytest.raises(ExternalStorageError, match="outside its declared"):
        _verify(deployment, mountpoint, outside, mountinfo, devices)

    link = tmp_path / "mount-link"
    os.symlink(mountpoint, link)
    with pytest.raises(ExternalStorageError, match="symbolic link"):
        _verify(deployment, link, link / destination.name, mountinfo, devices)


def test_appliance_env_nas_storage_is_resolved_and_strict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployment, mountpoint, destination, mountinfo, devices = _layout(tmp_path, monkeypatch)
    configured_nas = deployment / "configured nas"
    configured_nas.mkdir()
    devices[configured_nas.resolve()] = 12
    appliance_env = deployment / "appliance.env"
    appliance_env.write_text('NAS_STORAGE="configured nas"\n')
    assert (
        _verify(
            deployment,
            mountpoint,
            destination,
            mountinfo,
            devices,
            appliance_env=appliance_env,
        )["filesystem"]
        == "ext4"
    )

    appliance_env.write_text("NAS_STORAGE=storage\nNAS_STORAGE=other\n")
    with pytest.raises(ExternalStorageError, match="more than once"):
        _verify(
            deployment,
            mountpoint,
            destination,
            mountinfo,
            devices,
            appliance_env=appliance_env,
        )

    appliance_env.write_text("NAS_STORAGE=$HOME/storage\n")
    with pytest.raises(ExternalStorageError, match="unsafe NAS_STORAGE"):
        _verify(
            deployment,
            mountpoint,
            destination,
            mountinfo,
            devices,
            appliance_env=appliance_env,
        )
