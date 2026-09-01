"""blackboard_arm — reference execution arm #2 (composition-layer template).

Proves the arm contract generalizes beyond memory: a self-contained
collaboration family (blackboard read/write) registered as a ``kind: arm``
block with no service dependency. Together with ``memory_arm`` (which
demonstrates ``consumes: [memory]``), this covers both arm shapes.
"""

from __future__ import annotations

from runtime.execution.suckers.blackboard_skills import register_blackboard_skills
from runtime.platform.plugins.plugin_base import ModulePlugin


class BlackboardArmPlugin(ModulePlugin):
    name = "blackboard_arm"

    def register_skills(self) -> None:
        ctx = self.ctx
        if ctx.skill_registry is not None:
            register_blackboard_skills(ctx.skill_registry)

