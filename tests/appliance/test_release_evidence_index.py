from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "appliance" / "release_evidence_index.py"
SPEC = importlib.util.spec_from_file_location("echo_release_evidence_index", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
release_index = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release_index)
PREFLIGHT_SCRIPT = ROOT / "deploy" / "appliance" / "delivery_source_preflight.py"
PREFLIGHT_SPEC = importlib.util.spec_from_file_location(
    "echo_delivery_source_preflight_contract", PREFLIGHT_SCRIPT
)
assert PREFLIGHT_SPEC is not None and PREFLIGHT_SPEC.loader is not None
source_preflight = importlib.util.module_from_spec(PREFLIGHT_SPEC)
PREFLIGHT_SPEC.loader.exec_module(source_preflight)

OS_COMMIT = "1" * 40
OS_TREE = "2" * 40
AGENT_COMMIT = OS_COMMIT
RELEASE_TAG = "echo-appliance-v1.0.0"
SOURCE_MANIFEST_SHA = "4" * 64
AGENT_MANIFEST_SHA = "5" * 64
RAW_SIGNATURE_SHA = "6" * 64
AB_SIGNATURE_SHA = "7" * 64
KEYRING_SHA = "8" * 64


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _with_evidence_id(value: dict[str, Any]) -> dict[str, Any]:
    value["evidence_id"] = _digest(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    )
    return value


def _source() -> dict[str, Any]:
    return {
        "repository": "https://github.com/dengdenghua/echo-os.git",
        "commit": OS_COMMIT,
        "tree": OS_TREE,
        "commit_time": "2026-08-27T00:00:00+00:00",
        "source_date_epoch": 1787788800,
        "manifest_sha256": SOURCE_MANIFEST_SHA,
    }


def _full_source() -> dict[str, Any]:
    return {
        "schema": 1,
        "kind": "echo-os-source-identity",
        **_source(),
        "dirty": False,
    }


def _preflight() -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "kind": "echo.delivery-source-preflight",
        "mode": "online",
        "ready": True,
        "expectedBranch": "os-main",
        "branch": "os-main",
        "sourceRevision": OS_COMMIT,
        "osRepository": "dengdenghua/echo-os",
        "agentSource": {
            "repository": "dengdenghua/echo-os",
            "commit": AGENT_COMMIT,
        },
        "requiredWorkflows": list(release_index.REQUIRED_WORKFLOWS),
        "checks": [
            {"code": code, "status": "passed", "detail": f"{code} is verified"}
            for code in sorted(release_index.PREFLIGHT_CHECKS)
        ],
        "blockers": [],
    }


def _raw() -> dict[str, Any]:
    return _with_evidence_id(
        {
            "schema": 1,
            "image_id": "echo-os",
            "image_version": "1.0.0",
            "architecture": "x86-64",
            "os_source": _source(),
            "agent_source_id": AGENT_COMMIT,
            "agent_manifest_sha256": AGENT_MANIFEST_SHA,
            "install_bundle": {"manifest_sha256": "9" * 64},
            "release_trust": {"keyring": {"sha256": KEYRING_SHA}},
            "installed_image": {"sha256": "a" * 64, "size": 1},
            "checks": {
                f"gate_{number}": {"sha256": f"{number:x}" * 64, "size": 1}
                for number in range(1, 16)
            },
        }
    )


def _ab() -> dict[str, Any]:
    return _with_evidence_id(
        {
            "schema": 3,
            "evidence_kind": "echo-os-ab-update",
            "architecture": "x86-64",
            "base_version": "1.0.0",
            "update_version": "1.0.1",
            "os_source": _full_source(),
            "agent": {
                "source_id": AGENT_COMMIT,
                "manifest_sha256": AGENT_MANIFEST_SHA,
            },
            "base_image": {"sha256": "b" * 64, "size": 1},
            "runner_preflight": {"sha256": "e" * 64, "size": 1},
            "operations_systemd_verification": {
                "schemaVersion": 1,
                "kind": "echo.operations-systemd-native-verification",
                "sourceRevision": OS_COMMIT,
                "os": {"id": "debian", "versionId": "13", "codename": "trixie"},
                "systemdVersion": "systemd 257 (257.8-1)",
                "units": {
                    name: {"sha256": character * 64}
                    for name, character in zip(
                        (
                            "echo-state-backup.service",
                            "echo-state-backup.timer",
                            "echo-audit-evidence.service",
                            "echo-audit-evidence.timer",
                        ),
                        ("1", "2", "3", "4"),
                        strict=True,
                    )
                },
                "verified": True,
                "reportSha256": "5" * 64,
            },
            "update_bundle": {"manifest_sha256": "c" * 64},
            "update_keyring": {"sha256": KEYRING_SHA, "size": 1},
            "checks": {"rollback": {"sha256": "d" * 64, "size": 1}},
        }
    )


def _omv(evidence: bytes, plugin: bytes) -> dict[str, Any]:
    return {
        "verified": True,
        "schemaVersion": 6,
        "sourceRevision": OS_COMMIT,
        "omvVersion": "8.0.0",
        "pluginVersion": "1.0.0-1",
        "pluginSha256": _digest(plugin),
        "evidenceSha256": _digest(evidence),
        "netplanProbeResult": "upstream-compatible",
        "nfsShareUuid": "00000000-0000-0000-0000-000000000001",
        "nfsClientCidr": "192.0.2.0/24",
        "accountGroupName": "family",
        "accountUserName": "alice",
        "accountSmbShareName": "family",
        "accountSmbProtocol": "smb3",
        "warningCount": 0,
    }


def _appliance() -> dict[str, Any]:
    digest = f"sha256:{'e' * 64}"
    image = "ghcr.io/dengdenghua/echo-os"
    return {
        "schemaVersion": 1,
        "kind": "echo-appliance-container-release",
        "createdAt": "2026-08-27T00:00:00Z",
        "release": {"tag": RELEASE_TAG, "version": "1.0.0"},
        "source": {
            "repository": "dengdenghua/echo-os",
            "ref": f"refs/tags/{RELEASE_TAG}",
            "commit": OS_COMMIT,
        },
        "agentSource": {
            "repository": "dengdenghua/echo-os",
            "commit": AGENT_COMMIT,
        },
        "image": {
            "name": image,
            "indexDigest": digest,
            "indexFile": "echo-appliance-index.json",
            "immutableReference": f"{image}@{digest}",
            "platformDigests": {"linux/amd64": digest, "linux/arm64": digest},
        },
        "stateSchema": {"currentVersion": 1},
        "attestations": {
            "buildkitProvenanceMode": "max",
            "buildkitSbom": True,
            "registryAttestationManifestCount": 2,
            "githubOidcProvenanceRequired": True,
        },
        "sboms": {},
        "pythonDependencies": {},
        "operationsBundle": {
            "artifactId": "5" * 16,
            "archive": "echo-appliance-operations.tar.gz",
            "sha256": "6" * 64,
            "fileCount": 21,
            "architectures": ["amd64", "arm64"],
            "imageReference": f"{image}@{digest}",
            "checksum": {
                "file": "echo-appliance-operations.tar.gz.sha256",
                "sha256": "7" * 64,
            },
            "spdx": {
                "file": "echo-appliance-operations.spdx.json",
                "format": "SPDX-2.3",
                "sha256": "8" * 64,
            },
            "verifier": {"file": "operations_bundle.py", "sha256": "9" * 64},
            "verifyCommand": (
                "python3 operations_bundle.py verify echo-appliance-operations.tar.gz"
            ),
            "extractCommand": (
                "sudo python3 operations_bundle.py extract "
                "echo-appliance-operations.tar.gz --destination /opt/echo-os "
                "--require-root-owner"
            ),
            "installCommand": "./install-appliance.sh",
        },
        "upgrade": {},
        "recovery": {},
    }


def _candidate_preflight(digests: dict[str, str]) -> dict[str, Any]:
    workflows = release_index.CANDIDATE_RUNS
    runs = {
        name: {
            "id": 100 + index,
            "attempt": 1,
            "workflow": workflow,
            "event": "push",
            "headBranch": RELEASE_TAG if name == "appliance" else "os-main",
            "htmlUrl": f"https://github.com/dengdenghua/echo-os/actions/runs/{100 + index}",
        }
        for index, (name, workflow) in enumerate(workflows.items(), start=1)
    }
    attestations = {}
    for name, (run_name, branch_ref) in release_index.CANDIDATE_ATTESTATIONS.items():
        attestations[name] = {
            "sha256": digests[name],
            "signerWorkflow": (f"github.com/dengdenghua/echo-os/{workflows[run_name]}"),
            "sourceRef": f"refs/tags/{RELEASE_TAG}" if branch_ref is None else branch_ref,
            "runnerPolicy": release_index.CANDIDATE_RUNNER_POLICIES[run_name],
            "verificationCount": 1,
        }
    report = {
        "schemaVersion": 1,
        "kind": "echo.delivery-release-candidate-preflight",
        "ready": True,
        "repository": "dengdenghua/echo-os",
        "sourceRevision": OS_COMMIT,
        "releaseTag": RELEASE_TAG,
        "releaseTagRevision": OS_COMMIT,
        "runs": runs,
        "attestations": attestations,
    }
    report["reportId"] = _digest(json.dumps(report, sort_keys=True, separators=(",", ":")).encode())
    return report


def _inputs() -> dict[str, Any]:
    preflight = _preflight()
    raw = _raw()
    ab = _ab()
    omv_evidence = b'{"real":"omv"}\n'
    plugin = b"debian-package"
    appliance = _appliance()
    preflight_bytes = json.dumps(preflight).encode()
    raw_bytes = json.dumps(raw).encode()
    ab_bytes = json.dumps(ab).encode()
    omv = _omv(omv_evidence, plugin)
    omv_bytes = json.dumps(omv).encode()
    appliance_bytes = json.dumps(appliance).encode()
    candidate = _candidate_preflight(
        {
            "osImageManifest": _digest(raw_bytes),
            "osImageSignature": RAW_SIGNATURE_SHA,
            "osImageKeyring": KEYRING_SHA,
            "abManifest": _digest(ab_bytes),
            "abSignature": AB_SIGNATURE_SHA,
            "abKeyring": KEYRING_SHA,
            "omvEvidence": _digest(omv_evidence),
            "omvVerification": _digest(omv_bytes),
            "omvPlugin": _digest(plugin),
            "applianceManifest": _digest(appliance_bytes),
        }
    )
    candidate_bytes = json.dumps(candidate).encode()
    return {
        "preflight": (preflight, preflight_bytes),
        "candidate_preflight": (candidate, candidate_bytes),
        "raw_evidence": (raw, raw_bytes),
        "raw_signature": {
            "manifestSha256": _digest(raw_bytes),
            "signatureSha256": RAW_SIGNATURE_SHA,
            "keyringSha256": KEYRING_SHA,
        },
        "ab_evidence": (ab, ab_bytes),
        "ab_signature": {
            "manifestSha256": _digest(ab_bytes),
            "signatureSha256": AB_SIGNATURE_SHA,
            "keyringSha256": KEYRING_SHA,
        },
        "omv_verification": (omv, omv_bytes),
        "omv_evidence_raw": omv_evidence,
        "omv_plugin_raw": plugin,
        "appliance": (appliance, appliance_bytes),
    }


def _change_ab_source(inputs: dict[str, Any]) -> None:
    ab = inputs["ab_evidence"][0]
    ab["os_source"]["commit"] = "f" * 40
    ab["operations_systemd_verification"]["sourceRevision"] = "f" * 40
    del ab["evidence_id"]
    _with_evidence_id(ab)
    raw = json.dumps(ab).encode()
    inputs["ab_evidence"] = (ab, raw)
    inputs["ab_signature"]["manifestSha256"] = _digest(raw)


def _change_appliance_agent(inputs: dict[str, Any]) -> None:
    inputs["appliance"][0]["agentSource"]["commit"] = "f" * 40


def _change_omv_plugin_digest(inputs: dict[str, Any]) -> None:
    inputs["omv_verification"][0]["pluginSha256"] = "f" * 64


def _change_raw_signature_digest(inputs: dict[str, Any]) -> None:
    inputs["raw_signature"]["manifestSha256"] = "f" * 64


def test_release_index_accepts_the_exact_source_preflight_check_contract() -> None:
    assert set(source_preflight.PREFLIGHT_CHECK_CODES) == release_index.PREFLIGHT_CHECKS
    assert "git_repository" in release_index.PREFLIGHT_CHECKS


def test_packaged_release_index_uses_its_sibling_signature_verifier(tmp_path: Path) -> None:
    bundle = tmp_path / "candidate-audit"
    bundle.mkdir()
    packaged_script = bundle / "release_evidence_index.py"
    packaged_script.write_bytes(SCRIPT.read_bytes())
    verifier = bundle / "verify-os-image-evidence-release.sh"
    verifier.write_bytes(
        (ROOT / "packaging/image/verify-os-image-evidence-release.sh").read_bytes()
    )
    verifier.chmod(0o755)
    spec = importlib.util.spec_from_file_location(
        "packaged_release_evidence_index", packaged_script
    )
    assert spec is not None and spec.loader is not None
    packaged = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(packaged)

    assert packaged._signature_verifier_path() == verifier


def test_packaged_signature_verifier_prefers_its_sibling_public_keyring_auditor() -> None:
    source = (ROOT / "packaging/image/verify-os-image-evidence-release.sh").read_text(
        encoding="utf-8"
    )

    assert 'PACKAGED_VERIFY_KEYRING="$IMAGE_DIR/verify_public_keyring.py"' in source
    assert 'REPOSITORY_VERIFY_KEYRING="$IMAGE_DIR/../../deploy/installer/' in source
    assert '[[ -f "$PACKAGED_VERIFY_KEYRING" && ! -L "$PACKAGED_VERIFY_KEYRING" ]]' in source


def test_builds_one_source_bound_candidate_without_claiming_product_delivery() -> None:
    report = release_index.build_index(**_inputs())

    assert report["source"] == {
        "repository": "dengdenghua/echo-os",
        "commit": OS_COMMIT,
        "agentRepository": "dengdenghua/echo-os",
        "agentCommit": AGENT_COMMIT,
        "releaseTag": RELEASE_TAG,
    }
    assert report["ciReleaseCandidateReady"] is True
    assert report["nasProductDeliveryReady"] is False
    assert report["evidence"]["candidatePreflight"]["runnerPolicies"] == (
        release_index.CANDIDATE_RUNNER_POLICIES
    )
    assert report["evidence"]["appliance"]["operationsBundle"] == {
        "artifactId": "5" * 16,
        "sha256": "6" * 64,
        "imageReference": report["evidence"]["appliance"]["immutableReference"],
    }
    assert report["physicalAcceptance"]["complete"] is False
    assert report["physicalAcceptance"]["remainingGates"] == list(release_index.PHYSICAL_GATES)
    unsigned = dict(report)
    index_id = unsigned.pop("indexId")
    assert index_id == _digest(json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode())


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (_change_ab_source, "different OS commits"),
        (_change_appliance_agent, "embedded Agent source differs"),
        (_change_omv_plugin_digest, "artifact bytes"),
        (_change_raw_signature_digest, "another manifest"),
    ],
)
def test_rejects_cross_run_or_forged_evidence(mutation: Any, message: str) -> None:
    inputs = _inputs()
    mutation(inputs)

    with pytest.raises(release_index.ReleaseEvidenceError, match=message):
        release_index.build_index(**inputs)


def test_rejects_operations_bundle_for_another_immutable_image() -> None:
    inputs = _inputs()
    inputs["appliance"][0]["operationsBundle"]["imageReference"] = (
        f"ghcr.io/echo-os/echo-os@sha256:{'f' * 64}"
    )

    with pytest.raises(release_index.ReleaseEvidenceError, match="release identity"):
        release_index.build_index(**inputs)


def test_rejects_rewritten_self_checked_manifest() -> None:
    inputs = _inputs()
    raw = inputs["raw_evidence"][0]
    raw["image_version"] = "2.0.0"
    rewritten = json.dumps(raw).encode()
    inputs["raw_evidence"] = (raw, rewritten)
    inputs["raw_signature"]["manifestSha256"] = _digest(rewritten)

    with pytest.raises(release_index.ReleaseEvidenceError, match="evidence ID"):
        release_index.build_index(**inputs)


def test_rejects_provenance_report_for_another_artifact() -> None:
    inputs = _inputs()
    candidate = inputs["candidate_preflight"][0]
    candidate["attestations"]["omvEvidence"]["sha256"] = "f" * 64
    del candidate["reportId"]
    candidate["reportId"] = _digest(
        json.dumps(candidate, sort_keys=True, separators=(",", ":")).encode()
    )
    inputs["candidate_preflight"] = (candidate, json.dumps(candidate).encode())

    with pytest.raises(release_index.ReleaseEvidenceError, match="another omvEvidence"):
        release_index.build_index(**inputs)


def test_rejects_an_attestation_with_the_wrong_runner_policy() -> None:
    inputs = _inputs()
    candidate = inputs["candidate_preflight"][0]
    candidate["attestations"]["osImageManifest"]["runnerPolicy"] = "github-hosted-only"
    del candidate["reportId"]
    candidate["reportId"] = _digest(
        json.dumps(candidate, sort_keys=True, separators=(",", ":")).encode()
    )
    inputs["candidate_preflight"] = (candidate, json.dumps(candidate).encode())

    with pytest.raises(release_index.ReleaseEvidenceError, match="attestation is invalid"):
        release_index.build_index(**inputs)


def test_strict_loader_rejects_duplicate_keys_and_symlinks(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"ready":true,"ready":false}', encoding="utf-8")
    link = tmp_path / "link.json"
    link.symlink_to(duplicate)

    with pytest.raises(release_index.ReleaseEvidenceError, match="duplicate JSON key"):
        release_index._load_json(duplicate, "test")
    with pytest.raises(release_index.ReleaseEvidenceError, match="unavailable"):
        release_index._load_json(link, "test")
    with pytest.raises(release_index.ReleaseEvidenceError, match="unavailable"):
        release_index._load_json(tmp_path / "missing.json", "test")


def test_cli_writes_new_index_and_returns_zero_for_a_coherent_candidate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    inputs = _inputs()
    paths: dict[str, Path] = {}
    for name, key in (
        ("preflight", "preflight"),
        ("candidate", "candidate_preflight"),
        ("raw", "raw_evidence"),
        ("ab", "ab_evidence"),
        ("omv-report", "omv_verification"),
        ("appliance", "appliance"),
    ):
        path = tmp_path / f"{name}.json"
        path.write_bytes(inputs[key][1])
        paths[name] = path
    paths["omv-evidence"] = tmp_path / "omv-evidence.json"
    paths["omv-evidence"].write_bytes(inputs["omv_evidence_raw"])
    paths["plugin"] = tmp_path / "plugin.deb"
    paths["plugin"].write_bytes(inputs["omv_plugin_raw"])
    for name in ("raw-signature", "raw-keyring", "ab-signature", "ab-keyring"):
        paths[name] = tmp_path / name
        paths[name].write_bytes(name.encode())
    output = tmp_path / "release-index.json"

    def verify(manifest: Path, _signature: Path, _keyring: Path) -> dict[str, str]:
        signature = RAW_SIGNATURE_SHA if manifest == paths["raw"] else AB_SIGNATURE_SHA
        return {
            "manifestSha256": _digest(manifest.read_bytes()),
            "signatureSha256": signature,
            "keyringSha256": KEYRING_SHA,
        }

    exit_code = release_index.main(
        [
            "--source-preflight",
            str(paths["preflight"]),
            "--candidate-preflight",
            str(paths["candidate"]),
            "--os-image-evidence",
            str(paths["raw"]),
            "--os-image-signature",
            str(paths["raw-signature"]),
            "--os-image-keyring",
            str(paths["raw-keyring"]),
            "--ab-evidence",
            str(paths["ab"]),
            "--ab-signature",
            str(paths["ab-signature"]),
            "--ab-keyring",
            str(paths["ab-keyring"]),
            "--omv-verification",
            str(paths["omv-report"]),
            "--omv-evidence",
            str(paths["omv-evidence"]),
            "--omv-plugin-package",
            str(paths["plugin"]),
            "--appliance-release",
            str(paths["appliance"]),
            "--output",
            str(output),
        ],
        signature_verifier=verify,
    )

    assert exit_code == 0
    assert json.loads(output.read_text())["nasProductDeliveryReady"] is False
    assert "candidate=ready product=physical-gates-required" in capsys.readouterr().out
    assert output.stat().st_mode & 0o777 == 0o444


def test_cli_fails_closed_when_output_already_exists(
    tmp_path: Path,
) -> None:
    output = tmp_path / "index.json"
    output.write_text("keep", encoding="utf-8")

    with pytest.raises(release_index.ReleaseEvidenceError, match="new path"):
        release_index._write_new(output, {"ready": True})

    assert output.read_text(encoding="utf-8") == "keep"
