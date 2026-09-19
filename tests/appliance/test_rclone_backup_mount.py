from __future__ import annotations

import os
from pathlib import Path

import pytest

from deploy.appliance import rclone_backup_mount as mount


def _runtime(tmp_path: Path) -> dict[str, object]:
    owner = os.getuid() if hasattr(os, "getuid") else 0
    mount_root = tmp_path / "mnt"
    target = mount_root / "offsite"
    credential_root = tmp_path / "run" / "credentials" / "unit"
    mount_root.mkdir()
    target.mkdir()
    credential_root.mkdir(parents=True)
    mount_root.chmod(0o755)
    target.chmod(0o755)
    credential = credential_root / "rclone.conf"
    credential.write_text("encrypted-or-decrypted-private-config", encoding="utf-8")
    credential.chmod(0o400)
    rclone = tmp_path / "rclone"
    rclone.write_text("binary", encoding="utf-8")
    rclone.chmod(0o755)
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text("", encoding="utf-8")
    return {
        "environ": {"CREDENTIALS_DIRECTORY": str(credential_root)},
        "rclone_path": rclone,
        "mount_root": mount_root,
        "mountinfo": mountinfo,
        "credential_root_prefix": str(tmp_path / "run" / "credentials") + os.sep,
        "trusted_uid": owner,
    }


def test_command_uses_only_the_systemd_credential_and_managed_mountpoint(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)

    command = mount.command("offsite", **runtime)

    assert command[:3] == [str(runtime["rclone_path"]), "mount", "echo:"]
    assert command[3] == str(runtime["mount_root"] / "offsite")
    assert command[4] == (
        f"--config={runtime['environ']['CREDENTIALS_DIRECTORY']}{os.sep}rclone.conf"
    )
    assert not any("access" in item.lower() or "secret" in item.lower() for item in command)
    assert "--vfs-cache-mode=off" in command


@pytest.mark.parametrize("remote_id", ["../escape", "UPPER", "a/b", "-bad", "a" * 33])
def test_command_rejects_invalid_instance_ids(tmp_path: Path, remote_id: str) -> None:
    with pytest.raises(mount.RemoteMountError):
        mount.command(remote_id, **_runtime(tmp_path))


def test_command_rejects_an_existing_mount(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    target = runtime["mount_root"] / "offsite"
    runtime["mountinfo"].write_text(
        f"1 0 0:1 / {target} rw - fuse.rclone remote: rw\n", encoding="utf-8"
    )

    with pytest.raises(mount.RemoteMountError, match="already active"):
        mount.command("offsite", **runtime)


def test_command_rejects_credential_directory_outside_systemd_runtime(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    runtime["environ"] = {"CREDENTIALS_DIRECTORY": str(tmp_path / "elsewhere")}

    with pytest.raises(mount.RemoteMountError, match="credential directory"):
        mount.command("offsite", **runtime)
