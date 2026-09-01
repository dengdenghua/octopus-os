"""User-triggered lifecycle control for an existing local ComfyUI installation."""

from __future__ import annotations

import atexit
import contextlib
import os
import subprocess
import sys
import threading
from pathlib import Path
from urllib.parse import urlparse

from runtime.platform.process.paths import app_paths
from runtime.platform.process.tree import process_group_kwargs, terminate_process_tree

_PROCESS: subprocess.Popen[bytes] | None = None
_LOCK = threading.Lock()


def resolve_comfyui_home() -> Path | None:
    from runtime.sensing.gateway.comfyui_manager import managed_home

    configured = os.environ.get("ECHO_COMFYUI_HOME", "").strip()
    candidates = [
        Path(configured).expanduser() if configured else None,
        managed_home(),
        app_paths().data_dir / "comfyui",
        Path.home() / "ComfyUI",
    ]
    for candidate in candidates:
        if candidate is not None and candidate.is_dir():
            return candidate.resolve()
    return None


def resolve_comfyui_command() -> tuple[list[str], Path] | None:
    from runtime.sensing.gateway.comfyui_manager import managed_home, managed_python

    home = resolve_comfyui_home()
    if home is None or not (home / "main.py").is_file():
        return None
    python_candidates = [
        home / ".venv" / "bin" / "python",
        home / "venv" / "bin" / "python",
    ]
    if home == managed_home() and managed_python().is_file():
        python_candidates.insert(0, managed_python())
    python = next((item for item in python_candidates if item.is_file()), Path(sys.executable))
    raw_url = os.environ.get("ECHO_COMFYUI_URL", "http://127.0.0.1:8188")
    parsed = urlparse(raw_url)
    port = parsed.port or 8188
    return (
        [
            str(python),
            str(home / "main.py"),
            "--listen",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        home,
    )


def process_status() -> dict[str, object]:
    with _LOCK:
        process = _PROCESS
        owned = process is not None
        running = process is not None and process.poll() is None
        pid = process.pid if running else None
    return {"owned": owned, "running": running, "pid": pid}


def start_comfyui() -> str:
    """Start a detected installation with a fixed shell-free argv."""
    global _PROCESS
    with _LOCK:
        if _PROCESS is not None and _PROCESS.poll() is None:
            return "already_started"
        resolved = resolve_comfyui_command()
        if resolved is None:
            return "not_found"
        argv, home = resolved
        log_path = app_paths().data_dir / "design" / "comfyui.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with log_path.open("ab") as log:
                _PROCESS = subprocess.Popen(  # noqa: S603 — fixed argv, shell=False
                    argv,
                    cwd=str(home),
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    **process_group_kwargs(),
                )
        except OSError:
            _PROCESS = None
            return "error"
    atexit.register(stop_comfyui)
    return "started"


def stop_comfyui() -> str:
    """Stop only the child launched by this process; never touch external ComfyUI."""
    global _PROCESS
    with _LOCK:
        process, _PROCESS = _PROCESS, None
    if process is None:
        return "not_owned"
    if process.poll() is not None:
        return "already_stopped"
    with contextlib.suppress(Exception):
        terminate_process_tree(process, grace_s=3, kill_wait_s=2)
    return "stopped"


__all__ = [
    "process_status",
    "resolve_comfyui_command",
    "resolve_comfyui_home",
    "start_comfyui",
    "stop_comfyui",
]
