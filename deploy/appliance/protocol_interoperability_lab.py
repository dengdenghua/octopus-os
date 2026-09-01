#!/usr/bin/env python3
"""Run candidate-bound SMB/NFS probes on real Windows, macOS and Linux clients."""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import platform
import re
import stat
import subprocess  # nosec B404
import sys
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any

try:
    from deploy.appliance import operations_systemd as systemd
    from deploy.appliance import operations_systemd_lab as operations_lab
except ModuleNotFoundError:
    import operations_systemd as systemd
    import operations_systemd_lab as operations_lab

SCHEMA_VERSION = 1
GATE = "external_smb_and_nfs_client_interoperability"
PLAN_KIND = "echo.protocol-interoperability-physical-lab-plan"
EVIDENCE_KIND = "echo.protocol-interoperability-physical-lab-evidence"
LIFECYCLE_KIND = "echo.protocol-interoperability-physical-lifecycle"
LIFECYCLE_NAME = "protocol-interoperability-lifecycle.json"
AUTHORIZATION_NAME = ".echo-protocol-interoperability-lab.json"
MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_EVIDENCE_BYTES = 2 * 1024 * 1024 * 1024
PROBE_BYTES = 8 * 1024 * 1024
LARGE_FILE_BYTES = 1024 * 1024 * 1024
QUOTA_PROBE_BYTES = 1024 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SHA1 = re.compile(r"^[0-9a-f]{40}$")
ARTIFACT_ID = re.compile(r"^[0-9a-f]{16}$")
SERVER_NAME = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?$")

ROLE_CHECKS = {
    "windows-smb": ("Windows", "smb", "windowsSmbReadWrite", "protocol-windows-smb.log"),
    "macos-smb": ("Darwin", "smb", "macosSmbReadWrite", "protocol-macos-smb.log"),
    "linux-smb": ("Linux", "smb", "linuxSmbReadWrite", "protocol-linux-smb.log"),
    "macos-nfs": ("Darwin", "nfs", "macosNfsReadWrite", "protocol-macos-nfs.log"),
    "linux-nfs": ("Linux", "nfs", "linuxNfsReadWrite", "protocol-linux-nfs.log"),
}
PHASE_CHECKS = {
    **{role: value[2] for role, value in ROLE_CHECKS.items()},
    "permissions": "userAndAclPermissionsVerified",
    "quota": "quotaEnforcedAcrossProtocols",
    "large-file": "largeFileVerified",
}
PHASE_OUTPUTS = {
    **{role: value[3] for role, value in ROLE_CHECKS.items()},
    "permissions": "protocol-permissions.log",
    "quota": "protocol-quota.log",
    "large-file": "protocol-large-file.log",
}
PHASES = tuple(PHASE_CHECKS)
EXPECTED_CHECKS = tuple(PHASE_CHECKS.values())


class ProtocolInteroperabilityLabError(RuntimeError):
    """The physical protocol interoperability lab cannot proceed safely."""


MountProbe = Callable[[Path, str, str, str], Mapping[str, Any]]
QuotaProbe = Callable[[Path, Path, int, int, str], Mapping[str, Any]]
CrossProtocolProbe = Callable[[Path, Path, int, str], Mapping[str, Any]]


def _canonical(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_regular(path: Path, label: str, *, maximum: int = MAX_JSON_BYTES) -> bytes:
    if path.is_symlink():
        raise ProtocolInteroperabilityLabError(f"{label} is empty, oversized or unsafe")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ProtocolInteroperabilityLabError(f"{label} is unavailable") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or not 1 <= before.st_size <= maximum:
            raise ProtocolInteroperabilityLabError(f"{label} is empty, oversized or unsafe")
        chunks: list[bytes] = []
        total = 0
        while total <= maximum:
            chunk = os.read(descriptor, min(1024 * 1024, maximum + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        after = os.fstat(descriptor)
        if total > maximum or (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise ProtocolInteroperabilityLabError(f"{label} changed while it was read")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _read_json(path: Path, label: str, *, maximum: int = MAX_JSON_BYTES) -> dict[str, Any]:
    raw = _read_regular(path, label, maximum=maximum)
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=systemd._reject_duplicate_keys)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ProtocolInteroperabilityLabError(f"{label} is not strict JSON") from exc
    if not isinstance(value, dict):
        raise ProtocolInteroperabilityLabError(f"{label} is not an object")
    return value


def _write_new(path: Path, value: Mapping[str, Any], *, mode: int = 0o444) -> None:
    if not path.is_absolute() or path.parent.is_symlink():
        raise ProtocolInteroperabilityLabError("lab output must use an absolute safe path")
    raw = _canonical(value)
    if len(raw) > MAX_JSON_BYTES:
        raise ProtocolInteroperabilityLabError("lab output exceeds its size bound")
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    try:
        written = 0
        while written < len(raw):
            count = os.write(descriptor, raw[written:])
            if count <= 0:
                raise ProtocolInteroperabilityLabError("lab output could not be written completely")
            written += count
        os.fsync(descriptor)
        if os.name != "nt":
            os.fchmod(descriptor, mode)
    finally:
        os.close(descriptor)


def _bundle_identity(
    bundle_root: Path, candidate: Mapping[str, str], *, trusted_uid: int
) -> dict[str, Any]:
    base = operations_lab._operations_bundle_identity(
        bundle_root,
        candidate,
        trusted_uid=trusted_uid,
    )
    manifest = _read_json(bundle_root / "bundle-manifest.json", "operations bundle manifest")
    artifact = manifest.get("artifact") if isinstance(manifest, dict) else None
    files = manifest.get("files") if isinstance(manifest, dict) else None
    record = files.get("protocol_interoperability_lab.py") if isinstance(files, dict) else None
    tool = bundle_root / "protocol_interoperability_lab.py"
    raw = _read_regular(tool, "candidate protocol interoperability lab tool")
    if (
        not isinstance(artifact, dict)
        or not isinstance(artifact.get("entrypoints"), dict)
        or artifact["entrypoints"].get("protocolInteroperabilityLab")
        != "./protocol_interoperability_lab.py plan|probe|permissions|quota|large-file|verify"
        or not isinstance(record, dict)
        or set(record) != {"sha256", "size", "mode"}
        or record.get("sha256") != _sha256(raw)
        or record.get("size") != len(raw)
        or record.get("mode") != "0755"
    ):
        raise ProtocolInteroperabilityLabError(
            "protocol interoperability lab tool is not from the release candidate"
        )
    return {**base, "protocolLabSha256": _sha256(raw), "protocolLabSize": len(raw)}


def _server_name(value: str) -> str:
    normalized = value.strip().rstrip(".").lower()
    if SERVER_NAME.fullmatch(normalized) is None or ".." in normalized:
        raise ProtocolInteroperabilityLabError("protocol lab server name is invalid")
    return normalized


def build_plan(
    *,
    candidate_index: Path,
    bundle_root: Path,
    server: str,
    lab_share_id: str,
    evidence_directory: Path,
    output: Path,
    trusted_uid: int | None = None,
) -> dict[str, Any]:
    uid = os.getuid() if trusted_uid is None else trusted_uid
    if os.name == "nt":
        raise ProtocolInteroperabilityLabError("protocol lab plans must be created on the NAS host")
    candidate = operations_lab._candidate_identity(candidate_index, trusted_uid=uid)
    root = bundle_root.resolve(strict=True)
    if not root.is_dir() or bundle_root.is_symlink():
        raise ProtocolInteroperabilityLabError("operations bundle root is unsafe")
    bundle = _bundle_identity(root, candidate, trusted_uid=uid)
    evidence_root = evidence_directory.resolve(strict=True)
    if (
        not evidence_directory.is_absolute()
        or evidence_directory.is_symlink()
        or not evidence_root.is_dir()
        or any(evidence_root.iterdir())
    ):
        raise ProtocolInteroperabilityLabError(
            "protocol lab evidence directory must be one empty absolute directory"
        )
    try:
        share_id = str(uuid.UUID(lab_share_id, version=4))
    except ValueError as exc:
        raise ProtocolInteroperabilityLabError("lab share ID must be one canonical UUIDv4") from exc
    if share_id != lab_share_id:
        raise ProtocolInteroperabilityLabError("lab share ID must be one canonical UUIDv4")
    payload: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": PLAN_KIND,
        "gate": GATE,
        "releaseCandidate": candidate,
        "operationsBundle": bundle,
        "server": _server_name(server),
        "authorization": {
            "schemaVersion": 1,
            "kind": "echo.protocol-interoperability-lab-authorization",
            "candidateIndexId": candidate["indexId"],
            "labShareId": share_id,
            "dedicatedLabShare": True,
            "markerName": AUTHORIZATION_NAME,
        },
        "evidenceDirectory": str(evidence_root),
        "phases": list(PHASES),
        "outputs": dict(PHASE_OUTPUTS),
        "sizes": {
            "clientProbeBytes": PROBE_BYTES,
            "quotaProbeMaximumBytes": QUOTA_PROBE_BYTES,
            "largeFileBytes": LARGE_FILE_BYTES,
            "chunkBytes": CHUNK_BYTES,
        },
    }
    payload["planId"] = _sha256(_canonical(payload))
    payload["confirmations"] = {
        phase: f"RUN ECHO PROTOCOL LAB {phase} {payload['planId']}" for phase in PHASES
    }
    if output.name != "protocol-interoperability-lab-plan.json":
        raise ProtocolInteroperabilityLabError(
            "protocol lab plan must use protocol-interoperability-lab-plan.json"
        )
    _write_new(output, payload, mode=0o400)
    return payload


def _validate_plan(value: Mapping[str, Any]) -> dict[str, Any]:
    expected_keys = {
        "schemaVersion",
        "kind",
        "gate",
        "releaseCandidate",
        "operationsBundle",
        "server",
        "authorization",
        "evidenceDirectory",
        "phases",
        "outputs",
        "sizes",
        "planId",
        "confirmations",
    }
    candidate = value.get("releaseCandidate")
    bundle = value.get("operationsBundle")
    authorization = value.get("authorization")
    sizes = value.get("sizes")
    plan_id = value.get("planId")
    confirmations = value.get("confirmations")
    try:
        share_id = str(uuid.UUID(str(authorization.get("labShareId")), version=4))
    except (ValueError, AttributeError):
        share_id = ""
    if (
        set(value) != expected_keys
        or value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != PLAN_KIND
        or value.get("gate") != GATE
        or not isinstance(candidate, dict)
        or set(candidate)
        != {
            "indexPath",
            "indexId",
            "indexSha256",
            "osRepository",
            "sourceRevision",
            "agentRepository",
            "agentRevision",
            "releaseTag",
            "applianceManifestSha256",
            "immutableReference",
            "operationsArtifactId",
            "operationsArchiveSha256",
        }
        or any(
            not isinstance(candidate.get(name), str) or not candidate[name] for name in candidate
        )
        or not Path(candidate["indexPath"]).is_absolute()
        or SHA256.fullmatch(str(candidate.get("indexId"))) is None
        or SHA256.fullmatch(str(candidate.get("indexSha256"))) is None
        or SHA1.fullmatch(str(candidate.get("sourceRevision"))) is None
        or SHA1.fullmatch(str(candidate.get("agentRevision"))) is None
        or SHA256.fullmatch(str(candidate.get("applianceManifestSha256"))) is None
        or ARTIFACT_ID.fullmatch(str(candidate.get("operationsArtifactId"))) is None
        or SHA256.fullmatch(str(candidate.get("operationsArchiveSha256"))) is None
        or not isinstance(bundle, dict)
        or set(bundle)
        != {
            "artifactId",
            "archiveSha256",
            "imageReference",
            "manifestSha256",
            "labToolSha256",
            "labToolSize",
            "protocolLabSha256",
            "protocolLabSize",
        }
        or any(
            not isinstance(bundle.get(name), str) or SHA256.fullmatch(bundle[name]) is None
            for name in ("archiveSha256", "manifestSha256", "labToolSha256", "protocolLabSha256")
        )
        or not isinstance(bundle.get("labToolSize"), int)
        or isinstance(bundle.get("labToolSize"), bool)
        or not isinstance(bundle.get("protocolLabSize"), int)
        or isinstance(bundle.get("protocolLabSize"), bool)
        or bundle.get("labToolSize", 0) <= 0
        or bundle.get("protocolLabSize", 0) <= 0
        or bundle.get("artifactId") != candidate.get("operationsArtifactId")
        or bundle.get("archiveSha256") != candidate.get("operationsArchiveSha256")
        or bundle.get("imageReference") != candidate.get("immutableReference")
        or value.get("server") != _server_name(str(value.get("server", "")))
        or not isinstance(authorization, dict)
        or not share_id
        or authorization
        != {
            "schemaVersion": 1,
            "kind": "echo.protocol-interoperability-lab-authorization",
            "candidateIndexId": candidate.get("indexId"),
            "labShareId": share_id,
            "dedicatedLabShare": True,
            "markerName": AUTHORIZATION_NAME,
        }
        or not isinstance(value.get("evidenceDirectory"), str)
        or not Path(value["evidenceDirectory"]).is_absolute()
        or value.get("phases") != list(PHASES)
        or value.get("outputs") != PHASE_OUTPUTS
        or sizes
        != {
            "clientProbeBytes": PROBE_BYTES,
            "quotaProbeMaximumBytes": QUOTA_PROBE_BYTES,
            "largeFileBytes": LARGE_FILE_BYTES,
            "chunkBytes": CHUNK_BYTES,
        }
        or not isinstance(plan_id, str)
        or SHA256.fullmatch(plan_id) is None
        or confirmations != {phase: f"RUN ECHO PROTOCOL LAB {phase} {plan_id}" for phase in PHASES}
    ):
        raise ProtocolInteroperabilityLabError("protocol lab plan is invalid")
    unsigned = dict(value)
    unsigned.pop("confirmations")
    unsigned.pop("planId")
    if plan_id != _sha256(_canonical(unsigned)):
        raise ProtocolInteroperabilityLabError("protocol lab plan ID is invalid")
    return dict(value)


def _load_plan(path: Path) -> dict[str, Any]:
    if os.name != "nt" and stat.S_IMODE(path.stat().st_mode) & 0o222:
        raise ProtocolInteroperabilityLabError("protocol lab plan must be read-only")
    return _validate_plan(_read_json(path, "protocol interoperability lab plan"))


def _confirmation(plan: Mapping[str, Any], phase: str, supplied: str) -> None:
    if phase not in PHASES or supplied != plan["confirmations"][phase]:
        raise ProtocolInteroperabilityLabError("protocol lab confirmation is invalid")


def _authorization(root: Path, plan: Mapping[str, Any]) -> dict[str, Any]:
    resolved = root.resolve(strict=True)
    if root.is_symlink() or not resolved.is_dir():
        raise ProtocolInteroperabilityLabError("protocol lab mount root is unsafe")
    marker = _read_json(resolved / AUTHORIZATION_NAME, "protocol lab authorization marker")
    try:
        entries = {entry.name for entry in resolved.iterdir()}
    except OSError as exc:
        raise ProtocolInteroperabilityLabError(
            "protocol lab mount could not be enumerated safely"
        ) from exc
    if marker != plan["authorization"] or entries != {AUTHORIZATION_NAME}:
        raise ProtocolInteroperabilityLabError(
            "protocol lab mount is not authorized for this candidate or is not an empty share"
        )
    return marker


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _unc_identity(value: str) -> tuple[str, str] | None:
    normalized = value.replace("/", "\\")
    if not normalized.startswith("\\\\"):
        return None
    parts = [part for part in normalized.lstrip("\\").split("\\") if part]
    if len(parts) < 2:
        return None
    return parts[0].rstrip(".").casefold(), parts[1].casefold()


def _remote_source_server(source: str, protocol: str) -> str | None:
    text = source.strip()
    if protocol == "smb":
        if not text.startswith("//"):
            return None
        authority = text[2:].split("/", 1)[0]
        return authority.rsplit("@", 1)[-1].rstrip(".").casefold() or None
    host, separator, _export = text.partition(":")
    return host.rstrip(".").casefold() if separator and host else None


def _native_mount_probe(root: Path, protocol: str, server: str, system_name: str) -> dict[str, Any]:
    if system_name == "Windows":
        if protocol != "smb":
            raise ProtocolInteroperabilityLabError("Windows is only accepted for the SMB gate")
        command = [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "$ErrorActionPreference='Stop'; "
            "[pscustomobject]@{Connections=@(Get-SmbConnection | "
            "Select-Object ServerName,ShareName,Dialect,Signed,Encrypted); "
            "Mappings=@(Get-SmbMapping | Select-Object LocalPath,RemotePath,Status)} | "
            "ConvertTo-Json -Compress -Depth 5",
        ]
        completed = _run(command)
        if completed.returncode != 0 or len(completed.stdout) > MAX_JSON_BYTES:
            raise ProtocolInteroperabilityLabError("Windows SMB connection evidence is unavailable")
        try:
            value = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise ProtocolInteroperabilityLabError(
                "Windows SMB connection evidence is invalid"
            ) from exc
        if not isinstance(value, dict):
            raise ProtocolInteroperabilityLabError("Windows SMB connection evidence is invalid")
        records = value.get("Connections")
        mappings = value.get("Mappings")
        if not isinstance(records, list) or not isinstance(mappings, list):
            raise ProtocolInteroperabilityLabError("Windows SMB connection evidence is invalid")
        identity = _unc_identity(str(root))
        if identity is None:
            drive = str(root.drive).rstrip("\\").casefold()
            if not drive:
                raise ProtocolInteroperabilityLabError(
                    "Windows SMB path differs from the planned server"
                )
            matching_mappings = [
                record
                for record in mappings
                if isinstance(record, dict)
                and str(record.get("LocalPath", "")).rstrip("\\").casefold() == drive
                and str(record.get("Status", "")).casefold() == "ok"
            ]
            if len(matching_mappings) != 1:
                raise ProtocolInteroperabilityLabError(
                    "Windows mount is not one healthy SMB mapping"
                )
            identity = _unc_identity(str(matching_mappings[0].get("RemotePath", "")))
        if identity is None or identity[0] != server.casefold():
            raise ProtocolInteroperabilityLabError(
                "Windows SMB path differs from the planned server"
            )
        path_server, path_share = identity
        matched = [
            record
            for record in records
            if isinstance(record, dict)
            and str(record.get("ServerName", "")).rstrip(".").casefold() == path_server
            and str(record.get("ShareName", "")).casefold() == path_share
            and str(record.get("Dialect", "")).startswith(("2", "3"))
        ]
        if not matched:
            raise ProtocolInteroperabilityLabError("Windows has no matching SMB2/SMB3 connection")
        raw = completed.stdout.encode()
        fstype = "smb"
    else:
        command = (
            [
                "/usr/bin/findmnt",
                "--json",
                "--target",
                str(root),
                "--output",
                "SOURCE,FSTYPE,OPTIONS",
            ]
            if system_name == "Linux"
            else ["/sbin/mount"]
        )
        completed = _run(command)
        if completed.returncode != 0 or len(completed.stdout) > MAX_JSON_BYTES:
            raise ProtocolInteroperabilityLabError("native mount evidence is unavailable")
        raw = completed.stdout.encode()
        if system_name == "Linux":
            try:
                value = json.loads(completed.stdout)
                filesystems = value["filesystems"]
            except (KeyError, TypeError, json.JSONDecodeError) as exc:
                raise ProtocolInteroperabilityLabError("Linux mount evidence is invalid") from exc
            if not isinstance(filesystems, list) or len(filesystems) != 1:
                raise ProtocolInteroperabilityLabError("Linux mount identity is ambiguous")
            source = str(filesystems[0].get("source", ""))
            fstype = str(filesystems[0].get("fstype", "")).casefold()
        else:
            target = f" on {root} ("
            lines = [line for line in completed.stdout.splitlines() if target in line]
            if len(lines) != 1:
                raise ProtocolInteroperabilityLabError("macOS mount identity is ambiguous")
            source, suffix = lines[0].split(target, 1)
            fstype = suffix.split(",", 1)[0].rstrip(")").casefold()
        expected_types = {"smb": {"cifs", "smb", "smbfs"}, "nfs": {"nfs", "nfs4"}}
        if (
            fstype not in expected_types[protocol]
            or _remote_source_server(source, protocol) != server.casefold()
        ):
            raise ProtocolInteroperabilityLabError(
                "mount protocol or server does not match the plan"
            )
    return {
        "mounted": True,
        "protocol": protocol,
        "filesystemType": fstype,
        "serverMatched": True,
        "nativeEvidenceSha256": _sha256(raw),
    }


def _mount(
    root: Path,
    protocol: str,
    plan: Mapping[str, Any],
    system_name: str,
    probe: MountProbe,
) -> dict[str, Any]:
    value = dict(probe(root, protocol, plan["server"], system_name))
    if (
        set(value)
        != {"mounted", "protocol", "filesystemType", "serverMatched", "nativeEvidenceSha256"}
        or value["mounted"] is not True
        or value["protocol"] != protocol
        or not isinstance(value["filesystemType"], str)
        or not value["filesystemType"]
        or value["serverMatched"] is not True
        or not isinstance(value["nativeEvidenceSha256"], str)
        or SHA256.fullmatch(value["nativeEvidenceSha256"]) is None
    ):
        raise ProtocolInteroperabilityLabError("protocol mount evidence contract is invalid")
    return value


def _payload_chunk(seed: str, index: int, size: int) -> bytes:
    digest = hashlib.sha256(f"{seed}:{index}".encode()).digest()
    return (digest * ((size + len(digest) - 1) // len(digest)))[:size]


def _write_payload(path: Path, size: int, seed: str) -> str:
    digest = hashlib.sha256()
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    try:
        written = 0
        index = 0
        while written < size:
            chunk = _payload_chunk(seed, index, min(CHUNK_BYTES, size - written))
            offset = 0
            while offset < len(chunk):
                count = os.write(descriptor, chunk[offset:])
                if count <= 0:
                    raise ProtocolInteroperabilityLabError("protocol payload write stalled")
                offset += count
            digest.update(chunk)
            written += len(chunk)
            index += 1
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def _digest_file(path: Path, expected_size: int) -> str:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    if size != expected_size:
        raise ProtocolInteroperabilityLabError("protocol payload size changed across the mount")
    return digest.hexdigest()


def _phase_payload(
    plan: Mapping[str, Any], phase: str, details: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": EVIDENCE_KIND,
        "gate": GATE,
        "planId": plan["planId"],
        "phase": phase,
        "check": PHASE_CHECKS[phase],
        "passed": True,
        "details": dict(details),
    }


def _client_identity(system_name: str) -> dict[str, str]:
    host = platform.node().strip().casefold()
    return {
        "operatingSystem": system_name,
        "architecture": platform.machine() or "unknown",
        "hostIdentitySha256": _sha256(host.encode()),
    }


def run_probe(
    *,
    plan_path: Path,
    role: str,
    mount_root: Path,
    confirmation: str,
    output: Path,
    system_name: str | None = None,
    mount_probe: MountProbe = _native_mount_probe,
) -> dict[str, Any]:
    if role not in ROLE_CHECKS:
        raise ProtocolInteroperabilityLabError("protocol client role is invalid")
    plan = _load_plan(plan_path)
    _confirmation(plan, role, confirmation)
    expected_system, protocol, _check, expected_output = ROLE_CHECKS[role]
    actual_system = platform.system() if system_name is None else system_name
    if actual_system != expected_system:
        raise ProtocolInteroperabilityLabError("protocol role is running on the wrong client OS")
    if output.name != expected_output:
        raise ProtocolInteroperabilityLabError("protocol role output filename is invalid")
    root = mount_root.resolve(strict=True)
    _authorization(root, plan)
    mount = _mount(root, protocol, plan, actual_system, mount_probe)
    directory = root / f".echo-protocol-{plan['planId'][:16]}-{role}"
    source = directory / "payload.bin"
    renamed = directory / "payload-renamed.bin"
    try:
        directory.mkdir(mode=0o700)
        digest = _write_payload(source, PROBE_BYTES, f"{plan['planId']}:{role}")
        if _digest_file(source, PROBE_BYTES) != digest:
            raise ProtocolInteroperabilityLabError("protocol client readback digest differs")
        source.rename(renamed)
        if source.exists() or _digest_file(renamed, PROBE_BYTES) != digest:
            raise ProtocolInteroperabilityLabError("protocol rename did not preserve the payload")
        renamed.unlink()
        if renamed.exists():
            raise ProtocolInteroperabilityLabError("protocol client delete did not complete")
    finally:
        for path in (source, renamed):
            with suppress(FileNotFoundError):
                path.unlink()
        with suppress(FileNotFoundError):
            directory.rmdir()
    details = {
        "client": _client_identity(actual_system),
        "mount": mount,
        "bytes": PROBE_BYTES,
        "sha256": digest,
        "writeVerified": True,
        "readVerified": True,
        "renameVerified": True,
        "deleteVerified": True,
    }
    payload = _phase_payload(plan, role, details)
    _write_new(output, payload)
    return payload


def _small_allowed_probe(root: Path, seed: str) -> None:
    name = root / f".echo-protocol-permission-{seed[:16]}"
    try:
        digest = _write_payload(name, CHUNK_BYTES, seed)
        if _digest_file(name, CHUNK_BYTES) != digest:
            raise ProtocolInteroperabilityLabError("allowed protocol permission probe changed")
    finally:
        with suppress(FileNotFoundError):
            name.unlink()


def _denied_probe(root: Path, seed: str) -> str:
    name = root / f".echo-protocol-denied-{seed[:16]}"
    try:
        _write_payload(name, CHUNK_BYTES, seed)
    except OSError as exc:
        if exc.errno not in {errno.EACCES, errno.EPERM, errno.EROFS}:
            raise ProtocolInteroperabilityLabError(
                "denied mount failed for an unexpected reason"
            ) from exc
        return errno.errorcode.get(exc.errno, str(exc.errno))
    finally:
        with suppress(FileNotFoundError):
            name.unlink()
    raise ProtocolInteroperabilityLabError("denied protocol identity unexpectedly wrote data")


def run_permissions(
    *,
    plan_path: Path,
    smb_allowed: Path,
    nfs_allowed: Path,
    smb_denied: Path,
    nfs_denied: Path,
    confirmation: str,
    output: Path,
    mount_probe: MountProbe = _native_mount_probe,
    system_name: str | None = None,
) -> dict[str, Any]:
    plan = _load_plan(plan_path)
    phase = "permissions"
    _confirmation(plan, phase, confirmation)
    actual_system = platform.system() if system_name is None else system_name
    if actual_system != "Linux":
        raise ProtocolInteroperabilityLabError("permission policy phase requires the Linux client")
    roots = {
        "smbAllowed": (smb_allowed.resolve(strict=True), "smb"),
        "nfsAllowed": (nfs_allowed.resolve(strict=True), "nfs"),
        "smbDenied": (smb_denied.resolve(strict=True), "smb"),
        "nfsDenied": (nfs_denied.resolve(strict=True), "nfs"),
    }
    mounts = {}
    for label, (root, protocol) in roots.items():
        _authorization(root, plan)
        mounts[label] = _mount(root, protocol, plan, actual_system, mount_probe)
    _small_allowed_probe(roots["smbAllowed"][0], f"{plan['planId']}:smb-allowed")
    _small_allowed_probe(roots["nfsAllowed"][0], f"{plan['planId']}:nfs-allowed")
    denied = {
        "smb": _denied_probe(roots["smbDenied"][0], f"{plan['planId']}:smb-denied"),
        "nfs": _denied_probe(roots["nfsDenied"][0], f"{plan['planId']}:nfs-denied"),
    }
    payload = _phase_payload(
        plan,
        phase,
        {
            "client": _client_identity(actual_system),
            "mounts": mounts,
            "allowedSmbWrite": True,
            "allowedNfsWrite": True,
            "deniedSmbWrite": True,
            "deniedNfsWrite": True,
            "denialErrors": denied,
        },
    )
    if output.name != PHASE_OUTPUTS[phase]:
        raise ProtocolInteroperabilityLabError("permission phase output filename is invalid")
    _write_new(output, payload)
    return payload


def _write_until_quota(path: Path, maximum: int, chunk_size: int, seed: str) -> tuple[int, str]:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    written = 0
    try:
        while written < maximum:
            chunk = _payload_chunk(seed, written // chunk_size, min(chunk_size, maximum - written))
            try:
                offset = 0
                while offset < len(chunk):
                    count = os.write(descriptor, chunk[offset:])
                    if count <= 0:
                        raise ProtocolInteroperabilityLabError("quota probe write stalled")
                    offset += count
                    written += count
            except OSError as exc:
                if exc.errno not in {errno.EDQUOT, errno.ENOSPC}:
                    raise ProtocolInteroperabilityLabError(
                        "quota probe failed for an unexpected reason"
                    ) from exc
                return written, errno.errorcode.get(exc.errno, str(exc.errno))
        raise ProtocolInteroperabilityLabError("quota was not enforced within the bounded probe")
    finally:
        os.close(descriptor)


def _quota_probe(smb: Path, nfs: Path, maximum: int, chunk: int, seed: str) -> dict[str, Any]:
    relative = f".echo-protocol-quota-{seed[:16]}"
    smb_path = smb / relative
    nfs_path = nfs / relative
    nfs_second = nfs / f"{relative}-second"
    try:
        written, first_error = _write_until_quota(smb_path, maximum, chunk, seed)
        if not nfs_path.exists() or nfs_path.stat().st_size != written:
            raise ProtocolInteroperabilityLabError("quota payload is not coherent across protocols")
        second_written, second_error = _write_until_quota(
            nfs_second,
            min(8 * chunk, maximum),
            chunk,
            f"{seed}:nfs",
        )
        if second_written != 0:
            raise ProtocolInteroperabilityLabError(
                "NFS accepted new quota data after SMB exhausted the same account"
            )
        return {
            "smbQuotaRejected": True,
            "nfsQuotaRejected": True,
            "crossProtocolVisibility": True,
            "allocatedBytes": written,
            "smbError": first_error,
            "nfsError": second_error,
        }
    finally:
        for path in (nfs_second, nfs_path, smb_path):
            with suppress(FileNotFoundError):
                path.unlink()


def run_quota(
    *,
    plan_path: Path,
    smb_root: Path,
    nfs_root: Path,
    confirmation: str,
    output: Path,
    mount_probe: MountProbe = _native_mount_probe,
    quota_probe: QuotaProbe = _quota_probe,
    system_name: str | None = None,
) -> dict[str, Any]:
    plan = _load_plan(plan_path)
    phase = "quota"
    _confirmation(plan, phase, confirmation)
    actual_system = platform.system() if system_name is None else system_name
    if actual_system != "Linux":
        raise ProtocolInteroperabilityLabError("quota phase requires the Linux client")
    smb = smb_root.resolve(strict=True)
    nfs = nfs_root.resolve(strict=True)
    for root, protocol in ((smb, "smb"), (nfs, "nfs")):
        _authorization(root, plan)
        _mount(root, protocol, plan, actual_system, mount_probe)
    details = dict(
        quota_probe(
            smb,
            nfs,
            plan["sizes"]["quotaProbeMaximumBytes"],
            plan["sizes"]["chunkBytes"],
            plan["planId"],
        )
    )
    if (
        set(details)
        != {
            "smbQuotaRejected",
            "nfsQuotaRejected",
            "crossProtocolVisibility",
            "allocatedBytes",
            "smbError",
            "nfsError",
        }
        or details["smbQuotaRejected"] is not True
        or details["nfsQuotaRejected"] is not True
        or details["crossProtocolVisibility"] is not True
        or not isinstance(details["allocatedBytes"], int)
        or isinstance(details["allocatedBytes"], bool)
        or details["allocatedBytes"] <= 0
        or details["allocatedBytes"] > plan["sizes"]["quotaProbeMaximumBytes"]
        or details["smbError"] not in {"EDQUOT", "ENOSPC"}
        or details["nfsError"] not in {"EDQUOT", "ENOSPC"}
    ):
        raise ProtocolInteroperabilityLabError(
            "quota probe did not prove cross-protocol enforcement"
        )
    payload = _phase_payload(plan, phase, {"client": _client_identity(actual_system), **details})
    if output.name != PHASE_OUTPUTS[phase]:
        raise ProtocolInteroperabilityLabError("quota phase output filename is invalid")
    _write_new(output, payload)
    return payload


def _cross_protocol_probe(smb: Path, nfs: Path, size: int, seed: str) -> dict[str, Any]:
    relative = f".echo-protocol-large-{seed[:16]}.bin"
    smb_path = smb / relative
    nfs_path = nfs / relative
    try:
        digest = _write_payload(smb_path, size, seed)
        if not nfs_path.exists() or _digest_file(nfs_path, size) != digest:
            raise ProtocolInteroperabilityLabError("large file differs across SMB and NFS")
        nfs_path.unlink()
        if smb_path.exists():
            raise ProtocolInteroperabilityLabError(
                "cross-protocol large file delete was not visible"
            )
        return {
            "bytes": size,
            "sha256": digest,
            "writtenViaSmb": True,
            "readViaNfs": True,
            "deletedViaNfs": True,
            "deleteObservedViaSmb": True,
        }
    finally:
        for path in (nfs_path, smb_path):
            with suppress(FileNotFoundError):
                path.unlink()


def run_large_file(
    *,
    plan_path: Path,
    smb_root: Path,
    nfs_root: Path,
    confirmation: str,
    output: Path,
    mount_probe: MountProbe = _native_mount_probe,
    cross_protocol_probe: CrossProtocolProbe = _cross_protocol_probe,
    system_name: str | None = None,
) -> dict[str, Any]:
    plan = _load_plan(plan_path)
    phase = "large-file"
    _confirmation(plan, phase, confirmation)
    actual_system = platform.system() if system_name is None else system_name
    if actual_system != "Linux":
        raise ProtocolInteroperabilityLabError("large-file phase requires the Linux client")
    smb = smb_root.resolve(strict=True)
    nfs = nfs_root.resolve(strict=True)
    for root, protocol in ((smb, "smb"), (nfs, "nfs")):
        _authorization(root, plan)
        _mount(root, protocol, plan, actual_system, mount_probe)
    details = dict(
        cross_protocol_probe(
            smb,
            nfs,
            plan["sizes"]["largeFileBytes"],
            plan["planId"],
        )
    )
    if (
        set(details)
        != {
            "bytes",
            "sha256",
            "writtenViaSmb",
            "readViaNfs",
            "deletedViaNfs",
            "deleteObservedViaSmb",
        }
        or details["bytes"] != LARGE_FILE_BYTES
        or not isinstance(details["sha256"], str)
        or SHA256.fullmatch(details["sha256"]) is None
        or any(details[name] is not True for name in details if name.endswith(("Smb", "Nfs")))
    ):
        raise ProtocolInteroperabilityLabError("large-file probe did not prove cross-protocol I/O")
    payload = _phase_payload(plan, phase, {"client": _client_identity(actual_system), **details})
    if output.name != PHASE_OUTPUTS[phase]:
        raise ProtocolInteroperabilityLabError("large-file phase output filename is invalid")
    _write_new(output, payload)
    return payload


def _evidence_record(path: Path, phase: str, plan: Mapping[str, Any]) -> dict[str, Any]:
    if os.name != "nt":
        try:
            info = path.lstat()
        except OSError as exc:
            raise ProtocolInteroperabilityLabError(
                f"protocol evidence for {phase} is unavailable"
            ) from exc
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o444:
            raise ProtocolInteroperabilityLabError(
                "protocol phase evidence must be a regular mode 0444 file"
            )
    raw = _read_regular(path, f"protocol evidence for {phase}", maximum=MAX_EVIDENCE_BYTES)
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=systemd._reject_duplicate_keys)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ProtocolInteroperabilityLabError(
            "protocol phase evidence is not strict JSON"
        ) from exc
    if (
        not isinstance(value, dict)
        or set(value)
        != {"schemaVersion", "kind", "gate", "planId", "phase", "check", "passed", "details"}
        or value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != EVIDENCE_KIND
        or value.get("gate") != GATE
        or value.get("planId") != plan["planId"]
        or value.get("phase") != phase
        or value.get("check") != PHASE_CHECKS[phase]
        or value.get("passed") is not True
        or not isinstance(value.get("details"), dict)
    ):
        raise ProtocolInteroperabilityLabError("protocol phase evidence contract is invalid")
    return {"name": path.name, "sha256": _sha256(raw), "size": len(raw)}


def verify_evidence(
    *,
    plan_path: Path,
    candidate_index: Path,
    bundle_root: Path,
    evidence_directory: Path,
    output: Path,
    trusted_uid: int | None = None,
) -> dict[str, Any]:
    plan = _load_plan(plan_path)
    uid = os.getuid() if trusted_uid is None else trusted_uid
    candidate = operations_lab._candidate_identity(candidate_index, trusted_uid=uid)
    root_bundle = bundle_root.resolve(strict=True)
    bundle = _bundle_identity(root_bundle, candidate, trusted_uid=uid)
    if plan["releaseCandidate"] != candidate or plan["operationsBundle"] != bundle:
        raise ProtocolInteroperabilityLabError(
            "protocol lab plan drifted from its release candidate or operations bundle"
        )
    root = evidence_directory.resolve(strict=True)
    if str(root) != plan["evidenceDirectory"] or evidence_directory.is_symlink():
        raise ProtocolInteroperabilityLabError("protocol evidence directory differs from the plan")
    if output.parent.resolve(strict=True) != root or output.name != LIFECYCLE_NAME:
        raise ProtocolInteroperabilityLabError("protocol lifecycle output path is invalid")
    records = {
        PHASE_CHECKS[phase]: _evidence_record(root / PHASE_OUTPUTS[phase], phase, plan)
        for phase in PHASES
    }
    candidate = plan["releaseCandidate"]
    payload = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": LIFECYCLE_KIND,
        "gate": GATE,
        "candidate": {
            "indexId": candidate["indexId"],
            "sourceRevision": candidate["sourceRevision"],
            "agentRevision": candidate["agentRevision"],
            "releaseTag": candidate["releaseTag"],
            "operationsArtifactId": candidate["operationsArtifactId"],
            "operationsArchiveSha256": candidate["operationsArchiveSha256"],
        },
        "labPlanId": plan["planId"],
        "checks": {
            check: {"passed": True, "evidence": records[check]} for check in EXPECTED_CHECKS
        },
        "allPassed": True,
    }
    _write_new(output, payload)
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan")
    plan.add_argument("--candidate-index", type=Path, required=True)
    plan.add_argument("--bundle-root", type=Path, required=True)
    plan.add_argument("--server", required=True)
    plan.add_argument("--lab-share-id", required=True)
    plan.add_argument("--evidence-directory", type=Path, required=True)
    plan.add_argument("--output", type=Path, required=True)
    probe = commands.add_parser("probe")
    probe.add_argument("--plan", type=Path, required=True)
    probe.add_argument("--role", choices=tuple(ROLE_CHECKS), required=True)
    probe.add_argument("--mount", type=Path, required=True)
    probe.add_argument("--confirm", required=True)
    probe.add_argument("--output", type=Path, required=True)
    permissions = commands.add_parser("permissions")
    permissions.add_argument("--plan", type=Path, required=True)
    permissions.add_argument("--smb-allowed", type=Path, required=True)
    permissions.add_argument("--nfs-allowed", type=Path, required=True)
    permissions.add_argument("--smb-denied", type=Path, required=True)
    permissions.add_argument("--nfs-denied", type=Path, required=True)
    permissions.add_argument("--confirm", required=True)
    permissions.add_argument("--output", type=Path, required=True)
    quota = commands.add_parser("quota")
    quota.add_argument("--plan", type=Path, required=True)
    quota.add_argument("--smb", type=Path, required=True)
    quota.add_argument("--nfs", type=Path, required=True)
    quota.add_argument("--confirm", required=True)
    quota.add_argument("--output", type=Path, required=True)
    large = commands.add_parser("large-file")
    large.add_argument("--plan", type=Path, required=True)
    large.add_argument("--smb", type=Path, required=True)
    large.add_argument("--nfs", type=Path, required=True)
    large.add_argument("--confirm", required=True)
    large.add_argument("--output", type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--plan", type=Path, required=True)
    verify.add_argument("--candidate-index", type=Path, required=True)
    verify.add_argument("--bundle-root", type=Path, required=True)
    verify.add_argument("--evidence-directory", type=Path, required=True)
    verify.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "plan":
            result = build_plan(
                candidate_index=args.candidate_index,
                bundle_root=args.bundle_root,
                server=args.server,
                lab_share_id=args.lab_share_id,
                evidence_directory=args.evidence_directory,
                output=args.output,
            )
            print(
                "ECHO_PROTOCOL_INTEROPERABILITY_LAB_PLAN_READY "
                f"candidate={result['releaseCandidate']['indexId']} plan={result['planId']}"
            )
            for phase in PHASES:
                print(f"{phase}: {result['confirmations'][phase]}")
            return 0
        if args.command == "probe":
            result = run_probe(
                plan_path=args.plan,
                role=args.role,
                mount_root=args.mount,
                confirmation=args.confirm,
                output=args.output,
            )
        elif args.command == "permissions":
            result = run_permissions(
                plan_path=args.plan,
                smb_allowed=args.smb_allowed,
                nfs_allowed=args.nfs_allowed,
                smb_denied=args.smb_denied,
                nfs_denied=args.nfs_denied,
                confirmation=args.confirm,
                output=args.output,
            )
        elif args.command == "quota":
            result = run_quota(
                plan_path=args.plan,
                smb_root=args.smb,
                nfs_root=args.nfs,
                confirmation=args.confirm,
                output=args.output,
            )
        elif args.command == "large-file":
            result = run_large_file(
                plan_path=args.plan,
                smb_root=args.smb,
                nfs_root=args.nfs,
                confirmation=args.confirm,
                output=args.output,
            )
        else:
            result = verify_evidence(
                plan_path=args.plan,
                candidate_index=args.candidate_index,
                bundle_root=args.bundle_root,
                evidence_directory=args.evidence_directory,
                output=args.output,
            )
            print(
                "ECHO_PROTOCOL_INTEROPERABILITY_LIFECYCLE_READY "
                f"plan={result['labPlanId']} checks={len(result['checks'])}"
            )
            return 0
    except (
        OSError,
        KeyError,
        TypeError,
        ValueError,
        subprocess.SubprocessError,
        ProtocolInteroperabilityLabError,
        operations_lab.OperationsSystemdLabError,
        systemd.OperationsSystemdError,
    ) as exc:
        print(f"Echo protocol interoperability physical lab failed: {exc}", file=sys.stderr)
        return 1
    print(
        "ECHO_PROTOCOL_INTEROPERABILITY_LAB_PHASE_OK "
        f"phase={result['phase']} plan={result['planId']} output={PHASE_OUTPUTS[result['phase']]}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
