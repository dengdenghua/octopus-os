"""Per-connection approval manager and gateway-wide interrupt registry.

Split from ``realtime_gateway.py``. ``ApprovalManager`` is bound to a
single ``RpcConnection``; ``SharedTurnInterrupts`` is shared across every
connection so an interrupt issued on a sibling tab reaches the turn
running on this one.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from runtime.protocol import JsonRpcResponse

from ._realtime_gateway_types import _ApprovalError

_logger = logging.getLogger(__name__)


class ApprovalManager:
    """Tracks server→client requests awaiting a client response.

    Bound to a single ``RpcConnection``: when the WS closes, all
    outstanding futures are cancelled. There is no shared state across
    connections, so multi-worker deployments don't deadlock.
    """

    def __init__(self) -> None:
        self._pending: dict[int | str, asyncio.Future[Any]] = {}
        self._pending_turn_ids: dict[int | str, str] = {}
        self._lock = asyncio.Lock()
        self._next_id = 1

    async def open(self, *, turn_id: str | None = None) -> tuple[int, asyncio.Future[Any]]:
        """Reserve a request id and return its pending future."""
        async with self._lock:
            req_id = self._next_id
            self._next_id += 1
            loop = asyncio.get_running_loop()
            fut: asyncio.Future[Any] = loop.create_future()
            self._pending[req_id] = fut
            if turn_id:
                self._pending_turn_ids[req_id] = turn_id
            return req_id, fut

    async def resolve(self, req_id: int | str, response: JsonRpcResponse) -> None:
        async with self._lock:
            fut = self._pending.pop(req_id, None)
            self._pending_turn_ids.pop(req_id, None)
        if fut is None or fut.done():
            return
        if response.error is not None:
            fut.set_exception(_ApprovalError(response.error))
            return
        fut.set_result(response.result)

    async def cancel_one(self, req_id: int | str, reason: str = "cancelled") -> None:
        async with self._lock:
            fut = self._pending.pop(req_id, None)
            self._pending_turn_ids.pop(req_id, None)
        if fut is not None and not fut.done():
            fut.cancel()
        _logger.debug("approval cancelled req_id=%s (%s)", req_id, reason)

    async def cancel_turn(self, turn_id: str) -> int:
        """Cancel every approval request owned by one interrupted turn."""
        async with self._lock:
            request_ids = [
                req_id
                for req_id, pending_turn_id in self._pending_turn_ids.items()
                if pending_turn_id == turn_id
            ]
            futures = [self._pending.pop(req_id, None) for req_id in request_ids]
            for req_id in request_ids:
                self._pending_turn_ids.pop(req_id, None)
        cancelled = 0
        for fut in futures:
            if fut is not None and not fut.done():
                # Resolve as an explicit decline instead of cancelling the
                # Future: asyncio.wait_for can translate inner cancellation
                # into a timeout, which incorrectly fails the whole turn.
                fut.set_result({"action": "decline", "reason": "turn interrupted"})
                cancelled += 1
        if cancelled:
            _logger.debug("approval manager cancelled %d for turn %s", cancelled, turn_id)
        return cancelled

    async def cancel_all(self, reason: str = "connection closed") -> None:
        async with self._lock:
            pending = list(self._pending.items())
            self._pending.clear()
            self._pending_turn_ids.clear()
        for _, fut in pending:
            if not fut.done():
                fut.cancel()
        if pending:
            _logger.debug("approval manager cancelled %d pending (%s)", len(pending), reason)


class SharedTurnInterrupts:
    """Gateway-wide interrupt registry, shared by every connection.

    The per-connection ``_interrupted_turns`` set only works when the
    ``turn/interrupt`` RPC arrives on the same connection that runs
    the turn. A second tab (or a post-reconnect socket) on the same
    thread is a *different* connection, so its interrupt must be
    visible to the emitter the turn was registered on. Runtimes keep
    polling ``emitter.is_turn_interrupted`` — that check consults this
    registry too. Entries are keyed by turn id, flagged only while the
    turn is known to be running, and cleared on unregister (the turn
    lifecycle's ``finally``) so ids never leak.
    """

    def __init__(self) -> None:
        self._active_turn_ids: set[str] = set()
        self._interrupted_turn_ids: set[str] = set()

    def register(self, turn_id: str) -> None:
        self._active_turn_ids.add(turn_id)
        # A stale interrupt that predates this registration must not
        # poison the new turn (mirrors RpcConnection.register_turn).
        self._interrupted_turn_ids.discard(turn_id)

    def unregister(self, turn_id: str) -> None:
        self._active_turn_ids.discard(turn_id)
        self._interrupted_turn_ids.discard(turn_id)

    def request_interrupt(self, turn_id: str) -> bool:
        """Flag ``turn_id``; True only when a running turn was hit."""
        if turn_id not in self._active_turn_ids:
            return False
        self._interrupted_turn_ids.add(turn_id)
        return True

    def is_interrupted(self, turn_id: str) -> bool:
        return turn_id in self._interrupted_turn_ids
