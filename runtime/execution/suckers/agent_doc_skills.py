"""Agent documentation skills loaded from ``skills/public``.

These are prompt-as-skill packages copied from public agent-skill
repositories. They are registered explicitly so code/admin mode can
whitelist and call them like normal tools.
"""

from __future__ import annotations

from pathlib import Path

from .market_skills import immutable_prompt_catalog_required, load_single_market_skill
from .registry import SkillRegistry

AGENT_DOC_SKILL_IDS: tuple[str, ...] = (
    "gemini-api-dev",
    "gemini-interactions-api",
    "gemini-live-api-dev",
    "vertex-ai-api-dev",
    "frontend-ui-engineering",
    "api-and-interface-design",
    "browser-testing-with-devtools",
    "performance-optimization",
    "code-review-and-quality",
    "awesome-design-md",
    "frontend-design",
    "react-best-practices",
    "typescript-best-practices",
    "code-quality",
    "uiux-pro-max",
    "writing-plans",
    "brainstorming",
)


def register_agent_doc_skills(registry: SkillRegistry) -> int:
    # Load from the preferred external location (skills/public/).
    # Falls back to the legacy in-package all_skills/ directory if the
    # external path is unavailable (e.g. bare wheel install without resources).
    from runtime.platform.process.paths import resources_root

    external_dir = resources_root() / "skills" / "public"
    legacy_dir = Path(__file__).resolve().parent.parent / "all_skills"
    all_skills_dir = (
        legacy_dir
        if immutable_prompt_catalog_required()
        else external_dir
        if external_dir.is_dir()
        else legacy_dir
    )
    registered = 0
    for skill_id in AGENT_DOC_SKILL_IDS:
        if load_single_market_skill(
            registry,
            skill_id,
            all_skills_dir=all_skills_dir,
            ignore_frontmatter_enabled=True,
            verify_tests=False,
        ):
            registered += 1
    return registered


__all__ = ["AGENT_DOC_SKILL_IDS", "register_agent_doc_skills"]
