"""Thread-claim-aware event emitter used by the realtime gateway."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any

from runtime.platform.process.thread_turn_claim import (
    ThreadTurnClaim,
    ThreadTurnClaimConflict,
    ThreadTurnClaimUnavailable,
    acquire_thread_turn_claim,
)
from runtime.protocol import ServerMethod
from runtime.sensing.gateway._realtime_gateway_types import EventEmitter
from runtime.sensing.gateway._realtime_thread_delete_probe import (
    assert_thread_accepts_runtime_writes,
)
from runtime.sensing.gateway.realtime_interrupt_control import tail_contains_interrupt

# Preserve the original log channel after splitting this helper from the gateway.
_logger = logging.getLogger("runtime.sensing.gateway.realtime_gateway")

_InterruptTailer = Callable[..., tuple[bool, int]]


class BackgroundThreadWriteFenced(RuntimeError):
    """A late background projection lost thread write authority."""


class _ClaimAwareEmitter:
    """Bind the runtime-created turn id to a held thread claim.

    The wrapped emitter retains all transport/approval/interrupt semantics.
    It also tails durable cross-worker control records addressed to this
    claim's opaque epoch and projects them into the existing local interrupt
    registry. Claim metadata binding is fail-closed: without an exact epoch
    remote Stop could not be made reliable.
    """

    def __init__(
        self,
        delegate: EventEmitter,
        claim: ThreadTurnClaim,
        *,
        log: Any,
        runtime: Any = None,
        thread_access_resolver: Any = None,
        tail_interrupt: _InterruptTailer | None = None,
    ) -> None:
        self._delegate = delegate
        self._claim = claim
        self._log = log
        self._runtime = runtime
        self._thread_access_resolver = thread_access_resolver
        self._tail_interrupt = tail_interrupt or tail_contains_interrupt
        try:
            self._control_offset = log.path.stat().st_size
        except OSError:
            self._control_offset = 0
        self._control_turn_id: str | None = None
        self._control_task: asyncio.Task[None] | None = None

    @property
    def actor_id(self) -> str | None:
        return getattr(self._delegate, "actor_id", None)

    @property
    def tenant_id(self) -> str | None:
        return getattr(self._delegate, "tenant_id", None)

    async def notify(self, method: ServerMethod | str, params: dict[str, Any]) -> None:
        await self._delegate.notify(method, params)

    async def request_approval(
        self,
        method: ServerMethod | str,
        params: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> Any:
        return await self._delegate.request_approval(method, params, timeout=timeout)

    def is_turn_interrupted(self, turn_id: str) -> bool:
        return self._delegate.is_turn_interrupted(turn_id)

    def get_interrupt_reason(self, turn_id: str) -> str | None:
        return self._delegate.get_interrupt_reason(turn_id)

    def register_turn(self, turn_id: str) -> None:
        if not self._claim.bind_turn(turn_id):
            raise ThreadTurnClaimUnavailable("thread claim metadata could not bind the active turn")
        self._delegate.register_turn(turn_id)
        self._control_turn_id = turn_id
        self._control_task = asyncio.create_task(
            self._watch_persisted_interrupt(turn_id),
            name=f"turn-interrupt-watch:{turn_id}",
        )

    def unregister_turn(self, turn_id: str) -> None:
        task = self._control_task
        self._control_task = None
        self._control_turn_id = None
        if task is not None:
            task.cancel()
        self._delegate.unregister_turn(turn_id)

    @asynccontextmanager
    async def background_write_guard(self, thread_id: str):  # type: ignore[no-untyped-def]
        """Hold canonical authority across one late EventLog projection.

        A write racing the foreground turn boundary first tries an atomic
        retain, avoiding self-conflict while that turn still owns the lock. If
        foreground ownership has ended, it makes exactly one fresh claim
        attempt. A new turn or deletion that won first fences this watcher
        permanently; callers must not retry the projection later.
        """

        if thread_id != self._claim.thread_id:
            raise BackgroundThreadWriteFenced("background write targeted a different thread")

        authority: Any = self._claim.retain_if_live()
        if authority is None:
            logs_root = getattr(self._runtime, "_logs_root", None)
            if logs_root is None:
                logs_root = self._claim.path.parent.parent
            try:
                authority = acquire_thread_turn_claim(logs_root, thread_id)
            except (ThreadTurnClaimConflict, ThreadTurnClaimUnavailable) as exc:
                raise BackgroundThreadWriteFenced(
                    "background write lost canonical thread authority"
                ) from exc

        try:
            try:
                assert_thread_accepts_runtime_writes(
                    self._runtime,
                    thread_id,
                    thread_access_resolver=self._thread_access_resolver,
                )
            except Exception as exc:  # noqa: BLE001 - probe uncertainty fences the writer
                raise BackgroundThreadWriteFenced(
                    "background write failed the permanent-delete fence"
                ) from exc
            yield
        finally:
            authority.release()

    async def _watch_persisted_interrupt(self, turn_id: str) -> None:
        """Tail only bytes appended after this claim was constructed."""

        try:
            while self._control_turn_id == turn_id and not self._claim.released:
                matched, self._control_offset = self._tail_interrupt(
                    self._log,
                    self._control_offset,
                    thread_id=self._claim.thread_id,
                    turn_id=turn_id,
                    claim_epoch=self._claim.claim_epoch,
                )
                if matched:
                    self._latch_interrupt(turn_id)
                    return
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            return
        except Exception:  # noqa: BLE001 — shared control loss is fail-closed
            _logger.exception("cross-worker interrupt watcher failed for %s", turn_id)
            # A remote worker may already have returned ``interrupted: true``
            # after fsyncing its request. If this owner can no longer read
            # the shared control stream, continuing the turn would violate
            # that acknowledgement. Latch the existing local interrupt path
            # so approval waits and execution boundaries both stop safely.
            self._latch_interrupt(turn_id)

    def _latch_interrupt(self, turn_id: str) -> None:
        request_interrupt = getattr(self._delegate, "request_interrupt", None)
        if callable(request_interrupt):
            request_interrupt(turn_id)


__all__ = ["BackgroundThreadWriteFenced", "_ClaimAwareEmitter"]
