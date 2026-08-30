"""Rerank fetched/retrieved sources by relevance to a query.

Two backends, chosen by env var or explicit arg:

- `bm25` (default, zero-dep): classic Okapi BM25 over a CJK-aware token
  stream. No network, no ML model. Good enough to beat raw retrieval order.
- `cohere`: Cohere Rerank v3 API (https://docs.cohere.com/reference/rerank).
  Needs `COHERE_API_KEY`. Far more accurate for cross-lingual + semantic
  cases, costs a few cents per 100 docs.

Env routing (mirrors `web_skills._resolve_backend`):
  RERANK_BACKEND=bm25|cohere     — explicit override
  COHERE_API_KEY set             — auto-picks cohere
  otherwise                      — bm25
"""

from __future__ import annotations

import math
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from runtime.research.citations import SourceEntry, _coerce

try:
    import httpx  # type: ignore[import-untyped]

    HTTPX_AVAILABLE = True
except ImportError:  # pragma: no cover
    HTTPX_AVAILABLE = False
    httpx = None  # type: ignore[assignment]


@dataclass(slots=True)
class RerankHit:
    """A reranked source paired with its relevance score."""

    source: SourceEntry
    score: float
    rank: int  # 0-indexed rank after reranking


@dataclass(slots=True)
class RerankResult:
    hits: list[RerankHit]
    backend: str

    @property
    def sources(self) -> list[SourceEntry]:
        return [h.source for h in self.hits]


def _resolve_backend() -> str:
    explicit = (os.environ.get("RERANK_BACKEND") or "").strip().lower()
    if explicit:
        return explicit
    if os.environ.get("COHERE_API_KEY"):
        return "cohere"
    return "bm25"


def rerank(
    query: str,
    sources: Iterable[SourceEntry | dict],
    *,
    top_k: int | None = None,
    backend: str | None = None,
    client: Any = None,
    timeout_ms: int = 8000,
) -> RerankResult:
    """Score `sources` against `query`, return sorted-by-score top-K."""
    normalized = [_coerce(s) for s in sources]
    if not normalized:
        return RerankResult(hits=[], backend=backend or _resolve_backend())

    chosen = (backend or _resolve_backend()).lower()

    if chosen == "cohere":
        key = os.environ.get("COHERE_API_KEY", "")
        if not key:
            # Explicit fallback rather than silent error: caller asked for
            # cohere but no key → drop to bm25 so research loop still works.
            chosen = "bm25"
        else:
            scores = _cohere_rerank(client, key, query, normalized, timeout_ms=timeout_ms)
            if scores is None:  # network failure
                chosen = "bm25"
            else:
                return _assemble(normalized, scores, top_k, backend="cohere")

    scores = _bm25_scores(query, normalized)
    return _assemble(normalized, scores, top_k, backend="bm25")


# ═══════════════════════════════════════════════════════════
# BM25
# ═══════════════════════════════════════════════════════════

# CJK block (basic) + word chars. Matches either a single CJK char (Chinese,
# Japanese kana/kanji, Korean) or a run of ASCII word chars. This gives us
# reasonable cross-lingual behavior without needing jieba/MeCab.
_TOKEN_RE = re.compile(
    r"[぀-ヿ㐀-䶿一-鿿가-힯]"
    r"|[A-Za-z0-9]+"
)


def _tokenize(text: str) -> list[str]:
    if not text:
        return []
    return [t.lower() for t in _TOKEN_RE.findall(text)]


def _bm25_scores(
    query: str,
    sources: list[SourceEntry],
    *,
    k1: float = 1.5,
    b: float = 0.75,
) -> list[float]:
    q_terms = _tokenize(query)
    if not q_terms:
        return [0.0] * len(sources)

    docs: list[list[str]] = []
    for s in sources:
        body = " ".join(filter(None, [s.title, s.snippet, s.content]))
        docs.append(_tokenize(body))
    doc_lens = [len(d) for d in docs]
    avg_len = sum(doc_lens) / len(doc_lens) if doc_lens else 0.0
    n_docs = len(docs)

    # Document frequency per query term.
    df: dict[str, int] = {}
    unique_q = set(q_terms)
    for d in docs:
        d_set = set(d)
        for t in unique_q:
            if t in d_set:
                df[t] = df.get(t, 0) + 1

    scores: list[float] = []
    for d, dl in zip(docs, doc_lens, strict=True):
        if not d:
            scores.append(0.0)
            continue
        # Term frequency for this doc.
        tf: dict[str, int] = {}
        for tok in d:
            if tok in unique_q:
                tf[tok] = tf.get(tok, 0) + 1
        score = 0.0
        for t in q_terms:
            f = tf.get(t, 0)
            if f == 0:
                continue
            n_t = df.get(t, 0)
            # Okapi BM25 idf with +0.5 smoothing, floor at 0 to avoid
            # negative weights for terms that appear in every doc.
            idf = math.log(1.0 + (n_docs - n_t + 0.5) / (n_t + 0.5))
            denom = f + k1 * (1 - b + b * dl / avg_len) if avg_len else f + k1
            score += idf * (f * (k1 + 1)) / denom
        scores.append(score)
    return scores


# ═══════════════════════════════════════════════════════════
# Cohere Rerank
# ═══════════════════════════════════════════════════════════


def _cohere_rerank(
    client: Any,
    api_key: str,
    query: str,
    sources: list[SourceEntry],
    *,
    timeout_ms: int,
    model: str = "rerank-multilingual-v3.0",
) -> list[float] | None:
    """Returns scores in the SAME order as `sources`, or None on failure."""
    close_after = False
    if client is None:
        if not HTTPX_AVAILABLE:
            return None
        client = httpx.Client(timeout=timeout_ms / 1000)
        close_after = True

    docs = [
        " ".join(filter(None, [s.title, s.snippet, s.content])).strip() or s.url for s in sources
    ]
    try:
        r = client.post(
            "https://api.cohere.com/v1/rerank",
            json={
                "model": model,
                "query": query,
                "documents": docs,
                "top_n": len(docs),
                "return_documents": False,
            },
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        r.raise_for_status()
        data = r.json()
    except Exception:  # noqa: BLE001 — any cohere failure falls back to BM25; never propagate
        return None
    finally:
        if close_after:
            client.close()

    scores = [0.0] * len(sources)
    for item in data.get("results", []):
        idx = item.get("index")
        score = item.get("relevance_score")
        if isinstance(idx, int) and 0 <= idx < len(scores) and score is not None:
            scores[idx] = float(score)
    return scores


# ═══════════════════════════════════════════════════════════
# Assembly
# ═══════════════════════════════════════════════════════════


def _assemble(
    sources: list[SourceEntry],
    scores: list[float],
    top_k: int | None,
    *,
    backend: str,
) -> RerankResult:
    paired = list(zip(sources, scores, strict=True))
    # Stable sort by descending score; original order breaks ties.
    paired.sort(key=lambda x: x[1], reverse=True)
    if top_k is not None:
        paired = paired[:top_k]
    hits = [RerankHit(source=src, score=float(sc), rank=i) for i, (src, sc) in enumerate(paired)]
    return RerankResult(hits=hits, backend=backend)


__all__ = [
    "RerankHit",
    "RerankResult",
    "rerank",
]
