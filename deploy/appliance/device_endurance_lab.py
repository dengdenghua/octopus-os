#!/usr/bin/env python3
"""Run the candidate-bound x86/ARM cold-boot, soak and hard-power lab."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess  # nosec B404
import sys
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit, urlunsplit

try:
    from deploy.appliance import operations_systemd as systemd
    from deploy.appliance import operations_systemd_lab as operations_lab
except ModuleNotFoundError:
    import operations_systemd as systemd
    import operations_systemd_lab as operations_lab

SCHEMA_VERSION = 1
X86_GATE = "physical_x86_64_install_and_cold_boot"
ARM_GATE = "supported_arm64_hardware_install_and_cold_boot"
GATES = (X86_GATE, ARM_GATE)
ARCHITECTURES = {"x86_64": (X86_GATE, "amd64"), "aarch64": (ARM_GATE, "arm64")}
PHASES = ("baseline", "soak", "arm-power-cut", "recovered")
PHASE_OUTPUTS = {
    "baseline": "device-baseline.log",
    "soak": "device-soak.log",
    "arm-power-cut": "device-power-cut-armed.log",
    "recovered": "device-recovered.log",
}
MIN_SOAK_SECONDS = 24 * 60 * 60
MAX_FIRST_BOOT_UPTIME_SECONDS = 6 * 60 * 60
NAS_TRANSFER_BYTES = 1024 * 1024 * 1024
MAX_EVIDENCE_BYTES = 8 * 1024 * 1024
MAX_FAMILY_FIXTURE_BYTES = 32 * 1024
SAFE_CONTAINER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
INSTALL_AUTH = re.compile(
    r"^ECHO_INSTALL_BUNDLE_AUTHENTICATED action=install version=(\S+) "
    r"manifest=([0-9a-f]{64}) source=([0-9a-f]{64})$"
)
INSTALL_LOCKED = re.compile(
    r"^ECHO_INSTALL_TARGET_LOCKED target=(/dev/[A-Za-z0-9._/-]+) "
    r"device-id=([0-9]+:[0-9]+) identity=stable$"
)
INSTALL_COMPLETE = re.compile(
    r"^ECHO_INSTALL_COMPLETE target=(/dev/[A-Za-z0-9._/-]+) version=(\S+) "
    r"source=([0-9a-f]{64}) home=(/dev/[A-Za-z0-9._/-]+) "
    r"data=luks2-tpm2-signed-pcr11-recovery$"
)


class DeviceEnduranceLabError(RuntimeError):
    """The physical device endurance lab cannot proceed safely."""


@dataclass(frozen=True)
class LabTools:
    python: Path = Path("/usr/bin/python3")
    docker: Path = Path("/usr/bin/docker")
    journalctl: Path = Path("/usr/bin/journalctl")
    logger: Path = Path("/usr/bin/logger")
    sync: Path = Path("/usr/bin/sync")
    dpkg_query: Path = Path("/usr/bin/dpkg-query")


DEFAULT_TOOLS = LabTools()
CommandRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]
BootIdReader = Callable[[], str]
Clock = Callable[[], int]
UptimeReader = Callable[[], float]
DeviceIdentityReader = Callable[[], str]
JournalProbe = Callable[[str, str, LabTools, CommandRunner], Mapping[str, Any]]


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603
        command,
        check=False,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=3600,
        env={
            **os.environ,
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
        },
    )


def _canonical(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_json(path: Path, label: str, *, trusted_uid: int, exact_mode: int) -> dict[str, Any]:
    raw = systemd._safe_regular(
        path,
        label,
        maximum=systemd.MAX_PLAN_BYTES,
        trusted_uid=trusted_uid,
        private=exact_mode & 0o077 == 0,
        exact_mode=exact_mode,
    )
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=systemd._reject_duplicate_keys)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise DeviceEnduranceLabError(f"{label} is not strict JSON") from exc
    if not isinstance(value, dict):
        raise DeviceEnduranceLabError(f"{label} is not an object")
    return value


def _write_new(path: Path, value: Mapping[str, Any], *, trusted_uid: int, mode: int) -> None:
    if path.exists() or path.is_symlink() or path.parent.is_symlink():
        raise DeviceEnduranceLabError("device lab output must be a new regular file")
    parent = path.parent.resolve(strict=True)
    systemd._assert_owned_directory(parent, "device lab output directory", trusted_uid=trusted_uid)
    descriptor = os.open(
        parent / path.name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
        mode,
    )
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            descriptor = -1
            handle.write(_canonical(value))
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _boot_id() -> str:
    try:
        value = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
        parsed = uuid.UUID(value)
    except (OSError, UnicodeError, ValueError) as exc:
        raise DeviceEnduranceLabError("kernel boot identity is unavailable") from exc
    if str(parsed) != value:
        raise DeviceEnduranceLabError("kernel boot identity is not canonical")
    return value


def _uptime() -> float:
    try:
        value = float(Path("/proc/uptime").read_text(encoding="ascii").split()[0])
    except (OSError, UnicodeError, ValueError, IndexError) as exc:
        raise DeviceEnduranceLabError("kernel uptime is unavailable") from exc
    if value < 0:
        raise DeviceEnduranceLabError("kernel uptime is invalid")
    return value


def _device_identity() -> str:
    try:
        value = Path("/etc/machine-id").read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as exc:
        raise DeviceEnduranceLabError("machine identity is unavailable") from exc
    if re.fullmatch(r"[0-9a-f]{32}", value) is None:
        raise DeviceEnduranceLabError("machine identity is invalid")
    return _sha256(value.encode())


def _validated_tools(tools: LabTools, *, trusted_uid: int) -> None:
    for tool in (
        tools.python,
        tools.docker,
        tools.journalctl,
        tools.logger,
        tools.sync,
        tools.dpkg_query,
    ):
        systemd._safe_regular(
            tool,
            f"physical device lab tool {tool.name}",
            maximum=64 * 1024 * 1024,
            trusted_uid=trusted_uid,
            private=False,
            exact_mode=0o755,
        )


def _safe_relative(value: str) -> str:
    raw = value.strip().strip("/")
    parts = PurePosixPath(raw).parts
    if (
        not raw
        or len(raw) > 256
        or "\\" in raw
        or "\x00" in raw
        or any(part in {"", ".", "..", ".echo-trash"} for part in parts)
    ):
        raise DeviceEnduranceLabError("NAS transfer test path is invalid")
    return PurePosixPath(*parts).as_posix()


def _installer_record(path: Path, *, trusted_uid: int) -> dict[str, Any]:
    raw = systemd._safe_regular(
        path,
        "private installer transcript",
        maximum=MAX_EVIDENCE_BYTES,
        trusted_uid=trusted_uid,
        private=True,
        exact_mode=0o400,
    )
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeError as exc:
        raise DeviceEnduranceLabError("private installer transcript is not UTF-8") from exc
    auth = [match for line in lines if (match := INSTALL_AUTH.fullmatch(line))]
    locked = [match for line in lines if (match := INSTALL_LOCKED.fullmatch(line))]
    complete = [match for line in lines if (match := INSTALL_COMPLETE.fullmatch(line))]
    readback = lines.count(
        "  verified: exact uncompressed image bytes by direct post-flush readback"
    )
    if len(auth) != 1 or len(locked) != 1 or len(complete) != 1 or readback != 1:
        raise DeviceEnduranceLabError(
            "installer transcript lacks one complete authenticated install"
        )
    if (
        auth[0].group(1) != complete[0].group(2)
        or auth[0].group(3) != complete[0].group(3)
        or locked[0].group(1) != complete[0].group(1)
    ):
        raise DeviceEnduranceLabError("installer transcript identity changed during installation")
    return {
        "path": str(path.resolve(strict=True)),
        "sha256": _sha256(raw),
        "size": len(raw),
        "imageVersion": auth[0].group(1),
        "manifestSha256": auth[0].group(2),
        "sourceSha256": auth[0].group(3),
        "targetIdentitySha256": _sha256(f"{locked[0].group(1)}\0{locked[0].group(2)}".encode()),
        "postWriteReadbackVerified": True,
        "dataProtection": "luks2-tpm2-signed-pcr11-recovery",
    }


def _bundle_identity(
    bundle_root: Path, candidate: Mapping[str, str], *, trusted_uid: int
) -> dict[str, Any]:
    manifest_raw = systemd._safe_regular(
        bundle_root / "bundle-manifest.json",
        "operations bundle manifest",
        maximum=systemd.MAX_PLAN_BYTES,
        trusted_uid=trusted_uid,
        private=False,
        exact_mode=0o644,
    )
    try:
        manifest = json.loads(
            manifest_raw.decode("utf-8"), object_pairs_hook=systemd._reject_duplicate_keys
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise DeviceEnduranceLabError("operations bundle manifest is not strict JSON") from exc
    artifact = manifest.get("artifact") if isinstance(manifest, dict) else None
    files = manifest.get("files") if isinstance(manifest, dict) else None
    expected = {
        "device_endurance_lab.py": (
            "deviceEnduranceLab",
            "./device_endurance_lab.py plan|run",
        ),
        "verify-running-appliance.py": (None, None),
    }
    if (
        not isinstance(manifest, dict)
        or set(manifest) != {"schemaVersion", "artifact", "files"}
        or manifest.get("schemaVersion") != 1
        or not isinstance(artifact, dict)
        or artifact.get("id") != candidate["operationsArtifactId"]
        or artifact.get("name") != bundle_root.name
        or artifact.get("imageReference") != candidate["immutableReference"]
        or not isinstance(artifact.get("entrypoints"), dict)
        or not isinstance(files, dict)
    ):
        raise DeviceEnduranceLabError("device lab bundle is not from the release candidate")
    result: dict[str, Any] = {
        "artifactId": candidate["operationsArtifactId"],
        "archiveSha256": candidate["operationsArchiveSha256"],
        "imageReference": candidate["immutableReference"],
        "manifestSha256": _sha256(manifest_raw),
    }
    for name, (entrypoint, command) in expected.items():
        raw = systemd._safe_regular(
            bundle_root / name,
            f"candidate bundle tool {name}",
            maximum=16 * 1024 * 1024,
            trusted_uid=trusted_uid,
            private=False,
            exact_mode=0o755,
        )
        record = files.get(name)
        if (
            not isinstance(record, dict)
            or record != {"sha256": _sha256(raw), "size": len(raw), "mode": "0755"}
            or (entrypoint is not None and artifact["entrypoints"].get(entrypoint) != command)
        ):
            raise DeviceEnduranceLabError("device lab bundle tool bytes are unbound")
        result[
            "deviceEnduranceLabSha256"
            if name == "device_endurance_lab.py"
            else "runningVerifierSha256"
        ] = _sha256(raw)
    return result


def _architecture(machine: str) -> tuple[str, str, str]:
    normalized = machine.casefold()
    if normalized not in ARCHITECTURES:
        raise DeviceEnduranceLabError("device lab requires x86_64 or aarch64 hardware")
    gate, verifier_arch = ARCHITECTURES[normalized]
    profile_arch = "x86_64" if normalized == "x86_64" else "arm64"
    return gate, profile_arch, verifier_arch


def _container_image(container: str, tools: LabTools, runner: CommandRunner) -> str:
    completed = runner([str(tools.docker), "inspect", "--format", "{{.Config.Image}}", container])
    value = completed.stdout.strip()
    if completed.returncode != 0 or "\n" in value or not value:
        raise DeviceEnduranceLabError("running Echo container image is unavailable")
    return value


def _normalized_origin(base_url: str, gate: str) -> str:
    parsed = urlsplit(base_url)
    hostname = parsed.hostname or ""
    if (
        parsed.scheme not in {"http", "https"}
        or not hostname
        or len(hostname) > 253
        or re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", hostname) is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise DeviceEnduranceLabError("device lab appliance URL must be one loopback origin")
    if gate == X86_GATE and parsed.scheme != "https":
        raise DeviceEnduranceLabError("x86 delivery lab requires the production HTTPS origin")
    if parsed.scheme == "http" and hostname not in {"127.0.0.1", "localhost"}:
        raise DeviceEnduranceLabError("plain HTTP device labs are restricted to loopback")
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def _family_fixture_record(path: Path, *, trusted_uid: int) -> dict[str, Any]:
    raw = systemd._safe_regular(
        path,
        "private family isolation fixture",
        maximum=MAX_FAMILY_FIXTURE_BYTES,
        trusted_uid=trusted_uid,
        private=True,
        exact_mode=0o400,
    )
    return {
        "path": str(path.resolve(strict=True)),
        "sha256": _sha256(raw),
        "size": len(raw),
        "mode": "0400",
    }


def build_plan(
    *,
    candidate_index: Path,
    bundle_root: Path,
    installer_log: Path,
    evidence_directory: Path,
    nas_transfer_path: str,
    family_isolation_fixture: Path,
    base_url: str,
    main_container: str,
    proxy_container: str,
    output: Path,
    tools: LabTools = DEFAULT_TOOLS,
    runner: CommandRunner = _run,
    boot_id_reader: BootIdReader = _boot_id,
    uptime_reader: UptimeReader = _uptime,
    device_identity_reader: DeviceIdentityReader = _device_identity,
    effective_uid: int | None = None,
    trusted_uid: int = 0,
    system_name: str | None = None,
    machine: str | None = None,
    os_release: Path = Path("/etc/os-release"),
) -> dict[str, Any]:
    uid = os.geteuid() if effective_uid is None else effective_uid
    host_system = os.uname().sysname if system_name is None else system_name
    if uid != 0 or host_system != "Linux":
        raise DeviceEnduranceLabError("physical device lab plan requires Linux root")
    if (
        SAFE_CONTAINER.fullmatch(main_container) is None
        or SAFE_CONTAINER.fullmatch(proxy_container) is None
    ):
        raise DeviceEnduranceLabError("device lab container names are invalid")
    _validated_tools(tools, trusted_uid=trusted_uid)
    gate, profile_arch, verifier_arch = _architecture(
        platform.machine() if machine is None else machine
    )
    candidate = operations_lab._candidate_identity(candidate_index, trusted_uid=trusted_uid)
    bundle = _bundle_identity(bundle_root, candidate, trusted_uid=trusted_uid)
    release_version = candidate["releaseTag"].removeprefix("echo-appliance-v")
    installer = _installer_record(installer_log, trusted_uid=trusted_uid)
    family_fixture = _family_fixture_record(
        family_isolation_fixture,
        trusted_uid=trusted_uid,
    )
    if installer["imageVersion"] != release_version:
        raise DeviceEnduranceLabError("installer transcript belongs to another release version")
    evidence_root = evidence_directory.resolve(strict=True)
    systemd._assert_owned_directory(
        evidence_root, "device lab evidence directory", trusted_uid=trusted_uid
    )
    if any(evidence_root.iterdir()):
        raise DeviceEnduranceLabError("device lab evidence directory must start empty")
    first_boot_uptime = uptime_reader()
    if not 0 <= first_boot_uptime <= MAX_FIRST_BOOT_UPTIME_SECONDS:
        raise DeviceEnduranceLabError("device lab plan must be created during the first six hours")
    if _container_image(main_container, tools, runner) != candidate["immutableReference"]:
        raise DeviceEnduranceLabError("running Echo container is not the release candidate image")
    platform_record = {
        **operations_lab._read_os_release(os_release),
        "omvVersion": operations_lab._omv_version(
            operations_lab.LabTools(dpkg_query=tools.dpkg_query), runner
        ),
        "architecture": profile_arch,
    }
    device_identity = device_identity_reader()
    baseline_boot_id = boot_id_reader()
    try:
        canonical_boot_id = str(uuid.UUID(baseline_boot_id))
    except ValueError as exc:
        raise DeviceEnduranceLabError("baseline boot identity is invalid") from exc
    if SHA256.fullmatch(device_identity) is None or canonical_boot_id != baseline_boot_id:
        raise DeviceEnduranceLabError("device or boot identity is invalid")
    payload: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "echo.device-endurance-physical-lab-plan",
        "gate": gate,
        "releaseCandidate": candidate,
        "bundleRoot": str(bundle_root.resolve(strict=True)),
        "operationsBundle": bundle,
        "platform": platform_record,
        "deviceIdentitySha256": device_identity,
        "installer": installer,
        "evidenceDirectory": str(evidence_root),
        "appliance": {
            "baseUrl": _normalized_origin(base_url, gate),
            "mainContainer": main_container,
            "proxyContainer": proxy_container,
            "expectedArchitecture": verifier_arch,
            "nasTransferPath": _safe_relative(nas_transfer_path),
            "nasTransferBytes": NAS_TRANSFER_BYTES,
            "familyIsolationFixture": family_fixture,
        },
        "baselineBootId": baseline_boot_id,
        "firstBootUptimeSeconds": first_boot_uptime,
        "minimumSoakSeconds": MIN_SOAK_SECONDS,
        "phases": list(PHASES),
    }
    payload["planId"] = _sha256(_canonical(payload))
    payload["confirmations"] = {
        phase: f"RUN ECHO DEVICE ENDURANCE LAB {phase} {payload['planId']}" for phase in PHASES
    }
    _write_new(output, payload, trusted_uid=trusted_uid, mode=0o400)
    return payload


def _verify_plan_identity(plan: Mapping[str, Any], confirmation: str, phase: str) -> None:
    expected_keys = {
        "schemaVersion",
        "kind",
        "gate",
        "releaseCandidate",
        "bundleRoot",
        "operationsBundle",
        "platform",
        "deviceIdentitySha256",
        "installer",
        "evidenceDirectory",
        "appliance",
        "baselineBootId",
        "firstBootUptimeSeconds",
        "minimumSoakSeconds",
        "phases",
        "planId",
        "confirmations",
    }
    unsigned = dict(plan)
    confirmations = unsigned.pop("confirmations", None)
    plan_id = unsigned.pop("planId", None)
    expected_confirmations = {
        item: f"RUN ECHO DEVICE ENDURANCE LAB {item} {plan_id}" for item in PHASES
    }
    if (
        set(plan) != expected_keys
        or plan.get("schemaVersion") != SCHEMA_VERSION
        or plan.get("kind") != "echo.device-endurance-physical-lab-plan"
        or plan.get("gate") not in GATES
        or plan.get("phases") != list(PHASES)
        or plan.get("minimumSoakSeconds") != MIN_SOAK_SECONDS
        or confirmations != expected_confirmations
        or confirmations.get(phase) != confirmation
        or plan_id != _sha256(_canonical(unsigned))
    ):
        raise DeviceEnduranceLabError("device lab plan or confirmation is invalid")


def _phase_dependencies(root: Path, phase: str, *, plan_id: str, trusted_uid: int) -> None:
    index = PHASES.index(phase)
    required = {PHASE_OUTPUTS[item] for item in PHASES[:index]}
    forbidden = {PHASE_OUTPUTS[item] for item in PHASES[index:]}
    actual = {name for name in required | forbidden if (root / name).exists()}
    if not required <= actual or actual & forbidden:
        raise DeviceEnduranceLabError("device lab evidence sequence is incomplete or stale")
    for name in required:
        value = _read_json(
            root / name,
            f"prior device lab evidence {name}",
            trusted_uid=trusted_uid,
            exact_mode=0o444,
        )
        if (
            set(value) != {"schemaVersion", "kind", "planId", "evidence", "passed", "details"}
            or value.get("schemaVersion") != SCHEMA_VERSION
            or value.get("kind") != "echo.device-endurance-physical-lab-evidence"
            or value.get("planId") != plan_id
            or value.get("evidence") != name
            or value.get("passed") is not True
            or not isinstance(value.get("details"), dict)
        ):
            raise DeviceEnduranceLabError("prior device lab evidence is invalid")


def _write_phase(
    root: Path, phase: str, plan_id: str, details: Mapping[str, Any], *, trusted_uid: int
) -> None:
    _write_new(
        root / PHASE_OUTPUTS[phase],
        {
            "schemaVersion": SCHEMA_VERSION,
            "kind": "echo.device-endurance-physical-lab-evidence",
            "planId": plan_id,
            "evidence": PHASE_OUTPUTS[phase],
            "passed": True,
            "details": dict(details),
        },
        trusted_uid=trusted_uid,
        mode=0o444,
    )


def _verify_running_result(value: Any, *, expected_arch: str) -> dict[str, Any]:
    transfer = value.get("nas_transfer") if isinstance(value, dict) else None
    family = value.get("family_isolation") if isinstance(value, dict) else None
    if (
        not isinstance(value, dict)
        or value.get("bundle_verified") is not True
        or value.get("bundle_dirty") is not False
        or value.get("login") != 200
        or value.get("workbench") != 200
        or value.get("architecture") != expected_arch
        or value.get("main_has_docker_socket") is not False
        or value.get("proxy_network_internal") is not True
        or value.get("main_effective_capabilities") != 0
        or value.get("proxy_effective_capabilities") != 0
        or value.get("no_new_privileges") is not True
        or value.get("approval") != 200
        or value.get("approval_replay") != 403
        or value.get("protected_stop") != 403
        or value.get("audit_verify") != 200
        or not isinstance(transfer, dict)
        or transfer.get("writeExecuted") is not True
        or transfer.get("size") != NAS_TRANSFER_BYTES
        or transfer.get("restartVerified") is not True
        or transfer.get("cancelVerified") is not True
        or transfer.get("recycleRestoreVerified") is not True
        or transfer.get("physicallyDeleted") is not False
        or transfer.get("restoredSha256") != transfer.get("sha256")
        or not isinstance(transfer.get("sha256"), str)
        or SHA256.fullmatch(transfer["sha256"]) is None
        or not isinstance(family, dict)
        or family.get("verified") is not True
        or family.get("memberCount") != 2
        or family.get("accountDirectoryIsolated") is not True
        or family.get("fileProjectionVerified") is not True
        or family.get("photoProjectionVerified") is not True
        or family.get("memberManagementRejected") is not True
        or family.get("secretsReturned") is not False
        or not isinstance(family.get("identitySetSha256"), str)
        or SHA256.fullmatch(family["identitySetSha256"]) is None
        or not isinstance(family.get("policySetSha256"), str)
        or SHA256.fullmatch(family["policySetSha256"]) is None
    ):
        raise DeviceEnduranceLabError("running appliance did not pass the device delivery probe")
    return {
        "bundleVerified": True,
        "administratorLoginReady": True,
        "fileLifecycleVerified": True,
        "agentWorkbenchVerified": True,
        "oneGiBTransferVerified": True,
        "containerRestartResumeVerified": True,
        "dockerControlApprovalVerified": True,
        "familyMemberIsolationVerified": True,
        "familyIdentitySetSha256": family["identitySetSha256"],
        "familyPolicySetSha256": family["policySetSha256"],
        "runtimeArchitecture": expected_arch,
        "transferSha256": transfer["sha256"],
    }


def _running_probe(
    plan: Mapping[str, Any], tools: LabTools, runner: CommandRunner
) -> dict[str, Any]:
    appliance = plan["appliance"]
    path_label = appliance["nasTransferPath"] or "ROOT"
    confirmation = (
        f"VERIFY ECHO NAS TRANSFER {appliance['nasTransferBytes']} {path_label} "
        f"ON {appliance['baseUrl']} AND RESTART {appliance['mainContainer']}"
    )
    command = [
        str(tools.python),
        str(Path(plan["bundleRoot"]) / "verify-running-appliance.py"),
        "--base-url",
        appliance["baseUrl"],
        "--main-container",
        appliance["mainContainer"],
        "--proxy-container",
        appliance["proxyContainer"],
        "--expected-arch",
        appliance["expectedArchitecture"],
        "--require-clean-bundle",
        "--require-omv",
        "--nas-transfer-test-bytes",
        str(appliance["nasTransferBytes"]),
        "--nas-transfer-test-path",
        appliance["nasTransferPath"],
        "--nas-transfer-write-confirm",
        confirmation,
        "--nas-transfer-restart-main",
        "--require-nas-transfer",
        "--family-isolation-fixture",
        appliance["familyIsolationFixture"]["path"],
        "--require-family-isolation",
    ]
    completed = runner(command)
    if (
        completed.returncode != 0
        or len(completed.stdout.encode("utf-8", "replace")) > MAX_EVIDENCE_BYTES
    ):
        raise DeviceEnduranceLabError("running appliance device verification failed")
    try:
        value = json.loads(completed.stdout, object_pairs_hook=systemd._reject_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise DeviceEnduranceLabError("running appliance verifier returned invalid JSON") from exc
    return _verify_running_result(value, expected_arch=appliance["expectedArchitecture"])


def _journal_probe(
    previous_boot_id: str,
    plan_id: str,
    tools: LabTools,
    runner: CommandRunner,
) -> dict[str, Any]:
    marker = f"ECHO_DEVICE_HARD_POWER_CUT_ARMED plan={plan_id} boot={previous_boot_id}"
    completed = runner(
        [
            str(tools.journalctl),
            "--boot",
            previous_boot_id,
            "--output=cat",
            "--no-pager",
        ]
    )
    if (
        completed.returncode != 0
        or len(completed.stdout.encode("utf-8", "replace")) > 64 * 1024 * 1024
    ):
        raise DeviceEnduranceLabError("previous persistent boot journal is unavailable")
    lines = completed.stdout.splitlines()
    intent_count = lines.count(marker)
    clean_tokens = (
        "Reached target System Power Off",
        "Reached target System Reboot",
        "systemd-shutdown",
    )
    clean_shutdown = any(token in line for token in clean_tokens for line in lines)
    return {
        "persistentJournalAvailable": True,
        "powerCutIntentFound": intent_count == 1,
        "cleanShutdownFound": clean_shutdown,
    }


def run_phase(
    *,
    plan_path: Path,
    phase: str,
    confirmation: str,
    tools: LabTools = DEFAULT_TOOLS,
    runner: CommandRunner = _run,
    boot_id_reader: BootIdReader = _boot_id,
    clock_ns: Clock = time.time_ns,
    uptime_reader: UptimeReader = _uptime,
    device_identity_reader: DeviceIdentityReader = _device_identity,
    journal_probe: JournalProbe = _journal_probe,
    effective_uid: int | None = None,
    trusted_uid: int = 0,
    system_name: str | None = None,
    os_release: Path = Path("/etc/os-release"),
) -> dict[str, Any]:
    if phase not in PHASES:
        raise DeviceEnduranceLabError("device lab phase is invalid")
    uid = os.geteuid() if effective_uid is None else effective_uid
    host_system = os.uname().sysname if system_name is None else system_name
    if uid != 0 or host_system != "Linux":
        raise DeviceEnduranceLabError("physical device lab phase requires Linux root")
    _validated_tools(tools, trusted_uid=trusted_uid)
    plan = _read_json(
        plan_path, "physical device endurance lab plan", trusted_uid=trusted_uid, exact_mode=0o400
    )
    _verify_plan_identity(plan, confirmation, phase)
    candidate = operations_lab._candidate_identity(
        Path(plan["releaseCandidate"]["indexPath"]), trusted_uid=trusted_uid
    )
    bundle_root = Path(plan["bundleRoot"])
    if (
        not bundle_root.is_absolute()
        or candidate != plan["releaseCandidate"]
        or _bundle_identity(bundle_root, candidate, trusted_uid=trusted_uid)
        != plan["operationsBundle"]
        or _installer_record(Path(plan["installer"]["path"]), trusted_uid=trusted_uid)
        != plan["installer"]
        or _family_fixture_record(
            Path(plan["appliance"]["familyIsolationFixture"]["path"]),
            trusted_uid=trusted_uid,
        )
        != plan["appliance"]["familyIsolationFixture"]
        or device_identity_reader() != plan["deviceIdentitySha256"]
    ):
        raise DeviceEnduranceLabError("device lab candidate, bundle, installer or device drifted")
    current_platform = {
        **operations_lab._read_os_release(os_release),
        "omvVersion": operations_lab._omv_version(
            operations_lab.LabTools(dpkg_query=tools.dpkg_query), runner
        ),
        "architecture": plan["platform"]["architecture"],
    }
    if current_platform != plan["platform"]:
        raise DeviceEnduranceLabError("device lab platform drifted")
    appliance = plan["appliance"]
    if (
        _container_image(appliance["mainContainer"], tools, runner)
        != candidate["immutableReference"]
    ):
        raise DeviceEnduranceLabError("running Echo container drifted from the release candidate")
    root = Path(plan["evidenceDirectory"]).resolve(strict=True)
    _phase_dependencies(root, phase, plan_id=plan["planId"], trusted_uid=trusted_uid)
    current_boot = boot_id_reader()
    try:
        canonical_current_boot = str(uuid.UUID(current_boot))
    except ValueError as exc:
        raise DeviceEnduranceLabError("current boot identity is invalid") from exc
    if canonical_current_boot != current_boot:
        raise DeviceEnduranceLabError("current boot identity is invalid")
    now_ns = clock_ns()
    if not isinstance(now_ns, int) or isinstance(now_ns, bool) or now_ns <= 0:
        raise DeviceEnduranceLabError("device lab clock is invalid")

    if phase == "baseline":
        if (
            current_boot != plan["baselineBootId"]
            or uptime_reader() > MAX_FIRST_BOOT_UPTIME_SECONDS
        ):
            raise DeviceEnduranceLabError(
                "baseline no longer belongs to the first cold boot window"
            )
        details = {
            "installerCompleted": True,
            "installerSha256": plan["installer"]["sha256"],
            "postWriteReadbackVerified": True,
            "firstColdBootHealthy": True,
            "bootId": current_boot,
            "observedAtNs": now_ns,
            "deviceIdentitySha256": plan["deviceIdentitySha256"],
            "appliance": _running_probe(plan, tools, runner),
        }
    elif phase == "soak":
        baseline = _read_json(
            root / PHASE_OUTPUTS["baseline"],
            "device baseline evidence",
            trusted_uid=trusted_uid,
            exact_mode=0o444,
        )
        elapsed_ns = now_ns - int(baseline["details"]["observedAtNs"])
        if current_boot != baseline["details"]["bootId"] or elapsed_ns < MIN_SOAK_SECONDS * 10**9:
            raise DeviceEnduranceLabError("device has not completed 24 hours on one boot")
        details = {
            "continuousRunStable": True,
            "sameBoot": True,
            "durationSeconds": elapsed_ns // 10**9,
            "bootId": current_boot,
            "observedAtNs": now_ns,
            "appliance": _running_probe(plan, tools, runner),
        }
    elif phase == "arm-power-cut":
        soak = _read_json(
            root / PHASE_OUTPUTS["soak"],
            "device soak evidence",
            trusted_uid=trusted_uid,
            exact_mode=0o444,
        )
        if current_boot != soak["details"]["bootId"]:
            raise DeviceEnduranceLabError("device rebooted before the physical power-cut phase")
        marker = f"ECHO_DEVICE_HARD_POWER_CUT_ARMED plan={plan['planId']} boot={current_boot}"
        logged = runner([str(tools.logger), "--tag", "echo-device-lab", "--", marker])
        synced = runner([str(tools.journalctl), "--sync"])
        storage_synced = runner([str(tools.sync)])
        if logged.returncode != 0 or synced.returncode != 0 or storage_synced.returncode != 0:
            raise DeviceEnduranceLabError("power-cut intent could not be durably recorded")
        details = {
            "physicalPowerCutArmed": True,
            "bootId": current_boot,
            "intentSha256": _sha256(marker.encode()),
            "observedAtNs": now_ns,
            "nextAction": "physically-remove-and-restore-power",
        }
    else:
        armed = _read_json(
            root / PHASE_OUTPUTS["arm-power-cut"],
            "device power-cut evidence",
            trusted_uid=trusted_uid,
            exact_mode=0o444,
        )
        previous_boot = armed["details"]["bootId"]
        journal = dict(journal_probe(previous_boot, plan["planId"], tools, runner))
        if (
            current_boot == previous_boot
            or journal.get("persistentJournalAvailable") is not True
            or journal.get("powerCutIntentFound") is not True
            or journal.get("cleanShutdownFound") is not False
        ):
            raise DeviceEnduranceLabError("a real unclean physical power cycle was not proven")
        details = {
            "hardPowerCycleRecovered": True,
            "bootIdChanged": True,
            "previousBootId": previous_boot,
            "currentBootId": current_boot,
            "uncleanShutdownVerified": True,
            "observedAtNs": now_ns,
            "journal": journal,
            "appliance": _running_probe(plan, tools, runner),
        }
    _write_phase(root, phase, plan["planId"], details, trusted_uid=trusted_uid)
    return {"phase": phase, "planId": plan["planId"], "output": PHASE_OUTPUTS[phase]}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan = subparsers.add_parser("plan")
    plan.add_argument("--candidate-index", type=Path, required=True)
    plan.add_argument("--bundle-root", type=Path, required=True)
    plan.add_argument("--installer-log", type=Path, required=True)
    plan.add_argument("--evidence-directory", type=Path, required=True)
    plan.add_argument("--nas-transfer-path", required=True)
    plan.add_argument("--family-isolation-fixture", type=Path, required=True)
    plan.add_argument("--base-url", default="http://127.0.0.1:8000")
    plan.add_argument("--main-container", default="echo-os")
    plan.add_argument("--proxy-container", default="echo-docker-control")
    plan.add_argument("--output", type=Path, required=True)
    run = subparsers.add_parser("run")
    run.add_argument("--plan", type=Path, required=True)
    run.add_argument("--phase", choices=PHASES, required=True)
    run.add_argument("--confirm", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "plan":
            plan = build_plan(
                candidate_index=args.candidate_index,
                bundle_root=args.bundle_root,
                installer_log=args.installer_log,
                evidence_directory=args.evidence_directory,
                nas_transfer_path=args.nas_transfer_path,
                family_isolation_fixture=args.family_isolation_fixture,
                base_url=args.base_url,
                main_container=args.main_container,
                proxy_container=args.proxy_container,
                output=args.output,
            )
            print(
                "ECHO_DEVICE_ENDURANCE_LAB_PLAN_READY "
                f"gate={plan['gate']} candidate={plan['releaseCandidate']['indexId']} "
                f"plan={plan['planId']} phases={len(plan['phases'])}"
            )
            for phase in PHASES:
                print(f"{phase}: {plan['confirmations'][phase]}")
            return 0
        report = run_phase(
            plan_path=args.plan,
            phase=args.phase,
            confirmation=args.confirm,
        )
    except (
        OSError,
        KeyError,
        TypeError,
        ValueError,
        subprocess.SubprocessError,
        DeviceEnduranceLabError,
        operations_lab.OperationsSystemdLabError,
        systemd.OperationsSystemdError,
    ) as exc:
        print(f"Echo device endurance physical lab failed: {exc}", file=sys.stderr)
        return 1
    print(
        "ECHO_DEVICE_ENDURANCE_LAB_PHASE_OK "
        f"phase={report['phase']} plan={report['planId']} output={report['output']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
