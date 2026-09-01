"""Default model-selection block for the composition layer.

Implements :class:`~runtime.platform.models.selector.ModelSelector` with the
precedence chain the runtime already uses in practice:

    explicit ``context["model_name"]``
        > ``declared_model`` (agent definition's pinned model)
        > ``cheap_model`` (when ``use_cheap_model``)
        > ``default_model`` (planner/stack default)

Encapsulating this chain as a swappable block means operators can change the
cheap-role policy or add a budget tier without editing cerebrum/bridge —
the ServiceBus exposes it as the ``model_router`` service.
"""

from __future__ import annotations

from typing import Any

from runtime.platform.models.selector import (
    ModelSelection,
)
from runtime.platform.models.selector import (
    ModelSelector as _ModelSelectorProtocol,
)

__all__ = ["DefaultModelSelector", "ModelSelection", "ModelSelector"]


class DefaultModelSelector:
    """Reproduce the runtime's established model-selection precedence."""

    name = "default"

    def select(
        self,
        *,
        role: str,
        default_model: str,
        context: dict[str, Any] | None = None,
        declared_model: str | None = None,
        use_cheap_model: bool = False,
        cheap_model: str | None = None,
    ) -> ModelSelection:
        # 1. Explicit per-call override wins (bridge injects it for both
        #    explicit overrides and resolved cheap routing).
        if isinstance(context, dict):
            override = context.get("model_name")
            if isinstance(override, str) and override.strip():
                return ModelSelection(model=override.strip())

        # 2. Agent definition pins a model.
        if isinstance(declared_model, str) and declared_model.strip():
            return ModelSelection(model=declared_model.strip())

        # 3. Cheap routing for cheap-eligible roles.
        if use_cheap_model and isinstance(cheap_model, str) and cheap_model.strip():
            return ModelSelection(model=cheap_model.strip())

        # 4. Planner/stack default.
        return ModelSelection(
            model=default_model.strip() if isinstance(default_model, str) else default_model
        )


# Re-export the protocol under the name the composition layer documents.
ModelSelector = _ModelSelectorProtocol
