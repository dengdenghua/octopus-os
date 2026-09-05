"""Read-only probe failures and real parser evidence, independent of NAS writes."""

from __future__ import annotations

import asyncio
import builtins
import io
import json
import subprocess
import threading
from typing import Any

import httpx
import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from appliance import native_storage as storage
from appliance.native_storage_probe import health_observation, run_readonly
from appliance.native_storage_routes import register_native_storage_routes

DISK = {"name": "sda", "type": "disk", "size": 10 * 1024**3}
DF = "Filesystem Type 1B-blocks Used Available Capacity Mounted on\n/dev/sda1 ext4 10000 1000 9000 10% /data\n"
MD_EMPTY = "Personalities : [raid1]\nunused devices: <none>\n"


@pytest.fixture
def commands(monkeypatch: pytest.MonkeyPatch):
    outputs: dict[str, Any] = {
        "lsblk": json.dumps({"blockdevices": [DISK]}),
        "df": DF,
        "smartctl": json.dumps({"smart_status": {"passed": True}}),
        "zpool": FileNotFoundError(),
        "findmnt": "rw,relatime\n",
        "blkid": "",
        "mdstat": MD_EMPTY,
    }
    calls: list[tuple[str, ...]] = []

    def run(argv, **kwargs):
        calls.append(tuple(argv))
        assert kwargs["check"] is False
        assert kwargs["timeout"] <= 25
        value = outputs[argv[0]]
        if isinstance(value, BaseException):
            raise value
        if callable(value):
            value = value(argv)
        if isinstance(value, subprocess.CompletedProcess):
            return value
        return subprocess.CompletedProcess(argv, 0, stdout=value, stderr="")

    real_open = builtins.open

    def open_file(path, *args, **kwargs):
        if str(path) == "/proc/mdstat":
            value = outputs["mdstat"]
            if isinstance(value, BaseException):
                raise value
            return io.StringIO(value)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(builtins, "open", open_file)
    monkeypatch.setattr(
        storage.shutil, "which", lambda binary: "/usr/bin/findmnt" if binary == "findmnt" else None
    )
    return outputs, calls


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        (FileNotFoundError(), "tool_missing"),
        (PermissionError(), "permission_denied"),
        (subprocess.TimeoutExpired("lsblk", 20), "timeout"),
        (OSError(), "read_failed"),
        (UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid"), "invalid_output"),
        (
            subprocess.CompletedProcess([], 1, "", "Permission denied: PRIVATE-HOST-DETAIL"),
            "permission_denied",
        ),
        (subprocess.CompletedProcess([], 1, "", "PRIVATE-HOST-DETAIL"), "command_failed"),
    ],
)
def test_failed_device_read_keeps_reason_without_host_stderr(commands, failure, code):
    outputs, _ = commands
    outputs["lsblk"] = failure
    result = storage._probe_block_devices()
    assert result.value == []
    assert result.evidence["code"] == code
    assert result.evidence["state"] in {"error", "unavailable"}
    assert "PRIVATE-HOST-DETAIL" not in json.dumps(result.evidence)


@pytest.mark.parametrize(
    "invalid", ["not-json", "[]", '{"blockdevices":{}}', '{"blockdevices":[null]}']
)
def test_invalid_device_inventory_cannot_become_a_success(commands, invalid):
    outputs, _ = commands
    outputs["lsblk"] = invalid
    probe = storage._probe_block_devices()
    assert probe.value == []
    assert probe.evidence["code"] == "parse_failed"


def test_partial_inventory_retains_the_valid_disk(commands):
    outputs, _ = commands
    outputs["lsblk"] = json.dumps({"blockdevices": [DISK, {"name": "sdb", "size": "bad"}]})
    probe = storage._probe_block_devices()
    assert [item["devicefile"] for item in probe.value] == ["/dev/sda"]
    assert probe.evidence["state"] == "partial"
    assert probe.evidence["count"] == 1


@pytest.mark.parametrize("filesystem_type", [[], {}, True])
def test_invalid_filesystem_type_does_not_crash_inventory(commands, filesystem_type):
    outputs, _ = commands
    outputs["lsblk"] = json.dumps({"blockdevices": [{**DISK, "fstype": filesystem_type}]})
    probe = storage._probe_block_devices()
    assert probe.value[0]["devicefile"] == "/dev/sda"
    assert probe.evidence["state"] == "partial"
    assert probe.evidence["code"] == "partial_parse"


def test_empty_inventory_is_explicit_and_not_healthy(commands):
    outputs, _ = commands
    outputs["lsblk"] = '{"blockdevices":[]}'
    probe = storage._probe_block_devices()
    assert probe.evidence["state"] == "empty"
    assert probe.evidence["code"] == "empty_inventory"


def test_partial_capacity_parse_keeps_valid_zero_usage(commands):
    outputs, _ = commands
    outputs["df"] = (
        DF.replace("1000 9000 10%", "0 10000 0%") + "/dev/sdb1 ext4 invalid 2 3 4% /mnt\n"
    )
    probe = storage._probe_filesystems()
    assert probe.value[0]["usedPercent"] == 0
    assert probe.evidence["state"] == "partial"
    assert probe.evidence["code"] == "partial_parse"


def test_multiline_md_members_and_recovery_are_parsed(commands):
    outputs, _ = commands
    outputs["mdstat"] = (
        "Personalities : [raid1]\n"
        "md0 : active raid1 sda1[0] sdb1[1]\n"
        "      100000 blocks super 1.2 [2/1] [U_]\n"
        "      [====>................] recovery = 25.0% (25000/100000)\n"
        "unused devices: <none>\n"
    )
    probe = storage._probe_md_arrays()
    assert probe.evidence["state"] == "ok"
    assert probe.value[0]["status"] == "degraded"
    assert probe.value[0]["activeDevices"] == 1
    assert probe.value[0]["operationPercent"] == 25


def test_inactive_md_is_not_matched_as_active(commands):
    outputs, _ = commands
    outputs["mdstat"] = "Personalities : [raid1]\nmd0 : inactive sda1[0](S)\n"
    assert storage._probe_md_arrays().value[0]["status"] == "inactive"


def test_absent_array_technologies_are_not_machine_failures(commands):
    assert storage._probe_md_arrays().evidence["state"] == "not-applicable"
    assert storage._probe_zfs_pools().evidence["state"] == "not-applicable"


@pytest.mark.parametrize("operation", ["scrub", "resilver"])
def test_zfs_topology_reports_multiline_maintenance_progress(commands, operation):
    outputs, _ = commands

    def zpool(argv):
        if argv[1] == "list":
            return "family\t10000\t1000\t9000\tONLINE\t10\t0\n"
        return (
            "  pool: family\n"
            " state: ONLINE\n"
            f"  scan: {operation} in progress since Sun Sep 5 01:00:00 2026\n"
            "        0B repaired, 25.9% done, 00:10:00 to go\n"
            "config:\n\n"
            "        NAME      STATE     READ WRITE CKSUM\n"
            "        family    ONLINE       0     0     0\n\n"
            "errors: No known data errors\n"
        )

    outputs["zpool"] = zpool

    pool = storage._probe_zfs_pools().value[0][0]

    assert pool["operation"] == operation
    assert pool["operationPercent"] == 25


def test_missing_tools_are_gaps_when_inventory_proves_zfs_or_md(commands):
    outputs, _ = commands
    outputs["mdstat"] = FileNotFoundError()
    md = storage._probe_md_arrays(expected=True)
    zfs = storage._probe_zfs_pools(expected=True)
    assert md.evidence["required"] and md.evidence["state"] == "unavailable"
    assert zfs.evidence["required"] and zfs.evidence["state"] == "unavailable"


@pytest.mark.parametrize(
    ("exit_code", "health", "state"),
    [
        (0, "PASSED", "ok"),
        (8, "FAILED", "ok"),
        (16, "FAILED", "ok"),
        (32, "WARNING", "ok"),
        (64, "WARNING", "ok"),
        (128, "WARNING", "ok"),
        (4, "PASSED", "partial"),
        (1, "UNKNOWN", "error"),
        (2, "UNKNOWN", "error"),
        (-9, "UNKNOWN", "error"),
        (256, "UNKNOWN", "error"),
    ],
)
def test_smart_exit_mask_separates_disk_health_from_read_failures(
    commands, exit_code, health, state
):
    outputs, _ = commands
    outputs["smartctl"] = subprocess.CompletedProcess(
        [],
        exit_code,
        json.dumps({"smart_status": {"passed": True}, "temperature": {"current": 31}}),
        "",
    )
    report = storage.smart_report("/dev/sda")
    assert report["health"] == health
    assert report["temperatureC"] == 31
    assert report["probeEvidence"][0]["state"] == state
    assert report["probeEvidence"][0]["exitCode"] == exit_code


@pytest.mark.parametrize(
    "payload", ["not-json", "[]", '{"smart_status":[]}', '{"smart_status":true}']
)
def test_malformed_smart_payload_is_an_evidenced_gap(commands, payload):
    outputs, _ = commands
    outputs["smartctl"] = payload
    report = storage.smart_report("/dev/sda")
    assert report["health"] == "UNKNOWN"
    assert report["probeEvidence"][0]["code"] == "parse_failed"


def test_smart_unsupported_is_not_an_all_clear(commands):
    outputs, _ = commands
    outputs["smartctl"] = '{"smart_support":{"available":false}}'
    report = storage.smart_report("/dev/sda")
    assert report["health"] == "UNKNOWN"
    assert report["available"] is False
    assert report["probeEvidence"][0]["code"] == "unsupported"


def test_background_smart_probe_preserves_rotational_disk_standby(commands):
    outputs, calls = commands
    outputs["lsblk"] = json.dumps({"blockdevices": [{**DISK, "rota": "1"}]})
    outputs["smartctl"] = json.dumps({"power_mode": "STANDBY"})

    disk_probe = storage._probe_block_devices()
    report = storage._probe_smart_devices(disk_probe.value)[0]

    assert calls[-1] == (
        "smartctl",
        "-j",
        "-n",
        "standby,0",
        "-H",
        "-i",
        "/dev/sda",
    )
    assert report.value["health"] == "UNKNOWN"
    assert report.value["powerState"] == "standby"
    assert report.evidence["state"] == "not-applicable"
    assert report.evidence["code"] == "device_standby"
    assert report.evidence["required"] is False
    assert report.evidence["count"] == 0

    health = storage.storage_health()
    assert health["state"] == "healthy"
    assert health["coverage"] == "complete"
    assert not any(item["code"].startswith("smart.") for item in health["activeAlerts"])


def test_explicit_smart_detail_read_may_wake_a_standby_disk(commands):
    outputs, calls = commands
    outputs["smartctl"] = json.dumps(
        {"smart_status": {"passed": True}, "temperature": {"current": 31}}
    )

    report = storage.smart_report("/dev/sda")

    assert calls[-1] == ("smartctl", "-j", "-H", "-A", "-i", "/dev/sda")
    assert report["health"] == "PASSED"


def test_command_wrapper_preserves_empty_output_failure_metadata(commands):
    outputs, _ = commands
    outputs["df"] = FileNotFoundError()
    output = run_readonly(("df", "-B1"), timeout=20)
    assert output == ""
    assert output.code == "tool_missing"


def _current_probes():
    disk = storage._probe_block_devices()
    filesystems = storage._probe_filesystems()
    md = storage._probe_md_arrays()
    zfs = storage._probe_zfs_pools()
    smart = storage._probe_smart_devices(disk.value)
    return [
        disk.evidence,
        filesystems.evidence,
        md.evidence,
        zfs.evidence,
        *(item.evidence for item in smart),
    ]


def test_complete_snapshot_requires_observations_but_no_arrays(commands):
    snapshot = health_observation(_current_probes(), [], checked_at="2026-09-05T01:00:00Z")
    assert snapshot["state"] == "healthy"
    assert snapshot["coverage"] == "complete"
    assert snapshot["stale"] is False
    assert snapshot["lastSuccessfulAt"] == snapshot["checkedAt"]
    assert snapshot["monitoring"] is False
    assert snapshot["persistenceHealthy"] is None


def test_partial_snapshot_does_not_fabricate_success_time(commands):
    outputs, _ = commands
    outputs["smartctl"] = PermissionError()
    snapshot = health_observation(_current_probes(), [], checked_at="2026-09-05T01:00:00Z")
    assert snapshot["state"] == "degraded"
    assert snapshot["coverage"] == "partial"
    assert snapshot["stale"] is True
    assert snapshot["lastSuccessfulAt"] is None
    assert snapshot["summary"]["critical"] == 0
    assert snapshot["available"] is True


def test_no_observation_is_unknown_not_a_bad_disk(commands):
    outputs, _ = commands
    outputs["lsblk"] = FileNotFoundError()
    outputs["df"] = FileNotFoundError()
    snapshot = health_observation(_current_probes(), [], checked_at="2026-09-05T01:00:00Z")
    assert snapshot["state"] == "unknown"
    assert snapshot["coverage"] == "none"
    assert snapshot["stale"] is True
    assert snapshot["lastSuccessfulAt"] is None
    assert snapshot["summary"]["critical"] == 0
    assert snapshot["available"] is False


def test_real_alert_is_not_hidden_by_an_unrelated_probe_failure(commands):
    outputs, _ = commands
    outputs["smartctl"] = FileNotFoundError()
    snapshot = health_observation(
        _current_probes(),
        [{"id": "full", "code": "filesystem.usage", "severity": "critical"}],
        checked_at="2026-09-05T01:00:00Z",
    )
    assert snapshot["state"] == "critical"
    assert snapshot["coverage"] == "partial"
    assert snapshot["stale"] is True


def test_partial_df_exit_keeps_capacity_and_its_command_error(commands):
    outputs, _ = commands
    outputs["df"] = subprocess.CompletedProcess([], 1, DF, "another volume failed")
    result = storage._probe_filesystems()
    assert result.value[0]["availableBytes"] == 9000
    assert result.evidence["code"] == "command_failed"
    snapshot = storage.storage_health()
    assert snapshot["state"] == "degraded"
    assert snapshot["filesystems"] == 1


def test_zfs_list_is_preserved_when_details_are_denied(commands):
    outputs, calls = commands
    outputs["zpool"] = lambda argv: (
        subprocess.CompletedProcess(argv, 0, "tank\t10000\t1000\t9000\tONLINE\t10\t0\n", "")
        if argv[1] == "list"
        else subprocess.CompletedProcess(argv, 1, "", "permission denied")
    )
    result = storage._probe_zfs_pools()
    assert result.value[0][0]["health"] == "ONLINE"
    assert result.evidence["state"] == "partial"
    assert result.evidence["code"] == "permission_denied"
    assert [call for call in calls if call[:2] == ("zpool", "status")] == [
        ("zpool", "status", "tank")
    ]


@pytest.mark.parametrize(
    "failure", [FileNotFoundError(), PermissionError(), subprocess.TimeoutExpired("smartctl", 25)]
)
def test_health_api_retains_inventory_while_reporting_smart_gap(commands, failure):
    outputs, _ = commands
    outputs["smartctl"] = failure
    app = FastAPI()
    router = APIRouter()
    register_native_storage_routes(router)
    app.include_router(router)
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        payload = response.json()
        assert payload["state"] == "degraded"
        assert payload["coverage"] == "partial"
        assert payload["devices"] == payload["filesystems"] == 1
        assert payload["lastSuccessfulAt"] is None
        assert payload["probeEvidence"][-1]["state"] in {"error", "unavailable"}
        assert client.get("/filesystems").json()["filesystems"][0]["availableBytes"] == 9000
        disk = client.get("/smart/devices").json()["devices"][0]
        assert disk["health"] == "UNKNOWN"
        assert disk["probeEvidence"][0]["state"] in {"error", "unavailable"}


def test_status_does_not_claim_empty_enumeration_is_available(commands):
    outputs, _ = commands
    outputs["lsblk"] = '{"blockdevices":[]}'
    payload = storage.status()
    assert payload["available"] is False
    assert payload["state"] == "unknown"
    assert payload["devices"] == 0
    assert payload["coverage"] == "none"


def test_health_alerts_preserve_critical_and_historical_smart_exit_bits(commands):
    outputs, _ = commands
    outputs["smartctl"] = subprocess.CompletedProcess(
        [], 8, '{"smart_status":{"passed":false}}', ""
    )
    critical = storage.storage_health()
    assert critical["state"] == "critical"
    assert critical["coverage"] == "complete"
    assert critical["summary"]["critical"] == 1
    outputs["smartctl"] = subprocess.CompletedProcess(
        [], 64, '{"smart_status":{"passed":true}}', ""
    )
    warning = storage.storage_health()
    assert warning["state"] == "warning"
    assert warning["coverage"] == "complete"
    assert warning["activeAlerts"][0]["code"] == "smart.history"


def test_zero_disks_with_capacity_stays_degraded_without_false_disk_failure(commands):
    outputs, _ = commands
    outputs["lsblk"] = '{"blockdevices":[]}'
    result = storage.storage_health()
    assert result["state"] == "degraded"
    assert result["devices"] == 0
    assert result["filesystems"] == 1
    assert result["summary"]["critical"] == 0


def test_unmounted_zfs_member_requires_the_missing_zfs_probe(commands):
    outputs, _ = commands
    outputs["lsblk"] = json.dumps({"blockdevices": [{**DISK, "fstype": "zfs_member"}]})
    health = storage.storage_health()
    assert health["state"] == "degraded"
    assert health["coverage"] == "partial"
    probe = next(item for item in health["probeEvidence"] if item["source"] == "zfs")
    assert probe["required"] is True
    assert probe["code"] == "tool_missing"
    assert storage.storage_topology()["coverage"] == "partial"


def test_denied_mount_options_are_a_partial_observation_not_green(commands):
    outputs, _ = commands
    outputs["findmnt"] = PermissionError()
    health = storage.storage_health()
    assert health["state"] == "degraded"
    assert health["coverage"] == "partial"
    probe = next(item for item in health["probeEvidence"] if item["source"] == "filesystems")
    assert probe["code"] == "permission_denied"
    assert storage.filesystems()[0]["mountOptionsKnown"] is False
    assert storage.filesystems()[0]["availableBytes"] == 9000


def test_faulted_zfs_detail_overrides_an_earlier_online_inventory(commands):
    outputs, _ = commands
    outputs["zpool"] = lambda argv: (
        "tank\t10000\t1000\t9000\tONLINE\t10\t0\n"
        if argv[1] == "list"
        else "  pool: tank\n state: FAULTED\n"
    )
    health = storage.storage_health()
    assert health["state"] == "critical"
    assert health["coverage"] == "partial"
    assert health["activeAlerts"][0]["code"] == "zfs.faulted"
    probe = next(item for item in health["probeEvidence"] if item["source"] == "zfs")
    assert probe["code"] == "conflicting_health"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("route", "function"),
    [
        ("/health", "storage_health"),
        ("/status", "status"),
        ("/topology", "storage_topology"),
        ("/filesystems", "filesystems"),
        ("/smart/devices", "smart_devices"),
        ("/sharing", "sharing_overview"),
        ("/smart?devicefile=/dev/sda", "smart_report"),
    ],
)
async def test_slow_native_reads_do_not_block_other_asgi_requests(monkeypatch, route, function):
    entered = threading.Event()
    release = threading.Event()

    def slow_read(*_args):
        entered.set()
        assert release.wait(10), "test did not release blocked probe"
        return {}

    monkeypatch.setattr(storage, function, slow_read)
    app = FastAPI()
    router = APIRouter()
    register_native_storage_routes(router)
    app.include_router(router)

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        blocked = asyncio.create_task(client.get(route))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            response = await asyncio.wait_for(client.get("/ping"), timeout=2)
            assert response.json() == {"ok": True}
            assert not blocked.done()
        finally:
            release.set()
            result = await asyncio.wait_for(blocked, timeout=5)
        assert result.status_code == 200
