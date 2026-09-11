"""Implementation note."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from runtime.platform.models import (
    DEFAULT_TRUST_BY_TYPE,
    TRUST_INFERENCE_CAP,
    TRUST_REM_CAP,
    ContextPacket,
    ContextSegment,
    CostEntry,
    QuotaAllocation,
    Source,
    default_source,
)


class TestSource:
    def test_trust_score_bounded(self):
        with pytest.raises(ValidationError):
            Source(source_id="x", source_type="user", trust_score=1.5)

        with pytest.raises(ValidationError):
            Source(source_id="x", source_type="user", trust_score=-0.1)

    def test_default_source_uses_type_prior(self):
        s = default_source("alice", "user")
        assert s.trust_score == DEFAULT_TRUST_BY_TYPE["user"]

    def test_inference_cap(self):
        """Implementation note."""
        assert TRUST_INFERENCE_CAP == 0.5
        assert TRUST_REM_CAP == 0.4

    def test_frozen(self):
        s = Source(source_id="x", source_type="user")
        with pytest.raises(ValidationError):
            s.trust_score = 0.9  # type: ignore[misc]


class TestCostEntry:
    def test_tokens_computed_property(self):
        c = CostEntry(tokens_in=100, tokens_out=50, usd=0.01)
        assert c.tokens == 150

    def test_negative_rejected(self):
        with pytest.raises(ValidationError):
            CostEntry(tokens_in=-1, tokens_out=0, usd=0.0)

    def test_addition(self):
        a = CostEntry(tokens_in=10, tokens_out=5, usd=0.001, latency_ms=100)
        b = CostEntry(tokens_in=20, tokens_out=10, usd=0.002, latency_ms=200)
        c = a + b
        assert c.tokens_in == 30
        assert c.tokens_out == 15
        assert c.usd == pytest.approx(0.003)
        # Implementation note.
        assert c.latency_ms == 200


class TestQuotaAllocation:
    def test_default_sums_to_one(self):
        """RCP-I9 · quotas sum == 1.0。"""
        q = QuotaAllocation(system=0.15, suckers=0.10, memory=0.30, history=0.45)
        assert abs(q.system + q.suckers + q.memory + q.history - 1.0) < 1e-6

    def test_reject_invalid_sum(self):
        with pytest.raises(ValidationError):
            QuotaAllocation(system=0.5, suckers=0.5, memory=0.5, history=0.5)

    def test_as_tokens_distribution(self):
        q = QuotaAllocation(system=0.15, suckers=0.10, memory=0.30, history=0.45)
        d = q.as_tokens(total_budget=10_000)
        assert d["system"] == 1_500
        assert d["history"] == 4_500


class TestContextPacket:
    def test_tokens_used_counts_segments(self):
        p = ContextPacket(
            total_budget_tokens=10_000,
            segments=[
                ContextSegment(bucket="system", content="x", tokens_estimated=500),
                ContextSegment(bucket="history", content="y", tokens_estimated=3_000),
            ],
        )
        assert p.tokens_used == 3_500
        assert p.tokens_by_bucket == {"system": 500, "history": 3_000}

    def test_over_budget_detection(self):
        p = ContextPacket(
            total_budget_tokens=100,
            segments=[ContextSegment(bucket="system", content="x", tokens_estimated=200)],
        )
        assert p.over_budget()

    def test_bucket_overflow(self):
        p = ContextPacket(
            total_budget_tokens=10_000,
            segments=[
                ContextSegment(bucket="history", content="x", tokens_estimated=6_000),
            ],
        )
        overflow = p.bucket_overflow()
        # history default quota = 45% = 4500; used 6000; overflow = 1500
        assert overflow["history"] == pytest.approx(1_500, abs=1)

    def test_json_round_trip(self):
        p = ContextPacket(
            total_budget_tokens=5_000,
            segments=[ContextSegment(bucket="system", content="hi", tokens_estimated=10)],
        )
        j = p.model_dump_json()
        p2 = ContextPacket.model_validate_json(j)
        assert p2.tokens_used == p.tokens_used
        assert p2.packet_id == p.packet_id
