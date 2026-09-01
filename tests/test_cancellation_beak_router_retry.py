"""Tests for scoped cancellation, beak metrics, and multi-router retry:

1. Cancellation contextvar + scoped wiring
2. Metrics emission from ToolExecutor (Beak)
3. Retry on transient errors in MultiModelRouter
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

# ═══════════════════════════════════════════════════════════════
# 1. Cancellation contextvar wiring
# ═══════════════════════════════════════════════════════════════


class TestScopedCancellation:
    def test_default_token_never_cancels(self):
        from runtime.safety.approval.cancellation import current_cancellation_token

        t = current_cancellation_token()
        assert t.is_cancelled is False

    def test_scoped_cancellation_installs_token(self):
        from runtime.safety.approval.cancellation import (
            CancellationSource,
            current_cancellation_token,
            scoped_cancellation,
        )

        src = CancellationSource()
        with scoped_cancellation(src.token):
            assert current_cancellation_token() is src.token

    def test_scoped_cancellation_restores_on_exit(self):
        from runtime.safety.approval.cancellation import (
            CancellationSource,
            current_cancellation_token,
            scoped_cancellation,
        )

        outer_default = current_cancellation_token()
        src = CancellationSource()
        with scoped_cancellation(src.token):
            assert current_cancellation_token() is src.token
        assert current_cancellation_token() is outer_default

    def test_nested_scopes(self):
        from runtime.safety.approval.cancellation import (
            CancellationSource,
            current_cancellation_token,
            scoped_cancellation,
        )

        a = CancellationSource()
        b = CancellationSource()
        with scoped_cancellation(a.token):
            assert current_cancellation_token() is a.token
            with scoped_cancellation(b.token):
                assert current_cancellation_token() is b.token
            assert current_cancellation_token() is a.token

    def test_anthropic_call_raises_when_pre_cancelled(self):
        """The non-stream call() path should fast-fail under a tripped token."""
        from runtime.safety.approval.cancellation import (
            CancellationSource,
            OperationCancelled,
            scoped_cancellation,
        )

        # Use a duck-typed client so we don't need anthropic SDK.
        from runtime.sensing.model_router.anthropic_router import AnthropicModelRouter
        from runtime.sensing.model_router.models import Message, ModelRequest

        client = MagicMock()
        # If router actually fired the network call we'd hit this and
        # the test would fail differently — pre-flight cancellation
        # check should kick in before reaching messages.create().
        client.messages.create.side_effect = AssertionError(
            "should not reach network on pre-cancelled token",
        )

        router = AnthropicModelRouter(client=client)
        src = CancellationSource()
        src.cancel(reason="client-disconnected-pre-flight")

        req = ModelRequest(
            model="claude-haiku-4-5-20251001",
            messages=[Message(role="user", content="hi")],
            max_tokens=10,
            temperature=0.0,
        )
        with scoped_cancellation(src.token), pytest.raises(OperationCancelled):
            router.call(req)

        # Confirm the network adapter was not touched.
        client.messages.create.assert_not_called()


# ═══════════════════════════════════════════════════════════════
# 2. Metrics emission from Beak
# ═══════════════════════════════════════════════════════════════


class TestBeakMetricsWiring:
    @pytest.fixture(autouse=True)
    def fresh_registry(self):
        """Ensure each test starts with a clean global registry."""
        from runtime.platform.observability.metrics import get_registry

        reg = get_registry()
        reg.clear()
        yield reg
        reg.clear()

    def _build_executor(self):
        from runtime.execution.suckers import Skill, SkillRegistry
        from runtime.execution.tool_engine.executor import ToolExecutor
        from runtime.memory.journal import InMemoryJournal
        from runtime.safety.auth import TrustEngine

        def _ok_handler(**_kw):
            return "ok"

        registry = SkillRegistry()
        registry.register(
            Skill(
                name="echo",
                description="echo",
                trusted_source="builtin://echo",
                handler=_ok_handler,
            ),
            verify_tests=False,
        )

        def _fail_handler(**_kw):
            raise RuntimeError("boom")

        registry.register(
            Skill(
                name="boom",
                description="raises",
                trusted_source="builtin://boom",
                handler=_fail_handler,
            ),
            verify_tests=False,
        )

        return ToolExecutor(
            registry=registry,
            immunity=TrustEngine(trusted_sources=["builtin://*"]),
            journal=InMemoryJournal(),
        )

    def _execute(self, executor, sucker_id: str, args: dict | None = None):
        from uuid import uuid4

        from runtime.platform.models import Budget, BudgetLimits

        tid = uuid4()
        budget = Budget(
            task_id=tid,
            limits=BudgetLimits(usd=1.0, tokens=10_000, wallclock_s=60.0),
        )
        return executor.execute_step(
            step_id=1,
            node_id="n1",
            sucker_id=sucker_id,
            args=args or {},
            caller="arm:test",
            task_id=tid,
            arm_id="arm-test",
            budget=budget,
        )

    def test_success_increments_calls_counter(self, fresh_registry):
        executor = self._build_executor()
        self._execute(executor, "echo")
        c = fresh_registry.get("echo_skill_calls_total")
        assert c is not None
        assert c.value(labels={"sucker_id": "echo", "status": "success"}) == 1.0

    def test_latency_histogram_observes(self, fresh_registry):
        executor = self._build_executor()
        self._execute(executor, "echo")
        h = fresh_registry.get("echo_skill_latency_seconds")
        assert h is not None
        snap = h.snapshot(labels={"sucker_id": "echo"})
        assert snap["count"] == 1
        assert snap["sum"] >= 0

    def test_failure_increments_errors_counter(self, fresh_registry):
        executor = self._build_executor()
        self._execute(executor, "boom")
        # Status should NOT be success.
        c = fresh_registry.get("echo_skill_calls_total")
        assert c.value(labels={"sucker_id": "boom", "status": "success"}) == 0
        e = fresh_registry.get("echo_skill_errors_total")
        assert e is not None
        # Error counter incremented; the exact error_type label may
        # be "RuntimeError" or whatever beak normalized it to.
        total = sum(
            v
            for k, v in e._values.items()  # type: ignore[attr-defined]
            if any(pair[0] == "sucker_id" and pair[1] == "boom" for pair in k)
        )
        assert total >= 1

    def test_multiple_calls_aggregate(self, fresh_registry):
        executor = self._build_executor()
        for _ in range(3):
            self._execute(executor, "echo")
        c = fresh_registry.get("echo_skill_calls_total")
        assert c.value(labels={"sucker_id": "echo", "status": "success"}) == 3

    def test_prometheus_export_renders(self, fresh_registry):
        executor = self._build_executor()
        self._execute(executor, "echo")
        out = fresh_registry.render_prometheus()
        assert "echo_skill_calls_total" in out
        assert "echo_skill_latency_seconds" in out
        assert 'sucker_id="echo"' in out


# ═══════════════════════════════════════════════════════════════
# 3. Retry in MultiModelRouter
# ═══════════════════════════════════════════════════════════════


class _FlakyRouter:
    """Test double: fails the first ``fail_count`` calls then succeeds."""

    provider_name = "flaky"

    def __init__(self, fail_count: int, error: Exception):
        self._fail_count = fail_count
        self._error = error
        self.calls = 0

    @property
    def default_model(self) -> str:
        return "flaky/v1"

    def call(self, request):
        self.calls += 1
        if self.calls <= self._fail_count:
            raise self._error
        from runtime.platform.models import CostEntry
        from runtime.sensing.model_router.models import ModelResponse

        return ModelResponse(
            text="ok",
            input_tokens=1,
            output_tokens=1,
            cost=CostEntry(tokens_in=1, tokens_out=1, usd=0.0),
            model=request.model,
            provider="flaky",
        )


class _AlwaysFails:
    provider_name = "broken"

    def __init__(self, error: Exception):
        self._error = error
        self.calls = 0

    @property
    def default_model(self) -> str:
        return "broken/v1"

    def call(self, request):
        self.calls += 1
        raise self._error


class TestMultiRouterRetry:
    def _make_request(self):
        from runtime.sensing.model_router.models import Message, ModelRequest

        return ModelRequest(
            model="flaky/v1",
            messages=[Message(role="user", content="hi")],
            max_tokens=10,
            temperature=0.0,
        )

    def test_retries_transient_error_then_succeeds(self):
        from runtime.sensing.model_router.multi_router import MultiModelRouter

        # Mimic an SDK rate-limit error class.
        class RateLimitError(Exception):
            pass

        flaky = _FlakyRouter(fail_count=2, error=RateLimitError("429 boom"))
        router = MultiModelRouter(
            primary=flaky,
            retry_attempts=3,
            retry_base_delay=0.001,
        )
        # Patch sleep so the test doesn't actually wait.
        import runtime.platform.runtime_policy.retry as r

        original = r.time.sleep
        r.time.sleep = lambda s: None
        try:
            resp = router.call(self._make_request())
        finally:
            r.time.sleep = original
        assert resp.text == "ok"
        # 2 failures + 1 success = 3 calls within ONE provider.
        assert flaky.calls == 3
        # And only ONE attempt is recorded at the multi-router level.
        assert len(router.dispatch_log[-1].attempts) == 1
        assert router.dispatch_log[-1].attempts[0].success is True

    def test_does_not_retry_non_transient(self):
        from runtime.sensing.model_router.multi_router import MultiModelRouter

        class AuthError(Exception):
            pass

        broken = _AlwaysFails(error=AuthError("invalid api key"))
        # Provide a fallback so the chain has somewhere to go.
        flaky_ok = _FlakyRouter(fail_count=0, error=RuntimeError("unused"))
        router = MultiModelRouter(
            primary=broken,
            fallbacks=[flaky_ok],
            retry_attempts=3,
            retry_base_delay=0.001,
        )
        resp = router.call(self._make_request())
        assert resp.text == "ok"
        # Auth error → only ONE call to broken (no retry on permanent).
        assert broken.calls == 1
        # Fell over to fallback exactly once.
        assert flaky_ok.calls == 1

    def test_exhausts_retries_then_falls_over(self):
        from runtime.sensing.model_router.multi_router import MultiModelRouter

        class TimeoutError_(Exception):  # noqa: N801 — trailing _ disambiguates from builtin
            pass

        broken = _AlwaysFails(error=TimeoutError_("502 gateway timeout"))
        backup = _FlakyRouter(fail_count=0, error=RuntimeError("unused"))
        router = MultiModelRouter(
            primary=broken,
            fallbacks=[backup],
            retry_attempts=3,
            retry_base_delay=0.001,
        )
        import runtime.platform.runtime_policy.retry as r

        original = r.time.sleep
        r.time.sleep = lambda s: None
        try:
            resp = router.call(self._make_request())
        finally:
            r.time.sleep = original

        assert resp.text == "ok"
        # 3 retries on broken, then 1 successful call on backup.
        assert broken.calls == 3
        assert backup.calls == 1

    def test_retry_attempts_one_disables_retry(self):
        from runtime.sensing.model_router.multi_router import MultiModelRouter

        class RateLimitError(Exception):
            pass

        flaky = _FlakyRouter(fail_count=1, error=RateLimitError("429"))
        router = MultiModelRouter(
            primary=flaky,
            retry_attempts=1,
        )
        # No retry → first failure propagates as a route attempt fail
        # and (with no fallback) the multi-router re-raises.
        with pytest.raises(RateLimitError):
            router.call(self._make_request())
        assert flaky.calls == 1

    def test_is_transient_error_classifier(self):
        from runtime.sensing.model_router.multi_router import _is_transient_error

        class RateLimitError(Exception):
            pass

        assert _is_transient_error(RateLimitError("rate limit"))
        assert _is_transient_error(ConnectionError("connection refused"))
        assert _is_transient_error(TimeoutError("read timeout"))
        assert _is_transient_error(RuntimeError("HTTP 503 Service Unavailable"))
        assert not _is_transient_error(ValueError("bad input"))
        assert not _is_transient_error(KeyError("missing field"))
