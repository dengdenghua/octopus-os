"""Unit tests for budget, credentials, skill usage, and error classification."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from runtime.memory.diagnostics.error_classifier import (
    ErrorCategory,
    RecoveryAction,
    classify_error,
)
from runtime.platform.budget.iteration_budget import (
    IterationBudget,
    IterationBudgetConfig,
    IterationBudgetExceeded,
)
from runtime.platform.budget.rate_limit_tracker import RateLimitEntry, RateLimitTracker
from runtime.platform.budget.usage_pricing import UsageConfig, UsagePricing
from runtime.platform.credentials.credential_pool import CredentialEntry, CredentialPool
from runtime.platform.credentials.credential_sources import EnvVarSource, FileSource
from runtime.safety.evolution.skill_usage import (
    SkillUsageTracker,
)

# ═══════════════════════════════════════════════════════════
# IterationBudget
# ═══════════════════════════════════════════════════════════


class TestIterationBudget:
    def test_tick_increments(self):
        b = IterationBudget(IterationBudgetConfig(max_iterations=10))
        count, status = b.tick()
        assert count == 1
        assert status == "ok"

    def test_warning_at_threshold(self):
        b = IterationBudget(IterationBudgetConfig(max_iterations=10, warn_at_percent=0.8))
        for _ in range(8):
            b.tick()
        _, status = b.tick()
        assert status == "warning"

    def test_hard_limit_raises(self):
        b = IterationBudget(
            IterationBudgetConfig(max_iterations=3, hard_limit=True, auto_extend=False)
        )
        b.tick()
        b.tick()
        b.tick()
        with pytest.raises(IterationBudgetExceeded):
            b.tick()

    def test_soft_limit_returns_over_budget(self):
        b = IterationBudget(
            IterationBudgetConfig(max_iterations=3, hard_limit=False, auto_extend=False)
        )
        for _ in range(3):
            b.tick()
        _, status = b.tick()
        assert status == "over_budget"

    def test_remaining(self):
        b = IterationBudget(IterationBudgetConfig(max_iterations=10))
        b.tick()
        assert b.remaining == 9

    def test_percent_used(self):
        b = IterationBudget(IterationBudgetConfig(max_iterations=10))
        for _ in range(5):
            b.tick()
        assert b.percent_used == 0.5

    def test_reset(self):
        b = IterationBudget(IterationBudgetConfig(max_iterations=10))
        for _ in range(5):
            b.tick()
        b.reset()
        assert b.count == 0

    def test_is_exceeded(self):
        b = IterationBudget(IterationBudgetConfig(max_iterations=2, auto_extend=False))
        b.tick()
        assert b.is_exceeded() is False
        b.tick()
        assert b.is_exceeded() is True

    def test_auto_extend_on_progress(self):
        b = IterationBudget(
            IterationBudgetConfig(
                max_iterations=5,
                auto_extend=True,
                extend_by_percent=1.0,
                max_extensions=2,
                progress_window=3,
                progress_threshold=0.6,
            )
        )
        for i in range(5):
            b.tick(tool_name=f"tool_{i}", success=True, is_unique=True)
        _, status = b.tick(tool_name="tool_next", success=True, is_unique=True)
        assert status == "extended"
        assert b.effective_limit > 5

    def test_no_extend_when_stuck(self):
        b = IterationBudget(
            IterationBudgetConfig(
                max_iterations=5,
                auto_extend=True,
                extend_by_percent=1.0,
                max_extensions=2,
                progress_window=3,
                progress_threshold=0.6,
                hard_limit=True,
            )
        )
        for _i in range(5):
            b.tick(tool_name="same_tool", success=False, is_unique=False)
        with pytest.raises(IterationBudgetExceeded):
            b.tick(tool_name="same_tool", success=False, is_unique=False)

    def test_is_stuck_detects_loop(self):
        b = IterationBudget(
            IterationBudgetConfig(
                max_iterations=20,
                progress_window=5,
                progress_threshold=0.6,
            )
        )
        for _i in range(10):
            b.tick(tool_name="same_tool", success=False, is_unique=False)
        assert b.is_stuck() is True

    def test_is_stuck_false_when_progressing(self):
        b = IterationBudget(
            IterationBudgetConfig(
                max_iterations=20,
                progress_window=5,
                progress_threshold=0.6,
            )
        )
        for i in range(10):
            b.tick(tool_name=f"tool_{i}", success=True, is_unique=True)
        assert b.is_stuck() is False

    def test_max_extensions_respected(self):
        b = IterationBudget(
            IterationBudgetConfig(
                max_iterations=3,
                auto_extend=True,
                extend_by_percent=1.0,
                max_extensions=1,
                progress_window=2,
                progress_threshold=0.5,
                hard_limit=True,
            )
        )
        for i in range(3):
            b.tick(tool_name=f"tool_{i}", success=True, is_unique=True)
        _, status = b.tick(tool_name="tool_a", success=True, is_unique=True)
        assert status == "extended"
        for i in range(2):
            b.tick(tool_name=f"tool_{i + 10}", success=True, is_unique=True)
        with pytest.raises(IterationBudgetExceeded):
            b.tick(tool_name="tool_z", success=True, is_unique=True)

    def test_status_report(self):
        b = IterationBudget(IterationBudgetConfig(max_iterations=10))
        b.tick(tool_name="read_file", success=True, is_unique=True)
        status = b.status()
        assert status["count"] == 1
        assert status["is_stuck"] is False


# ═══════════════════════════════════════════════════════════
# RateLimitTracker
# ═══════════════════════════════════════════════════════════


class TestRateLimitTracker:
    def test_update_and_get(self):
        tracker = RateLimitTracker()
        entry = RateLimitEntry(model="gpt-4o", provider="openai", requests_remaining=100)
        tracker.update(entry)
        got = tracker.get("openai", "gpt-4o")
        assert got is not None
        assert got.requests_remaining == 100

    def test_is_limited_when_zero_remaining(self):
        import time as _time

        tracker = RateLimitTracker()
        entry = RateLimitEntry(
            model="gpt-4o",
            provider="openai",
            requests_remaining=0,
            reset_at=_time.time() + 60,
        )
        tracker.update(entry)
        assert tracker.is_limited("openai", "gpt-4o") is True

    def test_not_limited_when_remaining(self):
        tracker = RateLimitTracker()
        entry = RateLimitEntry(model="gpt-4o", provider="openai", requests_remaining=10)
        tracker.update(entry)
        assert tracker.is_limited("openai", "gpt-4o") is False

    def test_update_from_headers(self):
        tracker = RateLimitTracker()
        headers = {"x-ratelimit-remaining-requests": "50", "x-ratelimit-reset": "1234567890.0"}
        entry = tracker.update_from_headers("openai", "gpt-4o", headers)
        assert entry is not None
        assert entry.requests_remaining == 50

    def test_status(self):
        tracker = RateLimitTracker()
        tracker.update(RateLimitEntry(model="gpt-4o", provider="openai", requests_remaining=50))
        status = tracker.status()
        assert "openai:gpt-4o" in status


# ═══════════════════════════════════════════════════════════
# UsagePricing
# ═══════════════════════════════════════════════════════════


class TestUsagePricing:
    def test_compute_cost(self):
        pricing = UsagePricing(UsageConfig(log_path=""))
        cost = pricing.compute_cost("gpt-4o", 1_000_000, 1_000_000)
        assert cost == 12.50  # 2.50 + 10.00

    def test_compute_cost_unknown_model(self):
        pricing = UsagePricing(UsageConfig(log_path=""))
        cost = pricing.compute_cost("unknown-model", 1000, 1000)
        assert cost == 0.0

    def test_record_and_summary(self, tmp_path):
        pricing = UsagePricing(UsageConfig(log_path=str(tmp_path / "usage.jsonl")))
        pricing.record("gpt-4o", 1000, 500, provider="openai")
        pricing.record("gpt-4o-mini", 2000, 1000, provider="openai")
        summary = pricing.summary()
        assert summary["total_calls"] == 2
        assert "gpt-4o" in summary["by_model"]

    def test_budget_tracking(self, tmp_path):
        pricing = UsagePricing(
            UsageConfig(
                log_path=str(tmp_path / "usage.jsonl"),
                budget_usd=0.001,
            )
        )
        assert pricing.is_over_budget() is False
        pricing.record("gpt-4o", 1_000_000, 1_000_000)
        assert pricing.is_over_budget() is True

    def test_mimo_pricing(self):
        pricing = UsagePricing(UsageConfig(log_path=""))
        cost = pricing.compute_cost("mimo-v2-flash", 1_000_000, 1_000_000)
        assert cost == 0.25  # 0.05 + 0.20


# ═══════════════════════════════════════════════════════════
# CredentialPool
# ═══════════════════════════════════════════════════════════


class TestCredentialPool:
    def test_add_and_get(self):
        pool = CredentialPool()
        pool.add(CredentialEntry(key_id="key1", secret="sk-abc", provider="openai"))
        entry = pool.get("openai")
        assert entry is not None
        assert entry.secret == "sk-abc"

    def test_add_from_env(self):
        pool = CredentialPool()
        with patch.dict(os.environ, {"TEST_API_KEY": "sk-test123"}):
            entry = pool.add_from_env("TEST_API_KEY", provider="openai")
            assert entry is not None
            assert entry.secret == "sk-test123"

    def test_round_robin(self):
        pool = CredentialPool()
        pool.add(CredentialEntry(key_id="k1", secret="s1", provider="openai", weight=1.0))
        pool.add(CredentialEntry(key_id="k2", secret="s2", provider="openai", weight=1.0))
        secrets = set()
        for _ in range(100):
            e = pool.get("openai")
            if e:
                secrets.add(e.secret)
        assert len(secrets) == 2

    def test_cooldown(self):
        pool = CredentialPool()
        pool.add(CredentialEntry(key_id="k1", secret="s1", provider="openai"))
        pool.report_error("k1", cooldown_sec=300)
        entry = pool.get("openai")
        assert entry is None

    def test_report_success(self):
        pool = CredentialPool()
        pool.add(CredentialEntry(key_id="k1", secret="s1", provider="openai"))
        pool.report_error("k1")
        pool.report_success("k1")
        status = pool.status()
        assert status["entries"][0]["error_count"] == 0

    def test_size(self):
        pool = CredentialPool()
        pool.add(CredentialEntry(key_id="k1", secret="s1"))
        assert pool.size == 1


# ═══════════════════════════════════════════════════════════
# CredentialSources
# ═══════════════════════════════════════════════════════════


class TestCredentialSources:
    def test_env_var_source(self):
        with patch.dict(os.environ, {"MY_KEY": "val123"}):
            src = EnvVarSource({"api_key": "MY_KEY"})
            result = src.load()
            assert result["api_key"] == "val123"

    def test_env_var_missing(self):
        src = EnvVarSource({"api_key": "NONEXISTENT_VAR_XYZ"})
        result = src.load()
        assert "api_key" not in result

    def test_file_source_json(self, tmp_path):
        f = tmp_path / "creds.json"
        f.write_text('{"api_key": "sk-from-file"}', encoding="utf-8")
        src = FileSource(f, fmt="json")
        result = src.load()
        assert result["api_key"] == "sk-from-file"

    def test_file_source_dotenv(self, tmp_path):
        f = tmp_path / ".env"
        f.write_text("API_KEY=sk-dotenv\n# comment\nOTHER=val", encoding="utf-8")
        src = FileSource(f, fmt="dotenv")
        result = src.load()
        assert result["API_KEY"] == "sk-dotenv"

    def test_file_source_missing(self):
        src = FileSource("/nonexistent/path/creds.json")
        result = src.load()
        assert result == {}


# ═══════════════════════════════════════════════════════════
# SkillUsageTracker
# ═══════════════════════════════════════════════════════════


class TestSkillUsageTracker:
    def test_record_and_stats(self, tmp_path):
        tracker = SkillUsageTracker(log_path=str(tmp_path / "usage.jsonl"))
        tracker.record("skill_a", True, 1.5)
        tracker.record("skill_a", False, 2.0, error_type="timeout")
        tracker.record("skill_b", True, 0.5)
        stats = tracker.stats()
        assert len(stats) == 2
        assert stats[0].skill_name == "skill_a"

    def test_success_rate(self, tmp_path):
        tracker = SkillUsageTracker(log_path=str(tmp_path / "usage.jsonl"))
        tracker.record("skill_a", True)
        tracker.record("skill_a", True)
        tracker.record("skill_a", False)
        stats = tracker.stats("skill_a")
        assert len(stats) == 1
        assert stats[0].success_rate == pytest.approx(0.667, abs=0.01)

    def test_top_skills(self, tmp_path):
        tracker = SkillUsageTracker(log_path=str(tmp_path / "usage.jsonl"))
        for _ in range(10):
            tracker.record("popular", True)
        for _ in range(2):
            tracker.record("rare", True)
        top = tracker.top_skills(1)
        assert top[0].skill_name == "popular"

    def test_least_reliable(self, tmp_path):
        tracker = SkillUsageTracker(log_path=str(tmp_path / "usage.jsonl"))
        for _ in range(5):
            tracker.record("bad_skill", False, error_type="crash")
        unreliable = tracker.least_reliable(min_calls=3)
        assert len(unreliable) == 1
        assert unreliable[0].success_rate < 0.5


# ═══════════════════════════════════════════════════════════
# ErrorClassifier
# ═══════════════════════════════════════════════════════════


class TestErrorClassifier:
    def test_rate_limit(self):
        result = classify_error("Rate limit exceeded", status_code=429)
        assert result.category == ErrorCategory.RATE_LIMIT
        assert result.is_retryable is True
        assert result.action == RecoveryAction.RETRY_WITH_BACKOFF

    def test_auth_error(self):
        result = classify_error("Invalid API Key", status_code=401)
        assert result.category == ErrorCategory.AUTH
        assert result.action == RecoveryAction.SWITCH_KEY

    def test_timeout(self):
        result = classify_error("Request timeout exceeded")
        assert result.category == ErrorCategory.TIMEOUT
        assert result.is_retryable is True

    def test_content_filter(self):
        result = classify_error("Content filtered by safety policy")
        assert result.category == ErrorCategory.CONTENT_FILTER
        assert result.is_retryable is False
        assert result.action == RecoveryAction.ABORT

    def test_context_length(self):
        result = classify_error("Maximum context length exceeded")
        assert result.category == ErrorCategory.CONTEXT_LENGTH
        assert result.action == RecoveryAction.REDUCE_CONTEXT

    def test_server_error(self):
        result = classify_error("Internal server error", status_code=500)
        assert result.category == ErrorCategory.SERVER
        assert result.is_retryable is True

    def test_network_error(self):
        result = classify_error("connection reset by peer DNS lookup failed")
        assert result.category == ErrorCategory.NETWORK

    def test_unknown_error(self):
        result = classify_error("Something weird happened")
        assert result.category == ErrorCategory.UNKNOWN
        assert result.is_retryable is False

    def test_exception_input(self):
        result = classify_error(ValueError("Rate limit hit"))
        assert result.category == ErrorCategory.RATE_LIMIT
