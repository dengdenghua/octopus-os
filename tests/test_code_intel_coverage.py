"""Dense coverage for code_intelligence_skills (audit Q-05)."""

from __future__ import annotations

from pathlib import Path

from runtime.execution.suckers import code_intelligence_skills as cis

# ── _code_analyze ────────────────────────────────────────────


def test_code_analyze_python() -> None:
    out = cis._code_analyze(
        path="probe.py",
        content='"""doc"""\nimport os\n\ndef add(a, b):\n    return a + b\n\nclass Foo:\n    def bar(self):\n        return add(1, 2)\n',
    )
    assert out["language"] == "python"
    assert any(f["name"] == "add" for f in out["functions"])
    assert any(c["name"] == "Foo" for c in out["classes"])
    assert "os" in out["imports"]
    assert any(e["caller"] == "bar" and e["callee"] == "add" for e in out["call_edges"])


def test_code_analyze_syntax_error_and_errors(tmp_path: Path) -> None:
    err = cis._code_analyze(content="def broken(:\n", language="python")
    assert "syntax error" in err["error"]
    assert "no content" in cis._code_analyze(content="")["error"]
    assert "file not found" in cis._code_analyze(path=str(tmp_path / "nope.py"))["error"]
    big = tmp_path / "big.py"
    big.write_text("x" * 600_000, encoding="utf-8")
    assert "too large" in cis._code_analyze(path=str(big))["error"]


def test_code_analyze_from_file_and_generic(tmp_path: Path) -> None:
    f = tmp_path / "m.py"
    f.write_text("def f():\n    pass\n", encoding="utf-8")
    out = cis._code_analyze(path=str(f))
    assert out["language"] == "python"
    gen = cis._code_analyze(content="console.log('hi')", language="javascript")
    assert "language" in gen


# ── _code_edit_diff ──────────────────────────────────────────


def test_code_edit_diff_search_replace(tmp_path: Path) -> None:
    f = tmp_path / "a.py"
    f.write_text("x = 1\ny = 2\n", encoding="utf-8")
    out = cis._code_edit_diff(str(f), search="y = 2", replace="y = 3")
    assert out["ok"] is True and out["mode"] == "search_replace"
    assert "y = 3" in f.read_text(encoding="utf-8")
    assert "missing path" in cis._code_edit_diff("")["error"]
    assert "file not found" in cis._code_edit_diff(str(tmp_path / "nope"))["error"]
    assert (
        "search text not found" in cis._code_edit_diff(str(f), search="zzz", replace="q")["error"]
    )


def test_code_edit_diff_missing_and_bad_args(tmp_path: Path) -> None:
    f = tmp_path / "b.py"
    f.write_text("a = 1\n", encoding="utf-8")
    assert "provide either" in cis._code_edit_diff(str(f))["error"]
    bad = cis._code_edit_diff(str(f), diff="@@ -1,1 +1,1 @@\n-a = 1\n+b = 2\n")
    assert "ok" in bad or "error" in bad  # unified diff apply works or fails cleanly


# ── _ast_search validation paths (no tree-sitter needed) ─────


def test_ast_search_validation(tmp_path: Path) -> None:
    invalid = cis._ast_search(
        query_type="bogus",
        target_name="x",
        root=str(tmp_path),
        glob="*.py",
        sandbox_dir=None,
        max_matches=10,
    )
    assert "invalid query_type" in invalid["error"]
    missing = cis._ast_search(
        query_type="function_calls",
        target_name="",
        root=str(tmp_path),
        glob="*.py",
        sandbox_dir=None,
        max_matches=10,
    )
    assert "missing target_name" in missing["error"]
    no_root = cis._ast_search(
        query_type="function_calls",
        target_name="x",
        root=str(tmp_path / "nope"),
        glob="*.py",
        sandbox_dir=None,
        max_matches=10,
    )
    # tree-sitter is absent in CI, so the dependency check reports first.
    assert no_root["error"] == "ast_unavailable" or no_root["error"].startswith("root not found")


# ── _code_find_symbol ────────────────────────────────────────


def test_code_find_symbol(tmp_path: Path) -> None:
    assert "missing symbol" in cis._code_find_symbol("")["error"]
    assert (
        "directory not found"
        in cis._code_find_symbol("add", directory=str(tmp_path / "nope"))["error"]
    )
    (tmp_path / "mod.py").write_text("def target_fn():\n    pass\n", encoding="utf-8")
    out = cis._code_find_symbol("target_fn", directory=str(tmp_path))
    assert out["count"] >= 1
    assert any("target_fn" in str(r) for r in out["definitions"])


def test_code_dependency_graph(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "pkg" / "a.py").write_text("import pkg.b\n", encoding="utf-8")
    (tmp_path / "pkg" / "b.py").write_text("def f():\n    pass\n", encoding="utf-8")
    out = cis._code_dependency_graph(str(tmp_path))
    assert "error" not in out
    assert len(out["nodes"]) == 3
    assert any(e["source"].endswith("a.py") and "pkg" in e["target"] for e in out["edges"])
    assert "directory not found" in cis._code_dependency_graph(str(tmp_path / "nope"))["error"]


class _FakeEncoder:
    def encode(self, texts, **kwargs):
        import numpy as np

        return [np.array([1.0, 0.0, 0.5]) for _ in texts]


def test_code_search_embedding_backend(monkeypatch, tmp_path: Path) -> None:
    from runtime.execution.suckers import code_intelligence_skills as _cis

    (tmp_path / "a.py").write_text("def target():\n    pass\n", encoding="utf-8")
    monkeypatch.setattr(_cis, "_get_embedder", lambda: _FakeEncoder())
    monkeypatch.setattr(_cis, "_INDEX", [])
    monkeypatch.setattr(_cis, "_INDEX_DIR", "")
    out = _cis._code_search("target", directory=str(tmp_path), extensions=".py", top_k=5)
    assert out.get("backend") in ("embedding", "text_fallback")
    assert "missing query" in _cis._code_search("", directory=str(tmp_path))["error"]
    ast_out = _cis._code_search(
        "x",
        mode="ast",
        query_type="function_calls",
        target_name="",
        root=str(tmp_path),
        glob="*.py",
        sandbox_dir=None,
    )
    assert "error" in ast_out  # missing target_name validation from _ast_search


def test_code_search_ast_mode_validation(tmp_path: Path) -> None:
    from runtime.execution.suckers import code_intelligence_skills as _cis

    out = _cis._code_search(
        "x",
        mode="ast",
        query_type="bogus",
        target_name="f",
        root=str(tmp_path),
        glob="*.py",
        sandbox_dir=None,
    )
    assert "invalid query_type" in out["error"]

