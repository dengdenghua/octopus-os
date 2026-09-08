from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from appliance import nas_backup_schedule_policy as policy
from deploy.appliance import nas_data_backup


def _identity_files(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    service = tmp_path / "echo-nas-data-backup.service"
    timer = tmp_path / "echo-nas-data-backup.timer"
    credential = tmp_path / "echo-nas-backup-password"
    runtime = tmp_path / "restic"
    for path, payload in (
        (service, b"[Service]\nExecStart=/bin/true\n"),
        (timer, b"[Timer]\nOnCalendar=daily\n"),
        (credential, b"encrypted credential"),
        (runtime, b"runtime"),
    ):
        path.write_bytes(payload)
        if os.name == "posix":
            path.chmod(0o600 if path == credential else 0o644)
    return service, timer, credential, runtime


def _desired(enabled: bool = True) -> dict[str, object]:
    return {
        "schema": policy.DESIRED_SCHEMA,
        "enabled": enabled,
        "repository": "/mnt/off-device/echo-nas-data" if enabled else None,
        "repositoryMount": "/mnt/off-device" if enabled else None,
    }


def _kwargs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    service, timer, credential, runtime = _identity_files(tmp_path)
    monkeypatch.setattr(nas_data_backup, "RESTIC", runtime)
    return {
        "config_path": tmp_path / "schedule.json",
        "service_path": service,
        "timer_path": timer,
        "credential_path": credential,
        "trusted_uid": tmp_path.stat().st_uid,
        "repository_binding_reader": lambda _config: {
            "filesystem": "ext4",
            "sourceSha256": "a" * 64,
            "device": 9,
            "inode": 10,
        },
    }


def test_plan_is_bound_to_private_paths_but_publicly_redacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kwargs = _kwargs(tmp_path, monkeypatch)
    plan = policy.plan_policy(_desired(), timer_enabled_reader=lambda: False, **kwargs)
    assert plan["operation"] == "enable"
    assert plan["requiresApproval"] is True
    assert plan["desired"] == {"enabled": True, "repositoryConfigured": True}
    assert plan["safety"]["encryptedCredentialRequired"] is True
    assert plan["pathsRedacted"] is True
    assert "/mnt/" not in json.dumps(plan)


def test_apply_writes_private_config_and_enables_timer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kwargs = _kwargs(tmp_path, monkeypatch)
    enabled = False

    def state() -> bool:
        return enabled

    def action(value: str) -> None:
        nonlocal enabled
        enabled = value == "enable"

    plan = policy.plan_policy(_desired(), timer_enabled_reader=state, **kwargs)
    result = policy.apply_policy(
        _desired(),
        plan["planId"],
        timer_enabled_reader=state,
        timer_action=action,
        trusted_gid=tmp_path.stat().st_gid,
        **kwargs,
    )
    assert result["applied"] is True
    assert result["verified"] is True
    assert enabled is True
    configured, saved = policy.schedule_runner.read_config(
        kwargs["config_path"], owner=tmp_path.stat().st_uid
    )
    assert configured is True
    assert saved == _desired()
    if os.name == "posix":
        assert kwargs["config_path"].stat().st_mode & 0o777 == 0o600


def test_apply_refuses_a_stale_timer_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    kwargs = _kwargs(tmp_path, monkeypatch)
    enabled = False
    plan = policy.plan_policy(_desired(), timer_enabled_reader=lambda: enabled, **kwargs)
    enabled = True
    with pytest.raises(policy.NasBackupSchedulePolicyError, match="stale"):
        policy.apply_policy(
            _desired(),
            plan["planId"],
            timer_enabled_reader=lambda: enabled,
            timer_action=lambda _action: None,
            trusted_gid=tmp_path.stat().st_gid,
            **kwargs,
        )


def test_apply_rolls_back_config_when_timer_enable_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kwargs = _kwargs(tmp_path, monkeypatch)
    enabled = False
    actions: list[str] = []

    def action(value: str) -> None:
        nonlocal enabled
        actions.append(value)
        if value == "enable":
            raise OSError("injected")
        enabled = False

    plan = policy.plan_policy(_desired(), timer_enabled_reader=lambda: enabled, **kwargs)
    with pytest.raises(OSError, match="rolled back"):
        policy.apply_policy(
            _desired(),
            plan["planId"],
            timer_enabled_reader=lambda: enabled,
            timer_action=action,
            trusted_gid=tmp_path.stat().st_gid,
            **kwargs,
        )
    assert actions == ["enable", "disable"]
    assert not kwargs["config_path"].exists()


def test_disable_remains_available_without_runtime_or_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kwargs = _kwargs(tmp_path, monkeypatch)
    config_path = kwargs["config_path"]
    config_path.write_text(json.dumps(_desired()) + "\n", encoding="utf-8")
    if os.name == "posix":
        config_path.chmod(0o600)
    kwargs["credential_path"].unlink()
    kwargs["service_path"].unlink()
    nas_data_backup.RESTIC.unlink()
    enabled = True

    def action(value: str) -> None:
        nonlocal enabled
        enabled = value == "enable"

    plan = policy.plan_policy(_desired(False), timer_enabled_reader=lambda: enabled, **kwargs)
    result = policy.apply_policy(
        _desired(False),
        plan["planId"],
        timer_enabled_reader=lambda: enabled,
        timer_action=action,
        trusted_gid=tmp_path.stat().st_gid,
        **kwargs,
    )
    assert result["operation"] == "disable"
    assert result["verified"] is True
    assert enabled is False


def test_status_returns_redacted_history_and_readiness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kwargs = _kwargs(tmp_path, monkeypatch)
    config_path = kwargs["config_path"]
    config_path.write_text(json.dumps(_desired()) + "\n", encoding="utf-8")
    if os.name == "posix":
        config_path.chmod(0o600)
    result = policy.policy_status(
        config_path=config_path,
        history_path=tmp_path / "history.json",
        service_path=kwargs["service_path"],
        timer_path=kwargs["timer_path"],
        credential_path=kwargs["credential_path"],
        trusted_uid=tmp_path.stat().st_uid,
        timer_enabled_reader=lambda: True,
    )
    assert result["enabled"] is True
    assert result["repositoryConfigured"] is True
    assert result["credentialConfigured"] is True
    assert result["schedulerInstalled"] is True
    assert result["timerEnabled"] is True
    assert result["history"] == []
    assert result["pathsRedacted"] is True
    assert "/mnt/" not in json.dumps(result)
