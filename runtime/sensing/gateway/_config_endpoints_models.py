"""Model listing & compatibility endpoints for the config router.

Pure structural split of ``_config_endpoints.py`` — no logic changes.
``_register_models`` attaches the provider-catalog, custom-model listing,
compat-diagnostics, compat-profile and merged ``/api/llm-models`` endpoints
to the injected router, reading ``custom_models_state`` through the injected
``_ConfigCtx``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from runtime.platform.models.custom_model_selection import custom_model_selection_id
from runtime.sensing.gateway._config_helpers import (
    _builtin_openai_compat_catalog,
    _compat_diagnostic_for_entry,
    _custom_model_wire_entry,
    _entry_1m_enabled,
    _entry_context_window,
    _entry_model_id,
    _entry_supported_efforts,
)
from runtime.sensing.gateway._config_models import (
    CustomModelsList,
    ProvidersResponse,
)

if TYPE_CHECKING:
    from ._config_endpoints import _ConfigCtx


def _register_models(router: Any, ctx: _ConfigCtx) -> None:
    custom_models_state = ctx.custom_models

    @router.get("/api/providers", response_model=ProvidersResponse)
    def api_list_providers() -> dict[str, Any]:
        """List available LLM providers with their declared capabilities.

        Pulled from the ``Provider`` mixin on each router subclass.
        Used by the frontend ModelPicker to badge models with capability
        hints and to gate UI features (hide "upload image" for
        vision-less models).
        """
        providers: list[dict[str, Any]] = []
        _specs: list[tuple[str, str, str]] = [
            ("anthropic", "runtime.sensing.model_router.anthropic_router", "AnthropicModelRouter"),
            ("openai", "runtime.sensing.model_router.openai_router", "OpenAIModelRouter"),
            ("gemini", "runtime.sensing.model_router.gemini_router", "GeminiModelRouter"),
        ]
        for name, modpath, classname in _specs:
            try:
                mod = __import__(modpath, fromlist=[classname])
                cls = getattr(mod, classname, None)
                if cls is None:
                    continue
                caps = getattr(cls, "capabilities", None)
                if caps is None:
                    continue
                providers.append(
                    {
                        "name": getattr(cls, "provider_name", name),
                        "supports_vision": caps.supports_vision,
                        "supports_tool_use": caps.supports_tool_use,
                        "supports_streaming": caps.supports_streaming,
                        "supports_prompt_cache": caps.supports_prompt_cache,
                        "supports_structured_output": caps.supports_structured_output,
                        "default_model": caps.default_model,
                        "pricing_hint": caps.pricing_hint,
                    }
                )
            except (OSError, ValueError, AttributeError):  # noqa: BLE001
                continue
        return {"providers": providers}

    @router.get(
        "/api/config/custom-models",
        response_model=CustomModelsList,
    )
    @ctx.serialize_custom_models
    def api_list_custom_models() -> dict[str, Any]:
        """List user-added models. ``api_key`` is NEVER echoed back —
        presence reported as ``has_api_key`` boolean only."""
        return {
            "models": [_custom_model_wire_entry(entry) for entry in custom_models_state.values()],
        }

    @router.get("/api/config/custom-models/compat-diagnostics")
    @ctx.serialize_custom_models
    def api_custom_model_compat_diagnostics(
        model_id: str | None = None,
    ) -> dict[str, Any]:
        """Dry-run OpenAI-compatible request shaping for custom models.

        This endpoint does **not** call an upstream model and never
        returns API keys. It shows the operator which compat profile
        each custom model resolves to, which request fields are
        removed/changed before dispatch, and which fallback retries
        would be attempted for a representative strict-provider 400.
        """
        entries = [
            entry
            for entry in custom_models_state.values()
            if model_id is None or _entry_model_id(entry) == model_id
        ]
        return {
            "schema": "echo.openai_compat_diagnostics.v1",
            "total": len(entries),
            "diagnostics": [_compat_diagnostic_for_entry(entry) for entry in entries],
        }

    @router.get("/api/config/openai-compat-profiles")
    def api_openai_compat_profiles() -> dict[str, Any]:
        """List built-in OpenAI-compatible provider profiles.

        This is a dry run over representative provider/model pairs: no
        upstream request is made and no API key is required. It gives
        operators a stable compatibility matrix before any custom model
        is configured.
        """
        from runtime.sensing.model_router.openai_compat_smoke_matrix import (
            openai_compat_smoke_readiness,
        )

        diagnostics = _builtin_openai_compat_catalog()
        return {
            "schema": "echo.openai_compat_profile_catalog.v1",
            "total": len(diagnostics),
            "diagnostics": diagnostics,
            "live_smoke": openai_compat_smoke_readiness(),
        }

    # ─── Merged listing · Echo presets + custom models ─────
    # Frontend ModelPicker consumes this · it's declared on the
    # config router so it runs BEFORE the openai_gateway's own
    # /api/llm-models (FastAPI picks first-match). Living here
    # keeps the merge logic close to the ``custom_models_state``
    # dict it reads from.

    @router.get("/api/llm-models")
    @ctx.serialize_custom_models
    def api_llm_models() -> dict[str, Any]:
        custom: list[dict[str, Any]] = []
        for e in custom_models_state.values():
            entry_id = e["id"]
            entry_label = e.get("display_name") or e.get("name") or entry_id
            provider = e.get("provider") or "openai"
            supports_thinking = bool(e.get("supports_thinking"))
            supports_vision = bool(e.get("supports_vision"))
            supports_tool_use = bool(e.get("supports_tool_use", True))
            omit_sampling_parameters = e.get("omit_sampling_parameters")

            # Expand the entry's ``models`` list into one picker row per
            # variant so the UI can show concrete model ids
            # (e.g. ``mimo-v2.5-pro``, ``mimo-v2.5-flash``) instead of
            # the entry alias ``mimo2.5``. Falls back to legacy single-
            # ``model`` field, then to the entry id itself when neither
            # is present.
            raw_models = e.get("models")
            variants: list[str] = []
            if isinstance(raw_models, list):
                variants = [str(m).strip() for m in raw_models if str(m or "").strip()]
            if not variants:
                legacy = e.get("model")
                if isinstance(legacy, str) and legacy.strip():
                    variants = [legacy.strip()]
            if not variants:
                # Worst case: surface the entry id itself so the row
                # at least appears (the user can edit the entry to
                # add a real model id).
                variants = [entry_id]
            context_window = _entry_context_window(e, variants)
            enable_1m_context = _entry_1m_enabled(e, variants)

            for variant in variants:
                # Show the concrete variant name only — no entry-id
                # prefix, since the variant id is already unique. The
                # entry id is still surfaced via ``entry_id`` for
                # downstream callers that need to know which custom-
                # model entry the variant came from (e.g. credentials
                # / base_url lookup).
                display = entry_label if len(variants) == 1 else (variant or entry_label)
                row = {
                    "id": variant,
                    "name": variant,
                    "model": variant,
                    "display_name": display,
                    "source_display_name": entry_label,
                    "provider": provider,
                    "supports_thinking": supports_thinking,
                    "supports_vision": supports_vision,
                    "supports_tool_use": supports_tool_use,
                    "context_window": context_window,
                    "context_profile": "default",
                    "omit_sampling_parameters": (
                        bool(omit_sampling_parameters)
                        if omit_sampling_parameters is not None
                        else None
                    ),
                    "compat_profile": e.get("compat_profile"),
                    "thinking_request_style": e.get("thinking_request_style"),
                    "reasoning_efforts": _entry_supported_efforts(e, variant),
                    "drop_tool_choice": e.get("drop_tool_choice"),
                    "strict_tool_schema": e.get("strict_tool_schema"),
                    "max_temperature": e.get("max_temperature"),
                    "custom": True,
                    "entry_id": entry_id,
                    "selection_id": custom_model_selection_id(
                        entry_id,
                        variant,
                        "default",
                    ),
                }
                custom.append(row)
                if enable_1m_context:
                    custom.append(
                        {
                            **row,
                            "id": f"{variant}::1m",
                            "name": f"{variant}::1m",
                            "context_window": 1_000_000,
                            "context_profile": "1m",
                            "selection_id": custom_model_selection_id(
                                entry_id,
                                variant,
                                "1m",
                            ),
                        }
                    )
        # Echo Mix — built-in mixture-of-agents virtual model. Selecting
        # it routes /v1/chat/completions through proposers + aggregator
        # (see openai_gateway/mix.py). Listed first as the echo-native
        # flagship; degrades to a single model if no proposer pool is set.
        from runtime.sensing.gateway.openai_gateway.mix import MIX_MODEL_ID

        mix_presets = [
            {
                "id": MIX_MODEL_ID,
                "name": MIX_MODEL_ID,
                "display_name": "mix",
                "provider": "echo",
                "supports_thinking": True,
                "supports_vision": False,
                "supports_tool_use": True,
                "reasoning_efforts": None,
            },
        ]
        return {"models": mix_presets + custom}
