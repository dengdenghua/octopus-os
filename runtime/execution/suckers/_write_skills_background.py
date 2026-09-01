"""Background-process machinery for write_skills · extracted from write_skills.py.

Holds ``_BackgroundProcess``, the in-memory registry, the on-disk metadata
helpers, the process liveness probe, and the recovered-metadata snapshotter.

Note: ``_snapshot_background_metadata`` resolves ``_probe_process`` lazily via
``runtime.execution.suckers.write_skills`` so that tests monkeypatching
``write_skills._probe_process`` still observe the patched function at call time.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from ._write_skills_common import _BACKGROUND_OUTPUT_CAP, _optional_float


def _background_policy_with_result(
    policy: dict[str, Any],
    *,
    status: str,
    exit_code: int | None,
    started_at: float | None,
    stdout_truncated: bool = False,
    stderr_truncated: bool = False,
) -> dict[str, Any]:
    from runtime.platform.process.streaming import execution_policy_result_snapshot

    enriched = dict(policy) if isinstance(policy, dict) else {}
    duration_ms: int | None = None
    if started_at is not None:
        duration_ms = int(max(0.0, time.time() - float(started_at)) * 1000)
    enriched["result"] = execution_policy_result_snapshot(
        status=status,
        exit_code=exit_code,
        timed_out=False,
        cancelled=status == "cancelled",
        killed=status == "cancelled",
        stdout_truncated=stdout_truncated,
        stderr_truncated=stderr_truncated,
        duration_ms=duration_ms,
    )
    return enriched


def _background_execution_policy(
    *,
    sandbox_requested: bool,
    sandbox_workspace: str | None,
    cwd: str | None,
    sandbox_backend: str,
    sandbox_hard: bool,
    env_mode: str,
) -> dict[str, Any]:
    from runtime.platform.process.streaming import execution_policy_snapshot

    return execution_policy_snapshot(
        sandbox_requested=sandbox_requested,
        workspace=sandbox_workspace,
        cwd=cwd,
        backend=sandbox_backend,
        hard=sandbox_hard,
        allow_network=False,
        env_mode=env_mode,
        process_group=True,
        timeout_s=None,
    )


class _BackgroundProcess:
    def __init__(
        self,
        *,
        task_id: str,
        argv: list[str],
        proc: subprocess.Popen[str],
        cwd: str | None,
        sandbox_backend: str = "direct",
        sandbox_hard: bool = False,
        execution_policy: dict[str, Any] | None = None,
        stdout_path: Path,
        stderr_path: Path,
        metadata_path: Path,
    ) -> None:
        self.task_id = task_id
        self.argv = argv
        self.proc = proc
        self.cwd = cwd
        self.sandbox_backend = sandbox_backend
        self.sandbox_hard = sandbox_hard
        self.execution_policy = execution_policy or {}
        self.started_at = time.time()
        self.cancelled = False
        self._wait_failed = False
        self.ended_at: float | None = None
        self._lock = threading.Lock()
        self.stdout_path = stdout_path
        self.stderr_path = stderr_path
        self.metadata_path = metadata_path
        self._output_threads: list[threading.Thread] = []
        for stream_name, stream, path in (
            ("stdout", self.proc.stdout, self.stdout_path),
            ("stderr", self.proc.stderr, self.stderr_path),
        ):
            if stream is None:
                continue
            path.touch(exist_ok=True)
            thread = threading.Thread(
                target=self._drain_output,
                args=(stream, path),
                daemon=True,
                name=f"background-exec-{task_id}-{stream_name}",
            )
            self._output_threads.append(thread)
            thread.start()
        self._persist(exit_code=None)
        self._wait_thread = threading.Thread(
            target=self._wait_and_persist,
            name=f"background-exec-{task_id}-wait",
            daemon=True,
        )
        self._wait_thread.start()

    def _metadata(self, *, exit_code: int | None) -> dict[str, Any]:
        with self._lock:
            return self._metadata_unlocked(exit_code=exit_code)

    def _metadata_unlocked(self, *, exit_code: int | None) -> dict[str, Any]:
        """Build the metadata dict. The caller must hold ``self._lock``."""
        cancelled = self.cancelled
        wait_failed = self._wait_failed
        if cancelled:
            status = "cancelled"
        elif exit_code is None and wait_failed:
            status = "unknown"
        elif exit_code is None:
            status = "running"
        elif exit_code == 0:
            status = "completed"
        else:
            status = "failed"
        return {
            "task_id": self.task_id,
            "argv": self.argv,
            "cwd": self.cwd,
            "sandbox_backend": self.sandbox_backend,
            "sandbox_hard": self.sandbox_hard,
            "execution_policy": _background_policy_with_result(
                self.execution_policy,
                status=status,
                exit_code=exit_code,
                started_at=self.started_at,
                stdout_truncated=_background_file_truncated(self.stdout_path),
                stderr_truncated=_background_file_truncated(self.stderr_path),
            ),
            "pid": self.proc.pid,
            "process_group_id": _process_group_id(self.proc.pid),
            "started_at": self.started_at,
            "cancelled": self.cancelled,
            "exit_code": exit_code,
            "stdout_path": str(self.stdout_path),
            "stderr_path": str(self.stderr_path),
        }

    def _persist(self, *, exit_code: int | None) -> None:
        with self._lock:
            _write_background_metadata(
                self.metadata_path,
                self._metadata_unlocked(exit_code=exit_code),
            )

    def _wait_and_persist(self) -> None:
        try:
            exit_code = self.proc.wait()
        except Exception:  # noqa: BLE001
            with self._lock:
                self._wait_failed = True
                self.ended_at = time.time()
            self._persist(exit_code=None)
            return
        for thread in self._output_threads:
            thread.join(timeout=2.0)
        with self._lock:
            self.ended_at = time.time()
        self._persist(exit_code=exit_code)

    @staticmethod
    def _drain_output(stream: Any, path: Path) -> None:
        """Drain a child pipe while retaining only a bounded file tail."""
        try:
            with path.open("r+b") as handle:
                for line in iter(stream.readline, ""):
                    if not line:
                        break
                    handle.seek(0, os.SEEK_END)
                    handle.write(line.encode("utf-8", errors="replace"))
                    handle.flush()
                    end = handle.tell()
                    if end > _BACKGROUND_OUTPUT_CAP:
                        handle.seek(max(0, end - _BACKGROUND_OUTPUT_CAP))
                        tail = handle.read(_BACKGROUND_OUTPUT_CAP)
                        handle.seek(0)
                        handle.write(tail)
                        handle.truncate()
                        handle.seek(0, os.SEEK_END)
        except (OSError, ValueError):
            return
        finally:
            with contextlib.suppress(Exception):
                stream.close()

    def snapshot(self) -> dict[str, Any]:
        exit_code = self.proc.poll()
        with self._lock:
            cancelled = self.cancelled
        if cancelled:
            status = "cancelled"
        elif exit_code is None:
            status = "running"
        elif exit_code == 0:
            status = "completed"
        else:
            status = "failed"
        if exit_code is not None:
            for thread in self._output_threads:
                thread.join(timeout=2.0)
            self._persist(exit_code=exit_code)

        raw_stdout = _read_background_text(self.stdout_path)
        raw_stderr = _read_background_text(self.stderr_path)
        execution_policy = _background_policy_with_result(
            self.execution_policy,
            status=status,
            exit_code=exit_code,
            started_at=self.started_at,
            stdout_truncated=_background_file_truncated(self.stdout_path),
            stderr_truncated=_background_file_truncated(self.stderr_path),
        )
        return {
            "task_id": self.task_id,
            "status": status,
            "argv": self.argv,
            "cwd": self.cwd,
            "sandbox_backend": self.sandbox_backend,
            "sandbox_hard": self.sandbox_hard,
            "execution_policy": execution_policy,
            "exit_code": exit_code,
            "running": status == "running",
            "stdout": raw_stdout[:_BACKGROUND_OUTPUT_CAP],
            "stderr": raw_stderr[:_BACKGROUND_OUTPUT_CAP],
            "stdout_truncated": _background_file_truncated(self.stdout_path),
            "stderr_truncated": _background_file_truncated(self.stderr_path),
            "started_at": self.started_at,
        }

    def kill(self) -> dict[str, Any]:
        from runtime.platform.process.tree import terminate_process_tree

        with self._lock:
            self.cancelled = True
        if self.proc.poll() is None:
            terminate_process_tree(self.proc)
        deadline = time.monotonic() + 3.0
        while self._wait_thread.is_alive() and time.monotonic() < deadline:
            self._wait_thread.join(timeout=0.1)
        return self.snapshot()


_BACKGROUND_PROCESSES: dict[str, _BackgroundProcess] = {}

# Finished tasks stay queryable for this long; afterwards they are pruned from
# the in-memory registry to bound growth in long agent sessions.
_BACKGROUND_FINISHED_TTL_S = 1800.0
_BACKGROUND_REGISTRY_MAX = 128
# Cap on simultaneously RUNNING background tasks per runtime process, so a
# runaway agent cannot fork-bomb the host via background_exec.
_BACKGROUND_MAX_CONCURRENT = 16


def _prune_finished_background_processes(
    *,
    ttl_s: float = _BACKGROUND_FINISHED_TTL_S,
    max_keep: int = _BACKGROUND_REGISTRY_MAX,
) -> int:
    """Drop finished registry entries older than ``ttl_s`` (or beyond ``max_keep``).

    Pruned tasks remain readable on disk via their persisted metadata, so this
    only bounds memory, never data. Returns the number of pruned entries.
    """
    now = time.time()
    prunable: list[str] = []
    finished: list[tuple[float, str]] = []
    for task_id, bg in list(_BACKGROUND_PROCESSES.items()):
        with bg._lock:
            ended_at = bg.ended_at
        if ended_at is None:
            continue
        if now - ended_at >= ttl_s:
            prunable.append(task_id)
        else:
            finished.append((ended_at, task_id))
    for task_id in prunable:
        _BACKGROUND_PROCESSES.pop(task_id, None)
    # The cap bounds the entries it can actually evict (finished ones), not the
    # whole registry. Sizing overflow by the whole registry meant a session with
    # many RUNNING tasks computed a large overflow and evicted still-fresh
    # finished entries well inside their TTL — while the registry stayed over
    # max_keep anyway, since running entries are never evictable. Keep the
    # newest finished entries and drop only the oldest surplus.
    overflow = len(finished) - max_keep
    if overflow > 0:
        for _, task_id in sorted(finished)[:overflow]:
            _BACKGROUND_PROCESSES.pop(task_id, None)
    return len(prunable)


def _background_root() -> Path:
    from runtime.platform.process.paths import app_paths

    root = app_paths().data_dir / "background_exec"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _background_paths(task_id: str) -> dict[str, Path]:
    task_dir = _background_root() / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    return {
        "dir": task_dir,
        "metadata": task_dir / "metadata.json",
        "stdout": task_dir / "stdout.txt",
        "stderr": task_dir / "stderr.txt",
    }


def _write_background_metadata(path: Path, metadata: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    tmp.replace(path)


def _read_background_metadata(task_id: str) -> dict[str, Any] | None:
    try:
        path = _background_paths(task_id)["metadata"]
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _read_background_text(path: Path) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - _BACKGROUND_OUTPUT_CAP))
            data = handle.read(_BACKGROUND_OUTPUT_CAP)
    except OSError:
        return ""
    return data.decode("utf-8", errors="replace")


def _background_file_truncated(path: Path) -> bool:
    try:
        return path.stat().st_size >= _BACKGROUND_OUTPUT_CAP
    except OSError:
        return False


# Terminal-state task dirs older than the TTL (or oldest beyond the cap)
# are swept; live or unparsable dirs are never touched.
_BACKGROUND_DIR_TTL_S = 7 * 24 * 3600.0
_BACKGROUND_DIR_MAX = 256
_BACKGROUND_TASK_ID_RE = re.compile(r"^[0-9a-f]{8,64}$")
_BACKGROUND_TERMINAL_RECOVERY = frozenset({"orphaned_process_exited", "orphaned_process_missing"})


def _background_dir_mtime(task_dir: Path) -> float:
    try:
        return max(
            task_dir.stat().st_mtime,
            (task_dir / "metadata.json").stat().st_mtime,
        )
    except OSError:
        try:
            return task_dir.stat().st_mtime
        except OSError:
            return 0.0


def _sweep_background_dirs(
    *,
    ttl_s: float = _BACKGROUND_DIR_TTL_S,
    max_keep: int = _BACKGROUND_DIR_MAX,
) -> int:
    """Delete terminal-state task dirs past ``ttl_s`` or beyond ``max_keep``.

    Terminal means the metadata carries an ``exit_code``, a ``cancelled``
    flag, or a terminal ``recovery_state``. Live and unparsable dirs are
    never removed. Returns the number of directories removed.
    """
    try:
        candidates = [d for d in _background_root().iterdir() if d.is_dir()]
    except OSError:
        return 0
    now = time.time()
    kept_terminal: list[tuple[float, Path]] = []
    removed = 0
    for task_dir in candidates:
        if not _BACKGROUND_TASK_ID_RE.match(task_dir.name):
            continue
        try:
            metadata = json.loads((task_dir / "metadata.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(metadata, dict):
            continue
        terminal = (
            metadata.get("exit_code") is not None
            or bool(metadata.get("cancelled"))
            or str(metadata.get("recovery_state") or "") in _BACKGROUND_TERMINAL_RECOVERY
        )
        if not terminal:
            continue
        mtime = _background_dir_mtime(task_dir)
        if now - mtime >= ttl_s:
            shutil.rmtree(task_dir, ignore_errors=True)
            removed += 1
        else:
            kept_terminal.append((mtime, task_dir))
    # The cap bounds the dirs it can actually delete — terminal dirs still
    # inside their TTL — and nothing else. Sizing overflow by ``len(candidates)``
    # let live dirs (and dirs skipped as unparsable or wrongly named) inflate
    # the count, deleting terminal dirs that had finished seconds ago despite a
    # 7-day TTL. Live/unsweepable dirs cannot be traded against this budget, so
    # they must not shrink it either; only the oldest surplus is dropped.
    overflow = len(kept_terminal) - max_keep
    for _, task_dir in sorted(kept_terminal)[: max(overflow, 0)]:
        shutil.rmtree(task_dir, ignore_errors=True)
        removed += 1
    return removed


def _process_group_id(pid: int) -> int | None:
    if os.name == "nt" or pid <= 0:
        return None
    try:
        return os.getpgid(pid)
    except OSError:
        return None


def _probe_process(pid: int | None) -> tuple[bool, int | None]:
    if not pid or pid <= 0:
        return False, None
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            process_query_limited_information = 0x1000
            still_active = 259
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            handle = kernel32.OpenProcess(
                process_query_limited_information,
                False,
                wintypes.DWORD(pid),
            )
            if not handle:
                return False, None
            try:
                code = wintypes.DWORD()
                ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
                if not ok:
                    return False, None
                exit_code = int(code.value)
                if exit_code == still_active:
                    return True, None
                return False, exit_code
            finally:
                kernel32.CloseHandle(handle)
        except Exception:  # noqa: BLE001
            return True, None
    try:
        waited_pid, status = os.waitpid(pid, os.WNOHANG)
        if waited_pid == pid:
            return False, os.waitstatus_to_exitcode(status)
    except (
        ChildProcessError
    ):  # expected · already reaped elsewhere, falls through to the liveness probe
        pass
    except OSError:  # expected · falls through to the liveness probe below
        pass
    try:
        os.kill(pid, 0)
    except OSError:
        return False, None
    return True, None


def _snapshot_background_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    task_id = str(metadata.get("task_id") or "")
    try:
        exit_code = metadata.get("exit_code")
        exit_code = int(exit_code) if exit_code is not None else None
    except (TypeError, ValueError):
        exit_code = None
    try:
        pid = int(metadata.get("pid") or 0)
    except (TypeError, ValueError):
        pid = 0
    if exit_code is None:
        # Resolve _probe_process via the write_skills module so tests that
        # monkeypatch ``write_skills._probe_process`` observe the patch here.
        from runtime.execution.suckers.write_skills import _probe_process

        running, probed_exit = _probe_process(pid)
        if probed_exit is not None:
            exit_code = probed_exit
            metadata["exit_code"] = exit_code
            with contextlib.suppress(Exception):
                _write_background_metadata(
                    _background_paths(task_id)["metadata"],
                    metadata,
                )
    else:
        running = False

    cancelled = bool(metadata.get("cancelled"))
    if cancelled:
        status = "cancelled"
    elif running:
        status = "running"
    elif exit_code == 0:
        status = "completed"
    elif exit_code is None:
        status = "unknown"
    else:
        status = "failed"

    default_paths = _background_paths(task_id)
    stdout_path = Path(str(metadata.get("stdout_path") or default_paths["stdout"]))
    stderr_path = Path(str(metadata.get("stderr_path") or default_paths["stderr"]))
    raw_stdout = _read_background_text(stdout_path)
    raw_stderr = _read_background_text(stderr_path)
    execution_policy = (
        dict(metadata["execution_policy"])
        if isinstance(metadata.get("execution_policy"), dict)
        else {}
    )
    execution_policy = _background_policy_with_result(
        execution_policy,
        status=status,
        exit_code=exit_code,
        started_at=_optional_float(metadata.get("started_at")),
        stdout_truncated=_background_file_truncated(stdout_path),
        stderr_truncated=_background_file_truncated(stderr_path),
    )
    return {
        "task_id": task_id,
        "status": status,
        "argv": list(metadata.get("argv") or []),
        "cwd": metadata.get("cwd"),
        "sandbox_backend": str(metadata.get("sandbox_backend") or "direct"),
        "sandbox_hard": bool(metadata.get("sandbox_hard")),
        "execution_policy": execution_policy,
        "pid": pid,
        "process_group_id": metadata.get("process_group_id"),
        "exit_code": exit_code,
        "running": status == "running",
        "stdout": raw_stdout[:_BACKGROUND_OUTPUT_CAP],
        "stderr": raw_stderr[:_BACKGROUND_OUTPUT_CAP],
        "stdout_truncated": _background_file_truncated(stdout_path),
        "stderr_truncated": _background_file_truncated(stderr_path),
        "started_at": metadata.get("started_at"),
        "recovery_state": metadata.get("recovery_state"),
        "recovered": True,
    }


def recover_background_processes() -> dict[str, int]:
    """Scan persisted background jobs and converge stale metadata.

    A restarted service cannot reconstruct ``Popen`` handles, but it can
    safely observe processes launched in their own process groups. Live jobs
    are marked as externally adopted and remain pollable; dead jobs are
    recorded as exited/unknown instead of remaining indefinitely ``running``.
    No process is killed during startup.
    """

    stats = {"scanned": 0, "adopted": 0, "converged": 0, "unknown": 0}
    try:
        candidates = list(_background_root().iterdir())
    except OSError:
        return stats
    for task_dir in candidates:
        if not task_dir.is_dir():
            continue
        try:
            metadata = json.loads((task_dir / "metadata.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(metadata, dict) or not metadata.get("task_id"):
            continue
        stats["scanned"] += 1
        if metadata.get("cancelled") or metadata.get("exit_code") is not None:
            continue
        try:
            pid = int(metadata.get("pid") or 0)
        except (TypeError, ValueError):
            pid = 0
        running, exit_code = _probe_process(pid)
        if running:
            metadata["recovery_state"] = "adopted_external"
            stats["adopted"] += 1
        else:
            if exit_code is not None:
                metadata["exit_code"] = exit_code
                metadata["recovery_state"] = "orphaned_process_exited"
                stats["converged"] += 1
            else:
                metadata["recovery_state"] = "orphaned_process_missing"
                stats["unknown"] += 1
        metadata["recovered_at"] = time.time()
        with contextlib.suppress(Exception):
            _write_background_metadata(task_dir / "metadata.json", metadata)
    return stats


def background_process_identity_matches(metadata: dict[str, Any]) -> bool:
    """Fail closed if a recovered PID no longer belongs to our process group."""

    if os.name == "nt":
        return True
    try:
        pid = int(metadata.get("pid") or 0)
        expected = int(metadata.get("process_group_id") or pid)
        return pid > 0 and os.getpgid(pid) == expected and expected == pid
    except (OSError, TypeError, ValueError):
        return False
