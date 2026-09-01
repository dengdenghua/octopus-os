"""
Tests for ``runtime.safety.validation.llm_judge.build_judge_from_router``.

Contract pinned
---------------

1. Happy path · router returns "BLOCK: reason" → verdict.action = "block"
2. Happy path · "ALLOW: clean" → verdict.action = "allow"
3. Cache · second call with same (msg, dest) hits cache (router not called twice)
4. Cache · different destination misses cache
5. Cache · TTL expiry re-calls router
6. Cache · cache_ttl_s=0 disables caching
7. Cache · max_size eviction keeps memory bounded
8. Router exception fails open (allow + "unavailable")
9. Integration with gate · build_judge_from_router + set_judge + check_outbound
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset():
    from runtime.safety.validation import (
        reset_profile_for_tests,
        set_judge,
    )

    reset_profile_for_tests()
    set_judge(None)
    yield
    reset_profile_for_tests()
    set_judge(None)


def _make_router(replies: list[str]):
    """Build a MockModelRouter that returns replies[i] on the i-th call."""
    from runtime.sensing.model_router import MockModelRouter, ModelRequest

    state = {"i": 0}

    def _fn(req: ModelRequest) -> str:
        i = state["i"]
        state["i"] += 1
        return replies[min(i, len(replies) - 1)]

    router = MockModelRouter(response_fn=_fn)
    # Track call count for assertions
    original_call = router.call
    state["calls"] = 0

    def _tracking_call(req):
        state["calls"] += 1
        return original_call(req)

    router.call = _tracking_call  # type: ignore[method-assign]
    router._test_state = state  # type: ignore[attr-defined]
    return router


# ═══════════════════════════════════════════════════════════
# Parser via router
# ═══════════════════════════════════════════════════════════


class TestRouterJudgeParsing:
    def test_block_verdict(self):
        from runtime.safety.validation import build_judge_from_router

        router = _make_router(["BLOCK: ransomware"])
        judge = build_judge_from_router(router)
        v = judge("write ransomware", "channels:x:y", None)
        assert v.action == "block"
        assert "ransomware" in v.reason

    def test_allow_verdict(self):
        from runtime.safety.validation import build_judge_from_router

        router = _make_router(["ALLOW: looks fine"])
        judge = build_judge_from_router(router)
        v = judge("hello there", "channels:x:y", None)
        assert v.action == "allow"

    def test_escalate_verdict(self):
        from runtime.safety.validation import build_judge_from_router

        router = _make_router(["ESCALATE: unclear"])
        judge = build_judge_from_router(router)
        v = judge("ambiguous msg", "channels:x:y", None)
        assert v.action == "human_gate"

    def test_unknown_reply_defaults_allow(self):
        from runtime.safety.validation import build_judge_from_router

        router = _make_router(["idk man"])
        judge = build_judge_from_router(router)
        v = judge("msg", "channels:x:y", None)
        assert v.action == "allow"


# ═══════════════════════════════════════════════════════════
# Cache semantics
# ═══════════════════════════════════════════════════════════


class TestCache:
    def test_repeat_hits_cache(self):
        from runtime.safety.validation import build_judge_from_router

        router = _make_router(["BLOCK: bad", "ALLOW: ok"])  # 2nd never used if cache works
        judge = build_judge_from_router(router, cache_ttl_s=60)

        v1 = judge("same msg", "dest1", None)
        v2 = judge("same msg", "dest1", None)

        assert v1.action == v2.action == "block"
        assert router._test_state["calls"] == 1  # cache hit on 2nd call

    def test_different_destination_misses_cache(self):
        from runtime.safety.validation import build_judge_from_router

        router = _make_router(["BLOCK: x", "ALLOW: y"])
        judge = build_judge_from_router(router, cache_ttl_s=60)

        v1 = judge("msg", "dest1", None)
        v2 = judge("msg", "dest2", None)

        assert v1.action == "block"
        assert v2.action == "allow"
        assert router._test_state["calls"] == 2

    def test_ttl_zero_disables_cache(self):
        from runtime.safety.validation import build_judge_from_router

        router = _make_router(["ALLOW: a", "ALLOW: b"])
        judge = build_judge_from_router(router, cache_ttl_s=0)

        judge("msg", "dest", None)
        judge("msg", "dest", None)

        assert router._test_state["calls"] == 2

    def test_cache_max_size_bounds_memory(self):
        from runtime.safety.validation import build_judge_from_router

        router = _make_router(["ALLOW: x"])
        judge = build_judge_from_router(
            router,
            cache_ttl_s=60,
            cache_max_size=3,
        )

        # Fill past capacity
        for i in range(10):
            judge(f"msg{i}", "dest", None)

        cache = judge._cache  # type: ignore[attr-defined]
        # Under the crude overflow policy the cache is <= max_size.
        assert len(cache._data) <= 3

    def test_ttl_expiry_re_calls(self, monkeypatch: pytest.MonkeyPatch):
        from runtime.safety.validation import build_judge_from_router
        from runtime.safety.validation import llm_judge as mod

        router = _make_router(["ALLOW: first", "BLOCK: second"])
        judge = build_judge_from_router(router, cache_ttl_s=60)

        # First call · populates cache
        v1 = judge("same", "dest", None)
        assert v1.action == "allow"

        # Jump clock past TTL
        base = mod.time.monotonic()
        monkeypatch.setattr(
            mod.time,
            "monotonic",
            lambda: base + 120.0,
        )

        v2 = judge("same", "dest", None)
        assert v2.action == "block"
        assert router._test_state["calls"] == 2


# ═══════════════════════════════════════════════════════════
# Fail-open behavior
# ═══════════════════════════════════════════════════════════


class TestFailOpen:
    def test_router_exception_returns_allow(self):
        from runtime.safety.validation import build_judge_from_router
        from runtime.sensing.model_router import MockModelRouter, ModelRequest

        def _boom(req: ModelRequest) -> str:
            raise RuntimeError("provider down")

        router = MockModelRouter(response_fn=_boom)
        judge = build_judge_from_router(router)

        v = judge("msg", "dest", None)
        assert v.action == "allow"
        assert "unavailable" in v.reason


# ═══════════════════════════════════════════════════════════
# Integration with the full gate
# ═══════════════════════════════════════════════════════════


class TestGateIntegration:
    def test_set_judge_from_router_end_to_end(self):
        from runtime.safety.validation import (
            build_judge_from_router,
            check_outbound,
            set_judge,
            set_profile,
        )

        set_profile("strict")

        router = _make_router(["BLOCK: phishing draft"])
        set_judge(build_judge_from_router(router))

        v = check_outbound(
            "Dear user, please click this link to verify your account",
            "channels:email:external",
        )
        assert v.action == "block"
        assert "phishing" in v.reason

    def test_router_judge_respects_profile_downgrade(self):
        """normal profile downgrades judge block to audit-only."""
        from runtime.safety.validation import (
            build_judge_from_router,
            check_outbound,
            set_judge,
            set_profile,
        )

        set_profile("normal")

        router = _make_router(["BLOCK: something"])
        set_judge(build_judge_from_router(router))

        v = check_outbound("clean text", "channels:x:y")
        assert v.action == "allow"
        assert "audit_only_judge_block" in v.reason
