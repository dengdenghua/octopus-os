from __future__ import annotations

import json
import os
import subprocess
from contextlib import contextmanager
from pathlib import Path

import pytest

from deploy.appliance import nas_data_backup as backup


def completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, "")


def test_read_only_snapshot_uses_deepest_mount(tmp_path: Path) -> None:
    source = tmp_path / "snapshots" / "daily"
    source.mkdir(parents=True)
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(
        "1 0 0:1 / / rw,relatime - ext4 /dev/root rw\n"
        f"2 1 0:2 / {tmp_path / 'snapshots'} ro,nodev - btrfs /dev/mapper/nas ro\n",
        encoding="utf-8",
    )

    record = backup._require_read_only_snapshot(source, mountinfo)

    assert record["mountpoint"] == str(tmp_path / "snapshots")
    assert record["filesystem"] == "btrfs"
    assert len(record["sourceSha256"]) == 64


def test_live_writable_tree_is_not_a_backup_source(tmp_path: Path) -> None:
    source = tmp_path / "nas"
    source.mkdir()
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(
        f"1 0 0:1 / {tmp_path} rw,relatime - ext4 /dev/root rw\n",
        encoding="utf-8",
    )

    with pytest.raises(backup.NasDataBackupError, match="read-only"):
        backup._require_read_only_snapshot(source, mountinfo)


def test_read_only_bind_of_live_ext4_tree_is_not_a_snapshot(tmp_path: Path) -> None:
    live = tmp_path / "live"
    source = tmp_path / "readonly-bind"
    live.mkdir()
    source.mkdir()
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(
        f"1 0 0:1 / {tmp_path} rw,relatime - ext4 /dev/md0 rw\n"
        f"2 1 0:1 /live {source} ro,nodev - ext4 /dev/md0 rw\n",
        encoding="utf-8",
    )

    with pytest.raises(backup.NasDataBackupError, match="independent filesystem snapshot"):
        backup._require_snapshot_independence(source, live, mountinfo)


def test_distinct_read_only_btrfs_subvolume_is_an_independent_snapshot(
    tmp_path: Path,
) -> None:
    live = tmp_path / "live"
    source = tmp_path / "snapshot"
    live.mkdir()
    source.mkdir()
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(
        f"1 0 0:1 / {tmp_path} rw,relatime - ext4 /dev/root rw\n"
        f"2 1 0:2 /@nas {live} rw,nodev - btrfs /dev/mapper/nas rw,subvolid=256\n"
        f"3 1 0:2 /@snapshots/daily {source} ro,nodev - btrfs /dev/mapper/nas ro,subvolid=300\n",
        encoding="utf-8",
    )

    backup._require_snapshot_independence(source, live, mountinfo)


def test_snapshot_selection_is_complete_and_unambiguous() -> None:
    snapshots = [
        {"id": "a" * 64, "path": "/srv/snap-a", "time": "2026-08-26T00:00:00Z"},
        {"id": "b" * 64, "path": "/srv/snap-b", "time": "2026-08-27T00:00:00Z"},
    ]

    assert backup._select_snapshot("latest", snapshots)["id"] == "b" * 64
    assert backup._select_snapshot("a" * 12, snapshots)["id"] == "a" * 64
    with pytest.raises(backup.NasDataBackupError, match="invalid"):
        backup._select_snapshot("not-a-snapshot", snapshots)


def test_restored_tree_rejects_escaping_links_and_special_files(tmp_path: Path) -> None:
    root = tmp_path / "restored"
    root.mkdir()
    (root / "ok.txt").write_text("ok", encoding="utf-8")
    (root / "escape").symlink_to("../../outside")
    with pytest.raises(backup.NasDataBackupError, match="escaping symlink"):
        backup._tree_safe(root)

    (root / "escape").unlink()
    os.mkfifo(root / "pipe")
    with pytest.raises(backup.NasDataBackupError, match="special file"):
        backup._tree_safe(root)


def test_restored_root_requires_only_the_authenticated_path(tmp_path: Path) -> None:
    staging = tmp_path / "stage"
    expected = staging / "srv" / "snapshots" / "daily"
    expected.mkdir(parents=True)
    assert backup._restored_root(staging, Path("/srv/snapshots/daily")) == expected

    (staging / "unexpected").mkdir()
    with pytest.raises(backup.NasDataBackupError, match="unexpected path hierarchy"):
        backup._restored_root(staging, Path("/srv/snapshots/daily"))


@contextmanager
def no_lock():
    yield


@contextmanager
def fake_password(_password: bytes):
    yield 9


def test_restore_full_reads_then_atomically_promotes_empty_nas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    deployment = tmp_path / "deployment"
    deployment.mkdir()
    nas = deployment / "storage"
    nas.mkdir()
    snapshot_id = "c" * 64
    original = Path("/srv/echo-snapshots/nightly")
    commands: list[list[str]] = []

    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if command[-2:] == ["cat", "config"]:
            return completed(json.dumps({"id": "d" * 64}))
        if "snapshots" in command:
            return completed(
                json.dumps(
                    [
                        {
                            "id": snapshot_id,
                            "paths": [str(original)],
                            "tags": [backup.TAG],
                            "time": "2026-08-27T00:00:00Z",
                        }
                    ]
                )
            )
        if "restore" in command:
            target = Path(command[command.index("--target") + 1])
            restored = target.joinpath(*original.parts[1:])
            restored.mkdir(parents=True)
            (restored / "family.mov").write_bytes(b"video")
            (restored / "album").mkdir()
            (restored / "album" / "photo.jpg").write_bytes(b"photo")
            return completed()
        if "check" in command:
            return completed()
        raise AssertionError(command)

    def exchange(left: Path, right: Path) -> None:
        temporary = left.parent / ".test-empty-swap"
        left.rename(temporary)
        right.rename(left)
        temporary.rename(right)

    monkeypatch.setattr(backup, "_context", lambda **_kwargs: (repository, nas))
    monkeypatch.setattr(backup, "_operation_lock", no_lock)
    monkeypatch.setattr(backup, "_password_memfd", fake_password)

    report = backup.restore(
        repository=repository,
        repository_mount=tmp_path,
        deployment_root=deployment,
        appliance_env=None,
        selector="latest",
        confirmation=f"RESTORE ECHO NAS {snapshot_id} TO {nas}",
        password=b"a-secure-test-password",
        runner=runner,
        exchange=exchange,
    )

    assert report["snapshotId"] == snapshot_id
    assert report["atomicPromotion"] is True
    assert report["fullReadVerified"] is True
    assert (nas / "family.mov").read_bytes() == b"video"
    assert (nas / "album" / "photo.jpg").read_bytes() == b"photo"
    assert len([command for command in commands if "check" in command]) == 2
    assert not list(deployment.glob(".storage.echo-nas-restore-*"))


def test_restore_wrong_confirmation_leaves_empty_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    deployment = tmp_path / "deployment"
    deployment.mkdir()
    nas = deployment / "storage"
    nas.mkdir()
    snapshot_id = "e" * 64

    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[-2:] == ["cat", "config"]:
            return completed(json.dumps({"id": "f" * 64}))
        if "snapshots" in command:
            return completed(
                json.dumps(
                    [
                        {
                            "id": snapshot_id,
                            "paths": ["/srv/snapshot"],
                            "tags": [backup.TAG],
                            "time": "2026-08-27T00:00:00Z",
                        }
                    ]
                )
            )
        if "check" in command:
            return completed()
        raise AssertionError("restore must not start with the wrong confirmation")

    monkeypatch.setattr(backup, "_context", lambda **_kwargs: (repository, nas))
    monkeypatch.setattr(backup, "_operation_lock", no_lock)
    monkeypatch.setattr(backup, "_password_memfd", fake_password)

    with pytest.raises(backup.NasDataBackupError, match="confirmation"):
        backup.restore(
            repository=repository,
            repository_mount=tmp_path,
            deployment_root=deployment,
            appliance_env=None,
            selector="latest",
            confirmation="RESTORE SOMETHING ELSE",
            password=b"a-secure-test-password",
            runner=runner,
        )
    assert list(nas.iterdir()) == []
