#!/usr/bin/env python3
"""Verify six signed physical gates and emit the final NAS delivery decision."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess  # nosec B404
import sys
import tempfile
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Keep the standalone acceptance verifier runnable with the macOS system
# Python used by release operators.  ``datetime.UTC`` was added in Python 3.11,
# while ``timezone.utc`` has the same identity and comparison semantics on the
# older interpreter.  The repository can retain its Python 3.11 development
# baseline without making the evidence CLI fail before it can even print help.
UTC = timezone.utc  # noqa: UP017 - standalone release operators may use Python 3.10

# The documented repository command executes this file by path. Python then
# exposes deploy/appliance (not the repository root) on sys.path, so package
# imports would otherwise fail before argument parsing. Keep standalone
# release-bundle copies on their local-import path.
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if (
    (_REPOSITORY_ROOT / "deploy/appliance/release_evidence_index.py").is_file()
    and (_REPOSITORY_ROOT / "deploy/installer/verify_public_keyring.py").is_file()
    and str(_REPOSITORY_ROOT) not in sys.path
):
    sys.path.insert(0, str(_REPOSITORY_ROOT))

try:
    from deploy.appliance import hub_lifecycle_lab as hub_lab
    from deploy.appliance import lan_discovery_functional_lab as lan_discovery_lab
    from deploy.appliance import paperless_functional_lab as paperless_lab
    from deploy.appliance.release_evidence_index import PHYSICAL_GATES
    from deploy.installer.verify_public_keyring import (
        PublicKeyringError,
        verify_public_keyring_bytes,
    )
except ModuleNotFoundError:
    import hub_lifecycle_lab as hub_lab
    import lan_discovery_functional_lab as lan_discovery_lab
    import paperless_functional_lab as paperless_lab
    from release_evidence_index import PHYSICAL_GATES
    from verify_public_keyring import PublicKeyringError, verify_public_keyring_bytes

SCHEMA_VERSION = 2
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
MAX_TOTAL_GATE_BYTES = 256 * 1024 * 1024
SHA1 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
OPENPGP_FINGERPRINT = re.compile(r"^[0-9A-F]{40,64}$")
RELEASE_TAG = re.compile(r"^echo-appliance-v[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$")
UTC_TIMESTAMP = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$"
)
ARTIFACT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
IMMUTABLE_IMAGE_REFERENCE = re.compile(r"^[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64}$")
FORBIDDEN_TEXT = re.compile(
    r"(?:/dev/disk/by-id/|\b(?:serial|wwn|password|passwd|token|secret|authorization)\s*[:=])",
    re.IGNORECASE,
)
TEXT_SUFFIXES = {".json", ".log", ".txt", ".md", ".csv", ".tsv"}
HARDWARE_PROFILE_NAME = "hardware-profile.json"
GATE_RESULT_NAME = "gate-result.json"
OPERATIONS_SYSTEMD_LIFECYCLE_NAME = "operations-systemd-lifecycle.json"
OPERATIONS_SYSTEMD_GATE = "power_loss_during_update_and_state_restore"
OPERATIONS_SYSTEMD_LIFECYCLE_CHECKS = (
    "operationsSystemdInstalled",
    "operationsSystemdInstallRollbackVerified",
    "backupTimerTriggered",
    "auditTimerTriggered",
    "missingBackupMountFailedClosed",
    "missingAuditMountFailedClosed",
    "operationsSystemdRemovalLeftNoUnitsOrTimers",
    "operationsSystemdRemovalPreservedCredentialsAndData",
    "operationsSystemdRemovalRollbackVerified",
)
POWER_STATE_LIFECYCLE_NAME = "power-state-lifecycle.json"
POWER_STATE_LIFECYCLE_CHECKS = (
    "immutableDigestUpgradeVerified",
    "updatePowerLossRolledBack",
    "failedUpgradeRollbackVerified",
    "managedUninstallDataPreserved",
    "stateRestoreCommitted",
    "dataPreserved",
)
POWER_STATE_PHASES = (
    "baseline",
    "arm-power-cut",
    "recover-power-cut",
    "upgrade-success",
    "upgrade-failure",
    "managed-uninstall",
    "state-restore",
)
POWER_STATE_PHASE_EVIDENCE_NAMES = {
    "baseline": "power-state-baseline.log",
    "arm-power-cut": "power-update-cut-armed.log",
    "recover-power-cut": "power-update-cut-recovered.log",
    "upgrade-success": "power-upgrade-success.log",
    "upgrade-failure": "power-upgrade-failure.log",
    "managed-uninstall": "power-managed-uninstall.log",
    "state-restore": "power-state-restore.log",
}
POWER_STATE_EVIDENCE_NAMES = {
    "immutableDigestUpgradeVerified": POWER_STATE_PHASE_EVIDENCE_NAMES["upgrade-success"],
    "updatePowerLossRolledBack": POWER_STATE_PHASE_EVIDENCE_NAMES["recover-power-cut"],
    "failedUpgradeRollbackVerified": POWER_STATE_PHASE_EVIDENCE_NAMES["upgrade-failure"],
    "managedUninstallDataPreserved": POWER_STATE_PHASE_EVIDENCE_NAMES["managed-uninstall"],
    "stateRestoreCommitted": POWER_STATE_PHASE_EVIDENCE_NAMES["state-restore"],
    "dataPreserved": POWER_STATE_PHASE_EVIDENCE_NAMES["state-restore"],
}
POWER_STATE_CANARY_BYTES = 1024 * 1024
POWER_STATE_NAS_CANARY_BYTES = 1024 * 1024 * 1024
BARE_METAL_GATE = "recovery_media_bare_metal_restore"
BARE_METAL_LIFECYCLE_NAME = "bare-metal-recovery-lifecycle.json"
BARE_METAL_LIFECYCLE_CHECKS = (
    "recoveryMediaVerified",
    "bareMetalRestored",
    "coldBootHealthy",
    "dataVerified",
    "offDeviceBackupRestored",
    "authenticationStateVerified",
    "auditStateVerified",
    "agentStateVerified",
    "nasDataVerified",
)
BARE_METAL_PHASES = (
    "source-backup",
    "recovery-install",
    "cold-boot",
    "restore",
    "recovery-promote",
    "trial-verify",
    "recovery-commit",
    "final-verify",
)
BARE_METAL_PHASE_EVIDENCE_NAMES = {
    "source-backup": "bare-metal-source-backup.log",
    "recovery-install": "bare-metal-install.log",
    "cold-boot": "bare-metal-cold-boot.log",
    "restore": "bare-metal-data-restore.log",
    "recovery-promote": "bare-metal-promote.log",
    "trial-verify": "bare-metal-trial-verify.log",
    "recovery-commit": "bare-metal-commit.log",
    "final-verify": "bare-metal-final-verify.log",
}
BARE_METAL_EVIDENCE_NAMES = {
    "recoveryMediaVerified": BARE_METAL_PHASE_EVIDENCE_NAMES["recovery-install"],
    "bareMetalRestored": BARE_METAL_PHASE_EVIDENCE_NAMES["recovery-install"],
    "coldBootHealthy": BARE_METAL_PHASE_EVIDENCE_NAMES["final-verify"],
    "dataVerified": BARE_METAL_PHASE_EVIDENCE_NAMES["final-verify"],
    "offDeviceBackupRestored": BARE_METAL_PHASE_EVIDENCE_NAMES["restore"],
    "authenticationStateVerified": BARE_METAL_PHASE_EVIDENCE_NAMES["final-verify"],
    "auditStateVerified": BARE_METAL_PHASE_EVIDENCE_NAMES["final-verify"],
    "agentStateVerified": BARE_METAL_PHASE_EVIDENCE_NAMES["final-verify"],
    "nasDataVerified": BARE_METAL_PHASE_EVIDENCE_NAMES["final-verify"],
}
BARE_METAL_STATE_CANARY_BYTES = 1024 * 1024
BARE_METAL_AGENT_CANARY_BYTES = 1024 * 1024
BARE_METAL_NAS_CANARY_BYTES = 1024 * 1024 * 1024
STORAGE_RECOVERY_LIFECYCLE_NAME = "storage-recovery-lifecycle.json"
STORAGE_RECOVERY_GATE = "real_disk_smart_and_raid_degradation_recovery"
STORAGE_RECOVERY_LIFECYCLE_CHECKS = (
    "smartHealthy",
    "diskDisconnectObserved",
    "raidDegradationObserved",
    "filesystemReadOnlyHandled",
    "volumeFullHandled",
    "rebootRecoveryVerified",
    "recycleBinRestoreVerified",
    "raidRebuildCompleted",
    "dataPreserved",
)
STORAGE_RECOVERY_EVIDENCE_NAMES = {
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
STORAGE_RECOVERY_PHASE_EVIDENCE_NAMES = {
    *STORAGE_RECOVERY_EVIDENCE_NAMES.values(),
    "storage-reconnect.log",
}
PROTOCOL_INTEROPERABILITY_LIFECYCLE_NAME = "protocol-interoperability-lifecycle.json"
PROTOCOL_INTEROPERABILITY_GATE = "external_smb_and_nfs_client_interoperability"
PROTOCOL_INTEROPERABILITY_LIFECYCLE_CHECKS = (
    "windowsSmbReadWrite",
    "macosSmbReadWrite",
    "linuxSmbReadWrite",
    "macosNfsReadWrite",
    "linuxNfsReadWrite",
    "userAndAclPermissionsVerified",
    "quotaEnforcedAcrossProtocols",
    "largeFileVerified",
)
PROTOCOL_INTEROPERABILITY_EVIDENCE_NAMES = {
    "windowsSmbReadWrite": "protocol-windows-smb.log",
    "macosSmbReadWrite": "protocol-macos-smb.log",
    "linuxSmbReadWrite": "protocol-linux-smb.log",
    "macosNfsReadWrite": "protocol-macos-nfs.log",
    "linuxNfsReadWrite": "protocol-linux-nfs.log",
    "userAndAclPermissionsVerified": "protocol-permissions.log",
    "quotaEnforcedAcrossProtocols": "protocol-quota.log",
    "largeFileVerified": "protocol-large-file.log",
}
DEVICE_ENDURANCE_LIFECYCLE_NAME = "device-endurance-lifecycle.json"
HUB_LIFECYCLE_PLAN_NAME = "hub-lifecycle-plan.json"
HUB_LIFECYCLE_RESULT_NAME = "hub-lifecycle-result.json"
PAPERLESS_FUNCTIONAL_PLAN_NAME = "paperless-functional-plan.json"
PAPERLESS_FUNCTIONAL_RESULT_NAME = "paperless-functional-result.json"
LAN_DISCOVERY_FUNCTIONAL_PLAN_NAME = "lan-discovery-functional-plan.json"
LAN_DISCOVERY_FUNCTIONAL_RESULT_NAME = "lan-discovery-functional-result.json"
LAN_DISCOVERY_PROBE_NAMES = tuple(lan_discovery_lab.PROBE_NAMES)
DEVICE_ENDURANCE_GATES = {
    "physical_x86_64_install_and_cold_boot",
    "supported_arm64_hardware_install_and_cold_boot",
}
DEVICE_ENDURANCE_LIFECYCLE_CHECKS = (
    "installerCompleted",
    "firstColdBootHealthy",
    "administratorLoginReady",
    "fileUploadDownloadCopyTrashVerified",
    "familyMemberIsolationVerified",
    "agentWorkbenchVerified",
    "oneGiBTransferVerified",
    "dockerControlApprovalVerified",
    "hubNineAppLifecycleVerified",
    "hubNineAppPublicEndpointsVerified",
    "paperlessOfficeOcrVerified",
    "homeAssistantLanDiscoveryVerified",
    "syncthingLanDiscoveryVerified",
    "continuousRunStable",
    "hardPowerCycleRecovered",
)
DEVICE_ENDURANCE_EVIDENCE_NAMES = {
    "installerCompleted": "device-baseline.log",
    "firstColdBootHealthy": "device-baseline.log",
    "administratorLoginReady": "device-baseline.log",
    "fileUploadDownloadCopyTrashVerified": "device-baseline.log",
    "familyMemberIsolationVerified": "device-baseline.log",
    "agentWorkbenchVerified": "device-baseline.log",
    "oneGiBTransferVerified": "device-baseline.log",
    "dockerControlApprovalVerified": "device-baseline.log",
    "hubNineAppLifecycleVerified": HUB_LIFECYCLE_RESULT_NAME,
    "hubNineAppPublicEndpointsVerified": HUB_LIFECYCLE_RESULT_NAME,
    "paperlessOfficeOcrVerified": PAPERLESS_FUNCTIONAL_RESULT_NAME,
    "homeAssistantLanDiscoveryVerified": LAN_DISCOVERY_FUNCTIONAL_RESULT_NAME,
    "syncthingLanDiscoveryVerified": LAN_DISCOVERY_FUNCTIONAL_RESULT_NAME,
    "continuousRunStable": "device-soak.log",
    "hardPowerCycleRecovered": "device-recovered.log",
}
DEVICE_ENDURANCE_PHASE_EVIDENCE_NAMES = {
    "device-baseline.log",
    "device-soak.log",
    "device-power-cut-armed.log",
    "device-recovered.log",
}
DELIVERY_REQUIREMENTS = ("G1", "G2", "G3", "G4", "G5", "G6")
GATE_REQUIREMENTS = {
    "physical_x86_64_install_and_cold_boot": {
        "architecture": "x86_64",
        "minimumDevices": 1,
        "minimumDurationSeconds": 86400,
        "profileClass": "reference-x86-nas",
        "deliveryRequirements": ("G1", "G4", "G5", "G6"),
        "suffix": "architecture=x86_64 install=complete cold-boot=healthy login=ready",
        "resultChecks": (
            "installerCompleted",
            "firstColdBootHealthy",
            "administratorLoginReady",
            "fileUploadDownloadCopyTrashVerified",
            "familyMemberIsolationVerified",
            "agentWorkbenchVerified",
            "oneGiBTransferVerified",
            "dockerControlApprovalVerified",
            "hubNineAppLifecycleVerified",
            "hubNineAppPublicEndpointsVerified",
            "paperlessOfficeOcrVerified",
            "homeAssistantLanDiscoveryVerified",
            "syncthingLanDiscoveryVerified",
            "tlsBrowserTrustVerified",
            "secureCookieVerified",
            "originAndWebSocketPolicyVerified",
            "sessionRevocationVerified",
            "approvalReplayRejected",
            "auditChainVerified",
            "dockerSocketIsolationVerified",
            "thirdPartyCspAllowlistVerified",
            "continuousRunStable",
            "hardPowerCycleRecovered",
        ),
    },
    "supported_arm64_hardware_install_and_cold_boot": {
        "architecture": "arm64",
        "minimumDevices": 1,
        "minimumDurationSeconds": 86400,
        "profileClass": "supported-arm64-nas",
        "deliveryRequirements": ("G1", "G5", "G6"),
        "suffix": "architecture=arm64 install=complete cold-boot=healthy login=ready",
        "resultChecks": (
            "installerCompleted",
            "firstColdBootHealthy",
            "administratorLoginReady",
            "fileUploadDownloadCopyTrashVerified",
            "familyMemberIsolationVerified",
            "agentWorkbenchVerified",
            "oneGiBTransferVerified",
            "dockerControlApprovalVerified",
            "hubNineAppLifecycleVerified",
            "hubNineAppPublicEndpointsVerified",
            "paperlessOfficeOcrVerified",
            "homeAssistantLanDiscoveryVerified",
            "syncthingLanDiscoveryVerified",
            "continuousRunStable",
            "hardPowerCycleRecovered",
        ),
    },
    "real_disk_smart_and_raid_degradation_recovery": {
        "architecture": None,
        "minimumDevices": 2,
        "minimumDurationSeconds": 1,
        "profileClass": "storage-recovery-lab",
        "deliveryRequirements": ("G2",),
        "suffix": "smart=healthy raid-degraded=observed raid-rebuild=complete data=preserved",
        "resultChecks": (
            "smartHealthy",
            "diskDisconnectObserved",
            "raidDegradationObserved",
            "filesystemReadOnlyHandled",
            "volumeFullHandled",
            "rebootRecoveryVerified",
            "recycleBinRestoreVerified",
            "raidRebuildCompleted",
            "dataPreserved",
        ),
    },
    "external_smb_and_nfs_client_interoperability": {
        "architecture": None,
        "minimumDevices": 1,
        "minimumDurationSeconds": 1,
        "profileClass": "protocol-interoperability-lab",
        "deliveryRequirements": ("G3",),
        "suffix": "smb=read-write nfs=read-write permissions=verified large-file=verified",
        "resultChecks": (
            "windowsSmbReadWrite",
            "macosSmbReadWrite",
            "linuxSmbReadWrite",
            "macosNfsReadWrite",
            "linuxNfsReadWrite",
            "userAndAclPermissionsVerified",
            "quotaEnforcedAcrossProtocols",
            "largeFileVerified",
        ),
    },
    "power_loss_during_update_and_state_restore": {
        "architecture": None,
        "minimumDevices": 1,
        "minimumDurationSeconds": 1,
        "profileClass": "power-interruption-lab",
        "deliveryRequirements": ("G5", "G6"),
        "suffix": "update-power-loss=rollback state-restore=commit data=preserved",
        "resultChecks": (
            "immutableDigestUpgradeVerified",
            "updatePowerLossRolledBack",
            "failedUpgradeRollbackVerified",
            "managedUninstallDataPreserved",
            "stateRestoreCommitted",
            "dataPreserved",
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
        ),
    },
    BARE_METAL_GATE: {
        "architecture": None,
        "minimumDevices": 1,
        "minimumDurationSeconds": 1,
        "profileClass": "bare-metal-recovery-lab",
        "deliveryRequirements": ("G5",),
        "suffix": "media=verified bare-metal=restored cold-boot=healthy data=verified",
        "resultChecks": (
            "recoveryMediaVerified",
            "bareMetalRestored",
            "coldBootHealthy",
            "dataVerified",
            "offDeviceBackupRestored",
            "authenticationStateVerified",
            "auditStateVerified",
            "agentStateVerified",
            "nasDataVerified",
        ),
    },
}


class PhysicalAcceptanceError(RuntimeError):
    """Physical evidence cannot authorize a NAS product delivery."""


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PhysicalAcceptanceError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_regular(path: Path, maximum: int, label: str) -> bytes:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise PhysicalAcceptanceError(f"{label} is unavailable") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size <= 0 or before.st_size > maximum:
            raise PhysicalAcceptanceError(f"{label} is empty, oversized or unsafe")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise PhysicalAcceptanceError(f"{label} ended while reading")
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
        if (
            after.st_dev != before.st_dev
            or after.st_ino != before.st_ino
            or after.st_size != before.st_size
            or after.st_mtime_ns != before.st_mtime_ns
            or after.st_ctime_ns != before.st_ctime_ns
        ):
            raise PhysicalAcceptanceError(f"{label} changed while reading")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _load_json(path: Path, maximum: int, label: str) -> tuple[dict[str, Any], bytes]:
    raw = _read_regular(path, maximum, label)
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PhysicalAcceptanceError(f"{label} is not strict JSON") from exc
    if not isinstance(value, dict):
        raise PhysicalAcceptanceError(f"{label} must be a JSON object")
    return value, raw


def _exact(value: object, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise PhysicalAcceptanceError(f"{label} has an unexpected schema")
    return value


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _verify_physical_signature(manifest: Path, signature: Path, keyring: Path) -> dict[str, str]:
    manifest_raw = _read_regular(manifest, MAX_MANIFEST_BYTES, "physical gate manifest")
    signature_raw = _read_regular(signature, MAX_MANIFEST_BYTES, "physical gate signature")
    keyring_raw = _read_regular(keyring, 16 * 1024 * 1024, "acceptance public keyring")
    try:
        verify_public_keyring_bytes(keyring_raw)
    except PublicKeyringError as exc:
        raise PhysicalAcceptanceError("acceptance keyring is not public-only") from exc
    if (
        len(
            {
                manifest.resolve(strict=True),
                signature.resolve(strict=True),
                keyring.resolve(strict=True),
            }
        )
        != 3
    ):
        raise PhysicalAcceptanceError("manifest, signature and keyring must be distinct")
    try:
        with tempfile.TemporaryDirectory(prefix="echo-physical-signature-") as temporary_name:
            temporary = Path(temporary_name)
            manifest_copy = temporary / "evidence.json"
            signature_copy = temporary / "evidence.json.gpg"
            keyring_copy = temporary / "acceptance-keyring.gpg"
            manifest_copy.write_bytes(manifest_raw)
            signature_copy.write_bytes(signature_raw)
            keyring_copy.write_bytes(keyring_raw)
            completed = subprocess.run(  # nosec B603
                [
                    "/usr/bin/gpgv",
                    "--status-fd",
                    "1",
                    "--keyring",
                    str(keyring_copy),
                    str(signature_copy),
                    str(manifest_copy),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PhysicalAcceptanceError("gpgv is unavailable for physical acceptance") from exc
    if (
        completed.returncode != 0
        or len(completed.stdout.encode("utf-8", "replace")) > 64 * 1024
        or len(completed.stderr.encode("utf-8", "replace")) > 64 * 1024
    ):
        raise PhysicalAcceptanceError("physical gate signature is invalid")
    fingerprints = []
    for line in completed.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[:2] == ["[GNUPG:]", "VALIDSIG"]:
            fingerprints.append(fields[2])
    if len(fingerprints) != 1 or OPENPGP_FINGERPRINT.fullmatch(fingerprints[0]) is None:
        raise PhysicalAcceptanceError("physical signature has no unique valid signer fingerprint")
    return {
        "manifestSha256": _sha256(manifest_raw),
        "signatureSha256": _sha256(signature_raw),
        "keyringSha256": _sha256(keyring_raw),
        "signerFingerprint": fingerprints[0],
    }


def _validate_candidate(value: Mapping[str, Any], raw: bytes) -> dict[str, str]:
    if set(value) != {
        "schemaVersion",
        "kind",
        "source",
        "evidence",
        "ciReleaseCandidateReady",
        "nasProductDeliveryReady",
        "physicalAcceptance",
        "indexId",
    }:
        raise PhysicalAcceptanceError("candidate evidence index has an unexpected schema")
    source = _exact(
        value["source"],
        {"repository", "commit", "agentRepository", "agentCommit", "releaseTag"},
        "candidate source",
    )
    physical = _exact(
        value["physicalAcceptance"], {"complete", "remainingGates"}, "candidate physical gates"
    )
    evidence = value["evidence"]
    appliance = (
        _exact(
            evidence.get("appliance"),
            {"manifestSha256", "immutableReference", "operationsBundle"},
            "candidate appliance evidence",
        )
        if isinstance(evidence, dict)
        else {}
    )
    operations = (
        _exact(
            appliance.get("operationsBundle"),
            {"artifactId", "sha256", "imageReference"},
            "candidate operations bundle evidence",
        )
        if appliance
        else {}
    )
    if (
        value["schemaVersion"] != 1
        or value["kind"] != "echo.delivery-release-evidence-index"
        or value["ciReleaseCandidateReady"] is not True
        or value["nasProductDeliveryReady"] is not False
        or physical["complete"] is not False
        or physical["remainingGates"] != list(PHYSICAL_GATES)
        or not isinstance(evidence, dict)
        or "candidatePreflight" not in evidence
        or not isinstance(appliance.get("manifestSha256"), str)
        or SHA256.fullmatch(appliance["manifestSha256"]) is None
        or not isinstance(appliance.get("immutableReference"), str)
        or not isinstance(operations.get("artifactId"), str)
        or re.fullmatch(r"[0-9a-f]{16}", operations["artifactId"]) is None
        or not isinstance(operations.get("sha256"), str)
        or SHA256.fullmatch(operations["sha256"]) is None
        or operations.get("imageReference") != appliance["immutableReference"]
        or not isinstance(source["repository"], str)
        or not isinstance(source["agentRepository"], str)
        or not isinstance(source["commit"], str)
        or SHA1.fullmatch(source["commit"]) is None
        or not isinstance(source["agentCommit"], str)
        or SHA1.fullmatch(source["agentCommit"]) is None
        or not isinstance(source["releaseTag"], str)
        or RELEASE_TAG.fullmatch(source["releaseTag"]) is None
    ):
        raise PhysicalAcceptanceError("candidate is not one complete CI release candidate")
    index_id = value["indexId"]
    unsigned = dict(value)
    del unsigned["indexId"]
    canonical = json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
    if not isinstance(index_id, str) or index_id != _sha256(canonical):
        raise PhysicalAcceptanceError("candidate evidence index ID is invalid")
    return {
        "indexId": index_id,
        "indexSha256": _sha256(raw),
        "repository": source["repository"],
        "sourceRevision": source["commit"],
        "agentRepository": source["agentRepository"],
        "agentRevision": source["agentCommit"],
        "releaseTag": source["releaseTag"],
        "applianceManifestSha256": appliance["manifestSha256"],
        "immutableReference": appliance["immutableReference"],
        "operationsArtifactId": operations["artifactId"],
        "operationsArchiveSha256": operations["sha256"],
    }


def _utc_time(value: object, label: str) -> datetime:
    if not isinstance(value, str) or UTC_TIMESTAMP.fullmatch(value) is None:
        raise PhysicalAcceptanceError(f"{label} must be one canonical UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise PhysicalAcceptanceError(f"{label} is invalid") from exc
    if parsed.tzinfo != UTC:
        raise PhysicalAcceptanceError(f"{label} is not UTC")
    return parsed


def _validate_hardware_profile(
    raw: bytes,
    *,
    gate: str,
    architecture: str,
    device_count: int,
) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PhysicalAcceptanceError("hardware-profile.json is not strict JSON") from exc
    expected = {
        "schemaVersion",
        "kind",
        "gate",
        "profileClass",
        "architecture",
        "deviceCount",
        "serialsRedacted",
    }
    requirement = GATE_REQUIREMENTS[gate]
    if (
        not isinstance(value, dict)
        or set(value) != expected
        or not isinstance(value["schemaVersion"], int)
        or isinstance(value["schemaVersion"], bool)
        or value["schemaVersion"] != 1
        or value["kind"] != "echo.physical-hardware-profile"
        or value["gate"] != gate
        or value["profileClass"] != requirement["profileClass"]
        or value["architecture"] != architecture
        or not isinstance(value["deviceCount"], int)
        or isinstance(value["deviceCount"], bool)
        or value["deviceCount"] != device_count
        or value["serialsRedacted"] is not True
    ):
        raise PhysicalAcceptanceError("hardware-profile.json does not match the gate hardware")
    return value


def _gate_result_payload(gate: str) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "kind": "echo.physical-gate-result",
        "gate": gate,
        "checks": {name: True for name in GATE_REQUIREMENTS[gate]["resultChecks"]},
        "allPassed": True,
    }


def _operations_systemd_lifecycle_payload(
    evidence: Mapping[str, Mapping[str, Any]],
    *,
    candidate: Mapping[str, str],
    lab_plan_id: str,
) -> dict[str, Any]:
    return {
        "schemaVersion": 2,
        "kind": "echo.operations-systemd-physical-lifecycle",
        "gate": OPERATIONS_SYSTEMD_GATE,
        "candidate": {
            "indexId": candidate["indexId"],
            "sourceRevision": candidate["sourceRevision"],
            "agentRevision": candidate["agentRevision"],
            "releaseTag": candidate["releaseTag"],
            "operationsArtifactId": candidate["operationsArtifactId"],
            "operationsArchiveSha256": candidate["operationsArchiveSha256"],
        },
        "labPlanId": lab_plan_id,
        "checks": {
            check: {"passed": True, "evidence": dict(evidence[check])}
            for check in OPERATIONS_SYSTEMD_LIFECYCLE_CHECKS
        },
        "allPassed": True,
    }


def _power_state_lifecycle_payload(
    evidence: Mapping[str, Mapping[str, Any]],
    phases: Mapping[str, Mapping[str, Any]],
    context: Mapping[str, Any],
    *,
    candidate: Mapping[str, str],
    lab_plan_id: str,
) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "kind": "echo.power-state-physical-lifecycle",
        "gate": OPERATIONS_SYSTEMD_GATE,
        "candidate": {
            "indexId": candidate["indexId"],
            "sourceRevision": candidate["sourceRevision"],
            "agentRevision": candidate["agentRevision"],
            "releaseTag": candidate["releaseTag"],
            "operationsArtifactId": candidate["operationsArtifactId"],
            "operationsArchiveSha256": candidate["operationsArchiveSha256"],
        },
        "labPlanId": lab_plan_id,
        "context": dict(context),
        "checks": {
            check: {"passed": True, "evidence": dict(evidence[check])}
            for check in POWER_STATE_LIFECYCLE_CHECKS
        },
        "phases": {phase: dict(phases[phase]) for phase in POWER_STATE_PHASES},
        "allPassed": True,
    }


def _bare_metal_lifecycle_payload(
    evidence: Mapping[str, Mapping[str, Any]],
    phases: Mapping[str, Mapping[str, Any]],
    context: Mapping[str, Any],
    *,
    candidate: Mapping[str, str],
    lab_plan_id: str,
) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "kind": "echo.bare-metal-recovery-physical-lifecycle",
        "gate": BARE_METAL_GATE,
        "candidate": {
            "indexId": candidate["indexId"],
            "sourceRevision": candidate["sourceRevision"],
            "agentRevision": candidate["agentRevision"],
            "releaseTag": candidate["releaseTag"],
            "operationsArtifactId": candidate["operationsArtifactId"],
            "operationsArchiveSha256": candidate["operationsArchiveSha256"],
        },
        "labPlanId": lab_plan_id,
        "context": dict(context),
        "checks": {
            check: {"passed": True, "evidence": dict(evidence[check])}
            for check in BARE_METAL_LIFECYCLE_CHECKS
        },
        "phases": {phase: dict(phases[phase]) for phase in BARE_METAL_PHASES},
        "allPassed": True,
    }


def _storage_recovery_lifecycle_payload(
    evidence: Mapping[str, Mapping[str, Any]],
    *,
    candidate: Mapping[str, str],
    lab_plan_id: str,
) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "kind": "echo.storage-recovery-physical-lifecycle",
        "gate": STORAGE_RECOVERY_GATE,
        "candidate": {
            "indexId": candidate["indexId"],
            "sourceRevision": candidate["sourceRevision"],
            "agentRevision": candidate["agentRevision"],
            "releaseTag": candidate["releaseTag"],
            "operationsArtifactId": candidate["operationsArtifactId"],
            "operationsArchiveSha256": candidate["operationsArchiveSha256"],
        },
        "labPlanId": lab_plan_id,
        "checks": {
            check: {"passed": True, "evidence": dict(evidence[check])}
            for check in STORAGE_RECOVERY_LIFECYCLE_CHECKS
        },
        "allPassed": True,
    }


def _protocol_interoperability_lifecycle_payload(
    evidence: Mapping[str, Mapping[str, Any]],
    *,
    candidate: Mapping[str, str],
    lab_plan_id: str,
) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "kind": "echo.protocol-interoperability-physical-lifecycle",
        "gate": PROTOCOL_INTEROPERABILITY_GATE,
        "candidate": {
            "indexId": candidate["indexId"],
            "sourceRevision": candidate["sourceRevision"],
            "agentRevision": candidate["agentRevision"],
            "releaseTag": candidate["releaseTag"],
            "operationsArtifactId": candidate["operationsArtifactId"],
            "operationsArchiveSha256": candidate["operationsArchiveSha256"],
        },
        "labPlanId": lab_plan_id,
        "checks": {
            check: {"passed": True, "evidence": dict(evidence[check])}
            for check in PROTOCOL_INTEROPERABILITY_LIFECYCLE_CHECKS
        },
        "allPassed": True,
    }


def _device_endurance_lifecycle_payload(
    evidence: Mapping[str, Mapping[str, Any]],
    *,
    candidate: Mapping[str, str],
    lab_plan_id: str,
    gate: str,
) -> dict[str, Any]:
    if gate not in DEVICE_ENDURANCE_GATES:
        raise PhysicalAcceptanceError("device endurance lifecycle has an invalid device gate")
    return {
        "schemaVersion": 1,
        "kind": "echo.device-endurance-physical-lifecycle",
        "gate": gate,
        "candidate": {
            "indexId": candidate["indexId"],
            "sourceRevision": candidate["sourceRevision"],
            "agentRevision": candidate["agentRevision"],
            "releaseTag": candidate["releaseTag"],
            "operationsArtifactId": candidate["operationsArtifactId"],
            "operationsArchiveSha256": candidate["operationsArchiveSha256"],
        },
        "labPlanId": lab_plan_id,
        "checks": {
            check: {"passed": True, "evidence": dict(evidence[check])}
            for check in DEVICE_ENDURANCE_LIFECYCLE_CHECKS
        },
        "allPassed": True,
    }


def _validate_gate_result(raw: bytes, *, gate: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PhysicalAcceptanceError(f"{GATE_RESULT_NAME} is not strict JSON") from exc
    expected_checks = tuple(GATE_REQUIREMENTS[gate]["resultChecks"])
    if (
        not isinstance(value, dict)
        or set(value) != {"schemaVersion", "kind", "gate", "checks", "allPassed"}
        or not isinstance(value["schemaVersion"], int)
        or isinstance(value["schemaVersion"], bool)
        or value["schemaVersion"] != 1
        or value["kind"] != "echo.physical-gate-result"
        or value["gate"] != gate
        or not isinstance(value["checks"], dict)
        or set(value["checks"]) != set(expected_checks)
        or any(value["checks"][name] is not True for name in expected_checks)
        or value["allPassed"] is not True
    ):
        raise PhysicalAcceptanceError(
            f"{GATE_RESULT_NAME} does not attest every required check for {gate}"
        )
    return value


def _validate_operations_systemd_lifecycle(
    raw: bytes,
    *,
    artifacts: Mapping[str, Mapping[str, Any]],
    candidate: Mapping[str, str],
) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PhysicalAcceptanceError(
            f"{OPERATIONS_SYSTEMD_LIFECYCLE_NAME} is not strict JSON"
        ) from exc
    expected_top = {
        "schemaVersion",
        "kind",
        "gate",
        "candidate",
        "labPlanId",
        "checks",
        "allPassed",
    }
    expected_candidate = {
        "indexId": candidate["indexId"],
        "sourceRevision": candidate["sourceRevision"],
        "agentRevision": candidate["agentRevision"],
        "releaseTag": candidate["releaseTag"],
        "operationsArtifactId": candidate["operationsArtifactId"],
        "operationsArchiveSha256": candidate["operationsArchiveSha256"],
    }
    if (
        not isinstance(value, dict)
        or set(value) != expected_top
        or not isinstance(value["schemaVersion"], int)
        or isinstance(value["schemaVersion"], bool)
        or value["schemaVersion"] != 2
        or value["kind"] != "echo.operations-systemd-physical-lifecycle"
        or value["gate"] != OPERATIONS_SYSTEMD_GATE
        or value["candidate"] != expected_candidate
        or not isinstance(value["labPlanId"], str)
        or SHA256.fullmatch(value["labPlanId"]) is None
        or not isinstance(value["checks"], dict)
        or set(value["checks"]) != set(OPERATIONS_SYSTEMD_LIFECYCLE_CHECKS)
        or value["allPassed"] is not True
    ):
        raise PhysicalAcceptanceError(
            f"{OPERATIONS_SYSTEMD_LIFECYCLE_NAME} has an invalid lifecycle contract"
        )

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
    for check in OPERATIONS_SYSTEMD_LIFECYCLE_CHECKS:
        item = value["checks"][check]
        if (
            not isinstance(item, dict)
            or set(item) != {"passed", "evidence"}
            or item["passed"] is not True
            or not isinstance(item["evidence"], dict)
            or set(item["evidence"]) != {"name", "sha256", "size"}
        ):
            raise PhysicalAcceptanceError(
                f"{OPERATIONS_SYSTEMD_LIFECYCLE_NAME} does not bind passed check {check}"
            )
        evidence = item["evidence"]
        name = evidence["name"]
        if (
            not isinstance(name, str)
            or ARTIFACT_NAME.fullmatch(name) is None
            or name in reserved
            or not isinstance(evidence["sha256"], str)
            or SHA256.fullmatch(evidence["sha256"]) is None
            or not isinstance(evidence["size"], int)
            or isinstance(evidence["size"], bool)
            or evidence["size"] <= 0
            or name not in artifacts
            or dict(artifacts[name]) != evidence
        ):
            raise PhysicalAcceptanceError(
                f"{OPERATIONS_SYSTEMD_LIFECYCLE_NAME} evidence for {check} is unbound"
            )
    return value


def _power_state_canary(value: object, *, size: int) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {"path", "sha256", "size"}
        and isinstance(value.get("path"), str)
        and Path(value["path"]).is_absolute()
        and isinstance(value.get("sha256"), str)
        and SHA256.fullmatch(value["sha256"]) is not None
        and value.get("size") == size
    )


def _valid_boot_id(value: object) -> bool:
    try:
        return str(uuid.UUID(str(value))) == value
    except (ValueError, AttributeError):
        return False


def _validate_power_state_phase_details(
    context: Mapping[str, Any],
    phase: str,
    details: Mapping[str, Any],
    *,
    lab_plan_id: str,
) -> None:
    canaries = context["canaries"]
    if phase == "baseline":
        valid = details == {
            "previousImageVerified": True,
            "targetImage": context["targetImage"],
            "bootId": context["baselineBootId"],
            "recoveryService": {"enabled": True, "active": False},
            "canaries": canaries,
        }
    elif phase == "arm-power-cut":
        marker = (
            f"ECHO_POWER_STATE_UPDATE_CUT_ARMED plan={lab_plan_id} boot={context['baselineBootId']}"
        )
        valid = (
            set(details)
            == {
                "physicalPowerCutArmed",
                "bootId",
                "marker",
                "transactionId",
                "transactionPhase",
                "targetSelected",
                "nextAction",
            }
            and details.get("physicalPowerCutArmed") is True
            and details.get("bootId") == context["baselineBootId"]
            and details.get("marker") == marker
            and isinstance(details.get("transactionId"), str)
            and SHA256.fullmatch(details["transactionId"]) is not None
            and details.get("transactionPhase") == "selected"
            and details.get("targetSelected") is True
            and details.get("nextAction") == "physically-remove-and-restore-power"
        )
    elif phase == "recover-power-cut":
        valid = (
            set(details)
            == {
                "updatePowerLossRolledBack",
                "bootIdChanged",
                "previousBootId",
                "currentBootId",
                "uncleanShutdownVerified",
                "automaticRecoveryServiceResult",
                "previousImageRestored",
                "canaries",
                "journal",
            }
            and details.get("updatePowerLossRolledBack") is True
            and details.get("bootIdChanged") is True
            and _valid_boot_id(details.get("previousBootId"))
            and _valid_boot_id(details.get("currentBootId"))
            and details.get("previousBootId") == context["baselineBootId"]
            and details.get("currentBootId") != details.get("previousBootId")
            and details.get("uncleanShutdownVerified") is True
            and details.get("automaticRecoveryServiceResult") == "success"
            and details.get("previousImageRestored") == context["previousImage"]
            and details.get("canaries") == canaries
            and details.get("journal")
            == {
                "persistentJournalAvailable": True,
                "powerCutIntentFound": True,
                "cleanShutdownFound": False,
            }
        )
    elif phase == "upgrade-success":
        valid = details == {
            "immutableDigestUpgradeVerified": True,
            "previousImage": context["previousImage"],
            "targetImage": context["targetImage"],
            "transactionCommitted": True,
            "canaries": canaries,
        }
    elif phase == "upgrade-failure":
        valid = details == {
            "failedUpgradeRollbackVerified": True,
            "failureInjectedAfterSelection": True,
            "candidateImageRestored": context["targetImage"],
            "transactionRecovered": True,
            "canaries": canaries,
        }
    elif phase == "managed-uninstall":
        valid = details == {
            "managedUninstallDataPreserved": True,
            "composeVolumesRemoved": False,
            "containersReinstalled": True,
            "canaries": canaries,
        }
    else:
        backup = details.get("externalBackup")
        valid = (
            set(details)
            == {
                "stateRestoreCommitted",
                "dataPreserved",
                "readOnlyPreflightVerified",
                "externalBackup",
                "previousStateRetained",
                "canaries",
            }
            and details.get("stateRestoreCommitted") is True
            and details.get("dataPreserved") is True
            and details.get("readOnlyPreflightVerified") is True
            and details.get("previousStateRetained") is True
            and details.get("canaries") == canaries
            and isinstance(backup, dict)
            and set(backup) == {"path", "sha256", "size"}
            and isinstance(backup.get("path"), str)
            and Path(backup["path"]).is_absolute()
            and isinstance(backup.get("sha256"), str)
            and SHA256.fullmatch(backup["sha256"]) is not None
            and isinstance(backup.get("size"), int)
            and not isinstance(backup.get("size"), bool)
            and 0 < backup["size"] <= 100 * 1024 * 1024 * 1024
        )
    if not valid:
        raise PhysicalAcceptanceError(f"power/state lab evidence details are invalid: {phase}")


def _validate_power_state_lifecycle(
    raw: bytes,
    *,
    artifacts: Mapping[str, Mapping[str, Any]],
    artifact_bytes: Mapping[str, bytes],
    candidate: Mapping[str, str],
) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PhysicalAcceptanceError(f"{POWER_STATE_LIFECYCLE_NAME} is not strict JSON") from exc
    expected_candidate = {
        "indexId": candidate["indexId"],
        "sourceRevision": candidate["sourceRevision"],
        "agentRevision": candidate["agentRevision"],
        "releaseTag": candidate["releaseTag"],
        "operationsArtifactId": candidate["operationsArtifactId"],
        "operationsArchiveSha256": candidate["operationsArchiveSha256"],
    }
    context = value.get("context") if isinstance(value, dict) else None
    canaries = context.get("canaries") if isinstance(context, dict) else None
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "schemaVersion",
            "kind",
            "gate",
            "candidate",
            "labPlanId",
            "context",
            "checks",
            "phases",
            "allPassed",
        }
        or value.get("schemaVersion") != 1
        or value.get("kind") != "echo.power-state-physical-lifecycle"
        or value.get("gate") != OPERATIONS_SYSTEMD_GATE
        or value.get("candidate") != expected_candidate
        or not isinstance(value.get("labPlanId"), str)
        or SHA256.fullmatch(value["labPlanId"]) is None
        or not isinstance(context, dict)
        or set(context) != {"previousImage", "targetImage", "baselineBootId", "canaries"}
        or IMMUTABLE_IMAGE_REFERENCE.fullmatch(str(context.get("previousImage"))) is None
        or context.get("targetImage") != candidate["immutableReference"]
        or context.get("previousImage") == context.get("targetImage")
        or not _valid_boot_id(context.get("baselineBootId"))
        or not isinstance(canaries, dict)
        or set(canaries) != {"state", "nas"}
        or not _power_state_canary(canaries.get("state"), size=POWER_STATE_CANARY_BYTES)
        or not _power_state_canary(canaries.get("nas"), size=POWER_STATE_NAS_CANARY_BYTES)
        or not isinstance(value.get("checks"), dict)
        or set(value["checks"]) != set(POWER_STATE_LIFECYCLE_CHECKS)
        or not isinstance(value.get("phases"), dict)
        or set(value["phases"]) != set(POWER_STATE_PHASES)
        or value.get("allPassed") is not True
    ):
        raise PhysicalAcceptanceError(
            f"{POWER_STATE_LIFECYCLE_NAME} has an invalid lifecycle contract"
        )
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
    for phase in POWER_STATE_PHASES:
        evidence = value["phases"][phase]
        name = POWER_STATE_PHASE_EVIDENCE_NAMES[phase]
        if (
            not isinstance(evidence, dict)
            or set(evidence) != {"name", "sha256", "size"}
            or evidence.get("name") != name
            or name in reserved
            or not isinstance(evidence.get("sha256"), str)
            or SHA256.fullmatch(evidence["sha256"]) is None
            or not isinstance(evidence.get("size"), int)
            or isinstance(evidence.get("size"), bool)
            or evidence["size"] <= 0
            or name not in artifacts
            or dict(artifacts[name]) != evidence
            or name not in artifact_bytes
        ):
            raise PhysicalAcceptanceError(
                f"{POWER_STATE_LIFECYCLE_NAME} phase evidence is unbound: {phase}"
            )
        try:
            phase_value = json.loads(
                artifact_bytes[name].decode("utf-8"),
                object_pairs_hook=_reject_duplicate_pairs,
            )
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise PhysicalAcceptanceError("power/state phase evidence is not strict JSON") from exc
        if (
            not isinstance(phase_value, dict)
            or set(phase_value) != {"schemaVersion", "kind", "planId", "phase", "passed", "details"}
            or phase_value.get("schemaVersion") != 1
            or phase_value.get("kind") != "echo.power-state-physical-lab-evidence"
            or phase_value.get("planId") != value["labPlanId"]
            or phase_value.get("phase") != phase
            or phase_value.get("passed") is not True
            or not isinstance(phase_value.get("details"), dict)
        ):
            raise PhysicalAcceptanceError("power/state phase evidence contract is invalid")
        _validate_power_state_phase_details(
            context,
            phase,
            phase_value["details"],
            lab_plan_id=value["labPlanId"],
        )
    for check in POWER_STATE_LIFECYCLE_CHECKS:
        item = value["checks"][check]
        phase_name = POWER_STATE_EVIDENCE_NAMES[check]
        phase = next(
            name
            for name, evidence_name in POWER_STATE_PHASE_EVIDENCE_NAMES.items()
            if evidence_name == phase_name
        )
        if (
            not isinstance(item, dict)
            or set(item) != {"passed", "evidence"}
            or item.get("passed") is not True
            or item.get("evidence") != value["phases"][phase]
        ):
            raise PhysicalAcceptanceError(
                f"{POWER_STATE_LIFECYCLE_NAME} does not bind passed check {check}"
            )
    return value


def _bare_metal_file_record(value: object, *, size: int | None = None) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {"path", "sha256", "size"}
        and isinstance(value.get("path"), str)
        and Path(value["path"]).is_absolute()
        and isinstance(value.get("sha256"), str)
        and SHA256.fullmatch(value["sha256"]) is not None
        and isinstance(value.get("size"), int)
        and not isinstance(value.get("size"), bool)
        and value["size"] > 0
        and (size is None or value["size"] == size)
    )


def _bare_metal_state(value: object) -> bool:
    return (
        isinstance(value, dict)
        and set(value)
        == {
            "authenticationStateVerified",
            "auditStateVerified",
            "auditEntries",
            "auditSigningKeyId",
            "sessionNotBefore",
            "schemaVersion",
        }
        and value.get("authenticationStateVerified") is True
        and value.get("auditStateVerified") is True
        and isinstance(value.get("auditEntries"), int)
        and not isinstance(value.get("auditEntries"), bool)
        and value["auditEntries"] >= 0
        and isinstance(value.get("auditSigningKeyId"), str)
        and 1 <= len(value["auditSigningKeyId"]) <= 256
        and isinstance(value.get("sessionNotBefore"), int)
        and not isinstance(value.get("sessionNotBefore"), bool)
        and value["sessionNotBefore"] >= 0
        and isinstance(value.get("schemaVersion"), int)
        and not isinstance(value.get("schemaVersion"), bool)
        and value["schemaVersion"] >= 1
    )


def _bare_metal_appliance(value: object, architecture: str) -> bool:
    return value == {
        "bundleVerified": True,
        "immutableImageVerified": True,
        "administratorLoginReady": True,
        "agentWorkbenchReady": True,
        "auditVerified": True,
        "dockerApprovalVerified": True,
        "runtimeArchitecture": architecture,
    }


def _bare_metal_context(value: object, *, candidate: Mapping[str, str]) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "sourceSystem",
        "backups",
        "verifierArchitecture",
    }:
        return False
    source = value.get("sourceSystem")
    backups = value.get("backups")
    architecture = value.get("verifierArchitecture")
    if (
        not isinstance(source, dict)
        or set(source)
        != {
            "bootId",
            "machineIdSha256",
            "sourceRevision",
            "state",
            "appliance",
            "canaries",
        }
        or not _valid_boot_id(source.get("bootId"))
        or not isinstance(source.get("machineIdSha256"), str)
        or SHA256.fullmatch(source["machineIdSha256"]) is None
        or source.get("sourceRevision") != candidate["sourceRevision"]
        or not _bare_metal_state(source.get("state"))
        or architecture not in {"amd64", "arm64"}
        or not _bare_metal_appliance(source.get("appliance"), architecture)
        or not isinstance(source.get("canaries"), dict)
        or set(source["canaries"]) != {"state", "agent", "nas"}
        or not _bare_metal_file_record(
            source["canaries"].get("state"), size=BARE_METAL_STATE_CANARY_BYTES
        )
        or not _bare_metal_file_record(
            source["canaries"].get("agent"), size=BARE_METAL_AGENT_CANARY_BYTES
        )
        or not _bare_metal_file_record(
            source["canaries"].get("nas"), size=BARE_METAL_NAS_CANARY_BYTES
        )
        or not isinstance(backups, dict)
        or set(backups) != {"applianceState", "user", "nas"}
        or not _bare_metal_file_record(backups.get("applianceState"))
    ):
        return False
    user = backups.get("user")
    nas = backups.get("nas")
    receipt = nas.get("receipt") if isinstance(nas, dict) else None
    return (
        isinstance(user, dict)
        and set(user) == {"repository", "repositoryId", "snapshotId", "fullReadVerified"}
        and user.get("repository") == "/mnt/echo-backup/echo-os-user"
        and isinstance(user.get("repositoryId"), str)
        and re.fullmatch(r"[0-9a-f]{16,64}", user["repositoryId"]) is not None
        and isinstance(user.get("snapshotId"), str)
        and SHA256.fullmatch(user["snapshotId"]) is not None
        and user.get("fullReadVerified") is True
        and isinstance(nas, dict)
        and set(nas) == {"repository", "mountpoint", "snapshotId", "receipt"}
        and all(
            isinstance(nas.get(name), str) and Path(nas[name]).is_absolute()
            for name in ("repository", "mountpoint")
        )
        and isinstance(nas.get("snapshotId"), str)
        and SHA256.fullmatch(nas["snapshotId"]) is not None
        and isinstance(receipt, dict)
        and set(receipt) == {"path", "sha256", "size", "repositoryId", "sourceSha256"}
        and _bare_metal_file_record({name: receipt[name] for name in ("path", "sha256", "size")})
        and isinstance(receipt.get("repositoryId"), str)
        and SHA256.fullmatch(receipt["repositoryId"]) is not None
        and isinstance(receipt.get("sourceSha256"), str)
        and SHA256.fullmatch(receipt["sourceSha256"]) is not None
    )


def _validate_bare_metal_phase_details(
    context: Mapping[str, Any],
    phase: str,
    details: Mapping[str, Any],
    values: Mapping[str, Mapping[str, Any]],
    *,
    candidate: Mapping[str, str],
) -> None:
    source = context["sourceSystem"]
    backups = context["backups"]
    architecture = context["verifierArchitecture"]
    sha_fields = lambda names: all(  # noqa: E731 - compact strict evidence predicate
        isinstance(details.get(name), str) and SHA256.fullmatch(details[name]) is not None
        for name in names
    )
    if phase == "source-backup":
        valid = details == {
            "sourceRevision": source["sourceRevision"],
            "sourceBootId": source["bootId"],
            "sourceMachineIdSha256": source["machineIdSha256"],
            "backups": backups,
            "canaries": source["canaries"],
            "state": source["state"],
            "appliance": source["appliance"],
        }
    elif phase == "recovery-install":
        valid = (
            set(details)
            == {
                "recoveryMediaVerified",
                "bareMetalRestored",
                "sourceRevision",
                "recoveryVersion",
                "installerManifestSha256",
                "installerSourceSha256",
                "installerPlanTranscriptSha256",
                "targetIdentitySha256",
                "transcriptSha256",
                "postWriteReadbackVerified",
                "recoveryBootId",
            }
            and details.get("recoveryMediaVerified") is True
            and details.get("bareMetalRestored") is True
            and details.get("sourceRevision") == candidate["sourceRevision"]
            and details.get("recoveryVersion")
            == candidate["releaseTag"].removeprefix("echo-appliance-v")
            and sha_fields(
                (
                    "installerManifestSha256",
                    "installerSourceSha256",
                    "installerPlanTranscriptSha256",
                    "targetIdentitySha256",
                    "transcriptSha256",
                )
            )
            and details.get("postWriteReadbackVerified") is True
            and _valid_boot_id(details.get("recoveryBootId"))
            and details.get("recoveryBootId") != source["bootId"]
        )
    elif phase == "cold-boot":
        recovery = values["recovery-install"]
        valid = (
            set(details)
            == {
                "firstColdBootHealthy",
                "recoveryBootId",
                "installedBootId",
                "replacementMachineIdSha256",
                "sourceMachineIdentityChanged",
                "sourceRevision",
                "appliance",
            }
            and details.get("firstColdBootHealthy") is True
            and details.get("recoveryBootId") == recovery["recoveryBootId"]
            and _valid_boot_id(details.get("installedBootId"))
            and details.get("installedBootId") not in {source["bootId"], recovery["recoveryBootId"]}
            and isinstance(details.get("replacementMachineIdSha256"), str)
            and SHA256.fullmatch(details["replacementMachineIdSha256"]) is not None
            and details.get("replacementMachineIdSha256") != source["machineIdSha256"]
            and details.get("sourceMachineIdentityChanged") is True
            and details.get("sourceRevision") == candidate["sourceRevision"]
            and _bare_metal_appliance(details.get("appliance"), architecture)
        )
    elif phase == "restore":
        valid = (
            set(details)
            == {
                "offDeviceBackupRestored",
                "applianceStateBackupSha256",
                "applianceState",
                "userSnapshotId",
                "userAgentRestoreStaged",
                "nasSnapshotId",
                "nasAtomicPromotion",
                "nasEntries",
                "nasLogicalBytes",
                "canaries",
                "bootId",
            }
            and details.get("offDeviceBackupRestored") is True
            and details.get("applianceStateBackupSha256") == backups["applianceState"]["sha256"]
            and details.get("applianceState") == source["state"]
            and details.get("userSnapshotId") == backups["user"]["snapshotId"]
            and details.get("userAgentRestoreStaged") is True
            and details.get("nasSnapshotId") == backups["nas"]["snapshotId"]
            and details.get("nasAtomicPromotion") is True
            and isinstance(details.get("nasEntries"), int)
            and not isinstance(details.get("nasEntries"), bool)
            and details["nasEntries"] >= 1
            and isinstance(details.get("nasLogicalBytes"), int)
            and not isinstance(details.get("nasLogicalBytes"), bool)
            and details["nasLogicalBytes"] >= BARE_METAL_NAS_CANARY_BYTES
            and details.get("canaries")
            == {name: source["canaries"][name] for name in ("state", "nas")}
            and details.get("bootId") == values["cold-boot"]["installedBootId"]
        )
    elif phase == "recovery-promote":
        valid = (
            set(details)
            == {
                "agentRestorePromoted",
                "transactionId",
                "recoveryBootId",
                "promotionTranscriptSha256",
            }
            and details.get("agentRestorePromoted") is True
            and isinstance(details.get("transactionId"), str)
            and re.fullmatch(r"[0-9a-f]{24}", details["transactionId"]) is not None
            and _valid_boot_id(details.get("recoveryBootId"))
            and details.get("recoveryBootId") != values["restore"]["bootId"]
            and sha_fields(("promotionTranscriptSha256",))
        )
    elif phase == "trial-verify":
        promoted = values["recovery-promote"]
        valid = (
            set(details)
            == {
                "trialBootHealthy",
                "transactionId",
                "bootId",
                "applianceState",
                "userAgentState",
                "nasTree",
                "canaries",
                "appliance",
            }
            and details.get("trialBootHealthy") is True
            and details.get("transactionId") == promoted["transactionId"]
            and _valid_boot_id(details.get("bootId"))
            and details.get("bootId") != promoted["recoveryBootId"]
            and details.get("applianceState") == source["state"]
            and details.get("userAgentState")
            == {
                "snapshotId": backups["user"]["snapshotId"],
                "action": "restore-staged",
                "fullReadVerified": True,
            }
            and details.get("nasTree")
            == {
                "entries": values["restore"]["nasEntries"],
                "logicalBytes": values["restore"]["nasLogicalBytes"],
            }
            and details.get("canaries") == source["canaries"]
            and _bare_metal_appliance(details.get("appliance"), architecture)
        )
    elif phase == "recovery-commit":
        valid = (
            set(details)
            == {
                "agentRestoreCommitted",
                "transactionId",
                "oldDataDeleted",
                "recoveryBootId",
                "commitTranscriptSha256",
            }
            and details.get("agentRestoreCommitted") is True
            and details.get("transactionId") == values["recovery-promote"]["transactionId"]
            and details.get("oldDataDeleted") is True
            and _valid_boot_id(details.get("recoveryBootId"))
            and details.get("recoveryBootId") != values["trial-verify"]["bootId"]
            and sha_fields(("commitTranscriptSha256",))
        )
    else:
        valid = (
            set(details)
            == {
                "coldBootHealthy",
                "dataVerified",
                "authenticationStateVerified",
                "auditStateVerified",
                "agentStateVerified",
                "nasDataVerified",
                "transactionId",
                "bootId",
                "applianceState",
                "userAgentState",
                "nasTree",
                "canaries",
                "appliance",
            }
            and all(
                details.get(name) is True
                for name in (
                    "coldBootHealthy",
                    "dataVerified",
                    "authenticationStateVerified",
                    "auditStateVerified",
                    "agentStateVerified",
                    "nasDataVerified",
                )
            )
            and details.get("transactionId") == values["recovery-commit"]["transactionId"]
            and _valid_boot_id(details.get("bootId"))
            and details.get("bootId") != values["recovery-commit"]["recoveryBootId"]
            and details.get("applianceState") == source["state"]
            and details.get("userAgentState")
            == {
                "snapshotId": backups["user"]["snapshotId"],
                "action": "restore-committed",
                "fullReadVerified": True,
            }
            and details.get("nasTree")
            == {
                "entries": values["restore"]["nasEntries"],
                "logicalBytes": values["restore"]["nasLogicalBytes"],
            }
            and details.get("canaries") == source["canaries"]
            and _bare_metal_appliance(details.get("appliance"), architecture)
        )
    if not valid:
        raise PhysicalAcceptanceError(f"bare-metal phase evidence is invalid: {phase}")


def _validate_bare_metal_lifecycle(
    raw: bytes,
    *,
    artifacts: Mapping[str, Mapping[str, Any]],
    artifact_bytes: Mapping[str, bytes],
    candidate: Mapping[str, str],
) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PhysicalAcceptanceError(f"{BARE_METAL_LIFECYCLE_NAME} is not strict JSON") from exc
    expected_candidate = {
        "indexId": candidate["indexId"],
        "sourceRevision": candidate["sourceRevision"],
        "agentRevision": candidate["agentRevision"],
        "releaseTag": candidate["releaseTag"],
        "operationsArtifactId": candidate["operationsArtifactId"],
        "operationsArchiveSha256": candidate["operationsArchiveSha256"],
    }
    context = value.get("context") if isinstance(value, dict) else None
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "schemaVersion",
            "kind",
            "gate",
            "candidate",
            "labPlanId",
            "context",
            "checks",
            "phases",
            "allPassed",
        }
        or value.get("schemaVersion") != 1
        or value.get("kind") != "echo.bare-metal-recovery-physical-lifecycle"
        or value.get("gate") != BARE_METAL_GATE
        or value.get("candidate") != expected_candidate
        or not isinstance(value.get("labPlanId"), str)
        or SHA256.fullmatch(value["labPlanId"]) is None
        or not _bare_metal_context(context, candidate=candidate)
        or not isinstance(value.get("checks"), dict)
        or set(value["checks"]) != set(BARE_METAL_LIFECYCLE_CHECKS)
        or not isinstance(value.get("phases"), dict)
        or set(value["phases"]) != set(BARE_METAL_PHASES)
        or value.get("allPassed") is not True
    ):
        raise PhysicalAcceptanceError(
            f"{BARE_METAL_LIFECYCLE_NAME} has an invalid lifecycle contract"
        )
    values: dict[str, Mapping[str, Any]] = {}
    for phase in BARE_METAL_PHASES:
        evidence = value["phases"][phase]
        name = BARE_METAL_PHASE_EVIDENCE_NAMES[phase]
        if (
            not isinstance(evidence, dict)
            or set(evidence) != {"name", "sha256", "size"}
            or evidence.get("name") != name
            or not isinstance(evidence.get("sha256"), str)
            or SHA256.fullmatch(evidence["sha256"]) is None
            or not isinstance(evidence.get("size"), int)
            or isinstance(evidence.get("size"), bool)
            or evidence["size"] <= 0
            or name not in artifacts
            or dict(artifacts[name]) != evidence
            or name not in artifact_bytes
        ):
            raise PhysicalAcceptanceError(
                f"{BARE_METAL_LIFECYCLE_NAME} phase evidence is unbound: {phase}"
            )
        try:
            phase_value = json.loads(
                artifact_bytes[name].decode("utf-8"),
                object_pairs_hook=_reject_duplicate_pairs,
            )
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise PhysicalAcceptanceError("bare-metal phase evidence is not strict JSON") from exc
        if (
            not isinstance(phase_value, dict)
            or set(phase_value) != {"schemaVersion", "kind", "planId", "phase", "passed", "details"}
            or phase_value.get("schemaVersion") != 1
            or phase_value.get("kind") != "echo.bare-metal-recovery-physical-lab-evidence"
            or phase_value.get("planId") != value["labPlanId"]
            or phase_value.get("phase") != phase
            or phase_value.get("passed") is not True
            or not isinstance(phase_value.get("details"), dict)
        ):
            raise PhysicalAcceptanceError("bare-metal phase evidence contract is invalid")
        values[phase] = phase_value["details"]
        _validate_bare_metal_phase_details(
            context,
            phase,
            phase_value["details"],
            values,
            candidate=candidate,
        )
    for check in BARE_METAL_LIFECYCLE_CHECKS:
        item = value["checks"][check]
        phase = next(
            name
            for name, evidence_name in BARE_METAL_PHASE_EVIDENCE_NAMES.items()
            if evidence_name == BARE_METAL_EVIDENCE_NAMES[check]
        )
        if (
            not isinstance(item, dict)
            or set(item) != {"passed", "evidence"}
            or item.get("passed") is not True
            or item.get("evidence") != value["phases"][phase]
        ):
            raise PhysicalAcceptanceError(
                f"{BARE_METAL_LIFECYCLE_NAME} does not bind passed check {check}"
            )
    return value


def _validate_storage_recovery_lifecycle(
    raw: bytes,
    *,
    artifacts: Mapping[str, Mapping[str, Any]],
    candidate: Mapping[str, str],
) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PhysicalAcceptanceError(
            f"{STORAGE_RECOVERY_LIFECYCLE_NAME} is not strict JSON"
        ) from exc
    expected_candidate = {
        "indexId": candidate["indexId"],
        "sourceRevision": candidate["sourceRevision"],
        "agentRevision": candidate["agentRevision"],
        "releaseTag": candidate["releaseTag"],
        "operationsArtifactId": candidate["operationsArtifactId"],
        "operationsArchiveSha256": candidate["operationsArchiveSha256"],
    }
    if (
        not isinstance(value, dict)
        or set(value)
        != {"schemaVersion", "kind", "gate", "candidate", "labPlanId", "checks", "allPassed"}
        or value["schemaVersion"] != 1
        or value["kind"] != "echo.storage-recovery-physical-lifecycle"
        or value["gate"] != STORAGE_RECOVERY_GATE
        or value["candidate"] != expected_candidate
        or not isinstance(value["labPlanId"], str)
        or SHA256.fullmatch(value["labPlanId"]) is None
        or not isinstance(value["checks"], dict)
        or set(value["checks"]) != set(STORAGE_RECOVERY_LIFECYCLE_CHECKS)
        or value["allPassed"] is not True
    ):
        raise PhysicalAcceptanceError(
            f"{STORAGE_RECOVERY_LIFECYCLE_NAME} has an invalid lifecycle contract"
        )
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
    if not set(artifacts) >= STORAGE_RECOVERY_PHASE_EVIDENCE_NAMES:
        raise PhysicalAcceptanceError(
            f"{STORAGE_RECOVERY_LIFECYCLE_NAME} omits one or more phase artifacts"
        )
    for check in STORAGE_RECOVERY_LIFECYCLE_CHECKS:
        item = value["checks"][check]
        if (
            not isinstance(item, dict)
            or set(item) != {"passed", "evidence"}
            or item["passed"] is not True
            or not isinstance(item["evidence"], dict)
            or set(item["evidence"]) != {"name", "sha256", "size"}
        ):
            raise PhysicalAcceptanceError(
                f"{STORAGE_RECOVERY_LIFECYCLE_NAME} does not bind passed check {check}"
            )
        evidence = item["evidence"]
        name = evidence["name"]
        if (
            not isinstance(name, str)
            or ARTIFACT_NAME.fullmatch(name) is None
            or name != STORAGE_RECOVERY_EVIDENCE_NAMES[check]
            or name in reserved
            or not isinstance(evidence["sha256"], str)
            or SHA256.fullmatch(evidence["sha256"]) is None
            or not isinstance(evidence["size"], int)
            or isinstance(evidence["size"], bool)
            or evidence["size"] <= 0
            or name not in artifacts
            or dict(artifacts[name]) != evidence
        ):
            raise PhysicalAcceptanceError(
                f"{STORAGE_RECOVERY_LIFECYCLE_NAME} evidence for {check} is unbound"
            )
    return value


def _validate_protocol_interoperability_lifecycle(
    raw: bytes,
    *,
    artifacts: Mapping[str, Mapping[str, Any]],
    candidate: Mapping[str, str],
) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PhysicalAcceptanceError(
            f"{PROTOCOL_INTEROPERABILITY_LIFECYCLE_NAME} is not strict JSON"
        ) from exc
    expected_candidate = {
        "indexId": candidate["indexId"],
        "sourceRevision": candidate["sourceRevision"],
        "agentRevision": candidate["agentRevision"],
        "releaseTag": candidate["releaseTag"],
        "operationsArtifactId": candidate["operationsArtifactId"],
        "operationsArchiveSha256": candidate["operationsArchiveSha256"],
    }
    if (
        not isinstance(value, dict)
        or set(value)
        != {"schemaVersion", "kind", "gate", "candidate", "labPlanId", "checks", "allPassed"}
        or value["schemaVersion"] != 1
        or value["kind"] != "echo.protocol-interoperability-physical-lifecycle"
        or value["gate"] != PROTOCOL_INTEROPERABILITY_GATE
        or value["candidate"] != expected_candidate
        or not isinstance(value["labPlanId"], str)
        or SHA256.fullmatch(value["labPlanId"]) is None
        or not isinstance(value["checks"], dict)
        or set(value["checks"]) != set(PROTOCOL_INTEROPERABILITY_LIFECYCLE_CHECKS)
        or value["allPassed"] is not True
        or not set(artifacts) >= set(PROTOCOL_INTEROPERABILITY_EVIDENCE_NAMES.values())
    ):
        raise PhysicalAcceptanceError(
            f"{PROTOCOL_INTEROPERABILITY_LIFECYCLE_NAME} has an invalid lifecycle contract"
        )
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
    for check in PROTOCOL_INTEROPERABILITY_LIFECYCLE_CHECKS:
        item = value["checks"][check]
        if (
            not isinstance(item, dict)
            or set(item) != {"passed", "evidence"}
            or item["passed"] is not True
            or not isinstance(item["evidence"], dict)
            or set(item["evidence"]) != {"name", "sha256", "size"}
        ):
            raise PhysicalAcceptanceError(
                f"{PROTOCOL_INTEROPERABILITY_LIFECYCLE_NAME} does not bind passed check {check}"
            )
        evidence = item["evidence"]
        name = evidence["name"]
        if (
            not isinstance(name, str)
            or ARTIFACT_NAME.fullmatch(name) is None
            or name != PROTOCOL_INTEROPERABILITY_EVIDENCE_NAMES[check]
            or name in reserved
            or not isinstance(evidence["sha256"], str)
            or SHA256.fullmatch(evidence["sha256"]) is None
            or not isinstance(evidence["size"], int)
            or isinstance(evidence["size"], bool)
            or evidence["size"] <= 0
            or name not in artifacts
            or dict(artifacts[name]) != evidence
        ):
            raise PhysicalAcceptanceError(
                f"{PROTOCOL_INTEROPERABILITY_LIFECYCLE_NAME} evidence for {check} is unbound"
            )
    return value


def _validate_device_endurance_lifecycle(
    raw: bytes,
    *,
    artifacts: Mapping[str, Mapping[str, Any]],
    candidate: Mapping[str, str],
    gate: str,
) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PhysicalAcceptanceError(
            f"{DEVICE_ENDURANCE_LIFECYCLE_NAME} is not strict JSON"
        ) from exc
    expected_candidate = {
        "indexId": candidate["indexId"],
        "sourceRevision": candidate["sourceRevision"],
        "agentRevision": candidate["agentRevision"],
        "releaseTag": candidate["releaseTag"],
        "operationsArtifactId": candidate["operationsArtifactId"],
        "operationsArchiveSha256": candidate["operationsArchiveSha256"],
    }
    if (
        gate not in DEVICE_ENDURANCE_GATES
        or not isinstance(value, dict)
        or set(value)
        != {"schemaVersion", "kind", "gate", "candidate", "labPlanId", "checks", "allPassed"}
        or value["schemaVersion"] != 1
        or value["kind"] != "echo.device-endurance-physical-lifecycle"
        or value["gate"] != gate
        or value["candidate"] != expected_candidate
        or not isinstance(value["labPlanId"], str)
        or SHA256.fullmatch(value["labPlanId"]) is None
        or not isinstance(value["checks"], dict)
        or set(value["checks"]) != set(DEVICE_ENDURANCE_LIFECYCLE_CHECKS)
        or value["allPassed"] is not True
        or not set(artifacts) >= DEVICE_ENDURANCE_PHASE_EVIDENCE_NAMES
    ):
        raise PhysicalAcceptanceError(
            f"{DEVICE_ENDURANCE_LIFECYCLE_NAME} has an invalid lifecycle contract"
        )
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
    for check in DEVICE_ENDURANCE_LIFECYCLE_CHECKS:
        item = value["checks"][check]
        if (
            not isinstance(item, dict)
            or set(item) != {"passed", "evidence"}
            or item["passed"] is not True
            or not isinstance(item["evidence"], dict)
            or set(item["evidence"]) != {"name", "sha256", "size"}
        ):
            raise PhysicalAcceptanceError(
                f"{DEVICE_ENDURANCE_LIFECYCLE_NAME} does not bind passed check {check}"
            )
        evidence = item["evidence"]
        name = evidence["name"]
        if (
            not isinstance(name, str)
            or ARTIFACT_NAME.fullmatch(name) is None
            or name != DEVICE_ENDURANCE_EVIDENCE_NAMES[check]
            or name in reserved
            or not isinstance(evidence["sha256"], str)
            or SHA256.fullmatch(evidence["sha256"]) is None
            or not isinstance(evidence["size"], int)
            or isinstance(evidence["size"], bool)
            or evidence["size"] <= 0
            or name not in artifacts
            or dict(artifacts[name]) != evidence
        ):
            raise PhysicalAcceptanceError(
                f"{DEVICE_ENDURANCE_LIFECYCLE_NAME} evidence for {check} is unbound"
            )
    return value


def _expected_marker(gate: str, candidate: Mapping[str, str]) -> str:
    return (
        f"ECHO_PHYSICAL_ACCEPTANCE_OK gate={gate} candidate={candidate['indexId']} "
        f"os={candidate['sourceRevision']} agent={candidate['agentRevision']} result=passed "
        f"{GATE_REQUIREMENTS[gate]['suffix']}"
    )


def _privacy_scan(name: str, raw: bytes) -> None:
    if Path(name).suffix.casefold() not in TEXT_SUFFIXES:
        return
    try:
        text = raw.decode("utf-8")
    except UnicodeError as exc:
        raise PhysicalAcceptanceError(f"text artifact {name} is not UTF-8") from exc
    if FORBIDDEN_TEXT.search(text):
        raise PhysicalAcceptanceError(f"text artifact {name} contains forbidden sensitive data")


def _validate_gate(
    gate: str,
    directory: Path,
    candidate: Mapping[str, str],
    keyring: Path,
    *,
    signature_verifier: Callable[[Path, Path, Path], Mapping[str, str]],
) -> dict[str, Any]:
    if directory.is_symlink() or not directory.is_dir():
        raise PhysicalAcceptanceError(f"physical gate directory is unavailable: {gate}")
    manifest_path = directory / "evidence.json"
    signature_path = directory / "evidence.json.gpg"
    manifest, manifest_raw = _load_json(manifest_path, MAX_MANIFEST_BYTES, f"{gate} manifest")
    try:
        signature = signature_verifier(manifest_path, signature_path, keyring)
    except (OSError, RuntimeError) as exc:
        raise PhysicalAcceptanceError(f"{gate} signature verification failed") from exc
    if set(signature) != {
        "manifestSha256",
        "signatureSha256",
        "keyringSha256",
        "signerFingerprint",
    }:
        raise PhysicalAcceptanceError(f"{gate} signature verifier returned an invalid contract")
    for name in ("manifestSha256", "signatureSha256", "keyringSha256"):
        if not isinstance(signature[name], str) or SHA256.fullmatch(signature[name]) is None:
            raise PhysicalAcceptanceError(f"{gate} signature verifier returned an invalid digest")
    if (
        not isinstance(signature["signerFingerprint"], str)
        or OPENPGP_FINGERPRINT.fullmatch(signature["signerFingerprint"]) is None
    ):
        raise PhysicalAcceptanceError(f"{gate} signature verifier returned an invalid signer")
    if signature["manifestSha256"] != _sha256(manifest_raw):
        raise PhysicalAcceptanceError(f"{gate} signature belongs to another manifest")

    _exact(
        manifest,
        {
            "schemaVersion",
            "kind",
            "gate",
            "candidate",
            "execution",
            "hardware",
            "result",
            "primaryLog",
            "artifacts",
        },
        f"{gate} manifest",
    )
    manifest_candidate = _exact(
        manifest["candidate"],
        {"indexId", "sourceRevision", "agentRevision", "releaseTag"},
        f"{gate} candidate",
    )
    if manifest_candidate != {
        "indexId": candidate["indexId"],
        "sourceRevision": candidate["sourceRevision"],
        "agentRevision": candidate["agentRevision"],
        "releaseTag": candidate["releaseTag"],
    }:
        raise PhysicalAcceptanceError(f"{gate} evidence belongs to another release candidate")
    execution = _exact(
        manifest["execution"], {"labRunId", "startedAt", "finishedAt"}, f"{gate} execution"
    )
    try:
        run_id = uuid.UUID(str(execution["labRunId"]))
    except (ValueError, AttributeError) as exc:
        raise PhysicalAcceptanceError(f"{gate} lab run ID is not one UUIDv4") from exc
    if run_id.version != 4 or str(run_id) != execution["labRunId"]:
        raise PhysicalAcceptanceError(f"{gate} lab run ID is not one canonical UUIDv4")
    started = _utc_time(execution["startedAt"], f"{gate} start")
    finished = _utc_time(execution["finishedAt"], f"{gate} finish")
    duration = finished - started
    minimum_duration = timedelta(seconds=int(GATE_REQUIREMENTS[gate]["minimumDurationSeconds"]))
    if not minimum_duration <= duration <= timedelta(days=7):
        raise PhysicalAcceptanceError(f"{gate} execution duration is invalid")

    hardware = _exact(
        manifest["hardware"],
        {"architecture", "profileSha256", "deviceCount", "serialsRedacted"},
        f"{gate} hardware",
    )
    requirement = GATE_REQUIREMENTS[gate]
    architecture = hardware["architecture"]
    if (
        architecture not in {"x86_64", "arm64"}
        or (requirement["architecture"] is not None and architecture != requirement["architecture"])
        or not isinstance(hardware["profileSha256"], str)
        or SHA256.fullmatch(hardware["profileSha256"]) is None
        or not isinstance(hardware["deviceCount"], int)
        or isinstance(hardware["deviceCount"], bool)
        or hardware["deviceCount"] < int(requirement["minimumDevices"])
        or hardware["serialsRedacted"] is not True
    ):
        raise PhysicalAcceptanceError(f"{gate} hardware evidence is invalid")

    expected_marker = _expected_marker(gate, candidate)
    result = _exact(
        manifest["result"],
        {"passed", "marker", "deliveryRequirements"},
        f"{gate} result",
    )
    primary_log = manifest["primaryLog"]
    artifacts = manifest["artifacts"]
    if (
        manifest["schemaVersion"] != 1
        or manifest["kind"] != "echo.physical-acceptance-gate"
        or manifest["gate"] != gate
        or result
        != {
            "passed": True,
            "marker": expected_marker,
            "deliveryRequirements": list(requirement["deliveryRequirements"]),
        }
        or not isinstance(primary_log, str)
        or Path(primary_log).suffix.casefold() != ".log"
        or not isinstance(artifacts, list)
        or not 1 <= len(artifacts) <= 32
    ):
        raise PhysicalAcceptanceError(f"{gate} result contract is invalid")

    artifact_names: list[str] = []
    artifact_records: list[dict[str, Any]] = []
    total_size = 0
    primary_raw: bytes | None = None
    hardware_profile_raw: bytes | None = None
    gate_result_raw: bytes | None = None
    operations_systemd_lifecycle_raw: bytes | None = None
    power_state_lifecycle_raw: bytes | None = None
    storage_recovery_lifecycle_raw: bytes | None = None
    protocol_interoperability_lifecycle_raw: bytes | None = None
    device_endurance_lifecycle_raw: bytes | None = None
    hub_lifecycle_plan_raw: bytes | None = None
    hub_lifecycle_result_raw: bytes | None = None
    paperless_functional_plan_raw: bytes | None = None
    paperless_functional_result_raw: bytes | None = None
    lan_discovery_functional_plan_raw: bytes | None = None
    lan_discovery_functional_result_raw: bytes | None = None
    lan_discovery_probe_raw: dict[str, bytes] = {}
    bare_metal_lifecycle_raw: bytes | None = None
    artifact_bytes: dict[str, bytes] = {}
    for item in artifacts:
        record = _exact(item, {"name", "sha256", "size"}, f"{gate} artifact")
        name = record["name"]
        if (
            not isinstance(name, str)
            or ARTIFACT_NAME.fullmatch(name) is None
            or name in {"evidence.json", "evidence.json.gpg"}
            or name in artifact_names
            or not isinstance(record["sha256"], str)
            or SHA256.fullmatch(record["sha256"]) is None
            or not isinstance(record["size"], int)
            or isinstance(record["size"], bool)
            or record["size"] <= 0
        ):
            raise PhysicalAcceptanceError(f"{gate} artifact declaration is invalid")
        raw = _read_regular(directory / name, MAX_ARTIFACT_BYTES, f"{gate} artifact {name}")
        if record["size"] != len(raw) or record["sha256"] != _sha256(raw):
            raise PhysicalAcceptanceError(f"{gate} artifact {name} does not match its declaration")
        _privacy_scan(name, raw)
        total_size += len(raw)
        if total_size > MAX_TOTAL_GATE_BYTES:
            raise PhysicalAcceptanceError(f"{gate} artifact set exceeds its total size bound")
        artifact_names.append(name)
        artifact_records.append(dict(record))
        artifact_bytes[name] = raw
        if name == primary_log:
            primary_raw = raw
        if name == HARDWARE_PROFILE_NAME:
            hardware_profile_raw = raw
        if name == GATE_RESULT_NAME:
            gate_result_raw = raw
        if name == OPERATIONS_SYSTEMD_LIFECYCLE_NAME:
            operations_systemd_lifecycle_raw = raw
        if name == POWER_STATE_LIFECYCLE_NAME:
            power_state_lifecycle_raw = raw
        if name == STORAGE_RECOVERY_LIFECYCLE_NAME:
            storage_recovery_lifecycle_raw = raw
        if name == PROTOCOL_INTEROPERABILITY_LIFECYCLE_NAME:
            protocol_interoperability_lifecycle_raw = raw
        if name == DEVICE_ENDURANCE_LIFECYCLE_NAME:
            device_endurance_lifecycle_raw = raw
        if name == HUB_LIFECYCLE_PLAN_NAME:
            hub_lifecycle_plan_raw = raw
        if name == HUB_LIFECYCLE_RESULT_NAME:
            hub_lifecycle_result_raw = raw
        if name == PAPERLESS_FUNCTIONAL_PLAN_NAME:
            paperless_functional_plan_raw = raw
        if name == PAPERLESS_FUNCTIONAL_RESULT_NAME:
            paperless_functional_result_raw = raw
        if name == LAN_DISCOVERY_FUNCTIONAL_PLAN_NAME:
            lan_discovery_functional_plan_raw = raw
        if name == LAN_DISCOVERY_FUNCTIONAL_RESULT_NAME:
            lan_discovery_functional_result_raw = raw
        if name in LAN_DISCOVERY_PROBE_NAMES:
            lan_discovery_probe_raw[name] = raw
        if name == BARE_METAL_LIFECYCLE_NAME:
            bare_metal_lifecycle_raw = raw
    if artifact_names != sorted(artifact_names) or primary_raw is None:
        raise PhysicalAcceptanceError(f"{gate} artifacts are not sorted or omit the primary log")
    hardware_profiles = [
        record
        for record in artifact_records
        if record["name"] == HARDWARE_PROFILE_NAME and record["sha256"] == hardware["profileSha256"]
    ]
    if len(hardware_profiles) != 1 or hardware_profile_raw is None:
        raise PhysicalAcceptanceError(
            f"{gate} hardware profile digest does not match {HARDWARE_PROFILE_NAME}"
        )
    hardware_profile = _validate_hardware_profile(
        hardware_profile_raw,
        gate=gate,
        architecture=architecture,
        device_count=hardware["deviceCount"],
    )
    if gate_result_raw is None:
        raise PhysicalAcceptanceError(f"{gate} artifacts omit required {GATE_RESULT_NAME}")
    _validate_gate_result(gate_result_raw, gate=gate)
    artifact_lookup = {record["name"]: record for record in artifact_records}
    if gate == OPERATIONS_SYSTEMD_GATE:
        if operations_systemd_lifecycle_raw is None:
            raise PhysicalAcceptanceError(
                f"{gate} artifacts omit required {OPERATIONS_SYSTEMD_LIFECYCLE_NAME}"
            )
        _validate_operations_systemd_lifecycle(
            operations_systemd_lifecycle_raw,
            artifacts=artifact_lookup,
            candidate=candidate,
        )
    elif operations_systemd_lifecycle_raw is not None:
        raise PhysicalAcceptanceError(
            f"{gate} must not include {OPERATIONS_SYSTEMD_LIFECYCLE_NAME}"
        )
    if gate == OPERATIONS_SYSTEMD_GATE:
        if power_state_lifecycle_raw is None:
            raise PhysicalAcceptanceError(
                f"{gate} artifacts omit required {POWER_STATE_LIFECYCLE_NAME}"
            )
        _validate_power_state_lifecycle(
            power_state_lifecycle_raw,
            artifacts=artifact_lookup,
            artifact_bytes=artifact_bytes,
            candidate=candidate,
        )
    elif power_state_lifecycle_raw is not None:
        raise PhysicalAcceptanceError(f"{gate} must not include {POWER_STATE_LIFECYCLE_NAME}")
    if gate == STORAGE_RECOVERY_GATE:
        if storage_recovery_lifecycle_raw is None:
            raise PhysicalAcceptanceError(
                f"{gate} artifacts omit required {STORAGE_RECOVERY_LIFECYCLE_NAME}"
            )
        _validate_storage_recovery_lifecycle(
            storage_recovery_lifecycle_raw,
            artifacts=artifact_lookup,
            candidate=candidate,
        )
    elif storage_recovery_lifecycle_raw is not None:
        raise PhysicalAcceptanceError(f"{gate} must not include {STORAGE_RECOVERY_LIFECYCLE_NAME}")
    if gate == PROTOCOL_INTEROPERABILITY_GATE:
        if protocol_interoperability_lifecycle_raw is None:
            raise PhysicalAcceptanceError(
                f"{gate} artifacts omit required {PROTOCOL_INTEROPERABILITY_LIFECYCLE_NAME}"
            )
        _validate_protocol_interoperability_lifecycle(
            protocol_interoperability_lifecycle_raw,
            artifacts=artifact_lookup,
            candidate=candidate,
        )
    elif protocol_interoperability_lifecycle_raw is not None:
        raise PhysicalAcceptanceError(
            f"{gate} must not include {PROTOCOL_INTEROPERABILITY_LIFECYCLE_NAME}"
        )
    if gate in DEVICE_ENDURANCE_GATES:
        if hub_lifecycle_plan_raw is None or hub_lifecycle_result_raw is None:
            raise PhysicalAcceptanceError(
                f"{gate} artifacts omit required Hub nine-app lifecycle evidence"
            )
        try:
            hub_lab.validate_evidence_bytes(
                hub_lifecycle_plan_raw,
                hub_lifecycle_result_raw,
                expected_candidate=candidate,
            )
        except hub_lab.HubLifecycleLabError as exc:
            raise PhysicalAcceptanceError(
                f"{gate} Hub nine-app lifecycle evidence is invalid: {exc}"
            ) from exc
        if paperless_functional_plan_raw is None or paperless_functional_result_raw is None:
            raise PhysicalAcceptanceError(
                f"{gate} artifacts omit required Paperless OCR/Office functional evidence"
            )
        try:
            paperless_lab.validate_evidence_bytes(
                paperless_functional_plan_raw,
                paperless_functional_result_raw,
                expected_candidate=candidate,
            )
        except paperless_lab.PaperlessFunctionalLabError as exc:
            raise PhysicalAcceptanceError(
                f"{gate} Paperless OCR/Office functional evidence is invalid: {exc}"
            ) from exc
        if lan_discovery_functional_plan_raw is None or lan_discovery_functional_result_raw is None:
            raise PhysicalAcceptanceError(
                f"{gate} artifacts omit required LAN discovery functional evidence"
            )
        if set(lan_discovery_probe_raw) != set(LAN_DISCOVERY_PROBE_NAMES):
            raise PhysicalAcceptanceError(
                f"{gate} artifacts omit required LAN discovery probe evidence"
            )
        try:
            lan_plan, lan_result = lan_discovery_lab.validate_evidence_bytes(
                lan_discovery_functional_plan_raw,
                lan_discovery_functional_result_raw,
                expected_candidate=candidate,
            )
            lan_discovery_lab.validate_probe_artifacts(
                lan_plan,
                lan_result,
                lan_discovery_probe_raw,
            )
        except lan_discovery_lab.LanDiscoveryFunctionalLabError as exc:
            raise PhysicalAcceptanceError(
                f"{gate} LAN discovery functional evidence is invalid: {exc}"
            ) from exc
        if device_endurance_lifecycle_raw is None:
            raise PhysicalAcceptanceError(
                f"{gate} artifacts omit required {DEVICE_ENDURANCE_LIFECYCLE_NAME}"
            )
        _validate_device_endurance_lifecycle(
            device_endurance_lifecycle_raw,
            artifacts=artifact_lookup,
            candidate=candidate,
            gate=gate,
        )
    elif (
        device_endurance_lifecycle_raw is not None
        or hub_lifecycle_plan_raw is not None
        or hub_lifecycle_result_raw is not None
        or paperless_functional_plan_raw is not None
        or paperless_functional_result_raw is not None
        or lan_discovery_functional_plan_raw is not None
        or lan_discovery_functional_result_raw is not None
        or lan_discovery_probe_raw
    ):
        raise PhysicalAcceptanceError(
            f"{gate} must not include device, Hub, Paperless or LAN functional evidence"
        )
    if gate == BARE_METAL_GATE:
        if bare_metal_lifecycle_raw is None:
            raise PhysicalAcceptanceError(
                f"{gate} artifacts omit required {BARE_METAL_LIFECYCLE_NAME}"
            )
        _validate_bare_metal_lifecycle(
            bare_metal_lifecycle_raw,
            artifacts=artifact_lookup,
            artifact_bytes=artifact_bytes,
            candidate=candidate,
        )
    elif bare_metal_lifecycle_raw is not None:
        raise PhysicalAcceptanceError(f"{gate} must not include {BARE_METAL_LIFECYCLE_NAME}")
    expected_files = {"evidence.json", "evidence.json.gpg", *artifact_names}
    try:
        actual_files = {entry.name for entry in directory.iterdir()}
    except OSError as exc:
        raise PhysicalAcceptanceError(f"cannot enumerate {gate} evidence") from exc
    if actual_files != expected_files or any(
        entry.is_symlink() or not entry.is_file() for entry in directory.iterdir()
    ):
        raise PhysicalAcceptanceError(f"{gate} directory contains missing or unexpected files")
    try:
        primary_text = primary_raw.decode("utf-8")
    except UnicodeError as exc:
        raise PhysicalAcceptanceError(f"{gate} primary log is not UTF-8") from exc
    if primary_text.splitlines().count(expected_marker) != 1:
        raise PhysicalAcceptanceError(f"{gate} primary log lacks one unique success marker")

    artifact_set = json.dumps(artifact_records, sort_keys=True, separators=(",", ":")).encode()
    gate_report = {
        "manifestSha256": signature["manifestSha256"],
        "signatureSha256": signature["signatureSha256"],
        "keyringSha256": signature["keyringSha256"],
        "signerFingerprint": signature["signerFingerprint"],
        "hardwareProfileSha256": hardware["profileSha256"],
        "hardwareProfileClass": hardware_profile["profileClass"],
        "deliveryRequirements": list(requirement["deliveryRequirements"]),
        "gateResultSha256": _sha256(gate_result_raw),
        "verifiedChecks": list(GATE_REQUIREMENTS[gate]["resultChecks"]),
        "architecture": architecture,
        "deviceCount": hardware["deviceCount"],
        "labRunId": execution["labRunId"],
        "startedAt": execution["startedAt"],
        "finishedAt": execution["finishedAt"],
        "durationSeconds": duration.total_seconds(),
        "artifactSetSha256": _sha256(artifact_set),
        "artifactCount": len(artifact_records),
    }
    if operations_systemd_lifecycle_raw is not None:
        gate_report["operationsSystemdLifecycleSha256"] = _sha256(operations_systemd_lifecycle_raw)
    if power_state_lifecycle_raw is not None:
        gate_report["powerStateLifecycleSha256"] = _sha256(power_state_lifecycle_raw)
    if storage_recovery_lifecycle_raw is not None:
        gate_report["storageRecoveryLifecycleSha256"] = _sha256(storage_recovery_lifecycle_raw)
    if protocol_interoperability_lifecycle_raw is not None:
        gate_report["protocolInteroperabilityLifecycleSha256"] = _sha256(
            protocol_interoperability_lifecycle_raw
        )
    if device_endurance_lifecycle_raw is not None:
        gate_report["deviceEnduranceLifecycleSha256"] = _sha256(device_endurance_lifecycle_raw)
    if hub_lifecycle_plan_raw is not None and hub_lifecycle_result_raw is not None:
        gate_report["hubLifecyclePlanSha256"] = _sha256(hub_lifecycle_plan_raw)
        gate_report["hubLifecycleResultSha256"] = _sha256(hub_lifecycle_result_raw)
    if paperless_functional_plan_raw is not None and paperless_functional_result_raw is not None:
        gate_report["paperlessFunctionalPlanSha256"] = _sha256(paperless_functional_plan_raw)
        gate_report["paperlessFunctionalResultSha256"] = _sha256(paperless_functional_result_raw)
    if (
        lan_discovery_functional_plan_raw is not None
        and lan_discovery_functional_result_raw is not None
    ):
        gate_report["lanDiscoveryFunctionalPlanSha256"] = _sha256(lan_discovery_functional_plan_raw)
        gate_report["lanDiscoveryFunctionalResultSha256"] = _sha256(
            lan_discovery_functional_result_raw
        )
    if bare_metal_lifecycle_raw is not None:
        gate_report["bareMetalRecoveryLifecycleSha256"] = _sha256(bare_metal_lifecycle_raw)
    return gate_report


def verify_acceptance(
    *,
    candidate_index: Path,
    evidence_root: Path,
    keyring: Path,
    signature_verifier: Callable[[Path, Path, Path], Mapping[str, str]],
) -> dict[str, Any]:
    candidate_value, candidate_raw = _load_json(
        candidate_index, MAX_MANIFEST_BYTES, "candidate evidence index"
    )
    candidate = _validate_candidate(candidate_value, candidate_raw)
    if not evidence_root.is_absolute() or evidence_root.is_symlink():
        raise PhysicalAcceptanceError("physical evidence root must be absolute and non-symlink")
    root = evidence_root.resolve(strict=True)
    if not root.is_dir():
        raise PhysicalAcceptanceError("physical evidence root is unavailable")
    if {entry.name for entry in root.iterdir()} != set(PHYSICAL_GATES) or any(
        entry.is_symlink() or not entry.is_dir() for entry in root.iterdir()
    ):
        raise PhysicalAcceptanceError(
            "physical evidence root must contain exactly six gate folders"
        )

    gates = {
        gate: _validate_gate(
            gate,
            root / gate,
            candidate,
            keyring,
            signature_verifier=signature_verifier,
        )
        for gate in PHYSICAL_GATES
    }
    keyring_digests = {record["keyringSha256"] for record in gates.values()}
    if len(keyring_digests) != 1:
        raise PhysicalAcceptanceError("physical gates were not verified by one acceptance keyring")
    signer_fingerprints = {record["signerFingerprint"] for record in gates.values()}
    if len(signer_fingerprints) != 1:
        raise PhysicalAcceptanceError("physical gates were not signed by one acceptance key")
    lab_run_ids = {record["labRunId"] for record in gates.values()}
    if len(lab_run_ids) != len(PHYSICAL_GATES):
        raise PhysicalAcceptanceError("physical gates must use six distinct lab run IDs")
    covered_delivery_requirements = {
        requirement for record in gates.values() for requirement in record["deliveryRequirements"]
    }
    if covered_delivery_requirements != set(DELIVERY_REQUIREMENTS):
        raise PhysicalAcceptanceError("physical gates do not cover every NAS delivery requirement")

    payload: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "echo.nas-product-delivery-release",
        "candidate": candidate,
        "acceptanceKeyringSha256": next(iter(keyring_digests)),
        "acceptanceSignerFingerprint": next(iter(signer_fingerprints)),
        "gates": gates,
        "deliveryRequirementsVerified": list(DELIVERY_REQUIREMENTS),
        "ciReleaseCandidateReady": True,
        "physicalAcceptanceComplete": True,
        "nasProductDeliveryReady": True,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    payload["reportId"] = _sha256(canonical)
    return payload


def _write_new(path: Path, payload: Mapping[str, Any]) -> None:
    if path.name in {"", ".", ".."} or path.parent.is_symlink():
        raise PhysicalAcceptanceError("output path is unsafe")
    parent = path.parent.resolve(strict=True)
    target = parent / path.name
    if target.exists() or target.is_symlink():
        raise PhysicalAcceptanceError("output must be a new path")
    data = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o444)
        try:
            os.link(temporary, target, follow_symlinks=False)
        except FileExistsError as exc:
            raise PhysicalAcceptanceError("output must remain a new path") from exc
        temporary.unlink()
        directory = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-index", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--acceptance-keyring", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    signature_verifier: Callable[[Path, Path, Path], Mapping[str, str]] | None = None,
) -> int:
    args = _parser().parse_args(argv)
    if signature_verifier is None:
        signature_verifier = _verify_physical_signature
    try:
        payload = verify_acceptance(
            candidate_index=args.candidate_index,
            evidence_root=args.evidence_root,
            keyring=args.acceptance_keyring,
            signature_verifier=signature_verifier,
        )
        _write_new(args.output, payload)
    except (OSError, PhysicalAcceptanceError) as exc:
        print(f"Echo physical acceptance failed: {exc}", file=sys.stderr)
        return 1
    print(
        "ECHO_NAS_PRODUCT_DELIVERY_READY "
        f"candidate={payload['candidate']['indexId']} gates={len(payload['gates'])} "
        f"report={payload['reportId']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
