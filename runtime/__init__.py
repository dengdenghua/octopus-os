"""Echo Agent · biomimetic self-evolving agent OS.

Engineering-name package. The package layout uses engineering names for
most subsystems, with a small set of brand terms (cerebrum, hearts, nerves,
arms, swarm, suckers, tentacle) preserved as project vocabulary.

Subpackages are physically organized under 7 semantic groups:
    core/      cerebrum graph_runtime hearts nerves (incl. nerves/reflex)
    execution/ agents arms tool_engine suckers parallel_agents swarm
    sensing/   model_router gateway server normalize
    memory/    journal hemolymph knowledge_graph threads
    safety/    auth budget_breaker validation recovery experiments
               chromatophores invariants
    adapters/  channels integrations mcp_client scheduler instrumentation
    platform/  config models ui i18n

Prefer the explicit group path `from runtime.<group>.X` in new code.
"""

__version__ = "0.2.0"

import os
from typing import Any


def _install_legacy_environment_aliases() -> None:
    """Promote pre-Echo environment settings before runtime modules load."""

    legacy_prefix = "OCTO" + "PUS_"
    for name, value in tuple(os.environ.items()):
        if name.startswith(legacy_prefix):
            os.environ.setdefault(f"ECHO_{name.removeprefix(legacy_prefix)}", value)


_install_legacy_environment_aliases()

_LAZY_EXPORTS = {
    # core
    "cerebrum": "runtime.core.cerebrum",
    "graph_runtime": "runtime.core.graph_runtime",
    "hearts": "runtime.core.hearts",
    "nerves": "runtime.core.nerves",
    "reflex": "runtime.core.nerves.reflex",
    # execution
    "agents": "runtime.execution.agents",
    "arms": "runtime.execution.arms",
    "tool_engine": "runtime.execution.tool_engine",
    "suckers": "runtime.execution.suckers",
    "parallel_agents": "runtime.execution.parallel_agents",
    "swarm": "runtime.execution.swarm",
    # sensing
    "model_router": "runtime.sensing.model_router",
    "normalize": "runtime.sensing.normalize",
    "gateway": "runtime.sensing.gateway",
    "server": "runtime.sensing.server",
    # memory
    "journal": "runtime.memory.journal",
    "hemolymph": "runtime.memory.hemolymph",
    "knowledge_graph": "runtime.memory.knowledge_graph",
    "threads": "runtime.memory.threads",
    # safety
    "auth": "runtime.safety.auth",
    "budget_breaker": "runtime.safety.budget_breaker",
    "invariants": "runtime.safety.invariants",
    "recovery": "runtime.safety.recovery",
    "experiments": "runtime.safety.experiments",
    "chromatophores": "runtime.safety.chromatophores",
    # adapters
    "channels": "runtime.adapters.channels",
    "integrations": "runtime.adapters.integrations",
    "mcp_client": "runtime.adapters.mcp_client",
    "scheduler": "runtime.adapters.scheduler",
    "instrumentation": "runtime.adapters.instrumentation",
    # platform
    "config": "runtime.platform.config",
    "models": "runtime.platform.models",
    "ui": "runtime.platform.ui",
    "i18n": "runtime.platform.i18n",
    # embedding boundary
    "kernel": "runtime.kernel",
}


def __getattr__(name: str) -> Any:
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    value = import_module(target)
    globals()[name] = value
    return value


__all__ = [
    "__version__",
    # core
    "cerebrum",
    "graph_runtime",
    "hearts",
    "nerves",
    "reflex",
    # execution
    "agents",
    "arms",
    "tool_engine",
    "suckers",
    "parallel_agents",
    "swarm",
    # sensing
    "model_router",
    "normalize",
    "gateway",
    "server",
    # memory
    "journal",
    "hemolymph",
    "knowledge_graph",
    "threads",
    # safety
    "auth",
    "budget_breaker",
    "invariants",
    "recovery",
    "experiments",
    "chromatophores",
    # adapters
    "channels",
    "integrations",
    "mcp_client",
    "scheduler",
    "instrumentation",
    # platform
    "config",
    "models",
    "ui",
    "i18n",
    "kernel",
]
