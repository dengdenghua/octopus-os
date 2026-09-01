# Safety · Recovery

> MemoryConsolidator · SkillForge · KG updater · 从 trajectory 反哺记忆 / 技能 / 图谱。

**Source**: `runtime/safety/recovery/`

## Package summary

Self-evolution subsystem — biomimetic alias: *Regeneration*.

## Exports

- `CollectorConfig`
- `ExtractorConfig`
- `ForgeConfig`
- `ForgedSkillCandidate`
- `GenomeRegistry`
- `GenomeRegistryConfig`
- `IntelCollector`
- `IntelRunReport`
- `IntelSource`
- `ConsolidatedMemory`
- `ConsolidationReport`
- `ConsolidatorConfig`
- `EvolutionConstraintConfig`
- `EvolutionConstraintResult`
- `EvolutionConstraintValidator`
- `EvolutionDataset`
- `EvolutionDatasetBuilder`
- `EvolutionExample`
- `FailureCluster`
- `ImportedSessionSample`
- `KGUpdater`
- `KGUpdateReport`
- `LearnedRule`
- `persist_kg_from_journal`
- `MemoryConsolidator`
- `MemoryScope`
- `LLMReplayCandidateReport`
- `LLMReplayCaseResult`
- `LLMReplayReport`
- `NativeEvolutionScore`
- `NativeEvolutionWeights`
- `OptimizerBackend`
- `OptimizerRunConfig`
- `OptimizerRunContext`
- `ReplayCandidateReport`
- `ReplayCase`
- `ReplayCaseResult`
- `ReplayReport`
- `SandboxReplayCandidateReport`
- `SandboxReplayCaseResult`
- `SandboxReplayReport`
- `SessionImportReport`
- `TurnReplayCandidateReport`
- `TurnReplayCase`
- `TurnReplayCaseResult`
- `TurnReplayReport`
- `filter_memories_for_agent`
- `format_memories_for_prompt`
- `RuleExtractionReport`
- `RuleExtractor`
- `ShadowConfig`
- `ShadowResult`
- `SkillForge`
- `SkillForgeResult`
- `UnsafeSkillPromotionError`
- `RecipeEvaluationReport`
- `RecipeEvaluator`
- `RecipeEvaluatorConfig`
- `RecipeScore`
- `RewriteProposal`
- `RewriteReport`
- `RewriterConfig`
- `WorkflowRewriter`
- `ApplyOutcome`
- `ApplyResult`
- `apply_proposals_to_rules`
- `available_optimizer_backends`
- `build_external_session_dataset`
- `format_proposals_for_review`
- `format_recipe_report`
- `format_rules_for_prompt`
- `get_optimizer_backend`
- `evaluate_front_native`
- `build_replay_cases`
- `build_turn_replay_cases`
- `collect_external_session_failures`
- `discover_external_session_roots`
- `import_external_sessions`
- `lightweight_shadow_validate`
- `optimize_with_backend`
- `pattern_signature`
- `score_candidate_native`
- `replay_llm_candidates`
- `replay_candidate`
- `replay_candidates`
- `replay_turn_candidates`
- `run_sandbox_replay`
- `serialize_constraint_results`

## Modules

| Module | Summary |
| --- | --- |
| `_gepa_failures.py` | Failure-sample collectors extracted from ``gepa_bridge.py``. |
| `_gepa_helpers.py` | Private helpers extracted from ``gepa_bridge.py``. |
| `evolution_constraints.py` | — |
| `evolution_dataset.py` | Unified dataset builder for regeneration and prompt evolution. |
| `evolution_router.py` | EvolutionRouter · route evolution candidates to the right forge. |
| `external_importers.py` | — |
| `forge_auto_tick.py` | RecipeForge auto-promote scheduler · the last-mile autonomy knob. |
| `genome_registry.py` | Genome Registry — versioned JSON snapshot store for system configuration. |
| `gepa_addendum_store.py` | — |
| `gepa_bridge.py` | Bridge between Echo's existing reflection layer and the GEPA prompt optimizer. |
| `gepa_optimizer.py` | GEPA-style prompt optimizer · 7th reflection path. |
| `gepa_runs.py` | — |
| `gepa_variants.py` | Multi-variant per-recipe addendums · turns GEPA from "one optimized prompt per recipe" into "N candidate prompts per recipe, traffic- split by weight, sticky per conversation". |
| `intel_collector.py` | — |
| `kg_updater.py` | — |
| `lightweight_shadow.py` | — |
| `memory_consolidator.py` | — |
| `native_evolution_eval.py` | — |
| `native_llm_replay.py` | — |
| `native_replay.py` | — |
| `native_replay_sandbox.py` | — |
| `native_turn_replay.py` | — |
| `optimizer_backends.py` | Pluggable prompt-optimizer backends for Echo evolution. |
| `recipe_evaluator.py` | — |
| `reflex_forge.py` | ReflexForge · auto-generate reflex rules from successful turns. |
| `rule_extractor.py` | — |
| `scheduler.py` | — |
| `skill_forge.py` | — |
| `tenant_scope.py` | Tenant-safe journal reads for learning and regeneration. |
| `variant_evaluator.py` | — |
| `workflow_applier.py` | — |
| `workflow_rewriter.py` | — |

## Who imports this

**40** file(s) reference this package:

- **`runtime/_cli_commands.py/`** · 1 file(s)
  - `runtime/_cli_commands.py`
- **`runtime/cli_reflect.py/`** · 1 file(s)
  - `runtime/cli_reflect.py`
- **`runtime/cli_serve.py/`** · 1 file(s)
  - `runtime/cli_serve.py`
- **`runtime/core/`** · 3 file(s)
  - `runtime/core/cerebrum/llm_planner.py`
  - `runtime/core/cerebrum/planner.py`
  - `runtime/core/graph_runtime/runtime.py`
- **`runtime/execution/`** · 3 file(s)
  - `runtime/execution/cron_context.py`
  - `runtime/execution/suckers/cron_skills.py`
  - `runtime/execution/suckers/memory_skills.py`
- **`runtime/memory/`** · 4 file(s)
  - `runtime/memory/diagnostics/wiki_compiler.py`
  - `runtime/memory/hemolymph/composer.py`
  - `runtime/memory/learning/deep_evolution.py`
  - `runtime/memory/learning/promotion_applier.py`
- **`runtime/platform/`** · 8 file(s)
  - `runtime/platform/capabilities/tenant_context.py`
  - `runtime/platform/ui/_app_agents.py`
  - `runtime/platform/ui/_app_stack.py`
  - `runtime/platform/ui/_reflex_admin_gepa_apply.py`
  - `runtime/platform/ui/_reflex_admin_gepa_autotick.py`
  - _… and 3 more_
- **`runtime/safety/`** · 5 file(s)
  - `runtime/safety/evolution/auto_trigger.py`
  - `runtime/safety/evolution/drift_monitor.py`
  - `runtime/safety/evolution/replay_latency_budget.py`
  - `runtime/safety/experiments/prompt_mutator.py`
  - `runtime/safety/experiments/prompt_optimizer.py`
- **`runtime/sensing/`** · 14 file(s)
  - `runtime/sensing/gateway/_agents_endpoints_system.py`
  - `runtime/sensing/gateway/_observability_helpers.py`
  - `runtime/sensing/gateway/_observability_journal.py`
  - `runtime/sensing/gateway/_observability_kg.py`
  - `runtime/sensing/gateway/_observability_rollback_panels.py`
  - _… and 9 more_

