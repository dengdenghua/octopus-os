from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from appliance import native_mdraid_replace as subject
from appliance.omv_models import MdRaid1ReplaceDesiredState
from appliance.omv_protocol import (
    MDRAID1_REPLACE_DESIRED_SCHEMA,
    validate_mdraid1_replace_desired,
)

ARRAY_UUID = "11111111:22222222:33333333:44444444"
DESIRED = {
    "schema": MDRAID1_REPLACE_DESIRED_SCHEMA,
    "name": "family",
    "arrayUuid": ARRAY_UUID,
    "replacementDevice": "/dev/sdc",
    "dataPreserved": True,
}


def _topology(*, failed: bool = True) -> dict[str, Any]:
    return {
        "array": {
            "name": "family",
            "devicefile": "/dev/md/echo-family",
            "uuid": ARRAY_UUID,
            "configSha256": "a" * 64,
        },
        "kernelDevice": "md127",
        "syncAction": "idle",
        "survivingMember": {
            "devicefile": "/dev/sdb",
            "slot": 0,
            "states": ["in_sync"],
            "sizeBytes": 10 * 1024**3,
            "serial": "survivor",
            "wwn": None,
        },
        "failedMember": (
            {"devicefile": "/dev/sdd", "slot": None, "states": ["faulty"]} if failed else None
        ),
        "missingSlot": 1,
        "minimumReplacementBytes": 10 * 1024**3,
        "topologyHash": "b" * 64,
    }


def _blank(size: int = 12 * 1024**3) -> dict[str, Any]:
    return {
        "devicefile": "/dev/sdc",
        "sizeBytes": size,
        "serial": "replacement",
        "wwn": None,
    }


def test_protocol_and_model_normalize_replacement() -> None:
    assert validate_mdraid1_replace_desired(DESIRED) == DESIRED
    model = MdRaid1ReplaceDesiredState.model_validate(DESIRED)
    assert model.model_dump(by_alias=True) == DESIRED


@pytest.mark.parametrize(
    "field,value",
    [
        ("arrayUuid", "not-an-md-uuid"),
        ("replacementDevice", "/dev/sdc1"),
        ("dataPreserved", False),
    ],
)
def test_protocol_rejects_unsafe_replacement(field: str, value: Any) -> None:
    desired = {**DESIRED, field: value}
    with pytest.raises(ValueError):
        validate_mdraid1_replace_desired(desired)


def test_degraded_topology_requires_one_survivor_and_idle_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        subject,
        "_configured_identity",
        lambda *_args: {
            "name": "family",
            "devicefile": "/dev/md/echo-family",
            "uuid": ARRAY_UUID,
            "configSha256": "a" * 64,
        },
    )
    monkeypatch.setattr(subject, "_run", lambda *_args: SimpleNamespace(returncode=1))
    monkeypatch.setattr(
        subject,
        "_run_checked",
        lambda *_args: "\n".join(
            (
                "MD_LEVEL=raid1",
                "MD_DEVICES=2",
                "MD_METADATA=1.2",
                "MD_DEVNAME=echo-family",
                f"MD_UUID={ARRAY_UUID}",
                "MD_RESHAPE_ACTIVE=False",
            )
        ),
    )
    kernel = {
        "kernelDevice": "md127",
        "syncAction": "idle",
        "componentSizeSectors": 1024,
        "members": [
            {"devicefile": "/dev/sdb", "slot": 0, "states": ["in_sync"]},
            {"devicefile": "/dev/sdd", "slot": None, "states": ["faulty"]},
        ],
    }
    monkeypatch.setattr(subject, "_sysfs_snapshot", lambda _target: kernel)
    monkeypatch.setattr(
        subject,
        "_existing_disk_identity",
        lambda path: {
            "devicefile": path,
            "sizeBytes": 10 * 1024**3,
            "serial": "survivor",
            "wwn": None,
        },
    )

    result = subject._degraded_topology("family", ARRAY_UUID, Path("mdadm.conf"))
    assert result["missingSlot"] == 1
    assert result["failedMember"]["devicefile"] == "/dev/sdd"

    kernel["syncAction"] = "recover"
    with pytest.raises(ValueError, match="active recovery"):
        subject._degraded_topology("family", ARRAY_UUID, Path("mdadm.conf"))


def test_sysfs_snapshot_reads_kernel_member_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    md_dir = tmp_path / "md127" / "md"
    member_dir = md_dir / "dev-sdb"
    block_dir = member_dir / "block"
    block_dir.mkdir(parents=True)
    (md_dir / "sync_action").write_text("idle\n", encoding="ascii")
    (md_dir / "component_size").write_text("2097152\n", encoding="ascii")
    (member_dir / "state").write_text("in_sync\n", encoding="ascii")
    (member_dir / "slot").write_text("0\n", encoding="ascii")
    (block_dir / "uevent").write_text("MAJOR=8\nMINOR=16\nDEVNAME=sdb\n", encoding="ascii")
    monkeypatch.setattr(subject, "_SYS_CLASS_BLOCK", tmp_path)
    monkeypatch.setattr(subject, "_run_checked", lambda *_args: "md127\n")

    snapshot = subject._sysfs_snapshot("/dev/md/echo-family")

    assert snapshot == {
        "kernelDevice": "md127",
        "syncAction": "idle",
        "componentSizeSectors": 2097152,
        "members": [{"devicefile": "/dev/sdb", "slot": 0, "states": ["in_sync"]}],
    }


def test_degraded_topology_rejects_healthy_array(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        subject,
        "_configured_identity",
        lambda *_args: {"devicefile": "/dev/md/echo-family"},
    )
    monkeypatch.setattr(subject, "_run", lambda *_args: SimpleNamespace(returncode=0))
    with pytest.raises(ValueError, match="exactly one failed"):
        subject._degraded_topology("family", ARRAY_UUID, Path("mdadm.conf"))


def test_plan_binds_topology_and_replacement_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subject, "_require_tools", lambda: None)
    monkeypatch.setattr(subject, "_degraded_topology", lambda *_args: _topology())
    monkeypatch.setattr(subject, "inspect_blank_whole_disks", lambda _paths: [_blank()])

    plan = subject._build_plan(DESIRED, Path("mdadm.conf"))

    assert plan["requiresApproval"] is True
    assert plan["failedMember"]["devicefile"] == "/dev/sdd"
    assert plan["replacement"]["serial"] == "replacement"
    assert len(plan["planId"]) == 64
    assert plan["safety"]["force"] is False
    assert plan["safety"]["marksHealthyMemberFaulty"] is False


def test_plan_rejects_smaller_replacement(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subject, "_require_tools", lambda: None)
    monkeypatch.setattr(subject, "_degraded_topology", lambda *_args: _topology())
    monkeypatch.setattr(
        subject,
        "inspect_blank_whole_disks",
        lambda _paths: [_blank(size=9 * 1024**3)],
    )
    with pytest.raises(ValueError, match="smaller"):
        subject._build_plan(DESIRED, Path("mdadm.conf"))


@pytest.mark.parametrize("failed", [True, False])
def test_apply_removes_only_known_faulty_member_then_adds_blank_disk(
    monkeypatch: pytest.MonkeyPatch,
    failed: bool,
) -> None:
    plan = {
        "planId": "c" * 64,
        "array": {"devicefile": "/dev/md/echo-family", "uuid": ARRAY_UUID},
        "survivingMember": {"devicefile": "/dev/sdb"},
        "failedMember": ({"devicefile": "/dev/sdd", "states": ["faulty"]} if failed else None),
        "replacement": {"devicefile": "/dev/sdc"},
    }
    monkeypatch.setattr(subject, "_array_transaction", nullcontext)
    monkeypatch.setattr(subject, "_build_plan", lambda *_args: plan)
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(subject, "_run_mutating", lambda *args: calls.append(args))
    monkeypatch.setattr(
        subject,
        "_replacement_accepted",
        lambda _plan: {
            "kernelDevice": "md127",
            "syncAction": "recover",
            "members": [],
        },
    )

    result = subject.apply_mdraid1_replace(DESIRED, plan["planId"])

    if failed:
        assert calls[0] == (
            "mdadm",
            "/dev/md/echo-family",
            "--remove",
            "/dev/sdd",
        )
    else:
        assert len(calls) == 1
    assert calls[-1] == ("mdadm", "/dev/md/echo-family", "--add", "/dev/sdc")
    assert result["maintenanceState"] == "recovering"
    assert result["dataPreserved"] is True


def test_apply_rejects_stale_plan_before_mutation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subject, "_array_transaction", nullcontext)
    monkeypatch.setattr(subject, "_build_plan", lambda *_args: {"planId": "d" * 64})
    mutated = False

    def mutate(*_args: str) -> None:
        nonlocal mutated
        mutated = True

    monkeypatch.setattr(subject, "_run_mutating", mutate)
    with pytest.raises(ValueError, match="stale"):
        subject.apply_mdraid1_replace(DESIRED, "e" * 64)
    assert mutated is False
