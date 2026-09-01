from __future__ import annotations

import json
import stat
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from appliance.omv_client import OmvUnavailable
from appliance.omv_health import OmvHealthMonitor


class _Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 26, 1, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value

    def advance(self) -> None:
        self.value += timedelta(minutes=5)


class _HealthClient:
    configured = True

    def __init__(self) -> None:
        self.available = True
        self.filesystem_entries = [
            {
                "devicefile": "/dev/md0",
                "label": "Family",
                "sizeBytes": 1_000,
                "availableBytes": 40,
                "usedPercent": 96,
            }
        ]
        self.device_entries = [
            {
                "devicefile": "/dev/sda",
                "health": "FAILED",
                "temperatureC": 61,
            }
        ]
        self.topology = {
            "devices": [],
            "arrays": [
                {
                    "devicefile": "/dev/md0",
                    "status": "degraded",
                    "operationPercent": None,
                }
            ],
        }

    def _ready(self) -> None:
        if not self.available:
            raise OmvUnavailable("bridge unavailable")

    def filesystems(self):
        self._ready()
        return self.filesystem_entries

    def smart_devices(self):
        self._ready()
        return self.device_entries

    def storage_topology(self):
        self._ready()
        return self.topology


def test_monitor_persists_alerts_and_resolution_history(tmp_path: Path) -> None:
    client = _HealthClient()
    clock = _Clock()
    state_path = tmp_path / "omv-health-state.json"
    monitor = OmvHealthMonitor(client, state_path, clock=clock)

    unhealthy = monitor.poll()

    assert unhealthy["state"] == "critical"
    assert unhealthy["stale"] is False
    assert unhealthy["summary"] == {"critical": 4, "warning": 0, "total": 4}
    assert {alert["code"] for alert in unhealthy["activeAlerts"]} == {
        "disk.smart",
        "disk.temperature",
        "volume.capacity",
        "raid.degraded",
    }
    assert all(alert["occurrences"] == 1 for alert in unhealthy["activeAlerts"])
    assert [event["event"] for event in unhealthy["events"]] == ["opened"] * 4
    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600
    persisted = json.loads(state_path.read_text())
    assert "monitoring" not in persisted
    assert persisted["readOnly"] is True

    client.filesystem_entries[0]["usedPercent"] = 20
    client.filesystem_entries[0]["availableBytes"] = 800
    client.device_entries[0]["health"] = "GOOD"
    client.device_entries[0]["temperatureC"] = 35
    client.topology["arrays"][0]["status"] = "healthy"
    clock.advance()

    recovered = monitor.poll()

    assert recovered["state"] == "healthy"
    assert recovered["activeAlerts"] == []
    assert recovered["summary"] == {"critical": 0, "warning": 0, "total": 0}
    assert [event["event"] for event in recovered["events"][-4:]] == ["resolved"] * 4

    reloaded = OmvHealthMonitor(client, state_path, clock=clock).snapshot()
    assert reloaded["state"] == "healthy"
    assert reloaded["lastSuccessfulAt"] == "2026-08-26T01:05:00Z"
    assert len(reloaded["events"]) == 8


def test_bridge_failure_keeps_prior_alerts_stale_until_a_successful_poll(
    tmp_path: Path,
) -> None:
    client = _HealthClient()
    clock = _Clock()
    monitor = OmvHealthMonitor(client, tmp_path / "health.json", clock=clock)
    first = monitor.poll()
    prior_smart = next(alert for alert in first["activeAlerts"] if alert["code"] == "disk.smart")
    client.available = False
    clock.advance()

    unavailable = monitor.poll()

    assert unavailable["state"] == "unavailable"
    assert unavailable["stale"] is True
    assert unavailable["lastSuccessfulAt"] == "2026-08-26T01:00:00Z"
    assert "bridge.unavailable" in {alert["code"] for alert in unavailable["activeAlerts"]}
    stale_smart = next(
        alert for alert in unavailable["activeAlerts"] if alert["code"] == "disk.smart"
    )
    assert stale_smart["firstSeenAt"] == prior_smart["firstSeenAt"]
    assert stale_smart["lastSeenAt"] == prior_smart["lastSeenAt"]
    assert stale_smart["occurrences"] == prior_smart["occurrences"]

    client.available = True
    client.filesystem_entries = []
    client.device_entries = []
    client.topology = {"devices": [], "arrays": []}
    clock.advance()
    recovered = monitor.poll()

    assert recovered["state"] == "healthy"
    assert recovered["stale"] is False
    assert recovered["activeAlerts"] == []
    resolved_codes = {
        event["code"] for event in recovered["events"] if event["event"] == "resolved"
    }
    assert "bridge.unavailable" in resolved_codes
    assert "disk.smart" in resolved_codes


def test_monitor_never_follows_a_state_symlink(tmp_path: Path) -> None:
    client = _HealthClient()
    outside = tmp_path / "outside.json"
    outside.write_text("do not replace")
    state_path = tmp_path / "health.json"
    state_path.symlink_to(outside)

    snapshot = OmvHealthMonitor(client, state_path).poll()

    assert snapshot["persistenceHealthy"] is False
    assert outside.read_text() == "do not replace"
    assert state_path.is_symlink()


def test_invalid_environment_thresholds_fail_closed(monkeypatch) -> None:
    client = _HealthClient()
    monkeypatch.setenv("ECHO_OMV_TEMP_WARNING_C", "70")
    monkeypatch.setenv("ECHO_OMV_TEMP_CRITICAL_C", "60")

    with pytest.raises(ValueError, match="temperature thresholds"):
        OmvHealthMonitor.from_environment(client, None)


def test_unconfigured_monitor_does_not_start_a_background_thread() -> None:
    client = _HealthClient()
    client.configured = False
    monitor = OmvHealthMonitor(client, None, interval_seconds=0.01)

    monitor.start()

    assert monitor.running is False
    assert monitor.poll()["state"] == "notConfigured"


def test_background_monitor_polls_and_stops_cleanly() -> None:
    completed = threading.Event()

    class _ObservedClient(_HealthClient):
        def storage_topology(self):
            result = super().storage_topology()
            completed.set()
            return result

    monitor = OmvHealthMonitor(_ObservedClient(), None, interval_seconds=0.01)

    monitor.start()
    assert completed.wait(timeout=1)
    assert monitor.running is True
    monitor.stop()

    assert monitor.running is False
