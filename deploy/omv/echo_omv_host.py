#!/usr/bin/env python3
"""Plan, install, upgrade, or remove the Echo OMV host bridge safely."""

from __future__ import annotations

import argparse
import contextlib
import grp
import hashlib
import json
import os
import platform
import re
import shlex
import socket
import stat
import subprocess  # nosec B404
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from platform_preflight import (
    PlatformPaths,
    PlatformPreflightError,
    probe_nas_readiness,
)

STATE_SCHEMA_VERSION = 1
SERVICE_NAME = "echo-omv-bridge.service"
GROUP_NAME = "echo-omv"
MAX_SOURCE_BYTES = 4 * 1024 * 1024
MAX_STATE_BYTES = 64 * 1024
MAX_OS_RELEASE_BYTES = 64 * 1024
SUPPORTED_DEBIAN_VERSION = "13"
SUPPORTED_OMV_MAJOR = 8
SUPPORT_MATRIX_ID = "debian-13+omv-8"
SOURCE_FILES = (
    "appliance/__init__.py",
    "appliance/omv_bridge.py",
)
_MANAGED_CODE_ROOT = "/usr/lib/echo-os/omv-bridge"
_UNIT_EXACT_LINES = {
    "User=": "User=root",
    "Group=": f"Group={GROUP_NAME}",
    "WorkingDirectory=": f"WorkingDirectory={_MANAGED_CODE_ROOT}",
    "Environment=": f"Environment=PYTHONPATH={_MANAGED_CODE_ROOT}",
    "RuntimeDirectory=": "RuntimeDirectory=echo-omv",
    "RuntimeDirectoryMode=": "RuntimeDirectoryMode=0750",
    "UMask=": "UMask=0007",
    "ExecStart=": (
        "ExecStart=/usr/bin/python3 -m appliance.omv_bridge "
        "--socket /run/echo-omv/omv.sock --omv-rpc /usr/sbin/omv-rpc "
        "--lsblk /usr/bin/lsblk"
    ),
    "NoNewPrivileges=": "NoNewPrivileges=true",
    "CapabilityBoundingSet=": "CapabilityBoundingSet=",
    "PrivateNetwork=": "PrivateNetwork=true",
    "PrivateDevices=": "PrivateDevices=true",
    "ProtectSystem=": "ProtectSystem=strict",
    "RestrictAddressFamilies=": "RestrictAddressFamilies=AF_UNIX",
    "ReadWritePaths=": "ReadWritePaths=/run/echo-omv",
}
_UNIT_FORBIDDEN_PREFIXES = (
    "BindPaths=",
    "EnvironmentFile=",
    "ExecCondition=",
    "ExecReload=",
    "ExecStartPre=",
    "ExecStartPost=",
    "ExecStop=",
    "ExecStopPost=",
    "RootDirectory=",
)


class HostInstallError(RuntimeError):
    """The requested host integration operation cannot proceed safely."""


@dataclass(frozen=True)
class HostLayout:
    unit_path: Path = Path("/etc/systemd/system/echo-omv-bridge.service")
    code_root: Path = Path("/usr/lib/echo-os/omv-bridge")
    state_root: Path = Path("/var/lib/echo-os/omv-host")
    socket_path: Path = Path("/run/echo-omv/omv.sock")

    @property
    def state_path(self) -> Path:
        return self.state_root / "install-state.json"

    @property
    def uninstall_receipt_path(self) -> Path:
        return self.state_root / "last-uninstall.json"


@dataclass(frozen=True)
class ToolPaths:
    python: Path = Path("/usr/bin/python3")
    omv_rpc: Path = Path("/usr/sbin/omv-rpc")
    dpkg_query: Path = Path("/usr/bin/dpkg-query")
    lsblk: Path = Path("/usr/bin/lsblk")
    systemctl: Path = Path("/usr/bin/systemctl")
    systemd_analyze: Path = Path("/usr/bin/systemd-analyze")
    groupadd: Path = Path("/usr/sbin/groupadd")
    groupdel: Path = Path("/usr/sbin/groupdel")


@dataclass(frozen=True)
class GroupInfo:
    name: str
    gid: int
    members: tuple[str, ...]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_lower_hex(value: str, length: int) -> bool:
    return len(value) == length and all(character in "0123456789abcdef" for character in value)


def _utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _normalized_architecture(value: str) -> str:
    aliases = {
        "x86_64": "amd64",
        "amd64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }
    architecture = aliases.get(value.strip().casefold())
    if architecture is None:
        raise HostInstallError(f"unsupported host architecture: {value!r}")
    return architecture


def _parse_os_release(payload: bytes) -> dict[str, str]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HostInstallError("host os-release must be UTF-8") from exc
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise HostInstallError("host os-release contains an invalid line")
        key, encoded = line.split("=", 1)
        if re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", key) is None or key in values:
            raise HostInstallError("host os-release contains an invalid or duplicate key")
        try:
            decoded = shlex.split(encoded, comments=False, posix=True)
        except ValueError as exc:
            raise HostInstallError("host os-release contains an invalid value") from exc
        if len(decoded) != 1 or len(decoded[0]) > 255 or any(char < " " for char in decoded[0]):
            raise HostInstallError("host os-release contains an invalid value")
        values[key] = decoded[0]
    return values


def _omv_major_version(value: str) -> int:
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > 128
        or "\n" in normalized
        or "\r" in normalized
        or any(character < " " for character in normalized)
    ):
        raise HostInstallError("installed openmediavault package version is invalid")
    match = re.fullmatch(r"(?:[0-9]+:)?([0-9]+)(?:[.+~:-][0-9A-Za-z.+~:-]+)?", normalized)
    if match is None:
        raise HostInstallError("installed openmediavault package version is invalid")
    return int(match.group(1))


def _lookup_group(name: str) -> GroupInfo | None:
    try:
        entry = grp.getgrnam(name)
    except KeyError:
        return None
    return GroupInfo(entry.gr_name, entry.gr_gid, tuple(entry.gr_mem))


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603
        command,
        check=False,
        text=True,
        capture_output=True,
    )


def _validate_unit(unit: bytes) -> None:
    try:
        text = unit.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HostInstallError("OMV bridge systemd unit must be UTF-8") from exc
    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if "/opt/echo-os" in text or any(line.startswith(_UNIT_FORBIDDEN_PREFIXES) for line in lines):
        raise HostInstallError("OMV bridge systemd unit escapes the managed code boundary")
    for prefix, expected in _UNIT_EXACT_LINES.items():
        matches = [line for line in lines if line.startswith(prefix)]
        if matches != [expected]:
            raise HostInstallError(
                f"OMV bridge systemd unit has an unsafe or duplicate {prefix} directive"
            )


def _safe_read(path: Path, *, maximum: int) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise HostInstallError(f"cannot safely open {path}") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or not 0 <= info.st_size <= maximum:
            raise HostInstallError(f"unsafe or oversized regular file: {path}")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, maximum - total + 1))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum:
                raise HostInstallError(f"file exceeds safety limit: {path}")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _assert_managed_regular(
    path: Path,
    *,
    maximum: int,
    mode: int,
    trusted_uid: int,
) -> bytes:
    data = _safe_read(path, maximum=maximum)
    info = path.lstat()
    if info.st_uid != trusted_uid or stat.S_IMODE(info.st_mode) != mode:
        raise HostInstallError(f"managed host file has unsafe ownership or mode: {path}")
    return data


def _assert_trusted_executable(path: Path) -> None:
    try:
        resolved = path.resolve(strict=True)
        info = resolved.stat()
    except OSError as exc:
        raise HostInstallError(f"required executable is unavailable: {path}") from exc
    if (
        not resolved.is_absolute()
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
        or stat.S_IMODE(info.st_mode) & 0o022
        or not os.access(resolved, os.X_OK)
    ):
        raise HostInstallError(f"required executable is not root-trusted: {path}")


def _ensure_directory(
    path: Path,
    mode: int,
    *,
    trusted_uid: int,
    enforce_mode: bool,
) -> None:
    path.mkdir(parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or path.is_symlink() or info.st_uid != trusted_uid:
        raise HostInstallError(f"host installation directory is unsafe: {path}")
    if enforce_mode:
        path.chmod(mode)


def _atomic_write(
    path: Path,
    data: bytes,
    *,
    mode: int,
    trusted_uid: int,
) -> None:
    _ensure_directory(
        path.parent,
        0o755 if mode != 0o600 else 0o700,
        trusted_uid=trusted_uid,
        enforce_mode=False,
    )
    if path.is_symlink():
        raise HostInstallError(f"refusing to replace a symlink: {path}")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        owned = descriptor
        descriptor = -1
        with os.fdopen(owned, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        path.chmod(mode)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary.exists():
            temporary.unlink()


def _remove_empty_parents(path: Path, *, stop: Path) -> None:
    current = path
    while current != stop and current.is_relative_to(stop):
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def _unix_health(socket_path: Path) -> bool:
    if not socket_path.is_absolute() or socket_path.is_symlink():
        return False
    try:
        info = socket_path.stat()
    except OSError:
        return False
    if not stat.S_ISSOCK(info.st_mode):
        return False
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(2)
    try:
        client.connect(str(socket_path))
        client.sendall(b"GET /health HTTP/1.1\r\nHost: echo-omv\r\nConnection: close\r\n\r\n")
        response = client.recv(4096)
    except OSError:
        return False
    finally:
        client.close()
    return response.startswith(b"HTTP/1.0 200 ") or response.startswith(b"HTTP/1.1 200 ")


class OmvHostInstaller:
    def __init__(
        self,
        source_root: Path | str,
        gid: int,
        *,
        layout: HostLayout | None = None,
        tools: ToolPaths | None = None,
        command_runner: Callable[[list[str]], subprocess.CompletedProcess[str]] = _run,
        group_lookup: Callable[[str], GroupInfo | None] = _lookup_group,
        health_check: Callable[[Path], bool] = _unix_health,
        executable_check: Callable[[Path], None] = _assert_trusted_executable,
        system_name: str | None = None,
        architecture: str | None = None,
        os_release_path: Path | str = Path("/usr/lib/os-release"),
        platform_paths: PlatformPaths | None = None,
        hostname: str | None = None,
        effective_uid: int | None = None,
        trusted_uid: int = 0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if isinstance(gid, bool) or not 1 <= gid <= 2**31 - 1:
            raise HostInstallError("Echo OMV group GID must be a positive integer")
        self.source_root = Path(source_root).resolve()
        self.gid = gid
        self.layout = layout or HostLayout()
        self.tools = tools or ToolPaths()
        self._command_runner = command_runner
        self._group_lookup = group_lookup
        self._health_check = health_check
        self._executable_check = executable_check
        self._system_name = system_name or platform.system()
        self._architecture = architecture or platform.machine()
        self._os_release_path = Path(os_release_path)
        self._platform_paths = platform_paths or PlatformPaths()
        self._hostname = hostname
        self._effective_uid = os.geteuid() if effective_uid is None else effective_uid
        self._trusted_uid = trusted_uid
        self._sleep = sleep

    @property
    def unit_source(self) -> Path:
        return self.source_root / "deploy/omv/echo-omv-bridge.service.example"

    @property
    def platform_preflight_source(self) -> Path:
        return self.source_root / "deploy/omv/platform_preflight.py"

    def _source_payload(self) -> tuple[dict[str, bytes], bytes, dict[str, str], str]:
        sources: dict[str, bytes] = {}
        hashes: dict[str, str] = {}
        for relative in SOURCE_FILES:
            payload = _safe_read(self.source_root / relative, maximum=MAX_SOURCE_BYTES)
            sources[relative] = payload
            hashes[relative] = _sha256(payload)
        unit = _safe_read(self.unit_source, maximum=MAX_SOURCE_BYTES)
        _validate_unit(unit)
        hashes["systemdUnit"] = _sha256(unit)
        preflight = _safe_read(self.platform_preflight_source, maximum=MAX_SOURCE_BYTES)
        hashes["platformPreflight"] = _sha256(preflight)
        identity = _sha256(
            b"\0".join(
                [
                    unit,
                    preflight,
                    *(sources[name] for name in SOURCE_FILES),
                    str(self.gid).encode(),
                ]
            )
        )[:16]
        return sources, unit, hashes, identity

    def _load_state(self) -> dict[str, Any] | None:
        path = self.layout.state_path
        if not path.exists():
            if path.is_symlink():
                raise HostInstallError("managed OMV host state is a broken symlink")
            return None
        try:
            value = json.loads(
                _assert_managed_regular(
                    path,
                    maximum=MAX_STATE_BYTES,
                    mode=0o600,
                    trusted_uid=self._trusted_uid,
                ).decode("utf-8")
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HostInstallError("managed OMV host state is invalid") from exc
        if (
            not isinstance(value, dict)
            or value.get("schemaVersion") != STATE_SCHEMA_VERSION
            or value.get("groupName") != GROUP_NAME
            or isinstance(value.get("gid"), bool)
            or not isinstance(value.get("gid"), int)
            or not isinstance(value.get("groupCreated"), bool)
            or not isinstance(value.get("bundleId"), str)
            or not _is_lower_hex(value["bundleId"], 16)
            or not isinstance(value.get("unitSha256"), str)
            or not _is_lower_hex(value["unitSha256"], 64)
            or not isinstance(value.get("codeSha256"), dict)
        ):
            raise HostInstallError("managed OMV host state schema is invalid")
        code_hashes = value["codeSha256"]
        if set(code_hashes) != set(SOURCE_FILES) or any(
            not isinstance(code_hashes[name], str) or not _is_lower_hex(code_hashes[name], 64)
            for name in SOURCE_FILES
        ):
            raise HostInstallError("managed OMV host code manifest is invalid")
        return value

    def _installed_hashes(self) -> tuple[str | None, dict[str, str]]:
        unit_hash = None
        if self.layout.unit_path.exists():
            unit_hash = _sha256(
                _assert_managed_regular(
                    self.layout.unit_path,
                    maximum=MAX_SOURCE_BYTES,
                    mode=0o644,
                    trusted_uid=self._trusted_uid,
                )
            )
        elif self.layout.unit_path.is_symlink():
            raise HostInstallError("installed OMV unit is a broken symlink")
        code_hashes: dict[str, str] = {}
        for relative in SOURCE_FILES:
            target = self.layout.code_root / relative
            if target.exists():
                code_hashes[relative] = _sha256(
                    _assert_managed_regular(
                        target,
                        maximum=MAX_SOURCE_BYTES,
                        mode=0o644,
                        trusted_uid=self._trusted_uid,
                    )
                )
            elif target.is_symlink():
                raise HostInstallError("installed OMV bridge code is a broken symlink")
        return unit_hash, code_hashes

    def _assert_managed_install_untouched(self, state: dict[str, Any]) -> None:
        unit_hash, code_hashes = self._installed_hashes()
        if unit_hash != state["unitSha256"] or code_hashes != state["codeSha256"]:
            raise HostInstallError(
                "installed OMV bridge differs from its managed manifest; refusing overwrite"
            )
        if state["gid"] != self.gid:
            raise HostInstallError(
                f"managed OMV bridge uses GID {state['gid']}, requested {self.gid}"
            )

    def _assert_prerequisites(self) -> str:
        if self._system_name != "Linux":
            raise HostInstallError("Echo OMV host integration requires Linux")
        architecture = _normalized_architecture(self._architecture)
        for path in (
            self.tools.python,
            self.tools.omv_rpc,
            self.tools.dpkg_query,
            self.tools.lsblk,
            self.tools.systemctl,
            self.tools.systemd_analyze,
            self.tools.groupadd,
            self.tools.groupdel,
        ):
            self._executable_check(path)
        return architecture

    def _assert_supported_platform(self) -> dict[str, Any]:
        if not self._os_release_path.is_absolute() or self._os_release_path.is_symlink():
            raise HostInstallError("host os-release path must be one trusted absolute file")
        payload = _safe_read(self._os_release_path, maximum=MAX_OS_RELEASE_BYTES)
        info = self._os_release_path.lstat()
        if info.st_uid != self._trusted_uid or stat.S_IMODE(info.st_mode) & 0o022:
            raise HostInstallError("host os-release ownership or mode is unsafe")
        release = _parse_os_release(payload)
        distribution = release.get("ID", "").casefold()
        distribution_version = release.get("VERSION_ID", "")
        if distribution != "debian" or distribution_version != SUPPORTED_DEBIAN_VERSION:
            raise HostInstallError(
                "unsupported host distribution: Echo OMV bridge requires Debian 13"
            )

        result = self._command_runner(
            [
                str(self.tools.dpkg_query),
                "-W",
                "-f=${Version}",
                "openmediavault",
            ]
        )
        if result.returncode != 0 or not isinstance(result.stdout, str):
            raise HostInstallError("openmediavault package version could not be queried")
        omv_version = result.stdout.strip()
        omv_major = _omv_major_version(omv_version)
        if omv_major != SUPPORTED_OMV_MAJOR:
            raise HostInstallError(
                "unsupported openmediavault version: Echo OMV bridge requires OMV 8"
            )
        try:
            readiness = probe_nas_readiness(
                paths=self._platform_paths,
                hostname=self._hostname,
                trusted_uid=self._trusted_uid,
            )
        except PlatformPreflightError as exc:
            raise HostInstallError(f"OMV platform preflight could not be proven: {exc}") from exc
        if not readiness["ready"]:
            issue_codes = ", ".join(issue["code"] for issue in readiness["issues"])
            raise HostInstallError(f"OMV platform preflight failed: {issue_codes}")
        return {
            "distribution": distribution,
            "distributionVersion": distribution_version,
            "omvVersion": omv_version,
            "omvMajor": omv_major,
            "supportMatrix": SUPPORT_MATRIX_ID,
            "platformPreflight": readiness,
        }

    def plan(self) -> dict[str, Any]:
        architecture = self._assert_prerequisites()
        host_platform = self._assert_supported_platform()
        _sources, _unit, source_hashes, bundle_id = self._source_payload()
        state = self._load_state()
        group = self._group_lookup(GROUP_NAME)
        if group is not None and group.gid != self.gid:
            raise HostInstallError(
                f"{GROUP_NAME} already uses GID {group.gid}, expected {self.gid}"
            )
        if state is None:
            unit_exists = self.layout.unit_path.exists() or self.layout.unit_path.is_symlink()
            code_exists = self.layout.code_root.exists() or self.layout.code_root.is_symlink()
            if unit_exists or code_exists:
                raise HostInstallError(
                    "unmanaged OMV bridge files already exist; refusing to adopt or overwrite"
                )
            action = "install"
        else:
            self._assert_managed_install_untouched(state)
            action = "unchanged" if state["bundleId"] == bundle_id else "upgrade"
        return {
            "schemaVersion": STATE_SCHEMA_VERSION,
            "supported": True,
            "architecture": architecture,
            **host_platform,
            "sourceRoot": str(self.source_root),
            "gid": self.gid,
            "groupName": GROUP_NAME,
            "groupExists": group is not None,
            "action": action,
            "bundleId": bundle_id,
            "sourceSha256": source_hashes,
            "unitPath": str(self.layout.unit_path),
            "codeRoot": str(self.layout.code_root),
            "statePath": str(self.layout.state_path),
            "installConfirmation": f"INSTALL ECHO OMV BRIDGE {self.gid} {bundle_id}",
            "uninstallConfirmation": (
                f"UNINSTALL ECHO OMV BRIDGE {state['bundleId']}" if state else None
            ),
        }

    def _command(self, *parts: str) -> subprocess.CompletedProcess[str]:
        result = self._command_runner(list(parts))
        if result.returncode != 0:
            raise subprocess.CalledProcessError(
                result.returncode,
                list(parts),
                output=result.stdout,
                stderr=result.stderr,
            )
        return result

    def _wait_for_health(self, *, attempts: int = 60) -> None:
        for _attempt in range(attempts):
            if self._health_check(self.layout.socket_path):
                info = self.layout.socket_path.stat()
                if stat.S_IMODE(info.st_mode) != 0o660 or info.st_gid != self.gid:
                    raise HostInstallError(
                        "OMV bridge socket health passed with unsafe mode or group"
                    )
                return
            self._sleep(0.25)
        raise HostInstallError("OMV bridge did not become healthy within 15 seconds")

    def _write_code(self, sources: dict[str, bytes]) -> None:
        _ensure_directory(
            self.layout.code_root,
            0o755,
            trusted_uid=self._trusted_uid,
            enforce_mode=True,
        )
        for relative, payload in sources.items():
            _ensure_directory(
                (self.layout.code_root / relative).parent,
                0o755,
                trusted_uid=self._trusted_uid,
                enforce_mode=True,
            )
            _atomic_write(
                self.layout.code_root / relative,
                payload,
                mode=0o644,
                trusted_uid=self._trusted_uid,
            )

    def _write_state(
        self,
        *,
        bundle_id: str,
        unit_hash: str,
        code_hashes: dict[str, str],
        group_created: bool,
        installed_at: str,
    ) -> None:
        state = {
            "schemaVersion": STATE_SCHEMA_VERSION,
            "bundleId": bundle_id,
            "installedAt": installed_at,
            "updatedAt": _utc_timestamp(),
            "sourceRoot": str(self.source_root),
            "groupName": GROUP_NAME,
            "gid": self.gid,
            "groupCreated": group_created,
            "unitSha256": unit_hash,
            "codeSha256": code_hashes,
        }
        encoded = json.dumps(
            state,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        _ensure_directory(
            self.layout.state_root,
            0o700,
            trusted_uid=self._trusted_uid,
            enforce_mode=True,
        )
        _atomic_write(
            self.layout.state_path,
            encoded + b"\n",
            mode=0o600,
            trusted_uid=self._trusted_uid,
        )

    def install(self, confirmation: str) -> dict[str, Any]:
        report = self.plan()
        if confirmation != report["installConfirmation"]:
            raise HostInstallError("installation confirmation does not match this source and GID")
        if self._effective_uid != 0:
            raise HostInstallError("installation must run as root")
        sources, unit, source_hashes, bundle_id = self._source_payload()
        state = self._load_state()
        previous_unit, previous_code = self._installed_bytes()
        group = self._group_lookup(GROUP_NAME)
        group_created_now = False
        group_created_ever = bool(state and state["groupCreated"])
        installed_at = str(state.get("installedAt")) if state else _utc_timestamp()
        service_was_active = False
        try:
            if state is not None:
                active = self._command_runner(
                    [str(self.tools.systemctl), "is-active", "--quiet", SERVICE_NAME]
                )
                service_was_active = active.returncode == 0
            if group is None:
                self._command(
                    str(self.tools.groupadd),
                    "--non-unique",
                    "--gid",
                    str(self.gid),
                    GROUP_NAME,
                )
                group_created_now = True
                group_created_ever = True
                group = self._group_lookup(GROUP_NAME)
                if group is None or group.gid != self.gid:
                    raise HostInstallError("echo-omv group creation could not be verified")

            self._write_code(sources)
            _atomic_write(
                self.layout.unit_path,
                unit,
                mode=0o644,
                trusted_uid=self._trusted_uid,
            )
            self._command(str(self.tools.systemd_analyze), "verify", str(self.layout.unit_path))
            self._command(str(self.tools.systemctl), "daemon-reload")
            self._command(str(self.tools.systemctl), "enable", "--now", SERVICE_NAME)
            self._wait_for_health()
            code_hashes = {name: source_hashes[name] for name in SOURCE_FILES}
            self._write_state(
                bundle_id=bundle_id,
                unit_hash=source_hashes["systemdUnit"],
                code_hashes=code_hashes,
                group_created=group_created_ever,
                installed_at=installed_at,
            )
        except Exception as exc:
            self._rollback_install(
                previous_unit=previous_unit,
                previous_code=previous_code,
                service_was_active=service_was_active,
                remove_group=group_created_now,
            )
            if isinstance(exc, HostInstallError):
                raise
            raise HostInstallError(f"OMV bridge installation failed: {exc}") from exc
        return {**report, "installed": True, "action": report["action"]}

    def _installed_bytes(self) -> tuple[bytes | None, dict[str, bytes]]:
        unit = (
            _assert_managed_regular(
                self.layout.unit_path,
                maximum=MAX_SOURCE_BYTES,
                mode=0o644,
                trusted_uid=self._trusted_uid,
            )
            if self.layout.unit_path.exists()
            else None
        )
        code: dict[str, bytes] = {}
        for relative in SOURCE_FILES:
            target = self.layout.code_root / relative
            if target.exists():
                code[relative] = _assert_managed_regular(
                    target,
                    maximum=MAX_SOURCE_BYTES,
                    mode=0o644,
                    trusted_uid=self._trusted_uid,
                )
        return unit, code

    def _remove_installed_files(self) -> None:
        self.layout.unit_path.unlink(missing_ok=True)
        for relative in reversed(SOURCE_FILES):
            (self.layout.code_root / relative).unlink(missing_ok=True)
        _remove_empty_parents(
            self.layout.code_root / "appliance",
            stop=self.layout.code_root.parent,
        )

    def _rollback_install(
        self,
        *,
        previous_unit: bytes | None,
        previous_code: dict[str, bytes],
        service_was_active: bool,
        remove_group: bool,
    ) -> None:
        with contextlib.suppress(Exception):
            self._command(str(self.tools.systemctl), "disable", "--now", SERVICE_NAME)
        self._remove_installed_files()
        if previous_unit is not None:
            _atomic_write(
                self.layout.unit_path,
                previous_unit,
                mode=0o644,
                trusted_uid=self._trusted_uid,
            )
        if previous_code:
            self._write_code(previous_code)
        with contextlib.suppress(Exception):
            self._command(str(self.tools.systemctl), "daemon-reload")
            if service_was_active:
                self._command(str(self.tools.systemctl), "enable", "--now", SERVICE_NAME)
        if remove_group:
            with contextlib.suppress(Exception):
                self._command(str(self.tools.groupdel), GROUP_NAME)

    def uninstall(self, confirmation: str) -> dict[str, Any]:
        if self._effective_uid != 0:
            raise HostInstallError("uninstallation must run as root")
        state = self._load_state()
        if state is None:
            raise HostInstallError("no managed Echo OMV bridge installation was found")
        expected = f"UNINSTALL ECHO OMV BRIDGE {state['bundleId']}"
        if confirmation != expected:
            raise HostInstallError("uninstallation confirmation does not match installed bridge")
        self._assert_prerequisites()
        self._assert_managed_install_untouched(state)
        previous_unit, previous_code = self._installed_bytes()
        previous_receipt = None
        if self.layout.uninstall_receipt_path.exists():
            previous_receipt = _assert_managed_regular(
                self.layout.uninstall_receipt_path,
                maximum=MAX_STATE_BYTES,
                mode=0o600,
                trusted_uid=self._trusted_uid,
            )
        elif self.layout.uninstall_receipt_path.is_symlink():
            raise HostInstallError("managed OMV uninstall receipt is a broken symlink")
        active = self._command_runner(
            [str(self.tools.systemctl), "is-active", "--quiet", SERVICE_NAME]
        )
        service_was_active = active.returncode == 0
        try:
            self._command(str(self.tools.systemctl), "disable", "--now", SERVICE_NAME)
            self._remove_installed_files()
            self._command(str(self.tools.systemctl), "daemon-reload")
        except Exception as exc:
            self._rollback_install(
                previous_unit=previous_unit,
                previous_code=previous_code,
                service_was_active=service_was_active,
                remove_group=False,
            )
            raise HostInstallError(f"OMV bridge uninstall was rolled back: {exc}") from exc

        group_removed = False
        group_retained_reason: str | None = None
        group = self._group_lookup(GROUP_NAME)
        if state["groupCreated"] and group is not None and group.gid == state["gid"]:
            if group.members:
                group_retained_reason = "group still has explicit members"
            else:
                try:
                    self._command(str(self.tools.groupdel), GROUP_NAME)
                    group_removed = True
                except (OSError, subprocess.SubprocessError) as exc:
                    group_retained_reason = f"group removal was not safe: {exc}"

        receipt = {
            "schemaVersion": STATE_SCHEMA_VERSION,
            "uninstalledAt": _utc_timestamp(),
            "bundleId": state["bundleId"],
            "gid": state["gid"],
            "groupRemoved": group_removed,
            "groupRetainedReason": group_retained_reason,
            "preservedNasData": True,
        }
        try:
            _atomic_write(
                self.layout.uninstall_receipt_path,
                json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode() + b"\n",
                mode=0o600,
                trusted_uid=self._trusted_uid,
            )
            self.layout.state_path.unlink()
        except Exception as exc:
            with contextlib.suppress(Exception):
                if previous_receipt is None:
                    self.layout.uninstall_receipt_path.unlink(missing_ok=True)
                else:
                    _atomic_write(
                        self.layout.uninstall_receipt_path,
                        previous_receipt,
                        mode=0o600,
                        trusted_uid=self._trusted_uid,
                    )
            if group_removed:
                with contextlib.suppress(Exception):
                    self._command(
                        str(self.tools.groupadd),
                        "--non-unique",
                        "--gid",
                        str(state["gid"]),
                        GROUP_NAME,
                    )
            self._rollback_install(
                previous_unit=previous_unit,
                previous_code=previous_code,
                service_was_active=service_was_active,
                remove_group=False,
            )
            raise HostInstallError(f"OMV bridge uninstall was rolled back: {exc}") from exc
        return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=("plan", "install", "uninstall"),
        help="plan is read-only; install/uninstall require root and an exact confirmation",
    )
    parser.add_argument("--gid", type=int, required=True, help="numeric GID shared with Echo PGID")
    parser.add_argument(
        "--source-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Echo OS source tree containing appliance/ and deploy/omv/",
    )
    parser.add_argument("--confirm", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        installer = OmvHostInstaller(args.source_root, args.gid)
        if args.action == "plan":
            result = installer.plan()
        elif args.action == "install":
            result = installer.install(args.confirm)
        else:
            result = installer.uninstall(args.confirm)
    except (HostInstallError, OSError, subprocess.SubprocessError) as exc:
        print(f"Echo OMV host operation failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
