"""terminal_router · WebSocket-based persistent shell sessions.

Each code thread can own one shell session. The shell process stays
alive across WebSocket reconnects so the user doesn't lose state
when switching tabs.

Environment state persistence
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
When ``enable_state_snapshot`` is ``True`` (default on init), each
command is wrapped so the shell environment (cwd, env vars) persists
across commands — even if the underlying process restarts.

Safe-rm protection
~~~~~~~~~~~~~~~~~~
When ``enable_safe_rm`` is ``True`` (default), dangerous file operations
(rm, del, mv, etc.) are intercepted and blocked based on path allow/deny lists.

Dual-layer output buffering
~~~~~~~~~~~~~~~~~~~~~~~~~~~
Uses ByteStreamBuffer (1MB cap) + LineBuffer (1000 lines) for
memory-safe output handling with UTF-16 surrogate pair safety.

Protocol (JSON over WebSocket):
    client → server:
        {"type": "input", "data": "ls -la\r"}
        {"type": "resize", "cols": 120, "rows": 30}
    server → client:
        {"type": "output", "data": "..."}
        {"type": "exit", "code": 0}
        {"type": "error", "message": "..."}
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import subprocess
import time
from typing import Any

from fastapi import HTTPException, Request, WebSocket

from runtime.execution.arms.output_buffer import ByteStreamBuffer, LineBuffer
from runtime.execution.arms.safe_rm import SafeRmConfig, SafeRmProtector
from runtime.execution.arms.shell_state import ShellEnvState
from runtime.execution.arms.shell_state_manager import ShellStateManager
from runtime.platform.process.sliding_window_limiter import SlidingWindowLimiter
from runtime.safety.env_scrub import scrub_credential_env

_logger = logging.getLogger(__name__)

# Inbound anti-abuse ceiling on the terminal WS, mirroring the team-rooms
# handler. Terminal input is user data (typed commands / pastes), so the
# frame cap is generous — a legit paste is far under it — while still
# bounding memory, and the per-connection rate limiter sheds a runaway
# client's sustained flood. Oversized/over-rate frames are dropped before
# they reach the PTY.
_TERMINAL_WS_MAX_INBOUND_BYTES = 256 * 1024
_TERMINAL_WS_MSG_PER_SEC = 30

# ── Session store ──────────────────────────────────────────────
# PLACEHOLDER_SESSIONS

_sessions: dict[str, ShellSession] = {}

# Idle terminal sessions are kept across ws reconnects (persistent shell), but a
# client that drops and never returns would otherwise leak its shell + the
# subprocess forever. reap_sessions() drops dead shells, reaps sessions idle
# beyond _IDLE_TTL_SECONDS, and hard-caps the live count at _MAX_SESSIONS
# (least-recently-active evicted first). It runs both on each new connection AND
# on a background sweep (_reaper_loop) so an abandoned shell is freed on the TTL
# even when nobody ever opens another terminal.
_IDLE_TTL_SECONDS = 1800.0  # 30 min
_MAX_SESSIONS = 64
_REAP_SWEEP_SECONDS = 60.0  # background reaper cadence


class ShellSession:
    """One persistent shell process bound to a session_id."""

    def __init__(
        self,
        session_id: str,
        cwd: str | None = None,
        *,
        enable_state_snapshot: bool = True,
        enable_safe_rm: bool = True,
        safe_rm_config: SafeRmConfig | None = None,
        max_output_bytes: int = 1 * 1024 * 1024,
        max_output_lines: int = 1000,
    ):
        self.session_id = session_id
        # actor_id of the first authenticated client to connect · later
        # connects must present the same actor or they are refused (4403).
        # Stays None when auth is disabled (single-user local), where
        # ownership is not enforced. Bound via _bind_or_check_owner().
        self.owner_actor: str | None = None
        self.process: asyncio.subprocess.Process | None = None
        self.cwd = cwd or os.getcwd()
        self._output_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=4096)
        self._reader_task: asyncio.Task[None] | None = None
        self._alive = False
        # Monotonic timestamp of the last user input / output byte · drives the
        # idle reaper (reap_sessions) so an abandoned shell is eventually freed.
        self.last_activity = time.monotonic()

        # State snapshot mechanism
        self._state_mgr = ShellStateManager(
            enabled=enable_state_snapshot,
            shell_type="powershell" if platform.system() == "Windows" else "bash",
        )

        # Safe-rm protection
        self._safe_rm = SafeRmProtector(
            safe_rm_config or SafeRmConfig(enabled=enable_safe_rm),
        )

        # Dual-layer output buffers
        self._byte_buffer = ByteStreamBuffer(max_bytes=max_output_bytes)
        self._line_buffer = LineBuffer(max_lines=max_output_lines)

    async def start(self) -> None:
        if self.process and self.process.returncode is None:
            return
        shell = _default_shell()
        self.process = await asyncio.create_subprocess_exec(
            *shell,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=self.cwd,
            # A persistent interactive shell must not inherit the server's
            # full os.environ — any authenticated user could otherwise
            # ``echo $ANTHROPIC_API_KEY`` straight out of it. Hand it a
            # credential-scrubbed copy (same heuristics as the unconfined
            # exec path) with TERM restored on top.
            env=scrub_credential_env({"TERM": "xterm-256color"}),
            **_subprocess_platform_kwargs(),
        )
        self._alive = True
        await self._output_queue.put(
            {
                "type": "output",
                "data": _initial_terminal_text(self.cwd),
            }
        )
        self._reader_task = asyncio.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        assert self.process and self.process.stdout
        try:
            while True:
                chunk = await self.process.stdout.read(4096)
                if not chunk:
                    break
                self.last_activity = time.monotonic()
                decoded = chunk.decode("utf-8", errors="replace")

                # Update byte buffer
                self._byte_buffer.append(chunk)

                # Update line buffer
                self._line_buffer.append(decoded)

                # Parse state snapshot
                new_state = self._state_mgr.parse_new_state(decoded)
                if new_state:
                    self._state_mgr.current_state = new_state
                    _logger.debug(
                        "shell session %s state updated: cwd=%s",
                        self.session_id,
                        new_state.cwd,
                    )

                # Extract clean output (remove state markers)
                clean_data = self._state_mgr.extract_clean_output(decoded)

                # Send output to queue
                await self._output_queue.put(
                    {
                        "type": "output",
                        "data": clean_data,
                    }
                )
                if new_state:
                    await self._output_queue.put(
                        {
                            "type": "output",
                            "data": _prompt_for_cwd(new_state.cwd),
                        }
                    )

                # Report dropped data if any
                if self._byte_buffer.bytes_dropped > 0 or self._line_buffer.lines_dropped > 0:
                    _logger.debug(
                        "shell session %s output trimmed: bytes_dropped=%d, lines_dropped=%d",
                        self.session_id,
                        self._byte_buffer.bytes_dropped,
                        self._line_buffer.lines_dropped,
                    )
        except (OSError, ConnectionError, TimeoutError) as exc:
            _logger.debug("shell read error session=%s: %s", self.session_id, exc)
        finally:
            code = self.process.returncode if self.process else -1
            self._alive = False
            await self._output_queue.put({"type": "exit", "code": code})

    async def write(self, data: str) -> None:
        self.last_activity = time.monotonic()
        if self.process and self.process.stdin and self.process.returncode is None:
            # Apply safe_rm protection
            protected = self._safe_rm.wrap_command(data)

            # Apply state snapshot wrapping
            wrapped = self._state_mgr.wrap_command(protected)

            self.process.stdin.write(wrapped.encode("utf-8"))
            await self.process.stdin.drain()

    async def get_output(self, timeout: float = 0.1) -> dict[str, Any] | None:
        try:
            return await asyncio.wait_for(self._output_queue.get(), timeout=timeout)
        except TimeoutError:
            return None

    @property
    def alive(self) -> bool:
        return self._alive

    @property
    def current_state(self) -> ShellEnvState | None:
        return self._state_mgr.current_state

    @property
    def output_stats(self) -> dict[str, int]:
        return {
            "byte_buffer_size": self._byte_buffer.size,
            "byte_buffer_dropped": self._byte_buffer.bytes_dropped,
            "line_buffer_count": self._line_buffer.line_count,
            "line_buffer_dropped": self._line_buffer.lines_dropped,
        }

    @property
    def safe_rm_blocked_count(self) -> int:
        return self._safe_rm.blocked_count

    async def kill(self) -> None:
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=3.0)
            except TimeoutError:
                self.process.kill()
        self._alive = False
        if self._reader_task:
            self._reader_task.cancel()


def _default_shell() -> list[str]:
    if platform.system() == "Windows":
        return ["powershell.exe", "-NoLogo", "-NoProfile", "-Command", "-"]
    shell = os.environ.get("SHELL", "/bin/bash")
    return [shell, "--login"]


def _subprocess_platform_kwargs() -> dict[str, Any]:
    if platform.system() != "Windows":
        return {}
    create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if not create_no_window:
        return {}
    return {"creationflags": create_no_window}


def _initial_terminal_text(cwd: str) -> str:
    if platform.system() == "Windows":
        return (
            "Windows PowerShell\r\n"
            "Copyright (C) Microsoft Corporation. All rights reserved.\r\n\r\n"
            "Install the latest PowerShell for new features and improvements! "
            "https://aka.ms/PSWindows\r\n\r\n"
            f"{_prompt_for_cwd(cwd)}"
        )
    return _prompt_for_cwd(cwd)


def _prompt_for_cwd(cwd: str) -> str:
    if platform.system() == "Windows":
        return f"PS {cwd}> "
    return f"{cwd}$ "


def get_session(session_id: str, cwd: str | None = None) -> ShellSession:
    if session_id not in _sessions:
        _sessions[session_id] = ShellSession(session_id, cwd=cwd)
    return _sessions[session_id]


def _bind_or_check_owner(session: ShellSession, actor_id: str | None) -> bool:
    """Enforce per-session ownership on the terminal.

    The ``session_id`` is a client-controlled URL path segment (the
    frontend derives it from a thread id, which is enumerable and
    shareable). Without an owner check any authenticated user who knows
    another user's session_id could attach to their live shell — read its
    output and inject commands. So the first authenticated connect binds
    the session to that ``actor_id``; later connects must match.

    ``actor_id is None`` means auth is disabled (single-user local) — no
    ownership is enforced and every connect is allowed. Returns True when
    the connection may proceed, False when it must be refused.
    """
    if actor_id is None:
        return True
    if session.owner_actor is None:
        session.owner_actor = actor_id
        return True
    return session.owner_actor == actor_id


async def kill_session(session_id: str) -> None:
    session = _sessions.pop(session_id, None)
    if session:
        await session.kill()


async def reap_sessions(*, exclude_id: str | None = None) -> None:
    """Drop dead/idle terminal sessions to bound ``_sessions`` growth.

    A session is reaped if its process has exited (``alive`` is False) or it
    has been idle longer than ``_IDLE_TTL_SECONDS``. If the live count still
    exceeds ``_MAX_SESSIONS``, the least-recently-active sessions are evicted.
    ``exclude_id`` (the session being (re)connected to) is never reaped, so a
    reconnect within the idle window keeps its shell.
    """
    now = time.monotonic()
    doomed: list[str] = []
    for sid, sess in list(_sessions.items()):
        if sid == exclude_id:
            continue
        if not sess.alive or (now - sess.last_activity) > _IDLE_TTL_SECONDS:
            doomed.append(sid)
    survivors = [sess for sid, sess in _sessions.items() if sid not in doomed and sid != exclude_id]
    overflow = len(survivors) - _MAX_SESSIONS
    if overflow > 0:
        survivors.sort(key=lambda s: s.last_activity)
        doomed.extend(s.session_id for s in survivors[:overflow])
    for sid in doomed:
        await kill_session(sid)
    if doomed:
        _logger.info("terminal: reaped %d idle/dead session(s)", len(doomed))


_reaper_task: asyncio.Task[None] | None = None


async def _reaper_loop() -> None:
    """Periodically reap idle/dead shells so the _IDLE_TTL_SECONDS TTL is
    enforced even when no new terminal connection arrives to trigger it."""
    while True:
        await asyncio.sleep(_REAP_SWEEP_SECONDS)
        try:
            await reap_sessions()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — a sweep failure must not kill the loop
            _logger.warning("terminal: background reaper sweep failed", exc_info=True)


async def _start_reaper() -> None:
    global _reaper_task
    if _reaper_task is None or _reaper_task.done():
        _reaper_task = asyncio.create_task(_reaper_loop())


async def _stop_reaper() -> None:
    global _reaper_task
    task, _reaper_task = _reaper_task, None
    if task is not None and not task.done():
        from contextlib import suppress

        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


# ── FastAPI WebSocket route ────────────────────────────────────


def mount_terminal_routes(
    app: Any,
    *,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
) -> None:
    """Attach /api/terminal/ws/{session_id} to a FastAPI app.

    The WebSocket opens a persistent shell, so when ``require_auth`` is
    set (and an identity store is wired) the handshake is authenticated
    before the shell starts — an unauthenticated client is closed with
    4401 and never reaches a process.
    """
    try:
        from fastapi import Request, WebSocket, WebSocketDisconnect  # noqa: F401
    except ImportError:
        _logger.warning("fastapi not available, terminal WebSocket disabled")
        return

    # Enforce the idle TTL on a cadence, not only when a new client connects.
    app.router.add_event_handler("startup", _start_reaper)
    app.router.add_event_handler("shutdown", _stop_reaper)

    def _resolve_ws_actor(ws: WebSocket) -> str | None:
        """Authenticate a terminal WS handshake. Returns actor_id, or
        None when auth isn't required. Raises PermissionError on an
        explicit auth failure so the caller closes the socket."""
        if identity_store is None:
            if require_auth:
                raise PermissionError("identity store required for terminal auth")
            return None
        token: str | None = None
        auth_header = ""
        try:
            auth_header = ws.headers.get("authorization") or ""
        except Exception:  # noqa: BLE001
            auth_header = ""
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
        if token is None:
            try:
                subproto = ws.headers.get("sec-websocket-protocol") or ""
            except Exception:  # noqa: BLE001
                subproto = ""
            parts = [p.strip() for p in subproto.split(",") if p.strip()]
            if len(parts) >= 2 and parts[0].lower() == "bearer":
                token = parts[1]
        if not token:
            if require_auth:
                raise PermissionError("missing terminal auth token")
            return None
        if jwt_secret and token.count(".") == 2:
            identity = identity_store.verify_jwt(
                token,
                secret=jwt_secret,
                required_issuer=jwt_issuer,
                required_audience=jwt_audience,
            )
            if identity is not None:
                if require_auth and not set(identity.roles).intersection({"admin", "operator"}):
                    raise PermissionError("operator role required")
                return identity.actor_id
            if require_auth:
                raise PermissionError("invalid jwt")
        identity = identity_store.verify_api_key(token)
        if identity is not None:
            if require_auth and not set(identity.roles).intersection({"admin", "operator"}):
                raise PermissionError("operator role required")
            return identity.actor_id
        if require_auth:
            raise PermissionError("invalid token")
        return None

    def _auth_http(request: Request) -> str | None:
        from runtime.safety.auth.principal import require_operator, resolve_principal

        principal = resolve_principal(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )
        if require_auth:
            require_operator(
                request,
                identity_store,
                require_auth,
                jwt_secret=jwt_secret,
                jwt_issuer=jwt_issuer,
                jwt_audience=jwt_audience,
            )
        return principal.actor_id if principal is not None else None

    @app.post("/api/terminal/kill/{session_id}")
    async def terminal_kill(session_id: str, request: Request) -> dict[str, bool]:
        actor_id = _auth_http(request)
        # Owner check mirrors the WS path: a caller may only kill a shell
        # they own. 404 (not 403) so a probe can't confirm another actor's
        # session exists. No-op when auth is off (actor_id is None).
        session = _sessions.get(session_id)
        if (
            session is not None
            and actor_id is not None
            and session.owner_actor is not None
            and session.owner_actor != actor_id
        ):
            raise HTTPException(404, "terminal session not found")
        await kill_session(session_id)
        return {"ok": True}

    @app.websocket("/api/terminal/ws/{session_id}")
    async def terminal_ws(ws: WebSocket, session_id: str) -> None:
        try:
            actor_id = _resolve_ws_actor(ws)
        except PermissionError as exc:
            # Refuse before accept(): no shell is ever spawned. 4401
            # mirrors HTTP 401 in the WS application close-code range.
            from contextlib import suppress

            with suppress(Exception):
                await ws.close(code=4401, reason=str(exc))
            return
        await ws.accept()
        # Bound _sessions growth: free shells abandoned by clients that
        # disconnected without calling /api/terminal/kill. Never touches the
        # session we're about to (re)connect to.
        await reap_sessions(exclude_id=session_id)
        cwd = ws.query_params.get("cwd")
        session = get_session(session_id, cwd=cwd)
        # Per-session ownership: refuse a different actor attaching to a
        # shell they don't own (4403 ≈ HTTP 403). No-op when auth is off.
        if not _bind_or_check_owner(session, actor_id):
            from contextlib import suppress

            with suppress(Exception):
                await ws.close(code=4403, reason="terminal session not owned by caller")
            return
        try:
            await session.start()
        except Exception as exc:
            await ws.send_json({"type": "error", "message": str(exc)})
            await ws.close()
            return

        async def _send_output() -> None:
            while True:
                msg = await session.get_output(timeout=0.05)
                if msg:
                    try:
                        await ws.send_json(msg)
                    except (OSError, ConnectionError, TimeoutError):
                        return
                    if msg.get("type") == "exit":
                        return
                else:
                    await asyncio.sleep(0.02)

        output_task = asyncio.create_task(_send_output())
        # Per-connection inbound anti-flood guard (mirrors team_rooms_ws).
        # Local to this handler so it's freed when the connection closes.
        _inbound_limiter = SlidingWindowLimiter(
            limit=_TERMINAL_WS_MSG_PER_SEC,
            window_s=1.0,
        )
        try:
            while True:
                raw = await ws.receive_text()
                if len(raw) > _TERMINAL_WS_MAX_INBOUND_BYTES:
                    _logger.warning(
                        "terminal: dropping %d-byte inbound frame (limit %d) session=%s",
                        len(raw),
                        _TERMINAL_WS_MAX_INBOUND_BYTES,
                        session_id,
                    )
                    continue
                if not _inbound_limiter.allow(actor_id or "<anon>"):
                    _logger.debug(
                        "terminal: shedding over-rate inbound frame session=%s", session_id
                    )
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await session.write(raw)
                    continue
                if msg.get("type") == "input":
                    await session.write(msg.get("data", ""))
                elif msg.get("type") == "resize":
                    pass
        except WebSocketDisconnect:
            _logger.debug("terminal ws disconnected session=%s", session_id)
        except (ConnectionError, TimeoutError, OSError) as exc:
            _logger.debug("terminal ws error session=%s: %s", session_id, exc)
        finally:
            output_task.cancel()
