"""Dense coverage for _code_intel_helpers (audit Q-05)."""

from __future__ import annotations

from pathlib import Path

from runtime.execution.suckers import _code_intel_helpers as h


def test_guess_language() -> None:
    assert h._guess_language("a.py") == "python"
    assert h._guess_language("a.js") == "javascript"
    assert h._guess_language("a.ts") == "typescript"
    assert h._guess_language("a.go") == "go"
    assert h._guess_language("a.rb") == "ruby"
    assert h._guess_language("a.unknown") == "unknown"


def test_analyze_generic_languages() -> None:
    js = h._analyze_generic(
        "export function foo() {}\nclass Bar {}\nimport x from 'y';\n", "a.js", "javascript"
    )
    assert any(f["name"] == "foo" for f in js["functions"])
    assert any(c["name"] == "Bar" for c in js["classes"])
    assert "y" in js["imports"]

    go = h._analyze_generic("func main() {}\ntype T struct {}", "a.go", "go")
    assert any(f["name"] == "main" for f in go["functions"])
    assert any(c["name"] == "T" for c in go["classes"])

    rust = h._analyze_generic("pub fn run() {}\npub struct S {}", "a.rs", "rust")
    assert any(f["name"] == "run" for f in rust["functions"])
    assert any(c["name"] == "S" for c in rust["classes"])

    ruby = h._analyze_generic("def hi\nend\nclass K\nend\nrequire 'x'\n", "a.rb", "ruby")
    assert any(f["name"] == "hi" for f in ruby["functions"])
    assert any(c["name"] == "K" for c in ruby["classes"])
    assert "x" in ruby["imports"]


def test_expand_brace_glob() -> None:
    assert h._expand_brace_glob("*.py") == ["*.py"]
    out = h._expand_brace_glob("src/{a,b}.py")
    assert set(out) == {"src/a.py", "src/b.py"}
    nested = h._expand_brace_glob("x/{1,2}/{a,b}")
    assert len(nested) == 4


def test_split_into_chunks() -> None:
    short = h._split_into_chunks("def f():\n    pass\n", "m.py")
    assert len(short) == 1 and "m.py" in short[0]
    # A boundary (blank line) after the 50-line threshold triggers a split.
    many = (
        "\n".join(f"line{i}" for i in range(55)) + "\n\n" + "\n".join(f"tail{i}" for i in range(20))
    )
    chunks = h._split_into_chunks(many, "big.py")
    assert len(chunks) >= 2
    empty = h._split_into_chunks("   ", "e.py")
    assert empty == []


def test_fallback_text_search(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("def target():\n    x = 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("no match here\n", encoding="utf-8")
    out = h._fallback_text_search("target", str(tmp_path), ".py", 5)
    assert out["count"] == 1
    assert out["results"][0]["path"] == "a.py"
    assert h._fallback_text_search("zzz", str(tmp_path), ".py", 5)["count"] == 0
    assert h._fallback_text_search("q", str(tmp_path / "nope"), ".py", 5)["results"] == []


def test_apply_unified_diff() -> None:
    original = "a = 1\nb = 2\n"
    diff = "@@ -1,2 +1,2 @@\n-a = 1\n+b = 3\n b = 2\n"
    applied = h._apply_unified_diff(original, diff)
    assert "b = 3" in applied and "b = 2" in applied
    # An unparseable diff leaves the original untouched (never raises).
    assert h._apply_unified_diff(original, "not a diff") == original

