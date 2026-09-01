from __future__ import annotations

import hashlib
import json
import stat
import subprocess
import uuid
from pathlib import Path
from typing import Any

import pytest

from deploy.appliance import operations_bundle
from deploy.appliance import protocol_interoperability_lab as lab

REPOSITORY = Path(__file__).resolve().parents[2]
IMAGE_REFERENCE = f"ghcr.io/echo-os/echo-os@sha256:{'6' * 64}"


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _candidate(tmp_path: Path, bundle: dict[str, Any]) -> Path:
    value: dict[str, Any] = {
        "schemaVersion": 1,
        "kind": "echo.delivery-release-evidence-index",
        "source": {
            "repository": "dengdenghua/echo-os",
            "commit": "1" * 40,
            "agentRepository": "dengdenghua/echo-agent",
            "agentCommit": "2" * 40,
            "releaseTag": "echo-appliance-v1.0.0",
        },
        "evidence": {
            "candidatePreflight": {"reportId": "4" * 64},
            "appliance": {
                "manifestSha256": "5" * 64,
                "immutableReference": IMAGE_REFERENCE,
                "operationsBundle": {
                    "artifactId": bundle["artifactId"],
                    "sha256": bundle["archiveSha256"],
                    "imageReference": IMAGE_REFERENCE,
                },
            },
        },
        "ciReleaseCandidateReady": True,
        "nasProductDeliveryReady": False,
        "physicalAcceptance": {
            "complete": False,
            "remainingGates": [
                "physical_x86_64_install_and_cold_boot",
                "supported_arm64_hardware_install_and_cold_boot",
                "real_disk_smart_and_raid_degradation_recovery",
                lab.GATE,
                "power_loss_during_update_and_state_restore",
                "recovery_media_bare_metal_restore",
            ],
        },
    }
    value["indexId"] = _digest(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o444)
    return path


def _setup(tmp_path: Path) -> tuple[dict[str, Any], Path, Path, Path]:
    built = operations_bundle.build(REPOSITORY, tmp_path / "build", IMAGE_REFERENCE)
    root_name, files = operations_bundle._read_archive(Path(built["archive"]))
    root = tmp_path / "bundle" / root_name
    root.mkdir(parents=True)
    for name, (raw, mode) in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        path.chmod(mode)
    candidate = _candidate(tmp_path, built)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    plan_path = tmp_path / "protocol-interoperability-lab-plan.json"
    plan = lab.build_plan(
        candidate_index=candidate,
        bundle_root=root,
        server="echo-nas.lan",
        lab_share_id=str(uuid.UUID(int=19, version=4)),
        evidence_directory=evidence,
        output=plan_path,
    )
    return plan, plan_path, evidence, root


def _mount_probe(_root: Path, protocol: str, _server: str, _system: str) -> dict[str, Any]:
    return {
        "mounted": True,
        "protocol": protocol,
        "filesystemType": "cifs" if protocol == "smb" else "nfs4",
        "serverMatched": True,
        "nativeEvidenceSha256": "a" * 64,
    }


def _authorized(root: Path, plan: dict[str, Any]) -> Path:
    root.mkdir()
    marker = root / lab.AUTHORIZATION_NAME
    marker.write_text(json.dumps(plan["authorization"]) + "\n", encoding="utf-8")
    return root


def test_windows_native_probe_binds_the_exact_unc_server_share_and_smb_dialect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = {
        "Connections": [
            {
                "ServerName": "echo-nas.lan",
                "ShareName": "lab-share",
                "Dialect": "3.1.1",
                "Signed": True,
                "Encrypted": False,
            }
        ],
        "Mappings": [],
    }
    monkeypatch.setattr(
        lab,
        "_run",
        lambda command: subprocess.CompletedProcess(command, 0, json.dumps(value), ""),
    )

    result = lab._native_mount_probe(
        Path(r"\\echo-nas.lan\lab-share\folder"),
        "smb",
        "echo-nas.lan",
        "Windows",
    )

    assert result["filesystemType"] == "smb"
    assert result["serverMatched"] is True

    with pytest.raises(lab.ProtocolInteroperabilityLabError, match="planned server"):
        lab._native_mount_probe(Path("/local/folder"), "smb", "echo-nas.lan", "Windows")

    value["Connections"][0]["ShareName"] = "different-share"
    with pytest.raises(lab.ProtocolInteroperabilityLabError, match="matching SMB2/SMB3"):
        lab._native_mount_probe(
            Path(r"\\echo-nas.lan\lab-share\folder"),
            "smb",
            "echo-nas.lan",
            "Windows",
        )


def test_unix_mount_sources_require_the_exact_server_not_a_substring() -> None:
    assert lab._remote_source_server("//user@echo-nas.lan/share", "smb") == "echo-nas.lan"
    assert lab._remote_source_server("echo-nas.lan:/export/share", "nfs") == "echo-nas.lan"
    assert (
        lab._remote_source_server("//echo-nas.lan.attacker.example/share", "smb") != "echo-nas.lan"
    )
    assert (
        lab._remote_source_server("echo-nas.lan.attacker.example:/export", "nfs") != "echo-nas.lan"
    )


def test_plan_is_candidate_bundle_and_share_bound(tmp_path: Path) -> None:
    plan, plan_path, evidence, root = _setup(tmp_path)

    assert plan["schemaVersion"] == 1
    assert plan["kind"] == lab.PLAN_KIND
    assert plan["gate"] == lab.GATE
    assert plan["releaseCandidate"]["indexId"]
    assert plan["operationsBundle"]["protocolLabSha256"] == _digest(
        (root / "protocol_interoperability_lab.py").read_bytes()
    )
    assert plan["server"] == "echo-nas.lan"
    assert plan["evidenceDirectory"] == str(evidence)
    assert plan["phases"] == list(lab.PHASES)
    assert plan["outputs"] == lab.PHASE_OUTPUTS
    assert plan["sizes"]["largeFileBytes"] == 1024**3
    assert plan["sizes"]["quotaProbeMaximumBytes"] == 1024**3
    assert stat.S_IMODE(plan_path.stat().st_mode) == 0o400
    assert lab._load_plan(plan_path) == plan


@pytest.mark.parametrize(
    ("role", "system_name"),
    [
        ("windows-smb", "Windows"),
        ("macos-smb", "Darwin"),
        ("linux-smb", "Linux"),
        ("macos-nfs", "Darwin"),
        ("linux-nfs", "Linux"),
    ],
)
def test_real_client_probe_writes_reads_renames_deletes_and_records_native_mount(
    tmp_path: Path, role: str, system_name: str
) -> None:
    plan, plan_path, evidence, root = _setup(tmp_path)
    share = _authorized(tmp_path / "share", plan)
    output = evidence / lab.PHASE_OUTPUTS[role]

    result = lab.run_probe(
        plan_path=plan_path,
        role=role,
        mount_root=share,
        confirmation=plan["confirmations"][role],
        output=output,
        system_name=system_name,
        mount_probe=_mount_probe,
    )

    assert result["check"] == lab.PHASE_CHECKS[role]
    assert result["passed"] is True
    assert result["details"]["bytes"] == lab.PROBE_BYTES
    assert result["details"]["writeVerified"] is True
    assert result["details"]["readVerified"] is True
    assert result["details"]["renameVerified"] is True
    assert result["details"]["deleteVerified"] is True
    assert result["details"]["mount"]["serverMatched"] is True
    assert {path.name for path in share.iterdir()} == {lab.AUTHORIZATION_NAME}
    assert stat.S_IMODE(output.stat().st_mode) == 0o444


def test_probe_rejects_wrong_os_confirmation_marker_mount_and_replace(tmp_path: Path) -> None:
    plan, plan_path, evidence, _root = _setup(tmp_path)
    share = _authorized(tmp_path / "share", plan)
    output = evidence / lab.PHASE_OUTPUTS["windows-smb"]
    arguments = {
        "plan_path": plan_path,
        "role": "windows-smb",
        "mount_root": share,
        "confirmation": plan["confirmations"]["windows-smb"],
        "output": output,
        "system_name": "Windows",
        "mount_probe": _mount_probe,
    }

    with pytest.raises(lab.ProtocolInteroperabilityLabError, match="wrong client OS"):
        lab.run_probe(**{**arguments, "system_name": "Linux"})
    with pytest.raises(lab.ProtocolInteroperabilityLabError, match="confirmation"):
        lab.run_probe(**{**arguments, "confirmation": "RUN SOMETHING ELSE"})

    marker = share / lab.AUTHORIZATION_NAME
    marker.write_text("{}\n", encoding="utf-8")
    with pytest.raises(lab.ProtocolInteroperabilityLabError, match="not authorized"):
        lab.run_probe(**arguments)
    marker.write_text(json.dumps(plan["authorization"]) + "\n", encoding="utf-8")
    unexpected = share / "existing-user-data.txt"
    unexpected.write_text("must not touch\n", encoding="utf-8")
    with pytest.raises(lab.ProtocolInteroperabilityLabError, match="not an empty share"):
        lab.run_probe(**arguments)
    unexpected.unlink()

    def bad_mount(*_args: object) -> dict[str, Any]:
        result = _mount_probe(share, "smb", "echo-nas.lan", "Windows")
        result["serverMatched"] = False
        return result

    with pytest.raises(lab.ProtocolInteroperabilityLabError, match="mount evidence"):
        lab.run_probe(**{**arguments, "mount_probe": bad_mount})

    lab.run_probe(**arguments)
    with pytest.raises(FileExistsError):
        lab.run_probe(**arguments)


def test_permissions_require_real_allowed_and_denied_smb_and_nfs_paths(tmp_path: Path) -> None:
    plan, plan_path, evidence, _root = _setup(tmp_path)
    smb_allowed = _authorized(tmp_path / "smb-allowed", plan)
    nfs_allowed = _authorized(tmp_path / "nfs-allowed", plan)
    smb_denied = _authorized(tmp_path / "smb-denied", plan)
    nfs_denied = _authorized(tmp_path / "nfs-denied", plan)
    smb_denied.chmod(0o555)
    nfs_denied.chmod(0o555)
    output = evidence / lab.PHASE_OUTPUTS["permissions"]
    try:
        result = lab.run_permissions(
            plan_path=plan_path,
            smb_allowed=smb_allowed,
            nfs_allowed=nfs_allowed,
            smb_denied=smb_denied,
            nfs_denied=nfs_denied,
            confirmation=plan["confirmations"]["permissions"],
            output=output,
            mount_probe=_mount_probe,
            system_name="Linux",
        )
    finally:
        smb_denied.chmod(0o755)
        nfs_denied.chmod(0o755)

    assert result["check"] == "userAndAclPermissionsVerified"
    assert result["details"]["allowedSmbWrite"] is True
    assert result["details"]["allowedNfsWrite"] is True
    assert result["details"]["deniedSmbWrite"] is True
    assert result["details"]["deniedNfsWrite"] is True
    assert result["details"]["denialErrors"] == {"smb": "EACCES", "nfs": "EACCES"}


def test_quota_and_large_file_phases_require_cross_protocol_proof(tmp_path: Path) -> None:
    plan, plan_path, evidence, _root = _setup(tmp_path)
    smb = _authorized(tmp_path / "smb", plan)
    nfs = _authorized(tmp_path / "nfs", plan)

    quota = lab.run_quota(
        plan_path=plan_path,
        smb_root=smb,
        nfs_root=nfs,
        confirmation=plan["confirmations"]["quota"],
        output=evidence / lab.PHASE_OUTPUTS["quota"],
        mount_probe=_mount_probe,
        quota_probe=lambda *_args: {
            "smbQuotaRejected": True,
            "nfsQuotaRejected": True,
            "crossProtocolVisibility": True,
            "allocatedBytes": 64 * 1024**2,
            "smbError": "EDQUOT",
            "nfsError": "EDQUOT",
        },
        system_name="Linux",
    )
    large = lab.run_large_file(
        plan_path=plan_path,
        smb_root=smb,
        nfs_root=nfs,
        confirmation=plan["confirmations"]["large-file"],
        output=evidence / lab.PHASE_OUTPUTS["large-file"],
        mount_probe=_mount_probe,
        cross_protocol_probe=lambda _smb, _nfs, size, _seed: {
            "bytes": size,
            "sha256": "c" * 64,
            "writtenViaSmb": True,
            "readViaNfs": True,
            "deletedViaNfs": True,
            "deleteObservedViaSmb": True,
        },
        system_name="Linux",
    )

    assert quota["check"] == "quotaEnforcedAcrossProtocols"
    assert quota["details"]["allocatedBytes"] == 64 * 1024**2
    assert large["check"] == "largeFileVerified"
    assert large["details"]["bytes"] == 1024**3


def test_quota_phase_rejects_unproven_or_partial_enforcement(tmp_path: Path) -> None:
    plan, plan_path, evidence, _root = _setup(tmp_path)
    smb = _authorized(tmp_path / "smb", plan)
    nfs = _authorized(tmp_path / "nfs", plan)

    with pytest.raises(lab.ProtocolInteroperabilityLabError, match="did not prove"):
        lab.run_quota(
            plan_path=plan_path,
            smb_root=smb,
            nfs_root=nfs,
            confirmation=plan["confirmations"]["quota"],
            output=evidence / lab.PHASE_OUTPUTS["quota"],
            mount_probe=_mount_probe,
            quota_probe=lambda *_args: {
                "smbQuotaRejected": True,
                "nfsQuotaRejected": False,
                "crossProtocolVisibility": True,
                "allocatedBytes": 1,
                "smbError": "EDQUOT",
                "nfsError": "EDQUOT",
            },
            system_name="Linux",
        )


def test_cross_protocol_probe_performs_real_streamed_io_without_sparse_shortcut(
    tmp_path: Path,
) -> None:
    root = tmp_path / "same-share"
    root.mkdir()

    result = lab._cross_protocol_probe(root, root, 2 * 1024**2, "d" * 64)

    assert result["bytes"] == 2 * 1024**2
    assert result["writtenViaSmb"] is True
    assert result["readViaNfs"] is True
    assert result["deletedViaNfs"] is True
    assert result["deleteObservedViaSmb"] is True
    assert list(root.iterdir()) == []


def test_verify_binds_all_eight_machine_generated_logs_to_one_lifecycle(tmp_path: Path) -> None:
    plan, plan_path, evidence, root = _setup(tmp_path)
    for phase in lab.PHASES:
        payload = lab._phase_payload(plan, phase, {"machineGenerated": True})
        lab._write_new(evidence / lab.PHASE_OUTPUTS[phase], payload)
    output = evidence / lab.LIFECYCLE_NAME

    lifecycle = lab.verify_evidence(
        plan_path=plan_path,
        candidate_index=Path(plan["releaseCandidate"]["indexPath"]),
        bundle_root=root,
        evidence_directory=evidence,
        output=output,
    )

    assert lifecycle["kind"] == lab.LIFECYCLE_KIND
    assert lifecycle["gate"] == lab.GATE
    assert lifecycle["labPlanId"] == plan["planId"]
    assert set(lifecycle["checks"]) == set(lab.EXPECTED_CHECKS)
    assert lifecycle["allPassed"] is True
    for check, item in lifecycle["checks"].items():
        assert item["passed"] is True
        phase = next(name for name, value in lab.PHASE_CHECKS.items() if value == check)
        path = evidence / lab.PHASE_OUTPUTS[phase]
        assert item["evidence"] == {
            "name": path.name,
            "sha256": _digest(path.read_bytes()),
            "size": path.stat().st_size,
        }
    assert stat.S_IMODE(output.stat().st_mode) == 0o444


def test_verify_rejects_missing_failed_foreign_or_modified_phase_evidence(tmp_path: Path) -> None:
    plan, plan_path, evidence, root = _setup(tmp_path)
    for phase in lab.PHASES:
        lab._write_new(
            evidence / lab.PHASE_OUTPUTS[phase],
            lab._phase_payload(plan, phase, {"machineGenerated": True}),
        )
    target = evidence / lab.PHASE_OUTPUTS["linux-nfs"]
    original = target.read_bytes()

    target.unlink()
    with pytest.raises(lab.ProtocolInteroperabilityLabError, match="unavailable"):
        lab.verify_evidence(
            plan_path=plan_path,
            candidate_index=Path(plan["releaseCandidate"]["indexPath"]),
            bundle_root=root,
            evidence_directory=evidence,
            output=evidence / lab.LIFECYCLE_NAME,
        )
    target.write_bytes(original)
    target.chmod(0o444)

    value = json.loads(target.read_text())
    value["planId"] = "e" * 64
    target.chmod(0o644)
    target.write_text(json.dumps(value) + "\n", encoding="utf-8")
    target.chmod(0o444)
    with pytest.raises(lab.ProtocolInteroperabilityLabError, match="contract"):
        lab.verify_evidence(
            plan_path=plan_path,
            candidate_index=Path(plan["releaseCandidate"]["indexPath"]),
            bundle_root=root,
            evidence_directory=evidence,
            output=evidence / lab.LIFECYCLE_NAME,
        )


def test_plan_rejects_drift_invalid_server_nonempty_evidence_and_replace(tmp_path: Path) -> None:
    plan, plan_path, evidence, root = _setup(tmp_path)
    value = json.loads(plan_path.read_text())
    value["server"] = "other-nas.lan"
    plan_path.chmod(0o600)
    plan_path.write_text(json.dumps(value) + "\n", encoding="utf-8")
    plan_path.chmod(0o400)
    with pytest.raises(lab.ProtocolInteroperabilityLabError, match="plan ID"):
        lab._load_plan(plan_path)

    candidate = Path(plan["releaseCandidate"]["indexPath"])
    second_evidence = tmp_path / "second-evidence"
    second_evidence.mkdir()
    (second_evidence / "existing").write_text("occupied", encoding="utf-8")
    with pytest.raises(lab.ProtocolInteroperabilityLabError, match="empty absolute"):
        lab.build_plan(
            candidate_index=candidate,
            bundle_root=root,
            server="echo-nas.lan",
            lab_share_id=str(uuid.UUID(int=20, version=4)),
            evidence_directory=second_evidence,
            output=tmp_path / "second" / "protocol-interoperability-lab-plan.json",
        )
    with pytest.raises(lab.ProtocolInteroperabilityLabError, match="server name"):
        lab.build_plan(
            candidate_index=candidate,
            bundle_root=root,
            server="https://echo-nas.lan/share",
            lab_share_id=str(uuid.UUID(int=20, version=4)),
            evidence_directory=evidence,
            output=tmp_path / "protocol-interoperability-lab-plan.json",
        )


def test_verify_rejects_candidate_or_operations_bundle_drift(tmp_path: Path) -> None:
    plan, plan_path, evidence, root = _setup(tmp_path)
    for phase in lab.PHASES:
        lab._write_new(
            evidence / lab.PHASE_OUTPUTS[phase],
            lab._phase_payload(plan, phase, {"machineGenerated": True}),
        )
    candidate = Path(plan["releaseCandidate"]["indexPath"])
    executor = root / "protocol_interoperability_lab.py"
    executor.write_bytes(executor.read_bytes() + b"\n# replaced\n")
    executor.chmod(0o755)

    with pytest.raises(
        lab.ProtocolInteroperabilityLabError, match="not from the release candidate"
    ):
        lab.verify_evidence(
            plan_path=plan_path,
            candidate_index=candidate,
            bundle_root=root,
            evidence_directory=evidence,
            output=evidence / lab.LIFECYCLE_NAME,
        )
