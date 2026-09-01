#!/usr/bin/env python3
"""Read-only delivery preflight for the Echo OS OpenMediaVault host."""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shlex
import socket
import stat
import subprocess  # nosec B404
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
SUPPORTED_DEBIAN_VERSION = "13"
SUPPORTED_OMV_MAJOR = 8
SUPPORT_MATRIX = "debian-13+omv-8"
MAX_SYSTEM_FILE_BYTES = 512 * 1024
MAX_NETPLAN_FILES = 32
MAX_HOSTNAME_LENGTH = 15
HOSTNAME_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_OMV_VERSION_PATTERN = re.compile(r"(?:[0-9]+:)?([0-9]+)(?:[.+~:-][0-9A-Za-z.+~:-]+)?")
_SET_FIELD_PATTERN = re.compile(r"\.set\(\s*['\"](?P<field>dns(?:name)?servers)['\"]\s*,")


class PlatformPreflightError(RuntimeError):
    """The target cannot be proven safe for the supported Echo NAS path."""


@dataclass(frozen=True)
class PlatformPaths:
    os_release: Path = Path("/usr/lib/os-release")
    dpkg_query: Path = Path("/usr/bin/dpkg-query")
    netplan_directory: Path = Path("/etc/netplan")
    netplan_importer: Path = Path("/usr/share/openmediavault/confdb/populate.d/40netplan.sh")
    network_interface_model: Path = Path(
        "/usr/share/openmediavault/datamodels/conf.system.network.interface.json"
    )


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603
        command,
        check=False,
        text=True,
        capture_output=True,
    )


def _safe_trusted_read(
    path: Path,
    *,
    trusted_uid: int,
    maximum: int = MAX_SYSTEM_FILE_BYTES,
) -> bytes:
    if not path.is_absolute() or path.is_symlink():
        raise PlatformPreflightError(f"platform input is not one trusted absolute file: {path}")
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise PlatformPreflightError(f"platform input cannot be read: {path}") from exc
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != trusted_uid
            or stat.S_IMODE(info.st_mode) & 0o022
            or not 0 <= info.st_size <= maximum
        ):
            raise PlatformPreflightError(
                f"platform input has unsafe ownership, mode, or size: {path}"
            )
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(64 * 1024, maximum - total + 1))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum:
                raise PlatformPreflightError(f"platform input exceeds its limit: {path}")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _decode_utf8(data: bytes, *, label: str) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PlatformPreflightError(f"{label} must be UTF-8") from exc


def _parse_os_release(data: bytes) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in _decode_utf8(data, label="os-release").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise PlatformPreflightError("os-release contains an invalid line")
        key, encoded = line.split("=", 1)
        if re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", key) is None or key in values:
            raise PlatformPreflightError("os-release contains an invalid or duplicate key")
        try:
            decoded = shlex.split(encoded, comments=False, posix=True)
        except ValueError as exc:
            raise PlatformPreflightError("os-release contains an invalid value") from exc
        if len(decoded) != 1 or len(decoded[0]) > 255:
            raise PlatformPreflightError("os-release contains an invalid value")
        values[key] = decoded[0]
    return values


def _omv_major(version: str) -> int:
    normalized = version.strip()
    if (
        not normalized
        or len(normalized) > 128
        or any(character < " " for character in normalized)
        or _OMV_VERSION_PATTERN.fullmatch(normalized) is None
    ):
        raise PlatformPreflightError("installed openmediavault package version is invalid")
    match = _OMV_VERSION_PATTERN.fullmatch(normalized)
    assert match is not None
    return int(match.group(1))


def _json_contains_key(value: Any, expected: str) -> bool:
    if isinstance(value, dict):
        return expected in value or any(
            _json_contains_key(child, expected) for child in value.values()
        )
    if isinstance(value, list):
        return any(_json_contains_key(child, expected) for child in value)
    return False


def _active_netplan_nameservers(data: bytes, *, label: str) -> bool:
    text = _decode_utf8(data, label=label)
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if re.match(r"^\s*nameservers\s*:\s*(?:\{.*\})?\s*$", line):
            return True
    return False


def _netplan_files(paths: PlatformPaths, *, trusted_uid: int) -> list[Path]:
    directory = paths.netplan_directory
    if not directory.exists():
        if directory.is_symlink():
            raise PlatformPreflightError("Netplan configuration directory is a broken symlink")
        return []
    if not directory.is_absolute() or directory.is_symlink():
        raise PlatformPreflightError("Netplan configuration directory is unsafe")
    info = directory.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != trusted_uid
        or stat.S_IMODE(info.st_mode) & 0o022
    ):
        raise PlatformPreflightError("Netplan configuration directory has unsafe ownership or mode")
    candidates = sorted(
        {
            *directory.glob("*.yaml"),
            *directory.glob("*.yml"),
        },
        key=lambda path: path.name,
    )
    if len(candidates) > MAX_NETPLAN_FILES:
        raise PlatformPreflightError("too many Netplan configuration files to verify safely")
    return candidates


def probe_nas_readiness(
    *,
    paths: PlatformPaths | None = None,
    hostname: str | None = None,
    trusted_uid: int = 0,
) -> dict[str, Any]:
    """Check SMB identity and the known OMV 8 Netplan import compatibility."""
    selected = paths or PlatformPaths()
    raw_hostname = hostname if hostname is not None else socket.gethostname()
    short_hostname = raw_hostname.strip().split(".", 1)[0].casefold()
    hostname_valid = HOSTNAME_PATTERN.fullmatch(short_hostname) is not None
    smb_hostname_compatible = hostname_valid and len(short_hostname) <= MAX_HOSTNAME_LENGTH

    importer_text = _decode_utf8(
        _safe_trusted_read(selected.netplan_importer, trusted_uid=trusted_uid),
        label="OMV Netplan importer",
    )
    model_data = _safe_trusted_read(
        selected.network_interface_model,
        trusted_uid=trusted_uid,
    )
    try:
        model = json.loads(_decode_utf8(model_data, label="OMV network interface model"))
    except json.JSONDecodeError as exc:
        raise PlatformPreflightError("OMV network interface model is not valid JSON") from exc

    importer_fields = sorted(
        {match.group("field") for match in _SET_FIELD_PATTERN.finditer(importer_text)}
    )
    model_has_current_field = _json_contains_key(model, "dnsnameservers")
    model_has_legacy_field = _json_contains_key(model, "dnsservers")
    known_field_mismatch = (
        "dnsservers" in importer_fields
        and "dnsnameservers" not in importer_fields
        and model_has_current_field
        and not model_has_legacy_field
    )
    importer_model_agree = ("dnsnameservers" in importer_fields and model_has_current_field) or (
        "dnsservers" in importer_fields and model_has_legacy_field
    )
    if not known_field_mismatch and not importer_model_agree:
        raise PlatformPreflightError(
            "OMV Netplan importer and network model field compatibility cannot be proven"
        )

    netplan_files = _netplan_files(selected, trusted_uid=trusted_uid)
    active_nameservers: list[str] = []
    for path in netplan_files:
        data = _safe_trusted_read(path, trusted_uid=trusted_uid)
        if _active_netplan_nameservers(data, label=f"Netplan file {path.name}"):
            active_nameservers.append(path.name)

    active_field_mismatch = known_field_mismatch and bool(active_nameservers)
    issues: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    if not hostname_valid:
        issues.append(
            {
                "code": "hostname_invalid",
                "message": "The host name is not one safe DNS label.",
                "remediation": "Set a lowercase device name using letters, digits and internal hyphens.",
            }
        )
    elif not smb_hostname_compatible:
        issues.append(
            {
                "code": "smb_hostname_too_long",
                "message": "The device name exceeds the 15-character SMB/NetBIOS limit.",
                "remediation": "Choose a unique Echo device name containing at most 15 characters.",
            }
        )
    if active_field_mismatch:
        issues.append(
            {
                "code": "omv_netplan_dns_field_mismatch",
                "message": (
                    "This OMV Netplan importer writes dnsservers while the installed model "
                    "accepts dnsnameservers, and active Netplan DNS configuration is present."
                ),
                "remediation": (
                    "Install an upstream-fixed OMV package or apply the vendor-approved exact "
                    "version workaround before installing Echo; Echo will not patch OMV files."
                ),
            }
        )
    elif known_field_mismatch:
        warnings.append(
            {
                "code": "omv_netplan_dns_field_mismatch_latent",
                "message": (
                    "The installed OMV importer still contains the known DNS field mismatch, "
                    "but no active Netplan nameservers block was found."
                ),
                "remediation": "Upgrade to an upstream-fixed OMV package before adding Netplan DNS.",
            }
        )

    return {
        "ready": not issues,
        "hostname": short_hostname,
        "hostnameValid": hostname_valid,
        "smbHostnameCompatible": smb_hostname_compatible,
        "smbHostnameLimit": MAX_HOSTNAME_LENGTH,
        "netplan": {
            "configurationFiles": [path.name for path in netplan_files],
            "activeNameserverFiles": active_nameservers,
            "importerFields": importer_fields,
            "modelHasDnsnameservers": model_has_current_field,
            "modelHasDnsservers": model_has_legacy_field,
            "knownFieldMismatch": known_field_mismatch,
            "compatible": not active_field_mismatch,
        },
        "issues": issues,
        "warnings": warnings,
    }


def probe_platform(
    *,
    paths: PlatformPaths | None = None,
    hostname: str | None = None,
    trusted_uid: int = 0,
    command_runner: Callable[[list[str]], subprocess.CompletedProcess[str]] = _run,
) -> dict[str, Any]:
    selected = paths or PlatformPaths()
    release = _parse_os_release(_safe_trusted_read(selected.os_release, trusted_uid=trusted_uid))
    distribution = release.get("ID", "").casefold()
    distribution_version = release.get("VERSION_ID", "")
    if distribution != "debian" or distribution_version != SUPPORTED_DEBIAN_VERSION:
        raise PlatformPreflightError("unsupported host distribution: Echo NAS requires Debian 13")
    result = command_runner([str(selected.dpkg_query), "-W", "-f=${Version}", "openmediavault"])
    if result.returncode != 0 or not isinstance(result.stdout, str):
        raise PlatformPreflightError("openmediavault package version could not be queried")
    omv_version = result.stdout.strip()
    omv_major = _omv_major(omv_version)
    if omv_major != SUPPORTED_OMV_MAJOR:
        raise PlatformPreflightError("unsupported openmediavault version: Echo NAS requires OMV 8")
    readiness = probe_nas_readiness(
        paths=selected,
        hostname=hostname,
        trusted_uid=trusted_uid,
    )
    architecture = platform.machine().casefold()
    return {
        "schemaVersion": SCHEMA_VERSION,
        "supported": readiness["ready"],
        "distribution": distribution,
        "distributionVersion": distribution_version,
        "omvVersion": omv_version,
        "omvMajor": omv_major,
        "supportMatrix": SUPPORT_MATRIX,
        "architecture": architecture,
        **readiness,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="emit only errors; the exit status still enforces the complete preflight",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = probe_platform()
    except (OSError, PlatformPreflightError, subprocess.SubprocessError) as exc:
        print(f"Echo OMV platform preflight failed: {exc}", file=sys.stderr)
        return 1
    if not args.quiet:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if not report["ready"]:
        for issue in report["issues"]:
            print(
                f"Echo OMV platform preflight failed [{issue['code']}]: "
                f"{issue['message']} {issue['remediation']}",
                file=sys.stderr,
            )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
