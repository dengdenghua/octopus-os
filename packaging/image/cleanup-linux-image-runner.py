#!/usr/bin/env python3
"""Remove bounded generated leftovers from one trusted image job."""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import sys
from collections.abc import Sequence
from pathlib import Path

WORKSPACE_TARGETS = (
    "deploy/appliance/agent-bundle.json",
    "deploy/appliance/agent-codex",
    "deploy/appliance/agent-dist",
    "deploy/appliance/agent-resources",
    "packaging/image/mkosi.agent-runtime",
    "packaging/image/mkosi.output",
    "packaging/recovery/mkosi.output",
)
SCRATCH_PREFIX = "echo-"
RUNNER_WORK_ROOTS = (
    Path("/srv/echo-os-image-runner"),
    Path("/__w"),
)
REPOSITORY_NAME = "echo-os"
RUNNER_SCRATCH_NAME = "_temp"


class RunnerCleanupError(RuntimeError):
    """The cleanup boundary is unsafe or could not be completed."""


def _canonical_directory(path: Path, label: str) -> Path:
    if not path.is_absolute() or path.is_symlink():
        raise RunnerCleanupError(f"{label} must be an absolute non-symlink directory")
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise RunnerCleanupError(f"{label} is unavailable") from error
    if resolved != path or not resolved.is_dir() or resolved == Path("/"):
        raise RunnerCleanupError(f"{label} must be one canonical directory below root")
    return resolved


def _remove(path: Path, *, label: str) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    except OSError as error:
        raise RunnerCleanupError(f"cannot inspect {label}") from error
    if stat.S_ISLNK(metadata.st_mode):
        raise RunnerCleanupError(f"refusing linked {label}")
    try:
        if stat.S_ISDIR(metadata.st_mode):
            shutil.rmtree(path)
        else:
            path.unlink()
    except OSError as error:
        raise RunnerCleanupError(f"cannot remove {label}") from error
    return True


def cleanup(
    *,
    workspace: Path,
    scratch: Path,
    runner_work_root: Path | None = None,
) -> tuple[str, ...]:
    candidate_roots = RUNNER_WORK_ROOTS if runner_work_root is None else (runner_work_root,)
    matching_roots = tuple(
        root
        for root in candidate_roots
        if workspace == root / REPOSITORY_NAME / REPOSITORY_NAME
        and scratch == root / RUNNER_SCRATCH_NAME
    )
    if len(matching_roots) != 1:
        raise RunnerCleanupError(
            "runner workspace and scratch do not match one dedicated host or container layout"
        )
    work_root = _canonical_directory(matching_roots[0], "runner work root")
    workspace_root = _canonical_directory(workspace, "runner workspace")
    scratch_root = _canonical_directory(scratch, "runner scratch")
    if workspace_root == scratch_root:
        raise RunnerCleanupError("runner workspace and scratch must be distinct")
    expected_workspace = work_root / REPOSITORY_NAME / REPOSITORY_NAME
    expected_scratch = work_root / RUNNER_SCRATCH_NAME
    if workspace_root != expected_workspace:
        raise RunnerCleanupError("runner workspace is outside the dedicated repository checkout")
    if scratch_root != expected_scratch:
        raise RunnerCleanupError("runner scratch is outside the dedicated work root")

    removed: list[str] = []
    for relative in WORKSPACE_TARGETS:
        target = workspace_root / relative
        if _remove(target, label=f"workspace target {relative}"):
            removed.append(f"workspace/{relative}")

    try:
        scratch_entries = sorted(scratch_root.iterdir(), key=lambda item: item.name)
    except OSError as error:
        raise RunnerCleanupError("cannot enumerate runner scratch") from error
    for entry in scratch_entries:
        if not entry.name.startswith(SCRATCH_PREFIX):
            continue
        if _remove(entry, label=f"scratch target {entry.name}"):
            removed.append(f"scratch/{entry.name}")
    return tuple(removed)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--scratch", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    if os.environ.get("CI") != "true" or os.environ.get("GITHUB_ACTIONS") != "true":
        print(
            "Echo OS runner cleanup rejected: GitHub Actions job identity is required",
            file=sys.stderr,
        )
        return 1
    arguments = _parser().parse_args(argv)
    try:
        removed = cleanup(workspace=arguments.workspace, scratch=arguments.scratch)
    except RunnerCleanupError as error:
        print(f"Echo OS runner cleanup rejected: {error}", file=sys.stderr)
        return 1
    print(f"ECHO_IMAGE_RUNNER_CLEANUP_OK removed={len(removed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
