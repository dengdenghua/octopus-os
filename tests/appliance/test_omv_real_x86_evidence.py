from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

_REPOSITORY = Path(__file__).resolve().parents[2]
_SCRIPT = _REPOSITORY / "deploy" / "omv" / "verify-real-omv-x86-evidence.py"
_SPEC = importlib.util.spec_from_file_location("echo_real_omv_x86_evidence", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
evidence = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = evidence
_SPEC.loader.exec_module(evidence)

REVISION = "0123456789abcdef0123456789abcdef01234567"
PLUGIN_VERSION = "0.2.0-1"


def _payload(plugin_bytes: bytes) -> dict[str, object]:
    return {
        "schemaVersion": 6,
        "environment": "github-actions-disposable-systemd-container",
        "architecture": "x86_64",
        "distribution": "debian",
        "distributionVersion": "13",
        "omvVersion": "8.5.6-1",
        "pluginVersion": PLUGIN_VERSION,
        "pluginSha256": hashlib.sha256(plugin_bytes).hexdigest(),
        "supportMatrix": "debian-13+omv-8",
        "preflight": {
            "ready": True,
            "smbHostnameCompatible": True,
            "netplan": {
                "configurationFiles": [],
                "activeNameserverFiles": [],
                "importerFields": ["dnsservers"],
                "modelHasDnsnameservers": True,
                "modelHasDnsservers": False,
                "knownFieldMismatch": True,
                "compatible": True,
            },
            "warnings": [
                {
                    "code": "omv_netplan_dns_field_mismatch_latent",
                    "message": "Known mismatch is latent.",
                    "remediation": "Upgrade before adding Netplan DNS.",
                }
            ],
        },
        "checks": {
            "realOmvPackage": True,
            "realOmvRpc": True,
            "activeNetplanBehaviorVerified": True,
            "netplanProbeResult": "mismatch-blocked",
            "upstreamFilesUnchangedByPreflight": True,
            "workbenchGenerated": True,
            "bridgeSystemdActive": True,
            "socketContract": "660:root:echo-omv:socket",
            "systemdPolicy": (
                "User=root,Group=echo-omv,NoNewPrivileges=yes,"
                "PrivateNetwork=yes,ProtectSystem=strict,"
            ),
            "purgePreservedNasData": True,
            "reinstallHealthy": True,
            "sentinelSha256": "a" * 64,
        },
        "nfs": {
            "clientCidr": "172.18.0.0/16",
            "serverIp": "172.18.0.2",
            "filesystemUuid": "11111111-2222-3333-4444-555555555555",
            "sharedFolderUuid": "22222222-3333-4444-5555-666666666666",
            "sharedFolderPlanId": "d" * 64,
            "privilegePlanId": "e" * 64,
            "shareUuid": "33333333-4444-4555-8666-777777777777",
            "planId": "b" * 64,
            "exportPath": "/export/echo-ci-nfs",
            "remotePath": "/echo-ci-nfs",
            "rwWriteSha256": "c" * 64,
            "preservedFileSha256": "c" * 64,
            "createdByEchoBridge": True,
            "sharedFolderCreatedByEchoBridge": True,
            "sharedFolderPermissionsVerified": True,
            "sharedFolderPrivilegeCreatedByEchoBridge": True,
            "omvConfigVerified": True,
            "exportsVerified": True,
            "serverActive": True,
            "tcp2049Listening": True,
            "rwMountVerified": True,
            "readOnlyRemountVerified": True,
            "purgePreservedShare": True,
            "purgePreservedPayload": True,
            "purgePreservedPrivilege": True,
            "reinstallReadbackVerified": True,
            "reinstallPayloadVerified": True,
            "reinstallPrivilegeReadbackVerified": True,
            "reinstallPrivilegePlanNoop": True,
        },
        "accounts": {
            "groupName": "familyci",
            "groupGid": 1000,
            "groupPlanId": "8" * 64,
            "userName": "motherci",
            "userUid": 1000,
            "userGid": 100,
            "userPlanId": "9" * 64,
            "passwordResetPlanId": "6" * 64,
            "smbShareName": "echo-ci-nfs",
            "smbShareUuid": "44444444-5555-4666-8777-888888888888",
            "smbPlanId": "a" * 64,
            "smbProtocol": "SMB3",
            "smbPayloadSha256": "f" * 64,
            "passwordNeverReturned": True,
            "nologinVerified": True,
            "noSshKeysVerified": True,
            "selfModificationDisabled": True,
            "sambaAccountVerified": True,
            "smbAuthenticationVerified": True,
            "smbReadWriteVerified": True,
            "oldPasswordRejected": True,
            "replacementPasswordAuthenticationVerified": True,
            "accountFieldsPreservedAfterPasswordReset": True,
            "purgePreservedGroup": True,
            "purgePreservedUser": True,
            "purgePreservedSambaAccount": True,
            "purgePreservedSmbAuthentication": True,
            "purgePreservedSmbPayload": True,
            "purgeSmbPayloadSha256": "f" * 64,
            "reinstallReadbackVerified": True,
            "existingGroupCreateRejected": True,
            "existingUserCreateRejected": True,
            "reinstallPasswordNeverReturned": True,
            "reinstallSmbAuthenticationVerified": True,
            "reinstallSmbPayloadVerified": True,
            "reinstallSmbPayloadSha256": "f" * 64,
            "reinstallSmbPlanNoop": True,
        },
        "sourceRevision": REVISION,
    }


def _fixture(tmp_path: Path) -> tuple[Path, Path, dict[str, object]]:
    plugin = tmp_path / f"openmediavault-echo-os_{PLUGIN_VERSION}_all.deb"
    plugin_bytes = b"deterministic native OMV plugin fixture"
    plugin.write_bytes(plugin_bytes)
    payload = _payload(plugin_bytes)
    artifact = tmp_path / "echo-real-omv-x86-evidence.json"
    artifact.write_text(json.dumps(payload), encoding="utf-8")
    return artifact, plugin, payload


def test_real_omv_evidence_verifies_exact_plugin_and_revision(tmp_path: Path) -> None:
    artifact, plugin, _payload_value = _fixture(tmp_path)

    assert os.access(_SCRIPT, os.X_OK)
    report = evidence.verify(artifact, plugin, REVISION)

    assert report["verified"] is True
    assert report["omvVersion"] == "8.5.6-1"
    assert report["pluginVersion"] == PLUGIN_VERSION
    assert report["pluginSha256"] == hashlib.sha256(plugin.read_bytes()).hexdigest()
    assert report["sourceRevision"] == REVISION
    assert report["netplanProbeResult"] == "mismatch-blocked"
    assert report["nfsShareUuid"] == "33333333-4444-4555-8666-777777777777"
    assert report["nfsClientCidr"] == "172.18.0.0/16"
    assert report["accountGroupName"] == "familyci"
    assert report["accountUserName"] == "motherci"
    assert report["accountSmbShareName"] == "echo-ci-nfs"
    assert report["accountSmbProtocol"] == "SMB3"
    assert report["warningCount"] == 1


@pytest.mark.parametrize(
    ("path", "value", "match"),
    [
        (("schemaVersion",), 1, "schema"),
        (("architecture",), "arm64", "architecture"),
        (("omvVersion",), "9.0.0-1", "OMV 8"),
        (("preflight", "ready"), False, "not ready"),
        (("preflight", "netplan", "compatible"), False, "Netplan compatibility"),
        (("checks", "realOmvRpc"), False, "realOmvRpc"),
        (("checks", "socketContract"), "666:root:root:socket", "socket contract"),
        (("checks", "netplanProbeResult"), "skipped", "probe result"),
        (("nfs", "rwMountVerified"), False, "rwMountVerified"),
        (
            ("nfs", "sharedFolderPermissionsVerified"),
            False,
            "sharedFolderPermissionsVerified",
        ),
        (
            ("nfs", "sharedFolderPrivilegeCreatedByEchoBridge"),
            False,
            "sharedFolderPrivilegeCreatedByEchoBridge",
        ),
        (("nfs", "clientCidr"), "203.0.113.0/24", "RFC1918"),
        (("nfs", "serverIp"), "172.19.0.2", "outside"),
        (("nfs", "shareUuid"), "not-a-uuid", "shareUuid"),
        (("nfs", "sharedFolderPlanId"), "short", "sharedFolderPlanId"),
        (("nfs", "privilegePlanId"), "short", "privilegePlanId"),
        (("nfs", "planId"), "short", "planId"),
        (("accounts", "smbAuthenticationVerified"), False, "smbAuthenticationVerified"),
        (("accounts", "oldPasswordRejected"), False, "oldPasswordRejected"),
        (("accounts", "passwordResetPlanId"), "short", "passwordResetPlanId"),
        (("accounts", "smbShareUuid"), "not-a-uuid", "SMB identifiers"),
        (("accounts", "smbProtocol"), "SMB1", "SMB endpoint or protocol"),
        (("accounts", "purgeSmbPayloadSha256"), "e" * 64, "changed"),
        (("nfs", "exportPath"), "/wrong", "export path"),
        (("nfs", "preservedFileSha256"), "d" * 64, "digest"),
        (("accounts", "passwordNeverReturned"), False, "passwordNeverReturned"),
        (("accounts", "userName"), "root", "disposable identities"),
        (("accounts", "groupPlanId"), "short", "groupPlanId"),
    ],
)
def test_real_omv_evidence_rejects_failed_or_off_matrix_claims(
    tmp_path: Path,
    path: tuple[str, ...],
    value: object,
    match: str,
) -> None:
    artifact, plugin, payload = _fixture(tmp_path)
    target = payload
    for key in path[:-1]:
        target = target[key]  # type: ignore[index,assignment]
    target[path[-1]] = value  # type: ignore[index]
    artifact.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(evidence.EvidenceError, match=match):
        evidence.verify(artifact, plugin, REVISION)


def test_real_omv_evidence_rejects_wrong_plugin_bytes_and_revision(tmp_path: Path) -> None:
    artifact, plugin, _payload_value = _fixture(tmp_path)
    plugin.write_bytes(plugin.read_bytes() + b"tampered")

    with pytest.raises(evidence.EvidenceError, match="digest"):
        evidence.verify(artifact, plugin, REVISION)
    with pytest.raises(evidence.EvidenceError, match="sourceRevision"):
        evidence.verify(
            artifact,
            plugin,
            "f" * 40,
        )


def test_real_omv_evidence_rejects_extra_and_duplicate_fields(tmp_path: Path) -> None:
    artifact, plugin, payload = _fixture(tmp_path)
    payload["unexpected"] = True
    artifact.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(evidence.EvidenceError, match="unexpected or missing"):
        evidence.verify(artifact, plugin, REVISION)

    artifact.write_text(
        json.dumps(_payload(plugin.read_bytes())).replace(
            '"schemaVersion": 6,',
            '"schemaVersion": 6, "schemaVersion": 6,',
            1,
        ),
        encoding="utf-8",
    )
    with pytest.raises(evidence.EvidenceError, match="duplicate field"):
        evidence.verify(artifact, plugin, REVISION)


def test_real_omv_evidence_rejects_netplan_contradiction(tmp_path: Path) -> None:
    artifact, plugin, payload = _fixture(tmp_path)
    changed = copy.deepcopy(payload)
    changed["checks"]["netplanProbeResult"] = "upstream-compatible"  # type: ignore[index]
    artifact.write_text(json.dumps(changed), encoding="utf-8")

    with pytest.raises(evidence.EvidenceError, match="contradicts"):
        evidence.verify(artifact, plugin, REVISION)


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks are unavailable")
def test_real_omv_evidence_rejects_symlink_artifact_and_package(tmp_path: Path) -> None:
    artifact, plugin, _payload_value = _fixture(tmp_path)
    artifact_link = tmp_path / "artifact-link.json"
    plugin_link = tmp_path / f"openmediavault-echo-os_{PLUGIN_VERSION}_all.deb"
    real_plugin = tmp_path / "real-plugin.deb"
    plugin.rename(real_plugin)
    os.symlink(artifact, artifact_link)
    os.symlink(real_plugin, plugin_link)

    with pytest.raises(evidence.EvidenceError, match="non-symlink"):
        evidence.verify(artifact_link, real_plugin, REVISION)
    with pytest.raises(evidence.EvidenceError, match="non-symlink"):
        evidence.verify(artifact, plugin_link, REVISION)


def test_real_omv_evidence_requires_absolute_paths(tmp_path: Path) -> None:
    artifact, plugin, _payload_value = _fixture(tmp_path)

    with pytest.raises(evidence.EvidenceError, match="absolute"):
        evidence.verify(Path(artifact.name), plugin, REVISION)
    with pytest.raises(evidence.EvidenceError, match="absolute"):
        evidence.verify(artifact, Path(plugin.name), REVISION)
