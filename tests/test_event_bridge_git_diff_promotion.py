"""Regression: read-only ``git diff`` must not be promoted to a FileChangeItem.

The realtime bridge promotes a COMPLETED tool's unified diff into a
FileChangeItem so the UI can render the agent's own edits. But the
``git_diff`` tool is a *viewer*: its output is whatever sits in the working
tree — often another session's uncommitted work — not a change this turn
made. Promoting it makes the verification gate attribute those files to the
turn: a pure-read turn that ran ``git diff`` hard-failed with "code changes
were produced but no verification step was recorded".

Only a write tool's own diff (``apply_patch``'s ``diff_preview``, which
arrives as ``evt["diff"]``) represents a change the turn produced.
"""

from __future__ import annotations

from runtime.sensing.gateway.realtime_cerebrum import _file_change_item_from_tool_evt

_PLAIN_DIFF = "--- a/foo.py\n+++ b/foo.py\n@@ -1,1 +1,1 @@\n-old\n+new\n"
_DIRTY_TREE_DIFF = (
    "diff --git a/other.py b/other.py\n"
    "index 1111111..2222222 100644\n"
    "--- a/other.py\n"
    "+++ b/other.py\n"
    "@@ -1,1 +1,1 @@\n"
    "-old\n"
    "+new\n"
)


def test_git_diff_tool_is_not_promoted() -> None:
    evt = {"tool_name": "git_diff", "diff": _DIRTY_TREE_DIFF}
    assert _file_change_item_from_tool_evt(evt) is None


def test_git_show_tool_is_not_promoted() -> None:
    evt = {"tool_name": "git_show", "diff": _DIRTY_TREE_DIFF}
    assert _file_change_item_from_tool_evt(evt) is None


def test_git_diff_file_changes_list_is_not_promoted() -> None:
    evt = {
        "tool_name": "git_diff",
        "file_changes": [{"path": "other.py", "op": "update", "diff": _DIRTY_TREE_DIFF}],
    }
    assert _file_change_item_from_tool_evt(evt) is None


def test_write_tool_diff_still_promotes() -> None:
    # apply_patch's diff_preview arrives at the bridge as evt["diff"].
    evt = {"tool_name": "apply_patch", "diff": _PLAIN_DIFF}
    item = _file_change_item_from_tool_evt(evt)
    assert item is not None
    assert item.changes[0].path == "foo.py"


def test_legacy_evt_without_tool_name_still_promotes() -> None:
    # The pre-tool_name event shape (existing callers/tests) keeps working.
    item = _file_change_item_from_tool_evt({"diff": _PLAIN_DIFF})
    assert item is not None
    assert item.changes[0].path == "foo.py"

