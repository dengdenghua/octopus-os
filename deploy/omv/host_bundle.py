#!/usr/bin/env python3
"""Build and verify a deterministic Echo OMV host integration bundle."""

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
MAX_SOURCE_BYTES = 8 * 1024 * 1024
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 32
ARCHITECTURES = ("amd64", "arm64")
CONTROLLED_WRITES = (
    "shared-folder.create.simple.v1",
    "shared-folder.privilege.simple.v1",
    "smb.share.desired.v1",
    "nfs.share.private-network.v1",
    "filesystem.quota.user-group.v1",
    "account.group.create.v1",
    "account.user.create.v1",
    "account.user.password.reset.v1",
)
MANIFEST_NAME = "bundle-manifest.json"
CHECKSUMS_NAME = "SHA256SUMS"
ARTIFACT_PATTERN = re.compile(r"echo-omv-host-([0-9a-f]{16})")
SOURCE_MODES = {
    "appliance/__init__.py": 0o644,
    "appliance/omv_bridge.py": 0o644,
    "deploy/omv/README.md": 0o644,
    "deploy/omv/echo-omv-bridge.service.example": 0o644,
    "deploy/omv/echo_omv_host.py": 0o755,
    "deploy/omv/platform_preflight.py": 0o755,
}


class BundleError(RuntimeError):
    """The OMV host bundle could not be built or verified safely."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_json(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


def _safe_read(path: Path, *, maximum: int) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise BundleError(f"cannot safely read bundle input: {path}") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or not 0 <= info.st_size <= maximum:
            raise BundleError(f"bundle input is not a bounded regular file: {path}")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, maximum - total + 1))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum:
                raise BundleError(f"bundle input exceeds its size limit: {path}")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _source_payload(source_root: Path) -> dict[str, bytes]:
    return {
        relative: _safe_read(source_root / relative, maximum=MAX_SOURCE_BYTES)
        for relative in SOURCE_MODES
    }


def _artifact_id(payload: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for relative in sorted(payload):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(payload[relative]).digest())
        digest.update(b"\0")
    return digest.hexdigest()[:16]


def _manifest(payload: dict[str, bytes], artifact_id: str) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "artifact": {
            "id": artifact_id,
            "name": f"echo-omv-host-{artifact_id}",
            "architectures": list(ARCHITECTURES),
            "installEntrypoint": "deploy/omv/echo_omv_host.py",
            "readOnlyStorageBridge": False,
            "controlledWrites": list(CONTROLLED_WRITES),
        },
        "files": {
            relative: {
                "sha256": _sha256(payload[relative]),
                "size": len(payload[relative]),
                "mode": f"{SOURCE_MODES[relative]:04o}",
            }
            for relative in sorted(payload)
        },
    }


def _checksums(payload: dict[str, bytes]) -> bytes:
    return "".join(
        f"{_sha256(payload[relative])}  {relative}\n" for relative in sorted(payload)
    ).encode("utf-8")


def _spdx_id(relative: str) -> str:
    return f"SPDXRef-File-{hashlib.sha256(relative.encode()).hexdigest()[:16]}"


def _package_verification_code(payload: dict[str, bytes]) -> str:
    file_hashes = sorted(
        hashlib.sha1(data, usedforsecurity=False).hexdigest() for data in payload.values()
    )
    # SPDX 2.3 mandates SHA-1 for the package verification code.
    return hashlib.sha1(  # nosec B324
        "".join(file_hashes).encode("ascii"),
        usedforsecurity=False,
    ).hexdigest()


def _sbom(payload: dict[str, bytes], artifact_id: str) -> dict[str, Any]:
    package_id = "SPDXRef-Package-EchoOmvHost"
    files = [
        {
            "fileName": f"./{relative}",
            "SPDXID": _spdx_id(relative),
            "checksums": [
                {
                    "algorithm": "SHA256",
                    "checksumValue": _sha256(payload[relative]),
                }
            ],
            "licenseConcluded": "Apache-2.0",
            "copyrightText": "NOASSERTION",
        }
        for relative in sorted(payload)
    ]
    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"echo-omv-host-{artifact_id}",
        "documentNamespace": f"https://echo-age.com/spdx/echo-omv-host/{artifact_id}",
        "creationInfo": {
            "created": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "creators": ["Tool: Echo-OMV-host-bundle/1"],
        },
        "documentDescribes": [package_id],
        "packages": [
            {
                "name": "Echo OMV Host Integration",
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
                "comment": (
                    "Architecture-neutral Python/systemd integration. Runtime requires "
                    "Linux, systemd, Python 3, OpenMediaVault omv-rpc, and util-linux lsblk."
                ),
            }
        ],
        "files": files,
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
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BundleError("OMV host SPDX SBOM is invalid") from exc
    if (
        not isinstance(value, dict)
        or value.get("spdxVersion") != "SPDX-2.3"
        or value.get("dataLicense") != "CC0-1.0"
        or value.get("name") != f"echo-omv-host-{artifact_id}"
        or value.get("documentDescribes") != ["SPDXRef-Package-EchoOmvHost"]
        or not isinstance(value.get("packages"), list)
        or len(value["packages"]) != 1
        or value["packages"][0].get("versionInfo") != artifact_id
        or value["packages"][0].get("licenseDeclared") != "Apache-2.0"
        or value["packages"][0].get("packageVerificationCode")
        != {"packageVerificationCodeValue": _package_verification_code(payload)}
        or not isinstance(value.get("files"), list)
    ):
        raise BundleError("OMV host SPDX SBOM schema is invalid")
    observed: dict[str, str] = {}
    for item in value["files"]:
        if not isinstance(item, dict) or not isinstance(item.get("checksums"), list):
            raise BundleError("OMV host SPDX SBOM file inventory is invalid")
        filename = str(item.get("fileName", ""))
        checksums = item["checksums"]
        if (
            not filename.startswith("./")
            or len(checksums) != 1
            or checksums[0].get("algorithm") != "SHA256"
            or not isinstance(checksums[0].get("checksumValue"), str)
        ):
            raise BundleError("OMV host SPDX SBOM file checksum is invalid")
        observed[filename[2:]] = checksums[0]["checksumValue"]
    expected = {relative: _sha256(data) for relative, data in payload.items()}
    if observed != expected:
        raise BundleError("OMV host SPDX SBOM does not match its payload")


def _archive_files(payload: dict[str, bytes], artifact_id: str) -> dict[str, tuple[bytes, int]]:
    manifest = _manifest(payload, artifact_id)
    files = {relative: (data, SOURCE_MODES[relative]) for relative, data in payload.items()}
    files[MANIFEST_NAME] = (_canonical_json(manifest), 0o644)
    files[CHECKSUMS_NAME] = (_checksums(payload), 0o644)
    return files


def _tar_info(name: str, *, mode: int, size: int = 0, directory: bool = False) -> tarfile.TarInfo:
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
            tarfile.open(
                mode="w",
                fileobj=compressed,
                format=tarfile.USTAR_FORMAT,
            ) as archive,
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
                data, mode = files[relative]
                archive.addfile(
                    _tar_info(
                        f"{root_name}/{relative}",
                        mode=mode,
                        size=len(data),
                    ),
                    io.BytesIO(data),
                )
            raw.flush()
            os.fsync(raw.fileno())
        os.replace(temporary, path)
        path.chmod(0o644)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary.exists():
            temporary.unlink()


def _atomic_write(path: Path, data: bytes, *, mode: int) -> None:
    if path.is_symlink():
        raise BundleError("refusing to replace a bundle metadata symlink")
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
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary.exists():
            temporary.unlink()


def build(source_root: Path, output_directory: Path) -> dict[str, Any]:
    source_root = source_root.resolve()
    output_directory.mkdir(parents=True, exist_ok=True)
    if output_directory.is_symlink() or not output_directory.is_dir():
        raise BundleError("bundle output directory is unsafe")
    payload = _source_payload(source_root)
    artifact_id = _artifact_id(payload)
    root_name = f"echo-omv-host-{artifact_id}"
    archive_path = output_directory / f"{root_name}.tar.gz"
    if archive_path.is_symlink():
        raise BundleError("refusing to replace a bundle symlink")
    _write_archive(archive_path, root_name, _archive_files(payload, artifact_id))
    verification = verify(archive_path)
    archive_sha256 = _sha256(_safe_read(archive_path, maximum=MAX_ARCHIVE_BYTES))
    checksum_path = archive_path.with_name(f"{archive_path.name}.sha256")
    _atomic_write(
        checksum_path,
        f"{archive_sha256}  {archive_path.name}\n".encode("ascii"),
        mode=0o644,
    )
    sbom_path = archive_path.with_name(f"{root_name}.spdx.json")
    sbom_data = _canonical_json(_sbom(payload, artifact_id))
    _validate_sbom(sbom_data, payload, artifact_id)
    _atomic_write(sbom_path, sbom_data, mode=0o644)
    return {
        **verification,
        "archive": str(archive_path.resolve()),
        "archiveSha256": archive_sha256,
        "checksum": str(checksum_path.resolve()),
        "sbom": str(sbom_path.resolve()),
    }


def _collect_archive_members(
    archive: tarfile.TarFile,
) -> tuple[str, dict[str, tuple[bytes, int]]]:
    members = archive.getmembers()
    if not 1 <= len(members) <= MAX_ARCHIVE_MEMBERS:
        raise BundleError("OMV host bundle has an unsafe member count")
    roots: set[str] = set()
    files: dict[str, tuple[bytes, int]] = {}
    for member in members:
        pure = PurePosixPath(member.name)
        if pure.is_absolute() or ".." in pure.parts or len(pure.parts) < 1:
            raise BundleError("OMV host bundle contains an unsafe path")
        roots.add(pure.parts[0])
        if member.issym() or member.islnk() or member.isdev():
            raise BundleError("OMV host bundle contains a link or device")
        if member.isdir():
            if stat.S_IMODE(member.mode) != 0o755:
                raise BundleError("OMV host bundle directory mode is unsafe")
            continue
        if not member.isfile() or not 0 <= member.size <= MAX_SOURCE_BYTES:
            raise BundleError("OMV host bundle contains an unsafe file")
        relative = PurePosixPath(*pure.parts[1:]).as_posix()
        if not relative or relative in files:
            raise BundleError("OMV host bundle contains a duplicate file")
        extracted = archive.extractfile(member)
        if extracted is None:
            raise BundleError("OMV host bundle file cannot be read")
        data = extracted.read(MAX_SOURCE_BYTES + 1)
        if len(data) != member.size or len(data) > MAX_SOURCE_BYTES:
            raise BundleError("OMV host bundle file size is invalid")
        files[relative] = (data, stat.S_IMODE(member.mode))
    if len(roots) != 1:
        raise BundleError("OMV host bundle must have one top-level directory")
    return next(iter(roots)), files


def _read_archive_members(archive_path: Path) -> tuple[str, dict[str, tuple[bytes, int]]]:
    archive_data = _safe_read(archive_path, maximum=MAX_ARCHIVE_BYTES)
    try:
        with tarfile.open(mode="r:gz", fileobj=io.BytesIO(archive_data)) as archive:
            return _collect_archive_members(archive)
    except (tarfile.TarError, OSError) as exc:
        raise BundleError("OMV host bundle is not a valid gzip tar archive") from exc


def _validated_manifest(data: bytes) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BundleError("OMV host bundle manifest is invalid") from exc
    artifact = value.get("artifact") if isinstance(value, dict) else None
    files = value.get("files") if isinstance(value, dict) else None
    if (
        not isinstance(value, dict)
        or value.get("schemaVersion") != SCHEMA_VERSION
        or not isinstance(artifact, dict)
        or not isinstance(files, dict)
        or not isinstance(artifact.get("id"), str)
        or ARTIFACT_PATTERN.fullmatch(str(artifact.get("name"))) is None
        or artifact.get("name") != f"echo-omv-host-{artifact['id']}"
        or artifact.get("architectures") != list(ARCHITECTURES)
        or artifact.get("installEntrypoint") != "deploy/omv/echo_omv_host.py"
        or artifact.get("readOnlyStorageBridge") is not False
        or artifact.get("controlledWrites") != list(CONTROLLED_WRITES)
        or set(files) != set(SOURCE_MODES)
    ):
        raise BundleError("OMV host bundle manifest schema is invalid")
    if not re.fullmatch(r"[0-9a-f]{16}", artifact["id"]):
        raise BundleError("OMV host bundle artifact ID is invalid")
    return value


def verify(archive_path: Path) -> dict[str, Any]:
    archive_path = archive_path.resolve()
    root_name, files = _read_archive_members(archive_path)
    expected_names = {*SOURCE_MODES, MANIFEST_NAME, CHECKSUMS_NAME}
    if set(files) != expected_names:
        raise BundleError("OMV host bundle file set is incomplete or unexpected")
    manifest = _validated_manifest(files[MANIFEST_NAME][0])
    artifact_id = manifest["artifact"]["id"]
    if root_name != f"echo-omv-host-{artifact_id}":
        raise BundleError("OMV host bundle root does not match its artifact ID")
    payload: dict[str, bytes] = {}
    for relative, expected_mode in SOURCE_MODES.items():
        data, mode = files[relative]
        record = manifest["files"].get(relative)
        if (
            mode != expected_mode
            or not isinstance(record, dict)
            or record.get("sha256") != _sha256(data)
            or record.get("size") != len(data)
            or record.get("mode") != f"{expected_mode:04o}"
        ):
            raise BundleError(f"OMV host bundle file failed integrity checks: {relative}")
        payload[relative] = data
    if artifact_id != _artifact_id(payload):
        raise BundleError("OMV host bundle artifact ID does not match its payload")
    if files[CHECKSUMS_NAME] != (_checksums(payload), 0o644):
        raise BundleError("OMV host bundle checksum inventory is invalid")
    if files[MANIFEST_NAME][1] != 0o644:
        raise BundleError("OMV host bundle manifest mode is invalid")
    return {
        "verified": True,
        "artifactId": artifact_id,
        "root": root_name,
        "architectures": list(ARCHITECTURES),
        "fileCount": len(payload),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    build_parser = subparsers.add_parser("build")
    build_parser.add_argument(
        "--source-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    build_parser.add_argument("--output-directory", type=Path, default=Path("dist"))
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("archive", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = (
            build(args.source_root, args.output_directory)
            if args.action == "build"
            else verify(args.archive)
        )
    except (BundleError, OSError, tarfile.TarError) as exc:
        print(f"Echo OMV bundle operation failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
