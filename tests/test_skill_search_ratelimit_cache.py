"""Tests for skill search, rate limiting, and prompt cache:

1. Semantic Skill Search (TF-IDF)
2. Skill Rate Limiter (token bucket)
3. Anthropic Prompt Cache Hints
"""

from __future__ import annotations

import pytest

from runtime.execution.suckers.registry import Skill, SkillRegistry

# ═══════════════════════════════════════════════════════════════
# 1. Semantic Skill Search
# ═══════════════════════════════════════════════════════════════


def _dummy_handler(**_kw):
    return {}


def _make_skill(
    name: str,
    description: str = "",
    summary: str = "",
    affinity: list[str] | None = None,
) -> Skill:
    return Skill(
        name=name,
        description=description,
        summary=summary,
        affinity=affinity or [],
        trusted_source=f"builtin://{name}",
        handler=_dummy_handler,
    )


class TestSemanticSkillSearch:
    @pytest.fixture
    def populated_registry(self):
        reg = SkillRegistry()
        reg.register(
            _make_skill(
                "read_file",
                description="Read the contents of a file from disk.",
                summary="Read a file.",
            ),
            verify_tests=False,
        )
        reg.register(
            _make_skill(
                "write_file",
                description="Write content to a file on disk.",
                summary="Write a file.",
            ),
            verify_tests=False,
        )
        reg.register(
            _make_skill(
                "web_search",
                description="Search the web and return top results with URLs.",
                summary="Search the web.",
            ),
            verify_tests=False,
        )
        reg.register(
            _make_skill(
                "exec_shell",
                description="Execute a shell command and return stdout.",
                summary="Run shell.",
            ),
            verify_tests=False,
        )
        reg.register(
            _make_skill(
                "fetch_url",
                description="Download the HTML page at a given URL.",
                summary="Fetch a URL.",
            ),
            verify_tests=False,
        )
        return reg

    def test_search_file_query_ranks_file_skills_first(self, populated_registry):
        from runtime.execution.suckers.search import TfIdfSkillSearcher

        s = TfIdfSkillSearcher(populated_registry)
        top = s.search("read the config file", k=3)
        # The top result should be file-related.
        assert top[0] in {"read_file", "write_file"}

    def test_search_web_query_ranks_web_skills_first(self, populated_registry):
        from runtime.execution.suckers.search import TfIdfSkillSearcher

        s = TfIdfSkillSearcher(populated_registry)
        top = s.search("search the web for news", k=2)
        assert top[0] == "web_search"

    def test_search_returns_at_most_k(self, populated_registry):
        from runtime.execution.suckers.search import TfIdfSkillSearcher

        s = TfIdfSkillSearcher(populated_registry)
        top = s.search("file", k=2)
        assert len(top) <= 2

    def test_empty_query_returns_all_enabled(self, populated_registry):
        from runtime.execution.suckers.search import TfIdfSkillSearcher

        s = TfIdfSkillSearcher(populated_registry)
        top = s.search("", k=10)
        assert len(top) == 5  # all 5 skills

    def test_refresh_rebuilds_index(self, populated_registry):
        from runtime.execution.suckers.search import TfIdfSkillSearcher

        s = TfIdfSkillSearcher(populated_registry)
        s.search("file")  # build index
        # Add a new skill.
        populated_registry.register(
            _make_skill(
                "grep",
                description="Search files for a regex pattern.",
                summary="Grep files.",
            ),
            verify_tests=False,
        )
        s.refresh()
        top = s.search("grep regex pattern", k=1)
        assert top == ["grep"]

    def test_search_exported_from_suckers(self):
        from runtime.execution.suckers import SkillSearcher, TfIdfSkillSearcher

        assert TfIdfSkillSearcher is not None
        assert SkillSearcher is not None


# ═══════════════════════════════════════════════════════════════
# 2. Skill Rate Limiter
# ═══════════════════════════════════════════════════════════════


class TestSkillRateLimiter:
    def test_acquire_allowed_when_under_capacity(self):
        from runtime.execution.suckers.rate_limit import SkillRateLimiter

        rl = SkillRateLimiter(capacity=3, refill_per_sec=1.0)
        for _ in range(3):
            ok, retry = rl.try_acquire("web_search")
            assert ok is True
            assert retry == 0.0

    def test_throttled_after_capacity_exhausted(self):
        from runtime.execution.suckers.rate_limit import SkillRateLimiter

        rl = SkillRateLimiter(capacity=2, refill_per_sec=0.1)
        rl.try_acquire("web_search")
        rl.try_acquire("web_search")
        ok, retry = rl.try_acquire("web_search")
        assert ok is False
        assert retry > 0

    def test_refill_happens_over_time(self):
        from runtime.execution.suckers.rate_limit import SkillRateLimiter

        # Fake clock so the test doesn't sleep.
        now = [0.0]
        rl = SkillRateLimiter(
            capacity=2,
            refill_per_sec=1.0,
            clock=lambda: now[0],
        )
        rl.try_acquire("s")
        rl.try_acquire("s")
        ok, _ = rl.try_acquire("s")
        assert ok is False

        # Advance 3 seconds → bucket refills to 2 + 3*1.0 clipped to capacity=2.
        now[0] = 3.0
        ok, retry = rl.try_acquire("s")
        assert ok is True
        assert retry == 0.0

    def test_per_caller_bucket_isolation(self):
        from runtime.execution.suckers.rate_limit import SkillRateLimiter

        rl = SkillRateLimiter(capacity=1, refill_per_sec=0.001)
        ok_a, _ = rl.try_acquire("s", caller="arm-a")
        ok_b, _ = rl.try_acquire("s", caller="arm-b")
        # Both should succeed — different buckets.
        assert ok_a is True
        assert ok_b is True
        # Second call on arm-a should throttle.
        throttled, _ = rl.try_acquire("s", caller="arm-a")
        assert throttled is False

    def test_overrides_apply_per_skill(self):
        from runtime.execution.suckers.rate_limit import SkillRateLimiter

        rl = SkillRateLimiter(
            capacity=100,
            refill_per_sec=100,
            overrides={"strict_skill": (1, 0.001)},
        )
        # strict_skill: capacity=1.
        ok, _ = rl.try_acquire("strict_skill")
        assert ok is True
        throttled, _ = rl.try_acquire("strict_skill")
        assert throttled is False
        # Other skills unaffected.
        ok_other, _ = rl.try_acquire("other")
        assert ok_other is True

    def test_reset_restores_bucket(self):
        from runtime.execution.suckers.rate_limit import SkillRateLimiter

        rl = SkillRateLimiter(capacity=1, refill_per_sec=0.001)
        rl.try_acquire("s")
        throttled, _ = rl.try_acquire("s")
        assert throttled is False
        rl.reset("s")
        ok, _ = rl.try_acquire("s")
        assert ok is True

    def test_stats_tracks_calls_and_throttles(self):
        from runtime.execution.suckers.rate_limit import SkillRateLimiter

        rl = SkillRateLimiter(capacity=1, refill_per_sec=0.001)
        rl.try_acquire("s")  # allowed
        rl.try_acquire("s")  # throttled
        rl.try_acquire("s")  # throttled
        stats = rl.stats()
        assert stats["calls_total"] == 3
        assert stats["throttled_total"] == 2
        assert 0.5 < stats["throttle_ratio"] < 0.7

    def test_set_override_takes_effect_immediately(self):
        from runtime.execution.suckers.rate_limit import SkillRateLimiter

        rl = SkillRateLimiter(capacity=10, refill_per_sec=0.001)
        # Fill partially.
        rl.try_acquire("s")
        rl.try_acquire("s")
        # Tighten.
        rl.set_override("s", capacity=1, refill_per_sec=0.001)
        # New bucket is fresh at capacity=1.
        ok, _ = rl.try_acquire("s")
        assert ok is True
        throttled, _ = rl.try_acquire("s")
        assert throttled is False

    def test_exported_from_suckers(self):
        from runtime.execution.suckers import (
            DEFAULT_CAPACITY,
            SkillRateLimiter,
        )

        assert SkillRateLimiter is not None
        assert DEFAULT_CAPACITY == 20


# ═══════════════════════════════════════════════════════════════
# 3. Anthropic Prompt Cache Hints
# ═══════════════════════════════════════════════════════════════


class TestPromptCache:
    def test_prepare_cached_system_small_returns_string(self):
        from runtime.sensing.model_router.prompt_cache import prepare_cached_system

        result = prepare_cached_system("short prompt")
        assert result == "short prompt"

    def test_prepare_cached_system_large_returns_block_list(self):
        from runtime.sensing.model_router.prompt_cache import prepare_cached_system

        big = "x" * 5000
        result = prepare_cached_system(big)
        assert isinstance(result, list)
        assert result[0]["type"] == "text"
        assert result[0]["cache_control"] == {"type": "ephemeral"}
        assert result[0]["text"] == big

    def test_prepare_cached_tools_adds_breakpoint_to_last(self):
        from runtime.sensing.model_router.prompt_cache import prepare_cached_tools

        tools = [
            {"name": "a", "description": "x" * 1500, "input_schema": {}},
            {"name": "b", "description": "x" * 1500, "input_schema": {}},
        ]
        result = prepare_cached_tools(tools)
        assert "cache_control" not in result[0]
        assert result[1]["cache_control"] == {"type": "ephemeral"}

    def test_prepare_cached_tools_skips_when_too_small(self):
        from runtime.sensing.model_router.prompt_cache import prepare_cached_tools

        tools = [{"name": "a", "description": "short", "input_schema": {}}]
        result = prepare_cached_tools(tools)
        assert "cache_control" not in result[0]

    def test_prepare_cached_tools_preserves_original(self):
        from runtime.sensing.model_router.prompt_cache import prepare_cached_tools

        tools = [
            {"name": "a", "description": "x" * 2000, "input_schema": {}},
            {"name": "b", "description": "x" * 2000, "input_schema": {}},
        ]
        prepare_cached_tools(tools)
        assert "cache_control" not in tools[1]  # original not mutated

    def test_budget_breakpoints_respects_max(self):
        from runtime.sensing.model_router.prompt_cache import (
            MAX_BREAKPOINTS,
            budget_breakpoints,
        )

        # With system + tools cached, 2 remain of 4.
        n = budget_breakpoints(
            has_system_cache=True,
            has_tools_cache=True,
            messages_remaining=10,
        )
        assert n == 2
        # No caching used → all 4 available.
        n = budget_breakpoints(
            has_system_cache=False,
            has_tools_cache=False,
            messages_remaining=10,
        )
        assert n == MAX_BREAKPOINTS
        # Capped at messages_remaining.
        n = budget_breakpoints(
            has_system_cache=False,
            has_tools_cache=False,
            messages_remaining=1,
        )
        assert n == 1

    def test_mark_cache_breakpoint_string_content(self):
        from runtime.sensing.model_router.prompt_cache import mark_cache_breakpoint

        msg = {"role": "user", "content": "hello"}
        out = mark_cache_breakpoint(msg)
        assert out["content"][0]["text"] == "hello"
        assert out["content"][0]["cache_control"] == {"type": "ephemeral"}

    def test_mark_cache_breakpoint_list_content(self):
        from runtime.sensing.model_router.prompt_cache import mark_cache_breakpoint

        msg = {
            "role": "user",
            "content": [
                {"type": "text", "text": "a"},
                {"type": "text", "text": "b"},
            ],
        }
        out = mark_cache_breakpoint(msg)
        assert "cache_control" not in out["content"][0]
        assert out["content"][1]["cache_control"] == {"type": "ephemeral"}

    def test_estimate_cache_savings_hit_heavy(self):
        from runtime.sensing.model_router.prompt_cache import estimate_cache_savings

        # 100 fresh input, 900 from cache read, 0 written.
        r = estimate_cache_savings(100, 900, 0)
        # uncached = 1000, actual = 100 + 0.10*900 = 190 → savings ≈ 0.81.
        assert r["savings_ratio"] > 0.7
        assert r["cache_hit_ratio"] == 0.9

    def test_estimate_cache_savings_write_heavy(self):
        from runtime.sensing.model_router.prompt_cache import estimate_cache_savings

        # First turn: 100 fresh, 0 read, 900 written.
        r = estimate_cache_savings(100, 0, 900)
        # uncached = 1000, actual = 100 + 1.25*900 = 1225 → savings negative.
        assert r["savings_ratio"] < 0

    def test_exported_from_eyes(self):
        from runtime.sensing.model_router import (
            MAX_BREAKPOINTS,
            MIN_CACHE_CHARS,
            budget_breakpoints,
            estimate_cache_savings,
            mark_cache_breakpoint,
            prepare_cached_system,
            prepare_cached_tools,
        )

        assert MAX_BREAKPOINTS == 4
        assert MIN_CACHE_CHARS >= 1000
        assert callable(prepare_cached_system)
        assert callable(prepare_cached_tools)
        assert callable(mark_cache_breakpoint)
        assert callable(budget_breakpoints)
        assert callable(estimate_cache_savings)

    def test_model_response_has_cache_fields(self):
        from runtime.sensing.model_router.models import ModelResponse

        resp = ModelResponse(text="hi")
        assert resp.cache_read_tokens == 0
        assert resp.cache_creation_tokens == 0

        resp2 = ModelResponse(
            text="hi",
            input_tokens=100,
            output_tokens=50,
            cache_read_tokens=900,
            cache_creation_tokens=0,
        )
        assert resp2.cache_read_tokens == 900
