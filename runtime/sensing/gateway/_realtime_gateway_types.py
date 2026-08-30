"""Shared types, protocols, exceptions and constants for the realtime gateway.

Split from ``realtime_gateway.py`` so the production transport stays under
the module line budget. This module holds the pure type surface — no
framing, no connection, no dispatch logic — that the other gateway
submodules and the gateway itself depend on.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from runtime.protocol import JsonRpcError, ServerMethod, Turn

# Default approval wait. 10 minutes is enough for the operator to
# notice and respond; tune via the RealtimeGateway constructor for
# environments with stricter SLAs.
_APPROVAL_TIMEOUT_DEFAULT = 600.0


class EventEmitter(Protocol):
    """Sink the runtime uses to push events out to a client.

    A ``RpcConnection`` implements this. Implementations must be
    coroutine-safe — one turn loop may interleave deltas from multiple
    items, and asyncio task scheduling can reorder otherwise atomic
    sequences.
    """

    async def notify(self, method: ServerMethod | str, params: dict[str, Any]) -> None: ...

    async def request_approval(
        self,
        method: ServerMethod | str,
        params: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> Any: ...

    def is_turn_interrupted(self, turn_id: str) -> bool:
        """Cooperative cancel signal.

        Runtime authors poll this between long-running steps. Returns
        ``True`` once the client has issued ``turn/interrupt`` for the
        given ``turn_id`` (or once the connection is closing).
        """
        ...

    def get_interrupt_reason(self, turn_id: str) -> str | None:
        """Return the human-readable reason this turn was interrupted."""
        ...

    def register_turn(self, turn_id: str) -> None:
        """Tell the connection a new turn has begun.

        The gateway routes any ``turn/interrupt`` RPC for this turn id
        to this connection's interrupt registry. Runtime authors call
        this immediately after constructing the Turn but before the
        first await that could be interrupted.
        """
        ...

    def unregister_turn(self, turn_id: str) -> None: ...


class RealtimeRuntime(Protocol):
    """The contract turn loops implement to plug into the gateway.

    Implementations supply the actual agent logic. The gateway only
    invokes ``start_turn``; everything else (interruption, steering,
    listing) is dispatched by ``handle_request`` if implemented.
    """

    async def start_turn(self, params: dict[str, Any], emitter: EventEmitter) -> Turn: ...

    async def handle_request(
        self,
        method: str,
        params: dict[str, Any],
        emitter: EventEmitter,
    ) -> Any:
        """Dispatch any non-``turn/start`` client method.

        Defaults to method-not-found. Override to add ``thread/list``,
        ``turn/interrupt``, etc.
        """
        ...


RequestHandler = Callable[[dict[str, Any]], Awaitable[Any]]


class _ApprovalError(Exception):
    def __init__(self, error: JsonRpcError) -> None:
        super().__init__(error.message)
        self.error = error


class _RpcError(Exception):
    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data
