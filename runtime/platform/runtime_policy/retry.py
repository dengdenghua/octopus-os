"""Retry Policy — exponential backoff + jitter decorator.

A small, dependency-free retry primitive for use on LLM calls, tool
invocations, HTTP requests, and any other flaky operation. The goal
is: retry what's transient, fail fast on what's permanent, and never
sleep longer than the caller agreed to.

Design
------
* **Exponential backoff with jitter** — ``delay = base * 2**attempt``,
  multiplied by a uniform ``[1-jitter, 1]`` factor (AWS full-jitter
  style). Clipped to ``max_delay``. First attempt is instant (no
  pre-sleep), so single-shot success is zero overhead.
* **Retryable exception whitelist** — callers explicitly list the
  exception classes that count as "try again". Everything else
  propagates immediately. Default is ``(Exception,)`` only when the
  caller opts into "retry anything".
* **HTTP status classifier** — ``is_retryable_http_status(code)``
  flags the usual transient codes (408, 429, 500, 502, 503, 504).
* **Decorator and functional form** — ``@retry(...)`` on any callable,
  or ``retry_call(fn, ...)`` when the policy is dynamic.
* **Sleep hook** — ``sleep`` is injectable so tests don't actually
  sleep. Production callers use ``time.sleep``; tests swap in
  ``lambda s: None``.

Usage
-----

    from runtime.platform.runtime_policy.retry import retry, RetryPolicy

    @retry(
        on=(ConnectionError, TimeoutError),
        attempts=3,
        base_delay=0.5,
    )
    def fetch(url: str) -> bytes: ...

    # Functional form for dynamic policies:
    result = retry_call(
        fn=lambda: http.get("/x"),
        policy=RetryPolicy(on=(ConnectionError,), attempts=5),
    )

Retry vs. circuit breaker
-------------------------
Retry is local to one callsite; the existing ``CircuitBreaker`` is
global for a provider. A well-wired system uses both: retry absorbs
transient blips inside one call, the breaker trips when retries
themselves stop helping.
"""

from __future__ import annotations

import contextlib
import functools
import logging
import random
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, TypeVar

_LOG = logging.getLogger("echo.platform.retry")

T = TypeVar("T")


# ── HTTP status helpers ────────────────────────────────────────

# Transient HTTP codes suitable for automatic retry. 429 (Too Many
# Requests) is included — callers that want to respect Retry-After
# should read the response header and override the policy's
# ``retry_after_fn`` rather than excluding 429.
RETRYABLE_HTTP_STATUSES: frozenset[int] = frozenset({408, 429, 500, 502, 503, 504})


def is_retryable_http_status(code: int | None) -> bool:
    """Return True if ``code`` is in the default transient set."""
    if code is None:
        return False
    return int(code) in RETRYABLE_HTTP_STATUSES


# ── Policy ─────────────────────────────────────────────────────


@dataclass
class RetryPolicy:
    """Configuration for a single retry cycle.

    Attributes
    ----------
    on:
        Tuple of exception classes that count as retryable.
    attempts:
        Maximum attempts including the first. ``attempts=1`` disables
        retry entirely (runs once, no backoff).
    base_delay:
        Initial backoff in seconds. The actual delay for attempt N
        (0-indexed) is ``base_delay * 2**N``, clipped to ``max_delay``.
    max_delay:
        Upper bound on a single sleep. Prevents a long chain from
        waiting 10+ minutes after many failures.
    jitter:
        Multiplier drawn from ``[1-jitter, 1]``. ``0.25`` ≈ AWS full-
        jitter spirit: 75-100% of the theoretical delay.
    retry_if:
        Optional callable ``(exc) -> bool`` that can override the
        ``on`` whitelist for dynamic decisions (e.g. reading a
        ``Retry-After`` header from an HTTPError).
    """

    on: tuple[type[BaseException], ...] = ()
    attempts: int = 3
    base_delay: float = 0.5
    max_delay: float = 30.0
    jitter: float = 0.25
    retry_if: Callable[[BaseException], bool] | None = None

    def compute_delay(self, attempt_index: int) -> float:
        """Return the pre-sleep delay for the given attempt index.

        ``attempt_index`` is 0 for the first retry (after the first
        failed attempt), 1 for the second retry, etc. The initial
        attempt never calls this.
        """
        raw = self.base_delay * (2**attempt_index)
        raw = min(raw, self.max_delay)
        if self.jitter > 0:
            factor = 1.0 - random.random() * self.jitter
            raw *= factor
        return max(0.0, raw)

    def should_retry(self, exc: BaseException) -> bool:
        """True if ``exc`` is retryable under this policy."""
        if self.retry_if is not None:
            try:
                return bool(self.retry_if(exc))
            except (TypeError, ValueError, AttributeError):  # noqa: BLE001
                return False
        if not self.on:
            return False
        return isinstance(exc, self.on)


# ── Functional + decorator form ────────────────────────────────


def retry_call(
    fn: Callable[..., T],
    *args: Any,
    policy: RetryPolicy,
    sleep: Callable[[float], None] = time.sleep,
    on_retry: Callable[[int, BaseException, float], None] | None = None,
    **kwargs: Any,
) -> T:
    """Invoke ``fn(*args, **kwargs)`` with retries driven by ``policy``.

    Parameters
    ----------
    fn:
        The callable to execute. Any return value is passed through.
    policy:
        A ``RetryPolicy`` instance.
    sleep:
        Injectable sleep for tests. Defaults to ``time.sleep``.
    on_retry:
        Optional callback ``(attempt_index, exception, delay)`` fired
        BEFORE each sleep so observability / metrics can record the
        retry. ``attempt_index`` is 0 for the first retry.

    Raises
    ------
    The last-seen exception if all attempts fail.
    """
    if policy.attempts < 1:
        raise ValueError("policy.attempts must be >= 1")

    last_exc: BaseException | None = None
    for i in range(policy.attempts):
        try:
            return fn(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001
            last_exc = exc
            is_last = i >= policy.attempts - 1
            if is_last or not policy.should_retry(exc):
                raise
            delay = policy.compute_delay(i)
            if on_retry is not None:
                # observability must never break retry
                with contextlib.suppress(Exception):  # noqa: BLE001
                    on_retry(i, exc, delay)
            _LOG.debug(
                "retry attempt=%d/%d exc=%s sleep=%.3fs",
                i + 1,
                policy.attempts,
                type(exc).__name__,
                delay,
            )
            if delay > 0:
                sleep(delay)
    # Unreachable — the for-loop either returns or re-raises. This
    # exists only for type-checkers.
    assert last_exc is not None
    raise last_exc


def retry(
    *,
    on: type[BaseException] | Iterable[type[BaseException]] = (),
    attempts: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 30.0,
    jitter: float = 0.25,
    retry_if: Callable[[BaseException], bool] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    on_retry: Callable[[int, BaseException, float], None] | None = None,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator form of ``retry_call``.

    Example::

        @retry(on=(ConnectionError,), attempts=5, base_delay=0.2)
        def fetch(url): ...
    """
    if isinstance(on, type):
        on_tuple: tuple[type[BaseException], ...] = (on,)
    else:
        on_tuple = tuple(on)
    policy = RetryPolicy(
        on=on_tuple,
        attempts=attempts,
        base_delay=base_delay,
        max_delay=max_delay,
        jitter=jitter,
        retry_if=retry_if,
    )

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            return retry_call(
                fn,
                *args,
                policy=policy,
                sleep=sleep,
                on_retry=on_retry,
                **kwargs,
            )

        return wrapper

    return decorator


__all__ = [
    "RETRYABLE_HTTP_STATUSES",
    "RetryPolicy",
    "is_retryable_http_status",
    "retry",
    "retry_call",
]
