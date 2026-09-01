"""Unit tests for ``runtime.execution.tool_engine.tool_taxonomy``.

Covers the core classification matrix: every (affinity, trusted_source,
name) combination that should produce a distinct ``ToolTaxonomy``.
"""

from __future__ import annotations

from dataclasses import dataclass

from runtime.execution.tool_engine.tool_taxonomy import (
    ToolTaxonomy,
    classify_skill,
    register_taxonomy,
    reset_overrides,
    taxonomy_to_audit_dict,
)


@dataclass
class _FakeSkill:
    """Lightweight stand-in for ``Skill`` — duck-typed for classify_skill."""

    name: str
    affinity: list[str]
    trusted_source: str


def _skill(name: str, affinity: list[str], src: str = "builtin://x") -> _FakeSkill:
    return _FakeSkill(name=name, affinity=affinity, trusted_source=src)


def test_classify_readonly_file_skill() -> None:
    skill = _skill("read_file", affinity=["file"])
    tax = classify_skill(skill)
    assert tax.kind == "Read"
    assert tax.readonly is True
    assert tax.namespace == "builtin"


def test_classify_write_skill_is_edit() -> None:
    skill = _skill("write_file", affinity=["write", "file"])
    tax = classify_skill(skill)
    assert tax.kind == "Edit"
    assert tax.readonly is False


def test_classify_exec_skill() -> None:
    skill = _skill("run_shell", affinity=["exec", "dangerous"])
    tax = classify_skill(skill)
    assert tax.kind == "Execute"
    assert tax.readonly is False
    assert "dangerous" in tax.tags


def test_classify_delete_skill_is_edit() -> None:
    skill = _skill("delete_file", affinity=["delete"])
    tax = classify_skill(skill)
    assert tax.kind == "Edit"
    assert tax.readonly is False


def test_classify_web_search_skill() -> None:
    skill = _skill("search_web", affinity=["web", "crawler"])
    tax = classify_skill(skill)
    assert tax.kind == "WebSearch"
    assert tax.readonly is True


def test_classify_subagent_by_name() -> None:
    skill = _skill("spawn_subagent_explore", affinity=[])
    tax = classify_skill(skill)
    assert tax.kind == "Subagent"


def test_classify_mcp_namespace_overrides_kind() -> None:
    skill = _skill("custom_query", affinity=["read"], src="mcp://my-server/q")
    tax = classify_skill(skill)
    assert tax.kind == "MCP"
    assert tax.namespace == "mcp"


def test_classify_skill_public_namespace() -> None:
    skill = _skill("dcf", affinity=[], src="skill://public/dcf")
    tax = classify_skill(skill)
    assert tax.namespace == "skill.public"


def test_classify_skill_team_namespace() -> None:
    skill = _skill("internal_tool", affinity=[], src="skill://team/finance/x")
    tax = classify_skill(skill)
    assert tax.namespace == "skill.team"


def test_classify_unknown_source_falls_back_to_scheme() -> None:
    skill = _skill("custom_x", affinity=[], src="custom://my-tool")
    tax = classify_skill(skill)
    assert tax.namespace == "custom"


def test_register_taxonomy_override() -> None:
    skill = _skill("read_file", affinity=["file"])
    # Force an override that says read_file is actually Edit
    override = ToolTaxonomy(kind="Edit", namespace="builtin", readonly=False, version=2)
    register_taxonomy("read_file", override)
    try:
        tax = classify_skill(skill)
        assert tax.kind == "Edit"
        assert tax.version == 2
        assert tax.readonly is False
    finally:
        reset_overrides()


def test_reset_overrides_clears_state() -> None:
    register_taxonomy(
        "x",
        ToolTaxonomy(kind="Other", namespace="builtin", readonly=True),
    )
    reset_overrides()
    # After reset, classify falls back to derived taxonomy.
    skill = _skill("read_file", affinity=["file"])
    tax = classify_skill(skill)
    assert tax.kind == "Read"


def test_taxonomy_to_audit_dict_shape() -> None:
    tax = ToolTaxonomy(
        kind="Edit",
        namespace="skill.public",
        readonly=False,
        version=3,
        tags=("write", "dangerous"),
    )
    audit = taxonomy_to_audit_dict(tax)
    assert "x.echo/tool" in audit
    block = audit["x.echo/tool"]
    assert block["kind"] == "Edit"
    assert block["namespace"] == "skill.public"
    assert block["readonly"] is False
    assert block["version"] == 3
    # Tags intentionally not in audit payload — kept internal.
    assert "tags" not in block


def test_to_dict_includes_tags() -> None:
    tax = ToolTaxonomy(
        kind="Read",
        namespace="builtin",
        readonly=True,
        tags=("file",),
    )
    assert tax.to_dict() == {
        "kind": "Read",
        "namespace": "builtin",
        "readonly": True,
        "version": 1,
        "tags": ["file"],
    }

