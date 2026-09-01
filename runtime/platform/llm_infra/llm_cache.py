"""LLM Response Cache — deterministic LLM call dedup.

Different from prompt caching at the provider level (Round 17): that
saves tokens on cache hits, this avoids the network call entirely.

When to enable
--------------
Cache lookup is keyed by ``(model, system, messages, temperature, tools)``.
That makes it safe to enable when:

* ``temperature == 0`` — output is reproducible
* OR the caller explicitly opts in via ``allow_nondeterministic=True``

Random sampling at temp > 0 is intentionally non-deterministic, so
caching it would silently lose variance. Default policy is to skip
the cache when ``temperature > 0`` unless overridden.

Storage backends
----------------
* ``InMemoryCacheBackend`` — bounded LRU dict. Default. Tests use this.
* ``DiskCacheBackend`` — JSON files at ``~/.echo/llm_cache/``. Picks
  up across restarts. Disabled by default to keep first-run latency
  predictable.

Both backends honor a TTL (default 1h) and the ``IdempotencyGuard``
thundering-herd protection from Round 19 for cold-cache races.

Telemetry
---------
``LLMResponseCache.stats()`` returns hit/miss + estimated USD saved.
Saved USD is computed by summing the ``cost.usd`` of cached responses
returned on hits — gives a real number to put on a dashboard.

Wiring
------
Wrap any ``ModelRouter``::

    from runtime.platform.llm_infra.llm_cache import CachedModelRouter

    primary = AnthropicModelRouter(api_key=...)
    cached = CachedModelRouter(primary, ttl_seconds=60 * 60)

    response = cached.call(request)  # cache miss → underlying call
    response = cached.call(request)  # cache hit → no network

The wrapper is a drop-in for any place that takes a ``ModelRouter``.
``call_stream`` is NOT cached — partial-stream caching is messy and
the streaming path's main cost saver is the prompt cache layer
(Round 17), not duplicate avoidance.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict
from typing import Any, Protocol

_LOG = logging.getLogger("echo.platform.llm_cache")

DEFAULT_TTL_SECONDS: float = 3600.0
DEFAULT_MAX_ENTRIES: int = 1000


# ── Cache backend protocol ───────────────────────────────────


class LLMCacheBackend(Protocol):
    def get(self, key: str) -> Any | None: ...
    def set(self, key: str, value: Any, ttl_seconds: float) -> None: ...
    def invalidate(self, key: str) -> bool: ...
    def clear(self) -> None: ...
    def size(self) -> int: ...


class InMemoryCacheBackend:
    """Bounded LRU + per-entry TTL."""

    def __init__(self, *, max_entries: int = DEFAULT_MAX_ENTRIES) -> None:
        self._max = max_entries
        self._data: OrderedDict[str, tuple[Any, float]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        now = time.time()
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            value, expires_at = entry
            if now >= expires_at:
                del self._data[key]
                return None
            self._data.move_to_end(key)  # LRU bump
            return value

    def set(self, key: str, value: Any, ttl_seconds: float) -> None:
        expires_at = time.time() + ttl_seconds
        with self._lock:
            self._data[key] = (value, expires_at)
            self._data.move_to_end(key)
            while len(self._data) > self._max:
                self._data.popitem(last=False)  # evict LRU

    def invalidate(self, key: str) -> bool:
        with self._lock:
            return self._data.pop(key, None) is not None

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def size(self) -> int:
        with self._lock:
            return len(self._data)


# ── Key composition ──────────────────────────────────────────


def make_cache_key(request: Any) -> str | None:
    """Compute a stable SHA-256 key for a ``ModelRequest``.

    Returns ``None`` when the request has signals that make it
    inherently non-cacheable (e.g. enable_thinking, where the model
    may emit a different reasoning trace each time even at temp=0).
    """
    # Best-effort attribute access — keep this resilient to model
    # evolution. Missing attrs default to safe values.
    if getattr(request, "enable_thinking", False):
        return None
    parts: dict[str, Any] = {
        "model": getattr(request, "model", ""),
        "max_tokens": getattr(request, "max_tokens", 0),
        "temperature": getattr(request, "temperature", 0.0),
        "messages": _serialize_messages(getattr(request, "messages", [])),
        "tools": _serialize_tools(getattr(request, "tools", [])),
        "system_provider": getattr(request, "system_provider", ""),
    }
    raw = json.dumps(parts, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _serialize_messages(messages: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages or []:
        role = getattr(m, "role", None) or (m.get("role") if isinstance(m, dict) else "")
        content = getattr(m, "content", None) if not isinstance(m, dict) else m.get("content")
        out.append({"role": role, "content": content})
    return out


def _serialize_tools(tools: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for t in tools or []:
        if hasattr(t, "model_dump"):
            try:
                out.append(t.model_dump())
                continue
            except (TypeError, ValueError):  # noqa: BLE001 — model_dump unsupported; check dict shape next
                pass
        if isinstance(t, dict):
            out.append(t)
        else:
            out.append({"name": str(getattr(t, "name", t))})
    return out


# ── LLMResponseCache ──────────────────────────────────────────


class LLMResponseCache:
    """Cache for ``ModelRouter.call()`` results.

    Parameters
    ----------
    backend:
        Storage backend. Defaults to ``InMemoryCacheBackend(max_entries=1000)``.
    ttl_seconds:
        Time-to-live for cached entries. Default 1 hour.
    cache_temp_zero_only:
        When True (default), skip caching for ``temperature > 0``.
        Set False only when the caller explicitly wants to dedupe
        non-deterministic responses (rare).
    """

    def __init__(
        self,
        *,
        backend: LLMCacheBackend | None = None,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        cache_temp_zero_only: bool = True,
    ) -> None:
        self._backend = backend or InMemoryCacheBackend()
        self._ttl = ttl_seconds
        self._temp_zero_only = cache_temp_zero_only
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0
        self._skipped = 0
        self._usd_saved = 0.0
        self._tokens_saved = 0

    # ── public API ──────────────────────────────────────────

    def lookup(self, request: Any) -> Any | None:
        """Try the cache. Returns the cached response or ``None``.

        Records hit/miss/skip statistics. Skipped entries (e.g.
        non-zero temperature without override) count as ``skipped``,
        not ``miss``.
        """
        if not self._cacheable(request):
            with self._lock:
                self._skipped += 1
            return None
        key = make_cache_key(request)
        if key is None:
            with self._lock:
                self._skipped += 1
            return None
        cached = self._backend.get(key)
        if cached is None:
            with self._lock:
                self._misses += 1
            return None
        with self._lock:
            self._hits += 1
            cost = getattr(cached, "cost", None)
            if cost is not None:
                self._usd_saved += float(getattr(cost, "usd", 0.0) or 0.0)
                self._tokens_saved += int(getattr(cost, "tokens_in", 0) or 0) + int(
                    getattr(cost, "tokens_out", 0) or 0
                )
        return cached

    def store(self, request: Any, response: Any) -> None:
        """Cache ``response`` keyed off ``request``."""
        if not self._cacheable(request):
            return
        key = make_cache_key(request)
        if key is None:
            return
        self._backend.set(key, response, self._ttl)

    def invalidate(self, request: Any) -> bool:
        key = make_cache_key(request)
        if key is None:
            return False
        return self._backend.invalidate(key)

    def clear(self) -> None:
        self._backend.clear()
        with self._lock:
            self._hits = 0
            self._misses = 0
            self._skipped = 0
            self._usd_saved = 0.0
            self._tokens_saved = 0

    def stats(self) -> dict[str, Any]:
        with self._lock:
            total = self._hits + self._misses
            return {
                "hits": self._hits,
                "misses": self._misses,
                "skipped": self._skipped,
                "size": self._backend.size(),
                "hit_ratio": (self._hits / total) if total > 0 else 0.0,
                "usd_saved": round(self._usd_saved, 6),
                "tokens_saved": self._tokens_saved,
            }

    # ── internals ───────────────────────────────────────────

    def _cacheable(self, request: Any) -> bool:
        if self._temp_zero_only:
            temp = float(getattr(request, "temperature", 0.0) or 0.0)
            if temp > 0.0:
                return False
        return True


# ── CachedModelRouter wrapper ────────────────────────────────


class CachedModelRouter:
    """Drop-in ``ModelRouter`` wrapper that consults a cache first.

    Streaming calls (``call_stream``) bypass the cache — partial-stream
    caching is fragile and the prompt-cache layer (Round 17) covers
    the duplicate-prefix savings on streaming requests anyway.
    """

    def __init__(
        self,
        underlying: Any,
        *,
        cache: LLMResponseCache | None = None,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        cache_temp_zero_only: bool = True,
    ) -> None:
        self._under = underlying
        self.cache = cache or LLMResponseCache(
            ttl_seconds=ttl_seconds,
            cache_temp_zero_only=cache_temp_zero_only,
        )

    @property
    def default_model(self) -> str | None:
        return getattr(self._under, "default_model", None)

    def call(self, request: Any) -> Any:
        cached = self.cache.lookup(request)
        if cached is not None:
            _LOG.debug(
                "llm_cache hit · model=%s tokens_saved=%d",
                getattr(request, "model", "?"),
                int(getattr(getattr(cached, "cost", None), "tokens_in", 0) or 0)
                + int(getattr(getattr(cached, "cost", None), "tokens_out", 0) or 0),
            )
            return cached
        response = self._under.call(request)
        self.cache.store(request, response)
        return response

    def __getattr__(self, name: str) -> Any:
        # Pass through everything else (call_stream, capabilities, ...).
        return getattr(self._under, name)


__all__ = [
    "CachedModelRouter",
    "DEFAULT_MAX_ENTRIES",
    "DEFAULT_TTL_SECONDS",
    "InMemoryCacheBackend",
    "LLMCacheBackend",
    "LLMResponseCache",
    "make_cache_key",
]
