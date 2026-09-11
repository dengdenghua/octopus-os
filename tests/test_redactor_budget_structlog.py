"""Tests for PII redaction, budget tracking, and structured logging:

1. PII Redactor (regex-based secret/PII scrubbing)
2. Token Budget Tracker (session-level cumulative ceiling)
3. Structured Logging (JSON events with correlation IDs)
"""

from __future__ import annotations

import io
import json
import logging
import threading

import pytest

from runtime.platform.models import CostEntry

# ═══════════════════════════════════════════════════════════════
# 1. PII Redactor
# ═══════════════════════════════════════════════════════════════


class TestRedactor:
    def test_redacts_anthropic_api_key(self):
        from runtime.platform.observability.redactor import redact_text

        text = "use sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789ABC for auth"
        out = redact_text(text)
        assert "sk-ant-api03" not in out
        assert "[REDACTED:api_key]" in out

    def test_redacts_openai_api_key(self):
        from runtime.platform.observability.redactor import redact_text

        out = redact_text("export OPENAI_API_KEY=sk-proj1234567890abcdefghijklmn")
        assert "sk-proj" not in out
        assert "[REDACTED:api_key]" in out

    def test_redacts_aws_access_key_id(self):
        from runtime.platform.observability.redactor import redact_text

        out = redact_text("AKIA1234567890ABCDEF is the key")
        assert "AKIA1234567890ABCDEF" not in out
        assert "[REDACTED:api_key]" in out

    def test_redacts_aws_secret(self):
        from runtime.platform.observability.redactor import redact_text

        # 40-char base64-like secret with explicit context word.
        out = redact_text('aws_secret_access_key="abcdefghijklmnopqrstuvwxyz0123456789ABCD"')
        assert "abcdefghijklmnopqrstuvwxyz0123456789ABCD" not in out
        assert "[REDACTED:aws_secret]" in out

    def test_redacts_email(self):
        from runtime.platform.observability.redactor import redact_text

        out = redact_text("Contact alice@example.com for details")
        assert "alice@example.com" not in out
        assert "[REDACTED:email]" in out

    def test_redacts_credit_card_with_luhn(self):
        from runtime.platform.observability.redactor import redact_text

        # Valid Visa test number.
        out = redact_text("Card: 4532 0151 1283 0366")
        assert "4532" not in out or "[REDACTED:credit_card]" in out

    def test_does_not_redact_random_16_digits(self):
        from runtime.platform.observability.redactor import redact_text

        # Order number that fails Luhn — should pass through unchanged.
        out = redact_text("Order ID: 1234567890123456")
        assert "[REDACTED:credit_card]" not in out

    def test_redacts_jwt(self):
        from runtime.platform.observability.redactor import redact_text

        jwt = (
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
            ".eyJzdWIiOiIxMjM0NTY3ODkwIn0"
            ".SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        )
        out = redact_text(f"Bearer {jwt}")
        assert "eyJ" not in out
        assert "[REDACTED:jwt]" in out

    def test_redacts_private_key_block(self):
        from runtime.platform.observability.redactor import redact_text

        text = (
            "config:\n"
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEpAIBAAKCAQEAxxxxxxxxxxxxxxxxxxxx\n"
            "-----END RSA PRIVATE KEY-----\n"
            "after"
        )
        out = redact_text(text)
        assert "MIIEpAIBAAKCAQEA" not in out
        assert "[REDACTED:private_key]" in out

    def test_idempotent_redaction(self):
        from runtime.platform.observability.redactor import redact_text

        once = redact_text("alice@example.com")
        twice = redact_text(once)
        assert once == twice

    def test_redact_with_report_returns_counts(self):
        from runtime.platform.observability.redactor import Redactor

        r = Redactor()
        out, report = r.redact_with_report(
            "alice@example.com and bob@example.org and a sk-proj1234567890abcdef key"
        )
        cats = {entry["category"] for entry in report}
        assert "email" in cats
        # Each email category shows the counts.
        for entry in report:
            if entry["category"] == "email":
                assert entry["count"] == 2

    def test_disable_category(self):
        from runtime.platform.observability.redactor import Redactor

        r = Redactor()
        r.disable("email")
        out = r.redact("alice@example.com")
        assert "alice@example.com" in out

    def test_add_custom_pattern(self):
        from runtime.platform.observability.redactor import Redactor

        r = Redactor()
        r.add_pattern("internal_id", r"\bINT-\d{6}\b")
        out = r.redact("ticket INT-123456 was filed")
        assert "INT-123456" not in out
        assert "[REDACTED:internal_id]" in out

    def test_redact_dict_recurses(self):
        from runtime.platform.observability.redactor import redact_dict

        data = {
            "user": {"email": "alice@example.com"},
            "tags": ["safe", "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789ABC"],
            "count": 42,
        }
        out = redact_dict(data)
        assert "alice@example.com" not in json.dumps(out)
        assert "sk-ant-api03" not in json.dumps(out)
        assert out["count"] == 42

    def test_empty_input_passthrough(self):
        from runtime.platform.observability.redactor import redact_text

        assert redact_text("") == ""
        assert redact_text(None) is None  # type: ignore[arg-type]


# ═══════════════════════════════════════════════════════════════
# 2. Token Budget Tracker
# ═══════════════════════════════════════════════════════════════


class TestTokenBudgetTracker:
    def test_record_accumulates(self):
        from runtime.platform.llm_infra.budget_tracker import TokenBudgetTracker

        t = TokenBudgetTracker()
        t.record("s1", CostEntry(tokens_in=100, tokens_out=50, usd=0.01))
        t.record("s1", CostEntry(tokens_in=20, tokens_out=10, usd=0.005))
        state = t.get("s1")
        assert state.tokens_used == 180  # 100+50+20+10
        assert abs(state.usd_used - 0.015) < 1e-9
        assert state.call_count == 2

    def test_isolated_per_session(self):
        from runtime.platform.llm_infra.budget_tracker import TokenBudgetTracker

        t = TokenBudgetTracker()
        t.record("s1", CostEntry(tokens_in=10, tokens_out=10))
        t.record("s2", CostEntry(tokens_in=5, tokens_out=5))
        assert t.get("s1").tokens_used == 20
        assert t.get("s2").tokens_used == 10

    def test_warning_fires_at_80_percent(self):
        from runtime.platform.llm_infra.budget_tracker import TokenBudgetTracker

        warnings: list[float] = []

        def on_warn(sid, state, threshold):
            warnings.append(threshold)

        t = TokenBudgetTracker(
            tokens_ceiling=100,
            on_warning=on_warn,
        )
        t.record("s1", CostEntry(tokens_in=50, tokens_out=30))  # 80%
        assert 0.80 in warnings

    def test_warning_fires_only_once_per_threshold(self):
        from runtime.platform.llm_infra.budget_tracker import TokenBudgetTracker

        warnings: list[float] = []

        def on_warn(sid, state, threshold):
            warnings.append(threshold)

        t = TokenBudgetTracker(tokens_ceiling=100, on_warning=on_warn)
        t.record("s1", CostEntry(tokens_in=80, tokens_out=0))  # 80%
        t.record("s1", CostEntry(tokens_in=5, tokens_out=0))  # 85%
        # 80% threshold should have fired exactly once.
        assert warnings.count(0.80) == 1

    def test_budget_exceeded_raises(self):
        from runtime.platform.llm_infra.budget_tracker import (
            BudgetExceeded,
            TokenBudgetTracker,
        )

        t = TokenBudgetTracker(tokens_ceiling=100)
        with pytest.raises(BudgetExceeded) as exc_info:
            t.record("s1", CostEntry(tokens_in=80, tokens_out=30))
        assert exc_info.value.dimension == "tokens"
        assert exc_info.value.session_id == "s1"

    def test_usd_ceiling_independent_of_tokens(self):
        from runtime.platform.llm_infra.budget_tracker import (
            BudgetExceeded,
            TokenBudgetTracker,
        )

        t = TokenBudgetTracker(usd_ceiling=1.0)
        with pytest.raises(BudgetExceeded) as exc_info:
            t.record("s1", CostEntry(tokens_in=10, tokens_out=10, usd=1.5))
        assert exc_info.value.dimension == "usd"

    def test_remaining_returns_none_when_no_ceiling(self):
        from runtime.platform.llm_infra.budget_tracker import TokenBudgetTracker

        t = TokenBudgetTracker()
        t.record("s1", CostEntry(tokens_in=100))
        rem = t.remaining("s1")
        assert rem["tokens"] is None
        assert rem["usd"] is None

    def test_remaining_decreases_with_usage(self):
        from runtime.platform.llm_infra.budget_tracker import TokenBudgetTracker

        t = TokenBudgetTracker(tokens_ceiling=1000)
        t.record("s1", CostEntry(tokens_in=300, tokens_out=200))
        rem = t.remaining("s1")
        assert rem["tokens"] == 500

    def test_per_session_ceiling_overrides_default(self):
        from runtime.platform.llm_infra.budget_tracker import (
            BudgetExceeded,
            TokenBudgetTracker,
        )

        t = TokenBudgetTracker(tokens_ceiling=10_000)
        # Override at first record — session keeps its own ceiling.
        with pytest.raises(BudgetExceeded):
            t.record(
                "s1",
                CostEntry(tokens_in=100, tokens_out=50),
                tokens_ceiling=100,
            )

    def test_reset_clears_session(self):
        from runtime.platform.llm_infra.budget_tracker import TokenBudgetTracker

        t = TokenBudgetTracker()
        t.record("s1", CostEntry(tokens_in=100))
        t.reset("s1")
        assert t.get("s1") is None

    def test_snapshot_all(self):
        from runtime.platform.llm_infra.budget_tracker import TokenBudgetTracker

        t = TokenBudgetTracker()
        t.record("s1", CostEntry(tokens_in=10))
        t.record("s2", CostEntry(tokens_in=20))
        snap = t.snapshot_all()
        assert len(snap) == 2
        sids = {s["session_id"] for s in snap}
        assert sids == {"s1", "s2"}

    def test_thread_safe_record(self):
        from runtime.platform.llm_infra.budget_tracker import TokenBudgetTracker

        t = TokenBudgetTracker()

        def worker():
            for _ in range(100):
                t.record("s1", CostEntry(tokens_in=1))

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        assert t.get("s1").tokens_used == 1000


# ═══════════════════════════════════════════════════════════════
# 3. Structured Logging
# ═══════════════════════════════════════════════════════════════


class TestStructuredLogging:
    @pytest.fixture
    def logger_with_capture(self):
        """Return (logger, buffer) with StructuredFormatter installed."""
        from runtime.platform.observability.structured_logging import StructuredFormatter

        buf = io.StringIO()
        handler = logging.StreamHandler(buf)
        handler.setFormatter(StructuredFormatter())
        logger = logging.getLogger(f"test.struct.{id(buf)}")
        logger.handlers = [handler]
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
        return logger, buf

    def _last_event(self, buf: io.StringIO) -> dict:
        line = buf.getvalue().strip().splitlines()[-1]
        return json.loads(line)

    def test_basic_fields_present(self, logger_with_capture):
        logger, buf = logger_with_capture
        logger.info("hello")
        ev = self._last_event(buf)
        assert ev["msg"] == "hello"
        assert ev["level"] == "INFO"
        assert "ts" in ev
        assert "logger" in ev

    def test_correlation_context_attached(self, logger_with_capture):
        from runtime.platform.observability.structured_logging import correlation_context

        logger, buf = logger_with_capture
        with correlation_context(session_id="sess-1", task_id="t1"):
            logger.info("inside")
        ev = self._last_event(buf)
        assert ev["session_id"] == "sess-1"
        assert ev["task_id"] == "t1"

    def test_correlation_nesting(self, logger_with_capture):
        from runtime.platform.observability.structured_logging import correlation_context

        logger, buf = logger_with_capture
        with correlation_context(session_id="outer"), correlation_context(task_id="inner"):
            logger.info("nested")
        ev = self._last_event(buf)
        assert ev["session_id"] == "outer"
        assert ev["task_id"] == "inner"

    def test_correlation_resets_on_exit(self, logger_with_capture):
        from runtime.platform.observability.structured_logging import correlation_context

        logger, buf = logger_with_capture
        with correlation_context(session_id="temp"):
            pass
        logger.info("after")
        ev = self._last_event(buf)
        assert "session_id" not in ev

    def test_extra_fields_propagate(self, logger_with_capture):
        logger, buf = logger_with_capture
        logger.info("step done", extra={"step_id": 7, "duration_ms": 42})
        ev = self._last_event(buf)
        assert ev["step_id"] == 7
        assert ev["duration_ms"] == 42

    def test_exception_serialized(self, logger_with_capture):
        logger, buf = logger_with_capture
        try:
            raise ValueError("kaboom")
        except ValueError:
            logger.exception("err")
        ev = self._last_event(buf)
        assert "exc" in ev
        assert "ValueError" in ev["exc"]
        assert "kaboom" in ev["exc"]

    def test_redact_option_scrubs_secrets(self):
        from runtime.platform.observability.structured_logging import StructuredFormatter

        buf = io.StringIO()
        handler = logging.StreamHandler(buf)
        handler.setFormatter(StructuredFormatter(redact=True))
        logger = logging.getLogger("test.struct.redact")
        logger.handlers = [handler]
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
        logger.info("key is sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789ABC")
        ev = json.loads(buf.getvalue().strip().splitlines()[-1])
        assert "sk-ant-api03" not in ev["msg"]
        assert "[REDACTED:api_key]" in ev["msg"]

    def test_extra_correlation_kwargs(self, logger_with_capture):
        from runtime.platform.observability.structured_logging import correlation_context

        logger, buf = logger_with_capture
        with correlation_context(session_id="s1", trace_id="trace-abc"):
            logger.info("event")
        ev = self._last_event(buf)
        assert ev["session_id"] == "s1"
        assert ev["trace_id"] == "trace-abc"

    def test_pydantic_model_in_extra_serializes(self, logger_with_capture):
        from pydantic import BaseModel

        class M(BaseModel):
            x: int
            y: str

        logger, buf = logger_with_capture
        logger.info("with pyd", extra={"payload": M(x=1, y="z")})
        ev = self._last_event(buf)
        assert ev["payload"] == {"x": 1, "y": "z"}

    def test_get_correlation_ids_outside_context(self):
        from runtime.platform.observability.structured_logging import get_correlation_ids

        # Outside any active scope → empty dict.
        ids = get_correlation_ids()
        assert ids == {} or all(v is None for v in ids.values())
