"""Linux-only checks against real child processes, never emulated on Windows.

The parent-death fixture deliberately does not read a pipe after ready: closing
the owner's stdin/stdout cannot make it exit normally and mask missing PDEATHSIG.
These source-Python checks do not attest PyInstaller bootloader behavior.
"""

from __future__ import annotations

import contextlib
import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from runtime.execution.misc import document_process_limits as limits

pytestmark = pytest.mark.skipif(
    sys.platform != "linux", reason="requires real Linux /proc, rlimit and parent-death signal"
)
_ROOT = Path(__file__).resolve().parents[1]
_MEMORY = 192 * 1024 * 1024

_SLEEPER = r"""
import json, os, resource, sys, time
from runtime.execution.misc.document_process_limits import (
    DocumentProcessLimitError, apply_worker_limits,
)
config = json.loads(sys.argv[1])
mode = sys.argv[2]
if mode == 'tight_memory':
    resource.setrlimit(resource.RLIMIT_AS, (96 * 1024 * 1024, 96 * 1024 * 1024))
elif mode == 'tight_cpu':
    resource.setrlimit(resource.RLIMIT_CPU, (1, 1))
try:
    ready = apply_worker_limits(config, config['memory_bytes'], config['cpu_seconds'])
except DocumentProcessLimitError as error:
    print(json.dumps({'error': error.code}), flush=True)
    sys.exit(23)
print(json.dumps(ready), flush=True)
# Crucially, do not read stdin or write stdout again. No EOF or broken pipe
# causes this process to finish when its owner exits.
time.sleep(30)
"""

_OWNER = r"""
import json, os, subprocess, sys
from runtime.execution.misc.document_process_limits import ParentProcessLimits
guard = ParentProcessLimits(192 * 1024 * 1024, 10)
worker = subprocess.Popen(
    [sys.executable, '-u', '-c', sys.argv[1], json.dumps(guard.worker_config()), 'idle'],
    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    text=True, start_new_session=True,
)
guard.attach(worker)
ready = json.loads(worker.stdout.readline(8193))
guard.bind_worker(ready['worker_pid'], identity=ready['worker_identity'])
print(json.dumps(ready), flush=True)
sys.stdin.read(1)
os._exit(0)
"""


def _spawn(code: str, *args: str) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-u", "-c", code, *args],
        cwd=_ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )


def _line(proc: subprocess.Popen[str]) -> dict:
    lines: queue.Queue[str] = queue.Queue(maxsize=1)
    thread = threading.Thread(target=lambda: lines.put(proc.stdout.readline(8193)), daemon=True)
    thread.start()
    raw = lines.get(timeout=10)
    assert raw and len(raw) <= 8192, f"worker did not complete bounded ready: {proc.poll()}"
    return json.loads(raw)


def _alive(pid: int, identity: str) -> bool:
    try:
        facts = limits._linux_stat(pid)
    except (FileNotFoundError, ProcessLookupError):
        return False
    return facts["identity"] == identity and facts["state"] not in {"Z", "X"}


def _assert_dead(pid: int, identity: str) -> None:
    deadline = time.monotonic() + 5
    while _alive(pid, identity) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not _alive(pid, identity), "synthetic sleeping worker survived parent death"


def _reap(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=5)
    for stream in (proc.stdin, proc.stdout, proc.stderr):
        stream.close()


def test_parent_death_kills_worker_that_cannot_exit_from_pipe_eof():
    owner = _spawn(_OWNER, _SLEEPER)
    ready = None
    try:
        ready = _line(owner)
        assert ready["ready"] is True
        assert ready["parent_pid"] == owner.pid
        assert ready["parent_death_signal"] == signal.SIGKILL
        assert ready["worker_pid"] != owner.pid
        assert _alive(ready["worker_pid"], ready["worker_identity"])
        owner.stdin.write("x")
        owner.stdin.flush()
        assert owner.wait(timeout=5) == 0
        _assert_dead(ready["worker_pid"], ready["worker_identity"])
    finally:
        _reap(owner)
        # A failed protection assertion must not leave the synthetic sleeper.
        if ready and _alive(ready["worker_pid"], ready["worker_identity"]):
            with contextlib.suppress(ProcessLookupError):
                os.kill(ready["worker_pid"], signal.SIGKILL)
            _assert_dead(ready["worker_pid"], ready["worker_identity"])


def test_bind_rejects_wrong_identity_and_worker_in_another_owned_session():
    with (
        limits.ParentProcessLimits(_MEMORY, 10) as first,
        limits.ParentProcessLimits(_MEMORY, 10) as second,
    ):
        processes = []
        try:
            workers = []
            for guard in (first, second):
                proc = _spawn(_SLEEPER, json.dumps(guard.worker_config()), "idle")
                processes.append((guard, proc))
                guard.attach(proc)
                ready = _line(proc)
                guard.bind_worker(ready["worker_pid"], identity=ready["worker_identity"])
                workers.append(ready)
            own, foreign = workers
            with pytest.raises(limits.DocumentProcessLimitError, match="identity_or_group"):
                first.bind_worker(
                    own["worker_pid"], identity=str(int(own["worker_identity"]) + 1)
                )
            with pytest.raises(limits.DocumentProcessLimitError, match="identity_or_group"):
                first.bind_worker(foreign["worker_pid"], identity=foreign["worker_identity"])
            assert _alive(foreign["worker_pid"], foreign["worker_identity"])
            assert first.terminate(processes[0][1], timeout_s=3)
            assert _alive(foreign["worker_pid"], foreign["worker_identity"]), (
                "rejected foreign worker must not become part of the first cleanup scope"
            )
            assert second.terminate(processes[1][1], timeout_s=3)
        finally:
            for guard, proc in reversed(processes):
                try:
                    guard.terminate(proc, timeout_s=3)
                finally:
                    _reap(proc)


@pytest.mark.parametrize("mode", ["tight_memory", "tight_cpu"])
def test_tighter_existing_hard_limit_is_not_relaxed_or_reported_as_ready(mode):
    with limits.ParentProcessLimits(_MEMORY, 10) as guard:
        proc = _spawn(_SLEEPER, json.dumps(guard.worker_config()), mode)
        try:
            guard.attach(proc)
            assert _line(proc) == {"error": "inherited_rlimit_too_small"}
            assert proc.wait(timeout=5) == 23
            assert proc.stdout.read(8193) == "", "rejected budgets must never report ready"
            assert guard.terminate(proc, timeout_s=3)
        finally:
            _reap(proc)
