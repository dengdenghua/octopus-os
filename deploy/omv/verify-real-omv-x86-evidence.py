#!/usr/bin/env python3
"""Verify one real OMV x86 CI evidence artifact against its plugin and revision."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 6
MAX_EVIDENCE_BYTES = 256 * 1024
MAX_PLUGIN_BYTES = 32 * 1024 * 1024
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
UUID_PATTERN = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
REVISION_PATTERN = re.compile(r"[0-9a-f]{40}")
OMV_VERSION_PATTERN = re.compile(r"(?:[0-9]+:)?8(?:[.+~:-][0-9A-Za-z.+~:-]+)?")
PLUGIN_FILENAME_PATTERN = re.compile(
    r"openmediavault-echo-os_(?P<version>[0-9][0-9A-Za-z.+~:-]*-[0-9][0-9A-Za-z.+~]*)_all\.deb"
)
ROOT_KEYS = {
    "schemaVersion",
    "environment",
    "architecture",
    "distribution",
    "distributionVersion",
    "omvVersion",
    "pluginVersion",
    "pluginSha256",
    "supportMatrix",
    "preflight",
    "checks",
    "nfs",
    "accounts",
    "sourceRevision",
}
PREFLIGHT_KEYS = {
    "ready",
    "smbHostnameCompatible",
    "netplan",
    "warnings",
}
NETPLAN_KEYS = {
    "configurationFiles",
    "activeNameserverFiles",
    "importerFields",
    "modelHasDnsnameservers",
    "modelHasDnsservers",
    "knownFieldMismatch",
    "compatible",
}
CHECK_KEYS = {
    "realOmvPackage",
    "realOmvRpc",
    "activeNetplanBehaviorVerified",
    "netplanProbeResult",
    "upstreamFilesUnchangedByPreflight",
    "workbenchGenerated",
    "bridgeSystemdActive",
    "socketContract",
    "systemdPolicy",
    "purgePreservedNasData",
    "reinstallHealthy",
    "sentinelSha256",
}
TRUE_CHECKS = CHECK_KEYS - {
    "netplanProbeResult",
    "socketContract",
    "systemdPolicy",
    "sentinelSha256",
}
SYSTEMD_POLICY = {
    "User=root",
    "Group=echo-omv",
    "NoNewPrivileges=yes",
    "PrivateNetwork=yes",
    "ProtectSystem=strict",
}
WARNING_KEYS = {"code", "message", "remediation"}
NFS_KEYS = {
    "clientCidr",
    "serverIp",
    "filesystemUuid",
    "sharedFolderUuid",
    "sharedFolderPlanId",
    "privilegePlanId",
    "shareUuid",
    "planId",
    "exportPath",
    "remotePath",
    "rwWriteSha256",
    "preservedFileSha256",
    "createdByEchoBridge",
    "sharedFolderCreatedByEchoBridge",
    "sharedFolderPermissionsVerified",
    "sharedFolderPrivilegeCreatedByEchoBridge",
    "omvConfigVerified",
    "exportsVerified",
    "serverActive",
    "tcp2049Listening",
    "rwMountVerified",
    "readOnlyRemountVerified",
    "purgePreservedShare",
    "purgePreservedPayload",
    "purgePreservedPrivilege",
    "reinstallReadbackVerified",
    "reinstallPayloadVerified",
    "reinstallPrivilegeReadbackVerified",
    "reinstallPrivilegePlanNoop",
}
NFS_TRUE_FIELDS = {
    "createdByEchoBridge",
    "sharedFolderCreatedByEchoBridge",
    "sharedFolderPermissionsVerified",
    "sharedFolderPrivilegeCreatedByEchoBridge",
    "omvConfigVerified",
    "exportsVerified",
    "serverActive",
    "tcp2049Listening",
    "rwMountVerified",
    "readOnlyRemountVerified",
    "purgePreservedShare",
    "purgePreservedPayload",
    "purgePreservedPrivilege",
    "reinstallReadbackVerified",
    "reinstallPayloadVerified",
    "reinstallPrivilegeReadbackVerified",
    "reinstallPrivilegePlanNoop",
}
ACCOUNT_KEYS = {
    "groupName",
    "groupGid",
    "groupPlanId",
    "userName",
    "userUid",
    "userGid",
    "userPlanId",
    "passwordResetPlanId",
    "smbShareName",
    "smbShareUuid",
    "smbPlanId",
    "smbProtocol",
    "smbPayloadSha256",
    "passwordNeverReturned",
    "nologinVerified",
    "noSshKeysVerified",
    "selfModificationDisabled",
    "sambaAccountVerified",
    "smbAuthenticationVerified",
    "smbReadWriteVerified",
    "oldPasswordRejected",
    "replacementPasswordAuthenticationVerified",
    "accountFieldsPreservedAfterPasswordReset",
    "purgePreservedGroup",
    "purgePreservedUser",
    "purgePreservedSambaAccount",
    "purgePreservedSmbAuthentication",
    "purgePreservedSmbPayload",
    "purgeSmbPayloadSha256",
    "reinstallReadbackVerified",
    "existingGroupCreateRejected",
    "existingUserCreateRejected",
    "reinstallPasswordNeverReturned",
    "reinstallSmbAuthenticationVerified",
    "reinstallSmbPayloadVerified",
    "reinstallSmbPayloadSha256",
    "reinstallSmbPlanNoop",
}
ACCOUNT_TRUE_FIELDS = ACCOUNT_KEYS - {
    "groupName",
    "groupGid",
    "groupPlanId",
    "userName",
    "userUid",
    "userGid",
    "userPlanId",
    "passwordResetPlanId",
    "smbShareName",
    "smbShareUuid",
    "smbPlanId",
    "smbProtocol",
    "smbPayloadSha256",
    "purgeSmbPayloadSha256",
    "reinstallSmbPayloadSha256",
}


class EvidenceError(RuntimeError):
    """The evidence cannot prove the claimed real OMV x86 result."""


def _safe_read(path: Path, *, maximum: int, label: str) -> bytes:
    if not path.is_absolute() or path.is_symlink():
        raise EvidenceError(f"{label} must be one absolute non-symlink file")
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise EvidenceError(f"{label} cannot be opened safely") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or not 1 <= info.st_size <= maximum:
            raise EvidenceError(f"{label} is empty, oversized, or not regular")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, maximum - total + 1))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum:
                raise EvidenceError(f"{label} exceeds its size limit")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceError(f"evidence contains a duplicate field: {key}")
        result[key] = value
    return result


def _exact_object(value: Any, keys: set[str], *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise EvidenceError(f"{label} has unexpected or missing fields")
    return value


def _bounded_text(value: Any, *, label: str, maximum: int = 512) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= maximum
        or any(character < " " for character in value)
    ):
        raise EvidenceError(f"{label} is not bounded printable text")
    return value


def _string_list(value: Any, *, label: str, maximum_items: int = 64) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum_items:
        raise EvidenceError(f"{label} is not a bounded list")
    result: list[str] = []
    for index, item in enumerate(value):
        result.append(_bounded_text(item, label=f"{label}[{index}]", maximum=255))
    if len(set(result)) != len(result):
        raise EvidenceError(f"{label} contains duplicates")
    return result


def _validate_warnings(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list) or len(value) > 16:
        raise EvidenceError("preflight warnings are not a bounded list")
    warnings: list[dict[str, str]] = []
    codes: set[str] = set()
    for index, raw in enumerate(value):
        warning = _exact_object(raw, WARNING_KEYS, label=f"preflight warning {index}")
        code = _bounded_text(warning["code"], label="warning code", maximum=128)
        if re.fullmatch(r"[a-z0-9_]+", code) is None or code in codes:
            raise EvidenceError("preflight warning code is invalid or duplicated")
        codes.add(code)
        warnings.append(
            {
                "code": code,
                "message": _bounded_text(warning["message"], label="warning message", maximum=2048),
                "remediation": _bounded_text(
                    warning["remediation"], label="warning remediation", maximum=2048
                ),
            }
        )
    return warnings


def _validate_netplan(value: Any) -> dict[str, Any]:
    netplan = _exact_object(value, NETPLAN_KEYS, label="preflight netplan")
    configuration_files = _string_list(
        netplan["configurationFiles"], label="Netplan configuration files"
    )
    active_files = _string_list(
        netplan["activeNameserverFiles"], label="active Netplan nameserver files"
    )
    if not set(active_files).issubset(configuration_files):
        raise EvidenceError("active Netplan nameserver files are not in the file inventory")
    importer_fields = _string_list(netplan["importerFields"], label="OMV importer fields")
    if not set(importer_fields).issubset({"dnsservers", "dnsnameservers"}):
        raise EvidenceError("OMV importer fields contain an unsupported value")
    for field in (
        "modelHasDnsnameservers",
        "modelHasDnsservers",
        "knownFieldMismatch",
        "compatible",
    ):
        if not isinstance(netplan[field], bool):
            raise EvidenceError(f"preflight netplan {field} must be boolean")
    if netplan["compatible"] is not True:
        raise EvidenceError("final preflight did not prove Netplan compatibility")
    return netplan


def _validate_checks(value: Any) -> dict[str, Any]:
    checks = _exact_object(value, CHECK_KEYS, label="evidence checks")
    for field in TRUE_CHECKS:
        if checks[field] is not True:
            raise EvidenceError(f"required real OMV check did not pass: {field}")
    if checks["netplanProbeResult"] not in {"mismatch-blocked", "upstream-compatible"}:
        raise EvidenceError("the real OMV Netplan probe result is invalid")
    if checks["socketContract"] != "660:root:echo-omv:socket":
        raise EvidenceError("the real bridge socket contract is invalid")
    policy = _bounded_text(checks["systemdPolicy"], label="systemd policy", maximum=1024)
    observed_policy = {item for item in policy.split(",") if item}
    if observed_policy != SYSTEMD_POLICY:
        raise EvidenceError("the running bridge systemd policy is incomplete or unexpected")
    sentinel = checks["sentinelSha256"]
    if not isinstance(sentinel, str) or SHA256_PATTERN.fullmatch(sentinel) is None:
        raise EvidenceError("the preserved NAS sentinel digest is invalid")
    return checks


def _validate_nfs(value: Any) -> dict[str, Any]:
    nfs = _exact_object(value, NFS_KEYS, label="real NFS evidence")
    for field in NFS_TRUE_FIELDS:
        if nfs[field] is not True:
            raise EvidenceError(f"required real NFS check did not pass: {field}")
    try:
        network = ipaddress.ip_network(nfs["clientCidr"], strict=True)
    except (TypeError, ValueError) as exc:
        raise EvidenceError("real NFS client network is invalid") from exc
    allowed_networks = (
        ipaddress.ip_network("10.0.0.0/8"),
        ipaddress.ip_network("172.16.0.0/12"),
        ipaddress.ip_network("192.168.0.0/16"),
    )
    if network.version != 4 or not any(network.subnet_of(item) for item in allowed_networks):
        raise EvidenceError("real NFS client network is not canonical RFC1918 IPv4")
    if nfs["clientCidr"] != network.with_prefixlen:
        raise EvidenceError("real NFS client network is not canonical RFC1918 IPv4")
    try:
        server_ip = ipaddress.ip_address(nfs["serverIp"])
    except (TypeError, ValueError) as exc:
        raise EvidenceError("real NFS server address is invalid") from exc
    if server_ip.version != 4 or server_ip not in network:
        raise EvidenceError("real NFS server address is outside the client network")
    for field in ("filesystemUuid", "sharedFolderUuid", "shareUuid"):
        identifier = nfs[field]
        if not isinstance(identifier, str) or UUID_PATTERN.fullmatch(identifier) is None:
            raise EvidenceError(f"real NFS {field} is invalid")
    for field in ("sharedFolderPlanId", "privilegePlanId", "planId"):
        plan_id = nfs[field]
        if not isinstance(plan_id, str) or SHA256_PATTERN.fullmatch(plan_id) is None:
            raise EvidenceError(f"real NFS {field} is invalid")
    if nfs["exportPath"] != "/export/echo-ci-nfs" or nfs["remotePath"] != "/echo-ci-nfs":
        raise EvidenceError("real NFS export path is invalid")
    write_digest = nfs["rwWriteSha256"]
    preserved_digest = nfs["preservedFileSha256"]
    if (
        not isinstance(write_digest, str)
        or SHA256_PATTERN.fullmatch(write_digest) is None
        or preserved_digest != write_digest
    ):
        raise EvidenceError("real NFS preserved payload digest is invalid or changed")
    return nfs


def _validate_accounts(value: Any) -> dict[str, Any]:
    accounts = _exact_object(value, ACCOUNT_KEYS, label="real account evidence")
    if accounts["groupName"] != "familyci" or accounts["userName"] != "motherci":
        raise EvidenceError("real account evidence uses unexpected disposable identities")
    for field in ("groupGid", "userUid", "userGid"):
        identifier = accounts[field]
        if (
            isinstance(identifier, bool)
            or not isinstance(identifier, int)
            or not 0 <= identifier < 2**31
        ):
            raise EvidenceError(f"real account {field} is invalid")
    for field in ("groupPlanId", "userPlanId", "passwordResetPlanId"):
        plan_id = accounts[field]
        if not isinstance(plan_id, str) or SHA256_PATTERN.fullmatch(plan_id) is None:
            raise EvidenceError(f"real account {field} is invalid")
    if accounts["smbShareName"] != "echo-ci-nfs" or accounts["smbProtocol"] != "SMB3":
        raise EvidenceError("real account SMB endpoint or protocol is invalid")
    if (
        not isinstance(accounts["smbShareUuid"], str)
        or UUID_PATTERN.fullmatch(accounts["smbShareUuid"]) is None
        or not isinstance(accounts["smbPlanId"], str)
        or SHA256_PATTERN.fullmatch(accounts["smbPlanId"]) is None
        or not isinstance(accounts["smbPayloadSha256"], str)
        or SHA256_PATTERN.fullmatch(accounts["smbPayloadSha256"]) is None
    ):
        raise EvidenceError("real account SMB identifiers or payload digest are invalid")
    if (
        accounts["purgeSmbPayloadSha256"] != accounts["smbPayloadSha256"]
        or accounts["reinstallSmbPayloadSha256"] != accounts["smbPayloadSha256"]
    ):
        raise EvidenceError("real account SMB payload digest changed across plugin lifecycle")
    for field in ACCOUNT_TRUE_FIELDS:
        if accounts[field] is not True:
            raise EvidenceError(f"required real account check did not pass: {field}")
    return accounts


def verify(evidence_path: Path, plugin_path: Path, expected_revision: str) -> dict[str, Any]:
    if REVISION_PATTERN.fullmatch(expected_revision) is None:
        raise EvidenceError("expected source revision must be one lowercase Git SHA-1")
    evidence_bytes = _safe_read(
        evidence_path,
        maximum=MAX_EVIDENCE_BYTES,
        label="real OMV evidence",
    )
    plugin_bytes = _safe_read(
        plugin_path,
        maximum=MAX_PLUGIN_BYTES,
        label="native OMV plugin",
    )
    try:
        payload = json.loads(
            evidence_bytes.decode("utf-8"),
            object_pairs_hook=_unique_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceError("real OMV evidence is not strict UTF-8 JSON") from exc
    root = _exact_object(payload, ROOT_KEYS, label="real OMV evidence")
    if root["schemaVersion"] != SCHEMA_VERSION:
        raise EvidenceError("real OMV evidence schema is unsupported")
    expected_scalars = {
        "environment": "github-actions-disposable-systemd-container",
        "architecture": "x86_64",
        "distribution": "debian",
        "distributionVersion": "13",
        "supportMatrix": "debian-13+omv-8",
        "sourceRevision": expected_revision,
    }
    for field, expected in expected_scalars.items():
        if root[field] != expected:
            raise EvidenceError(f"real OMV evidence has an invalid {field}")
    omv_version = root["omvVersion"]
    if not isinstance(omv_version, str) or OMV_VERSION_PATTERN.fullmatch(omv_version) is None:
        raise EvidenceError("real OMV evidence does not identify an OMV 8 package version")
    filename_match = PLUGIN_FILENAME_PATTERN.fullmatch(plugin_path.name)
    if filename_match is None:
        raise EvidenceError("native OMV plugin filename is not canonical")
    plugin_version = filename_match.group("version")
    if root["pluginVersion"] != plugin_version:
        raise EvidenceError("evidence plugin version does not match the package filename")
    plugin_sha256 = hashlib.sha256(plugin_bytes).hexdigest()
    if root["pluginSha256"] != plugin_sha256:
        raise EvidenceError("evidence plugin digest does not match the package bytes")

    preflight = _exact_object(root["preflight"], PREFLIGHT_KEYS, label="evidence preflight")
    if preflight["ready"] is not True or preflight["smbHostnameCompatible"] is not True:
        raise EvidenceError("final real-host platform preflight was not ready")
    _validate_netplan(preflight["netplan"])
    warnings = _validate_warnings(preflight["warnings"])
    checks = _validate_checks(root["checks"])
    nfs = _validate_nfs(root["nfs"])
    accounts = _validate_accounts(root["accounts"])
    if (
        checks["netplanProbeResult"] == "mismatch-blocked"
        and not preflight["netplan"]["knownFieldMismatch"]
    ):
        raise EvidenceError("Netplan mismatch evidence contradicts the final preflight")
    if (
        checks["netplanProbeResult"] == "upstream-compatible"
        and preflight["netplan"]["knownFieldMismatch"]
    ):
        raise EvidenceError("upstream-compatible evidence contradicts the final preflight")
    return {
        "verified": True,
        "schemaVersion": SCHEMA_VERSION,
        "sourceRevision": expected_revision,
        "omvVersion": omv_version,
        "pluginVersion": plugin_version,
        "pluginSha256": plugin_sha256,
        "evidenceSha256": hashlib.sha256(evidence_bytes).hexdigest(),
        "netplanProbeResult": checks["netplanProbeResult"],
        "nfsShareUuid": nfs["shareUuid"],
        "nfsClientCidr": nfs["clientCidr"],
        "accountGroupName": accounts["groupName"],
        "accountUserName": accounts["userName"],
        "accountSmbShareName": accounts["smbShareName"],
        "accountSmbProtocol": accounts["smbProtocol"],
        "warningCount": len(warnings),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--plugin-package", required=True, type=Path)
    parser.add_argument("--expected-source-revision", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = verify(
            args.evidence,
            args.plugin_package,
            args.expected_source_revision,
        )
    except (EvidenceError, OSError) as exc:
        print(f"Echo real OMV x86 evidence verification failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
