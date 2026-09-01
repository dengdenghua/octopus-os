"""Pluggable prompt-optimizer backends for Echo evolution.

The runtime owns governance: failure sampling, proposal ledger, canary,
rollback, and replay gates.  Optimizer backends only produce candidates.
This module is the narrow seam that lets the current native GEPA path,
future DSPy GEPA, or external optimizers enter the same governance flow.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class OptimizerRunConfig:
    backend: str = "native_gepa"
    recipe_id: str | None = None
    judge_model: str = "claude-sonnet-4-6"
    mutator_model: str = "claude-sonnet-4-6"
    n_iter: int = 10
    eval_tasks: int = 5
    ledger_path: Any = "data/proposal_ledger.jsonl"
    trigger: str = "manual"
    record_winner: bool = True


@dataclass(frozen=True, slots=True)
class OptimizerRunContext:
    seed_prompt: str
    journal: Any
    router: Any
    config: OptimizerRunConfig


class OptimizerBackend(Protocol):
    name: str
    description: str

    def optimize(self, context: OptimizerRunContext) -> Any:
        """Run the optimizer and return a GEPA-compatible result object."""


class NativeGepaBackend:
    name = "native_gepa"
    description = "Echo native Pareto/reflection GEPA-style optimizer."

    def optimize(self, context: OptimizerRunContext) -> Any:
        from runtime.safety.recovery import gepa_bridge

        cfg = context.config
        result = gepa_bridge.optimize_for_recipe(
            seed_prompt=context.seed_prompt,
            journal=context.journal,
            router=context.router,
            recipe_id=cfg.recipe_id,
            judge_model=cfg.judge_model,
            mutator_model=cfg.mutator_model,
            n_iter=cfg.n_iter,
            eval_tasks=cfg.eval_tasks,
            ledger_path=cfg.ledger_path,
            trigger=cfg.trigger,
            record_winner=cfg.record_winner,
        )
        result.optimizer_backend = self.name
        return result


class DspyGepaBackend:
    name = "dspy_gepa"
    description = "Optional DSPy GEPA backend; requires the dspy package."

    def optimize(self, context: OptimizerRunContext) -> Any:
        try:
            import dspy  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "dspy_gepa backend is unavailable: install DSPy and wire a "
                "DSPy Program adapter before selecting this backend."
            ) from exc
        raise NotImplementedError(
            "dspy_gepa backend is registered but no Echo->DSPy Program adapter is configured yet."
        )


class ExternalGepaBackend:
    name = "external_gepa"
    description = "External optimizer handoff; imports winners through the ledger."

    def optimize(self, context: OptimizerRunContext) -> Any:
        raise NotImplementedError(
            "external_gepa backend is a governance slot only. Import an "
            "external winner into ProposalLedger, then let canary/registry "
            "handle rollout."
        )


_BACKENDS: dict[str, OptimizerBackend] = {
    NativeGepaBackend.name: NativeGepaBackend(),
    DspyGepaBackend.name: DspyGepaBackend(),
    ExternalGepaBackend.name: ExternalGepaBackend(),
}

_ALIASES = {
    "native": "native_gepa",
    "gepa": "native_gepa",
    "echo_gepa": "native_gepa",
    "dspy": "dspy_gepa",
    "dspy-gepa": "dspy_gepa",
    "external": "external_gepa",
}


def available_optimizer_backends() -> list[dict[str, str]]:
    return [
        {"name": name, "description": backend.description}
        for name, backend in sorted(_BACKENDS.items())
    ]


def get_optimizer_backend(name: str | None = None) -> OptimizerBackend:
    key = (name or "native_gepa").strip().lower()
    key = _ALIASES.get(key, key)
    try:
        return _BACKENDS[key]
    except KeyError as exc:
        supported = ", ".join(sorted(_BACKENDS))
        raise ValueError(f"unknown optimizer backend {name!r}; supported: {supported}") from exc


def optimize_with_backend(
    *,
    seed_prompt: str,
    journal: Any,
    router: Any,
    config: OptimizerRunConfig | None = None,
) -> Any:
    cfg = config or OptimizerRunConfig()
    backend = get_optimizer_backend(cfg.backend)
    return backend.optimize(
        OptimizerRunContext(
            seed_prompt=seed_prompt,
            journal=journal,
            router=router,
            config=cfg,
        )
    )


__all__ = [
    "OptimizerBackend",
    "OptimizerRunConfig",
    "OptimizerRunContext",
    "available_optimizer_backends",
    "get_optimizer_backend",
    "optimize_with_backend",
]
