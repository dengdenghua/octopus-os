"""Optional co-launch of the echo-storage sibling service.

echo-storage stays an independent, separately-deployable package — it serves
File Agent for the whole family (agent / os / enterprise) and carries heavy
OCR / vision / embedding deps, so it must NOT be merged into echo-agent. But
for a single-machine user, starting two services by hand is friction. This
supervisor lets ``echo serve`` ALSO bring storage up as a child process,
opt-in via ``ECHO_STORAGE_AUTOSTART`` — one command for them, while everyone
else still runs storage standalone and points ``ECHO_STORAGE_URL`` at it.
The browser-facing API is consolidated under echo-agent's
``/api/storage/v1/*`` proxy, so port 8767 remains private in co-launch mode.

Best-effort + graceful: disabled / already running / binary not found / fails to
start → log + skip; the agent runs fine and ``search_documents`` degrades to
"storage unavailable". The child is terminated when the agent process exits.
Resolution never uses a shell, so nothing here is an injection surface.
"""

from __future__ import annotations

import atexit
import contextlib
import logging
import os
import shlex
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

_LOG = logging.getLogger("echo.storage_supervisor")
_proc: subprocess.Popen | None = None
_lock = threading.Lock()

# Heartbeat supervision: how often to probe liveness, and the minimum gap between
# restart attempts (so a wedged / boot-looping storage can't be thrash-restarted).
_HEARTBEAT_INTERVAL_S = 20.0
_RESTART_BACKOFF_S = 30.0

# Liveness + restart state, surfaced via storage_status() for a status endpoint / UI.
_status_lock = threading.Lock()
_state: dict[str, Any] = {
    "up": False,
    "last_check_ts": 0.0,
    "restart_count": 0,
    "last_restart_ts": 0.0,
    "heartbeat": False,
}
_heartbeat_started = False


def _autostart_enabled() -> bool:
    return os.environ.get("ECHO_STORAGE_AUTOSTART", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _storage_port() -> str:
    from runtime.execution.suckers.storage_skills import _base_url

    with contextlib.suppress(Exception):
        port = urlparse(_base_url()).port
        if port:
            return str(port)
    return "8767"


def resolve_storage_command() -> list[str] | None:
    """Resolve the argv that launches ``echo-storage serve``, or ``None`` if
    it can't be found. Priority: ``ECHO_STORAGE_CMD`` (explicit) → the sibling
    repo's ``.venv`` console script → a console script on PATH. Always argv lists
    (no shell)."""
    port = _storage_port()

    explicit = (os.environ.get("ECHO_STORAGE_CMD") or "").strip()
    if explicit:
        argv = shlex.split(explicit)
        return argv if "serve" in argv else [*argv, "serve", "--port", port]

    # Sibling layout: <…>/echo/echo-agent/runtime/… → <…>/echo/echo-storage
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "runtime").is_dir():
            sibling = parent.parent / "echo-storage" / ".venv" / "bin" / "echo-storage"
            if sibling.exists():
                return [str(sibling), "serve", "--port", port]
            break

    found = shutil.which("echo-storage")
    if found:
        return [found, "serve", "--port", port]
    return None


def _already_up() -> bool:
    # Liveness, not auth: a 401 means Storage is up (restarting won't fix auth),
    # so storage_alive treats any HTTP response as up — only a connection failure
    # is 'down'. This keeps the heartbeat from thrash-restarting a healthy Storage.
    from runtime.execution.suckers.storage_skills import storage_alive

    return storage_alive(timeout=1.5)


def storage_status() -> dict[str, Any]:
    """Snapshot of storage liveness for a status endpoint / UI light. Returns the
    heartbeat-cached value when the heartbeat is running; otherwise probes once on
    demand so the answer is still correct when storage is externally managed."""
    with _status_lock:
        snap = dict(_state)
    if not snap["heartbeat"]:
        up = _already_up()
        snap["up"] = up
        snap["last_check_ts"] = time.time()
    return snap


def _record(up: bool) -> None:
    with _status_lock:
        _state["up"] = up
        _state["last_check_ts"] = time.time()


def _launch(cmd: list[str]) -> bool:
    """Spawn the storage child (shell-free, resolved argv). Idempotent: returns
    True without respawning when a live child already exists."""
    global _proc
    with _lock:
        if _proc is not None and _proc.poll() is None:
            return True
        _proc = subprocess.Popen(  # noqa: S603 — argv list, shell=False, resolved command
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        atexit.register(stop_storage)
    _LOG.info("echo-storage launched: %s", " ".join(cmd))
    return True


def _should_restart(
    *,
    up: bool,
    autostart: bool,
    proc_alive: bool,
    resolvable: bool,
    now: float,
    last_restart: float,
    backoff_s: float,
) -> bool:
    """Pure restart decision (keeps the heartbeat loop testable + thrash-free):
    relaunch only when storage is DOWN, we own its lifecycle (autostart), we can
    resolve the launch command, our own child isn't already (re)booting, and we
    haven't just tried within the backoff window."""
    if up or not autostart or not resolvable or proc_alive:
        return False
    return (now - last_restart) >= backoff_s


def maybe_start_storage(*, force: bool = False) -> str:
    """Co-launch storage when opt-in + not already up + resolvable. Returns a
    status: ``disabled`` / ``already_running`` / ``not_found`` / ``started`` /
    ``error``. Non-blocking — readiness is logged from a background thread, so
    server boot is never delayed. Never raises. Ongoing supervision (restart on
    death) is the heartbeat's job — see start_storage_heartbeat()."""
    # Service boot remains opt-in, but an explicit user action from the local
    # UI is itself consent to start the bundled sibling service.  Keeping the
    # same gate here made the UI's “重新连接” button a permanent no-op whenever
    # ECHO_STORAGE_AUTOSTART was unset.
    if not force and not _autostart_enabled():
        return "disabled"
    try:
        if _already_up():
            _LOG.info("echo-storage already running; not co-launching")
            _record(True)
            return "already_running"
        cmd = resolve_storage_command()
        if cmd is None:
            _LOG.info(
                "echo-storage not found; skipping autostart (install it, or set ECHO_STORAGE_CMD)"
            )
            return "not_found"
        _launch(cmd)
        threading.Thread(target=_wait_ready, name="storage-ready", daemon=True).start()
        return "started"
    except Exception as exc:  # noqa: BLE001 — autostart is strictly best-effort
        _LOG.warning("echo-storage autostart failed: %s", exc)
        return "error"


def _wait_ready(*, attempts: int = 20, interval: float = 0.5) -> None:
    for _ in range(attempts):
        proc = _proc
        if proc is None or proc.poll() is not None:
            _LOG.warning("echo-storage exited during startup; running without it")
            return
        if _already_up():
            _LOG.info("echo-storage co-launched and ready (pid %s)", proc.pid)
            return
        time.sleep(interval)
    _LOG.info("echo-storage not ready yet; search_documents will pick it up once it is")


def start_storage_heartbeat() -> None:
    """Start the ongoing supervision heartbeat once (idempotent, daemon thread).

    No-op unless autostart owns storage's lifecycle — when storage is externally
    managed we observe but don't restart it. Upgrades the old one-shot 10s
    readiness window into continuous liveness + restart-on-death, so a storage
    that boots late or crashes mid-session is picked back up instead of silently
    leaving ``search_documents`` degraded forever."""
    global _heartbeat_started
    if not _autostart_enabled():
        return
    with _status_lock:
        if _heartbeat_started:
            return
        _heartbeat_started = True
        _state["heartbeat"] = True
    threading.Thread(target=_heartbeat_loop, name="storage-heartbeat", daemon=True).start()
    _LOG.info("echo-storage heartbeat supervisor started")


def _heartbeat_loop() -> None:
    while True:
        time.sleep(_HEARTBEAT_INTERVAL_S)
        try:
            up = _already_up()
            _record(up)
            if up:
                continue
            cmd = resolve_storage_command()
            proc = _proc
            proc_alive = proc is not None and proc.poll() is None
            with _status_lock:
                last_restart = float(_state["last_restart_ts"])
            if _should_restart(
                up=up,
                autostart=_autostart_enabled(),
                proc_alive=proc_alive,
                resolvable=cmd is not None,
                now=time.time(),
                last_restart=last_restart,
                backoff_s=_RESTART_BACKOFF_S,
            ):
                assert cmd is not None  # resolvable=True guaranteed it
                _launch(cmd)
                with _status_lock:
                    _state["restart_count"] = int(_state["restart_count"]) + 1
                    _state["last_restart_ts"] = time.time()
                _LOG.warning(
                    "storage heartbeat: echo-storage was down — relaunched (%s)",
                    " ".join(cmd),
                )
        except Exception as exc:  # noqa: BLE001 — the heartbeat must never die
            _LOG.debug("storage heartbeat tick error: %s", exc)


def stop_storage() -> None:
    """Terminate the co-launched child (if we started one). Idempotent."""
    global _proc
    with _lock:
        proc, _proc = _proc, None
    if proc is None or proc.poll() is not None:
        return
    with contextlib.suppress(Exception):
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            proc.kill()
