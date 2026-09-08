"""Native Echo planning exports, loaded only when requested."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .llm_planner import LLMPlanner as LLMPlanner
    from .planner import PlannerError as PlannerError
    from .planner import StaticPlanner as StaticPlanner

_EXPORT_MODULES = {
    "LLMPlanner": ".llm_planner",
    "PlannerError": ".planner",
    "StaticPlanner": ".planner",
}

__all__ = list(_EXPORT_MODULES)


def __getattr__(name: str) -> Any:
    module = _EXPORT_MODULES.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
