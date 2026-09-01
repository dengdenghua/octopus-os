"""Dense coverage for the project-agnostic wiki generator (audit Q-05)."""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.sensing.gateway import wiki_generic as wg


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "mod.py").write_text(
        '"""A module docstring."""\n\ndef add(a, b):\n    return a + b\n\nclass Foo:\n    pass\n',
        encoding="utf-8",
    )
    (root / "app.js").write_text(
        "// header\nfunction hello() {}\nexport const x = 1;\n", encoding="utf-8"
    )
    (root / "README.md").write_text("# Project\n\nSome docs.\n", encoding="utf-8")
    (root / "docs").mkdir()
    (root / "docs" / "guide.md").write_text("## Guide\n", encoding="utf-8")
    return root


def test_summarize_file_python(tmp_path: Path) -> None:
    f = tmp_path / "mod.py"
    f.write_text(
        '"""A module docstring."""\n\ndef add(a, b):\n    return a + b\n', encoding="utf-8"
    )
    summary, symbols = wg._summarize_file(f, "python")
    assert "A module docstring" in summary
    assert "add" in symbols


def test_summarize_file_js_generic_and_unreadable(tmp_path: Path) -> None:
    js = tmp_path / "a.js"
    js.write_text("/**\n * A JSDoc summary.\n */\nfunction f() {}\n", encoding="utf-8")
    s, syms = wg._summarize_file(js, "javascript")
    assert "JSDoc" in s
    txt = tmp_path / "b.txt"
    txt.write_text("plain text\n", encoding="utf-8")
    s2, _ = wg._summarize_file(txt, "text")
    assert s2
    s3, _ = wg._summarize_file(tmp_path / "missing.py", "python")
    assert "(unreadable)" in s3


def test_walk_finds_files_and_skips_noise(tmp_path: Path) -> None:
    root = _project(tmp_path)
    (root / ".echo-wiki").mkdir()
    (root / ".echo-wiki" / "index.json").write_text("{}", encoding="utf-8")
    files = wg._walk(root)
    paths = {f["path"] for f in files}
    assert "src/mod.py" in paths
    assert "app.js" in paths
    assert not any(".echo-wiki" in p for p in paths)
    assert wg._walk(root / "missing") == []


def test_render_lang_md_and_readme(tmp_path: Path) -> None:
    root = _project(tmp_path)
    files = wg._walk(root)
    lang_md = wg._render_lang_md("python", files)
    assert "mod.py" in lang_md
    readme = wg._render_readme(root, files)
    assert "By language" in readme


def test_generate_end_to_end(tmp_path: Path) -> None:
    root = _project(tmp_path)
    result = wg.generate(root)
    assert result["files_analyzed"] >= 2
    assert result["by_lang"].get("python") == 1
    wiki = wg.wiki_dir(root)
    assert (wiki / "index.json").exists()
    assert (wiki / "README.md").exists()
    assert (wiki / "by-language" / "python.md").exists()


def test_status_before_and_after_generate(tmp_path: Path) -> None:
    root = _project(tmp_path)
    before = wg.status(root)
    assert before["status"] == "not_generated"
    wg.generate(root)
    after = wg.status(root)
    assert after["exists"] is True
    assert after["files_analyzed"] >= 2


def test_list_and_read_doc(tmp_path: Path) -> None:
    root = _project(tmp_path)
    assert wg.list_docs(root) == []
    wg.generate(root)
    listed = wg.list_docs(root)
    assert listed and all("path" in d for d in listed)
    content = wg.read_doc(root, "README.md")
    assert "wiki" in content.lower() or "#" in content
    with pytest.raises(FileNotFoundError):
        wg.read_doc(root, "nope.md")
    with pytest.raises(PermissionError):
        wg.read_doc(root, "../etc/passwd")


def test_settings_roundtrip(tmp_path: Path) -> None:
    root = _project(tmp_path)
    assert wg.get_settings(root).get("autosync") is False
    saved = wg.set_settings(root, autosync=True)
    assert saved.get("autosync") is True
    assert wg.get_settings(root).get("autosync") is True

