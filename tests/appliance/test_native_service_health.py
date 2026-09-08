from __future__ import annotations

from datetime import UTC, datetime

from appliance.native_service_health import service_health

NOW = datetime(2026, 9, 9, 1, 2, 3, tzinfo=UTC)


def _unit(
    name: str,
    *,
    load: str = "loaded",
    unit_file: str = "enabled",
    active: str = "active",
    sub: str = "running",
    result: str = "success",
    restarts: int = 0,
) -> str:
    return "\n".join(
        (
            f"Id={name}",
            f"LoadState={load}",
            f"UnitFileState={unit_file}",
            f"ActiveState={active}",
            f"SubState={sub}",
            f"Result={result}",
            f"NRestarts={restarts}",
        )
    )


def _probe(output: str):
    return service_health(
        command_finder=lambda _command: "/usr/bin/systemctl",
        reader=lambda: output,
        now=lambda: NOW,
    )


def test_missing_systemd_is_explicitly_unavailable() -> None:
    snapshot = service_health(command_finder=lambda _command: None)

    assert snapshot["available"] is False
    assert snapshot["state"] == "unknown"
    assert snapshot["code"] == "toolMissing"
    assert snapshot["activeAlerts"] == []


def test_enabled_active_units_are_healthy_and_disabled_units_are_not_expected() -> None:
    snapshot = _probe(
        "\n\n".join(
            (
                _unit("echo-appliance.service", restarts=2),
                _unit(
                    "docker.service",
                    unit_file="disabled",
                    active="inactive",
                    sub="dead",
                ),
                _unit(
                    "echo-agent.service",
                    load="not-found",
                    unit_file="",
                    active="inactive",
                    sub="dead",
                    result="",
                ),
            )
        )
    )

    assert snapshot["available"] is True
    assert snapshot["state"] == "healthy"
    assert snapshot["expected"] == 1
    assert snapshot["activeAlerts"] == []
    assert snapshot["checkedAt"] == "2026-09-09T01:02:03Z"


def test_failed_inactive_and_start_limited_units_produce_bounded_alerts() -> None:
    snapshot = _probe(
        "\n\n".join(
            (
                _unit(
                    "echo-appliance.service",
                    active="failed",
                    sub="failed",
                    result="exit-code",
                    restarts=1,
                ),
                _unit(
                    "nginx.service",
                    active="failed",
                    sub="failed",
                    result="start-limit-hit",
                    restarts=5,
                ),
                _unit(
                    "smbd.service",
                    active="inactive",
                    sub="dead",
                    result="success",
                ),
            )
        )
    )

    assert snapshot["state"] == "critical"
    assert [alert["code"] for alert in snapshot["activeAlerts"]] == [
        "service.failed",
        "service.restart_storm",
        "service.inactive",
    ]
    assert all(
        set(alert) == {"id", "code", "severity", "resource", "message"}
        for alert in snapshot["activeAlerts"]
    )
    assert "exit-code" not in str(snapshot["activeAlerts"])


def test_unexpected_or_malformed_systemd_output_fails_closed() -> None:
    snapshot = _probe(_unit("attacker-controlled.service"))

    assert snapshot["available"] is False
    assert snapshot["code"] == "probeFailed"
    assert snapshot["units"] == []
    assert "attacker-controlled" not in str(snapshot)
