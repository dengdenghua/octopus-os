from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest

from tests.appliance.hub_lifecycle_fixture import hub_lifecycle_material
from tests.appliance.lan_discovery_functional_fixture import (
    lan_discovery_functional_material,
)
from tests.appliance.paperless_functional_fixture import paperless_functional_material

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "appliance" / "physical_acceptance.py"
SPEC = importlib.util.spec_from_file_location("echo_physical_acceptance", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
physical = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(physical)

OS_COMMIT = "1" * 40
AGENT_COMMIT = "2" * 40
RELEASE_TAG = "echo-appliance-v1.0.0"
KEYRING_SHA = "3" * 64
SIGNER_FINGERPRINT = "A" * 40


def test_cli_help_runs_from_outside_repository(tmp_path: Path) -> None:
    completed = subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT), "--help"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "Verify six signed physical gates" in completed.stdout


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _candidate() -> dict[str, Any]:
    value = {
        "schemaVersion": 1,
        "kind": "echo.delivery-release-evidence-index",
        "source": {
            "repository": "dengdenghua/echo-os",
            "commit": OS_COMMIT,
            "agentRepository": "dengdenghua/echo-agent",
            "agentCommit": AGENT_COMMIT,
            "releaseTag": RELEASE_TAG,
        },
        "evidence": {
            "candidatePreflight": {"reportId": "4" * 64},
            "appliance": {
                "manifestSha256": "5" * 64,
                "immutableReference": f"ghcr.io/echo-os/echo-os@sha256:{'6' * 64}",
                "operationsBundle": {
                    "artifactId": "7" * 16,
                    "sha256": "8" * 64,
                    "imageReference": f"ghcr.io/echo-os/echo-os@sha256:{'6' * 64}",
                },
            },
        },
        "ciReleaseCandidateReady": True,
        "nasProductDeliveryReady": False,
        "physicalAcceptance": {
            "complete": False,
            "remainingGates": list(physical.PHYSICAL_GATES),
        },
    }
    value["indexId"] = _digest(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())
    return value


def _write_json(path: Path, value: Any) -> bytes:
    raw = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    path.write_bytes(raw)
    return raw


def _hardware_profile(gate: str, index: int) -> bytes:
    requirement = physical.GATE_REQUIREMENTS[gate]
    architecture = requirement["architecture"] or "x86_64"
    return (
        json.dumps(
            {
                "schemaVersion": 1,
                "kind": "echo.physical-hardware-profile",
                "gate": gate,
                "profileClass": requirement["profileClass"],
                "architecture": architecture,
                "deviceCount": requirement["minimumDevices"],
                "serialsRedacted": True,
            },
            sort_keys=True,
        )
        + "\n"
    ).encode()


def _gate_result(gate: str) -> bytes:
    return (
        json.dumps(physical._gate_result_payload(gate), sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()


def _operations_systemd_lifecycle(log: bytes, candidate: dict[str, Any]) -> bytes:
    evidence = {
        check: {"name": "acceptance.log", "sha256": _digest(log), "size": len(log)}
        for check in physical.OPERATIONS_SYSTEMD_LIFECYCLE_CHECKS
    }
    return (
        json.dumps(
            physical._operations_systemd_lifecycle_payload(
                evidence,
                candidate={
                    "indexId": candidate["indexId"],
                    "sourceRevision": candidate["source"]["commit"],
                    "agentRevision": candidate["source"]["agentCommit"],
                    "releaseTag": candidate["source"]["releaseTag"],
                    "operationsArtifactId": candidate["evidence"]["appliance"]["operationsBundle"][
                        "artifactId"
                    ],
                    "operationsArchiveSha256": candidate["evidence"]["appliance"][
                        "operationsBundle"
                    ]["sha256"],
                },
                lab_plan_id="9" * 64,
            ),
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()


def _power_state_material(candidate: dict[str, Any]) -> tuple[dict[str, bytes], bytes]:
    plan_id = "d" * 64
    boot_a = "11111111-1111-4111-8111-111111111111"
    boot_b = "22222222-2222-4222-8222-222222222222"
    previous = f"ghcr.io/echo-os/echo-os@sha256:{'5' * 64}"
    target = candidate["evidence"]["appliance"]["immutableReference"]
    canaries = {
        "state": {
            "path": "/opt/echo-appliance/data/power-state-canary.bin",
            "sha256": "a" * 64,
            "size": physical.POWER_STATE_CANARY_BYTES,
        },
        "nas": {
            "path": "/srv/echo-nas/power-state-canary.bin",
            "sha256": "b" * 64,
            "size": physical.POWER_STATE_NAS_CANARY_BYTES,
        },
    }
    context = {
        "previousImage": previous,
        "targetImage": target,
        "baselineBootId": boot_a,
        "canaries": canaries,
    }
    details = {
        "baseline": {
            "previousImageVerified": True,
            "targetImage": target,
            "bootId": boot_a,
            "recoveryService": {"enabled": True, "active": False},
            "canaries": canaries,
        },
        "arm-power-cut": {
            "physicalPowerCutArmed": True,
            "bootId": boot_a,
            "marker": f"ECHO_POWER_STATE_UPDATE_CUT_ARMED plan={plan_id} boot={boot_a}",
            "transactionId": "c" * 64,
            "transactionPhase": "selected",
            "targetSelected": True,
            "nextAction": "physically-remove-and-restore-power",
        },
        "recover-power-cut": {
            "updatePowerLossRolledBack": True,
            "bootIdChanged": True,
            "previousBootId": boot_a,
            "currentBootId": boot_b,
            "uncleanShutdownVerified": True,
            "automaticRecoveryServiceResult": "success",
            "previousImageRestored": previous,
            "canaries": canaries,
            "journal": {
                "persistentJournalAvailable": True,
                "powerCutIntentFound": True,
                "cleanShutdownFound": False,
            },
        },
        "upgrade-success": {
            "immutableDigestUpgradeVerified": True,
            "previousImage": previous,
            "targetImage": target,
            "transactionCommitted": True,
            "canaries": canaries,
        },
        "upgrade-failure": {
            "failedUpgradeRollbackVerified": True,
            "failureInjectedAfterSelection": True,
            "candidateImageRestored": target,
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
                "path": "/media/off-device/verified.echo-backup",
                "sha256": "e" * 64,
                "size": 4096,
            },
            "previousStateRetained": True,
            "canaries": canaries,
        },
    }
    logs = {
        physical.POWER_STATE_PHASE_EVIDENCE_NAMES[phase]: (
            json.dumps(
                {
                    "schemaVersion": 1,
                    "kind": "echo.power-state-physical-lab-evidence",
                    "planId": plan_id,
                    "phase": phase,
                    "passed": True,
                    "details": details[phase],
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode()
        for phase in physical.POWER_STATE_PHASES
    }
    phases = {
        phase: {
            "name": name,
            "sha256": _digest(logs[name]),
            "size": len(logs[name]),
        }
        for phase, name in physical.POWER_STATE_PHASE_EVIDENCE_NAMES.items()
    }
    evidence = {
        check: next(
            phases[phase]
            for phase, name in physical.POWER_STATE_PHASE_EVIDENCE_NAMES.items()
            if name == physical.POWER_STATE_EVIDENCE_NAMES[check]
        )
        for check in physical.POWER_STATE_LIFECYCLE_CHECKS
    }
    lifecycle = (
        json.dumps(
            physical._power_state_lifecycle_payload(
                evidence,
                phases,
                context,
                candidate={
                    "indexId": candidate["indexId"],
                    "sourceRevision": candidate["source"]["commit"],
                    "agentRevision": candidate["source"]["agentCommit"],
                    "releaseTag": candidate["source"]["releaseTag"],
                    "operationsArtifactId": candidate["evidence"]["appliance"]["operationsBundle"][
                        "artifactId"
                    ],
                    "operationsArchiveSha256": candidate["evidence"]["appliance"][
                        "operationsBundle"
                    ]["sha256"],
                },
                lab_plan_id=plan_id,
            ),
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()
    return logs, lifecycle


def _storage_recovery_material(candidate: dict[str, Any]) -> tuple[dict[str, bytes], bytes]:
    logs = {
        name: (json.dumps({"machineGenerated": name}, sort_keys=True) + "\n").encode()
        for name in physical.STORAGE_RECOVERY_PHASE_EVIDENCE_NAMES
    }
    evidence = {
        check: {
            "name": name,
            "sha256": _digest(logs[name]),
            "size": len(logs[name]),
        }
        for check, name in physical.STORAGE_RECOVERY_EVIDENCE_NAMES.items()
    }
    lifecycle = (
        json.dumps(
            physical._storage_recovery_lifecycle_payload(
                evidence,
                candidate={
                    "indexId": candidate["indexId"],
                    "sourceRevision": candidate["source"]["commit"],
                    "agentRevision": candidate["source"]["agentCommit"],
                    "releaseTag": candidate["source"]["releaseTag"],
                    "operationsArtifactId": candidate["evidence"]["appliance"]["operationsBundle"][
                        "artifactId"
                    ],
                    "operationsArchiveSha256": candidate["evidence"]["appliance"][
                        "operationsBundle"
                    ]["sha256"],
                },
                lab_plan_id="a" * 64,
            ),
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()
    return logs, lifecycle


def _protocol_interoperability_material(
    candidate: dict[str, Any],
) -> tuple[dict[str, bytes], bytes]:
    logs = {
        name: (json.dumps({"machineGenerated": name}, sort_keys=True) + "\n").encode()
        for name in physical.PROTOCOL_INTEROPERABILITY_EVIDENCE_NAMES.values()
    }
    evidence = {
        check: {"name": name, "sha256": _digest(logs[name]), "size": len(logs[name])}
        for check, name in physical.PROTOCOL_INTEROPERABILITY_EVIDENCE_NAMES.items()
    }
    lifecycle = (
        json.dumps(
            physical._protocol_interoperability_lifecycle_payload(
                evidence,
                candidate={
                    "indexId": candidate["indexId"],
                    "sourceRevision": candidate["source"]["commit"],
                    "agentRevision": candidate["source"]["agentCommit"],
                    "releaseTag": candidate["source"]["releaseTag"],
                    "operationsArtifactId": candidate["evidence"]["appliance"]["operationsBundle"][
                        "artifactId"
                    ],
                    "operationsArchiveSha256": candidate["evidence"]["appliance"][
                        "operationsBundle"
                    ]["sha256"],
                },
                lab_plan_id="b" * 64,
            ),
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()
    return logs, lifecycle


def _device_endurance_material(
    candidate: dict[str, Any], gate: str
) -> tuple[dict[str, bytes], bytes, bytes, bytes, bytes]:
    architecture = (
        "arm64" if physical.GATE_REQUIREMENTS[gate]["architecture"] == "arm64" else "amd64"
    )
    hub_plan, hub_result = hub_lifecycle_material(
        candidate,
        architecture=architecture,
    )
    paperless_plan, paperless_result = paperless_functional_material(
        candidate,
        architecture=architecture,
    )
    lan_plan, lan_result, lan_probes = lan_discovery_functional_material(
        candidate,
        architecture=architecture,
    )
    logs = {
        name: (json.dumps({"machineGenerated": name}, sort_keys=True) + "\n").encode()
        for name in physical.DEVICE_ENDURANCE_PHASE_EVIDENCE_NAMES
    }
    logs[physical.HUB_LIFECYCLE_RESULT_NAME] = hub_result
    logs[physical.PAPERLESS_FUNCTIONAL_RESULT_NAME] = paperless_result
    logs[physical.LAN_DISCOVERY_FUNCTIONAL_RESULT_NAME] = lan_result
    logs.update(lan_probes)
    evidence = {
        check: {"name": name, "sha256": _digest(logs[name]), "size": len(logs[name])}
        for check, name in physical.DEVICE_ENDURANCE_EVIDENCE_NAMES.items()
    }
    lifecycle = (
        json.dumps(
            physical._device_endurance_lifecycle_payload(
                evidence,
                candidate={
                    "indexId": candidate["indexId"],
                    "sourceRevision": candidate["source"]["commit"],
                    "agentRevision": candidate["source"]["agentCommit"],
                    "releaseTag": candidate["source"]["releaseTag"],
                    "operationsArtifactId": candidate["evidence"]["appliance"]["operationsBundle"][
                        "artifactId"
                    ],
                    "operationsArchiveSha256": candidate["evidence"]["appliance"][
                        "operationsBundle"
                    ]["sha256"],
                },
                lab_plan_id="c" * 64,
                gate=gate,
            ),
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()
    return logs, lifecycle, hub_plan, paperless_plan, lan_plan


def _bare_metal_material(candidate: dict[str, Any]) -> tuple[dict[str, bytes], bytes]:
    plan_id = "f" * 64
    candidate_identity = {
        "indexId": candidate["indexId"],
        "sourceRevision": candidate["source"]["commit"],
        "agentRevision": candidate["source"]["agentCommit"],
        "releaseTag": candidate["source"]["releaseTag"],
        "operationsArtifactId": candidate["evidence"]["appliance"]["operationsBundle"][
            "artifactId"
        ],
        "operationsArchiveSha256": candidate["evidence"]["appliance"]["operationsBundle"]["sha256"],
    }
    app = {
        "bundleVerified": True,
        "immutableImageVerified": True,
        "administratorLoginReady": True,
        "agentWorkbenchReady": True,
        "auditVerified": True,
        "dockerApprovalVerified": True,
        "runtimeArchitecture": "amd64",
    }
    state = {
        "authenticationStateVerified": True,
        "auditStateVerified": True,
        "auditEntries": 7,
        "auditSigningKeyId": "test-signing-key",
        "sessionNotBefore": 1,
        "schemaVersion": 1,
    }
    canaries = {
        name: {"path": path, "sha256": digest * 64, "size": size}
        for name, path, digest, size in (
            (
                "state",
                "/opt/echo-appliance/data/bare-metal-state-canary.bin",
                "a",
                physical.BARE_METAL_STATE_CANARY_BYTES,
            ),
            (
                "agent",
                "/var/lib/echo-agent/bare-metal-agent-canary.bin",
                "b",
                physical.BARE_METAL_AGENT_CANARY_BYTES,
            ),
            (
                "nas",
                "/srv/echo-nas/bare-metal-nas-canary.bin",
                "c",
                physical.BARE_METAL_NAS_CANARY_BYTES,
            ),
        )
    }
    backups = {
        "applianceState": {
            "path": "/media/off-device/appliance-state.echo-backup",
            "sha256": "d" * 64,
            "size": 4096,
        },
        "user": {
            "repository": "/mnt/echo-backup/echo-os-user",
            "repositoryId": "e" * 64,
            "snapshotId": "1" * 64,
            "fullReadVerified": True,
        },
        "nas": {
            "repository": "/media/off-device/echo-nas-restic",
            "mountpoint": "/media/off-device",
            "snapshotId": "2" * 64,
            "receipt": {
                "path": "/media/off-device/nas-backup-receipt.json",
                "sha256": "3" * 64,
                "size": 512,
                "repositoryId": "4" * 64,
                "sourceSha256": "5" * 64,
            },
        },
    }
    source = {
        "bootId": "11111111-1111-4111-8111-111111111111",
        "machineIdSha256": "6" * 64,
        "sourceRevision": OS_COMMIT,
        "state": state,
        "appliance": app,
        "canaries": canaries,
    }
    transaction = "7" * 24
    entries = 3
    logical_bytes = physical.BARE_METAL_NAS_CANARY_BYTES + 17
    details = {
        "source-backup": {
            "sourceRevision": OS_COMMIT,
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
            "sourceRevision": OS_COMMIT,
            "recoveryVersion": "1.0.0",
            "installerManifestSha256": "8" * 64,
            "installerSourceSha256": "9" * 64,
            "installerPlanTranscriptSha256": "a" * 64,
            "targetIdentitySha256": "b" * 64,
            "transcriptSha256": "c" * 64,
            "postWriteReadbackVerified": True,
            "recoveryBootId": "22222222-2222-4222-8222-222222222222",
        },
        "cold-boot": {
            "firstColdBootHealthy": True,
            "recoveryBootId": "22222222-2222-4222-8222-222222222222",
            "installedBootId": "33333333-3333-4333-8333-333333333333",
            "replacementMachineIdSha256": "d" * 64,
            "sourceMachineIdentityChanged": True,
            "sourceRevision": OS_COMMIT,
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
            "promotionTranscriptSha256": "e" * 64,
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
            "commitTranscriptSha256": "f" * 64,
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
    logs = {
        physical.BARE_METAL_PHASE_EVIDENCE_NAMES[phase]: (
            json.dumps(
                {
                    "schemaVersion": 1,
                    "kind": "echo.bare-metal-recovery-physical-lab-evidence",
                    "planId": plan_id,
                    "phase": phase,
                    "passed": True,
                    "details": details[phase],
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode()
        for phase in physical.BARE_METAL_PHASES
    }
    phases = {
        phase: {"name": name, "sha256": _digest(logs[name]), "size": len(logs[name])}
        for phase, name in physical.BARE_METAL_PHASE_EVIDENCE_NAMES.items()
    }
    evidence = {
        check: next(
            phases[phase]
            for phase, name in physical.BARE_METAL_PHASE_EVIDENCE_NAMES.items()
            if name == physical.BARE_METAL_EVIDENCE_NAMES[check]
        )
        for check in physical.BARE_METAL_LIFECYCLE_CHECKS
    }
    lifecycle = (
        json.dumps(
            physical._bare_metal_lifecycle_payload(
                evidence,
                phases,
                {
                    "sourceSystem": source,
                    "backups": backups,
                    "verifierArchitecture": "amd64",
                },
                candidate=candidate_identity,
                lab_plan_id=plan_id,
            ),
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()
    return logs, lifecycle


def _gate_manifest(gate: str, candidate: dict[str, Any], index: int) -> dict[str, Any]:
    candidate_identity = {
        "indexId": candidate["indexId"],
        "sourceRevision": OS_COMMIT,
        "agentRevision": AGENT_COMMIT,
        "releaseTag": RELEASE_TAG,
    }
    architecture = physical.GATE_REQUIREMENTS[gate]["architecture"] or "x86_64"
    marker = physical._expected_marker(gate, candidate_identity)
    log = f"physical lab gate start\n{marker}\nphysical lab gate finish\n".encode()
    profile = _hardware_profile(gate, index)
    gate_result = _gate_result(gate)
    artifacts = [
        {"name": "acceptance.log", "sha256": _digest(log), "size": len(log)},
        {
            "name": physical.GATE_RESULT_NAME,
            "sha256": _digest(gate_result),
            "size": len(gate_result),
        },
        {
            "name": physical.HARDWARE_PROFILE_NAME,
            "sha256": _digest(profile),
            "size": len(profile),
        },
    ]
    if gate == physical.OPERATIONS_SYSTEMD_GATE:
        lifecycle = _operations_systemd_lifecycle(log, candidate)
        artifacts.append(
            {
                "name": physical.OPERATIONS_SYSTEMD_LIFECYCLE_NAME,
                "sha256": _digest(lifecycle),
                "size": len(lifecycle),
            }
        )
        logs, power_lifecycle = _power_state_material(candidate)
        artifacts.extend(
            {"name": name, "sha256": _digest(raw), "size": len(raw)} for name, raw in logs.items()
        )
        artifacts.append(
            {
                "name": physical.POWER_STATE_LIFECYCLE_NAME,
                "sha256": _digest(power_lifecycle),
                "size": len(power_lifecycle),
            }
        )
    if gate == physical.STORAGE_RECOVERY_GATE:
        logs, lifecycle = _storage_recovery_material(candidate)
        artifacts.extend(
            {"name": name, "sha256": _digest(raw), "size": len(raw)} for name, raw in logs.items()
        )
        artifacts.append(
            {
                "name": physical.STORAGE_RECOVERY_LIFECYCLE_NAME,
                "sha256": _digest(lifecycle),
                "size": len(lifecycle),
            }
        )
    if gate == physical.PROTOCOL_INTEROPERABILITY_GATE:
        logs, lifecycle = _protocol_interoperability_material(candidate)
        artifacts.extend(
            {"name": name, "sha256": _digest(raw), "size": len(raw)} for name, raw in logs.items()
        )
        artifacts.append(
            {
                "name": physical.PROTOCOL_INTEROPERABILITY_LIFECYCLE_NAME,
                "sha256": _digest(lifecycle),
                "size": len(lifecycle),
            }
        )
    if gate in physical.DEVICE_ENDURANCE_GATES:
        logs, lifecycle, hub_plan, paperless_plan, lan_plan = _device_endurance_material(
            candidate, gate
        )
        artifacts.extend(
            {"name": name, "sha256": _digest(raw), "size": len(raw)} for name, raw in logs.items()
        )
        artifacts.append(
            {
                "name": physical.DEVICE_ENDURANCE_LIFECYCLE_NAME,
                "sha256": _digest(lifecycle),
                "size": len(lifecycle),
            }
        )
        artifacts.append(
            {
                "name": physical.HUB_LIFECYCLE_PLAN_NAME,
                "sha256": _digest(hub_plan),
                "size": len(hub_plan),
            }
        )
        artifacts.append(
            {
                "name": physical.PAPERLESS_FUNCTIONAL_PLAN_NAME,
                "sha256": _digest(paperless_plan),
                "size": len(paperless_plan),
            }
        )
        artifacts.append(
            {
                "name": physical.LAN_DISCOVERY_FUNCTIONAL_PLAN_NAME,
                "sha256": _digest(lan_plan),
                "size": len(lan_plan),
            }
        )
    if gate == physical.BARE_METAL_GATE:
        logs, lifecycle = _bare_metal_material(candidate)
        artifacts.extend(
            {"name": name, "sha256": _digest(raw), "size": len(raw)} for name, raw in logs.items()
        )
        artifacts.append(
            {
                "name": physical.BARE_METAL_LIFECYCLE_NAME,
                "sha256": _digest(lifecycle),
                "size": len(lifecycle),
            }
        )
    return {
        "schemaVersion": 1,
        "kind": "echo.physical-acceptance-gate",
        "gate": gate,
        "candidate": candidate_identity,
        "execution": {
            "labRunId": str(uuid.UUID(int=index, version=4)),
            "startedAt": "2026-08-26T01:00:00Z",
            "finishedAt": "2026-08-27T01:30:00Z",
        },
        "hardware": {
            "architecture": architecture,
            "profileSha256": _digest(profile),
            "deviceCount": physical.GATE_REQUIREMENTS[gate]["minimumDevices"],
            "serialsRedacted": True,
        },
        "result": {
            "passed": True,
            "marker": marker,
            "deliveryRequirements": list(physical.GATE_REQUIREMENTS[gate]["deliveryRequirements"]),
        },
        "primaryLog": "acceptance.log",
        "artifacts": sorted(artifacts, key=lambda item: item["name"]),
    }


def _evidence(tmp_path: Path) -> tuple[Path, Path, Path, dict[str, Any]]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    candidate = _candidate()
    candidate_path = tmp_path / "candidate.json"
    _write_json(candidate_path, candidate)
    keyring = tmp_path / "acceptance-keyring.gpg"
    keyring.write_bytes(b"public-acceptance-keyring")
    root = tmp_path / "physical"
    root.mkdir()
    for index, gate in enumerate(physical.PHYSICAL_GATES, start=1):
        directory = root / gate
        directory.mkdir()
        manifest = _gate_manifest(gate, candidate, index)
        marker = manifest["result"]["marker"]
        (directory / "acceptance.log").write_text(
            f"physical lab gate start\n{marker}\nphysical lab gate finish\n",
            encoding="utf-8",
        )
        (directory / physical.HARDWARE_PROFILE_NAME).write_bytes(_hardware_profile(gate, index))
        (directory / physical.GATE_RESULT_NAME).write_bytes(_gate_result(gate))
        if gate == physical.OPERATIONS_SYSTEMD_GATE:
            (directory / physical.OPERATIONS_SYSTEMD_LIFECYCLE_NAME).write_bytes(
                _operations_systemd_lifecycle(
                    (directory / "acceptance.log").read_bytes(), candidate
                )
            )
            logs, lifecycle = _power_state_material(candidate)
            for name, raw in logs.items():
                (directory / name).write_bytes(raw)
            (directory / physical.POWER_STATE_LIFECYCLE_NAME).write_bytes(lifecycle)
        if gate == physical.STORAGE_RECOVERY_GATE:
            logs, lifecycle = _storage_recovery_material(candidate)
            for name, raw in logs.items():
                (directory / name).write_bytes(raw)
            (directory / physical.STORAGE_RECOVERY_LIFECYCLE_NAME).write_bytes(lifecycle)
        if gate == physical.PROTOCOL_INTEROPERABILITY_GATE:
            logs, lifecycle = _protocol_interoperability_material(candidate)
            for name, raw in logs.items():
                (directory / name).write_bytes(raw)
            (directory / physical.PROTOCOL_INTEROPERABILITY_LIFECYCLE_NAME).write_bytes(lifecycle)
        if gate in physical.DEVICE_ENDURANCE_GATES:
            logs, lifecycle, hub_plan, paperless_plan, lan_plan = _device_endurance_material(
                candidate, gate
            )
            for name, raw in logs.items():
                (directory / name).write_bytes(raw)
            (directory / physical.HUB_LIFECYCLE_PLAN_NAME).write_bytes(hub_plan)
            (directory / physical.PAPERLESS_FUNCTIONAL_PLAN_NAME).write_bytes(paperless_plan)
            (directory / physical.LAN_DISCOVERY_FUNCTIONAL_PLAN_NAME).write_bytes(lan_plan)
            (directory / physical.DEVICE_ENDURANCE_LIFECYCLE_NAME).write_bytes(lifecycle)
        if gate == physical.BARE_METAL_GATE:
            logs, lifecycle = _bare_metal_material(candidate)
            for name, raw in logs.items():
                (directory / name).write_bytes(raw)
            (directory / physical.BARE_METAL_LIFECYCLE_NAME).write_bytes(lifecycle)
        _write_json(directory / "evidence.json", manifest)
        (directory / "evidence.json.gpg").write_bytes(f"signature-{gate}".encode())
    return candidate_path, root, keyring, candidate


def _signature(manifest: Path, signature: Path, keyring: Path) -> dict[str, str]:
    return {
        "manifestSha256": _digest(manifest.read_bytes()),
        "signatureSha256": _digest(signature.read_bytes()),
        "keyringSha256": KEYRING_SHA if keyring.exists() else "f" * 64,
        "signerFingerprint": SIGNER_FINGERPRINT,
    }


def _rewrite_gate(root: Path, gate: str, manifest: dict[str, Any]) -> None:
    _write_json(root / gate / "evidence.json", manifest)


def _rewrite_hardware_profile(root: Path, gate: str, value: Any) -> None:
    directory = root / gate
    raw = value if isinstance(value, bytes) else (json.dumps(value, sort_keys=True) + "\n").encode()
    (directory / physical.HARDWARE_PROFILE_NAME).write_bytes(raw)
    manifest = json.loads((directory / "evidence.json").read_text())
    manifest["hardware"]["profileSha256"] = _digest(raw)
    profile_record = next(
        record
        for record in manifest["artifacts"]
        if record["name"] == physical.HARDWARE_PROFILE_NAME
    )
    profile_record.update(sha256=_digest(raw), size=len(raw))
    _rewrite_gate(root, gate, manifest)


def _rewrite_gate_result(root: Path, gate: str, value: Any) -> None:
    directory = root / gate
    raw = value if isinstance(value, bytes) else (json.dumps(value, sort_keys=True) + "\n").encode()
    (directory / physical.GATE_RESULT_NAME).write_bytes(raw)
    manifest = json.loads((directory / "evidence.json").read_text())
    result_record = next(
        record for record in manifest["artifacts"] if record["name"] == physical.GATE_RESULT_NAME
    )
    result_record.update(sha256=_digest(raw), size=len(raw))
    _rewrite_gate(root, gate, manifest)


def _rewrite_operations_lifecycle(root: Path, value: Any) -> None:
    gate = physical.OPERATIONS_SYSTEMD_GATE
    directory = root / gate
    raw = value if isinstance(value, bytes) else (json.dumps(value, sort_keys=True) + "\n").encode()
    (directory / physical.OPERATIONS_SYSTEMD_LIFECYCLE_NAME).write_bytes(raw)
    manifest = json.loads((directory / "evidence.json").read_text())
    record = next(
        item
        for item in manifest["artifacts"]
        if item["name"] == physical.OPERATIONS_SYSTEMD_LIFECYCLE_NAME
    )
    record.update(sha256=_digest(raw), size=len(raw))
    _rewrite_gate(root, gate, manifest)


def _rewrite_protocol_lifecycle(root: Path, value: Any) -> None:
    gate = physical.PROTOCOL_INTEROPERABILITY_GATE
    directory = root / gate
    raw = value if isinstance(value, bytes) else (json.dumps(value, sort_keys=True) + "\n").encode()
    (directory / physical.PROTOCOL_INTEROPERABILITY_LIFECYCLE_NAME).write_bytes(raw)
    manifest = json.loads((directory / "evidence.json").read_text())
    record = next(
        item
        for item in manifest["artifacts"]
        if item["name"] == physical.PROTOCOL_INTEROPERABILITY_LIFECYCLE_NAME
    )
    record.update(sha256=_digest(raw), size=len(raw))
    _rewrite_gate(root, gate, manifest)


def _rewrite_hub_lifecycle(
    root: Path,
    gate: str,
    *,
    plan: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
) -> None:
    directory = root / gate
    manifest = json.loads((directory / "evidence.json").read_text())
    for name, value in (
        (physical.HUB_LIFECYCLE_PLAN_NAME, plan),
        (physical.HUB_LIFECYCLE_RESULT_NAME, result),
    ):
        if value is None:
            continue
        raw = physical.hub_lab._canonical(value)
        (directory / name).write_bytes(raw)
        record = next(item for item in manifest["artifacts"] if item["name"] == name)
        record.update(sha256=_digest(raw), size=len(raw))
    _rewrite_gate(root, gate, manifest)


def _rewrite_paperless_functional(
    root: Path,
    gate: str,
    *,
    plan: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
) -> None:
    directory = root / gate
    manifest = json.loads((directory / "evidence.json").read_text())
    for name, value in (
        (physical.PAPERLESS_FUNCTIONAL_PLAN_NAME, plan),
        (physical.PAPERLESS_FUNCTIONAL_RESULT_NAME, result),
    ):
        if value is None:
            continue
        raw = physical.paperless_lab._canonical(value)
        (directory / name).write_bytes(raw)
        record = next(item for item in manifest["artifacts"] if item["name"] == name)
        record.update(sha256=_digest(raw), size=len(raw))
    _rewrite_gate(root, gate, manifest)


def _rewrite_lan_discovery_functional(
    root: Path,
    gate: str,
    *,
    plan: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
) -> None:
    directory = root / gate
    manifest = json.loads((directory / "evidence.json").read_text())
    for name, value in (
        (physical.LAN_DISCOVERY_FUNCTIONAL_PLAN_NAME, plan),
        (physical.LAN_DISCOVERY_FUNCTIONAL_RESULT_NAME, result),
    ):
        if value is None:
            continue
        raw = physical.lan_discovery_lab._canonical(value)
        (directory / name).write_bytes(raw)
        record = next(item for item in manifest["artifacts"] if item["name"] == name)
        record.update(sha256=_digest(raw), size=len(raw))
    _rewrite_gate(root, gate, manifest)


def test_six_signed_physical_gates_promote_one_candidate_to_product_delivery(
    tmp_path: Path,
) -> None:
    candidate_path, root, keyring, candidate = _evidence(tmp_path)

    report = physical.verify_acceptance(
        candidate_index=candidate_path,
        evidence_root=root,
        keyring=keyring,
        signature_verifier=_signature,
    )

    assert report["candidate"]["indexId"] == candidate["indexId"]
    assert report["schemaVersion"] == 2
    assert report["ciReleaseCandidateReady"] is True
    assert report["physicalAcceptanceComplete"] is True
    assert report["nasProductDeliveryReady"] is True
    assert report["deliveryRequirementsVerified"] == list(physical.DELIVERY_REQUIREMENTS)
    assert set(report["gates"]) == set(physical.PHYSICAL_GATES)
    assert report["acceptanceKeyringSha256"] == KEYRING_SHA
    assert report["acceptanceSignerFingerprint"] == SIGNER_FINGERPRINT
    for gate, gate_report in report["gates"].items():
        assert gate_report["verifiedChecks"] == list(
            physical.GATE_REQUIREMENTS[gate]["resultChecks"]
        )
        assert gate_report["deliveryRequirements"] == list(
            physical.GATE_REQUIREMENTS[gate]["deliveryRequirements"]
        )
        if gate == physical.OPERATIONS_SYSTEMD_GATE:
            lifecycle = root / gate / physical.OPERATIONS_SYSTEMD_LIFECYCLE_NAME
            assert gate_report["operationsSystemdLifecycleSha256"] == _digest(
                lifecycle.read_bytes()
            )
            power_lifecycle = root / gate / physical.POWER_STATE_LIFECYCLE_NAME
            assert gate_report["powerStateLifecycleSha256"] == _digest(power_lifecycle.read_bytes())
        else:
            assert "operationsSystemdLifecycleSha256" not in gate_report
            assert "powerStateLifecycleSha256" not in gate_report
        if gate == physical.PROTOCOL_INTEROPERABILITY_GATE:
            lifecycle = root / gate / physical.PROTOCOL_INTEROPERABILITY_LIFECYCLE_NAME
            assert gate_report["protocolInteroperabilityLifecycleSha256"] == _digest(
                lifecycle.read_bytes()
            )
        else:
            assert "protocolInteroperabilityLifecycleSha256" not in gate_report
        if gate in physical.DEVICE_ENDURANCE_GATES:
            assert gate_report["hubLifecyclePlanSha256"] == _digest(
                (root / gate / physical.HUB_LIFECYCLE_PLAN_NAME).read_bytes()
            )
            assert gate_report["hubLifecycleResultSha256"] == _digest(
                (root / gate / physical.HUB_LIFECYCLE_RESULT_NAME).read_bytes()
            )
            assert gate_report["paperlessFunctionalPlanSha256"] == _digest(
                (root / gate / physical.PAPERLESS_FUNCTIONAL_PLAN_NAME).read_bytes()
            )
            assert gate_report["paperlessFunctionalResultSha256"] == _digest(
                (root / gate / physical.PAPERLESS_FUNCTIONAL_RESULT_NAME).read_bytes()
            )
        else:
            assert "hubLifecyclePlanSha256" not in gate_report
            assert "hubLifecycleResultSha256" not in gate_report
            assert "paperlessFunctionalPlanSha256" not in gate_report
            assert "paperlessFunctionalResultSha256" not in gate_report
    unsigned = dict(report)
    report_id = unsigned.pop("reportId")
    assert report_id == _digest(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
    )


def test_physical_signature_verifier_extracts_one_full_validsig_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = tmp_path / "evidence.json"
    signature = tmp_path / "evidence.json.gpg"
    keyring = tmp_path / "acceptance-keyring.gpg"
    manifest.write_text('{"evidence":true}\n', encoding="utf-8")
    signature.write_bytes(b"detached-signature")
    keyring.write_bytes(b"\xc6\x01\x00")

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        del kwargs
        assert Path(argv[-1]) != manifest
        assert Path(argv[-1]).read_bytes() == manifest.read_bytes()
        assert Path(argv[-2]).read_bytes() == signature.read_bytes()
        assert Path(argv[argv.index("--keyring") + 1]).read_bytes() == keyring.read_bytes()
        return subprocess.CompletedProcess(
            args=["/usr/bin/gpgv"],
            returncode=0,
            stdout=f"[GNUPG:] VALIDSIG {SIGNER_FINGERPRINT} 2026-08-27 0 4 0 1 8 00\n",
            stderr="gpgv: Good signature\n",
        )

    monkeypatch.setattr(physical.subprocess, "run", run)

    result = physical._verify_physical_signature(manifest, signature, keyring)

    assert result == {
        "manifestSha256": _digest(manifest.read_bytes()),
        "signatureSha256": _digest(signature.read_bytes()),
        "keyringSha256": _digest(keyring.read_bytes()),
        "signerFingerprint": SIGNER_FINGERPRINT,
    }


def test_physical_signature_verifier_rejects_multiple_valid_signers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = tmp_path / "evidence.json"
    signature = tmp_path / "evidence.json.gpg"
    keyring = tmp_path / "acceptance-keyring.gpg"
    manifest.write_text('{"evidence":true}\n', encoding="utf-8")
    signature.write_bytes(b"detached-signature")
    keyring.write_bytes(b"\xc6\x01\x00")

    def run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        del args, kwargs
        return subprocess.CompletedProcess(
            args=["/usr/bin/gpgv"],
            returncode=0,
            stdout=(
                f"[GNUPG:] VALIDSIG {SIGNER_FINGERPRINT} 2026-08-27 0 4 0 1 8 00\n"
                f"[GNUPG:] VALIDSIG {'B' * 40} 2026-08-27 0 4 0 1 8 00\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(physical.subprocess, "run", run)

    with pytest.raises(physical.PhysicalAcceptanceError, match="unique valid signer"):
        physical._verify_physical_signature(manifest, signature, keyring)


def test_rejects_a_missing_or_extra_physical_gate(tmp_path: Path) -> None:
    candidate_path, root, keyring, _candidate_value = _evidence(tmp_path)
    missing = root / physical.PHYSICAL_GATES[-1]
    moved = tmp_path / "held-gate"
    missing.rename(moved)

    with pytest.raises(physical.PhysicalAcceptanceError, match="exactly six"):
        physical.verify_acceptance(
            candidate_index=candidate_path,
            evidence_root=root,
            keyring=keyring,
            signature_verifier=_signature,
        )


def test_rejects_a_gate_from_another_candidate(tmp_path: Path) -> None:
    candidate_path, root, keyring, _candidate_value = _evidence(tmp_path)
    gate = physical.PHYSICAL_GATES[0]
    manifest = json.loads((root / gate / "evidence.json").read_text())
    manifest["candidate"]["indexId"] = "f" * 64
    _rewrite_gate(root, gate, manifest)

    with pytest.raises(physical.PhysicalAcceptanceError, match="another release candidate"):
        physical.verify_acceptance(
            candidate_index=candidate_path,
            evidence_root=root,
            keyring=keyring,
            signature_verifier=_signature,
        )


@pytest.mark.parametrize(
    ("gate", "field", "value", "message"),
    [
        (
            "physical_x86_64_install_and_cold_boot",
            "architecture",
            "arm64",
            "hardware evidence",
        ),
        (
            "supported_arm64_hardware_install_and_cold_boot",
            "architecture",
            "x86_64",
            "hardware evidence",
        ),
        (
            "real_disk_smart_and_raid_degradation_recovery",
            "deviceCount",
            1,
            "hardware evidence",
        ),
        (
            "physical_x86_64_install_and_cold_boot",
            "serialsRedacted",
            False,
            "hardware evidence",
        ),
    ],
)
def test_rejects_wrong_architecture_storage_or_redaction_contract(
    tmp_path: Path, gate: str, field: str, value: Any, message: str
) -> None:
    candidate_path, root, keyring, _candidate_value = _evidence(tmp_path)
    manifest = json.loads((root / gate / "evidence.json").read_text())
    manifest["hardware"][field] = value
    _rewrite_gate(root, gate, manifest)

    with pytest.raises(physical.PhysicalAcceptanceError, match=message):
        physical.verify_acceptance(
            candidate_index=candidate_path,
            evidence_root=root,
            keyring=keyring,
            signature_verifier=_signature,
        )


def test_rejects_missing_or_duplicate_success_marker(tmp_path: Path) -> None:
    candidate_path, root, keyring, _candidate_value = _evidence(tmp_path)
    gate = physical.PHYSICAL_GATES[0]
    directory = root / gate
    manifest = json.loads((directory / "evidence.json").read_text())
    marker = manifest["result"]["marker"]
    raw = f"{marker}\n{marker}\n".encode()
    (directory / "acceptance.log").write_bytes(raw)
    manifest["artifacts"][0].update(sha256=_digest(raw), size=len(raw))
    _rewrite_gate(root, gate, manifest)

    with pytest.raises(physical.PhysicalAcceptanceError, match="one unique success marker"):
        physical.verify_acceptance(
            candidate_index=candidate_path,
            evidence_root=root,
            keyring=keyring,
            signature_verifier=_signature,
        )


def test_rejects_hardware_profile_digest_not_bound_to_the_declared_artifact(
    tmp_path: Path,
) -> None:
    candidate_path, root, keyring, _candidate_value = _evidence(tmp_path)
    gate = physical.PHYSICAL_GATES[0]
    manifest = json.loads((root / gate / "evidence.json").read_text())
    manifest["hardware"]["profileSha256"] = "f" * 64
    _rewrite_gate(root, gate, manifest)

    with pytest.raises(physical.PhysicalAcceptanceError, match="hardware profile digest"):
        physical.verify_acceptance(
            candidate_index=candidate_path,
            evidence_root=root,
            keyring=keyring,
            signature_verifier=_signature,
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (b"not-json\n", "not strict JSON"),
        (
            b'{"schemaVersion":1,"schemaVersion":1,"kind":"echo.physical-hardware-profile"}\n',
            "duplicate JSON key",
        ),
        ({"architecture": "arm64"}, "does not match"),
        ({"deviceCount": 2}, "does not match"),
        ({"gate": "supported_arm64_hardware_install_and_cold_boot"}, "does not match"),
        ({"profileClass": "invented-unique-device"}, "does not match"),
        ({"serialsRedacted": False}, "does not match"),
        ({"unexpected": "field"}, "does not match"),
    ],
)
def test_rejects_unstructured_or_drifting_hardware_profile(
    tmp_path: Path, mutation: Any, message: str
) -> None:
    candidate_path, root, keyring, _candidate_value = _evidence(tmp_path)
    gate = physical.PHYSICAL_GATES[0]
    if isinstance(mutation, bytes):
        value: Any = mutation
    else:
        value = json.loads((root / gate / physical.HARDWARE_PROFILE_NAME).read_text())
        value.update(mutation)
    _rewrite_hardware_profile(root, gate, value)

    with pytest.raises(physical.PhysicalAcceptanceError, match=message):
        physical.verify_acceptance(
            candidate_index=candidate_path,
            evidence_root=root,
            keyring=keyring,
            signature_verifier=_signature,
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (b"not-json\n", "not strict JSON"),
        (
            b'{"schemaVersion":1,"schemaVersion":1,"kind":"echo.physical-gate-result"}\n',
            "duplicate JSON key",
        ),
        ({"allPassed": False}, "does not attest every required check"),
        ({"unexpected": True}, "does not attest every required check"),
    ],
)
def test_rejects_missing_false_or_unstructured_gate_result(
    tmp_path: Path, mutation: Any, message: str
) -> None:
    candidate_path, root, keyring, _candidate_value = _evidence(tmp_path)
    gate = physical.PHYSICAL_GATES[4]
    if isinstance(mutation, bytes):
        value: Any = mutation
    else:
        value = json.loads((root / gate / physical.GATE_RESULT_NAME).read_text())
        value.update(mutation)
    _rewrite_gate_result(root, gate, value)

    with pytest.raises(physical.PhysicalAcceptanceError, match=message):
        physical.verify_acceptance(
            candidate_index=candidate_path,
            evidence_root=root,
            keyring=keyring,
            signature_verifier=_signature,
        )


def test_power_and_bare_metal_results_require_full_operations_and_disaster_recovery() -> None:
    power = physical._gate_result_payload("power_loss_during_update_and_state_restore")
    recovery = physical._gate_result_payload("recovery_media_bare_metal_restore")

    assert {
        "operationsSystemdInstalled",
        "operationsSystemdInstallRollbackVerified",
        "backupTimerTriggered",
        "auditTimerTriggered",
        "externalBackupVerified",
        "missingBackupMountFailedClosed",
        "missingAuditMountFailedClosed",
        "auditEvidenceExportVerified",
        "operationsSystemdRemovalLeftNoUnitsOrTimers",
        "operationsSystemdRemovalPreservedCredentialsAndData",
        "operationsSystemdRemovalRollbackVerified",
    } <= set(power["checks"])
    assert all(power["checks"].values())
    assert recovery["checks"]["offDeviceBackupRestored"] is True


def test_gate_results_cover_every_declared_nas_delivery_requirement() -> None:
    covered = {
        requirement
        for gate in physical.GATE_REQUIREMENTS.values()
        for requirement in gate["deliveryRequirements"]
    }
    assert covered == set(physical.DELIVERY_REQUIREMENTS)

    x86 = set(physical.GATE_REQUIREMENTS[physical.PHYSICAL_GATES[0]]["resultChecks"])
    storage = set(physical.GATE_REQUIREMENTS[physical.PHYSICAL_GATES[2]]["resultChecks"])
    clients = set(physical.GATE_REQUIREMENTS[physical.PHYSICAL_GATES[3]]["resultChecks"])
    lifecycle = set(physical.GATE_REQUIREMENTS[physical.PHYSICAL_GATES[4]]["resultChecks"])
    assert {
        "fileUploadDownloadCopyTrashVerified",
        "familyMemberIsolationVerified",
        "agentWorkbenchVerified",
        "tlsBrowserTrustVerified",
        "sessionRevocationVerified",
        "auditChainVerified",
        "continuousRunStable",
        "hardPowerCycleRecovered",
    } <= x86
    assert {
        "diskDisconnectObserved",
        "filesystemReadOnlyHandled",
        "volumeFullHandled",
        "rebootRecoveryVerified",
        "recycleBinRestoreVerified",
    } <= storage
    assert {
        "windowsSmbReadWrite",
        "macosSmbReadWrite",
        "linuxSmbReadWrite",
        "macosNfsReadWrite",
        "linuxNfsReadWrite",
        "userAndAclPermissionsVerified",
        "quotaEnforcedAcrossProtocols",
    } <= clients
    assert {
        "operationsSystemdInstalled",
        "operationsSystemdInstallRollbackVerified",
        "backupTimerTriggered",
        "auditTimerTriggered",
        "missingBackupMountFailedClosed",
        "missingAuditMountFailedClosed",
        "operationsSystemdRemovalLeftNoUnitsOrTimers",
        "operationsSystemdRemovalPreservedCredentialsAndData",
        "operationsSystemdRemovalRollbackVerified",
    } <= lifecycle


def test_final_acceptance_refuses_a_verifier_mapping_that_omits_g2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate_path, root, keyring, _candidate_value = _evidence(tmp_path)
    gate = physical.PHYSICAL_GATES[2]
    storage_gate = physical.GATE_REQUIREMENTS[gate]
    monkeypatch.setitem(storage_gate, "deliveryRequirements", ())
    manifest = json.loads((root / gate / "evidence.json").read_text())
    manifest["result"]["deliveryRequirements"] = []
    _rewrite_gate(root, gate, manifest)

    with pytest.raises(physical.PhysicalAcceptanceError, match="every NAS delivery requirement"):
        physical.verify_acceptance(
            candidate_index=candidate_path,
            evidence_root=root,
            keyring=keyring,
            signature_verifier=_signature,
        )


def test_signed_manifest_cannot_claim_a_different_delivery_requirement_mapping(
    tmp_path: Path,
) -> None:
    candidate_path, root, keyring, _candidate_value = _evidence(tmp_path)
    gate = physical.PHYSICAL_GATES[0]
    manifest = json.loads((root / gate / "evidence.json").read_text())
    manifest["result"]["deliveryRequirements"] = ["G1"]
    _rewrite_gate(root, gate, manifest)

    with pytest.raises(physical.PhysicalAcceptanceError, match="result contract"):
        physical.verify_acceptance(
            candidate_index=candidate_path,
            evidence_root=root,
            keyring=keyring,
            signature_verifier=_signature,
        )


def test_gate_result_rejects_integer_values_that_compare_equal_to_booleans() -> None:
    gate = "power_loss_during_update_and_state_restore"
    result = physical._gate_result_payload(gate)
    result["allPassed"] = 1
    raw = (json.dumps(result) + "\n").encode()
    with pytest.raises(physical.PhysicalAcceptanceError, match="every required check"):
        physical._validate_gate_result(raw, gate=gate)

    result = physical._gate_result_payload(gate)
    result["checks"]["externalBackupVerified"] = 1
    raw = (json.dumps(result) + "\n").encode()
    with pytest.raises(physical.PhysicalAcceptanceError, match="every required check"):
        physical._validate_gate_result(raw, gate=gate)


def test_lifecycle_gate_requires_machine_bound_operations_systemd_evidence(
    tmp_path: Path,
) -> None:
    candidate_path, root, keyring, _candidate_value = _evidence(tmp_path)
    gate = physical.OPERATIONS_SYSTEMD_GATE
    directory = root / gate
    (directory / physical.OPERATIONS_SYSTEMD_LIFECYCLE_NAME).unlink()
    manifest = json.loads((directory / "evidence.json").read_text())
    manifest["artifacts"] = [
        record
        for record in manifest["artifacts"]
        if record["name"] != physical.OPERATIONS_SYSTEMD_LIFECYCLE_NAME
    ]
    _rewrite_gate(root, gate, manifest)

    with pytest.raises(physical.PhysicalAcceptanceError, match="omit required operations"):
        physical.verify_acceptance(
            candidate_index=candidate_path,
            evidence_root=root,
            keyring=keyring,
            signature_verifier=_signature,
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (b"not-json\n", "not strict JSON"),
        (
            b'{"schemaVersion":1,"schemaVersion":1,"kind":"echo.operations-systemd-physical-lifecycle"}\n',
            "duplicate JSON key",
        ),
        ({"allPassed": False}, "invalid lifecycle contract"),
        ({"unexpected": True}, "invalid lifecycle contract"),
    ],
)
def test_rejects_unstructured_or_false_operations_systemd_lifecycle(
    tmp_path: Path, mutation: Any, message: str
) -> None:
    candidate_path, root, keyring, _candidate_value = _evidence(tmp_path)
    gate = physical.OPERATIONS_SYSTEMD_GATE
    if isinstance(mutation, bytes):
        value: Any = mutation
    else:
        value = json.loads((root / gate / physical.OPERATIONS_SYSTEMD_LIFECYCLE_NAME).read_text())
        value.update(mutation)
    _rewrite_operations_lifecycle(root, value)

    with pytest.raises(physical.PhysicalAcceptanceError, match=message):
        physical.verify_acceptance(
            candidate_index=candidate_path,
            evidence_root=root,
            keyring=keyring,
            signature_verifier=_signature,
        )


def test_rejects_operations_lifecycle_check_bound_to_other_bytes(tmp_path: Path) -> None:
    candidate_path, root, keyring, _candidate_value = _evidence(tmp_path)
    gate = physical.OPERATIONS_SYSTEMD_GATE
    value = json.loads((root / gate / physical.OPERATIONS_SYSTEMD_LIFECYCLE_NAME).read_text())
    value["checks"]["backupTimerTriggered"]["evidence"]["sha256"] = "f" * 64
    _rewrite_operations_lifecycle(root, value)

    with pytest.raises(physical.PhysicalAcceptanceError, match="evidence.*unbound"):
        physical.verify_acceptance(
            candidate_index=candidate_path,
            evidence_root=root,
            keyring=keyring,
            signature_verifier=_signature,
        )


def test_protocol_gate_requires_machine_generated_lifecycle_and_all_eight_fixed_logs(
    tmp_path: Path,
) -> None:
    candidate_path, root, keyring, _candidate_value = _evidence(tmp_path)
    gate = physical.PROTOCOL_INTEROPERABILITY_GATE
    directory = root / gate
    lifecycle = directory / physical.PROTOCOL_INTEROPERABILITY_LIFECYCLE_NAME
    lifecycle.unlink()
    manifest = json.loads((directory / "evidence.json").read_text())
    manifest["artifacts"] = [
        record
        for record in manifest["artifacts"]
        if record["name"] != physical.PROTOCOL_INTEROPERABILITY_LIFECYCLE_NAME
    ]
    _rewrite_gate(root, gate, manifest)

    with pytest.raises(physical.PhysicalAcceptanceError, match="omit required protocol"):
        physical.verify_acceptance(
            candidate_index=candidate_path,
            evidence_root=root,
            keyring=keyring,
            signature_verifier=_signature,
        )


def test_device_gates_require_machine_bound_endurance_lifecycle(tmp_path: Path) -> None:
    candidate_path, root, keyring, _candidate_value = _evidence(tmp_path)
    gate = "physical_x86_64_install_and_cold_boot"
    directory = root / gate
    (directory / physical.DEVICE_ENDURANCE_LIFECYCLE_NAME).unlink()
    manifest = json.loads((directory / "evidence.json").read_text())
    manifest["artifacts"] = [
        record
        for record in manifest["artifacts"]
        if record["name"] != physical.DEVICE_ENDURANCE_LIFECYCLE_NAME
    ]
    _rewrite_gate(root, gate, manifest)

    with pytest.raises(physical.PhysicalAcceptanceError, match="omit required device"):
        physical.verify_acceptance(
            candidate_index=candidate_path,
            evidence_root=root,
            keyring=keyring,
            signature_verifier=_signature,
        )


def test_device_gates_require_the_hub_plan_and_result_pair(tmp_path: Path) -> None:
    candidate_path, root, keyring, _candidate_value = _evidence(tmp_path)
    gate = "physical_x86_64_install_and_cold_boot"
    directory = root / gate
    (directory / physical.HUB_LIFECYCLE_PLAN_NAME).unlink()
    manifest = json.loads((directory / "evidence.json").read_text())
    manifest["artifacts"] = [
        record
        for record in manifest["artifacts"]
        if record["name"] != physical.HUB_LIFECYCLE_PLAN_NAME
    ]
    _rewrite_gate(root, gate, manifest)

    with pytest.raises(physical.PhysicalAcceptanceError, match="Hub nine-app"):
        physical.verify_acceptance(
            candidate_index=candidate_path,
            evidence_root=root,
            keyring=keyring,
            signature_verifier=_signature,
        )


def test_device_gates_require_the_paperless_functional_plan_and_result_pair(
    tmp_path: Path,
) -> None:
    candidate_path, root, keyring, _candidate_value = _evidence(tmp_path)
    gate = "physical_x86_64_install_and_cold_boot"
    directory = root / gate
    (directory / physical.PAPERLESS_FUNCTIONAL_PLAN_NAME).unlink()
    manifest = json.loads((directory / "evidence.json").read_text())
    manifest["artifacts"] = [
        record
        for record in manifest["artifacts"]
        if record["name"] != physical.PAPERLESS_FUNCTIONAL_PLAN_NAME
    ]
    _rewrite_gate(root, gate, manifest)

    with pytest.raises(physical.PhysicalAcceptanceError, match="Paperless OCR/Office"):
        physical.verify_acceptance(
            candidate_index=candidate_path,
            evidence_root=root,
            keyring=keyring,
            signature_verifier=_signature,
        )


def test_device_gate_rejects_forged_paperless_functional_result(tmp_path: Path) -> None:
    candidate_path, root, keyring, _candidate_value = _evidence(tmp_path)
    gate = "physical_x86_64_install_and_cold_boot"
    result = json.loads((root / gate / physical.PAPERLESS_FUNCTIONAL_RESULT_NAME).read_text())
    result["checks"]["chineseOcrVerified"] = False
    unsigned = dict(result)
    unsigned.pop("resultId")
    result["resultId"] = _digest(physical.paperless_lab._canonical(unsigned))
    _rewrite_paperless_functional(root, gate, result=result)

    with pytest.raises(physical.PhysicalAcceptanceError, match="Paperless.*invalid"):
        physical.verify_acceptance(
            candidate_index=candidate_path,
            evidence_root=root,
            keyring=keyring,
            signature_verifier=_signature,
        )


def test_device_gates_require_the_lan_discovery_functional_plan_and_result_pair(
    tmp_path: Path,
) -> None:
    candidate_path, root, keyring, _candidate_value = _evidence(tmp_path)
    gate = "physical_x86_64_install_and_cold_boot"
    directory = root / gate
    (directory / physical.LAN_DISCOVERY_FUNCTIONAL_PLAN_NAME).unlink()
    manifest = json.loads((directory / "evidence.json").read_text())
    manifest["artifacts"] = [
        record
        for record in manifest["artifacts"]
        if record["name"] != physical.LAN_DISCOVERY_FUNCTIONAL_PLAN_NAME
    ]
    _rewrite_gate(root, gate, manifest)

    with pytest.raises(physical.PhysicalAcceptanceError, match="LAN discovery functional"):
        physical.verify_acceptance(
            candidate_index=candidate_path,
            evidence_root=root,
            keyring=keyring,
            signature_verifier=_signature,
        )


def test_device_gate_rejects_forged_lan_discovery_result(tmp_path: Path) -> None:
    candidate_path, root, keyring, _candidate_value = _evidence(tmp_path)
    gate = "physical_x86_64_install_and_cold_boot"
    result = json.loads((root / gate / physical.LAN_DISCOVERY_FUNCTIONAL_RESULT_NAME).read_text())
    result["syncthing"]["companion"]["machineIdentitySha256"] = result["syncthing"]["nas"][
        "machineIdentitySha256"
    ]
    unsigned = dict(result)
    unsigned.pop("resultId")
    result["resultId"] = _digest(physical.lan_discovery_lab._canonical(unsigned))
    _rewrite_lan_discovery_functional(root, gate, result=result)

    with pytest.raises(physical.PhysicalAcceptanceError, match="LAN discovery.*invalid"):
        physical.verify_acceptance(
            candidate_index=candidate_path,
            evidence_root=root,
            keyring=keyring,
            signature_verifier=_signature,
        )


def test_device_gate_rejects_rehashed_lan_probe_that_differs_from_result(
    tmp_path: Path,
) -> None:
    candidate_path, root, keyring, _candidate_value = _evidence(tmp_path)
    gate = "physical_x86_64_install_and_cold_boot"
    directory = root / gate
    path = directory / physical.lan_discovery_lab.SYNCTHING_NAS_NAME
    value = json.loads(path.read_text())
    value["details"]["trafficBytes"] += 1
    raw = physical.lan_discovery_lab._canonical(value)
    path.write_bytes(raw)
    manifest = json.loads((directory / "evidence.json").read_text())
    record = next(item for item in manifest["artifacts"] if item["name"] == path.name)
    record.update(sha256=_digest(raw), size=len(raw))
    _rewrite_gate(root, gate, manifest)

    with pytest.raises(physical.PhysicalAcceptanceError, match="probe artifact bytes"):
        physical.verify_acceptance(
            candidate_index=candidate_path,
            evidence_root=root,
            keyring=keyring,
            signature_verifier=_signature,
        )


def test_device_gate_rejects_semantically_tampered_hub_runtime_evidence(tmp_path: Path) -> None:
    candidate_path, root, keyring, _candidate_value = _evidence(tmp_path)
    gate = "physical_x86_64_install_and_cold_boot"
    result = json.loads((root / gate / physical.HUB_LIFECYCLE_RESULT_NAME).read_text())
    network = result["firstInstall"]["immich"]["installation"]["services"]["database"]["networks"][
        0
    ]
    network["internal"] = not network["internal"]
    unsigned = dict(result)
    unsigned.pop("resultId")
    result["resultId"] = _digest(physical.hub_lab._canonical(unsigned))
    _rewrite_hub_lifecycle(root, gate, result=result)

    with pytest.raises(physical.PhysicalAcceptanceError, match="network evidence"):
        physical.verify_acceptance(
            candidate_index=candidate_path,
            evidence_root=root,
            keyring=keyring,
            signature_verifier=_signature,
        )


def test_device_gate_rejects_hub_evidence_from_another_candidate(tmp_path: Path) -> None:
    candidate_path, root, keyring, _candidate_value = _evidence(tmp_path)
    gate = "physical_x86_64_install_and_cold_boot"
    plan = json.loads((root / gate / physical.HUB_LIFECYCLE_PLAN_NAME).read_text())
    result = json.loads((root / gate / physical.HUB_LIFECYCLE_RESULT_NAME).read_text())
    plan["releaseCandidate"]["sourceRevision"] = "f" * 40
    identity = {key: value for key, value in plan.items() if key not in {"planId", "confirmation"}}
    plan_id = _digest(physical.hub_lab._canonical(identity))
    plan["planId"] = plan_id
    plan["confirmation"] = f"RUN ECHO HUB LIFECYCLE {plan_id}"
    result["planId"] = plan_id
    result["releaseCandidate"] = plan["releaseCandidate"]
    unsigned = dict(result)
    unsigned.pop("resultId")
    result["resultId"] = _digest(physical.hub_lab._canonical(unsigned))
    _rewrite_hub_lifecycle(root, gate, plan=plan, result=result)

    with pytest.raises(physical.PhysicalAcceptanceError, match="another release candidate"):
        physical.verify_acceptance(
            candidate_index=candidate_path,
            evidence_root=root,
            keyring=keyring,
            signature_verifier=_signature,
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"allPassed": False}, "invalid lifecycle contract"),
        ({"labPlanId": "not-a-plan"}, "invalid lifecycle contract"),
        ({"candidate": {}}, "invalid lifecycle contract"),
    ],
)
def test_protocol_lifecycle_rejects_false_foreign_or_unbound_claims(
    tmp_path: Path, mutation: dict[str, Any], message: str
) -> None:
    candidate_path, root, keyring, _candidate_value = _evidence(tmp_path)
    gate = physical.PROTOCOL_INTEROPERABILITY_GATE
    value = json.loads(
        (root / gate / physical.PROTOCOL_INTEROPERABILITY_LIFECYCLE_NAME).read_text()
    )
    value.update(mutation)
    _rewrite_protocol_lifecycle(root, value)

    with pytest.raises(physical.PhysicalAcceptanceError, match=message):
        physical.verify_acceptance(
            candidate_index=candidate_path,
            evidence_root=root,
            keyring=keyring,
            signature_verifier=_signature,
        )


def test_protocol_lifecycle_rejects_a_check_bound_to_other_bytes(tmp_path: Path) -> None:
    candidate_path, root, keyring, _candidate_value = _evidence(tmp_path)
    gate = physical.PROTOCOL_INTEROPERABILITY_GATE
    value = json.loads(
        (root / gate / physical.PROTOCOL_INTEROPERABILITY_LIFECYCLE_NAME).read_text()
    )
    value["checks"]["windowsSmbReadWrite"]["evidence"]["sha256"] = "f" * 64
    _rewrite_protocol_lifecycle(root, value)

    with pytest.raises(physical.PhysicalAcceptanceError, match="evidence.*unbound"):
        physical.verify_acceptance(
            candidate_index=candidate_path,
            evidence_root=root,
            keyring=keyring,
            signature_verifier=_signature,
        )


def test_rejects_manifest_that_omits_the_required_gate_result(tmp_path: Path) -> None:
    candidate_path, root, keyring, _candidate_value = _evidence(tmp_path)
    gate = physical.PHYSICAL_GATES[0]
    directory = root / gate
    (directory / physical.GATE_RESULT_NAME).unlink()
    manifest = json.loads((directory / "evidence.json").read_text())
    manifest["artifacts"] = [
        record for record in manifest["artifacts"] if record["name"] != physical.GATE_RESULT_NAME
    ]
    _rewrite_gate(root, gate, manifest)

    with pytest.raises(physical.PhysicalAcceptanceError, match="omit required gate-result.json"):
        physical.verify_acceptance(
            candidate_index=candidate_path,
            evidence_root=root,
            keyring=keyring,
            signature_verifier=_signature,
        )


@pytest.mark.parametrize(
    ("started", "finished", "message"),
    [
        ("2026-08-27T01:00Z", "2026-08-27T02:00:00Z", "canonical UTC"),
        ("2026-08-27T01:00:00Z", "2026-08-27T02:00:00Z", "duration"),
        ("2026-08-27T01:00:00Z", "2026-09-03T01:00:00.500000Z", "duration"),
    ],
)
def test_rejects_noncanonical_or_out_of_bounds_execution_time(
    tmp_path: Path, started: str, finished: str, message: str
) -> None:
    candidate_path, root, keyring, _candidate_value = _evidence(tmp_path)
    gate = physical.PHYSICAL_GATES[0]
    manifest = json.loads((root / gate / "evidence.json").read_text())
    manifest["execution"].update(startedAt=started, finishedAt=finished)
    _rewrite_gate(root, gate, manifest)

    with pytest.raises(physical.PhysicalAcceptanceError, match=message):
        physical.verify_acceptance(
            candidate_index=candidate_path,
            evidence_root=root,
            keyring=keyring,
            signature_verifier=_signature,
        )


def test_rejects_artifact_hash_mismatch_or_unexpected_file(tmp_path: Path) -> None:
    candidate_path, root, keyring, _candidate_value = _evidence(tmp_path)
    gate = physical.PHYSICAL_GATES[0]
    directory = root / gate
    (directory / "acceptance.log").write_text("rewritten\n", encoding="utf-8")

    with pytest.raises(physical.PhysicalAcceptanceError, match="does not match"):
        physical.verify_acceptance(
            candidate_index=candidate_path,
            evidence_root=root,
            keyring=keyring,
            signature_verifier=_signature,
        )

    _candidate_path, second_root, second_keyring, _candidate_value = _evidence(tmp_path / "second")
    (second_root / physical.PHYSICAL_GATES[0] / "unlisted.txt").write_text("extra\n")
    with pytest.raises(physical.PhysicalAcceptanceError, match="unexpected files"):
        physical.verify_acceptance(
            candidate_index=_candidate_path,
            evidence_root=second_root,
            keyring=second_keyring,
            signature_verifier=_signature,
        )


@pytest.mark.parametrize(
    "sensitive_line",
    [
        "serial=ABC123",
        "wwn: 5000c500deadbeef",
        "/dev/disk/by-id/nvme-secret",
        "password=hunter2",
        "Authorization: Bearer secret",
    ],
)
def test_rejects_sensitive_identifiers_or_credentials_in_text_artifacts(
    tmp_path: Path, sensitive_line: str
) -> None:
    candidate_path, root, keyring, _candidate_value = _evidence(tmp_path)
    gate = physical.PHYSICAL_GATES[0]
    directory = root / gate
    manifest = json.loads((directory / "evidence.json").read_text())
    marker = manifest["result"]["marker"]
    raw = f"{marker}\n{sensitive_line}\n".encode()
    (directory / "acceptance.log").write_bytes(raw)
    manifest["artifacts"][0].update(sha256=_digest(raw), size=len(raw))
    _rewrite_gate(root, gate, manifest)

    with pytest.raises(physical.PhysicalAcceptanceError, match="sensitive data"):
        physical.verify_acceptance(
            candidate_index=candidate_path,
            evidence_root=root,
            keyring=keyring,
            signature_verifier=_signature,
        )


def test_rejects_signature_for_another_manifest_or_acceptance_key(tmp_path: Path) -> None:
    candidate_path, root, keyring, _candidate_value = _evidence(tmp_path)

    def wrong_manifest(manifest: Path, signature: Path, keyring_path: Path) -> dict[str, str]:
        result = _signature(manifest, signature, keyring_path)
        result["manifestSha256"] = "f" * 64
        return result

    with pytest.raises(physical.PhysicalAcceptanceError, match="another manifest"):
        physical.verify_acceptance(
            candidate_index=candidate_path,
            evidence_root=root,
            keyring=keyring,
            signature_verifier=wrong_manifest,
        )

    def split_keys(manifest: Path, signature: Path, keyring_path: Path) -> dict[str, str]:
        result = _signature(manifest, signature, keyring_path)
        if manifest.parent.name == physical.PHYSICAL_GATES[-1]:
            result["keyringSha256"] = "f" * 64
        return result

    with pytest.raises(physical.PhysicalAcceptanceError, match="one acceptance keyring"):
        physical.verify_acceptance(
            candidate_index=candidate_path,
            evidence_root=root,
            keyring=keyring,
            signature_verifier=split_keys,
        )

    def split_signers(manifest: Path, signature: Path, keyring_path: Path) -> dict[str, str]:
        result = _signature(manifest, signature, keyring_path)
        if manifest.parent.name == physical.PHYSICAL_GATES[-1]:
            result["signerFingerprint"] = "B" * 40
        return result

    with pytest.raises(physical.PhysicalAcceptanceError, match="one acceptance key"):
        physical.verify_acceptance(
            candidate_index=candidate_path,
            evidence_root=root,
            keyring=keyring,
            signature_verifier=split_signers,
        )


def test_rejects_tampered_candidate_index_and_non_uuid_lab_run(tmp_path: Path) -> None:
    candidate_path, root, keyring, candidate = _evidence(tmp_path)
    candidate["source"]["commit"] = "f" * 40
    _write_json(candidate_path, candidate)
    with pytest.raises(physical.PhysicalAcceptanceError, match="index ID"):
        physical.verify_acceptance(
            candidate_index=candidate_path,
            evidence_root=root,
            keyring=keyring,
            signature_verifier=_signature,
        )

    second = tmp_path / "uuid"
    second.mkdir()
    candidate_path, root, keyring, _candidate_value = _evidence(second)
    gate = physical.PHYSICAL_GATES[0]
    manifest = json.loads((root / gate / "evidence.json").read_text())
    manifest["execution"]["labRunId"] = str(uuid.uuid1())
    _rewrite_gate(root, gate, manifest)
    with pytest.raises(physical.PhysicalAcceptanceError, match="UUIDv4"):
        physical.verify_acceptance(
            candidate_index=candidate_path,
            evidence_root=root,
            keyring=keyring,
            signature_verifier=_signature,
        )


def test_rejects_reusing_one_lab_run_id_across_physical_gates(tmp_path: Path) -> None:
    candidate_path, root, keyring, _candidate_value = _evidence(tmp_path)
    first_gate, second_gate = physical.PHYSICAL_GATES[:2]
    first = json.loads((root / first_gate / "evidence.json").read_text())
    second = json.loads((root / second_gate / "evidence.json").read_text())
    second["execution"]["labRunId"] = first["execution"]["labRunId"]
    _rewrite_gate(root, second_gate, second)

    with pytest.raises(physical.PhysicalAcceptanceError, match="six distinct lab run IDs"):
        physical.verify_acceptance(
            candidate_index=candidate_path,
            evidence_root=root,
            keyring=keyring,
            signature_verifier=_signature,
        )


def test_cli_writes_read_only_product_delivery_manifest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    candidate_path, root, keyring, _candidate_value = _evidence(tmp_path)
    output = tmp_path / "product-delivery.json"

    exit_code = physical.main(
        [
            "--candidate-index",
            str(candidate_path),
            "--evidence-root",
            str(root),
            "--acceptance-keyring",
            str(keyring),
            "--output",
            str(output),
        ],
        signature_verifier=_signature,
    )

    assert exit_code == 0
    assert json.loads(output.read_text())["nasProductDeliveryReady"] is True
    assert output.stat().st_mode & 0o777 == 0o444
    assert "ECHO_NAS_PRODUCT_DELIVERY_READY" in capsys.readouterr().out
