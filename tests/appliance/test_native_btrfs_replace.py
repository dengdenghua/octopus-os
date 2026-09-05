from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from appliance import native_btrfs_replace as subject
from appliance import native_storage
from appliance.native_storage_routes import create_omv_alias_router
from appliance.omv_models import BtrfsReplaceDesiredState
from appliance.omv_protocol import (
    BTRFS_REPLACE_DESIRED_SCHEMA,
    validate_btrfs_replace_desired,
)

FS_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
DESIRED = {
    "schema": BTRFS_REPLACE_DESIRED_SCHEMA,
    "filesystemUuid": FS_UUID,
    "missingDevid": 2,
    "replacementDevice": "/dev/sdc",
    "dataPreserved": True,
}


def _filesystem(**overrides: Any) -> dict[str, Any]:
    return {
        "devicefile": FS_UUID,
        "uuid": FS_UUID,
        "mountpoint": "/data/family",
        "level": "btrfs-raid1",
        "status": "degraded",
        "totalDevices": 2,
        "activeDevices": 1,
        "missingDevices": 1,
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


def _topology() -> dict[str, Any]:
    return {
        "filesystem": _filesystem(),
        "missingMember": {
            "devid": 2,
            "sizeBytes": 0,
            "usedBytes": 0,
            "devicefile": None,
            "missing": True,
        },
        "survivingMember": {
            "devid": 1,
            "sizeBytes": 12 * 1024**3,
            "usedBytes": 1024,
            "devicefile": "/dev/sdb",
            "missing": False,
            "replaceTarget": False,
            "writeable": True,
            "errorStats": {},
            "errorCount": 0,
            "serial": "survivor",
            "wwn": None,
        },
        "minimumReplacementBytes": 12 * 1024**3,
        "scrub": {"kind": "scrub", "state": "completed"},
        "replace": {"kind": "deviceReplace", "state": "idle"},
        "topologyHash": "a" * 64,
        "scrubStatusHash": "b" * 64,
        "replaceStatusHash": "c" * 64,
    }


def _blank(size: int = 12 * 1024**3) -> dict[str, Any]:
    return {
        "devicefile": "/dev/sdc",
        "sizeBytes": size,
        "serial": "replacement",
        "wwn": None,
    }


def test_protocol_and_model_normalize_btrfs_replacement() -> None:
    assert validate_btrfs_replace_desired(DESIRED) == DESIRED
    assert BtrfsReplaceDesiredState.model_validate(DESIRED).model_dump(by_alias=True) == DESIRED


@pytest.mark.parametrize(
    "field,value",
    [
        ("filesystemUuid", "not-a-uuid"),
        ("missingDevid", True),
        ("missingDevid", 0),
        ("replacementDevice", "/dev/sdc1"),
        ("dataPreserved", False),
    ],
)
def test_protocol_rejects_expanded_or_unsafe_replacement(field: str, value: Any) -> None:
    with pytest.raises(ValueError):
        validate_btrfs_replace_desired({**DESIRED, field: value})
    with pytest.raises(ValueError):
        BtrfsReplaceDesiredState.model_validate({**DESIRED, field: value})
    with pytest.raises(ValueError):
        validate_btrfs_replace_desired({**DESIRED, "force": True})


@pytest.mark.parametrize(
    "missing_path", ["MISSING", "<missing disk> MISSING", "missing", "/dev/sdc MISSING"]
)
def test_filesystem_show_requires_two_members_and_accepts_real_missing_marker(
    missing_path: str,
) -> None:
    output = (
        f"Label: 'family'  uuid: {FS_UUID}\n"
        "\tTotal devices 2 FS bytes used 4096\n"
        "\tdevid    1 size 12884901888 used 4096 path /dev/sdb\n"
        f"\tdevid    2 size 0 used 0 path  {missing_path}\n"
        "*** Some devices missing\n"
    )

    parsed = subject._parse_filesystem_show(output, expected_uuid=FS_UUID)

    assert parsed["members"][1] == {
        "devid": 2,
        "sizeBytes": 0,
        "usedBytes": 0,
        "devicefile": None,
        "missing": True,
    }


@pytest.mark.parametrize(
    "output,state,progress,errors",
    [
        ("Never started\n", "idle", None, None),
        ("12.7% done, 0 write errs, 1 uncorr. read errs\n", "inProgress", 12, 1),
        (
            "Started on 1.Sep 12:00:00, finished on 1.Sep 12:03:00, "
            "0 write errs, 0 uncorr. read errs\n",
            "completed",
            None,
            0,
        ),
        ("Started on 1.Sep, canceled on 1.Sep\n", "failed", None, None),
    ],
)
def test_replace_status_parser_is_bounded(
    output: str, state: str, progress: int | None, errors: int | None
) -> None:
    assert subject._parse_replace_status(output) == {
        "kind": "deviceReplace",
        "state": state,
        "progressPercent": progress,
        "errors": errors,
    }


def test_replace_status_parser_rejects_unknown_output() -> None:
    with pytest.raises(OSError, match="unknown state"):
        subject._parse_replace_status("status maybe running")


def test_replace_status_is_a_single_nonblocking_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, ...]] = []

    def run(*args: str) -> Any:
        calls.append(args)
        return type("Result", (), {"returncode": 0, "stdout": "Never started\n", "stderr": ""})()

    monkeypatch.setattr(subject, "_run", run)

    status, status_hash = subject._replace_status("/data/family")

    assert calls == [("btrfs", "replace", "status", "-1", "/data/family")]
    assert status["state"] == "idle"
    assert len(status_hash) == 64


def test_sysfs_snapshot_reads_missing_member_and_rejects_errors(tmp_path: Path) -> None:
    filesystem = tmp_path / FS_UUID
    for devid, missing, writeable in ((1, 0, 1), (2, 1, 0)):
        member = filesystem / "devinfo" / str(devid)
        member.mkdir(parents=True)
        (member / "missing").write_text(f"{missing}\n", encoding="ascii")
        (member / "replace_target").write_text("0\n", encoding="ascii")
        (member / "writeable").write_text(f"{writeable}\n", encoding="ascii")
        (member / "error_stats").write_text(
            "write_errs 0 read_errs 0 flush_errs 0 corruption_errs 0 generation_errs 0\n",
            encoding="ascii",
        )
    (filesystem / "exclusive_operation").write_text("none\n", encoding="ascii")

    snapshot = subject._sysfs_snapshot(FS_UUID, sysfs_root=tmp_path)

    assert snapshot["exclusiveOperation"] == "none"
    assert snapshot["members"][1]["missing"] is True
    assert snapshot["members"][0]["writeable"] is True

    (filesystem / "devinfo" / "1" / "error_stats").write_text(
        "write_errs 1 read_errs 0 flush_errs 0 corruption_errs 0 generation_errs 0\n",
        encoding="ascii",
    )
    assert subject._sysfs_snapshot(FS_UUID, sysfs_root=tmp_path)["members"][0]["errorCount"] == 1


@pytest.mark.parametrize("kernel_reports_missing", [True, False])
def test_degraded_topology_binds_echo_mount_kernel_layout_and_maintenance(
    monkeypatch: pytest.MonkeyPatch,
    kernel_reports_missing: bool,
) -> None:
    kernel = {
        "exclusiveOperation": "none",
        "members": [
            {
                "devid": 1,
                "missing": False,
                "replaceTarget": False,
                "writeable": True,
                "errorStats": {},
                "errorCount": 0,
            },
            {
                "devid": 2,
                "missing": kernel_reports_missing,
                "replaceTarget": False,
                "writeable": not kernel_reports_missing,
                "errorStats": {"write_errs": 6, "flush_errs": 1},
                "errorCount": 7,
            },
        ],
    }
    layout = {
        "totalDevices": 2,
        "members": [
            {
                "devid": 1,
                "sizeBytes": 12 * 1024**3,
                "usedBytes": 1,
                "devicefile": "/dev/sdb",
                "missing": False,
            },
            {
                "devid": 2,
                "sizeBytes": 0,
                "usedBytes": 0,
                "devicefile": None,
                "missing": True,
            },
        ],
    }
    monkeypatch.setattr(
        subject,
        "managed_btrfs_filesystems",
        lambda **_kwargs: [{"uuid": FS_UUID, "mountpoint": "/data/family"}],
    )
    monkeypatch.setattr(subject, "_health_inventory", lambda: [_filesystem()])
    monkeypatch.setattr(subject, "_sysfs_snapshot", lambda *_args, **_kwargs: kernel)
    monkeypatch.setattr(subject, "_run_checked", lambda *_args: "show")
    monkeypatch.setattr(subject, "_parse_filesystem_show", lambda *_args, **_kwargs: layout)
    monkeypatch.setattr(
        subject,
        "_existing_disk_identity",
        lambda path: {
            "devicefile": path,
            "sizeBytes": 12 * 1024**3,
            "serial": "survivor",
            "wwn": None,
        },
    )
    monkeypatch.setattr(
        subject,
        "_scrub_status",
        lambda *_args: ({"kind": "scrub", "state": "completed"}, "scrub-hash"),
    )
    monkeypatch.setattr(
        subject,
        "_replace_status",
        lambda *_args: ({"kind": "deviceReplace", "state": "idle"}, "replace-hash"),
    )

    topology = subject._degraded_topology(
        FS_UUID, fstab_path=Path("fstab"), sysfs_root=Path("sysfs")
    )

    assert topology["missingMember"]["devid"] == 2
    assert topology["minimumReplacementBytes"] == 12 * 1024**3
    assert topology["survivingMember"]["serial"] == "survivor"

    # A live detach records expected write/flush failures against the missing
    # path in the aggregate health probe. Per-member sysfs counters above keep
    # the surviving member fail-closed without making repair impossible.
    filesystem = _filesystem(deviceErrorCount=7)
    monkeypatch.setattr(subject, "_health_inventory", lambda: [filesystem])
    assert (
        subject._degraded_topology(FS_UUID, fstab_path=Path("fstab"), sysfs_root=Path("sysfs"))[
            "missingMember"
        ]["devid"]
        == 2
    )

    kernel["members"][0]["errorCount"] = 1
    with pytest.raises(ValueError, match="writable surviving"):
        subject._degraded_topology(FS_UUID, fstab_path=Path("fstab"), sysfs_root=Path("sysfs"))
    kernel["members"][0]["errorCount"] = 0

    kernel["exclusiveOperation"] = "balance"
    with pytest.raises(ValueError, match="active exclusive"):
        subject._degraded_topology(FS_UUID, fstab_path=Path("fstab"), sysfs_root=Path("sysfs"))


def test_plan_binds_topology_and_blank_replacement(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subject, "_require_tools", lambda: None)
    monkeypatch.setattr(subject, "_degraded_topology", lambda *_args, **_kwargs: _topology())
    monkeypatch.setattr(subject, "_blank_disk_candidates", lambda: [_blank()])

    plan = subject._build_plan(DESIRED, fstab_path=Path("fstab"), sysfs_root=Path("sysfs"))

    assert plan["requiresApproval"] is True
    assert plan["replacement"]["serial"] == "replacement"
    assert plan["missingMember"]["devid"] == 2
    assert plan["safety"]["force"] is False
    assert plan["safety"]["autoResize"] is False
    assert plan["safety"]["rollback"] == "noneAfterReplacementAccepted"
    assert len(plan["planId"]) == 64


def test_plan_rejects_smaller_replacement(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subject, "_require_tools", lambda: None)
    monkeypatch.setattr(subject, "_degraded_topology", lambda *_args, **_kwargs: _topology())
    monkeypatch.setattr(subject, "_blank_disk_candidates", lambda: [_blank(9 * 1024**3)])

    with pytest.raises(ValueError, match="smaller"):
        subject._build_plan(DESIRED, fstab_path=Path("fstab"), sysfs_root=Path("sysfs"))


def test_apply_starts_numeric_missing_devid_without_force_or_resize(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = {
        "planId": "d" * 64,
        "desired": DESIRED,
        "filesystem": {"uuid": FS_UUID, "mountpoint": "/data/family"},
        "replacement": _blank(),
        "_replaceStatusHash": "before",
    }
    monkeypatch.setattr(subject, "btrfs_volume_transaction", nullcontext)
    monkeypatch.setattr(subject, "_build_plan", lambda *_args, **_kwargs: dict(plan))
    commands: list[tuple[str, ...]] = []
    monkeypatch.setattr(subject, "_run_mutating", lambda *args: commands.append(args))
    monkeypatch.setattr(
        subject,
        "_replacement_accepted",
        lambda *_args, **_kwargs: (
            {
                "kind": "deviceReplace",
                "state": "inProgress",
                "progressPercent": 1,
                "errors": 0,
            },
            "replacing",
        ),
    )

    result = subject.apply_btrfs_replace(DESIRED, plan["planId"])

    assert commands == [("btrfs", "replace", "start", "2", "/dev/sdc", "/data/family")]
    assert not {"-f", "--force", "-r", "-B", "--enqueue", "resize"} & set(commands[0])
    assert result["verified"] is True
    assert result["dataPreserved"] is True
    assert result["maintenanceState"] == "replacing"


def test_replacement_acceptance_requires_status_transition_and_kernel_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = {
        "desired": DESIRED,
        "filesystem": {"uuid": FS_UUID, "mountpoint": "/data/family"},
        "replacement": _blank(),
    }
    status = {
        "kind": "deviceReplace",
        "state": "inProgress",
        "progressPercent": 2,
        "errors": 0,
    }
    kernel = {
        "exclusiveOperation": "device replace",
        "members": [{"devid": 0, "replaceTarget": True}],
    }
    monkeypatch.setattr(subject, "_replace_status", lambda _mount: (status, "after"))
    monkeypatch.setattr(subject, "_sysfs_snapshot", lambda *_args, **_kwargs: kernel)

    assert subject._replacement_accepted(
        plan, previous_status_hash="before", sysfs_root=Path("sysfs")
    ) == (status, "replacing")
    assert (
        subject._replacement_accepted(plan, previous_status_hash="after", sysfs_root=Path("sysfs"))
        is None
    )


def test_replacement_acceptance_falls_back_to_kernel_after_status_read_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = {
        "desired": DESIRED,
        "filesystem": {"uuid": FS_UUID, "mountpoint": "/data/family"},
        "replacement": _blank(),
    }
    monkeypatch.setattr(
        subject,
        "_replace_status",
        lambda _mount: (_ for _ in ()).throw(OSError("temporarily unavailable")),
    )
    monkeypatch.setattr(
        subject,
        "_sysfs_snapshot",
        lambda *_args, **_kwargs: {
            "exclusiveOperation": "device replace",
            "members": [{"devid": 0, "replaceTarget": True}],
        },
    )

    accepted = subject._replacement_accepted(
        plan, previous_status_hash="before", sysfs_root=Path("sysfs")
    )

    assert accepted is not None
    assert accepted[0]["state"] == "inProgress"
    assert accepted[1] == "replacing"


def test_apply_accepts_transition_after_late_command_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = {
        "planId": "d" * 64,
        "desired": DESIRED,
        "filesystem": {"uuid": FS_UUID, "mountpoint": "/data/family"},
        "replacement": _blank(),
        "_replaceStatusHash": "before",
    }
    monkeypatch.setattr(subject, "btrfs_volume_transaction", nullcontext)
    monkeypatch.setattr(subject, "_build_plan", lambda *_args, **_kwargs: dict(plan))
    monkeypatch.setattr(
        subject,
        "_run_mutating",
        lambda *_args: (_ for _ in ()).throw(OSError("client disconnected")),
    )
    monkeypatch.setattr(
        subject,
        "_replacement_accepted",
        lambda *_args, **_kwargs: (
            {"kind": "deviceReplace", "state": "completed"},
            "acceptedOrCompleted",
        ),
    )

    result = subject.apply_btrfs_replace(DESIRED, plan["planId"])

    assert result["maintenanceState"] == "acceptedOrCompleted"


def test_apply_polls_until_fast_replacement_state_settles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = {
        "planId": "d" * 64,
        "desired": DESIRED,
        "filesystem": {"uuid": FS_UUID, "mountpoint": "/data/family"},
        "replacement": _blank(),
        "_replaceStatusHash": "before",
    }
    responses = iter(
        [
            OSError("sysfs is settling"),
            None,
            (
                {"kind": "deviceReplace", "state": "completed", "errors": 0},
                "acceptedOrCompleted",
            ),
        ]
    )
    sleeps: list[float] = []
    monkeypatch.setattr(subject, "btrfs_volume_transaction", nullcontext)
    monkeypatch.setattr(subject, "_build_plan", lambda *_args, **_kwargs: dict(plan))
    monkeypatch.setattr(subject, "_run_mutating", lambda *_args: None)

    def accepted(*_args, **_kwargs):
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(subject, "_replacement_accepted", accepted)
    monkeypatch.setattr(subject.time, "sleep", sleeps.append)

    result = subject.apply_btrfs_replace(DESIRED, plan["planId"])

    assert result["maintenanceState"] == "acceptedOrCompleted"
    assert sleeps == [subject._ACCEPTANCE_POLL_SECONDS] * 2


def test_apply_rejects_stale_plan_before_mutation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subject, "btrfs_volume_transaction", nullcontext)
    monkeypatch.setattr(
        subject,
        "_build_plan",
        lambda *_args, **_kwargs: {"planId": "e" * 64, "_replaceStatusHash": "before"},
    )
    mutated = False

    def mutate(*_args: str) -> None:
        nonlocal mutated
        mutated = True

    monkeypatch.setattr(subject, "_run_mutating", mutate)
    with pytest.raises(ValueError, match="stale"):
        subject.apply_btrfs_replace(DESIRED, "f" * 64)
    assert mutated is False


def test_native_alias_binds_replacement_to_exact_approval_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ECHO_APPLIANCE", raising=False)
    plan_id = "c" * 64
    current_plan = {"planId": plan_id, "requiresApproval": True}
    approvals: list[dict[str, Any]] = []

    class Approval:
        def consume(self, **kwargs: Any) -> None:
            approvals.append(kwargs)

    class Audit:
        def record(self, **_kwargs: Any) -> None:
            pass

    monkeypatch.setattr(native_storage, "plan_btrfs_replace", lambda _desired: current_plan)
    monkeypatch.setattr(
        native_storage,
        "apply_btrfs_replace",
        lambda _desired, _plan_id: {**current_plan, "applied": True, "verified": True},
    )
    app = FastAPI()
    app.include_router(create_omv_alias_router(approval=Approval(), audit=Audit()))

    response = TestClient(app).post(
        "/api/appliance/omv/volumes/btrfs-raid1/replace/apply",
        json={"desired": DESIRED, "planId": plan_id},
        headers={"X-Echo-Approval": "approval-token"},
    )

    assert response.status_code == 200
    assert approvals[0]["action"] == "omv.btrfs-raid1.replace"


def test_native_alias_exposes_btrfs_replacement_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ECHO_APPLIANCE", raising=False)
    expected = [{"filesystem": _filesystem(), "replacementDevices": [_blank()]}]
    monkeypatch.setattr(native_storage, "btrfs_replacement_candidates", lambda: expected)
    app = FastAPI()
    app.include_router(create_omv_alias_router())

    response = TestClient(app).get("/api/appliance/omv/volumes/btrfs-raid1/replacement-candidates")

    assert response.status_code == 200
    assert response.json() == {"replacements": expected, "readOnly": True, "source": "native"}
