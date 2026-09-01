from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from runtime.platform.process.service_provider import get_provider

_LOG = logging.getLogger("echo.reflex.tiers")


@dataclass
class TierResult:
    """One tier's verdict · ``reply is None`` means miss."""

    tier_name: str
    reply: str | None = None
    latency_ms: float = 0.0
    detail: str = ""  # e.g. "match_score=0.82" or "via slm"


class ReplyTier(ABC):
    """Abstract tier · subclass + implement ``try_reply``."""

    name: str = "abstract"
    enabled: bool = True

    @abstractmethod
    def try_reply(
        self,
        *,
        prompt: str,
        actor: str | None = None,
    ) -> TierResult: ...

    def describe(self) -> dict[str, Any]:
        """Sanitised view for ops UI."""
        return {"name": self.name, "enabled": self.enabled}


class FuzzyCacheTier(ReplyTier):
    name = "fuzzy_cache"

    def __init__(
        self,
        *,
        similarity: float = 0.7,
        max_entries: int = 500,
        ttl_hours: float = 168.0,  # 1 week
    ) -> None:
        self.similarity = similarity
        self.max_entries = max_entries
        self.ttl_seconds = ttl_hours * 3600
        self._store: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._lock = threading.RLock()
        self.hits = 0
        self.misses = 0

    @staticmethod
    def _bigrams(text: str) -> set[str]:
        s = text.strip().lower()
        if len(s) < 2:
            return {s} if s else set()
        return {s[i : i + 2] for i in range(len(s) - 1)}

    @staticmethod
    def _jaccard(a: set[str], b: set[str]) -> float:
        if not a and not b:
            return 1.0
        union = len(a | b)
        return (len(a & b) / union) if union else 0.0

    def remember(self, prompt: str, reply: str) -> None:
        """Store a (prompt, reply) pair · called from outside the
        tier when an LLM round-trip just produced a fresh answer
        that future similar prompts could reuse.

        Refuses to cache prompts that are:
          * too long (>150 chars) · long questions are almost always
            fresh research / detailed tasks where cache reuse produces
            stale or wrong answers
          * contain research / search / realtime keywords · the answer
            depends on when it was asked (markets, news, live data)
          * prefixed with slash commands · those trigger specific
            actions, not conversational replies

        Prior to this guard a single "call_agent failed" degraded
        reply for a research query could live in the cache for a
        week and be re-served to every similar research query.
        """
        if not isinstance(prompt, str) or not prompt.strip():
            return
        if not isinstance(reply, str) or not reply.strip():
            return
        stripped = prompt.strip()
        # 1. Length guard · conversations keep short turns, research
        #    queries run long · 150 char is a comfortable split.
        if len(stripped) > 150:
            return
        # 2. Keyword guard · block volatile / research domains whose
        #    "correct" answer depends on when / with what context the
        #    question was asked.
        lower = stripped.lower()
        no_cache_keywords = (
            "调研",
            "研究",
            "报告",
            "搜索",
            "查询",
            "最新",
            "今日",
            "昨日",
            "昨天",
            "今天",
            "现在",
            "实时",
            "市场",
            "research",
            "report",
            "search",
            "latest",
            "news",
            "market",
            "today",
            "yesterday",
            "realtime",
            "live",
        )
        if any(kw in lower for kw in no_cache_keywords):
            return
        # 3. Slash commands are instructions, not conversation.
        if stripped.startswith("/"):
            return
        # 4. Error / failure replies shouldn't be cached · if the LLM
        #    degraded answer that would mislead future users.
        reply_head = reply.strip()[:80].lower()
        error_markers = (
            "unable to",
            "cannot",
            "抱歉",
            "无法",
            "目前无法",
            "sorry",
            "i don't have",
            "not configured",
        )
        if any(m in reply_head for m in error_markers):
            return

        key = stripped.lower()
        with self._lock:
            if len(self._store) >= self.max_entries and key not in self._store:
                self._store.popitem(last=False)
            self._store[key] = {
                "prompt": prompt,
                "reply": reply,
                "ts": time.time(),
            }
            self._store.move_to_end(key)

    def try_reply(
        self,
        *,
        prompt: str,
        actor: str | None = None,
    ) -> TierResult:
        t0 = time.perf_counter()
        if not self.enabled or not prompt:
            self.misses += 1
            return TierResult(self.name, None, (time.perf_counter() - t0) * 1000)
        bg = self._bigrams(prompt)
        now = time.time()
        best: tuple[float, dict[str, Any]] | None = None
        with self._lock:
            for entry in self._store.values():
                # TTL check · stale entries don't count.
                if now - entry["ts"] > self.ttl_seconds:
                    continue
                score = self._jaccard(bg, self._bigrams(entry["prompt"]))
                if score >= self.similarity and (best is None or score > best[0]):
                    best = (score, entry)
        elapsed = (time.perf_counter() - t0) * 1000
        if best is None:
            self.misses += 1
            return TierResult(self.name, None, elapsed)
        self.hits += 1
        score, entry = best
        return TierResult(
            self.name,
            reply=entry["reply"],
            latency_ms=elapsed,
            detail=f"match_score={score:.2f} src={entry['prompt']!r}",
        )

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "enabled": self.enabled,
            "similarity": self.similarity,
            "size": len(self._store),
            "max_entries": self.max_entries,
            "ttl_hours": self.ttl_seconds / 3600,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": (
                self.hits / (self.hits + self.misses) if (self.hits + self.misses) else 0.0
            ),
        }


class SLMTier(ReplyTier):
    """Local SLM tier · STUB by default. When ``endpoint`` is set,
    posts the prompt to an OpenAI-compatible chat endpoint running
    locally (llama.cpp / ollama / vLLM / mlc-llm). The expected
    latency is 200–800 ms on a typical edge device — fast enough
    to absorb most "almost reflex" traffic without the cloud cost.

    Disabled when ``endpoint`` is empty · the tier reports as
    "not configured" in describe() so the operator knows to plug
    in a model.
    """

    name = "slm"

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        model: str = "qwen2.5-0.5b",
        timeout_ms: int = 800,
        system_prompt: str = (
            "You are a smart home assistant. Reply in ONE short "
            "sentence in the user's language. No explanations."
        ),
    ) -> None:
        self.endpoint = (endpoint or "").strip()
        self.model = model
        self.timeout_s = max(0.05, timeout_ms / 1000.0)
        self.system_prompt = system_prompt
        self.enabled = bool(self.endpoint)
        self.hits = 0
        self.misses = 0
        self.errors = 0

    def try_reply(
        self,
        *,
        prompt: str,
        actor: str | None = None,
    ) -> TierResult:
        t0 = time.perf_counter()
        if not self.enabled or not prompt:
            self.misses += 1
            return TierResult(self.name, None, (time.perf_counter() - t0) * 1000)
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 80,
            "temperature": 0.2,
        }
        # SSRF guard (audit C4): the SLM endpoint is a local/private model
        # server by design, so private IPs are allowed — but the scheme and
        # host still must pass url_guard (blocks file://, missing host, …).
        from runtime.safety.auth.url_guard import check_url

        endpoint_url = self.endpoint.rstrip("/") + "/chat/completions"
        verdict = check_url(endpoint_url, allow_private=True)
        if not verdict.allow:
            self.errors += 1
            return TierResult(
                self.name,
                None,
                (time.perf_counter() - t0) * 1000,
                detail=f"slm url rejected: {verdict.reason}",
            )
        try:
            req = urllib.request.Request(
                endpoint_url,
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:  # nosec B310 — audited HTTP LLM endpoint
                data = json.loads(resp.read())
            text = (
                ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "")
            ).strip()
            elapsed = (time.perf_counter() - t0) * 1000
            if not text:
                self.misses += 1
                return TierResult(self.name, None, elapsed, detail="empty SLM response")
            self.hits += 1
            return TierResult(self.name, reply=text, latency_ms=elapsed, detail=f"slm={self.model}")
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            self.errors += 1
            elapsed = (time.perf_counter() - t0) * 1000
            return TierResult(
                self.name,
                None,
                elapsed,
                detail=f"slm error: {type(exc).__name__}",
            )
        except (ImportError, AttributeError, TypeError) as exc:
            self.errors += 1
            elapsed = (time.perf_counter() - t0) * 1000
            return TierResult(
                self.name,
                None,
                elapsed,
                detail=f"slm error: {type(exc).__name__}: {exc}",
            )

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "enabled": self.enabled,
            "endpoint": self.endpoint or "(not configured)",
            "model": self.model,
            "timeout_ms": int(self.timeout_s * 1000),
            "hits": self.hits,
            "misses": self.misses,
            "errors": self.errors,
            "hit_rate": (
                self.hits / (self.hits + self.misses) if (self.hits + self.misses) else 0.0
            ),
        }


class TieredResponder:
    """Run tiers in priority order · first non-None reply wins.

    Skip a tier by setting ``tier.enabled = False``. Add a new tier
    by appending to the list (or via ``register``). Per-tier stats
    bubble up through ``describe`` for the admin UI.
    """

    def __init__(self, tiers: list[ReplyTier]) -> None:
        self._tiers = list(tiers)
        self._lock = threading.RLock()

    def register(self, tier: ReplyTier) -> None:
        with self._lock:
            self._tiers.append(tier)

    def list_tiers(self) -> list[ReplyTier]:
        with self._lock:
            return list(self._tiers)

    def try_reply(
        self,
        *,
        prompt: str,
        actor: str | None = None,
    ) -> tuple[TierResult | None, list[TierResult]]:
        """Try each tier · return ``(winning_result, all_results)``.
        Empty winner → caller falls through to its planner / LLM
        path. ``all_results`` is the per-tier breakdown for
        observability (admin UI / journal).
        """
        results: list[TierResult] = []
        for t in self.list_tiers():
            if not t.enabled:
                continue
            r = t.try_reply(prompt=prompt, actor=actor)
            results.append(r)
            if r.reply is not None:
                return r, results
        return None, results

    def describe(self) -> list[dict[str, Any]]:
        return [t.describe() for t in self.list_tiers()]


def get_default_fuzzy_cache() -> FuzzyCacheTier:
    provider = get_provider()
    tier = provider.get("fuzzy_cache")
    if tier is None:
        tier = FuzzyCacheTier()
        provider.register_instance("fuzzy_cache", tier)
    return tier


def get_default_slm() -> SLMTier:
    provider = get_provider()
    tier = provider.get("slm_tier")
    if tier is None:
        tier = SLMTier()
        provider.register_instance("slm_tier", tier)
    return tier


def configure_slm(
    *,
    endpoint: str | None,
    model: str | None = None,
    timeout_ms: int | None = None,
) -> None:
    """Hot-reload the SLM tier config · called from the rules
    loader when the yaml has a ``slm:`` block."""
    tier = get_default_slm()
    tier.endpoint = (endpoint or "").strip()
    tier.enabled = bool(tier.endpoint)
    if model:
        tier.model = model
    if timeout_ms:
        tier.timeout_s = max(0.05, int(timeout_ms) / 1000.0)


__all__ = [
    "TierResult",
    "ReplyTier",
    "FuzzyCacheTier",
    "SLMTier",
    "TieredResponder",
    "get_default_fuzzy_cache",
    "get_default_slm",
    "configure_slm",
]
