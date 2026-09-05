from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from appliance import native_btrfs, native_btrfs_scrub, native_storage
from appliance.native_storage_routes import create_omv_alias_router
from appliance.omv_protocol import validate_btrfs_scrub_desired

FS_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _desired(**overrides: Any) -> dict[str, Any]:
    return {
        "schema": "echo.omv.btrfs-scrub-desired.v1",
        "filesystemUuid": FS_UUID,
        "operation": "start",
        **overrides,
    }


def _filesystem(**overrides: Any) -> dict[str, Any]:
    return {
        "devicefile": FS_UUID,
        "uuid": FS_UUID,
        "mountpoint": "/data/family",
        "level": "btrfs-raid1",
        "status": "healthy",
        "totalDevices": 2,
        "activeDevices": 2,
        "missingDevices": 0,
        "dataProfile": "raid1",
        "metadataProfile": "raid1",
        "operation": None,
        "operationPercent": None,
        "deviceErrors": {},
        "deviceErrorCount": 0,
        "readOnly": False,
        "kind": "btrfs",
        **overrides,
    }


@pytest.fixture
def scrub_host(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    etc = tmp_path / "etc"
    etc.mkdir()
    fstab = etc / "fstab"
    fstab.write_text(
        "# BEGIN ECHO OS MANAGED BTRFS\n"
        f"UUID={FS_UUID} /data/family btrfs "
        "defaults,nofail,x-systemd.device-timeout=30s 0 0\n"
        "# END ECHO OS MANAGED BTRFS\n",
        encoding="utf-8",
    )
    state: dict[str, Any] = {
        "scan": {"kind": "scrub", "state": "idle", "progressPercent": None, "errors": None},
        "hash": "before",
    }
    monkeypatch.setattr(native_btrfs, "_LOCK_PATH", tmp_path / "btrfs.lock")
    monkeypatch.setattr(native_btrfs_scrub, "_require_tools", lambda: None)
    monkeypatch.setattr(native_btrfs_scrub, "_health_inventory", lambda: [_filesystem()])
    monkeypatch.setattr(native_btrfs_scrub, "_exclusive_operation", lambda _uuid: "none")
    monkeypatch.setattr(
        native_btrfs_scrub,
        "_scrub_status",
        lambda _mountpoint, _uuid: (dict(state["scan"]), state["hash"]),
    )
    return fstab, state


@pytest.mark.parametrize(
    "payload",
    [
        _desired(operation="cancel"),
        _desired(filesystemUuid="not-a-uuid"),
        {**_desired(), "force": True},
    ],
)
def test_btrfs_scrub_protocol_rejects_expanded_or_unsafe_state(
    payload: dict[str, Any],
) -> None:
    with pytest.raises(ValueError):
        validate_btrfs_scrub_desired(payload)


def test_managed_btrfs_filesystems_reads_only_owned_fstab_block(tmp_path: Path) -> None:
    fstab = tmp_path / "fstab"
    fstab.write_text(
        "/dev/external /mnt/external btrfs defaults 0 0\n"
        "# BEGIN ECHO OS MANAGED BTRFS\n"
        f"UUID={FS_UUID} /data/family btrfs "
        "defaults,nofail,x-systemd.device-timeout=30s 0 0\n"
        "# END ECHO OS MANAGED BTRFS\n",
        encoding="utf-8",
    )

    assert native_btrfs.managed_btrfs_filesystems(fstab_path=fstab) == [
        {"uuid": FS_UUID, "mountpoint": "/data/family"}
    ]


def test_btrfs_scrub_plan_binds_managed_healthy_raid1(
    scrub_host: tuple[Path, dict[str, Any]],
) -> None:
    fstab, _state = scrub_host
    plan = native_btrfs_scrub.plan_btrfs_scrub(_desired(), fstab_path=fstab)

    assert plan["schema"] == "echo.omv.btrfs-scrub-plan.v1"
    assert plan["operation"] == "start"
    assert plan["filesystem"] == _filesystem()
    assert plan["before"]["state"] == "idle"
    assert plan["exclusiveOperation"] == "none"
    assert plan["requiresApproval"] is True
    assert plan["safety"]["readOnly"] is False
    assert plan["safety"]["force"] is False
    assert "_statusHash" not in plan


def test_btrfs_maintenance_exposes_startability_without_internal_hash(
    scrub_host: tuple[Path, dict[str, Any]],
) -> None:
    fstab, _state = scrub_host

    records = native_btrfs_scrub.btrfs_scrub_maintenance(fstab_path=fstab)

    assert records[0]["canStartScrub"] is True
    assert records[0]["scan"]["state"] == "idle"
    assert records[0]["exclusiveOperation"] == "none"
    assert not any(key.startswith("_") for key in records[0])


@pytest.mark.parametrize(
    "filesystem,scan_state",
    [
        (_filesystem(missingDevices=1, activeDevices=1, status="degraded"), "idle"),
        (_filesystem(readOnly=True, status="warning"), "idle"),
        (_filesystem(dataProfile="single", level="btrfs-mixed"), "idle"),
        (_filesystem(), "inProgress"),
    ],
)
def test_btrfs_scrub_plan_rejects_unsafe_topology_or_active_scrub(
    monkeypatch: pytest.MonkeyPatch,
    scrub_host: tuple[Path, dict[str, Any]],
    filesystem: dict[str, Any],
    scan_state: str,
) -> None:
    fstab, state = scrub_host
    monkeypatch.setattr(native_btrfs_scrub, "_health_inventory", lambda: [filesystem])
    state["scan"] = {
        "kind": "scrub",
        "state": scan_state,
        "progressPercent": 10 if scan_state == "inProgress" else None,
        "errors": None,
    }

    with pytest.raises(ValueError, match="complete writable RAID1"):
        native_btrfs_scrub.plan_btrfs_scrub(_desired(), fstab_path=fstab)


def test_btrfs_scrub_rejects_an_active_balance(
    monkeypatch: pytest.MonkeyPatch,
    scrub_host: tuple[Path, dict[str, Any]],
) -> None:
    fstab, _state = scrub_host
    monkeypatch.setattr(native_btrfs_scrub, "_exclusive_operation", lambda _uuid: "balance")

    records = native_btrfs_scrub.btrfs_scrub_maintenance(fstab_path=fstab)

    assert records[0]["exclusiveOperation"] == "balance"
    assert records[0]["canStartScrub"] is False
    with pytest.raises(ValueError, match="exclusive operation"):
        native_btrfs_scrub.plan_btrfs_scrub(_desired(), fstab_path=fstab)


def test_btrfs_scrub_apply_starts_without_force_wait_cancel_or_read_only(
    monkeypatch: pytest.MonkeyPatch,
    scrub_host: tuple[Path, dict[str, Any]],
) -> None:
    fstab, state = scrub_host
    commands: list[tuple[str, ...]] = []

    def mutate(*args: str, **_kwargs: Any) -> None:
        commands.append(args)
        state["scan"] = {
            "kind": "scrub",
            "state": "inProgress",
            "progressPercent": 1,
            "errors": 0,
        }
        state["hash"] = "after"

    monkeypatch.setattr(native_btrfs_scrub, "_run_mutating", mutate)
    desired = _desired()
    plan = native_btrfs_scrub.plan_btrfs_scrub(desired, fstab_path=fstab)
    result = native_btrfs_scrub.apply_btrfs_scrub(desired, plan["planId"], fstab_path=fstab)

    assert commands == [("btrfs", "scrub", "start", "/data/family")]
    assert not {"-f", "--force", "-B", "-r", "cancel"} & set(commands[0])
    assert result["verified"] is True
    assert result["maintenanceState"] == "scrubbing"
    assert result["scan"]["state"] == "inProgress"


def test_btrfs_scrub_apply_accepts_instant_completion_after_late_cli_failure(
    monkeypatch: pytest.MonkeyPatch,
    scrub_host: tuple[Path, dict[str, Any]],
) -> None:
    fstab, state = scrub_host

    def mutate(*_args: str, **_kwargs: Any) -> None:
        state["scan"] = {
            "kind": "scrub",
            "state": "completed",
            "progressPercent": 100,
            "errors": 0,
        }
        state["hash"] = "after"
        raise OSError("client timed out after command was accepted")

    monkeypatch.setattr(native_btrfs_scrub, "_run_mutating", mutate)
    desired = _desired()
    plan = native_btrfs_scrub.plan_btrfs_scrub(desired, fstab_path=fstab)
    result = native_btrfs_scrub.apply_btrfs_scrub(desired, plan["planId"], fstab_path=fstab)

    assert result["maintenanceState"] == "completed"


def test_btrfs_scrub_transition_polls_past_transient_no_stats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    statuses = iter(
        [
            (
                {"kind": "scrub", "state": "idle", "progressPercent": None, "errors": None},
                "before",
            ),
            (
                {
                    "kind": "scrub",
                    "state": "completed",
                    "progressPercent": None,
                    "errors": 0,
                },
                "after",
            ),
        ]
    )
    sleeps: list[float] = []
    monkeypatch.setattr(native_btrfs_scrub, "_scrub_status", lambda *_args: next(statuses))

    transition = native_btrfs_scrub._verified_transition(
        "/data/family",
        FS_UUID,
        previous_status_hash="before",
        attempts=2,
        sleeper=sleeps.append,
    )

    assert transition == (
        {
            "kind": "scrub",
            "state": "completed",
            "progressPercent": None,
            "errors": 0,
        },
        "completed",
    )
    assert sleeps == [0.25]


def test_scheduled_btrfs_scrub_waits_and_accepts_identical_completed_status(
    monkeypatch: pytest.MonkeyPatch,
    scrub_host: tuple[Path, dict[str, Any]],
) -> None:
    fstab, state = scrub_host
    state["scan"] = {
        "kind": "scrub",
        "state": "completed",
        "progressPercent": None,
        "errors": 0,
    }
    state["hash"] = "same-low-resolution-status"
    commands: list[tuple[tuple[str, ...], float]] = []

    def run(*args: str, timeout: float = 120.0) -> SimpleNamespace:
        commands.append((args, timeout))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(native_btrfs_scrub, "_run", run)
    monkeypatch.setattr(
        native_btrfs_scrub,
        "_run_mutating",
        lambda *_args, **_kwargs: pytest.fail("scheduled scrub must use foreground execution"),
    )
    desired = _desired()
    plan = native_btrfs_scrub.plan_btrfs_scrub(
        desired,
        fstab_path=fstab,
        wait_for_completion=True,
    )
    result = native_btrfs_scrub.apply_btrfs_scrub(
        desired,
        plan["planId"],
        fstab_path=fstab,
        wait_for_completion=True,
    )

    assert plan["execution"] == {"waitForCompletion": True}
    assert plan["safety"]["wait"] is True
    assert result["maintenanceState"] == "completed"
    assert commands == [
        (
            ("btrfs", "scrub", "start", "-B", "/data/family"),
            native_btrfs_scrub._SCHEDULED_SCRUB_TIMEOUT_SECONDS,
        )
    ]


def test_btrfs_scrub_apply_rejects_stale_status(
    scrub_host: tuple[Path, dict[str, Any]],
) -> None:
    fstab, state = scrub_host
    plan = native_btrfs_scrub.plan_btrfs_scrub(_desired(), fstab_path=fstab)
    state["scan"] = {
        "kind": "scrub",
        "state": "completed",
        "progressPercent": 100,
        "errors": 0,
    }
    state["hash"] = "changed-before-apply"

    with pytest.raises(ValueError, match="stale"):
        native_btrfs_scrub.apply_btrfs_scrub(_desired(), plan["planId"], fstab_path=fstab)


def test_btrfs_scrub_apply_rechecks_exclusive_operation(
    monkeypatch: pytest.MonkeyPatch,
    scrub_host: tuple[Path, dict[str, Any]],
) -> None:
    fstab, _state = scrub_host
    operation = {"value": "none"}
    monkeypatch.setattr(
        native_btrfs_scrub,
        "_exclusive_operation",
        lambda _uuid: operation["value"],
    )
    plan = native_btrfs_scrub.plan_btrfs_scrub(_desired(), fstab_path=fstab)
    operation["value"] = "resize"

    with pytest.raises(ValueError, match="exclusive operation"):
        native_btrfs_scrub.apply_btrfs_scrub(_desired(), plan["planId"], fstab_path=fstab)


def test_btrfs_scrub_status_parser_is_bounded_and_structured() -> None:
    output = (
        f"UUID:             {FS_UUID}\n"
        "Scrub started:    Sat Sep  5 12:00:00 2026\n"
        "Status:           running\n"
        "Bytes scrubbed:   1000  (48.59%)\n"
        "Error summary:    csum=2 verify=1\n"
    )

    assert native_btrfs_scrub._parse_scrub_status(output, expected_uuid=FS_UUID) == {
        "kind": "scrub",
        "state": "inProgress",
        "progressPercent": 48,
        "errors": 3,
    }

    completed = (
        f"UUID:             {FS_UUID}\n"
        "Status:           finished\n"
        "Error summary:    no errors found\n"
    )
    assert native_btrfs_scrub._parse_scrub_status(completed, expected_uuid=FS_UUID) == {
        "kind": "scrub",
        "state": "completed",
        "progressPercent": None,
        "errors": 0,
    }


def test_btrfs_scrub_status_treats_no_prior_stats_as_idle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        native_btrfs_scrub,
        "_run",
        lambda *_args: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="ERROR: no stats available for this filesystem",
        ),
    )

    snapshot, status_hash = native_btrfs_scrub._scrub_status("/data/family", FS_UUID)

    assert snapshot["state"] == "idle"
    assert len(status_hash) == 64


def test_btrfs_scrub_status_preserves_completed_error_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = f"UUID:             {FS_UUID}\nStatus:           finished\nError summary:    csum=2\n"
    monkeypatch.setattr(
        native_btrfs_scrub,
        "_run",
        lambda *_args: SimpleNamespace(returncode=3, stdout=output, stderr=""),
    )

    snapshot, _status_hash = native_btrfs_scrub._scrub_status("/data/family", FS_UUID)

    assert snapshot == {
        "kind": "scrub",
        "state": "completed",
        "progressPercent": None,
        "errors": 2,
    }


def test_native_alias_binds_btrfs_scrub_to_exact_approval_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ECHO_APPLIANCE", raising=False)
    plan_id = "c" * 64
    current_plan = {"planId": plan_id, "operation": "start", "requiresApproval": True}
    approvals: list[dict[str, Any]] = []

    class Approval:
        def consume(self, **kwargs: Any) -> None:
            approvals.append(kwargs)

    class Audit:
        def record(self, **_kwargs: Any) -> None:
            pass

    monkeypatch.setattr(native_storage, "plan_btrfs_scrub", lambda _desired: current_plan)
    monkeypatch.setattr(
        native_storage,
        "apply_btrfs_scrub",
        lambda _desired, _plan_id: {**current_plan, "applied": True, "verified": True},
    )
    app = FastAPI()
    app.include_router(create_omv_alias_router(approval=Approval(), audit=Audit()))

    response = TestClient(app).post(
        "/api/appliance/omv/volumes/btrfs-raid1/scrub/apply",
        json={"desired": _desired(), "planId": plan_id},
        headers={"X-Echo-Approval": "approval-token"},
    )

    assert response.status_code == 200
    assert approvals[0]["action"] == "omv.btrfs.scrub.start"


def test_native_alias_exposes_btrfs_maintenance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ECHO_APPLIANCE", raising=False)
    expected = [{"filesystem": _filesystem(), "scan": {"state": "idle"}}]
    monkeypatch.setattr(native_storage, "btrfs_scrub_maintenance", lambda: expected)
    app = FastAPI()
    app.include_router(create_omv_alias_router())

    response = TestClient(app).get("/api/appliance/omv/volumes/btrfs-raid1/maintenance")

    assert response.status_code == 200
    assert response.json() == {"filesystems": expected, "readOnly": True, "source": "native"}
