"""Real child-process budgets; fixtures contain no documents or user secrets."""

from __future__ import annotations

import ctypes
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from runtime.execution.misc import document_process_limits as limits

_MEMORY = 192 * 1024 * 1024
_ROOT = Path(__file__).resolve().parents[1]
_WINDOWS = sys.platform == "win32"
_SUPPORTED = sys.platform in {"win32", "linux"}
_WIN = pytest.mark.skipif(not _WINDOWS, reason="requires real Windows Job Objects")
_LINUX = pytest.mark.skipif(sys.platform != "linux", reason="requires real Linux rlimit/prctl")
_REAL = pytest.mark.skipif(not _SUPPORTED, reason="requires implemented OS")

_WORKER = r"""
import json, os, subprocess, sys, time
from pathlib import Path
from runtime.execution.misc.document_process_limits import apply_worker_limits
c = json.loads(sys.argv[1])
facts = apply_worker_limits(c, c['memory_bytes'], c['cpu_seconds'])
print(json.dumps(facts), flush=True)
mode = sys.argv[2]
sys.stdin.read(1)
if mode == 'memory':
    try:
        x = bytearray(c['memory_bytes'] * 2)
    except MemoryError:
        print('memory_denied', flush=True)
    else:
        print('memory_not_limited', flush=True)
elif mode == 'cpu':
    while True:
        pass
elif mode in ('tree', 'tree_exit'):
    child = subprocess.Popen(
        [sys.executable, '-c', 'import time; time.sleep(30)'],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )
    print(json.dumps({'grandchild_pid': child.pid}), flush=True)
    if mode == 'tree_exit':
        os._exit(0)
    sys.stdin.read(1)
else:
    sys.stdin.read(1)
"""


def _spawn(code: str, *args: str) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-u", "-c", code, *args],
        cwd=_ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=not _WINDOWS,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _line(proc: subprocess.Popen[str], timeout: float = 10) -> str:
    result: queue.Queue[str] = queue.Queue(maxsize=1)
    thread = threading.Thread(target=lambda: result.put(proc.stdout.readline(8193)), daemon=True)
    thread.start()
    value = result.get(timeout=timeout)
    assert value, f"worker exited before bounded handshake: {proc.poll()}"
    assert len(value) <= 8192
    return value.strip()


def _go(proc: subprocess.Popen[str]) -> None:
    proc.stdin.write("x")
    proc.stdin.flush()


def _alive(pid: int) -> bool:
    if _WINDOWS:
        api = limits._windows_api()
        handle = api.OpenProcess(limits._PARENT_RIGHTS, False, pid)
        if not handle:
            assert ctypes.get_last_error() == 87  # ERROR_INVALID_PARAMETER: no such process.
            return False
        try:
            return api.WaitForSingleObject(handle, 0) == limits._WAIT_TIMEOUT
        finally:
            api.CloseHandle(handle)
    try:
        return limits._linux_stat(pid)["state"] not in {"Z", "X"}
    except FileNotFoundError:
        return False


def _eventually_dead(pid: int, timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not _alive(pid), f"synthetic process {pid} survived cleanup"


def _assert_worker_membership(guard, pid):
    api = limits._windows_api()
    handle = api.OpenProcess(limits._PARENT_RIGHTS, False, pid)
    assert handle
    try:
        assert limits._win_in_job(api, handle, guard._job)
    finally:
        api.CloseHandle(handle)


@pytest.fixture
def launch():
    created: list[tuple[limits.ParentProcessLimits, subprocess.Popen[str]]] = []

    def start(mode: str = "idle", *, cpu: int = 10):
        guard = limits.ParentProcessLimits(_MEMORY, cpu)
        proc = _spawn(_WORKER, json.dumps(guard.worker_config()), mode)
        created.append((guard, proc))
        guard.attach(proc)
        ready = json.loads(_line(proc))
        assert ready["ready"] is True
        guard.bind_worker(ready["worker_pid"], identity=ready["worker_identity"])
        return guard, proc, ready

    yield start
    for guard, proc in reversed(created):
        try:
            if not guard._closed:
                guard.terminate(proc, timeout_s=3)
        finally:
            guard.close()
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=3)
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                stream.close()


@pytest.mark.parametrize(
    "memory,cpu",
    [
        (0, 1),
        (-1, 1),
        (True, 1),
        (1.0, 1),
        (_MEMORY, 0),
        (_MEMORY, False),
        (_MEMORY, 1.5),
        (_MEMORY, float("inf")),
        (1 << 100, 1),
    ],
)
def test_invalid_limits_are_rejected_before_creating_any_process(memory, cpu):
    with pytest.raises(limits.DocumentProcessLimitError, match="invalid_resource_budget"):
        limits.ParentProcessLimits(memory, cpu)


def test_unknown_platform_fails_closed(monkeypatch):
    monkeypatch.setattr(limits.sys, "platform", "unimplemented-os")
    with pytest.raises(limits.DocumentProcessLimitError, match="unsupported_platform"):
        limits.ParentProcessLimits(_MEMORY, 1)
    with pytest.raises(limits.DocumentProcessLimitError, match="unsupported_platform"):
        limits.apply_worker_limits({}, _MEMORY, 1)


@_REAL
def test_worker_confirms_actual_applied_limits_and_refuses_self_parent(launch):
    guard, proc, ready = launch()
    if _WINDOWS:
        # The Windows venv redirector can differ even without PyInstaller.
        _assert_worker_membership(guard, ready["worker_pid"])
        assert limits._win_in_job(limits._windows_api(), proc._handle, guard._job)
    else:
        assert ready["worker_pid"] == proc.pid
    assert ready["memory_bytes"] == _MEMORY
    assert ready["cpu_seconds"] == 10
    assert ready["parent_pid"] == os.getpid()
    assert guard.worker_config()["parent_identity"].isdigit()
    assert ready["worker_identity"].isdigit()
    assert guard.terminate(proc, timeout_s=3)
    assert proc.poll() is not None
    with pytest.raises(limits.DocumentProcessLimitError, match="invalid_worker_limits_config"):
        limits.apply_worker_limits(guard.worker_config(), _MEMORY, 10)


@_REAL
def test_real_memory_allocation_is_denied(launch):
    guard, proc, _ = launch("memory")
    _go(proc)
    assert _line(proc) == "memory_denied"
    assert proc.wait(timeout=5) == 0
    assert guard.terminate(proc, timeout_s=3)


@_REAL
def test_real_cpu_budget_terminates_busy_worker(launch):
    guard, proc, ready = launch("cpu", cpu=1)
    started = time.monotonic()
    _go(proc)
    assert proc.wait(timeout=8) != 0
    assert time.monotonic() - started < 8
    assert ready["cpu_seconds"] == 1
    assert guard.terminate(proc, timeout_s=3)


@_REAL
@pytest.mark.parametrize("mode", ["tree", "tree_exit"])
def test_cleanup_kills_grandchild_even_if_direct_child_already_exited(launch, mode):
    guard, proc, _ = launch(mode)
    _go(proc)
    child_pid = json.loads(_line(proc))["grandchild_pid"]
    assert _alive(child_pid)
    if mode == "tree_exit":
        assert proc.wait(timeout=5) == 0
    assert guard.terminate(proc, timeout_s=3)
    _eventually_dead(child_pid)


@_WIN
def test_real_job_reports_memory_cpu_and_kill_flags(launch):
    guard, proc, ready = launch()
    actual = limits._win_limits(limits._windows_api(), guard._job, _MEMORY, 10)
    assert actual.basic.flags == limits._JOB_FLAGS
    assert actual.basic.flags & (0x800 | 0x1000) == 0
    assert ready["kill_on_job_close"] is True
    assert ready["memory_scope"] == "process_and_job_commit"
    assert ready["cpu_scope"] == "process_and_job_user_time"
    assert limits._win_in_job(limits._windows_api(), proc._handle, guard._job)


@_WIN
def test_close_alone_really_kills_all_job_members(launch):
    guard, proc, _ = launch("tree")
    _go(proc)
    child_pid = json.loads(_line(proc))["grandchild_pid"]
    guard.close()
    proc.wait(timeout=5)
    _eventually_dead(child_pid)
    guard.close()
    with pytest.raises(limits.DocumentProcessLimitError, match="context_closed"):
        guard.worker_config()


@_WIN
def test_worker_self_joins_named_job_without_parent_attaching_actual_python():
    # Reproduces the bootloader distinction without claiming this is a frozen binary.
    guard = limits.ParentProcessLimits(_MEMORY, 10)
    proc = _spawn(_WORKER, json.dumps(guard.worker_config()), "idle")
    try:
        ready = json.loads(_line(proc))  # deliberately before guard.attach
        _assert_worker_membership(guard, ready["worker_pid"])
        guard.attach(proc)  # already-a-member path is idempotent
        guard.attach(proc)
        guard.bind_worker(ready["worker_pid"], identity=ready["worker_identity"])
        assert limits._win_in_job(limits._windows_api(), proc._handle, guard._job)
        assert guard.terminate(proc, timeout_s=3)
    finally:
        guard.close()
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=3)


@_WIN
def test_job_limit_setup_failure_closes_actual_created_handle(monkeypatch):
    api = limits._windows_api()
    closed = []
    original_close = api.CloseHandle

    def fail(*_):
        ctypes.set_last_error(5)
        return 0

    def close(handle):
        closed.append(handle)
        return original_close(handle)

    monkeypatch.setattr(api, "SetInformationJobObject", fail)
    monkeypatch.setattr(api, "CloseHandle", close)
    with pytest.raises(limits.DocumentProcessLimitError, match="job_limit_setup_failed") as caught:
        limits.ParentProcessLimits(_MEMORY, 10)
    assert caught.value.os_error == 5
    assert len(closed) == 1
    assert api.WaitForSingleObject(closed[0], 0) == 0xFFFFFFFF  # handle actually closed


@_WIN
def test_job_assignment_refusal_never_reports_protected_success(monkeypatch):
    api = limits._windows_api()
    proc = _spawn("import time; time.sleep(30)")
    guard = limits.ParentProcessLimits(_MEMORY, 10)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(api, "AssignProcessToJobObject", lambda *_: 0)
            with pytest.raises(limits.DocumentProcessLimitError, match="job_attach_failed"):
                guard.attach(proc)
            assert guard.terminate(proc, timeout_s=3) is False
            assert proc.poll() is not None
    finally:
        guard.close()
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=3)


@_WIN
def test_job_termination_failure_does_not_claim_tree_cleanup(monkeypatch, launch):
    guard, proc, _ = launch()
    with monkeypatch.context() as patch:
        patch.setattr(limits._windows_api(), "TerminateJobObject", lambda *_: 0)
        assert guard.terminate(proc, timeout_s=0.1) is False
        assert proc.poll() is None
    assert guard.terminate(proc, timeout_s=3)


@_WIN
def test_bind_rejects_reused_identity_and_never_adopts_another_jobs_worker(launch):
    guard, proc, facts = launch()
    with pytest.raises(limits.DocumentProcessLimitError, match="identity_or_job_mismatch"):
        guard.bind_worker(facts["worker_pid"], identity=str(int(facts["worker_identity"]) + 1))
    with limits.ParentProcessLimits(_MEMORY, 10) as other:
        with pytest.raises(limits.DocumentProcessLimitError, match="identity_or_job_mismatch"):
            other.bind_worker(facts["worker_pid"], identity=facts["worker_identity"])
        assert not limits._win_in_job(limits._windows_api(), proc._handle, other._job)
    assert guard.terminate(proc, timeout_s=3)


@_WIN
def test_failed_member_capture_still_kills_job_without_claiming_confirmed_cleanup(
    monkeypatch, launch
):
    guard, proc, _ = launch()

    def fail(*_):
        raise limits.DocumentProcessLimitError("synthetic_snapshot_failure")

    with monkeypatch.context() as patch:
        patch.setattr(limits, "_win_member_pids", fail)
        assert guard.terminate(proc, timeout_s=3) is False
    proc.wait(timeout=3)
    assert guard.terminate(proc, timeout_s=3)


@_WIN
def test_terminate_waits_for_real_worker_and_grandchild_handles_not_just_job_counts(launch):
    guard, proc, facts = launch("tree")
    _go(proc)
    child_pid = json.loads(_line(proc))["grandchild_pid"]
    api = limits._windows_api()
    handles = [
        api.OpenProcess(limits._PARENT_RIGHTS, False, pid)
        for pid in (facts["worker_pid"], child_pid)
    ]
    try:
        assert all(handles)
        assert all(api.WaitForSingleObject(handle, 0) == limits._WAIT_TIMEOUT for handle in handles)
        assert guard.terminate(proc, timeout_s=3)
        assert all(api.WaitForSingleObject(handle, 0) == 0 for handle in handles)
    finally:
        for handle in handles:
            if handle:
                api.CloseHandle(handle)


@_WIN
def test_failed_close_retains_lifecycle_handle_for_retry(monkeypatch, launch):
    guard, proc, _ = launch()
    job = guard._job
    api = limits._windows_api()
    close = api.CloseHandle
    with monkeypatch.context() as patch:
        patch.setattr(api, "CloseHandle", lambda handle: 0 if handle == job else close(handle))
        with pytest.raises(limits.DocumentProcessLimitError, match="job_close_failed"):
            guard.close()
        assert not guard._closed
        assert guard._job == job
        assert proc.poll() is None
    guard.close()
    assert guard._closed
    proc.wait(timeout=5)


@_REAL
def test_worker_parent_identity_mismatch_prevents_ready():
    with limits.ParentProcessLimits(_MEMORY, 10) as guard:
        config = guard.worker_config()
        config["parent_identity"] = "0"
        proc = _spawn(_WORKER, json.dumps(config), "idle")
        try:
            guard.attach(proc)
            assert proc.wait(timeout=5) != 0
            assert proc.stdout.read(8193) == ""
            assert guard.terminate(proc, timeout_s=3)
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=3)


@_WIN
def test_worker_independently_verifies_job_budget_before_ready():
    with limits.ParentProcessLimits(_MEMORY, 10) as guard:
        config = guard.worker_config()
        config["memory_bytes"] += 4096
        proc = _spawn(_WORKER, json.dumps(config), "idle")
        try:
            guard.attach(proc)
            assert proc.wait(timeout=5) != 0
            assert proc.stdout.read(8193) == ""
            assert "job_limits_mismatch" in proc.stderr.read(8193)
            assert guard.terminate(proc, timeout_s=3)
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=3)


@_REAL
def test_parent_abrupt_exit_stops_the_real_worker(tmp_path):
    # Parent exits with os._exit: no context manager, destructor, or finally runs.
    code = r"""
import json, os, subprocess, sys
from runtime.execution.misc.document_process_limits import ParentProcessLimits
guard = ParentProcessLimits(192 * 1024 * 1024, 10)
worker = subprocess.Popen(
    [sys.executable, '-u', '-c', sys.argv[1], json.dumps(guard.worker_config()), 'idle'],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    text=True, start_new_session=sys.platform != 'win32',
    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
)
guard.attach(worker)
print(worker.stdout.readline(8193), end='', flush=True)
sys.stdin.read(1)
os._exit(0)
"""
    parent = _spawn(code, _WORKER)
    worker_pid = None
    try:
        worker_pid = json.loads(_line(parent))["worker_pid"]
        assert _alive(worker_pid)
        _go(parent)
        assert parent.wait(timeout=5) == 0
        _eventually_dead(worker_pid)
    finally:
        if parent.poll() is None:
            parent.kill()
        parent.wait(timeout=3)
        if worker_pid:
            _eventually_dead(worker_pid)


@_WIN
def test_parent_abrupt_exit_kills_grandchildren_too():
    code = r"""
import json, os, subprocess, sys
from runtime.execution.misc.document_process_limits import ParentProcessLimits
guard = ParentProcessLimits(192 * 1024 * 1024, 10)
worker = subprocess.Popen(
    [sys.executable, '-u', '-c', sys.argv[1], json.dumps(guard.worker_config()), 'tree'],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
    creationflags=subprocess.CREATE_NO_WINDOW,
)
guard.attach(worker)
print(worker.stdout.readline(8193), end='', flush=True)
worker.stdin.write('x'); worker.stdin.flush()
print(worker.stdout.readline(8193), end='', flush=True)
sys.stdin.read(1)
os._exit(0)
"""
    parent = _spawn(code, _WORKER)
    pids = []
    try:
        pids.append(json.loads(_line(parent))["worker_pid"])
        pids.append(json.loads(_line(parent))["grandchild_pid"])
        assert all(_alive(pid) for pid in pids)
        _go(parent)
        assert parent.wait(timeout=5) == 0
        for pid in pids:
            _eventually_dead(pid)
    finally:
        if parent.poll() is None:
            parent.kill()
        parent.wait(timeout=3)
        for pid in pids:
            _eventually_dead(pid)


@_LINUX
def test_linux_real_readback_of_rlimits_and_parent_death(launch):
    guard, proc, ready = launch()
    import resource

    assert resource.prlimit(proc.pid, resource.RLIMIT_AS) == (_MEMORY, _MEMORY)
    assert resource.prlimit(proc.pid, resource.RLIMIT_CPU) == (10, 10)
    assert resource.prlimit(proc.pid, resource.RLIMIT_CORE) == (0, 0)
    assert ready["parent_death_signal"] == 9
    assert ready["process_group"] == proc.pid
    assert guard.terminate(proc, timeout_s=3)


@_LINUX
def test_linux_refuses_worker_not_started_in_new_session():
    with limits.ParentProcessLimits(_MEMORY, 10) as guard:
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            with pytest.raises(limits.DocumentProcessLimitError, match="requires_new_session"):
                guard.attach(proc)
            assert guard.terminate(proc, timeout_s=3) is False
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=3)
