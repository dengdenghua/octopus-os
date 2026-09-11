"""Tests for the run_batch_cron CLI entry point.

Covers the wiring that turns ``python -m runtime.safety.evolution.run_batch_cron``
into a one-line cron call: argument parsing, judge resolution defaults,
no-op behaviour when no judge is wired, exit codes.
"""

from __future__ import annotations

import pytest

from runtime.safety.evolution import run_batch_cron
from runtime.safety.evolution.guard_judge import (
    GuardJudgeVerdict,
    null_guard_judge,
)

# ══════════════════════════════════════════════════════════════════
# Argument parsing
# ══════════════════════════════════════════════════════════════════


class TestArgParsing:
    def test_defaults(self) -> None:
        args = run_batch_cron._parse_args([])
        assert args.max_hits == 50
        assert args.failure_streak_limit == 5
        assert args.dry_run is False
        assert args.verbose is False

    def test_max_hits_override(self) -> None:
        args = run_batch_cron._parse_args(["--max-hits", "200"])
        assert args.max_hits == 200

    def test_dry_run_flag(self) -> None:
        args = run_batch_cron._parse_args(["--dry-run"])
        assert args.dry_run is True

    def test_failure_streak_override(self) -> None:
        args = run_batch_cron._parse_args(["--failure-streak-limit", "10"])
        assert args.failure_streak_limit == 10

    def test_verbose_short_flag(self) -> None:
        args = run_batch_cron._parse_args(["-v"])
        assert args.verbose is True


# ══════════════════════════════════════════════════════════════════
# Judge resolution
# ══════════════════════════════════════════════════════════════════


class TestJudgeResolution:
    def test_no_provider_returns_null_judge(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Force the import inside _resolve_judge to fail.
        import sys

        original = sys.modules.get("runtime.platform.process.service_provider")
        sys.modules["runtime.platform.process.service_provider"] = None  # type: ignore[assignment]
        try:
            judge = run_batch_cron._resolve_judge()
            assert judge is null_guard_judge
        finally:
            if original is not None:
                sys.modules["runtime.platform.process.service_provider"] = original
            else:
                sys.modules.pop("runtime.platform.process.service_provider", None)

    def test_resolved_judge_used_when_available(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Inject a fake judge into the provider; _resolve_judge should
        # find it. We can't easily reach the real service_provider in
        # all envs so we monkeypatch the get_provider import directly.
        from runtime.platform.process.service_provider import get_provider

        def fake_judge(label: str, msg: str, traj: str) -> GuardJudgeVerdict:
            return GuardJudgeVerdict(action="true_positive")

        get_provider().register_instance("guard_judge", fake_judge)
        try:
            judge = run_batch_cron._resolve_judge()
            assert judge is fake_judge
        finally:
            # Clean up — unregister so other tests don't see this.
            get_provider().register_instance("guard_judge", None)


# ══════════════════════════════════════════════════════════════════
# main() exit codes
# ══════════════════════════════════════════════════════════════════


class TestMainEntry:
    def test_no_judge_returns_zero(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # No judge wired → null_guard_judge → batch is a no-op.
        # Provider may or may not have guard_judge registered from
        # other tests; explicitly clear it.
        from runtime.platform.process.service_provider import get_provider

        get_provider().register_instance("guard_judge", None)

        rc = run_batch_cron.main([])
        assert rc == 0
        captured = capsys.readouterr()
        assert "no judge" in captured.out.lower()

    def test_dry_run_returns_zero(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from runtime.platform.process.service_provider import get_provider

        def stub_judge(label: str, msg: str, traj: str) -> GuardJudgeVerdict:
            raise AssertionError("judge must not be called in dry run")

        get_provider().register_instance("guard_judge", stub_judge)
        try:
            rc = run_batch_cron.main(["--dry-run"])
            assert rc == 0
            captured = capsys.readouterr()
            assert "dry run" in captured.out.lower()
        finally:
            get_provider().register_instance("guard_judge", None)

    def test_judge_exception_propagates_to_exit_1(
        self,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Force run_judge_batch itself to blow up — main() must catch
        # and return 1, not let the cron job inherit the traceback.
        def boom(**kwargs):
            raise RuntimeError("telemetry exploded")

        monkeypatch.setattr(run_batch_cron, "run_judge_batch", boom)
        rc = run_batch_cron.main([])
        assert rc == 1
        captured = capsys.readouterr()
        assert "ERROR" in captured.err
