"""Canonical iOS capability list loaded from ios SKILL.md files.

Mirrors :mod:`runtime.tentacle.mobile.capabilities` but loads skills under
``runtime/tentacle/ios/skills/`` whose names use the ``ios.*`` prefix.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from runtime.tentacle.llm.skill_manifest import SkillManifestLoader


def ios_skills_root() -> Path:
    """Return the canonical runtime/tentacle/ios/skills directory."""
    candidates = [
        Path(__file__).resolve().parent / "skills",
        Path.cwd() / "runtime" / "tentacle" / "ios" / "skills",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return candidates[0]


@lru_cache(maxsize=1)
def ios_capabilities() -> tuple[str, ...]:
    """Load all iOS capability names from the canonical SKILL.md set."""
    specs = SkillManifestLoader().load_directory(ios_skills_root())
    return tuple(sorted(spec.name for spec in specs if spec.name.startswith("ios.")))


__all__ = [
    "ios_capabilities",
    "ios_skills_root",
]
