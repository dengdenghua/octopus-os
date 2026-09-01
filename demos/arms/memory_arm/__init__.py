"""memory_arm — reference execution arm over the REAL memory skill family.

This is the "new block = a directory + a manifest" proof scaled to a real
432-line skill family: the arm declares ``kind: arm``, provides the memory
skills, consumes the ``memory`` service, and registers the family through
``register_memory_skills`` (idempotent — re-loading never crashes).

The extra ``memory_arm.recall`` skill demonstrates the composition-layer
socket: its handler resolves the ``memory`` service through
``ctx.service_bus`` at call time — no direct import of the journal, so
swapping the memory backend never touches this arm.
"""

from __future__ import annotations

from runtime.execution.suckers.memory_skills import register_memory_skills
from runtime.execution.suckers.registry import Skill
from runtime.platform.plugins.plugin_base import ModulePlugin

SKILL_NAME = "memory_arm.recall"


class MemoryArmPlugin(ModulePlugin):
    name = "memory_arm"

    def register_skills(self) -> None:
        ctx = self.ctx

        # Real family: 12 memory skills (remember / recall / diary_write / …).
        if ctx.skill_registry is not None:
            register_memory_skills(ctx.skill_registry)

        # Composition-layer demo: a skill that consumes the `memory` service.
        def _handle(input: dict | None = None) -> str:
            memory = ctx.service_bus.require("memory")
            rows = memory.recall(event_type="task_started", limit=5)
            return f"recalled {len(rows)} journal events via memory service"

        ctx.register_skill(
            Skill(
                name=SKILL_NAME,
                description=(
                    "Recall recent journal events through the composition "
                    "layer's `memory` service (reference arm template)."
                ),
                affinity=["memory", "recall", "arm"],
                cost_profile="low",
                trusted_source="builtin://memory_arm",
                handler=_handle,
            )
        )

