from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from deploy.appliance import mdraid_replacement_lab as lab

ARRAY_UUID = "11111111:22222222:33333333:44444444"
FS_UUID = "11111111-2222-3333-4444-555555555555"
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
    return {"artifactId": "artifact", "mdraidReplacementLabSha256": "c" * 64}


def _platform(_path: Path, _tools: Any, _runner: Any) -> dict[str, str]:
    return {"id": "debian", "versionId": "13", "omvVersion": "8.5.6"}


def _disk() -> dict[str, Any]:
    return {
        "devicefile": REPLACEMENT,
        "sizeBytes": 8 * 1024**3,
        "serial": "replacement-disk",
        "wwn": None,
        "model": "test",
    }


def _binding() -> dict[str, Any]:
    return {
        "array": {"path": "/dev/md/echo-labarray", "majorMinor": "9:0", "sizeBytes": 1},
        "members": [
            {
                "path": path,
                "parentPath": path,
                "majorMinor": f"8:{index}",
                "sizeBytes": 1,
                "identitySha256": str(index) * 64,
            }
            for index, path in enumerate([*ORIGINAL, REPLACEMENT], 1)
        ],
        "mountpoint": "/data/labvolume",
    }


def _provisioning() -> tuple[dict[str, Any], dict[str, Any]]:
    return (
        {
            "planId": "d" * 64,
            "releaseCandidate": _candidate(Path(), 0),
            "operationsBundle": {"artifactId": "artifact"},
            "platform": _platform(Path(), None, None),
            "appliance": {"baseUrl": "http://127.0.0.1:8000"},
            "selection": {
                "mdDesired": {"name": "labarray", "devices": ORIGINAL},
                "arrayTarget": "/dev/md/echo-labarray",
                "mountpoint": "/data/labvolume",
            },
        },
        {
            "array": {"uuid": ARRAY_UUID},
            "filesystem": {"uuid": FS_UUID},
            "probe": {"name": ".echo-storage-provisioning-probe", "size": 1, "sha256": "e" * 64},
        },
    )


class FakeApi:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any, Any]] = []

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
        if path.endswith("/arrays/mdraid1/candidates"):
            return 200, {"devices": [_disk()]}
        if path.endswith("/replacement-candidates"):
            return 200, {
                "replacements": [
                    {
                        "array": {
                            "name": "labarray",
                            "uuid": ARRAY_UUID,
                            "devicefile": "/dev/md/echo-labarray",
                        },
                        "survivingMember": {"devicefile": "/dev/vdc"},
                        "failedMember": None,
                        "missingSlot": 0,
                        "minimumReplacementBytes": 4 * 1024**3,
                        "replacementDevices": [_disk()],
                    }
                ]
            }
        if path.endswith("/replace/plan"):
            return 200, {
                "schema": lab.PLAN_SCHEMA,
                "operation": "replaceFailedMember",
                "desired": payload,
                "planId": "f" * 64,
                "requiresApproval": True,
                "replacement": _disk(),
                "safety": {"force": False, "marksHealthyMemberFaulty": False},
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
                "maintenanceState": "recovering",
                "array": {
                    "devicefile": "/dev/md/echo-labarray",
                    "uuid": ARRAY_UUID,
                },
                "replacement": _disk(),
            }
        raise AssertionError((method, path, payload))


def _host(*_args: Any) -> dict[str, Any]:
    return {"healthy": True, "readOnly": False}


def _device(*_args: Any) -> dict[str, Any]:
    return _binding()


def _write_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setattr(lab.systemd, "_assert_owned_directory", lambda *_args, **_kwargs: None)
    evidence = tmp_path / "evidence"
    evidence.mkdir(exist_ok=True)
    bundle = tmp_path / "bundle"
    bundle.mkdir(exist_ok=True)
    material: dict[str, Any] = {
        "schemaVersion": lab.SCHEMA_VERSION,
        "kind": "echo.mdraid-replacement-lab-plan",
        "releaseCandidate": _candidate(Path(), 0),
        "operationsBundle": _bundle(bundle, {}, 0),
        "platform": _platform(Path(), None, None),
        "provisioningPlanId": "d" * 64,
        "appliance": {"baseUrl": "http://127.0.0.1:8000"},
        "array": {
            "name": "labarray",
            "devicefile": "/dev/md/echo-labarray",
            "uuid": ARRAY_UUID,
            "filesystemUuid": FS_UUID,
            "mountpoint": "/data/labvolume",
            "originalMembers": ORIGINAL,
            "sacrificialMember": "/dev/vdb",
            "survivingMember": "/dev/vdc",
            "replacement": lab._single_candidate([_disk()], REPLACEMENT),
        },
        "deviceBinding": _binding(),
        "probe": {"name": "probe", "size": 1, "sha256": "e" * 64},
        "evidenceDirectory": str(evidence.resolve()),
        "baselineBootId": "boot-a",
        "phases": list(lab.PHASES),
    }
    material["planId"] = lab.provisioning._sha256(lab.provisioning._canonical(material))
    material["confirmations"] = {
        phase: f"RUN ECHO MDRAID REPLACEMENT LAB {phase} {material['planId']}"
        for phase in lab.PHASES
    }
    lab.provisioning._write_new(tmp_path / "plan.json", material, 0, 0o400)
    return material


def test_build_plan_binds_third_blank_disk_and_provisioning_identity(
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
        device_verifier=_device,
        host_verifier=_host,
        probe_reader=lambda _path, value, _uid: value,
        boot_id_reader=lambda: "boot-a",
        tool_validator=lambda _tools, _uid: None,
        effective_uid=0,
        system_name="Linux",
    )

    assert plan["array"]["replacement"]["identitySha256"]
    assert "replacement-disk" not in str(plan)
    assert plan["provisioningPlanId"] == "d" * 64
    assert plan["confirmations"]["repair"].endswith(plan["planId"])


def test_three_phases_use_real_replacement_api_and_verify_reboot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LAB_PASSWORD", "password")
    plan = _write_plan(tmp_path, monkeypatch)
    api = FakeApi()
    commands: list[list[str]] = []

    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    common = {
        "plan_path": tmp_path / "plan.json",
        "candidate_index": tmp_path / "candidate.json",
        "bundle_root": tmp_path / "bundle",
        "password_env": "LAB_PASSWORD",
        "http_caller": api,
        "candidate_loader": _candidate,
        "bundle_loader": _bundle,
        "platform_probe": _platform,
        "device_verifier": _device,
        "host_verifier": _host,
        "probe_reader": lambda _path, value, _uid: value,
        "array_probe": lambda _path: {"healthy": True, "active": 2},
        "tool_validator": lambda _tools, _uid: None,
        "effective_uid": 0,
        "system_name": "Linux",
        "runner": runner,
    }
    lab.run_phase(
        **common,
        phase="repair",
        confirmation=plan["confirmations"]["repair"],
        boot_id_reader=lambda: "boot-a",
    )
    assert [command[-2] for command in commands] == ["--fail", "--remove"]
    assert any(call[1].endswith("/replace/plan") for call in api.calls)
    assert any(call[1].endswith("/replace/apply") for call in api.calls)

    lab.run_phase(
        **common,
        phase="rebuild",
        confirmation=plan["confirmations"]["rebuild"],
        boot_id_reader=lambda: "boot-a",
    )
    lab.run_phase(
        **common,
        phase="reboot-verify",
        confirmation=plan["confirmations"]["reboot-verify"],
        boot_id_reader=lambda: "boot-b",
    )
    final = json.loads((tmp_path / "evidence" / lab.PHASE_OUTPUTS["reboot-verify"]).read_text())
    assert final["details"]["dataPreserved"] is True
    assert final["details"]["currentBootId"] == "boot-b"


def test_identity_drift_blocks_before_member_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LAB_PASSWORD", "password")
    plan = _write_plan(tmp_path, monkeypatch)
    mutated = False

    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        nonlocal mutated
        mutated = True
        return subprocess.CompletedProcess(command, 0, "", "")

    with pytest.raises(lab.MdRaidReplacementLabError, match="identities changed"):
        lab.run_phase(
            plan_path=tmp_path / "plan.json",
            phase="repair",
            confirmation=plan["confirmations"]["repair"],
            candidate_index=tmp_path / "candidate.json",
            bundle_root=tmp_path / "bundle",
            password_env="LAB_PASSWORD",
            runner=runner,
            candidate_loader=_candidate,
            bundle_loader=_bundle,
            platform_probe=_platform,
            device_verifier=lambda *_args: {"changed": True},
            tool_validator=lambda _tools, _uid: None,
            effective_uid=0,
            system_name="Linux",
        )
    assert mutated is False
