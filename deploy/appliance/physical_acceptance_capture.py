#!/usr/bin/env python3
"""Build one bounded physical-gate manifest without signing private material."""

from __future__ import annotations

import argparse
import json
import re
import stat
import sys
import uuid
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

try:
    from deploy.appliance import hub_lifecycle_lab as hub_lab
    from deploy.appliance import lan_discovery_functional_lab as lan_discovery_lab
    from deploy.appliance import paperless_functional_lab as paperless_lab
    from deploy.appliance.physical_acceptance import (
        ARTIFACT_NAME,
        BARE_METAL_EVIDENCE_NAMES,
        BARE_METAL_GATE,
        BARE_METAL_LIFECYCLE_CHECKS,
        BARE_METAL_LIFECYCLE_NAME,
        BARE_METAL_PHASE_EVIDENCE_NAMES,
        BARE_METAL_PHASES,
        DELIVERY_REQUIREMENTS,
        DEVICE_ENDURANCE_EVIDENCE_NAMES,
        DEVICE_ENDURANCE_GATES,
        DEVICE_ENDURANCE_LIFECYCLE_CHECKS,
        DEVICE_ENDURANCE_LIFECYCLE_NAME,
        DEVICE_ENDURANCE_PHASE_EVIDENCE_NAMES,
        GATE_REQUIREMENTS,
        GATE_RESULT_NAME,
        HARDWARE_PROFILE_NAME,
        HUB_LIFECYCLE_PLAN_NAME,
        HUB_LIFECYCLE_RESULT_NAME,
        LAN_DISCOVERY_FUNCTIONAL_PLAN_NAME,
        LAN_DISCOVERY_FUNCTIONAL_RESULT_NAME,
        LAN_DISCOVERY_PROBE_NAMES,
        MAX_ARTIFACT_BYTES,
        MAX_MANIFEST_BYTES,
        MAX_TOTAL_GATE_BYTES,
        OPERATIONS_SYSTEMD_GATE,
        OPERATIONS_SYSTEMD_LIFECYCLE_CHECKS,
        OPERATIONS_SYSTEMD_LIFECYCLE_NAME,
        PAPERLESS_FUNCTIONAL_PLAN_NAME,
        PAPERLESS_FUNCTIONAL_RESULT_NAME,
        PHYSICAL_GATES,
        POWER_STATE_CANARY_BYTES,
        POWER_STATE_EVIDENCE_NAMES,
        POWER_STATE_LIFECYCLE_CHECKS,
        POWER_STATE_LIFECYCLE_NAME,
        POWER_STATE_NAS_CANARY_BYTES,
        POWER_STATE_PHASE_EVIDENCE_NAMES,
        POWER_STATE_PHASES,
        PROTOCOL_INTEROPERABILITY_GATE,
        PROTOCOL_INTEROPERABILITY_LIFECYCLE_NAME,
        SHA256,
        STORAGE_RECOVERY_GATE,
        STORAGE_RECOVERY_LIFECYCLE_CHECKS,
        STORAGE_RECOVERY_LIFECYCLE_NAME,
        PhysicalAcceptanceError,
        _bare_metal_context,
        _bare_metal_file_record,
        _bare_metal_lifecycle_payload,
        _device_endurance_lifecycle_payload,
        _expected_marker,
        _gate_result_payload,
        _load_json,
        _operations_systemd_lifecycle_payload,
        _power_state_lifecycle_payload,
        _privacy_scan,
        _read_regular,
        _reject_duplicate_pairs,
        _sha256,
        _storage_recovery_lifecycle_payload,
        _utc_time,
        _validate_bare_metal_lifecycle,
        _validate_bare_metal_phase_details,
        _validate_candidate,
        _validate_device_endurance_lifecycle,
        _validate_gate_result,
        _validate_hardware_profile,
        _validate_operations_systemd_lifecycle,
        _validate_power_state_lifecycle,
        _validate_power_state_phase_details,
        _validate_protocol_interoperability_lifecycle,
        _validate_storage_recovery_lifecycle,
        _write_new,
    )
except ModuleNotFoundError:
    import hub_lifecycle_lab as hub_lab
    import lan_discovery_functional_lab as lan_discovery_lab
    import paperless_functional_lab as paperless_lab
    from physical_acceptance import (
        ARTIFACT_NAME,
        BARE_METAL_EVIDENCE_NAMES,
        BARE_METAL_GATE,
        BARE_METAL_LIFECYCLE_CHECKS,
        BARE_METAL_LIFECYCLE_NAME,
        BARE_METAL_PHASE_EVIDENCE_NAMES,
        BARE_METAL_PHASES,
        DELIVERY_REQUIREMENTS,
        DEVICE_ENDURANCE_EVIDENCE_NAMES,
        DEVICE_ENDURANCE_GATES,
        DEVICE_ENDURANCE_LIFECYCLE_CHECKS,
        DEVICE_ENDURANCE_LIFECYCLE_NAME,
        DEVICE_ENDURANCE_PHASE_EVIDENCE_NAMES,
        GATE_REQUIREMENTS,
        GATE_RESULT_NAME,
        HARDWARE_PROFILE_NAME,
        HUB_LIFECYCLE_PLAN_NAME,
        HUB_LIFECYCLE_RESULT_NAME,
        LAN_DISCOVERY_FUNCTIONAL_PLAN_NAME,
        LAN_DISCOVERY_FUNCTIONAL_RESULT_NAME,
        LAN_DISCOVERY_PROBE_NAMES,
        MAX_ARTIFACT_BYTES,
        MAX_MANIFEST_BYTES,
        MAX_TOTAL_GATE_BYTES,
        OPERATIONS_SYSTEMD_GATE,
        OPERATIONS_SYSTEMD_LIFECYCLE_CHECKS,
        OPERATIONS_SYSTEMD_LIFECYCLE_NAME,
        PAPERLESS_FUNCTIONAL_PLAN_NAME,
        PAPERLESS_FUNCTIONAL_RESULT_NAME,
        PHYSICAL_GATES,
        POWER_STATE_CANARY_BYTES,
        POWER_STATE_EVIDENCE_NAMES,
        POWER_STATE_LIFECYCLE_CHECKS,
        POWER_STATE_LIFECYCLE_NAME,
        POWER_STATE_NAS_CANARY_BYTES,
        POWER_STATE_PHASE_EVIDENCE_NAMES,
        POWER_STATE_PHASES,
        PROTOCOL_INTEROPERABILITY_GATE,
        PROTOCOL_INTEROPERABILITY_LIFECYCLE_NAME,
        SHA256,
        STORAGE_RECOVERY_GATE,
        STORAGE_RECOVERY_LIFECYCLE_CHECKS,
        STORAGE_RECOVERY_LIFECYCLE_NAME,
        PhysicalAcceptanceError,
        _bare_metal_context,
        _bare_metal_file_record,
        _bare_metal_lifecycle_payload,
        _device_endurance_lifecycle_payload,
        _expected_marker,
        _gate_result_payload,
        _load_json,
        _operations_systemd_lifecycle_payload,
        _power_state_lifecycle_payload,
        _privacy_scan,
        _read_regular,
        _reject_duplicate_pairs,
        _sha256,
        _storage_recovery_lifecycle_payload,
        _utc_time,
        _validate_bare_metal_lifecycle,
        _validate_bare_metal_phase_details,
        _validate_candidate,
        _validate_device_endurance_lifecycle,
        _validate_gate_result,
        _validate_hardware_profile,
        _validate_operations_systemd_lifecycle,
        _validate_power_state_lifecycle,
        _validate_power_state_phase_details,
        _validate_protocol_interoperability_lifecycle,
        _validate_storage_recovery_lifecycle,
        _write_new,
    )

LAB_PLAN_NAME = "echo-physical-acceptance-lab-plan.json"
ALL_RESULT_CHECKS = sorted(
    {check for requirement in GATE_REQUIREMENTS.values() for check in requirement["resultChecks"]}
)
OPERATIONS_LAB_EVIDENCE = {
    "operationsSystemdInstalled": "operations-install.log",
    "operationsSystemdInstallRollbackVerified": "operations-install-rollback.log",
    "backupTimerTriggered": "backup-timer.log",
    "auditTimerTriggered": "audit-timer.log",
    "missingBackupMountFailedClosed": "backup-mount-loss.log",
    "missingAuditMountFailedClosed": "audit-mount-loss.log",
    "operationsSystemdRemovalLeftNoUnitsOrTimers": "operations-remove.log",
    "operationsSystemdRemovalPreservedCredentialsAndData": "operations-remove.log",
    "operationsSystemdRemovalRollbackVerified": "operations-remove-rollback.log",
}
OPERATIONS_LAB_PRESERVED = {"deviceState", "NASData", "stateBackups", "auditEvidence"}
OPERATIONS_LAB_PHASES = (
    "install-rollback",
    "install",
    "observe-backup-timer",
    "observe-audit-timer",
    "backup-mount-loss",
    "audit-mount-loss",
    "remove-rollback",
    "remove",
)
STORAGE_LAB_EVIDENCE = {
    "smartHealthy": "storage-baseline.log",
    "diskDisconnectObserved": "storage-degraded.log",
    "raidDegradationObserved": "storage-degraded.log",
    "filesystemReadOnlyHandled": "storage-readonly.log",
    "volumeFullHandled": "storage-volume-full.log",
    "rebootRecoveryVerified": "storage-reboot.log",
    "recycleBinRestoreVerified": "storage-recycle-restore.log",
    "raidRebuildCompleted": "storage-rebuild.log",
    "dataPreserved": "storage-rebuild.log",
}
STORAGE_LAB_PHASES = (
    "baseline",
    "degraded",
    "readonly",
    "volume-full",
    "reconnect",
    "rebuild",
    "reboot",
    "recycle-restore",
)


def _candidate(path: Path) -> dict[str, str]:
    value, raw = _load_json(path, MAX_MANIFEST_BYTES, "candidate evidence index")
    candidate = _validate_candidate(value, raw)
    candidate["indexPath"] = str(path.resolve(strict=True))
    return candidate


def _lab_plan(candidate: dict[str, str]) -> dict[str, Any]:
    gates = []
    for gate in PHYSICAL_GATES:
        requirement = GATE_REQUIREMENTS[gate]
        gates.append(
            {
                "gate": gate,
                "profileClass": requirement["profileClass"],
                "deliveryRequirements": list(requirement["deliveryRequirements"]),
                "requiredArchitecture": requirement["architecture"],
                "minimumDevices": requirement["minimumDevices"],
                "minimumDurationSeconds": requirement["minimumDurationSeconds"],
                "evidenceDirectory": f"physical-evidence/{gate}",
                "defaultPrimaryLog": "acceptance.log",
                "hardwareProfile": HARDWARE_PROFILE_NAME,
                "gateResult": GATE_RESULT_NAME,
                "operationsSystemdLifecycle": (
                    OPERATIONS_SYSTEMD_LIFECYCLE_NAME if gate == OPERATIONS_SYSTEMD_GATE else None
                ),
                "operationsSystemdLab": (
                    "operations_systemd_lab.py plan|run"
                    if gate == OPERATIONS_SYSTEMD_GATE
                    else None
                ),
                "powerStateLifecycle": (
                    POWER_STATE_LIFECYCLE_NAME if gate == OPERATIONS_SYSTEMD_GATE else None
                ),
                "powerStateRecoveryLab": (
                    "power_state_recovery_lab.py seed|plan|run|verify"
                    if gate == OPERATIONS_SYSTEMD_GATE
                    else None
                ),
                "storageRecoveryLifecycle": (
                    STORAGE_RECOVERY_LIFECYCLE_NAME if gate == STORAGE_RECOVERY_GATE else None
                ),
                "storageRecoveryLab": (
                    "storage_recovery_lab.py plan|run" if gate == STORAGE_RECOVERY_GATE else None
                ),
                "protocolInteroperabilityLifecycle": (
                    PROTOCOL_INTEROPERABILITY_LIFECYCLE_NAME
                    if gate == PROTOCOL_INTEROPERABILITY_GATE
                    else None
                ),
                "protocolInteroperabilityLab": (
                    "protocol_interoperability_lab.py "
                    "plan|probe|permissions|quota|large-file|verify"
                    if gate == PROTOCOL_INTEROPERABILITY_GATE
                    else None
                ),
                "deviceEnduranceLifecycle": (
                    DEVICE_ENDURANCE_LIFECYCLE_NAME if gate in DEVICE_ENDURANCE_GATES else None
                ),
                "deviceEnduranceLab": (
                    "device_endurance_lab.py plan|run" if gate in DEVICE_ENDURANCE_GATES else None
                ),
                "hubLifecyclePlan": (
                    HUB_LIFECYCLE_PLAN_NAME if gate in DEVICE_ENDURANCE_GATES else None
                ),
                "hubLifecycleResult": (
                    HUB_LIFECYCLE_RESULT_NAME if gate in DEVICE_ENDURANCE_GATES else None
                ),
                "hubLifecycleLab": (
                    "hub_lifecycle_lab.py plan|run|verify"
                    if gate in DEVICE_ENDURANCE_GATES
                    else None
                ),
                "paperlessFunctionalPlan": (
                    PAPERLESS_FUNCTIONAL_PLAN_NAME if gate in DEVICE_ENDURANCE_GATES else None
                ),
                "paperlessFunctionalResult": (
                    PAPERLESS_FUNCTIONAL_RESULT_NAME if gate in DEVICE_ENDURANCE_GATES else None
                ),
                "paperlessFunctionalLab": (
                    "paperless_functional_lab.py plan|run|verify"
                    if gate in DEVICE_ENDURANCE_GATES
                    else None
                ),
                "lanDiscoveryFunctionalPlan": (
                    LAN_DISCOVERY_FUNCTIONAL_PLAN_NAME if gate in DEVICE_ENDURANCE_GATES else None
                ),
                "lanDiscoveryFunctionalResult": (
                    LAN_DISCOVERY_FUNCTIONAL_RESULT_NAME if gate in DEVICE_ENDURANCE_GATES else None
                ),
                "lanDiscoveryFunctionalLab": (
                    "lan_discovery_functional_lab.py "
                    "plan|credentials|syncthing|home-assistant|verify"
                    if gate in DEVICE_ENDURANCE_GATES
                    else None
                ),
                "bareMetalRecoveryLifecycle": (
                    BARE_METAL_LIFECYCLE_NAME if gate == BARE_METAL_GATE else None
                ),
                "bareMetalRecoveryLab": (
                    "bare_metal_recovery_lab.py plan|run|verify"
                    if gate == BARE_METAL_GATE
                    else None
                ),
                "successMarker": _expected_marker(gate, candidate),
                "successCriteria": str(requirement["suffix"]).split(),
                "requiredResultChecks": list(requirement["resultChecks"]),
            }
        )
    payload: dict[str, Any] = {
        "schemaVersion": 17,
        "kind": "echo.physical-acceptance-lab-plan",
        "candidate": {
            "indexId": candidate["indexId"],
            "osRepository": candidate["repository"],
            "sourceRevision": candidate["sourceRevision"],
            "agentRepository": candidate["agentRepository"],
            "agentRevision": candidate["agentRevision"],
            "releaseTag": candidate["releaseTag"],
            "immutableReference": candidate["immutableReference"],
            "operationsArtifactId": candidate["operationsArtifactId"],
            "operationsArchiveSha256": candidate["operationsArchiveSha256"],
        },
        "gates": gates,
        "deliveryRequirements": list(DELIVERY_REQUIREMENTS),
        "signing": {
            "manifest": "evidence.json",
            "detachedSignature": "evidence.json.gpg",
            "oneAcceptanceKeyRequired": True,
            "distinctLabRunIdPerGateRequired": True,
        },
        "physicalAcceptanceComplete": False,
        "nasProductDeliveryReady": False,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    payload["planId"] = _sha256(canonical)
    return payload


def build_lab_plan(*, candidate_index: Path, output: Path) -> dict[str, Any]:
    candidate = _candidate(candidate_index)
    if output.name != LAB_PLAN_NAME or output.parent.is_symlink():
        raise PhysicalAcceptanceError(f"lab plan output must be a new {LAB_PLAN_NAME}")
    payload = _lab_plan(candidate)
    _write_new(output, payload)
    return payload


def verify_lab_plan(*, candidate_index: Path, plan_path: Path) -> dict[str, Any]:
    candidate = _candidate(candidate_index)
    value, _raw = _load_json(plan_path, MAX_MANIFEST_BYTES, "physical acceptance lab plan")
    expected = _lab_plan(candidate)
    if value != expected:
        raise PhysicalAcceptanceError("physical acceptance lab plan differs from its candidate")
    return expected


def marker(candidate_index: Path, gate: str) -> str:
    if gate not in GATE_REQUIREMENTS:
        raise PhysicalAcceptanceError("physical gate name is invalid")
    return _expected_marker(gate, _candidate(candidate_index))


def build_hardware_profile(
    *, gate: str, architecture: str, device_count: int, output: Path
) -> dict[str, Any]:
    if gate not in GATE_REQUIREMENTS:
        raise PhysicalAcceptanceError("physical gate name is invalid")
    requirement = GATE_REQUIREMENTS[gate]
    if (
        architecture not in {"x86_64", "arm64"}
        or (requirement["architecture"] is not None and architecture != requirement["architecture"])
        or not isinstance(device_count, int)
        or isinstance(device_count, bool)
        or device_count < int(requirement["minimumDevices"])
    ):
        raise PhysicalAcceptanceError("physical gate hardware fields are invalid")
    if output.name != HARDWARE_PROFILE_NAME or output.parent.is_symlink():
        raise PhysicalAcceptanceError(
            f"hardware profile output must be a new {HARDWARE_PROFILE_NAME}"
        )
    payload = {
        "schemaVersion": 1,
        "kind": "echo.physical-hardware-profile",
        "gate": gate,
        "profileClass": requirement["profileClass"],
        "architecture": architecture,
        "deviceCount": device_count,
        "serialsRedacted": True,
    }
    _validate_hardware_profile(
        (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode(),
        gate=gate,
        architecture=architecture,
        device_count=device_count,
    )
    _write_new(output, payload)
    return payload


def build_gate_result(*, gate: str, passed_checks: Sequence[str], output: Path) -> dict[str, Any]:
    if gate not in GATE_REQUIREMENTS:
        raise PhysicalAcceptanceError("physical gate name is invalid")
    expected_checks = tuple(GATE_REQUIREMENTS[gate]["resultChecks"])
    if len(passed_checks) != len(set(passed_checks)) or set(passed_checks) != set(expected_checks):
        raise PhysicalAcceptanceError(
            "gate result must explicitly pass every required check exactly once"
        )
    if output.name != GATE_RESULT_NAME or output.parent.is_symlink():
        raise PhysicalAcceptanceError(f"gate result output must be a new {GATE_RESULT_NAME}")
    payload = _gate_result_payload(gate)
    _validate_gate_result(
        (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode(),
        gate=gate,
    )
    _write_new(output, payload)
    return payload


def build_operations_systemd_lifecycle(
    *,
    candidate_index: Path,
    lab_plan: Path,
    gate: str,
    evidence_arguments: Sequence[str],
    output: Path,
) -> dict[str, Any]:
    if gate != OPERATIONS_SYSTEMD_GATE:
        raise PhysicalAcceptanceError("operations systemd lifecycle belongs only to the G5 gate")
    if output.name != OPERATIONS_SYSTEMD_LIFECYCLE_NAME or output.parent.is_symlink():
        raise PhysicalAcceptanceError(
            f"operations lifecycle output must be a new {OPERATIONS_SYSTEMD_LIFECYCLE_NAME}"
        )
    evidence_directory = output.parent.resolve(strict=True)
    records: dict[str, dict[str, Any]] = {}
    reserved = {
        "evidence.json",
        "evidence.json.gpg",
        HARDWARE_PROFILE_NAME,
        GATE_RESULT_NAME,
        OPERATIONS_SYSTEMD_LIFECYCLE_NAME,
        POWER_STATE_LIFECYCLE_NAME,
        STORAGE_RECOVERY_LIFECYCLE_NAME,
        PROTOCOL_INTEROPERABILITY_LIFECYCLE_NAME,
        DEVICE_ENDURANCE_LIFECYCLE_NAME,
        HUB_LIFECYCLE_PLAN_NAME,
        BARE_METAL_LIFECYCLE_NAME,
    }
    for argument in evidence_arguments:
        check, separator, path_text = argument.partition("=")
        if (
            not separator
            or not check
            or not path_text
            or check not in OPERATIONS_SYSTEMD_LIFECYCLE_CHECKS
            or check in records
        ):
            raise PhysicalAcceptanceError(
                "operations lifecycle must bind every required check exactly once"
            )
        path = Path(path_text)
        resolved = path.resolve(strict=True)
        name = resolved.name
        if (
            resolved.parent != evidence_directory
            or path.is_symlink()
            or ARTIFACT_NAME.fullmatch(name) is None
            or name in reserved
        ):
            raise PhysicalAcceptanceError(
                "operations lifecycle evidence must be a safe gate artifact"
            )
        raw = _read_regular(
            resolved,
            MAX_ARTIFACT_BYTES,
            f"operations lifecycle evidence for {check}",
        )
        _privacy_scan(name, raw)
        records[check] = {"name": name, "sha256": _sha256(raw), "size": len(raw)}
    if set(records) != set(OPERATIONS_SYSTEMD_LIFECYCLE_CHECKS):
        raise PhysicalAcceptanceError(
            "operations lifecycle must bind every required check exactly once"
        )
    candidate = _candidate(candidate_index)
    lab_plan_id = _validate_operations_lab_plan(
        lab_plan,
        candidate=candidate,
        evidence_directory=evidence_directory,
    )
    payload = _operations_systemd_lifecycle_payload(
        records,
        candidate=candidate,
        lab_plan_id=lab_plan_id,
    )
    artifact_lookup = {record["name"]: record for record in records.values()}
    _validate_operations_systemd_lifecycle(
        (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode(),
        artifacts=artifact_lookup,
        candidate=candidate,
    )
    _write_new(output, payload)
    return payload


def build_power_state_lifecycle(
    *,
    candidate_index: Path,
    lab_plan: Path,
    gate: str,
    evidence_directory: Path,
    output: Path,
) -> dict[str, Any]:
    if gate != OPERATIONS_SYSTEMD_GATE:
        raise PhysicalAcceptanceError("power/state lifecycle belongs only to the G5 gate")
    if output.name != POWER_STATE_LIFECYCLE_NAME or output.parent.is_symlink():
        raise PhysicalAcceptanceError(
            f"power/state lifecycle output must be a new {POWER_STATE_LIFECYCLE_NAME}"
        )
    root = evidence_directory.resolve(strict=True)
    if output.parent.resolve(strict=True) != root or evidence_directory.is_symlink():
        raise PhysicalAcceptanceError("power/state evidence must use the lifecycle gate directory")
    candidate = _candidate(candidate_index)
    lab_plan_id, context = _validate_power_state_lab_plan(
        lab_plan,
        candidate=candidate,
        evidence_directory=root,
    )
    phases, phase_bytes = _power_state_phase_records(
        root,
        context=context,
        lab_plan_id=lab_plan_id,
    )
    evidence = {
        check: phases[
            next(
                phase
                for phase, name in POWER_STATE_PHASE_EVIDENCE_NAMES.items()
                if name == POWER_STATE_EVIDENCE_NAMES[check]
            )
        ]
        for check in POWER_STATE_LIFECYCLE_CHECKS
    }
    payload = _power_state_lifecycle_payload(
        evidence,
        phases,
        context,
        candidate=candidate,
        lab_plan_id=lab_plan_id,
    )
    _validate_power_state_lifecycle(
        (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode(),
        artifacts={record["name"]: record for record in phases.values()},
        artifact_bytes=phase_bytes,
        candidate=candidate,
    )
    _write_new(output, payload)
    return payload


def build_bare_metal_lifecycle(
    *,
    candidate_index: Path,
    lab_plan: Path,
    gate: str,
    evidence_directory: Path,
    output: Path,
) -> dict[str, Any]:
    if gate != BARE_METAL_GATE:
        raise PhysicalAcceptanceError("bare-metal lifecycle belongs only to the G6 recovery gate")
    if output.name != BARE_METAL_LIFECYCLE_NAME or output.parent.is_symlink():
        raise PhysicalAcceptanceError(
            f"bare-metal lifecycle output must be a new {BARE_METAL_LIFECYCLE_NAME}"
        )
    root = evidence_directory.resolve(strict=True)
    if output.parent.resolve(strict=True) != root or evidence_directory.is_symlink():
        raise PhysicalAcceptanceError("bare-metal evidence must use the lifecycle gate directory")
    candidate = _candidate(candidate_index)
    lab_plan_id, context = _validate_bare_metal_lab_plan(
        lab_plan,
        candidate=candidate,
        evidence_directory=root,
    )
    phases, phase_bytes = _bare_metal_phase_records(
        root,
        context=context,
        candidate=candidate,
        lab_plan_id=lab_plan_id,
    )
    evidence = {
        check: phases[
            next(
                phase
                for phase, name in BARE_METAL_PHASE_EVIDENCE_NAMES.items()
                if name == BARE_METAL_EVIDENCE_NAMES[check]
            )
        ]
        for check in BARE_METAL_LIFECYCLE_CHECKS
    }
    payload = _bare_metal_lifecycle_payload(
        evidence,
        phases,
        context,
        candidate=candidate,
        lab_plan_id=lab_plan_id,
    )
    _validate_bare_metal_lifecycle(
        (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode(),
        artifacts={record["name"]: record for record in phases.values()},
        artifact_bytes=phase_bytes,
        candidate=candidate,
    )
    _write_new(output, payload)
    return payload


def build_storage_recovery_lifecycle(
    *,
    candidate_index: Path,
    lab_plan: Path,
    gate: str,
    evidence_arguments: Sequence[str],
    output: Path,
) -> dict[str, Any]:
    if gate != STORAGE_RECOVERY_GATE:
        raise PhysicalAcceptanceError("storage recovery lifecycle belongs only to the G2 gate")
    if output.name != STORAGE_RECOVERY_LIFECYCLE_NAME or output.parent.is_symlink():
        raise PhysicalAcceptanceError(
            f"storage lifecycle output must be a new {STORAGE_RECOVERY_LIFECYCLE_NAME}"
        )
    evidence_directory = output.parent.resolve(strict=True)
    records: dict[str, dict[str, Any]] = {}
    reserved = {
        "evidence.json",
        "evidence.json.gpg",
        HARDWARE_PROFILE_NAME,
        GATE_RESULT_NAME,
        OPERATIONS_SYSTEMD_LIFECYCLE_NAME,
        POWER_STATE_LIFECYCLE_NAME,
        STORAGE_RECOVERY_LIFECYCLE_NAME,
        PROTOCOL_INTEROPERABILITY_LIFECYCLE_NAME,
        DEVICE_ENDURANCE_LIFECYCLE_NAME,
        BARE_METAL_LIFECYCLE_NAME,
    }
    for argument in evidence_arguments:
        check, separator, path_text = argument.partition("=")
        if (
            not separator
            or not check
            or not path_text
            or check not in STORAGE_RECOVERY_LIFECYCLE_CHECKS
            or check in records
        ):
            raise PhysicalAcceptanceError(
                "storage lifecycle must bind every required check exactly once"
            )
        path = Path(path_text)
        resolved = path.resolve(strict=True)
        name = resolved.name
        if (
            resolved.parent != evidence_directory
            or path.is_symlink()
            or ARTIFACT_NAME.fullmatch(name) is None
            or name in reserved
        ):
            raise PhysicalAcceptanceError("storage lifecycle evidence must be a safe gate artifact")
        raw = _read_regular(
            resolved,
            MAX_ARTIFACT_BYTES,
            f"storage lifecycle evidence for {check}",
        )
        _privacy_scan(name, raw)
        records[check] = {"name": name, "sha256": _sha256(raw), "size": len(raw)}
    if set(records) != set(STORAGE_RECOVERY_LIFECYCLE_CHECKS):
        raise PhysicalAcceptanceError(
            "storage lifecycle must bind every required check exactly once"
        )
    candidate = _candidate(candidate_index)
    lab_plan_id = _validate_storage_lab_plan(
        lab_plan,
        candidate=candidate,
        evidence_directory=evidence_directory,
    )
    payload = _storage_recovery_lifecycle_payload(
        records,
        candidate=candidate,
        lab_plan_id=lab_plan_id,
    )
    artifact_lookup = {record["name"]: record for record in records.values()}
    reconnect_path = evidence_directory / "storage-reconnect.log"
    reconnect_raw = _read_regular(
        reconnect_path,
        MAX_ARTIFACT_BYTES,
        "storage lifecycle reconnect phase evidence",
    )
    artifact_lookup[reconnect_path.name] = {
        "name": reconnect_path.name,
        "sha256": _sha256(reconnect_raw),
        "size": len(reconnect_raw),
    }
    _validate_storage_recovery_lifecycle(
        (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode(),
        artifacts=artifact_lookup,
        candidate=candidate,
    )
    _write_new(output, payload)
    return payload


def build_device_endurance_lifecycle(
    *,
    candidate_index: Path,
    lab_plan: Path,
    gate: str,
    evidence_arguments: Sequence[str],
    output: Path,
) -> dict[str, Any]:
    if gate not in DEVICE_ENDURANCE_GATES:
        raise PhysicalAcceptanceError("device endurance lifecycle belongs only to device gates")
    if output.name != DEVICE_ENDURANCE_LIFECYCLE_NAME or output.parent.is_symlink():
        raise PhysicalAcceptanceError(
            f"device lifecycle output must be a new {DEVICE_ENDURANCE_LIFECYCLE_NAME}"
        )
    evidence_directory = output.parent.resolve(strict=True)
    records: dict[str, dict[str, Any]] = {}
    reserved = {
        "evidence.json",
        "evidence.json.gpg",
        HARDWARE_PROFILE_NAME,
        GATE_RESULT_NAME,
        OPERATIONS_SYSTEMD_LIFECYCLE_NAME,
        POWER_STATE_LIFECYCLE_NAME,
        STORAGE_RECOVERY_LIFECYCLE_NAME,
        PROTOCOL_INTEROPERABILITY_LIFECYCLE_NAME,
        DEVICE_ENDURANCE_LIFECYCLE_NAME,
        HUB_LIFECYCLE_PLAN_NAME,
        PAPERLESS_FUNCTIONAL_PLAN_NAME,
        LAN_DISCOVERY_FUNCTIONAL_PLAN_NAME,
        BARE_METAL_LIFECYCLE_NAME,
    }
    for argument in evidence_arguments:
        check, separator, path_text = argument.partition("=")
        if (
            not separator
            or not check
            or not path_text
            or check not in DEVICE_ENDURANCE_LIFECYCLE_CHECKS
            or check in records
        ):
            raise PhysicalAcceptanceError(
                "device lifecycle must bind every required check exactly once"
            )
        path = Path(path_text)
        resolved = path.resolve(strict=True)
        name = resolved.name
        if (
            resolved.parent != evidence_directory
            or path.is_symlink()
            or ARTIFACT_NAME.fullmatch(name) is None
            or name in reserved
            or name != DEVICE_ENDURANCE_EVIDENCE_NAMES[check]
        ):
            raise PhysicalAcceptanceError("device lifecycle evidence must be a fixed gate artifact")
        raw = _read_regular(resolved, MAX_ARTIFACT_BYTES, f"device lifecycle evidence for {check}")
        _privacy_scan(name, raw)
        records[check] = {"name": name, "sha256": _sha256(raw), "size": len(raw)}
    if set(records) != set(DEVICE_ENDURANCE_LIFECYCLE_CHECKS):
        raise PhysicalAcceptanceError(
            "device lifecycle must bind every required check exactly once"
        )
    candidate = _candidate(candidate_index)
    hub_plan_path = evidence_directory / HUB_LIFECYCLE_PLAN_NAME
    hub_result_path = evidence_directory / HUB_LIFECYCLE_RESULT_NAME
    for path, mode, label in (
        (hub_plan_path, 0o400, "Hub lifecycle plan"),
        (hub_result_path, 0o444, "Hub lifecycle result"),
    ):
        try:
            observed_mode = stat.S_IMODE(path.lstat().st_mode)
        except OSError as exc:
            raise PhysicalAcceptanceError(f"{label} is unavailable") from exc
        if path.is_symlink() or observed_mode != mode:
            raise PhysicalAcceptanceError(f"{label} has unsafe mode or type")
    hub_plan_raw = _read_regular(
        hub_plan_path,
        MAX_ARTIFACT_BYTES,
        "Hub lifecycle plan",
    )
    hub_result_raw = _read_regular(
        hub_result_path,
        MAX_ARTIFACT_BYTES,
        "Hub lifecycle result",
    )
    try:
        hub_lab.validate_evidence_bytes(
            hub_plan_raw,
            hub_result_raw,
            expected_candidate=candidate,
        )
    except hub_lab.HubLifecycleLabError as exc:
        raise PhysicalAcceptanceError(f"Hub nine-app lifecycle evidence is invalid: {exc}") from exc
    paperless_plan_path = evidence_directory / PAPERLESS_FUNCTIONAL_PLAN_NAME
    paperless_result_path = evidence_directory / PAPERLESS_FUNCTIONAL_RESULT_NAME
    for path, mode, label in (
        (paperless_plan_path, 0o400, "Paperless functional plan"),
        (paperless_result_path, 0o444, "Paperless functional result"),
    ):
        try:
            observed_mode = stat.S_IMODE(path.lstat().st_mode)
        except OSError as exc:
            raise PhysicalAcceptanceError(f"{label} is unavailable") from exc
        if path.is_symlink() or observed_mode != mode:
            raise PhysicalAcceptanceError(f"{label} has unsafe mode or type")
    paperless_plan_raw = _read_regular(
        paperless_plan_path,
        MAX_ARTIFACT_BYTES,
        "Paperless functional plan",
    )
    paperless_result_raw = _read_regular(
        paperless_result_path,
        MAX_ARTIFACT_BYTES,
        "Paperless functional result",
    )
    try:
        paperless_lab.validate_evidence_bytes(
            paperless_plan_raw,
            paperless_result_raw,
            expected_candidate=candidate,
        )
    except paperless_lab.PaperlessFunctionalLabError as exc:
        raise PhysicalAcceptanceError(
            f"Paperless OCR/Office functional evidence is invalid: {exc}"
        ) from exc
    lan_plan_path = evidence_directory / LAN_DISCOVERY_FUNCTIONAL_PLAN_NAME
    lan_result_path = evidence_directory / LAN_DISCOVERY_FUNCTIONAL_RESULT_NAME
    for path, mode, label in (
        (lan_plan_path, 0o400, "LAN discovery functional plan"),
        (lan_result_path, 0o444, "LAN discovery functional result"),
    ):
        try:
            observed_mode = stat.S_IMODE(path.lstat().st_mode)
        except OSError as exc:
            raise PhysicalAcceptanceError(f"{label} is unavailable") from exc
        if path.is_symlink() or observed_mode != mode:
            raise PhysicalAcceptanceError(f"{label} has unsafe mode or type")
    lan_plan_raw = _read_regular(
        lan_plan_path,
        MAX_ARTIFACT_BYTES,
        "LAN discovery functional plan",
    )
    lan_result_raw = _read_regular(
        lan_result_path,
        MAX_ARTIFACT_BYTES,
        "LAN discovery functional result",
    )
    lan_probe_raw: dict[str, bytes] = {}
    for name in LAN_DISCOVERY_PROBE_NAMES:
        path = evidence_directory / name
        try:
            observed_mode = stat.S_IMODE(path.lstat().st_mode)
        except OSError as exc:
            raise PhysicalAcceptanceError("LAN discovery probe evidence is unavailable") from exc
        if path.is_symlink() or observed_mode != 0o444:
            raise PhysicalAcceptanceError("LAN discovery probe evidence has unsafe mode or type")
        lan_probe_raw[name] = _read_regular(
            path,
            MAX_ARTIFACT_BYTES,
            f"LAN discovery probe {name}",
        )
    try:
        lan_plan, lan_result = lan_discovery_lab.validate_evidence_bytes(
            lan_plan_raw,
            lan_result_raw,
            expected_candidate=candidate,
        )
        lan_discovery_lab.validate_probe_artifacts(lan_plan, lan_result, lan_probe_raw)
    except lan_discovery_lab.LanDiscoveryFunctionalLabError as exc:
        raise PhysicalAcceptanceError(
            f"LAN discovery functional evidence is invalid: {exc}"
        ) from exc
    lab_plan_id = _validate_device_lab_plan(
        lab_plan,
        candidate=candidate,
        evidence_directory=evidence_directory,
        gate=gate,
    )
    payload = _device_endurance_lifecycle_payload(
        records,
        candidate=candidate,
        lab_plan_id=lab_plan_id,
        gate=gate,
    )
    artifact_lookup = {record["name"]: record for record in records.values()}
    armed_path = evidence_directory / "device-power-cut-armed.log"
    armed_raw = _read_regular(
        armed_path,
        MAX_ARTIFACT_BYTES,
        "device lifecycle power-cut arm evidence",
    )
    artifact_lookup[armed_path.name] = {
        "name": armed_path.name,
        "sha256": _sha256(armed_raw),
        "size": len(armed_raw),
    }
    _validate_device_endurance_lifecycle(
        (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode(),
        artifacts=artifact_lookup,
        candidate=candidate,
        gate=gate,
    )
    _write_new(output, payload)
    return payload


def _validate_device_lab_plan(
    path: Path,
    *,
    candidate: dict[str, str],
    evidence_directory: Path,
    gate: str,
) -> str:
    value, _raw = _load_json(path, MAX_MANIFEST_BYTES, "device endurance lab plan")
    release = value.get("releaseCandidate") if isinstance(value, dict) else None
    bundle = value.get("operationsBundle") if isinstance(value, dict) else None
    platform_value = value.get("platform") if isinstance(value, dict) else None
    installer = value.get("installer") if isinstance(value, dict) else None
    appliance = value.get("appliance") if isinstance(value, dict) else None
    family_fixture = (
        appliance.get("familyIsolationFixture") if isinstance(appliance, dict) else None
    )
    confirmations = value.get("confirmations") if isinstance(value, dict) else None
    plan_id = value.get("planId") if isinstance(value, dict) else None
    expected_release = {
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
    }
    expected_architecture = "x86_64" if gate == "physical_x86_64_install_and_cold_boot" else "arm64"
    expected_verifier_arch = "amd64" if expected_architecture == "x86_64" else "arm64"
    expected_keys = {
        "schemaVersion",
        "kind",
        "gate",
        "releaseCandidate",
        "bundleRoot",
        "operationsBundle",
        "platform",
        "deviceIdentitySha256",
        "installer",
        "evidenceDirectory",
        "appliance",
        "baselineBootId",
        "firstBootUptimeSeconds",
        "minimumSoakSeconds",
        "phases",
        "planId",
        "confirmations",
    }
    try:
        boot_id = str(uuid.UUID(str(value.get("baselineBootId"))))
    except (ValueError, AttributeError):
        boot_id = ""
    base_url = appliance.get("baseUrl") if isinstance(appliance, dict) else None
    parsed_url = urlsplit(base_url) if isinstance(base_url, str) else None
    valid_origin = bool(
        parsed_url is not None
        and parsed_url.scheme in {"http", "https"}
        and isinstance(parsed_url.hostname, str)
        and 1 <= len(parsed_url.hostname) <= 253
        and re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", parsed_url.hostname)
        is not None
        and parsed_url.username is None
        and parsed_url.password is None
        and parsed_url.path in {"", "/"}
        and not parsed_url.query
        and not parsed_url.fragment
        and (parsed_url.scheme == "https" or parsed_url.hostname in {"127.0.0.1", "localhost"})
    )
    if (
        gate not in DEVICE_ENDURANCE_GATES
        or not isinstance(value, dict)
        or set(value) != expected_keys
        or value.get("schemaVersion") != 1
        or value.get("kind") != "echo.device-endurance-physical-lab-plan"
        or value.get("gate") != gate
        or release != expected_release
        or not isinstance(value.get("bundleRoot"), str)
        or not Path(value["bundleRoot"]).is_absolute()
        or not isinstance(bundle, dict)
        or set(bundle)
        != {
            "artifactId",
            "archiveSha256",
            "imageReference",
            "manifestSha256",
            "deviceEnduranceLabSha256",
            "runningVerifierSha256",
        }
        or bundle.get("artifactId") != candidate["operationsArtifactId"]
        or bundle.get("archiveSha256") != candidate["operationsArchiveSha256"]
        or bundle.get("imageReference") != candidate["immutableReference"]
        or any(
            not isinstance(bundle.get(name), str) or SHA256.fullmatch(bundle[name]) is None
            for name in ("manifestSha256", "deviceEnduranceLabSha256", "runningVerifierSha256")
        )
        or not isinstance(platform_value, dict)
        or set(platform_value) != {"id", "versionId", "omvVersion", "architecture"}
        or platform_value.get("id") != "debian"
        or platform_value.get("versionId") != "13"
        or not isinstance(platform_value.get("omvVersion"), str)
        or not platform_value["omvVersion"].startswith("8.")
        or platform_value.get("architecture") != expected_architecture
        or not isinstance(value.get("deviceIdentitySha256"), str)
        or SHA256.fullmatch(value["deviceIdentitySha256"]) is None
        or not isinstance(installer, dict)
        or set(installer)
        != {
            "path",
            "sha256",
            "size",
            "imageVersion",
            "manifestSha256",
            "sourceSha256",
            "targetIdentitySha256",
            "postWriteReadbackVerified",
            "dataProtection",
        }
        or not isinstance(installer.get("path"), str)
        or not Path(installer["path"]).is_absolute()
        or any(
            not isinstance(installer.get(name), str) or SHA256.fullmatch(installer[name]) is None
            for name in ("sha256", "manifestSha256", "sourceSha256", "targetIdentitySha256")
        )
        or not isinstance(installer.get("size"), int)
        or isinstance(installer.get("size"), bool)
        or installer["size"] <= 0
        or installer.get("imageVersion") != candidate["releaseTag"].removeprefix("echo-appliance-v")
        or installer.get("postWriteReadbackVerified") is not True
        or installer.get("dataProtection") != "luks2-tpm2-signed-pcr11-recovery"
        or value.get("evidenceDirectory") != str(evidence_directory)
        or not isinstance(appliance, dict)
        or set(appliance)
        != {
            "baseUrl",
            "mainContainer",
            "proxyContainer",
            "expectedArchitecture",
            "nasTransferPath",
            "nasTransferBytes",
            "familyIsolationFixture",
        }
        or not valid_origin
        or (
            gate == "physical_x86_64_install_and_cold_boot"
            and not appliance["baseUrl"].startswith("https://")
        )
        or not isinstance(appliance.get("mainContainer"), str)
        or ARTIFACT_NAME.fullmatch(appliance["mainContainer"]) is None
        or not isinstance(appliance.get("proxyContainer"), str)
        or ARTIFACT_NAME.fullmatch(appliance["proxyContainer"]) is None
        or appliance.get("expectedArchitecture") != expected_verifier_arch
        or not isinstance(appliance.get("nasTransferPath"), str)
        or not appliance["nasTransferPath"]
        or appliance.get("nasTransferBytes") != 1024 * 1024 * 1024
        or not isinstance(family_fixture, dict)
        or set(family_fixture) != {"path", "sha256", "size", "mode"}
        or not isinstance(family_fixture.get("path"), str)
        or not Path(family_fixture["path"]).is_absolute()
        or not isinstance(family_fixture.get("sha256"), str)
        or SHA256.fullmatch(family_fixture["sha256"]) is None
        or not isinstance(family_fixture.get("size"), int)
        or isinstance(family_fixture.get("size"), bool)
        or not 1 <= family_fixture["size"] <= 32 * 1024
        or family_fixture.get("mode") != "0400"
        or value.get("baselineBootId") != boot_id
        or not isinstance(value.get("firstBootUptimeSeconds"), (int, float))
        or isinstance(value.get("firstBootUptimeSeconds"), bool)
        or not 0 <= value["firstBootUptimeSeconds"] <= 6 * 60 * 60
        or value.get("minimumSoakSeconds") != 24 * 60 * 60
        or value.get("phases") != ["baseline", "soak", "arm-power-cut", "recovered"]
        or not isinstance(plan_id, str)
        or SHA256.fullmatch(plan_id) is None
        or confirmations
        != {
            phase: f"RUN ECHO DEVICE ENDURANCE LAB {phase} {plan_id}"
            for phase in ("baseline", "soak", "arm-power-cut", "recovered")
        }
    ):
        raise PhysicalAcceptanceError("device lab plan is not bound to this candidate and gate")
    unsigned = dict(value)
    unsigned.pop("confirmations")
    unsigned.pop("planId")
    canonical = (json.dumps(unsigned, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if plan_id != _sha256(canonical):
        raise PhysicalAcceptanceError("device lab plan ID is invalid")
    return plan_id


def _validate_storage_lab_plan(
    path: Path,
    *,
    candidate: dict[str, str],
    evidence_directory: Path,
) -> str:
    value, _raw = _load_json(path, MAX_MANIFEST_BYTES, "storage recovery lab plan")
    release = value.get("releaseCandidate") if isinstance(value, dict) else None
    bundle = value.get("operationsBundle") if isinstance(value, dict) else None
    platform = value.get("platform") if isinstance(value, dict) else None
    devices = value.get("devices") if isinstance(value, dict) else None
    mount = value.get("mount") if isinstance(value, dict) else None
    authorization = value.get("authorization") if isinstance(value, dict) else None
    nas_transfer = value.get("nasTransfer") if isinstance(value, dict) else None
    expected_release = {
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
    }
    expected_keys = {
        "schemaVersion",
        "kind",
        "gate",
        "releaseCandidate",
        "bundleRoot",
        "operationsBundle",
        "platform",
        "devices",
        "sacrificialMember",
        "mount",
        "authorization",
        "evidenceDirectory",
        "nasTransfer",
        "baselineBootId",
        "phases",
        "planId",
        "confirmations",
    }
    plan_id = value.get("planId") if isinstance(value, dict) else None
    confirmations = value.get("confirmations") if isinstance(value, dict) else None
    members = devices.get("members") if isinstance(devices, dict) else None
    array = devices.get("array") if isinstance(devices, dict) else None
    try:
        boot_id = str(uuid.UUID(str(value.get("baselineBootId"))))
        volume_id = str(uuid.UUID(str(authorization.get("labVolumeId")), version=4))
    except (ValueError, AttributeError):
        boot_id = ""
        volume_id = ""
    if (
        not isinstance(value, dict)
        or set(value) != expected_keys
        or value.get("schemaVersion") != 1
        or value.get("kind") != "echo.storage-recovery-physical-lab-plan"
        or value.get("gate") != STORAGE_RECOVERY_GATE
        or release != expected_release
        or not isinstance(value.get("bundleRoot"), str)
        or not Path(value["bundleRoot"]).is_absolute()
        or not isinstance(bundle, dict)
        or set(bundle)
        != {
            "artifactId",
            "archiveSha256",
            "imageReference",
            "manifestSha256",
            "storageRecoveryLabSha256",
            "runningVerifierSha256",
        }
        or bundle.get("artifactId") != candidate["operationsArtifactId"]
        or bundle.get("archiveSha256") != candidate["operationsArchiveSha256"]
        or bundle.get("imageReference") != candidate["immutableReference"]
        or any(
            not isinstance(bundle.get(name), str) or SHA256.fullmatch(bundle[name]) is None
            for name in ("manifestSha256", "storageRecoveryLabSha256", "runningVerifierSha256")
        )
        or not isinstance(platform, dict)
        or set(platform) != {"id", "versionId", "omvVersion"}
        or platform.get("id") != "debian"
        or platform.get("versionId") != "13"
        or not isinstance(platform.get("omvVersion"), str)
        or not platform["omvVersion"].startswith("8.")
        or not isinstance(devices, dict)
        or set(devices) != {"array", "members", "mountpoint"}
        or not isinstance(array, dict)
        or set(array) != {"path", "majorMinor", "sizeBytes"}
        or not isinstance(array.get("path"), str)
        or not array["path"].startswith("/dev/md")
        or not isinstance(array.get("majorMinor"), str)
        or not isinstance(array.get("sizeBytes"), int)
        or isinstance(array.get("sizeBytes"), bool)
        or not 4 * 1024**3 <= array["sizeBytes"] <= 64 * 1024**3
        or not isinstance(members, list)
        or len(members) != 2
        or any(
            not isinstance(member, dict)
            or set(member) != {"path", "parentPath", "majorMinor", "sizeBytes", "identitySha256"}
            or not isinstance(member.get("identitySha256"), str)
            or SHA256.fullmatch(member["identitySha256"]) is None
            or not isinstance(member.get("path"), str)
            or not member["path"].startswith("/dev/")
            or not isinstance(member.get("parentPath"), str)
            or not member["parentPath"].startswith("/dev/")
            or not isinstance(member.get("majorMinor"), str)
            or not isinstance(member.get("sizeBytes"), int)
            or isinstance(member.get("sizeBytes"), bool)
            or member["sizeBytes"] <= 0
            for member in members
        )
        or len({member["identitySha256"] for member in members}) != 2
        or value.get("sacrificialMember") not in {member.get("path") for member in members}
        or not isinstance(mount, dict)
        or set(mount)
        != {"target", "source", "filesystem", "readOnly", "sizeBytes", "availableBytes"}
        or mount.get("target") != devices.get("mountpoint")
        or mount.get("source") != array.get("path")
        or mount.get("filesystem") not in {"ext4", "xfs"}
        or mount.get("readOnly") is not False
        or not isinstance(mount.get("sizeBytes"), int)
        or isinstance(mount.get("sizeBytes"), bool)
        or not 4 * 1024**3 <= mount["sizeBytes"] <= array["sizeBytes"]
        or not isinstance(mount.get("availableBytes"), int)
        or isinstance(mount.get("availableBytes"), bool)
        or mount["availableBytes"] < 2 * 1024**3
        or not isinstance(authorization, dict)
        or set(authorization)
        != {
            "schemaVersion",
            "kind",
            "disposable",
            "candidateIndexId",
            "arrayDevice",
            "mountpoint",
            "labVolumeId",
        }
        or authorization.get("schemaVersion") != 1
        or authorization.get("kind") != "echo.storage-recovery-lab-authorization"
        or authorization.get("disposable") is not True
        or authorization.get("candidateIndexId") != candidate["indexId"]
        or authorization.get("arrayDevice") != array.get("path")
        or authorization.get("mountpoint") != devices.get("mountpoint")
        or authorization.get("labVolumeId") != volume_id
        or value.get("evidenceDirectory") != str(evidence_directory)
        or not isinstance(nas_transfer, dict)
        or set(nas_transfer) != {"baseUrl", "path", "bytes"}
        or nas_transfer.get("baseUrl")
        not in {
            "http://127.0.0.1:8000",
            "http://localhost:8000",
            "https://127.0.0.1:8000",
            "https://localhost:8000",
        }
        or not isinstance(nas_transfer.get("path"), str)
        or not nas_transfer["path"]
        or nas_transfer.get("bytes") != 1024 * 1024 * 1024
        or value.get("baselineBootId") != boot_id
        or value.get("phases") != list(STORAGE_LAB_PHASES)
        or not isinstance(plan_id, str)
        or SHA256.fullmatch(plan_id) is None
        or confirmations
        != {
            phase: f"RUN ECHO STORAGE RECOVERY LAB {phase} {plan_id}"
            for phase in STORAGE_LAB_PHASES
        }
    ):
        raise PhysicalAcceptanceError("storage lab plan is not bound to this candidate")
    unsigned = dict(value)
    unsigned.pop("confirmations")
    unsigned.pop("planId")
    canonical = (json.dumps(unsigned, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if plan_id != _sha256(canonical):
        raise PhysicalAcceptanceError("storage lab plan ID is invalid")
    return plan_id


def _validate_operations_lab_plan(
    path: Path,
    *,
    candidate: dict[str, str],
    evidence_directory: Path,
) -> str:
    value, _raw = _load_json(path, MAX_MANIFEST_BYTES, "operations systemd lab plan")
    confirmations = value.get("confirmations") if isinstance(value, dict) else None
    release = value.get("releaseCandidate") if isinstance(value, dict) else None
    bundle = value.get("operationsBundle") if isinstance(value, dict) else None
    expected_release = {
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
    }
    expected_keys = {
        "schemaVersion",
        "kind",
        "releaseCandidate",
        "operationsBundle",
        "platform",
        "config",
        "installPlan",
        "evidenceDirectory",
        "preservation",
        "baseline",
        "phases",
        "planId",
        "confirmations",
    }
    plan_id = value.get("planId") if isinstance(value, dict) else None
    if (
        not isinstance(value, dict)
        or set(value) != expected_keys
        or value.get("schemaVersion") != 2
        or value.get("kind") != "echo.operations-systemd-physical-lab-plan"
        or release != expected_release
        or not isinstance(bundle, dict)
        or set(bundle)
        != {
            "artifactId",
            "archiveSha256",
            "imageReference",
            "manifestSha256",
            "labToolSha256",
            "labToolSize",
        }
        or bundle.get("artifactId") != candidate["operationsArtifactId"]
        or bundle.get("archiveSha256") != candidate["operationsArchiveSha256"]
        or bundle.get("imageReference") != candidate["immutableReference"]
        or not isinstance(bundle.get("manifestSha256"), str)
        or SHA256.fullmatch(bundle["manifestSha256"]) is None
        or not isinstance(bundle.get("labToolSha256"), str)
        or SHA256.fullmatch(bundle["labToolSha256"]) is None
        or not isinstance(bundle.get("labToolSize"), int)
        or isinstance(bundle.get("labToolSize"), bool)
        or bundle["labToolSize"] <= 0
        or value.get("evidenceDirectory") != str(evidence_directory)
        or value.get("phases") != list(OPERATIONS_LAB_PHASES)
        or not isinstance(plan_id, str)
        or SHA256.fullmatch(plan_id) is None
        or confirmations
        != {phase: f"RUN ECHO OPERATIONS LAB {phase} {plan_id}" for phase in OPERATIONS_LAB_PHASES}
    ):
        raise PhysicalAcceptanceError("operations lab plan is not bound to this candidate")
    unsigned = dict(value)
    unsigned.pop("confirmations")
    unsigned.pop("planId")
    canonical = (json.dumps(unsigned, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if plan_id != _sha256(canonical):
        raise PhysicalAcceptanceError("operations lab plan ID is invalid")
    return plan_id


def _validate_power_state_lab_plan(
    path: Path,
    *,
    candidate: dict[str, str],
    evidence_directory: Path,
) -> tuple[str, dict[str, Any]]:
    value, _raw = _load_json(path, MAX_MANIFEST_BYTES, "power/state physical lab plan")
    release = value.get("releaseCandidate") if isinstance(value, dict) else None
    bundle = value.get("operationsBundle") if isinstance(value, dict) else None
    tools = bundle.get("tools") if isinstance(bundle, dict) else None
    operations = value.get("operationsLabPlan") if isinstance(value, dict) else None
    canaries = value.get("canaries") if isinstance(value, dict) else None
    backup = value.get("backup") if isinstance(value, dict) else None
    host_tools = value.get("hostTools") if isinstance(value, dict) else None
    confirmations = value.get("confirmations") if isinstance(value, dict) else None
    plan_id = value.get("planId") if isinstance(value, dict) else None
    expected_release = {
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
    }
    expected_keys = {
        "schemaVersion",
        "kind",
        "gate",
        "releaseCandidate",
        "bundleRoot",
        "operationsBundle",
        "operationsLabPlan",
        "evidenceDirectory",
        "previousImage",
        "targetImage",
        "releaseEnvironment",
        "transactionPath",
        "baselineBootId",
        "containers",
        "canaries",
        "backup",
        "hostTools",
        "phases",
        "planId",
        "confirmations",
    }
    bundle_root = value.get("bundleRoot") if isinstance(value, dict) else None
    state = canaries.get("state") if isinstance(canaries, dict) else None
    nas = canaries.get("nas") if isinstance(canaries, dict) else None

    def canary(record: object, expected_size: int) -> bool:
        return (
            isinstance(record, dict)
            and set(record) == {"path", "sha256", "size"}
            and isinstance(record.get("path"), str)
            and Path(record["path"]).is_absolute()
            and isinstance(record.get("sha256"), str)
            and SHA256.fullmatch(record["sha256"]) is not None
            and record.get("size") == expected_size
        )

    if (
        not isinstance(value, dict)
        or set(value) != expected_keys
        or value.get("schemaVersion") != 1
        or value.get("kind") != "echo.power-state-physical-lab-plan"
        or value.get("gate") != OPERATIONS_SYSTEMD_GATE
        or release != expected_release
        or not isinstance(bundle_root, str)
        or not Path(bundle_root).is_absolute()
        or not isinstance(bundle, dict)
        or set(bundle)
        != {
            "artifactId",
            "archiveSha256",
            "imageReference",
            "manifestSha256",
            "tools",
        }
        or bundle.get("artifactId") != candidate["operationsArtifactId"]
        or bundle.get("archiveSha256") != candidate["operationsArchiveSha256"]
        or bundle.get("imageReference") != candidate["immutableReference"]
        or not isinstance(bundle.get("manifestSha256"), str)
        or SHA256.fullmatch(bundle["manifestSha256"]) is None
        or not isinstance(tools, dict)
        or set(tools)
        != {
            "power_state_recovery_lab.py",
            "upgrade-appliance.sh",
            "recover-appliance-upgrade.sh",
            "upgrade_transaction.py",
            "backup-state.sh",
            "restore-state.sh",
            "install-appliance.sh",
        }
        or any(
            not isinstance(item, str) or SHA256.fullmatch(item) is None for item in tools.values()
        )
        or not isinstance(operations, dict)
        or set(operations) != {"path", "planId"}
        or not isinstance(operations.get("path"), str)
        or not Path(operations["path"]).is_absolute()
        or not isinstance(operations.get("planId"), str)
        or SHA256.fullmatch(operations["planId"]) is None
        or value.get("evidenceDirectory") != str(evidence_directory)
        or not isinstance(value.get("previousImage"), str)
        or re.fullmatch(r"[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64}", value["previousImage"])
        is None
        or value.get("targetImage") != candidate["immutableReference"]
        or value.get("previousImage") == value.get("targetImage")
        or value.get("releaseEnvironment") != f"{bundle_root}/echo-release.env"
        or value.get("transactionPath") != f"{bundle_root}/.echo-upgrade-transaction.json"
        or not _valid_boot_id(value.get("baselineBootId"))
        or not isinstance(value.get("containers"), dict)
        or set(value["containers"]) != {"main", "proxy"}
        or any(
            not isinstance(name, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name) is None
            for name in value["containers"].values()
        )
        or not isinstance(canaries, dict)
        or set(canaries) != {"state", "nas"}
        or not canary(state, POWER_STATE_CANARY_BYTES)
        or not canary(nas, POWER_STATE_NAS_CANARY_BYTES)
        or Path(state["path"]).parent != Path(bundle_root) / "data"
        or not isinstance(backup, dict)
        or set(backup) != {"directory", "mountpoint", "credential"}
        or any(
            not isinstance(item, str) or not Path(item).is_absolute() for item in backup.values()
        )
        or not isinstance(host_tools, dict)
        or set(host_tools)
        != {
            "docker",
            "systemctl",
            "systemd_run",
            "systemd_creds",
            "journalctl",
            "logger",
            "sync",
            "dpkg_query",
        }
        or any(
            not isinstance(item, str) or not Path(item).is_absolute()
            for item in host_tools.values()
        )
        or value.get("phases") != list(POWER_STATE_PHASES)
        or not isinstance(plan_id, str)
        or SHA256.fullmatch(plan_id) is None
        or confirmations
        != {phase: f"RUN ECHO POWER STATE LAB {phase} {plan_id}" for phase in POWER_STATE_PHASES}
    ):
        raise PhysicalAcceptanceError("power/state lab plan is not bound to this candidate")
    unsigned = dict(value)
    unsigned.pop("confirmations")
    unsigned.pop("planId")
    canonical = (json.dumps(unsigned, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if plan_id != _sha256(canonical):
        raise PhysicalAcceptanceError("power/state lab plan ID is invalid")
    context = {
        "previousImage": value["previousImage"],
        "targetImage": value["targetImage"],
        "baselineBootId": value["baselineBootId"],
        "canaries": value["canaries"],
    }
    return plan_id, context


def _power_state_phase_records(
    directory: Path,
    *,
    context: dict[str, Any],
    lab_plan_id: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, bytes]]:
    records: dict[str, dict[str, Any]] = {}
    phase_bytes: dict[str, bytes] = {}
    for phase in POWER_STATE_PHASES:
        name = POWER_STATE_PHASE_EVIDENCE_NAMES[phase]
        path = directory / name
        try:
            mode = stat.S_IMODE(path.lstat().st_mode)
        except OSError as exc:
            raise PhysicalAcceptanceError("power/state lab phase evidence is unavailable") from exc
        if path.is_symlink() or mode != 0o444:
            raise PhysicalAcceptanceError("power/state lab phase evidence has unsafe mode or type")
        raw = _read_regular(path, MAX_ARTIFACT_BYTES, f"power/state lab evidence {name}")
        _privacy_scan(name, raw)
        try:
            value = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise PhysicalAcceptanceError("power/state lab evidence is not strict JSON") from exc
        if (
            not isinstance(value, dict)
            or set(value) != {"schemaVersion", "kind", "planId", "phase", "passed", "details"}
            or value.get("schemaVersion") != 1
            or value.get("kind") != "echo.power-state-physical-lab-evidence"
            or value.get("planId") != lab_plan_id
            or value.get("phase") != phase
            or value.get("passed") is not True
            or not isinstance(value.get("details"), dict)
        ):
            raise PhysicalAcceptanceError("power/state lab evidence contract is invalid")
        _validate_power_state_phase_details(
            context,
            phase,
            value["details"],
            lab_plan_id=lab_plan_id,
        )
        records[phase] = {"name": name, "sha256": _sha256(raw), "size": len(raw)}
        phase_bytes[name] = raw
    return records, phase_bytes


def _validate_bare_metal_lab_plan(
    path: Path,
    *,
    candidate: dict[str, str],
    evidence_directory: Path,
) -> tuple[str, dict[str, Any]]:
    value, _raw = _load_json(path, MAX_MANIFEST_BYTES, "bare-metal physical lab plan")
    release = value.get("releaseCandidate") if isinstance(value, dict) else None
    bundle = value.get("operationsBundle") if isinstance(value, dict) else None
    tools = bundle.get("tools") if isinstance(bundle, dict) else None
    source = value.get("sourceSystem") if isinstance(value, dict) else None
    backups = value.get("backups") if isinstance(value, dict) else None
    installed = value.get("installedSystem") if isinstance(value, dict) else None
    installer = value.get("installer") if isinstance(value, dict) else None
    confirmations = value.get("confirmations") if isinstance(value, dict) else None
    plan_id = value.get("planId") if isinstance(value, dict) else None
    expected_release = {
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
    }
    expected_keys = {
        "schemaVersion",
        "kind",
        "gate",
        "releaseCandidate",
        "bundleRoot",
        "operationsBundle",
        "installer",
        "backups",
        "sourceSystem",
        "installedSystem",
        "appliance",
        "evidenceDirectory",
        "phases",
        "planId",
        "confirmations",
    }
    bundle_root = value.get("bundleRoot") if isinstance(value, dict) else None
    recovery_key = installer.get("recoveryKey") if isinstance(installer, dict) else None
    context = {
        "sourceSystem": source,
        "backups": backups,
        "verifierArchitecture": (
            installed.get("verifierArchitecture") if isinstance(installed, dict) else None
        ),
    }
    expected_tool_names = {
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
    }
    if (
        not isinstance(value, dict)
        or set(value) != expected_keys
        or value.get("schemaVersion") != 1
        or value.get("kind") != "echo.bare-metal-recovery-physical-lab-plan"
        or value.get("gate") != BARE_METAL_GATE
        or release != expected_release
        or not isinstance(bundle_root, str)
        or not Path(bundle_root).is_absolute()
        or not isinstance(bundle, dict)
        or set(bundle)
        != {"artifactId", "archiveSha256", "imageReference", "manifestSha256", "tools"}
        or bundle.get("artifactId") != candidate["operationsArtifactId"]
        or bundle.get("archiveSha256") != candidate["operationsArchiveSha256"]
        or bundle.get("imageReference") != candidate["immutableReference"]
        or not isinstance(bundle.get("manifestSha256"), str)
        or SHA256.fullmatch(bundle["manifestSha256"]) is None
        or not isinstance(tools, dict)
        or set(tools) != expected_tool_names
        or any(
            not isinstance(item, str) or SHA256.fullmatch(item) is None for item in tools.values()
        )
        or not isinstance(installer, dict)
        or set(installer) != {"bundle", "target", "recoveryKey"}
        or not all(
            isinstance(installer.get(name), str) and Path(installer[name]).is_absolute()
            for name in ("bundle", "target")
        )
        or not isinstance(recovery_key, dict)
        or not _bare_metal_file_record(recovery_key)
        or not isinstance(installed, dict)
        or set(installed)
        != {
            "deploymentRoot",
            "agentRoot",
            "agentUid",
            "nasRoot",
            "architecture",
            "verifierArchitecture",
            "sourceIdentity",
            "userBackup",
            "userState",
            "restoreHealth",
        }
        or any(
            not isinstance(installed.get(name), str) or not Path(installed[name]).is_absolute()
            for name in (
                "deploymentRoot",
                "agentRoot",
                "nasRoot",
                "sourceIdentity",
                "userBackup",
                "userState",
                "restoreHealth",
            )
        )
        or not isinstance(installed.get("agentUid"), int)
        or isinstance(installed.get("agentUid"), bool)
        or installed["agentUid"] < 0
        or installed.get("architecture") not in {"x86_64", "arm64"}
        or installed.get("verifierArchitecture") not in {"amd64", "arm64"}
        or not _bare_metal_context(context, candidate=candidate)
        or value.get("evidenceDirectory") != str(evidence_directory)
        or value.get("phases") != list(BARE_METAL_PHASES)
        or not isinstance(plan_id, str)
        or SHA256.fullmatch(plan_id) is None
        or confirmations
        != {
            phase: f"RUN ECHO BARE METAL RECOVERY LAB {phase} {plan_id}"
            for phase in BARE_METAL_PHASES[1:]
        }
    ):
        raise PhysicalAcceptanceError("bare-metal lab plan is not bound to this candidate")
    unsigned = dict(value)
    unsigned.pop("confirmations")
    unsigned.pop("planId")
    canonical = (json.dumps(unsigned, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if plan_id != _sha256(canonical):
        raise PhysicalAcceptanceError("bare-metal lab plan ID is invalid")
    return plan_id, context


def _bare_metal_phase_records(
    directory: Path,
    *,
    context: dict[str, Any],
    candidate: dict[str, str],
    lab_plan_id: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, bytes]]:
    records: dict[str, dict[str, Any]] = {}
    phase_bytes: dict[str, bytes] = {}
    values: dict[str, dict[str, Any]] = {}
    for phase in BARE_METAL_PHASES:
        name = BARE_METAL_PHASE_EVIDENCE_NAMES[phase]
        path = directory / name
        try:
            mode = stat.S_IMODE(path.lstat().st_mode)
        except OSError as exc:
            raise PhysicalAcceptanceError("bare-metal lab phase evidence is unavailable") from exc
        if path.is_symlink() or mode != 0o444:
            raise PhysicalAcceptanceError("bare-metal lab phase evidence has unsafe mode or type")
        raw = _read_regular(path, MAX_ARTIFACT_BYTES, f"bare-metal lab evidence {name}")
        _privacy_scan(name, raw)
        try:
            value = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise PhysicalAcceptanceError("bare-metal lab evidence is not strict JSON") from exc
        if (
            not isinstance(value, dict)
            or set(value) != {"schemaVersion", "kind", "planId", "phase", "passed", "details"}
            or value.get("schemaVersion") != 1
            or value.get("kind") != "echo.bare-metal-recovery-physical-lab-evidence"
            or value.get("planId") != lab_plan_id
            or value.get("phase") != phase
            or value.get("passed") is not True
            or not isinstance(value.get("details"), dict)
        ):
            raise PhysicalAcceptanceError("bare-metal lab evidence contract is invalid")
        values[phase] = value["details"]
        _validate_bare_metal_phase_details(
            context,
            phase,
            value["details"],
            values,
            candidate=candidate,
        )
        records[phase] = {"name": name, "sha256": _sha256(raw), "size": len(raw)}
        phase_bytes[name] = raw
    return records, phase_bytes


def operations_lab_evidence_arguments(
    directory: Path,
    *,
    candidate_index: Path,
    lab_plan: Path,
) -> list[str]:
    if not directory.is_absolute() or directory.is_symlink():
        raise PhysicalAcceptanceError("operations lab evidence directory must be absolute")
    root = directory.resolve(strict=True)
    if not root.is_dir():
        raise PhysicalAcceptanceError("operations lab evidence directory is unavailable")
    candidate = _candidate(candidate_index)
    expected_plan_id = _validate_operations_lab_plan(
        lab_plan,
        candidate=candidate,
        evidence_directory=root,
    )
    plan_ids: set[str] = set()
    for name in set(OPERATIONS_LAB_EVIDENCE.values()):
        path = root / name
        if path.is_symlink() or path.stat().st_mode & 0o777 != 0o444:
            raise PhysicalAcceptanceError("operations lab evidence has unsafe mode or type")
        raw = _read_regular(path, MAX_ARTIFACT_BYTES, f"operations lab evidence {name}")
        _privacy_scan(name, raw)
        try:
            value = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise PhysicalAcceptanceError("operations lab evidence is not strict JSON") from exc
        if (
            not isinstance(value, dict)
            or set(value) != {"schemaVersion", "kind", "planId", "evidence", "passed", "details"}
            or value["schemaVersion"] != 2
            or value["kind"] != "echo.operations-systemd-physical-lab-evidence"
            or not isinstance(value["planId"], str)
            or SHA256.fullmatch(value["planId"]) is None
            or value["planId"] != expected_plan_id
            or value["evidence"] != name
            or value["passed"] is not True
            or not isinstance(value["details"], dict)
        ):
            raise PhysicalAcceptanceError("operations lab evidence contract is invalid")
        _validate_operations_lab_details(name, value["details"])
        plan_ids.add(value["planId"])
    if len(plan_ids) != 1:
        raise PhysicalAcceptanceError("operations lab evidence belongs to different plans")
    return [
        f"{check}={root / OPERATIONS_LAB_EVIDENCE[check]}"
        for check in OPERATIONS_SYSTEMD_LIFECYCLE_CHECKS
    ]


def storage_lab_evidence_arguments(
    directory: Path,
    *,
    candidate_index: Path,
    lab_plan: Path,
) -> list[str]:
    if not directory.is_absolute() or directory.is_symlink():
        raise PhysicalAcceptanceError("storage lab evidence directory must be absolute")
    root = directory.resolve(strict=True)
    if not root.is_dir():
        raise PhysicalAcceptanceError("storage lab evidence directory is unavailable")
    candidate = _candidate(candidate_index)
    expected_plan_id = _validate_storage_lab_plan(
        lab_plan,
        candidate=candidate,
        evidence_directory=root,
    )
    plan_ids: set[str] = set()
    for name in set(STORAGE_LAB_EVIDENCE.values()) | {"storage-reconnect.log"}:
        path = root / name
        if path.is_symlink() or path.stat().st_mode & 0o777 != 0o444:
            raise PhysicalAcceptanceError("storage lab evidence has unsafe mode or type")
        raw = _read_regular(path, MAX_ARTIFACT_BYTES, f"storage lab evidence {name}")
        _privacy_scan(name, raw)
        try:
            value = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise PhysicalAcceptanceError("storage lab evidence is not strict JSON") from exc
        if (
            not isinstance(value, dict)
            or set(value) != {"schemaVersion", "kind", "planId", "evidence", "passed", "details"}
            or value["schemaVersion"] != 1
            or value["kind"] != "echo.storage-recovery-physical-lab-evidence"
            or value.get("planId") != expected_plan_id
            or value.get("evidence") != name
            or value.get("passed") is not True
            or not isinstance(value.get("details"), dict)
        ):
            raise PhysicalAcceptanceError("storage lab evidence contract is invalid")
        _validate_storage_lab_details(name, value["details"])
        plan_ids.add(value["planId"])
    if plan_ids != {expected_plan_id}:
        raise PhysicalAcceptanceError("storage lab evidence belongs to different plans")
    return [
        f"{check}={root / STORAGE_LAB_EVIDENCE[check]}"
        for check in STORAGE_RECOVERY_LIFECYCLE_CHECKS
    ]


def device_lab_evidence_arguments(
    directory: Path,
    *,
    candidate_index: Path,
    lab_plan: Path,
    gate: str,
) -> list[str]:
    if not directory.is_absolute() or directory.is_symlink():
        raise PhysicalAcceptanceError("device lab evidence directory must be absolute")
    root = directory.resolve(strict=True)
    if not root.is_dir():
        raise PhysicalAcceptanceError("device lab evidence directory is unavailable")
    candidate = _candidate(candidate_index)
    expected_plan_id = _validate_device_lab_plan(
        lab_plan,
        candidate=candidate,
        evidence_directory=root,
        gate=gate,
    )
    for name in DEVICE_ENDURANCE_PHASE_EVIDENCE_NAMES:
        path = root / name
        try:
            mode = stat.S_IMODE(path.lstat().st_mode)
        except OSError as exc:
            raise PhysicalAcceptanceError("device lab evidence is unavailable") from exc
        if path.is_symlink() or mode != 0o444:
            raise PhysicalAcceptanceError("device lab evidence has unsafe mode or type")
        raw = _read_regular(path, MAX_ARTIFACT_BYTES, f"device lab evidence {name}")
        _privacy_scan(name, raw)
        try:
            value = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise PhysicalAcceptanceError("device lab evidence is not strict JSON") from exc
        if (
            not isinstance(value, dict)
            or set(value) != {"schemaVersion", "kind", "planId", "evidence", "passed", "details"}
            or value.get("schemaVersion") != 1
            or value.get("kind") != "echo.device-endurance-physical-lab-evidence"
            or value.get("planId") != expected_plan_id
            or value.get("evidence") != name
            or value.get("passed") is not True
            or not isinstance(value.get("details"), dict)
        ):
            raise PhysicalAcceptanceError("device lab evidence contract is invalid")
        _validate_device_lab_details(name, value["details"])
    return [
        f"{check}={root / DEVICE_ENDURANCE_EVIDENCE_NAMES[check]}"
        for check in DEVICE_ENDURANCE_LIFECYCLE_CHECKS
    ]


def _valid_device_appliance(value: object) -> bool:
    return (
        isinstance(value, dict)
        and set(value)
        == {
            "bundleVerified",
            "administratorLoginReady",
            "fileLifecycleVerified",
            "familyMemberIsolationVerified",
            "familyIdentitySetSha256",
            "familyPolicySetSha256",
            "agentWorkbenchVerified",
            "oneGiBTransferVerified",
            "containerRestartResumeVerified",
            "dockerControlApprovalVerified",
            "runtimeArchitecture",
            "transferSha256",
        }
        and all(
            value.get(name) is True
            for name in (
                "bundleVerified",
                "administratorLoginReady",
                "fileLifecycleVerified",
                "familyMemberIsolationVerified",
                "agentWorkbenchVerified",
                "oneGiBTransferVerified",
                "containerRestartResumeVerified",
                "dockerControlApprovalVerified",
            )
        )
        and value.get("runtimeArchitecture") in {"amd64", "arm64"}
        and isinstance(value.get("transferSha256"), str)
        and SHA256.fullmatch(value["transferSha256"]) is not None
        and isinstance(value.get("familyIdentitySetSha256"), str)
        and SHA256.fullmatch(value["familyIdentitySetSha256"]) is not None
        and isinstance(value.get("familyPolicySetSha256"), str)
        and SHA256.fullmatch(value["familyPolicySetSha256"]) is not None
    )


def _valid_boot_id(value: object) -> bool:
    try:
        return str(uuid.UUID(str(value))) == value
    except (ValueError, AttributeError):
        return False


def _validate_device_lab_details(name: str, details: dict[str, Any]) -> None:
    if name == "device-baseline.log":
        valid = (
            set(details)
            == {
                "installerCompleted",
                "installerSha256",
                "postWriteReadbackVerified",
                "firstColdBootHealthy",
                "bootId",
                "observedAtNs",
                "deviceIdentitySha256",
                "appliance",
            }
            and details["installerCompleted"] is True
            and isinstance(details["installerSha256"], str)
            and SHA256.fullmatch(details["installerSha256"]) is not None
            and details["postWriteReadbackVerified"] is True
            and details["firstColdBootHealthy"] is True
            and _valid_boot_id(details["bootId"])
            and isinstance(details["observedAtNs"], int)
            and not isinstance(details["observedAtNs"], bool)
            and details["observedAtNs"] > 0
            and isinstance(details["deviceIdentitySha256"], str)
            and SHA256.fullmatch(details["deviceIdentitySha256"]) is not None
            and _valid_device_appliance(details["appliance"])
        )
    elif name == "device-soak.log":
        valid = (
            set(details)
            == {
                "continuousRunStable",
                "sameBoot",
                "durationSeconds",
                "bootId",
                "observedAtNs",
                "appliance",
            }
            and details["continuousRunStable"] is True
            and details["sameBoot"] is True
            and isinstance(details["durationSeconds"], int)
            and not isinstance(details["durationSeconds"], bool)
            and details["durationSeconds"] >= 24 * 60 * 60
            and _valid_boot_id(details["bootId"])
            and isinstance(details["observedAtNs"], int)
            and not isinstance(details["observedAtNs"], bool)
            and details["observedAtNs"] > 0
            and _valid_device_appliance(details["appliance"])
        )
    elif name == "device-power-cut-armed.log":
        valid = (
            set(details)
            == {
                "physicalPowerCutArmed",
                "bootId",
                "intentSha256",
                "observedAtNs",
                "nextAction",
            }
            and details["physicalPowerCutArmed"] is True
            and _valid_boot_id(details["bootId"])
            and isinstance(details["intentSha256"], str)
            and SHA256.fullmatch(details["intentSha256"]) is not None
            and isinstance(details["observedAtNs"], int)
            and not isinstance(details["observedAtNs"], bool)
            and details["observedAtNs"] > 0
            and details["nextAction"] == "physically-remove-and-restore-power"
        )
    else:
        journal = details.get("journal")
        valid = (
            set(details)
            == {
                "hardPowerCycleRecovered",
                "bootIdChanged",
                "previousBootId",
                "currentBootId",
                "uncleanShutdownVerified",
                "observedAtNs",
                "journal",
                "appliance",
            }
            and details["hardPowerCycleRecovered"] is True
            and details["bootIdChanged"] is True
            and _valid_boot_id(details["previousBootId"])
            and _valid_boot_id(details["currentBootId"])
            and details["previousBootId"] != details["currentBootId"]
            and details["uncleanShutdownVerified"] is True
            and isinstance(details["observedAtNs"], int)
            and not isinstance(details["observedAtNs"], bool)
            and details["observedAtNs"] > 0
            and journal
            == {
                "persistentJournalAvailable": True,
                "powerCutIntentFound": True,
                "cleanShutdownFound": False,
            }
            and _valid_device_appliance(details["appliance"])
        )
    if not valid:
        raise PhysicalAcceptanceError(f"device lab evidence details are invalid: {name}")


def _valid_seed(value: object) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {"sha256", "size", "fileCount"}
        and isinstance(value.get("sha256"), str)
        and SHA256.fullmatch(value["sha256"]) is not None
        and value.get("size") == 64 * 1024 * 1024
        and value.get("fileCount") == 2
    )


def _validate_storage_lab_details(name: str, details: dict[str, Any]) -> None:
    if name == "storage-baseline.log":
        valid = (
            set(details)
            == {"smartHealthy", "smartDiskCount", "arrayHealthy", "activeMembers", "seed"}
            and details["smartHealthy"] is True
            and details["smartDiskCount"] == 2
            and details["arrayHealthy"] is True
            and details["activeMembers"] == 2
            and _valid_seed(details["seed"])
        )
    elif name == "storage-degraded.log":
        valid = details == {
            "memberDisconnected": True,
            "raidDegraded": True,
            "activeMembers": 1,
            "dataReadable": True,
        }
    elif name == "storage-readonly.log":
        valid = details == {
            "readOnlyObserved": True,
            "writeRejected": True,
            "readWriteRestored": True,
        }
    elif name == "storage-volume-full.log":
        valid = (
            set(details)
            == {"enospcObserved", "rejectedWrite", "cleanupRecovered", "allocatedBytes"}
            and details["enospcObserved"] is True
            and details["rejectedWrite"] is True
            and details["cleanupRecovered"] is True
            and isinstance(details["allocatedBytes"], int)
            and not isinstance(details["allocatedBytes"], bool)
            and details["allocatedBytes"] > 0
        )
    elif name == "storage-reconnect.log":
        valid = details == {"sameMemberReconnected": True, "rebuildStarted": True}
    elif name == "storage-rebuild.log":
        valid = (
            set(details) == {"raidRebuildCompleted", "activeMembers", "dataPreserved", "seed"}
            and details["raidRebuildCompleted"] is True
            and details["activeMembers"] == 2
            and details["dataPreserved"] is True
            and _valid_seed(details["seed"])
        )
    elif name == "storage-reboot.log":
        valid = details == {
            "bootIdChanged": True,
            "arrayHealthy": True,
            "mountedReadWrite": True,
            "dataPreserved": True,
        }
    else:
        valid = (
            set(details) == {"bytes", "sha256", "restoreVerified", "finalState"}
            and details["bytes"] == 1024 * 1024 * 1024
            and isinstance(details["sha256"], str)
            and SHA256.fullmatch(details["sha256"]) is not None
            and details["restoreVerified"] is True
            and details["finalState"] == "recoverable-trash"
        )
    if not valid:
        raise PhysicalAcceptanceError(f"storage lab evidence details are invalid: {name}")


def _validate_operations_lab_details(name: str, details: dict[str, Any]) -> None:
    if name == "operations-install-rollback.log":
        valid = details == {"baselineRestored": True}
    elif name == "operations-install.log":
        valid = (
            set(details) == {"installed", "installedAtNs", "platform", "timerTriggers", "unitState"}
            and details["installed"] is True
            and isinstance(details["installedAtNs"], int)
            and not isinstance(details["installedAtNs"], bool)
            and details["installedAtNs"] > 0
            and isinstance(details["platform"], dict)
            and set(details["platform"]) == {"id", "versionId", "omvVersion"}
            and details["platform"].get("id") == "debian"
            and details["platform"].get("versionId") == "13"
            and isinstance(details["platform"].get("omvVersion"), str)
            and details["platform"]["omvVersion"].startswith("8.")
            and isinstance(details["timerTriggers"], dict)
            and isinstance(details["unitState"], dict)
        )
    elif name in {"backup-timer.log", "audit-timer.log"}:
        product = details.get("product")
        valid = (
            set(details) == {"timer", "lastTriggerChanged", "serviceResult", "product"}
            and isinstance(details["timer"], str)
            and details["lastTriggerChanged"] is True
            and details["serviceResult"] == "success"
            and isinstance(product, dict)
            and set(product) == {"name", "sha256", "size"}
            and isinstance(product["name"], str)
            and ARTIFACT_NAME.fullmatch(product["name"]) is not None
            and isinstance(product["sha256"], str)
            and SHA256.fullmatch(product["sha256"]) is not None
            and isinstance(product["size"], int)
            and not isinstance(product["size"], bool)
            and product["size"] > 0
        )
    elif name in {"backup-mount-loss.log", "audit-mount-loss.log"}:
        valid = (
            set(details) == {"service", "failedReturnCode", "fallbackWriteAbsent", "mountRestored"}
            and isinstance(details["service"], str)
            and isinstance(details["failedReturnCode"], int)
            and not isinstance(details["failedReturnCode"], bool)
            and details["failedReturnCode"] != 0
            and details["fallbackWriteAbsent"] is True
            and details["mountRestored"] is True
        )
    elif name == "operations-remove-rollback.log":
        valid = details == {"installedStateRestored": True}
    else:
        preserved = details.get("preserved")
        credentials = details.get("credentials")
        valid = (
            set(details) == {"removed", "unitsAndTimersAbsent", "credentials", "preserved"}
            and details["removed"] is True
            and details["unitsAndTimersAbsent"] is True
            and isinstance(credentials, dict)
            and set(credentials) == {"backup", "audit"}
            and all(
                isinstance(record, dict)
                and set(record) == {"sha256"}
                and isinstance(record["sha256"], str)
                and SHA256.fullmatch(record["sha256"]) is not None
                for record in credentials.values()
            )
            and isinstance(preserved, dict)
            and set(preserved) == OPERATIONS_LAB_PRESERVED
            and all(
                isinstance(record, dict)
                and set(record) == {"sha256", "size"}
                and isinstance(record["sha256"], str)
                and SHA256.fullmatch(record["sha256"]) is not None
                and isinstance(record["size"], int)
                and not isinstance(record["size"], bool)
                and record["size"] > 0
                for record in preserved.values()
            )
        )
    if not valid:
        raise PhysicalAcceptanceError(f"operations lab evidence details are invalid: {name}")


def build_manifest(
    *,
    candidate_index: Path,
    gate: str,
    architecture: str,
    hardware_profile_sha256: str,
    device_count: int,
    lab_run_id: str,
    started_at: str,
    finished_at: str,
    primary_log: Path,
    artifacts: list[Path],
    output: Path,
) -> dict[str, Any]:
    candidate = _candidate(candidate_index)
    if gate not in GATE_REQUIREMENTS:
        raise PhysicalAcceptanceError("physical gate name is invalid")
    requirement = GATE_REQUIREMENTS[gate]
    if (
        architecture not in {"x86_64", "arm64"}
        or (requirement["architecture"] is not None and architecture != requirement["architecture"])
        or SHA256.fullmatch(hardware_profile_sha256) is None
        or not isinstance(device_count, int)
        or isinstance(device_count, bool)
        or device_count < int(requirement["minimumDevices"])
    ):
        raise PhysicalAcceptanceError("physical gate hardware fields are invalid")
    try:
        parsed_run_id = uuid.UUID(lab_run_id)
    except ValueError as exc:
        raise PhysicalAcceptanceError("lab run ID must be one canonical UUIDv4") from exc
    if parsed_run_id.version != 4 or str(parsed_run_id) != lab_run_id:
        raise PhysicalAcceptanceError("lab run ID must be one canonical UUIDv4")
    started = _utc_time(started_at, "physical gate start")
    finished = _utc_time(finished_at, "physical gate finish")
    duration = finished - started
    minimum_duration = timedelta(seconds=int(requirement["minimumDurationSeconds"]))
    if not minimum_duration <= duration <= timedelta(days=7):
        raise PhysicalAcceptanceError("physical gate execution duration is invalid")

    if output.name != "evidence.json" or output.parent.is_symlink():
        raise PhysicalAcceptanceError("physical manifest output must be a new evidence.json")
    evidence_directory = output.parent.resolve(strict=True)
    if not evidence_directory.is_dir():
        raise PhysicalAcceptanceError("physical gate evidence directory is unavailable")
    resolved_primary = primary_log.resolve(strict=True)
    if resolved_primary.parent != evidence_directory:
        raise PhysicalAcceptanceError("primary log must already be inside the evidence directory")
    if Path(resolved_primary.name).suffix.casefold() != ".log":
        raise PhysicalAcceptanceError("primary log must use a .log filename")
    if not 1 <= len(artifacts) <= 32:
        raise PhysicalAcceptanceError("physical artifact count must be between 1 and 32")

    paths: dict[str, Path] = {}
    for path in artifacts:
        resolved = path.resolve(strict=True)
        name = resolved.name
        if (
            resolved.parent != evidence_directory
            or ARTIFACT_NAME.fullmatch(name) is None
            or name in {"evidence.json", "evidence.json.gpg"}
            or name in paths
            or path.is_symlink()
        ):
            raise PhysicalAcceptanceError("physical artifact path is unsafe or duplicated")
        paths[name] = resolved
    if resolved_primary.name not in paths:
        raise PhysicalAcceptanceError("primary log must be included in the artifact list")
    if HARDWARE_PROFILE_NAME not in paths:
        raise PhysicalAcceptanceError(f"artifact list must include {HARDWARE_PROFILE_NAME}")
    if GATE_RESULT_NAME not in paths:
        raise PhysicalAcceptanceError(f"artifact list must include {GATE_RESULT_NAME}")
    if gate == OPERATIONS_SYSTEMD_GATE:
        if OPERATIONS_SYSTEMD_LIFECYCLE_NAME not in paths:
            raise PhysicalAcceptanceError(
                f"artifact list must include {OPERATIONS_SYSTEMD_LIFECYCLE_NAME}"
            )
        if POWER_STATE_LIFECYCLE_NAME not in paths:
            raise PhysicalAcceptanceError(
                f"artifact list must include {POWER_STATE_LIFECYCLE_NAME}"
            )
    elif OPERATIONS_SYSTEMD_LIFECYCLE_NAME in paths:
        raise PhysicalAcceptanceError(
            f"only {OPERATIONS_SYSTEMD_GATE} may include {OPERATIONS_SYSTEMD_LIFECYCLE_NAME}"
        )
    if gate != OPERATIONS_SYSTEMD_GATE and POWER_STATE_LIFECYCLE_NAME in paths:
        raise PhysicalAcceptanceError(
            f"only {OPERATIONS_SYSTEMD_GATE} may include {POWER_STATE_LIFECYCLE_NAME}"
        )
    if gate == STORAGE_RECOVERY_GATE:
        if STORAGE_RECOVERY_LIFECYCLE_NAME not in paths:
            raise PhysicalAcceptanceError(
                f"artifact list must include {STORAGE_RECOVERY_LIFECYCLE_NAME}"
            )
    elif STORAGE_RECOVERY_LIFECYCLE_NAME in paths:
        raise PhysicalAcceptanceError(
            f"only {STORAGE_RECOVERY_GATE} may include {STORAGE_RECOVERY_LIFECYCLE_NAME}"
        )
    if gate == PROTOCOL_INTEROPERABILITY_GATE:
        if PROTOCOL_INTEROPERABILITY_LIFECYCLE_NAME not in paths:
            raise PhysicalAcceptanceError(
                f"artifact list must include {PROTOCOL_INTEROPERABILITY_LIFECYCLE_NAME}"
            )
    elif PROTOCOL_INTEROPERABILITY_LIFECYCLE_NAME in paths:
        raise PhysicalAcceptanceError(
            f"only {PROTOCOL_INTEROPERABILITY_GATE} may include "
            f"{PROTOCOL_INTEROPERABILITY_LIFECYCLE_NAME}"
        )
    if gate in DEVICE_ENDURANCE_GATES:
        if DEVICE_ENDURANCE_LIFECYCLE_NAME not in paths:
            raise PhysicalAcceptanceError(
                f"artifact list must include {DEVICE_ENDURANCE_LIFECYCLE_NAME}"
            )
        if HUB_LIFECYCLE_PLAN_NAME not in paths or HUB_LIFECYCLE_RESULT_NAME not in paths:
            raise PhysicalAcceptanceError(
                "artifact list must include the Hub nine-app lifecycle plan and result"
            )
        if (
            PAPERLESS_FUNCTIONAL_PLAN_NAME not in paths
            or PAPERLESS_FUNCTIONAL_RESULT_NAME not in paths
        ):
            raise PhysicalAcceptanceError(
                "artifact list must include the Paperless functional plan and result"
            )
        if (
            LAN_DISCOVERY_FUNCTIONAL_PLAN_NAME not in paths
            or LAN_DISCOVERY_FUNCTIONAL_RESULT_NAME not in paths
        ):
            raise PhysicalAcceptanceError(
                "artifact list must include the LAN discovery functional plan and result"
            )
        if not set(LAN_DISCOVERY_PROBE_NAMES) <= set(paths):
            raise PhysicalAcceptanceError(
                "artifact list must include all LAN discovery probe evidence"
            )
    elif (
        DEVICE_ENDURANCE_LIFECYCLE_NAME in paths
        or HUB_LIFECYCLE_PLAN_NAME in paths
        or HUB_LIFECYCLE_RESULT_NAME in paths
        or PAPERLESS_FUNCTIONAL_PLAN_NAME in paths
        or PAPERLESS_FUNCTIONAL_RESULT_NAME in paths
        or LAN_DISCOVERY_FUNCTIONAL_PLAN_NAME in paths
        or LAN_DISCOVERY_FUNCTIONAL_RESULT_NAME in paths
        or any(name in paths for name in LAN_DISCOVERY_PROBE_NAMES)
    ):
        raise PhysicalAcceptanceError(
            "only device gates may include device, Hub, Paperless or LAN functional evidence"
        )
    if gate == BARE_METAL_GATE:
        if BARE_METAL_LIFECYCLE_NAME not in paths:
            raise PhysicalAcceptanceError(f"artifact list must include {BARE_METAL_LIFECYCLE_NAME}")
    elif BARE_METAL_LIFECYCLE_NAME in paths:
        raise PhysicalAcceptanceError(
            f"only {BARE_METAL_GATE} may include {BARE_METAL_LIFECYCLE_NAME}"
        )
    actual_files = {entry.name for entry in evidence_directory.iterdir()}
    if actual_files != set(paths) or any(
        entry.is_symlink() or not entry.is_file() for entry in evidence_directory.iterdir()
    ):
        raise PhysicalAcceptanceError(
            "evidence directory must contain exactly the declared physical artifacts"
        )

    expected_marker = _expected_marker(gate, candidate)
    records: list[dict[str, Any]] = []
    artifact_bytes: dict[str, bytes] = {}
    total_size = 0
    for name in sorted(paths):
        raw = _read_regular(paths[name], MAX_ARTIFACT_BYTES, f"physical artifact {name}")
        _privacy_scan(name, raw)
        total_size += len(raw)
        if total_size > MAX_TOTAL_GATE_BYTES:
            raise PhysicalAcceptanceError("physical artifact set exceeds its total size bound")
        if name == resolved_primary.name:
            try:
                text = raw.decode("utf-8")
            except UnicodeError as exc:
                raise PhysicalAcceptanceError("primary log must be UTF-8") from exc
            if text.splitlines().count(expected_marker) != 1:
                raise PhysicalAcceptanceError("primary log must contain one exact success marker")
        artifact_bytes[name] = raw
        records.append({"name": name, "sha256": _sha256(raw), "size": len(raw)})
    profile_record = next(record for record in records if record["name"] == HARDWARE_PROFILE_NAME)
    if profile_record["sha256"] != hardware_profile_sha256:
        raise PhysicalAcceptanceError(
            f"hardware profile digest does not match {HARDWARE_PROFILE_NAME}"
        )
    profile_raw = _read_regular(
        paths[HARDWARE_PROFILE_NAME], MAX_ARTIFACT_BYTES, "physical hardware profile"
    )
    _validate_hardware_profile(
        profile_raw,
        gate=gate,
        architecture=architecture,
        device_count=device_count,
    )
    _validate_gate_result(
        _read_regular(paths[GATE_RESULT_NAME], MAX_ARTIFACT_BYTES, "physical gate result"),
        gate=gate,
    )
    if gate == OPERATIONS_SYSTEMD_GATE:
        _validate_operations_systemd_lifecycle(
            _read_regular(
                paths[OPERATIONS_SYSTEMD_LIFECYCLE_NAME],
                MAX_ARTIFACT_BYTES,
                "operations systemd lifecycle evidence",
            ),
            artifacts={record["name"]: record for record in records},
            candidate=candidate,
        )
        _validate_power_state_lifecycle(
            _read_regular(
                paths[POWER_STATE_LIFECYCLE_NAME],
                MAX_ARTIFACT_BYTES,
                "power/state lifecycle evidence",
            ),
            artifacts={record["name"]: record for record in records},
            artifact_bytes=artifact_bytes,
            candidate=candidate,
        )
    if gate == STORAGE_RECOVERY_GATE:
        _validate_storage_recovery_lifecycle(
            _read_regular(
                paths[STORAGE_RECOVERY_LIFECYCLE_NAME],
                MAX_ARTIFACT_BYTES,
                "storage recovery lifecycle evidence",
            ),
            artifacts={record["name"]: record for record in records},
            candidate=candidate,
        )
    if gate == PROTOCOL_INTEROPERABILITY_GATE:
        _validate_protocol_interoperability_lifecycle(
            _read_regular(
                paths[PROTOCOL_INTEROPERABILITY_LIFECYCLE_NAME],
                MAX_ARTIFACT_BYTES,
                "protocol interoperability lifecycle evidence",
            ),
            artifacts={record["name"]: record for record in records},
            candidate=candidate,
        )
    if gate in DEVICE_ENDURANCE_GATES:
        try:
            hub_lab.validate_evidence_bytes(
                artifact_bytes[HUB_LIFECYCLE_PLAN_NAME],
                artifact_bytes[HUB_LIFECYCLE_RESULT_NAME],
                expected_candidate=candidate,
            )
        except hub_lab.HubLifecycleLabError as exc:
            raise PhysicalAcceptanceError(
                f"Hub nine-app lifecycle evidence is invalid: {exc}"
            ) from exc
        try:
            paperless_lab.validate_evidence_bytes(
                artifact_bytes[PAPERLESS_FUNCTIONAL_PLAN_NAME],
                artifact_bytes[PAPERLESS_FUNCTIONAL_RESULT_NAME],
                expected_candidate=candidate,
            )
        except paperless_lab.PaperlessFunctionalLabError as exc:
            raise PhysicalAcceptanceError(
                f"Paperless OCR/Office functional evidence is invalid: {exc}"
            ) from exc
        try:
            lan_plan, lan_result = lan_discovery_lab.validate_evidence_bytes(
                artifact_bytes[LAN_DISCOVERY_FUNCTIONAL_PLAN_NAME],
                artifact_bytes[LAN_DISCOVERY_FUNCTIONAL_RESULT_NAME],
                expected_candidate=candidate,
            )
            lan_discovery_lab.validate_probe_artifacts(
                lan_plan,
                lan_result,
                {name: artifact_bytes[name] for name in LAN_DISCOVERY_PROBE_NAMES},
            )
        except lan_discovery_lab.LanDiscoveryFunctionalLabError as exc:
            raise PhysicalAcceptanceError(
                f"LAN discovery functional evidence is invalid: {exc}"
            ) from exc
        _validate_device_endurance_lifecycle(
            _read_regular(
                paths[DEVICE_ENDURANCE_LIFECYCLE_NAME],
                MAX_ARTIFACT_BYTES,
                "device endurance lifecycle evidence",
            ),
            artifacts={record["name"]: record for record in records},
            candidate=candidate,
            gate=gate,
        )
    if gate == BARE_METAL_GATE:
        _validate_bare_metal_lifecycle(
            _read_regular(
                paths[BARE_METAL_LIFECYCLE_NAME],
                MAX_ARTIFACT_BYTES,
                "bare-metal recovery lifecycle evidence",
            ),
            artifacts={record["name"]: record for record in records},
            artifact_bytes=artifact_bytes,
            candidate=candidate,
        )

    manifest = {
        "schemaVersion": 1,
        "kind": "echo.physical-acceptance-gate",
        "gate": gate,
        "candidate": {
            "indexId": candidate["indexId"],
            "sourceRevision": candidate["sourceRevision"],
            "agentRevision": candidate["agentRevision"],
            "releaseTag": candidate["releaseTag"],
        },
        "execution": {
            "labRunId": lab_run_id,
            "startedAt": started_at,
            "finishedAt": finished_at,
        },
        "hardware": {
            "architecture": architecture,
            "profileSha256": hardware_profile_sha256,
            "deviceCount": device_count,
            "serialsRedacted": True,
        },
        "result": {
            "passed": True,
            "marker": expected_marker,
            "deliveryRequirements": list(requirement["deliveryRequirements"]),
        },
        "primaryLog": resolved_primary.name,
        "artifacts": records,
    }
    _write_new(output, manifest)
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    marker_parser = subparsers.add_parser("marker")
    marker_parser.add_argument("--candidate-index", type=Path, required=True)
    marker_parser.add_argument("--gate", choices=PHYSICAL_GATES, required=True)

    plan_parser = subparsers.add_parser("plan")
    plan_parser.add_argument("--candidate-index", type=Path, required=True)
    plan_parser.add_argument("--output", type=Path, required=True)

    verify_plan_parser = subparsers.add_parser("verify-plan")
    verify_plan_parser.add_argument("--candidate-index", type=Path, required=True)
    verify_plan_parser.add_argument("--plan", type=Path, required=True)

    profile_parser = subparsers.add_parser("profile")
    profile_parser.add_argument("--gate", choices=PHYSICAL_GATES, required=True)
    profile_parser.add_argument("--architecture", choices=("x86_64", "arm64"), required=True)
    profile_parser.add_argument("--device-count", type=int, required=True)
    profile_parser.add_argument("--output", type=Path, required=True)

    result_parser = subparsers.add_parser("result")
    result_parser.add_argument("--gate", choices=PHYSICAL_GATES, required=True)
    result_parser.add_argument(
        "--pass-check", choices=ALL_RESULT_CHECKS, action="append", required=True
    )
    result_parser.add_argument("--output", type=Path, required=True)

    operations_parser = subparsers.add_parser("operations-result")
    operations_parser.add_argument("--gate", choices=(OPERATIONS_SYSTEMD_GATE,), required=True)
    operations_parser.add_argument("--candidate-index", type=Path, required=True)
    operations_parser.add_argument("--lab-plan", type=Path, required=True)
    operations_source = operations_parser.add_mutually_exclusive_group(required=True)
    operations_source.add_argument(
        "--evidence",
        action="append",
        metavar="CHECK=PATH",
    )
    operations_source.add_argument("--lab-directory", type=Path)
    operations_parser.add_argument("--output", type=Path, required=True)

    power_parser = subparsers.add_parser("power-result")
    power_parser.add_argument("--gate", choices=(OPERATIONS_SYSTEMD_GATE,), required=True)
    power_parser.add_argument("--candidate-index", type=Path, required=True)
    power_parser.add_argument("--lab-plan", type=Path, required=True)
    power_parser.add_argument("--lab-directory", type=Path, required=True)
    power_parser.add_argument("--output", type=Path, required=True)

    bare_metal_parser = subparsers.add_parser("bare-metal-result")
    bare_metal_parser.add_argument("--gate", choices=(BARE_METAL_GATE,), required=True)
    bare_metal_parser.add_argument("--candidate-index", type=Path, required=True)
    bare_metal_parser.add_argument("--lab-plan", type=Path, required=True)
    bare_metal_parser.add_argument("--lab-directory", type=Path, required=True)
    bare_metal_parser.add_argument("--output", type=Path, required=True)

    storage_parser = subparsers.add_parser("storage-result")
    storage_parser.add_argument("--gate", choices=(STORAGE_RECOVERY_GATE,), required=True)
    storage_parser.add_argument("--candidate-index", type=Path, required=True)
    storage_parser.add_argument("--lab-plan", type=Path, required=True)
    storage_source = storage_parser.add_mutually_exclusive_group(required=True)
    storage_source.add_argument(
        "--evidence",
        action="append",
        metavar="CHECK=PATH",
    )
    storage_source.add_argument("--lab-directory", type=Path)
    storage_parser.add_argument("--output", type=Path, required=True)

    device_parser = subparsers.add_parser("device-result")
    device_parser.add_argument("--gate", choices=sorted(DEVICE_ENDURANCE_GATES), required=True)
    device_parser.add_argument("--candidate-index", type=Path, required=True)
    device_parser.add_argument("--lab-plan", type=Path, required=True)
    device_source = device_parser.add_mutually_exclusive_group(required=True)
    device_source.add_argument(
        "--evidence",
        action="append",
        metavar="CHECK=PATH",
    )
    device_source.add_argument("--lab-directory", type=Path)
    device_parser.add_argument("--output", type=Path, required=True)

    build_parser = subparsers.add_parser("build")
    build_parser.add_argument("--candidate-index", type=Path, required=True)
    build_parser.add_argument("--gate", choices=PHYSICAL_GATES, required=True)
    build_parser.add_argument("--architecture", choices=("x86_64", "arm64"), required=True)
    build_parser.add_argument("--hardware-profile-sha256", required=True)
    build_parser.add_argument("--device-count", type=int, required=True)
    build_parser.add_argument("--lab-run-id", required=True)
    build_parser.add_argument("--started-at", required=True)
    build_parser.add_argument("--finished-at", required=True)
    build_parser.add_argument("--primary-log", type=Path, required=True)
    build_parser.add_argument("--artifact", type=Path, action="append", required=True)
    build_parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "marker":
            print(marker(args.candidate_index, args.gate))
            return 0
        if args.command == "plan":
            plan = build_lab_plan(candidate_index=args.candidate_index, output=args.output)
            print(
                "ECHO_PHYSICAL_LAB_PLAN_READY "
                f"candidate={plan['candidate']['indexId']} gates={len(plan['gates'])} "
                f"plan={plan['planId']}"
            )
            return 0
        if args.command == "verify-plan":
            plan = verify_lab_plan(candidate_index=args.candidate_index, plan_path=args.plan)
            print(
                "ECHO_PHYSICAL_LAB_PLAN_OK "
                f"candidate={plan['candidate']['indexId']} gates={len(plan['gates'])} "
                f"plan={plan['planId']}"
            )
            return 0
        if args.command == "profile":
            profile = build_hardware_profile(
                gate=args.gate,
                architecture=args.architecture,
                device_count=args.device_count,
                output=args.output,
            )
            print(
                "ECHO_PHYSICAL_HARDWARE_PROFILE_READY "
                f"gate={profile['gate']} architecture={profile['architecture']} "
                f"devices={profile['deviceCount']}"
            )
            return 0
        if args.command == "result":
            result = build_gate_result(
                gate=args.gate,
                passed_checks=args.pass_check,
                output=args.output,
            )
            print(
                "ECHO_PHYSICAL_GATE_RESULT_READY "
                f"gate={result['gate']} checks={len(result['checks'])}"
            )
            return 0
        if args.command == "operations-result":
            evidence_arguments = (
                operations_lab_evidence_arguments(
                    args.lab_directory,
                    candidate_index=args.candidate_index,
                    lab_plan=args.lab_plan,
                )
                if args.lab_directory is not None
                else args.evidence
            )
            result = build_operations_systemd_lifecycle(
                candidate_index=args.candidate_index,
                lab_plan=args.lab_plan,
                gate=args.gate,
                evidence_arguments=evidence_arguments,
                output=args.output,
            )
            print(
                "ECHO_OPERATIONS_SYSTEMD_LIFECYCLE_READY "
                f"gate={result['gate']} checks={len(result['checks'])}"
            )
            return 0
        if args.command == "storage-result":
            evidence_arguments = (
                storage_lab_evidence_arguments(
                    args.lab_directory,
                    candidate_index=args.candidate_index,
                    lab_plan=args.lab_plan,
                )
                if args.lab_directory is not None
                else args.evidence
            )
            result = build_storage_recovery_lifecycle(
                candidate_index=args.candidate_index,
                lab_plan=args.lab_plan,
                gate=args.gate,
                evidence_arguments=evidence_arguments,
                output=args.output,
            )
            print(
                "ECHO_STORAGE_RECOVERY_LIFECYCLE_READY "
                f"gate={result['gate']} checks={len(result['checks'])}"
            )
            return 0
        if args.command == "power-result":
            result = build_power_state_lifecycle(
                candidate_index=args.candidate_index,
                lab_plan=args.lab_plan,
                gate=args.gate,
                evidence_directory=args.lab_directory,
                output=args.output,
            )
            print(
                "ECHO_POWER_STATE_LIFECYCLE_READY "
                f"gate={result['gate']} checks={len(result['checks'])}"
            )
            return 0
        if args.command == "bare-metal-result":
            result = build_bare_metal_lifecycle(
                candidate_index=args.candidate_index,
                lab_plan=args.lab_plan,
                gate=args.gate,
                evidence_directory=args.lab_directory,
                output=args.output,
            )
            print(
                "ECHO_BARE_METAL_RECOVERY_LIFECYCLE_READY "
                f"gate={result['gate']} checks={len(result['checks'])}"
            )
            return 0
        if args.command == "device-result":
            evidence_arguments = (
                device_lab_evidence_arguments(
                    args.lab_directory,
                    candidate_index=args.candidate_index,
                    lab_plan=args.lab_plan,
                    gate=args.gate,
                )
                if args.lab_directory is not None
                else args.evidence
            )
            result = build_device_endurance_lifecycle(
                candidate_index=args.candidate_index,
                lab_plan=args.lab_plan,
                gate=args.gate,
                evidence_arguments=evidence_arguments,
                output=args.output,
            )
            print(
                "ECHO_DEVICE_ENDURANCE_LIFECYCLE_READY "
                f"gate={result['gate']} checks={len(result['checks'])}"
            )
            return 0
        manifest = build_manifest(
            candidate_index=args.candidate_index,
            gate=args.gate,
            architecture=args.architecture,
            hardware_profile_sha256=args.hardware_profile_sha256,
            device_count=args.device_count,
            lab_run_id=args.lab_run_id,
            started_at=args.started_at,
            finished_at=args.finished_at,
            primary_log=args.primary_log,
            artifacts=args.artifact,
            output=args.output,
        )
    except (OSError, PhysicalAcceptanceError) as exc:
        print(f"Echo physical acceptance capture failed: {exc}", file=sys.stderr)
        return 1
    print(
        "ECHO_PHYSICAL_GATE_MANIFEST_READY "
        f"gate={manifest['gate']} candidate={manifest['candidate']['indexId']} "
        f"artifacts={len(manifest['artifacts'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
