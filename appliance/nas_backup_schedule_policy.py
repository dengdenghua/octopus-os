"""Approval-bound configuration for verified native NAS data backups."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import tempfile
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from deploy.appliance import nas_data_backup
from deploy.appliance import nas_data_backup_schedule_runner as schedule_runner

DESIRED_SCHEMA = schedule_runner.CONFIG_SCHEMA
PLAN_SCHEMA = "echo.nas-data-backup-schedule-plan.v1"
CONFIG_PATH = schedule_runner.CONFIG_PATH
HISTORY_PATH = schedule_runner.STATUS_PATH
SERVICE_PATH = Path("/etc/systemd/system/echo-nas-data-backup.service")
TIMER_PATH = Path("/etc/systemd/system/echo-nas-data-backup.timer")
CREDENTIAL_PATH = Path("/etc/credstore.encrypted/echo-nas-backup-password")
SYSTEMCTL = Path("/usr/bin/systemctl")
MAX_CREDENTIAL_BYTES = 64 * 1024
MAX_UNIT_BYTES = 64 * 1024
SCHEDULE = "daily after 03:30 local time, randomized within 30 minutes"
_LOCK = threading.RLock()


class NasBackupSchedulePolicyError(ValueError):
    """The requested NAS backup schedule state is invalid."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _desired(value: Mapping[str, Any]) -> dict[str, Any]:
    if value.get("enabled") is False and (
        value.get("repository") is not None or value.get("repositoryMount") is not None
    ):
        raise NasBackupSchedulePolicyError(
            "disabled NAS backup schedule must clear repository paths"
        )
    try:
        return schedule_runner.validate_config(value)
    except schedule_runner.NasDataBackupScheduleError as exc:
        raise NasBackupSchedulePolicyError(str(exc)) from exc


def _safe_file_identity(
    path: Path,
    *,
    trusted_uid: int,
    maximum: int,
    exact_mode: int | None = None,
) -> dict[str, Any] | None:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    elif path.is_symlink():
        raise OSError("NAS backup runtime file is unsafe")
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise OSError("NAS backup runtime file is unavailable") from exc
    try:
        before = os.fstat(descriptor)
        mode = stat.S_IMODE(before.st_mode)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != trusted_uid
            or not 1 <= before.st_size <= maximum
            or (os.name == "posix" and exact_mode is not None and mode != exact_mode)
            or (os.name == "posix" and exact_mode is None and mode & 0o022)
        ):
            raise OSError("NAS backup runtime file is unsafe")
        raw = bytearray()
        while len(raw) <= maximum:
            chunk = os.read(descriptor, min(8192, maximum + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        payload = bytes(raw)
        after = os.fstat(descriptor)
        if len(payload) != before.st_size or (
            before.st_ino,
            before.st_dev,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_ino,
            after.st_dev,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise OSError("NAS backup runtime file changed while reading")
    finally:
        os.close(descriptor)
    return {
        "sha256": hashlib.sha256(payload).hexdigest(),
        "device": before.st_dev,
        "inode": before.st_ino,
        "size": before.st_size,
        "mtimeNs": before.st_mtime_ns,
    }


def scheduler_installed(
    *,
    service_path: Path = SERVICE_PATH,
    timer_path: Path = TIMER_PATH,
    trusted_uid: int = 0,
) -> bool:
    try:
        return all(
            _safe_file_identity(path, trusted_uid=trusted_uid, maximum=MAX_UNIT_BYTES) is not None
            for path in (service_path, timer_path)
        )
    except OSError:
        return False


def credential_configured(path: Path = CREDENTIAL_PATH, *, trusted_uid: int = 0) -> bool:
    try:
        return (
            _safe_file_identity(
                path,
                trusted_uid=trusted_uid,
                maximum=MAX_CREDENTIAL_BYTES,
                exact_mode=0o600,
            )
            is not None
        )
    except OSError:
        return False


def timer_enabled(
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    systemctl: Path = SYSTEMCTL,
) -> bool:
    if not systemctl.is_file() or systemctl.is_symlink():
        return False
    try:
        completed = runner(
            [str(systemctl), "is-enabled", "--quiet", "echo-nas-data-backup.timer"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
            env={**os.environ, "LC_ALL": "C", "LANG": "C"},
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def policy_status(
    *,
    config_path: Path = CONFIG_PATH,
    history_path: Path = HISTORY_PATH,
    service_path: Path = SERVICE_PATH,
    timer_path: Path = TIMER_PATH,
    credential_path: Path = CREDENTIAL_PATH,
    trusted_uid: int = 0,
    timer_enabled_reader: Callable[[], bool] = timer_enabled,
) -> dict[str, Any]:
    configured, config = schedule_runner.read_config(config_path, owner=trusted_uid)
    history = schedule_runner.read_history(history_path, owner=trusted_uid)
    return {
        "schemaVersion": 1,
        "configured": configured,
        "enabled": config["enabled"],
        "repositoryConfigured": bool(config["repository"] and config["repositoryMount"]),
        "credentialConfigured": credential_configured(credential_path, trusted_uid=trusted_uid),
        "schedulerInstalled": scheduler_installed(
            service_path=service_path,
            timer_path=timer_path,
            trusted_uid=trusted_uid,
        ),
        "timerEnabled": timer_enabled_reader(),
        "schedule": SCHEDULE,
        "history": history["attempts"],
        "pathsRedacted": True,
        "source": "native",
    }


def _repository_binding(config: Mapping[str, Any]) -> dict[str, Any]:
    repository, _nas = nas_data_backup._context(
        repository=Path(str(config["repository"])),
        repository_mount=Path(str(config["repositoryMount"])),
        deployment_root=schedule_runner.DEPLOYMENT_ROOT,
        appliance_env=schedule_runner.APPLIANCE_ENV,
        state_root_override=schedule_runner.STATE_ROOT,
        nas_root_override=schedule_runner.NAS_ROOT,
    )
    mount = nas_data_backup._mount_record(repository)
    metadata = repository.stat()
    return {
        "filesystem": mount["filesystem"],
        "sourceSha256": hashlib.sha256(mount["source"].encode("utf-8")).hexdigest(),
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
    }


def _plan_context(
    desired_state: Mapping[str, Any],
    *,
    config_path: Path,
    service_path: Path,
    timer_path: Path,
    credential_path: Path,
    trusted_uid: int,
    timer_enabled_reader: Callable[[], bool],
    repository_binding_reader: Callable[[Mapping[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    desired = _desired(desired_state)
    configured, current = schedule_runner.read_config(config_path, owner=trusted_uid)
    enabled = timer_enabled_reader()
    try:
        unit_identities: dict[str, Any] = {
            path.name: _safe_file_identity(path, trusted_uid=trusted_uid, maximum=MAX_UNIT_BYTES)
            for path in (service_path, timer_path)
        }
    except OSError:
        if desired["enabled"]:
            raise
        unit_identities = {service_path.name: "unsafe", timer_path.name: "unsafe"}
    installed = all(identity is not None for identity in unit_identities.values())
    try:
        credential_identity: Any = _safe_file_identity(
            credential_path,
            trusted_uid=trusted_uid,
            maximum=MAX_CREDENTIAL_BYTES,
            exact_mode=0o600,
        )
    except OSError:
        if desired["enabled"]:
            raise
        credential_identity = "unsafe"
    repository_binding = None
    if desired["enabled"]:
        if not installed:
            raise OSError("NAS backup scheduler is not installed")
        if credential_identity is None:
            raise OSError("NAS backup encrypted credential is not configured")
        if not nas_data_backup.RESTIC.is_file() or nas_data_backup.RESTIC.is_symlink():
            raise OSError("NAS backup runtime is unavailable")
        repository_binding = repository_binding_reader(desired)
    operation = (
        "none"
        if desired == current and enabled == desired["enabled"]
        else "enable"
        if desired["enabled"] and not current["enabled"]
        else "update"
        if desired["enabled"]
        else "disable"
    )
    binding = {
        "schema": PLAN_SCHEMA,
        "configured": configured,
        "current": current,
        "desired": desired,
        "timerEnabled": enabled,
        "unitIdentities": unit_identities,
        "credentialIdentity": credential_identity,
        "repositoryBinding": repository_binding,
        "operation": operation,
    }
    return {**binding, "planId": hashlib.sha256(_canonical(binding)).hexdigest()}


def _public_plan(context: Mapping[str, Any]) -> dict[str, Any]:
    desired = context["desired"]
    current = context["current"]
    return {
        "schema": PLAN_SCHEMA,
        "planId": context["planId"],
        "operation": context["operation"],
        "requiresApproval": context["operation"] != "none",
        "current": {
            "enabled": current["enabled"],
            "repositoryConfigured": bool(current["repository"]),
            "timerEnabled": context["timerEnabled"],
        },
        "desired": {
            "enabled": desired["enabled"],
            "repositoryConfigured": bool(desired["repository"]),
        },
        "schedule": SCHEDULE,
        "pathsRedacted": True,
        "safety": {
            "encryptedCredentialRequired": True,
            "externalMountedRepositoryRequired": True,
            "managedReadOnlyBtrfsSnapshotsOnly": True,
            "fullRepositoryReadAfterBackup": True,
        },
    }


def plan_policy(
    desired_state: Mapping[str, Any],
    *,
    config_path: Path = CONFIG_PATH,
    service_path: Path = SERVICE_PATH,
    timer_path: Path = TIMER_PATH,
    credential_path: Path = CREDENTIAL_PATH,
    trusted_uid: int = 0,
    timer_enabled_reader: Callable[[], bool] = timer_enabled,
    repository_binding_reader: Callable[[Mapping[str, Any]], dict[str, Any]] = (
        _repository_binding
    ),
) -> dict[str, Any]:
    return _public_plan(
        _plan_context(
            desired_state,
            config_path=config_path,
            service_path=service_path,
            timer_path=timer_path,
            credential_path=credential_path,
            trusted_uid=trusted_uid,
            timer_enabled_reader=timer_enabled_reader,
            repository_binding_reader=repository_binding_reader,
        )
    )


def _assert_parent(path: Path, *, trusted_uid: int) -> None:
    metadata = path.parent.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != trusted_uid
        or (os.name == "posix" and metadata.st_mode & 0o022)
    ):
        raise OSError("NAS backup schedule directory is unsafe")


def _atomic_write(path: Path, payload: bytes, *, uid: int, gid: int) -> None:
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        if hasattr(os, "fchown"):
            os.fchown(descriptor, uid, gid)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name == "posix":
            directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _systemctl_action(
    action: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    systemctl: Path = SYSTEMCTL,
) -> None:
    arguments = {
        "enable": ("enable", "--now", "echo-nas-data-backup.timer"),
        "disable": ("disable", "--now", "echo-nas-data-backup.timer"),
    }.get(action)
    if arguments is None:
        raise OSError("NAS backup timer action is invalid")
    if not systemctl.is_file() or systemctl.is_symlink():
        raise OSError("systemctl is unavailable")
    try:
        completed = runner(
            [str(systemctl), *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
            env={**os.environ, "LC_ALL": "C", "LANG": "C"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise OSError("NAS backup timer action could not be completed") from exc
    if completed.returncode != 0:
        raise OSError("NAS backup timer action failed")


def apply_policy(
    desired_state: Mapping[str, Any],
    plan_id: str,
    *,
    config_path: Path = CONFIG_PATH,
    service_path: Path = SERVICE_PATH,
    timer_path: Path = TIMER_PATH,
    credential_path: Path = CREDENTIAL_PATH,
    trusted_uid: int = 0,
    trusted_gid: int = 0,
    timer_enabled_reader: Callable[[], bool] = timer_enabled,
    timer_action: Callable[[str], None] = _systemctl_action,
    repository_binding_reader: Callable[[Mapping[str, Any]], dict[str, Any]] = (
        _repository_binding
    ),
) -> dict[str, Any]:
    if os.name == "posix" and os.geteuid() != trusted_uid:
        raise OSError("NAS backup schedule update requires root")
    with _LOCK:
        context = _plan_context(
            desired_state,
            config_path=config_path,
            service_path=service_path,
            timer_path=timer_path,
            credential_path=credential_path,
            trusted_uid=trusted_uid,
            timer_enabled_reader=timer_enabled_reader,
            repository_binding_reader=repository_binding_reader,
        )
        if context["planId"] != plan_id:
            raise NasBackupSchedulePolicyError("NAS backup schedule plan is stale; preview again")
        plan = _public_plan(context)
        if context["operation"] == "none":
            return {**plan, "applied": False, "verified": True}
        _assert_parent(config_path, trusted_uid=trusted_uid)
        existed = config_path.exists() and not config_path.is_symlink()
        previous = config_path.read_bytes() if existed else None
        previously_enabled = context["timerEnabled"]
        payload = _canonical(context["desired"]) + b"\n"
        try:
            _atomic_write(config_path, payload, uid=trusted_uid, gid=trusted_gid)
            timer_action("enable" if context["desired"]["enabled"] else "disable")
            _, verified = schedule_runner.read_config(config_path, owner=trusted_uid)
            if (
                verified != context["desired"]
                or timer_enabled_reader() != context["desired"]["enabled"]
            ):
                raise OSError("NAS backup schedule verification failed")
        except OSError as exc:
            rollback_errors: list[str] = []
            try:
                if existed and previous is not None:
                    _atomic_write(config_path, previous, uid=trusted_uid, gid=trusted_gid)
                elif config_path.exists() and not config_path.is_symlink():
                    config_path.unlink()
            except OSError:
                rollback_errors.append("configuration")
            try:
                timer_action("enable" if previously_enabled else "disable")
            except OSError:
                rollback_errors.append("timer")
            if rollback_errors:
                raise OSError(
                    "NAS backup schedule update failed and rollback was incomplete"
                ) from exc
            raise OSError("NAS backup schedule update failed and was rolled back") from exc
        return {**plan, "applied": True, "verified": True}


__all__ = [
    "CONFIG_PATH",
    "CREDENTIAL_PATH",
    "DESIRED_SCHEMA",
    "HISTORY_PATH",
    "NasBackupSchedulePolicyError",
    "PLAN_SCHEMA",
    "SERVICE_PATH",
    "SCHEDULE",
    "TIMER_PATH",
    "apply_policy",
    "credential_configured",
    "plan_policy",
    "policy_status",
    "scheduler_installed",
    "timer_enabled",
]
