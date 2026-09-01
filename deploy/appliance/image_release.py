#!/usr/bin/env python3
"""Validate a pushed multi-architecture Echo image and emit upgrade metadata."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from deploy.appliance import operations_bundle

SCHEMA_VERSION = 1
EXPECTED_PLATFORMS = ("linux/amd64", "linux/arm64")
EXPECTED_UV_VERSION = "0.11.25"
EXPECTED_AGENT_EXTRAS = ("serve", "tracing", "web", "local-auth", "video")
BUILD_DEPENDENCY_LOCK = "build-requirements.lock"
RUNTIME_DEPENDENCY_LOCK = "runtime-requirements.lock"
DEPENDENCY_LOCK_METADATA = "python-dependency-lock.json"
MAX_INPUT_BYTES = 8 * 1024 * 1024
IMAGE_INDEX_MEDIA_TYPES = {
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
}
IMAGE_MANIFEST_MEDIA_TYPES = {
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
}
IMAGE_NAME_PATTERN = re.compile(
    r"^ghcr\.io/[a-z0-9]+(?:[._-][a-z0-9]+)*/[a-z0-9]+(?:[._-][a-z0-9]+)*$"
)
DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
RAW_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
RELEASE_TAG_PATTERN = re.compile(
    r"^echo-appliance-v(?P<version>[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?)$"
)
SOURCE_REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SOURCE_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
LOCK_PIN_PATTERN = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)"
    r"(?:\[[A-Za-z0-9,._-]+\])?==(?P<version>[^ ;\\]+)"
    r"(?:\s*;\s*.+)?\s+\\$"
)
LOCK_HASH_PATTERN = re.compile(r"^    --hash=sha256:[0-9a-f]{64}(?: \\)?$")
FORBIDDEN_LOCK_TEXT = (
    "--index-url",
    "--extra-index-url",
    "--find-links",
    "--trusted-host",
    "--no-index",
    "file:",
    "git+",
    "http://",
    "https://",
    " -e ",
)


class ImageReleaseError(RuntimeError):
    """The image cannot be represented as a trusted Echo appliance release."""


def _utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_read(path: Path, *, maximum: int = MAX_INPUT_BYTES) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ImageReleaseError(f"cannot safely read release input: {path}") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or not 0 <= info.st_size <= maximum:
            raise ImageReleaseError(f"release input is not a bounded regular file: {path}")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, maximum - total + 1))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum:
                raise ImageReleaseError(f"release input exceeds its size limit: {path}")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _decode_json_object(data: bytes, context: str) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ImageReleaseError(f"{context} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ImageReleaseError(f"{context} must be a JSON object")
    return value


def _load_json(path: Path, context: str) -> dict[str, Any]:
    return _decode_json_object(_safe_read(path), context)


def _validate_descriptor(descriptor: Any) -> tuple[str, str, int, dict[str, Any]]:
    if not isinstance(descriptor, dict):
        raise ImageReleaseError("image index contains a non-object descriptor")
    digest = descriptor.get("digest")
    size = descriptor.get("size")
    media_type = descriptor.get("mediaType")
    platform = descriptor.get("platform")
    if (
        not isinstance(digest, str)
        or DIGEST_PATTERN.fullmatch(digest) is None
        or isinstance(size, bool)
        or not isinstance(size, int)
        or not 1 <= size <= 2**63 - 1
        or media_type not in IMAGE_MANIFEST_MEDIA_TYPES
        or not isinstance(platform, dict)
        or not isinstance(platform.get("os"), str)
        or not isinstance(platform.get("architecture"), str)
    ):
        raise ImageReleaseError("image index contains an unsafe descriptor")
    platform_name = f"{platform['os']}/{platform['architecture']}"
    return platform_name, digest, size, descriptor


def _validate_index(index: dict[str, Any]) -> tuple[dict[str, str], int]:
    manifests = index.get("manifests")
    if (
        index.get("schemaVersion") != 2
        or index.get("mediaType") not in IMAGE_INDEX_MEDIA_TYPES
        or not isinstance(manifests, list)
        or not 4 <= len(manifests) <= 32
    ):
        raise ImageReleaseError("registry image index schema is invalid")

    platform_digests: dict[str, str] = {}
    attestation_references: list[str] = []
    for raw_descriptor in manifests:
        platform, digest, _size, descriptor = _validate_descriptor(raw_descriptor)
        if platform in EXPECTED_PLATFORMS:
            if platform in platform_digests:
                raise ImageReleaseError(f"registry image index repeats {platform}")
            platform_digests[platform] = digest
            continue
        annotations = descriptor.get("annotations")
        if platform != "unknown/unknown" or not isinstance(annotations, dict):
            raise ImageReleaseError(f"registry image index has unexpected platform {platform}")
        if annotations.get("vnd.docker.reference.type") != "attestation-manifest":
            raise ImageReleaseError("unknown platform is not a BuildKit attestation manifest")
        reference = annotations.get("vnd.docker.reference.digest")
        if not isinstance(reference, str) or DIGEST_PATTERN.fullmatch(reference) is None:
            raise ImageReleaseError("BuildKit attestation manifest has an invalid reference")
        attestation_references.append(reference)

    if set(platform_digests) != set(EXPECTED_PLATFORMS):
        raise ImageReleaseError("registry image index does not contain amd64 and arm64")
    if len(attestation_references) < len(EXPECTED_PLATFORMS) or set(attestation_references) != set(
        platform_digests.values()
    ):
        raise ImageReleaseError(
            "registry image index lacks provenance/SBOM attestations for every platform"
        )
    return platform_digests, len(attestation_references)


def preflight_release_source(
    *,
    release_tag: str,
    source_repository: str,
    source_ref: str,
    source_sha: str,
) -> tuple[str, dict[str, str]]:
    """Reject a malformed tag or mutable source before an image build starts."""
    tag_match = RELEASE_TAG_PATTERN.fullmatch(release_tag)
    if tag_match is None:
        raise ImageReleaseError("release tag must match echo-appliance-v<semver>")
    if SOURCE_REPOSITORY_PATTERN.fullmatch(source_repository) is None:
        raise ImageReleaseError("source repository identity is invalid")
    if source_ref != f"refs/tags/{release_tag}":
        raise ImageReleaseError("source ref does not match the release tag")
    if SOURCE_SHA_PATTERN.fullmatch(source_sha) is None:
        raise ImageReleaseError("source commit must be a full lowercase Git SHA")
    return tag_match.group("version"), {
        "repository": source_repository,
        "commit": source_sha,
    }


def _state_schema_versions(path: Path) -> tuple[int, int]:
    try:
        tree = ast.parse(_safe_read(path).decode("utf-8"), filename=str(path))
    except (UnicodeDecodeError, SyntaxError) as exc:
        raise ImageReleaseError("state schema source cannot be inspected") from exc
    values: dict[str, int] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if (
            isinstance(target, ast.Name)
            and target.id in {"CURRENT_SCHEMA_VERSION", "MINIMUM_READABLE_SCHEMA_VERSION"}
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, int)
            and not isinstance(node.value.value, bool)
        ):
            values[target.id] = node.value.value
    if set(values) != {"CURRENT_SCHEMA_VERSION", "MINIMUM_READABLE_SCHEMA_VERSION"}:
        raise ImageReleaseError("state schema version constants are incomplete")
    current = values["CURRENT_SCHEMA_VERSION"]
    minimum = values["MINIMUM_READABLE_SCHEMA_VERSION"]
    if not 0 <= minimum <= current <= 2**31 - 1:
        raise ImageReleaseError("state schema version constants are invalid")
    return current, minimum


def _validate_spdx_sbom(path: Path, platform: str) -> tuple[bytes, dict[str, Any]]:
    data = _safe_read(path)
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ImageReleaseError(f"{platform} SBOM is not valid JSON") from exc
    packages = value.get("packages") if isinstance(value, dict) else None
    if (
        not isinstance(value, dict)
        or value.get("spdxVersion") not in {"SPDX-2.2", "SPDX-2.3"}
        or value.get("SPDXID") != "SPDXRef-DOCUMENT"
        or value.get("dataLicense") != "CC0-1.0"
        or not isinstance(value.get("creationInfo"), dict)
        or not isinstance(packages, list)
        or not 1 <= len(packages) <= 100_000
    ):
        raise ImageReleaseError(f"{platform} SBOM is not a supported SPDX document")
    return data, {
        "format": value["spdxVersion"],
        "sha256": _sha256(data),
        "file": path.name,
        "packageCount": len(packages),
    }


def _validate_python_dependency_locks(
    *,
    build_lock_path: Path,
    runtime_lock_path: Path,
    metadata_path: Path,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    if (
        build_lock_path.name != BUILD_DEPENDENCY_LOCK
        or runtime_lock_path.name != RUNTIME_DEPENDENCY_LOCK
        or metadata_path.name != DEPENDENCY_LOCK_METADATA
    ):
        raise ImageReleaseError("Python dependency release filenames are not canonical")
    data = {
        BUILD_DEPENDENCY_LOCK: _safe_read(build_lock_path),
        RUNTIME_DEPENDENCY_LOCK: _safe_read(runtime_lock_path),
        DEPENDENCY_LOCK_METADATA: _safe_read(metadata_path),
    }
    package_counts = {
        filename: _validate_requirement_lock(data[filename], filename)
        for filename in (BUILD_DEPENDENCY_LOCK, RUNTIME_DEPENDENCY_LOCK)
    }
    metadata = _decode_json_object(
        data[DEPENDENCY_LOCK_METADATA], "Python dependency lock metadata"
    )
    if set(metadata) != {
        "schemaVersion",
        "kind",
        "generator",
        "pythonVersion",
        "platforms",
        "onlyBinary",
        "inputs",
        "buildLock",
        "runtimeLock",
    }:
        raise ImageReleaseError("Python dependency lock metadata schema is invalid")
    generator = metadata.get("generator")
    inputs = metadata.get("inputs")
    if (
        metadata.get("schemaVersion") != 1
        or metadata.get("kind") != "echo-appliance-python-dependency-lock"
        or metadata.get("pythonVersion") != "3.12"
        or metadata.get("platforms") != list(EXPECTED_PLATFORMS)
        or metadata.get("onlyBinary") is not True
        or generator != {"name": "uv", "version": EXPECTED_UV_VERSION}
        or not isinstance(inputs, dict)
    ):
        raise ImageReleaseError("Python dependency lock contract is invalid")
    if set(inputs) != {
        "osProject",
        "agentProject",
        "agentExtras",
        "buildRequirementsSha256",
        "runtimeRequirementsSha256",
    }:
        raise ImageReleaseError("Python dependency lock input schema is invalid")
    os_project = inputs.get("osProject")
    agent_project = inputs.get("agentProject")
    if (
        not isinstance(os_project, dict)
        or not isinstance(agent_project, dict)
        or set(os_project) != {"name", "file", "sha256"}
        or set(agent_project) != {"name", "file", "sha256"}
        or os_project.get("name") != "echo-os"
        or os_project.get("file") != "pyproject.toml"
        or agent_project.get("file") != "pyproject.toml"
        or not isinstance(agent_project.get("name"), str)
        or not agent_project["name"]
        or inputs.get("agentExtras") != list(EXPECTED_AGENT_EXTRAS)
        or any(
            RAW_DIGEST_PATTERN.fullmatch(str(value or "")) is None
            for value in (
                os_project.get("sha256"),
                agent_project.get("sha256"),
                inputs.get("buildRequirementsSha256"),
                inputs.get("runtimeRequirementsSha256"),
            )
        )
    ):
        raise ImageReleaseError("Python dependency lock source identity is invalid")

    records: dict[str, dict[str, Any]] = {}
    for field, filename in (
        ("buildLock", BUILD_DEPENDENCY_LOCK),
        ("runtimeLock", RUNTIME_DEPENDENCY_LOCK),
    ):
        lock = metadata.get(field)
        if (
            not isinstance(lock, dict)
            or set(lock) != {"file", "sha256", "packageCount"}
            or lock.get("file") != filename
            or lock.get("sha256") != _sha256(data[filename])
            or isinstance(lock.get("packageCount"), bool)
            or not isinstance(lock.get("packageCount"), int)
            or not 1 <= lock["packageCount"] <= 10000
            or lock["packageCount"] != package_counts[filename]
        ):
            raise ImageReleaseError(f"Python dependency metadata does not match {filename}")
        records[field] = {
            "file": filename,
            "sha256": lock["sha256"],
            "packageCount": lock["packageCount"],
        }

    return {
        "metadataFile": DEPENDENCY_LOCK_METADATA,
        "metadataSha256": _sha256(data[DEPENDENCY_LOCK_METADATA]),
        "generator": generator,
        "pythonVersion": "3.12",
        "platforms": list(EXPECTED_PLATFORMS),
        "onlyBinary": True,
        "inputs": inputs,
        **records,
    }, data


def _validate_requirement_lock(data: bytes, filename: str) -> int:
    if not data or not data.endswith(b"\n"):
        raise ImageReleaseError(f"Python dependency lock is empty or truncated: {filename}")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ImageReleaseError(f"Python dependency lock is not UTF-8: {filename}") from exc
    lowered = f" {text.lower()} "
    if any(fragment in lowered for fragment in FORBIDDEN_LOCK_TEXT):
        raise ImageReleaseError(f"Python dependency lock has a mutable source: {filename}")

    package_names: set[str] = set()
    current_package = False
    current_hashes = 0
    package_count = 0
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        if line.startswith(" "):
            if not current_package or LOCK_HASH_PATTERN.fullmatch(line) is None:
                raise ImageReleaseError(f"Python dependency lock has an unsafe line: {filename}")
            current_hashes += 1
            continue
        if current_package and current_hashes == 0:
            raise ImageReleaseError(f"Python dependency lock omits a hash: {filename}")
        match = LOCK_PIN_PATTERN.fullmatch(line)
        if match is None:
            raise ImageReleaseError(f"Python dependency lock is not exactly pinned: {filename}")
        canonical_name = re.sub(r"[-_.]+", "-", match.group("name")).lower()
        if canonical_name in package_names:
            raise ImageReleaseError(f"Python dependency lock repeats a package: {filename}")
        package_names.add(canonical_name)
        package_count += 1
        current_package = True
        current_hashes = 0
    if current_package and current_hashes == 0:
        raise ImageReleaseError(f"Python dependency lock omits a hash: {filename}")
    if package_count == 0:
        raise ImageReleaseError(f"Python dependency lock contains no packages: {filename}")
    return package_count


def _canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        + b"\n"
    )


def _atomic_write(path: Path, data: bytes, *, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink() or not path.parent.is_dir() or path.is_symlink():
        raise ImageReleaseError(f"release output path is unsafe: {path}")
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


def create_release(
    *,
    image: str,
    digest: str,
    release_tag: str,
    source_repository: str,
    source_ref: str,
    source_sha: str,
    index_path: Path,
    state_schema_path: Path,
    sbom_paths: dict[str, Path],
    build_lock_path: Path,
    runtime_lock_path: Path,
    dependency_lock_metadata_path: Path,
    operations_bundle_path: Path,
    operations_checksum_path: Path,
    operations_sbom_path: Path,
    operations_verifier_path: Path,
    output_path: Path,
    env_output_path: Path,
) -> dict[str, Any]:
    if IMAGE_NAME_PATTERN.fullmatch(image) is None:
        raise ImageReleaseError("release image must be a lowercase ghcr.io repository")
    if DIGEST_PATTERN.fullmatch(digest) is None:
        raise ImageReleaseError("release image digest is invalid")
    version, agent_source = preflight_release_source(
        release_tag=release_tag,
        source_repository=source_repository,
        source_ref=source_ref,
        source_sha=source_sha,
    )

    index_data = _safe_read(index_path)
    if f"sha256:{_sha256(index_data)}" != digest:
        raise ImageReleaseError("registry image index bytes do not match the release digest")
    index = _decode_json_object(index_data, "registry image index")
    platforms, attestation_count = _validate_index(index)
    current_schema, minimum_schema = _state_schema_versions(state_schema_path)
    if set(sbom_paths) != set(EXPECTED_PLATFORMS):
        raise ImageReleaseError("release requires amd64 and arm64 SPDX SBOM files")
    sbom_data: dict[str, bytes] = {}
    sbom_records: dict[str, dict[str, Any]] = {}
    for platform in EXPECTED_PLATFORMS:
        data, record = _validate_spdx_sbom(sbom_paths[platform], platform)
        sbom_data[platform] = data
        sbom_records[platform] = record
    if len({record["file"] for record in sbom_records.values()}) != len(EXPECTED_PLATFORMS):
        raise ImageReleaseError("platform SBOM filenames must be distinct")
    dependency_record, dependency_data = _validate_python_dependency_locks(
        build_lock_path=build_lock_path,
        runtime_lock_path=runtime_lock_path,
        metadata_path=dependency_lock_metadata_path,
    )
    immutable_reference = f"{image}@{digest}"
    try:
        operations_record = operations_bundle.verify_release_artifacts(
            operations_bundle_path,
            operations_checksum_path,
            operations_sbom_path,
            expected_image_reference=immutable_reference,
        )
    except operations_bundle.OperationsBundleError as exc:
        raise ImageReleaseError(f"operations bundle is invalid: {exc}") from exc
    operations_verifier_data = _safe_read(operations_verifier_path)
    operations_verifier_sha256 = _sha256(operations_verifier_data)
    if operations_verifier_path.name != "operations_bundle.py":
        raise ImageReleaseError("operations bundle verifier filename is invalid")
    if operations_record["embeddedVerifierSha256"] != operations_verifier_sha256:
        raise ImageReleaseError(
            "published operations bundle verifier differs from the embedded verifier"
        )
    manifest = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "echo-appliance-container-release",
        "createdAt": _utc_timestamp(),
        "release": {
            "tag": release_tag,
            "version": version,
        },
        "source": {
            "repository": source_repository,
            "ref": source_ref,
            "commit": source_sha,
        },
        "agentSource": agent_source,
        "image": {
            "name": image,
            "indexDigest": digest,
            "indexFile": index_path.name,
            "immutableReference": immutable_reference,
            "platformDigests": {platform: platforms[platform] for platform in EXPECTED_PLATFORMS},
        },
        "stateSchema": {
            "currentVersion": current_schema,
            "minimumReadableVersion": minimum_schema,
            "automaticMigrationAllowed": False,
        },
        "attestations": {
            "buildkitProvenanceMode": "max",
            "buildkitSbom": True,
            "registryAttestationManifestCount": attestation_count,
            "githubOidcProvenanceRequired": True,
        },
        "sboms": sbom_records,
        "pythonDependencies": dependency_record,
        "operationsBundle": {
            "artifactId": operations_record["artifactId"],
            "archive": operations_bundle_path.name,
            "sha256": operations_record["archiveSha256"],
            "fileCount": operations_record["fileCount"],
            "architectures": operations_record["architectures"],
            "imageReference": operations_record["imageReference"],
            "checksum": {
                "file": operations_checksum_path.name,
                "sha256": operations_record["checksumSha256"],
            },
            "spdx": {
                "file": operations_sbom_path.name,
                "format": "SPDX-2.3",
                "sha256": operations_record["sbomSha256"],
            },
            "verifier": {
                "file": operations_verifier_path.name,
                "sha256": operations_verifier_sha256,
            },
            "verifyCommand": (
                f"python3 {operations_verifier_path.name} verify {operations_bundle_path.name}"
            ),
            "extractCommand": (
                f"sudo python3 {operations_verifier_path.name} extract "
                f"{operations_bundle_path.name} --destination /opt/echo-os "
                "--require-root-owner"
            ),
            "installCommand": "./install-appliance.sh",
        },
        "upgrade": {
            "command": f"./upgrade-appliance.sh {immutable_reference}",
            "requiresVerifiedStateBackup": True,
        },
        "recovery": {
            "command": "./restore-state.sh <external-verified.echo-backup>",
            "exactDigestConfirmationRequired": True,
            "stagedValidationRequired": True,
            "previousStateRetained": True,
            "automaticDirectoryRollback": True,
        },
    }
    manifest_data = _canonical_json(manifest)
    _atomic_write(output_path, manifest_data)
    env_data = f"ECHO_OS_IMAGE={immutable_reference}\n".encode("ascii")
    _atomic_write(env_output_path, env_data)
    checksum_path = output_path.with_name(f"{output_path.name}.sha256")
    checksum_data = (
        f"{_sha256(manifest_data)}  {output_path.name}\n"
        f"{_sha256(env_data)}  {env_output_path.name}\n"
        f"{_sha256(index_data)}  {index_path.name}\n"
        + "".join(
            f"{_sha256(sbom_data[platform])}  {sbom_paths[platform].name}\n"
            for platform in EXPECTED_PLATFORMS
        )
        + "".join(
            f"{_sha256(dependency_data[filename])}  {filename}\n"
            for filename in (
                BUILD_DEPENDENCY_LOCK,
                RUNTIME_DEPENDENCY_LOCK,
                DEPENDENCY_LOCK_METADATA,
            )
        )
        + f"{operations_record['archiveSha256']}  {operations_bundle_path.name}\n"
        + f"{operations_record['checksumSha256']}  {operations_checksum_path.name}\n"
        + f"{operations_record['sbomSha256']}  {operations_sbom_path.name}\n"
        + f"{operations_verifier_sha256}  {operations_verifier_path.name}\n"
    ).encode("ascii")
    _atomic_write(checksum_path, checksum_data)
    return {
        "verified": True,
        "releaseTag": release_tag,
        "version": version,
        "immutableReference": immutable_reference,
        "platforms": list(EXPECTED_PLATFORMS),
        "attestationManifestCount": attestation_count,
        "sboms": {platform: str(sbom_paths[platform].resolve()) for platform in EXPECTED_PLATFORMS},
        "pythonDependencies": {
            "buildLock": str(build_lock_path.resolve()),
            "runtimeLock": str(runtime_lock_path.resolve()),
            "metadata": str(dependency_lock_metadata_path.resolve()),
        },
        "operationsBundle": {
            "archive": str(operations_bundle_path.resolve()),
            "checksum": str(operations_checksum_path.resolve()),
            "sbom": str(operations_sbom_path.resolve()),
            "verifier": str(operations_verifier_path.resolve()),
        },
        "manifest": str(output_path.resolve()),
        "environment": str(env_output_path.resolve()),
        "checksums": str(checksum_path.resolve()),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--digest", required=True)
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--source-repository", required=True)
    parser.add_argument("--source-ref", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--index-json", type=Path, required=True)
    parser.add_argument(
        "--state-schema",
        type=Path,
        default=Path("appliance/state_schema.py"),
    )
    parser.add_argument("--sbom-amd64", type=Path, required=True)
    parser.add_argument("--sbom-arm64", type=Path, required=True)
    parser.add_argument("--build-lock", type=Path, required=True)
    parser.add_argument("--runtime-lock", type=Path, required=True)
    parser.add_argument("--dependency-lock-metadata", type=Path, required=True)
    parser.add_argument("--operations-bundle", type=Path, required=True)
    parser.add_argument("--operations-checksum", type=Path, required=True)
    parser.add_argument("--operations-sbom", type=Path, required=True)
    parser.add_argument("--operations-verifier", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("dist/echo-appliance-release.json"),
    )
    parser.add_argument(
        "--env-output",
        type=Path,
        default=Path("dist/echo-release.env"),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = create_release(
            image=args.image,
            digest=args.digest,
            release_tag=args.release_tag,
            source_repository=args.source_repository,
            source_ref=args.source_ref,
            source_sha=args.source_sha,
            index_path=args.index_json,
            state_schema_path=args.state_schema,
            sbom_paths={
                "linux/amd64": args.sbom_amd64,
                "linux/arm64": args.sbom_arm64,
            },
            build_lock_path=args.build_lock,
            runtime_lock_path=args.runtime_lock,
            dependency_lock_metadata_path=args.dependency_lock_metadata,
            operations_bundle_path=args.operations_bundle,
            operations_checksum_path=args.operations_checksum,
            operations_sbom_path=args.operations_sbom,
            operations_verifier_path=args.operations_verifier,
            output_path=args.output,
            env_output_path=args.env_output,
        )
    except (ImageReleaseError, OSError) as exc:
        print(f"Echo appliance release failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
