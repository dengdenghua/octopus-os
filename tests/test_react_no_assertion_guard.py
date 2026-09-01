from __future__ import annotations

import json

from runtime.core.cerebrum.react_guards import _trajectory_no_assertion_test_hits
from runtime.core.cerebrum.react_types import ReActStep


def test_later_assertion_repair_clears_earlier_test_guard_hit() -> None:
    path = "tests/test_cache.py"
    bad = "def test_cache():\n    value = cache.get()\n    consume(value)\n"
    repaired = "def test_cache():\n    value = cache.get()\n    assert value == 1\n"
    steps = [
        ReActStep(
            iteration=1,
            action=(
                f'write_text_file({{"path": {json.dumps(path)}, "content": {json.dumps(bad)}}})'
            ),
        ),
        ReActStep(
            iteration=2,
            action=(
                'write_text_file({"path": '
                f'{json.dumps(path)}, "content": {json.dumps(repaired)}}})'
            ),
        ),
    ]

    assert _trajectory_no_assertion_test_hits(steps) == {}

