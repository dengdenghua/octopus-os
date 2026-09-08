from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from deploy.appliance import btrfs_replacement_lab as lab

FILESYSTEM_UUID = "11111111-2222-3333-4444-555555555555"
ORIGINAL = ["/dev/vdb", "/dev/vdc"]
REPLACEMENT = "/dev/vdd"


def _candidate(_path: Path, _uid: int) -> dict[str, str]:
    return {
        "indexId": "index",
        "operationsArtifactId": "artifact",
        "operationsArchiveSha256": "a" * 64,
        "immutableReference": f"example.invalid/echo@sha256:{'b' * 64}",
    }


def _bundle(_root: Path, _candidate_value: Any, _uid: int) -> dict[str, str]:
    return {
        "artifactId": "artifact",
        "btrfsProvisioningLabSha256": "c" * 64,
        "btrfsReplacementLabSha256": "d" * 64,
    }


def _platform(_path: Path, _tools: Any, _runner: Any) -> dict[str, str]:
    return {"id": "debian", "versionId": "13", "omvVersion": "8.5.6"}


def _disk(path: str, serial: str, *, size: int = 12 * 1024**3) -> dict[str, Any]:
    return {
        "devicefile": path,
        "sizeBytes": size,
        "serial": serial,
        "wwn": None,
        "model": "test",
    }


def _bound(path: str, serial: str) -> dict[str, Any]:
    return lab.common._bound_candidates([_disk(path, serial)])[0]


def _provisioning() -> tuple[dict[str, Any], dict[str, Any]]:
    return (
        {
            "planId": "e" * 64,
            "releaseCandidate": _candidate(Path(), 0),
            "operationsBundle": {"artifactId": "artifact"},
            "platform": _platform(Path(), None, None),
            "appliance": {"baseUrl": "http://127.0.0.1:8000"},
            "selection": {
                "desired": {
                    "schema": lab.provisioning.BTRFS_DESIRED_SCHEMA,
                    "name": "btrfslab",
                    "devices": ORIGINAL,
                    "dataLossConfirmed": True,
                },
                "devices": [_bound("/dev/vdb", "disk-b"), _bound("/dev/vdc", "disk-c")],
                "mountpoint": "/data/btrfslab",
            },
        },
        {
            "filesystem": {
                "uuid": FILESYSTEM_UUID,
                "type": "btrfs",
                "mountpoint": "/data/btrfslab",
            },
            "probe": {"name": ".echo-storage-provisioning-probe", "size": 1, "sha256": "f" * 64},
        },
    )


def _replacement_candidate(*, survivor_serial: str = "disk-c") -> dict[str, Any]:
    return {
        "filesystem": {
            "uuid": FILESYSTEM_UUID,
            "mountpoint": "/data/btrfslab",
            "readOnly": False,
            "totalDevices": 2,
            "activeDevices": 1,
            "missingDevices": 1,
            "dataProfile": "raid1",
            "metadataProfile": "raid1",
            "deviceErrorCount": 0,
        },
        "missingMember": {
            "devid": 1,
            "sizeBytes": 0,
            "usedBytes": 0,
            "devicefile": None,
            "missing": True,
        },
        "survivingMember": {
            **_disk("/dev/vdc", survivor_serial),
            "devid": 2,
            "usedBytes": 1,
            "missing": False,
            "replaceTarget": False,
            "writeable": True,
            "errorStats": {},
            "errorCount": 0,
        },
        "minimumReplacementBytes": 12 * 1024**3,
        "replacementDevices": [_disk(REPLACEMENT, "disk-d")],
    }


class FakeApi:
    def __init__(self, *, survivor_serial: str = "disk-c") -> None:
        self.calls: list[tuple[str, str, Any, Any]] = []
        self.survivor_serial = survivor_serial

    def __call__(
        self,
        _base: str,
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
        if path.endswith("/btrfs-raid1/candidates"):
            return 200, {"devices": [_disk(REPLACEMENT, "disk-d")]}
        if path.endswith("/replacement-candidates"):
            return 200, {
                "replacements": [_replacement_candidate(survivor_serial=self.survivor_serial)]
            }
        if path.endswith("/replace/plan"):
            return 200, {
                "schema": lab.PLAN_SCHEMA,
                "operation": "replaceMissingMember",
                "desired": payload,
                "planId": "1" * 64,
                "requiresApproval": True,
                "replacement": _disk(REPLACEMENT, "disk-d"),
                "safety": {
                    "force": False,
                    "readFromSourceOnly": False,
                    "foreground": False,
                    "autoResize": False,
                    "degradedRemount": False,
                    "rollback": "noneAfterReplacementAccepted",
                },
            }
        if path == "/api/appliance/approvals":
            return 200, {
                "approvalToken": "approval",
                "action": payload["action"],
                "target": payload["target"],
            }
        if path.endswith("/replace/apply"):
            assert headers == {"X-Echo-Approval": "approval"}
            return 200, {
                "applied": True,
                "verified": True,
                "dataPreserved": True,
                "maintenanceState": "replacing",
                "filesystem": {"uuid": FILESYSTEM_UUID},
                "replacement": _disk(REPLACEMENT, "disk-d"),
            }
        raise AssertionError((method, path, payload))


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


def _write_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setattr(lab.systemd, "_assert_owned_directory", lambda *_args, **_kwargs: None)
    evidence = tmp_path / "evidence"
    evidence.mkdir(exist_ok=True)
    bundle = tmp_path / "bundle"
    bundle.mkdir(exist_ok=True)
    material: dict[str, Any] = {
        "schemaVersion": lab.SCHEMA_VERSION,
        "kind": "echo.btrfs-replacement-lab-plan",
        "releaseCandidate": _candidate(Path(), 0),
        "operationsBundle": _bundle(bundle, {}, 0),
        "platform": _platform(Path(), None, None),
        "provisioningPlanId": "e" * 64,
        "appliance": {"baseUrl": "http://127.0.0.1:8000"},
        "filesystem": {
            "uuid": FILESYSTEM_UUID,
            "mountpoint": "/data/btrfslab",
            "originalMembers": ORIGINAL,
            "sacrificialMember": "/dev/vdb",
            "survivingMember": _bound("/dev/vdc", "disk-c"),
            "replacement": _bound(REPLACEMENT, "disk-d"),
        },
        "probe": {"name": "probe", "size": 1, "sha256": "f" * 64},
        "evidenceDirectory": str(evidence.resolve()),
        "baselineBootId": "boot-a",
        "phases": list(lab.PHASES),
        "operatorPreparation": {
            "action": "hotDetachSacrificialMember",
            "device": "/dev/vdb",
            "rebootAllowedBeforeRepair": False,
            "automatedByLab": False,
        },
        "retention": "retainForManualInspectionAndCleanup",
    }
    material["planId"] = lab.common._sha256(lab.common._canonical(material))
    material["confirmations"] = {
        phase: f"RUN ECHO BTRFS REPLACEMENT LAB {phase} {material['planId']}"
        for phase in lab.PHASES
    }
    lab.common._write_new(tmp_path / "plan.json", material, 0, 0o400)
    return material


def test_build_plan_binds_operator_detach_and_third_blank_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LAB_PASSWORD", "password")
    monkeypatch.setattr(lab, "_provisioning_state", lambda *_args: _provisioning())
    monkeypatch.setattr(lab.systemd, "_assert_owned_directory", lambda *_args, **_kwargs: None)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    plan = lab.build_plan(
        candidate_index=tmp_path / "candidate.json",
        bundle_root=bundle,
        provisioning_plan=tmp_path / "provisioning.json",
        sacrificial_device="/dev/vdb",
        replacement_device=REPLACEMENT,
        evidence_directory=evidence,
        output=tmp_path / "plan.json",
        password_env="LAB_PASSWORD",
        http_caller=FakeApi(),
        candidate_loader=_candidate,
        bundle_loader=_bundle,
        platform_probe=_platform,
        host_verifier=_host,
        probe_reader=lambda _path, value, _uid: value,
        boot_id_reader=lambda: "boot-a",
        tool_validator=lambda _tools, _uid: None,
        effective_uid=0,
        system_name="Linux",
    )

    assert plan["operatorPreparation"] == {
        "action": "hotDetachSacrificialMember",
        "device": "/dev/vdb",
        "rebootAllowedBeforeRepair": False,
        "automatedByLab": False,
    }
    assert plan["filesystem"]["replacement"]["identitySha256"]
    assert "disk-d" not in str(plan)
    assert plan["confirmations"]["repair"].endswith(plan["planId"])


def test_three_phases_use_real_http_replacement_and_verify_reboot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LAB_PASSWORD", "password")
    plan = _write_plan(tmp_path, monkeypatch)
    api = FakeApi()
    common_args = {
        "plan_path": tmp_path / "plan.json",
        "candidate_index": tmp_path / "candidate.json",
        "bundle_root": tmp_path / "bundle",
        "password_env": "LAB_PASSWORD",
        "http_caller": api,
        "candidate_loader": _candidate,
        "bundle_loader": _bundle,
        "platform_probe": _platform,
        "host_verifier": _host,
        "probe_reader": lambda _path, value, _uid: value,
        "tool_validator": lambda _tools, _uid: None,
        "effective_uid": 0,
        "system_name": "Linux",
    }
    repair = lab.run_phase(
        **common_args,
        phase="repair",
        confirmation=plan["confirmations"]["repair"],
        boot_id_reader=lambda: "boot-a",
    )
    assert repair["output"] == "btrfs-replacement-repair.log"
    assert any(call[1].endswith("/replace/plan") for call in api.calls)
    assert any(call[1].endswith("/replace/apply") for call in api.calls)
    approvals = [call[2]["action"] for call in api.calls if call[1].endswith("/approvals")]
    assert approvals == ["omv.btrfs-raid1.replace"]

    lab.run_phase(
        **common_args,
        phase="rebuild",
        confirmation=plan["confirmations"]["rebuild"],
        boot_id_reader=lambda: "boot-a",
        status_probe=lambda *_args: {"state": "completed", "errors": 0},
    )
    reboot = lab.run_phase(
        **common_args,
        phase="reboot-verify",
        confirmation=plan["confirmations"]["reboot-verify"],
        boot_id_reader=lambda: "boot-b",
    )
    evidence = json.loads((tmp_path / "evidence" / reboot["output"]).read_text("utf-8"))
    assert evidence["details"]["dataPreserved"] is True
    assert evidence["details"]["currentBootId"] == "boot-b"


def test_survivor_identity_drift_blocks_before_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LAB_PASSWORD", "password")
    plan = _write_plan(tmp_path, monkeypatch)
    api = FakeApi(survivor_serial="wrong-disk")

    with pytest.raises(lab.BtrfsReplacementLabError, match="topology is unsafe"):
        lab.run_phase(
            plan_path=tmp_path / "plan.json",
            phase="repair",
            confirmation=plan["confirmations"]["repair"],
            candidate_index=tmp_path / "candidate.json",
            bundle_root=tmp_path / "bundle",
            password_env="LAB_PASSWORD",
            http_caller=api,
            candidate_loader=_candidate,
            bundle_loader=_bundle,
            platform_probe=_platform,
            probe_reader=lambda _path, value, _uid: value,
            tool_validator=lambda _tools, _uid: None,
            boot_id_reader=lambda: "boot-a",
            effective_uid=0,
            system_name="Linux",
        )
    assert not any(call[1].endswith("/approvals") for call in api.calls)


def test_repair_requires_hot_detach_without_reboot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LAB_PASSWORD", "password")
    plan = _write_plan(tmp_path, monkeypatch)

    with pytest.raises(lab.BtrfsReplacementLabError, match="without rebooting"):
        lab.run_phase(
            plan_path=tmp_path / "plan.json",
            phase="repair",
            confirmation=plan["confirmations"]["repair"],
            candidate_index=tmp_path / "candidate.json",
            bundle_root=tmp_path / "bundle",
            password_env="LAB_PASSWORD",
            candidate_loader=_candidate,
            bundle_loader=_bundle,
            platform_probe=_platform,
            tool_validator=lambda _tools, _uid: None,
            boot_id_reader=lambda: "boot-b",
            effective_uid=0,
            system_name="Linux",
        )


def test_status_parser_rejects_errors() -> None:
    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command,
            0,
            "Started on 1, finished on 2, 1 write errs, 2 uncorr. read errs\n",
            "",
        )

    status = lab._replace_status("/data/btrfslab", lab.provisioning.DEFAULT_TOOLS, runner)
    assert status == {"state": "completed", "errors": 3}
