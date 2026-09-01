"""Canonical Android capability list loaded from mobile SKILL.md files."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from runtime.tentacle.llm.skill_manifest import SkillManifestLoader


def mobile_skills_root() -> Path:
    """Return the canonical runtime/tentacle/mobile/skills directory."""
    candidates = [
        Path(__file__).resolve().parent / "skills",
        Path.cwd() / "runtime" / "tentacle" / "mobile" / "skills",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return candidates[0]


@lru_cache(maxsize=1)
def android_capabilities() -> tuple[str, ...]:
    """Load all Android capability names from the canonical SKILL.md set."""
    specs = SkillManifestLoader().load_directory(mobile_skills_root())
    return tuple(sorted(spec.name for spec in specs if spec.name.startswith("android.")))


@lru_cache(maxsize=1)
def android_browser_capabilities() -> tuple[str, ...]:
    """Load browser-only Android capabilities for the mobile browser arm."""
    return tuple(
        name
        for name in android_capabilities()
        if name.startswith("android.browser.") and name != "android.browser.install_extension"
    )


__all__ = [
    "android_browser_capabilities",
    "android_capabilities",
    "mobile_skills_root",
]
