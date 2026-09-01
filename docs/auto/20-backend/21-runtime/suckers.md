# Suckers · 技能注册

> Skill 注册表 · 原子层 · 沙箱 · 测试 tier。

**Source**: `runtime/execution/suckers/`

## Package summary

Suckers = skill pool.

## Exports

- `ATOMIC_SKILL_NAMES`
- `DEFAULT_CAPACITY`
- `DEFAULT_REFILL_RATE`
- `Skill`
- `SkillNotFound`
- `SkillRateLimiter`
- `SkillRegistry`
- `SkillSearcher`
- `SkillExpect`
- `SkillTestCase`
- `SkillTester`
- `SkillTestReport`
- `SkillTestResult`
- `SkillTestsFailed`
- `SkillTestTier`
- `TIER_THRESHOLDS`
- `TfIdfSkillSearcher`
- `dump_forged_skill_to_md`
- `is_atomic`
- `load_forged_skills_from_dir`

## Modules

| Module | Summary |
| --- | --- |
| `_browser_skills_handlers.py` | Registrar for browser_skills · extracted from browser_skills.py. |
| `_browser_skills_helpers.py` | Helpers for browser_skills · extracted from browser_skills.py. |
| `_code_intel_handlers.py` | Registrar for code_intelligence_skills · extracted from code_intelligence_skills.py. |
| `_code_intel_helpers.py` | Pure helper functions for code_intelligence_skills · extracted from code_intelligence_skills.py to keep the parent file under 1000 lines. |
| `_delegation_skills_agent.py` | ``_call_agent`` · single isolated subagent delegation. |
| `_delegation_skills_common.py` | Shared leaf helpers for delegation_skills · extracted from delegation_skills.py. |
| `_delegation_skills_graph.py` | ``call_agent_graph`` · declarative DAG fan-out with server-side fan-in. |
| `_delegation_skills_judge.py` | ``_run_verdict_repair`` / ``_run_tournament`` · judge panels. |
| `_delegation_skills_orchestration.py` | ``_run_orchestration`` · deterministic multi-round discovery loop. |
| `_delegation_skills_parallel.py` | ``_call_agent_parallel`` · concurrent fan-out + graceful-degradation envelope. |
| `_delegation_skills_pipeline.py` | ``_run_pipeline`` · ordered per-item stage chains, run concurrently. |
| `_delegation_skills_vote.py` | ``_call_agent_vote`` · the consensus / vote gate. |
| `_ephemeral_events.py` | Event emission helpers for ephemeral sub-agent runs. |
| `_ephemeral_tool_exec.py` | Tool execution helpers for ephemeral sub-agent runs. |
| `_ephemeral_verification.py` | Verification gate for ephemeral sub-agents. |
| `_lsp_candidates.py` | Seed a language server with the files a reference search must cover. |
| `_memory_skills_handlers.py` | Registrar for memory_skills · extracted from memory_skills.py. |
| `_write_skills_background.py` | Background-process machinery for write_skills · extracted from write_skills.py. |
| `_write_skills_common.py` | Shared helpers & constants for write_skills · extracted from write_skills.py. |
| `_write_skills_exec.py` | Shell execution skills for write_skills · extracted from write_skills.py. |
| `_write_skills_file.py` | File write / append / edit primitives for write_skills · extracted from write_skills.py. |
| `_write_skills_git.py` | Git core skills for write_skills · extracted from write_skills.py. |
| `_write_skills_git_network.py` | Git network / branch-switch skills for write_skills · extracted from write_skills.py. |
| `_write_skills_quality.py` | Code quality skills for write_skills · extracted from write_skills.py. |
| `agent_doc_skills.py` | Agent documentation skills loaded from ``skills/public``. |
| `agent_meta_skills.py` | — |
| `ask_user_question.py` | ask_user_question · pause-and-ask skill. |
| `blackboard_skills.py` | blackboard_skills · expose the turn-scoped shared dict as 3 skills. |
| `browser_act_skills.py` | — |
| `browser_backend.py` | Unified browser automation backend — the seam over three tracks. |
| `browser_backends.py` | Real BrowserBackend adapters over the three automation tracks. |
| `browser_backends_mock.py` | Mock browser backend — scripted, deterministic, no runtime needed. |
| `browser_dom_js.py` | Shared in-page JavaScript for browser perception. |
| `browser_launch.py` | Launching chromium when only some of its builds are installed. |
| `browser_session_worker.py` | Persistent, thread-affine browser sessions for agent browser skills. |
| `browser_skills.py` | — |
| `builtins.py` | — |
| `capability_skills.py` | — |
| `code_edit_skills.py` | AST-aware code editing skills · tree-sitter powered. |
| `code_intelligence_skills.py` | — |
| `code_navigation.py` | Cross-file symbol lookup and Python import-graph analysis. |
| `codex_plugin_skills.py` | — |
| `computer_api_skills.py` | Agent-facing computer automation skills. |
| `computer_macos.py` | — |
| `computer_skills.py` | — |
| `computer_uia_skills.py` | — |
| `computer_use_loop.py` | — |
| `computer_use_record.py` | Record a successful computer-use loop as a journal Trajectory. |
| `crawler_skills.py` | — |
| `cron_skills.py` | cron_skills · let the agent self-schedule a future turn from inside a turn. |
| `delegation_budget.py` | Smart per-turn delegation budget. |
| `delegation_result_cache.py` | Spawn-level content-hash result cache · resume a graph without respawning. |
| `delegation_skills.py` | — |
| `desktop_grounding.py` | Semantic grounding for the desktop vision loop. |
| `echo_skills.py` | ECHO Universe Engine 叙事 Ganglion 接入. |
| `enterprise_skills.py` | Echo Enterprise 企业服务 Arm 接入. |
| `ephemeral_agents.py` | Ephemeral sub-agent roles · lightweight personas for one-shot delegation tasks (``researcher`` / ``debugger`` / ``reviewer`` / …). |
| `ephemeral_injection_gate.py` | Prompt-injection taint gate for ephemeral sub-agent tool calls. |
| `ephemeral_limits.py` | Round, truncation, and model-selection policy for ephemeral agents. |
| `ephemeral_runner.py` | LLM-backed runner for ephemeral sub-agent roles. |
| `forged_persistence.py` | — |
| `fs_search_skills.py` | — |
| `history_skill.py` | history_skill · cross-thread conversation history retrieval. |
| `hub/installer.py` | — |
| `image_album_skills.py` | Image album skills (local AI photo library). |
| `image_search_backends.py` | Image-search provider backends for the kimi-compat skill group. |
| `image_semantic_skills.py` | Image semantic-search skills (local image library). |
| `jobs_skills.py` | Model-facing background-job skills (dsh ``tool-jobs`` port). |
| `kg_skill.py` | — |
| `kimi_compat_skills.py` | — |
| `layers.py` | — |
| `loader/md_loader.py` | — |
| `lsp_skills.py` | LSP (Language Server Protocol) integration skills. |
| `market_skills.py` | — |
| `memory_file_ops.py` | Low-level append operations for Markdown-backed agent memory. |
| `memory_skills.py` | memory_skills · per-agent long-term memory / user-profile / diary skills. |
| `notebook_skills.py` | — |
| `plan_mode.py` | — |
| `rate_limit.py` | Per-skill rate limiter — runaway-loop protection for LLM agents. |
| `reach_skills.py` | — |
| `registry.py` | — |
| `role_delegation_guidance.py` | Role-specific delegation guidance for hierarchical orchestration. |
| `search.py` | Semantic skill search — TF-IDF-based skill discovery. |
| `skill_library_skills.py` | skill_library_skills · expose Kimi-style "learned skills" as 3 skills. |
| `storage_skills.py` | File Agent document search via the echo-storage sibling service. |
| `sub_agent.py` | Legacy compatibility shim for subagent dispatch. |
| `testing.py` | — |
| `verdict_repair.py` | Verdict-gated repair loop — the closed-loop orchestration echo lacked. |
| `verify_skills.py` | Project verification · detect project type and run checks. |
| `video_album_skills.py` | Video album skills (local AI video library). |
| `web_skills.py` | — |
| `workflow_skill.py` | Model-facing ``workflow`` skill (dsh ``tool-workflow``). |
| `write_skills.py` | — |

## Who imports this

**88** file(s) reference this package:

- **`runtime/_cli_commands.py/`** · 1 file(s)
  - `runtime/_cli_commands.py`
- **`runtime/adapters/`** · 1 file(s)
  - `runtime/adapters/mcp_client/bridge.py`
- **`runtime/cli_code.py/`** · 1 file(s)
  - `runtime/cli_code.py`
- **`runtime/cli_core.py/`** · 1 file(s)
  - `runtime/cli_core.py`
- **`runtime/cli_reflect.py/`** · 1 file(s)
  - `runtime/cli_reflect.py`
- **`runtime/cli_run.py/`** · 1 file(s)
  - `runtime/cli_run.py`
- **`runtime/core/`** · 8 file(s)
  - `runtime/core/cerebrum/_react_context_helpers.py`
  - `runtime/core/cerebrum/_react_context_project.py`
  - `runtime/core/cerebrum/_react_execution_dispatch.py`
  - `runtime/core/cerebrum/_react_prompt_assembly_guidance.py`
  - `runtime/core/cerebrum/capability_router.py`
  - _… and 3 more_
- **`runtime/execution/`** · 14 file(s)
  - `runtime/execution/all_skills/__init__.py`
  - `runtime/execution/arms/base.py`
  - `runtime/execution/codex_backend/dynamic_tools.py`
  - `runtime/execution/codex_backend/role_context.py`
  - `runtime/execution/loops/verifiers.py`
  - _… and 9 more_
- **`runtime/memory/`** · 2 file(s)
  - `runtime/memory/cowork/runtime.py`
  - `runtime/memory/hemolymph/composer.py`
- **`runtime/platform/`** · 24 file(s)
  - `runtime/platform/config/builder.py`
  - `runtime/platform/lifecycle/demo.py`
  - `runtime/platform/plugins/bundled/clip_studio/__init__.py`
  - `runtime/platform/plugins/bundled/comfyui_bridge/__init__.py`
  - `runtime/platform/plugins/bundled/director_stage/__init__.py`
  - _… and 19 more_
- **`runtime/research/`** · 2 file(s)
  - `runtime/research/pipeline.py`
  - `runtime/research/prefetch.py`
- **`runtime/safety/`** · 7 file(s)
  - `runtime/safety/evolution/_recipes_evidence.py`
  - `runtime/safety/evolution/auto_trigger.py`
  - `runtime/safety/evolution/browser_desktop_quality.py`
  - `runtime/safety/evolution/runtime_deployment.py`
  - `runtime/safety/hooks/tool_edge_hooks.py`
  - _… and 2 more_
- **`runtime/sensing/`** · 24 file(s)
  - `runtime/sensing/gateway/_agent_world_helpers.py`
  - `runtime/sensing/gateway/_computer_appshot_routes.py`
  - `runtime/sensing/gateway/_meta_mentions.py`
  - `runtime/sensing/gateway/_realtime_react_stream_drive.py`
  - `runtime/sensing/gateway/_realtime_react_stream_helpers.py`
  - _… and 19 more_
- **`runtime/tour.py/`** · 1 file(s)
  - `runtime/tour.py`

