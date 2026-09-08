from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from deploy.appliance import btrfs_provisioning_lab as lab

DEVICES = ["/dev/vdb", "/dev/vdc"]
FILESYSTEM_UUID = "11111111-2222-3333-4444-555555555555"
API_PLAN_ID = "a" * 64


def _candidates(*, changed: bool = False) -> list[dict[str, Any]]:
    return [
        {
            "devicefile": "/dev/vdb",
            "sizeBytes": 8 * 1024**3,
            "serial": "disk-b-changed" if changed else "disk-b",
            "wwn": None,
            "model": "test",
        },
        {
            "devicefile": "/dev/vdc",
            "sizeBytes": 8 * 1024**3,
            "serial": "disk-c",
            "wwn": None,
            "model": "test",
        },
    ]


def _desired() -> dict[str, Any]:
    return {
        "schema": lab.BTRFS_DESIRED_SCHEMA,
        "name": "btrfslab",
        "devices": DEVICES,
        "dataLossConfirmed": True,
    }


def _api_plan() -> dict[str, Any]:
    return {
        "schema": lab.BTRFS_PLAN_SCHEMA,
        "operation": "createAndMount",
        "desired": _desired(),
        "devices": _candidates(),
        "mountpoint": "/data/btrfslab",
        "planId": API_PLAN_ID,
        "requiresApproval": True,
        "safety": {
            "destructive": True,
            "dataProfile": "raid1",
            "metadataProfile": "raid1",
            "force": False,
        },
    }


class FakeApi:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any, Any]] = []
        self.changed = False
        self.invalid_apply = False

    def __call__(
        self,
        _base_url: str,
        method: str,
        path: str,
        payload: Any,
        token: str | None,
        headers: Any,
    ) -> tuple[int, dict[str, Any]]:
        self.calls.append((method, path, payload, headers))
        if path == "/api/auth/local/login":
            return 200, {"success": True, "access_token": "bearer"}
        assert token == "bearer"
        if path.endswith("/volumes/btrfs-raid1/candidates"):
            return 200, {"devices": _candidates(changed=self.changed)}
        if path.endswith("/volumes/btrfs-raid1/plan"):
            assert payload == _desired()
            return 200, _api_plan()
        if path == "/api/appliance/approvals":
            assert payload["action"] == "omv.btrfs-raid1.create"
            return 200, {
                "approvalToken": "approval-btrfs",
                "action": payload["action"],
                "target": payload["target"],
            }
        if path.endswith("/volumes/btrfs-raid1/apply"):
            assert headers == {"X-Echo-Approval": "approval-btrfs"}
            if self.invalid_apply:
                return 200, {"applied": False, "verified": False}
            return 200, {
                "applied": True,
                "verified": True,
                "filesystem": {
                    "uuid": FILESYSTEM_UUID,
                    "label": "btrfslab",
                    "type": "btrfs",
                    "devices": DEVICES,
                    "mountpoint": "/data/btrfslab",
                    "dataProfile": "raid1",
                    "metadataProfile": "raid1",
                    "readOnly": False,
                },
            }
        raise AssertionError((method, path, payload))


def _candidate(_path: Path, _uid: int) -> dict[str, str]:
    return {
        "indexId": "index",
        "operationsArtifactId": "artifact",
        "operationsArchiveSha256": "b" * 64,
        "immutableReference": f"example.invalid/echo@sha256:{'c' * 64}",
    }


def _bundle(_root: Path, _candidate_value: Any, _uid: int) -> dict[str, str]:
    return {"artifactId": "artifact", "btrfsProvisioningLabSha256": "d" * 64}


def _platform(_path: Path, _tools: lab.LabTools, _runner: Any) -> dict[str, str]:
    return {"id": "debian", "versionId": "13", "omvVersion": "8.5.6"}


def _host(*args: Any) -> dict[str, Any]:
    return {
        "mountpoint": args[0],
        "filesystemUuid": args[1],
        "devices": list(args[2]),
        "dataProfile": "raid1",
        "metadataProfile": "raid1",
        "totalDevices": 2,
        "activeDevices": 2,
        "readOnly": False,
    }


def _build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, api: FakeApi) -> dict[str, Any]:
    monkeypatch.setenv("LAB_PASSWORD", "correct horse battery staple")
    monkeypatch.setattr(lab.systemd, "_assert_owned_directory", lambda *_args, **_kwargs: None)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    return lab.build_plan(
        candidate_index=tmp_path / "candidate.json",
        bundle_root=bundle,
        devices=DEVICES,
        volume_name="btrfslab",
        evidence_directory=evidence,
        base_url="http://127.0.0.1:8000",
        output=tmp_path / "plan.json",
        password_env="LAB_PASSWORD",
        http_caller=api,
        candidate_loader=_candidate,
        bundle_loader=_bundle,
        platform_probe=_platform,
        tool_validator=lambda _tools, _uid: None,
        boot_id_reader=lambda: "boot-a",
        effective_uid=0,
        system_name="Linux",
    )


def _run_args(tmp_path: Path, api: FakeApi) -> dict[str, Any]:
    return {
        "plan_path": tmp_path / "plan.json",
        "candidate_index": tmp_path / "candidate.json",
        "bundle_root": tmp_path / "bundle",
        "password_env": "LAB_PASSWORD",
        "http_caller": api,
        "candidate_loader": _candidate,
        "bundle_loader": _bundle,
        "platform_probe": _platform,
        "tool_validator": lambda _tools, _uid: None,
        "host_verifier": _host,
        "effective_uid": 0,
        "system_name": "Linux",
    }


def test_plan_binds_candidate_bundle_platform_and_hashed_disk_identities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _build(tmp_path, monkeypatch, FakeApi())

    assert plan["releaseCandidate"] == _candidate(Path(), 0)
    assert plan["operationsBundle"]["btrfsProvisioningLabSha256"] == "d" * 64
    assert plan["platform"]["omvVersion"] == "8.5.6"
    assert plan["selection"]["devices"] == lab.common._bound_candidates(_candidates())
    assert "disk-b" not in str(plan)
    assert plan["selection"]["apiPlanId"] == API_PLAN_ID
    assert plan["selection"]["mountpoint"] == "/data/btrfslab"
    assert plan["retention"] == "retainForManualInspectionAndCleanup"
    assert plan["confirmations"]["provision"].endswith(plan["planId"])
    if os.name == "posix":
        assert (tmp_path / "plan.json").stat().st_mode & 0o777 == 0o400


@pytest.mark.parametrize(
    ("devices", "volume_name"),
    [(["/dev/vdb"], "btrfslab"), (DEVICES, "bad/name"), (["/dev/vdb", "/dev/vdb"], "x")],
)
def test_plan_rejects_unsafe_selection_before_api_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    devices: list[str],
    volume_name: str,
) -> None:
    monkeypatch.setenv("LAB_PASSWORD", "password")
    with pytest.raises(lab.BtrfsProvisioningLabError, match="two safe whole disks"):
        lab.build_plan(
            candidate_index=tmp_path / "candidate.json",
            bundle_root=tmp_path,
            devices=devices,
            volume_name=volume_name,
            evidence_directory=tmp_path,
            base_url="http://127.0.0.1:8000",
            output=tmp_path / "plan.json",
            password_env="LAB_PASSWORD",
            effective_uid=0,
            system_name="Linux",
        )


def test_plan_requires_linux_root(tmp_path: Path) -> None:
    with pytest.raises(lab.BtrfsProvisioningLabError, match="Linux root"):
        lab.build_plan(
            candidate_index=tmp_path / "candidate.json",
            bundle_root=tmp_path,
            devices=DEVICES,
            volume_name="btrfslab",
            evidence_directory=tmp_path,
            base_url="http://127.0.0.1:8000",
            output=tmp_path / "plan.json",
            effective_uid=1000,
            system_name="Linux",
        )


def test_provision_uses_one_shot_approval_then_reboot_verifies_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = FakeApi()
    plan = _build(tmp_path, monkeypatch, api)
    probe = {"name": ".echo-storage-provisioning-probe", "size": 1024**2, "sha256": "e" * 64}
    args = _run_args(tmp_path, api)

    provision = lab.run_phase(
        **args,
        phase="provision",
        confirmation=plan["confirmations"]["provision"],
        boot_id_reader=lambda: "boot-a",
        probe_writer=lambda _path, _uid: probe,
    )
    assert provision["output"] == "btrfs-provisioning.log"
    approvals = [call[2]["action"] for call in api.calls if call[1].endswith("/approvals")]
    assert approvals == ["omv.btrfs-raid1.create"]

    reboot = lab.run_phase(
        **args,
        phase="reboot-verify",
        confirmation=plan["confirmations"]["reboot-verify"],
        boot_id_reader=lambda: "boot-b",
        probe_reader=lambda _path, expected, _uid: expected,
    )
    evidence = json.loads((tmp_path / "evidence" / reboot["output"]).read_text("utf-8"))
    assert evidence["details"]["mountedAfterReboot"] is True
    assert evidence["details"]["dataPreserved"] is True
    assert evidence["details"]["probe"] == probe


def test_provision_rejects_disk_identity_drift_before_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = FakeApi()
    plan = _build(tmp_path, monkeypatch, api)
    api.changed = True

    with pytest.raises(lab.BtrfsProvisioningLabError, match="identities changed"):
        lab.run_phase(
            **_run_args(tmp_path, api),
            phase="provision",
            confirmation=plan["confirmations"]["provision"],
            boot_id_reader=lambda: "boot-a",
        )
    assert not any(call[1].endswith("/approvals") for call in api.calls)


def test_failure_after_apply_begins_preserves_host_for_inspection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = FakeApi()
    plan = _build(tmp_path, monkeypatch, api)
    api.invalid_apply = True

    with pytest.raises(lab.BtrfsProvisioningLabError, match="preserve the host"):
        lab.run_phase(
            **_run_args(tmp_path, api),
            phase="provision",
            confirmation=plan["confirmations"]["provision"],
            boot_id_reader=lambda: "boot-a",
        )


def test_reboot_verification_requires_a_new_kernel_boot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = FakeApi()
    plan = _build(tmp_path, monkeypatch, api)
    args = _run_args(tmp_path, api)
    lab.run_phase(
        **args,
        phase="provision",
        confirmation=plan["confirmations"]["provision"],
        boot_id_reader=lambda: "boot-a",
        probe_writer=lambda _path, _uid: {
            "name": ".echo-storage-provisioning-probe",
            "size": 1024**2,
            "sha256": "e" * 64,
        },
    )

    with pytest.raises(lab.BtrfsProvisioningLabError, match="new kernel boot"):
        lab.run_phase(
            **args,
            phase="reboot-verify",
            confirmation=plan["confirmations"]["reboot-verify"],
            boot_id_reader=lambda: "boot-a",
        )


def test_host_verifier_requires_two_btrfs_members_and_raid1_profiles() -> None:
    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        if command[0].endswith("blkid"):
            stdout = f"TYPE=btrfs\nUUID={FILESYSTEM_UUID}\n"
        elif command[0].endswith("findmnt"):
            stdout = f"UUID={FILESYSTEM_UUID} btrfs rw,relatime /data/btrfslab\n"
        elif command[-3:-1] == ["df", "--raw"]:
            stdout = "Data, RAID1: total=1, used=1\nMetadata, RAID1: total=1, used=1\n"
        else:
            stdout = (
                f"Label: 'btrfslab'  uuid: {FILESYSTEM_UUID}\n\tTotal devices 2 FS bytes used 1\n"
            )
        return subprocess.CompletedProcess(command, 0, stdout, "")

    result = lab._verify_host("/data/btrfslab", FILESYSTEM_UUID, DEVICES, lab.DEFAULT_TOOLS, runner)
    assert result["totalDevices"] == 2
    assert result["activeDevices"] == 2
    assert result["dataProfile"] == "raid1"
    assert result["metadataProfile"] == "raid1"


def test_host_verifier_rejects_missing_member() -> None:
    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        if command[0].endswith("blkid"):
            stdout = f"TYPE=btrfs\nUUID={FILESYSTEM_UUID}\n"
        elif command[0].endswith("findmnt"):
            stdout = f"UUID={FILESYSTEM_UUID} btrfs rw /data/btrfslab\n"
        elif command[-3:-1] == ["df", "--raw"]:
            stdout = "Data, RAID1: total=1, used=1\nMetadata, RAID1: total=1, used=1\n"
        else:
            stdout = f"uuid: {FILESYSTEM_UUID}\nTotal devices 2\n*** Some devices missing\n"
        return subprocess.CompletedProcess(command, 0, stdout, "")

    with pytest.raises(lab.BtrfsProvisioningLabError, match="topology"):
        lab._verify_host("/data/btrfslab", FILESYSTEM_UUID, DEVICES, lab.DEFAULT_TOOLS, runner)
