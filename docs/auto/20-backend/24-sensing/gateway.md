# Sensing · Gateway (HTTP API)

> 全部 FastAPI router · openai_gateway / meta / mcp / config / channels / thread_compat / …

**Source**: `runtime/sensing/gateway/`

## Exports

- `StreamingJournal`
- `create_evolution_ops_router`
- `create_openai_router`
- `create_parallel_agents_router`
- `create_thread_state_router`

## Modules

| Module | Summary |
| --- | --- |
| `_agent_trace_router_approvals.py` | Approval, trust-denial, token-usage, checkpoint, and resume endpoint handlers for the agent trace router. |
| `_agent_trace_router_promotion.py` | Promotion and policy-review endpoint handlers for the agent trace router. |
| `_agent_trace_router_review.py` | Review, experience-ledger, and review-queue endpoint handlers for the agent trace router. |
| `_agent_trace_router_stores.py` | Shared store singletons, dependency container, and planning helpers for the agent trace router. |
| `_agent_trace_router_trace.py` | Trace-read endpoint handlers for the agent trace router. |
| `_agent_world_helpers.py` | Helper functions and data for ``agent_world_router``. |
| `_agents_endpoints.py` | Endpoint handlers for the agents router. |
| `_agents_endpoints_conversations.py` | Conversation (journal) endpoints for the agents router. |
| `_agents_endpoints_crud.py` | Agent CRUD + visual + reload endpoints for the agents router. |
| `_agents_endpoints_groups.py` | Agent-group endpoints for the agents router. |
| `_agents_endpoints_local_partners.py` | Local CLI partner discovery and registration endpoints. |
| `_agents_endpoints_shared.py` | Shared types for the ``_agents_endpoints`` submodules. |
| `_agents_endpoints_system.py` | System-level endpoints (regeneration status + capabilities) for the agents router. |
| `_agents_endpoints_tasks.py` | Task (pause/resume) endpoints for the agents router. |
| `_agents_endpoints_tools.py` | Arms + tool-registry endpoints for the agents router. |
| `_agents_helpers.py` | Pure helper functions for the agents router. |
| `_channels_constructors.py` | — |
| `_channels_models.py` | — |
| `_channels_persist.py` | — |
| `_computer_appshot_routes.py` | Appshot and native desktop-target routes for computer automation. |
| `_config_endpoints.py` | Endpoint handlers for the config router. |
| `_config_endpoints_codex.py` | Principal-scoped Coder/Codex account and model control endpoints. |
| `_config_endpoints_custom_models.py` | Custom-model lifecycle endpoints for the config router. |
| `_config_endpoints_local_models.py` | Local-model discovery + one-click import endpoints for the config router. |
| `_config_endpoints_models.py` | Model listing & compatibility endpoints for the config router. |
| `_config_endpoints_security.py` | Security & safety config endpoints for the config router. |
| `_config_endpoints_system.py` | System / runtime-config endpoints for the config router. |
| `_config_helpers.py` | Pure helper functions for the config router. |
| `_config_models.py` | Pydantic response models for the config router. |
| `_cowork_group_access.py` | Authorization helpers for the cowork group HTTP router. |
| `_cowork_group_models.py` | Pydantic request bodies for the cowork group HTTP API. |
| `_cowork_group_room_ensure.py` | Atomic ensure-room workflow for collaboration sessions. |
| `_cowork_group_room_link.py` | Fail-safe Team Room linking for collaboration sessions. |
| `_cowork_group_session.py` | Read-side projection helpers for unified cowork sessions. |
| `_device_flow_models.py` | Typed wire models for connector device-flow generations. |
| `_event_bridge_tool_items.py` | Tool-event → item/description builders for the realtime bridge. |
| `_evolution_helpers.py` | — |
| `_evolution_models.py` | — |
| `_evolution_ops_insights.py` | Read-model builders for the evolution operator console. |
| `_fs_router_diff.py` | Unified-diff parsing / reverse-apply helpers for the filesystem router. |
| `_fs_router_endpoints.py` | Endpoint handlers for the filesystem router. |
| `_fs_router_helpers.py` | Shared helpers for the filesystem router factory. |
| `_fs_router_models.py` | Response models and shared constants for the filesystem router. |
| `_fs_router_paths.py` | Path / root-resolution helpers for the filesystem router. |
| `_meta_mentions.py` | @-mention autocomplete builder for the meta router. |
| `_meta_models.py` | Pydantic response models for the meta router. |
| `_meta_skill_install.py` | Skill install / uninstall filesystem helpers for the meta router. |
| `_meta_skill_metadata.py` | Skill metadata assembly for the meta router. |
| `_observability_auth.py` | Router-level auth helpers shared by the observability endpoint groups. |
| `_observability_helpers.py` | Shared helpers for the observability router factory. |
| `_observability_journal.py` | Journal, reflect, evolution and tool-effect endpoints for the observability router. |
| `_observability_kg.py` | Knowledge-graph endpoints for the observability router. |
| `_observability_progress_stream.py` | Progress and SSE-stream endpoints for the observability router. |
| `_observability_rollback_panels.py` | File-rollback, rewind, blackboard, hemolymph, regeneration, budget and run endpoints. |
| `_observability_router_factory.py` | Factory for the observability router. |
| `_observability_state.py` | Shared state container for the observability router endpoint groups. |
| `_openai_gateway_router_helpers.py` | — |
| `_openai_gateway_router_ratelimit.py` | — |
| `_openai_gateway_router_run.py` | — |
| `_openai_gateway_router_synthesize.py` | — |
| `_projects_group_projections.py` | Project-group read-model projections and compensation helpers. |
| `_realtime_cerebrum_project_os.py` | Explicit Project OS command bridge for the realtime runtime. |
| `_realtime_cerebrum_requests.py` | JSON-RPC method dispatch for the realtime runtime. |
| `_realtime_cerebrum_steering.py` | Active-turn lease + steering management for the realtime runtime. |
| `_realtime_cerebrum_thread.py` | Thread/session + emit helpers for the realtime runtime. |
| `_realtime_claim_aware_emitter.py` | Thread-claim-aware event emitter used by the realtime gateway. |
| `_realtime_detached_turn.py` | Connection-detached emitter for server-resident turns (audit T-01). |
| `_realtime_gateway_approval.py` | Per-connection approval manager and gateway-wide interrupt registry. |
| `_realtime_gateway_connection.py` | Per-WebSocket RPC connection (``RpcConnection``). |
| `_realtime_gateway_frame.py` | Last-ditch frame-size guard so no single WS frame exceeds the ceiling. |
| `_realtime_gateway_session.py` | Realtime WebSocket session boundary for :mod:`realtime_gateway`. |
| `_realtime_gateway_types.py` | Shared types, protocols, exceptions and constants for the realtime gateway. |
| `_realtime_orchestrator_bridge.py` | Bridge a ``ParallelAgentOrchestrator`` batch stream onto a realtime turn. |
| `_realtime_react_stream_apply.py` | Reducer that maps bridge events to ``item/*`` notifications. |
| `_realtime_react_stream_drive.py` | ReAct loop stream driver. |
| `_realtime_react_stream_helpers.py` | Shared helpers & reactive predicates for the realtime stream drivers. |
| `_realtime_react_stream_reflection.py` | Direct-LLM reflection fast-path stream driver. |
| `_realtime_subagent_journal_items.py` | Journal-to-realtime item projections for sub-agent workbench lanes. |
| `_realtime_team_stream_mesh.py` | Mesh swarm stream driver — auto-selecting swarm (mesh vs team) + fallback. |
| `_realtime_thread_delete_probe.py` | Durable permanent-delete fence shared by realtime turn boundaries. |
| `_realtime_turn_idempotency.py` | Durable ``userItemId`` replay helpers for realtime turn startup. |
| `_realtime_turn_lifecycle_helpers.py` | Shared helpers for the realtime turn lifecycle. |
| `_realtime_turn_lifecycle_resume.py` | Resume-intent persistence for the realtime turn lifecycle. |
| `_team_room_binding.py` | Canonical Team Room thread binding helpers. |
| `_team_room_creation.py` | Atomic Team Room creation primitives. |
| `_team_room_delete.py` | Durable, fail-closed Team Room deletion. |
| `_team_room_persistence.py` | Cross-process optimistic persistence for Team Room state. |
| `_team_rooms_access.py` | Room membership and administration checks for the Team Rooms router. |
| `_team_rooms_state.py` | Persistence and wire-serialization helpers for Team Rooms. |
| `_team_stream_group_fanout.py` | Group fan-out stream driver — 蜂群 / 冒泡 cowork dispatch. |
| `_team_stream_topology.py` | Multi-agent team-topology stream driver — topology resolution + bridge. |
| `_team_tasks_access.py` | Authorization helpers for the persistent team-tasks router. |
| `_team_tasks_helpers.py` | Module-level helpers for the persistent team tasks router. |
| `_team_tasks_models.py` | Pydantic wire models for the persistent team tasks API. |
| `_thread_state_auto_title.py` | Auto-title service wiring shared by the thread state router. |
| `_thread_state_delete.py` | Fail-closed thread deletion with a durable Project OS binding fence. |
| `_thread_state_search_projection.py` | Search projection helpers for the thread-state HTTP router. |
| `_tool_bridge_exec.py` | Tool execution + semantic error + XML recovery helpers. |
| `_tool_bridge_loop.py` | The native agentic tool loop (``stream_agentic_fallback``). |
| `_tool_bridge_native.py` | Native model stream + timeout + tool-call fingerprint/dedup helpers. |
| `_tool_bridge_policy.py` | Goal / scope / budget / shell policy helpers for the native tool loop. |
| `_tool_bridge_protocol.py` | Public checkpoint / protocol-tag cleaning + narration helpers. |
| `_tool_bridge_scoring.py` | Per-turn quality scoring + auto-evolution tick helpers. |
| `_tool_bridge_session.py` | Session metadata + browser operation guidance helpers. |
| `a2a_router.py` | A2A (Agent-to-Agent) remote agent registry + relay router. |
| `account_usage_router.py` | — |
| `adaptive_delta_buffer.py` | 自适应流式刷新策略（纯决策，不存内容） |
| `agent_market_sources/financial-services/agent-plugins/model-builder/skills/dcf-model/scripts/validate_dcf.py` | DCF Model Validation Script Validates Excel DCF models for formula errors and common DCF mistakes |
| `agent_market_sources/financial-services/agent-plugins/pitch-agent/skills/dcf-model/scripts/validate_dcf.py` | DCF Model Validation Script Validates Excel DCF models for formula errors and common DCF mistakes |
| `agent_market_sources/financial-services/agent-plugins/pitch-agent/skills/ib-check-deck/scripts/extract_numbers.py` | Extract numerical values from presentation content for consistency checking. |
| `agent_modes_router.py` | Agent project/code mode detection endpoints. |
| `agent_trace_dependencies.py` | State factories and promotion helpers for the agent-trace API. |
| `agent_trace_router.py` | Read-only API for the durable agent trace store. |
| `agent_world_router.py` | Agent Market router · local agent marketplace. |
| `agents_local_partner.py` | LocalPartner subsystem — detection + secure registration. |
| `agents_models.py` | Pydantic wire models for ``agents_router``. |
| `agents_router.py` | Agents router · public factory for the ``/api/agents`` surface. |
| `ambient_suggestions_router.py` | Ambient Suggestions router · ``/api/ambient-suggestions/*``. |
| `android_router.py` | Android device HTTP API — server-side counterpart to Echo Mobile. |
| `anthropic_compat/event_adapter.py` | Map internal ReAct loop events to Anthropic Managed Agents event shapes. |
| `anthropic_compat/models.py` | Pydantic models for the Anthropic Managed Agents compat layer. |
| `anthropic_compat/router.py` | Anthropic Managed Agents REST + SSE router. |
| `anthropic_compat/session_manager.py` | Session lifecycle manager for the Anthropic compat layer. |
| `apps_router.py` | — |
| `asset_registry_router.py` | 统一资产仓库路由 —— 插件 / 技能 / 角色(WorkBuddy + Codex + 本地 + 内置)归一视图。 |
| `capability_router.py` | 统一「插件」市场路由 —— 所有外部能力(WorkBuddy MCP 服务 + Codex 插件)统一叫插件。 |
| `channels_router.py` | — |
| `comfyui_manager.py` | User-triggered managed ComfyUI installation and update jobs. |
| `comfyui_supervisor.py` | User-triggered lifecycle control for an existing local ComfyUI installation. |
| `completion_router.py` | Inline code completion endpoint — Tab-complete skeleton. |
| `computer_actions.py` | Action normalization/execution/preview-contract + UIA goal-planning for the computer-automation router. |
| `computer_control_session.py` | Control-session bookkeeping and activity/replay logging for the computer-automation router. |
| `computer_diagnostics.py` | Diagnostic / capability payload builders for the computer-automation router family. |
| `computer_lease.py` | Exclusive-operator lease management for the computer-automation router. |
| `computer_replay_evidence.py` | Replay-evidence summary for the computer-automation router family. |
| `computer_router.py` | Computer automation API. |
| `computer_router_state.py` | Shared mutable state for the computer-automation router family. |
| `computer_runtime_readiness.py` | Runtime-readiness aggregation for the computer-automation router. |
| `computer_vision.py` | Vision-model config resolution + OpenAI-compatible vision call for the computer-automation router. |
| `config_router.py` | Config router · identity-lock + providers + custom-models. |
| `connector_router.py` | 连接器网关路由 — 浏览/安装/认证编排/启停。 |
| `control_sessions_router.py` | Unified control-session API. |
| `cowork_group_router.py` | Thread-group API: WeChat-style membership + mode + shared blackboard. |
| `cron_router.py` | Cron settings compatibility router. |
| `dag_debugger_router.py` | — |
| `debug_router.py` | Debug diagnostics router · ``/api/debug/session-info``. |
| `deep_research_router.py` | Deep research API router. |
| `deployments_router.py` | — |
| `design_studio_router.py` | Local creative-workbench integrations used by the Design canvas. |
| `enterprise_assets_router.py` | Agent 消费企业版角色资产库(数字分身归并 C · 消费侧,只读)。 |
| `evolution_ops/budget.py` | Budget subsystem for evolution operators. |
| `evolution_ops/curriculum.py` | Curriculum subsystem for evolution operators. |
| `evolution_ops/framework_benchmarks.py` | Framework benchmarks subsystem for evolution operators. |
| `evolution_ops/mcp_ops.py` | MCP subsystem for evolution operators. |
| `evolution_ops/protocol_drift.py` | Protocol drift subsystem for evolution operators. |
| `evolution_ops/recipe_forge.py` | RecipeForge subsystem for evolution operators. |
| `evolution_ops/skill_forge.py` | SkillForge subsystem for evolution operators. |
| `evolution_ops/utils.py` | Shared utility functions for evolution operator subsystems. |
| `evolution_ops_router.py` | Evolution operator console control-plane routes. |
| `evolution_router.py` | — |
| `fs_router.py` | Filesystem router · ``/api/fs/{tree,read,write}``. |
| `index_router.py` | Code index router · ``/api/index/*``. |
| `intelligence_router.py` | — |
| `invariants_router.py` | Invariants router · catalog of the 34-rule constitution and which functions enforce each rule. |
| `journal_router.py` | Journal query router · ``/api/journal/*``. |
| `local_brain.py` | One-glance local-brain readiness for the work-mode setup wizard. |
| `local_brain_router.py` | Local-brain setup router · ``/api/local-brain/*``. |
| `loop_router.py` | — |
| `lsp_router.py` | Thin HTTP wrapper around the registered LSP skills. |
| `mcp_router.py` | MCP router · declare / enable / disable MCP servers at runtime. |
| `media_router.py` | Media (video understanding) web API. |
| `memory_router.py` | Local memory compatibility API. |
| `meta_router.py` | Meta router · feedback / skills / auth-provider listing. |
| `meta_skill_router.py` | FastAPI router for the 能力包 / Meta-Skill catalog. |
| `metrics_router.py` | Metrics router — Prometheus text export of the in-process registry. |
| `observability_router.py` | Observability router · journal / reflect / kg / progress / stream / run. |
| `openai_formatting.py` | Pure-function formatters for the OpenAI-compat gateway. |
| `openai_gateway/context_manager.py` | — |
| `openai_gateway/mix.py` | Echo Mix — a mixture-of-agents virtual model for the OpenAI gateway. |
| `openai_gateway/request_parser.py` | — |
| `openai_gateway/response_formatter.py` | — |
| `openai_gateway/stream_handler.py` | — |
| `openai_gateway/synthesis.py` | Final-answer synthesis for completed non-streaming gateway runs. |
| `openai_gateway/tool_converter.py` | — |
| `openai_gateway/turn_context.py` | Shared turn/session preparation for the OpenAI compatibility gateway. |
| `openai_gateway_router.py` | — |
| `org_router.py` | Organization API router (阶段一 企业协作 · 组织 API 路由). |
| `organizations_router.py` | REST endpoints for team-topology management. |
| `parallel_agents_router.py` | — |
| `plugin_hub_router.py` | PluginHub management REST API. |
| `plugins_router.py` | — |
| `projects_router.py` | Project OS API — drive milestone-driven projects over HTTP. |
| `prompts_router.py` | Prompts router · ``/api/prompts/*``. |
| `realtime_approval.py` | Approval bridge between the blocking react loop and the async gateway. |
| `realtime_cerebrum.py` | Cerebrum-backed realtime runtime. |
| `realtime_codex_backend.py` | Realtime driver for the isolated Codex App Server execution backend. |
| `realtime_echo.py` | Echo runtime — reference :class:`RealtimeRuntime` implementation. |
| `realtime_event_bridge.py` | React-event → ``item/*`` bridge state for the realtime runtime. |
| `realtime_frame_bounds.py` | Last-resort frame bounding for realtime WebSocket notifications. |
| `realtime_gateway.py` | Realtime gateway — JSON-RPC 2.0 over WebSocket. |
| `realtime_interrupt_control.py` | Authoritative cross-worker interrupt control for realtime turns. |
| `realtime_react_policy.py` | Routing policy and event translation for realtime agent streams. |
| `realtime_react_stream.py` | Single-agent stream drivers for the realtime runtime. |
| `realtime_team_stream.py` | Multi-agent team-topology stream driver for the realtime runtime. |
| `realtime_thread_history.py` | Realtime turn ↔ legacy conversation history adapters. |
| `realtime_thread_ops.py` | Thread maintenance operations for the realtime runtime. |
| `realtime_turn_input.py` | Turn-input shaping for the realtime runtime. |
| `realtime_turn_lifecycle.py` | Realtime turn validation, dispatch, resume handling, and finalization. |
| `realtime_turn_outcome.py` | Turn outcome inspection for the realtime runtime. |
| `realtime_turn_routing.py` | Turn-routing helpers for the realtime runtime. |
| `realtime_turn_support.py` | Observable-output, cowork-context, and resume-intent helpers. |
| `realtime_workbench.py` | Workbench snapshot + workspace-focus helpers for the realtime runtime. |
| `recorder_store.py` | Durable, privacy-aware event store for the optional Echo REC plugin. |
| `registry_consumer_router.py` | 资产 Registry 消费路由(母体接 registry · 只读浏览 + 安装 prompt-skill)。 |
| `remote_backends_router.py` | Remote backends router · ``/api/remote-backends/*``. |
| `remote_transport.py` | Remote Transport · connect a desktop session to a remote echo-agent runtime over SSH-tunneled HTTP. |
| `retrieve_router.py` | Retrieval router · ``/api/retrieve/rank``. |
| `skill_market_router.py` | — |
| `slash_command_expansion.py` | Slash-command expansion for realtime chat input. |
| `storage_proxy_router.py` | Same-origin gateway for the private echo-storage service. |
| `storage_supervisor.py` | Optional co-launch of the echo-storage sibling service. |
| `streaming_journal.py` | — |
| `stub_router.py` | — |
| `subagents_router.py` | Subagent FastAPI router. |
| `system_router.py` | System-level local maintenance endpoints. |
| `task_runs_router.py` | — |
| `teach_repeat_router.py` | Teach & Repeat API. |
| `team_invitations_router.py` | Human invitation HTTP surface for Team Rooms. |
| `team_role_models_router.py` | Team role-model settings router · ``/api/team/role-models``. |
| `team_rooms_models.py` | Wire and request models shared by the Team Rooms HTTP and WS surfaces. |
| `team_rooms_router.py` | Persistent team rooms API. |
| `team_rooms_ws.py` | Realtime Team Room WebSocket handler. |
| `team_speaker_policy.py` | Pure team-room governance helpers. |
| `team_tasks_router.py` | Persistent team tasks API. |
| `tentacle_join_router.py` | Tentacle join router · ``/api/tentacle/join-info``. |
| `terminal_router.py` | terminal_router · WebSocket-based persistent shell sessions. |
| `thread_access.py` | Shared authorization for canonical threads linked to Team Rooms. |
| `thread_share_relay.py` | Narrow server-to-server client for the public share relay. |
| `thread_share_store.py` | Durable, privacy-bounded snapshots for public thread sharing. |
| `thread_state_router.py` | Thread state HTTP router used by the realtime UI. |
| `thread_workspace.py` | Server-managed workspace paths for authenticated thread filesystem access. |
| `tool_bridge.py` | tool_bridge · the agentic-loop helper that turns Echo skills into Claude-native ``tool_use`` calls and loops result → next turn. |
| `turn_session.py` | Turn session metadata assembly for realtime execution. |
| `uploads_router.py` | Thread uploads / artifacts router. |
| `verify_router.py` | Verification router · ``/api/verify/*``. |
| `waiting_escalation.py` | Waiting-user escalation watchdog — side-channel notifications when an operator approval blocks longer than a threshold. |
| `wiki_generic.py` | Project-agnostic wiki generator · scans an arbitrary user-selected folder and writes a navigable static documentation tree under ``<root>/.echo-wiki/``. |
| `wiki_router.py` | — |
| `workbench_packages_router.py` | Static delivery contract for installed remote workbench surfaces. |
| `workspace_api_router.py` | Workspace HTTP API · ``/api/workspaces/*``. |
| `workspaces_router.py` | Workspace manifest API. |

## Who imports this

**17** file(s) reference this package:

- **`runtime/_cli_commands.py/`** · 1 file(s)
  - `runtime/_cli_commands.py`
- **`runtime/cli_serve.py/`** · 1 file(s)
  - `runtime/cli_serve.py`
- **`runtime/kernel/`** · 1 file(s)
  - `runtime/kernel/kernel.py`
- **`runtime/platform/`** · 13 file(s)
  - `runtime/platform/plugins/bundled/comfyui_bridge/__init__.py`
  - `runtime/platform/plugins/cloud_expert_store.py`
  - `runtime/platform/ui/_app_agents.py`
  - `runtime/platform/ui/_app_collab.py`
  - `runtime/platform/ui/_app_health.py`
  - _… and 8 more_
- **`runtime/projectos/`** · 1 file(s)
  - `runtime/projectos/group_service.py`

