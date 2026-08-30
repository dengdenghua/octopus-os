"""Agent skill registration compatibility surface consumed by Echo OS."""

from runtime.execution.suckers.registry import Skill, SkillRegistry
from runtime.execution.suckers.testing import SkillExpect, SkillTestCase

__all__ = ["Skill", "SkillExpect", "SkillRegistry", "SkillTestCase"]
