from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import tarfile
from pathlib import Path

import pytest

_REPOSITORY = Path(__file__).resolve().parents[2]
_SCRIPT = _REPOSITORY / "deploy" / "omv" / "host_bundle.py"
_SPEC = importlib.util.spec_from_file_location("echo_omv_host_bundle", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
bundle = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = bundle
_SPEC.loader.exec_module(bundle)


def test_host_bundle_is_deterministic_and_self_verifying(tmp_path: Path) -> None:
    first = bundle.build(_REPOSITORY, tmp_path / "first")
    second = bundle.build(_REPOSITORY, tmp_path / "second")
    first_archive = Path(first["archive"])
    second_archive = Path(second["archive"])

    assert first["verified"] is True
    assert first["architectures"] == ["amd64", "arm64"]
    assert first["artifactId"] == second["artifactId"]
    assert first["archiveSha256"] == second["archiveSha256"]
    assert first_archive.read_bytes() == second_archive.read_bytes()
    assert Path(first["checksum"]).read_text() == (
        f"{first['archiveSha256']}  {first_archive.name}\n"
    )
    first_sbom = json.loads(Path(first["sbom"]).read_text())
    assert first_sbom["spdxVersion"] == "SPDX-2.3"
    assert first_sbom["packages"][0]["versionInfo"] == first["artifactId"]
    assert {item["fileName"][2:] for item in first_sbom["files"]} == set(bundle.SOURCE_MODES)
    assert bundle.verify(first_archive)["fileCount"] == len(bundle.SOURCE_MODES)


def test_host_bundle_contains_only_fixed_files_and_modes(tmp_path: Path) -> None:
    report = bundle.build(_REPOSITORY, tmp_path)
    root, files = bundle._read_archive_members(Path(report["archive"]))

    assert root == f"echo-omv-host-{report['artifactId']}"
    assert set(files) == {
        *bundle.SOURCE_MODES,
        bundle.MANIFEST_NAME,
        bundle.CHECKSUMS_NAME,
    }
    assert {
        relative: mode
        for relative, (_data, mode) in files.items()
        if relative in bundle.SOURCE_MODES
    } == bundle.SOURCE_MODES
    assert files["deploy/omv/echo_omv_host.py"][1] == 0o755
    manifest = json.loads(files[bundle.MANIFEST_NAME][0])
    assert manifest["artifact"]["controlledWrites"] == list(bundle.CONTROLLED_WRITES)


def test_host_bundle_rejects_payload_tampering(tmp_path: Path) -> None:
    report = bundle.build(_REPOSITORY, tmp_path)
    archive_path = Path(report["archive"])
    root, files = bundle._read_archive_members(archive_path)
    target = "appliance/omv_bridge.py"
    original, mode = files[target]
    files[target] = (original + b"\n# tampered\n", mode)
    tampered = tmp_path / "tampered.tar.gz"
    bundle._write_archive(tampered, root, files)

    with pytest.raises(bundle.BundleError, match="integrity checks"):
        bundle.verify(tampered)


def test_host_bundle_rejects_links_without_extracting(tmp_path: Path) -> None:
    archive_path = tmp_path / "link.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        root = tarfile.TarInfo("echo-omv-host-0000000000000000/")
        root.type = tarfile.DIRTYPE
        root.mode = 0o755
        archive.addfile(root)
        link = tarfile.TarInfo("echo-omv-host-0000000000000000/appliance/omv_bridge.py")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        archive.addfile(link)

    with pytest.raises(bundle.BundleError, match="link or device"):
        bundle.verify(archive_path)


def test_host_bundle_rejects_symlinked_source_input(tmp_path: Path) -> None:
    source = tmp_path / "source"
    for relative in bundle.SOURCE_MODES:
        target = source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((_REPOSITORY / relative).read_bytes())
    installer = source / "deploy/omv/echo_omv_host.py"
    installer.unlink()
    os.symlink(_REPOSITORY / "deploy/omv/echo_omv_host.py", installer)

    with pytest.raises(bundle.BundleError, match="safely read"):
        bundle.build(source, tmp_path / "output")


def test_host_bundle_rejects_invalid_manifest_shape() -> None:
    with pytest.raises(bundle.BundleError, match="manifest schema"):
        bundle._validated_manifest(b"[]")


def test_host_bundle_rejects_sbom_that_does_not_match_payload() -> None:
    payload = bundle._source_payload(_REPOSITORY)
    artifact_id = bundle._artifact_id(payload)
    sbom = bundle._sbom(payload, artifact_id)
    sbom["files"][0]["checksums"][0]["checksumValue"] = "0" * 64

    with pytest.raises(bundle.BundleError, match="does not match"):
        bundle._validate_sbom(bundle._canonical_json(sbom), payload, artifact_id)


def test_host_bundle_rejects_oversized_member_before_reading(tmp_path: Path) -> None:
    archive_path = tmp_path / "oversized.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        item = tarfile.TarInfo("echo-omv-host-0000000000000000/oversized")
        item.mode = 0o644
        item.size = bundle.MAX_SOURCE_BYTES + 1
        archive.addfile(item, io.BytesIO(b"x" * item.size))

    with pytest.raises(bundle.BundleError, match="unsafe file"):
        bundle.verify(archive_path)
