"""End-to-end tests for the research_answer pipeline."""

from __future__ import annotations

from datetime import date
from typing import Any

from runtime.execution.suckers.registry import SkillRegistry
from runtime.research.pipeline import (
    register_deep_research_skill,
    register_research_skill,
    research_answer,
    research_loop,
)
from runtime.sensing.model_router import MockModelRouter


def _router(
    rewrite_reply: str = '["alt query 1", "alt query 2"]',
    synth_reply: str = "Answer with citations [1][2].",
) -> MockModelRouter:
    def _fn(req):
        prompt = req.messages[0].content if req.messages else ""
        low = prompt.lower()
        if "research assistant" in low:
            return synth_reply
        # Any other prompt is the query_rewrite call.
        return rewrite_reply

    return MockModelRouter(response_fn=_fn)


def _fake_search(results_by_query: dict[str, list[dict]] | list[dict]):
    """Returns a search_fn that either uses a per-query map or a single list."""

    def _search(query: str = "", max_results: int = 5, **_):
        if isinstance(results_by_query, dict):
            hits = results_by_query.get(query, [])
        else:
            hits = results_by_query
        return {"results": hits[:max_results]}

    return _search


def _fake_fetch(content_by_url: dict[str, str] | None = None, fail_urls: set[str] | None = None):
    content_by_url = content_by_url or {}
    fail_urls = fail_urls or set()

    def _fetch(url: str = "", extract: bool = False, **_):
        if url in fail_urls:
            raise RuntimeError("network boom")
        body = content_by_url.get(url, f"default body for {url}")
        return {
            "extracted": True,
            "content": body,
            "metadata": {"date": "2026-01-01"},
        }

    return _fetch


# ═══════════════════════════════════════════════════════════
# research_answer() end-to-end
# ═══════════════════════════════════════════════════════════


class TestResearchAnswerHappyPath:
    def test_full_pipeline(self):
        router = _router()
        search = _fake_search(
            [
                {"url": "https://a.example/1", "title": "Python sort", "snippet": "sorted()"},
                {"url": "https://a.example/2", "title": "Sort tutorial", "snippet": "list.sort()"},
            ]
        )
        fetch = _fake_fetch(
            {
                "https://a.example/1": "How to sort python lists with sorted().",
                "https://a.example/2": "The list.sort() method sorts in place.",
            }
        )
        r = research_answer(
            "how to sort a python list",
            router=router,
            search_fn=search,
            fetch_fn=fetch,
            n_queries=2,
            hits_per_query=2,
            top_k=2,
            today=date(2026, 5, 9),
        )
        assert r.question == "how to sort a python list"
        assert r.answer == "Answer with citations [1][2]."
        assert len(r.sources) == 2
        assert r.used_indices == [1, 2]
        assert r.invalid_indices == []
        assert r.backend == {"rewrite": "llm", "rerank": "bm25"}
        assert r.stats["queries"] >= 2
        assert r.stats["search_hits"] >= 2

    def test_dedupes_urls_across_queries(self):
        router = _router()
        shared_hit = {
            "url": "https://shared.example/",
            "title": "Shared",
            "snippet": "appears twice",
        }
        unique_hit = {"url": "https://unique.example/", "title": "Unique", "snippet": "only once"}
        search = _fake_search(
            {
                "original q": [shared_hit, unique_hit],
                "alt query 1": [shared_hit],
                "alt query 2": [shared_hit],
            }
        )
        fetch = _fake_fetch()
        r = research_answer(
            "original q",
            router=router,
            search_fn=search,
            fetch_fn=fetch,
            n_queries=3,
            hits_per_query=5,
            top_k=5,
        )
        # Shared URL must appear exactly once in the final sources.
        urls = [s.url for s in r.sources]
        assert urls.count("https://shared.example/") == 1
        assert "https://unique.example/" in urls

    def test_fetch_failure_falls_back_to_snippet(self):
        router = _router()
        search = _fake_search(
            [
                {
                    "url": "https://dead.example/",
                    "title": "Dead",
                    "snippet": "only snippet survives",
                },
                {"url": "https://alive.example/", "title": "Alive", "snippet": "hi"},
            ]
        )
        fetch = _fake_fetch(fail_urls={"https://dead.example/"})
        r = research_answer(
            "q",
            router=router,
            search_fn=search,
            fetch_fn=fetch,
            n_queries=1,
            hits_per_query=5,
            top_k=5,
        )
        urls = [s.url for s in r.sources]
        assert "https://dead.example/" in urls
        # the dead URL has snippet but no body
        dead = next(s for s in r.sources if s.url == "https://dead.example/")
        assert "only snippet survives" in dead.snippet
        assert dead.content == ""


# ═══════════════════════════════════════════════════════════
# Failure modes
# ═══════════════════════════════════════════════════════════


class TestResearchAnswerFailures:
    def test_empty_question(self):
        r = research_answer("", router=_router())
        assert r.answer == ""
        assert r.sources == []
        assert r.queries == []

    def test_no_search_hits(self):
        router = _router()
        search = _fake_search([])
        fetch = _fake_fetch()
        r = research_answer(
            "obscure q",
            router=router,
            search_fn=search,
            fetch_fn=fetch,
        )
        assert r.answer == ""
        assert r.sources == []
        assert r.stats["search_hits"] == 0

    def test_search_exception_skips_that_query(self):
        router = _router()

        def search(query="", max_results=5, **_):
            if query == "alt query 1":
                raise RuntimeError("search provider down")
            return {
                "results": [
                    {"url": f"https://ok.example/{query}", "title": "OK", "snippet": "hi"},
                ]
            }

        fetch = _fake_fetch()
        r = research_answer(
            "original q",
            router=router,
            search_fn=search,
            fetch_fn=fetch,
            n_queries=3,
            hits_per_query=5,
            top_k=5,
        )
        # Two queries succeed ("original q" + "alt query 2"), one failed.
        assert len(r.sources) == 2

    def test_synthesis_llm_failure_returns_sources_only(self):
        # Router answers query_rewrite but crashes on synthesis prompt.
        def _fn(req):
            prompt = req.messages[0].content if req.messages else ""
            if "research assistant" in prompt.lower():
                raise RuntimeError("LLM down")
            return '["alt"]'

        router = MockModelRouter(response_fn=_fn)
        search = _fake_search(
            [
                {"url": "https://a.example/", "title": "t", "snippet": "s"},
            ]
        )
        fetch = _fake_fetch()
        r = research_answer(
            "q",
            router=router,
            search_fn=search,
            fetch_fn=fetch,
        )
        assert r.answer == ""
        assert len(r.sources) >= 1
        assert r.used_indices == []


# ═══════════════════════════════════════════════════════════
# ResearchAnswer.to_json
# ═══════════════════════════════════════════════════════════


class TestResearchAnswerSerialization:
    def test_to_json_schema(self):
        router = _router()
        search = _fake_search(
            [
                {"url": "https://a.example/", "title": "T", "snippet": "S"},
            ]
        )
        r = research_answer(
            "q",
            router=router,
            search_fn=search,
            fetch_fn=_fake_fetch(),
        )
        j = r.to_json()
        assert set(j.keys()) >= {
            "question",
            "answer",
            "queries",
            "sources",
            "used_indices",
            "invalid_indices",
            "backend",
            "stats",
        }
        assert isinstance(j["sources"], list)
        if j["sources"]:
            assert j["sources"][0]["n"] == 1
            assert "url" in j["sources"][0]


# ═══════════════════════════════════════════════════════════
# Skill registration
# ═══════════════════════════════════════════════════════════


class TestResearchSkillRegistration:
    def test_registers_and_invokes(self, monkeypatch):
        # Patch the lazy imports inside pipeline so the skill handler's
        # real defaults (search_fn=_web_search, fetch_fn=_fetch_url) are
        # swapped for our fakes.
        import runtime.execution.suckers.web_skills as web_skills

        monkeypatch.setattr(
            web_skills,
            "_web_search",
            _fake_search(
                [
                    {"url": "https://a.example/", "title": "T", "snippet": "S"},
                ]
            ),
        )
        monkeypatch.setattr(web_skills, "_fetch_url", _fake_fetch())

        registry = SkillRegistry()
        n = register_research_skill(registry, router=_router())
        assert n == 1
        assert registry.has("research_answer")

        skill = registry.get("research_answer")
        out = skill.handler(question="what is x", n_queries=1, top_k=2)
        assert isinstance(out, dict)
        assert out["question"] == "what is x"
        assert out["answer"] == "Answer with citations [1][2]."
        assert out["sources"]
        assert out["backend"]["rewrite"] == "llm"

    def test_missing_question_returns_error(self):
        registry = SkillRegistry()
        register_research_skill(registry, router=_router())
        out = registry.get("research_answer").handler(question="")
        assert out == {"error": "missing question"}

    def test_handler_error_captured(self, monkeypatch):
        # Force research_answer to raise.
        import runtime.research.pipeline as pipeline

        def _boom(*a, **kw):
            raise RuntimeError("synthetic")

        monkeypatch.setattr(pipeline, "research_answer", _boom)

        registry = SkillRegistry()
        register_research_skill(registry, router=_router())
        out = registry.get("research_answer").handler(question="q")
        assert "error" in out
        assert "synthetic" in out["error"]


# ═══════════════════════════════════════════════════════════
# Citation retry
# ═══════════════════════════════════════════════════════════


class TestCitationRetry:
    def test_retries_when_invalid_marker_emitted(self):
        # First synthesis returns an invalid [99]; retry returns valid [1].
        call_log: list[str] = []

        def _fn(req):
            prompt = req.messages[0].content if req.messages else ""
            low = prompt.lower()
            if "research assistant" in low:
                call_log.append(prompt)
                if len(call_log) == 1:
                    return "Answer with bad citation [99]."
                # Retry — prompt should include "IMPORTANT" correction hint
                assert "IMPORTANT" in prompt
                assert "[99]" in prompt
                return "Corrected answer [1]."
            return '["alt"]'  # query rewrite

        router = MockModelRouter(response_fn=_fn)
        search = _fake_search(
            [
                {"url": "https://a.example/", "title": "T", "snippet": "S"},
            ]
        )
        r = research_answer(
            "q",
            router=router,
            search_fn=search,
            fetch_fn=_fake_fetch(),
            max_citation_retries=1,
        )
        assert r.answer == "Corrected answer [1]."
        assert r.used_indices == [1]
        assert r.invalid_indices == []
        assert r.stats.get("citation_retries") == 1

    def test_no_retry_when_all_citations_valid(self):
        router = _router(synth_reply="Answer [1].")
        search = _fake_search(
            [
                {"url": "https://a.example/", "title": "T", "snippet": "S"},
            ]
        )
        r = research_answer(
            "q",
            router=router,
            search_fn=search,
            fetch_fn=_fake_fetch(),
            max_citation_retries=1,
        )
        assert r.stats.get("citation_retries") == 0

    def test_retries_disabled_by_max_zero(self):
        router = _router(synth_reply="Bad [99].")
        search = _fake_search(
            [
                {"url": "https://a.example/", "title": "T", "snippet": "S"},
            ]
        )
        r = research_answer(
            "q",
            router=router,
            search_fn=search,
            fetch_fn=_fake_fetch(),
            max_citation_retries=0,
        )
        # invalid marker left intact when retries disabled
        assert r.invalid_indices == [99]
        assert r.stats.get("citation_retries") == 0


# ═══════════════════════════════════════════════════════════
# research_loop — multi-round
# ═══════════════════════════════════════════════════════════


class TestResearchLoop:
    def _loop_router(
        self,
        round1_answer: str,
        gap_decisions: list[dict[str, Any]],
        later_answers: list[str],
    ) -> MockModelRouter:
        """gap_decisions: list of {'done': bool, 'follow_up_queries': [...]}.
        later_answers: synthesis replies for round 2, 3, ...
        """
        rewrite_done = {"v": False}
        gap_idx = {"v": 0}
        synth_idx = {"v": 0}

        def _fn(req):
            prompt = req.messages[0].content if req.messages else ""
            low = prompt.lower()
            if "research assistant" in low:
                # synthesis
                if synth_idx["v"] == 0:
                    synth_idx["v"] += 1
                    return round1_answer
                reply = (
                    later_answers[synth_idx["v"] - 1]
                    if synth_idx["v"] - 1 < len(later_answers)
                    else round1_answer
                )
                synth_idx["v"] += 1
                return reply
            if "gap" in low or "review a draft" in low:
                # gap analysis
                import json

                dec = (
                    gap_decisions[gap_idx["v"]]
                    if gap_idx["v"] < len(gap_decisions)
                    else {"done": True, "follow_up_queries": []}
                )
                gap_idx["v"] += 1
                return json.dumps(dec)
            # query rewrite (first non-synth, non-gap call)
            if not rewrite_done["v"]:
                rewrite_done["v"] = True
                return '["alt"]'
            return '["alt"]'

        return MockModelRouter(response_fn=_fn)

    def test_stops_when_llm_says_done(self):
        router = self._loop_router(
            round1_answer="Good answer [1].",
            gap_decisions=[{"done": True, "follow_up_queries": []}],
            later_answers=[],
        )
        search = _fake_search(
            [
                {"url": "https://a.example/", "title": "T", "snippet": "S"},
            ]
        )
        r = research_loop(
            "q",
            router=router,
            search_fn=search,
            fetch_fn=_fake_fetch(),
            max_rounds=3,
        )
        assert r.stats["rounds"] == 1
        assert r.answer == "Good answer [1]."

    def test_runs_followup_round_and_merges_sources(self):
        call_log: list[str] = []

        def _search(query="", max_results=5, **_):
            call_log.append(query)
            if query == "missing_fact":
                return {
                    "results": [
                        {"url": "https://new.example/", "title": "New", "snippet": "newly found"},
                    ]
                }
            return {
                "results": [
                    {"url": "https://a.example/", "title": "Old", "snippet": "S"},
                ]
            }

        router = self._loop_router(
            round1_answer="Partial answer [1].",
            gap_decisions=[
                {"done": False, "follow_up_queries": ["missing_fact"]},
                {"done": True, "follow_up_queries": []},
            ],
            later_answers=["Complete answer [1][2]."],
        )
        r = research_loop(
            "q",
            router=router,
            search_fn=_search,
            fetch_fn=_fake_fetch(),
            max_rounds=3,
        )
        assert r.stats["rounds"] == 2
        assert "missing_fact" in call_log
        urls = [s.url for s in r.sources]
        assert "https://new.example/" in urls
        assert r.answer == "Complete answer [1][2]."

    def test_hits_max_rounds(self):
        router = self._loop_router(
            round1_answer="Draft 1 [1].",
            gap_decisions=[
                {"done": False, "follow_up_queries": ["fu1"]},
                {"done": False, "follow_up_queries": ["fu2"]},
                {"done": False, "follow_up_queries": ["fu3"]},
                {"done": False, "follow_up_queries": ["fu4"]},
            ],
            later_answers=["Draft 2 [1].", "Draft 3 [1].", "Draft 4 [1]."],
        )

        # Each follow-up query returns a fresh URL so new_urls_added > 0.
        def _search(query="", max_results=5, **_):
            return {
                "results": [
                    {"url": f"https://{query}.example/", "title": query, "snippet": "s"},
                ]
            }

        r = research_loop(
            "q",
            router=router,
            search_fn=_search,
            fetch_fn=_fake_fetch(),
            max_rounds=2,
        )
        assert r.stats["rounds"] == 2  # capped

    def test_stops_when_no_new_urls(self):
        # Follow-up query returns only URLs already in pool → no progress.
        router = self._loop_router(
            round1_answer="Draft [1].",
            gap_decisions=[{"done": False, "follow_up_queries": ["again"]}],
            later_answers=[],
        )
        shared = {"url": "https://a.example/", "title": "T", "snippet": "S"}

        def _search(query="", max_results=5, **_):
            return {"results": [shared]}

        r = research_loop(
            "q",
            router=router,
            search_fn=_search,
            fetch_fn=_fake_fetch(),
            max_rounds=5,
        )
        # Round 1 succeeded; round 2 produced 0 new URLs → loop broke.
        assert r.stats["rounds"] == 1
        assert r.answer == "Draft [1]."

    def test_empty_question_returns_early(self):
        r = research_loop("", router=_router())
        assert r.question == ""
        assert r.sources == []
        assert r.stats.get("rounds") == 0

    def test_drops_already_tried_followups(self):
        call_log: list[str] = []

        def _search(query="", max_results=5, **_):
            call_log.append(query)
            return {
                "results": [
                    {"url": f"https://{query}.example/", "title": query, "snippet": "s"},
                ]
            }

        # Follow-up proposes the original question's rewrite again — must be
        # filtered out.
        router = self._loop_router(
            round1_answer="Draft [1].",
            gap_decisions=[{"done": False, "follow_up_queries": ["alt"]}],
            later_answers=["Draft 2 [1]."],
        )
        r = research_loop(
            "q",
            router=router,
            search_fn=_search,
            fetch_fn=_fake_fetch(),
            max_rounds=3,
        )
        # "alt" was in round 1 rewrite, must not run again in round 2.
        assert call_log.count("alt") == 1
        # Loop terminated at round 1 (no new queries) so still 1 round.
        assert r.stats["rounds"] == 1


# ═══════════════════════════════════════════════════════════
# deep_research_answer skill
# ═══════════════════════════════════════════════════════════


class TestDeepResearchSkill:
    def test_registers_and_runs_single_round(self, monkeypatch):
        # Loop terminates after round 1 because gap analysis says done.
        def _fn(req):
            prompt = req.messages[0].content if req.messages else ""
            low = prompt.lower()
            if "research assistant" in low:
                return "Final answer [1]."
            if "review a draft" in low:
                return '{"done": true, "follow_up_queries": []}'
            return '["alt"]'

        router = MockModelRouter(response_fn=_fn)

        import runtime.execution.suckers.web_skills as web_skills

        monkeypatch.setattr(
            web_skills,
            "_web_search",
            _fake_search(
                [
                    {"url": "https://a.example/", "title": "T", "snippet": "S"},
                ]
            ),
        )
        monkeypatch.setattr(web_skills, "_fetch_url", _fake_fetch())

        registry = SkillRegistry()
        n = register_deep_research_skill(registry, router=router)
        assert n == 1
        assert registry.has("deep_research_answer")

        skill = registry.get("deep_research_answer")
        out = skill.handler(question="what is x", max_rounds=3)
        assert isinstance(out, dict)
        assert out["question"] == "what is x"
        assert out["answer"] == "Final answer [1]."
        assert out["stats"]["rounds"] == 1

    def test_missing_question(self):
        registry = SkillRegistry()
        register_deep_research_skill(registry, router=_router())
        out = registry.get("deep_research_answer").handler(question="")
        assert out == {"error": "missing question"}

    def test_max_rounds_out_of_range(self):
        registry = SkillRegistry()
        register_deep_research_skill(registry, router=_router())
        handler = registry.get("deep_research_answer").handler
        assert "out of range" in handler(question="q", max_rounds=0)["error"]
        assert "out of range" in handler(question="q", max_rounds=11)["error"]

    def test_handler_error_captured(self, monkeypatch):
        import runtime.research.pipeline as pipeline

        def _boom(*a, **kw):
            raise RuntimeError("deep synthetic")

        monkeypatch.setattr(pipeline, "research_loop", _boom)

        registry = SkillRegistry()
        register_deep_research_skill(registry, router=_router())
        out = registry.get("deep_research_answer").handler(question="q")
        assert "error" in out
        assert "deep synthetic" in out["error"]

    def test_default_max_rounds_override(self, monkeypatch):
        # Registering with a custom default, passing no max_rounds from
        # planner should use that default.
        captured = {}

        def _capture(*a, **kw):
            captured["max_rounds"] = kw.get("max_rounds")
            # Return a minimal valid ResearchAnswer
            from runtime.research.pipeline import ResearchAnswer

            return ResearchAnswer(
                question=a[0] if a else "",
                answer="ok",
                queries=[],
                sources=[],
                used_indices=[],
                invalid_indices=[],
                backend={"rewrite": "rule", "rerank": "bm25"},
                stats={"rounds": 1},
            )

        import runtime.research.pipeline as pipeline

        monkeypatch.setattr(pipeline, "research_loop", _capture)

        registry = SkillRegistry()
        register_deep_research_skill(
            registry,
            router=_router(),
            default_max_rounds=5,
        )
        registry.get("deep_research_answer").handler(question="q")
        assert captured["max_rounds"] == 5
