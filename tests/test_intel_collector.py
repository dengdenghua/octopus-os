"""Implementation note."""

from __future__ import annotations

import time

import pytest

from runtime.execution.suckers import Skill, SkillRegistry
from runtime.memory.journal import InMemoryJournal
from runtime.safety.recovery import (
    CollectorConfig,
    IntelCollector,
    IntelSource,
)

# ═══════════════════════════════════════════════════════════
# Fixtures · mock web_search + fetch_url
# ═══════════════════════════════════════════════════════════


@pytest.fixture
def mock_registry():
    r = SkillRegistry()

    def fake_search(query: str = "", max_results: int = 5, **_kw):
        return {
            "query": query,
            "backend": "mock",
            "results": [
                {"title": f"Result for {query}", "url": "https://mock/1", "snippet": "S1"},
                {"title": f"Another for {query}", "url": "https://mock/2", "snippet": "S2"},
            ][:max_results],
        }

    def fake_fetch(url: str = "", **_kw):
        if "bad" in url:
            return {"error": "http_error"}
        return {
            "url": url,
            "status_code": 200,
            "content": f"mock body of {url}",
            "length": 42,
            "truncated": False,
        }

    r.register(
        Skill(
            name="web_search",
            trusted_source="skill://public/web_search",
            handler=fake_search,
        ),
        verify_tests=False,
    )
    r.register(
        Skill(
            name="fetch_url",
            trusted_source="skill://public/fetch_url",
            handler=fake_fetch,
        ),
        verify_tests=False,
    )
    return r


@pytest.fixture
def journal():
    return InMemoryJournal()


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestRunOnce:
    def test_basic_run(self, mock_registry, journal):
        sources = [
            IntelSource(source_id="ai_news", query="AI news"),
            IntelSource(source_id="sec_alerts", query="security alerts"),
        ]
        collector = IntelCollector(sources=sources, journal=journal, registry=mock_registry)
        report = collector.run_once()

        assert report.sources_scanned == 2
        assert report.searches_succeeded == 2
        assert report.searches_failed == 0
        assert report.urls_fetched == 0  # Implementation note.
        # Implementation note.
        assert len(journal.read_by_type("step")) == 2
        assert len(journal.read_by_type("trajectory")) == 1

    def test_fetch_top_n_triggers_fetches(self, mock_registry, journal):
        sources = [
            IntelSource(source_id="s1", query="q1", fetch_top_n=2),
        ]
        report = IntelCollector(sources, journal, mock_registry).run_once()
        assert report.urls_fetched == 2
        # 1 search + 2 fetch = 3 steps
        steps = journal.read_by_type("step")
        assert len(steps) == 3

    def test_search_failure_captured(self, journal):
        """Implementation note."""
        r = SkillRegistry()
        # Implementation note.
        collector = IntelCollector(
            [IntelSource(source_id="x", query="q")],
            journal,
            r,
        )
        report = collector.run_once()
        assert report.searches_succeeded == 0
        assert report.searches_failed == 1

    def test_handler_exception_captured(self, journal):
        r = SkillRegistry()

        def boom(**kw):
            raise RuntimeError("network outage")

        r.register(
            Skill(
                name="web_search",
                trusted_source="skill://public/web_search",
                handler=boom,
            ),
            verify_tests=False,
        )
        report = IntelCollector([IntelSource(source_id="x", query="q")], journal, r).run_once()
        assert report.searches_failed == 1
        steps = journal.read_by_type("step")
        assert steps[0].step.result.status == "failed"
        assert "network outage" in (steps[0].step.result.error_type or "")

    def test_tags_propagate_to_result(self, mock_registry, journal):
        sources = [IntelSource(source_id="s1", query="q", tags=["weekly", "security"])]
        IntelCollector(sources, journal, mock_registry).run_once()
        step_event = journal.read_by_type("step")[0]
        assert "weekly" in step_event.step.result.stderr_tags
        assert "security" in step_event.step.result.stderr_tags


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestBackgroundLoop:
    def test_start_stop_is_clean(self, mock_registry, journal):
        sources = [IntelSource(source_id="x", query="q", frequency_seconds=60)]
        c = IntelCollector(sources, journal, mock_registry)
        assert not c.is_running
        c.start_background(tick_seconds=1)
        time.sleep(0.2)
        assert c.is_running
        c.stop(timeout=2.0)
        assert not c.is_running

    def test_runs_immediately_on_start(self, mock_registry, journal):
        """Implementation note."""
        sources = [IntelSource(source_id="x", query="q", frequency_seconds=60)]
        c = IntelCollector(
            sources,
            journal,
            mock_registry,
            config=CollectorConfig(stop_after_n_runs=1),
        )
        c.start_background(tick_seconds=1)
        # Implementation note.
        deadline = time.time() + 3.0
        while c.is_running and time.time() < deadline:
            time.sleep(0.05)
        assert c.run_count >= 1

    def test_cannot_start_twice(self, mock_registry, journal):
        c = IntelCollector(
            [IntelSource(source_id="x", query="q", frequency_seconds=60)],
            journal,
            mock_registry,
        )
        c.start_background(tick_seconds=5)
        try:
            with pytest.raises(RuntimeError, match="already running"):
                c.start_background(tick_seconds=1)
        finally:
            c.stop()


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestTrajectoryOutput:
    def test_trajectory_includes_all_steps(self, mock_registry, journal):
        sources = [
            IntelSource(source_id="a", query="qa", fetch_top_n=1),
            IntelSource(source_id="b", query="qb", fetch_top_n=1),
        ]
        IntelCollector(sources, journal, mock_registry).run_once()
        trajs = journal.read_by_type("trajectory")
        assert len(trajs) == 1
        traj = trajs[0].trajectory
        # 2 searches + 2 fetches = 4 steps
        assert traj.step_count == 4
        assert traj.strategy_id == "intel_scan"

    def test_trajectory_success_iff_all_searches_ok(self, journal):
        r = SkillRegistry()
        r.register(
            Skill(
                name="web_search",
                trusted_source="skill://public/web_search",
                handler=lambda **kw: {"error": "fail"},
            ),
            verify_tests=False,
        )
        IntelCollector([IntelSource(source_id="x", query="q")], journal, r).run_once()
        traj = journal.read_by_type("trajectory")[0].trajectory
        assert not traj.outcome.success


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestDownstreamCompat:
    def test_rule_extractor_reads_intel_trajectories(self, mock_registry, journal):
        """Implementation note."""
        from runtime.safety.recovery import RuleExtractor

        sources = [IntelSource(source_id="x", query="q")]
        IntelCollector(sources, journal, mock_registry).run_once()

        extractor = RuleExtractor(journal=journal)
        report = extractor.extract()
        # Implementation note.
        assert report.trajectories_scanned >= 1
        assert report.rules_produced == []

    def test_failed_intel_produces_rules(self, journal):
        """Implementation note."""
        from runtime.safety.recovery import ExtractorConfig, RuleExtractor

        r = SkillRegistry()
        r.register(
            Skill(
                name="web_search",
                trusted_source="skill://public/web_search",
                handler=lambda **kw: {
                    "query": kw.get("query", ""),
                    "backend": "mock",
                    "results": [{"url": "https://bad/x", "title": "t", "snippet": ""}],
                },
            ),
            verify_tests=False,
        )
        r.register(
            Skill(
                name="fetch_url",
                trusted_source="skill://public/fetch_url",
                handler=lambda **kw: {"error": "http_error"},
            ),
            verify_tests=False,
        )

        sources = [IntelSource(source_id=f"s{i}", query=f"q{i}", fetch_top_n=1) for i in range(5)]
        IntelCollector(sources, journal, r).run_once()

        # Implementation note.
        # Implementation note.
        report = RuleExtractor(
            journal=journal,
            config=ExtractorConfig(min_hits=3, include_partial_as_failure=True),
        ).extract()
        # Implementation note.
        rule_sucker_ids = [r.sucker_id for r in report.rules_produced]
        assert "fetch_url" in rule_sucker_ids
