"""Auto-retrieve relevant codebase context from the project wiki.

The context composer feeds the planner system / skills / memory, but nothing
about the *codebase* — so the agent re-greps the repo every task. echo
already generates a structured project wiki under ``docs/auto`` (titled topic
pages + ``index.json``); this module retrieves the pages most relevant to a
task and renders them into a compact prompt section, so the planner gets
codebase grounding the way Qoder's repo wiki does — without an LLM call.

Retrieval fuses up to three lanes by reciprocal-rank fusion (ADR-009 ·
gbrain-shaped), so heterogeneous signals combine without comparing magnitudes:

- **lexical** — BM25 over identifier-aware tokens (``ToolEngine`` /
  ``tool_engine`` → {tool, engine}) with CJK bigrams (a 中 goal matches a 中
  page) and an OKF source-tier weight. Always on; the only lane the default
  deployment runs.
- **semantic** — reranks the lexical top-pool with the ``ECHO_EMBED_*``
  embedder (Ollama / fastembed / sentence-transformers) when one is configured,
  bridging synonyms BM25 can't (planner→cerebrum). Dormant — and free —
  otherwise.
- **graph** — import-edge neighbours of the top hits (zero-LLM, from
  ``index.json``), surfacing a hit's dependency context.

RRF of a single lane is just that lane's order, so a deployment with no embedder
and no edges behaves exactly like plain BM25. Self-gating: no wiki (no
``docs/auto/index.json``) → returns ``None`` and the planner omits the section.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import re
import threading
from collections import Counter
from pathlib import Path
from typing import Any

# Short / structural tokens carry no retrieval signal — drop them so scoring
# keys on meaningful identifiers (module names, domain nouns).
_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "this",
        "that",
        "from",
        "into",
        "your",
        "you",
        "are",
        "use",
        "add",
        "fix",
        "run",
        "get",
        "set",
        "how",
        "why",
        "what",
        "where",
        "when",
        "make",
        "new",
        "all",
        "any",
        "can",
        "should",
        "would",
        "does",
        "did",
        "its",
        "our",
        "out",
        "via",
        "的",
        "了",
        "在",
        "和",
        "是",
        "把",
        "给",
        "做",
        "加",
        "改",
        "怎么",
        "如何",
    }
)

# Runs of alnum or CJK, separators (incl. ``_``) drop out — so snake_case is
# already split. Camel/acronym/number boundaries are split by _SUBWORD_RE.
_WORD_RE = re.compile(r"[A-Za-z0-9]+|[一-鿿]+")
_SUBWORD_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+|[一-鿿]+")

# BM25 params (standard defaults).
_BM25_K1 = 1.5
_BM25_B = 0.75

# Hybrid retrieval (ADR-009 · gbrain-shaped) — reciprocal-rank fusion of up to
# three lanes (lexical / semantic / graph). RRF combines *ranks*, not scores, so
# a BM25 score and a cosine score fuse without magnitude mixing.
_RRF_K = 60  # standard RRF damping
_FUSION_POOL = 12  # lexical top-N handed to the semantic lane (bounds embed cost)
_GRAPH_SEEDS = 3  # top lexical hits whose import-neighbours seed the graph lane
_RERANK_POOL = 8  # fused top-N a real reranker (Cohere) reorders, when configured

# Source-tier boost (ADR-009): a core subsystem page outranks an equally-relevant
# peripheral one. Multiplicative on the BM25 score, small. Tier comes from the
# page's OKF frontmatter; absent/unknown → 1.0 (no effect).
_TIER_WEIGHT = {"core": 1.15, "standard": 1.0}

# Agent profile material is prompt-private by default.  The generated project
# wiki used to include full SOUL / IDENTITY exports under ``26-agents``; because
# repo grounding searched every wiki page, a general-agent turn could receive a
# teammate's persona as ordinary project context.  Keep this policy close to
# the retriever so every caller inherits the safe default.
_AGENT_PROFILE_WIKI_PREFIX = "20-backend/26-agents/"


def is_private_agent_context_path(path: str) -> bool:
    """Whether a repo/wiki path is agent-private prompt material.

    This is intentionally a narrow path policy: runtime code such as
    ``runtime/execution/agents`` remains searchable, while top-level agent
    packs and their generated wiki mirrors never enter automatic grounding.
    Explicit user file reads go through the tool path and are governed by that
    tool's authorization policy, not this automatic prompt prefetch.
    """
    normalized = str(path or "").replace("\\", "/").lstrip("./").lower()
    return normalized.startswith("agents/") or normalized.startswith(_AGENT_PROFILE_WIKI_PREFIX)


def _maybe_rerank(
    query: str, scored: list[tuple[float, dict[str, Any]]]
) -> list[tuple[float, dict[str, Any]]]:
    """Final stage (ADR-009): a real cross-encoder reorders the fused top-pool.

    Uses echo's own ``research.rerank`` (Cohere Rerank v3 when COHERE_API_KEY
    is set). Gated on the key because rerank's zero-dep BM25 backend would just
    echo the lexical lane. Dormant + free otherwise; never breaks retrieval."""
    if len(scored) < 2 or not os.environ.get("COHERE_API_KEY"):
        return scored
    pool = scored[:_RERANK_POOL]
    sources = [
        {"url": p["path"], "title": p["title"], "content": (p["body"] or "")[:2000]}
        for _s, p in pool
    ]
    try:
        # Import the function from the submodule explicitly — runtime.research
        # re-exports ``rerank`` as a name, so ``from runtime.research import
        # rerank`` would shadow the module with the function.
        from runtime.research.rerank import rerank as _rerank_fn

        result = _rerank_fn(query, sources)
    except Exception:  # noqa: BLE001 — reranking must never break retrieval
        return scored
    if result.backend != "cohere":  # fell back to bm25 (no key / network) → keep
        return scored
    by_path = {p["path"]: (s, p) for s, p in pool}
    reranked = [by_path[h.source.url] for h in result.hits if h.source.url in by_path]
    return reranked + scored[_RERANK_POOL:] if reranked else scored


def _rrf(rankings: list[list[str]], k: int = _RRF_K) -> dict[str, float]:
    """Reciprocal-rank fusion: each doc scores Σ 1/(k + rank) over the lists it
    appears in. Rank-based, so heterogeneous lanes (BM25, cosine, graph
    proximity) combine without comparing magnitudes. A single list fuses to its
    own order — the no-op that keeps the default (no embedder / no edges) path
    identical to plain BM25."""
    out: dict[str, float] = {}
    for ranking in rankings:
        for rank_i, doc_id in enumerate(ranking):
            out[doc_id] = out.get(doc_id, 0.0) + 1.0 / (k + rank_i + 1)
    return out


# Cache the parsed + indexed wiki keyed by (dir, index.json mtime) so a hot
# planning loop doesn't re-read/re-index ~30 files every turn, but a
# regenerated wiki is picked up automatically.
_CACHE_LOCK = threading.Lock()
_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}

# Page-embedding cache for the semantic lane (ADR-009 Phase 3): embed the corpus
# once, keyed by index.json mtime, so a hot planner loop embeds only the per-call
# query — the gbrain "precompute doc vectors" pattern. Empty when no embedder.
_VEC_CACHE: dict[str, tuple[float, dict[str, list[float]]]] = {}


def _tokenize(text: str) -> list[str]:
    """Identifier-aware tokens: split camelCase / snake_case / acronyms into
    sub-words so ``ToolEngine`` and ``tool_engine`` both yield {tool, engine}.

    A CJK run emits both the **whole run** (exact-match signal) and its
    **adjacent-char bigrams** (partial-overlap signal). Keeping only the whole
    run made BM25 weak on Chinese: a CN goal and a CN description rarely share a
    whole run but do share bigrams (脱靶实测见 ADR-009 Phase 0 — "优化简历…" vs
    skill 描述). Bigrams mirror the composer's CJK signal so the two rankers
    converge on one engine."""
    out: list[str] = []
    for word in _WORD_RE.findall(text):
        if "一" <= word[0] <= "鿿":
            if word not in _STOPWORDS:
                out.append(word)
            # Adjacent-char bigrams within the run · cross-lingual overlap that
            # whole-run matching misses (a 2-char run's only bigram is itself,
            # so single domain words like 规划 still surface).
            for i in range(len(word) - 1):
                bg = word[i : i + 2]
                if bg not in _STOPWORDS:
                    out.append(bg)
            continue
        for part in _SUBWORD_RE.findall(word):
            p = part.lower()
            if len(p) >= 2 and p not in _STOPWORDS:
                out.append(p)
    return out


def _flatten(tree: Any) -> list[tuple[str, str]]:
    """Walk the index.json ``tree`` into a flat ``[(title, path)]`` list."""
    pages: list[tuple[str, str]] = []
    if not isinstance(tree, list):
        return pages
    for node in tree:
        if not isinstance(node, dict):
            continue
        if node.get("type") == "doc" and node.get("path"):
            pages.append((str(node.get("title") or ""), str(node["path"])))
        children = node.get("children")
        if children:
            pages.extend(_flatten(children))
    return pages


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split an OKF/YAML frontmatter block from the markdown body. gen_wiki
    emits each value as a JSON literal, so every ``key: <json>`` line parses
    with ``json.loads`` — no YAML dependency. No frontmatter → ``({}, text)``."""
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    meta: dict[str, Any] = {}
    for line in text[4:end].splitlines():
        key, sep, val = line.partition(":")
        if not sep:
            continue
        try:
            meta[key.strip()] = json.loads(val.strip())
        except (ValueError, TypeError):
            meta[key.strip()] = val.strip().strip('"')
    # Only treat it as OKF frontmatter if the required ``type`` field is present.
    # Otherwise a doc that merely opens with a ``---`` rule (with another ``---``
    # later) would have its body silently truncated — and a prose line that
    # happens to contain a colon would fool a "non-empty meta" check.
    if "type" not in meta:
        return {}, text
    return meta, text[end + 5 :]


def _build_index(wiki_dir: Path) -> dict[str, Any]:
    """Read every wiki page and build a small BM25 index over it."""
    index_path = wiki_dir / "index.json"
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"pages": [], "df": {}, "n": 0, "avgdl": 0.0}

    pages: list[dict[str, Any]] = []
    df: Counter[str] = Counter()
    total_len = 0
    for title, rel in _flatten(index.get("tree")):
        raw = ""
        with contextlib.suppress(OSError):
            raw = (wiki_dir / rel).read_text(encoding="utf-8")
        meta, body = _split_frontmatter(raw)
        # Agent pages are derived from prompt-private SOUL / IDENTITY files.
        # Do not place them in the searchable corpus at all: filtering after
        # ranking would still let them affect BM25/semantic scores and graph
        # expansion for unrelated project material.
        if is_private_agent_context_path(rel) or str(meta.get("type") or "").lower() == "agent":
            continue
        desc = str(meta.get("description") or "")
        tier = str(meta.get("tier") or "standard")
        tags = meta.get("tags") or []
        tag_str = " ".join(tags) if isinstance(tags, list) else str(tags)
        # Weight the high-signal OKF fields: title + description repeated so a
        # title/description hit beats a body mention. Frontmatter is stripped
        # from ``body`` so YAML keys pollute neither BM25 nor the prompt (which
        # injects ``body`` verbatim). No frontmatter → desc/tags empty → same
        # tokens as before (backward compatible).
        tf = Counter(_tokenize(f"{title} {title} {desc} {desc} {tag_str} {rel} {body}"))
        if not tf:
            continue
        length = sum(tf.values())
        total_len += length
        for term in tf:
            df[term] += 1
        pages.append(
            {"title": title, "path": rel, "body": body, "tf": tf, "length": length, "tier": tier}
        )

    n = len(pages)
    avgdl = (total_len / n) if n else 0.0
    # Page→page edges (ADR-009): undirected adjacency for graph-augmented
    # retrieval. A page imports / is imported by its neighbors, so a strong hit
    # can lift the dependency context around it. Absent ``edges`` → empty → no-op.
    adj: dict[str, set[str]] = {}
    for edge in index.get("edges") or []:
        a, b = edge.get("from"), edge.get("to")
        if a and b:
            adj.setdefault(a, set()).add(b)
            adj.setdefault(b, set()).add(a)
    return {"pages": pages, "df": df, "n": n, "avgdl": avgdl, "adj": adj}


def _load_index(wiki_dir: Path) -> dict[str, Any]:
    """Return the BM25 index for ``wiki_dir``, cached by index.json mtime."""
    index_path = wiki_dir / "index.json"
    try:
        mtime = index_path.stat().st_mtime
    except OSError:
        return {"pages": [], "df": {}, "n": 0, "avgdl": 0.0}

    key = str(wiki_dir)
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached is not None and cached[0] == mtime:
            return cached[1]

    built = _build_index(wiki_dir)

    with _CACHE_LOCK:
        _CACHE[key] = (mtime, built)
    return built


def _bm25(q_terms: list[str], page: dict[str, Any], idx: dict[str, Any]) -> float:
    tf: Counter[str] = page["tf"]
    df: dict[str, int] = idx["df"]
    n: int = idx["n"]
    avgdl: float = idx["avgdl"] or 1.0
    length = page["length"]
    score = 0.0
    for term in q_terms:
        f = tf.get(term, 0)
        if not f:
            continue
        # idf with the +1 inside log keeps it non-negative even for common terms.
        idf = math.log(1 + (n - df.get(term, 0) + 0.5) / (df.get(term, 0) + 0.5))
        denom = f + _BM25_K1 * (1 - _BM25_B + _BM25_B * length / avgdl)
        score += idf * (f * (_BM25_K1 + 1)) / denom
    return score


def _default_wiki_dir() -> Path:
    return Path.cwd() / "docs" / "auto"


def _page_vectors(base: Path, idx: dict[str, Any]) -> dict[str, list[float]]:
    """Embed every page once and cache by index.json mtime · ``{path: vector}``.
    A hot planner loop then embeds only the per-call query, not the whole corpus
    (gbrain's precompute pattern). Empty when no embedder, or when embedding
    fails — the semantic lane then simply stays off."""
    from runtime.memory.hemolymph import embedding_backend

    try:
        mtime = (base / "index.json").stat().st_mtime
    except OSError:
        return {}
    key = str(base)
    with _CACHE_LOCK:
        hit = _VEC_CACHE.get(key)
    if hit and hit[0] == mtime:
        return hit[1]
    pages = idx["pages"]
    texts = [f"{p['title']}\n{(p['body'] or '')[:600]}" for p in pages]
    vecs = embedding_backend.embed_texts(texts) or []
    out: dict[str, list[float]] = {}
    if len(vecs) == len(pages):
        out = {p["path"]: v for p, v in zip(pages, vecs, strict=False)}
    with _CACHE_LOCK:
        _VEC_CACHE[key] = (mtime, out)
    return out


def retrieve_repo_context(
    query: str,
    *,
    wiki_dir: str | Path | None = None,
    budget_tokens: int = 1400,
    max_pages: int = 2,
    _sink: list[dict[str, str]] | None = None,
) -> str | None:
    """Retrieve the wiki pages most relevant to ``query`` (BM25) as a prompt
    section. Returns ``None`` when there is no wiki or no page overlaps.

    ``_sink``: if given, the EXACT pages chosen for the prompt are appended as
    ``{"kind": "doc", "title", "path"}`` dicts — so a UI "consulted these docs"
    chip is faithful to what was actually injected, with no second scoring pass
    that could drift from this one.
    """
    query = (query or "").strip()
    if not query:
        return None
    q_terms = list(dict.fromkeys(_tokenize(query)))  # unique, order kept
    if not q_terms:
        return None

    base = Path(wiki_dir) if wiki_dir is not None else _default_wiki_dir()
    idx = _load_index(base)
    if not idx["pages"]:
        return None

    # ── Lane 1 · lexical (BM25 × source-tier) — always present ──────────────
    lexical = [
        (_bm25(q_terms, p, idx) * _TIER_WEIGHT.get(p.get("tier", "standard"), 1.0), p)
        for p in idx["pages"]
    ]
    lexical = [(s, p) for s, p in lexical if s > 0]
    if not lexical:
        return None
    lexical.sort(key=lambda sp: (-sp[0], sp[1]["path"]))
    pages_by_path = {p["path"]: p for _s, p in lexical}
    lexical_order = [p["path"] for _s, p in lexical]
    rankings: list[list[str]] = [lexical_order]

    # Lazy import keeps the semantic backend off the module-load path.
    from runtime.memory.hemolymph import embedding_backend
    from runtime.memory.hemolymph.semantic_code_index import _cosine

    # ── Lane 2 · semantic — cached page vectors, only the query embedded ────
    # Bridges synonyms BM25 can't (the ceiling repo_context documented), using
    # the ECHO_EMBED_* embedder. The corpus is embedded once and cached (by
    # index.json mtime); only the per-call query is embedded — gbrain's
    # precompute pattern. Dormant + free when no embedder is configured.
    if len(lexical) > 1 and embedding_backend.available():
        page_vecs = _page_vectors(base, idx)
        qv = embedding_backend.embed_texts([query]) if page_vecs else None
        if qv:
            q0 = qv[0]
            pool = [pth for pth in lexical_order[:_FUSION_POOL] if pth in page_vecs]
            if pool:
                rankings.append(
                    sorted(pool, key=lambda pth: _cosine(q0, page_vecs[pth]), reverse=True)
                )

    # ── Lane 3 · graph — import-edge neighbours of the top lexical hits ─────
    # Only lexical-matched neighbours (never promotes a zero-overlap page).
    # ECHO_CODEBASE_GRAPH=0 disables.
    adj = idx.get("adj") or {}
    _graph_on = os.environ.get("ECHO_CODEBASE_GRAPH", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )
    if adj and _graph_on:
        seen: set[str] = set()
        graph_order: list[str] = []
        for pth in lexical_order[:_GRAPH_SEEDS]:
            for nb in sorted(adj.get(pth, ())):
                if nb in pages_by_path and nb not in seen:
                    seen.add(nb)
                    graph_order.append(nb)
        if graph_order:
            rankings.append(graph_order)

    # Fuse the lanes. One lane → identity order (default path unchanged).
    fused = _rrf(rankings)
    scored = sorted(
        ((fused.get(p["path"], 0.0), p) for _s, p in lexical),
        key=lambda sp: (-sp[0], sp[1]["path"]),
    )
    # Optional final cross-encoder rerank (dormant unless COHERE_API_KEY is set).
    scored = _maybe_rerank(query, scored)

    # ~4 chars/token; split the body budget across the chosen pages.
    per_page_chars = max(400, (budget_tokens * 4) // max(1, max_pages))
    parts: list[str] = [
        "RELEVANT CODEBASE DOCS (auto-retrieved from the project wiki — use to "
        "orient before grepping; verify against source before relying on it):",
    ]
    for _score, page in scored[:max_pages]:
        body = (page["body"] or "").strip()
        if len(body) > per_page_chars:
            body = body[:per_page_chars].rstrip() + "\n…(truncated)"
        header = page["title"] or page["path"]
        if _sink is not None:
            _sink.append({"kind": "doc", "title": str(header), "path": str(page["path"])})
        parts.append(f"\n## {header}  ({page['path']})\n{body}")
    return "\n".join(parts)


def _codebase_context_disabled() -> bool:
    import os

    return os.environ.get("ECHO_CODEBASE_CONTEXT", "1").strip().lower() in (
        "0",
        "false",
        "no",
        "off",
    )


def build_codebase_context(
    goal: str,
    *,
    strict_explicit_scope: bool = False,
) -> tuple[str, list[dict[str, str]]]:
    """Combined codebase grounding for a goal: relevant wiki pages (summaries)
    + the actual source chunks. Returns ``(prompt_section, sources)`` where
    ``sources`` lists exactly the docs/chunks folded into ``prompt_section``
    (``{"kind": "doc"|"source", "title", "path"}``) — so a UI grounding chip
    shows what was *actually* injected, from the same retrieval.

    Shared by the planner AND the react chat loop so interactive chat gets the
    same grounding as planned turns — not just the graph paths. Self-gating +
    best-effort; disabled by ``ECHO_CODEBASE_CONTEXT=0``.
    """
    if _codebase_context_disabled():
        return "", []
    goal = (goal or "").strip()
    if not goal:
        return "", []
    parts: list[str] = []
    sources: list[dict[str, str]] = []
    if not strict_explicit_scope:
        with contextlib.suppress(Exception):
            wiki = retrieve_repo_context(goal, _sink=sources)
            if wiki:
                parts.append(wiki)
    with contextlib.suppress(Exception):
        from runtime.memory.hemolymph.code_index import retrieve_code_context

        code = retrieve_code_context(
            goal,
            _sink=sources,
            strict_explicit_paths=strict_explicit_scope,
        )
        if code:
            parts.append(code)
    return "\n\n".join(parts), sources


def render_codebase_context(goal: str) -> str:
    """``build_codebase_context``'s prompt section only — the existing string
    contract for callers that don't need the structured source list."""
    return build_codebase_context(goal)[0]


def collect_codebase_sources(goal: str) -> list[dict[str, str]]:
    """The docs/chunks that ``render_codebase_context(goal)`` would inject, as
    structured ``{"kind", "title", "path"}`` dicts — for a UI grounding chip."""
    return build_codebase_context(goal)[1]
