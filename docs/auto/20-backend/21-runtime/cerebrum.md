# Cerebrum · 规划器

> LLM Planner + Static Planner · 把自然语言意图拆成 TaskGraph。

**Source**: `runtime/core/cerebrum/`

## Exports

- `LLMPlanner`
- `PlannerError`
- `StaticPlanner`

## Modules

| Module | Summary |
| --- | --- |
| `_planner_helpers.py` | Pure helper functions extracted from :mod:`runtime.core.cerebrum.llm_planner`. |
| `_planner_parse.py` | Plan-JSON extraction + node validation for :class:`LLMPlanner`. |
| `_react_context_attachments.py` | User-message content assembly (attachments, images, JSONL manifest), message checkpoint (de)serialization helpers, and related-file prefetching. |
| `_react_context_code.py` | Compatibility exports for context builders moved out of the ReAct engine. |
| `_react_context_helpers.py` | Token estimation, context-compression helpers, and skill-catalog formatting for the ReAct loop. |
| `_react_context_project.py` | Project rules / git status / project-profile prompt builders. |
| `_react_execution_dispatch.py` | Tool dispatch / execution helpers for the ReAct loop. |
| `_react_execution_phase6d.py` | PHASE 6d — action dispatch + observation for the ReAct loop. |
| `_react_execution_phase6g.py` | PHASE 6g + 6d — loop-tail housekeeping and pre-dispatch guard cluster for the ReAct loop. |
| `_react_execution_progress.py` | Working-set / phase / progress-summary helpers and trajectory persistence + planner learning throttles for the ReAct loop. |
| `_react_execution_results.py` | Tool-result / observation shaping for the ReAct loop. |
| `_react_failure_classification.py` | Classification of failed tool executions for readable failure surfacing. |
| `_react_loop_reexports.py` | Lazy compatibility exports for helpers historically owned by react_loop. |
| `_react_parsing_codequality.py` | Code-quality detectors for ReAct write steps. |
| `_react_parsing_core.py` | Core ReAct text parsing + incremental Thought streaming. |
| `_react_parsing_testquality.py` | Test-correctness + production-hygiene detectors for ReAct write steps. |
| `_react_parsing_tools.py` | Tool-call / XML action parsing helpers for the ReAct trajectory. |
| `_react_parsing_verification.py` | Verification-trail detection helpers for ReAct steps. |
| `_react_prompt_assembly_bootstrap.py` | PHASE 1-2 turn bootstrap: entry guards + router / native-gate resolution, plus PHASE 4/4.5 start events + agent auto-delegation short-circuit. |
| `_react_prompt_assembly_guidance.py` | System-prompt guidance + tool / capability / skill-catalog sections for the PHASE 3 assembly. |
| `_react_prompt_assembly_sections.py` | Early PHASE 3 sections: date / public-orientation / work-mode / read-only / grounding / browser-operation / iteration & budget / todo-protocol resolution. |
| `_react_prompt_assembly_state.py` | Shared mutable assembly state for the PHASE 3 prompt-assembly split, plus the final ``messages`` composition and the memory / identity / team-roster sections. |
| `_visibility_trace.py` | 决策点 → 依据 → 结论 的可见性 trace（visibility trace）记录模块。 |
| `agent_auto_delegate.py` | Auto-delegate to pinned agents on the first ReAct step. |
| `agent_auto_parallel.py` | Auto-decompose + run subtasks in parallel for the first ReAct turn. |
| `ai_mode.py` | AI Mode — Marvis-style two-mode wrapper over the 3-tier router. |
| `capability_router.py` | — |
| `checkpoint_integrity.py` | — |
| `checkpoint_mirror.py` | Distributed checkpoint mirror — P3 fourth slice. |
| `completion_decision.py` | — |
| `completion_receipt.py` | — |
| `env_health.py` | Startup execution-health canary. |
| `guard_model_policy.py` | Model-aware guard routing — apply code-smell guards only to cheap models. |
| `input_mentions.py` | Parse @plugin/@skill/@agent and runtime surface mentions from prompts. |
| `leader.py` | Leader Process · single-owner supervisor for long-running tasks. |
| `live_steering.py` | Shared prompt contract for user messages received during an active turn. |
| `llm_planner.py` | — |
| `output_styles.py` | Per-turn output style overlays for the ReAct system prompt. |
| `pause_control.py` | — |
| `planner.py` | — |
| `plugin_auto_load.py` | Auto-activate pinned plugins/skill-packs from user mentions. |
| `prompt_persistence.py` | — |
| `react_action_outcomes.py` | Action outcome bookkeeping for the ReAct loop. |
| `react_auto_verify.py` | Runtime-side auto-verification salvage for final-answer guard deadlocks. |
| `react_browser_guards.py` | Browser-interaction and mixed-mode completion guards. |
| `react_browser_iteration.py` | Browser-surface gating and per-task iteration limits for the ReAct loop. |
| `react_checkpointing.py` | Periodic auto-checkpoint + distributed mirror for the ReAct loop. |
| `react_code_mode_guards.py` | Code-mode completion, write, inspection and tool-availability guards. |
| `react_code_smell_guards.py` | Code-smell guards (post-step / pre-Final-Answer gates). |
| `react_concurrency_guards.py` | Concurrency / path-boundary semantic guards (single-flight family). |
| `react_context.py` | ReAct context assembly: token budget, compression, prompt building. |
| `react_convergence.py` | Deterministic evidence-to-answer convergence for bounded ReAct turns. |
| `react_execution.py` | Execution / tool-dispatch helpers for the ReAct loop. |
| `react_execution_receipts.py` | Server-owned provenance for ReAct execution receipts. |
| `react_explicit_reads.py` | Explicit-read goal predicates and bounded read recovery. |
| `react_final_answer_content_guards.py` | Final-answer content guards (post-step / pre-Final-Answer gates). |
| `react_final_answer_guards.py` | Final-answer guard plumbing for the ReAct loop. |
| `react_goal_analysis.py` | Goal-intent and evidence-path analysis for ReAct guards. |
| `react_guard_types.py` | Core types for the ReAct final-answer guard registry. |
| `react_guards.py` | ReAct trajectory guards: post-step / pre-Final-Answer quality gates. |
| `react_in_flight_nudges.py` | In-flight nudges for the ReAct main loop (PHASE 6e, first half). |
| `react_loop.py` | — |
| `react_loop_controls.py` | Operator controls + run-budget knobs for the ReAct loop. |
| `react_loop_state.py` | Shared per-turn state for the ReAct main-loop phases (Wave 2). |
| `react_model_deadlines.py` | Model-call deadline machinery for the ReAct loop. |
| `react_model_stream.py` | PHASE 6b — LLM call + Final-Answer anchor streaming for the ReAct loop. |
| `react_native.py` | Native tool-use path for the single-agent ReAct loop. |
| `react_parallel_dispatch.py` | Concurrent multi-action dispatcher for the ReAct loop (口子 2). |
| `react_parsing.py` | ReAct trajectory parsing + post-step quality checks. |
| `react_phase_6c.py` | PHASE 6c of the ReAct main loop: parse step / format-violation check. |
| `react_prompt_assembly.py` | PHASE 3 — system + volatile prompt assembly for the ReAct loop. |
| `react_public_updates.py` | Public progress-update plumbing for the ReAct loop. |
| `react_quiet_evidence.py` | Quiet-evidence accumulation for the ReAct loop's public narrative. |
| `react_repeat_tool_guards.py` | Repeat tool call detection guards. |
| `react_resume.py` | Resume/checkpoint-rebuild helpers for the ReAct loop. |
| `react_security_detectors.py` | Security + quality detectors for ReAct trajectory steps. |
| `react_security_guards.py` | Security + quality guards (post-step / pre-Final-Answer gates). |
| `react_step_evaluator.py` | Deterministic, bounded repair hints for production ReAct turns. |
| `react_terminal.py` | Post-loop terminal handling + finalization for the ReAct loop. |
| `react_test_quality_guards.py` | Test-quality guards: cheats that satisfy coverage letter, not spirit. |
| `react_timeout_guards.py` | Tool timeout detection and policy guards. |
| `react_todo_protocol_guards.py` | Todo-protocol and completion-phrase guards. |
| `react_types.py` | — |
| `react_verification_guards.py` | Verification-completeness guards for ReAct code-mode turns. |
| `reply_styles.py` | Reply-style registry: user-facing response decoration is a selectable dimension, mirroring WorkBuddy's ``style/`` template set (professional / friendly / socratic / ...) and Codex's four-part personality templates (personality / values / tone / escalation). |
| `resume_cli.py` | CLI for inspecting + driving ReAct checkpoint resume (P3 long-task durability). |
| `rewind.py` | Turn-scoped rewind · roll a task back to a prior checkpoint anchor. |
| `rules_persistence.py` | — |
| `run_state.py` | — |
| `stable_prompt.py` | Cache-stable prompt builder. |
| `thinking_mode.py` | Structured thinking-mode helpers. |
| `todo_protocol.py` | Shared rules for the user-visible task checklist protocol. |
| `token_juicer.py` | Token compression for tool observations before they enter the LLM message stream. |
| `tool_output_sink.py` | Compatibility re-export for the lightweight process output sink. |
| `turn_complexity.py` | Three-tier smart model routing. |
| `verification_policy.py` | — |
| `work_mode.py` | Unified work-mode resolution — one model for "what kind of work is this turn". |

## Who imports this

**59** file(s) reference this package:

- **`runtime/cli_code.py/`** · 1 file(s)
  - `runtime/cli_code.py`
- **`runtime/cli_core.py/`** · 1 file(s)
  - `runtime/cli_core.py`
- **`runtime/cli_guard_health.py/`** · 1 file(s)
  - `runtime/cli_guard_health.py`
- **`runtime/cli_reflect.py/`** · 1 file(s)
  - `runtime/cli_reflect.py`
- **`runtime/cli_run.py/`** · 1 file(s)
  - `runtime/cli_run.py`
- **`runtime/cli_serve.py/`** · 1 file(s)
  - `runtime/cli_serve.py`
- **`runtime/core/`** · 1 file(s)
  - `runtime/core/graph_runtime/runtime.py`
- **`runtime/execution/`** · 8 file(s)
  - `runtime/execution/codex_backend/dynamic_tools.py`
  - `runtime/execution/loops/_controller_attempt.py`
  - `runtime/execution/misc/parallel_runner.py`
  - `runtime/execution/parallel_agents/_orchestrator_models.py`
  - `runtime/execution/parallel_agents/stack_runner.py`
  - _… and 3 more_
- **`runtime/memory/`** · 4 file(s)
  - `runtime/memory/cowork/turn_plan.py`
  - `runtime/memory/diagnostics/_trace_store_recovery.py`
  - `runtime/memory/threads/compaction.py`
  - `runtime/memory/threads/llm_summariser.py`
- **`runtime/platform/`** · 7 file(s)
  - `runtime/platform/config/builder.py`
  - `runtime/platform/lifecycle/demo.py`
  - `runtime/platform/ui/_app_collab.py`
  - `runtime/platform/ui/_app_parallel.py`
  - `runtime/platform/ui/_app_stack.py`
  - _… and 2 more_
- **`runtime/safety/`** · 4 file(s)
  - `runtime/safety/experiments/prompt_optimizer.py`
  - `runtime/safety/recovery/gepa_bridge.py`
  - `runtime/safety/recovery/workflow_applier.py`
  - `runtime/safety/validation/trust_signal.py`
- **`runtime/sensing/`** · 26 file(s)
  - `runtime/sensing/gateway/_agents_endpoints.py`
  - `runtime/sensing/gateway/_agents_endpoints_conversations.py`
  - `runtime/sensing/gateway/_agents_endpoints_tasks.py`
  - `runtime/sensing/gateway/_config_endpoints_system.py`
  - `runtime/sensing/gateway/_observability_journal.py`
  - _… and 21 more_
- **`runtime/tentacle/`** · 2 file(s)
  - `runtime/tentacle/coordinator.py`
  - `runtime/tentacle/mobile/cerebrum_adapter.py`
- **`runtime/tour.py/`** · 1 file(s)
  - `runtime/tour.py`

