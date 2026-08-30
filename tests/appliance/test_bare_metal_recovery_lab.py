from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from deploy.appliance import bare_metal_recovery_lab as lab


def completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, "")


def canonical(value: dict[str, object]) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def appliance() -> dict[str, object]:
    return {
        "bundleVerified": True,
        "immutableImageVerified": True,
        "administratorLoginReady": True,
        "agentWorkbenchReady": True,
        "auditVerified": True,
        "dockerApprovalVerified": True,
        "runtimeArchitecture": "amd64",
    }


def private_plan(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    candidate = {
        "indexPath": str(tmp_path / "candidate.json"),
        "indexId": "1" * 64,
        "sourceRevision": "2" * 40,
        "releaseTag": "echo-appliance-v1.2.3",
    }
    canaries = {
        name: {
            "path": str(tmp_path / name),
            "sha256": digest * 64,
            "size": size,
        }
        for name, digest, size in (
            ("state", "6", lab.STATE_CANARY_BYTES),
            ("agent", "7", lab.AGENT_CANARY_BYTES),
            ("nas", "8", lab.NAS_CANARY_BYTES),
        )
    }
    backups = {
        "applianceState": {"path": str(tmp_path / "state.echo-backup"), "sha256": "3" * 64},
        "user": {
            "repository": "/mnt/echo-backup/echo-os-user",
            "repositoryId": "4" * 16,
            "snapshotId": "4" * 64,
            "fullReadVerified": True,
        },
        "nas": {
            "repository": str(tmp_path / "nas-repository"),
            "mountpoint": str(tmp_path),
            "snapshotId": "5" * 64,
            "receipt": {"sha256": "9" * 64},
        },
    }
    source = {
        "bootId": "11111111-1111-4111-8111-111111111111",
        "machineIdSha256": "a" * 64,
        "sourceRevision": candidate["sourceRevision"],
        "state": {"authenticationStateVerified": True, "auditStateVerified": True},
        "appliance": appliance(),
        "canaries": canaries,
    }
    payload: dict[str, object] = {
        "schemaVersion": 1,
        "kind": "echo.bare-metal-recovery-physical-lab-plan",
        "gate": lab.GATE,
        "releaseCandidate": candidate,
        "bundleRoot": str(tmp_path / "bundle"),
        "operationsBundle": {"artifactId": "3" * 16},
        "installer": {"target": "/dev/sda"},
        "backups": backups,
        "sourceSystem": source,
        "installedSystem": {"verifierArchitecture": "amd64"},
        "appliance": {},
        "evidenceDirectory": str(evidence),
        "phases": list(lab.PHASES),
    }
    payload["planId"] = hashlib.sha256(canonical(payload)).hexdigest()
    payload["confirmations"] = {
        phase: f"RUN ECHO BARE METAL RECOVERY LAB {phase} {payload['planId']}"
        for phase in lab.RUN_PHASES
    }
    path = tmp_path / "bare-metal-plan.json"
    path.write_bytes(canonical(payload))
    path.chmod(0o400)
    return path, payload


def valid_details(plan: dict[str, object]) -> dict[str, dict[str, object]]:
    candidate = plan["releaseCandidate"]
    source = plan["sourceSystem"]
    backups = plan["backups"]
    assert isinstance(candidate, dict) and isinstance(source, dict) and isinstance(backups, dict)
    canaries = source["canaries"]
    state = source["state"]
    app = appliance()
    transaction = "d" * 24
    entries = 3
    logical_bytes = lab.NAS_CANARY_BYTES + 17
    return {
        "source-backup": {
            "sourceRevision": source["sourceRevision"],
            "sourceBootId": source["bootId"],
            "sourceMachineIdSha256": source["machineIdSha256"],
            "backups": backups,
            "canaries": canaries,
            "state": state,
            "appliance": app,
        },
        "recovery-install": {
            "recoveryMediaVerified": True,
            "bareMetalRestored": True,
            "sourceRevision": candidate["sourceRevision"],
            "recoveryVersion": "1.2.3",
            "installerManifestSha256": "1" * 64,
            "installerSourceSha256": "2" * 64,
            "installerPlanTranscriptSha256": "3" * 64,
            "targetIdentitySha256": "4" * 64,
            "transcriptSha256": "5" * 64,
            "postWriteReadbackVerified": True,
            "recoveryBootId": "22222222-2222-4222-8222-222222222222",
        },
        "cold-boot": {
            "firstColdBootHealthy": True,
            "recoveryBootId": "22222222-2222-4222-8222-222222222222",
            "installedBootId": "33333333-3333-4333-8333-333333333333",
            "replacementMachineIdSha256": "b" * 64,
            "sourceMachineIdentityChanged": True,
            "sourceRevision": candidate["sourceRevision"],
            "appliance": app,
        },
        "restore": {
            "offDeviceBackupRestored": True,
            "applianceStateBackupSha256": backups["applianceState"]["sha256"],
            "applianceState": state,
            "userSnapshotId": backups["user"]["snapshotId"],
            "userAgentRestoreStaged": True,
            "nasSnapshotId": backups["nas"]["snapshotId"],
            "nasAtomicPromotion": True,
            "nasEntries": entries,
            "nasLogicalBytes": logical_bytes,
            "canaries": {name: canaries[name] for name in ("state", "nas")},
            "bootId": "33333333-3333-4333-8333-333333333333",
        },
        "recovery-promote": {
            "agentRestorePromoted": True,
            "transactionId": transaction,
            "recoveryBootId": "44444444-4444-4444-8444-444444444444",
            "promotionTranscriptSha256": "c" * 64,
        },
        "trial-verify": {
            "trialBootHealthy": True,
            "transactionId": transaction,
            "bootId": "55555555-5555-4555-8555-555555555555",
            "applianceState": state,
            "userAgentState": {
                "snapshotId": backups["user"]["snapshotId"],
                "action": "restore-staged",
                "fullReadVerified": True,
            },
            "nasTree": {"entries": entries, "logicalBytes": logical_bytes},
            "canaries": canaries,
            "appliance": app,
        },
        "recovery-commit": {
            "agentRestoreCommitted": True,
            "transactionId": transaction,
            "oldDataDeleted": True,
            "recoveryBootId": "66666666-6666-4666-8666-666666666666",
            "commitTranscriptSha256": "e" * 64,
        },
        "final-verify": {
            "coldBootHealthy": True,
            "dataVerified": True,
            "authenticationStateVerified": True,
            "auditStateVerified": True,
            "agentStateVerified": True,
            "nasDataVerified": True,
            "transactionId": transaction,
            "bootId": "77777777-7777-4777-8777-777777777777",
            "applianceState": state,
            "userAgentState": {
                "snapshotId": backups["user"]["snapshotId"],
                "action": "restore-committed",
                "fullReadVerified": True,
            },
            "nasTree": {"entries": entries, "logicalBytes": logical_bytes},
            "canaries": canaries,
            "appliance": app,
        },
    }


def evidence_record(plan: dict[str, object], phase: str, details: dict[str, object]) -> bytes:
    return canonical(
        {
            "schemaVersion": 1,
            "kind": "echo.bare-metal-recovery-physical-lab-evidence",
            "planId": plan["planId"],
            "phase": phase,
            "passed": True,
            "details": details,
        }
    )


def write_evidence(plan: dict[str, object], details: dict[str, dict[str, object]]) -> None:
    root = Path(plan["evidenceDirectory"])
    for phase in lab.PHASES:
        output = root / lab.PHASE_OUTPUTS[phase]
        output.write_bytes(evidence_record(plan, phase, details[phase]))
        output.chmod(0o444)


def bypass_candidate_recheck(monkeypatch: pytest.MonkeyPatch, plan: dict[str, object]) -> None:
    monkeypatch.setattr(
        lab.operations_lab,
        "_candidate_identity",
        lambda *_args, **_kwargs: plan["releaseCandidate"],
    )
    monkeypatch.setattr(lab, "_bundle_identity", lambda *_args, **_kwargs: plan["operationsBundle"])


def test_source_plan_writes_private_plan_and_public_source_backup_off_device(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mount = tmp_path / "off-device"
    bundle = mount / "bundle"
    installer = mount / "installer"
    evidence = mount / "public-evidence"
    private = mount / "private-plan"
    repository = mount / "nas-repository"
    deployment = tmp_path / "installed-candidate"
    agent = tmp_path / "agent"
    nas = tmp_path / "nas"
    for directory in (
        mount,
        bundle,
        installer,
        evidence,
        private,
        repository,
        deployment,
        agent,
        nas,
        deployment / "data",
    ):
        directory.mkdir(exist_ok=True)
    candidate_path = mount / "candidate.json"
    candidate_path.write_text("{}\n", encoding="utf-8")
    recovery_key = mount / "recovery-key"
    state_backup = mount / "state.echo-backup"
    nas_receipt = mount / "nas-receipt.json"
    for path in (recovery_key, state_backup, nas_receipt):
        path.write_bytes(b"verified\n")
    candidate = {
        "indexPath": str(candidate_path.resolve()),
        "indexId": "1" * 64,
        "sourceRevision": "2" * 40,
        "releaseTag": "echo-appliance-v1.2.3",
        "immutableReference": f"ghcr.io/echo-os/echo-os@sha256:{'3' * 64}",
    }
    operations_bundle = {"artifactId": "4" * 16, "tools": {}}
    state = {
        "authenticationStateVerified": True,
        "auditStateVerified": True,
        "auditEntries": 7,
        "auditSigningKeyId": "test-key",
        "sessionNotBefore": 1,
        "schemaVersion": 1,
    }
    monkeypatch.setattr(lab, "_validated_tools", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        lab.operations_lab, "_candidate_identity", lambda *_args, **_kwargs: candidate
    )
    monkeypatch.setattr(lab, "_bundle_identity", lambda *_args, **_kwargs: operations_bundle)
    monkeypatch.setattr(
        lab, "_source_revision", lambda *_args, **_kwargs: candidate["sourceRevision"]
    )
    monkeypatch.setattr(
        lab,
        "_hash_regular",
        lambda path, *_args, **_kwargs: {
            "path": str(path.resolve()),
            "sha256": "5" * 64,
            "size": path.stat().st_size,
        },
    )
    monkeypatch.setattr(
        lab,
        "_canary",
        lambda path, _label, *, size, **_kwargs: {
            "path": str(path),
            "sha256": "6" * 64,
            "size": size,
        },
    )
    monkeypatch.setattr(
        lab,
        "_source_user_backup_state",
        lambda *_args, **_kwargs: {
            "repositoryId": "7" * 16,
            "snapshotId": "8" * 64,
            "fullReadVerified": True,
        },
    )
    monkeypatch.setattr(
        lab,
        "_nas_backup_receipt",
        lambda path, **_kwargs: {
            "path": str(path.resolve()),
            "sha256": "9" * 64,
            "size": path.stat().st_size,
            "repositoryId": "a" * 64,
            "sourceSha256": "b" * 64,
        },
    )
    monkeypatch.setattr(
        lab.device_lab,
        "_architecture",
        lambda _machine: ("physical_x86_64_install_and_cold_boot", "x86_64", "amd64"),
    )
    monkeypatch.setattr(lab, "_state_recovery", lambda *_args, **_kwargs: state)
    monkeypatch.setattr(lab, "_running_verification", lambda *_args, **_kwargs: appliance())
    plan_path = private / "echo-bare-metal-plan.json"

    plan = lab.build_plan(
        candidate_index=candidate_path,
        bundle_root=bundle,
        install_bundle=installer,
        target_disk=Path("/dev/sdz"),
        recovery_key=recovery_key,
        appliance_backup=state_backup,
        nas_backup_receipt=nas_receipt,
        user_snapshot="8" * 64,
        nas_repository=repository,
        nas_repository_mount=mount,
        nas_snapshot="c" * 64,
        deployment_root=deployment,
        agent_root=agent,
        nas_root=nas,
        state_canary=deployment / "data" / "state-canary",
        agent_canary=agent / "agent-canary",
        nas_canary=nas / "nas-canary",
        evidence_directory=evidence,
        base_url="https://echo.test",
        main_container="echo-os",
        proxy_container="echo-docker-control",
        output=plan_path,
        boot_id_reader=lambda: "11111111-1111-4111-8111-111111111111",
        machine_id_reader=lambda: "d" * 64,
        effective_uid=0,
        trusted_uid=Path().stat().st_uid,
        system_name="Linux",
        machine="x86_64",
        user_state_file=tmp_path / "user-backup-state.json",
    )

    source_log = evidence / lab.PHASE_OUTPUTS["source-backup"]
    assert plan_path.stat().st_mode & 0o777 == 0o400
    assert source_log.stat().st_mode & 0o777 == 0o444
    assert json.loads(source_log.read_text())["details"]["backups"] == plan["backups"]
    assert set(evidence.iterdir()) == {source_log}
    assert plan["confirmations"] == {
        phase: f"RUN ECHO BARE METAL RECOVERY LAB {phase} {plan['planId']}"
        for phase in lab.RUN_PHASES
    }


def test_recovery_tool_validation_does_not_require_docker(tmp_path: Path) -> None:
    paths = {name: tmp_path / name for name in ("python", "docker", "installer", "recovery")}
    for path in paths.values():
        path.write_text("#!/bin/sh\n", encoding="utf-8")
        path.chmod(0o755)
    tools = lab.LabTools(
        python=paths["python"],
        docker=paths["docker"],
        installer=paths["installer"],
        recovery=paths["recovery"],
    )
    paths["docker"].unlink()

    lab._validated_tools(tools, trusted_uid=Path().stat().st_uid, recovery=True)
    with pytest.raises(lab.systemd.OperationsSystemdError, match="docker"):
        lab._validated_tools(tools, trusted_uid=Path().stat().st_uid, recovery=False)


def test_private_plan_binds_destructive_phase_confirmations_and_rejects_tampering(
    tmp_path: Path,
) -> None:
    _path, plan = private_plan(tmp_path)
    phase = "recovery-install"
    confirmation = plan["confirmations"][phase]

    lab._verify_plan(plan, phase, confirmation)
    with pytest.raises(lab.BareMetalRecoveryLabError, match="plan or confirmation"):
        lab._verify_plan(plan, phase, "RUN SOMETHING ELSE")

    plan["evidenceDirectory"] = "/tmp/drift"
    with pytest.raises(lab.BareMetalRecoveryLabError, match="plan or confirmation"):
        lab._verify_plan(plan, phase, confirmation)


def test_installer_plan_binds_authenticated_release_target_and_confirmation(tmp_path: Path) -> None:
    bundle = tmp_path / "install"
    bundle.mkdir()
    stdout = (
        f"ECHO_INSTALL_BUNDLE_AUTHENTICATED action=plan version=1.2.3 "
        f"manifest={'a' * 64} source={'b' * 64}\n"
        "  confirmation: INSTALL-ECHO-OS:sdz:0123456789abcdef\n"
        f"ECHO_INSTALL_PLAN_READY target=/dev/sdz version=1.2.3 source={'b' * 64}\n"
    )

    result = lab._installer_plan(
        bundle,
        Path("/dev/sdz"),
        lab.LabTools(installer=Path("/usr/bin/echo-os-installer")),
        lambda *_args, **_kwargs: completed(stdout),
    )

    assert result["target"] == "/dev/sdz"
    assert result["manifestSha256"] == "a" * 64
    assert result["sourceSha256"] == "b" * 64
    assert result["confirmation"] == "INSTALL-ECHO-OS:sdz:0123456789abcdef"


def test_installer_plan_rejects_source_identity_drift(tmp_path: Path) -> None:
    bundle = tmp_path / "install"
    bundle.mkdir()
    stdout = (
        f"ECHO_INSTALL_BUNDLE_AUTHENTICATED action=plan version=1.2.3 "
        f"manifest={'a' * 64} source={'b' * 64}\n"
        "  confirmation: INSTALL-ECHO-OS:sdz:0123456789abcdef\n"
        f"ECHO_INSTALL_PLAN_READY target=/dev/sdz version=1.2.3 source={'c' * 64}\n"
    )

    with pytest.raises(lab.BareMetalRecoveryLabError, match="identity changed"):
        lab._installer_plan(
            bundle,
            Path("/dev/sdz"),
            lab.LabTools(),
            lambda *_args, **_kwargs: completed(stdout),
        )


def test_recovery_identity_requires_one_candidate_source_marker() -> None:
    stdout = f"Echo Recovery 1.2.3\nECHO_RECOVERY_READY version=1.2.3 os={'d' * 40}\n"

    result = lab._recovery_identity(lab.LabTools(), lambda *_args, **_kwargs: completed(stdout))

    assert result == {"version": "1.2.3", "sourceRevision": "d" * 40}


def test_private_plan_and_public_logs_must_survive_below_the_off_device_mount() -> None:
    mount = Path("/mnt/off-device")

    assert lab._strictly_below(mount / "private", mount)
    assert lab._strictly_below(mount / "g6-evidence", mount)
    assert not lab._strictly_below(mount, mount)
    assert not lab._strictly_below(Path("/root"), mount)


def test_phase_sequence_requires_every_prior_valid_read_only_record(tmp_path: Path) -> None:
    _path, plan = private_plan(tmp_path)
    root = Path(plan["evidenceDirectory"])
    details = valid_details(plan)
    first = lab.PHASES[0]
    (root / lab.PHASE_OUTPUTS[first]).write_bytes(evidence_record(plan, first, details[first]))
    (root / lab.PHASE_OUTPUTS[first]).chmod(0o444)

    lab._phase_dependencies(root, lab.RUN_PHASES[0], plan=plan, trusted_uid=Path().stat().st_uid)
    with pytest.raises(lab.BareMetalRecoveryLabError, match="sequence"):
        lab._phase_dependencies(root, first, plan=plan, trusted_uid=Path().stat().st_uid)


@pytest.mark.parametrize(
    ("action", "line", "expected"),
    [
        (
            "restore-plan",
            "Promote or resume: PROMOTE-ECHO-RESTORE-aaaaaaaaaaaaaaaaaaaaaaaa",
            "aaaaaaaaaaaaaaaaaaaaaaaa",
        ),
        (
            "restore-status",
            "Commit and delete old data: COMMIT-ECHO-RESTORE-bbbbbbbbbbbbbbbbbbbbbbbb",
            "bbbbbbbbbbbbbbbbbbbbbbbb",
        ),
    ],
)
def test_recovery_transaction_token_is_unique_and_bound(
    action: str, line: str, expected: str
) -> None:
    token, transaction = lab._transaction_command(
        action,
        "/dev/sda",
        lab.LabTools(),
        lambda *_args, **_kwargs: completed(line + "\n"),
    )

    assert transaction == expected
    assert token.endswith(expected)


def test_verify_evidence_binds_all_eight_phases_and_nine_gate_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, plan = private_plan(tmp_path)
    write_evidence(plan, valid_details(plan))
    bypass_candidate_recheck(monkeypatch, plan)

    report = lab.verify_evidence(path, trusted_uid=Path().stat().st_uid)

    assert report["allPassed"] is True
    assert set(report["phases"]) == set(lab.PHASES)
    assert set(report["checks"]) == set(lab.CHECK_OUTPUTS)
    assert all(report["checks"].values())


def test_verify_evidence_rejects_a_claim_without_final_nas_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, plan = private_plan(tmp_path)
    details = valid_details(plan)
    details["final-verify"]["nasDataVerified"] = False
    write_evidence(plan, details)
    bypass_candidate_recheck(monkeypatch, plan)

    with pytest.raises(lab.BareMetalRecoveryLabError, match="final-verify"):
        lab.verify_evidence(path, trusted_uid=Path().stat().st_uid)
