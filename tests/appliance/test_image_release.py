from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import stat
import sys
from pathlib import Path

import pytest

_REPOSITORY = Path(__file__).resolve().parents[2]
_SCRIPT = _REPOSITORY / "deploy" / "appliance" / "image_release.py"
_SPEC = importlib.util.spec_from_file_location("echo_appliance_image_release", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
release = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = release
_SPEC.loader.exec_module(release)

AMD64_DIGEST = f"sha256:{'a' * 64}"
ARM64_DIGEST = f"sha256:{'b' * 64}"


def _descriptor(
    operating_system: str,
    architecture: str,
    digest: str,
    *,
    reference: str | None = None,
) -> dict[str, object]:
    descriptor: dict[str, object] = {
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "digest": digest,
        "size": 1024,
        "platform": {"os": operating_system, "architecture": architecture},
    }
    if reference is not None:
        descriptor["annotations"] = {
            "vnd.docker.reference.type": "attestation-manifest",
            "vnd.docker.reference.digest": reference,
        }
    return descriptor


def _valid_index() -> dict[str, object]:
    return {
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.index.v1+json",
        "manifests": [
            _descriptor("linux", "amd64", AMD64_DIGEST),
            _descriptor("linux", "arm64", ARM64_DIGEST),
            _descriptor("unknown", "unknown", f"sha256:{'c' * 64}", reference=AMD64_DIGEST),
            _descriptor("unknown", "unknown", f"sha256:{'d' * 64}", reference=ARM64_DIGEST),
        ],
    }


def _write_json(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _valid_sbom(name: str) -> dict[str, object]:
    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": name,
        "documentNamespace": f"https://example.test/{name}",
        "creationInfo": {
            "created": "2026-08-26T00:00:00Z",
            "creators": ["Tool: test"],
        },
        "packages": [
            {
                "name": "python",
                "SPDXID": "SPDXRef-Package",
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": False,
                "licenseConcluded": "NOASSERTION",
                "licenseDeclared": "NOASSERTION",
                "copyrightText": "NOASSERTION",
            }
        ],
    }


def _valid_dependency_locks(tmp_path: Path) -> tuple[Path, Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    build = tmp_path / "build-requirements.lock"
    runtime = tmp_path / "runtime-requirements.lock"
    metadata = tmp_path / "python-dependency-lock.json"
    build.write_text("setuptools==70.0.0 \\\n    --hash=sha256:" + "a" * 64 + "\n")
    runtime.write_text("pydantic==2.0.0 \\\n    --hash=sha256:" + "b" * 64 + "\n")
    _write_json(
        metadata,
        {
            "schemaVersion": 1,
            "kind": "echo-appliance-python-dependency-lock",
            "generator": {"name": "uv", "version": "0.11.25"},
            "pythonVersion": "3.12",
            "platforms": ["linux/amd64", "linux/arm64"],
            "onlyBinary": True,
            "inputs": {
                "osProject": {
                    "name": "echo-os",
                    "file": "pyproject.toml",
                    "sha256": "c" * 64,
                },
                "agentProject": {
                    "name": "echo-agent-runtime",
                    "file": "pyproject.toml",
                    "sha256": "d" * 64,
                },
                "agentExtras": ["serve", "tracing", "web", "local-auth", "video"],
                "buildRequirementsSha256": "e" * 64,
                "runtimeRequirementsSha256": "f" * 64,
            },
            "buildLock": {
                "file": build.name,
                "sha256": hashlib.sha256(build.read_bytes()).hexdigest(),
                "packageCount": 1,
            },
            "runtimeLock": {
                "file": runtime.name,
                "sha256": hashlib.sha256(runtime.read_bytes()).hexdigest(),
                "packageCount": 1,
            },
        },
    )
    return build, runtime, metadata


def _create(tmp_path: Path, **overrides):
    index = _write_json(tmp_path / "index.json", _valid_index())
    amd64_sbom = _write_json(tmp_path / "amd64.spdx.json", _valid_sbom("echo-amd64"))
    arm64_sbom = _write_json(tmp_path / "arm64.spdx.json", _valid_sbom("echo-arm64"))
    output = tmp_path / "echo-appliance-release.json"
    environment = tmp_path / "echo-release.env"
    build_lock, runtime_lock, dependency_metadata = _valid_dependency_locks(
        tmp_path / "default-dependencies"
    )
    options = {
        "image": "ghcr.io/echo-os/echo-os",
        "digest": f"sha256:{hashlib.sha256(index.read_bytes()).hexdigest()}",
        "release_tag": "echo-appliance-v1.2.3",
        "source_repository": "echo-os/echo-os",
        "source_ref": "refs/tags/echo-appliance-v1.2.3",
        "source_sha": "e" * 40,
        "index_path": index,
        "state_schema_path": _REPOSITORY / "appliance/state_schema.py",
        "sbom_paths": {
            "linux/amd64": amd64_sbom,
            "linux/arm64": arm64_sbom,
        },
        "build_lock_path": build_lock,
        "runtime_lock_path": runtime_lock,
        "dependency_lock_metadata_path": dependency_metadata,
        "output_path": output,
        "env_output_path": environment,
    }
    options.update(overrides)
    if "index_path" in overrides and "digest" not in overrides:
        selected_index = Path(options["index_path"])
        options["digest"] = f"sha256:{hashlib.sha256(selected_index.read_bytes()).hexdigest()}"
    if "operations_bundle_path" not in overrides:
        operations = release.operations_bundle.build(
            _REPOSITORY,
            tmp_path / "operations",
            f"{options['image']}@{options['digest']}",
        )
        options["operations_bundle_path"] = Path(operations["archive"])
        options["operations_checksum_path"] = Path(operations["checksum"])
        options["operations_sbom_path"] = Path(operations["sbom"])
        options.setdefault(
            "operations_verifier_path",
            _REPOSITORY / "deploy/appliance/operations_bundle.py",
        )
    return release.create_release(**options), output, environment


def test_release_manifest_binds_two_platforms_agent_and_state_schema(tmp_path: Path) -> None:
    report, output, environment = _create(tmp_path)

    assert report["verified"] is True
    assert report["platforms"] == ["linux/amd64", "linux/arm64"]
    manifest = json.loads(output.read_text())
    index_digest = manifest["image"]["indexDigest"]
    assert report["immutableReference"] == f"ghcr.io/echo-os/echo-os@{index_digest}"
    assert manifest["image"] == {
        "name": "ghcr.io/echo-os/echo-os",
        "indexDigest": index_digest,
        "indexFile": "index.json",
        "immutableReference": f"ghcr.io/echo-os/echo-os@{index_digest}",
        "platformDigests": {
            "linux/amd64": AMD64_DIGEST,
            "linux/arm64": ARM64_DIGEST,
        },
    }
    assert manifest["agentSource"] == {
        "repository": manifest["source"]["repository"],
        "commit": manifest["source"]["commit"],
    }
    assert manifest["stateSchema"] == {
        "currentVersion": 2,
        "minimumReadableVersion": 0,
        "automaticMigrationAllowed": False,
    }
    assert manifest["recovery"] == {
        "command": "./restore-state.sh <external-verified.echo-backup>",
        "exactDigestConfirmationRequired": True,
        "stagedValidationRequired": True,
        "previousStateRetained": True,
        "automaticDirectoryRollback": True,
    }
    assert manifest["attestations"]["registryAttestationManifestCount"] == 2
    assert manifest["sboms"] == {
        "linux/amd64": {
            "file": "amd64.spdx.json",
            "format": "SPDX-2.3",
            "packageCount": 1,
            "sha256": manifest["sboms"]["linux/amd64"]["sha256"],
        },
        "linux/arm64": {
            "file": "arm64.spdx.json",
            "format": "SPDX-2.3",
            "packageCount": 1,
            "sha256": manifest["sboms"]["linux/arm64"]["sha256"],
        },
    }
    assert all(len(record["sha256"]) == 64 for record in manifest["sboms"].values())
    assert manifest["pythonDependencies"]["generator"] == {
        "name": "uv",
        "version": "0.11.25",
    }
    assert manifest["pythonDependencies"]["platforms"] == [
        "linux/amd64",
        "linux/arm64",
    ]
    assert manifest["pythonDependencies"]["onlyBinary"] is True
    assert manifest["pythonDependencies"]["buildLock"]["packageCount"] == 1
    assert manifest["pythonDependencies"]["runtimeLock"]["packageCount"] == 1
    operations = manifest["operationsBundle"]
    assert operations["archive"] == "echo-appliance-operations.tar.gz"
    assert operations["architectures"] == ["amd64", "arm64"]
    assert operations["imageReference"] == manifest["image"]["immutableReference"]
    assert operations["fileCount"] == len(release.operations_bundle.PAYLOAD_MODES)
    assert operations["checksum"]["file"] == ("echo-appliance-operations.tar.gz.sha256")
    assert operations["spdx"]["format"] == "SPDX-2.3"
    assert operations["verifier"]["file"] == "operations_bundle.py"
    assert operations["verifyCommand"].startswith("python3 operations_bundle.py verify")
    assert operations["extractCommand"] == (
        "sudo python3 operations_bundle.py extract echo-appliance-operations.tar.gz "
        "--destination /opt/echo-os --require-root-owner"
    )
    assert operations["installCommand"] == "./install-appliance.sh"
    assert environment.read_text() == (f"ECHO_OS_IMAGE=ghcr.io/echo-os/echo-os@{index_digest}\n")
    assert stat.S_IMODE(output.stat().st_mode) == 0o644
    assert stat.S_IMODE(environment.stat().st_mode) == 0o644
    checksums = output.with_name(f"{output.name}.sha256")
    assert checksums.exists()
    checksum_lines = checksums.read_text().splitlines()
    assert len(checksum_lines) == 12
    assert checksum_lines[2].endswith("  index.json")
    assert checksum_lines[3].endswith("  amd64.spdx.json")
    assert checksum_lines[4].endswith("  arm64.spdx.json")
    assert checksum_lines[5].endswith("  build-requirements.lock")
    assert checksum_lines[6].endswith("  runtime-requirements.lock")
    assert checksum_lines[7].endswith("  python-dependency-lock.json")
    assert checksum_lines[8].endswith("  echo-appliance-operations.tar.gz")
    assert checksum_lines[9].endswith("  echo-appliance-operations.tar.gz.sha256")
    assert checksum_lines[10].endswith("  echo-appliance-operations.spdx.json")
    assert checksum_lines[11].endswith("  operations_bundle.py")


def test_release_rejects_missing_arm64_image(tmp_path: Path) -> None:
    index = _valid_index()
    manifests = index["manifests"]
    assert isinstance(manifests, list)
    manifests.pop(1)
    manifests.append(
        _descriptor("unknown", "unknown", f"sha256:{'1' * 64}", reference=AMD64_DIGEST)
    )
    index_path = _write_json(tmp_path / "missing-arm64.json", index)

    with pytest.raises(release.ImageReleaseError, match="amd64 and arm64"):
        _create(tmp_path, index_path=index_path)


def test_release_rejects_platform_without_attestation(tmp_path: Path) -> None:
    index = _valid_index()
    manifests = index["manifests"]
    assert isinstance(manifests, list)
    manifests[-1] = _descriptor(
        "unknown",
        "unknown",
        f"sha256:{'2' * 64}",
        reference=AMD64_DIGEST,
    )
    index_path = _write_json(tmp_path / "missing-attestation.json", index)

    with pytest.raises(release.ImageReleaseError, match="every platform"):
        _create(tmp_path, index_path=index_path)


def test_release_rejects_tag_and_source_ref_mismatch(tmp_path: Path) -> None:
    with pytest.raises(release.ImageReleaseError, match="source ref"):
        _create(tmp_path, source_ref="refs/tags/echo-appliance-v1.2.4")


def test_release_rejects_index_bytes_that_do_not_match_digest(tmp_path: Path) -> None:
    with pytest.raises(release.ImageReleaseError, match="index bytes"):
        _create(tmp_path, digest=f"sha256:{'f' * 64}")


def test_release_rejects_incomplete_state_schema_contract(tmp_path: Path) -> None:
    state_schema = tmp_path / "state_schema.py"
    state_schema.write_text("CURRENT_SCHEMA_VERSION = 1\n")

    with pytest.raises(release.ImageReleaseError, match="constants are incomplete"):
        _create(tmp_path, state_schema_path=state_schema)


def test_release_rejects_invalid_platform_sbom(tmp_path: Path) -> None:
    invalid = _write_json(tmp_path / "invalid.spdx.json", {"spdxVersion": "SPDX-2.3"})

    with pytest.raises(release.ImageReleaseError, match="linux/arm64 SBOM"):
        _create(
            tmp_path,
            sbom_paths={
                "linux/amd64": _write_json(tmp_path / "valid.spdx.json", _valid_sbom("echo-amd64")),
                "linux/arm64": invalid,
            },
        )


def test_release_rejects_dependency_lock_hash_mismatch(tmp_path: Path) -> None:
    build, runtime, metadata = _valid_dependency_locks(tmp_path)
    runtime.write_text(runtime.read_text() + "changed\n")

    with pytest.raises(release.ImageReleaseError, match="runtime-requirements.lock"):
        _create(
            tmp_path,
            build_lock_path=build,
            runtime_lock_path=runtime,
            dependency_lock_metadata_path=metadata,
        )


def test_release_rejects_mutable_dependency_source(tmp_path: Path) -> None:
    build, runtime, metadata = _valid_dependency_locks(tmp_path)
    runtime.write_text("demo @ https://packages.example/demo.whl\n")

    with pytest.raises(release.ImageReleaseError, match="mutable source"):
        _create(
            tmp_path,
            build_lock_path=build,
            runtime_lock_path=runtime,
            dependency_lock_metadata_path=metadata,
        )


def test_release_refuses_symlink_output(tmp_path: Path) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text("preserve")
    output = tmp_path / "echo-appliance-release.json"
    os.symlink(outside, output)

    with pytest.raises(release.ImageReleaseError, match="output path is unsafe"):
        _create(tmp_path, output_path=output)

    assert outside.read_text() == "preserve"


def test_release_rejects_operations_bundle_for_another_image(tmp_path: Path) -> None:
    operations = release.operations_bundle.build(
        _REPOSITORY,
        tmp_path / "wrong-operations",
        f"ghcr.io/echo-os/echo-os@sha256:{'9' * 64}",
    )

    with pytest.raises(release.ImageReleaseError, match="operations bundle image"):
        _create(
            tmp_path,
            operations_bundle_path=Path(operations["archive"]),
            operations_checksum_path=Path(operations["checksum"]),
            operations_sbom_path=Path(operations["sbom"]),
            operations_verifier_path=(_REPOSITORY / "deploy/appliance/operations_bundle.py"),
        )


def test_release_rejects_operations_verifier_different_from_archive(tmp_path: Path) -> None:
    verifier = tmp_path / "operations_bundle.py"
    verifier.write_text("print('different verifier')\n")

    with pytest.raises(release.ImageReleaseError, match="differs from the embedded verifier"):
        _create(tmp_path, operations_verifier_path=verifier)
