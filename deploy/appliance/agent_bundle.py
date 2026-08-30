#!/usr/bin/env python3
"""Build provenance and integrity gate for the Echo Agent appliance bundle.

The appliance image consumes four independently built surfaces from the same
Agent checkout: the Python wheel, web UI, deployment resources, and pinned
Codex engine. This tool gives them one source identity, records content hashes,
and rejects a mixed bundle before an image can appear falsely healthy.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import re
import runpy
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
import uuid
from email.parser import Parser
from pathlib import Path, PurePosixPath
from typing import Any
from zipfile import ZipFile

SCHEMA_VERSION = 2
PARTIAL_MANIFEST = "agent-build.json"
DEFAULT_MANIFEST = "agent-bundle.json"
BUILD_DEPENDENCY_LOCK = "build-requirements.lock"
RUNTIME_DEPENDENCY_LOCK = "runtime-requirements.lock"
DEPENDENCY_LOCK_METADATA = "python-dependency-lock.json"
DEPENDENCY_LOCK_PLATFORMS = ["linux/amd64", "linux/arm64"]
DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
RESOURCE_INPUTS = (
    "agents",
    "skills",
    "prompts",
    "protocols",
    "resources",
    "teams",
    "extensions",
    ".echo/plugins",
    "skills.lock.json",
    "config.example.yaml",
)
GENERATED_BUNDLE_OUTPUTS = frozenset(
    {
        "deploy/appliance/agent-bundle.json",
        "deploy/appliance/agent-codex",
        "deploy/appliance/agent-dist",
        "deploy/appliance/agent-resources",
        "deploy/appliance/agent-webui",
    }
)


def _is_runtime_only_path(relative: str) -> bool:
    """Return whether a source-relative path is mutable local Agent state."""

    pure = PurePosixPath(relative)
    parts = pure.parts
    if not parts:
        return False
    if parts[0] == ".echo-home":
        return True
    if parts[0] == ".echo":
        return len(parts) < 2 or parts[1] != "plugins"
    if len(parts) < 4 or parts[0] != "agents" or parts[2] != "agent-core":
        return False
    if parts[3] in {"sessions", "tenants", "workspace"}:
        return True
    name = pure.name
    return name == ".scores.jsonl" or name.endswith(".transaction.lock")


def _is_ephemeral_generated_path(relative: str) -> bool:
    """Return whether a path is local cache output rather than product source."""

    pure = PurePosixPath(relative)
    if not pure.parts:
        return False
    return (
        pure.parts[0] == ".echo-tmp"
        or "__pycache__" in pure.parts
        or pure.suffix in {".pyc", ".pyo"}
    )


def _is_generated_bundle_output_path(relative: str) -> bool:
    pure = PurePosixPath(relative)
    return any(
        pure == PurePosixPath(output) or PurePosixPath(output) in pure.parents
        for output in GENERATED_BUNDLE_OUTPUTS
    )


def _source_paths(raw: bytes) -> list[str]:
    return [
        path
        for path in _nul_paths(raw)
        if not _is_runtime_only_path(path)
        and not _is_ephemeral_generated_path(path)
        and not _is_generated_bundle_output_path(path)
    ]


class BundleError(RuntimeError):
    """The Agent bundle cannot be proven internally consistent."""


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temp.write_bytes(_json_bytes(value))
    os.replace(temp, path)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BundleError(f"invalid or missing JSON: {path}") from exc
    if not isinstance(value, dict):
        raise BundleError(f"JSON object required: {path}")
    return value


def _run_git(source: Path, *args: str) -> bytes:
    try:
        return subprocess.run(
            ["git", "-C", str(source), *args],
            check=True,
            capture_output=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise BundleError(f"git {' '.join(args)} failed for {source}") from exc


def _sha256(path: Path) -> str:
    if not path.is_file():
        raise BundleError(f"artifact file missing: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _normalize_tree_mtime(root: Path, epoch: int) -> None:
    paths = [root, *root.rglob("*")] if root.is_dir() else [root]
    for path in reversed(paths):
        try:
            os.utime(path, (epoch, epoch), follow_symlinks=False)
        except (FileNotFoundError, NotImplementedError):
            continue


def _path_state_digest(root: Path, paths: list[str], *, seed: bytes) -> str:
    digest = hashlib.sha256(seed)
    for rel in sorted(set(paths)):
        path = root / rel
        digest.update(rel.encode("utf-8", "surrogateescape"))
        digest.update(b"\0")
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            digest.update(b"missing\0")
            continue
        digest.update(f"{stat.S_IMODE(mode):04o}".encode())
        digest.update(b"\0")
        if path.is_symlink():
            digest.update(b"symlink\0")
            digest.update(os.readlink(path).encode("utf-8", "surrogateescape"))
        elif path.is_file():
            digest.update(b"file\0")
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        else:
            digest.update(b"other\0")
        digest.update(b"\0")
    return digest.hexdigest()


def _nul_paths(raw: bytes) -> list[str]:
    return [os.fsdecode(item) for item in raw.split(b"\0") if item]


def _tracked_paths_with_worktree_eol_variants(source: Path) -> list[str]:
    """Return clean tracked paths whose checkout bytes differ by line endings."""
    variants: list[str] = []
    for record in _run_git(source, "ls-files", "--eol", "-z").split(b"\0"):
        if not record:
            continue
        try:
            metadata, raw_path = record.split(b"\t", 1)
        except ValueError as exc:
            raise BundleError("invalid git ls-files --eol record") from exc
        fields = metadata.split()
        if len(fields) < 2 or not fields[0].startswith(b"i/") or not fields[1].startswith(b"w/"):
            raise BundleError("invalid git ls-files --eol metadata")
        if fields[0][2:] != fields[1][2:]:
            variants.append(os.fsdecode(raw_path))
    return variants


def source_identity(agent_src: Path) -> dict[str, Any]:
    source = agent_src.expanduser().resolve()
    pyproject_path = source / "pyproject.toml"
    frontend_path = source / "frontend" / "package.json"
    if not pyproject_path.is_file() or not frontend_path.is_file():
        raise BundleError(f"not an Echo Agent checkout: {source}")

    pyproject = tomllib.loads(pyproject_path.read_text())
    frontend = json.loads(frontend_path.read_text())
    project = pyproject.get("project") or {}
    distribution = str(project.get("name") or "").strip()
    version = str(project.get("version") or "").strip()
    dev_dependencies = frontend.get("devDependencies") or {}
    if not isinstance(dev_dependencies, dict):
        raise BundleError("Agent frontend devDependencies must be an object")
    packaged_codex_version = str(dev_dependencies.get("@openai/codex") or "").strip() or None
    if not all((distribution, version)):
        raise BundleError("Agent Python package identity is incomplete")

    commit = _run_git(source, "rev-parse", "HEAD").decode().strip()
    commit_time = _run_git(source, "show", "-s", "--format=%cI", "HEAD").decode().strip()
    source_date_epoch = int(_run_git(source, "show", "-s", "--format=%ct", "HEAD").decode().strip())
    changed = _source_paths(_run_git(source, "diff", "--name-only", "-z", "HEAD", "--"))
    untracked = _source_paths(_run_git(source, "ls-files", "--others", "--exclude-standard", "-z"))
    snapshot_paths = sorted(set(changed + untracked))
    fingerprint = _path_state_digest(source, snapshot_paths, seed=commit.encode())
    dirty = bool(snapshot_paths)
    source_id = f"{commit}+dirty.{fingerprint[:16]}" if dirty else commit
    try:
        repository = _run_git(source, "remote", "get-url", "origin").decode().strip()
    except BundleError:
        repository = ""

    return {
        "repository": repository,
        "commit": commit,
        "commit_time": commit_time,
        "source_date_epoch": source_date_epoch,
        "dirty": dirty,
        "changed_file_count": len(snapshot_paths),
        "snapshot_paths": snapshot_paths,
        "fingerprint": fingerprint,
        "source_id": source_id,
        "python_distribution": distribution,
        "python_version": version,
        "packaged_codex_version": packaged_codex_version,
    }


def capture_source(agent_src: Path, output: Path, *, allow_dirty: bool) -> dict[str, Any]:
    identity = source_identity(agent_src)
    if identity["dirty"] and not allow_dirty:
        raise BundleError(
            "Agent checkout is dirty; commit the release source or set "
            "ECHO_AGENT_ALLOW_DIRTY=1 for a clearly marked local QA bundle"
        )
    _write_json(output, identity)
    return identity


def _verify_source(agent_src: Path, identity_path: Path) -> dict[str, Any]:
    expected = _read_json(identity_path)
    actual = source_identity(agent_src)
    if actual != expected:
        raise BundleError(
            "Agent source changed while bundle artifacts were being built: "
            f"expected {expected.get('source_id')}, got {actual.get('source_id')}"
        )
    return expected


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def snapshot_source(agent_src: Path, identity_path: Path, destination: Path) -> dict[str, Any]:
    """Freeze an already captured dirty worktree without pausing its live checkout."""
    source = agent_src.expanduser().resolve()
    expected = _read_json(identity_path)
    paths = expected.get("snapshot_paths")
    if not isinstance(paths, list) or not all(isinstance(item, str) for item in paths):
        raise BundleError("captured Agent identity has no snapshot path list")
    if destination.exists():
        raise BundleError(f"snapshot destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)

    try:
        subprocess.run(
            [
                "git",
                "clone",
                "--quiet",
                "--shared",
                "--no-checkout",
                str(source),
                str(destination),
            ],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(destination),
                "checkout",
                "--quiet",
                "--detach",
                str(expected.get("commit") or ""),
            ],
            check=True,
        )
        repository = str(expected.get("repository") or "")
        if repository:
            _run_git(destination, "remote", "set-url", "origin", repository)
        else:
            _run_git(destination, "remote", "remove", "origin")

        # The detached checkout already contains every clean tracked file at
        # the captured commit. Overlay only dirty/untracked paths so a large
        # checkout can be frozen before an actively edited file changes again.
        # Clean files with transformed/mixed working-tree line endings must
        # also be overlaid: Git considers their normalized content clean, but
        # byte-pinned license checks intentionally inspect the raw file. A
        # dirty .gitattributes remains the conservative full-copy exception.
        copy_paths = set(paths)
        copy_paths.update(_tracked_paths_with_worktree_eol_variants(source))
        if any(PurePosixPath(rel).name == ".gitattributes" for rel in paths):
            copy_paths.update(_nul_paths(_run_git(source, "ls-files", "-z")))
        for rel in sorted(copy_paths):
            pure = PurePosixPath(rel)
            if pure.is_absolute() or ".." in pure.parts:
                raise BundleError(f"unsafe Agent snapshot path: {rel!r}")
            original = source / rel
            target = destination / rel
            if not original.exists() and not original.is_symlink():
                _remove_path(target)
                continue
            if original.is_dir() and not original.is_symlink():
                raise BundleError(f"unsupported directory entry in Agent snapshot: {rel}")
            _remove_path(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, target, follow_symlinks=False)

        actual = source_identity(destination)
        if actual != expected:
            raise BundleError(
                "Agent source changed while its QA snapshot was being frozen; retry capture"
            )
        return actual
    except Exception:
        if destination.exists():
            shutil.rmtree(destination)
        raise


def _wheel_metadata(path: Path) -> tuple[str, str]:
    try:
        with ZipFile(path) as archive:
            metadata_names = [
                name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
            ]
            if len(metadata_names) != 1:
                raise BundleError(f"wheel must contain exactly one METADATA file: {path}")
            message = Parser().parsestr(archive.read(metadata_names[0]).decode("utf-8"))
    except (FileNotFoundError, OSError) as exc:
        raise BundleError(f"invalid or missing wheel: {path}") from exc
    name = str(message.get("Name") or "").strip()
    version = str(message.get("Version") or "").strip()
    if not name or not version:
        raise BundleError(f"wheel metadata has no Name/Version: {path}")
    return name, version


def _dependency_lock_record(
    dist: Path,
    *,
    expected_distribution: str | None = None,
    expected_extras: list[str] | None = None,
) -> dict[str, Any]:
    metadata_path = dist / DEPENDENCY_LOCK_METADATA
    if metadata_path.is_symlink():
        raise BundleError("Python dependency lock metadata cannot be a symlink")
    metadata = _read_json(metadata_path)
    generator = metadata.get("generator")
    inputs = metadata.get("inputs")
    if (
        metadata.get("schemaVersion") != 1
        or metadata.get("kind") != "echo-appliance-python-dependency-lock"
        or metadata.get("pythonVersion") != "3.12"
        or metadata.get("platforms") != DEPENDENCY_LOCK_PLATFORMS
        or metadata.get("onlyBinary") is not True
        or not isinstance(generator, dict)
        or generator.get("name") != "uv"
        or not isinstance(generator.get("version"), str)
        or VERSION_PATTERN.fullmatch(generator["version"]) is None
        or not isinstance(inputs, dict)
    ):
        raise BundleError("Python dependency lock metadata is incomplete")

    os_project = inputs.get("osProject")
    agent_project = inputs.get("agentProject")
    extras = inputs.get("agentExtras")
    input_hashes = (
        inputs.get("buildRequirementsSha256"),
        inputs.get("runtimeRequirementsSha256"),
    )
    if (
        not isinstance(os_project, dict)
        or not isinstance(agent_project, dict)
        or not isinstance(os_project.get("name"), str)
        or not isinstance(agent_project.get("name"), str)
        or os_project.get("file") != "pyproject.toml"
        or agent_project.get("file") != "pyproject.toml"
        or DIGEST_PATTERN.fullmatch(str(os_project.get("sha256") or "")) is None
        or DIGEST_PATTERN.fullmatch(str(agent_project.get("sha256") or "")) is None
        or not isinstance(extras, list)
        or not all(isinstance(item, str) and item for item in extras)
        or not all(
            isinstance(value, str) and DIGEST_PATTERN.fullmatch(value) is not None
            for value in input_hashes
        )
    ):
        raise BundleError("Python dependency lock source inputs are incomplete")
    if expected_distribution is not None and _canonical_name(
        agent_project["name"]
    ) != _canonical_name(expected_distribution):
        raise BundleError("Python dependency lock belongs to a different Agent distribution")
    if expected_extras is not None and extras != expected_extras:
        raise BundleError("Python dependency lock extras do not match the wheel requirement")

    locks: dict[str, dict[str, Any]] = {}
    for field, filename in (
        ("buildLock", BUILD_DEPENDENCY_LOCK),
        ("runtimeLock", RUNTIME_DEPENDENCY_LOCK),
    ):
        record = metadata.get(field)
        path = dist / filename
        if path.is_symlink():
            raise BundleError(f"Python dependency lock cannot be a symlink: {filename}")
        if (
            not isinstance(record, dict)
            or record.get("file") != filename
            or DIGEST_PATTERN.fullmatch(str(record.get("sha256") or "")) is None
            or isinstance(record.get("packageCount"), bool)
            or not isinstance(record.get("packageCount"), int)
            or not 1 <= record["packageCount"] <= 10000
            or _sha256(path) != record["sha256"]
        ):
            raise BundleError(f"Python dependency lock metadata mismatch: {filename}")
        locks[field] = {
            "file": filename,
            "sha256": record["sha256"],
            "packageCount": record["packageCount"],
        }

    return {
        "metadataFile": DEPENDENCY_LOCK_METADATA,
        "metadataSha256": _sha256(metadata_path),
        "schemaVersion": 1,
        "generator": {"name": "uv", "version": generator["version"]},
        "pythonVersion": "3.12",
        "platforms": DEPENDENCY_LOCK_PLATFORMS,
        "onlyBinary": True,
        "inputs": {
            "osProject": os_project,
            "agentProject": agent_project,
            "agentExtras": extras,
            "buildRequirementsSha256": input_hashes[0],
            "runtimeRequirementsSha256": input_hashes[1],
        },
        **locks,
    }


def _tree_digest(root: Path, *, exclude: set[str] | None = None) -> dict[str, Any]:
    excluded = exclude or set()
    digest = hashlib.sha256()
    count = 0
    total_size = 0
    if not root.is_dir():
        raise BundleError(f"artifact directory missing: {root}")
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        rel = path.relative_to(root).as_posix()
        if rel in excluded or path.is_dir():
            continue
        digest.update(rel.encode())
        digest.update(b"\0")
        if path.is_symlink():
            payload = os.readlink(path).encode("utf-8", "surrogateescape")
            digest.update(b"symlink\0")
            digest.update(payload)
            total_size += len(payload)
        elif path.is_file():
            digest.update(b"file\0")
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
                    total_size += len(chunk)
        else:
            raise BundleError(f"unsupported artifact entry: {path}")
        digest.update(b"\0")
        count += 1
    return {"tree_sha256": digest.hexdigest(), "file_count": count, "size": total_size}


def record_wheel(
    agent_src: Path,
    identity_path: Path,
    dist: Path,
    extras: list[str],
) -> dict[str, Any]:
    identity = _verify_source(agent_src, identity_path)
    wheels = sorted(dist.glob("*.whl"))
    if len(wheels) != 1:
        raise BundleError(f"exactly one Agent wheel required in {dist}; found {len(wheels)}")
    wheel = wheels[0]
    name, version = _wheel_metadata(wheel)
    if _canonical_name(name) != _canonical_name(str(identity["python_distribution"])):
        raise BundleError(
            f"wheel distribution {name!r} does not match source {identity['python_distribution']!r}"
        )
    if version != identity["python_version"]:
        raise BundleError(
            f"wheel version {version!r} does not match source {identity['python_version']!r}"
        )

    dependency_lock = _dependency_lock_record(
        dist,
        expected_distribution=name,
        expected_extras=extras,
    )

    requirement = f"{name}[{','.join(extras)}] @ file:///build/agent-dist/{wheel.name}\n"
    requirements_path = dist / "requirements.txt"
    requirements_path.write_text(requirement)
    artifact = {
        "filename": wheel.name,
        "sha256": _sha256(wheel),
        "distribution": name,
        "version": version,
        "extras": extras,
        "requirements_filename": requirements_path.name,
        "requirements_sha256": _sha256(requirements_path),
        "python_dependencies": dependency_lock,
    }
    partial = {
        "schema_version": SCHEMA_VERSION,
        "kind": "wheel",
        "source": identity,
        "artifact": artifact,
    }
    _write_json(dist / PARTIAL_MANIFEST, partial)
    _verify_source(agent_src, identity_path)
    _normalize_tree_mtime(dist, int(identity["source_date_epoch"]))
    return partial


def record_codex(agent_src: Path, identity_path: Path, dist: Path) -> dict[str, Any]:
    """Bind the source-owned, integrity-checked Linux Codex slice to Agent."""

    identity = _verify_source(agent_src, identity_path)
    expected_version = str(identity.get("packaged_codex_version") or "")
    if not expected_version:
        raise BundleError("Agent source does not pin a packaged Codex version")
    codex_manifest_path = dist / "echo-codex-bundle.json"
    codex_manifest = _read_json(codex_manifest_path)
    if (
        codex_manifest.get("schema") != "echo.codex_bundle.v1"
        or codex_manifest.get("package") != "@openai/codex"
        or codex_manifest.get("version") != expected_version
        or codex_manifest.get("platformPackage") != "@openai/codex-linux-x64"
        or codex_manifest.get("target") != "x86_64-unknown-linux-musl"
        or codex_manifest.get("fileHashPhase") != "pre-package"
    ):
        raise BundleError("Codex bundle does not match the selected Agent/Linux target")
    files = codex_manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise BundleError("Codex bundle has no verified file map")
    for relative, expected_hash in files.items():
        if (
            not isinstance(relative, str)
            or not isinstance(expected_hash, str)
            or DIGEST_PATTERN.fullmatch(expected_hash) is None
            or PurePosixPath(relative).is_absolute()
            or ".." in PurePosixPath(relative).parts
        ):
            raise BundleError("Codex bundle contains an unsafe file-map entry")
        target = dist / relative
        if target.is_symlink() or _sha256(target) != expected_hash:
            raise BundleError(f"Codex file hash mismatch: {relative}")
    executable = dist / "bin/codex"
    if executable.is_symlink() or not executable.is_file() or not os.access(executable, os.X_OK):
        raise BundleError("Codex bundle has no executable x86-64 entrypoint")
    with executable.open("rb") as handle:
        header = handle.read(20)
    if (
        len(header) != 20
        or header[:6] != b"\x7fELF\x02\x01"
        or int.from_bytes(header[18:20], "little") != 62
    ):
        raise BundleError("Codex entrypoint is not a little-endian x86-64 ELF64 binary")
    artifact = {
        **_tree_digest(dist, exclude={PARTIAL_MANIFEST}),
        "version": expected_version,
        "target": "x86_64-unknown-linux-musl",
        "manifest_sha256": _sha256(codex_manifest_path),
        "executable_sha256": _sha256(executable),
    }
    partial = {
        "schema_version": SCHEMA_VERSION,
        "kind": "codex",
        "source": identity,
        "artifact": artifact,
    }
    _write_json(dist / PARTIAL_MANIFEST, partial)
    _verify_source(agent_src, identity_path)
    _normalize_tree_mtime(dist, int(identity["source_date_epoch"]))
    return partial


def _resource_files(agent_src: Path) -> list[str]:
    args = ["--", *RESOURCE_INPUTS]
    tracked = _nul_paths(_run_git(agent_src, "ls-files", "-z", *args))
    untracked = _nul_paths(
        _run_git(
            agent_src,
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
            *args,
        )
    )
    return sorted(
        path
        for path in set(tracked + untracked)
        if not _is_runtime_only_path(path) and not _is_ephemeral_generated_path(path)
    )


def _promote_dir(stage: Path, destination: Path) -> None:
    stage = stage.resolve()
    destination = destination.resolve()
    if stage.parent != destination.parent or stage == destination:
        raise BundleError("staging and destination must be distinct sibling directories")
    backup = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.backup")
    if destination.exists():
        destination.rename(backup)
    try:
        stage.rename(destination)
    except Exception:
        if backup.exists() and not destination.exists():
            backup.rename(destination)
        raise
    if backup.exists():
        shutil.rmtree(backup)


def promote_dir(stage: Path, destination: Path) -> None:
    manifest = stage / PARTIAL_MANIFEST
    if not manifest.is_file():
        raise BundleError(f"staged artifact has no {PARTIAL_MANIFEST}: {stage}")
    stage = stage.expanduser().resolve()
    destination = destination.expanduser().resolve()
    if stage.parent == destination.parent:
        _promote_dir(stage, destination)
        return

    # Keep a changing staging tree outside the unified source checkout, then
    # copy it beside the destination so the final replacement remains atomic.
    destination.parent.mkdir(parents=True, exist_ok=True)
    local_stage = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    try:
        shutil.rmtree(local_stage)
        shutil.copytree(stage, local_stage, symlinks=True)
        _promote_dir(local_stage, destination)
    finally:
        if local_stage.exists():
            shutil.rmtree(local_stage)


def export_resources(agent_src: Path, identity_path: Path, dist: Path) -> dict[str, Any]:
    identity = _verify_source(agent_src, identity_path)
    dist = dist.expanduser().resolve()
    dist.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{dist.name}.", dir=dist.parent))
    try:
        for rel in _resource_files(agent_src):
            source = agent_src / rel
            if not source.exists() and not source.is_symlink():
                continue
            target = stage / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target, follow_symlinks=False)
        connector_materializer = (
            stage
            / "extensions"
            / "workbuddy-connectors"
            / "scripts"
            / "materialize-binary-assets.py"
        )
        if connector_materializer.is_file():
            namespace = runpy.run_path(str(connector_materializer))
            namespace["materialize"](stage / "extensions" / "workbuddy-connectors")
        artifact = _tree_digest(stage, exclude={PARTIAL_MANIFEST})
        partial = {
            "schema_version": SCHEMA_VERSION,
            "kind": "resources",
            "source": identity,
            "artifact": artifact,
        }
        _write_json(stage / PARTIAL_MANIFEST, partial)
        _verify_source(agent_src, identity_path)
        _normalize_tree_mtime(stage, int(identity["source_date_epoch"]))
        _promote_dir(stage, dist)
        return partial
    except Exception:
        if stage.exists():
            shutil.rmtree(stage)
        raise


def _verify_partial(root: Path, partial: dict[str, Any]) -> None:
    kind = partial.get("kind")
    artifact = partial.get("artifact")
    if partial.get("schema_version") != SCHEMA_VERSION or not isinstance(artifact, dict):
        raise BundleError(f"invalid {kind!r} partial manifest")
    if kind == "wheel":
        wheel = root / str(artifact.get("filename") or "")
        requirements = root / str(artifact.get("requirements_filename") or "")
        if _sha256(wheel) != artifact.get("sha256"):
            raise BundleError(f"Agent wheel hash mismatch: {wheel}")
        if _sha256(requirements) != artifact.get("requirements_sha256"):
            raise BundleError(f"Agent requirements hash mismatch: {requirements}")
        name, version = _wheel_metadata(wheel)
        if name != artifact.get("distribution") or version != artifact.get("version"):
            raise BundleError("Agent wheel metadata changed after preparation")
        expected_lock = artifact.get("python_dependencies")
        if not isinstance(expected_lock, dict):
            raise BundleError("Agent wheel has no Python dependency lock identity")
        actual_lock = _dependency_lock_record(
            root,
            expected_distribution=name,
            expected_extras=artifact.get("extras")
            if isinstance(artifact.get("extras"), list)
            else None,
        )
        if actual_lock != expected_lock:
            raise BundleError("Agent Python dependency lock changed after preparation")
    elif kind == "resources":
        current = _tree_digest(root, exclude={PARTIAL_MANIFEST})
        for field in ("tree_sha256", "file_count", "size"):
            if current[field] != artifact.get(field):
                raise BundleError(f"Agent resources {field} mismatch")
    elif kind == "codex":
        current = _tree_digest(root, exclude={PARTIAL_MANIFEST})
        for field in ("tree_sha256", "file_count", "size"):
            if current[field] != artifact.get(field):
                raise BundleError(f"Agent Codex {field} mismatch")
        manifest = _read_json(root / "echo-codex-bundle.json")
        executable = root / "bin/codex"
        if (
            manifest.get("schema") != "echo.codex_bundle.v1"
            or manifest.get("version") != artifact.get("version")
            or manifest.get("target") != artifact.get("target")
            or _sha256(root / "echo-codex-bundle.json") != artifact.get("manifest_sha256")
            or executable.is_symlink()
            or not os.access(executable, os.X_OK)
            or _sha256(executable) != artifact.get("executable_sha256")
        ):
            raise BundleError("Agent Codex identity or executable mismatch")
    else:
        raise BundleError(f"unknown partial manifest kind: {kind!r}")


def assemble_bundle(bundle_root: Path, identity_path: Path, output: Path) -> dict[str, Any]:
    identity = _read_json(identity_path)
    partials: dict[str, dict[str, Any]] = {}
    for kind, dirname in (
        ("wheel", "agent-dist"),
        ("resources", "agent-resources"),
        ("codex", "agent-codex"),
    ):
        root = bundle_root / dirname
        partial = _read_json(root / PARTIAL_MANIFEST)
        if partial.get("kind") != kind:
            raise BundleError(f"expected {kind} manifest in {root}")
        if partial.get("source") != identity:
            raise BundleError(
                f"mixed Agent source: {kind} is {partial.get('source', {}).get('source_id')}, "
                f"expected {identity.get('source_id')}"
            )
        _verify_partial(root, partial)
        partials[kind] = partial

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "product": "Echo Agent bundle for Echo OS",
        "assembled_at": identity.get("commit_time"),
        "source": identity,
        "wheel": partials["wheel"]["artifact"],
        "resources": partials["resources"]["artifact"],
        "codex": partials["codex"]["artifact"],
    }
    _write_json(output, manifest)
    _normalize_tree_mtime(output, int(identity["source_date_epoch"]))
    return manifest


def verify_bundle(bundle_root: Path, manifest_path: Path) -> dict[str, Any]:
    manifest = _read_json(manifest_path)
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise BundleError("unsupported Agent bundle manifest schema")
    identity = manifest.get("source")
    if not isinstance(identity, dict) or not identity.get("source_id"):
        raise BundleError("Agent bundle source identity missing")
    for kind, dirname in (
        ("wheel", "agent-dist"),
        ("resources", "agent-resources"),
        ("codex", "agent-codex"),
    ):
        root = bundle_root / dirname
        partial = _read_json(root / PARTIAL_MANIFEST)
        if partial.get("kind") != kind or partial.get("source") != identity:
            raise BundleError(f"Agent {kind} source identity mismatch")
        if partial.get("artifact") != manifest.get(kind):
            raise BundleError(f"Agent {kind} manifest does not match assembled bundle")
        _verify_partial(root, partial)
    return manifest


def verify_installed(manifest_path: Path) -> dict[str, Any]:
    manifest = _read_json(manifest_path)
    wheel = manifest.get("wheel") or {}
    name = str(wheel.get("distribution") or "")
    expected_version = str(wheel.get("version") or "")
    if not name or not expected_version:
        raise BundleError("bundle has no installed distribution identity")
    try:
        distribution = importlib.metadata.distribution(name)
    except importlib.metadata.PackageNotFoundError as exc:
        raise BundleError(f"prepared Agent distribution is not installed: {name}") from exc
    if distribution.version != expected_version:
        raise BundleError(
            f"installed Agent version {distribution.version} != bundle {expected_version}"
        )
    scripts = {
        entry.name for entry in distribution.entry_points if entry.group == "console_scripts"
    }
    if "echo-agent" not in scripts:
        raise BundleError(f"{name} does not provide the compatibility echo-agent entry point")
    return manifest


def verify_embedded_agent_api(echo_src: Path) -> dict[str, Any]:
    """Probe every OS-consumed Agent domain in the unified Echo checkout."""

    os_root = Path(__file__).resolve().parents[2]
    source = echo_src.expanduser().resolve()
    program = """
import importlib.util
import json
import sys

spec = importlib.util.spec_from_file_location("echo_os_agent_contract", sys.argv[1])
if spec is None or spec.loader is None:
    raise RuntimeError("Echo OS Agent contract could not be loaded")
contract = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = contract
spec.loader.exec_module(contract)

report = contract.require_agent_api_contract(
    required_domains=contract.ALL_AGENT_API_DOMAINS,
    optional_domains=(),
)
print(json.dumps(report, sort_keys=True, separators=(",", ":")))
"""
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    contract_path = os_root / "appliance" / "agent_api" / "contract.py"
    try:
        completed = subprocess.run(
            [sys.executable, "-c", program, str(contract_path)],
            cwd=source,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        report = json.loads(completed.stdout)
    except (
        OSError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        UnicodeError,
        json.JSONDecodeError,
    ) as exc:
        raise BundleError(
            "Embedded Agent runtime does not satisfy the Echo OS API contract"
        ) from exc
    if (
        not isinstance(report, dict)
        or report.get("schema") != "echo.agent_api_contract.v1"
        or report.get("compatible") is not True
        or not isinstance(report.get("required"), list)
        or len(report["required"]) != 8
        or report.get("optional") != []
        or any(
            not isinstance(item, dict)
            or item.get("compatible") is not True
            or item.get("missing") != []
            for item in report["required"]
        )
    ):
        raise BundleError("Embedded Agent runtime returned an invalid API contract report")
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    capture = sub.add_parser("capture-source")
    capture.add_argument("--agent-src", type=Path, required=True)
    capture.add_argument("--output", type=Path, required=True)
    capture.add_argument("--allow-dirty", action="store_true")

    wheel = sub.add_parser("record-wheel")
    wheel.add_argument("--agent-src", type=Path, required=True)
    wheel.add_argument("--identity", type=Path, required=True)
    wheel.add_argument("--dist", type=Path, required=True)
    wheel.add_argument("--extras", default="serve,tracing,web,local-auth")

    codex = sub.add_parser("record-codex")
    codex.add_argument("--agent-src", type=Path, required=True)
    codex.add_argument("--identity", type=Path, required=True)
    codex.add_argument("--dist", type=Path, required=True)

    resources = sub.add_parser("export-resources")
    resources.add_argument("--agent-src", type=Path, required=True)
    resources.add_argument("--identity", type=Path, required=True)
    resources.add_argument("--dist", type=Path, required=True)

    snapshot = sub.add_parser("snapshot-source")
    snapshot.add_argument("--agent-src", type=Path, required=True)
    snapshot.add_argument("--identity", type=Path, required=True)
    snapshot.add_argument("--destination", type=Path, required=True)

    promote = sub.add_parser("promote-dir")
    promote.add_argument("--stage", type=Path, required=True)
    promote.add_argument("--destination", type=Path, required=True)

    assemble = sub.add_parser("assemble")
    assemble.add_argument("--bundle-root", type=Path, required=True)
    assemble.add_argument("--identity", type=Path, required=True)
    assemble.add_argument("--output", type=Path, required=True)

    verify = sub.add_parser("verify")
    verify.add_argument("--bundle-root", type=Path, required=True)
    verify.add_argument("--manifest", type=Path, required=True)

    installed = sub.add_parser("verify-installed")
    installed.add_argument("--manifest", type=Path, required=True)

    agent_api = sub.add_parser("verify-agent-api")
    agent_api.add_argument("--echo-src", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "capture-source":
            result = capture_source(args.agent_src, args.output, allow_dirty=args.allow_dirty)
        elif args.command == "record-wheel":
            extras = [item.strip() for item in args.extras.split(",") if item.strip()]
            result = record_wheel(args.agent_src, args.identity, args.dist, extras)
        elif args.command == "record-codex":
            result = record_codex(args.agent_src, args.identity, args.dist)
        elif args.command == "export-resources":
            result = export_resources(args.agent_src, args.identity, args.dist)
        elif args.command == "snapshot-source":
            result = snapshot_source(args.agent_src, args.identity, args.destination)
        elif args.command == "promote-dir":
            promote_dir(args.stage, args.destination)
            result = {"promoted": str(args.destination)}
        elif args.command == "assemble":
            result = assemble_bundle(args.bundle_root, args.identity, args.output)
        elif args.command == "verify":
            result = verify_bundle(args.bundle_root, args.manifest)
        elif args.command == "verify-installed":
            result = verify_installed(args.manifest)
        elif args.command == "verify-agent-api":
            result = verify_embedded_agent_api(args.echo_src)
        else:  # pragma: no cover - argparse guarantees a known command
            raise BundleError(f"unknown command: {args.command}")
    except BundleError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    summary = {
        "ok": True,
        "command": args.command,
        "source_id": (result.get("source") or result).get("source_id")
        if isinstance(result, dict)
        else None,
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
