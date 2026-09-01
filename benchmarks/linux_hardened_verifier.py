"""Production Linux confinement for hidden-verifier candidate execution.

This module is deliberately Linux-specific.  It does not treat the presence
of ``bwrap`` (or an operator supplied executable path) as authorization.  A
run is authorized only by a root-owned v2 attestation that binds all executable
inputs and by live proofs of the delegated kernel controls used for the run.

The security boundary is intentionally narrow:

* bubblewrap supplies fresh user, mount, PID, IPC, UTS, cgroup and network
  namespaces;
* a delegated cgroup-v2 child supplies memory, PID and CPU accounting plus
  kernel ``cgroup.kill`` tree termination;
* an exclusively leased, pre-mounted tmpfs supplies hard byte and inode bounds;
* an evaluator-created, no-follow snapshot is the only candidate tree mounted;
* output is streamed into byte-bounded buffers rather than ``communicate()``;
* every exit path kills and proves the cgroup empty before returning.

Provisioning is separate from execution.  See
``benchmarks.hardened_verifier_attestation`` for the root-only generator and
unprivileged validator CLI.
"""

from __future__ import annotations

import ctypes
import errno
import fcntl
import json
import math
import os
import re
import resource
import selectors
import signal
import socket
import stat
import struct
import subprocess
import sys
import threading
import time
import types
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import IO, Any, BinaryIO, NoReturn

ATTESTATION_SCHEMA = "echo.hardened_verifier_runner.v2"
CANDIDATE_WORKER_CONTRACT_SCHEMA = "echo.candidate_worker_contract.v1"
CANDIDATE_API_ISOLATION_SCHEMA = "echo.candidate_api_process.v1"
RUNNER_BACKEND = "linux-bubblewrap-cgroup-v2"
WORKER_RPC = "u32be-canonical-json-v1"
RUNNER_READY_KIND = "runner_ready"

CONFIG_ENV = "ECHO_HARDENED_VERIFIER_RUNNER"
WORKER_CONFIG_ENV = "ECHO_CANDIDATE_WORKER_CONFIG"
WORKER_CONFIG_SHA256_ENV = "ECHO_CANDIDATE_WORKER_CONFIG_SHA256"
WORKER_CLI_ENV = "ECHO_CANDIDATE_WORKER_CLI_JSON"
WORKER_CONTRACT_SHA256_ENV = "ECHO_CANDIDATE_WORKER_CONTRACT_SHA256"

_RUN_NAME_RE = re.compile(r"^run-[0-9a-f]{32}$")
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{24,128}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_START_MARKER = b"__ECHO_HARDENED_SANDBOX_READY_v2__"
_PROBE_SECRET_KEY = "ECHO_SANDBOX_PROBE_SECRET"

_RUNTIME_MAX_ENTRIES = 500_000
_RUNTIME_MAX_DEPTH = 128
_RUNTIME_MAX_PATH_BYTES = 4_096
_RUNTIME_MAX_SINGLE_FILE_BYTES = 2 * 1024**3
_RUNTIME_MAX_TOTAL_BYTES = 16 * 1024**3
_RUNTIME_MAX_SYMLINK_BYTES = 4_096
_NAMESPACE_NAMES = ("user", "pid", "mnt", "net", "ipc", "uts", "cgroup")

INFRASTRUCTURE_INVALID_EXIT = 78
OUTPUT_LIMIT_EXIT = 79
RESOURCE_LIMIT_EXIT = 80
PROTOCOL_LIMIT_EXIT = 81


class LinuxRunnerInfrastructureError(RuntimeError):
    """A kernel, provisioning or evaluator guarantee is absent or drifted."""


class CandidateSnapshotViolation(RuntimeError):
    """The candidate tree itself violates the evaluator's bounded-tree contract."""


def _raise_combined_cleanup_failure(
    message: str,
    *,
    primary: BaseException | None,
    cleanup_errors: Sequence[BaseException],
) -> NoReturn:
    """Raise one infrastructure error whose standard traceback retains every cause."""

    causes: list[BaseException] = []
    if primary is not None:
        causes.append(primary)
    causes.extend(cleanup_errors)
    group = BaseExceptionGroup(message, causes)
    failure = LinuxRunnerInfrastructureError(message)
    failure.add_note("primary and cleanup exceptions are preserved in the attached exception group")
    raise failure from group


@dataclass(frozen=True, slots=True)
class RunnerLimits:
    """Non-configurable safety maxima bound by the runner source digest."""

    snapshot_max_entries: int = 20_000
    snapshot_max_depth: int = 48
    snapshot_max_path_bytes: int = 2_048
    snapshot_max_single_file_bytes: int = 32 * 1024 * 1024
    snapshot_max_total_bytes: int = 256 * 1024 * 1024
    verifier_max_source_bytes: int = 1024 * 1024
    stdout_max_bytes: int = 2 * 1024 * 1024
    stderr_max_bytes: int = 2 * 1024 * 1024
    wall_max_seconds: float = 180.0
    cpu_max_seconds: float = 120.0
    memory_max_bytes: int = 1536 * 1024 * 1024
    pids_max: int = 256
    fds_max: int = 256
    file_size_max_bytes: int = 64 * 1024 * 1024
    scratch_max_bytes: int = 768 * 1024 * 1024
    scratch_max_inodes: int = 65_536
    rpc_max_frame_bytes: int = 65_536
    rpc_max_frames: int = 64
    reap_timeout_seconds: float = 10.0

    def to_dict(self) -> dict[str, int | float]:
        return {
            name: getattr(self, name)
            for name in self.__dataclass_fields__  # type: ignore[attr-defined]
        }


DEFAULT_LIMITS = RunnerLimits()


def worker_rpc_contract(limits: RunnerLimits = DEFAULT_LIMITS) -> dict[str, Any]:
    return {
        "control_transport": "inherited-unix-stream-fd-launcher-only",
        "worker_transport": "inherited-unix-stream-fd",
        "framing": WORKER_RPC,
        "max_frame_bytes": limits.rpc_max_frame_bytes,
        "max_frames": limits.rpc_max_frames,
        "reserved_control_frames": ["runner_ready", "runner_complete"],
    }


def worker_isolation_contract() -> dict[str, Any]:
    return {
        "cgroup_kill": True,
        "cgroup_v2_controllers": ["cpu", "memory", "pids"],
        "challenge": "read-only",
        "controller_visible": False,
        "different_process": True,
        "inner_gid": 65534,
        "inner_uid": 65534,
        "network": "none-including-loopback",
        "per_invocation_probe": True,
        "pid_namespace": "private",
        "ptrace_controller": False,
        "user_namespace": "private",
        "workspace": "read-only",
    }


def worker_limit_contract() -> dict[str, Any]:
    return {
        "descriptors_bounded": True,
        "memory_bounded": True,
        "output_bytes": 4_194_304,
        "pids_bounded": True,
        "scratch_bytes_bounded": True,
        "scratch_inodes_bounded": True,
        "wall_time_bounded": True,
    }


@dataclass(frozen=True, slots=True)
class TreeDigest:
    sha256: str
    entries: int
    regular_files: int
    total_bytes: int
    max_depth: int

    def to_dict(self) -> dict[str, int | str]:
        return {
            "sha256": self.sha256,
            "entries": self.entries,
            "regular_files": self.regular_files,
            "total_bytes": self.total_bytes,
            "max_depth": self.max_depth,
        }


@dataclass(frozen=True, slots=True)
class SnapshotEvidence:
    source: TreeDigest
    copied: TreeDigest
    destination: Path = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class RunResourceEvidence:
    cpu_usage_usec: int
    memory_peak_bytes: int
    pids_peak: int
    scratch_used_bytes: int
    scratch_used_inodes: int
    termination_reason: str | None
    cgroup_reaped: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "cpu_usage_usec": self.cpu_usage_usec,
            "memory_peak_bytes": self.memory_peak_bytes,
            "pids_peak": self.pids_peak,
            "scratch_used_bytes": self.scratch_used_bytes,
            "scratch_used_inodes": self.scratch_used_inodes,
            "termination_reason": self.termination_reason,
            "cgroup_reaped": self.cgroup_reaped,
        }


@dataclass(frozen=True, slots=True)
class HardenedProcessResult:
    returncode: int
    stdout: bytes
    stderr: bytes
    timed_out: bool
    evidence: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class RunnerAttestation:
    path: Path
    config_sha256: str
    git_sha: str
    repository_root: Path
    bubblewrap_path: Path
    bubblewrap_sha256: str
    seccomp_path: Path
    seccomp_sha256: str
    runtime_root: Path
    runtime_tree_sha256: str
    runtime_python: str
    cgroup_parent: Path
    scratch_mount: Path
    launcher_argv: tuple[str, ...]
    launcher_executable_path: Path
    launcher_executable_sha256: str
    launcher_module_path: Path
    launcher_module_sha256: str
    contract_path: Path
    contract_sha256: str
    controller_path: Path
    controller_sha256: str
    worker_path: Path
    worker_sha256: str
    provisioned_uid: int
    provisioned_gid: int
    limits: RunnerLimits

    def public_dict(self) -> dict[str, Any]:
        return {
            "schema": ATTESTATION_SCHEMA,
            "authorization": True,
            "backend": RUNNER_BACKEND,
            "candidate_worker_contract_schema": CANDIDATE_WORKER_CONTRACT_SCHEMA,
            "config_path": str(self.path),
            "config_sha256": self.config_sha256,
            "git_sha": self.git_sha,
            "runtime_image": {
                "root": str(self.runtime_root),
                "tree_sha256": self.runtime_tree_sha256,
                "python": self.runtime_python,
            },
            "bubblewrap": {
                "path": str(self.bubblewrap_path),
                "sha256": self.bubblewrap_sha256,
            },
            "seccomp": {
                "path": str(self.seccomp_path),
                "sha256": self.seccomp_sha256,
            },
            "sources": {
                "launcher_executable_sha256": self.launcher_executable_sha256,
                "launcher_module_sha256": self.launcher_module_sha256,
                "contract_sha256": self.contract_sha256,
                "controller_sha256": self.controller_sha256,
                "worker_sha256": self.worker_sha256,
            },
            "launcher": {
                "argv": list(self.launcher_argv),
                "executable_sha256": self.launcher_executable_sha256,
                "module_path": str(self.launcher_module_path),
                "module_sha256": self.launcher_module_sha256,
            },
            "contract": {
                "path": str(self.contract_path),
                "sha256": self.contract_sha256,
            },
            "controller": {
                "path": str(self.controller_path),
                "sha256": self.controller_sha256,
            },
            "worker": {
                "path": str(self.worker_path),
                "sha256": self.worker_sha256,
            },
            "candidate_api_isolation_schema": CANDIDATE_API_ISOLATION_SCHEMA,
            "cgroup_v2": {
                "parent": str(self.cgroup_parent),
                "controllers": ["cpu", "memory", "pids"],
                "cgroup_kill_required": True,
            },
            "scratch": {
                "mount": str(self.scratch_mount),
                "filesystem": "tmpfs",
                "exclusive": True,
                "max_bytes": self.limits.scratch_max_bytes,
                "max_inodes": self.limits.scratch_max_inodes,
            },
            "namespaces": ["user", "mount", "pid", "ipc", "uts", "cgroup", "network"],
            "inner_uid": 65534,
            "inner_gid": 65534,
            "network": "none-including-loopback-and-external",
            "rpc": worker_rpc_contract(self.limits),
            "isolation": worker_isolation_contract(),
            "limits": worker_limit_contract(),
            "resource_limits": self.limits.to_dict(),
            "probe": "adversarial-per-invocation",
            "provisioning": {
                "attestation_owner": "root",
                "runtime_owner": "root-and-not-writable-by-runner",
                "scratch": "dedicated tmpfs,nosuid,nodev,hard-size-and-inode-cap",
                "cgroup": "delegated-v2,cpu+memory+pids,cgroup.kill",
                "execution_privilege": "unprivileged-after-provisioning",
            },
        }


@dataclass(slots=True)
class _TreeAccumulator:
    digest: Any = field(default_factory=sha256)
    entries: int = 0
    regular_files: int = 0
    total_bytes: int = 0
    max_depth: int = 0

    def add(self, record: Mapping[str, Any]) -> None:
        encoded = _canonical_json(record)
        self.digest.update(struct.pack(">I", len(encoded)))
        self.digest.update(encoded)
        self.entries += 1
        self.max_depth = max(self.max_depth, int(record["depth"]))
        if record["kind"] == "file":
            self.regular_files += 1
            self.total_bytes += int(record["size"])

    def finish(self) -> TreeDigest:
        return TreeDigest(
            sha256=self.digest.hexdigest(),
            entries=self.entries,
            regular_files=self.regular_files,
            total_bytes=self.total_bytes,
            max_depth=self.max_depth,
        )


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _require_sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise LinuxRunnerInfrastructureError(f"{label} must be a lowercase SHA-256")
    return value


def _require_absolute_path(value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise LinuxRunnerInfrastructureError(f"{label} must be a non-empty path")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise LinuxRunnerInfrastructureError(f"{label} must be absolute and normalized")
    return path


def _load_protected_json(path: str | Path) -> tuple[Path, bytes, dict[str, Any]]:
    candidate = Path(path)
    if not candidate.is_absolute():
        raise LinuxRunnerInfrastructureError("hardened runner attestation path must be absolute")
    try:
        observed = candidate.lstat()
    except OSError as exc:
        raise LinuxRunnerInfrastructureError(
            f"hardened runner attestation is unavailable: {candidate}: {exc}"
        ) from exc
    if not stat.S_ISREG(observed.st_mode) or candidate.is_symlink():
        raise LinuxRunnerInfrastructureError("hardened runner attestation must be a regular file")
    if observed.st_uid != 0:
        raise LinuxRunnerInfrastructureError("hardened runner attestation must be root-owned")
    if observed.st_mode & 0o022:
        raise LinuxRunnerInfrastructureError(
            "hardened runner attestation must not be group/world writable"
        )
    _assert_protected_ancestry(candidate, "hardened runner attestation")
    try:
        descriptor = os.open(candidate, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            payload = _read_fd_bounded(descriptor, 1024 * 1024)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise LinuxRunnerInfrastructureError(
            f"hardened runner attestation could not be read safely: {exc}"
        ) from exc
    if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (
        observed.st_dev,
        observed.st_ino,
        observed.st_size,
        observed.st_mtime_ns,
    ):
        raise LinuxRunnerInfrastructureError("hardened runner attestation drifted while read")
    try:
        parsed = json.loads(payload, object_pairs_hook=_reject_duplicate_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise LinuxRunnerInfrastructureError("hardened runner attestation is invalid JSON") from exc
    if not isinstance(parsed, dict):
        raise LinuxRunnerInfrastructureError("hardened runner attestation must be an object")
    if _canonical_json(parsed) + b"\n" != payload:
        raise LinuxRunnerInfrastructureError(
            "hardened runner attestation must be canonical JSON with one trailing newline"
        )
    return candidate, payload, parsed


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_fd_bounded(descriptor: int, maximum: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(descriptor, min(64 * 1024, maximum + 1 - total))
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        total += len(chunk)
        if total > maximum:
            raise LinuxRunnerInfrastructureError("bounded file exceeds its declared maximum")


def _file_sha256(path: Path, *, maximum: int = 128 * 1024 * 1024) -> str:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError as exc:
        raise LinuxRunnerInfrastructureError(
            f"cannot open content-bound file {path}: {exc}"
        ) from exc
    digest = sha256()
    total = 0
    before = os.fstat(descriptor)
    try:
        if not stat.S_ISREG(before.st_mode):
            raise LinuxRunnerInfrastructureError(f"content-bound path is not regular: {path}")
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > maximum:
                raise LinuxRunnerInfrastructureError(f"content-bound file is over limit: {path}")
            digest.update(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ):
        raise LinuxRunnerInfrastructureError(f"content-bound file drifted while read: {path}")
    return digest.hexdigest()


def _assert_digest(path: Path, expected: str, label: str) -> None:
    observed = _file_sha256(path)
    if observed != expected:
        raise LinuxRunnerInfrastructureError(
            f"{label} content digest drifted: expected {expected}, observed {observed}"
        )


def _assert_protected_regular(path: Path, label: str) -> None:
    try:
        observed = path.lstat()
    except OSError as exc:
        raise LinuxRunnerInfrastructureError(f"{label} is unavailable: {path}: {exc}") from exc
    if path.is_symlink() or not stat.S_ISREG(observed.st_mode):
        raise LinuxRunnerInfrastructureError(f"{label} must be a non-symlink regular file")
    if observed.st_uid != 0 or observed.st_mode & 0o022:
        raise LinuxRunnerInfrastructureError(
            f"{label} must be root-owned and not group/world writable"
        )
    _assert_protected_ancestry(path, label)


def _assert_protected_ancestry(path: Path, label: str) -> None:
    """Reject root-owned files replaceable through a writable parent directory."""

    try:
        current = path.resolve(strict=True).parent
        while True:
            observed = current.stat()
            if (
                not stat.S_ISDIR(observed.st_mode)
                or observed.st_uid != 0
                or stat.S_IMODE(observed.st_mode) & 0o022
            ):
                raise LinuxRunnerInfrastructureError(
                    f"{label} ancestry is not root-owned and immutable: {current}"
                )
            if current.parent == current:
                break
            current = current.parent
    except LinuxRunnerInfrastructureError:
        raise
    except OSError as exc:
        raise LinuxRunnerInfrastructureError(
            f"{label} ancestry is unavailable: {path}: {exc}"
        ) from exc


def _assert_root_protected(path: Path, label: str) -> None:
    try:
        observed = path.lstat()
        resolved = path.resolve(strict=True)
        resolved_stat = resolved.stat()
    except OSError as exc:
        raise LinuxRunnerInfrastructureError(f"{label} is unavailable: {path}: {exc}") from exc
    if path.is_symlink() or not stat.S_ISDIR(observed.st_mode):
        raise LinuxRunnerInfrastructureError(f"{label} must be a non-symlink directory")
    if observed.st_uid != 0 or resolved_stat.st_uid != 0:
        raise LinuxRunnerInfrastructureError(f"{label} must be root-owned")
    if (observed.st_mode | resolved_stat.st_mode) & 0o022:
        raise LinuxRunnerInfrastructureError(f"{label} must not be group/world writable")
    _assert_protected_ancestry(path, label)


def _tree_stat_identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def runtime_tree_digest(root: str | Path) -> TreeDigest:
    """Hash every runtime entry without following links or accepting devices."""

    root_path = Path(root)
    _assert_root_protected(root_path, "hardened verifier runtime root")
    accumulator = _TreeAccumulator()

    def visit(directory_fd: int, relative: PurePosixPath, depth: int) -> None:
        directory_before = os.fstat(directory_fd)
        names = _bounded_directory_names(
            directory_fd,
            _RUNTIME_MAX_ENTRIES - accumulator.entries,
            error_type=LinuxRunnerInfrastructureError,
            listing_message="runtime tree cannot be listed",
            overflow_message="runtime tree entry-count bound exceeded",
        )
        for name in names:
            if not _safe_component(name):
                raise LinuxRunnerInfrastructureError(
                    "runtime tree contains an unsafe path component"
                )
            child = relative / name
            encoded_path = child.as_posix().encode("utf-8", "strict")
            if len(encoded_path) > _RUNTIME_MAX_PATH_BYTES or depth > _RUNTIME_MAX_DEPTH:
                raise LinuxRunnerInfrastructureError("runtime tree path/depth bound exceeded")
            try:
                observed = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            except OSError as exc:
                raise LinuxRunnerInfrastructureError(
                    f"runtime entry became unavailable: {child}"
                ) from exc
            mode = stat.S_IMODE(observed.st_mode)
            if observed.st_uid != 0:
                raise LinuxRunnerInfrastructureError(
                    f"runtime tree entry is not root-owned: {child}"
                )
            if stat.S_ISDIR(observed.st_mode):
                if mode & 0o022:
                    raise LinuxRunnerInfrastructureError(
                        f"runtime directory is writable by the runner: {child}"
                    )
                accumulator.add(
                    {"depth": depth, "kind": "dir", "mode": mode, "path": child.as_posix()}
                )
                try:
                    child_fd = os.open(
                        name,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                        dir_fd=directory_fd,
                    )
                except OSError as exc:
                    raise LinuxRunnerInfrastructureError(
                        f"runtime directory is unsafe: {child}"
                    ) from exc
                try:
                    visit(child_fd, child, depth + 1)
                finally:
                    os.close(child_fd)
            elif stat.S_ISREG(observed.st_mode):
                if mode & 0o022:
                    raise LinuxRunnerInfrastructureError(
                        f"runtime file is writable by the runner: {child}"
                    )
                try:
                    digest, size = _hash_regular_at(
                        directory_fd,
                        name,
                        observed,
                        _RUNTIME_MAX_SINGLE_FILE_BYTES,
                    )
                except CandidateSnapshotViolation as exc:
                    raise LinuxRunnerInfrastructureError(
                        f"runtime regular file is unsafe: {child}: {exc}"
                    ) from exc
                if accumulator.total_bytes + size > _RUNTIME_MAX_TOTAL_BYTES:
                    raise LinuxRunnerInfrastructureError("runtime tree total-byte bound exceeded")
                accumulator.add(
                    {
                        "depth": depth,
                        "kind": "file",
                        "mode": mode,
                        "path": child.as_posix(),
                        "sha256": digest,
                        "size": size,
                    }
                )
            elif stat.S_ISLNK(observed.st_mode):
                try:
                    target = os.readlink(name, dir_fd=directory_fd)
                    observed_after = os.stat(
                        name,
                        dir_fd=directory_fd,
                        follow_symlinks=False,
                    )
                except OSError as exc:
                    raise LinuxRunnerInfrastructureError(
                        f"runtime symlink is unreadable: {child}"
                    ) from exc
                if _tree_stat_identity(observed) != _tree_stat_identity(observed_after):
                    raise LinuxRunnerInfrastructureError(
                        f"runtime symlink drifted while read: {child}"
                    )
                _validate_runtime_symlink(root_fd, child, target)
                accumulator.add(
                    {
                        "depth": depth,
                        "kind": "symlink",
                        "mode": mode,
                        "path": child.as_posix(),
                        "target": target,
                    }
                )
            else:
                raise LinuxRunnerInfrastructureError(
                    f"runtime tree contains a special file: {child}"
                )
        directory_after = os.fstat(directory_fd)
        if _tree_stat_identity(directory_before) != _tree_stat_identity(directory_after):
            raise LinuxRunnerInfrastructureError(
                f"runtime directory drifted while hashing: {relative}"
            )

    root_metadata = root_path.lstat()
    root_fd = os.open(root_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        if _tree_stat_identity(os.fstat(root_fd)) != _tree_stat_identity(root_metadata):
            raise LinuxRunnerInfrastructureError("runtime root drifted while opening")
        visit(root_fd, PurePosixPath("."), 1)
    finally:
        os.close(root_fd)
    return accumulator.finish()


def _validate_runtime_mountpoints(root: Path) -> None:
    """Require deterministic pre-created targets under the read-only image."""

    required_directories = (
        "proc",
        "dev",
        "workspace",
        "work",
        "challenge",
        "echo-trusted",
    )
    for relative in required_directories:
        candidate = root / relative
        try:
            observed = candidate.lstat()
        except OSError as exc:
            raise LinuxRunnerInfrastructureError(
                f"runtime image lacks required mountpoint /{relative}: {exc}"
            ) from exc
        if (
            candidate.is_symlink()
            or not stat.S_ISDIR(observed.st_mode)
            or observed.st_uid != 0
            or stat.S_IMODE(observed.st_mode) & 0o022
        ):
            raise LinuxRunnerInfrastructureError(
                f"runtime mountpoint /{relative} is not a root-owned immutable directory"
            )
    for relative in ("proc", "dev", "workspace", "work", "challenge"):
        if any((root / relative).iterdir()):
            raise LinuxRunnerInfrastructureError(
                f"runtime mountpoint /{relative} must be empty before provisioning"
            )
    worker_target = root / "echo-trusted" / "trusted_verifier_worker.py"
    try:
        observed_target = worker_target.lstat()
    except OSError as exc:
        raise LinuxRunnerInfrastructureError(
            "runtime image lacks the trusted-worker bind placeholder"
        ) from exc
    if (
        worker_target.is_symlink()
        or not stat.S_ISREG(observed_target.st_mode)
        or observed_target.st_uid != 0
        or stat.S_IMODE(observed_target.st_mode) & 0o022
        or observed_target.st_size != 0
    ):
        raise LinuxRunnerInfrastructureError(
            "trusted-worker bind placeholder must be an empty root-owned immutable file"
        )


def _safe_component(name: str) -> bool:
    if name in {"", ".", ".."} or "/" in name or "\x00" in name:
        return False
    try:
        encoded = name.encode("utf-8", "strict")
    except UnicodeEncodeError:
        return False
    return len(encoded) <= 255


def _bounded_directory_names(
    directory_fd: int,
    maximum: int,
    *,
    error_type: type[RuntimeError],
    listing_message: str,
    overflow_message: str,
) -> list[str]:
    """Collect at most ``maximum`` names without an unbounded ``listdir``."""

    names: list[str] = []
    try:
        with os.scandir(directory_fd) as entries:
            for entry in entries:
                if len(names) >= maximum:
                    raise error_type(overflow_message)
                names.append(entry.name)
    except error_type:
        raise
    except OSError as exc:
        raise error_type(listing_message) from exc
    return sorted(names)


def _validate_internal_symlink(path: PurePosixPath, target: str) -> None:
    try:
        target_bytes = target.encode("utf-8", "strict")
    except UnicodeEncodeError as exc:
        raise LinuxRunnerInfrastructureError(
            f"runtime symlink target is not valid UTF-8: {path}"
        ) from exc
    if not target_bytes or len(target_bytes) > _RUNTIME_MAX_SYMLINK_BYTES or "\x00" in target:
        raise LinuxRunnerInfrastructureError(f"runtime symlink target is invalid: {path}")
    target_path = PurePosixPath(target)
    if target_path.is_absolute():
        raise LinuxRunnerInfrastructureError(f"runtime symlink escapes its root: {path}")
    parts: list[str] = []
    for part in (*path.parent.parts, *target_path.parts):
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts or parts == ["."]:
                raise LinuxRunnerInfrastructureError(f"runtime symlink escapes its root: {path}")
            parts.pop()
        else:
            parts.append(part)


def _validate_runtime_symlink(
    runtime_root_fd: int,
    link_path: PurePosixPath,
    target: str,
) -> None:
    """Resolve a link inside a virtual root without consulting the host root."""

    try:
        target_bytes = target.encode("utf-8", "strict")
    except UnicodeEncodeError as exc:
        raise LinuxRunnerInfrastructureError(
            f"runtime symlink target is not valid UTF-8: {link_path}"
        ) from exc
    if not target_bytes or len(target_bytes) > _RUNTIME_MAX_SYMLINK_BYTES or "\x00" in target:
        raise LinuxRunnerInfrastructureError(f"runtime symlink target is invalid: {link_path}")
    target_path = PurePosixPath(target)
    resolved = [] if target_path.is_absolute() else _normalized_virtual_parts(link_path.parent)
    pending = list(target_path.parts[1:] if target_path.is_absolute() else target_path.parts)
    # Prove the declared target itself is beneath the image even if a distro
    # intentionally ships it dangling (common for alternatives/doc links).
    lexical = list(resolved)
    for component in pending:
        if component in {"", ".", "/"}:
            continue
        if component == "..":
            if not lexical:
                raise LinuxRunnerInfrastructureError(
                    f"runtime symlink escapes its virtual root: {link_path}"
                )
            lexical.pop()
        else:
            lexical.append(component)
    expansions = 0
    while pending:
        component = pending.pop(0)
        if component in {"", ".", "/"}:
            continue
        if component == "..":
            if not resolved:
                raise LinuxRunnerInfrastructureError(
                    f"runtime symlink escapes its virtual root: {link_path}"
                )
            resolved.pop()
            continue
        parent_fd = _open_runtime_directory(runtime_root_fd, resolved)
        try:
            try:
                observed = os.stat(component, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                # The link text and normalized virtual target are still
                # content-bound.  A read-only image cannot retarget it later.
                return
            except OSError as exc:
                raise LinuxRunnerInfrastructureError(
                    f"runtime symlink cannot be inspected: {link_path} -> {target}"
                ) from exc
            if stat.S_ISLNK(observed.st_mode):
                expansions += 1
                if expansions > 40:
                    raise LinuxRunnerInfrastructureError(
                        f"runtime symlink chain is cyclic or too deep: {link_path}"
                    )
                nested = PurePosixPath(os.readlink(component, dir_fd=parent_fd))
                if nested.is_absolute():
                    resolved = []
                    nested_parts = list(nested.parts[1:])
                else:
                    nested_parts = list(nested.parts)
                pending = nested_parts + pending
                continue
            if pending and not stat.S_ISDIR(observed.st_mode):
                raise LinuxRunnerInfrastructureError(
                    f"runtime symlink traverses a non-directory: {link_path}"
                )
            resolved.append(component)
        finally:
            os.close(parent_fd)


def _normalized_virtual_parts(path: PurePosixPath) -> list[str]:
    output: list[str] = []
    for part in path.parts:
        if part in {"", ".", "/"}:
            continue
        if part == "..":
            if not output:
                raise LinuxRunnerInfrastructureError("virtual path escapes its root")
            output.pop()
        else:
            output.append(part)
    return output


def _open_runtime_directory(root_fd: int, parts: Sequence[str]) -> int:
    descriptor = os.dup(root_fd)
    try:
        for component in parts:
            next_descriptor = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _hash_regular_at(
    directory_fd: int,
    name: str,
    observed: os.stat_result,
    maximum: int,
) -> tuple[str, int]:
    if observed.st_size > maximum:
        raise CandidateSnapshotViolation(f"candidate file exceeds single-file bound: {name}")
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
            dir_fd=directory_fd,
        )
    except OSError as exc:
        raise CandidateSnapshotViolation(
            f"candidate file is unsafe to open: {name}: {exc}"
        ) from exc
    digest = sha256()
    total = 0
    before = os.fstat(descriptor)
    try:
        if not stat.S_ISREG(before.st_mode):
            raise CandidateSnapshotViolation(f"candidate entry changed type while read: {name}")
        if _tree_stat_identity(before) != _tree_stat_identity(observed):
            raise CandidateSnapshotViolation(f"candidate entry drifted before read: {name}")
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, maximum + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > maximum:
                raise CandidateSnapshotViolation(
                    f"candidate file exceeds single-file bound: {name}"
                )
            digest.update(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)

    if _tree_stat_identity(before) != _tree_stat_identity(after) or total != after.st_size:
        raise CandidateSnapshotViolation(f"candidate file drifted while read: {name}")
    return digest.hexdigest(), total


def create_candidate_snapshot(
    source: str | Path,
    destination: str | Path,
    *,
    limits: RunnerLimits = DEFAULT_LIMITS,
    allow_internal_symlinks: bool = False,
) -> SnapshotEvidence:
    """Create and double-check a detached candidate snapshot.

    All symlinks are rejected.  That is intentionally stronger than merely
    rejecting known escapes: a link can be retargeted between validation and
    use, while the fixed fixtures have no legitimate need for candidate links.
    """

    source_path = Path(source)
    destination_path = Path(destination)
    try:
        root_lstat = source_path.lstat()
    except OSError as exc:
        raise CandidateSnapshotViolation(f"candidate workspace is unavailable: {exc}") from exc
    if source_path.is_symlink() or not stat.S_ISDIR(root_lstat.st_mode):
        raise CandidateSnapshotViolation("candidate workspace must be a non-symlink directory")
    if destination_path.exists():
        raise LinuxRunnerInfrastructureError("candidate snapshot destination already exists")
    destination_path.mkdir(mode=0o700)
    first = _copy_or_scan_candidate_tree(
        source_path,
        destination_path,
        limits=limits,
        copy=True,
        allow_internal_symlinks=allow_internal_symlinks,
    )
    second = _copy_or_scan_candidate_tree(
        source_path,
        None,
        limits=limits,
        copy=False,
        allow_internal_symlinks=allow_internal_symlinks,
    )
    if first != second:
        raise LinuxRunnerInfrastructureError(
            "candidate workspace drifted while the evaluator created its snapshot"
        )
    copied = _copy_or_scan_candidate_tree(
        destination_path,
        None,
        limits=limits,
        copy=False,
        allow_internal_symlinks=allow_internal_symlinks,
    )
    if copied != first:
        raise LinuxRunnerInfrastructureError("candidate snapshot content does not match its source")
    return SnapshotEvidence(source=first, copied=copied, destination=destination_path)


def candidate_tree_digest(
    source: str | Path,
    *,
    limits: RunnerLimits = DEFAULT_LIMITS,
    allow_internal_symlinks: bool = False,
) -> TreeDigest:
    return _copy_or_scan_candidate_tree(
        Path(source),
        None,
        limits=limits,
        copy=False,
        allow_internal_symlinks=allow_internal_symlinks,
    )


def trusted_controller_tree_manifest_sha256(
    root: str | Path,
    *,
    reject_symlinks: bool,
    limits: RunnerLimits = DEFAULT_LIMITS,
) -> str:
    """Reproduce the controller manifest with bounded no-follow dirfd walks."""

    root_path = Path(root)
    try:
        root_metadata = root_path.lstat()
    except OSError as exc:
        raise CandidateSnapshotViolation("controller tree root is unavailable") from exc
    if stat.S_ISLNK(root_metadata.st_mode) or not stat.S_ISDIR(root_metadata.st_mode):
        raise CandidateSnapshotViolation("controller tree root must be a non-symlink directory")
    rows: list[dict[str, Any]] = []
    total_bytes = 0
    file_limit = min(limits.snapshot_max_single_file_bytes, 256 * 1024)

    def visit(directory_fd: int, relative_root: PurePosixPath) -> None:
        nonlocal total_bytes
        directory_before = os.fstat(directory_fd)
        names = _bounded_directory_names(
            directory_fd,
            limits.snapshot_max_entries - len(rows),
            error_type=CandidateSnapshotViolation,
            listing_message="controller tree cannot be listed",
            overflow_message="controller tree entry-count limit exceeded",
        )
        for name in names:
            if not _safe_component(name):
                raise CandidateSnapshotViolation(
                    "controller tree contains an unsafe path component"
                )
            if len(rows) >= limits.snapshot_max_entries:
                raise CandidateSnapshotViolation("controller tree entry-count limit exceeded")
            relative_path = relative_root / name
            relative = relative_path.as_posix()
            if len(relative_path.parts) > limits.snapshot_max_depth:
                raise CandidateSnapshotViolation("controller tree depth limit exceeded")
            if len(relative.encode("utf-8", "strict")) > limits.snapshot_max_path_bytes:
                raise CandidateSnapshotViolation("controller tree path-byte limit exceeded")
            try:
                metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            except OSError as exc:
                raise CandidateSnapshotViolation(
                    f"controller tree entry became unavailable: {relative}"
                ) from exc
            mode = stat.S_IMODE(metadata.st_mode)
            if stat.S_ISLNK(metadata.st_mode):
                if reject_symlinks:
                    raise CandidateSnapshotViolation(
                        f"controller tree contains a forbidden symlink: {relative}"
                    )
                try:
                    target = os.readlink(name, dir_fd=directory_fd)
                    observed_after = os.stat(
                        name,
                        dir_fd=directory_fd,
                        follow_symlinks=False,
                    )
                    target_bytes = target.encode("utf-8", errors="strict")
                except (OSError, UnicodeError) as exc:
                    raise CandidateSnapshotViolation(
                        f"controller tree contains an unsafe symlink: {relative}"
                    ) from exc
                if len(target_bytes) > limits.snapshot_max_path_bytes:
                    raise CandidateSnapshotViolation(
                        "controller tree symlink target-byte limit exceeded"
                    )
                if _tree_stat_identity(metadata) != _tree_stat_identity(observed_after):
                    raise LinuxRunnerInfrastructureError(
                        "controller tree symlink drifted while read"
                    )
                rows.append({"kind": "symlink", "mode": mode, "path": relative, "target": target})
            elif stat.S_ISDIR(metadata.st_mode):
                rows.append({"kind": "directory", "mode": mode, "path": relative})
                try:
                    child_fd = os.open(
                        name,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                        dir_fd=directory_fd,
                    )
                except OSError as exc:
                    raise CandidateSnapshotViolation(
                        f"controller tree directory is unsafe: {relative}"
                    ) from exc
                try:
                    opened = os.fstat(child_fd)
                    if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
                        raise LinuxRunnerInfrastructureError(
                            "controller tree directory drifted before traversal"
                        )
                    visit(child_fd, relative_path)
                finally:
                    os.close(child_fd)
            elif stat.S_ISREG(metadata.st_mode):
                if reject_symlinks and metadata.st_nlink != 1:
                    raise CandidateSnapshotViolation(
                        f"controller tree file has unsafe links: {relative}"
                    )
                digest, size = _hash_regular_at(directory_fd, name, metadata, file_limit)
                total_bytes += size
                if total_bytes > limits.snapshot_max_total_bytes:
                    raise CandidateSnapshotViolation("controller tree total-byte limit exceeded")
                rows.append(
                    {
                        "kind": "file",
                        "mode": mode,
                        "path": relative,
                        "sha256": digest,
                        "size": size,
                    }
                )
            else:
                raise CandidateSnapshotViolation(
                    f"controller tree contains a special file: {relative}"
                )
        directory_after = os.fstat(directory_fd)
        if _tree_stat_identity(directory_before) != _tree_stat_identity(directory_after):
            raise LinuxRunnerInfrastructureError("controller tree directory drifted during scan")

    root_fd = os.open(
        root_path,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
    )
    try:
        if _tree_stat_identity(os.fstat(root_fd)) != _tree_stat_identity(root_metadata):
            raise LinuxRunnerInfrastructureError("controller tree root drifted while opening")
        visit(root_fd, PurePosixPath("."))
    finally:
        os.close(root_fd)
    rows.sort(key=lambda row: str(row["path"]))
    encoded = json.dumps(
        rows,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _copy_or_scan_candidate_tree(
    source: Path,
    destination: Path | None,
    *,
    limits: RunnerLimits,
    copy: bool,
    allow_internal_symlinks: bool = False,
) -> TreeDigest:
    accumulator = _TreeAccumulator()

    def visit(
        source_fd: int,
        destination_dir: Path | None,
        relative: PurePosixPath,
        depth: int,
    ) -> None:
        if depth > limits.snapshot_max_depth:
            raise CandidateSnapshotViolation("candidate snapshot depth limit exceeded")
        directory_before = os.fstat(source_fd)
        names = _bounded_directory_names(
            source_fd,
            limits.snapshot_max_entries - accumulator.entries,
            error_type=CandidateSnapshotViolation,
            listing_message=f"candidate directory cannot be listed: {relative}",
            overflow_message="candidate snapshot entry-count limit exceeded",
        )
        for name in names:
            if not _safe_component(name):
                raise CandidateSnapshotViolation("candidate tree contains an unsafe path component")
            child = relative / name
            path_bytes = child.as_posix().encode("utf-8", "strict")
            if len(path_bytes) > limits.snapshot_max_path_bytes:
                raise CandidateSnapshotViolation("candidate snapshot path-byte limit exceeded")
            if accumulator.entries >= limits.snapshot_max_entries:
                raise CandidateSnapshotViolation("candidate snapshot entry-count limit exceeded")
            try:
                observed = os.stat(name, dir_fd=source_fd, follow_symlinks=False)
            except OSError as exc:
                raise CandidateSnapshotViolation(
                    f"candidate entry became unavailable: {child}"
                ) from exc
            mode = stat.S_IMODE(observed.st_mode)
            if stat.S_ISLNK(observed.st_mode):
                if not allow_internal_symlinks:
                    raise CandidateSnapshotViolation(
                        f"candidate snapshot contains a symlink: {child}"
                    )
                try:
                    target = os.readlink(name, dir_fd=source_fd)
                    target_bytes = target.encode("utf-8", "strict")
                    observed_after = os.stat(
                        name,
                        dir_fd=source_fd,
                        follow_symlinks=False,
                    )
                    _validate_internal_symlink(child, target)
                except (OSError, UnicodeError, LinuxRunnerInfrastructureError) as exc:
                    raise CandidateSnapshotViolation(
                        f"candidate snapshot contains an escaping symlink: {child}"
                    ) from exc
                if len(target_bytes) > limits.snapshot_max_path_bytes:
                    raise CandidateSnapshotViolation(
                        "candidate snapshot symlink target-byte limit exceeded"
                    )
                if _tree_stat_identity(observed) != _tree_stat_identity(observed_after):
                    raise LinuxRunnerInfrastructureError(
                        f"candidate symlink drifted while read: {child}"
                    )
                accumulator.add(
                    {
                        "depth": depth,
                        "kind": "symlink",
                        "mode": 0o777,
                        "path": child.as_posix(),
                        "target": target,
                    }
                )
                if copy and destination_dir is not None:
                    os.symlink(target, destination_dir / name)
                continue
            if stat.S_ISDIR(observed.st_mode):
                accumulator.add(
                    {"depth": depth, "kind": "dir", "mode": 0o555, "path": child.as_posix()}
                )
                child_destination = destination_dir / name if destination_dir is not None else None
                if copy and child_destination is not None:
                    child_destination.mkdir(mode=0o700)
                try:
                    child_fd = os.open(
                        name,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                        dir_fd=source_fd,
                    )
                except OSError as exc:
                    raise CandidateSnapshotViolation(
                        f"candidate directory is unsafe: {child}"
                    ) from exc
                try:
                    visit(child_fd, child_destination, child, depth + 1)
                finally:
                    os.close(child_fd)
                if copy and child_destination is not None:
                    child_destination.chmod(0o555)
            elif stat.S_ISREG(observed.st_mode):
                digest, size = _hash_regular_at(
                    source_fd,
                    name,
                    observed,
                    limits.snapshot_max_single_file_bytes,
                )
                if accumulator.total_bytes + size > limits.snapshot_max_total_bytes:
                    raise CandidateSnapshotViolation("candidate snapshot total-byte limit exceeded")
                normalized_mode = 0o555 if mode & 0o111 else 0o444
                accumulator.add(
                    {
                        "depth": depth,
                        "kind": "file",
                        "mode": normalized_mode,
                        "path": child.as_posix(),
                        "sha256": digest,
                        "size": size,
                    }
                )
                if copy and destination_dir is not None:
                    _copy_regular_at(
                        source_fd,
                        name,
                        observed,
                        destination_dir / name,
                        normalized_mode,
                        limits.snapshot_max_single_file_bytes,
                    )
            else:
                raise CandidateSnapshotViolation(
                    f"candidate snapshot contains a special file: {child}"
                )
        directory_after = os.fstat(source_fd)
        if _tree_stat_identity(directory_before) != _tree_stat_identity(directory_after):
            raise LinuxRunnerInfrastructureError(
                f"candidate directory drifted while scanning: {relative}"
            )

    try:
        root_metadata = source.lstat()
    except OSError as exc:
        raise CandidateSnapshotViolation("candidate snapshot root is unavailable") from exc
    if stat.S_ISLNK(root_metadata.st_mode) or not stat.S_ISDIR(root_metadata.st_mode):
        raise CandidateSnapshotViolation("candidate snapshot root must be a non-symlink directory")
    root_fd = os.open(source, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        if _tree_stat_identity(os.fstat(root_fd)) != _tree_stat_identity(root_metadata):
            raise LinuxRunnerInfrastructureError("candidate snapshot root drifted while opening")
        visit(root_fd, destination, PurePosixPath("."), 1)
    finally:
        os.close(root_fd)
    if copy and destination is not None:
        destination.chmod(0o555)
    return accumulator.finish()


def _copy_regular_at(
    source_fd: int,
    name: str,
    observed: os.stat_result,
    destination: Path,
    mode: int,
    maximum: int,
) -> None:
    input_fd = os.open(
        name,
        os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
        dir_fd=source_fd,
    )
    output_fd = -1
    try:
        before = os.fstat(input_fd)
        if (before.st_dev, before.st_ino, before.st_size) != (
            observed.st_dev,
            observed.st_ino,
            observed.st_size,
        ):
            raise CandidateSnapshotViolation(f"candidate file drifted before copy: {name}")
        output_fd = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
            0o600,
        )
        total = 0
        while True:
            chunk = os.read(input_fd, min(1024 * 1024, maximum + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > maximum:
                raise CandidateSnapshotViolation(f"candidate file grew during copy: {name}")
            view = memoryview(chunk)
            while view:
                written = os.write(output_fd, view)
                view = view[written:]
        os.fsync(output_fd)
        after = os.fstat(input_fd)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise CandidateSnapshotViolation(f"candidate file drifted during copy: {name}")
        os.fchmod(output_fd, mode)
    finally:
        os.close(input_fd)
        if output_fd >= 0:
            os.close(output_fd)
        if sys.exc_info()[0] is not None:
            destination.unlink(missing_ok=True)


def load_attestation(
    path: str | Path,
    *,
    verify_runtime: bool = True,
    verify_kernel: bool = True,
) -> RunnerAttestation:
    """Parse and validate a root-owned v2 attestation without trusting PATH."""

    if not sys.platform.startswith("linux") and verify_kernel:
        raise LinuxRunnerInfrastructureError("hardened verifier runner is Linux-only")
    config_path, raw, payload = _load_protected_json(path)
    expected_keys = {
        "authorization",
        "backend",
        "bubblewrap",
        "candidate_worker_contract_schema",
        "candidate_api_isolation_schema",
        "cgroup_parent",
        "contract",
        "controller",
        "git_sha",
        "isolation",
        "launcher",
        "limits",
        "provisioned_gid",
        "provisioned_uid",
        "repository_root",
        "resource_limits",
        "runtime",
        "rpc",
        "schema",
        "scratch_mount",
        "seccomp",
        "worker",
    }
    if set(payload) != expected_keys:
        raise LinuxRunnerInfrastructureError(
            f"hardened runner attestation keys drifted: {sorted(set(payload) ^ expected_keys)}"
        )
    if payload.get("schema") != ATTESTATION_SCHEMA:
        raise LinuxRunnerInfrastructureError("unsupported hardened runner attestation schema")
    if payload.get("candidate_worker_contract_schema") != CANDIDATE_WORKER_CONTRACT_SCHEMA:
        raise LinuxRunnerInfrastructureError("unsupported candidate worker contract schema")
    if payload.get("candidate_api_isolation_schema") != CANDIDATE_API_ISOLATION_SCHEMA:
        raise LinuxRunnerInfrastructureError("unsupported isolated candidate-API schema")
    if payload.get("authorization") is not True or payload.get("backend") != RUNNER_BACKEND:
        raise LinuxRunnerInfrastructureError("hardened runner attestation is not authorized")
    git_sha = payload.get("git_sha")
    if not isinstance(git_sha, str) or not re.fullmatch(r"[0-9a-f]{40,64}", git_sha):
        raise LinuxRunnerInfrastructureError("attestation git_sha is invalid")
    if payload.get("limits") != worker_limit_contract():
        raise LinuxRunnerInfrastructureError("attested worker limit contract drifted")
    if payload.get("resource_limits") != DEFAULT_LIMITS.to_dict():
        raise LinuxRunnerInfrastructureError(
            "attested resource limits do not equal source-bound maxima"
        )
    if payload.get("rpc") != worker_rpc_contract():
        raise LinuxRunnerInfrastructureError("attested worker RPC contract drifted")
    if payload.get("isolation") != worker_isolation_contract():
        raise LinuxRunnerInfrastructureError("attested worker isolation contract drifted")

    def bound_file(name: str) -> tuple[Path, str]:
        value = payload.get(name)
        if not isinstance(value, dict) or set(value) != {"path", "sha256"}:
            raise LinuxRunnerInfrastructureError(f"attestation {name} binding is invalid")
        return (
            _require_absolute_path(value["path"], f"{name}.path"),
            _require_sha256(value["sha256"], f"{name}.sha256"),
        )

    bubblewrap_path, bubblewrap_sha = bound_file("bubblewrap")
    seccomp_path, seccomp_sha = bound_file("seccomp")
    controller_path, controller_sha = bound_file("controller")
    contract_path, contract_sha = bound_file("contract")
    worker_path, worker_sha = bound_file("worker")
    if contract_path != controller_path.with_name("trusted_verifier_contract.py"):
        raise LinuxRunnerInfrastructureError(
            "attested verifier contract must be beside the trusted controller"
        )
    launcher_payload = payload.get("launcher")
    if not isinstance(launcher_payload, dict) or set(launcher_payload) != {
        "argv",
        "executable_sha256",
        "module_path",
        "module_sha256",
    }:
        raise LinuxRunnerInfrastructureError("attestation launcher binding is invalid")
    launcher_argv_raw = launcher_payload["argv"]
    if (
        not isinstance(launcher_argv_raw, list)
        or not all(isinstance(item, str) for item in launcher_argv_raw)
        or len(launcher_argv_raw) != 3
    ):
        raise LinuxRunnerInfrastructureError("attestation launcher argv is invalid")
    launcher_executable = _require_absolute_path(launcher_argv_raw[0], "launcher.argv[0]")
    launcher_module = _require_absolute_path(
        launcher_payload["module_path"], "launcher.module_path"
    )
    if launcher_argv_raw != [str(launcher_executable), "-I", str(launcher_module)]:
        raise LinuxRunnerInfrastructureError(
            "attestation launcher argv is not the fixed no-shell form"
        )
    launcher_executable_sha = _require_sha256(
        launcher_payload["executable_sha256"], "launcher.executable_sha256"
    )
    launcher_module_sha = _require_sha256(
        launcher_payload["module_sha256"], "launcher.module_sha256"
    )
    runtime_payload = payload.get("runtime")
    if not isinstance(runtime_payload, dict) or set(runtime_payload) != {
        "python",
        "root",
        "tree_sha256",
    }:
        raise LinuxRunnerInfrastructureError("attestation runtime binding is invalid")
    runtime_root = _require_absolute_path(runtime_payload["root"], "runtime.root")
    runtime_digest = _require_sha256(runtime_payload["tree_sha256"], "runtime.tree_sha256")
    runtime_python = runtime_payload["python"]
    if (
        not isinstance(runtime_python, str)
        or not runtime_python.startswith("/")
        or ".." in PurePosixPath(runtime_python).parts
    ):
        raise LinuxRunnerInfrastructureError("runtime.python must be an absolute in-image path")
    repository_root = _require_absolute_path(payload["repository_root"], "repository_root")
    cgroup_parent = _require_absolute_path(payload["cgroup_parent"], "cgroup_parent")
    scratch_mount = _require_absolute_path(payload["scratch_mount"], "scratch_mount")
    provisioned_uid = payload.get("provisioned_uid")
    provisioned_gid = payload.get("provisioned_gid")
    if not isinstance(provisioned_uid, int) or not isinstance(provisioned_gid, int):
        raise LinuxRunnerInfrastructureError("attestation provisioned uid/gid are invalid")
    if verify_kernel and (os.geteuid(), os.getegid()) != (provisioned_uid, provisioned_gid):
        raise LinuxRunnerInfrastructureError(
            "hardened runner must execute as the attested unprivileged uid/gid"
        )
    for label, bound_path, digest in (
        ("bubblewrap", bubblewrap_path, bubblewrap_sha),
        ("seccomp", seccomp_path, seccomp_sha),
        ("launcher executable", launcher_executable, launcher_executable_sha),
        ("launcher module", launcher_module, launcher_module_sha),
        ("trusted verifier contract", contract_path, contract_sha),
        ("controller", controller_path, controller_sha),
        ("worker", worker_path, worker_sha),
    ):
        _assert_protected_regular(bound_path, label)
        _assert_digest(bound_path, digest, label)
    _assert_root_protected(runtime_root, "hardened verifier runtime root")
    _validate_runtime_mountpoints(runtime_root)
    if verify_runtime:
        observed_runtime = runtime_tree_digest(runtime_root)
        if observed_runtime.sha256 != runtime_digest:
            raise LinuxRunnerInfrastructureError(
                "hardened verifier runtime image content digest drifted"
            )
    attestation = RunnerAttestation(
        path=config_path,
        config_sha256=sha256(raw).hexdigest(),
        git_sha=git_sha,
        repository_root=repository_root,
        bubblewrap_path=bubblewrap_path,
        bubblewrap_sha256=bubblewrap_sha,
        seccomp_path=seccomp_path,
        seccomp_sha256=seccomp_sha,
        runtime_root=runtime_root,
        runtime_tree_sha256=runtime_digest,
        runtime_python=runtime_python,
        cgroup_parent=cgroup_parent,
        scratch_mount=scratch_mount,
        launcher_argv=tuple(launcher_argv_raw),
        launcher_executable_path=launcher_executable,
        launcher_executable_sha256=launcher_executable_sha,
        launcher_module_path=launcher_module,
        launcher_module_sha256=launcher_module_sha,
        contract_path=contract_path,
        contract_sha256=contract_sha,
        controller_path=controller_path,
        controller_sha256=controller_sha,
        worker_path=worker_path,
        worker_sha256=worker_sha,
        provisioned_uid=provisioned_uid,
        provisioned_gid=provisioned_gid,
        limits=DEFAULT_LIMITS,
    )
    if verify_kernel:
        _validate_cgroup_parent(attestation.cgroup_parent)
        _validate_scratch_mount(attestation.scratch_mount, attestation.limits)
    return attestation


def _decode_mount_path(value: str) -> str:
    return re.sub(
        r"\\([0-7]{3})",
        lambda match: chr(int(match.group(1), 8)),
        value,
    )


def _mount_record(path: Path) -> tuple[str, set[str], set[str]]:
    try:
        lines = Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise LinuxRunnerInfrastructureError(f"cannot read Linux mountinfo: {exc}") from exc
    target = str(path.resolve(strict=True))
    matches: list[tuple[str, set[str], set[str]]] = []
    for line in lines:
        left, separator, right = line.partition(" - ")
        if not separator:
            continue
        fields = left.split()
        right_fields = right.split()
        if len(fields) < 6 or len(right_fields) < 3:
            continue
        if _decode_mount_path(fields[4]) == target:
            matches.append(
                (right_fields[0], set(fields[5].split(",")), set(right_fields[2].split(",")))
            )
    if len(matches) != 1:
        raise LinuxRunnerInfrastructureError(f"path must be one exact mountpoint: {path}")
    return matches[0]


def _validate_scratch_mount(
    path: Path,
    limits: RunnerLimits,
    *,
    expected_uid: int | None = None,
    expected_gid: int | None = None,
) -> None:
    filesystem, mount_options, super_options = _mount_record(path)
    options = mount_options | super_options
    if filesystem != "tmpfs" or not {"rw", "nosuid", "nodev"}.issubset(options):
        raise LinuxRunnerInfrastructureError(
            "scratch must be a dedicated rw,nosuid,nodev tmpfs mount"
        )
    observed = path.lstat()
    if not stat.S_ISDIR(observed.st_mode) or path.is_symlink():
        raise LinuxRunnerInfrastructureError("scratch mount must be a non-symlink directory")
    owner_uid = os.geteuid() if expected_uid is None else expected_uid
    owner_gid = os.getegid() if expected_gid is None else expected_gid
    if observed.st_uid != owner_uid or observed.st_gid != owner_gid:
        raise LinuxRunnerInfrastructureError(
            "scratch mount must be delegated to the runner uid/gid"
        )
    if observed.st_mode & 0o077:
        raise LinuxRunnerInfrastructureError("scratch mount must be private to the runner")
    stats = os.statvfs(path)
    capacity = stats.f_frsize * stats.f_blocks
    if capacity <= 0 or capacity > limits.scratch_max_bytes:
        raise LinuxRunnerInfrastructureError("scratch tmpfs byte capacity exceeds its attested cap")
    if stats.f_files <= 0 or stats.f_files > limits.scratch_max_inodes:
        raise LinuxRunnerInfrastructureError(
            "scratch tmpfs inode capacity exceeds its attested cap"
        )
    minimum = 2 * limits.snapshot_max_total_bytes + 16 * 1024 * 1024
    if capacity < minimum:
        raise LinuxRunnerInfrastructureError(
            "scratch tmpfs is too small for a bounded snapshot run"
        )


def _current_cgroup_v2_path() -> Path:
    try:
        hierarchy_lines = Path("/proc/self/cgroup").read_text(encoding="ascii").splitlines()
        mount_lines = Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise LinuxRunnerInfrastructureError(
            f"cannot read current cgroup membership: {exc}"
        ) from exc
    memberships = [line.partition("::")[2] for line in hierarchy_lines if "::" in line]
    if len(memberships) != 1 or not memberships[0].startswith("/"):
        raise LinuxRunnerInfrastructureError("current cgroup-v2 membership is ambiguous")
    membership = Path(_decode_mount_path(memberships[0]))
    candidates: list[tuple[int, Path]] = []
    for line in mount_lines:
        left, separator, right = line.partition(" - ")
        if not separator:
            continue
        fields = left.split()
        right_fields = right.split()
        if len(fields) < 6 or len(right_fields) < 3 or right_fields[0] != "cgroup2":
            continue
        mount_root = Path(_decode_mount_path(fields[3]))
        mountpoint = Path(_decode_mount_path(fields[4]))
        try:
            relative = membership.relative_to(mount_root)
        except ValueError:
            continue
        candidates.append((len(mount_root.parts), mountpoint / relative))
    if not candidates:
        raise LinuxRunnerInfrastructureError(
            "current cgroup-v2 membership is outside every cgroup2 mount"
        )
    return max(candidates, key=lambda item: item[0])[1].resolve(strict=True)


def _validate_cgroup_parent(
    path: Path,
    *,
    require_current_membership: bool = True,
) -> None:
    filesystem, _mount_options, _super_options = _nearest_mount_record(path)
    if filesystem != "cgroup2":
        raise LinuxRunnerInfrastructureError("cgroup parent must be on a cgroup-v2 filesystem")
    if path.is_symlink() or not path.is_dir():
        raise LinuxRunnerInfrastructureError("cgroup parent must be a non-symlink directory")
    required = {"cpu", "memory", "pids"}
    controllers = set(_read_text(path / "cgroup.controllers").split())
    enabled = {
        item.removeprefix("+") for item in _read_text(path / "cgroup.subtree_control").split()
    }
    if not required.issubset(controllers) or not required.issubset(enabled):
        raise LinuxRunnerInfrastructureError(
            "delegated cgroup parent lacks enabled cpu,memory,pids controllers"
        )
    if not os.access(path, os.W_OK | os.X_OK):
        raise LinuxRunnerInfrastructureError("delegated cgroup parent is not writable")
    if _read_text(path / "cgroup.procs"):
        raise LinuxRunnerInfrastructureError(
            "delegated cgroup parent must not contain supervisor processes"
        )
    if require_current_membership:
        current = _current_cgroup_v2_path()
        try:
            common = Path(os.path.commonpath((str(current), str(path))))
        except ValueError as exc:
            raise LinuxRunnerInfrastructureError(
                "runner and delegated cgroup parent are on different filesystems"
            ) from exc
        if common == Path("/") or not (common / "cgroup.procs").exists():
            raise LinuxRunnerInfrastructureError(
                "runner is outside the delegated cgroup migration boundary"
            )
        if not os.access(common / "cgroup.procs", os.W_OK):
            raise LinuxRunnerInfrastructureError(
                "runner cannot migrate children into the delegated cgroup tree"
            )


def _nearest_mount_record(path: Path) -> tuple[str, set[str], set[str]]:
    resolved = path.resolve(strict=True)
    try:
        lines = Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise LinuxRunnerInfrastructureError(f"cannot read Linux mountinfo: {exc}") from exc
    candidates: list[tuple[int, str, set[str], set[str]]] = []
    for line in lines:
        left, separator, right = line.partition(" - ")
        if not separator:
            continue
        fields = left.split()
        right_fields = right.split()
        if len(fields) < 6 or len(right_fields) < 3:
            continue
        mountpoint = Path(_decode_mount_path(fields[4]))
        try:
            resolved.relative_to(mountpoint)
        except ValueError:
            continue
        candidates.append(
            (
                len(mountpoint.parts),
                right_fields[0],
                set(fields[5].split(",")),
                set(right_fields[2].split(",")),
            )
        )
    if not candidates:
        raise LinuxRunnerInfrastructureError(f"no mount record covers {path}")
    _depth, filesystem, mount_options, super_options = max(candidates, key=lambda item: item[0])
    return filesystem, mount_options, super_options


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="ascii").strip()
    except OSError as exc:
        raise LinuxRunnerInfrastructureError(
            f"kernel control became unavailable: {path}: {exc}"
        ) from exc


def _write_control(path: Path, value: str) -> None:
    try:
        path.write_text(value, encoding="ascii")
    except OSError as exc:
        raise LinuxRunnerInfrastructureError(
            f"cannot configure kernel control {path}: {exc}"
        ) from exc


@dataclass(slots=True)
class _ScratchLease:
    root: Path
    limits: RunnerLimits
    cgroup_parent: Path
    lock_descriptor: int = -1
    invocation: Path | None = None

    def __enter__(self) -> _ScratchLease:
        _validate_scratch_mount(self.root, self.limits)
        lock_path = self.root / ".echo-hardened-verifier.lock"
        self.lock_descriptor = os.open(
            lock_path,
            os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
        )
        try:
            fcntl.flock(self.lock_descriptor, fcntl.LOCK_EX)
            # Candidate tasks may have made scratch descendants unreadable.
            # Kill and prove every stale cgroup empty before touching any of
            # those paths, while the scratch lock serializes all runner cleanup.
            _CgroupRun(self.cgroup_parent, self.limits).recover_stale()
            self._recover_stale()
            name = f"run-{os.urandom(16).hex()}"
            self.invocation = self.root / name
            self.invocation.mkdir(mode=0o700)
            return self
        except Exception:
            os.close(self.lock_descriptor)
            self.lock_descriptor = -1
            raise

    def _recover_stale(self) -> None:
        for child in self.root.iterdir():
            if child.name == ".echo-hardened-verifier.lock":
                continue
            if not (_RUN_NAME_RE.fullmatch(child.name) or child.name.startswith(".reap-")):
                raise LinuxRunnerInfrastructureError(
                    f"scratch mount contains an unowned entry: {child.name}"
                )
            _safe_remove_tree(child, expected_parent=self.root)

    def usage(self) -> tuple[int, int]:
        stats = os.statvfs(self.root)
        used_bytes = (stats.f_blocks - stats.f_bfree) * stats.f_frsize
        used_inodes = stats.f_files - stats.f_ffree
        return max(0, used_bytes), max(0, used_inodes)

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        cleanup_errors: list[BaseException] = []
        cgroups_reaped = False
        try:
            _CgroupRun(self.cgroup_parent, self.limits).recover_stale()
            cgroups_reaped = True
        except BaseException as caught:  # cleanup must retain the primary error
            cleanup_errors.append(caught)
        if self.invocation is not None and cgroups_reaped:
            try:
                _safe_remove_tree(self.invocation, expected_parent=self.root)
            except BaseException as caught:  # cleanup must still release lock
                cleanup_errors.append(caught)
            self.invocation = None
        if self.lock_descriptor >= 0:
            try:
                fcntl.flock(self.lock_descriptor, fcntl.LOCK_UN)
            except OSError as caught:
                cleanup_errors.append(caught)
            try:
                os.close(self.lock_descriptor)
            except OSError as caught:
                cleanup_errors.append(caught)
            self.lock_descriptor = -1
        if cleanup_errors:
            _raise_combined_cleanup_failure(
                "hardened verifier cleanup failed after cgroup/scratch teardown",
                primary=exc if isinstance(exc, BaseException) else None,
                cleanup_errors=cleanup_errors,
            )


def _cleanup_identity(value: os.stat_result) -> tuple[int, int, int]:
    """Identity fields that remain stable while cleanup restores permissions."""

    return value.st_dev, value.st_ino, stat.S_IFMT(value.st_mode)


@dataclass(frozen=True, slots=True)
class _CleanupDirectory:
    name: str
    identity: tuple[int, int, int]


def _chmod_open_cleanup_directory(
    parent_fd: int,
    name: str,
    expected_identity: tuple[int, int, int],
) -> int:
    """Restore and open one directory without following its name as a link."""

    try:
        before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as exc:
        raise LinuxRunnerInfrastructureError(
            "scratch cleanup directory became unavailable"
        ) from exc
    if _cleanup_identity(before) != expected_identity or not stat.S_ISDIR(before.st_mode):
        raise LinuxRunnerInfrastructureError("scratch cleanup directory drifted")
    try:
        os.chmod(name, 0o700, dir_fd=parent_fd, follow_symlinks=False)
        after_chmod = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as exc:
        raise LinuxRunnerInfrastructureError(
            "scratch cleanup directory permissions cannot be restored"
        ) from exc
    if _cleanup_identity(after_chmod) != expected_identity:
        raise LinuxRunnerInfrastructureError(
            "scratch cleanup directory drifted while restoring permissions"
        )
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
            dir_fd=parent_fd,
        )
    except OSError as exc:
        raise LinuxRunnerInfrastructureError("scratch cleanup directory is unsafe") from exc
    if _cleanup_identity(os.fstat(descriptor)) != expected_identity:
        os.close(descriptor)
        raise LinuxRunnerInfrastructureError("scratch cleanup directory drifted while opening")
    return descriptor


def _unlink_cleanup_entry(
    parent_fd: int,
    name: str,
    expected_identity: tuple[int, int, int],
) -> None:
    try:
        observed = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if _cleanup_identity(observed) != expected_identity or stat.S_ISDIR(observed.st_mode):
            raise LinuxRunnerInfrastructureError("scratch cleanup entry drifted")
        os.unlink(name, dir_fd=parent_fd)
    except LinuxRunnerInfrastructureError:
        raise
    except OSError as exc:
        raise LinuxRunnerInfrastructureError("scratch cleanup entry cannot be removed") from exc


def _remove_tree_at(
    parent_fd: int,
    name: str,
    root_metadata: os.stat_result,
    *,
    maximum_inodes: int = DEFAULT_LIMITS.scratch_max_inodes,
) -> None:
    """Delete a stopped candidate tree with bounded memory and descriptor use."""

    root_identity = _cleanup_identity(root_metadata)
    if root_metadata.st_dev != os.fstat(parent_fd).st_dev:
        raise LinuxRunnerInfrastructureError("scratch cleanup root crosses a filesystem boundary")
    current_fd = _chmod_open_cleanup_directory(parent_fd, name, root_identity)
    root_node = _CleanupDirectory(name=name, identity=root_identity)
    stack: list[tuple[_CleanupDirectory, list[_CleanupDirectory]]] = []
    visited = 1

    def scan_current() -> list[_CleanupDirectory]:
        nonlocal visited
        children: list[_CleanupDirectory] = []
        try:
            with os.scandir(current_fd) as entries:
                for entry in entries:
                    visited += 1
                    if visited > maximum_inodes:
                        raise LinuxRunnerInfrastructureError(
                            "scratch cleanup exceeded its inode bound"
                        )
                    try:
                        metadata = os.stat(
                            entry.name,
                            dir_fd=current_fd,
                            follow_symlinks=False,
                        )
                    except OSError as exc:
                        raise LinuxRunnerInfrastructureError(
                            "scratch cleanup entry became unavailable"
                        ) from exc
                    if metadata.st_dev != root_identity[0]:
                        raise LinuxRunnerInfrastructureError(
                            "scratch cleanup entry crosses a filesystem boundary"
                        )
                    identity = _cleanup_identity(metadata)
                    if stat.S_ISDIR(metadata.st_mode):
                        children.append(_CleanupDirectory(name=entry.name, identity=identity))
                    else:
                        _unlink_cleanup_entry(current_fd, entry.name, identity)
        except LinuxRunnerInfrastructureError:
            raise
        except OSError as exc:
            raise LinuxRunnerInfrastructureError(
                "scratch cleanup directory cannot be scanned"
            ) from exc
        return children

    try:
        stack.append((root_node, scan_current()))
        while stack:
            node, children = stack[-1]
            if children:
                child = children.pop()
                child_fd = _chmod_open_cleanup_directory(
                    current_fd,
                    child.name,
                    child.identity,
                )
                os.close(current_fd)
                current_fd = child_fd
                stack.append((child, scan_current()))
                continue

            stack.pop()
            if stack:
                try:
                    reopened_parent_fd = os.open(
                        "..",
                        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                        dir_fd=current_fd,
                    )
                except OSError as exc:
                    raise LinuxRunnerInfrastructureError(
                        "scratch cleanup parent cannot be reopened"
                    ) from exc
                expected_parent = stack[-1][0]
                if _cleanup_identity(os.fstat(reopened_parent_fd)) != expected_parent.identity:
                    os.close(reopened_parent_fd)
                    raise LinuxRunnerInfrastructureError(
                        "scratch cleanup parent drifted while ascending"
                    )
                os.close(current_fd)
                current_fd = reopened_parent_fd
                try:
                    observed = os.stat(
                        node.name,
                        dir_fd=current_fd,
                        follow_symlinks=False,
                    )
                    if _cleanup_identity(observed) != node.identity or not stat.S_ISDIR(
                        observed.st_mode
                    ):
                        raise LinuxRunnerInfrastructureError(
                            "scratch cleanup directory drifted before removal"
                        )
                    os.rmdir(node.name, dir_fd=current_fd)
                except LinuxRunnerInfrastructureError:
                    raise
                except OSError as exc:
                    raise LinuxRunnerInfrastructureError(
                        "scratch cleanup directory cannot be removed"
                    ) from exc

        try:
            observed_root = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if _cleanup_identity(observed_root) != root_identity or not stat.S_ISDIR(
                observed_root.st_mode
            ):
                raise LinuxRunnerInfrastructureError("scratch cleanup root drifted before removal")
            os.rmdir(name, dir_fd=parent_fd)
        except LinuxRunnerInfrastructureError:
            raise
        except OSError as exc:
            raise LinuxRunnerInfrastructureError("scratch cleanup root cannot be removed") from exc
    finally:
        os.close(current_fd)


def _safe_remove_tree(path: Path, *, expected_parent: Path) -> None:
    if path.parent != expected_parent or path.name in {"", ".", ".."}:
        raise LinuxRunnerInfrastructureError("refusing broad scratch cleanup target")
    try:
        parent_metadata = expected_parent.lstat()
    except FileNotFoundError:
        raise LinuxRunnerInfrastructureError("scratch cleanup parent disappeared") from None
    if not stat.S_ISDIR(parent_metadata.st_mode) or expected_parent.is_symlink():
        raise LinuxRunnerInfrastructureError("scratch cleanup parent is unsafe")
    try:
        parent_fd = os.open(
            expected_parent,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
    except OSError as exc:
        raise LinuxRunnerInfrastructureError("scratch cleanup parent cannot be opened") from exc
    try:
        if _tree_stat_identity(os.fstat(parent_fd)) != _tree_stat_identity(parent_metadata):
            raise LinuxRunnerInfrastructureError("scratch cleanup parent drifted while opening")
        try:
            observed = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise LinuxRunnerInfrastructureError(
                "scratch cleanup target became unavailable"
            ) from exc
        if stat.S_ISDIR(observed.st_mode):
            _remove_tree_at(parent_fd, path.name, observed)
        else:
            _unlink_cleanup_entry(parent_fd, path.name, _cleanup_identity(observed))
    finally:
        os.close(parent_fd)


@dataclass(slots=True)
class _CgroupRun:
    parent: Path
    limits: RunnerLimits
    path: Path | None = None
    _baseline_events: dict[str, int] = field(default_factory=dict)

    def create(self, name: str) -> None:
        _validate_cgroup_parent(self.parent)
        self.recover_stale()
        path = self.parent / name
        try:
            path.mkdir(mode=0o700)
        except OSError as exc:
            raise LinuxRunnerInfrastructureError(f"cannot create verifier cgroup: {exc}") from exc
        self.path = path
        try:
            required = (
                "cgroup.kill",
                "cgroup.procs",
                "cgroup.events",
                "cpu.max",
                "cpu.stat",
                "memory.max",
                "memory.swap.max",
                "memory.events",
                "pids.max",
                "pids.events",
            )
            missing = [item for item in required if not (path / item).exists()]
            if missing:
                raise LinuxRunnerInfrastructureError(
                    f"new verifier cgroup lacks kernel controls: {missing}"
                )
            _write_control(path / "memory.max", str(self.limits.memory_max_bytes))
            _write_control(path / "memory.swap.max", "0")
            if (path / "memory.oom.group").exists():
                _write_control(path / "memory.oom.group", "1")
            _write_control(path / "pids.max", str(self.limits.pids_max))
            _write_control(path / "cpu.max", "100000 100000")
            self._assert_readback()
            self._baseline_events = {
                **{
                    f"memory.{key}": value
                    for key, value in _key_values(path / "memory.events").items()
                },
                **{
                    f"pids.{key}": value for key, value in _key_values(path / "pids.events").items()
                },
            }
        except Exception:
            self.kill_and_reap(ignore_missing=True)
            raise

    def _assert_readback(self) -> None:
        assert self.path is not None
        expected = {
            "memory.max": str(self.limits.memory_max_bytes),
            "memory.swap.max": "0",
            "pids.max": str(self.limits.pids_max),
            "cpu.max": "100000 100000",
        }
        for name, value in expected.items():
            if _read_text(self.path / name) != value:
                raise LinuxRunnerInfrastructureError(f"kernel refused exact {name} limit")

    def process_fd(self) -> int:
        if self.path is None:
            raise LinuxRunnerInfrastructureError("verifier cgroup was not created")
        return os.open(self.path / "cgroup.procs", os.O_WRONLY | os.O_CLOEXEC)

    def metrics(self) -> tuple[int, int, int, str | None]:
        if self.path is None:
            raise LinuxRunnerInfrastructureError("verifier cgroup was not created")
        cpu = _key_values(self.path / "cpu.stat").get("usage_usec", 0)
        memory_peak_path = self.path / "memory.peak"
        memory_peak = (
            _integer_control(memory_peak_path)
            if memory_peak_path.exists()
            else _integer_control(self.path / "memory.current")
        )
        pids_peak_path = self.path / "pids.peak"
        pids_peak = (
            _integer_control(pids_peak_path)
            if pids_peak_path.exists()
            else _integer_control(self.path / "pids.current")
        )
        memory_events = _key_values(self.path / "memory.events")
        pids_events = _key_values(self.path / "pids.events")
        reason: str | None = None
        if memory_events.get("oom_kill", 0) > self._baseline_events.get("memory.oom_kill", 0):
            reason = "memory_oom_kill"
        elif memory_events.get("max", 0) > self._baseline_events.get("memory.max", 0):
            reason = "memory_limit"
        elif pids_events.get("max", 0) > self._baseline_events.get("pids.max", 0):
            reason = "pids_limit"
        elif cpu > int(self.limits.cpu_max_seconds * 1_000_000):
            reason = "cpu_limit"
        return cpu, memory_peak, pids_peak, reason

    def recover_stale(self) -> None:
        _validate_cgroup_parent(self.parent)
        observed_children = 0
        try:
            with os.scandir(self.parent) as entries:
                for entry in entries:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                    observed_children += 1
                    if observed_children > self.limits.scratch_max_inodes:
                        raise LinuxRunnerInfrastructureError(
                            "delegated cgroup parent exceeds its stale-child bound"
                        )
                    if not _RUN_NAME_RE.fullmatch(entry.name):
                        raise LinuxRunnerInfrastructureError(
                            f"delegated cgroup parent contains an unowned child: {entry.name}"
                        )
                    self._kill_path(self.parent / entry.name, ignore_missing=False)
        except LinuxRunnerInfrastructureError:
            raise
        except OSError as exc:
            raise LinuxRunnerInfrastructureError(
                f"delegated cgroup parent cannot be enumerated: {exc}"
            ) from exc

    def kill_and_reap(self, *, ignore_missing: bool = False) -> None:
        path = self.path
        if path is None:
            return
        try:
            self._kill_path(path, ignore_missing=ignore_missing)
        finally:
            self.path = None

    def _kill_path(self, path: Path, *, ignore_missing: bool) -> None:
        if path.parent != self.parent or not _RUN_NAME_RE.fullmatch(path.name):
            raise LinuxRunnerInfrastructureError("refusing broad cgroup cleanup target")
        if not path.exists():
            if ignore_missing:
                return
            raise LinuxRunnerInfrastructureError("verifier cgroup disappeared before cleanup")
        kill_path = path / "cgroup.kill"
        try:
            kill_path.write_text("1", encoding="ascii")
        except OSError as exc:
            if not (ignore_missing and exc.errno == errno.ENOENT):
                raise LinuxRunnerInfrastructureError(f"cgroup.kill failed: {exc}") from exc
        deadline = time.monotonic() + self.limits.reap_timeout_seconds
        while time.monotonic() < deadline:
            events = _key_values(path / "cgroup.events")
            if events.get("populated") == 0:
                break
            time.sleep(0.01)
        else:
            raise LinuxRunnerInfrastructureError("cgroup remained populated after cgroup.kill")
        try:
            path.rmdir()
        except OSError as exc:
            raise LinuxRunnerInfrastructureError(f"verifier cgroup cleanup failed: {exc}") from exc


def _read_process_cgroup_v2_membership(pid: int) -> str:
    try:
        lines = Path(f"/proc/{pid}/cgroup").read_text(encoding="ascii").splitlines()
    except OSError as exc:
        raise LinuxRunnerInfrastructureError(
            f"cannot read cgroup membership for live probe process: {exc}"
        ) from exc
    memberships = [line.partition("::")[2] for line in lines if "::" in line]
    if len(memberships) != 1 or not memberships[0].startswith("/"):
        raise LinuxRunnerInfrastructureError(
            "live probe process has ambiguous cgroup-v2 membership"
        )
    return memberships[0]


def _exercise_cgroup_process_kill(
    attestation: RunnerAttestation,
    cgroup: _CgroupRun,
    limit_headroom: Mapping[str, Mapping[str, int | str | bool]],
) -> dict[str, Any]:
    """Attach a fixed trusted process, then prove cgroup.kill reaps it."""

    if cgroup.path is None:
        raise LinuxRunnerInfrastructureError("live-probe cgroup was not created")
    helper_source = (
        "import json,resource,signal\n"
        "k={'cpu_seconds':resource.RLIMIT_CPU,'descriptors':resource.RLIMIT_NOFILE,"
        "'file_size_bytes':resource.RLIMIT_FSIZE,'core_bytes':resource.RLIMIT_CORE}\n"
        "if hasattr(resource,'RLIMIT_AS'):k['address_space_bytes']=resource.RLIMIT_AS\n"
        "print(json.dumps({n:list(resource.getrlimit(v)) for n,v in k.items()},"
        "sort_keys=True,separators=(',',':')),flush=True)\n"
        "while True:signal.pause()\n"
    )
    cgroup_fd = cgroup.process_fd()
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            [
                str(attestation.launcher_executable_path),
                "-I",
                "-c",
                helper_source,
            ],
            cwd=attestation.runtime_root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env={"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"},
            close_fds=True,
            pass_fds=(cgroup_fd,),
            start_new_session=True,
            preexec_fn=_preexec_setup(cgroup_fd, attestation.limits),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        startup_error = LinuxRunnerInfrastructureError(
            f"cgroup live-probe helper failed to start: {exc}"
        )
        try:
            cgroup.kill_and_reap(ignore_missing=True)
        except BaseException as cleanup_error:
            _raise_combined_cleanup_failure(
                "cgroup live-probe startup and teardown both failed",
                primary=startup_error,
                cleanup_errors=[cleanup_error],
            )
        raise startup_error from exc
    finally:
        os.close(cgroup_fd)

    try:
        if process.stdout is None:
            raise LinuxRunnerInfrastructureError(
                "cgroup live-probe helper output pipe is unavailable"
            )
        os.set_blocking(process.stdout.fileno(), False)
        output = bytearray()
        deadline = time.monotonic() + min(5.0, attestation.limits.reap_timeout_seconds)
        while b"\n" not in output and time.monotonic() < deadline:
            if process.poll() is not None:
                break
            try:
                chunk = os.read(process.stdout.fileno(), 4097 - len(output))
            except BlockingIOError:
                time.sleep(0.01)
                continue
            if not chunk:
                break
            output.extend(chunk)
            if len(output) > 4096:
                raise LinuxRunnerInfrastructureError(
                    "cgroup live-probe helper output exceeded its bound"
                )
        raw_limits, separator, trailing = bytes(output).partition(b"\n")
        if not separator or trailing:
            raise LinuxRunnerInfrastructureError(
                "cgroup live-probe helper did not report one bounded limit frame"
            )
        try:
            effective_limits = json.loads(raw_limits)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LinuxRunnerInfrastructureError(
                "cgroup live-probe helper limit frame is invalid"
            ) from exc
        expected_limits = {
            label: [int(record["effective_child"]), int(record["effective_child"])]
            for label, record in limit_headroom.items()
        }
        if effective_limits != expected_limits:
            raise LinuxRunnerInfrastructureError(
                "cgroup live-probe helper process-limit readback is invalid"
            )
        if process.poll() is not None:
            raise LinuxRunnerInfrastructureError(
                "cgroup live-probe helper exited before membership verification"
            )
        member_pids = {
            int(value) for value in _read_text(cgroup.path / "cgroup.procs").splitlines() if value
        }
        if member_pids != {process.pid}:
            raise LinuxRunnerInfrastructureError(
                "cgroup live-probe membership did not contain exactly its helper"
            )
        membership = _read_process_cgroup_v2_membership(process.pid)
        if PurePosixPath(membership).name != cgroup.path.name:
            raise LinuxRunnerInfrastructureError(
                "cgroup live-probe helper reports the wrong membership"
            )
        child_name = cgroup.path.name
        cgroup.kill_and_reap()
        try:
            returncode = process.wait(timeout=attestation.limits.reap_timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            raise LinuxRunnerInfrastructureError(
                "cgroup live-probe helper was not reaped after cgroup.kill"
            ) from exc
        if returncode != -signal.SIGKILL:
            raise LinuxRunnerInfrastructureError(
                "cgroup live-probe helper did not terminate by kernel SIGKILL"
            )
        return {
            "child_name": child_name,
            "helper_pid": process.pid,
            "membership": membership,
            "membership_verified": True,
            "trusted_helper_effective_process_limits": effective_limits,
            "process_limit_scope": "trusted-live-probe-helper-preexec",
            "cgroup_kill_exercised": True,
            "helper_reaped": True,
            "helper_returncode": returncode,
            "populated_after_kill": 0,
            "child_removed": not (attestation.cgroup_parent / child_name).exists(),
        }
    except Exception as caught:
        cleanup_errors: list[BaseException] = []
        try:
            cgroup.kill_and_reap(ignore_missing=True)
        except BaseException as cleanup_error:
            cleanup_errors.append(cleanup_error)
        try:
            if process.poll() is None:
                process.kill()
        except BaseException as cleanup_error:
            cleanup_errors.append(cleanup_error)
        try:
            process.wait(timeout=attestation.limits.reap_timeout_seconds)
        except BaseException as cleanup_error:
            cleanup_errors.append(cleanup_error)
        if process.stdout is not None and not process.stdout.closed:
            try:
                process.stdout.close()
            except BaseException as cleanup_error:
                cleanup_errors.append(cleanup_error)
        if cleanup_errors:
            _raise_combined_cleanup_failure(
                "cgroup live-probe execution and teardown failed",
                primary=caught,
                cleanup_errors=cleanup_errors,
            )
        raise
    finally:
        if process.stdout is not None:
            process.stdout.close()


def _key_values(path: Path) -> dict[str, int]:
    result: dict[str, int] = {}
    for line in _read_text(path).splitlines():
        parts = line.split()
        if len(parts) != 2:
            raise LinuxRunnerInfrastructureError(f"invalid kernel counter format: {path}")
        try:
            result[parts[0]] = int(parts[1])
        except ValueError as exc:
            raise LinuxRunnerInfrastructureError(f"invalid kernel counter value: {path}") from exc
    return result


def _integer_control(path: Path) -> int:
    try:
        return int(_read_text(path))
    except ValueError as exc:
        raise LinuxRunnerInfrastructureError(f"invalid integer kernel control: {path}") from exc


_BOOTSTRAP = r"""
from __future__ import annotations
import json, os, shutil, socket, sys
from pathlib import Path

def denied(call):
    try:
        call()
    except BaseException:
        return True
    return False

outside_read, original_write, outside_write, loopback_port, controller_pid, *verifier_args = sys.argv[1:]
probe = {
    "outside_read_denied": denied(lambda: Path(outside_read).read_bytes()),
    "original_workspace_write_denied": denied(lambda: Path(original_write).write_text("changed")),
    "outside_write_denied": denied(lambda: Path(outside_write).write_text("escaped")),
    "snapshot_write_denied": denied(lambda: Path("/workspace/.echo-probe-write").write_text("x")),
    "loopback_network_denied": denied(lambda: socket.create_connection(("127.0.0.1", int(loopback_port)), .2)),
    "external_network_denied": denied(lambda: socket.create_connection(("1.1.1.1", 53), .2)),
    "host_secret_scrubbed": os.environ.get("ECHO_SANDBOX_PROBE_SECRET") is None,
    "controller_pid_hidden": not Path("/proc", controller_pid).exists(),
    "uid": os.getuid(),
    "gid": os.getgid(),
    "uid_map": Path("/proc/self/uid_map").read_text().strip(),
    "gid_map": Path("/proc/self/gid_map").read_text().strip(),
    "cgroup": Path("/proc/self/cgroup").read_text().strip(),
}
Path("/work/probe-write").write_text("ok", encoding="utf-8")
probe["scratch_write_succeeded"] = Path("/work/probe-write").read_text() == "ok"
print("__ECHO_HARDENED_SANDBOX_READY_v2__" + json.dumps(probe, sort_keys=True, separators=(",", ":")), flush=True)
source = sys.stdin.read()
if len(source.encode("utf-8")) > 1048576:
    raise SystemExit("verifier source too large")
shutil.copytree("/workspace", "/work/workspace", symlinks=False)
os.chdir("/work/workspace")
sys.dont_write_bytecode = True
sys.argv = ["<echo-hidden-verifier>", *[part.replace("/workspace", "/work/workspace") for part in verifier_args]]
namespace = {"__name__": "__main__", "__file__": "<echo-hidden-verifier>"}
exec(compile(source, "<echo-hidden-verifier>", "exec"), namespace, namespace)
""".lstrip()

_WORKER_BOOTSTRAP = r"""
from __future__ import annotations
import json, os, socket, struct, sys
from pathlib import Path

def denied(call):
    try:
        call()
    except BaseException:
        return True
    return False

(
    outside_read,
    original_write,
    outside_write,
    loopback_port,
    controller_pid,
    supervisor_pid,
    control_dev,
    control_ino,
    controller_protocol_dev,
    controller_protocol_ino,
    host_namespaces_json,
    worker_path,
    has_challenge,
) = sys.argv[1:]

host_namespaces = json.loads(host_namespaces_json)
namespace_names = ("user", "pid", "mnt", "net", "ipc", "uts", "cgroup")
inner_namespaces = {
    name: os.readlink("/proc/self/ns/" + name)
    for name in namespace_names
}

def unix_stream(descriptor):
    duplicate = os.dup(descriptor)
    candidate = socket.socket(fileno=duplicate)
    try:
        return candidate.family == socket.AF_UNIX and candidate.type & socket.SOCK_STREAM == socket.SOCK_STREAM
    finally:
        candidate.close()

def descriptor_identities():
    identities = []
    for raw_descriptor in os.listdir("/proc/self/fd"):
        try:
            observed = os.fstat(int(raw_descriptor))
        except (OSError, ValueError):
            continue
        identities.append((observed.st_dev, observed.st_ino))
    return identities

forbidden_channel_identities = {
    (int(control_dev), int(control_ino)),
    (int(controller_protocol_dev), int(controller_protocol_ino)),
}

probe = {
    "outside_read_denied": denied(lambda: Path(outside_read).read_bytes()),
    "original_workspace_write_denied": denied(lambda: Path(original_write).write_text("changed")),
    "outside_write_denied": denied(lambda: Path(outside_write).write_text("escaped")),
    "snapshot_write_denied": denied(lambda: Path("/workspace/.echo-probe-write").write_text("x")),
    "challenge_write_denied": denied(lambda: Path("/challenge/.echo-probe-write").write_text("x")),
    "loopback_network_denied": denied(lambda: socket.create_connection(("127.0.0.1", int(loopback_port)), .2)),
    "external_network_denied": denied(lambda: socket.create_connection(("1.1.1.1", 53), .2)),
    "host_secret_scrubbed": os.environ.get("ECHO_SANDBOX_PROBE_SECRET") is None,
    "controller_pid_hidden": not Path("/proc", controller_pid).exists(),
    "supervisor_pid_hidden": not Path("/proc", supervisor_pid).exists(),
    "controller_channels_absent": forbidden_channel_identities.isdisjoint(
        descriptor_identities()
    ),
    "candidate_protocol_is_unix_stream": unix_stream(3),
    "probe_pipe_present": not denied(lambda: os.fstat(4)),
    "private_namespaces": (
        set(host_namespaces) == set(namespace_names)
        and all(inner_namespaces[name] != host_namespaces[name] for name in namespace_names)
    ),
    "namespace_ids": inner_namespaces,
    "uid": os.getuid(),
    "gid": os.getgid(),
    "uid_map": Path("/proc/self/uid_map").read_text().strip(),
    "gid_map": Path("/proc/self/gid_map").read_text().strip(),
    "cgroup": Path("/proc/self/cgroup").read_text().strip(),
}
Path("/work/probe-write").write_text("ok", encoding="utf-8")
probe["scratch_write_succeeded"] = Path("/work/probe-write").read_text() == "ok"
payload = json.dumps(probe, sort_keys=True, separators=(",", ":")).encode()
frame = struct.pack("!I", len(payload)) + payload
offset = 0
while offset < len(frame):
    offset += os.write(4, frame[offset:])
os.close(4)
for raw_descriptor in os.listdir("/proc/self/fd"):
    try:
        descriptor = int(raw_descriptor)
    except ValueError:
        continue
    if descriptor > 3:
        try:
            os.close(descriptor)
        except OSError:
            pass
os.set_inheritable(3, True)
arguments = [
    sys.executable,
    "-I",
    worker_path,
    "--candidate-protocol-fd",
    "3",
    "--workspace",
    "/workspace",
]
if has_challenge == "1":
    arguments.extend(("--challenge-root", "/challenge"))
os.execv(sys.executable, arguments)
""".lstrip()


def _minimal_environment() -> dict[str, str]:
    return {
        "HOME": "/work/home",
        "TMPDIR": "/work/tmp",
        "TMP": "/work/tmp",
        "TEMP": "/work/tmp",
        "XDG_CACHE_HOME": "/work/cache",
        "XDG_CONFIG_HOME": "/work/config",
        "XDG_DATA_HOME": "/work/data",
        "LC_ALL": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "ECHO_BEHAVIORAL_EVAL": "1",
    }


def _bubblewrap_command(
    attestation: RunnerAttestation,
    *,
    snapshot: Path,
    work: Path,
    seccomp_fd: int,
    verifier_args: Sequence[str],
    probe_args: Sequence[str],
) -> list[str]:
    command = [
        str(attestation.bubblewrap_path),
        "--die-with-parent",
        "--new-session",
        "--unshare-all",
        "--uid",
        "65534",
        "--gid",
        "65534",
        "--cap-drop",
        "ALL",
        "--clearenv",
        "--ro-bind",
        str(attestation.runtime_root),
        "/",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--ro-bind",
        str(snapshot),
        "/workspace",
        "--bind",
        str(work),
        "/work",
        "--seccomp",
        str(seccomp_fd),
    ]
    for key, value in sorted(_minimal_environment().items()):
        command.extend(("--setenv", key, value))
    command.extend(
        (
            "--chdir",
            "/workspace",
            "--",
            attestation.runtime_python,
            "-I",
            "-c",
            _BOOTSTRAP,
            *probe_args,
            *verifier_args,
        )
    )
    return command


def _worker_bubblewrap_command(
    attestation: RunnerAttestation,
    *,
    workspace_snapshot: Path,
    challenge_snapshot: Path,
    work: Path,
    seccomp_fd: int,
    probe_args: Sequence[str],
    has_challenge: bool,
) -> list[str]:
    if seccomp_fd != 5:
        raise LinuxRunnerInfrastructureError(
            "candidate seccomp descriptor was not normalized to fd 5"
        )
    command = [
        str(attestation.bubblewrap_path),
        "--die-with-parent",
        "--new-session",
        "--unshare-all",
        "--uid",
        "65534",
        "--gid",
        "65534",
        "--cap-drop",
        "ALL",
        "--clearenv",
        "--ro-bind",
        str(attestation.runtime_root),
        "/",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--ro-bind",
        str(workspace_snapshot),
        "/workspace",
        "--ro-bind",
        str(challenge_snapshot),
        "/challenge",
        "--ro-bind",
        str(attestation.worker_path),
        "/echo-trusted/trusted_verifier_worker.py",
        "--bind",
        str(work),
        "/work",
        "--seccomp",
        "5",
    ]
    for key, value in sorted(_minimal_environment().items()):
        command.extend(("--setenv", key, value))
    command.extend(
        (
            "--chdir",
            "/workspace",
            "--",
            attestation.runtime_python,
            "-I",
            "-c",
            _WORKER_BOOTSTRAP,
            *probe_args,
            "/echo-trusted/trusted_verifier_worker.py",
            "1" if has_challenge else "0",
        )
    )
    return command


def _rlimit_json_value(value: int) -> int | str:
    return "infinity" if value == resource.RLIM_INFINITY else int(value)


def _process_limit_headroom(
    limits: RunnerLimits,
    *,
    cpu_seconds: int | None = None,
) -> dict[str, dict[str, int | str | bool]]:
    """Prove the parent can install every process limit advertised in evidence."""

    desired_cpu = max(1, math.ceil(limits.cpu_max_seconds)) if cpu_seconds is None else cpu_seconds
    specifications: list[tuple[str, int, int]] = [
        ("cpu_seconds", resource.RLIMIT_CPU, desired_cpu),
        ("descriptors", resource.RLIMIT_NOFILE, limits.fds_max),
        ("file_size_bytes", resource.RLIMIT_FSIZE, limits.file_size_max_bytes),
    ]
    if hasattr(resource, "RLIMIT_AS"):
        specifications.append(("address_space_bytes", resource.RLIMIT_AS, limits.memory_max_bytes))
    evidence: dict[str, dict[str, int | str | bool]] = {}
    for label, kind, desired in specifications:
        soft, hard = resource.getrlimit(kind)
        sufficient = hard == resource.RLIM_INFINITY or hard >= desired
        evidence[label] = {
            "inherited_soft": _rlimit_json_value(soft),
            "inherited_hard": _rlimit_json_value(hard),
            "required": desired,
            "effective_child": desired if sufficient else max(0, hard),
            "hard_sufficient": sufficient,
        }
        if not sufficient:
            raise LinuxRunnerInfrastructureError(
                f"host {label} hard limit is below the attested requirement"
            )
    evidence["core_bytes"] = {
        "inherited_soft": _rlimit_json_value(resource.getrlimit(resource.RLIMIT_CORE)[0]),
        "inherited_hard": _rlimit_json_value(resource.getrlimit(resource.RLIMIT_CORE)[1]),
        "required": 0,
        "effective_child": 0,
        "hard_sufficient": True,
    }
    return evidence


def _lower_process_limit(kind: int, desired: int) -> None:
    """Install a limit without ever raising an inherited hard ceiling."""

    _soft, inherited_hard = resource.getrlimit(kind)
    bounded = desired if inherited_hard == resource.RLIM_INFINITY else min(desired, inherited_hard)
    resource.setrlimit(kind, (bounded, bounded))


def _preexec_setup(cgroup_procs_fd: int, limits: RunnerLimits) -> Callable[[], None]:
    def setup() -> None:
        _lower_process_limit(resource.RLIMIT_CORE, 0)
        cpu = max(1, math.ceil(limits.cpu_max_seconds))
        _lower_process_limit(resource.RLIMIT_CPU, cpu)
        _lower_process_limit(resource.RLIMIT_NOFILE, limits.fds_max)
        _lower_process_limit(
            resource.RLIMIT_FSIZE,
            limits.file_size_max_bytes,
        )
        # RLIMIT_NPROC is keyed by the outer real UID, so it also counts the
        # trusted controller and unrelated runner services.  Using it here can
        # fire before the invocation-local pids.max controller and makes the
        # reason nondeterministic.  The delegated cgroup is the hard process
        # boundary and is checked continuously below.
        if hasattr(resource, "RLIMIT_AS"):
            _lower_process_limit(
                resource.RLIMIT_AS,
                limits.memory_max_bytes,
            )
        try:
            os.write(cgroup_procs_fd, b"0")
        finally:
            with suppress(OSError):
                os.close(cgroup_procs_fd)

    return setup


def _append_bounded(buffer: bytearray, chunk: bytes, maximum: int) -> bool:
    remaining = maximum - len(buffer)
    if remaining > 0:
        buffer.extend(chunk[:remaining])
    return len(chunk) > remaining


def _collect_process(
    process: subprocess.Popen[bytes],
    *,
    input_bytes: bytes,
    cgroup: _CgroupRun,
    scratch: _ScratchLease,
    timeout_seconds: float,
    limits: RunnerLimits,
    termination_probe: Callable[[], str | None] | None = None,
) -> tuple[int, bytes, bytes, bool, RunResourceEvidence]:
    selector: selectors.BaseSelector | None = None
    streams: dict[int, tuple[str, IO[bytes]]] = {}
    stdout = bytearray()
    stderr = bytearray()
    input_offset = 0
    deadline = time.monotonic() + min(timeout_seconds, limits.wall_max_seconds)
    termination_reason: str | None = None
    main_exited = False
    cpu_peak = memory_peak = pids_peak = 0
    raw_returncode: int | None = None
    primary_error: BaseException | None = None
    try:
        if process.stdin is None or process.stdout is None or process.stderr is None:
            raise LinuxRunnerInfrastructureError("sandbox process pipes were not provisioned")
        selector = selectors.DefaultSelector()
        for name, stream, registration_events in (
            ("stdin", process.stdin, selectors.EVENT_WRITE),
            ("stdout", process.stdout, selectors.EVENT_READ),
            ("stderr", process.stderr, selectors.EVENT_READ),
        ):
            descriptor = stream.fileno()
            os.set_blocking(descriptor, False)
            selector.register(descriptor, registration_events, name)
            streams[descriptor] = (name, stream)
        while selector.get_map() or process.poll() is None:
            now = time.monotonic()
            if termination_reason is None and now >= deadline:
                termination_reason = "wall_timeout"
            if termination_reason is None and termination_probe is not None:
                termination_reason = termination_probe()
            if cgroup.path is not None:
                cpu, memory, pids, resource_reason = cgroup.metrics()
                cpu_peak = max(cpu_peak, cpu)
                memory_peak = max(memory_peak, memory)
                pids_peak = max(pids_peak, pids)
                if termination_reason is None and resource_reason is not None:
                    termination_reason = resource_reason
            if process.poll() is not None and not main_exited:
                main_exited = True
                cgroup.kill_and_reap()
            if termination_reason is not None and not main_exited:
                cgroup.kill_and_reap()
                main_exited = True
            ready_events = selector.select(0.02)
            for key, _mask in ready_events:
                descriptor = int(key.fd)
                name, stream = streams[descriptor]
                if name == "stdin":
                    if input_offset >= len(input_bytes):
                        selector.unregister(descriptor)
                        stream.close()
                        continue
                    try:
                        written = os.write(
                            descriptor, input_bytes[input_offset : input_offset + 64 * 1024]
                        )
                    except BlockingIOError:
                        continue
                    except BrokenPipeError:
                        selector.unregister(descriptor)
                        stream.close()
                        continue
                    input_offset += written
                    if input_offset >= len(input_bytes):
                        selector.unregister(descriptor)
                        stream.close()
                else:
                    try:
                        chunk = os.read(descriptor, 64 * 1024)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        selector.unregister(descriptor)
                        stream.close()
                        continue
                    overflow = _append_bounded(
                        stdout if name == "stdout" else stderr,
                        chunk,
                        limits.stdout_max_bytes if name == "stdout" else limits.stderr_max_bytes,
                    )
                    if overflow and termination_reason is None:
                        termination_reason = f"{name}_limit"
            if main_exited and not ready_events:
                # Descendants are already killed; pipes should reach EOF promptly.
                for descriptor, (name, stream) in list(streams.items()):
                    if name == "stdin" or descriptor not in selector.get_map():
                        continue
                    try:
                        chunk = os.read(descriptor, 64 * 1024)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        selector.unregister(descriptor)
                        stream.close()
        try:
            raw_returncode = process.wait(timeout=limits.reap_timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            raise LinuxRunnerInfrastructureError(
                "sandbox leader did not reap after its cgroup was killed"
            ) from exc
    except BaseException as caught:
        primary_error = caught
        raise
    finally:
        cleanup_errors: list[BaseException] = []
        if selector is not None:
            try:
                selector.close()
            except BaseException as caught:
                cleanup_errors.append(caught)
        if cgroup.path is not None:
            try:
                cgroup.kill_and_reap(ignore_missing=True)
            except BaseException as caught:
                cleanup_errors.append(caught)
        try:
            process.wait(timeout=limits.reap_timeout_seconds)
        except subprocess.TimeoutExpired as caught:
            cleanup_errors.append(caught)
            with suppress(ProcessLookupError, PermissionError):
                os.killpg(process.pid, signal.SIGKILL)
            try:
                process.wait(timeout=limits.reap_timeout_seconds)
            except BaseException as second_wait_error:
                cleanup_errors.append(second_wait_error)
        except BaseException as caught:
            cleanup_errors.append(caught)
        for final_stream in (process.stdin, process.stdout, process.stderr):
            if final_stream is not None and not final_stream.closed:
                try:
                    final_stream.close()
                except BaseException as caught:
                    cleanup_errors.append(caught)
        if cleanup_errors:
            _raise_combined_cleanup_failure(
                "sandbox process collection and teardown failed",
                primary=primary_error,
                cleanup_errors=cleanup_errors,
            )
    used_bytes, used_inodes = scratch.usage()
    if used_bytes > limits.scratch_max_bytes or used_inodes > limits.scratch_max_inodes:
        raise LinuxRunnerInfrastructureError("scratch hard-cap attestation was violated")
    timed_out = termination_reason == "wall_timeout"
    if timed_out:
        returncode = 124
    elif termination_reason in {"stdout_limit", "stderr_limit"}:
        returncode = OUTPUT_LIMIT_EXIT
    elif termination_reason == "protocol_limit":
        returncode = PROTOCOL_LIMIT_EXIT
    elif termination_reason is not None:
        returncode = RESOURCE_LIMIT_EXIT
    else:
        if raw_returncode is None:
            raise LinuxRunnerInfrastructureError(
                "sandbox process collector has no trusted return code"
            )
        returncode = int(raw_returncode)
    evidence = RunResourceEvidence(
        cpu_usage_usec=cpu_peak,
        memory_peak_bytes=memory_peak,
        pids_peak=pids_peak,
        scratch_used_bytes=used_bytes,
        scratch_used_inodes=used_inodes,
        termination_reason=termination_reason,
        cgroup_reaped=True,
    )
    return returncode, bytes(stdout), bytes(stderr), timed_out, evidence


def _parse_probe(stdout: bytes) -> tuple[dict[str, Any], bytes]:
    lines = stdout.splitlines(keepends=True)
    for index, line in enumerate(lines):
        stripped = line.rstrip(b"\r\n")
        if not stripped.startswith(_START_MARKER):
            continue
        try:
            payload = json.loads(stripped[len(_START_MARKER) :])
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LinuxRunnerInfrastructureError("sandbox probe evidence is malformed") from exc
        if not isinstance(payload, dict):
            raise LinuxRunnerInfrastructureError("sandbox probe evidence is not an object")
        del lines[index]
        return payload, b"".join(lines)
    raise LinuxRunnerInfrastructureError(
        "sandbox failed before emitting adversarial probe evidence"
    )


def _validate_probe(payload: Mapping[str, Any]) -> None:
    required_true = {
        "outside_read_denied",
        "original_workspace_write_denied",
        "outside_write_denied",
        "snapshot_write_denied",
        "loopback_network_denied",
        "external_network_denied",
        "host_secret_scrubbed",
        "controller_pid_hidden",
        "scratch_write_succeeded",
    }
    if any(payload.get(key) is not True for key in required_true):
        raise LinuxRunnerInfrastructureError(
            "per-invocation adversarial sandbox probe failed: "
            + json.dumps(dict(payload), sort_keys=True)
        )
    if payload.get("uid") != 65534 or payload.get("gid") != 65534:
        raise LinuxRunnerInfrastructureError("sandbox did not enter the attested inner uid/gid")
    if not isinstance(payload.get("uid_map"), str) or not isinstance(payload.get("gid_map"), str):
        raise LinuxRunnerInfrastructureError("sandbox user-namespace evidence is absent")
    if not isinstance(payload.get("cgroup"), str) or not payload["cgroup"]:
        raise LinuxRunnerInfrastructureError("sandbox cgroup-namespace evidence is absent")


def _trusted_controller_preexec(timeout_seconds: float, limits: RunnerLimits) -> Callable[[], None]:
    def setup() -> None:
        _lower_process_limit(resource.RLIMIT_CORE, 0)
        cpu = max(1, math.ceil(min(timeout_seconds, limits.wall_max_seconds)))
        _lower_process_limit(resource.RLIMIT_CPU, cpu)
        _lower_process_limit(resource.RLIMIT_NOFILE, limits.fds_max)
        _lower_process_limit(
            resource.RLIMIT_FSIZE,
            limits.file_size_max_bytes,
        )
        if hasattr(resource, "RLIMIT_AS"):
            _lower_process_limit(
                resource.RLIMIT_AS,
                limits.memory_max_bytes,
            )

    return setup


def _collect_trusted_controller(
    process: subprocess.Popen[bytes],
    *,
    timeout_seconds: float,
    limits: RunnerLimits,
) -> tuple[int, bytes, bytes, bool]:
    """Bound the trusted controller without ever buffering unbounded output."""

    if process.stdout is None or process.stderr is None:
        raise LinuxRunnerInfrastructureError("trusted controller pipes were not provisioned")
    selector = selectors.DefaultSelector()
    streams: dict[int, tuple[str, IO[bytes]]] = {}
    for name, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
        descriptor = stream.fileno()
        os.set_blocking(descriptor, False)
        selector.register(descriptor, selectors.EVENT_READ)
        streams[descriptor] = (name, stream)
    stdout = bytearray()
    stderr = bytearray()
    deadline = time.monotonic() + min(timeout_seconds, limits.wall_max_seconds)
    termination_reason: str | None = None
    try:
        while selector.get_map() or process.poll() is None:
            if termination_reason is None and time.monotonic() >= deadline:
                termination_reason = "wall_timeout"
            if termination_reason is not None and process.poll() is None:
                with suppress(ProcessLookupError, PermissionError):
                    os.killpg(process.pid, signal.SIGKILL)
            ready = selector.select(0.02)
            for key, _mask in ready:
                descriptor = int(key.fd)
                name, stream = streams[descriptor]
                try:
                    chunk = os.read(descriptor, 64 * 1024)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(descriptor)
                    stream.close()
                    continue
                overflow = _append_bounded(
                    stdout if name == "stdout" else stderr,
                    chunk,
                    limits.stdout_max_bytes if name == "stdout" else limits.stderr_max_bytes,
                )
                if overflow and termination_reason is None:
                    termination_reason = f"{name}_limit"
        try:
            returncode = process.wait(timeout=limits.reap_timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            raise LinuxRunnerInfrastructureError(
                "trusted controller did not reap after termination"
            ) from exc
    finally:
        selector.close()
        if process.poll() is None:
            with suppress(ProcessLookupError, PermissionError):
                os.killpg(process.pid, signal.SIGKILL)
            with suppress(subprocess.TimeoutExpired):
                process.wait(timeout=limits.reap_timeout_seconds)
        for stream in (process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                stream.close()
    return int(returncode), bytes(stdout), bytes(stderr), termination_reason == "wall_timeout"


class LinuxHardenedVerifierRunner:
    """Validated, reusable runner for one root-provisioned attestation."""

    def __init__(self, attestation: RunnerAttestation) -> None:
        self.attestation = attestation

    @classmethod
    def from_config(cls, path: str | Path) -> LinuxHardenedVerifierRunner:
        return cls(load_attestation(path))

    def provenance(self) -> dict[str, Any]:
        # Executing the exact source-bound bootstrap proves the worker exposes
        # the split trusted-driver/candidate-API contract.  Legacy workers that
        # import a candidate while owning any observation channel fail here,
        # before a model/provider invocation can start.
        _load_trusted_supervisor(self.attestation)
        process_limit_headroom = _process_limit_headroom(self.attestation.limits)
        runtime = runtime_tree_digest(self.attestation.runtime_root)
        if runtime.sha256 != self.attestation.runtime_tree_sha256:
            raise LinuxRunnerInfrastructureError(
                "runtime image drifted during hardened-runner preflight"
            )
        for label, path, expected in (
            ("bubblewrap", self.attestation.bubblewrap_path, self.attestation.bubblewrap_sha256),
            ("seccomp", self.attestation.seccomp_path, self.attestation.seccomp_sha256),
            (
                "launcher executable",
                self.attestation.launcher_executable_path,
                self.attestation.launcher_executable_sha256,
            ),
            (
                "launcher module",
                self.attestation.launcher_module_path,
                self.attestation.launcher_module_sha256,
            ),
            (
                "trusted verifier contract",
                self.attestation.contract_path,
                self.attestation.contract_sha256,
            ),
            ("controller", self.attestation.controller_path, self.attestation.controller_sha256),
            ("worker", self.attestation.worker_path, self.attestation.worker_sha256),
        ):
            _assert_digest(path, expected, label)
        # Create/remove a real child so cgroup.kill is proven, not inferred.
        with _ScratchLease(
            self.attestation.scratch_mount,
            self.attestation.limits,
            self.attestation.cgroup_parent,
        ) as scratch:
            scratch_used_bytes, scratch_used_inodes = scratch.usage()
            cgroup = _CgroupRun(self.attestation.cgroup_parent, self.attestation.limits)
            child_name = f"run-{os.urandom(16).hex()}"
            cgroup.create(child_name)
            cgroup_live_probe = _exercise_cgroup_process_kill(
                self.attestation,
                cgroup,
                process_limit_headroom,
            )
        evidence = self.attestation.public_dict()
        evidence["runtime_observed"] = runtime.to_dict()
        evidence["candidate_api_isolation"] = {
            "schema": CANDIDATE_API_ISOLATION_SCHEMA,
            "worker_source_sha256": self.attestation.worker_sha256,
            "trusted_driver_owns_controller_protocol": True,
            "candidate_receives_per_call_api_only": True,
        }
        evidence["trusted_preexec_limit_headroom"] = process_limit_headroom
        evidence["candidate_resource_boundary"] = (
            "inherits-no-weaker-process-limits-plus-invocation-cgroup-v2"
        )
        evidence["cgroup_v2"]["live_probe"] = {
            **cgroup_live_probe,
            "controllers": sorted(
                set(_read_text(self.attestation.cgroup_parent / "cgroup.controllers").split())
                & {"cpu", "memory", "pids"}
            ),
        }
        filesystem, mount_options, super_options = _mount_record(self.attestation.scratch_mount)
        evidence["scratch"]["live_probe"] = {
            "filesystem": filesystem,
            "mount_options": sorted(mount_options | super_options),
            "used_bytes": scratch_used_bytes,
            "used_inodes": scratch_used_inodes,
            "stale_invocations_recovered": True,
        }
        return evidence

    def run_trusted_controller(
        self,
        *,
        case_id: str,
        workspace: str | Path,
        timeout_seconds: float,
    ) -> HardenedProcessResult:
        """Run the root-owned verdict controller; it never imports candidate code."""

        if case_id not in {"coding.concurrent-cache", "coding.path-boundary"}:
            raise LinuxRunnerInfrastructureError(
                f"no attested trusted controller is authorized for {case_id!r}"
            )
        limits = self.attestation.limits
        if timeout_seconds <= 0 or timeout_seconds > limits.wall_max_seconds:
            raise LinuxRunnerInfrastructureError(
                f"controller timeout must be in (0, {limits.wall_max_seconds}]"
            )
        process_limit_headroom = _process_limit_headroom(
            limits,
            cpu_seconds=max(1, math.ceil(min(timeout_seconds, limits.wall_max_seconds))),
        )
        workspace_path = Path(workspace).resolve(strict=True)
        runtime_pre = runtime_tree_digest(self.attestation.runtime_root)
        if runtime_pre.sha256 != self.attestation.runtime_tree_sha256:
            raise LinuxRunnerInfrastructureError(
                "runtime image drifted before trusted controller execution"
            )
        for label, path, expected in (
            (
                "launcher executable",
                self.attestation.launcher_executable_path,
                self.attestation.launcher_executable_sha256,
            ),
            (
                "launcher module",
                self.attestation.launcher_module_path,
                self.attestation.launcher_module_sha256,
            ),
            (
                "trusted verifier contract",
                self.attestation.contract_path,
                self.attestation.contract_sha256,
            ),
            ("controller", self.attestation.controller_path, self.attestation.controller_sha256),
            ("worker", self.attestation.worker_path, self.attestation.worker_sha256),
        ):
            _assert_digest(path, expected, label)
        environment = {
            "PATH": os.defpath,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            WORKER_CONFIG_ENV: str(self.attestation.path),
            WORKER_CONFIG_SHA256_ENV: self.attestation.config_sha256,
            WORKER_CLI_ENV: json.dumps(
                list(self.attestation.launcher_argv),
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            WORKER_CONTRACT_SHA256_ENV: self.attestation.contract_sha256,
        }
        command = [
            str(self.attestation.launcher_executable_path),
            "-I",
            str(self.attestation.controller_path),
            case_id,
            str(workspace_path),
        ]
        try:
            process = subprocess.Popen(
                command,
                cwd=self.attestation.runtime_root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
                close_fds=True,
                start_new_session=True,
                preexec_fn=_trusted_controller_preexec(timeout_seconds, limits),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise LinuxRunnerInfrastructureError(
                f"trusted verifier controller failed to start: {exc}"
            ) from exc
        returncode, stdout, stderr, timed_out = _collect_trusted_controller(
            process,
            timeout_seconds=timeout_seconds,
            limits=limits,
        )

        # A controller crash/timeout also triggers launcher PDEATHSIG.  Taking
        # the exclusive lease and creating one fresh child proves any stale
        # scratch/cgroup state was recovered and no candidate task remains.
        with _ScratchLease(
            self.attestation.scratch_mount,
            limits,
            self.attestation.cgroup_parent,
        ):
            cgroup = _CgroupRun(self.attestation.cgroup_parent, limits)
            cgroup.create(f"run-{os.urandom(16).hex()}")
            cgroup.kill_and_reap()

        runtime_post = runtime_tree_digest(self.attestation.runtime_root)
        if runtime_post != runtime_pre:
            raise LinuxRunnerInfrastructureError(
                "runtime image drifted during trusted controller execution"
            )
        for label, path, expected in (
            (
                "launcher executable",
                self.attestation.launcher_executable_path,
                self.attestation.launcher_executable_sha256,
            ),
            (
                "launcher module",
                self.attestation.launcher_module_path,
                self.attestation.launcher_module_sha256,
            ),
            (
                "trusted verifier contract",
                self.attestation.contract_path,
                self.attestation.contract_sha256,
            ),
            ("controller", self.attestation.controller_path, self.attestation.controller_sha256),
            ("worker", self.attestation.worker_path, self.attestation.worker_sha256),
        ):
            _assert_digest(path, expected, label)
        evidence = {
            "schema": "echo.trusted_verifier_controller_run.v2",
            "authorization": True,
            "case_id": case_id,
            "git_sha": self.attestation.git_sha,
            "config_sha256": self.attestation.config_sha256,
            "runtime_pre": runtime_pre.to_dict(),
            "runtime_post": runtime_post.to_dict(),
            "controller_sha256": self.attestation.controller_sha256,
            "contract_sha256": self.attestation.contract_sha256,
            "worker_sha256": self.attestation.worker_sha256,
            "launcher_module_sha256": self.attestation.launcher_module_sha256,
            "controller_returncode": returncode,
            "stdout_sha256": sha256(stdout).hexdigest(),
            "stderr_sha256": sha256(stderr).hexdigest(),
            "tree_reap_revalidated": True,
            "trusted_controller_preexec_limit_headroom": process_limit_headroom,
        }
        return HardenedProcessResult(
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
            evidence=evidence,
        )

    def run_hidden_verifier(
        self,
        *,
        verifier_source: bytes,
        verifier_source_sha256: str,
        argument_templates: Sequence[str],
        workspace: str | Path,
        timeout_seconds: float,
    ) -> HardenedProcessResult:
        limits = self.attestation.limits
        if timeout_seconds <= 0 or timeout_seconds > limits.wall_max_seconds:
            raise LinuxRunnerInfrastructureError(
                f"verifier timeout must be in (0, {limits.wall_max_seconds}]"
            )
        if len(verifier_source) > limits.verifier_max_source_bytes:
            raise LinuxRunnerInfrastructureError("trusted verifier source exceeds its bound")
        if sha256(verifier_source).hexdigest() != verifier_source_sha256:
            raise LinuxRunnerInfrastructureError("trusted verifier source digest does not match")
        process_limit_headroom = _process_limit_headroom(limits)
        workspace_path = Path(workspace).resolve(strict=True)
        verifier_pre = sha256(verifier_source).hexdigest()
        runtime_pre = runtime_tree_digest(self.attestation.runtime_root)
        if runtime_pre.sha256 != self.attestation.runtime_tree_sha256:
            raise LinuxRunnerInfrastructureError("runtime image drifted before verifier execution")
        with _ScratchLease(
            self.attestation.scratch_mount,
            limits,
            self.attestation.cgroup_parent,
        ) as scratch:
            assert scratch.invocation is not None
            invocation = scratch.invocation
            snapshot_path = invocation / "snapshot"
            work = invocation / "work"
            work.mkdir(mode=0o700)
            for name in ("home", "tmp", "cache", "config", "data"):
                (work / name).mkdir(mode=0o700)
            snapshot = create_candidate_snapshot(
                workspace_path,
                snapshot_path,
                limits=limits,
            )
            outside_dir = invocation / "evaluator-probe"
            outside_dir.mkdir(mode=0o700)
            outside_read = outside_dir / "outside-read"
            outside_read.write_text("evaluator-secret", encoding="utf-8")
            outside_write = outside_dir / "outside-write"
            original_write = workspace_path / ".echo-original-write-probe"
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            listener.settimeout(0.0)
            cgroup = _CgroupRun(self.attestation.cgroup_parent, limits)
            cgroup_name = f"run-{os.urandom(16).hex()}"
            cgroup.create(cgroup_name)
            cgroup_fd = cgroup.process_fd()
            seccomp_fd = os.open(
                self.attestation.seccomp_path,
                os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
            )
            verifier_args = [
                str(part).replace("{workspace}", "/workspace") for part in argument_templates
            ]
            probe_args = [
                str(outside_read),
                str(original_write),
                str(outside_write),
                str(listener.getsockname()[1]),
                str(os.getpid()),
            ]
            command = _bubblewrap_command(
                self.attestation,
                snapshot=snapshot_path,
                work=work,
                seccomp_fd=seccomp_fd,
                verifier_args=verifier_args,
                probe_args=probe_args,
            )
            environment = {_PROBE_SECRET_KEY: "must-not-leak"}
            try:
                process = subprocess.Popen(
                    command,
                    cwd=invocation,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=environment,
                    close_fds=True,
                    pass_fds=(cgroup_fd, seccomp_fd),
                    start_new_session=True,
                    preexec_fn=_preexec_setup(cgroup_fd, limits),
                )
            except (OSError, subprocess.SubprocessError) as exc:
                cgroup.kill_and_reap(ignore_missing=True)
                raise LinuxRunnerInfrastructureError(
                    f"hardened verifier sandbox failed to start: {exc}"
                ) from exc
            finally:
                os.close(cgroup_fd)
                os.close(seccomp_fd)
            try:
                returncode, raw_stdout, raw_stderr, timed_out, resources = _collect_process(
                    process,
                    input_bytes=verifier_source,
                    cgroup=cgroup,
                    scratch=scratch,
                    timeout_seconds=timeout_seconds,
                    limits=limits,
                )
            finally:
                listener.close()
            probe, stdout = _parse_probe(raw_stdout)
            _validate_probe(probe)
            if outside_read.read_text(encoding="utf-8") != "evaluator-secret":
                raise LinuxRunnerInfrastructureError("sandbox modified the external read sentinel")
            if outside_write.exists() or original_write.exists():
                raise LinuxRunnerInfrastructureError(
                    "sandbox escaped its filesystem write boundary"
                )
            workspace_post = candidate_tree_digest(workspace_path, limits=limits)
            if workspace_post != snapshot.source:
                raise LinuxRunnerInfrastructureError(
                    "candidate workspace drifted during hidden verification"
                )
            runtime_post = runtime_tree_digest(self.attestation.runtime_root)
            if runtime_post != runtime_pre:
                raise LinuxRunnerInfrastructureError(
                    "runtime image drifted during verifier execution"
                )
            for label, path, expected in (
                (
                    "bubblewrap",
                    self.attestation.bubblewrap_path,
                    self.attestation.bubblewrap_sha256,
                ),
                ("seccomp", self.attestation.seccomp_path, self.attestation.seccomp_sha256),
                (
                    "launcher executable",
                    self.attestation.launcher_executable_path,
                    self.attestation.launcher_executable_sha256,
                ),
                (
                    "launcher module",
                    self.attestation.launcher_module_path,
                    self.attestation.launcher_module_sha256,
                ),
                (
                    "trusted verifier contract",
                    self.attestation.contract_path,
                    self.attestation.contract_sha256,
                ),
                (
                    "controller",
                    self.attestation.controller_path,
                    self.attestation.controller_sha256,
                ),
                ("worker", self.attestation.worker_path, self.attestation.worker_sha256),
            ):
                _assert_digest(path, expected, label)
            evidence = {
                "schema": "echo.hardened_verifier_run.v2",
                "authorization": True,
                "git_sha": self.attestation.git_sha,
                "config_sha256": self.attestation.config_sha256,
                "runtime_pre": runtime_pre.to_dict(),
                "runtime_post": runtime_post.to_dict(),
                "workspace_pre": snapshot.source.to_dict(),
                "workspace_snapshot": snapshot.copied.to_dict(),
                "workspace_post": workspace_post.to_dict(),
                "verifier_pre_sha256": verifier_pre,
                "verifier_post_sha256": sha256(verifier_source).hexdigest(),
                "probe": probe,
                "resources": resources.to_dict(),
                "sandbox_preexec_limit_headroom": process_limit_headroom,
            }
            return HardenedProcessResult(
                returncode=returncode,
                stdout=stdout,
                stderr=raw_stderr,
                timed_out=timed_out,
                evidence=evidence,
            )


def _frame_payload(raw: bytes, *, limits: RunnerLimits, from_worker: bool) -> dict[str, Any]:
    if not raw or len(raw) > limits.rpc_max_frame_bytes:
        raise LinuxRunnerInfrastructureError("candidate worker RPC frame exceeds its bound")
    try:
        payload = json.loads(raw, object_pairs_hook=_reject_duplicate_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise LinuxRunnerInfrastructureError("candidate worker RPC frame is invalid JSON") from exc
    if not isinstance(payload, dict) or _canonical_json(payload) != raw:
        raise LinuxRunnerInfrastructureError("candidate worker RPC frame is not canonical")
    if from_worker and {"passed", "score", "reason"} & set(payload):
        raise LinuxRunnerInfrastructureError(
            "candidate worker attempted to forge trusted verdict fields"
        )
    return payload


def read_rpc_frame(stream: BinaryIO, *, limits: RunnerLimits = DEFAULT_LIMITS) -> dict[str, Any]:
    header = _read_exact(stream, 4)
    length = struct.unpack(">I", header)[0]
    if length <= 0 or length > limits.rpc_max_frame_bytes:
        raise LinuxRunnerInfrastructureError("candidate worker RPC frame length is invalid")
    return _frame_payload(_read_exact(stream, length), limits=limits, from_worker=True)


def write_rpc_frame(
    stream: BinaryIO,
    payload: Mapping[str, Any],
    *,
    limits: RunnerLimits = DEFAULT_LIMITS,
) -> None:
    encoded = _canonical_json(dict(payload))
    if not encoded or len(encoded) > limits.rpc_max_frame_bytes:
        raise LinuxRunnerInfrastructureError("candidate worker RPC frame exceeds its bound")
    stream.write(struct.pack(">I", len(encoded)) + encoded)
    stream.flush()


def _read_exact(stream: BinaryIO, length: int) -> bytes:
    chunks: list[bytes] = []
    remaining = length
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            raise LinuxRunnerInfrastructureError("candidate worker RPC stream ended mid-frame")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _arm_parent_death_signal() -> None:
    """Make a controller crash tear down the trusted launcher immediately."""

    if not sys.platform.startswith("linux"):
        raise LinuxRunnerInfrastructureError("candidate worker launcher is Linux-only")
    parent = os.getppid()
    if parent <= 1:
        raise LinuxRunnerInfrastructureError(
            "candidate worker launcher started without a live controller parent"
        )
    libc = ctypes.CDLL(None, use_errno=True)
    prctl = libc.prctl
    prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
    prctl.restype = ctypes.c_int
    # PR_SET_PDEATHSIG = 1.  SIGKILL cannot be caught or cleared by cleanup code.
    if prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise LinuxRunnerInfrastructureError(
            f"cannot arm launcher parent-death signal: {os.strerror(error)}"
        )
    if os.getppid() != parent:
        os.kill(os.getpid(), signal.SIGKILL)


def _relocate_socket(descriptor: int, *, label: str) -> socket.socket:
    """Own an inherited UNIX stream at a high CLOEXEC descriptor."""

    if isinstance(descriptor, bool) or not isinstance(descriptor, int) or descriptor < 3:
        raise LinuxRunnerInfrastructureError(f"{label} descriptor is invalid")
    try:
        if hasattr(fcntl, "F_DUPFD_CLOEXEC"):
            relocated = fcntl.fcntl(descriptor, fcntl.F_DUPFD_CLOEXEC, 64)
        else:  # pragma: no cover - Linux always exposes F_DUPFD_CLOEXEC
            relocated = fcntl.fcntl(descriptor, fcntl.F_DUPFD, 64)
            os.set_inheritable(relocated, False)
        os.close(descriptor)
        channel = socket.socket(fileno=relocated)
        if (
            channel.family != socket.AF_UNIX
            or channel.type & socket.SOCK_STREAM != socket.SOCK_STREAM
        ):
            raise LinuxRunnerInfrastructureError(f"{label} must be an AF_UNIX stream socket")
        channel.getpeername()
        channel.settimeout(5.0)
        return channel
    except LinuxRunnerInfrastructureError:
        with suppress(OSError):
            os.close(locals().get("relocated", -1))
        raise
    except OSError as exc:
        with suppress(OSError):
            os.close(locals().get("relocated", -1))
        raise LinuxRunnerInfrastructureError(f"{label} socket is unavailable: {exc}") from exc


def _move_fd(source: int, target: int) -> int:
    if source != target:
        os.dup2(source, target, inheritable=True)
        os.close(source)
    else:
        os.set_inheritable(target, True)
    return target


def _relocate_fd(descriptor: int, *, minimum: int = 64) -> int:
    try:
        if hasattr(fcntl, "F_DUPFD_CLOEXEC"):
            relocated = fcntl.fcntl(descriptor, fcntl.F_DUPFD_CLOEXEC, minimum)
        else:  # pragma: no cover - Linux always exposes F_DUPFD_CLOEXEC
            relocated = fcntl.fcntl(descriptor, fcntl.F_DUPFD, minimum)
            os.set_inheritable(relocated, False)
        os.close(descriptor)
        return int(relocated)
    except OSError as exc:
        raise LinuxRunnerInfrastructureError(
            f"cannot relocate trusted launcher descriptor: {exc}"
        ) from exc


def _send_control_frame(channel: socket.socket, payload: Mapping[str, Any]) -> None:
    encoded = _canonical_json(dict(payload))
    if not encoded or len(encoded) > DEFAULT_LIMITS.rpc_max_frame_bytes:
        raise LinuxRunnerInfrastructureError("trusted launcher control frame exceeds its bound")
    try:
        channel.sendall(struct.pack("!I", len(encoded)) + encoded)
    except (OSError, TimeoutError) as exc:
        raise LinuxRunnerInfrastructureError(
            "trusted launcher control channel closed while sending"
        ) from exc


def _read_worker_probe(
    descriptor: int,
    *,
    process: subprocess.Popen[bytes],
    cgroup: _CgroupRun,
    timeout_seconds: float,
    limits: RunnerLimits,
) -> dict[str, Any]:
    """Read one fixed-bootstrap frame before any candidate module is imported."""

    selector = selectors.DefaultSelector()
    buffer = bytearray()
    expected: int | None = None
    deadline = time.monotonic() + timeout_seconds
    os.set_blocking(descriptor, False)
    selector.register(descriptor, selectors.EVENT_READ)
    try:
        while True:
            if time.monotonic() >= deadline:
                raise LinuxRunnerInfrastructureError("candidate isolation probe timed out")
            if cgroup.path is None:
                raise LinuxRunnerInfrastructureError("candidate cgroup vanished during probe")
            _cpu, _memory, _pids, resource_reason = cgroup.metrics()
            if resource_reason is not None:
                raise LinuxRunnerInfrastructureError(
                    f"fixed candidate isolation probe exceeded {resource_reason}"
                )
            if process.poll() is not None:
                raise LinuxRunnerInfrastructureError(
                    "candidate sandbox exited before isolation was attested"
                )
            if not selector.select(0.02):
                continue
            try:
                chunk = os.read(descriptor, 64 * 1024)
            except BlockingIOError:
                continue
            if not chunk:
                break
            buffer.extend(chunk)
            if len(buffer) > limits.rpc_max_frame_bytes + 4:
                raise LinuxRunnerInfrastructureError("candidate isolation probe exceeded its bound")
            if expected is None and len(buffer) >= 4:
                expected = struct.unpack("!I", buffer[:4])[0]
                if expected < 2 or expected > limits.rpc_max_frame_bytes:
                    raise LinuxRunnerInfrastructureError(
                        "candidate isolation probe frame length is invalid"
                    )
            if expected is not None and len(buffer) > expected + 4:
                raise LinuxRunnerInfrastructureError(
                    "candidate isolation probe contained trailing bytes"
                )
    finally:
        selector.close()
        with suppress(OSError):
            os.close(descriptor)
    if expected is None or len(buffer) != expected + 4:
        raise LinuxRunnerInfrastructureError("candidate isolation probe was incomplete")
    return _frame_payload(bytes(buffer[4:]), limits=limits, from_worker=False)


def _current_namespace_identities() -> dict[str, str]:
    try:
        return {name: os.readlink(f"/proc/self/ns/{name}") for name in _NAMESPACE_NAMES}
    except OSError as exc:
        raise LinuxRunnerInfrastructureError(
            f"host namespace identity is unavailable: {exc}"
        ) from exc


def _validate_worker_probe(
    payload: Mapping[str, Any],
    *,
    host_namespace_ids: Mapping[str, str],
) -> None:
    _validate_probe(payload)
    required = {
        "candidate_protocol_is_unix_stream",
        "challenge_write_denied",
        "controller_channels_absent",
        "private_namespaces",
        "probe_pipe_present",
        "supervisor_pid_hidden",
    }
    if any(payload.get(name) is not True for name in required):
        raise LinuxRunnerInfrastructureError(
            "candidate/supervisor separation probe failed: "
            + json.dumps(dict(payload), sort_keys=True)
        )
    namespace_ids = payload.get("namespace_ids")
    if (
        not isinstance(namespace_ids, dict)
        or set(namespace_ids) != set(_NAMESPACE_NAMES)
        or set(host_namespace_ids) != set(_NAMESPACE_NAMES)
        or any(
            not isinstance(namespace_ids[name], str)
            or not re.fullmatch(r"[a-z]+:\[[0-9]+\]", namespace_ids[name])
            or namespace_ids[name] == host_namespace_ids[name]
            for name in _NAMESPACE_NAMES
        )
    ):
        raise LinuxRunnerInfrastructureError("candidate namespace identity attestation is invalid")


def _load_trusted_supervisor(
    attestation: RunnerAttestation,
) -> tuple[Callable[..., int], type[BaseException], int]:
    """Compile the exact attested worker bytes into the trusted host launcher."""

    _assert_digest(attestation.worker_path, attestation.worker_sha256, "worker")
    try:
        descriptor = os.open(
            attestation.worker_path,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
        try:
            source = _read_fd_bounded(descriptor, 2 * 1024 * 1024)
        finally:
            os.close(descriptor)
        if sha256(source).hexdigest() != attestation.worker_sha256:
            raise LinuxRunnerInfrastructureError("trusted worker source changed while loading")
        module_name = f"_echo_attested_worker_{attestation.worker_sha256[:16]}"
        module = types.ModuleType(module_name)
        module.__file__ = str(attestation.worker_path)
        module.__package__ = ""
        sys.modules[module_name] = module
        try:
            exec(compile(source, str(attestation.worker_path), "exec"), module.__dict__)
        finally:
            sys.modules.pop(module_name, None)
        supervisor = module.__dict__.get("run_trusted_supervisor")
        error_type = module.__dict__.get("TrustedSupervisorError")
        candidate_failure_exit = module.__dict__.get("CANDIDATE_FAILURE_EXIT")
        candidate_api_schema = module.__dict__.get("CANDIDATE_API_ISOLATION_SCHEMA")
        if (
            not callable(supervisor)
            or not isinstance(error_type, type)
            or not issubclass(error_type, BaseException)
            or candidate_failure_exit != PROTOCOL_LIMIT_EXIT
            or candidate_api_schema != CANDIDATE_API_ISOLATION_SCHEMA
        ):
            raise LinuxRunnerInfrastructureError(
                "attested worker does not expose the isolated candidate-API supervisor contract"
            )
        return supervisor, error_type, candidate_failure_exit
    except LinuxRunnerInfrastructureError:
        raise
    except BaseException as exc:
        raise LinuxRunnerInfrastructureError(
            f"trusted worker supervisor could not be loaded: {exc}"
        ) from exc


def _worker_cli_error(message: str) -> NoReturn:
    raise LinuxRunnerInfrastructureError(message)


def worker_cli(argv: Sequence[str]) -> int:
    """Run the trusted host supervisor and one isolated candidate process."""

    import argparse

    class InfrastructureParser(argparse.ArgumentParser):
        def error(self, message: str) -> NoReturn:
            _worker_cli_error(f"invalid candidate worker invocation: {message}")

    parser = InfrastructureParser(
        prog="echo-hardened-verifier-worker",
        allow_abbrev=False,
    )
    parser.add_argument("worker", choices=["worker"])
    parser.add_argument("--attestation", required=True)
    parser.add_argument("--workspace-snapshot", required=True)
    parser.add_argument("--workspace-manifest-sha256", required=True)
    parser.add_argument("--challenge-snapshot")
    parser.add_argument("--challenge-manifest-sha256", required=True)
    parser.add_argument("--control-fd", required=True, type=int)
    parser.add_argument("--protocol-fd", required=True, type=int)
    parser.add_argument("--run-nonce", required=True)
    parsed = parser.parse_args(list(argv))
    if not _NONCE_RE.fullmatch(parsed.run_nonce):
        _worker_cli_error("invalid worker run nonce")
    if not _SHA256_RE.fullmatch(parsed.workspace_manifest_sha256):
        _worker_cli_error("invalid workspace manifest digest")
    if not _SHA256_RE.fullmatch(parsed.challenge_manifest_sha256):
        _worker_cli_error("invalid challenge manifest digest")
    if parsed.control_fd < 3 or parsed.protocol_fd < 3 or parsed.control_fd == parsed.protocol_fd:
        _worker_cli_error("control and protocol descriptors must be distinct from stdio")
    try:
        control_stat = os.fstat(parsed.control_fd)
        protocol_stat = os.fstat(parsed.protocol_fd)
    except OSError as exc:
        raise LinuxRunnerInfrastructureError(
            f"trusted launcher channel is unavailable: {exc}"
        ) from exc
    if (control_stat.st_dev, control_stat.st_ino) == (
        protocol_stat.st_dev,
        protocol_stat.st_ino,
    ):
        _worker_cli_error("control and protocol descriptors alias one socket")

    _arm_parent_death_signal()
    attestation = load_attestation(parsed.attestation)
    try:
        running_module = Path(__file__).resolve(strict=True)
        attested_module = attestation.launcher_module_path.resolve(strict=True)
    except OSError as exc:
        raise LinuxRunnerInfrastructureError(
            "trusted launcher module identity is unavailable"
        ) from exc
    if running_module != attested_module:
        raise LinuxRunnerInfrastructureError(
            "candidate worker launcher is not the attested root-owned module"
        )
    _assert_digest(running_module, attestation.launcher_module_sha256, "launcher module")
    _assert_digest(
        attestation.launcher_executable_path,
        attestation.launcher_executable_sha256,
        "launcher executable",
    )
    supervisor, supervisor_error_type, candidate_failure_exit = _load_trusted_supervisor(
        attestation
    )
    _cpu_soft, inherited_cpu_hard = resource.getrlimit(resource.RLIMIT_CPU)
    nested_cpu_limit = max(1, math.ceil(attestation.limits.cpu_max_seconds))
    if inherited_cpu_hard != resource.RLIM_INFINITY:
        nested_cpu_limit = min(nested_cpu_limit, inherited_cpu_hard)
    if nested_cpu_limit < 1:
        raise LinuxRunnerInfrastructureError(
            "trusted controller left no CPU-limit headroom for its launcher"
        )
    _process_limit_headroom(attestation.limits, cpu_seconds=nested_cpu_limit)

    control_channel: socket.socket | None = None
    controller_protocol: socket.socket | None = None
    complete: dict[str, Any] | None = None
    try:
        # Relocating both controller-owned channels before allocating child FDs
        # makes it possible to give the candidate exactly FD 3 plus a temporary
        # probe pipe at FD 4.  Neither controller channel is in Popen.pass_fds.
        control_channel = _relocate_socket(parsed.control_fd, label="launcher control")
        controller_protocol = _relocate_socket(
            parsed.protocol_fd,
            label="controller protocol",
        )
        relocated_control_stat = os.fstat(control_channel.fileno())
        relocated_protocol_stat = os.fstat(controller_protocol.fileno())
        workspace_path = Path(parsed.workspace_snapshot).resolve(strict=True)
        challenge_path = (
            Path(parsed.challenge_snapshot).resolve(strict=True)
            if parsed.challenge_snapshot is not None
            else None
        )
        no_challenge_digest = sha256(b"no-challenge-tree").hexdigest()
        if (challenge_path is None) != (parsed.challenge_manifest_sha256 == no_challenge_digest):
            raise LinuxRunnerInfrastructureError(
                "challenge snapshot presence does not match its manifest binding"
            )

        runtime_pre = runtime_tree_digest(attestation.runtime_root)
        if runtime_pre.sha256 != attestation.runtime_tree_sha256:
            raise LinuxRunnerInfrastructureError(
                "runtime image drifted before candidate worker execution"
            )

        with _ScratchLease(
            attestation.scratch_mount,
            attestation.limits,
            attestation.cgroup_parent,
        ) as scratch:
            assert scratch.invocation is not None
            # FD 3/4/5 are deliberately normalized for the bwrap boundary.
            # Protect the lease lock first: in a minimal controller process it
            # commonly occupies FD 4, and dup2(probe_write, 4) must never
            # silently replace the descriptor that guarantees exclusivity and
            # stale-tree recovery.
            scratch.lock_descriptor = _relocate_fd(scratch.lock_descriptor)
            invocation = scratch.invocation
            workspace_snapshot = invocation / "workspace"
            challenge_snapshot = invocation / "challenge"
            work = invocation / "work"
            work.mkdir(mode=0o700)
            for name in ("home", "tmp", "cache", "config", "data"):
                (work / name).mkdir(mode=0o700)

            artifact_violation = False
            workspace_manifest_verified = False
            workspace_evidence: SnapshotEvidence | None = None
            try:
                observed_workspace_manifest = trusted_controller_tree_manifest_sha256(
                    workspace_path,
                    reject_symlinks=True,
                    limits=attestation.limits,
                )
                if observed_workspace_manifest != parsed.workspace_manifest_sha256:
                    raise LinuxRunnerInfrastructureError(
                        "controller workspace manifest changed before snapshot"
                    )
                workspace_manifest_verified = True
                workspace_evidence = create_candidate_snapshot(
                    workspace_path,
                    workspace_snapshot,
                    limits=attestation.limits,
                )
            except CandidateSnapshotViolation:
                # An oversized/deep/special engine artifact is a scored
                # candidate failure.  Run the fixed worker against an empty
                # read-only tree so ready/complete still prove the boundary.
                artifact_violation = True
                if workspace_snapshot.exists():
                    _safe_remove_tree(workspace_snapshot, expected_parent=invocation)
                workspace_snapshot.mkdir(mode=0o555)

            challenge_evidence: SnapshotEvidence | None = None
            if challenge_path is None:
                challenge_snapshot.mkdir(mode=0o555)
            else:
                observed_challenge_manifest = trusted_controller_tree_manifest_sha256(
                    challenge_path,
                    reject_symlinks=False,
                    limits=attestation.limits,
                )
                if observed_challenge_manifest != parsed.challenge_manifest_sha256:
                    raise LinuxRunnerInfrastructureError(
                        "controller challenge manifest changed before snapshot"
                    )
                challenge_evidence = create_candidate_snapshot(
                    challenge_path,
                    challenge_snapshot,
                    limits=attestation.limits,
                    allow_internal_symlinks=True,
                )

            candidate_parent_raw, candidate_child = socket.socketpair(
                socket.AF_UNIX,
                socket.SOCK_STREAM,
            )
            candidate_parent = _relocate_socket(
                candidate_parent_raw.detach(),
                label="candidate narrow protocol",
            )
            candidate_child_fd = _move_fd(candidate_child.detach(), 3)
            probe_read_fd, probe_write_fd = os.pipe()
            os.set_inheritable(probe_read_fd, False)
            os.set_inheritable(probe_write_fd, False)
            probe_read_fd = _relocate_fd(probe_read_fd)
            probe_write_fd = _move_fd(probe_write_fd, 4)
            seccomp_fd = os.open(
                attestation.seccomp_path,
                os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
            )
            seccomp_fd = _move_fd(seccomp_fd, 5)

            outside_dir = invocation / "evaluator-probe"
            outside_dir.mkdir(mode=0o700)
            outside_read = outside_dir / "outside-read"
            outside_read.write_text("evaluator-secret", encoding="utf-8")
            outside_write = outside_dir / "outside-write"
            original_write = workspace_path / ".echo-original-write-probe"
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            listener.settimeout(0.0)

            cgroup = _CgroupRun(attestation.cgroup_parent, attestation.limits)
            cgroup.create(f"run-{os.urandom(16).hex()}")
            cgroup_fd = cgroup.process_fd()
            host_namespace_ids = _current_namespace_identities()
            probe_args = [
                str(outside_read),
                str(original_write),
                str(outside_write),
                str(listener.getsockname()[1]),
                str(os.getppid()),
                str(os.getpid()),
                str(relocated_control_stat.st_dev),
                str(relocated_control_stat.st_ino),
                str(relocated_protocol_stat.st_dev),
                str(relocated_protocol_stat.st_ino),
                json.dumps(
                    host_namespace_ids,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            ]
            command = _worker_bubblewrap_command(
                attestation,
                workspace_snapshot=workspace_snapshot,
                challenge_snapshot=challenge_snapshot,
                work=work,
                seccomp_fd=seccomp_fd,
                probe_args=probe_args,
                has_challenge=challenge_path is not None,
            )
            process: subprocess.Popen[bytes] | None = None
            supervisor_done = threading.Event()
            supervisor_state: dict[str, Any] = {}
            try:
                process = subprocess.Popen(
                    command,
                    cwd=invocation,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env={_PROBE_SECRET_KEY: "must-not-leak"},
                    close_fds=True,
                    pass_fds=(candidate_child_fd, probe_write_fd, seccomp_fd, cgroup_fd),
                    start_new_session=True,
                    preexec_fn=_preexec_setup(cgroup_fd, attestation.limits),
                )
            except (OSError, subprocess.SubprocessError) as exc:
                cgroup.kill_and_reap(ignore_missing=True)
                raise LinuxRunnerInfrastructureError(
                    f"candidate worker sandbox failed to start: {exc}"
                ) from exc
            finally:
                for descriptor in (
                    candidate_child_fd,
                    probe_write_fd,
                    seccomp_fd,
                    cgroup_fd,
                ):
                    with suppress(OSError):
                        os.close(descriptor)

            try:
                assert process is not None
                try:
                    probe = _read_worker_probe(
                        probe_read_fd,
                        process=process,
                        cgroup=cgroup,
                        timeout_seconds=10.0,
                        limits=attestation.limits,
                    )
                    _validate_worker_probe(
                        probe,
                        host_namespace_ids=host_namespace_ids,
                    )
                except BaseException:
                    if cgroup.path is not None:
                        cgroup.kill_and_reap(ignore_missing=True)
                    with suppress(subprocess.TimeoutExpired):
                        process.wait(timeout=attestation.limits.reap_timeout_seconds)
                    raise

                _send_control_frame(
                    control_channel,
                    {
                        "contract_sha256": attestation.config_sha256,
                        "isolation": worker_isolation_contract(),
                        "kind": "runner_ready",
                        "run_nonce": parsed.run_nonce,
                        "version": 1,
                    },
                )
                supervisor_controller_fd = controller_protocol.fileno()

                def supervise() -> None:
                    try:
                        supervisor_state["returncode"] = supervisor(
                            supervisor_controller_fd,
                            candidate_parent.fileno(),
                            timeout_seconds=20.0,
                        )
                    except BaseException as exc:  # trusted thread, audited below
                        supervisor_state["exception"] = exc
                    finally:
                        candidate_parent.close()
                        supervisor_done.set()

                supervisor_thread = threading.Thread(
                    target=supervise,
                    name="echo-trusted-verifier-supervisor",
                    daemon=True,
                )
                supervisor_thread.start()

                def protocol_termination() -> str | None:
                    if not supervisor_done.is_set():
                        return None
                    if "exception" in supervisor_state:
                        return "protocol_limit"
                    if supervisor_state.get("returncode") == candidate_failure_exit:
                        return "protocol_limit"
                    return None

                (
                    sandbox_returncode,
                    candidate_stdout,
                    candidate_stderr,
                    timed_out,
                    resources,
                ) = _collect_process(
                    process,
                    input_bytes=b"",
                    cgroup=cgroup,
                    scratch=scratch,
                    timeout_seconds=20.0,
                    limits=attestation.limits,
                    termination_probe=protocol_termination,
                )
                supervisor_thread.join(timeout=attestation.limits.reap_timeout_seconds)
                if supervisor_thread.is_alive():
                    raise LinuxRunnerInfrastructureError(
                        "trusted candidate supervisor did not terminate after tree reap"
                    )
                controller_protocol.close()
                controller_protocol = None
                supervisor_exception = supervisor_state.get("exception")
                if supervisor_exception is not None:
                    if isinstance(supervisor_exception, supervisor_error_type):
                        raise LinuxRunnerInfrastructureError(
                            f"trusted candidate supervisor rejected controller input: "
                            f"{supervisor_exception}"
                        ) from supervisor_exception
                    raise LinuxRunnerInfrastructureError(
                        f"trusted candidate supervisor failed: {supervisor_exception}"
                    ) from supervisor_exception
                supervisor_returncode = supervisor_state.get("returncode")
                if supervisor_returncode not in {0, candidate_failure_exit}:
                    raise LinuxRunnerInfrastructureError(
                        "trusted candidate supervisor returned an invalid status"
                    )

                if artifact_violation:
                    worker_exit_code = candidate_failure_exit
                elif sandbox_returncode in {
                    OUTPUT_LIMIT_EXIT,
                    RESOURCE_LIMIT_EXIT,
                    PROTOCOL_LIMIT_EXIT,
                    124,
                }:
                    worker_exit_code = sandbox_returncode
                elif sandbox_returncode != 0 or supervisor_returncode != 0:
                    worker_exit_code = candidate_failure_exit
                else:
                    worker_exit_code = 0

                if outside_read.read_text(encoding="utf-8") != "evaluator-secret":
                    raise LinuxRunnerInfrastructureError(
                        "candidate sandbox modified the external read sentinel"
                    )
                if outside_write.exists() or original_write.exists():
                    raise LinuxRunnerInfrastructureError(
                        "candidate sandbox escaped its filesystem write boundary"
                    )
                if workspace_manifest_verified:
                    workspace_post_manifest = trusted_controller_tree_manifest_sha256(
                        workspace_path,
                        reject_symlinks=True,
                        limits=attestation.limits,
                    )
                    if workspace_post_manifest != parsed.workspace_manifest_sha256:
                        raise LinuxRunnerInfrastructureError(
                            "controller workspace changed during candidate execution"
                        )
                if not artifact_violation:
                    assert workspace_evidence is not None
                    if candidate_tree_digest(workspace_snapshot) != workspace_evidence.copied:
                        raise LinuxRunnerInfrastructureError(
                            "read-only candidate workspace snapshot drifted"
                        )
                if challenge_path is not None:
                    challenge_post_manifest = trusted_controller_tree_manifest_sha256(
                        challenge_path,
                        reject_symlinks=False,
                        limits=attestation.limits,
                    )
                    if challenge_post_manifest != parsed.challenge_manifest_sha256:
                        raise LinuxRunnerInfrastructureError(
                            "controller challenge changed during candidate execution"
                        )
                    assert challenge_evidence is not None
                    if (
                        candidate_tree_digest(
                            challenge_snapshot,
                            allow_internal_symlinks=True,
                        )
                        != challenge_evidence.copied
                    ):
                        raise LinuxRunnerInfrastructureError("read-only challenge snapshot drifted")

                runtime_post = runtime_tree_digest(attestation.runtime_root)
                if runtime_post != runtime_pre:
                    raise LinuxRunnerInfrastructureError(
                        "runtime image drifted during candidate execution"
                    )
                for label, path, expected in (
                    ("bubblewrap", attestation.bubblewrap_path, attestation.bubblewrap_sha256),
                    ("seccomp", attestation.seccomp_path, attestation.seccomp_sha256),
                    (
                        "launcher executable",
                        attestation.launcher_executable_path,
                        attestation.launcher_executable_sha256,
                    ),
                    (
                        "launcher module",
                        attestation.launcher_module_path,
                        attestation.launcher_module_sha256,
                    ),
                    (
                        "trusted verifier contract",
                        attestation.contract_path,
                        attestation.contract_sha256,
                    ),
                    ("controller", attestation.controller_path, attestation.controller_sha256),
                    ("worker", attestation.worker_path, attestation.worker_sha256),
                ):
                    _assert_digest(path, expected, label)

                # The captures are deliberately bounded even though controller
                # grading consumes only the narrow RPC channel.  Keeping their
                # digests in local state makes overflow/resource classification
                # deterministic without exposing candidate bytes as verdicts.
                _ = (
                    sha256(candidate_stdout).hexdigest(),
                    sha256(candidate_stderr).hexdigest(),
                    timed_out,
                    resources.to_dict(),
                    probe,
                )
                complete = {
                    "challenge_manifest_sha256": parsed.challenge_manifest_sha256,
                    "kind": "runner_complete",
                    "run_nonce": parsed.run_nonce,
                    "tree_terminated": True,
                    "version": 1,
                    "worker_exit_code": worker_exit_code,
                    "workspace_manifest_sha256": parsed.workspace_manifest_sha256,
                }
            finally:
                listener.close()
                if cgroup.path is not None:
                    cgroup.kill_and_reap(ignore_missing=True)
                if process is not None and process.poll() is None:
                    with suppress(ProcessLookupError, PermissionError):
                        os.killpg(process.pid, signal.SIGKILL)
                    with suppress(subprocess.TimeoutExpired):
                        process.wait(timeout=attestation.limits.reap_timeout_seconds)
                if candidate_parent.fileno() >= 0:
                    candidate_parent.close()

        if complete is None:
            raise LinuxRunnerInfrastructureError(
                "trusted launcher did not construct completion evidence"
            )
        _send_control_frame(control_channel, complete)
        return 0
    finally:
        if controller_protocol is not None:
            controller_protocol.close()
        if control_channel is not None:
            control_channel.close()


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        if arguments and arguments[0] == "worker":
            return worker_cli(arguments)
        if arguments and arguments[0] == "validate":
            import argparse

            class InfrastructureParser(argparse.ArgumentParser):
                def error(self, message: str) -> NoReturn:
                    _worker_cli_error(f"invalid validate invocation: {message}")

            parser = InfrastructureParser(
                prog="echo-hardened-verifier-validate",
                allow_abbrev=False,
            )
            parser.add_argument("validate", choices=["validate"])
            parser.add_argument("--attestation", required=True)
            parsed = parser.parse_args(arguments)
            attestation = load_attestation(parsed.attestation)
            sys.stdout.buffer.write(_canonical_json(attestation.public_dict()) + b"\n")
            sys.stdout.buffer.flush()
            return 0
        raise LinuxRunnerInfrastructureError(
            "usage: linux_hardened_verifier.py validate --attestation ABS | worker ..."
        )
    except LinuxRunnerInfrastructureError as exc:
        print(f"hardened verifier infrastructure invalid: {exc}", file=sys.stderr)
        return INFRASTRUCTURE_INVALID_EXIT
    except Exception as exc:  # noqa: BLE001 - trusted CLI must fail closed as infrastructure
        print(
            f"hardened verifier infrastructure failed unexpectedly: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return INFRASTRUCTURE_INVALID_EXIT


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ATTESTATION_SCHEMA",
    "CANDIDATE_API_ISOLATION_SCHEMA",
    "CANDIDATE_WORKER_CONTRACT_SCHEMA",
    "CONFIG_ENV",
    "CandidateSnapshotViolation",
    "DEFAULT_LIMITS",
    "HardenedProcessResult",
    "INFRASTRUCTURE_INVALID_EXIT",
    "LinuxHardenedVerifierRunner",
    "LinuxRunnerInfrastructureError",
    "OUTPUT_LIMIT_EXIT",
    "PROTOCOL_LIMIT_EXIT",
    "RESOURCE_LIMIT_EXIT",
    "RUNNER_BACKEND",
    "RunnerAttestation",
    "RunnerLimits",
    "SnapshotEvidence",
    "TreeDigest",
    "WORKER_CLI_ENV",
    "WORKER_CONFIG_ENV",
    "WORKER_CONFIG_SHA256_ENV",
    "WORKER_CONTRACT_SHA256_ENV",
    "WORKER_RPC",
    "candidate_tree_digest",
    "create_candidate_snapshot",
    "load_attestation",
    "read_rpc_frame",
    "runtime_tree_digest",
    "worker_cli",
    "worker_isolation_contract",
    "worker_limit_contract",
    "worker_rpc_contract",
    "write_rpc_frame",
]


