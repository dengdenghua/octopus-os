"""Read-only semantic search over the work-mode KB's persisted code index.

echo already has a local knowledge base: ``code_intelligence_skills`` + the
``/api/index/*`` panel build a SQLite index of ``(path, chunk, embedding)`` at
``data/code_index.db``. The react-chat grounding (``code_index``) reuses that
exact artifact — read-only, **never building** (safe on the hot path) — so it
can fuse dense semantic recall on top of BM25.

The query is embedded through :mod:`embedding_backend`, the SAME configurable
embedder the index BUILD uses, so corpus and query always share a model — and
both can point at one local Ollama / OpenAI-compatible endpoint (unifying with
echo-storage). Reading the stored float32 vectors uses stdlib ``array`` and
the cosine pass falls back to pure Python, so with a remote embedding endpoint
the semantic layer needs **no numpy / sentence-transformers locally**.

Self-gating: no DB, no embedding backend, or ``ECHO_CODEBASE_SEMANTIC=0`` →
``None`` and the caller stays pure BM25 (zero-dependency behaviour unchanged).
"""

from __future__ import annotations

import array
import math
import os
import sqlite3
from pathlib import Path
from typing import Any

from runtime.memory.hemolymph.embedding_backend import embed_texts

# The same artifact the work-mode KB agent writes (code_intelligence_skills /
# index_router). Reusing this path IS the reuse.
_DEFAULT_DB = Path("data/code_index.db")


def _disabled() -> bool:
    return os.environ.get("ECHO_CODEBASE_SEMANTIC", "auto").strip().lower() in (
        "0",
        "false",
        "no",
        "off",
    )


def _load_rows(db_path: Path) -> list[tuple[str, str, list[float]]]:
    """Read ``(path, chunk, vector)`` rows from the persisted index (float32
    blobs decoded with stdlib ``array`` — no numpy needed), or ``[]``."""
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            rows = conn.execute("SELECT path, chunk, embedding FROM code_chunks").fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return []
    out: list[tuple[str, str, list[float]]] = []
    for r in rows:
        try:
            vec = array.array("f")
            vec.frombytes(bytes(r[2]))
        except (TypeError, ValueError):
            continue
        out.append((str(r[0]), str(r[1]), list(vec)))
    return out


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a)) or 1e-9
    nb = math.sqrt(sum(x * x for x in b)) or 1e-9
    return dot / (na * nb)


def search_persisted(
    query: str,
    *,
    top_k: int = 5,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]] | None:
    """Top-k semantically-nearest chunks from the persisted KB index, or
    ``None`` when the semantic layer isn't available (no DB / no embedding
    backend / disabled). Read-only: never (re)builds the index, so it's safe on
    a hot path — it only consults what the user already indexed."""
    query = (query or "").strip()
    if not query or _disabled():
        return None
    path = Path(db_path) if db_path is not None else _DEFAULT_DB
    rows = _load_rows(path)
    if not rows:
        return None
    embedded = embed_texts([query])
    if not embedded or not embedded[0]:
        return None
    q = embedded[0]
    scored = [(_cosine(q, vec), p, chunk) for p, chunk, vec in rows]
    scored.sort(key=lambda t: -t[0])
    return [
        {"path": p, "snippet": c, "score": round(s, 4)} for s, p, c in scored[: max(1, int(top_k))]
    ]
