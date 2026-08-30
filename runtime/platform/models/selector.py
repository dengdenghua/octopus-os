"""Model selection contract — the "model-router" block of the composition layer.

Design doc: ``docs/architecture/blocks.md`` §2 (``model-router`` block).

Distinction from the execution router
-------------------------------------
``runtime.platform.models.llm.ModelRouter`` is the *execution* interface:
it takes a fully-formed ``ModelRequest`` and performs the upstream call.
This module is the *selection* interface: given the task/role/budget, decide
WHICH model (+ parameters) to run. A swappable ``ModelSelector`` is what lets
operators change "cheap roles use the cheap model" policy, add a local model
tier, or route by budget — without touching cerebrum.

Like ``ModelRouter``, the protocol lives in ``platform.models`` so the
kernel/safety/memory packages can depend on it without importing ``sensing``.
The default implementation ships in ``runtime.sensing.model_router.selector``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class ModelSelection:
    """The decision: which model, with which reasoning profile."""

    model: str
    reasoning_effort: str | None = None


@runtime_checkable
class ModelSelector(Protocol):
    """Narrow, implementation-agnostic model-selection contract."""

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
        """Return the model choice for one run.

        Parameters mirror today's precedence chain exactly:
          explicit ``context["model_name"]`` > ``declared_model`` (agent
          definition) > ``cheap_model`` (when ``use_cheap_model``) >
          ``default_model``. Implementations may change the policy, but the
          inputs stay stable so consumers don't need to know the policy.
        """
        ...
