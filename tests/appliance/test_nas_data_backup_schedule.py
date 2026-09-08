from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from deploy.appliance import (
    nas_data_backup as backup,
)
from deploy.appliance import (
    nas_data_backup_schedule_runner as schedule,
)

REF1 = "11111111-2222-4333-8444-555555555555"
REF2 = "22222222-3333-4444-8555-666666666666"
SNAPSHOT_NAME = "auto-20260908t020000z"
REPOSITORY = Path(__file__).resolve().parents[2]


def _config(*, enabled: bool = True) -> tuple[bool, dict[str, object]]:
    return True, {
        "schema": schedule.CONFIG_SCHEMA,
        "enabled": enabled,
        "repository": "/mnt/off-device/echo-nas-data" if enabled else None,
        "repositoryMount": "/mnt/off-device" if enabled else None,
    }


def _policy() -> tuple[bool, dict[str, object]]:
    return True, {
        "schemaVersion": 2,
        "shares": [
            {"sharedFolderRef": REF2, "retention": {"mode": "latest", "value": 8}},
            {"sharedFolderRef": REF1, "retention": {"mode": "latest", "value": 8}},
        ],
    }


def _inventory(reference: str, name: str = SNAPSHOT_NAME) -> dict[str, object]:
    return {
        "sharedFolderRef": reference,
        "snapshots": [{"name": name, "kind": "automatic"}],
    }


def _member(reference: str, name: str) -> dict[str, str]:
    filesystem = REF1 if reference == REF1 else REF2
    leaf = "photos" if reference == REF1 else "videos"
    return {
        "sharedFolderRef": reference,
        "filesystemUuid": filesystem,
        "snapshotId": str(
            backup.uuid.uuid5(backup._BTRFS_SNAPSHOT_NAMESPACE, f"{reference}:{name}")
        ),
        "sourceSnapshot": f"/mnt/volume-{leaf}/.echo-snapshots/{reference}/{name}",
        "restoreTarget": f"/mnt/volume-{leaf}/{leaf}",
        "storageKind": "btrfsSubvolume",
    }


def test_absent_or_disabled_config_is_a_successful_noop() -> None:
    absent = schedule.run_scheduled_backup(
        config_reader=lambda: (
            False,
            schedule.validate_config(
                {
                    "schema": schedule.CONFIG_SCHEMA,
                    "enabled": False,
                    "repository": None,
                    "repositoryMount": None,
                }
            ),
        )
    )
    disabled = schedule.run_scheduled_backup(config_reader=lambda: _config(enabled=False))
    assert absent == disabled
    assert absent["outcome"] == "disabled"
    assert absent["pathsRedacted"] is True


@pytest.mark.parametrize(
    "value",
    [
        {
            "schema": schedule.CONFIG_SCHEMA,
            "enabled": True,
            "repository": "/mnt/x",
            "repositoryMount": "/mnt/x",
        },
        {
            "schema": schedule.CONFIG_SCHEMA,
            "enabled": True,
            "repository": "/data/../etc",
            "repositoryMount": "/data",
        },
        {
            "schema": schedule.CONFIG_SCHEMA,
            "enabled": True,
            "repository": "/mnt/x/*",
            "repositoryMount": "/mnt/x",
        },
        {"schema": schedule.CONFIG_SCHEMA, "enabled": False, "repository": None},
    ],
)
def test_config_rejects_unsafe_shapes_and_paths(value: dict[str, object]) -> None:
    with pytest.raises(schedule.NasDataBackupScheduleError):
        schedule.validate_config(value)


def test_config_reader_rejects_duplicate_fields(tmp_path: Path) -> None:
    path = tmp_path / "schedule.json"
    path.write_text(
        '{"schema":"echo.nas-data-backup-schedule.v1","schema":"x",'
        '"enabled":false,"repository":null,"repositoryMount":null}',
        encoding="utf-8",
    )
    if os.name == "posix":
        path.chmod(0o600)
    with pytest.raises(schedule.NasDataBackupScheduleError, match="duplicate"):
        schedule.read_config(path, owner=path.stat().st_uid)


def test_fresh_common_snapshot_is_backed_up_without_creating_another() -> None:
    calls: dict[str, object] = {}

    def plan(**kwargs):
        calls["manifest"] = kwargs["manifest"]
        return {"planId": "a" * 64}

    def create(**kwargs):
        manifest = kwargs["manifest"]
        calls["backup"] = kwargs
        return {
            "setId": manifest["setId"],
            "snapshotId": "b" * 64,
            "repositoryId": "c" * 16,
            "memberCount": 2,
            "encrypted": True,
            "fullReadVerified": True,
            "pathsRedacted": True,
            "idempotent": False,
        }

    result = schedule.run_scheduled_backup(
        now=datetime(2026, 9, 8, 3, tzinfo=UTC),
        config_reader=_config,
        snapshot_policy_reader=_policy,
        inventory_reader=_inventory,
        snapshot_runner=lambda **_kwargs: pytest.fail("fresh common snapshot should be reused"),
        member_builder=_member,
        plan_fn=plan,
        backup_fn=create,
        password_reader=lambda: b"secret",
    )
    manifest = calls["manifest"]
    assert [item["sharedFolderRef"] for item in manifest["members"]] == [REF1, REF2]
    assert calls["backup"]["password"] == b"secret"
    assert result["outcome"] == "completed"
    assert result["memberCount"] == 2
    assert result["pathsRedacted"] is True
    assert "/mnt/" not in json.dumps(result)


def test_missing_common_snapshot_runs_snapshot_schedule_then_backs_up() -> None:
    created = False

    def inventory(reference: str) -> dict[str, object]:
        return (
            _inventory(reference, SNAPSHOT_NAME)
            if created
            else {
                "sharedFolderRef": reference,
                "snapshots": [],
            }
        )

    def create_snapshots(**kwargs):
        nonlocal created
        assert kwargs["now"] == datetime(2026, 9, 8, 2, tzinfo=UTC)
        created = True
        return {"outcome": "completed", "created": 2, "pruned": 0, "errors": 0}

    result = schedule.run_scheduled_backup(
        now=datetime(2026, 9, 8, 2, tzinfo=UTC),
        config_reader=_config,
        snapshot_policy_reader=_policy,
        inventory_reader=inventory,
        snapshot_runner=create_snapshots,
        member_builder=_member,
        plan_fn=lambda **_kwargs: {"planId": "a" * 64},
        backup_fn=lambda **kwargs: {
            "setId": kwargs["manifest"]["setId"],
            "snapshotId": "b" * 64,
            "repositoryId": "c" * 16,
            "memberCount": 2,
            "encrypted": True,
            "fullReadVerified": True,
            "pathsRedacted": True,
            "idempotent": True,
        },
        password_reader=lambda: b"secret",
    )
    assert created is True
    assert result["outcome"] == "completed"
    assert result["idempotent"] is True


def test_partial_snapshot_run_fails_before_password_or_backup() -> None:
    with pytest.raises(schedule.NasDataBackupScheduleError, match="incomplete"):
        schedule.run_scheduled_backup(
            now=datetime(2026, 9, 8, 2, tzinfo=UTC),
            config_reader=_config,
            snapshot_policy_reader=_policy,
            inventory_reader=lambda reference: {"sharedFolderRef": reference, "snapshots": []},
            snapshot_runner=lambda **_kwargs: {
                "outcome": "completedWithErrors",
                "created": 1,
                "pruned": 0,
                "errors": 1,
            },
            member_builder=_member,
            plan_fn=lambda **_kwargs: pytest.fail("preflight must not run"),
            backup_fn=lambda **_kwargs: pytest.fail("backup must not run"),
            password_reader=lambda: pytest.fail("credential must not be read"),
        )


def test_systemd_units_are_installed_but_require_explicit_enablement() -> None:
    service = (REPOSITORY / "deploy/appliance/systemd/echo-nas-data-backup.service").read_text(
        encoding="utf-8"
    )
    timer = (REPOSITORY / "deploy/appliance/systemd/echo-nas-data-backup.timer").read_text(
        encoding="utf-8"
    )
    provision = (REPOSITORY / "deploy/provision/base/provision-lib.sh").read_text(encoding="utf-8")
    assert "deploy.appliance.nas_data_backup_schedule_runner" in service
    assert "LoadCredentialEncrypted=echo-nas-backup-password" in service
    assert "NoNewPrivileges=true" in service
    assert "PrivateNetwork=true" in service
    assert "ProtectSystem=strict" in service
    assert "CapabilityBoundingSet=CAP_SYS_ADMIN" in service
    assert "OnCalendar=*-*-* 03:30:00" in timer
    assert "Persistent=true" in timer
    assert "RandomizedDelaySec=30min" in timer
    assert "echo-nas-data-backup.service" in provision
    assert "echo-nas-data-backup.timer" in provision
    assert "systemctl enable --now echo-nas-data-backup.timer" not in provision


def test_private_history_is_bounded_and_path_redacted(tmp_path: Path) -> None:
    path = tmp_path / "history.json"
    owner = tmp_path.stat().st_uid
    group = tmp_path.stat().st_gid
    for index in range(35):
        schedule.record_attempt(
            {
                "schema": schedule.RESULT_SCHEMA,
                "outcome": "completed",
                "setId": f"00000000-0000-4000-8000-{index:012d}",
                "snapshotId": f"{index:064x}",
                "repositoryId": f"{index:016x}",
                "memberCount": 2,
                "encrypted": True,
                "fullReadVerified": True,
                "idempotent": False,
                "pathsRedacted": True,
            },
            completed_at=datetime(2026, 9, 8, 3, index % 60, tzinfo=UTC),
            path=path,
            owner=owner,
            group=group,
        )
    history = schedule.read_history(path, owner=owner)
    assert len(history["attempts"]) == schedule.MAX_STATUS_ATTEMPTS
    assert history["attempts"][0]["setId"].endswith("000000000003")
    assert history["attempts"][-1]["setId"].endswith("000000000034")
    assert "/mnt/" not in json.dumps(history)
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600


def test_history_refuses_to_replace_a_symlink(tmp_path: Path) -> None:
    owner = tmp_path.stat().st_uid
    group = tmp_path.stat().st_gid
    target = tmp_path / "target.json"
    target.write_text("do not replace", encoding="utf-8")
    link = tmp_path / "history.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks are unavailable")
    with pytest.raises(schedule.NasDataBackupScheduleError, match="unsafe"):
        schedule.record_attempt(
            {
                "schema": schedule.RESULT_SCHEMA,
                "outcome": "disabled",
                "memberCount": 0,
                "pathsRedacted": True,
            },
            path=link,
            owner=owner,
            group=group,
        )
    assert target.read_text(encoding="utf-8") == "do not replace"
