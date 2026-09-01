#!/usr/bin/env python3
"""Plan and transactionally install Echo backup/audit systemd units."""

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
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from deploy.appliance.external_storage import ExternalStorageError, verify_external_storage
except ModuleNotFoundError:
    from external_storage import ExternalStorageError, verify_external_storage

SCHEMA_VERSION = 2
MAX_PLAN_BYTES = 1024 * 1024
MAX_CREDENTIAL_BYTES = 1024 * 1024
RECOVERY_SERVICE_NAME = "echo-appliance-upgrade-recovery.service"
UNIT_NAMES = (
    "echo-state-backup.service",
    "echo-state-backup.timer",
    "echo-audit-evidence.service",
    "echo-audit-evidence.timer",
    RECOVERY_SERVICE_NAME,
)
TIMER_NAMES = ("echo-state-backup.timer", "echo-audit-evidence.timer")
ENABLED_UNIT_NAMES = (RECOVERY_SERVICE_NAME, *TIMER_NAMES)
SAFE_PATH = re.compile(r"/[A-Za-z0-9._/-]+")


class OperationsSystemdError(RuntimeError):
    """The operations units cannot be configured safely."""


@dataclass(frozen=True)
class OperationsConfig:
    bundle_root: Path
    backup_directory: Path
    backup_mountpoint: Path
    audit_directory: Path
    audit_mountpoint: Path
    backup_credential: Path
    audit_credential: Path
    backup_keep: int = 7
    audit_keep_days: int = 365
    audit_keep_minimum: int = 12


@dataclass(frozen=True)
class SystemLayout:
    unit_directory: Path = Path("/etc/systemd/system")


@dataclass(frozen=True)
class SystemTools:
    systemctl: Path = Path("/usr/bin/systemctl")
    systemd_analyze: Path = Path("/usr/bin/systemd-analyze")


DEFAULT_SYSTEM_LAYOUT = SystemLayout()
DEFAULT_SYSTEM_TOOLS = SystemTools()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _safe_path(path: Path, label: str, *, directory: bool) -> Path:
    text = str(path)
    if (
        not path.is_absolute()
        or SAFE_PATH.fullmatch(text) is None
        or "//" in text
        or any(part in {".", ".."} for part in path.parts)
        or "%" in text
    ):
        raise OperationsSystemdError(f"{label} must be one safe absolute path")
    cursor = Path(path.anchor)
    for part in path.parts[1:]:
        cursor /= part
        if cursor.is_symlink():
            raise OperationsSystemdError(f"{label} must not contain a symbolic link")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise OperationsSystemdError(f"{label} is unavailable") from exc
    if directory != resolved.is_dir() or (not directory and not resolved.is_file()):
        raise OperationsSystemdError(f"{label} has the wrong file type")
    return resolved


def _safe_regular(
    path: Path,
    label: str,
    *,
    maximum: int,
    trusted_uid: int,
    private: bool = True,
    exact_mode: int | None = None,
    allow_empty: bool = False,
) -> bytes:
    path = _safe_path(path, label, directory=False)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise OperationsSystemdError(f"{label} cannot be opened safely") from exc
    try:
        before = os.fstat(descriptor)
        mode = stat.S_IMODE(before.st_mode)
        if (
            not stat.S_ISREG(before.st_mode)
            or not (0 if allow_empty else 1) <= before.st_size <= maximum
            or before.st_uid != trusted_uid
            or (private and mode & 0o077)
            or (exact_mode is not None and mode != exact_mode)
        ):
            raise OperationsSystemdError(f"{label} has unsafe ownership, mode, or size")
        payload = bytearray()
        while len(payload) <= maximum:
            chunk = os.read(descriptor, min(1024 * 1024, maximum - len(payload) + 1))
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
        if len(payload) > maximum or (
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
            raise OperationsSystemdError(f"{label} changed while it was read")
        return bytes(payload)
    finally:
        os.close(descriptor)


def _read_existing_unit(path: Path, name: str, *, trusted_uid: int) -> tuple[bytes, int]:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise OperationsSystemdError(f"existing systemd unit is unsafe: {name}") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != trusted_uid
            or not 0 <= before.st_size <= MAX_PLAN_BYTES
        ):
            raise OperationsSystemdError(f"existing systemd unit is unsafe: {name}")
        payload = bytearray()
        while len(payload) <= MAX_PLAN_BYTES:
            chunk = os.read(
                descriptor,
                min(1024 * 1024, MAX_PLAN_BYTES - len(payload) + 1),
            )
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
        if len(payload) > MAX_PLAN_BYTES or (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise OperationsSystemdError(f"existing systemd unit changed while read: {name}")
        return bytes(payload), stat.S_IMODE(before.st_mode)
    finally:
        os.close(descriptor)


def _assert_bundle_file(root: Path, name: str, *, mode: int, trusted_uid: int) -> None:
    path = root / name
    _safe_regular(
        path,
        f"bundle file {name}",
        maximum=16 * 1024 * 1024,
        trusted_uid=trusted_uid,
        private=False,
        exact_mode=mode,
    )


def _assert_owned_directory(path: Path, label: str, *, trusted_uid: int) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise OperationsSystemdError(f"{label} cannot be opened safely") from exc
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != trusted_uid
            or stat.S_IMODE(info.st_mode) & 0o022
        ):
            raise OperationsSystemdError(f"{label} has unsafe ownership or permissions")
    finally:
        os.close(descriptor)


def _validate_config(config: OperationsConfig) -> OperationsConfig:
    for name, value, lower, upper in (
        ("backup_keep", config.backup_keep, 2, 10000),
        ("audit_keep_days", config.audit_keep_days, 30, 3650),
        ("audit_keep_minimum", config.audit_keep_minimum, 2, 1000),
    ):
        if not isinstance(value, int) or isinstance(value, bool) or not lower <= value <= upper:
            raise OperationsSystemdError(f"{name} is outside its safe range")
    return OperationsConfig(
        bundle_root=_safe_path(config.bundle_root, "operations bundle root", directory=True),
        backup_directory=_safe_path(config.backup_directory, "backup directory", directory=True),
        backup_mountpoint=_safe_path(config.backup_mountpoint, "backup mountpoint", directory=True),
        audit_directory=_safe_path(config.audit_directory, "audit directory", directory=True),
        audit_mountpoint=_safe_path(config.audit_mountpoint, "audit mountpoint", directory=True),
        backup_credential=_safe_path(
            config.backup_credential, "backup encrypted credential", directory=False
        ),
        audit_credential=_safe_path(
            config.audit_credential, "audit encrypted credential", directory=False
        ),
        backup_keep=config.backup_keep,
        audit_keep_days=config.audit_keep_days,
        audit_keep_minimum=config.audit_keep_minimum,
    )


def _backup_service(config: OperationsConfig) -> bytes:
    return f"""[Unit]
Description=Verified offline Echo appliance-state backup
Requires=docker.service
After=docker.service network-online.target local-fs.target remote-fs.target
RequiresMountsFor={config.backup_mountpoint}

[Service]
Type=oneshot
WorkingDirectory={config.bundle_root}
Environment=ECHO_BACKUP_DIR={config.backup_directory}
Environment=ECHO_BACKUP_MOUNTPOINT={config.backup_mountpoint}
Environment=ECHO_BACKUP_KEEP={config.backup_keep}
LoadCredentialEncrypted=echo-backup-passphrase:{config.backup_credential}
ExecStart={config.bundle_root}/backup-state.sh
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths={config.backup_directory}
ReadWritePaths=/run/lock
Nice=10
IOSchedulingClass=best-effort
IOSchedulingPriority=7

[Install]
WantedBy=multi-user.target
""".encode()


def _audit_service(config: OperationsConfig) -> bytes:
    return f"""[Unit]
Description=Encrypted external Echo appliance audit evidence export
Requires=docker.service
After=docker.service network-online.target local-fs.target remote-fs.target
RequiresMountsFor={config.audit_mountpoint}

[Service]
Type=oneshot
WorkingDirectory={config.bundle_root}
Environment=ECHO_AUDIT_EXPORT_DIR={config.audit_directory}
Environment=ECHO_AUDIT_EXPORT_MOUNTPOINT={config.audit_mountpoint}
Environment=ECHO_AUDIT_KEEP_DAYS={config.audit_keep_days}
Environment=ECHO_AUDIT_KEEP_MINIMUM={config.audit_keep_minimum}
LoadCredentialEncrypted=echo-audit-export-passphrase:{config.audit_credential}
ExecStart={config.bundle_root}/export-audit-evidence.sh
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths={config.audit_directory}
ReadWritePaths=/run/lock
Nice=10
IOSchedulingClass=best-effort
IOSchedulingPriority=7

[Install]
WantedBy=multi-user.target
""".encode()


def _backup_timer() -> bytes:
    return b"""[Unit]
Description=Daily Echo appliance-state backup

[Timer]
OnCalendar=*-*-* 03:30:00
RandomizedDelaySec=30m
Persistent=true
Unit=echo-state-backup.service

[Install]
WantedBy=timers.target
"""


def _audit_timer() -> bytes:
    return b"""[Unit]
Description=Daily external Echo appliance audit evidence export

[Timer]
OnCalendar=*-*-* 04:15:00
RandomizedDelaySec=30m
Persistent=true
Unit=echo-audit-evidence.service

[Install]
WantedBy=timers.target
"""


def _upgrade_recovery_service(config: OperationsConfig) -> bytes:
    return f"""[Unit]
Description=Recover an interrupted Echo appliance image upgrade
Requires=docker.service
After=docker.service local-fs.target
Before=echo-state-backup.service echo-audit-evidence.service
ConditionPathExists={config.bundle_root}/.echo-upgrade-transaction.json

[Service]
Type=oneshot
WorkingDirectory={config.bundle_root}
ExecStart={config.bundle_root}/recover-appliance-upgrade.sh
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths={config.bundle_root}
ReadWritePaths=/run/lock

[Install]
WantedBy=multi-user.target
""".encode()


def _units(config: OperationsConfig) -> dict[str, bytes]:
    return {
        "echo-state-backup.service": _backup_service(config),
        "echo-state-backup.timer": _backup_timer(),
        "echo-audit-evidence.service": _audit_service(config),
        "echo-audit-evidence.timer": _audit_timer(),
        RECOVERY_SERVICE_NAME: _upgrade_recovery_service(config),
    }


def _config_payload(config: OperationsConfig) -> dict[str, Any]:
    return {
        "bundleRoot": str(config.bundle_root),
        "backupDirectory": str(config.backup_directory),
        "backupMountpoint": str(config.backup_mountpoint),
        "auditDirectory": str(config.audit_directory),
        "auditMountpoint": str(config.audit_mountpoint),
        "backupCredential": str(config.backup_credential),
        "auditCredential": str(config.audit_credential),
        "backupKeep": config.backup_keep,
        "auditKeepDays": config.audit_keep_days,
        "auditKeepMinimum": config.audit_keep_minimum,
    }


def _validated_host_tools(tools: SystemTools, *, trusted_uid: int) -> dict[str, dict[str, str]]:
    host_tools: dict[str, dict[str, str]] = {}
    for tool in (tools.systemctl, tools.systemd_analyze):
        payload = _safe_regular(
            tool,
            f"required host tool {tool.name}",
            maximum=64 * 1024 * 1024,
            trusted_uid=trusted_uid,
            private=False,
            exact_mode=0o755,
        )
        host_tools[tool.name] = {"path": str(tool), "sha256": _sha256(payload)}
    return host_tools


class OperationsSystemdInstaller:
    def __init__(
        self,
        config: OperationsConfig,
        *,
        layout: SystemLayout = DEFAULT_SYSTEM_LAYOUT,
        tools: SystemTools = DEFAULT_SYSTEM_TOOLS,
        command_runner: Callable[[list[str]], subprocess.CompletedProcess[str]] | None = None,
        storage_verifier: Callable[..., Mapping[str, str]] = verify_external_storage,
        effective_uid: int | None = None,
        trusted_uid: int = 0,
        system_name: str | None = None,
    ) -> None:
        self.config = _validate_config(config)
        self.layout = layout
        self.tools = tools
        self.command_runner = command_runner or self._run
        self.storage_verifier = storage_verifier
        self.effective_uid = os.geteuid() if effective_uid is None else effective_uid
        self.trusted_uid = trusted_uid
        self.system_name = os.uname().sysname if system_name is None else system_name

    @staticmethod
    def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # nosec B603
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def _preflight(self) -> dict[str, Any]:
        if self.system_name != "Linux":
            raise OperationsSystemdError("operations systemd installation requires Linux")
        for directory, label in (
            (self.config.bundle_root, "operations bundle root"),
            (self.config.backup_mountpoint, "backup mountpoint"),
            (self.config.backup_directory, "backup directory"),
            (self.config.audit_mountpoint, "audit mountpoint"),
            (self.config.audit_directory, "audit directory"),
            (self.config.backup_credential.parent, "backup credential directory"),
            (self.config.audit_credential.parent, "audit credential directory"),
        ):
            _assert_owned_directory(directory, label, trusted_uid=self.trusted_uid)
        host_tools = _validated_host_tools(self.tools, trusted_uid=self.trusted_uid)
        for name, mode in (
            ("backup-state.sh", 0o755),
            ("export-audit-evidence.sh", 0o755),
            ("external_storage.py", 0o755),
            ("recover-appliance-upgrade.sh", 0o755),
            ("upgrade_transaction.py", 0o755),
            ("docker-compose.yml", 0o644),
            ("echo-release.env", 0o600),
        ):
            _assert_bundle_file(
                self.config.bundle_root,
                name,
                mode=mode,
                trusted_uid=self.trusted_uid,
            )
        backup_credential = _safe_regular(
            self.config.backup_credential,
            "backup encrypted credential",
            maximum=MAX_CREDENTIAL_BYTES,
            trusted_uid=self.trusted_uid,
        )
        audit_credential = _safe_regular(
            self.config.audit_credential,
            "audit encrypted credential",
            maximum=MAX_CREDENTIAL_BYTES,
            trusted_uid=self.trusted_uid,
        )
        appliance_env = self.config.bundle_root / "appliance.env"
        try:
            backup_storage = self.storage_verifier(
                destination=self.config.backup_directory,
                mountpoint=self.config.backup_mountpoint,
                deployment_root=self.config.bundle_root,
                appliance_env=appliance_env if appliance_env.exists() else None,
            )
            audit_storage = self.storage_verifier(
                destination=self.config.audit_directory,
                mountpoint=self.config.audit_mountpoint,
                deployment_root=self.config.bundle_root,
                appliance_env=appliance_env if appliance_env.exists() else None,
            )
        except ExternalStorageError as exc:
            raise OperationsSystemdError(f"external operations storage is unsafe: {exc}") from exc
        return {
            "hostTools": host_tools,
            "credentials": {
                "backup": {"sha256": _sha256(backup_credential)},
                "audit": {"sha256": _sha256(audit_credential)},
            },
            "storage": {
                "backup": dict(backup_storage),
                "audit": dict(audit_storage),
            },
        }

    def plan(self) -> dict[str, Any]:
        preflight = self._preflight()
        units = _units(self.config)
        payload: dict[str, Any] = {
            "schemaVersion": SCHEMA_VERSION,
            "kind": "echo.operations-systemd-install-plan",
            "config": _config_payload(self.config),
            "units": {
                name: {"sha256": _sha256(units[name]), "mode": "0644"} for name in UNIT_NAMES
            },
            "timers": list(TIMER_NAMES),
            "recoveryService": RECOVERY_SERVICE_NAME,
            **preflight,
        }
        payload["planId"] = _sha256(_canonical_json(payload))
        payload["installConfirmation"] = f"INSTALL ECHO OPERATIONS {payload['planId']}"
        return payload

    def _verify_units(self, units: Mapping[str, bytes]) -> None:
        with tempfile.TemporaryDirectory(prefix="echo-operations-systemd-") as temporary:
            root = Path(temporary)
            paths = []
            for name in UNIT_NAMES:
                path = root / name
                path.write_bytes(units[name])
                path.chmod(0o644)
                paths.append(str(path))
            try:
                completed = self.command_runner([str(self.tools.systemd_analyze), "verify", *paths])
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise OperationsSystemdError("systemd unit verification could not run") from exc
        if completed.returncode != 0:
            raise OperationsSystemdError("systemd rejected the generated operations units")

    @staticmethod
    def _atomic_write(path: Path, payload: bytes, mode: int = 0o644) -> None:
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary_path = Path(temporary)
        try:
            os.fchmod(descriptor, mode)
            with os.fdopen(descriptor, "wb", closefd=True) as output:
                descriptor = -1
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary_path, path)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary_path.exists():
                temporary_path.unlink()

    def _systemctl(self, *arguments: str, acceptable: set[int] | None = None) -> bool:
        try:
            completed = self.command_runner([str(self.tools.systemctl), *arguments])
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise OperationsSystemdError(f"systemctl {' '.join(arguments)} could not run") from exc
        allowed = {0} if acceptable is None else acceptable
        if completed.returncode not in allowed:
            raise OperationsSystemdError(f"systemctl {' '.join(arguments)} failed")
        return completed.returncode == 0

    def install(self, plan: Mapping[str, Any], confirmation: str) -> dict[str, Any]:
        expected = self.plan()
        if dict(plan) != expected:
            raise OperationsSystemdError("operations systemd plan no longer matches the host")
        if confirmation != expected["installConfirmation"]:
            raise OperationsSystemdError("operations systemd confirmation is invalid")
        if self.effective_uid != 0:
            raise OperationsSystemdError("operations systemd installation requires root")
        unit_directory = _safe_path(
            self.layout.unit_directory, "systemd unit directory", directory=True
        )
        _assert_owned_directory(
            unit_directory, "systemd unit directory", trusted_uid=self.trusted_uid
        )
        units = _units(self.config)
        self._verify_units(units)

        previous_files: dict[str, tuple[bytes, int] | None] = {}
        previous_enabled_states: dict[str, tuple[bool, bool]] = {}
        for name in UNIT_NAMES:
            path = unit_directory / name
            if path.exists() or path.is_symlink():
                if path.is_symlink() or not path.is_file():
                    raise OperationsSystemdError(f"existing systemd unit is unsafe: {name}")
                previous_files[name] = _read_existing_unit(path, name, trusted_uid=self.trusted_uid)
            else:
                previous_files[name] = None
        for name in ENABLED_UNIT_NAMES:
            enabled = self._systemctl("is-enabled", "--quiet", name, acceptable={0, 1, 4})
            active = self._systemctl("is-active", "--quiet", name, acceptable={0, 3, 4})
            previous_enabled_states[name] = (enabled, active)

        try:
            for name in UNIT_NAMES:
                self._atomic_write(unit_directory / name, units[name])
            self._systemctl("daemon-reload")
            for name in ENABLED_UNIT_NAMES:
                self._systemctl("enable", "--now", name)
        except (OSError, OperationsSystemdError) as exc:
            try:
                for name in UNIT_NAMES:
                    path = unit_directory / name
                    previous = previous_files[name]
                    if previous is None:
                        if path.exists() and not path.is_symlink() and path.is_file():
                            path.unlink()
                    else:
                        self._atomic_write(path, previous[0], previous[1])
                self._systemctl("daemon-reload")
                for name, (enabled, active) in previous_enabled_states.items():
                    if enabled:
                        self._systemctl("enable", name, acceptable={0, 1})
                    else:
                        self._systemctl("disable", name, acceptable={0, 1, 5})
                    if active:
                        self._systemctl("start", name, acceptable={0, 5})
                    else:
                        self._systemctl("stop", name, acceptable={0, 5})
            except (OSError, OperationsSystemdError) as rollback_exc:
                raise OperationsSystemdError(
                    "operations systemd installation failed and rollback was incomplete"
                ) from rollback_exc
            raise OperationsSystemdError(
                "operations systemd installation failed and previous units were restored"
            ) from exc

        return {
            "schemaVersion": SCHEMA_VERSION,
            "kind": "echo.operations-systemd-installation",
            "planId": expected["planId"],
            "units": {name: _sha256(units[name]) for name in UNIT_NAMES},
            "timersEnabled": list(TIMER_NAMES),
            "recoveryServiceEnabled": RECOVERY_SERVICE_NAME,
            "installed": True,
        }


class OperationsSystemdRemover:
    def __init__(
        self,
        *,
        layout: SystemLayout = DEFAULT_SYSTEM_LAYOUT,
        tools: SystemTools = DEFAULT_SYSTEM_TOOLS,
        command_runner: Callable[[list[str]], subprocess.CompletedProcess[str]] | None = None,
        effective_uid: int | None = None,
        trusted_uid: int = 0,
        system_name: str | None = None,
    ) -> None:
        self.layout = layout
        self.tools = tools
        self.command_runner = command_runner or OperationsSystemdInstaller._run
        self.effective_uid = os.geteuid() if effective_uid is None else effective_uid
        self.trusted_uid = trusted_uid
        self.system_name = os.uname().sysname if system_name is None else system_name

    def _systemctl(self, *arguments: str, acceptable: set[int] | None = None) -> bool:
        try:
            completed = self.command_runner([str(self.tools.systemctl), *arguments])
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise OperationsSystemdError(f"systemctl {' '.join(arguments)} could not run") from exc
        allowed = {0} if acceptable is None else acceptable
        if completed.returncode not in allowed:
            raise OperationsSystemdError(f"systemctl {' '.join(arguments)} failed")
        return completed.returncode == 0

    def _snapshot(
        self,
    ) -> tuple[Path, dict[str, tuple[bytes, int]], dict[str, tuple[bool, bool]]]:
        if self.system_name != "Linux":
            raise OperationsSystemdError("operations systemd removal requires Linux")
        unit_directory = _safe_path(
            self.layout.unit_directory, "systemd unit directory", directory=True
        )
        _assert_owned_directory(
            unit_directory,
            "systemd unit directory",
            trusted_uid=self.trusted_uid,
        )
        files: dict[str, tuple[bytes, int]] = {}
        for name in UNIT_NAMES:
            path = unit_directory / name
            if path.is_symlink() or not path.is_file():
                raise OperationsSystemdError(
                    f"managed operations systemd unit is missing or unsafe: {name}"
                )
            files[name] = _read_existing_unit(path, name, trusted_uid=self.trusted_uid)
        states: dict[str, tuple[bool, bool]] = {}
        for name in ENABLED_UNIT_NAMES:
            enabled = self._systemctl("is-enabled", "--quiet", name, acceptable={0, 1, 4})
            active = self._systemctl("is-active", "--quiet", name, acceptable={0, 3, 4})
            states[name] = (enabled, active)
        return unit_directory, files, states

    @staticmethod
    def _plan_payload(
        unit_directory: Path,
        files: Mapping[str, tuple[bytes, int]],
        states: Mapping[str, tuple[bool, bool]],
        host_tools: Mapping[str, Mapping[str, str]],
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schemaVersion": SCHEMA_VERSION,
            "kind": "echo.operations-systemd-remove-plan",
            "unitDirectory": str(unit_directory),
            "units": {
                name: {
                    "sha256": _sha256(files[name][0]),
                    "mode": f"{files[name][1]:04o}",
                }
                for name in UNIT_NAMES
            },
            "timerStates": {
                name: {"enabled": states[name][0], "active": states[name][1]}
                for name in TIMER_NAMES
            },
            "recoveryState": {
                "enabled": states[RECOVERY_SERVICE_NAME][0],
                "active": states[RECOVERY_SERVICE_NAME][1],
            },
            "hostTools": dict(host_tools),
            "preservation": {
                "removed": ["managedUnitFiles", "managedTimerEnablement"],
                "preserved": [
                    "encryptedCredentials",
                    "deviceState",
                    "NASData",
                    "stateBackups",
                    "auditEvidence",
                ],
            },
        }
        payload["planId"] = _sha256(_canonical_json(payload))
        payload["removeConfirmation"] = f"REMOVE ECHO OPERATIONS {payload['planId']}"
        return payload

    def plan(self) -> dict[str, Any]:
        host_tools = _validated_host_tools(self.tools, trusted_uid=self.trusted_uid)
        unit_directory, files, states = self._snapshot()
        return self._plan_payload(unit_directory, files, states, host_tools)

    def _restore_states(self, states: Mapping[str, tuple[bool, bool]]) -> None:
        for name, (enabled, active) in states.items():
            if enabled:
                self._systemctl("enable", name, acceptable={0, 1})
            else:
                self._systemctl("disable", name, acceptable={0, 1, 5})
            if active:
                self._systemctl("start", name, acceptable={0, 5})
            else:
                self._systemctl("stop", name, acceptable={0, 5})

    def remove(self, plan: Mapping[str, Any], confirmation: str) -> dict[str, Any]:
        host_tools = _validated_host_tools(self.tools, trusted_uid=self.trusted_uid)
        unit_directory, files, states = self._snapshot()
        expected = self._plan_payload(unit_directory, files, states, host_tools)
        if dict(plan) != expected:
            raise OperationsSystemdError(
                "operations systemd remove plan no longer matches the host"
            )
        if confirmation != expected["removeConfirmation"]:
            raise OperationsSystemdError("operations systemd remove confirmation is invalid")
        if self.effective_uid != 0:
            raise OperationsSystemdError("operations systemd removal requires root")
        try:
            for name in ENABLED_UNIT_NAMES:
                self._systemctl("disable", "--now", name, acceptable={0, 1, 5})
            for name in UNIT_NAMES:
                path = unit_directory / name
                if path.is_symlink() or not path.is_file():
                    raise OperationsSystemdError(
                        f"managed operations systemd unit changed before removal: {name}"
                    )
                if _read_existing_unit(path, name, trusted_uid=self.trusted_uid) != files[name]:
                    raise OperationsSystemdError(
                        f"managed operations systemd unit changed before removal: {name}"
                    )
                path.unlink()
            self._systemctl("daemon-reload")
        except (OSError, OperationsSystemdError) as exc:
            try:
                for name in UNIT_NAMES:
                    OperationsSystemdInstaller._atomic_write(
                        unit_directory / name,
                        files[name][0],
                        files[name][1],
                    )
                self._systemctl("daemon-reload")
                self._restore_states(states)
            except (OSError, OperationsSystemdError) as rollback_exc:
                raise OperationsSystemdError(
                    "operations systemd removal failed and rollback was incomplete"
                ) from rollback_exc
            raise OperationsSystemdError(
                "operations systemd removal failed and managed units were restored"
            ) from exc
        return {
            "schemaVersion": SCHEMA_VERSION,
            "kind": "echo.operations-systemd-removal",
            "planId": expected["planId"],
            "unitsRemoved": list(UNIT_NAMES),
            "timersDisabled": list(TIMER_NAMES),
            "recoveryServiceDisabled": RECOVERY_SERVICE_NAME,
            "preserved": expected["preservation"]["preserved"],
            "removed": True,
        }


def _config_from_payload(value: object) -> OperationsConfig:
    keys = {
        "bundleRoot",
        "backupDirectory",
        "backupMountpoint",
        "auditDirectory",
        "auditMountpoint",
        "backupCredential",
        "auditCredential",
        "backupKeep",
        "auditKeepDays",
        "auditKeepMinimum",
    }
    if not isinstance(value, dict) or set(value) != keys:
        raise OperationsSystemdError("operations systemd plan config has an unexpected schema")
    path_keys = (
        "bundleRoot",
        "backupDirectory",
        "backupMountpoint",
        "auditDirectory",
        "auditMountpoint",
        "backupCredential",
        "auditCredential",
    )
    integer_keys = ("backupKeep", "auditKeepDays", "auditKeepMinimum")
    if any(not isinstance(value[key], str) or not value[key] for key in path_keys) or any(
        not isinstance(value[key], int) or isinstance(value[key], bool) for key in integer_keys
    ):
        raise OperationsSystemdError("operations systemd plan config has invalid value types")
    return OperationsConfig(
        bundle_root=Path(value["bundleRoot"]),
        backup_directory=Path(value["backupDirectory"]),
        backup_mountpoint=Path(value["backupMountpoint"]),
        audit_directory=Path(value["auditDirectory"]),
        audit_mountpoint=Path(value["auditMountpoint"]),
        backup_credential=Path(value["backupCredential"]),
        audit_credential=Path(value["auditCredential"]),
        backup_keep=value["backupKeep"],
        audit_keep_days=value["auditKeepDays"],
        audit_keep_minimum=value["auditKeepMinimum"],
    )


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise OperationsSystemdError(f"operations systemd plan repeats JSON key: {key}")
        value[key] = item
    return value


def _load_plan(path: Path) -> dict[str, Any]:
    payload = _safe_regular(
        path,
        "operations systemd plan",
        maximum=MAX_PLAN_BYTES,
        trusted_uid=os.getuid(),
    )
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise OperationsSystemdError("operations systemd plan is not JSON") from exc
    if not isinstance(value, dict):
        raise OperationsSystemdError("operations systemd plan must be an object")
    return value


def _write_plan(path: Path, payload: Mapping[str, Any]) -> None:
    text = str(path)
    if (
        not path.is_absolute()
        or SAFE_PATH.fullmatch(text) is None
        or "//" in text
        or any(part in {".", ".."} for part in path.parts)
    ):
        raise OperationsSystemdError("operations systemd plan output path is unsafe")
    parent = _safe_path(path.parent, "operations plan directory", directory=True)
    _assert_owned_directory(
        parent,
        "operations plan directory",
        trusted_uid=os.geteuid(),
    )
    target = parent / path.name
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = -1
    created = False
    try:
        descriptor = os.open(target, flags, 0o400)
        created = True
        os.fchmod(descriptor, 0o400)
        with os.fdopen(descriptor, "wb", closefd=True) as output:
            descriptor = -1
            output.write(_canonical_json(payload))
            output.flush()
            os.fsync(output.fileno())
    except OSError as exc:
        if created and target.exists() and not target.is_symlink() and target.is_file():
            target.unlink()
        raise OperationsSystemdError(
            "operations systemd plan output must be a new private file"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan = subparsers.add_parser("plan")
    plan.add_argument("--bundle-root", type=Path, required=True)
    plan.add_argument("--backup-directory", type=Path, required=True)
    plan.add_argument("--backup-mountpoint", type=Path, required=True)
    plan.add_argument("--audit-directory", type=Path, required=True)
    plan.add_argument("--audit-mountpoint", type=Path, required=True)
    plan.add_argument("--backup-credential", type=Path, required=True)
    plan.add_argument("--audit-credential", type=Path, required=True)
    plan.add_argument("--backup-keep", type=int, default=7)
    plan.add_argument("--audit-keep-days", type=int, default=365)
    plan.add_argument("--audit-keep-minimum", type=int, default=12)
    plan.add_argument("--output", type=Path, required=True)
    apply_parser = subparsers.add_parser("apply")
    apply_parser.add_argument("--plan", type=Path, required=True)
    apply_parser.add_argument("--confirm", required=True)
    remove_plan = subparsers.add_parser("remove-plan")
    remove_plan.add_argument("--output", type=Path, required=True)
    remove_parser = subparsers.add_parser("remove")
    remove_parser.add_argument("--plan", type=Path, required=True)
    remove_parser.add_argument("--confirm", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "plan":
            config = OperationsConfig(
                bundle_root=args.bundle_root,
                backup_directory=args.backup_directory,
                backup_mountpoint=args.backup_mountpoint,
                audit_directory=args.audit_directory,
                audit_mountpoint=args.audit_mountpoint,
                backup_credential=args.backup_credential,
                audit_credential=args.audit_credential,
                backup_keep=args.backup_keep,
                audit_keep_days=args.audit_keep_days,
                audit_keep_minimum=args.audit_keep_minimum,
            )
            plan = OperationsSystemdInstaller(config).plan()
            _write_plan(args.output, plan)
            print(
                "ECHO_OPERATIONS_SYSTEMD_PLAN_READY "
                f"plan={plan['planId']} confirmation={plan['installConfirmation']!r}"
            )
            return 0
        if args.command == "remove-plan":
            plan = OperationsSystemdRemover().plan()
            _write_plan(args.output, plan)
            print(
                "ECHO_OPERATIONS_SYSTEMD_REMOVE_PLAN_READY "
                f"plan={plan['planId']} confirmation={plan['removeConfirmation']!r}"
            )
            return 0
        plan = _load_plan(args.plan)
        if args.command == "apply":
            config = _config_from_payload(plan.get("config"))
            report = OperationsSystemdInstaller(config).install(plan, args.confirm)
        else:
            report = OperationsSystemdRemover().remove(plan, args.confirm)
    except (OSError, OperationsSystemdError, subprocess.TimeoutExpired) as exc:
        print(f"Echo operations systemd configuration failed: {exc}", file=sys.stderr)
        return 1
    if args.command == "apply":
        print(
            "ECHO_OPERATIONS_SYSTEMD_INSTALLED "
            f"plan={report['planId']} timers={len(report['timersEnabled'])}"
        )
    else:
        print(
            "ECHO_OPERATIONS_SYSTEMD_REMOVED "
            f"plan={report['planId']} preserved={len(report['preserved'])}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
