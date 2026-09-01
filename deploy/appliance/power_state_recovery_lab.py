#!/usr/bin/env python3
"""Run the candidate-bound appliance power-loss and state-recovery physical lab."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess  # nosec B404
import sys
import tempfile
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

try:
    from deploy.appliance import operations_systemd as systemd
    from deploy.appliance import operations_systemd_lab as operations_lab
except ModuleNotFoundError:
    import operations_systemd as systemd
    import operations_systemd_lab as operations_lab

SCHEMA_VERSION = 1
GATE = "power_loss_during_update_and_state_restore"
PHASES = (
    "baseline",
    "arm-power-cut",
    "recover-power-cut",
    "upgrade-success",
    "upgrade-failure",
    "managed-uninstall",
    "state-restore",
)
PHASE_OUTPUTS = {
    "baseline": "power-state-baseline.log",
    "arm-power-cut": "power-update-cut-armed.log",
    "recover-power-cut": "power-update-cut-recovered.log",
    "upgrade-success": "power-upgrade-success.log",
    "upgrade-failure": "power-upgrade-failure.log",
    "managed-uninstall": "power-managed-uninstall.log",
    "state-restore": "power-state-restore.log",
}
CHECK_OUTPUTS = {
    "immutableDigestUpgradeVerified": PHASE_OUTPUTS["upgrade-success"],
    "updatePowerLossRolledBack": PHASE_OUTPUTS["recover-power-cut"],
    "failedUpgradeRollbackVerified": PHASE_OUTPUTS["upgrade-failure"],
    "managedUninstallDataPreserved": PHASE_OUTPUTS["managed-uninstall"],
    "stateRestoreCommitted": PHASE_OUTPUTS["state-restore"],
    "dataPreserved": PHASE_OUTPUTS["state-restore"],
}
STATE_CANARY_BYTES = 1024 * 1024
NAS_CANARY_BYTES = 1024 * 1024 * 1024
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_BACKUP_BYTES = 100 * 1024 * 1024 * 1024
IMAGE_REFERENCE = re.compile(r"^[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64}$")
SAFE_CONTAINER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
SAFE_UNIT = re.compile(r"^echo-power-state-lab-[0-9a-f]{16}\.service$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
PROXY_PREFIX = "echo-power-state-docker-proxy-"
PROXY_ENV = "ECHO_POWER_STATE_DOCKER_PROXY"


class PowerStateRecoveryLabError(RuntimeError):
    """The destructive power/state physical lab cannot proceed safely."""


@dataclass(frozen=True)
class LabTools:
    docker: Path = Path("/usr/bin/docker")
    systemctl: Path = Path("/usr/bin/systemctl")
    systemd_run: Path = Path("/usr/bin/systemd-run")
    systemd_creds: Path = Path("/usr/bin/systemd-creds")
    journalctl: Path = Path("/usr/bin/journalctl")
    logger: Path = Path("/usr/bin/logger")
    sync: Path = Path("/usr/bin/sync")
    dpkg_query: Path = Path("/usr/bin/dpkg-query")


DEFAULT_TOOLS = LabTools()


def _canonical(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _execute(
    command: list[str],
    *,
    environment: Mapping[str, str] | None = None,
    timeout: int = 3600,
) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
    }
    if environment is not None:
        env.update(environment)
    return subprocess.run(  # nosec B603
        command,
        check=False,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=timeout,
        env=env,
    )


def _read_json(path: Path, label: str, *, exact_mode: int, trusted_uid: int) -> dict[str, Any]:
    raw = systemd._safe_regular(
        path,
        label,
        maximum=MAX_JSON_BYTES,
        trusted_uid=trusted_uid,
        private=exact_mode & 0o077 == 0,
        exact_mode=exact_mode,
    )
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=systemd._reject_duplicate_keys)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PowerStateRecoveryLabError(f"{label} is not strict JSON") from exc
    if not isinstance(value, dict):
        raise PowerStateRecoveryLabError(f"{label} is not an object")
    return value


def _write_new(
    path: Path,
    value: Mapping[str, Any],
    *,
    mode: int,
    trusted_uid: int,
) -> None:
    if path.exists() or path.is_symlink() or path.parent.is_symlink():
        raise PowerStateRecoveryLabError("power lab output must be a new regular file")
    parent = path.parent.resolve(strict=True)
    systemd._assert_owned_directory(parent, "power lab output directory", trusted_uid=trusted_uid)
    descriptor = os.open(
        parent / path.name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
        mode,
    )
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            stream.write(_canonical(value))
            stream.flush()
            os.fsync(stream.fileno())
        directory = os.open(parent, os.O_RDONLY | os.O_CLOEXEC)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _boot_id() -> str:
    try:
        value = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
        parsed = uuid.UUID(value)
    except (OSError, UnicodeError, ValueError) as exc:
        raise PowerStateRecoveryLabError("kernel boot identity is unavailable") from exc
    if str(parsed) != value:
        raise PowerStateRecoveryLabError("kernel boot identity is not canonical")
    return value


def _validated_tools(tools: LabTools, *, trusted_uid: int) -> None:
    for tool in (
        tools.docker,
        tools.systemctl,
        tools.systemd_run,
        tools.systemd_creds,
        tools.journalctl,
        tools.logger,
        tools.sync,
        tools.dpkg_query,
    ):
        systemd._safe_regular(
            tool,
            f"power lab host tool {tool.name}",
            maximum=64 * 1024 * 1024,
            trusted_uid=trusted_uid,
            private=False,
            exact_mode=0o755,
        )


def _hash_regular(
    path: Path,
    label: str,
    *,
    trusted_uid: int,
    exact_size: int | None = None,
    exact_mode: int = 0o600,
    maximum: int = MAX_BACKUP_BYTES,
) -> dict[str, Any]:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise PowerStateRecoveryLabError(f"{label} is unavailable") from exc
    digest = hashlib.sha256()
    total = 0
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != trusted_uid
            or stat.S_IMODE(before.st_mode) != exact_mode
            or before.st_size < 1
            or before.st_size > maximum
            or (exact_size is not None and before.st_size != exact_size)
        ):
            raise PowerStateRecoveryLabError(f"{label} has unsafe ownership, mode, or size")
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            total += len(block)
            if total > maximum:
                raise PowerStateRecoveryLabError(f"{label} exceeds its size limit")
            digest.update(block)
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
            raise PowerStateRecoveryLabError(f"{label} changed while it was read")
        return {"path": str(path.resolve(strict=True)), "sha256": digest.hexdigest(), "size": total}
    finally:
        os.close(descriptor)


def _release_image(path: Path, *, trusted_uid: int) -> str:
    raw = systemd._safe_regular(
        path,
        "appliance release environment",
        maximum=4096,
        trusted_uid=trusted_uid,
        private=True,
        exact_mode=0o600,
    )
    try:
        text = raw.decode("ascii")
    except UnicodeError as exc:
        raise PowerStateRecoveryLabError("release environment is invalid") from exc
    prefix = "ECHO_OS_IMAGE="
    if not text.startswith(prefix) or not text.endswith("\n") or text.count("\n") != 1:
        raise PowerStateRecoveryLabError("release environment is invalid")
    image = text[len(prefix) : -1]
    if IMAGE_REFERENCE.fullmatch(image) is None:
        raise PowerStateRecoveryLabError("release environment is not immutable")
    return image


def _container_image(name: str, tools: LabTools) -> str:
    completed = _execute(
        [str(tools.docker), "inspect", "--format", "{{.Config.Image}}", name], timeout=60
    )
    value = completed.stdout.strip()
    if completed.returncode != 0 or IMAGE_REFERENCE.fullmatch(value) is None:
        raise PowerStateRecoveryLabError(f"container image is unavailable: {name}")
    return value


def _service_state(name: str, tools: LabTools) -> dict[str, bool]:
    enabled = _execute([str(tools.systemctl), "is-enabled", "--quiet", name], timeout=60)
    active = _execute([str(tools.systemctl), "is-active", "--quiet", name], timeout=60)
    if enabled.returncode not in {0, 1, 4} or active.returncode not in {0, 3, 4}:
        raise PowerStateRecoveryLabError("systemd recovery-service state is unavailable")
    return {"enabled": enabled.returncode == 0, "active": active.returncode == 0}


def _bundle_identity(
    bundle_root: Path, candidate: Mapping[str, str], *, trusted_uid: int
) -> dict[str, Any]:
    manifest_raw = systemd._safe_regular(
        bundle_root / "bundle-manifest.json",
        "operations bundle manifest",
        maximum=MAX_JSON_BYTES,
        trusted_uid=trusted_uid,
        private=False,
        exact_mode=0o644,
    )
    try:
        manifest = json.loads(
            manifest_raw.decode("utf-8"), object_pairs_hook=systemd._reject_duplicate_keys
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PowerStateRecoveryLabError("operations bundle manifest is not strict JSON") from exc
    artifact = manifest.get("artifact") if isinstance(manifest, dict) else None
    files = manifest.get("files") if isinstance(manifest, dict) else None
    expected = {
        "power_state_recovery_lab.py": (
            "powerStateRecoveryLab",
            "./power_state_recovery_lab.py seed|plan|run|verify",
        ),
        "upgrade-appliance.sh": ("upgrade", "./upgrade-appliance.sh <registry@sha256:...>"),
        "recover-appliance-upgrade.sh": ("upgradeRecovery", "./recover-appliance-upgrade.sh"),
        "upgrade_transaction.py": (None, None),
        "backup-state.sh": (None, None),
        "restore-state.sh": ("restore", "./restore-state.sh <external-verified.echo-backup>"),
        "install-appliance.sh": ("install", "./install-appliance.sh"),
    }
    if (
        not isinstance(manifest, dict)
        or set(manifest) != {"schemaVersion", "artifact", "files"}
        or manifest.get("schemaVersion") != 1
        or not isinstance(artifact, dict)
        or set(artifact) != {"id", "name", "architectures", "imageReference", "entrypoints"}
        or artifact.get("id") != candidate["operationsArtifactId"]
        or artifact.get("name") != bundle_root.name
        or artifact.get("architectures") != ["amd64", "arm64"]
        or artifact.get("imageReference") != candidate["immutableReference"]
        or not isinstance(artifact.get("entrypoints"), dict)
        or not isinstance(files, dict)
    ):
        raise PowerStateRecoveryLabError("power lab bundle is not from the release candidate")
    inventory_digest = hashlib.sha256()
    checksum_lines = []
    for relative in sorted(files):
        record = files[relative]
        pure = PurePosixPath(relative)
        if (
            not isinstance(relative, str)
            or pure.is_absolute()
            or not pure.parts
            or any(part in {"", ".", ".."} for part in pure.parts)
            or not isinstance(record, dict)
            or set(record) != {"sha256", "size", "mode"}
            or not isinstance(record.get("sha256"), str)
            or SHA256.fullmatch(record["sha256"]) is None
            or not isinstance(record.get("size"), int)
            or isinstance(record.get("size"), bool)
            or not 1 <= record["size"] <= 16 * 1024 * 1024
            or record.get("mode") not in {"0600", "0644", "0755"}
        ):
            raise PowerStateRecoveryLabError("power lab bundle inventory is invalid")
        if relative == "echo-release.env":
            _release_image(bundle_root / relative, trusted_uid=trusted_uid)
        else:
            raw = systemd._safe_regular(
                bundle_root / relative,
                f"candidate bundle file {relative}",
                maximum=16 * 1024 * 1024,
                trusted_uid=trusted_uid,
                private=record["mode"] == "0600",
                exact_mode=int(record["mode"], 8),
            )
            if len(raw) != record["size"] or _sha256(raw) != record["sha256"]:
                raise PowerStateRecoveryLabError("power lab bundle inventory bytes drifted")
        inventory_digest.update(relative.encode())
        inventory_digest.update(b"\0")
        inventory_digest.update(bytes.fromhex(record["sha256"]))
        inventory_digest.update(b"\0")
        inventory_digest.update(record["mode"].encode("ascii"))
        inventory_digest.update(b"\0")
        checksum_lines.append(f"{record['sha256']}  {relative}\n")
    if inventory_digest.hexdigest()[:16] != candidate["operationsArtifactId"]:
        raise PowerStateRecoveryLabError("power lab bundle inventory ID is invalid")
    checksums = systemd._safe_regular(
        bundle_root / "SHA256SUMS",
        "candidate bundle checksum inventory",
        maximum=MAX_JSON_BYTES,
        trusted_uid=trusted_uid,
        private=False,
        exact_mode=0o644,
    )
    if checksums != "".join(checksum_lines).encode("ascii"):
        raise PowerStateRecoveryLabError("power lab bundle checksum inventory is invalid")
    result: dict[str, Any] = {
        "artifactId": candidate["operationsArtifactId"],
        "archiveSha256": candidate["operationsArchiveSha256"],
        "imageReference": candidate["immutableReference"],
        "manifestSha256": _sha256(manifest_raw),
        "tools": {},
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
            raise PowerStateRecoveryLabError("power lab bundle tool bytes are unbound")
        result["tools"][name] = _sha256(raw)
    return result


def _operations_plan(
    path: Path,
    *,
    candidate: Mapping[str, str],
    bundle_root: Path,
    trusted_uid: int,
) -> dict[str, Any]:
    value = _read_json(
        path, "operations physical lab plan", exact_mode=0o400, trusted_uid=trusted_uid
    )
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
    unsigned = dict(value)
    confirmations = unsigned.pop("confirmations", None)
    plan_id = unsigned.pop("planId", None)
    install = value.get("installPlan")
    config = install.get("config") if isinstance(install, dict) else None
    lab_config = value.get("config")
    expected_bundle = operations_lab._operations_bundle_identity(
        bundle_root,
        candidate,
        trusted_uid=trusted_uid,
    )
    if (
        set(value) != expected_keys
        or value.get("schemaVersion") != operations_lab.SCHEMA_VERSION
        or value.get("kind") != "echo.operations-systemd-physical-lab-plan"
        or value.get("releaseCandidate") != candidate
        or value.get("operationsBundle") != expected_bundle
        or not isinstance(plan_id, str)
        or plan_id != systemd._sha256(systemd._canonical_json(unsigned))
        or value.get("phases") != list(operations_lab.PHASES)
        or confirmations
        != {phase: f"RUN ECHO OPERATIONS LAB {phase} {plan_id}" for phase in operations_lab.PHASES}
        or not isinstance(lab_config, dict)
        or lab_config.get("bundleRoot") != str(bundle_root)
        or not isinstance(config, dict)
        or config != lab_config
        or config.get("bundleRoot") != str(bundle_root)
        or install.get("recoveryService") != systemd.RECOVERY_SERVICE_NAME
    ):
        raise PowerStateRecoveryLabError("operations plan does not bind this power lab")
    try:
        systemd._config_from_payload(lab_config)
    except systemd.OperationsSystemdError as exc:
        raise PowerStateRecoveryLabError("operations plan config is invalid") from exc
    return value


def _confirm(plan: Mapping[str, Any], phase: str, confirmation: str) -> None:
    if (
        plan.get("phases") != list(PHASES)
        or plan.get("confirmations", {}).get(phase) != confirmation
    ):
        raise PowerStateRecoveryLabError("power lab phase confirmation is invalid")


def _phase_dependencies(root: Path, phase: str, *, plan_id: str, trusted_uid: int) -> None:
    index = PHASES.index(phase)
    for previous in PHASES[:index]:
        value = _read_json(
            root / PHASE_OUTPUTS[previous],
            f"power lab evidence for {previous}",
            exact_mode=0o444,
            trusted_uid=trusted_uid,
        )
        if (
            value.get("planId") != plan_id
            or value.get("phase") != previous
            or value.get("passed") is not True
        ):
            raise PowerStateRecoveryLabError("prior power lab evidence is invalid")
    for current in PHASES[index:]:
        path = root / PHASE_OUTPUTS[current]
        if path.exists() or path.is_symlink():
            raise PowerStateRecoveryLabError("power lab phase output already exists")


def _write_phase(
    root: Path,
    phase: str,
    plan_id: str,
    details: Mapping[str, Any],
    *,
    trusted_uid: int,
) -> None:
    _write_new(
        root / PHASE_OUTPUTS[phase],
        {
            "schemaVersion": SCHEMA_VERSION,
            "kind": "echo.power-state-physical-lab-evidence",
            "planId": plan_id,
            "phase": phase,
            "passed": True,
            "details": dict(details),
        },
        mode=0o444,
        trusted_uid=trusted_uid,
    )


def _canaries(plan: Mapping[str, Any], *, trusted_uid: int) -> dict[str, Any]:
    expected = plan["canaries"]
    state = _hash_regular(
        Path(expected["state"]["path"]),
        "state recovery canary",
        trusted_uid=trusted_uid,
        exact_size=STATE_CANARY_BYTES,
    )
    nas = _hash_regular(
        Path(expected["nas"]["path"]),
        "NAS preservation canary",
        trusted_uid=trusted_uid,
        exact_size=NAS_CANARY_BYTES,
    )
    if state != expected["state"] or nas != expected["nas"]:
        raise PowerStateRecoveryLabError("state or NAS preservation canary changed")
    return {"state": state, "nas": nas}


def seed_candidate_bundle(
    *,
    candidate_index: Path,
    bundle_root: Path,
    main_container: str,
    proxy_container: str,
    confirmation: str | None,
    tools: LabTools = DEFAULT_TOOLS,
    effective_uid: int | None = None,
    trusted_uid: int = 0,
    system_name: str | None = None,
) -> dict[str, Any]:
    uid = os.geteuid() if effective_uid is None else effective_uid
    host_system = os.uname().sysname if system_name is None else system_name
    if uid != 0 or host_system != "Linux":
        raise PowerStateRecoveryLabError("power lab bundle seeding requires Linux root")
    if (
        SAFE_CONTAINER.fullmatch(main_container) is None
        or SAFE_CONTAINER.fullmatch(proxy_container) is None
    ):
        raise PowerStateRecoveryLabError("power lab container names are invalid")
    _validated_tools(tools, trusted_uid=trusted_uid)
    candidate = operations_lab._candidate_identity(candidate_index, trusted_uid=trusted_uid)
    root = bundle_root.resolve(strict=True)
    systemd._assert_owned_directory(root, "power lab bundle root", trusted_uid=trusted_uid)
    _bundle_identity(root, candidate, trusted_uid=trusted_uid)
    transaction = root / ".echo-upgrade-transaction.json"
    if transaction.exists() or transaction.is_symlink():
        raise PowerStateRecoveryLabError("pending upgrade transaction must be recovered first")
    current_main = _container_image(main_container, tools)
    current_proxy = _container_image(proxy_container, tools)
    if current_main != current_proxy or current_main == candidate["immutableReference"]:
        raise PowerStateRecoveryLabError(
            "power lab seeding requires both containers on one older immutable image"
        )
    release_env = root / "echo-release.env"
    selected = _release_image(release_env, trusted_uid=trusted_uid)
    required = f"SEED ECHO POWER STATE {candidate['indexId']} FROM {current_main}"
    if selected == current_main:
        return {
            "seeded": True,
            "alreadySeeded": True,
            "candidateIndexId": candidate["indexId"],
            "previousImage": current_main,
            "targetImage": candidate["immutableReference"],
        }
    if selected != candidate["immutableReference"]:
        raise PowerStateRecoveryLabError("candidate release environment has an unknown selection")
    if confirmation is None:
        return {
            "seeded": False,
            "alreadySeeded": False,
            "candidateIndexId": candidate["indexId"],
            "previousImage": current_main,
            "targetImage": candidate["immutableReference"],
            "requiredConfirmation": required,
        }
    if confirmation != required:
        raise PowerStateRecoveryLabError("power lab bundle seed confirmation is invalid")
    descriptor, temporary_name = tempfile.mkstemp(prefix=".echo-release.seed.", dir=root)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            stream.write(f"ECHO_OS_IMAGE={current_main}\n".encode("ascii"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, release_env)
        directory = os.open(root, os.O_RDONLY | os.O_CLOEXEC)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
    if _release_image(release_env, trusted_uid=trusted_uid) != current_main:
        raise PowerStateRecoveryLabError("candidate release environment seeding did not persist")
    return {
        "seeded": True,
        "alreadySeeded": False,
        "candidateIndexId": candidate["indexId"],
        "previousImage": current_main,
        "targetImage": candidate["immutableReference"],
    }


def build_plan(
    *,
    candidate_index: Path,
    bundle_root: Path,
    operations_lab_plan: Path,
    evidence_directory: Path,
    state_canary: Path,
    nas_canary: Path,
    main_container: str,
    proxy_container: str,
    output: Path,
    tools: LabTools = DEFAULT_TOOLS,
    effective_uid: int | None = None,
    trusted_uid: int = 0,
    system_name: str | None = None,
    boot_id: str | None = None,
) -> dict[str, Any]:
    uid = os.geteuid() if effective_uid is None else effective_uid
    host_system = os.uname().sysname if system_name is None else system_name
    if uid != 0 or host_system != "Linux":
        raise PowerStateRecoveryLabError("power lab plan requires Linux root")
    if (
        SAFE_CONTAINER.fullmatch(main_container) is None
        or SAFE_CONTAINER.fullmatch(proxy_container) is None
    ):
        raise PowerStateRecoveryLabError("power lab container names are invalid")
    _validated_tools(tools, trusted_uid=trusted_uid)
    candidate = operations_lab._candidate_identity(candidate_index, trusted_uid=trusted_uid)
    root = bundle_root.resolve(strict=True)
    systemd._assert_owned_directory(root, "power lab bundle root", trusted_uid=trusted_uid)
    bundle = _bundle_identity(root, candidate, trusted_uid=trusted_uid)
    operations = _operations_plan(
        operations_lab_plan,
        candidate=candidate,
        bundle_root=root,
        trusted_uid=trusted_uid,
    )
    evidence = evidence_directory.resolve(strict=True)
    systemd._assert_owned_directory(
        evidence, "power lab evidence directory", trusted_uid=trusted_uid
    )
    if any(
        (evidence / name).exists() or (evidence / name).is_symlink()
        for name in PHASE_OUTPUTS.values()
    ):
        raise PowerStateRecoveryLabError("power lab evidence outputs must start absent")
    release_env = root / "echo-release.env"
    transaction = root / ".echo-upgrade-transaction.json"
    if transaction.exists() or transaction.is_symlink():
        raise PowerStateRecoveryLabError("power lab requires no pending upgrade transaction")
    previous_image = _release_image(release_env, trusted_uid=trusted_uid)
    target_image = candidate["immutableReference"]
    if previous_image == target_image:
        raise PowerStateRecoveryLabError("power lab must start on an older immutable image")
    if (
        _container_image(main_container, tools) != previous_image
        or _container_image(proxy_container, tools) != previous_image
    ):
        raise PowerStateRecoveryLabError("both running containers must use the previous image")
    recovery_state = _service_state(systemd.RECOVERY_SERVICE_NAME, tools)
    if recovery_state != {"enabled": True, "active": False}:
        raise PowerStateRecoveryLabError("boot-time upgrade recovery service is not ready")
    state_record = _hash_regular(
        state_canary,
        "state recovery canary",
        trusted_uid=trusted_uid,
        exact_size=STATE_CANARY_BYTES,
    )
    nas_record = _hash_regular(
        nas_canary,
        "NAS preservation canary",
        trusted_uid=trusted_uid,
        exact_size=NAS_CANARY_BYTES,
    )
    if Path(state_record["path"]).parent != root / "data":
        raise PowerStateRecoveryLabError("state canary must be directly inside managed data")
    current_boot = _boot_id() if boot_id is None else boot_id
    try:
        current_boot = str(uuid.UUID(current_boot))
    except ValueError as exc:
        raise PowerStateRecoveryLabError("power lab boot identity is invalid") from exc
    install_config = operations["installPlan"]["config"]
    payload: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "echo.power-state-physical-lab-plan",
        "gate": GATE,
        "releaseCandidate": candidate,
        "bundleRoot": str(root),
        "operationsBundle": bundle,
        "operationsLabPlan": {
            "path": str(operations_lab_plan.resolve(strict=True)),
            "planId": operations["planId"],
        },
        "evidenceDirectory": str(evidence),
        "previousImage": previous_image,
        "targetImage": target_image,
        "releaseEnvironment": str(release_env),
        "transactionPath": str(transaction),
        "baselineBootId": current_boot,
        "containers": {"main": main_container, "proxy": proxy_container},
        "canaries": {"state": state_record, "nas": nas_record},
        "backup": {
            "directory": install_config["backupDirectory"],
            "mountpoint": install_config["backupMountpoint"],
            "credential": install_config["backupCredential"],
        },
        "hostTools": {name: str(getattr(tools, name)) for name in tools.__dataclass_fields__},
        "phases": list(PHASES),
    }
    payload["planId"] = _sha256(_canonical(payload))
    payload["confirmations"] = {
        phase: f"RUN ECHO POWER STATE LAB {phase} {payload['planId']}" for phase in PHASES
    }
    _write_new(output, payload, mode=0o400, trusted_uid=trusted_uid)
    return payload


def _load_plan(path: Path, *, trusted_uid: int) -> dict[str, Any]:
    value = _read_json(
        path, "power state physical lab plan", exact_mode=0o400, trusted_uid=trusted_uid
    )
    expected = {
        "schemaVersion",
        "kind",
        "gate",
        "releaseCandidate",
        "bundleRoot",
        "operationsBundle",
        "operationsLabPlan",
        "evidenceDirectory",
        "previousImage",
        "targetImage",
        "releaseEnvironment",
        "transactionPath",
        "baselineBootId",
        "containers",
        "canaries",
        "backup",
        "hostTools",
        "phases",
        "planId",
        "confirmations",
    }
    identity = {key: item for key, item in value.items() if key not in {"planId", "confirmations"}}
    if (
        set(value) != expected
        or value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != "echo.power-state-physical-lab-plan"
        or value.get("gate") != GATE
        or value.get("phases") != list(PHASES)
        or value.get("planId") != _sha256(_canonical(identity))
        or value.get("confirmations")
        != {phase: f"RUN ECHO POWER STATE LAB {phase} {value.get('planId')}" for phase in PHASES}
        or IMAGE_REFERENCE.fullmatch(str(value.get("previousImage"))) is None
        or IMAGE_REFERENCE.fullmatch(str(value.get("targetImage"))) is None
        or value.get("previousImage") == value.get("targetImage")
    ):
        raise PowerStateRecoveryLabError("power state physical lab plan is invalid")
    return value


def _environment(plan: Mapping[str, Any], credential: Path | None = None) -> dict[str, str]:
    value = {
        "ECHO_RELEASE_ENV": plan["releaseEnvironment"],
        "ECHO_UPGRADE_TRANSACTION": plan["transactionPath"],
        "ECHO_BACKUP_DIR": plan["backup"]["directory"],
        "ECHO_BACKUP_MOUNTPOINT": plan["backup"]["mountpoint"],
    }
    appliance_env = Path(plan["bundleRoot"]) / "appliance.env"
    if appliance_env.is_file() and not appliance_env.is_symlink():
        value["ECHO_APPLIANCE_ENV"] = str(appliance_env)
    if credential is not None:
        value["ECHO_BACKUP_PASSPHRASE_FILE"] = str(credential)
    return value


def _credential(plan: Mapping[str, Any], tools: LabTools) -> Path:
    descriptor, name = tempfile.mkstemp(prefix="echo-power-state-credential-", dir="/run")
    os.close(descriptor)
    output = Path(name)
    output.unlink()
    completed = _execute(
        [
            str(tools.systemd_creds),
            "decrypt",
            "--name=echo-backup-passphrase",
            plan["backup"]["credential"],
            str(output),
        ],
        timeout=60,
    )
    if completed.returncode != 0:
        raise PowerStateRecoveryLabError("backup credential cannot be decrypted on this device")
    _hash_regular(
        output,
        "decrypted backup credential",
        trusted_uid=0,
        exact_mode=0o600,
        maximum=4096,
    )
    return output


def _proxy_path(plan: Mapping[str, Any], *, trusted_uid: int) -> Path:
    source = Path(plan["bundleRoot"]) / "power_state_recovery_lab.py"
    raw = systemd._safe_regular(
        source,
        "power lab proxy source",
        maximum=16 * 1024 * 1024,
        trusted_uid=trusted_uid,
        private=False,
        exact_mode=0o755,
    )
    target = Path("/run") / f"{PROXY_PREFIX}{plan['planId'][:16]}"
    if target.exists() or target.is_symlink():
        target.unlink()
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o700)
    try:
        os.write(descriptor, raw)
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o700)
    finally:
        os.close(descriptor)
    return target


def _journal_probe(boot_id: str, marker: str, tools: LabTools) -> dict[str, Any]:
    completed = _execute(
        [str(tools.journalctl), "--boot", boot_id, "--output=cat", "--no-pager"],
        timeout=300,
    )
    if completed.returncode != 0 or not completed.stdout:
        raise PowerStateRecoveryLabError("previous persistent boot journal is unavailable")
    lines = completed.stdout.splitlines()
    clean_tokens = (
        "Reached target System Power Off",
        "Reached target System Reboot",
        "systemd-shutdown",
    )
    return {
        "persistentJournalAvailable": True,
        "powerCutIntentFound": lines.count(marker) == 1,
        "cleanShutdownFound": any(token in line for token in clean_tokens for line in lines),
    }


def _proxy_main(argv: Sequence[str]) -> int:
    plan_path = os.environ.get("ECHO_POWER_STATE_LAB_PLAN", "")
    mode = os.environ.get("ECHO_POWER_STATE_PROXY_MODE", "")
    real_docker = os.environ.get("ECHO_POWER_STATE_REAL_DOCKER", "")
    if not plan_path or mode not in {"hold", "fail"} or real_docker != "/usr/bin/docker":
        print("power-state Docker proxy environment is invalid", file=sys.stderr)
        return 125
    try:
        plan = _load_plan(Path(plan_path), trusted_uid=0)
        command = list(argv)
        release = _release_image(Path(plan["releaseEnvironment"]), trusted_uid=0)
        compose_up = "compose" in command and "up" in command
        if compose_up and mode == "fail" and release == plan["previousImage"]:
            print("ECHO_POWER_STATE_EXPECTED_COMPOSE_FAILURE", file=sys.stderr)
            return 42
        if compose_up and mode == "hold" and release == plan["targetImage"]:
            root = Path(plan["evidenceDirectory"]).resolve(strict=True)
            marker = (
                f"ECHO_POWER_STATE_UPDATE_CUT_ARMED plan={plan['planId']} "
                f"boot={plan['baselineBootId']}"
            )
            logger = _execute(
                [plan["hostTools"]["logger"], "--tag", "echo-power-state-lab", "--", marker],
                timeout=60,
            )
            journal = _execute([plan["hostTools"]["journalctl"], "--sync"], timeout=60)
            synced = _execute([plan["hostTools"]["sync"]], timeout=300)
            transaction = _read_json(
                Path(plan["transactionPath"]),
                "armed upgrade transaction",
                exact_mode=0o600,
                trusted_uid=0,
            )
            if (
                logger.returncode != 0
                or journal.returncode != 0
                or synced.returncode != 0
                or transaction.get("phase") != "selected"
                or transaction.get("targetImage") != plan["targetImage"]
            ):
                raise PowerStateRecoveryLabError("update could not be durably armed for power loss")
            _write_phase(
                root,
                "arm-power-cut",
                plan["planId"],
                {
                    "physicalPowerCutArmed": True,
                    "bootId": plan["baselineBootId"],
                    "marker": marker,
                    "transactionId": transaction["transactionId"],
                    "transactionPhase": "selected",
                    "targetSelected": True,
                    "nextAction": "physically-remove-and-restore-power",
                },
                trusted_uid=0,
            )
            while True:
                time.sleep(60)
        os.execv(real_docker, [real_docker, *command])
    except (OSError, PowerStateRecoveryLabError) as exc:
        print(f"power-state Docker proxy failed: {exc}", file=sys.stderr)
        return 125


def _run_upgrade(
    plan: Mapping[str, Any],
    target: str,
    tools: LabTools,
    *,
    proxy_mode: str | None = None,
    credential: Path,
    trusted_uid: int,
) -> subprocess.CompletedProcess[str]:
    environment = _environment(plan, credential)
    if proxy_mode is not None:
        proxy = _proxy_path(plan, trusted_uid=trusted_uid)
        environment.update(
            {
                "ECHO_DOCKER_BIN": str(proxy),
                PROXY_ENV: "1",
                "ECHO_POWER_STATE_LAB_PLAN": plan["planPath"],
                "ECHO_POWER_STATE_PROXY_MODE": proxy_mode,
                "ECHO_POWER_STATE_REAL_DOCKER": str(tools.docker),
            }
        )
    return _execute(
        [str(Path(plan["bundleRoot"]) / "upgrade-appliance.sh"), target],
        environment=environment,
        timeout=3600,
    )


def _run_phase_real(
    plan: dict[str, Any],
    phase: str,
    tools: LabTools,
    *,
    plan_path: Path,
    trusted_uid: int,
) -> Mapping[str, Any]:
    root = Path(plan["evidenceDirectory"])
    release_env = Path(plan["releaseEnvironment"])
    transaction = Path(plan["transactionPath"])
    containers = plan["containers"]
    previous = plan["previousImage"]
    target = plan["targetImage"]
    canaries = _canaries(plan, trusted_uid=trusted_uid)

    if phase == "baseline":
        recovery_state = _service_state(systemd.RECOVERY_SERVICE_NAME, tools)
        if (
            _boot_id() != plan["baselineBootId"]
            or _release_image(release_env, trusted_uid=trusted_uid) != previous
            or _container_image(containers["main"], tools) != previous
            or _container_image(containers["proxy"], tools) != previous
            or transaction.exists()
            or transaction.is_symlink()
            or recovery_state != {"enabled": True, "active": False}
        ):
            raise PowerStateRecoveryLabError("power lab baseline drifted")
        return {
            "previousImageVerified": True,
            "targetImage": target,
            "bootId": plan["baselineBootId"],
            "recoveryService": recovery_state,
            "canaries": canaries,
        }

    if phase == "arm-power-cut":
        credential = _credential(plan, tools)
        proxy = _proxy_path(plan, trusted_uid=trusted_uid)
        unit = f"echo-power-state-lab-{plan['planId'][:16]}.service"
        if SAFE_UNIT.fullmatch(unit) is None:
            raise PowerStateRecoveryLabError("power lab transient unit name is invalid")
        environment = _environment(plan, credential)
        environment.update(
            {
                "ECHO_DOCKER_BIN": str(proxy),
                PROXY_ENV: "1",
                "ECHO_POWER_STATE_LAB_PLAN": str(plan_path),
                "ECHO_POWER_STATE_PROXY_MODE": "hold",
                "ECHO_POWER_STATE_REAL_DOCKER": str(tools.docker),
            }
        )
        command = [
            str(tools.systemd_run),
            "--unit",
            unit.removesuffix(".service"),
            "--collect",
            "--property=Type=exec",
        ]
        for key, value in sorted(environment.items()):
            if key.startswith("ECHO_"):
                command.append(f"--setenv={key}={value}")
        command.extend([str(Path(plan["bundleRoot"]) / "upgrade-appliance.sh"), target])
        started = _execute(command, timeout=60)
        if started.returncode != 0:
            credential.unlink(missing_ok=True)
            raise PowerStateRecoveryLabError("power-loss upgrade unit could not start")
        output = root / PHASE_OUTPUTS[phase]
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline and not output.exists():
            time.sleep(1)
        if not output.exists():
            raise PowerStateRecoveryLabError("upgrade never reached the durable power-cut boundary")
        evidence = _read_json(
            output,
            "power-cut armed evidence",
            exact_mode=0o444,
            trusted_uid=trusted_uid,
        )
        if evidence.get("planId") != plan["planId"] or evidence.get("passed") is not True:
            raise PowerStateRecoveryLabError("power-cut armed evidence is invalid")
        return {"alreadyWritten": True, "unit": unit}

    if phase == "recover-power-cut":
        armed = _read_json(
            root / PHASE_OUTPUTS["arm-power-cut"],
            "power-cut armed evidence",
            exact_mode=0o444,
            trusted_uid=trusted_uid,
        )
        previous_boot = armed["details"]["bootId"]
        current_boot = _boot_id()
        journal = _journal_probe(previous_boot, armed["details"]["marker"], tools)
        result = _execute(
            [
                str(tools.systemctl),
                "show",
                systemd.RECOVERY_SERVICE_NAME,
                "--property=Result",
                "--value",
            ],
            timeout=60,
        )
        if (
            current_boot == previous_boot
            or journal
            != {
                "persistentJournalAvailable": True,
                "powerCutIntentFound": True,
                "cleanShutdownFound": False,
            }
            or transaction.exists()
            or transaction.is_symlink()
            or result.returncode != 0
            or result.stdout.strip() != "success"
            or _release_image(release_env, trusted_uid=trusted_uid) != previous
            or _container_image(containers["main"], tools) != previous
            or _container_image(containers["proxy"], tools) != previous
        ):
            raise PowerStateRecoveryLabError(
                "automatic recovery after physical power loss is unproven"
            )
        return {
            "updatePowerLossRolledBack": True,
            "bootIdChanged": True,
            "previousBootId": previous_boot,
            "currentBootId": current_boot,
            "uncleanShutdownVerified": True,
            "automaticRecoveryServiceResult": "success",
            "previousImageRestored": previous,
            "canaries": canaries,
            "journal": journal,
        }

    credential = _credential(plan, tools)
    try:
        if phase == "upgrade-success":
            completed = _run_upgrade(
                {**plan, "planPath": str(plan_path)},
                target,
                tools,
                credential=credential,
                trusted_uid=trusted_uid,
            )
            if (
                completed.returncode != 0
                or transaction.exists()
                or _release_image(release_env, trusted_uid=trusted_uid) != target
                or _container_image(containers["main"], tools) != target
                or _container_image(containers["proxy"], tools) != target
            ):
                raise PowerStateRecoveryLabError("immutable candidate upgrade did not commit")
            return {
                "immutableDigestUpgradeVerified": True,
                "previousImage": previous,
                "targetImage": target,
                "transactionCommitted": True,
                "canaries": _canaries(plan, trusted_uid=trusted_uid),
            }

        if phase == "upgrade-failure":
            completed = _run_upgrade(
                {**plan, "planPath": str(plan_path)},
                previous,
                tools,
                proxy_mode="fail",
                credential=credential,
                trusted_uid=trusted_uid,
            )
            if (
                completed.returncode == 0
                or "ECHO_POWER_STATE_EXPECTED_COMPOSE_FAILURE" not in completed.stderr
                or transaction.exists()
                or _release_image(release_env, trusted_uid=trusted_uid) != target
                or _container_image(containers["main"], tools) != target
                or _container_image(containers["proxy"], tools) != target
            ):
                raise PowerStateRecoveryLabError(
                    "failed upgrade did not restore the committed candidate"
                )
            return {
                "failedUpgradeRollbackVerified": True,
                "failureInjectedAfterSelection": True,
                "candidateImageRestored": target,
                "transactionRecovered": True,
                "canaries": _canaries(plan, trusted_uid=trusted_uid),
            }

        compose = [str(tools.docker), "compose"]
        appliance_env = Path(plan["bundleRoot"]) / "appliance.env"
        if appliance_env.exists():
            compose.extend(["--env-file", str(appliance_env)])
        compose.extend(
            [
                "--env-file",
                str(release_env),
                "--project-directory",
                plan["bundleRoot"],
                "-f",
                str(Path(plan["bundleRoot"]) / "docker-compose.yml"),
            ]
        )
        if phase == "managed-uninstall":
            down = _execute([*compose, "down", "--remove-orphans"], timeout=900)
            absent = all(
                _execute([str(tools.docker), "inspect", name], timeout=60).returncode != 0
                for name in containers.values()
            )
            if down.returncode != 0 or not absent:
                raise PowerStateRecoveryLabError("managed uninstall did not remove both containers")
            before_install = _canaries(plan, trusted_uid=trusted_uid)
            installed = _execute(
                [str(Path(plan["bundleRoot"]) / "install-appliance.sh")],
                environment=_environment(plan),
                timeout=1800,
            )
            if (
                installed.returncode != 0
                or _container_image(containers["main"], tools) != target
                or _container_image(containers["proxy"], tools) != target
                or _canaries(plan, trusted_uid=trusted_uid) != before_install
            ):
                raise PowerStateRecoveryLabError("managed reinstall did not preserve data")
            return {
                "managedUninstallDataPreserved": True,
                "composeVolumesRemoved": False,
                "containersReinstalled": True,
                "canaries": before_install,
            }

        before = {
            path.name: path.stat().st_mtime_ns
            for path in Path(plan["backup"]["directory"]).glob("*.echo-backup")
            if path.is_file() and not path.is_symlink()
        }
        backed_up = _execute(
            [str(Path(plan["bundleRoot"]) / "backup-state.sh")],
            environment=_environment(plan, credential),
            timeout=3600,
        )
        after = [
            path
            for path in Path(plan["backup"]["directory"]).glob("*.echo-backup")
            if path.is_file()
            and not path.is_symlink()
            and (path.name not in before or path.stat().st_mtime_ns != before[path.name])
        ]
        if backed_up.returncode != 0 or len(after) != 1:
            raise PowerStateRecoveryLabError("state restore lab did not create one external backup")
        backup = after[0]
        backup_record = _hash_regular(
            backup,
            "external state backup",
            trusted_uid=trusted_uid,
            exact_mode=0o600,
        )
        preflight = _execute(
            [str(Path(plan["bundleRoot"]) / "restore-state.sh"), str(backup)],
            environment=_environment(plan, credential),
            timeout=3600,
        )
        if preflight.returncode != 2 or "No live state was changed" not in preflight.stdout:
            raise PowerStateRecoveryLabError("restore preflight did not remain read-only")
        state_path = Path(plan["canaries"]["state"]["path"])
        replacement = state_path.with_name(f".{state_path.name}.power-state-mutated")
        descriptor = os.open(replacement, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            block = b"\xa5" * (1024 * 1024)
            os.write(descriptor, block)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(replacement, state_path)
        directory = os.open(state_path.parent, os.O_RDONLY | os.O_CLOEXEC)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        if _sha256(b"\xa5" * STATE_CANARY_BYTES) == plan["canaries"]["state"]["sha256"]:
            raise PowerStateRecoveryLabError("state mutation did not differ from its backup")
        rollback_before = set(Path(plan["bundleRoot"]).glob(".data.echo-rollback-*"))
        confirmation = (
            f"RESTORE sha256:{backup_record['sha256']} TO {Path(plan['bundleRoot']) / 'data'}"
        )
        restored = _execute(
            [str(Path(plan["bundleRoot"]) / "restore-state.sh"), str(backup)],
            environment={**_environment(plan, credential), "ECHO_RESTORE_CONFIRM": confirmation},
            timeout=3600,
        )
        rollback_after = set(Path(plan["bundleRoot"]).glob(".data.echo-rollback-*"))
        if (
            restored.returncode != 0
            or len(rollback_after - rollback_before) != 1
            or _canaries(plan, trusted_uid=trusted_uid) != canaries
            or _container_image(containers["main"], tools) != target
        ):
            raise PowerStateRecoveryLabError("verified state restore did not commit safely")
        return {
            "stateRestoreCommitted": True,
            "dataPreserved": True,
            "readOnlyPreflightVerified": True,
            "externalBackup": backup_record,
            "previousStateRetained": True,
            "canaries": canaries,
        }
    finally:
        credential.unlink(missing_ok=True)


def run_phase(
    *,
    plan_path: Path,
    phase: str,
    confirmation: str,
    tools: LabTools = DEFAULT_TOOLS,
    effective_uid: int | None = None,
    trusted_uid: int = 0,
    system_name: str | None = None,
) -> dict[str, Any]:
    uid = os.geteuid() if effective_uid is None else effective_uid
    host_system = os.uname().sysname if system_name is None else system_name
    if uid != 0 or host_system != "Linux":
        raise PowerStateRecoveryLabError("power lab phase requires Linux root")
    if phase not in PHASES:
        raise PowerStateRecoveryLabError("power lab phase is invalid")
    _validated_tools(tools, trusted_uid=trusted_uid)
    plan = _load_plan(plan_path, trusted_uid=trusted_uid)
    _confirm(plan, phase, confirmation)
    candidate = operations_lab._candidate_identity(
        Path(plan["releaseCandidate"]["indexPath"]), trusted_uid=trusted_uid
    )
    bundle_root = Path(plan["bundleRoot"])
    if (
        candidate != plan["releaseCandidate"]
        or _bundle_identity(bundle_root, candidate, trusted_uid=trusted_uid)
        != plan["operationsBundle"]
    ):
        raise PowerStateRecoveryLabError("power lab candidate or bundle drifted")
    root = Path(plan["evidenceDirectory"]).resolve(strict=True)
    _phase_dependencies(root, phase, plan_id=plan["planId"], trusted_uid=trusted_uid)
    details = _run_phase_real(
        plan,
        phase,
        tools,
        plan_path=plan_path.resolve(strict=True),
        trusted_uid=trusted_uid,
    )
    if details.get("alreadyWritten") is not True:
        _write_phase(root, phase, plan["planId"], details, trusted_uid=trusted_uid)
    return {"phase": phase, "planId": plan["planId"], "output": PHASE_OUTPUTS[phase]}


def _validated_phase_details(
    plan: Mapping[str, Any],
    phase: str,
    details: Mapping[str, Any],
) -> None:
    canaries = plan["canaries"]
    if phase == "baseline":
        valid = details == {
            "previousImageVerified": True,
            "targetImage": plan["targetImage"],
            "bootId": plan["baselineBootId"],
            "recoveryService": {"enabled": True, "active": False},
            "canaries": canaries,
        }
    elif phase == "arm-power-cut":
        marker = (
            f"ECHO_POWER_STATE_UPDATE_CUT_ARMED plan={plan['planId']} boot={plan['baselineBootId']}"
        )
        valid = (
            set(details)
            == {
                "physicalPowerCutArmed",
                "bootId",
                "marker",
                "transactionId",
                "transactionPhase",
                "targetSelected",
                "nextAction",
            }
            and details.get("physicalPowerCutArmed") is True
            and details.get("bootId") == plan["baselineBootId"]
            and details.get("marker") == marker
            and SHA256.fullmatch(str(details.get("transactionId"))) is not None
            and details.get("transactionPhase") == "selected"
            and details.get("targetSelected") is True
            and details.get("nextAction") == "physically-remove-and-restore-power"
        )
    elif phase == "recover-power-cut":
        previous_boot = details.get("previousBootId")
        current_boot = details.get("currentBootId")
        try:
            valid_boots = (
                str(uuid.UUID(str(previous_boot))) == previous_boot
                and str(uuid.UUID(str(current_boot))) == current_boot
                and previous_boot == plan["baselineBootId"]
                and current_boot != previous_boot
            )
        except ValueError:
            valid_boots = False
        valid = (
            set(details)
            == {
                "updatePowerLossRolledBack",
                "bootIdChanged",
                "previousBootId",
                "currentBootId",
                "uncleanShutdownVerified",
                "automaticRecoveryServiceResult",
                "previousImageRestored",
                "canaries",
                "journal",
            }
            and details.get("updatePowerLossRolledBack") is True
            and details.get("bootIdChanged") is True
            and valid_boots
            and details.get("uncleanShutdownVerified") is True
            and details.get("automaticRecoveryServiceResult") == "success"
            and details.get("previousImageRestored") == plan["previousImage"]
            and details.get("canaries") == canaries
            and details.get("journal")
            == {
                "persistentJournalAvailable": True,
                "powerCutIntentFound": True,
                "cleanShutdownFound": False,
            }
        )
    elif phase == "upgrade-success":
        valid = details == {
            "immutableDigestUpgradeVerified": True,
            "previousImage": plan["previousImage"],
            "targetImage": plan["targetImage"],
            "transactionCommitted": True,
            "canaries": canaries,
        }
    elif phase == "upgrade-failure":
        valid = details == {
            "failedUpgradeRollbackVerified": True,
            "failureInjectedAfterSelection": True,
            "candidateImageRestored": plan["targetImage"],
            "transactionRecovered": True,
            "canaries": canaries,
        }
    elif phase == "managed-uninstall":
        valid = details == {
            "managedUninstallDataPreserved": True,
            "composeVolumesRemoved": False,
            "containersReinstalled": True,
            "canaries": canaries,
        }
    else:
        backup = details.get("externalBackup")
        valid = (
            set(details)
            == {
                "stateRestoreCommitted",
                "dataPreserved",
                "readOnlyPreflightVerified",
                "externalBackup",
                "previousStateRetained",
                "canaries",
            }
            and details.get("stateRestoreCommitted") is True
            and details.get("dataPreserved") is True
            and details.get("readOnlyPreflightVerified") is True
            and details.get("previousStateRetained") is True
            and details.get("canaries") == canaries
            and isinstance(backup, dict)
            and set(backup) == {"path", "sha256", "size"}
            and isinstance(backup.get("path"), str)
            and Path(backup["path"]).is_absolute()
            and SHA256.fullmatch(str(backup.get("sha256"))) is not None
            and isinstance(backup.get("size"), int)
            and not isinstance(backup.get("size"), bool)
            and 0 < backup["size"] <= MAX_BACKUP_BYTES
        )
    if not valid:
        raise PowerStateRecoveryLabError(f"power lab evidence details are invalid: {phase}")


def verify_evidence(
    *,
    plan_path: Path,
    evidence_directory: Path,
    trusted_uid: int = 0,
) -> dict[str, Any]:
    plan = _load_plan(plan_path, trusted_uid=trusted_uid)
    candidate = operations_lab._candidate_identity(
        Path(plan["releaseCandidate"]["indexPath"]),
        trusted_uid=trusted_uid,
    )
    if (
        candidate != plan["releaseCandidate"]
        or _bundle_identity(Path(plan["bundleRoot"]), candidate, trusted_uid=trusted_uid)
        != plan["operationsBundle"]
    ):
        raise PowerStateRecoveryLabError("power lab candidate or bundle drifted")
    if evidence_directory.is_symlink() or not evidence_directory.is_dir():
        raise PowerStateRecoveryLabError("power lab evidence directory is unsafe")
    root = evidence_directory.resolve(strict=True)
    if root != Path(plan["evidenceDirectory"]):
        raise PowerStateRecoveryLabError("power lab evidence directory differs from the plan")
    systemd._assert_owned_directory(
        root,
        "power lab evidence directory",
        trusted_uid=trusted_uid,
    )
    records: dict[str, dict[str, Any]] = {}
    for phase in PHASES:
        path = root / PHASE_OUTPUTS[phase]
        value = _read_json(
            path,
            f"power lab evidence for {phase}",
            exact_mode=0o444,
            trusted_uid=trusted_uid,
        )
        raw = systemd._safe_regular(
            path,
            f"power lab evidence for {phase}",
            maximum=MAX_JSON_BYTES,
            trusted_uid=trusted_uid,
            private=False,
            exact_mode=0o444,
        )
        if (
            value.get("schemaVersion") != SCHEMA_VERSION
            or value.get("kind") != "echo.power-state-physical-lab-evidence"
            or value.get("planId") != plan["planId"]
            or value.get("phase") != phase
            or value.get("passed") is not True
            or not isinstance(value.get("details"), dict)
        ):
            raise PowerStateRecoveryLabError("power lab evidence contract is invalid")
        _validated_phase_details(plan, phase, value["details"])
        records[phase] = {"name": path.name, "sha256": _sha256(raw), "size": len(raw)}
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "echo.power-state-physical-lab-result",
        "planId": plan["planId"],
        "candidate": plan["releaseCandidate"],
        "checks": {
            check: {
                "passed": True,
                "evidence": records[
                    next(phase for phase, name in PHASE_OUTPUTS.items() if name == output)
                ],
            }
            for check, output in CHECK_OUTPUTS.items()
        },
        "phases": records,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    seed = subparsers.add_parser("seed")
    seed.add_argument("--candidate-index", type=Path, required=True)
    seed.add_argument("--bundle-root", type=Path, required=True)
    seed.add_argument("--main-container", default="echo-os")
    seed.add_argument("--proxy-container", default="echo-docker-control")
    seed.add_argument("--confirm")
    plan = subparsers.add_parser("plan")
    plan.add_argument("--candidate-index", type=Path, required=True)
    plan.add_argument("--bundle-root", type=Path, required=True)
    plan.add_argument("--operations-lab-plan", type=Path, required=True)
    plan.add_argument("--evidence-directory", type=Path, required=True)
    plan.add_argument("--state-canary", type=Path, required=True)
    plan.add_argument("--nas-canary", type=Path, required=True)
    plan.add_argument("--main-container", default="echo-os")
    plan.add_argument("--proxy-container", default="echo-docker-control")
    plan.add_argument("--output", type=Path, required=True)
    run = subparsers.add_parser("run")
    run.add_argument("--plan", type=Path, required=True)
    run.add_argument("--phase", choices=PHASES, required=True)
    run.add_argument("--confirm", required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--plan", type=Path, required=True)
    verify.add_argument("--evidence-directory", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    if os.environ.get(PROXY_ENV) == "1":
        return _proxy_main(sys.argv[1:] if argv is None else argv)
    args = _parser().parse_args(argv)
    try:
        if args.command == "seed":
            result = seed_candidate_bundle(
                candidate_index=args.candidate_index,
                bundle_root=args.bundle_root,
                main_container=args.main_container,
                proxy_container=args.proxy_container,
                confirmation=args.confirm,
            )
        elif args.command == "plan":
            result = build_plan(
                candidate_index=args.candidate_index,
                bundle_root=args.bundle_root,
                operations_lab_plan=args.operations_lab_plan,
                evidence_directory=args.evidence_directory,
                state_canary=args.state_canary,
                nas_canary=args.nas_canary,
                main_container=args.main_container,
                proxy_container=args.proxy_container,
                output=args.output,
            )
        elif args.command == "run":
            result = run_phase(
                plan_path=args.plan,
                phase=args.phase,
                confirmation=args.confirm,
            )
        else:
            result = verify_evidence(
                plan_path=args.plan,
                evidence_directory=args.evidence_directory,
            )
    except (OSError, PowerStateRecoveryLabError, subprocess.TimeoutExpired) as exc:
        print(f"power/state physical lab failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
