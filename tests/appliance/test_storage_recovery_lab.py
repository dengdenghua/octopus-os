from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any

import pytest

from deploy.appliance import storage_recovery_lab as lab

REPOSITORY = Path(__file__).resolve().parents[2]
OPERATIONS_ARTIFACT_ID = "9" * 16
IMAGE_REFERENCE = f"ghcr.io/echo-os/echo-os@sha256:{'7' * 64}"


def _candidate_index(tmp_path: Path) -> Path:
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
                    "artifactId": OPERATIONS_ARTIFACT_ID,
                    "sha256": "8" * 64,
                    "imageReference": IMAGE_REFERENCE,
                },
            },
        },
        "ciReleaseCandidateReady": True,
        "nasProductDeliveryReady": False,
        "physicalAcceptance": {
            "complete": False,
            "remainingGates": [lab.GATE],
        },
    }
    value["indexId"] = hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    path = tmp_path / "echo-delivery-release-evidence-index.json"
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o444)
    return path


def _bundle(tmp_path: Path) -> Path:
    root = tmp_path / f"echo-appliance-operations-{OPERATIONS_ARTIFACT_ID}"
    root.mkdir()
    records: dict[str, dict[str, Any]] = {}
    for name in ("storage_recovery_lab.py", "verify-running-appliance.py"):
        source = REPOSITORY / "deploy/appliance" / name
        destination = root / name
        shutil.copyfile(source, destination)
        destination.chmod(0o755)
        raw = destination.read_bytes()
        records[name] = {
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size": len(raw),
            "mode": "0755",
        }
    manifest = {
        "schemaVersion": 1,
        "artifact": {
            "id": OPERATIONS_ARTIFACT_ID,
            "name": root.name,
            "architectures": ["amd64", "arm64"],
            "imageReference": IMAGE_REFERENCE,
            "entrypoints": {"storageRecoveryLab": "./storage_recovery_lab.py plan|run"},
        },
        "files": records,
    }
    (root / "bundle-manifest.json").write_text(
        json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
    )
    (root / "bundle-manifest.json").chmod(0o644)
    return root


def _tools(tmp_path: Path) -> lab.LabTools:
    root = tmp_path / "tools"
    root.mkdir()
    paths: dict[str, Path] = {}
    for name in (
        "smartctl",
        "mdadm",
        "findmnt",
        "lsblk",
        "mount",
        "touch",
        "fallocate",
        "dd",
        "sync",
        "python3",
        "dpkg-query",
    ):
        path = root / name
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        path.chmod(0o755)
        paths[name] = path
    return lab.LabTools(
        smartctl=paths["smartctl"],
        mdadm=paths["mdadm"],
        findmnt=paths["findmnt"],
        lsblk=paths["lsblk"],
        mount=paths["mount"],
        touch=paths["touch"],
        fallocate=paths["fallocate"],
        dd=paths["dd"],
        sync=paths["sync"],
        python=paths["python3"],
        dpkg_query=paths["dpkg-query"],
    )


class StorageHarness:
    def __init__(self, tools: lab.LabTools) -> None:
        self.tools = tools
        self.phase = "plan"
        self.read_only = False
        self.fallocate_calls = 0
        self.rebuild_started = False
        self.devices = {
            "array": {"path": "/dev/md7", "majorMinor": "9:7", "sizeBytes": 8 << 30},
            "members": [
                {
                    "path": "/dev/sdb1",
                    "parentPath": "/dev/sdb",
                    "majorMinor": "8:17",
                    "sizeBytes": 8 << 30,
                    "identitySha256": "a" * 64,
                },
                {
                    "path": "/dev/sdc1",
                    "parentPath": "/dev/sdc",
                    "majorMinor": "8:33",
                    "sizeBytes": 8 << 30,
                    "identitySha256": "b" * 64,
                },
            ],
            "mountpoint": "",
        }

    def runner(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        name = Path(command[0]).name
        if name == "dpkg-query":
            return subprocess.CompletedProcess(command, 0, "8.7.3-1", "")
        if name == "mount":
            self.read_only = "remount,ro" in command
            return subprocess.CompletedProcess(command, 0, "", "")
        if name == "touch":
            rejected = self.read_only or self.phase == "volume-full"
            return subprocess.CompletedProcess(command, 1 if rejected else 0, "", "")
        if name == "fallocate":
            self.fallocate_calls += 1
            return subprocess.CompletedProcess(
                command, 0 if self.fallocate_calls == 1 else 1, "", "No space left on device"
            )
        if name == "dd":
            return subprocess.CompletedProcess(command, 1, "", "No space left on device")
        if name == "mdadm":
            self.rebuild_started = True
            return subprocess.CompletedProcess(command, 0, "", "")
        if name == "python3":
            result = {
                "nas_transfer": {
                    "writeExecuted": True,
                    "size": lab.NAS_TRANSFER_BYTES,
                    "recycleRestoreVerified": True,
                    "physicallyDeleted": False,
                    "sha256": "c" * 64,
                    "restoredSha256": "c" * 64,
                }
            }
            return subprocess.CompletedProcess(command, 0, json.dumps(result), "")
        return subprocess.CompletedProcess(command, 0, "", "")

    def array_probe(self, _array: Path) -> dict[str, Any]:
        if self.phase in {"plan", "baseline", "rebuild", "reboot", "recycle-restore"}:
            return {"healthy": True, "degraded": False, "active": 2, "recovering": False}
        if self.phase == "reconnect" and self.rebuild_started:
            return {"healthy": False, "degraded": False, "active": 2, "recovering": True}
        return {"healthy": False, "degraded": True, "active": 1, "recovering": False}

    def mount_probe(self, mountpoint: Path) -> dict[str, Any]:
        return {
            "target": str(mountpoint),
            "source": "/dev/md7",
            "filesystem": "ext4",
            "readOnly": self.read_only,
            "sizeBytes": 8 << 30,
            "availableBytes": 3 << 30,
        }

    @staticmethod
    def smart_probe(_members: Any) -> dict[str, Any]:
        return {"allPassed": True, "diskCount": 2, "disks": []}

    def member_probe(self, member: dict[str, Any]) -> bool:
        if member["path"] != "/dev/sdc1":
            return True
        return self.phase not in {"degraded", "readonly", "volume-full"}

    def device_verifier(
        self,
        _array: Path,
        _members: Any,
        mountpoint: Path,
        _tools: lab.LabTools,
        _runner: Any,
    ) -> dict[str, Any]:
        return {**self.devices, "mountpoint": str(mountpoint)}


def _fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[dict[str, Any], Path, StorageHarness, lab.LabTools, Path, Path]:
    monkeypatch.setattr(lab, "SEED_BYTES", 64 * 1024)
    monkeypatch.setattr(lab, "NAS_TRANSFER_BYTES", 1024 * 1024)
    monkeypatch.setattr(lab, "FILL_CHUNK_BYTES", 1024)
    monkeypatch.setattr(lab, "MIN_FILL_CHUNK_BYTES", 1024)
    candidate = _candidate_index(tmp_path)
    bundle = _bundle(tmp_path)
    tools = _tools(tmp_path)
    harness = StorageHarness(tools)
    mountpoint = tmp_path / "lab-volume"
    evidence = tmp_path / "evidence"
    mountpoint.mkdir()
    evidence.mkdir()
    marker = {
        "schemaVersion": 1,
        "kind": "echo.storage-recovery-lab-authorization",
        "disposable": True,
        "candidateIndexId": json.loads(candidate.read_text())["indexId"],
        "arrayDevice": "/dev/md7",
        "mountpoint": str(mountpoint.resolve()),
        "labVolumeId": str(uuid.uuid4()),
    }
    marker_path = mountpoint / lab.MARKER_NAME
    marker_path.write_text(json.dumps(marker, sort_keys=True) + "\n", encoding="utf-8")
    marker_path.chmod(0o444)
    os_release = tmp_path / "os-release"
    os_release.write_text('ID=debian\nVERSION_ID="13"\n', encoding="utf-8")
    plan_path = tmp_path / "storage-recovery-plan.json"
    boot_ids = iter((str(uuid.uuid4()), str(uuid.uuid4())))
    plan = lab.build_plan(
        candidate_index=candidate,
        bundle_root=bundle,
        array=Path("/dev/md7"),
        members=(Path("/dev/sdb1"), Path("/dev/sdc1")),
        sacrificial_member=Path("/dev/sdc1"),
        mountpoint=mountpoint,
        evidence_directory=evidence,
        nas_transfer_path="lab/recycle-probe.bin",
        base_url="http://127.0.0.1:8000",
        output=plan_path,
        tools=tools,
        runner=harness.runner,
        array_probe=harness.array_probe,
        mount_probe=harness.mount_probe,
        smart_probe=harness.smart_probe,
        device_verifier=harness.device_verifier,
        boot_id_reader=lambda: next(boot_ids),
        effective_uid=0,
        trusted_uid=os.getuid(),
        system_name="Linux",
        os_release=os_release,
    )
    harness.boot_id_reader = lambda: next(boot_ids)
    return plan, plan_path, harness, tools, os_release, evidence


def _run_phase(
    plan: dict[str, Any],
    plan_path: Path,
    harness: StorageHarness,
    tools: lab.LabTools,
    os_release: Path,
    phase: str,
) -> dict[str, Any]:
    harness.phase = phase
    return lab.run_phase(
        plan_path=plan_path,
        phase=phase,
        confirmation=plan["confirmations"][phase],
        wait_seconds=0,
        tools=tools,
        runner=harness.runner,
        array_probe=harness.array_probe,
        mount_probe=harness.mount_probe,
        smart_probe=harness.smart_probe,
        member_probe=harness.member_probe,
        device_verifier=harness.device_verifier,
        boot_id_reader=harness.boot_id_reader,
        effective_uid=0,
        trusted_uid=os.getuid(),
        system_name="Linux",
        os_release=os_release,
    )


def test_candidate_bound_storage_lab_completes_all_machine_evidence_phases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan, plan_path, harness, tools, os_release, evidence = _fixture(tmp_path, monkeypatch)

    for phase in lab.PHASES:
        report = _run_phase(plan, plan_path, harness, tools, os_release, phase)
        assert report == {
            "phase": phase,
            "planId": plan["planId"],
            "output": lab.PHASE_OUTPUTS[phase],
        }

    assert {path.name for path in evidence.iterdir()} == set(lab.PHASE_OUTPUTS.values())
    baseline = json.loads((evidence / "storage-baseline.log").read_text())
    assert baseline["details"]["smartHealthy"] is True
    assert baseline["details"]["seed"]["size"] == lab.SEED_BYTES
    assert json.loads((evidence / "storage-degraded.log").read_text())["details"] == {
        "activeMembers": 1,
        "dataReadable": True,
        "memberDisconnected": True,
        "raidDegraded": True,
    }
    assert (
        json.loads((evidence / "storage-volume-full.log").read_text())["details"]["enospcObserved"]
        is True
    )
    assert json.loads((evidence / "storage-recycle-restore.log").read_text())["details"] == {
        "bytes": lab.NAS_TRANSFER_BYTES,
        "finalState": "recoverable-trash",
        "restoreVerified": True,
        "sha256": "c" * 64,
    }


def test_storage_lab_rejects_skipped_phase_and_plan_tampering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan, plan_path, harness, tools, os_release, _evidence = _fixture(tmp_path, monkeypatch)

    with pytest.raises(lab.StorageRecoveryLabError, match="sequence"):
        _run_phase(plan, plan_path, harness, tools, os_release, "degraded")

    value = json.loads(plan_path.read_text())
    value["nasTransfer"]["path"] = "changed.bin"
    plan_path.chmod(0o600)
    plan_path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    plan_path.chmod(0o400)
    with pytest.raises(lab.StorageRecoveryLabError, match="plan or confirmation"):
        _run_phase(plan, plan_path, harness, tools, os_release, "baseline")


def test_storage_lab_rejects_replaced_candidate_executor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan, plan_path, harness, tools, os_release, _evidence = _fixture(tmp_path, monkeypatch)
    executor = Path(plan["bundleRoot"]) / "storage_recovery_lab.py"
    executor.write_bytes(executor.read_bytes() + b"\n# replaced\n")
    executor.chmod(0o755)

    with pytest.raises(lab.StorageRecoveryLabError, match="bundle tool bytes"):
        _run_phase(plan, plan_path, harness, tools, os_release, "baseline")


def test_storage_lab_requires_linux_root_and_authorization_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(lab, "SEED_BYTES", 64 * 1024)
    candidate = _candidate_index(tmp_path)
    bundle = _bundle(tmp_path)
    tools = _tools(tmp_path)
    harness = StorageHarness(tools)
    mountpoint = tmp_path / "lab-volume"
    evidence = tmp_path / "evidence"
    mountpoint.mkdir()
    evidence.mkdir()
    os_release = tmp_path / "os-release"
    os_release.write_text('ID=debian\nVERSION_ID="13"\n', encoding="utf-8")

    common = {
        "candidate_index": candidate,
        "bundle_root": bundle,
        "array": Path("/dev/md7"),
        "members": (Path("/dev/sdb1"), Path("/dev/sdc1")),
        "sacrificial_member": Path("/dev/sdc1"),
        "mountpoint": mountpoint,
        "evidence_directory": evidence,
        "nas_transfer_path": "lab/probe.bin",
        "base_url": "http://127.0.0.1:8000",
        "output": tmp_path / "plan.json",
        "tools": tools,
        "runner": harness.runner,
        "array_probe": harness.array_probe,
        "mount_probe": harness.mount_probe,
        "smart_probe": harness.smart_probe,
        "device_verifier": harness.device_verifier,
        "boot_id_reader": lambda: str(uuid.uuid4()),
        "trusted_uid": os.getuid(),
        "system_name": "Linux",
        "os_release": os_release,
    }
    with pytest.raises(lab.StorageRecoveryLabError, match="requires Linux root"):
        lab.build_plan(**common, effective_uid=1000)
    with pytest.raises(lab.StorageRecoveryLabError, match="dedicated and otherwise empty"):
        lab.build_plan(**common, effective_uid=0)
