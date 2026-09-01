#!/usr/bin/env python3
"""Fail early unless the host can build and cold-boot a full Echo OS image.

The image workflow writes a roughly 20 GiB disk, then keeps build artifacts and
an installed whole-disk copy on the workspace/scratch filesystems.  A standard
source-test runner is not automatically an image runner.  This verifier binds
the effective cgroup resources, KVM/device capacity and Secure-Boot firmware to
the release evidence before any signing identity is created.
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

SCHEMA = 2
GIB = 1024**3
MIN_EFFECTIVE_CPUS = 4.0
MIN_EFFECTIVE_MEMORY_BYTES = 16 * GIB
WORKSPACE_REQUIRED_BYTES = 48 * GIB
# The restore gate retains the installed and provisioned disks while it makes
# work, rollback and trial-login copies. Each installed disk is roughly 21 GiB,
# so the observed whole-disk peak is above 100 GiB before mkosi trees, caches,
# logs and TPM state. Keep those transient writes on the measured scratch
# filesystem and leave a material failure/debugging margin.
SCRATCH_REQUIRED_BYTES = 160 * GIB
MIN_FREE_LOOP_DEVICES = 4
MIN_FREE_NBD_DEVICES = 2
MAX_COMMAND_OUTPUT = 64 * 1024
MAX_FIRMWARE_DESCRIPTORS = 256
MAX_FIRMWARE_DESCRIPTOR_BYTES = 1024 * 1024
CONTAINER_WORK_ROOT = Path("/__w")
EXPECTED_WORKSPACE = CONTAINER_WORK_ROOT / "echo-os" / "echo-os"
EXPECTED_SCRATCH = CONTAINER_WORK_ROOT / "_temp"
REQUIRED_TOOLS = (
    "cryptsetup",
    "gpg",
    "gpgv",
    "losetup",
    "mcopy",
    "mkosi",
    "modprobe",
    "qemu-nbd",
    "qemu-system-x86_64",
    "sbsign",
    "sbverify",
    "sfdisk",
    "swtpm",
    "systemd-dissect",
    "systemd-repart",
    "udevadm",
    "veritysetup",
    "zstd",
)


class RunnerPreflightError(RuntimeError):
    """The current host cannot run the full image acceptance workflow."""


@dataclass(frozen=True)
class StorageFact:
    role: str
    path: str
    device_id: int
    free_bytes: int
    required_bytes: int


@dataclass(frozen=True)
class RunnerFacts:
    system: str
    machine: str
    uid: int
    effective_cpus: float
    effective_memory_bytes: int
    storage: tuple[StorageFact, ...]
    kvm_device_ready: bool
    qemu_kvm_supported: bool
    free_loop_devices: int
    free_nbd_devices: int
    secure_boot_firmware_descriptors: int
    available_tools: tuple[str, ...]


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
    return completed.stdout[:MAX_COMMAND_OUTPUT] if completed.returncode == 0 else ""


def _read_positive_integer(path: Path) -> int | None:
    try:
        value = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return None
    if not value.isdigit():
        return None
    parsed = int(value)
    return parsed if parsed > 0 else None


def _effective_memory_bytes() -> int:
    total = 0
    try:
        for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
            match = re.fullmatch(r"MemTotal:\s+([0-9]+)\s+kB", line)
            if match:
                total = int(match.group(1)) * 1024
                break
    except (OSError, UnicodeError):
        pass
    limits = [total] if total > 0 else []
    for candidate in (
        Path("/sys/fs/cgroup/memory.max"),
        Path("/sys/fs/cgroup/memory/memory.limit_in_bytes"),
    ):
        value = _read_positive_integer(candidate)
        if value is not None and value < (1 << 60):
            limits.append(value)
    return min(limits) if limits else 0


def _cpuset_count(value: str) -> int | None:
    total = 0
    try:
        for component in value.strip().split(","):
            if not component:
                continue
            if "-" in component:
                first, last = (int(item) for item in component.split("-", 1))
                if first < 0 or last < first:
                    return None
                total += last - first + 1
            else:
                if int(component) < 0:
                    return None
                total += 1
    except ValueError:
        return None
    return total if total > 0 else None


def _effective_cpu_count() -> float:
    candidates: list[float] = []
    count = os.cpu_count()
    if count:
        candidates.append(float(count))
    if hasattr(os, "sched_getaffinity"):
        with contextlib.suppress(OSError):
            candidates.append(float(len(os.sched_getaffinity(0))))
    try:
        cpuset = Path("/sys/fs/cgroup/cpuset.cpus.effective").read_text(encoding="ascii")
        cpuset_count = _cpuset_count(cpuset)
        if cpuset_count is not None:
            candidates.append(float(cpuset_count))
    except (OSError, UnicodeError):
        pass
    try:
        quota, period = Path("/sys/fs/cgroup/cpu.max").read_text(encoding="ascii").split()
        if quota != "max" and int(quota) > 0 and int(period) > 0:
            candidates.append(int(quota) / int(period))
    except (OSError, UnicodeError, ValueError):
        pass
    return min(candidates) if candidates else 0.0


def _storage_fact(
    path: str,
    role: str,
    required_bytes: int,
    expected_path: Path,
) -> StorageFact:
    resolved = Path(path).resolve(strict=True)
    if not resolved.is_dir():
        raise RunnerPreflightError(f"{role} path is not a directory")
    if resolved != expected_path:
        raise RunnerPreflightError(f"{role} path must be the dedicated {expected_path} directory")
    metadata = os.stat(resolved)
    filesystem = os.statvfs(resolved)
    return StorageFact(
        role=role,
        path=str(resolved),
        device_id=metadata.st_dev,
        free_bytes=filesystem.f_bavail * filesystem.f_frsize,
        required_bytes=required_bytes,
    )


def _kvm_device_ready() -> bool:
    path = Path("/dev/kvm")
    try:
        metadata = path.lstat()
        if not stat.S_ISCHR(metadata.st_mode) or path.is_symlink():
            return False
        flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
    except OSError:
        return False
    os.close(descriptor)
    return True


def _qemu_kvm_supported() -> bool:
    output = _bounded_command(("qemu-system-x86_64", "-accel", "help"))
    return any(
        line.strip().split(maxsplit=1)[0] == "kvm" for line in output.splitlines() if line.strip()
    )


def _free_loop_devices() -> int:
    used = {
        line.strip()
        for line in _bounded_command(
            ("losetup", "--list", "--noheadings", "--output", "NAME")
        ).splitlines()
        if line.strip()
    }
    count = 0
    for candidate in Path("/dev").glob("loop[0-9]*"):
        try:
            if stat.S_ISBLK(candidate.lstat().st_mode) and str(candidate) not in used:
                count += 1
        except OSError:
            continue
    return count


def _free_nbd_devices() -> int:
    count = 0
    for candidate in Path("/sys/class/block").glob("nbd[0-9]*"):
        device = Path("/dev") / candidate.name
        try:
            if not stat.S_ISBLK(device.lstat().st_mode):
                continue
        except OSError:
            continue
        pid = _read_positive_integer(candidate / "pid")
        if pid is None:
            count += 1
    return count


def _secure_boot_firmware_descriptors() -> int:
    matches = 0
    seen = 0
    for root in (Path("/etc/qemu/firmware"), Path("/usr/share/qemu/firmware")):
        if not root.is_dir() or root.is_symlink():
            continue
        for candidate in sorted(root.glob("*.json")):
            seen += 1
            if seen > MAX_FIRMWARE_DESCRIPTORS:
                return 0
            try:
                metadata = candidate.lstat()
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or candidate.is_symlink()
                    or metadata.st_size <= 0
                    or metadata.st_size > MAX_FIRMWARE_DESCRIPTOR_BYTES
                ):
                    continue
                descriptor = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            if not isinstance(descriptor, dict):
                continue
            features = descriptor.get("features")
            targets = descriptor.get("targets")
            if not isinstance(features, list) or "secure-boot" not in features:
                continue
            if not isinstance(targets, list):
                continue
            if any(
                isinstance(target, dict) and target.get("architecture") in {"x86_64", "amd64"}
                for target in targets
            ):
                matches += 1
    return matches


def collect_facts(workspace: str, scratch: str) -> RunnerFacts:
    tools = tuple(name for name in REQUIRED_TOOLS if shutil.which(name) is not None)
    return RunnerFacts(
        system=platform.system(),
        machine=platform.machine().lower(),
        uid=os.geteuid() if hasattr(os, "geteuid") else -1,
        effective_cpus=_effective_cpu_count(),
        effective_memory_bytes=_effective_memory_bytes(),
        storage=(
            _storage_fact(
                workspace,
                "workspace",
                WORKSPACE_REQUIRED_BYTES,
                EXPECTED_WORKSPACE,
            ),
            _storage_fact(
                scratch,
                "scratch",
                SCRATCH_REQUIRED_BYTES,
                EXPECTED_SCRATCH,
            ),
        ),
        kvm_device_ready=_kvm_device_ready(),
        qemu_kvm_supported=_qemu_kvm_supported(),
        free_loop_devices=_free_loop_devices(),
        free_nbd_devices=_free_nbd_devices(),
        secure_boot_firmware_descriptors=_secure_boot_firmware_descriptors(),
        available_tools=tools,
    )


def validate_facts(facts: RunnerFacts) -> tuple[str, ...]:
    errors: list[str] = []
    if facts.system != "Linux":
        errors.append("runner must use a Linux kernel")
    if facts.machine not in {"x86_64", "amd64"}:
        errors.append("runner must be x86-64")
    if facts.uid != 0:
        errors.append("runner container must be privileged root")
    if facts.effective_cpus < MIN_EFFECTIVE_CPUS:
        errors.append("runner has fewer than four effective CPUs")
    if facts.effective_memory_bytes < MIN_EFFECTIVE_MEMORY_BYTES:
        errors.append("runner has less than 16 GiB effective memory")
    missing_tools = sorted(set(REQUIRED_TOOLS) - set(facts.available_tools))
    if missing_tools:
        errors.append("runner tools are missing: " + ",".join(missing_tools))
    if not facts.kvm_device_ready or not facts.qemu_kvm_supported:
        errors.append("KVM acceleration is unavailable to QEMU")
    if facts.free_loop_devices < MIN_FREE_LOOP_DEVICES:
        errors.append("fewer than four free loop devices are available")
    if facts.free_nbd_devices < MIN_FREE_NBD_DEVICES:
        errors.append("fewer than two free NBD devices are available")
    if facts.secure_boot_firmware_descriptors < 1:
        errors.append("no x86-64 Secure-Boot QEMU firmware descriptor is available")

    roles = [item.role for item in facts.storage]
    if sorted(roles) != ["scratch", "workspace"] or len(roles) != len(set(roles)):
        errors.append("runner storage facts must contain workspace and scratch once")
    expected_paths = {
        "workspace": str(EXPECTED_WORKSPACE),
        "scratch": str(EXPECTED_SCRATCH),
    }
    by_device: dict[int, dict[str, int]] = {}
    for item in facts.storage:
        if item.path != expected_paths.get(item.role):
            errors.append(f"runner {item.role} path is outside the dedicated layout")
        if item.device_id < 0 or item.free_bytes < 0 or item.required_bytes <= 0:
            errors.append("runner storage facts are invalid")
            continue
        aggregate = by_device.setdefault(item.device_id, {"free": item.free_bytes, "required": 0})
        aggregate["free"] = min(aggregate["free"], item.free_bytes)
        aggregate["required"] += item.required_bytes
    for aggregate in by_device.values():
        if aggregate["free"] < aggregate["required"]:
            errors.append("runner filesystem does not have the required free space")
    return tuple(dict.fromkeys(errors))


def success_marker(facts: RunnerFacts) -> str:
    errors = validate_facts(facts)
    if errors:
        raise RunnerPreflightError("; ".join(errors))
    by_device: dict[int, dict[str, int]] = {}
    for item in facts.storage:
        aggregate = by_device.setdefault(item.device_id, {"free": item.free_bytes, "required": 0})
        aggregate["free"] = min(aggregate["free"], item.free_bytes)
        aggregate["required"] += item.required_bytes
    minimum_margin = min(value["free"] - value["required"] for value in by_device.values())
    return (
        "ECHO_IMAGE_RUNNER_READY arch=x86_64 "
        f"cpu={int(facts.effective_cpus)} "
        f"memory-gib={facts.effective_memory_bytes // GIB} "
        f"storage-margin-gib={minimum_margin // GIB} "
        "kvm=ready "
        f"loops={facts.free_loop_devices} nbd={facts.free_nbd_devices} "
        f"secure-boot-firmware={facts.secure_boot_firmware_descriptors}"
    )


def evidence_payload(facts: RunnerFacts) -> dict[str, object]:
    marker = success_marker(facts)
    serialized_facts = asdict(facts)
    serialized_facts["storage"] = [asdict(item) for item in facts.storage]
    serialized_facts["available_tools"] = list(facts.available_tools)
    return {
        "schema": SCHEMA,
        "kind": "echo-os-image-runner-preflight",
        "marker": marker,
        "facts": serialized_facts,
        "policy": {
            "minimum_effective_cpus": MIN_EFFECTIVE_CPUS,
            "minimum_effective_memory_bytes": MIN_EFFECTIVE_MEMORY_BYTES,
            "minimum_free_loop_devices": MIN_FREE_LOOP_DEVICES,
            "minimum_free_nbd_devices": MIN_FREE_NBD_DEVICES,
            "workspace_required_bytes": WORKSPACE_REQUIRED_BYTES,
            "scratch_required_bytes": SCRATCH_REQUIRED_BYTES,
        },
    }


def write_evidence(path: Path, payload: dict[str, object]) -> None:
    if not path.is_absolute():
        raise RunnerPreflightError("runner evidence output must be absolute")
    parent = path.parent.resolve(strict=True)
    if not parent.is_dir() or path.exists() or path.is_symlink():
        raise RunnerPreflightError("runner evidence output must be a new regular path")
    descriptor, temporary_name = tempfile.mkstemp(prefix=".echo-image-runner-", dir=str(parent))
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
    value.add_argument("--workspace", required=True)
    value.add_argument("--scratch", required=True)
    value.add_argument("--output", required=True, type=Path)
    return value


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    try:
        facts = collect_facts(arguments.workspace, arguments.scratch)
        payload = evidence_payload(facts)
        write_evidence(arguments.output, payload)
        print(payload["marker"], flush=True)
        return 0
    except RunnerPreflightError as error:
        print(f"Echo OS image runner rejected: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
