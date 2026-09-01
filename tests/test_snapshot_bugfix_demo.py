"""Implementation note."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from demos.bugfix_demo import run_demo

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="git not on PATH",
)


# The first end-to-end snapshot: the deterministic bugfix demo's journal
# transcript. Re-record deliberately (`pytest --snapshot-update`) after
# reviewing a real behavior change — never to silence a drift.
_VOLATILE_KEYS = {
    "event_id",
    "ts",
    "call_id",
    "task_id",
    "arm_id",
    "conversation_id",
}


class TestBugfixDemoSnapshot:
    def test_journal_transcript_matches_snapshot(self, tmp_path: Path, snapshot) -> None:
        result = run_demo(workdir=tmp_path, color=False, verbose=False)
        assert result["success"], f"demo failed · {result}"
        journal_path = tmp_path / "events.jsonl"
        events = [
            json.loads(line)
            for line in journal_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        snapshot.match(
            "journal",
            events,
            rebase_map={str(tmp_path): "{workdir}", sys.executable: "{python}"},
            scrub_keys=_VOLATILE_KEYS,
        )

    def test_step_sequence_matches_snapshot(self, tmp_path: Path, snapshot) -> None:
        result = run_demo(workdir=tmp_path, color=False, verbose=False)
        steps = [
            {
                "node": step.node_id,
                "skill": str(step.action.sucker_id),
                "status": step.result.status,
                "exit_code": step.result.exit_code,
                "error_type": step.result.error_type,
            }
            for step in result["steps"]
        ]
        snapshot.match("steps", steps, rebase_map={str(tmp_path): "{workdir}"})

