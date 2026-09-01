#!/usr/bin/env python3
"""Run one candidate-bound destructive bare-metal recovery lifecycle.

The private plan and source-backup record are created on the still-working
candidate Echo OS. Seven confirmed run phases then cross Recovery and normal
Echo OS boots: authenticated whole-disk installation, first cold boot, three
independent data restores, transactional Home/Agent promotion, trial
validation, Recovery commit and final cold-boot validation. Public evidence
contains only digests and redacted identities; passwords and recovery keys
never enter it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import stat
import subprocess  # nosec B404
import sys
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from deploy.appliance import device_endurance_lab as device_lab
    from deploy.appliance import nas_data_backup
    from deploy.appliance import operations_systemd as systemd
    from deploy.appliance import operations_systemd_lab as operations_lab
    from deploy.appliance import power_state_recovery_lab as power_lab
except ModuleNotFoundError:
    import device_endurance_lab as device_lab
    import nas_data_backup
    import operations_systemd as systemd
    import operations_systemd_lab as operations_lab
    import power_state_recovery_lab as power_lab


SCHEMA_VERSION = 1
GATE = "recovery_media_bare_metal_restore"
PHASES = (
    "source-backup",
    "recovery-install",
    "cold-boot",
    "restore",
    "recovery-promote",
    "trial-verify",
    "recovery-commit",
    "final-verify",
)
RUN_PHASES = PHASES[1:]
PHASE_OUTPUTS = {
    "source-backup": "bare-metal-source-backup.log",
    "recovery-install": "bare-metal-install.log",
    "cold-boot": "bare-metal-cold-boot.log",
    "restore": "bare-metal-data-restore.log",
    "recovery-promote": "bare-metal-promote.log",
    "trial-verify": "bare-metal-trial-verify.log",
    "recovery-commit": "bare-metal-commit.log",
    "final-verify": "bare-metal-final-verify.log",
}
CHECK_OUTPUTS = {
    "recoveryMediaVerified": PHASE_OUTPUTS["recovery-install"],
    "bareMetalRestored": PHASE_OUTPUTS["recovery-install"],
    "coldBootHealthy": PHASE_OUTPUTS["final-verify"],
    "dataVerified": PHASE_OUTPUTS["final-verify"],
    "offDeviceBackupRestored": PHASE_OUTPUTS["restore"],
    "authenticationStateVerified": PHASE_OUTPUTS["final-verify"],
    "auditStateVerified": PHASE_OUTPUTS["final-verify"],
    "agentStateVerified": PHASE_OUTPUTS["final-verify"],
    "nasDataVerified": PHASE_OUTPUTS["final-verify"],
}
STATE_CANARY_BYTES = 1024 * 1024
AGENT_CANARY_BYTES = 1024 * 1024
NAS_CANARY_BYTES = 1024 * 1024 * 1024
FIXED_AGENT_UID = 1000
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_OUTPUT_BYTES = 32 * 1024 * 1024
MAX_BACKUP_BYTES = 100 * 1024 * 1024 * 1024
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SNAPSHOT = re.compile(r"^[0-9a-f]{64}$")
SAFE_CONTAINER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
IMAGE_REFERENCE = re.compile(r"^[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64}$")
RECOVERY_READY = re.compile(r"^ECHO_RECOVERY_READY version=(\S+) os=([0-9a-f]{40})$")
INSTALL_AUTH = device_lab.INSTALL_AUTH
INSTALL_LOCKED = device_lab.INSTALL_LOCKED
INSTALL_COMPLETE = device_lab.INSTALL_COMPLETE
INSTALL_PLAN_AUTH = re.compile(
    r"^ECHO_INSTALL_BUNDLE_AUTHENTICATED action=plan version=(\S+) "
    r"manifest=([0-9a-f]{64}) source=([0-9a-f]{64})$"
)
INSTALL_PLAN_READY = re.compile(
    r"^ECHO_INSTALL_PLAN_READY target=(/dev/[A-Za-z0-9._/-]+) "
    r"version=(\S+) source=([0-9a-f]{64})$"
)
INSTALL_CONFIRMATION = re.compile(
    r"^  confirmation: (INSTALL-ECHO-OS:[A-Za-z0-9._-]+:[0-9a-f]{16})$"
)
PROMOTE_TOKEN = re.compile(r"^Promote or resume: (PROMOTE-ECHO-RESTORE-([0-9a-f]{24}))$")
COMMIT_TOKEN = re.compile(r"^Commit and delete old data: (COMMIT-ECHO-RESTORE-([0-9a-f]{24}))$")


class BareMetalRecoveryLabError(RuntimeError):
    """The destructive bare-metal recovery lifecycle cannot proceed safely."""


@dataclass(frozen=True)
class LabTools:
    python: Path = Path("/usr/bin/python3")
    docker: Path = Path("/usr/bin/docker")
    installer: Path = Path("/usr/bin/echo-os-installer")
    recovery: Path = Path("/usr/bin/echo-recovery")
    source_identity: Path = Path("/usr/lib/echo-os/echo-os-source-identity")
    user_backup: Path = Path("/usr/bin/echo-os-backup")
    restore_health: Path = Path("/usr/lib/echo-os/echo-restore-transaction.py")


DEFAULT_TOOLS = LabTools()
Runner = Callable[..., subprocess.CompletedProcess[str]]
BootIdReader = Callable[[], str]
MachineIdReader = Callable[[], str]


def _execute(
    command: Sequence[str],
    *,
    environment: Mapping[str, str] | None = None,
    timeout: int = 24 * 60 * 60,
) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
    }
    if environment:
        env.update(environment)
    return subprocess.run(  # nosec B603
        list(command),
        check=False,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=timeout,
        env=env,
    )


def _canonical(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_json(path: Path, label: str, *, mode: int, trusted_uid: int) -> dict[str, Any]:
    raw = systemd._safe_regular(
        path,
        label,
        maximum=MAX_JSON_BYTES,
        trusted_uid=trusted_uid,
        private=mode & 0o077 == 0,
        exact_mode=mode,
    )
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=systemd._reject_duplicate_keys)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BareMetalRecoveryLabError(f"{label} is not strict JSON") from exc
    if not isinstance(value, dict):
        raise BareMetalRecoveryLabError(f"{label} is not an object")
    return value


def _write_new(
    path: Path,
    value: Mapping[str, Any],
    *,
    mode: int,
    trusted_uid: int,
) -> None:
    if path.exists() or path.is_symlink() or path.parent.is_symlink():
        raise BareMetalRecoveryLabError("bare-metal lab output must be a new regular file")
    parent = path.parent.resolve(strict=True)
    systemd._assert_owned_directory(
        parent, "bare-metal lab output directory", trusted_uid=trusted_uid
    )
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


def _hash_regular(
    path: Path,
    label: str,
    *,
    owner_uid: int,
    modes: frozenset[int],
    maximum: int = MAX_BACKUP_BYTES,
    exact_size: int | None = None,
) -> dict[str, Any]:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise BareMetalRecoveryLabError(f"{label} is unavailable") from exc
    digest = hashlib.sha256()
    total = 0
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != owner_uid
            or stat.S_IMODE(before.st_mode) not in modes
            or not 1 <= before.st_size <= maximum
            or (exact_size is not None and before.st_size != exact_size)
        ):
            raise BareMetalRecoveryLabError(f"{label} has unsafe ownership, mode, or size")
        while block := os.read(descriptor, 1024 * 1024):
            total += len(block)
            if total > maximum:
                raise BareMetalRecoveryLabError(f"{label} exceeds its size bound")
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
            raise BareMetalRecoveryLabError(f"{label} changed while reading")
    finally:
        os.close(descriptor)
    return {"path": str(path.resolve(strict=True)), "sha256": digest.hexdigest(), "size": total}


def _boot_id() -> str:
    try:
        value = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
        parsed = uuid.UUID(value)
    except (OSError, UnicodeError, ValueError) as exc:
        raise BareMetalRecoveryLabError("kernel boot identity is unavailable") from exc
    if str(parsed) != value:
        raise BareMetalRecoveryLabError("kernel boot identity is not canonical")
    return value


def _machine_id() -> str:
    try:
        value = Path("/etc/machine-id").read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as exc:
        raise BareMetalRecoveryLabError("machine identity is unavailable") from exc
    if re.fullmatch(r"[0-9a-f]{32}", value) is None or value == "0" * 32:
        raise BareMetalRecoveryLabError("machine identity is invalid")
    return _sha256(value.encode("ascii"))


def _command(
    runner: Runner,
    command: Sequence[str],
    *,
    environment: Mapping[str, str] | None = None,
    timeout: int = 24 * 60 * 60,
) -> subprocess.CompletedProcess[str]:
    completed = runner(list(command), environment=environment, timeout=timeout)
    if (
        len(completed.stdout.encode("utf-8", "replace")) > MAX_OUTPUT_BYTES
        or len(completed.stderr.encode("utf-8", "replace")) > MAX_OUTPUT_BYTES
    ):
        raise BareMetalRecoveryLabError("bare-metal lab command output exceeds its bound")
    return completed


def _validated_tools(tools: LabTools, *, trusted_uid: int, recovery: bool) -> None:
    required = [tools.python]
    if recovery:
        required.extend([tools.installer, tools.recovery])
    else:
        required.extend(
            [tools.docker, tools.source_identity, tools.user_backup, tools.restore_health]
        )
    for tool in required:
        systemd._safe_regular(
            tool,
            f"bare-metal lab tool {tool.name}",
            maximum=64 * 1024 * 1024,
            trusted_uid=trusted_uid,
            private=False,
            exact_mode=0o755,
        )


def _recovery_identity(tools: LabTools, runner: Runner) -> dict[str, str]:
    completed = _command(runner, [str(tools.recovery), "status"], timeout=120)
    matches = [
        match for line in completed.stdout.splitlines() if (match := RECOVERY_READY.fullmatch(line))
    ]
    if completed.returncode != 0 or len(matches) != 1:
        raise BareMetalRecoveryLabError("candidate Recovery readiness is unproven")
    return {"version": matches[0].group(1), "sourceRevision": matches[0].group(2)}


def _installer_plan(
    install_bundle: Path,
    target_disk: Path,
    tools: LabTools,
    runner: Runner,
) -> dict[str, Any]:
    completed = _command(
        runner,
        [str(tools.installer), "plan", str(install_bundle), str(target_disk)],
    )
    lines = completed.stdout.splitlines()
    auth = [match for line in lines if (match := INSTALL_PLAN_AUTH.fullmatch(line))]
    ready = [match for line in lines if (match := INSTALL_PLAN_READY.fullmatch(line))]
    confirmations = [match for line in lines if (match := INSTALL_CONFIRMATION.fullmatch(line))]
    if completed.returncode != 0 or len(auth) != 1 or len(ready) != 1 or len(confirmations) != 1:
        raise BareMetalRecoveryLabError("installer did not return one authenticated read-only plan")
    if auth[0].group(1) != ready[0].group(2) or auth[0].group(3) != ready[0].group(3):
        raise BareMetalRecoveryLabError("installer bundle identity changed during planning")
    return {
        "target": ready[0].group(1),
        "version": ready[0].group(2),
        "manifestSha256": auth[0].group(2),
        "sourceSha256": ready[0].group(3),
        "confirmation": confirmations[0].group(1),
        "transcriptSha256": _sha256(completed.stdout.encode()),
    }


def _bundle_identity(
    bundle_root: Path,
    candidate: Mapping[str, str],
    *,
    trusted_uid: int,
) -> dict[str, Any]:
    try:
        result = power_lab._bundle_identity(
            bundle_root,
            candidate,
            trusted_uid=trusted_uid,
        )
    except power_lab.PowerStateRecoveryLabError as exc:
        raise BareMetalRecoveryLabError(
            "bare-metal operations bundle inventory is invalid"
        ) from exc
    manifest = _read_json(
        bundle_root / "bundle-manifest.json",
        "operations bundle manifest",
        mode=0o644,
        trusted_uid=trusted_uid,
    )
    artifact = manifest["artifact"]
    files = manifest["files"]
    expected = {
        "bare_metal_recovery_lab.py": (
            "bareMetalRecoveryLab",
            "./bare_metal_recovery_lab.py plan|run|verify",
        ),
        "nas_data_backup.py": (
            "nasDataBackup",
            "./nas_data_backup.py init|backup|check|restore",
        ),
        "restore-state.sh": (
            "restore",
            "./restore-state.sh <external-verified.echo-backup>",
        ),
        "verify-running-appliance.py": (None, None),
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
        if record != {"sha256": _sha256(raw), "size": len(raw), "mode": "0755"} or (
            entrypoint is not None and artifact["entrypoints"].get(entrypoint) != command
        ):
            raise BareMetalRecoveryLabError("bare-metal lab bundle tool bytes are unbound")
        result["tools"][name] = _sha256(raw)
    return result


def _canary(
    path: Path,
    label: str,
    *,
    parent: Path,
    owner_uid: int,
    size: int,
) -> dict[str, Any]:
    resolved_parent = parent.resolve(strict=True)
    resolved = path.resolve(strict=True)
    try:
        resolved.relative_to(resolved_parent)
    except ValueError as exc:
        raise BareMetalRecoveryLabError(f"{label} is outside its protected data root") from exc
    if resolved == resolved_parent:
        raise BareMetalRecoveryLabError(f"{label} must be a file below its data root")
    return _hash_regular(
        resolved,
        label,
        owner_uid=owner_uid,
        modes=frozenset({0o600}),
        maximum=size,
        exact_size=size,
    )


def _strictly_below(path: Path, root: Path) -> bool:
    """Return true only for a descendant, never the mount root itself."""
    return path != root and root in path.parents


def _nas_backup_receipt(
    path: Path,
    *,
    repository: Path,
    mountpoint: Path,
    deployment_root: Path,
    nas_root: Path,
    snapshot: str,
    trusted_uid: int,
) -> dict[str, Any]:
    raw = systemd._safe_regular(
        path,
        "NAS backup receipt",
        maximum=MAX_JSON_BYTES,
        trusted_uid=trusted_uid,
        private=False,
        exact_mode=0o444,
    )
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=systemd._reject_duplicate_keys)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BareMetalRecoveryLabError("NAS backup receipt is not strict JSON") from exc
    if (
        not isinstance(value, dict)
        or value.get("schemaVersion") != nas_data_backup.SCHEMA_VERSION
        or value.get("kind") != "echo.nas-data-backup.backup"
        or value.get("snapshotId") != snapshot
        or value.get("restoreTarget") != str(nas_root)
        or value.get("encrypted") is not True
        or value.get("fullReadVerified") is not True
        or not isinstance(value.get("repositoryId"), str)
        or nas_data_backup.REPOSITORY_ID.fullmatch(value["repositoryId"]) is None
        or not isinstance(value.get("source"), str)
        or not Path(value["source"]).is_absolute()
    ):
        raise BareMetalRecoveryLabError("NAS backup receipt does not prove the selected snapshot")
    # Re-run the same repository/mount/deployment safety checks without changing data.
    checked_repository, checked_nas = nas_data_backup._context(
        repository=repository,
        repository_mount=mountpoint,
        deployment_root=deployment_root,
        appliance_env=None,
        nas_root_override=nas_root,
    )
    if checked_repository != repository or checked_nas != nas_root:
        raise BareMetalRecoveryLabError("NAS backup receipt paths do not match the recovery plan")
    return {
        "path": str(path.resolve(strict=True)),
        "sha256": _sha256(raw),
        "size": len(raw),
        "repositoryId": value["repositoryId"],
        "sourceSha256": _sha256(value["source"].encode()),
    }


def _source_user_backup_state(
    path: Path,
    snapshot: str,
    *,
    trusted_uid: int,
) -> dict[str, Any]:
    value = _read_json(
        path,
        "source user backup state",
        mode=0o600,
        trusted_uid=trusted_uid,
    )
    if (
        value.get("schema") != 2
        or value.get("action") != "backup"
        or value.get("snapshot_id") != snapshot
        or value.get("verified_full_read") is not True
        or not isinstance(value.get("repository_id"), str)
        or re.fullmatch(r"[0-9a-f]{16,64}", value["repository_id"]) is None
    ):
        raise BareMetalRecoveryLabError("user/Agent backup state does not prove the snapshot")
    return {
        "repositoryId": value["repository_id"],
        "snapshotId": snapshot,
        "fullReadVerified": True,
    }


def build_plan(
    *,
    candidate_index: Path,
    bundle_root: Path,
    install_bundle: Path,
    target_disk: Path,
    recovery_key: Path,
    appliance_backup: Path,
    nas_backup_receipt: Path,
    user_snapshot: str,
    nas_repository: Path,
    nas_repository_mount: Path,
    nas_snapshot: str,
    deployment_root: Path,
    agent_root: Path,
    nas_root: Path,
    state_canary: Path,
    agent_canary: Path,
    nas_canary: Path,
    evidence_directory: Path,
    base_url: str,
    main_container: str,
    proxy_container: str,
    output: Path,
    tools: LabTools = DEFAULT_TOOLS,
    runner: Runner = _execute,
    boot_id_reader: BootIdReader = _boot_id,
    machine_id_reader: MachineIdReader = _machine_id,
    effective_uid: int | None = None,
    trusted_uid: int = 0,
    agent_uid: int = FIXED_AGENT_UID,
    system_name: str | None = None,
    machine: str | None = None,
    user_state_file: Path = Path("/var/lib/echo-os/user-backup-state.json"),
) -> dict[str, Any]:
    uid = os.geteuid() if effective_uid is None else effective_uid
    host_system = os.uname().sysname if system_name is None else system_name
    if uid != 0 or host_system != "Linux":
        raise BareMetalRecoveryLabError("bare-metal lab plan requires Linux root on the source OS")
    if SNAPSHOT.fullmatch(user_snapshot) is None or SNAPSHOT.fullmatch(nas_snapshot) is None:
        raise BareMetalRecoveryLabError("bare-metal restore requires complete backup snapshot IDs")
    if (
        SAFE_CONTAINER.fullmatch(main_container) is None
        or SAFE_CONTAINER.fullmatch(proxy_container) is None
    ):
        raise BareMetalRecoveryLabError("bare-metal lab container names are invalid")
    _validated_tools(tools, trusted_uid=trusted_uid, recovery=False)
    candidate = operations_lab._candidate_identity(candidate_index, trusted_uid=trusted_uid)
    root = bundle_root.resolve(strict=True)
    systemd._assert_owned_directory(root, "bare-metal lab bundle root", trusted_uid=trusted_uid)
    bundle = _bundle_identity(root, candidate, trusted_uid=trusted_uid)
    source_revision = _source_revision(tools.source_identity, runner)
    if source_revision != candidate["sourceRevision"]:
        raise BareMetalRecoveryLabError("source OS does not match the release candidate")
    installer_root = install_bundle.resolve(strict=True)
    if not installer_root.is_dir() or install_bundle.is_symlink():
        raise BareMetalRecoveryLabError("installer bundle directory is unsafe")
    recovery_key_record = _hash_regular(
        recovery_key,
        "private installer recovery key",
        owner_uid=trusted_uid,
        modes=frozenset({0o400, 0o600}),
        maximum=4096,
    )
    state_backup = _hash_regular(
        appliance_backup,
        "encrypted appliance-state backup",
        owner_uid=trusted_uid,
        modes=frozenset({0o600}),
    )
    nas_repository = nas_repository.resolve(strict=True)
    nas_repository_mount = nas_repository_mount.resolve(strict=True)
    deployment_root = deployment_root.resolve(strict=True)
    agent_root = agent_root.resolve(strict=True)
    nas_root = nas_root.resolve(strict=True)
    if (
        not nas_repository.is_dir()
        or nas_repository.is_symlink()
        or nas_repository_mount == Path("/")
        or (
            nas_repository != nas_repository_mount
            and nas_repository_mount not in nas_repository.parents
        )
        or nas_root == Path("/")
    ):
        raise BareMetalRecoveryLabError("bare-metal restore paths are unsafe")
    installed_bundle = _bundle_identity(deployment_root, candidate, trusted_uid=trusted_uid)
    if installed_bundle != bundle:
        raise BareMetalRecoveryLabError("source deployment is not the candidate operations bundle")
    canaries = {
        "state": _canary(
            state_canary,
            "appliance-state recovery canary",
            parent=deployment_root / "data",
            owner_uid=trusted_uid,
            size=STATE_CANARY_BYTES,
        ),
        "agent": _canary(
            agent_canary,
            "native Agent recovery canary",
            parent=agent_root,
            owner_uid=agent_uid,
            size=AGENT_CANARY_BYTES,
        ),
        "nas": _canary(
            nas_canary,
            "NAS recovery canary",
            parent=nas_root,
            owner_uid=trusted_uid,
            size=NAS_CANARY_BYTES,
        ),
    }
    user_backup = _source_user_backup_state(
        user_state_file,
        user_snapshot,
        trusted_uid=trusted_uid,
    )
    nas_receipt = _nas_backup_receipt(
        nas_backup_receipt,
        repository=nas_repository,
        mountpoint=nas_repository_mount,
        deployment_root=deployment_root,
        nas_root=nas_root,
        snapshot=nas_snapshot,
        trusted_uid=trusted_uid,
    )
    evidence = evidence_directory.resolve(strict=True)
    systemd._assert_owned_directory(
        evidence, "bare-metal evidence directory", trusted_uid=trusted_uid
    )
    if any(evidence.iterdir()):
        raise BareMetalRecoveryLabError("bare-metal evidence directory must start empty")
    output_parent = output.parent.resolve(strict=True)
    persistent_inputs = (
        Path(candidate["indexPath"]),
        root,
        installer_root,
        Path(recovery_key_record["path"]),
        Path(state_backup["path"]),
        Path(nas_receipt["path"]),
        evidence,
        output_parent,
    )
    if output_parent == evidence or any(
        not _strictly_below(path, nas_repository_mount) for path in persistent_inputs
    ):
        raise BareMetalRecoveryLabError(
            "candidate, bundle, backups, private plan and public evidence must survive in "
            "separate paths below the verified off-device mount"
        )
    gate, profile_arch, verifier_arch = device_lab._architecture(
        platform.machine() if machine is None else machine
    )
    if gate not in device_lab.GATES:
        raise BareMetalRecoveryLabError("bare-metal lab hardware architecture is unsupported")
    boot_id = boot_id_reader()
    try:
        if str(uuid.UUID(boot_id)) != boot_id:
            raise ValueError
    except ValueError as exc:
        raise BareMetalRecoveryLabError("source boot identity is invalid") from exc
    machine_id = machine_id_reader()
    if SHA256.fullmatch(machine_id) is None:
        raise BareMetalRecoveryLabError("source machine identity is invalid")
    payload: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "echo.bare-metal-recovery-physical-lab-plan",
        "gate": GATE,
        "releaseCandidate": candidate,
        "bundleRoot": str(root),
        "operationsBundle": bundle,
        "installer": {
            "bundle": str(installer_root),
            "target": str(target_disk.resolve(strict=False)),
            "recoveryKey": recovery_key_record,
        },
        "backups": {
            "applianceState": state_backup,
            "user": {
                "repository": "/mnt/echo-backup/echo-os-user",
                **user_backup,
            },
            "nas": {
                "repository": str(nas_repository),
                "mountpoint": str(nas_repository_mount),
                "snapshotId": nas_snapshot,
                "receipt": nas_receipt,
            },
        },
        "sourceSystem": {
            "bootId": boot_id,
            "machineIdSha256": machine_id,
            "sourceRevision": source_revision,
            "state": {},
            "appliance": {},
            "canaries": canaries,
        },
        "installedSystem": {
            "deploymentRoot": str(deployment_root),
            "agentRoot": str(agent_root),
            "agentUid": agent_uid,
            "nasRoot": str(nas_root),
            "architecture": profile_arch,
            "verifierArchitecture": verifier_arch,
            "sourceIdentity": str(tools.source_identity),
            "userBackup": str(tools.user_backup),
            "userState": str(user_state_file),
            "restoreHealth": str(tools.restore_health),
        },
        "appliance": {
            "baseUrl": device_lab._normalized_origin(base_url, gate),
            "mainContainer": main_container,
            "proxyContainer": proxy_container,
        },
        "evidenceDirectory": str(evidence),
        "phases": list(PHASES),
    }
    payload["sourceSystem"]["state"] = _state_recovery(payload, tools, runner)
    payload["sourceSystem"]["appliance"] = _running_verification(payload, tools, runner)
    payload["planId"] = _sha256(_canonical(payload))
    payload["confirmations"] = {
        phase: f"RUN ECHO BARE METAL RECOVERY LAB {phase} {payload['planId']}"
        for phase in RUN_PHASES
    }
    _write_new(output, payload, mode=0o400, trusted_uid=trusted_uid)
    _write_phase(
        evidence,
        "source-backup",
        payload["planId"],
        {
            "sourceRevision": source_revision,
            "sourceBootId": boot_id,
            "sourceMachineIdSha256": machine_id,
            "backups": payload["backups"],
            "canaries": canaries,
            "state": payload["sourceSystem"]["state"],
            "appliance": payload["sourceSystem"]["appliance"],
        },
        trusted_uid=trusted_uid,
    )
    return payload


def _verify_plan(plan: Mapping[str, Any], phase: str, confirmation: str) -> None:
    unsigned = dict(plan)
    confirmations = unsigned.pop("confirmations", None)
    plan_id = unsigned.pop("planId", None)
    expected_confirmations = {
        item: f"RUN ECHO BARE METAL RECOVERY LAB {item} {plan_id}" for item in RUN_PHASES
    }
    if (
        set(plan)
        != {
            "schemaVersion",
            "kind",
            "gate",
            "releaseCandidate",
            "bundleRoot",
            "operationsBundle",
            "installer",
            "backups",
            "sourceSystem",
            "installedSystem",
            "appliance",
            "evidenceDirectory",
            "phases",
            "planId",
            "confirmations",
        }
        or plan.get("schemaVersion") != SCHEMA_VERSION
        or plan.get("kind") != "echo.bare-metal-recovery-physical-lab-plan"
        or plan.get("gate") != GATE
        or plan.get("phases") != list(PHASES)
        or phase not in RUN_PHASES
        or not isinstance(plan_id, str)
        or SHA256.fullmatch(plan_id) is None
        or plan_id != _sha256(_canonical(unsigned))
        or confirmations != expected_confirmations
        or confirmations.get(phase) != confirmation
    ):
        raise BareMetalRecoveryLabError("bare-metal lab plan or confirmation is invalid")


def _phase_dependencies(
    root: Path,
    phase: str,
    *,
    plan: Mapping[str, Any],
    trusted_uid: int,
) -> None:
    index = PHASES.index(phase)
    values: dict[str, Mapping[str, Any]] = {}
    for previous in PHASES[:index]:
        value = _read_json(
            root / PHASE_OUTPUTS[previous],
            f"bare-metal evidence for {previous}",
            mode=0o444,
            trusted_uid=trusted_uid,
        )
        if (
            set(value) != {"schemaVersion", "kind", "planId", "phase", "passed", "details"}
            or value.get("schemaVersion") != SCHEMA_VERSION
            or value.get("kind") != "echo.bare-metal-recovery-physical-lab-evidence"
            or value.get("planId") != plan["planId"]
            or value.get("phase") != previous
            or value.get("passed") is not True
            or not isinstance(value.get("details"), dict)
        ):
            raise BareMetalRecoveryLabError("prior bare-metal evidence is invalid")
        values[previous] = value["details"]
        _validate_phase_details(plan, previous, value["details"], values)
    for current in PHASES[index:]:
        if (root / PHASE_OUTPUTS[current]).exists() or (root / PHASE_OUTPUTS[current]).is_symlink():
            raise BareMetalRecoveryLabError("bare-metal evidence sequence is stale")


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
            "kind": "echo.bare-metal-recovery-physical-lab-evidence",
            "planId": plan_id,
            "phase": phase,
            "passed": True,
            "details": dict(details),
        },
        mode=0o444,
        trusted_uid=trusted_uid,
    )


def _source_revision(path: Path, runner: Runner) -> str:
    completed = _command(runner, [str(path), "--commit"], timeout=60)
    value = completed.stdout.strip()
    if completed.returncode != 0 or re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise BareMetalRecoveryLabError("installed source identity is unavailable")
    return value


def _running_verification(
    plan: Mapping[str, Any], tools: LabTools, runner: Runner
) -> dict[str, Any]:
    appliance = plan["appliance"]
    installed = plan["installedSystem"]
    completed = _command(
        runner,
        [
            str(tools.python),
            str(Path(plan["bundleRoot"]) / "verify-running-appliance.py"),
            "--base-url",
            appliance["baseUrl"],
            "--main-container",
            appliance["mainContainer"],
            "--proxy-container",
            appliance["proxyContainer"],
            "--expected-arch",
            installed["verifierArchitecture"],
            "--require-clean-bundle",
            "--require-omv",
        ],
        timeout=1800,
    )
    if completed.returncode != 0:
        raise BareMetalRecoveryLabError("installed appliance verification failed")
    try:
        value = json.loads(completed.stdout, object_pairs_hook=systemd._reject_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise BareMetalRecoveryLabError("running verifier returned invalid JSON") from exc
    if (
        not isinstance(value, dict)
        or value.get("bundle_verified") is not True
        or value.get("bundle_dirty") is not False
        or value.get("login") != 200
        or value.get("workbench") != 200
        or value.get("architecture") != installed["verifierArchitecture"]
        or value.get("audit_verify") != 200
        or value.get("main_has_docker_socket") is not False
        or value.get("proxy_network_internal") is not True
        or value.get("main_effective_capabilities") != 0
        or value.get("proxy_effective_capabilities") != 0
        or value.get("no_new_privileges") is not True
        or value.get("approval") != 200
        or value.get("approval_replay") != 403
        or value.get("protected_stop") != 403
    ):
        raise BareMetalRecoveryLabError("installed appliance did not satisfy the recovery probe")
    for container in (appliance["mainContainer"], appliance["proxyContainer"]):
        inspected = _command(
            runner,
            [str(tools.docker), "inspect", "--format", "{{.Config.Image}}", container],
            timeout=60,
        )
        if (
            inspected.returncode != 0
            or inspected.stdout.strip() != plan["releaseCandidate"]["immutableReference"]
        ):
            raise BareMetalRecoveryLabError("installed appliance image is not the candidate")
    return {
        "bundleVerified": True,
        "immutableImageVerified": True,
        "administratorLoginReady": True,
        "agentWorkbenchReady": True,
        "auditVerified": True,
        "dockerApprovalVerified": True,
        "runtimeArchitecture": installed["verifierArchitecture"],
    }


def _state_recovery(plan: Mapping[str, Any], tools: LabTools, runner: Runner) -> dict[str, Any]:
    root = Path(plan["installedSystem"]["deploymentRoot"])
    compose = [
        str(tools.docker),
        "compose",
        "--env-file",
        str(root / "echo-release.env"),
        "--project-directory",
        str(root),
        "-f",
        str(root / "docker-compose.yml"),
        "exec",
        "-T",
        plan["appliance"]["mainContainer"],
        "python",
        "-m",
        "appliance.state_recovery",
        "--state-dir",
        "/data",
    ]
    appliance_env = root / "appliance.env"
    if appliance_env.exists() and not appliance_env.is_symlink():
        compose[2:2] = ["--env-file", str(appliance_env)]
    completed = _command(runner, compose, timeout=600)
    if completed.returncode != 0:
        raise BareMetalRecoveryLabError("restored appliance state validation failed")
    try:
        value = json.loads(completed.stdout, object_pairs_hook=systemd._reject_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise BareMetalRecoveryLabError("state recovery validator returned invalid JSON") from exc
    if (
        not isinstance(value, dict)
        or value.get("ok") is not True
        or value.get("schemaCurrent") is not True
        or value.get("administrator") != "admin"
        or not isinstance(value.get("auditEntries"), int)
        or value.get("nasUserDataIncluded") is not False
    ):
        raise BareMetalRecoveryLabError("authentication or audit state was not restored")
    return {
        "authenticationStateVerified": True,
        "auditStateVerified": True,
        "auditEntries": value["auditEntries"],
        "auditSigningKeyId": value["auditSigningKeyId"],
        "sessionNotBefore": value["sessionNotBefore"],
        "schemaVersion": value["schemaVersion"],
    }


def _user_state(
    plan: Mapping[str, Any],
    snapshot: str,
    *,
    action: str,
    trusted_uid: int,
) -> dict[str, Any]:
    value = _read_json(
        Path(plan["installedSystem"]["userState"]),
        "user backup state",
        mode=0o600,
        trusted_uid=trusted_uid,
    )
    if (
        value.get("snapshot_id") != snapshot
        or value.get("action") != action
        or value.get("verified_full_read") is not True
    ):
        raise BareMetalRecoveryLabError("restored user/Agent snapshot state is invalid")
    return {
        "snapshotId": snapshot,
        "action": action,
        "fullReadVerified": True,
    }


def _restore_health(plan: Mapping[str, Any], tools: LabTools, runner: Runner) -> str:
    completed = _command(
        runner,
        [
            str(tools.python),
            str(tools.restore_health),
            "health",
            "--home-root",
            "/home",
            "--var-root",
            "/var",
        ],
        timeout=600,
    )
    if completed.returncode != 0:
        raise BareMetalRecoveryLabError("Home/Agent restore health failed")
    return completed.stdout.strip()


def _recovered_canaries(
    plan: Mapping[str, Any],
    *,
    trusted_uid: int,
    include_agent: bool,
) -> dict[str, Any]:
    expected = plan["sourceSystem"]["canaries"]
    actual = {
        "state": _hash_regular(
            Path(expected["state"]["path"]),
            "restored appliance-state canary",
            owner_uid=trusted_uid,
            modes=frozenset({0o600}),
            maximum=STATE_CANARY_BYTES,
            exact_size=STATE_CANARY_BYTES,
        ),
        "nas": _hash_regular(
            Path(expected["nas"]["path"]),
            "restored NAS canary",
            owner_uid=trusted_uid,
            modes=frozenset({0o600}),
            maximum=NAS_CANARY_BYTES,
            exact_size=NAS_CANARY_BYTES,
        ),
    }
    if include_agent:
        actual["agent"] = _hash_regular(
            Path(expected["agent"]["path"]),
            "restored native Agent canary",
            owner_uid=plan["installedSystem"]["agentUid"],
            modes=frozenset({0o600}),
            maximum=AGENT_CANARY_BYTES,
            exact_size=AGENT_CANARY_BYTES,
        )
    expected_subset = {name: expected[name] for name in actual}
    if actual != expected_subset:
        raise BareMetalRecoveryLabError("restored data canary differs from the source backup")
    return actual


def _state_matches_source(plan: Mapping[str, Any], state: Mapping[str, Any]) -> None:
    if state != plan["sourceSystem"]["state"]:
        raise BareMetalRecoveryLabError(
            "restored authentication or audit identity differs from the source backup"
        )


def _transaction_command(
    action: str,
    target: str,
    tools: LabTools,
    runner: Runner,
) -> tuple[str, str]:
    first = _command(runner, [str(tools.recovery), action, target], timeout=3600)
    if first.returncode != 0:
        raise BareMetalRecoveryLabError(f"Recovery {action} failed")
    pattern = PROMOTE_TOKEN if action == "restore-plan" else COMMIT_TOKEN
    matches = [match for line in first.stdout.splitlines() if (match := pattern.fullmatch(line))]
    if len(matches) != 1:
        raise BareMetalRecoveryLabError(f"Recovery {action} returned no unique transaction token")
    return matches[0].group(1), matches[0].group(2)


def _run_phase_real(
    plan: Mapping[str, Any],
    phase: str,
    tools: LabTools,
    runner: Runner,
    boot_id_reader: BootIdReader,
    machine_id_reader: MachineIdReader,
    installer_confirmation: str | None,
    *,
    trusted_uid: int,
) -> Mapping[str, Any]:
    candidate = plan["releaseCandidate"]
    installer = plan["installer"]
    backups = plan["backups"]
    installed = plan["installedSystem"]
    recovery_phase = phase in {"recovery-install", "recovery-promote", "recovery-commit"}
    if recovery_phase:
        _validated_tools(tools, trusted_uid=trusted_uid, recovery=True)
        recovery = _recovery_identity(tools, runner)
        if recovery["sourceRevision"] != candidate["sourceRevision"] or recovery[
            "version"
        ] != candidate["releaseTag"].removeprefix("echo-appliance-v"):
            raise BareMetalRecoveryLabError("Recovery source drifted from the candidate")
    else:
        _validated_tools(tools, trusted_uid=trusted_uid, recovery=False)
        source = _source_revision(Path(installed["sourceIdentity"]), runner)
        if source != candidate["sourceRevision"]:
            raise BareMetalRecoveryLabError("installed OS source drifted from the candidate")
        installed_bundle = _bundle_identity(
            Path(installed["deploymentRoot"]), candidate, trusted_uid=trusted_uid
        )
        if installed_bundle != plan["operationsBundle"]:
            raise BareMetalRecoveryLabError(
                "installed operations bundle drifted from the candidate"
            )

    current_boot = boot_id_reader()
    try:
        if str(uuid.UUID(current_boot)) != current_boot:
            raise ValueError
    except ValueError as exc:
        raise BareMetalRecoveryLabError("current boot identity is invalid") from exc
    if current_boot == plan["sourceSystem"]["bootId"]:
        raise BareMetalRecoveryLabError("bare-metal phase did not leave the source-system boot")

    if phase == "recovery-install":
        current = _installer_plan(
            Path(installer["bundle"]), Path(installer["target"]), tools, runner
        )
        if current["version"] != candidate["releaseTag"].removeprefix("echo-appliance-v"):
            raise BareMetalRecoveryLabError("installer bundle belongs to another release")
        if installer_confirmation != current["confirmation"]:
            raise BareMetalRecoveryLabError(
                f"installer confirmation is required exactly as planned: {current['confirmation']}"
            )
        recovery_key = _hash_regular(
            Path(installer["recoveryKey"]["path"]),
            "private installer recovery key",
            owner_uid=trusted_uid,
            modes=frozenset({0o400, 0o600}),
            maximum=4096,
        )
        if recovery_key != installer["recoveryKey"]:
            raise BareMetalRecoveryLabError("private installer recovery key drifted")
        completed = _command(
            runner,
            [
                str(tools.installer),
                "install",
                installer["bundle"],
                installer["target"],
                current["confirmation"],
            ],
            environment={"ECHO_INSTALL_RECOVERY_KEY_FILE": installer["recoveryKey"]["path"]},
        )
        lines = completed.stdout.splitlines()
        authenticated = [match for line in lines if (match := INSTALL_AUTH.fullmatch(line))]
        locked = [match for line in lines if (match := INSTALL_LOCKED.fullmatch(line))]
        complete = [match for line in lines if (match := INSTALL_COMPLETE.fullmatch(line))]
        if (
            completed.returncode != 0
            or len(authenticated) != 1
            or len(locked) != 1
            or len(complete) != 1
            or lines.count(
                "  verified: exact uncompressed image bytes by direct post-flush readback"
            )
            != 1
            or authenticated[0].group(1) != current["version"]
            or authenticated[0].group(2) != current["manifestSha256"]
            or authenticated[0].group(3) != current["sourceSha256"]
            or locked[0].group(1) != complete[0].group(1)
            or complete[0].group(2) != current["version"]
            or complete[0].group(3) != current["sourceSha256"]
        ):
            raise BareMetalRecoveryLabError(
                "authenticated whole-disk installation did not complete"
            )
        return {
            "recoveryMediaVerified": True,
            "bareMetalRestored": True,
            "sourceRevision": candidate["sourceRevision"],
            "recoveryVersion": recovery["version"],
            "installerManifestSha256": current["manifestSha256"],
            "installerSourceSha256": current["sourceSha256"],
            "installerPlanTranscriptSha256": current["transcriptSha256"],
            "targetIdentitySha256": _sha256(f"{locked[0].group(1)}\0{locked[0].group(2)}".encode()),
            "transcriptSha256": _sha256(completed.stdout.encode()),
            "postWriteReadbackVerified": True,
            "recoveryBootId": current_boot,
        }

    if phase == "cold-boot":
        installed_record = _read_json(
            Path(plan["evidenceDirectory"]) / PHASE_OUTPUTS["recovery-install"],
            "bare-metal install evidence",
            mode=0o444,
            trusted_uid=trusted_uid,
        )
        if current_boot == installed_record["details"]["recoveryBootId"]:
            raise BareMetalRecoveryLabError("newly installed OS has not cold-booted")
        machine_id = machine_id_reader()
        if (
            SHA256.fullmatch(machine_id) is None
            or machine_id == plan["sourceSystem"]["machineIdSha256"]
        ):
            raise BareMetalRecoveryLabError(
                "replacement OS did not establish a new machine identity"
            )
        return {
            "firstColdBootHealthy": True,
            "recoveryBootId": installed_record["details"]["recoveryBootId"],
            "installedBootId": current_boot,
            "replacementMachineIdSha256": machine_id,
            "sourceMachineIdentityChanged": True,
            "sourceRevision": candidate["sourceRevision"],
            "appliance": _running_verification(plan, tools, runner),
        }

    if phase == "restore":
        deployment = Path(installed["deploymentRoot"])
        state = backups["applianceState"]
        current_state = _hash_regular(
            Path(state["path"]),
            "encrypted appliance-state backup",
            owner_uid=trusted_uid,
            modes=frozenset({0o600}),
        )
        if current_state != state:
            raise BareMetalRecoveryLabError("appliance-state backup drifted")
        for canary in plan["sourceSystem"]["canaries"].values():
            path = Path(canary["path"])
            if path.exists() or path.is_symlink():
                raise BareMetalRecoveryLabError(
                    "replacement target already contains a source recovery canary"
                )
        state_confirmation = f"RESTORE sha256:{state['sha256']} TO {deployment / 'data'}"
        restored_state = _command(
            runner,
            [str(deployment / "restore-state.sh"), state["path"]],
            environment={"ECHO_RESTORE_CONFIRM": state_confirmation},
        )
        if (
            restored_state.returncode != 0
            or "Echo state restore complete." not in restored_state.stdout
        ):
            raise BareMetalRecoveryLabError("appliance state restore did not commit")
        user = backups["user"]
        restored_user = _command(
            runner,
            [str(tools.user_backup), "restore", user["snapshotId"]],
        )
        if restored_user.returncode != 0 or "ECHO_USER_RESTORE_STAGED" not in restored_user.stdout:
            raise BareMetalRecoveryLabError("user/Agent restore was not staged")
        nas = backups["nas"]
        nas_confirmation = f"RESTORE ECHO NAS {nas['snapshotId']} TO {installed['nasRoot']}"
        restored_nas = _command(
            runner,
            [
                str(Path(plan["bundleRoot"]) / "nas_data_backup.py"),
                "restore",
                "--repository",
                nas["repository"],
                "--repository-mount",
                nas["mountpoint"],
                "--deployment-root",
                installed["deploymentRoot"],
                "--snapshot",
                nas["snapshotId"],
                "--confirm",
                nas_confirmation,
            ],
            environment={"NAS_STORAGE": installed["nasRoot"]},
        )
        try:
            nas_report = json.loads(restored_nas.stdout)
        except json.JSONDecodeError as exc:
            raise BareMetalRecoveryLabError("NAS restore returned invalid evidence") from exc
        if (
            restored_nas.returncode != 0
            or not isinstance(nas_report, dict)
            or nas_report.get("snapshotId") != nas["snapshotId"]
            or nas_report.get("atomicPromotion") is not True
            or nas_report.get("fullReadVerified") is not True
        ):
            raise BareMetalRecoveryLabError("NAS data restore did not commit atomically")
        restored_appliance_state = _state_recovery(plan, tools, runner)
        _state_matches_source(plan, restored_appliance_state)
        restored_canaries = _recovered_canaries(plan, trusted_uid=trusted_uid, include_agent=False)
        return {
            "offDeviceBackupRestored": True,
            "applianceStateBackupSha256": state["sha256"],
            "applianceState": restored_appliance_state,
            "userSnapshotId": user["snapshotId"],
            "userAgentRestoreStaged": True,
            "nasSnapshotId": nas["snapshotId"],
            "nasAtomicPromotion": True,
            "nasEntries": nas_report.get("entries"),
            "nasLogicalBytes": nas_report.get("logicalBytes"),
            "canaries": restored_canaries,
            "bootId": current_boot,
        }

    if phase == "recovery-promote":
        restore_record = _read_json(
            Path(plan["evidenceDirectory"]) / PHASE_OUTPUTS["restore"],
            "bare-metal data restore evidence",
            mode=0o444,
            trusted_uid=trusted_uid,
        )
        if current_boot == restore_record["details"]["bootId"]:
            raise BareMetalRecoveryLabError("Home/Agent promotion did not boot Recovery")
        token, transaction = _transaction_command(
            "restore-plan", installer["target"], tools, runner
        )
        promoted = _command(
            runner,
            [str(tools.recovery), "restore-promote", installer["target"], token],
            timeout=3600,
        )
        if (
            promoted.returncode != 0
            or f"ECHO_RESTORE_PROMOTED transaction={transaction} phase=trial" not in promoted.stdout
        ):
            raise BareMetalRecoveryLabError("Recovery did not promote the Home/Agent transaction")
        return {
            "agentRestorePromoted": True,
            "transactionId": transaction,
            "recoveryBootId": current_boot,
            "promotionTranscriptSha256": _sha256(promoted.stdout.encode()),
        }

    if phase == "trial-verify":
        promoted = _read_json(
            Path(plan["evidenceDirectory"]) / PHASE_OUTPUTS["recovery-promote"],
            "bare-metal promotion evidence",
            mode=0o444,
            trusted_uid=trusted_uid,
        )
        if current_boot == promoted["details"]["recoveryBootId"]:
            raise BareMetalRecoveryLabError("promoted restore has not trial-booted")
        cold = _read_json(
            Path(plan["evidenceDirectory"]) / PHASE_OUTPUTS["cold-boot"],
            "bare-metal cold-boot evidence",
            mode=0o444,
            trusted_uid=trusted_uid,
        )
        if machine_id_reader() != cold["details"]["replacementMachineIdSha256"]:
            raise BareMetalRecoveryLabError("replacement machine identity drifted during trial")
        health = _restore_health(plan, tools, runner)
        transaction = promoted["details"]["transactionId"]
        if (
            health
            != f"ECHO_RESTORE_TRANSACTION_READY phase=promoted trial=yes transaction={transaction}"
        ):
            raise BareMetalRecoveryLabError("Home/Agent trial transaction is unhealthy")
        state = _state_recovery(plan, tools, runner)
        _state_matches_source(plan, state)
        canaries = _recovered_canaries(plan, trusted_uid=trusted_uid, include_agent=True)
        return {
            "trialBootHealthy": True,
            "transactionId": transaction,
            "bootId": current_boot,
            "applianceState": state,
            "userAgentState": _user_state(
                plan,
                backups["user"]["snapshotId"],
                action="restore-staged",
                trusted_uid=trusted_uid,
            ),
            "nasTree": nas_data_backup._tree_safe(Path(installed["nasRoot"])),
            "canaries": canaries,
            "appliance": _running_verification(plan, tools, runner),
        }

    if phase == "recovery-commit":
        promoted = _read_json(
            Path(plan["evidenceDirectory"]) / PHASE_OUTPUTS["recovery-promote"],
            "bare-metal promotion evidence",
            mode=0o444,
            trusted_uid=trusted_uid,
        )
        trial = _read_json(
            Path(plan["evidenceDirectory"]) / PHASE_OUTPUTS["trial-verify"],
            "bare-metal trial evidence",
            mode=0o444,
            trusted_uid=trusted_uid,
        )
        if current_boot == trial["details"]["bootId"]:
            raise BareMetalRecoveryLabError("Home/Agent commit did not boot Recovery")
        token, transaction = _transaction_command(
            "restore-status", installer["target"], tools, runner
        )
        if transaction != promoted["details"]["transactionId"]:
            raise BareMetalRecoveryLabError("Recovery transaction identity drifted before commit")
        committed = _command(
            runner,
            [str(tools.recovery), "restore-commit", installer["target"], token],
            timeout=3600,
        )
        if (
            committed.returncode != 0
            or f"ECHO_RESTORE_COMMITTED transaction={transaction}" not in committed.stdout
        ):
            raise BareMetalRecoveryLabError("Recovery did not commit the restored Home/Agent data")
        return {
            "agentRestoreCommitted": True,
            "transactionId": transaction,
            "oldDataDeleted": True,
            "recoveryBootId": current_boot,
            "commitTranscriptSha256": _sha256(committed.stdout.encode()),
        }

    committed = _read_json(
        Path(plan["evidenceDirectory"]) / PHASE_OUTPUTS["recovery-commit"],
        "bare-metal commit evidence",
        mode=0o444,
        trusted_uid=trusted_uid,
    )
    if current_boot == committed["details"]["recoveryBootId"]:
        raise BareMetalRecoveryLabError("committed restore has not completed its final cold boot")
    cold = _read_json(
        Path(plan["evidenceDirectory"]) / PHASE_OUTPUTS["cold-boot"],
        "bare-metal cold-boot evidence",
        mode=0o444,
        trusted_uid=trusted_uid,
    )
    if machine_id_reader() != cold["details"]["replacementMachineIdSha256"]:
        raise BareMetalRecoveryLabError("replacement machine identity drifted after commit")
    if _restore_health(plan, tools, runner) != "ECHO_RESTORE_TRANSACTION_READY phase=none trial=no":
        raise BareMetalRecoveryLabError("committed Home/Agent transaction remains pending")
    state = _state_recovery(plan, tools, runner)
    _state_matches_source(plan, state)
    user = _user_state(
        plan,
        backups["user"]["snapshotId"],
        action="restore-committed",
        trusted_uid=trusted_uid,
    )
    nas_tree = nas_data_backup._tree_safe(Path(installed["nasRoot"]))
    restore_record = _read_json(
        Path(plan["evidenceDirectory"]) / PHASE_OUTPUTS["restore"],
        "bare-metal data restore evidence",
        mode=0o444,
        trusted_uid=trusted_uid,
    )
    if nas_tree != {
        "entries": restore_record["details"]["nasEntries"],
        "logicalBytes": restore_record["details"]["nasLogicalBytes"],
    }:
        raise BareMetalRecoveryLabError("restored NAS tree is empty")
    canaries = _recovered_canaries(plan, trusted_uid=trusted_uid, include_agent=True)
    return {
        "coldBootHealthy": True,
        "dataVerified": True,
        "authenticationStateVerified": state["authenticationStateVerified"],
        "auditStateVerified": state["auditStateVerified"],
        "agentStateVerified": True,
        "nasDataVerified": True,
        "transactionId": committed["details"]["transactionId"],
        "bootId": current_boot,
        "applianceState": state,
        "userAgentState": user,
        "nasTree": nas_tree,
        "canaries": canaries,
        "appliance": _running_verification(plan, tools, runner),
    }


def run_phase(
    *,
    plan_path: Path,
    phase: str,
    confirmation: str,
    installer_confirmation: str | None = None,
    tools: LabTools = DEFAULT_TOOLS,
    runner: Runner = _execute,
    boot_id_reader: BootIdReader = _boot_id,
    machine_id_reader: MachineIdReader = _machine_id,
    effective_uid: int | None = None,
    trusted_uid: int = 0,
    system_name: str | None = None,
) -> dict[str, Any]:
    uid = os.geteuid() if effective_uid is None else effective_uid
    host_system = os.uname().sysname if system_name is None else system_name
    if uid != 0 or host_system != "Linux" or phase not in RUN_PHASES:
        raise BareMetalRecoveryLabError(
            "bare-metal lab phase requires Linux root and a valid phase"
        )
    plan = _read_json(
        plan_path,
        "bare-metal recovery lab plan",
        mode=0o400,
        trusted_uid=trusted_uid,
    )
    _verify_plan(plan, phase, confirmation)
    candidate = operations_lab._candidate_identity(
        Path(plan["releaseCandidate"]["indexPath"]), trusted_uid=trusted_uid
    )
    bundle_root = Path(plan["bundleRoot"])
    if (
        candidate != plan["releaseCandidate"]
        or _bundle_identity(bundle_root, candidate, trusted_uid=trusted_uid)
        != plan["operationsBundle"]
    ):
        raise BareMetalRecoveryLabError("bare-metal candidate or bundle drifted")
    root = Path(plan["evidenceDirectory"]).resolve(strict=True)
    _phase_dependencies(root, phase, plan=plan, trusted_uid=trusted_uid)
    details = _run_phase_real(
        plan,
        phase,
        tools,
        runner,
        boot_id_reader,
        machine_id_reader,
        installer_confirmation,
        trusted_uid=trusted_uid,
    )
    _write_phase(root, phase, plan["planId"], details, trusted_uid=trusted_uid)
    return {"phase": phase, "planId": plan["planId"], "output": PHASE_OUTPUTS[phase]}


def _valid_sha(value: object) -> bool:
    return isinstance(value, str) and SHA256.fullmatch(value) is not None


def _valid_boot_id(value: object) -> bool:
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value
    except ValueError:
        return False


def _valid_state(value: object, expected: Mapping[str, Any]) -> bool:
    return isinstance(value, dict) and value == expected


def _valid_appliance(value: object, expected_architecture: str) -> bool:
    return isinstance(value, dict) and value == {
        "bundleVerified": True,
        "immutableImageVerified": True,
        "administratorLoginReady": True,
        "agentWorkbenchReady": True,
        "auditVerified": True,
        "dockerApprovalVerified": True,
        "runtimeArchitecture": expected_architecture,
    }


def _validate_phase_details(
    plan: Mapping[str, Any],
    phase: str,
    details: Mapping[str, Any],
    values: Mapping[str, Mapping[str, Any]],
) -> None:
    source = plan["sourceSystem"]
    candidate = plan["releaseCandidate"]
    backups = plan["backups"]
    architecture = plan["installedSystem"]["verifierArchitecture"]
    if phase == "source-backup":
        valid = details == {
            "sourceRevision": source["sourceRevision"],
            "sourceBootId": source["bootId"],
            "sourceMachineIdSha256": source["machineIdSha256"],
            "backups": backups,
            "canaries": source["canaries"],
            "state": source["state"],
            "appliance": source["appliance"],
        }
    elif phase == "recovery-install":
        valid = (
            set(details)
            == {
                "recoveryMediaVerified",
                "bareMetalRestored",
                "sourceRevision",
                "recoveryVersion",
                "installerManifestSha256",
                "installerSourceSha256",
                "installerPlanTranscriptSha256",
                "targetIdentitySha256",
                "transcriptSha256",
                "postWriteReadbackVerified",
                "recoveryBootId",
            }
            and details.get("recoveryMediaVerified") is True
            and details.get("bareMetalRestored") is True
            and details.get("sourceRevision") == candidate["sourceRevision"]
            and details.get("recoveryVersion")
            == candidate["releaseTag"].removeprefix("echo-appliance-v")
            and all(
                _valid_sha(details.get(name))
                for name in (
                    "installerManifestSha256",
                    "installerSourceSha256",
                    "installerPlanTranscriptSha256",
                    "targetIdentitySha256",
                    "transcriptSha256",
                )
            )
            and details.get("postWriteReadbackVerified") is True
            and _valid_boot_id(details.get("recoveryBootId"))
            and details.get("recoveryBootId") != source["bootId"]
        )
    elif phase == "cold-boot":
        recovery = values["recovery-install"]
        valid = (
            set(details)
            == {
                "firstColdBootHealthy",
                "recoveryBootId",
                "installedBootId",
                "replacementMachineIdSha256",
                "sourceMachineIdentityChanged",
                "sourceRevision",
                "appliance",
            }
            and details.get("firstColdBootHealthy") is True
            and details.get("recoveryBootId") == recovery["recoveryBootId"]
            and _valid_boot_id(details.get("installedBootId"))
            and details.get("installedBootId") not in {source["bootId"], recovery["recoveryBootId"]}
            and _valid_sha(details.get("replacementMachineIdSha256"))
            and details.get("replacementMachineIdSha256") != source["machineIdSha256"]
            and details.get("sourceMachineIdentityChanged") is True
            and details.get("sourceRevision") == candidate["sourceRevision"]
            and _valid_appliance(details.get("appliance"), architecture)
        )
    elif phase == "restore":
        cold = values["cold-boot"]
        valid = (
            set(details)
            == {
                "offDeviceBackupRestored",
                "applianceStateBackupSha256",
                "applianceState",
                "userSnapshotId",
                "userAgentRestoreStaged",
                "nasSnapshotId",
                "nasAtomicPromotion",
                "nasEntries",
                "nasLogicalBytes",
                "canaries",
                "bootId",
            }
            and details.get("offDeviceBackupRestored") is True
            and details.get("applianceStateBackupSha256") == backups["applianceState"]["sha256"]
            and _valid_state(details.get("applianceState"), source["state"])
            and details.get("userSnapshotId") == backups["user"]["snapshotId"]
            and details.get("userAgentRestoreStaged") is True
            and details.get("nasSnapshotId") == backups["nas"]["snapshotId"]
            and details.get("nasAtomicPromotion") is True
            and isinstance(details.get("nasEntries"), int)
            and not isinstance(details.get("nasEntries"), bool)
            and details["nasEntries"] >= 1
            and isinstance(details.get("nasLogicalBytes"), int)
            and not isinstance(details.get("nasLogicalBytes"), bool)
            and details["nasLogicalBytes"] >= NAS_CANARY_BYTES
            and details.get("canaries")
            == {name: source["canaries"][name] for name in ("state", "nas")}
            and details.get("bootId") == cold["installedBootId"]
        )
    elif phase == "recovery-promote":
        valid = (
            set(details)
            == {
                "agentRestorePromoted",
                "transactionId",
                "recoveryBootId",
                "promotionTranscriptSha256",
            }
            and details.get("agentRestorePromoted") is True
            and isinstance(details.get("transactionId"), str)
            and re.fullmatch(r"[0-9a-f]{24}", details["transactionId"]) is not None
            and _valid_boot_id(details.get("recoveryBootId"))
            and details.get("recoveryBootId") != values["restore"]["bootId"]
            and _valid_sha(details.get("promotionTranscriptSha256"))
        )
    elif phase == "trial-verify":
        promoted = values["recovery-promote"]
        valid = (
            set(details)
            == {
                "trialBootHealthy",
                "transactionId",
                "bootId",
                "applianceState",
                "userAgentState",
                "nasTree",
                "canaries",
                "appliance",
            }
            and details.get("trialBootHealthy") is True
            and details.get("transactionId") == promoted["transactionId"]
            and _valid_boot_id(details.get("bootId"))
            and details.get("bootId") != promoted["recoveryBootId"]
            and _valid_state(details.get("applianceState"), source["state"])
            and details.get("userAgentState")
            == {
                "snapshotId": backups["user"]["snapshotId"],
                "action": "restore-staged",
                "fullReadVerified": True,
            }
            and details.get("nasTree")
            == {
                "entries": values["restore"]["nasEntries"],
                "logicalBytes": values["restore"]["nasLogicalBytes"],
            }
            and details.get("canaries") == source["canaries"]
            and _valid_appliance(details.get("appliance"), architecture)
        )
    elif phase == "recovery-commit":
        promoted = values["recovery-promote"]
        valid = (
            set(details)
            == {
                "agentRestoreCommitted",
                "transactionId",
                "oldDataDeleted",
                "recoveryBootId",
                "commitTranscriptSha256",
            }
            and details.get("agentRestoreCommitted") is True
            and details.get("transactionId") == promoted["transactionId"]
            and details.get("oldDataDeleted") is True
            and _valid_boot_id(details.get("recoveryBootId"))
            and details.get("recoveryBootId") != values["trial-verify"]["bootId"]
            and _valid_sha(details.get("commitTranscriptSha256"))
        )
    else:
        committed = values["recovery-commit"]
        valid = (
            set(details)
            == {
                "coldBootHealthy",
                "dataVerified",
                "authenticationStateVerified",
                "auditStateVerified",
                "agentStateVerified",
                "nasDataVerified",
                "transactionId",
                "bootId",
                "applianceState",
                "userAgentState",
                "nasTree",
                "canaries",
                "appliance",
            }
            and all(
                details.get(name) is True
                for name in (
                    "coldBootHealthy",
                    "dataVerified",
                    "authenticationStateVerified",
                    "auditStateVerified",
                    "agentStateVerified",
                    "nasDataVerified",
                )
            )
            and details.get("transactionId") == committed["transactionId"]
            and _valid_boot_id(details.get("bootId"))
            and details.get("bootId") != committed["recoveryBootId"]
            and _valid_state(details.get("applianceState"), source["state"])
            and details.get("userAgentState")
            == {
                "snapshotId": backups["user"]["snapshotId"],
                "action": "restore-committed",
                "fullReadVerified": True,
            }
            and details.get("nasTree")
            == {
                "entries": values["restore"]["nasEntries"],
                "logicalBytes": values["restore"]["nasLogicalBytes"],
            }
            and details.get("canaries") == source["canaries"]
            and _valid_appliance(details.get("appliance"), architecture)
        )
    if not valid:
        raise BareMetalRecoveryLabError(f"bare-metal evidence details are invalid: {phase}")


def verify_evidence(plan_path: Path, trusted_uid: int = 0) -> dict[str, Any]:
    plan = _read_json(
        plan_path,
        "bare-metal recovery lab plan",
        mode=0o400,
        trusted_uid=trusted_uid,
    )
    _verify_plan(
        plan,
        "final-verify",
        plan.get("confirmations", {}).get("final-verify", ""),
    )
    candidate = operations_lab._candidate_identity(
        Path(plan["releaseCandidate"]["indexPath"]), trusted_uid=trusted_uid
    )
    if (
        candidate != plan["releaseCandidate"]
        or _bundle_identity(Path(plan["bundleRoot"]), candidate, trusted_uid=trusted_uid)
        != plan["operationsBundle"]
    ):
        raise BareMetalRecoveryLabError("bare-metal candidate or bundle drifted")
    root = Path(plan["evidenceDirectory"]).resolve(strict=True)
    records: dict[str, dict[str, Any]] = {}
    values: dict[str, dict[str, Any]] = {}
    for phase in PHASES:
        name = PHASE_OUTPUTS[phase]
        raw = systemd._safe_regular(
            root / name,
            f"bare-metal evidence {phase}",
            maximum=MAX_JSON_BYTES,
            trusted_uid=trusted_uid,
            private=False,
            exact_mode=0o444,
        )
        try:
            value = json.loads(
                raw.decode("utf-8"), object_pairs_hook=systemd._reject_duplicate_keys
            )
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise BareMetalRecoveryLabError("bare-metal evidence is not strict JSON") from exc
        if (
            not isinstance(value, dict)
            or set(value) != {"schemaVersion", "kind", "planId", "phase", "passed", "details"}
            or value.get("schemaVersion") != SCHEMA_VERSION
            or value.get("kind") != "echo.bare-metal-recovery-physical-lab-evidence"
            or value.get("planId") != plan["planId"]
            or value.get("phase") != phase
            or value.get("passed") is not True
            or not isinstance(value.get("details"), dict)
        ):
            raise BareMetalRecoveryLabError("bare-metal evidence sequence is invalid")
        records[phase] = {"name": name, "sha256": _sha256(raw), "size": len(raw)}
        values[phase] = value["details"]
        _validate_phase_details(plan, phase, value["details"], values)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "echo.bare-metal-recovery-physical-lab-verification",
        "planId": plan["planId"],
        "candidate": plan["releaseCandidate"],
        "checks": {check: True for check in CHECK_OUTPUTS},
        "phases": records,
        "allPassed": True,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan = subparsers.add_parser("plan")
    plan.add_argument("--candidate-index", type=Path, required=True)
    plan.add_argument("--bundle-root", type=Path, required=True)
    plan.add_argument("--install-bundle", type=Path, required=True)
    plan.add_argument("--target-disk", type=Path, required=True)
    plan.add_argument("--recovery-key", type=Path, required=True)
    plan.add_argument("--appliance-backup", type=Path, required=True)
    plan.add_argument("--nas-backup-receipt", type=Path, required=True)
    plan.add_argument("--user-snapshot", required=True)
    plan.add_argument("--nas-repository", type=Path, required=True)
    plan.add_argument("--nas-repository-mount", type=Path, required=True)
    plan.add_argument("--nas-snapshot", required=True)
    plan.add_argument("--deployment-root", type=Path, required=True)
    plan.add_argument("--agent-root", type=Path, default=Path("/var/lib/echo-agent"))
    plan.add_argument("--nas-root", type=Path, required=True)
    plan.add_argument("--state-canary", type=Path, required=True)
    plan.add_argument("--agent-canary", type=Path, required=True)
    plan.add_argument("--nas-canary", type=Path, required=True)
    plan.add_argument("--evidence-directory", type=Path, required=True)
    plan.add_argument("--base-url", default="http://127.0.0.1:8000")
    plan.add_argument("--main-container", default="echo-os")
    plan.add_argument("--proxy-container", default="echo-docker-control")
    plan.add_argument("--output", type=Path, required=True)
    run = subparsers.add_parser("run")
    run.add_argument("--plan", type=Path, required=True)
    run.add_argument("--phase", choices=RUN_PHASES, required=True)
    run.add_argument("--confirm", required=True)
    run.add_argument("--installer-confirm")
    verify = subparsers.add_parser("verify")
    verify.add_argument("--plan", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "plan":
            plan = build_plan(
                candidate_index=args.candidate_index,
                bundle_root=args.bundle_root,
                install_bundle=args.install_bundle,
                target_disk=args.target_disk,
                recovery_key=args.recovery_key,
                appliance_backup=args.appliance_backup,
                nas_backup_receipt=args.nas_backup_receipt,
                user_snapshot=args.user_snapshot,
                nas_repository=args.nas_repository,
                nas_repository_mount=args.nas_repository_mount,
                nas_snapshot=args.nas_snapshot,
                deployment_root=args.deployment_root,
                agent_root=args.agent_root,
                nas_root=args.nas_root,
                state_canary=args.state_canary,
                agent_canary=args.agent_canary,
                nas_canary=args.nas_canary,
                evidence_directory=args.evidence_directory,
                base_url=args.base_url,
                main_container=args.main_container,
                proxy_container=args.proxy_container,
                output=args.output,
            )
            print(
                "ECHO_BARE_METAL_RECOVERY_LAB_PLAN_READY "
                f"candidate={plan['releaseCandidate']['indexId']} plan={plan['planId']}"
            )
            print(f"source-backup: {PHASE_OUTPUTS['source-backup']} (created)")
            for phase in RUN_PHASES:
                print(f"{phase}: {plan['confirmations'][phase]}")
            return 0
        if args.command == "run":
            report = run_phase(
                plan_path=args.plan,
                phase=args.phase,
                confirmation=args.confirm,
                installer_confirmation=args.installer_confirm,
            )
            print(
                "ECHO_BARE_METAL_RECOVERY_LAB_PHASE_OK "
                f"phase={report['phase']} plan={report['planId']} output={report['output']}"
            )
            return 0
        report = verify_evidence(plan_path=args.plan)
    except (
        BareMetalRecoveryLabError,
        KeyError,
        OSError,
        TypeError,
        UnicodeError,
        ValueError,
        subprocess.SubprocessError,
        device_lab.DeviceEnduranceLabError,
        nas_data_backup.NasDataBackupError,
        operations_lab.OperationsSystemdLabError,
        systemd.OperationsSystemdError,
    ) as exc:
        print(f"Echo bare-metal recovery physical lab failed: {exc}", file=sys.stderr)
        return 1
    print(
        "ECHO_BARE_METAL_RECOVERY_LAB_VERIFIED "
        f"candidate={report['candidate']['indexId']} plan={report['planId']} phases={len(report['phases'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
