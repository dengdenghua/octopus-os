from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "appliance" / "verify-release-candidate-bundle.sh"
REQUIRED_FILES = (
    "echo-delivery-source-preflight.json",
    "echo-release-candidate-preflight.json",
    "echo-delivery-release-evidence-index.json",
    "delivery_source_preflight.py",
    "hub_lifecycle_lab.py",
    "lan_discovery_functional_lab.py",
    "paperless_functional_lab.py",
    "physical_acceptance.py",
    "physical_acceptance_capture.py",
    "product_delivery_bundle.py",
    "release_candidate_preflight.py",
    "release_evidence_index.py",
    "verify_public_keyring.py",
    "verify-os-image-evidence-release.sh",
    "verify-release-candidate-bundle.sh",
    "inputs/os-image-evidence.json",
    "inputs/os-image-evidence.json.gpg",
    "inputs/os-image-keyring.gpg",
    "inputs/ab-update-evidence.json",
    "inputs/ab-update-evidence.json.gpg",
    "inputs/ab-update-keyring.gpg",
    "inputs/omv-evidence.json",
    "inputs/omv-verification.json",
    "inputs/openmediavault-echo-os.deb",
    "inputs/appliance-release.json",
)
PYTHON_TOOL_SOURCES = {
    "delivery_source_preflight.py": "deploy/appliance/delivery_source_preflight.py",
    "hub_lifecycle_lab.py": "deploy/appliance/hub_lifecycle_lab.py",
    "lan_discovery_functional_lab.py": "deploy/appliance/lan_discovery_functional_lab.py",
    "paperless_functional_lab.py": "deploy/appliance/paperless_functional_lab.py",
    "physical_acceptance.py": "deploy/appliance/physical_acceptance.py",
    "physical_acceptance_capture.py": "deploy/appliance/physical_acceptance_capture.py",
    "product_delivery_bundle.py": "deploy/appliance/product_delivery_bundle.py",
    "release_candidate_preflight.py": "deploy/appliance/release_candidate_preflight.py",
    "release_evidence_index.py": "deploy/appliance/release_evidence_index.py",
    "verify_public_keyring.py": "deploy/installer/verify_public_keyring.py",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_checksums(bundle: Path) -> None:
    lines = [f"{_sha256(bundle / relative)}  {relative}\n" for relative in REQUIRED_FILES]
    (bundle / "echo-delivery-release-candidate.sha256").write_text("".join(lines), encoding="utf-8")


def _bundle(tmp_path: Path, *, replay_matches: bool = True) -> Path:
    bundle = tmp_path / "candidate-audit"
    (bundle / "inputs").mkdir(parents=True)
    for relative in REQUIRED_FILES:
        path = bundle / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"fixture:{relative}\n".encode())
    shutil.copy2(SCRIPT, bundle / "verify-release-candidate-bundle.sh")
    (bundle / "echo-delivery-release-evidence-index.json").write_text(
        '{"indexId":"candidate-fixture"}\n', encoding="utf-8"
    )
    output_expression = "packaged.read_bytes()" if replay_matches else 'b\'{"indexId":"other"}\\n\''
    (bundle / "release_evidence_index.py").write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "output = Path(sys.argv[sys.argv.index('--output') + 1])\n"
        "packaged = Path(__file__).with_name('echo-delivery-release-evidence-index.json')\n"
        f"output.write_bytes({output_expression})\n",
        encoding="utf-8",
    )
    _write_checksums(bundle)
    return bundle


def _run(bundle: Path, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    scratch = tmp_path / "scratch"
    scratch.mkdir(exist_ok=True)
    environment = os.environ.copy()
    environment["TMPDIR"] = str(scratch)
    return subprocess.run(
        [str(bundle / "verify-release-candidate-bundle.sh")],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env=environment,
    )


def test_candidate_python_tools_start_from_a_flat_offline_directory(
    tmp_path: Path,
) -> None:
    tools = tmp_path / "candidate-tools"
    outside = tmp_path / "outside-candidate"
    tools.mkdir()
    outside.mkdir()
    for name, source in PYTHON_TOOL_SOURCES.items():
        shutil.copy2(ROOT / source, tools / name)

    for name in PYTHON_TOOL_SOURCES:
        completed = subprocess.run(  # noqa: S603
            [sys.executable, str(tools / name), "--help"],
            cwd=outside,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert completed.returncode == 0, f"{name}: {completed.stderr}"
        assert completed.stdout.startswith("usage:"), name


def test_candidate_bundle_replays_the_packaged_index_without_repository_source(
    tmp_path: Path,
) -> None:
    completed = _run(_bundle(tmp_path), tmp_path)

    assert completed.returncode == 0, completed.stderr
    assert "ECHO_DELIVERY_CANDIDATE_OFFLINE_OK" in completed.stdout


def test_candidate_bundle_rejects_tampering_missing_checksum_or_extra_path(
    tmp_path: Path,
) -> None:
    tampered_root = tmp_path / "tampered"
    tampered_root.mkdir()
    tampered = _bundle(tampered_root)
    (tampered / "inputs/omv-evidence.json").write_text("tampered\n", encoding="utf-8")
    assert _run(tampered, tampered_root).returncode != 0

    missing_root = tmp_path / "missing"
    missing_root.mkdir()
    missing = _bundle(missing_root)
    checksum = missing / "echo-delivery-release-candidate.sha256"
    checksum.write_text("".join(checksum.read_text().splitlines(keepends=True)[:-1]))
    missing_result = _run(missing, missing_root)
    assert missing_result.returncode != 0
    assert "wrong size" in missing_result.stderr

    extra_root = tmp_path / "extra"
    extra_root.mkdir()
    extra = _bundle(extra_root)
    (extra / "private-signing-key.asc").write_text("must never be accepted\n", encoding="utf-8")
    extra_result = _run(extra, extra_root)
    assert extra_result.returncode != 0
    assert "unexpected path" in extra_result.stderr


def test_candidate_bundle_rejects_a_replayed_index_that_differs_from_the_package(
    tmp_path: Path,
) -> None:
    completed = _run(_bundle(tmp_path, replay_matches=False), tmp_path)

    assert completed.returncode != 0
    assert "differs from the packaged decision" in completed.stderr
