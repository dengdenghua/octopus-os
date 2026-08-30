from __future__ import annotations

import io
import json
import os
import stat
import tarfile
from pathlib import Path

import pytest

from deploy.appliance import operations_bundle as bundle

REPOSITORY = Path(__file__).resolve().parents[2]
IMAGE_REFERENCE = f"ghcr.io/echo-os/echo-os@sha256:{'a' * 64}"


def _built(tmp_path: Path) -> dict[str, object]:
    return bundle.build(REPOSITORY, tmp_path, IMAGE_REFERENCE)


def test_operations_bundle_is_deterministic_bound_and_self_verifying(tmp_path: Path) -> None:
    first = _built(tmp_path / "first")
    second = _built(tmp_path / "second")

    assert first["artifactId"] == second["artifactId"]
    assert first["archiveSha256"] == second["archiveSha256"]
    assert Path(first["archive"]).read_bytes() == Path(second["archive"]).read_bytes()
    assert first["imageReference"] == IMAGE_REFERENCE
    assert first["architectures"] == ["amd64", "arm64"]
    assert first["fileCount"] == len(bundle.PAYLOAD_MODES)
    assert first["embeddedVerifierSha256"] == bundle._sha256(
        (REPOSITORY / "deploy/appliance/operations_bundle.py").read_bytes()
    )
    verified = bundle.verify_release_artifacts(
        Path(first["archive"]),
        Path(first["checksum"]),
        Path(first["sbom"]),
        expected_image_reference=IMAGE_REFERENCE,
    )
    assert verified["artifactId"] == first["artifactId"]


def test_operations_bundle_has_fixed_inventory_modes_and_release_reference(tmp_path: Path) -> None:
    report = _built(tmp_path)
    root, files = bundle._read_archive(Path(report["archive"]))

    assert root == f"echo-appliance-operations-{report['artifactId']}"
    assert set(files) == {
        *bundle.PAYLOAD_MODES,
        bundle.MANIFEST_NAME,
        bundle.PAYLOAD_CHECKSUMS_NAME,
    }
    assert {name: mode for name, (_, mode) in files.items() if name in bundle.PAYLOAD_MODES} == (
        bundle.PAYLOAD_MODES
    )
    assert files["echo-release.env"] == (
        f"ECHO_OS_IMAGE={IMAGE_REFERENCE}\n".encode("ascii"),
        0o600,
    )
    manifest = json.loads(files[bundle.MANIFEST_NAME][0])
    assert manifest["artifact"]["entrypoints"]["install"] == "./install-appliance.sh"
    assert manifest["artifact"]["entrypoints"]["bareMetalRecoveryLab"] == (
        "./bare_metal_recovery_lab.py plan|run|verify"
    )
    assert manifest["artifact"]["entrypoints"]["deviceEnduranceLab"] == (
        "./device_endurance_lab.py plan|run"
    )
    assert manifest["artifact"]["entrypoints"]["hubLifecycleLab"] == (
        "./hub_lifecycle_lab.py plan|run|verify"
    )
    assert manifest["artifact"]["entrypoints"]["lanDiscoveryFunctionalLab"] == (
        "./lan_discovery_functional_lab.py plan|credentials|syncthing|home-assistant|verify"
    )
    assert manifest["artifact"]["entrypoints"]["paperlessFunctionalLab"] == (
        "./paperless_functional_lab.py plan|run|verify"
    )
    assert manifest["artifact"]["entrypoints"]["upgradeRecovery"] == (
        "./recover-appliance-upgrade.sh"
    )
    assert manifest["artifact"]["entrypoints"]["operationsSystemd"] == (
        "./operations_systemd.py plan|apply|remove-plan|remove"
    )
    assert manifest["artifact"]["entrypoints"]["operationsSystemdLab"] == (
        "./operations_systemd_lab.py plan|run"
    )
    assert manifest["artifact"]["entrypoints"]["powerStateRecoveryLab"] == (
        "./power_state_recovery_lab.py seed|plan|run|verify"
    )
    assert manifest["artifact"]["entrypoints"]["protocolInteroperabilityLab"] == (
        "./protocol_interoperability_lab.py plan|probe|permissions|quota|large-file|verify"
    )
    assert manifest["artifact"]["entrypoints"]["storageRecoveryLab"] == (
        "./storage_recovery_lab.py plan|run"
    )
    assert manifest["files"]["protocol_interoperability_lab.py"]["mode"] == "0755"
    assert manifest["files"]["bare_metal_recovery_lab.py"]["mode"] == "0755"
    assert manifest["files"]["power_state_recovery_lab.py"]["mode"] == "0755"
    assert manifest["files"]["device_endurance_lab.py"]["mode"] == "0755"
    assert manifest["files"]["hub_lifecycle_lab.py"]["mode"] == "0755"
    assert manifest["files"]["lan_discovery_functional_lab.py"]["mode"] == "0755"
    assert manifest["files"]["paperless_functional_lab.py"]["mode"] == "0755"
    assert manifest["files"]["recover-appliance-upgrade.sh"]["mode"] == "0755"
    assert manifest["files"]["storage_recovery_lab.py"]["mode"] == "0755"
    assert manifest["files"]["upgrade_transaction.py"]["mode"] == "0755"
    assert manifest["artifact"]["imageReference"] == IMAGE_REFERENCE


def test_operations_bundle_extracts_only_verified_files_and_refuses_replace(tmp_path: Path) -> None:
    report = _built(tmp_path / "build")
    destination = tmp_path / "install"
    extracted = bundle.extract(Path(report["archive"]), destination)
    root = Path(extracted["destination"])

    assert {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()} == {
        *bundle.PAYLOAD_MODES,
        bundle.MANIFEST_NAME,
        bundle.PAYLOAD_CHECKSUMS_NAME,
    }
    assert extracted["fileCount"] == len(bundle.PAYLOAD_MODES) + 2
    assert extracted["payloadFileCount"] == len(bundle.PAYLOAD_MODES)
    assert stat.S_IMODE((root / "echo-release.env").stat().st_mode) == 0o600
    assert stat.S_IMODE((root / "install-appliance.sh").stat().st_mode) == 0o755
    assert stat.S_IMODE((root / bundle.MANIFEST_NAME).stat().st_mode) == 0o644
    assert stat.S_IMODE((root / bundle.PAYLOAD_CHECKSUMS_NAME).stat().st_mode) == 0o644
    with pytest.raises(bundle.OperationsBundleError, match="already exists"):
        bundle.extract(Path(report["archive"]), destination)


def test_production_extraction_requires_root_before_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = _built(tmp_path / "build")
    destination = tmp_path / "production"
    monkeypatch.setattr(bundle.os, "geteuid", lambda: 1000)

    with pytest.raises(bundle.OperationsBundleError, match="requires root"):
        bundle.extract(
            Path(report["archive"]),
            destination,
            require_root_owner=True,
        )
    assert not destination.exists()


def test_operations_bundle_rejects_payload_tampering(tmp_path: Path) -> None:
    report = _built(tmp_path)
    archive = Path(report["archive"])
    root, files = bundle._read_archive(archive)
    files["docker-compose.yml"] = (b"tampered\n", 0o644)
    bundle._write_archive(archive, root, files)

    with pytest.raises(bundle.OperationsBundleError, match="integrity checks"):
        bundle.verify(archive)


def test_operations_bundle_rejects_link_and_traversal_members(tmp_path: Path) -> None:
    for name, member_factory, match in (
        (
            "link.tar.gz",
            lambda: _link_member("echo-appliance-operations-deadbeefdeadbeef/link"),
            "link or device",
        ),
        (
            "traversal.tar.gz",
            lambda: _file_member("../escape", b"bad"),
            "unsafe path",
        ),
    ):
        archive = tmp_path / name
        with tarfile.open(archive, "w:gz") as output:
            member, content = member_factory()
            output.addfile(member, content)
        with pytest.raises(bundle.OperationsBundleError, match=match):
            bundle.verify(archive)


def _link_member(name: str) -> tuple[tarfile.TarInfo, None]:
    member = tarfile.TarInfo(name)
    member.type = tarfile.SYMTYPE
    member.linkname = "/tmp/outside"
    return member, None


def _file_member(name: str, content: bytes) -> tuple[tarfile.TarInfo, io.BytesIO]:
    member = tarfile.TarInfo(name)
    member.size = len(content)
    member.mode = 0o644
    return member, io.BytesIO(content)


def test_operations_bundle_rejects_symlinked_source(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for _destination, (relative, _mode) in bundle.SOURCE_FILES.items():
        target = source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((REPOSITORY / relative).read_bytes())
    selected_source = next(iter(bundle.SOURCE_FILES.values()))[0]
    selected = source / selected_source
    selected.unlink()
    os.symlink(REPOSITORY / selected_source, selected)

    with pytest.raises(bundle.OperationsBundleError, match="cannot safely read"):
        bundle.build(source, tmp_path / "output", IMAGE_REFERENCE)


def test_operations_bundle_rejects_wrong_outer_checksum_and_sbom(tmp_path: Path) -> None:
    report = _built(tmp_path)
    checksum = Path(report["checksum"])
    checksum.write_text(f"{'0' * 64}  {bundle.ARCHIVE_NAME}\n")
    with pytest.raises(bundle.OperationsBundleError, match="outer checksum"):
        bundle.verify_release_artifacts(
            Path(report["archive"]),
            checksum,
            Path(report["sbom"]),
            expected_image_reference=IMAGE_REFERENCE,
        )

    report = _built(tmp_path / "rebuilt")
    sbom = Path(report["sbom"])
    value = json.loads(sbom.read_text())
    value["files"][0]["checksums"][0]["checksumValue"] = "0" * 64
    sbom.write_text(json.dumps(value))
    with pytest.raises(bundle.OperationsBundleError, match="does not match"):
        bundle.verify_release_artifacts(
            Path(report["archive"]),
            Path(report["checksum"]),
            sbom,
            expected_image_reference=IMAGE_REFERENCE,
        )
