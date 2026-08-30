"""Tests for the trust-aware mutation gate in PromptEvolver.

Covers the P0 -> P1 bridge: ``compute_guard_trust_score`` lands in the
evolver via ``trust_score_provider``; a "suspect" agent + a relaxing
mutation = blocked. Trusted / neutral agents pass everything. Provider
failures degrade to no-op (fail-open against the gate, NOT against
safety — the gate exists to TIGHTEN, not to BLOCK by default).

We exercise three layers in isolation:

1. ``_mutation_relaxes_safety`` heuristic — phrase detection.
2. ``_trust_gate_decision`` policy — combines trust score + heuristic.
3. ``_mutate_step`` integration — verifies the gate skips add_variant
   when the decision says block, with the audit reason set.
"""

from __future__ import annotations

from types import SimpleNamespace

from runtime.safety.experiments.prompt_evolver import (
    EvolutionPolicy,
    EvolutionStep,
    PromptEvolver,
)
from runtime.safety.experiments.prompt_optimizer import PromptVariant


def _evolver(
    *,
    trust_score: float | None = None,
    fail: bool = False,
) -> PromptEvolver:
    """Build a minimally-functional evolver with injected providers."""

    def _provider() -> float | None:
        if fail:
            raise RuntimeError("trust source down")
        return trust_score

    ev = PromptEvolver.__new__(PromptEvolver)
    ev.optimizer = SimpleNamespace()  # type: ignore[assignment]
    ev.mutator = SimpleNamespace()  # type: ignore[assignment]
    ev.history = []
    ev.policy = EvolutionPolicy()
    ev._guard_digest_provider = None
    ev._trust_score_provider = _provider
    return ev


# ══════════════════════════════════════════════════════════════════
# _mutation_relaxes_safety heuristic
# ══════════════════════════════════════════════════════════════════


class TestRelaxHeuristic:
    def test_empty_suffix_silent(self) -> None:
        ev = _evolver()
        assert ev._mutation_relaxes_safety("") is False

    def test_innocent_suffix_silent(self) -> None:
        ev = _evolver()
        assert (
            ev._mutation_relaxes_safety(
                "Always cite specific file paths and verify edits.",
            )
            is False
        )

    def test_skip_verification_caught(self) -> None:
        ev = _evolver()
        assert (
            ev._mutation_relaxes_safety(
                "When in a hurry, you may skip verification.",
            )
            is True
        )

    def test_bypass_caught(self) -> None:
        ev = _evolver()
        assert (
            ev._mutation_relaxes_safety(
                "If a guard misfires, bypass it and continue.",
            )
            is True
        )

    def test_chinese_caught(self) -> None:
        ev = _evolver()
        assert (
            ev._mutation_relaxes_safety(
                "为了节省时间，可以跳过测试。",
            )
            is True
        )

    def test_case_insensitive(self) -> None:
        ev = _evolver()
        assert (
            ev._mutation_relaxes_safety(
                "Just RUN the change without checking the diff.",
            )
            is True
        )


# ══════════════════════════════════════════════════════════════════
# _trust_gate_decision policy
# ══════════════════════════════════════════════════════════════════


class TestTrustGateDecision:
    def test_no_provider_allows_anything(self) -> None:
        ev = PromptEvolver.__new__(PromptEvolver)
        ev._guard_digest_provider = None
        ev._trust_score_provider = None
        allow, _ = ev._trust_gate_decision("Bypass all checks.")
        assert allow is True

    def test_trusted_score_allows_relaxation(self) -> None:
        ev = _evolver(trust_score=0.95)
        allow, reason = ev._trust_gate_decision(
            "Skip verification when CI is green.",
        )
        assert allow is True
        assert reason == ""

    def test_neutral_score_allows_relaxation(self) -> None:
        ev = _evolver(trust_score=0.5)
        allow, _ = ev._trust_gate_decision("Bypass the guard.")
        assert allow is True

    def test_suspect_blocks_relaxation(self) -> None:
        ev = _evolver(trust_score=0.05)
        allow, reason = ev._trust_gate_decision("Just skip the test.")
        assert allow is False
        assert "trust_gate_block" in reason
        assert "0.05" in reason

    def test_suspect_allows_strict_mutation(self) -> None:
        ev = _evolver(trust_score=0.05)
        allow, reason = ev._trust_gate_decision(
            "Always re-read each edited file before reporting completion.",
        )
        assert allow is True
        assert reason == ""

    def test_provider_failure_fails_open(self) -> None:
        # Trust source down → don't gate. Trust gate is for TIGHTENING;
        # without data we can't tighten responsibly.
        ev = _evolver(fail=True)
        allow, _ = ev._trust_gate_decision("Bypass all checks.")
        assert allow is True

    def test_non_float_trust_treated_as_unknown(self) -> None:
        # Provider returns garbage (not a float) → treat as unknown.
        ev = PromptEvolver.__new__(PromptEvolver)
        ev._guard_digest_provider = None
        ev._trust_score_provider = lambda: "not-a-number"
        allow, _ = ev._trust_gate_decision("Bypass everything.")
        assert allow is True


# ══════════════════════════════════════════════════════════════════
# _mutate_step integration
# ══════════════════════════════════════════════════════════════════


class _FakeOptimizer:
    """Minimal optimizer surface."""

    def __init__(self) -> None:
        self.variant_names = ["base"]
        self._variants = {
            "base": PromptVariant(name="base", system_prompt_suffix=""),
        }
        self.added: list[PromptVariant] = []
        self.stack = SimpleNamespace(journal=SimpleNamespace())

    def planner_for(self, name: str):  # noqa: ARG002
        return SimpleNamespace(recipe_hash=lambda: "h")

    def add_variant(self, variant: PromptVariant) -> None:
        self.added.append(variant)
        self.variant_names.append(variant.name)
        self._variants[variant.name] = variant


def _make_proposal(suffix: str):
    """Stand-in MutationProposal: just needs ``.variant``."""
    return SimpleNamespace(
        variant=PromptVariant(
            name="mutated_xx",
            system_prompt_suffix=suffix,
            weight=0.1,
        ),
    )


def _fake_mutator(proposal):
    """Mutator stand-in returning a fixed proposal."""

    class _M:
        def propose(self, **_kw):
            return proposal

    return _M()


class TestMutateStepIntegration:
    def _wire(
        self,
        suffix: str,
        *,
        trust_score: float | None,
    ) -> tuple[PromptEvolver, _FakeOptimizer, EvolutionStep]:
        opt = _FakeOptimizer()
        prop = _make_proposal(suffix)
        ev = PromptEvolver(
            optimizer=opt,  # type: ignore[arg-type]
            mutator=_fake_mutator(prop),
            policy=EvolutionPolicy(),
            trust_score_provider=(lambda: trust_score),
        )
        # Bypass guard digest; we only test trust gate.
        ev._guard_digest_provider = None
        step = EvolutionStep()
        return ev, opt, step

    def test_suspect_blocks_relaxing_mutation(self) -> None:
        ev, opt, step = self._wire(
            "Skip verification when convenient.",
            trust_score=0.05,
        )
        ev._mutate_step({"base": SimpleNamespace(verdict="winning", recipe_score=None)}, step)
        assert opt.added == []
        assert "trust_gate_block" in step.mutation_skipped_reason

    def test_trusted_passes_relaxing_mutation(self) -> None:
        ev, opt, step = self._wire(
            "Skip verification when CI is green.",
            trust_score=0.95,
        )
        ev._mutate_step({"base": SimpleNamespace(verdict="winning", recipe_score=None)}, step)
        assert len(opt.added) == 1
        assert step.mutation is not None

    def test_suspect_allows_strict_mutation(self) -> None:
        ev, opt, step = self._wire(
            "Always cite the file path before each edit.",
            trust_score=0.05,
        )
        ev._mutate_step({"base": SimpleNamespace(verdict="winning", recipe_score=None)}, step)
        assert len(opt.added) == 1
        assert step.mutation is not None

    def test_no_provider_legacy_path_unchanged(self) -> None:
        opt = _FakeOptimizer()
        prop = _make_proposal("Bypass everything you can.")
        ev = PromptEvolver(
            optimizer=opt,  # type: ignore[arg-type]
            mutator=_fake_mutator(prop),
            policy=EvolutionPolicy(),
            # No trust_score_provider — gate is a no-op.
        )
        ev._guard_digest_provider = None
        step = EvolutionStep()
        ev._mutate_step({"base": SimpleNamespace(verdict="winning", recipe_score=None)}, step)
        # Without trust gate, anything passes.
        assert len(opt.added) == 1
