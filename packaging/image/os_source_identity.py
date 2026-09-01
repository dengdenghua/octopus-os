#!/usr/bin/env python3
"""Capture and verify the clean Echo OS Git source used by a release build."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

SCHEMA = 1
KIND = "echo-os-source-identity"
MAX_MANIFEST_BYTES = 16 * 1024
GIT_ID = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY = re.compile(
    r"^(?:https://[0-9A-Za-z._-]+(?::[0-9]+)?/[^\s?#]+|"
    r"ssh://(?:[0-9A-Za-z._-]+@)?[0-9A-Za-z._-]+(?::[0-9]+)?/[^\s?#]+|"
    r"git@[0-9A-Za-z._-]+:[^\s?#]+)$"
)
EXACT_KEYS = {
    "schema",
    "kind",
    "repository",
    "commit",
    "tree",
    "commit_time",
    "source_date_epoch",
    "dirty",
}


class SourceIdentityError(RuntimeError):
    """The OS source cannot be used as one reproducible release input."""


def _git(repo: Path, *args: str) -> bytes:
    try:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            timeout=60,
        ).stdout
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise SourceIdentityError(f"Git source query failed: {' '.join(args)}") from error


def _one_line(raw: bytes, description: str) -> str:
    try:
        value = raw.decode("utf-8", "strict").strip()
    except UnicodeDecodeError as error:
        raise SourceIdentityError(f"{description} is not valid UTF-8") from error
    if not value or "\n" in value or "\r" in value:
        raise SourceIdentityError(f"{description} must contain one non-empty line")
    return value


def _validate_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != EXACT_KEYS:
        raise SourceIdentityError("OS source identity has an invalid top-level contract")
    schema = payload.get("schema")
    repository = payload.get("repository")
    commit = payload.get("commit")
    tree = payload.get("tree")
    commit_time = payload.get("commit_time")
    source_date_epoch = payload.get("source_date_epoch")
    if (
        not isinstance(schema, int)
        or isinstance(schema, bool)
        or schema != SCHEMA
        or payload.get("kind") != KIND
        or not isinstance(repository, str)
        or REPOSITORY.fullmatch(repository) is None
        or not isinstance(commit, str)
        or GIT_ID.fullmatch(commit) is None
        or not isinstance(tree, str)
        or GIT_ID.fullmatch(tree) is None
        or not isinstance(commit_time, str)
        or len(commit_time) > 64
        or not isinstance(source_date_epoch, int)
        or isinstance(source_date_epoch, bool)
        or not 1 <= source_date_epoch < 2**63
        or payload.get("dirty") is not False
    ):
        raise SourceIdentityError("OS source identity fields are invalid")
    try:
        parsed_time = datetime.fromisoformat(commit_time)
    except ValueError as error:
        raise SourceIdentityError("OS source commit time is invalid") from error
    if parsed_time.tzinfo is None or int(parsed_time.timestamp()) != source_date_epoch:
        raise SourceIdentityError("OS source commit time and epoch do not agree")
    return payload


def _read_regular(path: Path) -> bytes:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise SourceIdentityError("OS source identity is unavailable") from error
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_size <= 0
            or before.st_size > MAX_MANIFEST_BYTES
        ):
            raise SourceIdentityError("OS source identity is empty, oversized or unsafe")
        chunks: list[bytes] = []
        remaining = MAX_MANIFEST_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(4096, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(descriptor)
        if (
            after.st_dev != before.st_dev
            or after.st_ino != before.st_ino
            or after.st_size != before.st_size
            or after.st_mtime_ns != before.st_mtime_ns
            or after.st_ctime_ns != before.st_ctime_ns
        ):
            raise SourceIdentityError("OS source identity changed while reading")
        if len(raw) > MAX_MANIFEST_BYTES:
            raise SourceIdentityError("OS source identity exceeds 16 KiB")
        return raw
    finally:
        os.close(descriptor)


def load_identity(path: Path) -> dict[str, Any]:
    raw = _read_regular(path)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeError) as error:
        raise SourceIdentityError("OS source identity is malformed") from error
    identity = _validate_payload(payload)
    return {**identity, "manifest_sha256": hashlib.sha256(raw).hexdigest()}


def capture_identity(repo_input: Path, expected_commit: str) -> dict[str, Any]:
    if GIT_ID.fullmatch(expected_commit) is None:
        raise SourceIdentityError("expected OS source must be one full 40-char commit")
    if repo_input.is_symlink() or not repo_input.is_dir():
        raise SourceIdentityError("OS source must be a real repository directory")
    repo = repo_input.resolve(strict=True)
    status_before = _git(repo, "status", "--porcelain=v1", "--untracked-files=all", "-z")
    if status_before:
        raise SourceIdentityError("OS release source contains uncommitted or untracked files")
    commit = _one_line(_git(repo, "rev-parse", "--verify", "HEAD^{commit}"), "OS commit")
    tree = _one_line(_git(repo, "rev-parse", "--verify", "HEAD^{tree}"), "OS tree")
    repository = _one_line(_git(repo, "remote", "get-url", "origin"), "OS origin")
    commit_time = _one_line(_git(repo, "show", "-s", "--format=%cI", "HEAD"), "OS commit time")
    epoch_text = _one_line(_git(repo, "show", "-s", "--format=%ct", "HEAD"), "OS source epoch")
    try:
        source_date_epoch = int(epoch_text)
    except ValueError as error:
        raise SourceIdentityError("OS source epoch is invalid") from error
    payload = _validate_payload(
        {
            "schema": SCHEMA,
            "kind": KIND,
            "repository": repository,
            "commit": commit,
            "tree": tree,
            "commit_time": commit_time,
            "source_date_epoch": source_date_epoch,
            "dirty": False,
        }
    )
    if commit != expected_commit:
        raise SourceIdentityError(
            f"OS checkout is {commit}, but workflow expected {expected_commit}"
        )
    status_after = _git(repo, "status", "--porcelain=v1", "--untracked-files=all", "-z")
    commit_after = _one_line(
        _git(repo, "rev-parse", "--verify", "HEAD^{commit}"), "final OS commit"
    )
    tree_after = _one_line(_git(repo, "rev-parse", "--verify", "HEAD^{tree}"), "final OS tree")
    if status_after or commit_after != commit or tree_after != tree:
        raise SourceIdentityError("OS source changed while its identity was captured")
    return payload


def verify_repository(repo: Path, identity: dict[str, Any]) -> dict[str, Any]:
    """Recheck that a later build phase still uses the captured clean tree."""
    current = capture_identity(repo, str(identity["commit"]))
    expected = {key: identity[key] for key in EXACT_KEYS}
    if current != expected:
        raise SourceIdentityError("OS checkout no longer matches the captured source identity")
    return current


def write_identity(path: Path, payload: dict[str, Any], repo: Path) -> None:
    if (
        not path.is_absolute()
        or path.name in {"", ".", ".."}
        or path.parent.is_symlink()
        or not path.parent.is_dir()
    ):
        raise SourceIdentityError("OS source identity output requires one real absolute parent")
    output = path.parent.resolve(strict=True) / path.name
    source = repo.resolve(strict=True)
    try:
        output.relative_to(source)
    except ValueError:
        pass
    else:
        raise SourceIdentityError("OS source identity output must remain outside its Git tree")
    if output.exists() or output.is_symlink():
        raise SourceIdentityError("OS source identity output must be a new non-symlink file")
    encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if len(encoded) > MAX_MANIFEST_BYTES:
        raise SourceIdentityError("OS source identity output exceeds 16 KiB")
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, 0o600)
    try:
        offset = 0
        while offset < len(encoded):
            written = os.write(descriptor, encoded[offset:])
            if written <= 0:
                raise SourceIdentityError("cannot write the OS source identity")
            offset += written
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o444)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    finally:
        os.close(descriptor)
    try:
        os.replace(temporary, output)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    capture = subparsers.add_parser("capture")
    capture.add_argument("--repo", type=Path, required=True)
    capture.add_argument("--expected-commit", required=True)
    capture.add_argument("--output", type=Path, required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--manifest", type=Path, required=True)
    verify.add_argument("--machine", action="store_true")
    verify_repo = subparsers.add_parser("verify-repo")
    verify_repo.add_argument("--repo", type=Path, required=True)
    verify_repo.add_argument("--manifest", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.command == "capture":
            identity = capture_identity(args.repo, args.expected_commit)
            write_identity(args.output, identity, args.repo)
            verified = load_identity(args.output)
            print(
                f"ECHO_OS_SOURCE_IDENTITY_OK commit={verified['commit']} "
                f"tree={verified['tree']} manifest={verified['manifest_sha256']}"
            )
        elif args.command == "verify":
            verified = load_identity(args.manifest)
            if args.machine:
                print(
                    verified["repository"],
                    verified["commit"],
                    verified["tree"],
                    verified["manifest_sha256"],
                    sep="\t",
                )
            else:
                print(
                    f"ECHO_OS_SOURCE_IDENTITY_VERIFIED commit={verified['commit']} "
                    f"tree={verified['tree']} manifest={verified['manifest_sha256']}"
                )
        else:
            verified = load_identity(args.manifest)
            verify_repository(args.repo, verified)
            print(
                f"ECHO_OS_SOURCE_REPOSITORY_VERIFIED commit={verified['commit']} "
                f"tree={verified['tree']} manifest={verified['manifest_sha256']}"
            )
    except (OSError, SourceIdentityError, UnicodeError) as error:
        print(f"Echo OS source identity failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
