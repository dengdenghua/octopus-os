from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from deploy.appliance import storage_provisioning_lab as lab

DEVICES = ["/dev/vdb", "/dev/vdc"]
ARRAY_UUID = "11111111:22222222:33333333:44444444"
FILESYSTEM_UUID = "11111111-2222-3333-4444-555555555555"
MD_PLAN_ID = "a" * 64
EXT4_PLAN_ID = "b" * 64


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


def _md_desired() -> dict[str, Any]:
    return {
        "schema": lab.MD_DESIRED_SCHEMA,
        "name": "labarray",
        "devices": DEVICES,
        "dataLossConfirmed": True,
    }


def _md_plan() -> dict[str, Any]:
    return {
        "schema": lab.MD_PLAN_SCHEMA,
        "operation": "create",
        "desired": _md_desired(),
        "target": "/dev/md/echo-labarray",
        "devices": _candidates(),
        "planId": MD_PLAN_ID,
        "requiresApproval": True,
        "safety": {"destructive": True, "force": False},
    }


def _ext4_desired() -> dict[str, Any]:
    return {
        "schema": lab.EXT4_DESIRED_SCHEMA,
        "arrayUuid": ARRAY_UUID,
        "name": "labvolume",
        "dataLossConfirmed": True,
    }


def _ext4_plan() -> dict[str, Any]:
    return {
        "schema": lab.EXT4_PLAN_SCHEMA,
        "operation": "createAndMount",
        "desired": _ext4_desired(),
        "array": {"devicefile": "/dev/md/echo-labarray", "uuid": ARRAY_UUID},
        "mountpoint": "/data/labvolume",
        "planId": EXT4_PLAN_ID,
        "requiresApproval": True,
        "safety": {"destructive": True, "force": False},
    }


class FakeApi:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any, Any]] = []
        self.changed = False

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
        if path.endswith("/arrays/mdraid1/candidates"):
            return 200, {"devices": _candidates(changed=self.changed)}
        if path.endswith("/arrays/mdraid1/plan"):
            assert payload == _md_desired()
            return 200, _md_plan()
        if path == "/api/appliance/approvals":
            action = payload["action"]
            return 200, {
                "approvalToken": f"approval-{action}",
                "action": action,
                "target": payload["target"],
            }
        if path.endswith("/arrays/mdraid1/apply"):
            assert headers == {"X-Echo-Approval": "approval-omv.mdraid1.create"}
            return 200, {
                "applied": True,
                "verified": True,
                "array": {
                    "name": "labarray",
                    "devicefile": "/dev/md/echo-labarray",
                    "uuid": ARRAY_UUID,
                    "level": "raid1",
                    "devices": DEVICES,
                    "filesystem": None,
                },
            }
        if path.endswith("/volumes/ext4/candidates"):
            return 200, {
                "arrays": [
                    {
                        "devicefile": "/dev/md/echo-labarray",
                        "uuid": ARRAY_UUID,
                    }
                ]
            }
        if path.endswith("/volumes/ext4/plan"):
            assert payload == _ext4_desired()
            return 200, _ext4_plan()
        if path.endswith("/volumes/ext4/apply"):
            assert headers == {"X-Echo-Approval": "approval-omv.ext4-volume.create"}
            return 200, {
                "applied": True,
                "verified": True,
                "filesystem": {
                    "uuid": FILESYSTEM_UUID,
                    "label": "labvolume",
                    "type": "ext4",
                    "devicefile": "/dev/md/echo-labarray",
                    "mountpoint": "/data/labvolume",
                    "readOnly": False,
                },
            }
        raise AssertionError((method, path, payload))


def _candidate(_path: Path, _uid: int) -> dict[str, str]:
    return {
        "indexId": "index",
        "operationsArtifactId": "artifact",
        "operationsArchiveSha256": "c" * 64,
        "immutableReference": f"example.invalid/echo@sha256:{'d' * 64}",
    }


def _bundle(_root: Path, _candidate: Any, _uid: int) -> dict[str, str]:
    return {"artifactId": "artifact", "storageProvisioningLabSha256": "e" * 64}


def _platform(_path: Path, _tools: lab.LabTools, _runner: Any) -> dict[str, str]:
    return {"id": "debian", "versionId": "13", "omvVersion": "8.5.6"}


def _host(*args: Any) -> dict[str, Any]:
    return {
        "target": args[0],
        "arrayUuid": args[1],
        "mountpoint": args[2],
        "filesystemUuid": args[3],
        "devices": list(args[4]),
        "healthy": True,
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
        array_name="labarray",
        volume_name="labvolume",
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


def test_plan_binds_api_disk_identities_and_destructive_backend_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = FakeApi()
    plan = _build(tmp_path, monkeypatch, api)

    assert plan["selection"]["devices"] == lab._bound_candidates(_candidates())
    assert "disk-b" not in str(plan)
    assert plan["selection"]["mdPlanId"] == MD_PLAN_ID
    assert plan["selection"]["arrayTarget"] == "/dev/md/echo-labarray"
    assert plan["retention"] == "retainForStorageRecoveryLab"
    assert plan["confirmations"]["provision"].endswith(plan["planId"])
    if os.name == "posix":
        assert (tmp_path / "plan.json").stat().st_mode & 0o777 == 0o400


def test_provision_uses_two_one_shot_approvals_then_reboot_verifies_persistence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = FakeApi()
    plan = _build(tmp_path, monkeypatch, api)
    common = {
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
    provision = lab.run_phase(
        **common,
        phase="provision",
        confirmation=plan["confirmations"]["provision"],
        boot_id_reader=lambda: "boot-a",
        probe_writer=lambda _path, _uid: {
            "name": lab.PROBE_NAME,
            "size": lab.PROBE_BYTES,
            "sha256": "f" * 64,
        },
    )
    assert provision["output"] == "storage-provisioning.log"
    approvals = [call[2]["action"] for call in api.calls if call[1].endswith("/approvals")]
    assert approvals == ["omv.mdraid1.create", "omv.ext4-volume.create"]

    reboot = lab.run_phase(
        **common,
        phase="reboot-verify",
        confirmation=plan["confirmations"]["reboot-verify"],
        boot_id_reader=lambda: "boot-b",
        probe_reader=lambda _path, expected, _uid: expected,
    )
    assert reboot["output"] == "storage-provisioning-reboot.log"
    evidence = json_load(tmp_path / "evidence" / reboot["output"])
    assert evidence["details"]["assembledAfterReboot"] is True
    assert evidence["details"]["mountedAfterReboot"] is True
    assert evidence["details"]["dataPreserved"] is True


def test_provision_rejects_identity_drift_before_issuing_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = FakeApi()
    plan = _build(tmp_path, monkeypatch, api)
    api.changed = True

    with pytest.raises(lab.StorageProvisioningLabError, match="identities changed"):
        lab.run_phase(
            plan_path=tmp_path / "plan.json",
            phase="provision",
            confirmation=plan["confirmations"]["provision"],
            candidate_index=tmp_path / "candidate.json",
            bundle_root=tmp_path / "bundle",
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

    assert not any(call[1].endswith("/approvals") for call in api.calls)


def test_plan_rejects_non_loopback_appliance_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LAB_PASSWORD", "password")
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    with pytest.raises(lab.StorageProvisioningLabError, match="loopback"):
        lab.build_plan(
            candidate_index=tmp_path / "candidate.json",
            bundle_root=bundle,
            devices=DEVICES,
            array_name="labarray",
            volume_name="labvolume",
            evidence_directory=evidence,
            base_url="http://192.0.2.1:8000",
            output=tmp_path / "plan.json",
            password_env="LAB_PASSWORD",
            http_caller=FakeApi(),
            candidate_loader=_candidate,
            bundle_loader=_bundle,
            platform_probe=_platform,
            tool_validator=lambda _tools, _uid: None,
            effective_uid=0,
            system_name="Linux",
        )


def test_host_verifier_requires_exact_healthy_array_ext4_and_rw_mount() -> None:
    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        if "--test" in command:
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[0].endswith("mdadm"):
            stdout = (
                "MD_LEVEL=raid1\nMD_DEVICES=2\n"
                f"MD_UUID={ARRAY_UUID}\nMD_DEVICE_0_DEV=/dev/vdb\n"
                "MD_DEVICE_1_DEV=/dev/vdc\n"
            )
        elif command[0].endswith("blkid"):
            stdout = f"TYPE=ext4\nUUID={FILESYSTEM_UUID}\n"
        else:
            stdout = "/dev/md/echo-labarray ext4 rw,relatime /data/labvolume\n"
        return subprocess.CompletedProcess(command, 0, stdout, "")

    result = lab._verify_host(
        "/dev/md/echo-labarray",
        ARRAY_UUID,
        "/data/labvolume",
        FILESYSTEM_UUID,
        DEVICES,
        lab.DEFAULT_TOOLS,
        runner,
    )

    assert result["healthy"] is True
    assert result["readOnly"] is False


def test_host_verifier_rejects_read_only_reboot_mount() -> None:
    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        if "--test" in command:
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[0].endswith("mdadm"):
            stdout = (
                "MD_LEVEL=raid1\nMD_DEVICES=2\n"
                f"MD_UUID={ARRAY_UUID}\nMD_DEVICE_0_DEV=/dev/vdb\n"
                "MD_DEVICE_1_DEV=/dev/vdc\n"
            )
        elif command[0].endswith("blkid"):
            stdout = f"TYPE=ext4\nUUID={FILESYSTEM_UUID}\n"
        else:
            stdout = "/dev/md/echo-labarray ext4 ro,relatime /data/labvolume\n"
        return subprocess.CompletedProcess(command, 0, stdout, "")

    with pytest.raises(lab.StorageProvisioningLabError, match="not writable"):
        lab._verify_host(
            "/dev/md/echo-labarray",
            ARRAY_UUID,
            "/data/labvolume",
            FILESYSTEM_UUID,
            DEVICES,
            lab.DEFAULT_TOOLS,
            runner,
        )


def json_load(path: Path) -> dict[str, Any]:
    import json

    return json.loads(path.read_text(encoding="utf-8"))
