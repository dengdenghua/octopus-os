"""Connection-detached emitter for server-resident turns (audit T-01).

A long turn (minutes to hours) must survive its originating WebSocket
connection. Before this module existed, the turn ran inside the WS
request task: a disconnect cancelled the task, the connection close
injected the ``"*"`` wildcard interrupt, and the drive loop tore the
react producer down — a network hiccup, lid-close, or network switch
killed an hour of work.

``_DetachedTurnEmitter`` shields a resident turn from connection
teardown:

* ``is_turn_interrupted`` consults ONLY the gateway's shared interrupt
  registry (explicit ``turn/interrupt`` RPCs — which keep working from
  ANY connection, including a reconnected one). The connection-level
  ``"*"`` wildcard interrupt raised by a socket close is deliberately
  ignored.
* ``notify`` forwards to the owning connection while it is alive; once
  it dies, events fan out to every connection whose ``watched_threads``
  contains this thread, so event-mode reconnects and connections watching
  more than one thread pick the live stream back up. Dead sockets are
  no-ops (``RpcConnection.send`` already swallows disconnects).
* ``request_approval`` waits for a live connection (owner, then a
  reconnected watcher) until the approval budget runs out, so a user
  who reconnects can still answer a pending approval instead of the
  turn failing the moment the socket drops.

Event durability across reconnects is provided by the per-thread event
log replay (``thread/resume`` / ``thread/events``); this emitter keeps the
LIVE stream attached to whoever is watching in the same gateway process.
Cross-worker live continuation still requires a shared log tail or pub/sub
transport; the process-local connection registry cannot provide it.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from runtime.protocol import JsonRpcError, JsonRpcErrorCode, ServerMethod
from runtime.sensing.gateway._realtime_gateway_types import _ApprovalError

if TYPE_CHECKING:  # pragma: no cover - typing only (avoids import cycle)
    from runtime.sensing.gateway._realtime_gateway_connection import (
        RpcConnection,
    )


class _DetachedTurnEmitter:
    """Event sink that outlives any single WebSocket connection."""

    def __init__(
        self,
        gateway: Any,
        thread_id: str,
        owner: RpcConnection,
    ) -> None:
        self._gateway = gateway
        self._thread_id = thread_id
        self._owner = owner

    @property
    def actor_id(self) -> str | None:
        """Authenticated actor captured when the resident turn was created."""
        return getattr(self._owner, "actor_id", None)

    @property
    def tenant_id(self) -> str | None:
        """Authenticated tenant captured with ``actor_id`` for the turn."""
        return getattr(self._owner, "tenant_id", None)

    # ── targeting ──────────────────────────────────────────────

    @staticmethod
    def _is_live(conn: Any) -> bool:
        return not getattr(conn, "_closed", False)

    def _live_targets(self) -> list[Any]:
        """Owner while alive; otherwise every watcher of this thread."""
        owner = self._owner
        can_access = getattr(self._gateway, "_connection_can_access_thread", None)
        if (
            owner is not None
            and self._is_live(owner)
            and (not callable(can_access) or can_access(self._thread_id, owner))
        ):
            return [owner]
        return [
            conn
            for conn in list(getattr(self._gateway, "_connections", ()))
            if self._thread_id in getattr(conn, "watched_threads", ())
            and self._is_live(conn)
            and (not callable(can_access) or can_access(self._thread_id, conn))
        ]

    # ── EventEmitter surface ───────────────────────────────────

    async def notify(self, method: Any, params: dict[str, Any]) -> None:
        # Best-effort per target: RpcConnection bounds every send, so one
        # wedged socket can delay this projection only until that deadline.
        # Targets in the same batch run concurrently; otherwise N black-holed
        # watchers would multiply the per-connection send deadline by N.
        # Re-evaluate after each batch: when the owner times out and becomes
        # closed, deliver this same event to reconnected watchers rather than
        # waiting for the next event to switch targets.
        async def _notify_safely(target: Any) -> None:
            try:
                await target.notify(method, params)
            except Exception:  # noqa: BLE001 - projection is best-effort per socket
                return

        attempted: set[int] = set()
        while True:
            targets = [target for target in self._live_targets() if id(target) not in attempted]
            if not targets:
                return
            attempted.update(id(target) for target in targets)
            await asyncio.gather(*(_notify_safely(target) for target in targets))

    def is_turn_interrupted(self, turn_id: str) -> bool:
        # Shared registry ONLY: an explicit ``turn/interrupt`` RPC stops
        # the turn (from any connection). The connection-close wildcard
        # interrupt is intentionally NOT honored — surviving the socket
        # is the entire point of this emitter (audit T-01).
        shared = getattr(self._gateway, "_shared_interrupts", None)
        return bool(shared is not None and shared.is_interrupted(turn_id))

    def get_interrupt_reason(self, turn_id: str) -> str | None:
        if self.is_turn_interrupted(turn_id):
            return "用户停止了任务"
        return None

    def register_turn(self, turn_id: str) -> None:
        shared = getattr(self._gateway, "_shared_interrupts", None)
        if shared is not None:
            shared.register(turn_id)

    def unregister_turn(self, turn_id: str) -> None:
        shared = getattr(self._gateway, "_shared_interrupts", None)
        if shared is not None:
            shared.unregister(turn_id)

    def request_interrupt(self, turn_id: str) -> None:
        """Latch a durable cross-worker control in this worker's registry."""

        shared = getattr(self._gateway, "_shared_interrupts", None)
        if shared is not None:
            shared.request_interrupt(turn_id)

    async def request_approval(
        self,
        method: Any,
        params: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> Any:
        turn_id = params.get("turnId")
        if isinstance(turn_id, str) and self.is_turn_interrupted(turn_id):
            return {"action": "decline", "reason": "turn interrupted"}
        method_str = method.value if isinstance(method, ServerMethod) else str(method)
        fallback = getattr(self._gateway, "_approval_timeout", 60.0)
        budget = float(timeout if timeout is not None else fallback)
        deadline = time.monotonic() + budget
        while True:
            if isinstance(turn_id, str) and self.is_turn_interrupted(turn_id):
                return {"action": "decline", "reason": "turn interrupted"}
            targets = self._live_targets()
            if targets:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    return await targets[0].request_approval(method, params, timeout=remaining)
                except _ApprovalError:
                    # Real approval semantics (timeout/decline) from a
                    # live connection — surface unchanged.
                    raise
                except (Exception, asyncio.CancelledError):
                    # The connection died mid-approval (close() cancels
                    # pending futures). Give a reconnected client the
                    # remaining budget to pick the request up — unless
                    # THIS task is the one being cancelled.
                    current = asyncio.current_task()
                    if current is not None and current.cancelling() > 0:
                        raise
                await asyncio.sleep(0.25)
                continue
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(0.25)
        raise _ApprovalError(
            JsonRpcError(
                code=JsonRpcErrorCode.APPROVAL_TIMEOUT,
                message=f"timed out waiting for {method_str}",
            )
        )
