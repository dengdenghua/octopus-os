# Arms · 执行工具组

> Arm preset 工厂 · 将原始 Skill 按职责打包成 arm（fs_writer / git / shell / browser_read / ...）。

**Source**: `runtime/execution/arms/`

## Exports

- `PRESET_FACTORIES`
- `Arm`
- `ArmPool`
- `ByteStreamBuffer`
- `ExtensionContext`
- `ExtensionInfo`
- `ExtensionRegistry`
- `ExtensionState`
- `GateError`
- `LazyArmPool`
- `LazyPool`
- `LazyPromise`
- `LazyValue`
- `LineBuffer`
- `ProcessTreeManager`
- `PromiseGate`
- `SafeRmConfig`
- `SafeRmProtector`
- `SessionLock`
- `ShellEnvState`
- `ShellExecEvent`
- `ShellExecTelemetry`
- `ShellStateManager`
- `ToolCallContext`
- `ToolCallResult`
- `ToolDefinition`
- `ToolProvider`
- `ToolRegistry`
- `Worker`
- `get_shell_telemetry`
- `get_tool_registry`
- `make_all_presets`
- `make_code_arm`
- `make_coder_arm_v2`
- `make_desktop_operator_arm`
- `make_ecommerce_mind_arm`
- `make_file_arm`
- `make_general_arm`
- `make_search_arm`
- `make_shell_arm`
- `make_vibe_selling_arm`

## Modules

| Module | Summary |
| --- | --- |
| `base.py` | — |
| `enterprise_cache.py` | Enterprise Arm 本地决策层(Ganglion). |
| `extension_registry.py` | Dynamic extension registry — hot-pluggable skill registration. |
| `lazy_loader.py` | Lazy loading patterns — on-demand resource initialization. |
| `output_buffer.py` | Dual-layer output buffer for shell command output. |
| `presets.py` | — |
| `process_tree.py` | Process tree management and graceful shutdown utilities. |
| `promise_gate.py` | Promise gate — async concurrency control via chained promises. |
| `safe_rm.py` | safe_rm — file protection mechanism for shell commands. |
| `shell_state.py` | Shell environment state snapshot model. |
| `shell_state_manager.py` | Shell state snapshot manager. |
| `shell_telemetry.py` | Shell execution telemetry events. |
| `specialized.py` | — |
| `tool_registry.py` | MCP-style tool registry — declarative tool registration pattern. |

## Who imports this

**10** file(s) reference this package:

- **`runtime/cli_core.py/`** · 1 file(s)
  - `runtime/cli_core.py`
- **`runtime/cli_run.py/`** · 1 file(s)
  - `runtime/cli_run.py`
- **`runtime/execution/`** · 5 file(s)
  - `runtime/execution/agents/base.py`
  - `runtime/execution/agents/loader.py`
  - `runtime/execution/swarm/_runtime_helpers.py`
  - `runtime/execution/swarm/drive.py`
  - `runtime/execution/swarm/runtime.py`
- **`runtime/platform/`** · 2 file(s)
  - `runtime/platform/ui/_app_meta.py`
  - `runtime/platform/ui/_app_routers_extra.py`
- **`runtime/sensing/`** · 1 file(s)
  - `runtime/sensing/gateway/terminal_router.py`

