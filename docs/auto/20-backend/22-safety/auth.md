# Safety · Auth

> TrustEngine · allow/quarantine/reject · IMM-I1~I6 不变量守护。

**Source**: `runtime/safety/auth/`

## Exports

- `ANONYMOUS_ACTOR`
- `FileWriteVerdict`
- `GuardrailConfig`
- `GuardrailDecision`
- `Identity`
- `IdentityStore`
- `JWTError`
- `MODEL_FORBIDDEN_ARGS`
- `PathVerdict`
- `ToolCallGuardrailController`
- `ToolCallSignature`
- `TrustEngine`
- `CurrentPrincipal`
- `TenantScope`
- `URLVerdict`
- `check_file_write`
- `check_path`
- `check_url`
- `classify_tool`
- `classify_tool_failure`
- `encode_jwt_hs256`
- `hash_api_key`
- `is_safe_path`
- `require_operator`
- `require_roles`
- `resolve_principal`
- `scope_from_principal`
- `scope_from_request`
- `is_safe_url`
- `safe_httpx_request`
- `is_safe_write`
- `strip_model_controlled_overrides`
- `verify_jwt_hs256`

## Modules

| Module | Summary |
| --- | --- |
| `adaptive_immunity.py` | Adaptive immunity — the immunity protocol's behavioural-anomaly tier. |
| `arg_guard.py` | Strip model-controllable privilege escalation before dispatch. |
| `attack_memory.py` | Antibody memory — the immunity protocol's Memory tier. |
| `file_safety.py` | — |
| `identity.py` | — |
| `path_denylist.py` | User-defined path denylist — Marvis-style "不可读取文件夹". |
| `path_guard.py` | — |
| `principal.py` | Request principal resolution and role gates for shared deployments. |
| `scope.py` | Small, framework-independent tenant scope primitives. |
| `tool_guardrails.py` | — |
| `trust_engine.py` | — |
| `url_guard.py` | — |
| `websocket.py` | Shared browser-safe WebSocket bearer-token transport helpers. |

## Who imports this

**169** file(s) reference this package:

- **`runtime/adapters/`** · 5 file(s)
  - `runtime/adapters/integrations/local_auth/router.py`
  - `runtime/adapters/integrations/oct/router_auth.py`
  - `runtime/adapters/mcp_client/oauth.py`
  - `runtime/adapters/mcp_client/oauth_discovery.py`
  - `runtime/adapters/web_auth.py`
- **`runtime/cli_core.py/`** · 1 file(s)
  - `runtime/cli_core.py`
- **`runtime/cli_run.py/`** · 1 file(s)
  - `runtime/cli_run.py`
- **`runtime/cloud_edge/`** · 1 file(s)
  - `runtime/cloud_edge/router.py`
- **`runtime/core/`** · 3 file(s)
  - `runtime/core/nerves/reflex/actions.py`
  - `runtime/core/nerves/reflex/broadcast.py`
  - `runtime/core/nerves/reflex/tiers.py`
- **`runtime/execution/`** · 25 file(s)
  - `runtime/execution/codex_backend/account.py`
  - `runtime/execution/codex_backend/model_profile.py`
  - `runtime/execution/codex_backend/role_runner.py`
  - `runtime/execution/cron_context.py`
  - `runtime/execution/cron_executor.py`
  - _… and 20 more_
- **`runtime/memory/`** · 14 file(s)
  - `runtime/memory/diagnostics/_trace_store_replay_storage.py`
  - `runtime/memory/diagnostics/_trace_store_storage.py`
  - `runtime/memory/hemolymph/composer.py`
  - `runtime/memory/journal/_journal_base.py`
  - `runtime/memory/journal/journal.py`
  - _… and 9 more_
- **`runtime/platform/`** · 19 file(s)
  - `runtime/platform/capabilities/permission_grants.py`
  - `runtime/platform/capabilities/service.py`
  - `runtime/platform/capabilities/tenant_context.py`
  - `runtime/platform/config/builder.py`
  - `runtime/platform/connectors/credential_store.py`
  - _… and 14 more_
- **`runtime/projectos/`** · 6 file(s)
  - `runtime/projectos/_store_message_actions.py`
  - `runtime/projectos/_store_project_deletion.py`
  - `runtime/projectos/_store_task_claims.py`
  - `runtime/projectos/_store_thread_bindings.py`
  - `runtime/projectos/engine.py`
  - `runtime/projectos/store.py`
- **`runtime/safety/`** · 22 file(s)
  - `runtime/safety/evolution/auto_trigger.py`
  - `runtime/safety/evolution/candidate_registry.py`
  - `runtime/safety/evolution/drift_monitor.py`
  - `runtime/safety/evolution/fitness.py`
  - `runtime/safety/evolution/proposal_ledger.py`
  - _… and 17 more_
- **`runtime/sensing/`** · 68 file(s)
  - `runtime/sensing/gateway/_agent_trace_router_stores.py`
  - `runtime/sensing/gateway/_config_endpoints_codex.py`
  - `runtime/sensing/gateway/_config_endpoints_local_models.py`
  - `runtime/sensing/gateway/_config_endpoints_security.py`
  - `runtime/sensing/gateway/_cowork_group_access.py`
  - _… and 63 more_
- **`runtime/tentacle/`** · 2 file(s)
  - `runtime/tentacle/coordinator.py`
  - `runtime/tentacle/dashboard.py`
- **`runtime/tour.py/`** · 1 file(s)
  - `runtime/tour.py`
- **`runtime/workspace/`** · 1 file(s)
  - `runtime/workspace/store.py`

