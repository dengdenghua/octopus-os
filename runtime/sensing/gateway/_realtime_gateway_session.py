"""Realtime WebSocket session boundary for :mod:`realtime_gateway`.

This behavior-preserving mixin owns connection authentication/admission,
frame dispatch, per-connection thread watches, and client turn-parameter
sanitisation.  Durable turn execution, claims, interruption, and terminal
fan-out intentionally remain on ``RealtimeGateway``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import suppress
from typing import Any

try:  # Optional-dep guard: mirror sibling gateways (openai_gateway etc.)
    from fastapi import WebSocket, WebSocketDisconnect

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    WebSocket = None  # type: ignore[assignment,misc]

    class WebSocketDisconnect(Exception):  # type: ignore[no-redef]
        """Fallback shim so type references resolve when fastapi is absent."""

        pass


from runtime.platform.process.sliding_window_limiter import SlidingWindowLimiter
from runtime.protocol import (
    JsonRpcError,
    JsonRpcErrorCode,
    JsonRpcRequest,
    JsonRpcResponse,
    Notification,
    decode_message,
)
from runtime.safety.auth.principal import LEGACY_SESSION_COOKIE_NAME, SESSION_COOKIE_NAME
from runtime.safety.auth.websocket import accepted_auth_subprotocol, websocket_bearer_token
from runtime.sensing.gateway._realtime_gateway_approval import SharedTurnInterrupts
from runtime.sensing.gateway._realtime_gateway_connection import RpcConnection
from runtime.sensing.gateway._realtime_gateway_types import _ApprovalError, _RpcError

# Keep split code on the original log channel so dashboards and filters do not
# change merely because the implementation moved to a private module.
_logger = logging.getLogger("runtime.sensing.gateway.realtime_gateway")


class _RealtimeGatewaySessionMixin:
    """Connection/session implementation inherited by ``RealtimeGateway``."""

    # State is initialized by ``RealtimeGateway.__init__``. Declarations keep
    # the private mixin independently type-checkable without creating a
    # reverse import (and therefore a cycle) back to the concrete gateway.
    _identity_store: Any
    _require_auth: bool
    _allow_local_workspace_access: bool
    _jwt_secret: str | None
    _jwt_issuer: str | None
    _jwt_audience: str | None
    _jwt_leeway_seconds: int
    _trust_jwt_sub: bool
    _max_connections_per_actor: int
    _conn_counts: dict[str, int]
    _approval_timeout: float
    _max_in_flight_requests_per_connection: int
    _shared_interrupts: SharedTurnInterrupts
    _outbound_send_timeout_seconds: float
    _connections: set[RpcConnection]
    _max_inbound_msgs_per_sec: int
    _max_inbound_msg_bytes: int
    _loop: asyncio.AbstractEventLoop | None
    _wake_watch_refs: dict[str, int]
    _auto_turn_tasks: dict[str, asyncio.Task[None]]
    _allow_client_approval_bypass: bool

    async def _invoke(
        self,
        method: str,
        params: dict[str, Any],
        conn: RpcConnection,
    ) -> Any:
        """Dispatch hook implemented by the concrete gateway."""
        raise NotImplementedError

    async def _maybe_auto_turn(self, thread_id: str) -> None:
        """Auto-turn hook implemented by the concrete gateway."""
        raise NotImplementedError

    def _resolve_ws_actor(self, ws: WebSocket) -> str | None:
        """Authenticate a WebSocket handshake before ``accept()``.

        Mirrors ``_resolve_actor`` (openai_gateway) but for WS.
        Token sources, in order of preference:
          1. ``Authorization: Bearer <token>`` header (some proxies pass it)
          2. ``Sec-WebSocket-Protocol`` subprotocol value (browser-safe,
             base64url-encoded by current clients)
          3. The durable ``HttpOnly`` browser session cookie

        Query-string credentials are intentionally unsupported because URLs
        are routinely retained in browser history and proxy/access logs.

        Returns ``actor_id`` on success, ``None`` when ``require_auth`` is
        false and no credentials were presented. Raises ``_RpcError`` on
        explicit auth failure so the caller can close with 4401.
        """
        if self._identity_store is None:
            if self._require_auth:
                raise _RpcError(
                    JsonRpcErrorCode.UNAUTHORIZED,
                    "identity store required for realtime auth",
                )
            return None

        token: str | None = None
        try:
            auth_header = ws.headers.get("authorization") or ""
        except Exception:  # noqa: BLE001
            auth_header = ""
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()

        if token is None:
            token = websocket_bearer_token(ws)

        if token is None:
            try:
                token = str(
                    ws.cookies.get(SESSION_COOKIE_NAME)
                    or ws.cookies.get(LEGACY_SESSION_COOKIE_NAME)
                    or ""
                ).strip()
            except Exception:  # noqa: BLE001
                token = None

        if not token:
            if self._require_auth:
                raise _RpcError(
                    JsonRpcErrorCode.UNAUTHORIZED,
                    "missing realtime auth token",
                )
            return None

        if self._jwt_secret and token.count(".") == 2:
            identity = self._identity_store.verify_jwt(
                token,
                secret=self._jwt_secret,
                leeway_seconds=self._jwt_leeway_seconds,
                required_issuer=self._jwt_issuer,
                required_audience=self._jwt_audience,
                trust_jwt_sub=self._trust_jwt_sub,
            )
            if identity is not None:
                return identity.actor_id
            if self._require_auth:
                raise _RpcError(JsonRpcErrorCode.UNAUTHORIZED, "invalid jwt")

        identity = self._identity_store.verify_api_key(token)
        if identity is not None:
            return identity.actor_id
        if self._require_auth:
            raise _RpcError(JsonRpcErrorCode.UNAUTHORIZED, "invalid token")
        return None

    @staticmethod
    def _accept_subprotocol(ws: WebSocket) -> str | None:
        """Pick the subprotocol to acknowledge in ``accept()``.

        Browser clients that authenticate via ``Sec-WebSocket-Protocol``
        offer a marker plus a token (parsed by ``_resolve_ws_actor``). RFC
        6455 requires the server to select one of the offered protocols.
        Only the non-secret marker is echoed; the token value itself is
        never selected. Clients that use header or cookie auth get the old
        behavior: no subprotocol.
        """
        return accepted_auth_subprotocol(ws)

    def _admit_connection(self, actor_id: str | None) -> bool:
        """Reserve a connection slot for ``actor_id`` under the per-actor
        cap. Returns False when the actor is already at the cap. A no-op
        (always True) when auth is off (actor_id None) or the cap is 0."""
        if actor_id is None or self._max_connections_per_actor <= 0:
            return True
        count = self._conn_counts.get(actor_id, 0)
        if count >= self._max_connections_per_actor:
            return False
        self._conn_counts[actor_id] = count + 1
        return True

    def _release_connection(self, actor_id: str | None) -> None:
        """Return a slot reserved by _admit_connection; drop the key at 0
        so the counter map stays O(actors with a live connection)."""
        if actor_id is None or self._max_connections_per_actor <= 0:
            return
        count = self._conn_counts.get(actor_id, 0) - 1
        if count <= 0:
            self._conn_counts.pop(actor_id, None)
        else:
            self._conn_counts[actor_id] = count

    async def _serve(self, ws: WebSocket) -> None:
        try:
            actor_id = self._resolve_ws_actor(ws)
        except _RpcError as exc:
            # Refuse the handshake. 4401 mirrors the HTTP 401 semantic
            # in WS close-code space (the 4000–4999 range is for app use).
            with suppress(Exception):
                await ws.close(code=4401, reason=exc.message)
            return
        # Per-actor connection cap (4429 ≈ HTTP 429). Checked before
        # accept so an over-limit actor never spawns connection state.
        if not self._admit_connection(actor_id):
            with suppress(Exception):
                await ws.close(code=4429, reason="too many connections for this actor")
            return
        await ws.accept(subprotocol=self._accept_subprotocol(ws))
        conn = RpcConnection(
            ws,
            approval_timeout=self._approval_timeout,
            max_in_flight_requests=self._max_in_flight_requests_per_connection,
            shared_interrupts=self._shared_interrupts,
            outbound_send_timeout_seconds=self._outbound_send_timeout_seconds,
        )
        conn.bind_thread_watch_handler(lambda thread_id: self._watch_thread(thread_id, conn))
        conn.actor_id = actor_id
        if actor_id is not None and self._identity_store is not None:
            identity = self._identity_store.get(actor_id)
            metadata = getattr(identity, "metadata", None) or {}
            conn.tenant_id = str(metadata.get("tenant_id") or f"legacy:{actor_id}")
        self._connections.add(conn)
        # Each inbound client Request becomes a background task so the
        # receive loop stays free to deliver the corresponding Responses
        # for any server-initiated approval requests the handler may
        # await. Without this, awaiting an approval future from inside
        # ``_handle_payload`` blocks the only coroutine that could
        # ever resolve it — classic deadlock.
        in_flight: set[asyncio.Task[None]] = set()
        # Per-connection inbound guard, local to this handler so it's freed
        # when the connection closes — no shared map to leak. Over-sized
        # frames are dropped before decode; a runaway client's sustained
        # flood is shed without parsing. Mirrors ``team_rooms_ws``.
        _inbound_limiter = (
            SlidingWindowLimiter(limit=self._max_inbound_msgs_per_sec, window_s=1.0)
            if self._max_inbound_msgs_per_sec > 0
            else None
        )
        try:
            while True:
                try:
                    payload = await ws.receive_text()
                except WebSocketDisconnect:
                    break
                if self._max_inbound_msg_bytes > 0 and len(payload) > self._max_inbound_msg_bytes:
                    _logger.warning(
                        "realtime: dropping %d-byte inbound frame (limit %d)",
                        len(payload),
                        self._max_inbound_msg_bytes,
                    )
                    continue
                if _inbound_limiter is not None and not _inbound_limiter.allow(
                    actor_id or "<anon>"
                ):
                    _logger.debug("realtime: shedding over-rate inbound frame")
                    continue
                task = asyncio.create_task(self._handle_payload(conn, payload))
                in_flight.add(task)
                task.add_done_callback(in_flight.discard)
        finally:
            self._connections.discard(conn)
            for watched in list(getattr(conn, "watched_threads", ())):
                self._unwatch_thread(watched)
            self._release_connection(actor_id)
            for task in list(in_flight):
                task.cancel()
            await conn.close()

    async def _handle_payload(self, conn: RpcConnection, payload: str) -> None:
        try:
            message = decode_message(payload)
        except ValueError as exc:
            _logger.warning("realtime: malformed envelope: %s", exc)
            return  # Notification-style malformed input: drop. Per JSON-RPC
            # we *should* reply with PARSE_ERROR for ambiguous cases, but
            # without a recoverable id, the spec-compliant id is null and
            # most clients ignore it anyway. Logging is enough.

        if isinstance(message, JsonRpcResponse):
            await conn.approval.resolve(message.id, message)
            return

        if isinstance(message, Notification):
            # ``ping`` is a client-side keepalive — reply with ``pong``
            # so the client can detect a wedged or black-holed server
            # connection (silent TCP half-open, proxy timeout, etc).
            if message.method == "ping":
                with suppress(Exception):
                    await conn.notify("pong", {})
                return
            _logger.debug("realtime: dropping client notification %s", message.method)
            return

        # JsonRpcRequest — dispatch and reply.
        if conn.requests_saturated():
            await conn.send(
                JsonRpcResponse(
                    id=message.id,
                    error=JsonRpcError(
                        code=JsonRpcErrorCode.SERVER_BUSY,
                        message="too many in-flight realtime requests",
                    ),
                ),
            )
            return
        slot = await conn.acquire_request_slot()
        try:
            await self._dispatch_request(conn, message)
        finally:
            slot.release()

    async def _dispatch_request(self, conn: RpcConnection, request: JsonRpcRequest) -> None:
        try:
            result = await self._invoke(request.method, request.params, conn)
        except _ApprovalError as exc:
            await conn.send(JsonRpcResponse(id=request.id, error=exc.error))
            return
        except _RpcError as exc:
            await conn.send(
                JsonRpcResponse(
                    id=request.id,
                    error=JsonRpcError(code=exc.code, message=exc.message, data=exc.data),
                )
            )
            return
        except Exception as exc:  # noqa: BLE001
            _logger.exception("realtime: handler raised for %s", request.method)
            await conn.send(
                JsonRpcResponse(
                    id=request.id,
                    error=JsonRpcError(
                        code=JsonRpcErrorCode.INTERNAL_ERROR,
                        message=str(exc) or exc.__class__.__name__,
                    ),
                )
            )
            return
        await conn.send(JsonRpcResponse(id=request.id, result=result))

    # ── Subagent wakeup auto-turn (dsh report lane) ─────────────────

    def _capture_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None:
            self._loop = asyncio.get_running_loop()
        return self._loop

    def _watch_thread(self, thread_id: str, conn: RpcConnection) -> None:
        """Register the auto-wake watcher for a thread this connection watches.

        Idempotent per connection; refcounted across connections so the
        store handler lives exactly as long as a live watcher exists.
        Best-effort: a missing subagent store never raises.
        """
        if not thread_id:
            return
        if thread_id in conn.watched_threads:
            return
        conn.watched_threads.add(thread_id)
        refs = self._wake_watch_refs.get(thread_id, 0)
        if refs == 0:
            try:
                from runtime.execution.subagents.sessions import (
                    get_subagent_session_store,
                )

                store = get_subagent_session_store()
                if store is not None:
                    store.register_thread_wake_handler(
                        thread_id,
                        self._make_wake_handler(thread_id),
                    )
            except Exception:  # noqa: BLE001 — watcher registration is best-effort
                _logger.debug("subagent wake watcher register failed", exc_info=True)
        self._wake_watch_refs[thread_id] = refs + 1

    def _unwatch_thread(self, thread_id: str) -> None:
        """Drop one connection's watch; unregister the store handler at zero."""
        if not thread_id:
            return
        refs = self._wake_watch_refs.get(thread_id, 0)
        if refs <= 1:
            self._wake_watch_refs.pop(thread_id, None)
            try:
                from runtime.execution.subagents.sessions import (
                    get_subagent_session_store,
                )

                store = get_subagent_session_store()
                if store is not None:
                    store.unregister_thread_wake_handler(thread_id)
            except Exception:  # noqa: BLE001 — best-effort
                _logger.debug("subagent wake watcher unregister failed", exc_info=True)
        else:
            self._wake_watch_refs[thread_id] = refs - 1

    def _make_wake_handler(self, thread_id: str) -> Callable[[str, Any], None]:
        """Build the store wake handler hopping onto this gateway's loop.

        ``append_report`` invokes the handler synchronously on the
        reporting (worker) thread; the handler only schedules the
        auto-turn coroutine on the event loop and returns immediately.
        """
        loop = self._capture_loop()

        def _wake(session_id: str, report: Any) -> None:
            try:
                loop.call_soon_threadsafe(self._schedule_auto_turn, thread_id)
            except RuntimeError:
                _logger.debug(
                    "subagent wake scheduling skipped (event loop closed)",
                    exc_info=True,
                )

        return _wake

    def _schedule_auto_turn(self, thread_id: str) -> None:
        """Dedupe rapid wakeups: one pending task claims every parked report."""
        if thread_id in self._auto_turn_tasks:
            return
        task = asyncio.create_task(self._maybe_auto_turn(thread_id))
        self._auto_turn_tasks[thread_id] = task
        task.add_done_callback(lambda _t: self._auto_turn_tasks.pop(thread_id, None))

    def _watching_connection(self, thread_id: str) -> RpcConnection | None:
        for conn in list(self._connections):
            if thread_id in conn.watched_threads and not getattr(conn, "_closed", False):
                return conn
        return None

    def _sanitize_turn_params(
        self,
        params: dict[str, Any],
        conn: RpcConnection,
        *,
        thread_owner_actor_id: str | None = None,
        thread_tenant_id: str | None = None,
    ) -> dict[str, Any]:
        cleaned = dict(params)
        # Provider UIs use `off`/`none` to mean "do not request reasoning".
        # The public TurnParams protocol accepts only concrete effort tiers;
        # older clients and retried drafts must therefore omit the top-level
        # field instead of failing the whole task before it reaches the model.
        # The same preference may remain in metadata.context for provider-
        # specific routing, where it is normalized independently.
        if str(cleaned.get("effort") or "").strip().casefold() in {
            "off",
            "none",
            "disabled",
        }:
            cleaned.pop("effort", None)
        if cleaned.get("approvalPolicy") == "never" and not self._allow_client_approval_bypass:
            cleaned["approvalPolicy"] = "on-request"
        if conn.actor_id is not None:
            tenant_id = thread_tenant_id or conn.tenant_id or f"legacy:{conn.actor_id}"
            owner_actor_id = thread_owner_actor_id or conn.actor_id
            from runtime.sensing.gateway.thread_workspace import (
                PROTECTED_WORKSPACE_METADATA_KEYS,
            )

            path_keys = {
                *PROTECTED_WORKSPACE_METADATA_KEYS,
                "workspacePath",
                "extraWorkspaces",
                "personalWorkspacePath",
                "allowedWritePaths",
                "attachmentReadRoots",
                "artifactOutputRoot",
            }
            local_workspace_keys = {
                "workspace_path",
                "workspacePath",
                "personal_workspace_path",
                "personalWorkspacePath",
                "extra_workspaces",
                "extraWorkspaces",
            }

            def _authenticated_metadata(raw: Any) -> dict[str, Any]:
                metadata_dict = dict(raw) if isinstance(raw, dict) else {}
                for key in path_keys:
                    if self._allow_local_workspace_access and key in local_workspace_keys:
                        continue
                    metadata_dict.pop(key, None)
                metadata_dict.pop("actorId", None)
                raw_context = metadata_dict.get("context")
                context = dict(raw_context) if isinstance(raw_context, dict) else {}
                for key in path_keys:
                    if self._allow_local_workspace_access and key in local_workspace_keys:
                        continue
                    context.pop(key, None)
                context.pop("actorId", None)
                context["actor_id"] = conn.actor_id
                context["owner_actor_id"] = owner_actor_id
                context["tenant_id"] = tenant_id
                metadata_dict["context"] = context
                metadata_dict["actor_id"] = conn.actor_id
                metadata_dict["owner_actor_id"] = owner_actor_id
                metadata_dict["tenant_id"] = tenant_id
                return metadata_dict

            # Top-level cwd and every metadata-carried filesystem grant are
            # client input. Shared authenticated turns resolve their root from
            # the server-owned thread allocation later in intent construction;
            # loopback-local auth keeps only the user-selected workspace keys.
            if not self._allow_local_workspace_access:
                cleaned.pop("cwd", None)
            metadata_dict = _authenticated_metadata(cleaned.get("metadata"))
            cleaned["metadata"] = metadata_dict
            blocks = cleaned.get("input")
            input_blocks = list(blocks) if isinstance(blocks, list) else []
            if not input_blocks:
                input_blocks.append({"type": "metadata"})
            sanitized_blocks: list[Any] = []
            for index, raw_block in enumerate(input_blocks):
                block = dict(raw_block) if isinstance(raw_block, dict) else {"type": "metadata"}
                if index == 0 or isinstance(block.get("metadata"), dict):
                    block["metadata"] = _authenticated_metadata(block.get("metadata"))
                sanitized_blocks.append(block)
            input_blocks = sanitized_blocks
            cleaned["input"] = input_blocks
            # Server-injected ownership context. The client never chooses
            # these values; the gateway overwrites them after authentication.
            cleaned["tenant_id"] = tenant_id
            cleaned["owner_actor_id"] = owner_actor_id
        else:
            # These Pydantic fields are transport-internal. Anonymous/local
            # callers retain explicit cwd/context compatibility, but cannot
            # opt themselves into an authenticated principal by sending the
            # hidden ownership field names directly.
            cleaned.pop("tenant_id", None)
            cleaned.pop("owner_actor_id", None)
        return cleaned
