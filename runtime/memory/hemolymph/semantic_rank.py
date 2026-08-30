"""Generic semantic ranking — order candidate texts by relevance to a query.

Reuses the configurable embedding backend (Ollama / fastembed / sentence-
transformers via ``ECHO_EMBED_*``). When no embedder is available it falls
back to lexical token-overlap, so the ranking is always at least as good as
keyword matching — never worse.

Used by the tentacle bridge so a connected phone (echo-mobile) can rank its
on-device skills / cached actions by *meaning* ("发个微信" ≈ "给朋友发条消息")
instead of brittle ``contains`` keyword hits.
"""

from __future__ import annotations

import re
from typing import Any

from runtime.memory.hemolymph import embedding_backend
from runtime.memory.hemolymph.semantic_code_index import _cosine

_TOKEN = re.compile(r"[a-z0-9]+|[一-鿿]")


def _tokens(text: str) -> set[str]:
    """ASCII words + individual CJK chars — a cheap language-agnostic bag."""
    return set(_TOKEN.findall(text.lower()))


def _lexical_score(query_tokens: set[str], text: str) -> float:
    """Overlap coefficient — robust to length, 0..1."""
    cand = _tokens(text)
    if not query_tokens or not cand:
        return 0.0
    return len(query_tokens & cand) / min(len(query_tokens), len(cand))


def rank(query: str, candidates: list[str], *, top_k: int | None = None) -> dict[str, Any]:
    """Rank ``candidates`` by relevance to ``query``.

    Returns ``{"backend": "embed"|"lexical", "ranked": [{index, score, text}]}``
    sorted best-first. ``index`` refers back into the input list so the caller
    can map to its own objects.
    """
    query = (query or "").strip()
    clean = [(i, str(c)) for i, c in enumerate(candidates or []) if str(c).strip()]
    if not query or not clean:
        return {"backend": "none", "ranked": []}

    backend = "lexical"
    scores: list[tuple[int, float, str]] = []

    vectors = embedding_backend.embed_texts([query] + [c for _, c in clean])
    if vectors and len(vectors) == len(clean) + 1:
        backend = "embed"
        qv = vectors[0]
        for (idx, text), vec in zip(clean, vectors[1:], strict=False):
            scores.append((idx, _cosine(qv, vec), text))
    else:
        qt = _tokens(query)
        for idx, text in clean:
            scores.append((idx, _lexical_score(qt, text), text))

    scores.sort(key=lambda s: s[1], reverse=True)
    if top_k is not None:
        scores = scores[: max(0, top_k)]
    return {
        "backend": backend,
        "ranked": [
            {"index": idx, "score": round(score, 4), "text": text} for idx, score, text in scores
        ],
    }
