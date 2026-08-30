#!/usr/bin/env python3
"""Verify rendered Echo operations units with the host's native systemd parser."""

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
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

try:
    from deploy.appliance.operations_systemd import (
        UNIT_NAMES,
        OperationsConfig,
        _units,
    )
except ModuleNotFoundError:
    from operations_systemd import UNIT_NAMES, OperationsConfig, _units

MAX_TOOL_BYTES = 64 * 1024 * 1024
GIT_ID = re.compile(r"^[0-9a-f]{40}$")


class NativeSystemdVerificationError(RuntimeError):
    """The native systemd parser could not prove the generated unit contract."""


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _validate_tool(path: Path, *, trusted_uid: int) -> Path:
    if not path.is_absolute() or path.is_symlink():
        raise NativeSystemdVerificationError("systemd-analyze must be one absolute regular file")
    try:
        resolved = path.resolve(strict=True)
        info = resolved.stat()
    except OSError as exc:
        raise NativeSystemdVerificationError("systemd-analyze is unavailable") from exc
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != trusted_uid
        or stat.S_IMODE(info.st_mode) & 0o022
        or not os.access(resolved, os.X_OK)
        or not 0 < info.st_size <= MAX_TOOL_BYTES
    ):
        raise NativeSystemdVerificationError("systemd-analyze has unsafe ownership or mode")
    return resolved


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def verify_native_systemd_units(
    *,
    systemd_analyze: Path = Path("/usr/bin/systemd-analyze"),
    command_runner: Callable[[list[str]], subprocess.CompletedProcess[str]] = _run,
    trusted_uid: int = 0,
    system_name: str | None = None,
    os_release: dict[str, str] | None = None,
    require_os_id: str | None = None,
    require_version_id: str | None = None,
    source_revision: str,
) -> dict[str, Any]:
    observed_system = os.uname().sysname if system_name is None else system_name
    if observed_system != "Linux":
        raise NativeSystemdVerificationError("native systemd verification requires Linux")
    if GIT_ID.fullmatch(source_revision) is None:
        raise NativeSystemdVerificationError("source revision must be one full Git commit")
    release = platform.freedesktop_os_release() if os_release is None else os_release
    os_id = str(release.get("ID", "")).strip().casefold()
    version_id = str(release.get("VERSION_ID", "")).strip()
    codename = str(release.get("VERSION_CODENAME", "")).strip().casefold()
    if require_os_id is not None and os_id != require_os_id.casefold():
        raise NativeSystemdVerificationError("native systemd host OS does not match the gate")
    if require_version_id is not None and version_id != require_version_id:
        raise NativeSystemdVerificationError("native systemd host version does not match the gate")
    tool = _validate_tool(systemd_analyze, trusted_uid=trusted_uid)
    try:
        version = command_runner([str(tool), "--version"])
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NativeSystemdVerificationError("systemd version inspection could not run") from exc
    version_line = version.stdout.splitlines()[0].strip() if version.stdout else ""
    if version.returncode != 0 or not version_line.startswith("systemd "):
        raise NativeSystemdVerificationError("systemd-analyze returned an invalid version")

    with tempfile.TemporaryDirectory(prefix="echo-native-systemd-verify-") as temporary:
        root = Path(temporary)
        bundle = root / "bundle"
        bundle.mkdir(mode=0o755)
        for name in ("backup-state.sh", "export-audit-evidence.sh"):
            script = bundle / name
            script.write_text("#!/bin/sh\nexit 0\n")
            script.chmod(0o755)
        backup_mount = root / "backup"
        backup_directory = backup_mount / "echo-os"
        backup_directory.mkdir(parents=True, mode=0o700)
        audit_mount = root / "audit"
        audit_directory = audit_mount / "evidence"
        audit_directory.mkdir(parents=True, mode=0o700)
        credentials = root / "credentials"
        credentials.mkdir(mode=0o700)
        backup_credential = credentials / "backup"
        audit_credential = credentials / "audit"
        for credential in (backup_credential, audit_credential):
            credential.write_bytes(b"native-systemd-parser-placeholder")
            credential.chmod(0o600)

        config = OperationsConfig(
            bundle_root=bundle,
            backup_directory=backup_directory,
            backup_mountpoint=backup_mount,
            audit_directory=audit_directory,
            audit_mountpoint=audit_mount,
            backup_credential=backup_credential,
            audit_credential=audit_credential,
        )
        rendered = _units(config)
        unit_paths: list[str] = []
        docker_unit = root / "docker.service"
        docker_unit.write_text(
            "[Unit]\nDescription=Native parser fixture\n\n"
            "[Service]\nType=oneshot\nExecStart=/bin/true\nRemainAfterExit=yes\n"
        )
        docker_unit.chmod(0o644)
        unit_paths.append(str(docker_unit))
        for name in UNIT_NAMES:
            path = root / name
            path.write_bytes(rendered[name])
            path.chmod(0o644)
            unit_paths.append(str(path))
        try:
            completed = command_runner([str(tool), "verify", *unit_paths])
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise NativeSystemdVerificationError(
                "native systemd verification could not run"
            ) from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip().splitlines()
            suffix = f": {detail[-1][:500]}" if detail else ""
            raise NativeSystemdVerificationError(f"native systemd rejected generated units{suffix}")

    return {
        "schemaVersion": 1,
        "kind": "echo.operations-systemd-native-verification",
        "sourceRevision": source_revision,
        "os": {"id": os_id, "versionId": version_id, "codename": codename},
        "systemdVersion": version_line,
        "units": {name: {"sha256": _sha256(rendered[name])} for name in UNIT_NAMES},
        "verified": True,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--systemd-analyze",
        type=Path,
        default=Path("/usr/bin/systemd-analyze"),
    )
    parser.add_argument("--require-os-id")
    parser.add_argument("--require-version-id")
    parser.add_argument("--source-revision", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = verify_native_systemd_units(
            systemd_analyze=args.systemd_analyze,
            require_os_id=args.require_os_id,
            require_version_id=args.require_version_id,
            source_revision=args.source_revision,
        )
    except NativeSystemdVerificationError as exc:
        print(f"Echo native systemd verification failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
