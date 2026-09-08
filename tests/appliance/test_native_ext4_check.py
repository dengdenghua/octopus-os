from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from appliance import native_ext4_check as check
from appliance import native_storage
from appliance.native_storage_routes import create_omv_alias_router

FILESYSTEM_UUID = "11111111-2222-3333-4444-555555555555"
ARRAY_UUID = "11111111:22222222:33333333:44444444"
DEVICE = "/dev/md/echo-family"
MOUNTPOINT = "/data/family"


def _fstab(path: Path) -> None:
    path.write_text(
        "# BEGIN ECHO OS MANAGED EXT4\n"
        f"UUID={FILESYSTEM_UUID} {MOUNTPOINT} ext4 "
        "defaults,nofail,x-systemd.device-timeout=30s 0 2\n"
        "# END ECHO OS MANAGED EXT4\n",
        encoding="utf-8",
    )


def _array() -> dict[str, Any]:
    return {
        "name": "family",
        "devicefile": DEVICE,
        "uuid": ARRAY_UUID,
        "level": "raid1",
        "devices": ["/dev/sdb", "/dev/sdc"],
        "filesystem": None,
    }


def _runner(
    calls: list[list[str]],
    *,
    mounted: bool = False,
    e2fsck_code: int = 0,
    mask_code: int = 0,
    unmask_code: int = 0,
):
    masked = False

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal masked
        calls.append(argv)
        assert kwargs["check"] is False
        assert kwargs.get("shell") is None
        if argv[:2] == ["blkid", "--uuid"]:
            return subprocess.CompletedProcess(argv, 0, f"{DEVICE}\n", "")
        if argv[:3] == ["blkid", "--probe", "--output"]:
            return subprocess.CompletedProcess(
                argv,
                0,
                f"UUID={FILESYSTEM_UUID}\nTYPE=ext4\nLABEL=family\n",
                "",
            )
        if argv[0] == "findmnt":
            return subprocess.CompletedProcess(
                argv, 0 if mounted else 1, "mounted\n" if mounted else "", ""
            )
        if argv[0] == "systemd-escape":
            return subprocess.CompletedProcess(argv, 0, "data-family.mount\n", "")
        if argv[:2] == ["systemctl", "mask"]:
            masked = True
            return subprocess.CompletedProcess(argv, mask_code, "", "failed")
        if argv[:2] == ["systemctl", "unmask"]:
            if unmask_code == 0:
                masked = False
            return subprocess.CompletedProcess(argv, unmask_code, "", "failed")
        if argv[:2] == ["systemctl", "is-enabled"]:
            return subprocess.CompletedProcess(
                argv,
                1 if masked else 0,
                "masked-runtime\n" if masked else "generated\n",
                "",
            )
        if argv[0] == "e2fsck":
            return subprocess.CompletedProcess(argv, e2fsck_code, "bounded output", "")
        raise AssertionError(argv)

    return run


@pytest.fixture(autouse=True)
def tools_and_array(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(check, "_require_tools", lambda: None)
    monkeypatch.setattr(check, "managed_mdraid1_arrays", lambda **_kwargs: [_array()])


def test_inventory_only_marks_an_unmounted_managed_ext4_volume_checkable(tmp_path: Path) -> None:
    fstab = tmp_path / "fstab"
    _fstab(fstab)

    result = check.ext4_check_inventory(
        fstab_path=fstab,
        mdadm_config_path=tmp_path / "mdadm.conf",
        runner=_runner([]),
    )

    assert len(result) == 1
    assert result[0]["filesystemUuid"] == FILESYSTEM_UUID
    assert result[0]["array"]["uuid"] == ARRAY_UUID
    assert result[0]["canCheck"] is True
    assert result[0]["mounted"] is False


def test_plan_rejects_a_mounted_filesystem(tmp_path: Path) -> None:
    fstab = tmp_path / "fstab"
    _fstab(fstab)

    with pytest.raises(ValueError, match="already be unmounted"):
        check.plan_ext4_check(
            {
                "schema": "echo.omv.ext4-check-desired.v1",
                "filesystemUuid": FILESYSTEM_UUID,
                "operation": "check",
            },
            fstab_path=fstab,
            mdadm_config_path=tmp_path / "mdadm.conf",
            runner=_runner([], mounted=True),
        )


@pytest.mark.parametrize(
    ("exit_code", "result", "clean"),
    [(0, "clean", True), (4, "errorsFound", False)],
)
def test_apply_masks_mount_unit_runs_read_only_check_and_restores(
    tmp_path: Path,
    exit_code: int,
    result: str,
    clean: bool,
) -> None:
    fstab = tmp_path / "fstab"
    _fstab(fstab)
    calls: list[list[str]] = []
    runner = _runner(calls, e2fsck_code=exit_code)
    desired = {
        "schema": "echo.omv.ext4-check-desired.v1",
        "filesystemUuid": FILESYSTEM_UUID,
        "operation": "check",
    }
    plan = check.plan_ext4_check(
        desired,
        fstab_path=fstab,
        mdadm_config_path=tmp_path / "mdadm.conf",
        runner=runner,
    )

    applied = check.apply_ext4_check(
        desired,
        plan["planId"],
        fstab_path=fstab,
        mdadm_config_path=tmp_path / "mdadm.conf",
        runner=runner,
    )

    assert applied["verified"] is True
    assert applied["result"] == result
    assert applied["clean"] is clean
    assert applied["errorsDetected"] is (not clean)
    assert ["e2fsck", "-f", "-n", DEVICE] in calls
    assert ["systemctl", "mask", "--runtime", "data-family.mount"] in calls
    assert ["systemctl", "unmask", "--runtime", "data-family.mount"] in calls


def test_apply_reports_incomplete_mount_unit_restore(tmp_path: Path) -> None:
    fstab = tmp_path / "fstab"
    _fstab(fstab)
    runner = _runner([], unmask_code=1)
    desired = {
        "schema": "echo.omv.ext4-check-desired.v1",
        "filesystemUuid": FILESYSTEM_UUID,
        "operation": "check",
    }
    plan = check.plan_ext4_check(
        desired,
        fstab_path=fstab,
        mdadm_config_path=tmp_path / "mdadm.conf",
        runner=runner,
    )

    with pytest.raises(OSError, match="incomplete mount-unit restore"):
        check.apply_ext4_check(
            desired,
            plan["planId"],
            fstab_path=fstab,
            mdadm_config_path=tmp_path / "mdadm.conf",
            runner=runner,
        )


def test_apply_unmasks_after_a_partial_mask_failure(tmp_path: Path) -> None:
    fstab = tmp_path / "fstab"
    _fstab(fstab)
    calls: list[list[str]] = []
    runner = _runner(calls, mask_code=1)
    desired = {
        "schema": "echo.omv.ext4-check-desired.v1",
        "filesystemUuid": FILESYSTEM_UUID,
        "operation": "check",
    }
    plan = check.plan_ext4_check(
        desired,
        fstab_path=fstab,
        mdadm_config_path=tmp_path / "mdadm.conf",
        runner=runner,
    )

    with pytest.raises(OSError, match="rejected the bounded"):
        check.apply_ext4_check(
            desired,
            plan["planId"],
            fstab_path=fstab,
            mdadm_config_path=tmp_path / "mdadm.conf",
            runner=runner,
        )

    assert ["systemctl", "unmask", "--runtime", "data-family.mount"] in calls


def test_routes_keep_inventory_plan_and_approved_apply_separate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    desired = {
        "schema": "echo.omv.ext4-check-desired.v1",
        "filesystemUuid": FILESYSTEM_UUID,
        "operation": "check",
    }
    plan = {
        "schema": "echo.omv.ext4-check-plan.v1",
        "planId": "a" * 64,
        "operation": "offlineReadOnlyCheck",
        "desired": desired,
        "filesystem": {"filesystemUuid": FILESYSTEM_UUID},
        "requiresApproval": True,
    }
    approvals: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []

    class Approval:
        def consume(self, **kwargs: Any) -> None:
            approvals.append(kwargs)

    class Audit:
        def record(self, **kwargs: Any) -> None:
            audits.append(kwargs)

    monkeypatch.setattr(native_storage, "ext4_check_inventory", lambda: [])
    monkeypatch.setattr(native_storage, "plan_ext4_check", lambda _desired: plan)
    monkeypatch.setattr(
        native_storage,
        "apply_ext4_check",
        lambda _desired, _plan_id: {**plan, "applied": True, "verified": True},
    )
    app = FastAPI()
    app.include_router(create_omv_alias_router(approval=Approval(), audit=Audit()))
    client = TestClient(app)

    inventory = client.get("/api/appliance/omv/volumes/ext4/checks")
    preview = client.post("/api/appliance/omv/volumes/ext4/check/plan", json=desired)
    applied = client.post(
        "/api/appliance/omv/volumes/ext4/check/apply",
        json={"desired": desired, "planId": plan["planId"]},
        headers={"X-Echo-Approval": "approval-token"},
    )

    assert inventory.status_code == 200
    assert preview.status_code == 200
    assert applied.status_code == 200
    assert approvals[0]["action"] == "omv.ext4.offline-check"
    assert audits[0]["metadata"] == {
        "filesystemUuid": FILESYSTEM_UUID,
        "operation": "offlineReadOnlyCheck",
        "automaticUnmount": False,
        "repair": False,
        "source": "native",
    }
