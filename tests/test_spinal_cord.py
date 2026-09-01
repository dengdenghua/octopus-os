"""Implementation note."""

from __future__ import annotations

import time

from runtime.core.nerves.reflex import (
    CacheMatcher,
    DeterministicMatcher,
    ReflexMatch,
    ReflexMiss,
    ReflexRouter,
    RegexMatcher,
)
from runtime.platform.models import ParsedIntent


def _intent(goal: str, intent_type: str = "task", **flags) -> ParsedIntent:
    return ParsedIntent(
        raw=goal,
        intent_type=intent_type,
        normalized_goal=goal,
        flags=flags,
    )


# ═══════════════════════════════════════════════════════════
# RegexMatcher
# ═══════════════════════════════════════════════════════════


class TestRegexMatcher:
    def test_matches_exact_phrase(self):
        m = RegexMatcher("undo", r"^undo$")
        result = m.try_match(_intent("undo"))
        assert result is not None
        assert result.rule_id == "undo"

    def test_case_insensitive_default(self):
        m = RegexMatcher("undo", r"^undo$")
        assert m.try_match(_intent("UNDO")) is not None

    def test_captures_groups(self):
        m = RegexMatcher("volume", r"^volume (\d+)$")
        result = m.try_match(_intent("volume 42"))
        assert result is not None
        assert result.response["groups"] == ["42"]

    def test_returns_canned_response(self):
        m = RegexMatcher("hi", r"^hi$", response={"reply": "hello"})
        result = m.try_match(_intent("hi"))
        assert result.response == {"reply": "hello"}

    def test_handler_callback(self):
        def handler(match, intent):
            return f"you said: {match.group(0)}"

        m = RegexMatcher("repeat", r".+", handler=handler)
        result = m.try_match(_intent("abc def"))
        assert result.response == "you said: abc def"

    def test_no_match_returns_none(self):
        m = RegexMatcher("undo", r"^undo$")
        assert m.try_match(_intent("something else")) is None


# ═══════════════════════════════════════════════════════════
# DeterministicMatcher
# ═══════════════════════════════════════════════════════════


class TestDeterministicMatcher:
    def test_matches_by_intent_type(self):
        m = DeterministicMatcher("greet", "chitchat", response={"reply": "hi"})
        result = m.try_match(_intent("anything", intent_type="chitchat"))
        assert result is not None

    def test_wrong_intent_type_miss(self):
        m = DeterministicMatcher("greet", "chitchat", response={})
        assert m.try_match(_intent("x", intent_type="task")) is None

    def test_keywords_filter(self):
        m = DeterministicMatcher("files", "task", response={}, keywords=["list", "show"])
        assert m.try_match(_intent("list the files")) is not None
        assert m.try_match(_intent("do something else")) is None


# ═══════════════════════════════════════════════════════════
# CacheMatcher (REF-I4)
# ═══════════════════════════════════════════════════════════


class TestCacheMatcher:
    def test_key_stable_for_same_semantic(self):
        """Implementation note."""
        a = _intent("List my files")
        b = _intent("list my files")
        c = _intent("list  my   files")  # Implementation note.
        assert CacheMatcher.key_for(a) == CacheMatcher.key_for(b) == CacheMatcher.key_for(c)

    def test_key_differs_by_intent_type(self):
        a = _intent("x", intent_type="task")
        b = _intent("x", intent_type="query")
        assert CacheMatcher.key_for(a) != CacheMatcher.key_for(b)

    def test_miss_on_empty_cache(self):
        m = CacheMatcher()
        assert m.try_match(_intent("anything")) is None

    def test_hit_after_put(self):
        m = CacheMatcher()
        intent = _intent("count words in x")
        m.put(intent, {"count": 42})
        result = m.try_match(intent)
        assert result is not None
        assert result.response == {"count": 42}
        assert result.kind == "cache"

    def test_ttl_expiry(self, monkeypatch):
        m = CacheMatcher(ttl_seconds=60)
        intent = _intent("test")
        m.put(intent, "cached")
        # Implementation note.
        now = time.time() + 61
        monkeypatch.setattr(time, "time", lambda: now)
        assert m.try_match(intent) is None

    def test_max_entries_eviction(self):
        m = CacheMatcher(max_entries=3)
        for i in range(5):
            m.put(_intent(f"goal_{i}"), i)
        assert m.size() == 3


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestReflexRouter:
    def test_higher_priority_wins(self):
        low = RegexMatcher("low", r"^hi$", response="low", priority=1)
        high = RegexMatcher("high", r"^hi$", response="high", priority=10)
        router = ReflexRouter([low, high])
        result = router.try_match(_intent("hi"))
        assert isinstance(result, ReflexMatch)
        assert result.response == "high"

    def test_returns_miss_when_nothing_matches(self):
        router = ReflexRouter([RegexMatcher("a", r"^nope$")])
        result = router.try_match(_intent("hi"))
        assert isinstance(result, ReflexMiss)

    def test_force_deliberative_on_debug_intent(self):
        """Implementation note."""
        router = ReflexRouter([RegexMatcher("catch_all", r".+", response="should-not-hit")])
        result = router.try_match(_intent("fix bug", intent_type="debug"))
        assert isinstance(result, ReflexMiss)
        assert result.reason == "force_deliberative"

    def test_force_deliberative_on_deep_flag(self):
        router = ReflexRouter([RegexMatcher("catch_all", r".+", response="should-not-hit")])
        result = router.try_match(_intent("x", deep=True))
        assert isinstance(result, ReflexMiss)

    def test_chitchat_goes_through_reflex(self):
        """Implementation note."""
        router = ReflexRouter([DeterministicMatcher("greet", "chitchat", response={"r": "hi"})])
        result = router.try_match(_intent("hello", intent_type="chitchat"))
        assert isinstance(result, ReflexMatch)

    def test_hit_rate_tracking(self):
        router = ReflexRouter([RegexMatcher("a", r"^match$")])
        router.try_match(_intent("match"))  # hit
        router.try_match(_intent("nomatch"))  # miss
        assert router.hit_rate == 0.5

    def test_stats_by_rule(self):
        router = ReflexRouter(
            [
                RegexMatcher("r1", r"^match1$"),
                RegexMatcher("r2", r"^match2$"),
            ]
        )
        router.try_match(_intent("match1"))
        router.try_match(_intent("nomatch"))
        stats = router.stats_by_rule()
        assert stats["r1"]["hits"] == 1
        assert stats["r1"]["tries"] == 2
        assert stats["r2"]["hits"] == 0


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestRuleOrder:
    def test_rules_tried_in_priority_order(self):
        """Implementation note."""
        high_miss = RegexMatcher("high_miss", r"^never$", priority=10)
        low_hit = RegexMatcher("low_hit", r"^x$", priority=1)
        router = ReflexRouter([high_miss, low_hit])
        result = router.try_match(_intent("x"))
        assert isinstance(result, ReflexMatch)
        assert result.rule_id == "low_hit"  # Implementation note.
