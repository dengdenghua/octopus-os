"""Synchronous subprocess group lifecycle helpers."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import time
from typing import Any


def process_group_kwargs() -> dict[str, Any]:
    """Return kwargs that launch a child in its own process group/session."""
    if sys.platform == "win32":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


def terminate_process_tree(
    proc: subprocess.Popen[Any],
    *,
    grace_s: float = 1.0,
    kill_wait_s: float = 2.0,
) -> bool:
    """Best-effort terminate of ``proc`` and descendants.

    Returns True when the process has exited by the end of the attempt.
    """
    if proc.poll() is not None:
        return True
    if sys.platform == "win32":
        _terminate_windows_tree(proc)
    else:
        _signal_posix_group(proc, signal.SIGTERM)
    if _wait_exited(proc, grace_s):
        return True
    if sys.platform == "win32":
        with contextlib.suppress(OSError):
            proc.kill()
    else:
        _signal_posix_group(proc, signal.SIGKILL)
    return _wait_exited(proc, kill_wait_s)


def terminate_pid_tree(
    pid: int,
    *,
    grace_s: float = 1.0,
    kill_wait_s: float = 2.0,
) -> bool:
    """Best-effort terminate of a process tree when only the pid is known."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        _taskkill_windows_pid(pid)
        return True
    _signal_posix_pid_group(pid, signal.SIGTERM)
    if _pid_exited(pid, grace_s):
        return True
    _signal_posix_pid_group(pid, signal.SIGKILL)
    return _pid_exited(pid, kill_wait_s)


def run_capture(
    argv: list[str],
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command with captured output and descendant-safe timeout cleanup.

    This is the bounded-output counterpart for callers that need the familiar
    ``CompletedProcess`` contract. Unlike ``subprocess.run(timeout=...)``, a
    timeout terminates the process group/session before reaping the direct
    child, so shells and CLI descendants do not survive the request.
    """
    proc = subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        **process_group_kwargs(),
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        terminate_process_tree(proc, grace_s=0.2, kill_wait_s=0.5)
        try:
            stdout, stderr = proc.communicate(timeout=1.0)
        except subprocess.TimeoutExpired as final_exc:
            with contextlib.suppress(OSError):
                proc.kill()
            stdout, stderr = proc.communicate()
            if not stdout:
                stdout = final_exc.stdout or exc.stdout or ""
            if not stderr:
                stderr = final_exc.stderr or exc.stderr or ""
        raise subprocess.TimeoutExpired(
            cmd=argv,
            timeout=exc.timeout,
            output=stdout or exc.stdout,
            stderr=stderr or exc.stderr,
        ) from exc
    return subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)


def _wait_exited(proc: subprocess.Popen[Any], timeout_s: float) -> bool:
    try:
        proc.wait(timeout=timeout_s)
        return True
    except subprocess.TimeoutExpired:
        return False


def _signal_posix_group(proc: subprocess.Popen[Any], sig: int) -> None:
    try:
        pgid = os.getpgid(proc.pid)
        if pgid == proc.pid and pgid != os.getpgrp():
            os.killpg(pgid, sig)
            return
    except OSError:  # best-effort · falls through to the direct proc.terminate/kill below
        pass
    with contextlib.suppress(OSError):
        if sig == signal.SIGTERM:
            proc.terminate()
        else:
            proc.kill()


def _signal_posix_pid_group(pid: int, sig: int) -> None:
    try:
        pgid = os.getpgid(pid)
        if pgid == pid and pgid != os.getpgrp():
            os.killpg(pgid, sig)
            return
    except OSError:  # best-effort · falls through to the direct os.kill below
        pass
    with contextlib.suppress(OSError):
        os.kill(pid, sig)


def _pid_exited(pid: int, timeout_s: float) -> bool:
    deadline = time.monotonic() + max(0.0, timeout_s)
    while time.monotonic() <= deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return True
        time.sleep(0.05)
    return False


def _terminate_windows_tree(proc: subprocess.Popen[Any]) -> None:
    _taskkill_windows_pid(proc.pid)


def _taskkill_windows_pid(pid: int) -> None:
    taskkill = os.path.join(
        os.environ.get("SYSTEMROOT", r"C:\Windows"),
        "System32",
        "taskkill.exe",
    )
    with contextlib.suppress(Exception):
        subprocess.run(
            [taskkill, "/pid", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
