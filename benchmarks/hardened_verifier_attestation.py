"""Provision and validate the Linux hidden-verifier v2 attestation.

Generation is intentionally root-only.  It does not make an arbitrary path
trusted merely because a command-line flag named it: the repository must be a
clean Git checkout, the runtime and executable inputs must be root-owned and
not writable by the eventual runner, and the seccomp program must exactly
match the built-in policy generated through libseccomp.

The resulting canonical JSON is immutable operator evidence.  Normal benchmark
execution subsequently runs unprivileged as the attested uid/gid and rehashes
all bound inputs before and after every invocation.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import errno
import json
import os
import stat
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from benchmarks.linux_hardened_verifier import (
    ATTESTATION_SCHEMA,
    CANDIDATE_API_ISOLATION_SCHEMA,
    CANDIDATE_WORKER_CONTRACT_SCHEMA,
    DEFAULT_LIMITS,
    RUNNER_BACKEND,
    LinuxRunnerInfrastructureError,
    _assert_protected_ancestry,
    _assert_root_protected,
    _canonical_json,
    _file_sha256,
    _validate_cgroup_parent,
    _validate_runtime_mountpoints,
    _validate_scratch_mount,
    load_attestation,
    runtime_tree_digest,
    worker_isolation_contract,
    worker_limit_contract,
    worker_rpc_contract,
)

_SCMP_ACT_ALLOW = 0x7FFF0000
_SCMP_ACT_ERRNO = 0x00050000

# These calls are unnecessary to evaluate pure candidate behavior and expand
# kernel attack surface or permit the worker to manufacture a network endpoint.
# Existing connected AF_UNIX protocol FDs continue to support read/write.
_DENIED_SYSCALLS = (
    "_sysctl",
    "accept",
    "accept4",
    "add_key",
    "bind",
    "bpf",
    "chroot",
    "connect",
    "delete_module",
    "fanotify_init",
    "finit_module",
    "fsconfig",
    "fsmount",
    "fsopen",
    "fspick",
    "init_module",
    "io_uring_enter",
    "io_uring_register",
    "io_uring_setup",
    "ioperm",
    "iopl",
    "kcmp",
    "kexec_file_load",
    "kexec_load",
    "keyctl",
    "listen",
    "lookup_dcookie",
    "mount",
    "move_mount",
    "name_to_handle_at",
    "nfsservctl",
    "open_by_handle_at",
    "open_tree",
    "perf_event_open",
    "pivot_root",
    "process_vm_readv",
    "process_vm_writev",
    "ptrace",
    "quotactl",
    "reboot",
    "request_key",
    "setns",
    "socket",
    "socketpair",
    "swapon",
    "swapoff",
    "sysfs",
    "umount2",
    "unshare",
    "userfaultfd",
    "vhangup",
)


def _require_root() -> None:
    if os.geteuid() != 0:
        raise LinuxRunnerInfrastructureError(
            "hardened verifier attestation generation must run as root"
        )


def _protected_regular(path: Path, label: str) -> None:
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


def _git_evidence(repository_root: Path) -> str:
    try:
        completed = subprocess.run(
            [
                "git",
                "-C",
                str(repository_root),
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
        )
        revision = subprocess.run(
            ["git", "-C", str(repository_root), "rev-parse", "HEAD"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LinuxRunnerInfrastructureError(f"cannot attest repository Git state: {exc}") from exc
    if completed.returncode != 0 or completed.stdout:
        raise LinuxRunnerInfrastructureError(
            "hardened verifier attestation requires a completely clean Git checkout"
        )
    git_sha = revision.stdout.strip()
    if revision.returncode != 0 or len(git_sha) not in {40, 64}:
        raise LinuxRunnerInfrastructureError("cannot resolve attested repository Git SHA")
    return git_sha


def generate_seccomp_program(output: str | Path) -> str:
    """Generate the fixed libseccomp BPF program and return its SHA-256."""

    _require_root()
    destination = Path(output)
    if not destination.is_absolute():
        raise LinuxRunnerInfrastructureError("seccomp output path must be absolute")
    library_name = ctypes.util.find_library("seccomp")
    if not library_name:
        raise LinuxRunnerInfrastructureError("libseccomp is required to generate the fixed policy")
    library = ctypes.CDLL(library_name, use_errno=True)
    library.seccomp_init.argtypes = [ctypes.c_uint32]
    library.seccomp_init.restype = ctypes.c_void_p
    library.seccomp_release.argtypes = [ctypes.c_void_p]
    library.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    library.seccomp_syscall_resolve_name.restype = ctypes.c_int
    library.seccomp_rule_add.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_int,
        ctypes.c_uint,
    ]
    library.seccomp_rule_add.restype = ctypes.c_int
    library.seccomp_export_bpf.argtypes = [ctypes.c_void_p, ctypes.c_int]
    library.seccomp_export_bpf.restype = ctypes.c_int
    context = library.seccomp_init(_SCMP_ACT_ALLOW)
    if not context:
        raise LinuxRunnerInfrastructureError("libseccomp could not initialize the fixed policy")
    try:
        action = _SCMP_ACT_ERRNO | errno.EPERM
        resolved = 0
        for name in _DENIED_SYSCALLS:
            syscall_number = library.seccomp_syscall_resolve_name(name.encode("ascii"))
            if syscall_number < 0:
                continue
            result = library.seccomp_rule_add(context, action, syscall_number, 0)
            if result != 0:
                raise LinuxRunnerInfrastructureError(
                    f"libseccomp rejected the fixed {name} rule: {-result}"
                )
            resolved += 1
        if resolved < 25:
            raise LinuxRunnerInfrastructureError(
                "libseccomp resolved too few fixed-policy syscalls for this architecture"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            dir=destination.parent,
        )
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o444)
            result = library.seccomp_export_bpf(context, descriptor)
            if result != 0:
                raise LinuxRunnerInfrastructureError(
                    f"libseccomp could not export the fixed policy: {-result}"
                )
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            os.replace(temporary, destination)
            os.chown(destination, 0, 0)
            destination.chmod(0o444)
        except Exception:
            if descriptor >= 0:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)
            raise
    finally:
        library.seccomp_release(context)
    return _file_sha256(destination)


def validate_fixed_seccomp_program(path: str | Path) -> str:
    """Regenerate the policy and prove that a provisioned BPF file is exact."""

    candidate = Path(path)
    _protected_regular(candidate, "seccomp BPF program")
    with tempfile.TemporaryDirectory(prefix="echo-seccomp-verify-") as temporary:
        regenerated = Path(temporary) / "policy.bpf"
        expected = generate_seccomp_program(regenerated)
        observed = _file_sha256(candidate)
        if observed != expected or candidate.read_bytes() != regenerated.read_bytes():
            raise LinuxRunnerInfrastructureError(
                "provisioned seccomp BPF does not match the source-bound fixed policy"
            )
    return observed


def generate_attestation(
    *,
    output: str | Path,
    repository_root: str | Path,
    bubblewrap: str | Path,
    seccomp: str | Path,
    runtime_root: str | Path,
    runtime_python: str,
    cgroup_parent: str | Path,
    scratch_mount: str | Path,
    launcher_executable: str | Path,
    launcher_module: str | Path,
    contract: str | Path,
    controller: str | Path,
    worker: str | Path,
    provisioned_uid: int,
    provisioned_gid: int,
) -> dict[str, Any]:
    """Create canonical root-owned evidence from already-provisioned inputs."""

    _require_root()
    destination = Path(output)
    if not destination.is_absolute():
        raise LinuxRunnerInfrastructureError("attestation output path must be absolute")
    repo = Path(repository_root).resolve(strict=True)
    runtime = Path(runtime_root).resolve(strict=True)
    bound_files = {
        "bubblewrap": Path(bubblewrap).resolve(strict=True),
        "seccomp": Path(seccomp).resolve(strict=True),
        "launcher executable": Path(launcher_executable).resolve(strict=True),
        "launcher module": Path(launcher_module).resolve(strict=True),
        "contract": Path(contract).resolve(strict=True),
        "controller": Path(controller).resolve(strict=True),
        "worker": Path(worker).resolve(strict=True),
    }
    for label, path in bound_files.items():
        _protected_regular(path, label)
    if bound_files["contract"] != bound_files["controller"].with_name(
        "trusted_verifier_contract.py"
    ):
        raise LinuxRunnerInfrastructureError(
            "trusted verifier contract must be provisioned beside its controller"
        )
    git_sha = _git_evidence(repo)
    seccomp_sha = validate_fixed_seccomp_program(bound_files["seccomp"])
    _validate_runtime_mountpoints(runtime)
    runtime_digest = runtime_tree_digest(runtime)
    inside_python = Path(runtime_python)
    if not inside_python.is_absolute() or ".." in inside_python.parts:
        raise LinuxRunnerInfrastructureError("runtime Python must be an absolute in-image path")
    host_python = runtime / inside_python.relative_to("/")
    _protected_regular(host_python.resolve(strict=True), "runtime Python")
    cgroup = Path(cgroup_parent).resolve(strict=True)
    scratch = Path(scratch_mount).resolve(strict=True)
    _validate_cgroup_parent(cgroup, require_current_membership=False)
    _validate_scratch_mount(
        scratch,
        DEFAULT_LIMITS,
        expected_uid=provisioned_uid,
        expected_gid=provisioned_gid,
    )
    payload: dict[str, Any] = {
        "authorization": True,
        "backend": RUNNER_BACKEND,
        "bubblewrap": {
            "path": str(bound_files["bubblewrap"]),
            "sha256": _file_sha256(bound_files["bubblewrap"]),
        },
        "candidate_worker_contract_schema": CANDIDATE_WORKER_CONTRACT_SCHEMA,
        "candidate_api_isolation_schema": CANDIDATE_API_ISOLATION_SCHEMA,
        "cgroup_parent": str(cgroup),
        "contract": {
            "path": str(bound_files["contract"]),
            "sha256": _file_sha256(bound_files["contract"]),
        },
        "controller": {
            "path": str(bound_files["controller"]),
            "sha256": _file_sha256(bound_files["controller"]),
        },
        "git_sha": git_sha,
        "isolation": worker_isolation_contract(),
        "launcher": {
            "argv": [
                str(bound_files["launcher executable"]),
                "-I",
                str(bound_files["launcher module"]),
            ],
            "executable_sha256": _file_sha256(bound_files["launcher executable"]),
            "module_path": str(bound_files["launcher module"]),
            "module_sha256": _file_sha256(bound_files["launcher module"]),
        },
        "limits": worker_limit_contract(),
        "provisioned_gid": int(provisioned_gid),
        "provisioned_uid": int(provisioned_uid),
        "repository_root": str(repo),
        "resource_limits": DEFAULT_LIMITS.to_dict(),
        "runtime": {
            "python": runtime_python,
            "root": str(runtime),
            "tree_sha256": runtime_digest.sha256,
        },
        "rpc": worker_rpc_contract(),
        "schema": ATTESTATION_SCHEMA,
        "scratch_mount": str(scratch),
        "seccomp": {
            "path": str(bound_files["seccomp"]),
            "sha256": seccomp_sha,
        },
        "worker": {
            "path": str(bound_files["worker"]),
            "sha256": _file_sha256(bound_files["worker"]),
        },
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    _assert_root_protected(destination.parent, "attestation output directory")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o444)
        content = _canonical_json(payload) + b"\n"
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, destination)
        os.chown(destination, 0, 0)
        destination.chmod(0o444)
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
        raise
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    seccomp = subparsers.add_parser("generate-seccomp")
    seccomp.add_argument("--output", required=True)
    generate = subparsers.add_parser("generate")
    generate.add_argument("--output", required=True)
    generate.add_argument("--repository-root", required=True)
    generate.add_argument("--bubblewrap", required=True)
    generate.add_argument("--seccomp", required=True)
    generate.add_argument("--runtime-root", required=True)
    generate.add_argument("--runtime-python", required=True)
    generate.add_argument("--cgroup-parent", required=True)
    generate.add_argument("--scratch-mount", required=True)
    generate.add_argument("--launcher-executable", required=True)
    generate.add_argument("--launcher-module", required=True)
    generate.add_argument("--contract", required=True)
    generate.add_argument("--controller", required=True)
    generate.add_argument("--worker", required=True)
    generate.add_argument("--provisioned-uid", required=True, type=int)
    generate.add_argument("--provisioned-gid", required=True, type=int)
    validate = subparsers.add_parser("validate")
    validate.add_argument("--attestation", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parsed = _parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        if parsed.command == "generate-seccomp":
            digest = generate_seccomp_program(parsed.output)
            print(json.dumps({"path": parsed.output, "sha256": digest}, sort_keys=True))
            return 0
        if parsed.command == "generate":
            payload = generate_attestation(
                output=parsed.output,
                repository_root=parsed.repository_root,
                bubblewrap=parsed.bubblewrap,
                seccomp=parsed.seccomp,
                runtime_root=parsed.runtime_root,
                runtime_python=parsed.runtime_python,
                cgroup_parent=parsed.cgroup_parent,
                scratch_mount=parsed.scratch_mount,
                launcher_executable=parsed.launcher_executable,
                launcher_module=parsed.launcher_module,
                contract=parsed.contract,
                controller=parsed.controller,
                worker=parsed.worker,
                provisioned_uid=parsed.provisioned_uid,
                provisioned_gid=parsed.provisioned_gid,
            )
            print(json.dumps(payload, sort_keys=True))
            return 0
        attestation = load_attestation(parsed.attestation)
        print(json.dumps(attestation.public_dict(), sort_keys=True))
        return 0
    except LinuxRunnerInfrastructureError as exc:
        print(f"invalid hardened verifier infrastructure: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "generate_attestation",
    "generate_seccomp_program",
    "main",
    "validate_fixed_seccomp_program",
]


