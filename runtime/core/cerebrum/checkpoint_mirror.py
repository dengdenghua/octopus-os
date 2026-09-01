"""Distributed checkpoint mirror — P3 fourth slice.

The local journal carries every ``react_checkpoint`` event for a task.
That's enough for "kill the process and resume on the same machine"
durability. It is NOT enough for cross-machine handoff: if the host
running the task dies, no other process can read the local journal.

This module mirrors checkpoints to a shared key-value sink (typically
Redis) so any worker pulling from the same sink can:

* List task ids with outstanding (non-final) checkpoints anywhere in
  the cluster.
* Pull the latest payload for a task and resume it.

The store is duck-typed — anything providing ``get(k)`` / ``set(k, v)``
/ ``keys(pattern)`` works. Tests inject a tiny in-memory dict store;
production wires a real Redis client. We don't take a hard dependency
on the ``redis`` package so the module is importable even when redis
isn't installed.

Resilience
----------

The mirror runs against a network. Network = transient failures. We
add two cushioning layers:

* **Bounded retry** — ``put`` / ``get`` retry once after a brief sleep
  before giving up. Caps total attempts at 2 so a flaky redis can't
  stall an entire turn.
* **Circuit breaker** — once N consecutive operations fail, suppress
  further attempts for COOLDOWN seconds. The local journal stays the
  source of truth while the breaker is open. Auto-recovers by
  returning to normal state on cooldown expiry.

Both layers fail-soft: the loop never sees an exception escape the
mirror.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

_LOG = logging.getLogger("echo.cerebrum.checkpoint_mirror")

CHECKPOINT_KEY_PREFIX = "echo:react_checkpoint:"
TASKS_INDEX_KEY = "echo:react_checkpoint:tasks"

# Retry + circuit breaker tunables. Defaults chosen to keep latency
# bounded on a flaky redis: at most 2 attempts × ~50 ms sleep = 50 ms
# per worst-case failed call; once 3 consecutive ops fail we go quiet
# for 30 s and let the local journal carry the load.
_DEFAULT_RETRY_ATTEMPTS = 2
_DEFAULT_RETRY_SLEEP_S = 0.05
_DEFAULT_BREAKER_THRESHOLD = 3
_DEFAULT_BREAKER_COOLDOWN_S = 30.0


def _key_for(task_id: str) -> str:
    return f"{CHECKPOINT_KEY_PREFIX}{task_id}"


class _CircuitBreaker:
    """Tiny state machine: closed → open → half-open.

    * ``closed`` (normal): every call goes through; consecutive
      failures increment ``_failures``. When that hits the threshold
      we trip to ``open``.
    * ``open``: all calls short-circuit until ``cooldown`` seconds
      have elapsed since the trip; first call after that probes
      (half-open semantics) and either resets to closed (success)
      or re-opens with a fresh cooldown (failure).
    """

    def __init__(
        self,
        *,
        threshold: int = _DEFAULT_BREAKER_THRESHOLD,
        cooldown_s: float = _DEFAULT_BREAKER_COOLDOWN_S,
        clock: Any = time.monotonic,
    ) -> None:
        self._threshold = max(1, threshold)
        self._cooldown_s = max(0.0, cooldown_s)
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None

    def is_open(self) -> bool:
        if self._opened_at is None:
            return False
        # Cooldown elapsed → return False (let caller probe).
        return (self._clock() - self._opened_at) < self._cooldown_s

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self._threshold:
            self._opened_at = self._clock()


class CheckpointMirror:
    """Best-effort distributed mirror of latest checkpoint per task.

    Wraps a duck-typed KV store. The store must support:

    * ``set(key: str, value: str) -> None``
    * ``get(key: str) -> str | bytes | None``
    * ``sadd(key: str, *members: str) -> int`` (set membership)
    * ``srem(key: str, *members: str) -> int``
    * ``smembers(key: str) -> Iterable[str | bytes]``
    * ``delete(*keys: str) -> int``

    Real ``redis.Redis`` clients have all of these. The in-memory test
    double in ``tests/test_checkpoint_mirror.py`` implements the same
    surface.
    """

    def __init__(
        self,
        client: Any,
        *,
        retry_attempts: int = _DEFAULT_RETRY_ATTEMPTS,
        retry_sleep_s: float = _DEFAULT_RETRY_SLEEP_S,
        breaker_threshold: int = _DEFAULT_BREAKER_THRESHOLD,
        breaker_cooldown_s: float = _DEFAULT_BREAKER_COOLDOWN_S,
        sleep_fn: Any = time.sleep,
    ) -> None:
        self._client = client
        self._retry_attempts = max(1, retry_attempts)
        self._retry_sleep_s = max(0.0, retry_sleep_s)
        self._sleep_fn = sleep_fn
        self._breaker = _CircuitBreaker(
            threshold=breaker_threshold,
            cooldown_s=breaker_cooldown_s,
        )

    def _call_with_retry(self, op_name: str, fn: Any) -> tuple[bool, Any]:
        """Run ``fn()`` through retry + circuit breaker.

        Returns ``(success, result)``. On breaker-open or final
        failure, ``success=False`` and ``result=None``.
        """
        if self._breaker.is_open():
            _LOG.debug("mirror %s skipped — breaker open", op_name)
            return (False, None)
        last_exc: Exception | None = None
        for attempt in range(1, self._retry_attempts + 1):
            try:
                result = fn()
                self._breaker.record_success()
                return (True, result)
            except Exception as exc:  # noqa: BLE001 — fail-soft
                last_exc = exc
                if attempt < self._retry_attempts and self._retry_sleep_s > 0:
                    with _suppress_exception():
                        self._sleep_fn(self._retry_sleep_s)
        self._breaker.record_failure()
        _LOG.warning(
            "mirror %s failed after %d attempts: %s",
            op_name,
            self._retry_attempts,
            last_exc,
        )
        return (False, None)

    @staticmethod
    def _decode(raw: Any) -> str:
        if isinstance(raw, bytes):
            try:
                return raw.decode("utf-8")
            except UnicodeDecodeError:
                return raw.decode("utf-8", errors="replace")
        return raw if isinstance(raw, str) else str(raw)

    def put(self, task_id: str, checkpoint: dict[str, Any]) -> bool:
        """Mirror ``checkpoint`` for ``task_id``. Returns True on success.

        Errors are caught and logged; a False return tells the caller
        the mirror didn't accept the write but the loop should keep
        going (the local journal is the source of truth).
        """
        if not task_id:
            return False
        try:
            payload = json.dumps(
                checkpoint,
                ensure_ascii=False,
                default=str,
            )
        except Exception as exc:  # noqa: BLE001 — never raise from mirror
            _LOG.debug("checkpoint serialize failed: %s", exc)
            return False

        def _do_put() -> None:
            self._client.set(_key_for(task_id), payload)
            self._client.sadd(TASKS_INDEX_KEY, task_id)
            # If this checkpoint is the final answer, the task is done —
            # remove from the index so list_tasks() doesn't surface it.
            if checkpoint.get("has_final_answer"):
                with _suppress_exception():
                    self._client.srem(TASKS_INDEX_KEY, task_id)

        ok, _ = self._call_with_retry(f"put({task_id})", _do_put)
        return ok

    def get(self, task_id: str) -> dict[str, Any] | None:
        """Return the latest mirrored checkpoint for ``task_id``, or None."""
        if not task_id:
            return None
        ok, raw = self._call_with_retry(
            f"get({task_id})",
            lambda: self._client.get(_key_for(task_id)),
        )
        if not ok or raw is None:
            return None
        try:
            obj = json.loads(self._decode(raw))
        except (json.JSONDecodeError, UnicodeError) as exc:
            _LOG.warning("checkpoint mirror parse failed for %s: %s", task_id, exc)
            return None
        return obj if isinstance(obj, dict) else None

    def list_tasks(self) -> list[str]:
        """Return all task ids with mirrored non-final checkpoints."""
        ok, members = self._call_with_retry(
            "list_tasks",
            lambda: self._client.smembers(TASKS_INDEX_KEY) or [],
        )
        if not ok:
            return []
        out = []
        for m in members or []:
            decoded = self._decode(m)
            if decoded:
                out.append(decoded)
        out.sort()
        return out

    def forget(self, task_id: str) -> bool:
        """Remove a task's mirrored payload + index entry. Useful when a
        finished task should be reaped explicitly even before its final-
        answer ``put`` lands."""
        if not task_id:
            return False

        def _do_forget() -> None:
            self._client.delete(_key_for(task_id))
            self._client.srem(TASKS_INDEX_KEY, task_id)

        ok, _ = self._call_with_retry(f"forget({task_id})", _do_forget)
        return ok


class _SuppressException:
    """Internal helper: ``with _suppress_exception(): ...`` swallows any
    exception. Used so srem() failures inside put() don't poison the
    primary set+sadd we already succeeded at."""

    def __enter__(self) -> _SuppressException:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        return True


def _suppress_exception() -> _SuppressException:
    return _SuppressException()


def build_checkpoint_mirror_from_url(url: str) -> CheckpointMirror | None:
    """Build a CheckpointMirror backed by a real Redis client at ``url``.

    Returns None when the redis package isn't available or the URL is
    blank — the caller should treat that as "mirroring off". Network
    connectivity isn't probed here; the first put/get will surface any
    real connection error.
    """
    if not url or not url.strip():
        return None
    try:
        import redis  # type: ignore[import-untyped]
    except ImportError:
        _LOG.info("redis package unavailable; checkpoint mirror disabled")
        return None
    try:
        client = redis.Redis.from_url(url)
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("redis client build failed for %s: %s", url, exc)
        return None
    return CheckpointMirror(client)


__all__ = [
    "CHECKPOINT_KEY_PREFIX",
    "CheckpointMirror",
    "TASKS_INDEX_KEY",
    "build_checkpoint_mirror_from_url",
]
