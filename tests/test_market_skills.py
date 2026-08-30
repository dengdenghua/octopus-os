"""Implementation note."""

from __future__ import annotations

import json
from pathlib import Path

from runtime.execution.suckers import SkillRegistry
from runtime.execution.suckers.market_skills import (
    _parse_frontmatter,
    _sanitize_skill_name,
    load_single_market_skill,
    register_market_skills,
)

# Implementation note.


class TestParseFrontmatter:
    def test_simple_key_value(self):
        text = "---\nname: demo\ndescription: hi\n---\nbody"
        meta, body = _parse_frontmatter(text)
        assert meta == {"name": "demo", "description": "hi"}
        assert body == "body"

    def test_no_frontmatter_returns_empty_meta(self):
        text = "just a body\nwith lines"
        meta, body = _parse_frontmatter(text)
        assert meta == {}
        assert body == text.strip()

    def test_block_scalar_folded(self):
        text = "---\ndescription: >\n  this is a\n  folded description\n---\nhi"
        meta, _ = _parse_frontmatter(text)
        assert meta["description"] == "this is a folded description"

    def test_list_expanded(self):
        text = "---\ntags:\n  - one\n  - two\n---\nbody"
        meta, _ = _parse_frontmatter(text)
        assert meta["tags"] == ["one", "two"]

    def test_inline_list(self):
        text = "---\ntags: [a, b, c]\n---\nx"
        meta, _ = _parse_frontmatter(text)
        assert meta["tags"] == ["a", "b", "c"]

    def test_bools_coerced(self):
        text = "---\nenabled: false\nalways_apply: true\n---\nx"
        meta, _ = _parse_frontmatter(text)
        assert meta["enabled"] is False
        assert meta["always_apply"] is True


# ─── sanitize ────────────────────────────────────────────────


class TestSanitize:
    def test_keeps_ascii_alnum(self):
        assert _sanitize_skill_name("shopify-builder", "x") == "shopify-builder"
        assert _sanitize_skill_name("copy_writing_27", "x") == "copy_writing_27"

    def test_replaces_special(self):
        assert _sanitize_skill_name("weird space!", "x") == "weird_space_"

    def test_empty_uses_fallback(self):
        assert _sanitize_skill_name("", "fallback") == "fallback"
        assert _sanitize_skill_name("   ", "fb") == "fb"


# Implementation note.


class TestRegistration:
    def test_registers_basic_skill(self, tmp_path: Path):
        skill = tmp_path / "copywriter"
        skill.mkdir()
        (skill / "SKILL.md").write_text(
            "---\nname: copywriter\ndescription: write copy.\n---\n# Guide\n\nDo the thing.\n",
            encoding="utf-8",
        )
        r = SkillRegistry()
        count = register_market_skills(r, all_skills_dir=tmp_path)
        assert count == 1
        assert r.has("copywriter")
        s = r.get("copywriter")
        assert s.trusted_source == "skill://all_skills/copywriter"
        assert "market" in s.affinity

    def test_handler_returns_instructions_and_resources(self, tmp_path: Path):
        skill = tmp_path / "pdf"
        skill.mkdir()
        (skill / "SKILL.md").write_text(
            "---\nname: pdf\ndescription: PDF stuff.\n---\n# PDF Guide\n\nStep 1. Open.\n",
            encoding="utf-8",
        )
        (skill / "reference.md").write_text("extra docs", encoding="utf-8")
        (skill / "scripts").mkdir()
        (skill / "scripts" / "helper.py").write_text("print(1)", encoding="utf-8")

        r = SkillRegistry()
        register_market_skills(r, all_skills_dir=tmp_path)
        payload = r.get("pdf").handler()
        assert payload["skill"] == "pdf"
        assert "PDF Guide" in payload["instructions"]
        res_paths = {x["path"] for x in payload["resources"]}
        assert "reference.md" in res_paths
        script_paths = {x["path"] for x in payload["scripts"]}
        assert "scripts/helper.py" in script_paths
        assert Path(payload["cwd"]) == skill.resolve()

    def test_frontmatter_enabled_false_skipped(self, tmp_path: Path):
        off = tmp_path / "disabled_one"
        off.mkdir()
        (off / "SKILL.md").write_text(
            "---\nname: disabled_one\ndescription: x\nenabled: false\n---\nbody",
            encoding="utf-8",
        )
        on = tmp_path / "enabled_one"
        on.mkdir()
        (on / "SKILL.md").write_text(
            "---\nname: enabled_one\ndescription: y\n---\nbody",
            encoding="utf-8",
        )
        r = SkillRegistry()
        count = register_market_skills(r, all_skills_dir=tmp_path)
        assert count == 1
        assert r.has("enabled_one")
        assert not r.has("disabled_one")

    def test_skills_config_can_re_enable(self, tmp_path: Path):
        # frontmatter enabled omitted; config says enabled=true → load
        skill = tmp_path / "my-skill"
        skill.mkdir()
        (skill / "SKILL.md").write_text(
            "---\nname: my-skill\ndescription: z\n---\nbody",
            encoding="utf-8",
        )
        cfg = {"99_my-skill": {"installedVersion": "0.0.1", "enabled": True}}
        (tmp_path / "skills_config.json").write_text(
            json.dumps(cfg),
            encoding="utf-8",
        )
        r = SkillRegistry()
        count = register_market_skills(r, all_skills_dir=tmp_path)
        assert count == 1
        assert r.has("my-skill")

    def test_missing_name_falls_back_to_dirname(self, tmp_path: Path):
        # Implementation note.
        skill = tmp_path / "from-dir"
        skill.mkdir()
        (skill / "SKILL.md").write_text(
            "---\ndescription: no name\n---\nbody",
            encoding="utf-8",
        )
        r = SkillRegistry()
        register_market_skills(r, all_skills_dir=tmp_path)
        assert r.has("from-dir")

    def test_duplicate_names_skipped(self, tmp_path: Path):
        # Implementation note.
        for sub in ("a", "b"):
            d = tmp_path / sub
            d.mkdir()
            (d / "SKILL.md").write_text(
                "---\nname: same\ndescription: x\n---\nbody",
                encoding="utf-8",
            )
        r = SkillRegistry()
        count = register_market_skills(r, all_skills_dir=tmp_path)
        assert count == 1
        assert r.has("same")

    def test_body_truncated_at_limit(self, tmp_path: Path):
        skill = tmp_path / "huge"
        skill.mkdir()
        huge = "x" * (40 * 1024)  # 40 KB · over 32 KB cap
        (skill / "SKILL.md").write_text(
            f"---\nname: huge\ndescription: big.\n---\n{huge}",
            encoding="utf-8",
        )
        r = SkillRegistry()
        register_market_skills(r, all_skills_dir=tmp_path)
        payload = r.get("huge").handler()
        # Truncation marker appended · body within cap + marker length
        assert "[... truncated ...]" in payload["instructions"]
        assert len(payload["instructions"]) < 40 * 1024

    def test_missing_directory_returns_zero(self, tmp_path: Path):
        r = SkillRegistry()
        count = register_market_skills(
            r,
            all_skills_dir=tmp_path / "does_not_exist",
        )
        assert count == 0


# Implementation note.
#
# Implementation note.
# Implementation note.


class TestLoadSingleMarketSkill:
    """Implementation note."""

    def test_loads_skill_with_enabled_false(self, tmp_path: Path):
        skill = tmp_path / "closed"
        skill.mkdir()
        (skill / "SKILL.md").write_text(
            "---\nname: closed\ndescription: off by default.\nenabled: false\n---\nbody",
            encoding="utf-8",
        )
        r = SkillRegistry()
        # Implementation note.
        register_market_skills(r, all_skills_dir=tmp_path)
        assert not r.has("closed")
        # Implementation note.
        ok = load_single_market_skill(r, "closed", all_skills_dir=tmp_path)
        assert ok
        assert r.has("closed")

    def test_returns_true_when_already_registered(self, tmp_path: Path):
        skill = tmp_path / "on"
        skill.mkdir()
        (skill / "SKILL.md").write_text(
            "---\nname: on\ndescription: x\n---\nbody",
            encoding="utf-8",
        )
        r = SkillRegistry()
        register_market_skills(r, all_skills_dir=tmp_path)
        # Implementation note.
        assert load_single_market_skill(r, "on", all_skills_dir=tmp_path)

    def test_returns_false_for_missing_dir(self, tmp_path: Path):
        r = SkillRegistry()
        assert not load_single_market_skill(
            r,
            "does_not_exist",
            all_skills_dir=tmp_path,
        )

    def test_returns_false_when_all_skills_dir_missing(self, tmp_path: Path):
        r = SkillRegistry()
        assert not load_single_market_skill(
            r,
            "x",
            all_skills_dir=tmp_path / "nope",
        )

    def test_respects_frontmatter_when_opted_in(self, tmp_path: Path):
        skill = tmp_path / "off"
        skill.mkdir()
        (skill / "SKILL.md").write_text(
            "---\nname: off\ndescription: x\nenabled: false\n---\nbody",
            encoding="utf-8",
        )
        r = SkillRegistry()
        ok = load_single_market_skill(
            r,
            "off",
            all_skills_dir=tmp_path,
            ignore_frontmatter_enabled=False,
        )
        assert not ok
        assert not r.has("off")


class TestRealAllSkillsDirSmoke:
    def test_registers_some_real_skills(self):
        r = SkillRegistry()
        count = register_market_skills(r)
        # Implementation note.
        assert count >= 3, f"expected >= 3 market skills, got {count}"
        # Implementation note.
        all_names = set(r.all_names())
        expected = {"backend-building", "browse", "code-quality"}
        assert expected <= all_names, (
            f"missing real bundled skills {sorted(expected - all_names)}; "
            f"registry has {sorted(all_names)[:20]}..."
        )


class TestAliases:
    """``aliases:`` frontmatter field lets one physical SKILL.md serve
    multiple trigger names. Pins the contract that motivated the
    deduplication of EN/CN twin skills (see project_known_defects.md
    P3 cluster)."""

    def test_inline_alias_list_registers_canonical_plus_aliases(
        self,
        tmp_path: Path,
    ):
        skill = tmp_path / "valuation"
        skill.mkdir()
        (skill / "SKILL.md").write_text(
            "---\n"
            "name: valuation\n"
            "description: Run a DCF.\n"
            "aliases: [dcf-model, cashflow-valuation]\n"
            "---\n"
            "# DCF\n",
            encoding="utf-8",
        )
        r = SkillRegistry()
        count = register_market_skills(r, all_skills_dir=tmp_path)
        assert count == 3
        assert r.has("valuation")
        assert r.has("dcf-model")
        assert r.has("cashflow-valuation")
        # Aliases share handler bodies — every name returns the same
        # instructions so prompts keying on either name look identical.
        assert (
            r.get("valuation").handler()["instructions"]
            == (r.get("dcf-model").handler()["instructions"])
        )
        # Aliases are flagged as such via trusted_source so dedupers
        # / metrics can tell aliases from canonicals.
        assert r.get("valuation").trusted_source.endswith("/valuation")
        assert "#alias" in r.get("dcf-model").trusted_source

    def test_block_style_alias_list(self, tmp_path: Path):
        skill = tmp_path / "primary"
        skill.mkdir()
        (skill / "SKILL.md").write_text(
            "---\n"
            "name: primary\n"
            "description: x\n"
            "aliases:\n"
            "  - secondary\n"
            "  - tertiary\n"
            "---\n"
            "body\n",
            encoding="utf-8",
        )
        r = SkillRegistry()
        count = register_market_skills(r, all_skills_dir=tmp_path)
        assert count == 3
        assert r.has("primary")
        assert r.has("secondary")
        assert r.has("tertiary")

    def test_alias_collision_with_existing_skill_is_skipped(
        self,
        tmp_path: Path,
    ):
        # Pre-register a skill with the alias name; the alias should
        # back off rather than raising.
        first = tmp_path / "first"
        first.mkdir()
        (first / "SKILL.md").write_text(
            "---\nname: first\ndescription: f.\n---\nbody\n",
            encoding="utf-8",
        )
        second = tmp_path / "second"
        second.mkdir()
        (second / "SKILL.md").write_text(
            "---\nname: second\ndescription: s.\naliases: [first]\n---\nbody\n",
            encoding="utf-8",
        )
        r = SkillRegistry()
        count = register_market_skills(r, all_skills_dir=tmp_path)
        # ``first`` registers normally, ``second`` registers, alias
        # ``first`` collides and is skipped → 2 not 3.
        assert count == 2
        assert r.has("first")
        assert r.has("second")
        # Original ``first`` handler wins; alias didn't overwrite.
        assert "f" in r.get("first").description.lower()
