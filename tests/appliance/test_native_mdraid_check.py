from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from appliance import native_mdraid_check
from appliance.omv_protocol import validate_mdraid_check_desired

ARRAY_UUID = "11111111:22222222:33333333:44444444"


def _desired(**overrides: Any) -> dict[str, Any]:
    return {
        "schema": "echo.omv.mdraid-check-desired.v1",
        "name": "family",
        "arrayUuid": ARRAY_UUID,
        "operation": "start",
        **overrides,
    }


@pytest.fixture
def md_host(monkeypatch: pytest.MonkeyPatch) -> tuple[dict[str, Any], list[tuple[str, ...]]]:
    state: dict[str, Any] = {
        "action": "idle",
        "degraded": "0",
        "mismatch": "0",
        "completed": "none",
        "detailCode": 0,
        "members": [
            {"devicefile": "/dev/sdb", "slot": 0, "states": ["in_sync"]},
            {"devicefile": "/dev/sdc", "slot": 1, "states": ["in_sync"]},
        ],
    }
    commands: list[tuple[str, ...]] = []
    monkeypatch.setattr(native_mdraid_check, "_require_tools", lambda: None)
    monkeypatch.setattr(
        native_mdraid_check,
        "_configured_identity",
        lambda name, array_uuid, _config_path: {
            "name": name,
            "devicefile": f"/dev/md/echo-{name}",
            "uuid": array_uuid,
            "configSha256": "a" * 64,
        },
    )
    monkeypatch.setattr(
        native_mdraid_check,
        "_run",
        lambda *args, **_kwargs: subprocess.CompletedProcess(args, state["detailCode"], "", ""),
    )

    def checked(*args: str, **_kwargs: Any) -> str:
        assert args == ("mdadm", "--detail", "--export", "/dev/md/echo-family")
        return "\n".join(
            (
                "MD_LEVEL=raid1",
                "MD_DEVICES=2",
                "MD_METADATA=1.2",
                "MD_DEVNAME=echo-family",
                f"MD_UUID={ARRAY_UUID}",
                "MD_RESHAPE_ACTIVE=False",
            )
        )

    monkeypatch.setattr(native_mdraid_check, "_run_checked", checked)
    monkeypatch.setattr(
        native_mdraid_check,
        "_sysfs_snapshot",
        lambda _target: {
            "kernelDevice": "md7",
            "syncAction": state["action"],
            "componentSizeSectors": 1024,
            "members": state["members"],
        },
    )

    def read_sysfs(path: Path) -> str:
        return {
            "degraded": state["degraded"],
            "mismatch_cnt": state["mismatch"],
            "sync_completed": state["completed"],
        }[path.name]

    monkeypatch.setattr(native_mdraid_check, "_read_sysfs_text", read_sysfs)

    def mutate(*args: str, **_kwargs: Any) -> None:
        commands.append(args)
        state.update(action="check", completed="1 / 100")

    monkeypatch.setattr(native_mdraid_check, "_run_mutating", mutate)
    return state, commands


def test_protocol_accepts_only_start_for_a_bound_managed_array() -> None:
    assert validate_mdraid_check_desired(_desired()) == _desired()
    with pytest.raises(ValueError, match="unexpected fields"):
        validate_mdraid_check_desired({**_desired(), "repair": True})
    with pytest.raises(ValueError, match="must be start"):
        validate_mdraid_check_desired(_desired(operation="repair"))


def test_plan_binds_healthy_idle_array_without_exposing_repair(
    md_host: tuple[dict[str, Any], list[tuple[str, ...]]],
) -> None:
    _state, commands = md_host

    plan = native_mdraid_check.plan_mdraid_check(_desired())

    assert plan["schema"] == "echo.omv.mdraid-check-plan.v1"
    assert plan["array"]["uuid"] == ARRAY_UUID
    assert plan["before"]["action"] == "idle"
    assert plan["before"]["mismatchCount"] == 0
    assert plan["safety"]["data"] == "redundancyConsistencyScan"
    assert plan["safety"]["explicitRepair"] is False
    assert plan["safety"]["kernelReadErrorRecovery"] == "mayOccur"
    assert plan["safety"]["ioLoad"] == "high"
    assert commands == []


def test_maintenance_inventory_is_limited_to_echo_managed_entries(
    monkeypatch: pytest.MonkeyPatch,
    md_host: tuple[dict[str, Any], list[tuple[str, ...]]],
) -> None:
    _state, _commands = md_host
    monkeypatch.setattr(
        native_mdraid_check,
        "_read_config",
        lambda _path: (
            b"# BEGIN ECHO OS MANAGED MDRAID\n"
            b"ARRAY /dev/md/echo-family UUID=11111111:22222222:33333333:44444444 metadata=1.2\n"
            b"# END ECHO OS MANAGED MDRAID\n"
        ),
    )

    result = native_mdraid_check.mdraid_maintenance()

    assert len(result) == 1
    assert result[0]["array"]["name"] == "family"
    assert result[0]["canStartCheck"] is True


@pytest.mark.parametrize(
    ("update", "message"),
    [
        ({"action": "recover"}, "active md RAID1"),
        ({"detailCode": 1, "degraded": "1"}, "healthy two-member"),
    ],
)
def test_plan_rejects_active_or_degraded_arrays(
    md_host: tuple[dict[str, Any], list[tuple[str, ...]]],
    update: dict[str, Any],
    message: str,
) -> None:
    state, _commands = md_host
    state.update(update)

    with pytest.raises(ValueError, match=message):
        native_mdraid_check.plan_mdraid_check(_desired())


def test_apply_runs_only_check_and_verifies_kernel_transition(
    md_host: tuple[dict[str, Any], list[tuple[str, ...]]],
) -> None:
    _state, commands = md_host
    plan = native_mdraid_check.plan_mdraid_check(_desired())

    applied = native_mdraid_check.apply_mdraid_check(_desired(), plan["planId"])

    assert commands == [("mdadm", "--action=check", "/dev/md/echo-family")]
    assert all("repair" not in argument for command in commands for argument in command)
    assert applied["verified"] is True
    assert applied["maintenanceState"] == "checking"
    assert applied["current"]["progressPercent"] == 1.0


def test_apply_refuses_stale_plan_before_mutation(
    md_host: tuple[dict[str, Any], list[tuple[str, ...]]],
) -> None:
    _state, commands = md_host

    with pytest.raises(ValueError, match="stale"):
        native_mdraid_check.apply_mdraid_check(_desired(), "0" * 64)

    assert commands == []
