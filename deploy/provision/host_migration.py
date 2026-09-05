"""Versioned, plan-bound host migration for installed p3 appliances.

This is intentionally separate from the container image transaction. It owns
only additive Debian dependencies and a small allow-list of host systemd units
shipped by the checked-out Echo source tree.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
MIGRATION_ID = "nas-maintenance-v2"
SOURCE_ROOT = Path("/opt/echo-os")
UNIT_DIRECTORY = Path("/etc/systemd/system")
MARKER_PATH = Path("/var/lib/echo-os/host-migration-nas-maintenance-v2.json")
DPKG_QUERY = Path("/usr/bin/dpkg-query")
APT_GET = Path("/usr/bin/apt-get")
SYSTEMCTL = Path("/usr/bin/systemctl")
MAX_UNIT_BYTES = 64 * 1024
PACKAGES = ("btrfs-progs", "nut-client", "nut-server", "smartmontools")
UNIT_SOURCES = {
    "echo-appliance.service": "deploy/provision/base/echo-appliance.service",
    "echo-ups-shutdown-guard.service": ("deploy/appliance/systemd/echo-ups-shutdown-guard.service"),
    "echo-ups-shutdown-guard.timer": ("deploy/appliance/systemd/echo-ups-shutdown-guard.timer"),
    "echo-smart-self-test.service": ("deploy/appliance/systemd/echo-smart-self-test.service"),
    "echo-smart-self-test.timer": ("deploy/appliance/systemd/echo-smart-self-test.timer"),
    "echo-mdraid-check.service": ("deploy/appliance/systemd/echo-mdraid-check.service"),
    "echo-mdraid-check.timer": ("deploy/appliance/systemd/echo-mdraid-check.timer"),
    "echo-btrfs-scrub.service": ("deploy/appliance/systemd/echo-btrfs-scrub.service"),
    "echo-btrfs-scrub.timer": ("deploy/appliance/systemd/echo-btrfs-scrub.timer"),
}
TIMERS = (
    "echo-ups-shutdown-guard.timer",
    "echo-smart-self-test.timer",
    "echo-mdraid-check.timer",
    "echo-btrfs-scrub.timer",
)


class HostMigrationError(RuntimeError):
    """The host migration could not be planned or applied safely."""


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _safe_directory(path: Path, *, trusted_uid: int) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise HostMigrationError(f"required directory is unavailable: {path}") from exc
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise HostMigrationError(f"unsafe directory: {path}")
    if metadata.st_uid != trusted_uid or (os.name == "posix" and metadata.st_mode & 0o022):
        raise HostMigrationError(f"unsafe directory ownership or mode: {path}")


def _source_payloads(source_root: Path, *, trusted_uid: int) -> dict[str, bytes]:
    _safe_directory(source_root, trusted_uid=trusted_uid)
    resolved_root = source_root.resolve(strict=True)
    result: dict[str, bytes] = {}
    for unit, relative in UNIT_SOURCES.items():
        path = source_root / relative
        try:
            metadata = path.lstat()
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise HostMigrationError(f"migration source is missing: {relative}") from exc
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or not resolved.is_relative_to(resolved_root)
            or metadata.st_uid != trusted_uid
            or (os.name == "posix" and metadata.st_mode & 0o022)
            or metadata.st_size > MAX_UNIT_BYTES
        ):
            raise HostMigrationError(f"unsafe migration source: {relative}")
        payload = path.read_bytes()
        if len(payload) > MAX_UNIT_BYTES or not payload.endswith(b"\n"):
            raise HostMigrationError(f"invalid migration unit: {relative}")
        result[unit] = payload
    return result


def _target_payload(path: Path, *, trusted_uid: int) -> bytes | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != trusted_uid
        or (os.name == "posix" and metadata.st_mode & 0o022)
        or metadata.st_size > MAX_UNIT_BYTES
    ):
        raise HostMigrationError(f"unsafe installed unit: {path.name}")
    payload = path.read_bytes()
    if len(payload) > MAX_UNIT_BYTES:
        raise HostMigrationError(f"installed unit grew beyond its safety limit: {path.name}")
    return payload


def _migration_evidence_current(path: Path, *, trusted_uid: int) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != trusted_uid
        or (os.name == "posix" and metadata.st_mode & 0o022)
        or metadata.st_size > MAX_UNIT_BYTES
    ):
        raise HostMigrationError(f"unsafe migration evidence: {path}")
    payload = path.read_bytes()
    if len(payload) > MAX_UNIT_BYTES:
        raise HostMigrationError("migration evidence grew beyond its safety limit")
    try:
        evidence = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HostMigrationError("migration evidence is invalid") from exc
    return (
        isinstance(evidence, dict)
        and type(evidence.get("schemaVersion")) is int
        and evidence["schemaVersion"] == SCHEMA_VERSION
        and evidence.get("migrationId") == MIGRATION_ID
    )


def _run(
    executable: Path,
    arguments: tuple[str, ...],
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]],
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    if not executable.is_file() or executable.is_symlink():
        raise HostMigrationError(f"trusted executable is unavailable: {executable.name}")
    try:
        return runner(
            [str(executable), *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise HostMigrationError(f"host command failed: {executable.name}") from exc


def package_installed(
    package: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    dpkg_query: Path = DPKG_QUERY,
) -> bool:
    if package not in PACKAGES:
        raise HostMigrationError("package probe escaped the migration allow-list")
    completed = _run(
        dpkg_query,
        ("-W", "-f=${db:Status-Status}", package),
        runner=runner,
        timeout=15.0,
    )
    return completed.returncode == 0 and completed.stdout.strip() == "installed"


def unit_enabled(
    unit: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    systemctl: Path = SYSTEMCTL,
) -> bool:
    if unit not in TIMERS:
        raise HostMigrationError("unit probe escaped the migration allow-list")
    completed = _run(
        systemctl,
        ("is-enabled", "--quiet", unit),
        runner=runner,
        timeout=15.0,
    )
    return completed.returncode == 0


def appliance_active(
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    systemctl: Path = SYSTEMCTL,
) -> bool:
    completed = _run(
        systemctl,
        ("is-active", "--quiet", "echo-appliance.service"),
        runner=runner,
        timeout=15.0,
    )
    return completed.returncode == 0


def plan_migration(
    *,
    source_root: Path = SOURCE_ROOT,
    unit_directory: Path = UNIT_DIRECTORY,
    marker_path: Path = MARKER_PATH,
    trusted_uid: int = 0,
    package_probe: Callable[[str], bool] = package_installed,
    enabled_probe: Callable[[str], bool] = unit_enabled,
) -> dict[str, Any]:
    sources = _source_payloads(source_root, trusted_uid=trusted_uid)
    _safe_directory(unit_directory, trusted_uid=trusted_uid)
    _safe_directory(marker_path.parent, trusted_uid=trusted_uid)
    evidence_current = _migration_evidence_current(marker_path, trusted_uid=trusted_uid)
    units: list[dict[str, Any]] = []
    for name, source in sources.items():
        installed = _target_payload(unit_directory / name, trusted_uid=trusted_uid)
        units.append(
            {
                "name": name,
                "sourceSha256": _sha256(source),
                "installedSha256": _sha256(installed) if installed is not None else None,
                "operation": "none" if installed == source else "install",
            }
        )
    missing_packages = [package for package in PACKAGES if not package_probe(package)]
    enable_timers = [timer for timer in TIMERS if not enabled_probe(timer)]
    operation = (
        "none"
        if not missing_packages
        and not enable_timers
        and evidence_current
        and all(item["operation"] == "none" for item in units)
        else "migrate"
    )
    binding = {
        "schemaVersion": SCHEMA_VERSION,
        "migrationId": MIGRATION_ID,
        "operation": operation,
        "packages": {
            "missing": missing_packages,
            "policy": "additiveOnlyNoAutomaticRemoval",
        },
        "units": units,
        "enableTimers": enable_timers,
        "restartServices": ["echo-appliance.service"],
        "evidenceCurrent": evidence_current,
        "scope": "p3BareMetalHost",
    }
    return {
        **binding,
        "planId": _sha256(_canonical(binding)),
        "requiresRoot": True,
        "rollback": {
            "units": "restoredOnFailure",
            "newPackages": "retained",
            "data": "untouched",
        },
    }


def _atomic_write(path: Path, payload: bytes, *, trusted_uid: int, trusted_gid: int) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        if os.name == "posix":
            os.chown(temporary, trusted_uid, trusted_gid)
        os.replace(temporary, path)
        if os.name == "posix":
            directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _systemctl(
    *arguments: str,
    runner: Callable[..., subprocess.CompletedProcess[str]],
    systemctl: Path,
) -> None:
    completed = _run(systemctl, tuple(arguments), runner=runner, timeout=45.0)
    if completed.returncode != 0:
        raise HostMigrationError(f"systemd rejected operation: {' '.join(arguments)}")


def apply_migration(
    plan_id: str,
    *,
    source_root: Path = SOURCE_ROOT,
    unit_directory: Path = UNIT_DIRECTORY,
    marker_path: Path = MARKER_PATH,
    trusted_uid: int = 0,
    trusted_gid: int = 0,
    package_probe: Callable[[str], bool] = package_installed,
    enabled_probe: Callable[[str], bool] = unit_enabled,
    active_probe: Callable[[], bool] = appliance_active,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    apt_get: Path = APT_GET,
    systemctl: Path = SYSTEMCTL,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict[str, Any]:
    if os.name == "posix" and os.geteuid() != trusted_uid:
        raise HostMigrationError("host migration requires root")
    plan = plan_migration(
        source_root=source_root,
        unit_directory=unit_directory,
        marker_path=marker_path,
        trusted_uid=trusted_uid,
        package_probe=package_probe,
        enabled_probe=enabled_probe,
    )
    if plan["planId"] != plan_id:
        raise HostMigrationError("host migration plan is stale; preview again")
    if plan["operation"] == "none":
        if not active_probe():
            raise HostMigrationError("appliance service is not active")
        return {**plan, "applied": False, "verified": True}

    missing_packages = tuple(plan["packages"]["missing"])
    if missing_packages:
        if _run(apt_get, ("update",), runner=runner, timeout=300.0).returncode != 0:
            raise HostMigrationError("apt metadata refresh failed")
        install = _run(
            apt_get,
            ("install", "--yes", "--no-install-recommends", *missing_packages),
            runner=runner,
            timeout=900.0,
        )
        if install.returncode != 0 or any(not package_probe(item) for item in missing_packages):
            raise HostMigrationError("required host package installation failed")

    sources = _source_payloads(source_root, trusted_uid=trusted_uid)
    originals = {
        name: _target_payload(unit_directory / name, trusted_uid=trusted_uid) for name in sources
    }
    planned_units = {item["name"]: item for item in plan["units"]}
    if any(
        _sha256(payload) != planned_units[name]["sourceSha256"]
        or (_sha256(originals[name]) if originals[name] is not None else None)
        != planned_units[name]["installedSha256"]
        for name, payload in sources.items()
    ):
        raise HostMigrationError(
            "host migration source or installed units changed after package installation"
        )
    previously_enabled = {timer: enabled_probe(timer) for timer in TIMERS}
    changed: list[str] = []
    marker = {
        "schemaVersion": SCHEMA_VERSION,
        "migrationId": MIGRATION_ID,
        "planId": plan_id,
        "appliedAt": clock().astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "packagesInstalled": list(missing_packages),
        "unitSha256": {name: _sha256(payload) for name, payload in sources.items()},
        "enabledTimers": list(TIMERS),
    }
    try:
        for name, payload in sources.items():
            if originals[name] != payload:
                changed.append(name)
                _atomic_write(
                    unit_directory / name,
                    payload,
                    trusted_uid=trusted_uid,
                    trusted_gid=trusted_gid,
                )
        _systemctl("daemon-reload", runner=runner, systemctl=systemctl)
        for timer in TIMERS:
            _systemctl("enable", "--now", timer, runner=runner, systemctl=systemctl)
        _systemctl("restart", "echo-appliance.service", runner=runner, systemctl=systemctl)
        for name, payload in sources.items():
            if _target_payload(unit_directory / name, trusted_uid=trusted_uid) != payload:
                raise HostMigrationError(f"installed unit verification failed: {name}")
        if any(not enabled_probe(timer) for timer in TIMERS):
            raise HostMigrationError("timer enablement verification failed")
        if not active_probe():
            raise HostMigrationError("appliance service did not become active")
        _atomic_write(
            marker_path,
            _canonical(marker) + b"\n",
            trusted_uid=trusted_uid,
            trusted_gid=trusted_gid,
        )
    except (OSError, HostMigrationError) as exc:
        rollback_errors: list[str] = []
        for name in reversed(changed):
            target = unit_directory / name
            try:
                original = originals[name]
                if original is None:
                    if target.is_symlink():
                        raise OSError("refusing to unlink a replaced unit symlink")
                    if target.exists():
                        target.unlink()
                else:
                    _atomic_write(
                        target,
                        original,
                        trusted_uid=trusted_uid,
                        trusted_gid=trusted_gid,
                    )
            except OSError:
                rollback_errors.append(name)
        try:
            _systemctl("daemon-reload", runner=runner, systemctl=systemctl)
            for timer, was_enabled in previously_enabled.items():
                if not was_enabled:
                    _systemctl("disable", "--now", timer, runner=runner, systemctl=systemctl)
            _systemctl("restart", "echo-appliance.service", runner=runner, systemctl=systemctl)
        except HostMigrationError:
            rollback_errors.append("systemd")
        if rollback_errors:
            raise HostMigrationError(
                "host migration failed and unit rollback was incomplete: "
                + ", ".join(rollback_errors)
            ) from exc
        raise HostMigrationError(
            "host migration failed; units were restored and newly installed packages were retained"
        ) from exc

    return {
        **plan,
        "applied": True,
        "verified": True,
        "marker": str(marker_path),
        "packagesInstalled": list(missing_packages),
        "unitsInstalled": changed,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--plan", action="store_true")
    action.add_argument("--apply", metavar="PLAN_ID")
    arguments = parser.parse_args(argv)
    try:
        result = plan_migration() if arguments.plan else apply_migration(arguments.apply)
    except HostMigrationError as exc:
        print(f"host migration refused: {exc}", file=os.sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI boundary
    raise SystemExit(main())
