"""Per-WebSocket RPC connection (``RpcConnection``).

Split from ``realtime_gateway.py``. This class owns the WS socket, the
per-connection ``ApprovalManager``, the write lock, and the per-turn
interrupt registry. It is the only place that ever touches the WS object.
"""

from __future__ import annotations

import asyncio
import logging
import math
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


from runtime.protocol import (
    JsonRpcError,
    JsonRpcErrorCode,
    JsonRpcRequest,
    JsonRpcResponse,
    Notification,
    ServerMethod,
    encode_message,
)

from ._realtime_gateway_approval import ApprovalManager, SharedTurnInterrupts
from ._realtime_gateway_frame import (
    _FRAME_BYTE_LIMIT,
    _FRAME_CHAR_FASTPASS,
    _bound_oversized_frame,
)
from ._realtime_gateway_types import _APPROVAL_TIMEOUT_DEFAULT, _ApprovalError

_OUTBOUND_SEND_TIMEOUT_DEFAULT = 5.0
_logger = logging.getLogger(__name__)


class RpcConnection:
    """One client. Owns the WS, the approval manager, and a write lock.

    The write lock serializes ``websocket.send_text`` calls — Starlette
    raises if two coroutines write concurrently. The class is the only
    place that ever touches the WS object.
    """

    def __init__(
        self,
        ws: WebSocket,
        *,
        approval_timeout: float = _APPROVAL_TIMEOUT_DEFAULT,
        max_in_flight_requests: int = 32,
        shared_interrupts: SharedTurnInterrupts | None = None,
        outbound_send_timeout_seconds: float = _OUTBOUND_SEND_TIMEOUT_DEFAULT,
    ) -> None:
        self.ws = ws
        self.approval = ApprovalManager()
        self._approval_timeout = approval_timeout
        self._request_slots = asyncio.Semaphore(max(1, max_in_flight_requests))
        self._write_lock = asyncio.Lock()
        self._closed = False
        try:
            send_timeout = float(outbound_send_timeout_seconds)
        except (TypeError, ValueError):
            send_timeout = _OUTBOUND_SEND_TIMEOUT_DEFAULT
        self._outbound_send_timeout_seconds = (
            send_timeout
            if math.isfinite(send_timeout) and send_timeout > 0
            else _OUTBOUND_SEND_TIMEOUT_DEFAULT
        )
        # Authenticated actor id (None when auth is not required and no
        # credentials were presented). Set by ``RealtimeGateway._serve``
        # after the handshake gate runs. Runtime handlers consult this
        # for thread-ownership scoping.
        self.actor_id: str | None = None
        self.tenant_id: str | None = None
        # Last thread this connection successfully resumed. The gateway
        # uses it to fan terminal turn events out to sibling
        # connections watching the same thread.
        self.last_resumed_thread_id: str | None = None
        # Every thread this connection currently live-watches (resume or
        # turn start). The gateway refcounts subagent wake watchers per
        # thread and unwatches them all when this connection closes.
        self.watched_threads: set[str] = set()
        # Bound by RealtimeGateway after construction. Runtime resume/event
        # handlers invoke ``watch_thread`` after authorization but before
        # taking their response snapshot, closing the replay-to-live gap.
        self._thread_watch_handler: Callable[[str], None] | None = None
        # Per-turn interrupt flags. The runtime registers each turn id
        # before any awaitable that could be cancelled; the dispatcher
        # for ``turn/interrupt`` flips the flag; the runtime polls
        # ``is_turn_interrupted`` between steps. ``shared_interrupts``
        # is the gateway-wide registry so interrupts issued on *other*
        # connections reach turns running on this one.
        self._interrupted_turns: set[str] = set()
        self._shared_interrupts = shared_interrupts

    def bind_thread_watch_handler(self, handler: Callable[[str], None]) -> None:
        """Install the owning gateway's same-process live subscription hook."""
        self._thread_watch_handler = handler

    def watch_thread(self, thread_id: str) -> None:
        """Subscribe this connection before a resume/event snapshot is cut."""
        if self._thread_watch_handler is not None:
            self._thread_watch_handler(thread_id)

    def _mark_transport_closed(self) -> None:
        self._closed = True
        self._interrupted_turns.add("*")

    @staticmethod
    def _consume_send_task(task: asyncio.Task[None]) -> None:
        # A timed-out WebSocket implementation may take another loop tick to
        # acknowledge cancellation. Consume its eventual result so it never
        # becomes an un-retrieved task exception.
        with suppress(asyncio.CancelledError, Exception):
            task.result()

    async def _send_locked(
        self,
        message: JsonRpcRequest | JsonRpcResponse | Notification,
    ) -> None:
        async with self._write_lock:
            # Another writer may have timed out while this call waited for the
            # lock. Never enter the socket again once transport is closed.
            if self._closed:
                return
            try:
                text = encode_message(message)
                # O(1) char-count fast-path; only a rare oversized frame
                # pays the precise byte measure + shrink.
                if (
                    len(text) > _FRAME_CHAR_FASTPASS
                    and len(text.encode("utf-8")) > _FRAME_BYTE_LIMIT
                ):
                    text = encode_message(_bound_oversized_frame(message))
                await self.ws.send_text(text)
            except WebSocketDisconnect:
                # Client went away mid-stream. Flip the closed flag so
                # subsequent ``send`` calls fast-path return rather than
                # raising on every queued notify; also signal interrupt
                # for every in-flight turn so the runtime bails out
                # promptly. Swallowing here keeps the runtime's per-
                # event try/except simple — they don't have to know
                # the difference between "a single bad payload" and
                # "the connection died".
                self._mark_transport_closed()
            except RuntimeError as exc:
                # Starlette raises RuntimeError when ``send`` is called
                # after the WS lifecycle has progressed past ``connected``
                # (e.g. ``Cannot call "send" once a close message has been
                # sent``). Treat the same as a clean disconnect.
                if "close" in str(exc).lower() or "disconnect" in str(exc).lower():
                    self._mark_transport_closed()
                else:
                    raise

    async def send(self, message: JsonRpcRequest | JsonRpcResponse | Notification) -> None:
        """Bound both write-lock wait and socket send by one deadline.

        A black-holed WebSocket must not hold the reducer, tool-start audit
        receipt, or thread-turn claim forever. On timeout the connection is
        treated exactly like a disconnect and every later send becomes a
        fast no-op. Cancellation is fired at the underlying send task without
        waiting indefinitely for a non-cooperative transport to unwind.
        """
        if self._closed:
            return
        send_task = asyncio.create_task(self._send_locked(message))
        try:
            done, _pending = await asyncio.wait(
                {send_task},
                timeout=self._outbound_send_timeout_seconds,
                return_when=asyncio.ALL_COMPLETED,
            )
        except asyncio.CancelledError:
            send_task.cancel()
            send_task.add_done_callback(self._consume_send_task)
            raise
        if send_task not in done:
            self._mark_transport_closed()
            send_task.cancel()
            send_task.add_done_callback(self._consume_send_task)
            _logger.warning(
                "realtime outbound send timed out after %.3fs; connection marked closed",
                self._outbound_send_timeout_seconds,
            )
            return
        send_task.result()

    # EventEmitter
    async def notify(self, method: ServerMethod | str, params: dict[str, Any]) -> None:
        method_str = method.value if isinstance(method, ServerMethod) else method
        await self.send(Notification(method=method_str, params=params))

    # EventEmitter
    async def request_approval(
        self,
        method: ServerMethod | str,
        params: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> Any:
        method_str = method.value if isinstance(method, ServerMethod) else method
        turn_id = params.get("turnId")
        if isinstance(turn_id, str) and self.is_turn_interrupted(turn_id):
            return {"action": "decline", "reason": "turn interrupted"}
        req_id, fut = await self.approval.open(
            turn_id=turn_id if isinstance(turn_id, str) else None,
        )
        await self.send(JsonRpcRequest(id=req_id, method=method_str, params=params))
        if self._closed:
            await self.approval.cancel_one(req_id, "connection closed during send")
            raise ConnectionError("connection closed while sending approval request")
        budget = float(timeout or self._approval_timeout)
        deadline = asyncio.get_running_loop().time() + budget
        try:
            while True:
                if isinstance(turn_id, str) and self.is_turn_interrupted(turn_id):
                    await self.approval.cancel_one(req_id, "turn interrupted")
                    return {"action": "decline", "reason": "turn interrupted"}
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise TimeoutError
                try:
                    # Shield keeps each 100ms observation slice from
                    # cancelling the one approval Future.  This is what lets a
                    # cross-worker journal signal release a resident approval
                    # promptly instead of waiting for its full ten-minute
                    # budget.
                    return await asyncio.wait_for(
                        asyncio.shield(fut),
                        timeout=min(0.1, remaining),
                    )
                except TimeoutError:
                    if asyncio.get_running_loop().time() < deadline:
                        continue
                    raise
        except TimeoutError as exc:
            await self.approval.cancel_one(req_id, "timeout")
            raise _ApprovalError(
                JsonRpcError(
                    code=JsonRpcErrorCode.APPROVAL_TIMEOUT,
                    message=f"timed out waiting for {method_str}",
                )
            ) from exc
        except asyncio.CancelledError:
            await self.approval.cancel_one(req_id, "approval waiter cancelled")
            raise

    async def close(self) -> None:
        self._mark_transport_closed()
        # Treat a closing connection as an interrupt for every
        # in-flight turn. Runtime authors should bail out promptly
        # rather than try to push more state down a dead socket.
        await self.approval.cancel_all()

    # EventEmitter — interrupt registry
    def register_turn(self, turn_id: str) -> None:
        # Discarding any stale interrupt that arrived before the turn
        # was even known. Out-of-order ``turn/interrupt`` is unusual
        # but possible (client races); treat as a no-op rather than
        # leaving a poisoned flag for the next turn with the same id.
        self._interrupted_turns.discard(turn_id)
        if self._shared_interrupts is not None:
            self._shared_interrupts.register(turn_id)

    def unregister_turn(self, turn_id: str) -> None:
        self._interrupted_turns.discard(turn_id)
        if self._shared_interrupts is not None:
            self._shared_interrupts.unregister(turn_id)

    def is_turn_interrupted(self, turn_id: str) -> bool:
        if "*" in self._interrupted_turns:
            return True
        if turn_id in self._interrupted_turns:
            return True
        return self._shared_interrupts is not None and self._shared_interrupts.is_interrupted(
            turn_id
        )

    def get_interrupt_reason(self, turn_id: str) -> str | None:
        """Return the human-readable reason this turn was interrupted.

        Distinguishes connection teardown (``"*"`` wildcard) from an
        explicit ``turn/interrupt`` RPC (specific ``turn_id``) so the
        frontend can tell the user what actually happened.
        """
        if "*" in self._interrupted_turns:
            return "连接断开或后端重启"
        if turn_id in self._interrupted_turns:
            return "用户停止了任务"
        if self._shared_interrupts is not None and self._shared_interrupts.is_interrupted(turn_id):
            return "用户停止了任务"
        return None

    def request_interrupt(self, turn_id: str) -> None:
        """Called by the dispatcher when a ``turn/interrupt`` arrives."""
        self._interrupted_turns.add(turn_id)

    def requests_saturated(self) -> bool:
        return self._request_slots.locked()

    async def acquire_request_slot(self) -> asyncio.Semaphore:
        await self._request_slots.acquire()
        return self._request_slots
