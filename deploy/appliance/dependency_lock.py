#!/usr/bin/env python3
"""Create and verify hash-locked Python inputs for the Echo appliance image."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
PYTHON_VERSION = "3.12"
DEFAULT_EXTRAS = ("serve", "tracing", "web", "local-auth")
RUNTIME_PLATFORMS = {
    "linux/amd64": "x86_64-unknown-linux-gnu",
    "linux/arm64": "aarch64-unknown-linux-gnu",
}
PYPI_INDEX = "https://pypi.org/simple"
MAX_INPUT_BYTES = 2 * 1024 * 1024
PROJECT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
REQUIREMENT_NAME_PATTERN = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)")
PIN_PATTERN = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)"
    r"(?:\[[A-Za-z0-9,._-]+\])?==(?P<version>[^ ;\\]+)"
    r"(?:\s*;\s*.+)?\s+\\$"
)
HASH_PATTERN = re.compile(r"^    --hash=sha256:[0-9a-f]{64}(?: \\)?$")
UV_VERSION_PATTERN = re.compile(r"^uv (?P<version>[0-9]+\.[0-9]+\.[0-9]+)(?:\s|$)")
FORBIDDEN_LOCK_TEXT = (
    "--index-url",
    "--extra-index-url",
    "--find-links",
    "--trusted-host",
    "--no-index",
    "file:",
    "git+",
    "http://",
    "https://",
    " -e ",
)


class DependencyLockError(RuntimeError):
    """The appliance dependency locks are incomplete, mutable, or inconsistent."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _safe_read(path: Path, *, maximum: int = MAX_INPUT_BYTES) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise DependencyLockError(f"cannot safely read dependency input: {path}") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or not 1 <= info.st_size <= maximum:
            raise DependencyLockError(f"dependency input is not a bounded regular file: {path}")
        data = bytearray()
        while len(data) <= maximum:
            chunk = os.read(descriptor, min(1024 * 1024, maximum - len(data) + 1))
            if not chunk:
                break
            data.extend(chunk)
        if len(data) > maximum:
            raise DependencyLockError(f"dependency input exceeds its size limit: {path}")
        return bytes(data)
    finally:
        os.close(descriptor)


def _atomic_write(path: Path, data: bytes, *, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink() or not path.parent.is_dir() or path.is_symlink():
        raise DependencyLockError(f"dependency lock output path is unsafe: {path}")
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


def _load_project(path: Path, context: str) -> tuple[bytes, dict[str, Any]]:
    data = _safe_read(path)
    try:
        value = tomllib.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise DependencyLockError(f"{context} pyproject is invalid") from exc
    project = value.get("project")
    build_system = value.get("build-system")
    if not isinstance(project, dict) or not isinstance(build_system, dict):
        raise DependencyLockError(f"{context} pyproject lacks project/build-system metadata")
    name = project.get("name")
    dependencies = project.get("dependencies")
    optional = project.get("optional-dependencies", {})
    build_requires = build_system.get("requires")
    if (
        not isinstance(name, str)
        or PROJECT_NAME_PATTERN.fullmatch(name) is None
        or not isinstance(dependencies, list)
        or not isinstance(optional, dict)
        or not isinstance(build_requires, list)
    ):
        raise DependencyLockError(f"{context} pyproject dependency schema is invalid")
    return data, {
        "name": name,
        "dependencies": _requirements(dependencies, f"{context} project"),
        "optional": optional,
        "build": _requirements(build_requires, f"{context} build-system"),
        "raw": value,
    }


def _requirements(values: list[Any], context: str) -> list[str]:
    result: list[str] = []
    for value in values:
        if (
            not isinstance(value, str)
            or not 1 <= len(value) <= 512
            or "\n" in value
            or "\r" in value
            or value.startswith("-")
            or "@" in value
            or "://" in value
            or REQUIREMENT_NAME_PATTERN.match(value) is None
        ):
            raise DependencyLockError(f"{context} has an unsafe requirement")
        result.append(value)
    return result


def collect_inputs(
    os_project_path: Path,
    agent_project_path: Path,
    extras: tuple[str, ...],
) -> dict[str, Any]:
    os_data, os_project = _load_project(os_project_path, "Echo OS")
    agent_data, agent_project = _load_project(agent_project_path, "Echo Agent")
    optional = agent_project["optional"]
    runtime = [*os_project["dependencies"], *agent_project["dependencies"]]
    for extra in extras:
        values = optional.get(extra)
        if not isinstance(values, list):
            raise DependencyLockError(f"Echo Agent does not declare required extra {extra!r}")
        runtime.extend(_requirements(values, f"Echo Agent extra {extra}"))
    build = [*os_project["build"], *agent_project["build"]]
    tool = os_project["raw"].get("tool", {})
    uv_settings = tool.get("uv", {}) if isinstance(tool, dict) else {}
    required_uv = uv_settings.get("required-version") if isinstance(uv_settings, dict) else None
    if (
        not isinstance(required_uv, str)
        or re.fullmatch(r"==[0-9]+\.[0-9]+\.[0-9]+", required_uv) is None
    ):
        raise DependencyLockError("Echo OS must pin tool.uv.required-version exactly")
    return {
        "os": {
            "name": os_project["name"],
            "file": os_project_path.name,
            "sha256": _sha256(os_data),
        },
        "agent": {
            "name": agent_project["name"],
            "file": agent_project_path.name,
            "sha256": _sha256(agent_data),
        },
        "extras": list(extras),
        "uvVersion": required_uv.removeprefix("=="),
        "buildRequirements": sorted(set(build), key=str.casefold),
        "runtimeRequirements": sorted(set(runtime), key=str.casefold),
    }


def _direct_names(requirements: list[str]) -> set[str]:
    names: set[str] = set()
    for requirement in requirements:
        match = REQUIREMENT_NAME_PATTERN.match(requirement)
        assert match is not None
        names.add(_canonical_name(match.group("name")))
    return names


def validate_lock_bytes(
    data: bytes,
    *,
    context: str,
    required_names: set[str],
) -> dict[str, Any]:
    if not 1 <= len(data) <= MAX_INPUT_BYTES or not data.endswith(b"\n"):
        raise DependencyLockError(f"{context} lock is empty, oversized, or not newline terminated")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DependencyLockError(f"{context} lock is not UTF-8") from exc
    lowered = f" {text.lower()} "
    if any(fragment in lowered for fragment in FORBIDDEN_LOCK_TEXT):
        raise DependencyLockError(f"{context} lock contains a mutable package source")

    packages: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    current_hashes = 0
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        if line.startswith(" "):
            if current is None or HASH_PATTERN.fullmatch(line) is None:
                raise DependencyLockError(f"{context} lock has an unsafe continuation line")
            current_hashes += 1
            continue
        if current is not None and current_hashes == 0:
            raise DependencyLockError(f"{context} lock has a package without hashes")
        match = PIN_PATTERN.fullmatch(line)
        if match is None:
            raise DependencyLockError(f"{context} lock has an unpinned requirement")
        current = {"name": _canonical_name(match.group("name")), "version": match.group("version")}
        packages.append(current)
        current_hashes = 0
    if current is not None and current_hashes == 0:
        raise DependencyLockError(f"{context} lock has a package without hashes")
    if not packages:
        raise DependencyLockError(f"{context} lock contains no packages")
    locked_names = {package["name"] for package in packages}
    if len(locked_names) != len(packages):
        raise DependencyLockError(f"{context} lock repeats a package")
    missing = sorted(required_names - locked_names)
    if missing:
        raise DependencyLockError(f"{context} lock omits direct packages: {', '.join(missing)}")
    return {
        "sha256": _sha256(data),
        "packageCount": len(packages),
        "packages": packages,
    }


def _uv_version(uv_binary: str) -> str:
    try:
        result = subprocess.run(
            [uv_binary, "--version"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise DependencyLockError("cannot execute the pinned uv resolver") from exc
    match = UV_VERSION_PATTERN.match(result.stdout.strip())
    if match is None:
        raise DependencyLockError("uv returned an unrecognized version")
    return match.group("version")


def _resolver_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for name in tuple(environment):
        if name.startswith("UV_") or name.startswith("PIP_"):
            environment.pop(name)
    environment["UV_NO_CONFIG"] = "1"
    return environment


def _compile_lock(
    requirements: list[str],
    *,
    uv_binary: str,
    platform: str | None,
    constraint: Path | None,
) -> bytes:
    descriptor, output_name = tempfile.mkstemp(prefix="echo-python-lock-", suffix=".txt")
    os.close(descriptor)
    output = Path(output_name)
    command = [
        uv_binary,
        "pip",
        "compile",
        "-",
        "--python-version",
        PYTHON_VERSION,
        "--generate-hashes",
        "--no-annotate",
        "--no-header",
        "--only-binary",
        ":all:",
        "--default-index",
        PYPI_INDEX,
        "--index-strategy",
        "first-index",
        "--no-config",
        "--quiet",
        "--output-file",
        str(output),
    ]
    if platform is None:
        command.append("--universal")
    else:
        command.extend(("--python-platform", platform))
    if constraint is not None:
        command.extend(("--constraint", str(constraint)))
    try:
        subprocess.run(
            command,
            input="\n".join(requirements) + "\n",
            check=True,
            capture_output=True,
            text=True,
            env=_resolver_environment(),
        )
        return _safe_read(output)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "dependency resolution failed").strip()[-2000:]
        raise DependencyLockError(f"uv dependency resolution failed: {detail}") from exc
    finally:
        if output.exists():
            output.unlink()


def _metadata_bytes(
    inputs: dict[str, Any],
    *,
    build_lock_path: Path,
    build_report: dict[str, Any],
    runtime_lock_path: Path,
    runtime_report: dict[str, Any],
) -> bytes:
    value = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "echo-appliance-python-dependency-lock",
        "generator": {"name": "uv", "version": inputs["uvVersion"]},
        "pythonVersion": PYTHON_VERSION,
        "platforms": list(RUNTIME_PLATFORMS),
        "onlyBinary": True,
        "inputs": {
            "osProject": inputs["os"],
            "agentProject": inputs["agent"],
            "agentExtras": inputs["extras"],
            "buildRequirementsSha256": _sha256(
                ("\n".join(inputs["buildRequirements"]) + "\n").encode()
            ),
            "runtimeRequirementsSha256": _sha256(
                ("\n".join(inputs["runtimeRequirements"]) + "\n").encode()
            ),
        },
        "buildLock": {
            "file": build_lock_path.name,
            "sha256": build_report["sha256"],
            "packageCount": build_report["packageCount"],
        },
        "runtimeLock": {
            "file": runtime_lock_path.name,
            "sha256": runtime_report["sha256"],
            "packageCount": runtime_report["packageCount"],
        },
    }
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()


def _expected_metadata(
    inputs: dict[str, Any],
    build_lock_path: Path,
    build_data: bytes,
    runtime_lock_path: Path,
    runtime_data: bytes,
) -> tuple[bytes, dict[str, Any], dict[str, Any]]:
    build_report = validate_lock_bytes(
        build_data,
        context="build dependency",
        required_names=_direct_names(inputs["buildRequirements"]),
    )
    runtime_report = validate_lock_bytes(
        runtime_data,
        context="runtime dependency",
        required_names=_direct_names(inputs["runtimeRequirements"]),
    )
    metadata = _metadata_bytes(
        inputs,
        build_lock_path=build_lock_path,
        build_report=build_report,
        runtime_lock_path=runtime_lock_path,
        runtime_report=runtime_report,
    )
    return metadata, build_report, runtime_report


def refresh_locks(
    *,
    os_project_path: Path,
    agent_project_path: Path,
    extras: tuple[str, ...],
    build_lock_path: Path,
    runtime_lock_path: Path,
    metadata_path: Path,
    uv_binary: str,
) -> dict[str, Any]:
    inputs = collect_inputs(os_project_path, agent_project_path, extras)
    actual_uv = _uv_version(uv_binary)
    if actual_uv != inputs["uvVersion"]:
        raise DependencyLockError(f"uv {actual_uv} does not match required {inputs['uvVersion']}")
    build_data = _compile_lock(
        inputs["buildRequirements"], uv_binary=uv_binary, platform=None, constraint=None
    )
    runtime_results = [
        _compile_lock(
            inputs["runtimeRequirements"],
            uv_binary=uv_binary,
            platform=platform,
            constraint=None,
        )
        for platform in RUNTIME_PLATFORMS.values()
    ]
    if len(set(runtime_results)) != 1:
        raise DependencyLockError("amd64 and arm64 runtime dependency resolutions differ")
    runtime_data = runtime_results[0]
    metadata_data, build_report, runtime_report = _expected_metadata(
        inputs, build_lock_path, build_data, runtime_lock_path, runtime_data
    )
    _atomic_write(build_lock_path, build_data)
    _atomic_write(runtime_lock_path, runtime_data)
    _atomic_write(metadata_path, metadata_data)
    return {
        "verified": True,
        "refreshed": True,
        "uvVersion": actual_uv,
        "platforms": list(RUNTIME_PLATFORMS),
        "buildPackages": build_report["packageCount"],
        "runtimePackages": runtime_report["packageCount"],
        "metadataSha256": _sha256(metadata_data),
    }


def verify_locks(
    *,
    os_project_path: Path,
    agent_project_path: Path,
    extras: tuple[str, ...],
    build_lock_path: Path,
    runtime_lock_path: Path,
    metadata_path: Path,
    uv_binary: str,
    recompile: bool = True,
) -> dict[str, Any]:
    inputs = collect_inputs(os_project_path, agent_project_path, extras)
    actual_uv = _uv_version(uv_binary)
    if actual_uv != inputs["uvVersion"]:
        raise DependencyLockError(f"uv {actual_uv} does not match required {inputs['uvVersion']}")
    build_data = _safe_read(build_lock_path)
    runtime_data = _safe_read(runtime_lock_path)
    expected_metadata, build_report, runtime_report = _expected_metadata(
        inputs, build_lock_path, build_data, runtime_lock_path, runtime_data
    )
    if _safe_read(metadata_path) != expected_metadata:
        raise DependencyLockError("dependency lock metadata does not match source inputs")
    if recompile:
        rebuilt_build = _compile_lock(
            inputs["buildRequirements"],
            uv_binary=uv_binary,
            platform=None,
            constraint=build_lock_path,
        )
        if rebuilt_build != build_data:
            raise DependencyLockError("build dependency lock is not reproducible")
        for platform in RUNTIME_PLATFORMS.values():
            rebuilt_runtime = _compile_lock(
                inputs["runtimeRequirements"],
                uv_binary=uv_binary,
                platform=platform,
                constraint=runtime_lock_path,
            )
            if rebuilt_runtime != runtime_data:
                raise DependencyLockError("runtime dependency lock is not reproducible")
    return {
        "verified": True,
        "refreshed": False,
        "uvVersion": actual_uv,
        "platforms": list(RUNTIME_PLATFORMS),
        "buildPackages": build_report["packageCount"],
        "runtimePackages": runtime_report["packageCount"],
        "metadataSha256": _sha256(expected_metadata),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("refresh", "verify"))
    parser.add_argument("--os-project", type=Path, default=Path("pyproject.toml"))
    parser.add_argument("--agent-project", type=Path, required=True)
    parser.add_argument("--extras", default=",".join(DEFAULT_EXTRAS))
    parser.add_argument(
        "--build-lock",
        type=Path,
        default=Path("deploy/appliance/build-requirements.lock"),
    )
    parser.add_argument(
        "--runtime-lock",
        type=Path,
        default=Path("deploy/appliance/runtime-requirements.lock"),
    )
    parser.add_argument(
        "--metadata",
        type=Path,
        default=Path("deploy/appliance/python-dependency-lock.json"),
    )
    parser.add_argument("--uv", default="uv")
    parser.add_argument("--no-recompile", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    extras = tuple(item.strip() for item in args.extras.split(",") if item.strip())
    try:
        options = {
            "os_project_path": args.os_project,
            "agent_project_path": args.agent_project,
            "extras": extras,
            "build_lock_path": args.build_lock,
            "runtime_lock_path": args.runtime_lock,
            "metadata_path": args.metadata,
            "uv_binary": args.uv,
        }
        if args.command == "refresh":
            if args.no_recompile:
                raise DependencyLockError("--no-recompile is only valid with verify")
            report = refresh_locks(**options)
        else:
            report = verify_locks(**options, recompile=not args.no_recompile)
    except (DependencyLockError, OSError) as exc:
        print(f"Echo dependency lock failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
