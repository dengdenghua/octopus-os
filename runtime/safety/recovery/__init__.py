"""Self-evolution subsystem — biomimetic alias: *Regeneration*.

Despite the directory name, this is NOT crash/error recovery. It is the
agent's self-improvement loop: GEPA prompt/skill optimization, the genome
registry, evolution datasets + constraints, and external-session mining.
Engineering term: **self-evolution**. See docs/architecture/module-map.md.
"""

from .evolution_constraints import (
    EvolutionConstraintConfig,
    EvolutionConstraintResult,
    EvolutionConstraintValidator,
    serialize_constraint_results,
)
from .evolution_dataset import (
    EvolutionDataset,
    EvolutionDatasetBuilder,
    EvolutionExample,
    FailureCluster,
)
from .external_importers import (
    ImportedSessionSample,
    SessionImportReport,
    build_external_session_dataset,
    collect_external_session_failures,
    discover_external_session_roots,
    import_external_sessions,
)
from .genome_registry import (
    GenomeRegistry,
    GenomeRegistryConfig,
)
from .intel_collector import (
    CollectorConfig,
    IntelCollector,
    IntelRunReport,
    IntelSource,
)
from .kg_updater import KGUpdater, KGUpdateReport, persist_kg_from_journal
from .lightweight_shadow import (
    ShadowConfig,
    ShadowResult,
    lightweight_shadow_validate,
)
from .memory_consolidator import (
    ConsolidatedMemory,
    ConsolidationReport,
    ConsolidatorConfig,
    MemoryConsolidator,
    MemoryScope,
    filter_memories_for_agent,
    format_memories_for_prompt,
)
from .native_evolution_eval import (
    NativeEvolutionScore,
    NativeEvolutionWeights,
    evaluate_front_native,
    score_candidate_native,
)
from .native_llm_replay import (
    LLMReplayCandidateReport,
    LLMReplayCaseResult,
    LLMReplayReport,
    replay_llm_candidates,
)
from .native_replay import (
    ReplayCandidateReport,
    ReplayCase,
    ReplayCaseResult,
    ReplayReport,
    build_replay_cases,
    replay_candidate,
    replay_candidates,
)
from .native_replay_sandbox import (
    SandboxReplayCandidateReport,
    SandboxReplayCaseResult,
    SandboxReplayReport,
    run_sandbox_replay,
)
from .native_turn_replay import (
    TurnReplayCandidateReport,
    TurnReplayCase,
    TurnReplayCaseResult,
    TurnReplayReport,
    build_turn_replay_cases,
    replay_turn_candidates,
)
from .optimizer_backends import (
    OptimizerBackend,
    OptimizerRunConfig,
    OptimizerRunContext,
    available_optimizer_backends,
    get_optimizer_backend,
    optimize_with_backend,
)
from .recipe_evaluator import (
    RecipeEvaluationReport,
    RecipeEvaluator,
    RecipeEvaluatorConfig,
    RecipeScore,
    format_recipe_report,
)
from .rule_extractor import (
    ExtractorConfig,
    LearnedRule,
    RuleExtractionReport,
    RuleExtractor,
    format_rules_for_prompt,
)
from .skill_forge import (
    ForgeConfig,
    ForgedSkillCandidate,
    SkillForge,
    SkillForgeResult,
    UnsafeSkillPromotionError,
    pattern_signature,
)
from .workflow_applier import (
    ApplyOutcome,
    ApplyResult,
    apply_proposals_to_rules,
)
from .workflow_rewriter import (
    RewriteProposal,
    RewriterConfig,
    RewriteReport,
    WorkflowRewriter,
    format_proposals_for_review,
)

__all__ = [
    "CollectorConfig",
    "ExtractorConfig",
    "ForgeConfig",
    "ForgedSkillCandidate",
    "GenomeRegistry",
    "GenomeRegistryConfig",
    "IntelCollector",
    "IntelRunReport",
    "IntelSource",
    "ConsolidatedMemory",
    "ConsolidationReport",
    "ConsolidatorConfig",
    "EvolutionConstraintConfig",
    "EvolutionConstraintResult",
    "EvolutionConstraintValidator",
    "EvolutionDataset",
    "EvolutionDatasetBuilder",
    "EvolutionExample",
    "FailureCluster",
    "ImportedSessionSample",
    "KGUpdater",
    "KGUpdateReport",
    "LearnedRule",
    "persist_kg_from_journal",
    "MemoryConsolidator",
    "MemoryScope",
    "LLMReplayCandidateReport",
    "LLMReplayCaseResult",
    "LLMReplayReport",
    "NativeEvolutionScore",
    "NativeEvolutionWeights",
    "OptimizerBackend",
    "OptimizerRunConfig",
    "OptimizerRunContext",
    "ReplayCandidateReport",
    "ReplayCase",
    "ReplayCaseResult",
    "ReplayReport",
    "SandboxReplayCandidateReport",
    "SandboxReplayCaseResult",
    "SandboxReplayReport",
    "SessionImportReport",
    "TurnReplayCandidateReport",
    "TurnReplayCase",
    "TurnReplayCaseResult",
    "TurnReplayReport",
    "filter_memories_for_agent",
    "format_memories_for_prompt",
    "RuleExtractionReport",
    "RuleExtractor",
    "ShadowConfig",
    "ShadowResult",
    "SkillForge",
    "SkillForgeResult",
    "UnsafeSkillPromotionError",
    "RecipeEvaluationReport",
    "RecipeEvaluator",
    "RecipeEvaluatorConfig",
    "RecipeScore",
    "RewriteProposal",
    "RewriteReport",
    "RewriterConfig",
    "WorkflowRewriter",
    "ApplyOutcome",
    "ApplyResult",
    "apply_proposals_to_rules",
    "available_optimizer_backends",
    "build_external_session_dataset",
    "format_proposals_for_review",
    "format_recipe_report",
    "format_rules_for_prompt",
    "get_optimizer_backend",
    "evaluate_front_native",
    "build_replay_cases",
    "build_turn_replay_cases",
    "collect_external_session_failures",
    "discover_external_session_roots",
    "import_external_sessions",
    "lightweight_shadow_validate",
    "optimize_with_backend",
    "pattern_signature",
    "score_candidate_native",
    "replay_llm_candidates",
    "replay_candidate",
    "replay_candidates",
    "replay_turn_candidates",
    "run_sandbox_replay",
    "serialize_constraint_results",
]
