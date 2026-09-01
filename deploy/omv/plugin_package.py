#!/usr/bin/env python3
"""Build and verify the deterministic OpenMediaVault Echo OS plugin package."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import stat
import sys
import tarfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

PACKAGE_NAME = "openmediavault-echo-os"
PACKAGE_ARCHITECTURE = "all"
PLUGIN_ARCHITECTURES = ("amd64", "arm64")
SUPPORT_MATRIX = "debian-13+omv-8"
MAX_SOURCE_BYTES = 8 * 1024 * 1024
MAX_PACKAGE_BYTES = 32 * 1024 * 1024
MAX_TAR_MEMBERS = 32
AR_MAGIC = b"!<arch>\n"
VERSION_PATTERN = re.compile(r"^[0-9][0-9A-Za-z.+~:-]*-[0-9][0-9A-Za-z.+~]*$")

PLUGIN_ASSETS = {
    "control.in": 0o644,
    "preinst": 0o755,
    "postinst": 0o755,
    "prerm": 0o755,
    "postrm": 0o755,
    "triggers": 0o644,
    "workbench/navigation.yaml": 0o644,
    "workbench/route.yaml": 0o644,
    "workbench/component.yaml": 0o644,
    "copyright": 0o644,
}

DATA_SOURCES = {
    "usr/lib/echo-os/omv-bridge/appliance/__init__.py": "appliance/__init__.py",
    "usr/lib/echo-os/omv-bridge/appliance/omv_bridge.py": "appliance/omv_bridge.py",
    "usr/lib/echo-os/omv-bridge/platform_preflight.py": ("deploy/omv/platform_preflight.py"),
    "usr/lib/systemd/system/echo-omv-bridge.service": (
        "deploy/omv/echo-omv-bridge.service.example"
    ),
    "usr/share/doc/openmediavault-echo-os/README.md": "deploy/omv/README.md",
}

WORKBENCH_SOURCES = {
    "usr/share/openmediavault/workbench/navigation.d/services.echo-os.yaml": (
        "workbench/navigation.yaml"
    ),
    "usr/share/openmediavault/workbench/route.d/services.echo-os.yaml": ("workbench/route.yaml"),
    "usr/share/openmediavault/workbench/component.d/omv-services-echo-os-form-page.yaml": (
        "workbench/component.yaml"
    ),
    "usr/share/doc/openmediavault-echo-os/copyright": "copyright",
}


class PluginPackageError(RuntimeError):
    """The native OMV plugin package could not be built or verified safely."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_read(path: Path, *, maximum: int = MAX_SOURCE_BYTES) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise PluginPackageError(f"cannot safely read plugin input: {path}") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or not 0 <= info.st_size <= maximum:
            raise PluginPackageError(f"plugin input is not a bounded regular file: {path}")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, maximum - total + 1))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum:
                raise PluginPackageError(f"plugin input exceeds its size limit: {path}")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _plugin_root(source_root: Path) -> Path:
    return source_root / "deploy/omv/plugin"


def _validated_version(value: str) -> str:
    normalized = value.strip()
    if len(normalized) > 128 or VERSION_PATTERN.fullmatch(normalized) is None:
        raise PluginPackageError(f"invalid Debian plugin version: {value!r}")
    return normalized


def _default_version(source_root: Path) -> str:
    try:
        package = json.loads(_safe_read(source_root / "frontend/package.json").decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PluginPackageError("frontend package version is unavailable") from exc
    version = package.get("version") if isinstance(package, dict) else None
    if not isinstance(version, str):
        raise PluginPackageError("frontend package version is invalid")
    return _validated_version(f"{version}-1")


def _asset_payload(source_root: Path) -> dict[str, bytes]:
    root = _plugin_root(source_root)
    return {relative: _safe_read(root / relative) for relative in sorted(PLUGIN_ASSETS)}


def _data_payload(source_root: Path, assets: dict[str, bytes]) -> dict[str, tuple[bytes, int]]:
    files = {
        destination: (_safe_read(source_root / source), 0o644)
        for destination, source in DATA_SOURCES.items()
    }
    for destination, source in WORKBENCH_SOURCES.items():
        files[destination] = (assets[source], 0o644)
    return dict(sorted(files.items()))


def _installed_size(files: dict[str, tuple[bytes, int]]) -> int:
    return max(1, sum((len(data) + 1023) // 1024 for data, _mode in files.values()))


def _render_control(template: bytes, *, version: str, installed_size: int) -> bytes:
    try:
        text = template.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PluginPackageError("plugin control template must be UTF-8") from exc
    if text.count("@VERSION@") != 1 or text.count("@INSTALLED_SIZE@") != 1:
        raise PluginPackageError("plugin control template placeholders are invalid")
    rendered = text.replace("@VERSION@", version).replace("@INSTALLED_SIZE@", str(installed_size))
    return rendered.encode("utf-8")


def _md5sums(files: dict[str, tuple[bytes, int]]) -> bytes:
    return "".join(
        f"{hashlib.md5(data, usedforsecurity=False).hexdigest()}  {path}\n"  # nosec B324
        for path, (data, _mode) in sorted(files.items())
    ).encode("ascii")


def _control_payload(
    source_root: Path,
    assets: dict[str, bytes],
    data_files: dict[str, tuple[bytes, int]],
    *,
    version: str,
) -> dict[str, tuple[bytes, int]]:
    return {
        "control": (
            _render_control(
                assets["control.in"],
                version=version,
                installed_size=_installed_size(data_files),
            ),
            0o644,
        ),
        "md5sums": (_md5sums(data_files), 0o644),
        "postinst": (assets["postinst"], 0o755),
        "postrm": (assets["postrm"], 0o755),
        "preinst": (assets["preinst"], 0o755),
        "prerm": (assets["prerm"], 0o755),
        "triggers": (assets["triggers"], 0o644),
    }


def _parent_directories(paths: Any) -> set[str]:
    directories: set[str] = set()
    for path in paths:
        normalized = PurePosixPath(path)
        directories.update(
            parent.as_posix() for parent in normalized.parents if parent.as_posix() != "."
        )
    return directories


def _tar_gz(files: dict[str, tuple[bytes, int]], *, include_directories: bool = False) -> bytes:
    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        directories = _parent_directories(files) if include_directories else set()
        entries = sorted(directories | set(files))
        for path in entries:
            normalized = PurePosixPath(path)
            if normalized.is_absolute() or ".." in normalized.parts or not normalized.parts:
                raise PluginPackageError(f"unsafe plugin archive path: {path}")
            is_directory = path in directories
            archive_path = f"./{normalized.as_posix()}{'/' if is_directory else ''}"
            info = tarfile.TarInfo(archive_path)
            info.type = tarfile.DIRTYPE if is_directory else tarfile.REGTYPE
            data, mode = (b"", 0o755) if is_directory else files[path]
            info.mode = mode
            info.uid = 0
            info.gid = 0
            info.uname = "root"
            info.gname = "root"
            info.mtime = 0
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    compressed = io.BytesIO()
    with gzip.GzipFile(fileobj=compressed, mode="wb", filename="", mtime=0) as output:
        output.write(tar_buffer.getvalue())
    return compressed.getvalue()


def _ar_header(name: str, size: int) -> bytes:
    if not 1 <= len(name) <= 15 or size < 0:
        raise PluginPackageError("invalid Debian ar member")
    fields = f"{name + '/':<16}{0:<12}{0:<6}{0:<6}{0o100644:<8o}{size:<10}`\n"
    encoded = fields.encode("ascii")
    if len(encoded) != 60:
        raise PluginPackageError("invalid Debian ar header")
    return encoded


def _deb(control_tar: bytes, data_tar: bytes) -> bytes:
    output = bytearray(AR_MAGIC)
    for name, data in (
        ("debian-binary", b"2.0\n"),
        ("control.tar.gz", control_tar),
        ("data.tar.gz", data_tar),
    ):
        output.extend(_ar_header(name, len(data)))
        output.extend(data)
        if len(data) % 2:
            output.extend(b"\n")
    return bytes(output)


def _read_ar(payload: bytes) -> dict[str, bytes]:
    if len(payload) > MAX_PACKAGE_BYTES or not payload.startswith(AR_MAGIC):
        raise PluginPackageError("native OMV plugin is not a bounded Debian archive")
    offset = len(AR_MAGIC)
    members: dict[str, bytes] = {}
    while offset < len(payload):
        if offset + 60 > len(payload):
            raise PluginPackageError("native OMV plugin has a truncated ar header")
        header = payload[offset : offset + 60]
        offset += 60
        if header[58:60] != b"`\n":
            raise PluginPackageError("native OMV plugin has an invalid ar header")
        try:
            name = header[:16].decode("ascii").strip().removesuffix("/")
            size = int(header[48:58].decode("ascii").strip())
        except (UnicodeDecodeError, ValueError) as exc:
            raise PluginPackageError("native OMV plugin ar metadata is invalid") from exc
        if not name or name in members or size < 0 or offset + size > len(payload):
            raise PluginPackageError("native OMV plugin ar member is invalid")
        members[name] = payload[offset : offset + size]
        offset += size
        if size % 2:
            if offset >= len(payload) or payload[offset : offset + 1] != b"\n":
                raise PluginPackageError("native OMV plugin ar padding is invalid")
            offset += 1
    if list(members) != ["debian-binary", "control.tar.gz", "data.tar.gz"]:
        raise PluginPackageError("native OMV plugin ar member set is invalid")
    if members["debian-binary"] != b"2.0\n":
        raise PluginPackageError("native OMV plugin Debian format version is invalid")
    return members


def _read_tar_gz(
    payload: bytes, *, context: str, include_directories: bool = False
) -> dict[str, tuple[bytes, int]]:
    files: dict[str, tuple[bytes, int]] = {}
    directories: set[str] = set()
    try:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
            members = archive.getmembers()
            if not 1 <= len(members) <= MAX_TAR_MEMBERS:
                raise PluginPackageError(f"{context} has an unsafe member count")
            for member in members:
                path = member.name.removeprefix("./").removesuffix("/")
                normalized = PurePosixPath(path)
                if (
                    normalized.is_absolute()
                    or ".." in normalized.parts
                    or not normalized.parts
                    or path in files
                    or path in directories
                    or member.uid != 0
                    or member.gid != 0
                    or member.mtime != 0
                ):
                    raise PluginPackageError(f"{context} contains an unsafe member")
                if member.isdir():
                    if (
                        not include_directories
                        or stat.S_IMODE(member.mode) != 0o755
                        or member.size != 0
                    ):
                        raise PluginPackageError(f"{context} contains an unsafe directory")
                    directories.add(path)
                    continue
                if not member.isfile():
                    raise PluginPackageError(f"{context} contains an unsafe member")
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise PluginPackageError(f"{context} member cannot be read")
                data = extracted.read(MAX_SOURCE_BYTES + 1)
                if len(data) > MAX_SOURCE_BYTES:
                    raise PluginPackageError(f"{context} member is oversized")
                files[path] = (data, stat.S_IMODE(member.mode))
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise PluginPackageError(f"{context} is not a valid gzip tar archive") from exc
    expected_directories = _parent_directories(files) if include_directories else set()
    if directories != expected_directories:
        raise PluginPackageError(f"{context} directory inventory is invalid")
    return files


def _parse_control(data: bytes) -> dict[str, str]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PluginPackageError("native OMV plugin control is not UTF-8") from exc
    fields: dict[str, str] = {}
    current: str | None = None
    for line in text.splitlines():
        if line.startswith(" ") and current is not None:
            fields[current] += "\n" + line[1:]
            continue
        if ": " not in line:
            raise PluginPackageError("native OMV plugin control line is invalid")
        key, value = line.split(": ", 1)
        if key in fields or re.fullmatch(r"[A-Za-z][A-Za-z0-9-]*", key) is None:
            raise PluginPackageError("native OMV plugin control field is invalid")
        fields[key] = value
        current = key
    required = {
        "Package": PACKAGE_NAME,
        "Architecture": PACKAGE_ARCHITECTURE,
        "XB-Plugin-Section": "utilities",
        "XB-Plugin-Architecture": "amd64, arm64",
    }
    if any(fields.get(key) != value for key, value in required.items()):
        raise PluginPackageError("native OMV plugin identity fields are invalid")
    version = fields.get("Version", "")
    _validated_version(version)
    depends = fields.get("Depends", "")
    for dependency in (
        "openmediavault (>= 8.0)",
        "openmediavault (<< 9.0)",
        "adduser",
        "dpkg",
        "init-system-helpers",
        "python3",
        "systemd",
        "util-linux",
    ):
        if dependency not in depends.split(", "):
            raise PluginPackageError("native OMV plugin dependency boundary is invalid")
    return fields


def _spdx(data_files: dict[str, tuple[bytes, int]], version: str) -> bytes:
    created = (
        datetime.fromtimestamp(int(os.environ.get("SOURCE_DATE_EPOCH", "0")), tz=UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )
    files = [
        {
            "fileName": f"./{path}",
            "SPDXID": f"SPDXRef-File-{hashlib.sha256(path.encode()).hexdigest()[:16]}",
            "checksums": [{"algorithm": "SHA256", "checksumValue": _sha256(data)}],
            "licenseConcluded": "Apache-2.0",
            "copyrightText": "NOASSERTION",
        }
        for path, (data, _mode) in sorted(data_files.items())
    ]
    package_id = "SPDXRef-Package-OpenMediaVaultEchoOS"
    value = {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"{PACKAGE_NAME}-{version}",
        "documentNamespace": f"https://echo-age.com/spdx/{PACKAGE_NAME}/{version}",
        "creationInfo": {"created": created, "creators": ["Tool: Echo-OMV-plugin-package/1"]},
        "documentDescribes": [package_id],
        "packages": [
            {
                "name": PACKAGE_NAME,
                "SPDXID": package_id,
                "versionInfo": version,
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": True,
                "licenseConcluded": "Apache-2.0",
                "licenseDeclared": "Apache-2.0",
                "copyrightText": "NOASSERTION",
                "primaryPackagePurpose": "APPLICATION",
                "comment": f"OpenMediaVault plugin for {SUPPORT_MATRIX}",
            }
        ],
        "files": files,
        "relationships": [
            {
                "spdxElementId": package_id,
                "relationshipType": "CONTAINS",
                "relatedSpdxElement": item["SPDXID"],
            }
            for item in files
        ],
    }
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def verify(package_path: Path, *, source_root: Path | None = None) -> dict[str, Any]:
    package_bytes = _safe_read(package_path, maximum=MAX_PACKAGE_BYTES)
    members = _read_ar(package_bytes)
    control_files = _read_tar_gz(members["control.tar.gz"], context="control archive")
    data_files = _read_tar_gz(
        members["data.tar.gz"], context="data archive", include_directories=True
    )
    expected_control_names = {
        "control",
        "md5sums",
        "postinst",
        "postrm",
        "preinst",
        "prerm",
        "triggers",
    }
    if set(control_files) != expected_control_names:
        raise PluginPackageError("native OMV plugin control file set is invalid")
    if any(
        control_files[name][1]
        != (0o755 if name in {"postinst", "postrm", "preinst", "prerm"} else 0o644)
        for name in expected_control_names
    ):
        raise PluginPackageError("native OMV plugin control file mode is invalid")
    fields = _parse_control(control_files["control"][0])
    expected_data_names = set(DATA_SOURCES) | set(WORKBENCH_SOURCES)
    if set(data_files) != expected_data_names or any(
        mode != 0o644 for _data, mode in data_files.values()
    ):
        raise PluginPackageError("native OMV plugin data file set or mode is invalid")
    if control_files["triggers"][0] != b"activate restart-engined\n":
        raise PluginPackageError("native OMV plugin trigger is invalid")
    expected_md5 = _md5sums(data_files)
    if control_files["md5sums"][0] != expected_md5:
        raise PluginPackageError("native OMV plugin md5 inventory is invalid")
    forbidden_script_markers = (
        b"rm -rf",
        b"\nrm ",
        b"groupdel",
        b"curl ",
        b"wget ",
        b"eval ",
        b"/var/run/docker.sock",
        b"ECHO_NAS_ROOT",
        b"/srv/dev-disk-by-",
    )
    for name in ("preinst", "postinst", "prerm", "postrm"):
        script = control_files[name][0]
        if not script.startswith(b"#!/usr/bin/env dash\nset -eu\n") or any(
            marker in script for marker in forbidden_script_markers
        ):
            raise PluginPackageError(f"native OMV plugin {name} policy is unsafe")
    preinst = control_files["preinst"][0]
    postinst = control_files["postinst"][0]
    postrm = control_files["postrm"][0]
    if not all(
        marker in preinst
        for marker in (
            b"/usr/lib/os-release",
            b"/usr/bin/dpkg-query",
            b"supports only Debian 13 with OpenMediaVault 8",
            b"/var/lib/echo-os/omv-host/install-state.json",
            b"/etc/systemd/system/echo-omv-bridge.service",
            b"Refusing to overwrite",
        )
    ):
        raise PluginPackageError("native OMV plugin manual-install conflict guard is missing")
    if not all(
        marker in postinst
        for marker in (
            b"platform_preflight.py --quiet",
            b"addgroup --system echo-omv",
            b"dpkg-trigger update-workbench",
            b"deb-systemd-invoke restart",
            b"/run/echo-omv/omv.sock",
            b"bridge failed its post-install health check",
        )
    ):
        raise PluginPackageError("native OMV plugin service activation is incomplete")
    if b"Keep the echo-omv group" not in postrm:
        raise PluginPackageError("native OMV plugin removal preservation policy is missing")
    unit = data_files["usr/lib/systemd/system/echo-omv-bridge.service"][0]
    preflight = data_files["usr/lib/echo-os/omv-bridge/platform_preflight.py"][0]
    if not all(
        marker in preflight
        for marker in (
            b"MAX_HOSTNAME_LENGTH = 15",
            b"omv_netplan_dns_field_mismatch",
            b"Echo will not patch OMV files",
        )
    ):
        raise PluginPackageError("native OMV plugin platform preflight is incomplete")
    if (
        b"ConditionFileIsExecutable=/usr/sbin/omv-rpc" not in unit
        or b"ConditionFileIsExecutable=/usr/bin/lsblk" not in unit
        or b"PrivateNetwork=true" not in unit
        or b"CapabilityBoundingSet=" not in unit
        or b"Group=echo-omv" not in unit
        or b"/run/echo-omv/omv.sock" not in unit
        or b"/opt/echo-os" in unit
    ):
        raise PluginPackageError("native OMV plugin systemd unit boundary is invalid")
    if source_root is not None:
        assets = _asset_payload(source_root)
        expected_data = _data_payload(source_root, assets)
        expected_control = _control_payload(
            source_root,
            assets,
            expected_data,
            version=fields["Version"],
        )
        if data_files != expected_data or control_files != expected_control:
            raise PluginPackageError("native OMV plugin does not match the current source tree")
    return {
        "package": PACKAGE_NAME,
        "version": fields["Version"],
        "architecture": PACKAGE_ARCHITECTURE,
        "pluginArchitectures": list(PLUGIN_ARCHITECTURES),
        "supportMatrix": SUPPORT_MATRIX,
        "sha256": _sha256(package_bytes),
        "size": len(package_bytes),
        "dataFileCount": len(data_files),
        "dataDirectoryCount": len(_parent_directories(data_files)),
        "supportMatrixInstallGate": True,
        "manualInstallerConflictGuard": True,
        "preservesNasDataOnRemoval": True,
    }


def build(
    source_root: Path, output_directory: Path, *, version: str | None = None
) -> dict[str, Any]:
    normalized_version = (
        _validated_version(version) if version is not None else _default_version(source_root)
    )
    assets = _asset_payload(source_root)
    data_files = _data_payload(source_root, assets)
    control_files = _control_payload(
        source_root,
        assets,
        data_files,
        version=normalized_version,
    )
    package_bytes = _deb(_tar_gz(control_files), _tar_gz(data_files, include_directories=True))
    output_directory.mkdir(parents=True, exist_ok=True)
    package_path = output_directory / f"{PACKAGE_NAME}_{normalized_version}_all.deb"
    package_path.write_bytes(package_bytes)
    package_path.chmod(0o644)
    checksum_path = package_path.with_suffix(package_path.suffix + ".sha256")
    checksum_path.write_text(f"{_sha256(package_bytes)}  {package_path.name}\n")
    sbom_path = output_directory / f"{PACKAGE_NAME}_{normalized_version}_all.spdx.json"
    sbom_path.write_bytes(_spdx(data_files, normalized_version))
    report = verify(package_path, source_root=source_root)
    return {
        **report,
        "path": str(package_path),
        "checksumPath": str(checksum_path),
        "sbomPath": str(sbom_path),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("build", "verify"))
    parser.add_argument("package", nargs="?", type=Path)
    parser.add_argument(
        "--source-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    parser.add_argument("--output-directory", type=Path, default=Path("dist"))
    parser.add_argument("--version")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.action == "build":
            if args.package is not None:
                raise PluginPackageError("build does not accept a package path")
            result = build(
                args.source_root.resolve(),
                args.output_directory.resolve(),
                version=args.version,
            )
        else:
            if args.package is None:
                raise PluginPackageError("verify requires a package path")
            result = verify(args.package.resolve(), source_root=args.source_root.resolve())
    except (PluginPackageError, OSError, tarfile.TarError) as exc:
        print(f"Echo OMV plugin package operation failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
