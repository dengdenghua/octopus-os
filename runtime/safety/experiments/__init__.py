"""Prompt A/B experimentation subsystem — biomimetic alias: *Camouflage*.

Runs prompt variants as controlled experiments: A/B splitting, Pareto
selection, mutation/optimization, and auto-retire of losing variants.
Engineering term: **prompt A/B testing & variant lifecycle**.
See docs/architecture/module-map.md.
"""

from .auto_retire import (
    AutoRetireConfig,
    AutoRetireScheduler,
    AutoRetireStats,
    EvolverStepTriggered,
    VariantBoosted,
    VariantRetired,
)
from .pareto import pareto_frontier_by_name
from .prompt_evolver import EvolutionPolicy, EvolutionStep, PromptEvolver
from .prompt_mutator import MutationProposal, PromptMutator
from .prompt_optimizer import (
    PromptOptimizer,
    PromptVariant,
    VariantReport,
    dump_variants_to_yaml,
    load_variants_from_yaml,
)
from .variant import ABSplitter, Variant, VariantStats

__all__ = [
    "ABSplitter",
    "AutoRetireConfig",
    "AutoRetireScheduler",
    "AutoRetireStats",
    "EvolutionPolicy",
    "EvolutionStep",
    "EvolverStepTriggered",
    "MutationProposal",
    "PromptEvolver",
    "PromptMutator",
    "PromptOptimizer",
    "PromptVariant",
    "Variant",
    "VariantBoosted",
    "VariantReport",
    "VariantRetired",
    "VariantStats",
    "dump_variants_to_yaml",
    "load_variants_from_yaml",
    "pareto_frontier_by_name",
]
