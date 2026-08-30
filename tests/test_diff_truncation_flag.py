"""FileChange.diff_truncated wire flag.

The executor cuts unified diffs at its output limit and appends an
in-band marker (``... (truncated N bytes)``). These tests pin the
marker contract: ``diff_is_truncated`` recognises it, and the realtime
bridge sets ``FileChange.diff_truncated`` so clients can label the
diff and keep it out of revert paths.
"""

from runtime.protocol.items import diff_is_truncated
from runtime.sensing.gateway.realtime_cerebrum import _file_change_item_from_tool_evt

_PLAIN_DIFF = "--- a/foo.py\n+++ b/foo.py\n@@ -1,1 +1,1 @@\n-old\n+new\n"


class TestDiffIsTruncated:
    def test_plain_diff_is_not_truncated(self):
        assert diff_is_truncated(_PLAIN_DIFF) is False

    def test_marker_at_tail_is_truncated(self):
        assert diff_is_truncated(_PLAIN_DIFF + "\n... (truncated 4321 bytes)\n") is True

    def test_none_and_empty_are_not_truncated(self):
        assert diff_is_truncated(None) is False
        assert diff_is_truncated("") is False

    def test_marker_mid_diff_does_not_count(self):
        # Only a tail marker means the executor cut the output; the same
        # text inside a hunk body is user content.
        diff = (
            "--- a/foo.py\n+++ b/foo.py\n@@ -1,1 +1,2 @@\n keep\n+... (truncated 99 bytes)\n+tail\n"
        )
        assert diff_is_truncated(diff) is False


class TestFileChangeItemFlag:
    def test_raw_diff_with_marker_flags_last_change(self):
        evt = {"diff": _PLAIN_DIFF + "\n... (truncated 100 bytes)\n"}
        item = _file_change_item_from_tool_evt(evt)
        assert item is not None
        assert item.changes[-1].diff_truncated is True

    def test_raw_diff_without_marker_is_unflagged(self):
        item = _file_change_item_from_tool_evt({"diff": _PLAIN_DIFF})
        assert item is not None
        assert all(c.diff_truncated is False for c in item.changes)

    def test_pre_parsed_entry_flags_per_change(self):
        evt = {
            "file_changes": [
                {"path": "a.py", "op": "update", "diff": _PLAIN_DIFF},
                {
                    "path": "b.py",
                    "op": "update",
                    "diff": _PLAIN_DIFF + "\n... (truncated 7 bytes)\n",
                },
            ]
        }
        item = _file_change_item_from_tool_evt(evt)
        assert item is not None
        by_path = {c.path: c for c in item.changes}
        assert by_path["a.py"].diff_truncated is False
        assert by_path["b.py"].diff_truncated is True

    def test_flag_serialises_with_camel_case_alias(self):
        evt = {
            "file_changes": [
                {
                    "path": "b.py",
                    "op": "update",
                    "diff": _PLAIN_DIFF + "\n... (truncated 7 bytes)\n",
                }
            ]
        }
        item = _file_change_item_from_tool_evt(evt)
        assert item is not None
        payload = item.model_dump(by_alias=True, mode="json")
        assert payload["changes"][0]["diffTruncated"] is True
