"""Tests for the unified-diff → :class:`FileHunk` parser.

Covers single-file / multi-file diffs, the create / update / delete
ops, and the corner case where one ``@@`` block has no explicit
length suffix (``@@ -1 +1 @@`` is shorthand for ``@@ -1,1 +1,1 @@``).
"""

from __future__ import annotations

from runtime.protocol.diff_parser import parse_unified_diff


class TestSingleFileUpdate:
    def test_basic_hunk(self) -> None:
        diff = "--- a/foo.py\n+++ b/foo.py\n@@ -1,3 +1,3 @@\n line1\n-old\n+new\n line3\n"
        changes = parse_unified_diff(diff)
        assert len(changes) == 1
        ch = changes[0]
        assert ch.path == "foo.py"
        assert ch.op == "update"
        assert len(ch.hunks) == 1
        h = ch.hunks[0]
        assert (h.old_start, h.old_lines) == (1, 3)
        assert (h.new_start, h.new_lines) == (1, 3)
        assert "-old\n+new\n" in h.body
        assert h.decision == "pending"

    def test_short_hunk_header(self) -> None:
        diff = "--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-old\n+new\n"
        ch = parse_unified_diff(diff)[0]
        h = ch.hunks[0]
        assert (h.old_lines, h.new_lines) == (1, 1)


class TestCreateAndDelete:
    def test_create_file(self) -> None:
        diff = "--- /dev/null\n+++ b/new.txt\n@@ -0,0 +1,2 @@\n+hello\n+world\n"
        ch = parse_unified_diff(diff)[0]
        assert ch.path == "new.txt"
        assert ch.op == "create"
        assert ch.hunks[0].old_start == 0

    def test_delete_file(self) -> None:
        diff = "--- a/old.txt\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-hello\n-world\n"
        ch = parse_unified_diff(diff)[0]
        assert ch.path == "old.txt"
        assert ch.op == "delete"


class TestMultipleHunksAndFiles:
    def test_two_hunks_in_one_file(self) -> None:
        diff = "--- a/a.py\n+++ b/a.py\n@@ -1,1 +1,1 @@\n-A\n+a\n@@ -10,1 +10,1 @@\n-B\n+b\n"
        ch = parse_unified_diff(diff)[0]
        assert len(ch.hunks) == 2
        assert ch.hunks[0].old_start == 1
        assert ch.hunks[1].old_start == 10

    def test_two_files(self) -> None:
        diff = (
            "--- a/a.py\n"
            "+++ b/a.py\n"
            "@@ -1,1 +1,1 @@\n"
            "-A\n"
            "+a\n"
            "--- a/b.py\n"
            "+++ b/b.py\n"
            "@@ -3,1 +3,1 @@\n"
            "-X\n"
            "+x\n"
        )
        changes = parse_unified_diff(diff)
        paths = [c.path for c in changes]
        assert paths == ["a.py", "b.py"]


class TestEdgeCases:
    def test_empty_diff(self) -> None:
        assert parse_unified_diff("") == []
        assert parse_unified_diff("   \n") == []

    def test_unique_hunk_ids(self) -> None:
        diff = "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-A\n+a\n@@ -2 +2 @@\n-B\n+b\n"
        ids = [h.id for h in parse_unified_diff(diff)[0].hunks]
        assert len(set(ids)) == len(ids)

    def test_round_trip_diff_field_set(self) -> None:
        diff = "--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-old\n+new\n"
        ch = parse_unified_diff(diff)[0]
        assert ch.diff is not None
        assert "@@ -1,1 +1,1 @@" in ch.diff
        assert "-old" in ch.diff
        assert "+new" in ch.diff
