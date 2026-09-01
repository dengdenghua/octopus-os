"""Implementation note."""

from __future__ import annotations

from pathlib import Path

from runtime.execution.suckers import SkillRegistry
from runtime.execution.suckers.fs_search_skills import (
    _glob_files,
    _grep_text,
    _read_file_range,
    _tree,
    register_fs_search_skills,
)

# ─── Registration ────────────────────────────────────────────


class TestRegistration:
    def test_register_installs_all_four(self):
        r = SkillRegistry()
        count = register_fs_search_skills(r)
        assert count == 4
        for name in ("glob_files", "grep_text", "tree", "read_file_range"):
            assert r.has(name), f"missing: {name}"

    def test_trusted_sources_are_skill_public(self):
        r = SkillRegistry()
        register_fs_search_skills(r)
        for name in ("glob_files", "grep_text", "tree", "read_file_range"):
            assert r.get(name).trusted_source.startswith("skill://public/")

    def test_unified_base_catalog_exposes_fs_search(self):
        from runtime.execution.all_skills import (
            BASE_SKILL_IDS,
            register_base,
            skill_group,
            skill_kind,
        )

        r = SkillRegistry()
        register_base(r)

        for name in ("glob_files", "grep_text", "tree", "read_file_range"):
            assert name in BASE_SKILL_IDS
            assert r.has(name), f"missing from base registry: {name}"
            assert skill_group(name) == "fs_search"
            assert skill_kind(name) == "system"


# ─── glob_files ──────────────────────────────────────────────


class TestGlobFiles:
    def test_matches_simple_pattern(self, tmp_path: Path):
        (tmp_path / "a.py").write_text("x")
        (tmp_path / "b.py").write_text("y")
        (tmp_path / "readme.md").write_text("doc")
        r = _glob_files(pattern="*.py", root=str(tmp_path))
        names = {f["path"] for f in r["files"]}
        assert names == {"a.py", "b.py"}
        assert r["count"] == 2

    def test_recursive_double_star(self, tmp_path: Path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "deep").mkdir()
        (tmp_path / "src" / "deep" / "a.py").write_text("x")
        (tmp_path / "src" / "b.py").write_text("y")
        r = _glob_files(pattern="**/*.py", root=str(tmp_path))
        assert r["count"] == 2

    def test_missing_root_returns_error(self):
        r = _glob_files(pattern="*.py", root="/not/a/real/path/xyz")
        assert "error" in r

    def test_not_a_directory(self, tmp_path: Path):
        f = tmp_path / "file.txt"
        f.write_text("x")
        r = _glob_files(pattern="*.py", root=str(f))
        assert "error" in r

    def test_excludes_hidden(self, tmp_path: Path):
        (tmp_path / "visible.py").write_text("x")
        (tmp_path / ".hidden.py").write_text("y")
        r = _glob_files(pattern="*.py", root=str(tmp_path))
        names = {f["path"] for f in r["files"]}
        assert "visible.py" in names
        assert ".hidden.py" not in names

    def test_max_results_caps_output(self, tmp_path: Path):
        for i in range(20):
            (tmp_path / f"f{i}.txt").write_text("x")
        r = _glob_files(pattern="*.txt", root=str(tmp_path), max_results=5)
        assert r["count"] == 5
        assert r["truncated"]

    def test_dirs_excluded_by_default(self, tmp_path: Path):
        (tmp_path / "sub").mkdir()
        (tmp_path / "file.x").write_text("y")
        r = _glob_files(pattern="*", root=str(tmp_path))
        names = {f["path"] for f in r["files"]}
        assert "sub" not in names
        assert "file.x" in names


# ─── grep_text ───────────────────────────────────────────────


class TestGrepText:
    def test_finds_matches(self, tmp_path: Path):
        (tmp_path / "a.txt").write_text("hello\nworld\nhello again\n")
        r = _grep_text(pattern="hello", root=str(tmp_path), glob="*.txt")
        assert r["count"] == 2
        lines = {m["line"] for m in r["matches"]}
        assert lines == {1, 3}

    def test_ignore_case(self, tmp_path: Path):
        (tmp_path / "a.txt").write_text("Hello\nHELLO\nhello\n")
        r = _grep_text(
            pattern="hello",
            root=str(tmp_path),
            glob="*.txt",
            ignore_case=True,
        )
        assert r["count"] == 3

    def test_bad_regex_returns_error(self, tmp_path: Path):
        r = _grep_text(pattern="[unclosed", root=str(tmp_path))
        assert "error" in r
        assert "bad_regex" in r["error"]

    def test_missing_root_returns_error(self):
        r = _grep_text(pattern="x", root="/not/real/xyz")
        assert "error" in r

    def test_skips_non_utf8(self, tmp_path: Path):
        # Binary file · grep must skip silently, not crash
        (tmp_path / "b.bin").write_bytes(b"\x80\xff\x00notutf8")
        (tmp_path / "a.txt").write_text("hello\n")
        r = _grep_text(pattern="hello", root=str(tmp_path), glob="*")
        assert r["count"] == 1

    def test_max_matches_caps_output(self, tmp_path: Path):
        (tmp_path / "a.txt").write_text("x\n" * 50)
        r = _grep_text(
            pattern="x",
            root=str(tmp_path),
            glob="*.txt",
            max_matches=10,
        )
        assert r["count"] == 10
        assert r["truncated"]

    def test_truncates_long_line(self, tmp_path: Path):
        long = "x" * 2000
        (tmp_path / "a.txt").write_text(long + "\n")
        r = _grep_text(pattern="x", root=str(tmp_path), glob="*.txt", max_matches=1)
        assert len(r["matches"][0]["text"]) <= 500


# ─── tree ────────────────────────────────────────────────────


class TestTree:
    def test_builds_nested_structure(self, tmp_path: Path):
        (tmp_path / "a.txt").write_text("x")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "b.txt").write_text("y")
        r = _tree(root=str(tmp_path), max_depth=3)
        assert r["tree"]["is_dir"]
        names = [c["name"] for c in r["tree"]["children"]]
        assert "a.txt" in names
        assert "sub" in names
        sub = next(c for c in r["tree"]["children"] if c["name"] == "sub")
        sub_names = [c["name"] for c in sub["children"]]
        assert "b.txt" in sub_names

    def test_depth_limit(self, tmp_path: Path):
        # Build a chain 4 levels deep
        cur = tmp_path
        for i in range(4):
            cur = cur / f"d{i}"
            cur.mkdir()
        r = _tree(root=str(tmp_path), max_depth=2)
        # Walk down the children; at depth=2 we should see children_truncated
        node = r["tree"]
        depth = 0
        while node.get("children"):
            node = node["children"][0]
            depth += 1
            if not node.get("is_dir"):
                break
        assert depth <= 2 or "children_truncated" in node

    def test_hidden_excluded_by_default(self, tmp_path: Path):
        (tmp_path / "visible").mkdir()
        (tmp_path / ".hidden").mkdir()
        r = _tree(root=str(tmp_path), max_depth=2)
        names = [c["name"] for c in r["tree"]["children"]]
        assert "visible" in names
        assert ".hidden" not in names

    def test_missing_root_returns_error(self):
        r = _tree(root="/not/real/xyz")
        assert "error" in r


# ─── read_file_range ─────────────────────────────────────────


class TestReadFileRange:
    def test_reads_range(self, tmp_path: Path):
        lines = [f"line{i}" for i in range(1, 11)]
        p = tmp_path / "a.txt"
        p.write_text("\n".join(lines))
        r = _read_file_range(path=str(p), offset=3, limit=4)
        assert r["offset"] == 3
        assert r["returned_lines"] == 4
        assert r["content"] == "line3\nline4\nline5\nline6"

    def test_limit_beyond_file_caps(self, tmp_path: Path):
        p = tmp_path / "a.txt"
        p.write_text("one\ntwo\nthree")
        r = _read_file_range(path=str(p), offset=1, limit=100)
        assert r["returned_lines"] == 3
        assert not r["truncated"]

    def test_truncated_flag(self, tmp_path: Path):
        p = tmp_path / "a.txt"
        p.write_text("\n".join(str(i) for i in range(1, 11)))
        r = _read_file_range(path=str(p), offset=1, limit=5)
        assert r["truncated"]

    def test_missing_file_returns_error(self):
        r = _read_file_range(path="/no/such/file/zzz.txt")
        assert "error" in r

    def test_not_a_file(self, tmp_path: Path):
        r = _read_file_range(path=str(tmp_path))
        assert "error" in r

    def test_offset_clamped_to_1(self, tmp_path: Path):
        p = tmp_path / "a.txt"
        p.write_text("a\nb\nc")
        r = _read_file_range(path=str(p), offset=0, limit=1)
        assert r["offset"] == 1
        assert r["content"] == "a"
