"""Append-only performance journal for team-topology runs.

One JSONL line per ``TeamRunResult``. The evolver reads this file to
score topologies; the file shape is the *only* contract between
TeamRunner and TopologyEvolver. Keep it stable.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from runtime.platform.process.paths import app_paths

from .team_runner import TeamRunResult

_logger = logging.getLogger("echo.organization.performance_log")

_DEFAULT_RELATIVE = "topology_performance.jsonl"
_LOCK = threading.Lock()


def _default_path() -> Path:
    try:
        return app_paths().data_dir / _DEFAULT_RELATIVE
    except (AttributeError, OSError, TypeError):
        return Path("data") / _DEFAULT_RELATIVE


def record_run(
    result: TeamRunResult,
    *,
    path: Path | str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Append one row capturing the metrics needed for evolution.

    Cross-process safe: uses ``fcntl.flock`` / ``msvcrt.locking`` like
    the genome journal. Failure is swallowed (logged) so a broken
    disk doesn't take a successful turn down with it.
    """
    target = Path(path) if path else _default_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    row: dict[str, Any] = {
        "ts": time.time(),
        "topology": result.topology_name,
        "fingerprint": result.topology_fingerprint,
        "task_bucket": result.task_bucket,
        "success": bool(result.success),
        "quality_score": result.quality_score,
        "iterations": int(result.iterations),
        "total_duration_ms": float(result.total_duration_ms),
        "role_count": len(result.role_outputs),
        "roles": [
            {
                "role": str(o.role),
                "agent_id": o.agent_id,
                "duration_ms": float(o.duration_ms),
                "error": o.error,
                "score": o.score,
            }
            for o in result.role_outputs
        ],
        "error": result.error,
    }
    if extra:
        row["extra"] = extra

    line = json.dumps(row, ensure_ascii=False) + "\n"
    with _LOCK:
        try:
            with target.open("a", encoding="utf-8") as f:
                fd = f.fileno()
                _locked = _try_lock(fd)
                try:
                    f.write(line)
                    f.flush()
                    with contextlib.suppress(OSError):
                        os.fsync(fd)
                finally:
                    if _locked:
                        _try_unlock(fd, f)
        except OSError as exc:
            _logger.warning("topology performance write failed: %s", exc)


def _try_lock(fd: int) -> bool:
    if os.name == "nt":
        try:
            import msvcrt as _msvcrt

            _msvcrt.locking(fd, _msvcrt.LK_LOCK, 1)
            return True
        except OSError:
            return False
    else:
        try:
            import fcntl as _fcntl

            _fcntl.flock(fd, _fcntl.LOCK_EX)
            return True
        except (OSError, ImportError):
            return False


def _try_unlock(fd: int, f: Any) -> None:
    try:
        if os.name == "nt":
            import msvcrt as _msvcrt

            with contextlib.suppress(OSError):
                f.seek(0, 0)
            _msvcrt.locking(fd, _msvcrt.LK_UNLCK, 1)
        else:
            import fcntl as _fcntl

            _fcntl.flock(fd, _fcntl.LOCK_UN)
    except OSError:  # noqa: BLE001 — perf log write best-effort
        pass


def read_runs(
    *,
    path: Path | str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Return rows newest-first. ``limit`` caps the result size."""
    target = Path(path) if path else _default_path()
    if not target.is_file():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with target.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError as exc:
        _logger.warning("topology performance read failed: %s", exc)
        return []
    rows.reverse()  # newest first
    if limit is not None and limit >= 0:
        rows = rows[:limit]
    return rows


__all__ = ["read_runs", "record_run"]
