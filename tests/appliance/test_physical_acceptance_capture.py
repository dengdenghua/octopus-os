from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest

from deploy.appliance import physical_acceptance as physical
from tests.appliance.hub_lifecycle_fixture import hub_lifecycle_material
from tests.appliance.lan_discovery_functional_fixture import (
    lan_discovery_functional_material,
)
from tests.appliance.paperless_functional_fixture import paperless_functional_material

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "appliance" / "physical_acceptance_capture.py"
SPEC = importlib.util.spec_from_file_location("echo_physical_acceptance_capture", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
capture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(capture)

OS_COMMIT = "1" * 40
AGENT_COMMIT = "2" * 40
RELEASE_TAG = "echo-appliance-v1.0.0"


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _candidate(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
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
            "remainingGates": list(capture.PHYSICAL_GATES),
        },
    }
    value["indexId"] = _digest(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path, value


def _power_state_material(
    candidate: dict[str, Any], *, plan_id: str = "d" * 64
) -> tuple[dict[str, bytes], bytes]:
    boot_a = "11111111-1111-4111-8111-111111111111"
    boot_b = "22222222-2222-4222-8222-222222222222"
    previous = f"ghcr.io/echo-os/echo-os@sha256:{'5' * 64}"
    target = candidate["evidence"]["appliance"]["immutableReference"]
    canaries = {
        "state": {
            "path": (
                "/opt/echo-appliance-operations-"
                f"{candidate['evidence']['appliance']['operationsBundle']['artifactId']}"
                "/data/power-state-canary.bin"
            ),
            "sha256": "a" * 64,
            "size": capture.POWER_STATE_CANARY_BYTES,
        },
        "nas": {
            "path": "/srv/echo-nas/power-state-canary.bin",
            "sha256": "b" * 64,
            "size": capture.POWER_STATE_NAS_CANARY_BYTES,
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
        capture.POWER_STATE_PHASE_EVIDENCE_NAMES[phase]: (
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
        for phase in capture.POWER_STATE_PHASES
    }
    phases = {
        phase: {"name": name, "sha256": _digest(logs[name]), "size": len(logs[name])}
        for phase, name in capture.POWER_STATE_PHASE_EVIDENCE_NAMES.items()
    }
    evidence = {
        check: next(
            phases[phase]
            for phase, name in capture.POWER_STATE_PHASE_EVIDENCE_NAMES.items()
            if name == capture.POWER_STATE_EVIDENCE_NAMES[check]
        )
        for check in capture.POWER_STATE_LIFECYCLE_CHECKS
    }
    lifecycle = (
        json.dumps(
            capture._power_state_lifecycle_payload(
                evidence,
                phases,
                context,
                candidate={
                    "indexId": candidate["indexId"],
                    "sourceRevision": OS_COMMIT,
                    "agentRevision": AGENT_COMMIT,
                    "releaseTag": RELEASE_TAG,
                    "operationsArtifactId": "7" * 16,
                    "operationsArchiveSha256": "8" * 64,
                },
                lab_plan_id=plan_id,
            ),
            sort_keys=True,
        )
        + "\n"
    ).encode()
    return logs, lifecycle


def _bare_metal_material(
    candidate: dict[str, Any], *, plan_id: str
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, bytes]]:
    appliance = {
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
        "appliance": appliance,
        "canaries": canaries,
    }
    context = {
        "sourceSystem": source,
        "backups": backups,
        "verifierArchitecture": "amd64",
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
            "appliance": appliance,
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
            "appliance": appliance,
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
            "appliance": appliance,
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
            "appliance": appliance,
        },
    }
    logs = {
        capture.BARE_METAL_PHASE_EVIDENCE_NAMES[phase]: (
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
        for phase in capture.BARE_METAL_PHASES
    }
    return context, details, logs


def _gate_files(tmp_path: Path, gate: str) -> tuple[Path, Path, Path, dict[str, Any]]:
    candidate_path, candidate = _candidate(tmp_path)
    directory = tmp_path / gate
    directory.mkdir()
    identity = {
        "indexId": candidate["indexId"],
        "sourceRevision": OS_COMMIT,
        "agentRevision": AGENT_COMMIT,
        "releaseTag": RELEASE_TAG,
    }
    marker = capture._expected_marker(gate, identity)
    primary = directory / "acceptance.log"
    primary.write_text(f"start\n{marker}\nfinish\n", encoding="utf-8")
    attachment = directory / capture.HARDWARE_PROFILE_NAME
    requirement = capture.GATE_REQUIREMENTS[gate]
    attachment.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "kind": "echo.physical-hardware-profile",
                "gate": gate,
                "profileClass": requirement["profileClass"],
                "architecture": requirement["architecture"] or "x86_64",
                "deviceCount": requirement["minimumDevices"],
                "serialsRedacted": True,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    gate_result = directory / capture.GATE_RESULT_NAME
    gate_result.write_text(
        json.dumps(capture._gate_result_payload(gate), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if gate == capture.OPERATIONS_SYSTEMD_GATE:
        evidence = {
            check: {
                "name": primary.name,
                "sha256": _digest(primary.read_bytes()),
                "size": primary.stat().st_size,
            }
            for check in capture.OPERATIONS_SYSTEMD_LIFECYCLE_CHECKS
        }
        (directory / capture.OPERATIONS_SYSTEMD_LIFECYCLE_NAME).write_text(
            json.dumps(
                capture._operations_systemd_lifecycle_payload(
                    evidence,
                    candidate={
                        "indexId": candidate["indexId"],
                        "sourceRevision": OS_COMMIT,
                        "agentRevision": AGENT_COMMIT,
                        "releaseTag": RELEASE_TAG,
                        "operationsArtifactId": "7" * 16,
                        "operationsArchiveSha256": "8" * 64,
                    },
                    lab_plan_id="9" * 64,
                ),
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        logs, lifecycle = _power_state_material(candidate)
        for name, raw in logs.items():
            (directory / name).write_bytes(raw)
        (directory / capture.POWER_STATE_LIFECYCLE_NAME).write_bytes(lifecycle)
    if gate == capture.STORAGE_RECOVERY_GATE:
        logs = {
            name: (json.dumps({"machineGenerated": name}, sort_keys=True) + "\n").encode()
            for name in {*capture.STORAGE_LAB_EVIDENCE.values(), "storage-reconnect.log"}
        }
        for name, raw in logs.items():
            (directory / name).write_bytes(raw)
        evidence = {
            check: {
                "name": name,
                "sha256": _digest(logs[name]),
                "size": len(logs[name]),
            }
            for check, name in capture.STORAGE_LAB_EVIDENCE.items()
        }
        (directory / capture.STORAGE_RECOVERY_LIFECYCLE_NAME).write_text(
            json.dumps(
                capture._storage_recovery_lifecycle_payload(
                    evidence,
                    candidate={
                        "indexId": candidate["indexId"],
                        "sourceRevision": OS_COMMIT,
                        "agentRevision": AGENT_COMMIT,
                        "releaseTag": RELEASE_TAG,
                        "operationsArtifactId": "7" * 16,
                        "operationsArchiveSha256": "8" * 64,
                    },
                    lab_plan_id="a" * 64,
                ),
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    if gate == capture.PROTOCOL_INTEROPERABILITY_GATE:
        logs = {
            name: (json.dumps({"machineGenerated": name}, sort_keys=True) + "\n").encode()
            for name in physical.PROTOCOL_INTEROPERABILITY_EVIDENCE_NAMES.values()
        }
        for name, raw in logs.items():
            (directory / name).write_bytes(raw)
        evidence = {
            check: {"name": name, "sha256": _digest(logs[name]), "size": len(logs[name])}
            for check, name in physical.PROTOCOL_INTEROPERABILITY_EVIDENCE_NAMES.items()
        }
        (directory / capture.PROTOCOL_INTEROPERABILITY_LIFECYCLE_NAME).write_text(
            json.dumps(
                physical._protocol_interoperability_lifecycle_payload(
                    evidence,
                    candidate={
                        "indexId": candidate["indexId"],
                        "sourceRevision": OS_COMMIT,
                        "agentRevision": AGENT_COMMIT,
                        "releaseTag": RELEASE_TAG,
                        "operationsArtifactId": "7" * 16,
                        "operationsArchiveSha256": "8" * 64,
                    },
                    lab_plan_id="b" * 64,
                ),
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    if gate in capture.DEVICE_ENDURANCE_GATES:
        architecture = "arm64" if requirement["architecture"] == "arm64" else "amd64"
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
            for name in capture.DEVICE_ENDURANCE_PHASE_EVIDENCE_NAMES
        }
        logs[capture.HUB_LIFECYCLE_RESULT_NAME] = hub_result
        logs[capture.PAPERLESS_FUNCTIONAL_RESULT_NAME] = paperless_result
        logs[capture.LAN_DISCOVERY_FUNCTIONAL_RESULT_NAME] = lan_result
        logs.update(lan_probes)
        for name, raw in logs.items():
            (directory / name).write_bytes(raw)
        hub_plan_path = directory / capture.HUB_LIFECYCLE_PLAN_NAME
        hub_plan_path.write_bytes(hub_plan)
        hub_plan_path.chmod(0o400)
        (directory / capture.HUB_LIFECYCLE_RESULT_NAME).chmod(0o444)
        paperless_plan_path = directory / capture.PAPERLESS_FUNCTIONAL_PLAN_NAME
        paperless_plan_path.write_bytes(paperless_plan)
        paperless_plan_path.chmod(0o400)
        (directory / capture.PAPERLESS_FUNCTIONAL_RESULT_NAME).chmod(0o444)
        lan_plan_path = directory / capture.LAN_DISCOVERY_FUNCTIONAL_PLAN_NAME
        lan_plan_path.write_bytes(lan_plan)
        lan_plan_path.chmod(0o400)
        (directory / capture.LAN_DISCOVERY_FUNCTIONAL_RESULT_NAME).chmod(0o444)
        for name in capture.LAN_DISCOVERY_PROBE_NAMES:
            (directory / name).chmod(0o444)
        evidence = {
            check: {"name": name, "sha256": _digest(logs[name]), "size": len(logs[name])}
            for check, name in capture.DEVICE_ENDURANCE_EVIDENCE_NAMES.items()
        }
        (directory / capture.DEVICE_ENDURANCE_LIFECYCLE_NAME).write_text(
            json.dumps(
                capture._device_endurance_lifecycle_payload(
                    evidence,
                    candidate={
                        "indexId": candidate["indexId"],
                        "sourceRevision": OS_COMMIT,
                        "agentRevision": AGENT_COMMIT,
                        "releaseTag": RELEASE_TAG,
                        "operationsArtifactId": "7" * 16,
                        "operationsArchiveSha256": "8" * 64,
                    },
                    lab_plan_id="c" * 64,
                    gate=gate,
                ),
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    return candidate_path, primary, attachment, candidate


def _gate_artifacts(primary: Path, profile: Path, *additional: Path) -> list[Path]:
    artifacts = [primary, profile, primary.parent / capture.GATE_RESULT_NAME]
    lifecycle = primary.parent / capture.OPERATIONS_SYSTEMD_LIFECYCLE_NAME
    if lifecycle.exists():
        artifacts.append(lifecycle)
    power_lifecycle = primary.parent / capture.POWER_STATE_LIFECYCLE_NAME
    if power_lifecycle.exists():
        artifacts.append(power_lifecycle)
        artifacts.extend(
            primary.parent / name for name in capture.POWER_STATE_PHASE_EVIDENCE_NAMES.values()
        )
    storage_lifecycle = primary.parent / capture.STORAGE_RECOVERY_LIFECYCLE_NAME
    if storage_lifecycle.exists():
        artifacts.append(storage_lifecycle)
        artifacts.extend(
            primary.parent / name
            for name in {*capture.STORAGE_LAB_EVIDENCE.values(), "storage-reconnect.log"}
        )
    protocol_lifecycle = primary.parent / capture.PROTOCOL_INTEROPERABILITY_LIFECYCLE_NAME
    if protocol_lifecycle.exists():
        artifacts.append(protocol_lifecycle)
        artifacts.extend(
            primary.parent / name
            for name in physical.PROTOCOL_INTEROPERABILITY_EVIDENCE_NAMES.values()
        )
    device_lifecycle = primary.parent / capture.DEVICE_ENDURANCE_LIFECYCLE_NAME
    if device_lifecycle.exists():
        artifacts.append(device_lifecycle)
        artifacts.extend(
            primary.parent / name for name in capture.DEVICE_ENDURANCE_PHASE_EVIDENCE_NAMES
        )
        artifacts.extend(
            (
                primary.parent / capture.HUB_LIFECYCLE_PLAN_NAME,
                primary.parent / capture.HUB_LIFECYCLE_RESULT_NAME,
                primary.parent / capture.PAPERLESS_FUNCTIONAL_PLAN_NAME,
                primary.parent / capture.PAPERLESS_FUNCTIONAL_RESULT_NAME,
                primary.parent / capture.LAN_DISCOVERY_FUNCTIONAL_PLAN_NAME,
                primary.parent / capture.LAN_DISCOVERY_FUNCTIONAL_RESULT_NAME,
                *(primary.parent / name for name in capture.lan_discovery_lab.PROBE_NAMES),
            )
        )
    bare_metal_lifecycle = primary.parent / capture.BARE_METAL_LIFECYCLE_NAME
    if bare_metal_lifecycle.exists():
        artifacts.append(bare_metal_lifecycle)
        artifacts.extend(
            primary.parent / name for name in capture.BARE_METAL_PHASE_EVIDENCE_NAMES.values()
        )
    return [*artifacts, *additional]


def _operations_lab_plan(candidate_path: Path, evidence_directory: Path) -> Path:
    candidate = capture._candidate(candidate_path)
    payload = {
        "schemaVersion": 2,
        "kind": "echo.operations-systemd-physical-lab-plan",
        "releaseCandidate": {
            "indexPath": candidate["indexPath"],
            "indexId": candidate["indexId"],
            "indexSha256": candidate["indexSha256"],
            "osRepository": candidate["repository"],
            "sourceRevision": candidate["sourceRevision"],
            "agentRepository": candidate["agentRepository"],
            "agentRevision": candidate["agentRevision"],
            "releaseTag": candidate["releaseTag"],
            "applianceManifestSha256": candidate["applianceManifestSha256"],
            "immutableReference": candidate["immutableReference"],
            "operationsArtifactId": candidate["operationsArtifactId"],
            "operationsArchiveSha256": candidate["operationsArchiveSha256"],
        },
        "operationsBundle": {
            "artifactId": candidate["operationsArtifactId"],
            "archiveSha256": candidate["operationsArchiveSha256"],
            "imageReference": candidate["immutableReference"],
            "manifestSha256": "a" * 64,
            "labToolSha256": "b" * 64,
            "labToolSize": 123,
        },
        "platform": {},
        "config": {},
        "installPlan": {},
        "evidenceDirectory": str(evidence_directory.resolve()),
        "preservation": {},
        "baseline": {},
        "phases": list(capture.OPERATIONS_LAB_PHASES),
    }
    plan_id = _digest((json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode())
    payload["planId"] = plan_id
    payload["confirmations"] = {
        phase: f"RUN ECHO OPERATIONS LAB {phase} {plan_id}"
        for phase in capture.OPERATIONS_LAB_PHASES
    }
    path = candidate_path.parent / "operations-lab-plan.json"
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _power_state_lab_plan(candidate_path: Path, evidence_directory: Path) -> tuple[Path, str]:
    candidate = capture._candidate(candidate_path)
    bundle_root = f"/opt/echo-appliance-operations-{candidate['operationsArtifactId']}"
    boot_id = "11111111-1111-4111-8111-111111111111"
    payload: dict[str, Any] = {
        "schemaVersion": 1,
        "kind": "echo.power-state-physical-lab-plan",
        "gate": capture.OPERATIONS_SYSTEMD_GATE,
        "releaseCandidate": {
            "indexPath": candidate["indexPath"],
            "indexId": candidate["indexId"],
            "indexSha256": candidate["indexSha256"],
            "osRepository": candidate["repository"],
            "sourceRevision": candidate["sourceRevision"],
            "agentRepository": candidate["agentRepository"],
            "agentRevision": candidate["agentRevision"],
            "releaseTag": candidate["releaseTag"],
            "applianceManifestSha256": candidate["applianceManifestSha256"],
            "immutableReference": candidate["immutableReference"],
            "operationsArtifactId": candidate["operationsArtifactId"],
            "operationsArchiveSha256": candidate["operationsArchiveSha256"],
        },
        "bundleRoot": bundle_root,
        "operationsBundle": {
            "artifactId": candidate["operationsArtifactId"],
            "archiveSha256": candidate["operationsArchiveSha256"],
            "imageReference": candidate["immutableReference"],
            "manifestSha256": "9" * 64,
            "tools": {
                name: character * 64
                for name, character in zip(
                    (
                        "power_state_recovery_lab.py",
                        "upgrade-appliance.sh",
                        "recover-appliance-upgrade.sh",
                        "upgrade_transaction.py",
                        "backup-state.sh",
                        "restore-state.sh",
                        "install-appliance.sh",
                    ),
                    "abcdef1",
                    strict=True,
                )
            },
        },
        "operationsLabPlan": {"path": "/root/operations-plan.json", "planId": "2" * 64},
        "evidenceDirectory": str(evidence_directory.resolve()),
        "previousImage": f"ghcr.io/echo-os/echo-os@sha256:{'5' * 64}",
        "targetImage": candidate["immutableReference"],
        "releaseEnvironment": f"{bundle_root}/echo-release.env",
        "transactionPath": f"{bundle_root}/.echo-upgrade-transaction.json",
        "baselineBootId": boot_id,
        "containers": {"main": "echo-os", "proxy": "echo-docker-control"},
        "canaries": {
            "state": {
                "path": f"{bundle_root}/data/power-state-canary.bin",
                "sha256": "a" * 64,
                "size": capture.POWER_STATE_CANARY_BYTES,
            },
            "nas": {
                "path": "/srv/echo-nas/power-state-canary.bin",
                "sha256": "b" * 64,
                "size": capture.POWER_STATE_NAS_CANARY_BYTES,
            },
        },
        "backup": {
            "directory": "/media/off-device/echo-backups",
            "mountpoint": "/media/off-device",
            "credential": "/etc/credstore.encrypted/echo-backup-passphrase",
        },
        "hostTools": {
            "docker": "/usr/bin/docker",
            "systemctl": "/usr/bin/systemctl",
            "systemd_run": "/usr/bin/systemd-run",
            "systemd_creds": "/usr/bin/systemd-creds",
            "journalctl": "/usr/bin/journalctl",
            "logger": "/usr/bin/logger",
            "sync": "/usr/bin/sync",
            "dpkg_query": "/usr/bin/dpkg-query",
        },
        "phases": list(capture.POWER_STATE_PHASES),
    }
    plan_id = _digest((json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode())
    payload["planId"] = plan_id
    payload["confirmations"] = {
        phase: f"RUN ECHO POWER STATE LAB {phase} {plan_id}" for phase in capture.POWER_STATE_PHASES
    }
    path = candidate_path.parent / "power-state-lab-plan.json"
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    return path, plan_id


def _write_bare_metal_lab_plan(
    candidate_path: Path,
    evidence_directory: Path,
    context: dict[str, Any],
) -> tuple[Path, str]:
    candidate = capture._candidate(candidate_path)
    bundle_root = f"/opt/echo-appliance-operations-{candidate['operationsArtifactId']}"
    tools = (
        "power_state_recovery_lab.py",
        "upgrade-appliance.sh",
        "recover-appliance-upgrade.sh",
        "upgrade_transaction.py",
        "backup-state.sh",
        "restore-state.sh",
        "install-appliance.sh",
        "bare_metal_recovery_lab.py",
        "nas_data_backup.py",
        "verify-running-appliance.py",
    )
    payload: dict[str, Any] = {
        "schemaVersion": 1,
        "kind": "echo.bare-metal-recovery-physical-lab-plan",
        "gate": capture.BARE_METAL_GATE,
        "releaseCandidate": {
            "indexPath": candidate["indexPath"],
            "indexId": candidate["indexId"],
            "indexSha256": candidate["indexSha256"],
            "osRepository": candidate["repository"],
            "sourceRevision": candidate["sourceRevision"],
            "agentRepository": candidate["agentRepository"],
            "agentRevision": candidate["agentRevision"],
            "releaseTag": candidate["releaseTag"],
            "applianceManifestSha256": candidate["applianceManifestSha256"],
            "immutableReference": candidate["immutableReference"],
            "operationsArtifactId": candidate["operationsArtifactId"],
            "operationsArchiveSha256": candidate["operationsArchiveSha256"],
        },
        "bundleRoot": bundle_root,
        "operationsBundle": {
            "artifactId": candidate["operationsArtifactId"],
            "archiveSha256": candidate["operationsArchiveSha256"],
            "imageReference": candidate["immutableReference"],
            "manifestSha256": "9" * 64,
            "tools": {name: format(index, "x") * 64 for index, name in enumerate(tools, 1)},
        },
        "installer": {
            "bundle": "/media/echo-installer/install",
            "target": "/dev/sdz",
            "recoveryKey": {
                "path": "/root/echo-recovery-key.json",
                "sha256": "a" * 64,
                "size": 512,
            },
        },
        "backups": context["backups"],
        "sourceSystem": context["sourceSystem"],
        "installedSystem": {
            "deploymentRoot": "/opt/echo-appliance",
            "agentRoot": "/var/lib/echo-agent",
            "agentUid": 1000,
            "nasRoot": "/srv/echo-nas",
            "architecture": "x86_64",
            "verifierArchitecture": "amd64",
            "sourceIdentity": "/usr/lib/echo-os/echo-os-source-identity",
            "userBackup": "/usr/bin/echo-os-backup",
            "userState": "/var/lib/echo-os/user-backup-state.json",
            "restoreHealth": "/usr/lib/echo-os/echo-restore-transaction.py",
        },
        "appliance": {
            "baseUrl": "https://127.0.0.1:8000",
            "mainContainer": "echo-os",
            "proxyContainer": "echo-docker-control",
        },
        "evidenceDirectory": str(evidence_directory.resolve()),
        "phases": list(capture.BARE_METAL_PHASES),
    }
    plan_id = _digest((json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode())
    payload["planId"] = plan_id
    payload["confirmations"] = {
        phase: f"RUN ECHO BARE METAL RECOVERY LAB {phase} {plan_id}"
        for phase in capture.BARE_METAL_PHASES[1:]
    }
    path = candidate_path.parent / "bare-metal-recovery-plan.json"
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o400)
    return path, plan_id


def _bare_metal_lab_plan(
    candidate_path: Path,
    evidence_directory: Path,
    context: dict[str, Any] | None = None,
) -> tuple[Path, str]:
    write_fixture_logs = context is None
    logs: dict[str, bytes] = {}
    if context is None:
        source_value = json.loads(candidate_path.read_text(encoding="utf-8"))
        context, _details, logs = _bare_metal_material(source_value, plan_id="0" * 64)
    path, plan_id = _write_bare_metal_lab_plan(
        candidate_path,
        evidence_directory,
        context,
    )
    if write_fixture_logs:
        for name, raw in logs.items():
            value = json.loads(raw)
            value["planId"] = plan_id
            evidence_path = evidence_directory / name
            evidence_path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
            evidence_path.chmod(0o444)
    return path, plan_id


def _storage_lab_plan(candidate_path: Path, evidence_directory: Path) -> Path:
    candidate = capture._candidate(candidate_path)
    mountpoint = "/srv/echo-storage-recovery-lab"
    payload: dict[str, Any] = {
        "schemaVersion": 1,
        "kind": "echo.storage-recovery-physical-lab-plan",
        "gate": capture.STORAGE_RECOVERY_GATE,
        "releaseCandidate": {
            "indexPath": candidate["indexPath"],
            "indexId": candidate["indexId"],
            "indexSha256": candidate["indexSha256"],
            "osRepository": candidate["repository"],
            "sourceRevision": candidate["sourceRevision"],
            "agentRepository": candidate["agentRepository"],
            "agentRevision": candidate["agentRevision"],
            "releaseTag": candidate["releaseTag"],
            "applianceManifestSha256": candidate["applianceManifestSha256"],
            "immutableReference": candidate["immutableReference"],
            "operationsArtifactId": candidate["operationsArtifactId"],
            "operationsArchiveSha256": candidate["operationsArchiveSha256"],
        },
        "bundleRoot": f"/opt/echo-appliance-operations-{candidate['operationsArtifactId']}",
        "operationsBundle": {
            "artifactId": candidate["operationsArtifactId"],
            "archiveSha256": candidate["operationsArchiveSha256"],
            "imageReference": candidate["immutableReference"],
            "manifestSha256": "a" * 64,
            "storageRecoveryLabSha256": "b" * 64,
            "runningVerifierSha256": "c" * 64,
        },
        "platform": {"id": "debian", "versionId": "13", "omvVersion": "8.7.3-1"},
        "devices": {
            "array": {"path": "/dev/md7", "majorMinor": "9:7", "sizeBytes": 8 * 1024**3},
            "members": [
                {
                    "path": "/dev/sdb1",
                    "parentPath": "/dev/sdb",
                    "majorMinor": "8:17",
                    "sizeBytes": 8 * 1024**3,
                    "identitySha256": "d" * 64,
                },
                {
                    "path": "/dev/sdc1",
                    "parentPath": "/dev/sdc",
                    "majorMinor": "8:33",
                    "sizeBytes": 8 * 1024**3,
                    "identitySha256": "e" * 64,
                },
            ],
            "mountpoint": mountpoint,
        },
        "sacrificialMember": "/dev/sdc1",
        "mount": {
            "target": mountpoint,
            "source": "/dev/md7",
            "filesystem": "ext4",
            "readOnly": False,
            "sizeBytes": 8 * 1024**3,
            "availableBytes": 6 * 1024**3,
        },
        "authorization": {
            "schemaVersion": 1,
            "kind": "echo.storage-recovery-lab-authorization",
            "disposable": True,
            "candidateIndexId": candidate["indexId"],
            "arrayDevice": "/dev/md7",
            "mountpoint": mountpoint,
            "labVolumeId": str(uuid.UUID(int=11, version=4)),
        },
        "evidenceDirectory": str(evidence_directory.resolve()),
        "nasTransfer": {
            "baseUrl": "http://127.0.0.1:8000",
            "path": "lab/recycle-probe.bin",
            "bytes": 1024**3,
        },
        "baselineBootId": str(uuid.UUID(int=12, version=4)),
        "phases": list(capture.STORAGE_LAB_PHASES),
    }
    plan_id = _digest((json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode())
    payload["planId"] = plan_id
    payload["confirmations"] = {
        phase: f"RUN ECHO STORAGE RECOVERY LAB {phase} {plan_id}"
        for phase in capture.STORAGE_LAB_PHASES
    }
    path = candidate_path.parent / "storage-recovery-lab-plan.json"
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _write_storage_lab_evidence(directory: Path, plan_id: str) -> None:
    seed = {"sha256": "f" * 64, "size": 64 * 1024**2, "fileCount": 2}
    details = {
        "storage-baseline.log": {
            "smartHealthy": True,
            "smartDiskCount": 2,
            "arrayHealthy": True,
            "activeMembers": 2,
            "seed": seed,
        },
        "storage-degraded.log": {
            "memberDisconnected": True,
            "raidDegraded": True,
            "activeMembers": 1,
            "dataReadable": True,
        },
        "storage-readonly.log": {
            "readOnlyObserved": True,
            "writeRejected": True,
            "readWriteRestored": True,
        },
        "storage-volume-full.log": {
            "enospcObserved": True,
            "rejectedWrite": True,
            "cleanupRecovered": True,
            "allocatedBytes": 5 * 1024**3,
        },
        "storage-reconnect.log": {"sameMemberReconnected": True, "rebuildStarted": True},
        "storage-rebuild.log": {
            "raidRebuildCompleted": True,
            "activeMembers": 2,
            "dataPreserved": True,
            "seed": seed,
        },
        "storage-reboot.log": {
            "bootIdChanged": True,
            "arrayHealthy": True,
            "mountedReadWrite": True,
            "dataPreserved": True,
        },
        "storage-recycle-restore.log": {
            "bytes": 1024**3,
            "sha256": "1" * 64,
            "restoreVerified": True,
            "finalState": "recoverable-trash",
        },
    }
    for name, phase_details in details.items():
        payload = {
            "schemaVersion": 1,
            "kind": "echo.storage-recovery-physical-lab-evidence",
            "planId": plan_id,
            "evidence": name,
            "passed": True,
            "details": phase_details,
        }
        path = directory / name
        path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
        path.chmod(0o444)


def test_prints_the_exact_candidate_bound_gate_marker(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    candidate_path, candidate = _candidate(tmp_path)
    gate = capture.PHYSICAL_GATES[0]

    exit_code = capture.main(["marker", "--candidate-index", str(candidate_path), "--gate", gate])

    assert exit_code == 0
    output = capsys.readouterr().out.strip()
    assert f"gate={gate}" in output
    assert f"candidate={candidate['indexId']}" in output
    assert f"os={OS_COMMIT}" in output
    assert f"agent={AGENT_COMMIT}" in output


def test_plan_command_writes_one_deterministic_read_only_candidate_bound_six_gate_plan(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    candidate_path, candidate = _candidate(tmp_path)
    output = tmp_path / capture.LAB_PLAN_NAME

    exit_code = capture.main(
        ["plan", "--candidate-index", str(candidate_path), "--output", str(output)]
    )

    assert exit_code == 0
    first_raw = output.read_bytes()
    plan = json.loads(first_raw)
    assert plan["schemaVersion"] == 17
    assert plan["candidate"] == {
        "indexId": candidate["indexId"],
        "osRepository": "dengdenghua/echo-os",
        "sourceRevision": OS_COMMIT,
        "agentRepository": "dengdenghua/echo-agent",
        "agentRevision": AGENT_COMMIT,
        "releaseTag": RELEASE_TAG,
        "immutableReference": f"ghcr.io/echo-os/echo-os@sha256:{'6' * 64}",
        "operationsArtifactId": "7" * 16,
        "operationsArchiveSha256": "8" * 64,
    }
    assert [gate["gate"] for gate in plan["gates"]] == list(capture.PHYSICAL_GATES)
    assert plan["deliveryRequirements"] == list(capture.DELIVERY_REQUIREMENTS)
    assert plan["physicalAcceptanceComplete"] is False
    assert plan["nasProductDeliveryReady"] is False
    assert output.stat().st_mode & 0o777 == 0o444
    for gate in plan["gates"]:
        requirement = capture.GATE_REQUIREMENTS[gate["gate"]]
        assert gate["profileClass"] == requirement["profileClass"]
        assert gate["deliveryRequirements"] == list(requirement["deliveryRequirements"])
        assert gate["requiredArchitecture"] == requirement["architecture"]
        assert gate["minimumDevices"] == requirement["minimumDevices"]
        assert gate["minimumDurationSeconds"] == requirement["minimumDurationSeconds"]
        assert gate["successMarker"] == capture._expected_marker(gate["gate"], plan["candidate"])
        assert gate["successCriteria"] == requirement["suffix"].split()
        assert gate["gateResult"] == capture.GATE_RESULT_NAME
        assert gate["operationsSystemdLifecycle"] == (
            capture.OPERATIONS_SYSTEMD_LIFECYCLE_NAME
            if gate["gate"] == capture.OPERATIONS_SYSTEMD_GATE
            else None
        )
        assert gate["operationsSystemdLab"] == (
            "operations_systemd_lab.py plan|run"
            if gate["gate"] == capture.OPERATIONS_SYSTEMD_GATE
            else None
        )
        assert gate["powerStateLifecycle"] == (
            capture.POWER_STATE_LIFECYCLE_NAME
            if gate["gate"] == capture.OPERATIONS_SYSTEMD_GATE
            else None
        )
        assert gate["powerStateRecoveryLab"] == (
            "power_state_recovery_lab.py seed|plan|run|verify"
            if gate["gate"] == capture.OPERATIONS_SYSTEMD_GATE
            else None
        )
        assert gate["storageRecoveryLifecycle"] == (
            capture.STORAGE_RECOVERY_LIFECYCLE_NAME
            if gate["gate"] == capture.STORAGE_RECOVERY_GATE
            else None
        )
        assert gate["storageRecoveryLab"] == (
            "storage_recovery_lab.py plan|run"
            if gate["gate"] == capture.STORAGE_RECOVERY_GATE
            else None
        )
        assert gate["protocolInteroperabilityLifecycle"] == (
            capture.PROTOCOL_INTEROPERABILITY_LIFECYCLE_NAME
            if gate["gate"] == capture.PROTOCOL_INTEROPERABILITY_GATE
            else None
        )
        assert gate["protocolInteroperabilityLab"] == (
            "protocol_interoperability_lab.py plan|probe|permissions|quota|large-file|verify"
            if gate["gate"] == capture.PROTOCOL_INTEROPERABILITY_GATE
            else None
        )
        assert gate["deviceEnduranceLifecycle"] == (
            capture.DEVICE_ENDURANCE_LIFECYCLE_NAME
            if gate["gate"] in capture.DEVICE_ENDURANCE_GATES
            else None
        )
        assert gate["deviceEnduranceLab"] == (
            "device_endurance_lab.py plan|run"
            if gate["gate"] in capture.DEVICE_ENDURANCE_GATES
            else None
        )
        assert gate["hubLifecyclePlan"] == (
            capture.HUB_LIFECYCLE_PLAN_NAME
            if gate["gate"] in capture.DEVICE_ENDURANCE_GATES
            else None
        )
        assert gate["hubLifecycleResult"] == (
            capture.HUB_LIFECYCLE_RESULT_NAME
            if gate["gate"] in capture.DEVICE_ENDURANCE_GATES
            else None
        )
        assert gate["hubLifecycleLab"] == (
            "hub_lifecycle_lab.py plan|run|verify"
            if gate["gate"] in capture.DEVICE_ENDURANCE_GATES
            else None
        )
        assert gate["paperlessFunctionalPlan"] == (
            capture.PAPERLESS_FUNCTIONAL_PLAN_NAME
            if gate["gate"] in capture.DEVICE_ENDURANCE_GATES
            else None
        )
        assert gate["paperlessFunctionalResult"] == (
            capture.PAPERLESS_FUNCTIONAL_RESULT_NAME
            if gate["gate"] in capture.DEVICE_ENDURANCE_GATES
            else None
        )
        assert gate["paperlessFunctionalLab"] == (
            "paperless_functional_lab.py plan|run|verify"
            if gate["gate"] in capture.DEVICE_ENDURANCE_GATES
            else None
        )
        assert gate["lanDiscoveryFunctionalPlan"] == (
            capture.LAN_DISCOVERY_FUNCTIONAL_PLAN_NAME
            if gate["gate"] in capture.DEVICE_ENDURANCE_GATES
            else None
        )
        assert gate["lanDiscoveryFunctionalResult"] == (
            capture.LAN_DISCOVERY_FUNCTIONAL_RESULT_NAME
            if gate["gate"] in capture.DEVICE_ENDURANCE_GATES
            else None
        )
        assert gate["lanDiscoveryFunctionalLab"] == (
            "lan_discovery_functional_lab.py plan|credentials|syncthing|home-assistant|verify"
            if gate["gate"] in capture.DEVICE_ENDURANCE_GATES
            else None
        )
        assert gate["bareMetalRecoveryLifecycle"] == (
            capture.BARE_METAL_LIFECYCLE_NAME if gate["gate"] == capture.BARE_METAL_GATE else None
        )
        assert gate["bareMetalRecoveryLab"] == (
            "bare_metal_recovery_lab.py plan|run|verify"
            if gate["gate"] == capture.BARE_METAL_GATE
            else None
        )
        assert gate["requiredResultChecks"] == list(requirement["resultChecks"])
    unsigned = dict(plan)
    plan_id = unsigned.pop("planId")
    assert plan_id == _digest(json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode())
    assert "ECHO_PHYSICAL_LAB_PLAN_READY" in capsys.readouterr().out

    second = tmp_path / "second"
    second.mkdir()
    second_output = second / capture.LAB_PLAN_NAME
    capture.build_lab_plan(candidate_index=candidate_path, output=second_output)
    assert second_output.read_bytes() == first_raw


def test_plan_refuses_to_replace_an_existing_output(tmp_path: Path) -> None:
    candidate_path, _candidate_value = _candidate(tmp_path)
    output = tmp_path / capture.LAB_PLAN_NAME
    output.write_text("existing\n", encoding="utf-8")

    with pytest.raises(capture.PhysicalAcceptanceError, match="new path"):
        capture.build_lab_plan(candidate_index=candidate_path, output=output)


def test_verify_plan_rejects_self_reindexed_tampering_or_another_candidate(
    tmp_path: Path,
) -> None:
    candidate_path, _candidate_value = _candidate(tmp_path)
    output = tmp_path / capture.LAB_PLAN_NAME
    capture.build_lab_plan(candidate_index=candidate_path, output=output)
    assert capture.verify_lab_plan(candidate_index=candidate_path, plan_path=output)["planId"]

    tampered = json.loads(output.read_text())
    tampered["gates"][0]["minimumDevices"] = 99
    unsigned = dict(tampered)
    unsigned.pop("planId")
    tampered["planId"] = _digest(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
    )
    output.chmod(0o644)
    output.write_text(json.dumps(tampered) + "\n", encoding="utf-8")
    with pytest.raises(capture.PhysicalAcceptanceError, match="differs from its candidate"):
        capture.verify_lab_plan(candidate_index=candidate_path, plan_path=output)

    other_directory = tmp_path / "other"
    other_directory.mkdir()
    other_candidate_path, other_candidate = _candidate(other_directory)
    other_candidate["source"]["agentCommit"] = "9" * 40
    other_candidate.pop("indexId")
    other_candidate["indexId"] = _digest(
        json.dumps(other_candidate, sort_keys=True, separators=(",", ":")).encode()
    )
    other_candidate_path.write_text(json.dumps(other_candidate), encoding="utf-8")
    other_plan = other_directory / capture.LAB_PLAN_NAME
    capture.build_lab_plan(candidate_index=other_candidate_path, output=other_plan)
    with pytest.raises(capture.PhysicalAcceptanceError, match="differs from its candidate"):
        capture.verify_lab_plan(candidate_index=candidate_path, plan_path=other_plan)


def test_plan_runs_from_the_flat_offline_candidate_tool_layout(tmp_path: Path) -> None:
    candidate_path, candidate = _candidate(tmp_path)
    tools = tmp_path / "candidate-tools"
    tools.mkdir()
    shutil.copy2(ROOT / "deploy/appliance/physical_acceptance.py", tools)
    shutil.copy2(ROOT / "deploy/appliance/physical_acceptance_capture.py", tools)
    shutil.copy2(ROOT / "deploy/appliance/release_evidence_index.py", tools)
    shutil.copy2(ROOT / "deploy/installer/verify_public_keyring.py", tools)
    copied_candidate = tools / candidate_path.name
    shutil.copy2(candidate_path, copied_candidate)
    output = tools / capture.LAB_PLAN_NAME

    completed = subprocess.run(
        [
            sys.executable,
            str(tools / "physical_acceptance_capture.py"),
            "plan",
            "--candidate-index",
            str(copied_candidate),
            "--output",
            str(output),
        ],
        cwd=tools,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(output.read_text())["candidate"]["indexId"] == candidate["indexId"]
    assert "ECHO_PHYSICAL_LAB_PLAN_READY" in completed.stdout

    verified = subprocess.run(
        [
            sys.executable,
            str(tools / "physical_acceptance_capture.py"),
            "verify-plan",
            "--candidate-index",
            str(copied_candidate),
            "--plan",
            str(output),
        ],
        cwd=tools,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert verified.returncode == 0, verified.stderr
    assert "ECHO_PHYSICAL_LAB_PLAN_OK" in verified.stdout


def test_profile_command_writes_one_strict_read_only_gate_bound_template(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = tmp_path / "gate"
    directory.mkdir()
    output = directory / capture.HARDWARE_PROFILE_NAME
    gate = "supported_arm64_hardware_install_and_cold_boot"

    exit_code = capture.main(
        [
            "profile",
            "--gate",
            gate,
            "--architecture",
            "arm64",
            "--device-count",
            "1",
            "--output",
            str(output),
        ]
    )

    assert exit_code == 0
    assert json.loads(output.read_text()) == {
        "schemaVersion": 1,
        "kind": "echo.physical-hardware-profile",
        "gate": gate,
        "profileClass": "supported-arm64-nas",
        "architecture": "arm64",
        "deviceCount": 1,
        "serialsRedacted": True,
    }
    assert output.stat().st_mode & 0o777 == 0o444
    assert "ECHO_PHYSICAL_HARDWARE_PROFILE_READY" in capsys.readouterr().out


def test_result_command_writes_one_strict_read_only_gate_checklist(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = tmp_path / "gate"
    directory.mkdir()
    output = directory / capture.GATE_RESULT_NAME
    gate = "power_loss_during_update_and_state_restore"

    arguments = ["result", "--gate", gate]
    for check in capture.GATE_REQUIREMENTS[gate]["resultChecks"]:
        arguments.extend(("--pass-check", check))
    arguments.extend(("--output", str(output)))
    exit_code = capture.main(arguments)

    assert exit_code == 0
    assert json.loads(output.read_text()) == capture._gate_result_payload(gate)
    assert output.stat().st_mode & 0o777 == 0o444
    assert "ECHO_PHYSICAL_GATE_RESULT_READY" in capsys.readouterr().out


def test_result_command_rejects_missing_duplicate_or_cross_gate_checks(tmp_path: Path) -> None:
    gate = "power_loss_during_update_and_state_restore"
    required = list(capture.GATE_REQUIREMENTS[gate]["resultChecks"])
    output = tmp_path / capture.GATE_RESULT_NAME

    for checks in (
        required[:-1],
        [*required, required[-1]],
        [*required[:-1], "windowsSmbReadWrite"],
    ):
        with pytest.raises(capture.PhysicalAcceptanceError, match="every required check"):
            capture.build_gate_result(gate=gate, passed_checks=checks, output=output)
        assert not output.exists()


def test_lifecycle_plan_requires_real_systemd_install_trigger_failure_and_removal_checks(
    tmp_path: Path,
) -> None:
    candidate_path, _candidate_value = _candidate(tmp_path)
    plan = capture._lab_plan(capture._candidate(candidate_path))
    lifecycle = next(
        gate
        for gate in plan["gates"]
        if gate["gate"] == "power_loss_during_update_and_state_restore"
    )

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
    } <= set(lifecycle["requiredResultChecks"])


def test_operations_result_binds_every_lifecycle_check_to_existing_artifact_bytes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gate = capture.OPERATIONS_SYSTEMD_GATE
    candidate_path, primary, _profile, _candidate_value = _gate_files(tmp_path, gate)
    output = primary.parent / capture.OPERATIONS_SYSTEMD_LIFECYCLE_NAME
    output.unlink()
    lab_plan = _operations_lab_plan(candidate_path, primary.parent)
    arguments = [
        "operations-result",
        "--gate",
        gate,
        "--candidate-index",
        str(candidate_path),
        "--lab-plan",
        str(lab_plan),
    ]
    for check in capture.OPERATIONS_SYSTEMD_LIFECYCLE_CHECKS:
        arguments.extend(("--evidence", f"{check}={primary}"))
    arguments.extend(("--output", str(output)))

    exit_code = capture.main(arguments)

    assert exit_code == 0
    value = json.loads(output.read_text())
    assert set(value["checks"]) == set(capture.OPERATIONS_SYSTEMD_LIFECYCLE_CHECKS)
    assert value["allPassed"] is True
    for item in value["checks"].values():
        assert item == {
            "passed": True,
            "evidence": {
                "name": primary.name,
                "sha256": _digest(primary.read_bytes()),
                "size": primary.stat().st_size,
            },
        }
    assert output.stat().st_mode & 0o777 == 0o444
    assert "ECHO_OPERATIONS_SYSTEMD_LIFECYCLE_READY" in capsys.readouterr().out


def test_operations_result_rejects_missing_duplicate_cross_gate_or_outside_evidence(
    tmp_path: Path,
) -> None:
    gate = capture.OPERATIONS_SYSTEMD_GATE
    candidate_path, primary, _profile, _candidate_value = _gate_files(tmp_path, gate)
    output = primary.parent / capture.OPERATIONS_SYSTEMD_LIFECYCLE_NAME
    output.unlink()
    required = list(capture.OPERATIONS_SYSTEMD_LIFECYCLE_CHECKS)
    outside = tmp_path / "outside.log"
    outside.write_text("outside evidence\n", encoding="utf-8")
    lab_plan = _operations_lab_plan(candidate_path, primary.parent)

    cases = (
        [f"{check}={primary}" for check in required[:-1]],
        [*(f"{check}={primary}" for check in required), f"{required[-1]}={primary}"],
        [
            *(f"{check}={primary}" for check in required[:-1]),
            f"windowsSmbReadWrite={primary}",
        ],
        [
            *(f"{check}={primary}" for check in required[:-1]),
            f"{required[-1]}={outside}",
        ],
    )
    for evidence_arguments in cases:
        with pytest.raises(capture.PhysicalAcceptanceError):
            capture.build_operations_systemd_lifecycle(
                candidate_index=candidate_path,
                lab_plan=lab_plan,
                gate=gate,
                evidence_arguments=evidence_arguments,
                output=output,
            )
        assert not output.exists()


def test_power_result_binds_all_seven_phases_and_rejects_clean_shutdown_evidence(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    gate = capture.OPERATIONS_SYSTEMD_GATE
    candidate_path, primary, _profile, candidate = _gate_files(tmp_path, gate)
    output = primary.parent / capture.POWER_STATE_LIFECYCLE_NAME
    output.unlink()
    plan_path, plan_id = _power_state_lab_plan(candidate_path, primary.parent)
    logs, _lifecycle = _power_state_material(candidate, plan_id=plan_id)
    for name, raw in logs.items():
        path = primary.parent / name
        path.write_bytes(raw)
        path.chmod(0o444)

    exit_code = capture.main(
        [
            "power-result",
            "--gate",
            gate,
            "--candidate-index",
            str(candidate_path),
            "--lab-plan",
            str(plan_path),
            "--lab-directory",
            str(primary.parent.resolve()),
            "--output",
            str(output),
        ]
    )

    assert exit_code == 0
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["labPlanId"] == plan_id
    assert set(result["checks"]) == set(capture.POWER_STATE_LIFECYCLE_CHECKS)
    assert set(result["phases"]) == set(capture.POWER_STATE_PHASES)
    assert output.stat().st_mode & 0o777 == 0o444
    assert "ECHO_POWER_STATE_LIFECYCLE_READY" in capsys.readouterr().out

    output.unlink()
    recovered = primary.parent / capture.POWER_STATE_PHASE_EVIDENCE_NAMES["recover-power-cut"]
    value = json.loads(recovered.read_text(encoding="utf-8"))
    value["details"]["journal"]["cleanShutdownFound"] = True
    recovered.chmod(0o600)
    recovered.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    recovered.chmod(0o444)
    with pytest.raises(capture.PhysicalAcceptanceError, match="details are invalid"):
        capture.build_power_state_lifecycle(
            candidate_index=candidate_path,
            lab_plan=plan_path,
            gate=gate,
            evidence_directory=primary.parent,
            output=output,
        )


def test_bare_metal_result_and_manifest_bind_all_eight_destructive_phases(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    gate = capture.BARE_METAL_GATE
    candidate_path, primary, profile, candidate = _gate_files(tmp_path, gate)
    context, _details, _logs = _bare_metal_material(candidate, plan_id="0" * 64)
    plan_path, plan_id = _bare_metal_lab_plan(candidate_path, primary.parent, context)
    _context, _details, logs = _bare_metal_material(candidate, plan_id=plan_id)
    for name, raw in logs.items():
        path = primary.parent / name
        path.write_bytes(raw)
        path.chmod(0o444)
    lifecycle = primary.parent / capture.BARE_METAL_LIFECYCLE_NAME

    exit_code = capture.main(
        [
            "bare-metal-result",
            "--gate",
            gate,
            "--candidate-index",
            str(candidate_path),
            "--lab-plan",
            str(plan_path),
            "--lab-directory",
            str(primary.parent.resolve()),
            "--output",
            str(lifecycle),
        ]
    )

    assert exit_code == 0
    value = json.loads(lifecycle.read_text(encoding="utf-8"))
    assert value["labPlanId"] == plan_id
    assert set(value["checks"]) == set(capture.BARE_METAL_LIFECYCLE_CHECKS)
    assert set(value["phases"]) == set(capture.BARE_METAL_PHASES)
    assert lifecycle.stat().st_mode & 0o777 == 0o444
    assert "ECHO_BARE_METAL_RECOVERY_LIFECYCLE_READY" in capsys.readouterr().out

    manifest = capture.build_manifest(
        candidate_index=candidate_path,
        gate=gate,
        architecture="x86_64",
        hardware_profile_sha256=_digest(profile.read_bytes()),
        device_count=1,
        lab_run_id=str(uuid.UUID(int=19, version=4)),
        started_at="2026-08-26T01:00:00Z",
        finished_at="2026-08-27T02:00:00Z",
        primary_log=primary,
        artifacts=_gate_artifacts(primary, profile),
        output=primary.parent / "evidence.json",
    )
    records = {record["name"]: record for record in manifest["artifacts"]}
    assert records[capture.BARE_METAL_LIFECYCLE_NAME]["sha256"] == _digest(lifecycle.read_bytes())


def test_bare_metal_result_rejects_a_forged_final_nas_canary(tmp_path: Path) -> None:
    gate = capture.BARE_METAL_GATE
    candidate_path, primary, _profile, candidate = _gate_files(tmp_path, gate)
    context, _details, _logs = _bare_metal_material(candidate, plan_id="0" * 64)
    plan_path, plan_id = _bare_metal_lab_plan(candidate_path, primary.parent, context)
    _context, _details, logs = _bare_metal_material(candidate, plan_id=plan_id)
    final_name = capture.BARE_METAL_PHASE_EVIDENCE_NAMES["final-verify"]
    final = json.loads(logs[final_name])
    final["details"]["canaries"]["nas"]["sha256"] = "0" * 64
    logs[final_name] = (json.dumps(final, sort_keys=True, separators=(",", ":")) + "\n").encode()
    for name, raw in logs.items():
        path = primary.parent / name
        path.write_bytes(raw)
        path.chmod(0o444)

    with pytest.raises(capture.PhysicalAcceptanceError, match="final-verify"):
        capture.build_bare_metal_lifecycle(
            candidate_index=candidate_path,
            lab_plan=plan_path,
            gate=gate,
            evidence_directory=primary.parent,
            output=primary.parent / capture.BARE_METAL_LIFECYCLE_NAME,
        )


def test_build_requires_the_bare_metal_lifecycle_for_the_g6_recovery_gate(
    tmp_path: Path,
) -> None:
    gate = capture.BARE_METAL_GATE
    candidate_path, primary, profile, _candidate_value = _gate_files(tmp_path, gate)

    with pytest.raises(capture.PhysicalAcceptanceError, match="bare-metal-recovery-lifecycle"):
        capture.build_manifest(
            candidate_index=candidate_path,
            gate=gate,
            architecture="x86_64",
            hardware_profile_sha256=_digest(profile.read_bytes()),
            device_count=1,
            lab_run_id=str(uuid.UUID(int=20, version=4)),
            started_at="2026-08-26T01:00:00Z",
            finished_at="2026-08-27T02:00:00Z",
            primary_log=primary,
            artifacts=_gate_artifacts(primary, profile),
            output=primary.parent / "evidence.json",
        )


def test_bare_metal_result_binds_source_backup_and_seven_destructive_phases(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    candidate_path, primary, _profile, _candidate_value = _gate_files(
        tmp_path, capture.BARE_METAL_GATE
    )
    plan_path, plan_id = _bare_metal_lab_plan(candidate_path, primary.parent)
    output = primary.parent / capture.BARE_METAL_LIFECYCLE_NAME

    exit_code = capture.main(
        [
            "bare-metal-result",
            "--gate",
            capture.BARE_METAL_GATE,
            "--candidate-index",
            str(candidate_path),
            "--lab-plan",
            str(plan_path),
            "--lab-directory",
            str(primary.parent.resolve()),
            "--output",
            str(output),
        ]
    )

    assert exit_code == 0
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["labPlanId"] == plan_id
    assert set(result["checks"]) == set(capture.BARE_METAL_LIFECYCLE_CHECKS)
    assert set(result["phases"]) == set(capture.BARE_METAL_PHASES)
    assert output.stat().st_mode & 0o777 == 0o444
    assert "ECHO_BARE_METAL_RECOVERY_LIFECYCLE_READY" in capsys.readouterr().out

    output.unlink()
    final_path = primary.parent / capture.BARE_METAL_PHASE_EVIDENCE_NAMES["final-verify"]
    final = json.loads(final_path.read_text(encoding="utf-8"))
    final["details"]["nasDataVerified"] = False
    final_path.chmod(0o600)
    final_path.write_text(json.dumps(final, sort_keys=True) + "\n", encoding="utf-8")
    final_path.chmod(0o444)
    with pytest.raises(capture.PhysicalAcceptanceError, match="final-verify"):
        capture.build_bare_metal_lifecycle(
            candidate_index=candidate_path,
            lab_plan=plan_path,
            gate=capture.BARE_METAL_GATE,
            evidence_directory=primary.parent,
            output=output,
        )


def test_operations_result_consumes_the_fixed_lab_executor_outputs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gate = capture.OPERATIONS_SYSTEMD_GATE
    candidate_path, primary, _profile, _candidate_value = _gate_files(tmp_path, gate)
    output = primary.parent / capture.OPERATIONS_SYSTEMD_LIFECYCLE_NAME
    output.unlink()
    lab_plan = _operations_lab_plan(candidate_path, primary.parent)
    plan_id = json.loads(lab_plan.read_text())["planId"]
    for name in set(capture.OPERATIONS_LAB_EVIDENCE.values()):
        if name == "operations-install-rollback.log":
            details: dict[str, Any] = {"baselineRestored": True}
        elif name == "operations-install.log":
            details = {
                "installed": True,
                "installedAtNs": 1,
                "platform": {"id": "debian", "versionId": "13", "omvVersion": "8.7.3-1"},
                "timerTriggers": {},
                "unitState": {},
            }
        elif name in {"backup-timer.log", "audit-timer.log"}:
            details = {
                "timer": "echo-test.timer",
                "lastTriggerChanged": True,
                "serviceResult": "success",
                "product": {"name": "verified.echo-test", "sha256": "a" * 64, "size": 1},
            }
        elif name in {"backup-mount-loss.log", "audit-mount-loss.log"}:
            details = {
                "service": "echo-test.service",
                "failedReturnCode": 1,
                "fallbackWriteAbsent": True,
                "mountRestored": True,
            }
        elif name == "operations-remove-rollback.log":
            details = {"installedStateRestored": True}
        else:
            details = {
                "removed": True,
                "unitsAndTimersAbsent": True,
                "credentials": {
                    "backup": {"sha256": "d" * 64},
                    "audit": {"sha256": "e" * 64},
                },
                "preserved": {
                    label: {"sha256": "b" * 64, "size": 1}
                    for label in capture.OPERATIONS_LAB_PRESERVED
                },
            }
        path = primary.parent / name
        path.write_text(
            json.dumps(
                {
                    "schemaVersion": 2,
                    "kind": "echo.operations-systemd-physical-lab-evidence",
                    "planId": plan_id,
                    "evidence": name,
                    "passed": True,
                    "details": details,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        path.chmod(0o444)

    exit_code = capture.main(
        [
            "operations-result",
            "--gate",
            gate,
            "--candidate-index",
            str(candidate_path),
            "--lab-plan",
            str(lab_plan),
            "--lab-directory",
            str(primary.parent.resolve()),
            "--output",
            str(output),
        ]
    )

    assert exit_code == 0
    result = json.loads(output.read_text())
    assert {
        check: item["evidence"]["name"] for check, item in result["checks"].items()
    } == capture.OPERATIONS_LAB_EVIDENCE
    assert "ECHO_OPERATIONS_SYSTEMD_LIFECYCLE_READY" in capsys.readouterr().out


def test_operations_result_rejects_a_self_reindexed_plan_for_another_candidate(
    tmp_path: Path,
) -> None:
    candidate_path, primary, _profile, _candidate_value = _gate_files(
        tmp_path, capture.OPERATIONS_SYSTEMD_GATE
    )
    plan_path = _operations_lab_plan(candidate_path, primary.parent)
    plan = json.loads(plan_path.read_text())
    plan["releaseCandidate"]["agentRevision"] = "f" * 40
    unsigned = dict(plan)
    unsigned.pop("confirmations")
    unsigned.pop("planId")
    forged_id = _digest(
        (json.dumps(unsigned, sort_keys=True, separators=(",", ":")) + "\n").encode()
    )
    plan["planId"] = forged_id
    plan["confirmations"] = {
        phase: f"RUN ECHO OPERATIONS LAB {phase} {forged_id}"
        for phase in capture.OPERATIONS_LAB_PHASES
    }
    plan_path.write_text(json.dumps(plan, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(capture.PhysicalAcceptanceError, match="bound to this candidate"):
        capture.operations_lab_evidence_arguments(
            primary.parent.resolve(),
            candidate_index=candidate_path,
            lab_plan=plan_path,
        )


def test_storage_result_binds_all_eight_machine_phases_to_the_g2_candidate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    candidate_path, primary, _profile, _candidate_value = _gate_files(
        tmp_path, capture.STORAGE_RECOVERY_GATE
    )
    for name in {*capture.STORAGE_LAB_EVIDENCE.values(), "storage-reconnect.log"}:
        (primary.parent / name).unlink()
    (primary.parent / capture.STORAGE_RECOVERY_LIFECYCLE_NAME).unlink()
    plan_path = _storage_lab_plan(candidate_path, primary.parent)
    plan = json.loads(plan_path.read_text())
    _write_storage_lab_evidence(primary.parent, plan["planId"])
    output = primary.parent / capture.STORAGE_RECOVERY_LIFECYCLE_NAME

    arguments = capture.storage_lab_evidence_arguments(
        primary.parent.resolve(),
        candidate_index=candidate_path,
        lab_plan=plan_path,
    )
    result = capture.build_storage_recovery_lifecycle(
        candidate_index=candidate_path,
        lab_plan=plan_path,
        gate=capture.STORAGE_RECOVERY_GATE,
        evidence_arguments=arguments,
        output=output,
    )

    assert result["labPlanId"] == plan["planId"]
    assert set(result["checks"]) == set(capture.STORAGE_RECOVERY_LIFECYCLE_CHECKS)
    assert result["checks"]["diskDisconnectObserved"]["evidence"]["name"] == (
        "storage-degraded.log"
    )
    assert output.stat().st_mode & 0o777 == 0o444

    output.unlink()
    exit_code = capture.main(
        [
            "storage-result",
            "--gate",
            capture.STORAGE_RECOVERY_GATE,
            "--candidate-index",
            str(candidate_path),
            "--lab-plan",
            str(plan_path),
            "--lab-directory",
            str(primary.parent.resolve()),
            "--output",
            str(output),
        ]
    )
    assert exit_code == 0
    assert "ECHO_STORAGE_RECOVERY_LIFECYCLE_READY" in capsys.readouterr().out


def test_storage_result_rejects_handwritten_or_wrong_plan_evidence(tmp_path: Path) -> None:
    candidate_path, primary, _profile, _candidate_value = _gate_files(
        tmp_path, capture.STORAGE_RECOVERY_GATE
    )
    for name in {*capture.STORAGE_LAB_EVIDENCE.values(), "storage-reconnect.log"}:
        (primary.parent / name).unlink()
    (primary.parent / capture.STORAGE_RECOVERY_LIFECYCLE_NAME).unlink()
    plan_path = _storage_lab_plan(candidate_path, primary.parent)
    plan = json.loads(plan_path.read_text())
    _write_storage_lab_evidence(primary.parent, plan["planId"])
    degraded = primary.parent / "storage-degraded.log"
    value = json.loads(degraded.read_text())
    value["details"]["memberDisconnected"] = False
    degraded.chmod(0o644)
    degraded.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    degraded.chmod(0o444)

    with pytest.raises(capture.PhysicalAcceptanceError, match="details are invalid"):
        capture.storage_lab_evidence_arguments(
            primary.parent.resolve(),
            candidate_index=candidate_path,
            lab_plan=plan_path,
        )

    plan["releaseCandidate"]["sourceRevision"] = "0" * 40
    unsigned = dict(plan)
    unsigned.pop("confirmations")
    unsigned.pop("planId")
    forged_id = _digest(
        (json.dumps(unsigned, sort_keys=True, separators=(",", ":")) + "\n").encode()
    )
    plan["planId"] = forged_id
    plan["confirmations"] = {
        phase: f"RUN ECHO STORAGE RECOVERY LAB {phase} {forged_id}"
        for phase in capture.STORAGE_LAB_PHASES
    }
    plan_path.write_text(json.dumps(plan, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(capture.PhysicalAcceptanceError, match="bound to this candidate"):
        capture._validate_storage_lab_plan(
            plan_path,
            candidate=capture._candidate(candidate_path),
            evidence_directory=primary.parent.resolve(),
        )


def test_builds_a_sorted_read_only_manifest_from_existing_redacted_artifacts(
    tmp_path: Path,
) -> None:
    gate = "real_disk_smart_and_raid_degradation_recovery"
    candidate_path, primary, attachment, candidate = _gate_files(tmp_path, gate)
    output = primary.parent / "evidence.json"

    manifest = capture.build_manifest(
        candidate_index=candidate_path,
        gate=gate,
        architecture="x86_64",
        hardware_profile_sha256=_digest(attachment.read_bytes()),
        device_count=2,
        lab_run_id=str(uuid.UUID(int=7, version=4)),
        started_at="2026-08-26T01:00:00Z",
        finished_at="2026-08-27T02:00:00Z",
        primary_log=primary,
        artifacts=_gate_artifacts(primary, attachment),
        output=output,
    )

    assert manifest["candidate"]["indexId"] == candidate["indexId"]
    assert [record["name"] for record in manifest["artifacts"]] == sorted(
        {
            "acceptance.log",
            capture.GATE_RESULT_NAME,
            capture.HARDWARE_PROFILE_NAME,
            capture.STORAGE_RECOVERY_LIFECYCLE_NAME,
            *capture.STORAGE_LAB_EVIDENCE.values(),
            "storage-reconnect.log",
        }
    )
    assert json.loads(output.read_text()) == manifest
    assert output.stat().st_mode & 0o777 == 0o444


def test_device_manifest_rejects_rehashed_but_semantically_tampered_hub_result(
    tmp_path: Path,
) -> None:
    gate = "physical_x86_64_install_and_cold_boot"
    candidate_path, primary, profile, _candidate_value = _gate_files(tmp_path, gate)
    result_path = primary.parent / capture.HUB_LIFECYCLE_RESULT_NAME
    result = json.loads(result_path.read_text())
    network = result["reinstall"]["immich"]["installation"]["services"]["database"]["networks"][0]
    network["internal"] = not network["internal"]
    unsigned = dict(result)
    unsigned.pop("resultId")
    result["resultId"] = _digest(capture.hub_lab._canonical(unsigned))
    result_path.chmod(0o644)
    result_path.write_bytes(capture.hub_lab._canonical(result))
    result_path.chmod(0o444)

    with pytest.raises(capture.PhysicalAcceptanceError, match="network evidence"):
        capture.build_manifest(
            candidate_index=candidate_path,
            gate=gate,
            architecture="x86_64",
            hardware_profile_sha256=_digest(profile.read_bytes()),
            device_count=1,
            lab_run_id=str(uuid.UUID(int=17, version=4)),
            started_at="2026-08-26T01:00:00Z",
            finished_at="2026-08-27T02:00:00Z",
            primary_log=primary,
            artifacts=_gate_artifacts(primary, profile),
            output=primary.parent / "evidence.json",
        )


def test_device_manifest_rejects_rehashed_but_forged_paperless_result(
    tmp_path: Path,
) -> None:
    gate = "physical_x86_64_install_and_cold_boot"
    candidate_path, primary, profile, _candidate_value = _gate_files(tmp_path, gate)
    result_path = primary.parent / capture.PAPERLESS_FUNCTIONAL_RESULT_NAME
    result = json.loads(result_path.read_text())
    result["checks"]["pptxConvertedAndSearched"] = False
    unsigned = dict(result)
    unsigned.pop("resultId")
    result["resultId"] = _digest(capture.paperless_lab._canonical(unsigned))
    result_path.chmod(0o644)
    result_path.write_bytes(capture.paperless_lab._canonical(result))
    result_path.chmod(0o444)

    with pytest.raises(capture.PhysicalAcceptanceError, match="Paperless.*invalid"):
        capture.build_manifest(
            candidate_index=candidate_path,
            gate=gate,
            architecture="x86_64",
            hardware_profile_sha256=_digest(profile.read_bytes()),
            device_count=1,
            lab_run_id=str(uuid.UUID(int=18, version=4)),
            started_at="2026-08-26T01:00:00Z",
            finished_at="2026-08-27T02:00:00Z",
            primary_log=primary,
            artifacts=_gate_artifacts(primary, profile),
            output=primary.parent / "evidence.json",
        )


def test_rejects_an_artifact_outside_the_gate_directory(tmp_path: Path) -> None:
    gate = capture.PHYSICAL_GATES[0]
    candidate_path, primary, _attachment, _candidate_value = _gate_files(tmp_path, gate)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")

    with pytest.raises(capture.PhysicalAcceptanceError, match="unsafe or duplicated"):
        capture.build_manifest(
            candidate_index=candidate_path,
            gate=gate,
            architecture="x86_64",
            hardware_profile_sha256=_digest(_attachment.read_bytes()),
            device_count=1,
            lab_run_id=str(uuid.UUID(int=7, version=4)),
            started_at="2026-08-26T01:00:00Z",
            finished_at="2026-08-27T02:00:00Z",
            primary_log=primary,
            artifacts=_gate_artifacts(primary, _attachment, outside),
            output=primary.parent / "evidence.json",
        )


def test_rejects_missing_marker_or_sensitive_inventory(tmp_path: Path) -> None:
    gate = capture.PHYSICAL_GATES[0]
    candidate_path, primary, attachment, _candidate_value = _gate_files(tmp_path, gate)
    primary.write_text("no completion marker\n", encoding="utf-8")
    with pytest.raises(capture.PhysicalAcceptanceError, match="one exact success marker"):
        capture.build_manifest(
            candidate_index=candidate_path,
            gate=gate,
            architecture="x86_64",
            hardware_profile_sha256=_digest(attachment.read_bytes()),
            device_count=1,
            lab_run_id=str(uuid.UUID(int=7, version=4)),
            started_at="2026-08-26T01:00:00Z",
            finished_at="2026-08-27T02:00:00Z",
            primary_log=primary,
            artifacts=_gate_artifacts(primary, attachment),
            output=primary.parent / "evidence.json",
        )

    second = tmp_path / "sensitive"
    second.mkdir()
    candidate_path, primary, attachment, _candidate_value = _gate_files(second, gate)
    attachment.write_text("serial=ABC123\n", encoding="utf-8")
    with pytest.raises(capture.PhysicalAcceptanceError, match="sensitive data"):
        capture.build_manifest(
            candidate_index=candidate_path,
            gate=gate,
            architecture="x86_64",
            hardware_profile_sha256=_digest(attachment.read_bytes()),
            device_count=1,
            lab_run_id=str(uuid.UUID(int=7, version=4)),
            started_at="2026-08-26T01:00:00Z",
            finished_at="2026-08-27T02:00:00Z",
            primary_log=primary,
            artifacts=_gate_artifacts(primary, attachment),
            output=primary.parent / "evidence.json",
        )


def test_rejects_unbound_hardware_profile_or_unlisted_directory_file(tmp_path: Path) -> None:
    gate = capture.PHYSICAL_GATES[0]
    candidate_path, primary, profile, _candidate_value = _gate_files(tmp_path, gate)
    arguments = {
        "candidate_index": candidate_path,
        "gate": gate,
        "architecture": "x86_64",
        "hardware_profile_sha256": "f" * 64,
        "device_count": 1,
        "lab_run_id": str(uuid.UUID(int=7, version=4)),
        "started_at": "2026-08-26T01:00:00Z",
        "finished_at": "2026-08-27T02:00:00Z",
        "primary_log": primary,
        "artifacts": _gate_artifacts(primary, profile),
        "output": primary.parent / "evidence.json",
    }

    with pytest.raises(capture.PhysicalAcceptanceError, match="hardware profile digest"):
        capture.build_manifest(**arguments)

    (primary.parent / "unlisted.txt").write_text("not declared\n", encoding="utf-8")
    arguments["hardware_profile_sha256"] = _digest(profile.read_bytes())
    with pytest.raises(capture.PhysicalAcceptanceError, match="exactly the declared"):
        capture.build_manifest(**arguments)


def test_rejects_hardware_profile_content_that_drifts_from_build_arguments(tmp_path: Path) -> None:
    gate = capture.PHYSICAL_GATES[0]
    candidate_path, primary, profile, _candidate_value = _gate_files(tmp_path, gate)
    value = json.loads(profile.read_text())
    value["architecture"] = "arm64"
    profile.write_text(json.dumps(value) + "\n", encoding="utf-8")

    with pytest.raises(capture.PhysicalAcceptanceError, match="does not match"):
        capture.build_manifest(
            candidate_index=candidate_path,
            gate=gate,
            architecture="x86_64",
            hardware_profile_sha256=_digest(profile.read_bytes()),
            device_count=1,
            lab_run_id=str(uuid.UUID(int=7, version=4)),
            started_at="2026-08-26T01:00:00Z",
            finished_at="2026-08-27T02:00:00Z",
            primary_log=primary,
            artifacts=_gate_artifacts(primary, profile),
            output=primary.parent / "evidence.json",
        )


def test_device_result_binds_four_machine_phases_and_all_common_checks(tmp_path: Path) -> None:
    gate = "physical_x86_64_install_and_cold_boot"
    candidate_path, primary, _profile, candidate = _gate_files(tmp_path, gate)
    root = primary.parent
    lifecycle_path = root / capture.DEVICE_ENDURANCE_LIFECYCLE_NAME
    lifecycle_path.unlink()
    boot_a = "11111111-1111-4111-8111-111111111111"
    boot_b = "22222222-2222-4222-8222-222222222222"
    appliance = {
        "bundleVerified": True,
        "administratorLoginReady": True,
        "fileLifecycleVerified": True,
        "familyMemberIsolationVerified": True,
        "familyIdentitySetSha256": "1" * 64,
        "familyPolicySetSha256": "2" * 64,
        "agentWorkbenchVerified": True,
        "oneGiBTransferVerified": True,
        "containerRestartResumeVerified": True,
        "dockerControlApprovalVerified": True,
        "runtimeArchitecture": "amd64",
        "transferSha256": "d" * 64,
    }
    details = {
        "device-baseline.log": {
            "installerCompleted": True,
            "installerSha256": "e" * 64,
            "postWriteReadbackVerified": True,
            "firstColdBootHealthy": True,
            "bootId": boot_a,
            "observedAtNs": 1_800_000_000_000_000_000,
            "deviceIdentitySha256": "f" * 64,
            "appliance": appliance,
        },
        "device-soak.log": {
            "continuousRunStable": True,
            "sameBoot": True,
            "durationSeconds": 86400,
            "bootId": boot_a,
            "observedAtNs": 1_800_086_400_000_000_000,
            "appliance": appliance,
        },
        "device-power-cut-armed.log": {
            "physicalPowerCutArmed": True,
            "bootId": boot_a,
            "intentSha256": "a" * 64,
            "observedAtNs": 1_800_086_400_000_000_001,
            "nextAction": "physically-remove-and-restore-power",
        },
        "device-recovered.log": {
            "hardPowerCycleRecovered": True,
            "bootIdChanged": True,
            "previousBootId": boot_a,
            "currentBootId": boot_b,
            "uncleanShutdownVerified": True,
            "observedAtNs": 1_800_086_400_000_000_002,
            "journal": {
                "persistentJournalAvailable": True,
                "powerCutIntentFound": True,
                "cleanShutdownFound": False,
            },
            "appliance": appliance,
        },
    }
    plan_id = ""
    plan = {
        "schemaVersion": 1,
        "kind": "echo.device-endurance-physical-lab-plan",
        "gate": gate,
        "releaseCandidate": {
            "indexPath": str(candidate_path.resolve()),
            "indexId": candidate["indexId"],
            "indexSha256": _digest(candidate_path.read_bytes()),
            "osRepository": "dengdenghua/echo-os",
            "sourceRevision": OS_COMMIT,
            "agentRepository": "dengdenghua/echo-agent",
            "agentRevision": AGENT_COMMIT,
            "releaseTag": RELEASE_TAG,
            "applianceManifestSha256": "5" * 64,
            "immutableReference": f"ghcr.io/echo-os/echo-os@sha256:{'6' * 64}",
            "operationsArtifactId": "7" * 16,
            "operationsArchiveSha256": "8" * 64,
        },
        "bundleRoot": "/root/echo-appliance-operations-7777777777777777",
        "operationsBundle": {
            "artifactId": "7" * 16,
            "archiveSha256": "8" * 64,
            "imageReference": f"ghcr.io/echo-os/echo-os@sha256:{'6' * 64}",
            "manifestSha256": "9" * 64,
            "deviceEnduranceLabSha256": "a" * 64,
            "runningVerifierSha256": "b" * 64,
        },
        "platform": {
            "id": "debian",
            "versionId": "13",
            "omvVersion": "8.7.3-1",
            "architecture": "x86_64",
        },
        "deviceIdentitySha256": "f" * 64,
        "installer": {
            "path": "/root/private-installer.log",
            "sha256": "e" * 64,
            "size": 512,
            "imageVersion": "1.0.0",
            "manifestSha256": "c" * 64,
            "sourceSha256": "d" * 64,
            "targetIdentitySha256": "e" * 64,
            "postWriteReadbackVerified": True,
            "dataProtection": "luks2-tpm2-signed-pcr11-recovery",
        },
        "evidenceDirectory": str(root.resolve()),
        "appliance": {
            "baseUrl": "https://127.0.0.1:8000",
            "mainContainer": "echo-os",
            "proxyContainer": "echo-docker-control",
            "expectedArchitecture": "amd64",
            "nasTransferPath": "lab/device",
            "nasTransferBytes": 1024 * 1024 * 1024,
            "familyIsolationFixture": {
                "path": "/root/echo-family-isolation.json",
                "sha256": "3" * 64,
                "size": 2048,
                "mode": "0400",
            },
        },
        "baselineBootId": boot_a,
        "firstBootUptimeSeconds": 60.0,
        "minimumSoakSeconds": 86400,
        "phases": ["baseline", "soak", "arm-power-cut", "recovered"],
    }
    plan_id = _digest((json.dumps(plan, sort_keys=True, separators=(",", ":")) + "\n").encode())
    plan["planId"] = plan_id
    plan["confirmations"] = {
        phase: f"RUN ECHO DEVICE ENDURANCE LAB {phase} {plan_id}" for phase in plan["phases"]
    }
    plan_path = tmp_path / "device-plan.json"
    plan_path.write_text(json.dumps(plan, sort_keys=True) + "\n", encoding="utf-8")
    for name, phase_details in details.items():
        (root / name).write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "kind": "echo.device-endurance-physical-lab-evidence",
                    "planId": plan_id,
                    "evidence": name,
                    "passed": True,
                    "details": phase_details,
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        (root / name).chmod(0o444)

    arguments = capture.device_lab_evidence_arguments(
        root.resolve(),
        candidate_index=candidate_path,
        lab_plan=plan_path,
        gate=gate,
    )
    result = capture.build_device_endurance_lifecycle(
        candidate_index=candidate_path,
        lab_plan=plan_path,
        gate=gate,
        evidence_arguments=arguments,
        output=lifecycle_path,
    )

    assert result["gate"] == gate
    assert result["labPlanId"] == plan_id
    assert set(result["checks"]) == set(capture.DEVICE_ENDURANCE_LIFECYCLE_CHECKS)
    assert lifecycle_path.stat().st_mode & 0o777 == 0o444


def test_build_rejects_false_or_incomplete_gate_result(tmp_path: Path) -> None:
    gate = "power_loss_during_update_and_state_restore"
    candidate_path, primary, profile, _candidate_value = _gate_files(tmp_path, gate)
    result_path = primary.parent / capture.GATE_RESULT_NAME
    result = json.loads(result_path.read_text())
    result["checks"]["missingBackupMountFailedClosed"] = False
    result["allPassed"] = False
    result_path.write_text(json.dumps(result) + "\n", encoding="utf-8")

    with pytest.raises(capture.PhysicalAcceptanceError, match="every required check"):
        capture.build_manifest(
            candidate_index=candidate_path,
            gate=gate,
            architecture="x86_64",
            hardware_profile_sha256=_digest(profile.read_bytes()),
            device_count=1,
            lab_run_id=str(uuid.UUID(int=7, version=4)),
            started_at="2026-08-26T01:00:00Z",
            finished_at="2026-08-27T02:00:00Z",
            primary_log=primary,
            artifacts=_gate_artifacts(primary, profile),
            output=primary.parent / "evidence.json",
        )


def test_build_requires_the_operations_lifecycle_artifact_for_g5(tmp_path: Path) -> None:
    gate = capture.OPERATIONS_SYSTEMD_GATE
    candidate_path, primary, profile, _candidate_value = _gate_files(tmp_path, gate)
    lifecycle = primary.parent / capture.OPERATIONS_SYSTEMD_LIFECYCLE_NAME
    lifecycle.unlink()

    with pytest.raises(capture.PhysicalAcceptanceError, match="must include operations"):
        capture.build_manifest(
            candidate_index=candidate_path,
            gate=gate,
            architecture="x86_64",
            hardware_profile_sha256=_digest(profile.read_bytes()),
            device_count=1,
            lab_run_id=str(uuid.UUID(int=7, version=4)),
            started_at="2026-08-26T01:00:00Z",
            finished_at="2026-08-27T02:00:00Z",
            primary_log=primary,
            artifacts=_gate_artifacts(primary, profile),
            output=primary.parent / "evidence.json",
        )


def test_build_requires_the_power_state_lifecycle_artifact_for_g5(tmp_path: Path) -> None:
    gate = capture.OPERATIONS_SYSTEMD_GATE
    candidate_path, primary, profile, _candidate_value = _gate_files(tmp_path, gate)
    lifecycle = primary.parent / capture.POWER_STATE_LIFECYCLE_NAME
    lifecycle.unlink()

    with pytest.raises(capture.PhysicalAcceptanceError, match="power-state-lifecycle"):
        capture.build_manifest(
            candidate_index=candidate_path,
            gate=gate,
            architecture="x86_64",
            hardware_profile_sha256=_digest(profile.read_bytes()),
            device_count=1,
            lab_run_id=str(uuid.UUID(int=7, version=4)),
            started_at="2026-08-26T01:00:00Z",
            finished_at="2026-08-27T02:00:00Z",
            primary_log=primary,
            artifacts=_gate_artifacts(primary, profile),
            output=primary.parent / "evidence.json",
        )


def test_x86_and_arm_continuous_run_gates_require_at_least_24_hours(tmp_path: Path) -> None:
    gate = capture.PHYSICAL_GATES[0]
    candidate_path, primary, profile, _candidate_value = _gate_files(tmp_path, gate)

    with pytest.raises(capture.PhysicalAcceptanceError, match="duration"):
        capture.build_manifest(
            candidate_index=candidate_path,
            gate=gate,
            architecture="x86_64",
            hardware_profile_sha256=_digest(profile.read_bytes()),
            device_count=1,
            lab_run_id=str(uuid.UUID(int=7, version=4)),
            started_at="2026-08-27T01:00:00Z",
            finished_at="2026-08-27T02:00:00Z",
            primary_log=primary,
            artifacts=_gate_artifacts(primary, profile),
            output=primary.parent / "evidence.json",
        )


def test_cli_builds_manifest_without_creating_a_signature(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gate = capture.PHYSICAL_GATES[1]
    candidate_path, primary, attachment, _candidate_value = _gate_files(tmp_path, gate)
    output = primary.parent / "evidence.json"
    artifact_arguments = [
        item
        for artifact in _gate_artifacts(primary, attachment)
        for item in ("--artifact", str(artifact))
    ]

    exit_code = capture.main(
        [
            "build",
            "--candidate-index",
            str(candidate_path),
            "--gate",
            gate,
            "--architecture",
            "arm64",
            "--hardware-profile-sha256",
            _digest(attachment.read_bytes()),
            "--device-count",
            "1",
            "--lab-run-id",
            str(uuid.UUID(int=8, version=4)),
            "--started-at",
            "2026-08-26T01:00:00Z",
            "--finished-at",
            "2026-08-27T02:00:00Z",
            "--primary-log",
            str(primary),
            *artifact_arguments,
            "--output",
            str(output),
        ]
    )

    assert exit_code == 0
    assert output.exists()
    assert not (output.parent / "evidence.json.gpg").exists()
    assert "ECHO_PHYSICAL_GATE_MANIFEST_READY" in capsys.readouterr().out
