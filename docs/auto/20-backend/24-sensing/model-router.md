# Sensing · Model Router

> ModelRouter 抽象 · Anthropic / OpenAI / Gemini / Ollama / Mock / MultiModelRouter (multi-provider fallback)。

**Source**: `runtime/sensing/model_router/`

## Exports

- `AllKeysExhausted`
- `CredentialPool`
- `DispatchRecord`
- `EventType`
- `KeyStats`
- `ModelDispatchRouter`
- `GeminiModelRouter`
- `GeminiRouterError`
- `LLMResponseFormatError`
- `MAX_BREAKPOINTS`
- `MIN_CACHE_CHARS`
- `Message`
- `MockModelRouter`
- `ModelRequest`
- `ModelResponse`
- `ModelRouter`
- `ModelStrength`
- `ModelStreamEvent`
- `MultiModelRouter`
- `OllamaModelRouter`
- `OllamaRouterError`
- `OpenAIModelRouter`
- `OpenAIRouterError`
- `PooledModelRouter`
- `PoolReport`
- `DefaultModelSelector`
- `Provider`
- `ProviderCapabilities`
- `RouteAttempt`
- `budget_breakpoints`
- `clear_capability_cache`
- `estimate_cache_savings`
- `get_cached_capabilities`
- `mark_cache_breakpoint`
- `current_actor`
- `prepare_cached_system`
- `prepare_cached_tools`
- `probe_provider`

## Modules

| Module | Summary |
| --- | --- |
| `_providers_data.py` | Provider profile data and data-layer accessors for OpenAI-compatible gateways. |
| `_response_parsers.py` | Response-parsing helpers for OpenAI-compatible providers. |
| `actor_context.py` | Provider-neutral actor context for model-router calls. |
| `anthropic_router.py` | — |
| `capability_probe.py` | Provider Capability Auto-Detection. |
| `chatgpt_subscription_router.py` | ChatGPT-subscription model transport for the native Echo kernel. |
| `credential_pool.py` | — |
| `custom_model_flags.py` | Operator-declared capability flags from ``custom_models.json``. |
| `dispatch_router.py` | — |
| `gemini_router.py` | — |
| `hf_catalog.py` | Live local-model catalog from the HuggingFace Hub (GGUF), with offline fallback. |
| `hwfit.py` | Local-model cookbook: recommend which model to run on THIS machine. |
| `models.py` | Model router types and the mock implementation. |
| `multi_router.py` | — |
| `oct_router.py` | — |
| `ollama_router.py` | — |
| `openai_compat_providers.py` | Provider profiles for OpenAI-compatible chat-completion gateways. |
| `openai_compat_smoke_matrix.py` | Live-smoke metadata for OpenAI-compatible provider profiles. |
| `openai_compat_stream.py` | Shared OpenAI-compatible SSE stream parser. |
| `openai_responses_router.py` | OpenAI Responses-compatible transport for API-key model providers. |
| `openai_router.py` | — |
| `pooled_router.py` | — |
| `prompt_cache.py` | Anthropic prompt-cache hint helpers. |
| `provider.py` | — |
| `rescue_policy.py` | Compatibility exports for the canonical platform model-rescue policy. |
| `selector.py` | Default model-selection block for the composition layer. |
| `vision_guard.py` | Runtime vision capability guard. |

## Who imports this

**40** file(s) reference this package:

- **`runtime/cli_core.py/`** · 1 file(s)
  - `runtime/cli_core.py`
- **`runtime/cli_reflect.py/`** · 1 file(s)
  - `runtime/cli_reflect.py`
- **`runtime/cli_serve.py/`** · 1 file(s)
  - `runtime/cli_serve.py`
- **`runtime/execution/`** · 2 file(s)
  - `runtime/execution/suckers/computer_use_loop.py`
  - `runtime/execution/suckers/ephemeral_runner.py`
- **`runtime/platform/`** · 8 file(s)
  - `runtime/platform/config/builder.py`
  - `runtime/platform/lifecycle/demo.py`
  - `runtime/platform/llm_infra/llm_caller.py`
  - `runtime/platform/process/composition.py`
  - `runtime/platform/process/session.py`
  - _… and 3 more_
- **`runtime/projectos/`** · 1 file(s)
  - `runtime/projectos/llm_hooks.py`
- **`runtime/research/`** · 2 file(s)
  - `runtime/research/pipeline.py`
  - `runtime/research/query_rewrite.py`
- **`runtime/sensing/`** · 24 file(s)
  - `runtime/sensing/gateway/_config_endpoints_custom_models.py`
  - `runtime/sensing/gateway/_config_endpoints_models.py`
  - `runtime/sensing/gateway/_config_helpers.py`
  - `runtime/sensing/gateway/_evolution_helpers.py`
  - `runtime/sensing/gateway/_openai_gateway_router_helpers.py`
  - _… and 19 more_

