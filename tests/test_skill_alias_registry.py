from __future__ import annotations

from runtime.execution.suckers.registry import Skill, SkillRegistry


def _skill(name: str) -> Skill:
    return Skill(
        name=name,
        description="test skill",
        trusted_source="builtin://test",
        handler=lambda: {"ok": True},
    )


def test_legacy_skill_alias_resolves_without_duplicate_catalog_entry() -> None:
    registry = SkillRegistry()
    registry.register(_skill("ad-creative"), verify_tests=False)

    assert registry.has("ad-copywriter")
    assert registry.get("ad-copywriter").name == "ad-creative"
    assert registry.all_names() == ["ad-creative"]

