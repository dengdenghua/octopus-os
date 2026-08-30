"""Persona, mode and trusted prompt-skill projection for Coder turns."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from runtime.execution.codex_backend import role_context
from runtime.execution.suckers.registry import Skill, SkillRegistry


class _AllowPolicy:
    def __init__(self, names: set[str]) -> None:
        self._names = names

    def allows(self, name: str) -> bool:
        return name in self._names


def _agent(*names: str) -> SimpleNamespace:
    policy = _AllowPolicy(set(names))
    return SimpleNamespace(
        agent_id="coder",
        display_name="Kane",
        soul="Kane persona: terse, reliable, and code-focused.",
        skill_policy=lambda: policy,
    )


def test_role_persona_modes_and_only_registry_resolved_skill_content_are_injected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trusted_root = tmp_path / "trusted-skills"
    trusted_skill = trusted_root / "good" / "SKILL.md"
    trusted_skill.parent.mkdir(parents=True)
    trusted_skill.write_text(
        "---\nname: good\n---\nTRUSTED_GOOD_INSTRUCTION",
        encoding="utf-8",
    )
    outside = tmp_path / "client-selected" / "SKILL.md"
    outside.parent.mkdir(parents=True)
    outside.write_text("CLIENT_PATH_MUST_NOT_LOAD", encoding="utf-8")
    monkeypatch.setattr(role_context, "_prompt_skill_roots", lambda: (trusted_root,))

    registry = SkillRegistry()
    for name, source in (
        ("good", "skill://public/good"),
        ("plugin_action", "plugin://tenant/plugin_action"),
        ("evil", "skill://public/evil"),
    ):
        registry.register(
            Skill(
                name=name,
                description=f"{name} action",
                trusted_source=source,
                handler=lambda **_kwargs: {"ok": True},
            ),
            verify_tests=False,
        )

    instructions = role_context.compose_codex_role_instructions(
        _agent("good", "plugin_action", "evil"),
        context={
            "agent_mode": "audit",
            "personal_mode": "research",
            "workflow_preset": "audit.review",
            "personal_instructions": "PERSONAL_MODE_MARKER",
            # A browser/model-provided path is ordinary untrusted metadata and
            # is deliberately not consulted by the skill resolver.
            "skill_path": str(outside),
        },
        goal="$good $plugin_action $evil inspect this",
        registry=registry,
    )

    assert "Kane persona" in instructions
    assert "当前项目子模式: audit" in instructions
    assert "当前工作流: audit.review" in instructions
    assert "PERSONAL_MODE_MARKER" in instructions
    assert "TRUSTED_GOOD_INSTRUCTION" in instructions
    assert "CLIENT_PATH_MUST_NOT_LOAD" not in instructions
    assert str(outside) not in instructions
    assert "plugin_action action" not in instructions
    assert "ambient user Codex MCP servers" in instructions


def test_explicit_skill_mentions_must_be_allowed_enabled_and_known(tmp_path: Path) -> None:
    registry = SkillRegistry()
    registry.register(
        Skill(
            name="known",
            description="Known action",
            trusted_source="skill://public/known",
            handler=lambda: None,
        ),
        verify_tests=False,
    )
    registry.disable("known")

    rendered = role_context.resolve_explicit_skill_instructions(
        "$known $unknown @skill:unknown",
        registry=registry,
        agent=_agent("known", "unknown"),
    )
    assert rendered == ""

