#!/usr/bin/env python3
"""Run the destructive Debian 13/OMV operations-systemd physical lab in phases."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess  # nosec B404
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from deploy.appliance import operations_systemd as systemd
except ModuleNotFoundError:
    import operations_systemd as systemd

SCHEMA_VERSION = 2
MAX_EVIDENCE_BYTES = 2 * 1024 * 1024 * 1024
OMV_VERSION = re.compile(r"^8\.[0-9]+(?:\.[0-9]+)?(?:[-+~][0-9A-Za-z.+~-]+)?$")
SHA1 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
ARTIFACT_ID = re.compile(r"^[0-9a-f]{16}$")
PRESERVATION_LABELS = (
    "deviceState",
    "NASData",
    "stateBackups",
    "auditEvidence",
)
PHASES = (
    "install-rollback",
    "install",
    "observe-backup-timer",
    "observe-audit-timer",
    "backup-mount-loss",
    "audit-mount-loss",
    "remove-rollback",
    "remove",
)
PHASE_OUTPUTS = {
    "install-rollback": ("operations-install-rollback.log",),
    "install": ("operations-install.log",),
    "observe-backup-timer": ("backup-timer.log",),
    "observe-audit-timer": ("audit-timer.log",),
    "backup-mount-loss": ("backup-mount-loss.log",),
    "audit-mount-loss": ("audit-mount-loss.log",),
    "remove-rollback": ("operations-remove-rollback.log",),
    "remove": ("operations-remove.log",),
}


class OperationsSystemdLabError(RuntimeError):
    """The physical systemd lifecycle lab cannot proceed safely."""


@dataclass(frozen=True)
class LabTools:
    systemctl: Path = Path("/usr/bin/systemctl")
    systemd_analyze: Path = Path("/usr/bin/systemd-analyze")
    dpkg_query: Path = Path("/usr/bin/dpkg-query")
    mount: Path = Path("/usr/bin/mount")
    umount: Path = Path("/usr/bin/umount")


DEFAULT_TOOLS = LabTools()
CommandRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]
MountChecker = Callable[[Path], bool]
FallbackFileLister = Callable[[Path], Sequence[str]]
StorageVerifier = Callable[..., Mapping[str, str]]


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )


def _fallback_files(destination: Path) -> list[str]:
    return (
        [path.name for path in destination.iterdir() if path.is_file()]
        if destination.exists()
        else []
    )


def _sha256_file(path: Path, *, maximum: int = MAX_EVIDENCE_BYTES) -> dict[str, Any]:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise OperationsSystemdLabError("preservation or output file is unavailable") from exc
    digest = hashlib.sha256()
    total = 0
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or not 1 <= before.st_size <= maximum:
            raise OperationsSystemdLabError("preservation or output file is unsafe")
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, maximum - total + 1))
            if not chunk:
                break
            total += len(chunk)
            if total > maximum:
                raise OperationsSystemdLabError("preservation or output file is oversized")
            digest.update(chunk)
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise OperationsSystemdLabError("preservation or output file changed while read")
        return {"sha256": digest.hexdigest(), "size": total}
    finally:
        os.close(descriptor)


def _read_os_release(path: Path) -> dict[str, str]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise OperationsSystemdLabError("Debian OS identity is unavailable") from exc
    values: dict[str, str] = {}
    for line in text.splitlines():
        if "=" not in line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        values[key] = value.strip().strip('"')
    if values.get("ID") != "debian" or values.get("VERSION_ID") != "13":
        raise OperationsSystemdLabError("physical operations lab requires Debian 13")
    return {"id": "debian", "versionId": "13"}


def _omv_version(tools: LabTools, runner: CommandRunner) -> str:
    completed = runner([str(tools.dpkg_query), "-W", "-f=${Version}", "openmediavault"])
    version = completed.stdout.strip()
    normalized = version.split(":", 1)[-1]
    if completed.returncode != 0 or OMV_VERSION.fullmatch(normalized) is None:
        raise OperationsSystemdLabError("physical operations lab requires OMV 8")
    return normalized


def _state(tools: LabTools, runner: CommandRunner, name: str) -> dict[str, Any]:
    enabled = runner([str(tools.systemctl), "is-enabled", name])
    active = runner([str(tools.systemctl), "is-active", name])
    if enabled.returncode not in {0, 1, 4} or active.returncode not in {0, 3, 4}:
        raise OperationsSystemdLabError("systemd state query failed")
    enabled_state = enabled.stdout.strip()
    active_state = active.stdout.strip()
    if not enabled_state or not active_state:
        raise OperationsSystemdLabError("systemd state query returned no state")
    return {
        "enabled": enabled.returncode == 0,
        "active": active.returncode == 0,
        "enabledState": enabled_state,
        "activeState": active_state,
    }


def _timer_trigger(tools: LabTools, runner: CommandRunner, timer: str) -> str:
    completed = runner(
        [str(tools.systemctl), "show", timer, "--property=LastTriggerUSec", "--value"]
    )
    value = completed.stdout.strip()
    if completed.returncode != 0 or not value:
        raise OperationsSystemdLabError("timer trigger timestamp is unavailable")
    return value


def _service_result(tools: LabTools, runner: CommandRunner, service: str) -> str:
    completed = runner([str(tools.systemctl), "show", service, "--property=Result", "--value"])
    value = completed.stdout.strip()
    if completed.returncode != 0 or value != "success":
        raise OperationsSystemdLabError("timer service did not finish successfully")
    return value


def _unit_snapshot(
    unit_directory: Path, tools: LabTools, runner: CommandRunner, *, trusted_uid: int
) -> dict[str, Any]:
    units: dict[str, Any] = {}
    for name in systemd.UNIT_NAMES:
        path = unit_directory / name
        if path.exists() or path.is_symlink():
            payload, mode = systemd._read_existing_unit(path, name, trusted_uid=trusted_uid)
            units[name] = {"sha256": systemd._sha256(payload), "mode": f"{mode:04o}"}
        else:
            units[name] = None
    return {
        "units": units,
        "timers": {name: _state(tools, runner, name) for name in systemd.TIMER_NAMES},
        "recovery": _state(tools, runner, systemd.RECOVERY_SERVICE_NAME),
    }


def _write_new(path: Path, payload: Mapping[str, Any], *, trusted_uid: int) -> None:
    if path.parent.is_symlink() or path.exists() or path.is_symlink():
        raise OperationsSystemdLabError("lab evidence output must be a new regular file")
    parent = path.parent.resolve(strict=True)
    systemd._assert_owned_directory(parent, "lab evidence directory", trusted_uid=trusted_uid)
    data = systemd._canonical_json(payload)
    descriptor = os.open(
        parent / path.name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
        0o444,
    )
    try:
        os.fchmod(descriptor, 0o444)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            descriptor = -1
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _load_json(
    path: Path,
    label: str,
    *,
    trusted_uid: int | None = None,
    private: bool = True,
    exact_mode: int | None = None,
) -> dict[str, Any]:
    raw = systemd._safe_regular(
        path,
        label,
        maximum=systemd.MAX_PLAN_BYTES,
        trusted_uid=os.getuid() if trusted_uid is None else trusted_uid,
        private=private,
        exact_mode=exact_mode,
    )
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=systemd._reject_duplicate_keys)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise OperationsSystemdLabError(f"{label} is not strict JSON") from exc
    if not isinstance(value, dict):
        raise OperationsSystemdLabError(f"{label} is not an object")
    return value


def _candidate_identity(path: Path, *, trusted_uid: int) -> dict[str, str]:
    raw = systemd._safe_regular(
        path,
        "release candidate evidence index",
        maximum=systemd.MAX_PLAN_BYTES,
        trusted_uid=trusted_uid,
        private=False,
        exact_mode=0o444,
    )
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=systemd._reject_duplicate_keys)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise OperationsSystemdLabError("release candidate index is not strict JSON") from exc
    source = value.get("source") if isinstance(value, dict) else None
    evidence = value.get("evidence") if isinstance(value, dict) else None
    appliance = evidence.get("appliance") if isinstance(evidence, dict) else None
    operations = appliance.get("operationsBundle") if isinstance(appliance, dict) else None
    physical = value.get("physicalAcceptance") if isinstance(value, dict) else None
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "schemaVersion",
            "kind",
            "source",
            "evidence",
            "ciReleaseCandidateReady",
            "nasProductDeliveryReady",
            "physicalAcceptance",
            "indexId",
        }
        or value.get("schemaVersion") != 1
        or value.get("kind") != "echo.delivery-release-evidence-index"
        or value.get("ciReleaseCandidateReady") is not True
        or value.get("nasProductDeliveryReady") is not False
        or not isinstance(source, dict)
        or set(source) != {"repository", "commit", "agentRepository", "agentCommit", "releaseTag"}
        or not isinstance(source["repository"], str)
        or not isinstance(source["agentRepository"], str)
        or not isinstance(source["commit"], str)
        or SHA1.fullmatch(source["commit"]) is None
        or not isinstance(source["agentCommit"], str)
        or SHA1.fullmatch(source["agentCommit"]) is None
        or not isinstance(source["releaseTag"], str)
        or not isinstance(evidence, dict)
        or "candidatePreflight" not in evidence
        or not isinstance(appliance, dict)
        or set(appliance) != {"manifestSha256", "immutableReference", "operationsBundle"}
        or not isinstance(appliance["manifestSha256"], str)
        or SHA256.fullmatch(appliance["manifestSha256"]) is None
        or not isinstance(appliance["immutableReference"], str)
        or not isinstance(operations, dict)
        or set(operations) != {"artifactId", "sha256", "imageReference"}
        or not isinstance(operations["artifactId"], str)
        or ARTIFACT_ID.fullmatch(operations["artifactId"]) is None
        or not isinstance(operations["sha256"], str)
        or SHA256.fullmatch(operations["sha256"]) is None
        or operations["imageReference"] != appliance["immutableReference"]
        or not isinstance(physical, dict)
        or physical.get("complete") is not False
    ):
        raise OperationsSystemdLabError("release candidate identity is invalid")
    unsigned = dict(value)
    index_id = unsigned.pop("indexId", None)
    canonical = json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
    if not isinstance(index_id, str) or index_id != hashlib.sha256(canonical).hexdigest():
        raise OperationsSystemdLabError("release candidate index ID is invalid")
    return {
        "indexPath": str(path.resolve(strict=True)),
        "indexId": index_id,
        "indexSha256": hashlib.sha256(raw).hexdigest(),
        "osRepository": source["repository"],
        "sourceRevision": source["commit"],
        "agentRepository": source["agentRepository"],
        "agentRevision": source["agentCommit"],
        "releaseTag": source["releaseTag"],
        "applianceManifestSha256": appliance["manifestSha256"],
        "immutableReference": appliance["immutableReference"],
        "operationsArtifactId": operations["artifactId"],
        "operationsArchiveSha256": operations["sha256"],
    }


def _operations_bundle_identity(
    bundle_root: Path,
    candidate: Mapping[str, str],
    *,
    trusted_uid: int,
) -> dict[str, Any]:
    manifest_path = bundle_root / "bundle-manifest.json"
    raw = systemd._safe_regular(
        manifest_path,
        "operations bundle manifest",
        maximum=systemd.MAX_PLAN_BYTES,
        trusted_uid=trusted_uid,
        private=False,
        exact_mode=0o644,
    )
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=systemd._reject_duplicate_keys)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise OperationsSystemdLabError("operations bundle manifest is not strict JSON") from exc
    artifact = value.get("artifact") if isinstance(value, dict) else None
    files = value.get("files") if isinstance(value, dict) else None
    lab_record = files.get("operations_systemd_lab.py") if isinstance(files, dict) else None
    artifact_id = candidate["operationsArtifactId"]
    lab_path = bundle_root / "operations_systemd_lab.py"
    lab_raw = systemd._safe_regular(
        lab_path,
        "candidate operations systemd lab tool",
        maximum=systemd.MAX_PLAN_BYTES,
        trusted_uid=trusted_uid,
        private=False,
        exact_mode=0o755,
    )
    if (
        not isinstance(value, dict)
        or set(value) != {"schemaVersion", "artifact", "files"}
        or value.get("schemaVersion") != 1
        or not isinstance(artifact, dict)
        or set(artifact) != {"id", "name", "architectures", "imageReference", "entrypoints"}
        or artifact.get("id") != artifact_id
        or artifact.get("name") != f"echo-appliance-operations-{artifact_id}"
        or bundle_root.name != artifact["name"]
        or artifact.get("imageReference") != candidate["immutableReference"]
        or not isinstance(artifact.get("entrypoints"), dict)
        or artifact["entrypoints"].get("operationsSystemdLab")
        != "./operations_systemd_lab.py plan|run"
        or not isinstance(lab_record, dict)
        or set(lab_record) != {"sha256", "size", "mode"}
        or lab_record.get("sha256") != hashlib.sha256(lab_raw).hexdigest()
        or lab_record.get("size") != len(lab_raw)
        or lab_record.get("mode") != "0755"
    ):
        raise OperationsSystemdLabError("operations lab tool is not from the release candidate")
    return {
        "artifactId": artifact_id,
        "archiveSha256": candidate["operationsArchiveSha256"],
        "imageReference": candidate["immutableReference"],
        "manifestSha256": hashlib.sha256(raw).hexdigest(),
        "labToolSha256": hashlib.sha256(lab_raw).hexdigest(),
        "labToolSize": len(lab_raw),
    }


def _config(value: Mapping[str, Any]) -> systemd.OperationsConfig:
    try:
        return systemd._config_from_payload(value)
    except systemd.OperationsSystemdError as exc:
        raise OperationsSystemdLabError("lab plan operations config is invalid") from exc


def _preservation(arguments: Sequence[str]) -> dict[str, dict[str, Any]]:
    values: dict[str, dict[str, Any]] = {}
    for argument in arguments:
        label, separator, path_text = argument.partition("=")
        if not separator or label not in PRESERVATION_LABELS or label in values:
            raise OperationsSystemdLabError("lab plan requires four unique preservation files")
        path = Path(path_text)
        if not path.is_absolute() or path.is_symlink():
            raise OperationsSystemdLabError("preservation files must be absolute and non-symlink")
        values[label] = {"path": str(path.resolve(strict=True)), **_sha256_file(path)}
    if set(values) != set(PRESERVATION_LABELS):
        raise OperationsSystemdLabError("lab plan requires four unique preservation files")
    return values


def _validated_tools(tools: LabTools, *, trusted_uid: int) -> None:
    for tool in (tools.systemctl, tools.systemd_analyze, tools.dpkg_query):
        systemd._safe_regular(
            tool,
            f"physical lab tool {tool.name}",
            maximum=64 * 1024 * 1024,
            trusted_uid=trusted_uid,
            private=False,
            exact_mode=0o755,
        )
    for tool in (tools.mount, tools.umount):
        systemd._safe_regular(
            tool,
            f"physical lab tool {tool.name}",
            maximum=64 * 1024 * 1024,
            trusted_uid=trusted_uid,
            private=False,
        )
        if stat.S_IMODE(tool.stat().st_mode) not in {0o755, 0o4755}:
            raise OperationsSystemdLabError("mount tools have an unsafe executable mode")


def build_plan(
    *,
    candidate_index: Path,
    config: systemd.OperationsConfig,
    evidence_directory: Path,
    preservation_arguments: Sequence[str],
    output: Path,
    tools: LabTools = DEFAULT_TOOLS,
    runner: CommandRunner = _run,
    os_release: Path = Path("/etc/os-release"),
    unit_directory: Path = Path("/etc/systemd/system"),
    effective_uid: int | None = None,
    trusted_uid: int = 0,
    system_name: str | None = None,
    storage_verifier: StorageVerifier = systemd.verify_external_storage,
) -> dict[str, Any]:
    uid = os.geteuid() if effective_uid is None else effective_uid
    host_system = os.uname().sysname if system_name is None else system_name
    if uid != 0 or host_system != "Linux":
        raise OperationsSystemdLabError("physical operations lab plan requires Linux root")
    _validated_tools(tools, trusted_uid=trusted_uid)
    candidate = _candidate_identity(candidate_index, trusted_uid=trusted_uid)
    operations_bundle = _operations_bundle_identity(
        config.bundle_root,
        candidate,
        trusted_uid=trusted_uid,
    )
    os_identity = _read_os_release(os_release)
    omv_version = _omv_version(tools, runner)
    evidence_root = evidence_directory.resolve(strict=True)
    systemd._assert_owned_directory(
        evidence_root, "lab evidence directory", trusted_uid=trusted_uid
    )
    if config.backup_mountpoint == config.audit_mountpoint or (
        config.backup_mountpoint in config.audit_mountpoint.parents
        or config.audit_mountpoint in config.backup_mountpoint.parents
    ):
        raise OperationsSystemdLabError("backup and audit lab mountpoints must be independent")
    installer = systemd.OperationsSystemdInstaller(
        config,
        tools=systemd.SystemTools(tools.systemctl, tools.systemd_analyze),
        command_runner=runner,
        layout=systemd.SystemLayout(unit_directory),
        effective_uid=uid,
        trusted_uid=trusted_uid,
        system_name=host_system,
        storage_verifier=storage_verifier,
    )
    install_plan = installer.plan()
    backup_storage = install_plan["storage"]["backup"]
    audit_storage = install_plan["storage"]["audit"]
    if (
        backup_storage["deviceId"] == audit_storage["deviceId"]
        or backup_storage["source"] == audit_storage["source"]
    ):
        raise OperationsSystemdLabError(
            "backup and audit lab evidence require independent storage sources"
        )
    baseline = _unit_snapshot(unit_directory, tools, runner, trusted_uid=trusted_uid)
    recovery = baseline["recovery"]
    if (
        any(value is not None for value in baseline["units"].values())
        or recovery["enabled"]
        or recovery["active"]
        or recovery["enabledState"] not in {"disabled", "not-found"}
        or recovery["activeState"] not in {"inactive", "not-found"}
        or any(
            state["enabled"]
            or state["active"]
            or state["enabledState"] not in {"disabled", "not-found"}
            or state["activeState"] not in {"inactive", "not-found"}
            for state in baseline["timers"].values()
        )
    ):
        raise OperationsSystemdLabError("physical lab requires a clean managed-unit baseline")
    payload: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "echo.operations-systemd-physical-lab-plan",
        "releaseCandidate": candidate,
        "operationsBundle": operations_bundle,
        "platform": {**os_identity, "omvVersion": omv_version},
        "config": systemd._config_payload(installer.config),
        "installPlan": install_plan,
        "evidenceDirectory": str(evidence_root),
        "preservation": _preservation(preservation_arguments),
        "baseline": baseline,
        "phases": list(PHASES),
    }
    payload["planId"] = systemd._sha256(systemd._canonical_json(payload))
    payload["confirmations"] = {
        phase: f"RUN ECHO OPERATIONS LAB {phase} {payload['planId']}" for phase in PHASES
    }
    systemd._write_plan(output, payload)
    return payload


class _FailOnce:
    def __init__(self, delegate: CommandRunner, predicate: Callable[[list[str]], bool]) -> None:
        self.delegate = delegate
        self.predicate = predicate
        self.failed = False

    def __call__(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        if not self.failed and self.predicate(command):
            self.failed = True
            return subprocess.CompletedProcess(command, 1, "", "intentional physical-lab fault")
        return self.delegate(command)


def _phase_dependencies(root: Path, phase: str, *, plan_id: str, trusted_uid: int) -> None:
    index = PHASES.index(phase)
    required = {name for prior in PHASES[:index] for name in PHASE_OUTPUTS[prior]}
    forbidden = {name for current in PHASES[index:] for name in PHASE_OUTPUTS[current]}
    actual = {name for name in required | forbidden if (root / name).exists()}
    if not required <= actual or actual & forbidden:
        raise OperationsSystemdLabError("lab phase evidence sequence is incomplete or stale")
    for name in required:
        raw = systemd._safe_regular(
            root / name,
            f"prior lab evidence {name}",
            maximum=systemd.MAX_PLAN_BYTES,
            trusted_uid=trusted_uid,
            private=False,
            exact_mode=0o444,
        )
        try:
            value = json.loads(
                raw.decode("utf-8"), object_pairs_hook=systemd._reject_duplicate_keys
            )
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise OperationsSystemdLabError("prior lab evidence is not strict JSON") from exc
        if (
            not isinstance(value, dict)
            or set(value) != {"schemaVersion", "kind", "planId", "evidence", "passed", "details"}
            or value["schemaVersion"] != SCHEMA_VERSION
            or value["kind"] != "echo.operations-systemd-physical-lab-evidence"
            or value["planId"] != plan_id
            or value["evidence"] != name
            or value["passed"] is not True
            or not isinstance(value["details"], dict)
        ):
            raise OperationsSystemdLabError("prior lab evidence contract is invalid")


def _verify_preservation(plan: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    preservation = plan.get("preservation")
    if not isinstance(preservation, dict) or set(preservation) != set(PRESERVATION_LABELS):
        raise OperationsSystemdLabError("lab plan preservation contract is invalid")
    for label in PRESERVATION_LABELS:
        record = preservation[label]
        if not isinstance(record, dict) or set(record) != {"path", "sha256", "size"}:
            raise OperationsSystemdLabError("lab plan preservation contract is invalid")
        current = _sha256_file(Path(record["path"]))
        if current != {"sha256": record["sha256"], "size": record["size"]}:
            raise OperationsSystemdLabError(f"preserved asset changed during lab: {label}")
        result[label] = current
    return result


def _new_output(
    root: Path,
    name: str,
    plan_id: str,
    details: Mapping[str, Any],
    *,
    trusted_uid: int,
) -> None:
    _write_new(
        root / name,
        {
            "schemaVersion": SCHEMA_VERSION,
            "kind": "echo.operations-systemd-physical-lab-evidence",
            "planId": plan_id,
            "evidence": name,
            "passed": True,
            "details": dict(details),
        },
        trusted_uid=trusted_uid,
    )


def _latest_product(directory: Path, suffix: str, after_ns: int) -> dict[str, Any]:
    candidates = [
        path
        for path in directory.iterdir()
        if path.name.endswith(suffix)
        and path.is_file()
        and not path.is_symlink()
        and path.stat().st_mtime_ns >= after_ns
    ]
    if not candidates:
        raise OperationsSystemdLabError("timer did not create a new verified product")
    selected = max(candidates, key=lambda path: path.stat().st_mtime_ns)
    return {"name": selected.name, **_sha256_file(selected)}


def run_phase(
    *,
    plan_path: Path,
    phase: str,
    confirmation: str,
    tools: LabTools = DEFAULT_TOOLS,
    runner: CommandRunner = _run,
    mount_checker: MountChecker = os.path.ismount,
    unit_directory: Path = Path("/etc/systemd/system"),
    effective_uid: int | None = None,
    trusted_uid: int = 0,
    system_name: str | None = None,
    os_release: Path = Path("/etc/os-release"),
    storage_verifier: StorageVerifier = systemd.verify_external_storage,
    fallback_file_lister: FallbackFileLister = _fallback_files,
) -> dict[str, Any]:
    if phase not in PHASES:
        raise OperationsSystemdLabError("physical lab phase is invalid")
    uid = os.geteuid() if effective_uid is None else effective_uid
    host_system = os.uname().sysname if system_name is None else system_name
    if uid != 0 or host_system != "Linux":
        raise OperationsSystemdLabError("physical operations lab phase requires Linux root")
    _validated_tools(tools, trusted_uid=trusted_uid)
    plan = _load_json(plan_path, "physical operations lab plan")
    confirmations_value = plan.get("confirmations")
    expected_keys = {
        "schemaVersion",
        "kind",
        "releaseCandidate",
        "operationsBundle",
        "platform",
        "config",
        "installPlan",
        "evidenceDirectory",
        "preservation",
        "baseline",
        "phases",
        "planId",
        "confirmations",
    }
    if (
        set(plan) != expected_keys
        or plan.get("schemaVersion") != SCHEMA_VERSION
        or plan.get("kind") != "echo.operations-systemd-physical-lab-plan"
        or not isinstance(plan.get("planId"), str)
        or not isinstance(confirmations_value, dict)
        or confirmations_value.get(phase) != confirmation
    ):
        raise OperationsSystemdLabError("physical operations lab plan or confirmation is invalid")
    unsigned = dict(plan)
    confirmations = unsigned.pop("confirmations", None)
    claimed_plan_id = unsigned.pop("planId", None)
    if (
        not isinstance(confirmations, dict)
        or set(confirmations) != set(PHASES)
        or confirmations
        != {phase: f"RUN ECHO OPERATIONS LAB {phase} {claimed_plan_id}" for phase in PHASES}
        or claimed_plan_id != systemd._sha256(systemd._canonical_json(unsigned))
        or plan.get("phases") != list(PHASES)
    ):
        raise OperationsSystemdLabError("physical operations lab plan identity is invalid")
    current_platform = {
        **_read_os_release(os_release),
        "omvVersion": _omv_version(tools, runner),
    }
    if plan.get("platform") != current_platform:
        raise OperationsSystemdLabError("physical operations lab platform drifted")
    root = Path(plan["evidenceDirectory"]).resolve(strict=True)
    systemd._assert_owned_directory(root, "lab evidence directory", trusted_uid=trusted_uid)
    _phase_dependencies(root, phase, plan_id=plan["planId"], trusted_uid=trusted_uid)
    config = _config(plan["config"])
    release_candidate = plan.get("releaseCandidate")
    if not isinstance(release_candidate, dict):
        raise OperationsSystemdLabError("physical operations lab candidate binding is invalid")
    current_candidate = _candidate_identity(
        Path(str(release_candidate.get("indexPath"))),
        trusted_uid=trusted_uid,
    )
    if current_candidate != release_candidate or _operations_bundle_identity(
        config.bundle_root,
        current_candidate,
        trusted_uid=trusted_uid,
    ) != plan.get("operationsBundle"):
        raise OperationsSystemdLabError("release candidate or operations bundle drifted")
    installer = systemd.OperationsSystemdInstaller(
        config,
        tools=systemd.SystemTools(tools.systemctl, tools.systemd_analyze),
        command_runner=runner,
        layout=systemd.SystemLayout(unit_directory),
        effective_uid=uid,
        trusted_uid=trusted_uid,
        system_name=host_system,
        storage_verifier=storage_verifier,
    )
    if installer.plan() != plan["installPlan"]:
        raise OperationsSystemdLabError("operations install plan drifted during physical lab")
    plan_id = plan["planId"]
    before = _unit_snapshot(unit_directory, tools, runner, trusted_uid=trusted_uid)

    if phase == "install-rollback":
        fault = _FailOnce(
            runner,
            lambda command: command[1:] == ["enable", "--now", systemd.TIMER_NAMES[1]],
        )
        faulted = systemd.OperationsSystemdInstaller(
            config,
            tools=systemd.SystemTools(tools.systemctl, tools.systemd_analyze),
            command_runner=fault,
            layout=systemd.SystemLayout(unit_directory),
            effective_uid=uid,
            trusted_uid=trusted_uid,
            system_name=host_system,
            storage_verifier=storage_verifier,
        )
        try:
            faulted.install(plan["installPlan"], plan["installPlan"]["installConfirmation"])
        except systemd.OperationsSystemdError as exc:
            if "previous units were restored" not in str(exc):
                raise OperationsSystemdLabError("install fault did not complete rollback") from exc
        else:
            raise OperationsSystemdLabError("install fault injection did not fail")
        after = _unit_snapshot(unit_directory, tools, runner, trusted_uid=trusted_uid)
        if not fault.failed or after != before or after != plan["baseline"]:
            raise OperationsSystemdLabError("install rollback did not restore the clean baseline")
        _new_output(
            root,
            PHASE_OUTPUTS[phase][0],
            plan_id,
            {"baselineRestored": True},
            trusted_uid=trusted_uid,
        )

    elif phase == "install":
        report = installer.install(plan["installPlan"], plan["installPlan"]["installConfirmation"])
        after = _unit_snapshot(unit_directory, tools, runner, trusted_uid=trusted_uid)
        expected_units = plan["installPlan"]["units"]
        recovery = after["recovery"]
        if (
            any(after["units"][name] != expected_units[name] for name in systemd.UNIT_NAMES)
            or not recovery["enabled"]
            or recovery["active"]
            or any(
                not state["enabled"] or not state["active"] for state in after["timers"].values()
            )
        ):
            raise OperationsSystemdLabError("installed units or timers do not match the plan")
        _new_output(
            root,
            PHASE_OUTPUTS[phase][0],
            plan_id,
            {
                "installed": report["installed"],
                "installedAtNs": time.time_ns(),
                "platform": plan["platform"],
                "timerTriggers": {
                    name: _timer_trigger(tools, runner, name) for name in systemd.TIMER_NAMES
                },
                "unitState": after,
            },
            trusted_uid=trusted_uid,
        )

    elif phase in {"observe-backup-timer", "observe-audit-timer"}:
        install = _load_json(
            root / PHASE_OUTPUTS["install"][0],
            "install evidence",
            trusted_uid=trusted_uid,
            private=False,
            exact_mode=0o444,
        )
        backup = phase == "observe-backup-timer"
        timer = systemd.TIMER_NAMES[0 if backup else 1]
        service = systemd.UNIT_NAMES[0 if backup else 2]
        baseline_trigger = install["details"]["timerTriggers"][timer]
        trigger = _timer_trigger(tools, runner, timer)
        if trigger == baseline_trigger:
            raise OperationsSystemdLabError("timer has not triggered since installation")
        product = _latest_product(
            config.backup_directory if backup else config.audit_directory,
            ".echo-backup" if backup else ".echo-audit",
            install["details"]["installedAtNs"],
        )
        _new_output(
            root,
            PHASE_OUTPUTS[phase][0],
            plan_id,
            {
                "timer": timer,
                "lastTriggerChanged": True,
                "serviceResult": _service_result(tools, runner, service),
                "product": product,
            },
            trusted_uid=trusted_uid,
        )

    elif phase in {"backup-mount-loss", "audit-mount-loss"}:
        backup = phase == "backup-mount-loss"
        timer = systemd.TIMER_NAMES[0 if backup else 1]
        service = systemd.UNIT_NAMES[0 if backup else 2]
        mountpoint = config.backup_mountpoint if backup else config.audit_mountpoint
        destination = config.backup_directory if backup else config.audit_directory
        if not mount_checker(mountpoint):
            raise OperationsSystemdLabError("declared lab mount is not mounted before loss test")
        if runner([str(tools.systemctl), "stop", timer]).returncode != 0:
            raise OperationsSystemdLabError("timer could not be stopped for mount-loss test")
        recovered = False
        failure_returncode = 0
        try:
            if runner([str(tools.umount), "--", str(mountpoint)]).returncode != 0 or mount_checker(
                mountpoint
            ):
                raise OperationsSystemdLabError("lab mount could not be detached")
            failure_returncode = runner([str(tools.systemctl), "start", service]).returncode
            fallback_files = list(fallback_file_lister(destination))
            if failure_returncode == 0 or fallback_files:
                raise OperationsSystemdLabError("missing mount did not fail closed")
        finally:
            mounted = runner([str(tools.mount), "--", str(mountpoint)]).returncode == 0
            recovered = mounted and mount_checker(mountpoint)
            runner([str(tools.systemctl), "start", timer])
        if not recovered:
            raise OperationsSystemdLabError("lab mount was not restored after loss test")
        if installer.plan() != plan["installPlan"]:
            raise OperationsSystemdLabError("restored mount identity differs from the lab plan")
        if not _state(tools, runner, timer)["active"]:
            raise OperationsSystemdLabError("timer did not resume after mount-loss test")
        _new_output(
            root,
            PHASE_OUTPUTS[phase][0],
            plan_id,
            {
                "service": service,
                "failedReturnCode": failure_returncode,
                "fallbackWriteAbsent": True,
                "mountRestored": True,
            },
            trusted_uid=trusted_uid,
        )

    elif phase == "remove-rollback":
        fault = _FailOnce(
            runner,
            lambda command: command[1:] == ["daemon-reload"],
        )
        remover = systemd.OperationsSystemdRemover(
            command_runner=fault,
            layout=systemd.SystemLayout(unit_directory),
            tools=systemd.SystemTools(tools.systemctl, tools.systemd_analyze),
            effective_uid=uid,
            trusted_uid=trusted_uid,
            system_name=host_system,
        )
        remove_plan = remover.plan()
        try:
            remover.remove(remove_plan, remove_plan["removeConfirmation"])
        except systemd.OperationsSystemdError as exc:
            if "managed units were restored" not in str(exc):
                raise OperationsSystemdLabError("remove fault did not complete rollback") from exc
        else:
            raise OperationsSystemdLabError("remove fault injection did not fail")
        after = _unit_snapshot(unit_directory, tools, runner, trusted_uid=trusted_uid)
        if not fault.failed or after != before:
            raise OperationsSystemdLabError("remove rollback did not restore units and timers")
        _new_output(
            root,
            PHASE_OUTPUTS[phase][0],
            plan_id,
            {"installedStateRestored": True},
            trusted_uid=trusted_uid,
        )

    else:
        preservation = _verify_preservation(plan)
        remover = systemd.OperationsSystemdRemover(
            command_runner=runner,
            layout=systemd.SystemLayout(unit_directory),
            tools=systemd.SystemTools(tools.systemctl, tools.systemd_analyze),
            effective_uid=uid,
            trusted_uid=trusted_uid,
            system_name=host_system,
        )
        remove_plan = remover.plan()
        report = remover.remove(remove_plan, remove_plan["removeConfirmation"])
        after = _unit_snapshot(unit_directory, tools, runner, trusted_uid=trusted_uid)
        recovery = after["recovery"]
        if (
            any(value is not None for value in after["units"].values())
            or recovery["enabled"]
            or recovery["active"]
            or recovery["enabledState"] not in {"disabled", "not-found"}
            or recovery["activeState"] not in {"inactive", "not-found"}
            or any(
                state["enabled"]
                or state["active"]
                or state["enabledState"] not in {"disabled", "not-found"}
                or state["activeState"] not in {"inactive", "not-found"}
                for state in after["timers"].values()
            )
        ):
            raise OperationsSystemdLabError("managed removal left units or timers behind")
        after_preservation = _verify_preservation(plan)
        if after_preservation != preservation:
            raise OperationsSystemdLabError("managed removal changed preserved assets")
        _new_output(
            root,
            PHASE_OUTPUTS[phase][0],
            plan_id,
            {
                "removed": report["removed"],
                "unitsAndTimersAbsent": True,
                "credentials": plan["installPlan"]["credentials"],
                "preserved": after_preservation,
            },
            trusted_uid=trusted_uid,
        )
    return {"phase": phase, "planId": plan_id, "outputs": list(PHASE_OUTPUTS[phase])}


def _add_config(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--backup-directory", type=Path, required=True)
    parser.add_argument("--backup-mountpoint", type=Path, required=True)
    parser.add_argument("--audit-directory", type=Path, required=True)
    parser.add_argument("--audit-mountpoint", type=Path, required=True)
    parser.add_argument("--backup-credential", type=Path, required=True)
    parser.add_argument("--audit-credential", type=Path, required=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan = subparsers.add_parser("plan")
    plan.add_argument("--candidate-index", type=Path, required=True)
    _add_config(plan)
    plan.add_argument("--evidence-directory", type=Path, required=True)
    plan.add_argument("--preserve", action="append", required=True, metavar="LABEL=PATH")
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
                config=systemd.OperationsConfig(
                    bundle_root=args.bundle_root,
                    backup_directory=args.backup_directory,
                    backup_mountpoint=args.backup_mountpoint,
                    audit_directory=args.audit_directory,
                    audit_mountpoint=args.audit_mountpoint,
                    backup_credential=args.backup_credential,
                    audit_credential=args.audit_credential,
                ),
                evidence_directory=args.evidence_directory,
                preservation_arguments=args.preserve,
                output=args.output,
            )
            print(
                "ECHO_OPERATIONS_SYSTEMD_LAB_PLAN_READY "
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
        OperationsSystemdLabError,
        systemd.OperationsSystemdError,
        subprocess.TimeoutExpired,
    ) as exc:
        print(f"Echo operations systemd physical lab failed: {exc}", file=sys.stderr)
        return 1
    print(
        "ECHO_OPERATIONS_SYSTEMD_LAB_PHASE_OK "
        f"phase={report['phase']} plan={report['planId']} outputs={len(report['outputs'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
