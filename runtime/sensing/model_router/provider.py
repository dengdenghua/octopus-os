from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProviderCapabilities:
    """Declarative flags describing what an LLM adapter can do.

    Fields
    ------
    supports_vision :
        True if the provider accepts image inputs (multi-modal).
    supports_tool_use :
        True if the provider's native API exposes a tool-calling /
        function-calling protocol. Our runtime currently plans + executes
        tools itself (outside the provider), so this is informational —
        downstream could route tool-heavy tasks to tool-native providers.
    supports_streaming :
        True if the adapter can yield tokens as they arrive (SSE / delta
        stream). Not all providers / mirrors support this reliably.
    supports_prompt_cache :
        True if the provider honors a prompt-cache hint (Anthropic's
        ``cache_control``, OpenAI's ``prompt_cache_key``). Knowing this
        lets the composer pre-compute stable system-prompt segments.
    supports_structured_output :
        True if the provider can be asked to return JSON conforming to a
        schema (Anthropic tool-use → forced JSON, OpenAI ``response_format``).
    default_model :
        The model id used when ``request.model`` is empty/unknown.
    pricing_hint :
        Free-form labels like ``"low"`` / ``"mid"`` / ``"high"`` · used
        by the model picker UI for cost indicators.
    """

    supports_vision: bool = False
    supports_tool_use: bool = False
    supports_streaming: bool = False
    supports_prompt_cache: bool = False
    supports_structured_output: bool = False
    default_model: str = ""
    pricing_hint: str = ""
    extra: dict = field(default_factory=dict)


class Provider:
    """Mixin: opt-in capability declaration for ``ModelRouter`` subclasses.

    Subclasses should set ``provider_name`` and ``capabilities``::

        class MyAdapter(Provider, ModelRouter):
            provider_name = "foo"
            capabilities = ProviderCapabilities(supports_vision=True, ...)

    The mixin is intentionally empty aside from those two class attrs —
    the actual call protocol stays on ``ModelRouter`` (``call(request)``).
    This keeps backward compatibility with existing routers that don't
    inherit from ``Provider`` yet; they just lack capabilities metadata
    and the dispatcher treats them as no-capability-declared.
    """

    provider_name: str = ""
    capabilities: ProviderCapabilities = ProviderCapabilities()


__all__ = ["Provider", "ProviderCapabilities"]
