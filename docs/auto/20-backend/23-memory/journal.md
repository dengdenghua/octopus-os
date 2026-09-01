# Memory · Journal

> 全 append-only 日志 · events: trajectory / immune / budget / step · 所有 agent 行为的 ground truth。

**Source**: `runtime/memory/journal/`

## Exports

- `AssistantChunkEvent`
- `BrowserArtifactEvent`
- `BudgetEvent`
- `BudgetBreakerResetEvent`
- `CompletedNode`
- `CurriculumGoalDecisionEvent`
- `FileOpEvent`
- `FileRollbackEvent`
- `HookInvokedEvent`
- `HookResultEvent`
- `ImmuneEvent`
- `InMemoryJournal`
- `Journal`
- `JournalEvent`
- `JournalEventType`
- `JournalTransactionError`
- `JSONLJournal`
- `JournalIndex`
- `McpProposalDecisionEvent`
- `NodeStartedEvent`
- `PreviewRefreshEvent`
- `ProtocolDriftDecisionEvent`
- `ProgressStatus`
- `ReflexHitEvent`
- `ResumeInfo`
- `SkillProposalDecisionEvent`
- `SessionSummary`
- `StepEvent`
- `SubSessionSummaryEvent`
- `SubTextDeltaEvent`
- `SubToolEndEvent`
- `SubToolStartEvent`
- `TaskCheckpointEvent`
- `TaskProgressSnapshot`
- `TaskProgressTracker`
- `TaskStartedEvent`
- `ToolEffectIntentEvent`
- `ToolEffectReconciliationEvent`
- `TrajectoryConflictError`
- `TrajectoryEvent`
- `all_task_progress`
- `current_agent_id`
- `current_conversation_id`
- `current_owner_actor_id`
- `current_tenant_id`
- `journal_context`
- `resume_info`
- `task_progress_snapshot`

## Modules

| Module | Summary |
| --- | --- |
| `_chunk_rows.py` | Lossless storage packing for delta-chunk runs (dsh ``chunk-rows``). |
| `_journal_base.py` | — |
| `_journal_models.py` | — |
| `_journal_parse.py` | — |
| `activity.py` | Best-effort journal mirrors for long-running orchestration activity. |
| `derive.py` | Project model-visible history from the journal (dsh session-log idea). |
| `journal.py` | — |
| `journal_context.py` | — |
| `progress.py` | — |
| `progress_tracker.py` | — |
| `resume.py` | — |
| `sqlite_index.py` | SQLite-backed query index over the JSONL journal. |

## Who imports this

**60** file(s) reference this package:

- **`runtime/_cli_commands.py/`** · 1 file(s)
  - `runtime/_cli_commands.py`
- **`runtime/adapters/`** · 1 file(s)
  - `runtime/adapters/channels/manager.py`
- **`runtime/cli_core.py/`** · 1 file(s)
  - `runtime/cli_core.py`
- **`runtime/cli_reflect.py/`** · 1 file(s)
  - `runtime/cli_reflect.py`
- **`runtime/cli_run.py/`** · 1 file(s)
  - `runtime/cli_run.py`
- **`runtime/cli_serve.py/`** · 1 file(s)
  - `runtime/cli_serve.py`
- **`runtime/core/`** · 3 file(s)
  - `runtime/core/cerebrum/llm_planner.py`
  - `runtime/core/cerebrum/resume_cli.py`
  - `runtime/core/graph_runtime/runtime.py`
- **`runtime/execution/`** · 12 file(s)
  - `runtime/execution/cron_executor.py`
  - `runtime/execution/jobs/subagent_producer.py`
  - `runtime/execution/parallel_agents/helpers.py`
  - `runtime/execution/subagents/sessions.py`
  - `runtime/execution/suckers/_ephemeral_events.py`
  - _… and 7 more_
- **`runtime/memory/`** · 5 file(s)
  - `runtime/memory/goals/projection.py`
  - `runtime/memory/goals/service.py`
  - `runtime/memory/hemolymph/composer.py`
  - `runtime/memory/learning/promotion_applier.py`
  - `runtime/memory/runtime_state/hub.py`
- **`runtime/platform/`** · 3 file(s)
  - `runtime/platform/config/builder.py`
  - `runtime/platform/ui/app.py`
  - `runtime/platform/ui/state.py`
- **`runtime/safety/`** · 9 file(s)
  - `runtime/safety/hooks/external_bridge.py`
  - `runtime/safety/recovery/intel_collector.py`
  - `runtime/safety/recovery/kg_updater.py`
  - `runtime/safety/recovery/memory_consolidator.py`
  - `runtime/safety/recovery/recipe_evaluator.py`
  - _… and 4 more_
- **`runtime/sensing/`** · 21 file(s)
  - `runtime/sensing/gateway/_observability_auth.py`
  - `runtime/sensing/gateway/_observability_journal.py`
  - `runtime/sensing/gateway/_observability_progress_stream.py`
  - `runtime/sensing/gateway/_observability_rollback_panels.py`
  - `runtime/sensing/gateway/_realtime_react_stream_drive.py`
  - _… and 16 more_
- **`runtime/tour.py/`** · 1 file(s)
  - `runtime/tour.py`

