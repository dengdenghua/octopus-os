from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from deploy.appliance import device_endurance_lab as lab

REPOSITORY = Path(__file__).resolve().parents[2]
OPERATIONS_ARTIFACT_ID = "9" * 16
IMAGE_REFERENCE = f"ghcr.io/echo-os/echo-os@sha256:{'7' * 64}"
BOOT_A = "11111111-1111-4111-8111-111111111111"
BOOT_B = "22222222-2222-4222-8222-222222222222"


def _candidate_index(tmp_path: Path) -> Path:
    value: dict[str, Any] = {
        "schemaVersion": 1,
        "kind": "echo.delivery-release-evidence-index",
        "source": {
            "repository": "dengdenghua/echo-os",
            "commit": "1" * 40,
            "agentRepository": "dengdenghua/echo-agent",
            "agentCommit": "2" * 40,
            "releaseTag": "echo-appliance-v1.0.0",
        },
        "evidence": {
            "candidatePreflight": {"reportId": "4" * 64},
            "appliance": {
                "manifestSha256": "5" * 64,
                "immutableReference": IMAGE_REFERENCE,
                "operationsBundle": {
                    "artifactId": OPERATIONS_ARTIFACT_ID,
                    "sha256": "8" * 64,
                    "imageReference": IMAGE_REFERENCE,
                },
            },
        },
        "ciReleaseCandidateReady": True,
        "nasProductDeliveryReady": False,
        "physicalAcceptance": {
            "complete": False,
            "remainingGates": [lab.X86_GATE],
        },
    }
    value["indexId"] = hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    path = tmp_path / "echo-delivery-release-evidence-index.json"
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o444)
    return path


def _bundle(tmp_path: Path) -> Path:
    root = tmp_path / f"echo-appliance-operations-{OPERATIONS_ARTIFACT_ID}"
    root.mkdir()
    records: dict[str, dict[str, Any]] = {}
    for name in ("device_endurance_lab.py", "verify-running-appliance.py"):
        source = REPOSITORY / "deploy/appliance" / name
        destination = root / name
        shutil.copyfile(source, destination)
        destination.chmod(0o755)
        raw = destination.read_bytes()
        records[name] = {
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size": len(raw),
            "mode": "0755",
        }
    manifest = {
        "schemaVersion": 1,
        "artifact": {
            "id": OPERATIONS_ARTIFACT_ID,
            "name": root.name,
            "architectures": ["amd64", "arm64"],
            "imageReference": IMAGE_REFERENCE,
            "entrypoints": {"deviceEnduranceLab": "./device_endurance_lab.py plan|run"},
        },
        "files": records,
    }
    (root / "bundle-manifest.json").write_text(
        json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
    )
    (root / "bundle-manifest.json").chmod(0o644)
    return root


def _tools(tmp_path: Path) -> lab.LabTools:
    root = tmp_path / "tools"
    root.mkdir()
    paths: dict[str, Path] = {}
    for name in ("python3", "docker", "journalctl", "logger", "sync", "dpkg-query"):
        path = root / name
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        path.chmod(0o755)
        paths[name] = path
    return lab.LabTools(
        python=paths["python3"],
        docker=paths["docker"],
        journalctl=paths["journalctl"],
        logger=paths["logger"],
        sync=paths["sync"],
        dpkg_query=paths["dpkg-query"],
    )


def _installer_log(tmp_path: Path) -> Path:
    path = tmp_path / "private-installer.log"
    path.write_text(
        "\n".join(
            (
                f"ECHO_INSTALL_BUNDLE_AUTHENTICATED action=install version=1.0.0 manifest={'a' * 64} source={'b' * 64}",
                "ECHO_INSTALL_TARGET_LOCKED target=/dev/sdz device-id=65:0 identity=stable",
                "  verified: exact uncompressed image bytes by direct post-flush readback",
                f"ECHO_INSTALL_COMPLETE target=/dev/sdz version=1.0.0 source={'b' * 64} home=/dev/sdz10 data=luks2-tpm2-signed-pcr11-recovery",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    path.chmod(0o400)
    return path


def _os_release(tmp_path: Path) -> Path:
    path = tmp_path / "os-release"
    path.write_text('ID="debian"\nVERSION_ID="13"\n', encoding="utf-8")
    return path


def _family_fixture(tmp_path: Path) -> Path:
    path = tmp_path / "family-isolation.json"
    path.write_text('{"private":"fixture"}\n', encoding="utf-8")
    path.chmod(0o400)
    return path


def _running_result() -> dict[str, Any]:
    return {
        "bundle_verified": True,
        "bundle_dirty": False,
        "login": 200,
        "workbench": 200,
        "architecture": "amd64",
        "main_has_docker_socket": False,
        "proxy_network_internal": True,
        "main_effective_capabilities": 0,
        "proxy_effective_capabilities": 0,
        "no_new_privileges": True,
        "approval": 200,
        "approval_replay": 403,
        "protected_stop": 403,
        "audit_verify": 200,
        "nas_transfer": {
            "writeExecuted": True,
            "size": lab.NAS_TRANSFER_BYTES,
            "restartVerified": True,
            "cancelVerified": True,
            "recycleRestoreVerified": True,
            "physicallyDeleted": False,
            "sha256": "c" * 64,
            "restoredSha256": "c" * 64,
        },
        "family_isolation": {
            "verified": True,
            "memberCount": 2,
            "identitySetSha256": "e" * 64,
            "policySetSha256": "f" * 64,
            "accountDirectoryIsolated": True,
            "fileProjectionVerified": True,
            "photoProjectionVerified": True,
            "memberManagementRejected": True,
            "secretsReturned": False,
        },
    }


class Harness:
    def __init__(self, tools: lab.LabTools) -> None:
        self.tools = tools
        self.commands: list[list[str]] = []

    def runner(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        name = Path(command[0]).name
        if name == "dpkg-query":
            return subprocess.CompletedProcess(command, 0, "8.7.3-1", "")
        if name == "docker":
            return subprocess.CompletedProcess(command, 0, IMAGE_REFERENCE + "\n", "")
        if name == "python3":
            return subprocess.CompletedProcess(command, 0, json.dumps(_running_result()), "")
        return subprocess.CompletedProcess(command, 0, "", "")


def _fixture(tmp_path: Path) -> tuple[dict[str, Any], Path, lab.LabTools, Harness, Path]:
    candidate = _candidate_index(tmp_path)
    bundle = _bundle(tmp_path)
    tools = _tools(tmp_path)
    harness = Harness(tools)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    plan_path = tmp_path / "device-plan.json"
    plan = lab.build_plan(
        candidate_index=candidate,
        bundle_root=bundle,
        installer_log=_installer_log(tmp_path),
        evidence_directory=evidence,
        nas_transfer_path="lab/device",
        family_isolation_fixture=_family_fixture(tmp_path),
        base_url="https://127.0.0.1:8000",
        main_container="echo-os",
        proxy_container="echo-docker-control",
        output=plan_path,
        tools=tools,
        runner=harness.runner,
        boot_id_reader=lambda: BOOT_A,
        uptime_reader=lambda: 60.0,
        device_identity_reader=lambda: "d" * 64,
        effective_uid=0,
        trusted_uid=os.getuid(),
        system_name="Linux",
        machine="x86_64",
        os_release=_os_release(tmp_path),
    )
    return plan, plan_path, tools, harness, evidence


def _run(
    plan: dict[str, Any],
    plan_path: Path,
    tools: lab.LabTools,
    harness: Harness,
    phase: str,
    *,
    boot_id: str,
    now_ns: int,
    journal: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return lab.run_phase(
        plan_path=plan_path,
        phase=phase,
        confirmation=plan["confirmations"][phase],
        tools=tools,
        runner=harness.runner,
        boot_id_reader=lambda: boot_id,
        clock_ns=lambda: now_ns,
        uptime_reader=lambda: 60.0,
        device_identity_reader=lambda: "d" * 64,
        journal_probe=(
            (lambda *_args: journal)
            if journal is not None
            else lambda *_args: {
                "persistentJournalAvailable": True,
                "powerCutIntentFound": True,
                "cleanShutdownFound": False,
            }
        ),
        effective_uid=0,
        trusted_uid=os.getuid(),
        system_name="Linux",
        os_release=plan_path.parent / "os-release",
    )


def test_device_lab_runs_first_boot_soak_and_unclean_power_recovery(tmp_path: Path) -> None:
    plan, plan_path, tools, harness, evidence = _fixture(tmp_path)
    start = 1_800_000_000_000_000_000

    _run(plan, plan_path, tools, harness, "baseline", boot_id=BOOT_A, now_ns=start)
    _run(
        plan,
        plan_path,
        tools,
        harness,
        "soak",
        boot_id=BOOT_A,
        now_ns=start + lab.MIN_SOAK_SECONDS * 10**9,
    )
    _run(
        plan,
        plan_path,
        tools,
        harness,
        "arm-power-cut",
        boot_id=BOOT_A,
        now_ns=start + lab.MIN_SOAK_SECONDS * 10**9 + 1,
    )
    result = _run(
        plan,
        plan_path,
        tools,
        harness,
        "recovered",
        boot_id=BOOT_B,
        now_ns=start + lab.MIN_SOAK_SECONDS * 10**9 + 2,
    )

    assert result["phase"] == "recovered"
    assert plan["gate"] == lab.X86_GATE
    assert plan["minimumSoakSeconds"] == 86400
    assert plan_path.stat().st_mode & 0o777 == 0o400
    assert {path.name for path in evidence.iterdir()} == set(lab.PHASE_OUTPUTS.values())
    assert all(path.stat().st_mode & 0o777 == 0o444 for path in evidence.iterdir())
    baseline = json.loads((evidence / "device-baseline.log").read_text())
    recovered = json.loads((evidence / "device-recovered.log").read_text())
    assert baseline["details"]["installerCompleted"] is True
    assert baseline["details"]["appliance"]["oneGiBTransferVerified"] is True
    assert baseline["details"]["appliance"]["familyMemberIsolationVerified"] is True
    assert recovered["details"]["hardPowerCycleRecovered"] is True
    assert "/dev/sdz" not in json.dumps(baseline)
    verifier_commands = [
        command
        for command in harness.commands
        if len(command) > 1 and command[1].endswith("verify-running-appliance.py")
    ]
    assert verifier_commands
    assert all("--require-family-isolation" in command for command in verifier_commands)
    assert all(
        command[command.index("--family-isolation-fixture") + 1]
        == plan["appliance"]["familyIsolationFixture"]["path"]
        for command in verifier_commands
    )


def test_device_lab_rejects_family_fixture_drift(tmp_path: Path) -> None:
    plan, plan_path, tools, harness, _evidence = _fixture(tmp_path)
    fixture = Path(plan["appliance"]["familyIsolationFixture"]["path"])
    fixture.chmod(0o600)
    fixture.write_text('{"private":"changed"}\n', encoding="utf-8")
    fixture.chmod(0o400)

    with pytest.raises(lab.DeviceEnduranceLabError, match="drifted"):
        _run(
            plan,
            plan_path,
            tools,
            harness,
            "baseline",
            boot_id=BOOT_A,
            now_ns=1_800_000_000_000_000_000,
        )


def test_device_lab_rejects_short_soak_and_clean_shutdown(tmp_path: Path) -> None:
    plan, plan_path, tools, harness, _evidence = _fixture(tmp_path)
    start = 1_800_000_000_000_000_000
    _run(plan, plan_path, tools, harness, "baseline", boot_id=BOOT_A, now_ns=start)

    with pytest.raises(lab.DeviceEnduranceLabError, match="24 hours"):
        _run(
            plan,
            plan_path,
            tools,
            harness,
            "soak",
            boot_id=BOOT_A,
            now_ns=start + 60 * 10**9,
        )

    _run(
        plan,
        plan_path,
        tools,
        harness,
        "soak",
        boot_id=BOOT_A,
        now_ns=start + lab.MIN_SOAK_SECONDS * 10**9,
    )
    _run(
        plan,
        plan_path,
        tools,
        harness,
        "arm-power-cut",
        boot_id=BOOT_A,
        now_ns=start + lab.MIN_SOAK_SECONDS * 10**9 + 1,
    )
    with pytest.raises(lab.DeviceEnduranceLabError, match="real unclean"):
        _run(
            plan,
            plan_path,
            tools,
            harness,
            "recovered",
            boot_id=BOOT_B,
            now_ns=start + lab.MIN_SOAK_SECONDS * 10**9 + 2,
            journal={
                "persistentJournalAvailable": True,
                "powerCutIntentFound": True,
                "cleanShutdownFound": True,
            },
        )


def test_x86_plan_requires_https_and_private_complete_installer_log(tmp_path: Path) -> None:
    candidate = _candidate_index(tmp_path)
    bundle = _bundle(tmp_path)
    tools = _tools(tmp_path)
    harness = Harness(tools)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    installer = _installer_log(tmp_path)

    with pytest.raises(lab.DeviceEnduranceLabError, match="HTTPS"):
        lab.build_plan(
            candidate_index=candidate,
            bundle_root=bundle,
            installer_log=installer,
            evidence_directory=evidence,
            nas_transfer_path="lab/device",
            family_isolation_fixture=_family_fixture(tmp_path),
            base_url="http://127.0.0.1:8000",
            main_container="echo-os",
            proxy_container="echo-docker-control",
            output=tmp_path / "plan.json",
            tools=tools,
            runner=harness.runner,
            boot_id_reader=lambda: BOOT_A,
            uptime_reader=lambda: 60.0,
            device_identity_reader=lambda: "d" * 64,
            effective_uid=0,
            trusted_uid=os.getuid(),
            system_name="Linux",
            machine="x86_64",
            os_release=_os_release(tmp_path),
        )

    installer.chmod(0o600)
    with pytest.raises(lab.systemd.OperationsSystemdError, match="mode"):
        lab._installer_record(installer, trusted_uid=os.getuid())
