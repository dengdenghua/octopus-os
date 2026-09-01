#!/usr/bin/env python3
"""Verify a dedicated host before registering the Echo OS image runner.

This is the host-side counterpart to ``verify-linux-image-runner.py``.  It is
run as the non-root account that will own the GitHub Actions runner.  The
workflow verifier still rechecks resources and build tools from inside the
privileged Debian job container before creating any signing identities.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

SCHEMA = 1
GIB = 1024**3
MIN_EFFECTIVE_CPUS = 4.0
MIN_EFFECTIVE_MEMORY_BYTES = 16 * GIB
# GitHub places both GITHUB_WORKSPACE and RUNNER_TEMP below the runner work
# directory.  The in-container policy therefore accumulates the 48 GiB
# workspace and 160 GiB scratch requirements on this one host filesystem.
MIN_WORK_ROOT_FREE_BYTES = 208 * GIB
MIN_LOOP_DEVICES = 64
MIN_NBD_DEVICES = 16
MIN_NBD_PARTITIONS = 16
MAX_COMMAND_OUTPUT = 4096
RUNNER_WORK_ROOT = "/srv/echo-os-image-runner"


class HostPreflightError(RuntimeError):
    """The host is not ready to own the dedicated image runner."""


@dataclass(frozen=True)
class HostFacts:
    system: str
    machine: str
    uid: int
    effective_cpus: float
    effective_memory_bytes: int
    work_root: str
    work_root_device_id: int
    work_root_free_bytes: int
    work_root_private: bool
    docker_client_present: bool
    docker_server_version: str
    docker_context: str
    docker_environment_clean: bool
    docker_socket_ready: bool
    docker_security_options_valid: bool
    docker_security_options: tuple[str, ...]
    kvm_device_ready: bool
    kernel_modules_tree_ready: bool
    loop_max: int
    nbd_max: int
    nbd_max_part: int


def _bounded_command(arguments: Sequence[str]) -> str:
    try:
        completed = subprocess.run(
            list(arguments),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout[:MAX_COMMAND_OUTPUT].strip()


def _read_positive_integer(path: Path) -> int:
    try:
        value = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return 0
    return int(value) if value.isdigit() and int(value) > 0 else 0


def _effective_cpu_count() -> float:
    candidates: list[float] = []
    count = os.cpu_count()
    if count:
        candidates.append(float(count))
    if hasattr(os, "sched_getaffinity"):
        with contextlib.suppress(OSError):
            candidates.append(float(len(os.sched_getaffinity(0))))
    try:
        quota, period = Path("/sys/fs/cgroup/cpu.max").read_text(encoding="ascii").split()
        if quota != "max" and int(quota) > 0 and int(period) > 0:
            candidates.append(int(quota) / int(period))
    except (OSError, UnicodeError, ValueError):
        pass
    return min(candidates) if candidates else 0.0


def _effective_memory_bytes() -> int:
    candidates: list[int] = []
    try:
        for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
            match = re.fullmatch(r"MemTotal:\s+([0-9]+)\s+kB", line)
            if match:
                candidates.append(int(match.group(1)) * 1024)
                break
    except (OSError, UnicodeError):
        pass
    for candidate in (
        Path("/sys/fs/cgroup/memory.max"),
        Path("/sys/fs/cgroup/memory/memory.limit_in_bytes"),
    ):
        value = _read_positive_integer(candidate)
        if value and value < (1 << 60):
            candidates.append(value)
    return min(candidates) if candidates else 0


def _kvm_device_ready() -> bool:
    device = Path("/dev/kvm")
    try:
        metadata = device.lstat()
        if not stat.S_ISCHR(metadata.st_mode) or device.is_symlink():
            return False
        flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(device, flags)
    except OSError:
        return False
    os.close(descriptor)
    return True


def _docker_socket_ready() -> bool:
    socket_path = Path("/var/run/docker.sock")
    try:
        metadata = socket_path.lstat()
    except OSError:
        return False
    return stat.S_ISSOCK(metadata.st_mode) and not socket_path.is_symlink()


def _docker_security_options() -> tuple[str, ...] | None:
    output = _bounded_command(("docker", "info", "--format", "{{json .SecurityOptions}}"))
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, list) or len(parsed) > 32:
        return None
    if not all(isinstance(item, str) and 0 < len(item) <= 256 for item in parsed):
        return None
    return tuple(parsed)


def collect_facts(work_root: str) -> HostFacts:
    requested = Path(work_root)
    if requested != Path(RUNNER_WORK_ROOT):
        raise HostPreflightError(f"runner work root must be the dedicated {RUNNER_WORK_ROOT} path")
    if not requested.is_absolute() or requested.is_symlink():
        raise HostPreflightError("runner work root must be an absolute non-symlink path")
    try:
        resolved = requested.resolve(strict=True)
    except OSError as error:
        raise HostPreflightError("runner work root does not exist") from error
    if resolved != requested or not resolved.is_dir():
        raise HostPreflightError("runner work root must be a canonical directory")
    metadata = resolved.stat()
    filesystem = os.statvfs(resolved)
    mode = stat.S_IMODE(metadata.st_mode)
    current_uid = os.geteuid() if hasattr(os, "geteuid") else -1
    docker_version = _bounded_command(("docker", "version", "--format", "{{.Server.Version}}"))
    docker_security_options = _docker_security_options()
    kernel_release = platform.release()
    modules_tree = Path("/lib/modules") / kernel_release
    return HostFacts(
        system=platform.system(),
        machine=platform.machine().lower(),
        uid=current_uid,
        effective_cpus=_effective_cpu_count(),
        effective_memory_bytes=_effective_memory_bytes(),
        work_root=str(resolved),
        work_root_device_id=metadata.st_dev,
        work_root_free_bytes=filesystem.f_bavail * filesystem.f_frsize,
        work_root_private=(
            metadata.st_uid == current_uid
            and mode & 0o077 == 0
            and os.access(resolved, os.R_OK | os.W_OK | os.X_OK)
        ),
        docker_client_present=shutil.which("docker") is not None,
        docker_server_version=docker_version,
        docker_context=_bounded_command(("docker", "context", "show")),
        docker_environment_clean=not any(
            os.environ.get(name) for name in ("DOCKER_HOST", "DOCKER_CONTEXT")
        ),
        docker_socket_ready=_docker_socket_ready(),
        docker_security_options_valid=docker_security_options is not None,
        docker_security_options=docker_security_options or (),
        kvm_device_ready=_kvm_device_ready(),
        kernel_modules_tree_ready=(modules_tree.is_dir() and not modules_tree.is_symlink()),
        loop_max=_read_positive_integer(Path("/sys/module/loop/parameters/max_loop")),
        nbd_max=_read_positive_integer(Path("/sys/module/nbd/parameters/nbds_max")),
        nbd_max_part=_read_positive_integer(Path("/sys/module/nbd/parameters/max_part")),
    )


def validate_facts(facts: HostFacts) -> tuple[str, ...]:
    errors: list[str] = []
    if facts.system != "Linux":
        errors.append("runner host must use a Linux kernel")
    if facts.machine not in {"x86_64", "amd64"}:
        errors.append("runner host must be x86-64")
    if facts.uid <= 0:
        errors.append("runner service account must be a non-root local user")
    if facts.work_root != RUNNER_WORK_ROOT:
        errors.append(f"runner work root must be {RUNNER_WORK_ROOT}")
    if facts.effective_cpus < MIN_EFFECTIVE_CPUS:
        errors.append("runner host has fewer than four effective CPUs")
    if facts.effective_memory_bytes < MIN_EFFECTIVE_MEMORY_BYTES:
        errors.append("runner host has less than 16 GiB effective memory")
    if facts.work_root_device_id < 0 or facts.work_root_free_bytes < MIN_WORK_ROOT_FREE_BYTES:
        errors.append("runner work root has less than 208 GiB free")
    if not facts.work_root_private:
        errors.append("runner work root must be owned by the service user with mode 0700")
    if not facts.docker_client_present or not re.fullmatch(
        r"[0-9][0-9A-Za-z.+_~:-]{0,63}", facts.docker_server_version
    ):
        errors.append("runner service user cannot reach a healthy Docker server")
    if (
        facts.docker_context != "default"
        or not facts.docker_environment_clean
        or not facts.docker_socket_ready
    ):
        errors.append("runner must use the local default Docker socket")
    if not facts.docker_security_options_valid or any(
        option == "name=rootless" for option in facts.docker_security_options
    ):
        errors.append("runner Docker server must be rootful for privileged device jobs")
    if not facts.kvm_device_ready:
        errors.append("runner service user cannot open the KVM character device")
    if not facts.kernel_modules_tree_ready:
        errors.append("the running kernel module tree is unavailable")
    if facts.loop_max < MIN_LOOP_DEVICES:
        errors.append("loop max_loop is lower than 64")
    if facts.nbd_max < MIN_NBD_DEVICES:
        errors.append("NBD nbds_max is lower than 16")
    if facts.nbd_max_part < MIN_NBD_PARTITIONS:
        errors.append("NBD max_part is lower than 16")
    return tuple(errors)


def success_marker(facts: HostFacts) -> str:
    errors = validate_facts(facts)
    if errors:
        raise HostPreflightError("; ".join(errors))
    return (
        "ECHO_IMAGE_RUNNER_HOST_READY arch=x86_64 "
        f"cpu={int(facts.effective_cpus)} "
        f"memory-gib={facts.effective_memory_bytes // GIB} "
        f"work-free-gib={facts.work_root_free_bytes // GIB} "
        f"work-device={facts.work_root_device_id} "
        f"docker={facts.docker_server_version} docker-context=default "
        f"docker-mode=rootful kvm=ready "
        f"loop-max={facts.loop_max} nbd-max={facts.nbd_max} "
        f"nbd-max-part={facts.nbd_max_part}"
    )


def evidence_payload(facts: HostFacts) -> dict[str, object]:
    serialized_facts = asdict(facts)
    serialized_facts["docker_security_options"] = list(facts.docker_security_options)
    return {
        "schema": SCHEMA,
        "kind": "echo-os-image-runner-host-preflight",
        "marker": success_marker(facts),
        "facts": serialized_facts,
        "policy": {
            "minimum_effective_cpus": MIN_EFFECTIVE_CPUS,
            "minimum_effective_memory_bytes": MIN_EFFECTIVE_MEMORY_BYTES,
            "minimum_work_root_free_bytes": MIN_WORK_ROOT_FREE_BYTES,
            "minimum_loop_devices": MIN_LOOP_DEVICES,
            "minimum_nbd_devices": MIN_NBD_DEVICES,
            "minimum_nbd_partitions": MIN_NBD_PARTITIONS,
        },
    }


def write_evidence(path: Path, payload: dict[str, object]) -> None:
    if not path.is_absolute():
        raise HostPreflightError("host evidence output must be absolute")
    parent = path.parent.resolve(strict=True)
    if not parent.is_dir() or path.exists() or path.is_symlink():
        raise HostPreflightError("host evidence output must be a new regular path")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".echo-image-runner-host-", dir=str(parent)
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
        raise


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser()
    value.add_argument("--work-root", required=True)
    value.add_argument("--output", required=True, type=Path)
    return value


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    try:
        facts = collect_facts(arguments.work_root)
        payload = evidence_payload(facts)
        write_evidence(arguments.output, payload)
        print(payload["marker"], flush=True)
        return 0
    except (HostPreflightError, OSError) as error:
        print(f"Echo OS image runner host rejected: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
