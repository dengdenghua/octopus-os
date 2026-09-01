"""Gill hearts · background context preprocessor pumps.

Biological metaphor: gill hearts pump blood to the gills for oxygenation
(preprocessing) before the systemic heart pumps it to the body (main loop).
Here, up to two gill-heart pumps run in the background (each only starts if its function is provided):

  * **Compression gill** — pre-compresses long history into summaries so
    the systemic heart (main compose loop) doesn't block on truncation.
  * **Retrieval gill** — pre-fetches relevant memory trajectories from the
    journal so the compose loop doesn't block on disk reads.

The pumps are optional: if not started, ContextComposer falls back to
synchronous behavior (back-compat). When started, they periodically
refresh a cache of preprocessed segments that compose() can consume.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field

from runtime.platform.models import ContextSegment

_log = logging.getLogger("runtime.core.hearts.gill_pump")

# Default pump interval: 5 seconds. Short enough to keep the cache fresh
# for interactive use, long enough to avoid burning CPU on idle.
_DEFAULT_PUMP_INTERVAL_S: float = 5.0

# Max items in the preprocessed cache. Bounded to avoid unbounded growth
# if the compose loop is slow to consume.
_CACHE_MAX: int = 20


def retrieval_gill_enabled() -> bool:
    """Return whether retrieval caching is enabled (default: on).

    Operators can disable it immediately with ``ECHO_GILL_RETRIEVAL=0``.
    """
    return os.environ.get("ECHO_GILL_RETRIEVAL", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


@dataclass
class GillCache:
    """Thread-safe cache of preprocessed context segments.

    Written by the gill-heart pumps (background threads), read by
    ContextComposer.compose() (main loop). Lock-free reads would be
    ideal but the GIL + a copy-on-read list swap is good enough for
    the ~5s pump interval.
    """

    compressed_history: list[ContextSegment] = field(default_factory=list)
    retrieved_memory: list[ContextSegment] = field(default_factory=list)
    last_compressed_ts: float = 0.0
    last_retrieved_ts: float = 0.0
    retrieved_context_key: str | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def get_compressed(self) -> list[ContextSegment]:
        with self._lock:
            return list(self.compressed_history)

    def get_memory(
        self,
        context_key: str | None = None,
        *,
        max_age_s: float | None = None,
    ) -> list[ContextSegment]:
        with self._lock:
            if context_key is not None and context_key != self.retrieved_context_key:
                return []
            if max_age_s is not None and (
                self.last_retrieved_ts <= 0 or (time.time() - self.last_retrieved_ts) >= max_age_s
            ):
                return []
            return list(self.retrieved_memory)

    def set_compressed(self, segments: list[ContextSegment]) -> None:
        with self._lock:
            self.compressed_history = segments[:_CACHE_MAX]
            self.last_compressed_ts = time.time()

    def set_memory(
        self,
        segments: list[ContextSegment],
        context_key: str | None = None,
    ) -> None:
        with self._lock:
            self.retrieved_memory = segments[:_CACHE_MAX]
            self.retrieved_context_key = context_key
            self.last_retrieved_ts = time.time()

    def is_fresh(self, max_age_s: float = 30.0) -> bool:
        """True if both caches have been refreshed within ``max_age_s``."""
        now = time.time()
        return (
            self.last_compressed_ts > 0
            and self.last_retrieved_ts > 0
            and (now - self.last_compressed_ts) < max_age_s
            and (now - self.last_retrieved_ts) < max_age_s
        )


class GillHeartPump:
    """Background pump that preprocesses hemolymph (context) for the
    systemic heart (main compose loop).

    Up to two pumps run as daemon threads (each conditional on its function being non-None):
      1. Compression gill — calls ``compress_fn()`` periodically to
         pre-compress history segments.
      2. Retrieval gill — calls ``retrieve_fn()`` periodically to
         pre-fetch memory segments.

    Both write results into a shared ``GillCache``. ContextComposer
    reads from the cache instead of doing synchronous work.

    Usage::

        cache = GillCache()
        pump = GillHeartPump(
            cache=cache,
            compress_fn=lambda: my_compression_logic(),
            retrieve_fn=lambda: my_retrieval_logic(),
        )
        pump.start()
        # ... compose() reads cache.get_compressed() / cache.get_memory()
        pump.stop()
    """

    def __init__(
        self,
        *,
        cache: GillCache,
        compress_fn: Callable[[], list[ContextSegment]] | None = None,
        retrieve_fn: Callable[[], list[ContextSegment]] | None = None,
        retrieve_context_key: str | Callable[[], str] | None = None,
        interval_s: float = _DEFAULT_PUMP_INTERVAL_S,
    ) -> None:
        self.cache = cache
        self._compress_fn = compress_fn
        self._retrieve_fn = retrieve_fn
        self._retrieve_context_key = retrieve_context_key
        self._interval_s = interval_s
        self._stop_event = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        if self._threads:
            return  # already running
        self._stop_event.clear()
        if self._compress_fn is not None:
            t = threading.Thread(
                target=self._pump_loop,
                args=(self._compress_fn, self.cache.set_compressed, "compression"),
                daemon=True,
                name="gill-heart-compression",
            )
            t.start()
            self._threads.append(t)
        if self._retrieve_fn is not None:
            t = threading.Thread(
                target=self._retrieval_loop,
                daemon=True,
                name="gill-heart-retrieval",
            )
            t.start()
            self._threads.append(t)
        _log.info(
            "gill hearts started (interval=%ss, pumps=%d)",
            self._interval_s,
            len(self._threads),
        )

    def stop(self, timeout: float = 3.0) -> None:
        self._stop_event.set()
        for t in self._threads:
            t.join(timeout=timeout)
        self._threads.clear()
        _log.info("gill hearts stopped")

    @property
    def is_running(self) -> bool:
        return bool(self._threads) and not self._stop_event.is_set()

    def _pump_loop(
        self,
        work_fn: Callable[[], list[ContextSegment]],
        cache_fn: Callable[[list[ContextSegment]], None],
        label: str,
    ) -> None:
        while not self._stop_event.is_set():
            try:
                segments = work_fn() or []
                cache_fn(segments)
            except Exception:
                _log.debug("gill heart %s pump error", label, exc_info=True)
            # Wait for interval or stop signal, whichever comes first.
            self._stop_event.wait(timeout=self._interval_s)

    def _retrieval_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                assert self._retrieve_fn is not None
                segments = self._retrieve_fn() or []
                key_source = self._retrieve_context_key
                context_key = key_source() if callable(key_source) else key_source
                self.cache.set_memory(segments, context_key=context_key)
            except Exception:
                _log.debug("gill heart retrieval pump error", exc_info=True)
            self._stop_event.wait(timeout=self._interval_s)


# Late import to avoid circular dependency at module load.
from collections.abc import Callable  # noqa: E402

__all__ = ["GillCache", "GillHeartPump", "retrieval_gill_enabled"]
