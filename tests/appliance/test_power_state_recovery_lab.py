from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from deploy.appliance import operations_bundle, operations_systemd_lab
from deploy.appliance import operations_systemd as systemd
from deploy.appliance import power_state_recovery_lab as lab

REPOSITORY = Path(__file__).resolve().parents[2]
TARGET_IMAGE = f"ghcr.io/echo-os/echo-os@sha256:{'7' * 64}"
PREVIOUS_IMAGE = f"ghcr.io/echo-os/echo-os@sha256:{'6' * 64}"
BOOT_A = "11111111-1111-4111-8111-111111111111"
BOOT_B = "22222222-2222-4222-8222-222222222222"


def _candidate_index(tmp_path: Path, report: dict[str, Any]) -> Path:
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
                "immutableReference": TARGET_IMAGE,
                "operationsBundle": {
                    "artifactId": report["artifactId"],
                    "sha256": report["archiveSha256"],
                    "imageReference": TARGET_IMAGE,
                },
            },
        },
        "ciReleaseCandidateReady": True,
        "nasProductDeliveryReady": False,
        "physicalAcceptance": {"complete": False, "remainingGates": [lab.GATE]},
    }
    value["indexId"] = hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    path = tmp_path / "echo-delivery-release-evidence-index.json"
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o444)
    return path


def _tools(tmp_path: Path) -> lab.LabTools:
    root = tmp_path / "tools"
    root.mkdir()
    paths: dict[str, Path] = {}
    for name in (
        "docker",
        "systemctl",
        "systemd-run",
        "systemd-creds",
        "journalctl",
        "logger",
        "sync",
        "dpkg-query",
    ):
        path = root / name
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        path.chmod(0o755)
        paths[name] = path
    return lab.LabTools(
        docker=paths["docker"],
        systemctl=paths["systemctl"],
        systemd_run=paths["systemd-run"],
        systemd_creds=paths["systemd-creds"],
        journalctl=paths["journalctl"],
        logger=paths["logger"],
        sync=paths["sync"],
        dpkg_query=paths["dpkg-query"],
    )


def _operations_plan(
    tmp_path: Path,
    candidate: dict[str, str],
    bundle_root: Path,
) -> Path:
    backup_mount = tmp_path / "backup-mount"
    backup_directory = backup_mount / "echo-os"
    audit_mount = tmp_path / "audit-mount"
    audit_directory = audit_mount / "echo-os"
    for path in (backup_directory, audit_directory):
        path.mkdir(parents=True)
    credentials = tmp_path / "credentials"
    credentials.mkdir(mode=0o700)
    backup_credential = credentials / "echo-backup-passphrase"
    audit_credential = credentials / "echo-audit-passphrase"
    for path in (backup_credential, audit_credential):
        path.write_bytes(b"encrypted-credential")
        path.chmod(0o600)
    config = {
        "bundleRoot": str(bundle_root),
        "backupDirectory": str(backup_directory),
        "backupMountpoint": str(backup_mount),
        "auditDirectory": str(audit_directory),
        "auditMountpoint": str(audit_mount),
        "backupCredential": str(backup_credential),
        "auditCredential": str(audit_credential),
        "backupKeep": 7,
        "auditKeepDays": 365,
        "auditKeepMinimum": 12,
    }
    payload: dict[str, Any] = {
        "schemaVersion": operations_systemd_lab.SCHEMA_VERSION,
        "kind": "echo.operations-systemd-physical-lab-plan",
        "releaseCandidate": candidate,
        "operationsBundle": operations_systemd_lab._operations_bundle_identity(
            bundle_root,
            candidate,
            trusted_uid=os.getuid(),
        ),
        "platform": {"id": "debian", "versionId": "13", "omvVersion": "8.7.3-1"},
        "config": config,
        "installPlan": {
            "config": config,
            "recoveryService": systemd.RECOVERY_SERVICE_NAME,
        },
        "evidenceDirectory": str(tmp_path / "operations-evidence"),
        "preservation": {},
        "baseline": {},
        "phases": list(operations_systemd_lab.PHASES),
    }
    payload["planId"] = systemd._sha256(systemd._canonical_json(payload))
    payload["confirmations"] = {
        phase: f"RUN ECHO OPERATIONS LAB {phase} {payload['planId']}"
        for phase in operations_systemd_lab.PHASES
    }
    path = tmp_path / "operations-lab-plan.json"
    path.write_bytes(systemd._canonical_json(payload))
    path.chmod(0o400)
    return path


def _fake_execute(
    command: list[str],
    *,
    environment: object = None,
    timeout: int = 3600,
) -> subprocess.CompletedProcess[str]:
    del environment, timeout
    name = Path(command[0]).name
    if name == "docker" and command[1:2] == ["inspect"]:
        return subprocess.CompletedProcess(command, 0, PREVIOUS_IMAGE + "\n", "")
    if name == "systemctl" and command[1:2] == ["is-enabled"]:
        return subprocess.CompletedProcess(command, 0, "enabled\n", "")
    if name == "systemctl" and command[1:2] == ["is-active"]:
        return subprocess.CompletedProcess(command, 3, "inactive\n", "")
    return subprocess.CompletedProcess(command, 0, "", "")


def _fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[dict[str, Any], Path, Path, lab.LabTools]:
    monkeypatch.setattr(lab, "STATE_CANARY_BYTES", 4096)
    monkeypatch.setattr(lab, "NAS_CANARY_BYTES", 8192)
    monkeypatch.setattr(lab, "_execute", _fake_execute)
    report = operations_bundle.build(REPOSITORY, tmp_path / "release", TARGET_IMAGE)
    extracted = operations_bundle.extract(Path(report["archive"]), tmp_path / "installed")
    root = Path(extracted["destination"])
    (root / "echo-release.env").write_text(
        f"ECHO_OS_IMAGE={PREVIOUS_IMAGE}\n",
        encoding="ascii",
    )
    (root / "echo-release.env").chmod(0o600)
    data = root / "data"
    data.mkdir()
    state_canary = data / "power-state-canary.bin"
    state_canary.write_bytes(b"s" * lab.STATE_CANARY_BYTES)
    state_canary.chmod(0o600)
    nas_canary = tmp_path / "nas-preservation-canary.bin"
    nas_canary.write_bytes(b"n" * lab.NAS_CANARY_BYTES)
    nas_canary.chmod(0o600)
    candidate_path = _candidate_index(tmp_path, report)
    candidate = operations_systemd_lab._candidate_identity(
        candidate_path,
        trusted_uid=os.getuid(),
    )
    operations_plan = _operations_plan(tmp_path, candidate, root)
    evidence = tmp_path / "power-evidence"
    evidence.mkdir()
    tools = _tools(tmp_path)
    plan_path = tmp_path / "power-state-plan.json"
    plan = lab.build_plan(
        candidate_index=candidate_path,
        bundle_root=root,
        operations_lab_plan=operations_plan,
        evidence_directory=evidence,
        state_canary=state_canary,
        nas_canary=nas_canary,
        main_container="echo-os",
        proxy_container="echo-docker-control",
        output=plan_path,
        tools=tools,
        effective_uid=0,
        trusted_uid=os.getuid(),
        system_name="Linux",
        boot_id=BOOT_A,
    )
    return plan, plan_path, evidence, tools


def _details(plan: dict[str, Any], phase: str) -> dict[str, Any]:
    canaries = plan["canaries"]
    values: dict[str, dict[str, Any]] = {
        "baseline": {
            "previousImageVerified": True,
            "targetImage": TARGET_IMAGE,
            "bootId": BOOT_A,
            "recoveryService": {"enabled": True, "active": False},
            "canaries": canaries,
        },
        "arm-power-cut": {
            "physicalPowerCutArmed": True,
            "bootId": BOOT_A,
            "marker": f"ECHO_POWER_STATE_UPDATE_CUT_ARMED plan={plan['planId']} boot={BOOT_A}",
            "transactionId": "a" * 64,
            "transactionPhase": "selected",
            "targetSelected": True,
            "nextAction": "physically-remove-and-restore-power",
        },
        "recover-power-cut": {
            "updatePowerLossRolledBack": True,
            "bootIdChanged": True,
            "previousBootId": BOOT_A,
            "currentBootId": BOOT_B,
            "uncleanShutdownVerified": True,
            "automaticRecoveryServiceResult": "success",
            "previousImageRestored": PREVIOUS_IMAGE,
            "canaries": canaries,
            "journal": {
                "persistentJournalAvailable": True,
                "powerCutIntentFound": True,
                "cleanShutdownFound": False,
            },
        },
        "upgrade-success": {
            "immutableDigestUpgradeVerified": True,
            "previousImage": PREVIOUS_IMAGE,
            "targetImage": TARGET_IMAGE,
            "transactionCommitted": True,
            "canaries": canaries,
        },
        "upgrade-failure": {
            "failedUpgradeRollbackVerified": True,
            "failureInjectedAfterSelection": True,
            "candidateImageRestored": TARGET_IMAGE,
            "transactionRecovered": True,
            "canaries": canaries,
        },
        "managed-uninstall": {
            "managedUninstallDataPreserved": True,
            "composeVolumesRemoved": False,
            "containersReinstalled": True,
            "canaries": canaries,
        },
        "state-restore": {
            "stateRestoreCommitted": True,
            "dataPreserved": True,
            "readOnlyPreflightVerified": True,
            "externalBackup": {
                "path": str(Path(plan["backup"]["directory"]) / "verified.echo-backup"),
                "sha256": "b" * 64,
                "size": 1024,
            },
            "previousStateRetained": True,
            "canaries": canaries,
        },
    }
    return values[phase]


def test_candidate_bundle_seed_requires_exact_confirmation_and_current_container_agreement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(lab, "_execute", _fake_execute)
    report = operations_bundle.build(REPOSITORY, tmp_path / "release", TARGET_IMAGE)
    extracted = operations_bundle.extract(Path(report["archive"]), tmp_path / "installed")
    root = Path(extracted["destination"])
    candidate_path = _candidate_index(tmp_path, report)
    tools = _tools(tmp_path)

    preview = lab.seed_candidate_bundle(
        candidate_index=candidate_path,
        bundle_root=root,
        main_container="echo-os",
        proxy_container="echo-docker-control",
        confirmation=None,
        tools=tools,
        effective_uid=0,
        trusted_uid=os.getuid(),
        system_name="Linux",
    )

    assert preview["seeded"] is False
    assert lab._release_image(root / "echo-release.env", trusted_uid=os.getuid()) == TARGET_IMAGE
    with pytest.raises(lab.PowerStateRecoveryLabError, match="confirmation"):
        lab.seed_candidate_bundle(
            candidate_index=candidate_path,
            bundle_root=root,
            main_container="echo-os",
            proxy_container="echo-docker-control",
            confirmation="SEED SOMETHING ELSE",
            tools=tools,
            effective_uid=0,
            trusted_uid=os.getuid(),
            system_name="Linux",
        )
    seeded = lab.seed_candidate_bundle(
        candidate_index=candidate_path,
        bundle_root=root,
        main_container="echo-os",
        proxy_container="echo-docker-control",
        confirmation=preview["requiredConfirmation"],
        tools=tools,
        effective_uid=0,
        trusted_uid=os.getuid(),
        system_name="Linux",
    )
    assert seeded["seeded"] is True
    assert seeded["alreadySeeded"] is False
    assert lab._release_image(root / "echo-release.env", trusted_uid=os.getuid()) == PREVIOUS_IMAGE

    repeated = lab.seed_candidate_bundle(
        candidate_index=candidate_path,
        bundle_root=root,
        main_container="echo-os",
        proxy_container="echo-docker-control",
        confirmation=None,
        tools=tools,
        effective_uid=0,
        trusted_uid=os.getuid(),
        system_name="Linux",
    )
    assert repeated["alreadySeeded"] is True

    dependency = root / "external_storage.py"
    dependency.write_bytes(dependency.read_bytes() + b"\n# replaced after candidate extraction\n")
    dependency.chmod(0o755)
    with pytest.raises(lab.PowerStateRecoveryLabError, match="inventory bytes drifted"):
        lab.seed_candidate_bundle(
            candidate_index=candidate_path,
            bundle_root=root,
            main_container="echo-os",
            proxy_container="echo-docker-control",
            confirmation=None,
            tools=tools,
            effective_uid=0,
            trusted_uid=os.getuid(),
            system_name="Linux",
        )


def test_plan_is_bound_to_candidate_bundle_and_operations_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, plan_path, _evidence, _tools = _fixture(tmp_path, monkeypatch)

    assert plan["previousImage"] == PREVIOUS_IMAGE
    assert plan["targetImage"] == TARGET_IMAGE
    assert plan["operationsBundle"]["tools"]["power_state_recovery_lab.py"]
    assert plan["canaries"]["state"]["size"] == lab.STATE_CANARY_BYTES
    assert plan["canaries"]["nas"]["size"] == lab.NAS_CANARY_BYTES
    assert plan_path.stat().st_mode & 0o777 == 0o400
    assert lab._load_plan(plan_path, trusted_uid=os.getuid()) == plan


def test_all_phases_are_ordered_and_semantically_verified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, plan_path, evidence, tools = _fixture(tmp_path, monkeypatch)

    def run_phase_real(
        current: dict[str, Any],
        phase: str,
        _tools: lab.LabTools,
        *,
        plan_path: Path,
        trusted_uid: int,
    ) -> dict[str, Any]:
        del _tools, plan_path, trusted_uid
        return _details(current, phase)

    monkeypatch.setattr(lab, "_run_phase_real", run_phase_real)
    for phase in lab.PHASES:
        result = lab.run_phase(
            plan_path=plan_path,
            phase=phase,
            confirmation=plan["confirmations"][phase],
            tools=tools,
            effective_uid=0,
            trusted_uid=os.getuid(),
            system_name="Linux",
        )
        assert result["output"] == lab.PHASE_OUTPUTS[phase]

    verified = lab.verify_evidence(
        plan_path=plan_path,
        evidence_directory=evidence,
        trusted_uid=os.getuid(),
    )
    assert set(verified["phases"]) == set(lab.PHASES)
    assert set(verified["checks"]) == set(lab.CHECK_OUTPUTS)
    assert all(value["passed"] is True for value in verified["checks"].values())

    recovered = evidence / lab.PHASE_OUTPUTS["recover-power-cut"]
    value = json.loads(recovered.read_text(encoding="utf-8"))
    value["details"]["journal"]["cleanShutdownFound"] = True
    recovered.chmod(0o600)
    recovered.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    recovered.chmod(0o444)
    with pytest.raises(lab.PowerStateRecoveryLabError, match="details are invalid"):
        lab.verify_evidence(
            plan_path=plan_path,
            evidence_directory=evidence,
            trusted_uid=os.getuid(),
        )


def test_downgrade_fault_proxy_only_fails_after_previous_image_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = {"releaseEnvironment": "/opt/echo-release.env", "previousImage": PREVIOUS_IMAGE}
    monkeypatch.setenv("ECHO_POWER_STATE_LAB_PLAN", "/run/power-plan.json")
    monkeypatch.setenv("ECHO_POWER_STATE_PROXY_MODE", "fail")
    monkeypatch.setenv("ECHO_POWER_STATE_REAL_DOCKER", "/usr/bin/docker")
    monkeypatch.setattr(lab, "_load_plan", lambda *_args, **_kwargs: plan)
    monkeypatch.setattr(lab, "_release_image", lambda *_args, **_kwargs: PREVIOUS_IMAGE)

    assert lab._proxy_main(["compose", "up", "-d"]) == 42

    called: list[list[str]] = []

    def execv(_executable: str, command: list[str]) -> None:
        called.append(command)
        raise RuntimeError("delegated to real Docker")

    monkeypatch.setattr(lab, "_release_image", lambda *_args, **_kwargs: TARGET_IMAGE)
    monkeypatch.setattr(os, "execv", execv)
    with pytest.raises(RuntimeError, match="delegated"):
        lab._proxy_main(["compose", "up", "-d"])
    assert called == [["/usr/bin/docker", "compose", "up", "-d"]]
