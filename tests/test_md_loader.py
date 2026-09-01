# ruff: noqa: E402 — module-level imports below are intentionally late
"""Implementation note."""

from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

from runtime.execution.suckers import SkillRegistry
from runtime.execution.suckers.loader import (
    SkillLoadError,
    load_skill_from_md,
    load_skills_from_dir,
)

# ═══════════════════════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════════════════════


def _write_handler_py(
    dir_: Path,
    *,
    filename: str = "h.py",
    content: str = "def handler(text='', **_kw): return {'reversed': text[::-1]}",
) -> Path:
    p = dir_ / filename
    p.write_text(content, encoding="utf-8")
    return p


def _write_skill_md(
    dir_: Path,
    *,
    filename: str = "reverse.md",
    frontmatter: str,
    body: str = "# human docs",
) -> Path:
    p = dir_ / filename
    p.write_text(f"---\n{frontmatter}\n---\n\n{body}\n", encoding="utf-8")
    return p


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestSingleLoad:
    def test_load_with_file_handler(self, tmp_path: Path):
        _write_handler_py(tmp_path)
        md = _write_skill_md(
            tmp_path,
            frontmatter=(
                "name: reverse_text\n"
                "description: Reverse string\n"
                "affinity: [text]\n"
                "cost_profile: low\n"
                "trusted_source: skill://public/reverse\n"
                "handler: ./h.py:handler\n"
            ),
        )
        skill = load_skill_from_md(md)
        assert skill.name == "reverse_text"
        assert skill.description == "Reverse string"
        assert skill.affinity == ["text"]
        assert skill.cost_profile == "low"
        assert skill.handler(text="abc") == {"reversed": "cba"}

    def test_load_with_tests(self, tmp_path: Path):
        _write_handler_py(tmp_path)
        md = _write_skill_md(
            tmp_path,
            frontmatter=(
                "name: reverse_text\n"
                "trusted_source: skill://public/x\n"
                "handler: ./h.py:handler\n"
                "tests:\n"
                "  - name: basic\n"
                "    tier: golden\n"
                "    args: {text: 'abc'}\n"
                "    expect:\n"
                "      output_equals: {reversed: 'cba'}\n"
            ),
        )
        skill = load_skill_from_md(md)
        assert len(skill.tests) == 1
        case = skill.tests[0]
        assert case.name == "basic"
        assert case.tier == "golden"
        assert case.args == {"text": "abc"}
        assert case.expect.output_equals == {"reversed": "cba"}

    def test_load_with_module_handler(self, tmp_path: Path, monkeypatch):
        """Implementation note."""
        md = _write_skill_md(
            tmp_path,
            frontmatter=(
                "name: count_words_md\n"
                "trusted_source: skill://public/count_words_md\n"
                "handler: runtime.execution.suckers.builtins:_count_words\n"
            ),
        )
        skill = load_skill_from_md(md)
        result = skill.handler(text="one two three")
        assert result["words"] == 3


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestErrors:
    def test_missing_file(self, tmp_path: Path):
        with pytest.raises(SkillLoadError, match="not found"):
            load_skill_from_md(tmp_path / "nope.md")

    def test_not_a_file(self, tmp_path: Path):
        with pytest.raises(SkillLoadError, match="not a file"):
            load_skill_from_md(tmp_path)  # Implementation note.

    def test_no_frontmatter(self, tmp_path: Path):
        p = tmp_path / "x.md"
        p.write_text("# just body · no frontmatter", encoding="utf-8")
        with pytest.raises(SkillLoadError, match="no YAML frontmatter"):
            load_skill_from_md(p)

    def test_malformed_yaml(self, tmp_path: Path):
        _write_skill_md(
            tmp_path,
            frontmatter="name: [unclosed list\nhandler: x:y",
        )
        with pytest.raises(SkillLoadError, match="YAML parse failed"):
            load_skill_from_md(tmp_path / "reverse.md")

    def test_missing_handler(self, tmp_path: Path):
        _write_skill_md(
            tmp_path,
            frontmatter=("name: x\ntrusted_source: skill://public/x\n"),
        )
        with pytest.raises(SkillLoadError, match="missing 'handler'"):
            load_skill_from_md(tmp_path / "reverse.md")

    def test_missing_name(self, tmp_path: Path):
        _write_handler_py(tmp_path)
        _write_skill_md(
            tmp_path,
            frontmatter=("trusted_source: skill://public/x\nhandler: ./h.py:handler\n"),
        )
        with pytest.raises(SkillLoadError, match="missing required field 'name'"):
            load_skill_from_md(tmp_path / "reverse.md")

    def test_bad_handler_format(self, tmp_path: Path):
        _write_skill_md(
            tmp_path,
            frontmatter=("name: x\ntrusted_source: skill://public/x\nhandler: no_colon_here\n"),
        )
        with pytest.raises(SkillLoadError, match="must be '<module>:<func>'"):
            load_skill_from_md(tmp_path / "reverse.md")

    def test_unknown_module(self, tmp_path: Path):
        _write_skill_md(
            tmp_path,
            frontmatter=("name: x\ntrusted_source: skill://public/x\nhandler: no.such.module:f\n"),
        )
        with pytest.raises(SkillLoadError, match="cannot import"):
            load_skill_from_md(tmp_path / "reverse.md")

    def test_unknown_func_in_module(self, tmp_path: Path):
        _write_handler_py(tmp_path)
        _write_skill_md(
            tmp_path,
            frontmatter=(
                "name: x\ntrusted_source: skill://public/x\nhandler: ./h.py:no_such_func\n"
            ),
        )
        with pytest.raises(SkillLoadError, match="has no attribute"):
            load_skill_from_md(tmp_path / "reverse.md")

    def test_non_callable_target(self, tmp_path: Path):
        (tmp_path / "h.py").write_text("NOT_CALLABLE = 42\n", encoding="utf-8")
        _write_skill_md(
            tmp_path,
            frontmatter=(
                "name: x\ntrusted_source: skill://public/x\nhandler: ./h.py:NOT_CALLABLE\n"
            ),
        )
        with pytest.raises(SkillLoadError, match="not callable"):
            load_skill_from_md(tmp_path / "reverse.md")


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestPathSecurity:
    def test_relative_path_cannot_escape_base_dir(self, tmp_path: Path):
        """Implementation note."""
        evil = tmp_path / "evil.py"
        evil.write_text("def bad(**kw): return 'pwned'\n", encoding="utf-8")

        md_dir = tmp_path / "sub"
        md_dir.mkdir()
        _write_skill_md(
            md_dir,
            filename="x.md",
            frontmatter=("name: x\ntrusted_source: skill://public/x\nhandler: ../evil.py:bad\n"),
        )
        with pytest.raises(SkillLoadError, match="escapes base dir"):
            load_skill_from_md(md_dir / "x.md")

    def test_non_py_file_rejected(self, tmp_path: Path):
        (tmp_path / "h.txt").write_text("def f(): return 1", encoding="utf-8")
        _write_skill_md(
            tmp_path,
            frontmatter=("name: x\ntrusted_source: skill://public/x\nhandler: ./h.txt:f\n"),
        )
        with pytest.raises(SkillLoadError, match="must be .py"):
            load_skill_from_md(tmp_path / "reverse.md")


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestBulkLoad:
    def test_loads_multiple_md_files(self, tmp_path: Path):
        _write_handler_py(tmp_path)
        for name in ("a", "b", "c"):
            _write_skill_md(
                tmp_path,
                filename=f"{name}.md",
                frontmatter=(
                    f"name: skill_{name}\n"
                    f"trusted_source: skill://public/{name}\n"
                    "handler: ./h.py:handler\n"
                ),
            )
        skills = load_skills_from_dir(tmp_path)
        assert {s.name for s in skills} == {"skill_a", "skill_b", "skill_c"}

    def test_recursive_glob(self, tmp_path: Path):
        _write_handler_py(tmp_path)
        sub = tmp_path / "nested"
        sub.mkdir()
        # Implementation note.
        _write_handler_py(sub, filename="h.py")
        _write_skill_md(
            tmp_path,
            filename="top.md",
            frontmatter=("name: top\ntrusted_source: skill://public/t\nhandler: ./h.py:handler\n"),
        )
        _write_skill_md(
            sub,
            filename="deep.md",
            frontmatter=("name: deep\ntrusted_source: skill://public/d\nhandler: ./h.py:handler\n"),
        )
        flat = load_skills_from_dir(tmp_path)  # Implementation note.
        assert {s.name for s in flat} == {"top"}
        deep = load_skills_from_dir(tmp_path, recursive=True)
        assert {s.name for s in deep} == {"top", "deep"}

    def test_skip_errors_ignores_bad(self, tmp_path: Path):
        _write_handler_py(tmp_path)
        _write_skill_md(
            tmp_path,
            filename="good.md",
            frontmatter=("name: good\ntrusted_source: skill://public/g\nhandler: ./h.py:handler\n"),
        )
        # Implementation note.
        _write_skill_md(
            tmp_path,
            filename="bad.md",
            frontmatter="name: bad\ntrusted_source: skill://public/b\n",
        )
        with pytest.raises(SkillLoadError):
            load_skills_from_dir(tmp_path)  # Implementation note.
        ok = load_skills_from_dir(tmp_path, skip_errors=True)
        assert {s.name for s in ok} == {"good"}

    def test_not_a_directory(self, tmp_path: Path):
        f = tmp_path / "a.md"
        f.write_text("---\nname: x\n---", encoding="utf-8")
        with pytest.raises(SkillLoadError, match="not a directory"):
            load_skills_from_dir(f)


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestRegistryIntegration:
    def test_loaded_skill_registers_and_golden_passes(self, tmp_path: Path):
        _write_handler_py(tmp_path)
        md = _write_skill_md(
            tmp_path,
            frontmatter=(
                "name: reverse_text\n"
                "trusted_source: skill://public/reverse\n"
                "handler: ./h.py:handler\n"
                "tests:\n"
                "  - name: basic\n"
                "    tier: golden\n"
                "    args: {text: 'abc'}\n"
                "    expect:\n"
                "      output_equals: {reversed: 'cba'}\n"
            ),
        )
        skill = load_skill_from_md(md)
        reg = SkillRegistry()
        # Implementation note.
        reg.register(skill)
        assert reg.has("reverse_text")
        assert reg.get("reverse_text").handler(text="xyz") == {"reversed": "zyx"}

    def test_batch_load_then_register(self, tmp_path: Path):
        _write_handler_py(tmp_path)
        for name in ("a", "b"):
            _write_skill_md(
                tmp_path,
                filename=f"{name}.md",
                frontmatter=(
                    f"name: skill_{name}\n"
                    f"trusted_source: skill://public/{name}\n"
                    "handler: ./h.py:handler\n"
                ),
            )
        skills = load_skills_from_dir(tmp_path)
        reg = SkillRegistry()
        for s in skills:
            reg.register(s, verify_tests=False)  # Implementation note.
        assert reg.has("skill_a") and reg.has("skill_b")
