"""High-level research-answer pipeline · ties the six stages into one call.

Stages (see `runtime/research/README` if you want the long version):

  ① query_rewrite   → fan out the user question into N queries
  ② web_search      → multi-backend retrieval (Tavily/Brave/Serper/SearXNG/DDG)
  ③ fetch_url(extract=True) → trafilatura-cleaned article bodies
  ④ rerank          → BM25 or Cohere Rerank to top-K
  ⑤ render_citation_prompt → Perplexity-style numbered-source prompt
  ⑥ resolve_citations → [n] markers → URLs

Public API:

  research_answer(question, router=..., ...) -> ResearchAnswer
  register_research_skill(registry, router)    -> 1  (skill "research_answer")

The function is designed to be robust against individual stage failures:
a dead URL in ③ just drops that source; query_rewrite failing falls back
to rule-based; rerank failing falls back to BM25; if the LLM synthesis
step (⑤→⑥) fails, we still return the top-ranked sources so the caller
can see SOMETHING.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .citations import (
    CitationResolution,
    SourceEntry,
    render_citation_prompt,
    resolve_citations,
)
from .query_rewrite import rewrite_query
from .rerank import rerank

_logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ResearchAnswer:
    question: str
    answer: str
    queries: list[str]  # rewritten search queries
    sources: list[SourceEntry]  # top-K reranked, numbered [1..K]
    used_indices: list[int]  # which [n] markers the answer cited
    invalid_indices: list[int]  # out-of-range citation markers (if any)
    backend: dict[str, str]  # {"rewrite": "llm"/"rule", "rerank": "bm25"/"cohere"}
    stats: dict[str, int] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "answer": self.answer,
            "queries": self.queries,
            "sources": [
                {
                    "n": i + 1,
                    "url": s.url,
                    "title": s.title,
                    "published": s.published,
                    "author": s.author,
                }
                for i, s in enumerate(self.sources)
            ],
            "used_indices": self.used_indices,
            "invalid_indices": self.invalid_indices,
            "backend": self.backend,
            "stats": self.stats,
        }


def research_answer(
    question: str,
    *,
    router: Any,
    search_fn: Callable[..., dict[str, Any]] | None = None,
    fetch_fn: Callable[..., dict[str, Any]] | None = None,
    n_queries: int = 3,
    hits_per_query: int = 5,
    top_k: int = 5,
    synth_model: str = "claude-haiku-4-5-20251001",
    rewrite_model: str = "claude-haiku-4-5-20251001",
    synth_max_tokens: int = 1024,
    max_citation_retries: int = 1,
    today: date | None = None,
) -> ResearchAnswer:
    """Run the full pipeline and return a cited answer.

    Parameters
    ----------
    question  : user's original question
    router    : a ModelRouter for LLM calls (query rewrite + final synthesis)
    search_fn : override for _web_search (testing); defaults to the real skill
    fetch_fn  : override for _fetch_url (testing)
    n_queries : how many search queries to fan out (inc. original)
    hits_per_query : search.max_results per call
    top_k     : keep top-K sources after rerank
    """
    question = (question or "").strip()
    stats: dict[str, int] = {}
    if not question:
        return ResearchAnswer(
            question="",
            answer="",
            queries=[],
            sources=[],
            used_indices=[],
            invalid_indices=[],
            backend={"rewrite": "rule", "rerank": "bm25"},
            stats=stats,
        )

    # Lazy imports so that tests that inject search_fn/fetch_fn don't need
    # httpx installed, and so importing this module doesn't pull in the
    # URL-guard chain eagerly.
    if search_fn is None:
        from runtime.execution.suckers.web_skills import _web_search

        search_fn = _web_search
    if fetch_fn is None:
        from runtime.execution.suckers.web_skills import _fetch_url

        fetch_fn = _fetch_url

    # ① Query rewrite
    rr = rewrite_query(question, router=router, n=n_queries, model=rewrite_model, today=today)
    queries = rr.queries or [question]
    stats["queries"] = len(queries)

    # ② Multi-query search in parallel, then dedupe by URL in query order.
    # executor.map preserves input order, so ranking stays deterministic while
    # independent network requests no longer block one another.
    hits_by_url: dict[str, dict[str, Any]] = {}

    def _search_one(q: str) -> tuple[str, dict[str, Any]]:
        try:
            return q, search_fn(query=q, max_results=hits_per_query)
        except Exception as e:  # noqa: BLE001
            _logger.warning("web_search failed for %r: %s", q, e)
            return q, {"error": str(e), "results": []}

    with ThreadPoolExecutor(
        max_workers=min(4, max(1, len(queries))),
        thread_name_prefix="research-search",
    ) as pool:
        search_responses = list(pool.map(_search_one, queries))

    search_failures = 0
    for _q, resp in search_responses:
        if resp.get("error"):
            search_failures += 1
        for h in resp.get("results", []) or []:
            url = (h.get("url") or "").strip()
            if not url or url in hits_by_url:
                continue
            hits_by_url[url] = h
    stats["search_hits"] = len(hits_by_url)
    stats["search_failures"] = search_failures

    if not hits_by_url:
        return ResearchAnswer(
            question=question,
            answer="",
            queries=queries,
            sources=[],
            used_indices=[],
            invalid_indices=[],
            backend={"rewrite": rr.backend, "rerank": "bm25"},
            stats=stats,
        )

    # ③ Fetch main content per URL. Dead/extract-fail URLs just keep
    # their search snippet so the source isn't lost from the pool.
    def _fetch_one(item: tuple[str, dict[str, Any]]) -> dict[str, Any]:
        url, hit = item
        fetched: dict[str, Any] = {}
        try:
            fetched = fetch_fn(url=url, extract=True)
        except Exception as e:  # noqa: BLE001
            _logger.info("fetch_url failed for %s: %s", url, e)
        return {
            "url": url,
            "title": hit.get("title") or "",
            "snippet": hit.get("snippet") or "",
            "content": fetched.get("content") or "" if fetched.get("extracted") else "",
            "metadata": fetched.get("metadata") or {},
        }

    hit_items = list(hits_by_url.items())
    with ThreadPoolExecutor(
        max_workers=min(6, max(1, len(hit_items))),
        thread_name_prefix="research-fetch",
    ) as pool:
        enriched = list(pool.map(_fetch_one, hit_items))
    stats["fetched"] = sum(1 for e in enriched if e["content"])
    stats["fetch_failures"] = len(enriched) - stats["fetched"]

    # ④ Rerank against the ORIGINAL question (not rewrites — the user's
    # phrasing is the authoritative target for relevance).
    ranked = rerank(question, enriched, top_k=top_k)
    top_sources = ranked.sources
    stats["reranked"] = len(top_sources)

    # ⑤ Build citation prompt + call LLM for synthesis.
    prompt, normalized = render_citation_prompt(
        question,
        top_sources,
        today=today,
    )

    answer_text = ""
    retries_used = 0
    try:
        from runtime.sensing.model_router import Message, ModelRequest

        def _synth(extra_instruction: str = "") -> str:
            content = prompt
            if extra_instruction:
                content = f"{prompt}\n\nIMPORTANT: {extra_instruction}"
            req = ModelRequest(
                model=synth_model,
                messages=[Message(role="user", content=content)],
                max_tokens=synth_max_tokens,
                temperature=0.2,
            )
            r = router.call(req)
            return r.text or ""

        answer_text = _synth()

        # If the first draft cites [n] markers outside [1..K], give the
        # model ONE chance to fix it. More retries rarely help — if it
        # can't self-correct in two passes it's stuck on a misconception.
        for _ in range(max_citation_retries):
            probe = resolve_citations(answer_text, normalized)
            if not probe.invalid_indices:
                break
            bad = ", ".join(f"[{n}]" for n in probe.invalid_indices)
            valid_range = f"[1]..[{len(normalized)}]"
            retries_used += 1
            answer_text = _synth(
                f"Your previous answer cited {bad}, which does not exist. "
                f"Valid citation markers are {valid_range} only. "
                f"Rewrite the answer citing only existing sources; "
                f"drop claims you cannot support."
            )
    except Exception as e:  # noqa: BLE001
        _logger.warning("research synthesis LLM call failed: %s", e)
        answer_text = ""
    stats["citation_retries"] = retries_used

    # ⑥ Resolve [n] citation markers.
    if answer_text:
        res: CitationResolution = resolve_citations(answer_text, normalized)
    else:
        res = CitationResolution(answer="", used_indices=[], used_sources=[], invalid_indices=[])

    return ResearchAnswer(
        question=question,
        answer=answer_text,
        queries=queries,
        sources=normalized,
        used_indices=res.used_indices,
        invalid_indices=res.invalid_indices,
        backend={"rewrite": rr.backend, "rerank": ranked.backend},
        stats=stats,
    )


# ═══════════════════════════════════════════════════════════
# Skill registration
# ═══════════════════════════════════════════════════════════


def research_loop(
    question: str,
    *,
    router: Any,
    search_fn: Callable[..., dict[str, Any]] | None = None,
    fetch_fn: Callable[..., dict[str, Any]] | None = None,
    max_rounds: int = 3,
    n_queries: int = 3,
    hits_per_query: int = 5,
    top_k: int = 5,
    gap_model: str = "claude-haiku-4-5-20251001",
    synth_model: str = "claude-haiku-4-5-20251001",
    rewrite_model: str = "claude-haiku-4-5-20251001",
    synth_max_tokens: int = 1024,
    today: date | None = None,
) -> ResearchAnswer:
    """Multi-round research · Perplexity Pro-style.

    After each round, ask the LLM whether it has enough to answer. If not,
    it proposes follow-up queries; we run them, merge new sources with old,
    re-rerank against the original question, and re-synthesize. Stops when
    the LLM signals done, no new queries are proposed, or we hit
    `max_rounds`.
    """
    question = (question or "").strip()
    if not question:
        return ResearchAnswer(
            question="",
            answer="",
            queries=[],
            sources=[],
            used_indices=[],
            invalid_indices=[],
            backend={"rewrite": "rule", "rerank": "bm25"},
            stats={"rounds": 0},
        )

    # Round 1: standard research_answer.
    current = research_answer(
        question,
        router=router,
        search_fn=search_fn,
        fetch_fn=fetch_fn,
        n_queries=n_queries,
        hits_per_query=hits_per_query,
        top_k=top_k,
        synth_model=synth_model,
        rewrite_model=rewrite_model,
        synth_max_tokens=synth_max_tokens,
        today=today,
    )
    current.stats["rounds"] = 1
    tried_queries: set[str] = {q.lower() for q in current.queries}

    # Pooled enriched sources across rounds, keyed by URL. We keep the raw
    # entries (with content/snippet/metadata) so a fresh rerank can see
    # them all together. `current.sources` is only the top-K after rerank,
    # we need the full pool for future rounds.
    pool: dict[str, dict[str, Any]] = {
        s.url: {
            "url": s.url,
            "title": s.title,
            "snippet": s.snippet,
            "content": s.content,
            "metadata": {"date": s.published, "author": s.author},
        }
        for s in current.sources
    }

    if search_fn is None:
        from runtime.execution.suckers.web_skills import _web_search as _sfn
    else:
        _sfn = search_fn
    if fetch_fn is None:
        from runtime.execution.suckers.web_skills import _fetch_url as _ffn
    else:
        _ffn = fetch_fn

    for round_idx in range(2, max_rounds + 1):
        follow_ups = _decide_follow_ups(
            question=question,
            answer=current.answer,
            sources=current.sources,
            router=router,
            model=gap_model,
            already_tried=tried_queries,
        )
        if not follow_ups:
            break

        # Run the new queries, add hits to pool.
        new_urls_added = 0
        for q in follow_ups:
            tried_queries.add(q.lower())
            try:
                resp = _sfn(query=q, max_results=hits_per_query)
            except Exception as e:  # noqa: BLE001
                _logger.warning("follow-up search failed for %r: %s", q, e)
                continue
            for h in resp.get("results", []) or []:
                url = (h.get("url") or "").strip()
                if not url or url in pool:
                    continue
                fetched: dict[str, Any] = {}
                try:
                    fetched = _ffn(url=url, extract=True)
                except Exception as e:  # noqa: BLE001
                    _logger.info("follow-up fetch failed for %s: %s", url, e)
                pool[url] = {
                    "url": url,
                    "title": h.get("title") or "",
                    "snippet": h.get("snippet") or "",
                    "content": (fetched.get("content") or "" if fetched.get("extracted") else ""),
                    "metadata": fetched.get("metadata") or {},
                }
                new_urls_added += 1

        if new_urls_added == 0:
            # Nothing new to consider — LLM would re-draft on the same
            # sources and reach the same conclusion. Save the round.
            break

        # Re-rerank the full pool against the original question, re-synth.
        ranked = rerank(question, list(pool.values()), top_k=top_k)
        prompt, normalized = render_citation_prompt(
            question,
            ranked.sources,
            today=today,
        )
        new_answer = ""
        try:
            from runtime.sensing.model_router import Message, ModelRequest

            def _synth_round(extra: str = "") -> str:
                # ``prompt`` is captured from this loop iteration but
                # ``_synth_round`` is invoked synchronously below — never
                # stored, never deferred — so the usual late-binding pitfall
                # doesn't apply.
                content = prompt if not extra else f"{prompt}\n\nIMPORTANT: {extra}"  # noqa: B023
                req = ModelRequest(
                    model=synth_model,
                    messages=[Message(role="user", content=content)],
                    max_tokens=synth_max_tokens,
                    temperature=0.2,
                )
                r = router.call(req)
                return r.text or ""

            new_answer = _synth_round()
            probe = resolve_citations(new_answer, normalized)
            if probe.invalid_indices:
                bad = ", ".join(f"[{n}]" for n in probe.invalid_indices)
                new_answer = _synth_round(
                    f"Previous answer cited {bad}, which does not exist. "
                    f"Valid citations are [1]..[{len(normalized)}] only."
                )
        except Exception as e:  # noqa: BLE001
            _logger.warning("round %d synthesis failed: %s", round_idx, e)
            new_answer = current.answer  # keep previous draft

        res = (
            resolve_citations(new_answer, normalized)
            if new_answer
            else CitationResolution(answer="", used_indices=[], used_sources=[], invalid_indices=[])
        )

        current = ResearchAnswer(
            question=question,
            answer=new_answer,
            queries=current.queries + follow_ups,
            sources=normalized,
            used_indices=res.used_indices,
            invalid_indices=res.invalid_indices,
            backend={"rewrite": current.backend.get("rewrite", "rule"), "rerank": ranked.backend},
            stats={
                **current.stats,
                "rounds": round_idx,
                "pool_size": len(pool),
                "reranked": len(normalized),
            },
        )

    return current


def _decide_follow_ups(
    *,
    question: str,
    answer: str,
    sources: list[SourceEntry],
    router: Any,
    model: str,
    already_tried: set[str],
    max_follow_ups: int = 3,
) -> list[str]:
    """Ask the LLM: is the answer good enough, and if not what else to search?"""
    if not answer:
        # No draft to critique → don't loop blindly; the first round
        # failure (no search hits / synthesis crash) won't be fixed by
        # another LLM call.
        return []
    if not sources:
        return []

    from runtime.platform.prompts import get_prompt

    summary_lines = [f"[{i + 1}] {s.title or s.url} — {s.url}" for i, s in enumerate(sources)]
    prompt = (
        get_prompt("research_gap_analysis")
        .replace("{question}", question)
        .replace("{answer}", answer)
        .replace("{sources_summary}", "\n".join(summary_lines) or "(none)")
    )

    try:
        from runtime.sensing.model_router import Message, ModelRequest

        req = ModelRequest(
            model=model,
            messages=[Message(role="user", content=prompt)],
            max_tokens=256,
            temperature=0.1,
        )
        resp = router.call(req)
        text = resp.text or ""
    except Exception as e:  # noqa: BLE001
        _logger.warning("gap analysis LLM call failed: %s", e)
        return []

    parsed = _parse_gap_decision(text)
    if parsed.get("done") is True:
        return []

    raw_queries = parsed.get("follow_up_queries") or []
    out: list[str] = []
    for q in raw_queries:
        if not isinstance(q, str):
            continue
        norm = q.strip()
        if not norm or norm.lower() in already_tried:
            continue
        out.append(norm)
        if len(out) >= max_follow_ups:
            break
    return out


def _parse_gap_decision(text: str) -> dict[str, Any]:
    """Extract {'done': bool, 'follow_up_queries': [...]} from LLM output."""
    import json

    if not text:
        return {}
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            c = text[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : i + 1]
                    try:
                        data = json.loads(candidate)
                    except json.JSONDecodeError:
                        break
                    if isinstance(data, dict):
                        return data
                    break
        start = text.find("{", start + 1)
    return {}


def register_research_skill(
    registry: Any,
    router: Any,
    *,
    default_n_queries: int = 3,
    default_hits_per_query: int = 5,
    default_top_k: int = 5,
    synth_model: str = "claude-haiku-4-5-20251001",
    rewrite_model: str = "claude-haiku-4-5-20251001",
) -> int:
    """Register `research_answer` as a callable skill. Returns 1 on success."""
    from runtime.execution.suckers.registry import Skill

    def _handler(
        question: str = "",
        *,
        n_queries: int = default_n_queries,
        hits_per_query: int = default_hits_per_query,
        top_k: int = default_top_k,
        **_kw: Any,
    ) -> dict[str, Any]:
        if not question:
            return {"error": "missing question"}
        try:
            result = research_answer(
                question,
                router=router,
                n_queries=n_queries,
                hits_per_query=hits_per_query,
                top_k=top_k,
                synth_model=synth_model,
                rewrite_model=rewrite_model,
            )
        except Exception as e:  # noqa: BLE001
            _logger.exception("research_answer handler failed")
            return {"error": f"{type(e).__name__}: {e}"}
        return result.to_json()

    registry.register(
        Skill(
            name="research_answer",
            description=(
                "End-to-end research: rewrites the question into search "
                "queries, calls web_search across all of them, fetches and "
                "cleans the top pages (trafilatura main-content extraction), "
                "reranks by BM25 (or Cohere if COHERE_API_KEY is set), and "
                "synthesizes a cited answer with [n] markers linking back "
                "to the source URLs. Prefer this over manual "
                "web_search+fetch_url chains for any open-ended factual "
                "question. Args: {question: string, n_queries?: int, "
                "hits_per_query?: int, top_k?: int}."
            ),
            affinity=["web", "research", "synthesis"],
            cost_profile="high",  # Hits the LLM at least twice + ~N fetches.
            trusted_source="skill://public/research_answer",
            handler=_handler,
            tests=[],
        ),
        verify_tests=False,
    )
    return 1


def register_deep_research_skill(
    registry: Any,
    router: Any,
    *,
    default_max_rounds: int = 3,
    default_n_queries: int = 3,
    default_hits_per_query: int = 5,
    default_top_k: int = 5,
    gap_model: str = "claude-haiku-4-5-20251001",
    synth_model: str = "claude-haiku-4-5-20251001",
    rewrite_model: str = "claude-haiku-4-5-20251001",
) -> int:
    """Register `deep_research_answer` (multi-round). Returns 1."""
    from runtime.execution.suckers.registry import Skill

    def _handler(
        question: str = "",
        *,
        max_rounds: int = default_max_rounds,
        n_queries: int = default_n_queries,
        hits_per_query: int = default_hits_per_query,
        top_k: int = default_top_k,
        **_kw: Any,
    ) -> dict[str, Any]:
        if not question:
            return {"error": "missing question"}
        if max_rounds < 1 or max_rounds > 10:
            return {"error": f"max_rounds out of range: {max_rounds}"}
        try:
            result = research_loop(
                question,
                router=router,
                max_rounds=max_rounds,
                n_queries=n_queries,
                hits_per_query=hits_per_query,
                top_k=top_k,
                gap_model=gap_model,
                synth_model=synth_model,
                rewrite_model=rewrite_model,
            )
        except Exception as e:  # noqa: BLE001
            _logger.exception("deep_research_answer handler failed")
            return {"error": f"{type(e).__name__}: {e}"}
        return result.to_json()

    registry.register(
        Skill(
            name="deep_research_answer",
            description=(
                "Multi-round research (Perplexity Pro-style). Same as "
                "research_answer but after each draft, the model decides "
                "whether more searches would help and runs them, merging "
                "new sources before re-synthesizing. Costlier (2-3x LLM "
                "calls + extra fetches) — prefer this over research_answer "
                "ONLY for open-ended/comparative questions where one round "
                'is unlikely to suffice (e.g. "compare X vs Y vs Z", '
                '"what\'s the current state of <fast-moving topic>"). '
                "Args: {question: string, max_rounds?: int (1-10, default 3), "
                "n_queries?: int, hits_per_query?: int, top_k?: int}."
            ),
            affinity=["web", "research", "synthesis", "deep"],
            cost_profile="high",
            trusted_source="skill://public/deep_research_answer",
            handler=_handler,
            tests=[],
        ),
        verify_tests=False,
    )
    return 1


__all__ = [
    "ResearchAnswer",
    "research_answer",
    "research_loop",
    "register_research_skill",
    "register_deep_research_skill",
]
