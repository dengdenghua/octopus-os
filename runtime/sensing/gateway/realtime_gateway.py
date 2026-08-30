"""Realtime gateway — JSON-RPC 2.0 over WebSocket.

This is the production transport between any client and the runtime.
Replaces the SSE+POST pattern. The gateway owns:

  * One ``RpcConnection`` per WebSocket — bidirectional JSON-RPC.
  * Per-connection ``ApprovalManager`` — server-initiated requests
    (command approval, file approval, user input) are awaited via
    asyncio Futures bound to the connection. No global dict, no
    cross-worker state, no threading.Event.
  * Method dispatch — client Requests are routed to handlers registered
    on the gateway. Notifications from the client are dropped (the
    server side never expects unsolicited fire-and-forget from clients
    today; add notification handlers when that changes).

The gateway is transport-bound. The actual turn loop (planning, LLM
calls, tool dispatch) lives in implementations of ``RealtimeRuntime``.
The gateway only knows about envelopes and items.

This module is intentionally split into cohesive submodules
(``_realtime_gateway_*``) to keep it under the line budget; the public
surface (``RealtimeGateway``, ``RpcConnection``, ``ApprovalManager``,
protocols, exceptions) is re-exported here so ``from
runtime.sensing.gateway.realtime_gateway import X`` keeps working.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable as Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

try:  # Optional-dep guard: mirror sibling gateways (openai_gateway etc.)
    from fastapi import APIRouter, WebSocket, WebSocketDisconnect

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    APIRouter = None  # type: ignore[assignment,misc]
    WebSocket = None  # type: ignore[assignment,misc]

    class WebSocketDisconnect(Exception):  # type: ignore[no-redef]
        """Fallback shim so type references resolve when fastapi is absent."""

        pass


from runtime.memory.threads.event_log import EventLog, thread_log_path
from runtime.platform.models.primitives import now_utc
from runtime.platform.process.keyed_lock import KeyedLock
from runtime.platform.process.sliding_window_limiter import SlidingWindowLimiter
from runtime.platform.process.thread_turn_claim import (
    ThreadTurnClaim,
    ThreadTurnClaimConflict,
    ThreadTurnClaimUnavailable,
    acquire_thread_turn_claim,
)
from runtime.protocol import (
    ClientMethod,
    Item,
    JsonRpcErrorCode,
    ServerMethod,
    Turn,
    TurnParams,
    TurnStatus,
)
from runtime.protocol import (
    JsonRpcError as JsonRpcError,
)
from runtime.protocol import (
    JsonRpcRequest as JsonRpcRequest,
)
from runtime.protocol import (
    JsonRpcResponse as JsonRpcResponse,
)
from runtime.protocol import (
    Notification as Notification,
)
from runtime.protocol import (
    decode_message as decode_message,
)
from runtime.sensing.gateway._realtime_claim_aware_emitter import (
    _ClaimAwareEmitter as _ClaimAwareEmitterBase,
)
from runtime.sensing.gateway._realtime_detached_turn import _DetachedTurnEmitter
from runtime.sensing.gateway._realtime_gateway_approval import ApprovalManager, SharedTurnInterrupts
from runtime.sensing.gateway._realtime_gateway_connection import (
    _OUTBOUND_SEND_TIMEOUT_DEFAULT,
    RpcConnection,
)
from runtime.sensing.gateway._realtime_gateway_frame import (
    _FRAME_BYTE_LIMIT,
    _FRAME_TRUNC_MARK,
    _INBOUND_FRAME_BYTE_LIMIT,
    _INBOUND_MSG_PER_SEC,
    _bound_oversized_frame,
)
from runtime.sensing.gateway._realtime_gateway_session import (
    _RealtimeGatewaySessionMixin,
)
from runtime.sensing.gateway._realtime_gateway_types import (
    _APPROVAL_TIMEOUT_DEFAULT,
    EventEmitter,
    RealtimeRuntime,
    _RpcError,
)
from runtime.sensing.gateway._realtime_gateway_types import (
    _ApprovalError as _ApprovalError,
)
from runtime.sensing.gateway._realtime_thread_delete_probe import (
    assert_thread_accepts_runtime_writes,
)
from runtime.sensing.gateway._realtime_turn_idempotency import (
    existing_user_item_text,
    turn_for_user_item_id,
    turn_input_text,
)
from runtime.sensing.gateway.realtime_interrupt_control import (
    InterruptAuthorityUnavailable,
    InterruptTargetInactive,
    InterruptTargetNotFound,
    persist_interrupt_request,
    tail_contains_interrupt,
    thread_store_principal,
)

_logger = logging.getLogger(__name__)


class _ClaimAwareEmitter(_ClaimAwareEmitterBase):
    """Compatibility export retaining the gateway's interrupt-tail seam."""

    def __init__(
        self,
        delegate: EventEmitter,
        claim: ThreadTurnClaim,
        *,
        log: Any,
        runtime: Any = None,
        thread_access_resolver: Any = None,
    ) -> None:
        super().__init__(
            delegate,
            claim,
            log=log,
            runtime=runtime,
            thread_access_resolver=thread_access_resolver,
            tail_interrupt=lambda *args, **kwargs: tail_contains_interrupt(*args, **kwargs),
        )


# ── Gateway — FastAPI wiring + dispatch loop ─────────────────


class RealtimeGateway(_RealtimeGatewaySessionMixin):
    """Mountable FastAPI router exposing a single WebSocket endpoint.

    Usage::

        gateway = RealtimeGateway(runtime=my_runtime)
        app.include_router(gateway.router)

    Clients connect to ``GET /api/realtime`` (upgraded to WebSocket) and
    speak JSON-RPC 2.0 envelopes both directions.
    """

    def __init__(
        self,
        *,
        runtime: RealtimeRuntime,
        path: str = "/api/realtime",
        approval_timeout: float = _APPROVAL_TIMEOUT_DEFAULT,
        identity_store: Any = None,
        require_auth: bool = False,
        allow_local_workspace_access: bool = False,
        jwt_secret: str | None = None,
        jwt_issuer: str | None = None,
        jwt_audience: str | None = None,
        jwt_leeway_seconds: int = 0,
        # Claims never synthesize an identity; the subject must be registered
        # in the configured IdentityStore before a WebSocket is accepted.
        trust_jwt_sub: bool = False,
        allow_client_approval_bypass: bool = False,
        max_in_flight_requests_per_connection: int = 32,
        max_connections_per_actor: int = 64,
        max_turns_per_minute_per_actor: int = 120,
        # Per-connection inbound anti-abuse ceilings (mirror team_rooms_ws):
        # a single frame over ``max_inbound_msg_bytes`` is dropped before
        # parsing, and a sustained flood over ``max_inbound_msgs_per_sec``
        # is shed. Set either to 0 to disable. Lenient defaults — a legit
        # JSON-RPC frame is a few KB and clients send a few frames/sec.
        max_inbound_msg_bytes: int = _INBOUND_FRAME_BYTE_LIMIT,
        max_inbound_msgs_per_sec: int = _INBOUND_MSG_PER_SEC,
        # Whole outbound operation budget: waiting for another writer and
        # websocket.send_text share this deadline. A slow/black-holed client
        # is marked disconnected instead of stalling a resident turn.
        outbound_send_timeout_seconds: float = _OUTBOUND_SEND_TIMEOUT_DEFAULT,
        thread_access_resolver: Any = None,
    ) -> None:
        self._runtime = runtime
        self._approval_timeout = approval_timeout
        self._identity_store = identity_store
        self._require_auth = require_auth
        self._allow_local_workspace_access = bool(allow_local_workspace_access)
        self._jwt_secret = jwt_secret
        self._jwt_issuer = jwt_issuer
        self._jwt_audience = jwt_audience
        self._jwt_leeway_seconds = jwt_leeway_seconds
        self._trust_jwt_sub = trust_jwt_sub
        self._allow_client_approval_bypass = allow_client_approval_bypass
        self._thread_access_resolver = thread_access_resolver
        self._max_in_flight_requests_per_connection = max(
            1,
            max_in_flight_requests_per_connection,
        )
        self._max_inbound_msg_bytes = max(0, int(max_inbound_msg_bytes))
        self._max_inbound_msgs_per_sec = max(0, int(max_inbound_msgs_per_sec))
        self._outbound_send_timeout_seconds = outbound_send_timeout_seconds
        # Lenient per-actor anti-abuse ceilings (auth-on only — a local
        # single-user server with actor_id None is never limited). Sized
        # so many tabs/devices and bursty use pass freely; only a runaway
        # or hostile client trips them. Set to 0 to disable either.
        self._max_connections_per_actor = max(0, int(max_connections_per_actor))
        self._conn_counts: dict[str, int] = {}
        self._turn_rate_limiter = (
            SlidingWindowLimiter(int(max_turns_per_minute_per_actor), window_s=60.0)
            if int(max_turns_per_minute_per_actor) > 0
            else None
        )
        # Per-thread turn serialization. Reference-counted so the map is
        # reclaimed when a thread goes idle instead of leaking one lock
        # per thread_id for the process lifetime.
        self._turn_locks = KeyedLock()
        # Cross-process authority for that same invariant. Every production
        # realtime runtime persists under ``_logs_root``; embedders that do
        # not expose a log root fail closed on turn/start instead of silently
        # degrading to the process-local lock above.
        raw_logs_root = getattr(runtime, "_logs_root", None)
        self._turn_claim_logs_root = (
            Path(raw_logs_root) if isinstance(raw_logs_root, (str, Path)) else None
        )
        # Subagent wakeup auto-turn (dsh report lane): a ``wakeup`` report
        # arriving while the owning thread is idle and watched by a live
        # connection schedules a NEW parent turn that claims the parked
        # reports. Refcounts keep the store handler registered exactly as
        # long as at least one connection watches the thread.
        self._wake_watch_refs: dict[str, int] = {}
        self._active_turn_threads: set[str] = set()
        self._auto_turn_tasks: dict[str, asyncio.Task[None]] = {}
        # Audit T-01: turns run as server-resident tasks, decoupled from
        # the originating WS request task. The event loop only keeps WEAK
        # references to tasks, so a strong set is required to keep a
        # detached turn alive after its requester disconnects.
        self._resident_turn_tasks: set[asyncio.Task[Turn]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        # Cross-connection state: the shared interrupt registry (see
        # SharedTurnInterrupts) plus the live-connection set used to
        # fan terminal turn events out to same-thread watchers.
        self._shared_interrupts = SharedTurnInterrupts()
        self._connections: set[RpcConnection] = set()
        self._router = APIRouter()

        @self._router.websocket(path)
        async def _ws(ws: WebSocket) -> None:  # noqa: ANN202
            await self._serve(ws)

    def _thread_access_decision(
        self,
        thread_id: str,
        conn: RpcConnection,
    ) -> Any:
        resolver = self._thread_access_resolver
        if not callable(resolver):
            return None
        try:
            return resolver(thread_id, conn.actor_id, conn.tenant_id)
        except Exception:  # noqa: BLE001 - authorization fails closed
            from .thread_access import ThreadAccessDecision

            return ThreadAccessDecision(thread={})

    @staticmethod
    def _decision_allows(decision: Any, access: str) -> bool:
        return {
            "read": bool(getattr(decision, "can_read", False)),
            "write": bool(getattr(decision, "can_write", False)),
            "owner": bool(getattr(decision, "can_manage", False)),
        }.get(access, False)

    def _require_realtime_thread_access(
        self,
        thread_id: str,
        conn: RpcConnection,
        *,
        access: str,
    ) -> Any:
        decision = self._thread_access_decision(thread_id, conn)
        if decision is None:
            return None
        if self._decision_allows(decision, access):
            return decision
        # ThreadState is authoritative for managed shared threads. A missing
        # row retains the legacy/new-thread runtime fallback.
        if getattr(decision, "thread", None) is not None:
            raise _RpcError(
                JsonRpcErrorCode.THREAD_NOT_FOUND,
                f"unknown thread {thread_id}",
            )
        return decision

    def _connection_can_access_thread(
        self,
        thread_id: str,
        conn: RpcConnection,
        *,
        access: str = "read",
    ) -> bool:
        decision = self._thread_access_decision(thread_id, conn)
        if decision is None or getattr(decision, "thread", None) is None:
            return True
        return self._decision_allows(decision, access)

    @property
    def router(self) -> APIRouter:
        return self._router

    async def _invoke(
        self,
        method: str,
        params: dict[str, Any],
        conn: RpcConnection,
    ) -> Any:
        if method == ClientMethod.TURN_START.value:
            return await self._invoke_turn_start(params, conn)

        if method == ClientMethod.TURN_INTERRUPT.value:
            thread_id = params.get("threadId")
            turn_id = params.get("turnId")
            if "claimEpoch" in params:
                raise _RpcError(
                    JsonRpcErrorCode.INVALID_PARAMS,
                    "claimEpoch is server-owned",
                )
            if not isinstance(thread_id, str) or not thread_id:
                raise _RpcError(
                    JsonRpcErrorCode.INVALID_PARAMS,
                    "turn/interrupt requires threadId",
                )
            if not isinstance(turn_id, str) or not turn_id:
                raise _RpcError(
                    JsonRpcErrorCode.INVALID_PARAMS,
                    "turn/interrupt requires turnId",
                )
            try:
                from runtime.memory.threads.event_log import validate_thread_id

                thread_id = validate_thread_id(thread_id)
            except ValueError as exc:
                raise _RpcError(JsonRpcErrorCode.INVALID_PARAMS, str(exc)) from exc
            if self._turn_claim_logs_root is None:
                raise _RpcError(
                    JsonRpcErrorCode.INTERNAL_ERROR,
                    "authoritative interrupt control is unavailable",
                    data={
                        "reason": "interrupt_authority_unavailable",
                        "retryable": False,
                    },
                )
            interrupt_log = EventLog(thread_log_path(self._turn_claim_logs_root, thread_id))
            access = self._require_realtime_thread_access(thread_id, conn, access="write")
            try:
                authoritative_principal = thread_store_principal(
                    getattr(self._runtime, "_thread_store", None),
                    thread_id,
                )
                persist_interrupt_request(
                    logs_root=self._turn_claim_logs_root,
                    log=interrupt_log,
                    thread_id=thread_id,
                    turn_id=turn_id,
                    actor_id=conn.actor_id,
                    tenant_id=conn.tenant_id,
                    auth_required=self._require_auth,
                    authoritative_principal=authoritative_principal,
                    collaboration_access_granted=bool(
                        access is not None
                        and getattr(access, "can_write", False)
                        and not getattr(access, "can_manage", False)
                    ),
                )
            except InterruptTargetNotFound as exc:
                if not self._require_auth:
                    if not interrupt_log.snapshot().events:
                        # Compatibility for deliberately minimal local/test
                        # runtimes that expose a claim root but do not
                        # implement the event journal. This process-local
                        # fallback is never available in authenticated mode.
                        interrupted = self._shared_interrupts.request_interrupt(turn_id)
                        if interrupted:
                            conn.request_interrupt(turn_id)
                            await asyncio.gather(
                                *(
                                    connection.approval.cancel_turn(turn_id)
                                    for connection in self._connections
                                )
                            )
                        return {"turnId": turn_id, "interrupted": interrupted}
                    # Existing local journals keep their historical no-auth
                    # API shape: a stale/wrong id is an honest false ack. It
                    # never reaches the process-local registry.
                    return {"turnId": turn_id, "interrupted": False}
                # Uniform 404 prevents turn-id/thread-id probing across actors
                # and tenants in authenticated production.
                raise _RpcError(
                    JsonRpcErrorCode.THREAD_NOT_FOUND,
                    f"unknown thread {thread_id}",
                ) from exc
            except InterruptTargetInactive:
                return {"turnId": turn_id, "interrupted": False}
            except InterruptAuthorityUnavailable as exc:
                raise _RpcError(
                    JsonRpcErrorCode.INTERNAL_ERROR,
                    "authoritative interrupt control is unavailable",
                    data={
                        "reason": "interrupt_authority_unavailable",
                        "retryable": True,
                    },
                ) from exc

            # Only after the fsync succeeds may same-process fast paths fire.
            # A remote claim owner consumes the same record through its tail
            # watcher; the response therefore reports accepted even when this
            # worker's local registry has no matching turn.
            conn.request_interrupt(turn_id)
            self._shared_interrupts.request_interrupt(turn_id)
            # Approval waits are part of the turn, not independent dialogs.
            # Flag interruption before cancelling them: a racing approval
            # request then either sees the flag and declines immediately, or
            # registers early enough for cancel_turn to settle it.
            await asyncio.gather(
                *(connection.approval.cancel_turn(turn_id) for connection in self._connections)
            )
            return {"turnId": turn_id, "interrupted": True}

        # Anything else: defer to the runtime. Implementations that
        # don't override get a method-not-found.
        handler = getattr(self._runtime, "handle_request", None)
        if handler is None:
            raise _RpcError(JsonRpcErrorCode.METHOD_NOT_FOUND, f"no handler for {method}")
        try:
            result = await handler(method, params, conn)
        except NotImplementedError as exc:
            raise _RpcError(JsonRpcErrorCode.METHOD_NOT_FOUND, str(exc) or method) from exc
        if method in {
            ClientMethod.THREAD_RESUME.value,
            ClientMethod.THREAD_EVENTS.value,
        }:
            # Remember which thread this connection watches so turn
            # terminals reached on sibling connections can be fanned
            # out (see _invoke_turn_start). Cerebrum registers the live
            # watch inside the authorized handler before cutting its
            # snapshot; this post-success call is an idempotent fallback
            # for other runtimes and keeps the legacy last-resumed hint.
            resumed_thread = params.get("threadId")
            cursor_key = (
                "cursor" if method == ClientMethod.THREAD_EVENTS.value else "nextEventSequence"
            )
            result_cursor = result.get(cursor_key) if isinstance(result, dict) else None
            has_authoritative_history = (
                isinstance(result_cursor, int)
                and not isinstance(result_cursor, bool)
                and result_cursor > 0
            )
            if (
                isinstance(resumed_thread, str)
                and resumed_thread
                and (resumed_thread in conn.watched_threads or has_authoritative_history)
            ):
                conn.last_resumed_thread_id = resumed_thread
                self._watch_thread(resumed_thread, conn)
        return result

    def _acquire_thread_turn_claim(self, thread_id: str) -> ThreadTurnClaim:
        """Acquire the shared non-blocking authority for one thread turn."""

        if self._turn_claim_logs_root is None:
            raise ThreadTurnClaimUnavailable(
                "realtime runtime does not expose an authoritative logs root"
            )
        return acquire_thread_turn_claim(self._turn_claim_logs_root, thread_id)

    @staticmethod
    def _turn_claim_conflict_error(
        thread_id: str,
        conflict: ThreadTurnClaimConflict,
    ) -> _RpcError:
        data: dict[str, Any] = {
            "reason": "turn_already_active",
            "threadId": thread_id,
            "retryable": True,
        }
        if conflict.active_turn_id:
            data["activeTurnId"] = conflict.active_turn_id
        return _RpcError(
            JsonRpcErrorCode.SERVER_BUSY,
            "thread already has an active turn; use turn/steer or retry after completion",
            data=data,
        )

    @staticmethod
    def _turn_claim_unavailable_error(thread_id: str) -> _RpcError:
        return _RpcError(
            JsonRpcErrorCode.INTERNAL_ERROR,
            "authoritative thread turn lock is unavailable; refusing to start unsafely",
            data={
                "reason": "thread_turn_claim_unavailable",
                "threadId": thread_id,
                "retryable": False,
            },
        )

    async def _maybe_auto_turn(self, thread_id: str) -> None:
        """Open a new parent turn when a wakeup report finds the thread idle.

        dsh report lane: a ``wakeup`` report spends wake budget at the
        store (``maxConsecutiveWakes``); the gateway half is turning that
        wake into an actual parent turn. Guards, in order:

        * an active turn on the thread → the report is already queued
          (busy-owner ``inject``) and this is a no-op;
        * no live connection watching the thread → the report stays
          parked until the next resume surfaces it;
        * no undelivered reports left by the time the per-thread turn
          lock is held → a racing user turn already claimed them.

        The auto-turn input is a neutral stub; ``_start_turn`` surfaces
        every parked report via the steering lane (``[子代理报告] …``) and
        acks it, and the ``auto_wake`` metadata marker keeps this turn
        from refilling the consecutive-wake budget (only human input
        resets dsh ``spentWakes``).
        """
        if thread_id in self._active_turn_threads:
            return
        conn = self._watching_connection(thread_id)
        if conn is None:
            return
        try:
            from runtime.execution.subagents.sessions import (
                get_subagent_session_store,
            )

            store = get_subagent_session_store()
        except Exception:  # noqa: BLE001 — store is optional
            store = None
        if store is None or not store.pending_thread_reports(thread_id):
            return
        try:
            claim = self._acquire_thread_turn_claim(thread_id)
        except ThreadTurnClaimConflict:
            # A user turn (possibly in another worker) owns this thread. The
            # report remains durable and must not open a competing parent.
            return
        except ThreadTurnClaimUnavailable as exc:
            _logger.error(
                "subagent auto-wake refused for %s: authoritative turn claim unavailable (%s)",
                thread_id,
                exc,
            )
            with suppress(Exception):
                await conn.notify(
                    ServerMethod.ERROR,
                    {
                        "threadId": thread_id,
                        "error": {
                            "code": "thread_turn_claim_unavailable",
                            "message": "无法获得线程执行锁，已拒绝自动启动以避免重复执行",
                        },
                        "willRetry": False,
                    },
                )
            return

        try:
            try:
                assert_thread_accepts_runtime_writes(
                    self._runtime,
                    thread_id,
                    thread_access_resolver=self._thread_access_resolver,
                )
                async with self._turn_locks.hold(thread_id):
                    if thread_id in self._active_turn_threads:
                        return
                    if not store.pending_thread_reports(thread_id):
                        return
                    params = {
                        "threadId": thread_id,
                        "input": [
                            {
                                "type": "text",
                                "text": "[子代理报告]",
                                "metadata": {"context": {"auto_wake": True}},
                            }
                        ],
                    }
                    self._active_turn_threads.add(thread_id)
                    emitter = _ClaimAwareEmitter(
                        conn,
                        claim,
                        log=EventLog(thread_log_path(claim.path.parent.parent, thread_id)),
                        runtime=self._runtime,
                        thread_access_resolver=self._thread_access_resolver,
                    )
                    try:
                        turn = await self._runtime.start_turn(params, emitter)
                        self._prepare_terminal_turn_snapshot(thread_id, turn)
                    finally:
                        self._active_turn_threads.discard(thread_id)
                # Durable state is authoritative. Exit the local critical
                # section first, then release the fd before live transport.
                claim.release()
            except Exception as exc:  # noqa: BLE001 — surface, never crash the loop
                claim.release()
                _logger.warning(
                    "subagent auto-wake turn failed for thread %s: %s",
                    thread_id,
                    exc,
                )
                with suppress(Exception):
                    await conn.notify(
                        ServerMethod.ERROR,
                        {
                            "threadId": thread_id,
                            "error": {
                                "message": str(exc) or exc.__class__.__name__,
                            },
                            "willRetry": False,
                        },
                    )
                return
            await self._emit_turn_completed(conn, thread_id, turn)
        finally:
            claim.release()

    def _prepare_terminal_turn_snapshot(self, thread_id: str, turn: Turn) -> None:
        """Finalize the terminal snapshot without doing any network I/O.

        Turn drivers normally persist their durable terminal event and
        ThreadState snapshot before returning. The fallback below preserves
        fail-closed behavior for a runtime that violates that contract. Turn
        claim owners call this while still holding the OS descriptor, then
        release it before live WebSocket fan-out.
        """
        if turn.status == TurnStatus.IN_PROGRESS:
            turn.status = TurnStatus.FAILED
            turn.error = {
                "message": "runtime returned without a terminal task outcome",
                "code": "missing_terminal_state",
            }
            turn.outcome_reason = "missing_terminal_state"
            log_for = getattr(self._runtime, "_log_for", None)
            if callable(log_for):
                log_for(thread_id).turn_completed(
                    thread_id,
                    turn.id,
                    turn.status,
                    error=turn.error,
                )
        # Terminal snapshots must carry completedAt: journal replay stamps it
        # from the turn_completed event ts, so null here makes live and replay
        # views disagree.
        if turn.completed_at is None:
            turn.completed_at = now_utc()

    async def _emit_turn_completed(
        self,
        conn: RpcConnection,
        thread_id: str,
        turn: Turn,
    ) -> None:
        """Emit the terminal TURN_COMPLETED snapshot plus sibling fan-out.

        Shared by RPC-initiated and auto-woken turns so both paths keep
        the same fail-closed invariants (terminal status, completedAt,
        same-thread watcher fan-out). This method performs live network I/O;
        callers must release turn serialization before awaiting it.
        """
        with suppress(Exception):
            self._prepare_terminal_turn_snapshot(thread_id, turn)
            completed_params = {
                "threadId": thread_id,
                "turn": turn.model_dump(by_alias=True, mode="json"),
            }
            await conn.notify(ServerMethod.TURN_COMPLETED, completed_params)
            # Fan the terminal snapshot out to sibling connections that
            # resumed this thread (second tab, reconnected socket).
            # Without this they only learn the turn ended on their next
            # thread/resume and keep spinning. Best-effort: one dead
            # watcher must not starve the others or fail the caller.
            for watcher in list(self._connections):
                if watcher is conn or thread_id not in watcher.watched_threads:
                    continue
                if not self._connection_can_access_thread(thread_id, watcher):
                    continue
                with suppress(Exception):
                    await watcher.notify(ServerMethod.TURN_COMPLETED, completed_params)

    async def _invoke_turn_start(
        self,
        params: dict[str, Any],
        conn: RpcConnection,
    ) -> dict[str, Any]:
        """Run a turn to completion, streaming events on ``conn``.

        Returns the final ``Turn`` snapshot to the caller as the RPC
        result. The same turn lifecycle was already broadcast over
        notifications, so this return value is for callers that prefer
        a synchronous "wait for done" answer over watching the stream.

        Audit T-01: the turn itself runs in a server-resident task; if
        this connection drops mid-turn, the turn CONTINUES server-side
        and this RPC simply stops waiting. A reconnected client catches
        up via ``thread/resume`` (event-log replay) and keeps receiving
        live events as a watcher of the thread.
        """
        thread_id = params.get("threadId")
        if not isinstance(thread_id, str):
            raise _RpcError(
                JsonRpcErrorCode.INVALID_PARAMS,
                "turn/start requires threadId",
            )
        try:
            from runtime.memory.threads.event_log import validate_thread_id

            thread_id = validate_thread_id(thread_id)
        except ValueError as exc:
            raise _RpcError(JsonRpcErrorCode.INVALID_PARAMS, str(exc)) from exc
        access = self._require_realtime_thread_access(thread_id, conn, access="write")
        params = self._sanitize_turn_params(
            params,
            conn,
            thread_owner_actor_id=(
                getattr(access, "owner_actor_id", None) if access is not None else None
            ),
            thread_tenant_id=(getattr(access, "tenant_id", None) if access is not None else None),
        )

        # Validate the client-stable user item coordinate before claiming or
        # mutating the thread.  Once the claim is held below, an already
        # persisted ``userItemId`` becomes an idempotent read of its owning
        # turn instead of a second model execution.
        try:
            validated_params = TurnParams.model_validate(params)
        except ValueError as exc:
            raise _RpcError(
                JsonRpcErrorCode.INVALID_PARAMS,
                f"invalid turn/start params: {exc}",
            ) from exc
        user_item_id = validated_params.user_item_id

        # Claim before every stateful start action: a losing request must not
        # consume rate budget, register wake handlers, persist lifecycle rows,
        # or enter the runtime at all. The descriptor moves into the resident
        # task so requester disconnects do not release a still-running turn.
        try:
            claim = self._acquire_thread_turn_claim(thread_id)
        except ThreadTurnClaimConflict as exc:
            raise self._turn_claim_conflict_error(thread_id, exc) from None
        except ThreadTurnClaimUnavailable:
            raise self._turn_claim_unavailable_error(thread_id) from None

        try:
            from runtime.memory.threads import ThreadPermanentlyDeletedError

            try:
                assert_thread_accepts_runtime_writes(
                    self._runtime,
                    thread_id,
                    thread_access_resolver=self._thread_access_resolver,
                )
            except ThreadPermanentlyDeletedError as exc:
                raise _RpcError(
                    JsonRpcErrorCode.THREAD_NOT_FOUND,
                    f"unknown thread {thread_id}",
                ) from exc
            # Re-check under the authoritative thread claim.  Looking up first
            # and claiming second leaves a small TOCTOU window where another
            # worker can finish the same client item between those operations.
            # An in-flight duplicate receives the normal claim conflict; once
            # the owner finishes, a retry reuses its durable turn here.
            if user_item_id is not None:
                existing_turn = await asyncio.to_thread(
                    turn_for_user_item_id,
                    self._turn_claim_logs_root,
                    thread_id,
                    user_item_id,
                )
                if existing_turn is not None:
                    incoming_text = turn_input_text(validated_params)
                    existing_text = existing_user_item_text(existing_turn)
                    if existing_text is not None and incoming_text != existing_text:
                        raise _RpcError(
                            JsonRpcErrorCode.INVALID_PARAMS,
                            "userItemId was already used for different user input",
                        )
                    self._watch_thread(thread_id, conn)
                    claim.release()
                    _logger.info(
                        "realtime: idempotent turn/start replay thread_id=%s "
                        "turn_id=%s user_item_id=%s",
                        thread_id,
                        existing_turn.id,
                        user_item_id,
                    )
                    return {
                        "turn": existing_turn.model_dump(by_alias=True, mode="json"),
                    }
            # Lenient per-actor turn-rate ceiling (auth-on only). Bursts pass;
            # only a sustained flood trips SERVER_BUSY, which clients treat as
            # "back off and retry". Different threads still run concurrently —
            # this caps how fast one actor may *start* turns, not how many run.
            if (
                self._turn_rate_limiter is not None
                and conn.actor_id is not None
                and not self._turn_rate_limiter.allow(conn.actor_id)
            ):
                raise _RpcError(
                    JsonRpcErrorCode.SERVER_BUSY,
                    "rate limit: too many turns started; slow down and retry",
                )
            # A successfully claimed turn makes the thread live-watched so
            # later wakeup reports can open a parent turn after it completes.
            self._watch_thread(thread_id, conn)
            resident = asyncio.create_task(
                self._run_resident_turn(thread_id, params, conn, claim),
                name=f"resident-turn:{thread_id}",
            )
        except BaseException:
            claim.release()
            raise

        # Audit T-01: the turn runs as a SERVER-RESIDENT task, decoupled
        # from this WS request task. A disconnect cancels only this
        # handler (the shield below re-raises without touching the
        # resident); the turn keeps running, its events re-attach to
        # whoever resumes the thread, and the terminal snapshot still fans
        # out. A second turn/start now receives a deterministic conflict
        # instead of waiting behind the first with a stale user request.
        self._resident_turn_tasks.add(resident)
        resident.add_done_callback(self._resident_turn_tasks.discard)
        # Defensive ownership handoff: if the task is cancelled before its
        # coroutine body first runs, its ``finally`` cannot release the fd.
        resident.add_done_callback(lambda _task: claim.release())
        try:
            turn = await asyncio.shield(resident)
        except asyncio.CancelledError:
            # Requester went away mid-turn: the resident keeps running
            # server-side; a reconnected client catches up via
            # thread/resume replay + watcher fan-out.
            _logger.info(
                "realtime: requester disconnected; turn for thread %s continues server-side",
                thread_id,
            )
            raise
        except _RpcError:
            raise
        except Exception as exc:  # noqa: BLE001
            # The resident already surfaced ERROR to every live watcher;
            # this caller gets the JSON-RPC error response only.
            raise _RpcError(JsonRpcErrorCode.INTERNAL_ERROR, str(exc)) from exc
        return {"turn": turn.model_dump(by_alias=True, mode="json")}

    async def _run_resident_turn(
        self,
        thread_id: str,
        params: dict[str, Any],
        conn: RpcConnection,
        claim: ThreadTurnClaim,
    ) -> Turn:
        """Drive one turn to completion, decoupled from ``conn`` (T-01).

        The turn is steered through a ``_DetachedTurnEmitter`` so a
        dropped WebSocket no longer interrupts it: events flow to the
        owner while it is alive, then to connections that resumed the
        thread; only an explicit ``turn/interrupt`` stops the run.
        Terminal fan-out reuses ``_emit_turn_completed`` — ``send`` on a
        dead connection is a no-op, so the owner leg is safe either way.
        """
        emitter = _ClaimAwareEmitter(
            _DetachedTurnEmitter(self, thread_id, conn),
            claim,
            log=EventLog(thread_log_path(claim.path.parent.parent, thread_id)),
            runtime=self._runtime,
            thread_access_resolver=self._thread_access_resolver,
        )
        try:
            try:
                async with self._turn_locks.hold(thread_id):
                    self._active_turn_threads.add(thread_id)
                    try:
                        turn = await self._runtime.start_turn(params, emitter)
                        self._prepare_terminal_turn_snapshot(thread_id, turn)
                    finally:
                        self._active_turn_threads.discard(thread_id)
                # ``start_turn`` returns only after durable terminal state and
                # ThreadState projection. Live fan-out is best-effort transport
                # and must never extend either serialization lock.
                claim.release()
            except _RpcError:
                # Validation-style failures answer ONLY the RPC caller
                # (the handler converts this into the JSON-RPC error
                # response); no stream notification, matching the
                # pre-detachment behaviour.
                claim.release()
                raise
            except Exception as exc:  # noqa: BLE001
                claim.release()
                _logger.exception("realtime: turn/start crashed")
                await emitter.notify(
                    ServerMethod.ERROR,
                    {
                        "threadId": thread_id,
                        "error": {"message": str(exc) or exc.__class__.__name__},
                        "willRetry": False,
                    },
                )
                raise
            await self._emit_turn_completed(conn, thread_id, turn)
            return turn
        finally:
            claim.release()


# Type re-exports for runtime authors.
__all__ = [
    "_FRAME_BYTE_LIMIT",
    "_FRAME_TRUNC_MARK",
    "_bound_oversized_frame",
    "ApprovalManager",
    "EventEmitter",
    "Item",
    "RealtimeGateway",
    "RealtimeRuntime",
    "RpcConnection",
    "Turn",
]
