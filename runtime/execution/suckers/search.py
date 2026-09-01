"""Semantic skill search — TF-IDF-based skill discovery.

Rather than dumping every skill into the planner's context every
turn, pick the top-K most relevant to the current intent via cheap
local TF-IDF similarity. No external ML dependencies — pure stdlib.

Trade-off vs embeddings
-----------------------
TF-IDF is less semantically precise than dense embeddings but:
  - zero latency (no API call)
  - zero cost (no embedding model)
  - zero external deps (pure Python)
  - deterministic (repeatable across runs)

For the skill-discovery use case this is the right default. Callers
that want better recall can swap in an embeddings-based searcher via
the same ``SkillSearcher`` interface.

Typical usage
-------------

    from runtime.execution.suckers import SkillRegistry
    from runtime.execution.suckers.search import TfIdfSkillSearcher

    searcher = TfIdfSkillSearcher(registry)
    top_skills = searcher.search("read the config file", k=8)
    # → ["read_file", "list_cwd", "file_stats", ...]

    # Pass only the relevant K into the planner's context instead of
    # all 60+ skill index entries.
    relevant_index = [
        entry for entry in registry.index()
        if entry["name"] in top_skills
    ]
"""

from __future__ import annotations

import math
import re
from abc import ABC, abstractmethod
from collections import Counter
from typing import Any

# Very small stopword list — we keep it short so domain-specific
# terms like "file", "code", "write" stay informative.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "have",
        "in",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "was",
        "were",
        "will",
        "with",
        "would",
        # Agent-specific noise
        "returns",
        "return",
        "use",
        "used",
        "using",
        "into",
        "out",
    }
)

_TOKEN_RE = re.compile(r"[a-z0-9_]+")


def _tokenize(text: str) -> list[str]:
    """Split on word boundaries, lowercase, drop stopwords + short tokens."""
    if not text:
        return []
    toks = _TOKEN_RE.findall(text.lower())
    return [t for t in toks if len(t) > 1 and t not in _STOPWORDS]


class SkillSearcher(ABC):
    """Abstract skill-search interface.

    Implementations must support ``search(query, k)`` returning a
    list of skill names ordered by relevance (most relevant first).
    """

    @abstractmethod
    def search(self, query: str, *, k: int = 10) -> list[str]:
        """Return up to ``k`` skill names most relevant to ``query``."""

    def refresh(self) -> None:  # noqa: B027 — base class default no-op; subclasses override only when needed
        """Rebuild any cached indexes after the registry changes.

        Default is a no-op — stateless implementations don't need it.
        """


class TfIdfSkillSearcher(SkillSearcher):
    """TF-IDF-based semantic search over the registry's skill index.

    Indexes every registered skill's ``name + summary + description``
    into a TF-IDF vector. Scores queries via cosine similarity.

    The index is built lazily on the first ``search`` call and
    rebuilt on demand via ``refresh()``. For a 60-skill registry the
    index is a few KB of Python dicts — negligible.
    """

    def __init__(self, registry: Any) -> None:
        self._registry = registry
        self._built = False
        # doc_id → {term: tf_weight}
        self._doc_vectors: dict[str, dict[str, float]] = {}
        # term → idf
        self._idf: dict[str, float] = {}

    # ── public API ──────────────────────────────────────────

    def refresh(self) -> None:
        """Drop the cached index so the next search rebuilds from the
        current registry state."""
        self._built = False
        self._doc_vectors = {}
        self._idf = {}

    def search(self, query: str, *, k: int = 10) -> list[str]:
        if not self._built:
            self._build_index()

        query_terms = _tokenize(query)
        if not query_terms:
            # No signal — fall back to all enabled skill names.
            return list(self._doc_vectors.keys())[:k]

        q_tf = Counter(query_terms)
        q_vec: dict[str, float] = {}
        for term, count in q_tf.items():
            idf = self._idf.get(term, 0.0)
            if idf > 0:
                q_vec[term] = (1 + math.log(count)) * idf
        if not q_vec:
            # Query terms don't appear in any doc — return all.
            return list(self._doc_vectors.keys())[:k]

        q_norm = math.sqrt(sum(v * v for v in q_vec.values()))
        if q_norm == 0:
            return list(self._doc_vectors.keys())[:k]

        scores: list[tuple[float, str]] = []
        for name, doc_vec in self._doc_vectors.items():
            if not doc_vec:
                continue
            dot = sum(q_vec[t] * doc_vec[t] for t in q_vec if t in doc_vec)
            if dot <= 0:
                continue
            doc_norm = math.sqrt(sum(v * v for v in doc_vec.values()))
            if doc_norm == 0:
                continue
            scores.append((dot / (q_norm * doc_norm), name))

        scores.sort(key=lambda x: (-x[0], x[1]))
        return [name for _, name in scores[:k]]

    # ── internals ───────────────────────────────────────────

    def _build_index(self) -> None:
        """Materialize doc vectors + IDF weights from the registry."""
        # Collect (name, tokens) for every enabled skill.
        docs: dict[str, list[str]] = {}
        for name in self._enabled_names():
            skill = self._registry.get(name)
            text = " ".join(
                filter(
                    None,
                    [
                        skill.name.replace("_", " "),
                        skill.summary,
                        skill.description,
                        " ".join(skill.affinity),
                    ],
                )
            )
            docs[name] = _tokenize(text)

        # Document frequency per term.
        df: Counter[str] = Counter()
        for tokens in docs.values():
            for term in set(tokens):
                df[term] += 1

        n = max(1, len(docs))
        self._idf = {term: math.log(n / freq) + 1.0 for term, freq in df.items()}

        # Per-doc TF-IDF vectors.
        self._doc_vectors = {}
        for name, tokens in docs.items():
            if not tokens:
                self._doc_vectors[name] = {}
                continue
            tf = Counter(tokens)
            vec: dict[str, float] = {}
            for term, count in tf.items():
                idf = self._idf.get(term, 0.0)
                if idf > 0:
                    vec[term] = (1 + math.log(count)) * idf
            self._doc_vectors[name] = vec

        self._built = True

    def _enabled_names(self) -> list[str]:
        """Return enabled-skill names, falling back to all names if the
        registry has no enabled/disabled tracking."""
        fn = getattr(self._registry, "list_enabled", None)
        if callable(fn):
            names = fn()
            if names:
                return names
        return list(self._registry.all_names())


__all__ = [
    "SkillSearcher",
    "TfIdfSkillSearcher",
]
