"""Tests for ``propose_patch`` skill (lane G — diff-as-tool)."""

from __future__ import annotations

from pathlib import Path

from runtime.execution.suckers.code_edit_skills import _propose_patch


def _write(p: Path, content: str) -> None:
    p.write_text(content, encoding="utf-8")


def test_dry_run_does_not_modify_file(tmp_path: Path) -> None:
    f = tmp_path / "a.py"
    _write(f, "line1\nline2\nline3\n")
    diff = "--- a/a.py\n+++ b/a.py\n@@ -1,3 +1,3 @@\n-line1\n+LINE_ONE\n line2\n line3\n"
    result = _propose_patch(
        path=str(f),
        unified_diff=diff,
        dry_run=True,
        sandbox_dir=str(tmp_path),
    )
    assert result["ok"] is True
    assert result["dry_run"] is True
    # File still has original content
    assert f.read_text(encoding="utf-8") == "line1\nline2\nline3\n"


def test_apply_writes_file(tmp_path: Path) -> None:
    f = tmp_path / "a.py"
    _write(f, "line1\nline2\nline3\n")
    diff = "--- a/a.py\n+++ b/a.py\n@@ -1,3 +1,3 @@\n-line1\n+LINE_ONE\n line2\n line3\n"
    result = _propose_patch(
        path=str(f),
        unified_diff=diff,
        dry_run=False,
        sandbox_dir=str(tmp_path),
    )
    assert result["ok"] is True
    assert result["dry_run"] is False
    assert "LINE_ONE" in f.read_text(encoding="utf-8")
    assert "line1\n" not in f.read_text(encoding="utf-8")


def test_missing_path() -> None:
    result = _propose_patch(path="", unified_diff="--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n")
    assert result["error_type"] == "invalid_argument"


def test_missing_diff(tmp_path: Path) -> None:
    f = tmp_path / "a.py"
    _write(f, "x")
    result = _propose_patch(
        path=str(f),
        unified_diff="",
        sandbox_dir=str(tmp_path),
    )
    assert result["error_type"] == "invalid_argument"


def test_file_not_found(tmp_path: Path) -> None:
    diff = "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n"
    result = _propose_patch(
        path=str(tmp_path / "nope.py"),
        unified_diff=diff,
        sandbox_dir=str(tmp_path),
    )
    assert result["error_type"] == "not_found"


def test_no_op_diff(tmp_path: Path) -> None:
    """Diff whose - lines don't match anything in the file: the
    underlying patcher inserts the + lines anyway (best-effort
    behaviour). The skill returns ``ok=True`` with the (possibly
    surprising) result so the model can review the preview and
    decide whether to commit. The contract is "dry_run never lies
    about what would land", not "perfectly validate the diff".
    """
    f = tmp_path / "a.py"
    _write(f, "line1\nline2\n")
    diff = "--- a/a.py\n+++ b/a.py\n@@ -1,1 +1,1 @@\n-no_match_anywhere\n+replacement\n"
    result = _propose_patch(
        path=str(f),
        unified_diff=diff,
        dry_run=True,
        sandbox_dir=str(tmp_path),
    )
    # Either it was rejected as no-op, OR the dry-run preview shows
    # the + line somewhere — both are acceptable outcomes.
    if "error" in result:
        assert result["error_type"] in {"invalid_argument", "patch_apply_failed"}
    else:
        assert any("replacement" in h.get("preview", "") for h in result.get("hunks_preview", []))


def test_preview_shows_hunks(tmp_path: Path) -> None:
    f = tmp_path / "a.py"
    _write(f, "line1\nline2\nline3\nline4\nline5\n")
    diff = "--- a/a.py\n+++ b/a.py\n@@ -2,1 +2,1 @@\n-line2\n+changed\n"
    result = _propose_patch(
        path=str(f),
        unified_diff=diff,
        dry_run=True,
        sandbox_dir=str(tmp_path),
    )
    assert result["ok"] is True
    assert "hunks_preview" in result
    assert len(result["hunks_preview"]) >= 1
    assert any("changed" in h.get("preview", "") for h in result["hunks_preview"])
