"""
Tests for ``runtime.platform.prompts.seed``.

Covers
------
1.  ``seed_if_empty`` writes files when the registry dir is empty.
2.  ``seed_if_empty`` is a no-op when any ``*.md`` already exists.
3.  ``render`` substitutes ``{{ var }}`` placeholders.
4.  ``render`` leaves unknown ``{{ placeholders }}`` intact.
5.  Variant lookup · ``variant="friendly"`` returns the friendly body.
6.  Variant fallback · missing variant falls through to the base.
7.  All three default templates render without errors (smoke loop).
8.  Base ``agent_system_prompt`` contains the ``{{ personality }}``
    placeholder.
9.  ``friendly.md`` ≠ ``pragmatic.md`` ≠ base (content diversity).
10. ``render`` raises ``KeyError`` for an unknown template name.
11. ``seed_if_empty`` uses atomic writes · no ``.bak`` sibling is
    produced on the initial seed (``.bak`` only appears on overwrites).

All tests isolate state via ``tmp_path`` and construct their own
``PromptRegistry`` instance — no shared global registry, no CWD
dependence.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.platform.prompts.registry import PromptRegistry
from runtime.platform.prompts.seed import (
    DEFAULT_TEMPLATES,
    DEFAULT_VARIANTS,
    render,
    seed_if_empty,
)

# ═══════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════


@pytest.fixture()
def empty_registry(tmp_path: Path) -> PromptRegistry:
    """A fresh, empty registry rooted in ``tmp_path``."""
    return PromptRegistry(tmp_path)


@pytest.fixture()
def seeded_registry(tmp_path: Path) -> PromptRegistry:
    """A registry pre-populated with the default seed set."""
    reg = PromptRegistry(tmp_path)
    seed_if_empty(reg)
    return reg


# ═══════════════════════════════════════════════════════════
# 1 — seed writes when empty
# ═══════════════════════════════════════════════════════════


def test_seed_if_empty_writes_defaults(empty_registry: PromptRegistry, tmp_path: Path) -> None:
    written = seed_if_empty(empty_registry)

    # Every base template + every variant must be written.
    expected = len(DEFAULT_TEMPLATES) + sum(len(v) for v in DEFAULT_VARIANTS.values())
    assert written == expected

    # Base files land in prompts_dir itself.
    for name in DEFAULT_TEMPLATES:
        assert (tmp_path / f"{name}.md").is_file()

    # Variants land under variants/<name>/<variant>.md.
    for name, variants in DEFAULT_VARIANTS.items():
        for variant in variants:
            vpath = tmp_path / "variants" / name / f"{variant}.md"
            assert vpath.is_file()


# ═══════════════════════════════════════════════════════════
# 2 — seed is a no-op when the dir already has content
# ═══════════════════════════════════════════════════════════


def test_seed_if_empty_noop_when_populated(
    tmp_path: Path,
) -> None:
    # Pre-existing template written by hand — simulates an operator
    # who already customized their prompts/ dir.
    preexisting = tmp_path / "custom.md"
    preexisting.write_text("hand-crafted\n", encoding="utf-8")

    reg = PromptRegistry(tmp_path)
    written = seed_if_empty(reg)

    assert written == 0
    # Content untouched.
    assert preexisting.read_text(encoding="utf-8") == "hand-crafted\n"
    # None of the defaults snuck in.
    for name in DEFAULT_TEMPLATES:
        assert not (tmp_path / f"{name}.md").exists()


# ═══════════════════════════════════════════════════════════
# 3 — render substitutes {{ var }}
# ═══════════════════════════════════════════════════════════


def test_render_substitutes_placeholder(
    seeded_registry: PromptRegistry,
) -> None:
    out = render(
        seeded_registry,
        "agent_system_prompt",
        {
            "personality": "PERSONALITY-SENTINEL",
            "workspace_context": "WORKSPACE-SENTINEL",
        },
    )
    assert "PERSONALITY-SENTINEL" in out
    assert "WORKSPACE-SENTINEL" in out
    # Neither placeholder should remain unreplaced.
    assert "{{ personality }}" not in out
    assert "{{ workspace_context }}" not in out


# ═══════════════════════════════════════════════════════════
# 4 — render leaves unknown {{ placeholders }} intact
# ═══════════════════════════════════════════════════════════


def test_render_leaves_unknown_placeholder_intact(
    tmp_path: Path,
) -> None:
    reg = PromptRegistry(tmp_path)
    reg.set("custom", "hello {{ name }}, your role is {{ role }}")

    out = render(reg, "custom", {"name": "Ada"})

    assert "hello Ada" in out
    # ``role`` was not provided · must survive verbatim.
    assert "{{ role }}" in out


# ═══════════════════════════════════════════════════════════
# 5 — variant lookup returns the variant body
# ═══════════════════════════════════════════════════════════


def test_render_variant_returns_variant_body(
    seeded_registry: PromptRegistry,
) -> None:
    friendly = render(seeded_registry, "agent_system_prompt", {}, variant="friendly")
    # Friendly variant's literal title is a reliable witness.
    assert "Friendly Variant" in friendly
    assert "Pragmatic Variant" not in friendly


# ═══════════════════════════════════════════════════════════
# 6 — variant fallback to base when variant missing
# ═══════════════════════════════════════════════════════════


def test_render_variant_fallback_to_base(
    seeded_registry: PromptRegistry,
) -> None:
    out = render(
        seeded_registry,
        "agent_system_prompt",
        {},
        variant="does_not_exist",
    )
    # Base template has no "Variant" in its heading.
    assert "Variant" not in out.splitlines()[0]
    # But it DOES contain the untouched personality placeholder · the
    # caller passed no variables.
    assert "{{ personality }}" in out


# ═══════════════════════════════════════════════════════════
# 7 — smoke loop · every default template renders without error
# ═══════════════════════════════════════════════════════════


def test_all_defaults_render_without_error(
    seeded_registry: PromptRegistry,
) -> None:
    for name in DEFAULT_TEMPLATES:
        out = render(
            seeded_registry,
            name,
            {
                "personality": "x",
                "workspace_context": "y",
            },
        )
        assert isinstance(out, str)
        assert out  # non-empty


# ═══════════════════════════════════════════════════════════
# 8 — base agent_system_prompt carries {{ personality }}
# ═══════════════════════════════════════════════════════════


def test_base_template_has_personality_placeholder() -> None:
    base = DEFAULT_TEMPLATES["agent_system_prompt"]
    assert "{{ personality }}" in base


# ═══════════════════════════════════════════════════════════
# 9 — content diversity across variants and base
# ═══════════════════════════════════════════════════════════


def test_variants_differ_from_each_other_and_from_base() -> None:
    base = DEFAULT_TEMPLATES["agent_system_prompt"]
    friendly = DEFAULT_VARIANTS["agent_system_prompt"]["friendly"]
    pragmatic = DEFAULT_VARIANTS["agent_system_prompt"]["pragmatic"]

    assert friendly != pragmatic
    assert friendly != base
    assert pragmatic != base


# ═══════════════════════════════════════════════════════════
# 10 — render raises KeyError on unknown template
# ═══════════════════════════════════════════════════════════


def test_render_raises_keyerror_for_missing_template(
    empty_registry: PromptRegistry,
) -> None:
    with pytest.raises(KeyError):
        render(empty_registry, "nonexistent_template", {})


# ═══════════════════════════════════════════════════════════
# 11 — seeding uses atomic_write_text · no .bak on first write
# ═══════════════════════════════════════════════════════════


def test_seed_does_not_leave_bak_on_first_write(
    tmp_path: Path,
) -> None:
    reg = PromptRegistry(tmp_path)
    seed_if_empty(reg)

    # atomic_write_text only rotates a ``.bak`` sibling when overwriting
    # an existing file. On a fresh seed, no file pre-existed, so no
    # ``.bak`` anywhere under the tree.
    baks = list(tmp_path.rglob("*.bak"))
    assert baks == [], f"unexpected .bak files on first seed: {baks}"

    # Meanwhile, overwriting an existing template *should* produce a
    # .bak · sanity-check the contrast so the above assertion isn't
    # vacuous.
    reg.set("agent_system_prompt", "fresh body")
    baks_after_overwrite = list(tmp_path.rglob("*.bak"))
    assert baks_after_overwrite, "expected a .bak sibling after overwriting an existing template"
