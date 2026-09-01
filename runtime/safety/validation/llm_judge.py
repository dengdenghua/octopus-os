"""
Production LLM judge wiring · bridges ``constitution.judge`` to
the runtime's ``ModelRouter`` abstraction.

``judge.py`` deliberately doesn't import ``ModelRouter`` · it
defines the interface. This module does the binding · so tests
of the judge parser stay free of LLM dependencies, and
deployments can wire any router (Anthropic / OpenAI / Oct /
mock) without modifying the judge layer.

Features
--------

* **TTL cache** · avoid paying for the same outbound twice in
  a row (chatty agents re-send near-duplicates during tool
  loops). Cache key is ``(sha256(message), destination)`` ·
  default TTL 60 s.
* **Budget safety** · 5 s request timeout · 200-token cap on
  the reply · judge prompt truncates message at 4000 chars.
* **Fail-open** · any provider error (network / rate-limit /
  parse) degrades to ``allow`` via ``build_judge_from_llm_fn``'s
  exception handling. Regex gate stays the hard floor.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .judge import (
    JUDGE_PROMPT,
    Judge,
    JudgeVerdict,
    build_judge_from_llm_fn,
)

_log = logging.getLogger("runtime.safety.validation.llm_judge")


if TYPE_CHECKING:  # pragma: no cover
    from runtime.platform.models.llm import ModelRouter


# ═══════════════════════════════════════════════════════════
# TTL cache · keyed by (message_hash, destination)
# ═══════════════════════════════════════════════════════════


@dataclass
class _CacheEntry:
    verdict: JudgeVerdict
    expires_at: float


class _TTLCache:
    """Tiny thread-safe TTL cache · no eviction sweep · stale
    entries cleared on access. Max size capped so a pathological
    attacker can't balloon memory with unique messages."""

    def __init__(self, ttl_s: float = 60.0, max_size: int = 1024) -> None:
        self._ttl = ttl_s
        self._max = max_size
        self._data: dict[str, _CacheEntry] = {}
        self._lock = threading.Lock()

    def _key(self, message: str, destination: str) -> str:
        return hashlib.sha256(
            (destination + "\x00" + message).encode("utf-8", "replace"),
        ).hexdigest()

    def get(self, message: str, destination: str) -> JudgeVerdict | None:
        k = self._key(message, destination)
        now = time.monotonic()
        with self._lock:
            entry = self._data.get(k)
            if entry is None:
                return None
            if entry.expires_at < now:
                # lazy eviction
                del self._data[k]
                return None
            return entry.verdict

    def put(
        self,
        message: str,
        destination: str,
        verdict: JudgeVerdict,
    ) -> None:
        k = self._key(message, destination)
        expires = time.monotonic() + self._ttl
        with self._lock:
            if len(self._data) >= self._max:
                # Crude overflow · drop an arbitrary entry. A real
                # LRU would be nicer but this is belt-and-braces
                # against pathological growth · the common case is
                # hundreds of entries max.
                self._data.pop(next(iter(self._data)))
            self._data[k] = _CacheEntry(verdict=verdict, expires_at=expires)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


# ═══════════════════════════════════════════════════════════
# Public builder
# ═══════════════════════════════════════════════════════════


def build_judge_from_router(
    router: ModelRouter,
    *,
    model: str = "claude-haiku-4-5-20251001",
    system_provider: str = "anthropic",
    max_tokens: int = 200,
    temperature: float = 0.0,
    cache_ttl_s: float = 60.0,
    cache_max_size: int = 1024,
    prompt_template: str = JUDGE_PROMPT,
) -> Judge:
    """Build a judge that calls ``router.call()`` per outbound
    message (modulo cache hits).

    Parameters
    ----------
    router :
        Any ``runtime.sensing.model_router.ModelRouter`` subclass ·
        Mock / Anthropic / OpenAI / Oct all work.
    model :
        Model string the router understands. Default Haiku ·
        judge wants low latency · not deep reasoning.
    system_provider :
        Routing hint · usually matches the ``router`` class.
    max_tokens / temperature :
        Judge replies are short · keep tokens tight · temp zero
        for determinism (stable cache hits).
    cache_ttl_s / cache_max_size :
        TTL cache knobs. Set ``cache_ttl_s=0`` to disable.
    prompt_template :
        Override the default ``JUDGE_PROMPT`` if you want to
        retune the rubric.
    """
    from runtime.platform.models.llm import Message, ModelRequest

    cache = _TTLCache(ttl_s=cache_ttl_s, max_size=cache_max_size)

    def _llm_call(prompt: str) -> str:
        req = ModelRequest(
            model=model,
            messages=[Message(role="user", content=prompt)],
            max_tokens=max_tokens,
            temperature=temperature,
            system_provider=system_provider,
        )
        resp = router.call(req)
        return resp.text or ""

    parsed_judge = build_judge_from_llm_fn(
        _llm_call,
        prompt_template=prompt_template,
    )

    def _cached_judge(
        message: str,
        destination: str,
        session: Any,
    ) -> JudgeVerdict:
        if cache_ttl_s > 0:
            hit = cache.get(message, destination)
            if hit is not None:
                return hit
        try:
            verdict = parsed_judge(message, destination, session)
        except Exception as exc:  # noqa: BLE001
            # Defense-in-depth · parsed_judge already catches LLM
            # errors · this catches anything else (e.g. Message /
            # ModelRequest construction failures).
            _log.warning(
                "llm_judge unexpected error %s: %s · defaulting allow",
                type(exc).__name__,
                exc,
            )
            return JudgeVerdict(action="allow", reason="judge_unavailable")
        if cache_ttl_s > 0:
            cache.put(message, destination, verdict)
        return verdict

    # Attach cache for tests / ops access (e.g. clear on profile
    # change · inspect hit rate). Not part of the Judge Callable
    # contract · extra attribute on the function object.
    _cached_judge._cache = cache  # type: ignore[attr-defined]
    return _cached_judge


__all__ = ["build_judge_from_router"]
