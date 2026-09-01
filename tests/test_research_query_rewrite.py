"""Tests for query rewriting (LLM-backed + rule fallback)."""

from __future__ import annotations

from datetime import date

from runtime.research.query_rewrite import (
    _parse_query_array,
    rewrite_query,
    rule_based_rewrite,
)
from runtime.sensing.model_router import MockModelRouter

# ═══════════════════════════════════════════════════════════
# _parse_query_array — JSON extraction
# ═══════════════════════════════════════════════════════════


class TestParseQueryArray:
    def test_bare_json_array(self):
        assert _parse_query_array('["a", "b", "c"]') == ["a", "b", "c"]

    def test_json_array_in_prose(self):
        raw = 'Sure, here you go:\n["q1", "q2"]\nGood luck!'
        assert _parse_query_array(raw) == ["q1", "q2"]

    def test_strips_and_skips_empty(self):
        assert _parse_query_array('["  hi  ", "", "there"]') == ["hi", "there"]

    def test_numbered_list_fallback(self):
        raw = "1. alpha\n2. beta\n3. gamma"
        assert _parse_query_array(raw) == ["alpha", "beta", "gamma"]

    def test_bullet_list_fallback(self):
        raw = "- one\n- two words here\n- three"
        assert _parse_query_array(raw) == ["one", "two words here", "three"]

    def test_plain_prose_returns_empty(self):
        assert _parse_query_array("just some prose, no list.") == []

    def test_invalid_json_array_falls_through(self):
        # Malformed JSON but looks-like-array → parser skips, no bullet
        # lines → empty
        assert _parse_query_array("[not valid json here]") == []

    def test_empty_input(self):
        assert _parse_query_array("") == []


# ═══════════════════════════════════════════════════════════
# rule_based_rewrite
# ═══════════════════════════════════════════════════════════


class TestRuleBasedRewrite:
    def test_original_always_first(self):
        out = rule_based_rewrite("Python list sort")
        assert out[0] == "Python list sort"

    def test_strips_english_fillers(self):
        out = rule_based_rewrite("how to sort a list in python")
        assert "sort a list in python" in out

    def test_strips_chinese_fillers(self):
        out = rule_based_rewrite("请问鲁迅生平")
        assert "鲁迅生平" in out

    def test_appends_year_for_time_sensitive_en(self):
        out = rule_based_rewrite("latest GPT models")
        year = date.today().year
        assert any(str(year) in q for q in out)

    def test_appends_year_for_time_sensitive_cn(self):
        out = rule_based_rewrite("苹果股价 最近")
        year = date.today().year
        assert any(str(year) in q for q in out)

    def test_no_year_for_non_time_sensitive(self):
        out = rule_based_rewrite("capital of France")
        year = date.today().year
        assert not any(str(year) in q for q in out)

    def test_dedupes_when_strip_is_noop(self):
        out = rule_based_rewrite("python sort")
        assert out == ["python sort"]

    def test_caps_at_n(self):
        # time-sensitive Chinese would normally yield 2-3 rewrites
        out = rule_based_rewrite("请问 最近 AI 动态", n=2)
        assert len(out) <= 2

    def test_empty_returns_empty(self):
        assert rule_based_rewrite("") == []
        assert rule_based_rewrite("   ") == []


# ═══════════════════════════════════════════════════════════
# rewrite_query — LLM path + fallback
# ═══════════════════════════════════════════════════════════


class TestRewriteQueryLLM:
    def test_no_router_uses_rule_path(self):
        r = rewrite_query("latest GPT models")
        assert r.backend == "rule"
        assert r.queries[0] == "latest GPT models"

    def test_llm_json_response(self):
        mock = MockModelRouter(response='["AAPL stock price today", "Apple Q1 2026 earnings"]')
        r = rewrite_query("苹果股票最近怎么样", router=mock, n=3)
        assert r.backend == "llm"
        # original preserved first
        assert r.queries[0] == "苹果股票最近怎么样"
        assert "AAPL stock price today" in r.queries
        assert len(r.queries) <= 3

    def test_llm_prose_response_falls_back(self):
        mock = MockModelRouter(response="Just search for what you want.")
        r = rewrite_query("Apple 最近", router=mock)
        assert r.backend == "rule"
        assert r.queries[0] == "Apple 最近"

    def test_llm_router_exception_falls_back(self):
        class _Boom:
            def call(self, req):
                raise RuntimeError("network down")

        r = rewrite_query("the latest news", router=_Boom(), n=3)
        assert r.backend == "rule"
        assert r.queries[0] == "the latest news"

    def test_respects_n_cap(self):
        mock = MockModelRouter(response='["a","b","c","d","e","f"]')
        r = rewrite_query("seed", router=mock, n=3)
        assert len(r.queries) == 3
        assert r.queries[0] == "seed"

    def test_deduplicates_with_original(self):
        mock = MockModelRouter(response='["Seed", "SEED", "something else"]')
        r = rewrite_query("seed", router=mock, n=5)
        # case-insensitive dedup — only one of {seed, Seed, SEED} survives
        lowered = [q.lower() for q in r.queries]
        assert lowered.count("seed") == 1
        assert "something else" in r.queries

    def test_passes_prompt_substitutions(self):
        # Capture the rendered prompt to verify the template is filled in.
        captured = {}

        def _fn(req):
            captured["prompt"] = req.messages[0].content
            return '["q1", "q2"]'

        mock = MockModelRouter(response_fn=_fn)
        rewrite_query("my question", router=mock, n=4, today=date(2026, 5, 9))
        prompt = captured["prompt"]
        assert "my question" in prompt
        assert "2026-05-09" in prompt
        # {n} should have been filled — verify by checking the prompt
        # contains the integer and NOT the literal placeholder
        assert "{n}" not in prompt
        assert "4" in prompt

    def test_empty_question_returns_empty(self):
        r = rewrite_query("")
        assert r.queries == []
        assert r.backend == "rule"
