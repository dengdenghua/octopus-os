"""Tests for ``grep_text`` with ripgrep-style ``context_lines`` (lane A).

The skill now accepts ``context_lines: int`` (0..10). Each match comes
back with optional ``before`` / ``after`` lists carrying the
surrounding lines so the model can read context without round-tripping
to ``read_file``.
"""

from __future__ import annotations

from pathlib import Path

from runtime.execution.suckers.fs_search_skills import _grep_text


def _write_lines(p: Path, lines: list[str]) -> None:
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_grep_text_no_context_by_default(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    _write_lines(f, ["zero", "one", "MATCH", "three", "four"])
    out = _grep_text(
        "MATCH",
        str(tmp_path),
        sandbox_dir=str(tmp_path),
        allow_sensitive=True,
    )
    assert out["count"] == 1
    m = out["matches"][0]
    assert m["line"] == 3
    assert "before" not in m
    assert "after" not in m
    assert out["context_lines"] == 0


def test_grep_text_with_context_2(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    _write_lines(f, ["zero", "one", "two", "MATCH", "four", "five", "six"])
    out = _grep_text(
        "MATCH",
        str(tmp_path),
        context_lines=2,
        sandbox_dir=str(tmp_path),
        allow_sensitive=True,
    )
    assert out["count"] == 1
    m = out["matches"][0]
    assert m["line"] == 4
    assert [b["line"] for b in m["before"]] == [2, 3]
    assert [b["text"] for b in m["before"]] == ["one", "two"]
    assert [a["line"] for a in m["after"]] == [5, 6]
    assert [a["text"] for a in m["after"]] == ["four", "five"]
    assert out["context_lines"] == 2


def test_grep_text_context_at_file_start(tmp_path: Path) -> None:
    """Context window must clamp at file start (no negative line numbers)."""
    f = tmp_path / "a.txt"
    _write_lines(f, ["MATCH", "two", "three"])
    out = _grep_text(
        "MATCH",
        str(tmp_path),
        context_lines=3,
        sandbox_dir=str(tmp_path),
        allow_sensitive=True,
    )
    m = out["matches"][0]
    assert m["line"] == 1
    assert "before" not in m  # no lines before line 1
    assert [a["line"] for a in m["after"]] == [2, 3]


def test_grep_text_context_at_file_end(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    _write_lines(f, ["one", "two", "MATCH"])
    out = _grep_text(
        "MATCH",
        str(tmp_path),
        context_lines=3,
        sandbox_dir=str(tmp_path),
        allow_sensitive=True,
    )
    m = out["matches"][0]
    assert m["line"] == 3
    assert [b["line"] for b in m["before"]] == [1, 2]
    assert "after" not in m


def test_grep_text_context_clamped_to_max_10(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    _write_lines(
        f, [f"line-{i}" for i in range(50)] + ["MATCH"] + [f"after-{i}" for i in range(50)]
    )
    out = _grep_text(
        "MATCH",
        str(tmp_path),
        context_lines=999,  # request more than allowed
        sandbox_dir=str(tmp_path),
        allow_sensitive=True,
    )
    m = out["matches"][0]
    # Capped at 10
    assert len(m["before"]) == 10
    assert len(m["after"]) == 10
    assert out["context_lines"] == 10
