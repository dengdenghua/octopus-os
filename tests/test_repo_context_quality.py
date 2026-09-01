from __future__ import annotations

from runtime.safety.evolution.repo_context_quality import classify_dirty_worktree


def test_classify_dirty_worktree_preserves_overlapping_risks() -> None:
    report = classify_dirty_worktree(
        [
            "M  staged.py",
            " M unstaged.py",
            "MM both.py",
            "?? new.py",
            "UU conflict.py",
            " D removed.py",
            "R  old.py -> renamed.py",
        ]
    )

    assert report["staged_count"] == 3
    assert report["unstaged_count"] == 3
    assert report["untracked_count"] == 1
    assert report["conflicted_count"] == 1
    assert report["deleted_count"] == 1
    assert report["renamed_count"] == 1
    conflict = next(row for row in report["files"] if row["path"] == "conflict.py")
    assert conflict["conflicted"] is True
    assert conflict["staged"] is False
    assert conflict["unstaged"] is False


def test_classify_dirty_worktree_ignores_malformed_rows() -> None:
    assert classify_dirty_worktree(["", "M"]) == {
        "staged_count": 0,
        "unstaged_count": 0,
        "untracked_count": 0,
        "conflicted_count": 0,
        "deleted_count": 0,
        "renamed_count": 0,
        "files": [],
    }

