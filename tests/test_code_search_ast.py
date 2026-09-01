"""Tests for AST-based mode of `code_search` skill (tree-sitter backed).

Covers:
1. mode='regex' preserves legacy behaviour
2. ast mode rejects missing query_type / target_name
3. function_calls skip comments and string literals
4. function_definitions resolve names exactly
5. imports match identifiers in `import` and `from ... import` statements
6. Tree-sitter dependency missing path returns dependency_missing error
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import pytest

# Skip the whole module when tree-sitter is not available so the suite
# stays green in dependency-light environments.
pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_python")

from runtime.execution.suckers import code_intelligence_skills as cis


def _write(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# 1. regex mode preserves existing behaviour
# ---------------------------------------------------------------------------


def test_regex_mode_missing_query_returns_error(tmp_path: Path) -> None:
    out = cis._code_search(directory=str(tmp_path))
    assert "error" in out
    assert out["error"]


def test_regex_mode_text_fallback_finds_matches(monkeypatch, tmp_path: Path) -> None:
    # Force fallback path (no embedder) for deterministic regex/text behaviour.
    monkeypatch.setattr(cis, "_get_embedder", lambda: None)
    _write(tmp_path, "x.py", "needle = 1\nother = 2\n")
    out = cis._code_search("needle", directory=str(tmp_path))
    assert out.get("backend") == "text_fallback"
    assert any(r["path"].endswith("x.py") for r in out.get("results", []))


# ---------------------------------------------------------------------------
# 2. ast mode argument validation
# ---------------------------------------------------------------------------


def test_ast_mode_missing_query_type(tmp_path: Path) -> None:
    out = cis._code_search(mode="ast", target_name="foo", root=str(tmp_path))
    assert out.get("error_type") == "invalid_argument"


def test_ast_mode_invalid_query_type(tmp_path: Path) -> None:
    out = cis._code_search(
        mode="ast",
        query_type="bogus",
        target_name="foo",
        root=str(tmp_path),
    )
    assert out.get("error_type") == "invalid_argument"


def test_ast_mode_missing_target_name(tmp_path: Path) -> None:
    out = cis._code_search(mode="ast", query_type="function_calls", root=str(tmp_path))
    assert out.get("error_type") == "invalid_argument"


# ---------------------------------------------------------------------------
# 3. function_calls — must skip comments and string literals
# ---------------------------------------------------------------------------


def test_ast_function_calls_skips_comments_and_strings(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "calls.py",
        'def foo(): pass\nfoo()\nfoo()\n# foo() in comment\nx = "foo()"\n',
    )
    out = cis._code_search(
        mode="ast",
        query_type="function_calls",
        target_name="foo",
        root=str(tmp_path),
    )
    assert out["backend"] == "ast"
    assert out["count"] == 2, out
    lines = sorted(m["line"] for m in out["matches"])
    assert lines == [2, 3]
    assert all(m["kind"] == "call" for m in out["matches"])


def test_ast_function_calls_handles_method_calls(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "obj.py",
        "import x\nx.foo()\nobj.bar.foo()\nfoo()\n",
    )
    out = cis._code_search(
        mode="ast",
        query_type="function_calls",
        target_name="foo",
        root=str(tmp_path),
    )
    # all three 'foo'-calls should match (method form takes the trailing attr)
    assert out["count"] == 3, out


# ---------------------------------------------------------------------------
# 4. function_definitions
# ---------------------------------------------------------------------------


def test_ast_function_definitions_exact_name(tmp_path: Path) -> None:
    _write(tmp_path, "defs.py", "def foo(): pass\ndef bar(): pass\n")

    foo_out = cis._code_search(
        mode="ast",
        query_type="function_definitions",
        target_name="foo",
        root=str(tmp_path),
    )
    bar_out = cis._code_search(
        mode="ast",
        query_type="function_definitions",
        target_name="bar",
        root=str(tmp_path),
    )
    nope_out = cis._code_search(
        mode="ast",
        query_type="function_definitions",
        target_name="baz",
        root=str(tmp_path),
    )
    assert foo_out["count"] == 1
    assert foo_out["matches"][0]["line"] == 1
    assert bar_out["count"] == 1
    assert bar_out["matches"][0]["line"] == 2
    assert nope_out["count"] == 0


def test_ast_class_definitions(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "klass.py",
        "class Foo:\n    pass\nclass Bar:\n    pass\n",
    )
    out = cis._code_search(
        mode="ast",
        query_type="class_definitions",
        target_name="Foo",
        root=str(tmp_path),
    )
    assert out["count"] == 1
    assert out["matches"][0]["line"] == 1
    assert out["matches"][0]["kind"] == "class_definition"


# ---------------------------------------------------------------------------
# 5. imports
# ---------------------------------------------------------------------------


def test_ast_imports_python(tmp_path: Path) -> None:
    _write(tmp_path, "imp.py", "import os\nfrom typing import List\n")

    os_out = cis._code_search(
        mode="ast",
        query_type="imports",
        target_name="os",
        root=str(tmp_path),
    )
    list_out = cis._code_search(
        mode="ast",
        query_type="imports",
        target_name="List",
        root=str(tmp_path),
    )
    miss_out = cis._code_search(
        mode="ast",
        query_type="imports",
        target_name="Dict",
        root=str(tmp_path),
    )

    assert os_out["count"] == 1
    assert os_out["matches"][0]["line"] == 1
    assert list_out["count"] == 1
    assert list_out["matches"][0]["line"] == 2
    assert miss_out["count"] == 0


# ---------------------------------------------------------------------------
# 6. tree-sitter dependency missing path
# ---------------------------------------------------------------------------


def test_ast_mode_dependency_missing(monkeypatch, tmp_path: Path) -> None:
    """Simulate tree_sitter import failing inside _ast_search."""
    import builtins

    real_import = builtins.__import__

    def _fake_import(name: str, globals=None, locals=None, fromlist=(), level=0) -> Any:
        if name == "tree_sitter" and not fromlist:
            raise ImportError("simulated missing tree_sitter")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _fake_import)

    out = cis._code_search(
        mode="ast",
        query_type="function_calls",
        target_name="foo",
        root=str(tmp_path),
    )
    assert out.get("error_type") == "dependency_missing"
    assert out.get("error") == "ast_unavailable"
    assert "hint" in out


# ---------------------------------------------------------------------------
# helper: ensure module imports cleanly when run individually
# ---------------------------------------------------------------------------


def test_module_importable() -> None:
    importlib.reload(cis)
    assert hasattr(cis, "_code_search")
