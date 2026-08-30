# Safety · Validation

> 宪法层 · PRIV/LAWF/DGNT/SELF/EXFIL 五类 · rule gate + LLM judge + profile 降级。

**Source**: `runtime/safety/validation/`

## Exports

- `CONSTITUTION_SUMMARY`
- `ConstitutionViolationEvent`
- `Judge`
- `JudgeVerdict`
- `ProfileName`
- `Verdict`
- `build_judge_from_llm_fn`
- `build_judge_from_router`
- `check_outbound`
- `get_constitution_summary`
- `get_judge`
- `get_profile`
- `null_judge`
- `reset_profile_for_tests`
- `scan_pii`
- `scrub_pii`
- `set_judge`
- `set_profile`

## Modules

| Module | Summary |
| --- | --- |
| `bootstrap.py` | Bootstrap wiring for the constitution's LLM-judge tier. |
| `events.py` | Journal event types for constitution violations. |
| `gate.py` | The constitution gate · the single entry point channel adapters and outbound-path code call before sending anything externally. |
| `judge.py` | LLM-judge layer · semantic gate for cases regex can't catch. |
| `llm_judge.py` | Production LLM judge wiring · bridges ``constitution.judge`` to the runtime's ``ModelRouter`` abstraction. |
| `profiles.py` | Constitution profiles · strict / normal / lax. |
| `prompt_injection.py` | Indirect prompt-injection defense for untrusted tool output. |
| `rules.py` | Rule-layer checks · regex-based PII + keyword-based hazard detection. |
| `soul.py` | Constitution internalization · compress the policy into a compact prompt section for injection into agent system prompts. |
| `trust_signal.py` | Trust signal — bridges P1 guard telemetry into P0 constitution decisions. |

## Who imports this

**19** file(s) reference this package:

- **`runtime/adapters/`** · 1 file(s)
  - `runtime/adapters/channels/base.py`
- **`runtime/cli_serve.py/`** · 1 file(s)
  - `runtime/cli_serve.py`
- **`runtime/core/`** · 4 file(s)
  - `runtime/core/cerebrum/_react_execution_phase6d.py`
  - `runtime/core/cerebrum/_react_prompt_assembly_state.py`
  - `runtime/core/cerebrum/react_parallel_dispatch.py`
  - `runtime/core/cerebrum/react_resume.py`
- **`runtime/execution/`** · 7 file(s)
  - `runtime/execution/agents/loader.py`
  - `runtime/execution/codex_backend/dynamic_tools.py`
  - `runtime/execution/misc/parallel_runner.py`
  - `runtime/execution/parallel_agents/orchestrator.py`
  - `runtime/execution/subagents/bridge.py`
  - _… and 2 more_
- **`runtime/memory/`** · 1 file(s)
  - `runtime/memory/threads/llm_summariser.py`
- **`runtime/safety/`** · 4 file(s)
  - `runtime/safety/approval/approval_gate.py`
  - `runtime/safety/evolution/weekly_report.py`
  - `runtime/safety/experiments/prompt_evolver.py`
  - `runtime/safety/governance/execution_policy.py`
- **`runtime/sensing/`** · 1 file(s)
  - `runtime/sensing/gateway/_config_endpoints_security.py`

