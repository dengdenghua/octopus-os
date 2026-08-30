#!/usr/bin/env python3
"""Build, verify and safely extract an Echo appliance operations bundle."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import stat
import sys
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA_VERSION = 1
ARCHITECTURES = ("amd64", "arm64")
MAX_SOURCE_BYTES = 16 * 1024 * 1024
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 48
ARCHIVE_NAME = "echo-appliance-operations.tar.gz"
CHECKSUM_NAME = f"{ARCHIVE_NAME}.sha256"
SBOM_NAME = "echo-appliance-operations.spdx.json"
MANIFEST_NAME = "bundle-manifest.json"
PAYLOAD_CHECKSUMS_NAME = "SHA256SUMS"
IMAGE_REFERENCE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64}$")
ARTIFACT_ID_PATTERN = re.compile(r"^[0-9a-f]{16}$")
ROOT_PATTERN = re.compile(r"^echo-appliance-operations-([0-9a-f]{16})$")

# Destination path -> (repository source path, installed mode). The generated
# echo-release.env is added separately and is bound to the immutable image.
SOURCE_FILES: dict[str, tuple[str, int]] = {
    "README.md": ("deploy/appliance/OPERATIONS_BUNDLE_README.md", 0o644),
    "appliance.env.example": ("deploy/appliance/appliance.env.example", 0o644),
    "backup-state.sh": ("deploy/appliance/backup-state.sh", 0o755),
    "bare_metal_recovery_lab.py": (
        "deploy/appliance/bare_metal_recovery_lab.py",
        0o755,
    ),
    "device_endurance_lab.py": (
        "deploy/appliance/device_endurance_lab.py",
        0o755,
    ),
    "docker-compose.tls.yml": ("deploy/appliance/docker-compose.tls.yml", 0o644),
    "docker-compose.yml": ("deploy/appliance/docker-compose.yml", 0o644),
    "export-audit-evidence.sh": (
        "deploy/appliance/export-audit-evidence.sh",
        0o755,
    ),
    "external_storage.py": ("deploy/appliance/external_storage.py", 0o755),
    "hub_lifecycle_lab.py": (
        "deploy/appliance/hub_lifecycle_lab.py",
        0o755,
    ),
    "lan_discovery_functional_lab.py": (
        "deploy/appliance/lan_discovery_functional_lab.py",
        0o755,
    ),
    "paperless_functional_lab.py": (
        "deploy/appliance/paperless_functional_lab.py",
        0o755,
    ),
    "install-appliance.sh": ("deploy/appliance/install-appliance.sh", 0o755),
    "nas_data_backup.py": (
        "deploy/appliance/nas_data_backup.py",
        0o755,
    ),
    "operations_bundle.py": ("deploy/appliance/operations_bundle.py", 0o755),
    "operations_systemd.py": ("deploy/appliance/operations_systemd.py", 0o755),
    "operations_systemd_lab.py": (
        "deploy/appliance/operations_systemd_lab.py",
        0o755,
    ),
    "power_state_recovery_lab.py": (
        "deploy/appliance/power_state_recovery_lab.py",
        0o755,
    ),
    "protocol_interoperability_lab.py": (
        "deploy/appliance/protocol_interoperability_lab.py",
        0o755,
    ),
    "recover-appliance-upgrade.sh": (
        "deploy/appliance/recover-appliance-upgrade.sh",
        0o755,
    ),
    "storage_recovery_lab.py": (
        "deploy/appliance/storage_recovery_lab.py",
        0o755,
    ),
    "restore-state.sh": ("deploy/appliance/restore-state.sh", 0o755),
    "start-tls.sh": ("deploy/appliance/start-tls.sh", 0o755),
    "systemd/echo-audit-evidence.service.example": (
        "deploy/appliance/systemd/echo-audit-evidence.service.example",
        0o644,
    ),
    "systemd/echo-audit-evidence.timer.example": (
        "deploy/appliance/systemd/echo-audit-evidence.timer.example",
        0o644,
    ),
    "systemd/echo-appliance-upgrade-recovery.service.example": (
        "deploy/appliance/systemd/echo-appliance-upgrade-recovery.service.example",
        0o644,
    ),
    "systemd/echo-state-backup.service.example": (
        "deploy/appliance/systemd/echo-state-backup.service.example",
        0o644,
    ),
    "systemd/echo-state-backup.timer.example": (
        "deploy/appliance/systemd/echo-state-backup.timer.example",
        0o644,
    ),
    "tls/README.md": ("deploy/appliance/tls/README.md", 0o644),
    "tls/nginx-image.lock.json": (
        "deploy/appliance/tls/nginx-image.lock.json",
        0o644,
    ),
    "tls/nginx.conf": ("deploy/appliance/tls/nginx.conf", 0o644),
    "tls/verify-tls-assets.sh": (
        "deploy/appliance/tls/verify-tls-assets.sh",
        0o755,
    ),
    "upgrade-appliance.sh": ("deploy/appliance/upgrade-appliance.sh", 0o755),
    "upgrade_transaction.py": ("deploy/appliance/upgrade_transaction.py", 0o755),
    "verify-running-appliance.py": (
        "deploy/appliance/verify-running-appliance.py",
        0o755,
    ),
}
GENERATED_MODES = {"echo-release.env": 0o600}
PAYLOAD_MODES = {
    **{destination: mode for destination, (_, mode) in SOURCE_FILES.items()},
    **GENERATED_MODES,
}


class OperationsBundleError(RuntimeError):
    """The operations bundle could not be handled safely."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        + b"\n"
    )


def _safe_read(path: Path, *, maximum: int) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise OperationsBundleError(f"cannot safely read operations bundle input: {path}") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or not 0 <= info.st_size <= maximum:
            raise OperationsBundleError(
                f"operations bundle input is not a bounded regular file: {path}"
            )
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, maximum - total + 1))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum:
                raise OperationsBundleError(
                    f"operations bundle input exceeds its size limit: {path}"
                )
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _source_payload(source_root: Path, image_reference: str) -> dict[str, bytes]:
    if IMAGE_REFERENCE_PATTERN.fullmatch(image_reference) is None:
        raise OperationsBundleError("operations bundle image must be an immutable digest reference")
    payload = {
        destination: _safe_read(source_root / source, maximum=MAX_SOURCE_BYTES)
        for destination, (source, _) in SOURCE_FILES.items()
    }
    payload["echo-release.env"] = f"ECHO_OS_IMAGE={image_reference}\n".encode("ascii")
    return payload


def _artifact_id(payload: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for relative in sorted(payload):
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(hashlib.sha256(payload[relative]).digest())
        digest.update(b"\0")
        digest.update(f"{PAYLOAD_MODES[relative]:04o}".encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()[:16]


def _manifest(payload: dict[str, bytes], artifact_id: str, image_reference: str) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "artifact": {
            "id": artifact_id,
            "name": f"echo-appliance-operations-{artifact_id}",
            "architectures": list(ARCHITECTURES),
            "imageReference": image_reference,
            "entrypoints": {
                "install": "./install-appliance.sh",
                "bareMetalRecoveryLab": "./bare_metal_recovery_lab.py plan|run|verify",
                "nasDataBackup": "./nas_data_backup.py init|backup|check|restore",
                "deviceEnduranceLab": "./device_endurance_lab.py plan|run",
                "hubLifecycleLab": "./hub_lifecycle_lab.py plan|run|verify",
                "lanDiscoveryFunctionalLab": (
                    "./lan_discovery_functional_lab.py "
                    "plan|credentials|syncthing|home-assistant|verify"
                ),
                "paperlessFunctionalLab": ("./paperless_functional_lab.py plan|run|verify"),
                "tls": "./start-tls.sh",
                "upgrade": "./upgrade-appliance.sh <registry@sha256:...>",
                "upgradeRecovery": "./recover-appliance-upgrade.sh",
                "restore": "./restore-state.sh <external-verified.echo-backup>",
                "operationsSystemd": "./operations_systemd.py plan|apply|remove-plan|remove",
                "operationsSystemdLab": "./operations_systemd_lab.py plan|run",
                "powerStateRecoveryLab": "./power_state_recovery_lab.py seed|plan|run|verify",
                "protocolInteroperabilityLab": (
                    "./protocol_interoperability_lab.py "
                    "plan|probe|permissions|quota|large-file|verify"
                ),
                "storageRecoveryLab": "./storage_recovery_lab.py plan|run",
            },
        },
        "files": {
            relative: {
                "sha256": _sha256(payload[relative]),
                "size": len(payload[relative]),
                "mode": f"{PAYLOAD_MODES[relative]:04o}",
            }
            for relative in sorted(payload)
        },
    }


def _payload_checksums(payload: dict[str, bytes]) -> bytes:
    return "".join(
        f"{_sha256(payload[relative])}  {relative}\n" for relative in sorted(payload)
    ).encode()


def _spdx_id(relative: str) -> str:
    return f"SPDXRef-File-{hashlib.sha256(relative.encode()).hexdigest()[:16]}"


def _package_verification_code(payload: dict[str, bytes]) -> str:
    hashes = sorted(
        hashlib.sha1(data, usedforsecurity=False).hexdigest() for data in payload.values()
    )
    # SPDX 2.3 mandates SHA-1 for this package verification code.
    return hashlib.sha1(  # nosec B324
        "".join(hashes).encode("ascii"), usedforsecurity=False
    ).hexdigest()


def _sbom(payload: dict[str, bytes], artifact_id: str) -> dict[str, Any]:
    package_id = "SPDXRef-Package-EchoApplianceOperations"
    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"echo-appliance-operations-{artifact_id}",
        "documentNamespace": (f"https://echo-age.com/spdx/echo-appliance-operations/{artifact_id}"),
        "creationInfo": {
            "created": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "creators": ["Tool: Echo-appliance-operations-bundle/1"],
        },
        "documentDescribes": [package_id],
        "packages": [
            {
                "name": "Echo Appliance Operations Bundle",
                "SPDXID": package_id,
                "versionInfo": artifact_id,
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": True,
                "packageVerificationCode": {
                    "packageVerificationCodeValue": _package_verification_code(payload)
                },
                "licenseConcluded": "Apache-2.0",
                "licenseDeclared": "Apache-2.0",
                "copyrightText": "NOASSERTION",
                "primaryPackagePurpose": "APPLICATION",
            }
        ],
        "files": [
            {
                "fileName": f"./{relative}",
                "SPDXID": _spdx_id(relative),
                "checksums": [{"algorithm": "SHA256", "checksumValue": _sha256(payload[relative])}],
                "licenseConcluded": "Apache-2.0",
                "copyrightText": "NOASSERTION",
            }
            for relative in sorted(payload)
        ],
        "relationships": [
            {
                "spdxElementId": package_id,
                "relationshipType": "CONTAINS",
                "relatedSpdxElement": _spdx_id(relative),
            }
            for relative in sorted(payload)
        ],
    }


def _validate_sbom(data: bytes, payload: dict[str, bytes], artifact_id: str) -> None:
    try:
        value = json.loads(data.decode())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OperationsBundleError("operations bundle SPDX SBOM is invalid") from exc
    packages = value.get("packages") if isinstance(value, dict) else None
    files = value.get("files") if isinstance(value, dict) else None
    if (
        not isinstance(value, dict)
        or value.get("spdxVersion") != "SPDX-2.3"
        or value.get("dataLicense") != "CC0-1.0"
        or value.get("name") != f"echo-appliance-operations-{artifact_id}"
        or value.get("documentDescribes") != ["SPDXRef-Package-EchoApplianceOperations"]
        or not isinstance(packages, list)
        or len(packages) != 1
        or packages[0].get("versionInfo") != artifact_id
        or packages[0].get("licenseDeclared") != "Apache-2.0"
        or packages[0].get("packageVerificationCode")
        != {"packageVerificationCodeValue": _package_verification_code(payload)}
        or not isinstance(files, list)
    ):
        raise OperationsBundleError("operations bundle SPDX SBOM schema is invalid")
    observed: dict[str, str] = {}
    for item in files:
        checksums = item.get("checksums") if isinstance(item, dict) else None
        filename = item.get("fileName") if isinstance(item, dict) else None
        if (
            not isinstance(filename, str)
            or not filename.startswith("./")
            or not isinstance(checksums, list)
            or len(checksums) != 1
            or checksums[0].get("algorithm") != "SHA256"
            or not isinstance(checksums[0].get("checksumValue"), str)
            or filename[2:] in observed
        ):
            raise OperationsBundleError("operations bundle SPDX file inventory is invalid")
        observed[filename[2:]] = checksums[0]["checksumValue"]
    expected = {relative: _sha256(content) for relative, content in payload.items()}
    if observed != expected:
        raise OperationsBundleError("operations bundle SPDX SBOM does not match its payload")


def _archive_files(payload: dict[str, bytes], artifact_id: str, image_reference: str):
    files = {relative: (content, PAYLOAD_MODES[relative]) for relative, content in payload.items()}
    files[MANIFEST_NAME] = (
        _canonical_json(_manifest(payload, artifact_id, image_reference)),
        0o644,
    )
    files[PAYLOAD_CHECKSUMS_NAME] = (_payload_checksums(payload), 0o644)
    return files


def _tar_info(name: str, *, mode: int, size: int = 0, directory: bool = False):
    info = tarfile.TarInfo(name=name + ("/" if directory else ""))
    info.type = tarfile.DIRTYPE if directory else tarfile.REGTYPE
    info.mode = mode
    info.uid = 0
    info.gid = 0
    info.uname = "root"
    info.gname = "root"
    info.mtime = 0
    info.size = 0 if directory else size
    return info


def _write_archive(path: Path, root_name: str, files: dict[str, tuple[bytes, int]]) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        owned = descriptor
        descriptor = -1
        with (
            os.fdopen(owned, "wb") as raw,
            gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed,
            tarfile.open(mode="w", fileobj=compressed, format=tarfile.USTAR_FORMAT) as archive,
        ):
            directories = {root_name}
            for relative in files:
                current = PurePosixPath(root_name) / PurePosixPath(relative).parent
                while str(current) != ".":
                    directories.add(current.as_posix())
                    if current.as_posix() == root_name:
                        break
                    current = current.parent
            for directory in sorted(directories, key=lambda item: (item.count("/"), item)):
                archive.addfile(_tar_info(directory, mode=0o755, directory=True))
            for relative in sorted(files):
                content, mode = files[relative]
                archive.addfile(
                    _tar_info(f"{root_name}/{relative}", mode=mode, size=len(content)),
                    io.BytesIO(content),
                )
            raw.flush()
            os.fsync(raw.fileno())
        os.replace(temporary, path)
        path.chmod(0o644)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary.exists():
            temporary.unlink()


def _atomic_write(path: Path, data: bytes, *, mode: int = 0o644) -> None:
    if path.is_symlink():
        raise OperationsBundleError("refusing to replace an operations bundle metadata symlink")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        owned = descriptor
        descriptor = -1
        with os.fdopen(owned, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        path.chmod(mode)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary.exists():
            temporary.unlink()


def _collect_members(archive: tarfile.TarFile) -> tuple[str, dict[str, tuple[bytes, int]]]:
    members = archive.getmembers()
    if not 1 <= len(members) <= MAX_ARCHIVE_MEMBERS:
        raise OperationsBundleError("operations bundle has an unsafe member count")
    roots: set[str] = set()
    files: dict[str, tuple[bytes, int]] = {}
    for member in members:
        pure = PurePosixPath(member.name)
        if pure.is_absolute() or ".." in pure.parts or len(pure.parts) < 1:
            raise OperationsBundleError("operations bundle contains an unsafe path")
        roots.add(pure.parts[0])
        if member.issym() or member.islnk() or member.isdev():
            raise OperationsBundleError("operations bundle contains a link or device")
        if member.isdir():
            if stat.S_IMODE(member.mode) != 0o755:
                raise OperationsBundleError("operations bundle directory mode is unsafe")
            continue
        if not member.isfile() or not 0 <= member.size <= MAX_SOURCE_BYTES:
            raise OperationsBundleError("operations bundle contains an unsafe file")
        relative = PurePosixPath(*pure.parts[1:]).as_posix()
        if not relative or relative in files:
            raise OperationsBundleError("operations bundle contains a duplicate file")
        extracted = archive.extractfile(member)
        if extracted is None:
            raise OperationsBundleError("operations bundle file cannot be read")
        content = extracted.read(MAX_SOURCE_BYTES + 1)
        if len(content) != member.size or len(content) > MAX_SOURCE_BYTES:
            raise OperationsBundleError("operations bundle file size is invalid")
        files[relative] = (content, stat.S_IMODE(member.mode))
    if len(roots) != 1:
        raise OperationsBundleError("operations bundle must have one top-level directory")
    return next(iter(roots)), files


def _read_archive(path: Path) -> tuple[str, dict[str, tuple[bytes, int]]]:
    archive_data = _safe_read(path, maximum=MAX_ARCHIVE_BYTES)
    try:
        with tarfile.open(mode="r:gz", fileobj=io.BytesIO(archive_data)) as archive:
            return _collect_members(archive)
    except (tarfile.TarError, OSError) as exc:
        raise OperationsBundleError("operations bundle is not a valid gzip tar archive") from exc


def _validated_manifest(data: bytes) -> dict[str, Any]:
    try:
        value = json.loads(data.decode())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OperationsBundleError("operations bundle manifest is invalid") from exc
    artifact = value.get("artifact") if isinstance(value, dict) else None
    files = value.get("files") if isinstance(value, dict) else None
    artifact_id = artifact.get("id") if isinstance(artifact, dict) else None
    expected_entrypoints = {
        "install": "./install-appliance.sh",
        "bareMetalRecoveryLab": "./bare_metal_recovery_lab.py plan|run|verify",
        "nasDataBackup": "./nas_data_backup.py init|backup|check|restore",
        "deviceEnduranceLab": "./device_endurance_lab.py plan|run",
        "hubLifecycleLab": "./hub_lifecycle_lab.py plan|run|verify",
        "lanDiscoveryFunctionalLab": (
            "./lan_discovery_functional_lab.py plan|credentials|syncthing|home-assistant|verify"
        ),
        "paperlessFunctionalLab": "./paperless_functional_lab.py plan|run|verify",
        "tls": "./start-tls.sh",
        "upgrade": "./upgrade-appliance.sh <registry@sha256:...>",
        "upgradeRecovery": "./recover-appliance-upgrade.sh",
        "restore": "./restore-state.sh <external-verified.echo-backup>",
        "operationsSystemd": "./operations_systemd.py plan|apply|remove-plan|remove",
        "operationsSystemdLab": "./operations_systemd_lab.py plan|run",
        "powerStateRecoveryLab": "./power_state_recovery_lab.py seed|plan|run|verify",
        "protocolInteroperabilityLab": (
            "./protocol_interoperability_lab.py plan|probe|permissions|quota|large-file|verify"
        ),
        "storageRecoveryLab": "./storage_recovery_lab.py plan|run",
    }
    if (
        not isinstance(value, dict)
        or value.get("schemaVersion") != SCHEMA_VERSION
        or not isinstance(artifact, dict)
        or not isinstance(files, dict)
        or not isinstance(artifact_id, str)
        or ARTIFACT_ID_PATTERN.fullmatch(artifact_id) is None
        or artifact.get("name") != f"echo-appliance-operations-{artifact_id}"
        or artifact.get("architectures") != list(ARCHITECTURES)
        or IMAGE_REFERENCE_PATTERN.fullmatch(str(artifact.get("imageReference"))) is None
        or artifact.get("entrypoints") != expected_entrypoints
        or set(files) != set(PAYLOAD_MODES)
    ):
        raise OperationsBundleError("operations bundle manifest schema is invalid")
    return value


def _verified_payload(
    archive_path: Path, *, expected_image_reference: str | None = None
) -> tuple[dict[str, Any], dict[str, bytes]]:
    root_name, files = _read_archive(archive_path)
    expected_names = {*PAYLOAD_MODES, MANIFEST_NAME, PAYLOAD_CHECKSUMS_NAME}
    if set(files) != expected_names:
        raise OperationsBundleError("operations bundle file set is incomplete or unexpected")
    manifest = _validated_manifest(files[MANIFEST_NAME][0])
    artifact = manifest["artifact"]
    artifact_id = artifact["id"]
    if root_name != f"echo-appliance-operations-{artifact_id}":
        raise OperationsBundleError("operations bundle root does not match its artifact ID")
    if (
        expected_image_reference is not None
        and artifact["imageReference"] != expected_image_reference
    ):
        raise OperationsBundleError("operations bundle image does not match the release image")
    payload: dict[str, bytes] = {}
    for relative, expected_mode in PAYLOAD_MODES.items():
        content, mode = files[relative]
        record = manifest["files"].get(relative)
        if (
            mode != expected_mode
            or not isinstance(record, dict)
            or record.get("sha256") != _sha256(content)
            or record.get("size") != len(content)
            or record.get("mode") != f"{expected_mode:04o}"
        ):
            raise OperationsBundleError(
                f"operations bundle file failed integrity checks: {relative}"
            )
        payload[relative] = content
    if artifact_id != _artifact_id(payload):
        raise OperationsBundleError("operations bundle artifact ID does not match its payload")
    if files[PAYLOAD_CHECKSUMS_NAME] != (_payload_checksums(payload), 0o644):
        raise OperationsBundleError("operations bundle checksum inventory is invalid")
    if files[MANIFEST_NAME][1] != 0o644:
        raise OperationsBundleError("operations bundle manifest mode is invalid")
    expected_env = f"ECHO_OS_IMAGE={artifact['imageReference']}\n".encode("ascii")
    if payload["echo-release.env"] != expected_env:
        raise OperationsBundleError("operations bundle release environment is inconsistent")
    return manifest, payload


def verify(archive_path: Path, *, expected_image_reference: str | None = None) -> dict[str, Any]:
    archive_path = archive_path.resolve()
    manifest, payload = _verified_payload(
        archive_path, expected_image_reference=expected_image_reference
    )
    return {
        "verified": True,
        "artifactId": manifest["artifact"]["id"],
        "root": manifest["artifact"]["name"],
        "architectures": list(ARCHITECTURES),
        "imageReference": manifest["artifact"]["imageReference"],
        "fileCount": len(payload),
        "archive": str(archive_path),
        "archiveSha256": _sha256(_safe_read(archive_path, maximum=MAX_ARCHIVE_BYTES)),
    }


def verify_release_artifacts(
    archive_path: Path,
    checksum_path: Path,
    sbom_path: Path,
    *,
    expected_image_reference: str,
) -> dict[str, Any]:
    report = verify(archive_path, expected_image_reference=expected_image_reference)
    expected_checksum = f"{report['archiveSha256']}  {archive_path.name}\n".encode("ascii")
    checksum_data = _safe_read(checksum_path, maximum=4096)
    if checksum_data != expected_checksum:
        raise OperationsBundleError("operations bundle outer checksum is invalid")
    manifest, payload = _verified_payload(
        archive_path.resolve(), expected_image_reference=expected_image_reference
    )
    sbom_data = _safe_read(sbom_path, maximum=MAX_SOURCE_BYTES)
    _validate_sbom(sbom_data, payload, manifest["artifact"]["id"])
    return {
        **report,
        "checksum": str(checksum_path.resolve()),
        "checksumSha256": _sha256(checksum_data),
        "sbom": str(sbom_path.resolve()),
        "sbomSha256": _sha256(sbom_data),
        "embeddedVerifierSha256": _sha256(payload["operations_bundle.py"]),
    }


def build(source_root: Path, output_directory: Path, image_reference: str) -> dict[str, Any]:
    source_root = source_root.resolve()
    output_directory.mkdir(parents=True, exist_ok=True)
    if output_directory.is_symlink() or not output_directory.is_dir():
        raise OperationsBundleError("operations bundle output directory is unsafe")
    payload = _source_payload(source_root, image_reference)
    artifact_id = _artifact_id(payload)
    root_name = f"echo-appliance-operations-{artifact_id}"
    archive_path = output_directory / ARCHIVE_NAME
    checksum_path = output_directory / CHECKSUM_NAME
    sbom_path = output_directory / SBOM_NAME
    if archive_path.is_symlink():
        raise OperationsBundleError("refusing to replace an operations bundle symlink")
    _write_archive(
        archive_path,
        root_name,
        _archive_files(payload, artifact_id, image_reference),
    )
    archive_sha256 = _sha256(_safe_read(archive_path, maximum=MAX_ARCHIVE_BYTES))
    _atomic_write(
        checksum_path,
        f"{archive_sha256}  {archive_path.name}\n".encode("ascii"),
    )
    sbom_data = _canonical_json(_sbom(payload, artifact_id))
    _validate_sbom(sbom_data, payload, artifact_id)
    _atomic_write(sbom_path, sbom_data)
    return verify_release_artifacts(
        archive_path,
        checksum_path,
        sbom_path,
        expected_image_reference=image_reference,
    )


def _verify_production_ownership(
    root: Path,
) -> None:
    for path in (root, *root.rglob("*")):
        info = path.stat()
        if info.st_uid != 0 or stat.S_IMODE(info.st_mode) & 0o022:
            raise OperationsBundleError(
                "production operations bundle must be root-owned and not group/other writable"
            )


def extract(
    archive_path: Path,
    destination: Path,
    *,
    require_root_owner: bool = False,
) -> dict[str, Any]:
    archive_path = archive_path.resolve()
    manifest, payload = _verified_payload(archive_path)
    extracted_files = {
        **payload,
        MANIFEST_NAME: _canonical_json(manifest),
        PAYLOAD_CHECKSUMS_NAME: _payload_checksums(payload),
    }
    extracted_modes = {
        **PAYLOAD_MODES,
        MANIFEST_NAME: 0o644,
        PAYLOAD_CHECKSUMS_NAME: 0o644,
    }
    root_name = manifest["artifact"]["name"]
    if require_root_owner and os.geteuid() != 0:
        raise OperationsBundleError("production operations bundle extraction requires root")
    destination.mkdir(mode=0o755, parents=True, exist_ok=True)
    if destination.is_symlink() or not destination.is_dir():
        raise OperationsBundleError("operations bundle extraction destination is unsafe")
    destination = destination.resolve()
    if require_root_owner:
        _verify_production_ownership(destination)
    target = destination / root_name
    if target.exists() or target.is_symlink():
        raise OperationsBundleError("operations bundle extraction target already exists")
    temporary_parent = Path(tempfile.mkdtemp(prefix=".echo-operations.", dir=destination))
    temporary_root = temporary_parent / root_name
    try:
        temporary_root.mkdir(mode=0o755)
        for relative in sorted(extracted_files):
            target_file = temporary_root / PurePosixPath(relative)
            target_file.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            descriptor = os.open(
                target_file,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                extracted_modes[relative],
            )
            try:
                with os.fdopen(descriptor, "wb") as output:
                    descriptor = -1
                    output.write(extracted_files[relative])
                    output.flush()
                    os.fsync(output.fileno())
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
            target_file.chmod(extracted_modes[relative])
        if require_root_owner:
            _verify_production_ownership(temporary_root)
        os.replace(temporary_root, target)
        temporary_parent.rmdir()
    finally:
        if temporary_root.exists():
            for path in sorted(temporary_root.rglob("*"), reverse=True):
                path.unlink() if path.is_file() else path.rmdir()
            temporary_root.rmdir()
        if temporary_parent.exists():
            temporary_parent.rmdir()
    return {
        "extracted": True,
        "artifactId": manifest["artifact"]["id"],
        "imageReference": manifest["artifact"]["imageReference"],
        "destination": str(target),
        "fileCount": len(extracted_files),
        "payloadFileCount": len(payload),
        "productionRootOwned": require_root_owner,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    build_parser = subparsers.add_parser("build")
    build_parser.add_argument("--image-reference", required=True)
    build_parser.add_argument(
        "--source-root", type=Path, default=Path(__file__).resolve().parents[2]
    )
    build_parser.add_argument("--output-directory", type=Path, default=Path("dist"))
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("archive", type=Path)
    verify_parser.add_argument("--expected-image-reference")
    verify_parser.add_argument("--checksum", type=Path)
    verify_parser.add_argument("--sbom", type=Path)
    extract_parser = subparsers.add_parser("extract")
    extract_parser.add_argument("archive", type=Path)
    extract_parser.add_argument("--destination", type=Path, required=True)
    extract_parser.add_argument("--require-root-owner", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.action == "build":
            result = build(args.source_root, args.output_directory, args.image_reference)
        elif args.action == "verify":
            checksum = args.checksum or args.archive.with_name(CHECKSUM_NAME)
            sbom = args.sbom or args.archive.with_name(SBOM_NAME)
            if checksum.exists() or sbom.exists():
                if not checksum.is_file() or not sbom.is_file():
                    raise OperationsBundleError(
                        "operations bundle checksum and SPDX SBOM must be supplied together"
                    )
                reference = args.expected_image_reference or verify(args.archive)["imageReference"]
                result = verify_release_artifacts(
                    args.archive,
                    checksum,
                    sbom,
                    expected_image_reference=reference,
                )
            else:
                result = verify(
                    args.archive,
                    expected_image_reference=args.expected_image_reference,
                )
        else:
            result = extract(
                args.archive,
                args.destination,
                require_root_owner=args.require_root_owner,
            )
    except (OperationsBundleError, OSError, tarfile.TarError) as exc:
        print(f"Echo operations bundle failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
