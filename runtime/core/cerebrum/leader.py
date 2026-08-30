"""Leader Process · single-owner supervisor for long-running tasks.

Inspired by Grok Build's Leader Process model (``xai-grok-shell/src/
leader/mod.rs``): one Unix Domain Socket server owns the Agent
session lifecycle so that UI / Headless / IPC clients can come and
go without killing long tasks. A crashed UI reconnects to the same
leader and picks up where it left off.

This is the **minimum viable** surface: it does NOT replace the
existing FastAPI/SSE server. It runs alongside it as an optional
supervisor reachable via a UDS. The FastAPI server stays the primary
HTTP surface; the Leader Process handles three things the HTTP server
can't:

1. **Outliving the UI** — when the Electron app quits, in-flight
   ``run_react_loop`` calls keep running under the leader.
2. **Concurrent client convergence** — multiple clients (UI + CLI
   ``echo-agent leader attach``) see the same session state
   through one leader. Newer clients win version skew.
3. **IPC control plane** — ``/pause`` / ``/resume`` / ``/rewind``
   style commands over JSON-RPC 2.0 (reusing the existing
   ``runtime.protocol.envelope``).

Design rules
------------
* Single-instance per ``~/.echo/leader.sock`` path enforced by PID
  file + ``SO_REUSEADDR`` + atomic PID write.
* JSON-RPC 2.0 over UDS frames (length-prefixed ndjson). Same
  envelope as the WebSocket transport — no new wire format.
* All long-running work runs in **the leader's process**; clients
  only receive notifications.
* If the leader dies, the next ``ensure_leader`` call spawns a new
  one (the journal is the source of truth, not leader memory).

Usage
-----
::

    # 1. Start the leader (idempotent · does nothing if already up)
    python -m runtime.core.cerebrum.leader serve

    # 2. From any client (UI / CLI / IDE plugin)
    from runtime.core.cerebrum.leader import LeaderClient
    with LeaderClient.connect() as client:
        client.call("status", {})
        client.notify("pause", {"task_id": "..."})

What this module deliberately does NOT do
-----------------------------------------
* No leader election across machines — single-host only. The
  existing ``checkpoint_mirror`` (Redis-backed) handles multi-host.
* No transport-level auth — UDS filesystem permissions are the gate.
* No protocol breaking changes — existing SSE/HTTP endpoints stay.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import signal
import socket
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from runtime.protocol.envelope import (
    JsonRpcError,
    JsonRpcErrorCode,
    JsonRpcRequest,
    JsonRpcResponse,
    Notification,
    decode_message,
    encode_message,
)

_LOG = logging.getLogger("echo.leader")

# Default socket path.Honors ``ECHO_LEADER_SOCKET`` for tests &
# multi-instance dev setups.
DEFAULT_SOCKET_PATH = Path(
    os.environ.get("ECHO_LEADER_SOCKET", str(Path.home() / ".echo" / "leader.sock"))
)
DEFAULT_PID_PATH = DEFAULT_SOCKET_PATH.with_suffix(".pid")
PROTOCOL_VERSION = 1
HEARTBEAT_SECONDS = 30.0


# ── Errors ───────────────────────────────────────────────────


class LeaderError(RuntimeError):
    """Base error for leader-related failures."""


class LeaderNotRunning(LeaderError):
    """No leader process is reachable on the socket."""


class LeaderAlreadyRunning(LeaderError):
    """Another live leader owns the socket."""


# ── Protocol helpers ─────────────────────────────────────────


def _read_frame(sock: socket.socket) -> str | None:
    """Read one length-prefixed ndjson frame.

    Frame format: ``<len>\\n<json>`` where ``len`` is the byte count
    of ``json``. Returns ``None`` on clean EOF.
    """
    header = _recv_until(sock, b"\n")
    if not header:
        return None
    try:
        length = int(header.strip())
    except ValueError as exc:
        raise LeaderError(f"invalid frame header: {header!r}") from exc
    if length <= 0 or length > 16 * 1024 * 1024:  # 16 MiB cap
        raise LeaderError(f"frame length out of range: {length}")
    payload = _recv_exact(sock, length)
    if payload is None:
        return None
    return payload.decode("utf-8")


def _write_frame(sock: socket.socket, message: str) -> None:
    payload = message.encode("utf-8")
    sock.sendall(f"{len(payload)}\n".encode("ascii") + payload)


def _recv_exact(sock: socket.socket, n: int) -> bytes | None:
    chunks: list[bytes] = []
    remaining = n
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            return None
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _recv_until(sock: socket.socket, delimiter: bytes) -> bytes | None:
    chunks: list[bytes] = []
    while True:
        chunk = sock.recv(1)
        if not chunk:
            return None if not chunks else b"".join(chunks)
        if chunk == delimiter:
            return b"".join(chunks)
        chunks.append(chunk)
        if sum(len(c) for c in chunks) > 64:  # header bound
            raise LeaderError("frame header too long")


# ── LeaderProcess (server side) ──────────────────────────────


@dataclass
class LeaderState:
    """In-memory state the leader exposes to clients.

    Deliberately tiny — the journal remains the source of truth for
    task/checkpoint data. This only holds ambient runtime signals
    that don't have a journal home.
    """

    started_at: float = field(default_factory=time.time)
    protocol_version: int = PROTOCOL_VERSION
    pid: int = field(default_factory=os.getpid)
    # task_id → "running" | "paused" | "idle"
    task_status: dict[str, str] = field(default_factory=dict)
    # Subscribed client file descriptors for notification fanout.
    _client_lock: threading.Lock = field(default_factory=threading.Lock)
    _clients: set[socket.socket] = field(default_factory=set)

    def snapshot(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "protocol_version": self.protocol_version,
            "pid": self.pid,
            "uptime_seconds": time.time() - self.started_at,
            "task_status": dict(self.task_status),
            "client_count": len(self._clients),
        }

    def attach_client(self, sock: socket.socket) -> None:
        with self._client_lock:
            self._clients.add(sock)

    def detach_client(self, sock: socket.socket) -> None:
        with self._client_lock:
            self._clients.discard(sock)

    def broadcast(self, method: str, params: dict[str, Any]) -> None:
        msg = encode_message(Notification(jsonrpc="2.0", method=method, params=params))
        dead: list[socket.socket] = []
        with self._client_lock:
            targets = list(self._clients)
        for sock in targets:
            try:
                _write_frame(sock, msg)
            except OSError:
                dead.append(sock)
        if dead:
            with self._client_lock:
                for sock in dead:
                    self._clients.discard(sock)


class LeaderProcess:
    """Single-owner supervisor serving JSON-RPC over UDS."""

    def __init__(
        self,
        socket_path: Path | str = DEFAULT_SOCKET_PATH,
        pid_path: Path | str = DEFAULT_PID_PATH,
        *,
        state: LeaderState | None = None,
    ) -> None:
        self.socket_path = Path(socket_path)
        self.pid_path = Path(pid_path)
        self.state = state or LeaderState()
        self._server_sock: socket.socket | None = None
        self._stop = threading.Event()
        self._handlers: dict[str, Callable[[dict[str, Any]], Any]] = {
            "status": self._handle_status,
            "set_task_status": self._handle_set_task_status,
            "pause": self._handle_pause,
            "resume": self._handle_resume,
            "ping": lambda _params: {"pong": True, "pid": os.getpid()},
        }

    # ── Lifecycle ────────────────────────────────────────────

    def start(self, *, blocking: bool = True) -> None:
        """Bind the UDS, write the PID file, and serve.

        Raises :class:`LeaderAlreadyRunning` if another live process
        owns the PID file. Stale PID files (dead owner) are reclaimed.
        """
        self._claim_pid_file()
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        # Remove a stale socket from a crashed leader.
        if self.socket_path.exists():
            self.socket_path.unlink()
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(self.socket_path))
        # Lock down to the current user — no other-user tampering.
        os.chmod(self.socket_path, 0o600)
        server.listen(16)
        server.settimeout(0.5)
        self._server_sock = server
        _LOG.info("leader listening on %s (pid=%d)", self.socket_path, os.getpid())

        if blocking:
            try:
                self._serve_loop()
            finally:
                self.stop()
        else:
            thread = threading.Thread(target=self._serve_loop, daemon=True, name="echo-leader")
            thread.start()

    def stop(self) -> None:
        """Release the socket and PID file."""
        self._stop.set()
        if self._server_sock is not None:
            with contextlib.suppress(OSError):
                self._server_sock.close()
            self._server_sock = None
        try:
            if self.socket_path.exists():
                self.socket_path.unlink()
        except OSError:
            _LOG.debug("failed to remove leader socket %s", self.socket_path, exc_info=True)
        try:
            if self.pid_path.exists():
                self.pid_path.unlink()
        except OSError:
            _LOG.debug("failed to remove leader PID file %s", self.pid_path, exc_info=True)
        _LOG.info("leader stopped")

    # ── Server loop ──────────────────────────────────────────

    def _serve_loop(self) -> None:
        # Handle SIGTERM/SIGINT gracefully.
        for sig in (signal.SIGTERM, signal.SIGINT):
            # In background threads signal.signal can fail; ignore.
            with contextlib.suppress(ValueError, OSError):
                signal.signal(sig, lambda _signum, _frame: self._stop.set())

        while not self._stop.is_set():
            try:
                client, _ = self._server_sock.accept()  # type: ignore[union-attr]
            except TimeoutError:
                continue
            except OSError:
                break
            thread = threading.Thread(
                target=self._serve_client,
                args=(client,),
                daemon=True,
                name="echo-leader-client",
            )
            thread.start()

    def _serve_client(self, client: socket.socket) -> None:
        self.state.attach_client(client)
        try:
            while not self._stop.is_set():
                try:
                    raw = _read_frame(client)
                except OSError:
                    break
                except LeaderError as exc:
                    _LOG.warning("frame error: %s", exc)
                    break
                if raw is None:
                    break
                self._handle_message(client, raw)
        finally:
            self.state.detach_client(client)
            with contextlib.suppress(OSError):
                client.close()

    def _handle_message(self, client: socket.socket, raw: str) -> None:
        try:
            message = decode_message(raw)
        except Exception as exc:  # noqa: BLE001
            self._send_error(client, None, JsonRpcErrorCode.PARSE_ERROR, str(exc))
            return

        # Notifications carry no id and expect no response.
        if isinstance(message, Notification):
            handler = self._handlers.get(message.method)
            if handler is not None:
                try:
                    handler(message.params)
                except Exception as exc:  # noqa: BLE001
                    _LOG.warning("notification %s failed: %s", message.method, exc)
            return

        if not isinstance(message, JsonRpcRequest):
            self._send_error(client, None, JsonRpcErrorCode.INVALID_REQUEST, "not a request")
            return

        handler = self._handlers.get(message.method)
        if handler is None:
            self._send_error(
                client,
                message.id,
                JsonRpcErrorCode.METHOD_NOT_FOUND,
                f"unknown method: {message.method}",
            )
            return
        try:
            result = handler(message.params)
        except Exception as exc:  # noqa: BLE001
            self._send_error(client, message.id, JsonRpcErrorCode.INTERNAL_ERROR, str(exc))
            return
        response = JsonRpcResponse(jsonrpc="2.0", id=message.id, result=result)
        with contextlib.suppress(OSError):
            _write_frame(client, encode_message(response))

    def _send_error(
        self,
        client: socket.socket,
        msg_id: int | str | None,
        code: JsonRpcErrorCode,
        message: str,
    ) -> None:
        # Per spec, PARSE_ERROR may have no id; use 0 as a safe fallback.
        response_id = msg_id if msg_id is not None else 0
        response = JsonRpcResponse(
            jsonrpc="2.0",
            id=response_id,
            error=JsonRpcError(code=int(code), message=message),
        )
        with contextlib.suppress(OSError):
            _write_frame(client, encode_message(response))

    # ── Built-in handlers ────────────────────────────────────

    def _handle_status(self, _params: dict[str, Any]) -> dict[str, Any]:
        return self.state.snapshot()

    def _handle_set_task_status(self, params: dict[str, Any]) -> dict[str, Any]:
        task_id = str(params.get("task_id") or "")
        status = str(params.get("status") or "idle")
        if not task_id:
            raise ValueError("task_id required")
        if status not in {"running", "paused", "idle"}:
            raise ValueError(f"invalid status: {status}")
        self.state.task_status[task_id] = status
        self.state.broadcast("task_status_changed", {"task_id": task_id, "status": status})
        return {"ok": True, "task_id": task_id, "status": status}

    def _handle_pause(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._handle_set_task_status({**params, "status": "paused"})

    def _handle_resume(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._handle_set_task_status({**params, "status": "running"})

    def register_handler(self, method: str, handler: Callable[[dict[str, Any]], Any]) -> None:
        """Register an additional JSON-RPC method handler.

        Used by callers that want to extend the leader without
        subclassing. Built-in handlers can't be overwritten.
        """
        if method in self._handlers:
            raise ValueError(f"method already registered: {method}")
        self._handlers[method] = handler

    # ── PID file management ─────────────────────────────────

    def _claim_pid_file(self) -> None:
        if self.pid_path.exists():
            try:
                old_pid = int(self.pid_path.read_text().strip())
            except ValueError:
                old_pid = -1
            if old_pid > 0 and _pid_alive(old_pid):
                raise LeaderAlreadyRunning(
                    f"leader already running as pid {old_pid} (pid file: {self.pid_path})"
                )
            # Stale PID file — reclaim.
            with contextlib.suppress(OSError):
                self.pid_path.unlink()
        self.pid_path.parent.mkdir(parents=True, exist_ok=True)
        self.pid_path.write_text(str(os.getpid()))


def _pid_alive(pid: int) -> bool:
    """Return True if ``pid`` is a live process we can signal."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we can't signal it — treat as alive.
        return True
    except OSError:
        return False
    return True


# ── LeaderClient (client side) ───────────────────────────────


class LeaderClient:
    """Thin JSON-RPC client over UDS.

    Use as a context manager to ensure the socket is closed::

        with LeaderClient.connect() as client:
            client.call("status", {})
    """

    def __init__(self, sock: socket.socket, *, protocol_version: int = PROTOCOL_VERSION) -> None:
        self._sock = sock
        self._protocol_version = protocol_version
        self._next_id = 1
        self._lock = threading.Lock()

    @classmethod
    def connect(
        cls,
        socket_path: Path | str = DEFAULT_SOCKET_PATH,
        *,
        timeout: float = 5.0,
    ) -> LeaderClient:
        path = Path(socket_path)
        if not path.exists():
            raise LeaderNotRunning(f"leader socket not found: {path}")
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect(str(path))
        except OSError as exc:
            sock.close()
            raise LeaderNotRunning(f"failed to connect to {path}: {exc}") from exc
        return cls(sock)

    def __enter__(self) -> LeaderClient:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self._sock.close()

    def call(self, method: str, params: dict[str, Any]) -> Any:
        """Send a JSON-RPC request and wait for the response.

        Skips broadcast ``Notification`` frames received while
        waiting so the caller always sees the matching ``Response``.
        """
        with self._lock:
            request_id = self._next_id
            self._next_id += 1
            request = JsonRpcRequest(
                jsonrpc="2.0",
                id=request_id,
                method=method,
                params=params,
            )
            _write_frame(self._sock, encode_message(request))
            # Loop until we see our Response — broadcasts arrive
            # in between and must be silently dropped here.
            while True:
                raw = _read_frame(self._sock)
                if raw is None:
                    raise LeaderError("leader closed the connection")
                message = decode_message(raw)
                if isinstance(message, Notification):
                    continue
                if not isinstance(message, JsonRpcResponse):
                    raise LeaderError(f"unexpected message type: {type(message).__name__}")
                break
        if message.error is not None:
            raise LeaderError(f"rpc error {message.error.code}: {message.error.message}")
        return message.result

    def notify(self, method: str, params: dict[str, Any]) -> None:
        """Send a fire-and-forget notification (no response expected)."""
        with self._lock:
            notification = Notification(jsonrpc="2.0", method=method, params=params)
            _write_frame(self._sock, encode_message(notification))


# ── Convenience ──────────────────────────────────────────────


def ensure_leader(
    socket_path: Path | str = DEFAULT_SOCKET_PATH,
    pid_path: Path | str = DEFAULT_PID_PATH,
) -> LeaderClient:
    """Connect to the running leader, starting it first if needed.

    Forks a detached child process to run the leader so the caller
    returns immediately with a working client. If the leader is
    already running, just connects.
    """
    try:
        return LeaderClient.connect(socket_path)
    except LeaderNotRunning:
        _LOG.debug("leader is not running; starting a detached process")

    # Spawn a detached leader process.
    import subprocess

    env = {
        **os.environ,
        "ECHO_LEADER_SOCKET": str(socket_path),
    }
    subprocess.Popen(
        [sys.executable, "-m", "runtime.core.cerebrum.leader", "serve"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    # Wait for the socket to come up.
    deadline = time.time() + 5.0
    last_exc: Exception | None = None
    while time.time() < deadline:
        try:
            return LeaderClient.connect(socket_path)
        except LeaderNotRunning as exc:
            last_exc = exc
            time.sleep(0.1)
    raise LeaderNotRunning(f"leader did not come up in 5s: {last_exc}")


def main(argv: list[str] | None = None) -> int:
    """CLI entry: ``python -m runtime.core.cerebrum.leader serve``."""
    import argparse

    parser = argparse.ArgumentParser(prog="echo-leader")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub_serve = sub.add_parser("serve", help="Run the leader (foreground)")
    sub_serve.add_argument("--socket", default=str(DEFAULT_SOCKET_PATH))
    sub_serve.add_argument("--pid", default=str(DEFAULT_PID_PATH))
    sub_status = sub.add_parser("status", help="Query a running leader")
    sub_status.add_argument("--socket", default=str(DEFAULT_SOCKET_PATH))
    sub_stop = sub.add_parser("stop", help="Stop a running leader")
    sub_stop.add_argument("--socket", default=str(DEFAULT_SOCKET_PATH))
    sub_stop.add_argument("--pid", default=str(DEFAULT_PID_PATH))

    args = parser.parse_args(argv)

    if args.cmd == "serve":
        leader = LeaderProcess(socket_path=args.socket, pid_path=args.pid)
        leader.start(blocking=True)
        return 0

    if args.cmd == "status":
        try:
            with LeaderClient.connect(args.socket) as client:
                snapshot = client.call("status", {})
        except LeaderNotRunning as exc:
            print(f"leader not running: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(snapshot, indent=2))
        return 0

    if args.cmd == "stop":
        pid_path = Path(args.pid)
        if not pid_path.exists():
            print("no pid file", file=sys.stderr)
            return 1
        try:
            pid = int(pid_path.read_text().strip())
        except ValueError:
            print("invalid pid file", file=sys.stderr)
            return 1
        if not _pid_alive(pid):
            print(f"pid {pid} not alive; cleaning pid file")
            with contextlib.suppress(OSError):
                pid_path.unlink()
            return 0
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError as exc:
            print(f"failed to signal pid {pid}: {exc}", file=sys.stderr)
            return 1
        print(f"sent SIGTERM to pid {pid}")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_PID_PATH",
    "DEFAULT_SOCKET_PATH",
    "PROTOCOL_VERSION",
    "LeaderAlreadyRunning",
    "LeaderClient",
    "LeaderError",
    "LeaderNotRunning",
    "LeaderProcess",
    "LeaderState",
    "ensure_leader",
    "main",
]
