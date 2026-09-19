from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from appliance import nas_backup_remote_policy as remote


def _desired(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "schema": remote.DESIRED_SCHEMA,
        "operation": "create",
        "remoteId": "offsite",
        "label": "异地对象存储",
        "endpoint": "https://s3.example.test",
        "region": "us-east-1",
        "bucket": "echo-backups",
        "prefix": "family/nas",
        "accessKeyId": "ACCESS-KEY-123",
        "secretAccessKey": "private-secret-value",
    }
    value.update(overrides)
    return value


def _remove(remote_id: str = "offsite") -> dict[str, Any]:
    return {
        "schema": remote.DESIRED_SCHEMA,
        "operation": "remove",
        "remoteId": remote_id,
    }


def _runtime(tmp_path: Path) -> dict[str, Any]:
    owner = os.getuid() if hasattr(os, "getuid") else 0
    registry_parent = tmp_path / "etc" / "echo-os"
    credential_root = tmp_path / "etc" / "credentials"
    mount_root = tmp_path / "mnt" / "remotes"
    registry_parent.mkdir(parents=True)
    credential_root.mkdir(parents=True)
    mount_root.mkdir(parents=True)
    registry_parent.chmod(0o755)
    credential_root.chmod(0o700)
    mount_root.chmod(0o755)
    tools = {}
    for name in ("unit", "systemd-creds", "systemctl", "rclone", "fusermount3"):
        path = tmp_path / name
        path.write_text(name, encoding="utf-8")
        path.chmod(0o755)
        tools[name] = path
    return {
        "registry_path": registry_parent / "nas-backup-remotes.json",
        "credential_root": credential_root,
        "mount_root": mount_root,
        "unit_path": tools["unit"],
        "systemd_creds": tools["systemd-creds"],
        "systemctl": tools["systemctl"],
        "rclone": tools["rclone"],
        "fusermount3": tools["fusermount3"],
        "trusted_uid": owner,
        "binding_key": b"b" * 32,
        "in_use_reader": lambda _remote_id: False,
    }


def _write_credential(_desired: dict[str, Any], path: Path) -> None:
    path.write_bytes(b"encrypted-systemd-credential")
    path.chmod(0o600)


def test_create_plan_binds_but_never_returns_connection_secrets(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)

    plan = remote.plan_remote(_desired(), **runtime)

    assert plan["operation"] == "create"
    assert plan["desired"] == {
        "operation": "create",
        "remoteId": "offsite",
        "kind": "s3",
        "label": "异地对象存储",
    }
    assert plan["pathsRedacted"] is True
    assert plan["secretsRedacted"] is True
    serialized = json.dumps(plan, ensure_ascii=False)
    for secret in (
        "s3.example.test",
        "echo-backups",
        "family/nas",
        "ACCESS-KEY-123",
        "private-secret-value",
    ):
        assert secret not in serialized


def test_secret_or_endpoint_change_invalidates_plan(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)

    first = remote.plan_remote(_desired(), **runtime)
    second = remote.plan_remote(_desired(secretAccessKey="another-private-secret"), **runtime)
    third = remote.plan_remote(_desired(endpoint="https://another.example.test"), **runtime)

    assert len({first["planId"], second["planId"], third["planId"]}) == 3


def test_credential_config_has_one_s3_source_and_bucket_scoped_alias() -> None:
    config = remote._credential_config(remote._desired(_desired()))

    assert config == (
        b"[source]\n"
        b"type = s3\n"
        b"provider = Other\n"
        b"env_auth = false\n"
        b"access_key_id = ACCESS-KEY-123\n"
        b"secret_access_key = private-secret-value\n"
        b"endpoint = https://s3.example.test\n"
        b"region = us-east-1\n"
        b"acl = private\n\n"
        b"[echo]\n"
        b"type = alias\n"
        b"remote = source:echo-backups/family/nas\n"
    )
    assert len(config) < remote.MAX_CREDENTIAL_BYTES


def test_default_mount_probe_reads_the_process_mount_table(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Path] = []
    target = Path("/mnt/echo-backup-remotes/offsite")

    def rows(path: Path) -> list[dict[str, Any]]:
        seen.append(path)
        return [
            {
                "mountpoint": str(target),
                "filesystem": "fuse.rclone",
                "options": frozenset({"rw", "nodev"}),
                "source": "redacted",
            }
        ]

    monkeypatch.setattr(remote.external_storage, "_mount_rows", rows)

    assert remote._mount_active(target) is True
    assert seen == [remote.MOUNTINFO]


def test_default_mount_verifier_uses_the_current_external_storage_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = Path("/mnt/echo-backup-remotes/offsite")
    calls: list[dict[str, Any]] = []

    def verify(**kwargs: Any) -> dict[str, str]:
        calls.append(kwargs)
        return {"filesystem": "fuse.rclone"}

    monkeypatch.setattr(remote.external_storage, "verify_external_storage", verify)
    monkeypatch.setattr(remote, "_mount_active", lambda path: path == target)

    assert remote._verify_mount(target) is True
    assert calls == [
        {
            "destination": target,
            "mountpoint": target,
            "deployment_root": remote.schedule_runner.DEPLOYMENT_ROOT,
            "appliance_env": remote.schedule_runner.APPLIANCE_ENV,
            "state_root_override": remote.schedule_runner.STATE_ROOT,
            "nas_root_override": remote.schedule_runner.NAS_ROOT,
        }
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("remoteId", "../escape"),
        ("endpoint", "http://s3.example.test"),
        ("endpoint", "https://user:pass@s3.example.test"),
        ("endpoint", "https://s3.example.test/path"),
        ("bucket", "192.168.1.1"),
        ("bucket", "Bad_Bucket"),
        ("prefix", "family/../other"),
        ("secretAccessKey", "short"),
    ],
)
def test_create_plan_rejects_unsafe_remote_values(tmp_path: Path, field: str, value: str) -> None:
    runtime = _runtime(tmp_path)

    with pytest.raises(remote.NasBackupRemotePolicyError):
        remote.plan_remote(_desired(**{field: value}), **runtime)


def test_create_and_remove_are_verified_and_leave_no_plaintext(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    mounted = False
    actions: list[tuple[str, str]] = []

    def service_action(action: str, remote_id: str) -> None:
        nonlocal mounted
        actions.append((action, remote_id))
        mounted = action == "start"

    def mount_verifier(_path: Path) -> bool:
        return mounted

    desired = _desired()
    plan = remote.plan_remote(desired, **runtime)
    result = remote.apply_remote(
        desired,
        plan["planId"],
        **runtime,
        credential_writer=_write_credential,
        service_action=service_action,
        mount_state_reader=mount_verifier,
        mount_verifier=mount_verifier,
    )

    assert result["mounted"] is True
    assert actions == [("start", "offsite")]
    registry = runtime["registry_path"].read_text(encoding="utf-8")
    assert json.loads(registry)["remotes"][0]["label"] == "异地对象存储"
    assert "s3.example.test" not in registry
    assert "private-secret-value" not in registry
    credential = runtime["credential_root"] / "echo-rclone-backup-offsite.conf"
    assert credential.read_bytes() == b"encrypted-systemd-credential"

    remove_plan = remote.plan_remote(_remove(), **runtime)
    removed = remote.apply_remote(
        _remove(),
        remove_plan["planId"],
        **runtime,
        credential_writer=_write_credential,
        service_action=service_action,
        mount_state_reader=mount_verifier,
        mount_verifier=mount_verifier,
    )

    assert removed["mounted"] is False
    assert actions[-1] == ("stop", "offsite")
    assert not credential.exists()
    assert not (runtime["mount_root"] / "offsite").exists()
    assert json.loads(runtime["registry_path"].read_text(encoding="utf-8"))["remotes"] == []


def test_remove_refuses_a_remote_bound_to_the_backup_schedule(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    registry = {
        "schema": remote.REGISTRY_SCHEMA,
        "remotes": [{"id": "offsite", "label": "异地", "kind": "s3"}],
    }
    runtime["registry_path"].write_text(json.dumps(registry), encoding="utf-8")
    runtime["registry_path"].chmod(0o600)
    credential = runtime["credential_root"] / "echo-rclone-backup-offsite.conf"
    credential.write_bytes(b"encrypted")
    credential.chmod(0o600)
    runtime["in_use_reader"] = lambda _remote_id: True

    with pytest.raises(remote.NasBackupRemotePolicyError, match="schedule"):
        remote.plan_remote(_remove(), **runtime)


def test_failed_mount_verification_rolls_back_every_created_artifact(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    actions: list[tuple[str, str]] = []
    plan = remote.plan_remote(_desired(), **runtime)

    with pytest.raises(OSError, match="rolled back"):
        remote.apply_remote(
            _desired(),
            plan["planId"],
            **runtime,
            credential_writer=_write_credential,
            service_action=lambda action, remote_id: actions.append((action, remote_id)),
            mount_state_reader=lambda _path: bool(actions and actions[-1][0] == "start"),
            mount_verifier=lambda _path: False,
        )

    assert actions == [("start", "offsite"), ("stop", "offsite")]
    assert not runtime["registry_path"].exists()
    assert not (runtime["credential_root"] / "echo-rclone-backup-offsite.conf").exists()
    assert not (runtime["mount_root"] / "offsite").exists()


def test_mount_state_wait_tolerates_a_bounded_systemd_fuse_race(tmp_path: Path) -> None:
    states = iter([False, False, True])

    assert remote._wait_for_mount_state(
        tmp_path / "remote",
        True,
        lambda _path: next(states),
        timeout=1,
    )


def test_list_remotes_is_bounded_and_connection_data_is_redacted(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    registry = {
        "schema": remote.REGISTRY_SCHEMA,
        "remotes": [{"id": "offsite", "label": "异地", "kind": "s3"}],
    }
    runtime["registry_path"].write_text(
        json.dumps(registry, sort_keys=True, separators=(",", ":")), encoding="utf-8"
    )
    runtime["registry_path"].chmod(0o600)

    result = remote.list_remotes(
        registry_path=runtime["registry_path"],
        trusted_uid=runtime["trusted_uid"],
        mount_root=runtime["mount_root"],
        mount_state_reader=lambda _path: True,
    )

    assert result == {
        "schema": remote.STATUS_SCHEMA,
        "remotes": [{"id": "offsite", "label": "异地", "kind": "s3", "mounted": True}],
        "count": 1,
        "pathsRedacted": True,
        "secretsRedacted": True,
    }
