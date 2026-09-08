"""OS budgets for a fixed, trusted document worker, not a security sandbox.

Windows holds one non-inherited, named Job handle in the service. The actual
Python worker joins that Job before accepting document bytes (including when a
frozen bootloader is the process returned by Popen). Linux requires a new
session, applies per-process address-space/CPU limits, and arms parent death.
Linux descendants must remain in the launcher's group for explicit cleanup;
PDEATHSIG is not inherited across fork, and these are not cgroup tree budgets.

Call terminate even after the direct child exits. Only its True result confirms
cleanup. close is a last-resort lifecycle cleanup, not a confirmation receipt.
"""

from __future__ import annotations

import contextlib
import ctypes
import math
import os
import signal
import subprocess
import sys
import time
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any


class DocumentProcessLimitError(RuntimeError):
    """A required OS protection could not be established; do not parse input."""

    def __init__(self, code: str, *, os_error: int | None = None) -> None:
        self.code = code
        self.os_error = os_error
        super().__init__(code)


def _budgets(memory_bytes: int, cpu_seconds: int) -> None:
    if (
        type(memory_bytes) is not int
        or not 0 < memory_bytes <= min(sys.maxsize, (1 << 63) - 1)
        or type(cpu_seconds) is not int
        or not 0 < cpu_seconds <= ((1 << 63) - 1) // 10_000_000
    ):
        raise DocumentProcessLimitError("invalid_resource_budget")


def _platform() -> str:
    if sys.platform not in {"win32", "linux"}:
        raise DocumentProcessLimitError("unsupported_platform")
    return sys.platform


def _pid(proc: Any) -> int:
    value = proc if type(proc) is int else proc.pid
    if type(value) is not int or not 0 < value <= 0xFFFFFFFF or value == os.getpid():
        raise DocumentProcessLimitError("invalid_worker_pid")
    return value


def _linux_stat(pid: int) -> dict[str, Any]:
    # comm may contain spaces or parentheses. Everything after the last ')'
    # starts at field 3; starttime is field 22, session is field 6.
    with Path(f"/proc/{pid}/stat").open("rb") as stream:
        raw = stream.read(8193)
    if len(raw) > 8192:
        raise DocumentProcessLimitError("invalid_process_identity")
    tail = raw.rsplit(b")", 1)[1].split()
    return {
        "state": tail[0].decode("ascii"),
        "ppid": int(tail[1]),
        "group": int(tail[2]),
        "session": int(tail[3]),
        "identity": str(int(tail[19])),
    }


class _BasicLimit(ctypes.Structure):
    _fields_ = [
        ("process_time", ctypes.c_longlong),
        ("job_time", ctypes.c_longlong),
        ("flags", ctypes.c_uint32),
        ("minimum_working_set", ctypes.c_size_t),
        ("maximum_working_set", ctypes.c_size_t),
        ("active_process_limit", ctypes.c_uint32),
        ("affinity", ctypes.c_size_t),
        ("priority_class", ctypes.c_uint32),
        ("scheduling_class", ctypes.c_uint32),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_uint64)
        for name in (
            "read_operations",
            "write_operations",
            "other_operations",
            "read_bytes",
            "write_bytes",
            "other_bytes",
        )
    ]


class _ExtendedLimit(ctypes.Structure):
    _fields_ = [
        ("basic", _BasicLimit),
        ("io", _IoCounters),
        ("process_memory", ctypes.c_size_t),
        ("job_memory", ctypes.c_size_t),
        ("peak_process_memory", ctypes.c_size_t),
        ("peak_job_memory", ctypes.c_size_t),
    ]


class _Accounting(ctypes.Structure):
    _fields_ = [
        ("total_user_time", ctypes.c_longlong),
        ("total_kernel_time", ctypes.c_longlong),
        ("period_user_time", ctypes.c_longlong),
        ("period_kernel_time", ctypes.c_longlong),
        ("page_faults", ctypes.c_uint32),
        ("total_processes", ctypes.c_uint32),
        ("active_processes", ctypes.c_uint32),
        ("terminated_processes", ctypes.c_uint32),
    ]


# PROCESS_TIME | JOB_TIME | PROCESS_MEMORY | JOB_MEMORY | KILL_ON_JOB_CLOSE.
# Neither BREAKAWAY_OK nor SILENT_BREAKAWAY_OK is permitted.
_JOB_FLAGS = 0x0002 | 0x0004 | 0x0100 | 0x0200 | 0x2000
_PROCESS_RIGHTS = 0x0100 | 0x0001 | 0x1000 | 0x00100000
_PARENT_RIGHTS = 0x1000 | 0x00100000
_JOB_WORKER_RIGHTS = 0x0001 | 0x0004  # ASSIGN_PROCESS | QUERY, never inheritable.
_WAIT_TIMEOUT = 258


@lru_cache(maxsize=1)
def _windows_api() -> Any:
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = ctypes.c_void_p
    dword = ctypes.c_uint32
    boolean = ctypes.c_int
    signatures = {
        "CreateJobObjectW": ([handle, ctypes.c_wchar_p], handle),
        "OpenJobObjectW": ([dword, boolean, ctypes.c_wchar_p], handle),
        "SetInformationJobObject": ([handle, ctypes.c_int, handle, dword], boolean),
        "QueryInformationJobObject": (
            [handle, ctypes.c_int, handle, dword, ctypes.POINTER(dword)],
            boolean,
        ),
        "AssignProcessToJobObject": ([handle, handle], boolean),
        "IsProcessInJob": ([handle, handle, ctypes.POINTER(boolean)], boolean),
        "TerminateJobObject": ([handle, dword], boolean),
        "OpenProcess": ([dword, boolean, dword], handle),
        "GetCurrentProcess": ([], handle),
        "GetProcessTimes": ([handle, handle, handle, handle, handle], boolean),
        "WaitForSingleObject": ([handle, dword], dword),
        "CloseHandle": ([handle], boolean),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(api, name)
        function.argtypes = arguments
        function.restype = result
    return api


def _win_check(result: Any, code: str) -> None:
    if not result:
        raise DocumentProcessLimitError(code, os_error=ctypes.get_last_error())


def _win_identity(api: Any, process: Any) -> str:
    times = [ctypes.c_uint64() for _ in range(4)]
    _win_check(
        api.GetProcessTimes(process, *(ctypes.byref(value) for value in times)),
        "process_identity_unavailable",
    )
    return str(times[0].value)


def _win_in_job(api: Any, process: Any, job: Any) -> bool:
    member = ctypes.c_int()
    _win_check(api.IsProcessInJob(process, job, ctypes.byref(member)), "job_membership_failed")
    return bool(member.value)


def _win_limits(api: Any, job: Any, memory: int, cpu: int) -> _ExtendedLimit:
    actual = _ExtendedLimit()
    _win_check(
        api.QueryInformationJobObject(job, 9, ctypes.byref(actual), ctypes.sizeof(actual), None),
        "job_limit_query_failed",
    )
    if (
        actual.basic.flags != _JOB_FLAGS
        or actual.process_memory != memory
        or actual.job_memory != memory
        or actual.basic.process_time != cpu * 10_000_000
        or actual.basic.job_time != cpu * 10_000_000
    ):
        raise DocumentProcessLimitError("job_limits_mismatch")
    return actual


def _win_accounting(api: Any, job: Any) -> _Accounting:
    state = _Accounting()
    _win_check(
        api.QueryInformationJobObject(job, 1, ctypes.byref(state), ctypes.sizeof(state), None),
        "job_accounting_failed",
    )
    return state


def _win_member_pids(api: Any, job: Any) -> list[int]:
    capacity = 16
    while capacity <= 4096:

        class Pids(ctypes.Structure):
            _fields_ = [
                ("assigned", ctypes.c_uint32),
                ("returned", ctypes.c_uint32),
                ("pids", ctypes.c_size_t * capacity),
            ]

        state = Pids()
        success = api.QueryInformationJobObject(
            job,
            3,
            ctypes.byref(state),
            ctypes.sizeof(state),
            None,
        )
        if not success and ctypes.get_last_error() != 234:  # ERROR_MORE_DATA
            _win_check(success, "job_member_query_failed")
        if success and state.returned == state.assigned and state.returned <= capacity:
            return [int(state.pids[index]) for index in range(state.returned)]
        capacity = max(capacity * 2, int(state.assigned))
    raise DocumentProcessLimitError("job_member_snapshot_overflow")


class ParentProcessLimits:
    """Own one extraction's OS lifetime. This object is not reusable after close.

    Construct before Popen; use worker_config to bootstrap the fixed worker.
    On Linux Popen MUST use start_new_session=True. On Windows do not request
    breakaway or inherit_handles. attach binds the launcher, while the worker's
    independent handshake must prove the actual parser is inside the same Job.
    """

    def __init__(self, memory_bytes: int, cpu_seconds: int) -> None:
        _budgets(memory_bytes, cpu_seconds)
        self.platform = _platform()
        self.memory_bytes = memory_bytes
        self.cpu_seconds = cpu_seconds
        self.parent_pid = os.getpid()
        self._closed = False
        self._job: Any = None
        self._job_name: str | None = None
        self._processes: dict[int, Any] = {}
        self._groups: dict[int, str] = {}
        if self.platform == "win32":
            api = _windows_api()
            self.parent_identity = _win_identity(api, api.GetCurrentProcess())
            self._job_name = f"Local\\EchoDocument-{uuid.uuid4().hex}"
            ctypes.set_last_error(0)
            job = api.CreateJobObjectW(None, self._job_name)
            _win_check(job, "job_creation_failed")
            self._job = job
            try:
                if ctypes.get_last_error() == 183:  # Never adopt an existing Job.
                    raise DocumentProcessLimitError("job_name_collision")
                limits = _ExtendedLimit()
                limits.basic.flags = _JOB_FLAGS
                limits.basic.process_time = cpu_seconds * 10_000_000
                limits.basic.job_time = cpu_seconds * 10_000_000
                limits.process_memory = memory_bytes
                limits.job_memory = memory_bytes
                _win_check(
                    api.SetInformationJobObject(
                        job, 9, ctypes.byref(limits), ctypes.sizeof(limits)
                    ),
                    "job_limit_setup_failed",
                )
                _win_limits(api, job, memory_bytes, cpu_seconds)
            except BaseException:
                api.CloseHandle(job)
                self._job = None
                self._closed = True
                raise
        else:
            self.parent_identity = _linux_stat(self.parent_pid)["identity"]

    def __enter__(self) -> ParentProcessLimits:
        self._check_open()
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def _check_open(self) -> None:
        if (
            self._closed
            or os.getpid() != self.parent_pid
            or (self.platform == "win32" and self._job is None)
        ):
            raise DocumentProcessLimitError("limits_context_closed_or_forked")

    def worker_config(self) -> dict[str, Any]:
        self._check_open()
        return {
            "version": 1,
            "platform": self.platform,
            "parent_pid": self.parent_pid,
            "parent_identity": self.parent_identity,
            "job_name": self._job_name,
            "memory_bytes": self.memory_bytes,
            "cpu_seconds": self.cpu_seconds,
        }

    def attach(self, proc: Any) -> None:
        """Bind only the actual process returned by this extraction's Popen."""
        self._check_open()
        pid = _pid(proc)
        if pid in self._processes or pid in self._groups:
            return
        if self.platform == "win32":
            api = _windows_api()
            process = api.OpenProcess(_PROCESS_RIGHTS, False, pid)
            _win_check(process, "worker_process_open_failed")
            try:
                if not _win_in_job(api, process, self._job):
                    _win_check(
                        api.AssignProcessToJobObject(self._job, process), "job_attach_failed"
                    )
                if not _win_in_job(api, process, self._job):
                    raise DocumentProcessLimitError("job_membership_missing")
            except BaseException:
                api.CloseHandle(process)
                raise
            self._processes[pid] = process
        else:
            facts = _linux_stat(pid)
            if facts["group"] != pid or facts["session"] != pid or pid == os.getpgrp():
                raise DocumentProcessLimitError("worker_requires_new_session")
            if facts["ppid"] != self.parent_pid:
                raise DocumentProcessLimitError("worker_parent_mismatch")
            self._groups[pid] = facts["identity"]

    def bind_worker(self, pid: int, *, identity: str) -> None:
        """Pin the ready worker's identity BEFORE sending document bytes.

        This only verifies existing containment. It cannot adopt an arbitrary
        reported PID into the Job, unlike attach which binds the trusted Popen.
        """
        self._check_open()
        pid = _pid(pid)
        if not isinstance(identity, str) or not identity.isdigit():
            raise DocumentProcessLimitError("invalid_worker_identity")
        if self.platform == "win32":
            api = _windows_api()
            handle = api.OpenProcess(_PARENT_RIGHTS, False, pid)
            _win_check(handle, "worker_identity_open_failed")
            try:
                if (
                    _win_identity(api, handle) != identity
                    or not _win_in_job(api, handle, self._job)
                    or api.WaitForSingleObject(handle, 0) != _WAIT_TIMEOUT
                ):
                    raise DocumentProcessLimitError("worker_identity_or_job_mismatch")
                if pid not in self._processes:
                    self._processes[pid] = handle
                    handle = None
            finally:
                if handle is not None:
                    _win_check(api.CloseHandle(handle), "worker_identity_handle_close_failed")
        else:
            facts = _linux_stat(pid)
            if (
                facts["identity"] != identity
                or self._groups.get(pid) != identity
                or facts["group"] != pid
                or facts["session"] != pid
                or facts["ppid"] != self.parent_pid
            ):
                raise DocumentProcessLimitError("worker_identity_or_group_mismatch")

    def _capture_job_members(self) -> None:
        api = _windows_api()
        for pid in _win_member_pids(api, self._job):
            if pid in self._processes:
                continue  # existing HANDLE pins the PID against reuse
            handle = api.OpenProcess(_PARENT_RIGHTS, False, pid)
            if not handle and ctypes.get_last_error() == 87:
                continue  # process object already gone, not an unconfirmed live member
            _win_check(handle, "job_member_open_failed")
            try:
                if (
                    not _win_in_job(api, handle, self._job)
                    and api.WaitForSingleObject(handle, 0) != 0
                ):
                    raise DocumentProcessLimitError("job_member_identity_unconfirmed")
                self._processes[pid] = handle
                handle = None
            finally:
                if handle is not None:
                    _win_check(api.CloseHandle(handle), "job_member_handle_close_failed")

    def terminate(self, proc: Any, timeout_s: float = 2.0) -> bool:
        """Kill the entire owned Job/group, reap launcher, and confirm no live members.

        Returns False on an unconfirmed cleanup; callers must not release a
        concurrency slot as though all members were gone. Job errors never fall
        back to pretending a direct-child kill proves tree cleanup.
        """
        self._check_open()
        if not isinstance(timeout_s, (int, float)) or not math.isfinite(timeout_s) or timeout_s < 0:
            raise DocumentProcessLimitError("invalid_cleanup_timeout")
        deadline = time.monotonic() + timeout_s
        pid = _pid(proc)
        covered = pid in self._processes or pid in self._groups
        if not covered:
            try:
                self.attach(proc)
                covered = True
            except (OSError, DocumentProcessLimitError):
                pass
        try:
            if self.platform == "win32":
                api = _windows_api()
                snapshot_complete = True
                before_total = None
                try:
                    before_total = _win_accounting(api, self._job).total_processes
                    self._capture_job_members()
                except DocumentProcessLimitError:
                    snapshot_complete = False
                # A failed snapshot must not prevent the kernel kill itself.
                _win_check(api.TerminateJobObject(self._job, 1), "job_termination_failed")
            elif covered:
                self._kill_group(pid)
            if not covered and hasattr(proc, "kill") and proc.poll() is None:
                proc.kill()
            if hasattr(proc, "wait"):
                proc.wait(timeout=max(0.0, deadline - time.monotonic()))
            if not covered:
                return False
            while True:
                if self.platform == "win32":
                    self._capture_job_members()
                    state = _win_accounting(api, self._job)
                    # A process spawned across the snapshot/kill window cannot
                    # be certified by a potentially stale member list.
                    if not snapshot_complete or state.total_processes != before_total:
                        return False
                    waits = [
                        api.WaitForSingleObject(handle, 0) for handle in self._processes.values()
                    ]
                    if any(value not in {0, _WAIT_TIMEOUT} for value in waits):
                        return False
                    done = state.active_processes == 0 and all(value == 0 for value in waits)
                else:
                    done = not self._live_group(pid)
                if done:
                    return True
                if time.monotonic() >= deadline:
                    return False
                time.sleep(min(0.02, max(0.0, deadline - time.monotonic())))
        except (OSError, DocumentProcessLimitError, subprocess.TimeoutExpired):
            return False

    def _kill_group(self, pid: int) -> None:
        try:
            leader = _linux_stat(pid)
        except FileNotFoundError:
            leader = None
        if leader is not None and leader["identity"] != self._groups[pid]:
            raise DocumentProcessLimitError("worker_group_identity_changed")
        with contextlib.suppress(ProcessLookupError):
            os.killpg(pid, signal.SIGKILL)

    @staticmethod
    def _live_group(pid: int) -> bool:
        # killpg(..., 0) also reports zombies. /proc confirms no executable
        # member remains; reaping orphan zombies belongs to their system parent.
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                facts = _linux_stat(int(entry.name))
            except (FileNotFoundError, ProcessLookupError):
                continue
            if facts["group"] == pid and facts["state"] not in {"Z", "X"}:
                return True
        return False

    def close(self) -> None:
        if self._closed:
            return
        if os.getpid() != self.parent_pid:
            raise DocumentProcessLimitError("limits_context_closed_or_forked")
        errors: list[Exception] = []
        if self.platform == "win32":
            api = _windows_api()
            # Closing the last Job handle is itself the kernel kill action.
            # Retain a failed handle for retry; do not claim close succeeded.
            if self._job is not None:
                try:
                    _win_check(api.CloseHandle(self._job), "job_close_failed")
                    self._job = None
                except DocumentProcessLimitError as exc:
                    errors.append(exc)
            for pid, handle in list(self._processes.items()):
                try:
                    _win_check(api.CloseHandle(handle), "process_handle_close_failed")
                    del self._processes[pid]
                except DocumentProcessLimitError as exc:
                    errors.append(exc)
        else:
            for pid in list(self._groups):
                try:
                    self._kill_group(pid)
                    del self._groups[pid]
                except (OSError, DocumentProcessLimitError) as exc:
                    errors.append(exc)
        if not errors:
            self._closed = True
        else:
            raise errors[0]


def apply_worker_limits(
    config: dict[str, Any],
    memory_bytes: int,
    cpu_seconds: int,
) -> dict[str, Any]:
    """Run in the actual worker, before parser imports and document-byte reads.

    Configuration comes only from the service's private bootstrap protocol.
    A returned ready record attests configured OS limits, not successful parsing.
    """
    _budgets(memory_bytes, cpu_seconds)
    platform = _platform()
    if (
        not isinstance(config, dict)
        or type(config.get("version")) is not int
        or config.get("version") != 1
        or config.get("platform") != platform
        or config.get("memory_bytes") != memory_bytes
        or config.get("cpu_seconds") != cpu_seconds
        or type(config.get("parent_pid")) is not int
        or not 0 < config["parent_pid"] <= 0xFFFFFFFF
        or config["parent_pid"] == os.getpid()
        or not isinstance(config.get("parent_identity"), str)
        or not config["parent_identity"].isdigit()
    ):
        raise DocumentProcessLimitError("invalid_worker_limits_config")
    if platform == "win32":
        return _apply_windows(config, memory_bytes, cpu_seconds)
    return _apply_linux(config, memory_bytes, cpu_seconds)


def _apply_windows(config: dict[str, Any], memory: int, cpu: int) -> dict[str, Any]:
    name = config.get("job_name")
    if not isinstance(name, str) or not name.startswith("Local\\EchoDocument-") or len(name) != 51:
        raise DocumentProcessLimitError("invalid_worker_job_name")
    api = _windows_api()
    parent = api.OpenProcess(_PARENT_RIGHTS, False, config["parent_pid"])
    _win_check(parent, "worker_parent_unavailable")
    try:
        if (
            _win_identity(api, parent) != config["parent_identity"]
            or api.WaitForSingleObject(parent, 0) != _WAIT_TIMEOUT
        ):
            raise DocumentProcessLimitError("worker_parent_changed")
        job = api.OpenJobObjectW(_JOB_WORKER_RIGHTS, False, name)
        _win_check(job, "worker_job_unavailable")
        try:
            _win_limits(api, job, memory, cpu)
            process = api.GetCurrentProcess()
            if not _win_in_job(api, process, job):
                _win_check(api.AssignProcessToJobObject(job, process), "worker_job_attach_failed")
            if not _win_in_job(api, process, job):
                raise DocumentProcessLimitError("worker_job_membership_missing")
        finally:
            # No worker-side lifetime handle may survive into parsing. If the
            # parent died in this window, closing this last handle kills us too.
            _win_check(api.CloseHandle(job), "worker_job_close_failed")
        if api.WaitForSingleObject(parent, 0) != _WAIT_TIMEOUT:
            raise DocumentProcessLimitError("worker_parent_exited")
    finally:
        _win_check(api.CloseHandle(parent), "worker_parent_handle_close_failed")
    return {
        "ready": True,
        "platform": "win32",
        "worker_pid": os.getpid(),
        "worker_identity": _win_identity(api, api.GetCurrentProcess()),
        "parent_pid": config["parent_pid"],
        "job_name": name,
        "memory_bytes": memory,
        "memory_scope": "process_and_job_commit",
        "cpu_seconds": cpu,
        "cpu_scope": "process_and_job_user_time",
        "kill_on_job_close": True,
    }


def _apply_linux(config: dict[str, Any], memory: int, cpu: int) -> dict[str, Any]:
    import resource

    parent_pid = config["parent_pid"]

    def check_parent() -> None:
        if (
            os.getppid() != parent_pid
            or _linux_stat(parent_pid)["identity"] != config["parent_identity"]
        ):
            raise DocumentProcessLimitError("worker_parent_changed")

    check_parent()
    if os.getpgrp() != os.getpid() or os.getsid(0) != os.getpid():
        raise DocumentProcessLimitError("worker_requires_new_session")
    libc = ctypes.CDLL(None, use_errno=True)
    prctl = libc.prctl
    prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
    prctl.restype = ctypes.c_int
    if prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
        raise DocumentProcessLimitError("parent_death_setup_failed", os_error=ctypes.get_errno())
    death_signal = ctypes.c_int()
    if (
        prctl(2, ctypes.addressof(death_signal), 0, 0, 0) != 0
        or death_signal.value != signal.SIGKILL
    ):
        raise DocumentProcessLimitError("parent_death_verify_failed", os_error=ctypes.get_errno())
    check_parent()
    try:
        # Never relax a tighter inherited hard limit. A worker unable to apply
        # the exact service budget fails rather than silently reporting it.
        for kind, requested in ((resource.RLIMIT_AS, memory), (resource.RLIMIT_CPU, cpu)):
            _, hard = resource.getrlimit(kind)
            if hard != resource.RLIM_INFINITY and hard < requested:
                raise DocumentProcessLimitError("inherited_rlimit_too_small")
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except (OSError, ValueError) as exc:
        raise DocumentProcessLimitError("rlimit_setup_failed") from exc
    if (
        resource.getrlimit(resource.RLIMIT_AS) != (memory, memory)
        or resource.getrlimit(resource.RLIMIT_CPU) != (cpu, cpu)
        or resource.getrlimit(resource.RLIMIT_CORE) != (0, 0)
    ):
        raise DocumentProcessLimitError("rlimit_verify_failed")
    check_parent()
    return {
        "ready": True,
        "platform": "linux",
        "worker_pid": os.getpid(),
        "worker_identity": _linux_stat(os.getpid())["identity"],
        "parent_pid": parent_pid,
        "memory_bytes": memory,
        "memory_scope": "process_address_space",
        "cpu_seconds": cpu,
        "cpu_scope": "process_cpu_time",
        "parent_death_signal": int(signal.SIGKILL),
        "process_group": os.getpgrp(),
    }
