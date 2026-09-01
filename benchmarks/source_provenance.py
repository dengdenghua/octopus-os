"""Deterministic source-byte provenance for engine-comparison artifacts.

The manifest deliberately describes the working-tree bytes that a benchmark
controller can inspect.  It does not claim that a remote or already-running
Echo server loaded those bytes; callers must record server provenance
separately when that distinction matters.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmarks.fixed_suite_fixtures import FIXTURE_SPECS
from benchmarks.hardened_verifier_smoke import FULL_CHAIN_SMOKE_VERIFIER_NAMES

FILE_DIGEST_SCHEMA = "echo.source_file_digest.v1"
FILE_DIGEST_VERSION = 1
SOURCE_MANIFEST_SCHEMA = "echo.source_manifest.v1"
SOURCE_MANIFEST_VERSION = 1
ENGINE_COMPARISON_SOURCE_RULE = "echo.engine_comparison_source_inputs.v1"
EXPLICIT_SOURCE_RULE = "echo.explicit_source_inputs.v1"


@dataclass(frozen=True, order=True)
class FileDigest:
    """One regular file bound by repository-relative path and exact bytes."""

    path: str
    size_bytes: int
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": FILE_DIGEST_SCHEMA,
            "version": FILE_DIGEST_VERSION,
            "path": self.path,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class SourceManifest:
    """Versioned, canonically hashed collection of source file digests."""

    rule_id: str
    selected_case_ids: tuple[str, ...]
    files: tuple[FileDigest, ...]
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SOURCE_MANIFEST_SCHEMA,
            "version": SOURCE_MANIFEST_VERSION,
            "rule_id": self.rule_id,
            "selected_case_ids": list(self.selected_case_ids),
            "files": [file.to_dict() for file in self.files],
            "sha256": self.sha256,
        }

    def file(self, relative_path: str | Path) -> FileDigest:
        """Return one record, raising when the path is not in this manifest."""

        normalized = Path(relative_path).as_posix()
        for record in self.files:
            if record.path == normalized:
                return record
        raise KeyError(normalized)

    def subtree_sha256(self, relative_directory: str | Path) -> str:
        """Hash the manifest records below one repository-relative directory."""

        prefix = Path(relative_directory).as_posix().rstrip("/")
        rows = [
            record.to_dict()
            for record in self.files
            if record.path == prefix or record.path.startswith(prefix + "/")
        ]
        if not rows:
            raise KeyError(prefix)
        payload = {
            "schema": "echo.source_subtree.v1",
            "version": 1,
            "path": prefix,
            "files": rows,
        }
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(serialized).hexdigest()


def build_source_manifest(
    repo_root: str | Path,
    *,
    selected_case_ids: Iterable[str],
    selected_agent_ids: Iterable[str] = (),
) -> SourceManifest:
    """Hash source and evaluator bytes used by selected benchmark cases.

    The rule includes all Python under ``runtime/``; benchmark Python except
    fixtures, verifiers, results, and caches; the suite contract; dependency
    declarations; the fixed full-chain smoke wrappers; and only the selected
    fixture trees and their additional verifier files.
    Bundled skill payloads under ``runtime/execution/all_skills`` are excluded:
    they are task content, not engine implementation, and may contain repository
    symlinks by design.
    """

    root = _validated_root(repo_root)
    case_ids = _normalized_case_ids(selected_case_ids)
    agent_ids = _normalized_agent_ids(selected_agent_ids)
    unknown = set(case_ids) - set(FIXTURE_SPECS)
    if unknown:
        raise ValueError(f"source inputs are not declared for cases: {sorted(unknown)}")

    paths: list[Path] = [
        root / "pyproject.toml",
        root / "uv.lock",
        root / "skills.lock.json",
        root / "benchmarks" / "behavioral-surpass-suite.json",
    ]
    paths.extend(
        _walk_files(
            root / "benchmarks",
            include=lambda path: path.suffix == ".py",
            descend=lambda path: (
                path.name not in {"fixtures", "verifiers", "results", "__pycache__"}
            ),
        )
    )
    paths.extend(
        _walk_files(
            root / "runtime",
            include=lambda path: path.suffix == ".py",
            descend=lambda path: (
                path.name != "__pycache__" and path != root / "runtime" / "execution" / "all_skills"
            ),
        )
    )
    paths.extend(_walk_files(root / "agents" / "_shared", include=lambda _path: True))
    for agent_id in agent_ids:
        paths.extend(_walk_files(root / "agents" / agent_id, include=lambda _path: True))
    prompt_root = root / "prompts"
    if prompt_root.is_symlink() or not prompt_root.is_dir():
        raise ValueError(f"source input directory is unavailable: {prompt_root}")
    paths.extend(sorted(prompt_root.glob("*.yaml")))
    paths.extend(sorted(prompt_root.glob("*.yml")))
    verifier_names = set(FULL_CHAIN_SMOKE_VERIFIER_NAMES)
    for case_id in case_ids:
        inputs = FIXTURE_SPECS[case_id]
        fixture_files = _walk_files(
            root / "benchmarks" / "fixtures" / inputs.fixture_name,
            include=lambda _path: True,
        )
        if not fixture_files:
            raise ValueError(f"selected fixture contains no regular files: {inputs.fixture_name}")
        paths.extend(fixture_files)
        verifier_names.add(inputs.verifier_name)
    paths.extend(
        root / "benchmarks" / "verifiers" / verifier_name
        for verifier_name in sorted(verifier_names)
    )

    return build_file_manifest(
        root,
        paths,
        rule_id=ENGINE_COMPARISON_SOURCE_RULE,
        selected_case_ids=case_ids,
    )


def build_file_manifest(
    repo_root: str | Path,
    paths: Iterable[str | Path],
    *,
    rule_id: str = EXPLICIT_SOURCE_RULE,
    selected_case_ids: Iterable[str] = (),
) -> SourceManifest:
    """Build a fail-closed manifest from an explicit regular-file list."""

    root = _validated_root(repo_root)
    normalized_rule = str(rule_id).strip()
    if not normalized_rule:
        raise ValueError("source manifest rule_id must not be empty")
    case_ids = _normalized_case_ids(selected_case_ids)

    records: list[FileDigest] = []
    seen: set[str] = set()
    for raw_path in paths:
        path, relative = _validated_file(root, raw_path)
        relative_text = relative.as_posix()
        if relative_text in seen:
            raise ValueError(f"duplicate source input: {relative_text}")
        seen.add(relative_text)
        size_bytes, sha256 = _hash_regular_file(path)
        records.append(FileDigest(path=relative_text, size_bytes=size_bytes, sha256=sha256))

    records.sort(key=lambda record: record.path)
    payload = _canonical_payload(normalized_rule, case_ids, records)
    digest = hashlib.sha256(payload).hexdigest()
    return SourceManifest(
        rule_id=normalized_rule,
        selected_case_ids=case_ids,
        files=tuple(records),
        sha256=digest,
    )


def _canonical_payload(
    rule_id: str,
    selected_case_ids: Sequence[str],
    files: Sequence[FileDigest],
) -> bytes:
    payload = {
        "schema": SOURCE_MANIFEST_SCHEMA,
        "version": SOURCE_MANIFEST_VERSION,
        "rule_id": rule_id,
        "selected_case_ids": list(selected_case_ids),
        "files": [file.to_dict() for file in files],
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _validated_root(repo_root: str | Path) -> Path:
    raw = Path(repo_root).expanduser()
    if raw.is_symlink():
        raise ValueError(f"repository root must not be a symlink: {raw}")
    try:
        root = raw.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ValueError(f"repository root does not exist: {raw}") from exc
    if not root.is_dir():
        raise ValueError(f"repository root is not a directory: {root}")
    return root


def _normalized_case_ids(values: Iterable[str]) -> tuple[str, ...]:
    normalized = [str(value).strip() for value in values]
    if any(not value for value in normalized):
        raise ValueError("selected case IDs must not be empty")
    if len(set(normalized)) != len(normalized):
        raise ValueError("selected case IDs must not contain duplicates")
    return tuple(sorted(normalized))


def _normalized_agent_ids(values: Iterable[str]) -> tuple[str, ...]:
    normalized = [str(value).strip() for value in values]
    if any(
        not value
        or value in {".", ".."}
        or Path(value).name != value
        or "/" in value
        or "\\" in value
        for value in normalized
    ):
        raise ValueError("selected agent IDs must be non-empty path segments")
    if len(set(normalized)) != len(normalized):
        raise ValueError("selected agent IDs must not contain duplicates")
    return tuple(sorted(normalized))


def _walk_files(
    directory: Path,
    *,
    include: Callable[[Path], bool],
    descend: Callable[[Path], bool] | None = None,
) -> list[Path]:
    if directory.is_symlink():
        raise ValueError(f"source input directory must not be a symlink: {directory}")
    if not directory.exists():
        raise ValueError(f"source input directory does not exist: {directory}")
    if not directory.is_dir():
        raise ValueError(f"source input directory is not a directory: {directory}")

    output: list[Path] = []
    with os.scandir(directory) as entries:
        for entry in sorted(entries, key=lambda item: item.name):
            path = Path(entry.path)
            if entry.is_symlink():
                raise ValueError(f"source input must not be a symlink: {path}")
            if entry.is_dir(follow_symlinks=False):
                if descend is None or descend(path):
                    output.extend(_walk_files(path, include=include, descend=descend))
                continue
            if entry.is_file(follow_symlinks=False):
                if include(path):
                    output.append(path)
                continue
            if include(path):
                raise ValueError(f"source input is not a regular file: {path}")
    return output


def _validated_file(root: Path, raw_path: str | Path) -> tuple[Path, Path]:
    supplied = Path(raw_path).expanduser()
    candidate = supplied if supplied.is_absolute() else root / supplied
    candidate = Path(os.path.abspath(candidate))
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"source input is outside repository root: {supplied}") from exc
    if relative == Path("."):
        raise ValueError("repository root is not a regular source file")

    current = root
    for part in relative.parts:
        current = current / part
        try:
            metadata = current.lstat()
        except FileNotFoundError as exc:
            raise ValueError(f"source input does not exist: {relative.as_posix()}") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError(f"source input must not traverse a symlink: {relative.as_posix()}")
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"source input is not a regular file: {relative.as_posix()}")
    return candidate, relative


def _hash_regular_file(path: Path) -> tuple[int, str]:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"cannot open source input as a regular file: {path}") from exc
    digest = hashlib.sha256()
    size_bytes = 0
    try:
        metadata_before = os.fstat(descriptor)
        if not stat.S_ISREG(metadata_before.st_mode):
            raise ValueError(f"source input is not a regular file: {path}")
        while chunk := os.read(descriptor, 1024 * 1024):
            size_bytes += len(chunk)
            digest.update(chunk)
        metadata_after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    identity_before = (
        metadata_before.st_dev,
        metadata_before.st_ino,
        metadata_before.st_size,
        metadata_before.st_mtime_ns,
    )
    identity_after = (
        metadata_after.st_dev,
        metadata_after.st_ino,
        metadata_after.st_size,
        metadata_after.st_mtime_ns,
    )
    if identity_before != identity_after or size_bytes != metadata_after.st_size:
        raise ValueError(f"source input changed while being hashed: {path}")
    return size_bytes, digest.hexdigest()


__all__ = [
    "ENGINE_COMPARISON_SOURCE_RULE",
    "EXPLICIT_SOURCE_RULE",
    "FILE_DIGEST_SCHEMA",
    "FILE_DIGEST_VERSION",
    "SOURCE_MANIFEST_SCHEMA",
    "SOURCE_MANIFEST_VERSION",
    "FileDigest",
    "SourceManifest",
    "build_file_manifest",
    "build_source_manifest",
]


