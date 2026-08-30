from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from deploy.appliance import product_delivery_bundle as bundle

ROOT = Path(__file__).resolve().parents[2]
PHYSICAL_TEST = ROOT / "tests" / "appliance" / "test_physical_acceptance.py"
SPEC = importlib.util.spec_from_file_location("echo_product_bundle_physical_fixture", PHYSICAL_TEST)
assert SPEC is not None and SPEC.loader is not None
physical_fixture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(physical_fixture)


def _inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    candidate_index, evidence, keyring, _candidate = physical_fixture._evidence(tmp_path / "source")
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    shutil.copy2(candidate_index, candidate / "echo-delivery-release-evidence-index.json")
    verifier = candidate / "verify-release-candidate-bundle.sh"
    verifier.write_text("#!/usr/bin/env bash\nexit 99\n", encoding="utf-8")
    verifier.chmod(0o755)
    return candidate, evidence, keyring


def _candidate_verifier(candidate: Path) -> None:
    index = candidate / "echo-delivery-release-evidence-index.json"
    value = json.loads(index.read_text(encoding="utf-8"))
    assert value["ciReleaseCandidateReady"] is True
    assert (candidate / "verify-release-candidate-bundle.sh").is_file()


def _build(tmp_path: Path, output_name: str = "output") -> tuple[dict[str, Any], Path]:
    candidate, evidence, keyring = _inputs(tmp_path)
    output = tmp_path / output_name
    output.mkdir()
    report = bundle.build(
        candidate_bundle=candidate,
        evidence_root=evidence,
        acceptance_keyring=keyring,
        source_root=ROOT,
        output_directory=output,
        signature_verifier=physical_fixture._signature,
        candidate_verifier=_candidate_verifier,
    )
    return report, Path(report["directory"])


def _verify(root: Path) -> dict[str, Any]:
    return bundle.verify(
        root,
        signature_verifier=physical_fixture._signature,
        candidate_verifier=_candidate_verifier,
    )


def test_builds_deterministic_read_only_offline_product_delivery_directories(
    tmp_path: Path,
) -> None:
    first, first_root = _build(tmp_path / "first")
    second, second_root = _build(tmp_path / "second")

    assert first["nasProductDeliveryReady"] is True
    assert first["bundleId"] == second["bundleId"]
    assert first["productReportId"] == second["productReportId"]
    assert (first_root / bundle.MANIFEST_NAME).read_bytes() == (
        second_root / bundle.MANIFEST_NAME
    ).read_bytes()
    assert _verify(first_root)["verified"] is True
    assert first_root.stat().st_mode & 0o777 == 0o555
    assert all(
        path.stat().st_mode & 0o222 == 0 for path in first_root.rglob("*") if not path.is_symlink()
    )


@pytest.mark.parametrize("mutation", ["tamper", "extra", "symlink", "manifest-mode"])
def test_rejects_tampered_extra_or_linked_product_delivery_payload(
    tmp_path: Path, mutation: str
) -> None:
    _report, root = _build(tmp_path)
    if mutation == "tamper":
        gate = physical_fixture.physical.PHYSICAL_GATES[0]
        log = root / bundle.PHYSICAL_DIRECTORY / gate / "acceptance.log"
        log.chmod(0o644)
        log.write_text("changed after delivery\n", encoding="utf-8")
        log.chmod(0o444)
    elif mutation == "extra":
        root.chmod(0o755)
        (root / "private-signing-key.asc").write_text("forbidden extra\n", encoding="utf-8")
        root.chmod(0o555)
    elif mutation == "symlink":
        root.chmod(0o755)
        (root / "linked-evidence").symlink_to(root / bundle.REPORT_NAME)
        root.chmod(0o555)
    else:
        (root / bundle.MANIFEST_NAME).chmod(0o644)

    with pytest.raises(bundle.ProductDeliveryBundleError):
        _verify(root)


def test_rejects_a_self_reindexed_package_whose_product_report_does_not_replay(
    tmp_path: Path,
) -> None:
    _report, root = _build(tmp_path)
    report_path = root / bundle.REPORT_NAME
    original = json.loads(report_path.read_text(encoding="utf-8"))
    altered = dict(original)
    altered["nasProductDeliveryReady"] = False
    report_path.chmod(0o644)
    report_path.write_text(json.dumps(altered, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report_path.chmod(0o444)

    manifest_path = root / bundle.MANIFEST_NAME
    root.chmod(0o755)
    manifest_path.chmod(0o644)
    manifest_path.unlink()
    records = bundle._inventory(root)
    replacement = bundle._manifest(original, records)
    bundle._write_new(
        manifest_path,
        json.dumps(replacement, indent=2, sort_keys=True).encode() + b"\n",
    )
    root.chmod(0o555)

    with pytest.raises(bundle.ProductDeliveryBundleError, match="differs from physical replay"):
        _verify(root)


def test_refuses_to_replace_an_existing_delivery_directory(tmp_path: Path) -> None:
    _report, root = _build(tmp_path)
    candidate, evidence, keyring = _inputs(tmp_path / "second-input")

    with pytest.raises(bundle.ProductDeliveryBundleError, match="already exists"):
        bundle.build(
            candidate_bundle=candidate,
            evidence_root=evidence,
            acceptance_keyring=keyring,
            source_root=ROOT,
            output_directory=root.parent,
            signature_verifier=physical_fixture._signature,
            candidate_verifier=_candidate_verifier,
        )


def test_packaged_physical_tools_import_from_their_sibling_offline_directory(
    tmp_path: Path,
) -> None:
    _report, root = _build(tmp_path)
    tools = root / bundle.TOOLS_DIRECTORY

    for script in (
        "physical_acceptance.py",
        "physical_acceptance_capture.py",
        "product_delivery_bundle.py",
    ):
        completed = subprocess.run(
            [sys.executable, str(tools / script), "--help"],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert completed.returncode == 0, completed.stderr


def test_builds_from_the_flat_tool_layout_shipped_in_the_candidate_artifact(
    tmp_path: Path,
) -> None:
    candidate, evidence, keyring = _inputs(tmp_path)
    for destination, repository_relative in bundle.TOOL_FILES.items():
        shutil.copy2(ROOT / repository_relative, candidate / destination)
    output = tmp_path / "output"
    output.mkdir()

    report = bundle.build(
        candidate_bundle=candidate,
        evidence_root=evidence,
        acceptance_keyring=keyring,
        source_root=candidate,
        output_directory=output,
        signature_verifier=physical_fixture._signature,
        candidate_verifier=_candidate_verifier,
    )

    assert report["nasProductDeliveryReady"] is True
    assert Path(report["directory"]).is_dir()
