from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from deploy.appliance import ups_shutdown_guard as guard

REPOSITORY = Path(__file__).resolve().parents[2]


def _write(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")
    path.chmod(0o600)


def _policy(path: Path, *, enabled: bool = True, samples: int = 3) -> None:
    _write(
        path,
        {
            "schemaVersion": 1,
            "enabled": enabled,
            "requiredConsecutiveSamples": samples,
        },
    )


def _snapshot(*flags: str) -> dict[str, Any]:
    return {
        "available": True,
        "devices": [{"name": "local-ups", "available": True, "statusFlags": list(flags)}],
    }


def test_missing_policy_is_disabled_and_never_reads_nut(tmp_path: Path) -> None:
    result = guard.evaluate(
        policy_path=tmp_path / "missing.json",
        state_path=tmp_path / "state.json",
        trusted_uid=tmp_path.stat().st_uid,
        status_reader=lambda: (_ for _ in ()).throw(AssertionError("must not query NUT")),
        command_runner=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("must not power off")
        ),
    )

    assert result == {"outcome": "disabled", "shutdownRequested": False}


def test_low_battery_must_persist_before_fixed_poweroff(tmp_path: Path) -> None:
    policy = tmp_path / "policy.json"
    state = tmp_path / "state.json"
    systemctl = tmp_path / "systemctl"
    systemctl.write_text("trusted executable placeholder", encoding="utf-8")
    _policy(policy)
    calls: list[list[str]] = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        assert "shell" not in kwargs
        return subprocess.CompletedProcess(argv, 0, "", "")

    first = guard.evaluate(
        policy_path=policy,
        state_path=state,
        systemctl=systemctl,
        trusted_uid=tmp_path.stat().st_uid,
        status_reader=lambda: _snapshot("OB", "LB", "DISCHRG"),
        command_runner=run,
    )
    second = guard.evaluate(
        policy_path=policy,
        state_path=state,
        systemctl=systemctl,
        trusted_uid=tmp_path.stat().st_uid,
        status_reader=lambda: _snapshot("OB", "LB"),
        command_runner=run,
    )
    third = guard.evaluate(
        policy_path=policy,
        state_path=state,
        systemctl=systemctl,
        trusted_uid=tmp_path.stat().st_uid,
        status_reader=lambda: _snapshot("OB", "LB"),
        command_runner=run,
    )

    assert first["consecutiveSamples"] == 1
    assert second["consecutiveSamples"] == 2
    assert third["shutdownRequested"] is True
    assert calls == [[str(systemctl), "poweroff", "--no-block"]]


def test_safe_or_unavailable_sample_resets_confirmation(tmp_path: Path) -> None:
    policy = tmp_path / "policy.json"
    state = tmp_path / "state.json"
    _policy(policy)
    _write(
        state,
        {"schemaVersion": 1, "device": "local-ups", "consecutiveLowBatterySamples": 2},
    )

    result = guard.evaluate(
        policy_path=policy,
        state_path=state,
        trusted_uid=tmp_path.stat().st_uid,
        status_reader=lambda: {"available": False, "devices": []},
    )

    assert result["outcome"] == "unavailable"
    assert json.loads(state.read_text(encoding="utf-8"))["consecutiveLowBatterySamples"] == 0


def test_fsd_requests_poweroff_without_waiting(tmp_path: Path) -> None:
    policy = tmp_path / "policy.json"
    state = tmp_path / "state.json"
    systemctl = tmp_path / "systemctl"
    systemctl.write_text("trusted executable placeholder", encoding="utf-8")
    _policy(policy, samples=12)
    calls: list[list[str]] = []

    result = guard.evaluate(
        policy_path=policy,
        state_path=state,
        systemctl=systemctl,
        trusted_uid=tmp_path.stat().st_uid,
        status_reader=lambda: _snapshot("FSD"),
        command_runner=lambda argv, **_kwargs: (
            calls.append(argv) or subprocess.CompletedProcess(argv, 0, "", "")
        ),
    )

    assert result["outcome"] == "forcedShutdown"
    assert result["shutdownRequested"] is True
    assert calls == [[str(systemctl), "poweroff", "--no-block"]]


@pytest.mark.parametrize(
    "value",
    [
        {"schemaVersion": 1, "enabled": True, "requiredConsecutiveSamples": 1},
        {"schemaVersion": 1, "enabled": True, "requiredConsecutiveSamples": 13},
        {"schemaVersion": 1, "enabled": 1, "requiredConsecutiveSamples": 3},
        {"schemaVersion": 1, "enabled": True, "requiredConsecutiveSamples": 3, "host": "remote"},
    ],
)
def test_invalid_or_extended_policy_fails_closed(tmp_path: Path, value: dict[str, Any]) -> None:
    policy = tmp_path / "policy.json"
    _write(policy, value)

    with pytest.raises(guard.GuardError):
        guard.evaluate(
            policy_path=policy,
            state_path=tmp_path / "state.json",
            trusted_uid=tmp_path.stat().st_uid,
        )


def test_provisioning_installs_nut_and_hardened_guard_timer() -> None:
    provision = (REPOSITORY / "deploy/provision/base/provision-lib.sh").read_text(encoding="utf-8")
    service = (REPOSITORY / "deploy/appliance/systemd/echo-ups-shutdown-guard.service").read_text(
        encoding="utf-8"
    )
    timer = (REPOSITORY / "deploy/appliance/systemd/echo-ups-shutdown-guard.timer").read_text(
        encoding="utf-8"
    )

    assert "nut-client nut-server" in provision
    assert "echo-ups-shutdown-guard.service" in provision
    assert "systemctl enable --now echo-ups-shutdown-guard.timer" in provision
    assert (
        "ExecStart=/opt/echo-os/.venv/bin/python -m deploy.appliance.ups_shutdown_guard" in service
    )
    assert "NoNewPrivileges=true" in service
    assert "ProtectSystem=strict" in service
    assert "RestrictAddressFamilies=AF_UNIX" in service
    assert "OnUnitActiveSec=15s" in timer
