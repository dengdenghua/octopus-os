# Tool Engine · 执行器

> 把每步 tool call 串起整套治理 · Auth / Budget / Journal / Hooks · 同时做 OTel span。

**Source**: `runtime/execution/tool_engine/`

## Exports

- `NormalizedToolCall`
- `NormalizedToolLifecycleEvent`
- `NormalizedToolResult`
- `StepExecutionError`
- `ToolCallOrigin`
- `ToolKind`
- `ToolLifecycleKind`
- `ToolTaxonomy`
- `ToolExecutor`
- `classify_skill`
- `normalize_tool_lifecycle_event`
- `normalize_step_tool_result`
- `normalize_tool_result`
- `normalize_task_node_tool_call`
- `normalize_tool_call`
- `output_signals_error`
- `register_taxonomy`
- `render_tool_output`
- `reset_overrides`
- `taxonomy_to_audit_dict`
- `tool_lifecycle_event_to_react_event`
- `tool_lifecycle_event_to_trace_payload`

## Modules

| Module | Summary |
| --- | --- |
| `_executor_fileops.py` | — |
| `_executor_helpers.py` | — |
| `effect_receipts.py` | Crash-safe tool effect receipts for durable agent turns. |
| `effect_store.py` | Transactional cross-process coordination for tool side effects. |
| `executor.py` | — |
| `native_tool_execution.py` | Execute a model-native tool call through the Echo executor boundary. |
| `redis_effect_store.py` | Redis-backed, cross-host tool-effect receipts. |
| `session_metadata.py` | Project caller context into the metadata trusted by tool sessions. |
| `session_projection.py` | Byte-bounded projection of a session's conversation surface. |
| `session_reference.py` | Echo Native cross-session reference resolver. |
| `session_reference_uri.py` | Canonical Echo session URI and inline mention encoding. |
| `skill_gate.py` | Shared pre-execution safety gate for direct skill dispatch. |
| `tool_output_pruner.py` | Deterministic head/middle/tail pruning for over-budget tool results. |
| `tool_output_spill.py` | Session-scoped spill storage for oversized plain-text tool results. |
| `tool_protocol.py` | — |
| `tool_shadow_price.py` | Shadow-price accounting for tool-result pruning. |
| `tool_taxonomy.py` | Unified tool identity layer · stable taxonomy for audit & grouping. |

## Who imports this

**25** file(s) reference this package:

- **`runtime/cli_core.py/`** · 1 file(s)
  - `runtime/cli_core.py`
- **`runtime/cli_run.py/`** · 1 file(s)
  - `runtime/cli_run.py`
- **`runtime/core/`** · 7 file(s)
  - `runtime/core/cerebrum/_react_execution_dispatch.py`
  - `runtime/core/cerebrum/_react_execution_phase6d.py`
  - `runtime/core/cerebrum/_react_execution_results.py`
  - `runtime/core/cerebrum/react_action_outcomes.py`
  - `runtime/core/cerebrum/react_execution_receipts.py`
  - _… and 2 more_
- **`runtime/execution/`** · 6 file(s)
  - `runtime/execution/codex_backend/dynamic_tools.py`
  - `runtime/execution/subagents/sessions.py`
  - `runtime/execution/suckers/_ephemeral_tool_exec.py`
  - `runtime/execution/suckers/agent_meta_skills.py`
  - `runtime/execution/suckers/capability_skills.py`
  - `runtime/execution/suckers/forged_persistence.py`
- **`runtime/platform/`** · 1 file(s)
  - `runtime/platform/config/builder.py`
- **`runtime/safety/`** · 1 file(s)
  - `runtime/safety/recovery/skill_forge.py`
- **`runtime/sensing/`** · 8 file(s)
  - `runtime/sensing/gateway/_observability_rollback_panels.py`
  - `runtime/sensing/gateway/_realtime_react_stream_helpers.py`
  - `runtime/sensing/gateway/_tool_bridge_exec.py`
  - `runtime/sensing/gateway/_tool_bridge_policy.py`
  - `runtime/sensing/gateway/_tool_bridge_session.py`
  - _… and 3 more_

