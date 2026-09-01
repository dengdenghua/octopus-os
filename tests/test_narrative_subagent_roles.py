from runtime.execution.suckers.ephemeral_agents import BUILTIN_ROLES

NARRATIVE_ROLES = {
    "narrative-outline",
    "narrative-draft",
    "narrative-continuity",
    "narrative-style",
    "narrative-revision",
    "narrative-editorial",
}


def test_narrative_pipeline_roles_are_builtin_and_context_isolated() -> None:
    assert BUILTIN_ROLES.keys() >= NARRATIVE_ROLES
    for role_id in NARRATIVE_ROLES:
        role = BUILTIN_ROLES[role_id]
        assert role.share_context is False
        assert role.share_memory is False
        assert role.tool_allowlist == ("bb_read", "bb_keys")
        assert "canon" in role.system_prompt.lower()


def test_narrative_pipeline_roles_have_distinct_responsibilities() -> None:
    prompts = {role_id: BUILTIN_ROLES[role_id].system_prompt for role_id in NARRATIVE_ROLES}

    assert "outline" in prompts["narrative-outline"].lower()
    assert "prose" in prompts["narrative-draft"].lower()
    assert "continuity" in prompts["narrative-continuity"].lower()
    assert "style" in prompts["narrative-style"].lower()
    assert "revised" in prompts["narrative-revision"].lower()
    assert "recommendation" in prompts["narrative-editorial"].lower()
    assert len(set(prompts.values())) == len(NARRATIVE_ROLES)

