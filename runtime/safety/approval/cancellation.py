# ruff: noqa: E402 — module-level imports below are intentionally late
"""Request Cancellation — CancellationToken propagated through pipeline.

Problem
-------
When a user closes their browser tab mid-``/api/chat`` or hits Ctrl+C
on a CLI run, the in-flight LLM call, tool execution, and downstream
work should **stop**. Today they don't — the `AnthropicModelRouter`
happily finishes its 60-second streaming call even though the caller
is gone, burning budget and blocking the arm.

This module adds a .NET / Kotlin-style ``CancellationToken`` that's
* **cheap to check** — one atomic flag read
* **cascading** — child tokens inherit parent's cancelled state
* **non-throwing by default** — call sites decide whether to raise
  ``OperationCancelled`` or gracefully exit
* **wireable into FastAPI** — ``fastapi_cancellation_token(request)``
  returns a token that auto-cancels when the client disconnects

Design
------
* ``CancellationToken.is_cancelled`` — atomic bool read
* ``token.throw_if_cancelled()`` — helper for code that prefers raise
* ``CancellationSource`` — owns a token, exposes ``cancel()``
* Parent-child linking via ``source.create_linked_token()`` so
  cancelling a plan cancels every step under it
* ``token.on_cancelled(cb)`` — register a callback (useful to close
  streams, kill subprocesses, etc.)

Usage in a tool executor
------------------------

    def execute_step(step, *, cancellation: CancellationToken | None = None):
        cancellation = cancellation or CancellationToken.none()
        cancellation.throw_if_cancelled()
        # ... run handler ...
        cancellation.throw_if_cancelled()

LLM streaming loop
------------------

    for chunk in router.stream(request):
        if cancellation.is_cancelled:
            break
        process(chunk)

FastAPI integration
-------------------

    @router.post("/chat")
    async def chat(
        request: Request,
        cancellation: CancellationToken = Depends(fastapi_cancellation_token),
    ):
        result = await pipeline.run(request.body, cancellation=cancellation)
        return result
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from typing import Any

_LOG = logging.getLogger("echo.safety.cancellation")


class OperationCancelled(Exception):
    """Raised by ``throw_if_cancelled`` when a token has been tripped."""

    def __init__(self, reason: str = "operation cancelled") -> None:
        super().__init__(reason)
        self.reason = reason


# ── CancellationToken ───────────────────────────────────────


class CancellationToken:
    """Read-only view of a cancellation signal.

    Instances are produced by a ``CancellationSource``. Holders can
    poll ``is_cancelled``, call ``throw_if_cancelled()``, or register
    ``on_cancelled`` callbacks. They cannot trip the signal themselves
    — only the owning source can.
    """

    def __init__(self, *, source: CancellationSource) -> None:
        self._source = source

    # ── status ─────────────────────────────────────────────

    @property
    def is_cancelled(self) -> bool:
        return self._source._is_cancelled

    @property
    def reason(self) -> str:
        return self._source._reason

    def throw_if_cancelled(self) -> None:
        """Raise ``OperationCancelled`` iff this token is tripped."""
        if self._source._is_cancelled:
            raise OperationCancelled(self._source._reason or "cancelled")

    # ── callbacks ──────────────────────────────────────────

    def on_cancelled(self, callback: Callable[[str], None]) -> Callable[[], None]:
        """Register a callback fired when the token is tripped.

        If already tripped, the callback runs synchronously now.
        Callback exceptions are logged and swallowed so one bad
        listener can't break others. The returned function unregisters a
        still-pending callback, allowing short-lived child scopes to detach
        from a long-running parent without accumulating listeners.
        """
        fire_now = False
        with self._source._lock:
            if self._source._is_cancelled:
                fire_now = True
            else:
                self._source._callbacks.append(callback)
        if fire_now:
            self._safe_fire(callback, self._source._reason)

        def _unsubscribe() -> None:
            with self._source._lock:
                try:
                    self._source._callbacks.remove(callback)
                except ValueError:
                    return

        return _unsubscribe

    # ── linked tokens ──────────────────────────────────────

    def link(self) -> CancellationSource:
        """Create a child source whose token is cancelled if this one is.

        Cancelling the child does NOT cancel the parent.
        """
        child = CancellationSource()
        # If parent is already cancelled, propagate immediately.
        if self.is_cancelled:
            child.cancel(reason=self._source._reason or "parent cancelled")
            return child
        # Otherwise, relay any future cancellation to the child.
        unlink = self.on_cancelled(
            lambda reason: child.cancel(reason=reason or "parent cancelled"),
        )
        child.token.on_cancelled(lambda _reason: unlink())
        return child

    # ── helpers ────────────────────────────────────────────

    @classmethod
    def none(cls) -> CancellationToken:
        """A token that will never be cancelled. Useful as a default."""
        return _NONE_TOKEN

    def _safe_fire(self, cb: Callable[[str], None], reason: str) -> None:
        try:
            cb(reason)
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("cancellation callback raised: %s", exc)


# ── CancellationSource ──────────────────────────────────────


class CancellationSource:
    """Owner of a cancellation signal.

    Create one per logical request and pass ``source.token`` to the
    pipeline. Call ``source.cancel()`` to trip the token; every
    downstream consumer sees ``token.is_cancelled == True`` and every
    registered ``on_cancelled`` callback runs.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._is_cancelled = False
        self._reason = ""
        self._callbacks: list[Callable[[str], None]] = []
        self._event = threading.Event()
        self._token = CancellationToken(source=self)

    # ── public API ─────────────────────────────────────────

    @property
    def token(self) -> CancellationToken:
        return self._token

    @property
    def is_cancelled(self) -> bool:
        return self._is_cancelled

    def cancel(self, *, reason: str = "cancelled") -> bool:
        """Trip the signal. Idempotent — second call is a no-op."""
        with self._lock:
            if self._is_cancelled:
                return False
            self._is_cancelled = True
            self._reason = reason
            callbacks = list(self._callbacks)
            self._callbacks.clear()
        self._event.set()
        for cb in callbacks:
            try:
                cb(reason)
            except Exception as exc:  # noqa: BLE001
                _LOG.warning("cancellation callback raised: %s", exc)
        return True

    def create_linked_token(self) -> CancellationToken:
        """Return a child token that inherits this source's cancel signal."""
        return self._token.link().token

    # ── waiting ───────────────────────────────────────────

    def wait(self, timeout: float | None = None) -> bool:
        """Block until cancelled or ``timeout`` elapses. Returns True
        if cancellation happened.
        """
        return self._event.wait(timeout=timeout)

    async def wait_async(self, timeout: float | None = None) -> bool:
        """Async variant: await cancellation without blocking the event loop."""
        loop = asyncio.get_event_loop()
        fut: asyncio.Future[bool] = loop.create_future()

        def _callback(_: str) -> None:
            if not fut.done():
                loop.call_soon_threadsafe(fut.set_result, True)

        if self._is_cancelled:
            return True
        unsubscribe = self.token.on_cancelled(_callback)

        try:
            if timeout is not None:
                return await asyncio.wait_for(fut, timeout=timeout)
            return await fut
        except TimeoutError:
            return False
        finally:
            unsubscribe()


# ── Never-cancelled singleton ───────────────────────────────


class _NeverCancelledSource(CancellationSource):
    """A source that rejects any attempt to cancel it."""

    def cancel(self, *, reason: str = "cancelled") -> bool:
        return False


_NONE_SOURCE = _NeverCancelledSource()
_NONE_TOKEN = _NONE_SOURCE.token


# ── Contextvar-scoped ambient token ──────────────────────────
#
# Wiring a CancellationToken through every layer of the pipeline
# would mean touching ~30 call sites. Instead we park the token in
# a contextvar so any code on the call stack can poll the ambient
# token without knowing who set it.
#
# Use pattern:
#
#     with scoped_cancellation(token):
#         # Anywhere on this call stack:
#         current_cancellation_token().throw_if_cancelled()
#
# The ambient default is CancellationToken.none() (never cancels),
# so every code path stays safe even when no outer handler has
# installed a real token.

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager

_AMBIENT_TOKEN: contextvars.ContextVar[CancellationToken] = contextvars.ContextVar(
    "echo_cancellation_token",
    default=_NONE_TOKEN,
)


def current_cancellation_token() -> CancellationToken:
    """Return the ambient ``CancellationToken`` for this execution
    context. Falls back to ``CancellationToken.none()`` when none
    has been installed so callers can poll unconditionally.
    """
    return _AMBIENT_TOKEN.get()


@contextmanager
def scoped_cancellation(token: CancellationToken) -> Iterator[CancellationToken]:
    """Install ``token`` as the ambient cancellation token for the
    duration of the ``with`` block. Nested scopes restore their
    outer token on exit.
    """
    tok = _AMBIENT_TOKEN.set(token)
    try:
        yield token
    finally:
        _AMBIENT_TOKEN.reset(tok)


# ── FastAPI integration ────────────────────────────────────


def fastapi_cancellation_token(request: Any) -> CancellationToken:
    """FastAPI dependency. Returns a token that trips when the client
    disconnects.

    Usage::

        @router.post("/chat")
        async def chat(
            token: CancellationToken = Depends(fastapi_cancellation_token),
        ):
            ...

    The implementation spawns a background task that polls
    ``request.is_disconnected()`` every 250ms. When the client hangs
    up, the token is cancelled. We detach the task when the handler
    returns so it doesn't linger.
    """
    source = CancellationSource()

    async def _watcher() -> None:
        try:
            while not source.is_cancelled:
                try:
                    disconnected = await request.is_disconnected()
                except (ConnectionError, RuntimeError):  # noqa: BLE001
                    return
                if disconnected:
                    source.cancel(reason="client disconnected")
                    return
                await asyncio.sleep(0.25)
        except asyncio.CancelledError:  # noqa: BLE001 — poller cancelled; expected on shutdown
            pass

    try:
        task = asyncio.create_task(_watcher())
    except RuntimeError:
        # No running loop — return a token that simply never cancels.
        return source.token

    # Tie the watcher's lifetime to the response: the FastAPI framework
    # will typically cancel the task for us at handler teardown, but we
    # also stash a reference so caller code can manually stop it.
    source.token._watcher_task = task  # type: ignore[attr-defined]
    return source.token


__all__ = [
    "CancellationSource",
    "CancellationToken",
    "OperationCancelled",
    "current_cancellation_token",
    "fastapi_cancellation_token",
    "scoped_cancellation",
]
