"""Tests for the P1 evolution-loop integration: GuardTelemetry digest
seeding the PromptMutator's user prompt + the PromptEvolver wiring.

These exercise the public seam (renderer + _build_user_prompt + the
evolver's defensive _fetch_guard_digest) without spinning up the full
mutator LLM call — that's exercised in the camouflage suite.
"""

from __future__ import annotations

from pathlib import Path

from runtime.safety.evolution.guard_telemetry import (
    GuardTelemetry,
    default_guard_digest_provider,
)
from runtime.safety.experiments.prompt_evolver import PromptEvolver
from runtime.safety.experiments.prompt_mutator import (
    _build_user_prompt,
    _render_guard_digest_for_prompt,
)
from runtime.safety.experiments.prompt_optimizer import PromptVariant


def _variant(name: str = "v1", suffix: str = "") -> PromptVariant:
    return PromptVariant(name=name, system_prompt_suffix=suffix)


# ══════════════════════════════════════════════════════════════════
# Renderer
# ══════════════════════════════════════════════════════════════════


class TestRenderGuardDigest:
    def test_empty_returns_empty_string(self) -> None:
        assert _render_guard_digest_for_prompt({"total_hits": 0}) == ""

    def test_none_returns_empty(self) -> None:
        assert _render_guard_digest_for_prompt(None) == ""  # type: ignore[arg-type]

    def test_no_dict_returns_empty(self) -> None:
        assert _render_guard_digest_for_prompt("not a dict") == ""  # type: ignore[arg-type]

    def test_with_hits_includes_total_and_dominant(self) -> None:
        d = {
            "total_hits": 42,
            "dominant_category": "test-quality",
            "by_category": {"test-quality": 22, "code-smell": 14, "security": 6},
            "tuning_candidates": [],
            "tuning_threshold": 20,
        }
        out = _render_guard_digest_for_prompt(d)
        assert "42 blocked" in out
        assert "test-quality" in out
        assert "Dominant failure category" in out

    def test_with_candidates_lists_top_offenders(self) -> None:
        d = {
            "total_hits": 100,
            "dominant_category": "code-smell",
            "by_category": {"code-smell": 55, "test-quality": 45},
            "tuning_candidates": [
                {"label": "magic-number guard", "count": 34},
                {"label": "weak-test guard", "count": 28},
            ],
            "tuning_threshold": 20,
        }
        out = _render_guard_digest_for_prompt(d)
        assert "magic-number guard" in out
        assert "34 firings" in out
        assert "weak-test guard" in out
        assert "steer the agent" in out

    def test_caps_top_offenders_at_5(self) -> None:
        d = {
            "total_hits": 999,
            "dominant_category": "code-smell",
            "by_category": {"code-smell": 999},
            "tuning_candidates": [{"label": f"guard-{i}", "count": 100} for i in range(10)],
            "tuning_threshold": 20,
        }
        out = _render_guard_digest_for_prompt(d)
        assert "guard-0" in out
        assert "guard-4" in out
        assert "guard-5" not in out  # 6th and beyond are dropped


# ══════════════════════════════════════════════════════════════════
# _build_user_prompt — splice point
# ══════════════════════════════════════════════════════════════════


class TestBuildUserPromptWithDigest:
    def test_no_digest_legacy_format(self) -> None:
        prompt = _build_user_prompt(
            _variant(suffix="be careful"),
            ["traj1: failed"],
        )
        assert "<current>" in prompt
        assert "be careful" in prompt
        assert "Recent failed trajectories" in prompt
        assert "Recent guard-firing" not in prompt

    def test_with_digest_appended(self) -> None:
        digest = {
            "total_hits": 50,
            "dominant_category": "test-quality",
            "by_category": {"test-quality": 30, "security": 20},
            "tuning_candidates": [{"label": "weak-test guard", "count": 25}],
            "tuning_threshold": 20,
        }
        prompt = _build_user_prompt(
            _variant(suffix="x"),
            ["traj1"],
            guard_digest=digest,
        )
        assert "Recent failed trajectories" in prompt
        assert "Recent guard-firing patterns" in prompt
        assert "weak-test guard" in prompt
        assert prompt.index("Recent failed trajectories") < prompt.index("Recent guard-firing")

    def test_empty_digest_not_appended(self) -> None:
        prompt = _build_user_prompt(
            _variant(suffix="x"),
            ["traj1"],
            guard_digest={"total_hits": 0},
        )
        assert "Recent guard-firing" not in prompt


# ══════════════════════════════════════════════════════════════════
# default_guard_digest_provider — closes the loop
# ══════════════════════════════════════════════════════════════════


class TestDefaultProvider:
    def test_returns_well_formed_dict_when_sink_empty(self) -> None:
        # The default singleton path may or may not exist depending on
        # other test orderings. Regardless, the provider must return a
        # dict (or None on failure) — never raise.
        result = default_guard_digest_provider()
        assert result is None or isinstance(result, dict)

    def test_returns_dict_with_real_hits(self, tmp_path: Path) -> None:
        # Wire a fresh sink at a tmp path, populate, then read back via
        # an explicit GuardTelemetry instance to mirror what the
        # default_guard_digest_provider does internally.
        sink = GuardTelemetry(path=tmp_path / "hits.jsonl")
        for _ in range(5):
            sink.record("magic-number guard", "code-smell")
        d = sink.digest()
        assert d["total_hits"] == 5
        assert d["dominant_category"] == "code-smell"


# ══════════════════════════════════════════════════════════════════
# PromptEvolver._fetch_guard_digest — defensive wrapper
# ══════════════════════════════════════════════════════════════════


class _FakeOptimizer:
    """Minimal stand-in for PromptOptimizer — only what __init__ needs."""


class _FakeMutator:
    """Minimal stand-in for PromptMutator."""


class TestEvolverFetchDigest:
    def _make(self, provider) -> PromptEvolver:
        # PromptEvolver.__init__ only stores the args it's given; the
        # actual stack/journal accesses happen in step(). We don't call
        # step() here — only _fetch_guard_digest, which uses just the
        # provider closure.
        ev = PromptEvolver.__new__(PromptEvolver)
        ev.optimizer = _FakeOptimizer()  # type: ignore[assignment]
        ev.mutator = _FakeMutator()  # type: ignore[assignment]
        ev.history = []
        from runtime.safety.experiments.prompt_evolver import EvolutionPolicy

        ev.policy = EvolutionPolicy()
        ev._guard_digest_provider = provider
        return ev

    def test_no_provider_returns_none(self) -> None:
        ev = self._make(None)
        assert ev._fetch_guard_digest() is None

    def test_provider_raises_returns_none(self) -> None:
        def boom() -> dict:
            raise RuntimeError("telemetry down")

        ev = self._make(boom)
        assert ev._fetch_guard_digest() is None

    def test_empty_digest_returns_none(self) -> None:
        ev = self._make(lambda: {"total_hits": 0})
        assert ev._fetch_guard_digest() is None

    def test_non_dict_returns_none(self) -> None:
        ev = self._make(lambda: "not a dict")
        assert ev._fetch_guard_digest() is None

    def test_real_digest_passes_through(self) -> None:
        d = {"total_hits": 5, "dominant_category": "x", "by_category": {"x": 5}}
        ev = self._make(lambda: d)
        assert ev._fetch_guard_digest() == d
