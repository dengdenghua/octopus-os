from __future__ import annotations

import json
import os
import subprocess
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from deploy.appliance import btrfs_snapshot_functional_lab as lab


def test_loopback_origin_accepts_only_an_exact_local_origin() -> None:
    assert lab._loopback_origin("http://127.0.0.1:8000/") == "http://127.0.0.1:8000"
    assert lab._loopback_origin("https://[::1]:8443") == "https://[::1]:8443"

    for value in (
        "http://192.168.1.2:8000",
        "http://admin@127.0.0.1:8000",
        "http://127.0.0.1:8000/api",
        "http://127.0.0.1",
    ):
        with pytest.raises(lab.BtrfsSnapshotFunctionalLabError, match="loopback origin"):
            lab._loopback_origin(value)


def test_safe_lab_paths_are_fixed_to_private_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_root = tmp_path / "images"
    mount_root = tmp_path / "mounts"
    image_root.mkdir()
    mount_root.mkdir()
    monkeypatch.setattr(lab, "IMAGE_ROOT", image_root)
    monkeypatch.setattr(lab, "MOUNT_ROOT", mount_root)

    image, mountpoint = lab._safe_lab_paths("012345abcdef")

    assert image.parent == image_root
    assert mountpoint.parent == mount_root
    with pytest.raises(lab.BtrfsSnapshotFunctionalLabError, match="run id"):
        lab._safe_lab_paths("../../escape")
    image.touch()
    with pytest.raises(lab.BtrfsSnapshotFunctionalLabError, match="already exist"):
        lab._safe_lab_paths("012345abcdef")


def test_state_files_restore_exact_bytes_mode_and_absence(tmp_path: Path) -> None:
    existing = tmp_path / "existing.json"
    absent = tmp_path / "absent.json"
    existing.write_bytes(b"original\n")
    os.chmod(existing, 0o640)
    saved_existing = lab._save_file(existing)
    saved_absent = lab._save_file(absent)

    existing.write_bytes(b"changed\n")
    os.chmod(existing, 0o600)
    absent.write_bytes(b"created\n")
    lab._atomic_restore(saved_existing)
    lab._atomic_restore(saved_absent)

    assert existing.read_bytes() == b"original\n"
    if os.name == "posix":
        assert existing.stat().st_mode & 0o777 == 0o640
    assert not absent.exists()


def test_state_file_refuses_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_text("data", encoding="utf-8")
    link = tmp_path / "link"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks are not available")
    with pytest.raises(lab.BtrfsSnapshotFunctionalLabError, match="unsafe"):
        lab._save_file(link)


def test_test_password_updates_both_legacy_and_admin_hashes(tmp_path: Path) -> None:
    auth_path = tmp_path / "appliance-auth.json"
    auth_path.write_text(
        '{"accounts":{"admin":{"password_hash":"old"}},"password_hash":"old"}\n',
        encoding="utf-8",
    )
    saved = lab._save_file(auth_path)

    lab._set_test_password(saved, "temporary", hasher=lambda value: f"hashed:{value}")

    payload = json.loads(auth_path.read_text(encoding="utf-8"))
    assert payload["password_hash"] == "hashed:temporary"
    assert payload["accounts"]["admin"]["password_hash"] == "hashed:temporary"
    lab._atomic_restore(saved)
    assert auth_path.read_bytes() == saved.payload


def test_admin_token_is_short_lived_and_bound_to_the_existing_secret(tmp_path: Path) -> None:
    auth_path = tmp_path / "appliance-auth.json"
    secret = "S3cure-signing-secret-with-enough-entropy!"
    auth_path.write_text(
        json.dumps({"jwt_secret": secret, "accounts": {"admin": {"active": True}}}),
        encoding="utf-8",
    )
    saved = lab._save_file(auth_path)
    observed: dict[str, Any] = {}

    def encode(claims: Mapping[str, Any], key: str) -> str:
        observed.update({"claims": dict(claims), "secret": key})
        return "signed-token"

    assert lab._admin_token(saved, timestamp=1000, encoder=encode) == "signed-token"
    assert observed["secret"] == secret
    assert observed["claims"]["sub"] == "local:admin"
    assert observed["claims"]["iat"] == 1000
    assert observed["claims"]["exp"] == 1600


def test_target_selection_uses_opaque_uuid_not_host_path() -> None:
    reference = str(uuid.uuid4())
    mountpoint = Path("/mnt/echo-btrfs-snapshot-lab-abc")
    inventory = {
        "sharedFolderTargets": [
            {
                "mountPointRef": reference,
                "label": mountpoint.name,
                "type": "btrfs",
                "readOnly": False,
            }
        ]
    }

    assert lab._target_for_mount(inventory, mountpoint) == reference
    lab._assert_private_response(inventory, Path("/var/tmp/private.img"), mountpoint)

    leaking = {**inventory, "debug": str(mountpoint)}
    with pytest.raises(lab.BtrfsSnapshotFunctionalLabError, match="private host path"):
        lab._assert_private_response(leaking, Path("/var/tmp/private.img"), mountpoint)


def test_snapshot_inventory_requires_read_only_identity_and_lock_state() -> None:
    snapshot_id = str(uuid.uuid4())
    subvolume_uuid = str(uuid.uuid4())
    inventory = {
        "snapshots": [
            {
                "snapshotId": snapshot_id,
                "subvolumeUuid": subvolume_uuid,
                "name": "manual_test",
                "readOnly": True,
                "locked": True,
            }
        ]
    }

    assert lab._snapshot(inventory, "manual_test", locked=True)["snapshotId"] == snapshot_id
    with pytest.raises(lab.BtrfsSnapshotFunctionalLabError, match="flags"):
        lab._snapshot(inventory, "manual_test", locked=False)


def test_plan_approval_apply_binds_one_shot_token() -> None:
    plan_id = "a" * 64
    calls: list[tuple[str, str, Mapping[str, Any] | None, Mapping[str, str] | None]] = []

    def caller(
        _base_url: str,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None,
        _token: str | None,
        headers: Mapping[str, str] | None,
    ) -> tuple[int, Mapping[str, Any]]:
        calls.append((method, path, payload, headers))
        if path == "/plan":
            return 200, {"planId": plan_id, "requiresApproval": True}
        if path == "/api/appliance/approvals":
            return 200, {
                "approvalToken": "one-shot",
                "action": "test.action",
                "target": plan_id,
            }
        return 200, {"planId": plan_id, "verified": True}

    plan, result = lab._planned_apply(
        caller,
        "http://127.0.0.1:8000",
        "bearer",
        "password",
        desired={"schema": "test"},
        plan_path="/plan",
        apply_path="/apply",
        action="test.action",
    )

    assert plan["planId"] == result["planId"] == plan_id
    assert calls[1][2] == {
        "action": "test.action",
        "target": plan_id,
        "password": "password",
    }
    assert calls[2][3] == {"X-Echo-Approval": "one-shot"}


def test_request_reports_only_bounded_server_detail() -> None:
    def caller(*_args: Any) -> tuple[int, Mapping[str, Any]]:
        return 422, {"detail": "locked snapshots must be unlocked"}

    with pytest.raises(lab.BtrfsSnapshotFunctionalLabError, match="HTTP 422.*locked"):
        lab._request(
            caller,
            "http://127.0.0.1:8000",
            "POST",
            "/plan",
            expected=200,
        )


def test_cleanup_restores_state_and_removes_exact_lab_artifacts(tmp_path: Path) -> None:
    image = tmp_path / "lab.img"
    mountpoint = tmp_path / "mount"
    image.touch()
    mountpoint.mkdir()
    source = mountpoint / "source"
    recovered = mountpoint / "recovered"
    snapshot_root = mountpoint / ".echo-snapshots" / "share-ref"
    snapshot = snapshot_root / "manual"
    for directory in (source, recovered, snapshot_root, snapshot):
        directory.mkdir(exist_ok=True, parents=True)
    state = tmp_path / "state.json"
    state.write_bytes(b"before\n")
    saved = lab._save_file(state)
    state.write_bytes(b"after\n")

    def runner(command: list[str] | tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        if command[0:3] == ["btrfs", "subvolume", "delete"]:
            Path(command[3]).rmdir()
        return subprocess.CompletedProcess(command, 0, "", "")

    lab._cleanup(
        image=image,
        mountpoint=mountpoint,
        share_ref="share-ref",
        source_name="source",
        recovered_name="recovered",
        snapshot_names=["manual"],
        mounted=True,
        saved_files=[saved],
        runner=runner,
    )

    assert state.read_bytes() == b"before\n"
    assert not image.exists()
    assert not mountpoint.exists()


def test_preflight_requires_linux_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lab.sys, "platform", "win32")
    monkeypatch.setattr(lab.os, "geteuid", lambda: 0, raising=False)
    with pytest.raises(lab.BtrfsSnapshotFunctionalLabError, match="Linux root"):
        lab._preflight(lambda command: subprocess.CompletedProcess(command, 0, "", ""))
